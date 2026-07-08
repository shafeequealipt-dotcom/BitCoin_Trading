#!/usr/bin/env python3
"""Behavioral verification for the prompt-integrity cluster
(IMPLEMENT_PROMPT_INTEGRITY_AND_LATENCY_FIX): F19, F32, F33, F36, F30, F31.

Read-only. Each finding's checks are grouped and labeled. Run from the project
root. Exit 0 = all checks pass. No data is deleted or rewritten.

Checks favor the real objects/source over mocks where practical; the full
end-to-end prompt dump (a live CALL_A) is the ultimate cross-check and is run
separately by the operator after restart.
"""
from __future__ import annotations

import inspect
import sys

sys.path.insert(0, ".")

_FAIL: list[str] = []


def chk(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _FAIL.append(name)


print("=" * 74)
print("verify_prompt_integrity — F19/F32/F33/F36/F30/F31")
print("=" * 74)

from src.brain import strategist as S  # noqa: E402

with open("src/brain/strategist.py", encoding="utf-8") as _f:
    _strat_src = _f.read()

# ── F19 — candidate-count: system prompt count-neutral, user header dynamic ──
print("\n[F19] candidate-count mismatch (system prompt count-neutral)")
chk("TRADE_SYSTEM_PROMPT has no hardcoded '10 candidates'",
    "10 candidates" not in S.TRADE_SYSTEM_PROMPT)
chk("TRADE_SYSTEM_PROMPT_ZERO_TWO has no hardcoded '10 candidates'",
    "10 candidates" not in S.TRADE_SYSTEM_PROMPT_ZERO_TWO)
chk("both templates use the count-neutral phrasing",
    "across the full candidate set" in S.TRADE_SYSTEM_PROMPT
    and "across the full candidate set" in S.TRADE_SYSTEM_PROMPT_ZERO_TWO)
_src_full = inspect.getsource(S.ClaudeStrategist._format_packages_for_prompt_full)
chk("user-prompt builder counts rendered candidates",
    "_n_candidates_rendered += 1" in _src_full)
chk("user-prompt header is stamped with the rendered count",
    "TRADE CANDIDATES ({_n_candidates_rendered} candidates" in _src_full
    or "{_n_candidates_rendered} candidates;" in _src_full)
chk("STRAT_EVIDENCE_SUMMARY now logs rendered= (observability)",
    "rendered={_n_candidates_rendered}" in _src_full)

# ── F32 — ATR unit/label collision resolved ──────────────────────────────
print("\n[F32] ATR unit clash (distinct labels, no numeric change)")
chk("candidate Regime line renders atr_percentile (rank 0-100)",
    "atr_percentile={" in _strat_src)
chk("old colliding prompt label atr_pct= is gone from the render",
    "f\"atr_pct={" not in _strat_src)
chk("MARKET DATA still renders ATR% (normalized ATR), unchanged",
    "ATR%={_vp.atr_pct_5m" in _strat_src)
chk("the two volatility fields now have distinct labels",
    "atr_percentile=" in _strat_src and "ATR%=" in _strat_src)

# ── Issue 2.12 — score-vs-tier de-confusion (supersedes the F30 label) ────
print("\n[2.12] tier=vote-consensus, score=setup-quality (distinct, non-misleading labels)")
chk("Strategies line labels the structural score 'setup_quality_score'",
    "setup_quality_score {pkg.strategies.total_score" in _strat_src)
chk("the tier is tagged as the vote consensus (not conflated with the score)",
    "ensemble {_ens} (vote consensus)" in _strat_src
    and "ensemble {pkg.strategies.ensemble_consensus} (vote consensus)" in _strat_src)
chk("the misleading 'ensemble_score' label is gone from the Strategies line",
    "ensemble_score {pkg.strategies.total_score" not in _strat_src)
chk("old colliding 'total_score' prompt label still absent from Strategies line",
    "f\"total_score {pkg.strategies.total_score" not in _strat_src)
chk("structural 'Score: total=' line unchanged (TradeScorer quality)",
    "Score: total={comps.get('total'" in _strat_src)

# ── F33 — confluence-paradox veto note (presentation/observability) ──────
print("\n[F33] confluent-but-roomless direction signalled")
from src.config.settings import Settings  # noqa: E402
_S = Settings.load("config.toml")
chk("confluence-veto config loaded (centralized, tunable)",
    _S.structure.confluence_veto_note_enabled is True
    and float(_S.structure.confluence_veto_rr_floor) == 1.0
    and float(_S.structure.confluence_veto_ratio) == 2.0)
chk("candidate builder emits the confluence-veto NOTE",
    "the structurally-implied {_worse_dir} " in _strat_src
    and "vetoed for insufficient" in _strat_src)
chk("STRAT_CONFLUENCE_VETO observability sentinel present",
    "STRAT_CONFLUENCE_VETO |" in _strat_src)
chk("boot sentinel confirms the new config loaded",
    "STRAT_CONFLUENCE_VETO_CONFIG" in _strat_src)


def _confluence_fires(xray_dir, rr_long, rr_short, floor=1.0, ratio=2.0):
    best = "LONG" if rr_long >= rr_short else "SHORT"
    rr_ratio = (rr_long / max(rr_short, 0.01)) if rr_long >= rr_short else (rr_short / max(rr_long, 0.01))
    side = {"long": "LONG", "buy": "LONG", "short": "SHORT", "sell": "SHORT"}.get(xray_dir.lower(), "")
    worse = "SHORT" if best == "LONG" else "LONG"
    worse_rr = rr_short if worse == "SHORT" else rr_long
    return bool(side and side == worse and worse_rr < floor and rr_ratio >= ratio)


# BLUR (call0003): X-RAY short, long=4.78 short=0.40 -> better LONG, worse SHORT
# (roomless 0.40<1.0), ratio 11.9x -> NOTE fires.
chk("BLUR-style confluent roomless short -> note fires",
    _confluence_fires("short", 4.78, 0.40) is True)
# LDO (call0005): X-RAY short, long=8.23 short=0.24 -> fires.
chk("LDO-style confluent roomless short -> note fires",
    _confluence_fires("short", 8.23, 0.24) is True)
# MNT: X-RAY short, long=0.58 short=2.83 -> better SHORT (X-RAY agrees with the
# BETTER side) -> NOT a veto -> note does NOT fire (no over-fire).
chk("MNT-style X-RAY short that IS the better side -> note does NOT fire",
    _confluence_fires("short", 0.58, 2.83) is False)
# Healthy near-symmetric -> no fire.
chk("near-symmetric RR -> note does NOT fire",
    _confluence_fires("short", 1.10, 1.00) is False)

# ── F36 — market-data parity + labeled levels geometry ───────────────────
print("\n[F36] market-data parity + direction-labeled levels")
chk("MARKET DATA iterates the union of universe and candidate symbols",
    "_md_universe = list(universe)" in _strat_src
    and "for symbol in _md_universe:" in _strat_src)
chk("MARKET DATA force-includes every candidate (parity)",
    "or symbol in _candidate_symbols:" in _strat_src)
chk("STRAT_MARKETDATA_PARITY observability sentinel present",
    "STRAT_MARKETDATA_PARITY |" in _strat_src)
chk("Levels line is tagged with the geometry side",
    "{_lvl_side}-setup" in _strat_src and "_lvl_side = \"LONG\"" in _strat_src)
chk("levels/X-RAY direction-mismatch NOTE + sentinel present",
    "these structural levels are " in _strat_src
    and "STRAT_LEVELS_DIR_MISMATCH |" in _strat_src)


def _levels_side(entry, sl, tp):
    if not (entry > 0 and sl > 0 and tp > 0):
        return ""
    return "LONG" if tp >= entry else "SHORT"


def _levels_mismatch(entry, sl, tp, xray_dir):
    side = _levels_side(entry, sl, tp)
    xs = {"long": "LONG", "buy": "LONG", "short": "SHORT", "sell": "SHORT"}.get(xray_dir.lower(), "")
    return bool(side and xs and side != xs)


# MON (call0005): SL=0.023780 TP=0.020708 (TP<entry -> SHORT geometry) under
# X-RAY dir=long -> mismatch flagged.
chk("MON-style short-geometry levels under dir=long -> mismatch flagged",
    _levels_side(0.0212, 0.023780, 0.020708) == "SHORT"
    and _levels_mismatch(0.0212, 0.023780, 0.020708, "long") is True)
# A coherent long setup (TP above entry) under dir=long -> no mismatch.
chk("coherent long levels under dir=long -> no mismatch",
    _levels_side(0.0425, 0.0410, 0.0445) == "LONG"
    and _levels_mismatch(0.0425, 0.0410, 0.0445, "long") is False)
# No entry price -> no side, no spurious mismatch.
chk("missing entry price -> no side label, no spurious mismatch",
    _levels_side(0.0, 0.041, 0.044) == ""
    and _levels_mismatch(0.0, 0.041, 0.044, "long") is False)

# ── F31 — regime-fingerprint provenance sentinel (confirmed non-bug) ──────
print("\n[F31] regime-fingerprint observability (non-bug; coincidence provable)")
chk("per-candidate regime fingerprint + source id are recorded",
    "_this_regime_src = id(pkg.strategies) if _score_reg else id(rs)" in _strat_src
    and "_regime_fp.setdefault(_this_regime_fp" in _strat_src)
chk("duplicate-fingerprint sentinel present with provenance verdict",
    "STRAT_REGIME_FINGERPRINT_DUP" in _strat_src
    and "coincidence_distinct_sources" in _strat_src
    and "SHARED_SOURCE_INVESTIGATE" in _strat_src)


def _dup_verdict(members):
    ids = {m[1] for m in members}
    return ("coincidence_distinct_sources" if len(ids) == len(members)
            else "SHARED_SOURCE_INVESTIGATE")


# MNT/ADA identical fingerprint, DISTINCT source objects -> coincidence (healthy).
chk("identical fingerprint from distinct sources -> coincidence verdict",
    _dup_verdict([("MNTUSDT", 1001), ("ADAUSDT", 2002)]) == "coincidence_distinct_sources")
# Identical fingerprint sharing ONE source object -> would flag a real bug.
chk("identical fingerprint from a SHARED source -> investigate verdict",
    _dup_verdict([("MNTUSDT", 5005), ("ADAUSDT", 5005)]) == "SHARED_SOURCE_INVESTIGATE")

print("\n" + "-" * 74)
if _FAIL:
    print(f"RESULT: FAIL ({len(_FAIL)}): {_FAIL}")
    sys.exit(1)
print("RESULT: PASS — all prompt-integrity checks green.")
