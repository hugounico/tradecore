"""T7: cableado del login en la app real (`src.api.app.app`).

Usa la app real SIN entrar a su `lifespan` (nunca `with TestClient(app)`): el arranque de
Databento vive en el lifespan, así que estas pruebas no conectan nada. La config de acceso es la
sintética de `tests/auth_testkit.py`, cargada por `tests/conftest.py` antes de importar la app.
"""
from __future__ import annotations

import sys

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api.app import app
from src.api.auth.middleware import AuthGate
from tests.auth_testkit import TEST_ORIGIN, session_headers

# `src.api` reexporta el objeto `app`, que tapa al submódulo: se toma el módulo desde sys.modules.
app_module = sys.modules["src.api.app"]
AUTH_PATHS = {"/", "/login", "/auth/login.js", "/auth/enter", "/auth/logout"}


def _client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def test_auth_config_loaded_ok():
    assert app_module._auth_config.status == "OK"


def test_gate_registered_once_with_shared_config():
    gates = [m for m in app.user_middleware if m.cls is AuthGate]
    assert len(gates) == 1
    assert gates[0].kwargs["config"] is app_module._auth_config


def test_auth_routes_respond_and_hidden_from_openapi():
    c = _client()
    assert c.get("/login").status_code == 200
    assert c.get("/auth/login.js").status_code == 200
    assert c.post("/auth/enter", headers={"origin": TEST_ORIGIN}).status_code == 400  # sin cuerpo
    assert c.post("/auth/logout", headers={"origin": TEST_ORIGIN}).status_code == 303
    assert not (AUTH_PATHS & set(app.openapi()["paths"]))


def test_health_public_without_session():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", ["/static/index.html", "/static/app.js", "/docs", "/redoc",
                                  "/openapi.json", "/x"],
                         ids=["index", "appjs", "docs", "redoc", "openapi", "x"])
def test_protected_without_session(path):
    c = _client()
    html = c.get(path, headers={"accept": "text/html"})
    assert html.status_code == 303 and html.headers["location"] == "/login"
    assert c.get(path).status_code == 401


def test_dashboard_with_session():
    r = _client().get("/static/index.html", headers=session_headers())
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"


def test_root_and_login_page():
    c = _client()
    assert c.get("/").headers["location"] == "/login"
    assert c.get("/", headers=session_headers()).headers["location"] == "/static/index.html"
    page = c.get("/login")
    assert page.status_code == 200 and 'data-view="login"' in page.text
    assert "content-security-policy" in page.headers


@pytest.mark.parametrize("headers,code", [
    ({}, 4403),
    ({"origin": TEST_ORIGIN}, 4401),
    ({"origin": "https://evil.example"}, 4403),
], ids=["nothing", "origin_only", "bad_origin"])
def test_ws_rejected_without_session(headers, code):
    c = _client()
    with c.websocket_connect("/ws/chart", headers=headers) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == code
