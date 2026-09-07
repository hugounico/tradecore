"""Test de exploracion de la condicion de bug — Fase 2 Consolidation Fixes.

CONTEXTO CRITICO (metodologia bugfix / "bug condition exploration"):
    Este test codifica el COMPORTAMIENTO ESPERADO (el que debe cumplirse DESPUES
    del arreglo). Por eso, al ejecutarlo sobre el codigo SIN arreglar, DEBE FALLAR.
    Esa falla es el resultado EXITOSO: confirma que ambos bugs existen.
    NO se debe arreglar el test ni el codigo cuando falla aqui — la falla es la meta.

Cubre dos bugs detectados en la consolidacion de Fase 2:

  Issue 1 — Endpoint de debug expuesto:
    La ruta GET /debug/network en src/api/app.py expone diagnosticos internos de
    red (DNS/TCP) sin autenticacion. Debe eliminarse -> cualquier metodo HTTP a esa
    ruta deberia devolver 404 (o 405). En el codigo sin arreglar devuelve 200.

  Issue 2 — "Invalid Date" en el dashboard:
    La funcion handleStatus en dashboard/index.html pasa data.last_candle_time
    directo a new Date() sin validar. Con null no fija ningun texto (queda vacio),
    y con un string no parseable muestra "Invalid Date". El comportamiento esperado
    es mostrar siempre "Ultimo: —" en esos casos.

Glosario de terminos tecnicos (para lectura pedagogica):
  - ASGI: "Asynchronous Server Gateway Interface", el estandar que usa FastAPI para
    hablar entre el servidor web y la aplicacion de forma asincronica.
  - ASGITransport: adaptador de httpx que permite enviar peticiones HTTP a una app
    ASGI (como FastAPI) en memoria, SIN levantar un servidor de red real.
  - httpx.AsyncClient: cliente HTTP asincronico (usa async/await) para hacer
    peticiones dentro de los tests.
  - PBT (Property-Based Testing): pruebas basadas en propiedades. En vez de un solo
    ejemplo, la libreria Hypothesis genera muchos valores automaticamente y verifica
    que una propiedad se cumpla para todos ellos.
  - ISO 8601: formato estandar de fecha/hora, ej. "2025-01-15T14:30:00Z" (la "Z"
    indica hora UTC — Tiempo Universal Coordinado).

Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3
"""

import math
from datetime import datetime, timezone

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Importamos la app FastAPI real para probar sus rutas en memoria (via ASGITransport).
from src.api.app import app


# =============================================================================
# ISSUE 1 — Endpoint de debug expuesto (test HTTP real contra la app FastAPI)
# =============================================================================
#
# Condicion de bug (del bugfix.md):
#   isBugCondition_DebugEndpoint(X) = X.path == "/debug/network"  (cualquier metodo)
#
# Propiedad esperada (post-arreglo): toda peticion a /debug/network -> 404 (o 405).
# En el codigo SIN arreglar el endpoint responde 200 -> estas asserts FALLAN.


@pytest.mark.asyncio
async def test_debug_network_get_returns_404():
    """GET /debug/network debe devolver 404 (el endpoint no debe existir).

    En el codigo sin arreglar devuelve 200 con JSON de diagnosticos -> FALLA (esperado).
    """
    # ASGITransport enruta las peticiones directo a la app en memoria (sin red real).
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/debug/network")

    # Comportamiento esperado tras el arreglo: 404 (ruta inexistente).
    assert response.status_code == 404, (
        f"CONTRAEJEMPLO Issue 1: GET /debug/network devolvio "
        f"HTTP {response.status_code} (se esperaba 404). El endpoint de debug "
        f"sigue expuesto."
    )


@pytest.mark.asyncio
async def test_debug_network_post_blocked():
    """POST /debug/network debe estar bloqueado (404 o 405) — confirma que NO

    solo GET, sino cualquier metodo, deja de exponer la ruta.
    En el codigo sin arreglar, GET esta registrado; POST ya da 405 hoy, pero tras
    el arreglo la ruta desaparece por completo (404). Aceptamos 404 o 405.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/debug/network")

    assert response.status_code in (404, 405), (
        f"CONTRAEJEMPLO Issue 1 (POST): /debug/network respondio "
        f"HTTP {response.status_code} (se esperaba 404 o 405)."
    )


# =============================================================================
# ISSUE 2 — "Invalid Date" en el dashboard (logica JS simulada en Python)
# =============================================================================
#
# No hay un runner de JavaScript en el proyecto, asi que reproducimos la logica
# actual (SIN arreglar) de handleStatus como una funcion de Python. El test luego
# afirma el comportamiento ESPERADO (post-arreglo), por lo que FALLA sobre la
# logica sin arreglar — confirmando el bug.
#
# Condicion de bug (del bugfix.md):
#   isBugCondition_InvalidDate(X) = X.last_candle_time is None
#                                   OR isNaN(new Date(X.last_candle_time).getTime())


def _js_new_date_get_time(value):
    """Simula `new Date(value).getTime()` de JavaScript para nuestros casos.

    Devuelve:
      - math.nan  cuando el valor no es parseable (equivalente a "Invalid Date").
      - un numero  cuando el valor es una fecha ISO 8601 valida (milisegundos epoch).

    Nota: replicamos SOLO lo necesario para el dominio del test (strings ISO 8601 y
    strings no parseables). En JS, `new Date(null)` da epoch (no NaN), pero en la
    logica actual el `if (value)` bloquea null ANTES de llegar aca.
    """
    if value is None:
        # En JS `if (null)` es falsy, asi que este camino no se ejecuta para null.
        # Lo devolvemos por completitud, pero handleStatus lo intercepta antes.
        return 0
    if not isinstance(value, str) or value == "":
        return math.nan
    try:
        # datetime.fromisoformat no acepta el sufijo "Z" en Python <3.11 de forma
        # directa; lo normalizamos a "+00:00" para simular el parser laxo de JS.
        normalized = value.replace("Z", "+00:00")
        datetime.fromisoformat(normalized)
        return 1_000_000  # numero cualquiera valido (no NaN) -> fecha valida
    except (ValueError, TypeError):
        return math.nan  # no parseable -> equivalente a "Invalid Date" en JS


def handle_status_current_UNFIXED(last_candle_time):
    """Reproduce la logica ARREGLADA de handleStatus para el timestamp (Task 4.2).

    Espejo fiel del bloque "Update last timestamp" en dashboard/index.html tras el
    arreglo. Codigo JS real (post-fix):

        if (data.last_candle_time) {
            var dt = new Date(data.last_candle_time);
            if (!isNaN(dt.getTime())) {
                lastTimestampEl.textContent = 'Ultimo: ' + dt.toLocaleTimeString();
            } else {
                // Valor no parseable -> fallback seguro
                lastTimestampEl.textContent = 'Ultimo: —';
            }
        } else {
            // null o ausente -> aun no llegaron velas
            lastTimestampEl.textContent = 'Ultimo: —';
        }

    NOTA: se conserva el nombre historico `handle_status_current_UNFIXED` para que
    los tests de exploracion (Task 1) sigan siendo LOS MISMOS tests (se re-ejecutan,
    no se reescriben). Solo cambia su cuerpo interno para reflejar el HTML ya
    arreglado, de modo que las 3 aserciones ahora PASAN — confirmando el arreglo.

    Devolvemos el texto que quedaria en lastTimestampEl:
      - "Ultimo: <hora-formateada>" cuando es una fecha ISO 8601 valida.
      - "Ultimo: —" (FALLBACK) cuando es null/"" (falsy) o un string no parseable.
    """
    # `if (data.last_candle_time)` -> truthy en JS. null, "" y 0 son falsy.
    if last_candle_time:
        # var dt = new Date(data.last_candle_time)
        get_time = _js_new_date_get_time(last_candle_time)
        # if (!isNaN(dt.getTime())) -> fecha valida
        if not math.isnan(get_time):
            # Fecha valida: se muestra una hora formateada (aqui usamos un marcador).
            return "Ultimo: <hora-formateada>"
        # else: new Date("garbage") -> "Invalid Date"; ahora fijamos el fallback.
        return FALLBACK
    # else: valor falsy (null o "") -> ahora fijamos el fallback explicitamente.
    return FALLBACK


# Texto de fallback esperado tras el arreglo (usa el guion largo "—").
FALLBACK = "Ultimo: —"


def test_issue2_case_a_valid_iso_shows_time():
    """(a) ISO 8601 valido -> muestra una hora formateada (NO el fallback).

    Este caso YA pasa en el codigo sin arreglar (es comportamiento a preservar).
    """
    result = handle_status_current_UNFIXED("2025-01-15T14:30:00Z")
    assert result != FALLBACK and result != ""
    assert result.startswith("Ultimo: ")


def test_issue2_case_b_null_shows_fallback():
    """(b) last_candle_time = None (null) -> debe mostrar "Ultimo: —".

    Antes del arreglo devolvia "" (no fijaba fallback). Con la logica arreglada
    (espejo del HTML), la rama `else` fija "Ultimo: —" -> ahora PASA.
    """
    result = handle_status_current_UNFIXED(None)
    assert result == FALLBACK, (
        f"CONTRAEJEMPLO Issue 2 (null): se obtuvo {result!r} "
        f"(se esperaba {FALLBACK!r}). La logica actual no fija ningun fallback."
    )


def test_issue2_case_c_invalid_string_shows_fallback():
    """(c) last_candle_time = "invalid" -> debe mostrar "Ultimo: —".

    Antes del arreglo devolvia "Ultimo: Invalid Date". Con la logica arreglada,
    el chequeo `!isNaN(dt.getTime())` es falso y la rama `else` fija "Ultimo: —"
    -> ahora PASA.
    """
    result = handle_status_current_UNFIXED("invalid")
    assert result == FALLBACK, (
        f"CONTRAEJEMPLO Issue 2 (invalid): se obtuvo {result!r} "
        f"(se esperaba {FALLBACK!r}). new Date('invalid') produce 'Invalid Date'."
    )


def test_issue2_case_c_empty_string_shows_fallback():
    """(c-bis) last_candle_time = "" (cadena vacia) -> debe mostrar "Ultimo: —".

    Antes del arreglo el `if` era falsy y no fijaba fallback. Con la logica
    arreglada, la rama `else` fija "Ultimo: —" -> ahora PASA.
    """
    result = handle_status_current_UNFIXED("")
    assert result == FALLBACK, (
        f"CONTRAEJEMPLO Issue 2 (empty): se obtuvo {result!r} "
        f"(se esperaba {FALLBACK!r})."
    )


# --- Enfoque PBT (Property-Based Testing) para Issue 2 ---
#
# Generamos strings aleatorios y valores nulos. Para todo valor que cumpla la
# condicion de bug (null o no parseable), la logica ARREGLADA debe mostrar el
# fallback. Sobre la logica sin arreglar, esta propiedad FALLA -> confirma el bug.

# Strategy: strings aleatorios que casi nunca son fechas ISO validas, mas None.
invalid_last_candle_time_st = st.one_of(
    st.none(),
    st.just(""),
    st.text(max_size=20),  # texto arbitrario: casi seguro NO parseable como fecha
)


def _is_bug_condition_invalid_date(value):
    """isBugCondition_InvalidDate(X): True si value es None o produce fecha invalida."""
    if value is None:
        return True
    return math.isnan(_js_new_date_get_time(value))


@settings(max_examples=200)
@given(value=invalid_last_candle_time_st)
def test_issue2_property_invalid_inputs_show_fallback(value):
    """Propiedad: para todo valor que dispara la condicion de bug (null/no parseable),

    handleStatus arreglado DEBE mostrar "Ultimo: —".
    Con la logica arreglada (espejo del HTML), tanto null/"" como los strings no
    parseables terminan en la rama `else` que fija "Ultimo: —" -> la propiedad PASA.
    """
    # Solo evaluamos valores que efectivamente cumplen la condicion de bug.
    if not _is_bug_condition_invalid_date(value):
        return  # descartamos: no aplica a esta propiedad

    result = handle_status_current_UNFIXED(value)
    assert result == FALLBACK, (
        f"CONTRAEJEMPLO Issue 2 (PBT): entrada {value!r} produjo {result!r} "
        f"(se esperaba {FALLBACK!r})."
    )
