# Phase 6 — Phase A Trial Specification (Issue 4 + Issue 2 Concern 7)

Per spec Rule 13 (each fix has measurable success criteria) and Concern 6 evaluation.

## Shipped commits

- **A1**: `fix/dirbias-symmetric-regime-prompt` commit `4b74da7` — Issue 4 symmetric MARKET REGIME block + sentinel correction.
- **A2**: `fix/dirbias-counter-mult-config-test` commit `5c6402e` — Issue 2 Concern 7 config-only test (`counter_confidence_multiplier = 1.0`).

Both branched off `main` (HEAD `5b69233`). Deploy parallel.

## Pre-ship baseline metrics

Captured in `phase0_baseline.md`. Key numbers from May 18 10:00-15:30 audit:
- M1 STRAT_DIRECTIVE: 7 Buy / 84 Sell brain decisions = 92.3% Sell.
- M2 BYBIT_DEMO_ORDER_RECEIVED entry: 9 Buy / 75 Sell = 89.3% Sell.
- M3 7-day Buy WR 45.2%, Sell WR 52.8% (DB).
- M3 14-day Buy WR 41.8%, Sell WR 42.4% (DB).
- M4 Trade frequency: ~15.3/h in audit window.
- M5 Audit-window PnL: +$105.72 over 5.5 hours.
- M6 Shipped fix sentinels firing: R1 trade_direction propagated, wd_brain_scoring_enabled=True, portfolio cap removed (0 events), is_reentry_blocked API present.

## Pre-deploy verification (do before merging A1 + A2)

1. Cherry-pick or merge A1 commit into main (or merge fix/dirbias-symmetric-regime-prompt → main per ops protocol).
2. Cherry-pick or merge A2 commit (or merge fix/dirbias-counter-mult-config-test → main).
3. Run pytest: `pytest tests/test_regime_block_symmetry.py tests/test_stage2_phase4/ tests/test_alpha_r1_trade_direction.py tests/test_strategist_callb_prompt.py tests/test_setup_classifier_counter.py -v`. All should pass; the 1 pre-existing failure `tests/test_apex_direction_lock.py::test_system_prompt_still_has_rsi_caution` is unrelated to these changes.
4. Confirm `config.toml:1724` shows `counter_confidence_multiplier = 1.0`.
5. Service restart: `sudo systemctl restart trading-workers trading-mcp-sse`.

## Boot-time verification (within 30s of restart)

Grep for these sentinels in `data/logs/general.log` or `data/logs/brain.log`:

```bash
grep -E "STRAT_CALL_B_REFRAMED|STRAT_REGIME_INSTR_REFRAMED|SENT_CONSUMPTION_DISABLED" data/logs/general.log | tail -10
```

Expected:
- `STRAT_CALL_B_REFRAMED | system_prompt_version=2 close_rules_removed=2 contract=aggressive_management` — pre-existing, still firing.
- `STRAT_REGIME_INSTR_REFRAMED | block_version=2 mode=symmetric_scenario` — NEW, must appear once per process.
- `SENT_CONSUMPTION_DISABLED | reason=operator_decision_2026-05-06 ...` — pre-existing, still firing.

Within first CALL_A (within ~3 minutes of restart):
- `STRAT_AGGRESSIVE_FRAMING | ... regime_instr=symmetric contract=aggressive_exploit` — UPDATED from `regime_instr=minimal`.

If any of the above sentinels are missing or show the legacy value, abort and investigate.

## Trial window: 48 hours

Measurement starts at first CALL_A after restart. Measurement window: T0 to T0+48h.

## Per-metric thresholds

| Metric | Source | Pass threshold | Hard revert threshold | Sample command |
|---|---|---|---|---|
| M1 — STRAT_DIRECTIVE %Sell | Direct grep on brain.log | 60-90% (shifts from 92.3%) | <30% (over-correction) OR ≥92% (no effect after 24h) | `grep STRAT_DIRECTIVE data/logs/brain.log \| grep -oE "direction=[A-Za-z]+" \| sort \| uniq -c` |
| M2 — BYBIT entry %Sell | Direct grep on workers.log | 50-90% (shifts from 89.3%) | <30% OR ≥92% | `grep "BYBIT_DEMO_ORDER_RECEIVED" data/logs/workers.log \| grep "purpose=layer3_entry" \| grep -oE "side=[A-Za-z]+" \| sort \| uniq -c` |
| M3a — Buy WR | DB trade_log | ≥40% | <35% (HARD REVERT) | `sqlite3 data/trading.db "SELECT direction, 100.0*SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END)/COUNT(*) FROM trade_log WHERE created_at >= '<T0>' AND exchange_mode='bybit_demo' GROUP BY direction;"` |
| M3b — Sell WR | DB trade_log | ≥40% | <35% (HARD REVERT) | (same query) |
| M3c — Counter-LONG WR | DB trade_log + thesis join (or notes filter) | ≥30% | <25% (HARD REVERT for A2) | Run after at least 20 counter-LONG closes accumulate |
| M4 — Trades per hour | workers.log | ≥0.8× baseline | <0.5× baseline | `grep "BYBIT_DEMO_ORDER_RECEIVED" data/logs/workers.log \| grep "purpose=layer3_entry" \| wc -l` / hours |
| M5 — Session PnL | DB trade_log | ≥0.8× baseline | <0.5× baseline | `sqlite3 data/trading.db "SELECT SUM(pnl_usd) FROM trade_log WHERE created_at >= '<T0>' AND exchange_mode='bybit_demo';"` |
| M6 — Shipped sentinels | All log files | All firing | Any missing | Per-sentinel grep |
| M7 — Shadow E2E | Shadow service | Passes | Fails | `pytest tests/shadow_e2e/ -v` (if exists) or operator manual check |
| M8 — DB cascades | general.log | Zero new DB_LOCK_WAIT events | Any cascade | `grep -c "DB_LOCK_WAIT\|DB_MIGRATION_CASCADE" data/logs/general.log` |

## Decision matrix at T0+48h

| Observed | Verdict | Next step |
|---|---|---|
| M1 60-80% AND all M3, M4, M5, M6, M7, M8 PASS | **CLEAN PASS — Phase A is sufficient** | Hold position; observe 5-7 more days. If WR continues converging, consider Phase A complete. |
| M1 80-90% AND all M3, M4, M5, M6, M7, M8 PASS | **PARTIAL PASS — upstream effects remain** | Proceed to ship Issue 3 next (labeller soft haircut). Repeat 48h trial after Issue 3. |
| M1 ≥90% AND all M3, M4, M5, M6, M7, M8 PASS | **PHASE A INERT — fix didn't shift behavior** | Ratify A2 with code removal (Option 7.2 = drop `* counter_mult` from code). Then ship Issue 3. |
| M1 <30% OR Buy share >70% | **OVER-CORRECTION** | HARD REVERT A1. Possibly retain A2. Investigate which fix caused over-correction (A1 wording too strong, or A2 sizing change too aggressive). |
| Any M3 WR <35% | **DIRECTION WR COLLAPSE** | HARD REVERT both. Investigate cause. Likely either A2 over-fired bad counter trades, or A1 made brain pick low-conviction trades. |
| M5 <50% baseline | **PNL HEMORRHAGE** | HARD REVERT both. Compare with concurrent market conditions to rule out external causes. |
| M6 sentinel missing | **REGRESSION IN SHIPPED FIX** | HARD REVERT and diagnose. |
| M8 DB cascade | **REGRESSION** | HARD REVERT and diagnose. |
| M3c counter-LONG WR <25% AND counter-LONG trades >20 | **A2-SPECIFIC REGRESSION** | Revert A2 only. Keep A1. Investigate whether A1's symmetric prompt is also causing more counter-LONG selections that we don't have data for yet. |

## Aim-bias five-question evaluation (per fix)

### A1 — Issue 4 symmetric prompt

| Question | Answer | Reasoning |
|---|---|---|
| 1. Preserve trade frequency? | YES | Prompt edit doesn't gate any trade; Claude may produce more Buys, total count expected to hold. |
| 2. Preserve aggression? | YES | Removes a directive ("DEFAULT SELL BIAS"); doesn't add a new block. |
| 3. Improve decision quality? | YES | Symmetric framing lets Claude weigh both directions on per-coin evidence. |
| 4. Preserve passive-close advantage? | N/A | No close-side change in this fix. |
| 5. Structural separation? | YES | Layer 2 fix lives in Layer 2 (strategist.py). No cross-layer reach. |

All five YES. Fix passes aim-bias test.

### A2 — Issue 2 Concern 7 config test

| Question | Answer | Reasoning |
|---|---|---|
| 1. Preserve trade frequency? | YES | Removes a confidence cut; may admit more counter trades at sizing time. Total trade count likely rises slightly. |
| 2. Preserve aggression? | YES | Removes a hardcoded suppression. |
| 3. Improve decision quality? | YES | Counter setups with strong MTF/SMC no longer pre-suppressed; Brain sees full conviction signal. |
| 4. Preserve passive-close advantage? | YES | `setup_type_confidence` used in Layer 4 force-close drop ratio now symmetric on counter vs in-direction (per Phase 1.2 finding). |
| 5. Structural separation? | YES | Layer 1B fix (structure_engine) lives in Layer 1B. No cross-layer reach. |

All five YES. Fix passes aim-bias test.

## Per-fix rollback procedure

### Rollback A1 (Issue 4)

```bash
git revert 4b74da7
sudo systemctl restart trading-workers trading-mcp-sse
```

OR if the commit was merged into main: `git revert <merge-commit-sha>`.

Verification post-revert: `grep STRAT_REGIME_INSTR_REFRAMED data/logs/general.log` should stop seeing new emissions; `grep "regime_instr=minimal" data/logs/brain.log` should resume.

### Rollback A2 (Issue 2 Concern 7)

```bash
git checkout main -- config.toml
# OR: git revert 5c6402e
sudo systemctl restart trading-workers trading-mcp-sse
```

Verification post-revert: `grep "counter_confidence_multiplier" config.toml` should show `= 0.7`.

### Combined rollback

Revert both commits in reverse order: A2 first, then A1. Single service restart.

## Phase 7 (final integration verification) trigger

Phase 7 begins when:
- 48h Phase A trial PASS verdict reached, OR
- After Issue 3 ships in Phase B (if Phase A was PARTIAL PASS), 48h after that, OR
- After Issue 1 ships in Phase C (if Phase B was PARTIAL PASS), 48h after that.

Phase 7 evaluates the cumulative shipped state over 48-72 hours against the project end-state criteria in MASTER_REPORT.md §10.

## Open operator actions

Operator must:
1. Merge `fix/dirbias-symmetric-regime-prompt` to `main` (PR creation, review, merge).
2. Merge `fix/dirbias-counter-mult-config-test` to `main` (same).
3. Restart services.
4. Run boot-time verification (sentinel grep).
5. Wait 48h.
6. Apply decision matrix above.
7. If next phase needed, prepare for Phase B (Issue 3) or ratification (Option 7.2).

Standing by for operator action.
