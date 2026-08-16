"""Рендер карточек мероприятия.

Три адресата, три набора полей:
  * анонс в чат      — параметры, стоимость и ссылка, без состава;
  * карточка гостю   — то же плюс состав (если админ его не скрыл);
  * карточка админу  — всё плюс видимость, счётчики и служебные пометки.

Пропущенные при создании поля не рендерятся вовсе: строки «Локация: не
указана» в карточке быть не должно. Описание, если оно есть, идёт
последним отдельным абзацем моноширинным блоком.
"""

from __future__ import annotations

from html import escape

from app.config import get_settings
from app.db.models import Event, EventStatus, Registration, User
from app.utils.dates import fmt_date_short, fmt_time

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

# Жёсткий лимит Telegram на длину текстового сообщения.
TELEGRAM_MESSAGE_LIMIT = 4096


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
    """Дата и время одной короткой строкой: «19.04 (СБ) · 11:00–13:30»."""
    when = f"{fmt_date_short(event.date)} · {fmt_time(event.time_start)}"
    if event.time_end is not None:
        when += f"–{fmt_time(event.time_end)}"
    return when


def fmt_capacity(event: Event, taken: int) -> str:
    """Занятость в тех единицах, в которых люди о ней думают."""
    if event.is_doubles:
        pairs = taken // 2
        if event.max_pairs is None:
            return f"{pairs} пар"
        return f"{pairs} из {event.max_pairs} пар"
    if event.max_players is None:
        return f"{taken}"
    return f"{taken} из {event.max_players}"


def format_label(event: Event) -> str:
    return "👥 Парное (записываются вдвоём)" if event.is_doubles else "👤 Одиночное"


def is_full(event: Event, taken: int) -> bool:
    return event.max_players is not None and taken >= event.max_players


def status_line(event: Event, taken: int | None = None) -> str:
    """Статус для показа.

    Статус в БД — намерение админа (набор идёт / закрыт / отменено), а
    заполненность меняется сама и в обе стороны. Поэтому забитое
    мероприятие мы не «закрываем», а показываем как «мест нет»: стоит
    кому-то отмениться — надпись сама вернётся к «идёт набор».
    """
    if (
        taken is not None
        and event.status is EventStatus.OPEN
        and is_full(event, taken)
    ):
        return "🔴 Статус: Мест нет"
    return STATUS_LABEL[event.status]


def status_badge(event: Event, taken: int | None = None) -> str:
    if (
        taken is not None
        and event.status is EventStatus.OPEN
        and is_full(event, taken)
    ):
        return "🔴"
    return STATUS_BADGE[event.status]


def _core_lines(event: Event, taken: int | None = None) -> list[str]:
    """Строки, общие для всех вариантов карточки."""
    lines = [
        f"❗️ <b>{q(event.title)}</b>",
        status_line(event, taken),
        format_label(event),
    ]
    if event.rating_text:
        lines.append(f"1️⃣ Рейтинг {q(event.rating_text)}")
    if event.location:
        lines.append(f"📍 <b>Локация:</b> {q(event.location)}")
    if event.is_rated is not None:
        lines.append("📈 Рейтинговое" if event.is_rated else "📈 Не рейтинговое")
    if event.max_players is not None:
        capacity = (
            f"{event.max_pairs} пар" if event.is_doubles else str(event.max_players)
        )
        lines.append(f"🎾 Количество мест: {capacity}")
    lines.append(f"💰 <b>Стоимость:</b> {fmt_price(event.price)}")
    lines.append(f"📅 <b>Когда:</b> {fmt_when(event)}")
    return lines


def _with_description(lines: list[str], event: Event) -> list[str]:
    """Описание — отдельным абзацем в конце, цитатой под заголовком.

    blockquote, а не code: цитата переносит длинные строки по словам и
    не превращает текст в моноширинную простыню с горизонтальным
    скроллом на узких экранах.
    """
    if event.description:
        lines.append("")
        lines.append("<b>Регламент:</b>")
        lines.append(f"<blockquote>{q(event.description)}</blockquote>")
    return lines


def render_announcement(
    event: Event,
    *,
    taken: int = 0,
    roster: list[tuple[User, Registration]] | None = None,
) -> str:
    """Текст анонса для группового чата. Без пометки о видимости.

    Состав показываем, если админ его не скрыл. Длина сообщения в
    Telegram ограничена 4096 символами, а описание может занять половину
    этого запаса — поэтому список при необходимости подрезаем.
    """
    settings = get_settings()
    lines = _core_lines(event, taken)
    lines.append(f"👥 Записано: {fmt_capacity(event, taken)}")
    lines = _with_description(lines, event)

    footer = ["", f'👉 <a href="{settings.deep_link(event.id)}">Записаться в боте</a>']

    if event.show_roster and roster:
        roster_lines = render_roster(roster, is_doubles=event.is_doubles)
        head = "\n".join([*lines, "", "<b>Состав:</b>"])
        tail = "\n".join(footer)
        lines = _fit_roster(head, roster_lines, tail)
        return "\n".join([lines, tail])

    return "\n".join([*lines, *footer])


def _fit_roster(head: str, roster_lines: list[str], tail: str) -> str:
    """Складывает шапку и состав так, чтобы влезть в лимит сообщения."""
    budget = TELEGRAM_MESSAGE_LIMIT - len(head) - len(tail) - 32
    kept: list[str] = []
    used = 0
    for line in roster_lines:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1

    result = "\n".join([head, *kept])
    hidden = len(roster_lines) - len(kept)
    if hidden > 0:
        result += f"\n… и ещё {hidden} — весь список в боте"
    return result


def render_roster(
    rows: list[tuple[User, Registration]], *, is_doubles: bool
) -> list[str]:
    """Состав для участников: только имена, без контактов и уровней.

    Пара идёт одной строкой через «+» — так она читается как одна
    единица, и список из двенадцати пар помещается на экран.
    """
    lines = []
    for index, (user, registration) in enumerate(rows, start=1):
        if is_doubles and registration.partner_name:
            lines.append(
                f"{index}. {q(user.full_name)} + {q(registration.partner_name)}"
            )
        else:
            lines.append(f"{index}. {q(user.full_name)}")
    return lines


def render_for_player(
    event: Event,
    *,
    taken: int,
    my_registration: Registration | None,
    roster: list[tuple[User, Registration]] | None = None,
) -> str:
    """Карточка внутри бота: + занятость, состав и мой статус."""
    lines = _core_lines(event, taken)

    if event.max_players is None:
        lines.append(f"👥 Записано: {fmt_capacity(event, taken)}")
    elif event.status is EventStatus.OPEN:
        free = max(event.max_players - taken, 0)
        unit = "пар" if event.is_doubles else "мест"
        free_units = free // 2 if event.is_doubles else free
        lines.append(
            f"👥 Свободно {unit}: {free_units} · занято {fmt_capacity(event, taken)}"
        )
    else:
        lines.append(f"👥 Записано: {fmt_capacity(event, taken)}")

    lines = _with_description(lines, event)

    if event.show_roster and roster:
        lines.append("")
        lines.append("<b>Состав:</b>")
        lines.extend(render_roster(roster, is_doubles=event.is_doubles))

    if my_registration is not None:
        lines.append("")
        if event.is_doubles and my_registration.partner_name:
            lines.append(
                f"✅ <b>Вы записаны вместе с {q(my_registration.partner_name)}.</b>"
            )
        else:
            lines.append("✅ <b>Вы записаны.</b>")
    return "\n".join(lines)


def render_share(event: Event) -> str:
    """Сообщение, которым участник зовёт знакомых.

    Уходит через inline-режим, то есть отправляет его сам пользователь, но
    текст готовит бот — поэтому здесь доступна разметка и ссылка живёт
    прямо в словах, как в анонсе.
    """
    settings = get_settings()
    lines = [f"🎾 <b>{q(event.title)}</b>", f"📅 {fmt_when(event)}"]
    if event.location:
        lines.append(f"📍 {q(event.location)}")
    if event.price is not None:
        lines.append(f"💰 {fmt_price(event.price)}")
    lines.append("")
    lines.append(f'👉 <a href="{settings.deep_link(event.id)}">Записаться в боте</a>')
    return "\n".join(lines)


def render_for_admin(event: Event, *, taken: int, paid: int) -> str:
    """Карточка мероприятия в админке — с видимостью и служебными полями."""
    lines = _core_lines(event, taken)
    lines.append("✅ Видно всем" if event.is_public else "🙈 Скрытое (только по ссылке)")
    lines.append(
        "👁 Состав виден участникам" if event.show_roster else "🙈 Состав скрыт от участников"
    )
    lines.append(f"👥 Занято: {fmt_capacity(event, taken)} • оплатили: {paid}")
    lines = _with_description(lines, event)
    if event.announce_message_id:
        lines.append("")
        lines.append("📢 Анонс опубликован")
    return "\n".join(lines)


def render_preview(event: Event) -> str:
    """Предпросмотр перед публикацией — то же, что увидит админ в карточке."""
    lines = _core_lines(event)
    lines.append("✅ Видно всем" if event.is_public else "🙈 Скрытое (только по ссылке)")
    lines.append(
        "👁 Состав виден участникам" if event.show_roster else "🙈 Состав скрыт от участников"
    )
    return "\n".join(_with_description(lines, event))


def event_button_label(event: Event, *, taken: int | None = None) -> str:
    """Подпись в списке: «🟢 19.04 (СБ) 11:00 — Женский Friendsday [3/8 пар]»."""
    badge = status_badge(event, taken)
    counter = f" [{fmt_capacity(event, taken)}]" if taken is not None else ""
    return (
        f"{badge} {fmt_date_short(event.date)} {fmt_time(event.time_start)}"
        f" — {event.title}{counter}"
    )


def user_line(
    user: User,
    *,
    index: int | None = None,
    paid: bool | None = None,
    partner_name: str | None = None,
) -> str:
    """Строка участника в составе — для админских списков.

    Пара идёт одной строкой через «+». Отметка об оплате словами и только
    когда за участие берут деньги: `paid=None` убирает её целиком.
    """
    prefix = f"{index}. " if index is not None else ""
    name = q(user.full_name)
    link = f'<a href="tg://user?id={user.id}">{name}</a>'
    extras = []
    if user.level is not None:
        extras.append(fmt_level(user.level))
    if user.username:
        extras.append(f"@{q(user.username)}")
    tail = f" ({', '.join(extras)})" if extras else ""
    partner = f" + {q(partner_name)}" if partner_name else ""
    money = ""
    if paid is not None:
        money = " — оплачено" if paid else " — не оплачено"
    return f"{prefix}{link}{tail}{partner}{money}"
