    (function () {
        'use strict';

        // =====================================================================
        // A3a — Resiliencia del dashboard en vivo
        // ---------------------------------------------------------------------
        // Objetivos de este script:
        //  - UNA sola conexion WebSocket activa a la vez (sin sockets zombis).
        //  - Reconexion ILIMITADA con backoff (espera creciente) + jitter (ruido
        //    aleatorio para no reconectar todos los clientes a la vez).
        //  - Watchdog: si no llega NINGUN mensaje en WATCHDOG_TIMEOUT_MS, se asume
        //    conexion muerta (aunque el navegador no haya disparado onclose) y se
        //    fuerza reconexion.
        //  - initial_load REEMPLAZA todo el estado (velas, SMAs, marcadores).
        //  - Marcadores de senales persistentes y sin duplicados.
        //  - heartbeat del backend actualiza el estado de "mercado abierto".
        //
        // Glosario:
        //  - WebSocket: canal bidireccional persistente navegador<->backend.
        //  - backoff: aumentar la espera entre reintentos de reconexion.
        //  - jitter: variacion aleatoria de esa espera.
        //  - watchdog ("perro guardian"): temporizador que vigila que sigan
        //    llegando mensajes; si no, actua.
        // =====================================================================

        // --- Constantes de resiliencia (nombradas, no "numeros magicos") ---
        // Si no llega ningun mensaje del backend en este tiempo (ms), se considera
        // la conexion caida y se reconecta. 45s > 2 heartbeats (15s c/u) de margen.
        var WATCHDOG_TIMEOUT_MS = 45000;
        // Escalera de esperas de reconexion (ms). El intento N usa el paso
        // min(N, ultimo indice); a partir de ahi se mantiene en 30s (reintenta por
        // siempre, nunca se rinde).
        var BACKOFF_STEPS_MS = [1000, 2000, 4000, 8000, 16000, 30000];
        // Fraccion de jitter: la espera real se multiplica por (1 ± BACKOFF_JITTER).
        var BACKOFF_JITTER = 0.2;

        // --- State ---
        var markers = [];               // marcadores BUY/SELL actuales (ordenados por tiempo)
        var seenSignals = {};           // registro de senales ya dibujadas: clave "time|signal_type"
        var chart = null;
        var candlestickSeries = null;
        var smaFastSeries = null;
        var smaSlowSeries = null;
        var ws = null;                  // UNICO socket activo
        var reconnectAttempts = 0;      // contador de reintentos; se resetea SOLO en initial_load
        var reconnectTimer = null;      // UNICO timer de reconexion pendiente
        var watchdogTimer = null;       // UNICO watchdog activo
        var lastCandleTime = null;      // tiempo (epoch s) de la ultima vela conocida
        var marketOpen = null;          // bandera de horario regular del mercado (del backend)
        var dataLoaded = false;

        // --- A4.2: temporalidad visual (1m / 5m) ---
        // La matematica (velas, SMA y senales) es SIEMPRE de 1 minuto y la calcula el backend.
        // La vista de 5 minutos solo AGRUPA en el navegador las velas de 1 minuto:
        //   O = open del primer minuto, H = maximo, L = minimo, C = close del ultimo minuto,
        //   V = suma; bloques UTC de 300 s (:00, :05, :10 ...). La vela en curso se actualiza.
        // En 5m las SMA son las del motor de 1m (valor del ultimo minuto de cada bloque) y se
        // rotulan igual que en 1m: "SMA 9" / "SMA 21" (decision de Hugo, 26-oct). Cada senal se
        // dibuja sobre el bloque que la contiene y no se pierde ninguna (varias senales en un mismo
        // bloque quedan todas visibles).
        var TF_SECONDS = { '1m': 60, '5m': 300 };
        var TF_STORAGE_KEY = 'tradecore.timeframe';
        var timeframe = '5m';  // por defecto 5m (pedido de Felipe); se recuerda la eleccion del usuario
        // Verdad de 1 minuto recibida del backend (la vista se reconstruye desde aqui).
        var raw = { candles: [], smaFast: [], smaSlow: [], signals: [] };
        var MODE_LABELS = { simulation: 'Simulación', live: 'Live', replay: 'Replay' };

        // --- DOM refs ---
        var loadingOverlay = document.getElementById('loading-overlay');
        var connectionDot = document.getElementById('connection-dot');
        var connectionText = document.getElementById('connection-text');
        var modeBadge = document.getElementById('mode-badge');
        var lastTimestampEl = document.getElementById('last-timestamp');
        var chartContainer = document.getElementById('chart-container');

        // --- Chart Initialization ---
        function initChart() {
            chart = LightweightCharts.createChart(chartContainer, {
                width: chartContainer.clientWidth,
                height: chartContainer.clientHeight,
                layout: {
                    background: { type: 'solid', color: '#1a1a2e' },
                    textColor: '#d1d4dc',
                },
                grid: {
                    vertLines: { color: '#1e2a3a' },
                    horzLines: { color: '#1e2a3a' },
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                },
                rightPriceScale: {
                    borderColor: '#2a2e39',
                },
                timeScale: {
                    borderColor: '#2a2e39',
                    timeVisible: true,
                    secondsVisible: false,
                },
            });

            candlestickSeries = chart.addCandlestickSeries({
                upColor: '#26a69a',
                downColor: '#ef5350',
                borderDownColor: '#ef5350',
                borderUpColor: '#26a69a',
                wickDownColor: '#ef5350',
                wickUpColor: '#26a69a',
            });

            smaFastSeries = chart.addLineSeries({
                color: '#2196F3',
                lineWidth: 2,
                title: 'SMA 9',
            });

            smaSlowSeries = chart.addLineSeries({
                color: '#FF9800',
                lineWidth: 2,
                title: 'SMA 21',
            });

            // Responsive resize
            window.addEventListener('resize', function () {
                if (chart) {
                    chart.applyOptions({
                        width: chartContainer.clientWidth,
                        height: chartContainer.clientHeight,
                    });
                }
            });
        }

        // --- A4.2: agrupacion por temporalidad ---
        function bucketOf(t) {
            return t - (t % TF_SECONDS[timeframe]);
        }

        // Agrega al final o reemplaza el ultimo punto con el mismo tiempo (los handlers ya
        // descartan los puntos mas viejos que la ultima vela).
        function upsertLast(arr, item) {
            if (arr.length > 0 && arr[arr.length - 1].time === item.time) {
                arr[arr.length - 1] = item;
            } else {
                arr.push(item);
            }
        }

        function aggregateCandles(candles) {
            var out = [];
            for (var i = 0; i < candles.length; i++) {
                var c = candles[i];
                var b = bucketOf(c.time);
                var last = out.length > 0 ? out[out.length - 1] : null;
                if (last && last.time === b) {
                    last.high = Math.max(last.high, c.high);
                    last.low = Math.min(last.low, c.low);
                    last.close = c.close;
                    last.volume = last.volume + (c.volume || 0);
                } else {
                    out.push({ time: b, open: c.open, high: c.high, low: c.low,
                               close: c.close, volume: c.volume || 0 });
                }
            }
            return out;
        }

        // Vela agrupada del bloque b (solo los minutos de ese bloque, que estan al final).
        function bucketCandleAt(b) {
            var i0 = raw.candles.length;
            while (i0 > 0 && raw.candles[i0 - 1].time >= b) { i0--; }
            return aggregateCandles(raw.candles.slice(i0))[0];
        }

        function aggregateLine(points) {
            var out = [];
            for (var i = 0; i < points.length; i++) {
                upsertLast(out, { time: bucketOf(points[i].time), value: points[i].value });
            }
            return out;
        }

        function markerForView(sig) {
            var m = buildMarker(sig);
            if (m) { m.time = bucketOf(sig.time); }
            return m;
        }

        // Reconstruye TODA la vista (velas, SMA, marcadores, rotulos) desde la verdad de 1m.
        function render() {
            candlestickSeries.setData(aggregateCandles(raw.candles));
            smaFastSeries.setData(aggregateLine(raw.smaFast));
            smaSlowSeries.setData(aggregateLine(raw.smaSlow));
            markers = [];
            for (var i = 0; i < raw.signals.length; i++) {
                var m = markerForView(raw.signals[i]);
                if (m) { markers.push(m); }
            }
            markers.sort(function (a, b) { return a.time - b.time; });
            candlestickSeries.setMarkers(markers);
        }

        function markTimeframeButtons() {
            var btns = document.querySelectorAll('#timeframe-selector .tf-btn');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].getAttribute('data-tf') === timeframe) {
                    btns[i].classList.add('active');
                } else {
                    btns[i].classList.remove('active');
                }
            }
        }

        function setTimeframe(tf) {
            if (!TF_SECONDS.hasOwnProperty(tf) || tf === timeframe) { return; }
            timeframe = tf;
            try { window.localStorage.setItem(TF_STORAGE_KEY, tf); } catch (e) { /* sin almacenamiento */ }
            markTimeframeButtons();
            render();
            chart.timeScale().fitContent();
        }

        function initTimeframe() {
            var saved = null;
            try { saved = window.localStorage.getItem(TF_STORAGE_KEY); } catch (e) { /* sin almacenamiento */ }
            if (saved && TF_SECONDS.hasOwnProperty(saved)) { timeframe = saved; }
            var btns = document.querySelectorAll('#timeframe-selector .tf-btn');
            for (var i = 0; i < btns.length; i++) {
                btns[i].addEventListener('click', function () {
                    setTimeframe(this.getAttribute('data-tf'));
                });
            }
            markTimeframeButtons();
            render();
        }

        // --- WebSocket ---

        // Desarma por completo un socket viejo antes de abrir uno nuevo: le quita los
        // handlers (para que un onclose tardio NO dispare otra reconexion) y lo cierra.
        // Asi garantizamos que solo exista UN socket activo en todo momento.
        function teardownSocket() {
            clearWatchdog();
            if (ws) {
                ws.onopen = null;
                ws.onmessage = null;
                ws.onclose = null;
                ws.onerror = null;
                try { ws.close(); } catch (e) { /* ignore */ }
                ws = null;
            }
        }

        function connectWebSocket() {
            // Cancela cualquier reconexion pendiente: solo un intento en vuelo.
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            // Cierra y limpia el socket anterior antes de crear el nuevo.
            teardownSocket();

            var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            var wsUrl = protocol + '//' + window.location.host + '/ws/chart';

            ws = new WebSocket(wsUrl);
            var thisWs = ws;  // captura para ignorar eventos de sockets ya reemplazados
            resetWatchdog();  // vigila desde la creacion: si nunca llega un mensaje, reconecta

            ws.onopen = function () {
                // NO reseteamos reconnectAttempts aqui: abrir el socket no garantiza
                // que el backend este sano. El contador se resetea solo al recibir un
                // initial_load valido (senal de servicio realmente operativo).
                refreshStatus();
            };

            ws.onmessage = function (event) {
                if (thisWs !== ws) { return; }  // evento de un socket viejo: ignorar
                // Cualquier mensaje recibido = conexion viva: reinicia el watchdog.
                resetWatchdog();

                var parsed;
                try {
                    parsed = JSON.parse(event.data);
                } catch (e) {
                    console.error('Failed to parse WebSocket message:', e);
                    return;
                }

                // Puede ser un objeto (initial_load / heartbeat) o un array (batch).
                if (Array.isArray(parsed)) {
                    handleBatch(parsed);
                } else if (parsed && parsed.type) {
                    handleMessage(parsed);
                }
            };

            ws.onclose = function () {
                if (thisWs !== ws) { return; }  // cierre de un socket viejo: ignorar
                clearWatchdog();
                refreshStatus();
                scheduleReconnect();
            };

            ws.onerror = function () {
                if (thisWs !== ws) { return; }
                refreshStatus();
                // onerror suele ir seguido de onclose; dejamos que onclose reconecte.
            };
        }

        // Programa UNA reconexion con backoff + jitter. Reintenta por siempre (sin tope).
        function scheduleReconnect() {
            if (reconnectTimer) { return; }  // ya hay una reconexion programada

            // Paso de backoff: usa el indice min(intento, ultimo) -> se estabiliza en 30s.
            var idx = Math.min(reconnectAttempts, BACKOFF_STEPS_MS.length - 1);
            var base = BACKOFF_STEPS_MS[idx];
            // Jitter: multiplica por (1 ± BACKOFF_JITTER) con ruido uniforme.
            var factor = 1 + ((Math.random() * 2 - 1) * BACKOFF_JITTER);
            var delay = Math.round(base * factor);

            reconnectAttempts++;
            refreshStatus();  // mostrara "Reconectando…" (socket no abierto)

            reconnectTimer = setTimeout(function () {
                reconnectTimer = null;
                connectWebSocket();
            }, delay);
        }

        // --- Watchdog: vigila que sigan llegando mensajes ---
        // Unico punto que cancela el watchdog (evita timers huerfanos de sockets viejos).
        function clearWatchdog() {
            if (watchdogTimer !== null) {
                clearTimeout(watchdogTimer);
                watchdogTimer = null;
            }
        }

        function resetWatchdog() {
            clearWatchdog();
            watchdogTimer = setTimeout(function () {
                // Silencio prolongado: trata la conexion como muerta y reconecta.
                // teardownSocket() quita handlers, por eso el onclose NO disparara otra
                // reconexion; la forzamos nosotros aqui de forma controlada.
                teardownSocket();
                refreshStatus();
                scheduleReconnect();
            }, WATCHDOG_TIMEOUT_MS);
        }

        // --- Message Handlers ---
        function handleBatch(messages) {
            for (var i = 0; i < messages.length; i++) {
                handleMessage(messages[i]);
            }
        }

        function handleMessage(msg) {
            switch (msg.type) {
                case 'initial_load':
                    handleInitialLoad(msg.data);
                    break;
                case 'candle':
                    handleCandle(msg.data);
                    break;
                case 'signal':
                    handleSignal(msg.data);
                    break;
                case 'sma':
                    handleSma(msg.data);
                    break;
                case 'status':
                    handleStatus(msg.data);
                    break;
                case 'heartbeat':
                    handleHeartbeat(msg.data);
                    break;
                default:
                    // Tipo desconocido: ignorar (robustez ante futuros mensajes).
                    console.warn('Unknown message type:', msg.type);
            }
        }

        // Clave unica de una senal para deduplicar marcadores: "<time>|<signal_type>".
        function signalKey(data) {
            return String(data.time) + '|' + String(data.signal_type);
        }

        // Construye el objeto marcador de lightweight-charts a partir de una senal.
        // Devuelve null si el tipo no es BUY/SELL.
        function buildMarker(data) {
            if (data.signal_type === 'BUY') {
                return {
                    time: data.time,
                    position: 'belowBar',
                    color: '#26a69a',
                    shape: 'arrowUp',
                    text: 'BUY',
                };
            }
            if (data.signal_type === 'SELL') {
                return {
                    time: data.time,
                    position: 'aboveBar',
                    color: '#ef5350',
                    shape: 'arrowDown',
                    text: 'SELL',
                };
            }
            return null;
        }

        function handleInitialLoad(data) {
            if (!data) return;

            // initial_load REEMPLAZA todo el estado. Un cliente que (re)conecta parte
            // de cero con lo que el backend considera la verdad actual de la sesion.

            // Guarda la verdad de 1m (velas, SMA y senales deduplicadas por clave) y
            // reconstruye la vista en la temporalidad elegida (A4.2).
            raw.candles = (data.candles || []).slice();
            raw.smaFast = (data.sma_fast_series || []).slice();
            raw.smaSlow = (data.sma_slow_series || []).slice();
            raw.signals = [];
            seenSignals = {};
            var sigs = data.signals || [];
            for (var i = 0; i < sigs.length; i++) {
                if (buildMarker(sigs[i])) {
                    var key = signalKey(sigs[i]);
                    if (!seenSignals[key]) {
                        seenSignals[key] = true;
                        raw.signals.push(sigs[i]);
                    }
                }
            }
            raw.signals.sort(function (a, b) { return a.time - b.time; });
            render();

            // Recuerda el tiempo de la ultima vela y la bandera de mercado abierto.
            if (data.candles && data.candles.length > 0) {
                lastCandleTime = data.candles[data.candles.length - 1].time;
            } else {
                lastCandleTime = null;
            }
            if (data.market_open !== undefined) {
                marketOpen = data.market_open;
            }

            chart.timeScale().fitContent();

            // El contador de reconexion se resetea SOLO aqui: initial_load prueba que
            // el servicio esta realmente operativo (no solo que el socket abrio).
            reconnectAttempts = 0;

            // Oculta SIEMPRE el overlay de carga, aunque no haya velas (asi el usuario
            // no se queda viendo "Cargando datos historicos..." en una sesion vacia).
            dataLoaded = true;
            loadingOverlay.classList.add('hidden');

            refreshStatus();
        }

        function handleCandle(data) {
            if (!data) return;
            // Ignora velas mas viejas que la ultima conocida (evita retrocesos). Una
            // vela con el MISMO tiempo se trata como actualizacion de esa vela.
            if (lastCandleTime !== null && data.time < lastCandleTime) { return; }
            upsertLast(raw.candles, data);
            if (timeframe === '1m') {
                candlestickSeries.update(data);
            } else {
                candlestickSeries.update(bucketCandleAt(bucketOf(data.time)));
            }
            lastCandleTime = data.time;
            refreshStatus();
        }

        function handleSignal(data) {
            if (!data) return;

            var marker = markerForView(data);
            if (!marker) { return; }

            // Dedup por clave "time|signal_type": no dibujar dos veces la misma senal.
            var key = signalKey(data);
            if (seenSignals[key]) { return; }
            seenSignals[key] = true;
            raw.signals.push(data);

            // Inserta y mantiene orden ascendente por tiempo antes de setMarkers.
            markers.push(marker);
            markers.sort(function (a, b) { return a.time - b.time; });
            candlestickSeries.setMarkers(markers);
        }

        function handleSma(data) {
            if (!data) return;
            // Mismo criterio temporal que las velas: ignora puntos SMA mas viejos que
            // la ultima vela conocida; time == actualiza el punto existente.
            if (lastCandleTime !== null && data.time < lastCandleTime) { return; }
            if (data.sma_fast !== undefined) {
                upsertLast(raw.smaFast, { time: data.time, value: data.sma_fast });
                smaFastSeries.update({ time: bucketOf(data.time), value: data.sma_fast });
            }
            if (data.sma_slow !== undefined) {
                upsertLast(raw.smaSlow, { time: data.time, value: data.sma_slow });
                smaSlowSeries.update({ time: bucketOf(data.time), value: data.sma_slow });
            }
        }

        function handleStatus(data) {
            if (!data) return;

            // Mode badge: mapeo explicito (A4.2). replay ya no se muestra como "Live".
            if (data.mode) {
                modeBadge.textContent = MODE_LABELS[data.mode] || String(data.mode);
                modeBadge.className = '';
                if (MODE_LABELS.hasOwnProperty(data.mode)) { modeBadge.classList.add(data.mode); }
            }

            // Ultimo timestamp — guarda contra null (buffer vacio) y strings no
            // parseables (que mostrarian "Invalid Date").
            if (data.last_candle_time) {
                var dt = new Date(data.last_candle_time);
                if (!isNaN(dt.getTime())) {
                    // En UTC, igual que el eje del grafico (A4.2).
                    lastTimestampEl.textContent = 'Último: ' + dt.toISOString().slice(11, 16) + ' UTC';
                } else {
                    lastTimestampEl.textContent = 'Último: —';
                }
            } else {
                lastTimestampEl.textContent = 'Último: —';
            }

            // El indicador de conexion NO se decide aqui: lo gobierna refreshStatus()
            // segun la prioridad (reconectando > mercado cerrado > conectado).
            refreshStatus();
        }

        function handleHeartbeat(data) {
            if (!data) return;
            // El latido NO dibuja nada: solo actualiza la bandera de mercado abierto
            // (y, al llegar, ya reinicio el watchdog en onmessage).
            if (data.market_open !== undefined) {
                marketOpen = data.market_open;
                refreshStatus();
            }
        }

        // --- UI Helpers ---

        // Deriva el indicador de conexion por PRIORIDAD, a partir del estado real:
        //   1) "Reconectando…"  si el socket NO esta abierto (prioridad maxima).
        //   2) "Mercado cerrado" si esta conectado pero marketOpen === false
        //      (las velas viejas se quedan en pantalla; no se borra el grafico).
        //   3) "Conectado"      si esta conectado y el mercado esta abierto (o sin dato).
        function refreshStatus() {
            var isOpen = (ws && ws.readyState === WebSocket.OPEN);

            connectionDot.className = '';

            if (!isOpen) {
                connectionDot.classList.add('reconnecting');
                connectionText.textContent = 'Reconectando…';
                return;
            }

            if (marketOpen === false) {
                connectionDot.classList.add('connected');
                connectionText.textContent = 'Mercado cerrado';
                return;
            }

            connectionDot.classList.add('connected');
            connectionText.textContent = 'Conectado';
        }

        // --- Bootstrap ---
        initChart();
        initTimeframe();
        refreshStatus();      // estado inicial coherente antes de conectar
        connectWebSocket();
    })();
