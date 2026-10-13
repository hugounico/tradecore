"""A3a reproduction tests: multi-client broadcast + per-send resilience.

These tests encode the A1 live-dashboard bugs that A3a must fix. They are written
AGAINST THE INTENDED (post-fix) API of ThrottledPusher, so they MUST FAIL on the
current single-connection implementation and PASS only after Step 3.

Covered:
- T1: a batch flush reaches every connected client (not just one).
- T2: a client that disconnects (removed) stops receiving; the other keeps receiving.
- T3: a client whose send raises is dropped; the other still receives the batch.
- T4: a client whose send hangs is dropped via per-send timeout; the other receives.

Terminology:
- WebSocket = canal bidireccional persistente backend<->navegador.
- AsyncMock = mock cuyos metodos son corrutinas (se pueden "await").
- broadcast = enviar el mismo mensaje a todos los clientes conectados.
"""

import asyncio
import json

import pytest
from unittest.mock import AsyncMock

from src.api.throttled_pusher import ThrottledPusher
from src.schemas.candle import Candle
from datetime import datetime, timezone


def _candle(minute: int = 0, close: float = 100.0) -> Candle:
    """Build a simple candle for queueing into the pusher."""
    ts = datetime(2024, 6, 1, 10, minute, 0, tzinfo=timezone.utc)
    return Candle(timestamp=ts, open=close - 0.5, high=close + 1.0,
                  low=close - 1.0, close=close, volume=100 + minute)


def _mock_ws() -> AsyncMock:
    """Create a WebSocket-like async mock whose send_text records calls."""
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


async def _force_flush(pusher: ThrottledPusher) -> None:
    """Reset the throttle clock so the next flush_if_ready sends immediately."""
    # The throttle only sends when >= interval has elapsed since the last push.
    # Setting the last push time into the past guarantees the batch goes out now.
    pusher._last_push_time = 0.0
    await pusher.flush_if_ready()


class TestMultiClientBroadcast:
    """T1-T4: every connected client is served and dead clients are pruned."""

    async def test_t1_batch_reaches_all_clients(self):
        """T1: a queued batch is broadcast to EVERY connected client."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        a, b = _mock_ws(), _mock_ws()

        pusher.add_connection(a)
        pusher.add_connection(b)
        assert pusher.connection_count == 2

        await pusher.queue_candle(_candle())
        await _force_flush(pusher)

        # Both clients must have received exactly one batch send.
        assert a.send_text.await_count == 1
        assert b.send_text.await_count == 1
        # And it is the same JSON batch (an array with the candle message).
        payload_a = json.loads(a.send_text.await_args.args[0])
        payload_b = json.loads(b.send_text.await_args.args[0])
        assert payload_a == payload_b
        assert payload_a[0]["type"] == "candle"

    async def test_t2_disconnected_client_stops_receiving(self):
        """T2: after a client is removed, only the remaining client is served."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        a, b = _mock_ws(), _mock_ws()
        pusher.add_connection(a)
        pusher.add_connection(b)

        # Client A disconnects (endpoint calls remove_connection in its finally block).
        pusher.remove_connection(a)
        assert pusher.connection_count == 1

        await pusher.queue_candle(_candle(minute=1))
        await _force_flush(pusher)

        # A received nothing; B received the batch.
        assert a.send_text.await_count == 0
        assert b.send_text.await_count == 1

    async def test_t3_client_raising_on_send_is_dropped(self):
        """T3: a client whose send raises is pruned; the other still gets the batch."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        a, b = _mock_ws(), _mock_ws()
        a.send_text = AsyncMock(side_effect=RuntimeError("socket closed"))
        pusher.add_connection(a)
        pusher.add_connection(b)

        await pusher.queue_candle(_candle(minute=2))
        await _force_flush(pusher)

        # B received the batch despite A failing.
        assert b.send_text.await_count == 1
        # A failed and was removed from the active set.
        assert pusher.connection_count == 1

    async def test_t4_hanging_client_dropped_via_send_timeout(self):
        """T4: a client whose send never completes is dropped via per-send timeout."""
        # Short timeout so the test stays fast (<0.2s), no real long sleeps.
        pusher = ThrottledPusher(throttle_interval=1.0, send_timeout=0.05)
        a, b = _mock_ws(), _mock_ws()

        never = asyncio.Event()  # never set -> the send hangs forever

        async def _hang(_text):
            await never.wait()

        a.send_text = AsyncMock(side_effect=_hang)
        pusher.add_connection(a)
        pusher.add_connection(b)

        await pusher.queue_candle(_candle(minute=3))
        await _force_flush(pusher)

        # B got the batch; A timed out and was removed.
        assert b.send_text.await_count == 1
        assert pusher.connection_count == 1
