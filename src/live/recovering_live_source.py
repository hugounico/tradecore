"""A3c Ola 2 — `RecoveringLiveSource`: supervisor de recuperación de la fuente Live.

Envuelve una FÁBRICA de fuentes Databento (`make_source(start)`) y expone el contrato
`CandleSource` (`replay() -> AsyncIterator[Candle]`), gobernando el ciclo de vida de las
sesiones ante cortes (excepción o fin de stream) SIN delegar en el reconnect del SDK.

Diseño (R1–R4 de los tests de aceptación):
  R1. Un fin de stream DESPUÉS de haber entregado velas abre una recuperación; nunca es un fin
      exitoso de replay().
  R2. Cerrar el consumidor (aclose del generador externo) detiene la sesión actual y termina sin
      crear sesiones nuevas (se propaga la cancelación).
  R3. Toda sesión que termina (arranque, recuperación o respaldo) recibe aclose() + stop() ANTES
      de que la siguiente inicie su replay (nunca hay más de 1 sesión activa).
  R4. El plazo terminal (p.ej. 30 min) se evalúa DESPUÉS de cada espera y ANTES de abrir el intento.

Reglas adicionales:
  - Arranque: primario con start = now() − lookback. Si termina ANTES de entregar una vela →
    UN respaldo con start=0. Si también falla antes de entregar vela → terminal.
  - Cada intento de recuperación: primero la espera (delays 1,2,4,8,16,30,60 s; se reutiliza el
    último), luego el chequeo de plazo, luego primario con start = última vela entregada + 1 min;
    si termina antes de entregar vela → UN respaldo start=0 en ese mismo intento; si la sesión
    termina antes de su REPLAY_COMPLETED → siguiente intento.
  - Solo se entregan velas con timestamp ESTRICTAMENTE mayor que la última entregada.
  - `replay_completed_event` se activa con el PRIMER REPLAY_COMPLETED y NUNCA se limpia.
  - `recovering_since` se fija UNA vez al entrar en recovering y vuelve a None al volver a live.
  - Logs solo de ciclo de vida; nada por vela; sin credenciales; `start` formateado seguro.

NO importa Databento ni app.py. Única autoridad de recuperación (no activa SDK RECONNECT).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

from src.schemas.candle import Candle

logger = logging.getLogger(__name__)

PHASE_BOOTSTRAPPING = "bootstrapping"
PHASE_LIVE = "live"
PHASE_RECOVERING = "recovering"
PHASE_TERMINAL = "terminal"

_ONE_MINUTE = timedelta(minutes=1)


def _fmt_start(start) -> str:
    """Formatea el `start` para logs de forma segura: datetime.isoformat() o el entero 0."""
    if isinstance(start, datetime):
        return start.isoformat()
    return str(start)


class _SessionResult:
    """Resultado de ejecutar una sesión: si entregó velas y si completó su replay."""

    __slots__ = ("delivered", "completed")

    def __init__(self):
        self.delivered = False
        self.completed = False


class RecoveringLiveSource:
    """Supervisor que mantiene una fuente Live viva con recuperación de huecos.

    make_source(start): fábrica de sesión con start = datetime o el entero 0; la sesión expone
    replay() (async iterator de Candle), stop(), is_replay_complete y replay_completed_event.
    sleep(seg): corutina de espera inyectable.  now(): datetime "actual" inyectable.
    config: {'lookback': timedelta, 'recovery_delays_seconds': seq, 'terminal_after': timedelta}.
    """

    def __init__(self, make_source, sleep, now, config):
        self._make_source = make_source
        self._sleep = sleep
        self._now = now
        self._lookback: timedelta = config["lookback"]
        self._delays = list(config["recovery_delays_seconds"])
        self._terminal_after: timedelta = config["terminal_after"]

        self._phase = PHASE_BOOTSTRAPPING
        self._recovering_since: datetime | None = None
        self._last_delivered: datetime | None = None
        self._replay_completed_event = _make_event()

        # Sesión actual (para cierre determinista en el finally del generador externo).
        self._cur_session = None
        self._cur_agen = None
        self._cur_watcher: asyncio.Task | None = None

    # --- estado observable ---------------------------------------------------
    @property
    def phase(self) -> str:
        return self._phase

    @property
    def is_replay_complete(self) -> bool:
        """True solo cuando la fase viva ya observó su REPLAY_COMPLETED (fase == live).

        False en bootstrapping y durante una recuperación. Distinto de replay_completed_event,
        que es latcheado (se activa con el 1er REPLAY_COMPLETED y nunca se limpia)."""
        return self._phase == PHASE_LIVE

    @property
    def recovering_since(self) -> datetime | None:
        return self._recovering_since

    @property
    def replay_completed_event(self):
        return self._replay_completed_event

    # --- helpers -------------------------------------------------------------
    def _delay_for(self, attempt_index: int) -> float:
        if not self._delays:
            return 0.0
        if attempt_index < len(self._delays):
            return float(self._delays[attempt_index])
        return float(self._delays[-1])

    def _mark_live(self) -> None:
        """Frontera REPLAY_COMPLETED observada en la sesión actual → fase live."""
        self._replay_completed_event.set()
        self._phase = PHASE_LIVE
        self._recovering_since = None

    async def _watch_replay_completed(self, session) -> None:
        """Observa el evento de la sesión: marca live aunque no llegue ninguna vela posterior
        (mercado cerrado). Solo actúa si la sesión sigue siendo la actual."""
        await session.replay_completed_event.wait()
        if self._cur_session is session:
            self._mark_live()

    async def _cancel_watcher(self) -> None:
        """Cancela y espera el vigilante de la sesión actual (sin relanzar SU cancelación)."""
        watcher = self._cur_watcher
        self._cur_watcher = None
        if watcher is not None and not watcher.done():
            watcher.cancel()
            await asyncio.wait({watcher})

    async def _close_current(self) -> None:
        """Cierra la sesión actual: aclose() del generador interno + stop(), en ese orden (R3)."""
        await self._cancel_watcher()
        agen = self._cur_agen
        session = self._cur_session
        self._cur_agen = None
        self._cur_session = None
        if agen is not None:
            try:
                await agen.aclose()
            except Exception as exc:
                logger.debug("aclose de la sesión interna lanzó (ignorado): %r", exc)
        stop = getattr(session, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception as exc:
                logger.debug("stop() de la sesión lanzó (ignorado): %r", exc)

    async def _run_session(self, start, result: _SessionResult) -> AsyncIterator[Candle]:
        """Abre UNA sesión con make_source(start) y entrega sus velas (dedup estricto).

        Cierra la sesión ANTERIOR (aclose+stop) ANTES de abrir esta (R3). Latchea el evento y
        actualiza la fase al observar REPLAY_COMPLETED. Rellena `result.delivered/completed`.
        """
        # R3: cerrar la sesión previa antes de abrir la nueva.
        if self._cur_session is not None or self._cur_agen is not None:
            await self._close_current()

        session = self._make_source(start)
        agen = session.replay()
        self._cur_session = session
        self._cur_agen = agen
        self._cur_watcher = asyncio.create_task(self._watch_replay_completed(session))

        while True:
            try:
                candle = await agen.__anext__()
            except StopAsyncIteration:
                # Fin LIMPIO del stream.
                break
            except (GeneratorExit, KeyboardInterrupt, SystemExit):
                # Cierre/cancelación del consumidor o señales: propagar SIEMPRE (R2).
                raise
            except Exception as exc:
                # CORTE en vivo de la sesión (p.ej. Gateway timeout, ConnectionError). NO se lee el
                # texto del error: cualquier excepción del stream cuenta como fin de sesión y abre
                # la recuperación (la clasificación delivered/completed ya quedó registrada).
                logger.info("Sesión terminó por excepción (corte): %r", exc)
                break
            # Flanco de REPLAY_COMPLETED: las sesiones marcan is_replay_complete / el evento.
            if session.is_replay_complete and not result.completed:
                result.completed = True
                self._mark_live()
            # Dedup estricto: solo velas con timestamp > última entregada.
            if self._last_delivered is not None and candle.timestamp <= self._last_delivered:
                continue
            self._last_delivered = candle.timestamp
            result.delivered = True
            yield candle

        # El stream terminó: su vigilante ya no puede actuar (evita marcar live durante la espera
        # de recuperación con una sesión ya terminada).
        await self._cancel_watcher()
        # El stream terminó. Puede que REPLAY_COMPLETED llegara como último evento (sin vela
        # posterior): consúltalo por el estado de la sesión.
        if session.is_replay_complete and not result.completed:
            result.completed = True
            self._mark_live()

    def _enter_recovering(self) -> None:
        self._phase = PHASE_RECOVERING
        if self._recovering_since is None:
            self._recovering_since = self._now()

    # --- flujo principal -----------------------------------------------------
    async def replay(self) -> AsyncIterator[Candle]:
        """Generador externo: arranque + bucle de recuperación. Entrega `Candle`.

        R2: ante aclose()/cancelación del consumidor, el finally cierra la sesión actual
        (aclose + stop) y NO abre otra; la cancelación se propaga."""
        try:
            # ---------------- ARRANQUE ----------------
            start_primary = self._now() - self._lookback
            logger.info("Live arranque: primario start=%s", _fmt_start(start_primary))
            res = _SessionResult()
            async for c in self._run_session(start_primary, res):
                yield c

            if not res.completed and not res.delivered:
                # Primario terminó sin completar su replay Y sin entregar velas -> respaldo start=0.
                # Si ya entregó velas, el corte se trata como recuperación (R1).
                logger.info("Arranque sin completar replay; respaldo start=0")
                res = _SessionResult()
                async for c in self._run_session(0, res):
                    yield c

            if not res.completed and self._last_delivered is None:
                self._phase = PHASE_TERMINAL
                logger.critical("Arranque Live fallido (primario + respaldo); fase=terminal")
                return

            # Si la sesión viva terminó (su async for se agotó) tras completar, hay que recuperar.
            # ---------------- BUCLE DE RECUPERACIÓN ----------------
            while True:
                self._enter_recovering()
                attempt_index = 0
                recovered = False
                while True:
                    # R4: esperar -> evaluar plazo -> abrir intento.
                    delay = self._delay_for(attempt_index)
                    logger.info("Recuperación intento %d: espera %.0fs", attempt_index + 1, delay)
                    await self._sleep(delay)
                    if self._now() - self._recovering_since >= self._terminal_after:
                        self._phase = PHASE_TERMINAL
                        logger.critical(
                            "Plazo de recuperación agotado (%.0fs); fase=terminal",
                            self._terminal_after.total_seconds(),
                        )
                        return
                    attempt_index += 1

                    # Primario del intento: start = última entregada + 1 min; sin ancla → start=0.
                    start_rec = 0 if self._last_delivered is None else self._last_delivered + _ONE_MINUTE
                    logger.info("Recuperación: primario start=%s", _fmt_start(start_rec))
                    res = _SessionResult()
                    async for c in self._run_session(start_rec, res):
                        yield c
                    if res.completed:
                        recovered = True
                        break
                    if not res.delivered and start_rec != 0:
                        # Primario no entregó vela -> UN respaldo start=0 en este mismo intento
                        # (si el primario ya era start=0, el intento simplemente falló).
                        logger.info("Recuperación: respaldo start=0")
                        res = _SessionResult()
                        async for c in self._run_session(0, res):
                            yield c
                        if res.completed:
                            recovered = True
                            break
                    # No completó: siguiente intento (otra espera).

                if recovered:
                    # Volvimos a live. Si la nueva sesión viva luego cae (su async for se agotó),
                    # el while exterior repite y entra en recovering otra vez.
                    continue
        finally:
            # R2 / R3: cierre del consumidor o fin del flujo -> cerrar la sesión actual.
            if self._cur_session is not None or self._cur_agen is not None:
                await self._close_current()


def _make_event():
    """asyncio.Event sin requerir un loop en construcción (evita DeprecationWarning)."""
    import asyncio
    return asyncio.Event()
