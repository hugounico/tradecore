# TradeCore Live Visual v1 — Design

> Estado del documento: BORRADOR EN REVISION (Design-only). Este turno NO genera Tasks,
> NO implementa codigo, NO ejecuta tests de implementacion, Validation ni Final OOS.
> Rama de trabajo: `feature/live-dashboard-demo` (base `98e3e9fc2da773410dad6f1cc5e491de52e2e211`).
> `DEPLOYED_COMMIT = UNKNOWN` (verificacion de AWS ECS es una linea externa; no se infiere aqui).

Glosario breve (para lectura no experta):
- **Live**: datos de mercado reales en tiempo (casi) real desde Databento.
- **Simulation**: reproduccion de velas historicas ya cerradas (Fase A), NO es mercado real.
- **OHLCV**: Open/High/Low/Close/Volume de una vela.
- **SMA (Simple Moving Average)**: promedio movil simple de los ultimos N cierres.
- **WebSocket**: canal bidireccional persistente (aqui, backend <-> navegador).
- **UTC**: zona horaria universal; estandar interno unico de tiempos.
- **signal_id**: identificador unico de una senal; clave transversal de las 3 capas.
- **[SEGURO — REQUIERE CONFIRMACION]**: valor/decision que NO se inventa; requiere confirmacion del usuario.
- **[REQUIERE APROBACION — COMPONENTE PROTEGIDO]**: cambio sobre un archivo protegido de Fase A.

---

## 0. Rama y punto de partida

- Rama de trabajo: `feature/live-dashboard-demo`.
- Base inicial verificada: `98e3e9fc2da773410dad6f1cc5e491de52e2e211` (checkpoint `CALIBRATION_GIT_CONSOLIDATION = COMPLETE`).
- `research/math-validation` permanece en `98e3e9f...` y NO se modifica en esta linea de trabajo.
- Unicas mutaciones autorizadas este turno: checkout de la rama + creacion/edicion de este Design. Sin commit, sin push.

### Estructura de Spec (metodologia Kiro)
- Path del Design: `.kiro/specs/tradecore-live-visual-v1/design.md`.
- Esta Spec seguira, tras la aprobacion del Design: `requirements.md` (si se decide requirements-first o design-first) y luego `tasks.md`.
- En este turno se genera EXCLUSIVAMENTE `design.md`. `requirements.md` y `tasks.md` quedan pendientes.

### Clasificacion transversal de componentes

**Arquitectura existente reutilizada (Fase A, protegida — se consume sin alterar su comportamiento):**
- `src/engine/signal_engine.py` (SMA 9/21) — motor de senales, SIN cambios.
- `src/pipeline/candle_buffer.py` (`CandleBuffer`) — buffer en memoria, dedup por timestamp, cap 500.
- `src/pipeline/simulation_replay.py` (`SimulationReplay`) — fuente de simulacion.
- `src/schemas/candle.py` (`Candle`), `src/schemas/signal.py` (`Signal`) — modelos de datos.
- `src/api/app.py` — orquestacion FastAPI + WebSocket `/ws/chart` + `_processing_loop`.
- `src/api/throttled_pusher.py` (`ThrottledPusher`) — throttle 1/seg hacia el navegador.
- `dashboard/index.html` — UI (velas, SMA, marcadores, estado).
- `src/connectors/databento_connector.py` — carga Historical (Fase A) intacta.

**Componentes nuevos (esta Spec):**
- `DabentoConnector.subscribe_live()` + `is_connected` + `reconnect()` (Fase B Live, aislados de la carga historica).
- Mecanismo de deteccion y recuperacion de gaps (CONNECTED vs SYNCED).
- Fuente intercambiable de velas hacia `_processing_loop` (adaptador comun Simulation/Live).
- `LiveSession` (identidad de sesion) + `live_session_id` + provenance.
- Persistencia de las 3 capas (Capa 1 prioritaria) con un `JournalWriter` persistente.
- Nuevos estados de conexion/sync en el protocolo WebSocket + UI minima.

**Componentes protegidos que requeririan autorizacion (ver seccion 21):**
- `src/api/app.py`, `src/api/throttled_pusher.py`, `dashboard/index.html`,
  `src/connectors/databento_connector.py` (solo adicion Live aislada).

**Decisiones cerradas:** dataset GLBX.MDP3, schema ohlcv-1m, simbolo NQ.c.0 (continuous),
SMA 9/21 sin cambios, UTC interno, throttle 1/seg, Live y Simulation nunca se mezclan.

**Decisiones pendientes:** ver seccion R (marcadas [SEGURO — REQUIERE CONFIRMACION]).

**Fuera de alcance:** ver seccion 26 (Validation, Final OOS, ML, VolumeFilter/RiskGate operativos, etc.).

---

## 1. Alcance del documento

Design formal de TradeCore Live Visual v1: llevar el sistema de simulacion a un flujo
`Databento Live -> velas NQ 1m -> SMA 9/21 -> BUY/SELL -> WebSocket -> dashboard live ->
registro historico de senales (Capa 1)`, con separacion estricta Live/Simulation,
recuperacion de gaps, provenance y preparacion (diseno) de las 3 capas de registro.

---

## 2. Principio fundamental: Live y Simulation NUNCA se mezclan

Se definen dos modos conceptualmente separados y mutuamente excluyentes por sesion:

- **SIMULATION**: fuente = `SimulationReplay` (velas historicas cerradas). NO es mercado real.
- **LIVE**: fuente = `DabentoConnector.subscribe_live()` (mercado real, ohlcv-1m).

**Principio arquitectonico (obligatorio):** una sesion LIVE NUNCA hace fallback automatico
a Simulation. Ante perdida de conexion con Databento, la transicion permitida es:

```
LIVE  ->  Databento disconnected  ->  RECONNECTING / DATA STALE  ->  NO NEW SIGNALS
```

Prohibido:

```
LIVE  ->  Databento disconnected  ->  SimulationReplay        (PROHIBIDO)
```

Razon: mezclar datos historicos/simulados con mercado real contaminaria (a) la evaluacion
visual del socio y (b) el futuro dataset de las 3 capas. El `market_mode` de cada sesion es
inmutable: una sesion nace LIVE o SIMULATION y no cambia. Un cambio de modo exige una sesion
nueva con `live_session_id` distinto.

Consecuencia de diseno: durante RECONNECTING/STALE el dashboard sigue mostrando la ultima
vela valida pero NO se generan senales nuevas y el estado de market data no es SYNCED.

---

## 3. Contrato Databento Live — `DabentoConnector.subscribe_live()`

Diseno formal (NO implementacion). Coherente con la carga historica aprobada y con
`.kiro/steering/databento-architecture.md`.

- **Parametros de mercado (cerrados):** dataset `GLBX.MDP3`; schema `ohlcv-1m`; symbol `NQ.c.0`;
  `stype_in` compatible con continuous symbology (mismo criterio que Historical Fase A).
- **Unidad de procesamiento:** una vela de 1 minuto ya cerrada (ver seccion 7).

Ciclo de vida diseñado:
1. **Inicializacion:** crear cliente Live del SDK oficial (`databento.Live`) usando
   `DATABENTO_API_KEY` desde variable de entorno (NUNCA hardcodeada). Config de
   `reconnect_policy`/`heartbeat_interval_s` del SDK -> RESUELTO en seccion 4.2 (G0.2): `ReconnectPolicy.NONE` + `heartbeat_interval_s=None`
   (no se inventan; la doc oficial prevalece para capacidades del proveedor).
2. **Suscripcion:** `subscribe(dataset, schema="ohlcv-1m", stype_in, symbols=["NQ.c.0"])`.
3. **Recepcion:** iterar de forma asincrona los mensajes OHLCV; cada mensaje = una vela cerrada.
4. **Transformacion:** convertir el mensaje al `Candle` existente reutilizando la misma
   conversion de precios que Historical (`_convert_price`, int64 x 1e-9 -> float) y
   `ts_event` (ns) -> datetime UTC. NO se crea un modelo nuevo de vela.
5. **Cierre normal:** `client.stop()` (graceful) o `client.terminate()` (inmediato) segun el SDK.
6. **Errores:** distinguir fatales (AUTH_FAILED, API_KEY_DEACTIVATED -> NO reintentar, error
   visible) de no fatales (SYMBOL_RESOLUTION_FAILED, cortes de red -> reconexion, ver seccion 4).
7. **Estado de conexion:** exponer `is_connected` (ver seccion 4) y alimentar los estados de la
   seccion 8. La conexion Databento NO es un WebSocket (es TCP/DBN del SDK); nunca se trata como tal.

Regla de aislamiento: `subscribe_live()` se añade SIN tocar `load_historical()` /
`_fetch_historical_sync()`; la ruta historica de Fase A permanece byte-identica.

---

## 4. Reconexion y backoff

Diseño (NO implementacion):
- `is_connected: bool` — refleja si el cliente Live tiene conexion activa con Databento.
- `reconnect()` — reintenta la conexion Live con **exponential backoff**.
- **Backoff:** secuencia de reintentos con espera creciente. Valores concretos
  (numero de intentos, delays, `max_delay`) -> [SEGURO — REQUIERE CONFIRMACION].
  Nota de consistencia: `databento-architecture.md` menciona para el MVP "3 intentos con
  backoff 1s/2s/4s"; se propone reutilizar ese criterio, pero se deja como
  [SEGURO — REQUIERE CONFIRMACION] hasta que el usuario lo confirme para Live v1.
- **Reset del backoff:** tras una conexion estable durante un intervalo de estabilidad,
  el contador de backoff vuelve a su valor inicial. El mecanismo de reset ya esta
  implementado (T1.4, `record_stable_connection()`), pero el intervalo temporal concreto
  todavia NO esta definido: `stability_window_s = None`. Ver seccion 4.4
  ([DEUDA — RECONNECT_STABILITY_RESET_INTERVAL = OPEN]). Esto NO reabre G0.1: la politica
  de reconnect (3 intentos, delays 1s/2s/4s, fatal sin retry) sigue `G0.1 = RESOLVED`.
- **Errores permanentes** (AUTH_FAILED / API_KEY_DEACTIVATED): NO reintentar; marcar error
  fatal, detener la sesion Live y comunicarlo (estado + logging), sin fallback a Simulation.
- **Logging:** cada intento, exito/fallo, tipo de error y transiciones de estado se registran
  con logging estandar (sin secretos; nunca la API key).

Importante: `reconnect()` restablece la CONEXION, pero NO implica SYNCED. Tras reconectar
debe resolverse el gap (seccion 5) antes de volver a generar senales.

### 4.1 G0.1 — RECONNECT POLICY LIVE — RESUELTO

**[VERIFICADO — DATABENTO SDK 0.86.0, CODIGO FUENTE LOCAL]**
La version instalada `databento==0.86.0` fue inspeccionada localmente. Se verifico que:
- `ReconnectPolicy.RECONNECT` reintenta indefinidamente;
- no implementa backoff/delay entre nuevos intentos;
- no tiene limite de intentos;
- su logica de reconnect no distingue por si misma errores fatales de recuperables;
- un `ErrorMsg` recibido desde el gateway se entrega integro al callback publico `Live.add_callback`;
- ese `ErrorMsg` conserva sus campos estructurados `code` y `err`;
- para el camino en que se recibe ErrorMsg, el callback publico lo observa antes del manejo local de
  `connection_lost` y antes de cualquier intento de reconnect.

Resultado de la verificacion previa: FATAL_ERROR_OBSERVABILITY = CASE_A; FATAL_RECONNECT_CONTROL = CONTROL_A.

**[VERIFICADO — DOCUMENTACION OFICIAL DATABENTO]**
Ante una desconexion sin error, Databento instruye esperar aproximadamente 1 segundo antes de iniciar
una nueva conexion. Reconectar demasiado rapido puede activar el rate limiter del gateway.
Los siguientes codigos `ErrorMsg` estan documentados actualmente como fatales:
AUTH_FAILED, API_KEY_DEACTIVATED, CONNECTION_LIMIT_EXCEEDED, INVALID_SUBSCRIPTION, INTERNAL_ERROR,
REPLAY_DATA_AGED_OUT. Los errores fatales provocan el cierre de la sesion. No confundirlos con errores
no fatales/informativos, como SYMBOL_RESOLUTION_FAILED o SKIPPED_RECORDS_AFTER_SLOW_READING.

**[DECISION TRADECORE — MVP]**
TradeCore NO utilizara el loop automatico `ReconnectPolicy.RECONNECT` para gobernar las reconexiones
del MVP. Se utilizara `reconnect_policy = ReconnectPolicy.NONE`. TradeCore sera propietario de la
politica de reconnect sobre el `ConnectionStateMachine` construido en T1.3.

Politica para una desconexion recuperable:
`CONNECTED -> RECONNECTING -> esperar 1 s -> intento 1`; si falla `-> esperar 2 s -> intento 2`; si
falla `-> esperar 4 s -> intento 3`; si falla nuevamente `-> DISCONNECTED`.

Por tanto: MAX_RECONNECT_ATTEMPTS = 3; RECONNECT_DELAYS_SECONDS = [1, 2, 4]. Los valores (3 intentos;
delays 1s/2s/4s) son decisiones de arquitectura de TradeCore para el MVP; NO son una politica exigida
por Databento. El primer delay de 1 segundo es consistente con la instruccion documentada de Databento
de esperar antes de abrir una nueva conexion.

**[ERROR FATAL]** Si TradeCore recibe mediante la API publica `Live.add_callback` un `ErrorMsg` cuyo
codigo pertenece a la lista documentada como fatal: `-> DISCONNECTED`, 0 reintentos. La sesion debe
cerrarse limpiamente cuando corresponda. NO entrar al loop 1s/2s/4s despues de un ErrorMsg fatal.

**[RECUPERACION DE GAP]** La recuperacion de datos perdidos durante la desconexion (GAP_RECOVERY) queda
explicitamente FUERA DE T1.4. Se resolvera en la Wave/tarea de sincronizacion correspondiente. T1.4 no
debe implementar replay, recuperacion de records perdidos ni sincronizacion historica como consecuencia
de esta decision.

**[RE-SUSCRIPCION]** Al utilizar `ReconnectPolicy.NONE`, TradeCore no dependera de la auto-resuscripcion
interna que el SDK ejecuta bajo su reconnect automatico. La responsabilidad arquitectonica de
restablecer las subscriptions despues de crear una nueva sesion pertenece a TradeCore. IMPORTANTE: esto
NO significa que T1.4 deba implementar re-suscripcion si su criterio de aceptacion actual no la incluye;
la implementacion concreta debe permanecer en la primera Task/Wave propietaria de subscription/
re-subscription segun el DAG vigente. NO ampliar T1.4 para incluirla por inferencia.

**G0.1 = RESOLVED**

### 4.2 G0.2 — DATABENTO SDK LIVE CONFIG — RESUELTO

**[DECISION TRADECORE]** Para el MVP: `reconnect_policy = ReconnectPolicy.NONE`; `heartbeat_interval_s = None`.

**[VERIFICADO — SDK LOCAL]** Con `heartbeat_interval_s=None`, el SDK 0.86.0 utiliza internamente un
intervalo efectivo de aproximadamente 30 segundos. La deteccion local de conexion colgada utiliza ese
intervalo mas un margen de 10 segundos sin mensajes. Por tanto: heartbeat interval efectivo ~= 30 s;
condicion de hung connection ~= heartbeat_interval + 10 s. NO describir esto como un unico "heartbeat de
40 segundos". No existe en esta etapa una necesidad verificada que justifique configurar un heartbeat
distinto.

**G0.2 = RESOLVED**

### 4.3 DEUDA — SDK VERSION PINNING

**[DEUDA — SDK_VERSION_PINNING_DEBT = OPEN]** `requirements.txt` declara actualmente `databento>=0.39.0`.
La arquitectura anterior fue verificada especificamente contra `databento==0.86.0`. Una instalacion
diferente que cumpla `>=0.39.0` podria presentar comportamiento distinto. Esta deuda NO bloquea
G0.1/G0.2 ni T1.4. NO modificar requirements.txt en este turno. Debe quedar registrada para revision en
el checkpoint de consolidacion. SDK_VERSION_PINNING_DEBT = OPEN.

### 4.4 DEUDA — RECONNECT STABILITY RESET INTERVAL

**[DEUDA — RECONNECT_STABILITY_RESET_INTERVAL = OPEN]**

El mecanismo de reset de backoff esta implementado en T1.4 mediante una operacion
equivalente a `record_stable_connection()`. Sin embargo, todavia NO existe un intervalo
temporal aprobado que defina cuanto tiempo debe permanecer estable una conexion antes de
considerar reseteada la politica de backoff.

La configuracion actual mantiene: `stability_window_s = None`. Esto es intencional. NO
inventar valores como 30s, 60s u otro intervalo sin evidencia o decision posterior.

Esta deuda:
- NO invalida T1.4;
- NO reabre G0.1 (que permanece `G0.1 = RESOLVED`, seccion 4.1);
- NO bloquea el commit de consolidacion;
- NO bloquea continuar con las siguientes Tasks;
- debe resolverse antes de la integracion Live que necesite aplicar automaticamente el
  reset temporal.

Este intervalo es un concepto DISTINTO de la ventana de estabilidad Live para READY
(seccion 23 / READY_STABILITY_WINDOW): aquel define cuando resetear el backoff de
reconnect; la ventana READY define cuanta estabilidad demostrar antes de habilitar Trader
Review. NO fusionar ambos.

RECONNECT_STABILITY_RESET_INTERVAL = OPEN

---

## 5. Gap tras una desconexion (deteccion y recuperacion)

Problema (ejemplo): velas 12:30 y 12:31 recibidas; 12:32 desconexion; 12:36 reconexion.
Reanudar escuchando solo desde 12:36 dejaria un hueco 12:32–12:35 y un estado SMA
potencialmente inconsistente.

**Regla obligatoria:** no generar senales con un estado SMA potencialmente inconsistente por
un gap no resuelto. Se distingue CONNECTED (hay socket) de SYNCED (sin gaps, apto para senales).

**Estrategia de recuperacion diseñada (gap por debajo del umbral):**
1. **Congelar SignalEngine** (no evaluar señales) al detectar la desconexion / estado no SYNCED.
2. **Reconectar** (seccion 4).
3. **Identificar `last_completed_live_candle`** (ultima vela cerrada aceptada antes del gap).
4. **Recuperar las velas cerradas faltantes** del intervalo `(last_completed_live_candle, primera_vela_live_post_reconexion)`
   mediante la **Historical API** (mismo dataset/schema/simbolo), si corresponde.
5. **Alimentar esas velas cronologicamente** al MISMO pipeline (`CandleBuffer -> SignalEngine`),
   en orden ascendente por timestamp.
6. **Deduplicar por timestamp** (el `CandleBuffer` ya descarta `<= last_timestamp`; ver seccion 7).
7. **Solo entonces** declarar el stream `LIVE_SYNCED` y reanudar la generacion de senales.

**Umbral maximo de gap recuperable automaticamente:** `MAX_AUTO_RECOVERABLE_GAP`
= [SEGURO — REQUIERE CONFIRMACION] (minutos u horas; NO se inventa).
- Gap <= umbral: aplicar recuperacion via Historical API (pasos 1–7).
- Gap > umbral (p.ej. varias horas o un fin de semana): NO recuperar automaticamente. Declarar
  la sesion Live **finalizada** (`status = ENDED_GAP_EXCEEDED`) y exigir reinicio manual con
  **nuevo `live_session_id`**. Razon: evita costo/descarga masiva innecesaria y el riesgo de
  reanudar sobre un contexto de mercado sustancialmente distinto.

Nota anti-mezcla: la recuperacion via Historical API de velas CERRADAS reales del mismo
instrumento NO viola la seccion 2 (no es Simulation; son datos reales de mercado del hueco).
Aun asi, esas velas se marcan con su procedencia (seccion 11) para trazabilidad.

---

## 6. Transicion Historical/Simulation -> Live: pipeline compartido

Objetivo: reutilizar `_processing_loop` sin duplicar la logica
`Candle -> CandleBuffer -> SignalEngine -> Signal -> ThrottledPusher`.

Diseño: la FUENTE de velas debe ser intercambiable detras de una interfaz asincrona comun
(un "candle source" que produce `Candle` cerrados en orden):

```
SimulationReplay ─┐
                  ├──► (async iterator de Candle) ──► _processing_loop ──► CandleBuffer
Databento Live  ─┘                                     └► SignalEngine (SMA 9/21) ──► Signal ──► ThrottledPusher ──► WebSocket ──► dashboard
```

- Ambas fuentes exponen el mismo contrato: `async iterator[Candle]` (hoy `SimulationReplay.replay()`
  ya es `AsyncIterator[Candle]`; `subscribe_live()` debe ofrecer la misma forma).
- `_processing_loop` NO se duplica: recibe la fuente activa segun `market_mode` y procesa igual.
- Se evita explicitamente tener dos implementaciones de SMA/senales.

**Cambio minimo requerido en `app.py`** [REQUIERE APROBACION — COMPONENTE PROTEGIDO]:
- En el `lifespan`, seleccionar la fuente segun `Settings.mode` (`simulation` -> `SimulationReplay`;
  `live` -> `connector.subscribe_live()`), asignandola a la MISMA variable que consume
  `_processing_loop`. Sin duplicar el cuerpo del loop. Detalle y riesgo en la seccion 21.

---

## 7. Semantica de vela

Regla formal: el SignalEngine procesa UNICAMENTE velas de 1 minuto CERRADAS.

- Una vela Databento `ohlcv-1m` es, por definicion del schema, una observacion agregada y
  **cerrada** del minuto: apta directamente para SignalEngine (mismo contrato que la simulacion).
- NO se genera BUY/SELL sobre una vela en formacion/incompleta. (El schema ohlcv-1m no emite
  velas parciales; si en el futuro se usara un schema mas granular, habria que agregar cierre
  explicito — fuera de alcance de v1.)
- **Timestamp:** se usa `ts_event` (inicio del minuto) convertido a datetime UTC. UTC es el
  estandar interno unico.
- **Orden cronologico:** las velas se procesan en orden ascendente por timestamp.
- **Deduplicacion:** `CandleBuffer.append` descarta cualquier vela con `timestamp <= last_timestamp`
  (comportamiento existente reutilizado).
- **Vela duplicada:** se descarta silenciosamente (dedup del buffer). No re-evalua señal.
- **Vela fuera de orden** (timestamp anterior a la ultima): se descarta por la misma regla del
  buffer; se registra en logging como anomalia para diagnostico. No reordena el historico ni
  reejecuta el motor.
- **SMA 9/21 NO se altera.**

---

## 8. Estados de conexion

Se diseñan dos ejes de conexion independientes MAS un estado de sincronizacion de datos:

- **A. Browser <-> TradeCore** (ya existe): abierto en `ws.onopen` del navegador; refleja el
  WebSocket interno `/ws/chart`. Valores: `CONNECTED` / `DISCONNECTED` / `RECONNECTING` (navegador).
- **B. TradeCore <-> Databento** (nuevo): refleja `is_connected` del cliente Live. Valores:
  `CONNECTED` / `DISCONNECTED` / `RECONNECTING`.
- **C. Market data (sincronizacion)** (nuevo, conceptualmente distinto de CONNECTED):
  `SYNCED` (sin gaps, apto para senales) / `STALE` (sin datos frescos) /
  `RECONNECTING` (reconectando) / `RESYNCING` (recuperando gap) / `NOT_LIVE` (modo simulacion).

Distincion clave: **CONNECTED != LIVE_SYNCED**. Una conexion recuperada (B=CONNECTED) NO habilita
generacion de senales hasta que C=SYNCED (gap cerrado, seccion 5).

Ejemplo de estado consolidado a comunicar:
```
Browser:      CONNECTED
Databento:    CONNECTED
Market data:  SYNCED
Mode:         LIVE
```

**Comunicacion via WebSocket existente sin romper compatibilidad:** se reutiliza el mensaje
`type: "status"` existente, AÑADIENDO campos opcionales nuevos (p.ej. `databento_connected`,
`market_data_state`, `market_mode`, `live_session_id`) sin eliminar ni renombrar los campos
actuales (`connected`, `last_candle_time`, `mode`). Un dashboard antiguo ignora los campos
nuevos; el dashboard v1 los usa. Detalle del cambio y protecciones en las secciones 20 y 21.
Este es un contrato aditivo (backward-compatible).

---


## 9. Timestamp — correccion minima en `throttled_pusher.py`

[REQUIERE APROBACION — COMPONENTE PROTEGIDO] Archivo: `src/api/throttled_pusher.py`,
funcion `queue_status`.

- **Comportamiento actual:** `"last_candle_time": last_time.isoformat() + "Z"`. Como
  `last_time` es un datetime UTC *aware*, `isoformat()` ya emite el offset `+00:00`, y al
  concatenar `"Z"` produce una cadena ISO invalida: `...+00:00Z`. En el navegador,
  `new Date("...+00:00Z")` da `Invalid Date`.
- **Comportamiento esperado:** emitir un ISO 8601 valido y UTC inequivoco (una sola marca de
  zona). Dos opciones equivalentes (elegir una en Tasks):
  (a) usar `isoformat()` a secas cuando el datetime ya es aware (`...+00:00`); o
  (b) normalizar a sufijo `Z` sin duplicar offset, p.ej. `isoformat().replace("+00:00", "Z")`.
- **Ejemplo before/after:**
  - before: `2025-06-30T23:59:00+00:00Z`  (invalido)
  - after:  `2025-06-30T23:59:00+00:00`   (valido)  o  `2025-06-30T23:59:00Z` (valido)
- **Test que deberia demostrarlo:** dado un `last_time` UTC aware, `queue_status` produce un
  string `s` tal que en JS `!isNaN(new Date(s).getTime())` (equivalente Python: parseable por
  `datetime.fromisoformat` tras normalizar `Z`), y NO contiene el patron `+00:00Z`.
- No ejecutar el cambio en este turno. El guard del dashboard (`isNaN`) ya existe en HEAD y
  seguira como defensa en profundidad.

---

## 10. Identidad de sesion Live — `live_session_id` y entidad `LiveSession`

Cada arranque de una sesion LIVE genera un `live_session_id` unico (p.ej. UUID v4), creado
al iniciar `subscribe_live()`. Proposito: relacionar de forma inequivoca senales,
observaciones, outcomes, inicio/fin de sesion, eventos de desconexion, version de estrategia,
version desplegada y fuente de datos.

Se recomienda una entidad/tabla `LiveSession` (diseño conceptual, sin implementar):

| Campo | Descripcion |
|---|---|
| `live_session_id` | UUID unico de la sesion (PK) |
| `start_utc` | inicio de la sesion (UTC) |
| `end_utc` | fin de la sesion (UTC, nullable mientras esta activa) |
| `data_source` | p.ej. `databento_live` / `databento_historical_recovery` |
| `instrument` | `NQ.c.0` |
| `timeframe` | `1m` |
| `strategy_version` | ver seccion 16 |
| `application_version` | git SHA / version desplegada si esta disponible ([SEGURO — REQUIERE CONFIRMACION] el mecanismo para inyectarlo) |
| `market_mode` | `LIVE` / `SIMULATION` (inmutable por sesion) |
| `status` | `ACTIVE` / `RECONNECTING` / `RESYNCING` / `ENDED_NORMAL` / `ENDED_GAP_EXCEEDED` / `ENDED_FATAL` |

Una sesion SIMULATION tambien puede tener su propio id analogo para trazabilidad, con
`market_mode = SIMULATION`, de modo que jamas se confunda con LIVE.

---

## 11. Provenance / procedencia obligatoria

Toda senal debe poder demostrar su origen. Campos de procedencia (embebidos en Capa 1 y/o
derivables de `LiveSession` via `live_session_id`):

- `market_mode`: `LIVE` vs `SIMULATION` (obligatorio, inmutable).
- `data_source`: `databento_live` (o `databento_historical_recovery` para velas recuperadas de gap).
- `provider`: `databento`.
- `instrument`: `NQ.c.0`.
- `dataset`: `GLBX.MDP3`.
- `schema`: `ohlcv-1m`.
- `timeframe`: `1m`.
- `timezone`: `UTC`.
- `strategy_version` (seccion 16).
- `live_session_id` (seccion 10).

Invariante: NUNCA debe ser posible confundir a posteriori una senal simulada con una real.
`market_mode` es obligatorio en cada registro de Capa 1 y no admite valor por defecto ambiguo.

---

## 12. Registro de TRES CAPAS (clave transversal: `signal_id`)

Las tres capas se diseñan completas ahora. Solo **Capa 1** tiene prioridad de implementacion
inmediata (P1). `signal_id` (UUID v4) es la clave que une las tres.

### CAPA 1 — Signal Context (inmutable; info <= t)
Informacion disponible EXACTAMENTE en el instante `t` de generacion de la senal.

| Campo | Notas |
|---|---|
| `signal_id` | UUID v4 (PK) |
| `live_session_id` | FK -> LiveSession |
| `timestamp_utc` | timestamp de la vela que genero la senal (UTC) |
| `direction` | `BUY` / `SELL` |
| `reference_price` | `close[t]` (= `Signal.price`) |
| `sma_fast` | SMA 9 en t |
| `sma_slow` | SMA 21 en t |
| `volume_at_t` | volumen de la vela en t |
| `atr_at_t` | ATR conocido en t SI se calcula internamente; marcado como research metadata (seccion 17); nullable |
| `instrument` | `NQ.c.0` |
| `timeframe` | `1m` |
| `dataset` | `GLBX.MDP3` |
| `data_source` | `databento_live` / `databento_historical_recovery` |
| `market_mode` | `LIVE` / `SIMULATION` |
| `strategy_version` | seccion 16 (obligatorio) |
| `application_version` | git SHA / version desplegada si disponible |
| `active_config` | config relevante (periodos SMA, etc.), snapshot inmutable |

Regla dura: **Capa 1 = informacion disponible <= t**. Nada posterior a `t` puede modificarla.
Registro inmutable (append-only; sin UPDATE de campos de contexto).

### CAPA 2 — Signal Observations (info > t)
Evolucion posterior a la senal, suficiente para reconstruir su trayectoria y derivar outcomes.

| Campo | Notas |
|---|---|
| `observation_id` | PK |
| `signal_id` | FK -> Capa 1 |
| `observation_timestamp_utc` | timestamp de la vela observada (> t) |
| `open`/`high`/`low`/`close`/`volume` | OHLCV de la vela observada |
| `is_inverse_crossover` | bool: esta vela confirma la senal contraria del SMA |

Diseño normalizado (recomendado): NO persistir snapshots redundantes `+1m/+5m/+15m/+30m`.
En su lugar, persistir la **secuencia de velas OHLCV posteriores** (una fila por vela por
`signal_id`); cualquier horizonte (+1/+5/+15/+30m) y los maximos/minimos posteriores se
**derivan** inequivocamente de esa secuencia. Esto evita duplicacion y desincronizacion.
Regla dura: **Capa 2 = informacion > t**.

### CAPA 3 — Signal Outcomes (derivados de info > t)
Resultados calculados a partir de Capa 2 (y del stop candidato de investigacion).

| Campo | Notas |
|---|---|
| `outcome_id` | PK |
| `signal_id` | FK -> Capa 1 |
| `mfe_points` / `mae_points` | maxima excursion favorable/adversa (marco MFE/MAE) |
| `mfe_atr` / `mae_atr` | normalizadas por ATR_at_t si aplica (research metadata) |
| `stop_candidate_touched` | bool (ATR 1.75 candidato, research; nunca operativo) |
| `first_touch_timestamp_utc` | primer toque del stop candidato (nullable) |
| `inverse_crossover_timestamp_utc` | fin del horizonte endogeno (nullable) |
| `returned_to_reference` | bool: si el precio volvio a reference_price |
| `censored` | bool: horizonte no completado (p.ej. fin de sesion) |
| `horizon_metrics` | metricas por horizonte, derivables; estructura extensible |

Regla dura: **Capa 3 = derivados de informacion > t**. NO se define una unica verdad
`GANO/PERDIO`: el mismo historial debe poder producir DIFERENTES labels segun la pregunta de
investigacion (distinto horizonte, distinto stop, distinta regla de evaluacion). Las labels se
calculan on-demand desde Capas 2/3, no se congelan como un unico booleano de exito.

---

## 13. Anti-data-leakage (restriccion arquitectonica)

Invariantes de diseño (obligatorios):
1. **Capa 1 nunca recibe informacion futura** (solo datos <= t).
2. **Capas 2/3 nunca reescriben Capa 1.**

Mecanismos:
- **Separacion fisica/logica:** tres tablas/colecciones distintas (`signal_context`,
  `signal_observations`, `signal_outcomes`), no una tabla unica mutable.
- **Inmutabilidad de Capa 1:** append-only; sin UPDATE de campos de contexto. Idealmente
  garantizado por permisos de escritura (la ruta que escribe Capa 1 no puede escribir 2/3 y
  viceversa) y/o por constraint/trigger.
- **Claves:** `signal_id` PK en Capa 1; FK `signal_id` en Capas 2/3.
- **Foreign keys:** Capas 2/3 referencian Capa 1 (integridad referencial en un motor que la
  soporte; ver seccion 14).
- **Timestamps:** toda fila de Capa 2/3 debe tener `observation_timestamp_utc > timestamp_utc`
  de su Capa 1 (constraint de tiempo). Una fila con `<= t` es rechazada.
- **Restricciones de escritura:** el escritor de Capa 1 se invoca en `t`; los escritores de
  Capas 2/3 solo con datos posteriores.
- **Tests futuros de leakage:** (a) intentar escribir en Capa 2/3 con timestamp <= t -> debe
  fallar; (b) intentar mutar un campo de Capa 1 tras su creacion -> debe fallar; (c) verificar
  que ningun feature de Capa 1 depende de una fila de Capa 2/3.

**Ejemplo de violacion que el diseño debe IMPEDIR:** calcular `sma_fast`/`sma_slow` de Capa 1
usando el cierre de la vela `t+1` (futuro), o "rellenar" `atr_at_t` con un ATR recalculado tras
ver velas posteriores. Ambos inyectarian informacion > t en Capa 1 y quedan prohibidos por la
regla de inmutabilidad + constraint temporal.

---

## 14. Persistencia — decision formal

Comparacion para el registro Live (escritura continua, relacional Signal->Observations->Outcomes):

| Criterio | PostgreSQL | JSONL | Parquet | SQLite |
|---|---|---|---|---|
| INSERT incremental (1 fila/evento) | Excelente | Bueno (append) | Malo (orientado a batch/columnar) | Bueno |
| Relaciones 3 capas + FK | Excelente (FK nativas) | Nulo (sin relaciones) | Nulo | Bueno (FK) |
| Consultas (join, filtros) | Excelente (SQL) | Pobre (scan) | Bueno analitico, pobre transaccional | Bueno (SQL) |
| Persistencia tras reinicios | Si (servidor/volumen) | Si (archivo) | Si (archivo) | Si (archivo) |
| Concurrencia (multiples escritores/lectores) | Excelente | Pobre | Pobre | Limitada (lock a nivel archivo) |
| Integridad referencial | Si | No | No | Si |
| AWS | RDS/Aurora gestionado | S3/EFS manual | S3 manual | EBS/EFS (archivo) |
| Backups | Maduros (snapshots RDS) | Manual | Manual | Copia de archivo |
| Crecimiento continuo | Excelente | Degrada | Bueno para historico frio | Moderado |
| Export a ML | Bueno (a Parquet/df) | Bueno | Excelente (nativo ML) | Bueno |
| Complejidad operativa | Media-alta (servidor) | Muy baja | Baja | Baja |

**Tecnologia recomendada para Live v1: PostgreSQL.** Razones dominantes: escritura incremental
fila a fila en vivo, relaciones e integridad referencial nativas entre las 3 capas
(`signal_id` FK), consultas ad-hoc para el analisis del socio, concurrencia (backend escribe
mientras se consulta), y backups maduros en AWS (RDS). Nota de consistencia con steering
`dev-rules.md`: PostgreSQL figura en el roadmap futuro y aqui la funcionalidad Live realmente
lo requiere (no se adopta "por popularidad"); su seleccion se justifica por necesidad. El
alcance MVP simulation no obligaba DB; Live v1 con persistencia continua si.

**Por que esto NO contradice que Calibration historica siga en Parquet:** son dos dominios
distintos. Calibration opera sobre un dataset historico **inmutable, batch, columnar y de
lectura masiva** (Parquet es optimo y ya esta congelado/validado). El registro Live es
**transaccional, incremental, relacional y de escritura continua** (PostgreSQL es optimo).
Cada uno usa la tecnologia adecuada a su patron de acceso; no compiten ni se sustituyen.

Marcas pendientes: version/plan de PostgreSQL, esquema fisico exacto, y si RDS vs contenedor
Postgres en ECS -> [SEGURO — REQUIERE CONFIRMACION]. La decision de tecnologia (PostgreSQL) SI
queda propuesta; los detalles de despliegue se confirman antes de implementar.

---

## 15. Semantica de persistencia de Capa 1 (P1)

Situacion a diseñar explicitamente: `SignalEngine` genera BUY -> la persistencia de Capa 1 falla.

**Politica seleccionada: persistir Capa 1 ANTES de publicar la senal al dashboard, con reintentos
acotados y degradacion visible.** Secuencia:
1. Generada la senal, intentar `persist(signal_context)` con reintentos acotados
   (numero/backoff -> [SEGURO — REQUIERE CONFIRMACION]).
2. Si la persistencia tiene exito -> publicar la senal al dashboard (marcador BUY/SELL).
3. Si la persistencia falla tras los reintentos -> NO se garantiza trazabilidad; se registra un
   error en logging con contexto suficiente, y la senal se marca como **no trazable**. Politica
   por defecto propuesta: **no publicar como senal trazable de sesion formal** una senal que no
   se pudo persistir; en su lugar exponer un estado de error/degradacion en la UI (p.ej. market
   data degradado) para que el socio sepa que hubo un problema.
   Alternativa (publicar igual pero marcada "no persistida") -> [SEGURO — REQUIERE CONFIRMACION].

Objetivo invariante: **toda senal mostrada durante una sesion formal debe ser posteriormente
trazable por `signal_id`.** Persistir-antes-de-publicar hace cumplir este objetivo por
construccion. (Nota: el orden persist->publish añade latencia minima; aceptable dado el throttle
de 1/seg del dashboard.) No implementar en este turno.

---

## 16. Strategy version

`strategy_version` es un campo **explicito y obligatorio** de Capa 1. Mecanismo (no solo un
literal): un identificador versionado, asignado en el arranque de la sesion y grabado en cada
registro, que identifique inequivocamente la estrategia/version que produjo la senal. Ejemplo
conceptual `SMA_9_21_v1` (literal exacto -> [SEGURO — REQUIERE CONFIRMACION]).

Debe permitir distinguir en el futuro: SMA actual; SMA + mejora validada (p.ej. tras aprobar
ATR en Validation/Final OOS); Machine Learning; otras configuraciones. NUNCA se infiere la
estrategia desde la fecha: se registra explicitamente. Un cambio de estrategia => nuevo
`strategy_version` (y, en la practica, nueva sesion).

---

## 17. ATR 1.75 (candidate / research metadata unicamente)

Calibration selecciono `selected_atr_B = 1.75`, pero AUN no paso Validation ni Final OOS. Para
Live v1 se diseña UNICAMENTE la capacidad futura de calcularlo/registrarlo internamente:

- **NO** modifica la decision BUY/SELL.
- **NO** filtra senales.
- **NO** modifica `SignalEngine` (SMA 9/21 intacto).
- **NO** aparece en el dashboard (ni ATR, ni multiplicador, ni stop, ni lineas, ni toggle).
- **NO** es un stop operativo.
- **NO** se presenta como mejora aprobada.

Si se almacena en Capa 1 (`atr_at_t`) o en Capa 3 (`stop_candidate_touched`, `mfe_atr`,
`mae_atr`), debe quedar inequivocamente etiquetado como **candidate/research metadata** (p.ej.
prefijo/namespace `research_` o un flag `is_research=true`), separado de cualquier
configuracion operativa confirmada. Su calculo (si se activa) es lateral y no altera el motor.

---

## 18. VolumeFilter y RiskGate — separacion explicita

Para TradeCore Live Visual v1:
- **VolumeFilter NO participa** (no filtra, no decide, no se muestra).
- **RiskGate NO participa** (no calcula contratos; sus parametros no aprobados
  `equity_disponible`/`riesgo_pct`/`valor_por_punto` NO se completan).
- **Position sizing NO participa** (no se calcula ni se muestra).

Estos componentes permanecen aislados: no se importan ni invocan desde el pipeline live
(`app.py`, `throttled_pusher.py`, dashboard). El Design protege esta separacion como invariante
de v1; conectarlos requeriria una etapa y aprobacion separadas. Consistente con el diagnostico
previo (`RISK_GATE_LIVE_EXPOSURE = NONE`).

---

## 19. Regla de copy del dashboard (regla permanente de producto)

En NINGUN lugar de la interfaz —ahora con SMA, ni en el futuro con ATR o Machine Learning si
superan Validation/Final OOS— se debe presentar una senal como "el momento optimo para generar
ganancias" ni usar lenguaje equivalente que implique certeza, rentabilidad garantizada o
superioridad no demostrada.

El dashboard comunica UNICAMENTE: "TradeCore genero una senal BUY/SELL conforme a la estrategia
activa" (indicando `strategy_version`). Se distinguen tres conceptos separados:
- **senal generada** (hecho: la matematica activa produjo BUY/SELL);
- **valor predictivo** (se evalua con metodologia estadistica: Validation / Final OOS);
- **resultado economico** (depende de horizonte, stop, ejecucion; fuera de alcance visual).

La observacion del socio aporta evidencia cualitativa e hipotesis, pero NO sustituye la
validacion cuantitativa. Esta regla es permanente para futuras versiones y estrategias.

---

## 20. Dashboard Live Visual v1 (cambios minimos)

**Debe mostrar** (la mayoria ya existe; ver seccion 21 para los cambios protegidos):
- Instrumento NQ; timeframe 1m; velas; SMA 9; SMA 21; marcadores BUY/SELL.
- Estado **Browser <-> TradeCore** (ya existe).
- Estado **TradeCore <-> Databento** (nuevo indicador).
- Estado **market data**: `SYNCED` / `STALE` / `RECONNECTING` / `RESYNCING` (nuevo).
- Ultima vela valida (con timestamp valido — depende de la correccion de la seccion 9).
- Modo `LIVE` inequivoco (badge existente, valor `live`).

**NO debe mostrar:** ATR, ATR 1.75, stop, lineas/toggle ATR, VolumeFilter, RiskGate, position
size, probabilidades inexistentes, ni afirmaciones de rentabilidad (seccion 19).

Los cambios se limitan a añadir indicadores de estado (aditivos) reutilizando el mensaje
`status` extendido (seccion 8), sin rediseño visual mayor.

---

## 21. Componentes protegidos — auditoria de cambios [REQUIERE APROBACION]

### 21.1 `src/api/app.py`
- **Seccion:** `lifespan` (seleccion de fuente) + `_processing_loop` (reuso).
- **Comportamiento actual:** solo modo simulation; crea `SimulationReplay` y lo consume `_processing_loop`.
- **Cambio minimo:** en `lifespan`, elegir la fuente segun `Settings.mode` (`live` ->
  `connector.subscribe_live()`; `simulation` -> `SimulationReplay`) y asignarla a la MISMA
  variable/contrato que consume `_processing_loop`, SIN duplicar el cuerpo del loop. Añadir manejo
  de estados de conexion/sync (secciones 5, 8) y el gate "no señales si no SYNCED".
- **Razon:** habilitar Live reutilizando el pipeline; cumplir la separacion Live/Simulation.
- **Tests afectados:** tests de orquestacion del lifespan (si existen) + nuevos tests de seleccion de fuente.
- **Riesgo de regresion:** medio — tocar `lifespan` podria afectar el arranque de simulacion; mitigar con
  ruta simulation byte-identica y cobertura de ambos modos.

### 21.2 `dashboard/index.html`
- **Seccion:** `handleStatus` + markup del header (indicadores de estado).
- **Comportamiento actual:** un solo indicador (browser) + `Ultimo` con guard `isNaN`.
- **Cambio minimo:** añadir indicador Databento y estado market data; consumir campos nuevos
  opcionales del `status` (seccion 8). Mantener el guard de timestamp.
- **Razon:** distinguir las dos conexiones y el estado de sync para el socio.
- **Tests afectados:** (UI) — validacion manual + posibles tests de front si se añaden.
- **Riesgo de regresion:** bajo — cambios aditivos; el dashboard antiguo ignora campos nuevos.

### 21.3 `src/api/throttled_pusher.py`
- **Seccion:** `queue_status` (timestamp) + payload de status (campos nuevos).
- **Comportamiento actual:** `isoformat() + "Z"` (invalido) y campos `connected/last_candle_time/mode`.
- **Cambio minimo:** emitir ISO valido (seccion 9) y AÑADIR campos opcionales
  (`databento_connected`, `market_data_state`, `market_mode`, `live_session_id`) sin romper los existentes.
- **Razon:** corregir Invalid Date de raiz + comunicar estados Live.
- **Tests afectados:** nuevo test de timestamp valido; test de compatibilidad del payload status.
- **Riesgo de regresion:** bajo — aditivo + correccion puntual; verificar que el throttle 1/seg no cambia.

### 21.4 `src/connectors/databento_connector.py`
- **Seccion:** NUEVOS `subscribe_live()`, `is_connected`, `reconnect()` (Fase B). La carga
  historica (`load_historical`, `_fetch_historical_sync`) NO se altera.
- **Comportamiento actual:** solo Historical (Fase A). Sin metodos Live.
- **Cambio minimo:** añadir la ruta Live de forma AISLADA, sin tocar la Fase A.
- **Razon:** habilitar el stream Live.
- **Tests afectados:** desbloquea los 12 tests spec-ahead de `test_databento_connector.py` (seccion 22).
- **Riesgo de regresion:** bajo para Fase A si el aislamiento se respeta; medio en Live (logica nueva).
- **Nota de steering:** el steering `fase-actual-etapa1.md` protege "SOLO la parte de carga
  historica de Fase A" de este archivo; el streaming Live sigue bloqueado por separado (GATE
  Fase B). Añadir Live requiere levantar ese GATE con aprobacion explicita + plan Standard Databento.

---

## 22. Tests a diseñar (no escribir todavia)

- **Connector Live:** `subscribe_live` produce Candle desde mensajes ohlcv-1m; `is_connected`
  refleja estado; `reconnect` con backoff (exito 1er/2do intento, agotamiento, error fatal sin retry).
- **Gap:** deteccion de hueco tras reconexion; recuperacion via Historical API bajo umbral;
  finalizacion de sesion por gap > umbral; dedup; velas fuera de orden descartadas.
- **Semantica de vela:** solo velas cerradas generan señal; dedup por timestamp; orden cronologico.
- **Estados:** Browser status; Databento status; `SYNCED`/`STALE`/`RESYNCING`; no-señales si no SYNCED.
- **Timestamp:** `queue_status` emite ISO valido (sin `+00:00Z`); parseable.
- **Motor intacto:** `SignalEngine` no modificado (SMA 9/21) — snapshot/regresion.
- **Persistencia/trazabilidad:** Capa 1 persistida; `signal_id` trazable; `strategy_version` obligatorio;
  persist-antes-de-publish; fallo de persistencia => politica de seccion 15.
- **Anti-leakage:** rechazo de escritura Capa 2/3 con timestamp <= t; inmutabilidad de Capa 1.
- **Separacion:** LIVE nunca cae a Simulation; `market_mode` inmutable.
- **UI negativa:** ausencia de ATR/VolumeFilter/RiskGate/position size en la UI; copy sin rentabilidad.

**Relacion con los 12 tests spec-ahead existentes** (`tests/unit/test_databento_connector.py`):
- `TestDabentoConnectorLiveSubscription` (5): `subscribe_live` yields candles, actualiza
  `last_candle_time`, `is_connected` True durante stream / False al terminar, descarta velas
  <= ultima historica (esto ultimo se alinea con el dedup de gap recovery, seccion 5/7).
- `TestDabentoConnectorReconnection` (7): `reconnect` exito 1er/2do intento, hasta 3 intentos,
  backoff exponencial, AUTH_FAILED sin retry, `is_connected` False tras fallo, reconexion al
  desconectar el stream.
- Estos 12 tests hoy FALLAN porque Fase B nunca se implemento; este Design es exactamente su
  contrato de implementacion. Al implementar seccion 3/4 deberian pasar. (Los valores de backoff
  que los tests asumen —1s/2s/4s, 3 intentos— deben conciliarse con el
  [SEGURO — REQUIERE CONFIRMACION] de la seccion 4 antes de implementar.)

---

## 23. Criterios de aceptacion — LIVE_VISUAL_V1_READY_FOR_TRADER_REVIEW

Se declara `= YES` solo si TODO lo siguiente se cumple:
1. Databento Live conectado.
2. NQ.c.0 ohlcv-1m recibido continuamente.
3. Velas cronologicas y sin duplicados.
4. SMA 9/21 calculadas sobre velas cerradas.
5. BUY/SELL dibujados en la vela correcta.
6. Timestamp valido (sin Invalid Date).
7. Estado Browser <-> TradeCore visible.
8. Estado TradeCore <-> Databento visible.
9. Los gaps impiden señales hasta resync (CONNECTED != SYNCED respetado).
10. Capa 1 persistente y trazable por `signal_id`.
11. `strategy_version` registrada en cada senal.
12. Modo LIVE inequivoco.
13. ATR / VolumeFilter / RiskGate no visibles y no operativos.
14. Copy sin afirmaciones de rentabilidad (seccion 19).
15. Deployment AWS identificable por version/commit.
16. Prueba de estabilidad durante una ventana Live definida = [SEGURO — REQUIERE CONFIRMACION] (no se inventa la duracion).

---

## 24. Priorizacion P0–P3

- **P0 — Live tecnicamente funcional:** `subscribe_live` + `is_connected` + `reconnect` (Fase B);
  seleccion de fuente en `app.py`; suscripcion ohlcv-1m NQ.c.0; semantica de vela cerrada;
  estados de conexion basicos. (Requiere levantar GATE Fase B + plan Standard.)
- **P1 — indispensable antes de sesion formal del socio:** correccion de timestamp (seccion 9);
  indicador de segunda conexion + market data state; gap detection/recovery con CONNECTED vs
  SYNCED; **Signal Context (Capa 1) persistente y trazable** (obligatorio); `strategy_version`;
  deployment AWS identificable; copy sin rentabilidad.
- **P2 — enriquecimiento cientifico:** Capa 2 (observations) y Capa 3 (outcomes); ATR 1.75
  candidate/research metadata; labels multiples on-demand.
- **P3 — posteriores:** TradingView webhook automatico; mejoras visuales; otras estrategias.

---

## 25. Entregables incrementales (dependencias)

(No son Tasks; son unidades que permitiran generar Tasks pequeñas y commits atomicos tras aprobar el Design.)

- **E-A. Fix timestamp + status extendido** (P1) — `throttled_pusher.py` (+ dashboard consume campos). Dep: ninguna. Bajo riesgo, alto valor inmediato.
- **E-B. Connector Live** (P0) — `subscribe_live`/`is_connected`/`reconnect`. Dep: GATE Fase B + plan Standard. Desbloquea 12 tests.
- **E-C. Seleccion de fuente en app.py** (P0) — pipeline compartido Simulation/Live. Dep: E-B.
- **E-D. Semantica de vela cerrada + dedup/orden en flujo live** (P0). Dep: E-C.
- **E-E. Estados de conexion + market data (SYNCED/STALE) UI+protocolo** (P1). Dep: E-A, E-C.
- **E-F. Gap detection + recovery via Historical + umbral** (P1). Dep: E-C, E-E.
- **E-G. LiveSession + live_session_id + provenance** (P1). Dep: E-C.
- **E-H. Persistencia PostgreSQL + Capa 1 (persist-antes-de-publish)** (P1). Dep: E-G. (incluye decision de despliegue Postgres [SEGURO]).
- **E-I. Dashboard Live v1 (indicadores + copy)** (P1). Dep: E-E, E-A.
- **E-J. Deployment AWS identificable por version/commit** (P1). Dep: (independiente; coordinar con Linea AWS externa).
- **E-K. Prueba de estabilidad ventana Live** (P1). Dep: E-B..E-I. (duracion [SEGURO]).
- **E-L. Capa 2 (observations)** (P2). Dep: E-H.
- **E-M. Capa 3 (outcomes) + ATR candidate metadata** (P2). Dep: E-L.
- **E-N. TradingView webhook** (P3). Dep: posterior.

---

## 26. Fuera de alcance de Live Visual v1

Se mantienen explicitamente FUERA: Validation; Final OOS; Machine Learning; QQQ; NDX;
Greeks/GEX; Order Book completo; TradingView webhook automatico; nuevas reglas BUY/SELL;
VolumeFilter operativo; RiskGate operativo; position sizing; ejecucion automatica de ordenes.

---

## Decisiones [SEGURO — REQUIERE CONFIRMACION] pendientes (resumen)

1. ~~Parametros de reconexion Live~~ RESUELTO (G0.1, seccion 4.1): 3 intentos, delays 1s/2s/4s, fatal sin retry (decision TradeCore).
2. ~~Config del SDK Live~~ RESUELTO (G0.2, seccion 4.2): `reconnect_policy = ReconnectPolicy.NONE`, `heartbeat_interval_s = None`.
3. `MAX_AUTO_RECOVERABLE_GAP` (umbral de gap recuperable) (seccion 5).
4. Literal/mecanismo exacto de `strategy_version` (p.ej. `SMA_9_21_v1`) (seccion 16).
5. Detalles de despliegue de PostgreSQL: RDS vs contenedor, version, esquema fisico (seccion 14).
6. Politica exacta ante fallo de persistencia (numero de reintentos/backoff; publicar-marcada vs no-publicar) (seccion 15).
7. Duracion de la ventana de estabilidad Live para READY (seccion 23).
8. Mecanismo de inyeccion de `application_version`/git SHA (seccion 10).

---

# ADENDA A — Warm-up, maquina de estados Live y recuperacion de gaps

> Estado: BORRADOR EN REVISION. Esta adenda cierra el vacio de diseño (1) startup warm-up /
> re-warm-up y (2) parte de la maquina de estados/recuperacion. Complementa (no reemplaza) las
> secciones 1–26 ya aprobadas. La semantica temporal de timestamps y la auditoria final se
> añaden en la Parte 2 de este mismo turno (ADENDA B).

## A.2. Startup Live — estados obligatorios (maquina de estados ampliada)

Se amplia formalmente la maquina de estados Live (seccion 8) con la secuencia de arranque:

```
DATABENTO_DISCONNECTED
      └─► DATABENTO_CONNECTED
              └─► WARMING_UP        (reconstruccion del estado matematico)
                      └─► SYNCING   (conciliacion Historical warm-up <-> stream Live)
                              └─► LIVE_SYNCED   (unico estado apto para señales Live)
```

**Invariante arquitectonica (dura):** SOLO `LIVE_SYNCED` habilita la publicacion y persistencia
de nuevas señales BUY/SELL como señales Live. En CUALQUIER otro estado
(`DATABENTO_DISCONNECTED`, `DATABENTO_CONNECTED`, `WARMING_UP`, `SYNCING`, `STALE`,
`RECONNECTING`, `RESYNCING`) esta PROHIBIDO publicar/persistir/emitir/contabilizar nuevas
señales Live. Este invariante se suma a la distincion CONNECTED != SYNCED de la seccion 8:
`CONNECTED` (hay socket Databento) nunca implica aptitud para generar señales.

Relacion con los estados de market data (seccion 8): `WARMING_UP` y `SYNCING` son estados de
`market_data_state`; `LIVE_SYNCED` corresponde a `market_data_state = SYNCED`.

## A.3. Que significa WARMING_UP (no es solo llenar el buffer)

`WARMING_UP` NO significa unicamente llenar `CandleBuffer`. Debe inicializar TODO el estado
matematico necesario para que el `SignalEngine` pueda detectar el **siguiente** crossover de
forma valida:
- SMA rapida (SMA 9) con historia suficiente;
- SMA lenta (SMA 21) con historia suficiente;
- **estado de crossover anterior** (`_prev_fast` / `_prev_slow` del `SignalEngine`, o equivalente)
  correctamente establecido, de modo que la deteccion de cruce del PROXIMO cierre sea valida;
- cualquier otro contexto matematico habilitado para calculo interno en Live v1 (p.ej. ATR
  candidate/research, si se activa su calculo — ver seccion 17; NUNCA decide señales).

### `warmup_min` — derivacion dinamica (no hardcodear 22)

`warmup_min` NO es una constante arquitectonica ("22 candles"); es una funcion derivada de las
dependencias reales de los indicadores efectivamente activos, reutilizando la formula de
Etapa 1 cuando corresponda:

```
warmup_min = max(fast_period,
                 slow_period + crossover_state_requirement,
                 [ atr_period + 1  SOLO si el calculo ATR research esta habilitado en Live ],
                 [ volume_period   SOLO si se registra volumen-ventana en Live ])
```

- Para el **motor SMA 9/21 operativo de Live v1**, las dependencias que DECIDEN señales son
  unicamente `fast_period=9`, `slow_period=21` y el requisito de estado de cruce previo
  (`crossover_state_requirement = 1`, porque `SignalEngine.evaluate` necesita UNA evaluacion
  previa valida para poblar `_prev_fast/_prev_slow` antes de poder detectar un cruce).
- **Demostracion de suficiencia para SMA 9/21:** con `slow_period + crossover_state_requirement
  = 21 + 1 = 22`, tras alimentar 22 cierres cerrados el motor ha producido al menos una
  evaluacion previa completa (SMA9 y SMA21 definidas en la vela 21) y su estado
  `_prev_fast/_prev_slow` queda establecido; por tanto en el cierre 23 (primera vela nueva
  post-warm-up) el crossover del PROXIMO cierre es detectable de forma valida. El minimo que
  satisface SMA9 (9) y SMA21 (21) queda dominado por el termino 22, que ademas es EXACTAMENTE
  el que garantiza el estado de cruce anterior. Es decir: el mismo minimo que llena las SMAs
  tambien establece el estado previo necesario.
- **Valor resultante ACTUAL (documentado, no congelado como magia):** `signal_warmup_min = 22`
  para SMA 9/21 con `crossover_state_requirement = 1`. Si cambian los periodos, se **recalcula**
  con la formula; no se hardcodea 22.

### Distincion `signal_warmup_min` vs `research_metadata_warmup_min`

- **`signal_warmup_min`**: minimo requerido por los componentes que DECIDEN señales
  (solo SMA 9/21 + estado de cruce). = 22 hoy. Es el que gobierna la transicion a LIVE_SYNCED.
- **`research_metadata_warmup_min`**: minimo adicional requerido SOLO si se habilita calculo
  interno de metadata de investigacion (p.ej. ATR: `atr_period + 1 = 15`; volumen-ventana: 20).
  NO retrasa ni condiciona la generacion de señales Live; solo determina desde cuando la
  metadata research es calculable. Si esa metadata aun no es calculable, se registra como
  `null`/no disponible, SIN bloquear señales.
- Regla anti-acoplamiento: `signal_warmup_min` NUNCA debe crear dependencia de componentes NO
  activos operacionalmente (VolumeFilter/RiskGate no participan — seccion 18; ATR es research —
  seccion 17). El warm-up de señales depende exclusivamente del motor SMA operativo.

## A.4. Señales producidas durante warm-up (congelado)

Regla dura: durante `WARMING_UP` (y `SYNCING`) las velas historicas alimentan `CandleBuffer` y
`SignalEngine` UNICAMENTE para reconstruir su estado matematico. Cualquier crossover detectado
mientras se procesa historia de warm-up **NO es una nueva señal Live**.

Durante `WARMING_UP`/`SYNCING` esta PROHIBIDO:
- publicar BUY/SELL al dashboard;
- persistir ese crossover como nueva señal Live de Capa 1;
- enviarlo por WebSocket como señal nueva;
- contabilizarlo como señal de la nueva sesion.

La PRIMERA señal elegible como Live debe originarse en una **vela nueva valida posterior** a la
finalizacion de la sincronizacion (estado `LIVE_SYNCED`), es decir, una vela que llega por el
stream Live real y cuyo cierre ocurre estando ya SYNCED.

## A.5. Fuente del historico de warm-up

Fuente autoritativa: **Databento Historical API**, con procedencia de mercado compatible con
Live: `GLBX.MDP3`, `NQ.c.0`, `ohlcv-1m`, continuous symbology compatible con el histori­co
aprobado de Fase A. (Estas velas se marcan `data_source = databento_historical_warmup` en
provenance — seccion 11 — para no confundirlas con `databento_live`.)

**Operacion en terminos de velas, no de minutos:** NO se asume que pedir "22 minutos" garantiza
22 velas. Fines de semana, holidays, gaps de mercado e interrupciones producen menos velas por
minuto cronologico. La operacion se define como: **"recuperar al menos N (= signal_warmup_min)
velas cerradas validas inmediatamente anteriores al boundary de Live"**, ampliando hacia atras
la ventana de consulta cuanto sea necesario hasta reunir N velas cerradas validas (con un tope
de retroceso [SEGURO — REQUIERE CONFIRMACION] para no barrer indefinidamente).

## A.6. Reutilizacion vs. consulta nueva (decision formal)

**Decision:** cada nueva `LiveSession` obtiene su warm-up desde **Historical API cada vez**
(fuente autoritativa), priorizando confiabilidad y simplicidad sobre optimizacion prematura. NO
se implementa cache/reutilizacion de estado previo en Live v1.

Justificacion: reutilizar estado/datos previos exigiria demostrar inequivocamente continuidad,
identidad del instrumento, ausencia de gaps, frescura y orden temporal — precisamente lo que un
warm-up limpio garantiza por construccion. Un cache sin esas garantias arriesga contaminar el
estado matematico. Si en el futuro se propusiera caching, debera aportar esas pruebas; por ahora
la consulta Historical es la fuente autoritativa. (No se implementa cache en este turno.)

## A.7. Race condition Historical <-> Live (obligatorio) — boundary/watermark

No basta con "Historical termina -> recien entonces conectar Live": las velas que cierran
durante la consulta Historical se perderian (hueco silencioso) o se duplicarian. Se diseña un
**boundary/watermark temporal explicito**.

**Estrategia seleccionada (conectar-primero, bufferizar, luego warm-up hasta boundary):**
1. `DATABENTO_CONNECTED`: conectar Databento Live y **bufferizar temporalmente** las velas Live
   que empiecen a llegar (buffer Live de arranque), SIN procesarlas aun por el SignalEngine.
2. Determinar el **boundary** = timestamp de la primera vela Live cerrada bufferizada (o, si aun
   no llega ninguna, el ultimo minuto cerrado conocido); el warm-up cubrira el intervalo hasta
   (exclusivo) esa primera vela Live.
3. `WARMING_UP`: obtener via Historical API las >= `signal_warmup_min` velas cerradas validas
   inmediatamente ANTERIORES al boundary.
4. Alimentar el warm-up **cronologicamente** (orden ascendente) a `CandleBuffer` + `SignalEngine`
   (solo reconstruccion de estado; sin publicar señales — seccion A.4).
5. `SYNCING`: **merge** de las velas Historical de warm-up con las velas Live bufferizadas,
   **deduplicando por identidad/timestamp de vela** (`CandleBuffer` descarta `<= last_timestamp`)
   y garantizando **orden** ascendente.
6. Procesar cualquier continuidad faltante entre el fin del warm-up y el inicio del stream Live
   (catch-up via Historical si quedara un micro-hueco), volviendo a deduplicar.
7. Solo cuando no haya gaps conocidos y el estado matematico corresponda cronologicamente al
   stream actual -> `LIVE_SYNCED`.

**Comparacion con alternativas:**
- *(Alt. A) Historical-primero-luego-conectar*: mas simple pero deja un hueco no observado
  durante la consulta -> **rechazada** (viola continuidad verificable).
- *(Alt. B) Solo-Live-sin-warm-up*: arranque sin estado de cruce valido -> **rechazada** (viola
  A.3).
- *(Seleccionada) Conectar-primero + buffer + warm-up hasta boundary + merge/dedup*: algo mas
  compleja pero es la unica que garantiza **continuidad temporal verificable** sin hueco ni
  duplicacion. Se elige por seguridad de datos.

Resultado exigido: entre la ultima vela historica usada y la primera vela Live procesada NO debe
existir hueco silencioso, duplicacion ni desorden (continuidad verificable por timestamps
contiguos de velas cerradas, salvo gaps legitimos de mercado que se tratan como tales).

## A.8. WARMING_UP vs SYNCING (definiciones precisas)

- **WARMING_UP**: reconstruccion del ESTADO MATEMATICO previo (SMAs + estado de cruce) a partir
  de historia; su exito se mide por "el SignalEngine tiene estado valido para evaluar el proximo
  cierre".
- **SYNCING**: CONCILIACION entre la historia usada para warm-up y el stream Live actual: merge,
  dedup, orden, deteccion de gaps, determinacion del boundary/ultima vela cerrada y catch-up de
  continuidad faltante.
- **LIVE_SYNCED**: no existen gaps conocidos Y el estado matematico corresponde
  cronologicamente al stream actual. Solo entonces se habilitan señales Live.

## A.9. Coste y latencia del warm-up + alerta de salud de conexion

- **Volumen esperado de datos:** pequeño respecto del dataset historico de Calibration
  (~`signal_warmup_min` velas, del orden de decenas de velas ohlcv-1m por warm-up, mas el margen
  por gaps de mercado). Esta comparacion es una inferencia de magnitud, NO un precio.
- **Frecuencia de consulta:** una consulta Historical por arranque de `LiveSession` y una por
  cada re-warm-up (gap irrecuperable, seccion A.11).
- **Impacto en startup / latencia:** estimacion arquitectonica (no garantia): dominado por la
  latencia de la Historical API + alimentar unas decenas de velas; se espera del orden de
  segundos, pero es una ESTIMACION, no un compromiso.
- **Dependencia de Historical API:** el warm-up depende de que Historical este disponible; si
  falla, la sesion no alcanza `LIVE_SYNCED` (se mantiene en WARMING_UP/estado de error visible),
  nunca genera señales con estado incompleto.
- **Costo:** [SEGURO — COSTO ACTUAL REQUIERE VERIFICACION]. Puede documentarse que el volumen es
  pequeño frente a Calibration, pero NO se afirma un precio vigente sin evidencia de este turno.
- **UI durante el warm-up:** mostrar `Market Data: WARMING_UP` (y `SYNCING`), sin señales, con la
  ultima vela valida disponible; nunca comunicar aptitud para señales (seccion A.13).

**Contador de eventos de warm-up / re-warm-up:** se diseña un contador por `LiveSession` y por
dia. Si la frecuencia supera un umbral `MAX_WARMUP_EVENTS` [SEGURO — REQUIERE CONFIRMACION],
debe tratarse como **alerta de salud de conexion VISIBLE** (indicador en el dashboard +
logging), no como recuperacion silenciosa indefinida. Una conexion Databento inestable que
dispare warm-ups repetidos es informacion operacional valiosa para el socio, no un mecanismo
puramente interno.

## A.10. Gap recuperable (<= MAX_AUTO_RECOVERABLE_GAP) — reafirmado

Se mantiene la logica ya aprobada (seccion 5):

```
LIVE_SYNCED -> gap detectado -> STALE / RECONNECTING -> RESYNCING
            -> Historical gap recovery (velas cerradas del hueco) -> orden + dedup -> LIVE_SYNCED
```

No se generan señales mientras el estado matematico pueda estar incompleto (durante
STALE/RECONNECTING/RESYNCING).

## A.11. Gap irrecuperable (> MAX_AUTO_RECOVERABLE_GAP) — secuencia congelada

```
gap irrecuperable
   -> detener generacion de señales
   -> cerrar LiveSession actual (status = ENDED_GAP_EXCEEDED)
   -> descartar el estado matematico anterior
   -> reset SignalEngine (conceptual, seccion A.12)
   -> nuevo live_session_id
   -> DATABENTO_CONNECTED -> WARMING_UP -> SYNCING -> LIVE_SYNCED
   -> recien entonces permitir señales
```

Regla dura: NUNCA reutilizar `_prev_fast`, `_prev_slow`, un `CandleBuffer` incompleto, ni
contexto previo al hueco como si hubiera continuidad demostrada. **No existe ningun camino de
reanudacion de señales tras un gap irrecuperable sin un nuevo warm-up completo.**

### Garantia de orden atomico (obligatoria) — por cada vela recibida

Secuencia estricta, sin excepciones, para CADA vela recibida:

```
1. vela recibida
2. verificacion de continuidad / deteccion de gap   (SIEMPRE primero)
3. SOLO si NO hay gap detectado -> evaluacion del SignalEngine
4. SOLO si hay cruce -> publicacion + persistencia (y solo si estado == LIVE_SYNCED)
```

Debe ser arquitectonicamente IMPOSIBLE que una señal se evalue o publique en el mismo ciclo en
que se detecta un gap: la deteccion de gap SIEMPRE precede a la evaluacion; nunca ocurre en
paralelo ni despues. Si el paso 2 detecta gap, el ciclo NO evalua señal y transiciona a
STALE/RESYNCING (o a re-warm-up si el gap es irrecuperable).

## A.12. Reset (conceptual, sin modificar SignalEngine todavia)

El reset ante gap irrecuperable debe limpiar, conceptualmente, TODO estado que asuma
continuidad:
- **SignalEngine**: `_prev_fast`, `_prev_slow` (estado de cruce) -> a estado no inicializado.
- **CandleBuffer**: vaciar (o reconstruir desde cero en el nuevo warm-up).
- **Buffers Live temporales**: descartar el buffer de arranque/gap.
- **Estado de sincronizacion**: `market_data_state`, boundary/watermark, last_completed_live_candle.
- **Contadores/reconnect state**: reiniciar backoff; incrementar contador de re-warm-up (A.9).

`SignalEngine` es un COMPONENTE PROTEGIDO y hoy NO expone un metodo de reset. Dos opciones para
Tasks futuras (no implementar ahora):
- (a) **Preferida, sin tocar el componente protegido:** instanciar un `SignalEngine` NUEVO por
  `LiveSession` (descartar la instancia anterior) — el reset se logra por reemplazo, sin
  modificar la clase.
- (b) Añadir un metodo `reset()` explicito al `SignalEngine` -> [REQUIERE APROBACION —
  COMPONENTE PROTEGIDO] (comportamiento actual: sin reset; cambio minimo: metodo que reinicia
  `_prev_fast/_prev_slow`; riesgo de regresion: bajo pero toca Fase A).
La opcion (a) evita tocar el componente protegido y se recomienda para v1.

## A.13. Dashboard durante startup / recovery (cambio minimo)

El socio debe poder distinguir claramente, como minimo, estos estados de market data (junto al
estado de conexion Databento):

- `Databento: CONNECTED / Market Data: WARMING_UP`  (NO apto para señales)
- `Databento: CONNECTED / Market Data: SYNCING`     (NO apto para señales)
- `Databento: CONNECTED / Market Data: LIVE_SYNCED`  (apto para señales)
- `Databento: RECONNECTING / Market Data: STALE`     (NO apto para señales)
- `Databento: DISCONNECTED`                          (NO apto para señales)

Estados minimos a contemplar visualmente: `CONNECTED`, `WARMING_UP`, `SYNCING`/`RESYNCING`,
`LIVE_SYNCED`, `STALE`, `RECONNECTING`, `DISCONNECTED`.

Regla de comunicacion (dura): la palabra "CONNECTED" por si sola NUNCA debe comunicar que
"TradeCore esta listo para producir señales". La aptitud para señales se comunica exclusivamente
mediante `Market Data: LIVE_SYNCED`. Cambio de UI minimo (aditivo sobre el indicador de market
data ya previsto en secciones 8 y 20); no implementar en este turno.


---

# ADENDA B — Semantica temporal de las señales

> Estado: BORRADOR EN REVISION. Cierra el vacio de diseño (2) semantica temporal. Complementa
> las secciones 1–26 y la ADENDA A. Donde esta adenda contradiga texto anterior, PREVALECE esta
> adenda y el texto anterior queda reconciliado en la seccion "B.24 Auditoria de consistencia".

## B.14. Semantica temporal de una vela Databento (ohlcv-1m)

Evidencia recogida en el codigo actual del proyecto (Fase A):
- `src/schemas/candle.py`: `timestamp` = "UTC, **start of the minute** (from ts_event)".
- `src/connectors/databento_connector.py`: comentario "ts_event is the candle **open time**
  (start of the 1-min interval)"; se convierte `ts_event` (ns) -> datetime UTC.

Conclusion (segun la evidencia interna del proyecto): el timestamp de una vela `ohlcv-1m` que
TradeCore usa es la **APERTURA del intervalo de 1 minuto** (inicio del minuto), derivada de
`ts_event`. Es decir, una vela con timestamp `12:31:00Z` representa el minuto `[12:31:00,
12:32:00)` y esta CERRADA cuando el minuto ha transcurrido.

**[VERIFICADO — CANDLE TIMESTAMP SEMANTICS]**
Para el schema Databento `ohlcv-1m`, `ts_event` representa el inicio inclusivo del periodo de
agregacion de la barra. Por tanto: `signal_market_timestamp` = apertura del intervalo de mercado.
Evidencia: documentacion oficial Databento, OHLCV schema/data dictionary, verificada durante T0.1
(referencia completa disponible en el registro de evidencia de T0.1, no reproducida aqui).

**[SEGURO — LIVE BAR FINALITY REQUIRES VERIFICATION]**
La semantica de `ts_event` NO demuestra por si sola cuando Databento publica/entrega un registro
`ohlcv-1m` Live ni si el registro recibido representa una barra ya finalizada. Esta condicion debe
resolverse antes de implementar cualquier comportamiento que dependa materialmente de considerar
una vela Live como cerrada/elegible. Reglas: no inventar la respuesta; no marcar LIVE BAR FINALITY
como VERIFIED; no cerrar completamente G0.7 mientras englobe ambas cuestiones. Estado conceptual:
G0.7 = PARTIALLY_RESOLVED; TIMESTAMP_SEMANTICS = VERIFIED; LIVE_BAR_FINALITY = OPEN.

**Consistencia transversal obligatoria:** la convencion elegida (apertura del intervalo via
`ts_event`) debe ser IDENTICA en: histori­co (Fase A ya la usa), Live (`subscribe_live`
reutilizara la misma conversion), dashboard (Lightweight Charts ya recibe `int(timestamp)` epoch
de apertura), Capa 1 (`signal_market_timestamp`), Capas 2/3 y la futura comparacion con
TradingView. No debe existir mezcla de convenciones entre modulos.

## B.15. Dos timestamps minimos obligatorios por señal (congelado en Capa 1)

Se congelan DOS conceptos temporales separados y NO intercambiables en Capa 1:

- **`signal_market_timestamp`**: timestamp de la **vela de mercado** que origino la señal
  (apertura del intervalo, seccion B.14). Es la **referencia canonica** para: ubicacion en el
  grafico, alineacion con TradingView, analisis historico, ventanas MFE/MAE, relacion con
  observaciones posteriores (Capa 2) y features de Machine Learning.
- **`signal_emitted_at`**: timestamp UTC del instante en que TradeCore **termino de
  generar/publicar** la señal como evento operativo. Sirve para latencia, observabilidad y
  diagnostico operacional. **NUNCA sustituye** a `signal_market_timestamp`.

Ambos son obligatorios en Capa 1.

## B.16. Invariante signal_id <-> vela de mercado (texto normativo)

> "El `signal_id` queda asociado inmutablemente a la vela de mercado que origino la señal. Los
> timestamps operacionales de procesamiento, persistencia y publicacion se almacenan
> separadamente y nunca sustituyen al timestamp de mercado."

Ademas: `signal_market_timestamp` es **inmutable** una vez creada la fila de Capa 1 (append-only,
coherente con la inmutabilidad de Capa 1 de la seccion 13 / anti-data-leakage).

## B.17. Otros timestamps operacionales

- **`signal_emitted_at`** (obligatorio, seccion B.15).
- **`persisted_at`** (OPCIONAL en v1): instante en que Capa 1 quedo persistida. Util para
  auditar la politica "persist antes de publish" (seccion 15). Se recomienda incluirlo dado que
  la politica de persistencia es P1; queda como recomendado, no obligatorio.
- **`websocket_published_at`** (OPCIONAL, probablemente innecesario en v1): instante de envio por
  WebSocket. Solo añadir si aporta valor de diagnostico real; por defecto NO se incluye en v1
  para evitar campos de bajo valor.
- Regla dura: **NUNCA reutilizar un mismo campo con significados distintos.** Cada timestamp
  tiene un unico significado.
- **`created_at` de PostgreSQL:** si el motor añade automaticamente un `created_at` (default
  `now()`), su semantica es "instante de INSERT en la base" = un tiempo OPERACIONAL (equivalente
  o muy cercano a `persisted_at`). NO es tiempo de mercado y NUNCA se usa como
  `signal_market_timestamp`. Debe documentarse asi en el esquema para evitar confusion.

## B.18. SignalEngine protegido — manejo de `Signal.timestamp` (hallazgo + diseño sin tocarlo)

**Auditoria del comportamiento actual:**
- `src/engine/signal_engine.py` construye `Signal(..., timestamp=datetime.now(timezone.utc), ...)`.
  Es decir, HOY `Signal.timestamp` es el **reloj del sistema** en el momento de evaluar, NO el
  timestamp de la vela.
- `src/schemas/signal.py` documenta `timestamp` como "UTC, time of the candle that triggered it".
  **Discrepancia detectada:** el docstring afirma tiempo de mercado, pero la implementacion usa
  tiempo de sistema. En simulacion esto ya producia un timestamp de procesamiento (por eso el
  harness de Calibracion IGNORABA `Signal.timestamp` y usaba el timestamp de la vela).

**Decision de diseño (NO modificar el componente protegido):** el `signal_market_timestamp` NO
se obtiene de `Signal.timestamp`. Se obtiene del `Candle` que se acaba de procesar, en un
**wrapper/contexto de procesamiento Live** alrededor del motor:

```
closed Candle (con candle.timestamp = apertura del minuto)
   -> SignalEngine.evaluate(closes)            [componente protegido, SIN cambios]
   -> Signal (su .timestamp interno = system time; se trata como operacional, NO de mercado)
   -> Live processing wrapper/context:
         signal_market_timestamp = candle.timestamp     (vela que origino la señal)
         signal_emitted_at       = datetime.now(UTC)     (evento operativo)
   -> Signal Context (Capa 1)
```

- El `.timestamp` del objeto `Signal` queda **explicitamente mapeado** como tiempo operacional
  (puede reutilizarse como una aproximacion de `signal_emitted_at`, o registrarse aparte), y
  NUNCA como `signal_market_timestamp`.
- Este diseño NO requiere modificar `SignalEngine`. Si en el futuro se quisiera que
  `Signal.timestamp` sea el tiempo de la vela, seria [REQUIERE APROBACION — COMPONENTE PROTEGIDO]
  (archivo `signal_engine.py`, cambio: pasar el candle timestamp al `Signal`; riesgo de
  regresion: bajo pero toca Fase A y sus tests). Se PRIORIZA el diseño con wrapper que NO lo toca.

## B.19. Latencia (metrica operacional futura)

Con ambos timestamps disponibles se puede derivar en el futuro:

```
signal_latency = signal_emitted_at - signal_market_timestamp_close
```
(donde el cierre de la vela = `signal_market_timestamp` + 1 minuto, dado que el timestamp es la
apertura del intervalo — seccion B.14).

- `signal_latency` es una metrica **operacional** (observabilidad), NO un resultado economico ni
  un valor predictivo. No se confunde con MFE/MAE ni con P&L.
- NO es necesario mostrarla en el dashboard de Live v1. Queda disponible para diagnostico.
- Nunca se mezcla el market timestamp con la latencia de procesamiento.

## B.20. Capa 1 actualizada (esquema reconciliado)

Se ELIMINA cualquier campo ambiguo llamado solo `timestamp`. El campo `timestamp_utc` de la
seccion 12 se **renombra/desambigua** a `signal_market_timestamp`. Esquema minimo de Capa 1
(reconciliado; reemplaza la tabla de la seccion 12 en cuanto al tiempo):

| Campo | Tipo temporal | Notas |
|---|---|---|
| `signal_id` | — | UUID v4 (PK) |
| `live_session_id` | — | FK -> LiveSession |
| `signal_market_timestamp` | TIEMPO DE MERCADO | apertura de la vela que origino la señal (UTC); INMUTABLE; referencia canonica |
| `signal_emitted_at` | TIEMPO OPERATIVO | UTC en que TradeCore genero/publico la señal |
| `persisted_at` | TIEMPO OPERATIVO (opcional) | UTC de persistencia de Capa 1 |
| `direction` | — | BUY / SELL |
| `reference_price` | — | `close[t]` de la vela de mercado |
| `sma_fast` | — | SMA 9 en t |
| `sma_slow` | — | SMA 21 en t |
| `volume_at_t` | — | volumen de la vela en t |
| `atr_at_t` | — | research metadata (seccion 17); nullable |
| `instrument` / `timeframe` / `dataset` / `data_source` / `market_mode` | — | provenance (seccion 11) |
| `strategy_version` | — | obligatorio (seccion 16) |
| `application_version` | — | git SHA / version desplegada si disponible |
| `active_config` | — | snapshot inmutable de config relevante |

Documentacion explicita: `signal_market_timestamp` = **tiempo de mercado** (canonico);
`signal_emitted_at` / `persisted_at` / eventual `created_at` = **tiempos operativos**. No implementar migraciones/tablas.

## B.21. Implicancia para Capa 2

La regla "Capa 2 = informacion > t" define `t` inequivocamente como **`signal_market_timestamp`**
(NO `signal_emitted_at`). Toda observacion posterior (Capa 2) se relaciona temporalmente con
`signal_market_timestamp`; el constraint temporal anti-leakage (seccion 13) se expresa como
`observation_market_timestamp > signal_market_timestamp`.

## B.22. Implicancia para Capa 3

MFE, MAE, stop touches (candidato research), inverse crossover y futuras labels usan
**`signal_market_timestamp`** como referencia temporal de la señal para abrir sus ventanas.
NUNCA se inician las ventanas desde `signal_emitted_at` ni desde el tiempo del computador que
publico el WebSocket. Esto es coherente con el marco MFE/MAE de Calibracion (que ancla en la
vela de mercado, no en el reloj de proceso).

## B.23. TradingView — alineacion temporal

La futura comparacion temporal TradeCore <-> TradingView se realizara **por la vela de mercado
correspondiente** (`signal_market_timestamp`), NO comparando timestamps de llegada de mensajes.
Los tiempos de emision (`signal_emitted_at`) pueden conservarse para analizar latencia, pero es
una pregunta SEPARADA de la alineacion de la señal en el grafico.

---

## B.24. Auditoria de consistencia completa (revision de 26 secciones + ADENDA A)

Revision explicita y reconciliaciones aplicadas (no solo anexos):

- **Estados (secciones 8 y A.2):** consistentes. La maquina incorpora
  `WARMING_UP`, `SYNCING`, `LIVE_SYNCED`, `RESYNCING`, `STALE`, `RECONNECTING`, `DISCONNECTED`.
  Invariante unico de habilitacion de señales: `LIVE_SYNCED`. Sin estados inaccesibles: todos
  son alcanzables desde la secuencia de arranque (A.2) o desde el ciclo de gap (A.10/A.11).
- **Capa 1 (secciones 12 y B.20):** RECONCILIADA. El antiguo `timestamp_utc` se reemplaza por
  `signal_market_timestamp` + `signal_emitted_at`. No queda ningun campo ambiguo `timestamp`.
- **Anti-data-leakage (secciones 13 y B.21):** RECONCILIADO. `t` = `signal_market_timestamp`; el
  constraint temporal usa el timestamp de mercado, no el operativo.
- **Persistencia (seccion 15):** la politica "persist antes de publish" preserva AMBOS tiempos
  (`signal_market_timestamp` inmutable + `signal_emitted_at`; opcional `persisted_at`). Trazabilidad
  por `signal_id` intacta.
- **Dashboard (secciones 20 y A.13):** representa `WARMING_UP`/`SYNCING` como NO aptos para
  señales; "CONNECTED" nunca comunica aptitud. Consistente.
- **Separacion LIVE/SIMULATION (seccion 2):** intacta; ningun camino de warm-up/gap introduce
  fallback a Simulation. Las velas Historical de warm-up/recovery son datos reales de mercado
  (no Simulation), marcadas con su `data_source` de procedencia.
- **SignalEngine protegido (seccion B.18):** el diseño NO lo modifica (wrapper para timestamps;
  reset por reinstanciacion, A.12). No hay modificacion implicita del motor.

### Barrido de riesgos (item 26) — resultado

| Riesgo auditado | Resultado |
|---|---|
| Contradicciones internas | Ninguna pendiente (reconciliaciones B.24 aplicadas) |
| Estados inaccesibles | Ninguno |
| Camino que permita señal antes de `LIVE_SYNCED` | Ninguno (invariante A.2 + orden atomico A.11) |
| Reanudar tras gap irrecuperable sin warm-up | Imposible (secuencia congelada A.11) |
| Mezcla Historical/Live | Controlada por boundary/merge/dedup (A.7); sin hueco/duplicacion |
| Timestamp ambiguo | Eliminado (B.20) |
| Timestamp operacional usado como market timestamp | Prohibido (B.16/B.18) |
| Campos duplicados con distinta semantica | Prohibido (B.17) |
| Contradiccion con separacion LIVE/SIMULATION | Ninguna |
| Contradiccion con anti-data-leakage | Ninguna (B.21) |
| Modificacion implicita del SignalEngine | Ninguna (B.18) |

`DESIGN_FULL_CONSISTENCY_AUDIT = CLEAN`.

---

## B.25. Tests añadidos al Design (diseñar, no escribir)

Se añaden a la seccion 22 los siguientes tests futuros:
- **no signals during warm-up**: durante `WARMING_UP`/`SYNCING` ningun crossover se publica/persiste como señal Live.
- **warm-up state reconstruction**: tras warm-up, `SignalEngine` tiene `_prev_fast/_prev_slow` validos para detectar el proximo cruce.
- **startup Historical<->Live continuity**: sin hueco/duplicacion entre ultima vela historica y primera vela Live.
- **boundary merge/dedup**: merge Historical+bufferedLive deduplica por timestamp y ordena.
- **re-warm-up after irrecoverable gap**: gap > umbral fuerza cierre de sesion + nuevo warm-up completo; sin reanudacion con estado previo.
- **SignalEngine reset**: nueva `LiveSession` -> instancia nueva (o reset) sin estado de cruce heredado.
- **first eligible Live signal**: la primera señal Live proviene de una vela nueva post-`LIVE_SYNCED`.
- **signal_market_timestamp correcto**: igual al timestamp (apertura) de la vela que origino la señal.
- **signal_emitted_at separado**: distinto campo, tiempo operativo, no sustituye al de mercado.
- **market timestamp inmutable**: intentar mutar `signal_market_timestamp` tras crear Capa 1 -> falla.
- (Se mantienen los tests ya listados en seccion 22 y los 12 spec-ahead del connector.)

## B.26. READY FOR TRADER REVIEW — condiciones añadidas

Se añaden a la seccion 23 (todas deben cumplirse):
17. Startup warm-up probado (estado matematico reconstruido correctamente).
18. Ninguna señal antes de `LIVE_SYNCED`.
19. Gap irrecuperable obliga re-warm-up completo (sin reanudacion con estado previo).
20. `signal_market_timestamp` correctamente asociado a la vela de mercado.
21. `signal_emitted_at` registrado por separado (tiempo operativo).
22. Dashboard distingue `CONNECTED` de `WARMING_UP`/`SYNCING`/`LIVE_SYNCED`.

---

## B.27. Decisiones [SEGURO] pendientes (lista actualizada, no cerradas)

1. Parametros de reconexion Live (intentos, delays, `max_delay`, intervalo de reset) — seccion 4.
2. Config del SDK Live (`reconnect_policy`, `heartbeat_interval_s`) — seccion 3.
3. `MAX_AUTO_RECOVERABLE_GAP` (umbral de gap recuperable) — seccion 5 / A.10-A.11.
4. Estrategia/tope de la consulta Historical de warm-up (retroceso maximo para reunir N velas) — seccion A.5.
5. Costo actual de Databento — [SEGURO — COSTO ACTUAL REQUIERE VERIFICACION] — seccion A.9.
6. **Semantica del timestamp de vela** — PARCIALMENTE RESUELTO (T0.1): TIMESTAMP_SEMANTICS = VERIFICADO ([VERIFICADO — CANDLE TIMESTAMP SEMANTICS], seccion B.14); LIVE_BAR_FINALITY = OPEN ([SEGURO — LIVE BAR FINALITY REQUIRES VERIFICATION], seccion B.14). G0.7 = PARTIALLY_RESOLVED.
7. Politica exacta ante fallo de persistencia (reintentos/backoff; publicar-marcada vs no-publicar) — seccion 15.
8. Detalles de despliegue de PostgreSQL (RDS vs contenedor, version, esquema fisico) — seccion 14.
9. Literal/mecanismo exacto de `strategy_version` (p.ej. `SMA_9_21_v1`) — seccion 16.
10. Duracion de la ventana de estabilidad Live para READY — seccion 23.
11. Mecanismo de inyeccion de `application_version`/git SHA — seccion 10.
12. Umbral de frecuencia de re-warm-up `MAX_WARMUP_EVENTS` (alerta de salud de conexion) — seccion A.9.

Ninguna de estas se cierra en este turno; todas quedan pendientes antes de Tasks/implementacion.

---

# ADENDA C — Politica de persistencia: persist-before-publish (Capa 1)

> Estado: BORRADOR EN REVISION. Cierra la ultima decision arquitectonica pendiente: la persistencia
> obligatoria de Capa 1 ANTES de publicar una senal Live formal. Complementa las secciones 1-26,
> ADENDA A y ADENDA B. Donde contradiga texto anterior, PREVALECE esta adenda; las reconciliaciones
> se listan en "C.19 Revision acotada de consistencia". Los valores numericos concretos permanecen
> [SEGURO — REQUIERE CONFIRMACION]; la arquitectura e invariantes SI quedan cerradas.

## C.1. Principio obligatorio: persist-before-publish (invariante)

Invariante arquitectonica dura:

> Toda senal Live mostrada durante una sesion formal debe tener PREVIAMENTE un Signal Context
> (Capa 1) DURADERO asociado a su `signal_id`.

Secuencia obligatoria:

```
closed market candle
   -> SignalEngine genera BUY/SELL          (motor protegido, sin cambios)
   -> crear signal_id (UUID v4)
   -> crear Signal Context (Capa 1)
   -> persistir en PostgreSQL
   -> COMMIT confirmado                       (no basta con ejecutar el INSERT)
   -> [Publication Gate]  -> publicar BUY/SELL
   -> WebSocket
   -> dashboard
```

La publicacion SOLO queda autorizada cuando la transaccion de persistencia fue **confirmada
exitosamente** (COMMIT). Un INSERT sin COMMIT confirmado NO habilita publicacion.

Invariante permanente: `VISIBLE_SIGNAL => DURABLE_SIGNAL_CONTEXT`. Nunca debe existir una senal
visible cuya Capa 1 no pueda recuperarse posteriormente por `signal_id`.

## C.2. Publication Gate (compuesto)

Gate logico explicito para señales Live:

```
SIGNAL_PUBLICATION_ENABLED = (Market Data == LIVE_SYNCED) AND (Persistence == HEALTHY)
```

- Si cualquiera deja de cumplirse: `SIGNAL_PUBLICATION = BLOCKED`.
- Es INDEPENDIENTE de que el SignalEngine siga calculando internamente (el motor puede seguir
  actualizando SMAs y su estado de cruce; lo que se bloquea es la PUBLICACION/persistencia formal).
- Regla de separacion (ver C.11): NO se mezcla el estado de mercado (`market_data_state`) con el
  estado de persistencia (`persistence_state`). Son dos ejes de salud independientes; el gate es su AND.

## C.3. Fallo de INSERT / COMMIT

Si la persistencia de Capa 1 falla (INSERT o COMMIT no confirmado), la senal:
- NO se publica al WebSocket;
- NO aparece en el dashboard;
- NO se considera una senal Live formal observada por el socio;
- NO puede aparecer como si hubiese sido publicada correctamente.

Se diseñan **retries limitados** (reintentos acotados de la persistencia). Valores concretos
(numero de retries, intervalos/backoff, timeout total) -> [SEGURO — REQUIERE CONFIRMACION].

## C.4. Idempotencia obligatoria

Los retries reutilizan EXACTAMENTE el mismo `signal_id` (nunca un UUID nuevo por intento).

Caso critico de ACK perdido (commit ambiguo):
```
TradeCore -> INSERT -> PostgreSQL COMMIT realmente ocurre -> se pierde la confirmacion
          -> TradeCore cree que fallo -> retry
```
El retry NO puede crear una segunda senal. Diseño idempotente (conceptual, sin SQL):
- `signal_id` es PK / unique key de Capa 1;
- la operacion de persistencia es un **upsert idempotente por `signal_id`** (p.ej. INSERT que ante
  conflicto de PK no duplica y confirma el estado existente), de modo que un retry sobre una fila
  ya commiteada resulta en "ya persistida" y habilita publicacion una sola vez;
- misma Capa 1: mismo `signal_id`, mismo `signal_market_timestamp`, mismo contenido inmutable;
- verificacion segura ante retry: tras un ACK perdido, TradeCore puede **comprobar existencia por
  `signal_id`** antes/como parte del retry; si ya existe commiteada, se trata como exito (no re-inserta,
  no re-publica duplicado).
No se implementa SQL en este turno.

## C.5. Señales durante PERSISTENCE_DEGRADED

Si PostgreSQL permanece indisponible con `Market Data = LIVE_SYNCED` y `Persistence = DEGRADED`,
TradeCore PUEDE continuar (si es seguro):
- recibiendo velas;
- actualizando `CandleBuffer`;
- actualizando SMA 9/21;
- manteniendo el estado matematico del `SignalEngine`.

PERO: las nuevas señales Live formales permanecen BLOQUEADAS (no publicadas, no persistidas como
formales). Esto evita romper la continuidad matematica del motor solo porque fallo la base de datos
(ver C.8).

## C.6. Crossover detectado durante la falla (supresion + cierre de ciclo de vida)

Ejemplo: 14:31 SignalEngine detecta BUY mientras PostgreSQL esta caido.

- **A. Retry inmediato/acotado:** mientras la senal permanezca dentro de la politica temporal de
  retry aprobada ([SEGURO — REQUIERE CONFIRMACION]), se sigue intentando la persistencia con el
  MISMO `signal_id`. Si logra COMMIT dentro de esa ventana, se publica normalmente por el gate.
- **B. Retry agotado / senal ya no oportuna:** agotada la politica:
  - NO publicar retrospectivamente esa senal en el dashboard;
  - NO presentarla despues como si acabara de producirse;
  - NO generar un nuevo `signal_id` para rescatarla.
  Se marca como `SUPPRESSED_DUE_TO_PERSISTENCE` (estado de observabilidad/auditoria). **No es una
  senal Live publicada.** No hay publicacion tardia engañosa.

**Cierre de ciclo de vida del `signal_id` suprimido (obligatorio):** una vez que una senal alcanza
`SUPPRESSED_DUE_TO_PERSISTENCE` (retry agotado sin COMMIT confirmado), su `signal_id` queda
**definitivamente cerrado**. Ningun mecanismo posterior (recuperacion de PostgreSQL, reprocesamiento,
reconexion) puede reutilizar ese `signal_id` para persistir o publicar retroactivamente. La vela que
origino esa senal NO se reevalua para la misma senal (ya fue procesada; el pipeline procesa cada vela
cerrada una sola vez — orden atomico A.11). Queda EXPLICITAMENTE descartado que una reevaluacion futura
de esa misma vela reactive el `signal_id` suprimido: cualquier senal futura seria una senal COMPLETAMENTE
distinta, con su propio `signal_id` nuevo, nunca una reactivacion de la suprimida.

**Registro tecnico sin distorsionar Capa 1:** el evento suprimido puede registrarse como evento
tecnico/observabilidad (log estructurado y/o una tabla/registro de eventos operacionales SEPARADO de
Capa 1), con su `signal_id`, `signal_market_timestamp`, `signal_generated_at` y motivo
`SUPPRESSED_DUE_TO_PERSISTENCE`. Esto NO lo convierte en Capa 1 formal ni en senal publicada; sirve
para la observabilidad de C.14. Capa 1 permanece reservada exclusivamente para señales con contexto
durable confirmado.

## C.7. Recuperacion de PostgreSQL

Transicion de salud de persistencia:
```
PERSISTENCE_HEALTHY -> fallo sostenido -> PERSISTENCE_DEGRADED
                    -> health/recovery checks -> PERSISTENCE_RECOVERING -> PERSISTENCE_HEALTHY
```
- Intervalos de health-check y umbral de recuperacion -> [SEGURO — REQUIERE CONFIRMACION].
- **Evidencia minima para volver a HEALTHY:** una operacion real de escritura+lectura verificada con
  exito (p.ej. un write/read de sondeo confirmado por COMMIT), no solo "el socket responde". La
  definicion exacta de la sonda -> [SEGURO — REQUIERE CONFIRMACION], pero debe ser una verificacion
  efectiva de durabilidad, no un ping superficial.
- El retorno a HEALTHY **NO** provoca publicacion retroactiva automatica de señales que ya dejaron de
  ser elegibles (las `SUPPRESSED_DUE_TO_PERSISTENCE` estan cerradas, C.6). A partir de la recuperacion,
  las NUEVAS señales vuelven a seguir `persist -> commit -> publish`.

## C.8. Relacion con SignalEngine (separacion de fallas)

NO se resetea el SignalEngine solo porque PostgreSQL estuvo temporalmente indisponible, siempre que:
- `Market Data` permaneciera `LIVE_SYNCED`;
- no existiera gap de mercado;
- `CandleBuffer` continuara cronologicamente correcto.

Separacion explicita de dos problemas distintos:
```
Databento gap irrecuperable        -> reset / re-warm-up (ADENDA A.11)
PostgreSQL outage sin market gap   -> NO reset matematico; solo se bloquea la publicacion
```
La falla de persistencia y la perdida de continuidad de mercado son problemas independientes y se
tratan por separado.

## C.9. Revision de timestamps operacionales (se añade `signal_generated_at`)

Persist-before-publish introduce una diferencia temporal adicional (la persistencia ocurre entre la
generacion y la publicacion). Se distinguen ahora TRES instantes (mas los operativos de persistencia):

1. `signal_market_timestamp` — la vela de mercado que origino la senal (apertura del intervalo; ADENDA B.14). **Referencia financiera principal, inmutable.**
2. `signal_generated_at` — instante UTC en que el SignalEngine produjo el evento matematico (crossover detectado). **NUEVO.**
3. `signal_emitted_at` — instante UTC en que, DESPUES de la persistencia confirmada (COMMIT), la senal se publico como evento Live.

Se AÑADE `signal_generated_at` para no dejar dos eventos temporales distintos (generacion vs
publicacion) compartiendo ambiguamente `signal_emitted_at`. `signal_emitted_at` pasa a significar
inequivocamente "publicada tras COMMIT" (posterior a `signal_generated_at`). `signal_market_timestamp`
sigue siendo la referencia financiera principal. (Los nombres son propuestos; lo obligatorio es que
generacion y publicacion NO compartan campo.)

## C.10. Publicacion falla DESPUES de persistir (asimetria opuesta)

```
Capa 1 COMMIT OK -> WebSocket publish FAIL
```
Esto NO viola `VISIBLE_SIGNAL => PERSISTED` (la senal SI esta persistida; simplemente no llego a verse).
Tratamiento (sin ampliar alcance):
- registrar/loguear el fallo de publicacion (observabilidad);
- reenvio seguro por `signal_id` si corresponde al protocolo (el dashboard ya de-duplica marcadores por
  identidad de senal; ver seccion N/ handleSignal), de modo que un reintento de publicacion **no
  duplica** el marcador;
- nunca duplicar marcadores por retry.
No se diseña una arquitectura compleja de mensajeria para Live v1. Lo esencial es que el Design
distinga explicitamente **persist failure** (bloquea publicacion) de **publish failure** (la senal ya
es durable; solo se reintenta mostrarla, idempotentemente).

## C.11. Estado operacional de persistencia (eje de salud independiente)

Nuevo eje de salud, SEPARADO del estado de market data:
```
Persistence: HEALTHY | DEGRADED | RECOVERING
```
No se sobrecarga `LIVE_SYNCED`. Es perfectamente posible y debe representarse:
```
Market Data: LIVE_SYNCED
Persistence: DEGRADED
Signal Publication: BLOCKED
```
= sincronizado con el mercado pero sin poder producir señales formales trazables.

## C.12. Dashboard — PERSISTENCE_DEGRADED visible (obligatorio)

El socio debe distinguir "no aparecen señales porque no hubo crossover" de "no aparecen señales porque
TradeCore esta bloqueando su publicacion por una falla tecnica". Bloque de salud minimo (aditivo a los
indicadores de ADENDA A.13):

Estado sano:
```
Browser        CONNECTED
Databento      CONNECTED
Market Data    LIVE_SYNCED
Signal Record  HEALTHY
Signals        ENABLED
```
Ante falla de persistencia:
```
Browser        CONNECTED
Databento      CONNECTED
Market Data    LIVE_SYNCED
Signal Record  DEGRADED
Signals        BLOCKED
```
Debe comunicar inequivocamente: (1) persistencia degradada; (2) señales bloqueadas por razon tecnica;
(3) mercado/Databento pueden seguir funcionando. NO mostrar stack traces ni errores SQL. No implementar UI.

## C.13. Copy durante falla

Copy conceptual (adaptable a UX, sin violar las reglas de copy de la seccion 19):
- "Registro de señales: degradado"
- "Nuevas señales: temporalmente pausadas"

Prohibido: decir "Sin señales" (se confundiria con ausencia matematica de crossover); afirmar que el
mercado esta detenido si no lo esta; implicar perdida de conectividad Databento si Databento sigue
conectado. Se mantienen las reglas previas sobre no afirmar rentabilidad.

## C.14. Observabilidad de intervalos/señales suprimidas

Consecuencia importante: una ventana con `Signal Publication = BLOCKED` NO debe interpretarse despues
como evidencia de que la estrategia SMA no genero señales — es una ventana **tecnicamente degradada**.

Debe existir observabilidad suficiente para distinguir posteriormente:
- **no mathematical signal** (el SMA no cruzo), de
- **mathematical signal occurred but publication was suppressed due to persistence**
  (`SUPPRESSED_DUE_TO_PERSISTENCE`).

Informacion minima para esa distincion (SIN convertir una senal suprimida en senal formal publicada):
registro de eventos operacionales separado de Capa 1 (C.6) con `signal_id`, `signal_market_timestamp`,
`signal_generated_at`, motivo de supresion, y marca de la ventana degradada (inicio/fin de
`PERSISTENCE_DEGRADED`). Asi el analisis posterior sabe que esa ventana no es representativa del
comportamiento de la estrategia.

## C.15. Capa 1 — actualizacion

Se refleja en Capa 1 (reconciliando la tabla de ADENDA B.20):
- **Creacion ANTES de publicacion** (persist-before-publish).
- **Persistencia durable + COMMIT confirmado** como condicion para existir formalmente.
- **Idempotencia por `signal_id`** (PK/unique; upsert idempotente).
- **Timestamps reconciliados:** se añade `signal_generated_at`; junto a `signal_market_timestamp`
  (mercado, inmutable) y `signal_emitted_at` (publicacion tras COMMIT). Opcional `persisted_at`.
- **Inmutabilidad:** Capa 1 sigue siendo append-only e inmutable; `signal_market_timestamp` inmutable.
- Capa 1 NO se convierte en un registro mutable de estados posteriores. El estado transitorio de una
  senal en proceso de persistencia/retry, y los eventos `SUPPRESSED_DUE_TO_PERSISTENCE`, viven en
  observabilidad/eventos operacionales SEPARADOS, no como campos mutables de Capa 1. Una fila de Capa 1
  existe si y solo si su contexto quedo durable (COMMIT confirmado).

Esquema temporal minimo de Capa 1 tras esta adenda:
`signal_id`, `live_session_id`, `signal_market_timestamp` (mercado, inmutable),
`signal_generated_at` (operativo), `signal_emitted_at` (operativo, post-COMMIT),
`persisted_at` (operativo, opcional), + campos financieros/provenance ya aprobados.

## C.16. Pipeline / WebSocket (actualizado)

```
Candle (cerrada)
   -> SignalEngine (SMA 9/21, intacto)
   -> Signal candidate (+ signal_generated_at)
   -> Signal Context (Capa 1, signal_id)
   -> PostgreSQL COMMIT confirmado
   -> Publication Gate  (LIVE_SYNCED AND PERSISTENCE_HEALTHY)
   -> WebSocket  (+ signal_emitted_at)
   -> Dashboard
```
El SignalEngine SMA 9/21 permanece intacto (el gate y la persistencia son wrappers alrededor del motor;
ver ADENDA B.18). El orden atomico por vela (ADENDA A.11) se mantiene: gap-check -> evaluacion ->
(candidato) -> persist/commit -> gate -> publish.

## C.17. Tests de Design (diseñar, no implementar)

Se añaden a la seccion 22 / B.25:
- no publish before persistence commit;
- publish after successful commit;
- failed INSERT blocks publication;
- failed COMMIT blocks publication;
- retry reuses same `signal_id`;
- retry does not duplicate Signal Context;
- ambiguous commit acknowledgement handled idempotently (ACK perdido -> no duplica);
- prolonged DB outage -> `PERSISTENCE_DEGRADED`;
- market processing can continue while publication blocked;
- recovery -> future signals publish normally;
- old suppressed signal is NOT published retroactively;
- suppressed `signal_id` is permanently closed (no reactivation);
- SignalEngine is NOT reset solely due to DB outage;
- dashboard receives persistence health state;
- dashboard shows Signals BLOCKED during degradation;
- persistence degradation distinguishable from no-crossover;
- visible signal always recoverable by `signal_id`;
- publish failure after successful persistence does not create duplicate Context/marker;
- timestamp semantics remain correct under persistence delay (`signal_generated_at` vs `signal_emitted_at`).

## C.18. READY FOR TRADER REVIEW — condiciones añadidas

Se añaden a la seccion 23 / B.26 (todas obligatorias):
23. Every visible signal has durable Signal Context (COMMIT confirmado).
24. Persistence failure blocks publication.
25. Persistence state visible independientemente del market state.
26. El socio puede distinguir supresion tecnica de comportamiento lateral/sin-senal del mercado.
27. Retries son idempotentes (mismo `signal_id`, sin duplicados).
28. Señales suprimidas antiguas no se publican retroactivamente.
29. La recuperacion restaura publicacion solo para señales nuevas/elegibles.
30. Un outage de persistencia por si solo no corrompe/resetea el estado SMA.
31. La semantica de timestamps permanece inequivoca a traves de persist-before-publish.

## C.19. Revision acotada de consistencia (secciones afectadas)

- **Capa 1 (secciones 12, B.20, C.15):** RECONCILIADA. Se añade `signal_generated_at`; Capa 1 existe solo
  con contexto durable; permanece inmutable/append-only; estados transitorios y supresiones viven fuera de Capa 1.
- **Persistencia (secciones 14, 15, C.1-C.7):** la seccion 15 (persist-before-publish, ya propuesta) se
  ELEVA a invariante duro y se detalla (gate compuesto, idempotencia, supresion, recuperacion). PostgreSQL
  (seccion 14) sigue siendo la tecnologia; sin contradiccion.
- **Pipeline/WebSocket (secciones 16, C.16):** actualizado con Publication Gate + COMMIT; SignalEngine intacto.
- **Timestamps operacionales (ADENDA B, C.9):** RECONCILIADO. `signal_emitted_at` ahora = publicacion
  post-COMMIT; `signal_generated_at` = evento matematico. Sin campo compartido ambiguo. `signal_market_timestamp`
  sigue como referencia financiera.
- **Estados de salud (secciones 8, A.13, C.11):** se añade el eje `Persistence: HEALTHY/DEGRADED/RECOVERING`,
  independiente de `market_data_state`. `Signal Publication` (ENABLED/BLOCKED) es funcion del gate compuesto.
- **Dashboard (secciones 20, A.13, C.12-C.13):** añade `Signal Record` y `Signals` (ENABLED/BLOCKED) + copy; aditivo.
- **Tests (secciones 22, B.25, C.17)** y **READY (secciones 23, B.26, C.18):** ampliados.

Contradiccion detectada y corregida: la seccion 15 original ofrecia como "alternativa [SEGURO]" publicar
la senal marcada "no persistida". Esa alternativa queda **DESCARTADA** por el invariante C.1
(`VISIBLE_SIGNAL => DURABLE_SIGNAL_CONTEXT`): NUNCA se publica una senal sin Capa 1 durable. Lo que queda
[SEGURO] son solo los VALORES temporales de retry/timeout, no la posibilidad de publicar sin persistir.

Barrido de riesgos: sin camino a senal visible sin Capa 1 durable; sin publicacion retroactiva de
suprimidas; sin reset del motor por outage de DB; sin campo temporal ambiguo; separacion LIVE/SIMULATION
y anti-data-leakage intactas; SignalEngine sin modificacion implicita. `DESIGN_FULL_CONSISTENCY_AUDIT = CLEAN`.

## C.20. Decisiones [SEGURO] pendientes (añadidas a B.27)

13. Numero de retries de persistencia.
14. Intervalo/backoff de retries.
15. Timeout total de persistencia por senal.
16. Maxima antiguedad publicable de una senal pendiente (ventana de oportunidad antes de SUPPRESSED).
17. Intervalo de health-check de persistencia.
18. Umbral/evidencia exacta de recuperacion (definicion de la sonda write+read).

Todas [SEGURO — REQUIERE CONFIRMACION]. La arquitectura e invariantes quedan cerradas; solo faltan estos valores.
