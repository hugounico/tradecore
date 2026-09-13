"""LAYER C — Reglas de decision de la Calibracion (design.md 9-bis.13-SEQ, 9-bis.14 a 9-bis.20).

Funciones PURAS y deterministas (sin veto humano). Implementan:
- Seleccion secuencial de ATR (H1): regla 9-bis.13-SEQ / 9-bis.13-STOP con los casos A/B/C.
- Ranking lexicografico de candidatas C (9-bis.16), SIN metricas de H1 y con
  `atr_multiplier` FUERA del ranking/desempate.
- Top-N (9-bis.15): Top-N_C = 3; menos de 3 -> gradua solo esas; 0 -> ninguna.
- H3-B (9-bis.20): recomputo de H1 por poblacion Aceptada independiente (no reusa H1 global).

Glosario:
- selected_atr: el multiplicador ATR resultante de la regla secuencial.
- LI IC95 (limite inferior del IC 95%): cota inferior del intervalo de confianza.
- lexicografico: ordenar por criterio 1; solo ante empate exacto se pasa al criterio 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.calibration.grid import CONSECUTIVE_ATR_STEPS, TOP_N_C


# --- H1: seleccion secuencial de ATR (design.md 9-bis.13-SEQ / 9-bis.13-STOP) ---


@dataclass(frozen=True, slots=True)
class StepResult:
    """Resultado de un paso consecutivo m_i->m_j para la regla secuencial.

    Campos (todos provienen de las capas A/B; aqui solo se DECIDE con ellos):
    - m_i, m_j: multiplicadores del paso (consecutivos).
    - evidence_sufficient: gate 9-bis.13 ya evaluado para este paso.
    - q_lower: limite INFERIOR del IC95 de rescue_quality (Q_j).
    - p_lower: limite INFERIOR del IC95 de productive_rescue_population_rate (P_j).
    """

    m_i: float
    m_j: float
    evidence_sufficient: bool
    q_lower: float
    p_lower: float

    def justifies_widening(self) -> bool:
        """True si el paso justifica ampliar el ATR: las TRES condiciones de 9-bis.13-SEQ.

        1) evidence_sufficient == true;
        2) LI IC95 de Q_j estrictamente > 0.50;
        3) LI IC95 de P_j estrictamente > 0.
        """
        return (
            self.evidence_sufficient
            and self.q_lower > 0.50  # mas de la mitad de rescates son productivos
            and self.p_lower > 0.0  # efecto poblacional productivo no nulo
        )


@dataclass(frozen=True, slots=True)
class SequentialAtrResult:
    """Resultado de la seleccion secuencial de ATR (9-bis.13-SEQ).

    - selected_atr: el ultimo multiplicador inferior justificado, o None si evidencia
      insuficiente en el primer paso o ningun paso evaluable (casos A y C).
    - evidence_insufficient: True en los casos A y C (no gradua por H1).
    - trace: traza por paso (m_i, m_j, justifico?) para trazabilidad/reporte.
    """

    selected_atr: float | None
    evidence_insufficient: bool
    trace: list[tuple[float, float, bool]] = field(default_factory=list)


def sequential_atr_selection(steps_results: list[StepResult]) -> SequentialAtrResult:
    """Regla secuencial CONGELADA de avance de ATR (design.md 9-bis.13-SEQ / STOP).

    Parte de selected_atr = 1.50 y evalua los pasos EN ORDEN. El primer paso que no
    justifica ampliar DETIENE el avance; selected_atr = ultimo multiplicador inferior
    justificado.

    Casos de evidencia insuficiente:
    - Caso A: el primer paso (1.50->1.75) tiene evidence_sufficient=false ->
      evidence_insufficient=true; NO auto-selecciona 1.50; NO gradua por H1.
    - Caso B: un paso POSTERIOR pasa a evidence_sufficient=false tras pasos aprobados ->
      se detiene y conserva el ultimo atr_multiplier justificado.
    - Caso C: ningun paso evaluable -> evidence_insufficient=true.

    NOTA: se asume que `steps_results` viene en el orden congelado de CONSECUTIVE_ATR_STEPS.
    """
    # Caso C: sin pasos evaluables -> evidencia insuficiente, sin graduacion por H1.
    if not steps_results:
        return SequentialAtrResult(
            selected_atr=None, evidence_insufficient=True, trace=[]
        )

    # Caso A: el PRIMER paso no tiene evidencia suficiente -> no se auto-selecciona 1.50.
    first = steps_results[0]
    if not first.evidence_sufficient:
        return SequentialAtrResult(
            selected_atr=None,
            evidence_insufficient=True,
            trace=[(first.m_i, first.m_j, False)],
        )

    # Se parte de 1.50 como punto de partida (design.md 9-bis.13-SEQ).
    selected_atr: float | None = 1.50
    trace: list[tuple[float, float, bool]] = []

    for step in steps_results:
        justified = step.justifies_widening()
        trace.append((step.m_i, step.m_j, justified))
        if justified:
            # El paso justifica ampliar: se avanza al multiplicador superior del paso.
            selected_atr = step.m_j
        else:
            # Primer paso que falla DETIENE el avance (no se salta a un paso superior).
            # Caso B (evidencia insuficiente posterior) o fallo sustantivo: se conserva
            # el ultimo multiplicador inferior justificado (ya en selected_atr).
            break

    return SequentialAtrResult(
        selected_atr=selected_atr, evidence_insufficient=False, trace=trace
    )


# --- H3-B: recomputo de H1 por poblacion Aceptada (design.md 9-bis.20) ---


def recompute_h1_per_accepted_population(
    per_population_step_results: dict[float, list[StepResult]],
) -> dict[float, SequentialAtrResult]:
    """H3-B: recomputa H1 completa POR umbral sobre su poblacion Accepted (9-bis.20).

    Args:
        per_population_step_results: mapa `volume_threshold_factor -> lista de StepResult`
            calculada INDEPENDIENTEMENTE sobre la poblacion Accepted(threshold). NO se
            reutiliza la H1 general de la poblacion SMA completa.

    Returns:
        mapa `volume_threshold_factor -> SequentialAtrResult` (un selected_atr por umbral,
        recomputado por poblacion).
    """
    # Cada umbral recibe su PROPIA regla secuencial; nunca se comparte un resultado global.
    return {
        threshold: sequential_atr_selection(steps)
        for threshold, steps in per_population_step_results.items()
    }


# --- Ranking de candidatas C y Top-N (design.md 9-bis.15 y 9-bis.16) ---


@dataclass(frozen=True, slots=True)
class CCandidate:
    """Candidata C (una por volume_threshold_factor que paso H3-A via H3-B).

    Campos de ranking (design.md 9-bis.16). `atr_multiplier` se guarda SOLO por
    trazabilidad; NO participa en el ranking ni en el desempate.
    """

    volume_threshold_factor: float
    h2_joint_effect: float
    h2_joint_ci_width: float
    h2_joint_censoring_rate: float
    n_accepted_observable: int
    atr_multiplier: float | None = None  # trazabilidad; fuera del ranking


def _c_ranking_key(candidate: CCandidate) -> tuple:
    """Clave lexicografica de ordenamiento de candidatas C (design.md 9-bis.16).

    Orden (se compara criterio por criterio; solo ante empate exacto se pasa al siguiente;
    NO se suman ni ponderan; `atr_multiplier` NO participa):
      1. mayor h2_joint_effect          -> se niega para orden ascendente.
      2. menor h2_joint_ci_width.
      3. menor h2_joint_censoring_rate.
      4. mayor N_Accepted_observable    -> se niega para orden ascendente.
      5. menor volume_threshold_factor  -> SOLO clave tecnica final por determinismo.
    """
    return (
        -candidate.h2_joint_effect,  # (1) mayor efecto conjunto primero
        candidate.h2_joint_ci_width,  # (2) menor ancho de IC
        candidate.h2_joint_censoring_rate,  # (3) menor censura conjunta
        -candidate.n_accepted_observable,  # (4) mayor N aceptadas observables
        candidate.volume_threshold_factor,  # (5) clave tecnica final (determinismo)
    )


def rank_c_candidates(candidates: list[CCandidate]) -> list[CCandidate]:
    """Ordena las candidatas C por la clave lexicografica congelada (9-bis.16).

    Devuelve una lista NUEVA ordenada de mejor a peor. No usa metricas de H1 y trata
    `atr_multiplier` como irrelevante para el orden.
    """
    return sorted(candidates, key=_c_ranking_key)


def top_n_c(candidates: list[CCandidate]) -> list[CCandidate]:
    """Selecciona el Top-N_C (=3) de candidatas C (design.md 9-bis.15).

    - Menos de 3 validas -> gradua solo esas (NO se rellena con sub-umbral).
    - 0 -> ninguna.
    - La clave tecnica final del ranking garantiza determinismo, de modo que el corte
      nunca gradua mas de 3 aun ante empates.
    """
    ranked = rank_c_candidates(candidates)
    # Corte a lo sumo TOP_N_C; si hay menos, se devuelven solo las disponibles.
    return ranked[:TOP_N_C]
