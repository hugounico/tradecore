"""Middleware AuthGate (ASGI) — control de acceso deny-by-default + cabeceras de seguridad.

ASGI = interfaz asíncrona entre el servidor y la app. Este middleware envuelve la app:
- Rutas públicas (lista exacta): pasan siempre (incluso con config INVALID) y reciben las
  cabeceras de seguridad; algunas además `cache-control: no-store`.
- Rutas protegidas: solo pasan si la config está OK y la cookie de sesión es válida; si no,
  responde 303 a /login (si el cliente acepta HTML) o 401, sin llamar a la app.
- WebSocket: provisional hasta T5 — cierra toda conexión con el código 4401.

No lee cabeceras de reenvío ni la ruta cruda; no registra logs; no captura excepciones
genéricas; no importa la app concreta. La parte WebSocket definitiva (plazos, broadcast) es T5.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from src.api.auth.config import AuthConfig, load_auth_config
from src.api.auth.session import SessionCodec, extract_session_cookie

# Rutas públicas: coincidencia EXACTA de (método, ruta). Sin normalizar ni cambiar mayúsculas.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/health"),
    ("GET", "/"),
    ("GET", "/login"),
    ("GET", "/auth/login.js"),
    ("POST", "/auth/enter"),
    ("POST", "/auth/logout"),
})

# Cabeceras de seguridad (nombres en minúsculas, como exige HTTP/2 y espera T6).
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"strict-transport-security", b"max-age=31536000"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"DENY"),
)

_NO_STORE = (b"cache-control", b"no-store")
_COOKIE = b"cookie"
_ACCEPT = b"accept"
_WS_CLOSE_CODE = 4401


def _merge_headers(app_headers: list[tuple[bytes, bytes]],
                   overrides: tuple[tuple[bytes, bytes], ...]) -> list[tuple[bytes, bytes]]:
    """Devuelve una lista NUEVA: elimina del app toda cabecera cuyo nombre esté en overrides
    (comparación en minúsculas) y agrega una sola de cada override. El resto queda intacto,
    incluidos varios `set-cookie`."""
    override_names = {name.lower() for name, _ in overrides}
    result = [(n, v) for (n, v) in app_headers if n.lower() not in override_names]
    result.extend(overrides)
    return result


class AuthGate:
    """Envoltura ASGI. Una instancia por app; crea un SessionCodec propio."""

    def __init__(
        self,
        app,
        *,
        config: AuthConfig | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if config is None:
            config = load_auth_config()
        self._app = app
        self._config = config
        self._codec = SessionCodec(config)
        self._clock = clock

    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self._app(scope, receive, send)
            return
        if kind == "http":
            await self._handle_http(scope, receive, send)
            return
        if kind == "websocket":
            await self._handle_websocket(scope, receive, send)
            return
        # Cualquier otro tipo de scope: no se toca.
        return

    # --- HTTP -------------------------------------------------------------------
    async def _handle_http(self, scope, receive, send) -> None:
        method = scope["method"]
        path = scope["path"]

        if (method, path) in PUBLIC_ROUTES:
            # Público: siempre pasa (incluso INVALID). no-store salvo en /health y /.
            if path == "/login" or path.startswith("/auth/"):
                overrides = SECURITY_HEADERS + (_NO_STORE,)
            else:
                overrides = SECURITY_HEADERS
            await self._app(scope, receive, self._wrap_send(send, overrides))
            return

        # Protegido: deny-by-default.
        ok = False
        if self._config.status == "OK":
            cookie_value = self._collect_cookie(scope)
            result = self._codec.verify(cookie_value, int(self._clock()))
            ok = result.ok

        if ok:
            overrides = SECURITY_HEADERS + (_NO_STORE,)
            await self._app(scope, receive, self._wrap_send(send, overrides))
            return

        # No autorizado: no se llama a la app ni a receive.
        await self._deny(scope, send)

    def _collect_cookie(self, scope) -> str | None:
        """Une los valores de todas las cabeceras `cookie` con '; ' en su orden; None si no hay."""
        values = [v.decode("latin-1") for (name, v) in scope["headers"] if name.lower() == _COOKIE]
        if not values:
            return None
        return extract_session_cookie("; ".join(values))

    async def _deny(self, scope, send) -> None:
        """303 a /login si el cliente acepta HTML; si no, 401. Cuerpo vacío."""
        wants_html = any(
            b"text/html" in v.lower() for (name, v) in scope["headers"] if name.lower() == _ACCEPT
        )
        headers = list(SECURITY_HEADERS) + [_NO_STORE, (b"content-length", b"0")]
        if wants_html:
            status = 303
            headers.append((b"location", b"/login"))
        else:
            status = 401
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    def _wrap_send(self, send, overrides: tuple[tuple[bytes, bytes], ...]):
        """Devuelve un send que inyecta `overrides` en http.response.start sin mutar el original."""
        async def wrapped(message):
            if message["type"] == "http.response.start":
                new_message = dict(message)
                new_message["headers"] = _merge_headers(list(message.get("headers", [])), overrides)
                await send(new_message)
            else:
                await send(message)
        return wrapped

    # --- WebSocket (provisional T4: deny total con 4401) ------------------------
    async def _handle_websocket(self, scope, receive, send) -> None:
        message = await receive()
        if message["type"] == "websocket.connect":
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": _WS_CLOSE_CODE})
        # Cualquier otro primer mensaje: no se envía nada.
        return
