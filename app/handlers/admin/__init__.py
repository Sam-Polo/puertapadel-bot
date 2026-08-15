"""Роутеры админки. Порядок важен: меню и /admin — раньше воронок."""

from aiogram import Router

from app.handlers.admin import create, edit, manage, menu, users

router = Router(name="admin")
router.include_router(menu.router)
router.include_router(create.router)
# edit раньше manage: обе воронки живут на AdminCB, но у edit шаги
# привязаны к состоянию, и оно должно перехватываться первым.
router.include_router(edit.router)
router.include_router(manage.router)
router.include_router(users.router)

__all__ = ["router"]
