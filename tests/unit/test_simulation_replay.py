"""Unit tests for SimulationReplay class."""

from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

import pytest

from src.pipeline.simulation_replay import SimulationReplay
from src.schemas.candle import Candle


def _make_candles(count: int) -> list[Candle]:
    return [
        Candle(
            timestamp=datetime(2024, 1, 1, 12, i, tzinfo=timezone.utc),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000 + i,
        )
        for i in range(count)
    ]


class TestSimulationReplayYieldsAtCorrectInterval:
    """Candles yield at correct interval (RF-02.2)."""

    async def test_yields_all_candles_with_sleep_between(self):
        candles = _make_candles(3)
        replay = SimulationReplay(candles, interval=1.0)

        with patch("src.pipeline.simulation_replay.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            received = []
            async for candle in replay.replay():
                received.append(candle)

            assert received == candles
            # sleep called once after each candle (including last)
            assert mock_sleep.call_count == 3
            mock_sleep.assert_called_with(1.0)

    async def test_custom_interval_passed_to_sleep(self):
        candles = _make_candles(2)
        replay = SimulationReplay(candles, interval=0.5)

        with patch("src.pipeline.simulation_replay.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            async for _ in replay.replay():
                pass

            mock_sleep.assert_called_with(0.5)

    async def test_is_replaying_true_during_replay(self):
        candles = _make_candles(2)
        replay = SimulationReplay(candles, interval=0.0)

        with patch("src.pipeline.simulation_replay.asyncio.sleep", new_callable=AsyncMock):
            async for _ in replay.replay():
                assert replay.is_replaying is True

        assert replay.is_replaying is False

    async def test_empty_candles_yields_nothing(self):
        replay = SimulationReplay([], interval=1.0)

        with patch("src.pipeline.simulation_replay.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            received = []
            async for candle in replay.replay():
                received.append(candle)

            assert received == []
            mock_sleep.assert_not_called()


class TestSimulationReplayStopHaltsReplay:
    """Stop halts replay mid-stream (RF-02.2)."""

    async def test_stop_halts_replay_after_first_candle(self):
        candles = _make_candles(5)
        replay = SimulationReplay(candles, interval=0.0)

        with patch("src.pipeline.simulation_replay.asyncio.sleep", new_callable=AsyncMock):
            received = []
            async for candle in replay.replay():
                received.append(candle)
                if len(received) == 1:
                    replay.stop()

            # Should have gotten only 1 candle (stop checked at top of next iteration)
            assert len(received) == 1
            assert received[0] == candles[0]

    async def test_is_replaying_false_after_stop(self):
        candles = _make_candles(3)
        replay = SimulationReplay(candles, interval=0.0)

        with patch("src.pipeline.simulation_replay.asyncio.sleep", new_callable=AsyncMock):
            async for candle in replay.replay():
                replay.stop()

        assert replay.is_replaying is False
