"""Tests deterministas (de valor conocido) de ATRCalculator — Etapa 1.

Complementan a los tests property-based (`tests/property/test_atr_calculator_props.py`):
aqui se verifica la FORMULA EXACTA de Wilder con un valor calculado a mano, no solo
propiedades de frontera.

Metodo verificado (RMA/Wilder, decision propia de TradeCore — design.md punto 10(b)):
- TR de cada vela = max( high-low, |high-prevClose|, |low-prevClose| ).
- Seed = media simple de los primeros `period` valores de TR.
- Recurrencia: ATR_t = (ATR_{t-1} * (period-1) + TR_t) / period para cada TR posterior.

_(RF-E1-01; complementa Property 3)_
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.engine.atr_calculator import ATRCalculator
from src.schemas.candle import Candle


_BASE_TS = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _candle(high: float, low: float, close: float, index: int) -> Candle:
    """Vela con OHLC fijo (open no interviene en el TR, se fija al close)."""
    return Candle(
        timestamp=_BASE_TS + timedelta(minutes=index),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1,
    )


class TestATRDeterministicWilder:
    """Verifica el valor exacto del ATR con una secuencia fija y period=3.

    Secuencia fija (high, low, close):
      C0: h=?,   l=?,   c=100   (solo aporta close previo; su TR no se calcula)
      C1: h=105, l=99,  c=102
      C2: h=104, l=98,  c=101
      C3: h=110, l=103, c=106
      C4: h=107, l=100, c=104
      C5: h=112, l=105, c=108

    TR calculado a mano (TR_i usa candles[i] y candles[i-1].close):
      TR1 = max(105-99=6,  |105-100|=5, |99-100|=1)  = 6
      TR2 = max(104-98=6,  |104-102|=2, |98-102|=4)  = 6
      TR3 = max(110-103=7, |110-101|=9, |103-101|=2) = 9
      TR4 = max(107-100=7, |107-106|=1, |100-106|=6) = 7
      TR5 = max(112-105=7, |112-104|=8, |105-104|=1) = 8
    TR = [6, 6, 9, 7, 8], period = 3.

    Seed = media de los primeros 3 TR = (6+6+9)/3 = 7.0
    Paso TR4=7: ATR = (7.0*2 + 7)/3 = 21/3 = 7.0
    Paso TR5=8: ATR = (7.0*2 + 8)/3 = 22/3 = 7.333333...

    ATR esperado = 22/3 ≈ 7.3333333333
    """

    def _fixed_sequence(self) -> list[Candle]:
        return [
            _candle(high=100.0, low=100.0, close=100.0, index=0),  # solo aporta close previo
            _candle(high=105.0, low=99.0, close=102.0, index=1),
            _candle(high=104.0, low=98.0, close=101.0, index=2),
            _candle(high=110.0, low=103.0, close=106.0, index=3),
            _candle(high=107.0, low=100.0, close=104.0, index=4),
            _candle(high=112.0, low=105.0, close=108.0, index=5),
        ]

    def test_atr_matches_hand_computed_wilder_value(self) -> None:
        """El ATR calculado por el modulo debe igualar el valor calculado a mano (22/3)."""
        calc = ATRCalculator(period=3)
        candles = self._fixed_sequence()

        expected = 22.0 / 3.0  # ≈ 7.3333333333, derivado a mano arriba

        result = calc.atr(candles)
        assert result is not None
        # pytest.approx para tolerar el redondeo de punto flotante.
        assert result == pytest.approx(expected)

    def test_stop_points_uses_atr_times_multiplier(self) -> None:
        """stop_points = ATR * multiplier (verifica el consumo del ATR determinista)."""
        calc = ATRCalculator(period=3)
        candles = self._fixed_sequence()
        multiplier = 2.0

        expected = (22.0 / 3.0) * multiplier

        result = calc.stop_points(candles, multiplier)
        assert result is not None
        assert result == pytest.approx(expected)


class TestATRInsufficientDataDeterministic:
    """Con menos de period+1 velas, atr() y stop_points() devuelven None."""

    def test_atr_none_with_exactly_period_candles(self) -> None:
        calc = ATRCalculator(period=3)
        # 3 velas = period; se necesitan period+1 = 4. Debe devolver None.
        candles = [
            _candle(high=105.0, low=99.0, close=102.0, index=i) for i in range(3)
        ]
        assert calc.atr(candles) is None
        assert calc.stop_points(candles, 2.0) is None
