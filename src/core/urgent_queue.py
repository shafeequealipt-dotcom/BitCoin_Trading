"""UrgentQueue — watchdog concerns piggybacked into strategist calls.

Replaces watchdog's direct Claude calls in PASSIVE mode. Watchdog adds
concerns; strategist drains them into Call A (urgent only) or Call B (all).

Thread-safe: watchdog and strategist may access from different async contexts.
"""

import threading
import time
from dataclasses import dataclass, field

from src.core.logging import get_logger

log = get_logger("urgent_queue")


@dataclass
class WatchdogConcern:
    """A position concern from the Watchdog that needs Claude's attention."""

    symbol: str
    pnl_pct: float
    warnings: list[str]
    current_price: float
    entry_price: float
    side: str  # "Buy" or "Sell"
    sl_proximity_pct: float  # 0-100, how close to SL
    position_age_minutes: float
    stop_loss: float = 0.0
    urgency: str = "HIGH"  # "HIGH" or "CRITICAL"
    timestamp: float = field(default_factory=time.time)


class UrgentQueue:
    """Thread-safe queue for watchdog concerns.

    PASSIVE mode: watchdog adds concerns here instead of calling Claude.
    Call A drains items (injected into trade prompt with urgent addendum).
    Call B drains items (injected into position prompt, natural fit).
    """

    COOLDOWN_SECONDS = 150  # per-symbol cooldown (one brain cycle)
    MAX_CONCERNS = 10  # cap to prevent prompt bloat
    MAX_AGE_SECONDS = 600  # concerns older than 10 min are dropped

    def __init__(self):
        self._concerns: list[WatchdogConcern] = []
        self._lock = threading.Lock()
        self._last_queue_time: dict[str, float] = {}

    def add_concern(self, concern: WatchdogConcern) -> bool:
        """Add a concern. Returns False if suppressed by per-symbol cooldown."""
        now = time.time()
        last = self._last_queue_time.get(concern.symbol, 0)
        if now - last < self.COOLDOWN_SECONDS:
            return False

        with self._lock:
            # Replace existing concern for same symbol (latest wins)
            self._concerns = [
                c for c in self._concerns if c.symbol != concern.symbol
            ]
            self._concerns.append(concern)

            # Cap at MAX_CONCERNS (drop oldest)
            if len(self._concerns) > self.MAX_CONCERNS:
                self._concerns = self._concerns[-self.MAX_CONCERNS :]

        self._last_queue_time[concern.symbol] = now

        log.info(
            f"URGENT_QUEUE_ADD | sym={concern.symbol} pnl={concern.pnl_pct:+.2f}% "
            f"sl_consumed={concern.sl_proximity_pct:.0f}% urgency={concern.urgency} "
            f"warnings={len(concern.warnings)}"
        )
        return True

    def drain_concerns(self) -> list[WatchdogConcern]:
        """Atomically return and clear all pending concerns."""
        now = time.time()
        with self._lock:
            # Filter out stale concerns
            fresh = [
                c
                for c in self._concerns
                if now - c.timestamp < self.MAX_AGE_SECONDS
            ]
            self._concerns.clear()
        if fresh:
            log.info(
                f"URGENT_QUEUE_DRAIN | count={len(fresh)} "
                f"symbols=[{','.join(c.symbol for c in fresh)}]"
            )
        return fresh

    def clear_for_symbol(self, symbol: str) -> int:
        """Drop all queued concerns for a symbol and clear its dedup-cooldown.

        Called from a TradeCoordinator close-callback (registered in
        manager.py) so concerns generated while the position was open
        do not flow into the next CALL_A or CALL_B after the position
        has closed. This is the root-cause fix for F18 (phantom-close
        directives) — see dev_notes/six_tier_fixes/t1_1_phase1_investigation.md.

        Also pops the per-symbol entry from ``_last_queue_time`` so a
        future re-opened position for the same symbol can register a
        fresh concern immediately instead of waiting up to 150 s for
        the legacy dedup-cooldown to expire.

        Returns:
            Number of concerns that were dropped (0 if none queued).
        """
        with self._lock:
            before = len(self._concerns)
            self._concerns = [c for c in self._concerns if c.symbol != symbol]
            cleared = before - len(self._concerns)
            self._last_queue_time.pop(symbol, None)
        if cleared:
            log.info(f"URGENT_QUEUE_CLEAR | sym={symbol} cleared={cleared}")
        return cleared

    @property
    def has_concerns(self) -> bool:
        """Check if there are any pending concerns (non-destructive, thread-safe)."""
        with self._lock:
            return len(self._concerns) > 0

    # Phase 7 (Stage-1/2 fix): hard character cap on the formatted output.
    # MAX_CONCERNS already caps count at 10, but each concern's formatted
    # text is ~250-300 chars — a full queue blows ~3 KB into the prompt.
    # Capping at 1500 chars keeps the prompt-size budget predictable; any
    # trimmed concerns get summarised by the tail line so Claude still
    # knows they exist.
    MAX_FORMAT_CHARS = 1500

    def format_for_prompt(self, concerns: list[WatchdogConcern]) -> str:
        """Format concerns as text for Claude's prompt.

        Phase 7 (Stage-1/2 fix): output capped at ``MAX_FORMAT_CHARS``.
        CRITICAL urgency concerns are formatted first so they survive
        the cap; overflow concerns are elided with a tail summary line
        so Claude sees they existed without the prompt ballooning.
        """
        if not concerns:
            return ""
        # Priority-order: CRITICAL first, then HIGH, preserving original
        # insertion order within each tier. Stable sort — Python's sorted.
        concerns_sorted = sorted(
            concerns,
            key=lambda c: 0 if c.urgency == "CRITICAL" else 1,
        )
        header = (
            "\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n"
            "These positions need your attention. For each, decide: "
            "hold, close, tighten_stop, or set_exit.\n"
            "You MUST include position_actions for each alerted symbol "
            "in your response."
        )
        # Reserve a fixed budget for the tail-summary line so the total
        # output (header + concerns + optional tail) never exceeds
        # MAX_FORMAT_CHARS. 128 bytes is more than enough for a tail like
        # "(... 999 additional urgent concerns elided ...)".
        _TAIL_BUDGET = 128
        effective_cap = self.MAX_FORMAT_CHARS - _TAIL_BUDGET

        lines: list[str] = [header]
        total = len(header)
        rendered = 0
        from src.core.utils import format_price
        for c in concerns_sorted:
            tag = "CRITICAL" if c.urgency == "CRITICAL" else "URGENT"
            line = (
                f"\n[{tag}] {c.symbol} [{c.side}] — PnL: {c.pnl_pct:+.2f}%\n"
                f"  Entry: ${format_price(c.entry_price)} | Now: ${format_price(c.current_price)} | "
                f"SL: ${format_price(c.stop_loss)}\n"
                f"  SL consumed: {c.sl_proximity_pct:.0f}% | "
                f"Age: {c.position_age_minutes:.0f}min\n"
                f"  Warnings: {', '.join(c.warnings[:3])}"
            )
            # +1 accounts for the "\n".join separator added between lines.
            projected = total + len(line) + 1
            # When we're still within MAX_FORMAT_CHARS but would exceed the
            # effective cap, we need a tail — so stop BEFORE exceeding the
            # effective cap. If we can fit the line AND still fit the tail,
            # keep going; otherwise break and emit the tail.
            if projected > effective_cap and rendered < len(concerns_sorted) - 1:
                # More concerns remain — stopping here reserves tail budget.
                break
            if projected > self.MAX_FORMAT_CHARS:
                # Hard cap — even without more concerns we must stop.
                break
            lines.append(line)
            total = projected
            rendered += 1
        dropped = len(concerns_sorted) - rendered
        if dropped > 0:
            tail = (
                f"\n(... {dropped} additional urgent concern"
                f"{'s' if dropped != 1 else ''} elided to keep prompt bounded; "
                f"highest-priority CRITICALs retained above)"
            )
            lines.append(tail)
            log.info(
                f"URGENT_QUEUE_FORMAT_TRIMMED | rendered={rendered} "
                f"dropped={dropped} cap_chars={self.MAX_FORMAT_CHARS}"
            )
        return "\n".join(lines)
