"""A3c Ola 1.1 — tests de aceptación de `RecoveringLiveSource` (Ola 2 lo implementa).

ESPECIFICAN el comportamiento del supervisor de recuperación Databento que
`src/live/recovering_live_source.py` (clase `RecoveringLiveSource`) debe cumplir. La clase
NO existe todavía: `RecoveringLiveSource` se importa DENTRO de cada test, de modo que hoy
fallen con `ModuleNotFoundError` (ROJO esperado) sin romper la colección del archivo.

Reglas del contrato (aclaraciones vinculantes Ola 1.1):
  R1. Un fin de stream DESPUÉS de haber entregado velas abre una recuperación; nunca es fin
      exitoso de replay().
  R2. Cerrar el consumidor (aclose del generador de replay()) NO es desconexión: detiene la
      sesión actual y termina sin crear sesiones nuevas.
  R3. Toda sesión que termina (arranque, recuperación o respaldo) recibe stop() ANTES de que la
      siguiente inicie su replay.
  R4. El plazo de 30 min se evalúa DESPUÉS de cada espera y ANTES de abrir el intento.

Contrato de inyección asumido (mínimo):
    RecoveringLiveSource(make_source=..., sleep=..., now=..., config=...)
  - make_source(start): fábrica de sesión con start = datetime o el entero 0. Devuelve objeto
    con replay() (async iterator de Candle), stop(), is_replay_complete, replay_completed_event.
  - sleep(seg): corutina de espera inyectable.  now(): datetime "actual" inyectable.
  - config: lookback, recovery_delays_seconds, terminal_after.
Expone: phase, is_replay_complete, recovering_since, replay() (CandleSource).

ARNÉS:
  - Evento ("hold",): sesión sana que queda abierta (await de un Event que nunca se activa).
    Toda sesión que deba quedar en vivo termina en ("hold",), nunca en ("end",).
  - take(src, n): agen = src.replay() UNA vez; n anext; en finally await agen.aclose().
  - Registro ordenado de eventos: ("make",id,start),("replay_start",id,start),
    ("stop",id,start),("replay_end",id,start).
  - El sleep falso registra (segundos, phase, is_replay_complete, recovering_since).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.schemas.candle import Candle

T0 = datetime(2026, 10, 16, 13, 30, 0, tzinfo=timezone.utc)
LOOKBACK = timedelta(minutes=90)
MINUTE = timedelta(minutes=1)


def _candle(ts: datetime, close: float = 100.0) -> Candle:
    return Candle(timestamp=ts, open=close, high=close + 1, low=close - 1, close=close, volume=100)


# --- Infraestructura de fakes (solo simulan la fuente/reloj/sleep) -----------

class _FakeClock:
    """Reloj + sleep inyectables. El sleep falso registra el estado del supervisor."""

    def __init__(self, start: datetime, sleep_advances: bool = False):
        self._t = start
        self._sleep_advances = sleep_advances
        self.sleeps: list[float] = []
        # Observaciones ricas en cada sleep: (segundos, phase, is_replay_complete, recovering_since)
        self.observations: list[tuple] = []
        self._src = None  # se liga tras crear el supervisor

    def bind(self, src) -> None:
        self._src = src

    def now(self) -> datetime:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t = self._t + timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self._src is not None:
            self.observations.append(
                (seconds, self._src.phase, self._src.is_replay_complete, self._src.recovering_since)
            )
        if self._sleep_advances:
            self.advance(seconds)


class _FakeSession:
    """Sesión Databento simulada por un guion de eventos vía replay().

    Eventos:
      ("candle", Candle)    -> entrega una vela
      ("replay_completed",) -> marca is_replay_complete (frontera REPLAY->LIVE)
      ("end",)              -> fin limpio del stream (fin del async for)
      ("raise", Exception)  -> fin por excepción
      ("hold",)             -> sesión sana que queda abierta (await de un Event que nunca se activa)
    Escribe eventos ordenados en el log compartido y mantiene el conteo de sesiones activas.
    """

    _next_id = 0

    def __init__(self, start, script, registry):
        self.start = start
        self._script = list(script)
        self._registry = registry
        self.is_replay_complete = False
        self.replay_completed_event = asyncio.Event()
        self.stopped = False
        _FakeSession._next_id += 1
        self.id = _FakeSession._next_id
        self._hold_gate = asyncio.Event()  # nunca se activa -> "hold" bloquea hasta cancelación
        registry["starts"].append(start)
        registry["events"].append(("make", self.id, start))

    async def replay(self):
        self._registry["events"].append(("replay_start", self.id, self.start))
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
                elif kind == "hold":
                    # Sesión viva: espera indefinidamente (hasta que el consumidor haga aclose,
                    # que cancela este await). No termina por sí sola.
                    await self._hold_gate.wait()
                else:  # pragma: no cover
                    raise AssertionError(f"evento desconocido: {kind}")
        finally:
            self._registry["active"] -= 1
            self._registry["events"].append(("replay_end", self.id, self.start))

    def stop(self):
        self.stopped = True
        self._hold_gate.set()  # libera un "hold" si lo hubiera
        self._registry["events"].append(("stop", self.id, self.start))


class _ScriptedFactory:
    """make_source(start) que entrega sesiones según una cola de guiones (en orden de creación)."""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self._i = 0
        self.registry = {"starts": [], "events": [], "active": 0, "max_active": 0}
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

    # --- helpers de orden sobre el log de eventos ---
    def _first_index(self, kind, sess_id):
        for idx, ev in enumerate(self.registry["events"]):
            if ev[0] == kind and ev[1] == sess_id:
                return idx
        raise AssertionError(f"evento {kind} de sesión {sess_id} no registrado")


def _make_config(**overrides):
    cfg = {
        "lookback": LOOKBACK,
        "recovery_delays_seconds": [1, 2, 4, 8, 16, 30, 60],
        "terminal_after": timedelta(minutes=30),
    }
    cfg.update(overrides)
    return cfg


def _new(factory, clock, cfg=None):
    from src.live.recovering_live_source import RecoveringLiveSource
    src = RecoveringLiveSource(
        make_source=factory, sleep=clock.sleep, now=clock.now, config=cfg or _make_config()
    )
    clock.bind(src)
    return src


async def take(src, n):
    """Consume exactamente n velas de un único replay(); cierra el generador en finally (R2)."""
    agen = src.replay()
    out = []
    try:
        for _ in range(n):
            out.append(await agen.__anext__())
    finally:
        await agen.aclose()
    return out


async def _drain(src):
    out = []
    async for c in src.replay():
        out.append(c)
    return out


# ============================ TESTS ========================================

async def test_t1_startup_backup_start_zero_then_live():
    """T1: primario sin datos → respaldo start=0 [r1, replay_completed, live1, hold]; take 2."""
    from src.live.recovering_live_source import RecoveringLiveSource  # noqa: F401 (import ROJO hoy)

    clock = _FakeClock(T0)
    r1 = _candle(T0 - 30 * MINUTE)   # vela del replay (antes del completed)
    live1 = _candle(T0 - 1 * MINUTE)
    factory = _ScriptedFactory([
        [("end",)],  # primario: termina sin entregar vela
        [("candle", r1), ("replay_completed",), ("candle", live1), ("hold",)],  # respaldo
    ])
    src = _new(factory, clock)

    got = await take(src, 2)

    assert factory.registry["starts"] == [T0 - LOOKBACK, 0]
    assert [c.timestamp for c in got] == [r1.timestamp, live1.timestamp]
    # stop(primario) ocurre antes de replay_start(respaldo)
    primario, respaldo = factory.sessions[0], factory.sessions[1]
    assert factory._first_index("stop", primario.id) < factory._first_index("replay_start", respaldo.id)
    assert src.phase == "live"
    assert src.is_replay_complete is True
    # Tras aclose: no se crearon más sesiones y el respaldo quedó detenido (R2).
    assert factory.call_count == 2
    assert factory.sessions[1].stopped is True


async def test_t2_startup_both_fail_is_terminal():
    """T2 arranque terminal: primario y respaldo sin datos → 2 fuentes, terminal, replay() termina."""
    from src.live.recovering_live_source import RecoveringLiveSource  # noqa: F401

    clock = _FakeClock(T0)
    factory = _ScriptedFactory([
        [("end",)],
        [("raise", RuntimeError("backup boom"))],
    ])
    src = _new(factory, clock)
    candles = await _drain(src)

    assert candles == []
    assert factory.registry["starts"] == [T0 - LOOKBACK, 0]
    assert factory.call_count == 2
    assert src.phase == "terminal"


async def test_t3_live_timeout_recovers_and_returns_to_live():
    """T3 timeout en vivo: cae tras entregar c1,c2 → recovering; recuperación entrega el hueco."""
    from src.live.recovering_live_source import RecoveringLiveSource  # noqa: F401

    c1 = _candle(T0 + 0 * MINUTE)
    c2 = _candle(T0 + 1 * MINUTE)
    g1 = _candle(T0 + 2 * MINUTE)
    g2 = _candle(T0 + 3 * MINUTE)
    live1 = _candle(T0 + 4 * MINUTE)

    clock = _FakeClock(T0, sleep_advances=True)
    factory = _ScriptedFactory([
        [("replay_completed",), ("candle", c1), ("candle", c2), ("raise", ConnectionError("Gateway timeout"))],
        [("candle", g1), ("candle", g2), ("replay_completed",), ("candle", live1), ("hold",)],
    ])
    src = _new(factory, clock)

    got = await take(src, 5)

    assert [c.timestamp for c in got] == [c1.timestamp, c2.timestamp, g1.timestamp, g2.timestamp, live1.timestamp]
    assert factory.registry["starts"][1] == c2.timestamp + MINUTE
    # Observación en el sleep de recuperación: recovering, replay NO completo, recovering_since != None
    assert clock.observations, "debió haber al menos un sleep de recuperación"
    secs, phase, complete, since = clock.observations[0]
    assert phase == "recovering"
    assert complete is False
    assert since is not None
    # stop(inicial) < replay_start(recuperación)
    ini, rec = factory.sessions[0], factory.sessions[1]
    assert factory._first_index("stop", ini.id) < factory._first_index("replay_start", rec.id)
    assert factory.registry["max_active"] == 1
    assert src.phase == "live"
    assert src.is_replay_complete is True
    assert src.recovering_since is None
    # Tras aclose: ninguna sesión nueva más allá de las 2 guionadas.
    assert factory.call_count == 2


async def test_t4_clean_end_five_failed_attempts_then_live():
    """T4 fin limpio + 5 intentos fallidos (primario+respaldo c/u) + 6º primario exitoso."""
    from src.live.recovering_live_source import RecoveringLiveSource  # noqa: F401

    c1 = _candle(T0)
    g1 = _candle(T0 + 1 * MINUTE)
    live1 = _candle(T0 + 2 * MINUTE)
    clock = _FakeClock(T0, sleep_advances=True)
    scripts = [
        [("replay_completed",), ("candle", c1), ("end",)],  # inicial: fin limpio TRAS entregar (R1 => recovering)
    ]
    # 5 intentos fallidos: cada uno primario sin datos + respaldo sin datos = 10 sesiones
    for _ in range(5):
        scripts.append([("end",)])   # primario del intento, sin datos
        scripts.append([("end",)])   # respaldo start=0 del intento, sin datos
    # 6º intento: primario entrega + completa + live + hold
    scripts.append([("candle", g1), ("replay_completed",), ("candle", live1), ("hold",)])
    factory = _ScriptedFactory(scripts)
    src = _new(factory, clock)

    got = await take(src, 3)  # c1 (inicial) + g1 + live1 (6º intento)

    assert [c.timestamp for c in got] == [c1.timestamp, g1.timestamp, live1.timestamp]
    assert factory.call_count == 12
    expected_starts = [T0 - LOOKBACK] + [c1.timestamp + MINUTE, 0] * 5 + [c1.timestamp + MINUTE]
    assert factory.registry["starts"] == expected_starts
    assert clock.sleeps == [1, 2, 4, 8, 16, 30]
    # En todos los sleeps: recovering, replay NO completo, y el MISMO recovering_since.
    since_values = {obs[3] for obs in clock.observations}
    assert all(obs[1] == "recovering" for obs in clock.observations)
    assert all(obs[2] is False for obs in clock.observations)
    assert len(since_values) == 1 and None not in since_values
    assert src.phase == "live"
    assert src.recovering_since is None


async def test_t5_deadline_reaches_terminal_no_more_sessions():
    """T5 plazo: esperas [600]×n, plazo 30 min → sleeps [600,600,600], call_count 5, terminal."""
    from src.live.recovering_live_source import RecoveringLiveSource  # noqa: F401

    c1 = _candle(T0)
    clock = _FakeClock(T0, sleep_advances=True)
    scripts = [
        [("replay_completed",), ("candle", c1), ("raise", ConnectionError("drop"))],  # inicial
    ] + [[("end",)] for _ in range(50)]  # intentos fallidos disponibles de sobra
    factory = _ScriptedFactory(scripts)
    cfg = _make_config(recovery_delays_seconds=[600], terminal_after=timedelta(minutes=30))
    src = _new(factory, clock, cfg)

    await _drain(src)

    # Esperas de 600s: tras la 3ª (1800s) el reloj cruza el plazo y antes del 4º intento -> terminal.
    assert clock.sleeps == [600, 600, 600]
    # inicial + 4 sesiones de recuperación abiertas (las 3 que durmieron+abrieron y se evalúa el
    # plazo ANTES del 4º): call_count == 5.
    assert factory.call_count == 5
    since_values = {obs[3] for obs in clock.observations}
    assert len(since_values) == 1 and None not in since_values
    assert src.phase == "terminal"


async def test_t6_gap_rejected_only_newer_than_last():
    """T6 hueco rechazado: recuperación primaria sin datos → respaldo start=0 reenvía solape+hueco."""
    from src.live.recovering_live_source import RecoveringLiveSource  # noqa: F401

    c1 = _candle(T0 + 0 * MINUTE)
    c2 = _candle(T0 + 1 * MINUTE)   # última entregada antes del corte
    g1 = _candle(T0 + 2 * MINUTE)
    g2 = _candle(T0 + 3 * MINUTE)
    live1 = _candle(T0 + 4 * MINUTE)

    clock = _FakeClock(T0, sleep_advances=True)
    factory = _ScriptedFactory([
        [("replay_completed",), ("candle", c1), ("candle", c2), ("raise", ConnectionError("drop"))],
        [("end",)],  # recuperación primaria (start=c2+1m) sin datos
        # respaldo start=0: reenvía c1,c2 (solape, <=) + g1,g2 (hueco, >) + completa + live + hold
        [("candle", c1), ("candle", c2), ("candle", g1), ("candle", g2), ("replay_completed",), ("candle", live1), ("hold",)],
    ])
    src = _new(factory, clock)

    got = await take(src, 5)  # c1,c2 (iniciales) + g1,g2 (hueco) + live1

    assert [c.timestamp for c in got] == [c1.timestamp, c2.timestamp, g1.timestamp, g2.timestamp, live1.timestamp]
    assert factory.registry["starts"] == [T0 - LOOKBACK, c2.timestamp + MINUTE, 0]
    # Durante la entrega de g1/g2 (replay del respaldo) is_replay_complete era False: lo validamos
    # indirectamente por el orden del guion (replay_completed llega DESPUÉS de g2) y por el estado
    # final. No hay sleep entre g1/g2; el estado durante el replay se refleja en que live1 llega
    # ya con replay completo.
    assert src.phase == "live"
    assert src.is_replay_complete is True


async def test_t7_is_replay_complete_rearm_sequence():
    """T7 re-armado observando con anext: bootstrapping/False → live/True (1ª live) →
    recovering/False (g1) → live/True (live1). Termina en hold."""
    from src.live.recovering_live_source import RecoveringLiveSource  # noqa: F401

    live_ini = _candle(T0 + 0 * MINUTE)   # 1ª vela live tras REPLAY_COMPLETED inicial
    g1 = _candle(T0 + 1 * MINUTE)         # vela de recuperación ANTES de su replay_completed
    live1 = _candle(T0 + 2 * MINUTE)      # live tras replay_completed de recuperación

    clock = _FakeClock(T0, sleep_advances=True)
    factory = _ScriptedFactory([
        [("replay_completed",), ("candle", live_ini), ("raise", ConnectionError("drop"))],
        [("candle", g1), ("replay_completed",), ("candle", live1), ("hold",)],
    ])
    src = _new(factory, clock)

    # Estado inicial antes de consumir.
    assert src.phase == "bootstrapping"
    assert src.is_replay_complete is False

    agen = src.replay()
    try:
        c_live_ini = await agen.__anext__()
        assert c_live_ini.timestamp == live_ini.timestamp
        assert src.phase == "live" and src.is_replay_complete is True

        c_g1 = await agen.__anext__()
        assert c_g1.timestamp == g1.timestamp
        # g1 llega durante el replay de recuperación, ANTES de su replay_completed.
        assert src.phase == "recovering" and src.is_replay_complete is False

        c_live1 = await agen.__anext__()
        assert c_live1.timestamp == live1.timestamp
        assert src.phase == "live" and src.is_replay_complete is True
    finally:
        await agen.aclose()


# ============================ TESTS Ola 2 (T12, T13) =======================

async def test_t12_replay_completed_event_latches_and_never_clears():
    """T12: replay_completed_event: False al inicio; True tras el primer REPLAY_COMPLETED; sigue
    True durante una recuperación (mientras is_replay_complete del supervisor es False) y después
    de ella. Nunca se limpia."""
    from src.live.recovering_live_source import RecoveringLiveSource  # noqa: F401

    live_ini = _candle(T0 + 0 * MINUTE)
    g1 = _candle(T0 + 1 * MINUTE)      # vela de recuperación ANTES del replay_completed de recuperación
    live1 = _candle(T0 + 2 * MINUTE)

    clock = _FakeClock(T0, sleep_advances=True)
    factory = _ScriptedFactory([
        [("replay_completed",), ("candle", live_ini), ("raise", ConnectionError("drop"))],
        [("candle", g1), ("replay_completed",), ("candle", live1), ("hold",)],
    ])
    src = _new(factory, clock)

    # Al inicio: el evento existe y NO está activado.
    assert src.replay_completed_event.is_set() is False

    agen = src.replay()
    try:
        c0 = await agen.__anext__()     # live_ini: tras REPLAY_COMPLETED inicial
        assert c0.timestamp == live_ini.timestamp
        assert src.replay_completed_event.is_set() is True  # se activó con el 1er REPLAY_COMPLETED

        c1 = await agen.__anext__()     # g1: durante la recuperación (is_replay_complete False)
        assert c1.timestamp == g1.timestamp
        assert src.is_replay_complete is False
        # El EVENTO latcheado sigue True aunque el supervisor esté recovering.
        assert src.replay_completed_event.is_set() is True

        c2 = await agen.__anext__()     # live1: tras REPLAY_COMPLETED de recuperación
        assert c2.timestamp == live1.timestamp
        assert src.is_replay_complete is True
        assert src.replay_completed_event.is_set() is True
    finally:
        await agen.aclose()

    # Después de todo: el evento jamás se limpió.
    assert src.replay_completed_event.is_set() is True


class _InertStopSession:
    """Fake PROPIO de T13: stop() SOLO registra (no libera 'hold' ni termina el generador).

    El único modo de desbloquear un 'hold' o de finalizar el generador es que el consumidor
    cierre el async generator (aclose), lo que inyecta GeneratorExit en el `await`/`yield`.
    Así T13 comprueba que el supervisor cierra realmente el generador interno (aclose), no solo
    que llama stop().
    """

    _next_id = 0

    def __init__(self, start, script, registry):
        self.start = start
        self._script = list(script)
        self._registry = registry
        self.is_replay_complete = False
        self.replay_completed_event = asyncio.Event()
        self.stopped = False
        _InertStopSession._next_id += 1
        self.id = _InertStopSession._next_id
        self._never = asyncio.Event()  # jamás se activa; stop() NO lo toca
        registry["starts"].append(start)
        registry["events"].append(("make", self.id, start))

    async def replay(self):
        self._registry["events"].append(("replay_start", self.id, self.start))
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
                elif kind == "hold":
                    await self._never.wait()   # solo GeneratorExit (aclose) lo desbloquea
                else:  # pragma: no cover
                    raise AssertionError(f"evento desconocido: {kind}")
        finally:
            self._registry["active"] -= 1
            self._registry["events"].append(("replay_end", self.id, self.start))

    def stop(self):
        # SOLO registra. No libera 'hold' ni termina el generador.
        self.stopped = True
        self._registry["events"].append(("stop", self.id, self.start))


class _InertStopFactory:
    def __init__(self, scripts):
        self._scripts = list(scripts)
        self._i = 0
        self.registry = {"starts": [], "events": [], "active": 0, "max_active": 0}
        self.sessions: list[_InertStopSession] = []

    def __call__(self, start):
        if self._i >= len(self._scripts):
            raise AssertionError(
                f"make_source llamado {self._i + 1} veces; solo hay {len(self._scripts)} guiones"
            )
        script = self._scripts[self._i]
        self._i += 1
        s = _InertStopSession(start, script, self.registry)
        self.sessions.append(s)
        return s

    @property
    def call_count(self) -> int:
        return self._i

    def _first_index(self, kind, sess_id):
        for idx, ev in enumerate(self.registry["events"]):
            if ev[0] == kind and ev[1] == sess_id:
                return idx
        raise AssertionError(f"evento {kind} de sesión {sess_id} no registrado")


async def test_t13_inner_stream_closed_on_cut_and_on_external_aclose():
    """T13 cierre real del stream interno, con un fake donde stop() solo registra.

    (A) corte en vivo → ('replay_end', vieja) ANTES de ('replay_start', nueva).
    (B) aclose del generador externo con la sesión en 'hold' → se registran stop(actual) y
        ('replay_end', actual) y NO se crea otra sesión.
    Las esperas se envuelven con asyncio.wait_for(..., 2) para que un cierre faltante falle
    (timeout) en vez de colgar.
    """
    from src.live.recovering_live_source import RecoveringLiveSource  # noqa: F401

    c1 = _candle(T0 + 0 * MINUTE)
    g1 = _candle(T0 + 1 * MINUTE)
    live1 = _candle(T0 + 2 * MINUTE)

    clock = _FakeClock(T0, sleep_advances=True)
    factory = _InertStopFactory([
        # inicial: completa, entrega c1, luego CORTE en vivo (raise) -> abre recuperación
        [("replay_completed",), ("candle", c1), ("raise", ConnectionError("cut"))],
        # recuperación: entrega g1, completa, entrega live1, y queda en 'hold'
        [("candle", g1), ("replay_completed",), ("candle", live1), ("hold",)],
    ])
    from src.live.recovering_live_source import RecoveringLiveSource as _RLS
    src = _RLS(make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config())
    clock.bind(src)

    agen = src.replay()
    try:
        c_c1 = await asyncio.wait_for(agen.__anext__(), 2)
        assert c_c1.timestamp == c1.timestamp

        # (A) El corte en vivo debe cerrar el generador de la sesión vieja (replay_end) ANTES de
        # abrir la nueva (replay_start). Consumimos g1 (primera vela de la recuperación).
        c_g1 = await asyncio.wait_for(agen.__anext__(), 2)
        assert c_g1.timestamp == g1.timestamp

        old = factory.sessions[0]
        new = factory.sessions[1]
        assert factory._first_index("replay_end", old.id) < factory._first_index("replay_start", new.id)
        assert old.stopped is True  # stop() de la vieja fue llamado

        # Avanzamos a live1 (sesión de recuperación ya en vivo; luego quedará en 'hold').
        c_live1 = await asyncio.wait_for(agen.__anext__(), 2)
        assert c_live1.timestamp == live1.timestamp
    finally:
        # (B) Cierre externo con la sesión actual en 'hold': el supervisor debe cerrar el
        # generador interno (aclose -> replay_end) y llamar stop(actual), sin abrir otra sesión.
        await asyncio.wait_for(agen.aclose(), 2)

    new = factory.sessions[1]
    assert new.stopped is True
    assert ("replay_end", new.id, new.start) in factory.registry["events"]
    assert factory.call_count == 2  # no se creó ninguna sesión adicional



# ============================ TESTS Ola 2.1 (T14–T19) =====================

async def test_t14_consumer_cancelled_while_session_holds():
    """T14: cancelar la tarea consumidora con la sesión esperando ('hold') → CancelledError se
    propaga, la sesión actual recibe stop() y replay_end, y NO se abre otra sesión."""
    from src.live.recovering_live_source import RecoveringLiveSource

    c1 = _candle(T0)
    clock = _FakeClock(T0, sleep_advances=True)
    factory = _InertStopFactory([[("replay_completed",), ("candle", c1), ("hold",)]] + [[("hold",)]] * 3)
    src = RecoveringLiveSource(make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config())
    clock.bind(src)
    got = []

    async def consume():
        async for c in src.replay():
            got.append(c)

    task = asyncio.create_task(consume())
    for _ in range(50):
        if got:
            break
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert [c.timestamp for c in got] == [c1.timestamp]
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=2)
    assert task in done, "la cancelación no se propagó: el supervisor la absorbió"
    with pytest.raises(asyncio.CancelledError):
        task.result()

    s = factory.sessions[0]
    assert s.stopped is True
    assert ("replay_end", s.id, s.start) in factory.registry["events"]
    assert factory.call_count == 1
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert pending == [], f"tareas internas huérfanas: {pending}"


async def test_t15_startup_cut_after_data_opens_recovery_not_backup():
    """T15 (R1 en el arranque): si el primario ENTREGA velas y se corta antes de REPLAY_COMPLETED,
    se abre recuperación (espera + start = última + 1 min), no el respaldo start=0 inmediato."""
    from src.live.recovering_live_source import RecoveringLiveSource

    a, b, g = _candle(T0), _candle(T0 + MINUTE), _candle(T0 + 2 * MINUTE)
    clock = _FakeClock(T0, sleep_advances=True)
    factory = _InertStopFactory([
        [("candle", a), ("candle", b), ("raise", ConnectionError("cut"))],
        [("candle", g), ("replay_completed",), ("hold",)],
    ])
    src = RecoveringLiveSource(make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config())
    clock.bind(src)
    agen = src.replay()
    try:
        out = [await asyncio.wait_for(agen.__anext__(), 2) for _ in range(3)]
        assert [c.timestamp for c in out] == [a.timestamp, b.timestamp, g.timestamp]
        assert factory.registry["starts"] == [T0 - LOOKBACK, b.timestamp + MINUTE]
        assert clock.sleeps == [1]
        assert src.phase == "recovering"  # g llegó antes de su REPLAY_COMPLETED: recuperando, NO terminal
        assert src.is_replay_complete is False
    finally:
        await asyncio.wait_for(agen.aclose(), 2)
    assert factory.call_count == 2
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert pending == [], f"tareas internas huérfanas: {pending}"


async def _start_consumer(src):
    got = []

    async def consume():
        async for c in src.replay():
            got.append(c)

    return asyncio.create_task(consume()), got


async def _cancel(task):
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=2)
    assert task in done, "la cancelación no se propagó"


async def test_t16_bootstrap_replay_completed_without_next_candle():
    """T16: REPLAY_COMPLETED en el arranque SIN vela posterior (mercado cerrado) → el supervisor
    pasa a live sin esperar otra vela: evento activado, is_replay_complete True, phase 'live'."""
    from src.live.recovering_live_source import RecoveringLiveSource

    clock = _FakeClock(T0, sleep_advances=True)
    factory = _InertStopFactory([[("replay_completed",), ("hold",)]])
    src = RecoveringLiveSource(make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config())
    clock.bind(src)
    task, got = await _start_consumer(src)
    try:
        await asyncio.wait_for(src.replay_completed_event.wait(), 2)
        assert src.is_replay_complete is True
        assert src.phase == "live"
        assert got == []
        assert not any(ev[0] == "replay_end" for ev in factory.registry["events"])  # sesión sigue abierta
    finally:
        await _cancel(task)
    assert factory.call_count == 1


async def test_t17_recovery_replay_completed_without_next_candle():
    """T17 (caso sábado): la sesión de recuperación completa su replay SIN velas nuevas → vuelve a
    live sin esperar vela: phase 'live', is_replay_complete True, recovering_since None."""
    from src.live.recovering_live_source import RecoveringLiveSource

    c1 = _candle(T0)
    clock = _FakeClock(T0, sleep_advances=True)
    factory = _InertStopFactory([
        [("replay_completed",), ("candle", c1), ("raise", ConnectionError("gateway restart"))],
        [("replay_completed",), ("hold",)],
    ])
    src = RecoveringLiveSource(make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config())
    clock.bind(src)
    task, got = await _start_consumer(src)
    try:
        for _ in range(200):
            if factory.call_count == 2 and src.phase == "live":
                break
            await asyncio.sleep(0)
        assert [c.timestamp for c in got] == [c1.timestamp]
        assert factory.call_count == 2
        assert src.phase == "live"
        assert src.is_replay_complete is True
        assert src.recovering_since is None
        assert clock.sleeps == [1]
    finally:
        await _cancel(task)


async def test_t18_recovery_without_anchor_uses_start_zero_once_per_attempt():
    """T18: sin vela entregada (last_delivered None) la recuperación usa start=0 como primario, sin
    TypeError, y si falla antes de datos NO repite start=0 en el mismo intento: espera y reintenta."""
    from src.live.recovering_live_source import RecoveringLiveSource

    g = _candle(T0 + MINUTE)
    clock = _FakeClock(T0, sleep_advances=True)
    factory = _InertStopFactory([
        [("replay_completed",), ("end",)],                 # arranque: live sin velas, luego fin
        [("end",)],                                        # intento 1: start=0 falla sin datos
        [("replay_completed",), ("candle", g), ("hold",)], # intento 2: start=0 recupera
    ])
    src = RecoveringLiveSource(make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config())
    clock.bind(src)
    agen = src.replay()
    try:
        c = await asyncio.wait_for(agen.__anext__(), 2)
        assert c.timestamp == g.timestamp
        assert factory.registry["starts"] == [T0 - LOOKBACK, 0, 0]
        assert clock.sleeps == [1, 2]
        assert factory.registry["max_active"] == 1
        assert src.phase == "live"
    finally:
        await asyncio.wait_for(agen.aclose(), 2)
    assert factory.call_count == 3


class _YieldingClock(_FakeClock):
    """Como _FakeClock, pero su sleep CEDE el control al loop (como asyncio.sleep real) y registra
    el estado del supervisor DESPUÉS de ceder."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.after_yield: list[tuple] = []

    async def sleep(self, seconds: float) -> None:
        await super().sleep(seconds)
        for _ in range(5):
            await asyncio.sleep(0)
        self.after_yield.append((self._src.phase, self._src.recovering_since))


async def test_t19_ended_session_cannot_mark_live_during_backoff():
    """T19: REPLAY_COMPLETED y corte en la misma ráfaga. Durante la espera de recuperación (que cede
    el loop) nada de la sesión terminada puede devolver la fase a live ni borrar recovering_since."""
    from src.live.recovering_live_source import RecoveringLiveSource

    c1, g = _candle(T0), _candle(T0 + MINUTE)
    clock = _YieldingClock(T0, sleep_advances=True)
    factory = _InertStopFactory([
        [("replay_completed",), ("candle", c1), ("raise", ConnectionError("cut"))],
        [("candle", g), ("replay_completed",), ("hold",)],
    ])
    src = RecoveringLiveSource(make_source=factory, sleep=clock.sleep, now=clock.now, config=_make_config())
    clock.bind(src)
    agen = src.replay()
    try:
        out = [await asyncio.wait_for(agen.__anext__(), 2) for _ in range(2)]
        assert [c.timestamp for c in out] == [c1.timestamp, g.timestamp]
        assert clock.after_yield == [("recovering", T0)]
    finally:
        await asyncio.wait_for(agen.aclose(), 2)
