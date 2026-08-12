"""rename tournaments to events, optional fields, description, seats

Автогенерация предлагала create_table('events') + drop_table('tournaments'),
то есть выбросить все записи. Здесь то же самое сделано переименованием,
чтобы уже заведённые мероприятия и составы пережили обновление.

Revision ID: b3197bddbf9a
Revises: a4ed4a7f3ff6
Create Date: 2026-08-12 13:37:49.820232

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b3197bddbf9a'
down_revision: str | None = 'a4ed4a7f3ff6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite сам чинит REFERENCES в других таблицах при переименовании.
    op.rename_table('tournaments', 'events')

    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))
        # Всё, кроме названия, даты и времени начала, стало необязательным.
        batch_op.alter_column('location', existing_type=sa.String(length=128), nullable=True)
        batch_op.alter_column('time_end', existing_type=sa.Time(), nullable=True)
        batch_op.alter_column('max_players', existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column('is_rated', existing_type=sa.Boolean(), nullable=True)

    # Два прохода: индекс по event_id нельзя создать в том же batch-блоке,
    # где колонка ещё называется tournament_id — alembic собирает состав
    # индексов до применения переименования и не находит новую колонку.
    with op.batch_alter_table('registrations', schema=None) as batch_op:
        # seats=1 у существующих записей: раньше одна запись = одно место.
        batch_op.add_column(
            sa.Column('seats', sa.Integer(), server_default='1', nullable=False)
        )
        batch_op.add_column(sa.Column('partner_name', sa.String(length=128), nullable=True))
        batch_op.drop_index('ix_registrations_tournament_id')
        batch_op.drop_constraint('uq_registration_tournament_user', type_='unique')
        batch_op.alter_column(
            'tournament_id', new_column_name='event_id', existing_type=sa.Integer()
        )

    with op.batch_alter_table('registrations', schema=None) as batch_op:
        batch_op.create_index('ix_registrations_event_id', ['event_id'], unique=False)
        batch_op.create_unique_constraint(
            'uq_registration_event_user', ['event_id', 'user_id']
        )


def downgrade() -> None:
    with op.batch_alter_table('registrations', schema=None) as batch_op:
        batch_op.drop_constraint('uq_registration_event_user', type_='unique')
        batch_op.drop_index('ix_registrations_event_id')
        batch_op.alter_column(
            'event_id', new_column_name='tournament_id', existing_type=sa.Integer()
        )
        batch_op.drop_column('partner_name')
        batch_op.drop_column('seats')

    with op.batch_alter_table('registrations', schema=None) as batch_op:
        batch_op.create_index(
            'ix_registrations_tournament_id', ['tournament_id'], unique=False
        )
        batch_op.create_unique_constraint(
            'uq_registration_tournament_user', ['tournament_id', 'user_id']
        )

    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_column('description')
        # Обратно в NOT NULL: строки с пустыми значениями сюда не переживут,
        # поэтому откат имеет смысл только сразу после неудачного апгрейда.
        batch_op.alter_column('location', existing_type=sa.String(length=128), nullable=False)
        batch_op.alter_column('time_end', existing_type=sa.Time(), nullable=False)
        batch_op.alter_column('max_players', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('is_rated', existing_type=sa.Boolean(), nullable=False)

    op.rename_table('events', 'tournaments')
