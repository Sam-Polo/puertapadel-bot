"""Воронка создания мероприятия.

Обязательны дата, время начала, название, формат, видимость и показ
состава — остальные шаги пропускаются кнопкой, и пропущенные поля просто
не попадают в карточку. Название админ вводит целиком, локация берётся
из настроек клуба и не спрашивается.

Формат идёт до количества мест: от него зависит, считаем мы пары или
людей.
"""

from __future__ import annotations

import datetime as dt
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.config import get_settings
from app.db.models import Event, EventFormat, EventStatus, User
from app.filters import IsAdmin
from app.keyboards.admin import (
    abort_kb,
    admin_menu_kb,
    format_kb,
    max_players_kb,
    preview_kb,
    price_kb,
    show_roster_kb,
    skip_kb,
    visibility_kb,
    yes_no_kb,
)
from app.keyboards.callbacks import AdminCB
from app.services import announce
from app.services import events as events_service
from app.states import NewEventSG
from app.utils.dates import now, parse_date, parse_time
from app.utils.formatting import fmt_when, render_preview
from app.utils.tg import edit_or_send

logger = logging.getLogger(__name__)

router = Router(name="admin_create")
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin())

MAX_PLAYERS_MIN = 2
MAX_PLAYERS_MAX = 64
MAX_PAIRS_MIN = 1
MAX_PAIRS_MAX = 32
PRICE_MAX = 1_000_000
DESCRIPTION_MAX = 2000


async def start_new_event(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NewEventSG.date)
    await message.answer(texts.NEW_ASK_DATE, reply_markup=abort_kb())


@router.callback_query(AdminCB.filter(F.action == "new"))
async def on_new(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await start_new_event(callback.message, state)


@router.callback_query(AdminCB.filter(F.action == "abort"))
async def on_abort(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await edit_or_send(callback, texts.CREATION_ABORTED, admin_menu_kb())


# --- Переходы между шагами ---------------------------------------------------
#
# Каждый шаг умеет показать себя двумя способами: новым сообщением (после
# текстового ввода) и правкой текущего (после нажатия кнопки). Поэтому
# переходы вынесены в отдельные функции — их зовут и обработчики ввода,
# и обработчик «Пропустить».


async def _is_doubles(state: FSMContext) -> bool:
    data = await state.get_data()
    return data.get("format") == EventFormat.DOUBLES.value


async def _go_time_end(state: FSMContext) -> tuple[str, object]:
    await state.set_state(NewEventSG.time_end)
    return texts.NEW_ASK_TIME_END, skip_kb()


async def _go_title(state: FSMContext) -> tuple[str, object]:
    await state.set_state(NewEventSG.title)
    return texts.NEW_ASK_TITLE, abort_kb()


async def _go_format(state: FSMContext) -> tuple[str, object]:
    await state.set_state(NewEventSG.event_format)
    return texts.NEW_ASK_FORMAT, format_kb()


async def _go_max_players(state: FSMContext) -> tuple[str, object]:
    doubles = await _is_doubles(state)
    await state.set_state(NewEventSG.max_players)
    text = texts.NEW_ASK_MAX_PAIRS if doubles else texts.NEW_ASK_MAX_PLAYERS
    return text, max_players_kb(is_doubles=doubles)


async def _go_rating(state: FSMContext) -> tuple[str, object]:
    await state.set_state(NewEventSG.rating)
    return texts.NEW_ASK_RATING, skip_kb()


async def _go_is_rated(state: FSMContext) -> tuple[str, object]:
    await state.set_state(NewEventSG.is_rated)
    return texts.NEW_ASK_IS_RATED, yes_no_kb("rated")


async def _go_price(state: FSMContext) -> tuple[str, object]:
    await state.set_state(NewEventSG.price)
    return texts.NEW_ASK_PRICE, price_kb()


async def _go_visibility(state: FSMContext) -> tuple[str, object]:
    await state.set_state(NewEventSG.visibility)
    return texts.NEW_ASK_VISIBILITY, visibility_kb()


async def _go_show_roster(state: FSMContext) -> tuple[str, object]:
    await state.set_state(NewEventSG.show_roster)
    return texts.NEW_ASK_SHOW_ROSTER, show_roster_kb()


async def _go_description(state: FSMContext) -> tuple[str, object]:
    await state.set_state(NewEventSG.description)
    return texts.NEW_ASK_DESCRIPTION, skip_kb()


async def _go_preview(state: FSMContext) -> tuple[str, object]:
    data = await state.get_data()
    await state.set_state(NewEventSG.preview)
    text = texts.NEW_PREVIEW.format(card=render_preview(_build_draft(data)))
    return text, preview_kb()


# Необязательные шаги: какое поле обнуляем и куда идём дальше.
_SKIPPABLE: dict[str, str] = {
    NewEventSG.time_end.state: "time_end",
    NewEventSG.max_players.state: "max_players",
    NewEventSG.rating.state: "rating_text",
    NewEventSG.is_rated.state: "is_rated",
    NewEventSG.price.state: "price",
    NewEventSG.description.state: "description",
}

_NEXT_AFTER_SKIP = {
    NewEventSG.time_end.state: _go_title,
    NewEventSG.max_players.state: _go_rating,
    NewEventSG.rating.state: _go_is_rated,
    NewEventSG.is_rated.state: _go_price,
    NewEventSG.price.state: _go_visibility,
    NewEventSG.description.state: _go_preview,
}


@router.callback_query(AdminCB.filter(F.action == "skip"))
async def on_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Общая кнопка «Пропустить» — какой шаг пропускаем, знает FSM."""
    current = await state.get_state()
    if current not in _SKIPPABLE:
        await callback.answer("Этот шаг пропустить нельзя", show_alert=True)
        return

    await state.update_data({_SKIPPABLE[current]: None})
    await callback.answer("Пропущено")
    text, markup = await _NEXT_AFTER_SKIP[current](state)
    await edit_or_send(callback, text, markup)  # type: ignore[arg-type]


# --- Шаг 1: дата (обязательный) ---


@router.message(NewEventSG.date, F.text)
async def on_date(message: Message, state: FSMContext) -> None:
    date = parse_date(message.text or "")
    if date is None:
        await message.answer(texts.NEW_BAD_DATE, reply_markup=abort_kb())
        return
    if date < now().date():
        await message.answer(texts.NEW_DATE_IN_PAST, reply_markup=abort_kb())
        return

    await state.update_data(date=date.isoformat())
    await state.set_state(NewEventSG.time_start)
    await message.answer(texts.NEW_ASK_TIME_START, reply_markup=abort_kb())


# --- Шаг 2-3: время ---


@router.message(NewEventSG.time_start, F.text)
async def on_time_start(message: Message, state: FSMContext) -> None:
    time_start = parse_time(message.text or "")
    if time_start is None:
        await message.answer(texts.NEW_BAD_TIME, reply_markup=abort_kb())
        return

    await state.update_data(time_start=time_start.isoformat())
    text, markup = await _go_time_end(state)
    await message.answer(text, reply_markup=markup)  # type: ignore[arg-type]


@router.message(NewEventSG.time_end, F.text)
async def on_time_end(message: Message, state: FSMContext) -> None:
    time_end = parse_time(message.text or "")
    if time_end is None:
        await message.answer(texts.NEW_BAD_TIME, reply_markup=skip_kb())
        return

    await state.update_data(time_end=time_end.isoformat())
    text, markup = await _go_title(state)
    await message.answer(text, reply_markup=markup)  # type: ignore[arg-type]


# --- Шаг 4: название (обязательный) ---


@router.message(NewEventSG.title, F.text)
async def on_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not (3 <= len(title) <= 150):
        await message.answer(texts.NEW_BAD_TITLE, reply_markup=abort_kb())
        return

    await state.update_data(title=title)
    text, markup = await _go_format(state)
    await message.answer(text, reply_markup=markup)  # type: ignore[arg-type]


# --- Шаг 5: формат (обязательный) ---


@router.callback_query(NewEventSG.event_format, AdminCB.filter(F.action == "fmt"))
async def on_format(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await callback.answer()
    await state.update_data(format=callback_data.value)
    text, markup = await _go_max_players(state)
    await edit_or_send(callback, text, markup)  # type: ignore[arg-type]


# --- Шаг 6: вместимость ---


def _seats_from_input(value: int, *, is_doubles: bool) -> int:
    """В парном админ вводит пары, а хранится всё в местах."""
    return value * 2 if is_doubles else value


@router.callback_query(NewEventSG.max_players, AdminCB.filter(F.action == "max"))
async def on_max_players_preset(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await callback.answer()
    doubles = await _is_doubles(state)
    seats = _seats_from_input(int(callback_data.value), is_doubles=doubles)
    await state.update_data(max_players=seats)
    text, markup = await _go_rating(state)
    await edit_or_send(callback, text, markup)  # type: ignore[arg-type]


@router.message(NewEventSG.max_players, F.text)
async def on_max_players(message: Message, state: FSMContext) -> None:
    doubles = await _is_doubles(state)
    raw = (message.text or "").strip()
    low, high = (
        (MAX_PAIRS_MIN, MAX_PAIRS_MAX) if doubles else (MAX_PLAYERS_MIN, MAX_PLAYERS_MAX)
    )
    if not raw.isdigit() or not (low <= int(raw) <= high):
        error = texts.NEW_BAD_MAX_PAIRS if doubles else texts.NEW_BAD_MAX_PLAYERS
        await message.answer(error, reply_markup=max_players_kb(is_doubles=doubles))
        return

    await state.update_data(
        max_players=_seats_from_input(int(raw), is_doubles=doubles)
    )
    text, markup = await _go_rating(state)
    await message.answer(text, reply_markup=markup)  # type: ignore[arg-type]


# --- Шаг 7: рейтинг ---


@router.message(NewEventSG.rating, F.text)
async def on_rating(message: Message, state: FSMContext) -> None:
    rating = (message.text or "").strip()[:64]
    await state.update_data(rating_text=rating or None)
    text, markup = await _go_is_rated(state)
    await message.answer(text, reply_markup=markup)  # type: ignore[arg-type]


# --- Шаг 8: рейтинговое ---


@router.callback_query(NewEventSG.is_rated, AdminCB.filter(F.action == "rated"))
async def on_is_rated(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await callback.answer()
    await state.update_data(is_rated=callback_data.value == "1")
    text, markup = await _go_price(state)
    await edit_or_send(callback, text, markup)  # type: ignore[arg-type]


# --- Шаг 9: стоимость ---


@router.callback_query(NewEventSG.price, AdminCB.filter(F.action == "price"))
async def on_price_free(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(price=0)
    text, markup = await _go_visibility(state)
    await edit_or_send(callback, text, markup)  # type: ignore[arg-type]


@router.message(NewEventSG.price, F.text)
async def on_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit() or int(raw) > PRICE_MAX:
        await message.answer(texts.NEW_BAD_PRICE, reply_markup=price_kb())
        return

    await state.update_data(price=int(raw))
    text, markup = await _go_visibility(state)
    await message.answer(text, reply_markup=markup)  # type: ignore[arg-type]


# --- Шаг 10-11: видимость мероприятия и состава ---


@router.callback_query(NewEventSG.visibility, AdminCB.filter(F.action == "vis"))
async def on_visibility(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await callback.answer()
    await state.update_data(is_public=callback_data.value == "1")
    text, markup = await _go_show_roster(state)
    await edit_or_send(callback, text, markup)  # type: ignore[arg-type]


@router.callback_query(NewEventSG.show_roster, AdminCB.filter(F.action == "roster"))
async def on_show_roster(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await callback.answer()
    await state.update_data(show_roster=callback_data.value == "1")
    text, markup = await _go_description(state)
    await edit_or_send(callback, text, markup)  # type: ignore[arg-type]


# --- Шаг 12: описание ---


@router.message(NewEventSG.description, F.text)
async def on_description(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if len(description) > DESCRIPTION_MAX:
        await message.answer(texts.NEW_BAD_DESCRIPTION, reply_markup=skip_kb())
        return

    await state.update_data(description=description or None)
    text, markup = await _go_preview(state)
    await message.answer(text, reply_markup=markup)  # type: ignore[arg-type]


# --- Сохранение ---


def _draft_format(data: dict) -> EventFormat:
    return EventFormat(data.get("format", EventFormat.SINGLES.value))


def _build_draft(data: dict) -> Event:
    """Непривязанный к сессии объект — только чтобы отрисовать предпросмотр."""
    return Event(
        id=0,
        title=data["title"],
        date=dt.date.fromisoformat(data["date"]),
        time_start=dt.time.fromisoformat(data["time_start"]),
        time_end=(
            dt.time.fromisoformat(data["time_end"]) if data.get("time_end") else None
        ),
        format=_draft_format(data),
        location=get_settings().location_name,
        max_players=data.get("max_players"),
        rating_text=data.get("rating_text"),
        is_rated=data.get("is_rated"),
        price=data.get("price"),
        description=data.get("description"),
        is_public=data.get("is_public", True),
        show_roster=data.get("show_roster", True),
        status=EventStatus.DRAFT,
        created_by=0,
    )


async def _persist(
    data: dict, session: AsyncSession, admin_id: int, *, status: EventStatus
) -> Event:
    return await events_service.create(
        session,
        title=data["title"],
        date=dt.date.fromisoformat(data["date"]),
        time_start=dt.time.fromisoformat(data["time_start"]),
        time_end=(
            dt.time.fromisoformat(data["time_end"]) if data.get("time_end") else None
        ),
        event_format=_draft_format(data),
        max_players=data.get("max_players"),
        rating_text=data.get("rating_text"),
        is_rated=data.get("is_rated"),
        price=data.get("price"),
        description=data.get("description"),
        is_public=data.get("is_public", True),
        show_roster=data.get("show_roster", True),
        status=status,
        created_by=admin_id,
    )


@router.callback_query(NewEventSG.preview, AdminCB.filter(F.action == "draft"))
async def on_save_draft(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    event = await _persist(data, session, user.id, status=EventStatus.DRAFT)
    await state.clear()
    await callback.answer()
    logger.info("Админ %s сохранил черновик мероприятия %s", user.id, event.id)
    await edit_or_send(callback, texts.CREATED_DRAFT, admin_menu_kb())


@router.callback_query(NewEventSG.preview, AdminCB.filter(F.action == "publish"))
async def on_publish(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    event = await _persist(data, session, user.id, status=EventStatus.OPEN)
    await state.clear()
    await callback.answer()

    lines = [texts.CREATED_PUBLISHED]
    if not event.is_public:
        lines.append(texts.ANNOUNCE_HIDDEN)
    elif callback.bot is not None:
        try:
            published = await announce.publish(callback.bot, session, event)
        except announce.AnnounceError as error:
            lines.append(texts.ANNOUNCE_FAILED.format(error=error))
        else:
            lines.append(
                texts.ANNOUNCE_OK if published else texts.ANNOUNCE_NOT_CONFIGURED
            )

    lines.append("")
    lines.append(f"📅 {fmt_when(event)}")
    lines.append(f"🔗 Ссылка на запись:\n{get_settings().deep_link(event.id)}")

    logger.info("Админ %s опубликовал мероприятие %s", user.id, event.id)
    await edit_or_send(callback, "\n".join(lines), admin_menu_kb())


# --- Ввод не того типа ---


@router.message(NewEventSG.date)
@router.message(NewEventSG.time_start)
@router.message(NewEventSG.time_end)
@router.message(NewEventSG.title)
@router.message(NewEventSG.max_players)
@router.message(NewEventSG.rating)
@router.message(NewEventSG.price)
@router.message(NewEventSG.description)
async def on_wrong_content(message: Message) -> None:
    await message.answer("Жду текст 👆")


@router.message(NewEventSG.event_format)
@router.message(NewEventSG.is_rated)
@router.message(NewEventSG.visibility)
@router.message(NewEventSG.show_roster)
@router.message(NewEventSG.preview)
async def on_expected_button(message: Message) -> None:
    await message.answer("Нажмите одну из кнопок выше 👆")
