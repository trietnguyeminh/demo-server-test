import os

os.environ["AIC_MODE"] = "mock"
os.environ["AIC_AGENT_ENABLED"] = "true"
os.environ["AIC_AGENT_DEFAULT_PROVIDER"] = "mock"
os.environ["AIC_MOCK_PROVIDER_ENABLED"] = "true"

from fastapi.testclient import TestClient
from server.main import app


def test_health_and_search():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["backend"]["mode"] == "mock"
        assert health.json()["api_planner_ready"] is True
        assert health.json()["app_build_version"] == "multi-provider-agent-v5.0"
        assert (
            health.json()["router_contract_version"]
            == "routing-score-v2"
        )

        response = client.post(
            "/api/search",
            json={
                "query": "túi vải liên kết cùng phát triển",
                "profile": "auto",
                "ocr": "auto",
                "asr": "auto",
                "api_planner": "off",
                "top_k": 5,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["route"]["ocr"]["enabled"] is True
        assert body["route"]["ocr"]["routing_score"] >= 0.80
        assert body["route"]["asr"]["enabled"] is False
        assert "total_ms" in body["latency_ms"]
        assert "retrieval_wall_ms" in body["latency_ms"]


def test_auto_shadow_ocr_does_not_inject_unrelated_ocr_only_results():
    with TestClient(app) as client:
        response = client.post(
            "/api/search",
            json={
                "query": "người mặc áo đỏ đứng cạnh ô tô",
                "profile": "auto",
                "ocr": "auto",
                "asr": "off",
                "api_planner": "off",
                "top_k": 5,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["route"]["ocr"]["execution_state"] == "auto_parallel"
        assert any("shadow" in warning for warning in body["warnings"])


def test_benchmark():
    with TestClient(app) as client:
        response = client.post(
            "/api/benchmark",
            json={
                "queries": ["người mặc áo đỏ", "biển số 59A-123.45"],
                "profile": "fast",
                "repeats": 2,
                "top_k": 5,
            },
        )
        assert response.status_code == 200
        assert response.json()["summary"]["count"] == 4


def test_agent_health_and_planner_chat():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        body = health.json()
        assert body["agent"]["ready"] is True
        assert "mock" in body["agent"]["ready_providers"]
        assert body["agent_contract_version"] == "provider-registry-v2"

        response = client.post(
            "/api/agent/chat",
            json={
                "message": "túi vải liên kết cùng phát triển",
                "mode": "planner",
                "model_tier": "fast",
                "profile": "auto",
                "top_k": 5,
                "ocr": "auto",
                "asr": "auto",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["agent"]["provider"] == "mock"
        assert payload["agent"]["fallback_used"] is False
        assert payload["search"] is not None
        assert payload["search"]["route"]["ocr"]["enabled"] is True
        assert payload["search"]["route"]["asr"]["enabled"] is False
        assert payload["latency_ms"]["planner_ms"] > 0
        assert payload["session_id"]


def test_agent_full_and_session_reset():
    with TestClient(app) as client:
        response = client.post(
            "/api/agent/chat",
            json={
                "message": "tìm người đánh trống Yamaha",
                "mode": "full",
                "model_tier": "quality",
                "top_k": 5,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["reply"]
        assert payload["latency_ms"]["answer_ms"] > 0

        reset = client.delete(
            f"/api/agent/session/{payload['session_id']}"
        )
        assert reset.status_code == 200
        assert reset.json()["deleted"] is True


def test_health_never_exposes_api_key_value():
    with TestClient(app) as client:
        text = client.get("/api/health").text
        assert "GROQ_API_KEY" not in text
        assert "replace-in-kaggle-secret" not in text


def test_provider_registry_endpoint_and_secret_redaction():
    with TestClient(app) as client:
        response = client.get("/api/agent/providers")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ready_provider_count"] >= 1
        assert "mock" in payload["ready_providers"]
        text = response.text
        assert "API_KEY" not in text
        assert "sk-" not in text


def test_explicit_provider_and_model_override():
    with TestClient(app) as client:
        response = client.post(
            "/api/agent/chat",
            json={
                "message": "túi vải liên kết cùng phát triển",
                "mode": "planner",
                "provider": "mock",
                "model_tier": "fast",
                "model": "mock-custom-model",
                "top_k": 5,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["agent"]["requested_provider"] == "mock"
        assert payload["agent"]["provider"] == "mock"
        assert payload["agent"]["model"] == "mock-custom-model"
