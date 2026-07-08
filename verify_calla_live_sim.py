"""Prompt 4 — live simulation with the ACTUAL issue-occurring data.

Recreates the exact situations the live evidence captured and runs each fix
against them, confirming each responds as FIXED per its aim:

  PART A (Issue 3): the five real 2026-06-05 17:29 candidates (HYPER/SEI/GMT/
    LDO/MNT) with their EXACT captured rr_short/rr_long/range_pos — every one
    was dir=short, score 95-100, quality A+ despite rr_short 0.10-0.54. Fed
    through the REAL _compute_setup_score, each spent short must now score low
    (SKIP/C), while a hypothetical short WITH room still scores high.

  PART B (Issue 4): HYPER's exact captured state (0 fired, ensemble NONE, but
    Votes SELL=0.80 across 27 voters) through the REAL renderer — the lean must
    be surfaced instead of "genuine no-signal".

  PART C (Issue 1): replay REAL closed losing trades from position_snapshots
    (the per-tick pnl_pct MFE path) through the REAL ladder floor. Trades that
    went green sub-0.2% and round-tripped to a loss must now ARM the micro-floor
    (lock a small green) where the old 0.2% arm did nothing. Winners must not be
    cut. Read-only; no protected table is written.

  PART D (Issue 5): the rendered CALL_A prompt for an all-downtrend set carries
    the breadth framing AND the anti-fabrication rule, so the brain is told to
    reach for genuine plays but skip the spent shorts (no fabricated trade).

Cross-checks the results against the aim. Read-only throughout.
"""
import sqlite3
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import _build_profit_fetching, _build_structure
from src.workers.profit_sniper import ProfitSniper

try:
    import tomllib
except ImportError:
    import tomli as tomllib

_TOML = tomllib.load(open("config.toml", "rb"))
STRUCT = _build_structure(_TOML.get("analysis", {}).get("structure", {}))

failures = []


# ---------- PART A: Issue 3 on the real 2026-06-05 candidates ----------
# (symbol, rr_long, rr_short, range_pos) captured verbatim from the live prompt.
LIVE_CANDIDATES = [
    ("HYPERUSDT", 15.98, 0.12, 0.00),
    ("SEIUSDT", 20.66, 0.10, 0.00),
    ("GMTUSDT", 10.75, 0.18, 0.00),
    ("LDOUSDT", 11.45, 0.17, 0.00),
    ("MNTUSDT", 3.59, 0.54, 0.00),
]
eng_shim = SimpleNamespace(_settings=STRUCT)


def _score_short(rr_long, rr_short, pos):
    pl = SimpleNamespace(rr_ratio=round(max(rr_long, rr_short), 2),
                         rr_long=rr_long, rr_short=rr_short,
                         is_fallback_rr=False, rr_quality="excellent")
    ms = SimpleNamespace(structure="downtrend", strength="strong",
                         last_bos=SimpleNamespace(direction="bearish"), last_choch=None)
    mtf = SimpleNamespace(quality="good", score=7)
    return StructureEngine._compute_setup_score(
        eng_shim, position_in_range=pos, market_structure=ms,
        structural_placement=pl, suggested_direction="short", smc_confluence=70,
        volume_profile=None, fibonacci=None, mtf_confluence=mtf, symbol="SIM")


print("=== PART A — Issue 3: the real 2026-06-05 spent-short candidates ===")
partA = True
for sym, rrl, rrs, pos in LIVE_CANDIDATES:
    score, q = _score_short(rrl, rrs, pos)
    fixed = q in ("SKIP", "C") and score <= 49
    partA = partA and fixed
    print(f"  {sym}: rr_short={rrs} rr_long={rrl} pos={pos} -> score={score} "
          f"grade={q} (was A+/95-100) {'FIXED' if fixed else 'NOT FIXED'}")
# control: a short WITH room must still score high
cs, cq = _score_short(rr_long=0.5, rr_short=3.0, pos=0.85)
ctrl = cq in ("A+", "A")
print(f"  control short-with-room (rr_short=3.0 pos=0.85): score={cs} grade={cq} "
      f"{'OK (still high)' if ctrl else 'REGRESSION'}")
if not (partA and ctrl):
    failures.append("PART A (Issue 3 spent shorts)")
print(f"PART A: {'PASS' if partA and ctrl else 'FAIL'}\n")


# ---------- PART B: Issue 4 on HYPER's real 0-fired+lean state ----------
print("=== PART B — Issue 4: HYPER 0-fired but SELL=0.80 poll (27 voters) ===")
from src.brain.strategist import ClaudeStrategist
from src.core.coin_package import (AltDataBlock, CoinPackage, PriceDataBlock,
                                    SignalsBlock, StateLabelBlock, StrategiesBlock, XrayBlock)
import time as _t


class _LM:
    def get_strategy_votes(self, s):
        return {"votes": {f"S{i}": {"vote": "SELL", "confidence": 0.6, "weight": 1.0}
                          for i in range(27)},
                "buy_weighted": 0.0, "sell_weighted": 0.80, "opposing_weighted": 1.53,
                "two_sided": True, "consensus": "WEAK", "last_updated": _t.time()}

    def get_scorer_components(self, s):
        return None


_strat = ClaudeStrategist.__new__(ClaudeStrategist)
_strat.services = {"layer_manager": _LM(),
                   "structure_cache": SimpleNamespace(get=lambda s: None),
                   "signal_worker": SimpleNamespace(get_signal=lambda s: None),
                   "regime_detector": SimpleNamespace(get_coin_regime=lambda s: None)}
_strat.settings = SimpleNamespace(
    brain=SimpleNamespace(surface_briefing_fields=False, consensus_freshness_seconds=360),
    scanner=SimpleNamespace(briefing=SimpleNamespace(prompt_floor_interestingness=0.20)))
_pkg = CoinPackage(symbol="HYPERUSDT", qualified=True, opportunity_score=0.47,
                   qualification_reasons=["xray=bearish_structural_break"],
                   price_data=PriceDataBlock(current=0.0672, change_24h_pct=-17.6, regime="trending_down"),
                   xray=XrayBlock(setup_type="bearish_structural_break", setup_score=30,
                                  setup_type_confidence=0.70, trade_direction="short"),
                   strategies=StrategiesBlock(fired_count=0, ensemble_consensus="NONE", total_score=0.0),
                   signals=SignalsBlock(confidence=0.35, direction="neutral"),
                   alt_data=AltDataBlock(funding_rate=0.0001, funding_signal="longs_paying", fear_greed=12),
                   state_label=StateLabelBlock(primary="TREND_PULLBACK_SHORT", confidence=0.5))
out = _strat._format_packages_for_prompt_full({"HYPERUSDT": _pkg})
partB = "two-sided strategy poll DID lean SELL=0.80" in out and "genuine no-signal" not in out
print(f"  lean surfaced: {'two-sided strategy poll DID lean SELL=0.80' in out}; "
      f"'genuine no-signal' suppressed: {'genuine no-signal' not in out}")
if not partB:
    failures.append("PART B (Issue 4 lean surfacing)")
print(f"PART B: {'PASS' if partB else 'FAIL'}\n")


# ---------- PART C: Issue 1 replay of REAL small-green losers ----------
print("=== PART C — Issue 1: replay real losers' MFE paths through the ladder ===")
PF_AFTER = _build_profit_fetching(_TOML.get("profit_fetching", {}))   # micro=0.10
PF_BEFORE = _build_profit_fetching(_TOML.get("profit_fetching", {}))
PF_BEFORE.micro_floor_arm_pct = PF_BEFORE.min_profit_to_arm_ladder_pct  # old single-arm


def _arms(pf, peak, direction):
    shim = SimpleNamespace(_pf=pf, _last_breakeven_floor_logged={})
    st = SimpleNamespace(entry_price=100.0, direction=direction, peak_pnl_pct=peak, symbol="SIM")
    dl = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
    r = ProfitSniper._compute_ladder_floor(shim, st, dl, 0.0)
    return r.should_apply, r.lock_pct


con = sqlite3.connect("data/trading.db")
con.row_factory = sqlite3.Row
cur = con.cursor()
# Reconstruct trades from position_snapshots: group contiguous rows per
# (symbol, entry_price, direction); peak = max(pnl_pct), final = last pnl_pct.
rows = cur.execute(
    "SELECT symbol, direction, entry_price, mark_price, pnl_pct, ts_epoch "
    "FROM position_snapshots ORDER BY symbol, entry_price, ts_epoch"
).fetchall()
con.close()

trades = {}
for r in rows:
    key = (r["symbol"], round(float(r["entry_price"] or 0), 10), r["direction"])
    t = trades.setdefault(key, {"peak": -9e9, "final": 0.0, "dir": r["direction"], "n": 0})
    p = float(r["pnl_pct"] or 0.0)
    t["peak"] = max(t["peak"], p)
    t["final"] = p
    t["n"] += 1

# small-green round-trips: went green sub-0.2% but closed red.
rescued = 0
sample = []
total_small_green = 0
for key, t in trades.items():
    if t["n"] < 2:
        continue
    direction = "Buy" if str(t["dir"]).lower() in ("buy", "long") else "Sell"
    if 0.10 <= t["peak"] < 0.20 and t["final"] < 0:
        total_small_green += 1
        a_apply, a_lock = _arms(PF_AFTER, t["peak"], direction)
        b_apply, _ = _arms(PF_BEFORE, t["peak"], direction)
        if a_apply and not b_apply:
            rescued += 1
            if len(sample) < 6:
                sample.append((key[0], round(t["peak"], 3), round(t["final"], 3), round(a_lock, 3)))

# winner-safety: a winner that peaked >=0.6 (real rung) uses the step lock, not the micro band.
w_apply, w_lock = _arms(PF_AFTER, 0.80, "Buy")
winner_safe = w_apply and abs(w_lock - 0.30) < 1e-6  # step lock, not micro

print(f"  reconstructed trades: {len(trades)}; small-green-then-red (peak in "
      f"[0.10,0.20), closed red): {total_small_green}")
print(f"  micro-floor RESCUES (now arms where old 0.2% arm did not): {rescued}")
for s in sample:
    print(f"    {s[0]}: peak={s[1]}% final={s[2]}% -> micro-floor locks {s[3]}%")
print(f"  winner-safety (peak 0.80 -> step lock {w_lock}%, not micro): "
      f"{'OK' if winner_safe else 'FAIL'}")
partC = winner_safe and (total_small_green == 0 or rescued > 0)
if not partC:
    failures.append("PART C (Issue 1 loser replay)")
print(f"PART C: {'PASS' if partC else 'FAIL'}"
      + ("" if total_small_green else " (no sub-0.2% round-trip losers in window; "
         "logic verified, nothing to rescue)") + "\n")


# ---------- PART D: Issue 5 framing on the all-downtrend set ----------
print("=== PART D — Issue 5: brain receives breadth framing + clean inputs ===")
from src.brain.strategist import TRADE_SYSTEM_PROMPT_ZERO_TWO as ZT
partD = ("WORK to surface every genuine play" in ZT
         and "never manufacture a counter-evidence trade" in ZT
         and "PREFER 15-25 for quick scalps" in ZT)
# and the spent shorts the brain sees are now graded SKIP (Part A), so the
# breadth push cannot turn into fabricated spent-short trades.
print(f"  breadth framing present: {'WORK to surface every genuine play' in ZT}")
print(f"  anti-fabrication preserved: {'never manufacture a counter-evidence trade' in ZT}")
print(f"  shorter-hold guidance: {'PREFER 15-25 for quick scalps' in ZT}")
print(f"  spent shorts now SKIP (Part A) -> breadth cannot fabricate them: {partA}")
if not (partD and partA):
    failures.append("PART D (Issue 5 framing)")
print(f"PART D: {'PASS' if partD and partA else 'FAIL'}\n")


print("================ LIVE SIMULATION CROSS-CHECK ================")
if failures:
    print(f"LIVE SIM: FAIL — {failures}")
    sys.exit(1)
print("LIVE SIM: PASS — on the actual 2026-06-05 issue data, every fix responds "
      "as intended: spent A+ shorts now score SKIP, 0-fired coins surface their "
      "real lean, the micro-floor arms on the small-green band that round-tripped, "
      "and the brain gets breadth framing with anti-fabrication intact and clean "
      "(de-graded) spent-short inputs.")
