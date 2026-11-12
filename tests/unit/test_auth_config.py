"""Pruebas de T1: AuthSettings / AuthConfig (estados OK/INVALID, fail-closed).

Solo datos sintéticos (UUID inventados, JWKS de prueba, clave "a"*64). Nunca se usan
valores reales de AWS ni del .env. Las pruebas se escriben antes de la implementación.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.api.auth.config import (
    AuthConfig,
    AuthSettings,
    load_auth_config,
    validate_auth_settings,
)

# --- Nombres de las 8 variables -------------------------------------------------
VARS = (
    "TC_AUTH_SUPABASE_URL",
    "TC_AUTH_PUBLISHABLE_KEY",
    "TC_AUTH_JWKS_JSON",
    "TC_AUTH_ALLOWED_SUBS",
    "TC_AUTH_ALLOWED_ORIGINS",
    "TC_SESSION_KEY",
    "TC_SESSION_EPOCH",
    "TC_SESSION_TTL_SECONDS",
)
# Las 7 obligatorias (TTL tiene default y no es obligatoria).
MANDATORY = tuple(v for v in VARS if v != "TC_SESSION_TTL_SECONDS")

# --- Datos sintéticos válidos ---------------------------------------------------
# JWKS de prueba: una sola clave ES256 (x/y de 43 chars base64url que decodifican a 32 bytes).
JWK_ES256 = {
    "alg": "ES256",
    "crv": "P-256",
    "ext": True,
    "key_ops": ["verify"],
    "kid": "0d63f10f-0d16-48e4-8b53-baf614b574cc",
    "kty": "EC",
    "use": "sig",
    "x": "JPdlg8l77lcPf4wxSMnACUPQ616wVqX2Uu9Q6ZJGLqU",
    "y": "AkjFprNzyDruo5hL-u0_gZ15ELaRcmKb1rGj-ckl8oE",
}
JWK_HS256 = {"alg": "HS256", "kty": "oct", "kid": "legacy-hs", "k": "c2VjcmV0"}

GOOD_JWKS = json.dumps({"keys": [JWK_ES256]})
UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
KEY64 = "a" * 64  # válida: 64 chars del conjunto [A-Za-z0-9_-]


def good_raw() -> dict:
    """Configuración válida completa (todos los campos presentes y correctos)."""
    return {
        "TC_AUTH_SUPABASE_URL": "https://svvzrmhvsjalhpitjcvy.supabase.co",
        "TC_AUTH_PUBLISHABLE_KEY": "sb_publishable_SeQlUSsK9WIH8Gag8NjyXA",
        "TC_AUTH_JWKS_JSON": GOOD_JWKS,
        "TC_AUTH_ALLOWED_SUBS": f"{UUID_A},{UUID_B}",
        "TC_AUTH_ALLOWED_ORIGINS": "https://tr-abc.ecs.us-east-1.on.aws",
        "TC_SESSION_KEY": KEY64,
        "TC_SESSION_EPOCH": "1",
        "TC_SESSION_TTL_SECONDS": "28800",
    }


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Borra las 8 variables del entorno antes de cada prueba."""
    for v in VARS:
        monkeypatch.delenv(v, raising=False)


# --- 1. Configuración válida completa -------------------------------------------
def test_valid_full_config_ok():
    cfg = validate_auth_settings(good_raw())
    assert cfg.status == "OK"
    assert cfg.invalid_fields == ()
    assert cfg.supabase_url == "https://svvzrmhvsjalhpitjcvy.supabase.co"
    assert cfg.publishable_key == "sb_publishable_SeQlUSsK9WIH8Gag8NjyXA"
    assert cfg.allowed_subs == frozenset({UUID_A, UUID_B})
    assert cfg.allowed_origins == frozenset({"https://tr-abc.ecs.us-east-1.on.aws"})
    assert cfg.session_key == b"a" * 64
    assert cfg.session_epoch == 1
    assert cfg.session_ttl_seconds == 28800
    assert set(cfg.jwks_keys.keys()) == {JWK_ES256["kid"]}


# --- 2. Sin ninguna variable -> 7 obligatorias inválidas ------------------------
def test_empty_config_invalid_mandatory():
    cfg = validate_auth_settings({})
    assert cfg.status == "INVALID"
    assert cfg.invalid_fields == tuple(sorted(MANDATORY))
    # TTL ausente no se marca inválido.
    assert "TC_SESSION_TTL_SECONDS" not in cfg.invalid_fields
    # INVALID => todos los valores None.
    assert cfg.supabase_url is None
    assert cfg.publishable_key is None
    assert cfg.jwks_keys is None
    assert cfg.allowed_subs is None
    assert cfg.allowed_origins is None
    assert cfg.session_key is None
    assert cfg.session_epoch is None
    assert cfg.session_ttl_seconds is None


# --- 3. Cada obligatoria ausente o "" -> solo ese nombre inválido ---------------
@pytest.mark.parametrize("field", MANDATORY)
@pytest.mark.parametrize("bad", [None, ""])
def test_each_mandatory_missing_or_empty(field, bad):
    raw = good_raw()
    raw[field] = bad
    cfg = validate_auth_settings(raw)
    assert cfg.status == "INVALID"
    assert cfg.invalid_fields == (field,)


# --- 4. Un valor inválido por regla de la tabla ---------------------------------
@pytest.mark.parametrize(
    "field,value",
    [
        ("TC_AUTH_SUPABASE_URL", "http://svvzrmhvsjalhpitjcvy.supabase.co"),
        ("TC_AUTH_SUPABASE_URL", "https://svvzrmhvsjalhpitjcvy.supabase.co/"),
        ("TC_AUTH_SUPABASE_URL", "https://host.co:443"),
        ("TC_AUTH_PUBLISHABLE_KEY", "sb_secret_x"),
        ("TC_AUTH_JWKS_JSON", "no-json"),
        ("TC_AUTH_JWKS_JSON", json.dumps({"foo": 1})),
        ("TC_AUTH_JWKS_JSON", json.dumps({"keys": [dict(JWK_ES256, d="AQID")]})),
        ("TC_AUTH_JWKS_JSON", json.dumps({"keys": [JWK_HS256]})),
        (
            "TC_AUTH_JWKS_JSON",
            json.dumps({"keys": [JWK_ES256, dict(JWK_ES256)]}),  # kid duplicado
        ),
        (
            "TC_AUTH_JWKS_JSON",
            json.dumps({"keys": [dict(JWK_ES256, x="A" * 42)]}),  # x de 42
        ),
        ("TC_AUTH_ALLOWED_SUBS", "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"),  # UUID en mayúsculas
        ("TC_AUTH_ALLOWED_SUBS", f"{UUID_A},,{UUID_B}"),  # elemento vacío
        ("TC_AUTH_ALLOWED_ORIGINS", "http://x.y"),
        ("TC_SESSION_KEY", "a" * 42),  # 42 < 43
        ("TC_SESSION_KEY", "a" * 42 + "+"),  # char fuera del conjunto
        ("TC_SESSION_KEY", KEY64 + "\n"),  # salto de línea final
        ("TC_SESSION_EPOCH", "0"),
        ("TC_SESSION_EPOCH", "-1"),
        ("TC_SESSION_TTL_SECONDS", "0"),
        ("TC_SESSION_TTL_SECONDS", "abc"),
    ],
)
def test_single_rule_invalid(field, value):
    raw = good_raw()
    raw[field] = value
    cfg = validate_auth_settings(raw)
    assert cfg.status == "INVALID"
    assert field in cfg.invalid_fields


# --- 5. HS256 + ES256 -> OK con solo la ES256 -----------------------------------
def test_jwks_mixed_keeps_only_es256():
    raw = good_raw()
    raw["TC_AUTH_JWKS_JSON"] = json.dumps({"keys": [JWK_HS256, JWK_ES256]})
    cfg = validate_auth_settings(raw)
    assert cfg.status == "OK"
    assert set(cfg.jwks_keys.keys()) == {JWK_ES256["kid"]}


# --- 6. Variables del entorno real vía load_auth_config -------------------------
def test_load_from_environment_ok(monkeypatch):
    for k, v in good_raw().items():
        monkeypatch.setenv(k, v)
    cfg = load_auth_config()
    assert cfg.status == "OK"
    assert cfg.session_epoch == 1


# --- 7. .env ignorado -----------------------------------------------------------
def test_env_file_ignored(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    lines = [f"{k}={v}" for k, v in good_raw().items()]
    env.write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = load_auth_config()
    assert cfg.status == "INVALID"


# --- 8. Variables ajenas no afectan ---------------------------------------------
def test_foreign_vars_ignored(monkeypatch):
    for k, v in good_raw().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DATABENTO_API_KEY", "irrelevant")
    monkeypatch.setenv("FOO", "bar")
    cfg = load_auth_config()
    assert cfg.status == "OK"


# --- 9. Log --------------------------------------------------------------------
def test_log_on_invalid(monkeypatch, caplog):
    import logging

    with caplog.at_level(logging.ERROR, logger="src.api.auth.config"):
        cfg = load_auth_config()  # entorno vacío => INVALID
    assert cfg.status == "INVALID"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    expected = "AUTH_CONFIG_INVALID campos=[" + ",".join(sorted(MANDATORY)) + "]"
    assert errors[0].getMessage() == expected


def test_no_log_on_ok(monkeypatch, caplog):
    import logging

    for k, v in good_raw().items():
        monkeypatch.setenv(k, v)
    with caplog.at_level(logging.ERROR, logger="src.api.auth.config"):
        cfg = load_auth_config()
    assert cfg.status == "OK"
    assert [r for r in caplog.records if r.levelno == logging.ERROR] == []


# --- 10. Sin fugas del valor de la clave ----------------------------------------
@pytest.mark.parametrize("valid", [True, False])
def test_no_key_leak(monkeypatch, caplog, valid):
    import logging

    sentinel = "SENTINELKEYVALUE" + ("a" * 48 if valid else "")  # inválida si <43
    raw = good_raw()
    raw["TC_SESSION_KEY"] = sentinel
    cfg = validate_auth_settings(raw)
    assert sentinel not in repr(cfg)
    assert sentinel not in str(cfg)
    settings_obj = AuthSettings(TC_SESSION_KEY=sentinel)
    assert sentinel not in repr(settings_obj)
    assert sentinel not in str(settings_obj)
    with caplog.at_level(logging.ERROR, logger="src.api.auth.config"):
        for k, v in raw.items():
            monkeypatch.setenv(k, str(v))
        load_auth_config()
    assert sentinel not in caplog.text


# --- 11. Hypothesis: nunca lanza ------------------------------------------------
@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    st.dictionaries(
        keys=st.sampled_from(VARS),
        values=st.one_of(st.none(), st.text()),
    )
)
def test_validate_never_raises(raw):
    cfg = validate_auth_settings(raw)
    assert cfg.status in ("OK", "INVALID")


# --- 12. Imports permitidos -----------------------------------------------------
def test_imports_whitelist():
    allowed = {
        "__future__", "base64", "binascii", "dataclasses", "json", "logging",
        "re", "types", "typing", "collections.abc", "pydantic", "pydantic_settings",
    }
    src = Path("src/api/auth/config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imported.add(n.name)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported <= allowed, f"imports no permitidos: {imported - allowed}"


# ==============================================================================
# T1.1 — validación acotada (enteros con tope, TTL <= 8 h, JWTS acotado y listas
# que NO recortan \n/\t). Cada caso es un ítem de pytest independiente.
# ==============================================================================

# --- T1.1 INVALID: un solo campo inválido, sin excepción ------------------------
@pytest.mark.parametrize(
    "field,value",
    [
        ("TC_SESSION_EPOCH", "1" * 5000),                 # 1: época enorme (no debe lanzar)
        ("TC_SESSION_EPOCH", "1" * 11),                   # 2: 11 dígitos > 10
        ("TC_SESSION_TTL_SECONDS", "1" * 5000),           # 3: TTL enorme (no debe lanzar)
        ("TC_SESSION_TTL_SECONDS", "28801"),              # 4: TTL > 28800
        ("TC_AUTH_JWKS_JSON", "[" * 200000),              # 5: JWKS gigante (sobre el límite)
        ("TC_AUTH_JWKS_JSON", "[" * 16000),               # 6: bajo el límite -> RecursionError capturado
        # 7 se agrega aparte (necesita construir el valor)
        ("TC_AUTH_ALLOWED_SUBS", UUID_A + "\n"),          # 8: SUBS con salto de línea final
        ("TC_AUTH_ALLOWED_SUBS", UUID_A + "\t"),          # 9: SUBS con tabulador final
        ("TC_AUTH_ALLOWED_ORIGINS", "https://a.b\n"),     # 10: ORIGINS con salto de línea final
    ],
    # IDs cortos y únicos: evitan que pytest incruste valores gigantes (p. ej.
    # "[" * 200000) en el nodeid, que en Windows desborda el límite de 32767
    # caracteres de la variable de entorno PYTEST_CURRENT_TEST.
    ids=[
        "epoch_5000",
        "epoch_11",
        "ttl_5000",
        "ttl_28801",
        "jwks_200k",
        "jwks_deep_16k",
        "subs_newline",
        "subs_tab",
        "origins_newline",
    ],
)
def test_t11_invalid_single_field(field, value):
    raw = good_raw()
    raw[field] = value
    cfg = validate_auth_settings(raw)
    assert cfg.status == "INVALID"
    assert cfg.invalid_fields == (field,)


def test_t11_invalid_jwks_padded_over_limit():
    """Caso 7: GOOD_JWKS con espacios tras el primer '{' hasta 16385 caracteres."""
    base = GOOD_JWKS
    idx = base.index("{") + 1
    pad = 16385 - len(base)
    assert pad > 0
    value = base[:idx] + (" " * pad) + base[idx:]
    assert len(value) == 16385
    raw = good_raw()
    raw["TC_AUTH_JWKS_JSON"] = value
    cfg = validate_auth_settings(raw)
    assert cfg.status == "INVALID"
    assert cfg.invalid_fields == ("TC_AUTH_JWKS_JSON",)


# --- T1.1 OK --------------------------------------------------------------------
def test_t11_ok_subs_spaces_trimmed():
    """Caso 11: espacios alrededor de cada UUID se recortan -> OK."""
    raw = good_raw()
    raw["TC_AUTH_ALLOWED_SUBS"] = " " + UUID_A + " , " + UUID_B + " "
    cfg = validate_auth_settings(raw)
    assert cfg.status == "OK"
    assert cfg.allowed_subs == frozenset({UUID_A, UUID_B})


def test_t11_ok_epoch_10_digits():
    """Caso 12: época de 10 dígitos (tope) -> OK."""
    raw = good_raw()
    raw["TC_SESSION_EPOCH"] = "9999999999"
    cfg = validate_auth_settings(raw)
    assert cfg.status == "OK"
    assert cfg.session_epoch == 9999999999


def test_t11_ok_ttl_max():
    """Caso 13: TTL = 28800 (tope) -> OK."""
    raw = good_raw()
    raw["TC_SESSION_TTL_SECONDS"] = "28800"
    cfg = validate_auth_settings(raw)
    assert cfg.status == "OK"
    assert cfg.session_ttl_seconds == 28800


def test_t11_ok_two_es256_keys():
    """Caso 14: dos claves ES256 (distinto kid) -> OK, jwks_keys con 2 entradas."""
    second = dict(JWK_ES256, kid="22222222-2222-2222-2222-222222222222")
    raw = good_raw()
    raw["TC_AUTH_JWKS_JSON"] = json.dumps({"keys": [JWK_ES256, second]})
    cfg = validate_auth_settings(raw)
    assert cfg.status == "OK"
    assert len(cfg.jwks_keys) == 2
