---
inclusion: fileMatch
fileMatchPattern: "**/specs/**/requirements.md"
---

# Regla de redacción para documentos de requerimientos

## Audiencia objetivo

Los documentos de requerimientos deben ser comprensibles para **cualquier persona**, incluyendo operadores de trading, gestores de riesgo e inversores que no tienen formación en desarrollo de software o desarrolladores que no tienen experiencia en trading.

## Norma obligatoria: explicación de términos técnicos y siglas

**Todo término técnico, nombre de componente interno o sigla debe ir acompañado de una explicación entre paréntesis** la primera vez que aparece en cada sección del documento.

### Ejemplos de aplicación

| ❌ Incorrecto | ✅ Correcto |
|---|---|
| El Signal_Processor recibe el webhook | El Signal_Processor (módulo que recibe y valida señales entrantes) recibe el webhook (mensaje automático enviado por TradingView) |
| El Feature_Store indexa por timestamp UTC | El Feature_Store (almacén central de datos del modelo) indexa por timestamp UTC (marca de tiempo en zona horaria universal) |
| Se calcula MAE y RMSE | Se calcula el MAE (Error Absoluto Medio, que mide cuánto se equivoca el modelo en promedio) y el RMSE (Raíz del Error Cuadrático Medio, que penaliza más los errores grandes) |
| API Key en el header X-API-Key | Clave de acceso API (código secreto que identifica al usuario) en el encabezado X-API-Key |

## Componentes del sistema — glosario de referencia rápida

Al redactar requerimientos, usar siempre la forma: **Nombre_Componente** (descripción breve en lenguaje natural).

| Componente | Explicación entre paréntesis a usar |
|---|---|
| Signal_Processor | módulo que recibe y valida las señales enviadas por TradingView |
| Order_Book_Connector | módulo que obtiene en tiempo real las órdenes de compra y venta del mercado desde el broker |
| Data_Ingestion_Pipeline | proceso que recoge, normaliza y sincroniza todos los datos de entrada |
| Feature_Store | base de datos centralizada donde se almacenan todos los indicadores calculados |
| Quant_Model | modelo matemático que estima la probabilidad y magnitud del movimiento de precio |
| Signal_Engine | motor de reglas que decide si una predicción del modelo se convierte en señal de trading o se descarta |
| Backtesting_Module | módulo que evalúa qué tan bien habrían funcionado las señales en el pasado usando datos históricos reales |
| Live_Tracker | módulo que monitorea en tiempo real si las señales activas se están cumpliendo |
| Retraining_Scheduler | proceso automático que re-entrena el modelo con datos nuevos para mantener su precisión |
| API_Service | servicio web (FastAPI) que conecta el backend del sistema con el dashboard y aplicaciones externas |
| Dashboard | panel de control visual accesible desde el navegador |
| Feature_Store | almacén central de características (indicadores calculados a partir de los datos de mercado) |

## Siglas frecuentes — siempre explicar la primera vez en cada sección

| Sigla | Explicación |
|---|---|
| HTTP | Protocolo de transferencia de datos por internet |
| UTC | Zona horaria universal (Coordinated Universal Time) |
| UUID | Identificador único universal (código que identifica de forma exclusiva cada registro) |
| MAE | Error Absoluto Medio (promedio de cuánto se equivoca el modelo) |
| RMSE | Raíz del Error Cuadrático Medio (penaliza errores grandes más que el MAE) |
| NLP | Procesamiento de Lenguaje Natural (técnica de IA para analizar textos) |
| GARCH | Modelo estadístico que mide cómo cambia la volatilidad (variabilidad) del precio en el tiempo |
| ARIMA | Modelo estadístico para predecir series temporales basándose en sus valores pasados |
| API | Interfaz de Programación de Aplicaciones (sistema que permite comunicar dos programas) |
| JSON | Formato de texto estructurado para intercambiar datos entre sistemas |
| WebSocket | Protocolo de comunicación que mantiene una conexión abierta para enviar datos en tiempo real |
| OB | Order Block (zona de precio donde hubo actividad institucional significativa) |
| ORB | Opening Range Breakout (ruptura del rango de precios de la apertura del mercado) |

## Estructura de criterios de aceptación

Cada criterio de aceptación debe ser comprensible sin conocimiento técnico previo. Evitar jerga de programación innecesaria. Usar frases como:

- "el sistema debe..." en lugar de "THE component SHALL..."
- "si ocurre X, entonces el sistema debe Y" en lugar de "IF X THEN THE component SHALL Y"
- Describir qué hace el sistema desde la perspectiva del usuario, no de la implementación

## Idioma

Todos los documentos de requerimientos deben redactarse en **español**.

## Preservación de nombres y términos en inglés

Los nombres propios de tecnologías, productos, protocolos, siglas técnicas y términos que se usan nativamente en inglés deben **mantenerse en inglés** tal como se escriben originalmente (ej. WebSocket, API, REST, OHLCV, Databento, throttle, stream). La explicación entre paréntesis se redacta en español, pero el término en sí no se traduce.

| ❌ Incorrecto | ✅ Correcto |
|---|---|
| Enchufe web para datos en tiempo real | WebSocket (protocolo que mantiene una conexión abierta para enviar datos en tiempo real) |
| La interfaz de programación descansa | La API REST (interfaz de consulta de datos por internet) |
| Flujo de datos | stream (flujo continuo de datos que llegan a medida que ocurren) |
