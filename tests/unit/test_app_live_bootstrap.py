"""LIVE_REPLAY_BOOTSTRAP_SLICE — tests del cableado en app.py (offline, sin red).

Verifican que el processing loop suprime senales mientras el replay esta en curso y que la
frontera REPLAY→LIVE la dicta EXCLUSIVAMENTE REPLAY_COMPLETED (no un conteo de velas).

Se prueba `_processing_loop` con una fuente y un pusher mockeados y un predicado de supresion,
sin abrir Databento ni FastAPI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import importlib

import pytest

# NOTE: `import src.api.app as m` binds `m` to the FastAPI `app` object, because
# src/api/__init__.py does `from src.api.app import app`. Use importlib to get the MODULE.
app_module = importlib.import_module("src.api.app")
from src.engine.signal_engine import SignalEngine
from src.pipeline.candle_buffer import CandleBuffer
from src.schemas.candle import Candle


def _candle(minute: int, close: float) -> Candle:
    ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)
    return Candle(timestamp=ts, open=close, high=close + 1, low=close - 1, close=close, volume=100 + minute)


class _FakeSource:
    """CandleSource-like: replay() yields candles; is_replay_complete flips when told."""

    def __init__(self, candles, complete_after_index):
        self._candles = candles
        self._complete_after = complete_after_index
        self.is_replay_complete = False
        self._i = 0

    async def replay(self):
        for idx, c in enumerate(self._candles):
            # Boundary authority: flip AFTER emitting index == complete_after
            if self._complete_after is not None and idx > self._complete_after:
                self.is_replay_complete = True
            yield c
        # If boundary never reached via index, leave as-is.

    def stop(self):
        pass


class _RecordingPusher:
    def __init__(self):
        self.candles = []
        self.signals = []

    async def queue_candle(self, candle):
        self.candles.append(candle)

    async def queue_signal(self, signal):
        self.signals.append(signal)

    async def queue_sma_update(self, *a, **k):
        pass


def _crossover_candles():
    """Build a close sequence that triggers at least one SMA9/21 crossover after warmup.

    Rising then falling to force fast to cross slow both ways once enough data exists.
    """
    closes = [100.0] * 21           # flat warmup (>=21 so SMA defined)
    closes += [100.0 + i for i in range(1, 15)]   # strong rise -> BUY crossover
    closes += [114.0 - 2 * i for i in range(1, 15)]  # strong fall -> SELL crossover
    return [_candle(i, c) for i, c in enumerate(closes)]


@pytest.fixture(autouse=True)
def _reset_module_state():
    # Snapshot and restore module globals to avoid cross-test leakage.
    saved = (
        app_module._settings, app_module._buffer, app_module._pusher,
        app_module._engine, app_module._replay,
    )
    yield
    (app_module._settings, app_module._buffer, app_module._pusher,
     app_module._engine, app_module._replay) = saved


def _install(source, pusher):
    # Minimal settings object with the periods the loop reads.
    class _S:
        sma_fast_period = 9
        sma_slow_period = 21
    app_module._settings = _S()
    app_module._buffer = CandleBuffer()
    app_module._engine = SignalEngine(fast_period=9, slow_period=21)
    app_module._pusher = pusher
    app_module._replay = source


# --- Scenario E (core guarantee): signals suppressed for ALL replay candles regardless of count ---

async def test_replay_signals_suppressed_until_boundary_regardless_of_count():
    candles = _crossover_candles()  # ~49 candles, crossovers happen mid-replay
    # boundary only AFTER all candles => everything is replay => zero signals to pusher
    source = _FakeSource(candles, complete_after_index=len(candles))  # never completes mid-stream
    pusher = _RecordingPusher()
    _install(source, pusher)

    await app_module._processing_loop(
        suppress_signals=lambda: not source.is_replay_complete
    )

    # All candles were replay (boundary never reached during stream) -> no signals emitted,
    # even though a crossover mathematically occurred.
    assert pusher.signals == []
    assert len(pusher.candles) == len(candles)
    # Engine state was still advanced (warm): prev values are set.
    assert app_module._engine._prev_fast is not None
    assert app_module._engine._prev_slow is not None


# --- Scenario A: replay then live; post-boundary crossover DOES reach the pusher ---

async def test_signal_after_boundary_reaches_pusher():
    candles = _crossover_candles()
    # Complete replay early (after warmup, before the crossovers) so later crossovers are LIVE.
    source = _FakeSource(candles, complete_after_index=21)
    pusher = _RecordingPusher()
    _install(source, pusher)

    await app_module._processing_loop(
        suppress_signals=lambda: not source.is_replay_complete
    )

    # At least one signal should have been emitted in the LIVE phase.
    assert len(pusher.signals) >= 1


# --- Scenario B: insufficient replay (<22) then live continues feeding engine ---

async def test_insufficient_replay_then_live_emits_when_ready():
    warm = [_candle(i, 100.0) for i in range(5)]  # only 5 replay candles (<22)
    live = _crossover_candles()
    # shift live timestamps after warm
    live = [_candle(5 + i, c.close) for i, c in enumerate(live)]
    all_candles = warm + live
    source = _FakeSource(all_candles, complete_after_index=4)  # boundary after the 5 replay candles
    pusher = _RecordingPusher()
    _install(source, pusher)

    await app_module._processing_loop(
        suppress_signals=lambda: not source.is_replay_complete
    )

    # Replay (5 candles) emitted no signal; live phase eventually yields >=1 signal.
    assert len(pusher.signals) >= 1


# --- Scenario C: empty replay then live starts from scratch, no premature signal ---

async def test_empty_replay_then_live_no_premature_signal():
    live = _crossover_candles()
    source = _FakeSource(live, complete_after_index=-1)  # complete before any candle => all live
    pusher = _RecordingPusher()
    _install(source, pusher)

    await app_module._processing_loop(
        suppress_signals=lambda: not source.is_replay_complete
    )
    # All are live; a crossover occurs -> at least one signal. No crash on empty warmup.
    assert len(pusher.signals) >= 1


# --- simulation behavior: default (no suppression) emits signals as before ---

async def test_default_no_suppression_emits_signals():
    candles = _crossover_candles()
    source = _FakeSource(candles, complete_after_index=len(candles))
    pusher = _RecordingPusher()
    _install(source, pusher)

    # No suppress_signals arg => simulation/default behavior => signals flow.
    await app_module._processing_loop()

    assert len(pusher.signals) >= 1
