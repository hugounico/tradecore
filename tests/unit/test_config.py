"""Unit tests for src/config.py — Settings module."""

import os

import pytest
from pydantic import ValidationError

from src.config import Settings


class TestSettingsDefaults:
    """Verify default values are correct."""

    def test_all_defaults_with_api_key(self, monkeypatch):
        """When only DATABENTO_API_KEY is set, all other fields use defaults."""
        monkeypatch.setenv("DATABENTO_API_KEY", "test-key-123")
        # Clear any .env file influence
        settings = Settings(_env_file=None)

        assert settings.databento_api_key == "test-key-123"
        assert settings.databento_dataset == "GLBX.MDP3"
        assert settings.databento_symbol == "NQ.c.0"
        assert settings.databento_stype_in == "continuous"
        assert settings.sma_fast_period == 9
        assert settings.sma_slow_period == 21
        assert settings.historical_candles == 50
        assert settings.ws_throttle_seconds == 1.0
        assert settings.reconnect_max_attempts == 3
        assert settings.log_level == "INFO"
        assert settings.mode == "simulation"

    def test_mode_default_is_simulation(self, monkeypatch):
        """Mode defaults to 'simulation' (Fase A)."""
        monkeypatch.setenv("DATABENTO_API_KEY", "key")
        settings = Settings(_env_file=None)
        assert settings.mode == "simulation"


class TestSettingsOverrides:
    """Verify settings can be overridden via environment variables."""

    def test_override_mode(self, monkeypatch):
        monkeypatch.setenv("DATABENTO_API_KEY", "key")
        monkeypatch.setenv("MODE", "live")
        settings = Settings(_env_file=None)
        assert settings.mode == "live"

    def test_override_sma_periods(self, monkeypatch):
        monkeypatch.setenv("DATABENTO_API_KEY", "key")
        monkeypatch.setenv("SMA_FAST_PERIOD", "5")
        monkeypatch.setenv("SMA_SLOW_PERIOD", "30")
        settings = Settings(_env_file=None)
        assert settings.sma_fast_period == 5
        assert settings.sma_slow_period == 30

    def test_override_log_level(self, monkeypatch):
        monkeypatch.setenv("DATABENTO_API_KEY", "key")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        settings = Settings(_env_file=None)
        assert settings.log_level == "DEBUG"

    def test_override_ws_throttle(self, monkeypatch):
        monkeypatch.setenv("DATABENTO_API_KEY", "key")
        monkeypatch.setenv("WS_THROTTLE_SECONDS", "0.5")
        settings = Settings(_env_file=None)
        assert settings.ws_throttle_seconds == 0.5


class TestSettingsValidation:
    """Verify validation behavior for required fields."""

    def test_missing_api_key_raises_validation_error(self, monkeypatch):
        """Missing DATABENTO_API_KEY must raise a clear ValidationError."""
        # Ensure the env var is NOT set
        monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        # The error should mention the field name
        assert "databento_api_key" in str(exc_info.value).lower()
