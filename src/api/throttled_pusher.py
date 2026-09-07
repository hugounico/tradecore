"""Throttled WebSocket pusher that batches updates at max 1 push per second."""

import json
import logging
import time
from datetime import datetime

from starlette.websockets import WebSocket, WebSocketState

from src.schemas.candle import Candle
from src.schemas.signal import Signal

logger = logging.getLogger(__name__)


class ThrottledPusher:
    """Enforces max 1 visual update per second for the single browser WebSocket.

    All updates (completed candle, signals, SMA values) are queued and batched
    into a single message sent at most once per second.
    """

    def __init__(self, throttle_interval: float = 1.0):
        self._ws: WebSocket | None = None
        self._last_push_time: float = 0.0
        self._pending_messages: list[dict] = []
        self._throttle_interval = throttle_interval

    async def set_connection(self, ws: WebSocket | None) -> None:
        """Set or clear the single browser WebSocket connection."""
        self._ws = ws

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
            "data": {
                "signal_type": signal.type.value,
                "time": int(signal.timestamp.timestamp()),
                "price": signal.price,
                "sma_fast": signal.sma_fast,
                "sma_slow": signal.sma_slow,
            },
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

    async def flush_if_ready(self) -> None:
        """Called periodically (every 100ms). If >= throttle_interval since last push
        and there are pending messages, send them all as a batch and reset."""
        if not self._pending_messages:
            return

        if self._ws is None:
            # No browser connected — discard pending messages silently
            self._pending_messages.clear()
            return

        now = time.time()
        elapsed = now - self._last_push_time

        if elapsed < self._throttle_interval:
            return

        # Send batched messages
        batch = self._pending_messages.copy()
        self._pending_messages.clear()

        try:
            await self._ws.send_text(json.dumps(batch))
            self._last_push_time = now
        except Exception:
            # Push to closed socket — clear connection, log at DEBUG level
            logger.debug("WebSocket send failed, clearing connection")
            self._ws = None
