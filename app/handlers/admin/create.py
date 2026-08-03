"""Воронка создания турнира.

Шаги идут в порядке «сначала то, из чего можно собрать остальное»: дата и
время нужны, чтобы предложить служебный префикс названия «(СБ1100) 1904».
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
from app.db.models import Tournament, TournamentStatus, User
from app.filters import IsAdmin
from app.keyboards.admin import (
    abort_kb,
    admin_menu_kb,
    locations_kb,
    max_players_kb,
    preview_kb,
    price_kb,
    rating_kb,
    title_kb,
    visibility_kb,
    yes_no_kb,
)
from app.keyboards.callbacks import AdminCB
from app.services import announce
from app.services import tournaments as tournaments_service
from app.states import NewTournamentSG
from app.utils.dates import fmt_date, now, parse_date, parse_time, title_prefix
from app.utils.formatting import render_preview
from app.utils.tg import edit_or_send

logger = logging.getLogger(__name__)

router = Router(name="admin_create")
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin())

MAX_PLAYERS_MIN = 2
MAX_PLAYERS_MAX = 64
PRICE_MAX = 1_000_000


async def start_new_tournament(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NewTournamentSG.date)
    await message.answer(texts.NEW_ASK_DATE, reply_markup=abort_kb())


@router.callback_query(AdminCB.filter(F.action == "new"))
async def on_new(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await start_new_tournament(callback.message, state)


@router.callback_query(AdminCB.filter(F.action == "abort"))
async def on_abort(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await edit_or_send(callback, texts.CREATION_ABORTED, admin_menu_kb())


# --- Шаг 1: дата ---


@router.message(NewTournamentSG.date, F.text)
async def on_date(message: Message, state: FSMContext) -> None:
    date = parse_date(message.text or "")
    if date is None:
        await message.answer(texts.NEW_BAD_DATE, reply_markup=abort_kb())
        return
    if date < now().date():
        await message.answer(texts.NEW_DATE_IN_PAST, reply_markup=abort_kb())
        return

    await state.update_data(date=date.isoformat())
    await state.set_state(NewTournamentSG.time_start)
    await message.answer(texts.NEW_ASK_TIME_START, reply_markup=abort_kb())


# --- Шаг 2-3: время ---


@router.message(NewTournamentSG.time_start, F.text)
async def on_time_start(message: Message, state: FSMContext) -> None:
    time_start = parse_time(message.text or "")
    if time_start is None:
        await message.answer(texts.NEW_BAD_TIME, reply_markup=abort_kb())
        return

    await state.update_data(time_start=time_start.isoformat())
    await state.set_state(NewTournamentSG.time_end)
    await message.answer(texts.NEW_ASK_TIME_END, reply_markup=abort_kb())


@router.message(NewTournamentSG.time_end, F.text)
async def on_time_end(message: Message, state: FSMContext) -> None:
    time_end = parse_time(message.text or "")
    if time_end is None:
        await message.answer(texts.NEW_BAD_TIME, reply_markup=abort_kb())
        return

    data = await state.update_data(time_end=time_end.isoformat())
    date = dt.date.fromisoformat(data["date"])
    time_start = dt.time.fromisoformat(data["time_start"])

    await state.set_state(NewTournamentSG.title)
    await message.answer(
        texts.NEW_ASK_TITLE.format(prefix=title_prefix(date, time_start)),
        reply_markup=title_kb(),
    )


# --- Шаг 4: название ---


@router.callback_query(NewTournamentSG.title, AdminCB.filter(F.action == "title_full"))
async def on_title_full(callback: CallbackQuery, state: FSMContext) -> None:
    """Админ хочет ввести название целиком, без служебного префикса."""
    await callback.answer()
    await state.update_data(skip_prefix=True)
    await edit_or_send(callback, texts.NEW_ASK_TITLE_FULL, abort_kb())


@router.message(NewTournamentSG.title, F.text)
async def on_title(message: Message, state: FSMContext, session: AsyncSession) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()

    if data.get("skip_prefix"):
        title = raw
    else:
        date = dt.date.fromisoformat(data["date"])
        time_start = dt.time.fromisoformat(data["time_start"])
        title = f"{title_prefix(date, time_start)} {raw}"

    if not (3 <= len(title) <= 150):
        await message.answer(texts.NEW_BAD_TITLE, reply_markup=abort_kb())
        return

    await state.update_data(title=title)
    await state.set_state(NewTournamentSG.location)

    recent = await tournaments_service.recent_locations(session)
    await state.update_data(recent_locations=recent)
    await message.answer(
        texts.NEW_ASK_LOCATION,
        reply_markup=locations_kb(recent) if recent else abort_kb(),
    )


# --- Шаг 5: локация ---


@router.callback_query(NewTournamentSG.location, AdminCB.filter(F.action == "loc"))
async def on_location_preset(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    data = await state.get_data()
    recent: list[str] = data.get("recent_locations", [])
    try:
        location = recent[int(callback_data.value)]
    except (ValueError, IndexError):
        await callback.answer("Не нашёл эту локацию, введите текстом", show_alert=True)
        return

    await callback.answer()
    await state.update_data(location=location)
    await state.set_state(NewTournamentSG.max_players)
    await edit_or_send(callback, texts.NEW_ASK_MAX_PLAYERS, max_players_kb())


@router.message(NewTournamentSG.location, F.text)
async def on_location(message: Message, state: FSMContext) -> None:
    location = (message.text or "").strip()
    if not (2 <= len(location) <= 100):
        await message.answer(texts.NEW_BAD_LOCATION, reply_markup=abort_kb())
        return

    await state.update_data(location=location)
    await state.set_state(NewTournamentSG.max_players)
    await message.answer(texts.NEW_ASK_MAX_PLAYERS, reply_markup=max_players_kb())


# --- Шаг 6: количество игроков ---


async def _go_to_rating(state: FSMContext) -> None:
    await state.set_state(NewTournamentSG.rating)


@router.callback_query(NewTournamentSG.max_players, AdminCB.filter(F.action == "max"))
async def on_max_players_preset(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await callback.answer()
    await state.update_data(max_players=int(callback_data.value))
    await _go_to_rating(state)
    await edit_or_send(callback, texts.NEW_ASK_RATING, rating_kb())


@router.message(NewTournamentSG.max_players, F.text)
async def on_max_players(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (MAX_PLAYERS_MIN <= int(raw) <= MAX_PLAYERS_MAX):
        await message.answer(texts.NEW_BAD_MAX_PLAYERS, reply_markup=max_players_kb())
        return

    await state.update_data(max_players=int(raw))
    await _go_to_rating(state)
    await message.answer(texts.NEW_ASK_RATING, reply_markup=rating_kb())


# --- Шаг 7: рейтинг ---


@router.callback_query(NewTournamentSG.rating, AdminCB.filter(F.action == "rating"))
async def on_rating_any(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(rating_text=None)
    await state.set_state(NewTournamentSG.is_rated)
    await edit_or_send(callback, texts.NEW_ASK_IS_RATED, yes_no_kb("rated"))


@router.message(NewTournamentSG.rating, F.text)
async def on_rating(message: Message, state: FSMContext) -> None:
    rating = (message.text or "").strip()[:64]
    await state.update_data(rating_text=rating or None)
    await state.set_state(NewTournamentSG.is_rated)
    await message.answer(texts.NEW_ASK_IS_RATED, reply_markup=yes_no_kb("rated"))


# --- Шаг 8: рейтинговый ---


@router.callback_query(NewTournamentSG.is_rated, AdminCB.filter(F.action == "rated"))
async def on_is_rated(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await callback.answer()
    await state.update_data(is_rated=callback_data.value == "1")
    await state.set_state(NewTournamentSG.price)
    await edit_or_send(callback, texts.NEW_ASK_PRICE, price_kb())


# --- Шаг 9: стоимость ---


async def _go_to_visibility(state: FSMContext) -> None:
    await state.set_state(NewTournamentSG.visibility)


@router.callback_query(NewTournamentSG.price, AdminCB.filter(F.action == "price"))
async def on_price_free(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(price=0)
    await _go_to_visibility(state)
    await edit_or_send(callback, texts.NEW_ASK_VISIBILITY, visibility_kb())


@router.message(NewTournamentSG.price, F.text)
async def on_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit() or int(raw) > PRICE_MAX:
        await message.answer(texts.NEW_BAD_PRICE, reply_markup=price_kb())
        return

    await state.update_data(price=int(raw))
    await _go_to_visibility(state)
    await message.answer(texts.NEW_ASK_VISIBILITY, reply_markup=visibility_kb())


# --- Шаг 10: видимость и предпросмотр ---


@router.callback_query(NewTournamentSG.visibility, AdminCB.filter(F.action == "vis"))
async def on_visibility(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await callback.answer()
    data = await state.update_data(is_public=callback_data.value == "1")
    await state.set_state(NewTournamentSG.preview)

    draft = _build_draft(data)
    await edit_or_send(
        callback,
        texts.NEW_PREVIEW.format(card=render_preview(draft)),
        preview_kb(),
    )


def _build_draft(data: dict) -> Tournament:
    """Непривязанный к сессии объект — только чтобы отрисовать предпросмотр."""
    return Tournament(
        id=0,
        title=data["title"],
        location=data["location"],
        date=dt.date.fromisoformat(data["date"]),
        time_start=dt.time.fromisoformat(data["time_start"]),
        time_end=dt.time.fromisoformat(data["time_end"]),
        max_players=data["max_players"],
        rating_text=data.get("rating_text"),
        is_rated=data.get("is_rated", False),
        price=data.get("price"),
        is_public=data.get("is_public", True),
        status=TournamentStatus.DRAFT,
        created_by=0,
    )


async def _persist(
    data: dict, session: AsyncSession, admin_id: int, *, status: TournamentStatus
):
    return await tournaments_service.create(
        session,
        title=data["title"],
        location=data["location"],
        date=dt.date.fromisoformat(data["date"]),
        time_start=dt.time.fromisoformat(data["time_start"]),
        time_end=dt.time.fromisoformat(data["time_end"]),
        max_players=data["max_players"],
        rating_text=data.get("rating_text"),
        is_rated=data.get("is_rated", False),
        price=data.get("price"),
        is_public=data.get("is_public", True),
        status=status,
        created_by=admin_id,
    )


@router.callback_query(NewTournamentSG.preview, AdminCB.filter(F.action == "draft"))
async def on_save_draft(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    tournament = await _persist(data, session, user.id, status=TournamentStatus.DRAFT)
    await state.clear()
    await callback.answer()
    logger.info("Админ %s сохранил черновик турнира %s", user.id, tournament.id)
    await edit_or_send(callback, texts.CREATED_DRAFT, admin_menu_kb())


@router.callback_query(NewTournamentSG.preview, AdminCB.filter(F.action == "publish"))
async def on_publish(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    tournament = await _persist(data, session, user.id, status=TournamentStatus.OPEN)
    await state.clear()
    await callback.answer()

    lines = [texts.CREATED_PUBLISHED]
    if not tournament.is_public:
        lines.append(texts.ANNOUNCE_HIDDEN)
    elif callback.bot is None:
        pass
    else:
        try:
            published = await announce.publish(callback.bot, session, tournament)
        except announce.AnnounceError as error:
            lines.append(texts.ANNOUNCE_FAILED.format(error=error))
        else:
            lines.append(
                texts.ANNOUNCE_OK if published else texts.ANNOUNCE_NOT_CONFIGURED
            )

    lines.append("")
    lines.append(f"📅 {fmt_date(tournament.date)}")
    lines.append(f"🔗 Ссылка на запись:\n{get_settings().deep_link(tournament.id)}")

    logger.info("Админ %s опубликовал турнир %s", user.id, tournament.id)
    await edit_or_send(callback, "\n".join(lines), admin_menu_kb())


@router.message(NewTournamentSG.date)
@router.message(NewTournamentSG.time_start)
@router.message(NewTournamentSG.time_end)
@router.message(NewTournamentSG.title)
@router.message(NewTournamentSG.location)
@router.message(NewTournamentSG.max_players)
@router.message(NewTournamentSG.rating)
@router.message(NewTournamentSG.price)
async def on_wrong_content(message: Message) -> None:
    await message.answer("Жду текст 👆", reply_markup=abort_kb())


@router.message(NewTournamentSG.is_rated)
@router.message(NewTournamentSG.visibility)
@router.message(NewTournamentSG.preview)
async def on_expected_button(message: Message) -> None:
    await message.answer("Нажмите одну из кнопок выше 👆")
