"""Unit tests for CandleBuffer class."""

from datetime import datetime, timezone

import pytest

from src.pipeline.candle_buffer import CandleBuffer
from src.schemas.candle import Candle


def _make_candle(minute: int, close: float = 100.0) -> Candle:
    """Helper to create a candle with a given minute offset."""
    return Candle(
        timestamp=datetime(2025, 1, 15, 14, minute, 0, tzinfo=timezone.utc),
        open=close - 1.0,
        high=close + 1.0,
        low=close - 2.0,
        close=close,
        volume=100,
    )


class TestCandleBufferInit:
    def test_empty_buffer_length_is_zero(self):
        buf = CandleBuffer()
        assert len(buf) == 0

    def test_default_max_size_is_500(self):
        buf = CandleBuffer()
        assert buf._max_size == 500

    def test_custom_max_size(self):
        buf = CandleBuffer(max_size=10)
        assert buf._max_size == 10


class TestCandleBufferAppend:
    def test_append_single_candle(self):
        buf = CandleBuffer()
        candle = _make_candle(0)
        buf.append(candle)
        assert len(buf) == 1

    def test_append_preserves_order(self):
        buf = CandleBuffer()
        c1 = _make_candle(0, close=100.0)
        c2 = _make_candle(1, close=101.0)
        c3 = _make_candle(2, close=102.0)
        buf.append(c1)
        buf.append(c2)
        buf.append(c3)
        all_candles = buf.get_all()
        assert all_candles == [c1, c2, c3]

    def test_append_drops_oldest_when_exceeds_max_size(self):
        buf = CandleBuffer(max_size=3)
        candles = [_make_candle(i, close=100.0 + i) for i in range(5)]
        for c in candles:
            buf.append(c)
        assert len(buf) == 3
        all_candles = buf.get_all()
        assert all_candles == candles[2:]

    def test_append_discards_duplicate_timestamp(self):
        buf = CandleBuffer()
        c1 = _make_candle(5, close=100.0)
        c2 = Candle(
            timestamp=c1.timestamp,
            open=200.0,
            high=210.0,
            low=190.0,
            close=205.0,
            volume=500,
        )
        buf.append(c1)
        buf.append(c2)
        assert len(buf) == 1
        assert buf.get_all()[0] is c1

    def test_append_discards_older_timestamp(self):
        buf = CandleBuffer()
        c1 = _make_candle(5, close=100.0)
        c_old = _make_candle(3, close=99.0)
        buf.append(c1)
        buf.append(c_old)
        assert len(buf) == 1
        assert buf.get_all()[0] is c1


class TestCandleBufferGetCloses:
    def test_get_closes_returns_last_n(self):
        buf = CandleBuffer()
        for i in range(10):
            buf.append(_make_candle(i, close=100.0 + i))
        closes = buf.get_closes(3)
        assert closes == [107.0, 108.0, 109.0]

    def test_get_closes_when_fewer_than_n(self):
        buf = CandleBuffer()
        buf.append(_make_candle(0, close=50.0))
        buf.append(_make_candle(1, close=51.0))
        closes = buf.get_closes(10)
        assert closes == [50.0, 51.0]

    def test_get_closes_empty_buffer(self):
        buf = CandleBuffer()
        assert buf.get_closes(5) == []


class TestCandleBufferGetAll:
    def test_get_all_returns_copy(self):
        buf = CandleBuffer()
        c = _make_candle(0)
        buf.append(c)
        all_candles = buf.get_all()
        all_candles.clear()
        assert len(buf) == 1

    def test_get_all_empty_buffer(self):
        buf = CandleBuffer()
        assert buf.get_all() == []


class TestCandleBufferLastTimestamp:
    def test_last_timestamp_empty_buffer(self):
        buf = CandleBuffer()
        assert buf.last_timestamp() is None

    def test_last_timestamp_after_appends(self):
        buf = CandleBuffer()
        c1 = _make_candle(0)
        c2 = _make_candle(5)
        buf.append(c1)
        buf.append(c2)
        assert buf.last_timestamp() == c2.timestamp
