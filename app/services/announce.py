"""Публикация анонсов в чат клуба и рассылка уведомлений.

Анонс публикуется один раз, дальше правится: при смене статуса и по мере
записи участников. Правки идут через очередь с дебаунсом — Telegram
разрешает не больше 20 сообщений в минуту на группу и не чаще одного
в секунду в один чат, а всплеск записей легко даёт больше.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Event
from app.db.session import session_factory
from app.utils.formatting import render_announcement

logger = logging.getLogger(__name__)

# Сколько копим правки, прежде чем применить. Все записи, пришедшие за это
# окно, схлопываются в одно редактирование.
DEBOUNCE_SECONDS = 7.0

# Пауза между правками разных мероприятий: держимся ниже «одно сообщение
# в секунду в один чат».
BETWEEN_EDITS_SECONDS = 1.0

_pending: set[int] = set()
_worker: asyncio.Task | None = None


class AnnounceError(Exception):
    """Анонс отправить не удалось — текст исключения показываем админу."""


async def _load_roster(session: AsyncSession, event: Event):
    """Состав для анонса — только если админ разрешил его показывать."""
    if not event.show_roster:
        return None
    from app.services import events as events_service

    return await events_service.participants(session, event.id)


async def _render(session: AsyncSession, event: Event) -> str:
    from app.services import events as events_service

    taken = await events_service.seats_taken(session, event.id)
    roster = await _load_roster(session, event)
    return render_announcement(event, taken=taken, roster=roster)


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
            # Форум-супергруппа: без этого сообщение уйдёт в General.
            message_thread_id=settings.announce_thread_id,
            text=await _render(session, event),
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


async def refresh(bot: Bot, session: AsyncSession, event: Event) -> None:
    """Перерисовывает опубликованный анонс. Ошибки только логируем.

    Правка анонса — не критичный путь: если сообщение удалили или бота
    выгнали из чата, запись участников от этого ломаться не должна.
    Редактирование не требует message_thread_id — сообщение правится по
    своему id и остаётся в том топике, где было опубликовано.
    """
    if event.announce_message_id is None or event.announce_chat_id is None:
        return

    text = await _render(session, event)
    try:
        await bot.edit_message_text(
            chat_id=event.announce_chat_id,
            message_id=event.announce_message_id,
            text=text,
            disable_web_page_preview=True,
        )
    except TelegramRetryAfter as error:
        # Упёрлись в лимит частоты: ждём ровно столько, сколько сказали,
        # и пробуем один раз. Участник в этот момент уже записан — ждёт
        # только текст в чате.
        logger.info("Анонс %s: лимит частоты, жду %s с", event.id, error.retry_after)
        await asyncio.sleep(error.retry_after)
        with contextlib.suppress(TelegramAPIError):
            await bot.edit_message_text(
                chat_id=event.announce_chat_id,
                message_id=event.announce_message_id,
                text=text,
                disable_web_page_preview=True,
            )
    except TelegramAPIError as error:
        # "message is not modified" — тоже сюда, и это нормально.
        logger.info("Анонс мероприятия %s не обновлён: %s", event.id, error)


def schedule_refresh(bot: Bot, event_id: int) -> None:
    """Просит обновить анонс — не сразу, а вместе с соседними правками.

    Возврат мгновенный: запись участника не должна ждать Telegram.
    """
    _pending.add(event_id)

    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_drain(bot))


async def _drain(bot: Bot) -> None:
    """Пока есть накопленные правки — применяет их пачками."""
    while _pending:
        await asyncio.sleep(DEBOUNCE_SECONDS)
        batch = sorted(_pending)
        _pending.clear()

        for event_id in batch:
            try:
                async with session_factory() as session:
                    event = await session.get(Event, event_id)
                    if event is not None:
                        await refresh(bot, session, event)
            except Exception:  # noqa: BLE001 — воркер не должен умирать
                logger.exception("Не удалось обновить анонс мероприятия %s", event_id)
            await asyncio.sleep(BETWEEN_EDITS_SECONDS)


async def notify_user(bot: Bot, user_id: int, text: str, **kwargs) -> bool:
    """Личное сообщение участнику. False — если доставить не удалось."""
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
    """Рассылка списку участников. Возвращает число доставленных.

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
