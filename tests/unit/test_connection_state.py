"""Tests de T1.3 — maquina de estados de conexion Live (`ConnectionStateMachine`).

Derivados del criterio de T1.3: "transiciones de estado deterministas y observables", con
los estados CONNECTED/RECONNECTING/DISCONNECTED y la propiedad `is_connected`.

NO se prueban politicas de reconexion (reintentos/backoff/timers): eso pertenece a T1.4.
No hay red real ni dependencia del connector protegido.
"""

from src.live.connection_state import ConnectionState, ConnectionStateMachine


def test_initial_state_is_disconnected() -> None:
    """Estado inicial determinista = DISCONNECTED, y is_connected = False."""
    sm = ConnectionStateMachine()
    assert sm.state is ConnectionState.DISCONNECTED
    assert sm.is_connected is False


def test_mark_connected_sets_connected_and_is_connected_true() -> None:
    """CONNECTED es el unico estado donde is_connected == True."""
    sm = ConnectionStateMachine()
    sm.mark_connected()
    assert sm.state is ConnectionState.CONNECTED
    assert sm.is_connected is True


def test_mark_reconnecting_is_not_connected() -> None:
    """RECONNECTING se considera NO conectado."""
    sm = ConnectionStateMachine()
    sm.mark_reconnecting()
    assert sm.state is ConnectionState.RECONNECTING
    assert sm.is_connected is False


def test_mark_disconnected_is_not_connected() -> None:
    """DISCONNECTED se considera NO conectado."""
    sm = ConnectionStateMachine()
    sm.mark_connected()  # primero conectar
    sm.mark_disconnected()
    assert sm.state is ConnectionState.DISCONNECTED
    assert sm.is_connected is False


def test_is_connected_only_true_for_connected() -> None:
    """Verificacion explicita del valor de is_connected en cada estado relevante."""
    sm = ConnectionStateMachine()
    sm.mark_disconnected()
    assert sm.is_connected is False
    sm.mark_reconnecting()
    assert sm.is_connected is False
    sm.mark_connected()
    assert sm.is_connected is True


def test_transitions_are_deterministic() -> None:
    """Cada transicion lleva SIEMPRE al mismo estado destino (determinismo)."""
    sm = ConnectionStateMachine()
    # Recorrido explicito por los tres estados y de vuelta.
    sm.mark_connected()
    assert sm.state is ConnectionState.CONNECTED
    sm.mark_reconnecting()
    assert sm.state is ConnectionState.RECONNECTING
    sm.mark_connected()  # reconexion "exitosa" desde el punto de vista del estado
    assert sm.state is ConnectionState.CONNECTED
    sm.mark_disconnected()
    assert sm.state is ConnectionState.DISCONNECTED


def test_transitions_are_idempotent() -> None:
    """Repetir una transicion mantiene el mismo estado (idempotencia determinista)."""
    sm = ConnectionStateMachine()
    sm.mark_connected()
    sm.mark_connected()
    assert sm.state is ConnectionState.CONNECTED
    assert sm.is_connected is True


def test_two_instances_are_independent() -> None:
    """Dos instancias no comparten estado (estado por-objeto, observable y aislado)."""
    a = ConnectionStateMachine()
    b = ConnectionStateMachine()
    a.mark_connected()
    assert a.is_connected is True
    assert b.is_connected is False
