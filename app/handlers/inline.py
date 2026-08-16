"""Inline-режим: кнопка «Отправить другу».

Зачем он нужен: t.me/share подставляет пользователю обычный текст, и
разметки там быть не может — ссылка вылезает отдельной голой строкой.
В inline-режиме текст сообщения готовит бот, поэтому ссылка живёт прямо
в словах «Записаться в боте», как в анонсе.

Требует включённого inline-режима у бота (@BotFather → /setinline).
Побочный эффект — бота можно вызвать через @username в любом чате;
на посторонние запросы отдаём список ближайших мероприятий.
"""

from __future__ import annotations

import logging
import re

from aiogram import Router
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Event
from app.services import events as events_service
from app.utils.formatting import fmt_price, fmt_when, render_share

logger = logging.getLogger(__name__)

router = Router(name="inline")

# Запрос, который подставляет кнопка: «e42».
_QUERY_RE = re.compile(r"^e(\d+)$")

# Больше десятка вариантов в списке никто не листает.
MAX_RESULTS = 10

# Свежесть против нагрузки: за полминуты состав меняется незаметно.
CACHE_TIME = 30


async def _resolve(session: AsyncSession, query: str) -> list[Event]:
    """Что показать: конкретное мероприятие или список ближайших."""
    match = _QUERY_RE.match(query.strip())
    if match:
        # По прямому запросу отдаём и скрытое: ссылку на него человек
        # получил legally — он на него записан.
        event = await events_service.get(session, int(match.group(1)))
        if event is not None and event.accepts_signups:
            return [event]
        return []

    # Пустой или посторонний запрос: только публичные и открытые.
    events = await events_service.list_open_for_players(session)
    return [e for e in events if e.accepts_signups][:MAX_RESULTS]


def _article(event: Event) -> InlineQueryResultArticle:
    settings = get_settings()
    description = fmt_when(event)
    if event.price is not None:
        description += f" · {fmt_price(event.price)}"

    return InlineQueryResultArticle(
        id=str(event.id),
        title=event.title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=render_share(event),
            parse_mode="HTML",
        ),
        # Кнопка под сообщением: другу не нужно даже жать по тексту.
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Записаться", url=settings.deep_link(event.id)
                    )
                ]
            ]
        ),
    )


@router.inline_query()
async def on_inline_query(query: InlineQuery, session: AsyncSession) -> None:
    events = await _resolve(session, query.query)
    results = [_article(event) for event in events]

    await query.answer(
        results,
        cache_time=CACHE_TIME,
        is_personal=False,
        # Показывать нечего — уводим человека в бота, а не оставляем
        # с пустым выпадающим списком.
        button=(
            None
            if results
            else InlineQueryResultsButton(text="Открыть бота", start_parameter="inline")
        ),
    )
