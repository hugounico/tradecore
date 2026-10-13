"""Tests para src/api/market_hours.is_regular_market_open.

Verifican el calendario regular de CME Globex para NQ, usando instantes UTC
tz-aware convertidos internamente a hora de Chicago. No cubren feriados (la propia
funcion documenta esa limitacion), asi que los tests tampoco los asumen.

Terminologia:
- tz-aware: un datetime que conoce su zona horaria (tiene tzinfo).
- UTC = Tiempo Universal Coordinado; CT = hora de Chicago (America/Chicago).
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.api.market_hours import is_regular_market_open

_CT = ZoneInfo("America/Chicago")


def _ct(year, month, day, hour, minute=0) -> datetime:
    """Build a tz-aware datetime in Chicago time (as CME sees the clock)."""
    return datetime(year, month, day, hour, minute, tzinfo=_CT)


class TestNaiveRejected:
    """Un datetime naive (sin zona) debe ser rechazado para evitar ambiguedad."""

    def test_naive_datetime_raises_value_error(self):
        with pytest.raises(ValueError):
            is_regular_market_open(datetime(2026, 1, 5, 12, 0))  # sin tzinfo


class TestWeekend:
    """Sabado cerrado; domingo abre a las 17:00 CT."""

    def test_saturday_closed_all_day(self):
        # 2026-01-03 es sabado.
        assert is_regular_market_open(_ct(2026, 1, 3, 3)) is False
        assert is_regular_market_open(_ct(2026, 1, 3, 12)) is False
        assert is_regular_market_open(_ct(2026, 1, 3, 20)) is False

    def test_sunday_closed_before_17_open_after(self):
        # 2026-01-04 es domingo.
        assert is_regular_market_open(_ct(2026, 1, 4, 16, 59)) is False
        assert is_regular_market_open(_ct(2026, 1, 4, 17, 0)) is True
        assert is_regular_market_open(_ct(2026, 1, 4, 22, 0)) is True


class TestWeekdays:
    """Lunes-jueves: abierto salvo la pausa 16:00-17:00 CT."""

    def test_monday_open_outside_maintenance(self):
        # 2026-01-05 es lunes.
        assert is_regular_market_open(_ct(2026, 1, 5, 9, 30)) is True
        assert is_regular_market_open(_ct(2026, 1, 5, 15, 59)) is True

    def test_monday_closed_during_maintenance(self):
        assert is_regular_market_open(_ct(2026, 1, 5, 16, 0)) is False
        assert is_regular_market_open(_ct(2026, 1, 5, 16, 30)) is False

    def test_monday_reopens_at_17(self):
        assert is_regular_market_open(_ct(2026, 1, 5, 17, 0)) is True
        assert is_regular_market_open(_ct(2026, 1, 5, 23, 59)) is True

    def test_thursday_same_rule(self):
        # 2026-01-08 es jueves.
        assert is_regular_market_open(_ct(2026, 1, 8, 10, 0)) is True
        assert is_regular_market_open(_ct(2026, 1, 8, 16, 30)) is False
        assert is_regular_market_open(_ct(2026, 1, 8, 18, 0)) is True


class TestFriday:
    """Viernes: abierto hasta las 16:00 CT; luego cerrado el fin de semana."""

    def test_friday_open_before_16(self):
        # 2026-01-09 es viernes.
        assert is_regular_market_open(_ct(2026, 1, 9, 9, 0)) is True
        assert is_regular_market_open(_ct(2026, 1, 9, 15, 59)) is True

    def test_friday_closed_from_16(self):
        assert is_regular_market_open(_ct(2026, 1, 9, 16, 0)) is False
        assert is_regular_market_open(_ct(2026, 1, 9, 20, 0)) is False


class TestUtcInputConverted:
    """Un instante dado en UTC se convierte correctamente a hora de Chicago."""

    def test_utc_input_mapped_to_chicago(self):
        # 2026-01-05 (lunes) 22:30 UTC == 16:30 CT (CST = UTC-6) -> pausa -> cerrado.
        assert is_regular_market_open(datetime(2026, 1, 5, 22, 30, tzinfo=timezone.utc)) is False
        # 2026-01-05 (lunes) 15:00 UTC == 09:00 CT -> abierto.
        assert is_regular_market_open(datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)) is True
