"""backfill empty location

Локация раньше была необязательным шагом воронки, и у мероприятий,
заведённых с пропуском, поле осталось пустым. Теперь она одна на клуб и
подставляется автоматически — доливаем её и в старые записи, чтобы
карточка не выглядела обрезанной.

Revision ID: 22440681cbcd
Revises: 0e9ea2728862
Create Date: 2026-08-16 20:39:59.958377

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.config import get_settings

revision: str = '22440681cbcd'
down_revision: str | None = '0e9ea2728862'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE events SET location = :location "
            "WHERE location IS NULL OR location = ''"
        ).bindparams(location=get_settings().location_name)
    )


def downgrade() -> None:
    # Обратно различить «было пусто» и «заполнено осознанно» нельзя,
    # поэтому откат ничего не трогает.
    pass
