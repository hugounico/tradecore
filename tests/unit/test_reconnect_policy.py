"""Tests de T1.4 — ReconnectPolicy (reconnect + exponential backoff, abstraccion aislada).

Cubre el criterio de T1.4 con config inyectada y sleep FAKE (sin esperas reales):
exito 1er/2do/3er intento, agotamiento tras N, ausencia de intento N+1, delays observados,
fatal sin retry (incl. AUTH_FAILED equivalente), estados RECONNECTING/CONNECTED/DISCONNECTED,
y config distinta de [1,2,4] para demostrar que no esta hardcodeado.

No conecta a Databento, no usa API key, no toca componentes protegidos.
"""

import pytest

from src.live.connection_state import ConnectionState, ConnectionStateMachine
from src.live.reconnect_policy import (
    MVP_RECONNECT_CONFIG,
    FatalReconnectError,
    ReconnectConfig,
    ReconnectOutcome,
    ReconnectPolicy,
)


class FakeSleep:
    """Sleep async fake: registra los delays solicitados y completa inmediatamente."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _policy(config: ReconnectConfig, sleep: FakeSleep, is_fatal=None) -> ReconnectPolicy:
    return ReconnectPolicy(
        config=config,
        sleep=sleep,
        state_machine=ConnectionStateMachine(),
        is_fatal=is_fatal,
    )


def _op_sequence(results):
    """Construye una operacion de reconexion async que devuelve/lanza segun `results`.

    Cada elemento: True (conecta), False (falla no fatal) o una excepcion a lanzar.
    """
    it = iter(results)

    async def op() -> bool:
        outcome = next(it)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return op


async def test_success_on_first_attempt() -> None:
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    outcome = await p.run(_op_sequence([True]))
    assert outcome is ReconnectOutcome.RECONNECTED
    assert p.state is ConnectionState.CONNECTED
    assert p.attempts_made == 1
    assert sleep.delays == [1.0]  # espero 1s antes del unico intento


async def test_failure_then_success_on_second_attempt() -> None:
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    outcome = await p.run(_op_sequence([False, True]))
    assert outcome is ReconnectOutcome.RECONNECTED
    assert p.state is ConnectionState.CONNECTED
    assert p.attempts_made == 2
    assert sleep.delays == [1.0, 2.0]


async def test_success_on_third_attempt() -> None:
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    outcome = await p.run(_op_sequence([False, False, True]))
    assert outcome is ReconnectOutcome.RECONNECTED
    assert p.state is ConnectionState.CONNECTED
    assert p.attempts_made == 3
    assert sleep.delays == [1.0, 2.0, 4.0]


async def test_exhaustion_after_n_attempts() -> None:
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    outcome = await p.run(_op_sequence([False, False, False]))
    assert outcome is ReconnectOutcome.EXHAUSTED
    assert p.state is ConnectionState.DISCONNECTED
    assert p.attempts_made == 3


async def test_never_attempts_n_plus_one() -> None:
    """Con 3 fallos, la operacion se invoca EXACTAMENTE 3 veces (nunca un 4to intento)."""
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    calls = {"n": 0}

    async def op() -> bool:
        calls["n"] += 1
        return False

    outcome = await p.run(op)
    assert outcome is ReconnectOutcome.EXHAUSTED
    assert calls["n"] == 3  # exactamente N, no N+1
    assert len(sleep.delays) == 3


async def test_delays_follow_injected_config() -> None:
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    await p.run(_op_sequence([False, False, False]))
    assert sleep.delays == [1.0, 2.0, 4.0]


async def test_fatal_before_any_retry_zero_extra_attempts() -> None:
    """Condicion fatal en el primer intento -> FATAL, 1 intento consumido, 0 reintentos."""
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    outcome = await p.run(_op_sequence([FatalReconnectError("fatal")]))
    assert outcome is ReconnectOutcome.FATAL
    assert p.state is ConnectionState.DISCONNECTED
    assert p.attempts_made == 1  # se intento una vez y se detecto fatal; no hay reintentos


async def test_auth_failed_equivalent_fatal_zero_retry() -> None:
    """AUTH_FAILED (equivalente) via clasificador is_fatal -> 0 reintentos."""
    sleep = FakeSleep()

    class AuthError(Exception):
        pass

    def is_fatal(exc: BaseException) -> bool:
        return isinstance(exc, AuthError)  # p.ej. AUTH_FAILED

    p = _policy(MVP_RECONNECT_CONFIG, sleep, is_fatal=is_fatal)
    calls = {"n": 0}

    async def op() -> bool:
        calls["n"] += 1
        raise AuthError("AUTH_FAILED")

    outcome = await p.run(op)
    assert outcome is ReconnectOutcome.FATAL
    assert p.state is ConnectionState.DISCONNECTED
    assert calls["n"] == 1  # sin reintentos tras fatal


async def test_state_is_reconnecting_during_recovery() -> None:
    """Durante el ciclo (antes de resolver) el estado observable es RECONNECTING."""
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    observed: list[ConnectionState] = []

    async def op() -> bool:
        observed.append(p.state)  # estado mientras se intenta reconectar
        return False

    await p.run(op)
    assert all(s is ConnectionState.RECONNECTING for s in observed)
    assert observed  # se observo al menos un intento


async def test_success_transitions_to_connected() -> None:
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    await p.run(_op_sequence([True]))
    assert p.state is ConnectionState.CONNECTED


async def test_exhaustion_transitions_to_disconnected() -> None:
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    await p.run(_op_sequence([False, False, False]))
    assert p.state is ConnectionState.DISCONNECTED


async def test_config_other_than_1_2_4_not_hardcoded() -> None:
    """Config distinta demuestra que delays/max_attempts NO estan hardcodeados."""
    sleep = FakeSleep()
    cfg = ReconnectConfig(max_attempts=2, delays_seconds=(0.5, 3.0))
    p = _policy(cfg, sleep)
    outcome = await p.run(_op_sequence([False, False]))
    assert outcome is ReconnectOutcome.EXHAUSTED
    assert sleep.delays == [0.5, 3.0]  # delays inyectados, no [1,2,4]
    assert p.attempts_made == 2


async def test_record_stable_connection_resets_attempts() -> None:
    """El mecanismo de reset (sin inventar intervalo) vuelve el contador a 0."""
    sleep = FakeSleep()
    p = _policy(MVP_RECONNECT_CONFIG, sleep)
    await p.run(_op_sequence([False, True]))
    assert p.attempts_made == 2
    p.record_stable_connection()
    assert p.attempts_made == 0


def test_mvp_config_values() -> None:
    """La config MVP expone 3 intentos y delays [1,2,4] (constante de configuracion)."""
    assert MVP_RECONNECT_CONFIG.max_attempts == 3
    assert list(MVP_RECONNECT_CONFIG.delays_seconds) == [1.0, 2.0, 4.0]
    assert MVP_RECONNECT_CONFIG.stability_window_s is None  # RESET_INTERVAL_VALUE_DEFINED = NO
