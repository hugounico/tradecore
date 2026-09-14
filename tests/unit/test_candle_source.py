"""Tests de T1.1 — contrato/interfaz de fuente de velas (`CandleSource` / `CandleStream`).

Verifica que:
1. El contrato tipado existe y es importable.
2. `SimulationReplay` (Fase A, no modificado) SATISFACE estructuralmente el contrato.
3. El flujo producido es realmente un `AsyncIterator[Candle]` que entrega objetos `Candle`.

No se prueba Live real (diferido a Wave 6). No se modifica ningun componente protegido.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timezone

from src.live.candle_source import CandleSource, CandleStream
from src.pipeline.simulation_replay import SimulationReplay
from src.schemas.candle import Candle


def _make_candle(minute: int) -> Candle:
    """Crea una Candle sintetica anclada a la apertura de un minuto UTC dado."""
    return Candle(
        timestamp=datetime(2026, 9, 13, 14, minute, 0, tzinfo=timezone.utc),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10,
    )


def test_contract_symbols_exist() -> None:
    """El contrato y el alias de flujo estan definidos e importables."""
    # `CandleStream` es un alias de tipo; `CandleSource` es el Protocol del contrato.
    assert CandleStream is not None
    assert CandleSource is not None


def test_simulation_replay_satisfies_contract_structurally() -> None:
    """`SimulationReplay` cumple el contrato `CandleSource` por forma (runtime_checkable)."""
    source = SimulationReplay(candles=[_make_candle(31)], interval=0.0)
    # Verificacion estructural: tiene el metodo `replay` esperado por el Protocol.
    assert isinstance(source, CandleSource)
    assert hasattr(source, "replay")


async def test_replay_yields_async_iterator_of_candles() -> None:
    """El flujo de `replay()` es un AsyncIterator que entrega objetos Candle."""
    candles_in = [_make_candle(31), _make_candle(32)]
    source: CandleSource = SimulationReplay(candles=candles_in, interval=0.0)

    stream = source.replay()
    # El objeto devuelto debe comportarse como iterador asincrono.
    assert isinstance(stream, AsyncIterator)

    collected: list[Candle] = []
    async for candle in stream:  # se recorre con `async for` (contrato AsyncIterator[Candle])
        assert isinstance(candle, Candle)
        collected.append(candle)

    # Se preservan cantidad y orden de las velas entregadas.
    assert collected == candles_in
