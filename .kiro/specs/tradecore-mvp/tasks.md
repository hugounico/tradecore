# Implementation Plan: TradeCore MVP

## Overview

This plan implements the end-to-end data chain: Databento → OHLCV → Chart → Signal → Visualization. The system is a single-process Python application using FastAPI, the `databento` Python client (ohlcv-1m schema), and TradingView Lightweight Charts. Tasks follow a strict sequence: Fase A (historical data + simulation) must be fully validated before Fase B (live streaming) begins.

## Tasks

- [x] 1. Skill + Steering de Databento
  - [x] 1.1 Verify Databento Skill and Steering files are complete
    - Confirm `.kiro/skills/databento-integration/` contains SKILL.md, references/, and scripts/
    - Confirm `.kiro/steering/databento-architecture.md` exists with all architectural decisions
    - Verify no API keys, secrets, or credentials are stored in any Skill, Steering, or Spec file
    - _Requirements: RNF-02_

- [x] 2. Project structure, configuration, and secure API key setup
  - [x] 2.1 Create project directory structure and dependency files
    - Create `src/`, `src/connectors/`, `src/pipeline/`, `src/engine/`, `src/api/`, `src/schemas/`
    - Create `tests/`, `tests/unit/`, `tests/property/`, `tests/integration/`
    - Create `dashboard/` for static frontend files
    - Create `requirements.txt` with: fastapi, uvicorn[standard], databento, pydantic-settings, python-dotenv, hypothesis, pytest, httpx
    - Create `.env.example` with: `DATABENTO_API_KEY=your-api-key-here`, `MODE=simulation`, `LOG_LEVEL=INFO`
    - Create `.gitignore` including `.env`, `__pycache__/`, `*.pyc`, `.venv/`
    - _Requirements: RNF-02_

  - [x] 2.2 Implement configuration module with Pydantic Settings
    - Create `src/config.py` with `Settings` class using `pydantic_settings.BaseSettings`
    - Fields: `databento_api_key` (str, from DATABENTO_API_KEY env var), `databento_dataset` (default "GLBX.MDP3"), `databento_symbol` (default "NQ.c.0"), `databento_stype_in` (default "continuous"), `sma_fast_period` (default 9), `sma_slow_period` (default 21), `historical_candles` (default 50), `ws_throttle_seconds` (default 1.0), `reconnect_max_attempts` (default 3), `log_level` (default "INFO"), `mode` (default "simulation")
    - Configure `env_file = ".env"`
    - Verify that missing `DATABENTO_API_KEY` raises a clear validation error
    - _Requirements: RF-01.1, RNF-02_

  - [x] 2.3 Implement data models (Candle, Signal)
    - Create `src/schemas/candle.py` with frozen dataclass `Candle`: timestamp (datetime UTC), open, high, low, close (float), volume (int)
    - Create `src/schemas/signal.py` with `SignalType` enum (BUY/SELL) and frozen dataclass `Signal`: type, timestamp, price, sma_fast, sma_slow
    - Create `src/schemas/__init__.py` exporting all models
    - _Requirements: RF-01.2, RF-03.1_

- [x] 3. Connectivity Spike — Validate Databento access
  - [x] 3.1 Create connectivity spike script
    - Create `scripts/connectivity_spike.py` (standalone, NOT part of the application)
    - Load API key from DATABENTO_API_KEY env var
    - Initialize Databento Historical client
    - Resolve NQ.c.0 via `client.reference.symbology.resolve()` — log which raw_symbol it maps to
    - Download 5 ohlcv-1m candles for NQ.c.0 from a recent date
    - Log: each candle's timestamp, open, high, low, close, volume
    - Close connection cleanly
    - This script validates: API key works, symbol resolves, ohlcv-1m schema returns data, price format is understood
    - Do NOT build Feature Store, Dashboard, or Signal Engine here — just validate connectivity
    - _Requirements: RF-01.1, RF-01.4_

- [x] 4. Implement CandleBuffer
  - [x] 4.1 Implement CandleBuffer class
    - Create `src/pipeline/candle_buffer.py`
    - Constructor accepts `max_size: int = 500`
    - Methods: `append(candle)`, `get_closes(n) -> list[float]`, `get_all() -> list[Candle]`, `last_timestamp() -> datetime | None`, `__len__`
    - Maintains ordered list of completed candles, drops oldest when max_size exceeded
    - Discards candles with `ts_event` ≤ `last_timestamp()` (deduplication)
    - _Requirements: RF-01.2, RF-01.4_

- [x] 5. Implement SignalEngine with SMA crossover detection
  - [x] 5.1 Implement SignalEngine class
    - Create `src/engine/signal_engine.py`
    - Constructor accepts `fast_period: int = 9`, `slow_period: int = 21`
    - `compute_sma(prices, period) -> float` static method: arithmetic mean of last `period` values
    - `evaluate(closes: list[float]) -> Signal | None`: compute SMAs, detect crossover using previous state, return BUY/SELL/None
    - Stores `_prev_fast` and `_prev_slow` for crossover detection between calls
    - Returns None if `len(closes) < slow_period`
    - _Requirements: RF-03.1, RF-03.2, RF-03.3, RF-03.4_

  - [x] 5.2 Write property tests for SignalEngine
    - Create `tests/property/test_signal_engine_props.py`
    - **Property 1: SMA Crossover Signal Correctness** — Generate close prices with known previous SMA state; verify BUY when SMA(9) crosses above SMA(21), SELL when crosses below, None when no transition. Tag: `# Feature: tradecore-mvp, Property 1: SMA Crossover Signal Correctness`
    - **Property 2: No Signal on Insufficient Data** — Generate close price lists with length < 21; verify evaluate always returns None. Tag: `# Feature: tradecore-mvp, Property 2: No Signal on Insufficient Data`
    - **Property 3: SMA Computation Correctness** — Generate lists of N floats where N ≥ period; verify compute_sma equals arithmetic mean of last `period` values. Tag: `# Feature: tradecore-mvp, Property 3: SMA Computation Correctness`
    - Minimum 100 iterations each
    - _Requirements: RF-03.1, RF-03.2, RF-03.3, RF-03.4_

  - [x] 5.3 Write unit tests for SignalEngine
    - Create `tests/unit/test_signal_engine.py`
    - Test: exact crossover BUY and SELL; no signal when SMAs don't cross; no signal with < 21 candles; SMA matches manual calculation
    - _Requirements: RF-03.1, RF-03.2, RF-03.3, RF-03.4_

- [x] 6. Implement DabentoConnector (Historical loading)
  - [x] 6.1 Implement DabentoConnector class — Historical part
    - Create `src/connectors/databento_connector.py`
    - Constructor accepts: `api_key`, `dataset`, `symbol`, `stype_in`
    - `load_historical(count=50) -> list[Candle]`: fetch via `client.timeseries.get_range()` with schema `ohlcv-1m`, stype_in='continuous', for NQ.c.0
    - Convert Databento OHLCVMsg records to Candle dataclass (handle price conversion from int64 × 1e-9 → float)
    - Handle API failures gracefully: return empty list on error, 30s timeout
    - Sort candles by timestamp ascending
    - _Requirements: RF-01.1, RF-01.4_

  - [x] 6.2 Write unit tests for DabentoConnector (Historical)
    - Create `tests/unit/test_connectors.py`
    - Test with mocked databento Historical client: successful load returns candles, empty response returns empty list, timeout returns empty list, price conversion is correct
    - _Requirements: RF-01.1, RF-01.4_

- [x] 7. Implement SimulationReplay and wire Fase A pipeline
  - [x] 7.1 Implement SimulationReplay class
    - Create `src/pipeline/simulation_replay.py`
    - Constructor accepts: `candles: list[Candle]`, `interval: float = 1.0`
    - `replay() -> AsyncIterator[Candle]`: yields candles one-by-one with `interval` seconds between each, simulating live behavior
    - Properties: `is_replaying`, method `stop()`
    - _Requirements: RF-01.4, RF-02.2_

  - [x] 7.2 Implement ThrottledPusher class
    - Create `src/api/throttled_pusher.py`
    - Constructor accepts `throttle_interval: float = 1.0`
    - Methods: `set_connection(ws)`, `queue_candle(candle)`, `queue_signal(signal)`, `queue_sma_update(time, sma_fast, sma_slow)`, `queue_status(connected, last_time, mode)`, `flush_if_ready()`
    - Enforces max 1 WebSocket message per second
    - Batches all pending messages into a single JSON array on flush
    - _Requirements: RF-02.2, RF-02.3_

  - [x] 7.3 Write property test: Unified Visual Throttle Invariant
    - Create `tests/property/test_throttle_props.py`
    - **Property 4: Unified Visual Throttle Invariant** — Generate sequences of update events with varying timestamps; verify no two consecutive WebSocket sends are less than 1 second apart
    - Minimum 100 iterations
    - Tag: `# Feature: tradecore-mvp, Property 4: Unified Visual Throttle Invariant`
    - _Requirements: RF-02.2_

  - [x] 7.4 Write unit tests for ThrottledPusher and SimulationReplay
    - Create `tests/unit/test_throttled_pusher.py` — Test: two rapid events → one push; event after 1s → immediate push; candle + signal batched; no push when empty
    - Create `tests/unit/test_simulation_replay.py` — Test: candles yield at correct interval; stop halts replay
    - _Requirements: RF-02.2_

- [x] 8. Implement FastAPI application with WebSocket endpoint (Simulation mode)
  - [x] 8.1 Implement FastAPI app with WebSocket route and Fase A orchestration
    - Create `src/api/app.py` with FastAPI application
    - WebSocket endpoint at `/ws/chart`: accept single connection, store in ThrottledPusher, send `initial_load` message with first batch of candles and pre-computed SMA series
    - On startup (mode="simulation"): load historical candles via DabentoConnector → split into initial batch (first 21+) and replay batch → populate CandleBuffer with initial → start SimulationReplay for the rest
    - Processing loop: receive candle from SimulationReplay → append to CandleBuffer → evaluate signal via SignalEngine → queue updates to ThrottledPusher
    - Periodic flush task (every 100ms) calls `ThrottledPusher.flush_if_ready()`
    - Serve static files from `dashboard/` directory
    - Configure logging with standard Python `logging` module
    - _Requirements: RF-01.1, RF-01.2, RF-01.4, RF-02.2, RF-03.1_

  - [x] 8.2 Write unit tests for WebSocket message format
    - Create `tests/unit/test_api.py`
    - Test: initial_load message structure, candle update format, signal message format, status message format, batch array structure
    - _Requirements: RF-02.1, RF-04.1, RF-04.2_

- [x] 9. Implement frontend dashboard
  - [x] 9.1 Create HTML page with TradingView Lightweight Charts and WebSocket client
    - Create `dashboard/index.html` with:
    - Include TradingView Lightweight Charts v4 from CDN
    - Initialize chart with candlestick series, two line series (SMA 9 blue, SMA 21 orange)
    - WebSocket client connecting to `/ws/chart`
    - Handle `initial_load`: render all historical candles, SMA lines
    - Handle `candle` updates: add completed candle to chart
    - Handle `signal` messages: call `setMarkers()` — BUY as green triangle below candle, SELL as red triangle above
    - Handle `sma` updates: add data points to SMA line series
    - Handle `status` messages: update connection indicator (show mode: "Simulación" or "Live")
    - Loading state: show "Cargando datos históricos..." until first data batch received
    - Connection status: show connected/disconnected with last data timestamp
    - Vanilla JavaScript, no framework
    - _Requirements: RF-02.1, RF-02.2, RF-02.3, RF-04.1, RF-04.2_

- [x] 10. Integration wiring, Docker setup, and Fase A validation
  - [x] 10.1 Wire all components in main application entry point
    - Create `src/main.py` as the application entry point
    - Instantiate Settings, DabentoConnector, CandleBuffer, SignalEngine, ThrottledPusher, SimulationReplay
    - Wire the startup sequence based on `settings.mode`:
      - "simulation": load history → split initial/replay → populate buffer → start simulation replay → run event loop
      - "live": load history → populate buffer → start live subscription → run event loop
    - Ensure proper async lifecycle management (startup/shutdown events in FastAPI)
    - _Requirements: RF-01.1, RF-01.4_

  - [x] 10.2 Create Dockerfile and docker-compose.yml
    - Create `Dockerfile` for the FastAPI application (Python 3.11+ base, copy src/ and dashboard/, install requirements, expose port 8000)
    - Create `docker-compose.yml` with single `app` service, mapping port 8000, passing DATABENTO_API_KEY from host env
    - _Requirements: RNF-02_

  - [x] 10.3 Write integration tests for end-to-end Fase A flow
    - Create `tests/integration/test_e2e.py`
    - Test: mock Databento Historical → DabentoConnector → CandleBuffer → SignalEngine flow
    - Test: simulation replay delivers candles at 1/sec through pipeline
    - Test: WebSocket connection receives valid JSON matching protocol spec
    - **Property 5: Historical Synchronization — No Duplicates** — verify deduplication logic in CandleBuffer
    - **Property 6: Candle Data Integrity** — verify OHLCV values are preserved from Databento through to frontend message
    - _Requirements: RF-01.2, RF-01.4, RF-03.1_

- [x] 11. Checkpoint — Fase A complete
  - Ensure ALL tests pass
  - Verify full pipeline works: historical load → simulation replay → chart updates → signals appear
  - This is the validation milestone before proceeding to Fase B
  - Ask the user for approval to proceed

- [ ] 12. Implement DabentoConnector — Live streaming (Fase B)
  - [ ] 12.1 Add live subscription to DabentoConnector
    - Extend `src/connectors/databento_connector.py` with:
    - `subscribe_live() -> AsyncIterator[Candle]`: subscribe to ohlcv-1m on GLBX.MDP3 for NQ.c.0 via `db.Live()`, yield Candle objects as completed bars arrive
    - Implement reconnection: 3 attempts with exponential backoff (1s, 2s, 4s), using SDK's reconnect_policy or manual logic
    - Properties: `is_connected`, `last_candle_time`
    - Handle live ↔ historical synchronization: discard live candles with ts_event ≤ last historical candle
    - _Requirements: RF-01.1, RF-01.3, RNF-01_

  - [ ] 12.2 Add reconnection UX and error states
    - Frontend: on WebSocket close/error, show "Reconectando..." banner, attempt reconnection
    - Backend: on Databento disconnect, trigger reconnection logic, push status update to frontend
    - After 3 failed reconnection attempts: show persistent banner "Conexión perdida. Último dato: {timestamp}"
    - Handle AUTH_FAILED: show "Autenticación fallida", do NOT retry
    - _Requirements: RNF-01_

  - [ ] 12.3 Write unit tests for live connection and reconnection
    - Extend `tests/unit/test_connectors.py`
    - Test with mocked databento Live client: subscription yields candles, reconnection logic (3 attempts, backoff), error handling (AUTH_FAILED stops retries)
    - _Requirements: RF-01.1, RNF-01_

- [x] 13. Deploy to Amazon ECS Express Mode
  - [x] 13.1 Add health check endpoint to FastAPI
    - Add `GET /health` route in `src/api/app.py` returning `{"status": "ok"}` with HTTP 200
    - This endpoint is used by the ECS health check configuration
    - _Requirements: RNF-01_

  - [x] 13.2 Store DATABENTO_API_KEY in AWS Secrets Manager
    - Create a secret named `tradecore/databento-api-key` in Secrets Manager
    - Value: plaintext string (the API key itself, not JSON-wrapped)
    - This secret is referenced from the ECS Task Definition via `valueFrom`
    - Do NOT store the key as a plaintext environment variable in the task definition
    - _Requirements: RNF-02_

  - [x] 13.3 Create ECS Express Mode Service (Console wizard + ECR push)
    - **Step 1 — Push Docker image to ECR** (requires AWS CLI configured with credentials):
      ```
      aws sts get-caller-identity --query Account --output text
      aws ecr create-repository --repository-name tradecore --region <REGION> || true
      aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com
      docker build -t tradecore .
      docker tag tradecore:latest <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/tradecore:latest
      docker push <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/tradecore:latest
      ```
      Use the output of the first command as `<ACCOUNT_ID>`. Replace `<REGION>` with your target region.
    - **Step 2 — Open ECS Console wizard**: Navigate to ECS → Create Service → select "Express mode"
    - **Step 3 — Configure everything in the single wizard screen**:
      - Image: select `<ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/tradecore:latest` from ECR
      - Port mapping: container port 8000
      - Task Execution Role: select "Create new role" → will be created as `tradecoreTaskExecRole`
      - Infrastructure Role: select "Create new role" → will be created as `tradecoreInfraRole`
      - Health check: `GET /health` path, interval 30s, timeout 5s, healthy threshold 2, unhealthy threshold 3
      - Environment variables: `MODE=live`, `LOG_LEVEL=INFO`
      - Secrets: `DATABENTO_API_KEY` → reference to `tradecore/databento-api-key` in Secrets Manager (ARN from 13.2)
      - **Scaling: minimum tasks = 1, maximum tasks = 1** (single-instance requirement — CandleBuffer and ThrottledPusher maintain in-memory state; multiple instances would break state consistency)
    - **Step 4 — Click "Create"**: this single action creates the service AND both new roles simultaneously
    - **Step 5 — Add Secrets Manager policy to Task Execution Role** (post-creation):
      - Go to IAM → Roles → `tradecoreTaskExecRole`
      - Add an inline policy granting `secretsmanager:GetSecretValue` on the ARN of secret `tradecore/databento-api-key` (the role only exists after Step 4, so this must be done afterwards)
    - Note: ECS Express Mode provisions a shared Application Load Balancer automatically — no manual ALB/public-IP configuration needed
    - _Requirements: RNF-01, RNF-02_

  - [x] 13.4 Verify deployment and WebSocket connectivity
    - Confirm the service is running with 1 task in RUNNING state
    - Verify `GET /health` returns 200 from the public URL
    - Verify WebSocket connection to `wss://<url>/ws/chart` succeeds and receives `initial_load` message
    - Confirm always-on: task count stays at 1 (no scale-to-zero behavior)
    - _Requirements: RNF-01, RNF-02_

  - **Correcciones aplicadas en producción, no anticipadas en el plan original:**
    - URI de imagen quedó anclada a un digest sha256 fijo en vez del tag `:latest` — corregido manualmente editando el campo de imagen del servicio.
    - DATABENTO_API_KEY en Secrets Manager tenía espacios/saltos de línea invisibles corrompiendo el Basic Auth — corregido con `put-secret-value` + `.Trim()`.
    - GLBX.MDP3 requiere licencia para datos intradía (~24h); el modo simulation usa `end_offset_minutes=4320` (3 días) en ese callsite específico para evitarlo sin plan Standard.
    - `lightweight-charts` se cargaba sin versión fijada desde unpkg; v5 rompió `addCandlestickSeries`. Fijada a `@4.2.0`.
    - Existe un endpoint temporal `GET /debug/network` en `app.py` — hay que eliminarlo, ya cumplió su propósito de diagnóstico.
    - El dashboard muestra "Último: Invalid Date" en el timestamp — bug menor de formato pendiente.

- [ ] 14. Final checkpoint — Fase B complete ⚠️ **BLOQUEADA — NO EJECUTAR TODAVÍA**
  - Ensure live data flows end-to-end: Databento Live → Chart → Signals
  - Verify reconnection works after simulated disconnect
  - Confirm the deployed URL is accessible and responsive
  - Ask the user if questions arise
  - **⚠️ Esta Task está bloqueada**: requiere que la Task 12 (subscribe_live() y reconexión) esté implementada y aprobada. El despliegue actual usa MODE=simulation, no streaming en vivo. No ejecutar hasta que Task 12.1 y 12.2 estén completadas.

## Notes

- Tasks marked with `*` contain tests (optional for faster iteration but recommended)
- **GATE between Task 11 and Task 12**: Fase B CANNOT begin without explicit user approval and activation of Databento Standard plan
- Fase A validates the full pipeline using only historical data (free credits, $125 included with account)
- Fase B adds live streaming (requires Standard plan, monthly license fee)
- The connectivity spike (Task 3) is intentionally minimal — it only validates API access, not the full pipeline
- Property-based tests use Hypothesis with minimum 100 iterations
- Property tests tagged: `# Feature: tradecore-mvp, Property {N}: {description}`
- All timestamps are UTC
- No CandleBuilder needed — Databento delivers completed ohlcv-1m bars directly

### Hito alcanzado (fuera de secuencia del plan original)

Fase A (pipeline histórico simulado) validada y desplegada en producción en AWS, accesible públicamente. Esto es una desviación intencional del orden original del plan (que asumía Task 13 después de Task 12) — se priorizó tener algo compartible con socios antes de implementar streaming en vivo.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["4.1", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "6.1"] },
    { "id": 5, "tasks": ["6.2", "7.1", "7.2"] },
    { "id": 6, "tasks": ["7.3", "7.4", "8.1"] },
    { "id": 7, "tasks": ["8.2", "9.1"] },
    { "id": 8, "tasks": ["10.1", "10.2"] },
    { "id": 9, "tasks": ["10.3"] },
    { "id": "GATE", "tasks": ["12.1", "12.2"] },
    { "id": 10, "tasks": ["12.3", "13.1", "13.2"] },
    { "id": 11, "tasks": ["13.3"] },
    { "id": 12, "tasks": ["13.4"] }
  ]
}
```
