"""T3.1 — Abstraccion de persistencia (repository) independiente del motor.

Define el CONTRATO que el resto de TradeCore usa para persistir y recuperar:
- Capa 1 (Signal Context): el registro operativo/financiero de una senal Live.
- LiveSession: la sesion Live que agrupa senales, identificada por `live_session_id`.

El contrato NO conoce el motor concreto (PostgreSQL, memoria, etc.). Esto permite:
- probar con un doble en memoria (`InMemorySignalContextRepository` /
  `InMemoryLiveSessionRepository`) sin base de datos;
- diferir la decision de motor fisico (RDS vs contenedor), esquema y migraciones a Tasks
  posteriores (T3.2+) y a los Gates G0.9/G0.10.

Relacion con `SignalJournal` (componente existente, NO modificado): `SignalJournal` es un
journal append-only de `SignalRecord` para trazabilidad de Fase A. El repository de T3.1 es
un concepto DISTINTO (persistencia de Capa 1 / LiveSession de Live v1, con recuperacion por
id). Coexisten; T3.1 no toca ni reemplaza `SignalJournal`.

Alcance (T3.1): SOLO interfaces + doble en memoria para tests. NO hay tablas PostgreSQL,
NO hay migraciones, NO hay adapters reales, NO se implementa persist-before-publish (eso es
Wave 4). El modelo fisico exacto de Capa 1 / LiveSession se define en T3.2+; aqui los
identificadores se tratan de forma generica (str) para no anticipar el esquema fisico.

`Protocol` (typing estructural, PEP 544) se usa para el contrato: una implementacion cumple
si expone los metodos correctos, sin herencia obligatoria.
"""

from typing import Generic, Protocol, TypeVar, runtime_checkable

# TypeVar generico: el repository de Capa 1 guardara "un registro de Signal Context" cuyo
# tipo concreto se define en Tasks posteriores (T3.2+). Aqui es un parametro de tipo abierto
# para no acoplar el contrato a un modelo fisico todavia inexistente.
T = TypeVar("T")


@runtime_checkable
class SignalContextRepository(Protocol, Generic[T]):
    """Contrato de persistencia/recuperacion de registros de Capa 1 (Signal Context).

    `save` persiste un registro identificado por su `signal_id`. `get` lo recupera por id
    (o devuelve None si no existe). El contrato es minimo y deliberadamente agnostico del
    motor: no expone SQL, conexiones ni transacciones.
    """

    def save(self, signal_id: str, record: T) -> None:
        """Persiste `record` asociado a `signal_id` (referencia inmutable de la senal)."""
        ...

    def get(self, signal_id: str) -> T | None:
        """Recupera el registro de Capa 1 por `signal_id`; None si no existe."""
        ...


@runtime_checkable
class LiveSessionRepository(Protocol, Generic[T]):
    """Contrato de persistencia/recuperacion de `LiveSession` por `live_session_id`."""

    def save(self, live_session_id: str, session: T) -> None:
        """Persiste `session` asociado a `live_session_id`."""
        ...

    def get(self, live_session_id: str) -> T | None:
        """Recupera la LiveSession por `live_session_id`; None si no existe."""
        ...


class InMemorySignalContextRepository(Generic[T]):
    """Doble en memoria del contrato `SignalContextRepository` para tests/desarrollo.

    Guarda los registros en un diccionario. Sin persistencia a disco: se pierde al reiniciar
    el proceso. NO es un motor de produccion; existe para desbloquear tests del contrato sin
    fijar todavia el motor fisico.
    """

    def __init__(self) -> None:
        # Diccionario interno signal_id -> registro de Capa 1.
        self._store: dict[str, T] = {}

    def save(self, signal_id: str, record: T) -> None:
        # Idempotente por clave: guardar sobrescribe el registro previo del mismo signal_id.
        self._store[signal_id] = record

    def get(self, signal_id: str) -> T | None:
        # `.get` del dict devuelve None si la clave no existe (sin lanzar excepcion).
        return self._store.get(signal_id)


class InMemoryLiveSessionRepository(Generic[T]):
    """Doble en memoria del contrato `LiveSessionRepository` para tests/desarrollo."""

    def __init__(self) -> None:
        # Diccionario interno live_session_id -> objeto de sesion.
        self._store: dict[str, T] = {}

    def save(self, live_session_id: str, session: T) -> None:
        self._store[live_session_id] = session

    def get(self, live_session_id: str) -> T | None:
        return self._store.get(live_session_id)
