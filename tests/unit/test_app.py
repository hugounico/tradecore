"""Unit tests for src/api/app.py — FastAPI application and WebSocket endpoint."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.api.app import _build_initial_load_message
from src.schemas.candle import Candle


def _make_candle(minute_offset: int, close: float) -> Candle:
    """Create a candle at a given minute offset for testing."""
    ts = datetime(2024, 1, 15, 10, minute_offset, 0, tzinfo=timezone.utc)
    return Candle(
        timestamp=ts,
        open=close - 0.5,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=100 + minute_offset,
    )


class TestBuildInitialLoadMessage:
    """Tests for the _build_initial_load_message helper function."""

    def test_returns_correct_structure(self):
        """initial_load message has type, data with candles and SMA series."""
        candles = [_make_candle(i, 100.0 + i) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        assert msg["type"] == "initial_load"
        assert "candles" in msg["data"]
        assert "sma_fast_series" in msg["data"]
        assert "sma_slow_series" in msg["data"]

    def test_candle_count_matches_input(self):
        """All input candles appear in the message."""
        candles = [_make_candle(i, 100.0 + i) for i in range(30)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        assert len(msg["data"]["candles"]) == 30

    def test_candle_fields(self):
        """Each candle dict has time, open, high, low, close, volume."""
        candles = [_make_candle(0, 150.0)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        candle_dict = msg["data"]["candles"][0]
        assert "time" in candle_dict
        assert "open" in candle_dict
        assert "high" in candle_dict
        assert "low" in candle_dict
        assert "close" in candle_dict
        assert "volume" in candle_dict

    def test_sma_fast_series_starts_at_correct_position(self):
        """SMA fast series starts when enough candles exist (period 9 → index 8)."""
        candles = [_make_candle(i, 100.0 + i) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        # With 25 candles and fast period 9, SMA fast starts at candle index 8
        # So we get 25 - 9 + 1 = 17 SMA fast points
        assert len(msg["data"]["sma_fast_series"]) == 17

    def test_sma_slow_series_starts_at_correct_position(self):
        """SMA slow series starts when enough candles exist (period 21 → index 20)."""
        candles = [_make_candle(i, 100.0 + i) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        # With 25 candles and slow period 21, SMA slow starts at candle index 20
        # So we get 25 - 21 + 1 = 5 SMA slow points
        assert len(msg["data"]["sma_slow_series"]) == 5

    def test_sma_series_empty_when_not_enough_candles(self):
        """SMA series are empty when fewer candles than period."""
        candles = [_make_candle(i, 100.0 + i) for i in range(5)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        assert len(msg["data"]["sma_fast_series"]) == 0
        assert len(msg["data"]["sma_slow_series"]) == 0

    def test_sma_values_are_correct(self):
        """SMA values are correctly computed."""
        # Use constant price so SMA = that constant
        candles = [_make_candle(i, 100.0) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        # All SMA values should be 100.0 since all closes are 100.0
        for point in msg["data"]["sma_fast_series"]:
            assert point["value"] == 100.0
        for point in msg["data"]["sma_slow_series"]:
            assert point["value"] == 100.0

    def test_message_is_json_serializable(self):
        """The initial_load message can be serialized to JSON."""
        candles = [_make_candle(i, 100.0 + i) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        # Should not raise
        json_str = json.dumps(msg)
        assert json_str is not None

    def test_time_is_unix_timestamp(self):
        """Candle time values are Unix timestamps (integers)."""
        candles = [_make_candle(0, 100.0)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        candle_dict = msg["data"]["candles"][0]
        assert isinstance(candle_dict["time"], int)
