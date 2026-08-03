"""Точки входа: /start, /help, /cancel, главное меню и профиль.

Два роутера: `router` подключается первым (команды должны перехватываться
раньше текстовых шагов любой воронки), `fallback_router` — последним.
"""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.models import User
from app.handlers.registration import PENDING_TOURNAMENT, start_registration
from app.handlers.tournaments import send_tournament_card
from app.keyboards.callbacks import MenuCB
from app.keyboards.common import main_menu_kb, profile_kb
from app.services import users as users_service
from app.utils.formatting import q
from app.utils.tg import edit_or_send

logger = logging.getLogger(__name__)

router = Router(name="common")
router.message.filter(F.chat.type == "private")

fallback_router = Router(name="fallback")
fallback_router.message.filter(F.chat.type == "private")

_DEEP_LINK_RE = re.compile(r"^t(\d+)$")


def parse_deep_link(payload: str | None) -> int | None:
    """«t42» -> 42. Всё остальное игнорируем."""
    if not payload:
        return None
    match = _DEEP_LINK_RE.match(payload.strip())
    return int(match.group(1)) if match else None


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    """Единственная дверь в бота.

    Четыре случая: новичок, новичок по ссылке из анонса, зарегистрированный,
    зарегистрированный по ссылке. Плюс пятый — /start прямо посреди
    регистрации: воронка начинается заново, как и договаривались.
    """
    tournament_id = parse_deep_link(command.args)

    if not user.is_registered:
        await state.clear()
        if tournament_id is not None:
            await state.update_data({PENDING_TOURNAMENT: tournament_id})
            await message.answer(texts.WELCOME_FROM_ANNOUNCE)
        else:
            await message.answer(texts.WELCOME)
        await start_registration(message, state)
        return

    await state.clear()

    if tournament_id is not None:
        await send_tournament_card(message, session, user, tournament_id, src="link")
        return

    await message.answer(texts.MAIN_MENU, reply_markup=main_menu_kb())


@router.message(Command("help"))
async def cmd_help(message: Message, user: User) -> None:
    if not user.is_registered:
        await message.answer(texts.WELCOME)
        return
    await message.answer(texts.MAIN_MENU, reply_markup=main_menu_kb())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
    if not user.is_registered:
        await start_registration(message, state)
        return
    await message.answer(texts.MAIN_MENU, reply_markup=main_menu_kb())


@router.callback_query(MenuCB.filter(F.action == "main"))
async def open_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await edit_or_send(callback, texts.MAIN_MENU, main_menu_kb())


@router.callback_query(MenuCB.filter(F.action == "profile"))
async def open_profile(
    callback: CallbackQuery, session: AsyncSession, user: User
) -> None:
    await callback.answer()
    count = await users_service.count_registrations(session, user.id)
    await edit_or_send(
        callback,
        texts.PROFILE.format(
            first_name=q(user.first_name),
            last_name=q(user.last_name),
            gender=texts.GENDER_LABEL.get(str(user.gender), "—"),
            age=user.age,
            registrations=count,
        ),
        profile_kb(),
    )


@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    """Кнопка-счётчик «2/5» — нажимается, но ничего не делает."""
    await callback.answer()


@fallback_router.callback_query()
async def on_stale_callback(callback: CallbackQuery) -> None:
    """Кнопка, под которую не нашлось хендлера, — обычно из старого сообщения."""
    await callback.answer("Кнопка устарела. Откройте меню: /start", show_alert=True)


@fallback_router.message()
async def on_unknown_message(message: Message, user: User) -> None:
    """Любой текст вне воронок — возвращаем в меню, а не молчим."""
    if not user.is_registered:
        await message.answer(texts.NEED_REGISTRATION)
        return
    await message.answer(texts.MAIN_MENU, reply_markup=main_menu_kb())
