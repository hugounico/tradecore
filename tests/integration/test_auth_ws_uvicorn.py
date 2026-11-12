"""Prueba de integración T5 (STOP del Design §9.3/§14): AuthGate WebSocket con uvicorn real.

Levanta un servidor uvicorn real (implementación WebSocket por defecto, como producción) con
el endpoint tipo /ws/chart envuelto por AuthGate y un ThrottledPusher real que emite ticks.
Verifica el cierre real 4401 al vencer el plazo, que no llegan datos tras el plazo, y que la
conexión se limpia. También los rechazos 4401 (sin cookie) y 4403 (origin ajeno).

Sin src.api.app, Databento ni AWS. Solo loopback 127.0.0.1. Nunca valores reales.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import time

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect

from src.api.auth import middleware as mw
from src.api.auth.config import validate_auth_settings
from src.api.auth.session import SessionCodec
from src.api.throttled_pusher import ThrottledPusher

SUPABASE_URL = "https://svvzrmhvsjalhpitjcvy.supabase.co"
PUBKEY = "sb_publishable_SeQlUSsK9WIH8Gag8NjyXA"
ORIGIN_OK = "https://tr-abc.ecs.us-east-1.on.aws"
ORIGIN_OTHER = "https://malo.example"
SESSION_KEY = "a" * 64
JWK_ES256 = {
    "alg": "ES256", "crv": "P-256", "kty": "EC", "use": "sig", "key_ops": ["verify"],
    "kid": "k1",
    "x": "JPdlg8l77lcPf4wxSMnACUPQ616wVqX2Uu9Q6ZJGLqU",
    "y": "AkjFprNzyDruo5hL-u0_gZ15ELaRcmKb1rGj-ckl8oE",
}
GOOD_JWKS = json.dumps({"keys": [JWK_ES256]})
SUB_OK = "11111111-2222-3333-4444-555555555555"


def _cfg(ttl: int):
    raw = {
        "TC_AUTH_SUPABASE_URL": SUPABASE_URL,
        "TC_AUTH_PUBLISHABLE_KEY": PUBKEY,
        "TC_AUTH_JWKS_JSON": GOOD_JWKS,
        "TC_AUTH_ALLOWED_SUBS": SUB_OK,
        "TC_AUTH_ALLOWED_ORIGINS": ORIGIN_OK,
        "TC_SESSION_KEY": SESSION_KEY,
        "TC_SESSION_EPOCH": "1",
        "TC_SESSION_TTL_SECONDS": str(ttl),
    }
    cfg = validate_auth_settings(raw)
    assert cfg.status == "OK", cfg.invalid_fields
    return cfg


def _make_endpoint(pusher: ThrottledPusher):
    async def chart_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        pusher.add_connection(ws)
        try:
            await pusher.send_direct(ws, json.dumps({"type": "initial_load", "candles": []}))
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 (endpoint de prueba)
            pass
        finally:
            pusher.remove_connection(ws)
            logging.getLogger("src.api.auth.ws_uvicorn_test").info(
                "WebSocket connection cleaned up."
            )
    return chart_endpoint


async def _connect_expect_close(url: str, headers: dict) -> int | None:
    """Conecta (el gate hace accept+close) y devuelve el close_code; no deben llegar datos."""
    got_data = False
    async with connect(url, additional_headers=headers) as cli:
        try:
            await cli.recv()
            got_data = True
        except Exception:
            pass
        code = cli.close_code
    assert not got_data, "no debían llegar datos en un rechazo"
    return code


async def _wait_started(server: uvicorn.Server) -> None:
    for _ in range(200):
        if server.started:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("uvicorn no arrancó a tiempo")


@pytest.mark.asyncio
async def test_ws_uvicorn_real(caplog):
    await asyncio.wait_for(_run_scenario(caplog), timeout=25)


async def _run_scenario(caplog):
    # Captura el log de limpieza del endpoint (nivel INFO) para la verificación posterior.
    caplog.set_level(logging.INFO, logger="src.api.auth.ws_uvicorn_test")
    ttl = 3
    cfg = _cfg(ttl)
    pusher = ThrottledPusher(throttle_interval=0.1)
    app = mw.AuthGate(
        Starlette(routes=[WebSocketRoute("/ws/chart", _make_endpoint(pusher))]),
        config=cfg,
    )

    # Socket loopback con puerto efímero.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))

    async def broadcaster():
        i = 0
        while True:
            await pusher._broadcast(json.dumps({"type": "tick", "i": i}))
            i += 1
            await asyncio.sleep(0.05)

    bcast_task = None
    url = f"ws://127.0.0.1:{port}/ws/chart"
    try:
        await _wait_started(server)
        bcast_task = asyncio.create_task(broadcaster())

        now = int(time.time())
        deadline = now + ttl
        cookie = f"__Host-tc_session={SessionCodec(cfg).issue(SUB_OK, now)}"

        # --- Conexión autorizada: vive hasta el plazo y cierra 4401 ---------------
        data_times: list[float] = []
        messages = 0
        close_code = None
        close_time = None
        async with connect(url, additional_headers={"Cookie": cookie, "Origin": ORIGIN_OK}) as cli:
            try:
                while True:
                    await cli.recv()
                    data_times.append(time.time())
                    messages += 1
            except Exception:  # la conexión se cierra por el servidor (4401)
                pass
            close_time = time.time()
            close_code = cli.close_code

        assert messages >= 2, f"mensajes recibidos: {messages}"
        assert close_code == 4401, f"close_code={close_code}"
        assert close_time is not None and close_time <= deadline + 1
        for dt in data_times:
            assert dt <= deadline + 0.25, f"dato tras el plazo: {dt - deadline:.3f}s"

        # Limpieza: 0 conexiones y log de limpieza (<= 30 s del plazo; aquí es inmediato).
        for _ in range(300):
            if pusher.connection_count == 0:
                break
            await asyncio.sleep(0.01)
        assert pusher.connection_count == 0
        assert any("WebSocket connection cleaned up." in r.getMessage() for r in caplog.records)

        # --- Rechazo 4401 sin cookie (accept + close inmediato, sin datos) --------
        code_401 = await _connect_expect_close(url, {"Origin": ORIGIN_OK})
        assert code_401 == 4401, f"sin cookie: close_code={code_401}"

        # --- Rechazo 4403 origin ajeno -------------------------------------------
        code_403 = await _connect_expect_close(
            url, {"Cookie": cookie, "Origin": ORIGIN_OTHER})
        assert code_403 == 4403, f"origin ajeno: close_code={code_403}"
    finally:
        if bcast_task is not None:
            bcast_task.cancel()
        server.should_exit = True
        await server_task
