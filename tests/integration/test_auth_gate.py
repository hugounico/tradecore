"""Pruebas de T6a: rutas `/`, `/auth/enter` y `/auth/logout` detrás de AuthGate.

Solo app FastAPI de prueba; nunca src.api.app, red, Databento ni AWS. Cliente nuevo por prueba
y sin `with`. Cookies solo como cabecera cruda. Tokens, claves y UUID son sintéticos: las claves
P-256 se generan en la propia prueba. Los atributos del módulo bajo prueba se leen dentro de
cada prueba (la colección no falla con el stub).
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import logging
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from src.api.auth import routes as rt
from src.api.auth.config import validate_auth_settings
from src.api.auth.middleware import AuthGate
from src.api.auth.ratelimit import TokenBucket
from src.api.auth.session import SessionCodec
from src.api.auth.tokens import TokenVerifier

# --- Constantes sintéticas ------------------------------------------------------
NOW = 1_700_000_000
TTL = 28800
SUPABASE_URL = "https://svvzrmhvsjalhpitjcvy.supabase.co"
ISS = SUPABASE_URL + "/auth/v1"
PUBKEY = "sb_publishable_SeQlUSsK9WIH8Gag8NjyXA"
ORIGIN = "https://tr-abc.ecs.us-east-1.on.aws"
ORIGIN_OTHER = "https://malo.example"
SESSION_KEY = "a" * 64
SUB_OK = "11111111-2222-3333-4444-555555555555"
SUB_OK_2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SUB_NOT_ALLOWED = "99999999-9999-9999-9999-999999999999"
ALLOWED_SUBS = f"{SUB_OK},{SUB_OK_2}"
FORM = "application/x-www-form-urlencoded"
COOKIE_TAIL = "; Max-Age=28800; Path=/; Secure; HttpOnly; SameSite=Lax"
CLEAR_COOKIE = "__Host-tc_session=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"


# --- Claves y tokens (copiado de test_auth_tokens.py) ---------------------------
def _b64url_uint(value: int, length: int = 32) -> str:
    raw = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _gen_key():
    priv = ec.generate_private_key(ec.SECP256R1())
    nums = priv.public_key().public_numbers()
    jwk = {
        "kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "key_ops": ["verify"],
        "x": _b64url_uint(nums.x), "y": _b64url_uint(nums.y),
    }
    return priv, jwk


def _jwk_with_kid(jwk: dict, kid: str) -> dict:
    out = dict(jwk)
    out["kid"] = kid
    return out


def _priv_pem(priv) -> bytes:
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


_PRIV, _JWK_BASE = _gen_key()
JWK = _jwk_with_kid(_JWK_BASE, "k1")


def make_token(*, sub=SUB_OK, iat=NOW, exp=NOW + 600, kid="k1") -> str:
    payload = {"iss": ISS, "aud": "authenticated", "role": "authenticated",
               "sub": sub, "iat": iat, "exp": exp}
    return jwt.encode(payload, _priv_pem(_PRIV), algorithm="ES256", headers={"kid": kid})


# --- Config (copiado de test_auth_session.py) -----------------------------------
def _raw(**over) -> dict:
    raw = {
        "TC_AUTH_SUPABASE_URL": SUPABASE_URL,
        "TC_AUTH_PUBLISHABLE_KEY": PUBKEY,
        "TC_AUTH_JWKS_JSON": json.dumps({"keys": [JWK]}),
        "TC_AUTH_ALLOWED_SUBS": ALLOWED_SUBS,
        "TC_AUTH_ALLOWED_ORIGINS": ORIGIN,
        "TC_SESSION_KEY": SESSION_KEY,
        "TC_SESSION_EPOCH": "1",
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


def _session_cookie(cfg=None) -> str:
    return SessionCodec(cfg or _cfg()).issue(SUB_OK, NOW)


def _cookie_header(value: str) -> dict:
    return {"cookie": f"__Host-tc_session={value}"}


# --- Espías ---------------------------------------------------------------------
class SpyBucket:
    """Cubo de prueba: cuenta llamadas y concede o niega según `allow`."""

    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.calls = 0

    def try_acquire(self) -> bool:
        self.calls += 1
        return self.allow


def spy_verifier(monkeypatch) -> list:
    """Sustituye TokenVerifier en rt por una subclase que registra cada verify()."""
    calls: list = []

    class Spy(TokenVerifier):
        def verify(self, token, now):
            calls.append(token)
            return super().verify(token, now)

    monkeypatch.setattr(rt, "TokenVerifier", Spy)
    return calls


# --- Construcción de la app y del cliente ---------------------------------------
def build_gate(cfg, *, clock=None, bucket=None):
    clock = clock or (lambda: NOW)
    app = FastAPI()
    app.include_router(rt.build_auth_router(cfg, clock=clock, bucket=bucket or SpyBucket()))

    @app.get("/health")
    async def health():
        return PlainTextResponse("ok-health")

    @app.get("/x")
    async def x():
        return PlainTextResponse("ok-x")

    return AuthGate(app, config=cfg, clock=clock)


def client_for(cfg, *, clock=None, bucket=None) -> TestClient:
    return TestClient(build_gate(cfg, clock=clock, bucket=bucket), follow_redirects=False)


def post_enter(client, body: bytes, *, origin=ORIGIN, ctype=FORM):
    headers = {}
    if origin is not None:
        headers["origin"] = origin
    if ctype is not None:
        headers["content-type"] = ctype
    return client.post("/auth/enter", content=body, headers=headers)


def set_cookies(resp) -> list[str]:
    return [v.decode() for k, v in resp.headers.raw if k.decode().lower() == "set-cookie"]


def count_header(resp, name: str) -> int:
    return sum(1 for k, _ in resp.headers.raw if k.decode().lower() == name)


def route_logs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "src.api.auth.routes"]


# --- ASGI directo ---------------------------------------------------------------
def asgi(gate, method, path, headers, messages=()):
    """Ejecuta el gate con mensajes simulados. Devuelve (mensajes enviados, nº de receive)."""
    queue = list(messages)
    state = {"n": 0}
    sent: list = []

    async def receive():
        state["n"] += 1
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "root_path": "",
        "query_string": b"", "headers": headers,
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    }
    asyncio.run(gate(scope, receive, send))
    return sent, state["n"]


def start_of(sent) -> dict:
    return next(m for m in sent if m["type"] == "http.response.start")


def status_of(sent) -> int:
    return start_of(sent)["status"]


def header_values(sent, name: str) -> list[str]:
    return [v.decode() for k, v in start_of(sent)["headers"] if k.decode().lower() == name]


def chunk(body: bytes, more: bool) -> dict:
    return {"type": "http.request", "body": body, "more_body": more}


def enter_headers(*, origin=ORIGIN, ctype=FORM, extra=()):
    h = []
    if origin is not None:
        h.append((b"origin", origin.encode()))
    if ctype is not None:
        h.append((b"content-type", ctype.encode()))
    h.extend(extra)
    return h


# ================================================================================
# 1. GET /
# ================================================================================
@pytest.mark.parametrize(
    "case, expected",
    [
        ("no_cookie", "/login"),
        ("valid", "/static/index.html"),
        ("expired", "/login"),
        ("invalid_cfg", "/login"),
    ],
    ids=["no_cookie", "valid", "expired", "invalid_cfg"],
)
def test_root_redirects(case, expected):
    cookie = _session_cookie()
    clock = (lambda: NOW + TTL) if case == "expired" else (lambda: NOW)
    cfg = _cfg_invalid() if case == "invalid_cfg" else _cfg()
    c = client_for(cfg, clock=clock)
    headers = {} if case == "no_cookie" else _cookie_header(cookie)
    resp = c.get("/", headers=headers)
    assert resp.status_code == 303
    assert resp.headers["location"] == expected
    assert resp.headers["cache-control"] == "no-store"
    assert count_header(resp, "cache-control") == 1
    assert set_cookies(resp) == []


# ================================================================================
# 2. Canje válido
# ================================================================================
def test_enter_ok_sets_session_cookie():
    c = client_for(_cfg())
    resp = post_enter(c, f"access_token={make_token()}".encode())
    assert resp.status_code == 303
    assert resp.headers["location"] == "/static/index.html"
    assert resp.headers["cache-control"] == "no-store"
    cookies = set_cookies(resp)
    assert len(cookies) == 1
    assert cookies[0].startswith("__Host-tc_session=v1.")
    assert cookies[0].endswith(COOKIE_TAIL)
    pair = cookies[0].split(";")[0]
    assert c.get("/x", headers={"cookie": pair}).status_code == 200
    again = c.get("/", headers={"cookie": pair})
    assert again.headers["location"] == "/static/index.html"


# ================================================================================
# 3. Doble canje dentro de la ventana
# ================================================================================
def test_enter_twice_same_token():
    c = client_for(_cfg())
    token = make_token()
    first = post_enter(c, f"access_token={token}".encode())
    second = post_enter(c, f"access_token={token}".encode())
    for resp in (first, second):
        assert resp.status_code == 303
        cookies = set_cookies(resp)
        assert len(cookies) == 1
        pair = cookies[0].split(";")[0]
        assert c.get("/x", headers={"cookie": pair}).status_code == 200


# ================================================================================
# 4. Rechazos de token
# ================================================================================
def _tampered(token: str) -> str:
    h, p, s = token.split(".")
    ch = "B" if s[0] != "B" else "C"
    return f"{h}.{p}.{ch}{s[1:]}"


TOKEN_REJECTS = {
    "bad_sig": lambda: _tampered(make_token()),
    "stale": lambda: make_token(iat=NOW - 121),
    "not_allowed": lambda: make_token(sub=SUB_NOT_ALLOWED),
    "unknown_kid": lambda: make_token(kid="kZZ"),
    "bad_format": lambda: "texto-basura",
}


@pytest.mark.parametrize("reason", list(TOKEN_REJECTS), ids=list(TOKEN_REJECTS))
def test_enter_token_rejected(reason, caplog):
    c = client_for(_cfg())
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        resp = post_enter(c, f"access_token={TOKEN_REJECTS[reason]()}".encode())
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?e=1"
    assert set_cookies(resp) == []
    assert f"AUTH_ENTER_REJECT reason={reason}" in route_logs(caplog)


# ================================================================================
# 5. Config INVALID
# ================================================================================
def test_enter_config_invalid(caplog):
    bucket = SpyBucket()
    gate = build_gate(_cfg_invalid(), bucket=bucket)
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        sent, receives = asgi(gate, "POST", "/auth/enter", enter_headers())
    assert status_of(sent) == 303
    assert header_values(sent, "location") == ["/login?e=1"]
    assert bucket.calls == 0
    assert receives == 0
    assert "AUTH_ENTER_REJECT reason=config_invalid" in route_logs(caplog)


# ================================================================================
# 6. Origin
# ================================================================================
def test_enter_origin_absent(caplog):
    c = client_for(_cfg())
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        resp = post_enter(c, f"access_token={make_token()}".encode(), origin=None)
    assert resp.status_code == 403
    assert "AUTH_ENTER_REJECT reason=bad_origin" in route_logs(caplog)


def test_enter_origin_foreign(caplog):
    c = client_for(_cfg())
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        resp = post_enter(c, f"access_token={make_token()}".encode(), origin=ORIGIN_OTHER)
    assert resp.status_code == 403
    assert "AUTH_ENTER_REJECT reason=bad_origin" in route_logs(caplog)


def test_enter_origin_twice(caplog):
    gate = build_gate(_cfg())
    headers = enter_headers(extra=[(b"origin", ORIGIN.encode())])
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        sent, _ = asgi(gate, "POST", "/auth/enter", headers,
                       [chunk(f"access_token={make_token()}".encode(), False)])
    assert status_of(sent) == 403
    assert "AUTH_ENTER_REJECT reason=bad_origin" in route_logs(caplog)


# ================================================================================
# 7. Cuerpo
# ================================================================================
@pytest.mark.parametrize("ctype", ["text/plain", "application/json"], ids=["text", "json"])
def test_enter_wrong_content_type(ctype, caplog):
    c = client_for(_cfg())
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        resp = post_enter(c, f"access_token={make_token()}".encode(), ctype=ctype)
    assert resp.status_code == 400
    assert "AUTH_ENTER_REJECT reason=bad_body" in route_logs(caplog)


def test_enter_two_content_types():
    gate = build_gate(_cfg())
    headers = enter_headers(extra=[(b"content-type", FORM.encode())])
    sent, _ = asgi(gate, "POST", "/auth/enter", headers,
                   [chunk(f"access_token={make_token()}".encode(), False)])
    assert status_of(sent) == 400


def test_enter_content_type_with_charset():
    c = client_for(_cfg())
    resp = post_enter(c, f"access_token={make_token()}".encode(),
                      ctype="application/x-www-form-urlencoded; charset=utf-8")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/static/index.html"


def test_enter_content_length_too_big_no_read():
    gate = build_gate(_cfg())
    headers = enter_headers(extra=[(b"content-length", b"9000")])
    sent, receives = asgi(gate, "POST", "/auth/enter", headers)
    assert status_of(sent) == 413
    assert receives == 0


@pytest.mark.parametrize(
    "lengths", [[b"abc"], [b"10", b"10"]], ids=["not_digits", "repeated"],
)
def test_enter_content_length_invalid(lengths):
    gate = build_gate(_cfg())
    headers = enter_headers(extra=[(b"content-length", v) for v in lengths])
    sent, receives = asgi(gate, "POST", "/auth/enter", headers)
    assert status_of(sent) == 400
    assert receives == 0


def test_enter_8192_bytes_two_chunks_not_413():
    gate = build_gate(_cfg())
    body = b"access_token=" + b"a" * (8192 - len(b"access_token="))
    assert len(body) == 8192
    sent, _ = asgi(gate, "POST", "/auth/enter", enter_headers(),
                   [chunk(body[:4096], True), chunk(body[4096:], False)])
    assert status_of(sent) == 303
    assert header_values(sent, "location") == ["/login?e=1"]


def test_enter_8193_bytes_three_chunks_413(caplog):
    gate = build_gate(_cfg())
    messages = [chunk(b"a" * 4096, True), chunk(b"a" * 4097, True), chunk(b"", False)]
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        sent, receives = asgi(gate, "POST", "/auth/enter", enter_headers(), messages)
    assert status_of(sent) == 413
    assert receives == 2
    assert "AUTH_ENTER_REJECT reason=bad_body" in route_logs(caplog)


def test_enter_disconnect_mid_body(monkeypatch):
    calls = spy_verifier(monkeypatch)
    gate = build_gate(_cfg())
    sent, _ = asgi(gate, "POST", "/auth/enter", enter_headers(),
                   [chunk(b"access_token=x", True), {"type": "http.disconnect"}])
    assert status_of(sent) == 400
    assert calls == []


BAD_BODIES = {
    "dup_field": b"access_token=a&access_token=b",
    "other_field": b"other=x",
    "extra_field": b"access_token=a&x=b",
    "trailing_amp": b"access_token=x&",
    "no_equals": b"access_token",
    "three_fields": b"access_token=a&b=1&c=2",
    "pct_ff": b"access_token=%ff",
    "non_ascii": "access_token=é".encode("utf-8"),
}


@pytest.mark.parametrize("name", list(BAD_BODIES), ids=list(BAD_BODIES))
def test_enter_bad_body_400(name, caplog):
    c = client_for(_cfg())
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        resp = post_enter(c, BAD_BODIES[name])
    assert resp.status_code == 400
    assert "AUTH_ENTER_REJECT reason=bad_body" in route_logs(caplog)


def test_enter_literal_percent_is_bad_format(caplog):
    c = client_for(_cfg())
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        resp = post_enter(c, b"access_token=a%zzb")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?e=1"
    assert "AUTH_ENTER_REJECT reason=bad_format" in route_logs(caplog)


# ================================================================================
# 8. Orden de las validaciones
# ================================================================================
def test_order_rate_limit_before_origin(caplog):
    c = client_for(_cfg(), bucket=SpyBucket(allow=False))
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        resp = post_enter(c, f"access_token={make_token()}".encode(), origin=ORIGIN_OTHER)
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "1"
    assert "AUTH_ENTER_REJECT reason=rate_limited" in route_logs(caplog)


def test_order_origin_before_body():
    c = client_for(_cfg())
    resp = post_enter(c, b"access_token=x", origin=ORIGIN_OTHER, ctype="text/plain")
    assert resp.status_code == 403


def test_order_body_before_verify(monkeypatch):
    calls = spy_verifier(monkeypatch)
    c = client_for(_cfg())
    resp = post_enter(c, f"access_token={make_token()}".encode(), ctype="text/plain")
    assert resp.status_code == 400
    assert calls == []


# ================================================================================
# 9. Límite de tasa
# ================================================================================
def test_rate_limit_20_then_429_then_refill():
    t = [1_000_000_000]
    bucket = TokenBucket(clock=lambda: t[0])
    c = client_for(_cfg(), bucket=bucket)
    for _ in range(20):
        assert post_enter(c, b"access_token=x").status_code != 429
    assert post_enter(c, b"access_token=x").status_code == 429
    t[0] += 200_000_000
    assert post_enter(c, b"access_token=x").status_code != 429


# ================================================================================
# 10. Logout
# ================================================================================
def test_logout_ok():
    c = client_for(_cfg())
    resp = c.post("/auth/logout", headers={"origin": ORIGIN})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert set_cookies(resp) == [CLEAR_COOKIE]


def test_logout_no_origin(caplog):
    c = client_for(_cfg())
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        resp = c.post("/auth/logout")
    assert resp.status_code == 403
    assert set_cookies(resp) == []
    assert "AUTH_LOGOUT_REJECT reason=bad_origin" in route_logs(caplog)


def test_logout_config_invalid():
    c = client_for(_cfg_invalid())
    resp = c.post("/auth/logout", headers={"origin": ORIGIN})
    assert resp.status_code == 403
    assert set_cookies(resp) == []


# ================================================================================
# 11. Set-Cookie exclusivo
# ================================================================================
@pytest.mark.parametrize("path", ["/", "/health", "/x"], ids=["root", "health", "x"])
@pytest.mark.parametrize("with_cookie", [False, True], ids=["anon", "cookie"])
def test_no_set_cookie_elsewhere(path, with_cookie):
    c = client_for(_cfg())
    headers = _cookie_header(_session_cookie()) if with_cookie else {}
    assert set_cookies(c.get(path, headers=headers)) == []


# ================================================================================
# 12. /health con config OK e INVALID
# ================================================================================
@pytest.mark.parametrize("invalid", [False, True], ids=["ok", "invalid"])
def test_health_always_200(invalid):
    c = client_for(_cfg_invalid() if invalid else _cfg())
    assert c.get("/health").status_code == 200


# ================================================================================
# 13. Sin fugas en logs
# ================================================================================
def test_logs_do_not_leak(caplog):
    c = client_for(_cfg())
    token = make_token()
    with caplog.at_level(logging.INFO, logger="src.api.auth.routes"):
        ok = post_enter(c, f"access_token={token}".encode())
        post_enter(c, f"access_token={_tampered(token)}".encode())
        post_enter(c, b"other=x")
    cookie_value = set_cookies(ok)[0].split(";")[0].split("=", 1)[1]
    messages = [r.getMessage() for r in caplog.records]
    assert f"AUTH_ENTER_OK sub={SUB_OK}" in route_logs(caplog)
    for message in messages:
        assert token not in message
        assert cookie_value not in message
        assert "access_token" not in message


# ================================================================================
# 14. Construcción del router
# ================================================================================
def test_router_with_config_does_not_load(monkeypatch):
    calls = []
    cfg = _cfg()
    monkeypatch.setattr(rt, "load_auth_config", lambda: calls.append(1) or cfg)
    rt.build_auth_router(cfg)
    assert calls == []


def test_router_without_config_loads_once(monkeypatch):
    calls = []
    cfg = _cfg()
    monkeypatch.setattr(rt, "load_auth_config", lambda: calls.append(1) or cfg)
    rt.build_auth_router()
    assert calls == [1]


def test_routes_hidden_from_openapi():
    app = FastAPI()
    app.include_router(rt.build_auth_router(_cfg(), bucket=SpyBucket()))
    paths = app.openapi()["paths"]
    for hidden in ("/", "/auth/enter", "/auth/logout"):
        assert hidden not in paths


# ================================================================================
# 15. AST de routes.py
# ================================================================================
ALLOWED_LOG_MESSAGES = {
    "AUTH_ENTER_REJECT reason=%s",
    "AUTH_ENTER_OK sub=%s",
    "AUTH_LOGOUT_REJECT reason=bad_origin",
}


def _docstring_nodes(tree) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _except_names(handler: ast.ExceptHandler) -> list[str]:
    t = handler.type
    if t is None:
        return ["<bare>"]
    nodes = t.elts if isinstance(t, ast.Tuple) else [t]
    names = []
    for n in nodes:
        if isinstance(n, ast.Name):
            names.append(n.id)
        elif isinstance(n, ast.Attribute):
            names.append(n.attr)
        else:
            names.append("<other>")
    return names


def test_ast_rules():
    tree = ast.parse(Path("src/api/auth/routes.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name != "src.api.app" for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "src.api.app"
        elif isinstance(node, ast.ExceptHandler):
            assert set(_except_names(node)) <= {"ValueError", "ClientDisconnect"}
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            if id(node) in skip:
                continue
            low = (node.value.decode() if isinstance(node.value, bytes) else node.value).lower()
            assert low != "host"
            for forbidden in ("x-forwarded", "raw_path", "query_string"):
                assert forbidden not in low
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "logger":
                assert node.func.attr == "info"
                first = node.args[0]
                assert isinstance(first, ast.Constant)
                assert first.value in ALLOWED_LOG_MESSAGES
