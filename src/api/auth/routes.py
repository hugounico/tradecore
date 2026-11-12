"""Rutas de autenticación: `GET /`, `POST /auth/enter`, `POST /auth/logout` (T6a) y
`GET /login`, `GET /auth/login.js` (T6b).

`build_auth_router` devuelve un `APIRouter` (agrupador de rutas de FastAPI) oculto del esquema
OpenAPI. Todas las respuestas tienen cuerpo vacío; las redirecciones son 303 con destino fijo
(nunca derivado de la petición). Las validaciones de `/auth/enter` siguen un orden estricto y
cada rechazo deja una sola línea de log con el código, sin token, cookie ni cuerpo.

Este módulo no se conecta a la app real: el cableado es la tarea T7.
"""
from __future__ import annotations

import html
import logging
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.requests import ClientDisconnect

from src.api.auth.config import AuthConfig, load_auth_config
from src.api.auth.ratelimit import RETRY_AFTER_SECONDS, TokenBucket
from src.api.auth.session import SessionCodec, extract_session_cookie
from src.api.auth.tokens import TokenVerifier

logger = logging.getLogger(__name__)

# Tamaño máximo del cuerpo de /auth/enter (un access_token ocupa menos de 4 KB).
MAX_BODY_BYTES = 8192
LOGIN_LOCATION = "/login"
LOGIN_FAILED_LOCATION = "/login?e=1"
DASHBOARD_LOCATION = "/static/index.html"

# Página de login (T6b): archivos en src/api/auth/static/, leídos una vez al crear el router.
STATIC_DIR = Path(__file__).resolve().parent / "static"
# CSP estricta (Design §11, REQ-049), solo en /login y /auth/login.js. connect-src = URL de
# Supabase validada en T1; con config INVALID, 'none' (la página muestra "no disponible").
CSP_TEMPLATE = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "connect-src {connect}; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")

_FORM_TYPE = "application/x-www-form-urlencoded"
_FIELD_NAME = "access_token"


def _redirect(location: str, *, cookie: str | None = None, no_store: bool = False) -> Response:
    """Respuesta 303 con destino fijo, cuerpo vacío y, opcionalmente, una sola Set-Cookie."""
    headers = {"location": location}
    if cookie is not None:
        headers["set-cookie"] = cookie
    if no_store:
        headers["cache-control"] = "no-store"
    return Response(status_code=303, headers=headers)


def build_auth_router(
    config: AuthConfig | None = None,
    *,
    clock: Callable[[], float] = time.time,
    bucket: TokenBucket | None = None,
) -> APIRouter:
    """Crea el router. Con `config=None` lee el entorno una sola vez; el módulo no lo hace al importarse."""
    if config is None:
        config = load_auth_config()
    if bucket is None:
        bucket = TokenBucket()
    codec = SessionCodec(config)
    verifier = TokenVerifier(config)
    router = APIRouter(include_in_schema=False)

    # --- Página de login: se arma una vez (sin plantillas ni datos de la petición) ---
    ok = config.status == "OK"
    supabase_url = config.supabase_url if ok else ""
    publishable_key = config.publishable_key if ok else ""
    csp = CSP_TEMPLATE.format(connect=supabase_url or "'none'")
    template = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
    login_js = (STATIC_DIR / "login.js").read_bytes()
    filled = (template
              .replace("__TC_SUPABASE_URL__", html.escape(supabase_url, quote=True))
              .replace("__TC_PUBLISHABLE_KEY__", html.escape(publishable_key, quote=True)))
    page_login = filled.replace("__TC_VIEW__", "login").encode("utf-8")
    page_session = filled.replace("__TC_VIEW__", "session").encode("utf-8")
    page_headers = {"content-security-policy": csp, "cache-control": "no-store"}

    # --- Utilidades locales (cierran sobre config/codec) --------------------------
    def origin_is_valid(request: Request) -> bool:
        """Origin válido = config OK y exactamente una cabecera Origin permitida."""
        if config.status != "OK":
            return False
        origins = request.headers.getlist("origin")
        allowed = config.allowed_origins or frozenset()
        return len(origins) == 1 and origins[0] in allowed

    def session_is_valid(request: Request) -> bool:
        """Sesión válida = config OK y cookie de sesión verificada con el reloj inyectado."""
        if config.status != "OK":
            return False
        cookies = request.headers.getlist("cookie")
        header = "; ".join(cookies) if cookies else None
        return codec.verify(extract_session_cookie(header), int(clock())).ok

    def enter_reject(reason: str, response: Response) -> Response:
        """Registra el código de rechazo (una línea) y devuelve la respuesta dada."""
        logger.info("AUTH_ENTER_REJECT reason=%s", reason)
        return response

    # --- GET / ------------------------------------------------------------------
    @router.get("/")
    async def root(request: Request) -> Response:
        # La redirección depende de la sesión, por eso nunca se cachea.
        target = DASHBOARD_LOCATION if session_is_valid(request) else LOGIN_LOCATION
        return _redirect(target, no_store=True)

    # --- GET /login y GET /auth/login.js -------------------------------------
    @router.get("/login")
    async def login_page(request: Request) -> Response:
        body = page_session if session_is_valid(request) else page_login
        # Sin sesión: formulario; con sesión válida: "Sesión activa" (Ir al dashboard / Cerrar sesión).
        return Response(content=body, media_type="text/html; charset=utf-8", headers=page_headers)

    @router.get("/auth/login.js")
    async def login_script() -> Response:
        return Response(content=login_js, media_type="text/javascript; charset=utf-8",
                        headers=page_headers)

    # --- POST /auth/enter -------------------------------------------------------
    @router.post("/auth/enter")
    async def enter(request: Request) -> Response:
        # 1. Config inválida: no consume ficha ni lee el cuerpo.
        if config.status != "OK":
            return enter_reject("config_invalid", _redirect(LOGIN_FAILED_LOCATION))

        # 2. Límite de tasa global.
        if not bucket.try_acquire():
            return enter_reject(
                "rate_limited",
                Response(status_code=429, headers={"retry-after": str(RETRY_AFTER_SECONDS)}),
            )

        # 3. Origin permitido.
        if not origin_is_valid(request):
            return enter_reject("bad_origin", Response(status_code=403))

        # 4. Content-Type: exactamente uno y de tipo formulario.
        content_types = request.headers.getlist("content-type")
        if len(content_types) != 1 or (
            content_types[0].split(";", 1)[0].strip().lower() != _FORM_TYPE
        ):
            return enter_reject("bad_body", Response(status_code=400))

        # 5. Content-Length declarado (si existe): una sola cabecera, solo dígitos ASCII.
        lengths = request.headers.getlist("content-length")
        if len(lengths) > 1:
            return enter_reject("bad_body", Response(status_code=400))
        if lengths:
            declared = lengths[0]
            if not (declared.isascii() and declared.isdigit()):
                return enter_reject("bad_body", Response(status_code=400))
            # Más de 10 dígitos ya excede el tope; evita convertir cadenas enormes a int.
            if len(declared) > 10 or int(declared) > MAX_BODY_BYTES:
                return enter_reject("bad_body", Response(status_code=413))

        # 6. Lectura por tramos con tope acumulado (nunca body()/form()/json()).
        parts: list[bytes] = []
        total = 0
        try:
            async for part in request.stream():
                if total + len(part) > MAX_BODY_BYTES:
                    return enter_reject("bad_body", Response(status_code=413))
                parts.append(part)
                total += len(part)
        except ClientDisconnect:
            return enter_reject("bad_body", Response(status_code=400))

        # 7. Formulario estricto: ASCII, un único campo `access_token`.
        try:
            text = b"".join(parts).decode("ascii")
            pairs = parse_qsl(
                text,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=2,
            )
        except ValueError:
            return enter_reject("bad_body", Response(status_code=400))
        if len(pairs) != 1 or pairs[0][0] != _FIELD_NAME:
            return enter_reject("bad_body", Response(status_code=400))

        # 8. Verificación del token de Supabase.
        now = int(clock())
        result = verifier.verify(pairs[0][1], now)
        if not result.ok:
            return enter_reject(result.reason, _redirect(LOGIN_FAILED_LOCATION))

        # 9. Canje correcto: cookie de sesión propia y redirección al panel.
        logger.info("AUTH_ENTER_OK sub=%s", result.sub)
        cookie = codec.set_cookie_header(codec.issue(result.sub, now))
        return _redirect(DASHBOARD_LOCATION, cookie=cookie)

    # --- POST /auth/logout ------------------------------------------------------
    @router.post("/auth/logout")
    async def logout(request: Request) -> Response:
        if not origin_is_valid(request):
            logger.info("AUTH_LOGOUT_REJECT reason=bad_origin")
            return Response(status_code=403)
        return _redirect(LOGIN_LOCATION, cookie=codec.clear_cookie_header())

    return router
