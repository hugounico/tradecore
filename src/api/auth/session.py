"""Cookie de sesión firmada (HMAC-SHA256), sin renovación (§8).

Este módulo NO registra logs, NO lee el reloj (quien llama pasa `now`) y NO guarda estado
mutable: todo se deriva del `AuthConfig`. El valor de la cookie nunca aparece en lo que
devuelve `verify` ni en el `repr` de `SessionResult`.

Formato del valor de cookie:  "v1." + seg1 + "." + firma
- seg1  = base64url sin relleno del JSON canónico {"ep","exp","iat","sub"} (sort_keys).
- firma = base64url sin relleno de HMAC-SHA256(cfg.session_key, "v1."+seg1) (43 chars).
  La firma cubre también el prefijo "v1." (previene confusión de versión).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from src.api.auth.config import AuthConfig

COOKIE_NAME = "__Host-tc_session"
SESSION_REASONS = ("config_invalid", "missing", "bad_format", "bad_sig",
                   "expired", "bad_epoch", "not_allowed")

# Prefijo de versión del formato de cookie.
_PREFIX = "v1."
# Longitud máxima del valor de cookie aceptado/emitido.
_MAX_LEN = 512
# Forma del valor: v1.<seg1>.<firma de 43 chars base64url>. \A..\Z para no tolerar \n.
_RE_VALUE = re.compile(r"\Av1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}\Z")
# Claves exactas que debe tener el payload.
_PAYLOAD_KEYS = frozenset({"ep", "exp", "iat", "sub"})


@dataclass(frozen=True)
class SessionResult:
    ok: bool
    sub: str | None      # solo si ok
    exp: int | None      # solo si ok (lo usará el WebSocket como plazo)
    reason: str | None   # solo si no ok; uno de SESSION_REASONS


def _b64u_encode(raw: bytes) -> str:
    """bytes -> base64url sin relleno."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    """base64url sin relleno -> bytes. Agrega el padding que falte."""
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _is_int(value: object) -> bool:
    """Entero verdadero: `int` que NO es `bool`."""
    return isinstance(value, int) and not isinstance(value, bool)


def _fail(reason: str) -> SessionResult:
    return SessionResult(ok=False, sub=None, exp=None, reason=reason)


class SessionCodec:
    """Emite y verifica la cookie de sesión a partir de un `AuthConfig`. Sin estado mutable."""

    def __init__(self, cfg: AuthConfig) -> None:
        self._cfg = cfg
        self.ready: bool = cfg.status == "OK"

    def _sign(self, seg1: str) -> str:
        """Firma base64url de HMAC-SHA256 sobre 'v1.'+seg1 (cubre el prefijo)."""
        mac = hmac.new(self._cfg.session_key, (_PREFIX + seg1).encode("ascii"),
                       hashlib.sha256).digest()
        return _b64u_encode(mac)

    def issue(self, sub: str, now: int) -> str:
        """Devuelve el valor de la cookie. Lanza ValueError ante uso incorrecto del llamador."""
        if not self.ready:
            raise ValueError("SessionCodec no está listo (config INVALID)")
        if type(sub) is not str:
            raise ValueError("sub debe ser str")
        if self._cfg.allowed_subs is None or sub not in self._cfg.allowed_subs:
            raise ValueError("sub no autorizado")
        if type(now) is not int:  # un bool no vale (type() distingue bool de int)
            raise ValueError("now debe ser int")

        payload = {
            "ep": self._cfg.session_epoch,
            "exp": now + self._cfg.session_ttl_seconds,
            "iat": now,
            "sub": sub,
        }
        # JSON canónico (orden fijo) => determinista: mismas entradas, mismo valor.
        seg1 = _b64u_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        return f"{_PREFIX}{seg1}.{self._sign(seg1)}"

    def verify(self, value: object, now: int) -> SessionResult:
        """Verifica el valor de cookie. Nunca lanza; el primer fallo decide el reason."""
        # 1. config
        if not self.ready:
            return _fail("config_invalid")
        # 2. ausencia
        if value is None or value == "":
            return _fail("missing")
        # 3. forma
        if not isinstance(value, str):
            return _fail("bad_format")
        if len(value) > _MAX_LEN:
            return _fail("bad_format")
        if not _RE_VALUE.match(value):
            return _fail("bad_format")

        # 4. firma (sobre los textos base64url; el JSON NO se decodifica todavía)
        _v1, seg1, sig = value.split(".")
        expected = self._sign(seg1)
        if not hmac.compare_digest(expected, sig):
            return _fail("bad_sig")

        # 5. decodificar y validar el payload
        try:
            payload = json.loads(_b64u_decode(seg1).decode("utf-8"))
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return _fail("bad_format")
        if not isinstance(payload, dict):
            return _fail("bad_format")
        if frozenset(payload.keys()) != _PAYLOAD_KEYS:
            return _fail("bad_format")
        ep, exp, iat, sub = payload["ep"], payload["exp"], payload["iat"], payload["sub"]
        if not _is_int(ep) or not _is_int(exp) or not _is_int(iat):
            return _fail("bad_format")
        if not isinstance(sub, str) or sub == "":
            return _fail("bad_format")

        # 6. expiración
        if exp <= now:
            return _fail("expired")
        # 7. época (revocación global)
        if ep != self._cfg.session_epoch:
            return _fail("bad_epoch")
        # 8. sub autorizado (baja individual)
        if self._cfg.allowed_subs is None or sub not in self._cfg.allowed_subs:
            return _fail("not_allowed")

        # 9. OK
        return SessionResult(ok=True, sub=sub, exp=exp, reason=None)

    def set_cookie_header(self, value: str) -> str:
        """Cabecera Set-Cookie con atributos exactos. Lanza ValueError si el valor es inseguro."""
        if type(value) is not str or len(value) > _MAX_LEN or not _RE_VALUE.match(value):
            # Impide inyectar ';', espacios o saltos de línea en la cabecera.
            raise ValueError("valor de cookie inválido para la cabecera")
        return (f"{COOKIE_NAME}={value}; Max-Age={self._cfg.session_ttl_seconds}; "
                "Path=/; Secure; HttpOnly; SameSite=Lax")

    def clear_cookie_header(self) -> str:
        """Cabecera Set-Cookie que borra la cookie (logout)."""
        return f"{COOKIE_NAME}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"


def extract_session_cookie(cookie_header: str | None) -> str | None:
    """Extrae el valor de __Host-tc_session del encabezado Cookie.

    Devuelve el valor si el nombre aparece exactamente una vez; None si el encabezado es
    None/vacío, si no aparece o si aparece más de una vez. No usa http.cookies.
    """
    if not cookie_header:
        return None
    found: list[str] = []
    for pair in cookie_header.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, _sep, val = pair.partition("=")
        if name.strip() == COOKIE_NAME:
            found.append(val.strip())
    if len(found) != 1:
        return None
    return found[0]
