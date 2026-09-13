"""LAYER guard — Proteccion de fronteras temporales de los splits (anti-fuga de datos).

Funciones PURAS que hacen cumplir las reglas del design.md sobre los tres splits
temporales y el warm-up (calentamiento). Objetivo: impedir contaminacion entre splits
(cross-split contamination) y fuga de datos (data leakage).

Reglas implementadas (design.md, "Tres splits temporales" y 9-bis.14):
- Calibracion = `[CALIBRATION_START, CALIBRATION_END)`: inicio INCLUSIVO, fin EXCLUSIVO.
- El contexto de warm-up debe ser cronologicamente ANTERIOR al inicio del split evaluado
  y NUNCA puede usar datos de Validacion (>= CALIBRATION_END).
- Validacion y OOS final son INACCESIBLES desde el runner de Calibracion (se rechazan).
- Una observacion censurada NUNCA se completa con datos en/despues de la frontera.

Glosario:
- UTC (Coordinated Universal Time): zona horaria universal de referencia.
- split (particion por tiempo): tramo cronologico del dataset con una funcion.
- warm-up (calentamiento): velas previas que solo inicializan indicadores.
"""

from datetime import datetime, timezone

from src.calibration.grid import CALIBRATION_END, CALIBRATION_START


def _parse_utc(ts: str | datetime) -> datetime:
    """Normaliza un instante a datetime UTC con tzinfo.

    Acepta un string ISO 8601 (con sufijo 'Z' o offset) o un datetime. Un datetime
    naive (sin zona) se rechaza para evitar comparaciones ambiguas.
    """
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            # Un instante sin zona horaria es ambiguo: se prohibe explicitamente.
            raise ValueError("timestamp naive (sin tzinfo) no permitido; usar UTC explicito")
        # Se normaliza a UTC para comparar siempre en la misma referencia.
        return ts.astimezone(timezone.utc)
    # String ISO 8601: 'Z' no lo entiende fromisoformat en <3.11 de forma uniforme;
    # se reemplaza por '+00:00' para robustez.
    normalized = ts.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp ISO sin zona horaria no permitido; usar UTC explicito")
    return parsed.astimezone(timezone.utc)


# Fronteras del split de Calibracion, parseadas una sola vez (modulo).
_CALIBRATION_START_DT = _parse_utc(CALIBRATION_START)
_CALIBRATION_END_DT = _parse_utc(CALIBRATION_END)


def calibration_start() -> datetime:
    """Devuelve el inicio (inclusivo) de Calibracion como datetime UTC."""
    return _CALIBRATION_START_DT


def calibration_end() -> datetime:
    """Devuelve el fin (EXCLUSIVO) de Calibracion como datetime UTC."""
    return _CALIBRATION_END_DT


def is_within_calibration(ts: str | datetime) -> bool:
    """True si `ts` cae en Calibracion `[inicio, fin)`: inicio inclusivo, fin EXCLUSIVO.

    Un timestamp igual a `CALIBRATION_END` pertenece a Validacion -> devuelve False.
    Un timestamp inmediatamente anterior a `CALIBRATION_END` -> True.
    """
    t = _parse_utc(ts)
    # inicio inclusivo (>=) y fin exclusivo (<): la vela de las 00:00 del corte NO entra.
    return _CALIBRATION_START_DT <= t < _CALIBRATION_END_DT


def is_validation_or_later(ts: str | datetime) -> bool:
    """True si `ts` pertenece a Validacion u OOS final (>= CALIBRATION_END).

    Estos datos NO pueden usarse durante la Calibracion (ni como warm-up ni para
    completar observaciones censuradas).
    """
    t = _parse_utc(ts)
    return t >= _CALIBRATION_END_DT


def assert_warmup_context_valid(
    warmup_ts: str | datetime,
    split_start: str | datetime,
) -> None:
    """Valida que una vela de warm-up sea cronologicamente ANTERIOR al inicio del split.

    Reglas del warm-up (design.md, "Reglas del warm-up"):
    - El warm-up es SOLO contexto historico anterior al timestamp de inicio del split.
    - NUNCA puede ser >= CALIBRATION_END (nunca usa datos de Validacion/OOS).

    Lanza ValueError si la vela de warm-up no es estrictamente anterior al inicio del
    split, o si cae en Validacion/OOS.
    """
    w = _parse_utc(warmup_ts)
    s = _parse_utc(split_start)
    if w >= s:
        # El warm-up debe ser estrictamente anterior al primer instante evaluado.
        raise ValueError(
            "warm-up invalido: la vela de contexto no es anterior al inicio del split"
        )
    if w >= _CALIBRATION_END_DT:
        # Prohibicion dura: el warm-up jamas usa datos de Validacion (>= fin de Calibracion).
        raise ValueError("warm-up invalido: no puede usar datos de Validacion/OOS (>= fin)")


def reject_if_inaccessible(ts: str | datetime) -> None:
    """Rechaza el acceso a datos de Validacion u OOS final desde el runner de Calibracion.

    Validacion y OOS final estan SELLADOS para la Calibracion (design.md 9-bis.14 y
    seccion 10). Cualquier intento de leerlos para decidir la Calibracion se rechaza.
    """
    if is_validation_or_later(ts):
        raise PermissionError(
            "acceso denegado: los datos en/despues de la frontera de Calibracion "
            "(Validacion / OOS final) son inaccesibles durante la Calibracion"
        )


def can_complete_censored_observation(completion_ts: str | datetime) -> bool:
    """Indica si una observacion censurada puede completarse con datos en `completion_ts`.

    Regla dura (design.md 8.1 y 5.7): una observacion censurada NUNCA se completa con
    datos EN o DESPUES de la frontera de Calibracion. Solo datos estrictamente anteriores
    a `CALIBRATION_END` son admisibles.
    """
    t = _parse_utc(completion_ts)
    # Estrictamente anterior a la frontera: en la frontera o despues ya es Validacion.
    return t < _CALIBRATION_END_DT
