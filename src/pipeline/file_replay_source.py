"""A5.1b: FileReplaySource — reproduce velas grabadas como si llegaran en vivo (staging y tests).

Contrato usado por app.py (el mismo subconjunto que consume de RecoveringLiveSource):
- replay(): iterador asincrono de Candle.
- is_replay_complete: False durante la precarga (las senales se suprimen), True desde la primera vela
  con hora >= start.
- replay_completed_event: se activa en ese mismo momento.
- phase: "bootstrapping" -> "live" -> "finished" (fin del archivo; NO es un error).
- stop(): sincrono e idempotente; el iterador termina antes de la siguiente vela.

Reglas:
- Entrada estricta: horas con zona horaria y estrictamente crecientes (sin duplicados). Nada se ordena ni
  se repara: un archivo defectuoso falla con ValueError.
- Precarga por TIEMPO: velas con hora en [start - warmup, start). Las anteriores se ignoran.
- Reloj: speed <= 0 => nunca espera (solo cede el turno al event loop para que la app siga atendiendo).
  speed > 0 => antes de cada vela posterior a la precarga espera (hora - hora anterior) / speed segundos,
  con tope max_gap_seconds (pausas de CME y fines de semana no congelan la pantalla).
  Nunca se inventan minutos faltantes.
- Nunca se conecta a Databento ni a la red.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from src.schemas.candle import Candle

_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def load_candles_parquet(path: str) -> list[Candle]:
    """Lee un parquet OHLCV de 1 minuto (indice ts_event con zona horaria) SIN reordenarlo.

    Falla con ValueError si el indice no es de fechas con zona horaria, no es estrictamente creciente,
    tiene horas repetidas, faltan columnas OHLCV o hay valores vacios.
    """
    import pandas as pd  # import local: solo el modo replay lo necesita

    df = pd.read_parquet(path)
    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"faltan columnas OHLCV: {missing}")
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise ValueError("el indice no es de fechas")
    if idx.tz is None:
        raise ValueError("el indice no tiene zona horaria")
    if not (idx.is_monotonic_increasing and idx.is_unique):
        raise ValueError("el indice no es estrictamente creciente (desorden o duplicados)")
    df = df[list(_REQUIRED_COLUMNS)]
    if df.isna().any().any():
        raise ValueError("hay valores OHLCV vacios")
    idx = idx.tz_convert("UTC")
    return [
        Candle(timestamp=ts.to_pydatetime(), open=float(o), high=float(h), low=float(lo),
               close=float(c), volume=int(v))
        for ts, o, h, lo, c, v in zip(idx, df["open"], df["high"], df["low"], df["close"], df["volume"])
    ]


class FileReplaySource:
    def __init__(
        self,
        candles: list[Candle],
        start: datetime,
        warmup: timedelta = timedelta(minutes=90),
        speed: float = 60.0,
        max_gap_seconds: float = 5.0,
        sleep=asyncio.sleep,
    ) -> None:
        if start.tzinfo is None:
            raise ValueError("start debe incluir zona horaria")
        prev = None
        for c in candles:
            if c.timestamp.tzinfo is None:
                raise ValueError(f"vela sin zona horaria: {c.timestamp}")
            if prev is not None and c.timestamp <= prev:
                raise ValueError(f"velas fuera de orden o duplicadas en {c.timestamp}")
            prev = c.timestamp
        start = start.astimezone(timezone.utc)
        self._warmup = [c for c in candles if start - warmup <= c.timestamp < start]
        self._session = [c for c in candles if c.timestamp >= start]
        self._speed = speed
        self._max_gap = max_gap_seconds
        self._sleep = sleep
        self._stopped = False
        self._phase = "bootstrapping"
        self._complete = False
        self._event = asyncio.Event()

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def is_replay_complete(self) -> bool:
        return self._complete

    @property
    def replay_completed_event(self) -> asyncio.Event:
        return self._event

    def stop(self) -> None:
        self._stopped = True

    def _mark_live(self) -> None:
        self._complete = True
        self._phase = "live"
        self._event.set()

    async def replay(self) -> AsyncIterator[Candle]:
        for c in self._warmup:
            if self._stopped:
                return
            yield c
        self._mark_live()
        prev = self._warmup[-1].timestamp if self._warmup else None
        for c in self._session:
            if self._stopped:
                return
            if self._speed > 0:
                if prev is not None:
                    await self._sleep(min((c.timestamp - prev).total_seconds() / self._speed, self._max_gap))
            else:
                await asyncio.sleep(0)  # cede el turno; no es una espera
            if self._stopped:
                return
            prev = c.timestamp
            yield c
        self._phase = "finished"
