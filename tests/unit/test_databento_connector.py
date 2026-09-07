"""Unit tests for DabentoConnector — Historical loading.

Tests with mocked Databento client to validate:
- Successful historical load returns candles
- Price conversion from int64 × 1e-9 to float
- Empty response returns empty list
- Timeout returns empty list
- Candles are sorted by timestamp ascending
- API errors return empty list gracefully

Requirements: RF-01.1, RF-01.4
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.connectors.databento_connector import (
    FIXED_PRICE_SCALE,
    DabentoConnector,
    _convert_price,
)
from src.schemas.candle import Candle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_record(
    ts_event_ns: int,
    open_price: int,
    high_price: int,
    low_price: int,
    close_price: int,
    volume: int,
) -> MagicMock:
    """Create a mock OHLCVMsg record with int64 fixed-point prices."""
    record = MagicMock()
    record.ts_event = ts_event_ns
    record.open = open_price
    record.high = high_price
    record.low = low_price
    record.close = close_price
    record.volume = volume
    return record


# Sample timestamps (in nanoseconds)
TS_1 = 1700000000_000000000  # 2023-11-14T22:13:20Z
TS_2 = 1700000060_000000000  # 1 minute later
TS_3 = 1700000120_000000000  # 2 minutes later


# Sample NQ price (~21500) as int64 × 1e9
PRICE_OPEN = 21500_000000000
PRICE_HIGH = 21510_000000000
PRICE_LOW = 21490_000000000
PRICE_CLOSE = 21505_000000000
VOLUME = 1500


# ---------------------------------------------------------------------------
# Price conversion tests
# ---------------------------------------------------------------------------


class TestPriceConversion:
    """Tests for _convert_price helper function."""

    def test_int64_large_value_divided(self):
        """Large int64 values are divided by 1e9."""
        assert _convert_price(21500_000000000) == 21500.0

    def test_float_large_value_divided(self):
        """Large float values (> 1M) are divided by 1e9."""
        assert _convert_price(21500_000000000.0) == 21500.0

    def test_small_float_returned_as_is(self):
        """Small float values (already converted) returned as-is."""
        assert _convert_price(21500.0) == 21500.0

    def test_small_int_returned_as_float(self):
        """Small int values returned as float without division."""
        assert _convert_price(100) == 100.0

    def test_negative_large_value(self):
        """Negative large values are also converted."""
        assert _convert_price(-5000_000000000) == -5000.0

    def test_zero(self):
        """Zero is returned as 0.0."""
        assert _convert_price(0) == 0.0


# ---------------------------------------------------------------------------
# DabentoConnector tests
# ---------------------------------------------------------------------------


class TestDabentoConnectorHistorical:
    """Tests for DabentoConnector.load_historical method."""

    def _make_connector(self) -> DabentoConnector:
        return DabentoConnector(
            api_key="test-key-123",
            dataset="GLBX.MDP3",
            symbol="NQ.c.0",
            stype_in="continuous",
        )

    @pytest.mark.asyncio
    async def test_successful_load_returns_candles(self):
        """Successful API call returns a list of Candle objects."""
        records = [
            _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME),
            _make_mock_record(TS_2, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1600),
        ]

        connector = self._make_connector()

        with patch.object(connector, "_fetch_historical_sync", return_value=[
            Candle(
                timestamp=datetime.fromtimestamp(TS_1 / 1e9, tz=timezone.utc),
                open=21500.0, high=21510.0, low=21490.0, close=21505.0, volume=1500,
            ),
            Candle(
                timestamp=datetime.fromtimestamp(TS_2 / 1e9, tz=timezone.utc),
                open=21500.0, high=21510.0, low=21490.0, close=21505.0, volume=1600,
            ),
        ]):
            candles = await connector.load_historical(count=2)

        assert len(candles) == 2
        assert all(isinstance(c, Candle) for c in candles)
        assert candles[0].volume == 1500
        assert candles[1].volume == 1600

    @pytest.mark.asyncio
    async def test_candles_sorted_by_timestamp(self):
        """Returned candles are sorted by timestamp ascending."""
        # Return candles in reverse order
        ts_late = datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
        ts_early = datetime(2024, 1, 15, 14, 29, 0, tzinfo=timezone.utc)

        connector = self._make_connector()

        with patch.object(connector, "_fetch_historical_sync", return_value=[
            Candle(timestamp=ts_late, open=21500.0, high=21510.0, low=21490.0, close=21505.0, volume=100),
            Candle(timestamp=ts_early, open=21500.0, high=21510.0, low=21490.0, close=21505.0, volume=200),
        ]):
            candles = await connector.load_historical(count=2)

        assert candles[0].timestamp < candles[1].timestamp
        assert candles[0].volume == 200  # early candle first
        assert candles[1].volume == 100  # late candle second

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty_list(self):
        """When API returns no records, return empty list."""
        connector = self._make_connector()

        with patch.object(connector, "_fetch_historical_sync", return_value=[]):
            candles = await connector.load_historical(count=50)

        assert candles == []

    @pytest.mark.asyncio
    async def test_timeout_returns_empty_list(self):
        """When API times out, return empty list gracefully."""
        connector = self._make_connector()

        async def slow_fetch(count):
            await asyncio.sleep(5)  # Simulate slow response
            return []

        with patch.object(connector, "_fetch_historical", side_effect=slow_fetch):
            # Patch the timeout to be very short so test finishes quickly
            with patch("src.connectors.databento_connector.asyncio.wait_for", side_effect=asyncio.TimeoutError):
                candles = await connector.load_historical(count=50)

        assert candles == []

    @pytest.mark.asyncio
    async def test_api_error_returns_empty_list(self):
        """When API raises an exception, return empty list."""
        connector = self._make_connector()

        with patch.object(
            connector, "_fetch_historical_sync",
            side_effect=RuntimeError("Connection refused"),
        ):
            candles = await connector.load_historical(count=50)

        assert candles == []

    @pytest.mark.asyncio
    async def test_last_candle_time_updated_on_success(self):
        """After successful load, last_candle_time reflects the latest candle."""
        connector = self._make_connector()
        expected_time = datetime.fromtimestamp(TS_2 / 1e9, tz=timezone.utc)

        with patch.object(connector, "_fetch_historical_sync", return_value=[
            Candle(
                timestamp=datetime.fromtimestamp(TS_1 / 1e9, tz=timezone.utc),
                open=21500.0, high=21510.0, low=21490.0, close=21505.0, volume=1500,
            ),
            Candle(
                timestamp=expected_time,
                open=21500.0, high=21510.0, low=21490.0, close=21505.0, volume=1600,
            ),
        ]):
            await connector.load_historical(count=2)

        assert connector.last_candle_time == expected_time

    @pytest.mark.asyncio
    async def test_last_candle_time_none_on_error(self):
        """On error, last_candle_time remains None."""
        connector = self._make_connector()

        with patch.object(
            connector, "_fetch_historical_sync",
            side_effect=RuntimeError("API error"),
        ):
            await connector.load_historical(count=50)

        assert connector.last_candle_time is None

    def test_record_to_candle_conversion(self):
        """OHLCVMsg record is correctly converted to Candle with price scaling."""
        record = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)

        candle = DabentoConnector._record_to_candle(record)

        assert candle is not None
        assert candle.open == 21500.0
        assert candle.high == 21510.0
        assert candle.low == 21490.0
        assert candle.close == 21505.0
        assert candle.volume == 1500
        assert candle.timestamp == datetime.fromtimestamp(TS_1 / 1e9, tz=timezone.utc)

    def test_record_to_candle_invalid_record_returns_none(self):
        """Invalid record that raises an error returns None."""
        record = MagicMock()
        record.ts_event = "invalid"  # This will cause an error
        record.open = None  # This will fail

        candle = DabentoConnector._record_to_candle(record)

        assert candle is None

    def test_constructor_stores_parameters(self):
        """Constructor stores all parameters correctly."""
        connector = DabentoConnector(
            api_key="my-key",
            dataset="GLBX.MDP3",
            symbol="NQ.c.0",
            stype_in="continuous",
        )
        assert connector._api_key == "my-key"
        assert connector._dataset == "GLBX.MDP3"
        assert connector._symbol == "NQ.c.0"
        assert connector._stype_in == "continuous"
        assert connector.last_candle_time is None


# ---------------------------------------------------------------------------
# Live Streaming Tests (Fase B — Task 12.3)
# ---------------------------------------------------------------------------


class TestDabentoConnectorLiveSubscription:
    """Tests for DabentoConnector.subscribe_live method.

    Requirements: RF-01.1, RNF-01
    """

    def _make_connector(self) -> DabentoConnector:
        return DabentoConnector(
            api_key="test-key-123",
            dataset="GLBX.MDP3",
            symbol="NQ.c.0",
            stype_in="continuous",
        )

    @pytest.mark.asyncio
    async def test_live_subscription_yields_candles(self):
        """Live subscription yields Candle objects from mocked Live client records."""
        connector = self._make_connector()

        # Simulate two completed ohlcv-1m bars arriving via live stream
        record_1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)
        record_2 = _make_mock_record(TS_2, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1600)

        async def mock_live_iterator(*args, **kwargs):
            """Mock async iterator that yields records like db.Live()."""
            for rec in [record_1, record_2]:
                yield rec

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_instance = MagicMock()
            mock_live_class.return_value = mock_live_instance
            mock_live_instance.subscribe = MagicMock()
            mock_live_instance.__aiter__ = mock_live_iterator

            candles = []
            async for candle in connector.subscribe_live():
                candles.append(candle)
                if len(candles) >= 2:
                    break

        assert len(candles) == 2
        assert all(isinstance(c, Candle) for c in candles)
        assert candles[0].volume == 1500
        assert candles[1].volume == 1600
        assert candles[0].open == 21500.0
        assert candles[0].close == 21505.0

    @pytest.mark.asyncio
    async def test_live_subscription_updates_last_candle_time(self):
        """Live subscription updates last_candle_time as candles arrive."""
        connector = self._make_connector()

        record_1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)

        async def mock_live_iterator(*args, **kwargs):
            yield record_1

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_instance = MagicMock()
            mock_live_class.return_value = mock_live_instance
            mock_live_instance.subscribe = MagicMock()
            mock_live_instance.__aiter__ = mock_live_iterator

            async for candle in connector.subscribe_live():
                pass

        expected_time = datetime.fromtimestamp(TS_1 / 1e9, tz=timezone.utc)
        assert connector.last_candle_time == expected_time

    @pytest.mark.asyncio
    async def test_is_connected_true_during_live_stream(self):
        """is_connected is True while live subscription is active."""
        connector = self._make_connector()

        record_1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)

        connected_during_stream = []

        async def mock_live_iterator(*args, **kwargs):
            yield record_1

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_instance = MagicMock()
            mock_live_class.return_value = mock_live_instance
            mock_live_instance.subscribe = MagicMock()
            mock_live_instance.__aiter__ = mock_live_iterator

            async for candle in connector.subscribe_live():
                connected_during_stream.append(connector.is_connected)

        assert all(connected_during_stream), "is_connected should be True during active stream"

    @pytest.mark.asyncio
    async def test_is_connected_false_after_stream_ends(self):
        """is_connected is False after the live stream ends."""
        connector = self._make_connector()

        async def mock_live_iterator(*args, **kwargs):
            # Empty stream — ends immediately
            return
            yield  # Make it a proper async generator

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_instance = MagicMock()
            mock_live_class.return_value = mock_live_instance
            mock_live_instance.subscribe = MagicMock()
            mock_live_instance.__aiter__ = mock_live_iterator

            async for _ in connector.subscribe_live():
                pass

        assert connector.is_connected is False

    @pytest.mark.asyncio
    async def test_live_discards_candles_before_last_historical(self):
        """Live candles with ts_event ≤ last historical candle are discarded."""
        connector = self._make_connector()

        # Simulate that historical loading already set last_candle_time
        historical_time = datetime.fromtimestamp(TS_2 / 1e9, tz=timezone.utc)
        connector._last_candle_time = historical_time

        # Record 1: ts_event == TS_1 (before historical) — should be discarded
        # Record 2: ts_event == TS_2 (equal to historical) — should be discarded
        # Record 3: ts_event == TS_3 (after historical) — should be yielded
        record_old = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1000)
        record_equal = _make_mock_record(TS_2, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1100)
        record_new = _make_mock_record(TS_3, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1200)

        async def mock_live_iterator(*args, **kwargs):
            for rec in [record_old, record_equal, record_new]:
                yield rec

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_instance = MagicMock()
            mock_live_class.return_value = mock_live_instance
            mock_live_instance.subscribe = MagicMock()
            mock_live_instance.__aiter__ = mock_live_iterator

            candles = []
            async for candle in connector.subscribe_live():
                candles.append(candle)

        # Only the candle newer than the last historical one should be yielded
        assert len(candles) == 1
        assert candles[0].volume == 1200


class TestDabentoConnectorReconnection:
    """Tests for DabentoConnector reconnection logic.

    Requirements: RNF-01
    """

    def _make_connector(self) -> DabentoConnector:
        return DabentoConnector(
            api_key="test-key-123",
            dataset="GLBX.MDP3",
            symbol="NQ.c.0",
            stype_in="continuous",
        )

    @pytest.mark.asyncio
    async def test_reconnect_succeeds_on_first_attempt(self):
        """Reconnection succeeds on the first attempt."""
        connector = self._make_connector()

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_instance = MagicMock()
            mock_live_class.return_value = mock_live_instance
            mock_live_instance.subscribe = MagicMock()

            result = await connector.reconnect()

        assert result is True
        assert connector.is_connected is True

    @pytest.mark.asyncio
    async def test_reconnect_retries_up_to_3_times(self):
        """Reconnection retries up to 3 times before giving up."""
        connector = self._make_connector()

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            # All attempts fail
            mock_live_class.side_effect = ConnectionError("Connection refused")

            with patch("asyncio.sleep", new_callable=lambda: MagicMock(side_effect=asyncio.coroutine(lambda x: None))):
                result = await connector.reconnect()

        assert result is False
        # Should have tried exactly 3 times
        assert mock_live_class.call_count == 3

    @pytest.mark.asyncio
    async def test_reconnect_uses_exponential_backoff(self):
        """Reconnection uses exponential backoff: 1s, 2s, 4s."""
        connector = self._make_connector()

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_class.side_effect = ConnectionError("Connection refused")

            with patch("asyncio.sleep", side_effect=mock_sleep):
                result = await connector.reconnect()

        assert result is False
        # Backoff delays: 1s after 1st failure, 2s after 2nd failure
        # (no sleep after 3rd failure since we give up)
        assert sleep_calls == [1, 2, 4] or sleep_calls == [1, 2]
        # At minimum, first two delays should be 1s and 2s
        assert len(sleep_calls) >= 2
        assert sleep_calls[0] == 1
        assert sleep_calls[1] == 2

    @pytest.mark.asyncio
    async def test_reconnect_succeeds_on_second_attempt(self):
        """Reconnection succeeds on the second attempt after one failure."""
        connector = self._make_connector()

        call_count = [0]

        def live_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("Connection refused")
            mock_instance = MagicMock()
            mock_instance.subscribe = MagicMock()
            return mock_instance

        async def mock_sleep(seconds):
            pass

        with patch("src.connectors.databento_connector.db.Live", side_effect=live_side_effect):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                result = await connector.reconnect()

        assert result is True
        assert connector.is_connected is True
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_auth_failed_stops_retries_immediately(self):
        """AUTH_FAILED error stops reconnection immediately — no retries."""
        connector = self._make_connector()

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        # Simulate Databento auth failure (BentoError with AUTH text)
        auth_error = Exception("Authentication failed: invalid API key")
        auth_error.__class__.__name__ = "BentoError"

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_class.side_effect = auth_error

            with patch("asyncio.sleep", side_effect=mock_sleep):
                result = await connector.reconnect()

        assert result is False
        # Should NOT retry after auth failure — only 1 attempt
        assert mock_live_class.call_count == 1
        # No sleep calls since we don't retry
        assert sleep_calls == []

    @pytest.mark.asyncio
    async def test_is_connected_false_after_failed_reconnection(self):
        """is_connected is False after all reconnection attempts fail."""
        connector = self._make_connector()

        async def mock_sleep(seconds):
            pass

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_class.side_effect = ConnectionError("Connection refused")

            with patch("asyncio.sleep", side_effect=mock_sleep):
                await connector.reconnect()

        assert connector.is_connected is False

    @pytest.mark.asyncio
    async def test_live_subscription_triggers_reconnect_on_disconnect(self):
        """When live stream disconnects, subscribe_live attempts reconnection."""
        connector = self._make_connector()

        record_1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)

        call_count = [0]

        async def mock_live_iterator_first(*args, **kwargs):
            """First connection yields one record then raises error."""
            yield record_1
            raise ConnectionError("Stream disconnected")

        async def mock_live_iterator_empty(*args, **kwargs):
            """Second connection yields nothing (simulate failed reconnect)."""
            return
            yield

        with patch("src.connectors.databento_connector.db.Live") as mock_live_class:
            mock_live_instance = MagicMock()
            mock_live_class.return_value = mock_live_instance
            mock_live_instance.subscribe = MagicMock()

            # First iteration yields candle then errors
            mock_live_instance.__aiter__ = mock_live_iterator_first

        with patch.object(connector, "reconnect", return_value=False) as mock_reconnect:
            candles = []
            try:
                async for candle in connector.subscribe_live():
                    candles.append(candle)
            except ConnectionError:
                pass  # Expected after reconnection fails

            # At least one candle was received before disconnect
            assert len(candles) >= 1
            assert candles[0].volume == VOLUME
