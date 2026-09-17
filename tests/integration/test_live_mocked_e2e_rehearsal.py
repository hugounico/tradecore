"""Rehearsal E2E MOCKEADO del primer grafico Live — 100% offline, sin db.Live, sin red.

Objetivo: ejercitar OFFLINE la cadena que usara la futura unica conexion Live real, para
reducir el riesgo de esa conexion (que es un disparo irreversible autorizado por gate):

    fake OHLCV record  ->  live_record_to_candle (T1.2)  ->  LiveSubscription.subscribe_live
    (T1.5)  ->  LiveCandleSource.replay() (adaptador CandleSource)  ->  [forma de payload
    WebSocket que el ThrottledPusher.queue_candle ya produce hoy]

NO se importa ni se toca ningun componente protegido (app.py, throttled_pusher.py,
databento_connector.py de streaming, dashboard, SignalEngine). La FORMA del payload de vela
se replica aqui de forma explicita como CONTRATO esperado (leida verbatim de
`ThrottledPusher.queue_candle`), para que este rehearsal falle si la forma divergiera. El
cableado fisico real (que SI usa el pusher protegido) es Wave 6.

Este rehearsal NO reemplaza la prueba del sistema real; valida que las piezas nuevas/aisladas
encajan en la forma que el pipeline existente espera, ANTES de gastar la conexion Live real.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.live.live_candle_source import LiveCandleSource
from src.live.live_subscription import LiveSubscription
from src.schemas.candle import Candle

# Timestamps (ns) y precios int64 x 1e9, como entrega el SDK real para ohlcv-1m.
TS_A = 1700000000_000000000  # apertura de minuto
TS_B = 1700000060_000000000  # +60s


def _make_ohlcv_record(ts_event_ns, o, h, l, c, v) -> MagicMock:
    """Fake de un OHLCVMsg del SDK (atributos ts_event/open/high/low/close/volume int64)."""
    record = MagicMock()
    record.ts_event = ts_event_ns
    record.open = o
    record.high = h
    record.low = l
    record.close = c
    record.volume = v
    return record


def _candle_to_ws_payload(candle: Candle) -> dict:
    """Replica EXACTA de la forma que produce `ThrottledPusher.queue_candle` hoy.

    Se define localmente (no se importa el pusher protegido) para expresar el CONTRATO que el
    dashboard consume via `handleCandle` -> `candlestickSeries.update(data)`:
    `{time: epoch_seconds_int, open, high, low, close, volume}`.
    """
    return {
        "time": int(candle.timestamp.timestamp()),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def _patch_live(records):
    recs = list(records or [])

    async def _aiter(*args, **kwargs):
        for rec in recs:
            yield rec

    return patch("src.live.live_subscription.db.Live"), _aiter


async def test_mocked_e2e_fake_record_to_ws_payload():
    """Cadena completa offline: fake record -> mapper -> live source -> payload WS esperado."""
    # Dos velas de 1 minuto consecutivas, precios NQ ~21500.
    r_a = _make_ohlcv_record(TS_A, 21500_000000000, 21510_000000000, 21490_000000000, 21505_000000000, 1500)
    r_b = _make_ohlcv_record(TS_B, 21505_000000000, 21520_000000000, 21500_000000000, 21518_000000000, 1600)

    # Fuente Live envuelta como CandleSource (misma forma que el loop consume en simulacion).
    subscription = LiveSubscription(api_key="test-key-123")
    source = LiveCandleSource(subscription)

    cm, aiter_fn = _patch_live([r_a, r_b])
    with cm as mock_live_class:
        inst = MagicMock()
        mock_live_class.return_value = inst
        inst.subscribe = MagicMock()
        inst.__aiter__ = aiter_fn

        # El loop consumiria exactamente asi: `async for candle in source.replay()`.
        payloads = []
        async for candle in source.replay():
            payloads.append(_candle_to_ws_payload(candle))

    # Se produjeron dos payloads de vela con la forma que el dashboard renderiza.
    assert len(payloads) == 2

    first = payloads[0]
    assert set(first.keys()) == {"time", "open", "high", "low", "close", "volume"}
    # time = epoch segundos (int), derivado de ts_event (apertura del minuto).
    assert first["time"] == int(datetime.fromtimestamp(TS_A / 1e9, tz=timezone.utc).timestamp())
    assert isinstance(first["time"], int)
    # Precios convertidos por el mapper (int64 x 1e9 -> float).
    assert first["open"] == 21500.0
    assert first["high"] == 21510.0
    assert first["low"] == 21490.0
    assert first["close"] == 21505.0
    assert first["volume"] == 1500

    second = payloads[1]
    assert second["time"] == int(datetime.fromtimestamp(TS_B / 1e9, tz=timezone.utc).timestamp())
    assert second["close"] == 21518.0
    assert second["volume"] == 1600

    # Orden temporal estricto (el dashboard espera velas en orden ascendente).
    assert second["time"] > first["time"]


async def test_mocked_e2e_dedup_against_last_candle_time():
    """El dedup (mismo criterio que simulacion) descarta velas <= ultima conocida."""
    # Sembrar last_candle_time = TS_A: solo la vela TS_B (mas nueva) debe emitirse.
    seed = datetime.fromtimestamp(TS_A / 1e9, tz=timezone.utc)
    subscription = LiveSubscription(api_key="test-key-123", last_candle_time=seed)
    source = LiveCandleSource(subscription)

    r_a = _make_ohlcv_record(TS_A, 21500_000000000, 21510_000000000, 21490_000000000, 21505_000000000, 1500)
    r_b = _make_ohlcv_record(TS_B, 21505_000000000, 21520_000000000, 21500_000000000, 21518_000000000, 1600)

    cm, aiter_fn = _patch_live([r_a, r_b])
    with cm as mock_live_class:
        inst = MagicMock()
        mock_live_class.return_value = inst
        inst.subscribe = MagicMock()
        inst.__aiter__ = aiter_fn

        payloads = []
        async for candle in source.replay():
            payloads.append(_candle_to_ws_payload(candle))

    assert len(payloads) == 1
    assert payloads[0]["time"] == int(datetime.fromtimestamp(TS_B / 1e9, tz=timezone.utc).timestamp())
    assert payloads[0]["volume"] == 1600
