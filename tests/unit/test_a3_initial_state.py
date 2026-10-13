"""A3a reproduction tests: initial dashboard state on (re)connect.

These tests encode the A1 bug where a client that connects (or reconnects) to a
live session gets an incomplete first paint: no initial_load when the buffer is
empty, no past session signals (markers), and no market-open flag. They are
written AGAINST THE INTENDED (post-fix) behavior, so they MUST FAIL on the
current implementation and PASS only after Step 4.

Covered:
- T5: the endpoint ALWAYS sends an initial_load first, even with an empty buffer
      (candles == [] and signals == []), so the frontend can clear its loading
      overlay instead of hanging on "Cargando datos historicos".
- T6: initial_load carries the session's past signals, in the EXACT per-signal
      format of ThrottledPusher.signal_payload, ascending by time, filtered to the
      buffer's time range.
- T7: initial_load carries a boolean market_open flag.

Terminology:
- initial_load = primer mensaje que el backend envia al navegador al conectar;
  reemplaza por completo el estado del grafico.
- marker = flecha BUY/SELL dibujada sobre una vela.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from src.api.app import app, _build_initial_load_message
from src.schemas.candle import Candle
from src.schemas.signal import Signal, SignalType


def _candle(minute: int, close: float = 100.0) -> Candle:
    ts = datetime(2024, 6, 1, 10, minute, 0, tzinfo=timezone.utc)
    return Candle(timestamp=ts, open=close - 0.5, high=close + 1.0,
                  low=close - 1.0, close=close, volume=100 + minute)


def _signal(minute: int, stype: SignalType = SignalType.BUY, price: float = 100.0) -> Signal:
    ts = datetime(2024, 6, 1, 10, minute, 0, tzinfo=timezone.utc)
    return Signal(type=stype, timestamp=ts, price=price, sma_fast=99.0, sma_slow=98.0)


# ---------------------------------------------------------------------------
# T5: empty buffer still yields an initial_load (candles == [], signals == [])
# ---------------------------------------------------------------------------

class TestInitialLoadAlwaysSent:
    """T5: the WebSocket endpoint sends initial_load even with no candles."""

    def test_t5_empty_buffer_still_sends_initial_load(self, monkeypatch):
        """With an empty historical load, the first WS message is still initial_load."""
        monkeypatch.setenv("DATABENTO_API_KEY", "test-key-dummy")

        # No candles at all -> buffer stays empty for the whole lifespan.
        with patch(
            "src.api.app.DabentoConnector.load_historical",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with TestClient(app) as tc:
                with tc.websocket_connect("/ws/chart") as ws:
                    msg = json.loads(ws.receive_text())

        assert msg["type"] == "initial_load"
        assert msg["data"]["candles"] == []
        # Session signals field is present and empty on an empty session.
        assert msg["data"]["signals"] == []


# ---------------------------------------------------------------------------
# T6: initial_load carries past session signals in exact signal_payload format
# ---------------------------------------------------------------------------

class TestInitialLoadSignals:
    """T6: initial_load includes session signals, ascending, correct format."""

    def test_t6_signals_included_in_signal_payload_format(self):
        """Signals in initial_load match signal_payload(signal) exactly, ascending."""
        candles = [_candle(m, 100.0 + m) for m in range(5)]  # 10:00 .. 10:04
        # Two signals within the buffer range, provided OUT of order on purpose.
        s_late = _signal(4, SignalType.SELL, price=104.0)
        s_early = _signal(1, SignalType.BUY, price=101.0)

        msg = _build_initial_load_message(
            candles,
            sma_fast_period=9,
            sma_slow_period=21,
            signals=[s_late, s_early],
            market_open=True,
        )

        # Imported lazily: signal_payload only exists after the Step 3 fix. Doing
        # this inside the test keeps COLLECTION working so this test FAILS cleanly
        # (not as a module import error) on the current, unfixed code.
        from src.api.throttled_pusher import signal_payload

        got = msg["data"]["signals"]
        # Ascending by time.
        times = [s["time"] for s in got]
        assert times == sorted(times)
        # Exact per-signal format equals signal_payload(signal).
        assert got == [signal_payload(s_early), signal_payload(s_late)]
        # Sanity: the format carries signal_type + time (the fields the frontend needs).
        assert got[0]["signal_type"] == "BUY"
        assert got[0]["time"] == int(s_early.timestamp.timestamp())


# ---------------------------------------------------------------------------
# T7: initial_load carries a boolean market_open flag
# ---------------------------------------------------------------------------

class TestInitialLoadMarketOpen:
    """T7: initial_load includes a boolean market_open flag."""

    def test_t7_market_open_flag_present(self):
        """market_open is included in initial_load data as a bool."""
        candles = [_candle(m) for m in range(3)]

        msg_open = _build_initial_load_message(
            candles, sma_fast_period=9, sma_slow_period=21,
            signals=[], market_open=True,
        )
        msg_closed = _build_initial_load_message(
            candles, sma_fast_period=9, sma_slow_period=21,
            signals=[], market_open=False,
        )

        assert msg_open["data"]["market_open"] is True
        assert msg_closed["data"]["market_open"] is False
