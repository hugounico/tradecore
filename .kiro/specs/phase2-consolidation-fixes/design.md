# Phase 2 Consolidation Fixes — Bugfix Design

## Overview

This design addresses two bugs identified during Phase 2 consolidation:

1. **Debug endpoint exposed in production** — The `/debug/network` route in `src/api/app.py` exposes internal network diagnostics without authentication. It was a temporary debugging aid and must be removed entirely.

2. **"Invalid Date" cosmetic bug in dashboard** — The `handleStatus` function in `dashboard/index.html` calls `new Date(data.last_candle_time)` without null/validity checking. When the backend sends `null` (buffer empty at startup) or an unparseable string, the UI shows misleading text ("Último: 12:00:00 AM" or "Último: Invalid Date") instead of a safe fallback.

The fix strategy is minimal and surgical: delete dead code for Issue 1, add a guard clause for Issue 2.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug — (1) any HTTP request to `/debug/network`, (2) a status message where `last_candle_time` is `null` or produces an invalid `Date`
- **Property (P)**: The desired behavior — (1) HTTP 404 response, (2) display "Último: —"
- **Preservation**: Existing behavior that must remain unchanged — `/health` returns 200, `/ws/chart` delivers data, valid ISO timestamps display correctly
- **`debug_network`**: The handler function in `src/api/app.py` (lines 256–295) that performs DNS/TCP diagnostics
- **`handleStatus`**: The JavaScript function in `dashboard/index.html` that processes WebSocket `status` messages and updates UI elements
- **`ThrottledPusher.queue_status`**: Backend method that emits `last_candle_time` as either a valid ISO 8601 string (with trailing "Z") or `null`
- **`CandleBuffer.last_timestamp()`**: Returns `None` when buffer is empty (startup/no data received), or the most recent candle's `datetime` otherwise
- **RNF-01**: Requirement that a previously-displayed valid timestamp must NOT be cleared by subsequent null values — the fix only shows "—" when the incoming value is actually null/invalid

## Bug Details

### Bug Condition

**Issue 1 — Debug Endpoint:** The `/debug/network` route is reachable by any caller. It runs `socket.gethostbyname` and `socket.create_connection` against external hosts, leaking internal infrastructure information.

**Issue 2 — Invalid Date:** The `handleStatus` function blindly passes `data.last_candle_time` to `new Date()`. When the value is `null`, JavaScript produces epoch (1970-01-01T00:00:00). When the value is an unparseable string, `new Date()` returns an object whose `getTime()` is `NaN`.

**Formal Specification:**

```
FUNCTION isBugCondition_DebugEndpoint(X)
  INPUT: X of type HTTPRequest
  OUTPUT: boolean

  RETURN X.path = "/debug/network"
  // No method restriction — ANY HTTP method to this path triggers the condition
END FUNCTION
```

```
FUNCTION isBugCondition_InvalidDate(X)
  INPUT: X of type StatusMessage
  OUTPUT: boolean

  LET dt = new Date(X.last_candle_time)
  RETURN X.last_candle_time = null
         OR isNaN(dt.getTime())
END FUNCTION
```

### Examples

- **Issue 1**: `GET /debug/network` → currently returns 200 with JSON diagnostics; expected: HTTP 404. Also: `POST /debug/network` → expected 404 (any method)
- **Issue 2a**: Backend sends `{"last_candle_time": null}` (buffer empty at startup) → currently displays "Último: 12:00:00 AM" (epoch); expected: "Último: —"
- **Issue 2b**: Backend sends `{"last_candle_time": "not-a-date"}` → currently displays "Último: Invalid Date"; expected: "Último: —"
- **Issue 2c**: Backend sends `{"last_candle_time": "2025-01-15T14:30:00Z"}` → currently displays "Último: 9:30:00 AM" (correct, must be preserved)

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `GET /health` returns `{"status": "ok"}` with HTTP 200
- WebSocket at `/ws/chart` sends `initial_load` with candles and SMA series
- Static file serving from `/static` continues to work
- Valid ISO 8601 `last_candle_time` values (e.g., `"2025-01-15T14:30:00Z"`) display the correctly formatted local time
- Status messages retain `connected`, `last_candle_time`, and `mode` fields
- SMA(9)/SMA(21) signal engine operates identically
- `ThrottledPusher.queue_status` continues to send `null` or valid ISO string (no backend changes needed)
- Previously-displayed valid timestamps are NOT cleared by this fix — the fix only acts on the incoming `data.last_candle_time` value per message (RNF-01 compliance)

**Scope:**
- Issue 1: Only the `/debug/network` route is removed. All other routes and the WebSocket endpoint are unaffected.
- Issue 2: Only the `handleStatus` function is modified. Other message handlers (`handleCandle`, `handleSignal`, `handleSma`, `handleInitialLoad`) remain untouched.

## Hypothesized Root Cause

### Issue 1 — Debug Endpoint

**Root Cause**: Intentional temporary code that was never removed. The route was added during initial ECS deployment to diagnose Databento connectivity issues. The comments explicitly mark it as "TEMPORARY — DELETE AFTER DIAGNOSING NETWORK ISSUE". No downstream dependencies exist (confirmed: no frontend references, no test references, no other code imports or calls it).

### Issue 2 — Invalid Date

**Root Cause**: Missing null/validity guard in the `handleStatus` function. The current code:

```javascript
if (data.last_candle_time) {
    var dt = new Date(data.last_candle_time);
    lastTimestampEl.textContent = 'Último: ' + dt.toLocaleTimeString();
}
```

The `if (data.last_candle_time)` check is truthy-based, so it correctly blocks `null` from reaching `new Date()`. However:
- **Case (a)**: When `last_candle_time` is `null`, the `if` block is skipped entirely, but `lastTimestampEl.textContent` is never set to a fallback — it retains whatever was previously shown or remains empty. This is actually benign in isolation UNLESS the element starts empty at page load (which it does: `<span id="last-timestamp"></span>`). The real problem is that no initial "—" placeholder is displayed, leaving the user without context.
- **Case (b)**: When `last_candle_time` is a non-empty but unparseable string (truthy value), the `if` passes and `new Date("garbage")` produces `Invalid Date`, which `toLocaleTimeString()` renders as "Invalid Date".

The fix must handle both cases: (a) display "Último: —" when value is null/falsy, and (b) validate the Date object after construction to catch unparseable strings.

## Correctness Properties

Property 1: Bug Condition - Debug Endpoint Returns 404

_For any_ HTTP request (regardless of method: GET, POST, PUT, DELETE, etc.) to path `/debug/network`, the fixed application SHALL return HTTP 404 or 405 (endpoint does not exist / method not allowed), because the route handler and all associated code have been removed from the FastAPI router.

**Validates: Requirements 2.1**

Property 2: Bug Condition - Null/Invalid Date Shows Fallback

_For any_ status message where `last_candle_time` is `null` OR where `new Date(last_candle_time).getTime()` is `NaN`, the fixed `handleStatus` function SHALL set `lastTimestampEl.textContent` to `"Último: —"`.

**Validates: Requirements 2.2, 2.3**

Property 3: Preservation - Other Routes Unaffected

_For any_ HTTP request where the path is NOT `/debug/network`, the fixed application SHALL produce the same response as the original application, preserving all existing endpoints (`/health`, `/ws/chart`, `/static`).

**Validates: Requirements 3.1, 3.2**

Property 4: Preservation - Valid Timestamps Display Correctly

_For any_ status message where `last_candle_time` is a non-null string that produces a valid Date (i.e., `!isNaN(new Date(value).getTime())`), the fixed `handleStatus` function SHALL display `"Último: " + dt.toLocaleTimeString()` exactly as the original code does.

**Validates: Requirements 3.3, 3.4**

## Fix Implementation

### Changes Required

**File**: `src/api/app.py`

**Change**: Remove the `/debug/network` endpoint entirely (the `@app.get("/debug/network")` decorator, the `debug_network` function, and all surrounding comment blocks marked TEMPORARY).

**Specific Changes**:
1. **Delete lines 256–295** (the comment block, decorator, and entire `debug_network` function)
2. **No imports affected** — `socket` and `time` are only imported inside the function body (local imports), so no top-level import cleanup needed
3. **No route registration changes** — FastAPI auto-registers via decorators; removing the function removes the route

---

**File**: `dashboard/index.html`

**Function**: `handleStatus`

**Specific Changes**:
1. **Add null guard with fallback**: When `data.last_candle_time` is falsy, set text to "Último: —"
2. **Add validity check**: After constructing the Date, check `isNaN(dt.getTime())` — if invalid, set text to "Último: —"
3. **Preserve valid path**: Only call `dt.toLocaleTimeString()` when the Date is confirmed valid

**Before:**
```javascript
// Update last timestamp
if (data.last_candle_time) {
    var dt = new Date(data.last_candle_time);
    lastTimestampEl.textContent = 'Último: ' + dt.toLocaleTimeString();
}
```

**After:**
```javascript
// Update last timestamp — guard against null (buffer empty) and
// unparseable strings (would produce "Invalid Date" in the UI)
if (data.last_candle_time) {
    var dt = new Date(data.last_candle_time);
    if (!isNaN(dt.getTime())) {
        lastTimestampEl.textContent = 'Último: ' + dt.toLocaleTimeString();
    } else {
        // Unparseable value — show safe fallback
        lastTimestampEl.textContent = 'Último: —';
    }
} else {
    // null or missing — no candle data received yet
    lastTimestampEl.textContent = 'Último: —';
}
```

**RNF-01 Compliance Note:** This fix operates exclusively on the current incoming `data.last_candle_time` value. It does NOT clear a previously-displayed valid timestamp from a prior message. The backend (`ThrottledPusher.queue_status`) only sends `null` when `CandleBuffer.last_timestamp()` returns `None` (buffer empty). Once valid candles exist, `last_timestamp()` returns the preserved datetime even if Databento disconnects — the backend design already handles this correctly.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Confirm the bugs exist on the UNFIXED code before applying changes.

**Test Plan**:

**Issue 1 — Debug Endpoint:**
1. Start the application
2. `curl http://localhost:8000/debug/network` → expect 200 with JSON (confirms bug exists)
3. After fix: same curl → expect 404

**Issue 2 — Invalid Date:**
1. Open browser console, simulate: `new Date(null)` → epoch (confirms Case a)
2. Simulate: `new Date("not-a-date").toLocaleTimeString()` → "Invalid Date" (confirms Case b)
3. After fix: send status message with `null` → expect "Último: —"

**Expected Counterexamples**:
- Issue 1: HTTP 200 with network diagnostic JSON on unfixed code
- Issue 2: "Último: 12:00:00 AM" or "Último: Invalid Date" on unfixed code

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed code produces the expected behavior.

**Pseudocode:**
```
// Issue 1
FOR ALL X WHERE isBugCondition_DebugEndpoint(X) DO
  result := app_fixed.handle(X)
  ASSERT result.status_code = 404
END FOR

// Issue 2
FOR ALL X WHERE isBugCondition_InvalidDate(X) DO
  result := handleStatus_fixed(X)
  ASSERT lastTimestampEl.textContent = "Último: —"
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed code produces the same result as the original.

**Pseudocode:**
```
// Issue 1
FOR ALL X WHERE NOT isBugCondition_DebugEndpoint(X) DO
  ASSERT app_original.handle(X) = app_fixed.handle(X)
END FOR

// Issue 2
FOR ALL X WHERE NOT isBugCondition_InvalidDate(X) DO
  ASSERT handleStatus_original(X) = handleStatus_fixed(X)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

### Unit Tests

- **Issue 1**: Test that `/debug/network` returns 404 after removal (using httpx `TestClient`)
- **Issue 1**: Test that `/health` still returns 200
- **Issue 1**: Test that `/ws/chart` still accepts WebSocket connections
- **Issue 2**: Test `handleStatus` with `last_candle_time: null` → "Último: —"
- **Issue 2**: Test `handleStatus` with `last_candle_time: "not-a-date"` → "Último: —"
- **Issue 2**: Test `handleStatus` with `last_candle_time: ""` → "Último: —"
- **Issue 2**: Test `handleStatus` with `last_candle_time: "2025-01-15T14:30:00Z"` → valid time string

### Property-Based Tests

- Generate random HTTP paths and verify only `/debug/network` changed behavior (all others identical)
- Generate random strings for `last_candle_time` and verify: valid ISO strings produce formatted time, invalid/null produce "Último: —"
- Generate valid ISO 8601 UTC timestamps and confirm they all pass through `new Date()` correctly (no false positives in the validity check)

### Integration Tests

- Full application startup → verify `/debug/network` is not registered
- WebSocket connection → send status with `null` → verify dashboard element shows "—"
- WebSocket connection → send status with valid timestamp → verify dashboard element shows formatted time
- Full pytest suite execution → confirm no regressions across all existing tests

### Verification Protocol

Each issue must be verified with the following report format:

| Field | Content |
|-------|---------|
| **Issue** | Description of the bug |
| **Root cause** | Why the bug existed |
| **Change applied** | Exact code change |
| **Test executed** | How it was verified |
| **Result** | PASS/FAIL with evidence |

**Specific verification commands:**
1. `curl http://localhost:8000/debug/network` → must return 404
2. Valid ISO 8601 UTC timestamp passes `new Date()` correctly: `new Date("2025-01-15T14:30:00Z")` produces a valid Date with `!isNaN(dt.getTime())`
3. `pytest` — full suite must pass with no regressions
