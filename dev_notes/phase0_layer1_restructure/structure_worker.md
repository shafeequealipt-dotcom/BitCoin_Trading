# Phase 0.2 — StructureWorker / X-RAY Investigation

**Investigated:** `src/workers/structure_worker.py` (273 lines), `src/analysis/structure/structure_engine.py` (837 lines), `src/analysis/structure/setup_scanner.py`, `src/analysis/structure/structure_cache.py`, plus the 10 sub-engine files in `src/analysis/structure/`. HEAD = `8dca492`.

## A. Current implementation

`StructureWorker(SweetSpotWorker)` — sweet spot `0:45` (15s after KlineWorker's `0:30`). Reads `settings.universe.watch_list` (50 coins) batched at `settings.structure.batch_size=25` per tick — full sweep takes 2 ticks (~10 min on the 5-min window).

`tick()` (lines 79-198):
1. `_get_universe()` returns this tick's batch (wrap-around via `_batch_start` cursor at line 246).
2. Compute session context once per tick (lines 86-103) — `SessionTimer.get_context(...)` produces `current_session`, `session_phase`, `manipulation_likely`.
3. Per symbol: `candles = await _fetch_klines(symbol)` → `_engine.analyze(symbol, current_price, candles, session_context)` → `_cache.set(symbol, result)`.
4. After per-symbol analysis: `_setup_scanner.scan(all_analyses, session_context)` → ranked + skip_list (Phase 11).
5. Emit `XRAY_TICK_SUMMARY` and `XRAY_CACHE_HEALTH` info logs at end of tick.

`_fetch_klines` (lines 252-272): try `MarketRepository.get_klines(symbol, H1, 200)` first; fallback to `_shadow_reader.get_klines(symbol, "60", 200)` (Shadow DB, async-aiosqlite per the 2026-04-25 fix); else `None`.

## B. The `StructuralAnalysis` dataclass

Built by `StructureEngine.analyze()` at `structure_engine.py` (need to confirm exact line; the engine has 837 lines, so analyze is the public entrypoint). Fields known to exist (from Phase 0 traceability):

- `setup_score: float` (0-100) — consumed by ScannerWorker via `StructureWorker.get_setup_score(coin)` (lines 200-217).
- `structural_placement` (with `sl, tp, rr_ratio`) — consumed by Phase 5 qualitative filter (`min_rr_ratio` check).
- `mtf_confluence` — multi-timeframe alignment indicator.
- `key_features` (list[str]) — human-readable indicators ("fresh_OB_at_X", etc.).
- `session` (`current_session`, `session_phase`, `manipulation_likely`).

The X-RAY phases (1-12 per blueprint Section 8.2.1) emit individual sub-results that are aggregated into `StructuralAnalysis`. Phase 11 (`SetupScanner`) ranks setups + emits `skip_list`. Phase 12 (`SessionTimer`) emits session context.

## C. Callers / consumers

- `ScannerWorker._get_setup_score(coin)` — via `services["structure_worker"].get_setup_score(coin)` (lines 200-217).
- `MarketScanner` — `_setup_scanner.scan(...)` ranked + skip_list cached at `_cache.set_ranked_setups(ranked, skip_list)` (line 143).
- Stage 2 strategist — `structure_cache.get_top_setups(...)` (referenced in `strategist.py` per blueprint Section 4.4).
- Tests in `tests/test_phase5/test_structure_worker.py` and `tests/test_corrected_layer1_*`.

## D. Dependencies

- `MarketRepository` for trading.db klines.
- `ShadowKlineReader` (async-aiosqlite, lifecycle managed by WorkerManager) for Shadow DB fallback.
- `StructureEngine` with 10+ sub-engines.
- `SessionTimer` (lazy-init at line 89-91).
- `SetupScanner` (lazy-init at line 137-139).

## E. Scheduling

Sweet spot `0:45` via SweetSpotWorker base. Slow-tick threshold 6s (`_TICK_SLOW_PER_WORKER["structure_worker"]`). `XRAY_TICK_SUMMARY` reports `el=Xms drift_ms=Y`.

## F. Restructure change plan (Phase 2)

1. **Add `SetupType(str, Enum)` to `structure_engine.py`** with values:
   `NONE, BULLISH_FVG_OB, BULLISH_STRUCTURAL_BREAK, BULLISH_LIQUIDITY_SWEEP, BULLISH_RANGE_BREAKOUT, BEARISH_FVG_OB, BEARISH_STRUCTURAL_BREAK, BEARISH_LIQUIDITY_SWEEP, BEARISH_RANGE_BREAKDOWN`.
2. **Append two fields to `StructuralAnalysis` dataclass**: `setup_type: SetupType = SetupType.NONE`, `setup_type_confidence: float = 0.0`. Defaults preserve backward-compat.
3. **Add `classify_setup(analysis: StructuralAnalysis) -> tuple[SetupType, float]`** to `StructureEngine` — pure function reading existing fields (FVG, OB, BOS, sweeps, MTF confluence, compression). Decision tree (top-down, first match wins) per the plan in `/home/inshadaliqbal786/.claude/plans/plan-mode-today-dazzling-snail.md` Phase 2.
4. **Wire into `structure_worker.py`** — after `analysis = self._engine.analyze(...)` at line 116-119, before `self._cache.set(symbol, result)` at line 121, call `setup_type, conf = self._engine.classify_setup(result); result.setup_type = setup_type; result.setup_type_confidence = conf`.
5. **Emit `XRAY_CLASSIFY` per coin** (DEBUG; INFO when type != NONE) and `XRAY_CLASSIFY_SUMMARY` cycle-level log line.
6. Config: `[analysis.structure.setup_types]` block with `fvg_ob_min_confluence=0.7, structural_break_require_retest=true, sweep_min_displacement_pct=0.5, range_breakout_min_compression_bars=20, mtf_alignment_required=true`. Add `SetupTypesSettings` dataclass to `src/config/settings.py`.

## G. Verification criteria

- After Phase 2 deploy + 1 cycle: 50/50 watch_list coins have `setup_type` populated in StructureCache.
- Over 6h: no >5 coins flap setup_type more than once per 30 min.
- Confidence 0-1; high-confidence (>0.8) classifications visually look like clean patterns.
- Existing XRAY tests pass; tick latency change <5%.
- Existing `setup_score` numeric path unaffected.
