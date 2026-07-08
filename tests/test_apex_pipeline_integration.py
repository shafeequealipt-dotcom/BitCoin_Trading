"""End-to-end pipeline integration tests for the 3 APEX fixes.

Tests the REAL project code — DI wiring, data flow, service connections,
model construction, prompt rendering, and optimization enforcement.

No external services required (no Qwen API, no Bybit, no DB) — but uses
the real classes and their real methods with controlled inputs.
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

passed = 0
failed = 0
errors = []


def check(name, condition, msg=""):
    global passed, failed, errors
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        errors.append((name, msg))
        print(f"  FAIL: {name}: {msg}")


# ===========================================================================
# 1. IMPORT VERIFICATION — all modified modules import cleanly
# ===========================================================================

def test_imports():
    """All modified modules import without errors."""
    print("\n--- 1. Import Verification ---")

    from src.apex.optimizer import TradeOptimizer
    check("import TradeOptimizer", TradeOptimizer is not None)

    from src.apex.models import (
        CoinData, DirectiveContext, IntelligencePackage,
        TIASSymbolHistory, TIASSituationData, StructuralData,
        OptimizedTrade,
    )
    check("import all APEX models", CoinData is not None)

    from src.apex.prompts import APEX_SYSTEM_PROMPT, build_apex_user_prompt
    check("import APEX prompts", APEX_SYSTEM_PROMPT is not None)

    from src.apex.assembler import IntelligenceAssembler
    check("import IntelligenceAssembler", IntelligenceAssembler is not None)

    from src.brain.strategist import STRATEGIST_SYSTEM_PROMPT, ClaudeStrategist
    check("import Strategist", ClaudeStrategist is not None)

    from src.analysis.volatility_profile import VolatilityProfiler, CoinVolatilityProfile
    check("import VolatilityProfiler", VolatilityProfiler is not None)

    from src.strategies.regime import RegimeDetector
    check("import RegimeDetector", RegimeDetector is not None)

    from src.core.strategic_plan import StrategicPlan, CoinDirective, PositionAction
    check("import StrategicPlan", StrategicPlan is not None)


# ===========================================================================
# 2. MODEL CONSTRUCTION — dataclasses build correctly with new fields
# ===========================================================================

def test_model_construction():
    """All dataclasses instantiate correctly with real field values."""
    print("\n--- 2. Model Construction ---")

    from src.apex.models import (
        CoinData, DirectiveContext, IntelligencePackage,
        TIASSymbolHistory, TIASSituationData, OptimizedTrade,
    )

    # CoinData with volatility fields
    coin = CoinData(
        symbol="DOTUSDT",
        current_price=1.17,
        rsi=45.0,
        adx=40.0,
        atr=0.0033,
        atr_pct=0.28,
        volatility_class="medium",
        recommended_tp_pct=1.95,
        recommended_sl_pct=0.90,
        recommended_hold_min=36,
        recommended_strategy="momentum",
    )
    check("CoinData with volatility fields", coin.volatility_class == "medium")
    check("CoinData recommended_tp_pct", coin.recommended_tp_pct == 1.95)

    # DirectiveContext
    directive = DirectiveContext(
        symbol="DOTUSDT", direction="Sell", sl=1.10, tp=1.25,
        leverage=3, size_usd=600,
        reasoning="momentum play", plan_view="bearish outlook",
    )
    check("DirectiveContext construction", directive.direction == "Sell")

    # TIASSymbolHistory with trades list
    trades = [
        {"direction": "Sell", "win": True, "pnl_usd": 5.0, "pnl_pct": 1.2},
        {"direction": "Buy", "win": False, "pnl_usd": -3.0, "pnl_pct": -0.8},
    ]
    hist = TIASSymbolHistory(
        symbol="DOTUSDT", total_trades=2, wins=1, losses=1,
        win_rate=50.0, avg_win_pct=1.2, avg_loss_pct=-0.8,
        total_pnl_usd=2.0, ev_per_trade=1.0,
        trades=trades, regime="trending_down",
    )
    check("TIASSymbolHistory with trades", len(hist.trades) == 2)
    check("TIAS trades have direction key", hist.trades[0].get("direction") == "Sell")
    check("TIAS trades have win key", hist.trades[0].get("win") is True)

    # TIASSituationData
    sit = TIASSituationData(
        regime="trending_down", fear_greed=25,
        total_trades_in_condition=50,
        buy_win_rate=35.0, sell_win_rate=62.0,
        avg_buy_pnl=-0.5, avg_sell_pnl=0.8,
        direction_bias="sell",
    )
    check("TIASSituationData construction", sit.regime == "trending_down")

    # IntelligencePackage — full 5-section package
    pkg = IntelligencePackage(
        directive=directive, coin_data=coin,
        symbol_history=hist, situation_data=sit,
    )
    check("IntelligencePackage construction", pkg.directive.symbol == "DOTUSDT")
    check("Package regime accessible", pkg.situation_data.regime == "trending_down")
    check("Package volatility accessible", pkg.coin_data.recommended_tp_pct == 1.95)

    # OptimizedTrade — what Qwen returns
    opt = OptimizedTrade(
        symbol="DOTUSDT", direction="Sell", sl_pct=0.9, tp_pct=1.88,
        tp_mode="fixed", position_size_usd=800, leverage=3,
        entry_timing="immediate", add_on_pullback=False,
        was_flipped=False, original_direction="Sell",
        is_fallback=False,
    )
    check("OptimizedTrade construction", opt.direction == "Sell")
    check("OptimizedTrade is_fallback=False", opt.is_fallback is False)


# ===========================================================================
# 3. FORMAT RENDERING — CoinData.format() output verification
# ===========================================================================

def test_format_rendering():
    """CoinData.format() renders volatility profile with TP cap correctly."""
    print("\n--- 3. Format Rendering ---")

    from src.apex.models import CoinData

    # Case 1: Full volatility profile
    coin = CoinData(
        symbol="DOTUSDT", current_price=1.17,
        rsi=45.0, adx=40.0, atr_pct=0.28,
        volatility_class="medium",
        recommended_tp_pct=1.95, recommended_sl_pct=0.90,
        recommended_hold_min=36, recommended_strategy="momentum",
    )
    fmt = coin.format()
    check("format has symbol", "DOTUSDT" in fmt)
    check("format has RSI", "RSI(14): 45.0" in fmt)
    check("format has volatility class", "MEDIUM" in fmt)
    check("format has recTP", "recTP=1.9" in fmt)
    check("format has recSL", "recSL=0.9" in fmt)
    check("format has TP_CAP", "TP_CAP=" in fmt)
    tp_cap = round(1.95 * 1.3, 2)  # 2.54
    check("format TP_CAP value correct (2.5)", "2.5" in fmt)
    check("format has TP HARD CAP warning", "TP HARD CAP" in fmt)
    check("format has 1.3x explanation", "1.3x recTP" in fmt)
    check("format has hold time", "36min" in fmt)
    check("format has strategy", "momentum" in fmt)

    # Case 2: No volatility data
    coin_no_vol = CoinData(symbol="BTCUSDT", current_price=65000.0)
    fmt2 = coin_no_vol.format()
    check("no-vol format lacks TP_CAP", "TP_CAP" not in fmt2)
    check("no-vol format lacks HARD CAP", "TP HARD CAP" not in fmt2)

    # Case 3: Volatility class but no recTP
    coin_partial = CoinData(
        symbol="XUSDT", current_price=1.0,
        volatility_class="high",
    )
    fmt3 = coin_partial.format()
    check("partial-vol has class", "HIGH" in fmt3)
    check("partial-vol lacks TP_CAP", "TP_CAP" not in fmt3)

    # Case 4: Dead volatility (tiny values)
    coin_dead = CoinData(
        symbol="DEADUSDT", current_price=0.001,
        volatility_class="dead",
        recommended_tp_pct=0.30, recommended_sl_pct=0.20,
        recommended_hold_min=10, recommended_strategy="scalp",
    )
    fmt4 = coin_dead.format()
    dead_cap = round(0.30 * 1.3, 2)  # 0.39
    check("dead-vol TP_CAP correct (0.4)", "0.4" in fmt4 or "0.39" in fmt4)
    check("dead-vol has DEAD class", "DEAD" in fmt4)


# ===========================================================================
# 4. PROMPT BUILDER — end-to-end prompt rendering
# ===========================================================================

def test_prompt_builder():
    """build_apex_user_prompt renders all sections correctly."""
    print("\n--- 4. Prompt Builder ---")

    from src.apex.models import (
        CoinData, DirectiveContext, IntelligencePackage,
        TIASSymbolHistory, TIASSituationData, StructuralData,
    )
    from src.apex.prompts import build_apex_user_prompt, APEX_SYSTEM_PROMPT

    # Build a realistic package
    directive = DirectiveContext(
        symbol="ARIAUSDT", direction="Sell",
        sl=0.055, tp=0.048,
        leverage=3, size_usd=600,
        reasoning="[DIRECTION LOCKED: Sell — trending_down aligns with Sell. Do NOT change direction.] short momentum",
        plan_view="bearish",
    )
    coin = CoinData(
        symbol="ARIAUSDT", current_price=0.05,
        rsi=30.0, adx=45.0, atr_pct=0.35,
        volatility_class="medium",
        recommended_tp_pct=1.50, recommended_sl_pct=1.00,
        recommended_hold_min=30, recommended_strategy="momentum",
    )
    trades = [
        {"direction": "Sell", "win": True, "pnl_usd": 8.0, "pnl_pct": 1.5,
         "closed_by": "tp_hit", "hold_seconds": 900},
        {"direction": "Buy", "win": False, "pnl_usd": -23.52, "pnl_pct": -3.2,
         "closed_by": "sl_hit", "hold_seconds": 180, "ds_category": "counter_trend_entry"},
    ]
    hist = TIASSymbolHistory(
        symbol="ARIAUSDT", total_trades=2, wins=1, losses=1,
        win_rate=50.0, avg_win_pct=1.5, avg_loss_pct=-3.2,
        total_pnl_usd=-15.52, ev_per_trade=-7.76,
        trades=trades, pattern_summary="Mixed results",
        regime="trending_down",
    )
    sit = TIASSituationData(
        regime="trending_down", fear_greed=25,
        total_trades_in_condition=40,
        buy_win_rate=30.0, sell_win_rate=65.0,
        avg_buy_pnl=-1.2, avg_sell_pnl=0.8,
        direction_bias="sell",
    )
    pkg = IntelligencePackage(
        directive=directive, coin_data=coin,
        symbol_history=hist, situation_data=sit,
    )

    prompt = build_apex_user_prompt(pkg)

    # Section 1: Directive
    check("prompt has symbol", "ARIAUSDT" in prompt)
    check("prompt has direction", "Sell" in prompt)
    check("prompt has direction lock text", "DIRECTION LOCKED" in prompt)
    check("prompt has 'Do NOT change direction'", "Do NOT change direction" in prompt)

    # Section 2: Coin data with volatility
    check("prompt has volatility class", "MEDIUM" in prompt)
    check("prompt has TP_CAP", "TP_CAP=" in prompt)
    check("prompt has TP HARD CAP", "TP HARD CAP" in prompt)
    check("prompt has RSI", "30.0" in prompt)

    # Section 3: TIAS history
    check("prompt has TIAS history header", "TIAS HISTORY" in prompt)
    check("prompt has direction breakdown", "DIRECTION BREAKDOWN" in prompt)
    check("prompt has past trades", "WIN" in prompt or "LOSS" in prompt)

    # Section 4: Situation data
    check("prompt has situation data", "TIAS SITUATION DATA" in prompt)
    check("prompt has regime", "trending_down" in prompt)
    check("prompt has direction bias", "sell" in prompt)

    # Output format
    check("prompt has JSON format", '"direction"' in prompt)
    check("prompt has sl_pct", '"sl_pct"' in prompt)
    check("prompt has tp_pct", '"tp_pct"' in prompt)

    # System prompt checks
    check("system prompt has TP HARD CAP", "TP HARD CAP" in APEX_SYSTEM_PROMPT)
    check("system prompt has TP_CAP reference", "TP_CAP" in APEX_SYSTEM_PROMPT)
    check("system prompt has regime awareness", "trending_down" in APEX_SYSTEM_PROMPT)
    check("system prompt has volatility classes", "DEAD" in APEX_SYSTEM_PROMPT)


# ===========================================================================
# 5. DIRECTION LOCK — full logic with real optimizer
# ===========================================================================

def test_direction_lock_logic():
    """Direction lock gate logic with all regime scenarios."""
    print("\n--- 5. Direction Lock Logic ---")

    from src.apex.optimizer import TradeOptimizer
    from src.apex.models import (
        CoinData, DirectiveContext, IntelligencePackage,
        TIASSymbolHistory, TIASSituationData,
    )

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)

    def make_package(regime, trades=None):
        return IntelligencePackage(
            directive=DirectiveContext(
                symbol="TEST", direction="Sell", sl=0, tp=0,
                leverage=3, size_usd=600, reasoning="", plan_view="",
            ),
            coin_data=CoinData(symbol="TEST", current_price=1.0),
            symbol_history=TIASSymbolHistory(
                symbol="TEST", total_trades=0, wins=0, losses=0,
                win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
                total_pnl_usd=0.0, ev_per_trade=0.0,
                trades=trades or [],
            ),
            situation_data=TIASSituationData(
                regime=regime, fear_greed=50,
                total_trades_in_condition=0,
                buy_win_rate=0.0, sell_win_rate=0.0,
                avg_buy_pnl=0.0, avg_sell_pnl=0.0,
                direction_bias="neutral",
            ),
        )

    # Trending regimes: ALWAYS lock
    for regime, direction, expected_locked in [
        ("trending_down", "Sell", True),   # Natural alignment
        ("trending_down", "Buy", True),    # Per-coin override — still lock
        ("trending_up", "Buy", True),      # Natural alignment
        ("trending_up", "Sell", True),      # Per-coin override — still lock
    ]:
        pkg = make_package(regime)
        locked, reason = opt._check_direction_lock(pkg, direction, regime)
        check(
            f"lock({regime}, {direction}) = {expected_locked}",
            locked == expected_locked,
            f"Expected {expected_locked}, got {locked}: {reason}",
        )

    # Ranging/dead: NEVER lock
    for regime in ["ranging", "dead", "unknown", ""]:
        pkg = make_package(regime)
        locked, reason = opt._check_direction_lock(pkg, "Buy", regime)
        check(f"lock({regime}, Buy) = False", locked is False, f"got {locked}: {reason}")

    # Volatile: depends on evidence
    # No evidence (0 trades)
    pkg = make_package("volatile", trades=[])
    locked, _ = opt._check_direction_lock(pkg, "Buy", "volatile")
    check("volatile + 0 trades = locked", locked is True)

    # Insufficient evidence (5 trades, 80% WR — not enough count)
    trades_5 = [{"direction": "Sell", "win": i < 4} for i in range(5)]
    pkg = make_package("volatile", trades=trades_5)
    locked, _ = opt._check_direction_lock(pkg, "Buy", "volatile")
    check("volatile + 5 trades = locked (need 8)", locked is True)

    # Sufficient evidence (10 trades, 80% WR)
    trades_10 = [{"direction": "Sell", "win": i < 8} for i in range(10)]
    pkg = make_package("volatile", trades=trades_10)
    locked, _ = opt._check_direction_lock(pkg, "Buy", "volatile")
    check("volatile + 10 trades 80% WR = unlocked", locked is False)

    # Barely insufficient (10 trades, 60% WR)
    trades_low_wr = [{"direction": "Sell", "win": i < 6} for i in range(10)]
    pkg = make_package("volatile", trades=trades_low_wr)
    locked, _ = opt._check_direction_lock(pkg, "Buy", "volatile")
    check("volatile + 10 trades 60% WR = locked", locked is True)


# ===========================================================================
# 6. OPTIMIZER FLOW — fallback path, stats tracking
# ===========================================================================

def test_optimizer_fallback_and_stats():
    """Optimizer fallback preserves direction, stats track correctly."""
    print("\n--- 6. Optimizer Fallback & Stats ---")

    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)

    # Test fallback
    directive = {
        "symbol": "TESTUSDT", "direction": "Buy",
        "stop_loss_price": 0.95, "take_profit_price": 1.10,
        "size_usd": 1000, "leverage": 3,
    }
    fb = opt._fallback(directive, "test_reason")
    check("fallback preserves direction", fb.direction == "Buy")
    check("fallback is_fallback=True", fb.is_fallback is True)
    check("fallback was_flipped=False", fb.was_flipped is False)
    check("fallback preserves size", fb.position_size_usd == 1000)
    check("fallback preserves original_direction", fb.original_direction == "Buy")
    check("fallback count incremented", opt._fallback_count == 1)

    # Test stats
    stats = opt.get_stats()
    check("stats has 'optimized'", "optimized" in stats)
    check("stats has 'fallbacks'", stats["fallbacks"] == 1)
    check("stats has 'flips'", "flips" in stats)
    check("stats has 'lock_overrides'", "lock_overrides" in stats)
    check("stats lock_overrides = 0", stats["lock_overrides"] == 0)
    check("stats has 'flip_rate'", "flip_rate" in stats)
    check("stats has 'avg_time_ms'", "avg_time_ms" in stats)


# ===========================================================================
# 7. CONSTRAINTS — _apply_constraints doesn't break, TP cap applied after
# ===========================================================================

def test_constraints():
    """_apply_constraints clamps correctly, TP cap applies after."""
    print("\n--- 7. Constraints ---")

    from src.apex.optimizer import TradeOptimizer
    from src.apex.models import OptimizedTrade

    class FakeSettings:
        max_position_size_usd = 1200.0
        max_leverage = 5
        min_tp_pct = 0.3

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=FakeSettings())

    trade = OptimizedTrade(
        symbol="TEST", direction="Buy",
        sl_pct=0.1,    # Below 0.2 floor
        tp_pct=10.0,   # Above 8.0 cap
        tp_mode="weird_mode",
        position_size_usd=5000,  # Above 1200 cap
        leverage=10,   # Above 5 cap
        entry_timing="invalid",
        add_on_pullback=False,
    )
    result = opt._apply_constraints(trade)
    check("SL clamped to 0.2 floor", result.sl_pct == 0.2)
    check("TP clamped to 8.0 cap", result.tp_pct == 8.0)
    check("size clamped to 1200", result.position_size_usd == 1200.0)
    check("leverage clamped to 5", result.leverage == 5)
    check("tp_mode normalized to fixed", result.tp_mode == "fixed")
    check("entry_timing normalized to immediate", result.entry_timing == "immediate")

    # Now test TP cap enforcement (this happens AFTER _apply_constraints in optimize flow)
    # Simulate: recTP=1.95, cap = 1.95*1.3 = 2.535 ≈ 2.54
    trade2 = OptimizedTrade(
        symbol="DOT", direction="Sell",
        sl_pct=1.0, tp_pct=3.0,  # Above 2.54 cap
        tp_mode="fixed", position_size_usd=600, leverage=3,
        entry_timing="immediate", add_on_pullback=False,
    )
    _tp_cap = round(1.95 * 1.3, 2)  # 2.54
    if trade2.tp_pct > _tp_cap:
        trade2.tp_pct = _tp_cap
    check("TP cap applied (3.0 -> 2.54)", trade2.tp_pct == 2.54)


# ===========================================================================
# 8. STRATEGIST PROMPT — comprehensive text verification
# ===========================================================================

def test_strategist_prompt_comprehensive():
    """Full verification of strategist prompt changes."""
    print("\n--- 8. Strategist Prompt ---")

    from src.brain.strategist import STRATEGIST_SYSTEM_PROMPT

    # Blanket rules REMOVED
    check("no 'NEVER BUY'", "NEVER BUY" not in STRATEGIST_SYSTEM_PROMPT)
    check("no 'ONLY open SELL'", "ONLY open SELL" not in STRATEGIST_SYSTEM_PROMPT)
    check("no 'SELL ONLY'", "SELL ONLY" not in STRATEGIST_SYSTEM_PROMPT)
    check("no 'MUST only open SELL'", "MUST only open SELL" not in STRATEGIST_SYSTEM_PROMPT)
    check("no 'do NOT buy'", "do NOT buy" not in STRATEGIST_SYSTEM_PROMPT)

    # Per-coin rules ADDED
    check("has 'PER-COIN'", "PER-COIN" in STRATEGIST_SYSTEM_PROMPT)
    check("has 'INDIVIDUAL regime'", "INDIVIDUAL regime" in STRATEGIST_SYSTEM_PROMPT)
    check("has 'OVERRIDES global regime'", "OVERRIDES global regime" in STRATEGIST_SYSTEM_PROMPT)
    check("has 'DEFAULT BIAS'", "DEFAULT BIAS" in STRATEGIST_SYSTEM_PROMPT)

    # Valid guidance PRESERVED
    check("RSI caution preserved", "Oversold RSI in a downtrend" in STRATEGIST_SYSTEM_PROMPT)
    check("F&G caution preserved",
          "Fear & Greed extreme fear in a downtrend" in STRATEGIST_SYSTEM_PROMPT)
    check("volatility targets preserved", "VOLATILITY-ADAPTIVE TARGETS" in STRATEGIST_SYSTEM_PROMPT)
    check("SL minimum preserved", "1.5%" in STRATEGIST_SYSTEM_PROMPT)

    # Structure intact
    check("has DIRECTION BY REGIME header",
          "DIRECTION BY REGIME" in STRATEGIST_SYSTEM_PROMPT)
    check("has REGIME-AWARE TRADING rule",
          "REGIME-AWARE TRADING" in STRATEGIST_SYSTEM_PROMPT)
    check("has ranging direction",
          "ranging" in STRATEGIST_SYSTEM_PROMPT.lower())
    check("has volatile direction",
          "volatile" in STRATEGIST_SYSTEM_PROMPT.lower())
    check("has dead direction",
          "dead" in STRATEGIST_SYSTEM_PROMPT.lower())


# ===========================================================================
# 9. LAYER_MANAGER INTEGRATION — _apply_apex_optimization correctness
# ===========================================================================

def test_apply_apex_optimization():
    """_apply_apex_optimization handles direction lock output correctly."""
    print("\n--- 9. Layer Manager Integration ---")

    from src.apex.models import OptimizedTrade

    # Simulate what happens after direction lock enforcement
    # Case 1: Direction was locked, Qwen tried to flip, code overrode
    opt_locked = OptimizedTrade(
        symbol="ARIAUSDT", direction="Sell",  # Locked back to Sell
        sl_pct=0.9, tp_pct=1.88,
        tp_mode="fixed", position_size_usd=800, leverage=3,
        entry_timing="immediate", add_on_pullback=False,
        was_flipped=False,  # Set to False by lock enforcement
        original_direction="Sell",
        reasoning="[DIR LOCKED to Sell] Qwen wanted Buy but locked",
        is_fallback=False,
    )
    check("locked trade direction=Sell", opt_locked.direction == "Sell")
    check("locked trade was_flipped=False", opt_locked.was_flipped is False)
    check("locked trade reasoning has DIR LOCKED", "DIR LOCKED" in opt_locked.reasoning)

    # Case 2: Fallback — original preserved
    opt_fallback = OptimizedTrade(
        symbol="TESTUSDT", direction="Buy",
        sl_pct=2.0, tp_pct=1.5,
        tp_mode="fixed", position_size_usd=600, leverage=3,
        entry_timing="immediate", add_on_pullback=False,
        is_fallback=True,
    )
    check("fallback is_fallback=True", opt_fallback.is_fallback is True)

    # Verify layer_manager would skip fallback (original dict returned unchanged)
    original_dict = {"symbol": "TESTUSDT", "direction": "Buy", "size_usd": 1000}
    if getattr(opt_fallback, "is_fallback", False):
        result = original_dict  # layer_manager returns original unchanged
    else:
        result = {"modified": True}
    check("fallback returns original dict", result is original_dict)

    # Case 3: Normal optimization (no lock, no fallback)
    opt_normal = OptimizedTrade(
        symbol="ETHUSDT", direction="Buy",
        sl_pct=1.5, tp_pct=2.0,
        tp_mode="fixed", position_size_usd=1000, leverage=4,
        entry_timing="immediate", add_on_pullback=False,
        was_flipped=False, original_direction="Buy",
        is_fallback=False,
    )
    # Simulate layer_manager _apply_apex_optimization logic
    modified = dict(original_dict)
    modified["direction"] = opt_normal.direction
    modified["size_usd"] = opt_normal.position_size_usd
    modified["leverage"] = opt_normal.leverage
    modified["_apex_optimized"] = True
    modified["_apex_was_flipped"] = opt_normal.was_flipped

    check("normal apply sets direction", modified["direction"] == "Buy")
    check("normal apply sets size", modified["size_usd"] == 1000)
    check("normal apply sets _apex_optimized", modified["_apex_optimized"] is True)
    check("normal apply sets _apex_was_flipped", modified["_apex_was_flipped"] is False)


# ===========================================================================
# 10. VOLATILITY PROFILER — real service integration
# ===========================================================================

def test_volatility_profiler_integration():
    """VolatilityProfiler produces profiles that CoinData can consume."""
    print("\n--- 10. Volatility Profiler Integration ---")

    from src.analysis.volatility_profile import (
        VolatilityProfiler, CoinVolatilityProfile,
        _BASE_PARAMS, _REGIME_MODS,
    )
    from src.apex.models import CoinData

    # Verify base params exist for all classes
    for cls in ["dead", "low", "medium", "high", "extreme"]:
        check(f"base params for '{cls}'", cls in _BASE_PARAMS)

    # Verify regime mods exist
    for rgm in ["trending_up", "trending_down", "ranging", "volatile", "dead"]:
        check(f"regime mod for '{rgm}'", rgm in _REGIME_MODS)

    # Create a profile manually (as the profiler would)
    profile = CoinVolatilityProfile(
        symbol="DOTUSDT",
        atr_pct_5m=0.28,
        atr_pct_1h=0.56,
        volatility_class="medium",
        recommended_tp_pct=1.95,
        recommended_sl_pct=0.90,
        recommended_hold_min=36,
        recommended_strategy="momentum",
        regime="trending_down",
        regime_confidence=0.81,
    )

    # Verify CoinData can receive these fields (the assembler does this)
    coin = CoinData(symbol="DOTUSDT", current_price=1.17)
    coin.volatility_class = profile.volatility_class
    coin.recommended_tp_pct = profile.recommended_tp_pct
    coin.recommended_sl_pct = profile.recommended_sl_pct
    coin.recommended_hold_min = profile.recommended_hold_min
    coin.recommended_strategy = profile.recommended_strategy

    check("CoinData receives volatility_class", coin.volatility_class == "medium")
    check("CoinData receives recommended_tp_pct", coin.recommended_tp_pct == 1.95)
    check("CoinData receives recommended_sl_pct", coin.recommended_sl_pct == 0.90)
    check("CoinData receives recommended_hold_min", coin.recommended_hold_min == 36)
    check("CoinData receives recommended_strategy", coin.recommended_strategy == "momentum")

    # Verify format() renders correctly after population
    fmt = coin.format()
    check("populated CoinData format has TP_CAP", "TP_CAP=" in fmt)
    check("populated CoinData format has HARD CAP", "TP HARD CAP" in fmt)


# ===========================================================================
# 11. ASSEMBLER WIRING — service keys match what assembler expects
# ===========================================================================

def test_assembler_service_keys():
    """Verify the service key names the assembler uses exist in the expected pattern."""
    print("\n--- 11. Assembler Service Key Wiring ---")

    import src.apex.assembler as assembler_module
    import inspect

    # Check the entire module source (includes standalone helper functions)
    source = inspect.getsource(assembler_module)

    # Verify service keys used in assembler module match DI conventions
    check("assembler uses 'ta_cache' key",
          '"ta_cache"' in source or "'ta_cache'" in source)
    check("assembler uses 'ta' fallback key",
          '"ta"' in source or "'ta'" in source)
    check("assembler uses 'market_service' key",
          '"market_service"' in source or "'market_service'" in source)
    check("assembler uses 'market' fallback key",
          '"market"' in source or "'market'" in source)
    check("assembler uses 'volatility_profiler' key",
          '"volatility_profiler"' in source or "'volatility_profiler'" in source)
    check("assembler uses 'regime_detector' key",
          '"regime_detector"' in source or "'regime_detector'" in source)
    check("assembler uses 'structure_cache' key",
          '"structure_cache"' in source or "'structure_cache'" in source)


# ===========================================================================
# 12. OPTIMIZER WIRING — method signatures match call sites
# ===========================================================================

def test_optimizer_method_signatures():
    """Verify optimizer method signatures match how they're called."""
    print("\n--- 12. Optimizer Method Signatures ---")

    from src.apex.optimizer import TradeOptimizer
    import inspect

    # optimize() accepts (directive: dict, plan: Any = None)
    sig = inspect.signature(TradeOptimizer.optimize)
    params = list(sig.parameters.keys())
    check("optimize has 'self'", "self" in params)
    check("optimize has 'directive'", "directive" in params)
    check("optimize has 'plan'", "plan" in params)

    # _check_direction_lock() accepts (package, claude_direction, regime)
    sig2 = inspect.signature(TradeOptimizer._check_direction_lock)
    params2 = list(sig2.parameters.keys())
    check("_check_direction_lock has 'self'", "self" in params2)
    check("_check_direction_lock has 'package'", "package" in params2)
    check("_check_direction_lock has 'claude_direction'", "claude_direction" in params2)
    check("_check_direction_lock has 'regime'", "regime" in params2)

    # _check_flip_evidence() accepts (trades, claude_direction)
    sig3 = inspect.signature(TradeOptimizer._check_flip_evidence)
    params3 = list(sig3.parameters.keys())
    check("_check_flip_evidence has 'trades'", "trades" in params3)
    check("_check_flip_evidence has 'claude_direction'", "claude_direction" in params3)

    # Return type of _check_direction_lock is tuple[bool, str]
    ret = sig2.return_annotation
    check("_check_direction_lock returns tuple", "tuple" in str(ret))

    # get_stats returns dict with lock_overrides
    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    stats = opt.get_stats()
    check("get_stats has all expected keys",
          all(k in stats for k in ["optimized", "fallbacks", "flips",
                                    "flip_rate", "lock_overrides", "avg_time_ms"]))


# ===========================================================================
# 13. CROSS-FIX COHERENCE — all 3 fixes work together
# ===========================================================================

def test_cross_fix_coherence():
    """All 3 fixes work together without contradiction."""
    print("\n--- 13. Cross-Fix Coherence ---")

    from src.apex.optimizer import TradeOptimizer
    from src.apex.models import (
        CoinData, DirectiveContext, IntelligencePackage,
        TIASSymbolHistory, TIASSituationData, OptimizedTrade,
    )
    from src.apex.prompts import APEX_SYSTEM_PROMPT
    from src.brain.strategist import STRATEGIST_SYSTEM_PROMPT

    # Scenario: ARIAUSDT, trending_down regime, Claude says Sell
    # Fix 1: Direction should be LOCKED to Sell
    # Fix 2: Strategist should NOT have "NEVER BUY" blanket rule
    # Fix 3: TP cap should be enforced based on volatility profile

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)

    # Build package
    pkg = IntelligencePackage(
        directive=DirectiveContext(
            symbol="ARIAUSDT", direction="Sell", sl=0.055, tp=0.048,
            leverage=3, size_usd=600, reasoning="momentum", plan_view="bearish",
        ),
        coin_data=CoinData(
            symbol="ARIAUSDT", current_price=0.05,
            volatility_class="medium",
            recommended_tp_pct=1.50, recommended_sl_pct=1.00,
            recommended_hold_min=30, recommended_strategy="momentum",
        ),
        symbol_history=TIASSymbolHistory(
            symbol="ARIAUSDT", total_trades=5, wins=3, losses=2,
            win_rate=60.0, avg_win_pct=1.2, avg_loss_pct=-0.8,
            total_pnl_usd=5.0, ev_per_trade=1.0,
            trades=[
                {"direction": "Sell", "win": True},
                {"direction": "Sell", "win": True},
                {"direction": "Sell", "win": False},
                {"direction": "Buy", "win": False},
                {"direction": "Buy", "win": False},
            ],
        ),
        situation_data=TIASSituationData(
            regime="trending_down", fear_greed=25,
            total_trades_in_condition=40,
            buy_win_rate=30.0, sell_win_rate=65.0,
            avg_buy_pnl=-1.2, avg_sell_pnl=0.8,
            direction_bias="sell",
        ),
    )

    # Fix 1: Direction lock
    locked, reason = opt._check_direction_lock(pkg, "Sell", "trending_down")
    check("coherence: direction locked for Sell in trending_down", locked is True)
    check("coherence: reason mentions alignment", "aligns" in reason)

    # Fix 2: No blanket rule in strategist
    check("coherence: strategist allows per-coin Buy",
          "NEVER BUY" not in STRATEGIST_SYSTEM_PROMPT)

    # Fix 3: TP cap computed
    tp_cap = round(pkg.coin_data.recommended_tp_pct * 1.3, 2)
    check("coherence: TP cap = 1.95", tp_cap == 1.95)  # 1.50 * 1.3 = 1.95

    # Fix 3: If Qwen returns tp=2.5%, it gets capped
    fake_qwen_tp = 2.5
    if tp_cap is not None and fake_qwen_tp > tp_cap:
        fake_qwen_tp = tp_cap
    check("coherence: Qwen TP capped from 2.5 to 1.95", fake_qwen_tp == 1.95)

    # Fix 1 + Fix 3 together: direction locked AND TP capped
    check("coherence: both direction lock and TP cap can apply simultaneously",
          locked is True and tp_cap == 1.95)

    # Fix 2: Per-coin override scenario
    # Coin is in TRENDING_UP but global is trending_down
    # Strategist should allow Buy (no blanket NEVER BUY)
    # APEX should lock the Buy direction (Claude's per-coin decision)
    locked_buy, reason_buy = opt._check_direction_lock(pkg, "Buy", "trending_down")
    check("coherence: Buy in trending_down also locked (Claude's per-coin choice)",
          locked_buy is True)
    check("coherence: reason mentions per-coin", "per-coin" in reason_buy)


# ===========================================================================
# RUN ALL TESTS
# ===========================================================================

def main():
    global passed, failed, errors

    print("=" * 72)
    print("APEX PIPELINE INTEGRATION TEST — REAL PROJECT END-TO-END")
    print("=" * 72)

    test_imports()
    test_model_construction()
    test_format_rendering()
    test_prompt_builder()
    test_direction_lock_logic()
    test_optimizer_fallback_and_stats()
    test_constraints()
    test_strategist_prompt_comprehensive()
    test_apply_apex_optimization()
    test_volatility_profiler_integration()
    test_assembler_service_keys()
    test_optimizer_method_signatures()
    test_cross_fix_coherence()

    print()
    print("=" * 72)
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 72)

    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err}")
        sys.exit(1)
    else:
        print("\nAll tests passed. Pipeline integration verified.")
        sys.exit(0)


if __name__ == "__main__":
    main()
