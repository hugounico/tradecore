"""
Script determinístico para validar que NQ.c.0 resuelve correctamente
via Databento Reference API (symbology.resolve).

Uso:
    python check_symbol.py

Requiere:
    - pip install databento
    - Variable de entorno DATABENTO_API_KEY configurada

Este script NO consume créditos de datos — symbology.resolve es gratuito.
"""

import os
import sys
from datetime import date, timedelta

# Verificar que la API key está configurada como variable de entorno
if not os.environ.get("DATABENTO_API_KEY"):
    print("ERROR: Variable de entorno DATABENTO_API_KEY no configurada.")
    print("Configúrala con: set DATABENTO_API_KEY=tu-api-key (Windows)")
    sys.exit(1)

try:
    import databento as db
except ImportError:
    print("ERROR: Instala el cliente de Databento con: pip install databento")
    sys.exit(1)


def main():
    """Resuelve NQ.c.0 y muestra a qué contrato real mapea."""

    # Crear cliente Reference (usa DATABENTO_API_KEY automáticamente)
    client = db.Historical()

    # Fecha de consulta: hoy (o ayer si es fin de semana)
    today = date.today()

    print(f"Resolviendo NQ.c.0 (continuous, calendar, front-month)...")
    print(f"Dataset: GLBX.MDP3")
    print(f"Fecha: {today.isoformat()}")
    print("-" * 50)

    try:
        # Resolver símbolo continuo a símbolo raw del exchange
        result = client.symbology.resolve(
            dataset="GLBX.MDP3",
            symbols="NQ.c.0",
            stype_in="continuous",
            stype_out="raw_symbol",
            start_date=today.isoformat(),
            end_date=today.isoformat(),
        )

        print(f"Resultado: {result}")
        print("-" * 50)
        print("✓ NQ.c.0 resuelve correctamente.")
        print("  El contrato activo se actualiza automáticamente en cada rollover.")

    except Exception as e:
        print(f"✗ Error al resolver símbolo: {e}")
        print()
        print("Posibles causas:")
        print("  - API key inválida o expirada")
        print("  - Mercado cerrado (intentar con fecha de día hábil)")
        print("  - Problema de conectividad")
        sys.exit(1)


if __name__ == "__main__":
    main()
