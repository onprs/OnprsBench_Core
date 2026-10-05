"""服务端配置：数据目录、数据库路径。全部本地优先，默认落在用户数据目录。"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


def default_data_dir() -> Path:
    """默认数据目录：可用 ONPRSBENCH_DATA_DIR 覆盖（测试与便携场景）。"""
    env = os.environ.get("ONPRSBENCH_DATA_DIR")
    if env:
        return Path(env)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "OnprsBench"
    return Path.home() / ".local" / "share" / "onprsbench"


class Settings(BaseModel):
    data_dir: Path
    database_url: str
    protocol_schema_path: Path
    # Solver 并发度（同一 Run 内并行的 solver 调用上限）
    solver_concurrency: int = 4
    # 单次模型调用超时（秒）
    llm_timeout_s: float = 600.0


def load_settings() -> Settings:
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    return Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'onprsbench.db'}",
        protocol_schema_path=repo_root / "protocol" / "schema" / "dataset-protocol-v1.schema.json",
    )


settings = load_settings()
