"""Provider / Model / Deployment / ReasoningProfile API。"""

from __future__ import annotations

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Deployment,
    JudgeExecution,
    Model,
    Provider,
    ProviderModelCatalog,
    ReasoningProfile,
    SolverExecution,
    utcnow,
)
from ..runtime.factory import PROVIDER_TYPE_DEFAULTS
from ..schemas import (
    DeploymentCreate,
    DeploymentUpdate,
    ModelCreate,
    ProviderCreate,
    ProviderUpdate,
    ReasoningProfileCreate,
)
from ..services import credentials
from ..services import pricing

router = APIRouter(prefix="/api", tags=["setup"])


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def provider_dict(p: Provider, catalog: ProviderModelCatalog | None = None) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "type": p.type,
        "base_url": p.base_url,
        "has_credential": credentials.has_credential(p.credential_ref),
        "created_at": p.created_at,
        # 已拉取的模型列表摘要（供界面展示“再次拉取”与查看缓存）
        "model_catalog_count": len(catalog.models) if catalog is not None else 0,
        "model_catalog_fetched_at": catalog.fetched_at if catalog is not None else None,
    }


def model_dict(m: Model) -> dict:
    return {
        "id": m.id,
        "canonical_id": m.canonical_id,
        "display_name": m.display_name,
        "family": m.family,
        "generation": m.generation,
        "notes": m.notes,
    }


def deployment_dict(d: Deployment) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "model_id": d.model_id,
        "model_display_name": d.model.display_name if d.model else None,
        "model_canonical_id": d.model.canonical_id if d.model else None,
        "provider_id": d.provider_id,
        "provider_name": d.provider.name if d.provider else None,
        "provider_type": d.provider.type if d.provider else None,
        "api_model_name": d.api_model_name,
        "endpoint_override": d.endpoint_override,
        "custom_options": d.custom_options,
        "price_input_per_mtok": d.price_input_per_mtok,
        "price_output_per_mtok": d.price_output_per_mtok,
        "price_cached_input_per_mtok": d.price_cached_input_per_mtok,
        "price_cache_write_per_mtok": d.price_cache_write_per_mtok,
        "created_at": d.created_at,
    }


def profile_dict(p: ReasoningProfile) -> dict:
    return {
        "id": p.id,
        "deployment_id": p.deployment_id,
        "name": p.name,
        "reasoning_effort": p.reasoning_effort,
        "reasoning_budget": p.reasoning_budget,
        "max_output_tokens": p.max_output_tokens,
        "temperature": p.temperature,
        "top_p": p.top_p,
        "seed": p.seed,
        "provider_params": p.provider_params,
        "agent_max_turns": p.agent_max_turns,
    }


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


@router.get("/provider-types")
def list_provider_types() -> dict:
    return {"types": [{"type": k, **v} for k, v in PROVIDER_TYPE_DEFAULTS.items()]}


@router.get("/providers")
def list_providers(db: Session = Depends(get_db)) -> list[dict]:
    providers = db.scalars(sa.select(Provider).order_by(Provider.created_at)).all()
    catalogs = {
        row.provider_id: row
        for row in db.scalars(sa.select(ProviderModelCatalog)).all()
    }
    return [provider_dict(p, catalogs.get(p.id)) for p in providers]


@router.post("/providers", status_code=201)
def create_provider(body: ProviderCreate, db: Session = Depends(get_db)) -> dict:
    if body.type not in PROVIDER_TYPE_DEFAULTS:
        raise HTTPException(400, f"未知 provider type: {body.type}")
    defaults = PROVIDER_TYPE_DEFAULTS[body.type]
    base_url = body.base_url or defaults.get("base_url")

    credential_ref = None
    if body.api_key:
        credential_ref = credentials.store_api_key(body.api_key)
    elif defaults.get("needs_key") and body.type != "mock":
        # 允许先创建后补 key，但明确提示
        pass

    provider = Provider(name=body.name, type=body.type, base_url=base_url, credential_ref=credential_ref)
    db.add(provider)
    db.commit()
    return provider_dict(provider)


@router.patch("/providers/{provider_id}")
def update_provider(provider_id: str, body: ProviderUpdate, db: Session = Depends(get_db)) -> dict:
    provider = db.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(404, "渠道不存在")
    if body.name is not None:
        provider.name = body.name
    if body.base_url is not None:
        provider.base_url = body.base_url
    if body.api_key:
        if provider.credential_ref:
            credentials.delete_api_key(provider.credential_ref)
        provider.credential_ref = credentials.store_api_key(body.api_key)
    db.commit()
    return provider_dict(provider)


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: str, db: Session = Depends(get_db)) -> None:
    provider = db.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(404, "渠道不存在")
    used = db.scalar(sa.select(sa.func.count()).select_from(Deployment).where(Deployment.provider_id == provider_id))
    if used:
        raise HTTPException(409, "渠道仍被部署引用，不能删除")
    if provider.credential_ref:
        credentials.delete_api_key(provider.credential_ref)
    db.query(ProviderModelCatalog).filter(ProviderModelCatalog.provider_id == provider_id).delete()
    db.delete(provider)
    db.commit()


def _fetch_provider_models(provider: Provider) -> list[str]:
    """向上游请求可用模型列表（OpenAI 风格 GET /models）。"""
    if provider.type == "mock":
        return ["mock-strong", "mock-weak"]

    base = (provider.base_url or "").rstrip("/")
    if not base:
        raise HTTPException(400, "该渠道未配置接口地址，无法拉取模型列表")

    headers: dict[str, str] = {}
    api_key = credentials.get_api_key(provider.credential_ref) if provider.credential_ref else None
    if api_key:
        if provider.type == "anthropic":
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = httpx.get(f"{base}/models", headers=headers, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise HTTPException(502, f"拉取模型列表失败: {exc}") from exc

    items = data.get("data", [])
    return sorted(str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id"))


@router.get("/providers/{provider_id}/models")
def list_provider_models(provider_id: str, refresh: bool = False, db: Session = Depends(get_db)) -> dict:
    """返回 Provider 的可用模型列表。

    默认优先返回上次拉取的缓存（from_cache=true），界面无需重复请求上游；
    refresh=true 强制重新拉取并更新缓存；首次访问（无缓存）自动拉取并保存。
    """
    provider = db.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(404, "渠道不存在")

    catalog = db.scalar(
        sa.select(ProviderModelCatalog).where(ProviderModelCatalog.provider_id == provider_id)
    )
    if not refresh and catalog is not None:
        return {
            "models": catalog.models,
            "fetched_at": catalog.fetched_at,
            "from_cache": True,
        }

    models = _fetch_provider_models(provider)
    if catalog is None:
        catalog = ProviderModelCatalog(provider_id=provider_id, models=models, fetched_at=utcnow())
        db.add(catalog)
    else:
        catalog.models = models
        catalog.fetched_at = utcnow()
    db.commit()
    db.refresh(catalog)
    return {"models": models, "fetched_at": catalog.fetched_at, "from_cache": False}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@router.get("/models")
def list_models(db: Session = Depends(get_db)) -> list[dict]:
    return [model_dict(m) for m in db.scalars(sa.select(Model).order_by(Model.canonical_id))]


@router.post("/models", status_code=201)
def create_model(body: ModelCreate, db: Session = Depends(get_db)) -> dict:
    existing = db.scalar(sa.select(Model).where(Model.canonical_id == body.canonical_id))
    if existing:
        return model_dict(existing)
    model = Model(**body.model_dump())
    db.add(model)
    db.commit()
    return model_dict(model)


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------


@router.get("/pricing/preview")
def pricing_preview(provider_id: str, api_model_name: str, db: Session = Depends(get_db)) -> dict:
    """按渠道 + 模型名预览价格与模型能力（部署表单自动填充用）。

    价格优先级与 Run 时的解析一致（手动 override 在创建时由用户填写，不参与预览）。
    capabilities 为 None 表示价格目录中没有该模型，界面不做能力限制。
    """
    provider = db.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(404, "渠道不存在")
    resolved = pricing.resolve_pricing_for(
        provider_type=provider.type,
        api_model_name=api_model_name,
    )
    return {
        "source": resolved.source,
        "price_input_per_mtok": resolved.price_input_per_mtok,
        "price_output_per_mtok": resolved.price_output_per_mtok,
        "price_cached_input_per_mtok": resolved.price_cached_input_per_mtok,
        "price_cache_write_per_mtok": resolved.price_cache_write_per_mtok,
        "capabilities": pricing.model_capabilities(api_model_name),
    }


@router.get("/deployments")
def list_deployments(db: Session = Depends(get_db)) -> list[dict]:
    return [deployment_dict(d) for d in db.scalars(sa.select(Deployment).order_by(Deployment.created_at))]


@router.post("/deployments", status_code=201)
def create_deployment(body: DeploymentCreate, db: Session = Depends(get_db)) -> dict:
    if db.get(Model, body.model_id) is None:
        raise HTTPException(404, "模型不存在")
    if db.get(Provider, body.provider_id) is None:
        raise HTTPException(404, "渠道不存在")
    deployment = Deployment(**body.model_dump())
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return deployment_dict(deployment)


@router.patch("/deployments/{deployment_id}")
def update_deployment(deployment_id: str, body: DeploymentUpdate, db: Session = Depends(get_db)) -> dict:
    deployment = db.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(404, "部署不存在")
    for field_name, value in body.model_dump(exclude_unset=True).items():
        setattr(deployment, field_name, value)
    db.commit()
    db.refresh(deployment)
    return deployment_dict(deployment)


@router.delete("/deployments/{deployment_id}", status_code=204)
def delete_deployment(deployment_id: str, db: Session = Depends(get_db)) -> None:
    deployment = db.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(404, "部署不存在")
    # SQLite 默认不强制外键：显式检查，避免历史 Run 出现悬空引用
    used = db.scalar(
        sa.select(sa.func.count())
        .select_from(SolverExecution)
        .where(SolverExecution.deployment_id == deployment_id)
    ) + db.scalar(
        sa.select(sa.func.count())
        .select_from(JudgeExecution)
        .where(JudgeExecution.deployment_id == deployment_id)
    )
    if used:
        raise HTTPException(409, "部署已被历史评测使用，为保持可追溯性不能删除")
    db.query(ReasoningProfile).filter(ReasoningProfile.deployment_id == deployment_id).delete()
    db.delete(deployment)
    db.commit()


# ---------------------------------------------------------------------------
# ReasoningProfile
# ---------------------------------------------------------------------------


@router.get("/reasoning-profiles")
def list_reasoning_profiles(deployment_id: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    query = sa.select(ReasoningProfile)
    if deployment_id:
        query = query.where(ReasoningProfile.deployment_id == deployment_id)
    return [profile_dict(p) for p in db.scalars(query)]


@router.post("/reasoning-profiles", status_code=201)
def create_reasoning_profile(body: ReasoningProfileCreate, db: Session = Depends(get_db)) -> dict:
    if db.get(Deployment, body.deployment_id) is None:
        raise HTTPException(404, "部署不存在")
    profile = ReasoningProfile(**body.model_dump())
    db.add(profile)
    db.commit()
    return profile_dict(profile)


@router.delete("/reasoning-profiles/{profile_id}", status_code=204)
def delete_reasoning_profile(profile_id: str, db: Session = Depends(get_db)) -> None:
    profile = db.get(ReasoningProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "推理配置不存在")
    used = db.scalar(
        sa.select(sa.func.count())
        .select_from(SolverExecution)
        .where(SolverExecution.reasoning_profile_id == profile_id)
    ) + db.scalar(
        sa.select(sa.func.count())
        .select_from(JudgeExecution)
        .where(JudgeExecution.reasoning_profile_id == profile_id)
    )
    if used:
        raise HTTPException(409, "reasoning profile 已被历史 Run 使用，为保持可追溯性不能删除")
    db.delete(profile)
    db.commit()
