"""Публикация анонсов в чат клуба и рассылка уведомлений.

Анонс отправляется один раз при публикации мероприятия. Дальше он не
трогается — состав в чате не показываем, — но при смене статуса (набор
закрыт, мероприятие отменено) сообщение перерисовывается, чтобы в чате не
висела неправда.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Event
from app.utils.formatting import render_announcement

logger = logging.getLogger(__name__)


class AnnounceError(Exception):
    """Анонс отправить не удалось — текст исключения показываем админу."""


async def publish(bot: Bot, session: AsyncSession, event: Event) -> bool:
    """Публикует анонс. False — если публиковать нечего или незачем."""
    settings = get_settings()
    if not settings.announces_enabled:
        return False
    if not event.is_public:
        return False
    if event.announce_message_id is not None:
        return False

    try:
        message = await bot.send_message(
            chat_id=settings.announce_chat_id,  # type: ignore[arg-type]
            message_thread_id=settings.announce_thread_id,
            text=render_announcement(event),
            disable_web_page_preview=True,
        )
    except TelegramAPIError as error:
        logger.warning("Не удалось опубликовать анонс мероприятия %s: %s", event.id, error)
        raise AnnounceError(str(error)) from error

    event.announce_chat_id = message.chat.id
    event.announce_message_id = message.message_id
    await session.commit()
    logger.info("Анонс мероприятия %s опубликован: %s", event.id, message.message_id)
    return True


async def refresh(bot: Bot, event: Event) -> None:
    """Перерисовывает опубликованный анонс. Ошибки только логируем.

    Правка анонса — не критичный путь: если сообщение удалили или бота
    выгнали из чата, мероприятие от этого ломаться не должно.
    """
    if event.announce_message_id is None or event.announce_chat_id is None:
        return
    try:
        await bot.edit_message_text(
            chat_id=event.announce_chat_id,
            message_id=event.announce_message_id,
            text=render_announcement(event),
            disable_web_page_preview=True,
        )
    except TelegramAPIError as error:
        # "message is not modified" — тоже сюда, и это нормально.
        logger.info("Анонс мероприятия %s не обновлён: %s", event.id, error)


async def notify_user(bot: Bot, user_id: int, text: str, **kwargs) -> bool:
    """Личное сообщение игроку. False — если доставить не удалось."""
    try:
        await bot.send_message(user_id, text, **kwargs)
        return True
    except TelegramRetryAfter as error:
        await asyncio.sleep(error.retry_after)
        try:
            await bot.send_message(user_id, text, **kwargs)
            return True
        except TelegramAPIError:
            return False
    except TelegramAPIError as error:
        # Обычно: бот заблокирован пользователем. Это не наша проблема.
        logger.info("Не доставлено пользователю %s: %s", user_id, error)
        return False


async def notify_users(bot: Bot, user_ids: list[int], text: str) -> int:
    """Рассылка списку игроков. Возвращает число доставленных.

    Telegram разрешает ~30 сообщений в секунду; пауза держит нас ниже лимита.
    """
    delivered = 0
    for user_id in user_ids:
        if await notify_user(bot, user_id, text):
            delivered += 1
        await asyncio.sleep(0.05)
    return delivered


async def notify_admins(bot: Bot, text: str, *, exclude: int | None = None) -> None:
    settings = get_settings()
    for admin_id in settings.admin_ids:
        if admin_id == exclude:
            continue
        await notify_user(bot, admin_id, text)
