"""Всё, что бот делает в групповых чатах.

Единственная задача — помочь настроить анонсы: сказать chat_id (и id топика,
если это форум-супергруппа). Никакой другой активности в группе у бота нет.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import IS_NOT_MEMBER, MEMBER, ChatMemberUpdatedFilter, Command
from aiogram.types import ChatMemberUpdated, Message

from app import texts
from app.config import get_settings

logger = logging.getLogger(__name__)

router = Router(name="group")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

GROUP_TYPES = {"group", "supergroup"}


def _chat_id_text(chat_id: int, thread_id: int | None) -> str:
    thread = (
        f"<code>ANNOUNCE_THREAD_ID={thread_id}</code>\n" if thread_id is not None else ""
    )
    return texts.CHAT_ID_INFO.format(chat_id=chat_id, thread=thread)


@router.message(Command("chatid"))
async def cmd_chat_id(message: Message) -> None:
    """Отвечает только администраторам бота, чтобы не мусорить в общем чате."""
    settings = get_settings()
    if message.from_user is None or not settings.is_admin(message.from_user.id):
        return
    await message.reply(_chat_id_text(message.chat.id, message.message_thread_id))


@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> MEMBER))
async def on_added_to_chat(event: ChatMemberUpdated, bot: Bot) -> None:
    """Бота добавили в группу — сразу шлём админам готовые строки для .env."""
    if event.chat.type not in GROUP_TYPES:
        return

    settings = get_settings()
    text = (
        f"🤖 Меня добавили в чат «{event.chat.title}».\n\n"
        + _chat_id_text(event.chat.id, None)
    )
    logger.info("Бот добавлен в чат %s (%s)", event.chat.id, event.chat.title)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as error:  # noqa: BLE001 — уведомление не критично
            logger.info("Не смог сообщить админу %s: %s", admin_id, error)
