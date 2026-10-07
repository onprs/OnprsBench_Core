"""API 请求模型（Pydantic v2）。响应为 dict 序列化，见 api/ 各 router。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None  # write-only，进入系统 keyring，不入库


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # 提供时替换 keyring 中的 key


class ModelCreate(BaseModel):
    canonical_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    family: str | None = None
    generation: str | None = None
    notes: str | None = None


class DeploymentCreate(BaseModel):
    name: str = Field(min_length=1)
    model_id: str
    provider_id: str
    api_model_name: str = Field(min_length=1)
    endpoint_override: str | None = None
    custom_options: dict = Field(default_factory=dict)
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None
    price_cached_input_per_mtok: float | None = None
    price_cache_write_per_mtok: float | None = None


class DeploymentUpdate(BaseModel):
    name: str | None = None
    api_model_name: str | None = None
    endpoint_override: str | None = None
    custom_options: dict | None = None
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None
    price_cached_input_per_mtok: float | None = None
    price_cache_write_per_mtok: float | None = None


class ReasoningProfileCreate(BaseModel):
    deployment_id: str
    name: str = Field(min_length=1)
    reasoning_effort: str | None = None
    reasoning_budget: int | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    provider_params: dict = Field(default_factory=dict)
    # Agent 最大工具循环轮次：None = 框架默认；0 = 不限制；>0 = 上限
    agent_max_turns: int | None = None


class DatasetInstall(BaseModel):
    path: str = Field(min_length=1)


class TargetSpecIn(BaseModel):
    deployment_id: str
    reasoning_profile_id: str | None = None


class RunCreate(BaseModel):
    name: str = Field(min_length=1)
    installation_id: str
    suite_id: str
    solvers: list[TargetSpecIn] = Field(min_length=1)
    judges: list[TargetSpecIn] = Field(min_length=1)


class RejudgeRequest(BaseModel):
    solver_execution_ids: list[str] | None = None  # None = 该 Run 全部 completed solver executions
    judges: list[TargetSpecIn] | None = None  # None = 沿用 Run 原 judges
