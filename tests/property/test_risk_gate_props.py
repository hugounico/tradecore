"""Tests property-based (basados en propiedades) de RiskGate — Etapa 1.

Property-based testing (prueba basada en propiedades): en vez de comprobar ejemplos
concretos, se afirma una PROPIEDAD que debe cumplirse para MUCHAS entradas generadas
automaticamente (aqui, con la libreria Hypothesis).

Cubre la Tarea 6.1 del design.md (Property 4 ampliada):
- Sin division por cero: stop_pts igual a 0 o None NUNCA produce excepcion; RiskGate
  devuelve None y expone el motivo `stop_unavailable`.
- Monotonicidad (solicitada por el usuario): con equity, risk_pct y value_per_point
  constantes y positivos, al aumentar stop_pts el tamano de posicion decrece o queda
  igual, nunca aumenta (relacion inversa de la formula).
- Conversion a contratos enteros: cuando devuelve un tamano operable, es un int.
- Tope de riesgo (risk cap): si devuelve un entero N >= 1, entonces
  N * stop_pts * value_per_point <= equity * risk_pct (con tolerancia de punto flotante).
  Si devuelve None (no operable), la propiedad se satisface trivialmente.

**Validates: Requirements 1.7**
_(RF-E1-01; Property 4)_
"""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from src.engine.risk_gate import RiskGate


# --- Estrategias (generadores de datos aleatorios validos) ---

# equity: capital positivo en un rango realista.
equity_st = st.floats(
    min_value=1.0, max_value=10_000_000.0, allow_nan=False, allow_infinity=False
)

# risk_pct: porcentaje de riesgo positivo (ej. 0.001 = 0.1% hasta 0.5 = 50%).
risk_pct_st = st.floats(
    min_value=0.0001, max_value=0.5, allow_nan=False, allow_infinity=False
)

# value_per_point: valor monetario por punto, positivo (ej. MNQ=2, NQ=20).
value_per_point_st = st.floats(
    min_value=0.5, max_value=1000.0, allow_nan=False, allow_infinity=False
)

# stop_pts positivo para la propiedad de monotonicidad (evita 0/None a proposito).
positive_stop_st = st.floats(
    min_value=0.01, max_value=100_000.0, allow_nan=False, allow_infinity=False
)


class TestRiskGateNoDivisionByZero:
    """Property 4: stop_pts 0 o None nunca lanza excepcion; devuelve None + reason.

    **Validates: Requirements 1.7**
    """

    @given(
        equity=equity_st,
        risk_pct=risk_pct_st,
        value_per_point=value_per_point_st,
    )
    @settings(max_examples=200)
    def test_stop_zero_returns_none_never_raises(
        self, equity: float, risk_pct: float, value_per_point: float
    ) -> None:
        """stop_pts == 0 para cualquier otro parametro -> None y reason stop_unavailable."""
        gate = RiskGate()
        # No debe lanzar ZeroDivisionError ni ninguna otra excepcion.
        result = gate.position_size(
            stop_pts=0.0, equity=equity, risk_pct=risk_pct, value_per_point=value_per_point
        )
        assert result is None
        assert gate.reason == "stop_unavailable"

    @given(
        equity=equity_st,
        risk_pct=risk_pct_st,
        value_per_point=value_per_point_st,
    )
    @settings(max_examples=200)
    def test_stop_none_returns_none_never_raises(
        self, equity: float, risk_pct: float, value_per_point: float
    ) -> None:
        """stop_pts is None para cualquier otro parametro -> None y reason."""
        gate = RiskGate()
        result = gate.position_size(
            stop_pts=None, equity=equity, risk_pct=risk_pct, value_per_point=value_per_point
        )
        assert result is None
        assert gate.reason == "stop_unavailable"


class TestRiskGateMonotonicity:
    """Monotonicidad: mayor stop_pts -> tamano de posicion menor o igual, nunca mayor.

    Manteniendo equity, risk_pct y value_per_point constantes y positivos, la formula
    tamano_teorico = equity*risk_pct/(stop_pts*value_per_point) es inversamente
    proporcional a stop_pts. Como el tamano operable es floor(teorico), floor es una
    funcion no decreciente: si el teorico no aumenta, el floor tampoco. Un stop mayor
    puede incluso hacer que la senal deje de ser operable (None). Convencion para comparar:
    None se trata como "0 contratos" (no operable), que es el menor tamano posible.

    **Validates: Requirements 1.7**
    """

    @staticmethod
    def _as_contracts(result: int | None) -> int:
        # None (no operable) equivale al menor tamano posible: 0 contratos.
        return 0 if result is None else result

    @given(
        stop_a=positive_stop_st,
        stop_b=positive_stop_st,
        equity=equity_st,
        risk_pct=risk_pct_st,
        value_per_point=value_per_point_st,
    )
    @settings(max_examples=300)
    def test_larger_stop_never_increases_position_size(
        self,
        stop_a: float,
        stop_b: float,
        equity: float,
        risk_pct: float,
        value_per_point: float,
    ) -> None:
        """El stop mayor produce un tamano (en contratos) <= al del stop menor."""
        gate = RiskGate()
        # Ordenamos: smaller <= larger. Ambos son positivos por construccion (no 0/None).
        smaller, larger = (stop_a, stop_b) if stop_a <= stop_b else (stop_b, stop_a)

        size_smaller = self._as_contracts(
            gate.position_size(
                stop_pts=smaller, equity=equity, risk_pct=risk_pct, value_per_point=value_per_point
            )
        )
        size_larger = self._as_contracts(
            gate.position_size(
                stop_pts=larger, equity=equity, risk_pct=risk_pct, value_per_point=value_per_point
            )
        )

        # Contratos enteros: el stop mayor no aumenta el tamano (relacion inversa + floor).
        assert size_larger <= size_smaller


class TestRiskGateReturnTypeAndRiskCap:
    """Conversion a int y tope de riesgo (risk cap) para entradas positivas validas.

    Para toda entrada positiva valida:
      - si position_size devuelve un valor, es un int (contratos enteros);
      - si devuelve un entero N >= 1, entonces N*stop_pts*value_per_point <= equity*risk_pct;
      - si devuelve None (no operable), la propiedad del tope se satisface trivialmente.

    **Validates: Requirements 1.7**
    """

    @given(
        stop_pts=positive_stop_st,
        equity=equity_st,
        risk_pct=risk_pct_st,
        value_per_point=value_per_point_st,
    )
    @settings(max_examples=400)
    def test_integer_result_and_risk_never_exceeds_cap(
        self,
        stop_pts: float,
        equity: float,
        risk_pct: float,
        value_per_point: float,
    ) -> None:
        gate = RiskGate()
        result = gate.position_size(
            stop_pts=stop_pts, equity=equity, risk_pct=risk_pct, value_per_point=value_per_point
        )

        cap = equity * risk_pct  # tope de riesgo permitido

        if result is None:
            # No operable (floor teorico == 0). La propiedad se satisface trivialmente,
            # pero verificamos que el motivo sea el esperado.
            assert gate.reason == "position_below_min_contract"
            return

        # Operable: debe ser un entero >= 1.
        assert isinstance(result, int)
        assert result >= 1
        assert gate.reason is None

        # El teorico debe coincidir con floor(result) por construccion.
        theoretical = cap / (stop_pts * value_per_point)
        assert result == math.floor(theoretical)

        # Tope de riesgo: el riesgo real autorizado nunca supera equity*risk_pct.
        # Tolerancia relativa de punto flotante para el redondeo de la division/multiplicacion.
        real_risk = result * stop_pts * value_per_point
        assert real_risk <= cap + 1e-6 * max(1.0, abs(cap))
