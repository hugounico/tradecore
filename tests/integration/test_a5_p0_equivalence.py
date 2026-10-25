"""A5.1b / P0: el camino de produccion reproduce EXACTAMENTE las senales del laboratorio M0.

Corre FileReplaySource + _processing_loop (CandleBuffer y SignalEngine reales) sobre TODO el historico
y compara una huella de las senales con la de M0 (calculada por Claude y por ChatGPT por separado).
Los datos de CME no estan en el repo: el test lee la ruta de TRADECORE_HISTORICAL_PARQUET y se omite
si no existe. Debe correrse localmente en cada PR del Bloque A (tarda ~1-2 min).
"""

import hashlib
import os
import sys

import pytest

PARQUET = os.environ.get("TRADECORE_HISTORICAL_PARQUET", "")
PARQUET_SHA256 = "5be681c5f1ac4449d6dd347c7494157d2a0573f4f4d62fea5a5aee2a41eb9056"
M0_SIGNAL_COUNT = 56489
M0_SIGNALS_SHA256 = "5c27b30b8ade9d982b2f7a857347416d7a8a3513679ad09bbb7a6253eee0d4d1"


class _CollectingPusher:
    def __init__(self):
        self.signals = []

    async def queue_candle(self, candle):
        pass

    async def queue_sma_update(self, *args):
        pass

    async def queue_signal(self, signal):
        self.signals.append(signal)


class _Settings:
    sma_fast_period = 9
    sma_slow_period = 21
    mode = "replay"


@pytest.mark.skipif(not os.path.isfile(PARQUET), reason="TRADECORE_HISTORICAL_PARQUET no definido o inexistente")
async def test_p0_production_path_reproduces_m0_signals(monkeypatch):
    with open(PARQUET, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == PARQUET_SHA256

    app_module = sys.modules.get("src.api.app") or __import__("src.api.app", fromlist=["x"])
    from collections import deque

    from src.engine.signal_engine import SignalEngine
    from src.pipeline.candle_buffer import CandleBuffer
    from src.pipeline.file_replay_source import FileReplaySource, load_candles_parquet

    candles = load_candles_parquet(PARQUET)
    src = FileReplaySource(candles, start=candles[0].timestamp, speed=0, sleep=_no_sleep)
    pusher = _CollectingPusher()
    monkeypatch.setattr(app_module, "_replay", src)
    monkeypatch.setattr(app_module, "_buffer", CandleBuffer())
    monkeypatch.setattr(app_module, "_engine", SignalEngine(9, 21))
    monkeypatch.setattr(app_module, "_pusher", pusher)
    monkeypatch.setattr(app_module, "_settings", _Settings())
    monkeypatch.setattr(app_module, "_signal_history", deque(maxlen=10))

    await app_module._processing_loop(suppress_signals=lambda: not src.is_replay_complete)

    lines = "".join(f"{int(s.timestamp.timestamp())},{s.type.value},{s.price:.2f}\n" for s in pusher.signals)
    assert len(pusher.signals) == M0_SIGNAL_COUNT
    assert hashlib.sha256(lines.encode()).hexdigest() == M0_SIGNALS_SHA256


async def _no_sleep(_seconds):
    return None
