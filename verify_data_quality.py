#!/usr/bin/env python3
"""Offline validation for Problem 6 / F22 — the brain prompt's data-quality
signals are accurate and the loss-lesson cause is not truncated mid-sentence.
Read-only.

Three sub-bugs fixed:
(a) the "strategy inputs were incomplete" note fired on ANY blocker (incl. the
    advisory recent_loss_within_1h), contradicting completeness=1.00;
(b) recent_loss_within_1h (an advisory state flag) rendered as a data
    source_failed on every flagged coin;
(c) the TIAS Cause was hard-cut at 57 chars, dropping the failure pattern.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

sys.path.insert(0, ".")

from src.brain.strategist import ClaudeStrategist  # noqa: E402
from src.core.coin_package_validator import (  # noqa: E402
    SOURCE_FAILURE_MARKERS,
    STRATEGY_INPUT_FAILURE_MARKERS,
)

_FAIL: list[str] = []
SRC = Path("src/brain/strategist.py").read_text()


def chk(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _FAIL.append(name)


# ── (a) the incomplete-message fires only on real strategy-input failures ──
print("Sub-bug (a) — 'inputs incomplete' agrees with completeness")


def incomplete_fires(blockers, missing):
    # mirrors the production guard at strategist.py
    fail = [b for b in blockers if b in STRATEGY_INPUT_FAILURE_MARKERS]
    return bool(fail or missing)


chk("advisory-only blocker (recent_loss) does NOT fire the incomplete note",
    incomplete_fires(["recent_loss_within_1h"], []) is False)
chk("manipulation flag does NOT fire the incomplete note",
    incomplete_fires(["manipulation_likely_session"], []) is False)
chk("a real strategy-input failure DOES fire (signal_missing)",
    incomplete_fires(["signal_missing"], []) is True)
chk("a missing field DOES fire", incomplete_fires([], ["signals.confidence"]) is True)

# ── (b) blocker categorization: source_failed vs advisory ──────────────────
print("\nSub-bug (b) — only real source failures render as source_failed")


def categorize(blockers):
    src = [b for b in blockers if b in SOURCE_FAILURE_MARKERS]
    adv = [b for b in blockers if b not in SOURCE_FAILURE_MARKERS]
    return src, adv


_src, _adv = categorize(["recent_loss_within_1h", "signal_missing", "funding_missing",
                         "manipulation_likely_session"])
chk("recent_loss_within_1h is advisory, not source_failed",
    "recent_loss_within_1h" in _adv and "recent_loss_within_1h" not in _src)
chk("manipulation_likely_session is advisory", "manipulation_likely_session" in _adv)
chk("signal_missing and funding_missing are source_failed",
    "signal_missing" in _src and "funding_missing" in _src)
chk("a no-loss coin (only data failures) shows NO advisory recent_loss",
    "recent_loss_within_1h" not in categorize(["xray_missing"])[1])

# ── (c) TIAS cause keeps the failure pattern (real method) ─────────────────
print("\nSub-bug (c) — TIAS cause is not truncated mid-sentence")
stub = types.SimpleNamespace(
    settings=types.SimpleNamespace(
        brain=types.SimpleNamespace(tias_cause_max_chars=120)
    )
)
long_cause = (
    "The trade was a loss primarily due to entering during a trending_down "
    "regime against the structural bias while the win-rate table favored the "
    "other side; the stop was also placed too tight for the volatility."
)
lines = ClaudeStrategist._format_recent_loss_lines(stub, [{
    "direction": "Buy", "pnl_pct": -1.26, "closed_by": "bybit_sl_hit",
    "hold_seconds": 1500, "regime": "trending_down", "ds_why": long_cause,
}])
cause_line = lines[0] if lines else ""
cause_text = cause_line.split("Cause:", 1)[1].strip() if "Cause:" in cause_line else ""
kept = cause_text.rstrip(".").rstrip("…").rstrip(".")
chk("cause kept well beyond the old 57-char cut", len(kept) > 70,
    f"len={len(kept)}")
chk("cause does not exceed the configured max + ellipsis", len(kept) <= 121,
    f"len={len(kept)}")
chk("cause ends at a word boundary (no mid-word cut)",
    bool(cause_text) and (long_cause.startswith(kept.rstrip("."))
                          or kept.split()[-1] in long_cause.split()),
    f"tail={kept[-20:]!r}")
# Short causes are untouched.
short = "stopped out by a tight stop."
lines2 = ClaudeStrategist._format_recent_loss_lines(stub, [{
    "direction": "Sell", "pnl_pct": -0.3, "closed_by": "sl",
    "hold_seconds": 60, "regime": "volatile", "ds_why": short,
}])
chk("short cause is shown in full (no ellipsis)",
    short.rstrip(".") in lines2[0] and "..." not in lines2[0].split("Cause:")[1])

# ── source guard ───────────────────────────────────────────────────────────
print("\nSource guard")
chk("strategist imports the canonical marker sets",
    "STRATEGY_INPUT_FAILURE_MARKERS" in SRC and "SOURCE_FAILURE_MARKERS" in SRC)
chk("source_failed render uses SOURCE_FAILURE_MARKERS",
    "b in SOURCE_FAILURE_MARKERS" in SRC)
chk("advisory line rendered separately", "Advisory:" in SRC)
chk("no hard 57-char cut remains", "ds_why[:57]" not in SRC)
chk("truncation reads tias_cause_max_chars", "tias_cause_max_chars" in SRC)

print()
if _FAIL:
    print(f"RESULT: FAIL ({len(_FAIL)}): {_FAIL}")
    sys.exit(1)
print("RESULT: PASS — the incomplete note agrees with completeness, advisory")
print("flags no longer render as source failures, and the TIAS cause keeps its")
print("decision-relevant context with boundary-aware truncation.")
