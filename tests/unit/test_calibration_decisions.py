"""Tests deterministas de LAYER C (decisiones) — design.md 9-bis.13-SEQ, 9-bis.15, 9-bis.16, 9-bis.20."""

from src.calibration.decisions import (
    CCandidate,
    StepResult,
    rank_c_candidates,
    recompute_h1_per_accepted_population,
    sequential_atr_selection,
    top_n_c,
)


def _step(m_i: float, m_j: float, ev: bool, q_lower: float, p_lower: float) -> StepResult:
    return StepResult(m_i=m_i, m_j=m_j, evidence_sufficient=ev, q_lower=q_lower, p_lower=p_lower)


# Pasos que PASAN las tres condiciones: ev=True, q_lower>0.50, p_lower>0.
def _pass_step(m_i: float, m_j: float) -> StepResult:
    return _step(m_i, m_j, True, 0.60, 0.10)


# Paso con fallo SUSTANTIVO (evidencia suficiente pero IC no supera umbrales).
def _fail_substantive(m_i: float, m_j: float) -> StepResult:
    return _step(m_i, m_j, True, 0.40, 0.10)  # q_lower 0.40 <= 0.50 -> no justifica


# Paso con evidencia insuficiente.
def _insufficient(m_i: float, m_j: float) -> StepResult:
    return _step(m_i, m_j, False, 0.60, 0.10)


class TestSequentialAtrSelection:
    """Regla secuencial de avance de ATR (design.md 9-bis.13-SEQ / STOP)."""

    def test_initial_pass_advances(self) -> None:
        # Solo el primer paso pasa, el segundo falla sustantivo -> selected = 1.75.
        steps = [_pass_step(1.50, 1.75), _fail_substantive(1.75, 2.00)]
        result = sequential_atr_selection(steps)
        assert result.selected_atr == 1.75
        assert result.evidence_insufficient is False

    def test_substantive_fail_at_first_stops_at_150(self) -> None:
        # Primer paso con evidencia suficiente pero IC no supera -> se detiene en 1.50.
        steps = [_fail_substantive(1.50, 1.75)]
        result = sequential_atr_selection(steps)
        assert result.selected_atr == 1.50
        assert result.evidence_insufficient is False

    def test_initial_insufficiency_no_winner(self) -> None:
        # Caso A: primer paso evidence_sufficient=false -> no auto-selecciona 1.50.
        steps = [_insufficient(1.50, 1.75)]
        result = sequential_atr_selection(steps)
        assert result.selected_atr is None
        assert result.evidence_insufficient is True

    def test_pass_pass_fail_selects_second_atr(self) -> None:
        # PASS(1.50->1.75), PASS(1.75->2.00), FAIL(2.00->2.25) -> selected = 2.00.
        steps = [
            _pass_step(1.50, 1.75),
            _pass_step(1.75, 2.00),
            _fail_substantive(2.00, 2.25),
        ]
        result = sequential_atr_selection(steps)
        assert result.selected_atr == 2.00

    def test_later_insufficiency_keeps_last_justified(self) -> None:
        # Caso B: PASS(1.50->1.75), luego evidencia insuficiente -> conserva 1.75.
        steps = [_pass_step(1.50, 1.75), _insufficient(1.75, 2.00)]
        result = sequential_atr_selection(steps)
        assert result.selected_atr == 1.75
        assert result.evidence_insufficient is False

    def test_no_step_skipped(self) -> None:
        # Un paso que falla DETIENE; un paso posterior "bueno" NO se salta para justificar.
        steps = [
            _pass_step(1.50, 1.75),
            _fail_substantive(1.75, 2.00),  # detiene aqui
            _pass_step(2.00, 2.25),  # este NO debe considerarse
        ]
        result = sequential_atr_selection(steps)
        assert result.selected_atr == 1.75

    def test_no_steps_case_c(self) -> None:
        # Caso C: ninguna paso evaluable -> evidencia insuficiente.
        result = sequential_atr_selection([])
        assert result.selected_atr is None
        assert result.evidence_insufficient is True


class TestH3B:
    """H3-B: recomputo de H1 por poblacion Aceptada independiente (design.md 9-bis.20)."""

    def test_per_population_recomputation_differs(self) -> None:
        # Fixture que produce selected_atr DISTINTO para dos poblaciones Aceptadas,
        # probando que H1 se recomputa por poblacion (no reusa un H1 global).
        per_population = {
            # Accepted(0.50): solo el primer paso pasa -> selected = 1.75.
            0.50: [_pass_step(1.50, 1.75), _fail_substantive(1.75, 2.00)],
            # Accepted(0.70): dos pasos pasan -> selected = 2.00.
            0.70: [_pass_step(1.50, 1.75), _pass_step(1.75, 2.00),
                   _fail_substantive(2.00, 2.25)],
        }
        results = recompute_h1_per_accepted_population(per_population)
        assert results[0.50].selected_atr == 1.75
        assert results[0.70].selected_atr == 2.00
        # La prueba de independencia: los dos selected_atr son distintos.
        assert results[0.50].selected_atr != results[0.70].selected_atr


class TestRankingC:
    """Ranking lexicografico de candidatas C (design.md 9-bis.16)."""

    def test_full_lexicographic_order(self) -> None:
        # (1) mayor h2_joint_effect decide primero.
        a = CCandidate(0.50, h2_joint_effect=0.30, h2_joint_ci_width=0.20,
                       h2_joint_censoring_rate=0.10, n_accepted_observable=100)
        b = CCandidate(0.60, h2_joint_effect=0.20, h2_joint_ci_width=0.10,
                       h2_joint_censoring_rate=0.05, n_accepted_observable=200)
        ranked = rank_c_candidates([b, a])
        assert ranked[0] is a  # mayor efecto gana pese a peor todo lo demas
        assert ranked[1] is b

    def test_tiebreak_ci_width(self) -> None:
        # Empate en efecto -> menor h2_joint_ci_width gana.
        a = CCandidate(0.50, 0.20, h2_joint_ci_width=0.30, h2_joint_censoring_rate=0.10,
                       n_accepted_observable=100)
        b = CCandidate(0.60, 0.20, h2_joint_ci_width=0.10, h2_joint_censoring_rate=0.10,
                       n_accepted_observable=100)
        ranked = rank_c_candidates([a, b])
        assert ranked[0] is b  # menor ancho de IC

    def test_selected_atr_150_ranking_defined_without_h1(self) -> None:
        # Con atr_multiplier=1.50 en ambas, el ranking sigue definido SIN metricas de H1:
        # se decide por h2_joint_effect. Cambiar atr_multiplier no altera el orden.
        a = CCandidate(0.50, 0.30, 0.20, 0.10, 100, atr_multiplier=1.50)
        b = CCandidate(0.60, 0.20, 0.10, 0.05, 200, atr_multiplier=1.50)
        ranked = rank_c_candidates([b, a])
        assert ranked[0] is a

    def test_atr_multiplier_does_not_change_ranking(self) -> None:
        # Mismos criterios H2, distinto atr_multiplier -> MISMO orden (atr fuera del ranking).
        a1 = CCandidate(0.50, 0.30, 0.20, 0.10, 100, atr_multiplier=1.50)
        b1 = CCandidate(0.60, 0.20, 0.10, 0.05, 200, atr_multiplier=1.50)
        a2 = CCandidate(0.50, 0.30, 0.20, 0.10, 100, atr_multiplier=3.00)
        b2 = CCandidate(0.60, 0.20, 0.10, 0.05, 200, atr_multiplier=3.00)
        ranked1 = rank_c_candidates([b1, a1])
        ranked2 = rank_c_candidates([b2, a2])
        # El orden por volume_threshold_factor es identico independientemente del atr.
        assert [c.volume_threshold_factor for c in ranked1] == \
               [c.volume_threshold_factor for c in ranked2]

    def test_final_technical_key_volume_threshold(self) -> None:
        # Igualdad matematica EXACTA en todo -> clave tecnica final: menor volume_threshold.
        a = CCandidate(0.70, 0.20, 0.10, 0.10, 100)
        b = CCandidate(0.50, 0.20, 0.10, 0.10, 100)
        ranked = rank_c_candidates([a, b])
        assert ranked[0] is b  # menor volume_threshold_factor (0.50) primero


class TestTopN:
    """Top-N (design.md 9-bis.15): Top-N_C=3."""

    def test_four_valid_yields_exactly_three(self) -> None:
        cands = [
            CCandidate(0.50, 0.40, 0.10, 0.10, 100),
            CCandidate(0.60, 0.30, 0.10, 0.10, 100),
            CCandidate(0.70, 0.20, 0.10, 0.10, 100),
            CCandidate(0.80, 0.10, 0.10, 0.10, 100),
        ]
        result = top_n_c(cands)
        assert len(result) == 3
        # Los tres de mayor h2_joint_effect (0.40, 0.30, 0.20).
        assert [c.h2_joint_effect for c in result] == [0.40, 0.30, 0.20]

    def test_two_valid_yields_two_no_filling(self) -> None:
        cands = [
            CCandidate(0.50, 0.40, 0.10, 0.10, 100),
            CCandidate(0.60, 0.30, 0.10, 0.10, 100),
        ]
        result = top_n_c(cands)
        assert len(result) == 2  # no se rellena

    def test_zero_valid_yields_none(self) -> None:
        assert top_n_c([]) == []

    def test_technical_key_never_exceeds_three(self) -> None:
        # 4 candidatas con IGUALDAD exacta -> la clave tecnica ordena y corta a 3, nunca mas.
        cands = [
            CCandidate(0.50, 0.20, 0.10, 0.10, 100),
            CCandidate(0.60, 0.20, 0.10, 0.10, 100),
            CCandidate(0.70, 0.20, 0.10, 0.10, 100),
            CCandidate(0.80, 0.20, 0.10, 0.10, 100),
        ]
        result = top_n_c(cands)
        assert len(result) == 3
        # Determinismo: las de MENOR volume_threshold_factor (clave tecnica) gradúan.
        assert [c.volume_threshold_factor for c in result] == [0.50, 0.60, 0.70]
