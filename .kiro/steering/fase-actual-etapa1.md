---
inclusion: always
---

# Fase Actual — Etapa 1: Componentes Protegidos

Lista TEMPORAL de componentes protegidos para el estado actual del proyecto.
Se rige por el principio permanente definido en `proteccion-componentes.md`.
**Este archivo se actualiza cuando cambia la fase del proyecto.**

---

## Estado actual

- **Fase A:** pipeline historico simulado, validado y desplegado en produccion en AWS ECS
  (MODE=simulation). Es lo que se protege.
- **Etapa 1 (proxima, aun NO iniciada):** mejora matematica — stop-loss dinamico basado
  en ATR (Average True Range — medida de volatilidad del precio), filtro de volumen, y
  comparacion formal contra el baseline de TradingView.
- **Regla de extension:** la Etapa 1 EXTIENDE la logica validada (agrega ATR y filtro de
  volumen ENCIMA del baseline SMA), NO la reemplaza.
- **Fase B (Task 12 del spec tradecore-mvp):** streaming en vivo. Sigue BLOQUEADA y
  pendiente de aprobacion por separado. Su GATE se mantiene intacto; este archivo no lo
  altera.

---

## NIVEL 1 — Archivos protegidos

Requieren aprobacion explicita del usuario antes de cualquier modificacion. No estan
prohibidos para siempre — requieren aprobacion previa.

- `src/connectors/databento_connector.py` — SOLO la parte de carga historica de Fase A.
  (El streaming en vivo de Fase B sigue sin implementar y bloqueado por separado.)
- `src/pipeline/candle_buffer.py`
- `src/pipeline/simulation_replay.py`
- `src/engine/signal_engine.py`
- `src/api/throttled_pusher.py`
- `src/api/app.py`
- `dashboard/index.html`
- Toda la suite de tests existente (unit, integration, property-based).

---

## NIVEL 2 — Comportamientos y decisiones protegidas

Independiente de que archivo los implemente. Si modificas cualquier archivo (incluido uno
NUEVO) y eso altera alguno de estos puntos, se trata como modificacion a un componente
protegido y requiere aprobacion previa.

- La logica de calculo **SMA(9)/SMA(21)** como baseline (version de referencia contra la
  que se compara la Etapa 1). La Etapa 1 la EXTIENDE, no la reemplaza.
  (SMA = Simple Moving Average — promedio movil simple de los ultimos N precios de cierre.)
- Las reglas de generacion de senales **BUY/SELL** actualmente validadas.
- El flujo de procesamiento historico y de simulacion (**SimulationReplay**).
- La sincronizacion y secuencia de velas historicas.
- El **protocolo actual de mensajes WebSocket** con el dashboard.
  (WebSocket = canal bidireccional persistente entre backend y navegador.)
- El mecanismo de **throttle visual** (maximo 1 actualizacion por segundo).
  (Throttle = limitar la frecuencia de actualizaciones enviadas al dashboard.)
- Las decisiones de configuracion de Fase A: dataset **GLBX.MDP3**, simbolo **NQ.c.0**
  (contrato continuo), schema **ohlcv-1m**, **MODE=simulation**, periodos **SMA(9)/SMA(21)**,
  intervalo de throttle de **1 segundo**.
- Las propiedades de comportamiento cubiertas por los tests existentes de Fase A
  (**Property 1 a 6** del `design.md` de tradecore-mvp).

---

## Que NO cubre este archivo

- No bloquea la creacion de codigo nuevo e independiente para la Etapa 1.
- No aplica a Fase B (Task 12), que sigue bloqueada por separado.
- No incluye limpieza de Steering — pospuesta hasta despues de completar la Etapa 1.
