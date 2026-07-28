import os

os.environ["AIC_MODE"] = "mock"

from fastapi.testclient import TestClient
from server.main import app


def test_health_and_search():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["backend"]["mode"] == "mock"

        response = client.post(
            "/api/search",
            json={
                "query": "người đánh trống Yamaha",
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
        assert len(body["hits"]) >= 1
        assert "total_ms" in body["latency_ms"]


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
