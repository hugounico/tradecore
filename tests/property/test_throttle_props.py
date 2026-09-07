"""Property-based tests for ThrottledPusher visual throttle invariant.

Validates:
- RF-02.2: Max 1 visual update per second via unified throttle
"""

import asyncio
from unittest.mock import AsyncMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from src.api.throttled_pusher import ThrottledPusher


# --- Strategies ---

# Strategy for time deltas between events (0 to 5 seconds, in 0.01s steps)
time_delta_st = st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False)

# Strategy for event types to queue
event_type_st = st.sampled_from(["candle", "signal", "sma", "status"])


# Strategy for a sequence of (time_delta, event_type) pairs representing update events
event_sequence_st = st.lists(
    st.tuples(time_delta_st, event_type_st),
    min_size=2,
    max_size=30,
)


# Feature: tradecore-mvp, Property 4: Unified Visual Throttle Invariant
class TestUnifiedVisualThrottleInvariant:
    """Generate sequences of update events with varying timestamps; verify no two
    consecutive WebSocket sends are less than 1 second apart.

    **Validates: Requirements RF-02.2**
    """

    @given(events=event_sequence_st)
    @settings(max_examples=100)
    def test_no_two_sends_within_one_second(self, events: list[tuple[float, str]]) -> None:
        """No two consecutive WebSocket sends should be less than 1 second apart."""
        asyncio.run(self._run_throttle_test(events))

    async def _run_throttle_test(self, events: list[tuple[float, str]]) -> None:
        """Execute the throttle test with mocked time and WebSocket."""
        pusher = ThrottledPusher(throttle_interval=1.0)

        # Mock WebSocket that records send times
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock()
        await pusher.set_connection(mock_ws)

        # Track simulated time and actual send timestamps
        simulated_time = 1000.0  # Start at arbitrary base time
        send_times: list[float] = []

        # Original send_text to intercept and record send times
        original_send_text = mock_ws.send_text

        async def recording_send_text(data):
            send_times.append(simulated_time)

        mock_ws.send_text = AsyncMock(side_effect=recording_send_text)

        for time_delta, event_type in events:
            simulated_time += time_delta

            # Queue an event based on type
            if event_type == "candle":
                pusher._pending_messages.append({
                    "type": "candle",
                    "data": {"time": int(simulated_time), "open": 100.0,
                             "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10},
                })
            elif event_type == "signal":
                pusher._pending_messages.append({
                    "type": "signal",
                    "data": {"signal_type": "BUY", "time": int(simulated_time),
                             "price": 100.5, "sma_fast": 100.2, "sma_slow": 99.8},
                })
            elif event_type == "sma":
                pusher._pending_messages.append({
                    "type": "sma",
                    "data": {"time": int(simulated_time), "sma_fast": 100.2,
                             "sma_slow": 99.8},
                })
            elif event_type == "status":
                pusher._pending_messages.append({
                    "type": "status",
                    "data": {"connected": True, "last_candle_time": None,
                             "mode": "simulation"},
                })

            # Attempt flush at current simulated time
            with patch("src.api.throttled_pusher.time.time", return_value=simulated_time):
                await pusher.flush_if_ready()

        # Verify invariant: no two consecutive sends are less than 1 second apart
        for i in range(1, len(send_times)):
            gap = send_times[i] - send_times[i - 1]
            assert gap >= 1.0, (
                f"Throttle violated: consecutive sends at t={send_times[i-1]:.3f} "
                f"and t={send_times[i]:.3f} are only {gap:.3f}s apart (< 1.0s)"
            )
