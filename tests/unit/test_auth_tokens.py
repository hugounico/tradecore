"""Pruebas de T2: TokenVerifier / verify (forma, firma ES256, claims, ventana de canje).

Las claves de prueba se generan en la propia prueba con `cryptography` (curva P-256); el JWKS
es sintético (kid `k1`, y `k2` para rotación). El `AuthConfig` se arma con
`validate_auth_settings`. Los tokens se firman con `jwt.encode`. `NOW` es un entero fijo.
Solo datos sintéticos: nunca se usan tokens, claves ni secretos reales. Las pruebas se
escriben antes de implementar `tokens.py`.
"""
from __future__ import annotations

import ast
import base64
import json
import socket
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.api.auth.config import validate_auth_settings
from src.api.auth.tokens import REASONS, TokenResult, TokenVerifier

# --- Constantes de prueba -------------------------------------------------------
NOW = 1_700_000_000  # entero epoch fijo; el módulo no lee el reloj
SUPABASE_URL = "https://svvzrmhvsjalhpitjcvy.supabase.co"
ISS = SUPABASE_URL + "/auth/v1"
AUD = "authenticated"
ROLE = "authenticated"
PUBKEY = "sb_publishable_SeQlUSsK9WIH8Gag8NjyXA"
ORIGINS = "https://tr-abc.ecs.us-east-1.on.aws"
SESSION_KEY = "a" * 64
# sub permitidos (deben ser UUID porque T1 valida TC_AUTH_ALLOWED_SUBS como lista de UUID).
SUB_OK = "11111111-2222-3333-4444-555555555555"
SUB_OK_2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SUB_NOT_ALLOWED = "99999999-9999-9999-9999-999999999999"
ALLOWED_SUBS = f"{SUB_OK},{SUB_OK_2}"


# --- Utilidades de claves y JWKS ------------------------------------------------
def _b64url_uint(value: int, length: int = 32) -> str:
    """Entero -> base64url sin padding, a `length` bytes big-endian (coordenada EC)."""
    raw = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _gen_key() -> tuple[ec.EllipticCurvePrivateKey, dict]:
    """Genera una clave privada P-256 y devuelve (privada, jwk_publico) SIN kid."""
    priv = ec.generate_private_key(ec.SECP256R1())
    nums = priv.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "alg": "ES256",
        "use": "sig",
        "key_ops": ["verify"],
        "x": _b64url_uint(nums.x),
        "y": _b64url_uint(nums.y),
    }
    return priv, jwk


def _jwk_with_kid(jwk: dict, kid: str) -> dict:
    """Copia el jwk agregándole un kid."""
    out = dict(jwk)
    out["kid"] = kid
    return out


def _priv_pem(priv: ec.EllipticCurvePrivateKey) -> bytes:
    """PEM PKCS8 de la clave privada, formato que acepta jwt.encode para ES256."""
    from cryptography.hazmat.primitives import serialization

    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _cfg(jwks_keys: list[dict], *, status_ok: bool = True):
    """Arma un AuthConfig via validate_auth_settings con el JWKS dado."""
    raw = {
        "TC_AUTH_SUPABASE_URL": SUPABASE_URL,
        "TC_AUTH_PUBLISHABLE_KEY": PUBKEY,
        "TC_AUTH_JWKS_JSON": json.dumps({"keys": jwks_keys}),
        "TC_AUTH_ALLOWED_SUBS": ALLOWED_SUBS,
        "TC_AUTH_ALLOWED_ORIGINS": ORIGINS,
        "TC_SESSION_KEY": SESSION_KEY,
        "TC_SESSION_EPOCH": "1",
        "TC_SESSION_TTL_SECONDS": "28800",
    }
    cfg = validate_auth_settings(raw)
    if status_ok:
        assert cfg.status == "OK", cfg.invalid_fields
    return cfg


def _good_payload(**over) -> dict:
    """Payload base válido; `over` sobreescribe/elimina (valor None => se omite) claims."""
    payload = {
        "iss": ISS,
        "aud": AUD,
        "role": ROLE,
        "sub": SUB_OK,
        "iat": NOW,
        "exp": NOW + 600,
    }
    for k, v in over.items():
        if v is _OMIT:
            payload.pop(k, None)
        else:
            payload[k] = v
    return payload


_OMIT = object()  # centinela para eliminar un claim en _good_payload


def _encode(payload: dict, priv: ec.EllipticCurvePrivateKey, kid: str,
            *, alg: str = "ES256", extra_header: dict | None = None) -> str:
    """Firma un token ES256 con el kid dado (y cabeceras extra opcionales)."""
    headers = {"kid": kid}
    if extra_header:
        headers.update(extra_header)
    return jwt.encode(payload, _priv_pem(priv), algorithm=alg, headers=headers)


# --- Fixtures: claves k1 y k2 ---------------------------------------------------
@pytest.fixture(scope="module")
def k1():
    """Clave k1: (privada, jwk con kid=k1)."""
    priv, jwk = _gen_key()
    return priv, _jwk_with_kid(jwk, "k1")


@pytest.fixture(scope="module")
def k2():
    """Clave k2: (privada, jwk con kid=k2)."""
    priv, jwk = _gen_key()
    return priv, _jwk_with_kid(jwk, "k2")


@pytest.fixture
def verifier(k1):
    """Verificador con un JWKS que contiene solo k1."""
    _, jwk1 = k1
    return TokenVerifier(_cfg([jwk1]))


# ================================================================================
# 1. Token válido
# ================================================================================
def test_valid_token_ok(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(), priv, "k1")
    res = verifier.verify(tok, NOW)
    assert res.ok is True
    assert res.sub == SUB_OK
    assert res.reason is None


# ================================================================================
# 2. Firma alterada / firmada con otra clave -> bad_sig
# ================================================================================
def test_signature_last_char_changed_bad_sig(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(), priv, "k1")
    last = "A" if tok[-1] != "A" else "B"  # cambia el último carácter (firma)
    tampered = tok[:-1] + last
    assert verifier.verify(tampered, NOW).reason == "bad_sig"


def test_signed_with_other_key_same_kid_bad_sig(verifier):
    other_priv, _ = _gen_key()  # clave P-256 distinta, pero firmamos con kid k1
    tok = _encode(_good_payload(), other_priv, "k1")
    assert verifier.verify(tok, NOW).reason == "bad_sig"


# ================================================================================
# 3. alg no permitido (HS256, none) -> bad_format
# ================================================================================
def test_hs256_bad_format(verifier):
    # Token HS256 con kid k1: el alg distinto de ES256 se rechaza en la cabecera.
    tok = jwt.encode(_good_payload(), "secret", algorithm="HS256", headers={"kid": "k1"})
    assert verifier.verify(tok, NOW).reason == "bad_format"


def test_alg_none_bad_format(verifier):
    # alg: none => header.payload. (tercer segmento vacío). Construido a mano.
    def seg(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    header = seg({"alg": "none", "kid": "k1"})
    body = seg(_good_payload())
    tok = f"{header}.{body}."  # firma vacía => no cumple la regex de 3 segmentos no vacíos
    assert verifier.verify(tok, NOW).reason == "bad_format"


# ================================================================================
# 4. kid desconocido / ausente / vacío / numérico
# ================================================================================
def test_unknown_kid(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(), priv, "kZZ")  # firmado k1 pero con kid desconocido
    assert verifier.verify(tok, NOW).reason == "unknown_kid"


def test_missing_kid_bad_format(verifier, k1):
    priv, _ = k1
    tok = jwt.encode(_good_payload(), _priv_pem(priv), algorithm="ES256")  # sin kid
    assert verifier.verify(tok, NOW).reason == "bad_format"


def test_empty_kid_bad_format(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(), priv, "")  # kid = "" (vacío)
    assert verifier.verify(tok, NOW).reason == "bad_format"


def test_numeric_kid_bad_format(verifier, k1):
    priv, _ = k1
    # kid numérico (no texto): se arma cabecera a mano manteniendo firma válida imposible,
    # así que basta comprobar que el tipo no-texto se rechaza antes de la firma.
    tok = _encode(_good_payload(), priv, "x")
    # Reemplazamos la cabecera por una con kid entero.
    import base64 as _b

    def seg(obj) -> str:
        return _b.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    parts = tok.split(".")
    parts[0] = seg({"alg": "ES256", "kid": 123})
    assert verifier.verify(".".join(parts), NOW).reason == "bad_format"


# ================================================================================
# 5. Forma inválida -> bad_format
# ================================================================================
@pytest.mark.parametrize(
    "tok",
    [
        "aaa.bbb",                       # 2 segmentos
        "aaa.bbb.ccc.ddd",               # 4 segmentos
        "a." + ("b" * 5000) + ".c",       # > 4096 caracteres
        "aa*.bb.cc",                     # carácter fuera de base64url
        "",                              # cadena vacía
    ],
    ids=["two_seg", "four_seg", "too_long", "bad_char", "empty"],
)
def test_malformed_strings_bad_format(verifier, tok):
    assert verifier.verify(tok, NOW).reason == "bad_format"


@pytest.mark.parametrize(
    "tok",
    [None, b"aaa.bbb.ccc", 12345],
    ids=["none", "bytes", "int"],
)
def test_non_str_bad_format(verifier, tok):
    assert verifier.verify(tok, NOW).reason == "bad_format"


# ================================================================================
# 6. exp
# ================================================================================
def test_exp_equal_now_expired(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(exp=NOW, iat=NOW), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "expired"


def test_exp_now_plus_one_ok(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(exp=NOW + 1, iat=NOW), priv, "k1")
    assert verifier.verify(tok, NOW).ok is True


# ================================================================================
# 7. iat / nbf futuro (skew 30 s)
# ================================================================================
def test_iat_plus_30_ok(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(iat=NOW + 30, exp=NOW + 600), priv, "k1")
    assert verifier.verify(tok, NOW).ok is True


def test_iat_plus_31_stale(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(iat=NOW + 31, exp=NOW + 600), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "stale"


def test_nbf_plus_30_ok(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(nbf=NOW + 30), priv, "k1")
    assert verifier.verify(tok, NOW).ok is True


def test_nbf_plus_31_stale(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(nbf=NOW + 31), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "stale"


# ================================================================================
# 8. Ventana de canje now - iat
# ================================================================================
def test_now_minus_iat_120_ok(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(iat=NOW - 120, exp=NOW + 600), priv, "k1")
    assert verifier.verify(tok, NOW).ok is True


def test_now_minus_iat_121_stale(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(iat=NOW - 121, exp=NOW + 600), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "stale"


# ================================================================================
# 9. iss / aud / role / sub
# ================================================================================
def test_iss_different_bad_iss(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(iss="https://otro.supabase.co/auth/v1"), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "bad_iss"


def test_iss_trailing_slash_bad_iss(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(iss=ISS + "/"), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "bad_iss"


def test_aud_different_bad_aud(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(aud="otro"), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "bad_aud"


def test_aud_as_list_bad_aud(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(aud=["authenticated"]), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "bad_aud"


def test_role_different_bad_role(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(role="anon"), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "bad_role"


def test_sub_not_allowed(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(sub=SUB_NOT_ALLOWED), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "not_allowed"


# ================================================================================
# 10. Claims faltantes / tipos inválidos -> bad_format
# ================================================================================
@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "role"], ids=["exp", "iat", "sub", "role"])
def test_missing_claim_bad_format(verifier, k1, missing):
    priv, _ = k1
    tok = _encode(_good_payload(**{missing: _OMIT}), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "bad_format"


@pytest.mark.parametrize("exp_val", [float(NOW + 600), str(NOW + 600), True], ids=["float", "str", "bool"])
def test_exp_bad_type_bad_format(verifier, k1, exp_val):
    priv, _ = k1
    tok = _encode(_good_payload(exp=exp_val, iat=NOW), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "bad_format"


# ================================================================================
# 11. Mismo token dos veces -> ambos ok (sin uso único)
# ================================================================================
def test_same_token_twice_ok(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(), priv, "k1")
    assert verifier.verify(tok, NOW).ok is True
    assert verifier.verify(tok, NOW).ok is True


# ================================================================================
# 12. Rotación de claves
# ================================================================================
def test_rotation_both_kids_ok(k1, k2):
    _, jwk1 = k1
    _, jwk2 = k2
    v = TokenVerifier(_cfg([jwk1, jwk2]))
    p1, _ = k1
    p2, _ = k2
    assert v.verify(_encode(_good_payload(), p1, "k1"), NOW).ok is True
    assert v.verify(_encode(_good_payload(), p2, "k2"), NOW).ok is True


def test_rotation_token_k2_unknown_kid(verifier, k2):
    # verifier solo conoce k1; token firmado con k2/kid k2 => unknown_kid.
    p2, _ = k2
    tok = _encode(_good_payload(), p2, "k2")
    assert verifier.verify(tok, NOW).reason == "unknown_kid"


# ================================================================================
# 13. Config INVALID / punto fuera de la curva -> config_invalid
# ================================================================================
def test_config_invalid_not_ready():
    bad = validate_auth_settings({})  # todo ausente => INVALID
    assert bad.status == "INVALID"
    v = TokenVerifier(bad)
    assert v.ready is False
    assert v.verify("a.b.c", NOW).reason == "config_invalid"


def test_point_off_curve_not_ready(k1):
    # x/y de formato válido (43 chars base64url, 32 bytes) pero punto que NO está en P-256.
    _, jwk1 = k1
    bad_jwk = dict(jwk1)
    bad_jwk["x"] = base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()
    bad_jwk["y"] = base64.urlsafe_b64encode(b"\x02" * 32).rstrip(b"=").decode()
    cfg = _cfg([bad_jwk])  # T1 acepta el formato; el punto inválido lo detecta PyJWK
    v = TokenVerifier(cfg)
    assert v.ready is False
    assert v.verify("a.b.c", NOW).reason == "config_invalid"


# ================================================================================
# 14. Hypothesis: texto arbitrario nunca lanza y nunca da ok
# ================================================================================
@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.text())
def test_arbitrary_text_never_raises(verifier, s):
    res = verifier.verify(s, NOW)
    assert res.ok is False
    assert res.reason in REASONS


# ================================================================================
# 15. Sin logs; repr no filtra el token
# ================================================================================
def test_no_logs_and_repr_hides_token(verifier, k1, caplog):
    priv, _ = k1
    good = _encode(_good_payload(), priv, "k1")
    bad = "no.es.valido"
    with caplog.at_level(0):
        r_ok = verifier.verify(good, NOW)
        r_bad = verifier.verify(bad, NOW)
    assert caplog.records == []
    # El repr del resultado no debe contener el token.
    assert good not in repr(r_ok)
    assert bad not in repr(r_bad)


# ================================================================================
# 16. Manipulación tras firmar -> bad_sig
# ================================================================================
def test_payload_tampered_after_sign_bad_sig(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(), priv, "k1")
    header, _body, sig = tok.split(".")
    new_body = base64.urlsafe_b64encode(
        json.dumps(_good_payload(sub=SUB_OK_2)).encode()
    ).rstrip(b"=").decode()
    tampered = f"{header}.{new_body}.{sig}"
    assert verifier.verify(tampered, NOW).reason == "bad_sig"


def test_header_tampered_after_sign_bad_sig(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(), priv, "k1")
    _header, body, sig = tok.split(".")
    # Nueva cabecera: mismo alg ES256 y kid k1, pero agregando "typ":"X" => firma deja de valer.
    new_header = base64.urlsafe_b64encode(
        json.dumps({"alg": "ES256", "kid": "k1", "typ": "X"}).encode()
    ).rstrip(b"=").decode()
    tampered = f"{new_header}.{body}.{sig}"
    assert verifier.verify(tampered, NOW).reason == "bad_sig"


# ================================================================================
# 17. Orden de razones con defectos combinados
# ================================================================================
def test_order_badsig_beats_expired(verifier, k1):
    priv, _ = k1
    tok = _encode(_good_payload(exp=NOW - 10, iat=NOW - 20), priv, "k1")
    tampered = tok[:-1] + ("A" if tok[-1] != "A" else "B")  # firma rota + exp vencido
    assert verifier.verify(tampered, NOW).reason == "bad_sig"


def test_order_unknownkid_beats_garbage(verifier, k1):
    priv, _ = k1
    # kid desconocido + payload basura: unknown_kid decide (antes de verificar firma/claims).
    def seg(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    header = seg({"alg": "ES256", "kid": "kZZ"})
    body = seg({"garbage": True})
    tok = f"{header}.{body}.AAAA"
    assert verifier.verify(tok, NOW).reason == "unknown_kid"


def test_order_badformat_beats_notallowed(verifier, k1):
    priv, _ = k1
    # bien firmado, exp como str (bad_format) + sub fuera de lista: bad_format decide.
    tok = _encode(_good_payload(exp=str(NOW + 600), iat=NOW, sub=SUB_NOT_ALLOWED), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "bad_format"


def test_order_expired_beats_badiss(verifier, k1):
    priv, _ = k1
    # bien firmado, vencido + iss incorrecto: expired decide (exp se evalúa antes que iss).
    tok = _encode(_good_payload(exp=NOW - 1, iat=NOW - 10, iss="https://otro/auth/v1"), priv, "k1")
    assert verifier.verify(tok, NOW).reason == "expired"


# ================================================================================
# 18. Sin red: un token válido sigue dando ok con socket.connect bloqueado
# ================================================================================
def test_no_network(verifier, k1, monkeypatch):
    def _boom(*a, **k):
        raise OSError("red bloqueada en la prueba")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    priv, _ = k1
    tok = _encode(_good_payload(), priv, "k1")
    assert verifier.verify(tok, NOW).ok is True


# ================================================================================
# 19. Imports de tokens.py (AST) ⊆ conjunto permitido
# ================================================================================
def test_imports_whitelist():
    path = Path("src/api/auth/tokens.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed = {
        "__future__", "dataclasses", "re", "typing", "collections.abc",
        "jwt", "src.api.auth.config",
    }
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= allowed, f"imports no permitidos: {mods - allowed}"
