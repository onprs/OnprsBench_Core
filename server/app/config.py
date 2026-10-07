"""服务端配置：数据目录、数据库路径、程序判定（verifier）与工具链供给。全部本地优先。"""

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
    # 单次模型调用的 HTTP/流超时（秒）
    llm_timeout_s: float = 600.0
    # 单次模型调用的墙钟上限（秒，含长思考与重试）；0 = 不限制
    llm_call_timeout_s: float = 1800.0

    # ---- Agent 形态 Solver（统一执行形态） ----
    # 关闭后所有任务退化为单轮问答（仅用于故障排查）
    agent_solver_enabled: bool = True
    # 未在 Reasoning Profile 中配置时的默认最大工具循环轮次
    agent_max_turns: int = 40
    # agent 单条命令超时（秒）
    agent_command_timeout_s: int = 120

    # ---- Agent 命令沙箱 ----
    # enforce：命令白名单 / 参数校验 / 环境清理 / Python 网络限制全部生效（拒绝违规命令）
    # audit：只记录不阻断（调试与兼容模式）
    agent_sandbox_mode: str = "enforce"

    # ---- 程序判定（verifier） ----
    # 总开关；关闭后所有任务退化为纯 LLM 评分
    verifier_enabled: bool = True
    # 判定并发度（每个判定会编译/跑测试，消耗 CPU 与磁盘）
    verifier_concurrency: int = 2
    # 单次判定整体超时（秒），含环境准备
    verify_timeout_s: float = 1800.0
    # 单次测试/编译子进程超时（秒）
    verify_step_timeout_s: float = 600.0
    # 竞赛题应力测试：种子批次数 × 每批用例数
    algorithm_stress_seeds: int = 3
    algorithm_stress_cases: int = 100
    # 工具脚本（生成器等）使用的默认 Python 版本
    verifier_default_python: str = "3.12"

    # ---- 服务端口 ----
    # 可用 ONPRSBENCH_PORT 覆盖（验证/多实例场景需与正式实例隔离）
    port: int = 8765

    # ---- 工具链供给（自带环境，不要求用户预装） ----
    # 下载超时（秒）与镜像基址（None = GitHub 官方地址）
    toolchain_download_timeout_s: float = 600.0

    @property
    def toolchain_dir(self) -> Path:
        """自带工具链（uv、便携 MinGW 等）存放目录。"""
        return self.data_dir / "toolchains"

    @property
    def datasets_dir(self) -> Path:
        """安装数据集的托管副本目录（与源目录解耦，保证历史可追溯）。"""
        return self.data_dir / "datasets"

    @property
    def cache_dir(self) -> Path:
        """仓库归档、参考解编译产物等缓存目录。"""
        return self.data_dir / "cache"

    @property
    def verify_workspace_dir(self) -> Path:
        """每次程序判定的临时工作区根目录。"""
        return self.data_dir / "verify_workspaces"


def load_settings() -> Settings:
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    return Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'onprsbench.db'}",
        protocol_schema_path=repo_root / "protocol" / "schema" / "dataset-protocol-v1.schema.json",
        port=int(os.environ.get("ONPRSBENCH_PORT", "8765")),
    )


settings = load_settings()
