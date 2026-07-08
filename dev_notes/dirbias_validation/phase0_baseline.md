# Phase 0 — Pre-Flight Verification and Baseline Metrics

Date: 2026-05-19 04:35 UTC.  
Branch: `fix/wd-scoring-brain-vote`.  
Spec: `/home/inshadaliqbal786/IMPLEMENT_DIRBIAS_VALIDATION_AND_FIX.md`.  
Plan file: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-nifty-toast.md`.

## 1. Working tree status

Branch `fix/wd-scoring-brain-vote`. Two runtime files modified (`data/layer_state.json`, `data/logs/layer1c_full.jsonl` — not implementation code). 26 untracked files, all in `data/` and `dev_notes/` (runtime artifacts, prior session deliverables, backup DB). No implementation files dirty. Working tree is implementation-clean.

Action: no working-tree cleanup required before Phase 1. The runtime modifications are expected (workers write to them continuously). The untracked dev_notes are deliverables from prior fix sessions that remained un-committed; they should be reviewed and either committed or moved to `.gitignore` in a future hygiene pass — out of scope for this investigation.

## 2. Previously-shipped fixes — all present

| Fix | Verification | Result |
|---|---|---|
| R1 XRAY counter-inversion | `grep "trade_direction" src/apex/assembler.py` | Confirmed at line 767: `sd.trade_direction = str(getattr(analysis, "trade_direction", "") or "")` |
| wd_claude_action scoring | `grep "wd_brain_scoring_enabled" src/config/settings.py` | Confirmed at lines 974-975 with `enabled=True, enforce=False` (Phase 1 log-only mode as designed) |
| Portfolio cap (R4) removed | `grep -c "PORTFOLIO_CAP_HIT" src/apex/gate.py` | Confirmed `0` matches — CHECK 15 deleted as expected |
| 5-min reentry cooldown | `grep "is_reentry_blocked" src/core/trade_coordinator.py` | Confirmed API at lines 1481, 310, 1580 |

No regressions in shipped fixes. Investigation may proceed.

## 3. Validation working directory

Created `dev_notes/dirbias_validation/` at `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/dirbias_validation/`. Permissions: 775. Empty at Phase 0 start.

## 4. Baseline metrics — May 18 10:00-15:30 audit window

This is the primary baseline. Source log: `/home/inshadaliqbal786/ALL_LOGS_2026-05-18_10-00_to_15-30.log` (27 MB, 122,026 lines, generated 2026-05-19 02:30).

### 4.1 Funnel direction distribution

| Stage | Buy | Sell | %Sell |
|---|---:|---:|---:|
| Regime emissions (per-coin, 5.5h) | trending_up 176 | trending_down 1,567 | 8.9× downtrend-biased market |
| XRAY `suggested_direction` | 810 | 5,697 | 87.6% |
| XRAY `trade_direction` (post-counter-inversion) | 1,488 | 5,551 | 78.9% |
| `SCANNER_LABELED` primary | 148 | 716 | 82.9% |
| `APEX_LOCK_DECISION_EXPLAINED` brain decision | 7 | 84 | 92.3% |
| `BYBIT_DEMO_ORDER_RECEIVED purpose=layer3_entry` | 9 | 75 | 89.3% |

### 4.2 XRAY counter-setup density

| setup_type | count | conf range |
|---|---:|---|
| bearish_fvg_ob (in-direction SHORT) | 4,124 | 0.55-0.70 |
| bearish_structural_break | 718 | — |
| bullish_fvg_ob_counter (counter LONG against bearish bias) | 1,140 | 0.10-0.49 |
| bearish_fvg_ob_counter | 624 | 0.10-0.49 |
| bullish_fvg_ob (in-direction LONG) | 342 | 0.55-0.70 |

Total in-direction: 4,184. Total counter: 1,764. Counter ratio: 30%.

### 4.3 XRAY_DIRECTION_SPLIT samples

Modal distribution (12 samples each):
- `long_pct=6.0 short_pct=80.0 counter_count=4`
- `long_pct=28.0 short_pct=70.0 counter_count=4`
- `long_pct=18.0 short_pct=82.0`

### 4.4 APEX_LOCK_OVERRIDE_GRANTED

27 events. 22 of 27 flipped `brain_dir=Sell → qwen_dir=Buy` (override layer is *reducing* Sell concentration, not increasing it).

### 4.5 R3 WR-derived threshold smoking gun

```
XRAY_OVERRIDE_RATIO_DETAIL | flipped_dir=Buy buy_wr=46.0 sell_wr=49.1
                            buy_n=37 sell_n=163 derived_threshold=5.41 xray_ratio=0.15 source=wr
```

`buy_n=37` vs `sell_n=163` confirms WR data is feeding asymmetric thresholds. `xray_ratio=0.15` is far below `derived_threshold=5.41` — this particular flip never would have triggered regardless of threshold.

## 5. Baseline metrics — current `workers.log` (May 18 15:19 → May 19 04:35)

Source: `data/logs/workers.log` (2.5 MB, ~13 hours).

### 5.1 Sparse activity

| Metric | Count |
|---|---:|
| `STRAT_DIRECTIVE` direction events | 0 |
| `BYBIT_DEMO_ORDER_RECEIVED purpose=layer3_entry` | 4 (1 Buy + 3 Sell) |
| `APEX_LOCK_DECISION_EXPLAINED` | 5 (all Sell) |
| `XRAY_DIRECTION_SPLIT` | 3 |
| `XRAY_CLASSIFY` | 147 |
| `SCANNER_LABELED` | 48 |
| Regime emissions (per-coin) | 154 (52 trending_down, 48 volatile, 48 ranging, 6 trending_up) |
| `APEX_LOCK_OVERRIDE_GRANTED` | 1 (brain Sell → Buy) |
| `STRAT_AGGRESSIVE_FRAMING` | 0 |
| `STRAT_CALL_B_REFRAMED` boot | 0 |

The 13-hour current-log window is much less active than the 5.5-hour May 18 audit. Possible explanations:
- Worker process(es) restarted recently (no boot sentinel in this log slice).
- System has been in a quiet market state (no trades open → no CALL_B activity → minimal events).
- Workers were paused or partially running.

`brain.log` (separate file, range 2026-03-29 → 2026-05-19 02:28) shows 613 `STRAT_AGGRESSIVE_FRAMING` lifetime emissions. So brain CALL_A events DO fire — just in a different log file. The 0 in `workers.log` is expected (workers and brain log to separate sinks).

### 5.2 Most recent BYBIT_DEMO_ORDER_RECEIVED entries (post-audit window)

| Side | Count over 13h |
|---|---:|
| Buy | 1 |
| Sell | 3 |

4 entries in 13 hours is ~31× lower frequency than the audit window (84 entries in 5.5h ≈ 15.3/h vs current 0.31/h). System is markedly quieter in this slice. Likely market quiet, not a system fault.

## 6. Baseline metrics — DB (trading.db, bybit_demo only)

### 6.1 Recent windows

| Window | Buy count | Buy WR | Buy PnL | Sell count | Sell WR | Sell PnL |
|---|---:|---:|---:|---:|---:|---:|
| Last 7 days | 93 | 45.2% | +$87.91 | 392 | 52.8% | +$411.56 |
| Last 30 days | 122 | 41.8% | +$106.51 | 681 | 42.4% | +$366.16 |
| All-time | 122 | 41.8% | +$106.51 | 681 | 42.4% | +$366.16 |

All-time equals 30-day because the system has only been running for ~10 days (earliest trade: 2026-05-09 13:06; latest: 2026-05-18 15:34).

### 6.2 Critical baseline insight — 30-day WR is below break-even for BOTH directions

The 7-day Sell WR of 52.8% is anomalously good. **Over 30 days, Sell WR drops to 42.4% — below break-even.** Buy WR is consistently below break-even (41.8% over 30d, 45.2% over 7d). Both directions are losing on average over the full system lifetime; the system is essentially break-even in aggregate ($472.67 total PnL over 803 trades ≈ $0.59/trade).

**This complicates Concern 8 (bias might be correct).** The 7-day window cherry-picks a Sell-favorable slice. The longer 30d view shows Sells are barely profitable. Both directions need improvement, not just Buy rebalancing.

### 6.3 Daily breakdown (last 5 days)

| Day | Buy n | Buy WR | Buy PnL | Sell n | Sell WR | Sell PnL |
|---|---:|---:|---:|---:|---:|---:|
| 2026-05-14 | 30 | 53.3% | -$11.68 | 33 | 39.4% | -$122.70 |
| 2026-05-15 | 17 | 47.1% | +$29.35 | 41 | 51.2% | +$75.07 |
| 2026-05-16 | 7 | 85.7% | +$5.09 | 81 | 44.4% | -$15.67 |
| 2026-05-17 | 14 | 28.6% | -$28.07 | 43 | 55.8% | +$42.99 |
| 2026-05-18 | 9 | 33.3% | -$6.51 | 85 | 58.8% | +$146.92 |

Variance is high day-to-day. 2026-05-14 was a bad day for both directions (-$11.68 + -$122.70 = -$134.38). 2026-05-18 was a strong Sell day (+$146.92) with poor Buys (-$6.51). The "Sell bias is profitable" narrative is dominated by 2026-05-15 and 2026-05-18.

### 6.4 Active state

- Positions table: empty (no active positions right now).
- `trade_thesis` open: 0.
- Latest trade: 2026-05-18 15:34:30. No trades since.

## 7. Discrepancy/correction notes for Phase 1+

- Spec line 433 typo: says `src/labellers/state_labeler.py` (does not exist). Correct path is `src/workers/scanner/state_labeler.py`. This is a SPEC typo, not a code typo. Will be raised with operator at the Phase 4 Master Report gate.
- `STRAT_AGGRESSIVE_FRAMING` boot sentinel falsely claims `regime_instr=minimal`. The asymmetric MARKET REGIME block at `strategist.py:3371-3390` IS emitted in the live CALL_A user prompt. The sentinel mis-advertises state — this is Issue 4 sub-finding and will be addressed in any Issue 4 fix path.

## 8. Phase 0 verdict

System state is suitable for Phase 1 investigation. All shipped fixes confirmed present. Baseline metrics captured. Workspace established. No blockers.

Proceeding to Phase 1.
