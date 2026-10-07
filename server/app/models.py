"""SQLAlchemy ORM 模型。

核心约束：
- Model / Provider / Deployment 三分离，model_name 不作为任何实体唯一身份。
- runs / solver_executions / judge_executions / usage_records / pricing_snapshots /
  config_snapshots 中的原始事实一经写入不得覆盖（immutable raw facts）。
- 所有统计指标为 derived data，可在不重跑 Solver 的情况下重算。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(sa.TypeDecorator):
    """SQLite 无时区支持：写入统一转 UTC naive，读出统一附加 UTC。"""

    impl = sa.DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


# ---------------------------------------------------------------------------
# Model / Provider / Deployment 三分离
# ---------------------------------------------------------------------------


class Model(Base):
    """规范化模型身份（与供应商无关）。"""

    __tablename__ = "models"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    canonical_id: Mapped[str] = mapped_column(sa.String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(sa.String(255))
    family: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    generation: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    deployments: Mapped[list["Deployment"]] = relationship(back_populates="model")


class Provider(Base):
    """API 渠道。credential_ref 指向系统安全存储（keyring），不明文入库。"""

    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(255))
    type: Mapped[str] = mapped_column(sa.String(64))  # openai / anthropic / gemini / openrouter / deepseek / openai_compatible / ollama / mock
    base_url: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    deployments: Mapped[list["Deployment"]] = relationship(back_populates="provider")


class ProviderModelCatalog(Base):
    """Provider 可用模型列表的最近一次拉取结果。

    保留已拉取列表供界面直接展示（不重复请求上游）；fetched_at 为拉取时间，
    models 为上游返回的模型 ID 列表。每个 Provider 至多一条记录。
    """

    __tablename__ = "provider_model_catalogs"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(sa.ForeignKey("providers.id"), unique=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    models: Mapped[list] = mapped_column(sa.JSON, default=list)
    # 用户在渠道页勾选的模型（部署只从已选模型中选择）
    selected_models: Mapped[list] = mapped_column(sa.JSON, default=list)


class Deployment(Base):
    """Evaluation Target 的载体：Model × Provider × endpoint × options。"""

    __tablename__ = "deployments"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(255))
    model_id: Mapped[str] = mapped_column(sa.ForeignKey("models.id"), index=True)
    provider_id: Mapped[str] = mapped_column(sa.ForeignKey("providers.id"), index=True)
    api_model_name: Mapped[str] = mapped_column(sa.String(255))
    endpoint_override: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    custom_options: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    # 用户级价格 override（美元 / 百万 token），优先级最高
    price_input_per_mtok: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    price_output_per_mtok: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    # 缓存读取 / 写入单价（Anthropic 风格缓存计费）
    price_cached_input_per_mtok: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    price_cache_write_per_mtok: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    model: Mapped[Model] = relationship(back_populates="deployments")
    provider: Mapped[Provider] = relationship(back_populates="deployments")
    reasoning_profiles: Mapped[list["ReasoningProfile"]] = relationship(back_populates="deployment")


class ReasoningProfile(Base):
    """同一 Deployment 的不同思考强度 / 推理配置。"""

    __tablename__ = "reasoning_profiles"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    deployment_id: Mapped[str] = mapped_column(sa.ForeignKey("deployments.id"), index=True)
    name: Mapped[str] = mapped_column(sa.String(255))
    reasoning_effort: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    reasoning_budget: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    temperature: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    top_p: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    seed: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    provider_params: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    # Agent 形态 Solver 的最大工具循环轮次：NULL = 框架默认；0 = 不限制；>0 = 上限
    agent_max_turns: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    deployment: Mapped[Deployment] = relationship(back_populates="reasoning_profiles")


# ---------------------------------------------------------------------------
# 数据集安装与 task 元数据缓存
# ---------------------------------------------------------------------------


class DatasetInstallation(Base):
    """一次数据集安装。相同 manifest_hash 重复安装会复用已有记录。

    source_path 指向安装时复制到数据目录的托管副本，与原始目录解耦，
    保证原始目录移动/删除后历史 Run 仍可追溯与重新判定。
    """

    __tablename__ = "dataset_installations"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(sa.String(255), index=True)
    dataset_name: Mapped[str] = mapped_column(sa.String(255))
    dataset_version: Mapped[str] = mapped_column(sa.String(64))
    dataset_revision: Mapped[str] = mapped_column(sa.String(255))
    protocol_version: Mapped[str] = mapped_column(sa.String(16))
    manifest_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    source_path: Mapped[str] = mapped_column(sa.String(2048))
    # 分发形态：standard（判定资源按需下载）/ full（附带资源，可离线判定）
    distribution: Mapped[str] = mapped_column(
        sa.String(16), default="standard", server_default="standard"
    )
    suites: Mapped[list] = mapped_column(sa.JSON)  # [{id, name, description, layer, adapter, task_ids}]
    capabilities: Mapped[list] = mapped_column(sa.JSON, default=list)
    installed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    tasks: Mapped[list["TaskCache"]] = relationship(back_populates="installation")


class TaskCache(Base):
    """task 元数据缓存；payload 为按 bundle 组装的完整 task 快照。

    payload 结构（由 loader 从 task bundle 文件组装）：
      id / revision / title / type / tags / status / suite_id / path
      solver_visible: { problem, assets: [{path, sha256}] }
      judge_visible: { reference, rubric, anchors: [{score, text}], judge_assets: [path] }
      verify: verify.yaml 解析结果（可选）
      metadata: { difficulty, freshness, contamination, flagship, meta: meta.yaml 全文 }
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    installation_id: Mapped[str] = mapped_column(sa.ForeignKey("dataset_installations.id"), index=True)
    task_id: Mapped[str] = mapped_column(sa.String(255), index=True)
    revision: Mapped[int] = mapped_column(sa.Integer)
    task_hash: Mapped[str] = mapped_column(sa.String(64))  # 协议 bundle_sha256
    title: Mapped[str] = mapped_column(sa.String(512), default="")
    suite_id: Mapped[str] = mapped_column(sa.String(255), default="", index=True)
    type: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(32), default="active")
    tags: Mapped[list] = mapped_column(sa.JSON, default=list)
    difficulty: Mapped[str] = mapped_column(sa.String(32), default="unknown")
    flagship: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    contamination: Mapped[str] = mapped_column(sa.String(32))
    freshness: Mapped[str] = mapped_column(sa.String(64))
    task_path: Mapped[str] = mapped_column(sa.String(1024), default="")  # 相对数据集根的 bundle 路径
    payload: Mapped[dict] = mapped_column(sa.JSON)

    installation: Mapped[DatasetInstallation] = relationship(back_populates="tasks")

    __table_args__ = (sa.UniqueConstraint("installation_id", "task_id", "revision"),)


# ---------------------------------------------------------------------------
# Run 与执行记录（immutable raw facts）
# ---------------------------------------------------------------------------


class ConfigSnapshot(Base):
    """Run 创建时冻结的全部配置（solver/judge 目标、prompt 模板版本、聚合版本等）。"""

    __tablename__ = "config_snapshots"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    payload: Mapped[dict] = mapped_column(sa.JSON)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(255))
    status: Mapped[str] = mapped_column(sa.String(32), default="pending", index=True)
    # pending / running / completed / failed

    # 追溯字段（冗余自 config snapshot，便于查询）
    framework_version: Mapped[str] = mapped_column(sa.String(64))
    framework_commit: Mapped[str] = mapped_column(sa.String(64))
    dataset_id: Mapped[str] = mapped_column(sa.String(255), index=True)
    dataset_version: Mapped[str] = mapped_column(sa.String(64))
    dataset_revision: Mapped[str] = mapped_column(sa.String(255))
    manifest_hash: Mapped[str] = mapped_column(sa.String(64))
    suite_id: Mapped[str] = mapped_column(sa.String(255))

    installation_id: Mapped[str] = mapped_column(sa.ForeignKey("dataset_installations.id"))
    config_snapshot_id: Mapped[str] = mapped_column(sa.ForeignKey("config_snapshots.id"))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # 聚合成本与耗时（derived cache，可从 usage_records 重算）
    solver_wall_time_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    verifier_wall_time_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    judge_wall_time_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    total_wall_time_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    solver_cost: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    judge_cost: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    total_cost: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    solver_executions: Mapped[list["SolverExecution"]] = relationship(back_populates="run")


class SolverExecution(Base):
    """一次 Solver × Task 调用。prompt / response / usage 为 immutable。"""

    __tablename__ = "solver_executions"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("runs.id"), index=True)
    task_cache_id: Mapped[str] = mapped_column(sa.ForeignKey("tasks.id"), index=True)
    task_id: Mapped[str] = mapped_column(sa.String(255), index=True)  # 逻辑 task id
    task_revision: Mapped[int] = mapped_column(sa.Integer)
    task_hash: Mapped[str] = mapped_column(sa.String(64))

    deployment_id: Mapped[str] = mapped_column(sa.ForeignKey("deployments.id"), index=True)
    reasoning_profile_id: Mapped[str | None] = mapped_column(sa.ForeignKey("reasoning_profiles.id"), nullable=True)
    # 冻结的展示信息，deployment 删除后历史仍可读
    deployment_label: Mapped[str] = mapped_column(sa.String(512))
    profile_name: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)

    status: Mapped[str] = mapped_column(sa.String(32), default="pending", index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ttft_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    generation_time_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    total_latency_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    # 实际花费的模型调用轮次（oneshot = 1，agent = 工具循环轮次）
    turns: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    # 最后一次模型调用的结束原因（stop / length / tool_calls / …）
    finish_reason: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    # 是否发生过输出预算耗尽（任一调用 finish_reason == length）
    truncated: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False, server_default=sa.false())

    prompt_json: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)  # 发给 solver 的 messages
    response_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    raw_response_json: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    run: Mapped[Run] = relationship(back_populates="solver_executions")
    judge_executions: Mapped[list["JudgeExecution"]] = relationship(back_populates="solver_execution")
    verifier_executions: Mapped[list["VerifierExecution"]] = relationship(back_populates="solver_execution")


class JudgeExecution(Base):
    """一次 Judge 对某个 SolverExecution 的评分。raw output 必须保存。"""

    __tablename__ = "judge_executions"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("runs.id"), index=True)
    solver_execution_id: Mapped[str] = mapped_column(sa.ForeignKey("solver_executions.id"), index=True)

    deployment_id: Mapped[str] = mapped_column(sa.ForeignKey("deployments.id"), index=True)
    reasoning_profile_id: Mapped[str | None] = mapped_column(sa.ForeignKey("reasoning_profiles.id"), nullable=True)
    deployment_label: Mapped[str] = mapped_column(sa.String(512))
    profile_name: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)

    status: Mapped[str] = mapped_column(sa.String(32), default="pending", index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    total_latency_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    rubric_version: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    prompt_json: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)
    raw_output_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    raw_response_json: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    # 解析后的结构化评分（immutable：本次 judge 输出的事实）
    parse_ok: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    fatal_error: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    dimension_scores: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)  # {dim: score}
    judge_summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    key_errors: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)

    # derived cache：可重算，不重写原始输出
    aggregation_version: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    weighted_total: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    solver_execution: Mapped[SolverExecution] = relationship(back_populates="judge_executions")


class VerifierExecution(Base):
    """一次程序判定（对某个 SolverExecution 执行 verify 契约）。

    facts_json / environment_json / log_tail 为 immutable raw facts：
    判定输入（task hash、solver response）与输出（测试/对拍结果）一经写入不覆盖。
    重评（rejudge）复用已有判定事实，不重新执行。
    """

    __tablename__ = "verifier_executions"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("runs.id"), index=True)
    solver_execution_id: Mapped[str] = mapped_column(sa.ForeignKey("solver_executions.id"), index=True)
    verifier_kind: Mapped[str] = mapped_column(sa.String(32))  # swe_issue / algorithm
    status: Mapped[str] = mapped_column(sa.String(32), default="running", index=True)
    # running / completed / failed / unavailable（工具链无法供给时降级）
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    wall_time_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    facts_json: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    environment_json: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    log_tail: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    solver_execution: Mapped[SolverExecution] = relationship(back_populates="verifier_executions")


class UsageRecord(Base):
    """单次模型调用的 token / cost 记录。pricing snapshot 随记录冻结。"""

    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("runs.id"), index=True)
    owner_type: Mapped[str] = mapped_column(sa.String(16))  # solver / judge
    owner_id: Mapped[str] = mapped_column(sa.String(32), index=True)  # execution id
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    input_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    pricing_snapshot_id: Mapped[str | None] = mapped_column(sa.ForeignKey("pricing_snapshots.id"), nullable=True)
    cost: Mapped[float | None] = mapped_column(sa.Float, nullable=True)


class PricingSnapshot(Base):
    """Run 时刻的价格快照。历史 cost 绝不按未来价格重算。"""

    __tablename__ = "pricing_snapshots"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    deployment_id: Mapped[str | None] = mapped_column(sa.ForeignKey("deployments.id"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(sa.String(32))  # manual_override / models_dev / litellm_cost_map / unknown
    price_input_per_mtok: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    price_output_per_mtok: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    price_cached_input_per_mtok: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    price_cache_write_per_mtok: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    currency: Mapped[str] = mapped_column(sa.String(8), default="USD")
    raw_json: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AggregationVersion(Base):
    """派生指标聚合算法版本。修改权重/算法时新增版本，历史可重算。"""

    __tablename__ = "aggregation_versions"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(128))
    version: Mapped[str] = mapped_column(sa.String(64))
    definition: Mapped[dict] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (sa.UniqueConstraint("name", "version"),)
