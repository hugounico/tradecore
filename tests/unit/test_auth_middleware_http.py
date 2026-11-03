"""Pruebas de T4: AuthGate (parte HTTP) — deny-by-default, cabeceras, rutas públicas.

Solo app Starlette de prueba; nunca src.api.app, Databento, red ni AWS. Cliente nuevo por
prueba sin `with`. Cookies solo como cabecera cruda. Los atributos del módulo se leen dentro
de cada prueba (colección robusta frente al stub).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from src.api.auth import middleware as mw
from src.api.auth.config import validate_auth_settings
from src.api.auth.session import SessionCodec

# --- Config sintética (copiada de test_auth_session.py; no se importa de otra prueba) ---
NOW = 1_700_000_000
TTL = 28800
EPOCH = 1
SUPABASE_URL = "https://svvzrmhvsjalhpitjcvy.supabase.co"
PUBKEY = "sb_publishable_SeQlUSsK9WIH8Gag8NjyXA"
ORIGINS = "https://tr-abc.ecs.us-east-1.on.aws"
SESSION_KEY = "a" * 64
JWK_ES256 = {
    "alg": "ES256", "crv": "P-256", "kty": "EC", "use": "sig", "key_ops": ["verify"],
    "kid": "k1",
    "x": "JPdlg8l77lcPf4wxSMnACUPQ616wVqX2Uu9Q6ZJGLqU",
    "y": "AkjFprNzyDruo5hL-u0_gZ15ELaRcmKb1rGj-ckl8oE",
}
GOOD_JWKS = json.dumps({"keys": [JWK_ES256]})
SUB_OK = "11111111-2222-3333-4444-555555555555"
SUB_OK_2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ALLOWED_SUBS = f"{SUB_OK},{SUB_OK_2}"


def _raw(**over) -> dict:
    raw = {
        "TC_AUTH_SUPABASE_URL": SUPABASE_URL,
        "TC_AUTH_PUBLISHABLE_KEY": PUBKEY,
        "TC_AUTH_JWKS_JSON": GOOD_JWKS,
        "TC_AUTH_ALLOWED_SUBS": ALLOWED_SUBS,
        "TC_AUTH_ALLOWED_ORIGINS": ORIGINS,
        "TC_SESSION_KEY": SESSION_KEY,
        "TC_SESSION_EPOCH": str(EPOCH),
        "TC_SESSION_TTL_SECONDS": str(TTL),
    }
    raw.update(over)
    return raw


def _cfg(**over):
    cfg = validate_auth_settings(_raw(**over))
    assert cfg.status == "OK", cfg.invalid_fields
    return cfg


def _cfg_invalid():
    cfg = validate_auth_settings({})
    assert cfg.status == "INVALID"
    return cfg


def _valid_cookie(cfg=None) -> str:
    return SessionCodec(cfg or _cfg()).issue(SUB_OK, NOW)


# --- App de prueba --------------------------------------------------------------
class _Spy:
    """Registra qué rutas fueron llamadas (para comprobar deny sin tocar el app)."""

    def __init__(self) -> None:
        self.called: set[str] = set()


def _build_app(tmp_path, spy: _Spy):
    # Archivos estáticos.
    (tmp_path / "index.html").write_text("<html>idx</html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("// app", encoding="utf-8")
    (tmp_path / "styles.css").write_text("/* css */", encoding="utf-8")

    def make(name: str):
        async def endpoint(request):
            spy.called.add(name)
            return PlainTextResponse("ok-" + name, headers={"cache-control": "max-age=60"})
        return endpoint

    async def hdr_endpoint(request):
        spy.called.add("/hdr")
        resp = PlainTextResponse("hdr")
        resp.raw_headers.append((b"x-frame-options", b"ALLOWALL"))
        resp.raw_headers.append((b"x-frame-options", b"SAMEORIGIN"))
        resp.raw_headers.append((b"cache-control", b"public, max-age=60"))
        resp.raw_headers.append((b"set-cookie", b"a=1"))
        resp.raw_headers.append((b"set-cookie", b"b=2"))
        return resp

    routes = [
        Route("/health", make("/health"), methods=["GET"]),
        Route("/", make("/"), methods=["GET"]),
        Route("/login", make("/login"), methods=["GET"]),
        Route("/auth/login.js", make("/auth/login.js"), methods=["GET"]),
        Route("/auth/enter", make("/auth/enter"), methods=["POST"]),
        Route("/auth/logout", make("/auth/logout"), methods=["POST"]),
        Route("/docs", make("/docs"), methods=["GET"]),
        Route("/redoc", make("/redoc"), methods=["GET"]),
        Route("/openapi.json", make("/openapi.json"), methods=["GET"]),
        Route("/x", make("/x"), methods=["GET"]),
        Route("/hdr", hdr_endpoint, methods=["GET"]),
        Mount("/static", StaticFiles(directory=str(tmp_path)), name="static"),
    ]
    return Starlette(routes=routes)


def _client(tmp_path, spy, cfg, clock=None):
    app = _build_app(tmp_path, spy)
    gate = mw.AuthGate(app, config=cfg, clock=clock or (lambda: NOW))
    return TestClient(gate, follow_redirects=False)


# --- ASGI directo (para rutas que el cliente normaliza / cabeceras repetidas) ---
class _CaptureSend:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message) -> None:
        self.messages.append(message)

    @property
    def start(self) -> dict:
        return next(m for m in self.messages if m["type"] == "http.response.start")

    def headers_lower(self) -> list[tuple[str, str]]:
        return [(k.decode().lower(), v.decode()) for k, v in self.start["headers"]]


async def _raise_receive():
    raise AssertionError("receive no debe llamarse")


def _scope(method: str, path: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
    }


async def _call_asgi(gate, scope, receive=None):
    send = _CaptureSend()
    await gate(scope, receive or _raise_receive, send)
    return send


# ================================================================================
# 1. Constantes
# ================================================================================
def test_public_routes_and_headers():
    expected_routes = {
        ("GET", "/health"), ("GET", "/"), ("GET", "/login"),
        ("GET", "/auth/login.js"), ("POST", "/auth/enter"), ("POST", "/auth/logout"),
    }
    assert set(mw.PUBLIC_ROUTES) == expected_routes
    sh = {k.lower(): v for k, v in (
        (name.decode(), val.decode()) for name, val in mw.SECURITY_HEADERS
    )}
    assert sh == {
        "strict-transport-security": "max-age=31536000",
        "x-content-type-options": "nosniff",
        "referrer-policy": "same-origin",
        "x-frame-options": "DENY",
    }


# ================================================================================
# 2. Rutas públicas con config OK e INVALID
# ================================================================================
def _count_header(resp, name: str) -> int:
    name = name.lower()
    return sum(1 for k, _ in resp.headers.raw if k.decode().lower() == name)


SEC_NAMES = ["strict-transport-security", "x-content-type-options",
             "referrer-policy", "x-frame-options"]


@pytest.mark.parametrize(
    "method, path, keeps_maxage",
    [
        ("GET", "/health", True),
        ("GET", "/", True),
        ("GET", "/login", False),
        ("GET", "/auth/login.js", False),
        ("POST", "/auth/enter", False),
        ("POST", "/auth/logout", False),
    ],
    ids=["health", "root", "login", "loginjs", "enter", "logout"],
)
@pytest.mark.parametrize("invalid", [False, True], ids=["ok", "invalid"])
def test_public_routes(tmp_path, method, path, keeps_maxage, invalid):
    spy = _Spy()
    cfg = _cfg_invalid() if invalid else _cfg()
    c = _client(tmp_path, spy, cfg)
    resp = c.request(method, path)
    assert resp.status_code == 200
    assert path in spy.called
    for n in SEC_NAMES:
        assert _count_header(resp, n) == 1
    if keeps_maxage:
        assert resp.headers["cache-control"] == "max-age=60"
    else:
        assert resp.headers["cache-control"] == "no-store"
        assert _count_header(resp, "cache-control") == 1


# ================================================================================
# 3. Método no listado en ruta pública -> 401, app no llamado
# ================================================================================
@pytest.mark.parametrize(
    "method, path",
    [
        ("POST", "/health"), ("HEAD", "/health"), ("PUT", "/"),
        ("POST", "/login"), ("GET", "/auth/enter"), ("GET", "/auth/logout"),
    ],
    ids=["post_health", "head_health", "put_root", "post_login", "get_enter", "get_logout"],
)
def test_wrong_method_public_401(tmp_path, method, path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    resp = c.request(method, path)
    assert resp.status_code == 401
    assert spy.called == set()


# ================================================================================
# 4. Protegidas sin cookie: 303 con Accept HTML, 401 si no
# ================================================================================
PROTECTED = ["/static/index.html", "/static/app.js", "/static/styles.css",
             "/static/inexistente", "/docs", "/redoc", "/openapi.json", "/x"]


@pytest.mark.parametrize("path", PROTECTED, ids=lambda p: p.replace("/", "_"))
def test_protected_no_cookie_html_303(tmp_path, path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    resp = c.get(path, headers={"accept": "text/html,application/xhtml+xml"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert spy.called == set()
    assert resp.content == b""
    assert resp.headers["content-length"] == "0"
    assert resp.headers["cache-control"] == "no-store"
    for n in SEC_NAMES:
        assert _count_header(resp, n) == 1
    assert _count_header(resp, "set-cookie") == 0


@pytest.mark.parametrize("path", PROTECTED, ids=lambda p: p.replace("/", "_"))
@pytest.mark.parametrize("accept", ["application/json", None], ids=["json", "none"])
def test_protected_no_cookie_401(tmp_path, path, accept):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    headers = {"accept": accept} if accept is not None else {}
    resp = c.get(path, headers=headers)
    assert resp.status_code == 401
    assert spy.called == set()
    assert resp.content == b""
    assert resp.headers["content-length"] == "0"
    assert resp.headers["cache-control"] == "no-store"
    for n in SEC_NAMES:
        assert _count_header(resp, n) == 1
    assert _count_header(resp, "set-cookie") == 0


# ================================================================================
# 5. ASGI directo: rutas que el cliente normalizaría -> 401 exacto
# ================================================================================
@pytest.mark.parametrize(
    "path",
    ["/static/../health", "//static/index.html", "/STATIC/index.html", "/health/",
     "/Health", "/login/", "/static/%2e%2e/health"],
    ids=["dotdot", "double_slash", "upper_static", "trail_slash", "cap_health",
         "login_slash", "enc_dotdot"],
)
def test_asgi_normalized_paths_401(tmp_path, path):
    spy = _Spy()
    gate = mw.AuthGate(_build_app(tmp_path, spy), config=_cfg(), clock=lambda: NOW)
    import anyio
    send = anyio.run(_call_asgi, gate, _scope("GET", path))
    assert send.start["status"] == 401
    assert spy.called == set()


# ================================================================================
# 6. Config OK + cookie válida -> pasa
# ================================================================================
def test_valid_cookie_passes(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    cookie = _valid_cookie()
    resp = c.get("/x", headers={"cookie": f"__Host-tc_session={cookie}"})
    assert resp.status_code == 200
    assert resp.text == "ok-/x"
    assert "/x" in spy.called
    assert resp.headers["cache-control"] == "no-store"
    assert _count_header(resp, "cache-control") == 1
    for n in SEC_NAMES:
        assert _count_header(resp, n) == 1
    assert _count_header(resp, "set-cookie") == 0


def test_valid_cookie_404(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    cookie = _valid_cookie()
    resp = c.get("/no-existe", headers={"cookie": f"__Host-tc_session={cookie}"})
    assert resp.status_code == 404


def test_valid_cookie_405(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    cookie = _valid_cookie()
    resp = c.post("/static/index.html", headers={"cookie": f"__Host-tc_session={cookie}"})
    assert resp.status_code == 405


# ================================================================================
# 7. /hdr: colapsa x-frame-options y cache-control; conserva set-cookie del app
# ================================================================================
def test_hdr_headers_collapsed(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    cookie = _valid_cookie()
    resp = c.get("/hdr", headers={"cookie": f"__Host-tc_session={cookie}"})
    assert resp.status_code == 200
    assert _count_header(resp, "x-frame-options") == 1
    assert resp.headers["x-frame-options"] == "DENY"
    assert _count_header(resp, "cache-control") == 1
    assert resp.headers["cache-control"] == "no-store"
    set_cookies = [v.decode() for k, v in resp.headers.raw if k.decode().lower() == "set-cookie"]
    assert "a=1" in set_cookies
    assert "b=2" in set_cookies


# ================================================================================
# 8. Cookie válida entre otras cookies
# ================================================================================
def test_valid_cookie_among_others(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    cookie = _valid_cookie()
    resp = c.get("/x", headers={"cookie": f"a=1; __Host-tc_session={cookie}; b=2"})
    assert resp.status_code == 200
    assert "/x" in spy.called


# ================================================================================
# 9. Rechazos: 401 y app no llamado
# ================================================================================
def test_reject_tampered_sig(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    cookie = _valid_cookie()
    h, p, s = cookie.split(".")
    ch = "B" if s[0] != "B" else "C"
    bad = f"{h}.{p}.{ch}{s[1:]}"
    resp = c.get("/x", headers={"cookie": f"__Host-tc_session={bad}"})
    assert resp.status_code == 401
    assert spy.called == set()


def test_reject_expired(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg(), clock=lambda: NOW + TTL)
    cookie = _valid_cookie()
    resp = c.get("/x", headers={"cookie": f"__Host-tc_session={cookie}"})
    assert resp.status_code == 401
    assert spy.called == set()


def test_reject_bad_epoch(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg(TC_SESSION_EPOCH="2"))
    cookie = _valid_cookie(_cfg())  # cookie de época 1
    resp = c.get("/x", headers={"cookie": f"__Host-tc_session={cookie}"})
    assert resp.status_code == 401
    assert spy.called == set()


def test_reject_sub_removed(tmp_path):
    spy = _Spy()
    cfg2 = _cfg(TC_AUTH_ALLOWED_SUBS=SUB_OK_2)  # ya no incluye SUB_OK
    c = _client(tmp_path, spy, cfg2)
    cookie = _valid_cookie(_cfg())
    resp = c.get("/x", headers={"cookie": f"__Host-tc_session={cookie}"})
    assert resp.status_code == 401
    assert spy.called == set()


def test_reject_config_invalid_with_cookie(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg_invalid())
    cookie = _valid_cookie(_cfg())
    resp = c.get("/x", headers={"cookie": f"__Host-tc_session={cookie}"})
    assert resp.status_code == 401
    assert spy.called == set()
    # También 303 con Accept HTML.
    resp2 = c.get("/x", headers={"cookie": f"__Host-tc_session={cookie}",
                                 "accept": "text/html"})
    assert resp2.status_code == 303
    assert resp2.headers["location"] == "/login"


def test_reject_duplicate_cookie(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    cookie = _valid_cookie()
    resp = c.get("/x", headers={
        "cookie": f"__Host-tc_session={cookie}; __Host-tc_session={cookie}"})
    assert resp.status_code == 401
    assert spy.called == set()


def test_reject_only_other_cookies(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    resp = c.get("/x", headers={"cookie": "a=1; b=2"})
    assert resp.status_code == 401
    assert spy.called == set()


# ================================================================================
# 10. ASGI directo: dos cabeceras cookie
# ================================================================================
def test_asgi_two_cookie_headers_same_session_401(tmp_path):
    spy = _Spy()
    gate = mw.AuthGate(_build_app(tmp_path, spy), config=_cfg(), clock=lambda: NOW)
    cookie = _valid_cookie()
    import anyio
    headers = [
        (b"cookie", f"__Host-tc_session={cookie}".encode()),
        (b"cookie", f"__Host-tc_session={cookie}".encode()),
    ]
    send = anyio.run(_call_asgi, gate, _scope("GET", "/x", headers))
    assert send.start["status"] == 401
    assert spy.called == set()


def test_asgi_other_cookie_plus_session_passes(tmp_path):
    spy = _Spy()
    gate = mw.AuthGate(_build_app(tmp_path, spy), config=_cfg(), clock=lambda: NOW)
    cookie = _valid_cookie()
    import anyio

    async def recv():
        return {"type": "http.request", "body": b"", "more_body": False}

    headers = [
        (b"cookie", b"a=1"),
        (b"cookie", f"__Host-tc_session={cookie}".encode()),
    ]
    send = anyio.run(_call_asgi, gate, _scope("GET", "/x", headers), recv)
    assert send.start["status"] == 200
    assert "/x" in spy.called


# ================================================================================
# 11. Host / X-Forwarded-* ignorados
# ================================================================================
def test_forwarded_headers_ignored(tmp_path):
    spy = _Spy()
    c = _client(tmp_path, spy, _cfg())
    resp = c.get("/x", headers={
        "host": "evil.example",
        "x-forwarded-host": "evil.example",
        "x-forwarded-proto": "http",
        "accept": "text/html",
    })
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


# ================================================================================
# 12. Config INVALID: clock que lanza no se llama
# ================================================================================
def test_invalid_config_clock_not_called(tmp_path):
    spy = _Spy()

    def boom():
        raise AssertionError("clock no debe llamarse con config INVALID")

    c = _client(tmp_path, spy, _cfg_invalid(), clock=boom)
    resp = c.get("/x")
    assert resp.status_code == 401


# ================================================================================
# 13. lifespan: mismo scope/receive/send al app interno
# ================================================================================
def test_lifespan_passthrough(tmp_path):
    captured = {}

    async def inner(scope, receive, send):
        captured["scope"] = scope
        captured["receive"] = receive
        captured["send"] = send

    gate = mw.AuthGate(inner, config=_cfg(), clock=lambda: NOW)
    scope = {"type": "lifespan"}

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(m):
        pass

    import anyio
    anyio.run(gate.__call__, scope, receive, send)
    assert captured["scope"] is scope
    assert captured["receive"] is receive
    assert captured["send"] is send


# ================================================================================
# 15. load_auth_config: solo si config is None
# ================================================================================
def test_load_auth_config_not_called_with_config(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return _cfg()

    monkeypatch.setattr(mw, "load_auth_config", fake)
    mw.AuthGate(_build_app(tmp_path, _Spy()), config=_cfg())
    assert calls["n"] == 0


def test_load_auth_config_called_without_config(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return _cfg()

    monkeypatch.setattr(mw, "load_auth_config", fake)
    mw.AuthGate(_build_app(tmp_path, _Spy()))
    assert calls["n"] == 1


# ================================================================================
# 16. AST de middleware.py
# ================================================================================
def test_ast_rules():
    path = Path("src/api/auth/middleware.py")
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # No importa src.api.app.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name != "src.api.app"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "src.api.app"
    # Ningún except sin tipo ni except Exception.
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            assert node.type is not None, "except sin tipo"
            name = getattr(node.type, "id", None)
            assert name != "Exception"
    # Ninguna constante de texto/bytes (fuera de docstrings) igual a 'host' o que contenga
    # x-forwarded, raw_path o query_string.
    forbidden_sub = ("x-forwarded", "raw_path", "query_string")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            val = node.value.decode() if isinstance(node.value, bytes) else node.value
            low = val.lower()
            assert low != "host", f"constante 'host' prohibida"
            for sub in forbidden_sub:
                assert sub not in low, f"constante contiene {sub!r}"


def _strip_docstrings_check():
    # Nota: los docstrings se excluyen porque ast.get_docstring no marca nodos Constant;
    # para simplificar, el módulo real no debe mencionar esos términos ni en docstrings.
    pass
