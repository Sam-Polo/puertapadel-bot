"""Мелкие помощники поверх aiogram."""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


async def edit_or_send(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Перерисовывает сообщение, к которому прикреплена кнопка.

    Если сообщение слишком старое или недоступно — отправляем новое, чтобы
    пользователь не остался без ответа.
    """
    message = callback.message
    if message is None:
        if callback.from_user is not None and callback.bot is not None:
            await callback.bot.send_message(
                callback.from_user.id, text, reply_markup=reply_markup
            )
        return

    try:
        await message.edit_text(
            text, reply_markup=reply_markup, disable_web_page_preview=True
        )
    except TelegramBadRequest as error:
        if "message is not modified" in str(error):
            return
        logger.debug("edit_text не удался, отправляю новое сообщение: %s", error)
        await message.answer(text, reply_markup=reply_markup, disable_web_page_preview=True)


def paginate[T](items: list[T], page: int, page_size: int) -> tuple[list[T], int, int]:
    """Возвращает (срез, нормализованная страница, всего страниц)."""
    total_pages = max((len(items) + page_size - 1) // page_size, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    return items[start : start + page_size], page, total_pages
