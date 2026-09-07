"""Unit tests for SignalEngine class."""

import pytest

from src.engine.signal_engine import SignalEngine
from src.schemas.signal import Signal, SignalType


class TestSignalEngineComputeSMA:
    """Test SMA matches manual calculation (RF-03.4)."""

    def test_sma_matches_manual_calculation(self):
        prices = [10.0, 20.0, 30.0, 40.0, 50.0]
        period = 3
        expected = (30.0 + 40.0 + 50.0) / 3
        assert SignalEngine.compute_sma(prices, period) == expected

    def test_sma_uses_last_n_values(self):
        prices = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
        period = 5
        expected = sum(prices[-5:]) / 5
        assert SignalEngine.compute_sma(prices, period) == expected

    def test_sma_full_list_equals_mean(self):
        prices = [100.0, 200.0, 300.0]
        period = 3
        expected = (100.0 + 200.0 + 300.0) / 3
        assert SignalEngine.compute_sma(prices, period) == expected


class TestSignalEngineNoSignalInsufficientData:
    """Test no signal with < 21 candles (RF-03.3)."""

    def test_returns_none_with_zero_candles(self):
        engine = SignalEngine()
        assert engine.evaluate([]) is None

    def test_returns_none_with_20_candles(self):
        engine = SignalEngine()
        closes = [100.0] * 20
        assert engine.evaluate(closes) is None

    def test_returns_none_with_1_candle(self):
        engine = SignalEngine()
        assert engine.evaluate([50.0]) is None


class TestSignalEngineNoSignalNoCross:
    """Test no signal when SMAs don't cross (RF-03.2)."""

    def test_no_signal_when_fast_stays_above_slow(self):
        engine = SignalEngine(fast_period=9, slow_period=21)
        # First call: establish state where fast > slow
        # Use high values at the end so SMA(9) > SMA(21)
        closes_1 = [50.0] * 12 + [200.0] * 9
        result_1 = engine.evaluate(closes_1)
        # First call never produces a signal (no previous state)
        assert result_1 is None

        # Second call: keep fast above slow (same pattern)
        closes_2 = [50.0] * 12 + [200.0] * 9
        result_2 = engine.evaluate(closes_2)
        assert result_2 is None

    def test_no_signal_when_fast_stays_below_slow(self):
        engine = SignalEngine(fast_period=9, slow_period=21)
        # First call: establish state where fast < slow
        # Use low values at the end so SMA(9) < SMA(21)
        closes_1 = [200.0] * 12 + [50.0] * 9
        result_1 = engine.evaluate(closes_1)
        assert result_1 is None

        # Second call: keep fast below slow (same pattern)
        closes_2 = [200.0] * 12 + [50.0] * 9
        result_2 = engine.evaluate(closes_2)
        assert result_2 is None


class TestSignalEngineBuyCrossover:
    """Test exact crossover BUY signal (RF-03.1)."""

    def test_buy_signal_when_fast_crosses_above_slow(self):
        engine = SignalEngine(fast_period=9, slow_period=21)

        # First evaluate: set state where fast <= slow
        # SMA(9) of last 9 values <= SMA(21) of last 21 values
        closes_before = [100.0] * 21  # Both SMAs equal (100.0)
        result = engine.evaluate(closes_before)
        assert result is None  # No signal on first call

        # Second evaluate: fast crosses above slow
        # Make last 9 values high so SMA(9) > SMA(21)
        closes_after = [90.0] * 12 + [110.0] * 9
        result = engine.evaluate(closes_after)

        assert result is not None
        assert result.type == SignalType.BUY
        assert result.price == closes_after[-1]
        assert result.sma_fast > result.sma_slow


class TestSignalEngineSellCrossover:
    """Test exact crossover SELL signal (RF-03.1)."""

    def test_sell_signal_when_fast_crosses_below_slow(self):
        engine = SignalEngine(fast_period=9, slow_period=21)

        # First evaluate: set state where fast >= slow
        # All equal → fast == slow
        closes_before = [100.0] * 21
        result = engine.evaluate(closes_before)
        assert result is None  # No signal on first call

        # Second evaluate: fast crosses below slow
        # Make last 9 values low so SMA(9) < SMA(21)
        closes_after = [110.0] * 12 + [90.0] * 9
        result = engine.evaluate(closes_after)

        assert result is not None
        assert result.type == SignalType.SELL
        assert result.price == closes_after[-1]
        assert result.sma_fast < result.sma_slow
