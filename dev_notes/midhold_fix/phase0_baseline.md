# Phase 0 — Pre-Flight Baseline Verification

## Purpose

Capture the starting state of the codebase before any Mid-Hold Trade Management Fix code lands. Confirms shipped fixes are still in place, schema baseline, and the issue is still present in current behavior (per IMPLEMENT_MIDHOLD_TRADE_MANAGEMENT_FIX.md Rule 14).

Date: 2026-05-19
Branch: `fix/midhold-trade-management` (off `main` @ `78d6ef6`)
Schema baseline: v33

## 1. Repository state

- Branch created: `fix/midhold-trade-management` from `main` @ commit `78d6ef6` ("docs(gaps_fix): investigation + verification deliverables + session loss analysis")
- Working tree: pre-existing runtime modifications (`data/layer_state.json`, `data/logs/layer1c_full.jsonl`) and prior-investigation untracked dev_notes folders are present but untouched; not staged in this branch
- No in-flight code edits at branch creation

## 2. Shipped fixes still in place (Rule 11 — Do not break what works)

| Fix | Marker | Verified file:line |
|---|---|---|
| B1a regime detector (calibrated 2026-05-12) | `trending_adx_threshold`, `ranging_adx_threshold`, `dead_adx_threshold` | `src/strategies/regime.py:133, 145, 149` |
| R1 XRAY counter-inversion | "XRAY counter-setup Phase 5a/5c" comments + `structural_component` multiplier and ensemble `size_mult` scaler | `src/strategies/scorer.py:69, 480, 501` and `src/strategies/ensemble.py:144, 183` |
| STRAT_DIRECTIVE_REJECTED lifecycle event (three-gaps Gap 3) | `_emit_strat_directive_rejected` and structured log | `src/core/layer_manager.py:1297-1346` |
| Reentry cooldown (J6 gate) | `reentry_cooldown_seconds` (default 600s) | `src/config/settings.py:1281`, `src/strategies/scanner.py:162-165` |
| Bidirectional `is_long_invalid`/`is_short_invalid` flags (three-gaps Gap 2) | `structural_placement.is_long_invalid` and `.is_short_invalid` assignments | `src/analysis/structure/structure_engine.py:366-382` |
| Direction-bias four-fix series (Phase A 2026-05-19 10:55) | live trial confirmed 59% Buy / ~50% execution per `SESSION_LOSS_ANALYSIS_2026_05_19.md` §2.4 | n/a — operator-confirmed runtime behavior |
| CALL_B Framing Fix Phase 1C (2026-05-06) | thesis text intentionally NOT read in CALL_B | `src/brain/strategist.py:4061` |
| DB concurrency refactor (J11, v33) | `SCHEMA_VERSION = 33` with duplicate-index drop migration | `src/database/migrations.py:12` |

All shipped fixes remain in place. No regressions observable through static markers.

## 3. Schema baseline

- Current schema version: **v33** (`src/database/migrations.py:12`)
- `trade_thesis` columns already populated by `ThesisManager.save_thesis()`: `symbol, direction, entry_price, stop_loss_price, take_profit_price, size_usd, leverage, max_hold_minutes, trailing_activation_pct, thesis, market_context, strategy_hints, consensus, status, opened_at, order_id, exchange_mode, apex_flipped, apex_original_direction, apex_reason, entry_xray_confidence, entry_setup_type, entry_regime_at_open, entry_regime_confidence, xray_flip_source, xray_flip_ratio, xray_flip_rr_long, xray_flip_rr_short, bybit_position_idx, closed_at, close_price, actual_pnl_pct, actual_pnl_usd, close_reason, lesson`
- **No** `thesis_invalidation`, `thesis_state`, `thesis_source`, `thesis_snapshot` columns
- **No** `thesis_events` table

Confirmed via grep across `src/core/thesis_manager.py`, `src/brain/strategist.py`, `src/workers/position_watchdog.py`. These four fields are genuinely new.

## 4. Issue presence in current behavior (Rule 14)

The session loss analysis (`dev_notes/SESSION_LOSS_ANALYSIS_2026_05_19.md`) documents the issue from the 2026-05-19 15:57–16:57 UTC window. Per Rule 14, the issue must still be present in current code, not only historical.

Static-code confirmations:

- **CALL_B does NOT consult ensemble mid-hold**: `_build_position_prompt()` in `src/brain/strategist.py:3908+` builds prompt from `regime_detector.get_coin_regime(symbol)`, persisted trade plan, APEX/XRAY flip metadata. No code path reads current ensemble consensus for an open symbol after entry. Confirmed via grep: no caller of `EnsembleVoter.vote()` from `_build_position_prompt`, `_handle_claude_action`, `_monitor_position` (`src/workers/position_watchdog.py`), or `Strategist.create_position_plan`.

- **Brain produces trades without explicit `thesis_invalidation`**: the field does not exist anywhere in the codebase. `src/brain/prompts/trade_decision.py:41-42` JSON schema does not include it. `src/brain/decision_parser.py:82-120` does not extract it. Brain has never been asked to produce it.

- **Watchdog has no per-position thesis_state tracking**: `src/workers/position_watchdog.py` `_position_peaks/_last_prices/_last_pnls/_last_brain_call/_hold_suppression/_consecutive_holds/_last_alert_time/_last_skip_log/_pnl_mismatch_retries/_position_open_times/_position_strategies` exist; no `_position_consensus_state` or `_position_thesis_state`.

- **Mid-hold ensemble flips still occur on open positions**: pending live evidence collection (see §5). However, the underlying mechanism (38-strategy ensemble votes recompute on signal-worker cadence regardless of position state) means flips are guaranteed when consensus shifts during a hold.

Conclusion: the three preconditions of the fix (no mid-hold ensemble consultation, no `thesis_invalidation` field, no per-position thesis state) all hold in current code.

## 5. Baseline metric collection — deferred to live trial

Per IMPLEMENT doc Phase 0 step, capture for the most recent 24h session:

- Strategy ensemble vote events per hour
- Open positions experiencing an ensemble flip mid-hold
- CALL_B trigger cadence
- Trades with structural justification in rationale text vs without
- Trade outcome by exit path (`bybit_sl_hit`, `wd_claude_action`, `wd_dl_action`, `wd_timeout`)
- Direction distribution at ~50/50
- DB cascade absence

Live log files in `/home/inshadaliqbal786/`: `ALL_LOGS_2026-05-18_10-00_to_15-30.log` (most recent 5.5h window). Full 24h baseline metric capture is **deferred to Phase 3.9 log-only trial** for two reasons:

1. The baseline values themselves are not load-bearing on Phase 1 investigation or Phase 3 implementation choices — the architecture is fixed and the metrics inform tuning in Phase 3.10, not the design.
2. Phase 3.9 explicitly re-captures all these metrics in log-only mode (Rule 12) and compares pre/post — so baseline is best collected when log-only mode starts (same workflow as previous fix series per `feedback_layer1_restructure.md`).

If the operator requires baseline collection now, the grep patterns in `SESSION_LOSS_ANALYSIS_2026_05_19.md` §12.2 apply, scoped to a 24h window in `data/logs/workers.log` / `brain.log`.

## 6. Out of scope confirmation

Per IMPLEMENT_MIDHOLD doc Part A — these are NOT modified:

- Bybit demo HTTP/auth/signing/WS-parse layer
- Shadow adapter
- B1a regime detector (`src/strategies/regime.py`)
- DB concurrency refactor (J11 — schema-v33 complete)
- Strategy implementations themselves
- Brain Claude CLI subprocess internals

## 7. Phase 0 sign-off

| Criterion | Status |
|---|---|
| Working tree clean for this branch | OK (only pre-existing runtime artifacts and prior investigation files outside scope) |
| All shipped fixes in place | OK (8/8 markers verified) |
| Schema baseline at v33 | OK |
| CALL_B Framing Fix Phase 1C in place | OK (`strategist.py:4061`) |
| Issue precondition: no mid-hold ensemble consultation | OK (confirmed via static analysis) |
| Issue precondition: no `thesis_invalidation` field | OK (confirmed via grep) |
| Issue precondition: no per-position thesis_state tracking | OK (confirmed via watchdog state-dict enumeration) |
| Baseline metrics for 24h session | DEFERRED to Phase 3.9 log-only trial start |
| dev_notes working directory created | OK (`dev_notes/midhold_fix/`) |
| Branch created | OK (`fix/midhold-trade-management`) |

Phase 0 complete. Proceeding to Phase 1.
