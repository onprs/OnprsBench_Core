"""数据集分发形态（standard / full）

- dataset_installations 新增 distribution：standard（判定资源按需下载）/
  full（附带 resources，安装时注册到本地缓存，判定不联网）。
- 历史安装记录默认 standard（server_default），与旧产物行为一致。

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-10-07 01:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'd3e4f5a6b7c8'
down_revision: str | None = 'c2d3e4f5a6b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('dataset_installations') as batch_op:
        batch_op.add_column(
            sa.Column(
                'distribution',
                sa.String(length=16),
                nullable=False,
                server_default='standard',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('dataset_installations') as batch_op:
        batch_op.drop_column('distribution')
