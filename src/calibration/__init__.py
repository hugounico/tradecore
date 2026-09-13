"""Paquete de Calibracion diagnostica de la Etapa 1 (marco MFE/MAE congelado).

Este paquete implementa EXACTAMENTE el protocolo FROZEN documentado en la seccion
9-bis del `design.md` del spec `tradecore-etapa1` (subsecciones 9-bis.1 a 9-bis.22).
NO redisena la metodologia, NO inventa metricas ni cambia umbrales.

Se organiza en TRES capas separadas (no mezcladas en funciones monoliticas):

- LAYER A (`observations.py`): construye observaciones por senal (STOP PATH / SIGNAL
  PATH), la semantica ternaria de `stop_would_trigger`, el rescate marginal
  (`rescued_signal`) y el rescate productivo (`productive_rescue`), mas contadores de
  poblacion/censura.
- LAYER B (`block_bootstrap.py` + `metrics.py`): intervalos de confianza por block
  bootstrap semanal ISO y las metricas diagnosticas (H1: rescue_quality,
  productive_rescue_population_rate; H2: cliff_delta y su gate; secundarias).
- LAYER C (`decisions.py`): reglas de decision — seleccion secuencial de ATR (H1),
  ranking lexicografico de candidatas C, Top-N y recomputo de H1 por poblacion (H3-B).

`grid.py` contiene las constantes congeladas de la grilla y los umbrales.
`split_guard.py` protege los limites temporales de los splits (anti-fuga de datos).

IMPORTANTE: este paquete NO abre el dataset real, NO ejecuta la Calibracion real y NO
consulta Validacion ni OOS final. Todo se prueba con fixtures sinteticos.
"""
