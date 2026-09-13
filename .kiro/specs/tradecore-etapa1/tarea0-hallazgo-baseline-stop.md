# Tarea 0 — Hallazgo: mecanismo de stop del baseline Pine Script

**Tarea:** 0. Prerrequisito bloqueante — Verificar el mecanismo de stop del baseline Pine Script.
**Requisitos:** RF-E1-01. **Diseño:** [SEGURO] punto 10 (método de suavizado del ATR).
**Tipo de tarea:** investigación / verificación / documentación (NO código de producción).
**Estado del hallazgo:** baseline no confirmable con la evidencia disponible → `[SEGURO]` pendiente de Hugo.

---

## (a) Qué se buscó y qué se encontró

Búsqueda exhaustiva en todo el workspace (raíz del proyecto, `.kiro/`, `docs/`, `scripts/`,
`src/`, steering e historial), excluyendo `.venv`, `.git` y `.hypothesis`:

| Búsqueda | Resultado |
|---|---|
| Nombre del baseline: `NQ Hybrid v11`, `Sustainable Edge`, `Hybrid` | Solo aparece DENTRO del propio spec `tradecore-etapa1` (requirements/design/tasks). Ninguna fuente externa. |
| Archivos Pine Script (`*.pine`) | **Ninguno** en el workspace. |
| Sintaxis Pine (`@version=`, `indicator(`, `study(`, `strategy.entry`, `ta.atr`) | **Cero coincidencias.** No hay código Pine embebido en ningún archivo. |
| Documentos de auditoría (`*.txt`, `*.md`, `*.pdf`, `*.docx`) | No existe ningún archivo de auditoría. `docs/` está vacío. |
| Términos de auditoría (`auditoría`, `stop fijo`, `stop-loss fijo`, `volatilidad`) | Solo aparecen como texto DENTRO del propio spec etapa1, no como artefacto fuente. |
| `historial-tradecore.md` (fase de diseño previa) | Solo describe alcance general (GARCH/ARIMA, XGBoost, Greeks/GEX, comparación de proveedores). **No contiene el Pine Script ni sus parámetros de stop.** |

**Origen de la frase "stop-loss fijo no ajustado por volatilidad":** esa frase aparece
únicamente en la descripción de la Tarea 0 (`tasks.md`) como paráfrasis de "hallazgos de
auditoría previos". **No existe en el workspace un documento de auditoría que la respalde.**
Es una afirmación referida, sin artefacto fuente verificable.

Coherente con lo ya declarado en el propio spec:
- `requirements.md` RF-E1-04 crit. 6 (`[SEGURO]`): "el comportamiento, los parámetros exactos
  y la metodología del baseline Pine Script NO están documentados en los artefactos existentes
  del proyecto".
- `design.md` lista consolidada `[SEGURO]` punto 7: baseline pendiente.

## (b) Conclusión sobre el mecanismo de stop del baseline

**Estado: DESCONOCIDO — `[SEGURO]` pendiente de confirmación de Hugo.**

No se puede afirmar si el baseline "NQ Hybrid v11 - Sustainable Edge" usa un stop fijo en
puntos o un stop basado en ATR, porque **su código fuente no está en el workspace.** Conforme
a la restricción de gobernanza y a la convención `[SEGURO]`, NO se inventa ni se asume su
comportamiento. La afirmación referida ("stop fijo") es plausible pero **no verificable** con
la evidencia actual: no hay Pine Script ni documento de auditoría que la confirme.

Para confirmar este punto, Hugo debe aportar UNO de los siguientes:
1. El código fuente Pine Script del indicador/estrategia (idealmente con `@version` y la
   sección de cálculo del stop), o
2. Una captura/exportación de la configuración de la estrategia en TradingView que muestre
   cómo define el stop (fijo en puntos/ticks vs. `ta.atr(...)` con multiplicador), o
3. Un documento de auditoría formal que describa el mecanismo de stop con su fuente.

## (c) Método de suavizado del ATR de TradeCore — DEFINIDO DE FORMA INDEPENDIENTE

La Tarea 0 instruye: si el baseline usa stop fijo (o, como aquí, no es confirmable), TradeCore
define su método ATR **de forma independiente, sin necesidad de igualarlo al baseline.** Por
tanto se resuelve el `[SEGURO]` punto 10 del diseño (SMA vs RMA/Wilder) de forma autónoma:

**Decisión: RMA / SMMA de Welles Wilder (suavizado exponencial de Wilder).**

Alternativas y elección (regla 9 de `dev-rules.md`):

| Método | Qué es | Decisión |
|---|---|---|
| SMA del True Range | Media aritmética simple de los últimos N valores de TR. | Descartado como método principal. |
| **RMA / SMMA (Wilder)** | Suavizado exponencial: `ATR_t = (ATR_{t-1}*(N-1) + TR_t) / N`. | **ELEGIDO.** |

**Justificación:**
1. **Estándar de industria y del propio diseño.** El ATR fue definido por Welles Wilder usando
   su suavizado (RMA), no SMA. El `design.md` de este spec ya describe el cálculo como
   "estándar Welles Wilder", así que RMA es coherente con el diseño aprobado.
2. **Coincide con TradingView por defecto.** La función `ta.atr()` de Pine Script usa RMA
   internamente. Elegir RMA maximiza la comparabilidad futura con CUALQUIER baseline de
   TradingView que sí use ATR, reduciendo el riesgo de sesgo de método en la Validación B
   (RF-E1-04 crit. 4), sin por ello asumir el comportamiento del baseline actual.
3. **Estabilidad.** RMA reacciona de forma más suave a picos aislados de TR que la SMA, lo que
   da un `Stop_Puntos` menos ruidoso vela a vela — deseable para un stop de volatilidad.

**Coherencia con Requirements/Design:** RF-E1-01 crit. 1-2 exigen `atr_period` y
`atr_multiplier` configurables; esta decisión NO fija el multiplicador (sigue en la
Calibración, Tarea 1) ni cambia el alcance. `atr_period` default = 14 (ya fijado en diseño).
Esta decisión resuelve exclusivamente el método de suavizado, que el diseño dejó abierto.

**Implicación para la Tarea 2 (ATRCalculator):** implementar el ATR con recurrencia de Wilder:
`ATR_t = (ATR_{t-1} * (period - 1) + TR_t) / period`, con la primera semilla = media simple de
los primeros `period` valores de TR (semilla clásica de Wilder). Datos insuficientes
(< `atr_period + 1` velas) → `None`, motivo `insufficient_history_atr`.

## (d) Estado de bloqueo de la Tarea 1 (Calibración)

- **Sub-salida "método ATR de TradeCore definido": DESBLOQUEADA.** El método de suavizado
  (RMA/Wilder) queda definido de forma independiente; el `[SEGURO]` punto 10 del diseño, en su
  aspecto de "qué método usa TradeCore para calibrar", queda **resuelto**. La calibración
  interna de tres etapas (TradeCore actual / +ATR / +ATR+volumen) puede usar RMA/Wilder sin
  esperar al baseline.
- **Sub-salida "mecanismo de stop del baseline documentado": permanece `[SEGURO]`.** No afecta
  la calibración interna (Tarea 1), que compara TradeCore contra sí mismo, no contra el
  baseline (ver "Nota terminológica" de la Tarea 1: "baseline" se reserva para el Pine Script,
  la calibración usa "TradeCore actual").
- **La Validación B (Tarea 9) SIGUE BLOQUEADA** hasta que Hugo confirme el baseline (ya marcada
  así en `tasks.md` y coherente con `[SEGURO]` punto 7 del diseño).

**Veredicto:** La Tarea 1 (Calibración) queda **desbloqueada en cuanto al prerrequisito de la
Tarea 0**, porque el método de suavizado del ATR ya está definido (RMA/Wilder) y la calibración
no depende del baseline. Sus otras dependencias (ATRCalculator Tarea 2, VolumeFilter Tarea 3,
SignalRecord/Journal Tarea 4 y sus tests) siguen vigentes según el grafo de dependencias.

---

## Nota de gobernanza

- No se modificó ningún componente protegido (Nivel 1 de `fase-actual-etapa1.md`).
- No se escribió código de producción (tarea de documentación).
- No se inventó el comportamiento del baseline; se marcó como `[SEGURO]` pendiente de Hugo.
- Decisión pendiente de aprobación de Hugo si desea otro método de suavizado (SMA) en lugar
  del RMA/Wilder aquí elegido; en ausencia de objeción, se procede con RMA/Wilder.
