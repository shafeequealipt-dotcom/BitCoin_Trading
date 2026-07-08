# Phase 0 — Definitive Calibration & Wiring Fix: Forensic-vs-Code Verification

**Date:** 2026-04-28
**Source prompt:** `IMPLEMENT_LAYER1_DEFINITIVE_CALIBRATION_AND_WIRING_FIX_PROFESSIONAL.md`
**Forensic data:** `dev_notes/forensic_data_layer1_to_stage2/COMPLETE_FORENSIC_COLLECTION.md`
(NOTE — source prompt references this as `dev_notes/COMPLETE_FORENSIC_COLLECTION.md`; the
actual file lives one directory deeper.)

## Method

For each of the twelve issues, the implicated source file was read end-to-end (CLAUDE.md
"Analyse Before Touching Anything" rule), the forensic claim was checked against the live
code, and the current measured values (config keys, default literals) were captured with
file:line references. No code is changed in Phase 0. The result is the per-issue table
below + the fix scope each downstream phase needs.

## Per-issue verification table

| # | Issue | Status | Evidence (file:line, current value) |
|---|---|---|---|
| 1 | StructureWorker batch=2 ↔ StructureCache TTL=300 mismatch | **STILL ACTIVE** | `config.toml:925` `batch_size = 25`; `src/analysis/structure/structure_cache.py:15` `DEFAULT_TTL = 300.0`; `src/workers/structure_worker.py:82` `self._batch_size = settings.structure.batch_size`. Universe size 50 → two batches per sweep → ~10 min per coin > 5 min TTL → half the universe always stale. Forensic claim confirmed. |
| 2 | Setup classifier `mtf_score >= 0.70` too strict | **STILL ACTIVE** | `config.toml:931` `fvg_ob_min_confluence = 0.7`; `src/analysis/structure/structure_engine.py:701` `fvg_ob_min = getattr(cfg, "fvg_ob_min_confluence", 0.7)`. Forensic XRAY_NONE_REASON examples (mtf=0.40, mtf=0.60) all rejected against 0.70 confirmed. |
| 3 | `_bull_alignment` strict to uptrend, `_bear_alignment` strict to downtrend | **STILL ACTIVE** | `src/analysis/structure/structure_engine.py:727-731`: `def _bull_alignment(): return direction == "long" and struct in ("uptrend",)`; mirror for bear. No `ranging` branch. Combined with `STRAT_REGIME_DIST up=0` from forensic = guaranteed zero BULLISH_FVG_OB. |
| 4 | Scanner `min_rr_ratio = 2.0` hard filter; RR not in composite; reads single `rr_ratio` | **STILL ACTIVE** | `config.toml:400` `min_rr_ratio = 2.0`. Hard gate at `src/workers/scanner_worker.py:591-593` (`if rr < cfg.min_rr_ratio: ... return False`). Direction-blind read at `scanner_worker.py:587-588` (`sp.rr_ratio` only — `rr_long`/`rr_short` unused). Composite has 5 components (structure, strategy, signal, regime, funding) — see `_compute_opportunity_score` `scanner_worker.py:174-215`; RR is NOT a component. `StructuralPlacement.rr_long` / `rr_short` exist at `src/analysis/structure/models/structure_types.py:112-113`. |
| 5 | SignalWorker producing 100 % NEUTRAL | **PARTIAL** | The pre-fix sentiment-hard-gate path is gone (Phase 1 output-quality fix). `src/intelligence/signals/signal_generator.py:407-489` `_evaluate_signal` already implements multi-source weighted classification with active-component renormalisation; INACTIVE components are dropped, not zero-weighted, so a coin classified by F&G + funding alone will reach BUY/SELL if `direction_score >= buy_threshold = 0.25` (`config.toml:1059`). Confidence gate at `signal_generator.py:158-188` enforces `t_buy=0.40` / `t_strong=0.60` (`src/intelligence/signals/signal_models.py:44-50`). Persistent 100 % NEUTRAL therefore has TWO live root causes that need calibration, not architecture: (a) `direction_score` does not cross 0.25 because few components active, or (b) post-classification confidence < 0.40 → demoted to NEUTRAL. Phase 5 will instrument input availability and tune accordingly. |
| 6 | Brain auto-execute fires on incomplete cold-start packages | **STILL ACTIVE** | `src/core/layer_manager.py:631-770` `_run_brain_cycle` calls `strategist.create_trade_plan()` then `_execute_trades_background(plan)` with NO completeness inspection. The validator (`src/core/coin_package_validator.py:178`) only blocks below `fail_below = 0.50` (`config.toml:1073`); packages at completeness 0.50-0.85 still flow. No `BRAIN_COLD_START_BLOCK` or equivalent exists. |
| 7 | REGIME_FALLBACK fires for APEX/TIAS | **PARTIAL** | Service injection IS consistent: APEX `src/apex/gate.py:368` and `src/apex/assembler.py:586` and TIAS `src/tias/collector.py:280` all read `regime_detector` from the registry. Scanner reads `regime_worker` (`src/workers/scanner_worker.py:134`). Both keys are wired to the SAME `RegimeDetector` instance in `src/workers/manager.py:1132-1140`. Cause is therefore COLD-START race: `RegimeDetector._per_coin_regimes` (`src/strategies/regime.py:40`) is empty until `RegimeWorker.tick()` runs the first time. The DB-backed `REGIME_RESTORE` (`src/workers/regime_worker.py:69-124`) repopulates from `coin_regime_history` but only INSIDE the first tick — APEX/TIAS calls landing before that tick fall through to global. Phase 7 will warm the cache synchronously at startup. |
| 8 | Thesis keyed by symbol, not (symbol, trade_id) | **STILL ACTIVE** | `src/core/thesis_manager.py:117` `WHERE symbol = ? AND status = 'open'` — no order_id/trade_id filter. The DB schema already has `order_id` (`thesis_manager.py:54`); the bug is the WHERE clause + that the close callback (`src/workers/manager.py:1504-1511`) doesn't forward `order_id`. The close-record dict at `src/core/trade_coordinator.py:467` already carries `trade_id` — the wiring just stops short. Forensic S5 (close-Buy → open-Sell same symbol → wrong thesis applied) confirmed. |
| 9 | APEX flip discipline | **PARTIAL** | `_check_direction_lock` (`src/apex/optimizer.py:626-664`) DOES lock direction in trending regimes (`return True` for trending_up/down) and locks volatile-without-evidence. But ranging / dead / unknown → `return False, ""` (lines 663-664) → flip allowed unconditionally. No confidence gate; no flip-AND-resize coupling rule. Forensic S3 (APEX_FLIP Sell→Buy with no rolling check) consistent with this gap. |
| 10 | Profit-sniper M4 ladder re-fires without fresh stall | **PARTIAL** | `_stall_escape_action` (`src/workers/profit_sniper.py:2232-2342`) HAS `stall_escape_cooldown_seconds=30` cooldown (line 2336-2337), `stall_tighten_max_applications=3` cap (line 2320-2322), and `stall_recovery_threshold_pct=0.15` recovery delta (line 2315-2319). It does NOT have a per-position lifetime cap on partial closes — after the cooldown elapses, a fresh partial fires every cycle the position remains stalled. Forensic S6 (5 ladder steps in 1:49-1:54) consistent with rapid cooldown-then-re-fire. |
| 11 | D-3 lock contention residual | **VERIFY-ONLY** | Phase 11 of source prompt explicitly defines this as a verification step. Forensic F2 SPECIAL: historic 137-second waits (Apr 26) but zero in current capture. No code change unless live `DB_LOCK_WAIT > 5000ms` events found during 3-hour trial. |
| 12 | Ensemble voter `votes=38 score=1.00` collapse + AAVEUSDT flap | **INVESTIGATE** | `src/strategies/ensemble.py:90-110` thresholds: STRONG requires `agreeing >= 4.0 AND opposing <= 1.5`; GOOD reads `cfg.min_ensemble_agreement` and `cfg.max_ensemble_opposition`; size_mult from `CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, ...}`. The `score=1.00` artifact is the size_mult, not the score — `vote_batch` sorts by `total_score * size_multiplier`. `STRAT_CONSENSUS_CHANGE` already emitted at `src/workers/strategy_worker.py:756` so transitions are observable, but per-strategy vote breakdown is not logged. Phase 12 needs `STRAT_VOTE_TRACE` per-coin per-cycle telemetry first, then targeted fix (single-strategy cap or 2-cycle hysteresis). |

## Files read end-to-end during Phase 0

- `src/workers/structure_worker.py` (369 lines)
- `src/analysis/structure/structure_cache.py` (129 lines)
- `src/analysis/structure/structure_engine.py` — focus on `classify_setup`, `_bull_alignment`,
  `_bear_alignment`, `diagnose_none` (lines 676–960)
- `src/workers/scanner_worker.py` (1090 lines)
- `src/workers/signal_worker.py` (178 lines)
- `src/intelligence/signals/signal_generator.py` (491 lines)
- `src/intelligence/signals/signal_models.py` — CONFIDENCE_THRESHOLDS
- `src/core/coin_package_validator.py` (193 lines)
- `src/core/layer_manager.py` — focus on `_brain_review_loop`, `_run_brain_cycle`,
  `_execute_trades_background` (lines 600–870)
- `src/workers/regime_worker.py` (313 lines)
- `src/strategies/regime.py` — `RegimeDetector` lines 1–90
- `src/core/thesis_manager.py` (232 lines)
- `src/core/trade_coordinator.py` — `TradeState` and `on_trade_closed` (lines 1–120, 380–470)
- `src/apex/optimizer.py` — `optimize`, `_check_direction_lock`, `_log_optimization`
  (lines 60–290, 540–664)
- `src/workers/profit_sniper.py` — M5 dispatch + `_stall_escape_action` (lines 485–610, 2232–2342)
- `src/strategies/ensemble.py` (162 lines)
- `src/workers/manager.py` — service registry section (lines 50–270, 1132–1180, 1488–1520)
- `config.toml` — relevant sections: brain (150-198), scanner (380-407),
  analysis.structure (894-936), signal_generator.multi_source (1049-1062),
  coin_package_validator (1072-1075), mode4 (708-805)

## Forensic data fidelity check

The forensic data was captured 2026-04-27 22:05–23:20 UTC. Code reads done on 2026-04-28
(this morning). Recent commits since then (`git log --oneline -20`) are observability-only
(`phase8`–`phase15` of the obs work plus `e2e` runtime verification) — no behaviour change
that would invalidate the forensic claims. Manual file reads confirmed the literal values
the forensic data flagged.

## Phase plan summary (per approved plan in `~/.claude/plans/happy-growing-stardust.md`)

| Phase | Status from this verification | Action scope |
|---|---|---|
| 1 | STILL ACTIVE | Approach A — `batch_size 25 → 50`; keep TTL=300; observability extension. |
| 2 | STILL ACTIVE | `fvg_ob_min_confluence 0.7 → 0.5`; add MTF distribution log. |
| 3 | STILL ACTIVE | Broaden `_bull/_bear_alignment` to ranging when `mtf >= 0.55`. |
| 4 | STILL ACTIVE | `min_rr_ratio 2.0 → 1.3`; direction-aware RR; add to composite. |
| 5 | PARTIAL | Calibration only — `buy_threshold 0.25 → 0.18`; relax `*_min_active` if input distribution warrants. Add `SIG_INPUT_AVAILABILITY` aggregate. |
| 6 | STILL ACTIVE | New brain cold-start gate + telegram alert. |
| 7 | PARTIAL | Synchronous regime warmup tick; `is_ready()`; `REGIME_CACHE_QUERY` log. |
| 8 | STILL ACTIVE | `close_thesis(order_id=...)` + propagate through coordinator. |
| 9 | PARTIAL | Confidence gate (≥0.90) for ranging/dead/unknown; flip+resize coupling rule. |
| 10 | PARTIAL | Per-position partial-emit counter; `max_partials_per_position = 1`. |
| 11 | VERIFY-ONLY | 3-hour DB_LOCK_WAIT capture + report only. |
| 12 | INVESTIGATE | Add `STRAT_VOTE_TRACE`; iterate to single-strategy cap or hysteresis. |

## Operator decisions taken before implementation (recorded for traceability)

1. **Phase 1 — Approach A (full sweep per tick)** chosen over Approach B (TTL=600s).
   Reason: 50-coin tick at ~1.2 s is well inside the 5-min sweet-spot window; downstream
   readers see strictly fresh data with no staleness window baked in.
2. **Phase 5 — calibration only**, no `BUY_LEAN`/`SELL_LEAN` SignalType variants.
   Reason: introducing new enum members would ripple through scanner composite,
   brain prompt, telegram cards, /health, and alert templates — meaningful coordination
   risk for marginal benefit. Threshold calibration achieves the same operator outcome
   (mixed BUY/SELL/NEUTRAL distribution).

Both decisions captured by AskUserQuestion during the planning step (2026-04-28).
