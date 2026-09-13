"""Tests property-based y de block bootstrap de la Calibracion (design.md 9-bis.13-CI).

Property-based testing (prueba basada en propiedades): se afirma una PROPIEDAD que debe
cumplirse para MUCHAS entradas generadas por Hypothesis, en vez de ejemplos concretos.

Cubre:
- block bootstrap: mismo block_id para la misma semana ISO; caso de frontera de ano
  (ISO year != ano calendario); preservacion de bloques completos; reproducibilidad con
  semilla fija; gate de 20 bloques; sin semanas sinteticas vacias.
- propiedades generales: cliff_delta en [-1,1]; contadores no negativos y coherentes.

**Validates: Requirements RF-E1-01, RF-E1-02** (Calibracion Etapa 1; design.md 9-bis)
"""

from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from src.calibration.block_bootstrap import (
    block_bootstrap_ci,
    bootstrap_block_id,
    bootstrap_evidence_insufficient,
    n_bootstrap_blocks,
)
from src.calibration.metrics import cliff_delta
from src.calibration.observations import RescueState, count_step_population


class TestBlockIdSameIsoWeek:
    """Mismo block_id para timestamps de la misma semana ISO (design.md 9-bis.13-CI)."""

    def test_same_week_same_id(self) -> None:
        # 2025-01-06 (lunes) y 2025-01-08 (miercoles) son la misma semana ISO 2025-W02.
        # A mano con isocalendar(): 2025-01-06 -> (2025, 2, 1); 2025-01-08 -> (2025, 2, 3).
        id1 = bootstrap_block_id("2025-01-06T10:00:00Z")
        id2 = bootstrap_block_id("2025-01-08T23:59:00Z")
        assert id1 == id2 == "2025-W02"

    def test_iso_year_boundary_case(self) -> None:
        # Caso explicito de frontera: 2024-12-31 pertenece a la semana ISO 2025-W01.
        # Verificado a mano con datetime.isocalendar(): 2024-12-31 -> (2025, 1, 2).
        dt = datetime(2024, 12, 31, tzinfo=timezone.utc)
        iso = dt.isocalendar()
        assert (iso[0], iso[1]) == (2025, 1)  # ISO year 2025, semana 1 (ano calendario 2024)
        assert bootstrap_block_id("2024-12-31T12:00:00Z") == "2025-W01"
        # Coherencia: 2025-01-01 (miercoles) tambien cae en 2025-W01.
        assert bootstrap_block_id("2025-01-01T00:00:00Z") == "2025-W01"

    def test_different_weeks_different_ids(self) -> None:
        # 2025-01-06 (W02) vs 2025-01-13 (W03) -> ids distintos.
        assert bootstrap_block_id("2025-01-06T00:00:00Z") != \
               bootstrap_block_id("2025-01-13T00:00:00Z")


class TestNBootstrapBlocks:
    """Conteo de bloques y gate de 20 (design.md 9-bis.13-CI CI.4)."""

    def test_distinct_non_empty_blocks(self) -> None:
        block_ids = ["2025-W01", "2025-W01", "2025-W02", "2025-W03"]
        assert n_bootstrap_blocks(block_ids) == 3  # tres semanas distintas

    def test_20_blocks_eligible(self) -> None:
        # 20 semanas distintas -> no insuficiente.
        block_ids = [f"2025-W{w:02d}" for w in range(1, 21)]  # W01..W20 = 20 bloques
        assert n_bootstrap_blocks(block_ids) == 20
        assert bootstrap_evidence_insufficient(block_ids) is False

    def test_19_blocks_insufficient(self) -> None:
        # 19 semanas distintas -> insuficiente (< 20).
        block_ids = [f"2025-W{w:02d}" for w in range(1, 20)]  # W01..W19 = 19 bloques
        assert n_bootstrap_blocks(block_ids) == 19
        assert bootstrap_evidence_insufficient(block_ids) is True


class TestBlockBootstrapReproducibility:
    """Reproducibilidad con semilla fija y preservacion de bloques completos."""

    def _grouped(self) -> dict[str, list[float]]:
        # 25 bloques con miembros multiples (para superar el gate de 20 y tener senal).
        grouped: dict[str, list[float]] = {}
        for w in range(1, 26):
            # Cada bloque tiene 3 valores; el promedio del bloque varia por semana.
            grouped[f"2025-W{w:02d}"] = [float(w), float(w) + 1.0, float(w) + 2.0]
        return grouped

    def test_same_seed_same_ci(self) -> None:
        # Dos corridas con la MISMA semilla -> IC identico (reproducibilidad).
        grouped = self._grouped()
        stat = lambda xs: sum(xs) / len(xs) if xs else None  # media
        ci1 = block_bootstrap_ci(grouped, stat, resamples=500, seed=12345)
        ci2 = block_bootstrap_ci(grouped, stat, resamples=500, seed=12345)
        assert ci1 == ci2

    def test_whole_block_preservation(self) -> None:
        # La media global es constante = media de todos los valores, pero al remuestrear
        # bloques COMPLETOS el estadistico por replica varia dentro del rango de medias de
        # bloque. Verificamos que el IC queda dentro del rango [min, max] de valores.
        grouped = self._grouped()
        all_values = [v for members in grouped.values() for v in members]
        stat = lambda xs: sum(xs) / len(xs) if xs else None
        lower, upper = block_bootstrap_ci(grouped, stat, resamples=500, seed=7)
        assert min(all_values) <= lower <= upper <= max(all_values)

    def test_no_empty_weeks_generated(self) -> None:
        # Solo se pasan bloques no vacios; el bootstrap nunca crea semanas vacias.
        # Un bloque vacio en el input se ignora (no rompe ni aporta).
        grouped = self._grouped()
        grouped["2025-W40"] = []  # semana vacia: debe ignorarse
        stat = lambda xs: sum(xs) / len(xs) if xs else None
        # No debe lanzar y el resultado es finito.
        lower, upper = block_bootstrap_ci(grouped, stat, resamples=200, seed=1)
        assert lower <= upper


class TestCliffDeltaProperty:
    """Propiedad: cliff_delta siempre en [-1, 1] (design.md 9-bis.10)."""

    @given(
        a=st.lists(st.floats(min_value=-1000, max_value=1000, allow_nan=False,
                             allow_infinity=False), min_size=1, max_size=30),
        b=st.lists(st.floats(min_value=-1000, max_value=1000, allow_nan=False,
                             allow_infinity=False), min_size=1, max_size=30),
    )
    @settings(max_examples=200)
    def test_cliff_delta_in_range(self, a: list[float], b: list[float]) -> None:
        result = cliff_delta(a, b)
        assert result is not None
        assert -1.0 <= result <= 1.0


class TestStepPopulationProperty:
    """Propiedad: contadores coherentes y no negativos (design.md 9-bis.4)."""

    @given(
        states=st.lists(
            st.sampled_from([RescueState.TRUE, RescueState.FALSE, RescueState.UNKNOWN]),
            min_size=0, max_size=100,
        )
    )
    @settings(max_examples=200)
    def test_counters_consistent(self, states: list[RescueState]) -> None:
        c = count_step_population(states)
        # Observable + censored == total; ninguno negativo; tasa en [0, 1].
        assert c.n_step_observable + c.n_step_censored == c.n_step_total
        assert c.n_step_observable >= 0
        assert c.n_step_censored >= 0
        assert 0.0 <= c.step_censoring_rate <= 1.0
        # unknown NUNCA se cuenta como observable.
        n_unknown = sum(1 for s in states if s == RescueState.UNKNOWN)
        assert c.n_step_censored == n_unknown
