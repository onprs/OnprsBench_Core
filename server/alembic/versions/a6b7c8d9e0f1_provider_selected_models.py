"""Provider 已选模型

- provider_model_catalogs 新增 selected_models：用户在渠道页勾选的模型，
  部署创建只从已选模型中选择。

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-10-07 05:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a6b7c8d9e0f1'
down_revision: str | None = 'f5a6b7c8d9e0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('provider_model_catalogs') as batch_op:
        batch_op.add_column(sa.Column('selected_models', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('provider_model_catalogs') as batch_op:
        batch_op.drop_column('selected_models')
