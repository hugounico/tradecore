# Symbology y Schemas — Referencia para TradeCore MVP

## Tipos de Symbology en Databento

| stype_in | Descripción | Ejemplo |
|----------|-------------|---------|
| raw_symbol | Símbolo original del exchange | "NQZ5" (contrato dic 2025) |
| instrument_id | ID numérico del instrumento | 12345 |
| parent | Grupo de instrumentos relacionados | "NQ.FUT" (todos los contratos NQ) |
| continuous | Contrato continuo con regla de roll | "NQ.c.0" (front-month por calendario) |

## Continuous Contract Symbology (la que usa TradeCore)

Formato: `{ROOT}.{RULE}.{RANK}`

- **ROOT:** Símbolo raíz del producto (ej: "NQ" para E-mini Nasdaq-100)
- **RULE:** Regla de roll:
  - `c` = calendar (por proximidad de vencimiento) ← **USAMOS ESTA**
  - `v` = volume (mayor volumen de trading)
  - `n` = open interest (mayor interés abierto)
- **RANK:** Índice del contrato:
  - `0` = lead month (contrato de mayor rango) ← **USAMOS ESTE**
  - `1` = segundo contrato
  - `2` = tercer contrato

### TradeCore MVP usa: `NQ.c.0`

Esto resuelve automáticamente al contrato E-mini Nasdaq-100 front-month activo. Cuando ocurre el rollover trimestral, Databento cambia automáticamente al nuevo contrato — no se requiere código manual.

### Comparativa de alternativas (NO usar para MVP)

| Símbolo | Regla | Selecciona |
|---------|-------|-----------|
| NQ.c.0 | Calendar | Más próximo a vencer (front-month) ✓ |
| NQ.v.0 | Volume | Mayor volumen (puede ser distinto al front-month) |
| NQ.n.0 | Open Interest | Mayor OI (generalmente el front-month) |
| NQ.FUT | Parent | TODOS los contratos NQ mezclados ✗ |

**Importante:** `NQ.FUT` (parent symbology) entrega trades de TODOS los contratos NQ activos mezclados — corrompe la construcción de velas. Por eso usamos continuous.

## SymbolMappingMsg

Cuando usas continuous symbology en el Live client, Databento envía un `SymbolMappingMsg` al inicio indicando a qué contrato real resuelve:

```python
if isinstance(record, db.SymbolMappingMsg):
    print(f"{record.stype_in_symbol} → {record.stype_out_symbol}")
    # Ejemplo: "NQ.c.0" → "NQH6" (marzo 2026)
```

## Schema OHLCV — Detalles técnicos

### Sufijos disponibles

| Schema | Intervalo | Uso en TradeCore |
|--------|-----------|------------------|
| ohlcv-1s | 1 segundo | NO — demasiados datos |
| ohlcv-1m | 1 minuto | SÍ — schema del MVP |
| ohlcv-1h | 1 hora | NO |
| ohlcv-1d | 1 día | NO |

### Campos del registro OHLCV

| Campo | Tipo | Descripción |
|-------|------|-------------|
| ts_event | uint64 (nanosegundos UNIX) | Inicio del intervalo |
| open | int64 (×1e-9 para float) | Precio de apertura |
| high | int64 (×1e-9 para float) | Precio máximo |
| low | int64 (×1e-9 para float) | Precio mínimo |
| close | int64 (×1e-9 para float) | Precio de cierre |
| volume | uint64 | Volumen total en el intervalo |
| instrument_id | uint32 | ID numérico del instrumento |
| rtype | uint8 | Tipo de registro (33 = ohlcv-1m) |

### Convenciones importantes

- `ts_event` marca el **inicio** del intervalo (no el cierre).
- Si no hay trades en un intervalo → NO se genera registro.
- Basado en UTC — no en sesiones del exchange.
- El SDK Python convierte precios automáticamente en `.to_df()` y con `.pretty_*` properties.

### Conversión de precios (raw → float)

```python
# Opción 1: DataFrame (conversión automática)
df = data.to_df()  # Columnas open/high/low/close ya son float

# Opción 2: Registro individual
record.pretty_open   # Float convertido
record.pretty_close  # Float convertido

# Opción 3: Manual
price_float = record.open / 1_000_000_000  # Dividir por 1e9
```

## Resolución de símbolos (Reference API)

Para verificar que NQ.c.0 resuelve correctamente:

```python
import databento as db

client = db.Reference()
result = client.symbology.resolve(
    dataset="GLBX.MDP3",
    symbols="NQ.c.0",
    stype_in="continuous",
    stype_out="raw_symbol",
    start_date="2025-01-06",
    end_date="2025-01-06",
)
print(result)  # Muestra a qué raw_symbol resuelve NQ.c.0
```

## Fuente

- [Databento Standards — Symbology](https://databento.com/docs/standards-and-conventions/symbology)
- [Databento Schemas — OHLCV](https://databento.com/docs/schemas-and-data-formats/ohlcv)
- [Blog: Continuous contract symbology for live data](https://databento.com/blog/live-continuous-contract-symbology)
