"""Unit tests for ThrottledPusher class."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.api.throttled_pusher import ThrottledPusher
from src.schemas.candle import Candle
from src.schemas.signal import Signal, SignalType


def _make_candle(close: float = 100.0) -> Candle:
    return Candle(
        timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        open=99.0,
        high=101.0,
        low=98.0,
        close=close,
        volume=500,
    )


def _make_signal() -> Signal:
    return Signal(
        type=SignalType.BUY,
        timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        price=100.0,
        sma_fast=100.5,
        sma_slow=99.5,
    )


def _mock_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


class TestThrottledPusherTwoRapidEvents:
    """Two rapid events result in one push (RF-02.2)."""

    async def test_two_rapid_queues_produce_single_push(self):
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle())
            await pusher.queue_signal(_make_signal())
            await pusher.flush_if_ready()

        # Only one send_text call with both messages batched
        ws.send_text.assert_called_once()
        batch = json.loads(ws.send_text.call_args[0][0])
        assert len(batch) == 2
        assert batch[0]["type"] == "candle"
        assert batch[1]["type"] == "signal"


class TestThrottledPusherEventAfterInterval:
    """Event after throttle interval passes triggers immediate push (RF-02.2)."""

    async def test_event_after_interval_triggers_immediate_push(self):
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        # First push at t=100
        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle(close=100.0))
            await pusher.flush_if_ready()

        assert ws.send_text.call_count == 1

        # Second push at t=101.1 (more than 1s later)
        with patch("src.api.throttled_pusher.time.time", return_value=101.1):
            await pusher.queue_candle(_make_candle(close=105.0))
            await pusher.flush_if_ready()

        assert ws.send_text.call_count == 2


class TestThrottledPusherCandleAndSignalBatched:
    """Candle + signal queued within interval are batched into one message (RF-02.2)."""

    async def test_candle_and_signal_batched_together(self):
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=200.0):
            await pusher.queue_candle(_make_candle())
            await pusher.queue_signal(_make_signal())
            await pusher.flush_if_ready()

        ws.send_text.assert_called_once()
        batch = json.loads(ws.send_text.call_args[0][0])
        assert len(batch) == 2
        types = [msg["type"] for msg in batch]
        assert "candle" in types
        assert "signal" in types


class TestThrottledPusherNoPushWhenEmpty:
    """No push when there are no pending messages (RF-02.2)."""

    async def test_no_push_when_empty(self):
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        with patch("src.api.throttled_pusher.time.time", return_value=300.0):
            await pusher.flush_if_ready()

        ws.send_text.assert_not_called()


class TestThrottledPusherThrottleWithinInterval:
    """Events within throttle interval are held until interval elapses (RF-02.2)."""

    async def test_second_flush_within_interval_does_not_send(self):
        pusher = ThrottledPusher(throttle_interval=1.0)
        ws = _mock_ws()
        await pusher.set_connection(ws)

        # First flush at t=100
        with patch("src.api.throttled_pusher.time.time", return_value=100.0):
            await pusher.queue_candle(_make_candle())
            await pusher.flush_if_ready()

        assert ws.send_text.call_count == 1

        # Queue another event and flush at t=100.5 (within interval)
        with patch("src.api.throttled_pusher.time.time", return_value=100.5):
            await pusher.queue_candle(_make_candle(close=110.0))
            await pusher.flush_if_ready()

        # Should NOT have sent again
        assert ws.send_text.call_count == 1
