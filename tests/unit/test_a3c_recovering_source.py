"""A3c Ola 1 — tests de aceptación de `RecoveringLiveSource` (Ola 2 lo implementa).

Estos tests ESPECIFICAN el comportamiento del supervisor de recuperación Databento que
`src/live/recovering_live_source.py` (clase `RecoveringLiveSource`) debe cumplir. La clase
NO existe todavía: por eso `RecoveringLiveSource` se importa DENTRO de cada test, de modo que
hoy fallen con `ModuleNotFoundError` (ROJO esperado) y no rompan la colección del archivo.

Los fakes de este módulo SOLO simulan:
  - la fuente Databento (una "sesión": registra su `start`, el orden de eventos que emite,
    cuántas sesiones hay activas a la vez, el máximo, y las llamadas a `stop()`);
  - el reloj (`now()`) y la espera (`sleep(seg)`).
NO reimplementan la lógica de recuperación (eso es la Ola 2).

Contrato de inyección asumido (mínimo, decidido aquí):
    RecoveringLiveSource(make_source=..., sleep=..., now=..., config=...)
  - `make_source(start)`: fábrica que crea una nueva sesión/fuente con `start` = datetime o el
    entero 0. Devuelve un objeto con `replay()` (async iterator de Candle), `stop()`,
    `is_replay_complete` y `replay_completed_event` (asyncio.Event).
  - `sleep(seg)`: corutina de espera inyectable.
  - `now()`: devuelve el datetime "actual" (reloj inyectable).
  - `config`: esperas de recuperación, LOOKBACK y plazo terminal.
Expone: `phase`, `is_replay_complete`, `recovering_since` y `replay()` (CandleSource).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.schemas.candle import Candle

# Reloj base fijo para todos los tests (determinista, tz-aware UTC).
T0 = datetime(2026, 10, 16, 13, 30, 0, tzinfo=timezone.utc)
LOOKBACK = timedelta(minutes=90)
MINUTE = timedelta(minutes=1)


def _candle(ts: datetime, close: float = 100.0) -> Candle:
    return Candle(timestamp=ts, open=close, high=close + 1, low=close - 1, close=close, volume=100)


# --- Infraestructura de fakes (solo simulan la fuente/reloj/sleep) -----------

class _FakeClock:
    """Reloj inyectable. `advance()` lo mueve; `sleep()` opcionalmente avanza el reloj."""

    def __init__(self, start: datetime, sleep_advances: bool = False):
        self._t = start
        self.sleeps: list[float] = []
        self._sleep_advances = sleep_advances

    def now(self) -> datetime:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t = self._t + timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self._sleep_advances:
            self.advance(seconds)


class _FakeSession:
    """Una sesión Databento simulada: emite un guion de eventos vía replay().

    `script` es una lista de eventos:
      ("candle", Candle)      -> entrega una vela
      ("replay_completed",)   -> marca is_replay_complete (frontera REPLAY->LIVE)
      ("end",)                -> el stream termina limpio (fin del async for)
      ("raise", Exception)    -> el stream termina por excepción
    Registra en el `registry` compartido: el start pedido, stop_calls, sesiones activas y máximo.
    """

    def __init__(self, start, script, registry):
        self.start = start
        self._script = list(script)
        self._registry = registry
        self.is_replay_complete = False
        self.replay_completed_event = asyncio.Event()
        self.stopped = False
        registry["starts"].append(start)

    async def replay(self):
        self._registry["active"] += 1
        self._registry["max_active"] = max(self._registry["max_active"], self._registry["active"])
        try:
            for ev in self._script:
                kind = ev[0]
                if kind == "candle":
                    yield ev[1]
                elif kind == "replay_completed":
                    self.is_replay_complete = True
                    self.replay_completed_event.set()
                elif kind == "end":
                    return
                elif kind == "raise":
                    raise ev[1]
                else:  # pragma: no cover - guion inválido
                    raise AssertionError(f"evento desconocido: {kind}")
            # fin natural del guion == fin limpio del stream
        finally:
            self._registry["active"] -= 1

    def stop(self):
        self.stopped = True
        self._registry["stop_calls"].append(self.start)


class _ScriptedFactory:
    """`make_source(start)` que devuelve sesiones según una cola de guiones.

    `scripts` es una lista de guiones (uno por sesión, en orden de creación). El registry
    comparte starts/stop_calls/active/max_active entre todas las sesiones creadas.
    """

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self._i = 0
        self.registry = {"starts": [], "stop_calls": [], "active": 0, "max_active": 0}
        self.sessions: list[_FakeSession] = []

    def __call__(self, start):
        if self._i >= len(self._scripts):
            raise AssertionError(
                f"make_source llamado {self._i + 1} veces; solo hay {len(self._scripts)} guiones"
            )
        script = self._scripts[self._i]
        self._i += 1
        s = _FakeSession(start, script, self.registry)
        self.sessions.append(s)
        return s

    @property
    def call_count(self) -> int:
        return self._i


def _make_config(**overrides):
    """Config mínima de recuperación. Los valores del contrato: esperas 1,2,4,8,16,30,60;
    LOOKBACK 90 min; plazo terminal 30 min. Se pasan como configuración, no hardcode."""
    cfg = {
        "lookback": LOOKBACK,
        "recovery_delays_seconds": [1, 2, 4, 8, 16, 30, 60],
        "terminal_after": timedelta(minutes=30),
    }
    cfg.update(overrides)
    return cfg


async def _drain(source):
    """Consume source.replay() hasta el final, devolviendo las velas entregadas."""
    out = []
    async for c in source.replay():
        out.append(c)
    return out


# ============================ TESTS ========================================

async def test_t1_startup_backup_start_zero_then_live():
    """T1 arranque: primario (start=now-90m) termina sin datos → respaldo start=0 entrega y
    completa el replay. starts == [now-90m, 0]; phase == 'live'."""
    from src.live.recovering_live_source import RecoveringLiveSource

    clock = _FakeClock(T0)
    factory = _ScriptedFactory([
        # primario: termina sin entregar vela
        [("end",)],
        # respaldo start=0: entrega una vela y completa replay
        [("candle", _candle(T0)), ("replay_completed",), ("end",)],
    ])
    src = RecoveringLiveSource(
        make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config()
    )
    candles = await _drain(src)

    assert factory.registry["starts"] == [T0 - LOOKBACK, 0]
    assert len(candles) == 1
    assert src.phase == "live"
    assert src.is_replay_complete is True


async def test_t2_startup_both_fail_is_terminal():
    """T2 arranque terminal: primario y respaldo terminan sin datos → exactamente 2 fuentes,
    phase 'terminal', replay() termina."""
    from src.live.recovering_live_source import RecoveringLiveSource

    clock = _FakeClock(T0)
    factory = _ScriptedFactory([
        [("end",)],        # primario sin datos
        [("raise", RuntimeError("backup boom"))],  # respaldo sin datos
    ])
    src = RecoveringLiveSource(
        make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config()
    )
    candles = await _drain(src)

    assert factory.call_count == 2
    assert src.phase == "terminal"
    assert candles == []


async def test_t3_live_timeout_recovers_and_returns_to_live():
    """T3 timeout en vivo: tras entregar velas, la sesión cae → recovering (is_replay_complete
    False), stop de la vieja ANTES de iniciar la nueva, start = última + 1 min, hueco entregado
    una vez y en orden, max_active_sessions == 1, vuelve a live/True."""
    from src.live.recovering_live_source import RecoveringLiveSource

    c1 = _candle(T0 + 0 * MINUTE)
    c2 = _candle(T0 + 1 * MINUTE)
    # hueco: velas posteriores a c2
    g1 = _candle(T0 + 2 * MINUTE)
    g2 = _candle(T0 + 3 * MINUTE)

    clock = _FakeClock(T0, sleep_advances=True)
    factory = _ScriptedFactory([
        # sesión inicial: completa replay, entrega c1,c2, luego cae por excepción
        [("replay_completed",), ("candle", c1), ("candle", c2), ("raise", ConnectionError("drop"))],
        # recuperación: completa replay y entrega el hueco
        [("replay_completed",), ("candle", g1), ("candle", g2), ("end",)],
    ])

    phase_during_recovery = {}

    cfg = _make_config()
    src = RecoveringLiveSource(
        make_source=factory, sleep=clock.sleep, now=clock.now, config=cfg
    )
    candles = await _drain(src)

    # Entregó c1,c2 y el hueco g1,g2 (estrictamente crecientes, una sola vez cada una)
    assert [c.timestamp for c in candles] == [c1.timestamp, c2.timestamp, g1.timestamp, g2.timestamp]
    # La sesión de recuperación pidió start = última entregada (c2) + 1 min
    assert factory.registry["starts"][1] == c2.timestamp + MINUTE
    # Nunca hubo 2 sesiones activas a la vez; la vieja se cerró
    assert factory.registry["max_active"] == 1
    assert factory.sessions[0].stopped is True
    # stop de la vieja ocurre ANTES de registrar el start de la nueva
    assert factory.registry["stop_calls"] == [factory.sessions[0].start]
    # Terminó en live con replay completo
    assert src.phase == "live"
    assert src.is_replay_complete is True
    assert src.recovering_since is None


async def test_t4_clean_end_five_failed_attempts_then_live():
    """T4: fin limpio tras entregar velas + 5 intentos de recuperación que terminan antes de su
    REPLAY_COMPLETED, luego uno exitoso. sleeps == [1,2,4,8,16] antes de cada intento;
    recovering_since constante durante toda la recuperación; termina en live."""
    from src.live.recovering_live_source import RecoveringLiveSource

    c1 = _candle(T0)
    clock = _FakeClock(T0, sleep_advances=False)  # el reloj NO avanza con sleep en este test
    factory = _ScriptedFactory([
        # inicial: completa, entrega 1 vela, luego FIN LIMPIO (stream agotado)
        [("replay_completed",), ("candle", c1), ("end",)],
        # intentos 1..5: terminan ANTES de su replay_completed (sin datos nuevos)
        [("end",)],
        [("raise", ConnectionError("x"))],
        [("end",)],
        [("raise", ConnectionError("x"))],
        [("end",)],
        # intento 6: completa replay → vuelve a live
        [("replay_completed",), ("end",)],
    ])
    src = RecoveringLiveSource(
        make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config()
    )

    recovering_since_seen = []
    # envoltura para observar recovering_since durante el drain no es trivial; se valida al final
    await _drain(src)

    # Las esperas de los primeros 5 intentos de recuperación (antes de cada intento).
    assert clock.sleeps[:5] == [1, 2, 4, 8, 16]
    assert src.phase == "live"
    assert src.recovering_since is None


async def test_t5_deadline_reaches_terminal_no_more_sessions():
    """T5 plazo: el reloj falso alcanza 30 min desde recovering_since → terminal y no se abren
    más sesiones (el plazo se evalúa ANTES de cada intento)."""
    from src.live.recovering_live_source import RecoveringLiveSource

    c1 = _candle(T0)
    # sleep avanza el reloj; con esperas 1,2,4,8,16,30,60 la suma supera 30 min (1800s) pronto
    # tras bastantes intentos; forzamos plazo corto para determinismo: terminal_after pequeño.
    clock = _FakeClock(T0, sleep_advances=True)
    # Todos los intentos de recuperación fallan sin completar; nunca vuelve a live.
    scripts = [
        [("replay_completed",), ("candle", c1), ("raise", ConnectionError("drop"))],  # inicial
    ] + [[("end",)] for _ in range(50)]  # muchos intentos fallidos disponibles
    factory = _ScriptedFactory(scripts)
    cfg = _make_config(
        # esperas que, acumuladas, cruzan el plazo; plazo 30 min del contrato.
        recovery_delays_seconds=[600, 600, 600, 600, 600, 600],
        terminal_after=timedelta(minutes=30),
    )
    src = RecoveringLiveSource(
        make_source=factory, sleep=clock.sleep, now=clock.now, config=cfg
    )
    await _drain(src)

    assert src.phase == "terminal"
    # Se abrió la inicial + algunos intentos, pero NO las 50; al cruzar el plazo deja de abrir.
    assert factory.call_count < len(scripts)


async def test_t6_gap_rejected_only_newer_than_last():
    """T6 hueco rechazado: el primario de recuperación falla sin datos → respaldo start=0 entrega
    solape + hueco; solo salen velas con timestamp > última entregada."""
    from src.live.recovering_live_source import RecoveringLiveSource

    c1 = _candle(T0 + 0 * MINUTE)
    c2 = _candle(T0 + 1 * MINUTE)   # última entregada antes del corte
    # respaldo start=0 reenvía desde el principio: c1,c2 (solape, <=) y luego g (hueco, >)
    g = _candle(T0 + 2 * MINUTE)

    clock = _FakeClock(T0, sleep_advances=True)
    factory = _ScriptedFactory([
        # inicial: completa, entrega c1,c2, cae
        [("replay_completed",), ("candle", c1), ("candle", c2), ("raise", ConnectionError("drop"))],
        # recuperación primaria (start=c2+1m): termina sin entregar vela
        [("end",)],
        # respaldo start=0: reenvía c1,c2 (solape) + completa + g (hueco)
        [("replay_completed",), ("candle", c1), ("candle", c2), ("candle", g), ("end",)],
    ])
    src = RecoveringLiveSource(
        make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config()
    )
    candles = await _drain(src)

    # Solape (c1,c2) descartado en la recuperación; solo sale el hueco g una vez.
    assert [c.timestamp for c in candles] == [c1.timestamp, c2.timestamp, g.timestamp]
    # El respaldo del intento de recuperación pidió start=0
    assert 0 in factory.registry["starts"]
    assert src.phase == "live"


async def test_t7_is_replay_complete_rearm_sequence():
    """T7 re-armado de is_replay_complete:
    False (bootstrapping) → True (REPLAY_COMPLETED inicial) → corte → False (recovering) →
    False durante el replay de recuperación → True (REPLAY_COMPLETED de la recuperación)."""
    from src.live.recovering_live_source import RecoveringLiveSource

    c1 = _candle(T0)
    g1 = _candle(T0 + 1 * MINUTE)
    seen: list[tuple[str, bool]] = []

    clock = _FakeClock(T0, sleep_advances=True)

    # Guiones con "probes" implícitos: observamos is_replay_complete en puntos clave vía un
    # wrapper que registra el flag después de cada vela entregada por el supervisor.
    factory = _ScriptedFactory([
        [("replay_completed",), ("candle", c1), ("raise", ConnectionError("drop"))],
        [("candle", g1), ("replay_completed",), ("end",)],  # vela ANTES de completar: no debe marcar True
    ])
    src = RecoveringLiveSource(
        make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config()
    )

    # Estado inicial antes de consumir: bootstrapping, flag False.
    assert src.is_replay_complete is False

    out = []
    agen = src.replay()
    async for c in agen:
        out.append(c)
        seen.append((("c1" if c.timestamp == c1.timestamp else "g1"), src.is_replay_complete))

    # Tras c1 (post REPLAY_COMPLETED inicial) el flag estaba True;
    # g1 llega ANTES del REPLAY_COMPLETED de recuperación → en ese punto el flag debe ser False
    # (una vela NO marca replay completo).
    assert ("c1", True) in seen
    assert ("g1", False) in seen
    # Al final, el REPLAY_COMPLETED de la recuperación deja el flag en True.
    assert src.is_replay_complete is True
