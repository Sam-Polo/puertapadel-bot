"""Инлайн-клавиатуры админ-панели."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import get_settings
from app.db.models import Event, EventStatus, User
from app.keyboards.callbacks import AdminCB
from app.utils.formatting import event_button_label

MAX_PLAYERS_PRESETS = (4, 8, 12, 16)

_ABORT = InlineKeyboardButton(
    text="✖️ Отменить создание", callback_data=AdminCB(action="abort").pack()
)
_SKIP = InlineKeyboardButton(
    text="⏭ Пропустить", callback_data=AdminCB(action="skip").pack()
)


def admin_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать мероприятие", callback_data=AdminCB(action="new"))
    builder.button(text="📋 Мероприятия", callback_data=AdminCB(action="tours"))
    builder.button(text="👥 Пользователи", callback_data=AdminCB(action="users"))
    builder.adjust(1)
    return builder.as_markup()


def abort_kb() -> InlineKeyboardMarkup:
    """Обязательный шаг: выйти можно, пропустить — нет."""
    return InlineKeyboardMarkup(inline_keyboard=[[_ABORT]])


def skip_kb() -> InlineKeyboardMarkup:
    """Необязательный шаг."""
    return InlineKeyboardMarkup(inline_keyboard=[[_SKIP], [_ABORT]])


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
    builder.row(_SKIP)
    builder.row(_ABORT)
    return builder.as_markup()


def max_players_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value in MAX_PLAYERS_PRESETS:
        builder.button(text=str(value), callback_data=AdminCB(action="max", value=str(value)))
    builder.adjust(len(MAX_PLAYERS_PRESETS))
    markup = builder.as_markup()
    markup.inline_keyboard.append([_SKIP])
    markup.inline_keyboard.append([_ABORT])
    return markup


def yes_no_kb(action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=AdminCB(action=action, value="1"))
    builder.button(text="❌ Нет", callback_data=AdminCB(action=action, value="0"))
    builder.adjust(2)
    markup = builder.as_markup()
    markup.inline_keyboard.append([_SKIP])
    markup.inline_keyboard.append([_ABORT])
    return markup


def price_kb() -> InlineKeyboardMarkup:
    """«Бесплатно» и «пропустить» — разные вещи: 0 ₽ против «не указано»."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🆓 Бесплатно", callback_data=AdminCB(action="price", value="0"))
    builder.adjust(1)
    markup = builder.as_markup()
    markup.inline_keyboard.append([_SKIP])
    markup.inline_keyboard.append([_ABORT])
    return markup


def visibility_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Видно всем", callback_data=AdminCB(action="vis", value="1"))
    builder.button(text="🙈 Скрытое", callback_data=AdminCB(action="vis", value="0"))
    builder.adjust(1)
    markup = builder.as_markup()
    markup.inline_keyboard.append([_ABORT])
    return markup


def preview_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Опубликовать", callback_data=AdminCB(action="publish"))
    builder.button(text="💾 Сохранить черновиком", callback_data=AdminCB(action="draft"))
    builder.button(text="✖️ Отменить создание", callback_data=AdminCB(action="abort"))
    builder.adjust(1)
    return builder.as_markup()


def admin_events_kb(
    events: list[Event],
    *,
    page: int,
    total_pages: int,
    counters: dict[int, int],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for event in events:
        builder.row(
            InlineKeyboardButton(
                text=event_button_label(event, taken=counters.get(event.id, 0)),
                callback_data=AdminCB(action="tour", id=event.id, page=page).pack(),
            )
        )
    if total_pages > 1:
        builder.row(
            InlineKeyboardButton(
                text="◀️",
                callback_data=AdminCB(action="tours", page=(page - 1) % total_pages).pack(),
            ),
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"),
            InlineKeyboardButton(
                text="▶️",
                callback_data=AdminCB(action="tours", page=(page + 1) % total_pages).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(text="➕ Создать", callback_data=AdminCB(action="new").pack()),
        InlineKeyboardButton(text="⬅️ Меню", callback_data=AdminCB(action="menu").pack()),
    )
    return builder.as_markup()


def admin_event_kb(event: Event, *, page: int) -> InlineKeyboardMarkup:
    settings = get_settings()
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="👥 Состав",
            callback_data=AdminCB(action="players", id=event.id, page=page).pack(),
        )
    )

    if event.status is EventStatus.DRAFT:
        builder.row(
            InlineKeyboardButton(
                text="📢 Опубликовать",
                callback_data=AdminCB(
                    action="publish_existing", id=event.id, page=page
                ).pack(),
            )
        )
    elif event.status is EventStatus.OPEN:
        builder.row(
            InlineKeyboardButton(
                text="🔴 Закрыть набор",
                callback_data=AdminCB(
                    action="status", id=event.id, page=page, value="closed"
                ).pack(),
            )
        )
    elif event.status is EventStatus.CLOSED:
        builder.row(
            InlineKeyboardButton(
                text="🟢 Открыть набор",
                callback_data=AdminCB(
                    action="status", id=event.id, page=page, value="open"
                ).pack(),
            )
        )

    if event.status is not EventStatus.CANCELLED:
        builder.row(
            InlineKeyboardButton(
                text="🚫 Отменить мероприятие",
                callback_data=AdminCB(action="cancel", id=event.id, page=page).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(text="🔗 Ссылка на запись", url=settings.deep_link(event.id))
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К списку", callback_data=AdminCB(action="tours", page=page).pack()
        )
    )
    return builder.as_markup()


def admin_participants_kb(
    event: Event,
    participants: list[tuple[User, bool]],
    *,
    page: int,
    show_payment: bool = True,
) -> InlineKeyboardMarkup:
    """Кнопка на каждого участника — переключает отметку об оплате.

    Если стоимость не задана, значок оплаты не рисуем: отмечать нечего.
    """
    builder = InlineKeyboardBuilder()
    for index, (user, is_paid) in enumerate(participants, start=1):
        mark = ("💰 " if is_paid else "⏳ ") if show_payment else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{mark}{index}. {user.full_name}",
                callback_data=AdminCB(
                    action="paid", id=event.id, page=page, value=str(user.id)
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=AdminCB(
                    action="kick", id=event.id, page=page, value=str(user.id)
                ).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К мероприятию",
            callback_data=AdminCB(action="tour", id=event.id, page=page).pack(),
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


def confirm_cancel_event_kb(event: Event, *, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🚫 Да, отменить мероприятие",
        callback_data=AdminCB(action="cancel_ok", id=event.id, page=page),
    )
    builder.button(
        text="⬅️ Не отменять",
        callback_data=AdminCB(action="tour", id=event.id, page=page),
    )
    builder.adjust(1)
    return builder.as_markup()


def back_to_admin_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    return builder.as_markup()
