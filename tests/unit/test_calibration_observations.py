"""Tests deterministas de LAYER A (observaciones) — design.md 9-bis.8-ALT, 5.3-5.8.

Fixtures sinteticos; valores esperados calculados a mano contra el design.md.
"""

from datetime import datetime, timezone

import pytest

from src.calibration.observations import (
    ProductiveState,
    ProductiveUnknownReason,
    RescueState,
    StopPathObservation,
    TriggerState,
    assert_consecutive_step,
    count_rescued_population,
    count_step_population,
    productive_rescue,
    rescued_signal,
    rescued_signal_for_step,
    stop_would_trigger,
)
from src.schemas.candle import Candle
from src.schemas.signal import SignalType

_TS = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _bar(high: float, low: float) -> Candle:
    """Vela minima para tests de excursion (open/close/volume irrelevantes aqui)."""
    return Candle(timestamp=_TS, open=(high + low) / 2, high=high, low=low,
                  close=(high + low) / 2, volume=1)


class TestStopWouldTriggerThreeStates:
    """Semantica TERNARIA de stop_would_trigger (design.md 5.3 / 5.4)."""

    def test_true_when_touched_before_boundary(self) -> None:
        # BUY: ref=100, stop_points=5 -> stop_price=95. Alguna vela con low<=95 -> TRUE.
        # A mano: velas t+1..: [low 98], [low 94 (toca)], cruce inverso en indice 3.
        bars = [_bar(101, 98), _bar(99, 94), _bar(100, 97), _bar(100, 96)]
        result = stop_would_trigger(
            direction=SignalType.BUY,
            reference_price=100.0,
            stop_points=5.0,
            post_signal_bars=bars,
            inverse_crossover_index_or_None=3,
            calibration_boundary_reached_flag=False,
        )
        assert result == TriggerState.TRUE

    def test_false_when_inverse_crossover_before_boundary_no_touch(self) -> None:
        # BUY: stop_price=95. Ninguna vela toca 95 y hay cruce inverso antes del limite.
        # A mano: lows [98, 96, 97] nunca <= 95, cruce inverso en indice 2 -> FALSE.
        bars = [_bar(101, 98), _bar(100, 96), _bar(100, 97)]
        result = stop_would_trigger(
            direction=SignalType.BUY,
            reference_price=100.0,
            stop_points=5.0,
            post_signal_bars=bars,
            inverse_crossover_index_or_None=2,
            calibration_boundary_reached_flag=False,
        )
        assert result == TriggerState.FALSE

    def test_unknown_when_boundary_reached_no_touch_no_crossover(self) -> None:
        # BUY: stop_price=95. Sin toque y sin cruce inverso, limite alcanzado -> UNKNOWN.
        bars = [_bar(101, 98), _bar(100, 96), _bar(100, 97)]
        result = stop_would_trigger(
            direction=SignalType.BUY,
            reference_price=100.0,
            stop_points=5.0,
            post_signal_bars=bars,
            inverse_crossover_index_or_None=None,
            calibration_boundary_reached_flag=True,
        )
        assert result == TriggerState.UNKNOWN

    def test_never_unknown_becomes_false(self) -> None:
        # Sin cruce inverso y sin bandera de limite: no hay evidencia -> NUNCA false.
        bars = [_bar(101, 98), _bar(100, 96)]
        result = stop_would_trigger(
            direction=SignalType.BUY,
            reference_price=100.0,
            stop_points=5.0,
            post_signal_bars=bars,
            inverse_crossover_index_or_None=None,
            calibration_boundary_reached_flag=False,
        )
        assert result != TriggerState.FALSE
        assert result == TriggerState.UNKNOWN

    def test_sell_touch(self) -> None:
        # SELL: ref=100, stop_points=5 -> stop_price=105. Alguna vela con high>=105 -> TRUE.
        bars = [_bar(102, 99), _bar(106, 101), _bar(100, 97)]
        result = stop_would_trigger(
            direction=SignalType.SELL,
            reference_price=100.0,
            stop_points=5.0,
            post_signal_bars=bars,
            inverse_crossover_index_or_None=2,
            calibration_boundary_reached_flag=False,
        )
        assert result == TriggerState.TRUE


class TestCensoringIndependence:
    """signal_path_censored y stop_path_censored son INDEPENDIENTES (design.md 5.7 / 5.8)."""

    def test_partial_censoring_stays_valid(self) -> None:
        # Caso 5.8: signal_path_censored=true PERO stop tocado (trigger=true) y
        # stop_path_censored=false -> la observacion del STOP PATH sigue siendo valida.
        obs = StopPathObservation(
            trigger=TriggerState.TRUE,
            stop_path_censored=False,
            signal_path_censored=True,
        )
        assert obs.is_valid_stop_observation() is True

    def test_stop_censored_not_valid(self) -> None:
        # stop_path_censored=true (trigger unknown) -> NO valida como observacion de stop.
        obs = StopPathObservation(
            trigger=TriggerState.UNKNOWN,
            stop_path_censored=True,
            signal_path_censored=False,
        )
        assert obs.is_valid_stop_observation() is False


class TestRescuedSignal:
    """rescued_signal para multiplicadores consecutivos (design.md 9-bis.8-ALT)."""

    def test_true_false_gives_true(self) -> None:
        # true/false -> rescate (el estrecho se toca, el ancho no).
        assert rescued_signal(TriggerState.TRUE, TriggerState.FALSE) == RescueState.TRUE

    def test_true_true_gives_false(self) -> None:
        assert rescued_signal(TriggerState.TRUE, TriggerState.TRUE) == RescueState.FALSE

    def test_false_false_gives_false(self) -> None:
        assert rescued_signal(TriggerState.FALSE, TriggerState.FALSE) == RescueState.FALSE

    def test_true_unknown_gives_unknown(self) -> None:
        # Cualquier unknown -> unknown (excluido del observable, nunca false).
        assert rescued_signal(TriggerState.TRUE, TriggerState.UNKNOWN) == RescueState.UNKNOWN

    def test_unknown_false_gives_unknown(self) -> None:
        assert rescued_signal(TriggerState.UNKNOWN, TriggerState.FALSE) == RescueState.UNKNOWN

    def test_non_consecutive_step_raises(self) -> None:
        # (1.50, 2.00) NO es consecutivo -> ValueError.
        with pytest.raises(ValueError):
            assert_consecutive_step(1.50, 2.00)
        with pytest.raises(ValueError):
            rescued_signal_for_step(1.50, 2.00, TriggerState.TRUE, TriggerState.FALSE)

    def test_consecutive_step_ok(self) -> None:
        # (1.50, 1.75) SI es consecutivo -> no lanza y decide correctamente.
        result = rescued_signal_for_step(1.50, 1.75, TriggerState.TRUE, TriggerState.FALSE)
        assert result == RescueState.TRUE


class TestProductiveRescue:
    """productive_rescue: ventana empieza tras el toque (design.md 9-bis.8-ALT)."""

    def test_buy_productive(self) -> None:
        # BUY: ref=100. Tras el toque, alguna vela con high>=100 -> productivo TRUE.
        bars = [_bar(98, 96), _bar(101, 99)]  # segunda vela high=101 >= 100
        result = productive_rescue(SignalType.BUY, 100.0, first_stop_touch_bar_index=5,
                                   bars_strictly_after_stop_touch=bars)
        assert result.state == ProductiveState.TRUE

    def test_sell_productive(self) -> None:
        # SELL: ref=100. Tras el toque, alguna vela con low<=100 -> productivo TRUE.
        bars = [_bar(104, 102), _bar(103, 99)]  # segunda vela low=99 <= 100
        result = productive_rescue(SignalType.SELL, 100.0, first_stop_touch_bar_index=5,
                                   bars_strictly_after_stop_touch=bars)
        assert result.state == ProductiveState.TRUE

    def test_full_window_no_recovery_false(self) -> None:
        # BUY: ref=100. Ninguna vela posterior alcanza 100 -> FALSE (habia velas evaluables).
        bars = [_bar(98, 96), _bar(99, 97), _bar(98, 95)]
        result = productive_rescue(SignalType.BUY, 100.0, first_stop_touch_bar_index=5,
                                   bars_strictly_after_stop_touch=bars)
        assert result.state == ProductiveState.FALSE

    def test_touch_bar_excluded(self) -> None:
        # La vela del toque NO cuenta aunque alcance ref: la lista provista YA excluye la
        # vela del toque. Aqui damos SOLO velas posteriores que no recuperan -> FALSE, aun
        # si conceptualmente la vela del toque hubiera tocado 100.
        bars = [_bar(98, 96)]  # unica vela posterior, no alcanza 100
        result = productive_rescue(SignalType.BUY, 100.0, first_stop_touch_bar_index=5,
                                   bars_strictly_after_stop_touch=bars)
        assert result.state == ProductiveState.FALSE

    def test_touch_bar_excluded_even_if_it_reaches_reference(self) -> None:
        # FIX 5 (contrato explicito): una vela que EN EL TOQUE alcanzaria reference_price
        # NO debe estar en la lista y NO debe hacer productivo el rescate.
        # t_stop (high=100 >= ref) queda EXCLUIDA por contrato; la unica vela evaluable
        # (t_stop+1) no recupera el ancla -> FALSE, no TRUE.
        touch_bar = _bar(100, 94)  # high=100 tocaria el ancla, pero es la vela del toque
        bar_after_touch = _bar(98, 96)  # t_stop+1: no recupera reference_price
        # Solo se pasan las velas ESTRICTAMENTE posteriores al toque (la del toque queda fuera).
        bars = [bar_after_touch]
        result = productive_rescue(SignalType.BUY, 100.0, first_stop_touch_bar_index=5,
                                   bars_strictly_after_stop_touch=bars)
        # La vela del toque no forma parte de la evaluacion: no puede volver productivo el rescate.
        assert result.state == ProductiveState.FALSE
        # Y para dejarlo explicito: el high de la vela del toque no esta entre las evaluadas.
        assert touch_bar.high not in [b.high for b in bars]

    def test_no_post_touch_bar_unknown(self) -> None:
        # Sin vela evaluable posterior al toque -> unknown con motivo no_post_touch_bar.
        result = productive_rescue(SignalType.BUY, 100.0, first_stop_touch_bar_index=5,
                                   bars_strictly_after_stop_touch=[])
        assert result.state == ProductiveState.UNKNOWN
        assert result.reason == ProductiveUnknownReason.NO_POST_TOUCH_BAR


class TestPopulationCounters:
    """Contadores de poblacion y censura (design.md 9-bis.4 / 9-bis.8-ALT)."""

    def test_step_population_counts(self) -> None:
        # A mano: 3 true, 1 false, 2 unknown -> total=6, observable=4, censored=2,
        # rate = 2/6 = 0.3333...
        states = [
            RescueState.TRUE, RescueState.TRUE, RescueState.TRUE,
            RescueState.FALSE, RescueState.UNKNOWN, RescueState.UNKNOWN,
        ]
        c = count_step_population(states)
        assert c.n_step_total == 6
        assert c.n_step_observable == 4  # unknown excluido del observable
        assert c.n_step_censored == 2
        assert abs(c.step_censoring_rate - 2 / 6) < 1e-12

    def test_step_population_empty_no_zerodivision(self) -> None:
        c = count_step_population([])
        assert c.n_step_total == 0
        assert c.n_step_observable == 0
        assert c.step_censoring_rate == 0.0  # sin ZeroDivisionError

    def test_rescued_population_counts(self) -> None:
        # A mano: 2 true, 1 false, 1 unknown -> total=4, observable=3, unknown=1.
        states = [
            ProductiveState.TRUE, ProductiveState.TRUE,
            ProductiveState.FALSE, ProductiveState.UNKNOWN,
        ]
        c = count_rescued_population(states)
        assert c.n_rescued_total == 4
        assert c.n_rescued_observable == 3  # unknown fuera del observable
        assert c.n_rescued_unknown == 1
