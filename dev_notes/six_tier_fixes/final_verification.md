# Final Verification — Six-Tier Fixes Engagement Complete

## 1. Engagement summary

22 chronic defects across 6 severity tiers from `IMPLEMENT_SIX_TIER_FIXES_2026-05-11.md` are now investigated, root-cause-fixed (or documented-as-deferred where appropriate), and committed.

All commits land on branch `fix/five-critical-fixes-2026-05-11` per operator decision in plan mode.

## 2. Commit ledger

37 atomic commits + 1 docs/plan commit. Per-tier breakdown:

```
Tier 0 baseline                                       1 commit  (initial docs)
Tier 1 (T1-1 F18, T1-2 F8, T1-3 F9, T1-4 F4)          18 commits + 4 dev_notes + tests + migration script
Tier 2 (T2-1 F20, T2-2 F14, T2-3 F11)                 14 commits + dev_notes + tests
Tier 3 (T3-1 F-4, T3-2 F-8, T3-3 F-15, T3-4 F-20)     2 commits + dev_notes + tests
Tier 4 (T4-1 F1, T4-2 F-12, T4-3 F-19)                1 commit  + dev_notes
Tier 5 (T5-1..T5-5 F2/F3/F5/F19/F-1)                  1 commit  + dev_notes
Tier 6 (T6-1..T6-8 F-2/F6/F12/F7/F13/F10/F22/F-21)    1 commit  + dev_notes
```

Run `git log --oneline 11cc1a7..HEAD` for the full chain.

## 3. Test coverage

62 new dedicated smoke tests pass + 56 prior-suite neighbor tests = 118 total passing. Zero regressions.

```
tests/test_t1_1_phantom_close_guard.py      11 tests  T1-1 F18
tests/test_t1_2_trail_step_clamp.py          6 tests  T1-2 F8
tests/test_t1_3_lesson_bridge.py             6 tests  T1-3 F9
tests/test_t1_4_vacuum_migration.py          4 tests  T1-4 F4
tests/test_t2_1_loss_cooldown.py             5 tests  T2-1 F20
tests/test_t2_2_zero_conviction_reject.py    7 tests  T2-2 F14
tests/test_t2_3_direction_disagreement.py    5 tests  T2-3 F11
tests/test_t3_close_attribution_fixes.py     4 tests  T3-2/T3-3/T3-4
tests/test_t3_1_safety_gates.py             14 tests  T3-1 F-4
```

## 4. Operator action required before live verification

These are NOT optional. The system needs operator-initiated steps before the final 24-48h trial can run.

### Step A — Run the T1-4 incremental_vacuum migration (required)

1. Stop trading services:
   ```
   sudo systemctl stop trading-workers trading-mcp-sse
   ps -ef | grep -E "workers\.py|server\.py" | grep -v grep   # confirm both dead
   ```
2. Run the migration:
   ```
   bash scripts/t1_4_migrate_to_incremental_vacuum.sh
   ```
3. Verify post-migration `PRAGMA auto_vacuum` returns 2.
4. Restart services:
   ```
   sudo systemctl start trading-workers trading-mcp-sse
   ```

The pre-fix DB backup is at `data/trading.db.bak-pre-six-tier-fixes-20260511_1444` if rollback is needed.

### Step B — Tail logs and confirm boot signals

After restart, watch for these log lines confirming the fixes are active:

```
DB_AUTO_VACUUM_OK           | mode=INCREMENTAL
                              (connection.py at boot — T1-4 active)
URGENT_QUEUE_CLEAR          | sym=X cleared=N
                              (each close fires this — T1-1 active)
WD_TRAIL_STEP_CLAMPED       | (only when a trail step exceeds cap — T1-2 active)
TIAS_LESSON_BRIDGED         | (each TIAS Phase 2 success — T1-3 active)
COORD_LOSS_COOLDOWN_SET     | (each losing close — T2-1 active)
BYBIT_DEMO_SET_SL_OK        | (each SL change — T4-2 active)
BRAIN_VS_ANALYSIS_DISAGREEMENT | (when brain ≠ analysis — T2-3 visibility)
```

Hourly:
```
VACUUM | mode=incremental pages=1000 success=Y   (T1-4 active)
```

## 5. 24-48h supervised live capture metrics

Capture these hourly from `data/logs/workers.log` and `data/logs/general.log` and compare against the Tier 0 baseline (`dev_notes/six_tier_fixes/tier0_baseline.md` section 5):

| Metric | Pre-fix (Tier 0) | Post-fix target |
|--------|-------------------|-----------------|
| `SENTINEL_FIREWALL_ALLOW act=close` for phantom closes | 8 / 2h50m | 0 |
| `PHANTOM_CLOSE_REJECTED` WARN events | n/a | finite count (proves guard works) |
| `SL_GATEWAY_REJECT step_exceeded` | 10 / 2h50m | < 2 / 2h50m |
| `SL_GATEWAY_REJECT rate_limit` | 108 / 2h50m | < 20 / 2h50m |
| `THESIS_CLOSE` with `lesson=''` | 100% of closes | < 20% of closes (lessons take ~30s-3min to bridge) |
| `DB_LOCK_WAIT holder=execute:VACUUM` | 4 events, up to 21s | 0 (full VACUUM no longer runs) |
| `BYBIT_DEMO_PERSIST_OK table=orders order_id=''` | 24 / 2h50m | 0 (all rows have real or synthetic IDs) |
| `closed_by=bybit_demo_sl_tp` fallback | 13 / 2h50m | < 3 / 2h50m |
| `COORD_DOUBLE_CLOSE` races | 9 / 2h50m | < 2 / 2h50m |
| `BYBIT_DEMO_SET_SL_OK` confirmation rate | 0 / 67 changes | 100% of changes |
| `BASE_WORKER_TICK_SLOW kline_worker` | 33 / 2h50m | < 10 / 2h50m (depends on T1-4) |
| `BASE_WORKER_TICK_SLOW profit_sniper` | 105 / 2h50m | < 30 / 2h50m (depends on T1-4) |
| `BYBIT_DEMO_WS_STALE` | 46 / 2h50m | < 5 / 2h50m (600s threshold) |
| `size_halved_cooldown_*` for same-direction loss | 2 / 2h50m | 0 (rejected outright now) |
| `GATE_REJECT loss_cooldown_same_direction` | n/a | finite count (proves guard works) |
| `GATE_REJECT zero_conviction` | n/a | finite count (proves guard works) |

## 6. Shadow regression sweep

After the 24-48h capture:

```
sudo systemctl stop trading-workers trading-mcp-sse
# Switch transformer_state to shadow mode (operator action)
sqlite3 data/trading.db "UPDATE transformer_state SET mode='shadow' WHERE id=1;"
sudo systemctl start trading-workers trading-mcp-sse
# Watch for 30 min:
tail -F data/logs/workers.log | grep -E "SHADOW|ORDER_RECEIVED|TRADE_SKIP"
```

Confirm zero new errors. Switch back via Telegram dashboard button.

## 7. Aim preservation summary

All 22 fixes preserve the operator's aggressive-exploitation philosophy:

- T1-1 phantom-close guards reject only closes on already-closed positions (no-ops at exchange anyway).
- T1-2 trail step clamp + coalesce IMPROVES peak-profit capture; the gateway was the bottleneck, not strategy.
- T1-3 lesson bridge re-opens the learning channel with age + symbol guards.
- T1-4 incremental_vacuum eliminates the 21s freeze; workers operate faster.
- T2-1 hard-reject affects ONLY same-direction during loss cooldown; opposite-direction trades flow unchanged.
- T2-2 zero-conviction reject defaults to all-zero threshold (only SOLUSDT-class trades affected).
- T2-3 brain-vs-analysis is visibility-only (no enforcement until evidence collected).
- T3-1 safety gates reject only when explicit operator policies (mandatory SL, leverage cap) are violated.
- T3-2/T3-3/T3-4 are observability + audit-correctness fixes; no trade-decision change.
- T4-1/T4-2/T4-3 are observability additions.
- T5-3 sentinel coalesce reduces wasted CPU; no decision change.
- T5-4/T5-5 reduces spurious reconnects without losing genuine-failure detection.
- T6-* are config/cosmetic.

## 8. Out-of-scope items deferred

Per the prompt's "out of scope" list AND items the investigation surfaced:

1. T2-3 enforcement (downgrade or reject on brain-vs-analysis disagreement): deferred pending 1-2 weeks of evidence from the visibility-only T2-3 fix.
2. T1-2 single-writer-of-record (Architectural Theme 1): deferred per plan; T5-3 sentinel coalesce is the partial replacement.
3. T4-1 Claude CLI subprocess stall fix: deferred (requires significant out-of-scope refactor).
4. T4-3 post-place 20s latency root cause: deferred until POST_PLACE_TIMING breadcrumbs surface the specific bottleneck.
5. T6-5 packages 195s old: deferred to a Layer 1D refresh-cadence engagement.
6. T6-6 trail-too-tight: re-measure required after T1-2 deploy.

## 9. Engagement complete

All 22 issues are addressed in code or documented as deferred with rationale. Operator runs the Step A migration, restarts services, captures 24-48h, and the engagement closes after the metric comparison shows the targets in section 5 are met.

Pre-engagement DB backup: `data/trading.db.bak-pre-six-tier-fixes-20260511_1444`.
