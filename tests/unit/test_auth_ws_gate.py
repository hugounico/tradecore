"""Pruebas de T5 (ASGI directo): AuthGate WebSocket — cierre real 4401/4403, send_lock único.

Reloj inyectado por lista mutable. `ThrottledPusher` real. Sin src.api.app, Databento, AWS ni
red externa. Las pruebas se escriben antes de implementar la parte WebSocket definitiva.
"""
from __future__ import annotations

import asyncio
import gc
import json
import logging

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.api.auth import middleware as mw
from src.api.auth.config import validate_auth_settings
from src.api.auth.session import SessionCodec
from src.api.throttled_pusher import ThrottledPusher

# --- Config sintética (copiada de test_auth_session.py) -------------------------
NOW = 1_700_000_000
TTL = 3
EPOCH = 1
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
SUB_OK_2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ALLOWED_SUBS = f"{SUB_OK},{SUB_OK_2}"


def _raw(**over) -> dict:
    raw = {
        "TC_AUTH_SUPABASE_URL": SUPABASE_URL,
        "TC_AUTH_PUBLISHABLE_KEY": PUBKEY,
        "TC_AUTH_JWKS_JSON": GOOD_JWKS,
        "TC_AUTH_ALLOWED_SUBS": ALLOWED_SUBS,
        "TC_AUTH_ALLOWED_ORIGINS": ORIGIN_OK,
        "TC_SESSION_KEY": SESSION_KEY,
        "TC_SESSION_EPOCH": str(EPOCH),
        "TC_SESSION_TTL_SECONDS": str(TTL),
    }
    raw.update(over)
    return raw


def _cfg(**over):
    cfg = validate_auth_settings(_raw(**over))
    assert cfg.status == "OK", cfg.invalid_fields
    return cfg


def _cfg_invalid():
    cfg = validate_auth_settings({})
    assert cfg.status == "INVALID"
    return cfg


def _cookie(cfg=None) -> str:
    return SessionCodec(cfg or _cfg()).issue(SUB_OK, NOW)


EXP = NOW + TTL  # exp de la cookie emitida con NOW


# --- FakeServer: simula el servidor ASGI del lado del cliente --------------------
class FakeServer:
    """Entrega `receive()` desde una cola precargada y registra lo enviado por `send`."""

    def __init__(self, first_messages, *, fail_close: bool = False) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        for m in first_messages:
            self.queue.put_nowait(m)
        self.sent: list = []
        self.receive_calls = 0
        self._fail_close = fail_close

    async def receive(self):
        self.receive_calls += 1
        return await self.queue.get()

    async def send(self, message):
        self.sent.append(message)
        if message["type"] == "websocket.close":
            # El servidor real responde con una desconexión auténtica (código propio 4999).
            self.queue.put_nowait({"type": "websocket.disconnect", "code": 4999})
            if self._fail_close:
                raise OSError("socket muerto al cerrar")


# --- Endpoint de prueba: misma estructura que el real /ws/chart ------------------
def make_chart_endpoint(pusher: ThrottledPusher, record: dict):
    async def chart_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        pusher.add_connection(ws)
        try:
            await pusher.send_direct(ws, json.dumps({"type": "initial_load", "candles": []}))
            while True:
                await ws.receive_text()
        except WebSocketDisconnect as exc:
            record["disconnect_code"] = exc.code
        except Exception as exc:  # noqa: BLE001 (es el endpoint de prueba, no el middleware)
            record["error"] = repr(exc)
        finally:
            pusher.remove_connection(ws)
            record["cleaned_up"] = True
    return chart_endpoint


def _ws_scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {"type": "websocket", "path": "/ws/chart", "headers": headers}


def _headers(*, origin=ORIGIN_OK, origin_count=1, cookie=None, cookie_headers=None):
    h: list[tuple[bytes, bytes]] = []
    if origin is not None:
        for _ in range(origin_count):
            h.append((b"origin", origin.encode()))
    if cookie_headers is not None:
        for c in cookie_headers:
            h.append((b"cookie", c.encode()))
    elif cookie is not None:
        h.append((b"cookie", f"__Host-tc_session={cookie}".encode()))
    return h


def _build(cfg, pusher, record, clock):
    app = Starlette(routes=[WebSocketRoute("/ws/chart", make_chart_endpoint(pusher, record))])
    return mw.AuthGate(app, config=cfg, clock=clock)


async def _run(gate, scope, server):
    await gate(scope, server.receive, server.send)


def _types(server) -> list[str]:
    return [m["type"] for m in server.sent]


# ================================================================================
# 1. Rechazos 4401 con forma [accept, close 4401]
# ================================================================================
REJECT_4401 = {
    "config_invalid": dict(cfg_invalid=True, headers=lambda: _headers(cookie=_cookie())),
    "no_cookie": dict(headers=lambda: _headers(cookie=None)),
    "bad_sig": dict(headers=lambda: _headers(cookie=_tampered(_cookie()))),
    "expired": dict(t=EXP, headers=lambda: _headers(cookie=_cookie())),
    "dup_one_header": dict(headers=lambda: _headers(
        cookie_headers=[f"__Host-tc_session={_cookie()}; __Host-tc_session={_cookie()}"])),
    "dup_two_headers": dict(headers=lambda: _headers(
        cookie_headers=[f"__Host-tc_session={_cookie()}", f"__Host-tc_session={_cookie()}"])),
    "space_name": dict(headers=lambda: _headers(
        cookie_headers=[f"__Host-tc_session ={_cookie()}; __Host-tc_session={_cookie()}"])),
}


def _tampered(cookie: str) -> str:
    h, p, s = cookie.split(".")
    ch = "B" if s[0] != "B" else "C"
    return f"{h}.{p}.{ch}{s[1:]}"


@pytest.mark.parametrize("name", list(REJECT_4401), ids=list(REJECT_4401))
async def test_reject_4401(name):
    spec = REJECT_4401[name]
    cfg = _cfg_invalid() if spec.get("cfg_invalid") else _cfg()
    t = [spec.get("t", NOW)]
    pusher = ThrottledPusher(throttle_interval=0.1)
    record: dict = {}
    gate = _build(cfg, pusher, record, lambda: t[0])
    server = FakeServer([{"type": "websocket.connect"}])
    await _run(gate, _ws_scope(spec["headers"]()), server)
    assert _types(server) == ["websocket.accept", "websocket.close"]
    assert server.sent[1]["code"] == 4401
    assert server.receive_calls == 1
    assert "cleaned_up" not in record  # el app nunca se llamó


# ================================================================================
# 2. Rechazos 4403
# ================================================================================
REJECT_4403 = {
    "no_origin": dict(headers=lambda: _headers(origin=None, cookie=_cookie())),
    "other_origin": dict(headers=lambda: _headers(origin=ORIGIN_OTHER, cookie=_cookie())),
    "two_origins": dict(headers=lambda: _headers(origin_count=2, cookie=_cookie())),
    "other_origin_valid_cookie": dict(
        headers=lambda: _headers(origin=ORIGIN_OTHER, cookie=_cookie())),
}


@pytest.mark.parametrize("name", list(REJECT_4403), ids=list(REJECT_4403))
async def test_reject_4403(name):
    spec = REJECT_4403[name]
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.1)
    record: dict = {}
    gate = _build(_cfg(), pusher, record, lambda: t[0])
    server = FakeServer([{"type": "websocket.connect"}])
    await _run(gate, _ws_scope(spec["headers"]()), server)
    assert _types(server) == ["websocket.accept", "websocket.close"]
    assert server.sent[1]["code"] == 4403
    assert server.receive_calls == 1
    assert "cleaned_up" not in record


# ================================================================================
# 3. Prioridad de rechazos
# ================================================================================
async def test_priority_invalid_over_origin():
    # config INVALID + origin ajeno => 4401 (config primero).
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.1)
    gate = _build(_cfg_invalid(), pusher, {}, lambda: t[0])
    server = FakeServer([{"type": "websocket.connect"}])
    await _run(gate, _ws_scope(_headers(origin=ORIGIN_OTHER, cookie=_cookie())), server)
    assert server.sent[1]["code"] == 4401


async def test_priority_origin_over_cookie():
    # origin ajeno + cookie inválida => 4403 (origin antes que cookie).
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.1)
    gate = _build(_cfg(), pusher, {}, lambda: t[0])
    server = FakeServer([{"type": "websocket.connect"}])
    await _run(gate, _ws_scope(_headers(origin=ORIGIN_OTHER, cookie=_tampered(_cookie()))), server)
    assert server.sent[1]["code"] == 4403


# ================================================================================
# 4. Rechazo con primer mensaje disconnect -> nada enviado
# ================================================================================
async def test_reject_first_disconnect_sends_nothing():
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.1)
    record: dict = {}
    gate = _build(_cfg(), pusher, record, lambda: t[0])
    server = FakeServer([{"type": "websocket.disconnect", "code": 1000}])
    await _run(gate, _ws_scope(_headers(cookie=None)), server)  # no cookie => rechazo 4401
    assert server.sent == []
    assert "cleaned_up" not in record


# ================================================================================
# 5. Válida antes del plazo: initial + broadcast pasan, 1 conexión, sin close
# ================================================================================
async def test_valid_before_deadline():
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.0)
    record: dict = {}
    gate = _build(_cfg(), pusher, record, lambda: t[0])
    server = FakeServer([{"type": "websocket.connect"}])

    async def driver():
        await _run(gate, _ws_scope(_headers(cookie=_cookie())), server)

    task = asyncio.create_task(driver())
    await asyncio.sleep(0.05)
    assert pusher.connection_count == 1
    await pusher._broadcast(json.dumps({"type": "tick", "i": 1}))
    await asyncio.sleep(0.05)
    # Cierra limpio desconectando al cliente.
    server.queue.put_nowait({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(task, timeout=2)
    types = _types(server)
    assert "websocket.accept" in types
    assert "websocket.close" not in types
    sends = [m for m in server.sent if m["type"] == "websocket.send"]
    assert any("initial_load" in m.get("text", "") for m in sends)
    assert any('"tick"' in m.get("text", "") for m in sends)
    assert record.get("cleaned_up") is True
    assert pusher.connection_count == 0


# ================================================================================
# 6. Plazo con receive pendiente -> close 4401 y el endpoint ve disconnect 4999
# ================================================================================
async def test_deadline_with_pending_receive():
    t = [EXP - 0.05]
    pusher = ThrottledPusher(throttle_interval=0.1)
    record: dict = {}
    gate = _build(_cfg(), pusher, record, lambda: t[0])
    # Solo el connect inicial; luego el receive del endpoint queda pendiente (cola vacía).
    server = FakeServer([{"type": "websocket.connect"}])

    async def driver():
        await _run(gate, _ws_scope(_headers(cookie=_cookie())), server)

    task = asyncio.create_task(driver())
    await asyncio.sleep(0.05)
    t[0] = EXP  # vence
    await asyncio.wait_for(task, timeout=2)
    closes = [m for m in server.sent if m["type"] == "websocket.close"]
    assert len(closes) == 1
    assert closes[0]["code"] == 4401
    assert record.get("disconnect_code") == 4999
    # receive: #1 lo consume ws.accept() (connect); #2 el bucle receive_text (queda pendiente
    # hasta que el close genera el disconnect 4999).
    assert server.receive_calls == 2
    assert record.get("cleaned_up") is True
    assert pusher.connection_count == 0


# ================================================================================
# 7. Broadcast en el instante del plazo -> sin send posterior, un close 4401
# ================================================================================
async def test_broadcast_at_deadline():
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.0)
    record: dict = {}
    gate = _build(_cfg(), pusher, record, lambda: t[0])
    server = FakeServer([{"type": "websocket.connect"}])

    async def driver():
        await _run(gate, _ws_scope(_headers(cookie=_cookie())), server)

    task = asyncio.create_task(driver())
    await asyncio.sleep(0.05)
    assert pusher.connection_count == 1
    t[0] = EXP  # vence justo antes del broadcast
    await pusher._broadcast(json.dumps({"type": "tick", "i": 9}))
    await asyncio.wait_for(task, timeout=2)
    sends_after = [m for m in server.sent if m["type"] == "websocket.send" and '"tick"' in m.get("text", "")]
    assert sends_after == []  # el dato posterior al plazo no sale
    closes = [m for m in server.sent if m["type"] == "websocket.close"]
    assert len(closes) == 1 and closes[0]["code"] == 4401
    assert pusher.connection_count == 0
    assert record.get("cleaned_up") is True


# ================================================================================
# 8. Tras el cierre, el send envuelto lanza ClosedByGate (subclase de Exception)
# ================================================================================
def test_closedbygate_is_exception():
    assert issubclass(mw.ClosedByGate, Exception)


# ================================================================================
# 9. Desconexión del cliente antes del plazo: gate no envía close
# ================================================================================
async def test_client_disconnect_before_deadline():
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.1)
    record: dict = {}
    gate = _build(_cfg(), pusher, record, lambda: t[0])
    server = FakeServer([
        {"type": "websocket.connect"},
        {"type": "websocket.disconnect", "code": 1000},
    ])
    await asyncio.wait_for(
        _run(gate, _ws_scope(_headers(cookie=_cookie())), server), timeout=2)
    closes = [m for m in server.sent if m["type"] == "websocket.close"]
    assert closes == []
    assert record.get("disconnect_code") == 1000
    assert record.get("cleaned_up") is True


# ================================================================================
# 10. fail_close=True al vencer: un único DEBUG, el endpoint igual ve 4999
# ================================================================================
async def test_fail_close_logs_debug(caplog):
    t = [EXP - 0.05]
    pusher = ThrottledPusher(throttle_interval=0.1)
    record: dict = {}
    gate = _build(_cfg(), pusher, record, lambda: t[0])
    server = FakeServer([{"type": "websocket.connect"}], fail_close=True)

    async def driver():
        await _run(gate, _ws_scope(_headers(cookie=_cookie())), server)

    with caplog.at_level(logging.DEBUG, logger="src.api.auth.middleware"):
        task = asyncio.create_task(driver())
        await asyncio.sleep(0.05)
        t[0] = EXP
        await asyncio.wait_for(task, timeout=2)
    debug_recs = [r for r in caplog.records if r.name == "src.api.auth.middleware"]
    assert len(debug_recs) == 1
    assert debug_recs[0].levelno == logging.DEBUG
    assert debug_recs[0].getMessage() == "WS: cierre 4401 no transmitido"
    assert record.get("disconnect_code") == 4999
    assert "error" not in record
    closes = [m for m in server.sent if m["type"] == "websocket.close"]
    assert len(closes) == 1


# ================================================================================
# 11. Cancelación externa: no deja tareas huérfanas ni "Task was destroyed"
# ================================================================================
async def test_external_cancellation(caplog):
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.1)
    record: dict = {}
    gate = _build(_cfg(), pusher, record, lambda: t[0])
    # connect inicial; luego el receive del endpoint queda pendiente.
    server = FakeServer([{"type": "websocket.connect"}])

    async def driver():
        await _run(gate, _ws_scope(_headers(cookie=_cookie())), server)

    with caplog.at_level(logging.DEBUG, logger="asyncio"):
        task = asyncio.create_task(driver())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        pending = [x for x in asyncio.all_tasks() if x is not asyncio.current_task()]
        assert pending == []
        gc.collect()
    assert not any("Task was destroyed" in r.getMessage() for r in caplog.records)


# ================================================================================
# 12. accept en el plazo: el cliente recibe [accept, close 4401]
# ================================================================================
async def test_accept_at_deadline():
    # Solo el handshake lee EXP-1. La recepción del `connect` (dentro de ws.accept) ya ve EXP,
    # pero el gate no aplica el plazo antes del accept: el connect se entrega y el accept se
    # envía. El primer `send` de datos lee EXP y dispara el cierre: [accept, close 4401].
    seq = [EXP - 1]
    after = [EXP]

    def clock():
        if seq:
            return seq.pop(0)
        return after[0]

    pusher = ThrottledPusher(throttle_interval=0.1)
    record: dict = {}
    gate = _build(_cfg(), pusher, record, clock)
    server = FakeServer([{"type": "websocket.connect"}])
    await asyncio.wait_for(_run(gate, _ws_scope(_headers(cookie=_cookie())), server), timeout=2)
    assert _types(server) == ["websocket.accept", "websocket.close"]
    assert server.sent[1]["code"] == 4401
    assert "error" not in record
    assert record.get("cleaned_up") is True


# ================================================================================
# 13. Cierre propio del endpoint (1011) antes del plazo pasa; sin 4401 posterior
# ================================================================================
async def test_endpoint_own_close_passes():
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.1)
    record: dict = {}

    def make_ep(pusher, record):
        async def ep(ws: WebSocket) -> None:
            await ws.accept()
            await ws.close(code=1011)
            record["cleaned_up"] = True
        return ep

    app = Starlette(routes=[WebSocketRoute("/ws/chart", make_ep(pusher, record))])
    gate = mw.AuthGate(app, config=_cfg(), clock=lambda: t[0])
    server = FakeServer([{"type": "websocket.connect"}])
    await asyncio.wait_for(_run(gate, _ws_scope(_headers(cookie=_cookie())), server), timeout=2)
    closes = [m for m in server.sent if m["type"] == "websocket.close"]
    assert len(closes) == 1
    assert closes[0]["code"] == 1011
    assert record.get("cleaned_up") is True


# ================================================================================
# 14. Texto del cliente antes del plazo llega intacto; una sola tarea de recepción
# ================================================================================
async def test_client_text_passes_through():
    t = [NOW]
    pusher = ThrottledPusher(throttle_interval=0.1)
    received: list[str] = []

    def make_ep(pusher, received):
        async def ep(ws: WebSocket) -> None:
            await ws.accept()
            pusher.add_connection(ws)
            try:
                received.append(await ws.receive_text())
                received.append(await ws.receive_text())
            except WebSocketDisconnect:
                pass
            finally:
                pusher.remove_connection(ws)
        return ep

    app = Starlette(routes=[WebSocketRoute("/ws/chart", make_ep(pusher, received))])
    gate = mw.AuthGate(app, config=_cfg(), clock=lambda: t[0])
    server = FakeServer([
        {"type": "websocket.connect"},
        {"type": "websocket.receive", "text": "x"},
        {"type": "websocket.receive", "text": "y"},
        {"type": "websocket.disconnect", "code": 1000},
    ])
    await asyncio.wait_for(_run(gate, _ws_scope(_headers(cookie=_cookie())), server), timeout=2)
    assert received == ["x", "y"]
