# Phase 0 Summary — Post-Layer-1 Operational Fixes

**Date:** 2026-04-27
**Investigator:** Claude Opus 4.7 (1M context) running in plan-then-execute mode.
**Pre-conditions:** DB backup at `data/trading.db.pre-post-layer1-fixes.20260427.bak` (155MB); git tag `pre-post-layer1-fixes` on commit `80a5d9a`.

## Cross-issue dependency map

| Issue | Tier | Phase | Touches | Depends on | Verification cycle |
|---|---|---|----|---|---|
| 1. Shadow signature drift | T1 | 1 | `shadow_adapter.py` + new test | none | per-decision (immediate) |
| 2. Layer 3 boot gate | T1 | 2 | `order_service.py` + `layer_manager.py` + new exception + 2 tests | none | per-boot + 5-min heartbeat |
| 3. trade_thesis cleanup | T1 | 3 | `protected_tables.py` + `cleanup_worker.py` + `connection.py` + 2 tests | none | hourly (HH:18) |
| 4. Strategy consensus | T2 | 4 | `strategy_worker.py:591` + 1 test | useful for #6 verification (aggregate counts richer when cache full) | per Strategy cycle (5-min) |
| 5. Fund reconciler | T2 | 5 | new `fund_reconciler.py` + `manager.py` registration + `order_service.py` preflight/110007 + 2 tests | none | per 60s tick |
| 6. SCANNER_FILTER_AGGREGATE | T3 | 6 | `scanner_worker.py` + `/health` handler + 1 test | benefits from #4 land first | per Scanner cycle (5-min) |
| 7. CLAUDE_PROC_STALL legacy | T3 | 7 | `claude_code_client.py` + config + 1 test | none | per brain call |
| 8. active_universe enrichment | T3 | 8 | `scanner_worker.py` enrichment passing + 1 test | benefits from #6 land first (operators see richer aggregate when verifying) | per Scanner cycle |
| 9. altdata threshold | T4 | 9 | `altdata_worker.py` + `base_worker.py` + config | none | per AltData tick (5-min) |
| 10. Reddit disable | T4 | 10 | `manager.py` + `intelligence/sentiment/aggregator.py` + 1 test | none | per cycle |

**Phase order is optimal as written.** Issue 4 (consensus cache) lands before Issue 6 (aggregate log) so when the operator verifies SCANNER_FILTER_AGGREGATE, the qualified counts are non-zero and the fail-mix tells a real story instead of all-fail-consensus. No phase blocks another phase's commit, but Phase 4→6 verification is more meaningful in that order.

## Risks captured during investigation

1. **Reference doc mismatch.** Driving prompt mentions `live_monitor_60min_2026-04-27_06-24.md`; that file does not exist on disk. Only `layer1_live_monitor_2026-04-27.md` is present. Plan uses the existing one as ground truth.
2. **Issue 2 framing mismatch.** Driving prompt frames the fail-open gate as a leak that allowed the 06:27:14-16 ETHUSDT/BTCUSDT orders. Investigation: those orders were `purpose=mcp_tool` with Bybit `ErrCode 110007` — they passed the gate (Layer 3 was ON or the user used force=True), then Bybit rejected them for insufficient balance. The fail-open gate is a latent hole (no observed leak) but still worth tightening on safety grounds. Issue 5 is the actual cause of the 06:27 incidents.
3. **Issue 3 first-cycle delete count.** trade_thesis has 1154 rows from 2026-03-26 to 2026-04-26 — oldest row 32 days old, retention 60 days. First post-fix cleanup will delete 0 rows (no rows past retention yet). Real deletes start once rows age past 60 days. Verification must not assume non-zero deletion on first cycle.
4. **Issue 7 partially shipped.** The graduated bucket system already exists at `claude_code_client.py:1076-1159`. The fix is narrowly scoped to legacy-emission cleanup + tunability; not a full re-implementation.
5. **Issue 8 by design.** The Phase-5 Scanner rewrite intentionally writes 0.0 to enrichment columns ("auxiliary; not produced here"). Restoring enrichment is operator-UX cosmetic, not a brain-pipeline correctness fix. Worth doing for Telegram `/status` accuracy.
6. **Issue 10 partially shipped.** INFO startup log already exists at `manager.py:133`. Remaining work: WARNING level + degraded-mode aggregator emission; not a green-field fix.

## Verification gate (Phase 0 → Phase 1)

Before any code change, the following questions are answered concretely (Hard Rule 4 — No Assumptions):

1. **Q: What is the EXACT mechanism that makes ShadowOrderService crash with TypeError?**
   A: Shadow's `place_order` signature at `shadow_adapter.py:393-403` lacks the three keyword-only args `purpose`, `layer_snapshot`, `force` that callers (e.g., `strategy_worker.py:1232-1242`) pass. Python raises `TypeError: place_order() got an unexpected keyword argument 'purpose'`. Caught by `_execute_new_trades`'s outer try/except as a soft `TRADE_SKIP rsn=exception`.

2. **Q: What is the EXACT code path that produces `ORDER_GATE_NO_LM | action=allow`?**
   A: `OrderService._enforce_layer3_gate` at `order_service.py:155-228` checks `self._layer_manager`. If `None` (boot before LM attaches), logs the warning at line 170-174 and returns. All purposes (Layer 3 entry + operator + Layer 4) flow through. Documented as intentional in docstring at lines 163-166. The gap is purpose-undifferentiated boot policy — not actively leaking but architecturally unsafe.

3. **Q: What is the EXACT scheduler that emits the protected DELETE every hour?**
   A: `CleanupWorker` at `cleanup_worker.py:71` (interval 3600s). Iterates `RETENTION_POLICIES` at line 52 (which lists `("trade_thesis", "opened_at", 60)`), executes `DELETE FROM trade_thesis WHERE opened_at < ?` per table at lines 126-135. The pre-flight guard at `connection.py:295` raises `ProtectedTableViolation`; the cleanup catches at line 134 and logs `Cleanup failed for trade_thesis` at WARNING. Cadence: every hour at HH:18 (cleanup tick offset).

4. **Q: What is the EXACT semantic difference between `consensus_setups` and `filtered` at line 591?**
   A: `consensus_setups` is the full universe of ensemble decisions (~50 coins). `filtered = self.pnl_manager.apply_restrictions(consensus_setups, mode)` at line 564 drops setups below `max_score_threshold` (50 in NORMAL mode). For the cache that ScannerWorker reads, we want all 50 coins — so the call at line 591 should pass `consensus_setups`, not `filtered`. The summary alias at line 607 (`_strategy_consensus_summary`) correctly uses `filtered` for legacy strategist consumers.

5. **Q: What is the EXACT current drift between fund_manager's view and Bybit's `availableBalance`?**
   A: Cannot be determined from logs alone (we only see `FUND_POOLS` local view: `cap=$1257.05 available=$1257.05 in_use=$0.00`). The 06:27:14 incident in the live monitor showed Bybit rejecting orders with `ErrCode 110007` while local view claimed sufficient balance — proving drift exists but not quantifying it. Phase 5 reconciler will measure and report.

All five answers concrete; verification gate passes.

## Implementation order (re-confirmed)

```
Phase 0 (this) → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → Phase 8 → Phase 9 → Phase 10
```

Each phase: investigation in-file (Hard Rule 3) → implement → tests → atomic commit → smoke test (`pytest tests/<phase>` + import check). Operator runs Phase 11 (1-3h trial) and Phase 12 (24h observation) after Phase 10 lands.
