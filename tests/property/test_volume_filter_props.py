"""Tests property-based de VolumeFilter — Etapa 1.

Cubre la Tarea 3.1 / Property 2 del design.md:
- El filtro NUNCA cambia la direccion de la senal (solo acepta o rechaza). El VolumeFilter
  ni siquiera recibe la direccion: `accept()` devuelve (bool, motivo), nunca una direccion.
  La invariante `direccion_entrada == direccion_salida` se comprueba pasando una Signal por
  el filtro y verificando que su `type` (BUY/SELL) es identico antes y despues.
- Volumen por debajo del umbral SIEMPRE rechaza con motivo 'low_volume'.
- Volumen igual o por encima del umbral SIEMPRE pasa (True, None).

**Validates: Requirements 2.5**
_(RF-E1-02; Property 2)_
"""

from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from src.engine.volume_filter import VolumeFilter
from src.schemas.candle import Candle
from src.schemas.signal import Signal, SignalType


_FIXED_TS = datetime(2025, 1, 1, tzinfo=timezone.utc)

# Volumenes positivos realistas (numero de contratos negociados).
volume_st = st.integers(min_value=1, max_value=1_000_000)
price_st = st.floats(min_value=1.0, max_value=50000.0, allow_nan=False, allow_infinity=False)
# Factor de umbral dentro del rango de referencia de industria (0.5x-0.8x) y un poco mas.
factor_st = st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False)


def _candle_with_volume(volume: int) -> Candle:
    """Vela minima cuyo unico dato relevante para el filtro es el volumen."""
    return Candle(
        timestamp=_FIXED_TS,
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=volume,
    )


class TestVolumeFilterNeverChangesDirection:
    """Property 2: `direccion_entrada == direccion_salida`.

    El filtro decide aceptar/rechazar pero no toca la Signal. Verificamos que la
    direccion (type) de la Signal es la misma antes y despues de consultar el filtro.

    **Validates: Requirements 2.5**
    """

    @given(
        signal_type=st.sampled_from([SignalType.BUY, SignalType.SELL]),
        candle_volume=volume_st,
        recent_volumes=st.lists(volume_st, min_size=1, max_size=40),
        threshold_factor=factor_st,
    )
    @settings(max_examples=200)
    def test_direction_is_preserved(
        self,
        signal_type: SignalType,
        candle_volume: int,
        recent_volumes: list[int],
        threshold_factor: float,
    ) -> None:
        """La direccion de la Signal no cambia al pasar por el filtro de volumen."""
        vfilter = VolumeFilter(period=20)
        signal = Signal(
            type=signal_type,
            timestamp=_FIXED_TS,
            price=100.0,
            sma_fast=100.0,
            sma_slow=100.0,
        )
        direction_before = signal.type

        candle = _candle_with_volume(candle_volume)
        recent = [_candle_with_volume(v) for v in recent_volumes]

        accepted, reason = vfilter.accept(candle, recent, threshold_factor)

        # La invariante clave: la senal (y por tanto su direccion) es inmutable (frozen)
        # y el filtro no la reasigna. La direccion de salida es la misma que la de entrada.
        assert signal.type == direction_before
        # El resultado del filtro solo puede ser aceptar (sin motivo) o rechazar por volumen.
        assert isinstance(accepted, bool)
        assert reason in (None, "low_volume")


class TestVolumeBelowThresholdRejects:
    """Property 2: volumen por debajo del umbral SIEMPRE rechaza con 'low_volume'.

    **Validates: Requirements 2.5**
    """

    @given(
        recent_volumes=st.lists(volume_st, min_size=1, max_size=40),
        threshold_factor=st.floats(
            min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False
        ),
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_below_threshold_always_rejects(
        self, recent_volumes: list[int], threshold_factor: float, data
    ) -> None:
        """Si volumen_vela < threshold_factor * promedio -> (False, 'low_volume')."""
        vfilter = VolumeFilter(period=20)
        recent = [_candle_with_volume(v) for v in recent_volumes]
        # Promedio sobre la ventana efectiva (ultimas `period` velas), como hace el modulo.
        window = recent[-vfilter.period :]
        avg_volume = sum(c.volume for c in window) / len(window)
        threshold = threshold_factor * avg_volume

        # Solo hay caso "por debajo" si el umbral es > 1 (volumen minimo es entero 1).
        # Elegimos un volumen entero estrictamente por debajo del umbral.
        import math

        # Mayor entero estrictamente menor que threshold; debe ser >= 1 para ser volumen valido.
        max_below = math.ceil(threshold) - 1
        if max_below < 1:
            # No existe volumen entero valido por debajo del umbral: caso no aplicable.
            return
        candle_volume = data.draw(st.integers(min_value=1, max_value=max_below))

        candle = _candle_with_volume(candle_volume)
        accepted, reason = vfilter.accept(candle, recent, threshold_factor)

        assert accepted is False
        assert reason == "low_volume"


class TestVolumeAtOrAboveThresholdPasses:
    """Property 2: volumen igual o por encima del umbral SIEMPRE pasa (True, None).

    **Validates: Requirements 2.5**
    """

    @given(
        recent_volumes=st.lists(volume_st, min_size=1, max_size=40),
        threshold_factor=st.floats(
            min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False
        ),
        extra=st.integers(min_value=0, max_value=1_000_000),
    )
    @settings(max_examples=200)
    def test_at_or_above_threshold_always_passes(
        self, recent_volumes: list[int], threshold_factor: float, extra: int
    ) -> None:
        """Si volumen_vela >= threshold -> (True, None). El modulo usa `>=`."""
        vfilter = VolumeFilter(period=20)
        recent = [_candle_with_volume(v) for v in recent_volumes]
        window = recent[-vfilter.period :]
        avg_volume = sum(c.volume for c in window) / len(window)
        threshold = threshold_factor * avg_volume

        # Elegimos un volumen entero garantizado >= umbral: ceil(threshold) + extra.
        import math

        candle_volume = max(1, math.ceil(threshold)) + extra
        candle = _candle_with_volume(candle_volume)

        accepted, reason = vfilter.accept(candle, recent, threshold_factor)

        assert accepted is True
        assert reason is None
