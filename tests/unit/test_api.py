"""Unit tests for WebSocket message format.

Validates: Requirements RF-02.1, RF-04.1, RF-04.2

Tests cover:
- initial_load message structure
- candle update format
- signal message format
- status message format
- batch array structure
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.api.app import _build_initial_load_message
from src.api.throttled_pusher import ThrottledPusher
from src.schemas.candle import Candle
from src.schemas.signal import Signal, SignalType


# --- Helpers ---


def _make_candle(minute: int = 0, close: float = 100.0) -> Candle:
    """Create a test candle at a given minute offset."""
    ts = datetime(2024, 1, 15, 10, minute, 0, tzinfo=timezone.utc)
    return Candle(
        timestamp=ts,
        open=close - 0.5,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=100 + minute,
    )


def _make_signal(signal_type: SignalType = SignalType.BUY) -> Signal:
    """Create a test signal."""
    return Signal(
        type=signal_type,
        timestamp=datetime(2024, 1, 15, 10, 5, 0, tzinfo=timezone.utc),
        price=16234.50,
        sma_fast=16230.12,
        sma_slow=16228.45,
    )


def _mock_ws() -> AsyncMock:
    """Create a mock WebSocket."""
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


# --- Tests: initial_load message structure (RF-04.1) ---


class TestInitialLoadMessageStructure:
    """Tests for the initial_load WebSocket message format."""

    def test_initial_load_has_type_field(self):
        """initial_load message must have type='initial_load'."""
        candles = [_make_candle(i, 100.0 + i) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        assert msg["type"] == "initial_load"

    def test_initial_load_has_data_with_candles(self):
        """initial_load data must contain 'candles' list."""
        candles = [_make_candle(i, 100.0 + i) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        assert "data" in msg
        assert "candles" in msg["data"]
        assert isinstance(msg["data"]["candles"], list)

    def test_initial_load_has_sma_fast_series(self):
        """initial_load data must contain 'sma_fast_series' list."""
        candles = [_make_candle(i, 100.0 + i) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        assert "sma_fast_series" in msg["data"]
        assert isinstance(msg["data"]["sma_fast_series"], list)

    def test_initial_load_has_sma_slow_series(self):
        """initial_load data must contain 'sma_slow_series' list."""
        candles = [_make_candle(i, 100.0 + i) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        assert "sma_slow_series" in msg["data"]
        assert isinstance(msg["data"]["sma_slow_series"], list)

    def test_initial_load_candle_has_required_fields(self):
        """Each candle in initial_load must have time, open, high, low, close, volume."""
        candles = [_make_candle(0, 150.0)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        candle_dict = msg["data"]["candles"][0]
        required_fields = {"time", "open", "high", "low", "close", "volume"}
        assert required_fields.issubset(candle_dict.keys())

    def test_initial_load_candle_time_is_integer(self):
        """Candle time in initial_load is a Unix timestamp integer."""
        candles = [_make_candle(0, 150.0)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        candle_dict = msg["data"]["candles"][0]
        assert isinstance(candle_dict["time"], int)

    def test_initial_load_sma_series_entry_has_time_and_value(self):
        """SMA series entries must have 'time' (int) and 'value' (float)."""
        candles = [_make_candle(i, 100.0) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        # sma_fast_series should have entries since we have 25 candles > period 9
        assert len(msg["data"]["sma_fast_series"]) > 0
        entry = msg["data"]["sma_fast_series"][0]
        assert "time" in entry
        assert "value" in entry
        assert isinstance(entry["time"], int)
        assert isinstance(entry["value"], float)

    def test_initial_load_is_json_serializable(self):
        """The complete initial_load message must be JSON-serializable."""
        candles = [_make_candle(i, 100.0 + i) for i in range(25)]
        msg = _build_initial_load_message(candles, sma_fast_period=9, sma_slow_period=21)

        serialized = json.dumps(msg)
        deserialized = json.loads(serialized)
        assert deserialized["type"] == "initial_load"


# --- Tests: candle update format (RF-02.1) ---


class TestCandleUpdateFormat:
    """Tests for the candle message format in WebSocket updates."""

    async def test_candle_message_type(self):
        """Candle message must have type='candle'."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle(0, 16234.25))
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert batch[0]["type"] == "candle"

    async def test_candle_data_has_required_fields(self):
        """Candle data must contain time, open, high, low, close, volume."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle(0, 16234.25))
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        candle_data = batch[0]["data"]
        required_fields = {"time", "open", "high", "low", "close", "volume"}
        assert required_fields.issubset(candle_data.keys())

    async def test_candle_time_is_unix_integer(self):
        """Candle time field must be a Unix timestamp integer."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        candle = _make_candle(5, 16234.25)
        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(candle)
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        candle_data = batch[0]["data"]
        assert isinstance(candle_data["time"], int)
        assert candle_data["time"] == int(candle.timestamp.timestamp())

    async def test_candle_volume_is_integer(self):
        """Candle volume field must be an integer."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle(0, 16234.25))
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        candle_data = batch[0]["data"]
        assert isinstance(candle_data["volume"], int)

    async def test_candle_prices_are_floats(self):
        """Candle open, high, low, close must be floats."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle(0, 16234.25))
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        candle_data = batch[0]["data"]
        for field in ("open", "high", "low", "close"):
            assert isinstance(candle_data[field], float)


# --- Tests: signal message format (RF-04.2) ---


class TestSignalMessageFormat:
    """Tests for the signal message format in WebSocket updates."""

    async def test_signal_message_type(self):
        """Signal message must have type='signal'."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_signal(_make_signal(SignalType.BUY))
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert batch[0]["type"] == "signal"

    async def test_signal_data_has_required_fields(self):
        """Signal data must contain signal_type, time, price, sma_fast, sma_slow."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_signal(_make_signal(SignalType.BUY))
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        signal_data = batch[0]["data"]
        required_fields = {"signal_type", "time", "price", "sma_fast", "sma_slow"}
        assert required_fields.issubset(signal_data.keys())

    async def test_signal_type_buy_value(self):
        """BUY signal must have signal_type='BUY'."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_signal(_make_signal(SignalType.BUY))
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert batch[0]["data"]["signal_type"] == "BUY"

    async def test_signal_type_sell_value(self):
        """SELL signal must have signal_type='SELL'."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_signal(_make_signal(SignalType.SELL))
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert batch[0]["data"]["signal_type"] == "SELL"

    async def test_signal_time_is_unix_integer(self):
        """Signal time must be a Unix timestamp integer."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_signal(_make_signal())
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert isinstance(batch[0]["data"]["time"], int)

    async def test_signal_price_and_sma_are_floats(self):
        """Signal price, sma_fast, sma_slow must be floats."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_signal(_make_signal())
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        signal_data = batch[0]["data"]
        assert isinstance(signal_data["price"], float)
        assert isinstance(signal_data["sma_fast"], float)
        assert isinstance(signal_data["sma_slow"], float)


# --- Tests: status message format (RF-04.1) ---


class TestStatusMessageFormat:
    """Tests for the status message format in WebSocket updates."""

    async def test_status_message_type(self):
        """Status message must have type='status'."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        last_time = datetime(2025, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_status(connected=True, last_time=last_time, mode="simulation")
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert batch[0]["type"] == "status"

    async def test_status_data_has_required_fields(self):
        """Status data must contain connected, last_candle_time, mode."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        last_time = datetime(2025, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_status(connected=True, last_time=last_time, mode="simulation")
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        status_data = batch[0]["data"]
        required_fields = {"connected", "last_candle_time", "mode"}
        assert required_fields.issubset(status_data.keys())

    async def test_status_connected_is_boolean(self):
        """Status connected field must be a boolean."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_status(connected=True, last_time=None, mode="simulation")
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert isinstance(batch[0]["data"]["connected"], bool)
        assert batch[0]["data"]["connected"] is True

    async def test_status_last_candle_time_iso_format(self):
        """Status last_candle_time must be ISO 8601 with Z suffix when set."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        last_time = datetime(2025, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_status(connected=True, last_time=last_time, mode="simulation")
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        last_candle_time = batch[0]["data"]["last_candle_time"]
        assert isinstance(last_candle_time, str)
        assert last_candle_time.endswith("Z")

    async def test_status_last_candle_time_none_when_no_candles(self):
        """Status last_candle_time must be None when no candles received."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_status(connected=True, last_time=None, mode="simulation")
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert batch[0]["data"]["last_candle_time"] is None

    async def test_status_mode_is_string(self):
        """Status mode field must be a string."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_status(connected=True, last_time=None, mode="simulation")
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert isinstance(batch[0]["data"]["mode"], str)
        assert batch[0]["data"]["mode"] == "simulation"


# --- Tests: batch array structure (RF-02.1) ---


class TestBatchArrayStructure:
    """Tests for the batch array format sent over WebSocket."""

    async def test_batch_is_json_array(self):
        """Messages are sent as a JSON array (list)."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle(0, 100.0))
            await pusher.flush_if_ready()

        raw = ws.send_text.call_args[0][0]
        parsed = json.loads(raw)
        assert isinstance(parsed, list)

    async def test_batch_contains_multiple_message_types(self):
        """A batch can contain candle, signal, sma, and status messages together."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        last_time = datetime(2025, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle(0, 16234.25))
            await pusher.queue_signal(_make_signal())
            await pusher.queue_sma_update(1700000000, 16230.12, 16228.45)
            await pusher.queue_status(connected=True, last_time=last_time, mode="simulation")
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert len(batch) == 4
        types = [msg["type"] for msg in batch]
        assert "candle" in types
        assert "signal" in types
        assert "sma" in types
        assert "status" in types

    async def test_each_batch_item_has_type_and_data(self):
        """Every message in the batch must have 'type' and 'data' keys."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle(0, 100.0))
            await pusher.queue_signal(_make_signal())
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        for item in batch:
            assert "type" in item
            assert "data" in item

    async def test_batch_preserves_queue_order(self):
        """Messages in batch appear in the order they were queued."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle(0, 100.0))
            await pusher.queue_signal(_make_signal())
            await pusher.queue_sma_update(1700000000, 100.5, 99.5)
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        assert batch[0]["type"] == "candle"
        assert batch[1]["type"] == "signal"
        assert batch[2]["type"] == "sma"

    async def test_sma_message_format(self):
        """SMA message has type='sma' with time, sma_fast, sma_slow fields."""
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_sma_update(1700000000, 16230.12, 16228.45)
            await pusher.flush_if_ready()

        batch = json.loads(ws.send_text.call_args[0][0])
        sma_msg = batch[0]
        assert sma_msg["type"] == "sma"
        assert sma_msg["data"]["time"] == 1700000000
        assert sma_msg["data"]["sma_fast"] == 16230.12
        assert sma_msg["data"]["sma_slow"] == 16228.45
