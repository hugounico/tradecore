"""LAYER config — Constantes CONGELADAS de la grilla de Calibracion y umbrales (9-bis).

Todas las constantes aqui se CONGELARON antes de ejecutar la Calibracion (design.md
seccion 9-bis.19, anti-data-snooping). NO se modifican en funcion de resultados.

Glosario breve:
- grilla / grid (rejilla de busqueda): conjunto discreto de valores candidatos.
- ATR (Average True Range): medida de volatilidad reciente del precio.
- warm-up (calentamiento): velas previas que solo inicializan indicadores, sin contar
  como observaciones evaluadas.
- CI / IC (Confidence Interval / Intervalo de Confianza): rango que expresa la
  incertidumbre de una estimacion.
- Cliff's delta (delta de Cliff): tamano de efecto basado en rangos.
"""

# --- Grilla de parametros (design.md 9-bis / seccion 1) ---

# atr_multiplier: 1.5x a 3.0x, paso 0.25 -> 7 valores (design.md seccion 1).
# Se escriben como literales exactos para que un cambio de grilla haga fallar los tests.
ATR_MULTIPLIERS: list[float] = [1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00]

# volume_threshold_factor: 0.5x a 0.8x, paso 0.1 -> 4 valores (design.md seccion 1).
VOLUME_THRESHOLD_FACTORS: list[float] = [0.50, 0.60, 0.70, 0.80]

# --- Conteos conceptuales de configuraciones de la Tarea 1 (design.md seccion 2) ---
# A = baseline interno SMA (1 configuracion de referencia, 0 parametros que calibrar).
# B = 7 candidatos de atr_multiplier.
# C = grilla factorial COMPLETA 7 x 4 = 28 combinaciones.
# Total conceptual = 1 + 7 + 28 = 36.
COUNT_A: int = 1
COUNT_B: int = 7
COUNT_C: int = 28
COUNT_TOTAL: int = 36


# --- Pasos ATR consecutivos (design.md 9-bis.8-ALT y 9-bis.13-SEQ) ---
# Los seis pasos son EXACTAMENTE estos (solo multiplicadores consecutivos; no saltos).
CONSECUTIVE_ATR_STEPS: list[tuple[float, float]] = [
    (1.50, 1.75),
    (1.75, 2.00),
    (2.00, 2.25),
    (2.25, 2.50),
    (2.50, 2.75),
    (2.75, 3.00),
]


def compute_warmup_min(
    fast_period: int,
    slow_period: int,
    crossover_state_requirement: int,
    atr_period: int,
    volume_period: int,
) -> int:
    """Deriva `warmup_min` con la formula del design.md (NO es constante magica).

    Formula CONGELADA (seccion "Regla derivada del warm-up minimo"):

        warmup_min = max(fast_period,
                         slow_period + crossover_state_requirement,
                         atr_period + 1,
                         volume_period)

    Atribucion de cada termino (design.md):
    - fast_period               -> SMA(fast) necesita `fast` cierres.
    - slow_period + crossover_state_requirement -> SMA(slow) necesita `slow` cierres Y
      el detector de cruces necesita UNA evaluacion previa valida (`_prev_fast`/
      `_prev_slow` de SignalEngine); ese `+1` NO es un margen generico.
    - atr_period + 1            -> el primer True Range necesita el cierre previo.
    - volume_period             -> promedio de volumen de ventana completa.

    Con la config actual (9, 21, 1, 14, 20) el resultado es 22, pero se calcula
    dinamicamente: si cualquier periodo cambia, `warmup_min` se recalcula.
    """
    return max(
        fast_period,  # SMA rapida
        slow_period + crossover_state_requirement,  # SMA lenta + estado de cruce previo
        atr_period + 1,  # el primer TR necesita el cierre de la vela anterior
        volume_period,  # promedio de volumen de ventana completa
    )


# --- Umbrales congelados (design.md 9-bis.5, 9-bis.10, 9-bis.13, 9-bis.13-CI, 9-bis.15) ---

# Piso de observaciones observables por paso de H1 (9-bis.13). Convencion ex-ante.
N_STEP_OBSERVABLE_MIN: int = 50
# Piso de rescates observables antes de confiar en rescue_quality (9-bis.13).
N_RESCUED_OBSERVABLE_MIN: int = 30
# Techo de tasa de censura por paso (9-bis.5 y 9-bis.13). Por encima -> no elegible.
STEP_CENSORING_RATE_MAX: float = 0.30

# Piso de bloques (semanas ISO no vacias) para que un IC pueda producir PASS (9-bis.13-CI).
N_BOOTSTRAP_BLOCKS_MIN: int = 20
# Numero de re-muestreos bootstrap (9-bis.13-CI). Fijo.
BOOTSTRAP_RESAMPLES: int = 2000
# Nivel de confianza del IC bilateral (9-bis.13-CI).
CONFIDENCE_LEVEL: float = 0.95

# Gate numerico de la delta de Cliff para H2/H3-A (9-bis.10 y 9-bis.20).
# Efecto "pequeno" ex-ante: |Cliff_delta| >= 0.147.
CLIFF_DELTA_GATE: float = 0.147

# Top-N de candidatas C que gradua a Validacion (9-bis.15).
TOP_N_C: int = 3

# --- Ventana temporal del split de Calibracion (design.md, tres splits) ---
# `[inicio, fin)`: inicio INCLUSIVO, fin EXCLUSIVO.
CALIBRATION_START: str = "2023-09-13T00:00:00Z"
CALIBRATION_END: str = "2025-07-01T00:00:00Z"  # EXCLUSIVO (pertenece a Validacion)
