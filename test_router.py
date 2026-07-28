import json
import os

import httpx

from server.config import Settings
from server.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    ProviderRegistry,
    ProviderSpec,
)


def settings():
    os.environ["AIC_AGENT_ENABLED"] = "true"
    return Settings.from_env()


def test_openai_compatible_adapter_shape(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret-value")
    def handler(request: httpx.Request):
        payload = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer secret-value"
        assert payload["model"] == "test-model"
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={
            "model": "test-model",
            "choices": [{"message": {"content": "{\\\"ok\\\":true}"}}],
            "usage": {"total_tokens": 12},
        })
    spec=ProviderSpec("test","Test","openai","https://example.test/v1","TEST_API_KEY","fast","quality")
    provider=OpenAICompatibleProvider(spec,settings(),transport=httpx.MockTransport(handler))
    result=provider.complete(messages=[{"role":"user","content":"hi"}],model="test-model",json_mode=True,max_tokens=20)
    assert result.provider == "test"
    assert result.usage["total_tokens"] == 12


def test_anthropic_adapter_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "anthropic-secret")
    def handler(request: httpx.Request):
        payload=json.loads(request.content)
        assert request.headers["x-api-key"] == "anthropic-secret"
        assert payload["system"] == "system prompt"
        assert payload["messages"][0]["role"] == "user"
        return httpx.Response(200,json={
            "model":"claude-test",
            "content":[{"type":"text","text":"answer"}],
            "usage":{"input_tokens":3,"output_tokens":2},
        })
    spec=ProviderSpec("anthropic-test","Anthropic","anthropic","https://api.anthropic.test/v1","ANTHROPIC_TEST_KEY","fast","quality",supports_json_mode=False)
    provider=AnthropicProvider(spec,settings(),transport=httpx.MockTransport(handler))
    result=provider.complete(messages=[{"role":"system","content":"system prompt"},{"role":"user","content":"hi"}],model="claude-test",json_mode=False,max_tokens=20)
    assert result.content == "answer"


def test_registry_custom_openai_provider(monkeypatch):
    monkeypatch.setenv("CUSTOM_X_KEY", "super-secret-xyz")
    monkeypatch.setenv("AIC_OPENAI_COMPAT_PROVIDERS_JSON", json.dumps([{
        "id":"custom-x","base_url":"https://custom.test/v1","key_env":"CUSTOM_X_KEY",
        "fast_model":"x-fast","quality_model":"x-quality"
    }]))
    registry=ProviderRegistry(Settings.from_env())
    status=registry.status()
    assert "custom-x" in status["ready_providers"]
    assert "super-secret-xyz" not in json.dumps(status)
