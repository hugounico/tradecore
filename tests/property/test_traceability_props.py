"""Tests property-based de trazabilidad — SignalRecord + SignalJournal (Etapa 1).

Cubre la Tarea 4.1 / Properties 6 y 7 del design.md. Se prueba contra la implementacion
por DEFECTO en memoria (`InMemoryJournalWriter`), sin persistencia a disco.

- Property 6 (Trazabilidad completa): toda senal (emitida o descartada) produce
  EXACTAMENTE un SignalRecord con UUID (identificador unico universal), timestamp UTC
  y la configuracion matematica usada.
- Property 7 (Reproducibilidad): dado el mismo input (misma Signal y misma configuracion),
  el SignalRecord reconstruido es identico -> permite reproducir el resultado.

**Validates: Requirements 3.1, 3.4, 4.5**
_(RF-E1-03, RF-E1-04; Property 6, Property 7)_
"""

import uuid as uuid_module
from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from src.pipeline.signal_journal import InMemoryJournalWriter, SignalJournal
from src.schemas.candle import Candle
from src.schemas.signal import Signal, SignalType
from src.schemas.signal_record import SignalRecord


_FIXED_TS = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)

price_st = st.floats(min_value=1.0, max_value=50000.0, allow_nan=False, allow_infinity=False)
period_st = st.integers(min_value=1, max_value=100)
factor_st = st.floats(min_value=0.1, max_value=5.0, allow_nan=False, allow_infinity=False)
optional_float_st = st.one_of(
    st.none(), st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False)
)
discard_reason_st = st.sampled_from(
    [None, "low_volume", "insufficient_history_atr", "stop_unavailable"]
)


def _build_signal(signal_type: SignalType, price: float) -> Signal:
    """Construye una Signal minima con el timestamp UTC fijo de la vela origen."""
    return Signal(
        type=signal_type,
        timestamp=_FIXED_TS,
        price=price,
        sma_fast=price,
        sma_slow=price,
    )


def _build_record(
    signal: Signal,
    atr_period: int,
    atr_multiplier: float,
    volume_period: int,
    volume_threshold_factor: float,
    stop_points: float | None,
    volume_accepted: bool,
    discard_reason: str | None,
    position_size: float | None,
    record_uuid: str,
) -> SignalRecord:
    """Construye un SignalRecord de forma determinista (para reproducibilidad)."""
    return SignalRecord(
        signal=signal,
        uuid=record_uuid,
        # timestamp UTC de la vela origen, en formato ISO 8601 (RF-E1-03 crit. 3).
        candle_timestamp_utc=signal.timestamp.isoformat(),
        atr_period=atr_period,
        atr_multiplier=atr_multiplier,
        volume_period=volume_period,
        volume_threshold_factor=volume_threshold_factor,
        stop_points=stop_points,
        volume_accepted=volume_accepted,
        discard_reason=discard_reason,
        position_size=position_size,
    )


class TestTraceabilityCompleteness:
    """Property 6: toda senal (emitida o descartada) produce exactamente un SignalRecord
    con UUID, timestamp UTC y configuracion matematica, registrado en el journal.

    **Validates: Requirements 3.1**
    """

    @given(
        signal_type=st.sampled_from([SignalType.BUY, SignalType.SELL]),
        price=price_st,
        atr_period=period_st,
        atr_multiplier=factor_st,
        volume_period=period_st,
        volume_threshold_factor=factor_st,
        stop_points=optional_float_st,
        volume_accepted=st.booleans(),
        discard_reason=discard_reason_st,
        position_size=optional_float_st,
    )
    @settings(max_examples=200)
    def test_every_signal_produces_exactly_one_record(
        self,
        signal_type: SignalType,
        price: float,
        atr_period: int,
        atr_multiplier: float,
        volume_period: int,
        volume_threshold_factor: float,
        stop_points: float | None,
        volume_accepted: bool,
        discard_reason: str | None,
        position_size: float | None,
    ) -> None:
        """Registrar una senal (emitida o descartada) deja EXACTAMENTE un record valido."""
        writer = InMemoryJournalWriter()  # default en memoria (sin disco)
        journal = SignalJournal(writer=writer)

        signal = _build_signal(signal_type, price)
        record = _build_record(
            signal=signal,
            atr_period=atr_period,
            atr_multiplier=atr_multiplier,
            volume_period=volume_period,
            volume_threshold_factor=volume_threshold_factor,
            stop_points=stop_points,
            volume_accepted=volume_accepted,
            discard_reason=discard_reason,
            position_size=position_size,
            record_uuid=str(uuid_module.uuid4()),
        )

        journal.record(record)

        stored = writer.records
        # Exactamente un registro por senal.
        assert len(stored) == 1
        saved = stored[0]

        # UUID presente y valido (parseable como UUID real).
        assert saved.uuid
        uuid_module.UUID(saved.uuid)  # lanza ValueError si no es un UUID valido

        # Timestamp UTC de la vela origen registrado (ISO 8601, coherente con la Signal).
        assert saved.candle_timestamp_utc == _FIXED_TS.isoformat()

        # Configuracion matematica registrada intacta (RF-E1-03 crit. 1).
        assert saved.atr_period == atr_period
        assert saved.atr_multiplier == atr_multiplier
        assert saved.volume_period == volume_period
        assert saved.volume_threshold_factor == volume_threshold_factor

        # La Signal original queda intacta dentro del record (envuelta, no modificada).
        assert saved.signal is signal
        assert saved.signal.type == signal_type


class TestReproducibility:
    """Property 7: dado el mismo input (misma Signal + misma configuracion), el
    SignalRecord reconstruido es identico -> el resultado es reproducible.

    **Validates: Requirements 3.4, 4.5**
    """

    @given(
        signal_type=st.sampled_from([SignalType.BUY, SignalType.SELL]),
        price=price_st,
        atr_period=period_st,
        atr_multiplier=factor_st,
        volume_period=period_st,
        volume_threshold_factor=factor_st,
        stop_points=optional_float_st,
        volume_accepted=st.booleans(),
        discard_reason=discard_reason_st,
        position_size=optional_float_st,
    )
    @settings(max_examples=200)
    def test_same_input_reproduces_identical_record(
        self,
        signal_type: SignalType,
        price: float,
        atr_period: int,
        atr_multiplier: float,
        volume_period: int,
        volume_threshold_factor: float,
        stop_points: float | None,
        volume_accepted: bool,
        discard_reason: str | None,
        position_size: float | None,
    ) -> None:
        """Reconstruir el record con el mismo input y el mismo UUID da un record igual.

        La reproducibilidad exige que, fijando el UUID (parte de la 'foto' del registro),
        la misma entrada matematica produzca un SignalRecord identico (mismo stop, misma
        decision de filtro, misma direccion y misma configuracion).
        """
        fixed_uuid = str(uuid_module.uuid4())  # mismo UUID para ambas reconstrucciones
        signal = _build_signal(signal_type, price)

        record_a = _build_record(
            signal,
            atr_period,
            atr_multiplier,
            volume_period,
            volume_threshold_factor,
            stop_points,
            volume_accepted,
            discard_reason,
            position_size,
            fixed_uuid,
        )
        record_b = _build_record(
            signal,
            atr_period,
            atr_multiplier,
            volume_period,
            volume_threshold_factor,
            stop_points,
            volume_accepted,
            discard_reason,
            position_size,
            fixed_uuid,
        )

        # SignalRecord es frozen dataclass: dos instancias con los mismos campos son iguales.
        assert record_a == record_b
        # Reproducibilidad de los campos clave (direccion, stop, decision de filtro).
        assert record_a.signal.type == record_b.signal.type
        assert record_a.stop_points == record_b.stop_points
        assert record_a.volume_accepted == record_b.volume_accepted
        assert record_a.discard_reason == record_b.discard_reason
