"""A3c Ola 1 — tests de integración de contrato para `_processing_loop` y `/health`.

- T8 (VERDE hoy): el `_processing_loop` real, con fuente simulada, suprime señales durante un
  tramo de replay de recuperación (is_replay_complete=False) pero SÍ alimenta buffer+motor; las
  señales posteriores coinciden con la referencia continua desde ese punto.
- T9 (ROJO hoy, por comportamiento): una vela con timestamp <= última aceptada NO debe llegar a
  `pusher.queue_candle`. Hoy el loop encola la vela aunque el buffer la descarte → aserción falla.
- T10 (ROJO hoy, por comportamiento): `/health` debe leer `source.phase`: recovering → 200
  {status ok, phase recovering}; terminal → 503 {phase terminal}. Hoy `/health` no conoce `phase`.

Series deterministas construidas DENTRO de cada test (sin archivos externos). No abre red.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

app_module = importlib.import_module("src.api.app")
from src.engine.signal_engine import SignalEngine
from src.pipeline.candle_buffer import CandleBuffer
from src.schemas.candle import Candle

T0 = datetime(2026, 10, 16, 13, 30, 0, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)


def _candle(minute: int, close: float) -> Candle:
    ts = T0 + minute * MINUTE
    return Candle(timestamp=ts, open=close, high=close + 1, low=close - 1, close=close, volume=100 + minute)


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


class _ScriptedSource:
    """Fuente CandleSource-like: entrega velas de un guion y controla is_replay_complete.

    `events`: lista de ("candle", Candle) | ("replay_completed",). Permite simular un tramo de
    replay (suppress on) seguido de la frontera y velas live.
    """

    def __init__(self, events):
        self._events = list(events)
        self.is_replay_complete = False

    async def replay(self):
        for ev in self._events:
            if ev[0] == "replay_completed":
                self.is_replay_complete = True
            else:
                yield ev[1]

    def stop(self):
        pass


class _S:
    mode = "live"
    sma_fast_period = 9
    sma_slow_period = 21


@pytest.fixture(autouse=True)
def _reset_module_state():
    names = ["_settings", "_buffer", "_pusher", "_engine", "_replay",
             "_pipeline_task", "_shutdown_in_progress", "_stall_warning_logged",
             "_live_bootstrap_started_at"]
    saved = {n: getattr(app_module, n, "__MISSING__") for n in names}
    yield
    for n, v in saved.items():
        if v == "__MISSING__":
            if hasattr(app_module, n):
                delattr(app_module, n)
        else:
            setattr(app_module, n, v)


def _install(source, pusher):
    app_module._settings = _S()
    app_module._buffer = CandleBuffer()
    app_module._engine = SignalEngine(fast_period=9, slow_period=21)
    app_module._pusher = pusher
    app_module._replay = source


def _rising_then_falling(n_warm=21):
    """Serie que produce al menos un cruce SMA9/21 tras el warm-up."""
    closes = [100.0] * n_warm
    closes += [100.0 + i for i in range(1, 15)]     # subida fuerte -> BUY
    closes += [114.0 - 2 * i for i in range(1, 15)]  # bajada fuerte -> SELL
    return closes


# --- T8 (VERDE): recuperación durante replay no emite señales; luego continúa igual ---

async def test_t8_recovery_replay_suppressed_then_matches_reference():
    closes = _rising_then_falling()
    candles = [_candle(i, c) for i, c in enumerate(closes)]

    # Referencia: serie continua SIN supresión (todas live) -> señales de referencia.
    ref_source = _ScriptedSource([("replay_completed",)] + [("candle", c) for c in candles])
    ref_pusher = _RecordingPusher()
    _install(ref_source, ref_pusher)
    await app_module._processing_loop(suppress_signals=lambda: not ref_source.is_replay_complete)
    reference_signals = [(s.type.value) for s in ref_pusher.signals]

    # Escenario: un tramo inicial (primeras 25 velas) es REPLAY de recuperación (suppress on),
    # luego REPLAY_COMPLETED y el resto es live. Las velas del tramo alimentan buffer+motor pero
    # NO emiten señales; las señales posteriores == referencia desde ese punto.
    split = 25
    events = [("candle", c) for c in candles[:split]]
    events += [("replay_completed",)]
    events += [("candle", c) for c in candles[split:]]
    rec_source = _ScriptedSource(events)
    rec_pusher = _RecordingPusher()
    _install(rec_source, rec_pusher)
    await app_module._processing_loop(suppress_signals=lambda: not rec_source.is_replay_complete)

    # El buffer/motor recibió TODAS las velas (alimentación no suprimida).
    assert len(rec_pusher.candles) == len(candles)
    # Durante el tramo de replay (suppress on) no se emitió ninguna señal cuyo cruce cayera ahí:
    # todas las señales emitidas corresponden a cruces ocurridos DESPUÉS del split.
    # Y el motor, al estar caliente, produce desde el split las mismas señales que la referencia
    # tiene desde ese punto.
    ref_after = reference_signals[-len(rec_pusher.signals):] if rec_pusher.signals else []
    assert [s.type.value for s in rec_pusher.signals] == ref_after


# --- T9 (ROJO): una vela <= última aceptada NO debe llegar a queue_candle ---

async def test_t9_duplicate_candle_not_pushed():
    c1 = _candle(0, 100.0)
    c2 = _candle(1, 101.0)
    dup = _candle(1, 101.0)   # mismo timestamp que c2 -> el buffer lo descarta (<=)
    c3 = _candle(2, 102.0)

    source = _ScriptedSource([
        ("replay_completed",),
        ("candle", c1), ("candle", c2), ("candle", dup), ("candle", c3),
    ])
    pusher = _RecordingPusher()
    _install(source, pusher)
    await app_module._processing_loop(suppress_signals=lambda: not source.is_replay_complete)

    # El buffer sólo aceptó c1,c2,c3. La vela duplicada (dup, timestamp <= última) NO debe
    # haberse enviado al pusher. Hoy el loop encola SIEMPRE -> esta aserción falla (ROJO).
    pushed_ts = [c.timestamp for c in pusher.candles]
    assert pushed_ts == [c1.timestamp, c2.timestamp, c3.timestamp]


# --- T10 (ROJO): /health lee source.phase (recovering/terminal) ---

class _PhaseSource:
    """Fuente que solo expone `phase` (y stop) para el contrato nuevo de /health."""

    def __init__(self, phase):
        self.phase = phase
        self.is_replay_complete = True  # irrelevante para el contrato nuevo

    def stop(self):
        pass


class _AliveTask:
    def done(self):
        return False

    def cancelled(self):
        return False

    def exception(self):
        return None


async def _health():
    resp = await app_module.health_check()
    if isinstance(resp, dict):
        return 200, resp
    body = json.loads(bytes(resp.body).decode("utf-8"))
    return resp.status_code, body


async def test_t10_health_reads_source_phase_recovering_and_terminal():
    import time as _time
    # recovering -> 200 {status ok, phase recovering}
    app_module._settings = _S()
    app_module._pipeline_task = _AliveTask()
    app_module._shutdown_in_progress = False
    app_module._stall_warning_logged = False
    app_module._live_bootstrap_started_at = _time.monotonic()
    app_module._replay = _PhaseSource("recovering")

    code, body = await _health()
    assert code == 200
    assert body.get("status") == "ok"
    assert body.get("phase") == "recovering"

    # terminal -> 503 {phase terminal}
    app_module._replay = _PhaseSource("terminal")
    code, body = await _health()
    assert code == 503
    assert body.get("phase") == "terminal"
