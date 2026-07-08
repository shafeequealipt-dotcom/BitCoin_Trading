"""Failure-scenario simulation for the 11 fixes (read-only, no DB writes).

For each fix this rebuilds the REAL failure SITUATION from the live logs and
runs the SAME real object twice:

  * WITHOUT the fix (the fix's own off-switch flipped to the pre-fix value) —
    must REPRODUCE the original failure, and
  * WITH the fix (the shipped config) — must achieve the fix's stated AIM.

A fix is only reported FIXED when the off branch reproduces the failure AND the
on branch flips the outcome to the aim. Issues 4 and 5 have no off-switch (they
were verification-only — already correct by design); for those the harness
asserts the safe invariant holds on the real object and labels it VERIFIED.

This is a simulation/verification harness (project-root, per the program rules).
It builds real objects from the real config (Settings.load()), drives recreated
failure data through them, and never writes to the database or mutates a running
process.

Run:  .venv/bin/python simulate_failure_scenarios.py
"""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config.settings import Settings

S = Settings.load()

# (name, status, situation, without_fix, with_fix, aim)
#   status: "FIXED" (off reproduces bug + on meets aim) | "VERIFIED" (invariant)
RESULTS: list[dict] = []


def record(name, ok, situation, without_fix, with_fix, aim, kind="FIXED"):
    RESULTS.append(dict(name=name, ok=ok, situation=situation,
                        without_fix=without_fix, with_fix=with_fix,
                        aim=aim, kind=kind))


def run(name, fn):
    try:
        fn()
    except Exception as e:  # surface construction/wiring failures honestly
        import traceback
        record(name, False, "(scenario raised)", f"EXCEPTION: {e}",
               traceback.format_exc()[-700:], "n/a", kind="ERROR")


class _FakePS:
    """Position service whose set_stop_loss succeeds, so SLGateway.apply can
    complete the wire step and the resulting stop is observable (no exchange)."""
    async def set_stop_loss(self, symbol, new_sl):
        return True


# ════════════════════════════════════════════════════════════════════════
# PROGRAM 1 — profit/loss calibration (4 code fixes)
# ════════════════════════════════════════════════════════════════════════

def s_phase1():
    """Phase 1 — PnL reconciler exit-plausibility gate.

    Real failure: a NEAR close was reconciled against a STALE exchange row whose
    exit price (2.3379) was far from the real fill (~2.07), booking a phantom
    PnL. Off = no plausibility gate (pre-fix); On = gate rejects the out-of-band
    exit and keeps the provisional (true) booking, while still accepting a tiny
    fee-flip divergence.
    """
    from src.core.trade_coordinator import TradeCoordinator
    from src.workers.pnl_reconciler import PnLReconciler
    recon = PnLReconciler(S, db=None,
                          services={"trade_coordinator": TradeCoordinator()})

    ref_exit, phantom_exit = 2.07, 2.3379        # real fill vs stale-row exit
    feeflip_ref, feeflip_exit = 1685.0, 1685.97  # genuine same-fill fee flip

    # WITHOUT fix: an effectively-infinite divergence tolerance = no gate.
    recon._max_exit_div_pct = 1e9
    bug_phantom = recon._exit_implausible(ref_exit, phantom_exit)  # False=booked
    # WITH fix: the shipped tolerance.
    recon._max_exit_div_pct = S.bybit_demo.close_pnl_reconcile_max_exit_divergence_pct
    fix_phantom = recon._exit_implausible(ref_exit, phantom_exit)  # True=rejected
    fix_feeflip = recon._exit_implausible(feeflip_ref, feeflip_exit)  # False=ok

    ok = (bug_phantom is False) and (fix_phantom is True) and (fix_feeflip is False)
    record(
        "Phase 1: PnL reconciler exit-plausibility gate", ok,
        "NEAR close reconciled to a stale row (exit 2.3379 vs real fill 2.07) "
        "-> phantom PnL booked",
        "no gate -> phantom exit treated as plausible -> phantom PnL booked "
        f"(implausible={bug_phantom})",
        f"gate (tol {recon._max_exit_div_pct}%) -> phantom REJECTED "
        f"(implausible={fix_phantom}); genuine fee-flip 1685->1685.97 still "
        f"ACCEPTED (implausible={fix_feeflip})",
        "reject out-of-band exchange exits, keep the true provisional booking; "
        "never reject a genuine fee-only divergence",
    )


def s_finding_h():
    """Finding H — un-throttle the Chandelier runner trail (R3-bypass).

    Real failure: a winner's trail wanted to ratchet to 63.5 but the R3
    per-step cap (~0.25%) clamped it to ~62.66, so the runner gave back profit.
    Off = trail source NOT in the bypass set (clamped like any source); On =
    profit_sniper_trail is allow-listed so the trail rides to the requested 63.5.
    """
    from src.core.sl_gateway import SLGateway

    async def trail(source, bypass_set=None):
        g = SLGateway(S, _FakePS(), None)
        if bypass_set is not None:
            g._BREAKEVEN_BYPASS_SOURCES = bypass_set
        return await g.apply(
            symbol="AAVEUSDT", new_sl=63.5, source=source, direction="Buy",
            current_sl=62.5, current_price=64.0,
            bypass_step_cap_for_breakeven=True,
        )

    base = SLGateway(S, _FakePS(), None)._BREAKEVEN_BYPASS_SOURCES
    # WITHOUT fix: pre-fix bypass set with the trail source removed.
    bug_set = frozenset(base) - {"profit_sniper_trail"}
    r_bug = asyncio.run(trail("profit_sniper_trail", bug_set))
    # WITH fix: shipped allow-list.
    r_fix = asyncio.run(trail("profit_sniper_trail"))

    bug_sl = r_bug.new_sl_applied
    fix_sl = r_fix.new_sl_applied
    ok = (
        "profit_sniper_trail" in base
        and fix_sl is not None and abs(fix_sl - 63.5) < 1e-6
        and (bug_sl is None or bug_sl < fix_sl)
    )
    record(
        "Finding H: trail R3-bypass (real SLGateway.apply)", ok,
        "AAVE winner trail wanted 63.5 but R3 per-step cap (~0.25%) clamped it "
        "to ~62.66 -> runner gave back profit",
        f"trail source NOT allow-listed -> R3 clamps the step -> stop={bug_sl} "
        "(throttled, below the wanted 63.5)",
        f"profit_sniper_trail allow-listed -> trail rides to stop={fix_sl} "
        "(full ratchet, R3 overridden for the trail only)",
        "let the genuine runner trail ratchet without the per-step throttle, "
        "while every other source stays R3-capped",
    )


def s_finding_a():
    """Finding A — make the ladder breakeven/first-lock fee-aware.

    Real failure: NEAR's ladder locked a gross +0.035% 'breakeven' that did not
    clear the 0.11% round-trip fee, so the 'breakeven' lock booked a NET loss
    (-$4.57). Off = fee_clearance 0 (sub-fee lock stands); On = lift the floor
    to the fee-clearing level so a breakeven lock is net-breakeven-or-better.
    """
    from src.workers.profit_sniper import ProfitSniper

    def lock(fee_clear):
        sn = ProfitSniper.__new__(ProfitSniper)
        pf = copy.copy(S.profit_fetching)
        pf.ladder_lock_fee_clearance_pct = fee_clear
        sn._pf = pf
        sn._lc = S.loss_cutting
        sn._last_breakeven_floor_logged = {}
        state = SimpleNamespace(entry_price=100.0, direction="Buy",
                                peak_pnl_pct=0.18, symbol="NEARUSDT")
        dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
        return sn._compute_ladder_floor(state, dialed, 0.0)

    fee_pct = S.loss_cutting.cap_round_trip_fee_pct      # 0.11% round trip
    notional = 1000.0
    r_bug = lock(0.0)                                    # off-switch
    r_fix = lock(S.profit_fetching.ladder_lock_fee_clearance_pct)  # 0.13

    bug_net = (r_bug.lock_pct - fee_pct) / 100.0 * notional   # $ net at the lock
    fix_net = (r_fix.lock_pct - fee_pct) / 100.0 * notional
    ok = (r_bug.lock_pct < fee_pct) and (bug_net < 0) and \
         (r_fix.lock_pct >= fee_pct) and (fix_net >= 0)
    record(
        "Finding A: fee-aware ladder lock (real ProfitSniper)", ok,
        "NEAR ladder locked a gross +0.035% 'breakeven' below the 0.11% fee -> "
        "the lock booked a NET loss (-$4.57)",
        f"fee_clearance off -> lock {r_bug.lock_pct:.3f}% < fee {fee_pct}% -> "
        f"net at lock = ${bug_net:+.2f} on ${notional:.0f} (a 'breakeven' that "
        "loses the fee)",
        f"fee_clearance {S.profit_fetching.ladder_lock_fee_clearance_pct}% -> "
        f"lock raised to {r_fix.lock_pct:.3f}% (clears fee) -> net at lock = "
        f"${fix_net:+.2f} (net-breakeven-or-better)",
        "a breakeven/first lock must clear the round-trip fee so it is "
        "net-positive, not a quiet net loss",
    )


def s_finding_n():
    """Finding N — bound the hard cap on NET loss (subtract the round-trip fee).

    Real failure: the -$75 hard cap was applied to GROSS PnL, so after the
    0.11% round-trip fee the realized NET loss overshot the intended ceiling.
    Off = fee 0 (gross cap); On = cap budget = ceiling - round-trip fee, so the
    force-close fires earlier and the realized NET loss is bounded at the intent.
    """
    from src.workers.profit_sniper import ProfitSniper

    def net_cap(fee_pct):
        sn = ProfitSniper.__new__(ProfitSniper)
        lc = copy.copy(S.loss_cutting)
        lc.cap_round_trip_fee_pct = fee_pct
        sn._lc = lc
        return sn._lc_net_cap_dollars(75.0, 5996.0)

    notional = 5996.0
    fee_dollars = notional * S.loss_cutting.cap_round_trip_fee_pct / 100.0
    bug_budget = net_cap(0.0)                                  # gross cap = 75
    fix_budget = net_cap(S.loss_cutting.cap_round_trip_fee_pct)  # 75 - fee

    # Realized NET loss = the gross budget that triggers the close + the fee.
    bug_realized_net = bug_budget + fee_dollars   # overshoots the $75 intent
    fix_realized_net = fix_budget + fee_dollars   # bounded at the $75 intent
    ok = (abs(bug_budget - 75.0) < 1e-6 and bug_realized_net > 75.0 + 1e-6
          and abs(fix_realized_net - 75.0) < 1e-2)
    record(
        "Finding N: net-aware hard cap (real ProfitSniper)", ok,
        "the -$75 hard cap was applied to GROSS PnL on a $5996 notional -> after "
        "the 0.11% round-trip fee the realized NET loss overshot -$75",
        f"gross cap -> force-close budget ${bug_budget:.2f} -> realized NET "
        f"loss ${bug_realized_net:.2f} (overshoots the $75 ceiling by "
        f"${fee_dollars:.2f})",
        f"net-aware cap -> budget ${fix_budget:.2f} (=75 - fee {fee_dollars:.2f}) "
        f"-> realized NET loss ${fix_realized_net:.2f} (bounded at the $75 intent)",
        "the realized NET loss must be bounded by the dollar ceiling, not "
        "overshot by the round-trip fee",
    )


# ════════════════════════════════════════════════════════════════════════
# PROGRAM 2 — signal pipeline, entry, portfolio (7 issues)
# ════════════════════════════════════════════════════════════════════════

def s_issue1():
    """Issue 1 — make Fear-and-Greed direction-neutral in the classifier.

    Real failure: in the extreme-fear window the contrarian F&G term ((50-fg))
    was the sole/dominant active component, pinning ~100% of coins to BUY with
    ZERO sells — a one-directional contrarian-long book. Off = F&G drives
    direction; On = F&G excluded from direction so each coin's own funding/OI
    decides (a mix incl. real sells), and coins with no other signal abstain.
    """
    from src.intelligence.signals.signal_generator import SignalGenerator
    from src.core.types import SignalType

    # Recreated window: every coin in extreme fear (fg 10-30); funding spans
    # long-favoring (negative), short-favoring (positive) and sub-threshold.
    coins = [
        ("BTCUSDT", 12, +0.012), ("ETHUSDT", 15, -0.010), ("SOLUSDT", 18, +0.008),
        ("ADAUSDT", 20, +0.0001), ("XRPUSDT", 14, -0.009), ("AVAXUSDT", 22, +0.011),
        ("LINKUSDT", 16, -0.0002), ("DOGEUSDT", 25, +0.007), ("NEARUSDT", 11, +0.0003),
        ("ATOMUSDT", 19, -0.012), ("DOTUSDT", 23, +0.009), ("MATICUSDT", 17, +0.00005),
    ]

    def classify(neutral):
        sg = SignalGenerator.__new__(SignalGenerator)
        ms = copy.copy(S.signal_generator.multi_source)
        ms.fg_direction_neutral = neutral
        sg._ms_cfg = ms
        sg._sentiment_consumption_enabled = False
        mix = {"buy": 0, "sell": 0, "neutral": 0}
        for sym, fg, fund in coins:
            st, _ = sg._evaluate_signal(sentiment=0.0, fear_greed=fg,
                                        funding_rate=fund, oi_change=0.0, symbol=sym)
            if st in (SignalType.BUY, SignalType.STRONG_BUY):
                mix["buy"] += 1
            elif st in (SignalType.SELL, SignalType.STRONG_SELL):
                mix["sell"] += 1
            else:
                mix["neutral"] += 1
        return mix

    bug = classify(False)   # off-switch: F&G drives direction
    fix = classify(True)    # shipped: F&G neutral

    # Off must be one-directional (zero sells); On must surface real sells and
    # be a genuine mix (not all one class, not a flip to all-sell).
    ok = (bug["sell"] == 0 and bug["buy"] > 0
          and fix["sell"] > 0 and fix["buy"] > 0
          and fix["sell"] < len(coins))
    record(
        "Issue 1: Fear-and-Greed direction-neutral (real classifier)", ok,
        "extreme-fear window: contrarian F&G was the sole active component -> "
        "~100% BUY, ZERO sells (one-directional contrarian-long book)",
        f"F&G drives direction -> mix buy={bug['buy']} sell={bug['sell']} "
        f"neutral={bug['neutral']} (zero sells = one-directional)",
        f"F&G neutral -> mix buy={fix['buy']} sell={fix['sell']} "
        f"neutral={fix['neutral']} (per-coin funding decides; real sells appear; "
        "no-signal coins abstain)",
        "remove the F&G buy-pin so direction comes from each coin's own "
        "evidence -> a two-sided mix, not 100% buy and not a flip to all-sell",
    )


def s_issue2():
    """Issue 2 — handle a true UNKNOWN sentiment as missing, not a dragging zero.

    Real failure: sentiment is structurally absent (Reddit off, Finnhub has no
    altcoin coverage) -> aggregator returns level UNKNOWN. The confidence path
    fed that as 0.0, dragging the magnitude down. Off = feed 0.0; On = pass None
    so the confidence calculator excludes the missing component.
    """
    from src.intelligence.signals.confidence import ConfidenceCalculator
    c = ConfidenceCalculator()
    base = dict(fear_greed=1.0, funding_rate=0.5, open_interest=0.5,
                data_age_hours=1.0, volume_surge_ratio=1.0)
    bug = c.calculate(dict(base, news_sentiment=0.0, reddit_sentiment=0.0))
    fix = c.calculate(dict(base, news_sentiment=None, reddit_sentiment=None))
    ok = fix > bug
    record(
        "Issue 2: UNKNOWN sentiment as missing-input (real Confidence)", ok,
        "sentiment structurally UNKNOWN (Reddit off, Finnhub no altcoins) but a "
        "0.0 was fed into confidence, dragging the magnitude",
        f"UNKNOWN fed as 0.0 -> confidence {bug:.3f} (the absent source pulls "
        "the score down)",
        f"UNKNOWN passed as None -> confidence {fix:.3f} (the missing source is "
        "excluded, not counted as a zero)",
        "a genuinely-absent sentiment must not lower conviction; exclude it "
        "rather than scoring it as bearish-zero",
    )


def s_issue3():
    """Issue 3 — tighten the time-decay stop on a strong MAE recovery.

    Real failure: the MAE monotonic hold pinned the stop at the worst excursion;
    as the trade recovered, the tighter-only guard kept the loss budget wide, so
    a recovered trade still risked the full dip. Off = recovery-tighten disabled
    (stop stays wide); On = on a strong recovery tighten toward the recovered
    level (a tight bounce-capture) while never cutting a still-running recovery.
    """
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator

    def calc_branch(enabled):
        td = S.time_decay
        cfg = TimeDecayConfig(
            mae_recovery_tighten_enabled=enabled,
            mae_tightening_recovery_threshold=td.mae_tightening_recovery_threshold,
            recovery_tightening_buffer_pct=td.recovery_tightening_buffer_pct,
        )
        calc = TimeDecaySLCalculator(cfg)
        st = calc.create_state(
            symbol="RECOV", direction="Buy", entry_price=100.0, original_sl_pct=2.0,
            max_hold_seconds=2700, atr_5m_pct=0.5, regime_confidence=0.6,
            tick_seconds=5.0, entry_xray_confidence=0.65,
            entry_setup_type="BULLISH_FVG_OB", entry_regime_at_open="trending_up",
            entry_regime_confidence=0.70,
        )
        # worst dip -1.09%, now recovered to -0.2% (recovery ratio ~0.82),
        # a wide 1.5% budget already set during the dip.
        st.p_win = 0.9
        st.mae_pct = -1.09
        st.last_allowed_loss = 1.5
        st.last_pnl_pct = -0.2
        out = calc.calculate(
            st, current_pnl_pct=-0.2, position_age_seconds=400,
            regime_still_supports=True, velocity_pct_per_s=0.02,
            acceleration_pct_per_s2=0.005, structural_invalidation=False,
            invalidation_reason="stable",
        )
        return out, st.last_allowed_loss

    bug_sl, bug_budget = calc_branch(False)   # off-switch -> no tighten
    fix_sl, fix_budget = calc_branch(True)    # shipped -> tighten on recovery

    # Off: budget stays wide (>=1.5%, no tighten). On: budget tightens (<=0.5%)
    # and the stop ratchets up toward the recovered price.
    ok = (bug_budget >= 1.5 - 1e-9 and (bug_sl is None or bug_sl <= 98.5 + 1e-6)
          and fix_budget <= 0.5 + 1e-9 and fix_sl is not None and fix_sl > 99.0)
    record(
        "Issue 3: MAE recovery-tighten (real TimeDecay calc)", ok,
        "trade dipped to -1.09% then recovered to -0.2%; the monotonic MAE hold "
        "pinned the stop at the worst dip, keeping the full loss budget exposed",
        f"recovery-tighten off -> budget stays {bug_budget:.2f}% wide, "
        f"stop={bug_sl} (still risking the whole dip)",
        f"recovery-tighten on -> budget tightens to {fix_budget:.2f}%, "
        f"stop ratchets up to {fix_sl:.3f} (captures the bounce near least loss)",
        "tighten the stop toward the recovered level on a strong recovery, "
        "without strangling a moderate recovery that needs room",
    )


def s_issue4():
    """Issue 4 — only the volatility-spike catastrophe stop bypasses the
    age-guard (verification-only — already the behavior).

    Real situation: SNIPER_AGE_GUARD (300s) was the most frequent event; the
    concern was whether a violent young crash would be held back by the guard.
    Verification has two halves, both driven on the REAL code:
      (a) DETECTOR: drive the real ProfitSniper._lc_spike_triggered on a young
          position — a violent young crash fires (age-independent), a healthy
          young wiggle does NOT, and the opening-seconds carve-out widens the
          tolerance for a very-young settling trade while a genuine crash still
          fires inside it.
      (b) ROUTING: the spine evaluates the spike branch BEFORE the graduation
          gate and closes with check_min_hold=False, so the fired close is age-
          and graduation-independent.
    """
    import inspect
    import time
    from src.workers.profit_sniper import ProfitSniper
    from src.workers.sniper_ring_buffer import EnhancedRingBuffer, BufferPoint

    # ── (a) drive the REAL detector ──────────────────────────────────────
    async def detect(age_seconds, current_price, recent_high=100.0, atr=1.0):
        sn = ProfitSniper.__new__(ProfitSniper)
        sn._lc = S.loss_cutting

        async def _atr(_sym):           # known live ATR -> _pf_effective_atr="live"
            return atr
        sn._get_current_atr = _atr
        buf = EnhancedRingBuffer("CRASHUSDT")
        now = time.time()
        for dt in (15.0, 10.0, 5.0):    # recent in-window highs before the drop
            buf.add_point(BufferPoint(timestamp=now - dt, price=recent_high))
        tracked = {"buffer": buf}
        state = SimpleNamespace(atr_at_entry=atr, age_seconds=age_seconds)
        return await sn._lc_spike_triggered(
            "CRASHUSDT", tracked, state, current_price, is_long=True)

    # young (60s, past the 12s carve-out -> 2.5x mult): a 5-ATR crash vs a wiggle
    young_crash = asyncio.run(detect(60.0, 95.0))     # adverse 5.0 >= 2.5*1.0
    young_wiggle = asyncio.run(detect(60.0, 99.8))    # adverse 0.2 < 2.5*1.0
    # very young (5s, < 12s carve-out -> 3.8x mult): genuine crash still fires;
    # a moderate settling wiggle (3 ATR) is NOT misread as a crash at this age.
    veryyoung_crash = asyncio.run(detect(5.0, 95.0))  # adverse 5.0 >= 3.8*1.0
    veryyoung_settle = asyncio.run(detect(5.0, 97.0))  # adverse 3.0 < 3.8*1.0

    detector_ok = (young_crash[0] is True and young_wiggle[0] is False
                   and veryyoung_crash[0] is True and veryyoung_settle[0] is False)

    # ── (b) confirm the routing is age/graduation-independent ────────────
    src = inspect.getsource(ProfitSniper._pf_apply_spine)
    spike_idx = src.find("LOSS_SPIKE_STOP")
    grad_gate_idx = src.find("if not _graduated")
    spike_before_grad = 0 <= spike_idx < grad_gate_idx
    spike_min_hold_exempt = "check_min_hold=False" in \
        src[spike_idx: src.find("_execute_full_close", spike_idx) + 400]
    spike_enabled = bool(S.loss_cutting.enable_spike_stop)

    ok = (spike_enabled and detector_ok and spike_before_grad
          and spike_min_hold_exempt)
    record(
        "Issue 4: spike-stop bypasses age-guard (verification)", ok,
        "SNIPER_AGE_GUARD (300s) was the most frequent event; a violent young "
        "crash must still be cut immediately",
        "n/a (no off-switch — the age-guard gates the stall/time-decay cutters, "
        "never the spike)",
        f"DETECTOR (real _lc_spike_triggered): young 60s 5-ATR crash fires="
        f"{young_crash[0]} (adverse {young_crash[1]:.1f} vs {young_crash[3]:.1f}x"
        f"ATR), young 60s wiggle fires={young_wiggle[0]}; very-young 5s crash "
        f"fires={veryyoung_crash[0]} (opening {veryyoung_crash[3]:.1f}x mult), "
        f"5s settling wiggle fires={veryyoung_settle[0]}. ROUTING: spike before "
        f"graduation gate={spike_before_grad}, closes check_min_hold=False="
        f"{spike_min_hold_exempt}, enabled={spike_enabled}",
        "a genuine young crash is caught by the catastrophe stop regardless of "
        "age, while a healthy young trade keeps its breathing room",
        kind="VERIFIED",
    )


def s_issue5():
    """Issue 5 — R2 clamp never pulls an armed breakeven floor sub-breakeven
    (verification-only — already correct).

    Real situation: 436 R2_FLOOR_HELD events, all applied at/above breakeven, 0
    wrong-side rejects. Verification: an armed ladder breakeven floor on a long
    whose price retraced near entry is held at/above breakeven by the real
    SLGateway, never clamped below it.
    """
    from src.core.sl_gateway import SLGateway
    g = SLGateway(S, _FakePS(), None)

    async def apply():
        return await g.apply(
            symbol="NEARUSDT", new_sl=1.9990, source="profit_sniper_ladder",
            direction="Buy", current_sl=1.9980, current_price=2.0050,
            bypass_step_cap_for_breakeven=True, breakeven_floor_price=2.0000,
        )
    r = asyncio.run(apply())
    applied = r.new_sl_applied
    held_at_or_above_be = (not r.accepted) or (applied is None) or (applied >= 1.99999)
    ok = held_at_or_above_be
    record(
        "Issue 5: R2 floor never sub-breakeven (verification)", ok,
        "live: 436 R2_FLOOR_HELD events, all at/above breakeven, 0 wrong-side "
        "rejects — confirm the armed floor is never pulled sub-breakeven",
        "n/a (no off-switch — the R2 hold + wrong-side guard + terminal reject "
        "already protect the armed floor)",
        f"armed floor at breakeven 2.000, raw SL 1.9990 -> accepted={r.accepted} "
        f"applied={applied} (held at/above breakeven, never sub-breakeven)",
        "an armed breakeven floor must never be clamped below breakeven by the "
        "per-step R2 rule",
        kind="VERIFIED",
    )


def s_issue6():
    """Issue 6 — down-weight the FVG-OB-in-ranging entry archetype.

    Real failure: FVG-OB setups taken in a ranging regime were the largest loss
    cluster (~-$240, 7 win-prob cuts). Off = discount 1.0 (full confidence, same
    as trending -> high score + full size); On = discount 0.75 (lower confidence
    -> lower rank-funnel score + smaller ensemble size, floored 0.5).
    """
    from src.analysis.structure.structure_engine import StructureEngine
    from src.analysis.structure.models.structure_types import (
        StructuralAnalysis, MarketStructureResult, FairValueGap, OrderBlock, SetupType,
    )

    def mk(struct):
        a = StructuralAnalysis(symbol="ADAUSDT", suggested_direction="long",
                               smc_confluence=90, position_in_range=0.2,
                               total_confluence_factors=4)
        a.market_structure = MarketStructureResult(structure=struct, strength="strong")
        a.nearest_fvg = FairValueGap(direction="bullish", filled=False)
        a.nearest_ob = OrderBlock(direction="bullish", fresh=True)
        a.mtf_confluence = MagicMock(score=8)
        return a

    def classify(discount):
        st = copy.copy(S.structure)
        st.setup_types = copy.copy(S.structure.setup_types)
        st.setup_types.fvg_ob_ranging_confidence_discount = discount
        eng = StructureEngine(st)
        t_r, c_r = eng.classify_setup(mk("ranging"))
        t_t, c_t = eng.classify_setup(mk("uptrend"))
        return t_r, c_r, t_t, c_t

    _, bug_r, _, bug_t = classify(1.0)    # off-switch: no discount
    tr, fix_r, tt, fix_t = classify(S.structure.setup_types.fvg_ob_ranging_confidence_discount)

    # ensemble size multiplier tracks confidence (floored 0.5).
    def size_mult(conf):
        return max(0.5, conf)
    ok = (tr == SetupType.BULLISH_FVG_OB and tt == SetupType.BULLISH_FVG_OB
          and abs(bug_r - bug_t) < 1e-6        # off: ranging == trending
          and fix_r < bug_r - 1e-6             # on: ranging discounted
          and size_mult(fix_r) < size_mult(bug_r) + 1e-9)
    record(
        "Issue 6: FVG-OB ranging down-weight (real StructureEngine)", ok,
        "FVG-OB setups in a ranging regime were the largest loss cluster "
        "(~-$240, 7 win-prob cuts) yet scored/sized like trending setups",
        f"discount off (1.0) -> ranging conf {bug_r:.3f} == trending conf "
        f"{bug_t:.3f} -> full rank score + size {size_mult(bug_r):.2f}x",
        f"discount {S.structure.setup_types.fvg_ob_ranging_confidence_discount} "
        f"-> ranging conf {fix_r:.3f} (< trending {fix_t:.3f}) -> lower rank "
        f"score + smaller size {size_mult(fix_r):.2f}x (floored 0.5, not gated)",
        "select and size the ranging FVG-OB archetype less (down-weight, not a "
        "gate) so the loss cluster shrinks without zeroing a legitimate setup",
    )


def s_issue7():
    """Issue 7 — directional-drawdown circuit breaker at the entry gate.

    Real failure: a -$204 correlated cluster — multiple same-direction longs all
    bleeding together with no portfolio-level stop on new same-direction entries.
    Off = breaker disabled (new concentrated long admitted, cluster grows); On =
    breaker HALTS a new same-direction entry while the book is over-concentrated
    and in aggregate same-direction loss, but allows the opposite direction
    (rebalancing) and NEVER closes the open runners.
    """
    from src.apex.gate import TradeGate
    from src.core.types import Side

    class _PS:
        def __init__(self, rows):
            self._p = [SimpleNamespace(side=Side(s), unrealized_pnl=float(u))
                       for s, u in rows]
        async def get_positions(self):
            return list(self._p)
        async def get_position(self, symbol):
            return None

    def gate(enabled):
        a = copy.copy(S.apex)
        a.portfolio_dd_breaker_enabled = enabled
        a.brain_authoritative_sizing_enabled = False
        services = {
            # 4 concentrated longs all bleeding (~-$200 each = the -$204 cluster
            # scaled), book is 100% long, aggregate same-dir loss large vs equity.
            "position_service": _PS([("Buy", -200)] * 4),
            "fund_manager": SimpleNamespace(
                _account_state=SimpleNamespace(available=100000.0, total_equity=40000.0)),
        }
        return TradeGate(services, a)

    def trade(direction):
        return {"symbol": "BTCUSDT", "direction": direction, "size_usd": 1000.0,
                "leverage": 3, "_xray_confidence": 0.7, "_setup_score": 80.0,
                "_expected_rr": 3.0, "_claude_original_size_usd": 1000.0}

    bug = asyncio.run(gate(False).validate(trade("Buy")))     # off -> admitted
    halt = asyncio.run(gate(True).validate(trade("Buy")))     # on -> halted
    opp = asyncio.run(gate(True).validate(trade("Sell")))     # on -> allowed

    default_off = S.apex.portfolio_dd_breaker_enabled is False
    ok = (default_off
          and not bug.get("_gate_rejected")          # off reproduces: admitted
          and bool(halt.get("_gate_rejected"))       # on: same-dir halted
          and not opp.get("_gate_rejected"))         # on: opposite allowed
    record(
        "Issue 7: portfolio DD breaker (real TradeGate.validate)", ok,
        "-$204 correlated cluster: 4 longs all bleeding together, no "
        "portfolio-level stop on a new same-direction entry",
        f"breaker off (default) -> new concentrated Buy ADMITTED "
        f"(_gate_rejected={bool(bug.get('_gate_rejected'))}) -> the cluster grows",
        f"breaker on -> new Buy HALTED (_gate_rejected={bool(halt.get('_gate_rejected'))}); "
        f"opposite Sell ALLOWED (_gate_rejected={bool(opp.get('_gate_rejected'))}); "
        "open runners never closed",
        "bound correlated same-direction downside by halting NEW same-direction "
        "entries, while allowing rebalancing and never capping/closing runners "
        f"(ships default-OFF: enabled={S.apex.portfolio_dd_breaker_enabled})",
    )


SCENARIOS = [
    ("Phase 1", s_phase1),
    ("Finding H", s_finding_h),
    ("Finding A", s_finding_a),
    ("Finding N", s_finding_n),
    ("Issue 1", s_issue1),
    ("Issue 2", s_issue2),
    ("Issue 3", s_issue3),
    ("Issue 4", s_issue4),
    ("Issue 5", s_issue5),
    ("Issue 6", s_issue6),
    ("Issue 7", s_issue7),
]

for tag, fn in SCENARIOS:
    run(tag, fn)

print("\n" + "=" * 80)
print("FAILURE-SCENARIO SIMULATION — recreate the real failure, run fix OFF vs ON")
print("real objects, real Settings.load(), no DB writes")
print("=" * 80)
n_ok = sum(1 for r in RESULTS if r["ok"])
for r in RESULTS:
    tag = "FIXED" if r["kind"] == "FIXED" else r["kind"]
    mark = "PASS" if r["ok"] else "FAIL"
    print(f"\n[{mark}] {r['name']}   ({tag})")
    print(f"   SITUATION   : {r['situation']}")
    print(f"   WITHOUT FIX : {r['without_fix']}")
    print(f"   WITH FIX    : {r['with_fix']}")
    print(f"   AIM         : {r['aim']}")
print("\n" + "=" * 80)
print(f"RESULT: {n_ok}/{len(RESULTS)} scenarios behave as designed "
      "(off reproduces the failure, on meets the aim)")
print("=" * 80)
import sys
sys.exit(0 if n_ok == len(RESULTS) else 1)
