"""FSM-состояния: воронка регистрации участника и воронка создания мероприятия."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class RegistrationSG(StatesGroup):
    agreement = State()
    first_name = State()
    last_name = State()
    gender = State()
    level = State()
    confirm = State()


class NewEventSG(StatesGroup):
    date = State()
    time_start = State()
    time_end = State()
    title = State()
    # Формат до вместимости: от него зависит, считаем пары или людей.
    event_format = State()
    max_players = State()
    rating = State()
    is_rated = State()
    price = State()
    visibility = State()
    show_roster = State()
    description = State()
    preview = State()


class EditEventSG(StatesGroup):
    """Правка одного поля мероприятия. Какого — лежит в data['field']."""

    value = State()


class SignupSG(StatesGroup):
    """Запись за двоих: ждём имя напарника."""

    partner_name = State()


class EditProfileSG(StatesGroup):
    """Правка отдельного поля профиля — какое именно, лежит в data['field']."""

    value = State()
