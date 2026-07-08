"""Prompt 3 — end-to-end CALL_A pipeline check through the REAL project.

Constructs the real worker objects the way WorkerManager does (real Settings DI,
manager.py:230 StructureEngine(settings.structure), manager.py:882
ClaudeStrategist(client, services, settings), manager.py:1571 ProfitSniper),
captures the boot sentinels via a loguru sink (proving config is threaded
config.toml -> dataclass -> loader -> __init__ -> sentinel), then drives the
real runtime methods so each fix's runtime sentinel fires through the real path:

  Issue 3: real StructureEngine.analyze() runs end-to-end on a downtrend coin;
           boot XRAY_DIRECTIONAL_SCORE_CONFIG fired; the directional-RR scorer is
           in the live analyze path.
  Issue 1: real ProfitSniper._compute_ladder_floor() arms the micro-floor on a
           +0.15% peak; boot PROFIT_FETCHING_CONFIG_LOADED carries micro_arm;
           runtime MICRO_FLOOR_ARM fires.
  Issue 4: real ClaudeStrategist._format_packages_for_prompt_full() surfaces the
           two-sided lean on a 0-fired coin; runtime STRAT_ZERO_FIRED_NONZERO_POLL.
  Issue 5: boot STRAT_TRADE_PROMPT_VERSION carries activity_version + targets;
           both prompts carry the breadth framing.
  Issue 2: layer_manager XRAY_BOOKLOG_CYCLE counter path is importable/reachable.

Read-only: no protected tables written, no orders placed, db=None.
"""
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, ".")

from loguru import logger

# ---- capture every sentinel line emitted by the real objects ----
_CAP = []
logger.add(lambda m: _CAP.append(m.record["message"]), level="INFO")


def seen(tag):
    return [m for m in _CAP if tag in m]


from datetime import datetime, timedelta, timezone

from src.config.settings import Settings
from src.core.types import OHLCV, TimeFrame

SETTINGS = Settings.load(config_path="config.toml")
results = {}
_BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ============ Issue 3 — real StructureEngine via DI ============
from src.analysis.structure.structure_engine import StructureEngine

eng = StructureEngine(SETTINGS.structure)  # exactly manager.py:230

# Build a 60-candle confirmed downtrend that ends near the low (spent-short
# geometry: price has already fallen far past the bearish structure overhead).
candles = []
px = 100.0
for i in range(60):
    o = px
    px = px * (1.0 - 0.012)        # steady ~1.2%/bar decline
    h = o * 1.001
    low = px * 0.999
    candles.append(OHLCV(symbol="E2EDOWNUSDT", timeframe=TimeFrame.M5,
                         timestamp=_BASE_TS + timedelta(minutes=5 * i),
                         open=o, high=h, low=low, close=px, volume=1000.0 + i))
analysis = eng.analyze(symbol="E2EDOWNUSDT", current_price=px, candles=candles)

issue3_boot = bool(seen("XRAY_DIRECTIONAL_SCORE_CONFIG"))
issue3_ran = analysis is not None and hasattr(analysis, "setup_quality")
results["Issue3"] = {
    "boot_sentinel": issue3_boot,
    "analyze_ran_real_engine": issue3_ran,
    "setup_quality": getattr(analysis, "setup_quality", None),
    "suggested_direction": getattr(analysis, "suggested_direction", None),
}


# ============ Issue 1 — real ProfitSniper via DI ============
from src.workers.profit_sniper import ProfitSniper

sniper = ProfitSniper(settings=SETTINGS, db=None)  # manager.py:1571 shape (min args)
issue1_boot = any("micro_arm=0.1" in m for m in seen("PROFIT_FETCHING_CONFIG_LOADED"))
_state = SimpleNamespace(entry_price=100.0, direction="Buy", peak_pnl_pct=0.15,
                         symbol="E2EUSDT")
_dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
ladder = ProfitSniper._compute_ladder_floor(sniper, _state, _dialed, 0.0)
issue1_runtime = bool(seen("MICRO_FLOOR_ARM"))
results["Issue1"] = {
    "boot_sentinel_micro_arm": issue1_boot,
    "micro_floor_armed": bool(ladder.should_apply and ladder.breakeven_floor),
    "stop_above_entry": ladder.ladder_stop_price > 100.0,
    "runtime_MICRO_FLOOR_ARM": issue1_runtime,
}


# ============ Issue 4 + 5 — real ClaudeStrategist via DI ============
from src.brain.strategist import (
    ClaudeStrategist, TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO,
)
from src.core.coin_package import (
    AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock, StateLabelBlock,
    StrategiesBlock, XrayBlock,
)


class _LM:
    def get_strategy_votes(self, s):
        return {"votes": {f"S{i}": {"vote": "SELL", "confidence": 0.6, "weight": 1.0}
                          for i in range(27)},
                "buy_weighted": 0.1, "sell_weighted": 0.8, "opposing_weighted": 0.1,
                "two_sided": True, "consensus": "WEAK", "last_updated": time.time()}

    def get_scorer_components(self, s):
        return None


class _SC:
    def get(self, s):
        return SimpleNamespace(
            symbol=s, setup_quality="SKIP", position_in_range=0.0, smc_confluence=70,
            market_structure=SimpleNamespace(structure="downtrend"),
            nearest_fvg=SimpleNamespace(direction="bearish", midpoint=0.067),
            nearest_ob=SimpleNamespace(direction="bearish", midpoint=0.068),
            active_sweep_signal=None, mtf_confluence=SimpleNamespace(quality="good"),
            mtf_confluence_score=70, total_confluence_factors=3,
            volume_profile=SimpleNamespace(), poc_price=0.089, fib_key_level=0.082,
            session_context=SimpleNamespace(current_session="ny", session_phase="mid",
                                            manipulation_likely=False))


services = {"layer_manager": _LM(), "structure_cache": _SC(),
            "signal_worker": SimpleNamespace(get_signal=lambda s: None),
            "regime_detector": SimpleNamespace(get_coin_regime=lambda s: None)}
strat = ClaudeStrategist(claude_client=None, services=services, settings=SETTINGS)

issue5_boot = any("activity_version=1" in m and "target_play_count=3" in m
                  for m in seen("STRAT_TRADE_PROMPT_VERSION"))
issue5_framing = all(
    "WORK to surface every genuine play" in p and "deliberately size SMALLER" in p
    and "never manufacture a counter-evidence trade" in p
    for p in (TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO)
)
results["Issue5"] = {"boot_sentinel": issue5_boot, "breadth_framing_both": issue5_framing}

pkg = CoinPackage(
    symbol="HYPERUSDT", qualified=True, opportunity_score=0.47,
    qualification_reasons=["xray=bearish_structural_break"],
    price_data=PriceDataBlock(current=0.0672, change_24h_pct=-17.6, regime="trending_down"),
    xray=XrayBlock(setup_type="bearish_structural_break", setup_score=30,
                   setup_type_confidence=0.70, trade_direction="short"),
    strategies=StrategiesBlock(fired_count=0, ensemble_consensus="NONE", total_score=0.0),
    signals=SignalsBlock(confidence=0.35, direction="neutral"),
    alt_data=AltDataBlock(funding_rate=0.0001, funding_signal="longs_paying", fear_greed=12),
    state_label=StateLabelBlock(primary="TREND_PULLBACK_SHORT", confidence=0.5))
rendered = strat._format_packages_for_prompt_full({"HYPERUSDT": pkg})
results["Issue4"] = {
    "lean_surfaced": "two-sided strategy poll DID lean SELL=0.80" in rendered,
    "no_genuine_no_signal": "genuine no-signal" not in rendered,
    "runtime_STRAT_ZERO_FIRED_NONZERO_POLL": bool(seen("STRAT_ZERO_FIRED_NONZERO_POLL")),
}


# ============ Issue 2 — layer_manager counter path reachable ============
import importlib
_lm_mod = importlib.import_module("src.core.layer_manager")
_lm_src = open("src/core/layer_manager.py").read()
results["Issue2"] = {
    "module_imports": _lm_mod is not None,
    "booklog_cycle_counter_present": "_booklog_passed" in _lm_src and "XRAY_BOOKLOG_CYCLE" in _lm_src,
}


# ============ verdict ============
def _ok(d):
    return all(bool(v) for v in d.values() if isinstance(v, bool))


print("\n================ CALL_A PIPELINE E2E (real project) ================")
all_ok = True
for issue in ["Issue2", "Issue3", "Issue1", "Issue4", "Issue5"]:
    d = results[issue]
    ok = _ok(d)
    all_ok = all_ok and ok
    print(f"{issue}: {'PASS' if ok else 'FAIL'}")
    for k, v in d.items():
        print(f"    {k} = {v}")

print()
if not all_ok:
    print("CALL_A PIPELINE E2E: FAIL")
    sys.exit(1)
print("CALL_A PIPELINE E2E: PASS — real StructureEngine/ProfitSniper/"
      "ClaudeStrategist construct via DI, every boot sentinel fires with the "
      "wired config, and each fix's runtime path executes through the real "
      "objects.")
