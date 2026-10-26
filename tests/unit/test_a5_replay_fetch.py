"""A5.1c-1: el archivo de replay puede venir de un enlace firmado (https) con huella obligatoria; la firma del enlace
no llega al log ni a ningun mensaje de error, tambien cuando la descarga falla."""

import hashlib
import http.server
import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from starlette.testclient import TestClient

from src.api.app import app

T0 = datetime(2024, 6, 3, 14, 0, tzinfo=timezone.utc)
START = T0 + timedelta(minutes=90)
SECRET = "X-Amz-Signature=abc123secreto"


def _fetch(*args):
    from src.pipeline.file_replay_source import fetch_replay_file  # dentro del test: rojo limpio sin la funcion

    return fetch_replay_file(*args)


def _parquet(path):
    closes = [100.0] * 21 + [101.0] * 69 + [99.0] * 10
    idx = pd.DatetimeIndex([T0 + timedelta(minutes=i) for i in range(len(closes))], name="ts_event")
    pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1] * len(closes)},
                 index=idx).to_parquet(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def served(tmp_path):
    """Sirve tmp_path por HTTP local (127.0.0.1, puerto libre) mientras dura el test."""
    sha = _parquet(tmp_path / "s.parquet")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(tmp_path), **k)

        def do_GET(self):
            if self.path.startswith("/forbidden"):
                self.send_error(403, "Forbidden")
                return
            super().do_GET()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/s.parquet?{SECRET}", sha, tmp_path / "s.parquet"
    srv.shutdown()


def test_f1_local_path_is_returned_unchanged():
    assert _fetch("data/x.parquet", "") == "data/x.parquet"


def test_f2_url_requires_sha256():
    with pytest.raises(ValueError):
        _fetch("https://example.invalid/x.parquet", "")


def test_f2b_plain_http_outside_localhost_is_rejected():
    with pytest.raises(ValueError, match="https"):
        _fetch(f"http://example.invalid/x.parquet?{SECRET}", "0" * 64)


def test_f3_url_with_matching_sha_downloads_same_bytes(served):
    url, sha, original = served
    local = _fetch(url, sha.upper())
    with open(local, "rb") as f:
        assert f.read() == original.read_bytes()


def test_f4_url_with_wrong_sha_is_rejected(served):
    url, _sha, _ = served
    with pytest.raises(ValueError):
        _fetch(url, "0" * 64)


def test_f4b_failed_download_never_exposes_the_signature(served, caplog):
    url, sha, _ = served
    bad = url.replace("/s.parquet?", "/forbidden.parquet?")
    with pytest.raises(ValueError) as info:
        _fetch(bad, sha)
    assert "403" in str(info.value)
    assert "X-Amz-Signature" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert "X-Amz-Signature" not in caplog.text


def test_f5_replay_mode_from_url_runs_and_never_logs_the_signature(served, monkeypatch, caplog):
    url, sha, _ = served
    monkeypatch.setenv("DATABENTO_API_KEY", "replay-not-used")
    monkeypatch.setenv("MODE", "replay")
    monkeypatch.setenv("REPLAY_FILE", url)
    monkeypatch.setenv("REPLAY_SHA256", sha)
    monkeypatch.setenv("REPLAY_START", START.isoformat())
    monkeypatch.setenv("REPLAY_SPEED", "0")
    app_module = sys.modules["src.api.app"]
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as tc:
        deadline = time.monotonic() + 10.0
        while app_module._replay.phase != "finished" and time.monotonic() < deadline:
            time.sleep(0.02)
        health = tc.get("/health")
        with tc.websocket_connect("/ws/chart") as ws:
            msg = json.loads(ws.receive_text())
    assert health.json() == {"status": "ok", "phase": "finished", "mode": "replay"}
    assert len(msg["data"]["candles"]) > 0
    assert "Replay started" in caplog.text
    assert "X-Amz-Signature" not in caplog.text
