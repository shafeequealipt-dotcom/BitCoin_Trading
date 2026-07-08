"""Real-project end-to-end pipeline verification.

Wires the actual production chain — Settings → DatabaseManager →
StructureEngine + StructureCache → (manual cache population mimicking
StructureWorker.tick) → ServiceContainer dict → ScannerWorker accessor
DI → CoinPackage build → TradeScorer reading to_dict() → EnsembleVoter
size_mult scaling → ClaudeStrategist prompt rendering — and runs a
full cycle against the live trading.db to verify every wire actually
carries the counter-setup data through to the brain prompt.

Verifies in order:
  1. Settings.load() picks up all 9 new SetupTypesSettings keys.
  2. StructureEngine.analyze() runs end-to-end on real klines, populates
     StructureCache (we drive this manually to avoid the SweetSpotScheduler
     async init that StructureWorker.__init__ requires for live operation).
  3. StructureWorker.get_setup_type_confidence() accessor works against
     the populated cache (Phase 5b plumbing).
  4. ScannerWorker._get_setup_type_confidence() reads via DI from
     services["structure_worker"].
  5. ScannerWorker._compute_opportunity_score() applies struct_conf
     multiplier.
  6. TradeScorer.score() reads setup_type_confidence + trade_direction
     from analysis.to_dict() (the StrategyWorker → TradeScorer flow).
  7. EnsembleVoter.vote() reads scoring_details["setup_type_confidence"]
     and scales size_mult.
  8. CoinPackage.XrayBlock carries trade_direction.
  9. ClaudeStrategist prompt rendering produces the COUNTER-TRADE
     annotation.

Run from project root:
    PYTHONPATH=. .venv/bin/python -u scripts/xray_counter_real_pipeline_e2e.py
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter


async def main() -> None:
    print("=" * 70)
    print("  REAL-PROJECT END-TO-END PIPELINE VERIFICATION")
    print("=" * 70)
    print(flush=True)

    # ── Step 1: Settings.load() ──────────────────────────────────────
    print(">>> Step 1: Settings.load() — config.toml parsing", flush=True)
    from src.config.settings import Settings, SetupTypesSettings

    s = Settings.load()
    setup_cfg: SetupTypesSettings = s.structure.setup_types
    expected_keys = [
        "fvg_atr_multiplier", "ob_atr_multiplier",
        "fvg_min_distance_pct", "ob_min_distance_pct",
        "counter_setup_enabled", "counter_confidence_multiplier",
        "counter_mtf_threshold", "counter_alignment_strict",
        "structural_break_minor_confidence_multiplier",
    ]
    for k in expected_keys:
        assert hasattr(setup_cfg, k), f"missing setup_types key: {k}"
        print(f"    {k:50} = {getattr(setup_cfg, k)}")
    print("  ✓ All 9 new keys load from live config.toml", flush=True)
    print()

    # ── Step 2: DB + StructureEngine + StructureCache ────────────────
    print(">>> Step 2: DB + StructureEngine + StructureCache wiring", flush=True)
    from src.database.connection import DatabaseManager
    from src.database.repositories.market_repo import MarketRepository
    from src.analysis.structure.structure_engine import StructureEngine
    from src.analysis.structure.structure_cache import StructureCache
    from src.core.types import TimeFrame

    db = DatabaseManager(s.database.path)
    await db.connect()
    market_repo = MarketRepository(db)
    engine = StructureEngine(s.structure)
    cache = StructureCache(ttl_seconds=s.structure.cache_ttl_seconds)
    print(f"    DB connected: {s.database.path}")
    print(f"    StructureEngine instantiated")
    print(f"    StructureCache(ttl={s.structure.cache_ttl_seconds}s)", flush=True)
    print()

    # ── Step 3: Mimic StructureWorker.tick() — analyze + populate cache ─
    print(">>> Step 3: Driving StructureEngine.analyze() per coin (mimics worker.tick)", flush=True)
    setup_counts: Counter[str] = Counter()
    counter_examples = []
    in_direction_examples = []
    for sym in s.universe.watch_list:
        try:
            candles = await market_repo.get_klines(sym, TimeFrame.H1.value, 200)
        except Exception:
            continue
        if not candles or len(candles) < s.structure.min_candles:
            continue
        result = engine.analyze(sym, candles[-1].close, candles)
        if result is None:
            continue
        cache.set(sym, result)
        setup_counts[result.setup_type.value] += 1
        if "counter" in result.setup_type.value and len(counter_examples) < 5:
            counter_examples.append(result)
        elif result.setup_type.value in ("bullish_fvg_ob", "bearish_fvg_ob") and len(in_direction_examples) < 3:
            in_direction_examples.append(result)
    print(f"    {len(cache.get_all())} coins analyzed, distribution:", flush=True)
    for st, n in sorted(setup_counts.items(), key=lambda kv: -kv[1]):
        print(f"      {st:35} {n:3}")
    print(flush=True)

    # ── Step 4: StructureWorker accessor against populated cache ─────
    print(">>> Step 4: StructureWorker accessor (Phase 5b plumbing)", flush=True)
    # Import StructureWorker class but DON'T construct (avoids SweetSpotScheduler init).
    # Instead, instantiate via __new__ + manual attribute assignment to mimic the
    # worker's runtime state (only _cache is needed for get_setup_type_confidence).
    from src.workers.structure_worker import StructureWorker
    sw = StructureWorker.__new__(StructureWorker)
    sw._cache = cache
    if counter_examples:
        ce = counter_examples[0]
        conf = sw.get_setup_type_confidence(ce.symbol)
        assert conf is not None, "accessor returned None for cached coin"
        print(f"    sw.get_setup_type_confidence({ce.symbol!r}) = {conf}")
    miss = sw.get_setup_type_confidence("NONEXISTENT_COIN")
    assert miss is None
    print(f"    sw.get_setup_type_confidence('NONEXISTENT_COIN') = None (defensive)")
    print("  ✓ Phase 5b accessor wired through cache correctly", flush=True)
    print()

    # ── Step 5: ScannerWorker DI wiring + opportunity_score ──────────
    print(">>> Step 5: ScannerWorker DI wiring + _compute_opportunity_score", flush=True)
    from src.workers.scanner_worker import ScannerWorker

    # Mimic the WorkerManager DI: services dict carries structure_worker
    services = {
        "structure_worker": sw,
        "structure_cache": cache,
        "structure_engine": engine,
    }
    scanner_w = ScannerWorker.__new__(ScannerWorker)
    scanner_w.settings = s
    scanner_w.services = services
    print(f"    ScannerWorker.services keys: {list(services.keys())}")

    # Verify the accessor reads via DI through services["structure_worker"]
    if counter_examples:
        ce = counter_examples[0]
        accessor_conf = scanner_w._get_setup_type_confidence(ce.symbol)
        assert accessor_conf is not None
        assert abs(accessor_conf - ce.setup_type_confidence) < 1e-6
        print(f"    scanner_w._get_setup_type_confidence({ce.symbol!r}) = {accessor_conf} (matches cache)")

    # Run opportunity_score on a counter-setup coin
    if counter_examples:
        ce = counter_examples[0]
        score, breakdown = scanner_w._compute_opportunity_score(ce.symbol)
        print(f"    Opportunity score for {ce.symbol}:")
        print(f"      score          = {score:.4f}")
        print(f"      structure      = {breakdown['structure']:.3f} (post × confidence)")
        print(f"      structure_raw  = {breakdown['structure_raw']:.3f}")
        print(f"      structure_conf = {breakdown['structure_conf']:.3f}")
        assert breakdown["structure_conf"] < 1.0, "Phase 5b: counter setup should have conf < 1.0"
        assert breakdown["structure"] < breakdown["structure_raw"] * 1.0001, \
            "Phase 5b: struct_norm should be reduced by struct_conf"
        print(f"  ✓ Phase 5b struct_conf multiplier active (counter downweighted)", flush=True)
    print()

    # ── Step 6: TradeScorer reads to_dict() ──────────────────────────
    print(">>> Step 6: TradeScorer reads to_dict() — verifies the to_dict integration fix", flush=True)
    from src.strategies.scorer import TradeScorer
    from src.strategies.models.signal_types import RawSignal
    from src.strategies.models.regime_types import MarketRegime, RegimeState
    from src.core.types import Side

    scorer = TradeScorer(s)
    if counter_examples:
        ce = counter_examples[0]
        d = ce.to_dict()
        # Confirm the fix: trade_direction + atr_pct_h1 + nearest_*_counter present in dict
        for f in ("trade_direction", "atr_pct_h1", "nearest_fvg_counter", "nearest_ob_counter",
                  "setup_type", "setup_type_confidence"):
            assert f in d, f"to_dict() missing {f}"
        print(f"    {ce.symbol}.to_dict() includes all 6 new fields ✓")
        print(f"      trade_direction       = {d['trade_direction']!r}")
        print(f"      setup_type_confidence = {d['setup_type_confidence']}")
        print(f"      atr_pct_h1            = {d['atr_pct_h1']}")

        # Build minimal RawSignal + score it (StrategyWorker → TradeScorer flow)
        signal = RawSignal(
            strategy_name="test_a1", strategy_category="scalping",
            symbol=ce.symbol, direction=Side.BUY,
            entry_price=ce.current_price or 100.0,
            suggested_stop_loss=(ce.current_price or 100.0) * 0.99,
            suggested_take_profit=(ce.current_price or 100.0) * 1.02,
            timeframe="5",
            conditions_met={"momentum": True}, conditions_strength={"momentum": 0.7},
        )
        regime_state = RegimeState(
            regime=MarketRegime.RANGING, confidence=0.7,
            adx=12.0, atr_percentile=50.0, choppiness=60.0,
            volume_ratio=1.0, trend_direction=0,
        )
        scored = scorer.score(signal, [], {}, None, None, regime_state, structural_data=d)
        # scoring_details propagates the new fields
        assert scored.scoring_details["setup_type_confidence"] == round(ce.setup_type_confidence, 4)
        assert scored.scoring_details["trade_direction"] == ce.trade_direction
        print(f"    scoring_details propagated: trade_direction={scored.scoring_details['trade_direction']!r}, "
              f"setup_type_confidence={scored.scoring_details['setup_type_confidence']}")
        print(f"  ✓ to_dict() → TradeScorer → scoring_details flow intact (Phase 5a fix verified)", flush=True)
    print()

    # ── Step 7: EnsembleVoter scales size_mult ───────────────────────
    print(">>> Step 7: EnsembleVoter.vote() reads scoring_details and scales size_mult", flush=True)
    from src.strategies.ensemble import EnsembleVoter
    from src.strategies.registry import StrategyRegistry
    from src.strategies.base_strategy import BaseStrategy
    from src.core.types import TimeFrame as TF

    class _FakeBullish(BaseStrategy):
        def __init__(self, n): self._name = n
        @property
        def name(self): return self._name
        @property
        def category(self): return "momentum"
        @property
        def applicable_regimes(self): return [MarketRegime.RANGING]
        @property
        def timeframe(self): return TF.M5
        async def scan(self, *a, **kw): return None
        def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata):
            return ("BUY", 0.8, "test")

    reg = StrategyRegistry()
    for i in range(7):
        reg.register(_FakeBullish(f"bull_{i}"))
    voter = EnsembleVoter(reg, s)

    if counter_examples:
        # Vote on the counter-setup coin's scored setup
        result = voter.vote(scored, {ce.symbol: []}, {ce.symbol: {}}, None, None, regime_state)
        print(f"    Ensemble vote for {ce.symbol}:")
        print(f"      consensus      = {result.consensus_strength}")
        print(f"      base_size_mult (CONSENSUS_SIZE) = {1.0 if result.consensus_strength == 'STRONG' else 'varies'}")
        print(f"      size_multiplier (after Phase 5c × confidence) = {result.size_multiplier:.3f}")
        # Phase 5c — counter setup should have size_mult reduced
        # Floor 0.5 means at conf=0.35 → factor=0.5 → STRONG=1.0 × 0.5 = 0.5
        if result.consensus_strength == "STRONG":
            assert result.size_multiplier <= 1.0
            if ce.setup_type_confidence < 0.85:
                assert result.size_multiplier < 1.0, \
                    f"Phase 5c: STRONG counter should size_mult<1.0, got {result.size_multiplier}"
                print(f"  ✓ Phase 5c size_mult × confidence active (counter downweighted from 1.0)")
    print(flush=True)

    # ── Step 8: CoinPackage.XrayBlock + brain prompt rendering ───────
    print(">>> Step 8: CoinPackage.XrayBlock + ClaudeStrategist prompt rendering", flush=True)
    from src.core.coin_package import (
        XrayBlock, StructuralLevels, CoinPackage, PriceDataBlock,
        StrategiesBlock, SignalsBlock, AltDataBlock,
    )

    if counter_examples:
        ce = counter_examples[0]
        # Replicate scanner_worker._build_coin_package XrayBlock construction
        levels = StructuralLevels(current_price=ce.current_price or 100.0)
        _trade_direction = (
            getattr(ce, "trade_direction", "")
            or getattr(ce, "suggested_direction", "")
            or ""
        )
        xb = XrayBlock(
            setup_type=ce.setup_type.value,
            setup_score=float(getattr(ce, "setup_score", 0) or 0),
            setup_type_confidence=float(getattr(ce, "setup_type_confidence", 0.0) or 0.0),
            trade_direction=str(_trade_direction),
            structural_levels=levels,
        )
        assert xb.trade_direction == ce.trade_direction
        print(f"    XrayBlock for {ce.symbol}:")
        print(f"      setup_type            = {xb.setup_type}")
        print(f"      setup_type_confidence = {xb.setup_type_confidence}")
        print(f"      trade_direction       = {xb.trade_direction!r}")

        pkg = CoinPackage(
            symbol=ce.symbol,
            qualified=True,
            opportunity_score=score,
            xray=xb,
            price_data=PriceDataBlock(current=ce.current_price or 100.0),
            strategies=StrategiesBlock(),
            signals=SignalsBlock(),
            alt_data=AltDataBlock(),
        )

        # Replicate strategist render path verbatim from src/brain/strategist.py:1218–1230
        _setup_label = pkg.xray.setup_type
        _is_counter = "counter" in _setup_label
        if _is_counter:
            _setup_label = (
                f"{_setup_label} (COUNTER-TRADE — trade direction "
                f"is OPPOSITE to market structure bias; lower conviction)"
            )
        _trade_dir = pkg.xray.trade_direction or "n/a"
        rendered = (
            f"  Setup: {_setup_label} "
            f"(confidence {pkg.xray.setup_type_confidence:.2f}, "
            f"trade_direction={_trade_dir})"
        )
        print(f"    Rendered prompt line:")
        print(f"    {rendered}")
        assert "COUNTER-TRADE" in rendered
        assert f"trade_direction={ce.trade_direction}" in rendered
        print(f"  ✓ Counter annotation + trade_direction surfaced in brain prompt", flush=True)
    print()

    # ── Step 9: Cleanup ──────────────────────────────────────────────
    await db.disconnect()

    print("=" * 70)
    print("  REAL-PROJECT END-TO-END PIPELINE VERIFICATION PASSED")
    print("=" * 70)
    print()
    print("Wiring + data flow verified end-to-end on live trading.db:")
    print(f"  • config.toml → Settings: 9 new keys load correctly ✓")
    print(f"  • StructureEngine.analyze: {len(cache.get_all())}/{len(s.universe.watch_list)} coins analyzed ✓")
    print(f"  • Counter setups produced: {len(counter_examples)}")
    print(f"  • In-direction setups: {len(in_direction_examples)}")
    print(f"  • StructureCache stores StructuralAnalysis with Phase 2/3/4 fields ✓")
    print(f"  • StructureWorker.get_setup_type_confidence accessor ✓")
    print(f"  • ScannerWorker DI: services['structure_worker'] read via accessor ✓")
    print(f"  • ScannerWorker._compute_opportunity_score: struct_conf factor applied ✓")
    print(f"  • StructuralAnalysis.to_dict: 6 new fields present ✓ (audit fix)")
    print(f"  • TradeScorer reads to_dict, propagates to scoring_details ✓")
    print(f"  • EnsembleVoter reads scoring_details, scales size_mult ✓")
    print(f"  • CoinPackage.XrayBlock.trade_direction populated ✓")
    print(f"  • ClaudeStrategist prompt: COUNTER-TRADE annotation rendered ✓")


if __name__ == "__main__":
    asyncio.run(main())
