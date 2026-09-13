"""LAYER A — Observaciones por senal: STOP PATH / SIGNAL PATH, rescate y contadores.

Implementa EXACTAMENTE la semantica congelada del design.md:
- 9-bis.8-ALT (SIGNAL/STOP PATH, rescued_signal, productive_rescue),
- 5.3-5.8 (semantica ternaria de stop_would_trigger y censura),
- 9-bis.4 y 9-bis.8-ALT (contadores de poblacion y censura).

Reglas duras que NO se violan:
- La busqueda del STOP PATH empieza en t+1 (la vela de la senal `t` NO participa).
- `stop_would_trigger` es TERNARIO: "true" / "false" / "unknown". NUNCA unknown->false.
- `signal_path_censored` y `stop_path_censored` son campos INDEPENDIENTES.
- El rescate solo se define para multiplicadores CONSECUTIVOS.
- La ventana del rescate productivo empieza en la vela INMEDIATAMENTE POSTERIOR al
  primer toque del stop (la vela del toque queda EXCLUIDA aunque alcance reference_price).
- Los `unknown` quedan FUERA del denominador observable y SIEMPRE se reportan.

Glosario:
- STOP PATH (trayectoria del stop): observacion de un stop ATR especifico desde t+1
  hasta el PRIMERO de: toque del stop, cruce inverso, o limite del split.
- SIGNAL PATH (trayectoria de la senal): comportamiento posterior completo de la senal
  SMA hasta el cruce inverso.
- reference_price (precio de referencia): ancla matematica = Signal.price. NO es un
  precio de ejecucion.
- cruce inverso (inverse crossover): la siguiente senal SMA de direccion opuesta.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.calibration.grid import CONSECUTIVE_ATR_STEPS
from src.schemas.candle import Candle
from src.schemas.signal import SignalType


class TriggerState(str, Enum):
    """Estado TERNARIO de `stop_would_trigger` (design.md 5.3). NO es booleano puro.

    - TRUE: se OBSERVO que el stop fue tocado antes de terminar la ventana observable.
    - FALSE: el cruce inverso ocurrio antes del limite Y no hubo toque en toda la ventana
      -> evidencia SUFICIENTE de que el stop no se habria activado.
    - UNKNOWN: se alcanzo el limite del split SIN toque y SIN cruce inverso -> censurada;
      NO hay evidencia para afirmar `false`.
    """

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class RescueState(str, Enum):
    """Estado TERNARIO del rescate marginal (design.md 9-bis.8-ALT)."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class ProductiveState(str, Enum):
    """Estado TERNARIO del rescate productivo (design.md 9-bis.8-ALT)."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def stop_would_trigger(
    direction: SignalType,
    reference_price: float,
    stop_points: float,
    post_signal_bars: list[Candle],
    inverse_crossover_index_or_None: int | None,
    calibration_boundary_reached_flag: bool,
) -> TriggerState:
    """Evalua la semantica TERNARIA de `stop_would_trigger` para un stop ATR especifico.

    Args:
        direction: BUY o SELL (define hacia donde se mide el toque del stop).
        reference_price: ancla de la senal (Signal.price).
        stop_points: distancia FIJA del stop = ATR_at_signal * atr_multiplier.
        post_signal_bars: velas del STOP PATH desde t+1 (la vela de la senal EXCLUIDA).
            El indice 0 de esta lista corresponde a la vela t+1.
        inverse_crossover_index_or_None: indice DENTRO de `post_signal_bars` de la vela
            donde el motor SMA confirma el cruce inverso (la vela del cruce SI se incluye
            en la ventana). None si no hay cruce inverso dentro de las velas provistas.
        calibration_boundary_reached_flag: True si `post_signal_bars` termina porque se
            alcanzo el limite del split de Calibracion sin cruce inverso posterior.

    Returns:
        TriggerState.TRUE / FALSE / UNKNOWN segun la logica de censura de 5.4.

    Reglas (design.md 5.4 / 5.5):
    - BUY: tocado si alguna vela posterior tiene `low <= reference_price - stop_points`.
    - SELL: tocado si alguna vela posterior tiene `high >= reference_price + stop_points`.
    - Se recorre solo HASTA el cruce inverso inclusive (si existe).
    - TRUE si se toca antes de terminar la ventana observable.
    - FALSE si hubo cruce inverso antes del limite Y no se toco en toda la ventana.
    - UNKNOWN si se alcanzo el limite sin toque y sin cruce inverso. NUNCA unknown->false.
    """
    # Nivel de precio del stop segun la direccion (design.md 5.5).
    if direction == SignalType.BUY:
        stop_price = reference_price - stop_points  # stop por debajo del ancla
    else:  # SELL
        stop_price = reference_price + stop_points  # stop por encima del ancla

    # La ventana observable termina en el cruce inverso (inclusive) si existe; si no,
    # se recorren todas las velas provistas (que llegan hasta el limite del split).
    if inverse_crossover_index_or_None is not None:
        last_index = inverse_crossover_index_or_None  # incluye la vela del cruce
    else:
        last_index = len(post_signal_bars) - 1  # todas las velas disponibles

    # Busqueda del PRIMER toque del stop dentro de la ventana observable.
    for i in range(0, last_index + 1):
        bar = post_signal_bars[i]
        if direction == SignalType.BUY:
            # BUY: el stop se toca si el minimo de la vela perfora el nivel del stop.
            if bar.low <= stop_price:
                return TriggerState.TRUE
        else:  # SELL
            # SELL: el stop se toca si el maximo de la vela perfora el nivel del stop.
            if bar.high >= stop_price:
                return TriggerState.TRUE

    # No hubo toque en la ventana observable. Decidir entre FALSE y UNKNOWN (5.4):
    if inverse_crossover_index_or_None is not None:
        # Hubo cruce inverso antes del limite y no se toco -> evidencia suficiente: FALSE.
        return TriggerState.FALSE
    if calibration_boundary_reached_flag:
        # Se alcanzo el limite sin toque y sin cruce inverso -> censurado: UNKNOWN.
        return TriggerState.UNKNOWN
    # Sin cruce inverso y sin bandera de limite: la observacion no esta completa; por la
    # regla dura NUNCA se declara `false` sin evidencia -> se trata como censurada.
    return TriggerState.UNKNOWN


@dataclass(frozen=True, slots=True)
class StopPathObservation:
    """Observacion del STOP PATH de una senal para un `atr_multiplier` especifico.

    `signal_path_censored` y `stop_path_censored` son INDEPENDIENTES (design.md 5.7):
    una observacion con `stop_would_trigger=true` y `stop_path_censored=false` sigue
    siendo valida aunque `signal_path_censored=true` (caso de censura parcial, 5.8).
    """

    trigger: TriggerState
    stop_path_censored: bool
    signal_path_censored: bool

    def is_valid_stop_observation(self) -> bool:
        """True si la observacion del STOP PATH es utilizable (estado conocido).

        Una observacion con `stop_path_censored=false` y trigger en {true, false} es
        valida INDEPENDIENTEMENTE del estado de `signal_path_censored` (design.md 5.8).
        """
        return (not self.stop_path_censored) and self.trigger in (
            TriggerState.TRUE,
            TriggerState.FALSE,
        )


def rescued_signal(trigger_m_i: TriggerState, trigger_m_j: TriggerState) -> RescueState:
    """Rescate marginal para multiplicadores CONSECUTIVOS m_i < m_j (design.md 9-bis.8-ALT).

    Args:
        trigger_m_i: `stop_would_trigger` del stop mas ESTRECHO (m_i).
        trigger_m_j: `stop_would_trigger` del stop inmediatamente mas ANCHO (m_j).

    Returns:
        RescueState.TRUE  <=> trigger_m_i == true AND trigger_m_j == false.
        RescueState.FALSE  para true/true y false/false (y false/true).
        RescueState.UNKNOWN si CUALQUIERA de los dos es unknown (excluida del denominador
            observable). NUNCA se convierte unknown -> false.

    NOTA: esta funcion NO valida la consecutividad de los multiplicadores (recibe solo
    los estados). Para validar el par de multiplicadores use `assert_consecutive_step`.
    """
    # Cualquier unknown deja el rescate indeterminado (censura) -> excluido del observable.
    if trigger_m_i == TriggerState.UNKNOWN or trigger_m_j == TriggerState.UNKNOWN:
        return RescueState.UNKNOWN
    # Rescate: el stop estrecho se habria tocado y el inmediatamente mas ancho, no.
    if trigger_m_i == TriggerState.TRUE and trigger_m_j == TriggerState.FALSE:
        return RescueState.TRUE
    # true/true, false/false, false/true -> no hay rescate.
    return RescueState.FALSE


def assert_consecutive_step(m_i: float, m_j: float) -> None:
    """Valida que (m_i, m_j) sea uno de los seis pasos CONSECUTIVOS congelados.

    Lanza ValueError si el par no es consecutivo (design.md 9-bis.8-ALT: "No se permiten
    saltos no consecutivos").
    """
    # Se compara con redondeo defensivo para evitar problemas de representacion float.
    pair = (round(m_i, 2), round(m_j, 2))
    valid = [(round(a, 2), round(b, 2)) for a, b in CONSECUTIVE_ATR_STEPS]
    if pair not in valid:
        raise ValueError(
            f"paso ATR no consecutivo: ({m_i}, {m_j}); pasos validos: {CONSECUTIVE_ATR_STEPS}"
        )


def rescued_signal_for_step(
    m_i: float,
    m_j: float,
    trigger_m_i: TriggerState,
    trigger_m_j: TriggerState,
) -> RescueState:
    """Version que EXIGE consecutividad del par de multiplicadores antes de decidir.

    Rechaza (ValueError) pasos no consecutivos y luego delega en `rescued_signal`.
    """
    assert_consecutive_step(m_i, m_j)  # valida contra los seis pasos congelados
    return rescued_signal(trigger_m_i, trigger_m_j)


class ProductiveUnknownReason(str, Enum):
    """Motivos explicitos por los que `productive_rescue` queda `unknown` (9-bis.8-ALT)."""

    CALIBRATION_BOUNDARY = "calibration_boundary"  # limite del split antes de evaluar
    NO_POST_TOUCH_BAR = "no_post_touch_bar"  # no existe vela evaluable tras el toque


@dataclass(frozen=True, slots=True)
class ProductiveResult:
    """Resultado del rescate productivo: estado ternario + motivo si es unknown."""

    state: ProductiveState
    reason: ProductiveUnknownReason | None = None


def productive_rescue(
    direction: SignalType,
    reference_price: float,
    first_stop_touch_bar_index: int,
    bars_strictly_after_stop_touch: list[Candle],
) -> ProductiveResult:
    """Evalua el rescate productivo tras el primer toque del stop (design.md 9-bis.8-ALT).

    CONTRATO DE LA VENTANA (sin ambiguedad):
    - La vela del toque `t_stop` NO pertenece a esta lista.
    - La lista empieza en la vela de 1 minuto INMEDIATAMENTE POSTERIOR a `t_stop`
      (es decir, `t_stop + 1`), aunque esa vela del toque alcanzara `reference_price`
      (con OHLCV de 1 minuto no se conoce el orden intra-vela, por eso se excluye).
    - La lista termina en la vela del cruce inverso (inclusive).

    Args:
        direction: BUY o SELL.
        reference_price: ancla de la senal.
        first_stop_touch_bar_index: indice de la vela del primer toque `t_stop` (solo
            informativo: las velas provistas YA son estrictamente posteriores al toque).
        bars_strictly_after_stop_touch: velas EVALUABLES estrictamente posteriores al
            toque (desde `t_stop + 1`), hasta el cruce inverso inclusive. La vela del toque
            queda EXCLUIDA por contrato. Lista vacia -> no hay vela evaluable.

    Returns:
        ProductiveResult:
        - TRUE si alguna vela posterior recupera al menos reference_price
          (BUY: high >= reference_price; SELL: low <= reference_price).
        - FALSE si hay velas evaluables pero ninguna recupera reference_price.
        - UNKNOWN (con motivo) si no hay vela evaluable posterior al toque. NUNCA se
          fija automaticamente FALSE en ese caso.
    """
    # `first_stop_touch_bar_index` se recibe por trazabilidad; la exclusion de la vela
    # del toque ya esta reflejada en que la lista provista comienza DESPUES del toque.
    _ = first_stop_touch_bar_index

    if not bars_strictly_after_stop_touch:
        # No existe ninguna vela evaluable tras el toque -> unknown con motivo explicito.
        return ProductiveResult(
            state=ProductiveState.UNKNOWN,
            reason=ProductiveUnknownReason.NO_POST_TOUCH_BAR,
        )

    for bar in bars_strictly_after_stop_touch:
        if direction == SignalType.BUY:
            # BUY productivo: el precio vuelve al menos al ancla por arriba.
            if bar.high >= reference_price:
                return ProductiveResult(state=ProductiveState.TRUE)
        else:  # SELL
            # SELL productivo: el precio vuelve al menos al ancla por abajo.
            if bar.low <= reference_price:
                return ProductiveResult(state=ProductiveState.TRUE)

    # Habia velas evaluables pero ninguna recupero el reference_price -> FALSE.
    return ProductiveResult(state=ProductiveState.FALSE)


# --- Contadores de poblacion y censura (design.md 9-bis.4 y 9-bis.8-ALT) ---


@dataclass(frozen=True, slots=True)
class StepPopulationCounters:
    """Contadores por paso consecutivo m_i->m_j (design.md 9-bis.8-ALT).

    `unknown` queda FUERA de `N_step_observable` pero SIEMPRE se reporta como censura.
    """

    n_step_total: int
    n_step_observable: int
    n_step_censored: int
    step_censoring_rate: float


def count_step_population(rescue_states: list[RescueState]) -> StepPopulationCounters:
    """Cuenta la poblacion observable/censurada de un paso a partir de sus RescueState.

    - N_step_observable = estados en {true, false} (ambos multiplicadores conocidos).
    - N_step_censored   = estados unknown (algun multiplicador censurado).
    - step_censoring_rate = N_step_censored / N_step_total (0.0 si no hay observaciones).
    """
    n_total = len(rescue_states)
    # Observable = rescate con estado conocido (true o false). unknown excluido.
    n_observable = sum(1 for s in rescue_states if s in (RescueState.TRUE, RescueState.FALSE))
    n_censored = sum(1 for s in rescue_states if s == RescueState.UNKNOWN)
    # Tasa de censura; se evita ZeroDivisionError con poblacion vacia.
    rate = (n_censored / n_total) if n_total > 0 else 0.0
    return StepPopulationCounters(
        n_step_total=n_total,
        n_step_observable=n_observable,
        n_step_censored=n_censored,
        step_censoring_rate=rate,
    )


@dataclass(frozen=True, slots=True)
class RescuedPopulationCounters:
    """Contadores del rescate productivo dentro de rescued_signal=true (9-bis.8-ALT).

    `unknown` NUNCA cuenta como `false`; siempre se reportan los tres numeros.
    """

    n_rescued_total: int
    n_rescued_observable: int
    n_rescued_unknown: int


def count_rescued_population(
    productive_states: list[ProductiveState],
) -> RescuedPopulationCounters:
    """Cuenta la poblacion observable/indeterminada del rescate productivo.

    Se recibe la lista de `productive_rescue_status` de TODAS las senales rescatadas
    (rescued_signal=true). unknown queda fuera del observable pero se reporta.
    """
    n_total = len(productive_states)
    n_observable = sum(
        1 for s in productive_states if s in (ProductiveState.TRUE, ProductiveState.FALSE)
    )
    n_unknown = sum(1 for s in productive_states if s == ProductiveState.UNKNOWN)
    return RescuedPopulationCounters(
        n_rescued_total=n_total,
        n_rescued_observable=n_observable,
        n_rescued_unknown=n_unknown,
    )


# --- SIGNAL PATH + excursiones MFE/MAE (design.md 4.2-4.6) ---
#
# El SIGNAL PATH (trayectoria de la senal) es la ventana explicita y auditable sobre la
# que se miden las excursiones MFE/MAE. Se construye una sola vez y las funciones de
# excursion consumen SUS ventanas high_window/low_window, sin volver a recortar por su
# cuenta (asi no puede "elegir en silencio" una ventana distinta de la construida).


@dataclass(frozen=True, slots=True)
class SignalPath:
    """Ventana de medicion de una senal SMA (design.md 4.2-4.4), construida explicitamente.

    Reglas duras (congeladas):
    - La vela de la senal `t` queda EXCLUIDA (su cruce solo se confirma en su cierre).
    - La ventana empieza en `t+1`.
    - La ventana termina en la vela del cruce inverso INCLUSIVE; si no hay cruce inverso
      antes del limite del split de Calibracion, termina en el limite con
      `signal_path_censored=True` (NUNCA se completa con datos de Validation).

    Atributos:
        high_window: lista de `high` de las velas t+1..fin (cruce inverso incluido).
        low_window:  lista de `low`  de las velas t+1..fin (cruce inverso incluido).
        signal_path_censored: True si la ventana termino en el limite del split sin
            observar el cruce inverso (censura del SIGNAL PATH). Es INDEPENDIENTE de
            `stop_path_censored` (design.md 5.7): no se deriva uno del otro.
    """

    high_window: tuple[float, ...]
    low_window: tuple[float, ...]
    signal_path_censored: bool


def build_signal_path(
    signal_candle_t: Candle,
    post_signal_bars: list[Candle],
    inverse_crossover_index_or_None: int | None,
    calibration_boundary_reached_flag: bool,
) -> SignalPath:
    """Construye el SIGNAL PATH de una senal de forma explicita y auditable (design.md 4.2-4.4).

    Args:
        signal_candle_t: la vela que genera la senal (`t`). Se recibe SOLO para dejar
            explicito que queda EXCLUIDA; sus high/low NO entran en las ventanas.
        post_signal_bars: velas desde `t+1` en adelante (indice 0 = vela t+1). La vela `t`
            NUNCA esta aqui.
        inverse_crossover_index_or_None: indice DENTRO de `post_signal_bars` de la vela del
            cruce inverso (esa vela SI se incluye). None si no hay cruce inverso dentro de
            las velas provistas.
        calibration_boundary_reached_flag: True si `post_signal_bars` termina porque se
            alcanzo el limite del split de Calibracion sin cruce inverso. En ese caso la
            ventana se cierra en el limite y el SIGNAL PATH queda censurado.

    Returns:
        SignalPath con high_window/low_window (t+1..fin, cruce inverso inclusive) y la
        bandera `signal_path_censored`.

    Nota: NUNCA se usa Validation para completar la ventana. Si no hubo cruce inverso
    antes del limite, la ventana simplemente termina en el limite y se marca censurada.
    """
    # `signal_candle_t` se recibe por trazabilidad; su exclusion ya esta garantizada porque
    # `post_signal_bars` arranca en t+1 y la vela `t` no forma parte de esa lista.
    _ = signal_candle_t

    if inverse_crossover_index_or_None is not None:
        # Ventana observable cerrada por el cruce inverso: t+1..cruce (cruce INCLUIDO).
        last_index = inverse_crossover_index_or_None
        # Hubo cruce inverso observado -> el SIGNAL PATH NO esta censurado.
        censored = False
    else:
        # No hubo cruce inverso dentro de las velas provistas: la ventana llega hasta la
        # ultima vela disponible (el limite del split). El SIGNAL PATH queda censurado.
        last_index = len(post_signal_bars) - 1
        # Solo tiene sentido marcar censura si efectivamente se alcanzo el limite del split.
        censored = calibration_boundary_reached_flag

    # Recorte explicito de la ventana t+1..fin (inclusive). Si no hay velas, ventanas vacias.
    window = post_signal_bars[: last_index + 1] if post_signal_bars else []
    high_window = tuple(bar.high for bar in window)  # highs de t+1..fin
    low_window = tuple(bar.low for bar in window)  # lows  de t+1..fin

    return SignalPath(
        high_window=high_window,
        low_window=low_window,
        signal_path_censored=censored,
    )


def mfe_points(direction: SignalType, reference_price: float, signal_path: SignalPath) -> float:
    """MFE (Maximum Favorable Excursion) en puntos (design.md 4.5, con max(0, ...)).

    Consume EXACTAMENTE las ventanas del `signal_path` provisto; no re-recorta.

    - BUY:  MFE_points = max(0, max(high_window) - reference_price)
    - SELL: MFE_points = max(0, reference_price - min(low_window))

    Un delta crudo negativo significa que NO hubo excursion favorable -> 0.0 (no es anomalia).
    Ventana vacia (sin velas t+1..fin) -> 0.0: no hubo movimiento observable.
    """
    if direction == SignalType.BUY:
        if not signal_path.high_window:
            return 0.0  # sin velas posteriores: no hay excursion favorable observable
        # Favorable de un BUY = cuanto SUBIO el precio por encima del ancla.
        raw = max(signal_path.high_window) - reference_price
    else:  # SELL
        if not signal_path.low_window:
            return 0.0
        # Favorable de un SELL = cuanto BAJO el precio por debajo del ancla.
        raw = reference_price - min(signal_path.low_window)
    # max(0, ...) es parte de la DEFINICION: ausencia de movimiento favorable => 0.
    return max(0.0, raw)


def mae_points(direction: SignalType, reference_price: float, signal_path: SignalPath) -> float:
    """MAE (Maximum Adverse Excursion) en puntos (design.md 4.5, con max(0, ...)).

    OJO: MAE = Maximum Adverse Excursion, NO Mean Absolute Error. Consume EXACTAMENTE las
    ventanas del `signal_path` provisto; no re-recorta.

    - BUY:  MAE_points = max(0, reference_price - min(low_window))
    - SELL: MAE_points = max(0, max(high_window) - reference_price)

    Un delta crudo negativo significa que NO hubo excursion adversa -> 0.0 (no es anomalia).
    Ventana vacia -> 0.0.
    """
    if direction == SignalType.BUY:
        if not signal_path.low_window:
            return 0.0
        # Adverso de un BUY = cuanto BAJO el precio por debajo del ancla.
        raw = reference_price - min(signal_path.low_window)
    else:  # SELL
        if not signal_path.high_window:
            return 0.0
        # Adverso de un SELL = cuanto SUBIO el precio por encima del ancla.
        raw = max(signal_path.high_window) - reference_price
    # max(0, ...) es parte de la DEFINICION: ausencia de movimiento adverso => 0.
    return max(0.0, raw)


def normalize_by_atr(excursion_points: float, atr_at_signal: float | None) -> float | None:
    """Normaliza una excursion en puntos por el ATR conocido en la senal (design.md 4.6).

    excursion_ATR = excursion_points / ATR_at_signal, SOLO cuando ATR_at_signal no es None
    y es estrictamente > 0. En cualquier otro caso la version normalizada es NO EVALUABLE
    y se devuelve None (convencion de "no disponible" del runner), sin ZeroDivisionError.

    NO se inventa ATR ni se recalcula: se usa unicamente el `atr_at_signal` provisto (el
    ATR conocido en el timestamp de la senal, sin informacion futura).
    """
    if atr_at_signal is None or atr_at_signal <= 0:
        # ATR ausente o no positivo -> la normalizacion no es evaluable.
        return None
    return excursion_points / atr_at_signal
