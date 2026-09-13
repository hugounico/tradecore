# Design Document

## Overview

Este documento define el Diseño técnico de la Etapa 1 de TradeCore, a partir de los Requisitos aprobados (`requirements.md` de este mismo spec). El principio rector se mantiene: el motor de generación de señales BUY/SELL (compra/venta) existente NO se modifica. La Etapa 1 se implementa como **capas nuevas que envuelven y consumen** la salida del motor existente, no como cambios dentro de él.

**Relación con componentes protegidos:** varios archivos de Fase A están protegidos (ver `fase-actual-etapa1.md`). Este diseño prioriza no tocarlos. Donde una integración obligue a rozar un componente protegido, se marca explícitamente como **[REQUIERE APROBACIÓN]** y se propone la modificación mínima. Nada de esto se implementa hasta que se apruebe el diseño y, para los puntos protegidos, hasta aprobación explícita adicional.

**Alcance de avance:** conforme a RF-E1-06, solo RF-E1-05 (comparación visual en tiempo real) depende de Fase B y queda bloqueado. RF-E1-01 a RF-E1-04 y RF-E1-07 pueden diseñarse e implementarse de forma independiente.

---

## Dataset histórico de investigación (Etapa 1)

Esta sección documenta la **metodología aprobada del dataset histórico** (conjunto de datos de precios pasados) que la Etapa 1 usa para calibrar parámetros (Tarea 1) y validar estadísticamente (Tareas 8 y 9). Es documentación de metodología: NO autoriza descargar datos, ejecutar calibración ni implementar módulos aquí. Su propósito es dejar por escrito CÓMO se estructura el dataset y las reglas que garantizan que los resultados sean reproducibles y sin sesgo.

**Glosario breve de esta sección:**
- **Dataset** (conjunto de datos): la colección completa de velas históricas de precios.
- **Vela / candle** (barra OHLCV): resumen del precio en un intervalo de tiempo con apertura (Open), máximo (High), mínimo (Low), cierre (Close) y volumen (Volume — cantidad negociada).
- **Parquet** (formato de archivo columnar): formato de archivo eficiente para guardar tablas de datos grandes; ocupa menos espacio y se lee rápido.
- **SHA-256** (Secure Hash Algorithm 256 bits): función que produce una "huella digital" única de un archivo; si un solo byte cambia, la huella cambia. Sirve para verificar que dos copias son idénticas.
- **Metadata / meta JSON** (metadatos): datos que describen al dataset (fechas, símbolo, versión, huella SHA-256), guardados en un archivo JSON (formato de texto estructurado).
- **dataset_id** (identificador del dataset): código que identifica de forma única esta versión concreta del dataset.
- **Split temporal** (partición por tiempo): división del dataset en tramos cronológicos consecutivos que NO se solapan, para que cada tramo cumpla una función distinta (calibrar, validar, prueba final).
- **Warm-up** (calentamiento de indicadores): velas previas que solo sirven para "arrancar" el cálculo de un indicador que necesita historia anterior, sin contar como observaciones evaluadas.

### Propuesta aprobada (parámetros del dataset)

Configuración del dataset maestro (coherente con `databento-architecture.md`, sin cambiar ninguna decisión de Fase A):

- **Cobertura:** 3 años de historia de NQ (E-mini Nasdaq-100).
- **Símbolo:** `NQ.c.0` (contrato continuo, resuelto por Databento).
- **Dataset:** `GLBX.MDP3` (CME Globex Market Data Platform 3.0).
- **Schema:** `ohlcv-1m` (velas OHLCV de 1 minuto).
- **stype_in:** `continuous` (tipo de símbolo continuo).
- **Artefacto maestro:** un único archivo **Parquet maestro** + un archivo **meta JSON** (metadatos) + una huella **SHA-256** del Parquet + un **dataset_id** que identifica la versión.

### Tres splits temporales (convención `[inicio, fin)`)

El dataset se parte en tres tramos cronológicos consecutivos. La convención `[inicio, fin)` significa **intervalo semiabierto**: incluye el instante `inicio` y EXCLUYE el instante `fin` (es decir, `fin` pertenece al tramo siguiente). Esto evita que una misma vela caiga en dos tramos a la vez.

| Split | Función | Intervalo `[inicio, fin)` |
|---|---|---|
| **Calibración** (Calibration) | ajustar `atr_multiplier` y `volume_threshold_factor` (Tarea 1) | `[2023-09-13T00:00:00Z, 2025-07-01T00:00:00Z)` |
| **Validación** (Validation) | validar sin haber ajustado sobre estos datos | `[2025-07-01T00:00:00Z, 2026-02-02T00:00:00Z)` |
| **OOS final** (Final out-of-sample) | prueba final sobre datos nunca vistos | `[2026-02-02T00:00:00Z, 2026-09-12T00:00:00Z)` |

Todas las marcas de tiempo están en UTC (Coordinated Universal Time — zona horaria universal de referencia), formato ISO 8601 (estándar internacional de fecha y hora). Esta partición respeta la "Separación de datos" ya exigida en la Tarea 1 y la Tarea 8: los datos de calibración y los de validación son conjuntos disjuntos, de modo que la validación no quede sesgada por haberse ajustado sobre los mismos datos.

#### Justificación de las fechas de corte

- **Corte `2025-07-01T00:00:00Z`:** este instante cae un **martes**. NO se justifica como "inicio de semana" (no lo es). Su justificación es simplemente que es **inicio de mes** (1 de julio) y queda **cerca del objetivo temporal de ~60%** de la cobertura total del dataset. Se conserva la convención `[inicio, fin)`: esta vela pertenece al split de Validación y queda excluida del de Calibración.
- **Corte `2026-02-02T00:00:00Z`:** este instante cae un **lunes**, por lo que sí es **inicio de semana** (además de estar cerca del objetivo temporal para separar Validación de OOS final). Ese encuadre de "inicio de semana" es correcto para este corte. Se conserva la convención `[inicio, fin)`: esta vela pertenece al split OOS final y queda excluida del de Validación.

### Warm-up de indicadores (calentamiento)

Los splits de **Validación** y **OOS final** PUEDEN usar velas cronológicamente **inmediatamente ANTERIORES** a su timestamp de inicio, puramente como **contexto de calentamiento** (warm-up) para inicializar los indicadores que necesitan historia previa (por ejemplo, el ATR y las medias móviles necesitan varias velas anteriores antes de producir su primer valor válido).

**Reglas del warm-up (obligatorias):**

1. Las velas de warm-up se usan **ÚNICAMENTE** como entradas históricas para inicializar/calcular los indicadores.
2. Las velas de warm-up **NO** forman parte de las observaciones evaluadas ni de las métricas del split que empieza después del corte.
3. **No** puede usarse ninguna información **POSTERIOR** al timestamp evaluado para calcular una característica (feature) de ese instante.
4. Ningún resultado, etiqueta o métrica de Validación o de OOS final puede **retroalimentar** (feed-back) hacia la Calibración.
5. Las fronteras oficiales `[inicio, fin)` de los tres splits **NO cambian** por el uso de warm-up. El warm-up es contexto de cálculo, no una extensión del split.

**Por qué esto NO es fuga de datos (data leakage):** se usa exclusivamente información **cronológicamente anterior** al timestamp evaluado, exactamente como ocurriría en producción (en vivo, un indicador siempre se calcula con las velas que ya cerraron antes del instante actual). No se está "mirando el futuro"; se está reproduciendo la misma condición histórica que tendría el sistema operando en tiempo real.

#### Regla derivada del warm-up mínimo (`warmup_min`)

La cantidad de velas de warm-up NO es un número fijo arbitrario: se **deriva de las implementaciones reales de los indicadores** (verificadas en el código actual). Hechos verificados:

- **SMA(fast=9)** (Simple Moving Average — promedio móvil simple, rápido): necesita `fast_period` = **9** cierres para producir su primer valor.
- **SMA(slow=21)** (promedio móvil simple, lento): necesita `slow_period` = **21** cierres. Además, `SignalEngine.evaluate` devuelve `None` mientras `len(closes) < slow_period`, y la detección de cruce (crossover) necesita un **estado PREVIO válido** (el motor guarda `_prev_fast`/`_prev_slow`), por lo que la primera señal potencial requiere **una evaluación previa adicional**.
- **ATR(atr_period=14)** (Average True Range): `ATRCalculator` devuelve `None` con menos de `atr_period + 1` velas → necesita **15** velas (el primer True Range necesita el cierre de la vela anterior).
- **Promedio de volumen (volume_period=20):** necesita **20** velas para un promedio de ventana completa (el filtro tolera menos velas, pero el promedio completo requiere 20).

**Fórmula derivada (no constante mágica):**

```
warmup_min = max(fast_period, slow_period + crossover_state_requirement, atr_period + 1, volume_period)
```

El `+ 1` NO es un margen genérico aplicable a todos los indicadores: corresponde **específicamente** a que `SignalEngine` necesita una evaluación de SMA **PREVIA válida** para detectar un cruce (crossover). Por eso ese `+ 1` se modela como un componente nombrado explícito — `crossover_state_requirement` — **dentro del término de `slow_period`**, no como un `+ 1` colgado al final de la fórmula.

**Valor con los períodos configurados actualmente:**

```
fast_period                = 9
slow_period                = 21
crossover_state_requirement = 1
atr_period                 = 14
volume_period              = 20

warmup_min = max(9, 21 + 1, 14 + 1, 20) = max(9, 22, 15, 20) = 22 velas
```

**El resultado (22) NO cambia.** Lo único que se corrige es la **atribución** de cada requisito de historia, que ahora es correcta:

- `fast_period = 9` → SMA(9) necesita **9** cierres.
- `slow_period + crossover_state_requirement = 21 + 1 = 22` → SMA(21) necesita **21** cierres **Y** el detector de cruces necesita **una evaluación de SMA previa válida** (ese es el `+ 1`, atribuido al estado de crossover de `SignalEngine` — `_prev_fast`/`_prev_slow` —, NO a un margen genérico).
- `atr_period + 1 = 15` → ATR necesita `period + 1` velas (el primer True Range necesita el cierre de la vela anterior).
- `volume_period = 20` → promedio de volumen de ventana completa.

**Regla de mantenimiento:** el valor **22 no es una constante fija**; es el valor ACTUAL derivado de la configuración/implementación activa. Si cualquier período configurable cambia más adelante (por ejemplo, otro `atr_period` o `slow_period`), `warmup_min` se **recalcula con la fórmula anterior**. No se debe hardcodear 22 como número mágico. Esta sección **no modifica los indicadores** — solo documenta la regla derivada de su comportamiento ya implementado.

**Reiteración de las reglas del warm-up (ver arriba):** las velas de warm-up son **exclusivamente contexto histórico**; **NO** pasan a formar parte del set siguiente, **NO** participan en sus métricas, **NO** modifican las fronteras aprobadas `[inicio, fin)`, y **NUNCA** pueden incorporar información posterior al timestamp evaluado.

### Respaldo redundante del dataset maestro

Como el directorio `data/` está fuera de Git (control de versiones) y NO existe base de datos en el proyecto, el dataset **no debe depender de una única copia local**. Se documenta la siguiente estructura de respaldo:

- **Copia de trabajo:** `TradeCore/data/historical/`, que contiene al menos el Parquet maestro y su `.meta.json` (metadatos).
- **Backup independiente:** una **SEGUNDA copia FUERA del directorio de trabajo de TradeCore**, en una **ubicación CONFIGURABLE**. NO se asume S3, PostgreSQL, EFS ni ningún proveedor o infraestructura específica; solo se exige que sea una segunda copia en otra ubicación.
- **Contenido del backup:** debe incluir tanto el Parquet como los metadatos necesarios para reproducibilidad.
- **Verificación de integridad vía SHA-256:** la huella SHA-256 del Parquet registrada en `.meta.json` permite confirmar que la copia de trabajo y el backup contienen **exactamente el mismo dataset** (si las dos huellas coinciden, los archivos son idénticos byte a byte).
- **Recomendación opcional (separada):** además, se PUEDE registrar una huella del propio archivo de metadatos para verificar también su integridad. Esto se documenta **solo como recomendación**, sin añadir infraestructura extra.
- **Alcance:** en esta etapa el backup es **ÚNICAMENTE una segunda copia de artefactos de investigación**; no introduce ningún sistema de persistencia nuevo.

### Persistencia del entorno y advertencia sobre entornos efímeros

Se documentan explícitamente dos escenarios distintos respecto a dónde "vive" el dataset y si sobrevive a reinicios.

**A. Entorno ACTUAL (con evidencia del proyecto):** el proceso de descarga corre actualmente **de forma LOCAL** (máquina del desarrollador, Windows, usando el entorno virtual `.venv`), **NO dentro del contenedor**. Evidencia verificada en el repositorio:

- El **Dockerfile** copia únicamente `src/` y `dashboard/` (`COPY src/ ./src/` y `COPY dashboard/ ./dashboard/`) — **NO copia `data/`** dentro de la imagen del contenedor.
- El **docker-compose.yml** **NO define ningún volumen** (no hay sección `volumes:`), por lo que no monta almacenamiento externo dentro del contenedor.
- `data/historical/` reside en el **sistema de archivos del workspace local**, en la ruta del proyecto.

Conclusión (basada en esa evidencia, no en suposición): bajo la ejecución local actual, `data/historical/` está en **disco local persistente**, y sus archivos **sobreviven** a: cerrar Kiro/IDE, detener TradeCore, reiniciar el entorno de desarrollo y reiniciar la máquina (persistencia de disco local).

**B. Infraestructura FUTURA efímera (advertencia):** SI este proceso se ejecuta más adelante dentro de una infraestructura con **sistema de archivos efímero** (por ejemplo, un contenedor AWS ECS/Fargate sin almacenamiento persistente adjunto), entonces `data/historical/` **DENTRO del contenedor NO debe tratarse como persistencia permanente**: al reiniciar o reemplazar el contenedor, esos archivos se perderían. En ese caso se necesitaría **diseñar más adelante** una solución de almacenamiento externo persistente. **NO se selecciona ni se implementa esa solución ahora**, y **NO se asume S3/EFS/PostgreSQL**. El punto es dejar explícito que **"ruta de archivo ≠ garantía de persistencia"**: la estrategia actual de Parquet + backup es para el **entorno actual de investigación/desarrollo**, no necesariamente para la futura arquitectura productiva.

---

## Protocolo de Calibración diagnóstica (Etapa 1) — marco MFE/MAE congelado antes de observar resultados

Esta sección documenta la **metodología del experimento de Calibración** (Tarea 1) y se **CONGELA antes de observar cualquier resultado de Calibración**, exactamente igual que los splits temporales se congelaron **antes** de descargar el dataset. Congelar el protocolo por adelantado evita el sesgo de elegir reglas *después* de ver qué números salen (una forma de sobreajuste — *overfitting* — a los propios datos de calibración).

Es **documentación de metodología**: NO autoriza ejecutar calibración, NO modifica código ni componentes protegidos, y NO cambia el dataset, los splits ni el `dataset_id`. Su objetivo es dejar por escrito CÓMO se hará el experimento y QUÉ decisiones quedan fijadas de antemano.

**Propósito real de esta etapa (importante):** la Calibración de la Etapa 1 **NO** busca definir una estrategia de trading ganadora ni una estrategia económica de salida completa. Su propósito es **CARACTERIZAR MATEMÁTICAMENTE** tres cosas sobre las señales que el motor SMA ya genera:

1. el **comportamiento posterior** de las señales SMA existentes (cómo se mueve el precio después de cada señal);
2. el **efecto de distintos stops basados en ATR** (Average True Range — medida de volatilidad reciente del precio) sobre el perfil de riesgo de esas señales;
3. la **capacidad discriminante del filtro de volumen** (VolumeFilter): si separa señales con mejor o peor comportamiento posterior.

**Fuera del alcance de esta Calibración (salvo pedido explícito de una etapa futura):** la Etapa 1 **NO** define ahora `Target_Puntos`, `Target_Dolares`, `Horizonte_min_dias`, `Horizonte_max_dias`, *take-profit* (objetivo de toma de ganancias), múltiplos del tipo `k × R`, un horizonte fijo `H` de velas, ni una estrategia económica de salida completa. Todo eso pertenece al **diseño conceptual mayor de TradeCore** y/o al futuro **Quant_Model** (modelo cuantitativo previsto para etapas posteriores). No se tratan como "vacíos que obligatoriamente deben resolverse antes de la Etapa 1": están **FUERA DEL ALCANCE de la Calibración actual**, salvo que una etapa futura los requiera explícitamente. En su lugar, esta etapa mide el comportamiento del precio con el marco diagnóstico **MFE/MAE** definido más abajo.

**Glosario breve de esta sección:**
- **Grilla / grid** (rejilla de búsqueda): conjunto discreto de valores candidatos que se prueban para un parámetro.
- **Parámetro** (parameter): un valor configurable del sistema que se quiere ajustar (aquí: `atr_multiplier` y `volume_threshold_factor`).
- **Métrica** (metric): número que resume una propiedad del comportamiento observado según un criterio definido de antemano.
- **MFE** (Maximum Favorable Excursion — máxima excursión favorable): el mayor movimiento **a favor** de la dirección de la señal dentro de su ventana de medición.
- **MAE** (Maximum Adverse Excursion — máxima excursión adversa): el mayor movimiento **en contra** de la dirección de la señal dentro de su ventana de medición. **OJO — desambiguación crítica:** este **MAE = Maximum Adverse Excursion** NO es el **MAE = Mean Absolute Error** (error absoluto medio) del Requisito 6 / RF-E1-04. Son métricas distintas que comparten la sigla. En este documento, la primera vez que aparezca cada una se escribe su **nombre completo** para evitar confusión.
- **reference_price** (precio de referencia de la señal): el precio que se usa como ancla matemática para medir las excursiones posteriores (ver definición precisa más abajo). NO es un precio de ejecución.
- **ATR_at_signal** (ATR en el instante de la señal): el valor de ATR **conocido** en el timestamp de la señal, usado para normalizar excursiones.
- **Cruce inverso** (inverse crossover): la siguiente señal de dirección opuesta que genera el motor SMA (una BUY tras una SELL, o viceversa).
- **Horizonte endógeno** (endogenous horizon): ventana de medición que termina cuando el propio motor SMA produce el cruce inverso, en lugar de un límite temporal arbitrario impuesto desde fuera.
- **Censura (`signal_path_censored` / `stop_path_censored`)** (censura de observación): situación en la que una observación no puede completarse dentro del split y queda "censurada". Se distinguen dos campos independientes: (1) `signal_path_censored=true` cuando la ventana del SIGNAL PATH de una señal no puede completarse porque su **cruce inverso** cae **más allá del límite del split** (la señal no participa en métricas que exigen SIGNAL PATH completo); (2) `stop_path_censored=true` cuando la observación de un stop ATR **específico** termina en el **límite del split** sin haberse observado ni el toque del stop ni el cruce inverso (en ese caso `stop_would_trigger=unknown`, porque no hay evidencia suficiente para afirmar `true` ni `false`).
- **counterfactual_diagnostic** (diagnóstico contrafactual): análisis de "qué habría pasado" con una señal que la configuración filtrada NO habría emitido; sirve para diagnóstico, no significa que TradeCore la hubiera emitido.
- **OOS** (out-of-sample): datos "fuera de muestra", nunca usados para ajustar; sirven para la prueba final imparcial.
- **Data leakage** (fuga de datos): usar información que no estaría disponible en el momento real de la decisión (por ejemplo, mirar el futuro), lo que infla artificialmente los resultados.
- **Append-only** (solo-añadir): archivo al que solo se agregan registros nuevos, nunca se sobrescriben ni borran los anteriores.

**Dos precisiones conceptuales (correcciones de terminología):**

- **Sobre `closes[-1]` y el sesgo de anticipación (look-ahead bias):** el *look-ahead bias* (sesgo de anticipación) es usar información que **no estaría disponible en el momento de la decisión**. Como `SignalEngine.evaluate()` se ejecuta **DESPUÉS** de que la vela cierra, ese cierre **ya es conocido** en ese instante → usarlo **NO es fuga temporal** (no es look-ahead). El riesgo **distinto** sería **suponer que una operación puede ejecutarse exactamente a ese precio**: eso sería una **hipótesis de ejecución optimista / slippage** (deslizamiento) **no modelado**, NO fuga temporal. En este protocolo ese precio **NO se usa como supuesto de ejecución**; se usa **solo** como `reference_price` (precio de referencia de la señal) para medir el movimiento posterior del mercado.
- **Sobre precedencia target/stop intrabar — FUERA DE ALCANCE actual (corrección):** la antigua ambigüedad "target y stop alcanzados dentro de la misma vela" se **RETIRA del alcance actual**. `Target_Puntos` y el take-profit (toma de ganancias) están **fuera del alcance** de la Etapa 1. Para la hipótesis H1 solo se necesita saber si el **rango** de una vela **contiene el nivel del stop** (BUY: `low <= stop_price`; SELL: `high >= stop_price`). **NO se necesita el orden intra-minuto**, porque actualmente **no existe un segundo evento terminal en competencia** (no hay target aprobado que compita con el stop). Por tanto **NO se adopta ahora ninguna convención de "stop primero"**. La precedencia intrabar vuelve a ser relevante **solo** en una **simulación FUTURA con múltiples condiciones de salida simultáneas** (por ejemplo, stop y take-profit compitiendo). Con OHLCV de 1 minuto seguimos sin conocer el recorrido intra-minuto (solo Open/High/Low/Close), por lo que **no** existe un "estándar de industria" que diga que "el stop se toca primero"; si alguna vez se adoptara para ese caso futuro, sería una **"convención conservadora adoptada por TradeCore"**, no un hecho observado. Esta nota **reemplaza (supersede)** la anterior nota de empate intrabar.

---

### 1. Espacio de búsqueda (grilla exacta)

Rangos aprobados (coherentes con los rangos de referencia ya documentados en ATRCalculator y VolumeFilter): `atr_multiplier` de **1.5x a 3.0x**; `volume_threshold_factor` de **0.5x a 0.8x**.

- **Grilla de ATR**, paso (step) = **0.25** → valores: `[1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0]` → **7 valores**.
- **Grilla de Volumen**, paso = **0.1** → valores: `[0.5, 0.6, 0.7, 0.8]` → **4 valores**.
- **Grilla conjunta (C, si fuera completa)** = 7 × 4 = **28 combinaciones**.

**Justificación metodológica de la granularidad (documentada):** los pasos se eligieron **a priori** (antes de observar resultados) para dar una densidad de exploración razonable sin sobreajustar la propia grilla. El paso 0.25 en ATR produce 7 puntos sobre un rango de 1.5 de ancho (resolución moderada para un multiplicador de volatilidad); el paso 0.1 en volumen produce 4 puntos sobre un rango de 0.3 de ancho. **El paso NO se seleccionó mirando resultados.**

---

### 2. Tres etapas de la Tarea 1 (A/B/C) — distinción exacta

- **A. Baseline interno** = TradeCore actual = `SignalEngine` SMA(9)/SMA(21). **NO tiene parámetros ATR/VolumeFilter que calibrar** (0 configuraciones de parámetros; es el punto de referencia interno contra el que se caracteriza el efecto incremental de B y C). = **1 configuración de referencia**.
- **B. TradeCore + ATR:** el **MISMO** `SignalEngine`, sin cambios. Solo se evalúan los **7 candidatos** de `atr_multiplier`. **NO** se introduce `volume_threshold_factor` en esta etapa. → **7 configuraciones**.
- **C. TradeCore + ATR + VolumeFilter:** mismo `SignalEngine` y mismo ATR. **DECISIÓN CONFIRMADA AQUÍ** (documentada como decisión tomada **antes** de ejecutar): la etapa C **evaluará la grilla factorial COMPLETA** = `atr_multiplier` `[1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00]` (**7 valores**) × `volume_threshold_factor` `[0.50, 0.60, 0.70, 0.80]` (**4 valores**) = **28 configuraciones**.
  - **Justificación:** evaluar la grilla factorial completa permite observar el efecto del filtro de volumen sobre **todo** el rango de ATR (no solo sobre un ATR pre-seleccionado), evitando fijar prematuramente el ATR antes de medir la interacción ATR × volumen. Es una decisión de cobertura exhaustiva del espacio de búsqueda de la sección 1. **NO** se reduce C usando primero los "ganadores" de B (no se pre-selecciona un ATR antes de medir la interacción con volumen).
  - En cada combinación de C se analiza el **perfil del subconjunto de señales aceptadas por volumen** y su relación con el stop ATR correspondiente.
  - **Número de configuraciones de C = 7 × 4 = 28** (grilla factorial completa).

**Totales conceptuales de la Tarea 1:**
- **A = 1** (baseline interno de referencia; 0 parámetros que calibrar, cuenta como 1 configuración de referencia).
- **B = 7** (los 7 candidatos de `atr_multiplier`).
- **C = 28** (grilla factorial completa 7 × 4).
- **Total conceptual = 1 + 7 + 28 = 36 configuraciones.**

---

### 3. Hipótesis diagnósticas de la Etapa 1 (H1 / H2 / H3)

Las tres etapas A/B/C de la grilla se corresponden con tres **hipótesis diagnósticas**. Ninguna se plantea en términos de rentabilidad, expectancy económica ni "estrategia ganadora" — se plantean como **caracterización del comportamiento del precio** medido con MFE/MAE (ver sección 4).

**H1 — ATR (etapa B):** *¿Cómo cambia el **perfil de riesgo** de las señales SMA(9)/SMA(21) al aplicar diferentes distancias de stop dinámico basadas en ATR?* No se enmarca como rentabilidad ni como estrategia ganadora. Para cada multiplicador `[1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00]` se analiza **cómo la distancia de stop se relaciona con el movimiento adverso posterior** de cada señal.

**H2 — VolumeFilter (etapa C, dimensión de volumen):** *¿Las señales que VolumeFilter **rechazaría** por bajo volumen muestran posteriormente un **perfil de precio peor** que las señales aceptadas?* Valores de `volume_threshold_factor`: `[0.50, 0.60, 0.70, 0.80]`. Es un análisis **retrospectivo/diagnóstico**. Una señal que VolumeFilter rechazaría puede analizarse para ver **qué habría pasado después**, pero debe marcarse como **`counterfactual_diagnostic`** — esto NO significa que TradeCore la hubiera emitido en la configuración filtrada.

**H3 — ATR + VolumeFilter (etapa C, combinación):** *¿La combinación de filtro de volumen y stop ATR produce un **subconjunto** de señales con un **perfil de riesgo/excursión consistentemente más favorable** que el conjunto completo de señales SMA?* **NO** se usan expresiones como "más rentable", "mejor estrategia" ni "mayor expectancy económica" (no hay `target`/take-profit aprobado). La comparación se hace **únicamente** con las métricas diagnósticas de este protocolo (sección 4).

---

### 4. Métricas primarias diagnósticas: MFE / MAE

La medición del comportamiento posterior de cada señal se hace con dos métricas primarias de excursión, más su versión normalizada por volatilidad.

**Definiciones:**
- **MFE (Maximum Favorable Excursion — máxima excursión favorable):** el mayor movimiento **a favor** de la dirección de la señal, dentro de su ventana de medición.
- **MAE (Maximum Adverse Excursion — máxima excursión adversa):** el mayor movimiento **en contra** de la dirección de la señal, dentro de su ventana de medición.
- **Desambiguación crítica (se repite por su importancia):** este **MAE = Maximum Adverse Excursion** NO es el **MAE = Mean Absolute Error** (error absoluto medio) del Requisito 6 / RF-E1-04. Son métricas diferentes que comparten la sigla; se usa siempre el nombre completo en la primera mención de cada una.

#### 4.1. `reference_price` (ancla de medición)

`reference_price` **=** `Signal.price`, que es el **cierre (close) que `SignalEngine` usó** al detectar el cruce. Se usa **ÚNICAMENTE** como **ancla matemática** para medir las excursiones posteriores.

- **NO** se afirma que `reference_price` sea un precio real ejecutable.
- **NO** se usa para simular *fills* (ejecuciones), *slippage* (deslizamiento) ni P&L (*profit and loss* — ganancia/pérdida) real.
- El **precio de ejecución está fuera del alcance de esta etapa**.

#### 4.2. Inicio de la ventana de medición (t+1)

La **vela que genera la señal NO aporta** su `high`/`low` al cálculo de MFE/MAE. Motivo: el cruce solo se **confirma** cuando se conoce el **cierre** de esa vela. Por lo tanto:

- señal generada en la vela `t`;
- `reference_price = close[t]`;
- **primera vela usada para MFE/MAE = `t+1`**.

No se usan movimientos **anteriores** al momento lógico de generación de la señal.

#### 4.3. Fin de la ventana de medición: el siguiente cruce inverso (horizonte endógeno)

La ventana **termina** cuando `SignalEngine` genera la **siguiente señal de dirección opuesta**: una ventana BUY termina cuando aparece la siguiente SELL; una ventana SELL termina cuando aparece la siguiente BUY.

- **NO** se introducen `H` velas, días, *take-profit* ni ningún límite temporal arbitrario.
- El propio comportamiento del cruce SMA aporta un **horizonte endógeno** (definido por el sistema, no impuesto desde fuera).
- **Aclaración:** esta ventana se usa **SOLO como ventana de medición diagnóstica**, NO como una nueva regla de ejecución aprobada del producto.

#### 4.4. Inclusión de la vela del cruce inverso

Convención temporal: la posición conceptual permanece **bajo observación** hasta que el cruce opuesto se **confirma en el cierre de su vela**. Por lo tanto:

- la vela inicial de la señal queda **EXCLUIDA** de MFE/MAE;
- las velas siguientes quedan **INCLUIDAS**;
- la vela que **genera el cruce opuesto SÍ puede formar parte** de la ventana (porque durante su desarrollo el cruce opuesto **aún no se conocía**);
- la ventana **termina en el cierre** de esa vela.

Esta convención de medición **no** es una regla de ejecución futura sin aprobación adicional.

#### 4.5. Fórmulas diagnósticas (en puntos)

Con `high_future`/`low_future` **restringidos a la ventana** definida arriba (`t+1` hasta el cierre de la vela del cruce inverso, inclusive):

La excursión se define **siempre** con `max(0, ...)`: la excursión mide *cuánto se movió el precio en una dirección*, y un movimiento en esa dirección nunca puede ser negativo. Un delta crudo negativo significa que **NO hubo excursión** en esa dirección, y entonces la excursión vale exactamente `0`.

- **BUY:**
  - `MFE_points = max(0, max(high_future) - reference_price)`
  - `MAE_points = max(0, reference_price - min(low_future))`
- **SELL:**
  - `MFE_points = max(0, reference_price - min(low_future))`
  - `MAE_points = max(0, max(high_future) - reference_price)`

**Definición (no es una restricción a reportar):** el `max(0, ...)` es **parte de la definición matemática** de la excursión. Un delta crudo negativo significa que **NO hubo excursión** en esa dirección, y por lo tanto `excursion_points = 0`. Esto **NO** es una anomalía, ni una inconsistencia metodológica, ni un problema de datos, ni un motivo de descarte: es un valor perfectamente válido (ausencia de movimiento favorable/adverso respecto de `reference_price`).

##### CHANGELOG §4.5 — corrección de la definición de excursión

- **Antes:** las fórmulas podían arrojar un valor **negativo**, que se interpretaba como **inconsistencia metodológica o de datos** a reportar (y no se corregía en silencio).
- **Ahora:** la excursión se define como `max(0, raw_excursion)`, de modo que un delta crudo negativo se convierte en `excursion_points = 0` y **no** constituye por sí solo una anomalía. Ejemplo: una señal BUY muy favorable cuyo precio nunca se mueve en contra de `reference_price` produce un delta adverso crudo negativo; su **MAE correcto es cero**.
- **Razón:** la interpretación anterior confundía un comportamiento perfectamente válido (la **ausencia** de movimiento favorable/adverso respecto de `reference_price`) con una inconsistencia. Medir "cuánto se movió en una dirección" y no encontrar movimiento en esa dirección da cero, no un error.
- **Nota anti-leakage:** Esta corrección fue detectada mediante auditoría estática de diseño e implementación antes de observar datos o resultados reales de Calibration. No fue informada por performance, señales, métricas ni distribuciones del dataset real.

#### 4.6. Normalización por ATR

Además, se registran las versiones normalizadas por volatilidad cuando `ATR_at_signal > 0`:

- `MFE_ATR = MFE_points / ATR_at_signal`
- `MAE_ATR = MAE_points / ATR_at_signal`

Esto permite **comparar señales de contextos de volatilidad distintos** en una escala común. **NO** se recalcula el ATR de forma retrospectiva con información futura: `ATR_at_signal` es **solo** el ATR conocido en el timestamp de la señal.

---

### 5. Evaluación diagnóstica de los stops ATR

Para cada multiplicador de la grilla de ATR:

- `stop_points = ATR_at_signal × atr_multiplier`, considerado una **distancia FIJA en el instante de la señal** para este análisis.
- **NO** se implementa *trailing stop* (stop dinámico que sigue al precio), ni actualización posterior del stop, ni recálculo vela a vela (no aprobado).
- **BUY:** el stop se **alcanzaría** si el precio posterior toca `reference_price - stop_points`.
- **SELL:** el stop se alcanzaría si el precio posterior toca `reference_price + stop_points`.

Con MFE/MAE es posible **estudiar** (sin ejecutar estos cálculos ahora):
- proporción de señales cuyo `MAE_points` **supera** cada `stop_points`;
- proporción de señales que **permanecen dentro** del stop durante toda la ventana;
- distribución de `MAE_ATR`;
- distribución de `MFE_ATR`.

#### 5.1. Alcanzar el stop NO termina la ventana diagnóstica

Aunque retrospectivamente una señal **hubiera** tocado su stop ATR, la ventana MFE/MAE **PUEDE continuar** hasta el cruce inverso, **solo para fines diagnósticos**. Se distingue:

- **`stop_would_trigger`**: el umbral del stop fue alcanzado (booleano diagnóstico);
- **ventana completa MFE/MAE**: el comportamiento total del precio hasta el siguiente cruce inverso.

Esto permite analizar, por ejemplo, si un stop fue **demasiado ajustado** y el precio luego se movió a favor. **NO** se interpreta esta continuidad como una operación real que sigue abierta tras haber tocado el stop.

#### 5.2. SIGNAL PATH vs. STOP PATH — dos objetos analíticos DISTINTOS

Esta subsección distingue dos objetos de análisis que NO deben confundirse. Todos los campos aquí son **conceptuales**: **NO se implementan ahora**; primero se documenta la semántica.

**SIGNAL PATH (trayectoria de la señal):** describe el **comportamiento completo posterior** a una señal SMA.

- **Ventana:** señal en `t` → inicio en `t+1` → ... → **cruce SMA inverso** (fin endógeno, igual que la sección 4.3).
- **Campos conceptuales:** `signal_mfe_points`, `signal_mae_points`, `signal_mfe_atr`, `signal_mae_atr`.
- **Propiedades del SIGNAL PATH:**
  - describe el comportamiento **post-señal** del precio;
  - **CONTINÚA** aunque en el camino algún stop ATR hipotético se hubiera tocado (no se detiene por un stop);
  - **NO** representa una operación real;
  - **NO** debe usarse para afirmar que una operación detenida por stop "habría capturado" movimientos posteriores.
- Las fórmulas **MFE/MAE ya aprobadas** (BUY/SELL de la sección 4.5) **no cambian**.

**STOP PATH (trayectoria del stop):** representa la **observación de un stop ATR ESPECÍFICO** desde `t+1` hasta el **PRIMERO** de estos eventos observables:

1. el stop es **alcanzado** (tocado);
2. ocurre el **cruce SMA inverso**;
3. se alcanza el **límite del split de Calibración** (`2025-07-01T00:00:00Z`).

- Por eso el STOP PATH **existe analíticamente aunque el stop nunca se alcance** (puede terminar por cruce inverso o por límite).
- Para **cada** par (señal × `atr_multiplier`) se registra conceptualmente: `stop_points`, `stop_price`, `stop_would_trigger`, `first_stop_touch_bar_timestamp`, `stop_path_censored`.
- **NO** se implementan estos campos ahora.

#### 5.3. Semántica TERNARIA de `stop_would_trigger` (NO es booleano puro)

`stop_would_trigger` tiene **tres estados semánticos**, no dos:

- **`true`**: se **observó** que el stop fue tocado **antes** de que terminara la ventana observable.
- **`false`**: el **cruce inverso** ocurrió **antes** del límite de Calibración **Y** durante **toda** la ventana **no** se observó ningún toque de stop → evidencia **suficiente** de que ese stop **NO** se habría activado durante el SIGNAL PATH completo.
- **`unknown`**: se alcanzó el **límite de Calibración** `2025-07-01T00:00:00Z` **SIN** observar ni un toque de stop ni un cruce inverso → **NO** hay evidencia suficiente para afirmar `false`; la observación queda **censurada**.

Si más adelante se implementa en Python, **NO** se decide todavía la representación técnica (`Optional[bool]`, enum, etc.): primero se documenta la semántica.

#### 5.4. Lógica EXACTA de censura del STOP PATH

```
¿stop tocado antes del límite? → SÍ → stop_would_trigger=true, stop_path_censored=false
                               → NO → ¿crossover inverso antes del límite? → SÍ → stop_would_trigger=false, stop_path_censored=false
                                                                            → NO → stop_would_trigger=unknown, stop_path_censored=true
```

Regla: **NUNCA** se convierte automáticamente una observación censurada en `stop_would_trigger=false`.

#### 5.5. Regla de detección de stop BUY/SELL

- `stop_points = ATR_at_signal × atr_multiplier` — **fijo** en el timestamp de la señal (no *trailing*, no recomputar más tarde).
- **BUY:** `stop_price = reference_price - stop_points`; el stop se **observa tocado** si alguna vela posterior del STOP PATH tiene `low <= stop_price`.
- **SELL:** `stop_price = reference_price + stop_points`; el stop se observa tocado si alguna vela posterior tiene `high >= stop_price`.
- La búsqueda empieza en `t+1`; la **vela de la señal `t` NO participa**.

#### 5.6. `first_stop_touch_bar_timestamp`

Se usa **exactamente** este nombre (**NO** `first_stop_touch_timestamp`). Razón: con OHLCV de 1 minuto podemos identificar la **primera BARRA** cuyo rango contiene el nivel del stop, pero **no** el instante intra-minuto exacto.

- si `stop_would_trigger=true` → se registra el **timestamp de la primera barra** que cumple la condición;
- si `stop_would_trigger=false` → `first_stop_touch_bar_timestamp = null`;
- si `stop_would_trigger=unknown` → `null`.

**IMPORTANTE:** **NO** se documenta este timestamp como "inicio exacto de la barra" ni "cierre exacto de la barra" salvo que la semántica de timestamp del dataset/schema esté **verificada localmente**. Se describe simplemente como **"timestamp asociado por el dataset a la barra OHLCV de 1 minuto donde se detecta el primer toque"**. **NO** se hace una consulta facturable para resolver esta nomenclatura.

#### 5.7. Campos de censura INDEPENDIENTES

`signal_path_censored` y `stop_path_censored` son conceptualmente **DISTINTOS**:

- `signal_path_censored=false` si el **cruce inverso ocurre antes del límite** → MFE/MAE completo **utilizable**.
- `signal_path_censored=true` si el **cruce inverso NO ocurre antes del límite** → la señal **no participa** en métricas que exijan el SIGNAL PATH completo; **NO** se usa Validación para completarlo.

#### 5.8. Caso de censura PARCIAL (importante)

Una misma señal puede tener `signal_path_censored=true` pero `stop_path_censored=false`. Ejemplo:

1. señal generada **dentro** de Calibración;
2. stop ATR **tocado antes** de `2025-07-01T00:00:00Z`;
3. cruce inverso **NO** ocurre antes del límite.

Resultado: `stop_would_trigger=true`, `stop_path_censored=false`, `signal_path_censored=true`. La observación **sigue siendo válida** para ciertas métricas H1 relacionadas con el stop. **NO** se descarta toda la señal solo porque el SIGNAL PATH esté censurado.

#### 5.9. Intrabar target-vs-stop — FUERA DE ALCANCE

Ver también la corrección en "Dos precisiones conceptuales". Se **RETIRA** la ambigüedad "target y stop alcanzados dentro de la misma vela": `Target_Puntos` y el take-profit están **fuera del alcance** actual. Para H1 solo necesitamos si el **rango** de una vela **contiene el nivel del stop** (BUY `low <= stop_price`; SELL `high >= stop_price`). **NO** necesitamos el orden intra-minuto porque actualmente **no hay un segundo evento terminal en competencia**. La precedencia intrabar vuelve a ser relevante **solo** para una **simulación FUTURA con múltiples condiciones de salida simultáneas**. **NO** se adopta ninguna convención "stop primero" ahora.

#### 5.10. Advertencia OBLIGATORIA de sesgo mecánico de stops anchos

Un `atr_multiplier` mayor produce un stop **más lejano** de `reference_price`. En igualdad de condiciones, un stop más lejano **mecánicamente tenderá a alcanzarse MENOS veces**. Por lo tanto:

- **"menor `stop_trigger_rate` ≠ automáticamente mejor configuración"**;
- **"mayor `stop_survival_rate` ≠ automáticamente mejor configuración"**.

La futura métrica primaria de H1 debe evaluar el **trade-off** entre distancia/riesgo asumido y protección frente al movimiento adverso, **sin** seleccionar trivialmente el multiplicador más alto. **NO** se define esa función objetivo ahora.

---

### 6. H2 — grupos Aceptado vs. Rechazado

Para cada `volume_threshold_factor` se definen dos grupos:

- **Aceptado (Accepted):** señales que VolumeFilter **permitiría**.
- **Rechazado (Rejected):** señales que VolumeFilter **descartaría** (marcadas `counterfactual_diagnostic`).

Para **ambos** grupos podrán calcularse más adelante las mismas métricas diagnósticas: `MFE_points`, `MAE_points`, `MFE_ATR`, `MAE_ATR`, y proporción de `stop_would_trigger`.

**H2 se sostiene SOLO si** las señales rechazadas muestran de forma **consistente** un perfil **menos favorable** bajo las métricas definidas arriba. **NO** se define todavía un único criterio estadístico definitivo de "mejor"; primero se documenta **qué métricas son comparables**; la **métrica de selección final debe congelarse antes** de ejecutar la Calibración (ver sección 9).

---

### 7. Qué SÍ se puede medir ahora / qué NO

**SÍ se puede medir (por hipótesis):**
- **H1** → MFE (Maximum Favorable Excursion), MAE (Maximum Adverse Excursion), `MFE/ATR`, `MAE/ATR`, % `stop_would_trigger`, % de supervivencia al stop, distribuciones de excursión.
- **H2** → Aceptado vs. Rechazado mediante distribuciones de MFE/MAE, excursiones normalizadas por ATR, frecuencia de `stop_would_trigger`.
- **H3** → perfiles conjuntos del total de señales SMA vs. subconjunto aceptado por volumen vs. comportamiento a través de los distintos stops ATR.

**NO se puede afirmar todavía** (NO se usa durante la Calibración): beneficio económico, P&L real, expectancy económica, *profit factor* (factor de beneficio), retorno acumulado, *win rate* basado en take-profit, R/R (riesgo/beneficio) realizado, *slippage* (deslizamiento), comisiones, calidad real de ejecución. Responder a eso exige una **etapa futura distinta** con **reglas de ejecución/salida explícitamente aprobadas**.

---

### 8. Metodología de comparación A/B/C

Todas las etapas comparten, sin excepción:
- el **mismo dataset** (el Parquet maestro aprobado, `dataset_id` `5be681c5f1ac4449d6dd347c7494157d2a0573f4f4d62fea5a5aee2a41eb9056`);
- la **misma ventana del split de Calibración** `[2023-09-13T00:00:00Z, 2025-07-01T00:00:00Z)`;
- las **mismas reglas de entrada** (el motor SMA sin cambios);
- las **mismas definiciones de métrica diagnóstica** (MFE/MAE y sus normalizaciones);
- la **misma metodología** a través de A/B/C.

**Distinción explícita:** el **"Baseline interno de TradeCore"** (A, el motor SMA) **NO es** el **"baseline externo TradingView/Pine"** (que permanece **BLOQUEADO** hasta que se disponga de la información del script original). **No se mezclan.** La comparación A/B/C es puramente interna a TradeCore.

#### 8.1. Censura del SIGNAL PATH en la frontera de Calibración

La Calibración termina en `2025-07-01T00:00:00Z`. Si una señal aparece **dentro** de Calibración pero su **cruce inverso ocurre DESPUÉS** de esa frontera:

- **NO** se usa Validación para completar su ventana (Validación permanece **sin consultar**).
- Se marca **`signal_path_censored = true`** (la ventana del SIGNAL PATH no puede completarse dentro del split).
- **NO** participa en métricas que exijan una ventana completa, pero **puede quedar registrada** para trazabilidad.

Esta regla es **obligatoria** para evitar contaminación entre splits (*cross-split contamination*).

#### 8.2. Inicio de Calibración sin warm-up previo

La Calibración empieza **exactamente** en el inicio del dataset `2023-09-13T00:00:00Z`, por lo que **NO hay velas anteriores** para llenar el warm-up de 22 velas (ver `warmup_min` en la sección del dataset). **NO se inventan datos ni se extiende hacia atrás.**

Se usa el **comportamiento natural ya existente**:
- `SignalEngine` devuelve `None` hasta tener suficiente historia;
- el ATR permanece no disponible hasta reunir sus velas requeridas (`insufficient_history_atr`);
- cualquier componente dependiente de historia sigue su comportamiento documentado.

Las **primeras velas** cronológicamente pertenecen a Calibración, pero **NO generan resultados diagnósticos artificiales** antes de que el sistema tenga historia suficiente. El **primer timestamp efectivamente medible se REGISTRARÁ cuando Calibración se ejecute**, pero **NO se calcula ahora** mediante una corrida experimental.

---

### 9. Métrica de selección final — RESUELTA y CONGELADA en la sección 9-bis

> **Estado actualizado:** esta sección enumeraba los puntos que quedaban **PENDIENTES** de fijar antes de la Calibración. **Todos están ahora RESUELTOS y CONGELADOS** en la **sección 9-bis ("Protocolo FINAL de selección de configuraciones")**, más abajo. Ya **no queda ningún punto abierto**. Esta sección 9 se conserva como **índice** de qué se resolvió y del **estado final (FROZEN)** de cada punto.

El marco MFE/MAE resuelve la **observación del comportamiento del precio**, pero **NO autoriza elegir arbitrariamente una función objetivo**. Antes de ejecutar la Calibración, lo siguiente debía **congelarse ANTES de observar resultados**. Estado final de cada punto:

- métrica primaria de **H1** → **CONGELADA**: `h1_marginal_productive_rescue` ("Rescate Productivo Marginal") es la primaria; `h1_lexico` quedó **descartada**; `h1_rendimientos_decrecientes` y `h1_holgura_slack` quedan **solo como diagnósticos SECUNDARIOS** (items 9-bis.8 y 9-bis.8-ALT);
- métrica primaria de **H2** → **CONGELADA** (`h2_delta`, delta de Cliff), operacionalizada con el gate numérico exacto `±0.147` para H3-A (items 9-bis.10 y 9-bis.20);
- forma de **sintetizar H3** → **CONGELADA** como estructura jerárquica H3-A (gate de VolumeFilter) → H3-B (elección de ATR por H1 recomputada sobre la población Aceptada) (item 9-bis.20);
- métricas secundarias → **CONGELADAS** (items 9-bis.9 y 9-bis.11);
- tratamiento de las señales **censuradas** (`signal_path_censored` / `stop_path_censored`) → **CONGELADO** (items 9-bis.4 y 9-bis.5);
- tamaño mínimo de muestra → **CONGELADO** con pisos numéricos `N_step_observable_min = 50` y `N_rescued_observable_min = 30` (items 9-bis.5 y 9-bis.13);
- **elegibilidad por censura** → regla **numérica determinista SIN veto humano** (`step_censoring_rate_max = 0.30`, items 9-bis.5 y 9-bis.13);
- **intervalos de confianza** → **CONGELADOS**: IC bootstrap 95% bilateral, `bootstrap_resamples = 2000`, semilla fija reproducible, **remuestreo por BLOQUES semanales ISO** (block bootstrap temporal, no por `signal_id` individual), con piso `N_bootstrap_blocks_min = 20` (item 9-bis.13-CI revisado);
- **regla secuencial de avance de ATR** → **CONGELADA** (item 9-bis.13-SEQ);
- regla de **graduación a Validación** → **CONGELADA** (item 9-bis.14);
- **Universo de Validación A/B/C** → **CONGELADO** (item 9-bis.14-UNIV);
- **Top-N** (cuántas configuraciones gradúan) → **CONGELADO** en `Top-N_C = 3` (item 9-bis.15);
- **ranking de candidatas C** y criterios de **desempate** (tie-breaks) → **CONGELADOS**, lexicográficos y sin puntaje ponderado (item 9-bis.16).

Esta lista funciona como su tabla de contenidos y refleja qué quedó congelado. Ya no hay puntos abiertos.

**Principio firme (se mantiene):** la selección usa **ÚNICAMENTE** información de Calibración; **la Validación NO puede usarse para decidir quién pasa a Validación**.

---

### 9-bis. Protocolo FINAL de selección de configuraciones (congelado antes de Calibración)

Esta sección **RESUELVE y CONGELA** todos los puntos que la sección 9 dejaba pendientes. Se escribe **antes** de ejecutar la Calibración y **antes** de observar cualquier resultado. Es **documentación de metodología**: NO autoriza ejecutar nada, NO toca código ni componentes protegidos, NO cambia el dataset, los splits ni el `dataset_id`. Su valor es dejar por escrito **cómo se elegirán las configuraciones**, de modo que la elección no se pueda "acomodar" después de ver los números (evita el *data-snooping* — hurgar en los datos hasta encontrar la regla que conviene).

**Formato obligatorio de cada métrica.** Toda métrica se describe con estos siete campos, en este orden:
**nombre → fórmula → población → censura → dirección de optimización → interpretación → limitaciones.**

**Glosario breve de esta sección:**
- **Unidad experimental** (experimental unit): el objeto individual que cuenta como "una observación" en el análisis. Aquí es `signal_id`.
- **`signal_id`** (identificador de señal): identificador único de **una** señal SMA concreta (mismo mercado, timestamp, dirección, `reference_price`, `ATR_at_signal`).
- **Comparación pareada / repeated-measures** (paired / medidas repetidas): comparar valores que provienen del **mismo sujeto** bajo condiciones distintas, en vez de sujetos distintos. Elimina la variación entre sujetos.
- **Bootstrap** (remuestreo): técnica que estima la incertidumbre de un estadístico remuestreando con reemplazo las **unidades** muchas veces y observando cómo varía el resultado. En esta sección la **unidad de remuestreo es el BLOQUE semanal ISO** (block bootstrap temporal), no el `signal_id` individual (item 9-bis.13-CI revisado).
- **Block bootstrap** (bootstrap por bloques): variante que remuestrea **bloques contiguos** de observaciones en vez de observaciones sueltas, para preservar la **dependencia temporal** de corto plazo. Aquí el bloque es una **semana ISO** completa.
- **IC / CI** (Intervalo de Confianza / Confidence Interval): rango que expresa la incertidumbre de una estimación. Un IC estrecho = estimación estable; uno ancho = evidencia débil.
- **Tamaño de efecto** (effect size): magnitud de una diferencia (cuán grande es), distinto del **valor-p** (que solo indica si una diferencia es "estadísticamente detectable", no cuán grande es).
- **Probabilidad de superioridad / delta de Cliff** (probability of superiority / Cliff's delta): tamaño de efecto basado en rangos que mide con qué frecuencia un valor de un grupo supera a uno del otro grupo; robusto ante colas y valores atípicos.
- **Dominancia / frontera de Pareto** (dominance / Pareto frontier): una opción "domina" a otra si es al menos igual en todos los criterios y mejor en al menos uno; la frontera de Pareto es el conjunto de opciones no dominadas.
- **Selección lexicográfica** (lexicographic): ordenar por un primer criterio; solo si hay empate se pasa al segundo, y así sucesivamente (como ordenar palabras en un diccionario).
- **Gate** (compuerta / condición mínima): condición de aprobado/reprobado que una configuración debe cumplir para ser siquiera considerada.
- **Pre-registrado** (pre-registered): decidido y escrito por adelantado, antes de ver datos, de forma que no pueda cambiarse a conveniencia después.

---

#### 9-bis.1. Unidad experimental FINAL: `signal_id`

La **unidad experimental es `signal_id`** — una señal SMA individual. Esto tiene una consecuencia matemática central:

- Los **7 STOP PATHs** de una misma señal (con `atr_multiplier` ∈ `{1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00}`) **NO son 7 observaciones independientes**. Son **7 escenarios contrafactuales sobre la MISMA señal**: comparten mercado, `timestamp`, dirección (BUY/SELL), `reference_price` y `ATR_at_signal`. Lo único que cambia entre ellos es la **distancia del stop** (`m × ATR_at_signal`).
- Por tanto, **cualquier comparación de H1 entre multiplicadores ATR DEBE preservar esta dependencia** (tratarla como medidas repetidas sobre la misma señal), **NO** como si fueran muestras independientes.
- Lo mismo aplica a **H3** cuando una misma señal aparece bajo varias configuraciones ATR × Volumen: siguen siendo el **mismo `signal_id`** bajo condiciones distintas, no observaciones nuevas.

Tratar estos escenarios como independientes **inflaría artificialmente el tamaño de muestra** y subestimaría la incertidumbre. Se prohíbe explícitamente.

---

#### 9-bis.2. H1 — metodología PAREADA por `signal_id`

Las comparaciones de H1 entre multiplicadores ATR se hacen con **diferencias pareadas por `signal_id`** (medidas repetidas), **NO** con pruebas de muestras independientes.

**Por qué:** cada señal aporta **un valor por multiplicador**. Al comparar dos multiplicadores **dentro de la misma señal**, la enorme variación **entre señales** (unas ocurren en mercados tranquilos, otras en volátiles) **se cancela**, porque ambas condiciones se miden sobre el mismo mercado, mismo instante y mismo `ATR_at_signal`. Queda solo el efecto atribuible a **cambiar la distancia del stop**, que es justo lo que H1 quiere aislar.

**Construcción propuesta:**
1. Para cada señal `s` y cada par de multiplicadores `(m_i, m_j)`, se calcula la **diferencia pareada** de la métrica de interés: `d_s = métrica(s, m_i) − métrica(s, m_j)`.
2. Se resume la distribución de esas diferencias con **estadísticos robustos**: **mediana de las diferencias pareadas** (robusta ante colas), y sus cuartiles.
3. La incertidumbre se estima con **bootstrap por BLOQUES semanales ISO** (item 9-bis.13-CI revisado): se remuestrean **semanas ISO completas** con reemplazo, conservando juntas todas las señales de cada semana y su pareo interno, no señales sueltas ni observaciones sueltas. Produce un **IC** para la mediana de las diferencias.

**Justificación de idoneidad:** la mediana de diferencias pareadas + IC por bootstrap de bloques semanales es apropiada porque (a) respeta la dependencia within-signal (item 9-bis.1), (b) preserva mejor la dependencia temporal de corto plazo entre señales cercanas que el remuestreo individual (item 9-bis.13-CI revisado), (c) es robusta a la asimetría y a los valores atípicos típicos de las excursiones de precio, y (d) no asume normalidad (las distribuciones de MFE/MAE son marcadamente sesgadas).

---

#### 9-bis.3. H2 — dependencia entre umbrales de volumen

Los umbrales de volumen `{0.50, 0.60, 0.70, 0.80}` se aplican **sobre la MISMA población base de señales SMA**. Consecuencias que deben documentarse:

- Los conjuntos **Aceptado/Rechazado** de umbrales distintos están **relacionados** y pueden estar **ANIDADOS**: el conjunto Rechazado de un umbral **más estricto** contiene al conjunto Rechazado de un umbral **más laxo** (si una señal se rechaza con umbral 0.50, también se rechaza con 0.60, 0.70 y 0.80; subir el umbral solo puede rechazar más, nunca menos).
- Por tanto, **los 4 umbrales NO son 4 experimentos independientes**. Un **mismo `signal_id`** puede **cambiar de clase** (Aceptado ↔ Rechazado) a medida que cambia el umbral.
- La **unidad base sigue siendo `signal_id`**. Las comparaciones entre umbrales deben leerse como análisis **correlacionados** sobre la misma población, no como estudios separados.

Esto se documenta explícitamente para que ninguna lectura posterior trate los 4 umbrales como evidencia independiente acumulable.

---

#### 9-bis.4. Regla de censura y denominadores (obligatoria para toda tasa)

Para **cualquier tasa** que se reporte, se publican **siempre** estos cuatro números juntos:

- **`N_total`**: todas las observaciones consideradas.
- **`N_observable`**: observaciones con estado **conocido** (`true` o `false`).
- **`N_censored`**: observaciones **censuradas** (estado `unknown`).
- **`censoring_rate`** = `N_censored / N_total`.

**La tasa principal usa SOLO `N_observable` en el denominador.** Ejemplo canónico:

```
stop_trigger_rate = N_stop_true / N_stop_observable
donde  N_stop_observable = N_stop_true + N_stop_false
```

Reglas duras:
- El estado **`unknown` queda FUERA del denominador** de la tasa, pero **SE REPORTA** como censura (`N_censored` y `censoring_rate`).
- **NUNCA** se convierte `unknown → false`.
- **NUNCA** se descartan observaciones censuradas **sin contarlas**.
- **NUNCA** se oculta la proporción de censura.

---

#### 9-bis.5. Criterio de "evidencia insuficiente por censura" (regla NUMÉRICA determinista, sin veto humano)

Cómo se reporta la censura y cuándo se declara que una configuración tiene **evidencia insuficiente**. **Se retira por completo cualquier concepto de "veto humano" o revisión discrecional post-hoc.** La elegibilidad depende **EXCLUSIVAMENTE de reglas reproducibles fijadas antes de la Calibración** (umbrales numéricos), no del juicio de una persona.

- Junto a **cada** resultado se publica su `censoring_rate` (item 9-bis.4).
- **Fórmula determinista de elegibilidad (congelada antes de la Calibración):**

```
eligible = (N_observable >= N_observable_min) AND (censoring_rate <= censoring_rate_max)
```

  Una configuración/grupo que **falla cualquiera** de las dos condiciones queda **excluida del ranking/graduación** por regla, sin intervención humana. No se veta; se excluye por no cumplir un umbral escrito de antemano.

- **Valores propuestos (para aprobación del usuario; convenciones metodológicas ex-ante, honestamente declaradas):** no existe una derivación estadística única que fije estos números; son **convenciones transparentes ex-ante**, no valores derivados de los datos, y quedan **congelados antes de la Calibración**.

  - **`N_observable_min = 50`.**
    - **Qué problema controla:** evita rankear configuraciones cuya métrica primaria descansa sobre demasiado pocas señales, donde el IC por bootstrap sería inestable.
    - **Por qué es razonable en esta etapa:** da margen para un IC por bootstrap **moderadamente estable** sobre estadísticos basados en rangos/medianas agregados por `signal_id`; es **más conservador que el ≥30 habitual**, justificado por la asimetría de las excursiones y por el pareo (medidas repetidas reducen la muestra efectiva de pares).
    - **Riesgo residual si fuera menor:** IC inestables, rankings frágiles que cambian con pocas señales.
    - **Riesgo residual si fuera mayor:** demasiadas configuraciones excluidas → baja cobertura de la grilla, se podría perder la región de interés.
    - **Interacción con bootstrap/IC:** este piso acompaña (no reemplaza) al criterio de **ancho de IC** del item 9-bis.13; ambos deben cumplirse.
    - **Qué pasa exactamente si falla:** `eligible = false` → la configuración **no entra al ranking** ni gradúa; se reporta igual con sus cuatro números de censura.

  - **`censoring_rate_max = 0.30`.**
    - **Qué problema controla:** evita que una configuración pase con un subconjunto observable **no representativo**.
    - **Por qué es razonable en esta etapa:** por encima de ~30% censurado el subconjunto observable puede volverse **no representativo y asimétrico entre anchos de stop** (riesgo residual 9-bis.18(a): los stops anchos censuran más).
    - **Riesgo residual si fuera menor:** se excluyen configuraciones útiles con censura moderada pero manejable.
    - **Riesgo residual si fuera mayor:** configuraciones de stop ancho pasan sobre evidencia **fina y sesgada**.
    - **Interacción con bootstrap/IC:** censura alta encoge y sesga la muestra observable, ensanchando y distorsionando el IC; el techo actúa como salvaguarda antes de confiar en ese IC.
    - **Qué pasa exactamente si falla:** `eligible = false` → excluida del ranking/graduación; se reporta con su `censoring_rate` visible.

- **Aplicabilidad por caso:** la fórmula se aplica a **H1**, a **H2 Aceptado**, a **H2 Rechazado** y a **H3** usando el `N_observable` propio de cada población (item 9-bis.13). Salvo que el usuario apruebe pisos distintos por caso, se usa el **mismo par** (`N_observable_min = 50`, `censoring_rate_max = 0.30`) para todos, evitando complejidad innecesaria.
- **Naturaleza de estos valores (declaración honesta):** son **convenciones ex-ante transparentes**, **no** derivadas de los datos; se **congelan antes de la Calibración**. Una configuración que falla cualquiera de las dos condiciones se **excluye del ranking/graduación por regla determinista** — **no** es "vetada" por un humano.
- **La regla de reporte de censura se mantiene sin cambios:** siempre se publican `N_total`, `N_observable`, `N_censored`, `censoring_rate`; **nunca** se convierte `unknown → false` (item 9-bis.4).

---

#### 9-bis.6. Objetivo de H1 (qué NO puede ser)

La métrica primaria de H1 **NO puede reducirse a "stop más ancho → menos stops tocados → mejor"**. En concreto:

- **NI** un `stop_trigger_rate` más bajo por sí solo,
- **NI** un `stop_survival_rate` más alto por sí solo,

pueden ser la métrica primaria. Ambos se minimizan/maximizan **trivialmente** eligiendo el stop más ancho (ver la advertencia de sesgo mecánico de la sección 5.10).

La métrica **debe capturar el trade-off** entre **distancia de stop / riesgo asumido** y **protección frente al movimiento adverso**. Además, se mantiene la separación entre **SIGNAL PATH** (MFE/MAE de la señal SMA) y **STOP PATH** (comportamiento del stop ATR): **no** se fusionan como si fueran una operación económica real (no hay ejecución, ni P&L, ni take-profit aprobado).

---

#### 9-bis.7. Evitar puntajes compuestos arbitrarios

**NO** se usa por defecto un puntaje del tipo `score = w1·A + w2·B` con **pesos arbitrarios**. Se prefiere selección **jerárquica / lexicográfica / por dominancia (frontera de Pareto)**, que es **más interpretable** (se puede explicar en palabras por qué una configuración gana).

Si alguna vez se usara un compuesto, **cada término y cada peso** debe: (a) estar **justificado**, (b) estar **normalizado en escala** para que ninguna variable **domine mecánicamente** por tener unidades más grandes, (c) tener una **interpretación** explícita, y (d) estar **congelado antes** de la Calibración.

---

#### 9-bis.8. H1 — MÉTRICA PRIMARIA CONGELADA: `h1_marginal_productive_rescue` (`h1_lexico` DESCARTADA)

**Pregunta que respondía:** *¿qué configuración de ATR ofrece el mejor compromiso entre distancia de stop y protección frente al comportamiento adverso de las señales?*

**Idea central que sigue siendo válida (relación excursión-adversa vs. stop):** para una señal y un multiplicador `m`, la excursión adversa normalizada `MAE_ATR` (= `MAE_points / ATR_at_signal`) es **directamente comparable** con la distancia del stop, porque **la distancia del stop en unidades de ATR ES exactamente `m`**. La excursión adversa de una señal **"vulneraría" el stop `m`** si y solo si:

```
MAE_ATR >= m   →  breach (el movimiento adverso alcanza la distancia del stop m)
```

**ESTADO: `h1_lexico` queda DESCARTADA (retirada como métrica primaria).** No se usa. Se documenta aquí por qué, para que el error no se repita.

**Razón matemática exacta del descarte:**

- La regla descartada ordenaba primero por `stop_breach_rate(m)` con `breach_s(m) = 1[ MAE_ATR(s) >= m ]`. Pero **`breach_s(m)` decrece mecánicamente al crecer `m`**: un stop más ancho es, por construcción, **más difícil de vulnerar** (`1[MAE_ATR >= m]` solo puede pasar de 1 a 0 cuando `m` aumenta, nunca al revés para la misma señal). Por tanto `stop_breach_rate(m)` **reproduce exactamente el mismo sesgo** que `stop_trigger_rate`: mejora automáticamente con el stop más ancho.
- El segundo criterio, `median_MFE_ATR`, **NO puede corregir ese sesgo**, porque `MFE_ATR` pertenece al **SIGNAL PATH** y es **IDÉNTICO para todos los `atr_multiplier` de la misma señal**: el motor SMA, la señal y el SIGNAL PATH **no cambian con `m`** — lo único que cambia es la distancia hipotética del stop. Una variable que es **constante entre multiplicadores** no puede compensar a otra variable que **mejora automáticamente con `m`**. El segundo criterio se anula como criterio de comparación entre `m` porque no varía con `m`.

**REGLA CONGELADA (frozen rule):** ninguna métrica futura de H1 puede usar como **término corrector** una variable que sea **matemáticamente idéntica para todos los `atr_multiplier` de la misma señal**, **SALVO** que forme parte de una **comparación MARGINAL explícita** cuyo valor sí dependa del cambio de stop (por ejemplo, comparar qué ocurre entre `m_i` y `m_j` para las señales cuyo destino cambia con ese paso). Un corrector global constante en `m` está prohibido.

**Sustitución (CONGELADA):** la métrica primaria de H1 queda **CONGELADA** como **`h1_marginal_productive_rescue`** ("Rescate Productivo Marginal"), definida y operacionalizada en el item **9-bis.8-ALT**. Las otras alternativas quedan clasificadas así:

- `h1_lexico` → **DESCARTADA** (retirada, no se usa; documentada arriba solo para no repetir el error).
- `h1_rendimientos_decrecientes` → **NO es primaria**; se conserva **solo como diagnóstico SECUNDARIO** (item 9-bis.16-DR).
- `h1_holgura_slack` → **NO es primaria**; se conserva **solo como diagnóstico descriptivo SECUNDARIO** (item 9-bis.17-SLACK).

**No se presentan nuevas alternativas de H1.** La primaria es `h1_marginal_productive_rescue` y punto.

---

#### 9-bis.8-ALT. H1 primaria CONGELADA — `h1_marginal_productive_rescue` ("Rescate Productivo Marginal")

Este item **define y CONGELA** la métrica primaria de H1. Ya **no hay alternativas en evaluación**: `h1_lexico` está descartada, y `h1_rendimientos_decrecientes` / `h1_holgura_slack` quedan **solo como diagnósticos secundarios** (items 9-bis.16-DR y 9-bis.17-SLACK).

**Terminología — tolerancia adversa (NO es riesgo monetario):** un `atr_multiplier` mayor **no** implica automáticamente más riesgo en dólares. El `RiskGate` dimensiona la posición a partir de `stop_points`, de modo que un stop más ancho **puede reducir el número de contratos** y mantener el riesgo monetario objetivo **aproximadamente constante** (sujeto al redondeo a contratos enteros). Por eso H1 estudia la **`adverse_tolerance`** = la **tolerancia de precio adverso adicional** antes de invalidar la señal. **NO** es P&L, **NI** rentabilidad.

- **nombre:** `h1_marginal_productive_rescue`.
- **pregunta exacta que responde:** *al ampliar la tolerancia adversa del multiplicador consecutivo `m_i` al inmediatamente más ancho `m_j`, ¿esa tolerancia extra preserva ("rescata") señales que **después** recuperan al menos su `reference_price` antes del cruce inverso, en proporción suficiente para justificar el paso?*

**Definición CONGELADA de `rescued_signal` (rescate marginal):**

- Solo para multiplicadores **CONSECUTIVOS** `m_i < m_j`. Los seis pasos son exactamente: `1.50→1.75`, `1.75→2.00`, `2.00→2.25`, `2.25→2.50`, `2.50→2.75`, `2.75→3.00`. **No se permiten saltos no consecutivos.**
- `rescued_signal(m_i → m_j) = true` **si y solo si** ambos STOP PATHs son **evaluables** **Y** `stop_would_trigger(m_i)=true` **Y** `stop_would_trigger(m_j)=false` (el stop más estrecho se habría tocado; el inmediatamente más ancho, no).
- Si `stop_would_trigger(m_i)=true` pero `stop_would_trigger(m_j)=unknown` → `rescued_status=unknown`, y la señal queda **excluida del denominador observable**. **NUNCA** se convierte `unknown → false`.

**Población observable por paso (CONGELADA):** para cada paso `m_i → m_j`:

- `N_step_total` = todas las señales candidatas de la población analizada.
- `N_step_observable` = las que tienen `stop_would_trigger(m_i) ∈ {true,false}` **Y** `stop_would_trigger(m_j) ∈ {true,false}`.
- `N_step_censored` = `N_step_total − N_step_observable`.
- `step_censoring_rate` = `N_step_censored / N_step_total`.
- Cualquier `unknown` en cualquiera de los dos multiplicadores **excluye** la señal de `N_step_observable`.

**Definición CONGELADA de `productive_rescue` (rescate productivo):** una `rescued_signal` es `productive_rescue=true` si, **DESPUÉS del primer toque del stop `m_i`**, el precio vuelve **al menos hasta `reference_price`** antes del cruce inverso SMA:

- **BUY:** `high >= reference_price`.
- **SELL:** `low <= reference_price`.

Esto **NO** es ganancia económica; significa que el stop más estrecho habría invalidado una señal que **luego recuperó por completo la excursión adversa** de vuelta al `reference_price`.

**Ventana posterior al toque — regla OHLCV CONGELADA:** si `first_stop_touch_bar_timestamp = t_stop`, **NO** se usa el `high`/`low` de esa misma vela para la recuperación (OHLCV de 1 minuto no da el orden intra-vela). La búsqueda **empieza en la vela INMEDIATAMENTE POSTERIOR a `t_stop`** y **termina en el cruce inverso**. Si **no existe** ninguna vela evaluable posterior al toque, **NO** se fija automáticamente `productive_rescue=false`: se fija `productive_rescue_status=unknown` con **motivo explícito**, distinguiendo al menos: `calibration_boundary`, `no_post_touch_bar` y cualquier otra causa realmente necesaria. `productive_rescue_status` describe **ÚNICAMENTE** la evaluabilidad de ESTA métrica; **no reemplaza** `signal_path_censored` ni `stop_path_censored`.

**Población observable del rescate productivo (CONGELADA):** dentro de `rescued_signal=true`:

- `N_rescued_total` = total de señales rescatadas.
- `N_rescued_observable` = las que tienen `productive_rescue_status ∈ {true,false}`.
- `N_rescued_unknown` = las indeterminables.
- **Nunca** se cuenta `unknown` como `false`. **Siempre** se reportan los tres.

**Métricas de H1 (por paso consecutivo) — CONGELADAS:**

1. **`rescue_quality`** = `N_productive_rescue / N_rescued_observable` → de las señales realmente rescatadas por este aumento de tolerancia adversa, ¿qué proporción recuperó el `reference_price`?
2. **`productive_rescue_population_rate`** = `N_productive_rescue / N_step_observable` → ¿qué proporción de toda la población observable obtuvo un rescate productivo en este paso?

**Relación matemática (documentada explícitamente):** `productive_rescue_population_rate` **NO es evidencia independiente** de `rescue_quality`. Se cumple:

```
productive_rescue_population_rate = rescue_quality × (N_rescued_observable / N_step_observable)
```

salvo diferencias por estados no evaluables, que deben documentarse con exactitud. `rescue_quality` = **calidad** del rescate; `(N_rescued_observable / N_step_observable)` = **incidencia** del rescate; `productive_rescue_population_rate` **resume ambas**. **NO** se recombinan mediante pesos; **NO** se cuentan como dos evidencias estadísticas independientes.

**Δm no corrige el sesgo (CONGELADO):** todos los pasos tienen `Δm = 0.25`, así que dividir cualquier métrica por `Δm` solo la multiplica por una **constante común** → no cambia el ordenamiento → **NO** se usa como supuesta penalización del stop ancho.

- **unidad experimental:** `signal_id` (el conjunto rescatado se define within-signal a través de `m` consecutivos; comparación intrínsecamente pareada).
- **variables utilizadas:** `stop_would_trigger(m_i)`, `stop_would_trigger(m_j)`, `reference_price`, `high`/`low` posteriores al toque (SIGNAL PATH), cruce inverso, `first_stop_touch_bar_timestamp`.
- **tratamiento de censura:** cualquier `unknown` de STOP PATH excluye del `N_step_observable`; cualquier `productive_rescue_status=unknown` excluye del `N_rescued_observable`; los tres números de censura se reportan siempre.
- **dirección de optimización:** mayor `rescue_quality` y `productive_rescue_population_rate` = el ensanchamiento de tolerancia adversa "vale la pena" según la regla secuencial (item 9-bis.13-SEQ).
- **por qué NO favorece mecánicamente al stop más ancho:** el numerador **solo existe** para las señales realmente rescatadas por el paso `m_i→m_j` y exige **recuperación real posterior** hasta `reference_price`; ensanchar más solo ayuda si sigue rescatando señales que **luego se recuperan**.
- **ventajas:** aísla el efecto marginal real de cada ensanchamiento; interpretable como "calidad e incidencia del rescate por paso"; sin P&L inventado.
- **limitaciones:** si **muy pocas** señales se rescatan en un paso, `rescue_quality` es **inestable** → requiere los *gates* de muestra mínima/censura (items 9-bis.13 y 9-bis.5).

---

#### 9-bis.9. H1 — MÉTRICAS SECUNDARIAS

Todas son **secundarias** (apoyan, no deciden). Formato de siete campos:

1. **`median_MAE_ATR` (y cuartiles)** → mediana y cuartiles de `MAE_ATR` → población: señales observables por `m`, pareadas por `signal_id` → censura: `unknown` fuera, reportada → dirección: menor MAE_ATR = movimiento adverso más contenido → interpretación: cuán lejos suele ir el precio en contra, en unidades de ATR → limitaciones: sesgada; comparte `ATR_at_signal` con el stop.
2. **`median_MFE_ATR` (y cuartiles)** → mediana y cuartiles de `MFE_ATR` → misma población y censura → dirección: mayor = más excursión favorable disponible → interpretación: cuánto se mueve el precio a favor tras la señal → limitaciones: no implica que sea capturable (no hay take-profit).
3. **`stop_trigger_rate` (observable)** → `N_stop_true / N_stop_observable` → población: STOP PATHs observables por `m` → censura: `unknown` fuera del denominador, reportada → dirección: **no** se optimiza en solitario (sesgo del stop ancho, 5.10) → interpretación: con qué frecuencia se observa tocado el stop → limitaciones: baja mecánicamente al ensanchar el stop.
4. **`stop_survival_rate` (observable)** → `N_stop_false / N_stop_observable` → misma población → censura: igual → dirección: **no** se optimiza en solitario → interpretación: con qué frecuencia el stop sobrevive toda la ventana → limitaciones: complemento del anterior; mismo sesgo mecánico.
5. **`censoring_rate`** → `N_censored / N_total` → población: todas las observaciones de la config → censura: es la propia medida de censura → dirección: menor = evidencia más completa → interpretación: qué fracción quedó sin resolver por el límite del split → limitaciones: puede ser **asimétrica** (los stops anchos censuran más, item 9-bis.18(a)).

**Prohibido en H1 (primaria y secundarias):** P&L, take-profit, *profit factor*, *expectancy* económica.

---

#### 9-bis.10. H2 — MÉTRICA PRIMARIA (propuesta final)

**Pregunta que responde:** *¿las señales Aceptadas muestran de forma consistente un comportamiento posterior mejor que las Rechazadas?*

Se comparan **DISTRIBUCIONES** (no solo medias, por la asimetría y los atípicos) de `MFE`, `MAE`, `MFE_ATR`, `MAE_ATR` y, cuando aplique, el comportamiento del stop.

- **nombre:** `h2_delta` — **tamaño de efecto basado en rangos**: **probabilidad de superioridad / delta de Cliff** entre Aceptadas y Rechazadas, calculada por separado sobre `MAE_ATR` (adverso) y sobre `MFE_ATR` (favorable).
- **fórmula:** delta de Cliff `δ = (#{a>r} − #{a<r}) / (n_A · n_R)`, donde `a` recorre valores del grupo Aceptado y `r` del Rechazado; equivalente a `2·P(superioridad) − 1`. Se calcula un `δ` para `MAE_ATR` y otro para `MFE_ATR`, **por cada umbral** `{0.50, 0.60, 0.70, 0.80}`. IC por **bootstrap de BLOQUES semanales ISO** (item 9-bis.13-CI revisado; no remuestreo por `signal_id` individual).
- **población:** señales SMA base de cada umbral, partidas en Aceptado vs. Rechazado (Rechazado marcado `counterfactual_diagnostic`); observables. Recordar el anidamiento entre umbrales (item 9-bis.3).
- **censura:** `unknown` fuera de los cálculos que exijan estado conocido; `censoring_rate` reportada por grupo (item 9-bis.11).
- **dirección de optimización:** para `MAE_ATR`, se espera que las Aceptadas tengan **menor** excursión adversa (δ con signo que indique Aceptado mejor); para `MFE_ATR`, que tengan **mayor** excursión favorable. H2 se sostiene si el signo y la magnitud del efecto son **consistentes** en la dirección esperada.
- **interpretación:** `δ` mide **cuán frecuentemente** una señal Aceptada se comporta mejor que una Rechazada, con robustez ante colas; su IC indica si el efecto es estable.
- **limitaciones:** (1) **tamaño de efecto ≠ significancia**: un **valor-p por sí solo NO puede ser la métrica de selección**; (2) el anidamiento entre umbrales correlaciona las comparaciones (item 9-bis.3); (3) posible desbalance de tamaños entre grupos.

**Distinción explícita:** se reporta **tamaño de efecto + dirección + robustez (IC)**, y se **distingue** del valor-p. La significancia estadística sola no selecciona nada.

**Gate numérico exacto de H3-A (CONGELADO) — operacionaliza `h2_delta`:** la comparación inferencial es **Aceptado vs. Rechazado** (Aceptado vs. A es solo descriptivo). Por cada `volume_threshold_factor`, **H3-A pasa SOLO SI**:

- **`MFE_ATR`:** `Cliff_delta_MFE >= +0.147` **Y** su IC 95% **no cruza 0**;
- **`MAE_ATR`:** `Cliff_delta_MAE <= −0.147` **Y** su IC 95% **no cruza 0**.

**Interpretación:** las Aceptadas deben mostrar al menos un efecto favorable "pequeño" en MFE **y** en MAE, ambos respaldados por IC. El `0.147` se registra como **convención ex-ante** basada en la escala habitual de efecto "pequeño" de la delta de Cliff, **no** como ley universal. Aplican también los *gates* de muestra/censura de H2 (items 9-bis.5 y 9-bis.13).

**Nota anti-duplicación:** la primaria de H2 aprobada previamente (`h2_delta`, delta de Cliff) **no tenía umbral numérico**; este gate `±0.147` es su **operacionalización exacta NUEVA**, no un duplicado. **NO** se acepta "Aceptado debe ser mejor" sin fórmula.

---

#### 9-bis.11. H2 — MÉTRICAS SECUNDARIAS

Mismo formato de siete campos:

1. **`median_diff_<métrica>` (Aceptado − Rechazado)** para cada métrica de excursión (`MFE`, `MAE`, `MFE_ATR`, `MAE_ATR`) → fórmula: `mediana(Aceptado) − mediana(Rechazado)` → población: por umbral, observables → censura: `unknown` fuera, reportada → dirección: según métrica (adverso menor / favorable mayor en Aceptado) → interpretación: diferencia central de comportamiento entre grupos → limitaciones: la mediana no captura toda la forma de la distribución.
2. **Tamaños de muestra `n_Aceptado` / `n_Rechazado` por umbral** → conteos → población: por umbral → censura: se reportan también `N_observable`/`N_censored` por grupo → dirección: n/a (diagnóstico) → interpretación: base de cada grupo y su (des)balance → limitaciones: grupos muy desbalanceados debilitan el `δ`.
3. **`censoring_rate` por grupo** → `N_censored / N_total` por grupo (Aceptado, Rechazado) → población: por umbral → censura: es la propia medida → dirección: menor mejor → interpretación: cuánta evidencia quedó sin resolver por grupo → limitaciones: puede diferir entre grupos y sesgar la comparación.

---

#### 9-bis.12. Comparaciones múltiples

Con **7 ATR + 4 volumen + 28 conjuntas**, **NO** se elige por el **valor-p más pequeño** (eso es *p-hunting* — buscar significancia probando muchas veces).

- Se **prefieren tamaños de efecto con IC por bootstrap** (por bloques semanales ISO, item 9-bis.13-CI revisado) por encima de la caza de valores-p.
- **Si** se usa alguna prueba inferencial, se aplica un **control de multiplicidad** (por ejemplo, **FDR de Benjamini–Hochberg** — controla la proporción esperada de falsos positivos) y se **reportan los valores ajustados**.
- Se mantiene **proporcional**: una regla robusta de **tamaño de efecto + IC** es **suficiente y preferida** en esta etapa; el control de multiplicidad es un respaldo, no el eje.

Esto se declara explícitamente para no derivar en selección por significancia.

---

#### 9-bis.13. Gates de evidencia de H1 (CONGELADOS)

Los *gates* de evidencia de H1 quedan **CONGELADOS** con pisos numéricos exactos. Son **convenciones de gobernanza ex-ante para la Etapa 1**, no garantías universales:

```
N_step_observable_min    = 50
N_rescued_observable_min = 30
step_censoring_rate_max  = 0.30

evidence_sufficient = (N_step_observable    >= 50)
                  AND (N_rescued_observable >= 30)
                  AND (step_censoring_rate  <= 0.30)
```

- Si **cualquiera** de las tres condiciones falla → `evidence_sufficient = false`.
- **No hay veto humano:** la elegibilidad depende **exclusivamente** de estos umbrales reproducibles fijados antes de la Calibración.
- Se reportan siempre los cuatro números de censura por paso (`N_step_total`, `N_step_observable`, `N_step_censored`, `step_censoring_rate`) y los tres del rescate productivo (`N_rescued_total`, `N_rescued_observable`, `N_rescued_unknown`), según items 9-bis.4 y 9-bis.8-ALT.

**Naturaleza de estos valores (declaración honesta):** son **convenciones ex-ante transparentes**, no derivadas de los datos; se **congelan antes de la Calibración**. `N_step_observable_min = 50` es más conservador que el ≥30 habitual, justificado por la asimetría de las excursiones y por el pareo; `N_rescued_observable_min = 30` garantiza un mínimo de rescates observables antes de confiar en `rescue_quality`; `step_censoring_rate_max = 0.30` evita que un subconjunto observable no representativo (item 9-bis.18(a)) sostenga la decisión.

---

#### 9-bis.13-CI (revisado). Block bootstrap temporal por semana ISO (CONGELADO)

**Cambio metodológico (Corrección 1):** la **unidad de remuestreo** del bootstrap deja de ser el `signal_id` individual y pasa a ser el **BLOQUE semanal ISO**. Solo cambia **cómo** se estima la incertidumbre; las **métricas y los umbrales NO cambian** (siguen intactos `LI95(rescue_quality) > 0.50`, `LI95(productive_rescue_population_rate) > 0`, gate H2 de delta de Cliff `±0.147`). Se mantienen `bootstrap_resamples = 2000`, `confidence_level = 0.95` (IC 95% bilateral) y **semilla fija reproducible** documentada por el ejecutor (runner). El valor-p **NO** se usa como criterio.

**(CI.1) Definición del bloque.** `bootstrap_block_id = ISO_year + ISO_week`, derivado de forma **determinista** del `timestamp` UTC de cada señal. Un bloque contiene **TODAS** las `signal_id` que pertenecen a esa semana ISO **Y** todas sus observaciones/variables derivadas. Cuando un bloque es seleccionado, estos elementos **permanecen juntos**: las señales de la semana, sus escenarios contrafactuales ATR, su clasificación Aceptado/Rechazado y sus métricas derivadas. **NO** se hacen remuestreos independientes que rompan el pareo interno por `signal_id`.

**(CI.2) Procedimiento por réplica.** Para cada réplica bootstrap:
1. tomar **solo** los `bootstrap_block_id` **no vacíos** de la población ESPECÍFICA analizada;
2. remuestrear esos bloques **completos CON reemplazo**;
3. conservar **todas** las `signal_id` contenidas en cada bloque seleccionado;
4. recomputar la métrica sobre la muestra bootstrap resultante.

Nunca se generan ni se asumen semanas vacías. Se mantienen `bootstrap_resamples = 2000`, `confidence_level = 0.95` y la semilla fija registrada por el runner.

**(CI.3) Alcance de aplicación.** Aplica a **TODO IC que participe en decisiones de Calibración**: `rescue_quality`, `productive_rescue_population_rate`, `Cliff_delta_MFE` y `Cliff_delta_MAE`. **NO** se cambian sus métricas ni sus umbrales (`LI95(rescue_quality) > 0.50`; `LI95(productive_rescue_population_rate) > 0`; H2 delta de Cliff `±0.147`). Solo cambia la **estimación de la incertidumbre**.

**(CI.4) Gate de bloques mínimos (CONGELADO).** `N_bootstrap_blocks_min = 20` para cualquier población cuyo IC participe en una decisión. `N_bootstrap_blocks` = número de **semanas ISO no vacías** presentes en LA población ESPECÍFICA usada para ese cálculo. Ejemplos:
- H1 general → semanas que contienen señales H1 evaluables;
- H1 sobre `Accepted(0.70)` → semanas no vacías de esa población;
- H2 Aceptado/Rechazado → semanas representadas en las poblaciones correspondientes, según la definición exacta de cada IC.

Si `N_bootstrap_blocks < 20` → `bootstrap_evidence_insufficient = true` y ese IC **NO puede producir un PASS** de su gate. El **20 es una convención ex-ante conservadora de Etapa 1**, no una garantía estadística universal.

**(CI.5) No se calcula ahora el valor real.** Este turno **NO** computa el `N_bootstrap_blocks` real (no se lee ni se ejecuta la Calibración). Solo se **congela**: cómo se define, cómo se computa, el mínimo, y qué ocurre si no se cumple. El valor real lo **produce/registra el runner** cuando se autorice la ejecución.

**(CI.6) Nota de alcance estadístico.** El block bootstrap semanal es una **convención metodológica de Etapa 1** para preservar mejor la dependencia temporal de corto plazo que el remuestreo individual; **NO** se presenta como un modelado completo de toda la autocorrelación ni de todos los regímenes de mercado.

Este es el **único** método de IC para toda la Calibración; no se duplica con ningún otro. (Las referencias a bootstrap en los items 9-bis.2, 9-bis.9, 9-bis.10, 9-bis.12 y 9-bis.13 apuntan a esta misma definición congelada por bloques semanales ISO.)

---

#### 9-bis.13-SEQ. Regla determinista de avance de ATR (CONGELADA)

Selección **secuencial**. Se parte de `selected_atr = 1.50` y se evalúan los pasos **en orden**: `1.50→1.75`, `1.75→2.00`, `2.00→2.25`, `2.25→2.50`, `2.50→2.75`, `2.75→3.00`.

Para el paso `j`: `Q_j = rescue_quality`, `P_j = productive_rescue_population_rate`.

Un paso **justifica ampliar el ATR SOLO SI** se cumplen **las tres** condiciones:

1. `evidence_sufficient = true` (item 9-bis.13);
2. el **límite inferior del IC 95% de `Q_j`** es **estrictamente > 0.50**;
3. el **límite inferior del IC 95% de `P_j`** es **estrictamente > 0**.

- **Interpretación de (2):** más de la mitad de las señales rescatadas marginalmente muestran recuperación productiva, con evidencia compatible con el IC.
- **Interpretación de (3):** hay evidencia de un efecto poblacional productivo no nulo.
- **Prohibido:** frases cualitativas ("suficientemente bueno" / "material" / "parece estable"). Solo las tres condiciones numéricas deciden.

**Regla de parada (item 9-bis.13-STOP):** el **primer** paso que falla las tres condiciones **DETIENE** el avance. **No** se salta un paso para evaluar uno más alto como justificación. `selected_atr` = **el último multiplicador inferior justificado**.

- **Ejemplo:** `1.50→1.75` PASA, `1.75→2.00` PASA, `2.00→2.25` FALLA ⇒ `selected_atr = 2.00`.
- **Principio:** usar la **menor tolerancia adversa** cuyo ensanchamiento sucesivo esté respaldado por rescates productivos observables.

**Casos de evidencia insuficiente (CONGELADOS):**

- **Caso A — el primer paso (`1.50→1.75`) tiene `evidence_sufficient = false`** ⇒ H1 global `evidence_insufficient = true`; **NO** se auto-selecciona `1.50`; **NO** gradúa por H1. La ausencia de evidencia para ensanchar **NO** es evidencia a favor de `1.50`.
- **Caso B — un paso posterior pasa a `evidence_sufficient = false` tras pasos previos aprobados** ⇒ se detiene, se conserva el último `atr_multiplier` justificado y se documenta que el paso superior no pudo evaluarse.
- **Caso C — ningún paso es evaluable** ⇒ H1 `evidence_insufficient = true`; no hay graduación por esa vía.

---

#### 9-bis.14. Regla de GRADUACIÓN a Validación (determinista y reproducible)

Estructura **"gates → ranking → Top-N"**:

1. **Gates (condiciones mínimas de aprobado/reprobado)** — una configuración solo pasa si cumple **todas**:
   - cumple los **gates de evidencia de H1** (`evidence_sufficient`, item 9-bis.13);
   - su **censura** cae dentro de la regla pre-registrada (`step_censoring_rate_max = 0.30`, items 9-bis.5 y 9-bis.13);
   - su **`atr_multiplier`** resulta de la **regla secuencial congelada** (`selected_atr`, item 9-bis.13-SEQ); si H1 es `evidence_insufficient`, no hay `selected_atr` por esa vía.
2. **Ranking** — entre las candidatas C que pasan los gates, se ordena por el criterio **lexicográfico congelado** del item 9-bis.16, **no** por un escalar arbitrario ni por puntaje ponderado.
3. **Top-N** — gradúan las **`Top-N_C`** mejores (item 9-bis.15).

Reglas duras:
- **Orden de evaluación:** primero gates, luego ranking, luego corte Top-N. Una configuración que falla un *gate* **no** entra al ranking.
- **Criterio de exclusión:** falla de cualquier *gate* → excluida.
- **Número máximo graduado:** `Top-N_C` (item 9-bis.15).
- **La selección usa ÚNICAMENTE información de Calibración.** La **Validación NO puede usarse para decidir quién va a Validación**. La **Validación se usa una sola vez** (coherente con la sección 10).

---

#### 9-bis.14-UNIV. Universo de Validación A/B/C (CONGELADO)

Aunque H3 use *gates* jerárquicos, **las 36 configuraciones (A=1, B=7, C=28) se calculan y quedan trazables** antes de aplicar cualquier corte; **no se poda la grilla antes de calcular** (item 9-bis.21 / sección 2). Lo que cambia es **qué llega a Validación**:

- **A — Baseline SMA(9)/SMA(21):** va **siempre** a Validación como **CONTROL**. **No** cuenta en el Top-N.
- **B — Ablación ATR:** H1 se corre sobre la **población SMA COMPLETA** y selecciona **a lo sumo UNA** configuración B (el `selected_atr` de la regla secuencial). Si la H1 general es `evidence_insufficient`, **no gradúa ninguna B**. B **no** cuenta en el Top-N de C; su papel es la ablación `A → A+ATR`.
- **C — ATR + Volumen:** cada `volume_threshold_factor` que **pase H3-A** puede producir **a lo sumo una** configuración C vía H3-B (H1 recomputada sobre su población Aceptada). Por tanto hay **a lo sumo cuatro candidatas C** antes del Top-N. El **Top-N aplica EXCLUSIVAMENTE a estas candidatas C**.

Esto mantiene la interpretación limpia: **A = SMA**, **B = SMA+ATR**, **C = SMA+ATR+Volumen**.

---

#### 9-bis.15. Top-N (CONGELADO)

- **`Top-N_C = 3` (CONGELADO).** Justificación: llevar un conjunto **pequeño y plausible** de configuraciones C a una única Validación, sin convertir la Validación en una segunda Calibración encubierta. El Top-N aplica **exclusivamente a las candidatas C** (item 9-bis.14-UNIV); A y B no cuentan aquí.
- **Si `N_candidatas_C < 3`:** gradúan **solo las disponibles**; **NO** se rellena con configuraciones sub-umbral.
- **Si `N_candidatas_C = 0`:** la Validación **igual evalúa A** y, si existe, **B**. Un fallo completo de H3 **NO** invalida metodológicamente a A/B.
- **Empate en la posición N:** se resuelve con el **desempate determinista congelado** del item 9-bis.16.
- **Prohibido** usar "similar" / "prácticamente equivalente" **sin** una definición matemática previa. Cualquier de-duplicación exige definir equivalencia matemáticamente (epsilon pre-registrado); de lo contrario, **NO se de-duplica**.

---

#### 9-bis.16. Ranking de candidatas C y desempate (CONGELADO, lexicográfico, sin puntaje ponderado)

**Corrección 2 — separación de responsabilidades (CONGELADA).** El ranking anterior ordenaba las candidatas C usando `productive_rescue_population_rate` y `rescue_quality` "del último paso de H1 que justificó el ATR seleccionado". Eso era **defectuoso** por dos razones: (1) queda **INDEFINIDO cuando `selected_atr = 1.50`** (ningún paso justificó ese ATR, porque 1.50 es el punto de partida y no resulta de ningún paso `m_i→m_j`); y (2) comparaba **métricas marginales provenientes de pasos ATR potencialmente distintos** entre candidatas. Por tanto se **RETIRAN todas las métricas de H1 del ranking ENTRE candidatas C**.

**Separación de responsabilidades congelada:**
- **H1 selecciona `atr_multiplier` DENTRO de cada población Aceptada** (regla secuencial, items 9-bis.13-SEQ a 9-bis.13-STOP).
- **H2/H3-A determina la calidad del `volume_threshold_factor` y ORDENA las candidatas C entre sí.**
- Una vez que H1 seleccionó el ATR de una candidata, sus **métricas marginales NO se reutilizan** para compararla contra otro umbral. Esto cubre **automáticamente** el caso `selected_atr = 1.50`.

**Efecto conjunto de H2 (definición congelada):**

```
h2_joint_effect = min(Cliff_delta_MFE, −Cliff_delta_MAE)
```

Ambos términos se orientan de modo que **más alto = mejor**; `h2_joint_effect` representa **la más débil** de las dos mejoras de H2, evitando que una dimensión enorme compense la ausencia en la otra.

**Definiciones adicionales por candidata C válida (CONGELADAS):**

```
h2_joint_ci_width      = max( width(IC95_Cliff_MFE), width(IC95_Cliff_MAE) )    # menor = mejor
h2_joint_censoring_rate = max( censoring_rate_Accepted, censoring_rate_Rejected ) # menor = mejor
```

- Los anchos de IC 95% aquí usan el **block bootstrap por semana ISO** de la Corrección 1 (item 9-bis.13-CI revisado).
- `h2_joint_censoring_rate` se usa **solo** porque H2 mantiene tasas de censura diferenciadas Aceptado/Rechazado (item 9-bis.11); **NO** se inventa una tasa agregada nueva.

**Orden lexicográfico de las candidatas C (CONGELADO)** — se compara criterio por criterio; solo ante empate exacto se pasa al siguiente. **NO se suman ni se ponderan. `atr_multiplier` NO participa en este ranking ni en el desempate:**

1. mayor `h2_joint_effect`;
2. menor `h2_joint_ci_width`;
3. menor `h2_joint_censoring_rate`;
4. mayor `N_Accepted_observable`;
5. menor `volume_threshold_factor` **SOLO como clave técnica final** ante **igualdad matemática exacta completa**.

**Naturaleza de la clave final:** el criterio 5 (`volume_threshold_factor`) **NO** representa superioridad metodológica; existe **solo para garantizar determinismo**. `atr_multiplier` queda **explícitamente fuera** del ranking y del desempate.

Si el empate ocurre exactamente en la posición N **antes** de la clave técnica, **NO** se gradúan configuraciones extra: la clave técnica final preserva exactamente `Top-N_C <= 3` (evita que la Validación crezca en silencio).

---

#### 9-bis.16-DR. Diagnóstico secundario `h1_rendimientos_decrecientes` (NO decide)

- **nombre:** `h1_rendimientos_decrecientes` — **diagnóstico SECUNDARIO únicamente.**
- **qué mide:** rendimientos marginales decrecientes de `productive_rescue_population_rate` a lo largo de los **seis pasos consecutivos**.
- **regla:** **NO** usa codo subjetivo; **NO** modifica la regla secuencial (items 9-bis.13-SEQ a 9-bis.13-STOP). Solo describe la forma de la curva para lectura humana.

---

#### 9-bis.17-SLACK. Diagnóstico descriptivo `h1_holgura_slack` (NO decide)

- **nombre:** `h1_holgura_slack` — **diagnóstico descriptivo únicamente.**
- **qué mide:** para señales que NO tocan el stop `m` (observables), `slack_s(m) = m − MAE_ATR(s)` (≥ 0); se describe cómo cambia su **población** al variar `m`.
- **regla:** **NO** participa en la selección ni en el desempate; es puramente descriptivo.

---

#### 9-bis.17. Comparabilidad con el Baseline A

**A = SMA(9)/SMA(21)** **NO tiene stop ATR dinámico**, por lo que **NO tiene un `stop_trigger_rate` directamente comparable**. **NO se inventa** un stop para A con tal de forzar la comparación.

- **QUÉ SÍ se puede comparar contra A** — las **métricas diagnósticas del SIGNAL PATH**, porque describen la **señal**, no un stop: `MFE`, `MAE`, `MFE_ATR`, `MAE_ATR` sobre la **población completa de señales SMA**.
- **QUÉ SOLO se puede comparar entre configuraciones ATR** — todo lo del **STOP PATH**: `stop_trigger_rate`, `stop_survival_rate`, relaciones de *breach* (`MAE_ATR >= m`). A **no participa** en estas, porque no tiene stop.

Se documenta explícitamente esta **partición de comparabilidad** para no atribuir a A métricas de stop que no posee.

---

#### 9-bis.18. Riesgos metodológicos residuales (declarados honestamente)

- **(a) Censura asimétrica:** el manejo de `unknown` reduce la muestra efectiva de forma **asimétrica** para los stops **anchos** (un stop ancho tiende a no tocarse y, si además no hay cruce inverso antes del límite, queda `unknown`), lo que puede favorecerlos artificialmente si no se controla con la muestra mínima y el reporte de `censoring_rate`.
- **(b) Umbrales anidados:** los conjuntos Aceptado/Rechazado anidados (item 9-bis.3) hacen que las comparaciones de H2 **entre umbrales** estén **correlacionadas**; no deben leerse como evidencia independiente acumulable.
- **(c) Horizonte endógeno desigual:** la ventana termina en el cruce inverso, cuya longitud **varía por señal**; por tanto MFE/MAE se miden sobre **horizontes de duración desigual**. Se documenta que **MFE/MAE NO están normalizados por horizonte** y que, en consecuencia, comparar excursiones entre señales mezcla ventanas de distinta duración (una excursión mayor puede deberse simplemente a una ventana más larga).
- **(d) Denominador compartido `ATR_at_signal`:** `ATR_at_signal` se usa **a la vez** para normalizar excursiones (`MAE_ATR`, `MFE_ATR`) y para fijar la distancia del stop (`m × ATR_at_signal`). Ese **denominador compartido** puede **inducir correlación** entre `MAE_ATR` y el umbral de *breach* `m`. Se **señala explícitamente** como acoplamiento conocido (afecta la interpretación del item 9-bis.8).
- **(e) Independencia del bootstrap (MITIGADA, no eliminada):** el supuesto de independencia temporal del bootstrap está ahora **MITIGADO** por el remuestreo por **bloques semanales ISO** (item 9-bis.13-CI revisado), que conserva juntas las señales de una misma semana y preserva la dependencia de corto plazo mejor que el remuestreo individual por `signal_id`. **No queda eliminado:** persiste **dependencia residual en las fronteras entre semanas** (señales correlacionadas que caen en semanas ISO contiguas quedan en bloques distintos) y la **dependencia de largo alcance** (correlación que excede la ventana semanal) **sigue sin controlarse**.

---

#### 9-bis.19. Congelamiento anti-*data-snooping*

**Todo lo que quede congelado en 9-bis se congela ANTES de calcular la Calibración.** Una vez observados los resultados, **nada ya congelado** puede cambiarse retrospectivamente.

**Puntos CONGELADOS antes de ejecutar la Calibración (todos):** `rescued_signal`; `productive_rescue`; los estados `unknown`; la ventana posterior al toque; los denominadores de H1; los *gates* de evidencia; los intervalos de confianza; la regla secuencial de ATR; el gate de H2; H3-A; H3-B; la estructura A/B/C de Validación; el Top-N; el ranking; los desempates. Ya **no queda ningún punto abierto** en 9-bis.

Si más adelante se descubre que una regla ya congelada es **defectuosa**, el cambio **debe documentarse** **Y** la Calibración se considera **ya usada** para esa decisión: **no** se puede presentar una regla nueva **como si** hubiera sido *ex ante*. Cualquier cambio posterior a observar la Calibración se registra como `decision_informed_by_calibration=true` y rige solo para datos aún no observados bajo la regla vieja.

---

#### 9-bis.20. Síntesis de H3 — CONGELADA (jerárquica H3-A → H3-B)

**Pregunta que responde:** *¿el filtro de volumen selecciona un subconjunto de señales con mejor perfil diagnóstico, y qué tolerancia adversa (ATR) conviene asignar a ese subconjunto?*

**Corrección del error conceptual anterior (retirada de la formulación previa):** la formulación previa comparaba cada configuración conjunta contra A pidiendo que "dominara a A en `MAE_ATR`/`MFE_ATR` de población". **Eso era conceptualmente incorrecto:** `A = SignalEngine SMA(9)/SMA(21)` genera la **MISMA población de señales** que las configuraciones ATR; **el ATR NO modifica la señal, ni el cruce, ni el SIGNAL PATH, ni el MFE/MAE de la señal**. Por tanto, una configuración **no puede "dominar a A" en MFE/MAE de población** porque, **antes del VolumeFilter**, describe **exactamente las mismas señales** que A. Esa comparación se **ELIMINA**.

**H3 se redefine en dos componentes distintos:**

- **H3-A — efecto del VolumeFilter (aquí SÍ cambia la población):** comparar **Aceptado vs. Rechazado** con el **gate numérico exacto `±0.147`** de la delta de Cliff sobre `MFE_ATR` y `MAE_ATR`, ambos con IC 95% que no cruce 0 (item 9-bis.10). Aceptado vs. A es solo **descriptivo**. **Sin** afirmaciones de rentabilidad.
- **H3-B — elección de ATR dentro de la población filtrada (CONGELADO):** para **cada** `volume_threshold_factor` que **pase H3-A**, se **RECOMPUTA H1 completa** sobre su población `Accepted(threshold)`:
  - `0.50 → H1 sobre Accepted(0.50)`;
  - `0.60 → H1 sobre Accepted(0.60)`;
  - `0.70 → H1 sobre Accepted(0.70)`;
  - `0.80 → H1 sobre Accepted(0.80)`.
  - **NO** se reutiliza la H1 general (la de la población SMA completa). Se mantiene `signal_id` como unidad. **Aquí es donde se evalúa de verdad la interacción ATR × Volumen.** **División conceptual:** *"el VolumeFilter decide qué población conservar; el ATR decide hasta dónde puede ampliarse la tolerancia adversa dentro de esa población."*

**H3 final (jerárquico, CONGELADO):**
- **H3-A:** el VolumeFilter debe pasar su gate Aceptado-vs-Rechazado (`±0.147`).
- **H3-B:** sobre esa población Aceptada, H1 selecciona el ATR vía la **regla secuencial congelada** (items 9-bis.13-SEQ a 9-bis.13-STOP).
- **NO** hay puntaje H2+H1; **NO** se combinan tamaños de efecto de volumen con proporciones de ATR.

**H3 no es rentabilidad:** todo lo anterior es **perfil diagnóstico** (excursión y censura), nunca P&L, *expectancy* ni *profit factor*.

**ESTADO: H3 CONGELADA.** H1 está resuelta (`h1_marginal_productive_rescue`, item 9-bis.8-ALT), por lo que la estructura jerárquica H3-A → H3-B queda **congelada** en su totalidad.

---

#### 9-bis.21. Resolución de las tres ambigüedades previas

- **(i) Variable de recuperación del rescate productivo:** **CONGELADA** como "retorno al `reference_price`" (item 9-bis.8-ALT / `productive_rescue`). Las 4 candidatas anteriores quedan **superadas (superseded)**.
- **(ii) Síntesis de H3-B:** **RESUELTA** — H3-B recomputa H1 completa por umbral sobre el subconjunto Aceptado (item 9-bis.20).
- **(iii) Frontera del gate de H3-A:** **RESUELTA** — es la regla exacta de delta de Cliff `±0.147` con IC 95% que no cruza 0 (items 9-bis.10 y 9-bis.20).

---

#### 9-bis.22. Tabla FINAL de entrega (decisión | regla final | estado)

Estado ∈ {FROZEN, BLOCKED} únicamente. Nada se marca FROZEN si contiene expresiones cualitativas no operacionalizadas.

| decisión | regla final | estado |
|---|---|---|
| H1 primaria | `h1_marginal_productive_rescue` (Rescate Productivo Marginal); `h1_lexico` descartada; `h1_rendimientos_decrecientes` y `h1_holgura_slack` solo secundarios | FROZEN |
| Definición de `rescued_signal` | Solo pasos consecutivos; `stop_would_trigger(m_i)=true AND stop_would_trigger(m_j)=false` con ambos STOP PATH evaluables; `unknown` → `rescued_status=unknown`, nunca `false` | FROZEN |
| Definición de `productive_rescue` | Tras el primer toque de `m_i`, el precio vuelve al menos a `reference_price` antes del cruce inverso (BUY: `high>=ref`; SELL: `low<=ref`) | FROZEN |
| Observabilidad del rescate productivo | `N_rescued_total`/`N_rescued_observable`/`N_rescued_unknown`; `unknown` nunca cuenta como `false`; siempre se reportan los tres | FROZEN |
| Ventana posterior al toque | Empieza en la vela inmediatamente posterior a `t_stop`, termina en el cruce inverso; sin vela evaluable → `productive_rescue_status=unknown` con motivo (`calibration_boundary`/`no_post_touch_bar`/otra) | FROZEN |
| Denominadores de H1 | `rescue_quality = N_productive_rescue / N_rescued_observable`; `productive_rescue_population_rate = N_productive_rescue / N_step_observable`; relación documentada, no evidencias independientes | FROZEN |
| Gates de evidencia | `N_step_observable_min=50`, `N_rescued_observable_min=30`, `step_censoring_rate_max=0.30`; `evidence_sufficient` determinista, sin veto humano | FROZEN |
| Método de bootstrap (IC) | Block bootstrap temporal 95% bilateral, `bootstrap_resamples=2000`, `confidence_level=0.95`, semilla fija reproducible; unidad de remuestreo = BLOQUE semanal ISO (no `signal_id` individual); no valor-p como criterio (item 9-bis.13-CI revisado) | FROZEN |
| Definición del bloque bootstrap | `bootstrap_block_id = ISO_year + ISO_week` derivado determinista del `timestamp` UTC; el bloque agrupa todas las `signal_id` de esa semana y sus variables derivadas; se remuestrean bloques completos con reemplazo conservando el pareo interno | FROZEN |
| `N_bootstrap_blocks_min` | `= 20` para cualquier población cuyo IC participe en una decisión; `N_bootstrap_blocks` = semanas ISO no vacías de la población específica; convención ex-ante conservadora de Etapa 1, no garantía universal | FROZEN |
| Comportamiento si bloques < 20 | Si `N_bootstrap_blocks < 20` → `bootstrap_evidence_insufficient = true`; ese IC no puede producir PASS de su gate | FROZEN |
| Aplicación del bootstrap a H1 | IC de `rescue_quality` y `productive_rescue_population_rate` por bloques semanales ISO; umbrales sin cambio (`LI95(rescue_quality)>0.50`, `LI95(productive_rescue_population_rate)>0`) | FROZEN |
| Aplicación del bootstrap a H2 | IC de `Cliff_delta_MFE` y `Cliff_delta_MAE` por bloques semanales ISO; gate `±0.147` sin cambio | FROZEN |
| Regla secuencial de ATR | Desde 1.50, pasos consecutivos; avanza solo si `evidence_sufficient` Y LI IC95 de `Q_j`>0.50 Y LI IC95 de `P_j`>0 | FROZEN |
| Caso primer escalón insuficiente | `1.50→1.75` con `evidence_sufficient=false` ⇒ H1 `evidence_insufficient`; no auto-selecciona 1.50; no gradúa por H1 | FROZEN |
| Caso escalón posterior insuficiente | Se detiene, conserva el último `atr_multiplier` justificado y documenta que el paso superior no pudo evaluarse | FROZEN |
| Gate de H2 | Aceptado vs. Rechazado; `Cliff_delta_MFE>=+0.147` Y `Cliff_delta_MAE<=−0.147`, ambos con IC95 que no cruza 0; operacionaliza `h2_delta` | FROZEN |
| H3-A | VolumeFilter debe pasar el gate Aceptado-vs-Rechazado `±0.147` | FROZEN |
| H3-B | Recomputar H1 completa por umbral sobre `Accepted(threshold)`; no reutilizar la H1 general | FROZEN |
| A en Validation | Baseline SMA siempre entra como CONTROL; no cuenta en Top-N | FROZEN |
| B en Validation | Ablación ATR; H1 sobre población SMA completa selecciona a lo sumo 1 B; si `evidence_insufficient`, ninguna B; no cuenta en Top-N de C | FROZEN |
| C / Top-N | A lo sumo 4 candidatas C (una por umbral que pase H3-A vía H3-B); `Top-N_C=3` aplica solo a C | FROZEN |
| Ranking de candidatas C | Lexicográfico sin puntaje ponderado, SIN métricas de H1: (1) mayor `h2_joint_effect` → (2) menor `h2_joint_ci_width` → (3) menor `h2_joint_censoring_rate` → (4) mayor `N_Accepted_observable` → (5) menor `volume_threshold_factor` solo como clave técnica ante igualdad exacta. `atr_multiplier` no participa. Anchos IC95 por block bootstrap semanal | FROZEN |
| Caso `selected_atr = 1.50` | Cubierto automáticamente: al retirar las métricas de H1 del ranking entre candidatas, ya no hay término indefinido cuando el ATR seleccionado es el punto de partida 1.50 (no proviene de ningún paso) | FROZEN |
| Separación de responsabilidades | H1 selecciona `atr_multiplier` DENTRO de cada población Aceptada; H2/H3-A ordena las candidatas C entre sí; las métricas marginales de H1 no se reutilizan para comparar umbrales | FROZEN |
| Desempate | Sin métricas de H1; `atr_multiplier` fuera del desempate; clave técnica final `volume_threshold_factor` solo por determinismo ante igualdad exacta; sin graduar extras en la posición N | FROZEN |
| Validation | Una sola ronda formal; segunda ronda requiere autorización explícita; selección usa solo Calibración | FROZEN |
| Final OOS | Sellado; se abre solo con configuración final congelada Y autorización explícita del usuario | FROZEN |

**Nota:** todas las decisiones anteriores están operacionalizadas y no existe bloqueador estructural. Esta tabla **no autoriza ejecutar la Calibración**; solo declara que el protocolo está listo.

CALIBRATION_PROTOCOL_READY = YES

---

### 10. Política de uso de Validación (conservadora)

- La Validación tiene inicialmente **UNA sola ronda formal** de evaluación de los candidatos graduados.
- Tras observar esos resultados: **toda decisión basada en Validación debe registrarse explícitamente** como `decision_informed_by_validation`; **no** se permiten cambios silenciosos de parámetros seguidos de re-consultar la Validación.
- Cualquier **segunda ronda de Validación requiere autorización explícita del usuario**; si se autoriza una iteración adicional, debe quedar registrado que la Validación **ya informó decisiones previas**.
- El **OOS final permanece completamente sellado** y no puede abrirse hasta que exista una **configuración final congelada Y** el usuario lo **autorice explícitamente**.

---

### 11. Trazabilidad append-only de experimentos

Cada corrida de configuración (más adelante) debe producir un **registro append-only** (nunca se sobrescribe) con, al menos:
- `dataset_id`;
- rango usado;
- `run_id` (identificador de la corrida);
- fecha/hora **UTC**;
- versión de código / *commit* si está disponible;
- configuración SMA;
- `atr_multiplier`;
- `volume_threshold_factor` (cuando aplique);
- versión del **protocolo experimental**;
- número de señales generadas;
- número de señales con ventana MFE/MAE completa;
- número de señales `signal_path_censored=true`;
- número descartadas por volumen (cuando aplique);
- métricas diagnósticas calculadas (MFE/MAE y normalizaciones);
- estado (`status`) de la corrida.

**Formato de persistencia mínimo propuesto (compatible con la restricción "sin base de datos", append-only y reproducible):** un archivo **JSONL** (JSON Lines — un objeto JSON por línea, solo-añadir), por ejemplo `data/experiments/calibration_runs.jsonl`, complementado por el `SignalJournal` ya existente para el detalle por señal.

- **NO** se implementa PostgreSQL.
- **NO** se ejecuta nada para probarlo ahora.

---

### Nota sobre el documento fuente de Requisitos

El documento fuente de Requisitos **YA está presente** en el workspace como **copia controlada de solo lectura**: `.kiro/reference/TradeCore_Requisitos_del_Software_REFERENCIA.md` (verificada como existente y legible). Reglas de uso:

- se trata como **fuente verificable de jerarquía ALTA** ("ALTA verificable");
- **no** se modifica (es de solo lectura);
- **no** se copia automáticamente todo su contenido al alcance actual;
- **VERIFICAR sí, re-activar no:** permite verificar el requisito histórico, no reintroducir su funcionalidad al alcance en vigor;
- el **spec activo `tradecore-etapa1`** determina qué está **en vigor** para la Etapa 1;
- se distingue entre requisitos del **diseño conceptual mayor** (verificables en la referencia) y requisitos **vigentes para la Etapa 1** (los del spec activo);
- si entra en **conflicto** con una decisión de Etapa 1 explícitamente aprobada, se **reporta el conflicto** en lugar de reconciliarlo o reintroducir funcionalidad histórica de forma autónoma.

Esta tarea **no** dependía de ese archivo para avanzar.

---

### Auditoría de evidencia — mecánica de evaluación económica de señales (jerarquía y estado)

Esta subsección audita **dónde está definida** (y con qué nivel de autoridad) cada pieza que haría falta para evaluar el **resultado económico** de una señal (acierto/fallo respecto a un objetivo, retorno, R/R), y **qué falta**. Esta matriz **sigue siendo válida y se conserva como referencia**.

**Reencuadre importante (alcance):** los elementos que aquí aparecen como "NO DEFINIDO" (fórmula de `Target_Puntos`, horizonte temporal, precedencia target/stop intrabar, etc.) **ya NO se tratan como "vacíos que obligatoriamente deben resolverse antes de la Etapa 1"**. Conforme al reencuadre de la sección "Propósito real de esta etapa", esos elementos están **FUERA DEL ALCANCE de la Calibración diagnóstica actual** (pertenecen al diseño conceptual mayor de TradeCore y/o al futuro Quant_Model), salvo que una etapa futura los requiera explícitamente. La Calibración de la Etapa 1 **NO** los necesita, porque mide comportamiento del precio con el marco **MFE/MAE** (Maximum Favorable Excursion / Maximum Adverse Excursion) de las secciones 4–7, que **no depende** de un `target`/take-profit ni de un horizonte fijo. Esta matriz queda entonces como **auditoría de por qué la evaluación económica sigue fuera de alcance**, no como una lista de bloqueos pendientes de la etapa actual.

#### (a) Hallazgo preliminar de jerarquía

- El **documento fuente** de Requisitos **AHORA está presente** como **referencia de solo lectura** en el workspace: `.kiro/reference/TradeCore_Requisitos_del_Software_REFERENCIA.md` (copia controlada, verificada como existente y legible). Por lo tanto su texto literal **SÍ es auditable dentro del repo**. Esto **eleva la jerarquía** de los requisitos que antes solo aportaba el usuario de palabra.
- En consecuencia, los **Requisitos 5, 6, 7, 4.2 y 4.3** (contenido de la señal, Backtesting, estados three-way del Live_Tracker, contradicción de horizonte) pasan de **"ALTA (citada por el usuario / no verificable en repo)"** a **"ALTA verificable"**: su texto fuente de jerarquía ALTA es ahora consultable en el documento de referencia.
- Los **Requisitos 4.6, 4.7, 5.2 y 5.5** siguen **citados textualmente** en `requirements.md` → jerarquía **ALTA, verificable en repo** (sin cambio).

**Reglas explícitas de uso del documento de referencia (importantes):**

- **(a) VERIFICAR, no re-activar:** el documento de referencia sirve para **VERIFICAR** el requisito histórico (confirmar qué decía la fuente de jerarquía ALTA), NO para reintroducir su funcionalidad al alcance en vigor.
- **(b) El spec ACTIVO manda:** el spec **activo `tradecore-etapa1`** determina **qué está EN VIGOR** para la Etapa 1. Que un requisito sea ahora "ALTA verificable" **no** lo convierte automáticamente en requisito vigente de la Etapa 1.
- **(c) Conflicto → REPORTAR:** si el documento de referencia y una decisión de Etapa 1 **explícitamente aprobada** entran en **conflicto**, se **REPORTA el conflicto** al usuario; no se resuelve por cuenta propia.
- **(d) No reconciliar por cuenta propia:** **NO** se reconcilia ni se reintroduce funcionalidad histórica de forma autónoma; cualquier reintroducción requiere pedido/aprobación explícita del usuario.

**Alcance de esta elevación:** convertir estos requisitos a "ALTA verificable" **NO** los convierte en requisitos **en vigor** de la Etapa 1. Sigue habiendo distinción entre requisitos del **diseño conceptual mayor** (verificables en la referencia) y requisitos **vigentes para la Etapa 1** (los del spec activo). Los estados **CONTRADICTORIO** y **NO DEFINIDO** se conservan tal como estaban: la contradicción de horizonte (#8) sigue **CONTRADICTORIO**; `Target_Puntos` (#5) sigue **NO DEFINIDO** para el alcance en vigor de la Etapa 1 — con la precisión de que ahora **es verificable** que el documento conceptual **SÍ define** el campo, solo que ese campo está **fuera del alcance** de la Etapa 1.
- Los términos `Target_Puntos`, `Precio_Entrada`, `Variación_Estimada`, `Horizonte`, `fulfilled`, `expired`, `stop_loss_triggered`, `pending_evaluation`, `take_profit`, `trailing`, `intrabar` tienen **CERO coincidencias en el repo** (excepto el texto del propio bloqueo ya presente en este `design.md`).

#### (b) Leyenda de jerarquía

- **ALTA:** documento fuente de Requisitos aprobado.
- **MEDIA:** spec/diseño posterior aprobado que **extiende sin contradecir** lo anterior.
- **BAJA:** propuestas / históricos / experimental **no aprobado**.
- **IMPLEMENTACIÓN ACTUAL:** lo que el **código hace hoy**, que **no se vuelve requisito** si contradice documentación de jerarquía superior.

#### (c) Matriz de auditoría

| # | Aspecto | Fuente | Jerarquía | Regla encontrada | Estado | Observación (conceptual vs implementado) |
|---|---------|--------|-----------|------------------|--------|------------------------------------------|
| 1 | Estructura general de salida | Req.7 (referencia) | ALTA verificable | three-way target/horizonte/stop | PARCIAL | estados definidos; implementado: nada en `src/`. |
| 2 | `fulfilled` | Req.7 (referencia) | ALTA verificable | precio alcanza objetivo en plazo | PARCIAL | depende de `Target_Puntos` (#5) y horizonte (#8); no implementado. |
| 3 | `expired` | Req.7 (referencia) | ALTA verificable | vence plazo sin objetivo | PARCIAL | depende del horizonte (#8) contradictorio; no implementado. |
| 4 | `stop_loss_triggered` | Req.7 (referencia) + ATRCalculator | ALTA verificable / IMPLEMENTACIÓN | precio supera el stop | PARCIAL | existe `stop_points` (distancia) pero no la regla "toca stop → cierra". |
| 5 | Fórmula de `Target_Puntos` | Req. (referencia) | — (fórmula) | el documento de referencia DEFINE el campo; no una fórmula operable para Etapa 1 | NO DEFINIDO (para Etapa 1) | ahora es **verificable** que el documento conceptual **define** `Target_Puntos`; sigue **fuera del alcance** y sin fórmula en vigor para la Etapa 1; no se asumió `Precio_Entrada × Variación_Estimada`. |
| 6 | Regla de `Precio_Entrada` | Req.5 (referencia) / código | ALTA verificable / IMPLEMENTACIÓN | campo existe; regla ausente | PARCIAL / NO DEFINIDO | `Signal.price = closes[-1]` (cierre de la vela del cruce) en `signal_engine.py`; no está aprobado que ese sea el precio de entrada de evaluación (¿`open` de la vela siguiente?). |
| 7 | Definición de Stop | ATRCalculator + RF-E1-01 | ALTA (en repo) / IMPLEMENTACIÓN | `Stop_Puntos = ATR × atr_multiplier` | DEFINIDO (distancia) / PARCIAL (como salida) | distancia definida e implementada; uso como evento de cierre no. |
| 8 | Horizonte temporal | Req.4.2 vs 4.3 (referencia) + MVP 1-min | ALTA verificable | 4.2 días; 4.3 1-10 barras de 5min; motor 1-min | CONTRADICTORIO | tres escalas incompatibles; la contradicción es ahora verificable en la referencia; sin fuente posterior aprobada que resuelva; no se reconcilia por suposición. |
| 9 | Relación `Variación_Estimada` → `Target_Puntos` | Req.4 (referencia) | ALTA verificable | menciona `Variación_Estimada` sin transformación | NO DEFINIDO | no se infiere fórmula. |
| 10 | Regla de acierto/fallo | Req.6 (referencia) | ALTA verificable | "señales que llegaron al objetivo" | PARCIAL | depende de #5 y #8; no calculable aún. |
| 11 | Regla de R/R | Req.6 + RF-E1-04 crit.3 | ALTA | debe calcularse ratio riesgo/beneficio | PARCIAL / NO DEFINIDO | R = stop (definido), beneficio = target (NO DEFINIDO) → no calculable. |
| 12 | Señales superpuestas/activas | — | — | ninguna fuente lo trata | NO DEFINIDO | no se define si se permite más de una señal activa. |
| 13 | Señales al final del dataset | Req.6 (`pending_evaluation`) (referencia) | ALTA verificable | marca `pending_evaluation` si no hay datos al vencimiento | PARCIAL | requiere horizonte (#8) contradictorio; no implementado. |
| 14 | Precedencia target/stop intrabar | — | — | ninguna fuente lo trata | FUERA DE ALCANCE | H1 solo requiere si el rango de la vela contiene el stop (BUY `low <= stop_price`; SELL `high >= stop_price`); no hay segundo evento terminal en competencia, por lo que no se necesita orden intra-minuto ni convención "stop primero" ahora; solo reaparece en una simulación futura con múltiples salidas simultáneas. |
| 15 | MAE/RMSE aplicables al motor SMA | RF-E1-04 crit.6 (en repo) | ALTA (en repo) | marcado [SEGURO]: aplicabilidad dudosa | CONTRADICTORIO / NO DEFINIDO | el motor SMA no produce predicción continua/`Variación_Estimada` sobre la que medir error. |

#### (d) Conflictos y vacíos restantes

**Conflictos** (fuentes o niveles que se contradicen entre sí):
- **Horizonte temporal (#8):** Req.4.2 (días) vs. Req.4.3 (1-10 barras de 5 min) vs. motor MVP de 1 min → tres escalas incompatibles.
- **MAE/RMSE (#15):** exigidas como métricas pero conceptualmente inaplicables a un motor SMA que no predice un valor continuo.
- **`Precio_Entrada` (#6):** el código usa `closes[-1]`, pero no hay aprobación de que ese sea el precio de entrada de evaluación (posible `open` de la vela siguiente).

**Elementos sin fuente que los defina** (impedirían una evaluación **económica** de resultado; **FUERA DEL ALCANCE** de la Calibración diagnóstica actual, no bloqueos de la etapa):
- Fórmula de `Target_Puntos`.
- Horizonte temporal aplicable al MVP.
- Regla de `Precio_Entrada` de evaluación (precio de ejecución).
- Precedencia target/stop intrabar — **FUERA DE ALCANCE actual** (ver corrección en "Dos precisiones conceptuales"): para H1 solo importa si el rango de la vela contiene el stop; no hay un segundo evento terminal en competencia, así que **no** se necesita orden intra-minuto ni se adopta convención "stop primero" ahora. Solo reaparece en una simulación futura con múltiples condiciones de salida simultáneas.
- Tratamiento de señales superpuestas/activas.

Estos elementos **NO se necesitan para la Calibración diagnóstica de la Etapa 1**: la caracterización del comportamiento del precio se hace con el marco **MFE/MAE** (secciones 4–7), que usa `reference_price` como ancla de medición (no como precio de ejecución) y el **cruce inverso** como horizonte endógeno. Responder a la evaluación **económica** (P&L, expectancy, R/R realizado) exige una **etapa futura distinta** con reglas de ejecución/salida explícitamente aprobadas.

---

## Hallazgos del código existente (verificados)

Antes de diseñar, se leyeron los componentes reales. Hechos relevantes:

- **`Signal`** (`src/schemas/signal.py`) es un `dataclass(frozen=True, slots=True)` con campos: `type`, `timestamp`, `price`, `sma_fast`, `sma_slow`. Al ser `frozen` y con `slots`, **no se le pueden agregar atributos** sin modificar el archivo (protegido). Implicación de diseño: la información nueva de Etapa 1 (Stop_Puntos, resultado del filtro de volumen, configuración, UUID) NO se añade a `Signal`; se modela en una estructura nueva que **envuelve** la `Signal` original.
- **`Candle`** (`src/schemas/candle.py`) ya expone `high`, `low`, `close`, `volume` — suficiente para calcular ATR y el promedio de volumen sin cambios.
- **`SignalEngine.evaluate(closes)`** (`src/engine/signal_engine.py`) recibe una lista de cierres y devuelve `Signal | None`. Es el motor protegido; NO se toca.
- **`app.py`** ejecuta un `_processing_loop()` que, por cada vela: append al buffer -> `get_closes()` -> `engine.evaluate()` -> `queue_candle` / `queue_sma_update` / `queue_signal`. Aquí es donde se insertan las capas nuevas, DESPUÉS de que el motor decide.
- **`ThrottledPusher`** serializa mensajes con un campo `type` (`candle`, `signal`, `sma`, `status`). Añadir fuentes/campos nuevos se puede hacer con tipos o campos adicionales sin romper el protocolo existente.

---

## Architecture

La Etapa 1 introduce módulos NUEVOS e independientes en paquetes existentes, más un orquestador de post-procesamiento de señal:

```
Vela completada (Candle)
   -> [existente] CandleBuffer.append + get_closes
   -> [existente, PROTEGIDO] SignalEngine.evaluate  -> Signal | None
   -> [NUEVO] SignalPipeline (orquestador de Etapa 1):

        Signal
          |
          v
        VolumeFilter
          |-- rechazada (low_volume) -> SignalRecord (stop_points=None,
          |                              position_size=None,
          |                              discard_reason='low_volume') -> Journal
          |
          `-- aceptada -> ATRCalculator (Stop_Puntos) -> RiskGate (contratos enteros)
                          |
                          |-- contratos == 0 (rechazada por tamano sub-1-contrato)
                          |     -> SignalRecord (position_size=None,
                          |        discard_reason='position_below_min_contract') -> Journal
                          |     (NO se envia al Dashboard)
                          |
                          `-- contratos >= 1 (operable)
                                -> SignalRecord (discard_reason=None) -> Journal
                                -> ThrottledPusher.queue_signal (+ metadatos de fuente)
```

El diagrama muestra **dos ramas de rechazo distintas**: (1) `low_volume` en el
VolumeFilter (una senal con volumen insuficiente nunca llega a ATR/RiskGate); y (2)
`position_below_min_contract` en el RiskGate (una senal puede pasar el filtro de volumen,
tener un ATR/stop valido y AUN ASI ser rechazada porque el tamano de posicion permitido
por el riesgo, tras convertir a contratos enteros, es 0 — es decir, menor que 1 contrato
operable). En ambos casos se produce un SignalRecord con su `discard_reason` y se registra
en el Journal; ninguna de las dos se envia al Dashboard.

**Ambos caminos producen un SignalRecord** con UUID, timestamp UTC y configuración matemática usada, cumpliendo el Requisito 4.6/5.5 extendido (RF-E1-03 crit. 1), independientemente de si la señal fue emitida o descartada. Solo las señales aceptadas se envían al Dashboard vía `queue_signal`; las rechazadas se registran en el Journal pero no se emiten.

Módulos nuevos propuestos (archivos nuevos, no protegidos):

- `src/engine/atr_calculator.py` — cálculo de ATR (Average True Range — indicador de volatilidad reciente).
- `src/engine/volume_filter.py` — filtro de volumen sobre señal ya generada.
- `src/engine/risk_gate.py` — cálculo de tamaño de posición (Risk_Gate).
- `src/engine/signal_pipeline.py` — orquestador que aplica el orden de RF-E1-01 criterio 6.
- `src/schemas/signal_record.py` — estructura que ENVUELVE `Signal` con datos de Etapa 1.
- `src/pipeline/signal_journal.py` — registro/persistencia ligera de señales (trazabilidad).
- `src/validation/` (paquete nuevo) — validaciones estadísticas A y B (RF-E1-04).
- `src/connectors/tradingview_webhook.py` — ingesta del webhook de TradingView (RF-E1-05, bloqueado por Fase B).

---

## Components and Interfaces

### 1. ATRCalculator (RF-E1-01)

**Responsabilidad:** calcular el ATR sobre las últimas N velas y derivar `Stop_Puntos`.

**Cálculo (suavizado RMA/Wilder — decisión propia de TradeCore, aprobada):**
- True Range (TR — rango verdadero de la vela) de cada vela = max( high-low, |high-close_prev|, |low-close_prev| ).
- ATR con suavizado RMA (Wilder's Moving Average — suavizado exponencial de Welles Wilder): recurrencia `ATR_t = (ATR_{t-1} * (N-1) + TR_t) / N`, donde N = `atr_period`. El valor inicial (seed) es la media simple de los primeros `atr_period` valores de TR.
- `Stop_Puntos = ATR_actual * atr_multiplier`.

**Método de suavizado del ATR — DECIDIDO (RMA/Wilder), de forma INDEPENDIENTE.** TradeCore usa RMA/Wilder como su método propio, tomado como decisión de diseño por sí misma y aprobada por el usuario. **NO es un hallazgo sobre el baseline** ni depende de su comportamiento. Distinto y separado: el mecanismo de stop del baseline Pine Script sigue DESCONOCIDO / `[SEGURO]` (ver punto 10(a) de la lista consolidada) y bloquea únicamente la Validación B (Tarea 9), NO la calibración propia de TradeCore (Tarea 1).

**Parámetros:**
- `atr_period`: **valor por defecto 14** (estándar de industria, Welles Wilder). Configurable.
- `atr_multiplier`: **configurable, SIN valor por defecto definitivo.** Rango de referencia de industria documentado: **1.5x a 3x**. El valor final se fija en la Tarea de Calibración (Tarea 1).

**Datos insuficientes (RF-E1-01 crit. 4):** si hay menos de `atr_period + 1` velas, `stop_points()` devuelve `None`; el orquestador registra el motivo (`insufficient_history_atr`) y omite el cálculo de tamaño de posición, sin alterar la señal.

**Interfaz:**
```
class ATRCalculator:
    def __init__(self, period: int = 14): ...
    def atr(self, candles: list[Candle]) -> float | None
    def stop_points(self, candles: list[Candle], multiplier: float) -> float | None
```

**Frontera:** consume `Candle` (ya tiene high/low/close). No toca el motor ni el buffer.

### 2. VolumeFilter (RF-E1-02)

**Responsabilidad:** aceptar o rechazar una señal YA generada según el volumen de su vela frente al promedio de N períodos. Nunca cambia la dirección.

**Cálculo:**
- `avg_volume = promedio del volumen de las últimas volume_period velas`.
- La señal se acepta si `volumen_vela >= volume_threshold_factor * avg_volume`; si no, se rechaza con motivo `low_volume`.

**Parámetros:**
- `volume_period` (N): **valor por defecto 20** (estándar de industria). Configurable.
- `volume_threshold_factor`: **configurable, SIN valor por defecto definitivo.** Rango de referencia documentado: **0.5x a 0.8x** del volumen promedio. Valor final en la Tarea de Calibración (Tarea 1).

**Patrón de descarte:** homólogo a `low_confidence` del Requisito 5.2 — se descarta sin emitir y se registra el motivo `low_volume` en el journal (RF-E1-02 crit. 3). Aun descartada, se produce un SignalRecord (ver diagrama en Architecture).

**Interfaz:**
```
class VolumeFilter:
    def __init__(self, period: int = 20): ...
    def accept(self, candle: Candle, recent_candles: list[Candle], threshold_factor: float) -> tuple[bool, str | None]
```

### 3. RiskGate (RF-E1-01 crit. 5-7)

**Responsabilidad:** calcular el tamaño de posición operable (en **contratos enteros**) a partir de `Stop_Puntos`, y señalar cuándo la señal no es operable mediante su mecanismo de motivo (`reason`).

**Fórmula (tamaño teórico):** `tamano_teorico = equity_disponible * riesgo_pct / (stop_pts * valor_por_punto)`.

**Conversión a contratos enteros — DECISIÓN CONFIRMADA por Hugo:**
- **Tamaño teórico** (theoretical size): el resultado directo de la fórmula (puede ser fraccionario, ej. `0.5` contratos).
- **Tamaño operable** (operable size): `floor(tamano_teorico)` — el mayor número entero de contratos que NO supera el tamaño teórico. `position_size()` devuelve ESE entero.
- El uso de `floor` (redondeo hacia abajo, nunca hacia arriba) garantiza que el riesgo real (`contratos * stop_pts * valor_por_punto`) **nunca supere** `equity_disponible * riesgo_pct`. Redondear hacia arriba violaría el tope de riesgo.
- Si `floor(tamano_teorico) == 0` → la señal **no es operable** (el tamaño permitido por el riesgo es menor que 1 contrato). `position_size()` devuelve `None` y expone el motivo `position_below_min_contract` (mismo mecanismo `reason` que `stop_unavailable`). Un tamaño sub-1-contrato NO puede redondearse hacia arriba sin romper el tope de riesgo, por eso la señal se descarta en lugar de forzar 1 contrato.
- **Comportamiento seguro previo (sin cambios):** si `stop_pts` es 0 o `None`, NO se ejecuta la división (evita división por cero); se registra el motivo `stop_unavailable` y `position_size()` devuelve `None`.

**Separación de responsabilidades (importante):** `position_size()` calcula el entero de contratos permitido y comunica la no-operabilidad únicamente a través de su `reason`. **NO** contiene lógica de orquestación del SignalPipeline. La decisión de emitir un `SignalRecord` descartado completo (con UUID, timestamp, configuración) pertenece a la capa RiskGate/SignalPipeline conforme a la arquitectura aprobada; no se empuja lógica de SignalPipeline dentro de la función de cálculo.

**Parámetros — todos [SEGURO] (hechos/decisiones externas, NO técnicos):**
- `valor_por_punto`: **[SEGURO]** — hecho fijo del contrato, NO se asume ni promedia. NQ estándar = 20 USD/punto; Micro NQ (MNQ) = 2 USD/punto. Pendiente de que Hugo confirme qué contrato se usa en producción.
- `riesgo_pct`: **[SEGURO]** — decisión de gestión de riesgo del operador. Referencia de industria: 1-2% del capital por operación. Pendiente de decisión de Hugo.
- `equity_disponible`: **[SEGURO]** — depende del capital real de operación. Pendiente de dato de Hugo.

**Interfaz:**
```
class RiskGate:
    # Devuelve el numero ENTERO de contratos operables (int) o None si la senal no es
    # operable. Cuando devuelve None, expone el motivo en `self.reason`:
    #   - 'stop_unavailable'            -> stop_pts es None o 0 (no se divide)
    #   - 'position_below_min_contract' -> floor(tamano_teorico) == 0 (sub-1-contrato)
    def position_size(self, stop_pts: float | None, equity: float, risk_pct: float, value_per_point: float) -> int | None
```

### 4. SignalRecord (RF-E1-03) — envoltura, NO modificación de Signal

Como `Signal` es `frozen`/`slots`, se define una estructura nueva que lo envuelve:

```
@dataclass(frozen=True)
class SignalRecord:
    signal: Signal                 # la Signal original, intacta
    uuid: str                      # identificador unico universal
    candle_timestamp_utc: str      # ISO 8601 UTC de la vela origen
    atr_period: int
    atr_multiplier: float
    volume_period: int
    volume_threshold_factor: float
    stop_points: float | None
    volume_accepted: bool
    discard_reason: str | None     # 'low_volume', 'insufficient_history_atr', 'stop_unavailable', 'position_below_min_contract', None
    position_size: int | None      # contratos enteros operables (floor); None si no operable
```

**Trazabilidad (crit. 1-4):** el `SignalRecord` contiene toda la configuración matemática que produjo la señal, su UUID (identificador único universal) y el timestamp UTC de la vela. Esto permite reproducir el resultado dado el mismo input. Se genera **tanto para señales emitidas como descartadas** (RF-E1-03 crit. 1).

### 5. SignalJournal (RF-E1-03 crit. 5) — persistencia ligera

**[SEGURO] — Tensión persistencia vs. "sin base de datos".** El diseño propone (pendiente de confirmación) **persistencia ligera en archivos de log estructurados** (por ejemplo, JSON Lines: un registro JSON por línea), NO una base de datos. Se ubicaría en un directorio de datos configurable. Esta es la orientación no vinculante que el usuario ya anticipó; la decisión final se confirma antes de implementar la Tarea correspondiente. Alternativas consideradas: (a) solo log en memoria (se pierde al reiniciar — insuficiente para reproducibilidad histórica); (b) archivos JSONL (elegido tentativamente por simplicidad y reproducibilidad sin BD); (c) SQLite (descartado por la restricción "sin base de datos").

### 6. SignalPipeline — orquestador del orden (RF-E1-01 crit. 6)

Aplica exactamente el orden aprobado: señal -> filtro de volumen -> (si aceptada) ATR/Stop -> RiskGate -> SignalRecord -> Journal. Una señal rechazada por volumen NO llega a ATR ni RiskGate, pero SÍ genera su propio SignalRecord con `discard_reason='low_volume'` y se registra en el Journal (ver diagrama bifurcado en Architecture).

**[REQUIERE APROBACIÓN] Punto de integración en `app.py` (protegido):** el `_processing_loop()` actual llama directamente a `_pusher.queue_signal(signal)` cuando el motor emite señal. Para insertar el SignalPipeline entre el motor y el pusher, hay que modificar ese punto del archivo protegido `src/api/app.py`. **Modificación mínima propuesta:** reemplazar la llamada directa `queue_signal(signal)` por: pasar la `signal` por `SignalPipeline.process(...)`, y solo si el resultado es "aceptada", llamar `queue_signal`. No se cambia nada más del loop. Esto NO altera el motor BUY/SELL ni el protocolo; solo intercala el filtro/registro. Requiere aprobación explícita antes de implementarse.

### 7. Validación estadística (RF-E1-04) — paquete `src/validation/`

- **Validación A** (`validator_oos.py`): out-of-sample vs tasa base, +5 puntos porcentuales, según Requisito 4.7. No compara contra TradingView.
- **Validación B** (`validator_baseline.py`): TradeCore vs baseline Pine Script, mismas métricas (tasa de aciertos, ratio riesgo/beneficio, MAE, RMSE) con idéntica metodología para ambas fuentes.
- **Ventana configurable y reproducible:** fecha inicio, fecha fin, resolución, criterio de inclusión — como parámetros, nunca hardcodeados.
- **[SEGURO] Baseline no documentado:** toda la lógica de Validación B que dependa del baseline queda pendiente de confirmación, incluida la aplicabilidad de MAE/RMSE a un sistema que solo emite BUY/SELL con target y stop (no predicción continua). No se implementa hasta confirmar.

### 8. Comparación visual (RF-E1-05) — BLOQUEADO por Fase B

- **`tradingview_webhook.py`:** endpoint que recibe el webhook, normaliza el timestamp a **ISO 8601 UTC** en la ingesta, y encola la señal externa. Debe aceptar y convertir correctamente los tres formatos de timestamp de origen: (a) Unix timestamp en **segundos**; (b) Unix timestamp en **milisegundos**; (c) timestamps **ISO 8601 con offset de zona horaria** (por ejemplo `-05:00`). Los tres se normalizan a ISO 8601 UTC en el punto de ingesta.
- **ThrottledPusher:** se extiende con un campo `source` (`tradecore` | `tradingview`) en los mensajes de señal, o un nuevo `type` `signal_external`, sin romper el protocolo existente. Decisión de detalle a fijar en Tasks.
- **[SEGURO]** formato/autenticación del webhook y **[SEGURO]** tolerancia de alineación de velas entre feeds — pendientes.
- **Estado:** este componente NO avanza a implementación hasta que Fase B (Tareas 12.1-12.3) esté completa y validada (RF-E1-06).

### 9. Refactor del Dashboard (RF-E1-07)

- Extraer todo el CSS embebido de `dashboard/index.html` a `dashboard/styles.css`.
- Extraer todo el JavaScript embebido a `dashboard/app.js`.
- `index.html` queda solo con estructura + `<link>` y `<script src>`.
- Margen exterior de 8px en el contenedor (los 4 lados).
- Comportamiento observable idéntico: WebSocket, velas y marcadores sin cambios de lógica.

**[REQUIERE APROBACIÓN] `dashboard/index.html` está protegido (Nivel 1).** El refactor mueve código sin cambiar comportamiento; requiere aprobación explícita antes de ejecutarse.

---

## Data Models

| Estructura | Nueva/Existente | Notas |
|---|---|---|
| `Candle` | Existente, intacta | ya tiene OHLCV suficiente |
| `Signal` | Existente, intacta (frozen) | NO se modifica; se envuelve |
| `SignalRecord` | Nueva | envuelve `Signal` + datos Etapa 1; se genera para señales emitidas Y descartadas |
| Config Etapa 1 | Nueva | parámetros ATR/volumen/RiskGate/ventana |

---

## Error Handling

Casos de manejo de error/descarte, todos registrados en el SignalJournal con su motivo y sin afectar la decisión BUY/SELL del motor:

- **`insufficient_history_atr`** (RF-E1-01 crit. 4): menos de `atr_period + 1` velas disponibles para calcular ATR. `stop_points()` devuelve `None`; se registra el motivo y se omite el cálculo de tamaño de posición. La señal, si fue aceptada por volumen, se registra igualmente con `stop_points=None`.
- **`stop_unavailable`** (RF-E1-01 crit. 7): `stop_pts` es 0 o `None`. El RiskGate NO ejecuta la división (evita división por cero); se registra el motivo y no se produce `position_size`.
- **`position_below_min_contract`** (RF-E1-01 crit. 5-7): el tamaño de posición permitido por el riesgo (floor del tamaño teórico) es 0 contratos, es decir, menor que 1 contrato operable. La señal no es operable; el RiskGate devuelve `None` con este motivo y la señal se registra como descartada con `discard_reason='position_below_min_contract'`. Justificación: nunca se autoriza un riesgo superior a `equity_disponible * riesgo_pct`; un tamaño sub-1-contrato no puede redondearse hacia arriba sin romper ese tope.
- **`low_volume`** (RF-E1-02 crit. 3): el volumen de la vela está por debajo del umbral. La señal se descarta sin emitirse, pero SÍ se produce un SignalRecord con `discard_reason='low_volume'` y se registra en el Journal.
- **Webhook de TradingView (RF-E1-05):** timestamps en formato no reconocido o inconvertibles se registran como error de ingesta; la señal externa no se muestra hasta normalizarse. (Componente bloqueado por Fase B.)

En todos los casos, el motor SMA que decide BUY/SELL permanece intacto: estos manejos ocurren en las capas nuevas, después de la decisión.

---

## Testing Strategy

Se añaden tests NUEVOS (no se tocan los existentes, que están protegidos como evidencia de Fase A):

- **ATR:** propiedad — para series constantes el ATR es 0; el ATR nunca es negativo; con datos insuficientes devuelve `None`.
- **ATR (rango nulo):** dado un conjunto de velas donde High == Low == Close (sin rango de movimiento), el ATR resultante debe ser exactamente 0.
- **VolumeFilter:** propiedad — la dirección de la señal nunca cambia; una señal con volumen bajo el umbral siempre se rechaza con `low_volume`; volumen suficiente siempre pasa. Property: `direccion_entrada == direccion_salida` para toda señal.
- **RiskGate:** propiedad — `stop_pts=0` o `None` nunca produce excepción de división (devuelve `None` con `stop_unavailable`); el tamaño escala inversamente con `stop_pts`; la conversión por `floor` devuelve un número entero de contratos; si el `floor` del tamaño teórico es 0 la señal no es operable (`None` con `position_below_min_contract`); y para todo tamaño autorizado (entero N ≥ 1) el riesgo real `N * stop_pts * valor_por_punto` nunca supera `equity * riesgo_pct`.
- **SignalPipeline (orden):** propiedad — una señal rechazada por volumen nunca invoca ATR ni RiskGate; el motor BUY/SELL produce la misma dirección con y sin las capas de Etapa 1 (no regresión del motor).
- **Trazabilidad:** propiedad — dado un `SignalRecord` y las mismas velas, la reproducción da idéntico resultado (misma dirección, mismo stop, misma decisión de filtro). Tanto señales emitidas como descartadas producen SignalRecord.

---

## Correctness Properties

Propiedades formales que la implementación de Etapa 1 debe satisfacer (verificables por Property-Based Testing):

### Property 1: No regresión del motor

para toda secuencia de cierres, la dirección BUY/SELL producida por el motor es idéntica con y sin las capas de Etapa 1. Las capas nunca alteran la decisión.

**Validates: Requirements 1.6, 2.5**
_(RF-E1-01, RF-E1-02)_

### Property 2: Filtro no cambia dirección

para toda señal, `direccion_entrada == direccion_salida` del VolumeFilter; el filtro solo acepta o rechaza.

**Validates: Requirements 2.5**
_(RF-E1-02)_

### Property 3: ATR no negativo y cero en rango nulo

el ATR nunca es negativo; si High == Low == Close en todas las velas del período, el ATR es exactamente 0.

Nota: el criterio 4 de RF-E1-01 (historia insuficiente para el ATR -> devuelve None con motivo `insufficient_history_atr`) no tiene una Property formal dedicada en esta lista; su cobertura existe a nivel de test (Tarea 2.1 y la Testing Strategy), por lo que el comportamiento queda verificado aunque no como Property independiente.

**Validates: Requirements 1.3**
_(RF-E1-01)_

### Property 4: RiskGate — sin división por cero, conversión a contratos enteros y tope de riesgo

`stop_pts` igual a 0 o `None` nunca produce excepción; el RiskGate devuelve `None` y registra `stop_unavailable`.

Extensión (conversión de contratos, decisión confirmada): la conversión por `floor` del tamaño teórico devuelve un número ENTERO de contratos. Si `floor(tamano_teorico) == 0`, la señal no es operable: `position_size()` devuelve `None` y registra `position_below_min_contract`. Además, para toda entrada positiva válida en la que `position_size()` devuelve un entero N ≥ 1, se cumple el **tope de riesgo**: `N * stop_pts * valor_por_punto <= equity * riesgo_pct` (con tolerancia de punto flotante). Cuando devuelve `None` (no operable) la propiedad se satisface trivialmente.

**Validates: Requirements 1.7**
_(RF-E1-01)_

### Property 5: Orden del pipeline

una señal rechazada por volumen nunca invoca ATR ni RiskGate.

**Validates: Requirements 1.6, 2.5**
_(RF-E1-01, RF-E1-02)_

### Property 6: Trazabilidad completa

toda señal, emitida o descartada, produce exactamente un SignalRecord con UUID, timestamp UTC y configuración matemática usada.

**Validates: Requirements 3.1**
_(RF-E1-03)_

### Property 7: Reproducibilidad

dado un SignalRecord y las mismas velas de entrada, la reproducción da idéntico resultado (dirección, stop, decisión de filtro).

**Validates: Requirements 3.4, 4.5**
_(RF-E1-03, RF-E1-04)_

---

## Lista consolidada de [SEGURO] en Diseño

1. `atr_multiplier` — configurable, sin default; rango de referencia 1.5x-3x; valor final en Tarea 1 (Calibración).
2. `volume_threshold_factor` — configurable, sin default; rango de referencia 0.5x-0.8x; valor final en Tarea 1.
3. `valor_por_punto` — hecho de contrato (NQ=20 USD/pt, MNQ=2 USD/pt); pendiente de confirmación de Hugo.
4. `riesgo_pct` — decisión del operador; referencia 1-2%; pendiente de Hugo.
5. `equity_disponible` — capital real; pendiente de Hugo.
6. Persistencia ligera (JSONL) para el SignalJournal — orientación propuesta, confirmación final antes de implementar.
7. Baseline Pine Script (comportamiento, parámetros, aplicabilidad de MAE/RMSE) — pendiente.
8. Formato/autenticación del webhook de TradingView — pendiente.
9. Tolerancia de alineación de velas entre feeds distintos — pendiente.
10. **Mecanismo de stop del baseline Pine Script vs. método de suavizado del ATR propio de TradeCore** — dos cosas distintas que NO deben confundirse:

   (a) **Mecanismo de stop del baseline — SIGUE DESCONOCIDO / `[SEGURO]`.** La investigación de la Tarea 0 NO encontró evidencia del código fuente del Pine Script "NQ Hybrid v11 - Sustainable Edge" en el workspace, por lo que su mecanismo de stop (si usa ATR o stop fijo, y con qué método) NO está confirmado. Este punto NO está resuelto: permanece `[SEGURO]` y bloquea EXCLUSIVAMENTE la Validación B (Tarea 9 — comparación formal contra el baseline externo). No se asume ni se infiere nada sobre su comportamiento.

   (b) **Método de suavizado del ATR propio de TradeCore — DECIDIDO de forma INDEPENDIENTE: RMA/Wilder** (RMA — Wilder's Moving Average, suavizado exponencial de Welles Wilder). La recurrencia es `ATR_t = (ATR_{t-1} * (N-1) + TR_t) / N` (TR — True Range, rango verdadero de la vela; N — `atr_period`), sembrada (seed inicial) con la media simple de los primeros `period` valores de TR. Esta es una decisión de diseño PROPIA de TradeCore, tomada por sí misma; **NO es un hallazgo sobre el baseline** y **NO depende ni asume** el comportamiento del baseline. Por tanto, la calibración de TradeCore (Tarea 1) puede ejecutarse con este método sin esperar a que se confirme el mecanismo del baseline; lo único que sigue bloqueado por (a) es la Validación B.

**Valores/decisiones YA fijados (no [SEGURO]):** `atr_period = 14`, `volume_period = 20` — configurables, con estos valores iniciales. **Método de suavizado del ATR de TradeCore = RMA/Wilder** — decisión propia e independiente ya aprobada (ver punto 10(b)); no confundir con el mecanismo de stop del baseline, que sí sigue `[SEGURO]` (punto 10(a)).

---

## Puntos que requieren aprobación de componente protegido

1. **`src/api/app.py`** — intercalar `SignalPipeline` entre motor y pusher (modificación mínima en `_processing_loop`). [REQUIERE APROBACIÓN]
2. **`dashboard/index.html`** — refactor a CSS/JS externos + margen 8px (RF-E1-07). [REQUIERE APROBACIÓN]
3. **`src/api/throttled_pusher.py`** — agregar campo `source`/tipo para señales externas (solo para RF-E1-05, que además está bloqueado por Fase B). [REQUIERE APROBACIÓN]

Ningún test existente se modifica. Cualquier cambio aprobado a estos archivos se acompañará de la ejecución de la suite de tests relacionada, conforme al principio de protección de componentes.

---

## Nota sobre la Tarea de Calibración

Conforme a la instrucción de proceso, el documento de Tasks incluirá como **Tarea 1** (antes de cualquier implementación dependiente de esos valores) una "Calibración inicial de parámetros Etapa 1": un backtest exploratorio sobre datos históricos de NQ para proponer valores concretos de `atr_multiplier` (rango 1.5x-3x) y `volume_threshold_factor` (rango 0.5x-0.8x), dentro de los rangos de referencia documentados. **Método de suavizado del ATR — ya definido:** el método propio de TradeCore es RMA/Wilder (ver punto 10(b) de la lista `[SEGURO]`), decidido de forma independiente y aprobado; por tanto el prerrequisito de método de ATR para la calibración PROPIA de TradeCore ya está satisfecho y la Tarea 1 no está bloqueada por él. Esto NO resuelve el mecanismo de stop del baseline (punto 10(a)), que sigue `[SEGURO]` y bloquea únicamente la Validación B (Tarea 9). **Prerrequisito de dependencia real de la Tarea 1:** que los módulos productivos ATRCalculator (Tarea 2), VolumeFilter (Tarea 3) y SignalRecord/Journal (Tarea 4) estén implementados y probados (2.1, 3.1, 4.1). Ninguna tarea de implementación que dependa de los valores calibrados debe ejecutarse antes de la calibración.