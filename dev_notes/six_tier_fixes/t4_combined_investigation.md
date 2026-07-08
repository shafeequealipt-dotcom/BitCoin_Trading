# Tier 4 — Observability and latency (combined investigation + proposals)

Three issues, all observability-focused. Two ship code changes; one is investigation-only.

## T4-1 (F1 — Claude CLI subprocess 60-120 s stall)

**Status**: Investigation-only per the plan.

**Defect**: Every Stage-2 brain call stalls 60-120 s before any stdout. CALL_A 150-211 s, CALL_B 27-83 s. Two CALL_Bs in the report's window did NOT stall (26-28 s with small prompts 2.6-3.3 KB).

**Investigation findings**:

1. `src/brain/claude_code_client.py` invokes the Claude CLI subprocess. CLAUDE_PROC_STALL_60S / _120S log lines fire on `state=S wchan=ep_poll, stdout_so_far=0`.
2. The non-stalled CALL_Bs (call_id=12 followed call_id=11 by ~5 min) had small prompts AND no STRAT_AGGRESSIVE_FRAMING. Hypothesis: warm session cache + small prompt = fast path.
3. The stall is a Claude CLI internal — the project does NOT control what happens between subprocess invocation and first stdout. Mitigations require either:
   - Switching CALL_B to a direct API path (large refactor; out of scope per prompt).
   - Maintaining a warm-pool of CLI subprocesses (architectural change).
   - Reducing prompt size for CALL_A (covered by separate Stage 2 prompt-richness work, project memory `project_stage2_richness_status.md`).

**Conclusion**: T4-1 has no actionable code change without significant out-of-scope refactor. Documented as "expected per current architecture" with recommendation to revisit during a future Stage 2 latency-focused engagement. The existing CLAUDE_PROC_STALL_60S / _120S log tags ARE the observability — they let operators see the stalls. No additional code needed.

## T4-2 (Phase5 F-12 — silent SL-update success)

**Status**: Shipped.

**Defect**: 67 `SL_PROPAGATED` events in the report's window vs **0** `BYBIT_DEMO_SET_SL_*` confirmation logs.

**Fix**: Added `BYBIT_DEMO_SET_SL_OK` log line at INFO at `adapter.py:780` (the success return path) and mirror `BYBIT_DEMO_SET_TP_OK` at `adapter.py:847`. Post-fix every successful SL/TP change emits a confirmation log line operators can grep to confirm "the wire change actually landed at Bybit, not just locally."

## T4-3 (Phase5 F-19 — hidden 20-second post-place latency)

**Status**: Observability shipped; root-cause fix deferred pending evidence.

**Defect**: 20-second gap between `BYBIT_DEMO_PERSIST_OK` (adapter returned) and `STRAT_EXEC` (strategy_worker logged execution complete) for MONUSDT at 14:00. Subsequent trade in the same directive (CRVUSDT) did not ORDER_RECEIVED until 14:00:33.215 — 20.6 s after MONUSDT's order. Layer_manager's `_execute_new_trades` is serial within a directive, so the post-place block of trade N blocks trade N+1.

**Investigation**: the post-place block in `_execute_claude_trade` (after `order_svc.place_order`) contains:

1. `coordinator.register_trade(...)` — should be sync/fast.
2. `coordinator.register_trade_plan(...)` — should be sync/fast.
3. `thesis_mgr.save_thesis(...)` — async DB write.
4. `record_strategy_trade(...)` — async DB write.
5. `alert_manager.send_custom(...)` — async Telegram.

None of these should individually take 20 s. The CAPITAL_TIER refresh at 14:00:32.416 (just before STRAT_EXEC) suggests a separate worker tick is interleaving — possibly the fund_manager_worker on its own cadence. Without per-step timing breadcrumbs the actual bottleneck cannot be confirmed.

**Fix (observability)**: add timing breadcrumbs around each major await in the post-place block. Operators can then grep for `POST_PLACE_TIMING` events to see exactly where the 20 s goes. After 24-48 h of evidence, the operator can decide whether to wrap the actual culprit in `asyncio.create_task` (decouples from the strategy_worker tick).

**Fix (production)**: deferred until the bottleneck is identified from the new breadcrumbs.
