"""A5.1a: la hora de cada señal debe ser la hora de la vela que la generó (no el reloj del servidor).

- T1: _processing_loop con el SignalEngine y el CandleBuffer reales: la señal emitida y la guardada en
      _signal_history llevan timestamp == timestamp de su vela. Lado y precio no cambian.
- T2: un navegador que conecta justo después de la señal la recibe en initial_load (antes quedaba
      fuera del filtro [primera vela, última vela] porque su hora era la del reloj, posterior a la última vela).
Ambos FALLAN con el código previo (timestamp = datetime.now) y PASAN con la corrección en app.py.
signal_engine.py NO se modifica.
"""

import json
import sys
import time
from collections import deque
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from src.api.app import app
from src.schemas.candle import Candle
from src.schemas.signal import SignalType


def _candle(minute: int, close: float) -> Candle:
    ts = datetime(2024, 6, 1, 10, minute, 0, tzinfo=timezone.utc)
    return Candle(timestamp=ts, open=close, high=close + 1.0, low=close - 1.0, close=close, volume=100)


def _velas_con_un_cruce():
    """21 cierres planos (SMA9 == SMA21) y luego uno más alto: BUY exactamente en la vela 10:21."""
    return [_candle(m, 100.0) for m in range(21)] + [_candle(21, 101.0)]


class _FakeReplay:
    def __init__(self, candles):
        self._candles = candles

    async def replay(self):
        for c in self._candles:
            yield c


class _FakeSettings:
    sma_fast_period = 9
    sma_slow_period = 21
    mode = "simulation"


async def test_t1_signal_timestamp_equals_its_candle(monkeypatch):
    app_module = sys.modules["src.api.app"]
    from src.engine.signal_engine import SignalEngine
    from src.pipeline.candle_buffer import CandleBuffer

    velas = _velas_con_un_cruce()
    pusher = AsyncMock()
    hist = deque()
    monkeypatch.setattr(app_module, "_replay", _FakeReplay(velas))
    monkeypatch.setattr(app_module, "_buffer", CandleBuffer())
    monkeypatch.setattr(app_module, "_engine", SignalEngine(9, 21))
    monkeypatch.setattr(app_module, "_pusher", pusher)
    monkeypatch.setattr(app_module, "_settings", _FakeSettings())
    monkeypatch.setattr(app_module, "_signal_history", hist)

    await app_module._processing_loop(suppress_signals=None)

    assert len(hist) == 1
    s = hist[0]
    assert s.type == SignalType.BUY
    assert s.price == 101.0
    assert s.timestamp == velas[21].timestamp
    assert pusher.queue_signal.await_count == 1
    assert pusher.queue_signal.await_args.args[0].timestamp == velas[21].timestamp


def test_t2_reconnecting_client_receives_latest_signal(monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key-dummy")
    monkeypatch.setenv("MODE", "simulation")
    app_module = sys.modules["src.api.app"]
    from src.api.throttled_pusher import signal_payload

    velas = _velas_con_un_cruce()
    with patch("src.api.app.DabentoConnector.load_historical", new_callable=AsyncMock, return_value=velas):
        with TestClient(app) as tc:
            deadline = time.monotonic() + 5.0
            while not app_module._signal_history and time.monotonic() < deadline:
                time.sleep(0.02)
            assert len(app_module._signal_history) == 1, "el pipeline no emitió la señal esperada"
            emitted = app_module._signal_history[0]
            with tc.websocket_connect("/ws/chart") as ws:
                msg = json.loads(ws.receive_text())

    assert msg["type"] == "initial_load"
    assert msg["data"]["candles"][-1]["time"] == int(velas[21].timestamp.timestamp())
    assert msg["data"]["signals"] == [signal_payload(emitted)]
    assert msg["data"]["signals"][0]["time"] == int(velas[21].timestamp.timestamp())
