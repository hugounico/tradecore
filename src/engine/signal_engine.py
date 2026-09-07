"""Signal engine implementing SMA crossover detection."""

from datetime import datetime, timezone

from src.schemas.signal import Signal, SignalType


class SignalEngine:
    """Generates BUY/SELL signals based on SMA(fast) / SMA(slow) crossover.

    A BUY signal is emitted when SMA(fast) crosses above SMA(slow).
    A SELL signal is emitted when SMA(fast) crosses below SMA(slow).
    """

    def __init__(self, fast_period: int = 9, slow_period: int = 21) -> None:
        self.fast_period = fast_period
        self.slow_period = slow_period
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    @staticmethod
    def compute_sma(prices: list[float], period: int) -> float:
        """Compute the Simple Moving Average over the last `period` values.

        Args:
            prices: List of price values (must have at least `period` elements).
            period: Number of values to average.

        Returns:
            Arithmetic mean of the last `period` values.
        """
        return sum(prices[-period:]) / period

    def evaluate(self, closes: list[float]) -> Signal | None:
        """Evaluate close prices and return a signal if a crossover occurred.

        Args:
            closes: List of close prices ordered chronologically.

        Returns:
            A Signal (BUY or SELL) if a crossover is detected, otherwise None.
        """
        if len(closes) < self.slow_period:
            return None

        current_fast = self.compute_sma(closes, self.fast_period)
        current_slow = self.compute_sma(closes, self.slow_period)

        signal: Signal | None = None

        if self._prev_fast is not None and self._prev_slow is not None:
            # Detect crossover: fast crossed above slow → BUY
            if self._prev_fast <= self._prev_slow and current_fast > current_slow:
                signal = Signal(
                    type=SignalType.BUY,
                    timestamp=datetime.now(timezone.utc),
                    price=closes[-1],
                    sma_fast=current_fast,
                    sma_slow=current_slow,
                )
            # Detect crossover: fast crossed below slow → SELL
            elif self._prev_fast >= self._prev_slow and current_fast < current_slow:
                signal = Signal(
                    type=SignalType.SELL,
                    timestamp=datetime.now(timezone.utc),
                    price=closes[-1],
                    sma_fast=current_fast,
                    sma_slow=current_slow,
                )

        # Update stored previous values
        self._prev_fast = current_fast
        self._prev_slow = current_slow

        return signal
