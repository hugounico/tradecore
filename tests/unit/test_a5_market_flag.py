"""A5.1b: _market_open_flag() — en live y simulation conserva el comportamiento previo
(is_regular_market_open con el reloj real); en replay usa la hora de la ultima vela reproducida."""

import sys
from datetime import datetime, timedelta, timezone

import pytest

from src.schemas.candle import Candle


class _S:
    def __init__(self, mode):
        self.mode = mode


def _spy(monkeypatch, app_module):
    calls = []

    def fake(ts):
        calls.append(ts)
        return "SENTINEL"

    monkeypatch.setattr(app_module, "is_regular_market_open", fake)
    return calls


@pytest.mark.parametrize("mode", ["live", "simulation"])
def test_m1_live_and_simulation_use_the_real_clock(monkeypatch, mode):
    app_module = sys.modules.get("src.api.app") or __import__("src.api.app", fromlist=["x"])
    calls = _spy(monkeypatch, app_module)
    monkeypatch.setattr(app_module, "_settings", _S(mode))
    before = datetime.now(timezone.utc)
    assert app_module._market_open_flag() == "SENTINEL"
    after = datetime.now(timezone.utc)
    assert len(calls) == 1 and before <= calls[0] <= after


def test_m2_replay_uses_last_replayed_candle(monkeypatch):
    app_module = sys.modules.get("src.api.app") or __import__("src.api.app", fromlist=["x"])
    from src.pipeline.candle_buffer import CandleBuffer

    calls = _spy(monkeypatch, app_module)
    buf = CandleBuffer()
    ts = datetime(2024, 6, 3, 15, 30, tzinfo=timezone.utc)
    buf.append(Candle(timestamp=ts, open=1.0, high=1.0, low=1.0, close=1.0, volume=1))
    monkeypatch.setattr(app_module, "_settings", _S("replay"))
    monkeypatch.setattr(app_module, "_buffer", buf)
    assert app_module._market_open_flag() is True
    assert calls == [ts]
    monkeypatch.setattr(app_module, "_buffer", CandleBuffer())
    assert app_module._market_open_flag() is False
