"""A5.1b: MODE=replay de punta a punta con la app real, sin Databento.

- Ningun cliente/conector de Databento se crea (constructores que fallan si se llaman).
- Las senales de la precarga se suprimen; se emiten exactamente los cruces posteriores al inicio.
- /health: 200 con phase=finished al terminar el archivo (fin != error); 503 phase=error si el pipeline muere.
- INITIAL_LOAD_SIGNAL_HISTORY: un WebSocket nuevo recibe en initial_load exactamente las senales emitidas.
- market_open se calcula con la hora de la ultima vela reproducida, no con el reloj real.
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
from starlette.testclient import TestClient

from src.api.app import app
from src.engine.signal_engine import SignalEngine

T0 = datetime(2024, 6, 3, 14, 0, tzinfo=timezone.utc)  # lunes 09:00 en Chicago (mercado regular abierto)
START = T0 + timedelta(minutes=90)


def _closes():
    # 21 planos -> BUY en la precarga (min 21); planos -> SELL al inicio (min 90); planos -> BUY (min 100).
    return [100.0] * 21 + [101.0] * 69 + [99.0] * 10 + [102.0] * 21


def _write_parquet(path):
    closes = _closes()
    idx = pd.DatetimeIndex([T0 + timedelta(minutes=i) for i in range(len(closes))], name="ts_event")
    pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                  "volume": [1] * len(closes)}, index=idx).to_parquet(path)


def _expected_after_start():
    eng, out = SignalEngine(9, 21), []
    closes = _closes()
    for i in range(len(closes)):
        if i + 1 >= 21:
            s = eng.evaluate(closes[: i + 1])
            ts = T0 + timedelta(minutes=i)
            if s is not None and ts >= START:
                out.append((int(ts.timestamp()), s.type.value))
    return out


def _fail(*a, **k):
    raise AssertionError("MODE=replay no debe crear clientes ni conectores de Databento")


def test_replay_mode_end_to_end_without_databento(monkeypatch, tmp_path):
    path = tmp_path / "session.parquet"
    _write_parquet(path)
    monkeypatch.setenv("DATABENTO_API_KEY", "replay-not-used")
    monkeypatch.setenv("MODE", "replay")
    monkeypatch.setenv("REPLAY_FILE", str(path))
    monkeypatch.setenv("REPLAY_START", START.isoformat())
    monkeypatch.setenv("REPLAY_SPEED", "0")
    app_module = sys.modules["src.api.app"]
    expected = _expected_after_start()
    assert len(expected) == 2  # control del escenario: SELL al inicio y BUY despues

    with patch("src.api.app.DabentoConnector", side_effect=_fail), \
         patch("databento.Live", side_effect=_fail), \
         patch("databento.Historical", side_effect=_fail):
        with TestClient(app) as tc:
            deadline = time.monotonic() + 10.0
            while app_module._replay.phase != "finished" and time.monotonic() < deadline:
                time.sleep(0.02)
            assert app_module._replay.phase == "finished"
            health = tc.get("/health")
            with tc.websocket_connect("/ws/chart") as ws:
                msg = json.loads(ws.receive_text())

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "phase": "finished", "mode": "replay"}
    assert msg["type"] == "initial_load"
    assert msg["data"]["market_open"] is True
    got = [(s["time"], s["signal_type"]) for s in msg["data"]["signals"]]
    assert got == expected


def test_replay_pipeline_crash_reports_unhealthy(monkeypatch, tmp_path):
    path = tmp_path / "session.parquet"
    _write_parquet(path)
    monkeypatch.setenv("DATABENTO_API_KEY", "replay-not-used")
    monkeypatch.setenv("MODE", "replay")
    monkeypatch.setenv("REPLAY_FILE", str(path))
    monkeypatch.setenv("REPLAY_START", START.isoformat())
    monkeypatch.setenv("REPLAY_SPEED", "0")
    app_module = sys.modules["src.api.app"]
    from src.pipeline.file_replay_source import FileReplaySource

    async def _broken_replay(self):
        raise RuntimeError("fallo simulado del reproductor")
        yield  # pragma: no cover  (convierte la funcion en generador asincrono)

    monkeypatch.setattr(FileReplaySource, "replay", _broken_replay)
    with TestClient(app) as tc:
        deadline = time.monotonic() + 10.0
        while not app_module._pipeline_task.done() and time.monotonic() < deadline:
            time.sleep(0.02)
        health = tc.get("/health")

    assert health.status_code == 503
    assert health.json() == {"status": "down", "phase": "error", "mode": "replay"}
