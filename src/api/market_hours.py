"""Horario regular del mercado de futuros CME Globex para NQ (E-mini Nasdaq-100).

Determina si el mercado esta en horario regular de negociacion en un instante dado.
Se usa solo para informar al dashboard (bandera `market_open`): NO altera el pipeline
de datos ni la generacion de senales.

Horario (en hora de Chicago, "America/Chicago", que es la zona de CME):
- Domingo: abre a las 17:00 (apertura de la semana).
- Lunes a jueves: abierto todo el dia EXCEPTO la pausa diaria de mantenimiento
  16:00-17:00 (cierra 16:00, reabre 17:00).
- Viernes: abierto hasta las 16:00; despues cerrado el fin de semana.
- Sabado: cerrado todo el dia.

Terminologia:
- ZoneInfo = zona horaria de la biblioteca estandar (stdlib) `zoneinfo`.
- tz-aware / naive: un datetime "aware" conoce su zona horaria; uno "naive" no.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

# Zona horaria de CME. ZoneInfo aplica automaticamente el horario de verano (DST).
_CME_TZ = ZoneInfo("America/Chicago")

# Pausa diaria de mantenimiento (lunes-jueves): cerrado entre estas horas de Chicago.
_DAILY_CLOSE_HOUR = 16   # 16:00 -> cierre diario
_DAILY_OPEN_HOUR = 17    # 17:00 -> reapertura diaria

# Dias de la semana segun datetime.weekday(): lunes=0 ... domingo=6.
_MONDAY, _FRIDAY, _SATURDAY, _SUNDAY = 0, 4, 5, 6


def is_regular_market_open(now_utc: datetime) -> bool:
    """Return True if the regular CME session is open at `now_utc`.

    `now_utc` DEBE ser tz-aware (preferentemente en UTC); un datetime naive lanza
    ValueError para evitar comparaciones ambiguas de horario.

    No cubre feriados ni cierres anticipados de CME.
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc debe ser tz-aware (naive datetime no permitido)")

    # Convierte el instante a hora de Chicago para evaluar el calendario de CME.
    now = now_utc.astimezone(_CME_TZ)
    weekday = now.weekday()
    hour = now.hour

    # Sabado: cerrado todo el dia.
    if weekday == _SATURDAY:
        return False

    # Domingo: cerrado hasta las 17:00, luego abre.
    if weekday == _SUNDAY:
        return hour >= _DAILY_OPEN_HOUR

    # Viernes: abierto hasta las 16:00; a partir de ahi, cerrado el fin de semana.
    if weekday == _FRIDAY:
        return hour < _DAILY_CLOSE_HOUR

    # Lunes a jueves: abierto salvo la pausa diaria 16:00-17:00.
    if _DAILY_CLOSE_HOUR <= hour < _DAILY_OPEN_HOUR:
        return False
    return True
