"""数据库引擎与会话。SQLite + SQLAlchemy 2.x。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = sa.create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def run_migrations() -> None:
    """启动时执行 Alembic 迁移（upgrade head）。路径与 cwd 无关。"""
    from alembic import command
    from alembic.config import Config

    server_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(server_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(server_dir / "alembic"))
    command.upgrade(alembic_cfg, "head")


@contextmanager
def session_scope() -> Iterator[Session]:
    """写操作统一入口：提交/回滚/关闭。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：请求级会话。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
