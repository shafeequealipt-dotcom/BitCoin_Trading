#!/usr/bin/env python3
"""Offline validation for Problem 5 / F21 — the X-RAY structural block shows the
ACTUAL candidates with correctly-formatted price levels. Read-only.

Before: the live CALL_A block called structure_cache.get_top_setups(n=8), which
returned the top-confluence coins across the ~50-coin universe — leaking ~10
untradeable coins (LINK/AERO/ICP/AXS...) while dropping 4 of 5 actual candidates;
and it formatted FVG/OB price bounds with `.0f`, collapsing sub-dollar coins to
`$0-$0` and mid-price coins to `$8-$8`.

After: the block fetches each candidate's cached structural analysis directly
(ranked by score, falling back to the universe-top only on a cold-start race),
and formats every FVG/OB bound with format_price (magnitude-aware decimals).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

from src.core.utils import format_price  # noqa: E402

_FAIL: list[str] = []
SRC = Path("src/brain/strategist.py").read_text()


def chk(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _FAIL.append(name)


# ── Part A — price formatting fixes the $0-$0 / $8-$8 collapse ──────────────
print("Part A — FVG/OB price bounds no longer collapse")
# Real live values from the 2026-06-04 prompts (MON ~0.0207, ADA ~0.18, AVAX ~8,
# BNB ~604). The pre-fix `.0f` rendered the sub-dollar/integer collapses.
cases = {
    0.0207: "MON FVG/OB bottom", 0.0219: "MON top", 0.180000: "ADA bound",
    0.111145: "ALICE OB", 8.05: "AVAX OB", 604.3: "BNB OB",
}
for v, label in cases.items():
    out = format_price(v)
    collapsed = out in ("0", "0.0", "0.00") or float(out) == 0.0
    chk(f"{label} ({v}) renders non-zero", not collapsed, f"-> {out}")
# Distinct endpoints must stay distinct (no $8-$8 / $604-$604 collapse).
chk("distinct sub-dollar endpoints stay distinct",
    format_price(0.0207) != format_price(0.0219))
chk("distinct mid-price endpoints stay distinct",
    format_price(8.05) != format_price(8.12))

# ── Part B — candidate-filter selects candidates, excludes the universe ─────
print("\nPart B — X-RAY block iterates the candidate set, not the universe")


class _FakeAnalysis:
    def __init__(self, symbol, score):
        self.symbol = symbol
        self.setup_score = score


class _FakeCache:
    def __init__(self, universe):
        self._u = universe

    def get(self, sym):
        return self._u.get(sym)

    def get_all(self):
        return dict(self._u)

    def get_top_setups(self, n=8):
        return sorted(self._u.values(), key=lambda a: a.setup_score, reverse=True)[:n]


# Universe: 5 candidates + 5 untradeable leak coins (higher scores, so the old
# get_top_setups(n=8) would have surfaced the leak coins over the candidates).
universe = {s: _FakeAnalysis(s, sc) for s, sc in [
    ("MONUSDT", 40), ("ALICEUSDT", 35), ("ARBUSDT", 30), ("ADAUSDT", 28), ("MNTUSDT", 25),
    ("LINKUSDT", 99), ("AEROUSDT", 98), ("ICPUSDT", 97), ("AXSUSDT", 96), ("HBARUSDT", 95),
]}
candidates = {"MONUSDT", "ALICEUSDT", "ARBUSDT", "ADAUSDT", "MNTUSDT"}
cache = _FakeCache(universe)

# Replicate the production selection expression exactly.
if candidates:
    top = [a for a in (cache.get(s) for s in candidates) if a is not None]
    top.sort(key=lambda a: a.setup_score, reverse=True)
else:
    top = cache.get_top_setups(n=8)
shown = {a.symbol for a in top}
chk("X-RAY block shows exactly the candidates", shown == candidates, f"shown={sorted(shown)}")
chk("no untradeable universe coin leaks in",
    not (shown & {"LINKUSDT", "AEROUSDT", "ICPUSDT", "AXSUSDT", "HBARUSDT"}))
chk("rows are ranked by setup_score (readability)",
    [a.symbol for a in top][0] == "MONUSDT")
# Cold-start fallback: empty candidates -> universe-top (block not blank).
empty: set = set()
top_fallback = ([a for a in (cache.get(s) for s in empty) if a is not None]
                if empty else cache.get_top_setups(n=8))
chk("cold-start fallback returns the universe-top (block never blank)",
    len(top_fallback) == 8)

# ── Part C — source guard (the live CALL_A block uses the fixed code) ───────
print("\nPart C — source guard on the live CALL_A X-RAY block")
chk("FVG bound uses format_price", "format_price(nf.bottom)" in SRC and "format_price(nf.top)" in SRC)
chk("OB bound uses format_price", "format_price(no.low)" in SRC and "format_price(no.high)" in SRC)
chk("no `.0f` on FVG/OB price bounds anywhere",
    not re.search(r"(bottom|top|low|high):\.0f", SRC))
chk("live block filters to _candidate_symbols", "_candidate_symbols" in SRC
    and "structure_cache.get(_s) for _s in _candidate_symbols" in SRC)
chk("XRAY_FILTERED observability present", "XRAY_FILTERED" in SRC)
# Isolate the LIVE block body (XRAY_FILTERED marker -> its CALL_A except) so the
# legacy _build_context_prompt block (test-only, kept) is not matched.
_live_seg = (
    SRC.split("XRAY_FILTERED", 1)[1].split("XRAY_CTX_BUILD_FAIL | call=CALL_A", 1)[0]
    if "XRAY_FILTERED" in SRC and "XRAY_CTX_BUILD_FAIL | call=CALL_A" in SRC else ""
)
chk("universe skip-coins leak removed from the LIVE block",
    bool(_live_seg) and "skip_coins" not in _live_seg)

print()
if _FAIL:
    print(f"RESULT: FAIL ({len(_FAIL)}): {_FAIL}")
    sys.exit(1)
print("RESULT: PASS — the X-RAY block shows exactly the candidates with")
print("magnitude-aware price formatting; the untradeable-universe leak and the")
print("$0-$0 collapse are gone; cold-start falls back safely.")
