"""Runtime pipeline verification for the 11 fixes (read-only, no DB writes).

Builds the REAL objects from the REAL config (Settings.load()) and drives
representative data through each fix's pipeline, asserting the fix fires at
runtime and that the real config value flowed into the constructed object. This
is a verification harness (project-root, per the program rules); it never writes
to the database or mutates data.

Run:  .venv/bin/python verify_pipelines_runtime.py
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config.settings import Settings

S = Settings.load()
RESULTS: list[tuple[str, bool, str]] = []


class _FakePS:
    """A position service whose set_stop_loss succeeds — lets SLGateway.apply
    complete the wire step so the R3-bypass / R2-floor outcome is observable
    (no real exchange; read-only)."""
    async def set_stop_loss(self, symbol, new_sl):
        return True


def record(name: str, passed: bool, evidence: str) -> None:
    RESULTS.append((name, passed, evidence))


def check(name: str, fn) -> None:
    try:
        passed, evidence = fn()
    except Exception as e:  # pragma: no cover - surface construction failures
        import traceback
        passed, evidence = False, f"EXCEPTION: {e}\n{traceback.format_exc()[-600:]}"
    record(name, passed, evidence)


# ── Phase 1 — PnL reconciler exit-plausibility gate ───────────────────
def _phase1():
    from src.core.trade_coordinator import TradeCoordinator
    from src.workers.pnl_reconciler import PnLReconciler
    coord = TradeCoordinator()
    recon = PnLReconciler(S, db=None, services={"trade_coordinator": coord})
    cfg_val = S.bybit_demo.close_pnl_reconcile_max_exit_divergence_pct
    flowed = abs(recon._max_exit_div_pct - cfg_val) < 1e-9
    phantom = recon._exit_implausible(2.07, 2.3379)      # NEAR phantom -> reject
    feeflip = recon._exit_implausible(1685.0, 1685.97)   # ETH fee-flip -> accept
    ok = flowed and phantom is True and feeflip is False
    return ok, (f"config flowed: recon._max_exit_div_pct={recon._max_exit_div_pct} "
                f"(=config {cfg_val}); phantom(2.07->2.3379)reject={phantom}; "
                f"feeflip(1685->1685.97)accept={not feeflip}")


# ── Finding H + Issue 5 — real SLGateway.apply ────────────────────────
def _gateway():
    from src.core.sl_gateway import SLGateway
    g = SLGateway(S, _FakePS(), None)
    in_set = "profit_sniper_trail" in g._BREAKEVEN_BYPASS_SOURCES

    async def run():
        # H: a long trail raise of ~1.6% step (> R3 0.25%), bypass on for the
        # allowlisted trail source -> should NOT be R3-clamped to 0.25%.
        r_trail = await g.apply(
            symbol="AAVEUSDT", new_sl=63.5, source="profit_sniper_trail",
            direction="Buy", current_sl=62.5, current_price=64.0,
            bypass_step_cap_for_breakeven=True,
        )
        # Same move from a NON-allowlisted source -> R3 clamps the step.
        r_other = await g.apply(
            symbol="XYZUSDT", new_sl=63.5, source="time_decay",
            direction="Buy", current_sl=62.5, current_price=64.0,
            bypass_step_cap_for_breakeven=True,
        )
        return r_trail, r_other
    r_trail, r_other = asyncio.run(run())
    trail_sl = r_trail.new_sl_applied
    other_sl = r_other.new_sl_applied
    # The bypassed trail lands at the full requested 63.5 (R3 overridden); the
    # non-allowlisted source is R3-clamped to ~0.25%/step (~62.66) -> trail higher.
    bypass_works = (
        r_trail.accepted and trail_sl is not None
        and (other_sl is None or trail_sl > other_sl)
    )
    ok = in_set and bypass_works
    return ok, (f"profit_sniper_trail in bypass set={in_set}; trail accepted="
                f"{r_trail.accepted} trail_sl_applied={trail_sl} "
                f"vs other(R3-clamped)_sl={other_sl} (trail > other = bypass fired)")


def _r2_floor():
    from src.core.sl_gateway import SLGateway
    g = SLGateway(S, _FakePS(), None)

    async def run():
        # A long whose price retraced near entry; an armed ladder breakeven floor
        # must be held at/above breakeven, never clamped sub-breakeven. Use a raw
        # SL below breakeven so R2 would (pre-fix) clamp it sub-breakeven.
        return await g.apply(
            symbol="NEARUSDT", new_sl=1.9990, source="profit_sniper_ladder",
            direction="Buy", current_sl=1.9980, current_price=2.0050,
            bypass_step_cap_for_breakeven=True, breakeven_floor_price=2.0000,
        )
    r = asyncio.run(run())
    applied = r.new_sl_applied
    # If accepted, the applied stop must be held at/above breakeven (2.000).
    ok = (not r.accepted) or (applied is None) or (applied >= 1.99999)
    return ok, (f"accepted={r.accepted} applied_sl={applied} "
                f"breakeven=2.000 (held at/above breakeven, never sub-breakeven)")


# ── Finding A + N — real ProfitSniper methods, real config ────────────
def _sniper_fee_cap():
    from src.workers.profit_sniper import ProfitSniper
    sn = ProfitSniper.__new__(ProfitSniper)
    sn._pf = S.profit_fetching
    sn._lc = S.loss_cutting
    sn._last_breakeven_floor_logged = {}
    # A: a modest peak (0.18%) that cleared the fee hurdle -> lock raised to fee_clear.
    state = SimpleNamespace(entry_price=100.0, direction="Buy",
                            peak_pnl_pct=0.18, symbol="ADAUSDT")
    dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
    res = sn._compute_ladder_floor(state, dialed, 0.0)
    fee_clear = S.profit_fetching.ladder_lock_fee_clearance_pct
    a_ok = res.should_apply and res.lock_pct >= fee_clear - 1e-9
    # N: net cap subtracts the round-trip fee.
    net = sn._lc_net_cap_dollars(75.0, 5996.0)
    expect = 75.0 - 5996.0 * S.loss_cutting.cap_round_trip_fee_pct / 100.0
    n_ok = abs(net - expect) < 1e-6
    return (a_ok and n_ok), (
        f"A: peak 0.18% -> lock_pct={res.lock_pct} (>= fee_clear {fee_clear}); "
        f"N: net_cap(75,5996)={net:.4f} (expect {expect:.4f}, fee "
        f"{S.loss_cutting.cap_round_trip_fee_pct}% subtracted)")


# ── Issue 1 + 2 — real SignalGenerator / Confidence ───────────────────
def _signal():
    from src.intelligence.signals.signal_generator import SignalGenerator
    from src.core.types import SignalType
    sg = SignalGenerator.__new__(SignalGenerator)
    sg._ms_cfg = S.signal_generator.multi_source
    sg._sentiment_consumption_enabled = False
    neutral_flag = sg._ms_cfg.fg_direction_neutral
    fear, _ = sg._evaluate_signal(sentiment=0.0, fear_greed=15, funding_rate=0.0,
                                  oi_change=0.0, symbol="BTCUSDT")
    greed, _ = sg._evaluate_signal(sentiment=0.0, fear_greed=85, funding_rate=0.0,
                                   oi_change=0.0, symbol="BTCUSDT")
    fund, fund_reason = sg._evaluate_signal(sentiment=0.0, fear_greed=15,
                                            funding_rate=-0.012, oi_change=0.0,
                                            symbol="BTCUSDT")
    # neutrality: fear-alone and greed-alone both NEUTRAL (a mix, not a flip);
    # funding drives a per-coin BUY.
    i1_ok = (neutral_flag and fear == SignalType.NEUTRAL
             and greed == SignalType.NEUTRAL
             and fund in (SignalType.BUY, SignalType.STRONG_BUY))
    # off-switch via the real builder (the bug we fixed)
    from src.config.settings import _build_signal_generator
    off = _build_signal_generator({"multi_source": {"fg_direction_neutral": False}})
    offswitch_ok = off.multi_source.fg_direction_neutral is False
    # I2: UNKNOWN sentiment excluded from confidence (None > 0.0)
    from src.intelligence.signals.confidence import ConfidenceCalculator
    c = ConfidenceCalculator()
    base = dict(fear_greed=1.0, funding_rate=0.5, open_interest=0.5,
                data_age_hours=1.0, volume_surge_ratio=1.0)
    z = c.calculate(dict(base, news_sentiment=0.0, reddit_sentiment=0.0))
    n = c.calculate(dict(base, news_sentiment=None, reddit_sentiment=None))
    i2_ok = n > z
    return (i1_ok and offswitch_ok and i2_ok), (
        f"I1: fg_neutral={neutral_flag}; fear->{fear.value} greed->{greed.value} "
        f"(both neutral=mix-not-flip); funding-0.012->{fund.value}; "
        f"off-switch builder={off.multi_source.fg_direction_neutral}; "
        f"I2: conf None={n:.3f} > 0.0={z:.3f}")


# ── Issue 3 — TimeDecay recovery-tighten via the bridge mapping ───────
def _timedecay():
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator
    td = S.time_decay
    # Build exactly as the position_watchdog bridge does for the 3 new fields.
    cfg = TimeDecayConfig(
        mae_recovery_tighten_enabled=td.mae_recovery_tighten_enabled,
        mae_tightening_recovery_threshold=td.mae_tightening_recovery_threshold,
        recovery_tightening_buffer_pct=td.recovery_tightening_buffer_pct,
    )
    flowed = (cfg.mae_recovery_tighten_enabled == td.mae_recovery_tighten_enabled
              and cfg.mae_tightening_recovery_threshold == td.mae_tightening_recovery_threshold)
    calc = TimeDecaySLCalculator(cfg)
    state = calc.create_state(
        symbol="PHASE3", direction="Buy", entry_price=100.0, original_sl_pct=2.0,
        max_hold_seconds=2700, atr_5m_pct=0.5, regime_confidence=0.6,
        tick_seconds=5.0, entry_xray_confidence=0.65,
        entry_setup_type="BULLISH_FVG_OB", entry_regime_at_open="trending_up",
        entry_regime_confidence=0.70,
    )
    state.p_win = 0.9
    state.mae_pct = -1.09
    state.last_allowed_loss = 1.5
    state.last_pnl_pct = -0.2
    out = calc.calculate(
        state, current_pnl_pct=-0.2, position_age_seconds=400,
        regime_still_supports=True, velocity_pct_per_s=0.02,
        acceleration_pct_per_s2=0.005, structural_invalidation=False,
        invalidation_reason="stable",
    )
    # strong recovery (0.82) -> tighten toward ~0.5% allowed_loss (SL ~99.5)
    ok = flowed and out is not None and state.last_allowed_loss <= 0.5 + 1e-9
    return ok, (f"config flowed={flowed}; recovery-tighten out_sl={out} "
                f"last_allowed_loss={state.last_allowed_loss} (<=0.5 expected)")


# ── Issue 6 — real StructureEngine.classify_setup ─────────────────────
def _structure():
    from src.analysis.structure.structure_engine import StructureEngine
    from src.analysis.structure.models.structure_types import (
        StructuralAnalysis, MarketStructureResult, FairValueGap, OrderBlock,
    )
    from src.analysis.structure.models.structure_types import SetupType
    eng = StructureEngine(S.structure)
    disc = S.structure.setup_types.fvg_ob_ranging_confidence_discount

    def mk(struct):
        a = StructuralAnalysis(symbol="ADAUSDT", suggested_direction="long",
                               smc_confluence=90, position_in_range=0.2,
                               total_confluence_factors=4)
        a.market_structure = MarketStructureResult(structure=struct, strength="strong")
        a.nearest_fvg = FairValueGap(direction="bullish", filled=False)
        a.nearest_ob = OrderBlock(direction="bullish", fresh=True)
        a.mtf_confluence = MagicMock(score=8)
        return a
    t_ranging, c_ranging = eng.classify_setup(mk("ranging"))
    t_trend, c_trend = eng.classify_setup(mk("uptrend"))
    # ranging conf should be the trending conf * discount
    ok = (t_ranging == SetupType.BULLISH_FVG_OB and t_trend == SetupType.BULLISH_FVG_OB
          and abs(c_ranging - c_trend * disc) < 0.02 and c_ranging < c_trend)
    return ok, (f"discount={disc}; ranging conf={c_ranging} vs trending conf={c_trend} "
                f"(ranging == trending*{disc} expected)")


# ── Issue 7 — real TradeGate.validate ─────────────────────────────────
def _apex():
    from src.apex.gate import TradeGate
    from src.core.types import Side
    ap = S.apex
    default_off = ap.portfolio_dd_breaker_enabled is False

    class _PS:
        def __init__(self, rows):
            self._p = [SimpleNamespace(side=Side(s), unrealized_pnl=float(u)) for s, u in rows]
        async def get_positions(self):
            return list(self._p)
        async def get_position(self, symbol):
            return None

    def gate(enabled):
        import copy
        a = copy.copy(ap)
        a.portfolio_dd_breaker_enabled = enabled
        a.brain_authoritative_sizing_enabled = False
        services = {
            "position_service": _PS([("Buy", -200)] * 4),
            "fund_manager": SimpleNamespace(
                _account_state=SimpleNamespace(available=100000.0, total_equity=40000.0)),
        }
        return TradeGate(services, a)

    def trade(direction):
        return {"symbol": "BTCUSDT", "direction": direction, "size_usd": 1000.0,
                "leverage": 3, "_xray_confidence": 0.7, "_setup_score": 80.0,
                "_expected_rr": 3.0, "_claude_original_size_usd": 1000.0}

    halt = asyncio.run(gate(True).validate(trade("Buy")))
    opp = asyncio.run(gate(True).validate(trade("Sell")))
    off = asyncio.run(gate(False).validate(trade("Buy")))
    ok = (default_off and bool(halt.get("_gate_rejected"))
          and not opp.get("_gate_rejected") and not off.get("_gate_rejected"))
    return ok, (f"default_off={default_off}; concentrated Buy halt="
                f"{halt.get('_gate_rejected')}; opposite Sell rejected="
                f"{bool(opp.get('_gate_rejected'))}; breaker-off rejected="
                f"{bool(off.get('_gate_rejected'))}")


for nm, fn in [
    ("Phase1: PnL reconciler exit-plausibility gate", _phase1),
    ("Finding H: trail R3-bypass (real SLGateway)", _gateway),
    ("Issue 5: R2 floor never sub-breakeven (real SLGateway)", _r2_floor),
    ("Finding A+N: fee-aware lock + net-aware cap (real config)", _sniper_fee_cap),
    ("Issue 1+2: F&G-neutral + UNKNOWN-sentiment (real signal)", _signal),
    ("Issue 3: MAE recovery-tighten (bridge mapping)", _timedecay),
    ("Issue 6: FVG-OB ranging discount (real StructureEngine)", _structure),
    ("Issue 7: portfolio breaker (real TradeGate)", _apex),
]:
    check(nm, fn)

print("\n" + "=" * 78)
print("RUNTIME PIPELINE VERIFICATION — real objects, real Settings, driven data")
print("=" * 78)
n_pass = sum(1 for _, p, _ in RESULTS if p)
for nm, p, ev in RESULTS:
    print(f"\n[{'PASS' if p else 'FAIL'}] {nm}")
    print(f"      {ev}")
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{len(RESULTS)} pipelines verified at runtime")
print("=" * 78)
import sys
sys.exit(0 if n_pass == len(RESULTS) else 1)
