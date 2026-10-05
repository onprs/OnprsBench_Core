"""ModelClient 工厂：根据 Deployment + Provider 构建客户端。"""

from __future__ import annotations

from ..models import Deployment, Provider
from ..services.credentials import get_api_key
from .base import ModelClient
from .litellm_client import LiteLLMClient, build_litellm_model_string
from .mock_client import MockClient

# 内置 provider 类型的默认 base_url
PROVIDER_TYPE_DEFAULTS: dict[str, dict] = {
    "openai": {"label": "OpenAI", "base_url": None, "needs_key": True},
    "anthropic": {"label": "Anthropic", "base_url": None, "needs_key": True},
    "gemini": {"label": "Google Gemini", "base_url": None, "needs_key": True},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "needs_key": True},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com", "needs_key": True},
    "openai_compatible": {"label": "OpenAI Compatible", "base_url": None, "needs_key": False},
    "ollama": {"label": "Ollama", "base_url": "http://localhost:11434", "needs_key": False},
    "mock": {"label": "Mock（离线演示/测试）", "base_url": None, "needs_key": False},
}


def build_client(deployment: Deployment, provider: Provider) -> ModelClient:
    """根据 Deployment 配置构建模型客户端。"""
    if provider.type == "mock":
        # mock 延迟可通过 deployment custom_options 调节（如 {"mock_latency_s": 1.0}）
        latency = float(deployment.custom_options.get("mock_latency_s", 0.05))
        return MockClient(api_model_name=deployment.api_model_name, latency_s=latency)

    api_key = get_api_key(provider.credential_ref) if provider.credential_ref else None
    api_base = deployment.endpoint_override or provider.base_url
    return LiteLLMClient(
        litellm_model=build_litellm_model_string(provider.type, deployment.api_model_name),
        api_base=api_base,
        api_key=api_key,
        custom_options=deployment.custom_options,
    )
