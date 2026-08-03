"""Админка: база пользователей."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.models import User
from app.filters import IsAdmin
from app.keyboards.admin import admin_user_kb, admin_users_kb, back_to_admin_kb
from app.keyboards.callbacks import AdminCB
from app.services import users as users_service
from app.utils.dates import fmt_date
from app.utils.formatting import q
from app.utils.tg import edit_or_send

logger = logging.getLogger(__name__)

router = Router(name="admin_users")
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 10


async def show_users(
    callback: CallbackQuery, session: AsyncSession, *, page: int = 0
) -> None:
    total = await users_service.count_registered(session)
    if total == 0:
        await edit_or_send(callback, texts.ADMIN_USERS_EMPTY, back_to_admin_kb())
        return

    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = max(0, min(page, total_pages - 1))
    items = await users_service.list_registered(
        session, offset=page * PAGE_SIZE, limit=PAGE_SIZE
    )
    await edit_or_send(
        callback,
        texts.ADMIN_USERS.format(total=total),
        admin_users_kb(items, page=page, total_pages=total_pages),
    )


@router.callback_query(AdminCB.filter(F.action == "users"))
async def on_users(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    await callback.answer()
    await show_users(callback, session, page=callback_data.page)


async def show_user(
    callback: CallbackQuery, session: AsyncSession, target: User, *, page: int
) -> None:
    """Карточка игрока. Callback здесь уже отвечен вызывающим."""
    count = await users_service.count_registrations(session, target.id)
    tg = f"@{q(target.username)}" if target.username else "—"
    await edit_or_send(
        callback,
        texts.ADMIN_USER_CARD.format(
            full_name=q(target.full_name),
            tg=tg,
            id=target.id,
            gender=texts.GENDER_LABEL.get(str(target.gender), "—"),
            age=target.age or "—",
            registered_at=(
                fmt_date(target.registered_at.date()) if target.registered_at else "—"
            ),
            tournaments=count,
        )
        + ("\n\n🔒 <b>Заблокирован</b>" if target.is_blocked else ""),
        admin_user_kb(target, page=page),
    )


@router.callback_query(AdminCB.filter(F.action == "user"))
async def on_user(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    target = await session.get(User, callback_data.id)
    if target is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await callback.answer()
    await show_user(callback, session, target, page=callback_data.page)


@router.callback_query(AdminCB.filter(F.action == "block"))
async def on_block(
    callback: CallbackQuery, callback_data: AdminCB, session: AsyncSession
) -> None:
    target = await session.get(User, callback_data.id)
    if target is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    await users_service.set_blocked(session, target, blocked=not target.is_blocked)
    await callback.answer("Заблокирован" if target.is_blocked else "Разблокирован")
    logger.info(
        "Админ %s %s пользователя %s",
        callback.from_user.id,
        "заблокировал" if target.is_blocked else "разблокировал",
        target.id,
    )
    await show_user(callback, session, target, page=callback_data.page)
