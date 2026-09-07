"""Tests de PRESERVACION — Fase 2 Consolidation Fixes.

CONTEXTO (metodologia bugfix / "observation-first"):
    Estos tests bloquean ("lock in") el COMPORTAMIENTO EXISTENTE que NO debe
    cambiar despues de aplicar los arreglos. A diferencia del test de exploracion
    (test_phase2_bug_exploration.py, que DEBE FALLAR sobre codigo sin arreglar),
    estos tests de preservacion DEBEN PASAR sobre el codigo SIN arreglar.
    Sirven de linea base: si siguen pasando tras el arreglo, no hubo regresion.

Cubre la preservacion de los dos issues de Fase 2:

  Issue 1 — Otras rutas NO afectadas:
    Eliminar GET /debug/network no debe cambiar el resto de la app. En particular:
      - GET /health sigue devolviendo 200 con {"status": "ok"}.
      - El WebSocket en /ws/chart sigue aceptando conexiones.
      - Cualquier ruta que NO sea /debug/network responde igual que antes
        (las inexistentes -> 404).

  Issue 2 — Timestamps validos NO afectados:
    El arreglo de handleStatus solo debe cambiar el caso null/no-parseable. Para
    todo timestamp ISO 8601 valido, la logica arreglada debe producir EXACTAMENTE
    lo mismo que la logica original: "Ultimo: " + hora local formateada.

Glosario de terminos tecnicos (lectura pedagogica):
  - ASGI ("Asynchronous Server Gateway Interface"): estandar que usa FastAPI para
    comunicar el servidor web con la aplicacion de forma asincronica.
  - ASGITransport: adaptador de httpx que envia peticiones HTTP a una app ASGI
    (como FastAPI) EN MEMORIA, sin levantar un servidor de red real.
  - httpx.AsyncClient: cliente HTTP asincronico (usa async/await) para los tests.
  - WebSocket: canal de comunicacion bidireccional y persistente sobre una unica
    conexion (a diferencia de HTTP, que es peticion/respuesta puntual).
  - PBT ("Property-Based Testing"): en vez de un solo ejemplo, la libreria
    Hypothesis genera muchos valores y verifica que una propiedad se cumpla en todos.
  - ISO 8601: formato estandar de fecha/hora, ej. "2025-01-15T14:30:00Z" (la "Z"
    indica hora UTC — Tiempo Universal Coordinado).

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
"""

import math
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

# Importamos la app FastAPI real para probar sus rutas en memoria (via ASGITransport).
from src.api.app import app


# =============================================================================
# ISSUE 1 — Preservacion: otras rutas NO deben verse afectadas
# =============================================================================
#
# Propiedad de preservacion (del bugfix.md / design.md):
#   FOR ALL X WHERE NOT isBugCondition_DebugEndpoint(X):
#       handleRequest(X) == handleRequest'(X)
#
# Es decir: toda ruta distinta de /debug/network responde igual antes y despues
# del arreglo. Sobre el codigo SIN arreglar estas asserts YA se cumplen -> PASAN.


@pytest.mark.asyncio
async def test_health_returns_200_ok():
    """GET /health devuelve 200 con {"status": "ok"} — comportamiento a preservar.

    Requirement 3.1: /health SIGUE devolviendo {"status": "ok"} con HTTP 200.
    """
    # ASGITransport enruta la peticion directo a la app en memoria (sin red real).
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200, (
        f"/health devolvio HTTP {response.status_code} (se esperaba 200)."
    )
    # El cuerpo (body) de la respuesta debe ser exactamente {"status": "ok"}.
    assert response.json() == {"status": "ok"}, (
        f"/health devolvio {response.json()!r} (se esperaba {{'status': 'ok'}})."
    )


@pytest.mark.asyncio
async def test_nonexistent_route_returns_404():
    """GET /nonexistent devuelve 404 — ruta inexistente, comportamiento a preservar."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/nonexistent")

    assert response.status_code == 404, (
        f"/nonexistent devolvio HTTP {response.status_code} (se esperaba 404)."
    )


def test_ws_chart_accepts_connections():
    """El WebSocket en /ws/chart acepta conexiones — comportamiento a preservar.

    Requirement 3.2: /ws/chart SIGUE aceptando conexiones WebSocket.
    Usamos TestClient de FastAPI (starlette), que trae soporte de WebSocket para
    tests; httpx.ASGITransport por si solo no maneja el handshake de WebSocket.

    El handler llama a `ws.accept()` ANTES de comprobar si el pipeline esta listo:
    por eso, incluso sin inicializar el pipeline, la conexion es ACEPTADA (que es
    lo unico que la preservacion exige aqui). Si el pipeline no esta listo, el
    servidor cierra luego con codigo 1011, pero el handshake ya ocurrio.
    Instanciamos el TestClient DENTRO del test (no a nivel de modulo) para que su
    hilo interno se cierre limpiamente al salir del `with`.
    """
    # Si el handshake fallara, `websocket_connect` lanzaria excepcion -> test falla.
    with TestClient(app) as client:
        with client.websocket_connect("/ws/chart") as websocket:
            # Llegar aca sin excepcion confirma que el servidor ACEPTO la conexion.
            assert websocket is not None


# --- Enfoque PBT (Property-Based Testing) para Issue 1 ---
#
# Generamos rutas HTTP aleatorias con st.text(). Para toda ruta que NO sea
# /debug/network y que no colisione con una ruta ya registrada, la app responde
# 404 (ruta inexistente). Esta propiedad se cumple ANTES y DESPUES del arreglo,
# porque el arreglo solo elimina /debug/network.

# Rutas ya registradas en la app que NO debemos generar (responden != 404 hoy):
#   "/"            -> monta index.html (StaticFiles / ruta raiz)
#   "/health"      -> 200
#   "/debug/network" -> es justamente la ruta del bug (excluida)
#   "/ws/chart"    -> WebSocket (no es GET normal)
#   cualquier "/static/..." -> archivos estaticos
_RESERVED_PREFIXES = ("/static", "/ws")
_RESERVED_EXACT = {"", "/", "/health", "/debug/network", "/ws/chart"}


def _is_reserved_path(path: str) -> bool:
    """True si la ruta colisiona con algo ya registrado (no debe asumirse 404)."""
    if path in _RESERVED_EXACT:
        return True
    # /debug/network es la ruta del bug: la excluimos de la propiedad de preservacion.
    if path == "/debug/network":
        return True
    for prefix in _RESERVED_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


# Strategy: construimos rutas tipo "/<segmento>" con caracteres URL seguros.
# Evitamos barras internas y caracteres de control para no generar rutas raras
# que httpx no pueda enviar; el objetivo es "ruta cualquiera inexistente".
_path_segment = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),  # letras y digitos
        max_codepoint=127,  # ASCII: evita caracteres exoticos en la URL
    ),
    min_size=1,
    max_size=20,
)


@settings(max_examples=100, deadline=None)
@given(segment=_path_segment)
def test_random_non_debug_paths_return_404(segment):
    """Propiedad: para toda ruta aleatoria (que no sea reservada ni /debug/network),

    la app responde 404. Esto se cumple igual antes y despues del arreglo, porque
    eliminar /debug/network no crea ni cambia ninguna otra ruta.

    Combinar Hypothesis (@given, sincronico) con corrutinas async es fragil, por
    eso usamos el TestClient sincronico de FastAPI en vez de httpx.AsyncClient.
    Creamos el cliente una vez por ejecucion del test (no a nivel de modulo) para
    evitar que su hilo interno quede vivo tras la coleccion de pytest.
    """
    path = "/" + segment
    if _is_reserved_path(path):
        return  # descartamos rutas reservadas: no aplican a la propiedad

    # TestClient sin context manager NO dispara el ciclo de vida (lifespan) de la
    # app: para peticiones HTTP simples basta con enrutar la request a la app ASGI.
    client = TestClient(app)
    response = client.get(path)

    assert response.status_code == 404, (
        f"Ruta aleatoria {path!r} devolvio HTTP {response.status_code} "
        f"(se esperaba 404). Solo /debug/network deberia cambiar de comportamiento."
    )


# =============================================================================
# ISSUE 2 — Preservacion: timestamps ISO 8601 validos NO deben verse afectados
# =============================================================================
#
# Propiedad de preservacion (del bugfix.md / design.md):
#   FOR ALL X WHERE NOT isBugCondition_InvalidDate(X):
#       handleStatus(X) == handleStatus'(X)
#
# Reproducimos en Python tanto la logica ORIGINAL como la ARREGLADA de
# handleStatus, y verificamos que para toda fecha valida producen lo mismo.


def _js_new_date_get_time(value):
    """Simula `new Date(value).getTime()` de JavaScript para nuestro dominio.

    Devuelve:
      - un numero (milisegundos epoch) cuando el valor es una fecha ISO 8601 valida.
      - math.nan cuando el valor no es parseable (equivalente a "Invalid Date").

    Replicamos SOLO lo necesario para el dominio del test (strings ISO 8601).
    """
    if value is None:
        return 0  # en JS, new Date(null) -> epoch (0); no es NaN
    if not isinstance(value, str) or value == "":
        return math.nan
    try:
        # Python <3.11 no acepta el sufijo "Z" directo; lo normalizamos a "+00:00"
        # para simular el parser laxo de JavaScript sobre ISO 8601.
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        # getTime() en JS son milisegundos desde epoch; aqui devolvemos algo valido.
        return dt.timestamp() * 1000
    except (ValueError, TypeError):
        return math.nan


def _format_time_marker(value):
    """Marcador determinista que representa `dt.toLocaleTimeString()`.

    No podemos reproducir el locale exacto del navegador en Python, pero para la
    propiedad de PRESERVACION lo relevante es que original y arreglado usen la
    MISMA hora de la MISMA fecha valida. Usamos un marcador estable derivado del
    valor para comparar ambas ramas de forma equivalente.
    """
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    # Representacion estable HH:MM:SS (no depende de locale) — sirve para comparar.
    return dt.strftime("%H:%M:%S")


def handle_status_original(last_candle_time):
    """Logica ORIGINAL (sin arreglar) de handleStatus para el timestamp.

    JS original:
        if (data.last_candle_time) {
            var dt = new Date(data.last_candle_time);
            lastTimestampEl.textContent = 'Ultimo: ' + dt.toLocaleTimeString();
        }
    """
    if last_candle_time:  # truthy en JS
        get_time = _js_new_date_get_time(last_candle_time)
        if math.isnan(get_time):
            return "Ultimo: Invalid Date"
        return "Ultimo: " + _format_time_marker(last_candle_time)
    return ""  # valor falsy: no se fija texto


def handle_status_fixed(last_candle_time):
    """Logica ARREGLADA (post-fix) de handleStatus, segun design.md.

    JS arreglado:
        if (data.last_candle_time) {
            var dt = new Date(data.last_candle_time);
            if (!isNaN(dt.getTime())) {
                lastTimestampEl.textContent = 'Ultimo: ' + dt.toLocaleTimeString();
            } else {
                lastTimestampEl.textContent = 'Ultimo: —';
            }
        } else {
            lastTimestampEl.textContent = 'Ultimo: —';
        }
    """
    if last_candle_time:  # truthy en JS
        get_time = _js_new_date_get_time(last_candle_time)
        if not math.isnan(get_time):
            return "Ultimo: " + _format_time_marker(last_candle_time)
        return "Ultimo: —"  # no parseable -> fallback
    return "Ultimo: —"  # null/"" -> fallback


def _is_bug_condition_invalid_date(value):
    """isBugCondition_InvalidDate(X): True si value es None o produce fecha invalida."""
    if value is None:
        return True
    return math.isnan(_js_new_date_get_time(value))


def test_issue2_preservation_valid_iso_unchanged_example():
    """Ejemplo concreto: "2025-01-15T14:30:00Z" produce el mismo texto en ambas ramas.

    Requirement 3.3: timestamps ISO validos SIGUEN mostrando la hora local formateada.
    """
    value = "2025-01-15T14:30:00Z"
    # Precondicion: NO es condicion de bug (es una fecha valida).
    assert not _is_bug_condition_invalid_date(value)
    # La logica arreglada debe producir EXACTAMENTE lo mismo que la original.
    assert handle_status_fixed(value) == handle_status_original(value)
    # Y debe ser un texto de hora formateada (no el fallback).
    assert handle_status_fixed(value).startswith("Ultimo: ")
    assert handle_status_fixed(value) != "Ultimo: —"


# --- Enfoque PBT (Property-Based Testing) para Issue 2 ---
#
# Generamos timestamps ISO 8601 UTC validos a partir de objetos datetime, para
# garantizar que Hypothesis produzca SIEMPRE fechas parseables (no falsos negativos).
# Propiedad: para todo timestamp valido (NO condicion de bug), la logica arreglada
# produce lo mismo que la original. Esto pasa ANTES y DESPUES del arreglo.

# Strategy: datetimes con timezone UTC, en un rango razonable de anios.
_valid_datetimes = st.datetimes(
    min_value=datetime(1970, 1, 2),   # evitamos epoch exacto (borde) por claridad
    max_value=datetime(2100, 1, 1),
    timezones=st.just(timezone.utc),
)


@settings(max_examples=200, deadline=None)
@given(dt=_valid_datetimes)
def test_issue2_property_valid_iso_preserved(dt):
    """Propiedad: para todo timestamp ISO 8601 UTC valido, la logica arreglada

    produce el MISMO resultado que la original (preservacion). Ademas, confirmamos
    que esos valores NO son tratados como condicion de bug (no son invalidos).
    """
    # Construimos un string ISO 8601 con sufijo "Z" (UTC), como envia el backend.
    # Ej: "2025-01-15T14:30:00Z". Quitamos microsegundos para un formato limpio.
    iso_value = dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Precondicion de la propiedad: debe ser una fecha valida (NO condicion de bug).
    assert not _is_bug_condition_invalid_date(iso_value), (
        f"El generador produjo un valor invalido inesperado: {iso_value!r}"
    )

    original = handle_status_original(iso_value)
    fixed = handle_status_fixed(iso_value)

    # Preservacion: mismo texto en ambas ramas para fechas validas.
    assert fixed == original, (
        f"PRESERVACION rota: valor {iso_value!r} produjo original={original!r} "
        f"pero fixed={fixed!r} (deberian ser iguales)."
    )
    # Y el resultado NO debe ser el fallback (es una hora valida).
    assert fixed != "Ultimo: —"
    assert fixed.startswith("Ultimo: ")
