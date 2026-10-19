"""A3c Ola 1.1 — tests de integración de contrato para `_processing_loop` y `/health`.

- T8 (VERDE hoy, reescrito): el `_processing_loop` real, con fuente simulada. Un hueco de 5 velas
  se recupera mientras is_replay_complete=False; esas velas alimentan buffer+motor pero NO emiten
  señales. Las señales de la recuperación == señales de la referencia (toda la serie en vivo)
  EXCLUYENDO solo las del hueco (igualdad de listas completas). La referencia debe tener ≥1 señal
  antes, ≥1 DENTRO y ≥1 después del hueco (si no, el test reporta la distribución real).
- T9 (ROJO hoy): una vela con timestamp <= última aceptada NO debe llegar a pusher.queue_candle.
- T10 (ROJO hoy): /health debe leer source.phase: recovering → 200 {ok, recovering};
  terminal → 503 {terminal}. Hoy /health no conoce `phase`.
- T11 (VERDE hoy, nuevo): fuente SIN atributo `phase`, is_replay_complete=True, tarea viva →
  /health 200 {ok, live}.

Series deterministas construidas DENTRO de cada test (sin archivos externos). No abre red.
"""

from __future__ import annotations

import importlib
import json
import math
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
    """Registra cada señal como (índice_vela, timestamp_vela, tipo) y cada vela empujada."""

    def __init__(self):
        self.candles = []
        self.signals = []  # (candle_index, candle_ts, signal_type)
        self._idx = -1

    async def queue_candle(self, candle):
        self._idx += 1
        self._last_candle = candle
        self.candles.append(candle)

    async def queue_signal(self, signal):
        # El índice/timestamp corresponden a la última vela empujada (misma iteración del loop).
        self.signals.append((self._idx, self._last_candle.timestamp, signal.type.value))

    async def queue_sma_update(self, *a, **k):
        pass


class _ScriptedSource:
    """Fuente CandleSource-like: entrega velas de un guion y controla is_replay_complete.

    `events`: lista de ("candle", Candle) | ("replay_completed",).
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


def _oscillating_closes(n=140):
    """Serie oscilante determinista: varios cruces SMA9/21 repartidos en la serie.

    Oscilación senoidal de periodo ~24 velas sobre 140 velas (tras warm-up de 21) produce
    múltiples cruces, de modo que haya señales antes, dentro y después de un hueco intermedio.
    """
    closes = []
    for i in range(n):
        closes.append(100.0 + 8.0 * math.sin(i / 24.0 * 2 * math.pi))
    return closes


# --- T8 (VERDE): recuperación de hueco suprime señales; resto == referencia sin el hueco ---

async def test_t8_recovery_gap_suppressed_signals_equal_reference_minus_gap():
    closes = _oscillating_closes()
    candles = [_candle(i, c) for i, c in enumerate(closes)]

    # Referencia: TODA la serie en vivo (replay_completed primero) -> señales de referencia.
    ref_source = _ScriptedSource([("replay_completed",)] + [("candle", c) for c in candles])
    ref_pusher = _RecordingPusher()
    _install(ref_source, ref_pusher)
    await app_module._processing_loop(suppress_signals=lambda: not ref_source.is_replay_complete)
    reference = list(ref_pusher.signals)  # [(idx, ts, type), ...]

    # Elegir gap_start determinista: la primera señal de referencia con idx >= 30 marca el centro;
    # colocamos el hueco de 5 velas alrededor de esa señal para garantizar ≥1 señal DENTRO.
    ref_idxs = [s[0] for s in reference]
    assert ref_idxs, f"la referencia no produjo señales; distribución real: {reference}"
    center = next((i for i in ref_idxs if i >= 30), ref_idxs[len(ref_idxs) // 2])
    gap_start = max(22, center - 2)   # 5 velas: [gap_start, gap_start+5)
    gap_end = gap_start + 5
    gap_idxs = set(range(gap_start, gap_end))

    before = [s for s in reference if s[0] < gap_start]
    inside = [s for s in reference if gap_start <= s[0] < gap_end]
    after = [s for s in reference if s[0] >= gap_end]
    assert before and inside and after, (
        "la referencia debe tener señales antes, dentro y después del hueco; "
        f"antes={len(before)} dentro={len(inside)} despues={len(after)}; "
        f"gap=[{gap_start},{gap_end}); señales={reference}"
    )

    # Recuperación: velas [0,gap_start) en vivo; luego is_replay_complete=False para el hueco
    # [gap_start,gap_end); replay_completed; resto en vivo.
    events = [("replay_completed",)]                                   # arranque: live
    events += [("candle", c) for c in candles[:gap_start]]             # live antes del hueco
    # entrar en recuperación: la fuente deja de estar "completa" para el tramo del hueco
    events += [("__uncomplete__",)]
    events += [("candle", c) for c in candles[gap_start:gap_end]]      # hueco (suppress on)
    events += [("replay_completed",)]                                  # recuperación completa
    events += [("candle", c) for c in candles[gap_end:]]              # live después del hueco

    class _GapSource(_ScriptedSource):
        async def replay(self):
            for ev in self._events:
                if ev[0] == "replay_completed":
                    self.is_replay_complete = True
                elif ev[0] == "__uncomplete__":
                    self.is_replay_complete = False
                else:
                    yield ev[1]

    rec_source = _GapSource(events)
    rec_pusher = _RecordingPusher()
    _install(rec_source, rec_pusher)
    await app_module._processing_loop(suppress_signals=lambda: not rec_source.is_replay_complete)

    # Todas las velas alimentaron buffer+motor.
    assert len(rec_pusher.candles) == len(candles)
    # Igualdad de listas completas: recuperación == referencia excluyendo SOLO las señales del hueco.
    expected = [s for s in reference if s[0] not in gap_idxs]
    assert rec_pusher.signals == expected


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

    pushed_ts = [c.timestamp for c in pusher.candles]
    assert pushed_ts == [c1.timestamp, c2.timestamp, c3.timestamp]


# --- T10 (ROJO): /health lee source.phase (recovering/terminal) ---

class _PhaseSource:
    def __init__(self, phase):
        self.phase = phase
        self.is_replay_complete = True

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


def _install_health(source):
    import time as _time
    app_module._settings = _S()
    app_module._pipeline_task = _AliveTask()
    app_module._shutdown_in_progress = False
    app_module._stall_warning_logged = False
    app_module._live_bootstrap_started_at = _time.monotonic()
    app_module._replay = source


async def test_t10_health_reads_source_phase_recovering_and_terminal():
    _install_health(_PhaseSource("recovering"))
    code, body = await _health()
    assert code == 200
    assert body.get("status") == "ok"
    assert body.get("phase") == "recovering"

    app_module._replay = _PhaseSource("terminal")
    code, body = await _health()
    assert code == 503
    assert body.get("phase") == "terminal"


# --- T11 (VERDE): fuente sin `phase`, replay completo, tarea viva -> 200 live ---

class _NoPhaseSource:
    """Fuente que NO tiene atributo `phase` (como la fuente actual). is_replay_complete=True."""

    def __init__(self):
        self.is_replay_complete = True

    def stop(self):
        pass


async def test_t11_health_source_without_phase_is_live():
    _install_health(_NoPhaseSource())
    code, body = await _health()
    assert code == 200
    assert body.get("status") == "ok"
    assert body.get("phase") == "live"
