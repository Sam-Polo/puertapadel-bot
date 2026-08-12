"""Подкладывает в хендлеры объект User и закрывает бота для незарегистрированных."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import texts
from app.config import get_settings
from app.keyboards.callbacks import RegCB
from app.keyboards.common import start_registration_kb
from app.services import users as users_service
from app.states import RegistrationSG

logger = logging.getLogger(__name__)

# Что разрешено делать до завершения регистрации.
_ALLOWED_COMMANDS = ("/start", "/help", "/cancel")


class UserMiddleware(BaseMiddleware):
    """Заводит/обновляет запись пользователя и кладёт её в data['user']."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        session = data.get("session")
        if tg_user is None or session is None or tg_user.is_bot:
            return await handler(event, data)

        user = await users_service.get_or_create(session, tg_user)
        data["user"] = user

        if user.is_blocked:
            if isinstance(event, CallbackQuery):
                await event.answer(texts.BLOCKED, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(texts.BLOCKED)
            return None

        return await handler(event, data)


class RegistrationGateMiddleware(BaseMiddleware):
    """Не пропускает дальше, пока пользователь не завершил регистрацию.

    Обойти воронку можно тремя способами: прислать команду, нажать старую
    инлайн-кнопку из прошлой сессии или прислать что-то в произвольный
    момент. Все три перехватываем здесь, а не в каждом хендлере.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("user")
        if user is None or user.is_registered:
            return await handler(event, data)

        # Админу регистрация не нужна: он управляет мероприятиями, а не играет.
        # Если захочет записаться — воронку предложит сам хендлер записи.
        if get_settings().is_admin(user.id):
            return await handler(event, data)

        # Сообщения из групп сюда не доходят (гейт вешаем только на приватные
        # апдейты), но на всякий случай — пропускаем.
        if isinstance(event, Message) and event.chat.type != "private":
            return await handler(event, data)

        if await self._is_registration_flow(event, data):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message is not None:
                await event.message.answer(
                    texts.NEED_REGISTRATION, reply_markup=start_registration_kb()
                )
        elif isinstance(event, Message):
            state: FSMContext | None = data.get("state")
            current = await state.get_state() if state else None
            if current is not None and current.startswith(RegistrationSG.__name__):
                # Пользователь внутри воронки, но прислал не то, что просили.
                await event.answer(texts.REGISTRATION_IN_PROGRESS)
            else:
                await event.answer(
                    texts.NEED_REGISTRATION, reply_markup=start_registration_kb()
                )
        return None

    @staticmethod
    async def _is_registration_flow(event: TelegramObject, data: dict[str, Any]) -> bool:
        if isinstance(event, CallbackQuery):
            # Кнопки самой воронки регистрации.
            return bool(event.data and event.data.startswith(f"{RegCB.__prefix__}:"))

        if isinstance(event, Message):
            text = (event.text or "").strip()
            if text.startswith(_ALLOWED_COMMANDS):
                return True
            # Текстовые шаги воронки: имя, фамилия, уровень.
            state: FSMContext | None = data.get("state")
            if state is None:
                return False
            current = await state.get_state()
            return current is not None and current.startswith(RegistrationSG.__name__)

        return False
