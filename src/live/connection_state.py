"""T1.3 — Estado de conexion Live (`is_connected`) — abstraccion aislada.

Modela, de forma DETERMINISTA y OBSERVABLE, el estado de la conexion Live a Databento con
exactamente los tres estados que exige tasks.md:

    CONNECTED / RECONNECTING / DISCONNECTED

(Nombres tomados literalmente del spec T1.3.)

Es una maquina de estados pura, en memoria, SIN red real y SIN depender del connector
protegido. Existe para que otras piezas Live (p.ej. T1.4) puedan observar el estado de
conexion mediante una interfaz limpia.

FRONTERA (lo que T1.3 NO hace — pertenece a T1.4 u otras Tasks):
- NO decide politica de reconexion: sin numero de reintentos, sin delays, sin backoff, sin
  jitter, sin temporizadores. (Esos valores dependen de G0.1/G0.2, aun SIN resolver.)
- NO maneja errores de red, NO llama a Databento, NO hace subscribe_live(), NO reconnect real.
- NO conoce warm-up, LIVE_SYNCED, dedup, orden de velas ni LIVE_BAR_FINALITY.

Este modulo solo REPRESENTA en que estado esta la conexion y expone transiciones explicitas
para moverse entre esos estados; QUIEN llama decide cuando invocarlas (esa decision/politica
es de otra Task).
"""

from __future__ import annotations

from enum import Enum


class ConnectionState(Enum):
    """Los tres estados de conexion Live definidos por T1.3.

    `Enum` = tipo enumerado: un conjunto cerrado y explicito de valores posibles, lo que hace
    el estado determinista y facil de inspeccionar en tests.
    """

    DISCONNECTED = "DISCONNECTED"  # no hay conexion activa (estado inicial)
    CONNECTED = "CONNECTED"        # conexion activa y en uso
    RECONNECTING = "RECONNECTING"  # se perdio la conexion y se esta intentando restablecer
    # NOTA: RECONNECTING solo MODELA el estado; la POLITICA de reconexion (reintentos/backoff)
    # es responsabilidad de T1.4 y no vive aqui.


class ConnectionStateMachine:
    """Maquina de estados observable de la conexion Live (determinista, en memoria).

    Estado inicial: DISCONNECTED (aun no se ha conectado nada).

    Transiciones explicitas (metodos). Son deterministas: dado un estado, cada metodo lleva
    siempre al mismo estado destino, independientemente de tiempo o red:

        mark_connected()      -> CONNECTED       (desde cualquier estado)
        mark_reconnecting()   -> RECONNECTING    (desde cualquier estado)
        mark_disconnected()   -> DISCONNECTED    (desde cualquier estado)

    Se permite invocar cualquier transicion desde cualquier estado (idempotente si ya se esta
    en el destino). El spec no define una politica de "transicion invalida", por lo que NO se
    inventa una maquina restrictiva: simplemente se fija el estado destino de forma explicita.
    """

    def __init__(self) -> None:
        # Estado inicial determinista: sin conexion.
        self._state: ConnectionState = ConnectionState.DISCONNECTED

    @property
    def state(self) -> ConnectionState:
        """Estado actual (observable)."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """True unicamente cuando el estado es CONNECTED.

        RECONNECTING y DISCONNECTED se consideran "no conectado": mientras se reintenta o no
        hay conexion, el sistema NO debe tratarse como conectado.
        """
        return self._state is ConnectionState.CONNECTED

    def mark_connected(self) -> None:
        """Transiciona a CONNECTED (conexion establecida/activa)."""
        self._state = ConnectionState.CONNECTED

    def mark_reconnecting(self) -> None:
        """Transiciona a RECONNECTING (se perdio la conexion y se intenta restablecer).

        Solo marca el estado. No implementa reintentos ni backoff (eso es T1.4).
        """
        self._state = ConnectionState.RECONNECTING

    def mark_disconnected(self) -> None:
        """Transiciona a DISCONNECTED (sin conexion)."""
        self._state = ConnectionState.DISCONNECTED
