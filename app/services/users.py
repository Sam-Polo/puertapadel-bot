"""Операции с пользователями."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Registration, RegistrationStatus, User


async def get_or_create(session: AsyncSession, tg_user) -> User:
    """Находит пользователя по Telegram ID или заводит «пустого».

    Пустой = запись есть, но registered_at пуст: воронку регистрации он ещё
    не прошёл. Данные из Telegram обновляем на каждом заходе — люди меняют
    username, и по устаревшему их потом не найти.
    """
    user = await session.get(User, tg_user.id)
    if user is None:
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            tg_first_name=tg_user.first_name,
            tg_last_name=tg_user.last_name,
        )
        session.add(user)
        await session.commit()
        return user

    changed = (
        user.username != tg_user.username
        or user.tg_first_name != tg_user.first_name
        or user.tg_last_name != tg_user.last_name
    )
    if changed:
        user.username = tg_user.username
        user.tg_first_name = tg_user.first_name
        user.tg_last_name = tg_user.last_name
        await session.commit()
    return user


async def complete_registration(
    session: AsyncSession,
    user: User,
    *,
    first_name: str,
    last_name: str,
    gender: str,
    level: float | None,
    agreement_accepted_at: dt.datetime,
) -> User:
    user.first_name = first_name
    user.last_name = last_name
    user.gender = gender  # type: ignore[assignment]
    user.level = level
    user.agreement_accepted_at = agreement_accepted_at
    if user.registered_at is None:
        user.registered_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    await session.commit()
    return user


async def count_registrations(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count()).where(
        Registration.user_id == user_id,
        Registration.status == RegistrationStatus.ACTIVE,
    )
    return int((await session.execute(stmt)).scalar_one())


async def list_registered(
    session: AsyncSession, *, offset: int = 0, limit: int = 10
) -> list[User]:
    stmt = (
        select(User)
        .where(User.registered_at.is_not(None))
        .order_by(User.registered_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def count_registered(session: AsyncSession) -> int:
    stmt = select(func.count()).select_from(User).where(User.registered_at.is_not(None))
    return int((await session.execute(stmt)).scalar_one())


async def set_blocked(session: AsyncSession, user: User, *, blocked: bool) -> None:
    user.is_blocked = blocked
    await session.commit()
