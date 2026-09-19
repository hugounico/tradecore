"""LIVE_REPLAY_BOOTSTRAP_SLICE — tests unit offline (db.Live MOCKEADO, sin red).

Cubre el slice de bootstrap por intraday replay:
- `LiveSubscription` acepta un `replay_start` opcional y lo reenvia a `client.subscribe(start=...)`.
- `LiveSubscription` detecta `SystemCode.REPLAY_COMPLETED` (un `SystemMsg`, NO un Candle) y lo
  expone por un mecanismo separado (`is_replay_complete` / `replay_completed_event`), sin cambiar
  el tipo de salida de `subscribe_live()` (sigue siendo `AsyncIterator[Candle]`).
- La supresion de senales durante replay es responsabilidad del consumidor (`_processing_loop`
  con un predicado inyectable); se prueba aqui a nivel de `LiveSubscription` (que la frontera
  REPLAY_COMPLETED se comunica) y en test_app_live_bootstrap el cableado del loop.

REPLAY_COMPLETED es la UNICA autoridad de frontera. NO se usa conteo de velas como disparador.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from databento_dbn import SystemCode, SystemMsg

from src.live.live_subscription import LiveSubscription
from src.schemas.candle import Candle

TS_1 = 1700000000_000000000
TS_2 = 1700000060_000000000
TS_3 = 1700000120_000000000

PRICE_OPEN = 21500_000000000
PRICE_HIGH = 21510_000000000
PRICE_LOW = 21490_000000000
PRICE_CLOSE = 21505_000000000
VOLUME = 1500


def _ohlcv(ts_event_ns, volume=VOLUME) -> MagicMock:
    rec = MagicMock()
    rec.ts_event = ts_event_ns
    rec.open = PRICE_OPEN
    rec.high = PRICE_HIGH
    rec.low = PRICE_LOW
    rec.close = PRICE_CLOSE
    rec.volume = volume
    return rec


def _replay_completed_msg() -> SystemMsg:
    """Real SystemMsg with REPLAY_COMPLETED code (constructible offline)."""
    return SystemMsg(ts_event=TS_2, msg="Finished ohlcv-1m replay", code=SystemCode.REPLAY_COMPLETED)


def _patch_live(records):
    recs = list(records or [])

    async def _aiter(*args, **kwargs):
        for rec in recs:
            yield rec

    return patch("src.live.live_subscription.db.Live"), _aiter


def _make_live_instance(aiter_fn):
    inst = MagicMock()
    inst.subscribe = MagicMock()
    inst.__aiter__ = aiter_fn
    inst.stop = MagicMock(name="stop")
    return inst


# --- replay_start propagation ---

async def test_replay_start_is_passed_to_subscribe():
    replay_start = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
    sub = LiveSubscription(api_key="k", replay_start=replay_start)
    cm, aiter_fn = _patch_live([])
    with cm as mock_live_class:
        inst = _make_live_instance(aiter_fn)
        mock_live_class.return_value = inst
        async for _ in sub.subscribe_live():
            pass
    # subscribe() called with start=replay_start
    _, kwargs = inst.subscribe.call_args
    assert kwargs.get("start") == replay_start


async def test_replay_start_none_backward_compatible():
    """Without replay_start, subscribe is called with start=None (current behavior)."""
    sub = LiveSubscription(api_key="k")
    cm, aiter_fn = _patch_live([])
    with cm as mock_live_class:
        inst = _make_live_instance(aiter_fn)
        mock_live_class.return_value = inst
        async for _ in sub.subscribe_live():
            pass
    _, kwargs = inst.subscribe.call_args
    assert kwargs.get("start") is None


# --- REPLAY_COMPLETED detection (separate from CandleSource output) ---

async def test_replay_completed_event_not_set_before_boundary():
    sub = LiveSubscription(api_key="k")
    assert sub.is_replay_complete is False


async def test_replay_completed_detected_and_not_yielded_as_candle():
    """Scenario A/E: replay candles then REPLAY_COMPLETED. The SystemMsg is not a Candle;
    is_replay_complete flips True; only Candles are yielded."""
    records = [_ohlcv(TS_1, 100), _ohlcv(TS_2, 200), _replay_completed_msg(), _ohlcv(TS_3, 300)]
    cm, aiter_fn = _patch_live(records)
    sub = LiveSubscription(api_key="k")
    yielded = []
    replay_state_per_candle = []
    with cm as mock_live_class:
        inst = _make_live_instance(aiter_fn)
        mock_live_class.return_value = inst
        async for candle in sub.subscribe_live():
            yielded.append(candle)
            replay_state_per_candle.append(sub.is_replay_complete)

    assert all(isinstance(c, Candle) for c in yielded)
    assert len(yielded) == 3  # 3 OHLCV; the SystemMsg is NOT yielded
    # First two candles arrived during replay (is_replay_complete False),
    # the third after REPLAY_COMPLETED (True).
    assert replay_state_per_candle == [False, False, True]
    assert sub.is_replay_complete is True


async def test_replay_completed_event_awaitable():
    """replay_completed_event can be awaited; it is set once the boundary arrives."""
    import asyncio
    records = [_ohlcv(TS_1), _replay_completed_msg()]
    cm, aiter_fn = _patch_live(records)
    sub = LiveSubscription(api_key="k")
    with cm as mock_live_class:
        inst = _make_live_instance(aiter_fn)
        mock_live_class.return_value = inst
        async for _ in sub.subscribe_live():
            pass
    assert sub.replay_completed_event.is_set()


# --- CandleSource output type unchanged ---

async def test_candlesource_output_type_unchanged():
    """subscribe_live still yields only Candle even with SystemMsg in the stream."""
    records = [_ohlcv(TS_1), _replay_completed_msg(), _ohlcv(TS_2)]
    cm, aiter_fn = _patch_live(records)
    sub = LiveSubscription(api_key="k")
    with cm as mock_live_class:
        inst = _make_live_instance(aiter_fn)
        mock_live_class.return_value = inst
        out = [c async for c in sub.subscribe_live()]
    assert all(isinstance(c, Candle) for c in out)
