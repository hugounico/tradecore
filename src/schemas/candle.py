"""Data model for a completed 1-minute OHLCV candle from Databento."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Candle:
    """One completed 1-minute OHLCV bar from Databento ohlcv-1m schema."""

    timestamp: datetime  # UTC, start of the minute (from ts_event)
    open: float
    high: float
    low: float
    close: float
    volume: int
