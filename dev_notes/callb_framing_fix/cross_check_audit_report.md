# Cross-check Audit Report — CALL_B Framing + Flip Survival + Infrastructure Anomalies Definitive Fix

**Date:** 2026-05-07
**Audit scope:** all 8 phases shipped on `main` (parent `2ac091d`).
**Verdict:** **PASS** — all phases properly implemented, integrated, wired, and tested. Two wiring gaps were identified during the audit and immediately fixed (commits `7632cec` + `03106b9`).

---

## 1. Inventory

| Group | Count | Files |
|---|---|---|
| Source files modified | 10 | `config.toml`, `src/brain/__init__.py`, `src/brain/strategist.py`, `src/config/settings.py`, `src/core/thesis_manager.py`, `src/database/migrations.py`, `src/intelligence/sentiment/aggregator.py`, `src/intelligence/signals/signal_generator.py`, `src/mcp/server.py`, `src/strategies/performance_enforcer.py`, `src/workers/strategy_worker.py` |
| Test files (modified or new) | 7 | `tests/test_definitive_pipeline_e2e.py`, `tests/test_enforcer_clamp.py`, `tests/test_enforcer_survival_adjust.py` (NEW), `tests/test_sentiment_consumption_gate.py` (NEW), `tests/test_signal_non_destructive.py` (NEW), `tests/test_strategist_callb_prompt.py`, `tests/test_thesis_order_id_keying.py`, `tests/test_thesis_xray_flip.py` (NEW) |
| Dev notes | 7 | `dev_notes/callb_framing_fix/{phase0_baseline,phase1a_investigation,phase2c_audit,phase3_pragma_audit,phase6_trial,phase7_verification_report,cross_check_audit_report}.md` |

Total: 24 files, +2,624 / -67 lines.

---

## 2. Per-phase audit findings

### Phase 1B — `POSITION_SYSTEM_PROMPT` reframe

**File modified:** `src/brain/strategist.py:144-184` (definition + version sentinel) + `:457-462` (boot event in `__init__`).

**Architecture fit:** the system prompt is a module-level constant consumed at `:817` (`raw_response = await self.claude.send_message(prompt, POSITION_SYSTEM_PROMPT)`). Single read site.

**Integration:** the new `POSITION_SYSTEM_PROMPT_VERSION = 2` sentinel + `STRAT_CALL_B_REFRAMED` boot log fires once per `ClaudeStrategist.__init__`. Production construction site is `workers/manager.py:567`. ✓

**Naming:** the constant follows the existing pattern (`TRADE_SYSTEM_PROMPT`, `STRATEGIST_SYSTEM_PROMPT`, `POSITION_SYSTEM_PROMPT`); the version constant follows the docstring convention.

**Audit verdict:** clean.

### Phase 1C — drop original-thesis line

**File modified:** `src/brain/strategist.py:3199-3242` (per-position block construction).

**Architecture fit:** the `thesis_data = open_theses.get(symbol, {})` lookup at line 3206 is preserved — it carries SL/TP/leverage/APEX flip metadata which other lines in the same block consume. Only `thesis_text` (used solely at the dropped line 3174-equivalent) was orphan-removed.

**CLAUDE.md hard rule (the `thesis_mgr_early` precedent) was honoured:** every reference to `thesis_text` was grep-checked before deletion. Confirmed only one consumer.

**Integration:** thesis_manager is unchanged. The `thesis` column is still saved on entry by `strategy_worker.py:2136 (thesis=reasoning)` and still read by `get_recent_lessons` (line 217), `tias/collector.py:178`, etc. ✓

**Audit verdict:** clean.

### Phase 1D — aggressive-exploitation contract section

**File modified:** `src/brain/strategist.py:3127-3157`.

**Architecture fit:** the contract is a multi-line string appended to `sections` directly above the per-position blocks. Coherent with `POSITION_SYSTEM_PROMPT` (Phase 1B). The per-cycle restatement gives Claude the framing right next to the data it's reasoning about.

**Integration:** ordering of sections (regime → sentiment → today PnL → header → contract → positions → cooldowns → urgent_queue) preserved.

**Audit verdict:** clean.

### Phase 1E — schema v28 + flip metadata persistence

**Files modified:**
- `src/database/migrations.py:12` (SCHEMA_VERSION 27→28) and `:1325-1349` (4 ALTER TABLE statements appended).
- `src/core/thesis_manager.py:54-65` (4 new kwargs on `save_thesis`); `:71-77` (INSERT expanded); `:97-104` (THESIS_FLIP_PERSISTED log); `:122-131` (SELECT expanded in `get_open_theses`).
- `src/workers/strategy_worker.py:1721-1729` (XRAY-flip site stores `_xray_flip_rr_long` and `_xray_flip_rr_short` on the trade dict); `:2179-2225` (save_thesis call site forwards 4 new kwargs).
- `src/brain/strategist.py:3243-3287` (unified FLIPPED notice render with concrete RR for XRAY-driven flips, fallback to APEX-style for legacy rows).

**Architecture fit:**
- Schema v28 ALTER TABLE statements are additive with `NOT NULL DEFAULT` so legacy rows + every existing SELECT in the codebase stay valid (verified: `performance_enforcer._collect_stats` SELECTs only `symbol, direction, actual_pnl_pct, close_reason, exchange_mode`; `thesis_manager.get_recent_lessons` SELECTs explicit columns; `tias/collector.py` SELECTs explicit columns; `position_watchdog` SELECTs only entry-anchor v27 columns. **Zero `SELECT *` from `trade_thesis` in the codebase.**)
- The pre-flight column-exists check in `migrations.run_migrations` (lines 1356-1379) makes v28 idempotent.
- Schema v28 round-trip + idempotency verified via standalone test (`SCHEMA_VERSION=28`, expected cols present, re-run produces identical schema).
- New event `THESIS_FLIP_PERSISTED` fires only when `xray_flip_source` is non-empty (xray-driven flip) — apex-only flips don't trigger it because the v28 columns are at default for them. Behaviour matches plan.

**Integration:** the `_flip_source` / `_xray_flip_ratio` / `_apex_*` keys on the trade dict were already used by `strategy_worker.py:1809-1820` for reasoning-text enrichment; the new keys (`_xray_flip_rr_long`, `_xray_flip_rr_short`) are read only by the Phase 1E save-site. No collision.

**Naming:** `xray_flip_source`, `xray_flip_ratio`, `xray_flip_rr_long`, `xray_flip_rr_short` — consistent with existing `apex_flipped`, `apex_original_direction`, `apex_reason` (v23) and `entry_xray_confidence`, `entry_setup_type`, `entry_regime_at_open`, `entry_regime_confidence` (v27). 54 occurrences across 4 src files; no typos or aliases.

**E2E smoke verified:** 3-trade scenario (clean, apex-flipped, xray-flipped) renders correctly; THESIS_FLIP_PERSISTED fires only on the xray-flipped row; the prompt shows `"FLIPPED via APEX"` for apex-only and `"FLIPPED via XRAY ... 5.5x better"` for xray-driven. ✓

**Audit verdict:** clean.

### Phase 2A — SURVIVAL trigger to -12%, HALTED at -15%

**Files modified:**
- `config.toml:976-980` (`pnl_survival_pct = -12.0` + new `pnl_halted_pct = -15.0`).
- `src/config/settings.py:1398-1404` (`EnforcerSettings.pnl_survival_pct` default raised to `-12.0`; new `pnl_halted_pct: float = -15.0`).
- `src/strategies/performance_enforcer.py:73-87` (init reads `_pnl_halted_pct`); `:131-160` (clamp_leverage HALTED branch); `:164-186` (get_max_positions / get_min_score HALTED branches); `:201-228` (get_size_multiplier 5-band logic); `:266-287` (qualify_survival_trade halted-branch); `:332-359` (check_and_enforce 4-level computation); `:366-387` (ENFORCER_HALTED entry/exit log + state transition); `:481-489` (_get_level_change_reason `pnl_below_halted`).

**Architecture fit:**
- Settings → dataclass → enforcer init → check_and_enforce loop. Clean. The `_build_enforcer` function in `settings.py` already passes only fields with `hasattr(EnforcerSettings, k)` so no breakage.
- Level computation is monotone in PnL across all 4 levels: `pnl >= 0 → 0; > caution → 0; > survival → 1; > halted → 2; ≤ halted → 3`. Boundary tests verified (PnL exactly at thresholds maps to the LOWER level — reasonable inclusive-upper semantic). ✓

**Subtle dataclass-placement bug caught + fixed during initial Phase 5B implementation:** `SentimentSettings` was inserted IN THE MIDDLE of `EnforcerSettings` (the blank-line + `@dataclass` boundary terminated the dataclass; all fields below `pnl_halted_pct` became fields of `SentimentSettings`). Fixed in commit `65f6999`. Test suite caught it via `AttributeError: 'EnforcerSettings' object has no attribute 'level_1_max_positions'`.

**Integration:** the existing `clamp_leverage`, `get_max_positions_override`, `get_min_score_override`, `qualify_survival_trade` consumers in `strategy_worker.py:1462-1506` now exercise level 3 paths automatically. The new `ENFORCER_HALTED` event distinguishes from the generic `ENFORCER_LEVEL` transition log so operators can grep for emergency halts specifically.

**Naming:** `pnl_halted_pct` parallels `pnl_caution_pct` and `pnl_survival_pct`. `HALTED_CLAMP` parallels `PRESERVATION_CLAMP` and `SURVIVAL_CLAMP`. `pnl_below_halted` parallels `pnl_below_caution` and `pnl_below_survival`. Naming consistency is exemplary.

**Audit verdict:** clean.

### Phase 2B — convert SURVIVAL RR floor from BLOCK to TP-scale ADJUSTMENT

**Files modified:**
- `src/strategies/performance_enforcer.py:230-269` (new `_scale_tp_for_rr` helper); `:271-360` (new `try_adjust_for_survival_rr` public method).
- `src/workers/strategy_worker.py:1495-1544` (call-site integration: when qualify fires with `rr_X.X_below_3.0`, attempt adjustment; on success update `trade["take_profit_price"]` + log `ENFORCER_RR_ADJUSTED`; on failure log `ENFORCER_RR_ADJUST_FAIL` + fall through to legacy block).

**Architecture fit:**
- `_scale_tp_for_rr` is a pure helper (no I/O, no state mutation). Reused by `try_adjust_for_survival_rr`.
- `try_adjust_for_survival_rr` reads structure cache for the X-RAY's at-flip RR + structural placements; computes a 50% beyond-target ceiling; calls `_scale_tp_for_rr`; returns `(new_tp_or_None, reason, old_rr, new_rr)`.
- Disambiguation: `qualify_survival_trade` returns `rr_X.X_below_Y.Y` strings. `try_adjust_for_survival_rr` returns `rr_scaled_to_floor` (success) OR `rr_scale_*` failure strings. The strategy_worker filter (`startswith("rr_") and not startswith("rr_scale_") and != "halted"`) is correctly disambiguated. ✓
- HALTED (level 3) overrides adjustment — `try_adjust_for_survival_rr` returns `(None, "halted")` regardless of structure data, and the call site falls through to the standard block path.

**Integration:** the legacy `TRADE_SKIP rsn=survival_block` path is preserved for HALTED + non-RR-class qualify reasons (quality, confluence, no_xray_data). On successful adjust, the trade flows through to the rest of the pipeline with the new `take_profit_price`; the `tp` mirror is also updated for back-compat.

**Naming:** `_scale_tp_for_rr` (private helper, snake_case, signature-readable) and `try_adjust_for_survival_rr` (public, "try_" prefix denotes failable adjustment). Consistent with project conventions.

**Audit verdict:** clean.

### Phase 2C — audit other enforcer modes

**File:** `dev_notes/callb_framing_fix/phase2c_audit.md` (no code change).

**Verdict:** intentional no-op. Levels 1 and 2's remaining "block" gates (max_positions, min_score, qualify_survival_trade quality cap, confluence floor) are bounded quality-safety mechanisms — converting them to adjustments isn't meaningful (you can't adjust a setup-quality letter grade). Phase 4 of dir-block-fix (commit `2cb3dc4`) already converted Level 1 leverage from BLOCK to CLAMP. ✓

### Phase 3 — DB lock root-cause audit

**File:** `dev_notes/callb_framing_fix/phase3_pragma_audit.md` (no code change).

**Verdict:** intentional no-op. Phase 0 baseline's "PRAGMA discrepancy" was a CLI tool artifact (each `sqlite3 data/trading.db` invocation opens a new connection that doesn't see the per-connection PRAGMAs the running worker applies). DB_LOCK_WAIT count is currently 0 across log files. Verified via boot-time test that DatabaseManager applies all 9 PRAGMAs correctly via the DB_PRAGMAS log. ✓

### Phase 4 — non-destructive SIG_DOWNGRADE

**File modified:** `src/intelligence/signals/signal_generator.py:179-247`.

**Architecture fit:**
- `signal.components` is a `dict[str, Any]` already used as a JSON-serializable bag of model inputs. Adding 4 new fields (`original_signal_type`, `confidence_floor_failed`, `confidence_below_strong`, `confidence_below_buy`) is purely additive. No DB schema change (the `signals.components` column is a TEXT JSON blob serialized via `json.dumps(signal.components)` at `altdata_repo.py:283`).
- Field types: `str`, `bool`, `bool`, `bool` — all JSON-safe. ✓
- Back-compat: `signal.signal_type` still surfaces the downgraded form. Existing consumers (`scanner_worker._get_signal_confidence`, `claude_strategist`, `alerts/templates.signal_detected`) read only the surface fields they always read; the new fields are opt-in via `signal.components.get(...)`.

**Integration:** the alert template at `alerts/templates.py:50-54` iterates over `signal.components.items()[:5]` for Telegram display. The new keys are appended after `news_count`, `reddit_count` so they're position 7-10; the slice cap (5) skips them. No regression to alert formatting. ✓

**Naming:** `original_signal_type` (matches `signal_type`); `confidence_floor_failed` (clear semantic); `confidence_below_strong` and `confidence_below_buy` (explicit threshold names matching `t_strong` and `t_buy` locals).

**Audit verdict:** clean.

### Phase 5B — sentiment consumption gate

**Files modified:**
- `config.toml:951-961` (new `[sentiment]` section with `consumption_enabled = false`, documented above the key).
- `src/config/settings.py:1448-1466` (new `SentimentSettings` dataclass — placed correctly AFTER `EnforcerSettings` post-fixup `65f6999`); `:2547` (`Settings.sentiment` field); `:2675` (`_build_sentiment` call); `:2771` (Settings instantiation passes `sentiment=sentiment_cfg`); `:3346-3349` (`_build_sentiment` builder function).
- `src/intelligence/signals/signal_generator.py:68-86` (`_sentiment_consumption_enabled` init + `SENT_CONSUMPTION_DISABLED` boot WARNING); `:464-481` (force-deactivate sentiment in `_evaluate_signal` weighted classifier when False).
- `src/intelligence/sentiment/aggregator.py:69-101` (`_consumption_enabled` init + `SENTIMENT_DEGRADED_MODE | reason=consumption_disabled` boot WARNING); `:225-235` (suppress per-coin SENT_DEGRADED_MODE when flag false).
- **`src/mcp/server.py:149`** (DI fix in commit `7632cec`).
- **`src/brain/__init__.py:84`** (DI fix in commit `03106b9`).

**DI wiring audit findings (CRITICAL — caught + fixed during audit):**

The Phase 5B commit `bf2b5a9` initially wired the gate only at `workers/manager.py:164,168` (production trading-workers path). The audit identified two additional `SentimentAggregator` construction sites that bypassed the gate:

1. **`mcp/server.py:145`** — `trading-mcp-sse` service; `SentimentAggregator(db, scorer)` (no settings). Fixed in commit `7632cec` by passing `s` (the local Settings reference).
2. **`src/brain/__init__.py:84`** — deprecated `BrainManager` (legacy `python brain.py --once` path); same pattern. Fixed in commit `03106b9` by passing `self.settings`.

After both fixes, all three SentimentAggregator construction sites in production + deprecated paths pass settings. The gate is honoured uniformly.

**Architecture fit:**
- `SentimentSettings.consumption_enabled = False` default matches the operator decision.
- `SignalGenerator._sentiment_consumption_enabled` defaults to `True` (legacy/test back-compat) and is overridden to `False` only when settings carry the flag. Same pattern in `SentimentAggregator._consumption_enabled`.
- The `_evaluate_signal` gate force-deactivates only the `active['sentiment']` flag in the weighted-sum dict; the score `s_sentiment` is still computed for logging/debug. Surgical, no overreach.
- Per-coin `SENT_DEGRADED_MODE` suppression preserves the once-at-init WARNING for visibility.

**Naming:** `consumption_enabled` (clear, action-shaped name); `_sentiment_consumption_enabled` (private member matching public flag semantics); `SENT_CONSUMPTION_DISABLED` (event name parallels existing `SENT_DEGRADED_MODE`).

**Test coverage:**
- `tests/test_sentiment_consumption_gate.py` — 3 tests covering enabled/disabled gating in `_evaluate_signal` + default value.
- `tests/test_sentiment_degraded_mode.py:86-93` — asserts the wiring at `manager.py` carries `SentimentAggregator(db, scorer, settings)` literal. Still passes after the brain init fix (which adds a different but parallel call site).

**Audit verdict:** clean (after the two follow-up fixes).

---

## 3. Cross-cutting integrity verification

### 3.1 — Module imports

All 9 modified src modules import cleanly (verified via standalone import script):
- `src.brain.strategist`, `src.core.thesis_manager`, `src.config.settings`, `src.database.migrations`, `src.database.connection`, `src.workers.strategy_worker`, `src.strategies.performance_enforcer`, `src.intelligence.signals.signal_generator`, `src.intelligence.sentiment.aggregator`. ✓

### 3.2 — Settings._load_fresh end-to-end

Live `config.toml` parses correctly:
```
enforcer.pnl_caution = -3.0
enforcer.pnl_survival = -12.0
enforcer.pnl_halted = -15.0
enforcer.level_2_min_rr = 3.0
sentiment.consumption_enabled = False
```
✓

### 3.3 — Schema v28 round-trip + idempotency

Verified on a fresh in-memory `DatabaseManager`:
- `run_migrations` upgrades 0 → 28 cleanly.
- All 4 `xray_flip_*` columns present.
- Re-run produces identical schema (idempotent).
✓

### 3.4 — All 8 new observability events grep-able and present

| Event | Source | Status |
|---|---|---|
| `STRAT_CALL_B_REFRAMED` | `strategist.py:461` | ✓ |
| `STRAT_CALL_B_FLIP_NOTICE` | `strategist.py:3268` (xray) + `:3280` (apex) | ✓ |
| `THESIS_FLIP_PERSISTED` | `thesis_manager.py:101` | ✓ |
| `ENFORCER_HALTED` (entry + exit) | `performance_enforcer.py:495,502` | ✓ |
| `ENFORCER_RR_ADJUSTED` | `strategy_worker.py:1523` | ✓ |
| `ENFORCER_RR_ADJUST_FAIL` | `strategy_worker.py:1531` | ✓ |
| `SENT_CONSUMPTION_DISABLED` | `signal_generator.py:83` | ✓ |
| `SENTIMENT_DEGRADED_MODE\|reason=consumption_disabled` | `aggregator.py:95` | ✓ |

### 3.5 — Cross-cutting class instantiation

All affected classes instantiate cleanly with both production-style Settings AND minimal/no-Settings (back-compat for tests + legacy paths). Defaults are correctly threaded through. ✓

### 3.6 — DI wiring per construction site

| Class | Production sites | Status |
|---|---|---|
| `SentimentAggregator` | `workers/manager.py:164` (prod) + `mcp/server.py:149` (prod, fixed) + `brain/__init__.py:84` (deprecated, fixed) | ✓ |
| `SignalGenerator` | `workers/manager.py:168` (prod) + `mcp/server.py:154` (prod) | ✓ |
| `PerformanceEnforcer` | `workers/manager.py:1363` (prod, single site) | ✓ |
| `ThesisManager` | `workers/manager.py:472` (prod, single site) | ✓ |
| `ClaudeStrategist` | `workers/manager.py:567` (prod, single site) | ✓ |

Single-site DI for the trading-critical classes; multi-site for the intelligence-feed classes. All sites pass settings. ✓

### 3.7 — Boundary tests (PnL bands, edge cases)

Verified via standalone smoke check:
- PnL exactly at `caution_pct` (-3.0): level 1 (inclusive-upper).
- PnL exactly at `survival_pct` (-12.0): level 2.
- PnL exactly at `halted_pct` (-15.0): level 3.
- PnL infinitesimally below each threshold: maps to next level.
- `_scale_tp_for_rr` with zero risk: returns `(None, "rr_scale_zero_risk")`.
- `try_adjust_for_survival_rr` with no cache: returns `(None, "rr_no_cache")`.
- `try_adjust_for_survival_rr` at level<2: returns `(None, "rr_not_in_survival")`.
- `clamp_leverage` HALTED with leverage=1: `(1, "HALTED_CLAMP: 1->1...")` — the strategy_worker call site emits the log only when `clamped < requested`, so no spurious `ENFORCER_LEV_CLAMP` log. ✓

### 3.8 — Code quality

- Zero `try/except: pass` band-aids in new code.
- Zero `TODO`/`FIXME`/`XXX`/`HACK` markers in new code.
- Type hints on every new function signature.
- Docstrings on every new public method + class.
- Loguru events use `ctx()` context binding consistently.
- All thresholds config-driven (zero hardcoded magic numbers in gates).
- Schema v28 migrations are additive with `NOT NULL DEFAULT` for backward-compat.
- All new code follows the existing project naming conventions (snake_case private, snake_case public, SCREAMING_CASE constants, CamelCase classes).

---

## 4. Test results

### 4.1 — Per-file pass rates

| File | Tests | Result |
|---|---|---|
| `test_strategist_callb_prompt.py` (Phase 1B/1C/1D + existing TIAS guards) | 11 | PASS |
| `test_thesis_xray_flip.py` (Phase 1E NEW) | 5 | PASS |
| `test_enforcer_clamp.py` (Phase 4 dir-block + Phase 2A NEW) | 7 | PASS |
| `test_enforcer_survival_adjust.py` (Phase 2B NEW) | 7 | PASS |
| `test_signal_non_destructive.py` (Phase 4 NEW) | 3 | PASS |
| `test_sentiment_consumption_gate.py` (Phase 5B NEW) | 3 | PASS |
| `test_sentiment_degraded_mode.py` (Phase 10 + Phase 5B regression) | 7 | PASS |
| `test_thesis_order_id_keying.py` (Phase 8 schema mirror v28-update) | 2 | PASS |
| `test_definitive_pipeline_e2e.py` (Phase 8 + Phase 1E schema mirror v28-update) | 21 | PASS |
| `test_signal_generator_multi_source.py` (existing multi-source) | 13 | PASS |
| `test_sentiment_aggregator_tags.py` (existing tag classifier) | 4 | PASS |
| `test_cleanup_trade_thesis.py` (existing) | 3 | PASS |
| `test_strategist_calla_skip.py` (existing) | 4 | PASS |
| `test_stage2_phase{1,2,3,4}/*.py` (existing) | 111 | PASS |

**Total directly-affected sweep: 201 / 201 PASS**

### 4.2 — Focused regression sweep

`pytest -k "thesis or callb or strategist or stage2 or migration or enforcer or signal or sentiment or definitive"` (excluding pre-existing `test_phase7` import errors per `project_layer4_realignment.md` memory): **299 / 299 PASS** (29.36s).

### 4.3 — Smoke / integration / E2E

- Schema v28 migration round-trip + idempotency: PASS.
- 3-trade scenario (clean / apex-flipped / xray-flipped) end-to-end render: PASS — boot events fire, FLIPPED notices match expected per-trade format.
- Phase 4 non-destructive downgrade end-to-end with mocked deps: PASS.
- Phase 5B gate behaviour (enabled / disabled) end-to-end: PASS.
- Boundary tests across all 5 PnL bands + 4 enforcer levels: PASS.

---

## 5. Findings: gaps caught + fixed during audit

| # | Finding | Phase | Severity | Status |
|---|---|---|---|---|
| 1 | `mcp/server.py:145` constructed `SentimentAggregator(db, scorer)` without settings — Phase 5B gate inactive in trading-mcp-sse path | 5B | Medium (silent log spam in MCP path) | **Fixed in `7632cec`** |
| 2 | `src/brain/__init__.py:84` (legacy `BrainManager`) constructed `SentimentAggregator(self.db, scorer)` without settings | 5B | Low (deprecated path, but operator may run `brain.py --once`) | **Fixed in `03106b9`** |

No outstanding gaps. Both fixes carry verbose commit messages explaining the rationale.

## 6. Final commit chain

```
03106b9 fix(brain/__init__): pass settings to SentimentAggregator in legacy BrainManager init
7632cec fix(mcp/server): pass settings to SentimentAggregator so the consumption gate works in MCP path
0c8d648 docs(callb-framing-fix/6+7): trial monitor queries + verification report skeleton
65f6999 fix(settings/5B): move SentimentSettings out of EnforcerSettings (broken-dataclass fixup)
bf2b5a9 fix(sentiment/5B): gate sentiment consumption behind config flag (disabled by default)
e5978f9 fix(signals/4): preserve original signal alongside downgrade meta + Phase 2C/3 audits
118adcf fix(enforcer/2B): convert SURVIVAL RR floor from block to TP-scale adjustment
1150e10 fix(enforcer/2A): raise SURVIVAL trigger to -12% and add HALTED at -15%
9c2235f feat(thesis/callb-1E): persist XRAY flip metadata in schema v28
50b5356 fix(strategist/callb-1D): add aggressive-exploitation contract to CALL_B
e00c5d5 fix(strategist/callb-1C): drop original-thesis line from CALL_B per-position blocks
f62683c fix(strategist/callb-1B): drop regime-reversal and thesis-broken close rules
```

12 atomic commits on `main`, parent `2ac091d`. No bundling. Each independently revertable.

## 7. Recommendation

The fix is shipped, integrated, wired, and tested across all production paths. Operator can proceed with `sudo systemctl restart trading-workers trading-mcp-sse` to load the new code, then run the 5-7 day trial per `dev_notes/callb_framing_fix/phase6_trial.md`.

**No outstanding items.** Audit closes here.
