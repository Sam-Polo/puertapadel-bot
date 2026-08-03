"""FSM-состояния: воронка регистрации игрока и воронка создания турнира."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class RegistrationSG(StatesGroup):
    agreement = State()
    first_name = State()
    last_name = State()
    gender = State()
    age = State()
    confirm = State()


class NewTournamentSG(StatesGroup):
    date = State()
    time_start = State()
    time_end = State()
    title = State()
    location = State()
    max_players = State()
    rating = State()
    is_rated = State()
    price = State()
    visibility = State()
    preview = State()


class EditProfileSG(StatesGroup):
    """Правка отдельного поля профиля — какое именно, лежит в data['field']."""

    value = State()


class AdminNoteSG(StatesGroup):
    """Заметка админа об игроке."""

    text = State()
