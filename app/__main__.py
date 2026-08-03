"""Точка входа: собирает диспетчер и запускает long polling."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from app.config import get_settings
from app.db.session import check_connection
from app.handlers import build_router
from app.middlewares import (
    DbSessionMiddleware,
    RegistrationGateMiddleware,
    ThrottlingMiddleware,
    UserMiddleware,
)

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # aiogram на DEBUG печатает каждый апдейт целиком — шумно даже при отладке.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def setup_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())

    # Внешние middleware — до фильтров: троттлинг должен отсекать флуд раньше,
    # чем мы полезем в БД, а user/gate должны отработать до любого хендлера.
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.outer_middleware(ThrottlingMiddleware())
        observer.outer_middleware(DbSessionMiddleware())
        observer.outer_middleware(UserMiddleware())
        observer.outer_middleware(RegistrationGateMiddleware())

    # my_chat_member обрабатывается без пользовательского контекста.
    dispatcher.my_chat_member.outer_middleware(DbSessionMiddleware())

    dispatcher.include_router(build_router())
    return dispatcher


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Меню бота"),
            BotCommand(command="help", description="Помощь"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    # Каталог для файла БД может не существовать при первом запуске.
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    await check_connection()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = setup_dispatcher()

    me = await bot.get_me()
    if me.username and me.username.lower() != settings.bot_username.lower():
        logger.warning(
            "BOT_USERNAME=%s не совпадает с реальным @%s — ссылки в анонсах будут "
            "вести не туда. Поправьте .env.",
            settings.bot_username,
            me.username,
        )

    await set_commands(bot)
    logger.info(
        "Бот @%s запущен. Админов: %s. Анонсы: %s",
        me.username,
        len(settings.admin_ids),
        settings.announce_chat_id or "выключены",
    )

    try:
        # Апдейты, накопившиеся пока бот лежал, обрабатывать не нужно:
        # это чужие нажатия по кнопкам, которых уже нет на экране.
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Остановлен")
