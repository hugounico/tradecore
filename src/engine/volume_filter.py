"""VolumeFilter — filtro de volumen sobre senales YA generadas (Etapa 1, RF-E1-02).

Modulo NUEVO e independiente. Actua DESPUES de que el motor decide BUY/SELL: acepta o
rechaza la senal, pero NUNCA cambia su direccion. No modifica el motor ni el buffer.

Concepto:
- volumen (cantidad negociada en un periodo): poca participacion del mercado implica
  una senal menos confiable, por eso se descarta con volumen anomalamente bajo.
"""

from src.schemas.candle import Candle


class VolumeFilter:
    """Acepta o rechaza una senal comparando el volumen de su vela contra el promedio.

    Regla: la senal se acepta si el volumen de la vela es >= threshold_factor * promedio
    del volumen de las ultimas `period` velas. Si no, se rechaza con motivo `low_volume`.
    """

    def __init__(self, period: int = 20) -> None:
        # period (N): cantidad de velas sobre las que se promedia el volumen. Default 20.
        self.period = period

    def accept(
        self,
        candle: Candle,
        recent_candles: list[Candle],
        threshold_factor: float,
    ) -> tuple[bool, str | None]:
        """Decide si la senal de `candle` pasa el filtro de volumen.

        Args:
            candle: la vela que origino la senal (su volumen es el que se evalua).
            recent_candles: velas recientes usadas para calcular el promedio de volumen.
            threshold_factor: factor minimo respecto al promedio (ej. 0.5 = al menos el
                50% del volumen promedio). Configurable; se fija en la calibracion.

        Returns:
            (aceptada, motivo). Si se acepta -> (True, None). Si se rechaza ->
            (False, "low_volume"). NUNCA devuelve informacion de direccion: este filtro
            no conoce ni altera si la senal es BUY o SELL.
        """
        # Tomamos las ultimas `period` velas disponibles para el promedio. Si hay menos,
        # promediamos sobre las que existan (no bloqueamos por historia corta: el filtro
        # de volumen es un criterio de aceptacion, no un calculo que requiera N exacto).
        window = recent_candles[-self.period :]
        if not window:
            # Sin velas de referencia no hay promedio contra el cual comparar: en ausencia
            # de datos para rechazar, se deja pasar la senal (el filtro no inventa rechazo).
            return True, None

        # Promedio simple del volumen de la ventana de referencia.
        avg_volume = sum(c.volume for c in window) / len(window)

        # Umbral de referencia: fraccion del volumen promedio exigida a la vela.
        threshold = threshold_factor * avg_volume

        if candle.volume >= threshold:
            # Volumen suficiente: la senal continua su flujo (hacia ATR y RiskGate).
            return True, None
        # Volumen bajo el umbral: se descarta con motivo `low_volume` (patron homologo
        # al descarte por `low_confidence` del Requisito 5.2).
        return False, "low_volume"
