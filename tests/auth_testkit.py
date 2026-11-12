"""Configuración de acceso sintética para las pruebas que usan la app real (Paso 2, T7).

`tests/conftest.py` carga `AUTH_TEST_ENV` en el entorno del proceso de pruebas antes de que se
importe `src.api.app`, de modo que la app real arranca con una config de acceso válida (OK). Las
pruebas que abren el dashboard o el WebSocket de la app real se presentan como un usuario con
sesión mediante `session_headers()`. Todos los valores son inventados: nunca claves ni UUID reales.

Este módulo no importa nada de `src` a nivel de módulo: `conftest.py` lo importa ANTES que la app
(importar `src.api.*` carga `src/api/app.py`, que lee la config de acceso al importarse).
"""
from __future__ import annotations

import json
import time

TEST_ORIGIN = "https://tradecore.test"
TEST_SUB = "11111111-2222-3333-4444-555555555555"

_TEST_JWK = {
    "alg": "ES256", "crv": "P-256", "kty": "EC", "use": "sig", "key_ops": ["verify"],
    "kid": "k1",
    "x": "JPdlg8l77lcPf4wxSMnACUPQ616wVqX2Uu9Q6ZJGLqU",
    "y": "AkjFprNzyDruo5hL-u0_gZ15ELaRcmKb1rGj-ckl8oE",
}

AUTH_TEST_ENV = {
    "TC_AUTH_SUPABASE_URL": "https://tradecore-test.supabase.co",
    "TC_AUTH_PUBLISHABLE_KEY": "sb_publishable_TradeCoreTestOnly",
    "TC_AUTH_JWKS_JSON": json.dumps({"keys": [_TEST_JWK]}),
    "TC_AUTH_ALLOWED_SUBS": TEST_SUB,
    "TC_AUTH_ALLOWED_ORIGINS": TEST_ORIGIN,
    "TC_SESSION_KEY": "t" * 64,
    "TC_SESSION_EPOCH": "1",
    "TC_SESSION_TTL_SECONDS": "28800",
}


def session_headers() -> dict[str, str]:
    """Cabeceras de un navegador con sesión válida: cookie de sesión vigente + Origin permitido."""
    from src.api.auth.config import validate_auth_settings
    from src.api.auth.session import COOKIE_NAME, SessionCodec

    cfg = validate_auth_settings(AUTH_TEST_ENV)
    value = SessionCodec(cfg).issue(TEST_SUB, int(time.time()))
    return {"cookie": f"{COOKIE_NAME}={value}", "origin": TEST_ORIGIN}
