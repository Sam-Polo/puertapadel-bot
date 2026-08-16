"""Инлайн-клавиатуры админ-панели."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import get_settings
from app.db.models import Event, EventStatus, User
from app.keyboards.callbacks import AdminCB
from app.utils.formatting import event_button_label

# Парное меряем парами, одиночное — людьми. Вместимость выходит одна и та же.
MAX_PAIRS_PRESETS = (6, 8, 10, 12)
MAX_PLAYERS_PRESETS = tuple(pairs * 2 for pairs in MAX_PAIRS_PRESETS)

_ABORT = InlineKeyboardButton(
    text="✖️ Отменить создание", callback_data=AdminCB(action="abort").pack()
)
_SKIP = InlineKeyboardButton(
    text="⏭ Пропустить", callback_data=AdminCB(action="skip").pack()
)
_BACK = InlineKeyboardButton(
    text="⬅️ Назад", callback_data=AdminCB(action="back").pack()
)


def _nav(with_back: bool) -> list[list[InlineKeyboardButton]]:
    """Нижние строки любой клавиатуры воронки: назад и выход."""
    return [[_BACK, _ABORT]] if with_back else [[_ABORT]]


def admin_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать мероприятие", callback_data=AdminCB(action="new"))
    builder.button(text="📋 Мероприятия", callback_data=AdminCB(action="tours"))
    builder.button(text="👥 Пользователи", callback_data=AdminCB(action="users"))
    builder.adjust(1)
    return builder.as_markup()


def abort_kb(*, with_back: bool = False) -> InlineKeyboardMarkup:
    """Обязательный шаг: выйти можно, пропустить — нет."""
    return InlineKeyboardMarkup(inline_keyboard=_nav(with_back))


def skip_kb(*, with_back: bool = True) -> InlineKeyboardMarkup:
    """Необязательный шаг."""
    return InlineKeyboardMarkup(inline_keyboard=[[_SKIP], *_nav(with_back)])


def format_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👤 Одиночное", callback_data=AdminCB(action="fmt", value="singles")
    )
    builder.button(
        text="👥 Парное", callback_data=AdminCB(action="fmt", value="doubles")
    )
    builder.adjust(2)
    markup = builder.as_markup()
    markup.inline_keyboard.extend(_nav(True))
    return markup


def max_players_kb(*, is_doubles: bool) -> InlineKeyboardMarkup:
    """В парном кнопки считают пары, в одиночном — места.

    Значение в callback — всегда то, что нажал админ (пары или места);
    в места его переводит обработчик, знающий формат.
    """
    builder = InlineKeyboardBuilder()
    presets = MAX_PAIRS_PRESETS if is_doubles else MAX_PLAYERS_PRESETS
    suffix = " пар" if is_doubles else ""
    for value in presets:
        builder.button(
            text=f"{value}{suffix}", callback_data=AdminCB(action="max", value=str(value))
        )
    builder.adjust(len(presets))
    markup = builder.as_markup()
    markup.inline_keyboard.append([_SKIP])
    markup.inline_keyboard.extend(_nav(True))
    return markup


def show_roster_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👁 Показывать состав", callback_data=AdminCB(action="roster", value="1")
    )
    builder.button(
        text="🙈 Скрыть состав", callback_data=AdminCB(action="roster", value="0")
    )
    builder.adjust(1)
    markup = builder.as_markup()
    markup.inline_keyboard.extend(_nav(True))
    return markup


def yes_no_kb(action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=AdminCB(action=action, value="1"))
    builder.button(text="❌ Нет", callback_data=AdminCB(action=action, value="0"))
    builder.adjust(2)
    markup = builder.as_markup()
    markup.inline_keyboard.append([_SKIP])
    markup.inline_keyboard.extend(_nav(True))
    return markup


def price_kb() -> InlineKeyboardMarkup:
    """Стоимость пишется целиком вручную — готовых вариантов тут нет."""
    return InlineKeyboardMarkup(inline_keyboard=[[_SKIP], *_nav(True)])


def visibility_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Видно всем", callback_data=AdminCB(action="vis", value="1"))
    builder.button(text="🙈 Скрытое", callback_data=AdminCB(action="vis", value="0"))
    builder.adjust(1)
    markup = builder.as_markup()
    markup.inline_keyboard.extend(_nav(True))
    return markup


def preview_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Опубликовать", callback_data=AdminCB(action="publish"))
    builder.button(text="💾 Сохранить черновиком", callback_data=AdminCB(action="draft"))
    builder.adjust(1)
    markup = builder.as_markup()
    markup.inline_keyboard.extend(_nav(True))
    return markup


def edit_menu_kb(event: Event, *, page: int) -> InlineKeyboardMarkup:
    """Список полей мероприятия — по кнопке на каждое."""
    from app.texts import FIELD_LABELS

    builder = InlineKeyboardBuilder()
    for field in (
        "title", "date", "time_start", "time_end", "format",
        "max_players", "rating_text", "is_rated", "price",
        "is_public", "show_roster", "description",
    ):
        builder.button(
            text=FIELD_LABELS[field],
            callback_data=AdminCB(action="edf", id=event.id, page=page, value=field),
        )
    builder.adjust(2)
    markup = builder.as_markup()
    markup.inline_keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ К мероприятию",
                callback_data=AdminCB(action="tour", id=event.id, page=page).pack(),
            )
        ]
    )
    return markup


def edit_value_kb(
    event: Event,
    *,
    page: int,
    options: list[tuple[str, str]] | None = None,
    clearable: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура шага правки: варианты, очистка и возврат в меню полей."""
    builder = InlineKeyboardBuilder()
    for label, value in options or []:
        builder.button(text=label, callback_data=AdminCB(action="edset", value=value))
    builder.adjust(2)
    markup = builder.as_markup()
    if clearable:
        markup.inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="🗑 Очистить поле",
                    callback_data=AdminCB(action="edset", value="__clear__").pack(),
                )
            ]
        )
    markup.inline_keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=AdminCB(action="edit", id=event.id, page=page).pack(),
            )
        ]
    )
    return markup


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
        ),
        InlineKeyboardButton(
            text="✏️ Изменить",
            callback_data=AdminCB(action="edit", id=event.id, page=page).pack(),
        ),
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

    # Анонса может не быть и у давно созданного мероприятия: например, его
    # завели скрытым, а потом сделали публичным. Кнопка нужна всегда, пока
    # анонс не опубликован.
    if (
        event.is_public
        and event.announce_message_id is None
        and event.status is not EventStatus.DRAFT
    ):
        builder.row(
            InlineKeyboardButton(
                text="📢 Опубликовать анонс",
                callback_data=AdminCB(
                    action="publish_existing", id=event.id, page=page
                ).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="🗑 Удалить мероприятие",
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


def confirm_delete_event_kb(event: Event, *, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗑 Да, удалить",
        callback_data=AdminCB(action="cancel_ok", id=event.id, page=page),
    )
    builder.button(
        text="⬅️ Не удалять",
        callback_data=AdminCB(action="tour", id=event.id, page=page),
    )
    builder.adjust(1)
    return builder.as_markup()


def back_to_admin_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    return builder.as_markup()
