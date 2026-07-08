# Phase 0 — Pre-Flight Baseline

Captured 2026-05-12 07:47 UTC. Read-only. No code changes.

## Working tree state

- Branch: `fix/sell-bias-fixes-2026-05-11`
- HEAD: `848fe40c9e5788ab21441cf117bb1de29063d67f`
- Last 5 commits:
  - `848fe40` docs(p): real-project pipeline verification — 8 end-to-end checks PASS
  - `4bcc174` docs(p): deep audit report — 14 audit phases (A-N) pass
  - `3a552fb` fix(p): harden _check_insufficient_data_for_flip against degraded inputs
  - `18bc8cd` docs(p): cross-check report — 8-audit pass + hardening summary
  - `c1d0b33` test(p): cross-check hardening — endswith() counter-trade match + flip-decision integration tests
- Working tree NOT pristine. Modified files are state writes by running workers (not source WIP):
  - `data/layer_state.json` — Layer 1 worker state cache
  - `data/logs/layer1c_full.jsonl` — Layer 1C structured event stream
  - Untracked: `dev_notes/critical_high_fixes/LIVE_LOGS_2026-05-10_05-03_to_now.log`, `dev_notes/critical_high_fixes/LIVE_LOG_ANOMALY_REPORT_2026-05-10.md`, `dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db`, `dev_notes/sell_bias_fixes/p_phase4_live_monitoring_findings.md`, `dev_notes/three_issues/`
- Decision: state writes are expected; investigation proceeds without freezing or stashing.

## Services running

| Process | PID | State |
|---|---|---|
| workers.py | 398 | running |
| server.py --transport sse --port 8080 | 399 | running |
| brain.py | n/a | spawned per-decision as subprocess |
| pm2 (n8n only) | n/a | stopped, unrelated |

## PRIMARY APEX sell-bias fix verification

All elements confirmed present in current source.

| Element | File:Line | Status |
|---|---|---|
| APEX_FLIP_DECISION unified log emission | `src/apex/optimizer.py:547,588` | confirmed |
| `apex_min_flip_confidence_buy_to_sell = 0.95` | `src/config/settings.py:1931` | confirmed |
| `apex_min_flip_confidence_sell_to_buy = 0.70` | `src/config/settings.py:1932` | confirmed |
| `_enforce_flip_confidence` method | `src/apex/optimizer.py:1219,476` | confirmed |
| `apex_respect_counter_trade` setting + APEX_FLIP_COUNTER_PROTECTED gate | `src/apex/optimizer.py:419` | confirmed |
| `apex_min_trades_for_flip` setting + insufficient-data gate | `src/apex/optimizer.py:458,1116` | confirmed |
| `structural_data` typo fix (proper underscore form) | `src/strategies/scorer.py:38,43,51,75,80,265,269,285` | confirmed |

Live-log timing evidence — the fix's effect on production:

- 13 APEX-flip events (`DIRECTION_DECISION reason=apex_flip`) recorded between 2026-05-11 12:02:58 and 19:08:51.
- After 2026-05-11 22:23 the unified `APEX_FLIP_DECISION` log starts emitting.
- In the 21 `APEX_FLIP_DECISION` events captured from 22:23 onward, `brain_dir` equals `apex_dir` in every case — zero APEX flips after the deploy.
- Decision reasons observed: `no_flip_attempt 13`, `insufficient_data 3`, `conf_below_threshold 3`, `lock_override 1`, `counter_protected 1`.

Conclusion: PRIMARY fix is in place and is actively preventing APEX-layer Buy → Sell flips post-deploy. The remaining sell-bias is downstream (XRAY layer) and upstream (brain decision itself).

## Current configuration (canonical values from `config.toml`)

### `[risk]` (XRAY threshold)

```
xray_dir_flip_threshold_ratio = 3.0
```

### `[apex]` (flip discipline)

```
apex_min_flip_confidence              = 0.70   # symmetric fallback
apex_min_flip_confidence_buy_to_sell  = 0.95   # high bar — Buy→Sell is harmful
apex_min_flip_confidence_sell_to_buy  = 0.70   # moderate bar — Sell→Buy helps
```

### `[regime]` (classifier thresholds)

```
trending_adx_threshold        = 25
ranging_adx_threshold         = 20
ranging_choppiness_threshold  = 60
volatile_atr_percentile       = 150
dead_adx_threshold            = 15
dead_volume_ratio             = 0.5
hysteresis_count              = 2
```

Notes on regime config vs `RegimeDetector.detect()` at `src/strategies/regime.py:128-156`:

- The classifier uses `atr_percentile = natr * 100`. `volatile_atr_percentile = 150` is therefore unreachable by the NATR-derived value, which is normalized between roughly 0 and 100 in normal markets. The `VOLATILE` branch can only fire via the OR-clause `volume_ratio > 2.0`.
- `RANGING` requires both `adx < 20` AND `choppiness > 60` — strict definition.
- `TRENDING_UP/DOWN` requires `adx > 25` AND DIs aligned AND `choppiness < 45`.
- `DEAD` requires `adx < 15` AND `volume_ratio < 0.5` AND `atr_percentile < 50`.
- Any value outside these branches falls through to the `else` clause at lines 153-156 → `RANGING` with confidence `0.4`.
- Additional fallback at lines 91-113: if klines count < 50, return `RANGING` with confidence `0.3`.

These two fallbacks (insufficient-data, transition-band) are the primary candidates for inflating the `ranging` distribution observed in production.

### Regime worker scheduling

- `src/workers/regime_worker.py:48` — sweet spot `settings.workers.sweet_spots.regime_worker` (default `"1:15"`, fires at minute 15 of every cycle).
- Universe: 50 coins from `settings.universe.watch_list`.
- Global regime uses `settings.regime.primary_symbol` (BTCUSDT).

## Baseline metrics

Window definitions used below:

- **Wide window**: 2026-05-11 11:55:43 → 2026-05-12 07:47:24 UTC (~19.9h, four workers log files).
- **Post-deploy window**: 2026-05-11 22:23 → 2026-05-12 07:47 UTC (~9.4h, since PRIMARY fix started emitting `APEX_FLIP_DECISION`).

### Direction distribution

Source: `DIRECTION_DECISION` events emitted by `src/workers/strategy_worker.py:_execute_claude_trade:2254`.

**Wide window (n=86):**

| Field | Buy | Sell | Sell share |
|---|---|---|---|
| `brain_dir` | 33 (38.4%) | 53 (61.6%) | 61.6% |
| `final_dir` | 5 (5.8%) | 81 (94.2%) | **94.2%** |

Of 33 brain Buys, 28 (84.8%) were flipped to Sell in the wide window. Breakdown of `flipped=Y` events:

- `flip_source=xray`: 24 events
- `flip_source=none` + `reason=apex_flip`: 13 events (pre-deploy APEX flips; the new unified `APEX_FLIP_DECISION` log was not yet active during these events)

The `flip_source=none reason=apex_flip` combination is a known logging gap in the pre-PRIMARY-fix code path. It does not appear post-deploy.

**Post-deploy window (n=21):**

| Field | Buy | Sell | Sell share |
|---|---|---|---|
| `brain_dir` | 7 (33.3%) | 14 (66.7%) | 66.7% |
| `final_dir` | 2 (9.5%) | 19 (90.5%) | **90.5%** |

Of 7 brain Buys, 5 (71.4%) were flipped to Sell — all by XRAY. Breakdown:

- `reason=clean`: 10 events
- `reason=xray_flip`: 5 events
- `reason=xray_flip_suppressed_by_lock`: 5 events (APEX direction lock preserved the Buy on a trending coin)
- `reason=apex_dir_lock_held`: 1 event
- `flip_source=xray`: 5 events
- `flip_source=none`: 16 events
- Zero `reason=apex_flip` events

Conclusion: APEX is preserving all direction decisions post-deploy. XRAY is now the sole layer flipping Buys to Sells.

### Regime classification distribution

Source: `REGIME |` log lines emitted by `src/strategies/regime.py:171`.

**Wide window (n=3699):**

| Regime | Count | Share |
|---|---|---|
| ranging | 2882 | 77.9% |
| volatile | 294 | 7.9% |
| trending_down | 244 | 6.6% |
| dead | 199 | 5.4% |
| trending_up | 80 | 2.2% |

**Post-deploy window (n=886):**

| Regime | Count | Share |
|---|---|---|
| ranging | 751 | 84.8% |
| trending_down | 51 | 5.8% |
| volatile | 31 | 3.5% |
| dead | 27 | 3.0% |
| trending_up | 26 | 2.9% |

Observations:

- The 78-85% ranging share is consistent with the spec's 28/30 (93%) observation and is the central question of this investigation.
- `trending_up : trending_down` ratio is approximately `1 : 2`. The detector labels roughly twice as many bearish trends as bullish trends. This asymmetry independently contributes to sell-bias via the strategist's directional-bias prompt at `src/brain/strategist.py:80-95`.
- `volatile` declined from 7.9% in the wide window to 3.5% post-deploy. Consistent with markets quieting overnight UTC.

### XRAY flip event counts (wide window)

| Event | Count |
|---|---|
| `XRAY_DIR_FLIP` | 24 |
| `XRAY_FLIP_SUPPRESSED_BY_LOCK` | 6 |
| `XRAY_DIR_FLIP_BLOCKED` | 0 |
| `XRAY_DIR_BLOCK` | 0 |

The block variants (`_BLOCKED`, `_BLOCK`) are not firing — XRAY's structural-conflict check rarely overrides a structurally-preferred flip. Six flips were suppressed by `apex_locked=Y`, which corresponds to coins where APEX's direction lock fired (trending or volatile-with-evidence regimes).

### APEX flip decisions (wide window, n=21 events with the unified log)

Source: `APEX_FLIP_DECISION` log emitted by `src/apex/optimizer.py:588`.

| decision_reason | Count |
|---|---|
| no_flip_attempt | 13 |
| insufficient_data | 3 |
| conf_below_threshold | 3 |
| lock_override | 1 |
| counter_protected | 1 |

In every case `brain_dir == apex_dir`. Counter-trade and insufficient-data gates fired correctly.

### Trade history (DB)

Source: `data/trading.db` table `trade_history`, last 14h window (`entry_time > datetime('now','-14 hours')`).

| side | trades | wins | win_rate | total_pnl |
|---|---|---|---|---|
| Buy | 9 | 4 | 44.4% | -12.21 |
| Sell | 110 | 51 | 46.4% | +45.70 |

Executed trades are 92.4% Sell. Win rates between sides are nearly identical (~45%). Net PnL is positive (+33.49) but the Sell-bias remains the central operational concern.

## Open questions to be answered by later phases

These are the deltas the investigation will explain:

1. **Per-coin variance** — Of the 886 post-deploy regime classifications, how many distinct regimes were assigned at the same 5-minute timestamp across the 50 coins of the watch_list? If divergence rate is high, the detector behaves per-coin. (Phase 1 Step 1.5)
2. **Flip-causation chain** — Of the 5 post-deploy XRAY flips, what regime label did each symbol carry? Did APEX direction-lock fire or not? Was structural R:R legitimately high? (Phase 1 Step 1.6 — Q1b)
3. **Accuracy of ranging label** — Of regime samples labeled `ranging`, how many were actually ranging per objective 5-min kline analysis? (Phase 2)
4. **Transition-band leakage** — Of `ranging` samples, how many had ADX in [20, 25) or choppiness in [45, 60] (the `else = RANGING` branch)? (Phase 2 Step 2.4)

## Verification gate before Phase 1

- Phase 0 deliverable exists at this path: **PASS**
- PRIMARY fix elements confirmed in current source: **PASS**
- Wide-window baseline metrics captured with concrete numbers: **PASS**
- Post-deploy baseline metrics captured: **PASS**
- Current configuration documented verbatim: **PASS**

Phase 0 closed. Phase 1 cleared to begin.
