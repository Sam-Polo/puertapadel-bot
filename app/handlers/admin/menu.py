"""Команда /admin и корневое меню админки."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.filters import IsAdmin
from app.keyboards.admin import admin_menu_kb
from app.keyboards.callbacks import AdminCB
from app.utils.tg import edit_or_send

router = Router(name="admin_menu")


@router.message(Command("admin"), F.chat.type == "private", IsAdmin())
async def cmd_admin(message: Message, state: FSMContext) -> None:
    """Вход в админку сбрасывает незавершённые воронки — иначе легко
    оказаться «внутри» создания турнира и не понимать, почему бот
    просит ввести время."""
    await state.clear()
    await message.answer(texts.ADMIN_MENU, reply_markup=admin_menu_kb())


@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin_denied(message: Message) -> None:
    """Не-админу отвечаем явно, а не молчим: иначе он решит, что бот сломан."""
    await message.answer(texts.ADMIN_ONLY)


@router.callback_query(AdminCB.filter(F.action == "menu"), IsAdmin())
async def on_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await edit_or_send(callback, texts.ADMIN_MENU, admin_menu_kb())
