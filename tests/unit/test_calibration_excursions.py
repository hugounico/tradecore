"""Tests deterministas de excursiones MFE/MAE, SIGNAL PATH y normalizacion ATR (LAYER A).

design.md 4.2-4.6 (SIGNAL PATH + MFE/MAE con max(0, ...)), 9-bis.10 (Cliff delta, H2),
9-bis.8 (breach_s(m)).

TODOS los fixtures son sinteticos y los valores esperados estan calculados A MANO
(nunca producidos por la funcion de produccion). El objetivo es probar que:
- la construccion del SIGNAL PATH es explicita y auditable (excluye t, incluye el cruce),
- las funciones MFE/MAE consumen la ventana del SIGNAL PATH (no re-recortan) y aplican max(0,...),
- la normalizacion por ATR es no-evaluable (None) cuando ATR es None o <= 0,
- H2 (cliff_delta) puede consumir salidas de Layer A,
- breach_s(m) es coherente con MAE_ATR (indicador simple).
"""

from datetime import datetime, timezone

from src.calibration.metrics import cliff_delta
from src.calibration.observations import (
    SignalPath,
    build_signal_path,
    mae_points,
    mfe_points,
    normalize_by_atr,
)
from src.schemas.candle import Candle
from src.schemas.signal import SignalType

_TS = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _bar(high: float, low: float) -> Candle:
    """Vela minima para tests de excursion (open/close/volume irrelevantes aqui)."""
    return Candle(timestamp=_TS, open=(high + low) / 2, high=high, low=low,
                  close=(high + low) / 2, volume=1)


def _path(high_window: list[float], low_window: list[float], censored: bool = False) -> SignalPath:
    """Construye un SignalPath directo desde ventanas ya calculadas a mano."""
    return SignalPath(
        high_window=tuple(high_window),
        low_window=tuple(low_window),
        signal_path_censored=censored,
    )


class TestMfeMaePoints:
    """MFE/MAE en puntos con max(0, ...) (design.md 4.5). Valores esperados a mano."""

    def test_buy_favorable_and_adverse(self) -> None:
        # BUY: ref=100, high=[102,105,103], low=[99,97,98].
        # MFE = max(0, max(high)-ref) = max(0, 105-100) = 5.
        # MAE = max(0, ref-min(low)) = max(0, 100-97) = 3.
        p = _path(high_window=[102, 105, 103], low_window=[99, 97, 98])
        assert mfe_points(SignalType.BUY, 100.0, p) == 5.0
        assert mae_points(SignalType.BUY, 100.0, p) == 3.0

    def test_buy_no_favorable(self) -> None:
        # BUY sin favorable: high=[99,98] nunca supera ref=100 -> raw negativo -> MFE=0.
        p = _path(high_window=[99, 98], low_window=[97, 96])
        assert mfe_points(SignalType.BUY, 100.0, p) == 0.0  # sin error, sin anomalia

    def test_buy_no_adverse(self) -> None:
        # BUY sin adverso: low=[101,102] nunca baja de ref=100 -> raw negativo -> MAE=0.
        p = _path(high_window=[103, 104], low_window=[101, 102])
        assert mae_points(SignalType.BUY, 100.0, p) == 0.0

    def test_sell_favorable_and_adverse(self) -> None:
        # SELL: ref=100, high=[101,104], low=[98,95].
        # MFE (favorable de SELL = bajada) = max(0, ref-min(low)) = max(0, 100-95) = 5.
        # MAE (adverso de SELL = subida)   = max(0, max(high)-ref) = max(0, 104-100) = 4.
        p = _path(high_window=[101, 104], low_window=[98, 95])
        assert mfe_points(SignalType.SELL, 100.0, p) == 5.0
        assert mae_points(SignalType.SELL, 100.0, p) == 4.0

    def test_sell_no_favorable(self) -> None:
        # SELL sin favorable: todos los low por encima de ref=100 -> no bajo -> MFE=0.
        p = _path(high_window=[105, 106], low_window=[101, 102])
        assert mfe_points(SignalType.SELL, 100.0, p) == 0.0

    def test_sell_no_adverse(self) -> None:
        # SELL sin adverso: todos los high por debajo de ref=100 -> no subio -> MAE=0.
        p = _path(high_window=[98, 99], low_window=[95, 96])
        assert mae_points(SignalType.SELL, 100.0, p) == 0.0


class TestAtrNormalization:
    """Normalizacion por ATR (design.md 4.6). No-evaluable cuando ATR None o <= 0."""

    def test_normalization_positive_atr(self) -> None:
        # MFE_points=6, MAE_points=3, ATR=2 -> MFE_ATR=3, MAE_ATR=1.5.
        assert normalize_by_atr(6.0, 2.0) == 3.0
        assert normalize_by_atr(3.0, 2.0) == 1.5

    def test_atr_zero_non_evaluable(self) -> None:
        # ATR=0 -> no evaluable (None), sin ZeroDivisionError.
        assert normalize_by_atr(6.0, 0.0) is None

    def test_atr_none_non_evaluable(self) -> None:
        # ATR None -> no evaluable (None).
        assert normalize_by_atr(6.0, None) is None

    def test_atr_negative_non_evaluable(self) -> None:
        # ATR negativo (no fisico) -> no evaluable (None).
        assert normalize_by_atr(6.0, -1.0) is None


class TestSignalPathTemporalEndToEnd:
    """End-to-end: construir el SIGNAL PATH y LUEGO medir MFE/MAE (no listas pre-cortadas)."""

    def test_9_1_signal_candle_t_excluded(self) -> None:
        # 9.1 La vela t (extremos dominantes) NO debe aparecer en las excursiones.
        # ref=100. Vela t con high=999/low=1 (dominaria si participara), PERO NO se pasa a
        # post_signal_bars (que arranca en t+1). El SIGNAL PATH nunca ve la vela t.
        signal_candle_t = _bar(999, 1)  # extremos que dominarian MFE/MAE si participaran
        post = [_bar(102, 99), _bar(105, 97), _bar(103, 98)]  # t+1..; cruce inverso en idx 2
        sp = build_signal_path(signal_candle_t, post,
                               inverse_crossover_index_or_None=2,
                               calibration_boundary_reached_flag=False)
        # Los extremos de la vela t NO estan en las ventanas.
        assert 999 not in sp.high_window
        assert 1 not in sp.low_window
        # MFE/MAE calculados sobre la ventana t+1..cruce -> 5 y 3 (no 899/99).
        assert mfe_points(SignalType.BUY, 100.0, sp) == 5.0
        assert mae_points(SignalType.BUY, 100.0, sp) == 3.0

    def test_9_2_inverse_crossover_candle_included(self) -> None:
        # 9.2 La vela del cruce inverso SI se incluye: el MFE maximo ocurre exactamente ahi.
        # ref=100. post: t+1 (high 101), t+2 = cruce inverso (high 108 -> MFE=8).
        signal_candle_t = _bar(100, 100)
        post = [_bar(101, 99), _bar(108, 98)]  # idx 1 = cruce inverso, con el high maximo
        sp = build_signal_path(signal_candle_t, post,
                               inverse_crossover_index_or_None=1,
                               calibration_boundary_reached_flag=False)
        assert 108 in sp.high_window  # la vela del cruce SI aparece
        assert mfe_points(SignalType.BUY, 100.0, sp) == 8.0  # el maximo esta en el cruce
        assert sp.signal_path_censored is False

    def test_9_3_post_crossover_bar_excluded(self) -> None:
        # 9.3 Una vela inmediatamente DESPUES del cruce, con extremo aun mayor, NO afecta.
        # ref=100. post tiene 3 velas pero el cruce inverso es idx 1; la idx 2 (high 200)
        # queda FUERA de la ventana observable.
        signal_candle_t = _bar(100, 100)
        post = [_bar(103, 99), _bar(106, 98), _bar(200, 50)]  # cruce en idx 1; idx 2 excluida
        sp = build_signal_path(signal_candle_t, post,
                               inverse_crossover_index_or_None=1,
                               calibration_boundary_reached_flag=False)
        assert 200 not in sp.high_window  # la vela post-cruce no entra
        assert 50 not in sp.low_window
        # MFE maximo dentro de la ventana = 106-100 = 6 (no 100 de la vela post-cruce).
        assert mfe_points(SignalType.BUY, 100.0, sp) == 6.0
        assert mae_points(SignalType.BUY, 100.0, sp) == 2.0  # 100-98

    def test_9_4_boundary_censored_post_boundary_excluded(self) -> None:
        # 9.4 Sin cruce inverso antes del limite -> signal_path_censored=True. Una vela
        # sintetica DESPUES del limite (extremo enorme) NO debe afectar MFE/MAE.
        signal_candle_t = _bar(100, 100)
        # post SOLO contiene lo que hay hasta el limite (la vela post-limite NO se pasa).
        post = [_bar(104, 99), _bar(103, 97)]  # llega hasta el limite del split
        sp = build_signal_path(signal_candle_t, post,
                               inverse_crossover_index_or_None=None,
                               calibration_boundary_reached_flag=True)
        assert sp.signal_path_censored is True  # censurado: no hubo cruce inverso
        # Vela post-limite sintetica (jamas provista al builder): no puede influir.
        assert 500 not in sp.high_window
        # MFE/MAE sobre la ventana observable: 104-100=4 y 100-97=3.
        assert mfe_points(SignalType.BUY, 100.0, sp) == 4.0
        assert mae_points(SignalType.BUY, 100.0, sp) == 3.0


class TestH2IntegrationFromLayerA:
    """H2 (cliff_delta) consume salidas de Layer A: SIGNAL PATH -> MFE_ATR/MAE_ATR -> cliff_delta.

    NO se tocan cliff_delta / +-0.147 / IC95 / bootstrap / ranking / TopN: solo se prueba
    que la cadena puede alimentar H2 con valores PRODUCIDOS por las funciones de Layer A.
    """

    def _mae_atr_for(self, ref: float, atr: float,
                     highs_lows: list[tuple[float, float]]) -> float | None:
        """Cadena Layer A completa para un BUY: build_signal_path -> mae_points -> normalize."""
        signal_candle_t = _bar(ref, ref)
        post = [_bar(h, low) for (h, low) in highs_lows]
        # Cruce inverso en la ultima vela provista (ventana cerrada, no censurada).
        sp = build_signal_path(signal_candle_t, post,
                               inverse_crossover_index_or_None=len(post) - 1,
                               calibration_boundary_reached_flag=False)
        mae = mae_points(SignalType.BUY, ref, sp)
        return normalize_by_atr(mae, atr)

    def test_cliff_delta_consumes_layer_a_mae_atr(self) -> None:
        # Poblacion "Rechazada": BUYs con mucho movimiento adverso (MAE_ATR alto).
        # Poblacion "Aceptada": BUYs con poco movimiento adverso (MAE_ATR bajo).
        # ref=100, ATR=2 en todas para simplificar la cuenta a mano.
        rejected = [
            self._mae_atr_for(100.0, 2.0, [(101, 92), (102, 90)]),  # min low 90 -> MAE=10 -> /2=5
            self._mae_atr_for(100.0, 2.0, [(101, 94), (100, 92)]),  # min low 92 -> MAE=8 -> /2=4
        ]
        accepted = [
            self._mae_atr_for(100.0, 2.0, [(103, 99), (104, 98)]),  # min low 98 -> MAE=2 -> /2=1
            self._mae_atr_for(100.0, 2.0, [(105, 100), (106, 99)]),  # min low 99 -> MAE=1 -> /2=0.5
        ]
        # Valores producidos por Layer A (no pre-horneados):
        assert rejected == [5.0, 4.0]
        assert accepted == [1.0, 0.5]
        # cliff_delta(Aceptado, Rechazado) sobre MAE_ATR: todas las aceptadas < rechazadas.
        # #{a>b}=0, #{a<b}=4, n_a*n_b=4 -> (0-4)/4 = -1.0.
        delta = cliff_delta(accepted, rejected)
        assert delta == -1.0  # efecto perfecto: Aceptadas con MAE mucho menor


class TestBreachCoherence:
    """BREACH_COHERENCE_TEST = APPLICABLE (design.md 9-bis.8: breach_s(m)=1[MAE_ATR(s) >= m]).

    breach se implementa como indicador simple INLINE en el test; NO se agrega ninguna
    formula H1 nueva a produccion.
    """

    @staticmethod
    def _breach(mae_atr: float, m: float) -> bool:
        # Indicador simple: 1 si MAE_ATR alcanza o supera el multiplicador m, si no 0.
        return mae_atr >= m

    def test_mae_atr_zero_never_breaches_positive_m(self) -> None:
        # Una senal con MAE_ATR=0 y cualquier m>0 no puede romper el stop -> breach=False.
        for m in (0.5, 1.0, 1.5, 2.0, 3.0):
            assert self._breach(0.0, m) is False

    def test_breach_true_when_mae_reaches_m(self) -> None:
        # Coherencia positiva: MAE_ATR=2.0 rompe stops con m<=2.0 y no los de m>2.0.
        assert self._breach(2.0, 1.5) is True
        assert self._breach(2.0, 2.0) is True
        assert self._breach(2.0, 2.5) is False
