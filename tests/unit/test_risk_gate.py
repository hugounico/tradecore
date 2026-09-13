"""Tests deterministas (de valor conocido) de RiskGate — Etapa 1.

Complementan a los tests property-based (`tests/property/test_risk_gate_props.py`):
aqui se verifica la FORMULA EXACTA de tamano de posicion y la CONVERSION A CONTRATOS
ENTEROS con valores calculados a mano, no solo propiedades de frontera.

Formula del tamano teorico (RF-E1-01 crit. 5):
    tamano_teorico = equity * risk_pct / (stop_pts * value_per_point)

Conversion a contratos enteros (DECISION CONFIRMADA por Hugo):
    contratos = floor(tamano_teorico)
    Si contratos == 0 -> None con reason == "position_below_min_contract".

Proteccion division por cero (RF-E1-01 crit. 7): stop_pts None o 0 -> None, con
`reason == "stop_unavailable"`, sin lanzar excepcion.

_(RF-E1-01; complementa Property 4)_
"""

import math

import pytest

from src.engine.risk_gate import RiskGate


class TestRiskGateFloorConversion:
    """Verifica la conversion por floor a contratos enteros para tamanos teoricos conocidos.

    Ancla de parametros (SOLO para pruebas, NO defaults productivos — Tarea 6a):
      equity = 10000, risk_pct = 0.01  ->  tope de riesgo (cap) = equity*risk_pct = 100.
      value_per_point = 20.

    Con esa ancla, el tamano teorico es:
      teorico = 100 / (stop_pts * 20) = 5 / stop_pts.
    Por tanto, para forzar un tamano teorico T, elegimos stop_pts = 5 / T.

    Para cada T se verifica:
      1. el tamano teorico calculado a mano coincide con 5/stop_pts (intencion documentada);
      2. los contratos enteros via floor(T) son los esperados;
      3. si floor(T) == 0 -> position_size() devuelve None y reason == "position_below_min_contract";
      4. el riesgo real (contratos * stop_pts * value_per_point) nunca supera el tope (<= 100).
    """

    EQUITY = 10000.0
    RISK_PCT = 0.01
    VALUE_PER_POINT = 20.0
    CAP = EQUITY * RISK_PCT  # 10000 * 0.01 = 100 (tope de riesgo en USD)

    # (tamano_teorico T, contratos enteros esperados floor(T))
    # T se materializa con stop_pts = 5/T (ver docstring).
    CASES = [
        (0.3, 0),  # 0.3 -> floor 0 -> no operable
        (0.5, 0),  # 0.5 -> floor 0 -> no operable
        (0.9, 0),  # 0.9 -> floor 0 -> no operable
        (1.0, 1),  # 1.0 -> floor 1 -> operable
        (1.2, 1),  # 1.2 -> floor 1 -> operable
        (2.0, 2),  # 2.0 -> floor 2 -> operable
        (2.8, 2),  # 2.8 -> floor 2 -> operable
    ]

    @pytest.mark.parametrize("theoretical, expected_contracts", CASES)
    def test_floor_conversion_and_risk_cap(
        self, theoretical: float, expected_contracts: int
    ) -> None:
        gate = RiskGate()

        # stop_pts que produce el tamano teorico deseado con la ancla fijada.
        stop_pts = 5.0 / theoretical

        # (1) El tamano teorico esperado coincide con la formula (intencion documentada).
        computed_theoretical = self.CAP / (stop_pts * self.VALUE_PER_POINT)
        assert computed_theoretical == pytest.approx(theoretical)

        # (2) Los contratos enteros esperados son floor del tamano teorico.
        assert math.floor(theoretical) == expected_contracts

        result = gate.position_size(
            stop_pts=stop_pts,
            equity=self.EQUITY,
            risk_pct=self.RISK_PCT,
            value_per_point=self.VALUE_PER_POINT,
        )

        if expected_contracts == 0:
            # (3) floor == 0 -> no operable: None con motivo especifico.
            assert result is None
            assert gate.reason == "position_below_min_contract"
        else:
            # Operable: entero de contratos, sin motivo de descarte.
            assert isinstance(result, int)
            assert result == expected_contracts
            assert gate.reason is None

            # (4) El riesgo real autorizado nunca supera el tope equity*risk_pct.
            real_risk = result * stop_pts * self.VALUE_PER_POINT
            assert real_risk <= self.CAP + 1e-9


class TestRiskGateReturnsInteger:
    """El tamano operable se devuelve como int (no float) — cambio de tipo de retorno."""

    def test_operable_result_is_int(self) -> None:
        gate = RiskGate()
        # 10000*0.01/(2.5*20) = 100/50 = 2.0 -> floor 2 (operable).
        result = gate.position_size(
            stop_pts=2.5, equity=10000.0, risk_pct=0.01, value_per_point=20.0
        )
        assert result == 2
        assert isinstance(result, int)
        assert gate.reason is None


class TestRiskGateBelowMinContract:
    """floor(tamano_teorico) == 0 -> None con reason position_below_min_contract."""

    def test_theoretical_below_one_returns_none_with_reason(self) -> None:
        gate = RiskGate()
        # 10000*0.01/(10*20) = 100/200 = 0.5 -> floor 0 -> no operable.
        result = gate.position_size(
            stop_pts=10.0, equity=10000.0, risk_pct=0.01, value_per_point=20.0
        )
        assert result is None
        assert gate.reason == "position_below_min_contract"


class TestRiskGateStopUnavailable:
    """stop_pts None o 0 -> None con motivo stop_unavailable, sin excepcion (crit. 7)."""

    def test_stop_pts_none_returns_none_with_reason(self) -> None:
        gate = RiskGate()
        result = gate.position_size(
            stop_pts=None, equity=10000.0, risk_pct=0.01, value_per_point=20.0
        )
        assert result is None
        assert gate.reason == "stop_unavailable"

    def test_stop_pts_zero_returns_none_with_reason(self) -> None:
        gate = RiskGate()
        result = gate.position_size(
            stop_pts=0.0, equity=10000.0, risk_pct=0.01, value_per_point=20.0
        )
        assert result is None
        assert gate.reason == "stop_unavailable"
