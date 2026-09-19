"""Adaptador `LiveCandleSource`: expone una `LiveSubscription` como `CandleSource`.

Proposito (prerequisito estricto del primer grafico Live): el `_processing_loop` del backend
consume una fuente que cumple el contrato `CandleSource` de T1.1, es decir, que expone un
metodo `replay() -> AsyncIterator[Candle]` (asi lo hace `SimulationReplay` hoy). La pieza
`LiveSubscription` (T1.5) produce velas Live pero mediante `subscribe_live()`, un nombre de
metodo DISTINTO. Sin un adaptador, el loop no puede consumir la fuente Live.

Este modulo NO reimplementa logica de suscripcion ni de mapeo: es un adaptador delgado que
DELEGA en `LiveSubscription.subscribe_live()` y solo renombra la forma del flujo a `replay()`
para satisfacer, por typing estructural, el `Protocol` `CandleSource`. Asi la seleccion de
fuente en el backend (Wave 6, `app.py`) podra tratar Simulation y Live de forma uniforme, sin
duplicar el loop ni conocer el tipo concreto.

Fronteras:
- NO edita componentes protegidos (`app.py`, connector, pusher, dashboard, SignalEngine).
- NO conecta Databento por si mismo: solo envuelve una `LiveSubscription` ya construida.
- NO decide seleccion de fuente (eso es Wave 6 en `app.py`, con aprobacion).
- El cableado fisico Live↔loop es Wave 6; este adaptador es la pieza nueva/aislada que lo
  hara posible sin tocar protegidos.

Terminos:
- `CandleSource` = Protocol de T1.1 (typing estructural): una fuente cumple el contrato si
  tiene `replay()` que devuelve `AsyncIterator[Candle]`.
- adaptador = patron que traduce una interfaz existente (`subscribe_live`) a la esperada
  (`replay`) sin cambiar el objeto adaptado.
"""

from __future__ import annotations

from src.live.candle_source import CandleStream
from src.live.live_subscription import LiveSubscription


class LiveCandleSource:
    """Envuelve una `LiveSubscription` y la expone con la forma de `CandleSource`.

    Cumple el `Protocol` `CandleSource` de T1.1 por typing estructural: expone `replay()` que
    devuelve `AsyncIterator[Candle]`, delegando en `LiveSubscription.subscribe_live()`.
    """

    def __init__(self, subscription: LiveSubscription) -> None:
        # Se inyecta una LiveSubscription ya construida (no se crea aqui: mantiene el
        # adaptador libre de credenciales y de la decision de conexion).
        self._subscription = subscription

    def replay(self) -> CandleStream:
        """Devuelve el flujo asincrono de `Candle` de la suscripcion Live.

        Delega directamente en `subscribe_live()` (que es un async generator). No se
        reimplementa nada: solo se expone bajo el nombre `replay()` que el loop espera.
        """
        return self._subscription.subscribe_live()

    def stop(self) -> None:
        """Detiene la fuente. Compatibilidad con el shutdown existente del backend.

        El backend, al apagar, llama `source.stop()` (igual que hace con `SimulationReplay`). La
        `LiveSubscription` no expone un `stop()` sincrono: el cierre real del stream Live ocurre
        cuando el `_processing_loop` (async generator) se cancela y se ejecuta el `finally` de
        `subscribe_live()` (que marca DISCONNECTED). Por eso este `stop()` es un no-op seguro: existe
        para no romper el contrato de shutdown del backend sin duplicar ni forzar logica de cierre.
        """
        # No-op: el cierre efectivo lo realiza la cancelacion del pipeline task (finally de
        # subscribe_live()). Se mantiene el metodo por compatibilidad con el shutdown del backend.
        return None

    @property
    def is_connected(self) -> bool:
        """Estado de conexion observable, reexpuesto desde la `LiveSubscription`."""
        return self._subscription.is_connected

    @property
    def is_replay_complete(self) -> bool:
        """True una vez que la `LiveSubscription` observo SystemCode.REPLAY_COMPLETED.

        LIVE_REPLAY_BOOTSTRAP_SLICE: reexpone la frontera REPLAY->LIVE desde la suscripcion, sin
        cambiar el contrato `CandleSource` (que solo entrega `Candle` via `replay()`).
        """
        return self._subscription.is_replay_complete

    @property
    def replay_completed_event(self):
        """`asyncio.Event` de la suscripcion que se activa al recibir REPLAY_COMPLETED."""
        return self._subscription.replay_completed_event

    @property
    def subscription(self) -> LiveSubscription:
        """Acceso a la `LiveSubscription` envuelta (p.ej. para `last_candle_time`/reconnect)."""
        return self._subscription
