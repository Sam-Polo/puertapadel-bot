"""Правка уже созданного мероприятия.

Одно поле за раз: админ выбирает поле в меню, вводит значение текстом или
кнопкой, и сразу возвращается в карточку. Каждое сохранение ставит анонс
в очередь на обновление — в чате он не отстаёт.

Формат — единственное поле, которое запирается: менять его на записанном
составе значит оставить людей без пары.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.models import Event, EventFormat
from app.filters import IsAdmin
from app.keyboards.admin import (
    MAX_PAIRS_PRESETS,
    MAX_PLAYERS_PRESETS,
    edit_menu_kb,
    edit_value_kb,
)
from app.keyboards.callbacks import AdminCB
from app.services import announce
from app.services import events as events_service
from app.states import EditEventSG
from app.utils.dates import now, parse_date, parse_time
from app.utils.formatting import fmt_when, q
from app.utils.tg import edit_or_send

logger = logging.getLogger(__name__)

router = Router(name="admin_edit")
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin())

# Поля, которые можно очистить: они необязательны у мероприятия.
CLEARABLE = {"time_end", "max_players", "rating_text", "is_rated", "price", "description"}

# Поля, которые задаются только кнопками.
CHOICE_FIELDS = {"format", "is_rated", "is_public", "show_roster"}


def _options(field: str, event: Event) -> list[tuple[str, str]]:
    if field == "format":
        return [("👤 Одиночное", "singles"), ("👥 Парное", "doubles")]
    if field == "is_rated":
        return [("✅ Рейтинговое", "1"), ("❌ Не рейтинговое", "0")]
    if field == "is_public":
        return [("✅ Видно всем", "1"), ("🙈 Скрытое", "0")]
    if field == "show_roster":
        return [("👁 Показывать", "1"), ("🙈 Скрыть", "0")]
    if field == "max_players":
        presets = MAX_PAIRS_PRESETS if event.is_doubles else MAX_PLAYERS_PRESETS
        suffix = " пар" if event.is_doubles else ""
        return [(f"{value}{suffix}", str(value)) for value in presets]
    if field == "price":
        return [("🆓 Бесплатно", "0")]
    return []


async def _show_menu(
    callback: CallbackQuery, event: Event, *, page: int
) -> None:
    await edit_or_send(
        callback,
        texts.EDIT_MENU.format(title=q(event.title), when=fmt_when(event)),
        edit_menu_kb(event, page=page),
    )


async def _send_menu(message: Message, event: Event, *, page: int, note: str) -> None:
    """После текстового ввода меню приходит новым сообщением — правка
    прошлого тут не годится, его уже «закрыл» ответ пользователя."""
    await message.answer(
        f"{note}\n\n"
        + texts.EDIT_MENU.format(title=q(event.title), when=fmt_when(event)),
        reply_markup=edit_menu_kb(event, page=page),
    )


@router.callback_query(AdminCB.filter(F.action == "edit"))
async def on_edit_menu(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    event = await events_service.get(session, callback_data.id)
    if event is None:
        await callback.answer(texts.EVENT_NOT_FOUND, show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await _show_menu(callback, event, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "edf"))
async def on_pick_field(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Выбрали поле — просим значение."""
    event = await events_service.get(session, callback_data.id)
    if event is None:
        await callback.answer(texts.EVENT_NOT_FOUND, show_alert=True)
        return

    field = callback_data.value
    if field == "format":
        taken = await events_service.seats_taken(session, event.id)
        if taken:
            await callback.answer(texts.EDIT_FORMAT_LOCKED, show_alert=True)
            return

    await callback.answer()
    await state.set_state(EditEventSG.value)
    await state.update_data(
        event_id=event.id, field=field, page=callback_data.page
    )

    ask_key = "max_pairs" if field == "max_players" and event.is_doubles else field
    await edit_or_send(
        callback,
        texts.EDIT_ASK[ask_key],
        edit_value_kb(
            event,
            page=callback_data.page,
            options=_options(field, event),
            clearable=field in CLEARABLE,
        ),
    )


async def _apply(
    session: AsyncSession, event: Event, field: str, raw: str | None
) -> str | None:
    """Кладёт значение в поле. Возвращает текст ошибки или None при успехе."""
    if raw is None:  # очистка
        setattr(event, field, None)
        await session.commit()
        return None

    if field == "title":
        title = raw.strip()
        if not (3 <= len(title) <= 150):
            return texts.NEW_BAD_TITLE
        event.title = title

    elif field == "date":
        date = parse_date(raw)
        if date is None:
            return texts.NEW_BAD_DATE
        if date < now().date():
            return texts.EDIT_DATE_IN_PAST
        event.date = date

    elif field in {"time_start", "time_end"}:
        value = parse_time(raw)
        if value is None:
            return texts.NEW_BAD_TIME
        setattr(event, field, value)

    elif field == "max_players":
        if not raw.isdigit():
            return (
                texts.NEW_BAD_MAX_PAIRS if event.is_doubles else texts.NEW_BAD_MAX_PLAYERS
            )
        seats = int(raw) * 2 if event.is_doubles else int(raw)
        taken = await events_service.seats_taken(session, event.id)
        if seats < taken:
            return texts.EDIT_MAX_TOO_SMALL.format(taken=taken)
        event.max_players = seats

    elif field == "price":
        cleaned = raw.replace(" ", "")
        if not cleaned.isdigit() or int(cleaned) > 1_000_000:
            return texts.NEW_BAD_PRICE
        event.price = int(cleaned)

    elif field == "rating_text":
        event.rating_text = raw.strip()[:64] or None

    elif field == "description":
        if len(raw) > 2000:
            return texts.NEW_BAD_DESCRIPTION
        event.description = raw.strip() or None

    elif field == "format":
        event.format = EventFormat(raw)

    elif field in {"is_rated", "is_public", "show_roster"}:
        setattr(event, field, raw == "1")

    await session.commit()
    return None


async def _finish(
    session: AsyncSession, event: Event, field: str, bot, *, cleared: bool
) -> str:
    """Общий хвост: обновить анонс и собрать текст подтверждения."""
    if bot is not None:
        announce.schedule_refresh(bot, event.id)
    label = texts.FIELD_LABELS[field]
    template = texts.EDIT_CLEARED if cleared else texts.EDIT_SAVED
    return template.format(field=label)


@router.callback_query(EditEventSG.value, AdminCB.filter(F.action == "edset"))
async def on_choice(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Значение выбрано кнопкой — либо конкретное, либо «очистить»."""
    data = await state.get_data()
    event = await events_service.get(session, int(data["event_id"]))
    if event is None:
        await state.clear()
        await callback.answer(texts.EVENT_NOT_FOUND, show_alert=True)
        return

    field = str(data["field"])
    page = int(data["page"])
    cleared = callback_data.value == "__clear__"

    error = await _apply(session, event, field, None if cleared else callback_data.value)
    if error:
        await callback.answer(error, show_alert=True)
        return

    note = await _finish(session, event, field, callback.bot, cleared=cleared)
    await state.clear()
    await callback.answer(note)
    logger.info("Админ %s изменил %s мероприятия %s", callback.from_user.id, field, event.id)
    await _show_menu(callback, event, page=page)


@router.message(EditEventSG.value, F.text)
async def on_typed(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    event = await events_service.get(session, int(data["event_id"]))
    if event is None:
        await state.clear()
        await message.answer(texts.EVENT_NOT_FOUND)
        return

    field = str(data["field"])
    page = int(data["page"])

    if field in CHOICE_FIELDS:
        await message.answer("Здесь нужно нажать кнопку 👆")
        return

    error = await _apply(session, event, field, message.text or "")
    if error:
        await message.answer(error)
        return

    note = await _finish(session, event, field, message.bot, cleared=False)
    await state.clear()
    logger.info("Админ %s изменил %s мероприятия %s", message.from_user.id, field, event.id)
    await _send_menu(message, event, page=page, note=note)


@router.message(EditEventSG.value)
async def on_wrong_content(message: Message) -> None:
    await message.answer("Жду текст или нажатие кнопки 👆")
