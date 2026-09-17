"""Rehearsal INTEGRADO del primer grafico Live — componentes REALES, 100% offline.

Diferencia con `test_live_mocked_e2e_rehearsal.py` (contract rehearsal): aqui el payload NO se
reconstruye a mano. Se atraviesa el `ThrottledPusher` REAL (componente protegido, importado y
EJECUTADO, no modificado) para verificar que la vela Live producida por la fuente real genera,
a traves del pusher real, exactamente el payload que el dashboard consume.

Cadena ejercitada con componentes reales:
    fake OHLCV record
      -> live_record_to_candle (REAL, T1.2)
      -> LiveSubscription.subscribe_live (REAL, T1.5, con db.Live mockeado en su frontera)
      -> LiveCandleSource.replay() (REAL, adaptador CandleSource)
      -> ThrottledPusher.queue_candle (REAL, protegido) -> flush_if_ready (REAL)
      -> [captura del texto WS realmente emitido por el pusher]

Fronteras mockeadas (SOLO fronteras externas, NO la logica a verificar):
- `db.Live`: la unica frontera de red hacia Databento (parche de `src.live.live_subscription.db.Live`).
- La WebSocket del navegador: se sustituye por un doble que captura `send_text` (el pusher real solo
  emite si hay una conexion asignada). NO se abre socket ni servidor.

Efectos secundarios del ThrottledPusher (auditados en Fase G, solo lectura):
- red: `self._ws.send_text(...)` en `flush_if_ready`, solo si hay ws asignada;
- tiempo: throttle por `time.time()` (se fuerza el intervalo transcurrido reseteando `_last_push_time`).
- NO disco, NO hilos.

NO se afirma DASHBOARD_HANDLER_EXECUTED ni VISUAL_RENDER_CONFIRMED: el WebSocket real y el render
del dashboard se verifican contra la conexion Live real en el turno del primer intento.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.api.throttled_pusher import ThrottledPusher  # protegido: se IMPORTA y EJECUTA, no se modifica
from src.live.live_candle_source import LiveCandleSource
from src.live.live_subscription import LiveSubscription

TS_A = 1700000000_000000000
TS_B = 1700000060_000000000


def _make_ohlcv_record(ts_event_ns, o, h, l, c, v) -> MagicMock:
    r = MagicMock()
    r.ts_event = ts_event_ns
    r.open = o
    r.high = h
    r.low = l
    r.close = c
    r.volume = v
    return r


class _CapturingWS:
    """Doble minimo de la WebSocket del navegador: captura los textos emitidos por el pusher real.

    Reemplaza SOLO la frontera externa (envio por red). No sustituye ninguna logica del pusher.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


def _patch_live(records):
    recs = list(records or [])

    async def _aiter(*args, **kwargs):
        for rec in recs:
            yield rec

    return patch("src.live.live_subscription.db.Live"), _aiter


async def test_integrated_live_source_through_real_pusher():
    """Vela Live real -> ThrottledPusher REAL -> payload WS real capturado, forma esperada."""
    r_a = _make_ohlcv_record(TS_A, 21500_000000000, 21510_000000000, 21490_000000000, 21505_000000000, 1500)

    # Fuente Live real, envuelta como CandleSource real.
    subscription = LiveSubscription(api_key="test-key-123")
    source = LiveCandleSource(subscription)

    # Pusher REAL con conexion capturadora (frontera de red sustituida por un doble).
    pusher = ThrottledPusher(throttle_interval=1.0)
    ws = _CapturingWS()
    await pusher.set_connection(ws)

    cm, aiter_fn = _patch_live([r_a])
    with cm as mock_live_class:
        inst = MagicMock()
        mock_live_class.return_value = inst
        inst.subscribe = MagicMock()
        inst.__aiter__ = aiter_fn

        # El loop consumiria asi la fuente; aqui encolamos cada vela en el pusher REAL.
        async for candle in source.replay():
            await pusher.queue_candle(candle)

    # Forzar que el intervalo de throttle haya transcurrido y emitir con el pusher REAL.
    pusher._last_push_time = 0.0
    await pusher.flush_if_ready()

    # El pusher real emitio exactamente un batch por la "conexion".
    assert len(ws.sent) == 1
    batch = json.loads(ws.sent[0])
    assert isinstance(batch, list)
    candle_msgs = [m for m in batch if m.get("type") == "candle"]
    assert len(candle_msgs) == 1

    data = candle_msgs[0]["data"]
    # Forma contractual producida por el ThrottledPusher REAL (no reconstruida a mano).
    assert set(data.keys()) == {"time", "open", "high", "low", "close", "volume"}
    assert data["time"] == int(datetime.fromtimestamp(TS_A / 1e9, tz=timezone.utc).timestamp())
    assert isinstance(data["time"], int)
    assert data["open"] == 21500.0
    assert data["high"] == 21510.0
    assert data["low"] == 21490.0
    assert data["close"] == 21505.0
    assert data["volume"] == 1500


async def test_integrated_two_candles_batched_in_order():
    """Dos velas reales -> pusher real -> ambas en el batch, orden temporal ascendente."""
    r_a = _make_ohlcv_record(TS_A, 21500_000000000, 21510_000000000, 21490_000000000, 21505_000000000, 1500)
    r_b = _make_ohlcv_record(TS_B, 21505_000000000, 21520_000000000, 21500_000000000, 21518_000000000, 1600)

    subscription = LiveSubscription(api_key="test-key-123")
    source = LiveCandleSource(subscription)
    pusher = ThrottledPusher(throttle_interval=1.0)
    ws = _CapturingWS()
    await pusher.set_connection(ws)

    cm, aiter_fn = _patch_live([r_a, r_b])
    with cm as mock_live_class:
        inst = MagicMock()
        mock_live_class.return_value = inst
        inst.subscribe = MagicMock()
        inst.__aiter__ = aiter_fn

        async for candle in source.replay():
            await pusher.queue_candle(candle)

    pusher._last_push_time = 0.0
    await pusher.flush_if_ready()

    assert len(ws.sent) == 1
    batch = json.loads(ws.sent[0])
    candle_msgs = [m for m in batch if m.get("type") == "candle"]
    assert len(candle_msgs) == 2
    t0 = candle_msgs[0]["data"]["time"]
    t1 = candle_msgs[1]["data"]["time"]
    assert t1 > t0
    assert candle_msgs[1]["data"]["close"] == 21518.0
