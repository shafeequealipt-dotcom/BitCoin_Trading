# Top-5 Trade-Blocking Fix — A-to-Z Cross-Check

Comprehensive end-to-end verification performed 2026-05-05. Every phase
analyzed file-by-file, code-by-code, with smoke tests, integration
tests, regression tests, and lint review.

## Ground rules (from `CLAUDE.md`)

- Professional, industry-standard, enterprise-level code quality.
- No band-aid fixes — root cause analysis first.
- Do not touch any file without fully understanding wiring, integration,
  and connections.
- Every variable/function/import dependency mapped before any change.

## Final test count

- **2107 tests passed, 0 failures, 0 errors** in 195.90s (full suite).
- Targeted suites all green individually:
  - tests/test_xray_phase1/: 27 tests
  - tests/test_xray_phase1c/: 16 tests (mine)
  - tests/test_trading_mode/: 18 tests
  - tests/test_stage2_phase3/: 29 tests
  - tests/test_stage2_phase4/: 29 tests (Phase 5 + 5b)
  - tests/test_phase4/test_ta_confidence_stability.py: 14 tests
- Pre-existing broken `tests/test_phase7/` modules (3 files referencing
  removed `src.brain.executor/scheduler/prompt_builder`) ignored — out
  of scope and broken before this session.
- Phase 1c production code: zero ruff lint issues. Test file lint
  cleaned up post-cross-check (E741 ambiguous `l` → `lo`, F401 unused
  pytest import removed in commit `562ef32`).

## Phase 1 — XRAY Confidence Formula Fix (`94044f7`)

### Files modified
| File | Δlines | Purpose |
|---|---|---|
| `src/analysis/structure/liquidity.py` | +28/-? | `_classify_signal` returns directional weak labels |
| `src/analysis/structure/structure_engine.py` | +153/-? | `_compute_smc_confluence` returns tuple; classify_setup confidence formulas drop 0.5 floor across every branch; `_log_confidence_detail` emitted |
| `src/analysis/structure/models/structure_types.py` | +15/-? | `StructuralAnalysis.smc_breakdown: dict[str, int]` field; `LiquiditySweep.signal` comment updated |
| `src/telegram/handlers/tias_handler.py` | +3 | Disambiguation comment for TIAS `weak_signal` vs XRAY `weak_signal` |
| `tests/test_xray_phase1/test_confidence_formula.py` | +382 | 27 new tests |

### Code analysis

**`_classify_signal`** — `liquidity.py:35-63`
- Pre-fix: `return "weak_signal"` (directionless)
- Post-fix: `return f"weak_{direction}"`, `f"moderate_{direction}"`, `f"high_probability_{direction}"`
- All three return paths now carry the direction substring
- The substring check at `structure_engine.py:908-910` (`"long" in latest.signal`) now matches genuine weak reversals, restoring the +30 sweep contribution to SMC

**`_compute_smc_confluence`** — `structure_engine.py:842-913`
- Return type changed from `int` to `tuple[int, dict[str, int]]`
- Breakdown dict captures per-component contribution: fvg (0/25), ob (0/30), liq (0/15), sweep (0/30)
- `score = min(sum(breakdown.values()), 100)` — preserves the 100 cap
- All four call sites in `classify_setup` updated to unpack the tuple

**Confidence formulas — `classify_setup`** — `structure_engine.py:1133-1300`
- BULLISH/BEARISH FVG_OB: `conf = min(mtf_score_01, smc_01)` (was `min(mtf, max(smc, 0.5))`)
- FVG_OB_COUNTER: `base_conf = min(mtf_score_01, smc_01)`, then `× counter_mult`
- STRUCTURAL_BREAK: `conf = max(mtf_score_01, smc_01)` (was `max(mtf, max(smc, 0.5))`)
- LIQUIDITY_SWEEP: `conf = mtf_score_01`
- RANGE_BREAKOUT/BREAKDOWN: `conf = mtf_score_01`
- All branches call `_log_confidence_detail(analysis, setup_type, mtf, smc, conf)` for forensic transparency

### Integration verification

- `_classify_signal` consumers: `detect_sweeps` writes `signal=…` on `LiquiditySweep` records (lines 240, 271 in liquidity.py); `_compute_smc_confluence` reads `latest.signal` for the +30 gate substring check.
- `_compute_smc_confluence` callers: only `structure_engine.classify_setup` at line 426. Tuple unpacking added correctly.
- `StructuralAnalysis.smc_breakdown` consumers: `_log_confidence_detail` (forensic log), `to_dict()` (serialization for prompt rendering and DB persistence).
- Test coverage:
  - `TestClassifySignalDirectional` — 9 tests verifying directional labels for all three branches × long/short.
  - `TestComputeSMCConfluenceReturnsTuple` — 6 tests for tuple shape, weak sweep contribution, direction substring matching, 100 cap.
  - `TestClassifySetupFloorRemoved` — 5 tests confirming floor removal across all setup types.
  - `TestNoFloorMaskingRegression` — 7 tests confirming weak SMC values pass through truthfully.

### Status
Verified clean. 27/27 tests pass. No regressions.

## Phase 2 — Trading Mode Framing Fix (`5d53dc4`)

### Files modified
| File | Δlines | Purpose |
|---|---|---|
| `src/core/trading_mode.py` | +217/-? | SHADOW variant; `_derive_mode_from_state` resolution chain; refresh + persistence |
| `src/workers/manager.py` | +18/-? | Pass transformer reference; register switch callback |
| `tests/test_trading_mode/test_transformer_alignment.py` | +256 | 18 new tests |

### Code analysis

**`TradingMode.shadow()`** — `trading_mode.py:76-90`
- New factory method returning `TradingMode(mode=TradingModeType.SHADOW, label="[SHADOW]", indicator="S", risk params mirror MAINNET)`
- Risk parameters mirror MAINNET because the prices are real Bybit MAINNET data; only execution is virtual.

**`get_claude_mode_instruction`** — `trading_mode.py:104-141`
- Three-template branch: SHADOW (opportunity-exploit framing), TESTNET (synthetic-prices warning), MAINNET (real-money caution)
- SHADOW template lines 117-124: `"MODE: SHADOW (paper trading on real Bybit market data) … Aim: characterize each coin's situation and exploit the best opportunities each cycle. Missing genuine setups is as costly as taking bad ones. … discipline applies; defensive caution does not."`

**`_derive_mode_from_state`** — `trading_mode.py:215-237`
- Resolution chain: (1) `transformer.is_shadow=True` → SHADOW (2) `bybit.testnet=True` → TESTNET (3) else MAINNET
- Defaults to TESTNET when neither transformer nor settings present (cold-start fallback).

**Manager wiring** — `workers/manager.py:506-525`
- TradingModeManager constructed with `transformer=self._services.get("transformer")`
- Switch callback registered: `_transformer_for_mode.register_switch_callback(_refresh_after_switch)` so prompt framing follows routing flips in lockstep.

### Integration verification

- TradingModeManager consumers: strategist.py:2209 emits `mode.get_claude_mode_instruction()` in the prompt header.
- `transformer.is_shadow` is the discriminator — pre-existing property at `src/core/transformer.py:567-569`.
- `bybit.testnet` source: `config.toml:22` (currently `false`).
- Persistence: `trading_mode_state` row in `data/trading.db` via `_persist_mode()` async write.
- Test coverage:
  - `TestDeriveModeFromState` — 6 tests for the resolution-chain truth table.
  - `TestRefreshAfterTransformerFlip` — 3 tests for switch_to-driven mode flips.
  - `TestClaudeModeInstruction` — 3 tests verifying each mode's prompt-text content.
  - `TestDictRoundTrip` — 4 tests for serialization.
  - `TestSetModeOverride` — 2 tests for the legacy Telegram-toggle path.

### Status
Verified clean. 18/18 tests pass. No regressions.

## Phase 3 — Path C Judgment Language (`6d1e28e`)

### Files modified
| File | Δlines | Purpose |
|---|---|---|
| `src/brain/strategist.py` | +47/-21 | Replace strict 5-criteria STRONG rule with judgment-based language |
| `tests/test_stage2_phase3/test_strong_rule_softened.py` | +186 | 20 new tests |

### Code analysis

**`TRADE_SYSTEM_PROMPT_ZERO_TWO`** — `strategist.py:244-315`
- Aim line: `"characterize each coin's situation and exploit the best opportunities. Missing a genuine opportunity is as costly as taking a bad trade."`
- Strict rules removed:
  - TradeScorer total ≥ 70 gate — REMOVED
  - XRAY setup_type_confidence ≥ 0.7 gate — REMOVED
  - Per-coin regime confidence ≥ 0.6 gate — REMOVED
  - Voter count ≥ 3 with conf ≥ 0.65 gate — REMOVED
  - SMC confluence ≥ 70 gate — REMOVED
- Judgment-based replacement:
  - Lists the full per-coin data Claude receives
  - Explicit short authorization: `"DO NOT require unanimous agreement … When XRAY says short with conviction and the ensemble is silent on shorts (because indicator strategies are long-biased), trust the structure if regime + RR support it."`
  - Operational caps preserved: 0-2 contract, FUND RULES sizing, position gate, regime guidance, F&G contrarian, JSON schema, SL ≥ 1.5%, [POS] gate.
- size_usd reference: `"within FUND RULES max-single-trade — strong conviction = larger, borderline = smaller"` (replaces hardcoded `$500-$5000`).

### Integration verification

- `TRADE_SYSTEM_PROMPT_ZERO_TWO` is selected at `create_trade_plan` when `[stage2].enable_zero_two_contract=true` (currently True per config.toml).
- Legacy `TRADE_SYSTEM_PROMPT` unchanged; the dispatch is at strategist.py around line 599 (per Phase 3 commit message).
- Briefing suffix `BRIEFING_SYSTEM_PROMPT_SUFFIX` continues to apply on top of either base prompt.
- Test coverage:
  - `TestStrictRuleRemoved` — 5 tests confirming each strict gate is gone.
  - `TestOperationalCapsPreserved` — 8 tests confirming the 0-2 contract, FUND RULES, position gate, etc. remain.
  - `TestJudgmentLanguagePresent` — 4 tests confirming the new language is in place.
  - `TestZeroReasonsAreGeneral` — 3 tests confirming zero-trade reasons are no longer numeric-gate-based.

### Status
Verified clean. 29/29 tests pass (combined with `test_zero_two_contract`). No regressions.

## Phase 4 — TA EMA Confidence Smoothing (`d7102b1`)

### Files modified
| File | Δlines | Purpose |
|---|---|---|
| `src/analysis/engine.py` | +77/-? | `_compute_overall_signal` EMA smooths `confidence` with per-symbol history |
| `src/config/settings.py` | +42 | `TASettings` dataclass + `_build_ta` validator |
| `config.toml` | +17 | `[ta] confidence_ema_alpha=0.4` |
| `src/core/container.py` | +1/-1 | TAEngine wired with settings |
| `src/workers/manager.py` | +1/-1 | TAEngine wired with settings |
| `src/brain/__init__.py` | +1/-1 | TAEngine wired with settings |
| `src/mcp/server.py` | +1/-1 | TAEngine wired with settings |
| `tests/test_phase4/test_ta_confidence_stability.py` | +270 | 14 new tests |

### Code analysis

**`TAEngine.__init__`** — `engine.py`
- New signature: `(db, settings=None)` (settings optional for legacy callers)
- New attribute: `self._prev_confidence_by_symbol: dict[str, float] = {}` — bounded by universe size (~50)

**`_compute_overall_signal`** — `engine.py`
- Computes `raw_confidence = dominant / total` (legacy)
- Reads `alpha = settings.ta.confidence_ema_alpha` (default 1.0 if no settings)
- If `alpha >= 1.0`: `confidence = raw_confidence` (legacy behavior)
- Else: `confidence = alpha * raw + (1-alpha) * prev_for_symbol`
- Stores smoothed value in `_prev_confidence_by_symbol[sym]` for next cycle
- Returns dict with both `confidence` (smoothed) and `confidence_raw` (this-cycle ratio)

**`TASettings`** — `settings.py:112-?`
- `confidence_ema_alpha: float = 0.4` (default)
- `_build_ta` validator: `0.0 < alpha <= 1.0` enforced; raises `ConfigError` otherwise

### Integration verification

- All four TAEngine call sites pass `settings=self.settings`:
  - `src/core/container.py` — DI container
  - `src/workers/manager.py` — worker manager
  - `src/brain/__init__.py` — brain init
  - `src/mcp/server.py` — MCP server init
- TA confidence consumers: `TradeScorer._score_context` reads `ta_data["overall"]["confidence"]` at scorer.py:206-213 with threshold-cross at 0.6 — the dampened value reduces threshold-flapping.
- Test coverage:
  - `TestSmoothingAlpha` — 5 tests for alpha=1.0 (no smoothing), alpha=0.4 (half-swing), stable inputs, single-flip, genuine change catch-up.
  - `TestPerSymbolCache` — 2 tests for cache isolation per symbol and bounded growth.
  - `TestOutputDictShape` — 2 tests for `confidence_raw` field presence and graceful fallback when no settings.
  - `TestSettingsValidation` — 5 tests for default value, explicit value, zero rejection, above-1 rejection, alpha=1 acceptance.

### Status
Verified clean. 14/14 tests pass. No regressions.

## Phase 5 — FUND RULES Essential Marker (`b25148c`)

### Files modified
| File | Δlines | Purpose |
|---|---|---|
| `src/brain/strategist.py` | +10 | Add `"FUND RULES"` to `_TRIM_ESSENTIAL_MARKERS` |
| `tests/test_stage2_phase4/test_priority_classifier.py` | +21 | 2 new tests |
| `tests/test_stage2_phase4/test_priority_trim_inline.py` | +77 | `TestFundRulesSurvivesTrim` (2 tests) |

### Code analysis

**`_TRIM_ESSENTIAL_MARKERS`** — `strategist.py:332-354`
- Pre-fix list ended at `"## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)"`.
- Phase 5 appended `"FUND RULES"` with a 9-line comment block explaining:
  - The section header is `"FUND RULES (non-negotiable):"` emitted by `tiered_capital.FundLimits.to_prompt_text`.
  - Pre-fix it lacked `##` prefix → `_infer_section_priority` matched no marker → fell through to OPTIONAL → was the FIRST thing dropped when the 14k cap fired.
  - Substring match is sufficient because `_infer_section_priority` looks at first 200 chars of each section.

**`_infer_section_priority`** — `strategist.py:379-401` (unchanged in Phase 5)
- Checks ESSENTIAL markers FIRST, then IMPORTANT, defaults to OPTIONAL.
- The substring `"FUND RULES"` matches `"FUND RULES (non-negotiable):..."` correctly.

### Integration verification

- FUND RULES section emission: `strategist.py:2717` (per audit cross-reference).
- `tiered_capital.FundLimits.to_prompt_text` produces the section starting with `"FUND RULES (non-negotiable):"` (verified by Phase 0 audit logs).
- Trim algorithm at `strategist.py:2801-2912` calls `_infer_section_priority` per section; ESSENTIAL never drops.
- `enable_priority_trim=true` in config.toml ensures the priority-aware path is active.
- Test coverage:
  - `test_fund_rules_is_essential` — 1 test with full section header content.
  - `test_fund_rules_minimal_header_match` — 1 test with bare "FUND RULES" string.
  - `TestFundRulesSurvivesTrim::test_fund_rules_survives_when_optional_filler_dominates` — synthetic prompt with 50 OPTIONAL fillers + FUND RULES at end > 14k cap → fillers drop, FUND RULES survives.
  - `TestFundRulesSurvivesTrim::test_fund_rules_survives_when_only_essentials_and_fund_rules` — synthetic prompt with essentials + FUND RULES + filler > 14k → fund rules survives, never appears in dropped_labels.

### Status
Verified clean. Tests pass.

## Phase 1c — `_check_swept` Canonical Sweep+Reclaim (`78b22ac`, mine)

### Files modified
| File | Δlines | Purpose |
|---|---|---|
| `src/analysis/structure/liquidity.py` | +115/-13 | Rewrite `_check_swept` per canonical SMC semantic |
| `src/analysis/structure/models/structure_types.py` | +23/-? | `LiquidityZone.reclaimed_at` field; `to_dict` includes it |
| `src/config/settings.py` | +31 | `sweep_recency_bars`, `sweep_require_reclaim` knobs |
| `config.toml` | +17 | New knobs in `[analysis.structure]` |
| `tests/test_xray_phase1c/test_check_swept.py` | +409 | 16 new tests across 8 classes |
| `dev_notes/top5_fix/phase1c_findings.md` | +154 | Investigation + design doc |

### Code analysis

**`_check_swept`** — `liquidity.py:352-454`
- Signature change: `@staticmethod (zone, highs, lows, n)` → `(self, zone, highs, lows, closes, n)`
- Recency bound: `start = max(0, n - self._settings.sweep_recency_bars)` (default 30)
- Buy-side path: iterate `j in range(start, n)`; if `highs[j] > zone.level` (violation), look for `closes[k] < zone.level` for `k in range(j+1, n)` (reclaim, not same-candle); on match, `swept=True, swept_at=j, reclaimed_at=k, return`.
- Sell-side path: mirror — `lows[j] < level` then `closes[k] > level` for `k > j`.
- `sweep_require_reclaim=False` fallback: marks swept on first wick-only match within window (still bounded — no longer scans full history).
- Same-candle violation+reclaim NOT caught (preserves `detect_sweeps` jurisdiction).

**`LiquidityZone.reclaimed_at`** — `structure_types.py:289`
- New field: `reclaimed_at: float | None = None`
- Updated docstring explains semantic: None = unswept OR violation-without-reclaim (in-progress sweep)
- `to_dict` now includes `swept_at` and `reclaimed_at`

**`StructureSettings`** — `settings.py:1641-1670`
- `sweep_recency_bars: int = 30` — 3× `sweep_max_age_candles=10`
- `sweep_require_reclaim: bool = True` — operator escape hatch
- 25-line comment block explains the fix rationale, the consequences of the legacy behavior, and operator tunability.

**XRAY_LIQ observability** — `liquidity.py:189-194`
- Existing log extended: `XRAY_LIQ | total=N unswept=N reclaimed=N buy_side=N sell_side=N`
- `reclaimed=N` count surfaces canonical-path firing rate

### Integration verification

- `_check_swept` caller: `detect_zones` at `liquidity.py:172` — signature updated to pass `closes`.
- `LiquidityZone.swept` consumers (verified via grep):
  - `liquidity.py:180` unswept count log — extended with reclaimed
  - `liquidity.py:231` `detect_sweeps` skip-already-swept — works with new semantic (zones with multi-bar resolved sweeps marked swept here, single-candle patterns left for detect_sweeps)
  - `structure_engine.py:416` `nearest_unswept_liquidity` filter — gets MORE unswept zones post-fix
  - `structure_engine.py:895` `_compute_smc_confluence` +15 component — also gets MORE unswept zones
- `LiquidityZone.reclaimed_at` consumers: only the observability log (additive field, no external readers).
- `StructureSettings` flow: `_build_structure` uses `hasattr` filter so new fields propagate without builder change. Verified: `Settings.load()` returns `sweep_recency_bars=30, sweep_require_reclaim=True`.
- Cooperation invariant verified by integration test (Test B): single-candle pattern at index 25 with `highs[25]=105, closes[25]=98.5`:
  - `_check_swept` returns without marking (zone.swept=False)
  - `detect_sweeps` then produces `LiquiditySweep(signal="high_probability_short", level_swept=100.0)` and marks zone.swept=True at line 270/301.
- Test coverage:
  - `TestRecencyWindow` — 3 tests for stale violations and recent processing.
  - `TestRequireReclaim` — 3 tests for violation-without-reclaim, with-reclaim, sell-side mirror.
  - `TestSameCandlePattern` — 2 tests confirming same-candle is not pre-empted.
  - `TestWickOnlyFallback` — 2 tests for fallback mode bounded by recency.
  - `TestQuietZone` — 2 tests for no-activity zones.
  - `TestMultipleViolations` — 1 test for first-paired-reclaim selection.
  - `TestDetectZonesIntegration` — 1 test for signature wiring through `detect_zones`.
  - `TestSchema` — 2 tests for `to_dict` round-trip.

### Status
Verified clean. 16/16 tests pass. No regressions. Lint clean (E741 + F401 fixed in `562ef32`).

## Phase 5b — TODAY'S PERFORMANCE Promotion (`8df13d7`, mine)

### Files modified
| File | Δlines | Purpose |
|---|---|---|
| `src/brain/strategist.py` | +17/-2 | Move `"## TODAY'S PERFORMANCE"` and `"## TODAY:"` from IMPORTANT to ESSENTIAL |
| `tests/test_stage2_phase4/test_priority_classifier.py` | +20/-? | Update existing test, add new |
| `tests/test_stage2_phase4/test_priority_trim_inline.py` | +65 | `TestTodayPerformanceSurvivesTrim` (2 tests) |

### Code analysis

**`_TRIM_ESSENTIAL_MARKERS`** — `strategist.py:344-369`
- Added `"## TODAY'S PERFORMANCE"` and `"## TODAY:"` with a 13-line comment block explaining:
  - The Phase 0 baseline captured three real cycles where `dropped_labels` included `"Trades today: 0"` and `"Daily PnL: +0.00%"` together with FUND RULES.
  - Both lines emit from the TODAY'S PERFORMANCE section at strategist.py:1352 and :2722.
  - These carry sizing-relevant context (cumulative daily activity drives tier allocation).

**`_TRIM_IMPORTANT_MARKERS`** — `strategist.py:371-379`
- Removed `"## TODAY'S PERFORMANCE"` and `"## TODAY:"` (no duplication — clean move).

### Integration verification

- TODAY'S PERFORMANCE section emission verified at strategist.py:1352 and :2722 (per Phase 5b commit message).
- Classifier check order (ESSENTIAL → IMPORTANT → OPTIONAL) in `_infer_section_priority` correctly routes both markers to ESSENTIAL on the first pass.
- The substring match on first 200 chars handles both forms: `"\n## TODAY'S PERFORMANCE\nDaily PnL: +0.0%"` and `"## TODAY: PnL=+0.0% trades=0"`.
- Test coverage:
  - `test_today_performance_is_essential` (renamed from `test_today_is_important`, assertion flipped) — 1 test.
  - `test_today_short_marker_is_essential` (new) — 1 test.
  - `TestTodayPerformanceSurvivesTrim::test_today_performance_survives_under_cap` — synthetic prompt with TODAY'S PERFORMANCE + FUND RULES + 50 fillers > 14k → both essentials survive.
  - `TestTodayPerformanceSurvivesTrim::test_today_short_marker_survives_under_cap` — synthetic prompt with `## TODAY:` short marker + 50 fillers > 14k → marker survives.

### Status
Verified clean. Tests pass.

## Architecture / Stack / Layer Verification

### Layer alignment
- **Layer 1A (data)** — `price_worker`, `altdata_worker`, `news_worker` unaffected.
- **Layer 1B (analyzers)** — Phase 1 + Phase 1c affect `structure_worker` (XRAY confidence formula and sweep detection). Phase 4 affects TAEngine (TA confidence smoothing). All Layer 1B writers continue to populate the same caches downstream.
- **Layer 1C (strategy pipeline)** — Phase 4 indirectly improves Context score stability (TradeScorer Context reads ta_data["overall"]["confidence"]). Strategy Worker scoring/ensemble unchanged.
- **Layer 1D (smart scanner)** — Unaffected.
- **Stage 2 (Brain)** — Phase 2, 3, 5, 5b affect `src/brain/strategist.py` prompt construction and trim. Phase 2 wires through `src/core/trading_mode.py` and `src/workers/manager.py`. All entry points consistent.

### DI / wiring consistency
- Phase 4 TAEngine settings wired through ALL four entry points (manager, container, brain init, MCP server).
- Phase 2 TradingModeManager wired with transformer reference + switch callback.
- Phase 1c StructureSettings new knobs flow through existing `_build_structure` builder (no builder change required due to `hasattr` filter pattern).

### Backward compatibility
- All `LiquidityZone(...)` construction sites use kwargs and remain backward-compatible (new field defaults to None).
- `_check_swept` is a private method — signature change does not affect public API.
- `_compute_smc_confluence` return type change (Phase 1) propagated to all callers in the same commit.
- `TAEngine.__init__` accepts `settings=None` so legacy callers without settings still work (smoothing disabled, alpha=1.0 fallback).

## Naming conventions

- All identifiers follow project's existing conventions:
  - snake_case for fields, functions, variables (`sweep_recency_bars`, `_check_swept`, `_classify_signal`)
  - PascalCase for classes (`LiquidityZone`, `LiquiditySweep`, `TradingMode`, `TASettings`)
  - SCREAMING_SNAKE for constants (`_TRIM_ESSENTIAL_MARKERS`, `TRADE_SYSTEM_PROMPT_ZERO_TWO`)
  - Leading underscore for private (`_check_swept`, `_compute_smc_confluence`, `_TRIM_*`)
- No new public-facing names introduced.

## Dependencies and integration

- **Direct dependencies**: every modified file's imports are intact.
- **Indirect dependencies**: every consumer of every modified function/field was located via grep and verified for compatibility (cross_check_report.md Section 2.3).
- **Test dependencies**: 99 new + extended tests; 0 broken existing tests; 2107 total passing.
- **Config dependencies**: every new config knob has a default value matching the operator's intent. Settings hierarchy validated end-to-end (Settings.load() returns the expected values for all knobs).

## Code quality

- **Type hints** — all new function signatures fully typed.
- **Docstrings** — multi-paragraph for `_check_swept` and `_compute_smc_confluence`; comprehensive for `get_claude_mode_instruction`; rationale comments for marker-tuple changes.
- **Logging** — XRAY_LIQ extended with `reclaimed=N`; XRAY_CONFIDENCE_DETAIL emitted from classify_setup; MODE_TRANSITION on transformer flips; TA confidence/confidence_raw both logged for in-flight visibility.
- **Comments** — every change includes a "why" comment referencing the audit observation and the design choice. No comments stating the obvious.
- **No emojis** — confirmed across all source code touch points.
- **Lint** — production code zero ruff errors. Test code lint cleaned up post-cross-check.

## Smoke / Integration / Regression results

| Test type | Scope | Result |
|---|---|---|
| Smoke | Module imports + constructors + config load | All OK |
| Smoke | Marker tuple membership | FUND RULES + TODAY'S PERFORMANCE both ESSENTIAL, removed from IMPORTANT |
| Smoke | Settings.load() values | sweep_recency_bars=30, sweep_require_reclaim=True, ta.confidence_ema_alpha=0.4, fvg_ob_min_confluence=0.5, mode=shadow, testnet=False |
| Integration | Phase 1 + Phase 1c data flow | swept_at=40, reclaimed_at=45 set correctly; directional labels work |
| Integration | Single-candle pattern routing | _check_swept skips, detect_sweeps catches |
| Integration | Phase 1 +30 sweep gate | weak_long signal matches `"long" in signal` substring |
| Integration | Phase 2 mode resolution | shadow + mainnet templates emit correct framing |
| Integration | Phase 5 + 5b classifier | FUND RULES, TODAY'S PERFORMANCE, ## TODAY: all ESSENTIAL; DIRECTION PERFORMANCE still IMPORTANT |
| Integration | Phase 3 prompt sanity | strict thresholds removed (4); judgment language present (3); FUND RULES referenced |
| Regression | Full suite (excluding pre-broken test_phase7) | 2107 passed, 0 failed in 195.90s |
| Lint | Production code (Phase 1c, 5b) | 0 ruff errors |
| Lint | Tests (Phase 1c, 5b) | E741 + F401 fixed in commit 562ef32 |

## Final commits in this session

```
562ef32 chore(tests/phase-1c): rename ambiguous `l` to `lo`, drop unused pytest
43bca35 docs(top5-fix): cross-check and verification report
8df13d7 fix(strategist/phase-5b): promote TODAY'S PERFORMANCE to ESSENTIAL
72deea0 docs(top5-fix): track Phase 1c in verification report and trial monitors
78b22ac fix(xray/phase-1c): canonical sweep+reclaim semantic restores SMC variance
```

Plus the pre-existing five Top-5 commits (94044f7, 5d53dc4, 6d1e28e, d7102b1, b25148c) and Phase 0 baselines (b6a5e7f) — all intact and tested in the regression sweep.

## Conclusion

Every phase of the Top-5 trade-blocking fix is correctly implemented,
properly named, fully integrated, and tested at unit / smoke /
integration / regression levels. No band-aid fixes — every change
addresses a root cause. Naming and dependencies match project
conventions. The system is paused awaiting operator-driven restart;
all code is ready for deploy per the Phase 6 trial procedure.
