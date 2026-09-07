# Requirements Document

## Introduction

TradeCore MVP 01 es una aplicación mínima cuyo único objetivo es demostrar que la cadena completa funciona de punta a punta:

**Databento (proveedor de datos de mercado) → precio OHLCV (apertura, máximo, mínimo, cierre y volumen) → gráfico de velas → cálculo matemático → señal BUY/SELL (compra/venta) → visualización sobre el gráfico.**

Nada más.

---

## Glossary

- **Databento**: Proveedor de datos de mercado que ofrece acceso a datos en tiempo real y datos históricos mediante API REST (interfaz de consulta de datos por internet) y WebSocket (protocolo que mantiene una conexión abierta para enviar datos en tiempo real). El dataset (conjunto de datos) utilizado en este MVP es GLBX.MDP3 (CME Globex).
- **OHLCV**: Datos de precio de un instrumento en un período: Open (apertura), High (máximo), Low (mínimo), Close (cierre) y Volume (volumen negociado).
- **NASDAQ-100**: Índice bursátil de referencia que agrupa las 100 mayores empresas tecnológicas del mercado estadounidense. Es el mercado que se está analizando.
- **NQ (E-mini Nasdaq-100)**: Contrato de futuros sobre el índice NASDAQ-100, negociado en CME Globex (mercado electrónico de futuros). Es el instrumento real que el sistema consume desde Databento.
- **Vela (Candlestick)**: Representación gráfica de los datos OHLCV de un período de tiempo. En este MVP, cada vela representa 1 minuto.
- **SMA (Simple Moving Average — Media Móvil Simple)**: Promedio del precio de cierre de las últimas N velas completadas.
- **Señal BUY/SELL**: Indicación visual de compra (BUY) o venta (SELL) generada cuando se cumple una condición matemática al cierre de una vela.
- **Throttle (límite de renderizado)**: Frecuencia máxima con la que el gráfico se redibuja en pantalla (1 vez por segundo), independiente de la frecuencia con la que llegan datos.
- **WebSocket**: Protocolo de comunicación que mantiene una conexión abierta para enviar datos en tiempo real, sin necesidad de que el sistema los solicite repetidamente.
- **API REST**: Interfaz de consulta de datos por internet que permite solicitar información bajo demanda.
- **API key**: Clave secreta de acceso que identifica al usuario ante un servicio externo.
- **Stream**: Flujo continuo de datos que llegan a medida que ocurren en el mercado.

---

## Requirements

---

### RF-01: Conexión a Databento y obtención de datos del NQ

**Qué:** El sistema se conecta a Databento (proveedor de datos de mercado) y recibe velas OHLCV (apertura, máximo, mínimo, cierre, volumen) de 1 minuto del futuro E-mini Nasdaq-100 (NQ), ya agregadas por Databento mediante el schema `ohlcv-1m`. El instrumento utilizado es NQ.c.0 (contrato continuo front-month) del dataset GLBX.MDP3 (datos de CME Globex). El sistema NO construye velas localmente a partir de trades — recibe las velas completas directamente de Databento.

**Criterios de aceptación:**

1. Cuando el sistema arranca, debe cargar velas históricas de 1 minuto (mínimo 50 velas) del símbolo NQ.c.0 usando la API Historical (interfaz de consulta de datos por internet) de Databento con la API key (clave secreta de acceso) configurada como variable de entorno.
2. Cuando Databento completa una vela de 1 minuto y la entrega al sistema (ya sea vía streaming live o simulación de datos históricos), el sistema debe almacenarla en memoria con todos sus valores OHLCV intactos (timestamp de origen, apertura, máximo, mínimo, cierre, volumen).
3. Cuando la vela de 1 minuto llega al sistema como completada por Databento, el gráfico debe actualizarla visualmente y el sistema debe evaluar si se genera una señal (ver RF-03).
4. El sistema debe operar en dos modos secuenciales: **Fase A (simulación)** — reproduce velas históricas a través del pipeline a 1 vela/segundo para validar la cadena completa sin suscripción live; **Fase B (live)** — recibe velas en tiempo real de Databento via API Live (streaming) una vez aprobada la Fase A.

**Nota:** Databento solo entrega un registro `ohlcv-1m` cuando hay al menos un trade en ese intervalo de 1 minuto — si no hay actividad de mercado, no se genera registro.

---

### RF-02: Gráfico de velas japonesas con refresco visual limitado

**Qué:** El sistema muestra un gráfico de velas japonesas (representación gráfica de precio con cuerpo y mechas) de 1 minuto con los datos OHLCV (apertura, máximo, mínimo, cierre, volumen) recibidos de Databento, actualizándose visualmente con una frecuencia máxima de 1 vez por segundo (throttle — límite de renderizado).

**Criterios de aceptación:**

1. Cuando hay datos disponibles, el sistema debe renderizar (dibujar en pantalla) un gráfico de velas con eje vertical (precio) y eje horizontal (tiempo).
2. Las actualizaciones visuales del gráfico deben enviarse al navegador con un refresco máximo de 1 vez por segundo (throttle), independientemente de la frecuencia con la que lleguen velas completadas del backend.
3. Cuando una nueva vela de 1 minuto completada llega desde Databento (o desde la simulación de datos históricos), el gráfico debe mostrarla con sus valores OHLCV definitivos.

---

### RF-03: Regla matemática para generar señales

**Qué:** El sistema aplica un cálculo matemático simple sobre los precios y genera una señal BUY (compra) o SELL (venta) cuando se cumple la condición.

**Regla inicial:** Cruce de dos medias móviles simples — SMA (Simple Moving Average, promedio de los últimos N cierres). Cuando la SMA rápida (9 períodos) cruza por encima de la SMA lenta (21 períodos) → señal BUY. Cuando cruza por debajo → señal SELL.

**Criterios de aceptación:**

1. Cuando una vela de 1 minuto se completa (cierre definitivo), el sistema debe recalcular las SMAs (medias móviles simples) usando solo velas completadas y evaluar si ocurrió un cruce.
2. Cuando la SMA(9) cruza de abajo hacia arriba la SMA(21) en una vela completada, el sistema debe generar una señal BUY (compra) asociada a esa vela.
3. Cuando la SMA(9) cruza de arriba hacia abajo la SMA(21) en una vela completada, el sistema debe generar una señal SELL (venta) asociada a esa vela.
4. Si no hay suficientes velas completadas para calcular ambas SMAs (mínimo 21 velas), el sistema no debe generar señales.
5. El cálculo de señales NUNCA se ejecuta durante el refresco visual de la vela en formación — solo al cierre de una vela de 1 minuto. Esto garantiza que una señal, una vez emitida, no cambia ni desaparece.

---

### RF-04: Visualización de señales sobre el gráfico

**Qué:** Las señales BUY/SELL (compra/venta) se muestran como marcadores visuales directamente sobre el gráfico de velas.

**Criterios de aceptación:**

1. Cuando se genera una señal BUY (compra), el sistema debe mostrar un marcador verde (flecha o triángulo) debajo de la vela correspondiente.
2. Cuando se genera una señal SELL (venta), el sistema debe mostrar un marcador rojo (flecha o triángulo) encima de la vela correspondiente.

---

## Requisitos No Funcionales

---

### RNF-01: Reconexión automática

Si la conexión con Databento (proveedor de datos de mercado) se pierde, el sistema debe reintentar la conexión automáticamente (3 intentos con espera creciente: 1 s, 2 s, 4 s). Si falla tras los 3 intentos, debe mostrar un aviso visual indicando que no hay conexión y la hora del último dato recibido.

---

### RNF-02: Simplicidad

El código debe ser comprensible y modificable por una persona. No se requiere arquitectura compleja ni abstracciones innecesarias.

---

## Supuestos

1. Se dispone de una API key (clave secreta de acceso) válida de Databento con acceso al dataset GLBX.MDP3 (CME Globex) que incluye el futuro E-mini Nasdaq-100 (NQ). La API key se configura exclusivamente como variable de entorno (`DATABENTO_API_KEY`).
2. El sistema se ejecuta en un entorno local (un solo usuario, un solo proceso).
3. Los datos pueden tener retraso (delayed) según el plan de Databento; esto es aceptable para validar la cadena.
4. La regla SMA(9)/SMA(21) es solo el punto de partida para validar la cadena; no pretende ser rentable.
5. El timeframe (período de cada vela) es fijo en 1 minuto para este MVP. Se usa el schema `ohlcv-1m` de Databento — las velas llegan ya agregadas, no se construyen localmente a partir de trades.
6. Databento entrega un registro `ohlcv-1m` solo cuando hay al menos un trade en el intervalo. Si no hay actividad, no se genera vela para ese minuto.
7. La Fase A (simulación con datos históricos) se implementa y valida ANTES de activar la Fase B (streaming live), que requiere el plan Standard de Databento.

---

## Riesgos Técnicos

| Riesgo | Mitigación |
|--------|-----------|
| Rate limits (límites de solicitudes) de Databento según plan contratado | Verificar límites del plan antes de implementar; créditos gratis cubren Historical |
| El símbolo NQ cambia cada trimestre (roll del contrato de futuros) | NQ.c.0 con stype_in='continuous' resuelve automáticamente el rollover |
| Datos delayed (retrasados) vs. real-time (tiempo real) según el plan | Aceptable para el MVP; no afecta la validación de la cadena |
| La conexión live de Databento puede desconectarse | Reconexión automática (RNF-01); aviso visual al usuario |
| La API Historical de Databento podría no tener datos para el período solicitado | Aceptar lo que devuelva; si hay < 21 velas, deshabilitar señales hasta acumular más |
| El plan Standard de Databento (live) tiene costo mensual | Fase A valida todo sin costo; Fase B solo se activa tras aprobación manual |

---

## Fuera de Alcance

- Machine Learning / IA / modelos predictivos
- Order Book (libro de órdenes)
- TradingView
- QQQ / NDX / opciones / Greeks / GEX
- Backtesting (evaluación histórica)
- Retraining (reentrenamiento de modelos)
- Risk Management (gestión de riesgo)
- Ejecución automática de órdenes
- Brokers (intermediarios de mercado)
- Usuarios / autenticación / multi-tenancy (múltiples usuarios)
- Microservicios / arquitectura distribuida
- Notificaciones
- Persistencia a largo plazo
- Cualquier funcionalidad no listada en RF-01 a RF-04
