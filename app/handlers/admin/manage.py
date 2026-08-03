"""Админка: список турниров, карточка, состав, статусы, отмена турнира."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.models import Tournament, TournamentStatus, User
from app.filters import IsAdmin
from app.keyboards.admin import (
    admin_participants_kb,
    admin_tournament_kb,
    admin_tournaments_kb,
    back_to_admin_kb,
    confirm_cancel_tournament_kb,
)
from app.keyboards.callbacks import AdminCB
from app.services import announce
from app.services import tournaments as tournaments_service
from app.utils.dates import fmt_date, fmt_time
from app.utils.formatting import q, render_for_admin, user_line
from app.utils.tg import edit_or_send, paginate

logger = logging.getLogger(__name__)

router = Router(name="admin_manage")
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 8


async def show_tournaments(
    callback: CallbackQuery, session: AsyncSession, *, page: int = 0
) -> None:
    items = await tournaments_service.list_for_admin(session)
    if not items:
        await edit_or_send(callback, texts.ADMIN_TOURNAMENTS_EMPTY, back_to_admin_kb())
        return

    chunk, page, total_pages = paginate(items, page, PAGE_SIZE)
    counters = await tournaments_service.counters(session, [t.id for t in chunk])
    await edit_or_send(
        callback,
        "📋 <b>Турниры</b>\n\n🟢 набор • 🔴 закрыт • 📝 черновик • 🚫 отменён",
        admin_tournaments_kb(
            chunk, page=page, total_pages=total_pages, counters=counters
        ),
    )


async def show_tournament(
    callback: CallbackQuery,
    session: AsyncSession,
    tournament: Tournament,
    *,
    page: int,
) -> None:
    taken = await tournaments_service.count_active(session, tournament.id)
    paid = await tournaments_service.count_paid(session, tournament.id)
    await edit_or_send(
        callback,
        render_for_admin(tournament, taken=taken, paid=paid),
        admin_tournament_kb(tournament, page=page),
    )


async def _load(
    callback: CallbackQuery, session: AsyncSession, tournament_id: int
) -> Tournament | None:
    tournament = await tournaments_service.get(session, tournament_id)
    if tournament is None:
        await callback.answer(texts.TOURNAMENT_NOT_FOUND, show_alert=True)
    return tournament


@router.callback_query(AdminCB.filter(F.action == "tours"))
async def on_tours(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    await callback.answer()
    await show_tournaments(callback, session, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "tour"))
async def on_tour(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    tournament = await _load(callback, session, callback_data.id)
    if tournament is None:
        return
    await callback.answer()
    await show_tournament(callback, session, tournament, page=callback_data.page)


async def show_players(
    callback: CallbackQuery,
    session: AsyncSession,
    tournament: Tournament,
    *,
    page: int,
) -> None:
    """Состав турнира. Callback здесь уже отвечен вызывающим."""
    rows = await tournaments_service.participants(session, tournament.id)
    if not rows:
        await edit_or_send(
            callback,
            texts.ADMIN_PARTICIPANTS_EMPTY,
            admin_tournament_kb(tournament, page=page),
        )
        return

    lines = "\n".join(
        user_line(user, index=index, paid=registration.is_paid)
        for index, (user, registration) in enumerate(rows, start=1)
    )
    await edit_or_send(
        callback,
        texts.ADMIN_PARTICIPANTS.format(title=q(tournament.title), lines=lines),
        admin_participants_kb(
            tournament,
            [(user, registration.is_paid) for user, registration in rows],
            page=page,
        ),
    )


@router.callback_query(AdminCB.filter(F.action == "players"))
async def on_players(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    tournament = await _load(callback, session, callback_data.id)
    if tournament is None:
        return
    await callback.answer()
    await show_players(callback, session, tournament, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "paid"))
async def on_toggle_paid(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    tournament = await _load(callback, session, callback_data.id)
    if tournament is None:
        return

    result = await tournaments_service.toggle_paid(
        session, tournament.id, int(callback_data.value)
    )
    if result is None:
        await callback.answer("Записи уже нет", show_alert=True)
    else:
        await callback.answer("Отмечено как оплачено" if result else "Отметка снята")
    await show_players(callback, session, tournament, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "kick"))
async def on_kick(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    """Снять игрока с турнира — например, если он не пришёл или не оплатил."""
    tournament = await _load(callback, session, callback_data.id)
    if tournament is None:
        return

    player_id = int(callback_data.value)
    registration = await tournaments_service.cancel_signup(
        session, tournament.id, player_id
    )
    if registration is None:
        await callback.answer("Записи уже нет", show_alert=True)
    else:
        await callback.answer("Игрок снят с турнира")
        if callback.bot is not None:
            await announce.notify_user(
                callback.bot,
                player_id,
                texts.NOTIFY_PLAYER_REMOVED.format(
                    title=q(tournament.title), date=fmt_date(tournament.date)
                ),
            )
        logger.info("Админ %s снял игрока %s с турнира %s",
                    callback.from_user.id, player_id, tournament.id)
    await show_players(callback, session, tournament, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "status"))
async def on_status(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    tournament = await _load(callback, session, callback_data.id)
    if tournament is None:
        return

    status = (
        TournamentStatus.OPEN
        if callback_data.value == "open"
        else TournamentStatus.CLOSED
    )
    await tournaments_service.set_status(session, tournament, status)
    await callback.answer(
        "Набор открыт" if status is TournamentStatus.OPEN else "Набор закрыт"
    )

    # В чате висит анонс со старым статусом — перерисовываем.
    if callback.bot is not None:
        await announce.refresh(callback.bot, tournament)

    await show_tournament(callback, session, tournament, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "publish_existing"))
async def on_publish_existing(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    """Публикация черновика: открываем набор и шлём анонс."""
    tournament = await _load(callback, session, callback_data.id)
    if tournament is None:
        return

    await tournaments_service.set_status(session, tournament, TournamentStatus.OPEN)

    if tournament.announce_message_id is not None:
        await callback.answer(texts.ANNOUNCE_ALREADY)
    elif not tournament.is_public:
        await callback.answer(texts.ANNOUNCE_HIDDEN, show_alert=True)
    elif callback.bot is not None:
        try:
            published = await announce.publish(callback.bot, session, tournament)
        except announce.AnnounceError as error:
            await callback.answer(
                texts.ANNOUNCE_FAILED.format(error=error), show_alert=True
            )
        else:
            await callback.answer(
                texts.ANNOUNCE_OK if published else texts.ANNOUNCE_NOT_CONFIGURED,
                show_alert=not published,
            )

    await show_tournament(callback, session, tournament, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "cancel"))
async def on_cancel_ask(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    tournament = await _load(callback, session, callback_data.id)
    if tournament is None:
        return
    await callback.answer()

    taken = await tournaments_service.count_active(session, tournament.id)
    await edit_or_send(
        callback,
        texts.CONFIRM_CANCEL_TOURNAMENT.format(title=q(tournament.title), taken=taken),
        confirm_cancel_tournament_kb(tournament, page=callback_data.page),
    )


@router.callback_query(AdminCB.filter(F.action == "cancel_ok"))
async def on_cancel_confirm(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession, user: User
) -> None:
    tournament = await _load(callback, session, callback_data.id)
    if tournament is None:
        return

    user_ids = await tournaments_service.cancel_tournament(session, tournament)

    sent = 0
    if callback.bot is not None:
        if user_ids:
            sent = await announce.notify_users(
                callback.bot,
                user_ids,
                texts.NOTIFY_PLAYER_TOURNAMENT_CANCELLED.format(
                    title=q(tournament.title),
                    date=fmt_date(tournament.date),
                    time_start=fmt_time(tournament.time_start),
                ),
            )
        await announce.refresh(callback.bot, tournament)

    logger.info("Админ %s отменил турнир %s, уведомлено %s игроков",
                user.id, tournament.id, sent)
    await callback.answer(texts.TOURNAMENT_CANCELLED_OK.format(sent=sent), show_alert=True)
    await show_tournament(callback, session, tournament, page=callback_data.page)
