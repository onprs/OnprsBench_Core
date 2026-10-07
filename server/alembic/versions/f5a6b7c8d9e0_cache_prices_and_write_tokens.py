"""Deployment 缓存价格与 usage 缓存写入 token

- deployments 补齐缓存计费单价：缓存读取（cached input）与缓存写入（cache write）
- usage_records 记录 cache_write_tokens，使缓存写入成本可计算

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-10-07 04:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'f5a6b7c8d9e0'
down_revision: str | None = 'e4f5a6b7c8d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('deployments') as batch_op:
        batch_op.add_column(sa.Column('price_cached_input_per_mtok', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('price_cache_write_per_mtok', sa.Float(), nullable=True))

    with op.batch_alter_table('usage_records') as batch_op:
        batch_op.add_column(sa.Column('cache_write_tokens', sa.Integer(), nullable=True))

    with op.batch_alter_table('pricing_snapshots') as batch_op:
        batch_op.add_column(sa.Column('price_cache_write_per_mtok', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('pricing_snapshots') as batch_op:
        batch_op.drop_column('price_cache_write_per_mtok')

    with op.batch_alter_table('usage_records') as batch_op:
        batch_op.drop_column('cache_write_tokens')

    with op.batch_alter_table('deployments') as batch_op:
        batch_op.drop_column('price_cache_write_per_mtok')
        batch_op.drop_column('price_cached_input_per_mtok')
