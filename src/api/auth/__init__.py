"""Paquete de control de acceso (login) de TradeCore.

Contiene la configuración aislada de autenticación (AuthSettings / AuthConfig),
la verificación de tokens, la sesión por cookie, el límite de tasa, el middleware
de control de acceso y las rutas de login. Ningún módulo de este paquete modifica
el pipeline de datos de mercado ni la configuración principal (`src/config.py`).
"""
