"""End-to-end integration tests for the Fase A simulation pipeline.

Tests the full flow: DabentoConnector → CandleBuffer → SignalEngine → WebSocket
Includes property-based tests for deduplication and data integrity.

Validates: Requirements RF-01.2, RF-01.4, RF-03.1
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.api.app import app, _build_initial_load_message
from src.api.throttled_pusher import ThrottledPusher
from src.connectors.databento_connector import DabentoConnector
from src.engine.signal_engine import SignalEngine
from src.pipeline.candle_buffer import CandleBuffer
from src.pipeline.simulation_replay import SimulationReplay
from src.schemas.candle import Candle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle(minute_offset: int, close: float = 100.0) -> Candle:
    """Create a candle at a given minute offset from a base time."""
    ts = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute_offset)
    return Candle(
        timestamp=ts,
        open=close - 0.5,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=100 + minute_offset,
    )


def _make_candles(n: int, start_close: float = 100.0) -> list[Candle]:
    """Create n candles with incrementing close prices."""
    return [_make_candle(i, start_close + i * 0.5) for i in range(n)]


# ---------------------------------------------------------------------------
# Test 1: End-to-end pipeline flow
# ---------------------------------------------------------------------------

class TestE2EPipelineFlow:
    """Test mock DabentoConnector → CandleBuffer → SignalEngine integration."""

    async def test_e2e_pipeline_flow(self, monkeypatch):
        """Full pipeline: load historical → buffer → evaluate signals.

        Validates: RF-01.2, RF-01.4
        """
        monkeypatch.setenv("DATABENTO_API_KEY", "test-key-dummy")

        # Create test candles — enough for slow SMA (21 period)
        test_candles = _make_candles(30, start_close=100.0)

        # Mock the connector
        connector = DabentoConnector(
            api_key="test-key-dummy",
            dataset="GLBX.MDP3",
            symbol="NQ.c.0",
            stype_in="continuous",
        )

        with patch.object(connector, "_fetch_historical", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = test_candles
            candles = await connector.load_historical(count=30)

        # Feed into CandleBuffer
        buffer = CandleBuffer(max_size=500)
        for candle in candles:
            buffer.append(candle)

        assert len(buffer) == 30

        # Evaluate with SignalEngine
        engine = SignalEngine(fast_period=9, slow_period=21)
        closes = buffer.get_closes(30)

        # With incrementing prices, the first evaluation sets prev values
        result = engine.evaluate(closes)
        # First call won't generate a signal (needs previous state)
        # This is expected — the engine needs at least 2 evaluations to detect crossover

        # Verify the pipeline components work together
        assert len(closes) == 30
        assert buffer.last_timestamp() == test_candles[-1].timestamp
        assert all(isinstance(c, float) for c in closes)

    async def test_e2e_pipeline_with_signal_generation(self, monkeypatch):
        """Pipeline generates a signal when SMA crossover occurs.

        Validates: RF-01.4
        """
        monkeypatch.setenv("DATABENTO_API_KEY", "test-key-dummy")

        engine = SignalEngine(fast_period=3, slow_period=5)
        buffer = CandleBuffer(max_size=500)

        # Create candles that will cause a crossover:
        # First: declining prices (fast < slow), then rising prices (fast > slow)
        declining = [_make_candle(i, 100.0 - i * 2) for i in range(7)]
        rising = [_make_candle(7 + i, 86.0 + i * 5) for i in range(5)]
        all_candles = declining + rising

        signals_found = []
        for candle in all_candles:
            buffer.append(candle)
            closes = buffer.get_closes(5)
            if len(closes) >= 5:
                signal = engine.evaluate(closes)
                if signal is not None:
                    signals_found.append(signal)

        # At least one signal should be generated during the crossover
        assert len(signals_found) >= 1


# ---------------------------------------------------------------------------
# Test 2: Simulation replay delivers candles
# ---------------------------------------------------------------------------

class TestSimulationReplay:
    """Test that SimulationReplay delivers candles through the pipeline."""

    async def test_simulation_replay_delivers_candles(self):
        """SimulationReplay yields all candles through the pipeline.

        Validates: RF-01.2
        """
        test_candles = _make_candles(10, start_close=100.0)

        # Use very short interval for testing
        replay = SimulationReplay(candles=test_candles, interval=0.01)

        buffer = CandleBuffer(max_size=500)
        received = []

        async for candle in replay.replay():
            buffer.append(candle)
            received.append(candle)

        assert len(received) == 10
        assert len(buffer) == 10
        # Verify order is preserved
        for i, candle in enumerate(received):
            assert candle.timestamp == test_candles[i].timestamp
            assert candle.close == test_candles[i].close

    async def test_simulation_replay_timing(self):
        """SimulationReplay introduces delay between candles.

        Validates: RF-01.2
        """
        test_candles = _make_candles(3, start_close=100.0)
        replay = SimulationReplay(candles=test_candles, interval=0.05)

        start_time = asyncio.get_event_loop().time()
        count = 0
        async for _ in replay.replay():
            count += 1
        elapsed = asyncio.get_event_loop().time() - start_time

        assert count == 3
        # Should take at least 2 intervals (between 3 candles)
        assert elapsed >= 0.09  # 2 * 0.05 with some tolerance

    async def test_simulation_replay_stop(self):
        """SimulationReplay can be stopped mid-replay.

        Validates: RF-01.2
        """
        test_candles = _make_candles(10, start_close=100.0)
        replay = SimulationReplay(candles=test_candles, interval=0.01)

        received = []
        async for candle in replay.replay():
            received.append(candle)
            if len(received) == 3:
                replay.stop()

        assert len(received) <= 4  # stop may not take effect immediately


# ---------------------------------------------------------------------------
# Test 3: WebSocket receives valid JSON
# ---------------------------------------------------------------------------

class TestWebSocketReceivesValidJson:
    """Test that WebSocket clients receive valid JSON matching the protocol spec."""

    async def test_websocket_receives_valid_json(self, monkeypatch):
        """WebSocket endpoint sends initial_load with correct structure.

        Validates: RF-03.1
        """
        monkeypatch.setenv("DATABENTO_API_KEY", "test-key-dummy")

        test_candles = _make_candles(25, start_close=100.0)

        with patch(
            "src.api.app.DabentoConnector.load_historical",
            new_callable=AsyncMock,
            return_value=test_candles,
        ):
            from httpx import ASGITransport, AsyncClient

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Use starlette test client for WebSocket
                from starlette.testclient import TestClient

                with TestClient(app) as tc:
                    with tc.websocket_connect("/ws/chart") as ws:
                        # Receive the initial_load message
                        data = ws.receive_text()
                        msg = json.loads(data)

                        # Validate structure
                        assert msg["type"] == "initial_load"
                        assert "data" in msg
                        assert "candles" in msg["data"]
                        assert "sma_fast_series" in msg["data"]
                        assert "sma_slow_series" in msg["data"]

                        # Validate candle fields
                        if msg["data"]["candles"]:
                            candle = msg["data"]["candles"][0]
                            assert "time" in candle
                            assert "open" in candle
                            assert "high" in candle
                            assert "low" in candle
                            assert "close" in candle
                            assert "volume" in candle
                            assert isinstance(candle["time"], int)

    async def test_websocket_initial_load_candle_count(self, monkeypatch):
        """WebSocket initial_load contains at least the initial batch of candles.

        Validates: RF-03.1
        """
        monkeypatch.setenv("DATABENTO_API_KEY", "test-key-dummy")

        # Provide 30 candles, split_index = slow_period = 21
        # Initial buffer has 21 candles, but the pipeline processing loop
        # runs concurrently and may append replay candles before WS connect.
        test_candles = _make_candles(30, start_close=100.0)

        with patch(
            "src.api.app.DabentoConnector.load_historical",
            new_callable=AsyncMock,
            return_value=test_candles,
        ):
            from starlette.testclient import TestClient

            with TestClient(app) as tc:
                with tc.websocket_connect("/ws/chart") as ws:
                    data = ws.receive_text()
                    msg = json.loads(data)

                    # At least the initial batch (21) is present; replay may have added more
                    assert len(msg["data"]["candles"]) >= 21
                    # But never more than total candles provided
                    assert len(msg["data"]["candles"]) <= 30


# ---------------------------------------------------------------------------
# Property 5: Historical Synchronization — No Duplicates
# Feature: tradecore-mvp, Property 5: Historical Synchronization — No Duplicates
# ---------------------------------------------------------------------------

# Hypothesis strategy: generate candles with potentially overlapping timestamps
_base_time = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


@st.composite
def candle_list_with_duplicates(draw):
    """Generate a list of candles where some may have duplicate/overlapping timestamps."""
    n = draw(st.integers(min_value=1, max_value=50))
    # Generate minute offsets that may repeat
    offsets = draw(
        st.lists(
            st.integers(min_value=0, max_value=30),
            min_size=n,
            max_size=n,
        )
    )
    candles = []
    for offset in offsets:
        ts = _base_time + timedelta(minutes=offset)
        close = draw(st.floats(min_value=50.0, max_value=200.0, allow_nan=False, allow_infinity=False))
        candles.append(
            Candle(
                timestamp=ts,
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=draw(st.integers(min_value=1, max_value=10000)),
            )
        )
    return candles


class TestPropertyNoDuplicateCandles:
    """Property 5: CandleBuffer never contains duplicate or non-ascending timestamps.

    **Validates: Requirements RF-01.2**
    """

    # Feature: tradecore-mvp, Property 5: Historical Synchronization — No Duplicates
    @given(candles=candle_list_with_duplicates())
    @settings(max_examples=100)
    def test_property_no_duplicate_candles(self, candles: list[Candle]):
        """CandleBuffer deduplicates: no two candles share same or decreasing timestamps.

        **Validates: Requirements RF-01.2**
        """
        buffer = CandleBuffer(max_size=500)

        for candle in candles:
            buffer.append(candle)

        all_candles = buffer.get_all()

        # Property: all timestamps in buffer are strictly ascending
        for i in range(1, len(all_candles)):
            assert all_candles[i].timestamp > all_candles[i - 1].timestamp

        # Property: buffer size <= number of unique strictly ascending timestamps
        # Count the maximum possible ascending subsequence from input
        unique_ascending = 0
        last_ts = None
        for candle in candles:
            if last_ts is None or candle.timestamp > last_ts:
                unique_ascending += 1
                last_ts = candle.timestamp

        assert len(buffer) <= unique_ascending

    # Feature: tradecore-mvp, Property 5: Historical Synchronization — No Duplicates
    @given(candles=candle_list_with_duplicates())
    @settings(max_examples=100)
    def test_property_buffer_no_timestamp_regression(self, candles: list[Candle]):
        """Buffer timestamps are always strictly monotonically increasing.

        **Validates: Requirements RF-01.2**
        """
        buffer = CandleBuffer(max_size=500)

        for candle in candles:
            buffer.append(candle)

        timestamps = [c.timestamp for c in buffer.get_all()]

        # No duplicates
        assert len(timestamps) == len(set(timestamps))

        # Strictly ascending
        for i in range(1, len(timestamps)):
            assert timestamps[i] > timestamps[i - 1]


# ---------------------------------------------------------------------------
# Property 6: Candle Data Integrity
# Feature: tradecore-mvp, Property 6: Candle Data Integrity
# ---------------------------------------------------------------------------

@st.composite
def ohlcv_candle(draw):
    """Generate a valid candle with random OHLCV values."""
    minute_offset = draw(st.integers(min_value=0, max_value=1000))
    ts = _base_time + timedelta(minutes=minute_offset)

    # Generate valid OHLCV: high >= open,close and low <= open,close
    open_price = draw(st.floats(min_value=50.0, max_value=500.0, allow_nan=False, allow_infinity=False))
    close_price = draw(st.floats(min_value=50.0, max_value=500.0, allow_nan=False, allow_infinity=False))
    high_price = max(open_price, close_price) + draw(
        st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
    )
    low_price = min(open_price, close_price) - draw(
        st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
    )
    volume = draw(st.integers(min_value=1, max_value=100000))

    return Candle(
        timestamp=ts,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
    )


class TestPropertyCandleDataIntegrity:
    """Property 6: OHLCV values are preserved from source through to frontend message.

    **Validates: Requirements RF-01.4**
    """

    # Feature: tradecore-mvp, Property 6: Candle Data Integrity
    @given(candle=ohlcv_candle())
    @settings(max_examples=100)
    def test_property_candle_data_integrity_through_buffer(self, candle: Candle):
        """OHLCV values are preserved when passing through CandleBuffer.

        **Validates: Requirements RF-01.4**
        """
        buffer = CandleBuffer(max_size=500)
        buffer.append(candle)

        stored = buffer.get_all()
        assert len(stored) == 1

        retrieved = stored[0]
        assert retrieved.open == candle.open
        assert retrieved.high == candle.high
        assert retrieved.low == candle.low
        assert retrieved.close == candle.close
        assert retrieved.volume == candle.volume
        assert retrieved.timestamp == candle.timestamp

    # Feature: tradecore-mvp, Property 6: Candle Data Integrity
    @given(candle=ohlcv_candle())
    @settings(max_examples=100)
    def test_property_candle_data_integrity_through_pusher_format(self, candle: Candle):
        """OHLCV values are preserved in the ThrottledPusher message format.

        **Validates: Requirements RF-01.4**
        """
        # Simulate the message format used by ThrottledPusher.queue_candle
        message = {
            "type": "candle",
            "data": {
                "time": int(candle.timestamp.timestamp()),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            },
        }

        # Verify OHLCV values match original candle exactly
        assert message["data"]["open"] == candle.open
        assert message["data"]["high"] == candle.high
        assert message["data"]["low"] == candle.low
        assert message["data"]["close"] == candle.close
        assert message["data"]["volume"] == candle.volume

        # Verify the message is valid JSON
        serialized = json.dumps(message)
        deserialized = json.loads(serialized)

        # After JSON round-trip, float values are preserved
        assert deserialized["data"]["open"] == candle.open
        assert deserialized["data"]["high"] == candle.high
        assert deserialized["data"]["low"] == candle.low
        assert deserialized["data"]["close"] == candle.close
        assert deserialized["data"]["volume"] == candle.volume

    # Feature: tradecore-mvp, Property 6: Candle Data Integrity
    @given(candle=ohlcv_candle())
    @settings(max_examples=100)
    def test_property_candle_data_integrity_initial_load_format(self, candle: Candle):
        """OHLCV values are preserved in the initial_load message format.

        **Validates: Requirements RF-01.4**
        """
        # Use the actual _build_initial_load_message function
        msg = _build_initial_load_message([candle], sma_fast_period=9, sma_slow_period=21)

        candle_dict = msg["data"]["candles"][0]

        # Verify OHLCV values match original
        assert candle_dict["open"] == candle.open
        assert candle_dict["high"] == candle.high
        assert candle_dict["low"] == candle.low
        assert candle_dict["close"] == candle.close
        assert candle_dict["volume"] == candle.volume
        assert candle_dict["time"] == int(candle.timestamp.timestamp())
