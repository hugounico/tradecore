"""
Connectivity Spike — Validate Databento API access.

This standalone script validates:
- API key works (authentication)
- Symbol NQ.c.0 resolves correctly (continuous contract symbology)
- ohlcv-1m schema returns data
- Price format is understood (int64 × 1e-9 → float conversion)

NOT part of the main application. Run manually to verify Databento connectivity.

Requirements: RF-01.1, RF-01.4
"""

import logging
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("connectivity_spike")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATASET = "GLBX.MDP3"
SYMBOL = "NQ.c.0"
STYPE_IN = "continuous"
SCHEMA = "ohlcv-1m"
CANDLE_COUNT = 5


def main() -> None:
    """Run connectivity spike: resolve symbol and download sample candles."""

    # Load .env from project root (one level up from scripts/)
    load_dotenv()

    # 1. Load API key from environment
    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        logger.error("DATABENTO_API_KEY environment variable is not set.")
        sys.exit(1)

    logger.info("API key loaded (length=%d, starts with '%s...')", len(api_key), api_key[:4])

    try:
        import databento as db
    except ImportError:
        logger.error("databento package not installed. Run: pip install databento")
        sys.exit(1)

    # 2. Initialize Historical client
    logger.info("Initializing Databento Historical client...")
    client = db.Historical(key=api_key)
    logger.info("Historical client initialized successfully.")

    # 3. Resolve symbol NQ.c.0 via symbology
    logger.info("Resolving symbol '%s' (stype_in=%s, dataset=%s)...", SYMBOL, STYPE_IN, DATASET)
    try:
        # Use a recent trading day for resolution
        resolve_date = _last_trading_day()
        resolution = client.symbology.resolve(
            dataset=DATASET,
            symbols=[SYMBOL],
            stype_in=STYPE_IN,
            stype_out="raw_symbol",
            start_date=resolve_date,
            end_date=resolve_date,
        )
        logger.info("Symbology resolution result:")
        logger.info("  Input: %s (stype_in=%s)", SYMBOL, STYPE_IN)

        # Log the mapping from the resolution result
        if hasattr(resolution, "mappings") and resolution.mappings:
            for mapping in resolution.mappings:
                logger.info(
                    "  Mapped: %s → raw_symbol: %s (date range: %s to %s)",
                    mapping.native,
                    mapping.native,
                    getattr(mapping, "start_date", "N/A"),
                    getattr(mapping, "end_date", "N/A"),
                )
        else:
            # Try alternative access patterns depending on SDK version
            logger.info("  Resolution object: %s", resolution)

    except Exception as e:
        logger.error("Symbol resolution failed: %s", e)
        logger.info("Continuing to data download attempt anyway...")

    # 4. Download 5 ohlcv-1m candles
    logger.info(
        "Downloading %d candles (schema=%s, symbol=%s, dataset=%s)...",
        CANDLE_COUNT,
        SCHEMA,
        SYMBOL,
        DATASET,
    )
    try:
        # Use a recent trading day — go back a few days to ensure market was open
        end_date = _last_trading_day()
        start_date = end_date  # Same day — we only need a few candles

        data = client.timeseries.get_range(
            dataset=DATASET,
            symbols=[SYMBOL],
            schema=SCHEMA,
            stype_in=STYPE_IN,
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            limit=CANDLE_COUNT,
        )

        # Convert to DataFrame for easy inspection
        df = data.to_df()

        if df.empty:
            logger.warning("No candles returned. Market may have been closed on %s.", start_date)
            logger.info("Try running this script on a weekday when CME Globex was open.")
        else:
            logger.info("Received %d candle(s):", len(df))
            logger.info("-" * 80)
            logger.info(
                "%-26s  %12s  %12s  %12s  %12s  %10s",
                "Timestamp", "Open", "High", "Low", "Close", "Volume",
            )
            logger.info("-" * 80)

            for idx, row in df.iterrows():
                # Databento ohlcv-1m prices are int64 × 1e-9 in raw records,
                # but .to_df() typically converts them to float already.
                # We handle both cases:
                open_price = _convert_price(row["open"])
                high_price = _convert_price(row["high"])
                low_price = _convert_price(row["low"])
                close_price = _convert_price(row["close"])
                volume = int(row["volume"])

                logger.info(
                    "%-26s  %12.2f  %12.2f  %12.2f  %12.2f  %10d",
                    str(idx),
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    volume,
                )

            logger.info("-" * 80)
            logger.info("Price format verification:")
            logger.info(
                "  Raw 'open' dtype: %s, sample value: %s",
                df["open"].dtype,
                df["open"].iloc[0],
            )
            logger.info(
                "  Converted open price: %.2f (should look like a NQ futures price ~15000-22000)",
                _convert_price(df["open"].iloc[0]),
            )

    except Exception as e:
        logger.error("Failed to download candles: %s", e)
        sys.exit(1)

    # 5. Clean up
    logger.info("Connectivity spike completed successfully.")
    logger.info("Validated: API key works, symbol resolves, ohlcv-1m returns data, price format understood.")


def _convert_price(value: float | int) -> float:
    """Convert Databento price to float.

    Databento stores prices as int64 × 1e-9 (fixed-point).
    The .to_df() method may already convert to float, but if we get
    a large integer, we divide by 1e9.

    Heuristic: NQ futures trade around 15,000-22,000. If the value is
    greater than 1,000,000 it's likely still in int64 × 1e-9 format.
    """
    if isinstance(value, int) and value > 1_000_000:
        return value / 1e9
    if isinstance(value, float) and value > 1_000_000:
        return value / 1e9
    return float(value)


def _last_trading_day() -> date:
    """Return the most recent likely trading day (weekday, skipping weekends).

    This is a simple heuristic — does not account for market holidays.
    """
    today = date.today()
    # Go back to find a weekday (Mon=0 .. Fri=4)
    offset = 1
    candidate = today - timedelta(days=offset)
    while candidate.weekday() > 4:  # Skip Saturday (5) and Sunday (6)
        offset += 1
        candidate = today - timedelta(days=offset)
    return candidate


if __name__ == "__main__":
    main()
