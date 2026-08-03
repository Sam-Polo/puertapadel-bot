"""Пользовательская часть: список турниров, карточка, запись и отмена."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.models import Tournament, TournamentStatus, User
from app.keyboards.callbacks import MenuCB, PageCB, TourCB
from app.keyboards.common import (
    PAGE_SIZE,
    after_signup_kb,
    back_to_menu_kb,
    cancel_confirm_kb,
    signup_confirm_kb,
    start_registration_kb,
    tournament_card_kb,
    tournaments_list_kb,
)
from app.services import announce
from app.services import tournaments as tournaments_service
from app.services.tournaments import SignupResult
from app.utils.dates import fmt_date, fmt_time
from app.utils.formatting import fmt_price, q, render_for_player
from app.utils.tg import edit_or_send, paginate

logger = logging.getLogger(__name__)

router = Router(name="tournaments")

_SIGNUP_ERRORS = {
    SignupResult.ALREADY: texts.SIGNUP_ALREADY,
    SignupResult.CLOSED: texts.SIGNUP_CLOSED,
    SignupResult.CANCELLED: texts.SIGNUP_CANCELLED_TOURNAMENT,
    SignupResult.PASSED: texts.SIGNUP_PASSED,
}


async def show_tournaments_list(
    callback: CallbackQuery, session: AsyncSession, *, page: int = 0
) -> None:
    items = await tournaments_service.list_open_for_players(session)
    if not items:
        await edit_or_send(callback, texts.TOURNAMENTS_EMPTY, back_to_menu_kb())
        return

    chunk, page, total_pages = paginate(items, page, PAGE_SIZE)
    counters = await tournaments_service.counters(session, [t.id for t in chunk])
    await edit_or_send(
        callback,
        texts.TOURNAMENTS_LIST,
        tournaments_list_kb(
            chunk,
            page=page,
            total_pages=total_pages,
            scope="tournaments",
            src="list",
            counters=counters,
        ),
    )


async def show_my_tournaments(
    callback: CallbackQuery, session: AsyncSession, user: User, *, page: int = 0
) -> None:
    items = await tournaments_service.list_for_user(session, user.id)
    if not items:
        await edit_or_send(callback, texts.MY_TOURNAMENTS_EMPTY, back_to_menu_kb())
        return

    chunk, page, total_pages = paginate(items, page, PAGE_SIZE)
    counters = await tournaments_service.counters(session, [t.id for t in chunk])
    await edit_or_send(
        callback,
        texts.MY_TOURNAMENTS,
        tournaments_list_kb(
            chunk,
            page=page,
            total_pages=total_pages,
            scope="my",
            src="my",
            counters=counters,
        ),
    )


def _is_visible_to_player(t: Tournament, *, from_link: bool) -> bool:
    """Скрытый турнир открывается только по прямой ссылке."""
    if t.is_public:
        return True
    return from_link


async def _card_payload(
    session: AsyncSession, tournament: Tournament, user: User
) -> tuple[str, int, object]:
    taken = await tournaments_service.count_active(session, tournament.id)
    registration = await tournaments_service.get_registration(
        session, tournament.id, user.id
    )
    text = render_for_player(tournament, taken=taken, my_registration=registration)
    return text, taken, registration


async def show_tournament_card(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    tournament_id: int,
    *,
    page: int = 0,
    src: str = "list",
) -> None:
    tournament = await tournaments_service.get(session, tournament_id)
    if tournament is None:
        await edit_or_send(callback, texts.TOURNAMENT_NOT_FOUND, back_to_menu_kb())
        return
    if not _is_visible_to_player(tournament, from_link=src == "link"):
        await edit_or_send(callback, texts.TOURNAMENT_HIDDEN, back_to_menu_kb())
        return

    text, taken, registration = await _card_payload(session, tournament, user)
    await edit_or_send(
        callback,
        text,
        tournament_card_kb(
            tournament,
            my_registration=registration,  # type: ignore[arg-type]
            page=page,
            src=src,
            is_full=taken >= tournament.max_players,
        ),
    )


async def send_tournament_card(
    message: Message,
    session: AsyncSession,
    user: User,
    tournament_id: int,
    *,
    src: str = "link",
) -> bool:
    """Отдельным сообщением — для перехода по deep-link'у.

    False, если турнир показать нечего: вызывающий покажет меню.
    """
    tournament = await tournaments_service.get(session, tournament_id)
    if tournament is None:
        await message.answer(texts.TOURNAMENT_NOT_FOUND, reply_markup=back_to_menu_kb())
        return True
    if not _is_visible_to_player(tournament, from_link=src == "link"):
        await message.answer(texts.TOURNAMENT_HIDDEN, reply_markup=back_to_menu_kb())
        return True

    text, taken, registration = await _card_payload(session, tournament, user)
    await message.answer(
        text,
        reply_markup=tournament_card_kb(
            tournament,
            my_registration=registration,  # type: ignore[arg-type]
            page=0,
            src=src,
            is_full=taken >= tournament.max_players,
        ),
        disable_web_page_preview=True,
    )
    return True


@router.callback_query(PageCB.filter(F.scope == "tournaments"))
async def paginate_tournaments(
    callback: CallbackQuery, callback_data: PageCB, session: AsyncSession
) -> None:
    await callback.answer()
    await show_tournaments_list(callback, session, page=callback_data.page)


@router.callback_query(PageCB.filter(F.scope == "my"))
async def paginate_my(
    callback: CallbackQuery, callback_data: PageCB, session: AsyncSession, user: User
) -> None:
    await callback.answer()
    await show_my_tournaments(callback, session, user, page=callback_data.page)


@router.callback_query(TourCB.filter(F.action == "view"))
async def view_tournament(
    callback: CallbackQuery, callback_data: TourCB, session: AsyncSession, user: User
) -> None:
    await callback.answer()
    await show_tournament_card(
        callback,
        session,
        user,
        callback_data.id,
        page=callback_data.page,
        src=callback_data.src,
    )


@router.callback_query(TourCB.filter(F.action == "signup"))
async def ask_signup_confirm(
    callback: CallbackQuery, callback_data: TourCB, session: AsyncSession, user: User
) -> None:
    tournament = await tournaments_service.get(session, callback_data.id)
    if tournament is None:
        await callback.answer(texts.TOURNAMENT_NOT_FOUND, show_alert=True)
        return

    # Сюда попадает только админ: остальных до записи не пускает гейт.
    if not user.is_registered:
        await callback.answer()
        await edit_or_send(callback, texts.NEED_REGISTRATION, start_registration_kb())
        return

    if not tournament.accepts_signups:
        await callback.answer(texts.SIGNUP_CLOSED, show_alert=True)
        return

    taken = await tournaments_service.count_active(session, tournament.id)
    if taken >= tournament.max_players:
        await callback.answer(
            texts.SIGNUP_NO_SLOTS.format(max_players=tournament.max_players),
            show_alert=True,
        )
        await show_tournament_card(
            callback, session, user, tournament.id, page=callback_data.page, src=callback_data.src
        )
        return

    await callback.answer()
    await edit_or_send(
        callback,
        texts.SIGNUP_CONFIRM.format(
            title=q(tournament.title),
            date=fmt_date(tournament.date),
            time_start=fmt_time(tournament.time_start),
            time_end=fmt_time(tournament.time_end),
            location=q(tournament.location),
            price=fmt_price(tournament.price),
        ),
        signup_confirm_kb(tournament, page=callback_data.page, src=callback_data.src),
    )


@router.callback_query(TourCB.filter(F.action == "signup_ok"))
async def do_signup(
    callback: CallbackQuery, callback_data: TourCB, session: AsyncSession, user: User
) -> None:
    tournament = await tournaments_service.get(session, callback_data.id)
    if tournament is None:
        await callback.answer(texts.TOURNAMENT_NOT_FOUND, show_alert=True)
        return

    result, taken = await tournaments_service.signup(session, tournament, user)

    if result is SignupResult.NO_SLOTS:
        await callback.answer(
            texts.SIGNUP_NO_SLOTS.format(max_players=tournament.max_players),
            show_alert=True,
        )
        await show_tournament_card(
            callback, session, user, tournament.id, page=callback_data.page, src=callback_data.src
        )
        return

    if result is not SignupResult.OK:
        await callback.answer(_SIGNUP_ERRORS[result], show_alert=True)
        await show_tournament_card(
            callback, session, user, tournament.id, page=callback_data.page, src=callback_data.src
        )
        return

    await callback.answer("Готово!")
    await edit_or_send(
        callback,
        texts.SIGNUP_OK.format(
            title=q(tournament.title),
            date=fmt_date(tournament.date),
            time_start=fmt_time(tournament.time_start),
        ),
        after_signup_kb(),
    )
    logger.info("Игрок %s записан на турнир %s (%s/%s)", user.id, tournament.id, taken,
                tournament.max_players)


@router.callback_query(TourCB.filter(F.action == "cancel"))
async def ask_cancel_confirm(
    callback: CallbackQuery, callback_data: TourCB, session: AsyncSession
) -> None:
    tournament = await tournaments_service.get(session, callback_data.id)
    if tournament is None:
        await callback.answer(texts.TOURNAMENT_NOT_FOUND, show_alert=True)
        return

    await callback.answer()
    await edit_or_send(
        callback,
        texts.CANCEL_CONFIRM.format(
            title=q(tournament.title),
            date=fmt_date(tournament.date),
            time_start=fmt_time(tournament.time_start),
        ),
        cancel_confirm_kb(tournament, page=callback_data.page, src=callback_data.src),
    )


@router.callback_query(TourCB.filter(F.action == "cancel_ok"))
async def do_cancel(
    callback: CallbackQuery, callback_data: TourCB, session: AsyncSession, user: User
) -> None:
    tournament = await tournaments_service.get(session, callback_data.id)
    if tournament is None:
        await callback.answer(texts.TOURNAMENT_NOT_FOUND, show_alert=True)
        return

    registration = await tournaments_service.cancel_signup(session, tournament.id, user.id)
    if registration is None:
        await callback.answer(texts.CANCEL_NOT_FOUND, show_alert=True)
        await show_tournament_card(
            callback, session, user, tournament.id, page=callback_data.page, src=callback_data.src
        )
        return

    await callback.answer("Запись отменена")
    taken = await tournaments_service.count_active(session, tournament.id)

    # Админу это важно знать: место освободилось, возможно кого-то надо позвать.
    if callback.bot is not None and tournament.status is not TournamentStatus.CANCELLED:
        await announce.notify_admins(
            callback.bot,
            texts.NOTIFY_ADMIN_CANCEL.format(
                user=q(user.full_name),
                title=q(tournament.title),
                date=fmt_date(tournament.date),
                taken=taken,
                max_players=tournament.max_players,
            ),
        )

    await edit_or_send(
        callback,
        texts.CANCEL_OK.format(title=q(tournament.title)),
        back_to_menu_kb(),
    )
    logger.info("Игрок %s снялся с турнира %s", user.id, tournament.id)


@router.callback_query(MenuCB.filter(F.action == "tournaments"))
async def open_tournaments(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    await show_tournaments_list(callback, session)


@router.callback_query(MenuCB.filter(F.action == "my"))
async def open_my(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    await callback.answer()
    await show_my_tournaments(callback, session, user)
