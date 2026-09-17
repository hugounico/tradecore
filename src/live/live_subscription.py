"""T1.5 — `LiveSubscription`: logica de suscripcion Live como modulo NUEVO y AISLADO.

Este modulo COMPONE las piezas ya construidas en Wave 1 (T1.2-T1.4) para producir
velas (`Candle`) cerradas a partir de un cliente Live de Databento (`db.Live`), SIN
editar el conector protegido (`src/connectors/databento_connector.py`). La union fisica
con el conector es Wave 6 (INTEGRATION_DEFERRED_TO_WAVE_6 = YES); aqui la logica se
prueba con `db.Live` MOCKEADO (no requiere plan Standard ni conexion real).

Que hace (alcance T1.5):
- inicializa el cliente Live, se suscribe (dataset/schema/symbol/stype_in), itera el
  stream asincrono, mapea cada record a `Candle` (reusando T1.2, sin duplicar la
  conversion de precio/timestamp) y cierra;
- descarta velas cuyo `timestamp` sea <= la ultima vela conocida (dedup contra la
  ultima vela historica): solo se emite una vela si es estrictamente mas nueva;
- expone estado de conexion observable (reusando la maquina de estados de T1.3);
- opcionalmente gobierna la reconexion reusando la policy de T1.4 (inyectada), sin
  duplicar su logica de backoff.

Que NO hace (fronteras):
- NO edita el conector protegido ni ningun componente Nivel 1/2;
- NO decide "finalidad" del protocolo EOI (eso es investigacion aparte, ya cerrada
  empiricamente para la configuracion del probe);
- NO realiza la integracion productiva (seleccion de fuente en `app.py`) — eso es W6;
- NO implementa warm-up/sync/gap (eso es Wave 2).

Terminos:
- `db.Live` = cliente Live del SDK oficial de Databento; es un ITERADOR ASINCRONO de
  records (`async for record in client:`), NO un WebSocket. Se le llama `subscribe(...)`
  antes de iterar. El SDK usa `databento` (paquete instalado en el entorno).
- `Candle` = una vela OHLCV de 1 minuto ya cerrada (timestamp = apertura del minuto).
- dedup = descartar duplicados/velas viejas comparando su timestamp con la ultima
  conocida (misma politica que la ruta historica al reanudar).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime

import databento as db  # cliente oficial; se MOCKEA en tests (patch de este simbolo)

from src.live.connection_state import ConnectionStateMachine
from src.live.live_candle_mapper import live_record_to_candle
from src.live.reconnect_policy import ReconnectOutcome, ReconnectPolicy
from src.schemas.candle import Candle

# Logger estandar del modulo (mismo mecanismo que el resto del backend; sin dependencias nuevas).
logger = logging.getLogger(__name__)

# Configuracion de suscripcion (misma decision cerrada de Fase A: databento-architecture.md).
# Se exponen como constantes de configuracion, no como logica embebida.
DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
SYMBOL = "NQ.c.0"
STYPE_IN = "continuous"


class LiveSubscription:
    """Suscripcion Live aislada que produce `Candle` cerradas desde `db.Live`.

    Composicion (no duplicacion):
    - T1.2 `live_record_to_candle`: mapea cada record a `Candle` (o `None`).
    - T1.3 `ConnectionStateMachine`: estado observable CONNECTED/DISCONNECTED/RECONNECTING.
    - T1.4 `ReconnectPolicy` (opcional, inyectada): gobierna la reconexion con backoff.

    Dedup: se mantiene `last_candle_time` (la ultima vela conocida, que en la integracion
    W6 vendra de la carga historica). Solo se emite una vela si su `timestamp` es
    ESTRICTAMENTE mayor que `last_candle_time`; `<=` se descarta (incluye la igualdad).
    """

    def __init__(
        self,
        api_key: str,
        *,
        dataset: str = DATASET,
        schema: str = SCHEMA,
        symbol: str = SYMBOL,
        stype_in: str = STYPE_IN,
        last_candle_time: datetime | None = None,
        reconnect_policy: ReconnectPolicy | None = None,
    ) -> None:
        # Credencial y parametros de suscripcion (no se imprime ni persiste la API key).
        self._api_key = api_key
        self._dataset = dataset
        self._schema = schema
        self._symbol = symbol
        self._stype_in = stype_in
        # Ultima vela conocida para el dedup (None = aun no hay ninguna).
        self._last_candle_time: datetime | None = last_candle_time
        # Estado de conexion observable (reuso de T1.3, sin reinventar estado).
        self._state_machine = ConnectionStateMachine()
        # Policy de reconexion opcional (reuso de T1.4). No se usa en el flujo feliz;
        # queda disponible para gobernar reconexion sin duplicar backoff.
        self._reconnect_policy = reconnect_policy

    # ---- estado observable ----
    @property
    def is_connected(self) -> bool:
        """True unicamente mientras el stream Live esta activo (estado CONNECTED)."""
        return self._state_machine.is_connected

    @property
    def last_candle_time(self) -> datetime | None:
        """Timestamp (apertura) de la ultima vela emitida/conocida, o None."""
        return self._last_candle_time

    # ---- creacion del cliente (aislada para poder mockearse en tests) ----
    def _create_client(self) -> object:
        """Crea el cliente `db.Live`. Aislado en un metodo para que el test parchee
        `src.live.live_subscription.db.Live` sin tocar el conector protegido.

        ReconnectPolicy = NONE a nivel de SDK: la politica de reconexion de TradeCore
        (T1.4) NO delega en el reconnect automatico del SDK.
        """
        return db.Live(key=self._api_key)

    # ---- flujo principal: generador asincrono de velas cerradas ----
    async def subscribe_live(self) -> AsyncIterator[Candle]:
        """Itera el stream Live y produce `Candle` cerradas, en orden y sin duplicados.

        Contrato (identico al comportamiento spec-ahead de LiveSubscription):
        - crea el cliente y se suscribe (dataset/schema/symbol/stype_in);
        - marca CONNECTED mientras el stream esta activo;
        - por cada record: lo mapea a `Candle` (T1.2); si es valido y ESTRICTAMENTE mas
          nuevo que `last_candle_time`, actualiza `last_candle_time` y lo emite (`yield`);
        - al terminar el stream (o ante error), marca DISCONNECTED en `finally`.

        Es un iterador asincrono: se consume con `async for candle in sub.subscribe_live()`.
        """
        # Se declara ANTES del try para que el finally SIEMPRE pueda cerrarlo, incluso si
        # `subscribe(...)` falla. Un solo cliente por invocacion -> un solo cierre explicito.
        client = None
        try:
            client = self._create_client()
            # `subscribe(...)` en el SDK real registra la suscripcion antes de iterar.
            client.subscribe(
                dataset=self._dataset,
                schema=self._schema,
                symbols=self._symbol,
                stype_in=self._stype_in,
            )
            # El stream esta activo: estado observable CONNECTED.
            self._state_machine.mark_connected()
            async for record in client:
                # T1.2: mapea el record a Candle (o None si no es convertible).
                candle = live_record_to_candle(record)
                if candle is None:
                    # Record no convertible: se ignora con la misma politica tolerante.
                    continue
                # Dedup / orden: emitir solo si es estrictamente mas nuevo que lo conocido.
                if self._last_candle_time is not None and candle.timestamp <= self._last_candle_time:
                    continue
                self._last_candle_time = candle.timestamp
                yield candle
        finally:
            # El stream termino (fin normal, cancelacion o excepcion): ya no estamos conectados.
            self._state_machine.mark_disconnected()
            # Cierre EXPLICITO del cliente Live (deuda de ciclo de vida): antes el cierre real
            # solo ocurria via `db.Live.__del__` en el GC. Ahora se cierra de forma deterministica
            # exactamente una vez por invocacion de subscribe_live(), en el dueno del ciclo de vida.
            if client is not None:
                self._close_client(client)

    def _close_client(self, client: object) -> None:
        """Cierre explicito y seguro del cliente Live creado por `subscribe_live()`.

        Usa `stop()` (cierre GRACIOSO del SDK: "finish processing received records"), que es
        NO bloqueante y ademas se auto-protege (`if not is_connected(): return`). Se invoca UNA
        sola vez por ciclo de vida del cliente, desde el `finally` de `subscribe_live()`.

        Regla de propagacion: este metodo captura solo `Exception` proveniente del propio cierre
        (p.ej. `ValueError` del SDK si el cliente nunca llego a conectar) y la registra con el
        logger existente. NO captura `BaseException`, de modo que un `CancelledError` (que es
        `BaseException`, no `Exception`) NUNCA queda enmascarado; tampoco enmascara la excepcion
        primaria del stream/mapper, porque el `finally` no la suprime.
        """
        stop = getattr(client, "stop", None)
        if stop is None:
            # Cliente sin `stop()` (p.ej. doble de test minimalista): nada que cerrar.
            return
        try:
            stop()
        except Exception as exc:  # NO BaseException: preserva CancelledError y la excepcion primaria
            logger.warning("Error al cerrar el cliente Live (se ignora, no enmascara): %s", exc)

    # ---- reconexion (reuso de T1.4, sin duplicar backoff) ----
    async def reconnect(self) -> ReconnectOutcome:
        """Gobierna la reconexion usando la `ReconnectPolicy` inyectada (T1.4).

        La operacion de reconexion recrea el cliente y re-suscribe; devuelve True si
        conecto. Requiere una policy inyectada (no se inventa una por defecto aqui para
        no fijar valores de backoff no aprobados en este modulo).
        """
        if self._reconnect_policy is None:
            raise RuntimeError(
                "reconnect() requiere una ReconnectPolicy inyectada (T1.4); "
                "no se define una por defecto en LiveSubscription."
            )

        async def _operation() -> bool:
            client = self._create_client()
            client.subscribe(
                dataset=self._dataset,
                schema=self._schema,
                symbols=self._symbol,
                stype_in=self._stype_in,
            )
            return True

        outcome = await self._reconnect_policy.run(_operation)
        # Sincronizar el estado observable de ESTA suscripcion con el resultado de la policy
        # (T1.4 gobierna su propia maquina; aqui reflejamos el resultado en la nuestra, sin
        # duplicar la logica de backoff).
        if outcome is ReconnectOutcome.RECONNECTED:
            self._state_machine.mark_connected()
        else:
            self._state_machine.mark_disconnected()
        return outcome
