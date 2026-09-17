"""FastAPI application with WebSocket route and Fase A simulation orchestration.

Provides:
- WebSocket endpoint at /ws/chart for real-time chart data
- Static file serving from dashboard/ directory
- Fase A simulation pipeline: load historical → split → replay

Requirements: RF-01.1, RF-01.2, RF-01.4, RF-02.2, RF-03.1
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from src.api.throttled_pusher import ThrottledPusher
from src.config import Settings
from src.connectors.databento_connector import DabentoConnector
from src.engine.signal_engine import SignalEngine
from src.pipeline.candle_buffer import CandleBuffer
from src.pipeline.simulation_replay import SimulationReplay
from src.schemas.candle import Candle

logger = logging.getLogger(__name__)

# Module-level state shared across the app
_settings: Settings | None = None
_buffer: CandleBuffer | None = None
_pusher: ThrottledPusher | None = None
_engine: SignalEngine | None = None
_replay: SimulationReplay | None = None
_flush_task: asyncio.Task | None = None
_pipeline_task: asyncio.Task | None = None


def _configure_logging(level: str) -> None:
    """Configure standard Python logging at the specified level."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _build_initial_load_message(
    candles: list[Candle],
    sma_fast_period: int,
    sma_slow_period: int,
) -> dict:
    """Build the initial_load WebSocket message with candles and pre-computed SMA series."""
    candle_dicts = [
        {
            "time": int(c.timestamp.timestamp()),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]

    closes = [c.close for c in candles]

    # Compute SMA series for all candles where enough data exists
    sma_fast_series = []
    sma_slow_series = []

    for i in range(len(closes)):
        n = i + 1  # number of prices available up to index i
        if n >= sma_fast_period:
            sma_fast_val = SignalEngine.compute_sma(closes[: n], sma_fast_period)
            sma_fast_series.append({
                "time": int(candles[i].timestamp.timestamp()),
                "value": round(sma_fast_val, 2),
            })
        if n >= sma_slow_period:
            sma_slow_val = SignalEngine.compute_sma(closes[: n], sma_slow_period)
            sma_slow_series.append({
                "time": int(candles[i].timestamp.timestamp()),
                "value": round(sma_slow_val, 2),
            })

    return {
        "type": "initial_load",
        "data": {
            "candles": candle_dicts,
            "sma_fast_series": sma_fast_series,
            "sma_slow_series": sma_slow_series,
        },
    }


async def _flush_loop() -> None:
    """Periodically flush the ThrottledPusher every 100ms."""
    global _pusher
    while True:
        try:
            if _pusher is not None:
                await _pusher.flush_if_ready()
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Flush loop error: %s", exc)
            await asyncio.sleep(0.1)


async def _processing_loop() -> None:
    """Receive candles from SimulationReplay and process through the pipeline."""
    global _replay, _buffer, _engine, _pusher, _settings

    if _replay is None or _buffer is None or _engine is None or _pusher is None:
        logger.error("Pipeline components not initialized for processing loop.")
        return

    if _settings is None:
        logger.error("Settings not available for processing loop.")
        return

    async for candle in _replay.replay():
        try:
            # Append to buffer
            _buffer.append(candle)

            # Get closes for signal evaluation
            closes = _buffer.get_closes(_settings.sma_slow_period)

            # Evaluate signal
            signal = _engine.evaluate(closes)

            # Queue candle update
            await _pusher.queue_candle(candle)

            # Queue SMA update if we have enough data
            if len(closes) >= _settings.sma_slow_period:
                sma_fast = SignalEngine.compute_sma(closes, _settings.sma_fast_period)
                sma_slow = SignalEngine.compute_sma(closes, _settings.sma_slow_period)
                time_val = int(candle.timestamp.timestamp())
                await _pusher.queue_sma_update(time_val, round(sma_fast, 2), round(sma_slow, 2))

            # Queue signal if crossover detected
            if signal is not None:
                await _pusher.queue_signal(signal)
                logger.info("Signal generated: %s at %.2f", signal.type.value, signal.price)

        except Exception as exc:
            logger.error("Processing loop error for candle %s: %s", candle.timestamp, exc)

    logger.info("Simulation replay completed.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: setup on startup, cleanup on shutdown."""
    global _settings, _buffer, _pusher, _engine, _replay, _flush_task, _pipeline_task

    # Load settings
    _settings = Settings()
    _configure_logging(_settings.log_level)
    logger.info("TradeCore starting in '%s' mode.", _settings.mode)

    # Initialize components
    _buffer = CandleBuffer()
    _pusher = ThrottledPusher(throttle_interval=_settings.ws_throttle_seconds)
    _engine = SignalEngine(
        fast_period=_settings.sma_fast_period,
        slow_period=_settings.sma_slow_period,
    )

    # Start flush loop
    _flush_task = asyncio.create_task(_flush_loop())

    if _settings.mode == "simulation":
        # Load historical candles
        connector = DabentoConnector(
            api_key=_settings.databento_api_key,
            dataset=_settings.databento_dataset,
            symbol=_settings.databento_symbol,
            stype_in=_settings.databento_stype_in,
            # Use 3-day offset to avoid CME intraday data licensing restrictions
            # (recent ~24h requires paid subscription; archived data is freely accessible)
            end_offset_minutes=4320,
        )
        candles = await connector.load_historical(count=_settings.historical_candles)

        if candles:
            # Split at sma_slow_period: initial batch for pre-load, rest for replay
            split_index = _settings.sma_slow_period
            initial_batch = candles[:split_index]
            replay_batch = candles[split_index:]

            # Populate buffer with initial batch
            for candle in initial_batch:
                _buffer.append(candle)

            # Seed SignalEngine with initial closes so crossover state is primed
            initial_closes = _buffer.get_closes(_settings.sma_slow_period)
            if len(initial_closes) >= _settings.sma_slow_period:
                _engine.evaluate(initial_closes)

            # Create SimulationReplay for remaining candles
            _replay = SimulationReplay(candles=replay_batch, interval=1.0)

            # Start the pipeline processing loop
            _pipeline_task = asyncio.create_task(_processing_loop())
            logger.info(
                "Simulation started: %d initial candles, %d to replay.",
                len(initial_batch),
                len(replay_batch),
            )
        else:
            logger.warning("No historical candles loaded. Simulation will not run.")

    elif _settings.mode == "live":
        # FIRST_FUNCTIONAL_LIVE_CHART_SLICE (ver tasks.md): rama Live MINIMA.
        # NO completa T6.2 (sin maquina de estados W2, sin warm-up/persistencia/persist-before-publish,
        # sin T2.10, sin gate compuesto — ver T6_2_REMAINING_AFTER_FIRST_CHART_SLICE).
        # Reutiliza el MISMO _processing_loop que Simulation: la fuente Live cumple CandleSource.
        connector = DabentoConnector(
            api_key=_settings.databento_api_key,
            dataset=_settings.databento_dataset,
            symbol=_settings.databento_symbol,
            stype_in=_settings.databento_stype_in,
        )
        # Fuente Live que cumple CandleSource (replay()); ReconnectPolicy=NONE (una sola conexion).
        _replay = connector.live_candle_source()

        # Arranca el MISMO pipeline processing loop que consume la simulacion.
        _pipeline_task = asyncio.create_task(_processing_loop())
        logger.info(
            "Live started (first functional slice): %s %s %s stype=%s, ReconnectPolicy=NONE.",
            _settings.databento_dataset,
            "ohlcv-1m",
            _settings.databento_symbol,
            _settings.databento_stype_in,
        )

    yield

    # Shutdown: stop replay and cancel tasks
    if _replay is not None:
        _replay.stop()
    if _pipeline_task is not None:
        _pipeline_task.cancel()
        try:
            await _pipeline_task
        except asyncio.CancelledError:
            pass
    if _flush_task is not None:
        _flush_task.cancel()
        try:
            await _flush_task
        except asyncio.CancelledError:
            pass

    logger.info("TradeCore shutdown complete.")


# Create FastAPI app
app = FastAPI(title="TradeCore MVP", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Health check endpoint for ECS Express Mode.

    Returns 200 OK regardless of pipeline state — must respond
    even during startup before components are initialized.
    """
    return {"status": "ok"}


# Serve static files from dashboard/ directory
_dashboard_path = Path(__file__).resolve().parent.parent.parent / "dashboard"
if _dashboard_path.exists():
    app.mount("/static", StaticFiles(directory=str(_dashboard_path)), name="static")


@app.websocket("/ws/chart")
async def websocket_chart(ws: WebSocket) -> None:
    """WebSocket endpoint for real-time chart updates.

    Accepts a single connection, sends initial_load with candles and SMA series,
    then keeps alive while the flush loop sends incremental updates.
    """
    global _pusher, _buffer, _settings

    await ws.accept()
    logger.info("WebSocket client connected.")

    if _pusher is None or _buffer is None or _settings is None:
        await ws.close(code=1011, reason="Server not ready")
        return

    # Register connection with pusher
    await _pusher.set_connection(ws)

    try:
        # Send initial_load message with current buffer state
        candles = _buffer.get_all()
        if candles:
            initial_msg = _build_initial_load_message(
                candles,
                _settings.sma_fast_period,
                _settings.sma_slow_period,
            )
            await ws.send_text(json.dumps(initial_msg))

        # Send status update
        await _pusher.queue_status(
            connected=True,
            last_time=_buffer.last_timestamp(),
            mode=_settings.mode,
        )

        # Keep connection alive — wait for client disconnect
        while True:
            # We don't expect messages from client, but this keeps the connection open
            # and detects disconnection
            await ws.receive_text()

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
    finally:
        await _pusher.set_connection(None)
        logger.info("WebSocket connection cleaned up.")
