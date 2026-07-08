# Output Quality + Observability — Deep Audit Report

**Date:** 2026-04-27
**Scope:** End-to-end re-verification of every phase, every modified file, every integration point in `IMPLEMENT_LAYER1_OUTPUT_QUALITY_AND_OBSERVABILITY_PROFESSIONAL.md` (Modules 1 + 2, Phases 0–15).
**Method:** Static analysis (ruff + mypy) → per-phase line-by-line code review → call-site verification → settings round-trip → naming consistency → full regression.

**Verdict:** ✅ all 30 phase commits pass deep audit; no behavioural defects found; 14 newly-introduced ruff errors and 1 mypy missing-annotation cleaned up in commit `20b37d7`. Final test verdict: **1590 passed / 0 failures**.

---

## Summary table

| Phase | Module(s) | Audit verdict | Key verification |
|---|---|---|---|
| 1 | signal_generator + Settings | ✅ clean | Constructor type-annotated; settings forward-ref avoids circular import; both call sites (`manager.py:168`, `mcp/server.py:154`) pass `settings=`; multi-source dropped-inactive logic correct; thresholds validated in `__post_init__` |
| 2 | structure_engine + structure_worker | ✅ clean | `diagnose_none` mirrors `classify_setup` cfg reads + normalisation; advisory only (try/except); per-cycle `conf_p50/p95` indexed via sorted-list percentile (consistent across phases) |
| 3 | regime + Settings | ✅ clean | `hysteresis_count` config-driven default 2 (back-compat); `__post_init__` validates ≥ 1; REGIME_PENDING shows actual `count/N`; REGIME_PERCOIN_SUMMARY uses merged cache for stable cross-tick distribution |
| 4 | strategy_worker | ✅ clean | All four STRAT_L*_DONE additive (original tags preserved on next line); percentile math safe against empty inputs (`max(0, int(p * (n - 1)))`); component sums divided by n only when n>0 |
| 5 | coin_package_validator + scanner_worker | ✅ clean | Frozen dataclass (immutable); pure function; FAIL→continue (quarantine); WARN→still emitted but counted; lazy imports avoid circular deps; settings-tunable |
| 6 | cache_freshness + /health | ✅ clean | RLock paranoid-safe; `record_write` O(1); `get_snapshot` shallow-copies under lock; hooks at all 3 cache sites (klines/xray/packages) wrapped in try/except; CYCLE_FRESHNESS rollup with "unknown" fallback; /health Data Freshness section reads same snapshot |
| 7 | sentiment/aggregator | ✅ clean | `_reddit_intentionally_disabled` set at init; SENT_DEGRADED_MODE for config-disabled, SENT_NO_DATA for genuine gap; SENT_UNKNOWN retained as back-compat alias on genuine-gap path; behaviour unchanged (overall=0.0, level=UNKNOWN); defensive try/except on settings introspection |
| 9 | workers (price/scanner/strategy/regime) | ✅ clean | A1 SERVICE_ACCESSOR_FAIL at DEBUG (sparse exception path); A4 PRICE_WS_TICK_FAIL with cumulative_dropped; A5 PRICE_SKIP_INVALID at DEBUG; A7 STRAT_SKIP_STALE_AGG rollup replaces N-per-coin blast; A8 STRAT_TA_DONE per-cycle; A9 REGIME_RESTORE_FAIL with loaded_so_far + universe |
| 10 | order_service + layer_manager | ✅ clean | ORDER_ATTEMPT at very top of place_order BEFORE gate; BRAIN_TRADES_DROPPED structured at WARNING with layer/count/sample_syms[10] for both new_trades AND urgent action drop paths |
| 11 | claude_code_client + strategist | ✅ clean | CLAUDE_PARSE_FAIL distinct from CLAUDE_CALL_FAIL — only on `json.JSONDecodeError`, includes raw_response for prompt-engineering diagnostics; POSITION_INVALIDATED at INFO with sym + reason + invalidated_count, original DEBUG STRAT_POS_INVALIDATE retained |
| 12 | fear_greed + funding_rates | ✅ clean | FEAR_GREED_FETCH_FAIL emits BEFORE raise so context survives broad except; FUNDING_FETCH_FAIL per-symbol categorised (timeout/rate_limit/invalid/error), "invalid" → DEBUG; FEAR_GREED_FALLBACK with cached_value + age_h + max_age_h |
| 13 | strategist + trade_coordinator | ✅ clean | STRATEGIST_PACKAGES_READ at CALL_A read with age min/max for staleness diagnostics, defensive against missing built_at; POSITION_CLOSE_REASON in `set_close_reason` at INFO so cause-of-close is captured at decision time |
| 14 | base_worker + manager + order_service | ✅ clean | `wid = uuid4().hex[:8]` per-instance in BaseWorker.__init__; threaded through WM_START/STOP/CRASH; actor= field in ORDER_BLOCKED maps reason→actor (layer3_auto/system_auto/gate) |
| 15 | migrations | ✅ clean | SCHEMA_VERSION 24→25; 10 ALTER TABLE statements all `DEFAULT NULL` (preserves rows); idempotency via PRAGMA pre-flight + duplicate-column exception fallback; schema-only (populator deferred per documented follow-up) |

---

## Static analysis (post-cleanup)

### Ruff baseline-vs-current delta

```
Baseline ruff errors (pre-output-quality-fix):  1820 (1246 unique sig)
Current  ruff errors (HEAD post-cleanup):       1820 (1246 unique sig)
Newly introduced by 32 commits:                  0
```

Cleanup commit `20b37d7` resolved all 14 errors I introduced earlier:
- 3× I001 import-order (cache_freshness, coin_package_validator, telegram/handlers/system)
- 5× I001 import-order in test files
- 2× F401 unused imports (signal_generator FUNDING_RATE_THRESHOLDS, scanner_worker VERDICT_OK)
- 1× F401 unused pytest in tests
- 8× E501 line-too-long: structure_engine diagnose_none branch helpers + manager wid log lines + signal_generator SIG_GEN_INPUT line + strategy_worker STRAT_L2_DONE component string

### Mypy delta

Code I authored is type-clean except `signal_generator._compute_data_age_hours` whose pre-existing untyped `fg`, `fr`, `oi` parameters predate this work (commit `fbd13dea`, before the `pre-output-quality-fix` tag).

Constructor `SignalGenerator(aggregator, db, settings: "Settings | None" = None)` correctly forward-refs Settings via `TYPE_CHECKING` to avoid circular import.

### Python-version compat fix

The earlier ruff autofix wave applied UP017 (`Use datetime.UTC alias`) which is Python 3.11+ syntax. Reverted to `from datetime import timezone` + `timezone.utc` to match Python 3.10 (test environment) and the pattern used elsewhere in the codebase.

---

## Per-phase deep findings

### Phase 1 — `SignalGenerator` multi-source classifier

**Files audited:**
- `src/intelligence/signals/signal_generator.py` (full read, 491 lines)
- `src/config/settings.py:1599-1730` (SignalGeneratorMultiSourceSettings + SignalGeneratorSettings)
- `src/workers/manager.py:168` and `src/mcp/server.py:154` (constructor call sites)
- `config.toml:1049-1062` ([signal_generator.multi_source])
- `tests/test_signal_generator_multi_source.py` (13 tests passing)

**Findings:**

1. ✅ **Constructor back-compat:** `def __init__(self, aggregator, db, settings: "Settings | None" = None)`. Two-arg legacy form `SignalGenerator(aggregator, db)` works — falls back to `SignalGeneratorMultiSourceSettings()` defaults via lazy import inside `__init__`. Both production call sites pass `settings=settings`.
2. ✅ **Multi-source classifier (`_evaluate_signal`):** correct dropped-inactive-component logic. A component with score below `component_min_active` is excluded from the weighted sum (does NOT pull toward NEUTRAL by occupying weight). Empty-active-set → NEUTRAL with explicit reason. Weighted sum renormalised over active components only.
3. ✅ **Component score formulas** match dataclass docstring:
   - `s_sentiment = clamp(sentiment, -1, 1)`
   - `s_fg = clamp((50 - fg) / fg_normalize_range, -1, 1)` (CONTRARIAN — F&G low → bullish)
   - `s_funding = clamp(-funding / funding_normalize, -1, 1)` (INVERTED — high positive funding = bearish)
   - `s_oi = clamp(oi_change / oi_normalize_pct, -1, 1)`
4. ✅ **Phase 29 confidence-based downgrade preserved** (lines 158-188): STRONG_BUY/SELL with conf<strong → BUY/SELL; BUY/SELL with conf<buy → NEUTRAL.
5. ✅ **Logs:** `SIG_GEN_INPUT` (active flags + raw values) before classification; `SIG_CLASSIFY` (component scores + active flags + direction_score + final type) inside `_evaluate_signal`. Both pair on the same coin.
6. ✅ **Settings dataclass validation** (`__post_init__`): all 4 weights in (0, 1]; threshold ordering `0 < buy_threshold < strong_threshold ≤ 1`; normalisers > 0. Verified by 5 negative tests.

### Phase 2 — `StructureEngine.diagnose_none` + `XRAY_CLASSIFY_SUMMARY` extension

**Files audited:**
- `src/analysis/structure/structure_engine.py:805-986` (diagnose_none, 180 lines)
- `src/analysis/structure/structure_engine.py:676-803` (classify_setup — for parity check)
- `src/workers/structure_worker.py:140-227` (integration)
- `tests/test_setup_classifier_diagnose.py` (6 tests passing)

**Findings:**

1. ✅ **Decision-tree parity:** diagnose_none reads same cfg fields as classify_setup (`fvg_ob_min_confluence`, `sweep_min_displacement_pct`), normalises mtf and smc identically (`mtf/10`, `smc/100`), checks the same conditions per branch.
2. ✅ **Branch scoring:** each condition contributes 1; structural break weights BOS at 2 (the primary input); tie-break by score; if all 0 → "none".
3. ✅ **Weakest input identification:** computes input_scores dict (mtf, smc, direction_alignment, fvg_present, ob_present, sweep_present), returns the lowest-scoring key.
4. ✅ **Output dict** includes everything an operator needs to tune thresholds: closest_type, missed_by, weakest_input, mtf_score_01, smc_01, direction, structure, has_fvg/has_ob/has_active_sweep.
5. ✅ **Caller integration:** structure_worker emits XRAY_NONE_REASON at INFO when setup_type=="none" (per-coin), wrapped in try/except — diagnostic is non-fatal advisory.
6. ✅ **conf_p50/p95 percentile math:** `_p50 = _sorted[max(0, int(0.50 * (_n - 1)))]` — safe against empty list, consistent with rest of codebase.

### Phase 3 — Regime hysteresis + REGIME_PERCOIN_SUMMARY

**Files audited:**
- `src/strategies/regime.py:175-212` (hysteresis logic)
- `src/config/settings.py:737-762` (RegimeSettings + __post_init__)
- `src/workers/regime_worker.py:200-247` (REGIME_PERCOIN_SUMMARY)
- `config.toml:502` (hysteresis_count)

**Findings:**

1. ✅ **Hysteresis read:** `_hyst = int(getattr(cfg, "hysteresis_count", 2))` — defaults to 2 for back-compat with anything constructing a bare RegimeSettings.
2. ✅ **REGIME_PENDING formatting:** `count={new_count}/{_hyst}` — shows actual configured value.
3. ✅ **Validation:** `__post_init__` raises `ValueError` if `hysteresis_count < 1` (no infinite-pending bug).
4. ✅ **REGIME_PERCOIN_SUMMARY:** uses merged cache (`detector._per_coin_regimes.values()`) so distribution is stable across ticks; sorted desc by count for readability; includes `global=` + `divergent=` for context.

### Phase 4 — STRAT_L1/L2/L3/L4 distribution metrics

**Files audited:**
- `src/workers/strategy_worker.py:519-547` (STRAT_L1_DONE)
- `src/workers/strategy_worker.py:594-628` (STRAT_L2_DONE)
- `src/workers/strategy_worker.py:648-667` (STRAT_L3_DONE)
- `src/workers/strategy_worker.py:806-827` (STRAT_L4_HANDOFF)

**Findings:**

1. ✅ **Strictly additive:** every original STRAT_L1/L2/L3/L4 tag preserved on the next line — back-compat with downstream parsers/dashboards.
2. ✅ **STRAT_L1_DONE:** per-strategy fire rate via `_sig_per_strat`; top_firing[5] + non_firing[5] (scanned-but-zero); avg = signals/strategies.
3. ✅ **STRAT_L2_DONE:** percentile math via index-into-sorted-list (p25/p50/p75/p95); component avgs from base/confluence/context/quality fields; component-string sorted in declared order.
4. ✅ **STRAT_L3_DONE:** consensus distribution (`_cons_dist` sorted desc by count); `size_mult_avg` defaulted to 0 if empty.
5. ✅ **STRAT_L4_HANDOFF:** four cache sizes (score_cache, consensus, consensus_summary, hints_top20) — operator sees pipeline depth at a glance.

### Phase 5 — CoinPackage validator + Scanner integration

**Files audited:**
- `src/core/coin_package_validator.py` (full read, 194 lines)
- `src/workers/scanner_worker.py:830-908` (integration)
- `src/config/settings.py:1688-1717` (CoinPackageValidatorSettings)
- `config.toml:1064-1075` ([coin_package_validator])
- `tests/test_coin_package_validator.py` (11 tests passing)

**Findings:**

1. ✅ **Pure function + frozen result:** `validate_package()` is purely functional, no service deps; returns `@dataclass(frozen=True) ValidationResult`.
2. ✅ **Scoring formula:** `(req_score + 0.5 * opt_score) / (req_count + 0.5 * opt_count)` clamped to [0,1].
3. ✅ **5 required + 8 optional fields** match the docstring; SL/TP/RR optional only counted when `setup_present` (avoids penalising "force-included with setup_type=none" packages).
4. ✅ **Settings:** thresholds tunable; `__post_init__` validates `0 < fail_below < warn_below ≤ 1` and `staleness_fail_seconds > 0`.
5. ✅ **Quarantine:** FAIL → `continue` (not inserted into `packages` dict) → never reaches Stage 2 brain prompt.
6. ✅ **Logs:** PACKAGE_VALIDATE per-package (verdict + completeness + missing + stale); PACKAGE_QUARANTINED only on FAIL at WARNING; PACKAGE_VALIDATE_SUMMARY per-cycle rollup.

### Phase 6 — `cache_freshness` + `/health`

**Files audited:**
- `src/core/cache_freshness.py` (full read, 70 lines)
- `src/workers/kline_worker.py:208-216` (record_write hook)
- `src/workers/structure_worker.py:137-144` (record_write hook)
- `src/workers/scanner_worker.py:884-952` (record_write + CYCLE_FRESHNESS rollup)
- `src/telegram/handlers/system.py:223-256` (Data Freshness section)
- `tests/test_cache_freshness.py` (7 tests passing)

**Findings:**

1. ✅ **Concurrency:** module-level singleton dict + `RLock` (paranoid; covers future thread-executor scenarios).
2. ✅ **API:** `record_write`, `read_age_ms`, `get_snapshot` (shallow-copies under lock for /health iteration safety), `reset` (test-only).
3. ✅ **All 3 cache writers hooked:** klines (per `(symbol, timeframe)`), xray (per symbol), packages (cache-wide + per-symbol). Every hook in try/except so observability never blocks production.
4. ✅ **CYCLE_FRESHNESS rollup:** scanner_worker emits per-cycle p50/p95 ages for klines/xray/packages; "unknown" if no writes yet; failure path emits CYCLE_FRESHNESS_FAIL at DEBUG.
5. ✅ **/health Data Freshness section:** reads same snapshot; per-cache `min/med/max/n` summary; failure caught with `freshness query failed:` line.

### Phase 7 — Sentiment categorical reasons

**Files audited:**
- `src/intelligence/sentiment/aggregator.py:55-86` (init detection)
- `src/intelligence/sentiment/aggregator.py:160-223` (no-data branch)
- `tests/test_sentiment_aggregator_tags.py` (3 tests passing)

**Findings:**

1. ✅ **Init detection:** `_reddit_intentionally_disabled = True` iff `settings.reddit.client_id` is empty/missing. Defensive try/except keeps the aggregator constructible if settings introspection fails.
2. ✅ **Branch selection:**
   - Reddit disabled by config → `SENT_DEGRADED_MODE` only (no SENT_UNKNOWN — operator already saw `SENTIMENT_DEGRADED_MODE` at init).
   - Reddit configured but empty for this symbol → `SENT_NO_DATA` + `SENT_UNKNOWN` (legacy alias retained).
3. ✅ **Behaviour preserved:** `overall=0.0`, `level=UNKNOWN` in both branches. `SENT_NEUTRAL` always emitted for back-compat.
4. ✅ **Diagnostic context:** every tag carries sym, fg, change_24h.

### Phase 9 — Workers subsystem instrumentation

**Files audited:**
- `src/workers/scanner_worker.py:80-124` (3 accessors with SERVICE_ACCESSOR_FAIL)
- `src/workers/price_worker.py:185-237` (PRICE_SKIP_INVALID + PRICE_WS_TICK_FAIL)
- `src/workers/strategy_worker.py:242-315` (STRAT_SKIP_STALE_AGG + STRAT_TA_DONE)
- `src/workers/regime_worker.py:125-141` (REGIME_RESTORE_FAIL enrichment)

**Findings:**

1. ✅ **A1:** three accessors `_get_setup_score / _get_strategy_score / _get_signal_confidence` each emit SERVICE_ACCESSOR_FAIL at DEBUG on exception path — sparse, matches per-coin per-cycle traffic only on real failures.
2. ✅ **A4:** PRICE_WS_TICK_FAIL per-drop at WARNING with cumulative_dropped — pairs with the existing every-50 rollup.
3. ✅ **A5:** PRICE_SKIP_INVALID at DEBUG on `last_price <= 0` — surfaces silent drops.
4. ✅ **A7:** STRAT_SKIP_STALE_AGG replaces per-coin N-blast with one rollup at INFO (count, oldest, newest, sample_syms[5]).
5. ✅ **A8:** STRAT_TA_DONE per-cycle aggregate (fast/slow/max_ms/total_ms).
6. ✅ **A9:** REGIME_RESTORE_FAIL enriched with `loaded_so_far` and `universe` — distinguishes partial vs full failure.

### Phase 10 — Trading + LayerManager

**Files audited:**
- `src/trading/services/order_service.py:479-492` (ORDER_ATTEMPT)
- `src/core/layer_manager.py:710-754` (BRAIN_TRADES_DROPPED ×2 paths)

**Findings:**

1. ✅ **ORDER_ATTEMPT:** at very top of `place_order`, BEFORE gate enforcement — a rejected entry still leaves an audit trail. Includes link_id/sym/side/purpose/qty/force.
2. ✅ **BRAIN_TRADES_DROPPED:** structured WARNING for both new_trades drop AND urgent_position_action drop. Each carries layer=3_inactive + count + sample_syms[10] (defensive against missing `getattr(t, "symbol", "?")`). Original free-text warning preserved beside.

### Phase 11 — Brain instrumentation

**Files audited:**
- `src/brain/claude_code_client.py:540-557` (CLAUDE_PARSE_FAIL)
- `src/brain/strategist.py:200-217` (POSITION_INVALIDATED)

**Findings:**

1. ✅ **CLAUDE_PARSE_FAIL** at WARNING **only** on `json.JSONDecodeError` — distinct from CLAUDE_CALL_FAIL (subprocess/API errors). Carries reason=json_decode + truncated raw_response[:100] for prompt-engineering diagnostics.
2. ✅ **POSITION_INVALIDATED** at INFO with sym + reason=close_broadcast + invalidated_count. Original DEBUG `STRAT_POS_INVALIDATE` retained as back-compat for any downstream parser.

### Phase 12 — Intelligence external fetches

**Files audited:**
- `src/intelligence/altdata/fear_greed.py:55-141` (FETCH_FAIL + FALLBACK)
- `src/intelligence/altdata/funding_rates.py:75-89` (per-symbol categorisation)

**Findings:**

1. ✅ **G1 FEAR_GREED_FETCH_FAIL** logs URL + status + truncated body **before** raising APIError. Body read wrapped in try/except for `<read_failed>` fallback.
2. ✅ **G2 FUNDING_FETCH_FAIL** per-symbol categorised by string-match: `timeout` / `rate_limit` (covers 429 + Bybit 10003) / `invalid` (covers 10001 + "invalid symbol") / `error` (catch-all). `invalid` lowered to DEBUG (expected for non-existent symbols), others at WARNING.
3. ✅ **G3 FEAR_GREED_FALLBACK** at WARNING when serving stale cached value — `cached_value`, `age_h`, `max_age_h`, `reason`. Defensive against `cached.timestamp` possibly None.

### Phase 13 — Pipeline blind spots

**Files audited:**
- `src/brain/strategist.py:1370-1396` (STRATEGIST_PACKAGES_READ at CALL_A)
- `src/core/trade_coordinator.py:137-147` (POSITION_CLOSE_REASON)

**Findings:**

1. ✅ **STRATEGIST_PACKAGES_READ:** emitted at CALL_A read site; carries call=CALL_A, count, age_min_s, age_max_s, reader=brain_call_a. Defensive — `[-1, -1]` if any built_at missing or comprehension fails.
2. ✅ **POSITION_CLOSE_REASON:** emitted in `set_close_reason()` at INFO — captures cause-of-close at decision time, not just at placement time. Pairs with downstream SHADOW_POSITION_CLOSE.

### Phase 14 — Context-ID hygiene

**Files audited:**
- `src/workers/base_worker.py:149-156` (wid generation)
- `src/workers/manager.py:2118-2135` (wid in WM_START/STOP/CRASH)
- `src/trading/services/order_service.py:170-197` (actor= in ORDER_BLOCKED)

**Findings:**

1. ✅ **`wid = uuid4().hex[:8]`** generated in BaseWorker.__init__ — 8-char hex (per-process unique, not global) — distinguishes restarts of same `name`.
2. ✅ **Threaded into WM_START/STOP/CRASH** (both new and crashed-restart paths use same wid).
3. ✅ **actor= mapping:** `layer3_off/race → layer3_auto`; `lm_boot_not_ready/lm_deadline_exceeded → system_auto`; else → `gate`. Maintains audit semantics — every block has a who.

### Phase 15 — Schema migration

**Files audited:**
- `src/database/migrations.py:11-12` (SCHEMA_VERSION)
- `src/database/migrations.py:1281-1297` (10 new ALTER TABLE statements)
- `src/database/migrations.py:1336-1391` (idempotency machinery)

**Findings:**

1. ✅ **SCHEMA_VERSION 24 → 25.**
2. ✅ **All 10 columns `DEFAULT NULL`** — preserves existing rows on upgrade.
3. ✅ **Naming:** `signal_buy_pct`, `signal_sell_pct`, `signal_neutral_pct`, `xray_setup_type_count`, `regime_distribution_json` (TEXT), `l1_strategies_fired_avg`, `l2_score_p50`, `l3_consensus_dist_json` (TEXT), `package_completeness_avg`, `freshness_klines_to_xray_p50`. All snake_case + descriptive.
4. ✅ **Idempotency:** PRAGMA `table_info` pre-flight check skips existing columns (cached per table for cycle); duplicate-column exception fallback at DEBUG (belt-and-braces for race conditions).
5. ✅ **Schema-only:** explicit comment that the populator is a documented follow-up (cycle_metrics aggregator subscribing to the per-cycle tags).

---

## Settings round-trip

Verified end-to-end with the live `config.toml`:

```
config.toml [signal_generator.multi_source]:
  sentiment_min_active=0.05  fg_min_active=0.10
  funding_min_active=0.20    oi_min_active=0.20
  sentiment_weight=0.40       fg_weight=0.25
  funding_weight=0.20         oi_weight=0.15
  buy_threshold=0.25          strong_threshold=0.55
  fg_normalize_range=30.0     funding_normalize=0.005
  oi_normalize_pct=5.0
config.toml [coin_package_validator]:
  fail_below=0.50  warn_below=0.85  staleness_fail_seconds=300.0
config.toml [regime]:
  hysteresis_count=2
```

Each value reaches its consumer via:
1. `tomllib.load()` → raw dict
2. `_build_signal_generator(...)` / `_build_coin_package_validator(...)` / `_build_regime(...)` → typed dataclass
3. `Settings._load_fresh` → `Settings.signal_generator.multi_source.*` etc.
4. Consumer reads via `self._ms_cfg.<field>` / `self.settings.<field>`.

Validation negative tests (all fired correctly):
- `sentiment_weight=2.0` → `ValueError: signal_generator.multi_source.sentiment_weight must be in (0, 1]`
- `buy_threshold=0.7, strong_threshold=0.5` → `ValueError: must have 0 < buy_threshold < strong_threshold ≤ 1`
- `fail_below=0.9, warn_below=0.5` → `ValueError: must have 0 < fail_below < warn_below ≤ 1`
- `hysteresis_count=0` → `ValueError: regime.hysteresis_count must be >= 1`

---

## Naming + log-tag consistency

All 30 new tags emit from at least one source file (verified by grep):

```
SIG_GEN_INPUT (1), SIG_CLASSIFY (1), XRAY_NONE_REASON (1),
XRAY_CLASSIFY_SUMMARY (2), REGIME_PERCOIN_SUMMARY (2), REGIME_PENDING (1),
STRAT_L1_DONE (1), STRAT_L2_DONE (1), STRAT_L3_DONE (1), STRAT_L4_HANDOFF (1),
PACKAGE_VALIDATE (2), PACKAGE_VALIDATE_SUMMARY (2), PACKAGE_QUARANTINED (1),
CYCLE_FRESHNESS (2), SENT_DEGRADED_MODE (2), SENT_NO_DATA (1),
SERVICE_ACCESSOR_FAIL (1), PRICE_SKIP_INVALID (1), PRICE_WS_TICK_FAIL (1),
STRAT_SKIP_STALE_AGG (1), STRAT_TA_DONE (1), ORDER_ATTEMPT (1),
BRAIN_TRADES_DROPPED (1), CLAUDE_PARSE_FAIL (1), POSITION_INVALIDATED (1),
FEAR_GREED_FETCH_FAIL (1), FUNDING_FETCH_FAIL (1), FEAR_GREED_FALLBACK (1),
STRATEGIST_PACKAGES_READ (1), POSITION_CLOSE_REASON (1)
```

All tags follow the project convention:
- UPPERCASE_WITH_UNDERSCORES
- Domain prefix (SIG_, XRAY_, REGIME_, STRAT_, PACKAGE_, CYCLE_, SENT_, SERVICE_, PRICE_, ORDER_, BRAIN_, CLAUDE_, POSITION_, FEAR_GREED_, FUNDING_, STRATEGIST_)
- Format `TAG | k=v k=v | {ctx()}` with the ContextVar suffix (did=/wid=/tid=/sid=)
- `loguru` via `get_logger("component")` per module's domain

---

## Test verdicts

### Module 1 unit tests (added in this work)

```
tests/test_signal_generator_multi_source.py   13 passed
tests/test_setup_classifier_diagnose.py        6 passed
tests/test_coin_package_validator.py          11 passed
tests/test_cache_freshness.py                  7 passed
tests/test_sentiment_aggregator_tags.py        3 passed
                                              ───────────
                                              40 passed in 0.91s
```

### Full project regression

```
$ pytest -q --tb=short --ignore=tests/test_phase7
1590 passed, 11 warnings in 170.06s (0:02:50)
```

(The 11 warnings are pre-existing NumPy divide-by-zero in ADX trend indicator, unrelated to this work.)

### Pre-existing collection errors (not introduced)

```
tests/test_phase7/test_executor.py        ImportError: src.brain.executor
tests/test_phase7/test_prompt_builder.py  ImportError: src.brain.prompt_builder
tests/test_phase7/test_scheduler.py       ImportError: src.brain.scheduler
```

Last touched in commit `fea5a73` (initial project commit, pre-Layer-1-restructure). Stale dead tests referencing removed modules — orthogonal to this work, flagged as a separate housekeeping item.

### Settings round-trip integration (executed by hand)

All 17 new fields read from real `config.toml` → reach consumers with correct values. 5 negative validation tests fire as expected.

### Smoke imports

All 25 modified production modules + the 5 new test modules import cleanly under Python 3.10:

```
src.intelligence.signals.signal_generator   OK
src.config.settings                          OK
... (25 modules)
                                          ─────
                                   25/25 OK
```

---

## Hard-Rules adherence

| Rule | How honoured (in this audit) |
|---|---|
| Root cause, not symptom | Phase 1 fixed the sentiment hard gate, not just emitted SIG_DOWNGRADE; Phase 2 added explainability to the same classifier (no behavioural change to mask the issue); Phase 5 quarantines bad packages instead of injecting placeholders. |
| Investigation before implementation | `dev_notes/phase0_quality_fixes/` (7 issue files) and `dev_notes/phase8_observability_investigation.md` predate every line of code in their respective modules. |
| Understand before touching | Each modified file was read end-to-end before edit; call-site graph traced via grep; per-phase report cites file:line refs. This audit re-verified by reading every modified file again. |
| No assumptions | Q1-Q4 user decisions guided ambiguous choices (multi-source classifier vs sentiment fallback; categorical reason refinement vs fallback). Every claim backed by file:line evidence. |
| Production-quality | Type-annotated public APIs (verified via mypy); fail-loud validation (verified via 5 negative tests); structured logging via `get_logger(component)` + `ctx()`; settings-tunable defaults. |
| Per-phase atomic commits | 32 commits, each ≤1 logical change; code/tests/reports separate; verified by `git log --oneline pre-output-quality-fix..HEAD | wc -l`. |
| Naming convention | Snake_case for fields/columns/test files; UPPER_CASE for log tags; `_underscore_prefix` for private; consistent with the rest of the codebase. |

---

## CLAUDE.md "analyse before touching" check

Before this audit pass, every modification I made was preceded by reading the file end-to-end. During this audit I re-read every modified file and verified every integration point. The static-analysis cleanup commit (`20b37d7`) only:
- Reformatted log lines that were >100 chars (no semantic change).
- Removed truly unused imports verified by grep across the file.
- Added a `Settings | None` type annotation to a parameter that already had a docstring describing its type.
- Reverted a `datetime.UTC` (Py3.11+) autofix to `timezone.utc` (Py3.10-compat) to match the rest of the codebase.

No variables, functions, or blocks were deleted from production code. All test assertions were preserved.

---

## Final verdict

| Property | Status |
|---|---|
| 7 output-quality issues addressed at root cause | ✅ |
| 30 observability gaps closed or deferred with reason | ✅ |
| 25 modified modules import cleanly under Py3.10 | ✅ |
| 1590/1590 active tests passing | ✅ |
| 40/40 new unit tests passing | ✅ |
| 30 new tags wired into production emission paths | ✅ |
| Settings round-trip from config.toml — every new field | ✅ |
| Validation rules fail loudly on bad input (5 negative tests) | ✅ |
| Atomic per-phase commits with rollback path | ✅ |
| Per-phase reports written | ✅ |
| Static analysis: 0 newly-introduced ruff errors | ✅ |
| Static analysis: 0 newly-introduced mypy errors in code I authored | ✅ |
| Naming convention adhered | ✅ |
| Hard rules + CLAUDE.md "analyse before touching" honoured | ✅ |

**Module 1 + Module 2 are deep-audited and operator-ready for deployment alongside the dead-workers fix.**
