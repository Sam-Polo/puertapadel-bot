"""Пользовательская часть: список мероприятий, карточка, запись и отмена.

Как проходит запись, решает формат мероприятия, а не участник:
одиночное — сразу экран подтверждения, парное — сначала имя напарника,
и места занимаются парой.
"""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.models import Event, EventStatus, Registration, User
from app.keyboards.callbacks import EventCB, MenuCB, PageCB
from app.keyboards.common import (
    PAGE_SIZE,
    after_signup_kb,
    back_to_menu_kb,
    cancel_confirm_kb,
    event_card_kb,
    events_list_kb,
    signup_confirm_kb,
    start_registration_kb,
)
from app.services import announce
from app.services import events as events_service
from app.services.events import SignupResult
from app.states import SignupSG
from app.utils.formatting import (
    fmt_capacity,
    fmt_price,
    fmt_when,
    q,
    render_for_player,
)
from app.utils.tg import edit_or_send, paginate

logger = logging.getLogger(__name__)

router = Router(name="events")

# Имя и фамилия напарника: два и более слова из букв.
PARTNER_NAME_RE = re.compile(
    r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-']{1,29}"
    r"(?:\s+[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-']{1,29})+$"
)

_SIGNUP_ERRORS = {
    SignupResult.ALREADY: texts.SIGNUP_ALREADY,
    SignupResult.CLOSED: texts.SIGNUP_CLOSED,
    SignupResult.CANCELLED: texts.SIGNUP_CANCELLED_EVENT,
    SignupResult.PASSED: texts.SIGNUP_PASSED,
}


def share_text(event: Event) -> str:
    return texts.SHARE_TEXT.format(title=event.title, when=fmt_when(event))


async def show_events_list(
    callback: CallbackQuery, session: AsyncSession, *, page: int = 0
) -> None:
    items = await events_service.list_open_for_players(session)
    if not items:
        await edit_or_send(callback, texts.EVENTS_EMPTY, back_to_menu_kb())
        return

    chunk, page, total_pages = paginate(items, page, PAGE_SIZE)
    counters = await events_service.counters(session, [e.id for e in chunk])
    await edit_or_send(
        callback,
        texts.EVENTS_LIST,
        events_list_kb(
            chunk,
            page=page,
            total_pages=total_pages,
            scope="events",
            src="list",
            counters=counters,
        ),
    )


async def show_my_events(
    callback: CallbackQuery, session: AsyncSession, user: User, *, page: int = 0
) -> None:
    items = await events_service.list_for_user(session, user.id)
    if not items:
        await edit_or_send(callback, texts.MY_EVENTS_EMPTY, back_to_menu_kb())
        return

    chunk, page, total_pages = paginate(items, page, PAGE_SIZE)
    counters = await events_service.counters(session, [e.id for e in chunk])
    await edit_or_send(
        callback,
        texts.MY_EVENTS,
        events_list_kb(
            chunk,
            page=page,
            total_pages=total_pages,
            scope="my",
            src="my",
            counters=counters,
        ),
    )


def _is_visible_to_player(event: Event, *, from_link: bool) -> bool:
    """Скрытое мероприятие открывается только по прямой ссылке."""
    if event.is_public:
        return True
    return from_link


def _is_full(event: Event, taken: int) -> bool:
    return event.max_players is not None and taken >= event.max_players


async def _card_payload(
    session: AsyncSession, event: Event, user: User
) -> tuple[str, int, Registration | None]:
    taken = await events_service.seats_taken(session, event.id)
    registration = await events_service.get_registration(session, event.id, user.id)
    roster = (
        await events_service.participants(session, event.id)
        if event.show_roster
        else None
    )
    text = render_for_player(
        event, taken=taken, my_registration=registration, roster=roster
    )
    return text, taken, registration


async def show_event_card(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    event_id: int,
    *,
    page: int = 0,
    src: str = "list",
) -> None:
    event = await events_service.get(session, event_id)
    if event is None:
        await edit_or_send(callback, texts.EVENT_NOT_FOUND, back_to_menu_kb())
        return
    if not _is_visible_to_player(event, from_link=src == "link"):
        await edit_or_send(callback, texts.EVENT_HIDDEN, back_to_menu_kb())
        return

    text, taken, registration = await _card_payload(session, event, user)
    await edit_or_send(
        callback,
        text,
        event_card_kb(
            event,
            my_registration=registration,
            page=page,
            src=src,
            is_full=_is_full(event, taken),
            share_text=share_text(event),
        ),
    )


async def send_event_card(
    message: Message,
    session: AsyncSession,
    user: User,
    event_id: int,
    *,
    src: str = "link",
) -> None:
    """Отдельным сообщением — для перехода по deep-link'у."""
    event = await events_service.get(session, event_id)
    if event is None:
        await message.answer(texts.EVENT_NOT_FOUND, reply_markup=back_to_menu_kb())
        return
    if not _is_visible_to_player(event, from_link=src == "link"):
        await message.answer(texts.EVENT_HIDDEN, reply_markup=back_to_menu_kb())
        return

    text, taken, registration = await _card_payload(session, event, user)
    await message.answer(
        text,
        reply_markup=event_card_kb(
            event,
            my_registration=registration,
            page=0,
            src=src,
            is_full=_is_full(event, taken),
            share_text=share_text(event),
        ),
        disable_web_page_preview=True,
    )


@router.callback_query(PageCB.filter(F.scope == "events"))
async def paginate_events(
    callback: CallbackQuery, callback_data: PageCB, session: AsyncSession
) -> None:
    await callback.answer()
    await show_events_list(callback, session, page=callback_data.page)


@router.callback_query(PageCB.filter(F.scope == "my"))
async def paginate_my(
    callback: CallbackQuery, callback_data: PageCB, session: AsyncSession, user: User
) -> None:
    await callback.answer()
    await show_my_events(callback, session, user, page=callback_data.page)


@router.callback_query(EventCB.filter(F.action == "view"))
async def view_event(
    callback: CallbackQuery,
    callback_data: EventCB,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.clear()
    await show_event_card(
        callback,
        session,
        user,
        callback_data.id,
        page=callback_data.page,
        src=callback_data.src,
    )


def _render_confirm(event: Event, *, partner_name: str | None) -> str:
    participants = (
        texts.PARTICIPANTS_PAIR.format(partner=q(partner_name))
        if event.is_doubles and partner_name
        else texts.PARTICIPANTS_SINGLE
    )
    return texts.SIGNUP_CONFIRM.format(
        title=q(event.title),
        when=fmt_when(event),
        participants=participants,
        price=fmt_price(event.price),
    )


@router.callback_query(EventCB.filter(F.action == "signup"))
async def start_signup(
    callback: CallbackQuery,
    callback_data: EventCB,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Одиночное — сразу подтверждение, парное — сначала имя напарника."""
    event = await events_service.get(session, callback_data.id)
    if event is None:
        await callback.answer(texts.EVENT_NOT_FOUND, show_alert=True)
        return

    # Сюда попадает только админ: остальных до записи не пускает гейт.
    if not user.is_registered:
        await callback.answer()
        await edit_or_send(callback, texts.NEED_REGISTRATION, start_registration_kb())
        return

    if not event.accepts_signups:
        await callback.answer(texts.SIGNUP_CLOSED, show_alert=True)
        return

    taken = await events_service.seats_taken(session, event.id)
    if _is_full(event, taken):
        await callback.answer(texts.SIGNUP_NO_SLOTS, show_alert=True)
        await show_event_card(
            callback, session, user, event.id, page=callback_data.page, src=callback_data.src
        )
        return

    if event.max_players is not None:
        free = event.max_players - taken
        if free < event.seats_per_signup:
            await callback.answer(texts.SIGNUP_NO_SLOTS_FOR_PAIR, show_alert=True)
            await show_event_card(
                callback, session, user, event.id,
                page=callback_data.page, src=callback_data.src,
            )
            return

    await callback.answer()

    if not event.is_doubles:
        await state.clear()
        await edit_or_send(
            callback,
            _render_confirm(event, partner_name=None),
            signup_confirm_kb(event, page=callback_data.page, src=callback_data.src),
        )
        return

    await state.set_state(SignupSG.partner_name)
    await state.update_data(
        event_id=event.id, page=callback_data.page, src=callback_data.src
    )
    await edit_or_send(callback, texts.ASK_PARTNER_NAME)


@router.message(SignupSG.partner_name, F.text)
async def on_partner_name(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = " ".join((message.text or "").split())
    if not PARTNER_NAME_RE.match(raw):
        await message.answer(texts.BAD_PARTNER_NAME)
        return

    data = await state.get_data()
    event = await events_service.get(session, int(data["event_id"]))
    if event is None:
        await state.clear()
        await message.answer(texts.EVENT_NOT_FOUND, reply_markup=back_to_menu_kb())
        return

    partner_name = raw.title()
    await state.update_data(partner_name=partner_name)
    await message.answer(
        _render_confirm(event, partner_name=partner_name),
        reply_markup=signup_confirm_kb(
            event, page=int(data["page"]), src=str(data["src"])
        ),
    )


@router.message(SignupSG.partner_name)
async def on_partner_wrong_content(message: Message) -> None:
    await message.answer(texts.BAD_PARTNER_NAME)


@router.callback_query(EventCB.filter(F.action == "signup_ok"))
async def do_signup(
    callback: CallbackQuery,
    callback_data: EventCB,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    event = await events_service.get(session, callback_data.id)
    if event is None:
        await callback.answer(texts.EVENT_NOT_FOUND, show_alert=True)
        return

    data = await state.get_data()
    partner_name = data.get("partner_name") if event.is_doubles else None

    if event.is_doubles and not partner_name:
        # Состояние потерялось (например, бот перезапустился) — спросим заново.
        await callback.answer()
        await state.set_state(SignupSG.partner_name)
        await state.update_data(
            event_id=event.id, page=callback_data.page, src=callback_data.src
        )
        await edit_or_send(callback, texts.ASK_PARTNER_NAME)
        return

    result, taken = await events_service.signup(
        session, event, user, partner_name=partner_name
    )
    await state.clear()

    if result is SignupResult.NO_SLOTS:
        await callback.answer(texts.SIGNUP_NO_SLOTS, show_alert=True)
        await show_event_card(
            callback, session, user, event.id, page=callback_data.page, src=callback_data.src
        )
        return

    if result is SignupResult.NO_SLOTS_FOR_PAIR:
        await callback.answer(texts.SIGNUP_NO_SLOTS_FOR_PAIR, show_alert=True)
        await show_event_card(
            callback, session, user, event.id, page=callback_data.page, src=callback_data.src
        )
        return

    if result is not SignupResult.OK:
        await callback.answer(_SIGNUP_ERRORS[result], show_alert=True)
        await show_event_card(
            callback, session, user, event.id, page=callback_data.page, src=callback_data.src
        )
        return

    await callback.answer("Готово!")
    template = texts.SIGNUP_OK_DOUBLE if event.is_doubles else texts.SIGNUP_OK
    await edit_or_send(
        callback,
        template.format(
            title=q(event.title), when=fmt_when(event), partner=q(partner_name)
        )
        + texts.SIGNUP_SHARE_HINT,
        after_signup_kb(event, share_text(event)),
    )
    logger.info(
        "Участник %s записан на мероприятие %s (мест: %s, занято %s)",
        user.id, event.id, event.seats_per_signup, taken,
    )


@router.callback_query(EventCB.filter(F.action == "cancel"))
async def ask_cancel_confirm(
    callback: CallbackQuery, callback_data: EventCB, session: AsyncSession
) -> None:
    event = await events_service.get(session, callback_data.id)
    if event is None:
        await callback.answer(texts.EVENT_NOT_FOUND, show_alert=True)
        return

    await callback.answer()
    await edit_or_send(
        callback,
        texts.CANCEL_CONFIRM.format(
            title=q(event.title),
            when=fmt_when(event),
            freed=texts.FREED_PAIR if event.is_doubles else texts.FREED_ONE,
        ),
        cancel_confirm_kb(event, page=callback_data.page, src=callback_data.src),
    )


@router.callback_query(EventCB.filter(F.action == "cancel_ok"))
async def do_cancel(
    callback: CallbackQuery, callback_data: EventCB, session: AsyncSession, user: User
) -> None:
    event = await events_service.get(session, callback_data.id)
    if event is None:
        await callback.answer(texts.EVENT_NOT_FOUND, show_alert=True)
        return

    registration = await events_service.cancel_signup(session, event.id, user.id)
    if registration is None:
        await callback.answer(texts.CANCEL_NOT_FOUND, show_alert=True)
        await show_event_card(
            callback, session, user, event.id, page=callback_data.page, src=callback_data.src
        )
        return

    await callback.answer("Запись отменена")
    taken = await events_service.seats_taken(session, event.id)

    # Админу это важно знать: места освободились, возможно кого-то надо позвать.
    if callback.bot is not None and event.status is not EventStatus.CANCELLED:
        await announce.notify_admins(
            callback.bot,
            texts.NOTIFY_ADMIN_CANCEL.format(
                user=q(user.full_name),
                title=q(event.title),
                when=fmt_when(event),
                seats=registration.seats,
                taken=fmt_capacity(event, taken),
            ),
        )

    await edit_or_send(
        callback, texts.CANCEL_OK.format(title=q(event.title)), back_to_menu_kb()
    )
    logger.info("Участник %s снялся с мероприятия %s", user.id, event.id)


@router.callback_query(MenuCB.filter(F.action == "events"))
async def open_events(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await callback.answer()
    await state.clear()
    await show_events_list(callback, session)


@router.callback_query(MenuCB.filter(F.action == "my"))
async def open_my(
    callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    await callback.answer()
    await state.clear()
    await show_my_events(callback, session, user)
