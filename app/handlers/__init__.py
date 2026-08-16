"""Сборка роутеров.

Порядок принципиален:
  1. common.router   — /start, /help, /cancel перехватываются раньше, чем
                       текстовые шаги любой воронки съедят их как ввод;
  2. group.router    — групповые чаты;
  3. admin.router    — /admin и админские воронки;
  4. registration    — шаги регистрации участника;
  5. events          — списки, карточки, запись;
  6. fallback_router — всё, что никто не разобрал.
"""

from aiogram import Router

from app.handlers import admin as admin_handlers
from app.handlers import common, events, group, inline, registration


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(common.router)
    router.include_router(group.router)
    router.include_router(admin_handlers.router)
    router.include_router(registration.router)
    router.include_router(events.router)
    router.include_router(inline.router)
    router.include_router(common.fallback_router)
    return router


__all__ = ["build_router"]
