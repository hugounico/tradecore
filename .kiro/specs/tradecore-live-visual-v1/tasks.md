# Implementation Plan

## Overview

TradeCore Live Visual v1 — plan de tareas derivado estrictamente del Design aprobado.

> Fuente arquitectonica UNICA: `.kiro/specs/tradecore-live-visual-v1/design.md` (APPROVED,
> incl. ADENDA A warm-up, ADENDA B timestamps, ADENDA C persist-before-publish).
> Rama: `feature/live-dashboard-demo` (base `98e3e9fc2da773410dad6f1cc5e491de52e2e211`).
> Este documento es SOLO planificacion. No implementa codigo, no ejecuta tests/Validation/OOS,
> no hace commit/push. Ninguna Task amplia el alcance del Design.
>
> Convencion de estructura por Task: ID, Objetivo, Archivos (nuevos / existentes-no-protegidos /
> protegidos), Componentes protegidos afectados, Dependencias, Gate [SEGURO] aplicable,
> Criterio de aceptacion, Tests esperados, Evidencia esperada, `REQUIRES_EXPLICIT_APPROVAL`.
>
> Componentes PROTEGIDOS (solo se tocan en Wave 6, con aprobacion explicita):
> `src/api/app.py`, `dashboard/index.html`, `src/api/throttled_pusher.py`,
> `src/connectors/databento_connector.py`. Tambien protegido: `SignalEngine` (SMA 9/21) — NO se
> modifica; el reset se hace por reinstanciacion (Design A.12). Fase A / Calibration: intactos.
>
> Regla de separacion (Design seccion 5 / ADENDA): Waves 1-5 construyen y prueban piezas nuevas
> NO protegidas; Wave 6 realiza la integracion minima en protegidos. Si una Task 1-5 depende de
> una futura modificacion protegida -> `INTEGRATION_DEFERRED_TO_WAVE_6 = YES`. Si una pieza NO
> puede construirse/probarse de forma aislada sin tocar un protegido -> DETENERSE y reportar
> discrepancia Design<->implementacion (Design seccion 5, caso excepcional).

---

## Tasks

# WAVE 0 — Verificaciones, decisiones y gates (NO codigo)

Wave 0 contiene exclusivamente verificaciones documentales, decisiones [SEGURO] y gates. No se
implementa codigo. Los Gates consolidan TODOS los [SEGURO] del Design.

## T0.1 — Verificar semantica oficial del timestamp `ohlcv-1m` / `ts_event` (PRIMERA Task obligatoria)
- **Objetivo:** determinar, con evidencia de la documentacion OFICIAL vigente de Databento, que
  representa el timestamp de una vela `ohlcv-1m` (`ts_event`): apertura del intervalo, cierre,
  event time u otra semantica.
- **Archivos:** nuevos (nota de evidencia dentro de la Spec, p.ej. `research-notes/`); NINGUNO de
  produccion. Existentes no protegidos: NONE. Protegidos: NONE.
- **Componentes protegidos afectados:** NONE.
- **Dependencias:** ninguna (es la primera Task del documento).
- **Gate [SEGURO]:** G0.7 (candle timestamp semantics).
- **Criterio de aceptacion:** conclusion explicita (apertura / cierre / event time / otra) RESPALDADA
  por cita/enlace de documentacion oficial de Databento. NO se acepta como prueba suficiente:
  comentarios existentes en TradeCore, inferencias del codigo, ni comportamiento observado.
  Documentar la implicancia para: Historical, Live, `signal_market_timestamp`, dashboard y TradingView.
- **Tests esperados:** verificacion documental (no test de codigo).
- **Evidencia esperada:** cita/enlace a doc oficial + nota escrita con la conclusion y sus implicancias;
  actualizacion del Design (resolver el marcador `[SEGURO — CANDLE TIMESTAMP SEMANTICS REQUIRES VERIFICATION]`).
- **REQUIRES_EXPLICIT_APPROVAL:** NO (es investigacion documental; su resultado alimenta G0.7).

## Decision Gates (consolidacion de todos los [SEGURO] del Design)

Formato de cada Gate: decision requerida | por que | opciones (si el Design las identifica) |
Waves/Tasks bloqueadas | momento maximo de resolucion | clasificacion.
Clasificacion: `BLOCKS_IMPLEMENTATION` / `BLOCKS_PRODUCTION` / `BLOCKS_TRADER_REVIEW`.

- **G0.1 — Reconnect policy Live (retry count / delays / backoff / max_delay / reset interval).**
  Por que: gobierna `reconnect()` y estabilidad Live. Opciones: reutilizar criterio MVP (3 intentos
  1s/2s/4s) vs otro. Bloquea: T1.4, y la integracion Live de W6. Resolver antes de W1 implementacion
  de reconnect. Clasificacion: BLOCKS_IMPLEMENTATION. ESTADO: RESOLVED (decision TradeCore, Design seccion 4.1): ReconnectPolicy.NONE + politica propia 3 intentos, delays 1s/2s/4s, fatal sin retry.
- **G0.2 — Databento SDK Live config (`reconnect_policy`, `heartbeat_interval_s`).** Por que: parametros
  de conexion del SDK. Bloquea: T1.4 y W6 connector. Resolver antes de W1. BLOCKS_IMPLEMENTATION. ESTADO: RESOLVED (decision TradeCore, Design seccion 4.2): reconnect_policy=ReconnectPolicy.NONE, heartbeat_interval_s=None.
- **G0.3 — `MAX_AUTO_RECOVERABLE_GAP` (umbral de gap recuperable).** Por que: decide recovery vs
  re-warm-up. Bloquea: T2.7/T2.8. Resolver antes de W2 gap tasks. BLOCKS_IMPLEMENTATION.
- **G0.4 — Estrategia de consulta Historical de warm-up + tope de retroceso para reunir N velas.**
  Por que: definir "recuperar >= N velas cerradas validas" y su tope. Bloquea: T2.2. Resolver
  antes de W2. BLOCKS_IMPLEMENTATION.
- **G0.5 — Costo actual Databento (Live plan Standard + Historical warm-up).** Por que: viabilidad
  economica del stream Live y de las consultas de warm-up/recovery. Bloquea: despliegue Live formal.
  Resolver antes de W8/produccion. BLOCKS_PRODUCTION.
  SOURCE_DESIGN_SECTION = Design seccion A.9 (Coste y latencia del warm-up; costo marcado
  [SEGURO — COSTO ACTUAL REQUIERE VERIFICACION]); refuerzo en steering `databento-architecture.md`
  ("Fase B ... requiere plan Standard activo") y `tradecore-mvp/requirements.md` (tabla de riesgos:
  plan Standard tiene costo mensual). ORIGIN = EXPLICIT_SEGURO.
- **G0.6 — Databento plan Standard activo + levantamiento del GATE Fase B.** Por que: el streaming Live
  esta bloqueado por separado. Bloquea: cualquier ejecucion Live real (W6 connector integration real, W8).
  Resolver antes de W6 integracion real / W8. BLOCKS_PRODUCTION.
  SOURCE_DESIGN_SECTION = steering `databento-architecture.md`, apartado "Fase B — Streaming live
  (requiere plan Standard activo)" y su "GATE explicito entre Fase A y Fase B: requiere aprobacion
  manual del usuario y activacion del plan Standard antes de continuar"; reforzado por steering
  `fase-actual-etapa1.md` ("Fase B ... sigue BLOQUEADA y pendiente de aprobacion por separado; su GATE se
  mantiene intacto") y `tradecore-mvp/requirements.md` supuesto 7. ORIGIN = APPROVED_RESTRICTION
  (restriccion de gobernanza CONGELADA, no una hipotesis comercial). El plan Standard SI esta exigido
  explicitamente por el steering aprobado, por lo que se conserva el nombre del Gate (no se reformula a
  "verify entitlement" generico). NO se investiga precio ni se consulta Databento en este turno.
  - **G0.6-EXC-1 — END_OF_INTERVAL_DIAGNOSTIC_PROBE_ONLY (excepcion acotada de G0.6).**
    ESTADO: `G0.6-EXC-1 = AUTHORIZED_NOT_USED`. G0.6 permanece `OPEN` (esta excepcion NO resuelve,
    cierra ni levanta G0.6). Alcance: autoriza UNA (1) ejecucion diagnostica, manual, temporal, aislada
    y FUERA del repositorio, cuyo unico objetivo es observar la relacion entre
    `SystemMsg(END_OF_INTERVAL).ts_event` y `OHLCV(ohlcv-1m).ts_event` para obtener evidencia empirica
    destinada a resolver `LIVE_BAR_FINALITY` (actualmente CASE_B).
    Configuracion BLOQUEADA (EXCEPTION_CONFIGURATION_LOCKED = YES): `PROBE_MODE = LIVE_REAL`;
    `PROBE_DATASET = GLBX.MDP3`; `PROBE_SCHEMA = ohlcv-1m`; `PROBE_SYMBOL = NQ.c.0`;
    `PROBE_STYPE_IN = continuous`; `PROBE_SAMPLE_TARGET = 5`. Si cualquiera de estos parametros cambia,
    la excepcion queda `INVALIDATED_BY_CONFIGURATION_CHANGE` y requiere nueva autorizacion documental.
    NO autoriza: levantar G0.6; T1.5; W6; W8; integracion productiva; despliegue; modificacion de
    componentes protegidos; ejecucion Live general; futuras ejecuciones Live; Replay; cambio de
    dataset/schema/instrumento/stype; compra de plan; activacion de plan; retry ante entitlement;
    workaround ante errores de acceso.
    Condicion de uso: requiere autorizacion EXPLICITA del usuario en un turno posterior (este turno NO
    es esa autorizacion). La ejecucion futura debe aplicar STOP inmediato ante authentication failure,
    entitlement/access denied, dataset/schema/symbol/stype rechazado, necesidad de compra o activacion,
    configuracion distinta de la autorizada, o necesidad de tocar codigo/componente protegido; sin
    retry, sin workaround, sin compra, sin activacion.
    Ciclo de vida: es de un solo uso. Tras una ejecucion autorizada, un turno documental posterior debe
    cambiar el estado a `USED`; no se reutiliza ni se renueva automaticamente. ORIGIN = APPROVED_RESTRICTION
    (excepcion de gobernanza acotada). No modifica el campo Gate de T1.5.
  - **G0.6-EXC-1 — FINAL STATUS: USED (actualizacion aditiva; el texto historico anterior se conserva).**
    `G0.6-EXC-1 = USED`. La excepcion fue consumida durante RUN4, cuando se recibio Y persistio el primer
    record experimental real: tipo OHLCV, `local_sequence_number = 2`, evidencia persistida
    incrementalmente, timestamps preservados con precision original. G0.6 permanece `OPEN`.
    Resultado empirico RUN4: `RUN4_UNAMBIGUOUS_ASSOCIATIONS = 2`. En las dos asociaciones observadas,
    `EOI.ts_event == OHLCV.ts_event` con `MARKET_TIMESTAMP_DELTA_SECONDS = 0`. Los dos EOI observados
    tenian `instrument_id = 0` y `msg = "End of interval for ohlcv-1m"`. Por tanto
    `EOI_SCOPE_EMPIRICAL_OBSERVATION = SCHEMA_IDENTIFIED_FORMAL_SCOPE_NOT_RESOLVED` (el mensaje identifica
    directamente `ohlcv-1m`, pero con una unica configuracion observada NO quedo demostrado el scope
    formal per-schema frente a per-subscription).
    Suficiencia: `EVIDENCE_CLASSIFICATION = INSUFFICIENT_EMPIRICAL_EVIDENCE` porque
    `2 < MINIMUM_UNAMBIGUOUS_ASSOCIATIONS (5)`. Se mantienen sin cambios:
    `END_OF_INTERVAL_TIMESTAMP_MEANING = NOT_RESOLVED`;
    `OHLCV_1M_END_OF_INTERVAL_MATCHING_RULE = NOT_RESOLVED`;
    `END_OF_INTERVAL_CAN_BE_MATCHED_TO_OHLCV_1M = NOT_RESOLVED`;
    `LIVE_BAR_FINALITY_CLASSIFICATION = CASE_B`. G0.7 NO se cierra.
    Nota RUN4/RUN5-A: RUN4 no alcanzo de forma verificable `CAPTURE_WINDOW_COMPLETED` ni dejo evidencia
    persistida que confirmara el disparo del watchdog absoluto; por tanto
    `RUN4_HARNESS_FAILURE_ROOT_CAUSE = NOT_FULLY_RESOLVED`. RUN5-A descarto especificamente
    `CLIENT_START_BLOCKING_HYPOTHESIS = REJECTED` mediante inspeccion del codigo fuente instalado de
    Databento 0.86.0 (`Live.start()` es no bloqueante), y construyo/valido OFFLINE un harness nuevo:
    `HARNESS_READY_FOR_LIVE = YES`. La validacion offline cubrio flujo normal (6 OHLCV + 6 EOI simulados),
    cierre automatico de capture window, timeout sin evidencia, persistencia incremental, summary final,
    teardown y watchdog de emergencia con terminacion del proceso. Esto NO constituye una demostracion
    retrospectiva de la causa raiz de RUN4.

  - **G0.6-EXC-2 — END_OF_INTERVAL_DIAGNOSTIC_PROBE_ONLY (RUN5-B — corrected offline-validated harness).**
    ESTADO: `G0.6-EXC-2 = AUTHORIZED_NOT_USED`. Nueva autorizacion acotada porque `G0.6-EXC-1 = USED` y
    una excepcion consumida NO puede reutilizarse. G0.6 permanece `OPEN` (esta excepcion NO resuelve,
    cierra ni levanta G0.6 de forma general).
    Alcance: autoriza UNA (1) futura ejecucion diagnostica Live con el harness validado offline
    `eoi_probe_run5.py` (`HARNESS_READY_FOR_LIVE = YES`), cuyo objetivo es AUMENTAR la evidencia
    disponible para determinar si la relacion observada en RUN4 puede verificarse o debe permanecer no
    resuelta. NO predetermina el resultado: RUN5-B debe poder aportar evidencia consistente O detectar
    evidencia contradictoria (la hipotesis observada `EOI.ts_event == OHLCV.ts_event`,
    `MARKET_TIMESTAMP_DELTA_SECONDS = 0`, NO se da por confirmada).
    Configuracion INMUTABLE: `PROBE_MODE = LIVE_REAL`; `PROBE_DATASET = GLBX.MDP3`;
    `PROBE_SCHEMA = ohlcv-1m`; `PROBE_SYMBOL = NQ.c.0`; `PROBE_STYPE_IN = continuous`. Harness autorizado:
    `eoi_probe_run5.py`. Si cualquiera de estos parametros cambia, la excepcion queda invalidada y
    requiere nueva autorizacion documental.
    NO autoriza: levantar G0.6 de forma general; T1.5; Wave 2; W6; W8; integracion productiva; cambios en
    componentes protegidos; despliegue; Replay; Historical; compras; activaciones; retries; reconnect;
    cambio de dataset/schema/symbol/stype_in; segunda ejecucion. Ante rechazo de acceso: STOP, sin retry.
    Regla de consumo: `G0.6-EXC-2` se considera CONSUMIDA en cuanto RUN5-B reciba Y persista el primer
    record experimental real relevante (OHLCV o `SystemMsg` con `SystemCode.END_OF_INTERVAL`). NO depende
    de completar la ventana, ni de obtener 3 o 5 asociaciones, ni de resolver LIVE_BAR_FINALITY. Una vez
    persistido el primer record experimental: `G0_6_EXC_2_RUNTIME_STATUS = USED_PENDING_DOCUMENTATION`,
    aunque RUN5-B termine luego con fallo tecnico o evidencia insuficiente. Si el acceso es rechazado
    antes de recibir cualquier record experimental, la excepcion NO se considera consumida.
    Regla acumulativa pre-bloqueada: `CUMULATIVE_MINIMUM_UNAMBIGUOUS_ASSOCIATIONS = 5`;
    `RUN4_VALID_ASSOCIATIONS_CARRIED_FORWARD = 2`; `ADDITIONAL_UNAMBIGUOUS_ASSOCIATIONS_REQUIRED = 3`. Las
    asociaciones RUN4 solo pueden acumularse con RUN5-B si: configuracion identica, criterio de asociacion
    identico, nuevas asociaciones inequivocas y ninguna contradiccion no explicada. Para declarar
    `EMPIRICALLY_VERIFIED_FOR_PROBE_CONFIGURATION` se requiere: >= 5 asociaciones inequivocas acumuladas,
    relacion consistente y ninguna contradiccion no explicada. Alcanzar numericamente 5 NO basta si
    aparece cualquier contradiccion no explicada; en ese caso NO declarar suficiencia ni cerrar
    LIVE_BAR_FINALITY.
    Ventana de captura futura: aunque bastan 3 asociaciones adicionales para el umbral acumulado, NO
    terminar la captura al obtener la tercera; mantener la ventana completa definida por el harness
    validado (salvo condicion de seguridad/timeout ya prevista) para maximizar la deteccion de
    contradicciones. NO cambiar ahora los parametros temporales ya validados del harness.
    Condicion de uso: requiere autorizacion EXPLICITA del usuario en un turno posterior (este turno NO es
    esa autorizacion). ORIGIN = APPROVED_RESTRICTION. No modifica el campo Gate de T1.5.
  - **G0.6-EXC-2 — FINAL STATUS: USED (actualizacion aditiva; el texto historico anterior se conserva).**
    `G0_6_EXC_2_FINAL_STATUS = USED`. La excepcion fue consumida durante la ejecucion
    `RUN_ID = RUN5B_20260916T000839Z`. `LIVE_GLBX_MDP3_ACCESS = GRANTED`;
    `FIRST_EXPERIMENTAL_RECORD_TYPE = OHLCV`; `EXPERIMENTAL_RECORD_RECEIVED = YES`;
    `EXCEPTION_CONSUMED = YES`. La excepcion quedo consumida cuando el primer record experimental real fue
    recibido Y persistido. El `AUTHORIZED_NOT_USED` del bloque historico anterior describe unicamente el
    estado previo a RUN5-B y NO se reescribe retrospectivamente.

    Evidencia empirica RUN5-B: `RUN5B_UNAMBIGUOUS_ASSOCIATIONS = 2`; `RUN5B_AMBIGUOUS_ASSOCIATIONS = 0`;
    `RUN5B_CONTRADICTORY_UNAMBIGUOUS_ASSOCIATIONS = 0`. En las dos asociaciones se observo
    `EOI.ts_event == OHLCV.ts_event` con `MARKET_TIMESTAMP_DELTA_SECONDS = 0`, consistente con RUN4.
    Observacion comun: `EOI.instrument_id = 0`, `msg = "End of interval for ohlcv-1m"`. La proximidad
    temporal de llegada NO se convierte en regla normativa de matching.

    Acumulacion RUN4 + RUN5-B: `CUMULATIVE_ASSOCIATION_STATUS = ACCUMULABLE` porque ambas ejecuciones
    usaron la misma configuracion pertinente (`dataset = GLBX.MDP3`; `schema = ohlcv-1m`; `symbol = NQ.c.0`;
    `stype_in = continuous`) y el mismo criterio empirico de asociacion. Resultado:
    `RUN4_UNAMBIGUOUS_ASSOCIATIONS = 2`; `RUN5B_UNAMBIGUOUS_ASSOCIATIONS = 2`;
    `CUMULATIVE_UNAMBIGUOUS_ASSOCIATIONS = 4`; `CUMULATIVE_MINIMUM_UNAMBIGUOUS_ASSOCIATIONS = 5`;
    `ASSOCIATIONS_STILL_NEEDED = 1`; `CUMULATIVE_DELTA_PATTERN = 4/4 delta 0 s`; `CONTRADICTION_DETECTED = NO`;
    `AMBIGUOUS_ASSOCIATIONS_CUMULATIVE = 0`.

    Clasificacion metodologica: `EMPIRICAL_EVIDENCE_STATUS = INSUFFICIENT_EMPIRICAL_EVIDENCE` porque `4 < 5`.
    `NUMERIC_THRESHOLD_REACHED = NO`. Se conserva la regla `NUMERIC_THRESHOLD_REACHED != AUTOMATIC_VERIFICATION`.
    El umbral NO se reduce retrospectivamente.

    Semantica y scope aun no resueltos (las cuatro asociaciones consistentes NO convierten una observacion
    empirica en garantia de protocolo): `EOI_SCOPE_EMPIRICAL_OBSERVATION = SCHEMA_IDENTIFIED_FORMAL_SCOPE_NOT_RESOLVED`;
    `END_OF_INTERVAL_TIMESTAMP_MEANING = NOT_RESOLVED`; `OHLCV_1M_END_OF_INTERVAL_MATCHING_RULE = NOT_RESOLVED`;
    `END_OF_INTERVAL_CAN_BE_MATCHED_TO_OHLCV_1M = NOT_RESOLVED`. NO se afirma scope formal per-schema, scope
    formal per-subscription, regla normativa de adjacency, ni semantica protocolaria general basada solo en
    estas cuatro observaciones.

    Live bar finality: `LIVE_BAR_FINALITY_CLASSIFICATION = CASE_B` (sin cambios). Por tanto
    `G0.7 = PARTIALLY_RESOLVED` y la parte `LIVE_BAR_FINALITY` permanece bloqueante; G0.7 NO se cierra.

    Anomalia RUN5-B (registro estrictamente factual): RUN5-B NO persistio `CAPTURE_WINDOW_COMPLETED`,
    `ABSOLUTE_WATCHDOG`, `TEARDOWN_START` ni `TEARDOWN_END`. El traceback de la terminacion manual mostro al
    hilo principal dentro de `controller_loop -> time.sleep(self.POLL)`; por tanto
    `CONTROLLER_ALIVE_AT_MANUAL_INTERRUPTION = VERIFIED`. Queda rechazada la hipotesis previa de que, en ese
    instante, el hilo principal estuviera bloqueado dentro de una llamada nativa del SDK. Se mantiene
    `RUN5B_HARNESS_FAILURE_ROOT_CAUSE = NOT_FULLY_RESOLVED` y `RECORD_FLOW_CESSATION_CAUSE = NOT_RESOLVED`. NO
    se afirma causa compartida entre ambas anomalias.

    RUN5-C (exclusivamente offline): `PURE_DEADLINE_COMPARISON_RESULT = NORMAL`;
    `INTEGRATED_CONTROLLER_FAKE_RESULT = CAPTURE_WINDOW_COMPLETED`; `MULTI_THREAD_STATE_VISIBILITY_TEST = PASS`;
    `MULTI_THREAD_TEST_REPETITIONS = 5000`; `ACCELERATED_RUN5B_RELATION_TEST = CAPTURE_WINDOW_COMPLETED`;
    `WATCHDOG_STANDALONE_TEST_RESULT = PASS`; `DEADLINE_BRANCH_BUG_REPRODUCED = NO`. La anomalia RUN5-B NO fue
    reproducida mediante las pruebas offline realizadas. Esto NO identifica una causa raiz.

    Correccion epistemica: `REAL_LIVE_EXECUTION_SPECIFIC_FACTOR = POSSIBLE_NOT_DEMONSTRATED`. Significa que
    existen diferencias entre una ejecucion Live real y las reproducciones offline realizadas, pero NO se ha
    demostrado que una de esas diferencias sea la causa de la anomalia. No se afirma que el problema apunte al
    SDK, que el SDK sea la causa probable, que la interaccion Live sea la explicacion restante, ni que la
    anomalia solo pueda reproducirse con Live.

    RUN5-D creo, FUERA del repositorio, `eoi_probe_run5d.py`
    (SHA-256 `3adb1266a93517ea04b9eba062068ce1e7982aa576a278f8d1d31ce0dffee360`), preservando el harness base
    `eoi_probe_run5.py` (SHA-256 `87be3130df9e1f757fb8d62c96f918293b2be4191f69bab6702993b7d1d73c83`).
    `FUNCTIONAL_CONTROL_CHANGE_COUNT = 0`; `CONTROL_LOGIC_UNCHANGED_FROM_RUN5 = YES`;
    `HEARTBEAT_LOGGING_VERIFIED = YES`; `CALLBACK_OBSERVABILITY_VERIFIED = YES`;
    `EXECUTION_MANIFEST_VERIFIED = YES`; `OBSERVABILITY_PERTURBATION_DETECTED = NO`;
    `OFFLINE_SELFTEST_RESULT = PASS`; `HARNESS_OFFLINE_VALIDATED = YES`;
    `HARNESS_READY_FOR_AUTHORIZATION_REVIEW = YES`. `HARNESS_READY_FOR_AUTHORIZATION_REVIEW = YES` NO significa
    Live autorizado, EXC-3 autorizada, G0.6 resuelto ni produccion autorizada.

    G0.6-EXC-3: `G0_6_EXC_3_CREATED = NO`; `G0_6_EXC_3_AUTHORIZED = NO`. Su eventual creacion requiere: (1)
    revision y aceptacion de este cierre documental; (2) un turno documental separado; (3) autorizacion
    explicita posterior. No se incluyen aqui condiciones, configuracion ni reglas propuestas para EXC-3.
    ORIGIN = APPROVED_RESTRICTION. No modifica el campo Gate de T1.5.
- **G0.7 — Candle timestamp semantics.** Por que: define `signal_market_timestamp` canonico y alineacion
  TradingView. Alimentado por T0.1. Bloquea: T3.5 (timestamps de Capa 1), T7 time tests, W9. Resolver
  antes de W3. BLOCKS_IMPLEMENTATION.
  ESTADO (post-T0.1, aprobado por Hugo): G0.7 = PARTIALLY_RESOLVED — TIMESTAMP_SEMANTICS = VERIFIED
  (ts_event = apertura del intervalo, evidencia oficial Databento OHLCV data dictionary); LIVE_BAR_FINALITY
  = OPEN (finalidad de entrega Live no verificada; ver Design B.14 [SEGURO — LIVE BAR FINALITY REQUIRES
  VERIFICATION]). No cerrar completamente mientras englobe ambas cuestiones.
- **G0.8 — `strategy_version` (literal/mecanismo, p.ej. `SMA_9_21_v1`).** Por que: campo obligatorio de
  Capa 1. Bloquea: T3.4. Resolver antes de W3. BLOCKS_IMPLEMENTATION.
- **G0.9 — PostgreSQL deployment (RDS vs contenedor en ECS) + version.** Por que: infraestructura de
  persistencia. Bloquea: W3 fisico y W8. Resolver antes de W3 infra / W8. BLOCKS_PRODUCTION.
- **G0.10 — PostgreSQL schema/configuration fisico.** Por que: esquema de LiveSession/Capa 1. Bloquea:
  T3.2/T3.3. Resolver antes de W3. BLOCKS_IMPLEMENTATION.
- **G0.11 — Persistence retry count.** Por que: politica persist-before-publish (ADENDA C). Bloquea:
  T4.2/T4.3. Resolver antes de W4. BLOCKS_IMPLEMENTATION.
- **G0.12 — Persistence retry interval/backoff.** Igual que G0.11. Bloquea: T4.2/T4.3. BLOCKS_IMPLEMENTATION.
- **G0.13 — Persistence timeout total por senal.** Bloquea: T4.2/T4.3. BLOCKS_IMPLEMENTATION.
- **G0.14 — Maxima antiguedad publicable de una senal pendiente (ventana antes de SUPPRESSED).**
  Por que: define `SUPPRESSED_DUE_TO_PERSISTENCE`. Bloquea: T4.4. Resolver antes de W4. BLOCKS_IMPLEMENTATION.
- **G0.15 — Persistence health-check interval.** Bloquea: T4.6. BLOCKS_IMPLEMENTATION.
- **G0.16 — Recovery threshold/evidence (definicion de la sonda write+read).** Bloquea: T4.6. BLOCKS_IMPLEMENTATION.
- **G0.17 — `MAX_WARMUP_EVENTS` (umbral de alerta de salud de conexion).** Por que: alerta de re-warm-ups
  repetidos (Design A.9). Bloquea: T2.9 (contador/alerta) y su visual en W5. Resolver antes de W2/W5.
  BLOCKS_TRADER_REVIEW.
- **G0.18 — Stability window para trader review (duracion).** Por que: criterio Wave 9. Bloquea: T9.2.
  Resolver antes de W9. BLOCKS_TRADER_REVIEW.
- **G0.19 — Inyeccion de `application_version` / git SHA.** Por que: provenance + deployment identificable.
  Bloquea: T3.4 (campo), T8.4 (identidad de despliegue). Resolver antes de W3/W8. BLOCKS_PRODUCTION.

### Trazabilidad de los Decision Gates (auditoria)

Cada Gate se traza al Design aprobado / steering aprobado. Clasificacion de origen:
EXPLICIT_SEGURO (marcador [SEGURO] del Design), DIRECT_DESIGN_DEPENDENCY (parametro que una
seccion del Design exige decidir), APPROVED_RESTRICTION (restriccion de gobernanza congelada).

- G0.1 EXPLICIT_SEGURO — Design seccion 4 + ADENDA C.20 (#1) + B.27 (#1).
- G0.2 EXPLICIT_SEGURO — Design seccion 3 + B.27 (#2); steering `databento-architecture.md` (heartbeat/reconnect).
- G0.3 EXPLICIT_SEGURO — Design seccion 5 / A.10-A.11 + B.27 (#3).
- G0.4 EXPLICIT_SEGURO — Design A.5 + B.27 (#4).
- G0.5 EXPLICIT_SEGURO — Design A.9 (costo [SEGURO]); steering `databento-architecture.md` / `tradecore-mvp/requirements.md`.
- G0.6 APPROVED_RESTRICTION — steering `databento-architecture.md` (GATE Fase A/B, plan Standard) + `fase-actual-etapa1.md`.
- G0.7 EXPLICIT_SEGURO — Design B.14 ([SEGURO — CANDLE TIMESTAMP SEMANTICS REQUIRES VERIFICATION]) + B.27 (#6).
- G0.8 EXPLICIT_SEGURO — Design seccion 16 + B.27 (#4/strategy_version).
- G0.9 EXPLICIT_SEGURO — Design seccion 14 (PostgreSQL deployment [SEGURO]) + B.27 (#5).
- G0.10 DIRECT_DESIGN_DEPENDENCY — Design seccion 14 / C.15 (esquema Capa 1/LiveSession requerido para T3.2/T3.3).
- G0.11 EXPLICIT_SEGURO — Design C.3 / C.20 (#13).
- G0.12 EXPLICIT_SEGURO — Design C.3 / C.20 (#14).
- G0.13 EXPLICIT_SEGURO — Design C.3 / C.20 (#15).
- G0.14 EXPLICIT_SEGURO — Design C.6 / C.20 (#16).
- G0.15 EXPLICIT_SEGURO — Design C.7 / C.20 (#17).
- G0.16 EXPLICIT_SEGURO — Design C.7 / C.20 (#18).
- G0.17 EXPLICIT_SEGURO — Design A.9 (MAX_WARMUP_EVENTS) + B.27 (#12).
- G0.18 EXPLICIT_SEGURO — Design seccion 23 / B.27 (#10) (ventana de estabilidad).
- G0.19 EXPLICIT_SEGURO — Design seccion 10 / B.27 (#11) (application_version / git SHA).

Resultado: INITIAL_GATE_COUNT = 19, FINAL_GATE_COUNT = 19. GATES_REFORMULATED = 0, GATES_REMOVED = 0.
Todos los Gates son trazables al Design/steering aprobado; ninguno proviene solo de un reporte previo,
suposicion de implementacion ni preferencia comercial no aprobada.
## G0.GATE — Checkpoint CP-0 (Decisions Ready)
- **Objetivo:** confirmar que todos los Gates `BLOCKS_IMPLEMENTATION` necesarios para arrancar el
  siguiente bloque estan resueltos por Hugo antes de iniciar implementacion de la Wave dependiente.
- **Dependencias:** T0.1, G0.1..G0.19.
- **Criterio de aceptacion:** cada Gate `BLOCKS_IMPLEMENTATION` requerido por la Wave que arranca tiene
  valor confirmado (no [SEGURO]); los `BLOCKS_PRODUCTION`/`BLOCKS_TRADER_REVIEW` pueden seguir pendientes
  sin frenar desarrollo, pero registrados.
- **REQUIRES_EXPLICIT_APPROVAL:** YES (decision de Hugo).
- **ESTADO CP-0 (aprobado por Hugo):** PASSED unicamente para el bloque minimo [T1.1, T3.1].
  MINIMUM_GATES_REQUIRED_TO_START = NONE (T1.1 y T3.1 dependen solo de CP-0, sin Gate bloqueante de
  arranque). NO autoriza Wave 1 completa, Wave 3 completa, ni ninguna otra Task; el resto de las Tasks
  siguen requiriendo sus Gates respectivos.

---

# WAVE 1 — Databento Live foundations (piezas nuevas, sin tocar protegidos)

Objetivo: construir y probar la logica Live de forma AISLADA en modulos NUEVOS, sin editar
`src/connectors/databento_connector.py`. La integracion fisica en el connector protegido se difiere
a Wave 6. No crear una implementacion paralela permanente que duplique el connector.

## T1.1 — Contrato/interfaz de fuente de velas Live (candle source)
- **Objetivo:** definir la interfaz asincrona comun `AsyncIterator[Candle]` que Live y Simulation
  comparten (Design seccion 6), en un modulo nuevo, sin tocar protegidos.
- **Archivos:** nuevos (p.ej. `src/live/candle_source.py`). Protegidos: NONE.
- **Dependencias:** CP-0.
- **Gate:** —.
- **Criterio de aceptacion:** existe un contrato tipado que ambas fuentes pueden implementar; documenta
  que `SimulationReplay.replay()` ya lo satisface.
- **Tests esperados:** unit (contrato/typing).
- **Evidencia:** test pass + diff minimo del modulo nuevo.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T1.2 — Transformacion de mensaje Live ohlcv-1m -> Candle (reuso de conversion)
- **Objetivo:** funcion nueva que convierte un mensaje Live `ohlcv-1m` a `Candle`, reutilizando la MISMA
  logica de conversion de precio/timestamp que Historical (llamando helpers existentes, sin duplicarlos).
- **Archivos:** nuevos (`src/live/live_candle_mapper.py`). Existentes no protegidos: puede importar el
  helper `_convert_price` del connector (import, NO edicion). Protegidos: NONE (solo import).
- **Dependencias:** T1.1, G0.7 (semantica de timestamp).
- **Criterio de aceptacion:** un mensaje sintetico produce un `Candle` con OHLCV/timestamp correctos.
- **Tests esperados:** unit con records mock.
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T1.3 — Estado de conexion Live (`is_connected`) — abstraccion aislada
- **Objetivo:** modulo nuevo que modela el estado de conexion Databento (`CONNECTED/RECONNECTING/DISCONNECTED`)
  sin depender del connector protegido.
- **Archivos:** nuevos (`src/live/connection_state.py`). Protegidos: NONE.
- **Dependencias:** T1.1.
- **Criterio de aceptacion:** transiciones de estado deterministas y observables.
- **Tests esperados:** unit de maquina de estados.
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T1.4 — Reconnect + exponential backoff (abstraccion aislada)
- **Objetivo:** logica nueva de reconexion con backoff exponencial, error fatal sin retry, reset tras
  estabilidad — parametrizada por los Gates (NO hardcodear valores).
- **Archivos:** nuevos (`src/live/reconnect_policy.py`). Protegidos: NONE.
- **Dependencias:** T1.3, G0.1, G0.2.
- **Gate:** G0.1, G0.2 (parametros) — RESOLVED (Design seccion 4.1/4.2).
- **Criterio de aceptacion:** exito 1er/2do intento, agotamiento tras N, AUTH_FAILED sin retry, backoff
  segun parametros inyectados.
- **Tests esperados:** unit (mock de intentos), incl. casos de los 12 spec-ahead de Reconnection.
- **Evidencia:** test pass + mapeo a los tests spec-ahead.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T1.5 — `subscribe_live()` como funcion nueva aislada (pre-integracion)
- **Objetivo:** implementar la LOGICA de suscripcion Live (init cliente, subscribe, iterar, mapear a
  Candle, cierre) en un modulo nuevo que compone T1.2-T1.4, SIN editar el connector protegido.
- **Archivos:** nuevos (`src/live/live_subscription.py`). Protegidos: NONE (la union con el connector es W6).
- **Dependencias:** T1.2, T1.3, T1.4, G0.6 (para ejecucion real; la logica se prueba con mocks).
- **Gate:** G0.6 (plan Standard/GATE Fase B para ejecucion real; la Task se prueba con mocks sin plan).
- **Criterio de aceptacion:** con `db.Live` mockeado, produce Candles cerradas en orden; descarta velas
  <= ultima conocida (dedup); expone estado.
- **Tests esperados:** unit con `db.Live` mock (alinea con los 5 spec-ahead de LiveSubscription).
- **Evidencia:** test pass + mapeo a spec-ahead.
- **INTEGRATION_DEFERRED_TO_WAVE_6:** YES (union fisica en `databento_connector.py`).
- **REQUIRES_EXPLICIT_APPROVAL:** NO (modulo nuevo; la integracion protegida si requerira aprobacion en W6).

## T1.6 — Relacion con los 12 tests spec-ahead (mapeo, sin editar el connector)
- **Objetivo:** documentar como las piezas de W1 satisfacen los 12 tests spec-ahead de
  `tests/unit/test_databento_connector.py`, y que parte queda pendiente hasta la integracion W6.
- **Archivos:** nuevos (nota en la Spec). Protegidos: NONE.
- **Dependencias:** T1.1-T1.5.
- **Criterio de aceptacion:** tabla que asocia cada uno de los 12 tests con la pieza W1 y/o la integracion W6.
- **Evidencia:** documento de mapeo.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

---

# WAVE 2 — Warm-up / synchronization / recovery (piezas nuevas)

Objetivo: implementar la maquina de estados de datos y la logica warm-up/sync/gap en modulos nuevos.
Invariante explicito en TODA la Wave: `NO SIGNALS BEFORE LIVE_SYNCED`.

## T2.1 — `warmup_min` derivado dinamicamente (funcion/config, no hardcode)
- **Objetivo:** funcion que calcula `signal_warmup_min` (y `research_metadata_warmup_min`) desde las
  dependencias reales (Design A.3), sin acoplar componentes no operativos.
- **Archivos:** nuevos (`src/live/warmup.py`). Protegidos: NONE.
- **Dependencias:** CP-0.
- **Criterio de aceptacion:** para SMA 9/21 + `crossover_state_requirement=1` devuelve 22, y recalcula si
  cambian periodos; distingue signal vs research warmup.
- **Tests esperados:** unit (varios juegos de periodos).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T2.2 — Recuperacion de warm-up Historical en terminos de "N velas cerradas validas"
- **Objetivo:** logica nueva que solicita historia hasta reunir >= `signal_warmup_min` velas cerradas
  validas antes del boundary (no en minutos), con tope de retroceso (Gate).
- **Archivos:** nuevos (`src/live/warmup_loader.py`). Puede importar el connector Historical (import, no edicion). Protegidos: NONE.
- **Dependencias:** T2.1, G0.4.
- **Gate:** G0.4.
- **Criterio de aceptacion:** ante huecos de mercado (fines de semana/holidays) sigue reuniendo N velas o
  se detiene en el tope documentado.
- **Tests esperados:** unit con series sinteticas con gaps.
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T2.3 — Maquina de estados de market data (WARMING_UP/SYNCING/LIVE_SYNCED/STALE/RECONNECTING/RESYNCING)
- **Objetivo:** modulo nuevo con la maquina de estados de datos (Design A.2/A.8) e invariante de
  habilitacion de señales solo en LIVE_SYNCED.
- **Archivos:** nuevos (`src/live/market_data_state.py`). Protegidos: NONE.
- **Dependencias:** T2.1.
- **Criterio de aceptacion:** ninguna transicion permite señales fuera de LIVE_SYNCED; estados alcanzables.
- **Tests esperados:** unit de la maquina de estados.
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T2.4 — Buffer temporal de velas Live durante warm-up + boundary/watermark
- **Objetivo:** logica nueva que bufferiza velas Live al conectar, determina el boundary y coordina el
  warm-up hasta ese boundary (Design A.7, estrategia conectar-primero).
- **Archivos:** nuevos (`src/live/startup_boundary.py`). Protegidos: NONE.
- **Dependencias:** T2.2, T2.3.
- **Criterio de aceptacion:** boundary determinista; ninguna vela Live se procesa por el motor antes del merge.
- **Tests esperados:** unit con streams sinteticos.
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T2.5 — Merge + orden cronologico + dedup (Historical warm-up <-> Live buffer)
- **Objetivo:** logica nueva de conciliacion (merge, orden ascendente, dedup por timestamp reutilizando
  la semantica de `CandleBuffer`) para SYNCING (Design A.7/A.8).
- **Archivos:** nuevos (`src/live/sync_merge.py`). Puede usar `CandleBuffer` (import). Protegidos: NONE.
- **Dependencias:** T2.4.
- **Criterio de aceptacion:** sin hueco/duplicacion/desorden entre ultima historica y primera Live.
- **Tests esperados:** unit (dedup, out-of-order, contiguidad).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T2.6 — Warm-up feeding + reconstruccion de estado del motor por reinstanciacion
- **Objetivo:** logica nueva que alimenta cronologicamente las velas de warm-up a una INSTANCIA nueva de
  `SignalEngine` (sin modificar el motor) para reconstruir `_prev_fast/_prev_slow`, y suprime cualquier
  crossover de warm-up como NO-Live (Design A.4/A.12).
- **Archivos:** nuevos (`src/live/warmup_feeder.py`). Usa `SignalEngine` por composicion (instancia nueva). Protegidos: NONE.
- **Dependencias:** T2.5, T2.1.
- **Criterio de aceptacion:** tras feeding, el motor tiene estado valido para el proximo cierre; ningun
  crossover de warm-up se publica/persiste/cuenta.
- **Tests esperados:** unit (state reconstruction; no-signals-during-warm-up).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T2.7 — Gap detection + recoverable gap (<= MAX_AUTO_RECOVERABLE_GAP) + orden atomico
- **Objetivo:** logica nueva de deteccion de gap con orden atomico por vela (gap-check SIEMPRE antes de
  evaluar señal, Design A.11) y recovery via Historical para gaps recuperables (STALE/RECONNECTING/RESYNCING).
- **Archivos:** nuevos (`src/live/gap_manager.py`). Protegidos: NONE.
- **Dependencias:** T2.3, T2.5, G0.3.
- **Gate:** G0.3.
- **Criterio de aceptacion:** imposible evaluar/publicar señal en el ciclo donde se detecta gap; recovery
  cierra el hueco y vuelve a LIVE_SYNCED.
- **Tests esperados:** unit (gap detection; recoverable gap; atomic order).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T2.8 — Gap irrecuperable -> cierre de LiveSession + re-warm-up completo
- **Objetivo:** logica nueva de la secuencia congelada (Design A.11): detener señales, cerrar sesion
  (`ENDED_GAP_EXCEEDED`), descartar estado, nueva instancia de SignalEngine, nuevo `live_session_id`,
  warm-up completo; sin reanudacion con estado previo.
- **Archivos:** nuevos (`src/live/session_lifecycle.py`). Protegidos: NONE.
- **Dependencias:** T2.6, T2.7, G0.3.
- **Criterio de aceptacion:** no existe camino de reanudacion de señales tras gap irrecuperable sin
  warm-up completo; el `signal_id`/estado previo nunca se reutiliza.
- **Tests esperados:** unit (irrecoverable gap -> re-warm-up; no reuse de estado).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T2.9 — Contador de warm-up/re-warm-up + alerta de salud de conexion
- **Objetivo:** logica nueva que cuenta eventos de warm-up/re-warm-up por sesion y por dia y marca alerta
  si supera `MAX_WARMUP_EVENTS` (Design A.9).
- **Archivos:** nuevos (`src/live/warmup_metrics.py`). Protegidos: NONE.
- **Dependencias:** T2.8, G0.17.
- **Gate:** G0.17.
- **Criterio de aceptacion:** el contador incrementa por evento y expone un flag de alerta sobre el umbral.
- **Tests esperados:** unit.
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T2.10 — Primera señal elegible tras LIVE_SYNCED
- **Objetivo:** logica nueva que garantiza que la PRIMERA señal Live proviene de una vela nueva posterior
  a LIVE_SYNCED (Design A.4).
- **Archivos:** nuevos (parte de `session_lifecycle.py`/orquestacion). Protegidos: NONE.
- **Dependencias:** T2.6, T2.3.
- **Criterio de aceptacion:** no se emite señal Live hasta que exista una vela nueva post-SYNCED.
- **Tests esperados:** unit (first eligible live signal).
- **Evidencia:** test pass.
- **INTEGRATION_DEFERRED_TO_WAVE_6:** YES (el cableado final con `app.py` es W6).
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

---

# WAVE 3 — PostgreSQL / LiveSession / Capa 1 (piezas nuevas)

Objetivo: infraestructura logica de persistencia, esquema, LiveSession y Signal Context (Capa 1),
con inmutabilidad y anti-data-leakage. Capa 2/3 permanecen P2 (solo minimos estructurales para no
romper compatibilidad futura).

## T3.1 — Abstraccion de persistencia (repository) independiente del motor
- **Objetivo:** interfaz nueva de persistencia (repository) que el resto del sistema usa sin conocer el
  motor concreto (Design seccion 14). Permite tests con doble en memoria.
- **Archivos:** nuevos (`src/persistence/repository.py`). Protegidos: NONE.
- **Dependencias:** CP-0.
- **Gate:** —.
- **Criterio de aceptacion:** contrato claro para persistir/recuperar Capa 1 y LiveSession por id.
- **Tests esperados:** unit con implementacion fake en memoria.
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T3.2 — Esquema PostgreSQL: LiveSession
- **Objetivo:** definir el esquema/modelo de `LiveSession` (Design seccion 10) — sin desplegar RDS real.
- **Archivos:** nuevos (`src/persistence/models/live_session.py`, migraciones/DDL en `migrations/`). Protegidos: NONE.
- **Dependencias:** T3.1, G0.9, G0.10.
- **Gate:** G0.9, G0.10.
- **Criterio de aceptacion:** campos: `live_session_id` (PK), start/end UTC, data_source, instrument,
  timeframe, strategy_version, application_version, market_mode, status.
- **Tests esperados:** unit/migracion contra Postgres local o contenedor de test.
- **Evidencia:** DDL + test de creacion.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T3.3 — Esquema PostgreSQL: Signal Context (Capa 1) + constraints
- **Objetivo:** esquema de Capa 1 (Design 12 / B.20 / C.15) con PK `signal_id`, FK a LiveSession,
  inmutabilidad (append-only) y constraint temporal para anti-data-leakage.
- **Archivos:** nuevos (`src/persistence/models/signal_context.py`, migraciones). Protegidos: NONE.
- **Dependencias:** T3.2, G0.10.
- **Gate:** G0.10.
- **Criterio de aceptacion:** `signal_id` unique/PK; FK valida; sin UPDATE de campos de contexto; base para
  la restriccion `observation_ts > signal_market_timestamp` (Capa 2 futura).
- **Tests esperados:** unit (constraints, rechazo de update de contexto).
- **Evidencia:** DDL + tests de constraint.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T3.4 — Provenance + strategy_version + application/git version en Capa 1
- **Objetivo:** poblar los campos de procedencia (Design 11) y `strategy_version`/`application_version`.
- **Archivos:** nuevos (parte de `signal_context.py` + builder). Protegidos: NONE.
- **Dependencias:** T3.3, G0.8, G0.19.
- **Gate:** G0.8 (strategy_version), G0.19 (git SHA injection).
- **Criterio de aceptacion:** cada Capa 1 lleva market_mode/data_source/instrument/dataset/schema/
  strategy_version/application_version; imposible ambiguar LIVE vs SIMULATION.
- **Tests esperados:** unit.
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T3.5 — Timestamps de Capa 1: market / generated / emitted / persisted
- **Objetivo:** modelar los tres tiempos (Design B.9 / C.9): `signal_market_timestamp` (inmutable,
  canonico), `signal_generated_at`, `signal_emitted_at` (post-COMMIT), `persisted_at` (opcional). Eliminar
  cualquier campo ambiguo `timestamp`.
- **Archivos:** nuevos (parte de `signal_context.py`). Protegidos: NONE.
- **Dependencias:** T3.3, G0.7.
- **Gate:** G0.7 (semantica de timestamp).
- **Criterio de aceptacion:** los tres tiempos son campos distintos con semantica documentada; market
  timestamp inmutable; no hay campo compartido.
- **Tests esperados:** unit (inmutabilidad de market timestamp; separacion de campos).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T3.6 — Recuperacion por signal_id + durabilidad
- **Objetivo:** operacion de recuperar una Capa 1 por `signal_id` (soporte del invariante
  `VISIBLE_SIGNAL => DURABLE_SIGNAL_CONTEXT`).
- **Archivos:** nuevos (parte del repository). Protegidos: NONE.
- **Dependencias:** T3.3.
- **Criterio de aceptacion:** toda Capa 1 commiteada es recuperable por `signal_id`.
- **Tests esperados:** unit (round-trip persist/recover).
- **Evidencia:** test pass + query de ejemplo.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T3.7 — Minimos estructurales para Capa 2/3 (sin implementar P2)
- **Objetivo:** dejar las claves/relaciones minimas (FK `signal_id`) para que Capa 2/3 futuras encajen sin
  rediseñar Capa 1; NO implementar logica de observaciones/outcomes.
- **Archivos:** nuevos (placeholders de esquema documentados). Protegidos: NONE.
- **Dependencias:** T3.3.
- **Criterio de aceptacion:** el esquema de Capa 1 no requerira migracion disruptiva para añadir Capa 2/3.
- **Evidencia:** nota de diseño de compatibilidad.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

---

# WAVE 4 — Persist-before-publish / publication gate (piezas nuevas)

Objetivo: implementar la logica ADENDA C en modulos nuevos. No editar `app.py`/`throttled_pusher.py`/
dashboard (integracion en W6).

## T4.1 — Publication Gate compuesto (LIVE_SYNCED AND PERSISTENCE_HEALTHY)
- **Objetivo:** modulo nuevo que expone `SIGNAL_PUBLICATION_ENABLED` como AND de market state y
  persistence state (Design C.2), independiente del calculo del motor.
- **Archivos:** nuevos (`src/live/publication_gate.py`). Protegidos: NONE.
- **Dependencias:** T2.3, T4.5.
- **Criterio de aceptacion:** si falla cualquiera -> BLOCKED; ejes separados (no se mezclan).
- **Tests esperados:** unit (matriz de estados).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T4.2 — persist-before-publish + retries idempotentes (mismo signal_id)
- **Objetivo:** orquestacion nueva: crear signal_id -> Capa 1 -> persistir -> COMMIT confirmado -> gate ->
  (publicar). Retries con mismo `signal_id` (Design C.1/C.3/C.4).
- **Archivos:** nuevos (`src/live/signal_publisher.py`). Protegidos: NONE.
- **Dependencias:** T3.6, T4.1, G0.11, G0.12, G0.13.
- **Gate:** G0.11, G0.12, G0.13.
- **Criterio de aceptacion:** no se publica sin COMMIT confirmado; retries no generan UUID nuevo.
- **Tests esperados:** unit (no-publish-before-commit; publish-after-commit; retry same id).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T4.3 — Idempotencia ante ACK ambiguo (upsert por signal_id)
- **Objetivo:** manejar commit realmente ocurrido con confirmacion perdida: verificar existencia por
  `signal_id`; no duplicar Capa 1 ni marcador (Design C.4).
- **Archivos:** nuevos (parte de `signal_publisher.py` + repository upsert). Protegidos: NONE.
- **Dependencias:** T4.2.
- **Criterio de aceptacion:** un retry sobre fila ya commiteada no duplica y publica una sola vez.
- **Tests esperados:** unit (ambiguous ACK -> no duplicate).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T4.4 — SUPPRESSED_DUE_TO_PERSISTENCE + cierre de ciclo de vida del signal_id
- **Objetivo:** al agotar la ventana de retry (Gate), marcar la señal como suprimida (evento operacional
  separado de Capa 1), cerrar el `signal_id` (sin reactivacion), sin publicacion retroactiva (Design C.6).
- **Archivos:** nuevos (parte de `signal_publisher.py` + registro de eventos operacionales). Protegidos: NONE.
- **Dependencias:** T4.2, G0.14.
- **Gate:** G0.14.
- **Criterio de aceptacion:** señal suprimida no se publica luego; su `signal_id` nunca se reutiliza; queda
  registrada para observabilidad sin ser Capa 1 formal.
- **Tests esperados:** unit (no retroactive publish; suppressed id closed).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T4.5 — Estado de salud de persistencia (HEALTHY/DEGRADED/RECOVERING)
- **Objetivo:** eje de salud independiente de la persistencia (Design C.7/C.11).
- **Archivos:** nuevos (`src/live/persistence_state.py`). Protegidos: NONE.
- **Dependencias:** T3.1.
- **Criterio de aceptacion:** transiciones deterministas; separado de market state.
- **Tests esperados:** unit.
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T4.6 — Deteccion de outage + recuperacion (sonda write+read)
- **Objetivo:** health-check y recuperacion: DEGRADED tras fallo sostenido; RECOVERING; vuelta a HEALTHY solo
  con evidencia real write+read (Design C.7).
- **Archivos:** nuevos (parte de `persistence_state.py`). Protegidos: NONE.
- **Dependencias:** T4.5, G0.15, G0.16.
- **Gate:** G0.15, G0.16.
- **Criterio de aceptacion:** vuelta a HEALTHY exige operacion write+read confirmada; recovery no publica
  retroactivamente señales ya no elegibles.
- **Tests esperados:** unit (outage -> DEGRADED; recovery -> future publish).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T4.7 — Publish failure DESPUES de COMMIT (idempotente, sin duplicar marcador)
- **Objetivo:** manejar COMMIT OK + publish WebSocket FAIL: reenvio seguro por `signal_id`, sin duplicar
  marcador; distinguir de persist failure (Design C.10).
- **Archivos:** nuevos (parte de `signal_publisher.py`). Protegidos: NONE (el dashboard dedup es W6).
- **Dependencias:** T4.2.
- **Criterio de aceptacion:** un reintento de publicacion no crea contexto ni marcador duplicado.
- **Tests esperados:** unit (publish failure -> no duplicate).
- **Evidencia:** test pass.
- **INTEGRATION_DEFERRED_TO_WAVE_6:** YES (envio real por WebSocket es W6).
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T4.8 — No reset del SignalEngine por outage puramente de persistencia
- **Objetivo:** asegurar (a nivel de orquestacion nueva) que un outage de PostgreSQL sin gap de mercado NO
  resetea el motor (Design C.8).
- **Archivos:** nuevos (orquestacion). Protegidos: NONE.
- **Dependencias:** T4.5, T2.7.
- **Criterio de aceptacion:** con market LIVE_SYNCED y sin gap, un outage no dispara reset/re-warm-up.
- **Tests esperados:** unit (DB outage != market gap).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

---

# WAVE 5 — WebSocket / status / dashboard foundations (piezas nuevas)

Objetivo: construir contratos/serializacion/copy para comunicar todos los estados, backward-compatible.
NO editar `dashboard/index.html`, `throttled_pusher.py`, `app.py` (integracion en W6).

## T5.1 — Contrato extendido del evento `status` (aditivo, backward-compatible)
- **Objetivo:** definir el payload de status ampliado (browser, databento, market_data, persistence,
  signals, market_mode, live_session_id) SIN romper los campos actuales (Design 8 / A.13 / C.11-C.12).
- **Archivos:** nuevos (`src/live/status_contract.py`). Protegidos: NONE.
- **Dependencias:** T2.3, T4.5.
- **Criterio de aceptacion:** un cliente antiguo ignora campos nuevos; el nuevo los consume.
- **Tests esperados:** unit (compatibilidad del payload).
- **Evidencia:** test pass.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T5.2 — Serializacion de estados + timestamp ISO valido (helper nuevo)
- **Objetivo:** helper de serializacion que produce ISO 8601 UTC valido (sin `+00:00Z`) para el status,
  listo para inyectar en W6 (Design 9 / C).
- **Archivos:** nuevos (`src/live/status_serializer.py`). Protegidos: NONE (el fix fisico en pusher es W6).
- **Dependencias:** T5.1, G0.7.
- **Criterio de aceptacion:** el string es parseable como Date valido; no contiene `+00:00Z`.
- **Tests esperados:** unit (ISO valido).
- **Evidencia:** test pass.
- **INTEGRATION_DEFERRED_TO_WAVE_6:** YES (aplicar en `throttled_pusher.py`).
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T5.3 — Copy de estados (no crossover vs bloqueo tecnico)
- **Objetivo:** definir el copy conceptual (Design C.13 / seccion 19) que distingue ausencia de crossover
  de "señales bloqueadas por problema tecnico", sin claims de rentabilidad.
- **Archivos:** nuevos (constantes de copy). Protegidos: NONE.
- **Dependencias:** T5.1.
- **Criterio de aceptacion:** textos aprobados que cumplen las reglas de copy; sin "Sin señales" ambiguo.
- **Evidencia:** documento de copy.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## T5.4 — Mapeo de estados a indicadores de dashboard (contrato de UI, sin editar HTML)
- **Objetivo:** especificar como cada estado se refleja en los indicadores (Browser/Databento/Market Data/
  Signal Record/Signals) para consumir en W6.
- **Archivos:** nuevos (nota de contrato UI). Protegidos: NONE.
- **Dependencias:** T5.1, T5.3.
- **Criterio de aceptacion:** tabla estado -> indicador/color/texto; "CONNECTED" nunca implica aptitud.
- **Evidencia:** contrato UI documentado.
- **INTEGRATION_DEFERRED_TO_WAVE_6:** YES (render en `dashboard/index.html`).
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

---

# CHECKPOINT — CP-1 (Foundations Ready)

CP-1 es una condicion TECNICA y verificable por Kiro (NO una decision humana), previa a solicitar la
autorizacion de CP-2 para tocar componentes protegidos. Diferencia clave:
- **CP-1 (Foundations Ready)** = verificacion automatica/tecnica de Kiro; su resultado conceptual es
  `READY_FOR_PROTECTED_COMPONENT_REVIEW`. No autoriza tocar protegidos.
- **CP-2 (Protected Components Approval)** = decision HUMANA de Hugo; ninguna condicion automatica, test,
  validator o conclusion de Kiro puede sustituirla.

CP-1 verifica como minimo:
1. Waves 1-5 completadas segun sus criterios de aceptacion.
2. Logica nueva / no protegida construida (modulos `src/live/*`, `src/persistence/*`).
3. Tests aislados previstos para esas foundations satisfactorios.
4. Ningun archivo protegido modificado (app.py / dashboard/index.html / throttled_pusher.py /
   databento_connector.py / SignalEngine intactos).
5. Dependencias conocidas resueltas hasta ese punto (Gates BLOCKS_IMPLEMENTATION de W1-W5 confirmados).
6. Cambios pendientes en componentes protegidos expresables como modificaciones minimas y especificas
   (borrador de los diffs conceptuales de T6.1-T6.4 listo para presentar).
7. Riesgos de integracion documentados por componente.

Secuencia: CP-1 (technical foundations ready) -> CP-2 (explicit human approval) -> Wave 6 integration.
- **Dependencias:** WAVE 1, WAVE 2, WAVE 3, WAVE 4, WAVE 5 completas.
- **REQUIRES_EXPLICIT_APPROVAL:** NO (verificacion tecnica de Kiro; CP-2 es la aprobacion humana).

---
# CHECKPOINT — PROTECTED COMPONENTS (CP-2, obligatorio antes de Wave 6)

Antes de CUALQUIER Task de Wave 6, Kiro presenta para CADA archivo protegido: (1) archivo; (2) funcion/
seccion; (3) comportamiento actual; (4) cambio minimo propuesto; (5) motivo; (6) dependencias; (7) tests
afectados; (8) riesgo de regresion; (9) diff conceptual. Solo tras aprobacion EXPLICITA de Hugo se
ejecuta cada modificacion. Requiere CP-1 (Live/sync/persistence foundations listas sin tocar protegidos).

---

# WAVE 6 — Integracion en componentes protegidos (REQUIERE APROBACION EXPLICITA)

Cada Task de esta Wave: `REQUIRES_EXPLICIT_APPROVAL = YES`. No ejecutar sin aprobacion de Hugo tras CP-2.

**Aislamiento de aprobaciones (obligatorio):** la autorizacion es INDIVIDUAL por componente protegido.
approval(T6.1) != approval(T6.2) != approval(T6.3) != approval(T6.4). Aprobar una Task protegida NUNCA
autoriza implicitamente otra. CP-2 puede servir como checkpoint de revision, pero DEBE registrar
explicitamente cuales Tasks protegidas fueron autorizadas; si Hugo autoriza solo algunas, unicamente
esas pueden ejecutarse.

**Dependencia tecnica dentro de W6 (auditada, NO orden de escritura):** existen DOS ramas independientes
tras CP-2 (paralelizables si Hugo aprueba ambas): rama-conector `T6.1 -> T6.2` y rama-UI `T6.3 -> T6.4`.
T6.3/T6.4 NO dependen tecnicamente de T6.1/T6.2 (el fix de timestamp y los campos de status del pusher, y
su render en el dashboard, no requieren el connector Live ni la seleccion de fuente en app.py). El
end-to-end final si requiere ambas ramas, pero como Tasks documentales son independientes.

## T6.1 — `src/connectors/databento_connector.py`: añadir Live aislado (Fase A intacta)
- **Objetivo:** integrar `subscribe_live()`/`is_connected`/`reconnect()` (piezas de W1) en el connector,
  SIN alterar `load_historical`/`_fetch_historical_sync` (Fase A byte-identica).
- **Archivos protegidos:** `src/connectors/databento_connector.py`.
- **Componentes protegidos afectados:** el connector (solo adicion Live).
- **DEPENDS_ON:** [W1 (T1.1-T1.6), CP-1, CP-2, G0.6].
- **DEPENDENCY_JUSTIFICATION:** integra las piezas Live construidas en W1; requiere CP-1 (foundations
  listas) y CP-2 (aprobacion humana del diff del connector); G0.6 habilita ejecucion Live real. No depende
  de ninguna otra Task de W6 (es la raiz de la rama-conector).
- **Gate:** G0.6 (plan Standard / GATE Fase B).
- **Criterio de aceptacion:** Fase A Historical intacta; Live añadido; los 5 spec-ahead de LiveSubscription pasan.
- **Tests esperados:** unit spec-ahead LiveSubscription + regresion Historical.
- **Evidencia:** diff minimo + test pass + baseline Fase A sin cambios.
- **REQUIRES_EXPLICIT_APPROVAL:** YES.

## T6.2 — `src/api/app.py`: seleccion de fuente Live + state machine + publication flow
- **Objetivo:** integrar fuente Live/Simulation intercambiable, la maquina de estados (W2), el gate y el
  flujo persist-before-publish (W4) en el `lifespan`/`_processing_loop`, SIN duplicar el loop ni alterar SMA 9/21.
- **Archivos protegidos:** `src/api/app.py`.
- **DEPENDS_ON:** [T6.1, W2, W3, W4, T2.10, CP-2, G0.1-G0.4, G0.6].
- **DEPENDENCY_JUSTIFICATION:** app.py debe seleccionar la fuente Live YA integrada en el connector (T6.1)
  y cablear la maquina de estados (W2), persistencia/gate (W3/W4) y la primera-señal-post-SYNCED (T2.10).
  Depende tecnicamente de T6.1 (misma rama-conector). No depende de T6.3/T6.4.
- **Criterio de aceptacion:** simulacion byte-identica en su ruta; Live usa el mismo pipeline; ninguna
  señal antes de LIVE_SYNCED; SMA 9/21 sin cambios.
- **Tests esperados:** integracion (ambos modos) + regresion simulacion.
- **Evidencia:** diff minimo + tests.
- **REQUIRES_EXPLICIT_APPROVAL:** YES.

## T6.3 — `src/api/throttled_pusher.py`: timestamp ISO valido + status extendido backward-compatible
- **Objetivo:** corregir `last_time.isoformat() + "Z"` (Design 9) y añadir campos de status nuevos
  (T5.1/T5.2) sin romper los existentes.
- **Archivos protegidos:** `src/api/throttled_pusher.py`.
- **DEPENDS_ON:** [T5.1, T5.2, CP-2, G0.7].
- **DEPENDENCY_JUSTIFICATION:** el fix de timestamp ISO y los campos de status nuevos derivan del contrato
  (T5.1) y el serializer (T5.2); NO requieren el connector Live (T6.1) ni la seleccion de fuente (T6.2).
  Raiz de la rama-UI; paralelizable con T6.1/T6.2 tras CP-2.
- **Criterio de aceptacion:** status emite ISO valido; campos nuevos aditivos; throttle 1/seg sin cambios.
- **Tests esperados:** unit (ISO valido; compatibilidad de payload).
- **Evidencia:** diff minimo + tests.
- **REQUIRES_EXPLICIT_APPROVAL:** YES.

## T6.4 — `dashboard/index.html`: nuevos estados + fix visual minimo
- **Objetivo:** renderizar Databento/Market Data/Signal Record/Signals segun T5.4 y el copy T5.3; mantener
  guard de timestamp; dedup de marcadores por identidad de señal (T4.7); sin rediseño mayor.
- **Archivos protegidos:** `dashboard/index.html`.
- **DEPENDS_ON:** [T6.3, T5.3, T5.4, CP-2].
- **DEPENDENCY_JUSTIFICATION:** el dashboard consume el payload de status extendido que produce T6.3, el
  copy (T5.3) y el contrato de UI (T5.4). Depende de T6.3 (misma rama-UI). No depende tecnicamente de
  T6.1/T6.2 como cambio documental (el render de estados no exige el connector); el end-to-end final si
  requiere que app.py (T6.2) emita los estados.
- **Criterio de aceptacion:** el socio distingue CONNECTED de WARMING_UP/SYNCING/LIVE_SYNCED y ve
  DEGRADED/BLOCKED; comportamiento actual no roto.
- **Tests esperados:** verificacion manual + (si se añaden) tests de front.
- **Evidencia:** diff minimo + captura.
- **REQUIRES_EXPLICIT_APPROVAL:** YES.

---

# WAVE 7 — Tests y regresion

## T7.1 — Matriz de tests Databento (Live subscription, connection state, reconnect, backoff, closed candle)
- **Dependencias:** W1, T6.1. **Evidencia:** matriz + test pass. **REQUIRES_EXPLICIT_APPROVAL:** NO.
## T7.2 — Tests Synchronization (warm-up, no-signals-during-warm-up, boundary, dedup, out-of-order, recoverable/irrecoverable gap, re-warm-up, SignalEngine replacement)
- **Dependencias:** W2, T6.2. **Evidencia:** test pass. **REQUIRES_EXPLICIT_APPROVAL:** NO.
## T7.3 — Tests Persistence (durable Context, idempotent retries, ACK ambiguity, suppressed signals, no retroactive publish, recovery)
- **Dependencias:** W3, W4. **Evidencia:** test pass. **REQUIRES_EXPLICIT_APPROVAL:** NO.
## T7.4 — Tests Publication (no publish before commit, publish after commit, WebSocket retry sin marcador duplicado)
- **Dependencias:** W4, T6.2/T6.4. **Evidencia:** test pass. **REQUIRES_EXPLICIT_APPROVAL:** NO.
## T7.5 — Tests Time (signal_market_timestamp, signal_generated_at, signal_emitted_at, ISO 8601 status)
- **Dependencias:** T3.5, T6.3, G0.7. **Evidencia:** test pass. **REQUIRES_EXPLICIT_APPROVAL:** NO.
## T7.6 — Regresion (SignalEngine SMA 9/21 unchanged, Fase A Historical funcional, dashboard existente no roto, baseline de tests antes/despues)
- **Objetivo:** comparar la baseline conocida (283/297 passed / 12 failed Databento spec-ahead) antes y
  despues de W6; confirmar que los 12 spec-ahead ahora pasan o se explica cada cambio.
- **Dependencias:** W6 completo.
- **Criterio de aceptacion:** sin nuevas regresiones; SMA 9/21 intacto; Fase A intacta.
- **Evidencia:** salida de la suite antes/despues + relacion explicita con los 12 spec-ahead.
- **REQUIRES_EXPLICIT_APPROVAL:** NO.

## CP-3 — Regression Clean
- Suite y regresion satisfactorias tras integracion W6; sin nuevas regresiones. REQUIRES_EXPLICIT_APPROVAL: YES (confirmacion).

---

# WAVE 8 — AWS deployment / observability

## T8.1 — Despliegue de PostgreSQL seleccionado (RDS o contenedor) + secrets/config
- **Dependencias:** G0.9, G0.5, CP-4. **Gate:** G0.9, G0.5. **REQUIRES_EXPLICIT_APPROVAL:** YES.
## T8.2 — Inicializacion de esquema / migraciones en el entorno desplegado
- **Dependencias:** T8.1, T3.2, T3.3. **REQUIRES_EXPLICIT_APPROVAL:** YES.
## T8.3 — Health indicators desplegados (persistence health, Databento health) + logs
- **Dependencias:** T6.*, T8.2. **REQUIRES_EXPLICIT_APPROVAL:** YES.
## T8.4 — Identidad de despliegue verificable (git commit -> Docker image -> ECS)
- **Objetivo:** garantizar que la version desplegada es identificable por commit/imagen (Design seccion 10);
  NO aceptar `DEPLOYED_COMMIT = UNKNOWN` para Live Visual v1.
- **Dependencias:** G0.19, T8.1. **Gate:** G0.19.
- **Criterio de aceptacion:** cadena `git commit -> docker image -> ECS deployment` demostrable con evidencia.
- **Evidencia:** tag/imagen + descripcion del servicio ECS + version expuesta por la app.
- **REQUIRES_EXPLICIT_APPROVAL:** YES.
## T8.5 — Rollback + smoke tests de produccion
- **Dependencias:** T8.4. **REQUIRES_EXPLICIT_APPROVAL:** YES.

## CP-4 — Production Deployment Approval
- Aprobacion explicita antes de modificar AWS/ECS/RDS; G0.5/G0.6/G0.9 resueltos. REQUIRES_EXPLICIT_APPROVAL: YES.

---

# WAVE 9 — READY FOR TRADER REVIEW

## T9.1 — Checklist objetivo de los 25 criterios del Design
- **Objetivo:** verificar los 25 criterios (Design 23 / B.26 / C.18): Live conectado; NQ.c.0; ohlcv-1m;
  velas cerradas; continuidad; sin duplicados; warm-up correcto; ninguna señal antes de LIVE_SYNCED;
  SMA 9/21 correcto; BUY/SELL en vela correcta; market timestamp correcto; generated/emitted separados;
  Signal Context durable; signal_id trazable; persistence HEALTHY visible; bloqueo visible si DEGRADED;
  Databento state visible; Market Data state visible; gaps bloquean señales; re-warm-up funciona; LIVE
  inequivoco; ATR/Volume/Risk no afectan ni aparecen; sin claims de rentabilidad; AWS identificable.
- **Dependencias:** W6, W7, W8. **Evidencia:** checklist con evidencia por criterio. **REQUIRES_EXPLICIT_APPROVAL:** NO.
## T9.2 — Prueba de estabilidad durante la ventana aprobada
- **Objetivo:** ejecutar la ventana Live de estabilidad; su DURACION depende de G0.18 (no inventar).
- **Dependencias:** T9.1, G0.18. **Gate:** G0.18.
- **Criterio de aceptacion:** estabilidad sostenida durante la ventana aprobada; evidencia registrada.
- **REQUIRES_EXPLICIT_APPROVAL:** YES (declara READY).

## CP-5 — Trader Review Ready
- Todos los criterios de W9 cumplidos con evidencia. REQUIRES_EXPLICIT_APPROVAL: YES.

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": "WAVE_0", "name": "Decisions & Gates", "depends_on": [], "parallelizable_with": [] },
    { "id": "WAVE_1", "name": "Databento Live foundations", "depends_on": ["WAVE_0"], "parallelizable_with": ["WAVE_3"] },
    { "id": "WAVE_2", "name": "Warm-up / sync / recovery", "depends_on": ["WAVE_1"], "parallelizable_with": [] },
    { "id": "WAVE_3", "name": "PostgreSQL / LiveSession / Capa 1", "depends_on": ["WAVE_0"], "parallelizable_with": ["WAVE_1"] },
    { "id": "WAVE_4", "name": "persist-before-publish / publication gate", "depends_on": ["WAVE_2", "WAVE_3"], "parallelizable_with": [] },
    { "id": "WAVE_5", "name": "WebSocket / status / dashboard foundations", "depends_on": ["WAVE_2", "WAVE_4"], "parallelizable_with": [] },
    { "id": "CP_1", "name": "Foundations Ready (technical, Kiro)", "depends_on": ["WAVE_1", "WAVE_2", "WAVE_3", "WAVE_4", "WAVE_5"], "parallelizable_with": [] },
    { "id": "CP_2", "name": "Protected Components Approval (human)", "depends_on": ["CP_1"], "parallelizable_with": [] },
    { "id": "WAVE_6", "name": "Integration in protected components", "depends_on": ["CP_2"], "parallelizable_with": [], "internal_branches": { "connector_branch": ["T6.1", "T6.2"], "ui_branch": ["T6.3", "T6.4"], "note": "las dos ramas son independientes tras CP-2; aprobacion individual por Task" } },
    { "id": "WAVE_7", "name": "Tests & regression", "depends_on": ["WAVE_6"], "parallelizable_with": [] },
    { "id": "WAVE_8", "name": "AWS deployment / observability", "depends_on": ["WAVE_7"], "parallelizable_with": [] },
    { "id": "WAVE_9", "name": "Ready for trader review", "depends_on": ["WAVE_8"], "parallelizable_with": [] }
  ],
  "parallelizable": ["WAVE_1 || WAVE_3", "T6.1->T6.2 (connector branch) || T6.3->T6.4 (ui branch) within WAVE_6 after CP_2"],
  "not_parallelizable": ["WAVE_2->WAVE_1", "WAVE_4->WAVE_3", "CP_1->WAVE_1..5", "CP_2->CP_1", "WAVE_6->CP_2", "T6.2->T6.1", "T6.4->T6.3", "WAVE_7->WAVE_6", "WAVE_8->WAVE_7", "WAVE_9->WAVE_8"],
  "protected_component_waves": ["WAVE_6"]
}
```

```
WAVE 0 (T0.1 + G0.1..G0.19)
   └─► CP-0 (Decisions Ready: gates BLOCKS_IMPLEMENTATION resueltos)
          ├─► WAVE 1 (Databento Live foundations)          ─┐
          ├─► WAVE 3 (PostgreSQL / LiveSession / Capa 1)    ─┤ (1 y 3 en PARALELO)
          │                                                  │
          ▼                                                  │
       WAVE 2 (Warm-up / sync / recovery)  [dep W1]          │
          │                                                  │
          ├──────────────► WAVE 4 (persist-before-publish)  ◄┘ [dep W3]
          │                        │
          └───► WAVE 5 (WebSocket/status/dashboard foundations) [dep W2 estados + W4 persistence state]
                                   │
                                   ▼
                    CP-1 (Live foundations listas, sin tocar protegidos)
                                   ▼
                    CP-2 (Protected Components Approval)  ── REQUIRES_EXPLICIT_APPROVAL
                                   ▼
                         WAVE 6 (protegidos; 2 ramas independientes tras CP-2, aprobacion individual):
                             rama-conector: T6.1 → T6.2   ||   rama-UI: T6.3 → T6.4
                                   ▼
                         WAVE 7 (tests + regresion) ─► CP-3 (Regression Clean)
                                   ▼
                         CP-4 (Production Deployment Approval)
                                   ▼
                         WAVE 8 (AWS deployment / observability)
                                   ▼
                         WAVE 9 (READY FOR TRADER REVIEW) ─► CP-5
```

## Paralelizacion
- **Puede ejecutarse en paralelo:** WAVE 1 y WAVE 3 (foundations Live vs persistencia; independientes tras CP-0).
  Dentro de W5, T5.3 (copy) es independiente de T5.1/T5.2.
- **NO paralelizable:** WAVE 2 depende de W1; WAVE 4 depende de W3 (+ estados de W2); WAVE 6 depende de W1-W5
  + CP-2; W7 depende de W6; W8 depende de W7/CP-4; W9 depende de W8. Dentro de W6 hay DOS ramas
  independientes tras CP-2 (paralelizables si Hugo aprueba ambas): rama-conector T6.1→T6.2 y rama-UI
  T6.3→T6.4; T6.3/T6.4 NO dependen de T6.1/T6.2.
- **Checkpoints (6):** CP-0 (Decisions Ready), CP-1 (Foundations Ready — tecnico), CP-2 (Protected
  Components Approval — humano), CP-3 (Regression Clean), CP-4 (Production Deployment Approval),
  CP-5 (Trader Review Ready). Secuencia clave: CP-1 (tecnico) precede a CP-2 (humano) que precede a W6.

## Tasks que tocan componentes protegidos
- SOLO WAVE 6: T6.1 (databento_connector.py), T6.2 (app.py), T6.3 (throttled_pusher.py), T6.4 (dashboard/index.html).
  Todas con `REQUIRES_EXPLICIT_APPROVAL = YES` y precedidas por CP-2. Ninguna Task de W1-W5 modifica protegidos
  (las que dependen de integracion futura llevan `INTEGRATION_DEFERRED_TO_WAVE_6 = YES`).

## Scope audit (fuera de alcance — NO hay Tasks para esto)
NO se generaron Tasks para: Validation, Final OOS, ATR operativo/visible, VolumeFilter operativo, RiskGate
operativo, position sizing, TradingView webhook automatico, ML, QQQ, NDX, Greeks, GEX, full Order Book,
ejecucion de ordenes. ATR 1.75 solo aparece como research/metadata no operativa (Design 17) y NO es
dependencia de ninguna Task de Live Visual v1. No se introdujo funcionalidad fuera del Design aprobado.


---

## Notes

- Documento derivado estrictamente del Design APPROVED; ninguna Task amplia el alcance. Fuera de alcance
  (sin Tasks): Validation, Final OOS, ATR operativo/visible, VolumeFilter/RiskGate operativos, position
  sizing, TradingView webhook automatico, ML, QQQ, NDX, Greeks, GEX, full Order Book, ejecucion de ordenes.
- Componentes protegidos (`src/api/app.py`, `dashboard/index.html`, `src/api/throttled_pusher.py`,
  `src/connectors/databento_connector.py`, y `SignalEngine` SMA 9/21) solo se tocan en WAVE 6, con
  `REQUIRES_EXPLICIT_APPROVAL = YES` y tras el checkpoint CP-2. Ninguna Task de W1-W5 modifica protegidos.
- Todos los valores [SEGURO] estan consolidados como Decision Gates en WAVE 0 (G0.1-G0.19), clasificados
  en BLOCKS_IMPLEMENTATION / BLOCKS_PRODUCTION / BLOCKS_TRADER_REVIEW. No se invento ningun valor.
- Este turno es solo planificacion: no se implemento codigo, no se ejecutaron tests/Validation/Final OOS,
  no se hizo commit ni push. `DEPLOYED_COMMIT` sigue UNKNOWN hasta la evidencia de la Linea AWS externa
  (T8.4 lo cierra para Live Visual v1).
