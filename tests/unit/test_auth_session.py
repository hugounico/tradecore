"""Pruebas de T3: SessionCodec / cookie de sesión firmada HMAC-SHA256 (§8), sin renovación.

Solo datos sintéticos (UUID inventados, clave de sesión "a"*64). Nunca valores reales. Las
pruebas se escriben antes de la implementación. `NOW` es un entero fijo; el módulo no lee el
reloj.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import inspect
import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.api.auth import session as session_mod
from src.api.auth.config import validate_auth_settings
from src.api.auth.session import (
    COOKIE_NAME,
    SESSION_REASONS,
    SessionCodec,
    SessionResult,
    extract_session_cookie,
)

# --- Constantes de prueba -------------------------------------------------------
NOW = 1_700_000_000
TTL = 28800
EPOCH = 1
SUPABASE_URL = "https://svvzrmhvsjalhpitjcvy.supabase.co"
PUBKEY = "sb_publishable_SeQlUSsK9WIH8Gag8NjyXA"
ORIGINS = "https://tr-abc.ecs.us-east-1.on.aws"
SESSION_KEY = "a" * 64                 # texto; T1 la guarda como bytes UTF-8
SESSION_KEY_BYTES = SESSION_KEY.encode("utf-8")
OTHER_KEY_BYTES = ("b" * 64).encode("utf-8")
# JWKS mínimo válido (necesario para que la config sea OK en T1).
JWK_ES256 = {
    "alg": "ES256", "crv": "P-256", "kty": "EC", "use": "sig", "key_ops": ["verify"],
    "kid": "k1",
    "x": "JPdlg8l77lcPf4wxSMnACUPQ616wVqX2Uu9Q6ZJGLqU",
    "y": "AkjFprNzyDruo5hL-u0_gZ15ELaRcmKb1rGj-ckl8oE",
}
GOOD_JWKS = json.dumps({"keys": [JWK_ES256]})
SUB_OK = "11111111-2222-3333-4444-555555555555"
SUB_OK_2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SUB_NOT_ALLOWED = "99999999-9999-9999-9999-999999999999"
ALLOWED_SUBS = f"{SUB_OK},{SUB_OK_2}"


# --- Utilidades base64url / firma ----------------------------------------------
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_json(obj) -> str:
    text = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    return _b64u(text.encode("utf-8"))


def _sign(seg1: str, key: bytes = SESSION_KEY_BYTES) -> str:
    """Firma base64url de HMAC-SHA256 sobre 'v1.'+seg1 (cubre el prefijo)."""
    mac = hmac.new(key, ("v1." + seg1).encode("ascii"), hashlib.sha256).digest()
    return _b64u(mac)


def _make(payload: dict, *, key: bytes = SESSION_KEY_BYTES, prefix_in_sig: bool = True) -> str:
    """Construye una cookie v1 a partir de un payload dict, firmando con la clave dada."""
    seg1 = _b64u_json(payload)
    if prefix_in_sig:
        sig = _sign(seg1, key)
    else:
        # firma SIN el prefijo v1. (caso negativo)
        mac = hmac.new(key, seg1.encode("ascii"), hashlib.sha256).digest()
        sig = _b64u(mac)
    return f"v1.{seg1}.{sig}"


def _good_payload(**over) -> dict:
    p = {"ep": EPOCH, "exp": NOW + TTL, "iat": NOW, "sub": SUB_OK}
    p.update(over)
    return p


# --- Config helpers -------------------------------------------------------------
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


@pytest.fixture
def codec():
    return SessionCodec(_cfg())


# ================================================================================
# 1. issue -> verify ok; longitud; determinismo
# ================================================================================
def test_issue_verify_ok(codec):
    v = codec.issue(SUB_OK, NOW)
    assert len(v) <= 512
    res = codec.verify(v, NOW)
    assert res.ok is True
    assert res.sub == SUB_OK
    assert res.exp == NOW + TTL
    assert res.reason is None


def test_issue_deterministic(codec):
    assert codec.issue(SUB_OK, NOW) == codec.issue(SUB_OK, NOW)


# ================================================================================
# 2. Firma -> bad_sig
# ================================================================================
def test_payload_tampered_keeps_sig_bad_sig(codec):
    v = codec.issue(SUB_OK, NOW)
    _, _seg1, sig = v.split(".")
    new_seg1 = _b64u_json(_good_payload(sub=SUB_OK_2))
    assert codec.verify(f"v1.{new_seg1}.{sig}", NOW).reason == "bad_sig"


def test_sig_one_char_changed_bad_sig(codec):
    v = codec.issue(SUB_OK, NOW)
    h, seg1, sig = v.split(".")
    # Cambia un carácter de la firma que NO es el último (bits de relleno: lección T2.1).
    ch = "B" if sig[0] != "B" else "C"
    tampered = f"{h}.{seg1}.{ch}{sig[1:]}"
    assert codec.verify(tampered, NOW).reason == "bad_sig"


def test_signed_other_key_bad_sig(codec):
    v = _make(_good_payload(), key=OTHER_KEY_BYTES)
    assert codec.verify(v, NOW).reason == "bad_sig"


def test_sig_without_prefix_bad_sig(codec):
    v = _make(_good_payload(), prefix_in_sig=False)
    assert codec.verify(v, NOW).reason == "bad_sig"


# ================================================================================
# 3. Expiración
# ================================================================================
def test_verify_exp_minus_one_ok(codec):
    v = codec.issue(SUB_OK, NOW)
    assert codec.verify(v, NOW + TTL - 1).ok is True


def test_verify_at_exp_expired(codec):
    v = codec.issue(SUB_OK, NOW)
    assert codec.verify(v, NOW + TTL).reason == "expired"


# ================================================================================
# 4. Época (revocación global)
# ================================================================================
def test_bad_epoch(codec):
    # cookie de época 1, config de época 2.
    v = codec.issue(SUB_OK, NOW)
    codec2 = SessionCodec(_cfg(TC_SESSION_EPOCH="2"))
    assert codec2.verify(v, NOW).reason == "bad_epoch"


# ================================================================================
# 5. sub retirado de la lista
# ================================================================================
def test_not_allowed(codec):
    v = codec.issue(SUB_OK, NOW)
    codec2 = SessionCodec(_cfg(TC_AUTH_ALLOWED_SUBS=SUB_OK_2))  # ya no incluye SUB_OK
    assert codec2.verify(v, NOW).reason == "not_allowed"


# ================================================================================
# 6. missing / bad_format de forma
# ================================================================================
@pytest.mark.parametrize("value", [None, ""], ids=["none", "empty"])
def test_missing(codec, value):
    assert codec.verify(value, NOW).reason == "missing"


def _good_sig_42():
    seg1 = _b64u_json(_good_payload())
    return f"v1.{seg1}.{_sign(seg1)[:42]}"  # firma de 42 chars


def _good_sig_44():
    seg1 = _b64u_json(_good_payload())
    return f"v1.{seg1}.{_sign(seg1)}A"  # firma de 44 chars


@pytest.mark.parametrize(
    "value, tid",
    [
        (b"v1.aaa.bbb", "bytes"),
        (123, "int"),
        ("v2." + _b64u_json(_good_payload()) + "." + _sign(_b64u_json(_good_payload())), "v2_prefix"),
        ("v1.aaa", "two_seg"),
        ("v1.aaa.bbb.ccc", "four_seg"),
        ("v1." + ("a" * 600) + "." + ("b" * 43), "too_long"),
        ("v1.aa*aa." + ("b" * 43), "bad_char"),
        (_good_sig_42(), "sig42"),
        (_good_sig_44(), "sig44"),
    ],
    ids=lambda x: x if isinstance(x, str) else None,
)
def test_bad_format_shape(codec, value, tid):
    assert codec.verify(value, NOW).reason == "bad_format"


# ================================================================================
# 7. Firma válida pero seg1 con contenido inválido -> bad_format
# ================================================================================
def _signed_seg1(seg1: str) -> str:
    """Arma una cookie con firma HMAC válida sobre un seg1 arbitrario (texto base64url)."""
    return f"v1.{seg1}.{_sign(seg1)}"


def test_payload_json_invalid_bad_format(codec):
    seg1 = _b64u(b"no es json")
    assert codec.verify(_signed_seg1(seg1), NOW).reason == "bad_format"


def test_payload_list_bad_format(codec):
    seg1 = _b64u(json.dumps([1, 2, 3]).encode())
    assert codec.verify(_signed_seg1(seg1), NOW).reason == "bad_format"


def test_payload_missing_ep_bad_format(codec):
    seg1 = _b64u_json({"exp": NOW + TTL, "iat": NOW, "sub": SUB_OK})
    assert codec.verify(_signed_seg1(seg1), NOW).reason == "bad_format"


def test_payload_extra_key_bad_format(codec):
    seg1 = _b64u_json({"ep": EPOCH, "exp": NOW + TTL, "iat": NOW, "sub": SUB_OK, "x": 1})
    assert codec.verify(_signed_seg1(seg1), NOW).reason == "bad_format"


def test_payload_exp_bool_bad_format(codec):
    seg1 = _b64u_json({"ep": EPOCH, "exp": True, "iat": NOW, "sub": SUB_OK})
    assert codec.verify(_signed_seg1(seg1), NOW).reason == "bad_format"


def test_payload_exp_str_bad_format(codec):
    seg1 = _b64u_json({"ep": EPOCH, "exp": "1", "iat": NOW, "sub": SUB_OK})
    assert codec.verify(_signed_seg1(seg1), NOW).reason == "bad_format"


def test_payload_sub_empty_bad_format(codec):
    seg1 = _b64u_json({"ep": EPOCH, "exp": NOW + TTL, "iat": NOW, "sub": ""})
    assert codec.verify(_signed_seg1(seg1), NOW).reason == "bad_format"


# ================================================================================
# 8. Config INVALID / issue con argumentos inválidos -> ValueError
# ================================================================================
def test_config_invalid():
    bad = validate_auth_settings({})
    assert bad.status == "INVALID"
    c = SessionCodec(bad)
    assert c.ready is False
    assert c.verify("v1.a.b", NOW).reason == "config_invalid"
    with pytest.raises(ValueError):
        c.issue(SUB_OK, NOW)


def test_issue_sub_not_allowed_raises(codec):
    with pytest.raises(ValueError):
        codec.issue(SUB_NOT_ALLOWED, NOW)


def test_issue_sub_not_str_raises(codec):
    with pytest.raises(ValueError):
        codec.issue(123, NOW)


@pytest.mark.parametrize("now", [True, 1.5, "1"], ids=["bool", "float", "str"])
def test_issue_now_invalid_raises(codec, now):
    with pytest.raises(ValueError):
        codec.issue(SUB_OK, now)


# ================================================================================
# 9. Cabeceras Set-Cookie
# ================================================================================
def test_set_cookie_header_exact(codec):
    v = codec.issue(SUB_OK, NOW)
    expected = (f"__Host-tc_session={v}; Max-Age={TTL}; Path=/; Secure; HttpOnly; "
                "SameSite=Lax")
    assert codec.set_cookie_header(v) == expected


def test_clear_cookie_header_exact(codec):
    assert codec.clear_cookie_header() == (
        "__Host-tc_session=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"
    )


@pytest.mark.parametrize(
    "value, tid",
    [
        ("v1.aaa.bbb\r\nX: y", "crlf"),
        ("v1.aaa.bbb; Domain=x", "domain"),
        ("", "empty"),
        ("no-format", "bad_format"),
    ],
    ids=["crlf", "domain", "empty", "bad_format"],
)
def test_set_cookie_header_rejects(codec, value, tid):
    with pytest.raises(ValueError):
        codec.set_cookie_header(value)


# ================================================================================
# 10. extract_session_cookie
# ================================================================================
def test_extract_only_cookie():
    assert extract_session_cookie("__Host-tc_session=abc") == "abc"


def test_extract_among_others():
    h = "foo=1; __Host-tc_session=abc; bar=2"
    assert extract_session_cookie(h) == "abc"


def test_extract_with_spaces():
    h = "foo=1 ;  __Host-tc_session=abc ; bar=2"
    assert extract_session_cookie(h) == "abc"


@pytest.mark.parametrize(
    "h, tid",
    [
        (None, "none"),
        ("", "empty"),
        ("foo=1; bar=2", "absent"),
        ("__Host-tc_session2=abc", "lookalike2"),
        ("tc_session=abc", "lookalike_short"),
        ("__Host-tc_session=a; __Host-tc_session=b", "duplicate"),
    ],
    ids=["none", "empty", "absent", "lookalike2", "lookalike_short", "duplicate"],
)
def test_extract_none(h, tid):
    assert extract_session_cookie(h) is None


# ================================================================================
# 11. Sin renovación: superficie pública exacta
# ================================================================================
def test_public_surface():
    methods = {
        n for n, _ in inspect.getmembers(SessionCodec, inspect.isfunction)
        if not n.startswith("_")
    }
    assert methods == {"issue", "verify", "set_cookie_header", "clear_cookie_header"}
    funcs = {
        n for n, f in inspect.getmembers(session_mod, inspect.isfunction)
        if not n.startswith("_") and f.__module__ == session_mod.__name__
    }
    assert funcs == {"extract_session_cookie"}


def test_verify_returns_only_sessionresult(codec):
    v = codec.issue(SUB_OK, NOW)
    res = codec.verify(v, NOW)
    assert isinstance(res, SessionResult)
    assert res.exp == NOW + TTL


# ================================================================================
# 12. Sin estado: otro codec con la misma config valida la cookie
# ================================================================================
def test_stateless(codec):
    v = codec.issue(SUB_OK, NOW)
    other = SessionCodec(_cfg())
    assert other.verify(v, NOW).ok is True


# ================================================================================
# 13. Hypothesis: texto arbitrario nunca lanza ni da ok
# ================================================================================
@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.text())
def test_arbitrary_text_never_raises(codec, s):
    res = codec.verify(s, NOW)
    assert res.ok is False
    assert res.reason in SESSION_REASONS


# ================================================================================
# 14. Sin fugas
# ================================================================================
def test_no_logs_and_repr(codec, caplog):
    v = codec.issue(SUB_OK, NOW)
    with caplog.at_level(0):
        res = codec.verify(v, NOW)
    assert caplog.records == []
    assert v not in repr(res)


# ================================================================================
# 15. Prioridad de razones
# ================================================================================
def test_order_badsig_beats_expired(codec):
    # firma alterada + vencida => bad_sig (la firma se evalúa antes).
    v = _make(_good_payload(exp=NOW - 10, iat=NOW - 20))
    h, seg1, sig = v.split(".")
    ch = "B" if sig[0] != "B" else "C"
    tampered = f"{h}.{seg1}.{ch}{sig[1:]}"
    assert codec.verify(tampered, NOW).reason == "bad_sig"


def test_order_expired_beats_badepoch(codec):
    # firma válida + vencida + época antigua => expired (exp antes que ep).
    v = _make(_good_payload(ep=99, exp=NOW - 1, iat=NOW - 10))
    assert codec.verify(v, NOW).reason == "expired"


def test_order_badepoch_beats_notallowed(codec):
    # firma válida + época antigua + sub retirado => bad_epoch (ep antes que sub).
    v = _make(_good_payload(ep=99, sub=SUB_NOT_ALLOWED))
    assert codec.verify(v, NOW).reason == "bad_epoch"


# ================================================================================
# 16. AST de session.py
# ================================================================================
def test_ast_imports_and_rules():
    path = Path("src/api/auth/session.py")
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed = {
        "__future__", "base64", "binascii", "dataclasses", "hashlib", "hmac",
        "json", "re", "typing", "collections.abc", "src.api.auth.config",
    }
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= allowed, f"imports no permitidos: {mods - allowed}"
    assert "compare_digest" in src
    # No usa `except Exception`.
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            name = getattr(node.type, "id", None)
            assert name != "Exception"
