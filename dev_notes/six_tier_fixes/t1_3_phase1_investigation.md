# T1-3 Phase 1 — F9 TIAS learning loop dark investigation

## 1. Defect statement

Every `THESIS_CLOSE` log line shows `lesson=''`. 38 of 38 events in today's window (100%). Every closed trade in the `trade_thesis` table has an empty `lesson` column. CALL_A's "LESSONS FROM RECENT TRADES" prompt section renders only the header + per-trade pnl/reason summary, never the lesson body. CALL_B's lesson section was deliberately removed on 2026-05-05.

The system's primary feedback loop — show Claude what worked and what didn't — is functionally dark.

## 2. Plumbing trace (end-to-end)

| Stage | File:line | Behaviour |
|-------|-----------|-----------|
| 1. Close fires | `src/core/trade_coordinator.py:661` `on_trade_closed` | Pops `_trades`, builds record dict, fans out callbacks. |
| 2. Thesis callback | `src/workers/manager.py:1916` `_thesis_close_callback` | Calls `thesis_manager.close_thesis(symbol, close_price, pnl_pct, pnl_usd, close_reason, order_id)`. Does NOT pass `lesson=`. |
| 3. Thesis write | `src/core/thesis_manager.py:174-260` `close_thesis(lesson: str = "")` | Defaults `lesson=""`. Only auto-fills when `close_reason=="transformer_switch"` (line 201). UPDATEs `trade_thesis.lesson = ?` with empty string. Emits `THESIS_CLOSE | sym=... lesson=''`. |
| 4. TIAS analyzer (parallel) | `src/tias/analyzer.py:_map_response:190-211` | Maps DeepSeek JSON to `ds_*` columns (`ds_why`, `ds_what_should_done`, `ds_how_to_exploit`, etc.) on the separate `trade_intelligence` table. **No bridge to `trade_thesis.lesson`.** |
| 5. CALL_A reads | `src/brain/strategist.py:1399-1417` (inside `_build_context_prompt`) | Calls `thesis_mgr.get_recent_lessons(limit=10)`. Renders `## LESSONS FROM RECENT TRADES` header + per-trade summary lines. The `Lesson: <text>` body line is gated by `if l.get("lesson"):` — always False today, so never rendered. |
| 6. CALL_B reads | `src/brain/strategist.py:3540-3584` (inside `_build_position_prompt`) | Lesson injection block was REMOVED on 2026-05-05 (Post-Execution Closure Fix Phase 1A, commit f718686). Regression sentinel `_tias_lessons_removed = True` at line 3553. Hard-coded `recency_lessons_count=0` in the log emission at line 3584. |
| 7. TIAS Phase 2 trigger | `src/workers/manager.py:2400` `_tias_close_callback` (per memory) | Fires DeepSeek analysis in background; writes to `trade_intelligence` table, not `trade_thesis`. |

**Conclusion**: the `lesson` column on `trade_thesis` is functionally orphan. There is no code path that generates a non-empty lesson string from a close. The TIAS analyzer DOES generate rich lesson-like content (`ds_what_should_done`, `ds_how_to_exploit`) but writes to a DIFFERENT table on the `ds_*` columns; the bridge from TIAS output to `trade_thesis.lesson` was never built.

## 3. Why the prior fix REMOVED CALL_B's lesson section

Per memory `project_post_execution_closure_fix.md` (2026-05-05):

> RENDERUSDT Sell was force-closed 3:41 after entry citing "Recent lesson shows RENDERUSDT Sell just lost -0.23% on time_decay with low p_win" (workers.log 2026-05-05 15:07:54). This recency-bias pattern undid the Stage 2 framing fix's gains.

The CLOSED-LOOP failure mode:

1. Trade X closes at -0.23% on time_decay.
2. The narrative gets persisted to `trade_thesis.lesson` (hypothetically; it didn't happen here because the lesson was already empty by 2026-05-05).
3. Next CALL_B reads `get_recent_lessons` → "RENDERUSDT Sell just lost on time_decay" appears in prompt.
4. Brain reasons over the prompt; concludes "another RENDERUSDT Sell at 3min PnL=-0.07% likely follows same pattern" → close.
5. That close becomes the next "lesson". Loop tightens.

The 2026-05-05 fix correctly identified that PER-TRADE NARRATIVE LESSONS create a recency-bias feedback loop. The fix removed CALL_B's lesson section to break the loop on the position-management path.

**But the fix did NOT solve the root issue**: per-trade lessons are inherently closed-loop-prone, and the system still needs a learning channel. The 2026-05-05 fix explicitly flagged CALL_A's lesson injection (strategist.py:1198-1211 / actually now at 1407-1417 in `_build_context_prompt`) as out-of-scope follow-up.

## 4. Today's state — F9 is two distinct issues

### F9a — Generation gap

No code path generates a non-empty `lesson` string for any close. The plumbing is set up (column, parameter, getter) but the actual extraction was never wired. Result: `lesson=''` for every close, today and historically.

### F9b — Injection design

If we wire generation, the recency-bias closed-loop returns unless guarded. Three layers of mitigation are available:

- **Latency**: TIAS DeepSeek calls run in background and may take 30 s to 3 min to land. A natural delay reduces "just lost 3 min ago" leakage.
- **Age gate**: explicit minimum age before injection (e.g. lesson must be ≥ 5 min old).
- **Symbol scope**: lessons from symbol X are not injected into prompt cycles that contain symbol X (prevents auto-suggesting an action on the SAME symbol the lesson is about).
- **Aggregation**: switch from per-trade narrative ("RENDERUSDT lost X") to per-class statistic ("ranging-regime trailing_sl wins 65 %, time_decay loses 50 %") — closed-loop-immune by construction.

## 5. F9 is two-layer regression

Today's lesson='' is a SAFE-BY-COINCIDENCE state. The plumbing is half-built; the feedback channel is silent; no closed-loop risk exists because no input is fed. Fixing F9 means choosing between:

- Restoring the channel WITH guards (riskier; closed-loop is the proven failure mode).
- Switching the channel to a different abstraction (aggregated stats; safer; loses some signal richness).
- Confirming F9 as accepted current state (lesson='' is a feature, not a bug) and removing the misleading log field.

This is a design decision the operator must make. T1-3 cannot proceed to implementation without it.

## 6. Investigation conclusions

1. F9 root cause: no code generates a `lesson` string. Plumbing is set; values are never produced.
2. The natural source for lesson content (TIAS DeepSeek) writes to a DIFFERENT table on `ds_*` columns. A bridge from `ds_what_should_done` / `ds_how_to_exploit` to `trade_thesis.lesson` would fix F9a.
3. F9b (injection design) is the harder problem. The 2026-05-05 closed-loop incident proved that uncritical per-trade narrative lessons CREATE a closed-loop. Any fix that re-enables per-trade injection must add safeguards.
4. Three classes of safeguard (age gate, symbol scope, aggregation) can be combined. The operator needs to choose the combination.
5. The simplest aim-preserving design: bridge TIAS → lesson with age gate (>= 5 min) and same-symbol exclusion. Keep CALL_B lesson injection disabled per prior fix; re-enable CALL_A's injection (already plumbed at line 1415).

Phase 2 proposal follows.
