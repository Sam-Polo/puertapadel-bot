"""Разбор и форматирование дат/времени, введённых админом."""

from __future__ import annotations

import datetime as dt
import re

from app.config import get_settings

WEEKDAY_SHORT = ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")
WEEKDAY_FULL = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)

_DATE_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?$")
_TIME_RE = re.compile(r"^(\d{1,2})[:.\-]?(\d{2})$")


def now() -> dt.datetime:
    """Текущий момент в часовом поясе клуба, без tzinfo.

    Наивный результат намеренно: в БД все игровые даты лежат в местном
    времени, и сравнивать их нужно с таким же наивным «сейчас».
    """
    return dt.datetime.now(get_settings().timezone).replace(tzinfo=None)


def today() -> dt.date:
    return now().date()


def parse_date(raw: str) -> dt.date | None:
    """«19.04.2025», «19.04», «19/4/25» -> date. None, если не разобрали.

    Год можно не указывать: подставляем текущий, а если такая дата уже
    прошла — следующий (админ заводит мероприятия вперёд, не назад).
    """
    match = _DATE_RE.match(raw.strip())
    if not match:
        return None

    day, month, year = match.group(1), match.group(2), match.group(3)
    try:
        day_i, month_i = int(day), int(month)
        if year is None:
            year_i = today().year
        else:
            year_i = int(year)
            if year_i < 100:
                year_i += 2000
        result = dt.date(year_i, month_i, day_i)
    except ValueError:
        return None

    if year is None and result < today():
        try:
            result = result.replace(year=result.year + 1)
        except ValueError:  # 29 февраля
            return None
    return result


def parse_time(raw: str) -> dt.time | None:
    """«11:00», «1100», «9:30» -> time. None, если не разобрали."""
    match = _TIME_RE.match(raw.strip())
    if not match:
        return None
    try:
        return dt.time(int(match.group(1)), int(match.group(2)))
    except ValueError:
        return None


def fmt_date(value: dt.date) -> str:
    return value.strftime("%d.%m.%Y")


def fmt_time(value: dt.time) -> str:
    return value.strftime("%H:%M")


def fmt_date_short(value: dt.date) -> str:
    return f"{value.strftime('%d.%m')} ({WEEKDAY_SHORT[value.weekday()]})"


