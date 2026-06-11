"""Phase 0 smoke tests: the app boots and serves its health + index routes."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # Health surfaces how the harness is wired, without leaking secrets.
    assert "judge_provider" in body
    assert "target_adapter" in body


def test_index_renders():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Guardrails Harness" in resp.text
