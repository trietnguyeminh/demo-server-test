from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import time
from typing import Any

import httpx

from .config import Settings
from .contracts import PROVIDER_REGISTRY_CONTRACT


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    protocol: str
    base_url: str
    key_env: str | None
    fast_model: str
    quality_model: str
    enabled: bool = True
    token_field: str = "max_tokens"
    supports_json_mode: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_payload: dict[str, Any] = field(default_factory=dict)
    api_version: str | None = None

    @property
    def key_configured(self) -> bool:
        if self.key_env is None:
            return True
        return bool(os.getenv(self.key_env, "").strip())

    @property
    def ready(self) -> bool:
        return self.enabled and self.key_configured and bool(self.base_url)

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "protocol": self.protocol,
            "base_url": self.base_url if self.protocol.startswith("local") else None,
            "ready": self.ready,
            "enabled": self.enabled,
            "key_configured": self.key_configured,
            "fast_model": self.fast_model,
            "quality_model": self.quality_model,
            "supports_model_override": True,
        }


@dataclass
class ProviderCompletion:
    content: str
    provider: str
    model: str
    latency_ms: float
    usage: dict[str, Any] = field(default_factory=dict)


class BaseProvider:
    def __init__(self, spec: ProviderSpec, settings: Settings):
        self.spec = spec
        self.settings = settings

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        json_mode: bool,
        max_tokens: int,
    ) -> ProviderCompletion:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseProvider):
    def __init__(
        self,
        spec: ProviderSpec,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(spec, settings)
        self.client = httpx.Client(
            timeout=httpx.Timeout(settings.agent_timeout_s),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            transport=transport,
        )

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        json_mode: bool,
        max_tokens: int,
    ) -> ProviderCompletion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            self.spec.token_field: max_tokens,
            **self.spec.extra_payload,
        }
        if json_mode and self.spec.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json", **self.spec.extra_headers}
        if self.spec.key_env:
            key = os.getenv(self.spec.key_env, "").strip()
            if not key:
                raise ProviderError(f"Missing secret {self.spec.key_env}")
            headers["Authorization"] = f"Bearer {key}"

        url = f"{self.spec.base_url.rstrip('/')}/chat/completions"
        started = time.perf_counter()
        try:
            response = self.client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"{self.spec.id} timeout after {self.settings.agent_timeout_s:.1f}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.spec.id} network error: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        if response.status_code >= 400:
            raise ProviderError(
                f"{self.spec.id} HTTP {response.status_code}: {response.text[:1500]}"
            )
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderError(
                f"{self.spec.id} response misses choices[0].message.content"
            ) from exc
        usage = dict(data.get("usage") or {})
        actual_model = str(data.get("model") or model)
        return ProviderCompletion(
            content=str(content or ""),
            provider=self.spec.id,
            model=actual_model,
            latency_ms=latency_ms,
            usage=usage,
        )


class AnthropicProvider(BaseProvider):
    def __init__(
        self,
        spec: ProviderSpec,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(spec, settings)
        self.client = httpx.Client(
            timeout=httpx.Timeout(settings.agent_timeout_s),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            transport=transport,
        )

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        json_mode: bool,
        max_tokens: int,
    ) -> ProviderCompletion:
        key = os.getenv(self.spec.key_env or "", "").strip()
        if not key:
            raise ProviderError(f"Missing secret {self.spec.key_env}")

        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        anthropic_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] in {"user", "assistant"}
        ]
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "messages": anthropic_messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        started = time.perf_counter()
        try:
            response = self.client.post(
                f"{self.spec.base_url.rstrip('/')}/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": self.spec.api_version or "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"anthropic timeout after {self.settings.agent_timeout_s:.1f}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic network error: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        if response.status_code >= 400:
            raise ProviderError(
                f"anthropic HTTP {response.status_code}: {response.text[:1500]}"
            )
        data = response.json()
        text_parts = [
            block.get("text", "")
            for block in data.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if not text_parts:
            raise ProviderError("anthropic response contains no text block")
        usage = dict(data.get("usage") or {})
        return ProviderCompletion(
            content="\n".join(text_parts),
            provider=self.spec.id,
            model=str(data.get("model") or model),
            latency_ms=latency_ms,
            usage=usage,
        )


class MockProvider(BaseProvider):
    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        json_mode: bool,
        max_tokens: int,
    ) -> ProviderCompletion:
        user_text = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"),
            "",
        )
        if json_mode:
            marker = "CURRENT_USER_MESSAGE:"
            query = user_text.split(marker, 1)[1].splitlines()[0].strip() \
                if marker in user_text else user_text
            content = json.dumps(
                {
                    "resolved_query": query,
                    "search_required": True,
                    "ocr": "auto",
                    "asr": "auto",
                    "intent": "search",
                    "rationale": "mock structured planner",
                    "assistant_preface": "Tôi sẽ tìm bằng chứng đa phương thức.",
                },
                ensure_ascii=False,
            )
        else:
            content = (
                "Dựa trên evidence retrieval, đây là các candidate phù hợp nhất. "
                "Hãy kiểm tra gallery và timestamp trước khi nộp."
            )
        return ProviderCompletion(
            content=content,
            provider=self.spec.id,
            model=model,
            latency_ms=2.0,
            usage={"mock": True, "max_tokens": max_tokens},
        )


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _model(env_name: str, default: str) -> str:
    return os.getenv(env_name, default).strip()


def builtin_specs(settings: Settings) -> list[ProviderSpec]:
    specs = [
        ProviderSpec(
            "mock", "Mock test provider", "mock", "mock://local", None,
            "mock-fast", "mock-quality", enabled=_bool_env("AIC_MOCK_PROVIDER_ENABLED", False),
        ),
        ProviderSpec(
            "groq", "Groq", "openai", "https://api.groq.com/openai/v1",
            "GROQ_API_KEY", _model("GROQ_FAST_MODEL", "openai/gpt-oss-20b"),
            _model("GROQ_QUALITY_MODEL", "openai/gpt-oss-120b"),
            token_field="max_completion_tokens",
            extra_payload={"service_tier": os.getenv("GROQ_SERVICE_TIER", "on_demand")},
        ),
        ProviderSpec(
            "openai", "OpenAI", "openai", "https://api.openai.com/v1",
            "OPENAI_API_KEY", _model("OPENAI_FAST_MODEL", "gpt-5-mini"),
            _model("OPENAI_QUALITY_MODEL", "gpt-5"),
            token_field="max_completion_tokens",
        ),
        ProviderSpec(
            "gemini", "Google Gemini (OpenAI compatibility)", "openai",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "GEMINI_API_KEY", _model("GEMINI_FAST_MODEL", "gemini-3.5-flash"),
            _model("GEMINI_QUALITY_MODEL", "gemini-3.5-pro"),
        ),
        ProviderSpec(
            "anthropic", "Anthropic", "anthropic", "https://api.anthropic.com/v1",
            "ANTHROPIC_API_KEY", _model("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5"),
            _model("ANTHROPIC_QUALITY_MODEL", "claude-sonnet-4-5"),
            supports_json_mode=False, api_version=os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
        ),
        ProviderSpec(
            "openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY", _model("OPENROUTER_FAST_MODEL", "openrouter/auto"),
            _model("OPENROUTER_QUALITY_MODEL", "openrouter/auto"),
            extra_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://github.com"),
                "X-Title": os.getenv("OPENROUTER_APP_TITLE", "AIC Retrieval Agent"),
            },
        ),
        ProviderSpec(
            "mistral", "Mistral", "openai", "https://api.mistral.ai/v1",
            "MISTRAL_API_KEY", _model("MISTRAL_FAST_MODEL", "mistral-small-latest"),
            _model("MISTRAL_QUALITY_MODEL", "mistral-large-latest"),
        ),
        ProviderSpec(
            "deepseek", "DeepSeek", "openai", "https://api.deepseek.com",
            "DEEPSEEK_API_KEY", _model("DEEPSEEK_FAST_MODEL", "deepseek-v4-flash"),
            _model("DEEPSEEK_QUALITY_MODEL", "deepseek-v4-pro"),
        ),
        ProviderSpec(
            "xai", "xAI", "openai", "https://api.x.ai/v1",
            "XAI_API_KEY", _model("XAI_FAST_MODEL", "grok-3-mini"),
            _model("XAI_QUALITY_MODEL", "grok-3"),
        ),
        ProviderSpec(
            "together", "Together AI", "openai", "https://api.together.ai/v1",
            "TOGETHER_API_KEY", _model("TOGETHER_FAST_MODEL", "openai/gpt-oss-20b"),
            _model("TOGETHER_QUALITY_MODEL", "openai/gpt-oss-120b"),
        ),
        ProviderSpec(
            "fireworks", "Fireworks AI", "openai",
            "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY",
            _model("FIREWORKS_FAST_MODEL", "accounts/fireworks/models/qwen3-30b-a3b"),
            _model("FIREWORKS_QUALITY_MODEL", "accounts/fireworks/models/llama-v3p3-70b-instruct"),
        ),
        ProviderSpec(
            "cerebras", "Cerebras", "openai", "https://api.cerebras.ai/v1",
            "CEREBRAS_API_KEY", _model("CEREBRAS_FAST_MODEL", "gpt-oss-120b"),
            _model("CEREBRAS_QUALITY_MODEL", "gpt-oss-120b"),
        ),
        ProviderSpec(
            "ollama", "Ollama local", "local_openai",
            os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"), None,
            _model("OLLAMA_FAST_MODEL", "qwen3:8b"),
            _model("OLLAMA_QUALITY_MODEL", "qwen3:30b"),
            enabled=_bool_env("OLLAMA_ENABLED", False), supports_json_mode=False,
        ),
        ProviderSpec(
            "lmstudio", "LM Studio local", "local_openai",
            os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"), None,
            _model("LMSTUDIO_FAST_MODEL", "local-model"),
            _model("LMSTUDIO_QUALITY_MODEL", "local-model"),
            enabled=_bool_env("LMSTUDIO_ENABLED", False), supports_json_mode=False,
        ),
        ProviderSpec(
            "vllm", "vLLM local", "local_openai",
            os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8001/v1"), None,
            _model("VLLM_FAST_MODEL", "local-model"),
            _model("VLLM_QUALITY_MODEL", "local-model"),
            enabled=_bool_env("VLLM_ENABLED", False), supports_json_mode=False,
        ),
        ProviderSpec(
            "custom", "Custom OpenAI-compatible", "openai",
            os.getenv("CUSTOM_OPENAI_BASE_URL", "").rstrip("/"),
            "CUSTOM_OPENAI_API_KEY",
            _model("CUSTOM_OPENAI_FAST_MODEL", "custom-fast"),
            _model("CUSTOM_OPENAI_QUALITY_MODEL", "custom-quality"),
            enabled=bool(os.getenv("CUSTOM_OPENAI_BASE_URL", "").strip()),
            supports_json_mode=_bool_env("CUSTOM_OPENAI_JSON_MODE", True),
        ),
    ]

    if settings.agent_custom_providers_json:
        try:
            rows = json.loads(settings.agent_custom_providers_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid AIC_OPENAI_COMPAT_PROVIDERS_JSON: {exc}") from exc
        if not isinstance(rows, list):
            raise ValueError("AIC_OPENAI_COMPAT_PROVIDERS_JSON must be a JSON list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Each custom provider must be an object")
            provider_id = str(row["id"]).strip().lower()
            specs.append(
                ProviderSpec(
                    id=provider_id,
                    label=str(row.get("label") or provider_id),
                    protocol="openai",
                    base_url=str(row["base_url"]).rstrip("/"),
                    key_env=str(row.get("key_env") or f"{provider_id.upper()}_API_KEY"),
                    fast_model=str(row.get("fast_model") or row.get("model") or "model"),
                    quality_model=str(row.get("quality_model") or row.get("model") or "model"),
                    enabled=bool(row.get("enabled", True)),
                    token_field=str(row.get("token_field") or "max_tokens"),
                    supports_json_mode=bool(row.get("supports_json_mode", True)),
                    extra_headers=dict(row.get("extra_headers") or {}),
                    extra_payload=dict(row.get("extra_payload") or {}),
                )
            )
    return specs


class ProviderRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.specs = {spec.id: spec for spec in builtin_specs(settings)}
        self.providers: dict[str, BaseProvider] = {}
        self.errors: dict[str, str] = {}
        if not settings.agent_enabled:
            return
        for spec in self.specs.values():
            if not spec.ready:
                continue
            try:
                if spec.protocol == "mock":
                    provider: BaseProvider = MockProvider(spec, settings)
                elif spec.protocol == "anthropic":
                    provider = AnthropicProvider(spec, settings)
                else:
                    provider = OpenAICompatibleProvider(spec, settings)
                self.providers[spec.id] = provider
            except Exception as exc:
                self.errors[spec.id] = f"{type(exc).__name__}: {exc}"

    @property
    def ready(self) -> bool:
        return bool(self.providers)

    def ready_ids(self) -> list[str]:
        return sorted(self.providers)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.agent_enabled,
            "ready": self.ready,
            "default_provider": self.settings.agent_default_provider,
            "ready_provider_count": len(self.providers),
            "ready_providers": self.ready_ids(),
            "providers": [self.specs[key].public_dict() for key in sorted(self.specs)],
            "errors": dict(self.errors),
            "contract_version": PROVIDER_REGISTRY_CONTRACT,
        }

    def resolve(self, requested: str) -> tuple[BaseProvider, ProviderSpec]:
        provider_id = (requested or "auto").strip().lower()
        if provider_id == "auto":
            preferred = self.settings.agent_default_provider
            if preferred != "auto" and preferred in self.providers:
                provider_id = preferred
            else:
                ready = self.ready_ids()
                if not ready:
                    raise ProviderError("No external provider is configured")
                provider_id = ready[0]
        provider = self.providers.get(provider_id)
        if provider is None:
            spec = self.specs.get(provider_id)
            if spec is None:
                raise ProviderError(f"Unknown provider: {provider_id}")
            key_hint = spec.key_env or "enable the local provider"
            raise ProviderError(
                f"Provider {provider_id} is not ready; configure {key_hint}"
            )
        return provider, self.specs[provider_id]
