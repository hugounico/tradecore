# Design Document: TradeCore MVP

## Overview

TradeCore MVP validates the end-to-end data chain: **Databento → OHLCV → Chart → Signal → Visualization**. It is a single-process Python application that connects to Databento via the official `databento` Python client, receives pre-aggregated 1-minute OHLCV candles (schema `ohlcv-1m`), computes SMA(9)/SMA(21) crossovers on completed candles, and displays the results on an interactive candlestick chart in the browser.

The system is intentionally minimal: one user, one process, one instrument (NQ E-mini Nasdaq-100 futures continuous contract on GLBX.MDP3), no persistence, no authentication, no order execution.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Use `databento` Python client (Historical + Live) | Official SDK with built-in asyncio support, challenge-response auth, and DBN decoding. Avoids raw TCP protocol complexity. |
| Use continuous contract symbology (`NQ.c.0`, `stype_in='continuous'`) | Parent symbol (`NQ.FUT`) delivers data from multiple expiration contracts mixed together, which corrupts the candle series. Continuous contract resolves to the active front-month contract automatically. |
| Use `ohlcv-1m` schema directly (NOT `trades`) | Databento delivers pre-aggregated 1-minute OHLCV bars. This eliminates the need to build candles locally from raw trades, reduces data volume dramatically, and is sufficient for SMA(9)/SMA(21) crossover signals. No local candle construction needed. |
| FastAPI + single WebSocket to push updates to browser | One backend process, one browser connection. Server controls push rate (throttle). No need for multiple simultaneous client management. |
| TradingView Lightweight Charts (v4) for frontend | ~40 KB, purpose-built for financial candlestick charts, supports real-time updates, markers, and line series (for SMAs). No build step — served from CDN. |
| Vanilla JavaScript for UI state | The MVP UI state is minimal (connection status, loading indicator). No framework adds proportional benefit for this scope. |
| In-memory candle buffer (Python list) | No persistence required for MVP. Simple, fast, and disposable. |
| Standard Python `logging` (no structured JSON library) | For a single-process MVP, standard logging with a formatter is sufficient. |
| Unified visual throttle for ALL updates | All updates to the frontend respect the 1/second limit. Prevents two visual updates less than 1 second apart regardless of event type. |
| Historical first, Live second (Fase A → Fase B) | Validate the entire pipeline with historical data (free credits) before activating the paid Standard plan for live streaming. Reduces cost risk and enables full testing without time pressure. |
| Simulation mode reuses the same pipeline | Fase A "simulates live" by replaying historical candles through the same ThrottledPusher and WebSocket at 1/second — ensures the pipeline works identically in both modes. |

---

## Architecture

```mermaid
graph TB
    subgraph External
        DB_HIST[Databento Historical API<br/>ohlcv-1m schema]
        DB_LIVE[Databento Live API<br/>ohlcv-1m schema]
    end

    subgraph Backend ["FastAPI Process (single)"]
        CONN[DabentoConnector<br/>Historical + Live client wrapper]
        BUFFER[CandleBuffer<br/>In-memory list of completed candles]
        SIGNAL[SignalEngine<br/>SMA(9)/SMA(21) crossover detection]
        THROTTLE[ThrottledPusher<br/>Enforces 1/sec visual update limit]
        SIM[SimulationReplay<br/>Replays historical candles at 1/sec<br/>(Fase A only)]
    end

    subgraph Frontend ["Browser (HTML + Vanilla JS)"]
        CHART[Lightweight Charts<br/>Candlestick + SMA lines]
        MARKERS[Signal Markers<br/>BUY (green) / SELL (red)]
        STATUS[Connection & Loading Status]
    end

    DB_HIST -->|ohlcv-1m bars| CONN
    DB_LIVE -->|ohlcv-1m bars| CONN
    CONN -->|List[Candle]| BUFFER
    CONN -->|Candle stream| SIM
    SIM -->|Candle at 1/sec| BUFFER
    BUFFER -->|new candle| SIGNAL
    SIGNAL -->|BUY/SELL signal| THROTTLE
    BUFFER -->|completed candle| THROTTLE
    THROTTLE -->|JSON via WebSocket<br/>max 1/sec| CHART
    THROTTLE -->|JSON via WebSocket| MARKERS
    THROTTLE -->|connection state| STATUS
```

### Data Flow — Fase A (Historical + Simulation)

1. **Startup**: `DabentoConnector` downloads historical ohlcv-1m candles via `client.timeseries.get_range()`.
2. **Initial batch**: First N candles (≥21 for SMA calculation) are loaded into `CandleBuffer` immediately and sent to frontend as `initial_load`.
3. **Simulation replay**: `SimulationReplay` takes remaining candles and feeds them one-by-one into `CandleBuffer` at 1-second intervals, simulating live market behavior.
4. **Signal evaluation**: After each candle is appended and buffer has ≥21 candles → `SignalEngine` computes SMAs and checks for crossover.
5. **Frontend push**: `ThrottledPusher` sends updates at max 1/sec via WebSocket.

### Data Flow — Fase B (Live Streaming)

1. **Startup**: Same as Fase A — load historical candles for initial context.
2. **Live subscription**: `DabentoConnector` subscribes to `ohlcv-1m` on GLBX.MDP3 for NQ.c.0.
3. **Candle reception**: Each time Databento closes a 1-minute interval, it delivers a completed OHLCV bar to the client.
4. **Processing**: Same pipeline as Fase A — append to buffer → evaluate signal → push to frontend.

**Key simplification vs. trades schema:** With `ohlcv-1m`, Databento handles the candle aggregation. We receive one record per minute (only when there are trades). No local candle building, no minute-boundary detection, no in-progress candle state needed.

### Historical ↔ Live Synchronization

Since both Historical and Live APIs use the same `ohlcv-1m` schema:

1. On startup, `DabentoConnector` fetches historical candles and records the `ts_event` of the last one.
2. When the Live subscription starts, any bar with `ts_event` ≤ the last historical bar's timestamp is discarded.
3. Result: seamless join with no duplicates and no gaps (other than natural gaps where no trades occurred).

---

## Components and Interfaces

### 1. DabentoConnector

Wraps both Historical and Live Databento clients. Single source of candle data.

```python
class DabentoConnector:
    """Wraps databento Historical and Live clients for ohlcv-1m data."""

    def __init__(self, api_key: str, dataset: str, symbol: str, stype_in: str = "continuous"):
        ...

    async def load_historical(self, count: int = 50) -> list[Candle]:
        """Fetch the most recent `count` 1-minute candles from Historical API.
        Uses schema='ohlcv-1m', stype_in='continuous' for NQ.c.0.
        Returns candles sorted by timestamp ascending.
        Handles API failure gracefully (returns empty list, 30s timeout)."""
        ...

    async def subscribe_live(self) -> AsyncIterator[Candle]:
        """Subscribe to live ohlcv-1m stream. Yields Candle objects as they arrive.
        Each yielded candle is a completed 1-minute bar."""
        ...

    async def reconnect(self) -> bool:
        """Attempt reconnection: 3 tries, exponential backoff (1s, 2s, 4s).
        Returns True if reconnected, False if all attempts failed."""
        ...

    @property
    def is_connected(self) -> bool: ...

    @property
    def last_candle_time(self) -> datetime | None: ...
```

### 2. CandleBuffer

In-memory ordered collection of completed candles.

```python
class CandleBuffer:
    """Buffer of completed candles with max capacity."""

    def __init__(self, max_size: int = 500):
        ...

    def append(self, candle: Candle) -> None: ...
    def get_closes(self, n: int) -> list[float]: ...
    def get_all(self) -> list[Candle]: ...
    def last_timestamp(self) -> datetime | None: ...
    def __len__(self) -> int: ...
```

### 3. SignalEngine

Implements SMA crossover detection. Maintains previous SMA state for crossover comparison.

```python
class SignalEngine:
    """Evaluates SMA(9)/SMA(21) crossover on completed candles."""

    def __init__(self, fast_period: int = 9, slow_period: int = 21):
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def evaluate(self, closes: list[float]) -> Signal | None:
        """Given a list of close prices (most recent last), compute SMAs
        and detect crossover. Returns BUY/SELL Signal or None.
        Requires len(closes) >= slow_period.
        Called ONLY when a completed candle arrives — never on partial data."""
        ...

    @staticmethod
    def compute_sma(prices: list[float], period: int) -> float:
        """Compute Simple Moving Average over the last `period` prices."""
        ...
```

### 4. ThrottledPusher

Unified visual update throttle — ALL updates to the frontend go through this component.

```python
class ThrottledPusher:
    """Enforces max 1 visual update per second for the single browser WebSocket.
    
    All updates (completed candle, signals, SMA values) are queued and batched
    into a single message sent at most once per second.
    """

    def __init__(self, throttle_interval: float = 1.0):
        self._ws: WebSocket | None = None
        self._last_push_time: float = 0.0
        self._pending_messages: list[dict] = []

    async def set_connection(self, ws: WebSocket | None) -> None:
        """Set or clear the single browser WebSocket connection."""
        ...

    async def queue_candle(self, candle: Candle) -> None:
        """Queue a completed candle for next push."""
        ...

    async def queue_signal(self, signal: Signal) -> None:
        """Queue a BUY/SELL signal for next push."""
        ...

    async def queue_sma_update(self, time: int, sma_fast: float, sma_slow: float) -> None:
        """Queue SMA line data point for next push."""
        ...

    async def queue_status(self, connected: bool, last_time: datetime | None) -> None:
        """Queue connection status update."""
        ...

    async def flush_if_ready(self) -> None:
        """Called periodically (every 100ms). If >= 1 second since last push
        and there are pending messages, send them all as a batch and reset."""
        ...
```

### 5. SimulationReplay (Fase A only)

Replays historical candles at 1/second to simulate live behavior.

```python
class SimulationReplay:
    """Replays a list of candles at 1-second intervals, simulating live market.
    Used in Fase A to validate the full pipeline without a live subscription."""

    def __init__(self, candles: list[Candle], interval: float = 1.0):
        ...

    async def replay(self) -> AsyncIterator[Candle]:
        """Yield candles one by one with `interval` seconds between each.
        Feeds into the same pipeline as live data."""
        ...

    @property
    def is_replaying(self) -> bool: ...

    def stop(self) -> None: ...
```

### 6. Frontend (Lightweight Charts + Vanilla JS)

Single HTML page served by FastAPI static files:

- **Chart**: `createChart()` with candlestick series, two line series (SMA 9, SMA 21)
- **Markers**: `setMarkers()` for BUY (green triangle below) / SELL (red triangle above)
- **WebSocket client**: Connects to `/ws/chart`, receives JSON messages, updates chart
- **Loading state**: On page load, shows "Cargando datos históricos..." indicator until first data batch
- **Connection status**: Shows connected/disconnected/reconnecting state with last data timestamp
- **No framework**: Plain JavaScript handles the minimal UI state

---

## Data Models

### Candle

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True, slots=True)
class Candle:
    """One completed 1-minute OHLCV bar from Databento ohlcv-1m schema."""
    timestamp: datetime  # UTC, start of the minute (from ts_event)
    open: float
    high: float
    low: float
    close: float
    volume: int
```

### Signal

```python
from enum import Enum

class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

@dataclass(frozen=True, slots=True)
class Signal:
    """A trading signal generated by SMA crossover on candle close."""
    type: SignalType
    timestamp: datetime  # UTC, time of the candle that triggered it
    price: float         # Close price of the triggering candle
    sma_fast: float      # SMA(9) value at signal time
    sma_slow: float      # SMA(21) value at signal time
```

### WebSocket Message Protocol (Backend → Frontend)

Messages are sent as a batch array, max once per second:

```json
[
    {
        "type": "candle",
        "data": {
            "time": 1700000000,
            "open": 16234.50,
            "high": 16235.75,
            "low": 16233.00,
            "close": 16234.25,
            "volume": 1420
        }
    },
    {
        "type": "signal",
        "data": {
            "signal_type": "BUY",
            "time": 1700000000,
            "price": 16234.50,
            "sma_fast": 16230.12,
            "sma_slow": 16228.45
        }
    },
    {
        "type": "sma",
        "data": {
            "time": 1700000000,
            "sma_fast": 16230.12,
            "sma_slow": 16228.45
        }
    },
    {
        "type": "status",
        "data": {
            "connected": true,
            "last_candle_time": "2025-01-15T14:30:00Z",
            "mode": "simulation"
        }
    },
    {
        "type": "initial_load",
        "data": {
            "candles": [...],
            "sma_fast_series": [...],
            "sma_slow_series": [...]
        }
    }
]
```

The `initial_load` message is sent once on WebSocket connection with all buffered candles and pre-computed SMA series. After that, incremental updates arrive as batched arrays.

### Configuration

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """Application configuration loaded from environment / .env file."""
    databento_api_key: str           # DATABENTO_API_KEY env var
    databento_dataset: str = "GLBX.MDP3"
    databento_symbol: str = "NQ.c.0"
    databento_stype_in: str = "continuous"
    sma_fast_period: int = 9
    sma_slow_period: int = 21
    historical_candles: int = 50
    ws_throttle_seconds: float = 1.0
    reconnect_max_attempts: int = 3
    log_level: str = "INFO"
    mode: str = "simulation"         # "simulation" (Fase A) or "live" (Fase B)

    class Config:
        env_file = ".env"
```

---

## Why ohlcv-1m is sufficient for this MVP

The Signal Engine uses SMA(9) and SMA(21) computed on 1-minute candle closes. This requires exactly one data point per minute — the close price. The `ohlcv-1m` schema delivers precisely this: one completed OHLCV bar per minute of market activity.

Benefits over using `trades` schema:
- **~100x less data volume:** One record per active minute vs. potentially thousands of trades per minute for NQ futures.
- **No local candle construction:** Eliminates CandleBuilder complexity, minute-boundary detection, and in-progress candle state management.
- **Same data for Historical and Live:** Both APIs support `ohlcv-1m`, enabling identical processing code for both phases.
- **Lower cost:** Less bandwidth = less usage-based billing on Historical API.

The trade-off: we cannot show "in-progress" candle updates within the current minute. With ohlcv-1m, we only see the candle after the minute completes. For an SMA crossover demo, this is acceptable — signals are only evaluated on completed candles anyway.

---

## Correctness Properties

### Property 1: SMA Crossover Signal Correctness

*For any* list of close prices with length ≥ 21 and a known previous SMA state, if `SMA(9)` transitions from below `SMA(21)` to above `SMA(21)` the system SHALL produce a BUY signal; if `SMA(9)` transitions from above `SMA(21)` to below `SMA(21)` the system SHALL produce a SELL signal; if no transition occurs the system SHALL produce no signal.

**Validates: Requirements 3.2, 3.3**

### Property 2: No Signal on Insufficient Data

*For any* list of close prices with length < 21, the SignalEngine SHALL return no signal (None), regardless of the price values.

**Validates: Requirements 3.4**

### Property 3: SMA Computation Correctness

*For any* list of N float values where N ≥ period, `compute_sma(prices, period)` SHALL equal the arithmetic mean of the last `period` values in the list.

**Validates: Requirements 3.1**

### Property 4: Unified Visual Throttle Invariant

*For any* sequence of visual update events, the ThrottledPusher SHALL never send two consecutive WebSocket messages to the frontend less than 1 second apart.

**Validates: Requirements 2.2**

### Property 5: Historical Synchronization — No Duplicates

*For any* sequence of historical candles followed by a live candle stream, when a live candle's `ts_event` ≤ the last historical candle's `ts_event`, the system SHALL discard that candle. The first live candle with `ts_event` > last historical `ts_event` SHALL be appended to the buffer.

**Validates: Requirements 1.4**

### Property 6: Candle Data Integrity from Databento

*For any* OHLCV record received from Databento, the system SHALL preserve the original values (open, high, low, close, volume, ts_event) without modification when storing in CandleBuffer and sending to frontend. The only transformation allowed is price format conversion (int64 × 1e-9 → float).

**Validates: Requirements 1.2**

---

## Error Handling

### Databento Connection Failures

| Scenario | Behavior |
|----------|----------|
| Historical API failure on startup | Log error. Start with empty buffer. If simulation mode, show "No data available" on frontend. |
| Historical API timeout | Use 30-second timeout. On failure, proceed without historical data. |
| Historical returns fewer than 50 candles | Accept what's available. If < 21, disable signal generation until buffer fills from live/simulation. |
| Live connection failure | Retry with exponential backoff (1s → 2s → 4s). After 3 failures, show disconnection banner. |
| Mid-session live disconnect | Trigger reconnection logic. Frontend shows "Reconectando..." |
| All live retries exhausted | Display persistent banner: "Conexión perdida. Último dato: {timestamp}". |
| AUTH_FAILED or API_KEY_DEACTIVATED | Log error. Display "Autenticación fallida" on frontend. Do NOT retry. |
| SYMBOL_RESOLUTION_FAILED | Log warning. This is non-fatal — may resolve later. |

### Frontend WebSocket

| Scenario | Behavior |
|----------|----------|
| Browser disconnects | Clear connection reference. No error propagation. Backend continues. |
| Backend pushes to closed socket | Catch exception, clear connection, log at DEBUG level. |
| No browser connected | Backend continues processing normally (no-op pushes). |

### Logging Strategy

Standard Python `logging` module with clear format:

```python
import logging

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
```

Components use named loggers (`logging.getLogger(__name__)`).

---

## Testing Strategy

### Unit Tests (pytest)

| Component | Test Cases |
|-----------|-----------|
| DabentoConnector | Mock API: successful historical load, empty response, timeout handling, candle parsing from ohlcv-1m records |
| CandleBuffer | Append, get_closes, max_size overflow, last_timestamp, empty buffer |
| SignalEngine | Exact crossover detection (BUY and SELL); no signal when SMAs don't cross; no signal with < 21 candles; SMA matches manual calculation |
| ThrottledPusher | Two rapid events → one push; event after 1s → immediate push; candle + signal batched together; no push when no pending data |
| SimulationReplay | Candles yield at correct interval; stop halts replay |
| Configuration | Missing env vars raise error; defaults correct; mode defaults to "simulation" |

### Property-Based Tests (Hypothesis)

**Configuration:**
```python
from hypothesis import settings as hyp_settings
hyp_settings.register_profile("ci", max_examples=200)
hyp_settings.register_profile("default", max_examples=100)
```

**Test files:**
- `tests/property/test_signal_engine_props.py` — Properties 1, 2, 3
- `tests/property/test_throttle_props.py` — Property 4
- `tests/property/test_buffer_props.py` — Properties 5, 6

**Tag format:**
```python
# Feature: tradecore-mvp, Property 1: SMA Crossover Signal Correctness
```

### Integration Tests

| Test | Description |
|------|-------------|
| End-to-end historical flow | Mock Databento → DabentoConnector → Buffer → SignalEngine |
| Simulation replay | Verify candles arrive at 1/sec through full pipeline |
| WebSocket message format | Connect WS, receive JSON, validate schema matches protocol spec |

---

## Pending Decisions (To Verify in Connectivity Spike)

1. **Price conversion in ohlcv-1m records**: Verify whether the Python SDK's `.to_df()` or record properties (`.pretty_open`, etc.) provide direct float values, or if manual division by 1e9 is needed.

2. **Live ohlcv-1m delivery timing**: Verify when Databento delivers the ohlcv-1m record — at the exact minute boundary, or with a small delay after aggregation. This affects perceived latency.

3. **Continuous contract resolution**: Verify NQ.c.0 resolves correctly with a symbology.resolve call before proceeding with data requests.

4. **Historical API "last N bars" approach**: Verify whether to compute `start = now - (count * 1 minute)` or if there's a simpler way to request "most recent N bars."

5. **Live subscription to ohlcv-1m**: Verify that subscribing to `ohlcv-1m` on the Live API delivers completed bars (not partial/in-progress bars). The documentation states that OHLCV records are only emitted when the interval completes and has trades.
