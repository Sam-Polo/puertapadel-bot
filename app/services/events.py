"""Операции с мероприятиями и записями на них.

Занятость считается суммой `seats`, а не числом строк: одна запись может
занимать два места, когда человек записался и за напарника.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import enum
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Event,
    EventStatus,
    Registration,
    RegistrationStatus,
    User,
)
from app.utils.dates import now

# Запись «посмотреть свободные места → занять» состоит из двух шагов, между
# которыми легко проскочить второму участнику. Бот однопроцессный, поэтому
# достаточно блокировки на мероприятие в памяти.
_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


class SignupResult(enum.StrEnum):
    OK = "ok"
    ALREADY = "already"
    NO_SLOTS = "no_slots"
    NO_SLOTS_FOR_TWO = "no_slots_for_two"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    PASSED = "passed"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


async def get(session: AsyncSession, event_id: int) -> Event | None:
    return await session.get(Event, event_id)


async def seats_taken(session: AsyncSession, event_id: int) -> int:
    """Сколько мест занято: сумма seats по активным записям."""
    stmt = select(func.coalesce(func.sum(Registration.seats), 0)).where(
        Registration.event_id == event_id,
        Registration.status == RegistrationStatus.ACTIVE,
    )
    return int((await session.execute(stmt)).scalar_one())


async def count_paid(session: AsyncSession, event_id: int) -> int:
    stmt = select(func.count()).where(
        Registration.event_id == event_id,
        Registration.status == RegistrationStatus.ACTIVE,
        Registration.is_paid.is_(True),
    )
    return int((await session.execute(stmt)).scalar_one())


async def counters(session: AsyncSession, event_ids: list[int]) -> dict[int, int]:
    """Занятые места сразу по списку мероприятий — чтобы не делать N запросов."""
    if not event_ids:
        return {}
    stmt = (
        select(Registration.event_id, func.coalesce(func.sum(Registration.seats), 0))
        .where(
            Registration.event_id.in_(event_ids),
            Registration.status == RegistrationStatus.ACTIVE,
        )
        .group_by(Registration.event_id)
    )
    rows = (await session.execute(stmt)).all()
    result = dict.fromkeys(event_ids, 0)
    result.update({event_id: int(count) for event_id, count in rows})
    return result


async def list_open_for_players(session: AsyncSession) -> list[Event]:
    """Мероприятия, видимые участнику: публичные, не отменённые, не прошедшие."""
    stmt = (
        select(Event)
        .where(
            Event.is_public.is_(True),
            Event.status.in_([EventStatus.OPEN, EventStatus.CLOSED]),
            Event.date >= now().date(),
        )
        .order_by(Event.date, Event.time_start)
    )
    events = list((await session.execute(stmt)).scalars())
    # Начавшееся сегодня и уже закончившееся из списка убираем.
    current = now()
    return [e for e in events if e.ends_at > current]


async def list_for_admin(session: AsyncSession) -> list[Event]:
    """Все мероприятия: сначала ближайшие будущие, затем прошедшие."""
    stmt = select(Event).order_by(Event.date.desc(), Event.time_start.desc())
    events = list((await session.execute(stmt)).scalars())
    today = now().date()
    upcoming = sorted(
        (e for e in events if e.date >= today), key=lambda e: (e.date, e.time_start)
    )
    past = [e for e in events if e.date < today]
    return upcoming + past


async def list_for_user(session: AsyncSession, user_id: int) -> list[Event]:
    """Мероприятия, на которые участник записан сейчас — ближайшие сверху."""
    stmt = (
        select(Event)
        .join(Registration, Registration.event_id == Event.id)
        .where(
            Registration.user_id == user_id,
            Registration.status == RegistrationStatus.ACTIVE,
            Event.status != EventStatus.CANCELLED,
        )
        .order_by(Event.date, Event.time_start)
    )
    events = list((await session.execute(stmt)).scalars())
    current = now()
    return [e for e in events if e.ends_at > current]


async def get_registration(
    session: AsyncSession, event_id: int, user_id: int
) -> Registration | None:
    stmt = select(Registration).where(
        Registration.event_id == event_id,
        Registration.user_id == user_id,
        Registration.status == RegistrationStatus.ACTIVE,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def signup(
    session: AsyncSession,
    event: Event,
    user: User,
    *,
    seats: int = 1,
    partner_name: str | None = None,
) -> tuple[SignupResult, int]:
    """Записывает участника. Возвращает (результат, занято мест после)."""
    async with _locks[event.id]:
        # Пока мы ждали блокировку, соседний апдейт мог занять последнее место.
        # Его коммит не виден внутри уже открытой транзакции этой сессии
        # (SQLite отдаёт снапшот на момент первого чтения), поэтому закрываем
        # её и перечитываем мероприятие начисто. Именно commit, а не rollback:
        # rollback помечает протухшими все объекты сессии, включая user,
        # и первое же обращение к user.id уходит в синхронный ленивый load.
        await session.commit()
        await session.refresh(event)

        if event.status is EventStatus.CANCELLED:
            return SignupResult.CANCELLED, await seats_taken(session, event.id)
        if event.ends_at <= now():
            return SignupResult.PASSED, await seats_taken(session, event.id)
        if not event.accepts_signups:
            return SignupResult.CLOSED, await seats_taken(session, event.id)

        stmt = select(Registration).where(
            Registration.event_id == event.id,
            Registration.user_id == user.id,
        )
        registration = (await session.execute(stmt)).scalar_one_or_none()
        if registration is not None and registration.status is RegistrationStatus.ACTIVE:
            return SignupResult.ALREADY, await seats_taken(session, event.id)

        taken = await seats_taken(session, event.id)
        if event.max_players is not None:
            free = event.max_players - taken
            if free <= 0:
                return SignupResult.NO_SLOTS, taken
            if free < seats:
                # Места есть, но на двоих не хватает — это отдельный случай,
                # участнику предложим записаться одному.
                return SignupResult.NO_SLOTS_FOR_TWO, taken

        if registration is None:
            registration = Registration(
                event_id=event.id,
                user_id=user.id,
                status=RegistrationStatus.ACTIVE,
                seats=seats,
                partner_name=partner_name,
                registered_at=_utcnow(),
            )
            session.add(registration)
        else:
            # Повторная запись после отмены — переиспользуем строку,
            # уникальный индекс не даст создать вторую.
            registration.status = RegistrationStatus.ACTIVE
            registration.seats = seats
            registration.partner_name = partner_name
            registration.registered_at = _utcnow()
            registration.cancelled_at = None
            registration.is_paid = False

        await session.commit()
        return SignupResult.OK, taken + seats


async def cancel_signup(
    session: AsyncSession, event_id: int, user_id: int
) -> Registration | None:
    """Снимает запись целиком (оба места, если их два). None, если её не было."""
    async with _locks[event_id]:
        await session.commit()  # см. комментарий в signup()
        registration = await get_registration(session, event_id, user_id)
        if registration is None:
            return None
        registration.status = RegistrationStatus.CANCELLED
        registration.cancelled_at = _utcnow()
        await session.commit()
        return registration


async def participants(
    session: AsyncSession, event_id: int
) -> list[tuple[User, Registration]]:
    """Состав в порядке записи."""
    stmt = (
        select(Registration)
        .where(
            Registration.event_id == event_id,
            Registration.status == RegistrationStatus.ACTIVE,
        )
        .options(selectinload(Registration.user))
        .order_by(Registration.registered_at, Registration.id)
    )
    registrations = list((await session.execute(stmt)).scalars())
    return [(r.user, r) for r in registrations]


async def toggle_paid(
    session: AsyncSession, event_id: int, user_id: int
) -> bool | None:
    """Переключает отметку об оплате. None, если записи нет."""
    registration = await get_registration(session, event_id, user_id)
    if registration is None:
        return None
    registration.is_paid = not registration.is_paid
    await session.commit()
    return registration.is_paid


async def set_status(
    session: AsyncSession, event: Event, status: EventStatus
) -> Event:
    event.status = status
    await session.commit()
    return event


async def cancel_event(session: AsyncSession, event: Event) -> list[int]:
    """Отменяет мероприятие и все записи. Возвращает id тех, кого уведомить."""
    async with _locks[event.id]:
        await session.commit()  # см. комментарий в signup()
        await session.refresh(event)
        rows = await participants(session, event.id)
        user_ids = [user.id for user, _ in rows]
        for _, registration in rows:
            registration.status = RegistrationStatus.CANCELLED
            registration.cancelled_at = _utcnow()
        event.status = EventStatus.CANCELLED
        await session.commit()
        return user_ids


async def recent_locations(session: AsyncSession, limit: int = 5) -> list[str]:
    """Локации из последних мероприятий — подсказки при создании нового."""
    stmt = (
        select(Event.location, func.max(Event.created_at).label("last_used"))
        .where(Event.location.is_not(None))
        .group_by(Event.location)
        .order_by(func.max(Event.created_at).desc())
        .limit(limit)
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


async def create(
    session: AsyncSession,
    *,
    title: str,
    date: dt.date,
    time_start: dt.time,
    time_end: dt.time | None,
    location: str | None,
    max_players: int | None,
    rating_text: str | None,
    is_rated: bool | None,
    price: int | None,
    description: str | None,
    is_public: bool,
    status: EventStatus,
    created_by: int,
) -> Event:
    event = Event(
        title=title,
        date=date,
        time_start=time_start,
        time_end=time_end,
        location=location,
        max_players=max_players,
        rating_text=rating_text,
        is_rated=is_rated,
        price=price,
        description=description,
        is_public=is_public,
        status=status,
        created_by=created_by,
    )
    session.add(event)
    await session.commit()
    return event
