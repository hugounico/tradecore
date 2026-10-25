"""A5.1b: contrato de FileReplaySource y load_candles_parquet (precarga por tiempo, reloj, fin de archivo,
stop, entrada estricta: nada se ordena ni se repara)."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.schemas.candle import Candle

START = datetime(2024, 6, 3, 15, 30, tzinfo=timezone.utc)


def FileReplaySource(*args, **kwargs):
    # import dentro de cada test: sin el modulo, los tests FALLAN (no rompen la coleccion).
    from src.pipeline.file_replay_source import FileReplaySource as _Src
    return _Src(*args, **kwargs)


def _c(offset_min: int, close: float = 100.0) -> Candle:
    return Candle(timestamp=START + timedelta(minutes=offset_min), open=close, high=close,
                  low=close, close=close, volume=1)


class _Sleeps:
    def __init__(self):
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)


async def _collect(src):
    out = []
    async for c in src.replay():
        out.append((int((c.timestamp - START).total_seconds() // 60), src.is_replay_complete, src.phase))
    return out


async def test_r1_warmup_is_a_90_minute_time_window_and_signals_unlock_at_start():
    candles = [_c(m) for m in (-120, -91, -90, -30, -1, 0, 1)]
    src = FileReplaySource(candles, START, speed=0, sleep=_Sleeps())
    got = await _collect(src)
    assert got == [(-90, False, "bootstrapping"), (-30, False, "bootstrapping"), (-1, False, "bootstrapping"),
                   (0, True, "live"), (1, True, "live")]
    assert src.phase == "finished"
    assert src.replay_completed_event.is_set()


async def test_r2_pacing_uses_time_difference_over_speed_with_gap_cap():
    sleeps = _Sleeps()
    src = FileReplaySource([_c(-1), _c(0), _c(1), _c(61)], START, speed=60, max_gap_seconds=5, sleep=sleeps)
    await _collect(src)
    assert sleeps.calls == [1.0, 1.0, 5]  # 60 s/60, 60 s/60, min(3600 s/60, 5)


async def test_r3_speed_zero_never_calls_the_sleeper():
    sleeps = _Sleeps()
    src = FileReplaySource([_c(-1), _c(0), _c(500)], START, speed=0, sleep=sleeps)
    await _collect(src)
    assert sleeps.calls == []


async def test_r4_empty_warmup_unlocks_before_first_candle():
    src = FileReplaySource([_c(0), _c(1)], START, speed=0, sleep=_Sleeps())
    assert await _collect(src) == [(0, True, "live"), (1, True, "live")]


async def test_r5_stop_ends_replay_before_next_candle():
    src = FileReplaySource([_c(-1), _c(0), _c(1), _c(2)], START, speed=0, sleep=_Sleeps())
    seen = []
    async for c in src.replay():
        seen.append(c.timestamp)
        if c.timestamp == START:
            src.stop()
            src.stop()  # idempotente
    assert seen == [START - timedelta(minutes=1), START]
    assert src.phase != "finished"


def test_r6_naive_start_is_rejected():
    with pytest.raises(ValueError):
        FileReplaySource([_c(0)], START.replace(tzinfo=None))


@pytest.mark.parametrize("offsets", [[0, 2, 1], [0, 1, 1]], ids=["out_of_order", "duplicate"])
def test_r7_unordered_or_duplicate_candles_are_rejected(offsets):
    with pytest.raises(ValueError):
        FileReplaySource([_c(m) for m in offsets], START)


def test_r8_naive_candle_is_rejected():
    naive = Candle(timestamp=START.replace(tzinfo=None), open=1.0, high=1.0, low=1.0, close=1.0, volume=1)
    with pytest.raises(ValueError):
        FileReplaySource([naive], START)


async def test_r9_start_in_another_time_zone_is_the_same_instant():
    start_santiago = START.astimezone(ZoneInfo("America/Santiago"))
    src = FileReplaySource([_c(-1), _c(0)], start_santiago, speed=0, sleep=_Sleeps())
    assert await _collect(src) == [(-1, False, "bootstrapping"), (0, True, "live")]


def _load(df, tmp_path):
    from src.pipeline.file_replay_source import load_candles_parquet

    path = tmp_path / "x.parquet"
    df.to_parquet(path)
    return load_candles_parquet(str(path))


def _frame(minutes, tz="UTC", drop=None):
    idx = pd.DatetimeIndex([pd.Timestamp("2024-06-03 15:30", tz=tz) + pd.Timedelta(minutes=m) for m in minutes],
                           name="ts_event")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}, index=idx)
    return df.drop(columns=[drop]) if drop else df


def test_l1_loader_keeps_original_order_and_converts_to_utc(tmp_path):
    got = _load(_frame([0, 1, 5], tz="America/Chicago"), tmp_path)
    assert [c.timestamp.utcoffset() for c in got] == [timedelta(0)] * 3
    assert [(c.timestamp - got[0].timestamp) for c in got] == [timedelta(0), timedelta(minutes=1), timedelta(minutes=5)]


@pytest.mark.parametrize(
    "build",
    [
        lambda: _frame([0, 2, 1]),
        lambda: _frame([0, 1, 1]),
        lambda: _frame([0, 1], tz=None),
        lambda: _frame([0, 1], drop="volume"),
        lambda: _frame([0, 1]).reset_index(drop=True),
        lambda: _frame([0, 1]).assign(close=[1.0, float("nan")]),
    ],
    ids=["out_of_order", "duplicate", "naive_index", "missing_column", "not_datetime_index", "nan_value"],
)
def test_l2_loader_rejects_invalid_files(build, tmp_path):
    with pytest.raises(ValueError):
        _load(build(), tmp_path)
