"""Property-based tests for SignalEngine using Hypothesis.

Validates:
- RF-03.1: SMA recalculation on completed candle close
- RF-03.2: BUY signal when SMA(9) crosses above SMA(21)
- RF-03.3: SELL signal when SMA(9) crosses below SMA(21)
- RF-03.4: No signal when insufficient data (< 21 candles)
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.engine.signal_engine import SignalEngine
from src.schemas.signal import SignalType


# --- Strategies ---

# Realistic price strategy: positive floats in a reasonable range for NQ futures
price_st = st.floats(min_value=1.0, max_value=50000.0, allow_nan=False, allow_infinity=False)


# Feature: tradecore-mvp, Property 1: SMA Crossover Signal Correctness
class TestSMACrossoverSignalCorrectness:
    """Generate close prices with known previous SMA state; verify BUY when
    SMA(9) crosses above SMA(21), SELL when crosses below, None when no transition.

    **Validates: Requirements 3.2, 3.3**
    """

    @given(
        closes=st.lists(price_st, min_size=21, max_size=100),
        prev_fast=price_st,
        prev_slow=price_st,
    )
    @settings(max_examples=100)
    def test_buy_signal_on_upward_crossover(
        self, closes: list[float], prev_fast: float, prev_slow: float
    ) -> None:
        """When prev_fast <= prev_slow and current_fast > current_slow, a BUY signal is produced."""
        engine = SignalEngine(fast_period=9, slow_period=21)

        # Compute what the current SMAs would be
        current_fast = sum(closes[-9:]) / 9
        current_slow = sum(closes[-21:]) / 21

        # Set preconditions: previous fast was at or below slow
        assume(prev_fast <= prev_slow)
        # And current fast is above current slow (crossover upward)
        assume(current_fast > current_slow)

        # Inject previous state
        engine._prev_fast = prev_fast
        engine._prev_slow = prev_slow

        signal = engine.evaluate(closes)

        assert signal is not None
        assert signal.type == SignalType.BUY
        assert signal.price == closes[-1]

    @given(
        closes=st.lists(price_st, min_size=21, max_size=100),
        prev_fast=price_st,
        prev_slow=price_st,
    )
    @settings(max_examples=100)
    def test_sell_signal_on_downward_crossover(
        self, closes: list[float], prev_fast: float, prev_slow: float
    ) -> None:
        """When prev_fast >= prev_slow and current_fast < current_slow, a SELL signal is produced."""
        engine = SignalEngine(fast_period=9, slow_period=21)

        # Compute what the current SMAs would be
        current_fast = sum(closes[-9:]) / 9
        current_slow = sum(closes[-21:]) / 21

        # Set preconditions: previous fast was at or above slow
        assume(prev_fast >= prev_slow)
        # And current fast is below current slow (crossover downward)
        assume(current_fast < current_slow)

        # Inject previous state
        engine._prev_fast = prev_fast
        engine._prev_slow = prev_slow

        signal = engine.evaluate(closes)

        assert signal is not None
        assert signal.type == SignalType.SELL
        assert signal.price == closes[-1]

    @given(
        closes=st.lists(price_st, min_size=21, max_size=100),
        prev_fast=price_st,
        prev_slow=price_st,
    )
    @settings(max_examples=100)
    def test_no_signal_when_no_crossover(
        self, closes: list[float], prev_fast: float, prev_slow: float
    ) -> None:
        """When there is no transition (both sides same relative position), no signal is produced."""
        engine = SignalEngine(fast_period=9, slow_period=21)

        # Compute what the current SMAs would be
        current_fast = sum(closes[-9:]) / 9
        current_slow = sum(closes[-21:]) / 21

        # No crossover: fast was above and stays above, or fast was below and stays below
        # Case 1: prev_fast > prev_slow AND current_fast > current_slow (no cross)
        # Case 2: prev_fast < prev_slow AND current_fast < current_slow (no cross)
        same_side = (prev_fast > prev_slow and current_fast > current_slow) or (
            prev_fast < prev_slow and current_fast < current_slow
        )
        assume(same_side)

        # Inject previous state
        engine._prev_fast = prev_fast
        engine._prev_slow = prev_slow

        signal = engine.evaluate(closes)

        assert signal is None


# Feature: tradecore-mvp, Property 2: No Signal on Insufficient Data
class TestNoSignalOnInsufficientData:
    """Generate close price lists with length < 21; verify evaluate always returns None.

    **Validates: Requirements 3.4**
    """

    @given(closes=st.lists(price_st, min_size=0, max_size=20))
    @settings(max_examples=100)
    def test_no_signal_with_insufficient_data(self, closes: list[float]) -> None:
        """SignalEngine.evaluate returns None when len(closes) < 21 (slow_period)."""
        engine = SignalEngine(fast_period=9, slow_period=21)

        signal = engine.evaluate(closes)

        assert signal is None


# Feature: tradecore-mvp, Property 3: SMA Computation Correctness
class TestSMAComputationCorrectness:
    """Generate lists of N floats where N >= period; verify compute_sma equals
    arithmetic mean of last `period` values.

    **Validates: Requirements 3.1**
    """

    @given(
        prices=st.lists(price_st, min_size=1, max_size=200),
        period=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=100)
    def test_sma_equals_arithmetic_mean(self, prices: list[float], period: int) -> None:
        """compute_sma(prices, period) equals the arithmetic mean of last `period` values."""
        assume(len(prices) >= period)

        result = SignalEngine.compute_sma(prices, period)
        expected = sum(prices[-period:]) / period

        assert abs(result - expected) < 1e-9
