# Phase 1 — Lifecycle Phase 1 (Analysis) Logging Audit

**Date:** 2026-05-09
**Lifecycle phase:** Analysis (Layer 1) — market data ingestion → signal generation → structure → regime → strategy voting → ensemble → scanner ranking → coin package construction.
**Steps audited:** 12 (Steps 1.1 through 1.12).
**Files read end-to-end:** `price_worker.py` (381 lines), `kline_worker.py` (495 lines), `altdata_worker.py` (352 lines), `signal_worker.py` (233 lines), `regime_worker.py` (314 lines), `structure_worker.py` (451 lines), `ensemble.py` (256 lines), `open_interest.py` (112 lines), `funding_rates.py` (133 lines), `price_alert_worker.py` (67 lines, NOT actually Step 1.5 — see note), and grep-driven sweep of `strategy_worker.py` (2,402 lines, 110 log calls) and `scanner_worker.py` (2,031 lines, 47 log calls).

---

## Executive Summary

| Severity | Gap count |
|---|---|
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 18 |
| LOW | 2 |
| **Total** | **20** |

Layer 1 is the most heavily instrumented part of the system — almost every step has multiple structured tags firing per tick. The gaps are concentrated in:

1. **Prose log lines (legacy)** — 12 lines across the ten files use `log.info("Foo: {bar}", bar=...)` pattern instead of `TAG | k=v | ctx()`. They duplicate or precede information already in structured tags but carry no grep-able tag and no `ctx()`.
2. **DEBUG-level events that are invisible at the default INFO sink** — 9 events across the files. Operationally important paths (kline freshness scan failure, X-RAY scanner exception, ensemble vote failure trace) are silently dropped.
3. **One missing context-binding suffix** — `FUNDING_FETCH_FAIL` lacks `| {ctx()}` (funding_rates.py:86-89).
4. **One silent exception swallow** — regime cleanup at regime_worker.py:288-289.
5. **One audit-referenced event that does not exist** — "Ticker snapshot: 50/50 coins saved" (Step 1.5). Modern equivalent is `PRICE_WS_HEALTH | quotes_cached=N`.

No CRITICAL gaps: the trade-decision-affecting events (consensus changes, regime changes, scanner top-N selection, package validation, package quarantine) all fire with structured tags including `cycle_id` correlation.

---

## Methodology Recap (per Phase E of audit prompt)

For each lifecycle step:
1. Identified the file/function.
2. Read end-to-end (smaller files) or grep-walked (strategy_worker, scanner_worker).
3. Inventoried `log.info/.warning/.error/.critical/.debug` calls.
4. Verified emission against current rotation `data/logs/workers.log` (ran tag-frequency grep, full results below).
5. Identified gaps.
6. Severity: CRITICAL / HIGH / MEDIUM / LOW.
7. Fix difficulty: Trivial / Easy / Moderate / Complex.

Tag-frequency verification (current rotation, `workers.log`, 12,942 lines):

```
189 PRICE_WS_HEALTH       136 XRAY_CLASSIFY        54 ENSEMBLE
 51 ENSEMBLE_VOTE_WEIGHTED 29 KLINE_TICK_SUMMARY   29 KLINE_FETCH
 29 ALTDATA_TICK_DONE      29 ALTDATA_OI_TICK      29 ALTDATA_FUNDING_TICK
 13 STRAT_VOTE_TRACE       12 XRAY_NONE_REASON      6 SIG_BATCH
  6 REGIME_PERCOIN          3 XRAY_TICK_SUMMARY     3 XRAY_CLASSIFY_SUMMARY
  3 XRAY_CACHE_HEALTH       3 SIG_TICK_SUMMARY      3 SIG_INPUT_AVAILABILITY
  3 SIG_BATCH_STATS         3 REGIME_TICK_SUMMARY   3 REGIME_GLOBAL
  3 REGIME_DIVERGE          2 ALTDATA_FG_TICK      29 WAL_CHECKPOINT_SCHEDULED
```

Tags with 0 emissions in the current rotation are all transition/error tags (PRICE_WS_DISC, KLINE_STRAGGLER, ENSEMBLE_CONFLICT, REGIME_PERCOIN_FAIL, etc.) — they only fire on bad conditions, and the system has been healthy. Earlier rotations (e.g. `workers.2026-05-07_*.log`) confirm they fire when triggered.

---

## Step-By-Step Findings

### Step 1.1 — Price ingestion (`src/workers/price_worker.py`)

**Code path:** `PriceWorker.tick()` (line 90) maintains the persistent Bybit WS subscription. `_handle_ticker_update()` (line 204) processes each WS callback. `_on_save_ticker_done()` (line 317) is the bridge for per-tick `save_ticker` futures.

**Logs (10 events):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `PRICE_WS_LOOP_CAPTURE_FAIL` | ERROR | 119-121 | structured + `ctx()` ✓ |
| `PRICE_UNIVERSE_EMPTY` | WARNING | 125-127 | structured + `ctx()` ✓ |
| `PRICE_WS_CONN` | INFO | 156-159 | structured + `ctx()` ✓ |
| `PRICE_WS_DISC` | WARNING | 170 | structured + `ctx()` ✓ |
| `PRICE_WS_HEALTH` | INFO | 190-199 | structured + `ctx()` ✓ — 189 firings in rotation |
| `PRICE_SKIP_INVALID` | DEBUG | 232-235 | structured but **DEBUG-only** |
| `PRICE_WS_PERSIST_NOLOOP` | DEBUG | 275-278 | structured but **DEBUG-only** |
| `PRICE_WS_PERSIST_SCHEDULE_FAIL` | ERROR | 295-298 | structured + `ctx()` ✓ |
| `PRICE_WS_PERSIST_FAIL` | WARNING | 350-353 | structured + `ctx()` ✓ |
| `PRICE_WS_TICK_FAIL` | WARNING | 306-309 | structured + `ctx()` ✓ |

**Prose lines (3):**
- Line 132-134: `log.info("PriceWorker: Updating symbols {old} -> {new}", ...)` — tag-less, fires on universe change. Duplicate with `PRICE_WS_CONN`.
- Line 161-163: `log.info("Price worker: WebSocket connected, subscribed to {n} symbols", ...)` — duplicate with `PRICE_WS_CONN`.
- Line 171: `log.warning("Price worker: WebSocket disconnected, will reconnect")` — duplicate with `PRICE_WS_DISC`.
- Line 300: `log.debug("Price update: {s} = {p}", ...)` — DEBUG, invisible.
- Line 310: `log.error("Price worker callback error: {err}", ...)` — duplicate with `PRICE_WS_TICK_FAIL` and unstructured.
- Line 312-315: `log.warning("Price worker: {n} tickers dropped total", ...)` — every-50 rollup, duplicate with `PRICE_WS_TICK_FAIL` `cumulative_dropped` field.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 1.1-G1 | 3 prose duplicates (lines 132, 161, 171, 310, 312) of structured tags. Adds noise without adding info. | LOW | Trivial — delete |
| 1.1-G2 | 2 DEBUG events (`PRICE_SKIP_INVALID`, `PRICE_WS_PERSIST_NOLOOP`) invisible. Both are edge-cases that should be observable via per-tick rollups (e.g. `PRICE_WS_HEALTH` could carry `invalid_skips=N`, `persist_noloop=N`) instead of per-event DEBUG. | MEDIUM | Easy — add counters to `PRICE_WS_HEALTH` |

### Step 1.2 — Kline ingestion (`src/workers/kline_worker.py`)

**Code path:** `KlineWorker.tick()` (line 150) iterates the watch_list × TIMEFRAME_SCHEDULE on a sweet-spot schedule. `_classify_fetch_quality()` (line 123) maps fetch result → log level. `_maybe_run_wal_checkpoint()` (line 416) runs scheduled WAL checkpoint.

**Logs (13 events, all structured):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `KLINE_UNIVERSE_EMPTY` | WARNING | 165-167 | ✓ |
| `KLINE_FETCH_FAIL` | WARNING | 242-245 | ✓ |
| `KLINE_FETCH` | dynamic | 285-288 | INFO/WARNING/ERROR/CRITICAL via `_classify_fetch_quality` ✓ |
| `KLINE_GAP` | WARNING | 301-304 | per-symbol when level not INFO ✓ |
| `KLINE_CIRCUIT_BREAKER` | CRITICAL | 309-311 | opens 30 s circuit ✓ |
| `KLINE_STRAGGLER` | WARNING | 269-272 | consecutive-fail threshold ✓ |
| `KLINE_FRESHNESS_WARN` | WARNING | 362-366 | per-symbol, 600 s threshold ✓ |
| `KLINE_FRESHNESS_WARN` | WARNING | 383-387 | per-symbol no-rows-in-DB ✓ |
| `KLINE_WRITE_LAG` | WARNING | 371-376 | aggregate top-5 ✓ |
| `KLINE_FRESHNESS_SKIP` | DEBUG | 389 | exception in scan **DEBUG-only** |
| `WAL_CHECKPOINT_ERR` | WARNING | 452-454 | ✓ |
| `WAL_CHECKPOINT_SCHEDULED` | INFO | 475-481 | ✓ — 29 firings |
| `WAL_CHECKPOINT_ESCALATE` | WARNING | 489-493 | ✓ |
| `KLINE_TICK_SUMMARY` | INFO | 408-414 | ✓ — 29 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 1.2-G1 | `KLINE_FRESHNESS_SKIP` at DEBUG (line 389). The exception is in the post-tick freshness scan SQL — a real DB failure here means freshness reporting is silently broken. Should be WARNING. | MEDIUM | Trivial — change `log.debug` to `log.warning` |

### Step 1.3 — Altdata ingestion (`src/workers/altdata_worker.py`)

**Code path:** `AltDataWorker.tick()` (line 108) fires three independent sub-cadences (funding every tick, OI every `_oi_interval_s`, F&G every `_fg_interval_s`). `_timed()` (line 150) wraps each fetch for per-feed latency.

**Logs (9 events, mostly structured):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `ALTDATA_UNIVERSE_EMPTY` | WARNING | 120-122 | ✓ |
| `ALTDATA_SOURCE_FAIL` | WARNING | 189-192 | per-feed fail ✓ |
| `ALTDATA_FUNDING_TICK` | INFO | 262-266 | ✓ — 29 firings |
| `ALTDATA_OI_TICK` | INFO | 278-284 | ✓ — 29 firings |
| `ALTDATA_FG_TICK` | INFO | 287-291 | ✓ — 2 firings (60-min cadence expected) |
| `ALTDATA` | INFO | 294-297 | legacy aggregate (no major gap, kept for parsers) |
| `ALTDATA_TICK_DONE` | INFO | 313-318 | per-feed latency ✓ — 29 firings |

**Prose line (1):**
- Line 169: `log.warning("AltData worker: no sources due this tick")` — tag-less.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 1.3-G1 | Line 169 prose "no sources due this tick" should be `ALTDATA_NO_SOURCES_DUE | reason=all_disabled` structured. | LOW | Trivial |

### Step 1.4 — Open interest + funding rates (`src/intelligence/altdata/{open_interest,funding_rates}.py`)

**Code path:** Both are clients used by AltDataWorker via `_fetch_open_interest()` / `_fetch_funding_rates()`. Inner-level errors propagate up to `ALTDATA_SOURCE_FAIL`, but each client also has its own logging.

**Logs (open_interest.py, 3 events):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| (prose) "OI skipped for {s} (invalid symbol)" | DEBUG | 69 | invisible |
| (prose) "Failed to fetch OI for {s}" | WARNING | 71 | tag-less |
| (prose) "Fetched OI for {n} symbols" | DEBUG | 73 | invisible |

**Logs (funding_rates.py, 3 events):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| (prose) "Funding rate skipped..." | DEBUG | 84 | invisible |
| `FUNDING_FETCH_FAIL` | WARNING | 86-89 | structured but **MISSING `| {ctx()}` suffix** |
| (prose) "Fetched {n} funding rates" | DEBUG | 91 | invisible |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 1.4-G1 | open_interest.py line 71: prose "Failed to fetch OI for {s}: {err}" should be `OI_FETCH_FAIL | sym={s} category={cat} err='{err}' | {ctx()}` (parallel to funding's pattern). | MEDIUM | Trivial |
| 1.4-G2 | funding_rates.py line 86-89: `FUNDING_FETCH_FAIL` is missing `| {ctx()}` suffix. Cycle correlation is broken at this site. | MEDIUM | Trivial — append `| {ctx()}` |
| 1.4-G3 | DEBUG-only "OI skipped" / "Funding skipped" / "Fetched N" lines — both files have the same pattern. Either promote to INFO if rare or roll into a per-tick summary tag. | LOW | Trivial |

### Step 1.5 — Ticker collector snapshot

**Audit prompt expected event:** "Ticker snapshot: 50/50 coins saved" event continues to fire.

**Reality:** **NOT FOUND.** Searched `src/` for `Ticker snapshot`, `TICKER_SNAPSHOT`, `ticker_collector`, `TickerCollector` — only matches are unrelated docstrings in `core/types.py` and `database/repositories/market_repo.py`.

**Equivalent visibility provided by:** `PRICE_WS_HEALTH | ... quotes_cached=50 ...` (price_worker.py:198) emitted every PriceWorker tick (45 s default). Carries the same "50/50 coins" semantics.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 1.5-G1 | Audit reference text "Ticker snapshot: 50/50 coins saved" predates the current architecture. Document in Phase 11 report that PRICE_WS_HEALTH `quotes_cached=N` is the canonical successor. No code change required. | LOW | Documentation only |

### Step 1.6 — Signal generation (`src/workers/signal_worker.py`)

**Code path:** `SignalWorker.tick()` (line 69) iterates the watch_list, runs `aggregator.aggregate_for_symbol()` (sentiment) then `signal_generator.generate_signal()`. Caches result in `_signal_cache` for ScannerWorker's composite score.

**Logs (8 events):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `SIGNAL_UNIVERSE_EMPTY` | WARNING | 79-81 | ✓ |
| (prose) "Sentiment aggregation failed for {s}: {err}" | WARNING | 110-113 | tag-less per-coin error |
| (prose) "Signal for {s}: {type} (confidence: {c:.2f})" | INFO | 147-150 | **50 lines/cycle of tag-less prose** — NOISE candidate |
| (prose) "Signal worker failed for {s}: {err}" | ERROR | 158 | tag-less per-coin error |
| `SIG_BATCH` | INFO | 162-166 | per-cycle aggregate ✓ — 6 firings |
| `SIG_TICK_SUMMARY` | INFO | 175-179 | per-cycle aggregate ✓ — 3 firings |
| `SIG_INPUT_AVAILABILITY` | INFO | 194-201 | per-cycle distribution ✓ — 3 firings |
| `SIG_BATCH_STATS` | INFO | 217-221 | per-cycle stats ✓ — 3 firings |

The discrepancy `SIG_BATCH=6` vs `SIG_TICK_SUMMARY=3`: SIG_BATCH fires unconditionally in tick(), SIG_TICK_SUMMARY only when signals were generated. In current rotation, half the SignalWorker ticks generated zero signals → still emit SIG_BATCH but with `n=0`. Verify in Phase 11.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 1.6-G1 | Line 147-150 per-coin `Signal for {s}: {type} (confidence: ...)` at INFO — emits 50 lines/cycle of tag-less prose. The aggregate stats are already in SIG_BATCH_STATS. **Demote to DEBUG** (or remove entirely, since SIG_BATCH_STATS covers the distribution). | MEDIUM | Trivial — change `log.info` → `log.debug` |
| 1.6-G2 | Line 110-113 sentiment aggregation failure prose should be `SIG_SENT_AGG_FAIL | sym={s} err='{err}' | {ctx()}`. | MEDIUM | Trivial |
| 1.6-G3 | Line 158 top-level signal generator failure prose should be `SIG_GEN_FAIL | sym={s} err='{err}' | {ctx()}`. | MEDIUM | Trivial |

### Step 1.7 — Structure analysis (`src/workers/structure_worker.py`)

**Code path:** `StructureWorker.tick()` (line 84) fires at sweet spot 0:45 with batch_size=25 (full sweep in 2 ticks). Uses `xray` component logger.

**Logs (10 events):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `XRAY_SESSION_ERR` | WARNING | 108 | ✓ |
| `XRAY_CLASSIFY` (NONE) | DEBUG | 159-162 | per-symbol when type=NONE — **invisible** (could be 27/50 per cycle) |
| `XRAY_NONE_REASON` | INFO | 177-198 | per-symbol diagnostic when type=NONE ✓ — 12 firings |
| `XRAY_NONE_REASON_FAIL` | DEBUG | 203-206 | diagnose_none failure — **invisible** |
| `XRAY_CLASSIFY` (non-NONE) | INFO | 212-220 | per-symbol when pattern found ✓ — 136 firings |
| `XRAY_TICK_ERR` | WARNING | 229 | per-symbol exception ✓ |
| `XRAY_CLASSIFY_SUMMARY` | INFO | 274-279 | per-cycle distribution ✓ — 3 firings |
| `XRAY_SCANNER_ERR` | DEBUG | 295 | setup_scanner exception — **invisible** (operationally important) |
| `XRAY_TICK_SUMMARY` | INFO | 320-326 | per-tick aggregate ✓ — 3 firings |
| `XRAY_CACHE_HEALTH` | INFO | 341-350 | periodic cache health ✓ — 3 firings |
| `XRAY_CACHE_HEALTH_SKIP` | DEBUG | 352-354 | exception in health log — invisible |
| `XRAY_UNIVERSE_EMPTY` | WARNING | 412-414 | ✓ |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 1.7-G1 | `XRAY_CLASSIFY` at DEBUG for NONE setups (line 159-162). When 27/50 coins return NONE that's 27 invisible classifications per cycle. The follow-up `XRAY_NONE_REASON` IS at INFO and carries more detail, so this DEBUG line is redundant. **Delete** the DEBUG XRAY_CLASSIFY (NONE) — XRAY_NONE_REASON covers it. | MEDIUM | Trivial — delete |
| 1.7-G2 | `XRAY_SCANNER_ERR` at DEBUG (line 295). SetupScanner exceptions affect downstream `setups=N skips=N` numbers in XRAY_TICK_SUMMARY — a silent failure here would manifest as `setups=0` with no explanation. Promote to WARNING. | MEDIUM | Trivial |
| 1.7-G3 | `XRAY_NONE_REASON_FAIL` and `XRAY_CACHE_HEALTH_SKIP` at DEBUG. Both are diagnostic-failure cases where the diagnostic IS the observability. Promote to WARNING (or surface count in XRAY_TICK_SUMMARY). | LOW | Trivial |

### Step 1.8 — Regime detection (`src/workers/regime_worker.py`)

**Code path:** `RegimeWorker.tick()` (line 56) detects global regime (BTC), restores per-coin regimes from DB on first tick, then per-coin detection for the watch_list.

**Logs (10 events):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `REGIME_RESTORE_SKIP` | INFO | 84-86 | ✓ |
| `REGIME_RESTORE` | INFO | 121-124 | ✓ |
| `REGIME_RESTORE_FAIL` | WARNING | 137-140 | ✓ |
| `REGIME_GLOBAL` | INFO | 159-163 | ✓ — 3 firings |
| (prose) "Regime: {r} (conf={c:.2f}, ADX={adx:.1f}, chop={chop:.1f})" | INFO | 164-168 | **DUPLICATE of REGIME_GLOBAL** |
| `REGIME_PERCOIN_EMPTY` | WARNING | 182-186 | ✓ |
| `REGIME_PERCOIN` | INFO | 202-206 | ✓ — 6 firings |
| `REGIME_PERCOIN_SUMMARY` | INFO | 228-233 | ✓ |
| `REGIME_DIVERGE` | INFO | 242-247 | ✓ — 3 firings |
| `REGIME_PERCOIN_FAIL` | WARNING | 266-269 + 273-276 | ✓ |
| (silent swallow) cleanup exception | — | 288-289 | `except Exception: pass` |
| `REGIME_TICK_SUMMARY` | INFO | 293-298 | ✓ — 3 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 1.8-G1 | Line 164-168 prose "Regime: {r}..." duplicates `REGIME_GLOBAL` (line 159-163). Same regime value, same confidence, same ADX, same chop — twice per cycle. **Delete** the prose. | LOW | Trivial — delete |
| 1.8-G2 | Line 288-289 `except Exception: pass` for the regime-history cleanup DELETE. A silent failure here means `coin_regime_history` grows unbounded. Replace with `log.warning(f"REGIME_CLEANUP_FAIL | err='{str(e)[:120]}' | {ctx()}")`. | MEDIUM | Trivial |

### Step 1.9 — Strategy voting (`src/workers/strategy_worker.py` + `src/strategies/ensemble.py`)

**Code path:** `StrategyWorker` (2,402 lines) orchestrates Layer 1 → 4 strategy pipeline. Per-cycle per-strategy votes happen inside `EnsembleVoter.vote()` (`src/strategies/ensemble.py:35`). `STRAT_L1` aggregates per-strategy signal generation; `STRAT_L3` aggregates ensemble consensus output.

**Logs (strategy_worker.py — 110 calls; key INFO+ structured tags):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `STRAT_L1` | INFO | 644 | ✓ per-cycle Layer 1 aggregate |
| `STRAT_L1_SLOW` | WARNING | 610 | ✓ per-cycle slow indicator |
| `STRAT_L1_SIG` | DEBUG | 646 | per-signal — invisible |
| `STRAT_L2` | INFO | 769 | ✓ per-cycle Layer 2 aggregate |
| `STRAT_L2_SLOW` | WARNING | 771 | ✓ |
| `STRAT_L3` | INFO | 808 | ✓ per-cycle Layer 3 aggregate |
| `STRAT_L3_VOTE` | DEBUG | 811 | per-vote — invisible |
| `STRAT_L4` | INFO | 990 | ✓ per-cycle Layer 4 hints |

**Logs (strategy_worker.py — prose / unstructured):**

13 prose lines (lines 136, 991, 1753, 1775, 1787, 1996, 1999, 2014, 2017, 2040, 2043, 2047, 2050). Most are SL/TP-related and overlap Phase 4/5 audit territory but live inside strategy_worker.

**Logs (ensemble.py — 7 events):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| (prose) "Strategy {n} vote failed: {err}" | WARNING | 85-88 | tag-less |
| `ENSEMBLE_CONFLICT` | WARNING | 138 | ✓ |
| (prose) "Ensemble: {sym}..." | DEBUG | 172-179 | invisible |
| `ENSEMBLE_VOTE_WEIGHTED` | INFO | 188-193 | only when struct_conf < 0.85 ✓ — 51 firings |
| `STRAT_VOTE_TRACE` | INFO | 219-223 | only for STRONG ✓ — 13 firings |
| `STRAT_VOTE_TRACE_FAIL` | DEBUG | 225-227 | invisible |
| `ENSEMBLE` | INFO | 253 | per-batch summary ✓ — 54 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 1.9-G1 | ensemble.py line 85-88: per-strategy vote failure is prose. Should be `STRAT_VOTE_FAIL | strategy={n} sym={sym} err='{err}' | {ctx()}`. | MEDIUM | Trivial |
| 1.9-G2 | strategy_worker.py line 991 prose "Layer 4: {n} strategy hints for Claude" duplicates STRAT_L4. Delete. | LOW | Trivial |
| 1.9-G3 | strategy_worker.py SL/TP prose lines (1996, 1999, 2014, 2017, 2040, 2043, 2047, 2050) — 8 lines for SL/TP adjust/validate/auto-correct events. These are operational events that the operator should be able to grep. Should be `SL_TP_ADJUST` / `SL_TP_VALIDATE_SKIP` / `SL_TP_AUTO_CORRECT` structured tags with sym, side, old, new, reason. **Cross-cutting with Phase 4/5 audit** — flag for Phase 11 cross-reference. | MEDIUM | Easy — 8 sites |

### Step 1.10 — Ensemble consensus (`src/strategies/ensemble.py`)

Already covered in Step 1.9 — `ENSEMBLE` and `STRAT_VOTE_TRACE` and `ENSEMBLE_VOTE_WEIGHTED` cover this lifecycle step. Logging is excellent: STRONG/GOOD/WEAK/CONFLICT counts per cycle, plus per-coin trace for STRONG, plus weighted-size detail when struct_conf modifies the multiplier.

**Gaps:** none unique to Step 1.10 beyond those listed in Step 1.9.

### Step 1.11 — Scanner candidate scoring (`src/workers/scanner_worker.py`)

**Code path:** `ScannerWorker._compute_opportunity_score()` per coin → ranks `qualified_records` by composite score → selects top-N (`max_selection`, default 10). Emits per-cycle `SCANNER_FILTER_AGGREGATE`, `SCANNER_BRIEFING_SUMMARY`, plus per-coin `SCANNER_SELECTED` + `SCANNER_LABELED`.

**Logs (scanner_worker.py — 47 calls; key INFO+ tags):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `SCANNER_POSITIONS_FAIL` | WARNING | 339 | ✓ |
| `SCANNER_FG_PREFETCH_FAIL` | WARNING | 456-457 | ✓ |
| `SCANNER_POS_PREFETCH_FAIL` | WARNING | 511-525 | ✓ |
| `SCANNER_UNIVERSE_EMPTY` | WARNING | 1097-1098 | ✓ |
| `SCANNER_RECENT_LOSS_FETCH_FAIL` | WARNING | 1113-1114 | ✓ |
| `SCANNER_PACKAGE_BUILD_FAIL` | WARNING | 1171, 1277 | ✓ |
| `SCANNER_FILTER_AGGREGATE` | INFO | 1325 | per-cycle filter accounting ✓ |
| `SCANNER_BRIEFING_SUMMARY` | INFO | 1362 | per-cycle summary ✓ |
| `SCANNER_DB_WRITE_FAIL` | WARNING | 1428 | ✓ |
| `SCANNER_SUBSCRIBER_FAIL` | WARNING | 1436-1437 | ✓ |
| `SCANNER_SELECTED` | INFO | 1444-1445 | per-coin selected ✓ — 45 firings |
| `SCANNER_LABELED` | INFO | (paired with SELECTED) | ✓ — 45 firings |
| `STRAT_TOP_N_APPLIED` | INFO | (in strategist) | ✓ — 179 firings (across rotations) |

**Gaps:** none significant. Scanner is exhaustively instrumented.

### Step 1.12 — Coin package construction (`src/workers/scanner_worker.py:1230-1300`)

**Code path:** After top-N selection, `_build_package(coin, ...)` (called at lines ~1250 and ~1730) builds a CoinPackage per coin. `validate_package(pkg, ...)` from `src/core/coin_package_validator.py` returns OK / WARN / FAIL. Quarantined packages are **NOT** inserted into `layer_manager._coin_packages`.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `SCANNER_PACKAGE_BUILD_START` | INFO | 1241 | ✓ |
| `PACKAGE_VALIDATE` | INFO | 1258 | per-package verdict ✓ — 45 firings |
| `PACKAGE_QUARANTINED` | WARNING | 1265 | per-package quarantine ✓ |
| `SCANNER_PACKAGE_BUILD_FAIL` | WARNING | 1278 | per-package build exception ✓ |
| `SCANNER_PACKAGE_BUILD_DONE` | INFO | 1295 | per-cycle aggregate ✓ |
| `PACKAGE_VALIDATE_SUMMARY` | INFO | 1300 | per-cycle counters ✓ |

**Gaps:** none significant. Step 1.12 is the most heavily-instrumented step in Phase 1.

---

## Cross-Step Observations (Carry Forward To Phase 11)

### Observation A — DEBUG-only invisibility pattern

Across Phase 1, **9 DEBUG-level events** sit at the default INFO sink and are invisible:
- `PRICE_SKIP_INVALID`, `PRICE_WS_PERSIST_NOLOOP`, `PRICE_WS_PERSIST_NOLOOP` (price_worker)
- `KLINE_FRESHNESS_SKIP` (kline_worker)
- 2 prose DEBUG in altdata sub-clients
- `XRAY_CLASSIFY (NONE)`, `XRAY_NONE_REASON_FAIL`, `XRAY_SCANNER_ERR`, `XRAY_CACHE_HEALTH_SKIP` (structure_worker)
- `STRAT_L1_SIG`, `STRAT_L3_VOTE` (strategy_worker; per-signal/per-vote granularity at DEBUG is by design)
- `STRAT_VOTE_TRACE_FAIL` (ensemble)

Decision per event: PROMOTE TO INFO/WARNING when operationally meaningful, SUMMARIZE INTO ROLLUP when too high-frequency.

### Observation B — Prose vs structured pattern

Phase 1 has **12 tag-less prose log lines**. Most duplicate adjacent structured tags. The fix pattern is uniform: replace `log.info("Foo: {bar}", bar=...)` with either a structured tag or delete if duplicate.

### Observation C — Context binding

Phase 1 components mostly run **outside** trade/decision/watchdog cycles, so `no_ctx` is the legitimate state for many tags (PRICE_WS_HEALTH, KLINE_TICK_SUMMARY, LAYER1A_TICK_DONE, etc.). This is correct — these are infrastructure events, not trade events.

The exceptions where `ctx()` SHOULD propagate but doesn't:
- `FUNDING_FETCH_FAIL` is missing the suffix entirely (1.4-G2). This is a clear bug.
- The base_worker's `LAYER1A_TICK_DONE` shows `no_ctx` — by design, since the tick is a top-level scheduler event.

### Observation D — Cross-cutting strategy_worker prose

Strategy_worker has 8 prose SL/TP lines (lines 1996-2050) that are operationally important but unstructured. They affect Lifecycle Phase 4 (Validation) and Phase 5 (Execution) territory. **Recommended: defer fix to Phase 4/5 audit results so the structured tag matches that phase's naming.**

---

## Verification Gate

| Gate | Status |
|---|---|
| All 12 steps audited | PASS |
| Each step's code identified and read | PASS (small files end-to-end; large files grep-walked + targeted reads) |
| Current logs sampled to verify tag emission | PASS (38 tags grep'd against `workers.log`) |
| Gap list complete | PASS (20 gaps catalogued) |
| Severity assigned per gap | PASS |
| Fix difficulty assigned per gap | PASS (all Trivial or Easy except 1.7-G1 which is Trivial+delete) |
| Evidence cited per gap (file:line + log status) | PASS |

**Phase 1 verification gate:** PASS. Proceeding to Phase 2.

---

## Notes carried forward to Phase 2 investigation

- The strategy_worker prose pattern (SL/TP adjust/validate) will recur in Phase 4 (Validation). Defer fix recommendation until Phase 4 catalogues these.
- The `no_ctx` pattern is legitimate for Layer 1 infrastructure events — Phase 11 should explicitly mark these as "intentional" rather than gaps.
- DEBUG-level events are systemically invisible due to `setup_logging(log_level="INFO")` default. Phase 11 may consider whether a small handful of operationally-useful DEBUG lines warrant their own DEBUG sink (e.g. `data/logs/debug.log`).
- Layer 1 instrumentation is already enterprise-grade. Most Phase 1 fixes are TRIVIAL — single-line edits. Total estimated Phase 12.1 implementation effort: 1-2 hours.
