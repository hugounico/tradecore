"""SimulationReplay — replays historical candles at a configurable interval,
simulating live market behavior for Fase A pipeline validation."""

import asyncio
from collections.abc import AsyncIterator

from src.schemas.candle import Candle


class SimulationReplay:
    """Replays a list of candles at 1-second intervals, simulating live market.

    Used in Fase A to validate the full pipeline without a live subscription.
    """

    def __init__(self, candles: list[Candle], interval: float = 1.0) -> None:
        self._candles = candles
        self._interval = interval
        self._is_replaying = False
        self._stopped = False

    @property
    def is_replaying(self) -> bool:
        """True while the replay generator is actively yielding candles."""
        return self._is_replaying

    def stop(self) -> None:
        """Signal the replay loop to stop after the current iteration."""
        self._stopped = True

    async def replay(self) -> AsyncIterator[Candle]:
        """Yield candles one by one with `interval` seconds between each.

        Feeds into the same pipeline as live data.
        """
        self._is_replaying = True
        self._stopped = False
        try:
            for candle in self._candles:
                if self._stopped:
                    break
                yield candle
                await asyncio.sleep(self._interval)
        finally:
            self._is_replaying = False
