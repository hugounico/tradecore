"""Tests del wiring minimo del slice: `DabentoConnector.live_candle_source()`, 100% offline.

Verifican SOLO la integracion minima del FIRST_FUNCTIONAL_LIVE_CHART_SLICE:
- el connector expone una fuente Live que cumple el contrato `CandleSource` (tiene `replay()`);
- la fuente usa la config Live inmutable del slice (dataset/symbol/stype_in del connector, schema
  ohlcv-1m) y ReconnectPolicy=NONE (sin reconexion real);
- NO se toca `load_historical` (Fase A intacta): sigue existiendo y con su firma;
- el connector NO adquiere el metodo `subscribe_live()` propio (eso es T6.1 completo, diferido);
- `stop()` de la fuente es un no-op seguro (compat con el shutdown del backend).

NO abre db.Live, NO red. No cubre la seleccion de fuente en app.py (esa es integracion protegida cuyo
extremo real se valida en el turno del primer intento Live).
"""

from __future__ import annotations

import inspect

from src.connectors.databento_connector import DabentoConnector
from src.live.candle_source import CandleSource
from src.live.live_candle_source import LiveCandleSource
from src.live.live_subscription import LiveSubscription


def _make_connector() -> DabentoConnector:
    return DabentoConnector(
        api_key="test-key-123",
        dataset="GLBX.MDP3",
        symbol="NQ.c.0",
        stype_in="continuous",
    )


def test_live_candle_source_returns_candle_source():
    """live_candle_source() devuelve un LiveCandleSource que cumple el Protocol CandleSource."""
    connector = _make_connector()
    source = connector.live_candle_source()
    assert isinstance(source, LiveCandleSource)
    assert isinstance(source, CandleSource)  # typing estructural: tiene replay()
    assert hasattr(source, "replay")


def test_live_candle_source_uses_immutable_slice_config():
    """La fuente usa la config Live inmutable del slice y ReconnectPolicy=NONE."""
    connector = _make_connector()
    source = connector.live_candle_source()
    sub = source.subscription
    assert isinstance(sub, LiveSubscription)
    # Config inmutable del slice (atributos privados de LiveSubscription).
    assert sub._dataset == "GLBX.MDP3"
    assert sub._schema == "ohlcv-1m"
    assert sub._symbol == "NQ.c.0"
    assert sub._stype_in == "continuous"
    # ReconnectPolicy = NONE: sin reconexion real en el slice.
    assert sub._reconnect_policy is None


def test_live_candle_source_passes_last_candle_time_seed():
    """Se puede sembrar last_candle_time para el dedup (o None por defecto)."""
    connector = _make_connector()
    assert connector.live_candle_source().subscription.last_candle_time is None

    from datetime import datetime, timezone
    seed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seeded = connector.live_candle_source(last_candle_time=seed)
    assert seeded.subscription.last_candle_time == seed


def test_stop_is_safe_noop():
    """stop() de la fuente Live es un no-op seguro (no lanza)."""
    connector = _make_connector()
    source = connector.live_candle_source()
    # No debe lanzar (compat con shutdown del backend).
    source.stop()


def test_load_historical_still_present_faseA_intact():
    """load_historical sigue existiendo y es asincrono (Fase A intacta)."""
    connector = _make_connector()
    assert hasattr(connector, "load_historical")
    assert inspect.iscoroutinefunction(connector.load_historical)


def test_connector_has_no_subscribe_live_yet():
    """El slice NO agrega subscribe_live() al connector (eso es T6.1 completo, diferido)."""
    connector = _make_connector()
    assert not hasattr(connector, "subscribe_live")


def test_live_candle_source_creates_no_reconnect_capability():
    """La fuente del slice no habilita reconnect: subscription.reconnect requiere policy inyectada."""
    import pytest

    connector = _make_connector()
    sub = connector.live_candle_source().subscription

    async def _run():
        # Sin ReconnectPolicy inyectada, reconnect() lanza (no hay reconexion real en el slice).
        with pytest.raises(RuntimeError):
            await sub.reconnect()

    import asyncio
    asyncio.run(_run())
