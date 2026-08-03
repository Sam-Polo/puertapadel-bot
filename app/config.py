"""Конфигурация приложения: читается из окружения (.env)."""

from __future__ import annotations

from functools import cached_property, lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_ids(raw: str) -> list[int]:
    ids = []
    for chunk in raw.replace(" ", "").split(","):
        if chunk:
            ids.append(int(chunk))
    return ids


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(alias="BOT_TOKEN")
    bot_username: str = Field(alias="BOT_USERNAME")
    # Строкой, а не list[int]: pydantic-settings разбирает списки как JSON,
    # а в .env удобнее писать «123,456».
    admin_ids_raw: str = Field(alias="ADMIN_IDS")
    agreement_url: str = Field(alias="AGREEMENT_URL")

    announce_chat_id: int | None = Field(default=None, alias="ANNOUNCE_CHAT_ID")
    announce_thread_id: int | None = Field(default=None, alias="ANNOUNCE_THREAD_ID")

    tz: str = Field(default="Europe/Moscow", alias="TZ")
    db_path: str = Field(default="data/bot.sqlite3", alias="DB_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("admin_ids_raw")
    @classmethod
    def _check_admin_ids(cls, value: str) -> str:
        parsed = _split_ids(value)
        if not parsed:
            raise ValueError(
                "ADMIN_IDS пуст: укажите хотя бы один Telegram ID, иначе "
                "управлять турнирами будет некому"
            )
        return value

    @field_validator("bot_username", mode="before")
    @classmethod
    def _strip_at(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lstrip("@").strip()
        return value

    @field_validator("announce_chat_id", "announce_thread_id", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        """Пустая переменная в .env приходит как "" — считаем это "не задано"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @cached_property
    def admin_ids(self) -> list[int]:
        return _split_ids(self.admin_ids_raw)

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def sync_db_url(self) -> str:
        """Синхронный URL — нужен Alembic'у."""
        return f"sqlite:///{self.db_path}"

    @property
    def announces_enabled(self) -> bool:
        return self.announce_chat_id is not None

    def deep_link(self, tournament_id: int) -> str:
        return f"https://t.me/{self.bot_username}?start=t{tournament_id}"

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
