"""RiskGate — calculo del tamano de posicion de Etapa 1 (RF-E1-01, crit. 5-7).

Modulo NUEVO e independiente. NO modifica ni depende del motor de senales BUY/SELL
(SignalEngine). Consume el `Stop_Puntos` derivado del ATR y devuelve cuantos contratos
enteros operar; NUNCA altera la decision BUY/SELL.

Conceptos:
- RiskGate (modulo de gestion de riesgo): decide el TAMANO de la posicion (cuanto operar)
  en funcion del riesgo permitido y de la distancia del stop. Aqui su alcance es
  EXCLUSIVAMENTE el calculo del tamano de posicion; NO implementa circuit breaker,
  limite de senales concurrentes ni drawdown (esos pertenecen al Requisito 13, fuera de
  alcance de Etapa 1).
- tamano de la posicion: cantidad de contratos a operar. La formula produce un tamano
  TEORICO que puede ser fraccionario (ej. 0.5 contratos); este modulo lo convierte a un
  numero ENTERO de contratos operables mediante `floor` (redondeo hacia abajo). Ver
  `position_size()` para la politica de conversion confirmada.
- stop_pts (Stop_Puntos — distancia del stop expresada en puntos del instrumento):
  cuantos puntos separan el precio de entrada del stop-loss. En Etapa 1 se deriva del
  ATR (ver ATRCalculator). Si no esta disponible, no se puede dimensionar la posicion.
- value_per_point (valor_por_punto — valor monetario de cada punto del instrumento):
  cuanto dinero representa moverse 1 punto (ej. NQ estandar = 20 USD/punto). Es un hecho
  fijo del contrato; este modulo lo recibe como parametro, NO lo asume ni lo hardcodea.

Nota de gobernanza: `equity`, `risk_pct` y `value_per_point` son SIEMPRE parametros
provistos explicitamente por quien llama. Este modulo NO cablea valores productivos de
configuracion ni usa defaults silenciosos: la activacion productiva con los valores reales
de Hugo (valor_por_punto, riesgo_pct, equity_disponible) esta bloqueada por separado.
"""

# `math.floor` (redondeo hacia abajo al entero mas cercano que NO supera el valor) se usa
# para convertir el tamano teorico (posiblemente fraccionario) a contratos enteros
# operables. Ver la explicacion de la politica de conversion en `position_size()`.
import math


class RiskGate:
    """Calcula el tamano de posicion en CONTRATOS ENTEROS a partir de la distancia del stop.

    Formula del tamano teorico (RF-E1-01 crit. 5):
        tamano_teorico = equity * risk_pct / (stop_pts * value_per_point)

    Interpretacion: cuanto capital se arriesga (equity * risk_pct) dividido por cuanto
    dinero se pierde por contrato si se toca el stop (stop_pts * value_per_point). A mayor
    distancia de stop, menor tamano de posicion (relacion inversa).

    Conversion a contratos enteros (DECISION CONFIRMADA por Hugo): el tamano teorico se
    convierte a un numero ENTERO de contratos mediante `math.floor` (redondeo hacia abajo).
    """

    def __init__(self) -> None:
        # RiskGate no guarda estado de configuracion: todos los datos productivos
        # (equity, risk_pct, value_per_point) llegan como parametros de position_size().
        # Se expone `reason` para comunicar el motivo cuando no se puede dimensionar
        # (mismo mecanismo para 'stop_unavailable' y 'position_below_min_contract').
        self.reason: str | None = None

    def position_size(
        self,
        stop_pts: float | None,
        equity: float,
        risk_pct: float,
        value_per_point: float,
    ) -> int | None:
        """Devuelve el numero ENTERO de contratos operables, o None si no es operable.

        Args:
            stop_pts: distancia del stop en puntos (Stop_Puntos). Si es None o 0, no se
                puede dividir por el (division por cero) y no hay tamano de posicion.
            equity: capital disponible para operar (equity_disponible). Parametro
                explicito del llamador; NO un default productivo.
            risk_pct: porcentaje del capital que se arriesga por operacion (ej. 0.01 = 1%).
                Parametro explicito del llamador.
            value_per_point: valor monetario de cada punto del instrumento (ej. NQ = 20).
                Parametro explicito del llamador; hecho fijo del contrato.

        Returns:
            El numero ENTERO de contratos operables (int), o None cuando la senal no es
            operable. Al devolver None expone el motivo en `self.reason`:
              - "stop_unavailable"            -> stop_pts es None o 0 (RF-E1-01 crit. 7).
              - "position_below_min_contract" -> floor(tamano_teorico) == 0 (crit. 5-7).
            NUNCA lanza excepcion por division por cero.

        Nota de responsabilidad: esta funcion SOLO calcula el entero de contratos permitido
        y comunica la no-operabilidad via `self.reason`. NO orquesta el SignalPipeline ni
        construye SignalRecord: esa decision pertenece a la capa RiskGate/SignalPipeline.
        """
        # Proteccion division por cero (RF-E1-01 crit. 7 / Property 4): si no hay stop
        # disponible (None) o el stop es 0, NO se ejecuta la division. Se registra el
        # motivo `stop_unavailable` y se devuelve None, sin lanzar excepcion y sin afectar
        # la generacion de la senal BUY/SELL. Patron homologo al de ATRCalculator, que
        # devuelve None ante datos insuficientes, y al discard_reason de SignalRecord.
        if stop_pts is None or stop_pts == 0:
            self.reason = "stop_unavailable"
            return None

        # Tamano TEORICO de posicion (puede ser fraccionario, ej. 0.5 contratos):
        # capital arriesgado / perdida monetaria por contrato al tocar el stop.
        theoretical = equity * risk_pct / (stop_pts * value_per_point)

        # Conversion a contratos ENTEROS mediante `floor` (redondeo hacia abajo).
        # Se usa floor (y NO round ni ceil) porque redondear hacia arriba autorizaria un
        # riesgo real superior a `equity * risk_pct`, rompiendo el tope de riesgo. Con floor
        # el riesgo real (contracts * stop_pts * value_per_point) siempre queda <= al tope.
        contracts = math.floor(theoretical)

        # Si el tamano operable es 0 contratos, el riesgo permitido no alcanza ni para un
        # solo contrato (tamano sub-1-contrato). No se puede redondear hacia arriba sin
        # violar el tope de riesgo, asi que la senal NO es operable: se registra el motivo
        # `position_below_min_contract` y se devuelve None.
        if contracts == 0:
            self.reason = "position_below_min_contract"
            return None

        # Tamano operable valido (>= 1 contrato): se limpia el motivo y se devuelve el entero.
        self.reason = None
        return contracts
