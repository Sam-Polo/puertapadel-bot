"""Рендер карточек турнира.

Три адресата, три набора полей:
  * анонс в чат      — без стоимости и без состава;
  * карточка игроку  — со стоимостью и его собственным статусом;
  * карточка админу  — всё плюс счётчики и служебные пометки.
"""

from __future__ import annotations

from html import escape

from app.config import get_settings
from app.db.models import Registration, Tournament, TournamentStatus, User
from app.utils.dates import fmt_date, fmt_date_short, fmt_time

STATUS_LABEL = {
    TournamentStatus.DRAFT: "📝 Черновик",
    TournamentStatus.OPEN: "🎮 Статус: Идёт набор игроков",
    TournamentStatus.CLOSED: "🎮 Статус: Набор игроков закончен",
    TournamentStatus.CANCELLED: "🚫 Статус: Турнир отменён",
}

# Короткая подпись статуса — для кнопок и списков.
STATUS_BADGE = {
    TournamentStatus.DRAFT: "📝",
    TournamentStatus.OPEN: "🟢",
    TournamentStatus.CLOSED: "🔴",
    TournamentStatus.CANCELLED: "🚫",
}


def q(value: object) -> str:
    """Экранирование под parse_mode=HTML."""
    return escape(str(value), quote=False)


def fmt_price(price: int | None) -> str:
    if price is None:
        return "не указана"
    if price == 0:
        return "бесплатно"
    return f"{price:,} ₽".replace(",", " ")


def _core_lines(t: Tournament) -> list[str]:
    """Строки, общие для всех вариантов карточки."""
    lines = [
        f"❗️ <b>Название:</b> {q(t.title)}",
        STATUS_LABEL[t.status],
    ]
    if t.rating_text:
        lines.append(f"1️⃣ Рейтинг {q(t.rating_text)}")
    lines.append(f"📍 <b>Локация:</b> {q(t.location)}")
    lines.append("📈 Рейтинговая" if t.is_rated else "📈 Не рейтинговая")
    lines.append("✅ Видна всем" if t.is_public else "🙈 Скрытая (только по ссылке)")
    lines.append(f"🎾 Количество игроков: {t.max_players}")
    lines.append(
        f"📅 <b>Дата:</b> {fmt_date(t.date)} "
        f"<b>Время:</b> {fmt_time(t.time_start)} - {fmt_time(t.time_end)}"
    )
    return lines


def render_announcement(t: Tournament) -> str:
    """Текст анонса для группового чата. Без стоимости и без состава."""
    settings = get_settings()
    lines = _core_lines(t)
    lines.append("")
    lines.append(f'👉 <a href="{settings.deep_link(t.id)}">Записаться в боте</a>')
    return "\n".join(lines)


def render_for_player(
    t: Tournament,
    *,
    taken: int,
    my_registration: Registration | None,
) -> str:
    """Карточка турнира внутри бота: + стоимость, места и мой статус."""
    lines = _core_lines(t)
    lines.append(f"💰 <b>Стоимость:</b> {fmt_price(t.price)}")

    free = max(t.max_players - taken, 0)
    if t.status is TournamentStatus.OPEN:
        lines.append(f"👥 Свободных мест: {free} из {t.max_players}")
    else:
        lines.append(f"👥 Записано: {taken} из {t.max_players}")

    if my_registration is not None:
        lines.append("")
        lines.append("✅ <b>Вы записаны на этот турнир.</b>")
    return "\n".join(lines)


def render_for_admin(t: Tournament, *, taken: int, paid: int) -> str:
    """Карточка турнира в админке."""
    lines = _core_lines(t)
    lines.append(f"💰 <b>Стоимость:</b> {fmt_price(t.price)}")
    lines.append(f"👥 Записано: {taken} из {t.max_players} • оплатили: {paid}")
    if t.announce_message_id:
        lines.append("📢 Анонс опубликован")
    return "\n".join(lines)


def render_preview(t: Tournament) -> str:
    """Предпросмотр перед публикацией — то же, что увидит админ в карточке."""
    lines = _core_lines(t)
    lines.append(f"💰 <b>Стоимость:</b> {fmt_price(t.price)}")
    return "\n".join(lines)


def tournament_button_label(t: Tournament, *, taken: int | None = None) -> str:
    """Подпись турнира в списке: «🟢 19.04 (СБ) 11:00 — Женский Friendsday»."""
    badge = STATUS_BADGE[t.status]
    counter = f" [{taken}/{t.max_players}]" if taken is not None else ""
    return (
        f"{badge} {fmt_date_short(t.date)} {fmt_time(t.time_start)}"
        f" — {t.title}{counter}"
    )


def user_line(user: User, *, index: int | None = None, paid: bool | None = None) -> str:
    """Строка игрока в составе — для админских списков."""
    prefix = f"{index}. " if index is not None else ""
    name = q(user.full_name)
    link = f'<a href="tg://user?id={user.id}">{name}</a>'
    extras = []
    if user.age:
        extras.append(f"{user.age} л.")
    if user.username:
        extras.append(f"@{q(user.username)}")
    tail = f" ({', '.join(extras)})" if extras else ""
    money = ""
    if paid is not None:
        money = " 💰" if paid else " ⏳"
    return f"{prefix}{link}{tail}{money}"
