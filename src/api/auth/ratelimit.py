"""Límite de tasa global (token bucket) con enteros exactos, por proceso (§ Design F4).

Un "token bucket" (cubo de fichas) arranca lleno y se recarga a ritmo constante; cada petición
consume una ficha. Si no hay fichas, se rechaza. Aquí NO se usa `float`: 1 ficha equivale a
1_000_000_000 unidades (el mismo factor que 1 segundo en nanosegundos), de modo que la recarga
`elapsed_ns * refill_per_second` queda en enteros exactos.

Sin async, sin hilos, sin logs y sin instancia a nivel de módulo: la app corre en un solo
proceso, así que un bucket en memoria basta. T6 crea la instancia concreta.
"""
from __future__ import annotations

import time
from collections.abc import Callable

CAPACITY = 20              # fichas máximas (ráfaga permitida)
REFILL_PER_SECOND = 5      # fichas que se reponen por segundo
RETRY_AFTER_SECONDS = 1    # valor sugerido de Retry-After al rechazar (lo usa T6)

# 1 ficha = 1e9 unidades internas. Igual que ns por segundo: así `elapsed_ns * refill`
# incrementa en "unidades" sin fracciones.
_UNITS_PER_TOKEN = 1_000_000_000


class TokenBucket:
    """Cubo de fichas con reloj inyectable (por defecto `time.monotonic_ns`)."""

    def __init__(
        self,
        *,
        capacity: int = CAPACITY,
        refill_per_second: int = REFILL_PER_SECOND,
        clock: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        # `type(v) is int` rechaza bool (True/False son int en Python) y float.
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity debe ser int >= 1")
        if type(refill_per_second) is not int or refill_per_second < 1:
            raise ValueError("refill_per_second debe ser int >= 1")
        self._capacity_units = capacity * _UNITS_PER_TOKEN
        self._refill = refill_per_second
        self._clock = clock
        self._units = self._capacity_units          # arranca lleno
        self._last = clock()                         # una única lectura en el constructor

    def try_acquire(self) -> bool:
        """Intenta consumir una ficha. Devuelve True si había; False si no. Nunca lanza."""
        now = self._clock()                          # exactamente una lectura
        elapsed = now - self._last
        if elapsed > 0:
            # Recarga proporcional al tiempo transcurrido, tope en la capacidad.
            self._units = min(self._capacity_units, self._units + elapsed * self._refill)
            self._last = now
        # Si el reloj retrocede (elapsed <= 0): no recarga, no mueve last, no lanza.
        if self._units >= _UNITS_PER_TOKEN:
            self._units -= _UNITS_PER_TOKEN
            return True
        return False
