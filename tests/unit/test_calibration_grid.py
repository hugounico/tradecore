"""Tests deterministas de la grilla congelada (design.md seccion 1-2, 9-bis).

Estos tests FALLAN si la grilla cambia: son la salvaguarda anti-data-snooping.
Los valores esperados estan calculados a mano contra el design.md, no llamando a la
misma constante bajo prueba.
"""

from src.calibration import grid


class TestGridStructure:
    """Estructura exacta de la grilla (design.md seccion 1-2)."""

    def test_atr_multipliers_exact_list(self) -> None:
        # A mano (design.md seccion 1): 1.5 a 3.0 paso 0.25 -> 7 valores exactos.
        assert grid.ATR_MULTIPLIERS == [1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00]
        assert len(grid.ATR_MULTIPLIERS) == 7

    def test_volume_threshold_factors_exact_list(self) -> None:
        # A mano (design.md seccion 1): 0.5 a 0.8 paso 0.1 -> 4 valores exactos.
        assert grid.VOLUME_THRESHOLD_FACTORS == [0.50, 0.60, 0.70, 0.80]
        assert len(grid.VOLUME_THRESHOLD_FACTORS) == 4

    def test_configuration_counts(self) -> None:
        # A mano (design.md seccion 2): A=1, B=7, C=28, total=1+7+28=36.
        assert grid.COUNT_A == 1
        assert grid.COUNT_B == 7
        assert grid.COUNT_C == 28
        assert grid.COUNT_TOTAL == 36
        # Coherencia interna verificable.
        assert grid.COUNT_A + grid.COUNT_B + grid.COUNT_C == grid.COUNT_TOTAL
        assert grid.COUNT_C == len(grid.ATR_MULTIPLIERS) * len(grid.VOLUME_THRESHOLD_FACTORS)

    def test_consecutive_steps_only(self) -> None:
        # A mano (design.md 9-bis.8-ALT): exactamente 6 pasos consecutivos.
        assert grid.CONSECUTIVE_ATR_STEPS == [
            (1.50, 1.75),
            (1.75, 2.00),
            (2.00, 2.25),
            (2.25, 2.50),
            (2.50, 2.75),
            (2.75, 3.00),
        ]
        assert len(grid.CONSECUTIVE_ATR_STEPS) == 6
        # Cada paso usa multiplicadores adyacentes de la grilla (consecutivos).
        for i, (a, b) in enumerate(grid.CONSECUTIVE_ATR_STEPS):
            assert a == grid.ATR_MULTIPLIERS[i]
            assert b == grid.ATR_MULTIPLIERS[i + 1]


class TestFrozenThresholds:
    """Umbrales congelados con nombre (design.md 9-bis.5, 9-bis.10, 9-bis.13, CI, 9-bis.15)."""

    def test_thresholds_exact_values(self) -> None:
        # Valores calculados a mano contra el design.md (subsecciones citadas).
        assert grid.N_STEP_OBSERVABLE_MIN == 50
        assert grid.N_RESCUED_OBSERVABLE_MIN == 30
        assert grid.STEP_CENSORING_RATE_MAX == 0.30
        assert grid.N_BOOTSTRAP_BLOCKS_MIN == 20
        assert grid.BOOTSTRAP_RESAMPLES == 2000
        assert grid.CONFIDENCE_LEVEL == 0.95
        assert grid.CLIFF_DELTA_GATE == 0.147
        assert grid.TOP_N_C == 3

    def test_calibration_window_end_exclusive(self) -> None:
        # design.md, tres splits: inicio inclusivo, fin EXCLUSIVO.
        assert grid.CALIBRATION_START == "2023-09-13T00:00:00Z"
        assert grid.CALIBRATION_END == "2025-07-01T00:00:00Z"


class TestWarmupMinDynamic:
    """warmup_min derivado por formula, NO hardcodeado (design.md warm-up minimo)."""

    def test_warmup_min_current_config_is_22(self) -> None:
        # A mano: max(9, 21+1, 14+1, 20) = max(9, 22, 15, 20) = 22.
        result = grid.compute_warmup_min(
            fast_period=9,
            slow_period=21,
            crossover_state_requirement=1,
            atr_period=14,
            volume_period=20,
        )
        assert result == 22

    def test_warmup_min_changes_with_config(self) -> None:
        # Prueba que NO esta hardcodeado: cambiar atr_period=30 -> atr_period+1=31 domina.
        # A mano: max(9, 21+1, 30+1, 20) = max(9, 22, 31, 20) = 31.
        result = grid.compute_warmup_min(
            fast_period=9,
            slow_period=21,
            crossover_state_requirement=1,
            atr_period=30,
            volume_period=20,
        )
        assert result == 31

    def test_warmup_min_slow_period_dominates(self) -> None:
        # A mano: slow=50, crossover=1 -> 51 domina. max(9, 51, 15, 20) = 51.
        result = grid.compute_warmup_min(
            fast_period=9,
            slow_period=50,
            crossover_state_requirement=1,
            atr_period=14,
            volume_period=20,
        )
        assert result == 51

    def test_warmup_min_volume_period_dominates(self) -> None:
        # A mano: volume=40 domina. max(9, 22, 15, 40) = 40.
        result = grid.compute_warmup_min(
            fast_period=9,
            slow_period=21,
            crossover_state_requirement=1,
            atr_period=14,
            volume_period=40,
        )
        assert result == 40
