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
from datetime import datetime, timedelta, timezone
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


# ---------------------------------------------------------------------------
# S1: initial_load's session signals are filtered to the buffer's time range
# ---------------------------------------------------------------------------

class TestInitialLoadSessionHistoryFiltering:
    """S1: solo las senales dentro del rango [primera, ultima] vela aparecen."""

    def test_s1_session_signals_filtered_to_buffer_range(self, monkeypatch):
        """Con 21 velas (replay vacio, sin cruces), el initial_load incluye SOLO la
        senal cuyo timestamp cae dentro del rango temporal del buffer.

        Se inyectan 3 senales en el historial del modulo DESPUES de arrancar el app
        (el arranque reinicia _signal_history): una antes de la primera vela, una
        dentro del rango, y una despues de la ultima. initial_load debe traer solo la
        de dentro, en el formato exacto de signal_payload.
        """
        monkeypatch.setenv("DATABENTO_API_KEY", "test-key-dummy")
        monkeypatch.setenv("MODE", "simulation")

        # 21 velas consecutivas de 1 min, todas close=100.0: el split en sma_slow_period
        # (21) deja el replay vacio, asi que no hay cruces ni senales emitidas por el
        # pipeline. El buffer cubre el rango [10:00, 10:20].
        velas = [_candle(m, 100.0) for m in range(21)]

        # Modulo (no la instancia FastAPI): lo tomamos de sys.modules para evitar la
        # ambiguedad del nombre `app` (que en este modulo de test ya apunta a la app).
        import sys
        app_module = sys.modules["src.api.app"]
        from src.api.throttled_pusher import signal_payload

        # Timestamps construidos a mano: 1 min antes de la primera vela (10:00) y 1 min
        # despues de la ultima (10:20). El helper _signal() solo cubre minutos 0..59.
        base = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        antes = Signal(type=SignalType.BUY, timestamp=base - timedelta(minutes=1),
                       price=100.0, sma_fast=99.0, sma_slow=98.0)   # 09:59, antes de vela[0]
        dentro = _signal(10, SignalType.SELL, price=100.0)           # 10:10, dentro del rango
        despues = Signal(type=SignalType.BUY, timestamp=base + timedelta(minutes=21),
                         price=100.0, sma_fast=99.0, sma_slow=98.0)  # 10:21, despues de vela[20]

        with patch(
            "src.api.app.DabentoConnector.load_historical",
            new_callable=AsyncMock,
            return_value=velas,
        ):
            with TestClient(app) as tc:
                # El lifespan ya reinicio _signal_history; inyectamos las 3 senales
                # DESPUES de arrancar, accediendo via el modulo (no por import directo,
                # que capturaria el deque viejo previo al arranque).
                app_module._signal_history.append(antes)
                app_module._signal_history.append(dentro)
                app_module._signal_history.append(despues)

                with tc.websocket_connect("/ws/chart") as ws:
                    msg = json.loads(ws.receive_text())

        assert msg["type"] == "initial_load"
        # Solo la senal de dentro del rango, en el formato exacto de signal_payload.
        assert msg["data"]["signals"] == [signal_payload(dentro)]


# ---------------------------------------------------------------------------
# S2: _processing_loop only records EMITTED signals (suppressed ones are dropped)
# ---------------------------------------------------------------------------

class _FakeReplay:
    """Fuente de velas falsa: su replay() async produce `candles` y termina."""

    def __init__(self, candles):
        self._candles = candles

    async def replay(self):
        for c in self._candles:
            yield c


class _FakeEngine:
    """Motor falso: evaluate() devuelve SIEMPRE una Signal (fuerza el camino de emision)."""

    def evaluate(self, closes):
        last = closes[-1] if closes else 100.0
        return Signal(
            type=SignalType.BUY,
            timestamp=datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
            price=last,
            sma_fast=last,
            sma_slow=last,
        )


class _FakeSettings:
    """Settings minimo para el loop (solo los atributos que lee)."""

    sma_fast_period = 9
    sma_slow_period = 21
    mode = "simulation"


class TestProcessingLoopSignalHistory:
    """S2: el loop guarda en _signal_history solo las senales EMITIDAS (no las suprimidas)."""

    async def _run_loop(self, monkeypatch, suppress):
        """Arma el modulo con dobles y corre _processing_loop con el predicado dado.

        Devuelve (pusher, signal_history_len) tras ejecutar el loop.
        """
        import sys
        from collections import deque

        # Modulo (no la instancia FastAPI): via sys.modules para evitar la ambiguedad
        # del nombre `app` ya ligado en este modulo de test.
        app_module = sys.modules["src.api.app"]
        from src.pipeline.candle_buffer import CandleBuffer

        # 25 velas: con sma_slow_period=21, el buffer supera el minimo y se recorre el
        # camino de SMA; cada evaluate() del motor falso devuelve una Signal.
        velas = [_candle(m, 100.0 + m) for m in range(25)]

        pusher = AsyncMock()
        hist = deque()

        monkeypatch.setattr(app_module, "_replay", _FakeReplay(velas))
        monkeypatch.setattr(app_module, "_buffer", CandleBuffer())
        monkeypatch.setattr(app_module, "_engine", _FakeEngine())
        monkeypatch.setattr(app_module, "_pusher", pusher)
        monkeypatch.setattr(app_module, "_settings", _FakeSettings())
        monkeypatch.setattr(app_module, "_signal_history", hist)

        await app_module._processing_loop(suppress_signals=suppress)
        return pusher, hist

    async def test_s2_suppressed_signals_not_recorded(self, monkeypatch):
        """Caso suprimido: queue_signal NO se llama y _signal_history queda vacio."""
        pusher, hist = await self._run_loop(monkeypatch, suppress=lambda: True)

        assert pusher.queue_signal.await_count == 0
        assert len(hist) == 0

    async def test_s2_emitted_signals_recorded_positive_control(self, monkeypatch):
        """Control positivo: sin supresion, queue_signal se llama y el historial crece."""
        pusher, hist = await self._run_loop(monkeypatch, suppress=lambda: False)

        assert pusher.queue_signal.await_count > 0
        assert len(hist) > 0
