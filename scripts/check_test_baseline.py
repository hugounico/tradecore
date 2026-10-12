#!/usr/bin/env python3
"""check_test_baseline.py — herramienta de linea base de tests (solo biblioteca estandar).

Proposito (red de seguridad A2): detectar regresiones comparando el resultado actual de la
suite contra una "linea base" (baseline) congelada de la version V1 en produccion. NO cambia
el comportamiento de la aplicacion; solo lee reportes JUnit y decide PASS/FAIL.

Glosario breve:
- JUnit XML: formato estandar que pytest escribe con --junitxml; lista cada test y su resultado.
- identidad de un test: cadena "classname::name" leida del JUnit. Se usa la MISMA al generar y
  al verificar, para que un test se reconozca igual en ambos momentos.
- baseline (linea base): foto del resultado de cada test en V1 (passed/failed/error/skipped).
- FLAKY: un test que dio resultados distintos entre las dos corridas de generacion; se tolera.

Dos modos:
  --write  <junit1> <junit2> --out <baseline.json>   genera la linea base (con 2 corridas).
  --verify <junit> --baseline <baseline.json>         verifica una corrida contra la linea base.

El modo verify NUNCA modifica la linea base. Codigo de salida 0 = OK, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import xml.etree.ElementTree as ET


# Resultados posibles por test (normalizados).
PASSED = "passed"
FAILED = "failed"
ERROR = "error"
SKIPPED = "skipped"


def _result_of_testcase(tc: ET.Element) -> str:
    """Deriva el resultado de un <testcase> segun sus hijos (prioridad error > failure > skipped)."""
    # Un testcase puede tener <error>, <failure> o <skipped>; si no tiene ninguno, paso.
    for child in tc:
        tag = child.tag.lower()
        if tag.endswith("error"):
            return ERROR
        if tag.endswith("failure"):
            return FAILED
        if tag.endswith("skipped"):
            return SKIPPED
    return PASSED


def parse_junit(path: str) -> dict[str, str]:
    """Lee un archivo JUnit XML y devuelve {identidad: resultado}.

    identidad = "classname::name". Lanza excepcion si el archivo no existe, es ilegible
    o no contiene ningun <testcase> (el llamador lo trata como FAIL).
    """
    tree = ET.parse(path)  # ParseError si es ilegible; FileNotFoundError si no existe
    root = tree.getroot()
    # El root puede ser <testsuites> o directamente <testsuite>.
    testcases = root.iter("testcase")
    results: dict[str, str] = {}
    count = 0
    for tc in testcases:
        count += 1
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        identity = f"{classname}::{name}"
        res = _result_of_testcase(tc)
        # Si por alguna razon aparece la misma identidad dos veces, el peor resultado gana
        # (error > failed > skipped > passed) para no ocultar un fallo.
        order = {PASSED: 0, SKIPPED: 1, FAILED: 2, ERROR: 3}
        if identity in results and order[res] <= order[results[identity]]:
            continue
        results[identity] = res
    if count == 0:
        raise ValueError(f"JUnit sin tests: {path}")
    return results


def _is_fail_result(res: str) -> bool:
    """True si el resultado cuenta como 'fallo' (failed o error)."""
    return res in (FAILED, ERROR)


def write_baseline(junit1: str, junit2: str, out_path: str, commit: str) -> int:
    """Genera la linea base combinando run1 y run2. Marca FLAKY los tests que difieren."""
    r1 = parse_junit(junit1)
    r2 = parse_junit(junit2)

    all_ids = sorted(set(r1) | set(r2))
    tests: dict[str, dict] = {}
    flaky = []
    for identity in all_ids:
        a = r1.get(identity)
        b = r2.get(identity)
        # Si un test aparece en una corrida y no en la otra, o difiere su resultado -> FLAKY.
        is_flaky = (a != b)
        # Resultado "representativo": si es flaky lo marcamos; igual guardamos ambos.
        result = a if a is not None else b
        tests[identity] = {
            "run1": a,
            "run2": b,
            "result": result,
            "flaky": is_flaky,
        }
        if is_flaky:
            flaky.append(identity)

    baseline = {
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "commit": commit,
        "identity_format": "classname::name",
        "total_tests": len(all_ids),
        "flaky_count": len(flaky),
        "tests": tests,
    }
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    print(f"BASELINE_WRITTEN total={len(all_ids)} flaky={len(flaky)} out={out_path}")
    return 0


def verify(junit: str, baseline_path: str) -> int:
    """Verifica una corrida contra la linea base. NO modifica la linea base.

    Clasificacion por test:
      KNOWN_FAIL : fallaba en baseline y falla ahora -> permitido.
      NEW_FAIL   : pasaba (o es nuevo) y falla ahora -> FAIL.
      FIXED      : fallaba en baseline y pasa ahora -> permitido (+ BASELINE_CLEANUP_REQUIRED).
      MISSING    : estaba en baseline y no aparece ahora -> FAIL.
      FLAKY      : marcado flaky en baseline -> permitido (cualquier resultado).
      NEW_TEST   : no estaba en baseline y pasa -> permitido.
    JUnit inexistente/ilegible/0 tests -> FAIL.
    """
    try:
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)
    except Exception as exc:  # noqa: BLE001 - cualquier problema con la baseline es fatal
        print(f"VERIFY_FAIL no se pudo leer la baseline: {exc}")
        return 1

    try:
        current = parse_junit(junit)
    except Exception as exc:  # noqa: BLE001 - junit ausente/ilegible/vacio = FAIL
        print(f"VERIFY_FAIL JUnit invalido ({junit}): {exc}")
        return 1

    base_tests = baseline.get("tests", {})

    categories: dict[str, list[str]] = {
        "KNOWN_FAIL": [],
        "NEW_FAIL": [],
        "FIXED": [],
        "MISSING": [],
        "FLAKY": [],
        "NEW_TEST": [],
    }

    # Recorremos la union de identidades (baseline + actual).
    all_ids = set(base_tests) | set(current)
    for identity in sorted(all_ids):
        in_base = identity in base_tests
        in_cur = identity in current
        base_flaky = bool(base_tests.get(identity, {}).get("flaky")) if in_base else False
        base_result = base_tests.get(identity, {}).get("result") if in_base else None
        base_failed = _is_fail_result(base_result) if base_result is not None else False
        cur_result = current.get(identity)
        cur_failed = _is_fail_result(cur_result) if cur_result is not None else False

        if in_base and base_flaky:
            # Flaky en baseline: cualquier resultado se tolera (incluso ausencia).
            categories["FLAKY"].append(identity)
            continue

        if in_base and not in_cur:
            categories["MISSING"].append(identity)
            continue

        if not in_base and in_cur:
            # Test nuevo: permitido solo si pasa; si falla, es NEW_FAIL.
            if cur_failed:
                categories["NEW_FAIL"].append(identity)
            else:
                categories["NEW_TEST"].append(identity)
            continue

        # Presente en ambos y no flaky:
        if base_failed and cur_failed:
            categories["KNOWN_FAIL"].append(identity)
        elif base_failed and not cur_failed:
            categories["FIXED"].append(identity)
        elif (not base_failed) and cur_failed:
            categories["NEW_FAIL"].append(identity)
        # pasaba y pasa -> nada que reportar.

    # Reporte legible.
    for cat in ("KNOWN_FAIL", "FLAKY", "FIXED", "NEW_TEST", "NEW_FAIL", "MISSING"):
        ids = categories[cat]
        print(f"{cat}={len(ids)}")
        for i in ids:
            print(f"  {cat}: {i}")

    if categories["FIXED"]:
        print("BASELINE_CLEANUP_REQUIRED")

    # El resultado lo deciden NEW_FAIL y MISSING.
    failed = len(categories["NEW_FAIL"]) + len(categories["MISSING"])
    if failed > 0:
        print(f"VERIFY_FAIL new_fail={len(categories['NEW_FAIL'])} missing={len(categories['MISSING'])}")
        return 1
    print("VERIFY_OK new_fail=0 missing=0")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Linea base de tests (JUnit).")
    sub = parser.add_subparsers(dest="unused")  # no se usa; se decide por flags

    parser.add_argument("--write", nargs=2, metavar=("JUNIT1", "JUNIT2"))
    parser.add_argument("--out", metavar="OUT")
    parser.add_argument("--commit", metavar="COMMIT", default="")
    parser.add_argument("--verify", metavar="JUNIT")
    parser.add_argument("--baseline", metavar="BASELINE")

    args = parser.parse_args(argv)

    if args.write:
        if not args.out:
            print("ERROR: --write requiere --out")
            return 2
        return write_baseline(args.write[0], args.write[1], args.out, args.commit)

    if args.verify:
        if not args.baseline:
            print("ERROR: --verify requiere --baseline")
            return 2
        return verify(args.verify, args.baseline)

    print("ERROR: use --write <j1> <j2> --out <json> | --verify <j> --baseline <json>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
