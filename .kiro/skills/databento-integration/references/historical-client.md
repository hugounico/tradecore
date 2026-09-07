# Databento Historical Client — Referencia para TradeCore MVP

## Instalación

```bash
pip install -U databento
```

Requiere Python 3.10+.

## Autenticación

```python
import databento as db

# Opción 1: variable de entorno DATABENTO_API_KEY (recomendado)
client = db.Historical()

# Opción 2: pasar key explícitamente (solo para desarrollo local)
client = db.Historical(key="YOUR_API_KEY")
```

## Obtener OHLCV de 1 minuto para NQ.c.0

```python
import databento as db

client = db.Historical()

data = client.timeseries.get_range(
    dataset="GLBX.MDP3",
    symbols="NQ.c.0",
    stype_in="continuous",
    schema="ohlcv-1m",
    start="2025-01-06T14:30:00",  # ISO 8601 UTC
    end="2025-01-06T15:30:00",
)

# Convertir a DataFrame de pandas
df = data.to_df()
```

## Parámetros de get_range()

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| dataset | str | ID del dataset (ej: "GLBX.MDP3") |
| symbols | str o list | Símbolo(s) a consultar |
| stype_in | str | Tipo de symbology: "continuous", "raw_symbol", "parent", "instrument_id" |
| schema | str | Schema de datos: "ohlcv-1m", "ohlcv-1s", "trades", etc. |
| start | str/datetime/pd.Timestamp | Inicio del rango (inclusive), ISO 8601 UTC |
| end | str/datetime/pd.Timestamp | Fin del rango (exclusive) |
| limit | int (optional) | Máximo de registros a devolver |

## Campos del schema ohlcv-1m

| Campo | Tipo raw | Descripción |
|-------|----------|-------------|
| ts_event | uint64 (ns) | Timestamp de inicio del intervalo de 1 minuto (nanosegundos UNIX) |
| open | int64 | Precio de apertura (1 unidad = 1e-9, dividir para obtener float) |
| high | int64 | Precio máximo del intervalo |
| low | int64 | Precio mínimo del intervalo |
| close | int64 | Precio de cierre del intervalo |
| volume | uint64 | Volumen total negociado en el intervalo |
| instrument_id | uint32 | ID numérico del instrumento |

**Nota:** El SDK Python convierte automáticamente precios a float en `.to_df()`. Si se procesan registros individuales, usar `record.pretty_price` o dividir por 1e9.

## Convención de Databento para OHLCV

- `ts_event` marca el INICIO de cada intervalo.
- Si no hay trades en un intervalo, NO se genera registro (no hay barras vacías).
- Basado en UTC. Para sesiones por exchange, agregar manualmente desde granularidad menor.

## Pricing (Historical API)

- **Usage-based:** se cobra por tamaño de datos descargados (en formato binario original).
- **Créditos gratis:** cada cuenta nueva recibe $125 en créditos de datos históricos.
- **Sin licencia mensual** para datos históricos — solo pago por uso.
- Los mismos datos cuestan lo mismo independientemente del método de acceso (streaming, batch download, CSV, JSON).

## Fuente

[Databento API Reference — Historical](https://databento.com/docs/api-reference-historical)
