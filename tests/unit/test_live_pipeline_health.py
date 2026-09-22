"""PRE_AWS_LIVE_HARDENING — fail-fast observable en /health y supervisor de _pipeline_task.

Verifica el contrato de salud SOLO para mode == "live" (simulation queda idéntico):
- tarea terminada => 503 "terminal"
- bootstrap estancado (> BOOTSTRAP_STALL_DEADLINE_SECONDS sin REPLAY_COMPLETED) => 503 "bootstrap_stalled"
  con exactamente UN WARNING aunque se consulte varias veces
- bootstrap dentro del plazo => 200 "bootstrapping"
- replay completo con tarea viva => 200 "live" (el plazo NO aplica tras completar)
- apagado en curso => 200 "shutting_down"
- supervisor (done_callback): excepción => 1 CRITICAL; fin normal sin excepción => 1 CRITICAL;
  cancelación => 0; apagado en curso => 0
- fin del async for de _processing_loop: en live NO se emite "Simulation replay completed."; en
  simulation SÍ.

No abre red ni Databento. Inyecta estado del módulo de forma local y aislada; NO parchea
time.monotonic globalmente (usa un _live_bootstrap_started_at en el pasado para simular tiempo).
Reutiliza el patrón de import de test_app_live_bootstrap.py sin editarlo.
"""

from __future__ import annotations

import importlib
import logging
import time

import pytest

# `import src.api.app as m` liga m al objeto FastAPI (src/api/__init__ reexporta app). Usar importlib.
app_module = importlib.import_module("src.api.app")


# --- Fakes -------------------------------------------------------------------

class _FakeTask:
    """Sustituto de asyncio.Task para /health: controla done()/cancelled()/exception()."""

    def __init__(self, done=False, cancelled=False, exc=None):
        self._done = done
        self._cancelled = cancelled
        self._exc = exc

    def done(self):
        return self._done

    def cancelled(self):
        return self._cancelled

    def exception(self):
        # asyncio.Task.exception() lanza si fue cancelada; el supervisor no debe llamarla en ese caso.
        if self._cancelled:
            raise AssertionError("exception() no debe llamarse en tarea cancelada")
        return self._exc


class _LiveSourceStub:
    """Sustituto de LiveCandleSource para /health: solo expone is_replay_complete."""

    def __init__(self, is_replay_complete=False):
        self.is_replay_complete = is_replay_complete

    def stop(self):
        pass


class _SimSettings:
    mode = "simulation"


class _LiveSettings:
    mode = "live"


# --- Fixture: aislar y restaurar el estado del módulo -----------------------

@pytest.fixture(autouse=True)
def _reset_module_state():
    names = [
        "_settings", "_pipeline_task", "_replay", "_buffer", "_pusher", "_engine",
    ]
    extra = ["_live_bootstrap_started_at", "_shutdown_in_progress", "_stall_warning_logged"]
    saved = {n: getattr(app_module, n, None) for n in names}
    saved_extra = {n: getattr(app_module, n, "__MISSING__") for n in extra}
    yield
    for n, v in saved.items():
        setattr(app_module, n, v)
    for n, v in saved_extra.items():
        if v == "__MISSING__":
            if hasattr(app_module, n):
                delattr(app_module, n)
        else:
            setattr(app_module, n, v)


def _install_live(*, task, is_replay_complete, started_offset_s, shutting_down=False):
    """Instala estado de módulo para simular mode=live en /health."""
    app_module._settings = _LiveSettings()
    app_module._pipeline_task = task
    app_module._replay = _LiveSourceStub(is_replay_complete=is_replay_complete)
    app_module._live_bootstrap_started_at = (
        None if started_offset_s is None else time.monotonic() - started_offset_s
    )
    app_module._shutdown_in_progress = shutting_down
    app_module._stall_warning_logged = False


async def _health():
    """Llama al handler real y normaliza a (status_code, phase, mode)."""
    resp = await app_module.health_check()
    # 200 => dict; 503 => JSONResponse (starlette) con .status_code y .body (bytes JSON)
    if isinstance(resp, dict):
        return 200, resp.get("phase"), resp.get("mode"), resp
    import json
    body = json.loads(bytes(resp.body).decode("utf-8"))
    return resp.status_code, body.get("phase"), body.get("mode"), body


def _stall_deadline():
    return app_module.BOOTSTRAP_STALL_DEADLINE_SECONDS


# --- T1 (D): tarea terminada tras el boundary => 503 terminal ---------------

async def test_t1_task_done_after_boundary_is_terminal():
    _install_live(task=_FakeTask(done=True), is_replay_complete=True, started_offset_s=5.0)
    code, phase, mode, _ = await _health()
    assert code == 503
    assert phase == "terminal"
    assert mode == "live"


# --- T2 (D): tarea terminada antes del boundary => 503 terminal -------------

async def test_t2_task_done_before_boundary_is_terminal():
    _install_live(task=_FakeTask(done=True), is_replay_complete=False, started_offset_s=5.0)
    code, phase, _, _ = await _health()
    assert code == 503
    assert phase == "terminal"


# --- T3 (N): replay incompleto dentro del plazo => 200 bootstrapping --------

async def test_t3_bootstrapping_within_deadline():
    _install_live(task=_FakeTask(done=False), is_replay_complete=False, started_offset_s=5.0)
    code, phase, mode, _ = await _health()
    assert code == 200
    assert phase == "bootstrapping"
    assert mode == "live"


# --- T4 (D): replay incompleto, inicio hace > deadline => 503 stalled; UN WARNING ---

async def test_t4_bootstrap_stalled_after_deadline_single_warning(caplog):
    _install_live(
        task=_FakeTask(done=False),
        is_replay_complete=False,
        started_offset_s=_stall_deadline() + 60.0,
    )
    with caplog.at_level(logging.WARNING, logger="src.api.app"):
        for _ in range(3):
            code, phase, _, _ = await _health()
            assert code == 503
            assert phase == "bootstrap_stalled"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and "stall" in r.getMessage().lower()]
    assert len(warnings) == 1, f"esperaba exactamente 1 WARNING de stall, hubo {len(warnings)}"


# --- T5 (N): tras stall, llega REPLAY_COMPLETED con tarea viva => 200 live ---

async def test_t5_late_replay_completed_recovers_to_live():
    _install_live(
        task=_FakeTask(done=False),
        is_replay_complete=False,
        started_offset_s=_stall_deadline() + 60.0,
    )
    # primero estancado
    code, phase, _, _ = await _health()
    assert code == 503 and phase == "bootstrap_stalled"
    # llega REPLAY_COMPLETED tarde, tarea sigue viva
    app_module._replay.is_replay_complete = True
    code, phase, _, _ = await _health()
    assert code == 200
    assert phase == "live"


# --- T6 (P): replay completo, tarea viva, inicio hace mucho > deadline => 200 live ---

async def test_t6_live_ignores_deadline_after_replay_complete():
    _install_live(
        task=_FakeTask(done=False),
        is_replay_complete=True,
        started_offset_s=_stall_deadline() * 10,  # mucho más que el plazo
    )
    code, phase, _, _ = await _health()
    assert code == 200
    assert phase == "live"


# --- T7 (N): apagado en curso con tarea terminada => 200 shutting_down ------

async def test_t7_shutting_down_takes_precedence_over_terminal():
    _install_live(
        task=_FakeTask(done=True),
        is_replay_complete=True,
        started_offset_s=5.0,
        shutting_down=True,
    )
    code, phase, _, _ = await _health()
    assert code == 200
    assert phase == "shutting_down"


# --- T7b (N): _pipeline_task None => 200 starting ---------------------------

async def test_t7b_task_none_is_starting():
    app_module._settings = _LiveSettings()
    app_module._pipeline_task = None
    app_module._replay = None
    app_module._live_bootstrap_started_at = None
    app_module._shutdown_in_progress = False
    app_module._stall_warning_logged = False
    code, phase, _, _ = await _health()
    assert code == 200
    assert phase == "starting"


# --- T8 (P): simulation => /health idéntico al actual, tarea viva o terminada ---

async def test_t8_simulation_health_unchanged_task_alive():
    app_module._settings = _SimSettings()
    app_module._pipeline_task = _FakeTask(done=False)
    resp = await app_module.health_check()
    assert resp == {"status": "ok"}


async def test_t8_simulation_health_unchanged_task_done():
    app_module._settings = _SimSettings()
    app_module._pipeline_task = _FakeTask(done=True)
    resp = await app_module.health_check()
    assert resp == {"status": "ok"}


async def test_t8_settings_none_health_unchanged():
    # Escenario del test de preservación existente (sin lifespan): _settings None => {"status":"ok"}.
    app_module._settings = None
    app_module._pipeline_task = None
    resp = await app_module.health_check()
    assert resp == {"status": "ok"}


# --- T9 (D): supervisor done_callback ---------------------------------------

def _run_supervisor(task):
    """Invoca el supervisor real de la tarea (nombre fijado por el contrato)."""
    return app_module._on_pipeline_task_done(task)


async def test_t9_supervisor_exception_logs_one_critical(caplog):
    app_module._shutdown_in_progress = False
    with caplog.at_level(logging.CRITICAL, logger="src.api.app"):
        _run_supervisor(_FakeTask(done=True, exc=RuntimeError("stream boom")))
    crits = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(crits) == 1
    assert "boom" in crits[0].getMessage() or "RuntimeError" in crits[0].getMessage()


async def test_t9_supervisor_normal_end_logs_one_critical(caplog):
    app_module._shutdown_in_progress = False
    with caplog.at_level(logging.CRITICAL, logger="src.api.app"):
        _run_supervisor(_FakeTask(done=True, exc=None))
    crits = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(crits) == 1


async def test_t9_supervisor_cancelled_logs_nothing(caplog):
    app_module._shutdown_in_progress = False
    with caplog.at_level(logging.CRITICAL, logger="src.api.app"):
        _run_supervisor(_FakeTask(done=True, cancelled=True))
    crits = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(crits) == 0


async def test_t9_supervisor_during_shutdown_logs_nothing(caplog):
    app_module._shutdown_in_progress = True
    with caplog.at_level(logging.CRITICAL, logger="src.api.app"):
        _run_supervisor(_FakeTask(done=True, exc=RuntimeError("ignored during shutdown")))
    crits = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(crits) == 0


# --- T10 (D): fin de loop en live no emite "Simulation replay completed." ---

def _install_loop_state(mode):
    class _S:
        sma_fast_period = 9
        sma_slow_period = 21
    _S.mode = mode
    app_module._settings = _S()
    from src.pipeline.candle_buffer import CandleBuffer
    from src.engine.signal_engine import SignalEngine
    app_module._buffer = CandleBuffer()
    app_module._engine = SignalEngine(fast_period=9, slow_period=21)

    class _EmptyPusher:
        async def queue_candle(self, c): ...
        async def queue_signal(self, s): ...
        async def queue_sma_update(self, *a, **k): ...

    class _EmptySource:
        async def replay(self):
            if False:
                yield None  # generador vacío
        def stop(self): ...

    app_module._pusher = _EmptyPusher()
    app_module._replay = _EmptySource()


async def test_t10_live_loop_end_has_no_simulation_log(caplog):
    _install_loop_state(mode="live")
    with caplog.at_level(logging.INFO, logger="src.api.app"):
        await app_module._processing_loop(suppress_signals=lambda: True)
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("Simulation replay completed" in m for m in msgs), (
        "en live NO debe emitirse 'Simulation replay completed.'"
    )


async def test_t10_simulation_loop_end_has_simulation_log(caplog):
    _install_loop_state(mode="simulation")
    with caplog.at_level(logging.INFO, logger="src.api.app"):
        await app_module._processing_loop()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("Simulation replay completed" in m for m in msgs), (
        "en simulation SÍ debe emitirse 'Simulation replay completed.'"
    )
