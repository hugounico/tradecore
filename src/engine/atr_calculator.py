"""ATRCalculator — calculo del ATR (Average True Range) para el stop dinamico de Etapa 1.

Modulo NUEVO e independiente. NO modifica ni depende del motor de senales BUY/SELL
(SignalEngine) — solo consume objetos Candle ya existentes.

Conceptos:
- ATR (Average True Range — rango verdadero promedio): indicador que mide la
  volatilidad reciente del precio, es decir, cuanto se mueve el precio en promedio.
- TR (True Range — rango verdadero de una vela): la mayor de tres distancias que
  captura el movimiento real incluyendo saltos (gaps) entre el cierre anterior y la
  vela actual.
- RMA/Wilder (Wilder's Moving Average — suavizado exponencial de Welles Wilder):
  metodo de promediado que da mas peso al historial acumulado que una media simple.
  Es el metodo PROPIO de TradeCore para el ATR, decidido de forma independiente.
"""

from src.schemas.candle import Candle


class ATRCalculator:
    """Calcula el ATR con suavizado RMA/Wilder y deriva la distancia del stop en puntos.

    El ATR se calcula sobre `period` velas. El metodo de suavizado es RMA/Wilder:
    se siembra (seed) con la media simple de los primeros `period` valores de TR y
    luego se aplica la recurrencia de Wilder para cada TR posterior.
    """

    def __init__(self, period: int = 14) -> None:
        # period: cantidad de velas usadas para promediar el TR. Default 14 (Wilder).
        self.period = period

    @staticmethod
    def _true_range(current: Candle, prev_close: float) -> float:
        """Calcula el True Range (rango verdadero) de una vela.

        TR = max( high-low, |high-close_prev|, |low-close_prev| ).
        Se usa el cierre de la vela anterior (prev_close) para capturar saltos de precio
        (gaps) que ocurren entre el cierre de una vela y la apertura de la siguiente.
        """
        # Rango intra-vela: distancia entre maximo y minimo de la vela actual.
        high_low = current.high - current.low
        # Salto hacia arriba: distancia entre el maximo actual y el cierre anterior.
        high_prev_close = abs(current.high - prev_close)
        # Salto hacia abajo: distancia entre el minimo actual y el cierre anterior.
        low_prev_close = abs(current.low - prev_close)
        # El TR es la mayor de las tres distancias (el movimiento "real" mas amplio).
        return max(high_low, high_prev_close, low_prev_close)

    def atr(self, candles: list[Candle]) -> float | None:
        """Devuelve el ATR (suavizado RMA/Wilder) sobre las velas dadas, o None.

        Datos insuficientes (RF-E1-01 crit. 4): el primer TR necesita un cierre previo,
        por lo que para tener `period` valores de TR hacen falta al menos `period + 1`
        velas. Con menos de eso, devuelve None (el orquestador registrara el motivo
        `insufficient_history_atr` y omitira el calculo de tamano de posicion).
        """
        # Se necesitan al menos period+1 velas: cada TR usa el cierre de la vela previa.
        if len(candles) < self.period + 1:
            return None

        # Lista de TR: recorre desde la segunda vela (indice 1) porque el primer TR
        # necesita el cierre de la vela anterior (candles[i-1]).
        true_ranges: list[float] = []
        for i in range(1, len(candles)):
            tr = self._true_range(candles[i], candles[i - 1].close)
            true_ranges.append(tr)

        # Seed inicial de Wilder: media simple de los primeros `period` valores de TR.
        # (No usamos SMA continuo; el promedio simple solo inicializa la recurrencia.)
        atr_value = sum(true_ranges[: self.period]) / self.period

        # Recurrencia de Wilder para cada TR posterior al bloque de seed:
        # ATR_t = (ATR_{t-1} * (N-1) + TR_t) / N. Suaviza dando peso al historico.
        for tr in true_ranges[self.period :]:
            atr_value = (atr_value * (self.period - 1) + tr) / self.period

        return atr_value

    def stop_points(self, candles: list[Candle], multiplier: float) -> float | None:
        """Calcula la distancia del stop en puntos: Stop_Puntos = ATR_actual * multiplier.

        Si no hay ATR (datos insuficientes), devuelve None sin lanzar excepcion, para que
        el orquestador registre el motivo y omita el tamano de posicion sin afectar la
        decision BUY/SELL del motor.
        """
        atr_value = self.atr(candles)
        if atr_value is None:
            # Datos insuficientes: se propaga None (motivo insufficient_history_atr).
            return None
        # El multiplicador (atr_multiplier) es configurable y se fija en la calibracion.
        return atr_value * multiplier
