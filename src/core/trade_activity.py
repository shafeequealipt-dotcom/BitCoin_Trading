"""Trade-Activity Watchdog logic (2026-08-18) — detects a bot that is UP but
not actually trading.

## Why this exists

On 2026-08-16 01:06 the LLM provider (ZenMux) began returning HTTP 403
``access_denied`` on every completion: the free model this bot depended on had
silently been reclassified from $0/M to paid, and the account had no billing.

Nothing detected it for **2.5 days**. Equity sat frozen at $8,663.69 while:

  * systemd reported ``trading-workers`` active the whole time,
  * every worker ticked normally and logged heartbeats,
  * ``BrainLivenessWatchdog`` stayed green — the brain loop WAS progressing
    and dutifully writing its heartbeat every iteration,
  * ``CALL_A`` ran on schedule and ended ``status=failed`` every single cycle.

That is the gap this module closes. The existing liveness watchdogs answer
"is the process alive and progressing?" — and during that outage the honest
answer was *yes*. Nobody was asking the question that actually mattered:
**"is it still placing trades?"**

A provider returning clean, well-formed 403s is indistinguishable from health
to every heartbeat-based check. Only outcome-based monitoring catches it.

## Two signals, deliberately different severities

1. ``consecutive_brain_failures`` — the PRECISE detector. Both brain clients
   already maintain this counter (incremented on API error, reset to 0 on
   success), so a provider outage drives it up monotonically within minutes.
   This is high-confidence and immediately actionable ("your LLM provider is
   rejecting us"), so it is the CRITICAL signal.

2. ``seconds_since_last_trade`` — the BROAD backstop, for silent failures that
   never surface as an API error (a parser regression, an over-tight gate, a
   stuck execution path). This one is deliberately a WARNING with a generous
   threshold: quiet markets legitimately produce no trades, and this system
   now runs entry gates that intentionally block the majority of candidates,
   so a short dry spell is normal and must NOT page anyone.

Failure precedence is signal 1 over signal 2: when the provider is failing,
"no trades" is a downstream symptom, and reporting the cause beats reporting
the symptom.

## Fail-open

Any input that is ``None`` (stat unavailable, no trade history yet, DB read
failed) yields ``VERDICT_UNKNOWN`` and never alerts. A monitoring component
must not be able to manufacture a false alarm out of missing data — the whole
point is to raise trust in alerts, and a watchdog that cries wolf on a cold
boot gets muted by operators, which would reproduce the very blindness it
was built to remove.
"""

from __future__ import annotations

from dataclasses import dataclass

VERDICT_HEALTHY = "healthy"
VERDICT_BRAIN_FAILING = "brain_failing"
VERDICT_NO_TRADES = "no_trades"
VERDICT_UNKNOWN = "unknown"


@dataclass(frozen=True)
class TradeActivityResult:
    """Structured verdict for one trade-activity probe.

    Attributes:
        verdict: One of the VERDICT_* constants.
        should_alert: True when the condition warrants operator attention.
            Kept separate from ``verdict`` so the caller decides dispatch
            (and rate-limits it), mirroring the entry-gate
            ``would_block``/``verdict`` split used elsewhere.
        severity: "critical" | "warning" | "none" — lets the caller pick the
            alert channel/wording without re-deriving it from the verdict.
        consecutive_failures: Brain-client failure streak, or None.
        seconds_since_last_trade: Age of the most recent trade ENTRY, or None.
        failure_threshold: The streak length that triggers critical.
        no_trade_threshold_seconds: The dry-spell length that triggers warning.
        reason: Short machine-readable reason code.
    """
    verdict: str
    should_alert: bool
    severity: str
    consecutive_failures: int | None
    seconds_since_last_trade: float | None
    failure_threshold: int
    no_trade_threshold_seconds: float
    reason: str


def evaluate_trade_activity(
    consecutive_failures: int | None,
    seconds_since_last_trade: float | None,
    failure_threshold: int,
    no_trade_threshold_seconds: float,
) -> TradeActivityResult:
    """Classify whether the bot is genuinely trading.

    Args:
        consecutive_failures: Current failure streak from the brain client
            (``get_stats()['consecutive_failures']``, or the private
            ``_consecutive_failures`` attribute for clients that expose no
            ``get_stats``). None when unavailable -> that signal is skipped.
        seconds_since_last_trade: Seconds since the most recent trade ENTRY
            (not close — a position held for hours is still evidence the
            entry path works). None when there is no trade history at all.
        failure_threshold: Alert critically once the streak reaches this.
            <= 0 disables this check (kill switch).
        no_trade_threshold_seconds: Warn once the dry spell reaches this.
            <= 0 disables this check (kill switch).

    Returns:
        TradeActivityResult. Caller owns dispatch and rate-limiting.
    """
    _failing = (
        failure_threshold > 0
        and consecutive_failures is not None
        and consecutive_failures >= failure_threshold
    )
    if _failing:
        return TradeActivityResult(
            verdict=VERDICT_BRAIN_FAILING,
            should_alert=True,
            severity="critical",
            consecutive_failures=consecutive_failures,
            seconds_since_last_trade=seconds_since_last_trade,
            failure_threshold=failure_threshold,
            no_trade_threshold_seconds=no_trade_threshold_seconds,
            reason="consecutive_brain_failures_threshold_reached",
        )

    _dry = (
        no_trade_threshold_seconds > 0
        and seconds_since_last_trade is not None
        and seconds_since_last_trade >= no_trade_threshold_seconds
    )
    if _dry:
        return TradeActivityResult(
            verdict=VERDICT_NO_TRADES,
            should_alert=True,
            severity="warning",
            consecutive_failures=consecutive_failures,
            seconds_since_last_trade=seconds_since_last_trade,
            failure_threshold=failure_threshold,
            no_trade_threshold_seconds=no_trade_threshold_seconds,
            reason="no_trade_entry_within_threshold",
        )

    # Nothing to judge on: no failure stat AND no trade history.
    if consecutive_failures is None and seconds_since_last_trade is None:
        return TradeActivityResult(
            verdict=VERDICT_UNKNOWN,
            should_alert=False,
            severity="none",
            consecutive_failures=None,
            seconds_since_last_trade=None,
            failure_threshold=failure_threshold,
            no_trade_threshold_seconds=no_trade_threshold_seconds,
            reason="no_signals_available",
        )

    return TradeActivityResult(
        verdict=VERDICT_HEALTHY,
        should_alert=False,
        severity="none",
        consecutive_failures=consecutive_failures,
        seconds_since_last_trade=seconds_since_last_trade,
        failure_threshold=failure_threshold,
        no_trade_threshold_seconds=no_trade_threshold_seconds,
        reason="trading_activity_ok",
    )
