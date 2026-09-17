"""Tests del adaptador `LiveCandleSource` (T1.5 → CandleSource), 100% offline.

Verifican que el adaptador:
- satisface estructuralmente el `Protocol` `CandleSource` (tiene `replay()`);
- `replay()` produce las mismas `Candle` que `LiveSubscription.subscribe_live()`, delegando
  sin reimplementar logica;
- reexpone `is_connected`.

`db.Live` se mockea (patch de `src.live.live_subscription.db.Live`), igual que en los tests de
T1.5. No hay red, no hay conexion real, no se tocan componentes protegidos.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.live.candle_source import CandleSource
from src.live.live_candle_source import LiveCandleSource
from src.live.live_subscription import LiveSubscription
from src.schemas.candle import Candle

TS_1 = 1700000000_000000000
TS_2 = 1700000060_000000000
PRICE_OPEN = 21500_000000000
PRICE_HIGH = 21510_000000000
PRICE_LOW = 21490_000000000
PRICE_CLOSE = 21505_000000000


def _make_mock_record(ts_event_ns, o, h, l, c, v) -> MagicMock:
    record = MagicMock()
    record.ts_event = ts_event_ns
    record.open = o
    record.high = h
    record.low = l
    record.close = c
    record.volume = v
    return record


def _patch_live(records):
    recs = list(records or [])

    async def _aiter(*args, **kwargs):
        for rec in recs:
            yield rec

    return patch("src.live.live_subscription.db.Live"), _aiter


def test_adapter_satisfies_candle_source_protocol():
    """El adaptador cumple estructuralmente el Protocol CandleSource (tiene replay())."""
    sub = LiveSubscription(api_key="test-key-123")
    source = LiveCandleSource(sub)
    assert isinstance(source, CandleSource)
    assert hasattr(source, "replay")


async def test_replay_yields_same_candles_as_subscribe_live():
    """replay() produce las mismas Candle que subscribe_live() (delegacion, sin duplicar)."""
    sub = LiveSubscription(api_key="test-key-123")
    source = LiveCandleSource(sub)

    r1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1500)
    r2 = _make_mock_record(TS_2, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1600)

    cm, aiter_fn = _patch_live([r1, r2])
    with cm as mock_live_class:
        inst = MagicMock()
        mock_live_class.return_value = inst
        inst.subscribe = MagicMock()
        inst.__aiter__ = aiter_fn

        candles = []
        async for candle in source.replay():
            candles.append(candle)

    assert len(candles) == 2
    assert all(isinstance(c, Candle) for c in candles)
    assert candles[0].volume == 1500
    assert candles[1].volume == 1600
    assert candles[0].timestamp == datetime.fromtimestamp(TS_1 / 1e9, tz=timezone.utc)


async def test_adapter_reexposes_is_connected():
    """is_connected se refleja desde la LiveSubscription envuelta."""
    sub = LiveSubscription(api_key="test-key-123")
    source = LiveCandleSource(sub)
    assert source.is_connected is False  # sin stream activo

    r1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1500)
    cm, aiter_fn = _patch_live([r1])
    with cm as mock_live_class:
        inst = MagicMock()
        mock_live_class.return_value = inst
        inst.subscribe = MagicMock()
        inst.__aiter__ = aiter_fn

        during = []
        async for _c in source.replay():
            during.append(source.is_connected)

    assert during == [True]
    assert source.is_connected is False  # tras terminar el stream
