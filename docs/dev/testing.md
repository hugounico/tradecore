# Testing — red de seguridad (A2)

Esta pagina explica la red de seguridad de tests: una **linea base** congelada de la version
V1 en produccion y un **CI** (integracion continua) que detecta regresiones automaticamente.
No cambia el comportamiento de la aplicacion.

Glosario breve (una linea c/u):
- **CI (integracion continua)**: GitHub ejecuta los tests automaticamente en cada push/PR.
- **linea base (baseline)**: foto del resultado de cada test en V1 (passed/failed/error/skipped).
- **JUnit XML**: formato estandar que pytest escribe con `--junitxml`; lista cada test y su resultado.
- **identidad de un test**: cadena `classname::name` del JUnit; se usa igual al generar y al verificar.
- **regresion**: un test que antes pasaba y ahora falla (lo que queremos evitar).

## Identidad y runtime

- Runtime de produccion: **Python 3.11.16** (misma version que la imagen desplegada).
- Dependencias fijadas por version en `requirements-ci.txt` (copia exacta del `pip freeze`
  verificado de la imagen de produccion). `requirements-dev.txt` solo hace `-r requirements-ci.txt`.
- La linea base vive en `tests/baseline/v1_baseline.json` y corresponde al commit
  `b1895b4c3981894deb137a89325e25bc27435163` (tag `v1.0-live`).

## Como se genero la linea base

Se ejecuto la suite DOS veces dentro de la imagen de produccion (sin red) y se combinaron
los dos reportes JUnit. Un test con resultados distintos entre las dos corridas se marca
**FLAKY**. Comando de generacion (conceptual; se corrio dentro del contenedor de la imagen):

```
python scripts/check_test_baseline.py --write junit_run1.xml junit_run2.xml \
    --out tests/baseline/v1_baseline.json \
    --commit b1895b4c3981894deb137a89325e25bc27435163
```

La herramienta `scripts/check_test_baseline.py` usa solo la biblioteca estandar de Python.

## Como funciona el CI

Workflow: `.github/workflows/tests.yml`. Se dispara en `pull_request` y `push` hacia
`develop` y `main`. Corre en `ubuntu-latest` dentro del contenedor `python:3.11.16-slim`
(misma version que produccion; la etiqueta se verifico con `docker manifest inspect`).

Pasos:
1. `actions/checkout@v4` — trae el codigo.
2. `pip install -r requirements-ci.txt` — instala las dependencias congeladas.
3. `python -m pip check` — verifica que no haya conflictos de dependencias.
4. `pytest --junitxml=junit.xml` con `continue-on-error: true` — los fallos conocidos de la
   linea base NO cortan aqui.
5. `python scripts/check_test_baseline.py --verify junit.xml --baseline tests/baseline/v1_baseline.json`
   — **este paso decide PASS/FAIL**.
6. Sube `junit.xml` como artifact.

El CI no usa secretos ni variables de Databento/AWS.

## Significado de cada estado (modo --verify)

Comparando la corrida actual contra la linea base, cada test cae en una categoria:

- **KNOWN_FAIL**: fallaba en la linea base y sigue fallando -> permitido (no es regresion nueva).
- **NEW_FAIL**: pasaba (o es un test nuevo) y ahora falla -> **FALLA el CI** (regresion).
- **FIXED**: fallaba en la linea base y ahora pasa -> permitido; imprime `BASELINE_CLEANUP_REQUIRED`
  (hay que actualizar la linea base, con aprobacion — ver abajo).
- **MISSING**: estaba en la linea base y ya no aparece -> **FALLA el CI** (test perdido).
- **FLAKY**: marcado flaky en la linea base -> permitido con cualquier resultado; se reporta.
- **NEW_TEST**: no estaba en la linea base y pasa -> permitido.

Ademas, un `junit.xml` inexistente, ilegible o con 0 tests -> **FALLA el CI**.

El CI falla unicamente si hay al menos un `NEW_FAIL` o un `MISSING`.

## Regla de modificacion de la linea base

`tests/baseline/v1_baseline.json` se crea una sola vez (A2) y **solo se modifica con
aprobacion explicita de Hugo**. El modo `--verify` NUNCA modifica la linea base. Cuando
aparezca `BASELINE_CLEANUP_REQUIRED` (porque un test paso de fallar a pasar, o se agregan
tests nuevos que deben quedar registrados), la actualizacion de la linea base se hace como
un cambio aparte, revisado y aprobado por Hugo.
