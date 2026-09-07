"""In-memory ordered buffer of completed 1-minute OHLCV candles."""

from datetime import datetime

from src.schemas.candle import Candle


class CandleBuffer:
    """Buffer of completed candles with max capacity.

    Maintains an ordered list of completed candles (ascending by timestamp).
    Discards duplicates (candles with timestamp <= last stored timestamp)
    and drops oldest candles when max_size is exceeded.
    """

    def __init__(self, max_size: int = 500):
        self._max_size = max_size
        self._candles: list[Candle] = []

    def append(self, candle: Candle) -> None:
        """Append a completed candle to the buffer.

        Deduplication: discards candles with timestamp <= last_timestamp().
        Eviction: drops the oldest candle when max_size is exceeded.
        """
        last_ts = self.last_timestamp()
        if last_ts is not None and candle.timestamp <= last_ts:
            return

        self._candles.append(candle)

        if len(self._candles) > self._max_size:
            self._candles = self._candles[-self._max_size:]

    def get_closes(self, n: int) -> list[float]:
        """Return the last `n` close prices as a list of floats.

        If fewer than `n` candles are available, returns all close prices.
        """
        return [c.close for c in self._candles[-n:]]

    def get_all(self) -> list[Candle]:
        """Return all candles in the buffer, ordered by timestamp ascending."""
        return list(self._candles)

    def last_timestamp(self) -> datetime | None:
        """Return the timestamp of the most recent candle, or None if empty."""
        if not self._candles:
            return None
        return self._candles[-1].timestamp

    def __len__(self) -> int:
        """Return the number of candles currently in the buffer."""
        return len(self._candles)
