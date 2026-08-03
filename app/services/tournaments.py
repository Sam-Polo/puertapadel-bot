"""Операции с турнирами и записями на них."""

from __future__ import annotations

import asyncio
import datetime as dt
import enum
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Registration,
    RegistrationStatus,
    Tournament,
    TournamentStatus,
    User,
)
from app.utils.dates import now

# Запись «посмотреть свободные места → занять» состоит из двух шагов, между
# которыми легко проскочить второму игроку. Бот однопроцессный, поэтому
# достаточно блокировки на турнир в памяти.
_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


class SignupResult(enum.StrEnum):
    OK = "ok"
    ALREADY = "already"
    NO_SLOTS = "no_slots"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    PASSED = "passed"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


async def get(session: AsyncSession, tournament_id: int) -> Tournament | None:
    return await session.get(Tournament, tournament_id)


async def count_active(session: AsyncSession, tournament_id: int) -> int:
    stmt = select(func.count()).where(
        Registration.tournament_id == tournament_id,
        Registration.status == RegistrationStatus.ACTIVE,
    )
    return int((await session.execute(stmt)).scalar_one())


async def count_paid(session: AsyncSession, tournament_id: int) -> int:
    stmt = select(func.count()).where(
        Registration.tournament_id == tournament_id,
        Registration.status == RegistrationStatus.ACTIVE,
        Registration.is_paid.is_(True),
    )
    return int((await session.execute(stmt)).scalar_one())


async def counters(session: AsyncSession, tournament_ids: list[int]) -> dict[int, int]:
    """Число активных записей сразу по списку турниров — чтобы не делать N запросов."""
    if not tournament_ids:
        return {}
    stmt = (
        select(Registration.tournament_id, func.count())
        .where(
            Registration.tournament_id.in_(tournament_ids),
            Registration.status == RegistrationStatus.ACTIVE,
        )
        .group_by(Registration.tournament_id)
    )
    rows = (await session.execute(stmt)).all()
    result = dict.fromkeys(tournament_ids, 0)
    result.update({tid: int(count) for tid, count in rows})
    return result


async def list_open_for_players(session: AsyncSession) -> list[Tournament]:
    """Турниры, которые игрок видит в списке: публичные, не отменённые, не прошедшие."""
    stmt = (
        select(Tournament)
        .where(
            Tournament.is_public.is_(True),
            Tournament.status.in_([TournamentStatus.OPEN, TournamentStatus.CLOSED]),
            Tournament.date >= now().date(),
        )
        .order_by(Tournament.date, Tournament.time_start)
    )
    tournaments = list((await session.execute(stmt)).scalars())
    # Турнир, начавшийся сегодня и уже закончившийся, из списка убираем.
    current = now()
    return [t for t in tournaments if t.ends_at > current]


async def list_for_admin(session: AsyncSession) -> list[Tournament]:
    """Все турниры: сначала ближайшие будущие, затем прошедшие — новые сверху."""
    stmt = select(Tournament).order_by(Tournament.date.desc(), Tournament.time_start.desc())
    tournaments = list((await session.execute(stmt)).scalars())
    today = now().date()
    upcoming = sorted(
        (t for t in tournaments if t.date >= today),
        key=lambda t: (t.date, t.time_start),
    )
    past = [t for t in tournaments if t.date < today]
    return upcoming + past


async def list_for_user(session: AsyncSession, user_id: int) -> list[Tournament]:
    """Турниры, на которые игрок записан сейчас — ближайшие сверху."""
    stmt = (
        select(Tournament)
        .join(Registration, Registration.tournament_id == Tournament.id)
        .where(
            Registration.user_id == user_id,
            Registration.status == RegistrationStatus.ACTIVE,
            Tournament.status != TournamentStatus.CANCELLED,
        )
        .order_by(Tournament.date, Tournament.time_start)
    )
    tournaments = list((await session.execute(stmt)).scalars())
    current = now()
    return [t for t in tournaments if t.ends_at > current]


async def get_registration(
    session: AsyncSession, tournament_id: int, user_id: int
) -> Registration | None:
    stmt = select(Registration).where(
        Registration.tournament_id == tournament_id,
        Registration.user_id == user_id,
        Registration.status == RegistrationStatus.ACTIVE,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def signup(
    session: AsyncSession, tournament: Tournament, user: User
) -> tuple[SignupResult, int]:
    """Записывает игрока. Возвращает (результат, число занятых мест после)."""
    async with _locks[tournament.id]:
        # Пока мы ждали блокировку, соседний апдейт мог занять последнее место.
        # Его коммит не виден внутри уже открытой транзакции этой сессии
        # (SQLite отдаёт снапшот на момент первого чтения), поэтому закрываем
        # её и перечитываем турнир начисто. Именно commit, а не rollback:
        # rollback помечает протухшими все объекты сессии, включая user,
        # и первое же обращение к user.id уходит в синхронный лениный load.
        await session.commit()
        await session.refresh(tournament)

        if tournament.status is TournamentStatus.CANCELLED:
            return SignupResult.CANCELLED, await count_active(session, tournament.id)
        if tournament.ends_at <= now():
            return SignupResult.PASSED, await count_active(session, tournament.id)
        if not tournament.accepts_signups:
            return SignupResult.CLOSED, await count_active(session, tournament.id)

        stmt = select(Registration).where(
            Registration.tournament_id == tournament.id,
            Registration.user_id == user.id,
        )
        registration = (await session.execute(stmt)).scalar_one_or_none()
        if registration is not None and registration.status is RegistrationStatus.ACTIVE:
            return SignupResult.ALREADY, await count_active(session, tournament.id)

        taken = await count_active(session, tournament.id)
        if taken >= tournament.max_players:
            return SignupResult.NO_SLOTS, taken

        if registration is None:
            registration = Registration(
                tournament_id=tournament.id,
                user_id=user.id,
                status=RegistrationStatus.ACTIVE,
                registered_at=_utcnow(),
            )
            session.add(registration)
        else:
            # Повторная запись после отмены — переиспользуем строку,
            # уникальный индекс не даст создать вторую.
            registration.status = RegistrationStatus.ACTIVE
            registration.registered_at = _utcnow()
            registration.cancelled_at = None
            registration.is_paid = False

        await session.commit()
        return SignupResult.OK, taken + 1


async def cancel_signup(
    session: AsyncSession, tournament_id: int, user_id: int
) -> Registration | None:
    """Снимает запись. None, если активной записи не было."""
    async with _locks[tournament_id]:
        await session.commit()  # см. комментарий в signup()
        registration = await get_registration(session, tournament_id, user_id)
        if registration is None:
            return None
        registration.status = RegistrationStatus.CANCELLED
        registration.cancelled_at = _utcnow()
        await session.commit()
        return registration


async def participants(
    session: AsyncSession, tournament_id: int
) -> list[tuple[User, Registration]]:
    """Состав в порядке записи."""
    stmt = (
        select(Registration)
        .where(
            Registration.tournament_id == tournament_id,
            Registration.status == RegistrationStatus.ACTIVE,
        )
        .options(selectinload(Registration.user))
        .order_by(Registration.registered_at, Registration.id)
    )
    registrations = list((await session.execute(stmt)).scalars())
    return [(r.user, r) for r in registrations]


async def toggle_paid(
    session: AsyncSession, tournament_id: int, user_id: int
) -> bool | None:
    """Переключает отметку об оплате. None, если записи нет."""
    registration = await get_registration(session, tournament_id, user_id)
    if registration is None:
        return None
    registration.is_paid = not registration.is_paid
    await session.commit()
    return registration.is_paid


async def set_status(
    session: AsyncSession, tournament: Tournament, status: TournamentStatus
) -> Tournament:
    tournament.status = status
    await session.commit()
    return tournament


async def cancel_tournament(
    session: AsyncSession, tournament: Tournament
) -> list[int]:
    """Отменяет турнир и все записи. Возвращает id игроков — их надо уведомить."""
    async with _locks[tournament.id]:
        await session.commit()  # см. комментарий в signup()
        await session.refresh(tournament)
        rows = await participants(session, tournament.id)
        user_ids = [user.id for user, _ in rows]
        for _, registration in rows:
            registration.status = RegistrationStatus.CANCELLED
            registration.cancelled_at = _utcnow()
        tournament.status = TournamentStatus.CANCELLED
        await session.commit()
        return user_ids


async def recent_locations(session: AsyncSession, limit: int = 5) -> list[str]:
    """Локации из последних турниров — подсказки при создании нового."""
    stmt = (
        select(Tournament.location, func.max(Tournament.created_at).label("last_used"))
        .group_by(Tournament.location)
        .order_by(func.max(Tournament.created_at).desc())
        .limit(limit)
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


async def create(
    session: AsyncSession,
    *,
    title: str,
    location: str,
    date: dt.date,
    time_start: dt.time,
    time_end: dt.time,
    max_players: int,
    rating_text: str | None,
    is_rated: bool,
    price: int | None,
    is_public: bool,
    status: TournamentStatus,
    created_by: int,
) -> Tournament:
    tournament = Tournament(
        title=title,
        location=location,
        date=date,
        time_start=time_start,
        time_end=time_end,
        max_players=max_players,
        rating_text=rating_text,
        is_rated=is_rated,
        price=price,
        is_public=is_public,
        status=status,
        created_by=created_by,
    )
    session.add(tournament)
    await session.commit()
    return tournament
