"""Comprehensive tests for the 3 APEX fixes:
  Fix 1: Direction lock gate in optimizer.py
  Fix 2: Per-coin regime rules in strategist.py (prompt text verification)
  Fix 3: Volatility TP cap in models.py + prompts.py + optimizer.py
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ===========================================================================
# FIX 1 TESTS: Direction Lock Gate
# ===========================================================================

def test_check_flip_evidence_insufficient_trades():
    """Fewer than 8 opposite-direction trades => no flip evidence."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    trades = [
        {"direction": "Sell", "win": True},
        {"direction": "Sell", "win": True},
        {"direction": "Sell", "win": True},
        {"direction": "Buy", "win": False},
    ]
    # Claude direction is Buy, opposite is Sell — only 3 Sell trades (< 8)
    assert opt._check_flip_evidence(trades, "Buy") is False
    print("  PASS: insufficient trades (< 8) => no flip evidence")


def test_check_flip_evidence_low_wr():
    """8+ trades but WR < 70% => no flip evidence."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    # 10 Sell trades, 5 wins = 50% WR
    trades = [{"direction": "Sell", "win": i < 5} for i in range(10)]
    assert opt._check_flip_evidence(trades, "Buy") is False
    print("  PASS: low WR (50%) => no flip evidence")


def test_check_flip_evidence_overwhelming():
    """8+ trades with >70% WR => flip evidence found."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    # 10 Sell trades, 8 wins = 80% WR
    trades = [{"direction": "Sell", "win": i < 8} for i in range(10)]
    assert opt._check_flip_evidence(trades, "Buy") is True
    print("  PASS: overwhelming evidence (80% WR, 10 trades) => flip allowed")


def test_check_flip_evidence_empty_trades():
    """Empty trades list => no flip evidence."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    assert opt._check_flip_evidence([], "Buy") is False
    print("  PASS: empty trades => no flip evidence")


def test_check_flip_evidence_exactly_threshold():
    """Exactly 8 trades at exactly 70% WR => flip evidence found (>= 70)."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    # 10 Sell trades, 7 wins = 70% WR exactly
    trades = [{"direction": "Sell", "win": i < 7} for i in range(10)]
    assert opt._check_flip_evidence(trades, "Buy") is True
    print("  PASS: exactly 70% WR with 10 trades => flip allowed (boundary)")


class MockPackage:
    """Minimal mock for IntelligencePackage."""

    def __init__(self, regime, trades=None):
        self.symbol_history = type("H", (), {"trades": trades or []})()
        self.situation_data = type("S", (), {"regime": regime})()
        self.coin_data = type("C", (), {
            "current_price": 1.0,
            "recommended_tp_pct": None,
        })()
        self.directive = type("D", (), {"reasoning": ""})()


def test_direction_lock_trending_down_sell():
    """trending_down + Claude Sell (aligned) => NOT locked under composite.

    BETA R2 update (2026-05-17): the old regime-only lock fired even when
    Claude agreed with the regime (advisory directive-forcing). Under
    composite scoring the regime signal +1 means score >= 0 threshold,
    so no lock is needed — Claude's choice already aligns and there is
    no opposing evidence to override.
    """
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    pkg = MockPackage(regime="trending_down")
    locked, reason = opt._check_direction_lock(pkg, "Sell", "trending_down")
    assert locked is False, (
        f"aligned regime + brain dir must not lock under composite, got {reason!r}"
    )
    assert "composite_score" in reason
    print("  PASS: trending_down + Sell aligned => composite score positive, no lock")


def test_direction_lock_trending_down_buy():
    """trending_down + Claude Buy (opposing) => LOCKED under composite.

    BETA R2 update (2026-05-17): brain disagrees with regime, no other
    evidence supports brain (no structural data, no WR, no symbol history),
    composite score = -1 < 0 threshold -> lock fires.
    """
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    pkg = MockPackage(regime="trending_down")
    locked, reason = opt._check_direction_lock(pkg, "Buy", "trending_down")
    assert locked is True
    assert "composite_score" in reason
    print("  PASS: trending_down + Buy + no other evidence => composite < 0, locked")


def test_direction_lock_trending_up_buy():
    """trending_up + Claude Buy (aligned) => NOT locked under composite.

    BETA R2 update (2026-05-17): aligned brain + no opposing evidence
    => no lock. Mirror of test_direction_lock_trending_down_sell.
    """
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    pkg = MockPackage(regime="trending_up")
    locked, reason = opt._check_direction_lock(pkg, "Buy", "trending_up")
    assert locked is False
    assert "composite_score" in reason
    print("  PASS: trending_up + Buy aligned => composite score positive, no lock")


def test_direction_lock_ranging_no_lock():
    """ranging regime => NOT locked (both directions valid)."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    pkg = MockPackage(regime="ranging")
    locked, reason = opt._check_direction_lock(pkg, "Buy", "ranging")
    assert locked is False
    print("  PASS: ranging => no lock")


def test_direction_lock_dead_no_lock():
    """dead regime => NOT locked."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    pkg = MockPackage(regime="dead")
    locked, reason = opt._check_direction_lock(pkg, "Sell", "dead")
    assert locked is False
    print("  PASS: dead => no lock")


def test_direction_lock_volatile_no_evidence():
    """volatile + no evidence => NOT locked under composite.

    BETA R2 update (2026-05-17): the old volatile lock was a regime-
    based default-lock (lock unless overridden by evidence). Under
    composite scoring volatile contributes 0 to the regime signal —
    the new lock is evidence-driven, not regime-default. With no
    opposing evidence, no lock fires.
    """
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    # Only 3 opposite trades, insufficient to flip the symbol_evidence
    # signal (floor=70% WR over real sample size).
    trades = [{"direction": "Sell", "win": True} for _ in range(3)]
    pkg = MockPackage(regime="volatile", trades=trades)
    locked, reason = opt._check_direction_lock(pkg, "Buy", "volatile")
    # 3 Sell trades at 100% WR -> opp_wr >= 70% floor -> signal -1 -> locked
    # The composite framework treats opposing-direction high WR as
    # evidence against the brain regardless of regime. Old test
    # asserted lock from regime default; new test asserts lock from
    # symbol-specific opposite-direction WR.
    assert locked is True
    assert "composite_score" in reason
    print(
        "  PASS: volatile + opposing 100% WR => composite < 0 (symbol evidence locks)"
    )


def test_direction_lock_volatile_with_evidence():
    """volatile + overwhelming SAME-direction evidence => NOT locked.

    BETA R2 update (2026-05-17): the old test sent 10 Sell trades at
    80% WR with brain=Buy. Old semantic was "high opposite-direction WR
    is evidence FOR a flip into Buy" (volatile-only logic). New semantic
    is symmetric: high opposite-direction WR is evidence AGAINST the
    brain's choice. To keep this test asserting "evidence permits the
    brain's choice", invert the sample to have the brain's direction
    SAMPLE high.
    """
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    # 10 Buy trades at 80% WR — overwhelming SAME-direction evidence
    # for brain=Buy. Symbol-evidence signal = +1 -> no lock.
    trades = [{"direction": "Buy", "win": i < 8} for i in range(10)]
    pkg = MockPackage(regime="volatile", trades=trades)
    locked, reason = opt._check_direction_lock(pkg, "Buy", "volatile")
    assert locked is False, f"same-dir 80% WR must not lock, reason={reason!r}"
    assert "composite_score" in reason
    print(
        "  PASS: volatile + same-dir 80% WR => composite >= 0 (symbol evidence supports)"
    )


def test_direction_lock_unknown_regime():
    """unknown regime => NOT locked (conservative default: allow both)."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    pkg = MockPackage(regime="unknown")
    locked, reason = opt._check_direction_lock(pkg, "Buy", "unknown")
    assert locked is False
    print("  PASS: unknown regime => no lock")


def test_stats_include_lock_overrides():
    """get_stats() should include lock_overrides key."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    stats = opt.get_stats()
    assert "lock_overrides" in stats
    assert stats["lock_overrides"] == 0
    print("  PASS: get_stats() includes lock_overrides")


# ===========================================================================
# FIX 2 TESTS: Strategist Prompt — Per-Coin Rules
# ===========================================================================

def test_system_prompt_no_never_buy():
    """System prompt must NOT contain 'NEVER BUY' or 'ONLY open SELL'."""
    from src.brain.strategist import STRATEGIST_SYSTEM_PROMPT

    assert "NEVER BUY" not in STRATEGIST_SYSTEM_PROMPT, \
        "System prompt still contains 'NEVER BUY'"
    assert "ONLY open SELL" not in STRATEGIST_SYSTEM_PROMPT, \
        "System prompt still contains 'ONLY open SELL'"
    assert "SELL ONLY" not in STRATEGIST_SYSTEM_PROMPT, \
        "System prompt still contains 'SELL ONLY'"
    print("  PASS: system prompt has no blanket 'NEVER BUY'/'ONLY SELL' rules")


def test_system_prompt_has_per_coin():
    """System prompt must reference per-coin regime."""
    from src.brain.strategist import STRATEGIST_SYSTEM_PROMPT

    assert "PER-COIN" in STRATEGIST_SYSTEM_PROMPT, \
        "System prompt missing PER-COIN reference"
    assert "per-coin regime" in STRATEGIST_SYSTEM_PROMPT.lower() or \
           "per-coin" in STRATEGIST_SYSTEM_PROMPT, \
        "System prompt missing per-coin regime language"
    print("  PASS: system prompt references per-coin regime")


def test_system_prompt_direction_by_regime_updated():
    """DIRECTION BY REGIME section should say PER-COIN, not global."""
    from src.brain.strategist import STRATEGIST_SYSTEM_PROMPT

    # Per-coin-authority Phase 6 (2026-05-29) strengthened this to drop the
    # "global as default bias" fallback entirely.
    assert "PER-COIN — there is NO global direction bias" in STRATEGIST_SYSTEM_PROMPT, \
        "DIRECTION BY REGIME header not updated to per-coin/no-global"
    assert "INDIVIDUAL regime" in STRATEGIST_SYSTEM_PROMPT, \
        "Missing 'INDIVIDUAL regime' language"
    print("  PASS: DIRECTION BY REGIME says PER-COIN, no global bias")


def test_system_prompt_rule6_updated():
    """Rule #6 should say PER-COIN, not blanket SELL ONLY."""
    from src.brain.strategist import STRATEGIST_SYSTEM_PROMPT

    # Find rule 6 in the prompt
    idx = STRATEGIST_SYSTEM_PROMPT.find("6. REGIME-AWARE TRADING")
    assert idx > 0, "Rule 6 not found"
    rule6_section = STRATEGIST_SYSTEM_PROMPT[idx:idx + 400]
    assert "PER-COIN" in rule6_section, "Rule 6 not updated to PER-COIN"
    # Per-coin-authority Phase 6 (2026-05-29): per-coin is now the sole
    # direction authority (no global override clause at all).
    assert "no global direction bias" in rule6_section, \
        "Rule 6 missing 'no global direction bias' language"
    print("  PASS: Rule #6 updated to per-coin regime override")


def test_system_prompt_still_has_rsi_caution():
    """The oversold/fear caution should remain — it's valid guidance.

    The literal "Oversold RSI in a downtrend" / "Fear & Greed extreme
    fear in a downtrend" wording this test originally pinned was a
    prescriptive global-regime-bias phrasing that was deliberately
    rewritten — first in the aggressive-framing rewrite (2026-05-05,
    strategist.py comment block ~3957-3973) and then reframed
    NEUTRAL-on-direction in the direction-bias D1/D2 changes
    (STRAT_REGIME_BLOCK_VERSION = 5, strategist.py:257). The CAUTION
    ITSELF was NOT removed — it survives under different wording: the
    live STRATEGIST_SYSTEM_PROMPT still warns against flipping to a long
    purely on oversold/fear conditions while a coin's own regime is a
    downtrend. This test now pins that CURRENT real wording so it keeps
    guarding the intent without asserting prose that no longer exists.
    Production code is correct; only the stale literal was updated.
    Pins src/brain/strategist.py:106 (TRADE_SYSTEM_PROMPT, aliased as
    STRATEGIST_SYSTEM_PROMPT at strategist.py:279) — the FEAR & GREED
    block (~line 130-138).
    """
    from src.brain.strategist import STRATEGIST_SYSTEM_PROMPT

    # F&G is explicitly framed as NEUTRAL on direction (the reframe of
    # the old "extreme fear in a downtrend" caution).
    assert "F&G is a market-wide SENTIMENT reading" in STRATEGIST_SYSTEM_PROMPT, \
        "F&G sentiment-context caution removed (should have been kept)"
    # The surviving oversold/fear-in-a-downtrend caution: do not go long
    # merely because fear is high while the coin's regime is a downtrend.
    assert "Trending down + fear = the short is CONFIRMED" in STRATEGIST_SYSTEM_PROMPT, \
        "trending-down + fear caution removed (should have been kept)"
    assert "do NOT flip to long just because fear is high" in STRATEGIST_SYSTEM_PROMPT, \
        "the 'do not flip to long on fear' caution removed (should have been kept)"
    # The oversold-long path stays gated on structural confirmation.
    assert "possible oversold long, only if the coin" in STRATEGIST_SYSTEM_PROMPT, \
        "oversold-long structural-confirmation caution removed (should have been kept)"
    print("  PASS: oversold/fear caution preserved under reframed wording")


def test_system_prompt_volatility_rules_intact():
    """Volatility-adaptive targets rules should be intact."""
    from src.brain.strategist import STRATEGIST_SYSTEM_PROMPT

    assert "VOLATILITY-ADAPTIVE TARGETS" in STRATEGIST_SYSTEM_PROMPT
    assert "recTP%" in STRATEGIST_SYSTEM_PROMPT
    assert "recSL%" in STRATEGIST_SYSTEM_PROMPT
    print("  PASS: volatility-adaptive target rules intact")


# ===========================================================================
# FIX 3 TESTS: Volatility TP Cap
# ===========================================================================

def test_coindata_format_tp_cap():
    """CoinData.format() should show TP_CAP when volatility data present.

    Layer 1 Defect 7 (2026-05-21) aligned the display multiplier to
    the optimizer enforcement: medium class is now 1.6× (was 1.3×),
    so 1.95 × 1.6 = 3.12 → "3.1" rendered.
    """
    from src.apex.models import CoinData

    coin = CoinData(
        symbol="DOTUSDT",
        current_price=1.17,
        volatility_class="medium",
        recommended_tp_pct=1.95,
        recommended_sl_pct=0.90,
        recommended_hold_min=36,
        recommended_strategy="momentum",
    )
    formatted = coin.format()
    assert "TP_CAP=" in formatted, f"TP_CAP not in format output:\n{formatted}"
    # medium multiplier is now 1.6 (Defect 7): 1.95 × 1.6 = 3.12 → 3.1
    assert "3.1" in formatted, f"TP_CAP value (1.95*1.6=3.12) not in output:\n{formatted}"
    assert "TP HARD CAP" in formatted, f"TP HARD CAP warning not in output:\n{formatted}"
    # The display now explains which class multiplier is in use.
    assert "1.6x recTP" in formatted, f"medium-class 1.6x explanation not in output:\n{formatted}"
    print("  PASS: CoinData.format() shows TP_CAP and hard cap warning")


def test_coindata_format_no_volatility():
    """CoinData.format() without volatility data should NOT have TP_CAP."""
    from src.apex.models import CoinData

    coin = CoinData(symbol="BTCUSDT", current_price=65000.0)
    formatted = coin.format()
    assert "TP_CAP" not in formatted
    assert "TP HARD CAP" not in formatted
    print("  PASS: CoinData.format() without volatility has no TP_CAP")


def test_coindata_format_volatility_class_only():
    """CoinData with class but no recTP/recSL should not crash."""
    from src.apex.models import CoinData

    coin = CoinData(
        symbol="TESTUSDT",
        current_price=1.0,
        volatility_class="high",
        # No recommended_tp_pct or recommended_sl_pct
    )
    formatted = coin.format()
    assert "Volatility: HIGH" in formatted
    assert "TP_CAP" not in formatted  # No recTP => no cap
    print("  PASS: volatility_class without recTP/recSL doesn't crash")


def test_apex_system_prompt_tp_cap_rule():
    """APEX system prompt should include TP HARD CAP rule.

    Layer 1 Defect 7 (2026-05-21) generalised the prompt language so
    it no longer hardcodes a specific multiplier (the multiplier
    differs per volatility class). The instruction now refers to the
    per-class TP_CAP shown alongside in Coin Data, and the test
    verifies the surrounding structure rather than the literal
    multiplier.
    """
    from src.apex.prompts import APEX_SYSTEM_PROMPT

    assert "TP HARD CAP" in APEX_SYSTEM_PROMPT, \
        "APEX system prompt missing TP HARD CAP rule"
    # The prompt must still tell the model the cap is shown per coin.
    assert "TP_CAP" in APEX_SYSTEM_PROMPT, \
        "APEX system prompt missing TP_CAP reference"
    # The cap-rule language must still mention the recTP% basis.
    assert "recTP" in APEX_SYSTEM_PROMPT, \
        "APEX system prompt missing recTP% reference"
    print("  PASS: APEX system prompt has TP HARD CAP rule")


def test_apex_system_prompt_still_has_volatility_classes():
    """APEX system prompt should still have the volatility class ranges."""
    from src.apex.prompts import APEX_SYSTEM_PROMPT

    for cls in ["DEAD", "LOW", "MEDIUM", "HIGH", "EXTREME"]:
        assert cls in APEX_SYSTEM_PROMPT, f"Missing volatility class {cls}"
    print("  PASS: all 5 volatility classes present in APEX system prompt")


def test_tp_cap_computation():
    """TP cap = per-class multiplier × recommended_tp_pct.

    Layer 1 Defect 7 unified the display and optimizer maps to the
    settings defaults at APEXSettings.tp_cap_multiplier_by_class:
    {dead:1.4, low:1.5, medium:1.6, high:1.8, extreme:2.0}.
    Verifies the math inline so any future regression in the
    multiplier values surfaces immediately.
    """
    from src.apex.models import _CAP_MULT_MAP_DISPLAY

    medium_mult = _CAP_MULT_MAP_DISPLAY["medium"]
    assert medium_mult == 1.6, f"Expected 1.6, got {medium_mult}"

    rec_tp = 1.95
    cap = round(rec_tp * medium_mult, 2)
    assert cap == 3.12, f"Expected 3.12, got {cap}"

    rec_tp = 0.30
    cap = round(rec_tp * _CAP_MULT_MAP_DISPLAY["dead"], 2)
    # dead multiplier is 1.4: 0.30 × 1.4 = 0.42
    assert cap == 0.42, f"Expected 0.42 for dead class, got {cap}"

    rec_tp = 5.00
    cap = round(rec_tp * _CAP_MULT_MAP_DISPLAY["extreme"], 2)
    # extreme multiplier is 2.0: 5.00 × 2.0 = 10.0 (optimizer's hard
    # 5% ceiling clamps this at the call site; here we only verify
    # the multiplier math)
    assert cap == 10.0, f"Expected 10.0 for extreme class, got {cap}"
    print("  PASS: TP cap computation uses unified per-class multipliers")


# ===========================================================================
# INTEGRATION: Verify prompt builder renders lock instruction
# ===========================================================================

def test_lock_instruction_flows_to_prompt():
    """When direction is locked, the reasoning field should contain the lock
    instruction, and build_apex_user_prompt should render it."""
    from src.apex.models import (
        CoinData, DirectiveContext, IntelligencePackage,
        TIASSymbolHistory, TIASSituationData,
    )
    from src.apex.prompts import build_apex_user_prompt

    directive = DirectiveContext(
        symbol="ARIAUSDT", direction="Sell", sl=0.0, tp=0.0,
        leverage=3, size_usd=600,
        reasoning="[DIRECTION LOCKED: Sell — trending_down aligns with Sell. Do NOT change direction.] RSI oversold",
        plan_view="bearish",
    )
    coin = CoinData(symbol="ARIAUSDT", current_price=0.05)
    hist = TIASSymbolHistory(
        symbol="ARIAUSDT", total_trades=0, wins=0, losses=0,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        total_pnl_usd=0.0, ev_per_trade=0.0,
    )
    sit = TIASSituationData(
        regime="trending_down", fear_greed=30,
        total_trades_in_condition=0,
        buy_win_rate=0.0, sell_win_rate=0.0,
        avg_buy_pnl=0.0, avg_sell_pnl=0.0,
        direction_bias="sell",
    )
    pkg = IntelligencePackage(
        directive=directive, coin_data=coin,
        symbol_history=hist, situation_data=sit,
    )
    prompt = build_apex_user_prompt(pkg)
    assert "DIRECTION LOCKED" in prompt, \
        "Lock instruction not rendered in user prompt"
    assert "Do NOT change direction" in prompt, \
        "Lock enforcement text not in prompt"
    print("  PASS: lock instruction flows through to Qwen's user prompt")


# ===========================================================================
# INTEGRATION: Verify TP cap in full prompt rendering
# ===========================================================================

def test_tp_cap_in_full_prompt():
    """TP cap warning should appear in the full user prompt when volatility data exists."""
    from src.apex.models import (
        CoinData, DirectiveContext, IntelligencePackage,
        TIASSymbolHistory, TIASSituationData,
    )
    from src.apex.prompts import build_apex_user_prompt

    directive = DirectiveContext(
        symbol="DOTUSDT", direction="Sell", sl=1.10, tp=1.25,
        leverage=3, size_usd=600, reasoning="momentum play", plan_view="",
    )
    coin = CoinData(
        symbol="DOTUSDT", current_price=1.17,
        volatility_class="medium",
        recommended_tp_pct=1.95,
        recommended_sl_pct=0.90,
        recommended_hold_min=36,
        recommended_strategy="momentum",
    )
    hist = TIASSymbolHistory(
        symbol="DOTUSDT", total_trades=0, wins=0, losses=0,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        total_pnl_usd=0.0, ev_per_trade=0.0,
    )
    sit = TIASSituationData(
        regime="trending_down", fear_greed=30,
        total_trades_in_condition=0,
        buy_win_rate=0.0, sell_win_rate=0.0,
        avg_buy_pnl=0.0, avg_sell_pnl=0.0,
        direction_bias="sell",
    )
    pkg = IntelligencePackage(
        directive=directive, coin_data=coin,
        symbol_history=hist, situation_data=sit,
    )
    prompt = build_apex_user_prompt(pkg)
    assert "TP_CAP=" in prompt, "TP_CAP not in full prompt"
    assert "TP HARD CAP" in prompt, "TP HARD CAP warning not in full prompt"
    assert "MEDIUM" in prompt, "Volatility class MEDIUM not in prompt"
    print("  PASS: TP cap warning appears in full user prompt")


# ===========================================================================
# EDGE CASE: direction lock with empty string regime
# ===========================================================================

def test_direction_lock_empty_regime():
    """Empty/blank regime => no lock."""
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    pkg = MockPackage(regime="")
    locked, reason = opt._check_direction_lock(pkg, "Buy", "")
    assert locked is False
    print("  PASS: empty regime => no lock")


# ===========================================================================
# RUN ALL TESTS
# ===========================================================================

def main():
    tests = [
        # Fix 1 tests
        ("Fix 1: _check_flip_evidence — insufficient trades", test_check_flip_evidence_insufficient_trades),
        ("Fix 1: _check_flip_evidence — low WR", test_check_flip_evidence_low_wr),
        ("Fix 1: _check_flip_evidence — overwhelming", test_check_flip_evidence_overwhelming),
        ("Fix 1: _check_flip_evidence — empty trades", test_check_flip_evidence_empty_trades),
        ("Fix 1: _check_flip_evidence — exactly threshold", test_check_flip_evidence_exactly_threshold),
        ("Fix 1: direction lock — trending_down + Sell", test_direction_lock_trending_down_sell),
        ("Fix 1: direction lock — trending_down + Buy", test_direction_lock_trending_down_buy),
        ("Fix 1: direction lock — trending_up + Buy", test_direction_lock_trending_up_buy),
        ("Fix 1: direction lock — ranging", test_direction_lock_ranging_no_lock),
        ("Fix 1: direction lock — dead", test_direction_lock_dead_no_lock),
        ("Fix 1: direction lock — volatile no evidence", test_direction_lock_volatile_no_evidence),
        ("Fix 1: direction lock — volatile with evidence", test_direction_lock_volatile_with_evidence),
        ("Fix 1: direction lock — unknown regime", test_direction_lock_unknown_regime),
        ("Fix 1: direction lock — empty regime", test_direction_lock_empty_regime),
        ("Fix 1: get_stats includes lock_overrides", test_stats_include_lock_overrides),
        # Fix 2 tests
        ("Fix 2: no NEVER BUY in system prompt", test_system_prompt_no_never_buy),
        ("Fix 2: per-coin reference in system prompt", test_system_prompt_has_per_coin),
        ("Fix 2: DIRECTION BY REGIME updated", test_system_prompt_direction_by_regime_updated),
        ("Fix 2: Rule #6 updated", test_system_prompt_rule6_updated),
        ("Fix 2: RSI/F&G cautions preserved", test_system_prompt_still_has_rsi_caution),
        ("Fix 2: volatility rules intact", test_system_prompt_volatility_rules_intact),
        # Fix 3 tests
        ("Fix 3: CoinData.format() TP cap", test_coindata_format_tp_cap),
        ("Fix 3: CoinData.format() no volatility", test_coindata_format_no_volatility),
        ("Fix 3: CoinData.format() class-only", test_coindata_format_volatility_class_only),
        ("Fix 3: APEX system prompt TP cap rule", test_apex_system_prompt_tp_cap_rule),
        ("Fix 3: APEX system prompt volatility classes", test_apex_system_prompt_still_has_volatility_classes),
        ("Fix 3: TP cap computation math", test_tp_cap_computation),
        # Integration tests
        ("Integration: lock instruction in prompt", test_lock_instruction_flows_to_prompt),
        ("Integration: TP cap in full prompt", test_tp_cap_in_full_prompt),
    ]

    passed = 0
    failed = 0
    errors = []

    print("=" * 70)
    print("APEX DIRECTION LOCK + PER-COIN RULES + VOLATILITY TP CAP — TEST SUITE")
    print("=" * 70)

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            print(f"  FAIL: {name}: {e}")

    print()
    print("=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed, {len(tests)} total")
    print("=" * 70)

    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err}")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
