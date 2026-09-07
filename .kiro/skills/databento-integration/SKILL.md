# Databento Integration Skill

## Description

Guía técnica para la integración con Databento en TradeCore MVP. Cubre el cliente Historical (descarga de OHLCV), el cliente Live (streaming en tiempo real), symbology de contratos continuos, schemas de datos, y manejo de errores. Palabras clave: databento, OHLCV, cliente histórico, cliente live, symbology, NQ, futures, market data, ingesta de datos, ohlcv-1m, GLBX.MDP3, continuous contract.

## Scope (MVP only)

- **Dataset:** GLBX.MDP3 (CME Globex)
- **Symbol:** NQ.c.0 (E-mini Nasdaq-100 continuous front-month)
- **Schema:** ohlcv-1m (velas de 1 minuto)
- **stype_in:** 'continuous'

## When to use

Consulta los archivos de `references/` cuando estés trabajando en:

| Tarea | Archivo de referencia |
|-------|----------------------|
| Descargar datos históricos OHLCV | `references/historical-client.md` |
| Conectar al stream live de Databento | `references/live-client.md` |
| Resolver símbolos, entender schemas OHLCV, interpretar campos | `references/symbology-and-schemas.md` |
| Manejar errores, reconexión, heartbeat, flags | `references/error-handling-flags.md` |
| Validar que NQ.c.0 resuelve correctamente | `scripts/check_symbol.py` |

## Critical rules

- API key via `DATABENTO_API_KEY` environment variable — NEVER hardcode.
- Historical API first (Fase A), Live API second (Fase B) — see steering/databento-architecture.md.
- Do NOT add MBP-10, OPRA, options, Greeks/GEX references to this skill.
- Prices in raw OHLCV records are int64 (1 unit = 1e-9). The Python SDK provides `pretty_price` for converted float values.
