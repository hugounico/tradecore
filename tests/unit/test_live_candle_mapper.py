"""Tests de T1.2 — `live_record_to_candle` (mapeo record Live ohlcv-1m -> Candle).

Usa exclusivamente records sinteticos. Verifica:
1. un record valido produce un Candle;
2. ts_event (ns) -> timestamp como APERTURA del intervalo;
3-6. open/high/low/close convertidos correctamente;
7. volume conservado;
8. la conversion de precio reutiliza el MISMO helper que Historical (`_convert_price`),
   y el mapeo delega en `DabentoConnector._record_to_candle` (sin duplicacion).

No se prueba Live real, ni finalidad de vela, ni dedup/orden. No se modifica el connector.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch

from src.connectors.databento_connector import DabentoConnector, _convert_price
from src.live.live_candle_mapper import live_record_to_candle
from src.schemas.candle import Candle


@dataclass
class FakeOHLCVRecord:
    """Record sintetico con el shape de un OHLCVMsg Databento (solo los campos usados)."""

    ts_event: int  # nanosegundos Unix (apertura del intervalo)
    open: int  # precio int64 fixed-point (x 1e-9)
    high: int
    low: int
    close: int
    volume: int


def _make_record() -> FakeOHLCVRecord:
    # 2026-09-13T14:31:00Z en nanosegundos = apertura del minuto [14:31, 14:32).
    ts_ns = int(datetime(2026, 9, 13, 14, 31, 0, tzinfo=timezone.utc).timestamp() * 1e9)
    # Precios en formato raw fixed-point (> 1_000_000) para ejercitar _convert_price.
    return FakeOHLCVRecord(
        ts_event=ts_ns,
        open=21500_000000000,   # 21500.0 tras dividir por 1e9
        high=21510_000000000,   # 21510.0
        low=21490_000000000,    # 21490.0
        close=21505_000000000,  # 21505.0
        volume=1234,
    )


def test_valid_record_produces_candle() -> None:
    """Un record ohlcv-1m valido produce un objeto Candle."""
    candle = live_record_to_candle(_make_record())
    assert isinstance(candle, Candle)


def test_ts_event_maps_to_interval_open_timestamp() -> None:
    """ts_event (ns) se mapea al timestamp esperado = apertura del intervalo (UTC)."""
    candle = live_record_to_candle(_make_record())
    assert candle is not None
    expected_open = datetime(2026, 9, 13, 14, 31, 0, tzinfo=timezone.utc)
    assert candle.timestamp == expected_open


def test_open_converted_correctly() -> None:
    rec = _make_record()
    candle = live_record_to_candle(rec)
    assert candle is not None
    # La conversion debe coincidir con el helper Historical aplicado al mismo valor.
    assert candle.open == _convert_price(rec.open)
    assert candle.open == 21500.0


def test_high_converted_correctly() -> None:
    rec = _make_record()
    candle = live_record_to_candle(rec)
    assert candle is not None
    assert candle.high == _convert_price(rec.high)
    assert candle.high == 21510.0


def test_low_converted_correctly() -> None:
    rec = _make_record()
    candle = live_record_to_candle(rec)
    assert candle is not None
    assert candle.low == _convert_price(rec.low)
    assert candle.low == 21490.0


def test_close_converted_correctly() -> None:
    rec = _make_record()
    candle = live_record_to_candle(rec)
    assert candle is not None
    assert candle.close == _convert_price(rec.close)
    assert candle.close == 21505.0


def test_volume_preserved() -> None:
    rec = _make_record()
    candle = live_record_to_candle(rec)
    assert candle is not None
    assert candle.volume == 1234


def test_reuses_historical_mapper_without_duplication() -> None:
    """El mapper DELEGA en DabentoConnector._record_to_candle (reuso, no duplicacion)."""
    rec = _make_record()
    with patch.object(
        DabentoConnector, "_record_to_candle", wraps=DabentoConnector._record_to_candle
    ) as spy:
        live_record_to_candle(rec)
    # Prueba de reuso: el helper Historical fue invocado exactamente una vez con el record.
    spy.assert_called_once_with(rec)


def test_matches_historical_mapping_exactly() -> None:
    """El resultado del mapper Live es identico al mapeo Historical del mismo record."""
    rec = _make_record()
    assert live_record_to_candle(rec) == DabentoConnector._record_to_candle(rec)
