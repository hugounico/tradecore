"""Configuración aislada de control de acceso (login) — estados OK / INVALID (fail-closed).

Lee 8 variables de entorno como texto crudo (Pydantic no valida aquí: sus errores incluyen
`input_value=<valor>` y filtrarían la clave de sesión). La validación la hace
`validate_auth_settings`, una función pura que nunca lanza, no registra logs ni incluye valores
en lo que devuelve. `load_auth_config` orquesta la lectura y emite un único log ERROR si el
resultado es INVALID. Si la configuración es INVALID, no se entrega ningún valor parcial.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Nombres exactos de las 8 variables.
_SUPABASE_URL = "TC_AUTH_SUPABASE_URL"
_PUBLISHABLE_KEY = "TC_AUTH_PUBLISHABLE_KEY"
_JWKS_JSON = "TC_AUTH_JWKS_JSON"
_ALLOWED_SUBS = "TC_AUTH_ALLOWED_SUBS"
_ALLOWED_ORIGINS = "TC_AUTH_ALLOWED_ORIGINS"
_SESSION_KEY = "TC_SESSION_KEY"
_SESSION_EPOCH = "TC_SESSION_EPOCH"
_SESSION_TTL = "TC_SESSION_TTL_SECONDS"

# Las 7 obligatorias (TTL tiene default y no se marca inválida por ausencia).
_MANDATORY = (
    _SUPABASE_URL, _PUBLISHABLE_KEY, _JWKS_JSON, _ALLOWED_SUBS,
    _ALLOWED_ORIGINS, _SESSION_KEY, _SESSION_EPOCH,
)

# Expresiones regulares de validación. Se usa \A...\Z (no ^...$) para que un salto de línea
# al inicio o final NO sea tolerado ($ en Python admite un \n final; \Z no).
_RE_URL = re.compile(r"\Ahttps://[a-z0-9-]+(\.[a-z0-9-]+)+\Z")
_RE_PUBKEY = re.compile(r"\Asb_publishable_[A-Za-z0-9_-]+\Z")
_RE_UUID = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_RE_ORIGIN = re.compile(r"\Ahttps://[a-z0-9-]+(\.[a-z0-9-]+)+(:[0-9]{1,5})?\Z")
_RE_SESSION_KEY = re.compile(r"\A[A-Za-z0-9_-]{43,}\Z")
# Entero positivo acotado a 10 dígitos como máximo: evita que int() de cadenas enormes
# (miles de dígitos) lance ValueError por el límite de conversión de Python.
_RE_POS_INT = re.compile(r"\A[1-9][0-9]{0,9}\Z")
_RE_B64URL_32 = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")

# Tamaño máximo del JWKS en caracteres. Límite propio de TradeCore para evitar entradas
# patológicas (p. ej. miles de corchetes anidados que harían RecursionError en json.loads).
# Un JWKS con 2 claves ES256 ocupa ~600 caracteres.
_JWKS_MAX_CHARS = 16384

_DEFAULT_TTL = 28800


class AuthSettings(BaseSettings):
    """Lee las 8 variables como texto crudo. Sin validación de Pydantic (ver módulo)."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    supabase_url: str | None = Field(default=None, validation_alias=_SUPABASE_URL)
    publishable_key: str | None = Field(default=None, validation_alias=_PUBLISHABLE_KEY)
    jwks_json: str | None = Field(default=None, validation_alias=_JWKS_JSON)
    allowed_subs: str | None = Field(default=None, validation_alias=_ALLOWED_SUBS)
    allowed_origins: str | None = Field(default=None, validation_alias=_ALLOWED_ORIGINS)
    session_key: str | None = Field(default=None, validation_alias=_SESSION_KEY, repr=False)
    session_epoch: str | None = Field(default=None, validation_alias=_SESSION_EPOCH)
    session_ttl_seconds: str | None = Field(default=None, validation_alias=_SESSION_TTL)


@dataclass(frozen=True)
class AuthConfig:
    status: Literal["OK", "INVALID"]
    invalid_fields: tuple[str, ...]
    supabase_url: str | None = None
    publishable_key: str | None = None
    jwks_keys: Mapping[str, Mapping[str, str]] | None = None
    allowed_subs: frozenset[str] | None = None
    allowed_origins: frozenset[str] | None = None
    session_key: bytes | None = field(default=None, repr=False)
    session_epoch: int | None = None
    session_ttl_seconds: int | None = None


def _is_present(v: str | None) -> bool:
    """Presente = no None y no cadena vacía. Vacío cuenta como ausente."""
    return v is not None and v != ""


def _b64url_len32(s: str) -> bool:
    """43 chars base64url que decodifican a exactamente 32 bytes."""
    if not _RE_B64URL_32.match(s):
        return False
    try:
        raw = base64.urlsafe_b64decode(s + "=")  # padding para 43 chars
    except (binascii.Error, ValueError):
        return False
    return len(raw) == 32


def _parse_jwks(text: str) -> Mapping[str, Mapping[str, str]] | None:
    """Devuelve {kid: jwk} con SOLO claves ES256 válidas, o None si el JWKS es inválido.

    Reglas: objeto JSON con `keys` lista no vacía; ninguna clave puede contener `d` (clave
    privada); una clave ES256 cumple kty=EC, crv=P-256, alg=ES256, kid texto no vacío y único,
    x/y de 43 chars base64url que decodifican a 32 bytes, use ausente o 'sig', key_ops ausente
    o lista que contiene 'verify'. Las claves no ES256 se ignoran; 0 ES256 => None.
    """
    if len(text) > _JWKS_MAX_CHARS:
        # Entrada demasiado grande: se rechaza antes de parsear (evita trabajo y recursión
        # patológica en json.loads sobre miles de estructuras anidadas).
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        # ValueError/TypeError: JSON mal formado. RecursionError: anidamiento excesivo.
        return None
    if not isinstance(data, dict):
        return None
    keys = data.get("keys")
    if not isinstance(keys, list) or len(keys) == 0:
        return None

    result: dict[str, Mapping[str, str]] = {}
    for jwk in keys:
        if not isinstance(jwk, dict):
            return None
        if "d" in jwk:  # ninguna clave puede traer material privado
            return None
        # ¿Es ES256? Si no cumple la terna EC/P-256/ES256, se ignora.
        if not (jwk.get("kty") == "EC" and jwk.get("crv") == "P-256" and jwk.get("alg") == "ES256"):
            continue
        kid = jwk.get("kid")
        if not isinstance(kid, str) or kid == "":
            return None
        if kid in result:  # kid duplicado entre ES256
            return None
        x, y = jwk.get("x"), jwk.get("y")
        if not isinstance(x, str) or not isinstance(y, str):
            return None
        if not _b64url_len32(x) or not _b64url_len32(y):
            return None
        use = jwk.get("use")
        if use is not None and use != "sig":
            return None
        key_ops = jwk.get("key_ops")
        if key_ops is not None:
            if not isinstance(key_ops, list) or "verify" not in key_ops:
                return None
        result[kid] = MappingProxyType(dict(jwk))

    if not result:  # 0 claves ES256
        return None
    return MappingProxyType(result)


def validate_auth_settings(raw: Mapping[str, str | None]) -> AuthConfig:
    """Función pura: valida las 8 variables. Nunca lanza, no loguea, no filtra valores."""
    invalid: list[str] = []

    # Valores parseados (solo se conservan si todo es OK).
    supabase_url = raw.get(_SUPABASE_URL)
    publishable_key = raw.get(_PUBLISHABLE_KEY)
    jwks_raw = raw.get(_JWKS_JSON)
    subs_raw = raw.get(_ALLOWED_SUBS)
    origins_raw = raw.get(_ALLOWED_ORIGINS)
    session_key = raw.get(_SESSION_KEY)
    epoch_raw = raw.get(_SESSION_EPOCH)
    ttl_raw = raw.get(_SESSION_TTL)

    # TC_AUTH_SUPABASE_URL
    if not (_is_present(supabase_url) and _RE_URL.match(supabase_url)):
        invalid.append(_SUPABASE_URL)

    # TC_AUTH_PUBLISHABLE_KEY
    if not (_is_present(publishable_key) and _RE_PUBKEY.match(publishable_key)):
        invalid.append(_PUBLISHABLE_KEY)

    # TC_AUTH_JWKS_JSON
    jwks_keys = _parse_jwks(jwks_raw) if _is_present(jwks_raw) else None
    if jwks_keys is None:
        invalid.append(_JWKS_JSON)

    # TC_AUTH_ALLOWED_SUBS. Se recortan SOLO espacios alrededor de cada elemento (strip(" ")):
    # un salto de línea o tabulador dentro de un elemento NO se recorta y lo vuelve inválido.
    subs_set: frozenset[str] | None = None
    if _is_present(subs_raw):
        parts = [p.strip(" ") for p in subs_raw.split(",")]
        if parts and all(_RE_UUID.match(p) for p in parts):
            subs_set = frozenset(parts)
    if subs_set is None:
        invalid.append(_ALLOWED_SUBS)

    # TC_AUTH_ALLOWED_ORIGINS. Igual criterio: strip(" ") recorta solo espacios, no \n/\t.
    origins_set: frozenset[str] | None = None
    if _is_present(origins_raw):
        parts = [p.strip(" ") for p in origins_raw.split(",")]
        if parts and all(_RE_ORIGIN.match(p) for p in parts):
            origins_set = frozenset(parts)
    if origins_set is None:
        invalid.append(_ALLOWED_ORIGINS)

    # TC_SESSION_KEY (se usa tal cual en UTF-8; no se decodifica)
    if not (_is_present(session_key) and _RE_SESSION_KEY.match(session_key)):
        invalid.append(_SESSION_KEY)

    # TC_SESSION_EPOCH
    epoch_val: int | None = None
    if _is_present(epoch_raw) and _RE_POS_INT.match(epoch_raw):
        epoch_val = int(epoch_raw)
    if epoch_val is None:
        invalid.append(_SESSION_EPOCH)

    # TC_SESSION_TTL_SECONDS (ausente => default 28800; presente => entero positivo de hasta 10
    # dígitos Y además <= 28800, porque el Design fija la sesión en 8 h como máximo, §8/R2).
    ttl_val: int | None
    if not _is_present(ttl_raw):
        ttl_val = _DEFAULT_TTL
    elif _RE_POS_INT.match(ttl_raw) and int(ttl_raw) <= _DEFAULT_TTL:
        ttl_val = int(ttl_raw)
    else:
        ttl_val = None
        invalid.append(_SESSION_TTL)

    if invalid:
        return AuthConfig(status="INVALID", invalid_fields=tuple(sorted(invalid)))

    return AuthConfig(
        status="OK",
        invalid_fields=(),
        supabase_url=supabase_url,
        publishable_key=publishable_key,
        jwks_keys=jwks_keys,
        allowed_subs=subs_set,
        allowed_origins=origins_set,
        session_key=session_key.encode("utf-8"),
        session_epoch=epoch_val,
        session_ttl_seconds=ttl_val,
    )


def load_auth_config() -> AuthConfig:
    """Lee AuthSettings() del entorno, valida y, si es INVALID, emite un único log ERROR."""
    s = AuthSettings()
    raw = {
        _SUPABASE_URL: s.supabase_url,
        _PUBLISHABLE_KEY: s.publishable_key,
        _JWKS_JSON: s.jwks_json,
        _ALLOWED_SUBS: s.allowed_subs,
        _ALLOWED_ORIGINS: s.allowed_origins,
        _SESSION_KEY: s.session_key,
        _SESSION_EPOCH: s.session_epoch,
        _SESSION_TTL: s.session_ttl_seconds,
    }
    cfg = validate_auth_settings(raw)
    if cfg.status == "INVALID":
        logger.error("AUTH_CONFIG_INVALID campos=[%s]", ",".join(cfg.invalid_fields))
    return cfg
