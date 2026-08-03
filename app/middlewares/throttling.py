"""Простейший антиспам: не чаще N действий в секунду от одного пользователя.

Держим в памяти — при рестарте счётчики обнуляются, и это нормально:
задача middleware не в безопасности, а в том, чтобы зажатая кнопка
не превращалась в очередь одинаковых сообщений.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app import texts

# Разрешаем всплеск, но не поток.
LIMIT = 5
WINDOW = 3.0


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limit: int = LIMIT, window: float = WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._hits: defaultdict[int, deque[float]] = defaultdict(deque)
        self._warned: dict[int, float] = {}

    def _is_flooding(self, user_id: int) -> bool:
        current = time.monotonic()
        hits = self._hits[user_id]
        while hits and current - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return True
        hits.append(current)
        return False

    def _should_warn(self, user_id: int) -> bool:
        """Предупреждаем не чаще раза в 5 секунд, чтобы не спамить в ответ на спам."""
        current = time.monotonic()
        last = self._warned.get(user_id, 0.0)
        if current - last < 5.0:
            return False
        self._warned[user_id] = current
        return True

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        if not self._is_flooding(user.id):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            # Callback обязательно нужно «закрыть», иначе у клиента крутится часик.
            await event.answer(texts.TOO_FAST, show_alert=False)
        elif isinstance(event, Message) and self._should_warn(user.id):
            await event.answer(texts.TOO_FAST)
        return None
