# C1 — StructureWorker / X-RAY (Forensic Data)

CAPTURE TIMESTAMP (UTC): 2026-04-27 22:58:35
Log file: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
DB snapshot: `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db` (mtime 22:56)

---

## C.1.1 — File location and structure

**Worker file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/structure_worker.py` — 368 lines (verified `wc -l`).

Module docstring (`structure_worker.py:1-11`, verbatim):

```
"""Structure Worker: runs X-RAY structural analysis for the full watch_list.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md):
- Operates on ``config.universe.watch_list`` (50 coins). With batch_size=25,
  a full sweep completes in 2 ticks (~10 min via two sweet-spot fires).
- Fires at the configured sweet spot (default 0:45) within every 5-min
  window, after KlineWorker's 0:30 finishes its writes. The 15-second gap
  gives kline writes time to land in trading.db before structure reads.
- ``ShadowKlineReader`` (Shadow DB fallback path, async-aiosqlite per the
  2026-04-25 fix) is unchanged.
"""
```

**Sub-engine directory:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/` — 14 .py files (excl. `__init__.py`), 4302 lines total.

Files with one-line descriptions (header docstrings, verbatim first line):

| File | Lines | Class / role |
|------|-------|--------------|
| `structure_engine.py` | 1164 | "X-RAY Structure Engine — orchestrates all structural analysis." (`StructureEngine`) |
| `structure_cache.py` | 128  | "X-RAY structural analysis cache — compute once, share everywhere." (`StructureCache`) |
| `support_resistance.py` | 320 | (Phase 1) `SupportResistanceEngine` — S/R + swing pivots |
| `market_structure.py` | 306 | (Phase 2) `MarketStructureDetector` — BOS / CHoCH / structure label |
| `structural_levels.py` | 245 | (Phase 3) `StructuralLevelCalculator` — structural SL/TP placement |
| `fair_value_gap.py` | 212 | (Phase 4) `FairValueGapDetector` |
| `order_blocks.py` | 188 | (Phase 5) `OrderBlockDetector` |
| `liquidity.py` | 332 | (Phase 6+7) `LiquidityMapper` — zones + sweeps |
| `volume_profile.py` | 188 | (Phase 8) `VolumeProfileCalculator` |
| `fibonacci.py` | 191 | (Phase 9) `FibonacciCalculator` |
| `mtf_confluence.py` | 250 | (Phase 10) `MTFConfluenceScorer` |
| `setup_scanner.py` | 263 | (Phase 11) `SetupScanner` — "Smart Coin Selection" |
| `session_timing.py` | 218 | (Phase 12) `SessionTimer` — "Institutional Session Timing" |
| `shadow_kline_reader.py` | 296 | `ShadowKlineReader` — async aiosqlite fallback |

`models/structure_types.py` exists alongside (`SetupType`, `StructuralAnalysis`, etc.).

---

## C.1.2 — The 12 X-RAY phases

The pipeline orchestrator is `StructureEngine.analyze()` at `structure_engine.py:167-551`. The phase markers in code are **1-10 plus 11 and 12** (see `structure_engine.py:204, 247, 265, 334, 345, 358, 373, 400, 415, 436` for phase comment headers; `structure_worker.py:91, 229` for phases 12/11).

NOT FOUND — the labels "3a", "3b", "3c" referenced in the prompt do NOT appear as separate phase headers in any file under `src/analysis/structure/` (searched: `grep -n "Phase 3a\|3b\|3c" src/analysis/structure/*.py` returns no matches). Phase 3 in the code is a single block: "PHASE 3: Structural SL/TP Placement" (`structure_engine.py:265`). Documenting as a gap.

| Phase | Name | File | Computes | Writes to `StructuralAnalysis` |
|-------|------|------|----------|-------------------------------|
| 1 | Support & Resistance | `support_resistance.py` (`SupportResistanceEngine.calculate`) | `support_levels`, `resistance_levels`, `swing_data` (`structure_engine.py:204-215`) | `support_levels`, `resistance_levels`, `nearest_support`, `nearest_resistance`, `position_in_range` |
| 2 | Market Structure | `market_structure.py` (`MarketStructureDetector.detect`) | BOS/CHoCH events, `structure` label (uptrend/downtrend/ranging) (`structure_engine.py:247-263`) | `market_structure`, derives `suggested_direction` |
| 3 | Structural SL/TP Placement | `structural_levels.py` (`StructuralLevelCalculator.calculate`) | dual-direction SL/TP + RR (`structure_engine.py:265-332`) | `structural_placement` (with `rr_long`, `rr_short`, `rr_best`, `long_sl_price`, etc.) |
| 4 | Fair Value Gaps | `fair_value_gap.py` (`FairValueGapDetector.detect`) | unfilled FVG list (`structure_engine.py:334-343`) | `fvgs`, `nearest_fvg` |
| 5 | Order Blocks | `order_blocks.py` (`OrderBlockDetector.detect`) | OB list (`structure_engine.py:345-356`) | `order_blocks`, `nearest_ob` |
| 6+7 | Liquidity Zones + Sweeps | `liquidity.py` (`LiquidityMapper.detect_zones` / `.detect_sweeps`) | zones + recent sweeps (`structure_engine.py:358-383`) | `liquidity_zones`, `recent_sweeps`, `nearest_unswept_liquidity`, `active_sweep_signal`, `smc_confluence` |
| 8 | Volume Profile | `volume_profile.py` (`VolumeProfileCalculator.calculate`) | POC + VAH/VAL (`structure_engine.py:400-413`) | `volume_profile`, `poc_price` |
| 9 | Fibonacci | `fibonacci.py` (`FibonacciCalculator.calculate`) | retracement levels + key level (`structure_engine.py:415-434`) | `fibonacci`, `fib_key_level` |
| 10 | MTF Confluence | `mtf_confluence.py` (`MTFConfluenceScorer.score`) | 0-10 score + quality (`structure_engine.py:436-460`) | `mtf_confluence`, `mtf_confluence_score`, `confluence_quality` |
| 11 | Setup Scanner (smart coin selection) | `setup_scanner.py` (`SetupScanner.scan`) | ranks all `StructuralAnalysis` in cache; produces top-12 + skip list (`structure_worker.py:229-243`) | `_ranked_setups`, `_skip_list` on `StructureCache` (not on `StructuralAnalysis`) |
| 12 | Session Timing | `session_timing.py` (`SessionTimer.get_context`) | session label, manipulation flag, Asian range (`structure_worker.py:91-108`) | `session_context` field |

### Phase elapsed times — last 10 ticks (per-coin elapsed_ms inside `XRAY_ANALYZE` log)

Sample from cycle 22:25:45 (verbatim log, `el=` field):

```
sym=SOLUSDT     el=12ms   phases=10/10
sym=BNBUSDT     el=12ms   phases=10/10
sym=XRPUSDT     el=167ms  phases=10/10
sym=ADAUSDT     el=12ms   phases=10/10
sym=DOGEUSDT    el=56ms   phases=10/10
sym=AVAXUSDT    el=111ms  phases=10/10
sym=LINKUSDT    el=9ms    phases=10/10
sym=ARBUSDT     el=56ms   phases=10/10
sym=NEARUSDT    el=12ms   phases=10/10
sym=ATOMUSDT    el=24ms   phases=10/10
```

Per-tick aggregate (`XRAY_TICK_SUMMARY`, last 5 ticks):

```
22:05:46  universe=50 batch=0/2 symbols=25 analyzed=25 errors=0 cached=25 setups=12 skips=13 el=2069ms
22:10:45  universe=50 batch=1/2 symbols=25 analyzed=25 errors=0 cached=50 setups=12 skips=13 el=1869ms
22:15:45  universe=50 batch=0/2 symbols=25 analyzed=25 errors=0 cached=50 setups=12 skips=13 el=2207ms
22:20:45  universe=50 batch=1/2 symbols=25 analyzed=25 errors=0 cached=50 setups=12 skips=13 el=1838ms
22:25:47  universe=50 batch=1/2 symbols=25 analyzed=25 errors=0 cached=50 setups=12 skips=13 el=2303ms drift_ms=23
```

Phase-level breakout: NOT FOUND. Per-phase elapsed timing is not emitted by the engine (only the aggregate `XRAY_ANALYZE el=` per coin and `XRAY_TICK_SUMMARY el=` per tick). The `phases=10/10` field counts how many of phases 1-10 succeeded (`structure_engine.py:202, 213, 254, 330, 341, 354, 369, 381, 411, 432, 458`).

---

## C.1.3 — Setup classification

**Location:** `StructureEngine.classify_setup()` at `structure_engine.py:676-803`. Called from `structure_engine.py:524` inside `analyze()` after the analysis is otherwise populated.

Verbatim decision tree (`structure_engine.py:676-803`):

```python
def classify_setup(
    self, analysis: StructuralAnalysis,
) -> tuple[SetupType, float]:
    cfg = getattr(self._settings, "setup_types", None)
    fvg_ob_min = getattr(cfg, "fvg_ob_min_confluence", 0.7) if cfg else 0.7
    require_retest = (
        getattr(cfg, "structural_break_require_retest", True) if cfg else True
    )
    sweep_min_pct = (
        getattr(cfg, "sweep_min_displacement_pct", 0.5) if cfg else 0.5
    )
    breakout_min_bars = (
        getattr(cfg, "range_breakout_min_compression_bars", 20) if cfg else 20
    )

    direction = (analysis.suggested_direction or "").lower()
    struct = (analysis.market_structure.structure or "").lower()
    last_bos = analysis.market_structure.last_bos
    nearest_fvg = analysis.nearest_fvg
    nearest_ob = analysis.nearest_ob
    active_sweep = analysis.active_sweep_signal
    mtf = analysis.mtf_confluence
    mtf_score_01 = (
        float(getattr(mtf, "score", 0)) / 10.0 if mtf is not None else 0.0
    )
    smc_01 = max(0.0, min(1.0, analysis.smc_confluence / 100.0))

    def _bull_alignment() -> bool:
        return direction == "long" and struct in ("uptrend",)

    def _bear_alignment() -> bool:
        return direction == "short" and struct in ("downtrend",)

    # ── Bullish FVG + OB confluence ──
    if (
        nearest_fvg is not None and nearest_fvg.direction == "bullish"
        and not nearest_fvg.filled
        and nearest_ob is not None and nearest_ob.direction == "bullish"
        and nearest_ob.fresh
        and _bull_alignment()
        and mtf_score_01 >= fvg_ob_min
    ):
        conf = min(mtf_score_01, max(smc_01, 0.5))
        return SetupType.BULLISH_FVG_OB, round(conf, 4)

    # ── Bearish FVG + OB confluence (mirror) ──
    if (
        nearest_fvg is not None and nearest_fvg.direction == "bearish"
        and not nearest_fvg.filled
        and nearest_ob is not None and nearest_ob.direction == "bearish"
        and nearest_ob.fresh
        and _bear_alignment()
        and mtf_score_01 >= fvg_ob_min
    ):
        conf = min(mtf_score_01, max(smc_01, 0.5))
        return SetupType.BEARISH_FVG_OB, round(conf, 4)

    # ── Bullish structural break (BOS with optional retest) ──
    if (
        last_bos is not None and last_bos.direction == "bullish"
        and direction == "long"
        and (not require_retest or last_bos.significance == "major")
    ):
        conf = max(mtf_score_01, smc_01, 0.5)
        return SetupType.BULLISH_STRUCTURAL_BREAK, round(conf, 4)

    # ── Bearish structural break ──
    if (
        last_bos is not None and last_bos.direction == "bearish"
        and direction == "short"
        and (not require_retest or last_bos.significance == "major")
    ):
        conf = max(mtf_score_01, smc_01, 0.5)
        return SetupType.BEARISH_STRUCTURAL_BREAK, round(conf, 4)

    # ── Liquidity sweep + reclaim ──
    if active_sweep is not None and active_sweep.sweep_depth_pct >= sweep_min_pct:
        if active_sweep.sweep_type == "bullish_sweep" and direction == "long":
            conf = max(mtf_score_01, 0.5)
            return SetupType.BULLISH_LIQUIDITY_SWEEP, round(conf, 4)
        if active_sweep.sweep_type == "bearish_sweep" and direction == "short":
            conf = max(mtf_score_01, 0.5)
            return SetupType.BEARISH_LIQUIDITY_SWEEP, round(conf, 4)

    # ── Range breakout/breakdown (compression release) ──
    if (
        analysis.position_in_range >= 0.95 and direction == "long"
        and analysis.total_confluence_factors >= breakout_min_bars // 2
    ):
        return SetupType.BULLISH_RANGE_BREAKOUT, round(max(mtf_score_01, 0.5), 4)
    if (
        analysis.position_in_range <= 0.05 and direction == "short"
        and analysis.total_confluence_factors >= breakout_min_bars // 2
    ):
        return SetupType.BEARISH_RANGE_BREAKDOWN, round(max(mtf_score_01, 0.5), 4)

    return SetupType.NONE, 0.0
```

### Configurable thresholds (`config.toml:930-935`, verbatim):

```
[analysis.structure.setup_types]
fvg_ob_min_confluence = 0.7
structural_break_require_retest = true
sweep_min_displacement_pct = 0.5
range_breakout_min_compression_bars = 20
mtf_alignment_required = true
```

### Live distribution — last 100 classifications

From `XRAY_CLASSIFY_SUMMARY` events in `workers.log` (last 4 ticks × 25 = 100 classifications):

```
22:10:45  total=25  bearish_fvg_ob=15  none=10                     conf_p50=0.55 conf_p95=0.55
22:15:45  total=25  bearish_fvg_ob=18  none=6   bullish_fvg_ob=1   conf_p50=0.55 conf_p95=0.55
22:20:45  total=25  bearish_fvg_ob=15  none=10                     conf_p50=0.55 conf_p95=0.55
22:25:47  total=25  bearish_fvg_ob=18  none=6   bullish_fvg_ob=1   conf_p50=0.55 conf_p95=0.55
```

Aggregate (last 100 across these 4 ticks, exact `XRAY_CLASSIFY` count by setup_type):

```
bearish_fvg_ob = 66
none           = 32   (10 + 6 + 10 + 6)
bullish_fvg_ob = 2
```

Confidence is essentially constant at 0.55 because the threshold gate is `mtf_score_01 >= 0.7` (i.e. mtf score 7/10) and `conf = min(mtf_score_01, max(smc_01, 0.5))`. With `mtf=7/10` and `smc=55` (= 0.55 normalised), `conf = min(0.7, max(0.55, 0.5)) = 0.55`.

---

## C.1.4 — Batch processing

**Why 25 of 50 per tick:** `_get_universe()` slices `[batch_start : batch_start + batch_size]` and advances `_batch_start` by `batch_size` (`structure_worker.py:340-346`, verbatim):

```python
batch = self._full_universe[self._batch_start:self._batch_start + self._batch_size]
self._batch_start += self._batch_size
if self._batch_start >= len(self._full_universe):
    self._batch_start = 0  # wrap around to start of universe

return batch if batch else self._full_universe[:self._batch_size]
```

`batch_size` source (`structure_worker.py:82`):

```python
self._batch_size = settings.structure.batch_size
```

`config.toml:925`:

```
batch_size = 25
```

(NOTE: prompt says "batch_size 2" — that does NOT match the code/config. Live config and live logs both report `batch=0/2` or `batch=1/2`, meaning **2 batches of 25** to cover 50 coins. The "batch=1/2" denominator is the count of batches, not a batch_size value. Confirmed in `structure_worker.py:255-263`: `_batches_total = ceil(50/25) = 2`.)

**Per-coin elapsed (cycle 22:25, in ms):**

```
SOLUSDT=12  BNBUSDT=12  XRPUSDT=167  ADAUSDT=12   DOGEUSDT=56  AVAXUSDT=111
LINKUSDT=9  ARBUSDT=56  NEARUSDT=12  ATOMUSDT=24  INJUSDT=23   RENDERUSDT=12
ONDOUSDT=18 ENAUSDT=14  PYTHUSDT=38  SEIUSDT=8    AEROUSDT=8   RUNEUSDT=42
GALAUSDT=177 MANAUSDT=13 SANDUSDT=16 AXSUSDT=151  LDOUSDT=24
```

23 of 25 captured here; sum ≈ 1023 ms, mean ≈ 41 ms, median ≈ 16 ms. Tick total elapsed (incl. session-context, classify-summary, scanner): `el=2303ms`.

**If batch=1 (i.e. all 50 per tick), projected duration:** 2× the 25-coin mean = ~2050 ms of per-coin work, plus the SetupScanner pass (single pass over the full cache; `setup_scanner.py:36-89` runs in tens of ms). Projected: ~2.0–4.6 s per tick. NOT measured directly — projection is mean × 2 from the live 25-coin sample.

---

## C.1.5 — StructureCache

**Defined in:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/structure_cache.py` (128 lines).

**Constructor:** `structure_cache.py:25` — `def __init__(self, ttl_seconds: float = DEFAULT_TTL)`. `DEFAULT_TTL = 300.0` (`structure_cache.py:15`).

**Wired with TTL=300s** — `manager.py:219-221`:

```python
structure_cache = StructureCache(
    ttl_seconds=float(settings.structure.cache_ttl_seconds),
)
```

`cache_ttl_seconds = 300` (`config.toml:897`).

**Entry shape** — `structure_cache.py:27`:

```python
self._cache: dict[str, tuple[float, StructuralAnalysis]] = {}
```

Key: `symbol` (e.g. `"BTCUSDT"`). Value: `(monotonic_set_time, StructuralAnalysis)`.

`StructuralAnalysis` fields (visible at `structure_engine.py:483-517`, verbatim list):

```
symbol, current_price,
support_levels, resistance_levels, nearest_support, nearest_resistance, position_in_range,
market_structure, structural_placement, setup_score, setup_quality, suggested_direction,
fvgs, order_blocks, liquidity_zones, recent_sweeps,
nearest_fvg, nearest_ob, nearest_unswept_liquidity, active_sweep_signal, smc_confluence,
volume_profile, poc_price, fibonacci, fib_key_level, mtf_confluence, mtf_confluence_score,
confluence_quality, total_confluence_factors, session_context,
# (then patched in classify_setup):
setup_type, setup_type_confidence
```

`structure_cache.py` also stores `_ranked_setups` and `_skip_list` from Phase 11 (`set_ranked_setups`, `structure_cache.py:94-105`).

**Live size, oldest/newest age** — most recent `XRAY_CACHE_HEALTH` events (`workers.log`):

```
22:25:47  size=50  oldest_age_s=302  hits=165 misses=234 hit_rate=0.41
22:20:45  size=50  oldest_age_s=298  hits=...
22:15:45  size=50  oldest_age_s=298  ...
22:10:45  size=50  oldest_age_s=302  ...
```

`oldest_age_s ≈ 300` is exactly the TTL, meaning every alternate-batch-cursor coin sits at the TTL boundary at the moment of the next sweep. Newest entry age is 0–~5 s (just-written within the current tick). The cache reaches `size=50` after the second tick post-restart and stays there.

---

## C.1.6 — Freshness gate

**Cache TTL is the sole freshness mechanism inside the cache itself.** `StructureCache.get()` (`structure_cache.py:31-44`, verbatim):

```python
def get(self, symbol: str) -> StructuralAnalysis | None:
    cached = self._cache.get(symbol)
    if cached:
        cache_time, result = cached
        if time.monotonic() - cache_time < self._ttl:
            self._hits += 1
            return result
    self._misses += 1
    return None
```

Threshold: `self._ttl = 300.0 s` (live wiring). Beyond 300 s `.get()` returns None (the entry is not erased — just rejected on read).

**ScannerWorker access path** (`scanner_worker.py:533-547`):

```python
sw = self.services.get("structure_worker")
structure = None
try:
    cache = getattr(sw, "_cache", None) if sw else None
    structure = cache.get(symbol) if cache and hasattr(cache, "get") else None
except Exception:
    structure = None
if structure is None:
    record["reasons_failed"].append("no_xray_analysis")
    return False, record
setup_type = getattr(structure, "setup_type", None)
if setup_type is None or getattr(setup_type, "value", "none") == "none":
    record["reasons_failed"].append("no_xray_setup_type")
    return False, record
```

ScannerWorker reads the `StructureCache` directly (via the worker's `_cache` attr) — there is NO separate freshness threshold layered on top of the 300 s TTL.

A separate "xray cache write age" telemetry exists (`structure_worker.py:140-144` — `record_write("xray", symbol)`), and ScannerWorker reads it as a **rollup** (`scanner_worker.py:935-945`, `xray_age_p50_ms`, `xray_age_p95_ms`) but does NOT use it as a per-coin gate.

**% fresh at any moment:** Live `XRAY_CACHE_HEALTH` shows `size=50` continuously across the 21:55–22:27 window with `oldest_age_s ∈ [298, 302]`. Since the TTL is 300 s and the worker fires every 5 min covering 25/50 coins per fire, the steady-state pattern is: 25 entries are 0–300 s old (just-written) and 25 are 300+ s old — i.e. exactly half the universe is within TTL at any instant on average, and a coin oscillates between just-written and at-TTL-boundary. `XRAY_CACHE_HEALTH` reports `cached=50` because it counts dict size, NOT TTL-validity. The hit_rate=0.41 line confirms reads frequently miss the freshness check.

---

## OBSERVED ANOMALIES

- 64% of last 100 setups classify as `bearish_fvg_ob`, 32% as `none`, 2% as `bullish_fvg_ob`. The pre-condition for FVG_OB is `mtf_score_01 >= fvg_ob_min` (= 0.7). Since `mtf=7/10` is reached for the bulk of the universe (`XRAY_ANALYZE` shows `mtf=7/10(good)` on most coins) and direction is overwhelmingly `short` (because `structure=downtrend` for almost the whole sample), the bearish branch wins. The threshold value of 7/10 is met with no margin (exactly 7 == 0.7).
- Setup confidence is pinned at 0.55 because of the `min/max` clamp interaction with the constant `smc=55`/`mtf=7`.
- `oldest_age_s = 302` (>= TTL) in cache health report means the cache always carries at least one entry that is technically expired by the time the worker logs the health line.
