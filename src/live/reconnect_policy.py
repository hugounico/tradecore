"""T1.4 — Reconnect + exponential backoff (abstraccion aislada).

Gobierna CUANDO reintentar una operacion de reconexion Live, con backoff parametrizable y
manejo de error fatal (sin retry). Es una abstraccion PURA y AISLADA:

- NO crea `databento.Live`, NO usa API key, NO abre sockets, NO conecta a Databento;
- NO llama subscribe()/re-subscribe, NO procesa OHLCV, NO hace gap recovery;
- NO toca el connector protegido ni ningun componente protegido.

La operacion concreta de reconexion se INYECTA (callable async). Los parametros (numero de
intentos, delays, clasificacion fatal, mecanismo de espera) se inyectan/configuran; NO se
hardcodean valores rigidos en el algoritmo. La configuracion MVP aprobada (G0.1/G0.2, Design
seccion 4.1) es `max_attempts=3`, `delays=[1, 2, 4]`, fatal -> 0 reintentos, pero esos valores
se pasan como configuracion, no como constantes inevitables.

Reutiliza la maquina de estados de T1.3 (`ConnectionStateMachine`): RECONNECTING al iniciar la
recuperacion, CONNECTED si un intento tiene exito, DISCONNECTED si se agotan los intentos o si
la condicion es fatal.

RESET TRAS ESTABILIDAD: el mecanismo de reset del contador existe y es invocable
(`record_stable_connection()`), pero el VALOR concreto del intervalo de estabilidad NO esta
definido en G0.1/Design (RESET_INTERVAL_VALUE_DEFINED = NO). Por eso NO se inventa aqui: el
`stability_window_s` es un parametro opcional (default None) y la decision de "cuando la conexion
es estable" pertenece a quien llama; T1.4 solo provee el mecanismo de reseteo del contador.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from src.live.connection_state import ConnectionState, ConnectionStateMachine


class ReconnectOutcome(Enum):
    """Resultado observable de un ciclo de reconexion gobernado por la policy."""

    RECONNECTED = "RECONNECTED"      # un intento tuvo exito -> CONNECTED
    EXHAUSTED = "EXHAUSTED"          # se agotaron los intentos -> DISCONNECTED
    FATAL = "FATAL"                  # condicion fatal -> DISCONNECTED, 0 reintentos


class FatalReconnectError(Exception):
    """Senala una condicion FATAL de reconexion (p.ej. AUTH_FAILED) -> NO reintentar.

    Frontera de desacople: la operacion de reconexion inyectada (o la capa Databento futura)
    puede lanzar esta excepcion para indicar "no reintentar", SIN que T1.4 tenga que conocer
    `databento_dbn.ErrorMsg` ni la tabla de ErrorCode del SDK. Alternativamente se puede pasar
    un clasificador `is_fatal` (ver `ReconnectPolicy`).
    """


# Tipos inyectables (aliases para claridad):
# - operacion de reconexion: async, devuelve True si conecto, False si fallo (no fatal).
ReconnectOperation = Callable[[], Awaitable[bool]]
# - espera inyectable (async): en produccion puede ser asyncio.sleep; en tests, un fake.
SleepFn = Callable[[float], Awaitable[None]]
# - clasificador opcional de excepciones no-FatalReconnectError como fatal/no-fatal.
FatalClassifier = Callable[[BaseException], bool]


@dataclass
class ReconnectConfig:
    """Configuracion inyectable de la policy (NO hardcodeada en el algoritmo).

    - `max_attempts`: numero maximo de intentos de reconexion.
    - `delays_seconds`: espera (en segundos) ANTES de cada intento; se indexa por intento.
      Para la config MVP: [1, 2, 4] (esperar 1s antes del intento 1, 2s antes del 2, 4s antes
      del 3). Si hay menos delays que intentos, se reutiliza el ultimo delay.
    - `stability_window_s`: intervalo de estabilidad para el reset del contador. Default None:
      NO se inventa un valor (RESET_INTERVAL_VALUE_DEFINED = NO). Es solo informativo/observable;
      el reset efectivo lo dispara `record_stable_connection()` por decision de quien llama.
    """

    max_attempts: int
    delays_seconds: Sequence[float]
    stability_window_s: float | None = None

    def delay_for_attempt(self, attempt_index: int) -> float:
        """Delay (s) antes del intento `attempt_index` (0-based).

        Si hay menos delays que intentos, reutiliza el ultimo (evita IndexError sin hardcodear
        una secuencia fija dentro del algoritmo).
        """
        if not self.delays_seconds:
            return 0.0
        if attempt_index < len(self.delays_seconds):
            return float(self.delays_seconds[attempt_index])
        return float(self.delays_seconds[-1])


# Configuracion MVP aprobada (G0.1/Design 4.1). Se expone como CONSTANTE DE CONFIGURACION
# (no como logica): quien construye la policy la pasa explicitamente; el algoritmo no la asume.
MVP_RECONNECT_CONFIG = ReconnectConfig(max_attempts=3, delays_seconds=(1.0, 2.0, 4.0))


@dataclass
class ReconnectPolicy:
    """Politica de reconexion TradeCore (NO usa el reconnect automatico del SDK).

    Gobierna el ciclo: RECONNECTING -> [esperar delay -> intento]* -> CONNECTED | DISCONNECTED.

    Dependencias inyectadas:
    - `config`: `ReconnectConfig` (max_attempts, delays, stability_window_s).
    - `sleep`: funcion de espera async inyectable (asyncio.sleep en prod; fake en tests).
    - `state_machine`: `ConnectionStateMachine` de T1.3 (se reutiliza; no se duplica estado).
    - `is_fatal`: clasificador opcional de excepciones (ademas de `FatalReconnectError`).
    """

    config: ReconnectConfig
    sleep: SleepFn
    state_machine: ConnectionStateMachine = field(default_factory=ConnectionStateMachine)
    is_fatal: FatalClassifier | None = None

    # Contador de intentos consumidos desde el ultimo reset (observable para tests).
    _attempts_made: int = field(default=0, init=False)

    @property
    def attempts_made(self) -> int:
        """Numero de intentos consumidos desde el ultimo reset (observable)."""
        return self._attempts_made

    @property
    def state(self) -> ConnectionState:
        """Estado actual de la conexion (delegado en la maquina de T1.3)."""
        return self.state_machine.state

    def record_stable_connection(self) -> None:
        """Reset del contador de backoff tras estabilidad.

        MECANISMO de reset (T1.4 lo provee). El VALOR del intervalo de estabilidad NO esta
        definido (RESET_INTERVAL_VALUE_DEFINED = NO) y NO se inventa: quien llama decide cuando
        la conexion ha sido estable y entonces invoca este metodo para volver el contador a 0.
        """
        self._attempts_made = 0

    def _classify_fatal(self, exc: BaseException) -> bool:
        """True si la excepcion debe tratarse como fatal (sin reintentar)."""
        if isinstance(exc, FatalReconnectError):
            return True
        if self.is_fatal is not None:
            return bool(self.is_fatal(exc))
        return False

    async def run(self, reconnect_operation: ReconnectOperation) -> ReconnectOutcome:
        """Ejecuta el ciclo de reconexion gobernado por la policy.

        `reconnect_operation`: callable async inyectado que INTENTA reconectar; devuelve True si
        conecto, False si fallo (no fatal). Puede lanzar `FatalReconnectError` (o una excepcion
        que `is_fatal` clasifique como fatal) para indicar "no reintentar".

        Flujo (config MVP: 3 intentos, delays 1/2/4):
        - marca RECONNECTING;
        - para cada intento i (0..max_attempts-1): espera `delays[i]`, luego intenta;
          - exito -> CONNECTED, devuelve RECONNECTED;
          - fatal -> DISCONNECTED, 0 reintentos adicionales, devuelve FATAL;
          - fallo no fatal -> siguiente intento;
        - si se agotan los intentos -> DISCONNECTED, devuelve EXHAUSTED.

        No existe intento N+1. No hay retry infinito. No hay reconnect automatico del SDK.
        """
        # Iniciar recuperacion: estado observable RECONNECTING.
        self.state_machine.mark_reconnecting()
        self._attempts_made = 0

        for attempt_index in range(self.config.max_attempts):
            # Backoff: esperar ANTES del intento (delay parametrizado, no hardcodeado).
            delay = self.config.delay_for_attempt(attempt_index)
            if delay > 0:
                await self.sleep(delay)

            self._attempts_made += 1
            try:
                connected = await reconnect_operation()
            except BaseException as exc:  # noqa: BLE001 - se re-clasifica abajo
                if self._classify_fatal(exc):
                    # Fatal: cerrar sin mas reintentos.
                    self.state_machine.mark_disconnected()
                    return ReconnectOutcome.FATAL
                # No fatal (excepcion transitoria): contar como fallo y seguir intentando.
                connected = False

            if connected:
                self.state_machine.mark_connected()
                return ReconnectOutcome.RECONNECTED
            # fallo no fatal -> siguiente iteracion (si quedan intentos)

        # Agotados todos los intentos sin exito.
        self.state_machine.mark_disconnected()
        return ReconnectOutcome.EXHAUSTED
