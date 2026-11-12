"""Propiedad del invariante de acceso (Paso 2, T9; Design §10 y §14).

Invariante: una petición a una ruta protegida llega al app SOLO si la config es OK y la cookie de
sesión es válida. Para cualquier otra combinación (config INVALID, sin cookie, cookie alterada,
vencida, de otra época, firmada con otra clave, de un usuario retirado o basura) el gate responde
303/401 y el contenido protegido nunca sale. En WebSocket, el endpoint nunca se ejecuta.

La app de prueba es un comodín que responde 200 con un marcador secreto en cualquier ruta y método;
así "llegó al app" equivale a "status 200 con el marcador". Hypothesis con `derandomize=True` y sin
base de datos: mismo conjunto de ejemplos en cada corrida (reproducible en CI). Solo datos sintéticos.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.api.auth.config import validate_auth_settings
from src.api.auth.middleware import AuthGate
from src.api.auth.session import COOKIE_NAME, SessionCodec

# Lista pública del Design §6, escrita aquí a propósito (no importada del middleware); su igualdad
# con PUBLIC_ROUTES la verifica test_auth_coverage.py.
PUBLIC = frozenset({("GET", "/health"), ("GET", "/"), ("GET", "/login"), ("GET", "/auth/login.js"),
                    ("POST", "/auth/enter"), ("POST", "/auth/logout")})
NOW = 1_700_000_000
TTL = 28800
ORIGIN = "https://tr-abc.ecs.us-east-1.on.aws"
SUB_OK = "11111111-2222-3333-4444-555555555555"
SUB_RETIRED = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SECRET = b"CONTENIDO-PROTEGIDO-T9"
_JWK = {
    "alg": "ES256", "crv": "P-256", "kty": "EC", "use": "sig", "key_ops": ["verify"], "kid": "k1",
    "x": "JPdlg8l77lcPf4wxSMnACUPQ616wVqX2Uu9Q6ZJGLqU",
    "y": "AkjFprNzyDruo5hL-u0_gZ15ELaRcmKb1rGj-ckl8oE",
}
_B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
PROPS = settings(max_examples=300, deadline=None, derandomize=True, database=None,
                 suppress_health_check=[HealthCheck.too_slow])


def _raw(**over) -> dict:
    raw = {
        "TC_AUTH_SUPABASE_URL": "https://svvzrmhvsjalhpitjcvy.supabase.co",
        "TC_AUTH_PUBLISHABLE_KEY": "sb_publishable_TradeCoreTestOnly",
        "TC_AUTH_JWKS_JSON": json.dumps({"keys": [_JWK]}),
        "TC_AUTH_ALLOWED_SUBS": SUB_OK,
        "TC_AUTH_ALLOWED_ORIGINS": ORIGIN,
        "TC_SESSION_KEY": "a" * 64,
        "TC_SESSION_EPOCH": "1",
        "TC_SESSION_TTL_SECONDS": str(TTL),
    }
    raw.update(over)
    return raw


def _ok(**over):
    cfg = validate_auth_settings(_raw(**over))
    assert cfg.status == "OK", cfg.invalid_fields
    return cfg


CFG_OK = _ok()

# Configs INVALID: cada una rompe un campo distinto (incluida la ausencia total).
_INVALID_OVERRIDES = [
    {"TC_AUTH_SUPABASE_URL": ""}, {"TC_AUTH_SUPABASE_URL": "http://svvzrmhvsjalhpitjcvy.supabase.co"},
    {"TC_AUTH_PUBLISHABLE_KEY": ""}, {"TC_AUTH_JWKS_JSON": ""}, {"TC_AUTH_JWKS_JSON": "{no-json"},
    {"TC_AUTH_JWKS_JSON": json.dumps({"keys": []})}, {"TC_AUTH_ALLOWED_SUBS": ""},
    {"TC_AUTH_ALLOWED_SUBS": "no-es-uuid"}, {"TC_AUTH_ALLOWED_ORIGINS": ""},
    {"TC_AUTH_ALLOWED_ORIGINS": "http://tr-abc.ecs.us-east-1.on.aws"}, {"TC_SESSION_KEY": ""},
    {"TC_SESSION_KEY": "corta"}, {"TC_SESSION_KEY": "a" * 63 + "!"}, {"TC_SESSION_EPOCH": ""},
    {"TC_SESSION_EPOCH": "uno"}, {"TC_SESSION_TTL_SECONDS": "0"}, {"TC_SESSION_TTL_SECONDS": "-5"},
]
CFGS_INVALID = [validate_auth_settings({})] + [validate_auth_settings(_raw(**o)) for o in _INVALID_OVERRIDES]
assert all(c.status == "INVALID" for c in CFGS_INVALID)

# Cookies firmadas por emisores distintos al de la config vigente.
_ISSUER_OTHER_EPOCH = _ok(TC_SESSION_EPOCH="2")
_ISSUER_OTHER_KEY = _ok(TC_SESSION_KEY="b" * 64)
_ISSUER_RETIRED = _ok(TC_AUTH_ALLOWED_SUBS=f"{SUB_OK},{SUB_RETIRED}")


def _valid_cookie(issued_at: int = NOW) -> str:
    return SessionCodec(CFG_OK).issue(SUB_OK, issued_at)


def _tamper(value: str, pos: int, new_char: str) -> str:
    """Cambia un carácter base64url que no sea el último de un segmento (sus bits pueden ser de relleno)."""
    candidates = [i for i, c in enumerate(value)
                  if c in _B64URL and i + 1 < len(value) and value[i + 1] != "." and i >= 3]
    i = candidates[pos % len(candidates)]
    repl = new_char if new_char != value[i] else ("A" if value[i] != "A" else "B")
    return value[:i] + repl + value[i + 1:]


# Una cookie = (descripción, valor o None, ¿válida para CFG_OK en el reloj NOW?).
cookies = st.one_of(
    st.just(("ninguna", None, False)),
    st.just(("valida", _valid_cookie(), True)),
    st.integers(min_value=0, max_value=TTL - 1).map(
        lambda age: ("valida_con_edad", _valid_cookie(NOW - age), True)),
    st.integers(min_value=0, max_value=10 * TTL).map(
        lambda extra: ("vencida", _valid_cookie(NOW - TTL - extra), False)),
    st.tuples(st.integers(min_value=0, max_value=10_000), st.sampled_from(_B64URL)).map(
        lambda t: ("alterada", _tamper(_valid_cookie(), t[0], t[1]), False)),
    st.just(("otra_epoca", SessionCodec(_ISSUER_OTHER_EPOCH).issue(SUB_OK, NOW), False)),
    st.just(("otra_clave", SessionCodec(_ISSUER_OTHER_KEY).issue(SUB_OK, NOW), False)),
    st.just(("sub_retirado", SessionCodec(_ISSUER_RETIRED).issue(SUB_RETIRED, NOW), False)),
    st.text(alphabet=_B64URL + ".=", max_size=200).map(lambda s: ("basura", s, False)),
)
configs = st.one_of(st.just(("OK", CFG_OK)), st.sampled_from(CFGS_INVALID).map(lambda c: ("INVALID", c)))
METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
PATHS = ["/static/index.html", "/static/app.js", "/static/x", "/docs", "/openapi.json", "/x",
         "/health", "/", "/login", "/auth/login.js", "/auth/enter", "/auth/logout",
         "//static/index.html", "/STATIC/index.html", "/static/../health", "/ws/chart"]
protected_requests = st.tuples(st.sampled_from(METHODS), st.sampled_from(PATHS)).filter(
    lambda mp: mp not in PUBLIC)


def _app() -> FastAPI:
    app = FastAPI()

    @app.api_route("/{path:path}", methods=METHODS)
    async def catch_all(request: Request):
        return PlainTextResponse(SECRET.decode())

    return app


def _http(cfg, method: str, path: str, cookie: str | None, accept_html: bool):
    headers = [(b"origin", ORIGIN.encode())]
    if cookie is not None:
        headers.append((b"cookie", f"{COOKIE_NAME}={cookie}".encode()))
    if accept_html:
        headers.append((b"accept", b"text/html"))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "https", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1),
             "server": ("testserver", 443)}
    sent: list = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(AuthGate(_app(), config=cfg, clock=lambda: NOW)(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


@PROPS
@given(cfg=configs, cookie=cookies, req=protected_requests, accept_html=st.booleans())
def test_http_protected_reaches_app_only_with_ok_config_and_valid_cookie(cfg, cookie, req, accept_html):
    cfg_kind, config = cfg
    _, value, valid = cookie
    method, path = req
    status, body = _http(config, method, path, value, accept_html)
    if cfg_kind == "OK" and valid:
        assert status == 200
        assert SECRET in body or method == "HEAD"
    else:
        assert status in (303, 401)
        assert SECRET not in body


@PROPS
@given(cfg=configs, cookie=cookies, origin=st.sampled_from([ORIGIN, None, "https://malo.example"]))
def test_websocket_endpoint_runs_only_with_ok_config_valid_cookie_and_origin(cfg, cookie, origin):
    cfg_kind, config = cfg
    _, value, valid = cookie
    ran: list = []
    app = FastAPI()

    @app.websocket("/ws/chart")
    async def ws(websocket: WebSocket):
        ran.append(True)
        await websocket.accept()
        await websocket.close()

    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if value is not None:
        headers.append((b"cookie", f"{COOKIE_NAME}={value}".encode()))
    scope = {"type": "websocket", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "scheme": "wss", "path": "/ws/chart", "raw_path": b"/ws/chart", "root_path": "",
             "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1),
             "server": ("testserver", 443), "subprotocols": []}
    inbox = [{"type": "websocket.connect"}]
    sent: list = []

    async def receive():
        return inbox.pop(0) if inbox else {"type": "websocket.disconnect", "code": 1000}

    async def send(message):
        sent.append(message)

    asyncio.run(AuthGate(app, config=config, clock=lambda: NOW)(scope, receive, send))
    if cfg_kind == "OK" and valid and origin == ORIGIN:
        assert ran == [True]
    else:
        assert ran == []
        closes = [m.get("code") for m in sent if m["type"] == "websocket.close"]
        assert closes in ([4401], [4403])
        assert all(m["type"] in ("websocket.accept", "websocket.close") for m in sent)
