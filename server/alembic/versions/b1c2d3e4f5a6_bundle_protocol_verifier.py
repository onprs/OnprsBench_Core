"""bundle protocol 对齐与程序判定（verifier）

- tasks 表对齐 Dataset Protocol v1（bundle 形态）：新增 title/suite_id/status/
  difficulty/flagship/task_path，移除 domains（协议元数据中不存在该字段）。
- runs 表新增 verifier_wall_time_s（判定阶段 wall time）。
- 新增 verifier_executions 表（程序判定的 immutable raw facts）。

Revision ID: b1c2d3e4f5a6
Revises: 55b275d09a81
Create Date: 2026-10-06 03:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: str | None = '55b275d09a81'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'verifier_executions',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=False),
        sa.Column('solver_execution_id', sa.String(length=32), nullable=False),
        sa.Column('verifier_kind', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('wall_time_s', sa.Float(), nullable=True),
        sa.Column('facts_json', sa.JSON(), nullable=True),
        sa.Column('environment_json', sa.JSON(), nullable=True),
        sa.Column('log_tail', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
        sa.ForeignKeyConstraint(['solver_execution_id'], ['solver_executions.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_verifier_executions_run_id'), 'verifier_executions', ['run_id'], unique=False)
    op.create_index(op.f('ix_verifier_executions_solver_execution_id'), 'verifier_executions', ['solver_execution_id'], unique=False)
    op.create_index(op.f('ix_verifier_executions_status'), 'verifier_executions', ['status'], unique=False)

    op.add_column('runs', sa.Column('verifier_wall_time_s', sa.Float(), nullable=True))

    # SQLite 列级变更走 batch 模式重建表
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.add_column(sa.Column('title', sa.String(length=512), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('suite_id', sa.String(length=255), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('status', sa.String(length=32), nullable=False, server_default='active'))
        batch_op.add_column(sa.Column('difficulty', sa.String(length=32), nullable=False, server_default='unknown'))
        batch_op.add_column(sa.Column('flagship', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('task_path', sa.String(length=1024), nullable=False, server_default=''))
        batch_op.drop_column('domains')
    op.create_index(op.f('ix_tasks_suite_id'), 'tasks', ['suite_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tasks_suite_id'), table_name='tasks')
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.add_column(sa.Column('domains', sa.JSON(), nullable=True))
        batch_op.drop_column('task_path')
        batch_op.drop_column('flagship')
        batch_op.drop_column('difficulty')
        batch_op.drop_column('status')
        batch_op.drop_column('suite_id')
        batch_op.drop_column('title')
    op.drop_column('runs', 'verifier_wall_time_s')
    op.drop_index(op.f('ix_verifier_executions_status'), table_name='verifier_executions')
    op.drop_index(op.f('ix_verifier_executions_solver_execution_id'), table_name='verifier_executions')
    op.drop_index(op.f('ix_verifier_executions_run_id'), table_name='verifier_executions')
    op.drop_table('verifier_executions')
