"""T1.1 — Contrato/interfaz de fuente de velas (candle source).

Define la interfaz asincrona COMUN que las fuentes de velas de TradeCore comparten:
tanto la fuente de simulacion (Fase A, `SimulationReplay.replay()`) como la futura fuente
Live (Wave 6) producen `Candle` mediante un `AsyncIterator[Candle]`.

Por que un `Protocol` (typing estructural) y no una clase base abstracta (ABC):
- `SimulationReplay` ya EXISTE y ya expone `async def replay(self) -> AsyncIterator[Candle]`.
  Es un componente de Fase A (protegido / no se edita). Un `Protocol` permite declarar el
  contrato SIN obligar a `SimulationReplay` a heredar de nada — se verifica por forma
  (structural typing / "duck typing" tipado), no por herencia. Asi no tocamos protegidos.
- `Protocol` = mecanismo de `typing` (PEP 544): una clase cumple el contrato con solo tener
  los metodos correctos; no requiere declararlo explicitamente.

Alcance (T1.1): SOLO el contrato tipado. NO implementa `subscribe_live()` real, NO conecta
Databento, NO hay reconnect/warm-up/sincronizacion. La integracion Live real es Wave 6.
INTEGRATION_DEFERRED_TO_WAVE_6 = YES.
"""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from src.schemas.candle import Candle

# Alias de tipo que nombra el contrato de flujo de velas de forma explicita y reutilizable.
# `AsyncIterator[Candle]` = un iterador asincrono que va "entregando" (yield) objetos Candle
# uno a uno; se recorre con `async for candle in fuente: ...`.
CandleStream = AsyncIterator[Candle]


@runtime_checkable
class CandleSource(Protocol):
    """Contrato comun de una fuente de velas.

    Cualquier fuente (simulacion o Live) cumple este contrato si expone un metodo capaz de
    producir un flujo asincrono de `Candle`. Se marca `@runtime_checkable` para poder usar
    `isinstance(obj, CandleSource)` en tests como verificacion estructural minima.

    Nota de conformidad: `src.pipeline.simulation_replay.SimulationReplay` YA satisface este
    contrato — su metodo `replay()` esta anotado como `AsyncIterator[Candle]`. Esa
    conformidad se documenta y se prueba en los tests de T1.1 (sin modificar SimulationReplay).
    """

    def replay(self) -> CandleStream:
        """Devuelve un flujo asincrono de `Candle` (`AsyncIterator[Candle]`).

        Las implementaciones concretas (p.ej. simulacion) definen COMO se generan las velas y
        con que cadencia; el contrato solo fija la FORMA del flujo, no su temporizacion.
        """
        ...
