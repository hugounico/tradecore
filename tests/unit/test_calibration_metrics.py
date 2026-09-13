"""Tests deterministas de LAYER B (metricas) — design.md 9-bis.8-ALT, 9-bis.10, 9-bis.13.

Incluye el calculo MANUAL de cliff_delta (arrays pequenos, delta hecho a mano) y las
fronteras exactas de los gates de evidencia.
"""

from src.calibration.metrics import (
    censoring_rate,
    cliff_delta,
    evidence_sufficient,
    h2_gate_pass,
    h2_joint_censoring_rate,
    h2_joint_ci_width,
    h2_joint_effect,
    productive_rescue_population_rate,
    quartiles,
    rescue_quality,
    stop_survival_rate,
    stop_trigger_rate,
)


class TestRescueQuality:
    """rescue_quality con guarda de division por cero (design.md 9-bis.8-ALT)."""

    def test_basic(self) -> None:
        # A mano: 12 / 40 = 0.3.
        assert rescue_quality(12, 40) == 0.3

    def test_zero_denominator_returns_none(self) -> None:
        assert rescue_quality(0, 0) is None  # sin ZeroDivisionError

    def test_population_rate(self) -> None:
        # A mano: 12 / 60 = 0.2.
        assert productive_rescue_population_rate(12, 60) == 0.2

    def test_population_rate_zero_denominator(self) -> None:
        assert productive_rescue_population_rate(5, 0) is None


class TestEvidenceSufficient:
    """Fronteras exactas de los gates (design.md 9-bis.13): 50, 30, 0.30."""

    def test_all_pass_at_exact_boundaries(self) -> None:
        # 50 PASS, 30 PASS, 0.30 PASS -> True.
        assert evidence_sufficient(50, 30, 0.30) is True

    def test_step_observable_49_fails(self) -> None:
        # 49 < 50 -> False.
        assert evidence_sufficient(49, 30, 0.30) is False

    def test_rescued_observable_29_fails(self) -> None:
        # 29 < 30 -> False.
        assert evidence_sufficient(50, 29, 0.30) is False

    def test_censoring_above_030_fails(self) -> None:
        # 0.31 > 0.30 -> False.
        assert evidence_sufficient(50, 30, 0.31) is False

    def test_censoring_exactly_030_passes(self) -> None:
        assert evidence_sufficient(50, 30, 0.30) is True


class TestCliffDelta:
    """Delta de Cliff con ejemplo calculado a MANO (design.md 9-bis.10)."""

    def test_manual_computation(self) -> None:
        # A mano. A = [3, 5, 7], B = [2, 4].  n_a*n_b = 6 comparaciones.
        # Pares (a>b): 3>2, 5>2, 5>4, 7>2, 7>4  -> 5 mayores; 3>4? no.
        #   detalle: a=3 -> {3>2:si, 3>4:no}=1 ; a=5 -> {5>2:si,5>4:si}=2 ;
        #            a=7 -> {7>2:si,7>4:si}=2 ; total greater = 1+2+2 = 5.
        # Pares (a<b): a=3 -> 3<4:si =1 ; a=5 -> 0 ; a=7 -> 0 ; total less = 1.
        # delta = (5 - 1) / 6 = 4/6 = 0.6666...
        result = cliff_delta([3, 5, 7], [2, 4])
        assert abs(result - (4 / 6)) < 1e-12

    def test_all_greater_delta_one(self) -> None:
        # A todos mayores que B -> delta = +1.
        assert cliff_delta([10, 11], [1, 2]) == 1.0

    def test_all_less_delta_minus_one(self) -> None:
        # A todos menores que B -> delta = -1.
        assert cliff_delta([1, 2], [10, 11]) == -1.0

    def test_ties_neutral(self) -> None:
        # Todos iguales -> greater=less=0 -> delta = 0.
        assert cliff_delta([5, 5], [5, 5]) == 0.0

    def test_empty_group_none(self) -> None:
        assert cliff_delta([], [1, 2]) is None
        assert cliff_delta([1, 2], []) is None

    def test_in_range(self) -> None:
        result = cliff_delta([3, 5, 7], [2, 4])
        assert -1.0 <= result <= 1.0


class TestH2Gate:
    """Gate H3-A `±0.147` con IC que no cruza 0 (design.md 9-bis.10 / 9-bis.20)."""

    def test_pass(self) -> None:
        # MFE: delta=+0.20 (>=+0.147), IC (0.05, 0.30) no cruza 0.
        # MAE: delta=-0.20 (<=-0.147), IC (-0.30, -0.05) no cruza 0. -> PASS.
        assert h2_gate_pass(0.20, (0.05, 0.30), -0.20, (-0.30, -0.05)) is True

    def test_fail_mfe_effect_too_small(self) -> None:
        # MFE delta=0.10 < 0.147 -> FAIL aunque IC no cruce 0.
        assert h2_gate_pass(0.10, (0.05, 0.15), -0.20, (-0.30, -0.05)) is False

    def test_fail_mae_effect_too_small(self) -> None:
        # MAE delta=-0.10 > -0.147 -> FAIL.
        assert h2_gate_pass(0.20, (0.05, 0.30), -0.10, (-0.20, -0.02)) is False

    def test_fail_mfe_ci_crosses_zero(self) -> None:
        # Efecto suficiente pero IC MFE cruza 0 (-0.05, 0.40) -> FAIL.
        assert h2_gate_pass(0.20, (-0.05, 0.40), -0.20, (-0.30, -0.05)) is False

    def test_fail_mae_ci_crosses_zero(self) -> None:
        # IC MAE cruza 0 (-0.30, 0.05) -> FAIL.
        assert h2_gate_pass(0.20, (0.05, 0.30), -0.20, (-0.30, 0.05)) is False


class TestH2Joint:
    """Efecto conjunto de H2 para el ranking (design.md 9-bis.16)."""

    def test_joint_effect_min(self) -> None:
        # A mano: min(0.30, -(-0.20)) = min(0.30, 0.20) = 0.20.
        assert h2_joint_effect(0.30, -0.20) == 0.20

    def test_joint_ci_width_max(self) -> None:
        # A mano: width(0.05,0.30)=0.25; width(-0.30,-0.05)=0.25; max=0.25.
        assert h2_joint_ci_width((0.05, 0.30), (-0.30, -0.05)) == 0.25
        # A mano: width(0.0,0.40)=0.40; width(-0.10,-0.05)=0.05; max=0.40.
        assert abs(h2_joint_ci_width((0.0, 0.40), (-0.10, -0.05)) - 0.40) < 1e-12

    def test_joint_censoring_max(self) -> None:
        assert h2_joint_censoring_rate(0.10, 0.25) == 0.25


class TestSecondaryDiagnostics:
    """Diagnosticos secundarios (design.md 9-bis.9)."""

    def test_quartiles(self) -> None:
        # A mano: [1,2,3,4,5] -> mediana=3; mitad inf [1,2]->Q1=1.5; mitad sup [4,5]->Q3=4.5.
        q1, med, q3 = quartiles([5, 1, 3, 2, 4])
        assert med == 3
        assert q1 == 1.5
        assert q3 == 4.5

    def test_quartiles_empty(self) -> None:
        assert quartiles([]) is None

    def test_stop_trigger_rate(self) -> None:
        # A mano: 10 / 40 = 0.25.
        assert stop_trigger_rate(10, 40) == 0.25

    def test_stop_trigger_rate_zero(self) -> None:
        assert stop_trigger_rate(0, 0) is None

    def test_stop_survival_rate(self) -> None:
        # A mano: 30 / 40 = 0.75.
        assert stop_survival_rate(30, 40) == 0.75

    def test_censoring_rate(self) -> None:
        # A mano: 2 / 8 = 0.25.
        assert censoring_rate(2, 8) == 0.25

    def test_censoring_rate_zero_total(self) -> None:
        assert censoring_rate(0, 0) == 0.0  # sin ZeroDivisionError
