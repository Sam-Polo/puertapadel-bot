"""Инлайн-клавиатуры админ-панели."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import get_settings
from app.db.models import Tournament, TournamentStatus, User
from app.keyboards.callbacks import AdminCB
from app.utils.formatting import tournament_button_label

MAX_PLAYERS_PRESETS = (4, 8, 12, 16)


def admin_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать турнир", callback_data=AdminCB(action="new"))
    builder.button(text="📋 Турниры", callback_data=AdminCB(action="tours"))
    builder.button(text="👥 Пользователи", callback_data=AdminCB(action="users"))
    builder.adjust(1)
    return builder.as_markup()


def abort_kb() -> InlineKeyboardMarkup:
    """Кнопка выхода из воронки создания турнира."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✖️ Отменить создание", callback_data=AdminCB(action="abort"))
    return builder.as_markup()


def title_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Ввести название целиком", callback_data=AdminCB(action="title_full"))
    builder.button(text="✖️ Отменить создание", callback_data=AdminCB(action="abort"))
    builder.adjust(1)
    return builder.as_markup()


def locations_kb(recent: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # value ограничен размером callback_data, поэтому передаём индекс в списке.
    for index, name in enumerate(recent):
        builder.row(
            InlineKeyboardButton(
                text=f"📍 {name}",
                callback_data=AdminCB(action="loc", value=str(index)).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="✖️ Отменить создание", callback_data=AdminCB(action="abort").pack()
        )
    )
    return builder.as_markup()


def max_players_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value in MAX_PLAYERS_PRESETS:
        builder.button(text=str(value), callback_data=AdminCB(action="max", value=str(value)))
    builder.adjust(len(MAX_PLAYERS_PRESETS))
    builder.row(
        InlineKeyboardButton(
            text="✖️ Отменить создание", callback_data=AdminCB(action="abort").pack()
        )
    )
    return builder.as_markup()


def rating_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Без ограничений", callback_data=AdminCB(action="rating", value="any"))
    builder.button(text="✖️ Отменить создание", callback_data=AdminCB(action="abort"))
    builder.adjust(1)
    return builder.as_markup()


def yes_no_kb(action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=AdminCB(action=action, value="1"))
    builder.button(text="❌ Нет", callback_data=AdminCB(action=action, value="0"))
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text="✖️ Отменить создание", callback_data=AdminCB(action="abort").pack()
        )
    )
    return builder.as_markup()


def price_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Бесплатно", callback_data=AdminCB(action="price", value="0"))
    builder.button(text="✖️ Отменить создание", callback_data=AdminCB(action="abort"))
    builder.adjust(1)
    return builder.as_markup()


def visibility_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Видна всем", callback_data=AdminCB(action="vis", value="1"))
    builder.button(text="🙈 Скрытая", callback_data=AdminCB(action="vis", value="0"))
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="✖️ Отменить создание", callback_data=AdminCB(action="abort").pack()
        )
    )
    return builder.as_markup()


def preview_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Опубликовать", callback_data=AdminCB(action="publish"))
    builder.button(text="💾 Сохранить черновиком", callback_data=AdminCB(action="draft"))
    builder.button(text="✖️ Отменить создание", callback_data=AdminCB(action="abort"))
    builder.adjust(1)
    return builder.as_markup()


def admin_tournaments_kb(
    tournaments: list[Tournament],
    *,
    page: int,
    total_pages: int,
    counters: dict[int, int],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tournaments:
        builder.row(
            InlineKeyboardButton(
                text=tournament_button_label(t, taken=counters.get(t.id, 0)),
                callback_data=AdminCB(action="tour", id=t.id, page=page).pack(),
            )
        )
    if total_pages > 1:
        builder.row(
            InlineKeyboardButton(
                text="◀️",
                callback_data=AdminCB(
                    action="tours", page=(page - 1) % total_pages
                ).pack(),
            ),
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"),
            InlineKeyboardButton(
                text="▶️",
                callback_data=AdminCB(
                    action="tours", page=(page + 1) % total_pages
                ).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(text="➕ Создать", callback_data=AdminCB(action="new").pack()),
        InlineKeyboardButton(text="⬅️ Меню", callback_data=AdminCB(action="menu").pack()),
    )
    return builder.as_markup()


def admin_tournament_kb(t: Tournament, *, page: int) -> InlineKeyboardMarkup:
    settings = get_settings()
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="👥 Состав",
            callback_data=AdminCB(action="players", id=t.id, page=page).pack(),
        )
    )

    if t.status is TournamentStatus.DRAFT:
        builder.row(
            InlineKeyboardButton(
                text="📢 Опубликовать",
                callback_data=AdminCB(action="publish_existing", id=t.id, page=page).pack(),
            )
        )
    elif t.status is TournamentStatus.OPEN:
        builder.row(
            InlineKeyboardButton(
                text="🔴 Закрыть набор",
                callback_data=AdminCB(action="status", id=t.id, page=page, value="closed").pack(),
            )
        )
    elif t.status is TournamentStatus.CLOSED:
        builder.row(
            InlineKeyboardButton(
                text="🟢 Открыть набор",
                callback_data=AdminCB(action="status", id=t.id, page=page, value="open").pack(),
            )
        )

    if t.status is not TournamentStatus.CANCELLED:
        builder.row(
            InlineKeyboardButton(
                text="🚫 Отменить турнир",
                callback_data=AdminCB(action="cancel", id=t.id, page=page).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(text="🔗 Ссылка на запись", url=settings.deep_link(t.id))
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К списку", callback_data=AdminCB(action="tours", page=page).pack()
        )
    )
    return builder.as_markup()


def admin_participants_kb(
    t: Tournament,
    participants: list[tuple[User, bool]],
    *,
    page: int,
) -> InlineKeyboardMarkup:
    """Кнопка на каждого игрока — переключает отметку об оплате."""
    builder = InlineKeyboardBuilder()
    for index, (user, is_paid) in enumerate(participants, start=1):
        mark = "💰" if is_paid else "⏳"
        builder.row(
            InlineKeyboardButton(
                text=f"{mark} {index}. {user.full_name}",
                callback_data=AdminCB(
                    action="paid", id=t.id, page=page, value=str(user.id)
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=AdminCB(
                    action="kick", id=t.id, page=page, value=str(user.id)
                ).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К турниру",
            callback_data=AdminCB(action="tour", id=t.id, page=page).pack(),
        )
    )
    return builder.as_markup()


def admin_users_kb(
    users: list[User], *, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user in users:
        suffix = f" · @{user.username}" if user.username else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{user.full_name}{suffix}",
                callback_data=AdminCB(action="user", id=user.id, page=page).pack(),
            )
        )
    if total_pages > 1:
        builder.row(
            InlineKeyboardButton(
                text="◀️",
                callback_data=AdminCB(action="users", page=(page - 1) % total_pages).pack(),
            ),
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"),
            InlineKeyboardButton(
                text="▶️",
                callback_data=AdminCB(action="users", page=(page + 1) % total_pages).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(text="⬅️ Меню", callback_data=AdminCB(action="menu").pack())
    )
    return builder.as_markup()


def admin_user_kb(user: User, *, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    label = "🔓 Разблокировать" if user.is_blocked else "🔒 Заблокировать"
    builder.row(
        InlineKeyboardButton(
            text=label,
            callback_data=AdminCB(action="block", id=user.id, page=page).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К списку", callback_data=AdminCB(action="users", page=page).pack()
        )
    )
    return builder.as_markup()


def confirm_cancel_tournament_kb(t: Tournament, *, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🚫 Да, отменить турнир",
        callback_data=AdminCB(action="cancel_ok", id=t.id, page=page),
    )
    builder.button(
        text="⬅️ Не отменять",
        callback_data=AdminCB(action="tour", id=t.id, page=page),
    )
    builder.adjust(1)
    return builder.as_markup()


def back_to_admin_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    return builder.as_markup()
