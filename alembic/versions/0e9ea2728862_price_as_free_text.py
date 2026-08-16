"""price as free text

Revision ID: 0e9ea2728862
Revises: e7adba966e95
Create Date: 2026-08-16 20:01:32.880576

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0e9ea2728862'
down_revision: str | None = 'e7adba966e95'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.alter_column(
            'price',
            existing_type=sa.INTEGER(),
            type_=sa.String(length=256),
            existing_nullable=True,
        )

    # Раньше цена была числом, а «0» означал «бесплатно» — это знание жило
    # в коде форматирования. Теперь в поле лежит ровно то, что увидит
    # участник, поэтому дописываем единицы прошлым значениям.
    op.execute("UPDATE events SET price = 'бесплатно' WHERE price = '0'")
    op.execute(
        "UPDATE events SET price = price || ' ₽' "
        "WHERE price IS NOT NULL AND price <> '' AND price NOT GLOB '*[^0-9]*'"
    )


def downgrade() -> None:
    # Обратно в число влезут только чистые цифры; всё остальное (условия
    # словами) числом не представимо и обнуляется.
    op.execute("UPDATE events SET price = '0' WHERE price = 'бесплатно'")
    op.execute("UPDATE events SET price = replace(price, ' ₽', '')")
    op.execute("UPDATE events SET price = NULL WHERE price GLOB '*[^0-9]*'")

    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.alter_column(
            'price',
            existing_type=sa.String(length=256),
            type_=sa.INTEGER(),
            existing_nullable=True,
        )
