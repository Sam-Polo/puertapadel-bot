"""Роутеры админки. Порядок важен: меню и /admin — раньше воронок."""

from aiogram import Router

from app.handlers.admin import create, manage, menu, users

router = Router(name="admin")
router.include_router(menu.router)
router.include_router(create.router)
router.include_router(manage.router)
router.include_router(users.router)

__all__ = ["router"]
