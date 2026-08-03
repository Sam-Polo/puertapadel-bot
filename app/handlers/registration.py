"""Воронка регистрации игрока.

Порядок: согласие → имя → фамилия → пол → возраст → подтверждение.
Согласие спрашиваем первым: до него бот персональных данных не собирает.
"""

from __future__ import annotations

import datetime as dt
import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.config import get_settings
from app.db.models import User
from app.keyboards.callbacks import RegCB
from app.keyboards.common import (
    agreement_kb,
    back_to_menu_kb,
    gender_kb,
    main_menu_kb,
    registration_confirm_kb,
)
from app.services import users as users_service
from app.states import RegistrationSG
from app.utils.formatting import q
from app.utils.tg import edit_or_send

logger = logging.getLogger(__name__)

router = Router(name="registration")

# Буквы (в том числе с дефисом и апострофом), 2-30 символов.
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-' ]{1,29}$")

MIN_AGE = 6
MAX_AGE = 100

# Ключ в FSM, где ждёт турнир, на который человек шёл по ссылке из анонса.
PENDING_TOURNAMENT = "pending_tournament"


async def start_registration(message: Message, state: FSMContext) -> None:
    """Показывает согласие и переводит в первое состояние воронки.

    Данные FSM чистим, но турнир из deep-link'а сохраняем: пользователь
    пришёл записываться, и после регистрации мы обязаны его туда вернуть.
    """
    data = await state.get_data()
    pending = data.get(PENDING_TOURNAMENT)
    await state.clear()
    if pending is not None:
        await state.update_data({PENDING_TOURNAMENT: pending})

    await state.set_state(RegistrationSG.agreement)
    await message.answer(
        texts.AGREEMENT.format(url=get_settings().agreement_url),
        reply_markup=agreement_kb(),
        disable_web_page_preview=True,
    )


async def finish_registration(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Завершает регистрацию и ведёт дальше: к турниру из ссылки или в меню."""
    data = await state.get_data()
    pending = data.get(PENDING_TOURNAMENT)
    await state.clear()

    await message.answer(texts.REGISTRATION_DONE)

    if pending is not None:
        # Импорт здесь: tournaments импортирует клавиатуры, а не нас — но
        # держим связь односторонней и в рантайме.
        from app.handlers.tournaments import send_tournament_card

        await send_tournament_card(message, session, user, int(pending), src="link")
        return

    await message.answer(texts.MAIN_MENU, reply_markup=main_menu_kb())


@router.callback_query(RegCB.filter(F.action == "start"))
async def on_start_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await start_registration(callback.message, state)


@router.callback_query(RegCB.filter(F.action == "restart"))
async def on_restart(callback: CallbackQuery, state: FSMContext) -> None:
    """«Заполнить заново» — и из воронки, и из профиля."""
    await callback.answer()
    if callback.message is None:
        return
    await start_registration(callback.message, state)


@router.callback_query(RegistrationSG.agreement, RegCB.filter(F.action == "accept"))
async def on_agreement_accepted(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(
        agreement_accepted_at=dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat()
    )
    await state.set_state(RegistrationSG.first_name)
    await edit_or_send(callback, texts.ASK_FIRST_NAME)


@router.callback_query(RegistrationSG.agreement, RegCB.filter(F.action == "decline"))
async def on_agreement_declined(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await edit_or_send(callback, texts.AGREEMENT_DECLINED)


@router.message(RegistrationSG.first_name, F.text)
async def on_first_name(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not NAME_RE.match(value):
        await message.answer(texts.BAD_NAME)
        return
    await state.update_data(first_name=value.title())
    await state.set_state(RegistrationSG.last_name)
    await message.answer(texts.ASK_LAST_NAME)


@router.message(RegistrationSG.last_name, F.text)
async def on_last_name(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not NAME_RE.match(value):
        await message.answer(texts.BAD_NAME)
        return
    await state.update_data(last_name=value.title())
    await state.set_state(RegistrationSG.gender)
    await message.answer(texts.ASK_GENDER, reply_markup=gender_kb())


@router.callback_query(RegistrationSG.gender, RegCB.filter(F.action == "gender"))
async def on_gender(
    callback: CallbackQuery, callback_data: RegCB, state: FSMContext
) -> None:
    await callback.answer()
    await state.update_data(gender=callback_data.value)
    await state.set_state(RegistrationSG.age)
    await edit_or_send(callback, texts.ASK_AGE)


@router.message(RegistrationSG.age, F.text)
async def on_age(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (MIN_AGE <= int(raw) <= MAX_AGE):
        await message.answer(texts.BAD_AGE)
        return

    await state.update_data(age=int(raw))
    data = await state.get_data()
    await state.set_state(RegistrationSG.confirm)
    await message.answer(
        texts.REGISTRATION_CONFIRM.format(
            first_name=q(data["first_name"]),
            last_name=q(data["last_name"]),
            gender=texts.GENDER_LABEL[data["gender"]],
            age=data["age"],
        ),
        reply_markup=registration_confirm_kb(),
    )


@router.callback_query(RegistrationSG.confirm, RegCB.filter(F.action == "confirm"))
async def on_confirm(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await callback.answer()
    data = await state.get_data()

    accepted_raw = data.get("agreement_accepted_at")
    accepted_at = (
        dt.datetime.fromisoformat(accepted_raw)
        if accepted_raw
        else dt.datetime.now(dt.UTC).replace(tzinfo=None)
    )

    await users_service.complete_registration(
        session,
        user,
        first_name=data["first_name"],
        last_name=data["last_name"],
        gender=data["gender"],
        age=data["age"],
        agreement_accepted_at=accepted_at,
    )
    logger.info("Пользователь %s завершил регистрацию", user.id)

    if callback.message is None:
        return
    await finish_registration(callback.message, state, session, user)


@router.message(RegistrationSG.first_name)
@router.message(RegistrationSG.last_name)
@router.message(RegistrationSG.age)
async def on_wrong_content(message: Message) -> None:
    """Стикер/фото/голосовое вместо ответа — переспрашиваем, состояние не теряем."""
    await message.answer(texts.REGISTRATION_IN_PROGRESS)


@router.message(RegistrationSG.agreement)
@router.message(RegistrationSG.gender)
@router.message(RegistrationSG.confirm)
async def on_text_instead_of_button(message: Message) -> None:
    """На этих шагах ждём нажатие кнопки, а не текст."""
    await message.answer(texts.REGISTRATION_IN_PROGRESS)


@router.callback_query(RegCB.filter())
async def on_stale_registration_button(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка из старого сообщения, когда состояние уже другое.

    Без этого пользователь тыкает мёртвую кнопку и не понимает, почему
    ничего не происходит.
    """
    await callback.answer()
    current = await state.get_state()
    if current is None and callback.message is not None:
        await edit_or_send(callback, texts.MAIN_MENU, main_menu_kb())
        return
    if callback.message is not None:
        await callback.message.answer(
            texts.REGISTRATION_IN_PROGRESS, reply_markup=back_to_menu_kb()
        )
