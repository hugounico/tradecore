"""Configuración común de pytest para toda la suite.

Paso 2 (T7): la app real (`src.api.app`) lee la configuración de acceso al importarse. Se carga
aquí una config sintética válida (ver `tests/auth_testkit.py`) para que las pruebas que usan la
app real la encuentren en estado OK. Solo afecta al proceso de pruebas; nunca a producción.
"""
import os

from tests.auth_testkit import AUTH_TEST_ENV

os.environ.update(AUTH_TEST_ENV)
