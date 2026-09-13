# Requirements Document

## Introduction

Este documento define los Requisitos de la **Etapa 1** de TradeCore: una mejora matemática sobre el pipeline (cadena de procesamiento de datos) ya validado en Fase A. La Etapa 1 agrega tres capacidades matemáticas encima de lo existente, más una comparación formal contra un baseline (versión de referencia contra la que se mide el rendimiento) externo y una reorganización técnica del Dashboard (panel de control visual accesible desde el navegador).

**Principio rector de toda la Etapa 1:** el motor de generación de señales BUY/SELL (compra/venta) existente **no cambia**. El ATR (Average True Range — indicador que mide la volatilidad reciente del precio) modifica únicamente el cálculo del stop (nivel de precio donde se cierra una operación para limitar pérdidas). El filtro de volumen (volumen = cantidad negociada en un período) actúa únicamente como criterio de aceptación o rechazo de una señal **ya generada**. Ningún criterio de aceptación de esta etapa altera la lógica que decide si una señal es BUY o SELL.

**Convención `[SEGURO]`:** todo dato o supuesto que el equipo debe confirmar con evidencia antes de avanzar se marca como `[SEGURO]`. No se asume ni se inventa información marcada así.

---

## Glossary

- **ATR** (Average True Range — indicador que mide la volatilidad reciente del precio, es decir, cuánto se mueve el precio en promedio).
- **Stop / Stop-loss** (nivel de precio donde una operación se cierra automáticamente para limitar la pérdida).
- **Stop_Puntos** (distancia del stop expresada en puntos del instrumento, derivada del ATR en esta etapa).
- **Risk_Gate** (módulo que calcula el tamaño de la posición —cuántos lotes operar— en función del riesgo permitido y la distancia del stop).
- **Filtro de volumen** (criterio que rechaza señales generadas cuando la participación del mercado es anómalamente baja).
- **Baseline** (versión de referencia contra la que se compara el rendimiento; aquí, el script Pine Script "NQ Hybrid v11 - Sustainable Edge").
- **Pine Script** (lenguaje de programación de TradingView para crear indicadores y estrategias).
- **TradingView** (plataforma externa de gráficos y estrategias de trading).
- **webhook** (mensaje automático que un sistema externo —TradingView— envía a TradeCore cuando ocurre un evento).
- **out-of-sample** (datos que el modelo no usó para entrenarse, usados para validar sin sesgo).
- **tasa base** (porcentaje de barras donde la dirección correcta era simplemente la tendencia dominante del período).
- **MAE** (Error Absoluto Medio — promedio de cuánto se equivoca una estimación).
- **RMSE** (Raíz del Error Cuadrático Medio — penaliza más los errores grandes que el MAE).
- **UTC** (Coordinated Universal Time — zona horaria universal de referencia).
- **ISO 8601** (formato estándar internacional para representar fechas y horas, por ejemplo `2025-01-15T14:30:00Z`).
- **Unix timestamp** (forma de representar un instante como la cantidad de segundos transcurridos desde el 1 de enero de 1970 UTC).
- **UUID** (identificador único universal — código que identifica de forma exclusiva cada registro).
- **Feature_Store** (almacén de indicadores en memoria; NO es una base de datos en este proyecto).
- **Signal_Engine** (motor de reglas que decide si una señal se emite o se descarta).

---

## Requisitos fuente citados (trazabilidad)

Los siguientes requisitos provienen del documento `TradeCore_Requisitos_del_Software.docx` (fase de diseño previa) y se citan textualmente como fuente de trazabilidad. La Etapa 1 los EXTIENDE; no los reescribe.

> **Requisito 4.6:** El Quant_Model debe registrar en el sistema de registro de eventos (log) cada predicción realizada: los datos de entrada usados, los resultados obtenidos, la fecha y hora, y la versión del modelo. Esto garantiza que cualquier señal pueda ser rastreada y auditada.

> **Requisito 4.7:** Si la Probabilidad_de_Exito calculada para una predicción es inferior al umbral configurable (valor por defecto: 60 %), el Quant_Model debe marcar la predicción como `low_confidence` (baja confianza) y el Signal_Engine la descartará sin emitir señal. La Probabilidad_de_Exito debe ser validada sobre muestra out-of-sample. El umbral del 60% se considera superado únicamente si la tasa de acierto out-of-sample supera en al menos 5 puntos porcentuales la tasa base del período.

> **Requisito 5.2:** Si la predicción está marcada como `low_confidence`, el Signal_Engine debe descartarla sin emitir señal y registrar el descarte en el sistema de eventos con el motivo `low_confidence`.

> **Requisito 5.5:** El Signal_Engine debe guardar cada señal emitida en el Feature_Store con un identificador único UUID para su seguimiento posterior por el Live_Tracker y el Backtesting_Module.

---

## Requirements

---

### RF-E1-01: Stop dinámico basado en ATR

**Objetivo del Desarrollo:** Reemplazar el stop-loss fijo del baseline Pine Script ("NQ Hybrid v11") por un stop dinámico calculado en función del ATR (Average True Range — indicador que mide la volatilidad reciente del precio), de modo que la distancia del stop se adapte a la volatilidad del mercado. Este requisito modifica **únicamente** el cálculo del stop; no altera la lógica que decide si una señal es BUY (compra) o SELL (venta).

**Criterios de aceptación:**

1. El sistema debe permitir configurar el período del ATR (cantidad de velas usadas para calcular la volatilidad promedio) mediante un parámetro configurable, con un valor por defecto marcado como `[SEGURO]` (pendiente de confirmar contra el baseline).
2. El sistema debe permitir configurar el multiplicador del ATR (factor por el que se multiplica el ATR para obtener la distancia del stop) mediante un parámetro configurable, con un valor por defecto marcado como `[SEGURO]`.
3. Cuando una señal ha sido aceptada por el filtro de volumen (ver RF-E1-02), el sistema debe calcular la distancia del stop en puntos (`Stop_Puntos` — distancia del stop expresada en puntos del instrumento) como `Stop_Puntos = ATR_actual * multiplicador`.
4. Cuando el sistema no dispone de suficientes velas completadas para calcular el ATR (por ejemplo, al arrancar con historia insuficiente), no debe producir un `Stop_Puntos` basado en ATR; en su lugar debe registrar el motivo en el sistema de eventos (log) y omitir el cálculo de tamaño de posición para esa señal, sin alterar la decisión BUY/SELL.
5. El cálculo del tamaño de posición corresponde al Risk_Gate (módulo que calcula el tamaño de la posición) con la fórmula `lotes = equity_disponible * riesgo_pct / (stop_pts * valor_por_punto)`, donde `equity_disponible` (capital disponible para operar), `riesgo_pct` (porcentaje del capital que se arriesga por operación) y `valor_por_punto` (valor monetario de cada punto del instrumento) son parámetros de configuración marcados como `[SEGURO]`. Este cálculo consume el `Stop_Puntos` derivado del ATR y ocurre en el orden definido en el criterio 6.
6. **Orden completo de procesamiento de una señal.** El orden completo de procesamiento de una señal es: (1) el Signal_Engine (motor de reglas que decide si una señal se emite) genera la señal BUY/SELL con la lógica existente sin cambios; (2) el filtro de volumen (RF-E1-02) acepta o rechaza la señal; (3) solo si la señal fue aceptada, se calcula `Stop_Puntos` a partir del ATR; (4) el Risk_Gate calcula el tamaño de posición. Una señal rechazada por el filtro de volumen no debe llegar al cálculo de ATR ni de Risk_Gate.
7. Si `Stop_Puntos` es cero o no está disponible, el Risk_Gate no debe ejecutar la división de la fórmula de lotes (para evitar una división por cero) y debe registrar el motivo en el sistema de eventos, sin afectar la generación de la señal.

**Nota de frontera (no regresión):** este requisito no modifica el motor BUY/SELL. El ATR y el Risk_Gate consumen una señal ya decidida y ya aceptada por el filtro de volumen.

---

### RF-E1-02: Filtro de volumen sobre señales ya generadas

**Objetivo del Desarrollo:** Añadir un criterio de filtrado que descarte señales generadas en condiciones de volumen (cantidad negociada en un período) anómalamente bajo, ya que poca participación del mercado implica una señal menos confiable. El filtro actúa **después** de que la señal BUY/SELL fue generada y **antes** del cálculo de ATR y Risk_Gate: acepta o rechaza, pero nunca cambia la dirección de la señal.

**Criterios de aceptación:**

1. El sistema debe calcular un umbral de volumen de referencia comparando el volumen de la vela de la señal contra el volumen promedio de los últimos N períodos (cantidad de velas), donde N es configurable con valor por defecto marcado como `[SEGURO]`.
2. El sistema debe permitir configurar el criterio del umbral (por ejemplo, un factor mínimo respecto al promedio, como "el volumen debe ser al menos X veces el promedio") mediante parámetro configurable, con valor por defecto marcado como `[SEGURO]`.
3. Si una señal ya generada corresponde a una vela cuyo volumen está por debajo del umbral de referencia, el sistema debe descartar esa señal sin emitirla y registrar el descarte en el sistema de eventos (log) con el motivo `low_volume` (volumen bajo), siguiendo el mismo patrón de descarte con motivo definido en el Requisito 5.2 (que descarta con motivo `low_confidence`).
4. Si el volumen de la vela cumple o supera el umbral de referencia, el sistema debe permitir que la señal continúe su flujo normal hacia el cálculo de ATR y Risk_Gate (ver orden completo en RF-E1-01, criterio 6), sin modificación de su dirección.
5. El filtro de volumen no debe ejecutarse antes de la generación de la señal ni influir en la decisión BUY/SELL; solo se aplica como aceptación o rechazo posterior, ubicado en el paso (2) del orden completo definido en RF-E1-01, criterio 6.

**Nota de frontera (no regresión):** el filtro es un criterio de aceptación/rechazo posterior, homólogo al descarte por `low_confidence` del Requisito 5.2, pero basado en volumen en lugar de probabilidad. Se ubica entre la generación de la señal y el cálculo de ATR/Risk_Gate, de modo que una señal rechazada no consume cálculo de stop ni de tamaño de posición.

---

### RF-E1-03: Trazabilidad de configuración matemática por señal

**Objetivo del Desarrollo:** Garantizar que cada señal generada bajo la Etapa 1 registre exactamente qué configuración matemática la produjo, de forma que cualquier señal histórica pueda reproducirse de manera idéntica. Esto extiende el principio de trazabilidad ya exigido en los Requisitos 4.6 (registro de cada predicción con sus entradas y versión) y 5.5 (guardado de cada señal con un UUID —identificador único universal—).

**Criterios de aceptación:**

1. Cuando el sistema emite o descarta una señal bajo la Etapa 1, debe registrar en el sistema de eventos (log) la configuración matemática usada: período del ATR, multiplicador del ATR, N períodos del filtro de volumen y criterio del umbral de volumen.
2. Cada señal registrada debe llevar asociado un identificador único UUID (código universal único que identifica de forma exclusiva cada registro), en coherencia con el Requisito 5.5.
3. El registro de cada señal debe incluir la marca de tiempo en UTC (Coordinated Universal Time — zona horaria universal) de la vela que originó la señal, en coherencia con el registro de fecha y hora del Requisito 4.6.
4. La información registrada debe ser suficiente para que, dada una señal histórica y las mismas velas de entrada, el sistema pueda reproducir exactamente el mismo resultado (misma dirección, misma decisión de aceptación/rechazo del filtro de volumen y mismo `Stop_Puntos` cuando corresponda).
5. `[SEGURO]` — **Tensión de persistencia a resolver en Diseño:** la reproducibilidad exacta de señales históricas y la validación sobre ventanas configurables (ver RF-E1-04) requieren conservar señales y su configuración a lo largo del tiempo, lo que está en tensión con la restricción arquitectónica "sin base de datos". Como orientación no vinculante, es probable que se resuelva con persistencia ligera en archivos o logs estructurados (registros de eventos con formato regular) en lugar de una base de datos. La decisión final se toma en la fase de Diseño, no en Requisitos.

---

### RF-E1-04: Validación estadística — dos comparaciones independientes

**Objetivo del Desarrollo:** Validar el rendimiento de la Etapa 1 mediante **dos comparaciones independientes** que no deben confundirse entre sí: (A) la validación out-of-sample contra la tasa base, heredada sin cambios del Requisito 4.7; y (B) una comparación estadística nueva contra el baseline Pine Script externo. Ambas se ejecutan sobre una ventana de tiempo configurable y reproducible.

**Criterios de aceptación:**

1. **Validación A (out-of-sample contra tasa base, sin cambios):** el sistema debe validar su Probabilidad_de_Exito (probabilidad estimada de acierto) sobre muestra out-of-sample (datos que no se usaron para entrenar), considerándose superado el umbral únicamente si la tasa de acierto out-of-sample supera en al menos 5 puntos porcentuales la tasa base del período (porcentaje de barras donde la dirección correcta era la tendencia dominante), exactamente como define el Requisito 4.7. Esta validación NO compara contra TradingView.
2. **Validación B (contra el baseline Pine Script, nueva e independiente):** el sistema debe evaluar estadísticamente TradeCore (con ATR y filtro de volumen) frente al baseline Pine Script "NQ Hybrid v11 - Sustainable Edge", usando el mismo instrumento, el mismo período y la misma metodología de cálculo para ambas fuentes, de modo que la comparación sea válida.
3. La Validación B debe calcular, para ambas fuentes con la misma metodología, las siguientes métricas: (a) tasa de aciertos (porcentaje de señales correctas); (b) ratio riesgo/beneficio (relación entre lo arriesgado y lo ganado por operación); (c) MAE (Error Absoluto Medio — promedio de cuánto se equivoca la estimación); y (d) RMSE (Raíz del Error Cuadrático Medio — que penaliza más los errores grandes).
4. Para cada métrica de la Validación B, el sistema debe aplicar el mismo procedimiento de cálculo a las señales de TradeCore y a las del baseline (misma definición de acierto, misma referencia de precio, misma alineación temporal), de modo que ninguna fuente quede favorecida por diferencias de método.
5. Ambas validaciones (A y B) deben ejecutarse sobre una ventana de tiempo configurable y reproducible, definida explícitamente por: fecha de inicio, fecha de fin, resolución temporal (tamaño de vela) y criterio de inclusión de señales. La ventana no debe estar implícita ni fija en el código (hardcodeada).
6. `[SEGURO]` — **Baseline no documentado:** el comportamiento, los parámetros exactos y la metodología del baseline Pine Script "NQ Hybrid v11 - Sustainable Edge" NO están documentados en los artefactos existentes del proyecto. Toda la lógica de la Validación B que dependa de cómo funciona el baseline (cómo genera señales, cómo define su stop, con qué parámetros) queda marcada como `[SEGURO]` pendiente de confirmación. El sistema no debe asumir ni inventar el funcionamiento del baseline; la Validación B no puede completarse hasta que esta información sea confirmada con evidencia. Esto incluye definir si el baseline Pine Script produce una estimación numérica comparable para el cálculo de MAE y RMSE, o si estas métricas requieren una definición operacional adicional para poder aplicarse a un sistema que solo emite señales BUY/SELL con target (objetivo de precio) y stop, sin una predicción de precio continua.

---

### RF-E1-05: Comparación visual en tiempo real de ambas fuentes

**Objetivo del Desarrollo:** Permitir que el Dashboard (panel de control visual accesible desde el navegador) muestre en paralelo las señales de las dos fuentes —TradeCore (vía Databento, proveedor de datos de mercado) y TradingView (vía webhook, mensaje automático que TradingView envía cuando ocurre un evento)— de forma temporalmente alineada y visualmente distinguible.

**Criterios de aceptación:**

1. El Dashboard debe mostrar las señales de ambas fuentes alineadas sobre la misma vela de referencia, usando el mismo timestamp en UTC (Coordinated Universal Time — zona horaria universal) y la misma resolución temporal (tamaño de vela), de modo que una señal de TradeCore y una de TradingView del mismo instante aparezcan en la misma posición temporal. El timestamp recibido en el webhook de TradingView debe normalizarse a formato ISO 8601 UTC (formato estándar internacional de fecha y hora, por ejemplo `2025-01-15T14:30:00Z`) en el momento de la ingesta (entrada del dato al sistema), independientemente del formato de origen (Unix timestamp —segundos desde el 1 de enero de 1970 UTC— o fecha/hora con zona horaria local).
2. El Dashboard debe identificar visualmente de forma clara qué señal proviene de cada fuente (por ejemplo, mediante color, forma o etiqueta distinta), sin ambigüedad para el usuario.
3. Si una de las dos fuentes deja de estar disponible, el Dashboard debe indicar claramente cuál fuente sigue activa y cuál no, de modo que la ausencia de una fuente no se confunda con una diferencia de rendimiento entre ambas.
4. La recepción de señales de TradingView vía webhook debe integrarse sin alterar el protocolo de mensajes WebSocket (protocolo que mantiene una conexión abierta para enviar datos en tiempo real) ya existente entre el backend y el Dashboard para las señales de TradeCore.
5. `[SEGURO]` — **Formato del webhook de TradingView:** el formato exacto del mensaje (webhook) que TradingView envía, su contenido y su autenticación no están documentados en los artefactos existentes. Estos detalles quedan marcados como `[SEGURO]` pendientes de confirmación.
6. `[SEGURO]` — **Alineación de velas entre feeds distintos:** Databento (futuro continuo NQ.c.0) y TradingView pueden cerrar una vela con pequeñas diferencias de tiempo entre sí debido a latencia o diferencias de feed (fuente de datos). El criterio exacto de tolerancia para considerar que dos velas de fuentes distintas son "la misma vela" (por ejemplo, margen de segundos aceptado) no está definido y queda marcado como `[SEGURO]` pendiente de confirmación en Diseño.

---

### RF-E1-06: Dependencia de Fase B (Live Streaming) — bloqueo de avance acotado

**Objetivo del Desarrollo:** Declarar explícitamente que solo la comparación visual en tiempo real (RF-E1-05) depende de la Fase B (streaming de datos en vivo con Databento). Este requisito NO redefine la Fase B; solo declara la dependencia y establece un bloqueo de avance acotado a RF-E1-05. NO reescribe las Tareas 12.1–12.3 del spec `tradecore-mvp`.

**Criterios de aceptación:**

1. La comparación visual en tiempo real (RF-E1-05) depende de que existan datos en vivo; por lo tanto, requiere que las Tareas 12.1, 12.2 y 12.3 del spec `tradecore-mvp` estén completas y probadas: `subscribe_live()` (suscripción al stream —flujo continuo de datos— en vivo de Databento), reconexión con backoff exponencial (reintentos con espera creciente), sincronización entre datos en vivo e históricos, experiencia de usuario ante reconexión, y pruebas unitarias correspondientes.
2. El avance a Diseño de RF-E1-01, RF-E1-02, RF-E1-03 y RF-E1-04 no depende de la Fase B y puede proceder de forma independiente. Únicamente el avance a Diseño de RF-E1-05 (comparación visual en tiempo real) queda bloqueado hasta que las Tareas 12.1–12.3 de Fase B estén completas y validadas.
3. Este requisito no modifica ni reescribe el contenido de las Tareas 12.1–12.3; únicamente declara la dependencia y el bloqueo acotado.

**Nota:** al momento de redactar este documento, las Tareas 12.1, 12.2 y 12.3 figuran como pendientes (no completadas) en el spec `tradecore-mvp`.

---

### RF-E1-07: Refactor del Dashboard (deuda técnica, sin cambio de comportamiento)

**Objetivo del Desarrollo:** Reorganizar el código del Dashboard para separar estructura, estilo y lógica en archivos distintos (deuda técnica —trabajo de limpieza pendiente que mejora la mantenibilidad—), sin alterar ningún comportamiento existente. Es reorganización de código, no funcionalidad nueva.

**Criterios de aceptación:**

1. El archivo HTML (lenguaje de estructura de páginas web) del Dashboard debe contener únicamente estructura y referencias a recursos externos; no debe contener estilos CSS (lenguaje de estilos visuales) ni lógica JavaScript (lenguaje de interacción) embebidos dentro del propio HTML.
2. Todos los estilos visuales deben residir en un archivo `.css` separado, referenciado desde el HTML.
3. Toda la lógica de interacción debe residir en un archivo `.js` separado, referenciado desde el HTML.
4. El resultado debe ser verificable: una revisión del HTML no debe encontrar bloques de CSS ni de JavaScript embebidos.
5. El contenedor del Dashboard debe tener un margen exterior de 8 píxeles en los cuatro lados (arriba, abajo, izquierda y derecha).
6. El refactor no debe modificar la lógica de WebSocket (protocolo que mantiene una conexión abierta para enviar datos en tiempo real), ni el manejo de velas, ni los marcadores de señales existentes: el comportamiento observable del Dashboard debe permanecer idéntico al actual.

**Nota de frontera (no regresión):** este requisito toca el archivo `dashboard/index.html`, que está protegido en el contexto de la fase actual. Su modificación se limita a mover código (estructura, estilo, lógica) a archivos separados y agregar el margen, sin cambiar comportamiento observable. Requiere aprobación explícita antes de ejecutarse en fases posteriores, conforme al principio de protección de componentes vigente.

---

## Restricción transversal de no regresión

Ningún cambio de la Etapa 1 debe alterar la funcionalidad existente fuera de su alcance —recepción de datos, formación de velas, visualización de señales existente— salvo que sea estrictamente necesario para implementar los requisitos anteriores. Cualquier alteración necesaria de comportamiento existente debe quedar documentada explícitamente en el requisito correspondiente (ver notas de frontera en RF-E1-01, RF-E1-02 y RF-E1-07).

---

## Restricciones que se mantienen

- Arquitectura single-process (un solo proceso), sin base de datos, sin autenticación adicional a la ya definida.
- No se introduce Gradient Boosting (técnica de aprendizaje automático prevista para la Etapa 5), ni QQQ/NDX (otros instrumentos), ni Greeks/GEX (métricas de opciones), ni datos de libro de órdenes completo (Order Book). Todo esto queda fuera de alcance.
- Se mantiene la convención `[SEGURO]` para cualquier dato o supuesto que el equipo deba confirmar con evidencia antes de avanzar.

---

## Lista consolidada de puntos `[SEGURO]` (pendientes de confirmación)

1. Valor por defecto del período del ATR (RF-E1-01, criterio 1).
2. Valor por defecto del multiplicador del ATR (RF-E1-01, criterio 2).
3. Parámetros del Risk_Gate: `equity_disponible`, `riesgo_pct`, `valor_por_punto` (RF-E1-01, criterio 5).
4. Valor por defecto de N períodos del filtro de volumen (RF-E1-02, criterio 1).
5. Criterio/valor por defecto del umbral de volumen (RF-E1-02, criterio 2).
6. Resolución de la tensión persistencia vs. "sin base de datos" (RF-E1-03, criterio 5) — a decidir en Diseño.
7. Comportamiento, parámetros y metodología del baseline Pine Script "NQ Hybrid v11 - Sustainable Edge", incluida la aplicabilidad de MAE/RMSE a un sistema que solo emite señales BUY/SELL con target y stop (RF-E1-04, criterio 6).
8. Formato, contenido y autenticación del webhook de TradingView (RF-E1-05, criterio 5).
9. Criterio de tolerancia para alinear velas entre feeds distintos (Databento vs TradingView) (RF-E1-05, criterio 6).

---

## Decisiones de gobernanza registradas

1. Este documento constituye la revisión explícita de Requirements que autoriza incorporar TradingView, backtesting/validación estadística y Risk_Gate, previamente restringidos en `dev-rules.md`. La actualización de `dev-rules.md` es un paso separado, posterior a la aprobación de este documento.
2. La tensión entre reproducibilidad/persistencia y la arquitectura "sin base de datos" se declara como `[SEGURO]` a resolver en Diseño (RF-E1-03, criterio 5).
3. El avance a Diseño queda bloqueado ÚNICAMENTE para RF-E1-05 hasta que la Fase B (Tareas 12.1-12.3 de `tradecore-mvp`) esté completa y validada. RF-E1-01 a RF-E1-04 pueden avanzar a Diseño de forma independiente (RF-E1-06, criterio 2).