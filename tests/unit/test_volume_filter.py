"""Tests deterministas de VolumeFilter — Etapa 1.

Complementan a los tests property-based (`tests/property/test_volume_filter_props.py`).
Foco: el comportamiento EXACTO del operador de comparacion en el limite (boundary).

El modulo usa `candle.volume >= threshold`, por lo que un volumen EXACTAMENTE igual al
umbral debe ACEPTARSE -> (True, None).

_(RF-E1-02; complementa Property 2)_
"""

from datetime import datetime, timezone

from src.engine.volume_filter import VolumeFilter
from src.schemas.candle import Candle


_FIXED_TS = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _candle(volume: int) -> Candle:
    return Candle(
        timestamp=_FIXED_TS,
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=volume,
    )


class TestVolumeFilterBoundary:
    """Caso de frontera: volumen exactamente igual al umbral (threshold_factor * promedio)."""

    def test_volume_exactly_at_threshold_is_accepted(self) -> None:
        """volumen == threshold_factor * promedio -> (True, None) por el operador `>=`.

        Promedio elegido para que el umbral sea entero exacto:
          recent = 4 velas de volumen 100 -> promedio = 100.
          threshold_factor = 0.5 -> umbral = 0.5 * 100 = 50.
          Vela con volumen = 50 (exactamente el umbral) debe ACEPTARSE.
        """
        vfilter = VolumeFilter(period=20)
        recent = [_candle(100) for _ in range(4)]  # promedio = 100
        threshold_factor = 0.5
        # umbral = 0.5 * 100 = 50; volumen de la vela EXACTAMENTE 50.
        candle = _candle(50)

        accepted, reason = vfilter.accept(candle, recent, threshold_factor)

        assert accepted is True
        assert reason is None

    def test_volume_one_below_threshold_is_rejected(self) -> None:
        """Un volumen justo por debajo del umbral se rechaza con 'low_volume'."""
        vfilter = VolumeFilter(period=20)
        recent = [_candle(100) for _ in range(4)]  # promedio = 100
        threshold_factor = 0.5  # umbral = 50
        candle = _candle(49)  # 49 < 50

        accepted, reason = vfilter.accept(candle, recent, threshold_factor)

        assert accepted is False
        assert reason == "low_volume"

    def test_empty_recent_candles_passes(self) -> None:
        """Sin velas de referencia no hay promedio: el filtro deja pasar (True, None)."""
        vfilter = VolumeFilter(period=20)
        accepted, reason = vfilter.accept(_candle(1), [], 0.5)
        assert accepted is True
        assert reason is None
