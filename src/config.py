"""Application configuration loaded from environment / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """TradeCore MVP configuration.

    All settings are loaded from environment variables or a .env file.
    The only required field is databento_api_key (DATABENTO_API_KEY env var).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Databento connection
    databento_api_key: str  # Required — no default, raises ValidationError if missing
    databento_dataset: str = "GLBX.MDP3"
    databento_symbol: str = "NQ.c.0"
    databento_stype_in: str = "continuous"

    # Signal engine
    sma_fast_period: int = 9
    sma_slow_period: int = 21

    # Historical data
    historical_candles: int = 50

    # WebSocket throttle
    ws_throttle_seconds: float = 1.0

    # Reconnection
    reconnect_max_attempts: int = 3

    # Logging
    log_level: str = "INFO"

    # Operation mode: "simulation" (Fase A) or "live" (Fase B)
    mode: str = "simulation"
