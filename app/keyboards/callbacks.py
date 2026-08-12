"""Callback-фабрики.

Префиксы короткие: Telegram даёт на callback_data всего 64 байта.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="m"):
    """Навигация по главному меню."""

    action: str  # main | events | my | profile


class RegCB(CallbackData, prefix="r"):
    """Шаги регистрации участника."""

    action: str  # start | accept | decline | gender | confirm | restart
    value: str = ""


class EventCB(CallbackData, prefix="e"):
    """Действия участника с мероприятием."""

    action: str  # view | signup | seats | signup_ok | cancel | cancel_ok
    id: int = 0
    page: int = 0
    src: str = "list"  # откуда пришли: list | my | link — куда вернуть по «Назад»
    value: str = ""


class PageCB(CallbackData, prefix="p"):
    """Пагинация списков."""

    scope: str  # events | my | admin_events | admin_users
    page: int


class AdminCB(CallbackData, prefix="a"):
    """Действия админа."""

    action: str
    id: int = 0
    page: int = 0
    value: str = ""
