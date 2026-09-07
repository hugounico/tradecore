"""DabentoConnector — Wraps Databento Historical client for ohlcv-1m data.

Provides historical candle loading for TradeCore MVP.
Live streaming will be added in Fase B (Task 12).

Requirements: RF-01.1, RF-01.4
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.schemas.candle import Candle

logger = logging.getLogger(__name__)

# Databento stores prices as int64 fixed-point with scale factor 1e-9
FIXED_PRICE_SCALE = 1e9


class DabentoConnector:
    """Wraps Databento Historical and Live clients for ohlcv-1m data.

    Currently implements only the Historical loading part (Fase A).
    Live subscription will be added in Fase B.
    """

    def __init__(
        self,
        api_key: str,
        dataset: str = "GLBX.MDP3",
        symbol: str = "NQ.c.0",
        stype_in: str = "continuous",
        end_offset_minutes: int = 30,
    ) -> None:
        self._api_key = api_key
        self._dataset = dataset
        self._symbol = symbol
        self._stype_in = stype_in
        self._end_offset_minutes = end_offset_minutes
        self._last_candle_time: datetime | None = None

    @property
    def last_candle_time(self) -> datetime | None:
        """Timestamp of the last candle loaded or received."""
        return self._last_candle_time

    async def load_historical(self, count: int = 50) -> list[Candle]:
        """Fetch the most recent `count` 1-minute candles from Historical API.

        Uses schema='ohlcv-1m', stype_in='continuous' for NQ.c.0.
        Returns candles sorted by timestamp ascending.
        On any error (network, auth, timeout), logs the error and returns an empty list.
        """
        try:
            candles = await asyncio.wait_for(
                self._fetch_historical(count),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.error("Historical API request timed out after 30 seconds.")
            return []
        except Exception as exc:
            logger.error("Historical API request failed: %s", exc)
            return []

        # Sort by timestamp ascending
        candles.sort(key=lambda c: c.timestamp)

        if candles:
            self._last_candle_time = candles[-1].timestamp
            logger.info(
                "Loaded %d historical candles (latest: %s).",
                len(candles),
                self._last_candle_time,
            )
        else:
            logger.warning("Historical API returned no candles.")

        return candles

    async def _fetch_historical(self, count: int) -> list[Candle]:
        """Internal: perform the actual Databento Historical API call.

        Runs the blocking Databento client call in a thread executor
        to avoid blocking the async event loop.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._fetch_historical_sync, count)

    def _fetch_historical_sync(self, count: int) -> list[Candle]:
        """Synchronous Historical API call (runs in executor thread)."""
        import databento as db

        client = db.Historical(key=self._api_key)

        # Calculate time range: request extra padding to ensure we get enough bars
        # Markets may have gaps (no trades minutes), so request a wider window
        now = datetime.now(tz=timezone.utc)
        # Subtract end_offset to account for GLBX.MDP3 publication delay (~10-15 min)
        end = now - timedelta(minutes=self._end_offset_minutes)
        padding_factor = 3  # Request 3x the time window to account for gaps
        start = end - timedelta(minutes=count * padding_factor)

        data = client.timeseries.get_range(
            dataset=self._dataset,
            symbols=[self._symbol],
            schema="ohlcv-1m",
            stype_in=self._stype_in,
            start=start.isoformat(),
            end=end.isoformat(),
            limit=count,
        )

        candles: list[Candle] = []
        for record in data:
            candle = self._record_to_candle(record)
            if candle is not None:
                candles.append(candle)

        return candles

    @staticmethod
    def _record_to_candle(record) -> Candle | None:
        """Convert a Databento OHLCVMsg record to a Candle dataclass.

        Handles price conversion from int64 fixed-point (× 1e-9) to float.
        Returns None if the record cannot be converted.
        """
        try:
            # ts_event is the candle open time (start of the 1-min interval)
            # It's typically a nanosecond Unix timestamp (int)
            ts_event = record.ts_event
            if isinstance(ts_event, int):
                timestamp = datetime.fromtimestamp(
                    ts_event / 1e9, tz=timezone.utc
                )
            else:
                timestamp = ts_event

            # Price conversion: Databento int64 prices ÷ 1e9 → float
            open_price = _convert_price(record.open)
            high_price = _convert_price(record.high)
            low_price = _convert_price(record.low)
            close_price = _convert_price(record.close)
            volume = int(record.volume)

            return Candle(
                timestamp=timestamp,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
            )
        except Exception as exc:
            logger.warning("Failed to convert record to Candle: %s", exc)
            return None


def _convert_price(value: int | float) -> float:
    """Convert a Databento fixed-point price to float.

    Databento stores prices as int64 × 1e-9.
    If the value is a large integer (> 1,000,000), it's in raw fixed-point format.
    The .to_df() method converts to float, but iterating records gives raw int64.
    """
    if isinstance(value, int) and abs(value) > 1_000_000:
        return value / FIXED_PRICE_SCALE
    if isinstance(value, float) and abs(value) > 1_000_000:
        return value / FIXED_PRICE_SCALE
    return float(value)
