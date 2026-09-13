"""
download_historical.py
=======================

Script de investigacion REPRODUCIBLE para descargar datos historicos OHLCV-1m
de NQ.c.0 (contrato continuo E-mini Nasdaq-100) desde Databento y persistirlos
en un unico Parquet maestro, SIN limpieza ni modificacion de valores.

IMPORTANTE (gobernanza del proyecto):
  - Esta es una descarga REAL con costo REAL (~USD 3.84 estimado). Autorizada.
  - NO se limpian, imputan, reordenan ni deduplican datos. Se guardan "tal cual".
  - NO se toca ningun componente protegido de Fase A. Este script es codigo nuevo
    e independiente (investigacion), no altera el pipeline productivo.
  - La API key JAMAS se imprime, loguea ni se guarda. Solo se lee de la variable
    de entorno DATABENTO_API_KEY (via .env con load_dotenv).

Metodo de persistencia oficial:
  get_range(...) devuelve un objeto DBNStore. Ese objeto tiene el metodo oficial
  .to_parquet(path) que escribe el Parquet directamente desde el DBN, sin que
  nosotros reconstruyamos velas ni hagamos matematica con pandas.

Uso:
  .\.venv\Scripts\python.exe scripts\download_historical.py
"""

import os
import sys
import json
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

import databento as db  # SDK oficial de Databento (v0.86.0)
from dotenv import load_dotenv  # carga variables desde el archivo .env


# ---------------------------------------------------------------------------
# Parametros de descarga (decisiones cerradas de Fase A / arquitectura Databento)
# ---------------------------------------------------------------------------
DATASET = "GLBX.MDP3"        # CME Globex Market Data Platform 3.0
SYMBOLS = ["NQ.c.0"]         # contrato continuo E-mini Nasdaq-100 (roll por calendario)
STYPE_IN = "continuous"      # el simbolo de entrada es un simbolo continuo
SCHEMA = "ohlcv-1m"          # velas OHLCV de 1 minuto agregadas por Databento
START = "2023-09-13T00:00:00Z"   # inicio del rango solicitado (inclusive)
END = "2026-09-12T00:00:00Z"     # fin del rango solicitado (exclusive)

SDK_VERSION = db.__version__  # debe ser 0.86.0

# Rutas de salida (relativas a la raiz del repo)
ROOT = Path(__file__).resolve().parent.parent  # carpeta TradeCore/
DATA_DIR = ROOT / "data" / "historical"
BASENAME = "nq_ohlcv1m_2023-09-13_2026-09-12"
PARQUET_PATH = DATA_DIR / f"{BASENAME}.parquet"
META_PATH = DATA_DIR / f"{BASENAME}.meta.json"

# Ubicacion de backup (unica y aprobada). Si no es accesible: detenerse solo ahi.
BACKUP_DIR = Path(r"D:\TradeCore-Backups\historical")

# Reporte de ejecucion (lo lee el agente; el script se ejecuta redirigido a un tmp)
REPORT_PATH = ROOT / "scripts" / "_download_report.txt"


def log(lines: list, msg: str) -> None:
    """Agrega una linea al reporte y tambien la imprime (stdout redirigido a tmp)."""
    print(msg)
    lines.append(msg)


def sha256_of_file(path: Path) -> str:
    """Calcula el SHA-256 sobre TODOS los bytes del archivo (lectura en bloques)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):  # bloques de 1 MB
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    report: list = []
    log(report, f"=== download_historical.py === SDK databento {SDK_VERSION}")

    # -- Cargar la API key desde .env SIN exponerla ---------------------------
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        log(report, "ERROR: DATABENTO_API_KEY no esta definida en el entorno.")
        REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
        return 1
    # Nunca imprimimos la key. Solo confirmamos presencia y su longitud.
    log(report, f"API key presente (longitud={len(api_key)}). No se muestra su valor.")

    # -- Crear carpeta de salida si falta -------------------------------------
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log(report, f"Carpeta de datos: {DATA_DIR}")

    # -- Cliente historico ----------------------------------------------------
    client = db.Historical(key=api_key)

    # -- Costo estimado (get_cost NO consume creditos) ------------------------
    estimated_cost = None
    try:
        estimated_cost = client.metadata.get_cost(
            dataset=DATASET,
            start=START,
            end=END,
            symbols=SYMBOLS,
            schema=SCHEMA,
            stype_in=STYPE_IN,
        )
        log(report, f"Costo ESTIMADO (get_cost, sin consumir creditos): USD {estimated_cost}")
    except Exception as e:  # noqa: BLE001 - informativo, no debe frenar la descarga
        log(report, f"AVISO: get_cost fallo (no critico): {e!r}")

    # -- DESCARGA REAL --------------------------------------------------------
    # get_range devuelve un DBNStore materializado en memoria/stream.
    log(report, "Iniciando descarga REAL con get_range ...")
    store = client.timeseries.get_range(
        dataset=DATASET,
        symbols=SYMBOLS,
        stype_in=STYPE_IN,
        schema=SCHEMA,
        start=START,
        end=END,
    )
    log(report, "Descarga completada (DBNStore recibido).")

    # -- Costo REAL si el SDK/respuesta lo expone -----------------------------
    real_cost = None
    for attr in ("cost", "billed_cost", "cost_usd"):
        if hasattr(store, attr):
            real_cost = getattr(store, attr)
            break
    # La metadata del store puede exponer el costo
    try:
        meta_obj = store.metadata
        for attr in ("cost", "billed_cost"):
            if real_cost is None and hasattr(meta_obj, attr):
                real_cost = getattr(meta_obj, attr)
    except Exception:  # noqa: BLE001
        pass
    log(report, f"Costo REAL reportado por el SDK: {real_cost!r} (None = no expuesto)")

    # -- PRESERVAR relacion continuo -> instrumento subyacente ----------------
    # map_symbols=True hace que to_parquet escriba una columna 'symbol' mapeando
    # instrument_id -> raw_symbol usando las mappings LOCALES del store (sin query
    # adicional facturable). Ademas registramos las mappings en un sidecar.
    symbology_info = {}
    try:
        # store.mappings: dict raw_symbol -> lista de intervalos {start,end,symbol}
        mappings = store.mappings
        symbology_info["mappings"] = mappings
        log(report, f"store.mappings disponible: {len(mappings)} entrada(s) (sin query extra).")
    except Exception as e:  # noqa: BLE001
        symbology_info["mappings_error"] = repr(e)
        log(report, f"AVISO: store.mappings no disponible: {e!r}")
    try:
        symbology_info["symbols"] = list(store.symbols)
    except Exception as e:  # noqa: BLE001
        symbology_info["symbols_error"] = repr(e)

    # -- PERSISTIR con el metodo OFICIAL to_parquet ---------------------------
    # price_type FLOAT -> convierte los int64 (1e-9) a precio real (conversion del SDK).
    # pretty_ts True    -> timestamps como datetime UTC.
    # map_symbols True  -> agrega columna 'symbol' (raw_symbol subyacente por vela).
    # NO reconstruimos velas ni tocamos valores: to_parquet escribe desde el DBN.
    if PARQUET_PATH.exists():
        log(report, f"AVISO: el Parquet ya existe, se sobrescribe: {PARQUET_PATH}")
    store.to_parquet(PARQUET_PATH)  # modo 'w' por defecto
    log(report, f"Parquet escrito: {PARQUET_PATH}")

    parquet_size = PARQUET_PATH.stat().st_size
    log(report, f"Tamano del Parquet (bytes): {parquet_size}")

    # -- VERIFICACION DE INTEGRIDAD (solo INSPECCION, no reescritura) ---------
    import pandas as pd  # lectura de inspeccion unicamente

    df = pd.read_parquet(PARQUET_PATH)  # abre para inspeccionar
    log(report, "1) El Parquet abre correctamente.")
    row_count = len(df)
    log(report, f"2) Filas reales: {row_count}")
    log(report, f"   Columnas: {list(df.columns)}")

    # Localizar la columna/indice de timestamp de evento
    ts = None
    ts_source = None
    if isinstance(df.index, pd.DatetimeIndex):
        ts = df.index.to_series().reset_index(drop=True)
        ts_source = "index (DatetimeIndex)"
    else:
        for col in ("ts_event", "ts_recv", "index"):
            if col in df.columns:
                ts = pd.to_datetime(df[col], utc=True)
                ts_source = col
                break
    if ts is None:
        log(report, "ERROR: no se encontro columna de timestamp (ts_event/index).")
        REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
        return 2
    # Asegurar tz UTC para comparaciones
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    else:
        ts = ts.dt.tz_convert("UTC")
    log(report, f"   Timestamp tomado de: {ts_source}")

    first_ts = ts.iloc[0]
    last_ts = ts.iloc[-1]
    log(report, f"3) Primer timestamp real: {first_ts.isoformat()}")
    log(report, f"4) Ultimo timestamp real: {last_ts.isoformat()}")

    # 5) Rango [START, END)
    start_bound = pd.Timestamp(START)
    end_bound = pd.Timestamp(END)
    in_range = bool((ts >= start_bound).all() and (ts < end_bound).all())
    n_before = int((ts < start_bound).sum())
    n_after = int((ts >= end_bound).sum())
    log(report, f"5) Todos dentro de [{START}, {END}): {in_range} "
                f"(antes={n_before}, en/despues del fin={n_after})")

    # 6) Orden cronologico no-decreciente
    diffs = ts.diff().dropna()
    non_decreasing = bool((diffs >= pd.Timedelta(0)).all())
    log(report, f"6) Orden cronologico no-decreciente: {non_decreasing}")

    # 7) Timestamps duplicados
    dup_count = int(ts.duplicated().sum())
    log(report, f"7) Timestamps duplicados: {dup_count}")

    # 8) Gaps temporales notables (INFORMATIVO, NO se corrige nada)
    #    Reportamos los mayores gaps y cuantos superan unos pocos minutos.
    threshold = pd.Timedelta(minutes=5)
    gaps_over = int((diffs > threshold).sum())
    top_gaps = diffs.sort_values(ascending=False).head(5)
    log(report, f"8) Gaps > {threshold}: {gaps_over} (informativo, sin corregir).")
    log(report, "   Mayores gaps (informativo):")
    for i, g in top_gaps.items():
        # posicion i corresponde al fin del gap
        end_g = ts.iloc[i]
        start_g = ts.iloc[i - 1] if i - 1 >= 0 else None
        log(report, f"     {g} entre {start_g} -> {end_g}")

    # -- SHA-256 sobre el Parquet ya escrito ----------------------------------
    file_sha256 = sha256_of_file(PARQUET_PATH)
    log(report, f"SHA-256 del Parquet: {file_sha256}")

    # dataset_id: la doc aprobada no define una convencion corta inequivoca ->
    # usamos provisionalmente el propio SHA-256 y anotamos la ambiguedad.
    dataset_id = file_sha256
    log(report, "dataset_id = file_sha256 (provisional; convencion corta no definida aun).")

    # -- warm-up (definicion segun periodos actuales) -------------------------
    fast_period, slow_period, crossover_state_requirement = 9, 21, 1
    atr_period, volume_period = 14, 20
    warmup_min = max(fast_period,
                     slow_period + crossover_state_requirement,
                     atr_period + 1,
                     volume_period)  # = 22
    log(report, f"warmup_min = {warmup_min}")

    # -- Escribir meta.json (SIN secretos) ------------------------------------
    meta = {
        "dataset": DATASET,
        "symbol": SYMBOLS[0],
        "stype_in": STYPE_IN,
        "schema": SCHEMA,
        "requested_start": START,
        "requested_end": END,
        "first_real_timestamp": first_ts.isoformat(),
        "last_real_timestamp": last_ts.isoformat(),
        "timezone": "UTC",
        "databento_sdk_version": SDK_VERSION,
        "download_datetime_utc": datetime.now(timezone.utc).isoformat(),
        "row_count": row_count,
        "parquet_file_size_bytes": parquet_size,
        "file_sha256": file_sha256,
        "dataset_id": dataset_id,
        "dataset_id_note": (
            "Provisional: se usa el file_sha256 completo como dataset_id porque la "
            "documentacion aprobada no define una convencion corta inequivoca. "
            "Definir convencion corta mas adelante."
        ),
        "real_cost_usd": real_cost,
        "estimated_cost_usd": estimated_cost,
        "cost_note": (
            "real_cost_usd proviene del SDK si lo expone; si es null, usar "
            "estimated_cost_usd (get_cost, que NO consume creditos)."
        ),
        "warmup_min": warmup_min,
        "warmup_definition": (
            "warmup_min = max(fast_period, slow_period + crossover_state_requirement, "
            "atr_period + 1, volume_period) = 22 con periodos 9/21/1/14/20."
        ),
        "splits": {
            "calibration": {"start": "2023-09-13T00:00:00Z", "end": "2025-07-01T00:00:00Z"},
            "validation": {"start": "2025-07-01T00:00:00Z", "end": "2026-02-02T00:00:00Z"},
            "final_oos": {"start": "2026-02-02T00:00:00Z", "end": "2026-09-12T00:00:00Z"},
        },
        "integrity": {
            "opens_ok": True,
            "all_within_range": in_range,
            "rows_before_start": n_before,
            "rows_on_or_after_end": n_after,
            "chronological_non_decreasing": non_decreasing,
            "duplicate_timestamps": dup_count,
            "gaps_over_5min": gaps_over,
        },
        "symbology": symbology_info,
        "symbology_note": (
            "NQ.c.0 es continuo y mapea a distintos contratos NQ subyacentes en el tiempo. "
            "to_parquet se llamo con map_symbols=True (por defecto), por lo que el Parquet "
            "incluye la columna 'symbol' con el raw_symbol subyacente por vela, reconstruido "
            "desde las mappings LOCALES del DBNStore (sin query adicional facturable). "
            "Las mappings completas se registran aqui. Para una tabla de rollover mas rica "
            "(p.ej. fechas exactas de roll con definiciones completas) se requeriria una "
            "llamada adicional store.request_full_definitions() / request_symbology(), que "
            "SI implica una consulta adicional (posible costo/descarga) y por eso NO se ejecuto."
        ),
    }
    META_PATH.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    log(report, f"meta.json escrito: {META_PATH}")

    # -- BACKUP (solo si todo lo anterior fue exitoso) ------------------------
    backup_status = {}
    try:
        drive_root = Path(r"D:\\")
        if not drive_root.exists():
            log(report, "BACKUP: la unidad D:\\ no es accesible. Se detiene el backup aqui.")
            backup_status = {"done": False, "reason": "D: no accesible"}
        else:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup_parquet = BACKUP_DIR / PARQUET_PATH.name
            backup_meta = BACKUP_DIR / META_PATH.name
            # Copiamos (no movemos) preservando metadatos. No borramos originales.
            shutil.copy2(PARQUET_PATH, backup_parquet)
            shutil.copy2(META_PATH, backup_meta)
            backup_sha = sha256_of_file(backup_parquet)
            sha_match = (backup_sha == file_sha256)
            # Confirmar que el meta.json de backup abre correctamente
            meta_ok = False
            try:
                json.loads(backup_meta.read_text(encoding="utf-8"))
                meta_ok = True
            except Exception as e:  # noqa: BLE001
                log(report, f"BACKUP: meta.json de backup NO abre: {e!r}")
            log(report, f"BACKUP: parquet copiado a {backup_parquet}")
            log(report, f"BACKUP: SHA-256 backup = {backup_sha}")
            log(report, f"BACKUP: SHA-256 coincide con maestro: {sha_match}")
            log(report, f"BACKUP: meta.json backup abre correctamente: {meta_ok}")
            backup_status = {
                "done": True,
                "backup_parquet": str(backup_parquet),
                "backup_meta": str(backup_meta),
                "backup_sha256": backup_sha,
                "sha_match": sha_match,
                "meta_ok": meta_ok,
            }
    except Exception as e:  # noqa: BLE001
        log(report, f"BACKUP: fallo inesperado: {e!r}")
        backup_status = {"done": False, "reason": repr(e)}

    log(report, "=== FIN OK ===")
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
