# TradeCore MVP — Arquitectura Databento

Decisiones arquitectónicas permanentes para la integración con Databento en el MVP.

---

## Fuente de datos

- **Market Data Provider único:** Databento (reemplaza cualquier referencia anterior a Tradovate WebSocket).
- **Dataset:** GLBX.MDP3 (CME Globex Market Data Platform 3.0).
- **Instrumento:** NQ.c.0 con `stype_in='continuous'` (contrato continuo E-mini Nasdaq-100, líder por proximidad de vencimiento/calendario).
- **Schema:** `ohlcv-1m` (velas de 1 minuto agregadas por Databento). Decisión cerrada — no cambiar a `ohlcv-1s` ni `trades` sin aprobación explícita del usuario.

## Decisión de símbolo — NQ.c.0 vs. alternativas

- `NQ.c.0` usa la regla de roll por calendario (expiration) — el índice `.0` selecciona el contrato front-month (más próximo a vencer).
- NO usar `NQ.v.0` (roll por volumen) ni `NQ.n.0` (roll por open interest) para el MVP.
- NO hardcodear contratos específicos (NQU6, NQZ6, etc.) — Databento resuelve automáticamente el rollover.
- Si se encuentra razón técnica para cambiar, presentar como nota separada al usuario — no modificar directamente.

## Arquitectura de flujo de datos

```
Databento (Historical API / Live API)
    → DabentoConnector (Market Data Connector — componente de backend)
    → CandleBuffer (estructura en memoria, NO base de datos)
    → SignalEngine (SMA 9/21 crossover)
    → ThrottledPusher → WebSocket → Dashboard (JS plano)
```

## Reglas de separación

- El **frontend NUNCA se conecta directamente a Databento**. Toda comunicación con Databento pasa exclusivamente por el backend (DabentoConnector).
- El backend expone datos al frontend vía WebSocket interno (`/ws/chart`).

## Protocolo de conexión — distinción explícita

- **Backend ↔ Databento:** socket TCP crudo con protocolo binario propio de Databento (DBN — Databento Binary Encoding), encapsulado por el cliente oficial Python (`db.Live()`). **NO es un WebSocket.** El SDK maneja internamente la conexión TCP, la autenticación challenge-response, y la decodificación binaria.
- **Backend ↔ Navegador:** WebSocket estándar (FastAPI WebSocket en `/ws/chart`), gestionado por `ThrottledPusher`. Este es el **único WebSocket real** de todo el sistema.
- **Regla:** Ningún componente presente o futuro debe tratar la conexión a Databento como si fuera un WebSocket. Son protocolos fundamentalmente distintos.

## Seguridad de credenciales

- API key exclusivamente vía variable de entorno: `DATABENTO_API_KEY`.
- NUNCA almacenar en código fuente, Git, commits, README, logs, tests, Steering, Skills, Specs, capturas ni mensajes.
- El cliente Python de Databento usa `DATABENTO_API_KEY` automáticamente si no se pasa `key` al constructor.

## Secuencia obligatoria: Historical primero, Live después

### Fase A — Validación con datos históricos (usage-based, créditos gratis)
1. Resolver símbolo NQ.c.0 via `db.Historical().symbology.resolve()` (único método válido para este propósito).
2. Descargar muestra de OHLCV histórico via `client.timeseries.get_range()`.
3. Pipeline completo: ingesta → SignalEngine (SMA) → gráfico de velas.
4. Simular "tiempo real" reproduciendo velas históricas a 1 actualización/segundo.

### Fase B — Streaming live (requiere plan Standard activo)
5. Solo después de que Fase A funcione sin bugs conocidos.
6. Reemplazar fuente simulada por cliente Live real de Databento.
7. Desplegar a AWS.

**GATE explícito entre Fase A y Fase B:** requiere aprobación manual del usuario y activación del plan Standard antes de continuar.

## Manejo de conexión (cliente oficial)

- **Autenticación:** challenge-response automática del SDK. Usa `DATABENTO_API_KEY` como variable de entorno.
- **Heartbeat:** configurable via `heartbeat_interval_s` (default 30s del gateway). Si no se recibe nada en intervalo + 10s → conexión considerada muerta. Nota: el mínimo configurable permitido por Databento es 5 segundos — usar solo si en el futuro se requiere detección de desconexión más agresiva.
- **Reconexión:** `reconnect_policy="reconnect"` en el constructor `db.Live()`. Para MVP, implementar lógica manual: 3 intentos con backoff exponencial (1s, 2s, 4s).
- **Errores fatales** (AUTH_FAILED, API_KEY_DEACTIVATED): NO reintentar. Mostrar error al usuario.
- **Errores no fatales** (SYMBOL_RESOLUTION_FAILED): loguear, continuar con datos disponibles.
- **Cierre controlado:** `client.stop()` para shutdown graceful, `client.terminate()` para cierre inmediato.

## Precios y campo de precio

- Los campos `open`, `high`, `low`, `close` en el schema OHLCV son int64 donde 1 unidad = 1e-9. El SDK Python convierte automáticamente con `pretty_price`.
- Verificar en connectivity spike cómo el SDK maneja la conversión para el schema `ohlcv-1m`.

## Fuera de alcance del MVP (fases futuras del roadmap)

Order Book (MBP-10), TradingView signals, OPRA, opciones, Greeks/GEX, Quant_Model híbrido, Backtesting avanzado, Risk_Gate.

---

## Fuente de referencia

Documentación oficial: [databento.com/docs](https://databento.com/docs). Ante conflicto entre suposiciones previas y la documentación oficial, la documentación oficial prevalece para capacidades técnicas del proveedor. Las decisiones de alcance y negocio del MVP son del usuario y no se cambian automáticamente.
