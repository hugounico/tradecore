# Implementation Plan: Phase 2 Consolidation Fixes

## Overview

This plan fixes two bugs identified during Phase 2 consolidation: (1) removal of a temporary `/debug/network` endpoint exposed in production, and (2) fix for "Invalid Date" display in the dashboard when `last_candle_time` is null or unparseable. The workflow follows the bug condition methodology: write exploration tests to confirm bugs exist, write preservation tests to lock existing behavior, apply minimal surgical fixes, then verify everything passes.

## Tasks

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Debug Endpoint Reachable + Invalid Date Displayed
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bugs exist
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fixes when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate both bugs exist on unfixed code
  - **Issue 1 — Debug Endpoint (Python/pytest):**
    - Use `httpx.AsyncClient` with `ASGITransport(app=app)` to send `GET /debug/network`
    - Assert response status code is 404 (expected behavior after fix)
    - On UNFIXED code this will FAIL because the endpoint currently returns 200 with JSON diagnostics
    - Bug condition: `isBugCondition_DebugEndpoint(X) = X.path="/debug/network"` (any HTTP method)
  - **Issue 1 — Also test with POST method:**
    - Use `httpx.AsyncClient` to send `POST /debug/network`
    - Assert response status code is 404 or 405 (confirms ALL methods are blocked, not just GET)
  - **Issue 2 — Invalid Date (JavaScript logical test via Python):**
    - Write a Python test that simulates the `handleStatus` logic for THREE cases:
    - (a) `last_candle_time = "2025-01-15T14:30:00Z"` (valid ISO 8601) → assert result is formatted time string (NOT "—")
    - (b) `last_candle_time = null` (None) → assert result is `"Último: —"`
    - (c) `last_candle_time = "invalid"` or `""` (non-parseable) → assert result is `"Último: —"`
    - Implement the CURRENT (unfixed) JS logic as a Python function to demonstrate the bug
    - On UNFIXED logic: case (b) produces no output (empty), case (c) produces "Último: Invalid Date"
    - Bug condition: `isBugCondition_InvalidDate(X) = X.last_candle_time is None OR isNaN(new Date(X.last_candle_time).getTime())`
  - **Scoped PBT Approach**: For Issue 1, scope to the concrete path `/debug/network`. For Issue 2, use Hypothesis to generate random strings and verify that invalid/null inputs trigger the bug condition
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests FAIL (this confirms both bugs exist)
  - Document counterexamples found:
    - Issue 1: `GET /debug/network` → HTTP 200 (expected 404)
    - Issue 2: `handleStatus({last_candle_time: null})` → empty/no fallback; `handleStatus({last_candle_time: "invalid"})` → "Último: Invalid Date"
  - Mark task complete when tests are written, run, and failures are documented
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Other Routes and Valid Timestamps Unaffected
  - **IMPORTANT**: Follow observation-first methodology — run on UNFIXED code first
  - **Issue 1 — Preservation (Python/pytest + Hypothesis):**
    - Observe: `GET /health` returns 200 with `{"status": "ok"}` on unfixed code
    - Observe: WebSocket at `/ws/chart` accepts connections on unfixed code
    - Write property-based test: for all HTTP paths that are NOT `/debug/network`, the application responds identically (use Hypothesis `st.text()` for random paths)
    - Specifically test `/health` returns 200 and `/nonexistent` returns 404
  - **Issue 2 — Preservation (Python logical test + Hypothesis):**
    - Observe: valid ISO 8601 timestamps (e.g., `"2025-01-15T14:30:00Z"`) produce formatted time on unfixed code
    - Write property-based test: for all valid ISO 8601 UTC strings, the FIXED logic must produce `"Último: " + dt.toLocaleTimeString()` equivalent (same as original behavior)
    - Use Hypothesis to generate valid datetime strings and confirm they are NOT treated as invalid
    - Property: `for all X where !isBugCondition_InvalidDate(X): handleStatus'(X) == handleStatus(X)`
  - Verify ALL preservation tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix Issue 1 — Remove `/debug/network` endpoint

  - [x] 3.1 Confirm root cause in `src/api/app.py`
    - Verify the `/debug/network` route exists at lines 256–295
    - Confirm it is marked with "TEMPORARY — DELETE AFTER DIAGNOSING NETWORK ISSUE"
    - Confirm no other code imports or depends on the `debug_network` function
    - Confirm `socket` and `time` are local imports inside the function (no top-level cleanup needed)
    - _Requirements: 1.1_

  - [x] 3.2 Implement the fix — delete debug endpoint
    - Delete the entire block: comment header (lines 256–260), `@app.get("/debug/network")` decorator, `async def debug_network()` function, and closing comment (line 295)
    - Scope: ONLY `src/api/app.py` is modified — no other files touched
    - _Bug_Condition: isBugCondition_DebugEndpoint(X) where X.path = "/debug/network"_
    - _Expected_Behavior: HTTP 404 for any request to /debug/network_
    - _Preservation: /health returns 200, /ws/chart accepts WebSocket, /static serves files_
    - _Requirements: 2.1, 3.1, 3.2_

  - [x] 3.3 Verify with `curl` — debug endpoint returns 404 for ANY method
    - **Property 1: Expected Behavior** - Debug Endpoint Returns 404
    - Start the application (or use httpx TestClient)
    - Execute: `curl http://localhost:8000/debug/network` (GET) → expected 404
    - Execute: `curl -X POST http://localhost:8000/debug/network` (POST) → expected 404 or 405
    - **EXPECTED OUTCOME**: HTTP 404/405 for ALL methods (endpoint no longer exists)
    - If the application cannot be started locally (missing API keys, etc.), use httpx TestClient in a pytest test instead and report the limitation
    - _Requirements: 2.1_

- [x] 4. Fix Issue 2 — Guard `handleStatus` against null/invalid dates

  - [x] 4.1 Confirm root cause in `dashboard/index.html`
    - Verify the `handleStatus` function passes `data.last_candle_time` directly to `new Date()` without validity checking
    - Confirm the `if (data.last_candle_time)` check blocks null but NOT invalid strings (truthy non-parseable values pass through)
    - Confirm no `else` branch sets a fallback for null/missing values
    - _Requirements: 1.2, 1.3_

  - [x] 4.2 Implement the fix — add null/validity guard
    - Replace the current `if (data.last_candle_time)` block in `handleStatus` with:
    ```javascript
    if (data.last_candle_time) {
        var dt = new Date(data.last_candle_time);
        if (!isNaN(dt.getTime())) {
            lastTimestampEl.textContent = 'Último: ' + dt.toLocaleTimeString();
        } else {
            lastTimestampEl.textContent = 'Último: —';
        }
    } else {
        lastTimestampEl.textContent = 'Último: —';
    }
    ```
    - Scope: ONLY `dashboard/index.html` is modified — no other files touched
    - _Bug_Condition: isBugCondition_InvalidDate(X) where X.last_candle_time is null OR isNaN(new Date(X.last_candle_time).getTime())_
    - _Expected_Behavior: lastTimestampEl.textContent = "Último: —" for all bug-condition inputs_
    - _Preservation: Valid ISO 8601 timestamps continue to display formatted local time_
    - _Requirements: 2.2, 2.3, 3.3, 3.4_

  - [x] 4.3 Verify with logical test — all 3 cases covered
    - **Property 1: Expected Behavior** - Null/Invalid Date Shows Fallback
    - **IMPORTANT**: Re-run the SAME exploration test from task 1 (Issue 2 portion) — do NOT write a new test
    - Execute the Python logical test that simulates the FIXED `handleStatus` logic for all THREE cases:
    - (a) `last_candle_time = "2025-01-15T14:30:00Z"` → displays formatted time correctly (e.g., "Último: 9:30:00 AM" or locale equivalent)
    - (b) `last_candle_time = null` (None) → displays "Último: —"
    - (c) `last_candle_time = "invalid"` or `""` → displays "Último: —"
    - **EXPECTED OUTCOME**: All 3 cases PASS
    - _Requirements: 2.2, 2.3, 3.3_

  - [x] 4.4 Visual browser verification (complementary)
    - Start the application locally and open `http://localhost:8000/static/index.html` in a browser
    - **Case 1 (startup):** On first load before any candles arrive, verify the status bar shows "Último: —" (not "Invalid Date" or empty)
    - **Case 2 (valid data):** Wait for candles to arrive via WebSocket — verify "Último:" shows a formatted time (e.g., "Último: 10:05:00 AM")
    - If the application cannot be started locally (missing API keys, Databento not available), report exactly what blocks the visual verification and note that the logical test from 4.3 provides equivalent coverage
    - Do NOT mark this verification as "PASS" if it could not be executed — state the limitation clearly
    - _Requirements: 2.2, 2.3, 3.3_

- [x] 5. Re-run exploration and preservation tests after both fixes

  - [x] 5.1 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Both Bugs Fixed
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior for both issues
    - When this test passes, it confirms both bugs are fixed
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bugs are fixed)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 5.2 Verify preservation tests still pass
    - **Property 2: Preservation** - No Regressions After Fix
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all preservation tests still pass after both fixes
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 6. Scope confirmation
  - Verify that ONLY these two files were modified:
    - `src/api/app.py` (Issue 1 — removed `/debug/network` endpoint)
    - `dashboard/index.html` (Issue 2 — added null/validity guard in `handleStatus`)
  - **Issue 1 — `src/api/app.py` diff summary:**
    - Only deletion: lines 256–295 removed (TEMPORARY comment block + `@app.get("/debug/network")` + `async def debug_network()` function + closing comment)
    - No additions, no modifications to any other line
  - **Issue 2 — `dashboard/index.html` line-by-line diff:**
    - Show the exact before/after of the `handleStatus` function's timestamp section
    - Confirm that ONLY the `// Update last timestamp` block was modified
    - Confirm that no other function (`handleInitialLoad`, `handleCandle`, `handleSignal`, `handleSma`, `connectWebSocket`, `initChart`) was touched
    - Produce the diff using `git diff dashboard/index.html` or equivalent comparison
  - Confirm NO changes to: signal engine (`src/engine/`), AWS config, ThrottledPusher (`src/api/throttled_pusher.py`), CandleBuffer (`src/pipeline/candle_buffer.py`), DabentoConnector (`src/connectors/`), SimulationReplay (`src/pipeline/simulation_replay.py`), or any other file
  - If any additional files were touched, explain why and revert if not justified
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 7. Run full pytest suite — regression check
  - Execute: `pytest` (full suite)
  - This runs all existing unit tests, integration tests, and property-based tests
  - **EXPECTED OUTCOME**: All tests PASS with no regressions
  - **Requirement 3.5 traceability (SMA engine unchanged):**
    - Explicitly confirm that the following SignalEngine test files ran and passed:
      - `tests/unit/test_signal_engine.py`
      - `tests/property/test_signal_engine_props.py`
    - Include the pass count for these files in the output (e.g., "test_signal_engine.py: 8 passed")
    - This provides explicit evidence that the SMA(9)/SMA(21) logic was not affected
  - If any test fails, investigate and report:
    - Which test failed
    - Why it failed
    - Whether the failure is related to the fixes or pre-existing
  - If pytest cannot be executed (missing dependencies, environment issues), report exactly what blocks it
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 8. Final verification report
  - Produce the following report for each issue:

  **Issue 1 — Debug Endpoint Exposed:**
  | Field | Content |
  |-------|---------|
  | **Issue** | `/debug/network` route exposes internal network diagnostics without authentication |
  | **Root cause** | Temporary debugging code never removed (marked "DELETE AFTER DIAGNOSING") |
  | **Change applied** | Deleted lines 256–295 of `src/api/app.py` (decorator + function + comments) |
  | **Test executed** | `curl /debug/network` or httpx TestClient → expected 404 |
  | **Result** | PASS / FAIL (with evidence) |

  **Issue 2 — Invalid Date in Dashboard:**
  | Field | Content |
  |-------|---------|
  | **Issue** | `handleStatus` displays "Invalid Date" or epoch time for null/unparseable `last_candle_time` |
  | **Root cause** | Missing validity guard — truthy non-parseable strings pass `if` check; no `else` fallback for null |
  | **Change applied** | Added `isNaN(dt.getTime())` check + `else` branch in `handleStatus` in `dashboard/index.html` |
  | **Test executed** | Logical test covering 3 cases: (a) valid ISO → time, (b) null → "—", (c) invalid → "—" |
  | **Result** | PASS / FAIL (with evidence) |

  **Regression:**
  | Field | Content |
  |-------|---------|
  | **Test executed** | `pytest` full suite |
  | **Result** | PASS / FAIL (with details if FAIL) |

  **Requirement 3.5 — SMA Engine Unchanged:**
  | Field | Content |
  |-------|---------|
  | **Test executed** | `tests/unit/test_signal_engine.py` + `tests/property/test_signal_engine_props.py` (as part of full suite) |
  | **Result** | PASS / FAIL — include pass count to confirm SignalEngine tests ran |
  | **Evidence** | Explicit pytest output showing these files passed without modifications |

  - Do NOT mark any verification as "verified" if it could not actually be executed — report exactly what blocks it
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_


## Notes

- Stack: Python 3.11+ / FastAPI / vanilla JS. Testing: pytest + Hypothesis.
- Issue 2 JavaScript logic is tested via Python simulation since there is no JS test runner in the project.
- The `handleStatus` fix operates on the current incoming `data.last_candle_time` value per message — it does NOT clear a previously-displayed valid timestamp (RNF-01 compliance).
- `ThrottledPusher.queue_status` already sends `null` when buffer is empty and valid ISO string otherwise — no backend changes needed.
- If `curl` verification cannot be executed (missing API keys, Docker not running), use httpx TestClient instead and document the limitation.

## Task Dependency Graph

```json
{
  "waves": [
    ["1"],
    ["2"],
    ["3"],
    ["4"],
    ["5"],
    ["6"],
    ["7"],
    ["8"]
  ]
}
```
