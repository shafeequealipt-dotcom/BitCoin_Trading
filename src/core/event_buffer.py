"""Event Buffer — collects watchdog events for Claude's review.

Events are collected between Claude reviews. When Claude runs
(either 5-min timer or triggered), ALL buffered events are
formatted and included in the prompt.

Priority:
  HIGH — hard stop hit, 2+ positions closed, portfolio -5%
  MED  — SL/TP hit, big move (>2% in 5min), position action failed
  LOW  — timer close, small adjustments, info events
"""

import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("event_buffer")

# Phase 19 (Y-18): dedupe window. Two events with the same
# (symbol, event_type, payload_hash) within this many seconds collapse
# into one. 30 s matches the brief's recommended window — short enough
# to let legitimate re-emissions through after a status change, long
# enough to suppress watchdog tick spam (10 s cadence).
DEDUPE_WINDOW_SECONDS = 30.0


@dataclass
class WatchdogEvent:
    """A single event from watchdog observation."""
    priority: str  # "HIGH", "MED", "LOW"
    event_type: str  # "hard_stop", "sl_hit", "big_move", "timer_close", etc.
    symbol: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_prompt_line(self) -> str:
        priority_marker = {"HIGH": "!!!", "MED": "!!", "LOW": "!"}
        marker = priority_marker.get(self.priority, "!")
        data_str = " | ".join(f"{k}={v}" for k, v in self.data.items()) if self.data else ""
        age = int(time.time() - self.timestamp)
        return f"  [{marker}] {self.symbol}: {self.event_type} ({age}s ago) {data_str}"


class EventBuffer:
    """Collects events between Claude reviews, formatted for prompt."""

    MAX_EVENTS = 50
    MAX_TRIGGERS_PER_WINDOW = 2
    MIN_TRIGGER_GAP_SECONDS = 120  # 2 minutes
    TRIGGER_WINDOW_SECONDS = 300   # 5 minutes

    def __init__(self, data_lake=None):
        self._events: deque[WatchdogEvent] = deque(maxlen=self.MAX_EVENTS)
        self._trigger_times: list[float] = []
        self._last_trigger_time: float = 0
        self._data_lake = data_lake
        # Phase 19 (Y-18): dedupe ledger keyed by (symbol, event_type).
        # Stores (last_emit_ts, last_payload_hash, suppressed_count).
        # Suppressed count is reported in the EVBUF_DEDUPE log so ops
        # see how many duplicates were actually swallowed.
        self._dedupe: dict[tuple[str, str], tuple[float, str, int]] = {}

    def add_event(self, priority: str, event_type: str, symbol: str, **data) -> None:
        """Add an event from watchdog observation."""
        # Phase 19 (Y-18): dedupe by (symbol, event_type, payload_hash).
        # A repeated event with identical payload within DEDUPE_WINDOW
        # collapses into a single emission — surface the suppression
        # count so the original signal isn't completely silenced.
        try:
            payload_hash = hashlib.md5(
                json.dumps(data, sort_keys=True, default=str).encode()
            ).hexdigest()[:12]
        except Exception:
            # Fallback: fingerprint by event size when payload isn't
            # JSON-serializable. Worse-case behavior is no dedupe.
            payload_hash = f"size:{len(data)}"
        key = (symbol, event_type)
        now = time.time()
        prev = self._dedupe.get(key)
        if prev is not None:
            prev_ts, prev_hash, prev_n = prev
            if (
                prev_hash == payload_hash
                and (now - prev_ts) < DEDUPE_WINDOW_SECONDS
            ):
                # Duplicate inside the window — increment counter, log
                # at INFO so the suppression is auditable, return early.
                self._dedupe[key] = (prev_ts, payload_hash, prev_n + 1)
                log.info(
                    f"EVBUF_DEDUPE | sym={symbol} event={event_type} "
                    f"suppressed={prev_n + 1} window_s={DEDUPE_WINDOW_SECONDS:.0f} | {ctx()}"
                )
                return
        self._dedupe[key] = (now, payload_hash, 0)

        event = WatchdogEvent(
            priority=priority, event_type=event_type,
            symbol=symbol, data=data,
        )
        self._events.append(event)
        log.debug(f"EVBUF_ADD | type={event_type} sym={symbol} pri={priority} buf_size={len(self._events)} | {ctx()}")

        if priority == "HIGH":
            log.warning(
                "HIGH event: {sym} {type} {data}",
                sym=symbol, type=event_type, data=data,
            )

        # Data Lake: persist event (#10)
        if self._data_lake:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(
                        self._data_lake.write_event(
                            event_type=event_type, priority=priority,
                            symbol=symbol, data=data, source="watchdog",
                        )
                    )
            except Exception as e:
                # Phase 14 (P1-13) — was silent. Data-lake write is best-
                # effort observability so we still swallow, but the
                # failure is now visible at WARN for diagnosability.
                log.warning(f"Suppressed: {e} (data_lake event persist)")

    def get_events(self, since: float = 0) -> list[WatchdogEvent]:
        """Get events since a timestamp."""
        return [e for e in self._events if e.timestamp > since]

    def get_high_events(self) -> list[WatchdogEvent]:
        """Get only HIGH priority events."""
        return [e for e in self._events if e.priority == "HIGH"]

    def should_trigger_early_review(self) -> bool:
        """Check if events warrant an early Claude review.

        Conditions: HIGH events exist, within trigger limits.
        """
        now = time.time()

        # Check if any HIGH events
        high_events = [e for e in self._events if e.priority == "HIGH" and now - e.timestamp < self.TRIGGER_WINDOW_SECONDS]
        if not high_events:
            return False

        # Check trigger rate limits
        gap = now - self._last_trigger_time
        if gap < self.MIN_TRIGGER_GAP_SECONDS:
            return False

        recent_triggers = [t for t in self._trigger_times if now - t < self.TRIGGER_WINDOW_SECONDS]
        if len(recent_triggers) >= self.MAX_TRIGGERS_PER_WINDOW:
            return False

        log.info(f"EVBUF_TRIGGER | high_events={len(high_events)} gap={gap:.0f}s | {ctx()}")
        return True

    def mark_triggered(self) -> None:
        """Mark that an early review was triggered."""
        now = time.time()
        self._last_trigger_time = now
        self._trigger_times.append(now)
        # Clean old trigger times
        self._trigger_times = [t for t in self._trigger_times if now - t < 600]

    def get_prompt_text(self, max_events: int | None = None) -> str:
        """Format all buffered events for Claude's prompt.

        Args:
            max_events: optional hard cap on the number of events rendered
                into the prompt, applied AFTER the existing 10-minute recency
                filter. Priority ordering is preserved — HIGH and MED are
                kept ahead of LOW. Dropped events are summarised in a single
                trailing line so Claude sees the suppression rather than
                silently losing context. When ``None`` the historical
                behaviour (all recent events, 3000-char truncation) applies.

        Phase 2 session-stability: ``strategist._build_trade_prompt`` now
        passes ``max_events=settings.brain.prompt_event_buffer_max_events``
        (default 20) to bound the URGENT prompt tail during storms.
        """
        if not self._events:
            return ""

        now = time.time()
        recent = [e for e in self._events if now - e.timestamp < 600]  # Last 10 min
        if not recent:
            return ""

        # Partition first so we never drop HIGH before LOW.
        high = [e for e in recent if e.priority == "HIGH"]
        med = [e for e in recent if e.priority == "MED"]
        low = [e for e in recent if e.priority == "LOW"]

        dropped = 0
        capped_low_limit = 5  # pre-existing cap on LOW
        if max_events is not None and max_events >= 0:
            # Honour priority: keep HIGH in full, then MED, then LOW, each
            # newest-first, until we reach ``max_events``.
            def _newest(xs):
                return sorted(xs, key=lambda e: e.timestamp, reverse=True)

            ordered = _newest(high) + _newest(med) + _newest(low)
            kept = ordered[:max_events]
            dropped = max(0, len(ordered) - len(kept))
            high = [e for e in kept if e.priority == "HIGH"]
            med = [e for e in kept if e.priority == "MED"]
            low = [e for e in kept if e.priority == "LOW"]
            # Preserve chronological display within each tier.
            high.sort(key=lambda e: e.timestamp)
            med.sort(key=lambda e: e.timestamp)
            low.sort(key=lambda e: e.timestamp)
            capped_low_limit = len(low)  # already bounded by max_events

        lines = ["## WATCHDOG EVENTS (since last review)"]
        lines.append("These events occurred while you were thinking. Review and act accordingly.\n")

        if high:
            lines.append("URGENT:")
            for e in high:
                lines.append(e.to_prompt_line())
        if med:
            lines.append("IMPORTANT:")
            for e in med:
                lines.append(e.to_prompt_line())
        if low:
            lines.append("INFO:")
            for e in low[:capped_low_limit]:
                lines.append(e.to_prompt_line())

        if dropped > 0:
            lines.append(f"  ... ({dropped} earlier events dropped by max_events={max_events})")

        log.info(
            f"EVBUF_FLUSH | n={len(recent)} rendered={len(high)+len(med)+len(low)} "
            f"dropped={dropped} high={len(high)} med={len(med)} low={len(low)} | {ctx()}"
        )
        lines.append("")
        text = "\n".join(lines)
        if len(text) > 3000:
            # Truncate to keep HIGH events intact; cap total size
            truncated = text[:2800]
            last_newline = truncated.rfind("\n")
            text = truncated[:last_newline] + "\n  ... (remaining events truncated)"
        return text

    def clear(self) -> None:
        """Clear all events (called after Claude processes them)."""
        self._events.clear()

    def clear_for_symbol(self, symbol: str) -> int:
        """Drop all buffered events for one symbol.

        Used by the close-broadcast hub (Phase 2 — P0-1 ghost positions):
        when a position is reconciled as closed, any pending events for
        that symbol are stale and would mislead Claude on the next review.

        Returns the number of events removed.
        """
        before = len(self._events)
        # Rebuild the deque preserving insertion order, dropping by symbol.
        kept = [e for e in self._events if e.symbol != symbol]
        removed = before - len(kept)
        if removed > 0:
            self._events.clear()
            self._events.extend(kept)
            log.debug(
                f"EVBUF_CLEAR_SYM | sym={symbol} removed={removed} "
                f"buf_size={len(self._events)} | {ctx()}"
            )
        return removed

    @property
    def count(self) -> int:
        return len(self._events)

    @property
    def has_high(self) -> bool:
        return any(e.priority == "HIGH" for e in self._events)
