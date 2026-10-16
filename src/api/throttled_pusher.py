"""Throttled WebSocket pusher: batches updates at max 1 push per second and
broadcasts them to EVERY connected browser client.

A3a change (resiliencia del dashboard en vivo):
- Antes habia UNA sola conexion (`self._ws`): un segundo navegador desplazaba al
  primero y un socket colgado congelaba el push para todos. Ahora se mantiene un
  CONJUNTO de conexiones y cada batch se envia a todas (broadcast).
- Cada envio individual tiene timeout (`SEND_TIMEOUT_SECONDS`): un cliente lento o
  colgado se descarta sin bloquear a los demas.
- Se agrega un latido (heartbeat) fuera del throttle para que el navegador detecte
  cortes de conexion silenciosos.

Terminologia:
- WebSocket = canal bidireccional persistente backend<->navegador.
- broadcast = enviar el mismo mensaje a todos los clientes conectados.
- throttle = limitar la frecuencia de envios (aqui, maximo 1 batch por segundo).
- heartbeat = mensaje periodico "sigo vivo" que permite al cliente notar un corte.
"""

import asyncio
import json
import logging
import time
from datetime import datetime

from starlette.websockets import WebSocket

from src.schemas.candle import Candle
from src.schemas.signal import Signal

logger = logging.getLogger(__name__)

# Timeout (segundos) por ENVIO individual a un cliente. Si un `send_text` no termina
# dentro de este plazo, se considera el cliente muerto y se descarta, sin afectar al
# resto. 5s es amplio para una red normal pero acota el peor caso de un socket colgado.
SEND_TIMEOUT_SECONDS = 5.0


def signal_payload(signal: Signal) -> dict:
    """Return the per-signal `data` dict used in WebSocket signal messages.

    Formato unico de senal reutilizado por `queue_signal` (updates en vivo) y por el
    estado inicial (`initial_load`) para que el navegador dibuje los marcadores de la
    sesion con exactamente la misma forma en ambos caminos.
    """
    return {
        "signal_type": signal.type.value,
        "time": int(signal.timestamp.timestamp()),
        "price": signal.price,
        "sma_fast": signal.sma_fast,
        "sma_slow": signal.sma_slow,
    }


class ThrottledPusher:
    """Enforces max 1 visual update per second and broadcasts to all browser clients.

    All updates (completed candle, signals, SMA values) are queued and batched into a
    single message sent at most once per second to EVERY connected client.
    """

    def __init__(self, throttle_interval: float = 1.0, send_timeout: float = SEND_TIMEOUT_SECONDS):
        # Conexiones activas: cada WebSocket mapea a su propio Lock, para serializar los
        # envios a ESE socket (Starlette no permite dos send_text concurrentes al mismo ws).
        self._connections: dict[WebSocket, asyncio.Lock] = {}
        self._last_push_time: float = 0.0
        self._pending_messages: list[dict] = []
        self._throttle_interval = throttle_interval
        self._send_timeout = send_timeout

    # --- Gestion de conexiones (sincrona: solo toca estructuras en memoria) -------

    def add_connection(self, ws: WebSocket) -> None:
        """Register a browser WebSocket connection."""
        if ws not in self._connections:
            self._connections[ws] = asyncio.Lock()

    def remove_connection(self, ws: WebSocket) -> None:
        """Unregister a browser WebSocket connection (idempotente)."""
        self._connections.pop(ws, None)

    @property
    def connection_count(self) -> int:
        """Number of currently registered connections."""
        return len(self._connections)

    async def set_connection(self, ws: WebSocket | None) -> None:
        """Deprecated: no usar en app.py. Se conserva solo por compatibilidad con tests
        existentes que configuraban una unica conexion.

        - ws is None  -> elimina TODAS las conexiones.
        - ws provided -> deja SOLO esa conexion (elimina las demas y agrega esta).
        """
        self._connections.clear()
        if ws is not None:
            self._connections[ws] = asyncio.Lock()

    # --- Encolado de mensajes (formato IDENTICO al previo) ------------------------

    async def queue_candle(self, candle: Candle) -> None:
        """Queue a completed candle for next push."""
        self._pending_messages.append({
            "type": "candle",
            "data": {
                "time": int(candle.timestamp.timestamp()),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            },
        })

    async def queue_signal(self, signal: Signal) -> None:
        """Queue a BUY/SELL signal for next push."""
        self._pending_messages.append({
            "type": "signal",
            "data": signal_payload(signal),
        })

    async def queue_sma_update(self, time_val: int, sma_fast: float, sma_slow: float) -> None:
        """Queue SMA line data point for next push."""
        self._pending_messages.append({
            "type": "sma",
            "data": {
                "time": time_val,
                "sma_fast": sma_fast,
                "sma_slow": sma_slow,
            },
        })

    async def queue_status(
        self, connected: bool, last_time: datetime | None, mode: str = "simulation"
    ) -> None:
        """Queue connection status update."""
        self._pending_messages.append({
            "type": "status",
            "data": {
                "connected": connected,
                "last_candle_time": last_time.isoformat() + "Z" if last_time else None,
                "mode": mode,
            },
        })

    # --- Envio individual con timeout --------------------------------------------

    async def _send(self, ws: WebSocket, text: str) -> bool:
        """Send `text` to one client with a per-send timeout.

        Returns True on success, False on ANY failure (timeout, closed socket,
        error). El caller decide eliminar las conexiones que devolvieron False.
        """
        lock = self._connections.get(ws)
        if lock is None:
            # La conexion ya fue eliminada entre el snapshot y el envio.
            return False
        try:
            async with lock:  # serializa envios a ESTE socket
                await asyncio.wait_for(ws.send_text(text), self._send_timeout)
            return True
        except Exception:
            # Timeout, socket cerrado o cualquier error: cliente considerado muerto.
            logger.debug("WebSocket send failed/timed out; marking client for removal")
            return False

    async def send_direct(self, ws: WebSocket, text: str) -> bool:
        """Public: send a single message to one client immediately (sin throttle).

        Usado por el endpoint para el `initial_load`. Devuelve True/False igual que
        `_send`. NO elimina la conexion por si misma; el caller decide.
        """
        return await self._send(ws, text)

    # --- Flush periodico (broadcast) ---------------------------------------------

    async def flush_if_ready(self) -> None:
        """Called periodically (every 100ms). If >= throttle_interval since last push
        and there are pending messages, broadcast them to all clients and reset."""
        if not self._pending_messages:
            return

        if not self._connections:
            # Nadie conectado: descarta los mensajes pendientes en silencio.
            self._pending_messages.clear()
            return

        now = time.time()
        if (now - self._last_push_time) < self._throttle_interval:
            return

        # Toma el batch y resetea ANTES de enviar (los envios pueden tardar).
        batch = self._pending_messages.copy()
        self._pending_messages.clear()
        self._last_push_time = now

        text = json.dumps(batch)
        await self._broadcast(text)

    async def send_heartbeat(self, market_open: bool) -> None:
        """Broadcast a heartbeat to all clients immediately (NO throttle, NO queue).

        El navegador usa este latido para detectar cortes silenciosos y para saber si
        el mercado esta en horario regular. `server_time` es epoch en segundos (UTC).
        """
        if not self._connections:
            return
        text = json.dumps({
            "type": "heartbeat",
            "data": {
                "server_time": int(time.time()),
                "market_open": market_open,
            },
        })
        await self._broadcast(text)

    async def _broadcast(self, text: str) -> None:
        """Send `text` to every connected client in parallel, then prune the dead ones."""
        # Snapshot: la lista de destinos se fija antes de enviar (puede cambiar durante).
        targets = list(self._connections.keys())
        results = await asyncio.gather(*(self._send(ws, text) for ws in targets))
        # Elimina SOLO las conexiones cuyo envio fallo (resultado False).
        for ws, ok in zip(targets, results):
            if not ok:
                self.remove_connection(ws)
