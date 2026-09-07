# Databento Live Client — Referencia para TradeCore MVP

## Crear cliente Live

```python
import databento as db

# Recomendado: usar DATABENTO_API_KEY env var
client = db.Live()

# Con configuración explícita para el MVP
client = db.Live(
    heartbeat_interval_s=30,          # Heartbeat cada 30 segundos
    reconnect_policy="reconnect",      # Reconexión automática del SDK
)
```

## Suscripción a ohlcv-1m para NQ.c.0

```python
client.subscribe(
    dataset="GLBX.MDP3",
    schema="ohlcv-1m",
    stype_in="continuous",
    symbols="NQ.c.0",
)
```

## Iteración sincrónica (recomendada para MVP single-process)

```python
for record in client:
    if isinstance(record, db.OHLCVMsg):
        print(f"Candle: O={record.pretty_open} H={record.pretty_high} "
              f"L={record.pretty_low} C={record.pretty_close} V={record.volume}")
    elif isinstance(record, db.ErrorMsg):
        print(f"Error: {record.err}")
    elif isinstance(record, db.SystemMsg):
        if record.is_heartbeat:
            pass  # Heartbeat normal
```

## Iteración asincrónica (para integración con FastAPI)

```python
async for record in client:
    if isinstance(record, db.OHLCVMsg):
        # Procesar vela
        ...
```

## Parámetros de Live()

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| key | DATABENTO_API_KEY env | API key de 32 caracteres |
| heartbeat_interval_s | 30 (gateway) | Intervalo de heartbeat en segundos (mínimo 5) |
| reconnect_policy | None | "reconnect" para reconexión automática |
| ts_out | False | Agregar timestamp de envío del gateway |

## Parámetros de subscribe()

| Parámetro | Requerido | Descripción |
|-----------|-----------|-------------|
| dataset | Sí | "GLBX.MDP3" para CME Globex |
| schema | Sí | "ohlcv-1m" para velas de 1 minuto |
| symbols | No (default ALL) | "NQ.c.0" para continuous front-month |
| stype_in | No (default raw_symbol) | "continuous" para contratos continuos |
| start | No | Para intraday replay (últimas 24h) |

## Intraday Replay

El Live API permite replay de las últimas 24 horas. Útil para recuperación tras desconexión:

```python
client.subscribe(
    dataset="GLBX.MDP3",
    schema="ohlcv-1m",
    stype_in="continuous",
    symbols="NQ.c.0",
    start="2025-01-06T14:30:00",  # Replay desde esta hora
)
```

## Ciclo de vida de una sesión

1. `db.Live()` — crear cliente
2. `client.subscribe(...)` — suscribirse (autenticación ocurre aquí)
3. `client.start()` — iniciar streaming (o usar `for record in client:` que auto-inicia)
4. Procesar registros
5. `client.stop()` — cierre graceful (procesa registros pendientes)
6. `client.terminate()` — cierre inmediato (descarta registros pendientes)

## Callbacks (alternativa a iteración)

```python
def handle_record(record: db.DBNRecord) -> None:
    if isinstance(record, db.OHLCVMsg):
        # Procesar vela...
        pass

client.add_callback(record_callback=handle_record)
client.start()
client.block_for_close(timeout=None)  # Bloquear hasta desconexión
```

## Reconnect callbacks

```python
def on_reconnect(last_ts, new_start_ts):
    print(f"Gap de datos: {last_ts} → {new_start_ts}")

client.add_reconnect_callback(reconnect_callback=on_reconnect)
```

## Pricing (Live API)

- **Plan Standard requerido:** licencia mensual flat-rate para datos live de CME.
- No activar hasta que Fase A (histórico) esté validada.
- El plan Standard permite hasta 10 conexiones simultáneas por dataset.

## Fuente

[Databento API Reference — Live](https://databento.com/docs/api-reference-live)
