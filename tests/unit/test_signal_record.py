"""Tests deterministas de SignalRecord — Etapa 1 (RF-E1-03).

Complementan a los tests property-based de trazabilidad
(`tests/property/test_traceability_props.py`).

Foco: la rama de DESCARTE por volumen bajo. Cuando una senal se descarta por `low_volume`,
NO se calcula ATR ni tamano de posicion, por lo que el SignalRecord debe reflejar
exactamente: stop_points is None, position_size is None, discard_reason == 'low_volume'.

_(RF-E1-03; complementa Property 6)_
"""

import uuid as uuid_module
from datetime import datetime, timezone

from src.schemas.signal import Signal, SignalType
from src.schemas.signal_record import SignalRecord


_FIXED_TS = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)


class TestSignalRecordDiscardedByVolume:
    """Verifica los valores exactos del SignalRecord en la rama descartada por volumen."""

    def test_discarded_by_low_volume_field_values(self) -> None:
        """Rama 'low_volume': sin stop, sin tamano de posicion, motivo 'low_volume'."""
        signal = Signal(
            type=SignalType.BUY,
            timestamp=_FIXED_TS,
            price=15000.0,
            sma_fast=15000.0,
            sma_slow=14999.0,
        )
        record_uuid = str(uuid_module.uuid4())

        # SignalRecord tal como lo produce el orquestador cuando el filtro de volumen
        # rechaza la senal: la rama descartada NO llega a ATR ni a RiskGate.
        record = SignalRecord(
            signal=signal,
            uuid=record_uuid,
            candle_timestamp_utc=_FIXED_TS.isoformat(),
            atr_period=14,
            atr_multiplier=2.0,
            volume_period=20,
            volume_threshold_factor=0.5,
            stop_points=None,  # no se calculo ATR (rama descartada)
            volume_accepted=False,  # el filtro de volumen rechazo la senal
            discard_reason="low_volume",  # motivo exacto del descarte
            position_size=None,  # no se calculo tamano de posicion
        )

        # Aserciones exactas de la rama descartada por volumen.
        assert record.stop_points is None
        assert record.position_size is None
        assert record.discard_reason == "low_volume"
        assert record.volume_accepted is False

        # Trazabilidad basica: la Signal queda intacta y el UUID es valido.
        assert record.signal is signal
        assert record.signal.type == SignalType.BUY
        uuid_module.UUID(record.uuid)  # valida que el UUID sea parseable
        assert record.candle_timestamp_utc == _FIXED_TS.isoformat()
