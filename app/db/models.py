"""Модели БД.

Три сущности: пользователь, турнир, запись на турнир.
Времена турнира хранятся «наивными» — в часовом поясе клуба (settings.tz),
именно в таком виде их вводит админ и видят игроки. Служебные отметки
(created_at и т.п.) — в UTC.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Gender(enum.StrEnum):
    MALE = "male"
    FEMALE = "female"


class TournamentStatus(enum.StrEnum):
    DRAFT = "draft"  # создаётся админом, ещё не опубликован
    OPEN = "open"  # идёт набор игроков
    CLOSED = "closed"  # набор закончен
    CANCELLED = "cancelled"  # отменён


class RegistrationStatus(enum.StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    # Telegram ID и есть первичный ключ — он стабилен и уникален.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)

    # Данные из Telegram — обновляются при каждом заходе, могут отсутствовать.
    username: Mapped[str | None] = mapped_column(String(64))
    tg_first_name: Mapped[str | None] = mapped_column(String(128))
    tg_last_name: Mapped[str | None] = mapped_column(String(128))

    # Данные, которые пользователь ввёл сам при регистрации.
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))
    gender: Mapped[Gender | None] = mapped_column(Enum(Gender, native_enum=False, length=16))
    age: Mapped[int | None] = mapped_column(Integer)

    phone: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)  # заметка админа об игроке

    # Регистрация считается завершённой только когда проставлен этот момент.
    registered_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    agreement_accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    registrations: Mapped[list[Registration]] = relationship(back_populates="user")

    @property
    def is_registered(self) -> bool:
        return self.registered_at is not None

    @property
    def full_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return " ".join(parts)
        tg_parts = [p for p in (self.tg_first_name, self.tg_last_name) if p]
        return " ".join(tg_parts) or f"id{self.id}"


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    title: Mapped[str] = mapped_column(String(256))
    location: Mapped[str] = mapped_column(String(128))

    date: Mapped[dt.date] = mapped_column(Date)
    time_start: Mapped[dt.time] = mapped_column(Time)
    time_end: Mapped[dt.time] = mapped_column(Time)

    max_players: Mapped[int] = mapped_column(Integer)

    # Свободный текст: "от 3.10 до 3.40", "3+", "любой". Бот его не валидирует,
    # отсев игроков не по уровню — ручная работа админа.
    rating_text: Mapped[str | None] = mapped_column(String(64))
    is_rated: Mapped[bool] = mapped_column(Boolean, default=False)

    # Стоимость участия. В анонс не идёт — показывается игроку перед
    # подтверждением записи и админу в админке.
    price: Mapped[int | None] = mapped_column(Integer)

    # Публичный турнир виден в списке и анонсируется в чат.
    # Скрытый доступен только по прямой ссылке.
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[TournamentStatus] = mapped_column(
        Enum(TournamentStatus, native_enum=False, length=16),
        default=TournamentStatus.DRAFT,
    )

    # Координаты опубликованного анонса — чтобы отредактировать при смене статуса.
    announce_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    announce_message_id: Mapped[int | None] = mapped_column(BigInteger)

    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    registrations: Mapped[list[Registration]] = relationship(back_populates="tournament")

    @property
    def starts_at(self) -> dt.datetime:
        """Наивный datetime начала — для сортировки и сравнения с «сейчас»."""
        return dt.datetime.combine(self.date, self.time_start)

    @property
    def ends_at(self) -> dt.datetime:
        """Если конец раньше начала — считаем, что турнир перешёл за полночь."""
        end = dt.datetime.combine(self.date, self.time_end)
        if end <= self.starts_at:
            end += dt.timedelta(days=1)
        return end

    @property
    def accepts_signups(self) -> bool:
        return self.status is TournamentStatus.OPEN


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (
        # Одна строка на пару (турнир, игрок): повторная запись после отмены
        # переиспользует её, меняя статус обратно на active.
        UniqueConstraint("tournament_id", "user_id", name="uq_registration_tournament_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    status: Mapped[RegistrationStatus] = mapped_column(
        Enum(RegistrationStatus, native_enum=False, length=16),
        default=RegistrationStatus.ACTIVE,
    )

    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    # Момент последней активной записи — по нему строится порядок в составе.
    registered_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    tournament: Mapped[Tournament] = relationship(back_populates="registrations")
    user: Mapped[User] = relationship(back_populates="registrations")
