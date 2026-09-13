# Implementation Plan — TradeCore Etapa 1

## Overview

Plan de implementación de la Etapa 1 (mejora matemática: stop dinámico ATR, filtro de volumen, trazabilidad, validación estadística, comparación visual y refactor del Dashboard). El motor BUY/SELL existente NO se modifica; la Etapa 1 se implementa como capas nuevas que lo envuelven.

**Reglas de gobernanza que rigen este plan:**
- Los valores `valor_por_punto`, `riesgo_pct` y `equity_disponible` son decisiones/hechos externos: la ACTIVACIÓN productiva de las tareas que dependen de ellos está BLOQUEADA hasta que Hugo los confirme (no se usan defaults silenciosos productivos).
- El mecanismo de stop del baseline (¿usa ATR o stop fijo?) debe verificarse ANTES de la calibración (Tarea 0).
- Los tres puntos que tocan componentes protegidos (`app.py`, `dashboard/index.html`, `throttled_pusher.py`) son tareas separadas que requieren aprobación explícita de Hugo antes de ejecutarse.
- RF-E1-05 y sus tareas están BLOQUEADAS hasta que Fase B (Tareas 12.1-12.3 de tradecore-mvp) esté validada. El resto (RF-E1-01 a RF-E1-04, RF-E1-07) puede secuenciarse con normalidad.
- Ningún test existente se modifica. Cada tarea que toque un componente protegido corre la suite de tests relacionada tras aprobarse.
- La calibración (Tarea 1) usa los módulos PRODUCTIVOS ya implementados y probados (Waves previas), NO una implementación experimental o temporal.
- **Regla de completitud:** una tarea de implementación NO se marca `[x]` (completa) hasta que su tarea de test correspondiente esté implementada y PASE. "Completo" significa implementado Y verificado por su test formal (property-based), no solo por un sanity check manual. Específicamente: la Tarea 2 no se marca completa hasta que la Tarea 2.1 pase; la Tarea 3 hasta que 3.1 pase; la Tarea 4 hasta que 4.1 pase; la Tarea 5 hasta que 5.1 pase; la Tarea 6 hasta que 6.1 pase. Esta regla aplica de forma consistente a todo el plan.

**Verificaciones de código realizadas antes de este plan (para no asumir):**
- **Risk_Gate:** NO existe implementación previa en el código base (búsqueda de `risk_gate`, `circuit_breaker`, `drawdown` sin coincidencias). La Tarea 6 se crea desde cero con alcance LIMITADO al cálculo de tamaño de posición (RF-E1-01 crit. 5-7); circuit breaker, límite de señales concurrentes y drawdown pertenecen al Requisito 13 completo y quedan FUERA de alcance de Etapa 1.
- **Backtesting_Module:** NO existe módulo de evaluación histórica previo. La Tarea 8 construye la base de evaluación sin duplicar un módulo inexistente, y se diseña como base reutilizable.
- **Probabilidad_de_Éxito / medida de confianza:** NO existe en el código base (búsqueda de `probabilidad`, `probability`, `confidence`, `score` en `src/` sin coincidencias). El motor actual es un cruce de SMA(9)/SMA(21) que NO produce ninguna probabilidad calibrada. Esto crea una brecha con el Requisito 4.7 (ver [SEGURO] en la Tarea 8).

---

## Tasks

- [x] 0. Prerrequisito bloqueante — Verificar el mecanismo de stop del baseline Pine Script
  - **BLOQUEA la calibración (Tarea 1).** PRIMERO verificar si el baseline "NQ Hybrid v11 - Sustainable Edge" usa ATR para su stop, o un stop fijo en puntos. Hallazgos de auditoría previos identificaron un "stop-loss fijo no ajustado por volatilidad" como debilidad del Pine Script.
  - Si el baseline usa **stop fijo (no ATR):** documentarlo como hallazgo y definir el método ATR de TradeCore de forma INDEPENDIENTE, sin necesidad de igualarlo al baseline.
  - Solo si el baseline **sí usa ATR:** determinar si es SMA (media simple del TR) o RMA/SMMA de Wilder, ya que producen valores distintos y afectan la calibración y la Validación B.
  - Salida: mecanismo de stop del baseline documentado y método ATR de TradeCore definido. Sin esto, NO ejecutar la Tarea 1.
  - **HALLAZGO (Tarea 0 completada y aprobada):** el código fuente del Pine Script baseline NO está en el workspace; su mecanismo de stop permanece DESCONOCIDO / `[SEGURO]` y bloquea EXCLUSIVAMENTE la Validación B (Tarea 9). Por separado, el método de suavizado del ATR PROPIO de TradeCore se decidió de forma INDEPENDIENTE: RMA/Wilder (`ATR_t = (ATR_{t-1}*(N-1) + TR_t)/N`, seed = media simple de los primeros `atr_period` TR), `atr_period` default = 14. Esta decisión NO asume el comportamiento del baseline y NO desbloquea la Validación B. Ver `design.md` punto 10(a) y 10(b). Detalle en `tarea0-hallazgo-baseline-stop.md`.
  - _Requisitos: RF-E1-01; Diseño: [SEGURO] punto 10_

- [x] 2. Implementar ATRCalculator (módulo nuevo)
  - Crear `src/engine/atr_calculator.py` con el método de suavizado definido en la Tarea 0.
  - True Range por vela y ATR sobre `atr_period` velas (default 14); `stop_points(candles, multiplier)`.
  - Manejo de datos insuficientes: menos de `atr_period + 1` velas -> devuelve `None`, motivo `insufficient_history_atr`.
  - _Requisitos: RF-E1-01_

- [x] 3. Implementar VolumeFilter (módulo nuevo)
  - Crear `src/engine/volume_filter.py`. `accept(candle, recent_candles, threshold_factor)` compara volumen contra promedio de `volume_period` velas (default 20).
  - Rechazo con motivo `low_volume`. Nunca cambia la dirección de la señal.
  - _Requisitos: RF-E1-02_

- [x] 4. Implementar SignalRecord + SignalJournal con interfaz abstracta JournalWriter (módulos nuevos)
  - Crear `src/schemas/signal_record.py` (envuelve `Signal`, NO lo modifica) con UUID, timestamp UTC, configuración matemática, stop_points, volume_accepted, discard_reason, position_size.
  - Crear `src/pipeline/signal_journal.py` definido contra una **interfaz abstracta `JournalWriter`**. La **implementación por defecto para tests y desarrollo es EN MEMORIA** (una lista/estructura en memoria, sin persistencia a disco). Esto desbloquea a la Tarea 5 sin esperar el formato definitivo.
  - Cualquier implementación de logging persistente o JSONL es parte del `[SEGURO]` de persistencia definitiva (se implementa como otra `JournalWriter` y se activa después), NO parte del default de pruebas.
  - _Requisitos: RF-E1-03_

- [x] 2.1 Tests de ATRCalculator (property-based)
  - Crear tests NUEVOS. Property 3: ATR nunca negativo; si High==Low==Close en todas las velas, ATR exactamente 0; datos insuficientes -> None.
  - _Requisitos: RF-E1-01; Diseño: Property 3_

- [x] 3.1 Tests de VolumeFilter (property-based)
  - Property 2: `direccion_entrada == direccion_salida`; volumen bajo umbral siempre rechaza con `low_volume`; volumen suficiente siempre pasa.
  - _Requisitos: RF-E1-02; Diseño: Property 2_

- [x] 4.1 Tests de trazabilidad (property-based)
  - Property 6: toda señal (emitida o descartada) produce exactamente un SignalRecord con UUID, timestamp UTC y configuración. Property 7: reproducibilidad dado el mismo input. Se prueba contra la implementación EN MEMORIA de `JournalWriter`.
  - _Requisitos: RF-E1-03, RF-E1-04; Diseño: Property 6, Property 7_

- [ ] 1. Calibración inicial de parámetros Etapa 1 — en tres etapas comparables
  - **BLOQUEADA hasta completar Wave 1 (Tareas 2, 3, 4) + Wave 2 (Tests 2.1, 3.1, 4.1).** NO está lista para ejecutarse aún.
  - Método de suavizado del ATR de TradeCore: RMA/Wilder, decidido de forma INDEPENDIENTE y aprobado (Tarea 0). NO depende de confirmar el mecanismo de stop del baseline; ese sigue `[SEGURO]` y bloquea únicamente la Validación B (Tarea 9), no esta calibración.
  - Depende de que ATRCalculator (2), VolumeFilter (3) y SignalRecord/Journal (4) estén implementados y probados (2.1, 3.1, 4.1). Usa los módulos PRODUCTIVOS, no una implementación temporal.
  - Backtest exploratorio sobre datos históricos de NQ estructurado en **tres etapas comparables** para atribuir el efecto de cada componente por separado:
    - (a) "TradeCore actual (sin ATR ni filtro de volumen)";
    - (b) "TradeCore actual + ATR" (calibrar `atr_multiplier` en rango 1.5x-3x);
    - (c) "TradeCore actual + ATR + filtro de volumen" (calibrar `volume_threshold_factor` en rango 0.5x-0.8x).
  - Esto permite saber si el filtro de volumen aporta valor real por separado del ATR, en vez de un resultado combinado indistinguible.
  - **Nota terminológica:** en estas tres etapas, "TradeCore actual" es el punto de partida de la calibración interna. La palabra "baseline" se reserva EXCLUSIVAMENTE para el Pine Script externo "NQ Hybrid v11 - Sustainable Edge" (ver Tarea 9), y NO se usa aquí para evitar la doble acepción.
  - **Separación de datos (protege el Requisito 4.7):** los datos históricos usados para seleccionar `atr_multiplier` y `volume_threshold_factor` en esta calibración NO pueden formar parte de la muestra out-of-sample usada después por la Tarea 8 (Validación A). Debe existir una separación clara entre el conjunto de calibración y el de validación, de modo que el resultado de la Validación A no esté sesgado por haberse ajustado sobre los mismos datos.
  - Salida: valores propuestos de `atr_multiplier` y `volume_threshold_factor` con evidencia por etapa, y la partición de datos calibración/validación documentada.
  - **Metodología del dataset (documentada, no ejecutada aquí):** la estructura del dataset histórico de investigación (3 años de NQ.c.0, dataset GLBX.MDP3, schema ohlcv-1m, Parquet maestro + meta JSON + SHA-256 + dataset_id, tres splits temporales con convención `[inicio, fin)`, reglas de warm-up con `warmup_min` derivado por fórmula, respaldo redundante y advertencia de persistencia efímera) está documentada en `design.md`, sección "Dataset histórico de investigación (Etapa 1)". Esta Tarea 1 NO descarga datos ni ejecuta calibración hasta cumplir sus prerrequisitos de Wave.
  - _Requisitos: RF-E1-01, RF-E1-02; Diseño: Nota sobre la Tarea de Calibración, Dataset histórico de investigación (Etapa 1)_

- [x] 6. RiskGate — implementación (avanza ya) + activación productiva (bloqueada)
  - **NOTA DE COMPLETITUD:** SOLO la parte (a) (implementación de `position_size` + tests 6.1) está completa y verificada. La parte (b) (activación productiva con `valor_por_punto`, `riesgo_pct` y `equity_disponible` reales) permanece BLOQUEADA pendiente de la confirmación de esos tres valores por parte de Hugo.
  - **Verificación previa hecha:** no existe implementación previa de Risk_Gate. Se crea desde cero.
  - **Alcance LIMITADO:** EXCLUSIVAMENTE el cálculo de tamaño de posición de RF-E1-01 (crit. 5-7): `position_size(stop_pts, equity, risk_pct, value_per_point)`. NO implementa circuit breaker, límite de señales concurrentes ni drawdown diario (Requisito 13, FUERA de alcance). Si se implementa el Requisito 13 en el futuro, esta función se reutiliza/extiende, no se duplica.
  - **(a) Implementación y tests — PUEDE AVANZAR YA:** crear `src/engine/risk_gate.py` y sus tests usando parámetros EXPLÍCITOS de prueba (ej. equity=10000, risk_pct=0.01, value_per_point=20). Estos valores son SOLO para pruebas; NO se convierten en defaults productivos. Puede avanzar en paralelo a la Tarea 1 (no depende de sus resultados).
  - **DECISIÓN CONFIRMADA (Hugo):** conversión del tamaño teórico a contratos enteros operables mediante `floor` (redondeo hacia abajo). El riesgo real autorizado nunca debe superar `equity * risk_pct`. Si el resultado entero es menor que 1 contrato (`floor == 0`), la señal se considera NO operable y se descarta con el motivo de trazabilidad `position_below_min_contract`. `position_size()` devuelve el número entero de contratos cuando es operable, o `None` (con `reason`) cuando no lo es. Se descarta redondear hacia arriba porque violaría el tope de riesgo.
  - **(b) Activación con configuración real — BLOQUEADA:** el uso productivo con `valor_por_punto` (NQ=20 / MNQ=2 USD/pt), `riesgo_pct` (ref. 1-2%) y `equity_disponible` permanece bloqueado hasta que Hugo confirme los tres valores. La parte (a) NO queda detenida por esto.
  - _Requisitos: RF-E1-01 (crit. 5-7)_

- [ ] 5. Implementar SignalPipeline (orquestador, módulo nuevo)
  - Depende de ATRCalculator (2), VolumeFilter (3), SignalRecord/Journal (4) Y RiskGate implementación parte (a) (6).
  - Crear `src/engine/signal_pipeline.py`. Aplica el orden: señal -> VolumeFilter -> (si aceptada) ATR -> RiskGate -> SignalRecord -> Journal. Rama rechazada también produce SignalRecord.
  - NO invoca ATR ni RiskGate para señales rechazadas por volumen.
  - _Requisitos: RF-E1-01, RF-E1-02, RF-E1-03_

- [ ] 5.1 Tests del orden del pipeline (property-based)
  - Property 5: señal rechazada por volumen nunca invoca ATR ni RiskGate. Property 1: el motor BUY/SELL produce la misma dirección con y sin las capas de Etapa 1 (no regresión).
  - _Requisitos: RF-E1-01, RF-E1-02; Diseño: Property 1, Property 5_

- [x] 6.1 Tests de RiskGate (property-based)
  - Property 4: `stop_pts` 0 o None nunca produce excepción; RiskGate devuelve None y registra `stop_unavailable`; el tamaño escala inverso a `stop_pts`. Usa los parámetros explícitos de prueba de la Tarea 6(a).
  - _Requisitos: RF-E1-01; Diseño: Property 4_

- [ ] 7. [REQUIERE APROBACIÓN DE HUGO] Integrar SignalPipeline en app.py (componente protegido)
  - Depende de SignalPipeline (5) y sus tests (5.1).
  - `src/api/app.py` está protegido (Nivel 1). Modificación mínima: en `_processing_loop`, reemplazar la llamada directa `queue_signal(signal)` por el paso a `SignalPipeline.process(...)`, emitiendo solo señales aceptadas.
  - NO altera el motor BUY/SELL ni el protocolo WebSocket.
  - Requiere aprobación explícita de Hugo ANTES de tocar el archivo. Tras aplicar, correr la suite de tests relacionada.
  - _Requisitos: RF-E1-01, RF-E1-02, RF-E1-03; Diseño: [REQUIERE APROBACIÓN] punto 1_

- [ ] 8. Validación A — out-of-sample vs tasa base (verificar existencia de Probabilidad_de_Éxito primero)
  - **Verificación previa hecha:** no existe Backtesting_Module previo; se construye la base de evaluación sin duplicar un módulo inexistente, diseñada como base reutilizable.
  - **[SEGURO] — Brecha entre Requisito 4.7 y el motor actual:** verificar si la implementación actual produce alguna Probabilidad_de_Éxito o medida de confianza utilizable para el criterio del Requisito 4.7. Verificación de código ya realizada: el motor es un cruce de SMA(9)/SMA(21) y NO produce ninguna probabilidad calibrada (sin coincidencias de `probability`/`confidence`/`score` en `src/`). NO inventar ni aproximar artificialmente esa medida solo para poder ejecutar la validación. Marcar como [SEGURO]: "Verificar si la implementación actual produce una Probabilidad_de_Éxito real. Si no existe, la Validación A (RF-E1-04) no puede ejecutarse tal como está definida en el Requisito 4.7 hasta que se decida cómo derivar o aproximar esa medida de confianza a partir del motor SMA actual, o hasta que el Quant_Model completo se implemente en una etapa futura."
  - Implementación (cuando el [SEGURO] se resuelva): crear `src/validation/validator_oos.py`. Probabilidad_de_Exito out-of-sample; umbral superado solo si supera en +5 puntos porcentuales la tasa base. Ventana configurable y reproducible (fecha inicio/fin, resolución, criterio de inclusión). La muestra out-of-sample debe estar separada de los datos de calibración de la Tarea 1.
  - _Requisitos: RF-E1-04 (Validación A)_

- [ ] 9. [BLOQUEADA — baseline no documentado] Implementar Validación B — vs baseline Pine Script
  - Crear `src/validation/validator_baseline.py` reutilizando la base de evaluación de la Tarea 8 (misma metodología para ambas fuentes). Métricas: tasa de aciertos, ratio riesgo/beneficio, MAE, RMSE.
  - **BLOQUEADA:** el comportamiento, parámetros y metodología del baseline Pine Script no están documentados. Requiere confirmación (incluida la aplicabilidad de MAE/RMSE a un sistema que solo emite BUY/SELL con target y stop).
  - _Requisitos: RF-E1-04 (Validación B); Diseño: [SEGURO] punto 7_

- [ ] 10. [REQUIERE APROBACIÓN DE HUGO] Refactor del Dashboard (componente protegido)
  - `dashboard/index.html` está protegido (Nivel 1). Extraer CSS a `dashboard/styles.css` y JS a `dashboard/app.js`; el HTML queda solo con estructura + referencias. Margen exterior de 8px en el contenedor.
  - NO cambiar lógica de WebSocket, manejo de velas ni marcadores: comportamiento observable idéntico.
  - Requiere aprobación explícita de Hugo ANTES de tocar el archivo. Verificable: sin CSS/JS embebido en el HTML.
  - _Requisitos: RF-E1-07; Diseño: [REQUIERE APROBACIÓN] punto 2_

- [ ] 11. [BLOQUEADA por Fase B] Ingesta del webhook de TradingView
  - Crear `src/connectors/tradingview_webhook.py`. Normalizar timestamps a ISO 8601 UTC aceptando los tres formatos: Unix en segundos, Unix en milisegundos, e ISO con offset de zona horaria.
  - **BLOQUEADA:** RF-E1-05 depende de Fase B (Tareas 12.1-12.3 de tradecore-mvp) validada. Además [SEGURO]: formato/autenticación del webhook.
  - _Requisitos: RF-E1-05; Diseño: [SEGURO] punto 8, RF-E1-06_

- [ ] 12. [BLOQUEADA por Fase B + REQUIERE APROBACIÓN DE HUGO] Comparación visual de ambas fuentes
  - **Decisión de diseño previa OBLIGATORIA (antes de escribir código):** revisar el protocolo actual de `throttled_pusher.py` y decidir UNA sola opción entre añadir un campo `source` a los mensajes de señal existentes O crear un nuevo tipo `signal_external`. Documentar la decisión y su justificación EN EL CÓDIGO (docstring/comentario del mecanismo elegido), no como "alternativa considerada". Guía: preferir `source` si el frontend maneja el tipo `signal` de forma uniforme; preferir `signal_external` si el tratamiento visual/lógico difiere sustancialmente.
  - Extender `src/api/throttled_pusher.py` (componente protegido) con la opción elegida, sin romper el protocolo existente.
  - Dashboard muestra ambas fuentes alineadas por timestamp UTC, identificadas visualmente, con indicación clara si una fuente cae.
  - **DOBLE BLOQUEO:** requiere Fase B validada Y aprobación explícita de Hugo para tocar `throttled_pusher.py`. Además [SEGURO]: tolerancia de alineación de velas entre feeds.
  - _Requisitos: RF-E1-05; Diseño: [REQUIERE APROBACIÓN] punto 3, [SEGURO] punto 9, RF-E1-06_

---

## Proceso de ejecución — punto de control obligatorio

**Checkpoint antes de la Tarea 7 (tocar src/api/app.py):** al completar el desarrollo y pruebas de las Tareas 0 a 6 (incluida 6a) y la Tarea 5, DETENERSE antes de tocar `src/api/app.py` y presentar a Hugo:

1. Qué archivos nuevos se crearon (lista de paths).
2. Resultado completo de la suite de tests nuevos (Properties 1 a 7).
3. Los valores obtenidos en la calibración (Tarea 1) para `atr_multiplier` y `volume_threshold_factor`, con la evidencia comparativa de las tres etapas ("TradeCore actual" / "+ATR" / "+ATR+filtro de volumen").
4. El cambio exacto y mínimo propuesto en `_processing_loop` de `app.py` (diff o descripción línea por línea).

NO modificar `app.py` hasta que Hugo autorice explícitamente tras revisar esa evidencia.

**La misma lógica de punto de control aplica a la Tarea 10** (`dashboard/index.html`) **y a la Tarea 12** (`src/api/throttled_pusher.py`): presentar la evidencia y el cambio mínimo propuesto, y esperar autorización explícita antes de tocar cada componente protegido.

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["0"] },
    { "id": 1, "tasks": ["2", "3", "4"] },
    { "id": 2, "tasks": ["2.1", "3.1", "4.1"] },
    { "id": 3, "tasks": ["1", "6"] },
    { "id": 4, "tasks": ["5"] },
    { "id": 5, "tasks": ["5.1", "6.1"] },
    { "id": 6, "tasks": ["7"] },
    { "id": "INDEPENDENT", "tasks": ["8"] },
    { "id": "BLOCKED_BASELINE", "tasks": ["9"] },
    { "id": "APPROVAL", "tasks": ["10"] },
    { "id": "BLOCKED_FASE_B", "tasks": ["11", "12"] }
  ]
}
```

**Notas sobre el grafo:**
- Wave 3 contiene la Tarea 1 (calibración) y la Tarea 6 (RiskGate implementación parte a) como **ramas paralelas**: NO dependen entre sí, solo comparten prerequisitos previos. Se ejecutan en la misma wave.
- Wave 4 (Tarea 5, SignalPipeline) depende de Tareas 2, 3, 4 (Wave 1) Y de la implementación de RiskGate (Tarea 6, parte a).
- Tarea 8 (Validación A) es independiente de la cadena del pipeline; se marca INDEPENDENT. Su ejecución real está condicionada al [SEGURO] de Probabilidad_de_Éxito.

---

## Notes

- **Tareas que pueden avanzar ya (RF-E1-01 a RF-E1-04, RF-E1-07):** 0 (completada), 2, 3, 4, 2.1, 3.1, 4.1, 6(a), 6.1. La Tarea 1 (Calibración) NO puede avanzar aún: está BLOQUEADA hasta completar Wave 1 (2, 3, 4) y Wave 2 (2.1, 3.1, 4.1). Las Tareas 5 y 5.1 dependen de que Wave 1 y 6(a) estén listas. La Validación A (8) puede prepararse pero su ejecución depende del [SEGURO] de Probabilidad_de_Éxito. Con aprobación de Hugo: 7 y 10.
- **Bloqueadas por confirmación de valores de Hugo:** solo la ACTIVACIÓN productiva de RiskGate (Tarea 6, parte b); la implementación y tests (parte a) avanzan con parámetros de prueba explícitos.
- **Bloqueadas por aprobación de componente protegido:** 7 (app.py), 10 (dashboard), 12 (throttled_pusher).
- **Bloqueadas por baseline no documentado:** 9 (Validación B).
- **Bloqueada por brecha metodológica:** ejecución de la Validación A (8) hasta resolver el [SEGURO] de Probabilidad_de_Éxito.
- **Bloqueadas por Fase B:** 11 y 12 (RF-E1-05).
- **Separación de datos:** los datos de calibración (Tarea 1) y de validación out-of-sample (Tarea 8) deben ser conjuntos disjuntos.
- **Alcance acotado explícito:** Tarea 6 (RiskGate) NO implementa circuit breaker / concurrencia / drawdown (Requisito 13, fuera de alcance). Tarea 8 no duplica un Backtesting_Module inexistente.
- **Sin implementaciones temporales:** la calibración (Tarea 1) usa módulos productivos ya probados. Los parámetros de prueba de RiskGate (6a) NO son defaults productivos.
- **Regla de completitud (checkbox):** una tarea de implementación NO se marca `[x]` (completa) hasta que su tarea de test correspondiente esté implementada y PASE. "Completo" significa implementado Y verificado por su test formal (property-based), no solo por un sanity check manual. Específicamente: la Tarea 2 no se marca completa hasta que la Tarea 2.1 pase; la Tarea 3 hasta que 3.1 pase; la Tarea 4 hasta que 4.1 pase; la Tarea 5 hasta que 5.1 pase; la Tarea 6 hasta que 6.1 pase. Esta regla aplica de forma consistente a todo el plan.