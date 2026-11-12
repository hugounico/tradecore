"""Verificación del access_token de Supabase (ES256) — forma, firma y claims.

Este módulo NO lee el reloj (quien llama pasa `now` en segundos epoch), NO registra logs y
NUNCA incluye el token en lo que devuelve. Para cualquier entrada devuelve un `TokenResult`
sin lanzar: solo se capturan `jwt.PyJWTError` y `ValueError` en los pasos de firma/decodificación
(ver `verify`); un `except Exception` quedaría prohibido porque ocultaría errores de programación.

Invariante del resultado:
- `ok=True`  ⇒ `sub` no vacío y `reason=None`.
- `ok=False` ⇒ `sub=None` y `reason` ∈ `REASONS`.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

import jwt

from src.api.auth.config import AuthConfig

# Razones posibles de rechazo (orden del contrato). `ok=True` siempre lleva `reason=None`.
REASONS = ("config_invalid", "bad_format", "unknown_kid", "bad_sig", "expired",
           "stale", "bad_iss", "bad_aud", "bad_role", "not_allowed")

# Forma de un JWT compacto: 3 segmentos base64url no vacíos separados por puntos. \A..\Z para
# que un salto de línea al inicio/final no sea tolerado (a diferencia de ^...$ en Python).
_RE_JWT = re.compile(r"\A[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")

# Límites y constantes del contrato.
_MAX_TOKEN_LEN = 4096          # tokens más largos se rechazan sin verificar (bad_format)
_IAT_FUTURE_SKEW = 30          # iat/nbf pueden adelantarse como mucho 30 s respecto de now
_CANJE_WINDOW = 120            # now - iat no puede superar 120 s (ventana de canje)

# Opciones de jwt.decode: PyJWT SOLO verifica la firma. Todos los claims (exp, iat, nbf, aud,
# iss, sub...) se validan a mano más abajo, para no depender de su `leeway` (que también
# aflojaría `exp`) ni de sus mensajes de error.
_DECODE_OPTIONS = {
    "verify_signature": True,
    "verify_exp": False,
    "verify_nbf": False,
    "verify_iat": False,
    "verify_aud": False,
    "verify_iss": False,
    "verify_sub": False,
    "verify_jti": False,
}


@dataclass(frozen=True)
class TokenResult:
    """Resultado de verificar un token. Nunca contiene el token ni material sensible."""

    ok: bool
    sub: str | None      # presente solo si ok=True
    reason: str | None   # presente solo si ok=False; uno de REASONS


# Resultados precomputados para cada rechazo (inmutables; ahorran crear objetos repetidos).
def _fail(reason: str) -> TokenResult:
    return TokenResult(ok=False, sub=None, reason=reason)


def _is_int(value: object) -> bool:
    """Entero verdadero: `int` que NO es `bool` (en Python `True`/`False` son int)."""
    return isinstance(value, int) and not isinstance(value, bool)


class TokenVerifier:
    """Verifica tokens ES256 contra un `AuthConfig`. Construye las claves una sola vez."""

    def __init__(self, cfg: AuthConfig) -> None:
        self._cfg = cfg
        # _keys mapea kid -> objeto de clave pública que entiende jwt.decode (PyJWK.key).
        self._keys: dict[str, object] = {}
        self.ready: bool = False

        # V0: fail-closed. Si la config no está OK, el verificador queda inutilizable.
        if cfg.status != "OK" or cfg.jwks_keys is None:
            return

        # Construye una PyJWK por cada kid. Si alguna falla (p. ej. punto fuera de la curva
        # P-256), el verificador entero queda ready=False (no se acepta ningún token).
        try:
            for kid, jwk in cfg.jwks_keys.items():
                pyjwk = jwt.PyJWK(dict(jwk), algorithm="ES256")
                self._keys[kid] = pyjwk.key
        except (jwt.PyJWTError, ValueError, TypeError):
            # PyJWTError: JWK inválida para PyJWT. ValueError/TypeError: punto/campo inválido
            # al reconstruir la curva en `cryptography`.
            self._keys = {}
            return

        self.ready = True

    def verify(self, token: object, now: int) -> TokenResult:
        """Devuelve un TokenResult para cualquier entrada, sin lanzar nunca."""
        # V0: con ready=False (config inválida o claves irrecuperables) todo es config_invalid.
        if not self.ready:
            return _fail("config_invalid")

        # --- Paso 1: forma del token --------------------------------------------------
        if not isinstance(token, str):
            return _fail("bad_format")
        if len(token) > _MAX_TOKEN_LEN:
            return _fail("bad_format")
        if not _RE_JWT.match(token):
            return _fail("bad_format")

        # --- Paso 2: cabecera (antes de verificar la firma) ---------------------------
        try:
            header = jwt.get_unverified_header(token)
        except (jwt.PyJWTError, ValueError):
            return _fail("bad_format")
        if header.get("alg") != "ES256":
            return _fail("bad_format")
        kid = header.get("kid")
        if not isinstance(kid, str) or kid == "":
            return _fail("bad_format")
        key = self._keys.get(kid)
        if key is None:
            return _fail("unknown_kid")

        # --- Paso 3: firma ------------------------------------------------------------
        try:
            payload = jwt.decode(token, key, algorithms=["ES256"], options=_DECODE_OPTIONS)
        except jwt.InvalidSignatureError:
            return _fail("bad_sig")
        except (jwt.PyJWTError, ValueError):
            # Cualquier otro problema de forma/decodificación que PyJWT detecte.
            return _fail("bad_format")

        # --- Paso 4: presencia y tipos de claims --------------------------------------
        if not isinstance(payload, dict):
            return _fail("bad_format")
        exp = payload.get("exp")
        iat = payload.get("iat")
        sub = payload.get("sub")
        role = payload.get("role")
        if exp is None or iat is None or sub is None or role is None:
            return _fail("bad_format")
        if not _is_int(exp) or not _is_int(iat):
            return _fail("bad_format")
        nbf = payload.get("nbf")
        if nbf is not None and not _is_int(nbf):
            return _fail("bad_format")
        if not isinstance(sub, str) or sub == "":
            return _fail("bad_format")

        # --- Paso 5: expiración -------------------------------------------------------
        if exp <= now:
            return _fail("expired")

        # --- Paso 6: ventana de canje (token recién emitido) --------------------------
        if iat > now + _IAT_FUTURE_SKEW:
            return _fail("stale")
        if nbf is not None and nbf > now + _IAT_FUTURE_SKEW:
            return _fail("stale")
        if now - iat > _CANJE_WINDOW:
            return _fail("stale")

        # --- Paso 7: issuer -----------------------------------------------------------
        if payload.get("iss") != self._cfg.supabase_url + "/auth/v1":
            return _fail("bad_iss")

        # --- Paso 8: audience (texto exacto; una lista no vale) -----------------------
        if payload.get("aud") != "authenticated":
            return _fail("bad_aud")

        # --- Paso 9: role -------------------------------------------------------------
        if role != "authenticated":
            return _fail("bad_role")

        # --- Paso 10: sub autorizado --------------------------------------------------
        allowed: Mapping | frozenset | None = self._cfg.allowed_subs
        if allowed is None or sub not in allowed:
            return _fail("not_allowed")

        # --- Paso 11: todo validado ---------------------------------------------------
        return TokenResult(ok=True, sub=sub, reason=None)
