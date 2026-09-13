"""LAYER B — Metricas diagnosticas de la Calibracion (design.md 9-bis.8-ALT, 9-bis.10, 9-bis.16).

Funciones PURAS. NO hay P&L, expectancy, profit factor ni take-profit (prohibido en toda
la Calibracion: design.md 9-bis.9 y seccion 7).

Metricas H1 (rescate): rescue_quality, productive_rescue_population_rate, evidence_sufficient.
Metrica H2 (tamano de efecto): cliff_delta y el gate H3-A (`±0.147`).
Efecto conjunto H2: h2_joint_effect, h2_joint_ci_width, h2_joint_censoring_rate.
Diagnosticos secundarios: cuartiles de MAE_ATR/MFE_ATR, stop_trigger_rate,
stop_survival_rate, censoring_rate.

Glosario:
- Cliff's delta (delta de Cliff): tamano de efecto basado en rangos, en [-1, 1].
- IC / CI: intervalo de confianza; "no cruza 0" significa que ambos limites tienen el
  mismo signo (o un limite es exactamente 0 no cuenta como cruzar).
- MFE (Maximum Favorable Excursion): mayor movimiento a favor.
- MAE (Maximum Adverse Excursion): mayor movimiento en contra. (NO es Mean Absolute Error.)
"""

from __future__ import annotations

from statistics import median
from typing import Sequence

import numpy as np  # numpy ya es dependencia del proyecto (vectorizacion de cliff_delta)

from src.calibration.grid import (
    CLIFF_DELTA_GATE,
    N_RESCUED_OBSERVABLE_MIN,
    N_STEP_OBSERVABLE_MIN,
    STEP_CENSORING_RATE_MAX,
)


# --- H1: metricas de rescate (design.md 9-bis.8-ALT) ---


def rescue_quality(
    n_productive_rescue: int, n_rescued_observable: int
) -> float | None:
    """rescue_quality = N_productive_rescue / N_rescued_observable (9-bis.8-ALT).

    De las senales realmente rescatadas por el paso, que proporcion recupero el
    reference_price. Guarda contra denominador 0: devuelve None (evidencia insuficiente),
    NO lanza ZeroDivisionError.
    """
    if n_rescued_observable == 0:
        # Sin rescates observables no hay base para la calidad -> evidencia insuficiente.
        return None
    return n_productive_rescue / n_rescued_observable


def productive_rescue_population_rate(
    n_productive_rescue: int, n_step_observable: int
) -> float | None:
    """productive_rescue_population_rate = N_productive_rescue / N_step_observable (9-bis.8-ALT).

    Que proporcion de toda la poblacion observable obtuvo un rescate productivo en el paso.
    Guarda contra denominador 0: devuelve None, NO lanza ZeroDivisionError.
    """
    if n_step_observable == 0:
        return None
    return n_productive_rescue / n_step_observable


def evidence_sufficient(
    n_step_observable: int,
    n_rescued_observable: int,
    step_censoring_rate: float,
) -> bool:
    """Gate determinista de evidencia de H1 (design.md 9-bis.13).

    evidence_sufficient = (N_step_observable >= 50)
                      AND (N_rescued_observable >= 30)
                      AND (step_censoring_rate <= 0.30)

    Sin veto humano: solo los tres umbrales congelados deciden.
    """
    return (
        n_step_observable >= N_STEP_OBSERVABLE_MIN
        and n_rescued_observable >= N_RESCUED_OBSERVABLE_MIN
        and step_censoring_rate <= STEP_CENSORING_RATE_MAX
    )


# --- H2: tamano de efecto (design.md 9-bis.10) ---


def cliff_delta(
    group_a_values: Sequence[float], group_b_values: Sequence[float]
) -> float | None:
    """Delta de Cliff = (#{a>b} - #{a<b}) / (n_a * n_b), en [-1, 1] (design.md 9-bis.10).

    `group_a_values` = grupo Aceptado; `group_b_values` = grupo Rechazado. Guarda grupos
    vacios: devuelve None (no se puede calcular el efecto) en vez de dividir por 0.

    Implementacion vectorizada con NumPy (O((n_a + n_b) * log n_b) en vez de O(n_a * n_b)
    del doble bucle). El resultado es EXACTAMENTE el mismo estadistico: solo cambia COMO
    se cuentan los pares, no que se cuenta ni el signo, ni el trato de empates.
    """
    n_a = len(group_a_values)
    n_b = len(group_b_values)
    # La guarda de grupo vacio va ANTES de cualquier trabajo con numpy (contrato: None).
    if n_a == 0 or n_b == 0:
        return None

    # Se convierten ambos grupos a arreglos de float (definicion sobre numeros reales).
    a_arr = np.asarray(group_a_values, dtype=float)
    b_sorted = np.sort(np.asarray(group_b_values, dtype=float))  # b ordenado una sola vez

    # Idea: en un arreglo ORDENADO, np.searchsorted localiza en O(log n_b) cuantos
    # elementos quedan a un lado de cada `a`, evaluando TODOS los `a` de una vez.
    #   - side="left" -> indice del primer b >= a  == cantidad de b ESTRICTAMENTE < a.
    #   - side="right"-> indice del primer b >  a  == cantidad de b <= a.
    # Por lo tanto, para cada `a`:
    #   #{b < a} = searchsorted(b_sorted, a, "left")
    #   #{b > a} = n_b - searchsorted(b_sorted, a, "right")
    # y los empates (b == a) quedan EXCLUIDOS de ambos conteos (mismo trato neutral que
    # el doble bucle: a == b no suma ni a `greater` ni a `less`). Sumando sobre todos los
    # `a` se obtienen exactamente los mismos totales de pares que el bucle anidado:
    #   greater = #{(a, b): a > b} = #{(a, b): b < a}
    #   less    = #{(a, b): a < b} = #{(a, b): b > a}
    left = np.searchsorted(b_sorted, a_arr, side="left")  # por cada a: cantidad de b < a
    right = np.searchsorted(b_sorted, a_arr, side="right")  # por cada a: cantidad de b <= a
    greater = int(left.sum())  # #{a > b} (pares donde b < a)
    less = int((n_b - right).sum())  # #{a < b} (pares donde b > a)
    return (greater - less) / (n_a * n_b)


def _ci_crosses_zero(ci: tuple[float, float]) -> bool:
    """True si el IC (lower, upper) CRUZA 0 (lower < 0 < upper estricto).

    Un limite exactamente igual a 0 NO se considera "cruzar" por si solo; se considera
    que cruza cuando hay valores estrictamente a ambos lados del 0.
    """
    lower, upper = ci
    return lower < 0.0 < upper


def h2_gate_pass(
    cliff_delta_mfe: float,
    ci_mfe: tuple[float, float],
    cliff_delta_mae: float,
    ci_mae: tuple[float, float],
) -> bool:
    """Gate H3-A congelado (design.md 9-bis.10 y 9-bis.20).

    Pasa SOLO SI:
      - Cliff_delta_MFE >= +0.147  Y  su IC 95% NO cruza 0, Y
      - Cliff_delta_MAE <= -0.147  Y  su IC 95% NO cruza 0.

    Interpretacion: las Aceptadas muestran al menos un efecto favorable "pequeno" en MFE
    (mayor excursion favorable) y en MAE (menor excursion adversa), ambos respaldados por
    IC que no cruza 0.
    """
    mfe_ok = (cliff_delta_mfe >= CLIFF_DELTA_GATE) and (not _ci_crosses_zero(ci_mfe))
    mae_ok = (cliff_delta_mae <= -CLIFF_DELTA_GATE) and (not _ci_crosses_zero(ci_mae))
    return mfe_ok and mae_ok


# --- Efecto conjunto de H2 para el ranking de candidatas C (design.md 9-bis.16) ---


def h2_joint_effect(cliff_delta_mfe: float, cliff_delta_mae: float) -> float:
    """h2_joint_effect = min(Cliff_delta_MFE, -Cliff_delta_MAE) (design.md 9-bis.16).

    Ambos terminos se orientan a "mas alto = mejor"; el min representa la mejora MAS DEBIL
    de las dos, evitando que una dimension enorme compense la ausencia en la otra.
    """
    return min(cliff_delta_mfe, -cliff_delta_mae)


def _ci_width(ci: tuple[float, float]) -> float:
    """Ancho de un IC (upper - lower)."""
    lower, upper = ci
    return upper - lower


def h2_joint_ci_width(
    ci_mfe: tuple[float, float], ci_mae: tuple[float, float]
) -> float:
    """h2_joint_ci_width = max(width(IC_MFE), width(IC_MAE)) (design.md 9-bis.16). Menor = mejor."""
    return max(_ci_width(ci_mfe), _ci_width(ci_mae))


def h2_joint_censoring_rate(
    censoring_rate_accepted: float, censoring_rate_rejected: float
) -> float:
    """h2_joint_censoring_rate = max(censoring_rate_Accepted, censoring_rate_Rejected) (9-bis.16). Menor = mejor."""
    return max(censoring_rate_accepted, censoring_rate_rejected)


# --- Diagnosticos secundarios (design.md 9-bis.9 y 9-bis.11) ---


def quartiles(values: Sequence[float]) -> tuple[float, float, float] | None:
    """(Q1, mediana, Q3) de una muestra. None si esta vacia. Diagnostico secundario.

    Se usa el metodo de la mediana de mitades (compatible con stdlib, determinista).
    """
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    med = median(ordered)
    if n == 1:
        return (ordered[0], med, ordered[0])
    # Mitades inferior y superior (excluyen la mediana en tamanos impares).
    mid = n // 2
    lower_half = ordered[:mid]
    upper_half = ordered[mid + 1 :] if n % 2 == 1 else ordered[mid:]
    q1 = median(lower_half) if lower_half else ordered[0]
    q3 = median(upper_half) if upper_half else ordered[-1]
    return (q1, med, q3)


def stop_trigger_rate(n_stop_true: int, n_stop_observable: int) -> float | None:
    """stop_trigger_rate = N_stop_true / N_stop_observable (9-bis.9). Guarda 0 -> None.

    NO se optimiza en solitario (baja mecanicamente al ensanchar el stop, seccion 5.10).
    """
    if n_stop_observable == 0:
        return None
    return n_stop_true / n_stop_observable


def stop_survival_rate(n_stop_false: int, n_stop_observable: int) -> float | None:
    """stop_survival_rate = N_stop_false / N_stop_observable (9-bis.9). Guarda 0 -> None.

    Complemento del trigger rate; mismo sesgo mecanico, no se optimiza en solitario.
    """
    if n_stop_observable == 0:
        return None
    return n_stop_false / n_stop_observable


def censoring_rate(n_censored: int, n_total: int) -> float:
    """censoring_rate = N_censored / N_total (9-bis.4). Guarda 0 -> 0.0 (sin excepcion)."""
    if n_total == 0:
        return 0.0
    return n_censored / n_total
