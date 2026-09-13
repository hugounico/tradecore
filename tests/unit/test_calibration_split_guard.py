"""Tests deterministas del guard de splits (design.md tres splits, warm-up, 9-bis.14).

Verifican: inicio inclusivo, fin EXCLUSIVO, rechazo de Validacion/OOS, warm-up solo
cronologicamente anterior, y que una observacion censurada nunca se completa con datos
en/despues de la frontera.
"""

from datetime import datetime, timezone

import pytest

from src.calibration import split_guard


class TestCalibrationBoundaries:
    """`[inicio, fin)`: inicio inclusivo, fin EXCLUSIVO (design.md tres splits)."""

    def test_start_is_included(self) -> None:
        # 2023-09-13T00:00:00Z es el primer instante -> DENTRO de Calibracion.
        assert split_guard.is_within_calibration("2023-09-13T00:00:00Z") is True

    def test_end_is_excluded(self) -> None:
        # 2025-07-01T00:00:00Z pertenece a Validacion -> FUERA de Calibracion.
        assert split_guard.is_within_calibration("2025-07-01T00:00:00Z") is False

    def test_immediately_before_end_is_allowed(self) -> None:
        # Un minuto antes del corte todavia es Calibracion.
        assert split_guard.is_within_calibration("2025-06-30T23:59:00Z") is True

    def test_before_start_is_outside(self) -> None:
        # Anterior al inicio -> fuera de Calibracion.
        assert split_guard.is_within_calibration("2023-09-12T23:59:00Z") is False

    def test_validation_and_later_flag(self) -> None:
        # >= fin -> Validacion o posterior.
        assert split_guard.is_validation_or_later("2025-07-01T00:00:00Z") is True
        assert split_guard.is_validation_or_later("2026-02-02T00:00:00Z") is True
        assert split_guard.is_validation_or_later("2025-06-30T23:59:00Z") is False


class TestValidationInaccessible:
    """Validacion / OOS final son inaccesibles desde el runner (design.md 9-bis.14 / 10)."""

    def test_reject_validation_access(self) -> None:
        with pytest.raises(PermissionError):
            split_guard.reject_if_inaccessible("2025-07-01T00:00:00Z")

    def test_reject_final_oos_access(self) -> None:
        # OOS final (>= 2026-02-02) tambien es inaccesible (esta despues del fin).
        with pytest.raises(PermissionError):
            split_guard.reject_if_inaccessible("2026-05-01T00:00:00Z")

    def test_calibration_timestamp_accessible(self) -> None:
        # Un timestamp de Calibracion NO se rechaza.
        split_guard.reject_if_inaccessible("2024-01-01T00:00:00Z")  # no debe lanzar


class TestWarmupContext:
    """Warm-up: solo cronologicamente ANTERIOR al inicio del split y nunca Validacion."""

    def test_warmup_before_split_start_ok(self) -> None:
        # Vela de warm-up anterior al inicio del split evaluado -> valida.
        split_guard.assert_warmup_context_valid(
            warmup_ts="2025-06-30T23:59:00Z",
            split_start="2025-07-01T00:00:00Z",
        )  # no debe lanzar

    def test_warmup_at_or_after_split_start_rejected(self) -> None:
        # Warm-up igual al inicio del split -> no es "anterior" -> rechazado.
        with pytest.raises(ValueError):
            split_guard.assert_warmup_context_valid(
                warmup_ts="2025-07-01T00:00:00Z",
                split_start="2025-07-01T00:00:00Z",
            )

    def test_warmup_never_uses_validation(self) -> None:
        # Warm-up que cae en Validacion (>= fin de Calibracion) -> rechazado, aunque el
        # split evaluado empezara aun mas tarde.
        with pytest.raises(ValueError):
            split_guard.assert_warmup_context_valid(
                warmup_ts="2025-07-15T00:00:00Z",
                split_start="2026-02-02T00:00:00Z",
            )


class TestCensoredCompletion:
    """Una observacion censurada nunca se completa con datos en/despues de la frontera."""

    def test_completion_before_boundary_allowed(self) -> None:
        assert split_guard.can_complete_censored_observation("2025-06-30T23:59:00Z") is True

    def test_completion_at_boundary_denied(self) -> None:
        # En la frontera exacta ya es Validacion -> no se puede completar.
        assert split_guard.can_complete_censored_observation("2025-07-01T00:00:00Z") is False

    def test_completion_after_boundary_denied(self) -> None:
        assert split_guard.can_complete_censored_observation("2025-08-01T00:00:00Z") is False


class TestTimestampParsing:
    """Robustez del parseo de timestamps (UTC obligatorio)."""

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValueError):
            split_guard.is_within_calibration(datetime(2024, 1, 1))  # sin tzinfo

    def test_aware_datetime_accepted(self) -> None:
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert split_guard.is_within_calibration(dt) is True
