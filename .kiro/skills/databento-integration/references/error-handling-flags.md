# Error Handling, Flags y Reconexión — Referencia para TradeCore MVP

## Tipos de errores del Live Gateway

### Errores fatales (cierran la sesión)

| Código | Nombre | Acción |
|--------|--------|--------|
| 1 | AUTH_FAILED | NO reintentar. Verificar API key. |
| 2 | API_KEY_DEACTIVATED | NO reintentar. Contactar Databento. |
| 3 | CONNECTION_LIMIT_EXCEEDED | NO reintentar inmediatamente. Cerrar otras sesiones. |
| 5 | INVALID_SUBSCRIPTION | NO reintentar con mismos parámetros. Verificar dataset/schema/symbols. |
| 6 | INTERNAL_ERROR | Reintentar con backoff. Error del gateway. |
| 8 | REPLAY_DATA_AGED_OUT | Datos de replay ya no disponibles. Reiniciar sin `start`. |

### Errores no fatales (sesión continúa)

| Código | Nombre | Acción |
|--------|--------|--------|
| 4 | SYMBOL_RESOLUTION_FAILED | Loguear warning. El símbolo no se encontró, pero la sesión sigue para otros símbolos. |
| 7 | SKIPPED_RECORDS_AFTER_SLOW_READING | Loguear warning. El gateway saltó registros porque el cliente no procesaba suficientemente rápido. |

## System Messages

| Código | Nombre | Significado |
|--------|--------|-------------|
| 0 | HEARTBEAT | Conexión viva, sin datos nuevos. |
| 1 | SUBSCRIPTION_ACK | Suscripción confirmada por el gateway. |
| 2 | SLOW_READER_WARNING | El cliente está atrasándose respecto a datos real-time. |
| 3 | REPLAY_COMPLETED | El replay intraday alcanzó datos real-time. |
| 4 | END_OF_INTERVAL | Todos los registros del intervalo fueron publicados. |

## Detección de heartbeat en Python

```python
if isinstance(record, db.SystemMsg):
    if record.is_heartbeat:
        # Conexión viva, no hay datos nuevos que enviar
        pass
    elif record.code == 3:  # REPLAY_COMPLETED
        # El replay histórico terminó, ahora llegan datos real-time
        pass
```

## Detección de errores en Python

```python
if isinstance(record, db.ErrorMsg):
    if record.code == 1:  # AUTH_FAILED
        logger.error(f"Autenticación fallida: {record.err}")
        # Mostrar error en frontend, NO reintentar
    elif record.code == 4:  # SYMBOL_RESOLUTION_FAILED
        logger.warning(f"Símbolo no resuelto: {record.err}")
        # Continuar operando
    elif record.code == 7:  # SKIPPED_RECORDS
        logger.warning(f"Registros saltados: {record.err}")
        # Aceptar gaps, continuar
    else:
        logger.error(f"Error Databento [{record.code}]: {record.err}")
```

## Estrategia de reconexión para el MVP

### Con reconnect_policy del SDK (recomendado)

```python
client = db.Live(reconnect_policy="reconnect")
```

El SDK maneja reconexión automáticamente. Usa `add_reconnect_callback` para detectar gaps.

### Manual (si se necesita control fino)

```python
MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2, 4]  # Exponencial

for attempt in range(MAX_RETRIES):
    try:
        client = db.Live()
        client.subscribe(...)
        break
    except Exception as e:
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(BACKOFF_SECONDS[attempt])
        else:
            # Mostrar banner "Conexión perdida" en frontend
            raise
```

## Detección de conexión muerta (hung connection)

Regla de Databento:
- Si no se recibe NADA del gateway durante `heartbeat_interval_s + 10 segundos` → la conexión está muerta.
- Desconectar y reconectar.

```python
# Heartbeat interval por defecto: 30s
# Timeout de detección: 30 + 10 = 40 segundos sin datos → conexión muerta
client = db.Live(heartbeat_interval_s=30)
```

## Rate limits del gateway

- Máximo 10 conexiones simultáneas por dataset por equipo (plan Standard).
- Máximo 5 conexiones entrantes por segundo desde la misma IP.
- Máximo 10 suscripciones por segundo por sesión (no se rechazan, se retrasan).

Para el MVP (1 conexión, 1 suscripción): no hay riesgo de rate limiting.

## Mantenimiento programado del gateway

- CME Globex: **Sábado 02:15 CT** — todos los clientes se desconectan.
- El cliente debe manejar reconexión automática tras el mantenimiento.

## Flags en registros OHLCV

El campo `flags` es un bit field. Para el MVP con ohlcv-1m normalmente no requiere inspección especial — los datos llegan limpios como barras completadas.

## Fuente

- [Databento Live API — Error Detection](https://databento.com/docs/api-reference-live/basics/error-detection)
- [Databento Live API — Recovering After Disconnection](https://databento.com/docs/api-reference-live/basics/recovering-after-a-disconnection)
- [Databento Live API — System Messages](https://databento.com/docs/api-reference-live/basics/system-messages)
