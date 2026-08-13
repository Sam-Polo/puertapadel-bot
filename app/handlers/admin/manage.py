"""Админка: список мероприятий, карточка, состав, статусы, отмена."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.models import Event, EventStatus, User
from app.filters import IsAdmin
from app.keyboards.admin import (
    admin_event_kb,
    admin_events_kb,
    admin_participants_kb,
    back_to_admin_kb,
    confirm_cancel_event_kb,
)
from app.keyboards.callbacks import AdminCB
from app.services import announce
from app.services import events as events_service
from app.utils.formatting import fmt_capacity, fmt_when, q, render_for_admin, user_line
from app.utils.tg import edit_or_send, paginate

logger = logging.getLogger(__name__)

router = Router(name="admin_manage")
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 8


async def show_events(
    callback: CallbackQuery, session: AsyncSession, *, page: int = 0
) -> None:
    items = await events_service.list_for_admin(session)
    if not items:
        await edit_or_send(callback, texts.ADMIN_EVENTS_EMPTY, back_to_admin_kb())
        return

    chunk, page, total_pages = paginate(items, page, PAGE_SIZE)
    counters = await events_service.counters(session, [e.id for e in chunk])
    await edit_or_send(
        callback,
        "📋 <b>Мероприятия</b>\n\n🟢 набор • 🔴 закрыт • 📝 черновик • 🚫 отменено",
        admin_events_kb(chunk, page=page, total_pages=total_pages, counters=counters),
    )


async def show_event(
    callback: CallbackQuery, session: AsyncSession, event: Event, *, page: int
) -> None:
    taken = await events_service.seats_taken(session, event.id)
    paid = await events_service.count_paid(session, event.id)
    await edit_or_send(
        callback,
        render_for_admin(event, taken=taken, paid=paid),
        admin_event_kb(event, page=page),
    )


async def _load(
    callback: CallbackQuery, session: AsyncSession, event_id: int
) -> Event | None:
    event = await events_service.get(session, event_id)
    if event is None:
        await callback.answer(texts.EVENT_NOT_FOUND, show_alert=True)
    return event


@router.callback_query(AdminCB.filter(F.action == "tours"))
async def on_events(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    await callback.answer()
    await show_events(callback, session, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "tour"))
async def on_event(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    event = await _load(callback, session, callback_data.id)
    if event is None:
        return
    await callback.answer()
    await show_event(callback, session, event, page=callback_data.page)


async def show_players(
    callback: CallbackQuery, session: AsyncSession, event: Event, *, page: int
) -> None:
    """Состав мероприятия. Callback здесь уже отвечен вызывающим."""
    rows = await events_service.participants(session, event.id)
    if not rows:
        await edit_or_send(
            callback,
            texts.ADMIN_PARTICIPANTS_EMPTY,
            admin_event_kb(event, page=page),
        )
        return

    # Отметки об оплате имеют смысл, только если за участие берут деньги.
    tracks_payment = bool(event.price)

    # В парном мероприятии единица списка — пара, поэтому нумеруем пары,
    # а не людей, и оба имени идут одной строкой.
    lines = [
        user_line(
            user,
            index=index,
            paid=registration.is_paid if tracks_payment else None,
            partner_name=registration.partner_name if event.is_doubles else None,
        )
        for index, (user, registration) in enumerate(rows, start=1)
    ]

    taken = await events_service.seats_taken(session, event.id)
    await edit_or_send(
        callback,
        texts.ADMIN_PARTICIPANTS.format(
            title=q(event.title),
            lines="\n".join(lines),
            taken=fmt_capacity(event, taken),
            hint=texts.ADMIN_PARTICIPANTS_PAID_HINT if tracks_payment else "",
        ),
        admin_participants_kb(
            event,
            [(user, registration.is_paid) for user, registration in rows],
            page=page,
            show_payment=tracks_payment,
        ),
    )


@router.callback_query(AdminCB.filter(F.action == "players"))
async def on_players(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    event = await _load(callback, session, callback_data.id)
    if event is None:
        return
    await callback.answer()
    await show_players(callback, session, event, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "paid"))
async def on_toggle_paid(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    event = await _load(callback, session, callback_data.id)
    if event is None:
        return

    result = await events_service.toggle_paid(
        session, event.id, int(callback_data.value)
    )
    if result is None:
        await callback.answer("Записи уже нет", show_alert=True)
    else:
        await callback.answer("Отмечено как оплачено" if result else "Отметка снята")
    await show_players(callback, session, event, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "kick"))
async def on_kick(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    """Снять участника — например, если он не пришёл или не оплатил."""
    event = await _load(callback, session, callback_data.id)
    if event is None:
        return

    player_id = int(callback_data.value)
    registration = await events_service.cancel_signup(session, event.id, player_id)
    if registration is None:
        await callback.answer("Записи уже нет", show_alert=True)
    else:
        await callback.answer("Участник снят")
        if callback.bot is not None:
            await announce.notify_user(
                callback.bot,
                player_id,
                texts.NOTIFY_PLAYER_REMOVED.format(
                    title=q(event.title), when=fmt_when(event)
                ),
            )
        logger.info(
            "Админ %s снял участника %s с мероприятия %s",
            callback.from_user.id, player_id, event.id,
        )
        if callback.bot is not None:
            announce.schedule_refresh(callback.bot, event.id)
    await show_players(callback, session, event, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "status"))
async def on_status(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    event = await _load(callback, session, callback_data.id)
    if event is None:
        return

    status = EventStatus.OPEN if callback_data.value == "open" else EventStatus.CLOSED
    await events_service.set_status(session, event, status)
    await callback.answer(
        "Набор открыт" if status is EventStatus.OPEN else "Набор закрыт"
    )

    # В чате висит анонс со старым статусом — перерисовываем.
    if callback.bot is not None:
        await announce.refresh(callback.bot, session, event)

    await show_event(callback, session, event, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "publish_existing"))
async def on_publish_existing(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    """Публикация черновика: открываем набор и шлём анонс."""
    event = await _load(callback, session, callback_data.id)
    if event is None:
        return

    await events_service.set_status(session, event, EventStatus.OPEN)

    if event.announce_message_id is not None:
        await callback.answer(texts.ANNOUNCE_ALREADY)
    elif not event.is_public:
        await callback.answer(texts.ANNOUNCE_HIDDEN, show_alert=True)
    elif callback.bot is not None:
        try:
            published = await announce.publish(callback.bot, session, event)
        except announce.AnnounceError as error:
            await callback.answer(
                texts.ANNOUNCE_FAILED.format(error=error), show_alert=True
            )
        else:
            await callback.answer(
                texts.ANNOUNCE_OK if published else texts.ANNOUNCE_NOT_CONFIGURED,
                show_alert=not published,
            )

    await show_event(callback, session, event, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "cancel"))
async def on_cancel_ask(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    event = await _load(callback, session, callback_data.id)
    if event is None:
        return
    await callback.answer()

    taken = await events_service.seats_taken(session, event.id)
    await edit_or_send(
        callback,
        texts.CONFIRM_CANCEL_EVENT.format(title=q(event.title), taken=taken),
        confirm_cancel_event_kb(event, page=callback_data.page),
    )


@router.callback_query(AdminCB.filter(F.action == "cancel_ok"))
async def on_cancel_confirm(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession, user: User
) -> None:
    event = await _load(callback, session, callback_data.id)
    if event is None:
        return

    user_ids = await events_service.cancel_event(session, event)

    sent = 0
    if callback.bot is not None:
        if user_ids:
            sent = await announce.notify_users(
                callback.bot,
                user_ids,
                texts.NOTIFY_PLAYER_EVENT_CANCELLED.format(
                    title=q(event.title), when=fmt_when(event)
                ),
            )
        await announce.refresh(callback.bot, session, event)

    logger.info(
        "Админ %s отменил мероприятие %s, уведомлено %s участников",
        user.id, event.id, sent,
    )
    await callback.answer(texts.EVENT_CANCELLED_OK.format(sent=sent), show_alert=True)
    await show_event(callback, session, event, page=callback_data.page)
