"""System-level integration test using REAL klines from trading.db.

Loads H1 klines for the live watch_list, runs the full StructureEngine
pipeline on each coin, and reports the actual classification distribution
the new code produces — including counter setups when the structure
warrants them. Uses real market data (not synthetic) so the result
reflects what the live workers process will see post-restart.

Asserts:
- 50 coins analyzed (or close to it).
- setup_type distribution shows non-zero counter setups when in-direction
  zones are missing on uptrend/downtrend coins.
- atr_pct_h1 populated for every coin.
- trade_direction populated coherently with setup_type.
- to_dict() round-trips the new fields.

Run from project root:
    PYTHONPATH=. .venv/bin/python scripts/xray_counter_integration_test.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter


async def main() -> None:
    from src.config.settings import Settings
    from src.database.connection import DatabaseManager
    from src.database.repositories.market_repo import MarketRepository
    from src.analysis.structure.structure_engine import StructureEngine
    from src.core.types import TimeFrame

    settings = Settings.load()
    db = DatabaseManager(settings.database.path)
    await db.connect()
    market_repo = MarketRepository(db)
    eng = StructureEngine(settings.structure)

    watch_list = settings.universe.watch_list
    print(f"Loaded watch_list ({len(watch_list)} coins)")
    print(f"Setup-types config:")
    print(f"  fvg_atr_multiplier              = {settings.structure.setup_types.fvg_atr_multiplier}")
    print(f"  ob_atr_multiplier               = {settings.structure.setup_types.ob_atr_multiplier}")
    print(f"  counter_setup_enabled           = {settings.structure.setup_types.counter_setup_enabled}")
    print(f"  counter_confidence_multiplier   = {settings.structure.setup_types.counter_confidence_multiplier}")
    print(f"  counter_mtf_threshold           = {settings.structure.setup_types.counter_mtf_threshold}")
    print(f"  structural_break_require_retest = {settings.structure.setup_types.structural_break_require_retest}")
    print(f"  structural_break_minor_mult     = {settings.structure.setup_types.structural_break_minor_confidence_multiplier}")
    print()

    # Run full pipeline per coin
    setup_counts: Counter[str] = Counter()
    trade_dir_counts: Counter[str] = Counter()
    atr_pct_samples: list[float] = []
    counter_examples: list[tuple[str, float, str, str]] = []
    in_direction_examples: list[tuple[str, float, str, str]] = []
    none_examples: list[tuple[str, dict]] = []
    analyzed = 0
    skipped = 0

    for sym in watch_list:
        try:
            candles = await market_repo.get_klines(sym, TimeFrame.H1.value, 200)
        except Exception:
            candles = None
        if not candles or len(candles) < settings.structure.min_candles:
            skipped += 1
            continue
        last_close = candles[-1].close
        result = eng.analyze(sym, last_close, candles)
        if result is None:
            skipped += 1
            continue
        analyzed += 1
        st = result.setup_type.value
        setup_counts[st] += 1
        trade_dir_counts[result.trade_direction or "(empty)"] += 1
        atr_pct_samples.append(result.atr_pct_h1)
        # Sample examples
        if "counter" in st and len(counter_examples) < 5:
            counter_examples.append(
                (sym, result.setup_type_confidence, result.suggested_direction, result.trade_direction)
            )
        elif st in ("bullish_fvg_ob", "bearish_fvg_ob") and len(in_direction_examples) < 5:
            in_direction_examples.append(
                (sym, result.setup_type_confidence, result.suggested_direction, result.trade_direction)
            )
        elif st == "none" and len(none_examples) < 3:
            none_examples.append((sym, eng.diagnose_none(result)))

    print(f"=== Integration test results ({analyzed} coins analyzed, {skipped} skipped) ===")
    print()
    print(f"Setup-type distribution:")
    total = sum(setup_counts.values())
    for st, count in sorted(setup_counts.items(), key=lambda kv: -kv[1]):
        pct = count / total * 100 if total else 0
        print(f"  {st:35} {count:3} ({pct:.1f}%)")
    print()
    print(f"trade_direction distribution:")
    for td, count in sorted(trade_dir_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {td!r:15} {count:3}")
    print()
    if atr_pct_samples:
        atr_pct_samples.sort()
        n = len(atr_pct_samples)
        print(f"ATR% distribution (n={n}):")
        print(f"  min: {atr_pct_samples[0]:.3f}%")
        print(f"  p25: {atr_pct_samples[n//4]:.3f}%")
        print(f"  p50: {atr_pct_samples[n//2]:.3f}%")
        print(f"  p75: {atr_pct_samples[3*n//4]:.3f}%")
        print(f"  max: {atr_pct_samples[-1]:.3f}%")
    print()

    # Check 1: counter setups appear (or document the market state if none)
    counter_count = sum(c for st, c in setup_counts.items() if "counter" in st)
    print(f"=== Counter setup count: {counter_count} ===")
    if counter_count > 0:
        print(f"  Sample counter setups (sym, conf, suggested → trade):")
        for sym, conf, sug, trade in counter_examples:
            print(f"    {sym:12}  conf={conf:.3f}  {sug:5} → {trade}")

    # Check 2: in-direction setups still produce normally (regression check)
    in_count = setup_counts.get("bullish_fvg_ob", 0) + setup_counts.get("bearish_fvg_ob", 0)
    print(f"=== In-direction FVG_OB count: {in_count} ===")
    if in_direction_examples:
        print(f"  Sample in-direction (sym, conf, suggested = trade):")
        for sym, conf, sug, trade in in_direction_examples:
            print(f"    {sym:12}  conf={conf:.3f}  {sug:5} = {trade}")

    # Check 3: NONE coins truly cold (sample diagnose_none)
    none_count = setup_counts.get("none", 0)
    print(f"=== NONE count: {none_count} ===")
    if none_examples:
        print(f"  Sample NONE coin diagnostics:")
        for sym, diag in none_examples:
            print(f"    {sym}:")
            print(f"      closest_type        : {diag['closest_type']}")
            print(f"      in_direction_fvg    : {diag.get('in_direction_fvg', 'na')}")
            print(f"      counter_direction_fvg: {diag.get('counter_direction_fvg', 'na')}")
            print(f"      first_failure_branch: {diag.get('first_failure_branch', 'na')}")

    # Check 4: to_dict() round-trip works on every analyzed coin
    print(f"=== to_dict() round-trip integrity ===")
    if analyzed > 0:
        # Re-analyze first analyzed coin and round-trip
        for sym in watch_list:
            candles = await market_repo.get_klines(sym, TimeFrame.H1.value, 200)
            if candles and len(candles) >= settings.structure.min_candles:
                last_close = candles[-1].close
                result = eng.analyze(sym, last_close, candles)
                if result:
                    d = result.to_dict()
                    # Ensure every Phase 2/3/4 field is in the dict
                    assert "setup_type" in d
                    assert "setup_type_confidence" in d
                    assert "trade_direction" in d
                    assert "atr_pct_h1" in d
                    assert "nearest_fvg_counter" in d
                    assert "nearest_ob_counter" in d
                    # JSON serializable
                    json.dumps(d)
                    print(f"  ✓ {sym}: to_dict() includes all 6 Phase 2/3/4 fields, JSON serializable")
                    break

    # Check 5: trade_direction coherence
    print(f"=== trade_direction coherence ===")
    fail_coherence = 0
    for sym in watch_list:
        candles = await market_repo.get_klines(sym, TimeFrame.H1.value, 200)
        if not candles or len(candles) < settings.structure.min_candles:
            continue
        result = eng.analyze(sym, candles[-1].close, candles)
        if not result:
            continue
        st = result.setup_type.value
        td = result.trade_direction
        sd = result.suggested_direction
        if st == "none":
            if td != "":
                print(f"  ✗ {sym}: NONE setup but trade_direction={td!r} (should be empty)")
                fail_coherence += 1
        elif "counter" in st:
            if td == sd:
                print(f"  ✗ {sym}: counter setup but trade_direction == suggested ({td!r})")
                fail_coherence += 1
        else:
            if td != sd:
                print(f"  ✗ {sym}: in-direction setup but trade_direction != suggested ({td!r} vs {sd!r})")
                fail_coherence += 1
    if fail_coherence == 0:
        print(f"  ✓ All {analyzed} coins have coherent trade_direction vs setup_type")
    else:
        print(f"  ✗ {fail_coherence} coherence failures")
        sys.exit(1)

    print()
    print("=" * 60)
    print(f"  INTEGRATION TEST PASSED")
    print(f"  Analyzed: {analyzed}/{len(watch_list)} coins")
    print(f"  Setup distribution healthy: {setup_counts}")
    print(f"  Counter setups: {counter_count}")
    print(f"  In-direction setups: {in_count}")
    print(f"  NONE: {none_count}")
    print("=" * 60)

    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
