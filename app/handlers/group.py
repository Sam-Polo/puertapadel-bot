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


def _topic_id(message: Message) -> int | None:
    """Номер темы, если сообщение написано внутри неё.

    Проверяем именно is_topic_message: message_thread_id заполняется ещё и
    у ответов в обычных группах, и такой номер темой не является. В General
    его нет вовсе — Telegram считает общую ленту «не темой».
    """
    if not message.is_topic_message:
        return None
    return message.message_thread_id


def _chat_id_text(message: Message) -> str:
    thread_id = _topic_id(message)
    if thread_id is None:
        return texts.CHAT_ID_NO_TOPIC.format(chat_id=message.chat.id)

    topic = (
        message.reply_to_message.forum_topic_created.name
        if message.reply_to_message and message.reply_to_message.forum_topic_created
        else "текущая"
    )
    return texts.CHAT_ID_IN_TOPIC.format(
        topic=topic, chat_id=message.chat.id, thread_id=thread_id
    )


def _may_ask_chat_id(message: Message) -> bool:
    """Кому отвечаем на /chatid.

    Обычно — администраторам бота из .env. Но админ группы может писать
    анонимно, «от имени группы»: тогда Telegram подменяет отправителя на
    служебного @GroupAnonymousBot, и сверять его с ADMIN_IDS бессмысленно.
    В этом случае опираемся на sender_chat: писать от имени группы могут
    только её администраторы.
    """
    if message.sender_chat is not None:
        return message.sender_chat.id == message.chat.id

    settings = get_settings()
    return message.from_user is not None and settings.is_admin(message.from_user.id)


@router.message(Command("chatid"))
async def cmd_chat_id(message: Message) -> None:
    """Отвечает только администраторам, чтобы не мусорить в общем чате."""
    if not _may_ask_chat_id(message):
        # Молчим в чат, но оставляем след: иначе «бот не отвечает»
        # невозможно отличить от «бот не получил сообщение».
        logger.info(
            "/chatid отклонён: chat=%s from=%s sender_chat=%s",
            message.chat.id,
            message.from_user.id if message.from_user else None,
            message.sender_chat.id if message.sender_chat else None,
        )
        return
    logger.info(
        "/chatid в чате %s: thread=%s, is_topic=%s, forum=%s",
        message.chat.id,
        message.message_thread_id,
        message.is_topic_message,
        message.chat.is_forum,
    )
    await message.reply(_chat_id_text(message))


@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> MEMBER))
async def on_added_to_chat(event: ChatMemberUpdated, bot: Bot) -> None:
    """Бота добавили в группу — сразу шлём админам готовые строки для .env."""
    if event.chat.type not in GROUP_TYPES:
        return

    settings = get_settings()
    # Тему тут узнать неоткуда: добавление в чат к теме не привязано,
    # поэтому подсказываем общий id и как получить номер темы.
    text = (
        f"🤖 Меня добавили в чат «{event.chat.title}».\n\n"
        + texts.CHAT_ID_NO_TOPIC.format(chat_id=event.chat.id)
    )
    logger.info("Бот добавлен в чат %s (%s)", event.chat.id, event.chat.title)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as error:  # noqa: BLE001 — уведомление не критично
            logger.info("Не смог сообщить админу %s: %s", admin_id, error)
