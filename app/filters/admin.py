"""Фильтр «это администратор»."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import get_settings


class IsAdmin(BaseFilter):
    """Список админов задаётся в .env и в рантайме не меняется."""

    async def __call__(self, event: TelegramObject) -> bool:
        user = None
        if isinstance(event, Message | CallbackQuery):
            user = event.from_user
        if user is None:
            return False
        return get_settings().is_admin(user.id)
