<!--
NOTA DE USO PARA KIRO — LEER ANTES DE CITAR ESTE DOCUMENTO

Este archivo es una copia de referencia, de SOLO LECTURA, del documento original
"TradeCore_Requisitos_del_Software.docx" (fase de diseño previa del proyecto,
documento conceptual mayor). Se incorpora al workspace para que puedas verificar
directamente el texto de los requisitos citados, en vez de depender de
transcripciones manuales del usuario en el chat.

REGLAS DE USO OBLIGATORIAS:

1. Este documento describe la ARQUITECTURA CONCEPTUAL MAYOR de TradeCore
   (Quant_Model con GARCH/ARIMA/Gradient Boosting, Risk_Gate completo, Backtesting_Module,
   Live_Tracker, etc.). NO todos estos requisitos están implementados ni vigentes en el
   MVP actual (Fase A) ni en Etapa 1. No asumas que un requisito aquí descrito ya existe
   en el código solo porque está en este documento — verifica siempre contra el código real.

2. Los requisitos vigentes y con trazabilidad activa en el spec de Etapa 1
   (tradecore-etapa1) son únicamente los que están citados textualmente dentro de
   requirements.md/design.md/tasks.md de ese spec (a fecha de esta copia: 4.6, 4.7, 5.2, 5.5).
   Si necesitas citar un requisito adicional de este documento en un artefacto de Etapa 1,
   transcríbelo textualmente en el spec correspondiente (no solo referencies este archivo),
   para mantener la trazabilidad dentro del propio spec.

3. Varios campos y mecanismos aquí descritos (ej. Target_Puntos, Horizonte_min_dias/
   Horizonte_max_dias, Probabilidad_de_Exito, Quant_Model con GARCH/ARIMA) pertenecen al
   diseño conceptual mayor y fueron EXPLÍCITAMENTE dejados fuera de alcance de Etapa 1
   (ver "Principio general que rige toda la Etapa 1" en requirements.md de tradecore-etapa1).
   No los reintroduzcas ni los tomes como requisito vigente sin que el usuario lo autorice
   explícitamente.

4. Si encuentras una contradicción entre este documento y el estado real del código o los
   specs vigentes de Etapa 1, repórtala explícitamente como conflicto (igual que ya se hizo
   con la contradicción de horizonte: días vs. barras de 5 minutos vs. velas de 1 minuto).
   No la resuelvas por tu cuenta asumiendo cuál fuente prevalece.

5. La sigla "MAE" en este documento (Requisito 6) significa Mean Absolute Error
   (Error Absoluto Medio, métrica de error de predicción). Es DISTINTA de "MAE" usado en
   el sentido de Maximum Adverse Excursion en discusiones de calibración de Etapa 1.
   Desambigua siempre el término completo la primera vez que lo uses en cada sección.

Última actualización de esta copia: ver fecha de creación del archivo en el sistema.
-->

# Documento de Requisitos — TradeCore (documento fuente, fase de diseño previa)

**Estado:** documento conceptual mayor, NO vigente en su totalidad para el MVP actual ni para Etapa 1. Ver nota de uso arriba.

---

**Requisito 1: Recepción y normalización de señales de TradingView**

Objetivo del Desarrollo: Tomar las señales de compra y venta generadas automáticamente por una script (programa automatizado) en TradingView sean recibidas y procesadas por el sistema sin que yo tenga que hacer nada manualmente, para que el sistema las use como uno de sus datos de entrada.

**Criterios de aceptación**

1. Cuando el script de TradingView envía un webhook (mensaje automático por internet) con una señal de compra o venta, el Signal_Processor (módulo que recibe y valida señales entrantes) debe recibir el mensaje, verificar que tiene el formato correcto y guardarlo en el Feature_Store (almacén de indicadores) en un plazo máximo de 2 segundos.

2. Si el webhook recibido no tiene el formato esperado (le faltan campos obligatorios o contiene datos del tipo incorrecto), el Signal_Processor debe rechazar el mensaje devolviendo un error HTTP 422 (código estándar de internet que indica "dato inválido") y registrar el incidente en el sistema de registro de eventos (log) con la fecha, hora y contenido del mensaje recibido.

3. El Signal_Processor debe convertir la fecha y hora de las señales de TradingView al formato UTC ISO 8601 (zona horaria universal estándar) antes de guardarlas en el Feature_Store.

4. Mientras el Signal_Processor esté funcionando, el punto de recepción de mensajes de TradingView debe estar disponible más del 99 % del tiempo, medido en ventanas de 24 horas.

5. Si el Feature_Store (almacén de indicadores) no está disponible cuando llega una señal, el Signal_Processor debe mantener la señal en espera en memoria hasta 5 minutos y reintentar guardarla hasta 3 veces, esperando más tiempo entre cada intento (espera exponencial: 1 s, 2 s, 4 s).

**Requisito 2: Obtención de datos del libro de órdenes**

Objetivo del Desarrollo: Obtener en tiempo real los datos del libro de órdenes (registro de todas las órdenes de compra y venta activas) del NASDAQ100 desde el broker (empresa intermediaria de mercados), para que el modelo pueda incorporar información sobre la oferta y demanda real del mercado.

**Criterios de aceptación**

1. Cuando el sistema se inicia, el Order_Book_Connector (conector del libro de órdenes) debe establecer conexión con la API (interfaz de acceso a datos) del broker y comenzar a recibir actualizaciones del libro de órdenes del NASDAQ100.

2. El Order_Book_Connector debe extraer como mínimo los 10 mejores precios de compra (bid) y los 10 mejores precios de venta (ask) con sus respectivos volúmenes (cantidades) en cada actualización recibida.

3. El Data_Ingestion_Pipeline (proceso de ingesta de datos) debe normalizar (estandarizar el formato) y guardar los registros del libro de órdenes en el Feature_Store (almacén de indicadores) con una frecuencia mínima de 1 registro por minuto durante el horario de mercado (09:30–16:00 hora del Este de EE.UU.).

4. Si la conexión con el broker se interrumpe, el Order_Book_Connector debe intentar reconectarse automáticamente hasta 5 veces con intervalos de espera crecientes (espera exponencial), registrando cada intento fallido en el sistema de registro de eventos.

5. Si la reconexión falla después de 5 intentos, el Order_Book_Connector debe emitir una alerta de nivel CRÍTICO en el sistema de registro de eventos e indicar que la fuente del libro de órdenes no está disponible.

6. Mientras la fuente del libro de órdenes no esté disponible, el Quant_Model (modelo cuantitativo) debe continuar operando usando solo los datos disponibles de TradingView, marcando cada señal generada con el indicador `order_book_unavailable = true` (libro de órdenes no disponible) para que el operador sepa que la señal fue generada con información incompleta.

**Requisito 3: Almacén de indicadores y sincronización temporal (Feature_Store)**

Objetivo del Desarrollo: Quiero un almacén centralizado (Feature_Store) donde todos los datos de entrada estén organizados y sincronizados en el tiempo, para que el modelo reciba siempre información consistente y alineada independientemente de su fuente de origen.

**Criterios de aceptación**

1. El Feature_Store (almacén central de indicadores) debe organizar todos los datos por marca de tiempo en UTC (zona horaria universal) y por instrumento (NASDAQ100), garantizando que cualquier dataset (conjunto de datos de entrenamiento) pueda reproducirse exactamente en el futuro.

2. El Data_Ingestion_Pipeline (proceso de ingesta) debe alinear los datos de las dos fuentes (TradingView y libro de órdenes) en ventanas de tiempo configurables (por defecto: 1 minuto, 5 minutos, 1 hora, 1 día). Cuando faltan datos en una ventana, debe rellenar el hueco con el último valor disponible dentro de la misma sesión de mercado (técnica conocida como forward-fill).

3. El Feature_Store debe permitir consultar cualquier rango de datos históricos en un tiempo máximo de 5 segundos para rangos de hasta 90 días.

4. Si una fuente de datos tiene más del 30 % de sus valores ausentes en una ventana de tiempo determinada, el Data_Ingestion_Pipeline debe marcar esa ventana con el indicador `data_quality_warning` (aviso de calidad de datos) en el Feature_Store para que el operador sepa que los datos pueden ser incompletos.

5. El Feature_Store debe registrar junto a cada dato almacenado: su origen (fuente), la fecha y hora en que fue ingresado, y la versión del proceso que lo generó. Esto garantiza la trazabilidad completa (capacidad de rastrear de dónde vino cada dato).

**Requisito 4: Modelo cuantitativo (Quant_Model)**

Objetivo del Desarrollo: Desarrollar un modelo que combine técnicas estadísticas avanzadas e inteligencia artificial para estimar la probabilidad y magnitud del próximo movimiento de precio del NASDAQ100, para que las señales generadas estén respaldadas matemáticamente y no sean solo intuición.

**Criterios de aceptación**

1. El Quant_Model (modelo cuantitativo) debe implementar una arquitectura híbrida (que combina distintos enfoques) compuesta por: (a) un componente econométrico que incluye GARCH (modelo estadístico para medir la volatilidad —intensidad de los movimientos de precio—) y ARIMA (modelo para predecir tendencias en series de datos históricos); y (b) un componente de Machine Learning (inteligencia artificial) basado en Gradient Boosting con XGBoost o LightGBM (algoritmos de IA que aprenden de errores anteriores).

2. Cuando se ejecuta una predicción, el Quant_Model debe producir para cada horizonte temporal (plazo de tiempo) solicitado: la Variación_Estimada (porcentaje de cambio de precio esperado), la dirección (si el precio subirá o bajará), la Probabilidad_de_Exito (porcentaje de 0 % a 100 % que indica qué tan probable es que la señal sea correcta) y el Horizonte_Temporal estimado en días.

3. El Quant_Model debe soportar horizontes de predicción (plazos de tiempo para la predicción) configurables entre 1-10 barras de velas cada 5 minutos del gráfico.

4. El Quant_Model debe ser entrenado con datos históricos del Feature_Store (almacén de indicadores) que cubran un mínimo de 2 años de historia del NASDAQ100.

5. Cuando se solicita una predicción, el Quant_Model debe completar el cálculo y entregar el resultado al Signal_Engine (motor de señales) en un tiempo máximo de 3 segundos.

6. El Quant_Model debe registrar en el sistema de registro de eventos (log) cada predicción realizada: los datos de entrada usados, los resultados obtenidos, la fecha y hora, y la versión del modelo. Esto garantiza que cualquier señal pueda ser rastreada y auditada.

7. Si la Probabilidad_de_Exito calculada para una predicción es inferior al umbral configurable (valor por defecto: 60 %), el Quant_Model debe marcar la predicción como `low_confidence` (baja confianza) y el Signal_Engine (motor de señales) la descartará sin emitir señal. La Probabilidad_de_Exito debe ser validada sobre muestra out-of-sample (datos que el modelo no usó para entrenarse). El umbral del 60% se considera superado únicamente si la tasa de acierto out-of-sample supera en al menos 5 puntos porcentuales la tasa base del período (porcentaje de barras donde la dirección correcta era la tendencia dominante).

**Requisito 5: Motor de señales (Signal_Engine)**

Objetivo del Desarrollo: Quiero que el sistema emita señales de inversión solo cuando la predicción del modelo tenga suficiente confianza, para evitar operar en condiciones de baja probabilidad de éxito.

**Criterios de aceptación**

1. El Signal_Engine (motor de señales) debe emitir una señal únicamente cuando la Probabilidad_de_Exito producida por el Quant_Model (modelo cuantitativo) sea igual o superior al umbral de confianza mínima configurable (valor por defecto: 60 %).

2. Si la predicción está marcada como `low_confidence` (baja confianza, probabilidad < 60 %), el Signal_Engine debe descartarla sin emitir señal y registrar el descarte en el sistema de eventos con el motivo `low_confidence`.

3. Cuando el Signal_Engine emite una señal, el objeto de señal debe incluir los siguientes campos, tal como se ve en el panel de trading de referencia:

   - Tipo: COMPRA (BUY) o VENTA (SELL)

   - Probabilidad_de_Exito: porcentaje estimado de éxito (ej. ~66 %)

   - Trigger: qué activó la señal (ej. OB —Order Block, zona de actividad institucional—, ORB —Opening Range Breakout, ruptura del rango de apertura—, Sweep —barrida de liquidez—)

   - Precio_Entrada: precio recomendado de entrada a la operación

   - Target_Puntos: puntos de precio objetivo de ganancia (ej. +22 pts)

   - Target_Dolares: equivalente en dólares del objetivo de ganancia (ej. +$77)

   - Stop_Puntos: puntos de precio para el stop loss (límite de pérdida, ej. -15 pts)

   - Stop_Dolares: equivalente en dólares del stop loss (ej. -$53)

   - Horizonte_min_dias y Horizonte_max_dias: rango de días estimado para que se cumpla el objetivo

   - timestamp_emision: fecha y hora de emisión en UTC (zona horaria universal)

   - version_modelo: versión del modelo que generó la señal

   - fuentes_activas: lista de fuentes de datos usadas (ej. ["tradingview", "order_book"])

   - order_book_unavailable: indicador de si el libro de órdenes estaba disponible al generar la señal

4. El Signal_Engine debe garantizar que no existan dos señales activas simultáneas para el mismo instrumento, dirección y horizonte temporal. Si se genera una señal duplicada, debe descartarla y registrar el evento.

5. El Signal_Engine debe guardar cada señal emitida en el Feature_Store (almacén de indicadores) con un identificador único UUID (código universal único) para su seguimiento posterior por el Live_Tracker (seguidor en vivo) y el Backtesting_Module (módulo de evaluación histórica).

**Requisito 6: Módulo de evaluación histórica (Backtesting_Module)**

Objetivo del Desarrollo: El sistema debe evaluar qué tan bien habrían funcionado las señales del sistema en el pasado comparándolas con los precios reales del mercado, para conocer con precisión la tasa de aciertos y el ratio riesgo/beneficio antes de operar con capital real.

**Criterios de aceptación**

1. El Backtesting_Module (módulo de evaluación histórica) debe comparar cada señal histórica con el precio real del NASDAQ100 al vencimiento del Horizonte_Temporal (plazo estimado), determinando si la Variación_Estimada (movimiento de precio predicho) se materializó dentro del rango y plazo predichos.

2. El Backtesting_Module debe calcular las siguientes métricas para cualquier rango de fechas configurable: MAE (Error Absoluto Medio —promedio de cuánto se equivocó el modelo—), RMSE (Raíz del Error Cuadrático Medio —penaliza más los errores grandes—), tasa de aciertos (porcentaje de señales que llegaron al objetivo), ratio riesgo/beneficio (cuánto se gana en promedio por cada unidad de riesgo asumida), y tasa de señales emitidas vs. descartadas.

   > **Nota de desambiguación:** el "MAE" de este criterio es *Mean Absolute Error* (Error Absoluto Medio), una métrica de error de predicción. No confundir con "MAE" en el sentido de *Maximum Adverse Excursion*, usado en discusiones de calibración de Etapa 1 — son conceptos distintos que comparten sigla.

3. El Backtesting_Module debe exponer los resultados de la evaluación histórica a través del API_Service (servicio web) en formato JSON (formato estándar de intercambio de datos) que incluya las métricas calculadas y el rango de fechas analizado.

4. Cuando se ejecuta una evaluación histórica completa sobre todos los datos disponibles, el Backtesting_Module debe completar el procesamiento en un tiempo máximo de 60 segundos para datasets (conjuntos de datos) de hasta 2 años de señales.

5. El Backtesting_Module debe mantener un historial versionado de resultados, asociando cada evaluación a la versión del Quant_Model (modelo cuantitativo) utilizada, para poder comparar el desempeño entre versiones del modelo.

6. Si los datos de precio real para el vencimiento de una señal no están disponibles todavía, el Backtesting_Module debe marcar esa señal como `pending_evaluation` (pendiente de evaluación) y excluirla del cálculo hasta que los datos estén disponibles.

**Requisito 7: Seguimiento en vivo de señales (Live_Tracker)**

Objetivo del Desarrollo: Se debe monitorear en tiempo real el estado de cada señal activa emitida por el sistema, para saber si la predicción está en camino de cumplirse, ya se cumplió o fue invalidada.

**Criterios de aceptación**

1. Mientras exista al menos una señal activa, el Live_Tracker (seguidor en vivo) debe comparar el precio actual del NASDAQ100 contra el precio objetivo y el plazo de cada señal activa, con una frecuencia mínima de 1 vez por minuto durante el horario de mercado.

2. Cuando el precio real del NASDAQ100 alcanza el precio objetivo de una señal activa dentro del plazo estimado, el Live_Tracker debe marcar la señal como `fulfilled` (cumplida) y registrar la fecha, hora y precio real al que se cerró.

3. Cuando el plazo de una señal activa vence sin que el precio haya alcanzado el objetivo, el Live_Tracker debe marcar la señal como `expired` (vencida) y registrar el precio real en ese momento.

4. El Live_Tracker debe exponer el estado actualizado de todas las señales activas a través del API_Service (servicio web) con una latencia (retraso) máxima de 60 segundos entre actualizaciones.

5. Si el precio real del NASDAQ100 se mueve en dirección contraria a una señal activa en un porcentaje mayor al umbral de stop loss configurable (límite de pérdida; umbral configurable igual al stop_pts definido en la señal de origen del Signal_Engine), el Live_Tracker debe marcar la señal como `stop_loss_triggered` (stop loss activado) y registrar el evento. Al activarse stop_loss_triggered, el sistema debe emitir una alerta de nivel CRÍTICO al operador via API_Service y bloquear la emisión de nuevas señales sobre el mismo instrumento por un período configurable (default: 30 min).

**Requisito 8: Reentrenamiento automático del modelo (Retraining_Scheduler)**

Objetivo del Desarrollo: El modelo se debe actualizar automáticamente con los datos más recientes, para que su precisión mejore continuamente a medida que acumula experiencia con resultados reales del mercado.

**Criterios de aceptación**

1. El Retraining_Scheduler (programador de reentrenamiento) debe ejecutar un ciclo de reentrenamiento completo del Quant_Model (modelo cuantitativo) con una frecuencia configurable (valor por defecto: una vez por semana, los domingos a las 02:00 UTC —zona horaria universal—).

2. El proceso de reentrenamiento semanal debe completarse antes de las 08:00 UTC del lunes (apertura de mercados europeos). Si el proceso no ha terminado a las 08:00 UTC del lunes, el sistema debe interrumpirlo y continuar operando con el modelo anterior que estaba activo antes de iniciar el reentrenamiento, sin interrumpir el servicio en ningún momento.

3. Cuando el Backtesting_Module (módulo de evaluación histórica) detecta que la tasa de aciertos cayó por debajo del umbral configurable (valor por defecto: 50 %) en las últimas 4 semanas, el Retraining_Scheduler debe iniciar un ciclo de reentrenamiento no programado en un plazo máximo de 1 hora.

4. El Retraining_Scheduler debe utilizar el Feature_Store (almacén de indicadores) como fuente de datos de entrenamiento, incorporando todos los datos e indicadores disponibles hasta el momento del reentrenamiento.

5. Cuando el reentrenamiento produce un modelo con métricas de evaluación (precisión, tasa de aciertos, etc.) peores que las del modelo actualmente en uso, el Retraining_Scheduler debe conservar el modelo anterior como activo y registrar el resultado del reentrenamiento como `rejected` (rechazado) con las métricas comparativas.

6. Cuando el reentrenamiento produce un modelo con métricas de evaluación iguales o mejores que el modelo en uso, el Retraining_Scheduler debe reemplazar el modelo activo con el nuevo, archivar la versión anterior en el almacén de modelos (Model Store —repositorio de versiones de modelos—) y registrar el evento de promoción.

7. El Retraining_Scheduler debe registrar en el sistema de eventos cada ciclo de reentrenamiento con: fecha y hora de inicio, duración total, versión del nuevo modelo, métricas de evaluación obtenidas y resultado (promoted —promovido— o rejected —rechazado—).

**Requisito 9: Servicio web (API_Service)**

Objetivo del Desarrollo: Agregar un servicio web (API —Interfaz de Programación de Aplicaciones—) construido con FastAPI (framework moderno de Python para APIs web) que exponga todos los datos y operaciones del sistema, para que el Dashboard (panel de control) y cualquier aplicación externa puedan obtener la información de forma estructurada y segura.

**Criterios de aceptación**

1. El API_Service debe exponer como mínimo los siguientes puntos de acceso (endpoints): `GET /signals/active` (obtener señales activas), `GET /signals/history` (historial de señales), `GET /signals/{id}` (señal específica por su identificador único), `GET /metrics/backtesting` (métricas de evaluación histórica), `GET /metrics/live` (estado en vivo), `GET /sources/status` (estado de las fuentes de datos), `POST /signals/webhook/tradingview` (recibir señales de TradingView).

2. El API_Service debe devolver todas las respuestas en formato JSON (formato estándar de intercambio de datos) con una estructura consistente que siempre incluya los campos: `data` (datos de la respuesta), `status` (estado: éxito o error), `timestamp` (fecha y hora de la respuesta en UTC) y `errors` (lista de errores si los hubiera).

3. Cuando una petición al API_Service contiene parámetros inválidos, el servicio debe devolver el código de error HTTP 422 (dato inválido) con un cuerpo JSON que describa cada campo incorrecto y el motivo del error.

4. El API_Service debe implementar autenticación (verificación de identidad) mediante clave de API (API Key —código secreto de acceso—) en todas las rutas, rechazando con HTTP 401 (no autorizado) las peticiones que no incluyan una clave válida en el encabezado `X-API-Key`.

5. El API_Service debe registrar cada petición recibida en el sistema de eventos con: fecha y hora, método HTTP (GET, POST, etc.), ruta accedida, código de respuesta y tiempo de respuesta en milisegundos.

6. El API_Service debe responder a todas las peticiones de consulta (GET) en un tiempo máximo de 500 milisegundos para el percentil 95 (es decir, el 95 % de las peticiones deben responderse en menos de 500 ms) bajo carga normal de operación.

**Requisito 10: Panel de control visual (Dashboard)**

Objetivo del Desarrollo: Se debe agregar un panel de control web (Dashboard) que muestre en tiempo real las señales activas, el historial, las métricas del modelo y el estado de las fuentes de datos, para tomar decisiones informadas de manera visual y rápida, sin necesidad de conocimientos técnicos.

**Criterios de aceptación**

1. El Dashboard debe mostrar en la sección de señales activas —con un diseño similar al panel de trading de referencia— para cada señal: tipo (COMPRA/VENTA), Probabilidad_de_Exito (porcentaje), Trigger (qué activó la señal: OB —Order Block—, ORB —Opening Range Breakout—, Sweep —barrida de liquidez—), precio de entrada, objetivo en puntos y dólares (Target), stop loss en puntos y dólares (Stop), horizonte temporal en días y estado actual.

2. El Dashboard debe mostrar en la sección de historial de señales la lista de señales pasadas con su resultado final (fulfilled —cumplida—, expired —vencida—, stop_loss_triggered —stop loss activado—) y sus métricas asociadas, paginada en grupos de 20 registros.

3. El Dashboard debe mostrar las siguientes métricas acumuladas del modelo en el período seleccionado: tasa de aciertos (%), MAE (Error Absoluto Medio), RMSE (Raíz del Error Cuadrático Medio), ratio riesgo/beneficio y total de señales emitidas.

4. El Dashboard debe mostrar el estado en tiempo real de las dos fuentes de datos activas en esta versión (Signal_Processor —procesador de señales de TradingView— y Order_Book_Connector —conector del libro de órdenes—) con indicadores visuales de: activo (verde), degradado (amarillo) o no disponible (rojo).

5. El Dashboard debe actualizar automáticamente la información de señales activas y estado de fuentes sin que el operador tenga que recargar la página, con un intervalo máximo de 60 segundos entre actualizaciones usando WebSocket como canal primario con fallback a polling.

6. Cuando el Dashboard no puede conectarse al API_Service (servicio web), debe mostrar un mensaje de error visible al operador indicando el estado de conectividad y la fecha y hora del último dato recibido.

**Requisito 11: Registro de eventos, trazabilidad y manejo de errores**

Objetivo del Desarrollo: El sistema debe registrar de forma ordenada todos los eventos relevantes, errores y rastros de señales, para poder diagnosticar problemas, auditar (revisar) decisiones pasadas y garantizar que los resultados son reproducibles.

**Criterios de aceptación**

1. TradeCore debe implementar un sistema de registro de eventos (logging —archivo donde se guardan todos los eventos del sistema—) en formato JSON (formato estructurado de texto), con los siguientes niveles de severidad: DEBUG (depuración, para desarrolladores), INFO (información general), WARNING (advertencia), ERROR (error recuperable) y CRITICAL (error grave). El nivel activo debe poder cambiarse sin reiniciar el servicio.

2. Cada entrada del registro de eventos debe incluir como mínimo: timestamp (fecha y hora en UTC —zona horaria universal—), level (nivel de severidad), module (nombre del módulo que lo generó), message (descripción del evento), correlation_id (identificador único que vincula todos los registros de una misma operación) y, cuando aplique, signal_id (identificador de la señal relacionada) y model_version (versión del modelo activa).

3. Cuando cualquier componente del sistema genera un error inesperado (excepción no controlada), TradeCore debe capturarlo, registrarlo con nivel ERROR incluyendo el detalle completo del error (stack trace —rastro técnico del error—), y continuar operando sin interrumpir el servicio.

4. TradeCore debe mantener los archivos de registro con una política de retención de 90 días y rotación diaria (se crea un archivo nuevo cada día), sin superar 500 MB de almacenamiento total de registros.

5. El API_Service (servicio web) debe asignar un `correlation_id` (identificador de correlación —UUID único que vincula todos los registros de una petición—) a cada petición entrante y propagarlo a todos los módulos internos que participan en su procesamiento, permitiendo rastrear una operación de principio a fin.

**Requisito 12: Modularidad y extensibilidad del sistema**

Objetivo del Desarrollo: El sistema debe estar construido en módulos independientes con interfaces claras entre ellos, para poder agregar nuevas fuentes de datos o modelos en el futuro sin tener que reescribir grandes partes del código.

**Requisito 13: Risk_Gate**

Control de riesgo operativo (Risk_Gate)

Objetivo del Desarrollo: Como operador de trading, quiero que el sistema limite automáticamente la exposición de capital en condiciones adversas, para que ninguna sesión de mercado pueda superar el límite de pérdida diaria configurado ni acumular señales simultáneas sin control.

**Criterios de aceptación:**

El Risk_Gate debe calcular el tamaño de posición de cada señal emitida usando la fórmula: lotes = (equity_disponible * riesgo_pct) / (stop_pts * valor_por_punto). Los parámetros riesgo_pct y valor_por_punto deben ser configurables vía .env.

El Risk_Gate no debe permitir más de max_señales_concurrentes (default: 2) señales activas simultáneas para el mismo instrumento. Si se genera una nueva señal y ya existen 2 activas, la nueva señal debe descartarse y registrarse el evento con motivo max_concurrent_reached.

Si el P&L acumulado del día supera el límite de drawdown diario configurable (default: igual al daily_limit del operador), el Risk_Gate debe bloquear la emisión de nuevas señales para el resto de la sesión y emitir alerta CRÍTICO via API_Service.

Si se registran 3 señales consecutivas con resultado stop_loss_triggered, el Risk_Gate debe activar un circuit breaker que pause la emisión de nuevas señales por un período configurable (default: 120 minutos) e informar al operador via API_Service.

El Risk_Gate debe registrar cada decisión de bloqueo o reducción de tamaño en el sistema de eventos con: motivo, timestamp, estado del equity y señal afectada.

---

*Fin del documento fuente. Ver nota de uso al inicio de este archivo antes de citar cualquier sección en artefactos de Etapa 1.*
