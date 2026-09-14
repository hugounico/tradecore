"""Paquete `src.persistence` — abstraccion de persistencia (Live v1, Wave 3).

Define contratos de persistencia INDEPENDIENTES del motor concreto (PostgreSQL u otro).
El resto de TradeCore depende de estas interfaces, no de una base de datos especifica.
No decide despliegue fisico (RDS vs contenedor), esquema fisico, ni migraciones — eso
corresponde a Tasks posteriores (T3.2+) y a los Gates G0.9/G0.10.
"""
