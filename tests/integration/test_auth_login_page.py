"""Pruebas de T6b: página `GET /login` y `GET /auth/login.js` (HTTP + estáticas + regresión).

No repiten la prueba de navegador de Claude: solo verifican contrato HTTP (status, cabeceras,
CSP, cuerpo renderizado) y propiedades estáticas de `login.html`/`login.js` por lectura de disco.
Sin src.api.app, red, Databento ni AWS. Cliente nuevo por prueba y sin `with`. Solo datos
sintéticos. Los atributos del módulo bajo prueba se leen dentro de cada prueba.
"""
from __future__ import annotations

import base64
import json
import re
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

# --- Constantes sintéticas (como en test_auth_gate.py) --------------------------
NOW = 1_700_000_000
TTL = 28800
SUPABASE_URL = "https://svvzrmhvsjalhpitjcvy.supabase.co"
ISS = SUPABASE_URL + "/auth/v1"
PUBKEY = "sb_publishable_SeQlUSsK9WIH8Gag8NjyXA"
ORIGIN = "https://tr-abc.ecs.us-east-1.on.aws"
SESSION_KEY = "a" * 64
SUB_OK = "11111111-2222-3333-4444-555555555555"
SUB_OK_2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ALLOWED_SUBS = f"{SUB_OK},{SUB_OK_2}"

STATIC = Path("src/api/auth/static")
CSP_OK = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
          f"connect-src {SUPABASE_URL}; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
CSP_INVALID = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
               "connect-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
SEC_NAMES = ["strict-transport-security", "x-content-type-options",
             "referrer-policy", "x-frame-options"]


# --- Claves / tokens ------------------------------------------------------------
def _b64url_uint(value: int, length: int = 32) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


def _gen_key():
    priv = ec.generate_private_key(ec.SECP256R1())
    nums = priv.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "key_ops": ["verify"],
           "x": _b64url_uint(nums.x), "y": _b64url_uint(nums.y), "kid": "k1"}
    return priv, jwk


def _priv_pem(priv) -> bytes:
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


_PRIV, JWK = _gen_key()


# --- Config ---------------------------------------------------------------------
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


def build_gate(cfg, *, clock=None):
    clock = clock or (lambda: NOW)
    app = FastAPI()
    app.include_router(rt.build_auth_router(cfg, clock=clock, bucket=TokenBucket(clock=lambda: 0)))

    @app.get("/health")
    async def health():
        return PlainTextResponse("ok-health")

    return AuthGate(app, config=cfg, clock=clock)


def client_for(cfg, *, clock=None) -> TestClient:
    return TestClient(build_gate(cfg, clock=clock), follow_redirects=False)


def set_cookies(resp) -> list[str]:
    return [v.decode() for k, v in resp.headers.raw if k.decode().lower() == "set-cookie"]


def count_header(resp, name: str) -> int:
    return sum(1 for k, _ in resp.headers.raw if k.decode().lower() == name.lower())


# ================================================================================
# 1. GET /login sin cookie
# ================================================================================
def test_login_page_anon():
    c = client_for(_cfg())
    resp = c.get("/login")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert resp.headers["cache-control"] == "no-store"
    assert count_header(resp, "cache-control") == 1
    assert resp.headers["content-security-policy"] == CSP_OK
    for n in SEC_NAMES:
        assert count_header(resp, n) == 1
    body = resp.text
    assert 'data-view="login"' in body
    assert f'<meta name="tc-supabase-url" content="{SUPABASE_URL}">' in body
    assert f'<meta name="tc-publishable-key" content="{PUBKEY}">' in body
    assert "__TC_" not in body
    assert set_cookies(resp) == []


# ================================================================================
# 2. Vista según la cookie
# ================================================================================
def test_login_page_valid_cookie_session_view():
    c = client_for(_cfg())
    resp = c.get("/login", headers=_cookie_header(_session_cookie()))
    assert resp.status_code == 200
    body = resp.text
    assert 'data-view="session"' in body
    assert 'action="/auth/logout"' in body
    assert 'href="/static/index.html"' in body


@pytest.mark.parametrize("kind", ["expired", "tampered"], ids=["expired", "tampered"])
def test_login_page_bad_cookie_login_view(kind):
    cookie = _session_cookie()
    if kind == "expired":
        c = client_for(_cfg(), clock=lambda: NOW + TTL)
    else:
        h, p, s = cookie.split(".")
        ch = "B" if s[0] != "B" else "C"
        cookie = f"{h}.{p}.{ch}{s[1:]}"
        c = client_for(_cfg())
    resp = c.get("/login", headers=_cookie_header(cookie))
    assert resp.status_code == 200
    assert 'data-view="login"' in resp.text


# ================================================================================
# 3. Config INVALID
# ================================================================================
@pytest.mark.parametrize("with_cookie", [False, True], ids=["anon", "cookie"])
def test_login_page_config_invalid(with_cookie):
    c = client_for(_cfg_invalid())
    headers = _cookie_header(_session_cookie()) if with_cookie else {}
    resp = c.get("/login", headers=headers)
    assert resp.status_code == 200
    body = resp.text
    assert 'data-view="login"' in body
    assert '<meta name="tc-supabase-url" content="">' in body
    assert '<meta name="tc-publishable-key" content="">' in body
    assert resp.headers["content-security-policy"] == CSP_INVALID


# ================================================================================
# 4. GET /auth/login.js
# ================================================================================
@pytest.mark.parametrize("invalid", [False, True], ids=["ok", "invalid"])
def test_login_js(invalid):
    cfg = _cfg_invalid() if invalid else _cfg()
    c = client_for(cfg)
    resp = c.get("/auth/login.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/javascript; charset=utf-8"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["content-security-policy"] == (CSP_INVALID if invalid else CSP_OK)
    assert resp.content == (STATIC / "login.js").read_bytes()
    assert set_cookies(resp) == []


# ================================================================================
# 5. Métodos no permitidos → 401 del gate
# ================================================================================
@pytest.mark.parametrize(
    "method, path",
    [("POST", "/login"), ("HEAD", "/login"), ("POST", "/auth/login.js")],
    ids=["post_login", "head_login", "post_loginjs"],
)
def test_wrong_method_401(method, path):
    c = client_for(_cfg())
    assert c.request(method, path).status_code == 401


# ================================================================================
# 6. Otras rutas no llevan CSP
# ================================================================================
def test_other_routes_no_csp():
    c = client_for(_cfg())
    assert "content-security-policy" not in c.get("/").headers
    assert "content-security-policy" not in c.get("/health").headers
    logout = c.post("/auth/logout", headers={"origin": ORIGIN})
    assert "content-security-policy" not in logout.headers


# ================================================================================
# 7. Propiedades estáticas de los archivos
# ================================================================================
def test_static_files_properties():
    html_bytes = (STATIC / "login.html").read_bytes()
    js_bytes = (STATIC / "login.js").read_bytes()
    for raw in (html_bytes, js_bytes):
        assert not raw.startswith(b"\xef\xbb\xbf")  # sin BOM
        assert b"\r" not in raw                      # solo LF
        raw.decode("utf-8")                          # UTF-8 válido
    html_text = html_bytes.decode("utf-8")
    for marker in ("__TC_SUPABASE_URL__", "__TC_PUBLISHABLE_KEY__", "__TC_VIEW__"):
        assert html_text.count(marker) == 1
    assert html_text.count("<script") == 1
    assert '<script src="/auth/login.js" defer></script>' in html_text
    assert re.search(r"\son[a-z]+\s*=", html_text) is None
    assert "http://" not in html_text and "https://" not in html_text
    js_text = js_bytes.decode("utf-8")
    assert js_text.count('credentials: "omit"') == 2
    assert js_text.count('cache: "no-store"') == 2
    assert '"/auth/v1/token?grant_type=password"' in js_text
    assert '"/auth/v1/user"' in js_text
    assert '"/auth/enter"' in js_text
    for forbidden in ("localStorage", "sessionStorage", "indexedDB", "document.cookie",
                      "console.", "innerHTML", "eval(", "new Function", "refresh_token"):
        assert forbidden not in js_text, forbidden


# ================================================================================
# 8. Router oculto y sin lectura de entorno
# ================================================================================
def test_router_with_config_does_not_load(monkeypatch):
    def boom():
        raise AssertionError("no debe leer el entorno")

    monkeypatch.setattr(rt, "load_auth_config", boom)
    rt.build_auth_router(_cfg())


def test_login_routes_hidden_from_openapi():
    app = FastAPI()
    app.include_router(rt.build_auth_router(_cfg(), bucket=TokenBucket(clock=lambda: 0)))
    paths = app.openapi()["paths"]
    assert "/login" not in paths
    assert "/auth/login.js" not in paths


# ================================================================================
# 9. Coherencia HTML ↔ JS
# ================================================================================
def test_html_js_consistency():
    html_text = (STATIC / "login.html").read_text(encoding="utf-8")
    js_text = (STATIC / "login.js").read_text(encoding="utf-8")
    used_ids = set(re.findall(r'\$\("([^"]+)"\)', js_text))
    assert used_ids, "no se hallaron ids usados por JS"
    html_ids = set(re.findall(r'id="([^"]+)"', html_text))
    missing = used_ids - html_ids
    assert missing == set(), f"ids usados por JS y ausentes en HTML: {missing}"

    def input_or_button(el_id: str) -> str:
        m = re.search(r'<(input|button|a)\b[^>]*\bid="' + re.escape(el_id) + r'"[^>]*>', html_text)
        assert m, f"no se encontró el elemento id={el_id}"
        return m.group(0)

    for submit_id in ("login-submit", "pw-submit", "logout-submit"):
        assert 'type="submit"' in input_or_button(submit_id), submit_id
    for button_id in ("show-password", "show-login"):
        assert 'type="button"' in input_or_button(button_id), button_id

    assert 'id="pw-current"' in html_text  # el cambio exige la contraseña actual
    assert re.search(r'<form[^>]*method="post"[^>]*action="/auth/logout"', html_text) \
        or re.search(r'<form[^>]*action="/auth/logout"[^>]*method="post"', html_text)

    # Ningún input de credenciales tiene atributo name= (si JS falla, nada viaja en la URL).
    for inp in re.findall(r"<input\b[^>]*>", html_text):
        assert "name=" not in inp, inp
