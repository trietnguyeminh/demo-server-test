import os

os.environ["AIC_MODE"] = "mock"

from fastapi.testclient import TestClient
from server.main import app


def test_health_and_search():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["backend"]["mode"] == "mock"
        assert health.json()["api_planner_ready"] is False

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
