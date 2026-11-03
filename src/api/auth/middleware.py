"""Middleware AuthGate (ASGI) — control de acceso deny-by-default + cabeceras de seguridad.

ASGI = interfaz asíncrona entre el servidor y la app. Este middleware envuelve la app:
- Rutas públicas (lista exacta): pasan siempre (incluso con config INVALID) y reciben las
  cabeceras de seguridad; algunas además `cache-control: no-store`.
- Rutas protegidas: solo pasan si la config está OK y la cookie de sesión es válida; si no,
  responde 303 a /login (si el cliente acepta HTML) o 401, sin llamar a la app.
- WebSocket: handshake con 4401 (sin sesión) o 4403 (Origin no permitido); si pasa, la
  conexión vive hasta el `exp` de la cookie y el gate la cierra con 4401 real al vencer
  (opción A' del Design §9.3): un único `send_lock`, sin cancelar el `receive` del endpoint.

No lee cabeceras de reenvío ni la ruta cruda; no captura excepciones genéricas; no importa la
app concreta. Solo registra un `debug` si el cierre 4401 no se pudo transmitir.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from src.api.auth.config import AuthConfig, load_auth_config
from src.api.auth.session import SessionCodec, extract_session_cookie

logger = logging.getLogger(__name__)


class ClosedByGate(Exception):
    """Se lanza dentro del `send`/`receive` envueltos cuando el gate ya cerró el WebSocket.

    Hereda de `Exception` a propósito: el endpoint real (y el pusher) capturan `Exception`
    en su envío, así que esta señal se integra con su manejo de errores y termina el bucle.
    """

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
_ORIGIN = b"origin"

# Códigos de cierre WebSocket (rango privado 4000-4999 de la especificación WS).
WS_CLOSE_UNAUTHORIZED = 4401      # sin sesión válida (o config INVALID, o cookie vencida)
WS_CLOSE_FORBIDDEN_ORIGIN = 4403  # Origin ausente o no permitido


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

    # --- WebSocket (T5, opción A') ----------------------------------------------
    async def _handle_websocket(self, scope, receive, send) -> None:
        # Orden del Design: config INVALID (4401) → Origin ausente/ajeno (4403) → cookie (4401).
        if self._config.status != "OK":
            await self._ws_reject(WS_CLOSE_UNAUTHORIZED, receive, send)
            return
        origins = [v for (name, v) in scope["headers"] if name.lower() == _ORIGIN]
        allowed = self._config.allowed_origins or frozenset()
        if len(origins) != 1 or origins[0].decode("latin-1") not in allowed:
            await self._ws_reject(WS_CLOSE_FORBIDDEN_ORIGIN, receive, send)
            return
        # Una sola verificación: decide el rechazo y, si pasa, fija el plazo (exp de la cookie).
        result = self._codec.verify(self._collect_cookie(scope), int(self._clock()))
        if not result.ok:
            await self._ws_reject(WS_CLOSE_UNAUTHORIZED, receive, send)
            return
        await self._app(scope, *self._ws_wrap(send, receive, result.exp))

    async def _ws_reject(self, code: int, receive, send) -> None:
        """Rechazo de handshake: una lectura; si es connect → accept + close con `code`."""
        message = await receive()
        if message["type"] == "websocket.connect":
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": code})
        # Cualquier otro primer mensaje: no se envía nada y no se llama al app.

    def _ws_wrap(self, send, receive, deadline):
        """Crea los `wreceive`/`wsend` envueltos con estado local a esta conexión."""
        state = {"close_sent": False, "recv_task": None}
        send_lock = asyncio.Lock()

        async def _close_locked() -> None:
            # Debe llamarse con send_lock tomado.
            if not state["close_sent"]:
                try:
                    await send({"type": "websocket.close", "code": WS_CLOSE_UNAUTHORIZED})
                except (OSError, RuntimeError):
                    # El socket ya está muerto: no se pudo transmitir el cierre. Sin datos.
                    logger.debug("WS: cierre 4401 no transmitido")
                state["close_sent"] = True

        async def wsend(message) -> None:
            async with send_lock:
                if state["close_sent"]:
                    raise ClosedByGate()
                # `websocket.accept` no es dato: pasa aunque se haya alcanzado el plazo, para
                # que el cliente reciba el accept del endpoint (fuera de su try) y luego un
                # 4401 real.
                if message["type"] != "websocket.accept" and self._clock() >= deadline:
                    await _close_locked()
                    raise ClosedByGate()
                await send(message)
                if message["type"] == "websocket.close":
                    state["close_sent"] = True

        async def wreceive():
            if state["recv_task"] is None:
                state["recv_task"] = asyncio.create_task(receive())
            try:
                if not state["close_sent"]:
                    remaining = deadline - self._clock()
                    if remaining > 0:
                        # `wait` (no `wait_for`): al expirar NO cancela la tarea de recepción.
                        done, _ = await asyncio.wait({state["recv_task"]}, timeout=remaining)
                        if state["recv_task"] in done:
                            t, state["recv_task"] = state["recv_task"], None
                            return t.result()
                    async with send_lock:
                        await _close_locked()
                t, state["recv_task"] = state["recv_task"], None
                return await t
            except asyncio.CancelledError:
                if state["recv_task"] is not None:
                    state["recv_task"].cancel()
                raise

        return wreceive, wsend
