"""Модели БД.

Три сущности: пользователь, мероприятие, запись на мероприятие.
Времена мероприятия хранятся «наивными» — в часовом поясе клуба
(settings.tz), именно в таком виде их вводит админ и видят игроки.
Служебные отметки (created_at и т.п.) — в UTC.

Обязательны у мероприятия только название, дата и время начала: всё
остальное админ вправе пропустить, и тогда строка просто не попадает
в карточку.
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
    Float,
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


class EventStatus(enum.StrEnum):
    DRAFT = "draft"  # создаётся админом, ещё не опубликовано
    OPEN = "open"  # идёт набор участников
    CLOSED = "closed"  # набор закончен
    CANCELLED = "cancelled"  # отменено


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

    # Уровень игры в падел по шкале 0.00-7.00, игрок указывает сам.
    # Бот его не проверяет и по нему никого не отсеивает — это ориентир
    # для админа при формировании составов.
    level: Mapped[float | None] = mapped_column(Float)

    phone: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)  # заметка админа об участнике

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


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- обязательное ---
    title: Mapped[str] = mapped_column(String(256))
    date: Mapped[dt.date] = mapped_column(Date)
    time_start: Mapped[dt.time] = mapped_column(Time)

    # --- всё, что можно пропустить ---
    time_end: Mapped[dt.time | None] = mapped_column(Time)
    location: Mapped[str | None] = mapped_column(String(128))

    # None = набор без ограничения по числу мест.
    max_players: Mapped[int | None] = mapped_column(Integer)

    # Свободный текст: "от 3.10 до 3.40", "3+". Бот его не валидирует,
    # отсев участников по уровню — ручная работа админа.
    rating_text: Mapped[str | None] = mapped_column(String(64))

    # None = строку про рейтинговость в карточке не показываем вовсе.
    is_rated: Mapped[bool | None] = mapped_column(Boolean)

    # Стоимость участия. В анонс не идёт — показывается участнику перед
    # подтверждением записи и админу в админке.
    price: Mapped[int | None] = mapped_column(Integer)

    # Произвольный текст в конце карточки, отделённый пустой строкой.
    description: Mapped[str | None] = mapped_column(Text)

    # Публичное мероприятие видно в списке и анонсируется в чат.
    # Скрытое доступно только по прямой ссылке.
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, native_enum=False, length=16),
        default=EventStatus.DRAFT,
    )

    # Координаты опубликованного анонса — чтобы отредактировать при смене статуса.
    announce_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    announce_message_id: Mapped[int | None] = mapped_column(BigInteger)

    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    registrations: Mapped[list[Registration]] = relationship(back_populates="event")

    @property
    def starts_at(self) -> dt.datetime:
        """Наивный datetime начала — для сортировки и сравнения с «сейчас»."""
        return dt.datetime.combine(self.date, self.time_start)

    @property
    def ends_at(self) -> dt.datetime:
        """Момент, после которого мероприятие считается прошедшим.

        Время окончания необязательно: если его не указали, считаем, что
        мероприятие идёт до конца суток. Если конец раньше начала —
        значит, оно перешло за полночь.
        """
        if self.time_end is None:
            return dt.datetime.combine(self.date, dt.time.max)
        end = dt.datetime.combine(self.date, self.time_end)
        if end <= self.starts_at:
            end += dt.timedelta(days=1)
        return end

    @property
    def accepts_signups(self) -> bool:
        return self.status is EventStatus.OPEN

    @property
    def has_limit(self) -> bool:
        return self.max_players is not None


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (
        # Одна строка на пару (мероприятие, участник): повторная запись после
        # отмены переиспользует её, меняя статус обратно на active.
        UniqueConstraint("event_id", "user_id", name="uq_registration_event_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    status: Mapped[RegistrationStatus] = mapped_column(
        Enum(RegistrationStatus, native_enum=False, length=16),
        default=RegistrationStatus.ACTIVE,
    )

    # Сколько мест занимает запись: 1 — за себя, 2 — за себя и напарника.
    # Занятость мероприятия считается суммой seats, а не числом строк.
    seats: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    # Имя напарника, когда записались за двоих.
    partner_name: Mapped[str | None] = mapped_column(String(128))

    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    # Момент последней активной записи — по нему строится порядок в составе.
    registered_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    event: Mapped[Event] = relationship(back_populates="registrations")
    user: Mapped[User] = relationship(back_populates="registrations")
