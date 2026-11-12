"""Cobertura de rutas de la app real (Paso 2, T9; Design §6 y §14).

Recorre TODAS las rutas registradas en `src.api.app.app` (importada sin entrar a su `lifespan`:
nunca `with TestClient(app)`) y comprueba que, sin sesión, toda combinación método+ruta fuera de
`PUBLIC_ROUTES` es rechazada por el gate (401; 303 a /login si el navegador pide HTML) y que el
WebSocket cierra con 4401. Si mañana alguien agrega una ruta a `app.py`, queda cubierta sin tocar
esta prueba. La config sintética la carga `tests/conftest.py`.
"""
from __future__ import annotations

import sys

import pytest
from starlette.routing import Mount, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.api.app  # noqa: F401  (registra el módulo; `src.api` reexporta `app` y lo oculta)
from src.api.auth.middleware import PUBLIC_ROUTES
from tests.auth_testkit import TEST_ORIGIN

# Lista pública del Design §6, escrita aquí a propósito (no importada del middleware): si alguien
# agrega una ruta pública en el código, esta prueba lo detecta en vez de seguirle la corriente.
PUBLIC = frozenset({("GET", "/health"), ("GET", "/"), ("GET", "/login"), ("GET", "/auth/login.js"),
                    ("POST", "/auth/enter"), ("POST", "/auth/logout")})
APP = sys.modules["src.api.app"].app
METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
STATIC_PROBES = ["/static/index.html", "/static/app.js", "/static/styles.css", "/static/inexistente"]
# "/static/../health" no va aquí: el cliente de pruebas (httpx) lo normaliza a "/health" antes de
# enviarlo; ese caso lo cubre test_auth_middleware_http.py con ASGI directo.
TRICKY = ["/x", "//static/index.html", "/STATIC/index.html", "/static/%2e%2e/health",
          "/health/", "/login/", "/auth/enter/"]
# Rutas que la app real debe tener hoy: si la introspección deja de verlas, la prueba falla en vez
# de pasar en vacío.
EXPECTED_PRESENT = {"/health", "/", "/login", "/auth/login.js", "/auth/enter", "/auth/logout",
                    "/docs", "/redoc", "/openapi.json", "/static", "/ws/chart"}


def _flatten(routes):
    """Rutas efectivas, también las agrupadas por include_router (FastAPI reciente las envuelve)."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _flatten(inner.routes)
        elif isinstance(r, Mount):
            yield r
        elif getattr(r, "routes", None) is not None and not hasattr(r, "path"):
            yield from _flatten(r.routes)
        else:
            yield r


ROUTES = list(_flatten(APP.routes))
HTTP_PATHS = sorted({r.path for r in ROUTES
                     if not isinstance(r, (Mount, WebSocketRoute)) and hasattr(r, "methods")})
WS_PATHS = sorted({r.path for r in ROUTES if isinstance(r, WebSocketRoute)})
MOUNTS = sorted({r.path for r in ROUTES if isinstance(r, Mount)})
PROBES = sorted(set(HTTP_PATHS) | set(STATIC_PROBES) | set(TRICKY)
                | {m + "/inexistente" for m in MOUNTS})
CASES = [(m, p) for p in PROBES for m in METHODS if (m, p) not in PUBLIC]
NAV_PATHS = [p for p in PROBES if ("GET", p) not in PUBLIC]


def _client() -> TestClient:
    return TestClient(APP, follow_redirects=False)  # sin `with`: no ejecuta el lifespan


def test_route_discovery_sees_every_known_route():
    seen = set(HTTP_PATHS) | set(WS_PATHS) | set(MOUNTS)
    assert EXPECTED_PRESENT <= seen, sorted(EXPECTED_PRESENT - seen)
    assert WS_PATHS == ["/ws/chart"]


@pytest.mark.parametrize(("method", "path"), CASES, ids=[f"{m}-{p}" for m, p in CASES])
def test_unauthenticated_request_is_rejected(method, path):
    resp = _client().request(method, path, headers={"origin": TEST_ORIGIN})
    assert resp.status_code == 401, (method, path, resp.status_code)
    assert resp.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in resp.headers


@pytest.mark.parametrize("path", NAV_PATHS)
def test_unauthenticated_browser_navigation_redirects_to_login(path):
    resp = _client().get(path, headers={"accept": "text/html"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("path", WS_PATHS)
def test_unauthenticated_websocket_closes_4401(path):
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client().websocket_connect(path, headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_public_routes_answer_without_session():
    c = _client()
    assert c.get("/health").status_code == 200
    assert (c.get("/").status_code, c.get("/").headers["location"]) == (303, "/login")
    assert c.get("/login").status_code == 200
    assert c.get("/auth/login.js").status_code == 200
    assert c.post("/auth/enter").status_code == 403  # sin Origin: la ruta lo rechaza
    assert c.post("/auth/logout").status_code == 403


def test_public_list_matches_design():
    assert PUBLIC_ROUTES == PUBLIC
