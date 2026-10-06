"""Provider 可用模型列表缓存

- 新增 provider_model_catalogs：Provider 最近一次拉取的模型 ID 列表与时间，
  界面可保留并直接展示已拉取列表，无需每次重新请求上游。

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-07 02:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'e4f5a6b7c8d9'
down_revision: str | None = 'd3e4f5a6b7c8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'provider_model_catalogs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('provider_id', sa.String(length=32), nullable=False),
        sa.Column('fetched_at', sa.DateTime(), nullable=False),
        sa.Column('models', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('provider_model_catalogs') as batch_op:
        batch_op.create_index(
            batch_op.f('ix_provider_model_catalogs_provider_id'), ['provider_id'], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table('provider_model_catalogs') as batch_op:
        batch_op.drop_index(batch_op.f('ix_provider_model_catalogs_provider_id'))
    op.drop_table('provider_model_catalogs')
