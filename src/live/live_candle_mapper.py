"""T1.2 — Transformacion de un record/mensaje Live `ohlcv-1m` -> `Candle`.

Objetivo (frontera estricta): SOLO transformar campos de un record Databento `ohlcv-1m`
(`ts_event`, `open`, `high`, `low`, `close`, `volume`) al contrato existente `Candle`.

REUSO SIN DUPLICACION (regla de la Task y de gobernanza):
La conversion de precio y de timestamp que usa Historical vive dentro del connector
protegido `src/connectors/databento_connector.py`:
- `_convert_price(value)` — helper de precio a nivel de modulo (importable).
- `DabentoConnector._record_to_candle(record)` — staticmethod que hace el mapeo COMPLETO
  record->Candle, incluyendo la conversion de `ts_event` (ns) -> datetime UTC.

La logica de timestamp (`ts_event` ns -> datetime UTC = APERTURA del intervalo) NO esta
expuesta como helper independiente; esta embebida en `_record_to_candle`. Para NO duplicar
esa logica (prohibido por la regla de la Task) y NO editar el connector protegido, este
mapper DELEGA en `DabentoConnector._record_to_candle` (import + llamada, sin edicion). Asi
Live y Historical usan EXACTAMENTE la misma conversion de precio Y de timestamp.

Semantica de timestamp (verificada en T0.1, Design B.14 [VERIFICADO — CANDLE TIMESTAMP
SEMANTICS]): `ts_event` = inicio inclusivo / APERTURA del intervalo de agregacion de 1 min.
El `Candle.timestamp` resultante representa por tanto la apertura del minuto.

FRONTERA — lo que este modulo NO hace (permanece fuera de T1.2):
- NO decide si la vela esta cerrada/finalizada. LIVE_BAR_FINALITY permanece OPEN: recibir un
  record `ohlcv-1m` NO se codifica aqui como equivalente a "vela cerrada".
- NO filtra parciales, NO deduplica, NO ordena, NO decide elegibilidad.
- NO mantiene estado de conexion, NO reconecta, NO hace subscribe_live(), NO warm-up,
  NO LIVE_SYNCED, NO gaps, NO conecta Databento real.

INTEGRATION_DEFERRED_TO_WAVE_6 = YES (la union fisica con el connector es Wave 6).
"""

from __future__ import annotations

from src.connectors.databento_connector import DabentoConnector
from src.schemas.candle import Candle


def live_record_to_candle(record: object) -> Candle | None:
    """Convierte un record Live `ohlcv-1m` en un `Candle`, reutilizando la conversion Historical.

    Entrada: un record con los atributos `ts_event`, `open`, `high`, `low`, `close`, `volume`
    (el mismo shape que `OHLCVMsg` que produce Historical). Se aceptan records sinteticos que
    expongan esos atributos.

    Salida: un `Candle` (timestamp = apertura del intervalo, OHLCV convertidos) o `None` si el
    record no puede convertirse (misma politica tolerante que Historical).

    NOTA de trazabilidad: el valor mapeado a `Candle.timestamp` proviene del campo `ts_event`
    del record, interpretado como apertura del intervalo (evidencia oficial T0.1). La conversion
    exacta la realiza `DabentoConnector._record_to_candle` (reuso, sin duplicacion).

    NOTA de finalidad: esta funcion NO afirma que el record recibido sea una vela finalizada;
    solo transforma sus campos. La decision de finalidad/elegibilidad NO pertenece a T1.2.
    """
    # Delegacion directa al mapeo existente de Historical: misma conversion de precio y de
    # timestamp, sin duplicar codigo y sin editar el componente protegido.
    return DabentoConnector._record_to_candle(record)
