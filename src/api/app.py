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
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.market_hours import is_regular_market_open
from src.api.throttled_pusher import ThrottledPusher, signal_payload
from src.config import Settings
from src.connectors.databento_connector import DabentoConnector
from src.engine.signal_engine import SignalEngine
from src.pipeline.candle_buffer import CandleBuffer
from src.pipeline.simulation_replay import SimulationReplay
from src.schemas.candle import Candle

logger = logging.getLogger(__name__)

# --- LIVE_REPLAY_BOOTSTRAP_SLICE constants ---
# Ventana de replay intradia solicitada al arrancar en MODE=live. Debe quedar dentro del limite
# del SDK (replay "within 24 hours"). 90 min da margen sobre el minimo matematico de warm-up de
# senal (~22 velas de 1 min) sin correlacion lineal (una ventana puede contener menos velas que
# minutos si hay minutos sin volumen). Ventana relativamente corta para reducir (NO eliminar) la
# exposicion a un rollover del contrato continuo durante el replay.
LIVE_REPLAY_LOOKBACK_MINUTES = 90

# Timeout de espera de SystemCode.REPLAY_COMPLETED durante el arranque. En la prueba real llego en
# ~0.4s; 30s da margen amplio y acotado. Si no llega dentro del timeout, el arranque DEGRADA CON
# GRACIA: continua sin bloquear (con el contexto que haya llegado), sin reconnect ni 2a conexion.
REPLAY_COMPLETION_TIMEOUT_SECONDS = 30.0

# PRE_AWS_LIVE_HARDENING: plazo maximo (en segundos) que el arranque live puede pasar en la fase de
# bootstrap (replay) SIN observar REPLAY_COMPLETED antes de que /health lo reporte como estancado
# (503). Es un guardrail PROVISIONAL del MVP: en la prueba real el bootstrap se completo en ~1s, asi
# que 300s da un margen amplio y no se recalibra en este cambio. El plazo SOLO aplica mientras falta
# REPLAY_COMPLETED; una vez el replay se completa, la fase "live" nunca vuelve a mirar este deadline.
BOOTSTRAP_STALL_DEADLINE_SECONDS = 300.0

# A3a: cada cuantos segundos el backend emite un heartbeat (latido) a los navegadores.
# 15s deja al cliente margen para detectar un corte silencioso bastante antes de su watchdog
# (45s), sin saturar la conexion. El heartbeat NO pasa por el throttle de 1s del pusher.
HEARTBEAT_INTERVAL_SECONDS = 15.0

# A3a: tope de senales de la sesion que se recuerdan para repintarlas en el estado inicial
# (initial_load) de un cliente que (re)conecta. 500 cubre de sobra una sesion normal sin crecer
# sin limite en memoria; al llenarse, descarta las mas viejas (deque con maxlen).
SIGNAL_HISTORY_MAX = 500

# Module-level state shared across the app
_settings: Settings | None = None
_buffer: CandleBuffer | None = None
_pusher: ThrottledPusher | None = None
_engine: SignalEngine | None = None
_replay: SimulationReplay | None = None
_flush_task: asyncio.Task | None = None
_pipeline_task: asyncio.Task | None = None
_heartbeat_task: asyncio.Task | None = None

# A3a: historial de senales EMITIDAS en la sesion (no las suprimidas durante el replay). Se usa
# para repintar los marcadores BUY/SELL en el estado inicial de un cliente que (re)conecta. Cada
# entrada ya esta en el formato de signal_payload(). Se reinicia en cada lifespan.
_signal_history: deque = deque(maxlen=SIGNAL_HISTORY_MAX)

# PRE_AWS_LIVE_HARDENING: estado minimo para derivar la salud del pipeline live. NO es una maquina de
# estados persistida; la fase se DERIVA en cada consulta a /health a partir de estos valores.
# - _live_bootstrap_started_at: instante monotonico (time.monotonic()) en que se creo la tarea live.
#   Se resetea a None al iniciar cada lifespan y se fija de nuevo justo antes de crear _pipeline_task
#   en el bootstrap live; no se toca por request a /health.
# - _shutdown_in_progress: True en cuanto el lifespan empieza a apagar (primero tras el yield).
# - _stall_warning_logged: garantiza que el WARNING de bootstrap estancado se emita una sola vez.
_live_bootstrap_started_at: float | None = None
_shutdown_in_progress: bool = False
_stall_warning_logged: bool = False


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
    signals: list | None = None,
    market_open: bool | None = None,
) -> dict:
    """Build the initial_load WebSocket message with candles and pre-computed SMA series.

    A3a: ademas de velas y SMA, el estado inicial incluye las senales de la sesion
    (`signals`, para repintar los marcadores al (re)conectar) y la bandera `market_open`.
    `signals` se recibe como lista de Signal; cada una se serializa con signal_payload()
    y se ordena ascendente por tiempo. Ambos parametros son opcionales para no romper a
    los llamadores existentes (tests de preservacion): por defecto signals=[] y
    market_open=None.
    """
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

    # A3a: serializa las senales de la sesion con el MISMO formato que los updates en vivo
    # (signal_payload) y las ordena ascendente por tiempo para que el frontend pinte los
    # marcadores en orden. signals=None => lista vacia (sesion sin senales todavia).
    signal_dicts = [signal_payload(s) for s in (signals or [])]
    signal_dicts.sort(key=lambda s: s["time"])

    return {
        "type": "initial_load",
        "data": {
            "candles": candle_dicts,
            "sma_fast_series": sma_fast_series,
            "sma_slow_series": sma_slow_series,
            # A3a: senales de la sesion (marcadores) y bandera de mercado abierto.
            "signals": signal_dicts,
            "market_open": market_open,
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


async def _heartbeat_loop() -> None:
    """A3a: broadcast a heartbeat to all clients every HEARTBEAT_INTERVAL_SECONDS.

    El latido lleva la bandera `market_open` (horario regular de CME) para que el navegador
    distinga "mercado cerrado" de "conexion caida". Los errores se registran pero NO matan el
    loop: un fallo puntual de envio no debe detener los latidos siguientes.
    """
    global _pusher
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            if _pusher is not None:
                market_open = is_regular_market_open(datetime.now(timezone.utc))
                await _pusher.send_heartbeat(market_open)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Heartbeat loop error: %s", exc)


async def _processing_loop(suppress_signals=None) -> None:
    """Receive candles from a CandleSource and process through the pipeline.

    `suppress_signals` (opcional, LIVE_REPLAY_BOOTSTRAP_SLICE): predicado sin argumentos que
    devuelve True mientras las senales NO deben emitirse (fase de replay del bootstrap). El motor
    SIEMPRE se evalua (para calentar su estado SMA), pero la Signal devuelta NO se encola mientras
    el predicado sea True. Por defecto es None => nunca suprime => comportamiento de simulation
    intacto. La frontera REPLAY->LIVE la decide EXCLUSIVAMENTE quien construye este predicado
    (en el slice: SystemCode.REPLAY_COMPLETED via la fuente Live), NO un conteo de velas aqui.
    """
    global _replay, _buffer, _engine, _pusher, _settings, _signal_history

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

            # Queue signal if crossover detected — but suppress emission while a replay bootstrap
            # is in progress (the engine state above was still advanced; only emission is gated).
            if signal is not None:
                if suppress_signals is not None and suppress_signals():
                    logger.debug(
                        "Signal suppressed during replay bootstrap: %s at %.2f",
                        signal.type.value, signal.price,
                    )
                else:
                    await _pusher.queue_signal(signal)
                    # A3a: recuerda la senal EMITIDA (no las suprimidas) para repintar sus
                    # marcadores en el estado inicial de clientes que (re)conecten luego.
                    _signal_history.append(signal)
                    logger.info("Signal generated: %s at %.2f", signal.type.value, signal.price)

        except Exception as exc:
            logger.error("Processing loop error for candle %s: %s", candle.timestamp, exc)

    # Este mensaje describe el fin del replay de SIMULATION. En live el fin del async for significa
    # que la fuente Live se agoto (fin inesperado del stream), NO un replay simulado terminado: por
    # eso NO se emite aqui en live (la terminacion de la tarea la observa el supervisor + /health).
    # getattr con default: solo el arranque real fija mode; ante un settings sin ese atributo se
    # trata como NO-simulation (no se emite el log), sin romper llamadas directas al loop en tests.
    if getattr(_settings, "mode", None) == "simulation":
        logger.info("Simulation replay completed.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: setup on startup, cleanup on shutdown."""
    global _settings, _buffer, _pusher, _engine, _replay, _flush_task, _pipeline_task
    global _heartbeat_task, _signal_history
    global _live_bootstrap_started_at, _shutdown_in_progress, _stall_warning_logged

    # PRE_AWS_LIVE_HARDENING (D1): reinicia el estado de salud del pipeline al comenzar CADA lifespan,
    # antes de cargar settings. Sin esto, un segundo arranque en el mismo proceso heredaria valores del
    # anterior (p. ej. _shutdown_in_progress=True dejaria /health atascado en "shutting_down", o
    # _stall_warning_logged=True suprimiria para siempre el WARNING de stall). Cada arranque parte limpio.
    _live_bootstrap_started_at = None
    _shutdown_in_progress = False
    _stall_warning_logged = False

    # A3a: cada arranque empieza con un historial de senales limpio (sin arrastrar la sesion previa
    # si el proceso reutiliza el modulo, p. ej. en tests que levantan el app varias veces).
    _signal_history = deque(maxlen=SIGNAL_HISTORY_MAX)

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

    # A3a: start heartbeat loop (latido periodico a todos los navegadores conectados).
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())

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
        # LIVE_REPLAY_BOOTSTRAP_SLICE (ver tasks.md): rama Live con bootstrap de contexto por
        # intraday replay. Pide replay desde now-LOOKBACK, alimenta buffer+engine SIN emitir
        # senales de replay, y transiciona a Live cuando llega SystemCode.REPLAY_COMPLETED (unica
        # autoridad de frontera). NO completa T6.2 (sin W2/warm-up formal/persistencia/gate compuesto).
        # Reutiliza el MISMO _processing_loop que Simulation: la fuente Live cumple CandleSource.
        connector = DabentoConnector(
            api_key=_settings.databento_api_key,
            dataset=_settings.databento_dataset,
            symbol=_settings.databento_symbol,
            stype_in=_settings.databento_stype_in,
        )
        # Inicio de replay: now - LOOKBACK (tz-aware UTC; el SDK convierte a ns internamente).
        replay_start = datetime.now(timezone.utc) - timedelta(minutes=LIVE_REPLAY_LOOKBACK_MINUTES)
        # Fuente Live con replay start; ReconnectPolicy=NONE (una sola conexion).
        _replay = connector.live_candle_source(replay_start=replay_start)
        _live_source = _replay  # referencia para el predicado de supresion

        # Supresion de senales mientras el replay esta en curso: la Signal se descarta hasta que
        # la fuente observe REPLAY_COMPLETED. El motor SIGUE evaluandose (se calienta su estado).
        def _suppress_during_replay() -> bool:
            return not _live_source.is_replay_complete

        # PRE_AWS_LIVE_HARDENING: marca de inicio del bootstrap live (monotonica), fijada UNA sola
        # vez inmediatamente antes de crear la tarea; /health la usa para detectar bootstrap estancado.
        _live_bootstrap_started_at = time.monotonic()

        # Arranca el MISMO pipeline processing loop, con supresion de senales de replay.
        _pipeline_task = asyncio.create_task(
            _processing_loop(suppress_signals=_suppress_during_replay)
        )
        # Supervisor: la tarea corre en segundo plano y el lifespan sobrevive aunque muera; sin este
        # callback una terminacion (excepcion o fin inesperado del stream) pasaria en silencio. Solo
        # se registra en live. NO reintenta ni reconecta: solo hace OBSERVABLE la terminacion.
        _pipeline_task.add_done_callback(_on_pipeline_task_done)
        logger.info(
            "Live started (replay bootstrap): %s %s %s stype=%s, replay_start=%s, ReconnectPolicy=NONE.",
            _settings.databento_dataset,
            "ohlcv-1m",
            _settings.databento_symbol,
            _settings.databento_stype_in,
            replay_start.isoformat(),
        )

        # Espera de la frontera REPLAY_COMPLETED durante el arranque, con degradacion con gracia:
        # el buffer se llena con el contexto de replay antes de que el navegador conecte. Si no
        # llega dentro del timeout, se continua igualmente (sin bloquear el arranque, sin reconnect).
        try:
            await asyncio.wait_for(
                _live_source.replay_completed_event.wait(),
                timeout=REPLAY_COMPLETION_TIMEOUT_SECONDS,
            )
            logger.info("Replay bootstrap completed (REPLAY_COMPLETED observed).")
        except asyncio.TimeoutError:
            logger.warning(
                "Replay bootstrap did not complete within %.0fs; continuing without full "
                "bootstrap context (graceful degradation, no reconnect).",
                REPLAY_COMPLETION_TIMEOUT_SECONDS,
            )

    yield

    # PRE_AWS_LIVE_HARDENING: senala apagado ANTES de detener la fuente o cancelar la tarea, para que
    # el supervisor NO reporte como fallo la cancelacion esperada y /health responda "shutting_down".
    _shutdown_in_progress = True

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
    if _heartbeat_task is not None:
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass

    logger.info("TradeCore shutdown complete.")


def _on_pipeline_task_done(task) -> None:
    """PRE_AWS_LIVE_HARDENING: supervisor de la tarea live (done_callback, solo en live).

    Hace OBSERVABLE una terminacion del pipeline live. No reintenta, no reconecta, no lanza gap
    recovery: solo registra. Ramas:
    - apagado en curso  -> no loguea (la cancelacion es esperada).
    - task.cancelled()  -> no loguea (no se llama exception() sobre una tarea cancelada).
    - termino con excepcion -> 1 CRITICAL con la causa.
    - termino sin excepcion -> 1 CRITICAL (fin inesperado del stream, sin error explicito).
    """
    if _shutdown_in_progress:
        return
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical("Pipeline live terminado por excepcion: %r", exc)
    else:
        logger.critical(
            "Pipeline live termino inesperadamente sin excepcion (stream agotado); dashboard sin "
            "actualizaciones. Requiere reinicio del servicio."
        )


# Create FastAPI app
app = FastAPI(title="TradeCore MVP", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Health check endpoint.

    En simulation (o antes de tener settings) devuelve EXACTAMENTE 200 {"status":"ok"}, igual que
    siempre. En live (PRE_AWS_LIVE_HARDENING) deriva la fase del pipeline y responde fail-fast:
    un pipeline muerto o un bootstrap estancado se reportan como no saludables (503) para que el
    orquestador (ECS/ALB) lo detecte, en vez de servir 200 con el dashboard congelado.
    """
    global _stall_warning_logged

    # Guard: solo live usa el contrato nuevo. Sin settings o en simulation => salud historica intacta.
    if _settings is None or _settings.mode != "live":
        return {"status": "ok"}

    # 1) La tarea aun no se creo (arranque muy temprano).
    if _pipeline_task is None:
        return {"status": "ok", "phase": "starting", "mode": "live"}

    # 2) Apagado en curso: cancelaciones esperadas, no es un fallo.
    if _shutdown_in_progress:
        return {"status": "ok", "phase": "shutting_down", "mode": "live"}

    # 3) Tarea terminada mientras el servicio deberia estar vivo => pipeline muerto.
    if _pipeline_task.done():
        return JSONResponse(
            status_code=503,
            content={"status": "down", "phase": "terminal", "mode": "live"},
        )

    # replay completo? se lee del global _replay (LiveCandleSource en live), sin crear estado nuevo.
    replay_complete = _replay is not None and getattr(_replay, "is_replay_complete", False)

    # 4/5) Aun en bootstrap (replay incompleto): el deadline SOLO aplica aqui.
    if not replay_complete:
        elapsed = None
        if _live_bootstrap_started_at is not None:
            elapsed = time.monotonic() - _live_bootstrap_started_at
        if elapsed is not None and elapsed > BOOTSTRAP_STALL_DEADLINE_SECONDS:
            # Bootstrap estancado: un solo WARNING aunque /health se consulte muchas veces.
            if not _stall_warning_logged:
                logger.warning(
                    "Bootstrap live estancado (stall): %.0fs sin REPLAY_COMPLETED "
                    "(deadline %.0fs). Reportando 503 para que el orquestador reaccione.",
                    elapsed, BOOTSTRAP_STALL_DEADLINE_SECONDS,
                )
                _stall_warning_logged = True
            return JSONResponse(
                status_code=503,
                content={"status": "down", "phase": "bootstrap_stalled", "mode": "live"},
            )
        return {"status": "ok", "phase": "bootstrapping", "mode": "live"}

    # 6) Tarea viva y replay completo: operacion normal. El deadline ya no cuenta.
    return {"status": "ok", "phase": "live", "mode": "live"}


# Serve static files from dashboard/ directory
_dashboard_path = Path(__file__).resolve().parent.parent.parent / "dashboard"
if _dashboard_path.exists():
    app.mount("/static", StaticFiles(directory=str(_dashboard_path)), name="static")


@app.websocket("/ws/chart")
async def websocket_chart(ws: WebSocket) -> None:
    """WebSocket endpoint for real-time chart updates (multi-cliente).

    A3a: soporta VARIOS navegadores a la vez. Cada cliente recibe SIEMPRE un initial_load
    (aunque el buffer este vacio: candles=[], signals=[]), con las senales de la sesion y la
    bandera de mercado abierto, y luego se registra en el pusher para recibir los updates
    incrementales por broadcast. El registro se hace SIN ningun await entre tomar el snapshot
    del buffer y add_connection, para que el cliente no se pierda un flush intermedio.
    """
    global _pusher, _buffer, _settings, _signal_history

    await ws.accept()
    logger.info("WebSocket client connected.")

    if _pusher is None or _buffer is None or _settings is None:
        await ws.close(code=1011, reason="Server not ready")
        return

    # --- Snapshot del estado inicial + registro, SIN await intermedio ---------------
    # Toma el estado actual del buffer y arma el initial_load. Entre esta captura y
    # add_connection NO hay ningun await: asi el cliente queda registrado con un estado
    # coherente y no puede perderse un batch enviado "en el medio".
    candles = _buffer.get_all()

    # Senales de la sesion acotadas al rango temporal del buffer (primera..ultima vela).
    # Si no hay velas, no hay rango que mostrar -> lista vacia.
    session_signals: list = []
    if candles:
        first_ts = candles[0].timestamp
        last_ts = candles[-1].timestamp
        session_signals = [
            s for s in _signal_history if first_ts <= s.timestamp <= last_ts
        ]

    market_open = is_regular_market_open(datetime.now(timezone.utc))

    initial_msg = _build_initial_load_message(
        candles,
        _settings.sma_fast_period,
        _settings.sma_slow_period,
        signals=session_signals,
        market_open=market_open,
    )

    # Registra la conexion ANTES del primer envio (sin await desde el snapshot hasta aqui).
    _pusher.add_connection(ws)

    try:
        # Envia el estado inicial directamente a ESTE cliente (sin pasar por el throttle).
        ok = await _pusher.send_direct(ws, json.dumps(initial_msg))
        if not ok:
            # El cliente ya no esta disponible: termina y limpia en el finally.
            return

        # Status update (igual que antes: encolado y enviado por el flush loop a todos).
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
        # A3a: elimina SOLO esta conexion (nunca set_connection(None), que las borraria todas).
        _pusher.remove_connection(ws)
        logger.info("WebSocket connection cleaned up.")
