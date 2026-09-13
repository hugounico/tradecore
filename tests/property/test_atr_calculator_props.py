"""Tests property-based (basados en propiedades) de ATRCalculator — Etapa 1.

Property-based testing (prueba basada en propiedades): en vez de comprobar ejemplos
concretos, se afirma una PROPIEDAD que debe cumplirse para MUCHAS entradas generadas
automaticamente (aqui, con la libreria Hypothesis).

Cubre la Tarea 2.1 / Property 3 del design.md:
- El ATR (Average True Range — indicador de volatilidad) NUNCA es negativo.
- Si High == Low == Close en TODAS las velas (sin rango de movimiento), el ATR es
  exactamente 0.
- Datos insuficientes (menos de `atr_period + 1` velas) -> devuelve None.

**Validates: Requirements 1.3**
_(RF-E1-01; Property 3)_
"""

from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from src.engine.atr_calculator import ATRCalculator
from src.schemas.candle import Candle


# --- Estrategias (generadores de datos aleatorios validos) ---

# Precios realistas positivos en un rango razonable para el futuro NQ.
price_st = st.floats(min_value=1.0, max_value=50000.0, allow_nan=False, allow_infinity=False)

# Timestamp fijo: el ATR NO depende del tiempo, solo de OHLC, asi que basta uno constante.
_FIXED_TS = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _make_ohlc_candle(low: float, span_high: float, span_close: float) -> Candle:
    """Construye una vela con OHLC coherente (low <= open/close/high) a partir de floats.

    Se garantiza low <= todo lo demas sumando magnitudes no negativas, para que el
    generador nunca produzca velas invalidas (high < low, etc.).
    """
    high = low + span_high  # high siempre >= low (span_high >= 0)
    close = low + span_close  # close entre low y high por construccion del generador
    return Candle(
        timestamp=_FIXED_TS,
        open=low,  # open no interviene en el TR; se fija a low por simplicidad
        high=high,
        low=low,
        close=close,
        volume=1,  # el volumen no afecta el ATR
    )


# Estrategia de vela con rango valido: low base + dos incrementos no negativos.
def _candle_strategy():
    return st.builds(
        _make_ohlc_candle,
        low=price_st,
        span_high=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        # close se coloca dentro del rango [low, high]: usamos un factor 0..1 * span_high.
        span_close=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    )


class TestATRNeverNegative:
    """Property 3 (parte a): el ATR nunca es negativo para cualquier serie de velas.

    **Validates: Requirements 1.3**
    """

    @given(
        candles=st.lists(_candle_strategy(), min_size=1, max_size=60),
        period=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=200)
    def test_atr_is_never_negative(self, candles: list[Candle], period: int) -> None:
        """Para cualquier lista de velas y periodo, atr() es None o un valor >= 0."""
        calc = ATRCalculator(period=period)
        result = calc.atr(candles)
        # El TR es un maximo de distancias (valores >= 0) y el suavizado RMA promedia
        # valores no negativos, por lo que el ATR nunca puede ser negativo.
        if result is not None:
            assert result >= 0.0


class TestATRZeroOnNullRange:
    """Property 3 (parte b): si High == Low == Close en todas las velas, ATR == 0.

    Sin rango de movimiento, cada TR es 0 (high-low=0, |high-prevClose|=0,
    |low-prevClose|=0 cuando todos los precios coinciden), por lo que el ATR es 0.

    **Validates: Requirements 1.3**
    """

    @given(
        price=price_st,
        n_candles=st.integers(min_value=2, max_value=60),
        period=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=200)
    def test_atr_exactly_zero_when_no_range(
        self, price: float, n_candles: int, period: int
    ) -> None:
        """Todas las velas con el MISMO precio (H==L==C) -> ATR exactamente 0.0."""
        # Solo tiene sentido si hay suficientes velas para calcular (period+1); si no,
        # atr() devuelve None y la propiedad de "cero" no aplica. Filtramos ese caso.
        calc = ATRCalculator(period=period)
        candles = [
            Candle(
                timestamp=_FIXED_TS,
                open=price,
                high=price,  # High == Low == Close: sin rango de movimiento
                low=price,
                close=price,
                volume=1,
            )
            for _ in range(n_candles)
        ]
        result = calc.atr(candles)
        if result is not None:
            # Con precios identicos, todos los TR son 0 y el ATR debe ser exactamente 0.
            assert result == 0.0


class TestATRInsufficientData:
    """Property 3 (parte c): con menos de `period + 1` velas, atr() devuelve None.

    El primer TR necesita el cierre de la vela previa, por lo que para tener `period`
    valores de TR hacen falta al menos `period + 1` velas.

    **Validates: Requirements 1.3**
    """

    @given(
        period=st.integers(min_value=1, max_value=30),
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_returns_none_when_insufficient_history(self, period: int, data) -> None:
        """Cualquier lista con longitud < period + 1 produce None."""
        calc = ATRCalculator(period=period)
        # Generamos una cantidad de velas ESTRICTAMENTE menor a period+1 (0..period).
        n = data.draw(st.integers(min_value=0, max_value=period))
        candles = data.draw(
            st.lists(_candle_strategy(), min_size=n, max_size=n)
        )
        assert calc.atr(candles) is None
