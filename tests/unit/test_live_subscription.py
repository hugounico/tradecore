"""Tests unit de T1.5 — `LiveSubscription` con `db.Live` MOCKEADO (offline).

Replican los 5 comportamientos spec-ahead de LiveSubscription (definidos en
`tests/unit/test_databento_connector.py::TestDabentoConnectorLiveSubscription`, que
apuntan al conector protegido y se resolveran en W6), pero ejercitados contra el modulo
NUEVO y AISLADO `src/live/live_subscription.py`, sin tocar el conector protegido ni la
suite existente.

Patron de mock (identico al spec-ahead): `db.Live` se parchea; la instancia expone
`subscribe` (MagicMock sincrono) y `__aiter__` (iterador asincrono que produce records).
Aqui se parchea `src.live.live_subscription.db.Live` (el simbolo importado por el modulo
aislado), NO el del conector.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.live.live_subscription import LiveSubscription
from src.schemas.candle import Candle

# --- Fixtures de datos (mismos valores que el spec-ahead del conector) ---

TS_1 = 1700000000_000000000  # 2023-11-14T22:13:20Z (apertura de minuto)
TS_2 = 1700000060_000000000  # +60s
TS_3 = 1700000120_000000000  # +120s

PRICE_OPEN = 21500_000000000  # int64 x 1e9 -> 21500.0
PRICE_HIGH = 21510_000000000
PRICE_LOW = 21490_000000000
PRICE_CLOSE = 21505_000000000
VOLUME = 1500


def _make_mock_record(ts_event_ns, open_p, high_p, low_p, close_p, volume) -> MagicMock:
    """Crea un record OHLCVMsg mock con precios int64 fixed-point (como el SDK real)."""
    record = MagicMock()
    record.ts_event = ts_event_ns
    record.open = open_p
    record.high = high_p
    record.low = low_p
    record.close = close_p
    record.volume = volume
    return record


def _patch_live(records):
    """Devuelve un context manager que parchea `db.Live` para producir `records`.

    `records = None` (o lista vacia) simula un stream que termina de inmediato.
    """
    recs = list(records or [])

    async def _aiter(*args, **kwargs):
        for rec in recs:
            yield rec

    cm = patch("src.live.live_subscription.db.Live")
    return cm, _aiter


def _make_sub(last_candle_time=None) -> LiveSubscription:
    return LiveSubscription(api_key="test-key-123", last_candle_time=last_candle_time)


# --- (a) produce Candles desde los records del stream ---

async def test_live_subscription_yields_candles():
    sub = _make_sub()
    record_1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)
    record_2 = _make_mock_record(TS_2, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1600)

    cm, aiter_fn = _patch_live([record_1, record_2])
    with cm as mock_live_class:
        mock_live_instance = MagicMock()
        mock_live_class.return_value = mock_live_instance
        mock_live_instance.subscribe = MagicMock()
        mock_live_instance.__aiter__ = aiter_fn

        candles = []
        async for candle in sub.subscribe_live():
            candles.append(candle)
            if len(candles) >= 2:
                break

    assert len(candles) == 2
    assert all(isinstance(c, Candle) for c in candles)
    assert candles[0].volume == 1500
    assert candles[1].volume == 1600
    assert candles[0].open == 21500.0
    assert candles[0].close == 21505.0


# --- (b) actualiza last_candle_time conforme llegan velas ---

async def test_live_subscription_updates_last_candle_time():
    sub = _make_sub()
    record_1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)

    cm, aiter_fn = _patch_live([record_1])
    with cm as mock_live_class:
        mock_live_instance = MagicMock()
        mock_live_class.return_value = mock_live_instance
        mock_live_instance.subscribe = MagicMock()
        mock_live_instance.__aiter__ = aiter_fn

        async for _candle in sub.subscribe_live():
            pass

    expected_time = datetime.fromtimestamp(TS_1 / 1e9, tz=timezone.utc)
    assert sub.last_candle_time == expected_time


# --- (c) is_connected True durante el stream activo ---

async def test_is_connected_true_during_live_stream():
    sub = _make_sub()
    record_1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)

    connected_during_stream = []
    cm, aiter_fn = _patch_live([record_1])
    with cm as mock_live_class:
        mock_live_instance = MagicMock()
        mock_live_class.return_value = mock_live_instance
        mock_live_instance.subscribe = MagicMock()
        mock_live_instance.__aiter__ = aiter_fn

        async for _candle in sub.subscribe_live():
            connected_during_stream.append(sub.is_connected)

    assert connected_during_stream, "debe haberse emitido al menos una vela"
    assert all(connected_during_stream), "is_connected debe ser True durante el stream activo"


# --- (d) is_connected False al terminar el stream ---

async def test_is_connected_false_after_stream_ends():
    sub = _make_sub()

    cm, aiter_fn = _patch_live([])  # stream vacio: termina de inmediato
    with cm as mock_live_class:
        mock_live_instance = MagicMock()
        mock_live_class.return_value = mock_live_instance
        mock_live_instance.subscribe = MagicMock()
        mock_live_instance.__aiter__ = aiter_fn

        async for _ in sub.subscribe_live():
            pass

    assert sub.is_connected is False


# --- (e) descarta velas con ts_event <= la ultima conocida (dedup) ---

async def test_live_discards_candles_before_last_historical():
    historical_time = datetime.fromtimestamp(TS_2 / 1e9, tz=timezone.utc)
    sub = _make_sub(last_candle_time=historical_time)

    record_old = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1000)
    record_equal = _make_mock_record(TS_2, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1100)
    record_new = _make_mock_record(TS_3, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1200)

    cm, aiter_fn = _patch_live([record_old, record_equal, record_new])
    with cm as mock_live_class:
        mock_live_instance = MagicMock()
        mock_live_class.return_value = mock_live_instance
        mock_live_instance.subscribe = MagicMock()
        mock_live_instance.__aiter__ = aiter_fn

        candles = []
        async for candle in sub.subscribe_live():
            candles.append(candle)

    # Solo la vela estrictamente mas nueva que la ultima historica se emite.
    assert len(candles) == 1
    assert candles[0].volume == 1200


# --- extra: reconexion compone T1.4 (sin duplicar backoff) ---

async def test_reconnect_uses_injected_policy_success():
    """Con una ReconnectPolicy inyectada, reconnect() devuelve RECONNECTED al conectar."""
    from src.live.reconnect_policy import (
        MVP_RECONNECT_CONFIG,
        ReconnectOutcome,
        ReconnectPolicy,
    )

    async def _no_sleep(_seconds):
        return None

    policy = ReconnectPolicy(config=MVP_RECONNECT_CONFIG, sleep=_no_sleep)
    sub = LiveSubscription(api_key="test-key-123", reconnect_policy=policy)

    cm, _aiter = _patch_live([])
    with cm as mock_live_class:
        mock_live_instance = MagicMock()
        mock_live_class.return_value = mock_live_instance
        mock_live_instance.subscribe = MagicMock()

        outcome = await sub.reconnect()

    assert outcome is ReconnectOutcome.RECONNECTED
    assert sub.is_connected is True


async def test_reconnect_without_policy_raises():
    """Sin policy inyectada, reconnect() no inventa backoff: lanza RuntimeError."""
    sub = _make_sub()
    with pytest.raises(RuntimeError):
        await sub.reconnect()


# =====================================================================================
# Tests permanentes del CICLO DE VIDA del cliente Live (cierre explicito).
# Verifican que cada cliente creado por subscribe_live() recibe exactamente UN cierre
# explicito (`stop()`) al terminar la invocacion, por cualquier via (normal, cancelacion,
# excepcion del stream/mapper), sin enmascarar CancelledError ni la excepcion primaria.
# Cliente Databento MOCKEADO; sin red.
# =====================================================================================

import asyncio  # noqa: E402  (import local a la seccion de tests de ciclo de vida)


def _make_live_instance(aiter_fn, *, stop=None):
    """Instancia mock de db.Live con subscribe() sincrono, __aiter__ dado y stop() observable."""
    inst = MagicMock()
    inst.subscribe = MagicMock()
    inst.__aiter__ = aiter_fn
    inst.stop = stop if stop is not None else MagicMock(name="stop")
    return inst


# --- CASE A: terminacion normal del stream -> stop() exactamente una vez ---

async def test_lifecycle_case_a_normal_termination_closes_once():
    sub = _make_sub()
    record_1 = _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)
    cm, aiter_fn = _patch_live([record_1])
    with cm as mock_live_class:
        inst = _make_live_instance(aiter_fn)
        mock_live_class.return_value = inst

        async for _ in sub.subscribe_live():
            pass

    assert inst.stop.call_count == 1  # cierre explicito exactamente una vez
    assert sub.is_connected is False


# --- CASE B: cancelacion asyncio -> finally corre, desconectado, stop() una vez, CancelledError preservado ---

async def test_lifecycle_case_b_cancellation_closes_once_and_preserves_cancel():
    sub = _make_sub()

    started = asyncio.Event()

    async def _aiter_blocking(*args, **kwargs):
        # Emite una vela, senala que arranco, luego se bloquea hasta ser cancelado.
        yield _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)
        started.set()
        await asyncio.sleep(3600)  # se cancelara antes de completar
        yield _make_mock_record(TS_2, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)

    cm = patch("src.live.live_subscription.db.Live")
    with cm as mock_live_class:
        inst = _make_live_instance(_aiter_blocking)
        mock_live_class.return_value = inst

        async def _consume():
            async for _ in sub.subscribe_live():
                pass

        task = asyncio.create_task(_consume())
        await started.wait()
        assert sub.is_connected is True  # conectado durante el stream
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task  # CancelledError NO enmascarado: se propaga

    assert sub.is_connected is False          # finally corrio -> desconectado
    assert inst.stop.call_count == 1          # cierre explicito exactamente una vez


# --- CASE C: excepcion del stream -> stop() una vez, excepcion primaria observable ---

async def test_lifecycle_case_c_stream_exception_closes_once_and_propagates():
    sub = _make_sub()

    class _StreamBoom(RuntimeError):
        pass

    async def _aiter_raises(*args, **kwargs):
        yield _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)
        raise _StreamBoom("stream failed")

    cm = patch("src.live.live_subscription.db.Live")
    with cm as mock_live_class:
        inst = _make_live_instance(_aiter_raises)
        mock_live_class.return_value = inst

        with pytest.raises(_StreamBoom):  # excepcion primaria del stream sigue observable
            async for _ in sub.subscribe_live():
                pass

    assert inst.stop.call_count == 1
    assert sub.is_connected is False


# --- CASE D: el propio cierre lanza -> el error primario NO queda enmascarado ---

async def test_lifecycle_case_d_close_raises_does_not_mask_primary():
    sub = _make_sub()

    class _StreamBoom(RuntimeError):
        pass

    async def _aiter_raises(*args, **kwargs):
        yield _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME)
        raise _StreamBoom("primary stream error")

    stop_that_raises = MagicMock(name="stop", side_effect=ValueError("close failed"))

    cm = patch("src.live.live_subscription.db.Live")
    with cm as mock_live_class:
        inst = _make_live_instance(_aiter_raises, stop=stop_that_raises)
        mock_live_class.return_value = inst

        # La excepcion PRIMARIA (_StreamBoom) debe propagarse, NO el ValueError del cierre.
        with pytest.raises(_StreamBoom):
            async for _ in sub.subscribe_live():
                pass

    assert stop_that_raises.call_count == 1   # se intento cerrar
    assert sub.is_connected is False


# --- CASE E: camino normal -> no hay segundo cierre duplicado para el mismo cliente ---

async def test_lifecycle_case_e_no_double_close_on_normal_path():
    sub = _make_sub()
    records = [
        _make_mock_record(TS_1, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, VOLUME),
        _make_mock_record(TS_2, PRICE_OPEN, PRICE_HIGH, PRICE_LOW, PRICE_CLOSE, 1600),
    ]
    cm, aiter_fn = _patch_live(records)
    with cm as mock_live_class:
        inst = _make_live_instance(aiter_fn)
        mock_live_class.return_value = inst

        async for _ in sub.subscribe_live():
            pass

    assert inst.stop.call_count == 1  # exactamente una vez, sin duplicado
