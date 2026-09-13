# Calibration Run 1 — Resultado (evidencia permanente)

Evidencia legible y versionada de la primera Calibration real de la Etapa 1.
Fuentes verificadas: `_calibration_run_result.log`, `_calibration_partial_results.log`,
`_harness_equivalence_audit.log` (locales, fuera de Git). Este documento NO reproduce
logs brutos ni el JSON completo; resume la evidencia ya verificada. No contiene
interpretacion de trading, P&L, expectancy, profit factor, take-profit, slippage ni
comisiones.

## 1. Identity

- Nombre: Calibration Run 1
- Fecha UTC de ejecucion: 2026-09-13T05:41:05Z
- git base: `966ae1487290827612f9df9a6f5de7fc34500835`
- dataset_id: `5be681c5f1ac4449d6dd347c7494157d2a0573f4f4d62fea5a5aee2a41eb9056`
- SHA-256 dataset: `5be681c5f1ac4449d6dd347c7494157d2a0573f4f4d62fea5a5aee2a41eb9056` (SHA_MATCH = YES)
- Rango Calibration: `[2023-09-13T00:00:00Z, 2025-07-01T00:00:00Z)` (inicio inclusivo, fin exclusivo)
- Python: 3.12.13
- Databento SDK: 0.86.0
- bootstrap seed: 0
- BOOTSTRAP_RESAMPLES: 2000

## 2. Data protection

- Calibration rows utilizadas: 628,051 (de 1,050,863 filas totales del Parquet)
- min analytical timestamp: 2023-09-13T00:00:00Z
- max analytical timestamp: 2025-06-30T23:59:00Z
- warmup: 22 (derivado por formula congelada, no hardcodeado)
- VALIDATION_ROWS_USED: 0
- FINAL_OOS_EXECUTED: NO

## 3. Signals

- BUY: 16,919
- SELL: 16,918
- Total: 33,837
- Primera senal: 2023-09-13T00:36:00Z
- Ultima senal: 2025-06-30T23:39:00Z
- Bloques ISO globales no vacios: 95
- Sanity gate (>= 200 senales): PASS

## 4. Grid

- A (baseline/control): 1
- B (ATR): 7
- C (ATR x Volume): 28
- Total: 36

## 5. H1 (seleccion secuencial de ATR, poblacion general B)

Pasos decisivos realmente evaluados (early-stop tras el primer paso que no justifica ampliar):

### 1.50 -> 1.75
- N_step_observable: 33,836
- N_rescued_observable: 1,882
- rescue_quality: 0.6216790648246546  IC95 [0.6013626043866214, 0.6420409040173608]
- productive_rescue_population_rate: 0.03457855538479726  IC95 [0.032736779038971396, 0.03641145981886824]
- evidence_sufficient: true
- Resultado del gate (justifies_widening): PASS (LI IC95 de rescue_quality > 0.50 y LI IC95 de PRPR > 0)

### 1.75 -> 2.00
- N_step_observable: 33,836
- N_rescued_observable: 1,621
- rescue_quality: 0.5095619987661937  IC95 [0.48422661243933857, 0.5338289300652244]
- productive_rescue_population_rate: 0.02441186901525003  IC95 [0.022697565738461375, 0.02608806263198193]
- evidence_sufficient: true
- Resultado del gate (justifies_widening): FAIL
- Motivo concreto del stop secuencial: el limite inferior del IC95 de rescue_quality (0.4842) NO es > 0.50, por lo que el paso no justifica ampliar; la regla secuencial se detiene y conserva el ultimo multiplicador inferior justificado.

Conclusion: `selected_atr_B = 1.75`

## 6. H2 (por umbral de volumen)

| threshold | Accepted | Rejected | Cliff MFE [IC95] | Cliff MAE [IC95] | joint_effect | joint_ci_width | censoring |
|-----------|----------|----------|------------------|------------------|--------------|----------------|-----------|
| 0.50 | 27,987 | 5,850 | 0.08324911535545045 [0.06588120818349642, 0.10014188817229958] | 0.10256200146649284 [0.08678050437728863, 0.1194146026671728] | -0.10256200146649284 | 0.03426067998880315 | 3.5730875049129956e-05 |
| 0.60 | 25,084 | 8,753 | 0.07752733860043119 [0.06318747719219171, 0.09095621194976142] | 0.10539112972051061 [0.09219501426160609, 0.11877416471293692] | -0.10539112972051061 | 0.027768734757569705 | 3.986605007175889e-05 |
| 0.70 | 21,855 | 11,982 | 0.07356273485955311 [0.06179867604720315, 0.08529153716471097] | 0.10835522329479119 [0.09554409164802186, 0.12108575978458003] | -0.10835522329479119 | 0.02554166813655817 | 4.575611988103409e-05 |
| 0.80 | 18,756 | 15,081 | 0.07736952241502908 [0.0658316853513758, 0.08866658985943317] | 0.10743439538951452 [0.09502466174408405, 0.11983406750285425] | -0.10743439538951452 | 0.024809405758770192 | 5.331627212625293e-05 |

(censoring = h2_joint_censoring_rate; censoring_rejected = 0.0 en los cuatro umbrales; bootstrap_evidence_sufficient = true, 95/95 bloques ISO.)

## 7. H3-A (gate de tamano de efecto)

Gate congelado: PASS requiere Cliff_delta_MFE >= +0.147 con IC95 que no cruza 0, Y Cliff_delta_MAE <= -0.147 con IC95 que no cruza 0.

| threshold | Cliff MFE | Cliff MAE | Resultado | Motivo (protocolo) |
|-----------|-----------|-----------|-----------|--------------------|
| 0.50 | 0.08324911535545045 | 0.10256200146649284 | FAIL | efecto por debajo del gate +/-0.147 (cliff_delta_gate_or_ci_crosses_zero) |
| 0.60 | 0.07752733860043119 | 0.10539112972051061 | FAIL | efecto por debajo del gate +/-0.147 |
| 0.70 | 0.07356273485955311 | 0.10835522329479119 | FAIL | efecto por debajo del gate +/-0.147 |
| 0.80 | 0.07736952241502908 | 0.10743439538951452 | FAIL | efecto por debajo del gate +/-0.147 |

Los cuatro umbrales fallaron H3-A. (H3-A reutilizo exactamente los Cliff delta + IC95 calculados en H2; sin bootstrap adicional.)

## 8. H3-B

`NOT_EXECUTED — no H3-A threshold passed`

## 9. Ranking / graduates

- A = control
- B = 1.75
- C = []
- C candidates = 0

## 10. Harness verification

`HARNESS_EQUIVALENCE_AUDIT = CLEAN`

Gates (auditoria de equivalencia posterior, camino canonico vs acelerado):

- ATR = PASS
- VolumeFilter = PASS
- Signal Path = PASS
- Stop Path = PASS
- Productive Rescue = PASS
- Memoization = PASS
- H2 -> H3-A reuse = PASS
- Checkpoint/result integrity = PASS (16 checkpoints, todos status=complete)

Aclaraciones:
- No se recalculo bootstrap durante la auditoria.
- El harness acelerado (ATR precalculado, ventanas acotadas, memoizacion) fue verificado
  contra el camino canonico construido con la semantica original de los componentes
  congelados; equivalencia exacta (categoricos identicos; numericos con error <= 1e-12,
  en la practica 0.0).
- Los resultados de Calibration quedaron ratificados sin modificar ninguna observacion
  que alimento los 2000 bootstrap.

## 11. Estado final

`CALIBRATION_RESULTS = VERIFIED`

`CALIBRATION_EXECUTION = COMPLETE`

`VALIDATION_EXECUTED = NO`

`FINAL_OOS_EXECUTED = NO`
