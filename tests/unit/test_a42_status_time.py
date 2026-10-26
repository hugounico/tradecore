"""A4.2: el mensaje 'status' debe enviar last_candle_time como ISO 8601 UTC terminado en 'Z' que el
navegador pueda leer. Defecto: con una hora con zona horaria (todas las velas reales), el pusher enviaba
'...+00:00Z' -> new Date() del navegador = Invalid Date -> el dashboard mostraba siempre 'Último: —'."""

import json
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from src.api.throttled_pusher import ThrottledPusher

RFC3339_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


async def _status_time(last_time):
    pusher = ThrottledPusher(throttle_interval=1.0)
    ws = AsyncMock()
    await pusher.set_connection(ws)
    with patch("src.api.throttled_pusher.time.time", return_value=100.0):
        await pusher.queue_status(connected=True, last_time=last_time, mode="live")
        await pusher.flush_if_ready()
    return json.loads(ws.send_text.call_args[0][0])[0]["data"]["last_candle_time"]


async def test_s1_utc_time_is_plain_z():
    got = await _status_time(datetime(2024, 8, 5, 18, 5, tzinfo=timezone.utc))
    assert got == "2024-08-05T18:05:00Z"
    assert RFC3339_Z.match(got)


async def test_s2_other_time_zone_is_converted_to_utc():
    chicago = datetime(2024, 8, 5, 13, 5, tzinfo=ZoneInfo("America/Chicago"))  # CDT = UTC-5
    assert await _status_time(chicago) == "2024-08-05T18:05:00Z"


async def test_s3_naive_time_keeps_previous_format():
    assert await _status_time(datetime(2024, 8, 5, 18, 5)) == "2024-08-05T18:05:00Z"


async def test_s4_none_stays_none():
    assert await _status_time(None) is None
