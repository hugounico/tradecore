"""LAYER B — Block bootstrap temporal por semana ISO (design.md 9-bis.13-CI revisado).

Estima intervalos de confianza (IC) remuestreando BLOQUES semanales ISO completos CON
reemplazo, en vez de `signal_id` individuales. Esto preserva mejor la dependencia
temporal de corto plazo (design.md 9-bis.2 y 9-bis.13-CI).

Reglas duras CONGELADAS:
- `bootstrap_block_id = ISO_year + ISO_week` derivado de forma determinista del
  timestamp UTC via `datetime.isocalendar()` (el ISO year puede diferir del ano
  calendario en la frontera de ano).
- Se remuestrean BLOQUES completos; las senales de un bloque permanecen JUNTAS (nunca se
  remuestrean signal_id sueltos, para conservar el pareo interno).
- Semilla fija reproducible (`random.Random(seed)`): dos corridas con la misma semilla
  producen el MISMO IC.
- Nunca se generan semanas vacias.
- `n_bootstrap_blocks` = numero de semanas ISO NO vacias de ESA poblacion especifica.
- `bootstrap_evidence_insufficient = (n_bootstrap_blocks < N_BOOTSTRAP_BLOCKS_MIN)`.
  Si es insuficiente, el IC NO puede producir un PASS de su gate.

Glosario:
- ISO week / semana ISO: numeracion de semanas del estandar ISO 8601; la semana 1 es la
  que contiene el primer jueves del ano, por lo que dias de fin/inicio de ano pueden
  pertenecer a un ISO year distinto del ano calendario.
- bootstrap (remuestreo): tecnica para estimar incertidumbre remuestreando con reemplazo.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence, TypeVar

from src.calibration.grid import (
    BOOTSTRAP_RESAMPLES,
    CONFIDENCE_LEVEL,
    N_BOOTSTRAP_BLOCKS_MIN,
)

T = TypeVar("T")


def bootstrap_block_id(utc_timestamp: str | datetime) -> str:
    """Deriva el id de bloque semanal ISO: f"{iso_year}-W{iso_week:02d}" (9-bis.13-CI).

    Usa `datetime.isocalendar()`, cuyo `iso_year` puede diferir del ano calendario en la
    frontera de ano (ej. 2024-12-31 pertenece a la semana ISO 2025-W01).

    Acepta string ISO 8601 (con 'Z' u offset) o datetime con tzinfo. Un datetime naive se
    rechaza para evitar ambiguedad de zona horaria.
    """
    if isinstance(utc_timestamp, datetime):
        dt = utc_timestamp
        if dt.tzinfo is None:
            raise ValueError("timestamp naive (sin tzinfo) no permitido; usar UTC explicito")
        dt = dt.astimezone(timezone.utc)
    else:
        normalized = utc_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            raise ValueError("timestamp ISO sin zona horaria no permitido; usar UTC explicito")
        dt = dt.astimezone(timezone.utc)

    # isocalendar() devuelve (iso_year, iso_week, iso_weekday). El iso_year NO siempre
    # coincide con dt.year en las fronteras de ano; por eso se usa el iso_year.
    iso = dt.isocalendar()
    iso_year = iso[0]
    iso_week = iso[1]
    # Formato con semana a dos digitos para orden lexicografico estable dentro del ano.
    return f"{iso_year}-W{iso_week:02d}"


def n_bootstrap_blocks(block_ids: Sequence[str]) -> int:
    """Numero de bloques (semanas ISO) NO vacios presentes en ESTA poblacion (9-bis.13-CI).

    Se recibe la lista de `bootstrap_block_id` de las observaciones de la poblacion
    especifica; se cuentan los distintos. Nunca se cuentan semanas vacias (solo aparecen
    las que tienen al menos una observacion).
    """
    return len(set(block_ids))


def bootstrap_evidence_insufficient(block_ids: Sequence[str]) -> bool:
    """True si hay menos bloques que `N_BOOTSTRAP_BLOCKS_MIN` (9-bis.13-CI CI.4).

    Cuando es True, el IC de esa poblacion NO puede producir un PASS de su gate.
    """
    return n_bootstrap_blocks(block_ids) < N_BOOTSTRAP_BLOCKS_MIN


def _percentile(sorted_values: list[float], q: float) -> float:
    """Percentil (interpolacion lineal) de una lista YA ordenada. q en [0, 1].

    Implementacion propia (stdlib) para determinismo; equivalente al metodo lineal.
    """
    if not sorted_values:
        raise ValueError("no se puede calcular percentil de una lista vacia")
    if len(sorted_values) == 1:
        return sorted_values[0]
    # Posicion fraccional dentro del arreglo ordenado.
    pos = q * (len(sorted_values) - 1)
    lower_idx = int(pos)  # indice inferior
    frac = pos - lower_idx  # parte fraccional para interpolar
    if lower_idx + 1 >= len(sorted_values):
        return sorted_values[-1]
    # Interpolacion lineal entre los dos valores vecinos.
    return sorted_values[lower_idx] + frac * (
        sorted_values[lower_idx + 1] - sorted_values[lower_idx]
    )


def block_bootstrap_ci(
    observations_grouped_by_block: Mapping[str, Sequence[T]],
    statistic_fn: Callable[[list[T]], float | None],
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence_level: float = CONFIDENCE_LEVEL,
    seed: int = 0,
) -> tuple[float, float]:
    """IC percentil bilateral por block bootstrap semanal ISO (design.md 9-bis.13-CI).

    Args:
        observations_grouped_by_block: mapa `bootstrap_block_id -> observaciones` de la
            poblacion especifica. Los bloques VACIOS no deben incluirse.
        statistic_fn: funcion que recibe la lista PLANA de observaciones de una replica y
            devuelve el estadistico (o None si la replica no es evaluable; se descarta).
        resamples: numero de replicas (default 2000, congelado).
        confidence_level: nivel de confianza bilateral (default 0.95, congelado).
        seed: semilla fija para reproducibilidad (misma semilla -> mismo IC).

    Returns:
        (lower, upper): limites del IC percentil bilateral.

    Procedimiento (CI.2): por cada replica se remuestrean BLOQUES completos NO vacios CON
    reemplazo, conservando juntas todas las observaciones de cada bloque; se recomputa el
    estadistico; el IC son los percentiles [alpha/2, 1-alpha/2] de las replicas.
    """
    # Solo bloques NO vacios (nunca semanas vacias).
    blocks = [
        (bid, list(members))
        for bid, members in observations_grouped_by_block.items()
        if len(members) > 0
    ]
    if not blocks:
        raise ValueError("no hay bloques no vacios para el bootstrap")

    rng = random.Random(seed)  # generador determinista sembrado (reproducible)
    n_blocks = len(blocks)
    replica_stats: list[float] = []

    for _ in range(resamples):
        # Remuestreo CON reemplazo de bloques completos: se eligen n_blocks bloques.
        flat: list[T] = []
        for _ in range(n_blocks):
            idx = rng.randrange(n_blocks)  # indice de bloque elegido con reemplazo
            # Se conservan JUNTAS todas las observaciones del bloque (pareo interno).
            flat.extend(blocks[idx][1])
        stat = statistic_fn(flat)
        if stat is not None:
            replica_stats.append(stat)

    if not replica_stats:
        raise ValueError("ninguna replica bootstrap produjo un estadistico evaluable")

    replica_stats.sort()
    alpha = 1.0 - confidence_level  # p.ej. 0.05 para 95%
    lower = _percentile(replica_stats, alpha / 2.0)  # cola inferior
    upper = _percentile(replica_stats, 1.0 - alpha / 2.0)  # cola superior
    return (lower, upper)
