"""SignalRecord — envoltura de trazabilidad de una senal de Etapa 1 (RF-E1-03).

Modulo NUEVO. `Signal` (src/schemas/signal.py) es un dataclass frozen+slots protegido:
NO se le pueden agregar atributos ni se modifica. Por eso SignalRecord lo ENVUELVE
(composicion): guarda la Signal original intacta y le adjunta los datos matematicos de
Etapa 1 necesarios para reproducir el resultado (UUID, timestamp UTC, configuracion,
stop, decision del filtro de volumen y tamano de posicion).
"""

from dataclasses import dataclass

from src.schemas.signal import Signal


@dataclass(frozen=True, slots=True)
class SignalRecord:
    """Registro reproducible de una senal (emitida o descartada) bajo Etapa 1.

    Se genera TANTO para senales emitidas como descartadas (RF-E1-03 crit. 1). Es
    inmutable (frozen) para que el registro sea una foto fiel del momento de la senal.
    """

    signal: Signal  # la Signal original, intacta (no se modifica el componente protegido)
    uuid: str  # UUID — identificador unico universal del registro (RF-E1-03 crit. 2)
    candle_timestamp_utc: str  # ISO 8601 UTC de la vela origen (RF-E1-03 crit. 3)

    # Configuracion matematica usada (RF-E1-03 crit. 1) — permite reproducir el resultado.
    atr_period: int  # periodo del ATR (cantidad de velas)
    atr_multiplier: float  # multiplicador del ATR para derivar el stop
    volume_period: int  # N periodos del filtro de volumen
    volume_threshold_factor: float  # criterio del umbral de volumen

    # Resultados derivados del procesamiento de Etapa 1.
    stop_points: float | None  # distancia del stop en puntos; None si no hubo ATR
    volume_accepted: bool  # True si paso el filtro de volumen
    # discard_reason: motivo de descarte o ausencia de dato. Valores esperados:
    # 'low_volume', 'insufficient_history_atr', 'stop_unavailable' o None (sin descarte).
    discard_reason: str | None
    position_size: float | None  # tamano de posicion (lotes); None si no se pudo calcular
