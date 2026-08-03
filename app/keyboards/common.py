"""Инлайн-клавиатуры пользовательской части."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import get_settings
from app.db.models import Registration, Tournament, TournamentStatus
from app.keyboards.callbacks import MenuCB, PageCB, RegCB, TourCB
from app.utils.formatting import tournament_button_label

PAGE_SIZE = 6


def start_registration_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Зарегистрироваться", callback_data=RegCB(action="start"))
    return builder.as_markup()


def agreement_kb() -> InlineKeyboardMarkup:
    settings = get_settings()
    builder = InlineKeyboardBuilder()
    builder.button(text="📄 Открыть соглашение", url=settings.agreement_url)
    builder.button(text="✅ Принимаю", callback_data=RegCB(action="accept"))
    builder.button(text="❌ Не принимаю", callback_data=RegCB(action="decline"))
    builder.adjust(1)
    return builder.as_markup()


def gender_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚹 Мужской", callback_data=RegCB(action="gender", value="male"))
    builder.button(text="🚺 Женский", callback_data=RegCB(action="gender", value="female"))
    builder.adjust(2)
    return builder.as_markup()


def registration_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Всё верно", callback_data=RegCB(action="confirm"))
    builder.button(text="🔄 Заполнить заново", callback_data=RegCB(action="restart"))
    builder.adjust(1)
    return builder.as_markup()


def main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎾 Турниры", callback_data=MenuCB(action="tournaments"))
    builder.button(text="📋 Мои турниры", callback_data=MenuCB(action="my"))
    builder.button(text="👤 Профиль", callback_data=MenuCB(action="profile"))
    builder.adjust(2, 1)
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В меню", callback_data=MenuCB(action="main"))
    return builder.as_markup()


def _pagination_row(scope: str, page: int, total_pages: int) -> list[InlineKeyboardButton]:
    if total_pages <= 1:
        return []
    row = [
        InlineKeyboardButton(
            text="◀️",
            callback_data=PageCB(scope=scope, page=(page - 1) % total_pages).pack(),
        ),
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"),
        InlineKeyboardButton(
            text="▶️",
            callback_data=PageCB(scope=scope, page=(page + 1) % total_pages).pack(),
        ),
    ]
    return row


def tournaments_list_kb(
    tournaments: list[Tournament],
    *,
    page: int,
    total_pages: int,
    scope: str,
    src: str,
    counters: dict[int, int] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tournaments:
        taken = counters.get(t.id) if counters else None
        builder.row(
            InlineKeyboardButton(
                text=tournament_button_label(t, taken=taken),
                callback_data=TourCB(action="view", id=t.id, page=page, src=src).pack(),
            )
        )
    row = _pagination_row(scope, page, total_pages)
    if row:
        builder.row(*row)
    builder.row(
        InlineKeyboardButton(text="⬅️ В меню", callback_data=MenuCB(action="main").pack())
    )
    return builder.as_markup()


def tournament_card_kb(
    t: Tournament,
    *,
    my_registration: Registration | None,
    page: int,
    src: str,
    is_full: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if my_registration is not None:
        if t.status is not TournamentStatus.CANCELLED:
            builder.button(
                text="❌ Отменить запись",
                callback_data=TourCB(action="cancel", id=t.id, page=page, src=src),
            )
    elif t.accepts_signups and not is_full:
        builder.button(
            text="✅ Записаться",
            callback_data=TourCB(action="signup", id=t.id, page=page, src=src),
        )

    back_action = "my" if src == "my" else "tournaments"
    builder.button(text="⬅️ Назад", callback_data=MenuCB(action=back_action))
    builder.adjust(1)
    return builder.as_markup()


def signup_confirm_kb(t: Tournament, *, page: int, src: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить запись",
        callback_data=TourCB(action="signup_ok", id=t.id, page=page, src=src),
    )
    builder.button(
        text="⬅️ Назад",
        callback_data=TourCB(action="view", id=t.id, page=page, src=src),
    )
    builder.adjust(1)
    return builder.as_markup()


def cancel_confirm_kb(t: Tournament, *, page: int, src: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Да, отменить запись",
        callback_data=TourCB(action="cancel_ok", id=t.id, page=page, src=src),
    )
    builder.button(
        text="⬅️ Оставить запись",
        callback_data=TourCB(action="view", id=t.id, page=page, src=src),
    )
    builder.adjust(1)
    return builder.as_markup()


def profile_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить данные", callback_data=RegCB(action="restart"))
    builder.button(text="⬅️ В меню", callback_data=MenuCB(action="main"))
    builder.adjust(1)
    return builder.as_markup()


def after_signup_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Мои турниры", callback_data=MenuCB(action="my"))
    builder.button(text="🎾 Другие турниры", callback_data=MenuCB(action="tournaments"))
    builder.adjust(1)
    return builder.as_markup()
