# Bugfix Requirements Document

## Introduction

This document covers two bugs identified during Phase 2 consolidation of TradeCore:

1. **Debug endpoint exposed in production** — The `/debug/network` route in `src/api/app.py` exposes internal network diagnostics (DNS resolution, TCP connectivity to Databento and Google). It was marked as TEMPORARY during initial connectivity debugging and must be removed entirely.

2. **"Invalid Date" cosmetic bug in dashboard** — The `handleStatus` function in `dashboard/index.html` calls `new Date(data.last_candle_time)` without checking for `null`. When the backend sends `last_candle_time: null` (no candles received yet), `new Date(null)` produces epoch (Jan 1 1970 00:00:00), displaying misleading text like "Último: 7:00:00 PM" or "Invalid Date" if the value is an unexpected format.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a request is made to `/debug/network` THEN the system exposes internal network diagnostic information (DNS resolution of hist.databento.com, TCP connectivity to hist.databento.com:443, TCP connectivity to www.google.com:443) to any caller without authentication

1.2 WHEN the backend sends a status message with `last_candle_time` set to `null` THEN the dashboard creates `new Date(null)` which produces the epoch date (Jan 1 1970) and displays "Último: 12:00:00 AM" or the locale equivalent

1.3 WHEN the backend sends a status message with `last_candle_time` set to an unparseable string THEN the dashboard creates an invalid Date object and displays "Último: Invalid Date"

### Expected Behavior (Correct)

2.1 WHEN a request is made to `/debug/network` THEN the system SHALL return HTTP 404 (the endpoint does not exist)

2.2 WHEN the backend sends a status message with `last_candle_time` set to `null` THEN the dashboard SHALL display "Último: —" in the last-timestamp element

2.3 WHEN the backend sends a status message with `last_candle_time` set to an unparseable string (producing an Invalid Date) THEN the dashboard SHALL display "Último: —" in the last-timestamp element

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a request is made to `/health` THEN the system SHALL CONTINUE TO return `{"status": "ok"}` with HTTP 200

3.2 WHEN a WebSocket connection is established at `/ws/chart` THEN the system SHALL CONTINUE TO send `initial_load` messages with candles and SMA series

3.3 WHEN the backend sends a status message with a valid ISO 8601 `last_candle_time` (e.g. `"2025-01-15T14:30:00Z"`) THEN the dashboard SHALL CONTINUE TO display the formatted local time (e.g. "Último: 9:30:00 AM")

3.4 WHEN status messages are sent over WebSocket THEN the system SHALL CONTINUE TO include `connected`, `last_candle_time`, and `mode` fields with the same format as before

3.5 WHEN the SMA(9)/SMA(21) signal engine evaluates candle data THEN it SHALL CONTINUE TO operate identically (no changes to signal logic)

---

## Bug Condition Derivation

### Issue 1: Debug Endpoint

```pascal
FUNCTION isBugCondition_DebugEndpoint(X)
  INPUT: X of type HTTPRequest
  OUTPUT: boolean
  
  RETURN X.path = "/debug/network"
END FUNCTION
```

```pascal
// Property: Fix Checking — Debug Endpoint Removal
FOR ALL X WHERE isBugCondition_DebugEndpoint(X) DO
  result ← handleRequest'(X)
  ASSERT result.status_code = 404
END FOR
```

```pascal
// Property: Preservation Checking — Other Routes Unaffected
FOR ALL X WHERE NOT isBugCondition_DebugEndpoint(X) DO
  ASSERT handleRequest(X) = handleRequest'(X)
END FOR
```

### Issue 2: Invalid Date in Dashboard

```pascal
FUNCTION isBugCondition_InvalidDate(X)
  INPUT: X of type StatusMessage
  OUTPUT: boolean
  
  RETURN X.last_candle_time = null OR NOT isValidDate(new Date(X.last_candle_time))
END FUNCTION
```

```pascal
// Property: Fix Checking — Null/Invalid last_candle_time Handling
FOR ALL X WHERE isBugCondition_InvalidDate(X) DO
  result ← handleStatus'(X)
  ASSERT lastTimestampEl.textContent = "Último: —"
END FOR
```

```pascal
// Property: Preservation Checking — Valid Timestamps Still Displayed
FOR ALL X WHERE NOT isBugCondition_InvalidDate(X) DO
  ASSERT handleStatus(X) = handleStatus'(X)
END FOR
```
