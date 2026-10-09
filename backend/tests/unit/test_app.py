"""M0: application factory, /api/health, error shape, SPA serving and CSP."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from publicsafe import __version__
from publicsafe.config import ConfigError
from publicsafe.main import CSP_POLICY, create_app


@pytest.fixture
def client(make_settings) -> TestClient:
    settings = make_settings(admin_password_hash="h", frontend_dist=Path("/nonexistent/dist"))
    return TestClient(create_app(settings))


def test_factory_refuses_without_admin_hash(make_settings) -> None:
    with pytest.raises(ConfigError):
        create_app(make_settings(admin_password_hash=None))


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["uptime_s"] >= 0
    assert set(body) == {"status", "version", "uptime_s", "device", "model_sha256", "db"}


def test_error_shape_for_unknown_route(client: TestClient) -> None:
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Not Found", "details": {}}}


def test_no_csp_without_built_frontend(client: TestClient) -> None:
    assert "content-security-policy" not in client.get("/api/health").headers


def test_help_phone_renders_markdown(client: TestClient) -> None:
    r = client.get("/help/phone")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<h1" in r.text


def test_spa_serving_and_csp(tmp_path: Path, make_settings) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>spa</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    client = TestClient(create_app(make_settings(admin_password_hash="h", frontend_dist=dist)))

    root = client.get("/")
    assert root.status_code == 200 and "spa" in root.text
    assert root.headers["content-security-policy"] == CSP_POLICY
    assert root.headers["x-content-type-options"] == "nosniff"

    deep = client.get("/sessions/abc")  # client-side route -> index.html
    assert deep.status_code == 200 and "spa" in deep.text

    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/favicon.svg").text == "<svg/>"

    api = client.get("/api/missing")
    assert api.status_code == 404 and api.json()["error"]["code"] == "not_found"
    assert api.headers["content-security-policy"] == CSP_POLICY

    assert client.get("/..%2F..%2Fetc%2Fpasswd").status_code == 200  # falls back to index.html
