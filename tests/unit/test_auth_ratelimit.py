"""Pruebas de T4: TokenBucket (límite de tasa por proceso, enteros exactos).

Reloj inyectable (ns). Sin red, sin AWS. Las pruebas se escriben antes de implementar.
Los atributos del módulo se leen DENTRO de cada prueba para que la colección no falle con el
stub (que no define constantes).
"""
from __future__ import annotations

import time

import pytest

from src.api.auth import ratelimit as rl

NS = 1_000_000_000  # 1 ficha = 1e9 unidades (1 segundo en ns)


class _Clock:
    """Reloj controlable: devuelve el valor fijado; cuenta cuántas veces se lee."""

    def __init__(self, start: int = 0) -> None:
        self.t = start
        self.reads = 0

    def __call__(self) -> int:
        self.reads += 1
        return self.t

    def advance(self, ns: int) -> None:
        self.t += ns


# ================================================================================
# 1. Constantes y reloj por defecto
# ================================================================================
def test_constants():
    assert rl.CAPACITY == 20
    assert rl.REFILL_PER_SECOND == 5
    assert rl.RETRY_AFTER_SECONDS == 1


def test_default_clock_is_monotonic_ns():
    # El reloj por defecto es time.monotonic_ns (se puede crear sin pasar clock).
    b = rl.TokenBucket()
    assert b.try_acquire() is True


# ================================================================================
# 2. Capacidad inicial 20, el 21.º falla
# ================================================================================
def test_capacity_20_then_fail():
    clk = _Clock()
    b = rl.TokenBucket(clock=clk)
    assert all(b.try_acquire() for _ in range(20))
    assert b.try_acquire() is False


# ================================================================================
# 3. Recarga fina: 199_999_999 ns insuficiente, +1 ns alcanza
# ================================================================================
def test_refill_fine_grained():
    clk = _Clock()
    b = rl.TokenBucket(clock=clk)
    for _ in range(20):
        b.try_acquire()
    assert b.try_acquire() is False     # vacío
    clk.advance(199_999_999)            # 5 fichas/s => 1 ficha en 200_000_000 ns
    assert b.try_acquire() is False     # aún falta 1 ns
    clk.advance(1)
    assert b.try_acquire() is True      # ahora sí
    assert b.try_acquire() is False     # y vuelve a estar vacío


# ================================================================================
# 4. +10 s recarga exactamente hasta capacidad (20), no más
# ================================================================================
def test_refill_capped_at_capacity():
    clk = _Clock()
    b = rl.TokenBucket(clock=clk)
    for _ in range(20):
        b.try_acquire()
    clk.advance(10 * NS)                 # 10 s * 5 = 50 fichas, pero el tope es 20
    assert sum(1 for _ in range(100) if b.try_acquire()) == 20
    assert b.try_acquire() is False


# ================================================================================
# 5. Reloj que retrocede: no lanza, no recarga
# ================================================================================
def test_clock_going_backwards():
    clk = _Clock(start=10 * NS)
    b = rl.TokenBucket(clock=clk)
    for _ in range(20):
        b.try_acquire()
    clk.t = 5 * NS                       # retrocede 5 s
    assert b.try_acquire() is False      # no recarga ni lanza
    clk.t = 10 * NS                      # vuelve al last mayor
    assert b.try_acquire() is False      # elapsed respecto del last (10s) = 0
    clk.advance(200_000_000)             # 1 ficha
    assert b.try_acquire() is True


# ================================================================================
# 6. ValueError en argumentos inválidos
# ================================================================================
@pytest.mark.parametrize("bad", [0, -1, True, 2.0], ids=["zero", "neg", "bool", "float"])
def test_capacity_invalid(bad):
    with pytest.raises(ValueError):
        rl.TokenBucket(capacity=bad)


@pytest.mark.parametrize("bad", [0, -1, True, 2.0], ids=["zero", "neg", "bool", "float"])
def test_refill_invalid(bad):
    with pytest.raises(ValueError):
        rl.TokenBucket(refill_per_second=bad)


# ================================================================================
# 7. Dos instancias no comparten fichas
# ================================================================================
def test_two_instances_independent():
    clk = _Clock()
    a = rl.TokenBucket(clock=clk)
    b = rl.TokenBucket(clock=clk)
    for _ in range(20):
        a.try_acquire()
    assert a.try_acquire() is False
    assert b.try_acquire() is True       # b sigue lleno


# ================================================================================
# 8. Lecturas del reloj: 1 en el constructor, 1 por try_acquire
# ================================================================================
def test_clock_reads_count():
    clk = _Clock()
    b = rl.TokenBucket(clock=clk)
    assert clk.reads == 1                # constructor lee 1 vez
    b.try_acquire()
    assert clk.reads == 2
    b.try_acquire()
    assert clk.reads == 3
