"""Equivalencia EXACTA entre la version vectorizada de `cliff_delta` y una referencia
NAIVE O(n*m) escrita SOLO en este test.

Objetivo: probar que la optimizacion con NumPy (searchsorted) NO cambia el estadistico.
La definicion es identica: delta = (#{a>b} - #{a<b}) / (n_a * n_b); empates -> 0;
grupo vacio -> None; resultado en [-1, 1].

- Casos manuales (8): comparados contra el valor esperado calculado a mano Y contra naive.
- Property-based (Hypothesis): abs(optimizado - naive) <= 1e-12 para muchas entradas.
- Bootstrap end-to-end: mismo IC95 y mismo PASS/FAIL de H3-A usando naive vs optimizado
  con MISMO seed/bloques/resamples (resamples pequeno solo para el test; NO toca prod).

**Validates: Requirements RF-E1-02** (H2 tamano de efecto; design.md 9-bis.10)
"""

from __future__ import annotations

from typing import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.calibration.block_bootstrap import block_bootstrap_ci
from src.calibration.metrics import cliff_delta, h2_gate_pass


# ---------------------------------------------------------------------------
# Referencia NAIVE (SOLO en el test; NUNCA en produccion) — doble bucle O(n*m).
# Reproduce EXACTAMENTE la definicion original con empates neutrales.
# ---------------------------------------------------------------------------
def cliff_delta_naive(
    group_a_values: Sequence[float], group_b_values: Sequence[float]
) -> float | None:
    """Delta de Cliff O(n*m) por doble bucle explicito (referencia de equivalencia)."""
    n_a = len(group_a_values)
    n_b = len(group_b_values)
    if n_a == 0 or n_b == 0:
        return None
    greater = 0  # #{a > b}
    less = 0  # #{a < b}
    for a in group_a_values:
        for b in group_b_values:
            if a > b:
                greater += 1
            elif a < b:
                less += 1
            # a == b: empate neutral, no suma a ninguno.
    return (greater - less) / (n_a * n_b)


TOL = 1e-12  # tolerancia de igualdad numerica exigida (equivalencia exacta)


class TestCliffDeltaManualCases:
    """Ocho casos manuales: optimizado == esperado (a mano) y == naive, abs <= 1e-12."""

    def test_a_all_x_greater_y(self) -> None:
        # (a) todo X > Y -> todos los pares a>b -> delta = +1.
        a = [10.0, 11.0, 12.0]
        b = [1.0, 2.0, 3.0]
        expected = 1.0
        assert abs(cliff_delta(a, b) - expected) <= TOL
        assert abs(cliff_delta(a, b) - cliff_delta_naive(a, b)) <= TOL

    def test_b_all_x_less_y(self) -> None:
        # (b) todo X < Y -> todos los pares a<b -> delta = -1.
        a = [1.0, 2.0, 3.0]
        b = [10.0, 11.0, 12.0]
        expected = -1.0
        assert abs(cliff_delta(a, b) - expected) <= TOL
        assert abs(cliff_delta(a, b) - cliff_delta_naive(a, b)) <= TOL

    def test_c_all_equal(self) -> None:
        # (c) todos iguales -> todos empates -> delta = 0.
        a = [5.0, 5.0, 5.0]
        b = [5.0, 5.0]
        expected = 0.0
        assert abs(cliff_delta(a, b) - expected) <= TOL
        assert abs(cliff_delta(a, b) - cliff_delta_naive(a, b)) <= TOL

    def test_d_known_wins_losses_ties(self) -> None:
        # (d) combo con ganancias/perdidas/empates calculado a mano.
        # a = [2, 4, 6], b = [4, 5].  n_a*n_b = 6.
        # a=2: vs4 (<), vs5 (<)            -> less += 2
        # a=4: vs4 (=), vs5 (<)            -> less += 1 (empate no suma)
        # a=6: vs4 (>), vs5 (>)            -> greater += 2
        # greater=2, less=3 -> (2-3)/6 = -1/6.
        a = [2.0, 4.0, 6.0]
        b = [4.0, 5.0]
        expected = -1.0 / 6.0
        assert abs(cliff_delta(a, b) - expected) <= TOL
        assert abs(cliff_delta(a, b) - cliff_delta_naive(a, b)) <= TOL

    def test_e_many_duplicates(self) -> None:
        # (e) muchos duplicados (empates masivos) — verifica trato neutral de empates.
        # a = [1,1,1,2,2], b = [1,1,2].  n_a*n_b = 15.
        # a=1 (x3): vs1(=),vs1(=),vs2(<) -> cada uno less+=1 => less += 3
        # a=2 (x2): vs1(>),vs1(>),vs2(=) -> cada uno greater+=2 => greater += 4
        # greater=4, less=3 -> (4-3)/15 = 1/15.
        a = [1.0, 1.0, 1.0, 2.0, 2.0]
        b = [1.0, 1.0, 2.0]
        expected = 1.0 / 15.0
        assert abs(cliff_delta(a, b) - expected) <= TOL
        assert abs(cliff_delta(a, b) - cliff_delta_naive(a, b)) <= TOL

    def test_f_negative_values(self) -> None:
        # (f) valores negativos.
        # a = [-1, -2], b = [-3, 0]. n_a*n_b = 4.
        # a=-1: vs-3(>), vs0(<) -> greater+=1, less+=1
        # a=-2: vs-3(>), vs0(<) -> greater+=1, less+=1
        # greater=2, less=2 -> 0/4 = 0.
        a = [-1.0, -2.0]
        b = [-3.0, 0.0]
        expected = 0.0
        assert abs(cliff_delta(a, b) - expected) <= TOL
        assert abs(cliff_delta(a, b) - cliff_delta_naive(a, b)) <= TOL

    def test_g_floats(self) -> None:
        # (g) floats no enteros.
        # a = [0.1, 0.2, 0.3], b = [0.15, 0.25]. n_a*n_b = 6.
        # a=0.1:  <0.15, <0.25          -> less+=2
        # a=0.2:  >0.15, <0.25          -> greater+=1, less+=1
        # a=0.3:  >0.15, >0.25          -> greater+=2
        # greater=3, less=3 -> 0/6 = 0.
        a = [0.1, 0.2, 0.3]
        b = [0.15, 0.25]
        expected = 0.0
        assert abs(cliff_delta(a, b) - expected) <= TOL
        assert abs(cliff_delta(a, b) - cliff_delta_naive(a, b)) <= TOL

    def test_h_different_sizes(self) -> None:
        # (h) n_a != n_b.
        # a = [5, 1, 9, 3] (4), b = [4, 6] (2). n_a*n_b = 8.
        # a=5: >4(g), <6(l)   -> greater+=1, less+=1
        # a=1: <4(l), <6(l)   -> less+=2
        # a=9: >4(g), >6(g)   -> greater+=2
        # a=3: <4(l), <6(l)   -> less+=2
        # greater=3, less=5 -> (3-5)/8 = -0.25.
        a = [5.0, 1.0, 9.0, 3.0]
        b = [4.0, 6.0]
        expected = -0.25
        assert abs(cliff_delta(a, b) - expected) <= TOL
        assert abs(cliff_delta(a, b) - cliff_delta_naive(a, b)) <= TOL


class TestCliffDeltaEmptyGroups:
    """Grupo vacio -> None (contrato preservado)."""

    def test_empty_a(self) -> None:
        assert cliff_delta([], [1.0, 2.0]) is None

    def test_empty_b(self) -> None:
        assert cliff_delta([1.0, 2.0], []) is None

    def test_both_empty(self) -> None:
        assert cliff_delta([], []) is None


# ---------------------------------------------------------------------------
# Property-based: optimizado == naive para muchas entradas aleatorias.
# ---------------------------------------------------------------------------
# Generador de listas de floats "razonables" (sin NaN/inf) con tamanos variados,
# permitiendo duplicados, negativos y no-enteros.
_floats = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False, width=64
)
_float_list = st.lists(_floats, min_size=1, max_size=300)


class TestCliffDeltaProperty:
    """abs(optimizado - naive) <= 1e-12 para grupos generados por Hypothesis."""

    @settings(max_examples=300, deadline=None)
    @given(a=_float_list, b=_float_list)
    def test_optimized_equals_naive(
        self, a: list[float], b: list[float]
    ) -> None:
        opt = cliff_delta(a, b)
        naive = cliff_delta_naive(a, b)
        # Ambos deben coincidir en None-ness (aqui nunca vacio por min_size=1).
        assert (opt is None) == (naive is None)
        if opt is not None and naive is not None:
            assert abs(opt - naive) <= TOL

    @settings(max_examples=150, deadline=None)
    @given(
        a=st.lists(st.integers(min_value=-5, max_value=5).map(float), min_size=1, max_size=120),
        b=st.lists(st.integers(min_value=-5, max_value=5).map(float), min_size=1, max_size=120),
    )
    def test_optimized_equals_naive_heavy_ties(
        self, a: list[float], b: list[float]
    ) -> None:
        # Rango entero estrecho -> MUCHOS empates: caso critico para searchsorted.
        opt = cliff_delta(a, b)
        naive = cliff_delta_naive(a, b)
        assert opt is not None and naive is not None
        assert abs(opt - naive) <= TOL


# ---------------------------------------------------------------------------
# Bootstrap end-to-end: mismo IC95 y mismo PASS/FAIL de H3-A (naive vs optimizado).
# ---------------------------------------------------------------------------
def _cliff_ci_over_blocks(grouped, statistic_cliff, resamples, seed):
    """Replica el patron del harness: bloques con valores etiquetados A/B; el estadistico
    recomputa cliff_delta(a_vals, b_vals). Se parametriza CUAL cliff_delta se usa."""

    def stat(flat_list):
        a_vals = [v for tag, v in flat_list if tag == "A"]
        b_vals = [v for tag, v in flat_list if tag == "B"]
        if not a_vals or not b_vals:
            return None
        return statistic_cliff(a_vals, b_vals)

    return block_bootstrap_ci(grouped, stat, resamples=resamples, confidence_level=0.95, seed=seed)


class TestBootstrapEndToEndEquivalence:
    """El IC95 por block bootstrap y el gate H3-A son identicos con naive vs optimizado."""

    def _build_blocks(self):
        """Dataset sintetico pequeno: bloques semanales ISO con valores A/B etiquetados.
        Accepted (A) desplazado hacia arriba en MFE y hacia abajo en MAE frente a Rejected (B)."""
        # Cinco bloques ISO ficticios; cada uno con algunas observaciones A y B.
        blocks_mfe = {
            "2024-W01": [("A", 2.1), ("A", 2.4), ("B", 1.0), ("B", 0.8)],
            "2024-W02": [("A", 2.6), ("B", 1.2), ("B", 0.9), ("A", 2.0)],
            "2024-W03": [("A", 3.0), ("A", 2.2), ("B", 1.5)],
            "2024-W04": [("B", 0.7), ("A", 2.8), ("A", 2.3), ("B", 1.1)],
            "2024-W05": [("A", 2.5), ("B", 1.3), ("A", 2.9), ("B", 0.6)],
        }
        # MAE: Accepted MENOR (mejor) que Rejected -> cliff delta negativo esperado.
        blocks_mae = {
            "2024-W01": [("A", 0.5), ("A", 0.6), ("B", 1.8), ("B", 2.0)],
            "2024-W02": [("A", 0.4), ("B", 1.9), ("B", 2.2), ("A", 0.7)],
            "2024-W03": [("A", 0.3), ("A", 0.8), ("B", 2.1)],
            "2024-W04": [("B", 2.3), ("A", 0.55), ("A", 0.65), ("B", 1.7)],
            "2024-W05": [("A", 0.45), ("B", 1.6), ("A", 0.35), ("B", 2.4)],
        }
        return blocks_mfe, blocks_mae

    def test_ci95_and_gate_identical(self) -> None:
        blocks_mfe, blocks_mae = self._build_blocks()
        RESAMPLES_TEST = 200  # pequeno SOLO para el test; produccion sigue en 2000.
        SEED = 0

        # --- IC95 MFE (optimizado vs naive) ---
        ci_mfe_opt = _cliff_ci_over_blocks(blocks_mfe, cliff_delta, RESAMPLES_TEST, SEED)
        ci_mfe_naive = _cliff_ci_over_blocks(blocks_mfe, cliff_delta_naive, RESAMPLES_TEST, SEED)

        # --- IC95 MAE (optimizado vs naive) ---
        ci_mae_opt = _cliff_ci_over_blocks(blocks_mae, cliff_delta, RESAMPLES_TEST, SEED)
        ci_mae_naive = _cliff_ci_over_blocks(blocks_mae, cliff_delta_naive, RESAMPLES_TEST, SEED)

        # Los IC deben ser identicos dentro de precision numerica.
        assert abs(ci_mfe_opt[0] - ci_mfe_naive[0]) <= 1e-12
        assert abs(ci_mfe_opt[1] - ci_mfe_naive[1]) <= 1e-12
        assert abs(ci_mae_opt[0] - ci_mae_naive[0]) <= 1e-12
        assert abs(ci_mae_opt[1] - ci_mae_naive[1]) <= 1e-12

        # --- Point estimates identicos ---
        flat_mfe = [(t, v) for members in blocks_mfe.values() for (t, v) in members]
        a_mfe = [v for t, v in flat_mfe if t == "A"]
        b_mfe = [v for t, v in flat_mfe if t == "B"]
        flat_mae = [(t, v) for members in blocks_mae.values() for (t, v) in members]
        a_mae = [v for t, v in flat_mae if t == "A"]
        b_mae = [v for t, v in flat_mae if t == "B"]

        cd_mfe_opt = cliff_delta(a_mfe, b_mfe)
        cd_mfe_naive = cliff_delta_naive(a_mfe, b_mfe)
        cd_mae_opt = cliff_delta(a_mae, b_mae)
        cd_mae_naive = cliff_delta_naive(a_mae, b_mae)
        assert abs(cd_mfe_opt - cd_mfe_naive) <= 1e-12
        assert abs(cd_mae_opt - cd_mae_naive) <= 1e-12

        # --- Mismo PASS/FAIL de H3-A via h2_gate_pass (misma semantica congelada) ---
        gate_opt = h2_gate_pass(cd_mfe_opt, ci_mfe_opt, cd_mae_opt, ci_mae_opt)
        gate_naive = h2_gate_pass(cd_mfe_naive, ci_mfe_naive, cd_mae_naive, ci_mae_naive)
        assert gate_opt == gate_naive
