"""Agent 轮次预算、花费轮次与截断标记

- reasoning_profiles 新增 agent_max_turns：Agent 形态 Solver 的最大工具循环轮次
  （NULL = 框架默认，0 = 不限制，>0 = 上限）。
- solver_executions 新增 turns（实际花费轮次）、finish_reason（末次调用结束原因）、
  truncated（是否发生过输出预算耗尽），用于把截断作为一等事实记录与对比。

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-10-06 23:30:00.000000

"""
from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = 'c2d3e4f5a6b7'
down_revision: str | None = 'b1c2d3e4f5a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('reasoning_profiles') as batch_op:
        batch_op.add_column(sa.Column('agent_max_turns', sa.Integer(), nullable=True))

    with op.batch_alter_table('solver_executions') as batch_op:
        batch_op.add_column(sa.Column('turns', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('finish_reason', sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column('truncated', sa.Boolean(), nullable=False, server_default=sa.false())
        )

    _backfill_solver_facts()


def _backfill_solver_facts() -> None:
    """从历史 raw_response_json 派生回填 turns / finish_reason / truncated。

    这三项是原始 JSON 的派生事实（不可变原始记录不被改写）；
    回填后历史 Run 的截断与轮次在结果视图与对比中同样可见。
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, status, raw_response_json FROM solver_executions")
    ).fetchall()
    for row_id, status, raw in rows:
        if raw is None:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        finish_reason = data.get("finish_reason")
        mode = data.get("solver_mode")
        if mode == "agent":
            turns = data.get("turns")
        elif status == "completed":
            turns = 1  # oneshot 单轮
        else:
            turns = data.get("turns")
        bind.execute(
            sa.text(
                "UPDATE solver_executions "
                "SET finish_reason = :finish_reason, truncated = :truncated, turns = :turns "
                "WHERE id = :id"
            ),
            {
                "finish_reason": finish_reason,
                "truncated": 1 if finish_reason == "length" else 0,
                "turns": turns,
                "id": row_id,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table('solver_executions') as batch_op:
        batch_op.drop_column('truncated')
        batch_op.drop_column('finish_reason')
        batch_op.drop_column('turns')

    with op.batch_alter_table('reasoning_profiles') as batch_op:
        batch_op.drop_column('agent_max_turns')
