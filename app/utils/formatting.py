"""Рендер карточек мероприятия.

Три адресата, три набора полей:
  * анонс в чат      — без стоимости и без состава;
  * карточка гостю   — со стоимостью и его собственным статусом;
  * карточка админу  — всё плюс счётчики и служебные пометки.

Пропущенные при создании поля не рендерятся вовсе: строки «Локация: не
указана» в карточке быть не должно. Описание, если оно есть, идёт
последним и отделяется от остальных полей пустой строкой.
"""

from __future__ import annotations

from html import escape

from app.config import get_settings
from app.db.models import Event, EventStatus, Registration, User
from app.utils.dates import fmt_date, fmt_date_short, fmt_time

STATUS_LABEL = {
    EventStatus.DRAFT: "📝 Черновик",
    EventStatus.OPEN: "🎮 Статус: Идёт набор участников",
    EventStatus.CLOSED: "🎮 Статус: Набор участников закончен",
    EventStatus.CANCELLED: "🚫 Статус: Мероприятие отменено",
}

# Короткая подпись статуса — для кнопок и списков.
STATUS_BADGE = {
    EventStatus.DRAFT: "📝",
    EventStatus.OPEN: "🟢",
    EventStatus.CLOSED: "🔴",
    EventStatus.CANCELLED: "🚫",
}

LEVEL_MIN = 0.0
LEVEL_MAX = 7.0


def q(value: object) -> str:
    """Экранирование под parse_mode=HTML."""
    return escape(str(value), quote=False)


def parse_level(raw: str) -> float | None:
    """«3.40», «3,5», «3» -> float. None, если это не уровень.

    Запятую принимаем наравне с точкой: на мобильной клавиатуре она ближе.
    """
    candidate = raw.strip().replace(",", ".")
    try:
        value = float(candidate)
    except ValueError:
        return None
    if not (LEVEL_MIN <= value <= LEVEL_MAX):
        return None
    return round(value, 2)


def fmt_level(level: float | None) -> str:
    """Уровень всегда с двумя знаками — как принято в падел-рейтингах."""
    if level is None:
        return "не указан"
    return f"{level:.2f}"


def fmt_price(price: int | None) -> str:
    if price is None:
        return "не указана"
    if price == 0:
        return "бесплатно"
    return f"{price:,} ₽".replace(",", " ")


def fmt_when(event: Event) -> str:
    """«19.04.2025 Время: 11:00 - 13:30» либо без конца, если его не задали."""
    when = f"{fmt_date(event.date)} <b>Время:</b> {fmt_time(event.time_start)}"
    if event.time_end is not None:
        when += f" - {fmt_time(event.time_end)}"
    return when


def fmt_when_short(event: Event) -> str:
    """Хвост «, 11:00 - 13:30» для однострочных сообщений."""
    tail = f", {fmt_time(event.time_start)}"
    if event.time_end is not None:
        tail += f" - {fmt_time(event.time_end)}"
    return tail


def _core_lines(event: Event) -> list[str]:
    """Строки, общие для всех вариантов карточки."""
    lines = [
        f"❗️ <b>Название:</b> {q(event.title)}",
        STATUS_LABEL[event.status],
    ]
    if event.rating_text:
        lines.append(f"1️⃣ Рейтинг {q(event.rating_text)}")
    if event.location:
        lines.append(f"📍 <b>Локация:</b> {q(event.location)}")
    if event.is_rated is not None:
        lines.append("📈 Рейтинговое" if event.is_rated else "📈 Не рейтинговое")
    lines.append("✅ Видно всем" if event.is_public else "🙈 Скрытое (только по ссылке)")
    if event.max_players is not None:
        lines.append(f"🎾 Количество мест: {event.max_players}")
    lines.append(f"📅 <b>Дата:</b> {fmt_when(event)}")
    return lines


def _with_description(lines: list[str], event: Event) -> list[str]:
    """Описание — отдельным абзацем в самом конце."""
    if event.description:
        lines.append("")
        lines.append(q(event.description))
    return lines


def render_announcement(event: Event) -> str:
    """Текст анонса для группового чата. Без стоимости и без состава."""
    settings = get_settings()
    lines = _with_description(_core_lines(event), event)
    lines.append("")
    lines.append(f'👉 <a href="{settings.deep_link(event.id)}">Записаться в боте</a>')
    return "\n".join(lines)


def render_for_player(
    event: Event,
    *,
    taken: int,
    my_registration: Registration | None,
) -> str:
    """Карточка внутри бота: + стоимость, места и мой статус."""
    lines = _core_lines(event)
    lines.append(f"💰 <b>Стоимость:</b> {fmt_price(event.price)}")

    if event.max_players is None:
        lines.append(f"👥 Записано: {taken}")
    elif event.status is EventStatus.OPEN:
        free = max(event.max_players - taken, 0)
        lines.append(f"👥 Свободных мест: {free} из {event.max_players}")
    else:
        lines.append(f"👥 Записано: {taken} из {event.max_players}")

    lines = _with_description(lines, event)

    if my_registration is not None:
        lines.append("")
        if my_registration.seats > 1 and my_registration.partner_name:
            lines.append(
                f"✅ <b>Вы записаны вместе с {q(my_registration.partner_name)}</b> "
                f"({my_registration.seats} места)."
            )
        else:
            lines.append("✅ <b>Вы записаны.</b>")
    return "\n".join(lines)


def render_for_admin(event: Event, *, taken: int, paid: int) -> str:
    """Карточка мероприятия в админке."""
    lines = _core_lines(event)
    lines.append(f"💰 <b>Стоимость:</b> {fmt_price(event.price)}")
    limit = f" из {event.max_players}" if event.max_players is not None else " (без лимита)"
    lines.append(f"👥 Занято мест: {taken}{limit} • оплатили: {paid}")
    lines = _with_description(lines, event)
    if event.announce_message_id:
        lines.append("")
        lines.append("📢 Анонс опубликован")
    return "\n".join(lines)


def render_preview(event: Event) -> str:
    """Предпросмотр перед публикацией — то же, что увидит админ в карточке."""
    lines = _core_lines(event)
    lines.append(f"💰 <b>Стоимость:</b> {fmt_price(event.price)}")
    return "\n".join(_with_description(lines, event))


def event_button_label(event: Event, *, taken: int | None = None) -> str:
    """Подпись в списке: «🟢 19.04 (СБ) 11:00 — Женский Friendsday [5/8]»."""
    badge = STATUS_BADGE[event.status]
    counter = ""
    if taken is not None:
        counter = (
            f" [{taken}/{event.max_players}]"
            if event.max_players is not None
            else f" [{taken}]"
        )
    return (
        f"{badge} {fmt_date_short(event.date)} {fmt_time(event.time_start)}"
        f" — {event.title}{counter}"
    )


def user_line(
    user: User,
    *,
    index: int | None = None,
    paid: bool | None = None,
    seats: int = 1,
) -> str:
    """Строка участника в составе — для админских списков."""
    prefix = f"{index}. " if index is not None else ""
    name = q(user.full_name)
    link = f'<a href="tg://user?id={user.id}">{name}</a>'
    extras = []
    if user.level is not None:
        extras.append(fmt_level(user.level))
    if user.username:
        extras.append(f"@{q(user.username)}")
    tail = f" ({', '.join(extras)})" if extras else ""
    money = ""
    if paid is not None:
        money = " 💰" if paid else " ⏳"
    seats_mark = f" ×{seats}" if seats > 1 else ""
    return f"{prefix}{link}{tail}{seats_mark}{money}"


def partner_line(registration: Registration, *, index: int) -> str:
    """Строка напарника — идёт следом за строкой того, кто его записал."""
    owner = q(registration.user.full_name)
    return f"{index}. ↳ {q(registration.partner_name)} — гость ({owner})"
