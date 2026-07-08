# Phase 0 — Pre-Flight Baseline (shared, all 5 issues)

Date: 2026-05-11
Owner: investigator (Claude)
Spec: `/home/inshadaliqbal786/IMPLEMENT_SELL_BIAS_AND_PROFIT_EATING_FIXES.md`
Status: Phase 0 captured. PRIMARY Phase 1 next. No code changes in this stretch.

## 1. System State At Time Of Capture

- Repo: `/home/inshadaliqbal786/trading-intelligence-mcp`
- Branch: `fix/five-critical-fixes-2026-05-11` (last commit `79c0c15 test(e2e): six-tier-fixes engagement`)
- Services: `trading-mcp-sse.service` and `trading-workers.service` STOPPED by operator. DB and logs frozen.
- Live store: `data/trading.db` (175 MB, snapshot is frozen). Root `trading.db` (4 KB) is a stale stub — ignore.
- Working-tree noise (not committed under this work): `data/layer_state.json`, `data/logs/layer1c_full.jsonl` — runtime artifacts from the now-stopped workers.
- Branch policy locked: single `fix/sell-bias-fixes-2026-05-11` for the eventual implementation, with per-issue commit prefixes (`p/`, `i2/`, `i4/`, `i3/`, `i5/`). Branch NOT yet created — this stretch is read-only.

## 2. Spec-Reference Verification (Part C anchors)

Cross-checked against current source. Spec's reported line numbers were close but not exact in places — actuals listed.

### APEX subsystem (PRIMARY)

- `src/apex/optimizer.py` (47743 bytes, ~1100 lines)
  - `optimize()` orchestration: **lines 94-494** (spec said ~179, ~467)
  - `_check_direction_lock()` pre-call lock: **lines 885-931**
  - `_enforce_flip_confidence()`: **lines 933-977** — critical gate at line 972 (`apex_min_flip_confidence` default 0.70)
  - RR-weighted confidence boost: **lines 359-387**
  - `_apply_flip_resize_policy()`: **lines 979-1032** (spec said ~1024) — `APEX_FLIP_RESIZE_ACCEPTED` line 1025, `APEX_FLIP_RESIZE_CAPPED` line 1015
  - `_log_optimization()`: **lines 787-847** (spec said ~802) — `APEX_FLIP` WARNING line 803, `APEX_OK` line 824
  - Tags emitted: `APEX_TIER` (180/187/207), `APEX_DIR_LOCK` (229), `APEX_DIR_LOCK_OVERRIDE` (332), `APEX_FLIP_BLOCKED` (395)

- `src/apex/qwen_client.py` (15597 bytes)
  - HTTP optimize(): **lines 134-274**
  - `_parse_json()`: **lines 298-348**
  - `APEX_QWEN_OK` emitter: line 262 — confirmed in live logs (sample: `model=deepseek/deepseek-v3.2-20251201 latency_ms=798-3186 tokens_in=~2800 tokens_out=~280 cost_usd=~$0.001 per call`)

- `src/apex/prompts.py` (~226 lines)
  - `APEX_SYSTEM_PROMPT` regime rules: **lines 21-75** — explicit ranging-regime line: "Both directions valid, use direction breakdown"
  - `build_apex_user_prompt()`: **lines 82-226** (5 sections + JSON output schema with `confidence` field)

- `src/apex/gate.py` `validate()` lines 48-496 — does NOT mutate direction (confirmed).
- `src/apex/assembler.py` `assemble()` lines 55-119.
- `src/apex/models.py` `OptimizedTrade` lines 394-444 with `was_flipped`, `original_direction`, `is_locked`, `lock_reason`, `confidence` fields.

### Strategy worker + XRAY + regime (PRIMARY)

- `src/workers/strategy_worker.py` (130355 bytes)
  - `_execute_claude_trade()`: lines 1417-2283
  - `XRAY_DIR_FLIP` (WARNING): line 1769
  - `XRAY_FLIP_SUPPRESSED_BY_LOCK`: lines 1648-1660
  - xray_ratio computation: lines 1620-1622, 1757
  - `xray_dir_flip_threshold_ratio` default 3.0 (config.toml line 341)
  - `DIRECTION_DECISION` (INFO): lines 2254-2266 — fields: sym, brain_dir, final_dir, flipped, flip_source, apex_locked, lock_reason, xray_ratio, reason, analysis_dir, analysis_score, analysis_conf
  - final_dir resolution: lines 2200-2214
  - 5 reason codes: `clean`, `apex_flip`, `xray_flip`, `apex_dir_lock_held`, `xray_flip_suppressed_by_lock`

- `src/core/thesis_manager.py`: `THESIS_FLIP_PERSISTED` line 189.
- `src/strategies/regime.py`: classification at lines 133-156 (TRENDING_UP/DOWN, VOLATILE, RANGING, DEAD); 2-tick hysteresis.

### SL Gateway + writers + close coordinator (Issues 2 & 4)

- `src/core/sl_gateway.py` (727 lines): `SLGateway` class line 145; reject codes line 102-104; R2 too-close lines 411-429; R3 step-exceeded lines 442-452; R4 rate-limit lines 463-475; `_log_reject` lines 548-563.
- `src/workers/profit_sniper.py` (3753 lines) — Writer 1: `profit_sniper_trail` at line 1566.
- `src/workers/position_watchdog.py` (3451 lines) — Writers 2 & 3 at lines 954, 1506.
- `src/sentinel/deadline.py`: `DeadlineEngine` class line 54.
- `src/core/trade_coordinator.py`: `COORD_DOUBLE_CLOSE` at line 730.

### Profit Sniper (Issue 3) & Time-Decay (Issue 5)

- `_compute_trail_stop`: lines 1193-1272+
- `_determine_action`: lines 1679-1927 (thresholds lines 57-62)
- `_stall_escape_action`: lines 2387-2540+ (`SNIPER_AGE_GUARD` line 2478)
- `M4_DECISION`: line 1890
- `mode4_stall_valve` event emit: lines 3001-3008
- `src/risk/time_decay_sl.py` `calculate()`: lines 283-349; `TIME_DECAY_AGE_GUARD` line 344

### Live config values (config.toml)

- `[sl_gateway]` line 513: `min_distance_pct=0.3` (533), `max_step_pct=0.25` (540), `rate_limit_seconds=30` (542)
- `[risk] xray_dir_flip_threshold_ratio = 3.0` (line 341)
- `[apex]` flip controls (located in P.1.1 follow-up grep):
  - `apex_min_flip_confidence = 0.70`
  - `apex_block_flip_resize = true`
  - `apex_flip_rr_boost_threshold = 3.0`
  - `apex_flip_rr_boost_amount = 0.15`
  - `model = "deepseek/deepseek-v3.2"` (live shows full versioned `deepseek-v3.2-20251201`)
- `[mode4]` line 1097; sniper `min_age_seconds=300` (line 1267); time_decay `min_age_seconds=300` (line 1604)

## 3. PRIMARY Issue Baseline — Direction Distribution

### 3.1 bybit_demo (lifetime to date)

bybit_demo trading began 2026-05-09 13:06:05. Cutover window: 2026-05-09 through 2026-05-11 (today). Total: 295 trades over ~2.3 days.

| Direction | N | wins | WR | avg pnl_pct | net pnl_usd |
|-----------|---|------|----|-------------|-------------|
| Buy       | 27  | 8  | 29.6% | +0.066% | $-4.69   |
| Sell      | 268 | 73 | 27.2% | -0.015% | $-85.12  |
| **Total** | 295 | 81 | 27.5% | -0.008% | $-89.81  |

268/295 = **90.8% Sell trades all-time on bybit_demo**.

### 3.2 bybit_demo last 24h (subset)

| Direction | N | wins | WR | avg pnl_pct | net pnl_usd |
|-----------|---|------|----|-------------|-------------|
| Buy       | 7  | 3  | 42.9% | -0.065% | $-35.50 |
| Sell      | 89 | 42 | 47.2% | +0.045% | $+5.98  |

89/96 = **92.7% Sell** in the last 24h. Both directions saw improved WR in the last 24h relative to lifetime, but the directional skew persists.

### 3.3 Today (2026-05-11) DIRECTION_DECISION log analysis

From `data/logs/workers.log` + `workers.2026-05-11_11-55-43_739853.log` (~9 hours of logs, 65 DIRECTION_DECISION events):

| Field           | Distribution |
|-----------------|--------------|
| `brain_dir=Buy`  | 26 |
| `brain_dir=Sell` | 39 |
| `final_dir=Buy`  | 3 |
| `final_dir=Sell` | 62 |
| `flipped=Y`     | 32 (49.2%) |
| `flipped=N`     | 33 (50.8%) |

Reason taxonomy (matches Explore findings — 5 codes):

| Reason | Count |
|--------|-------|
| `clean`                          | 28 |
| `apex_flip`                      | 13 |
| `xray_flip`                      | 19 |
| `apex_dir_lock_held`             | 4 |
| `xray_flip_suppressed_by_lock`   | 1 |

**Key observations:**

1. Brain itself already biased Sell (39/65 = 60% Sell brain decisions today). Pre-existing tilt before any flip.
2. Of 26 Buy brain decisions, only 3 reached final_dir=Buy. **Buy survival rate: 11.5%.**
3. XRAY drove MORE flips than APEX today (19 vs 13). The spec characterizes the issue as primarily APEX-driven, but XRAY is the larger contributor by event count. **Both paths need investigation.**
4. APEX flips today were 23/23 = **100% in regime=ranging** (per `APEX_FLIP | ... regime=ranging` grep). The spec's 93% figure was within sampling tolerance — actual is even more skewed.
5. `xray_flip_suppressed_by_lock` fires once today — confirms the recently-shipped XRAY-vs-APEX lock interlock works as designed (Phase 1E lock plumbing 2026-05-11).

## 4. PRIMARY Baseline — Flipped vs Unflipped Performance (THE CRITICAL STATISTIC)

From `trade_intelligence` table (`apex_flipped` binary, 30-day window):

### 4.1 bybit_demo

| apex_flipped | N | wins | WR | avg pnl_pct | net pnl_usd |
|--------------|---|------|----|-------------|-------------|
| 0 (unflipped) | 124 | 46 | 37.1% | -0.011% | $-8.36   |
| 1 (flipped)   | 211 | 60 | 28.4% | -0.013% | **$+211.40** |

### 4.2 shadow

| apex_flipped | N | wins | WR | avg pnl_pct | net pnl_usd |
|--------------|---|------|----|-------------|-------------|
| 0 (unflipped) | 661 | 285 | 43.1% | -0.013% | **$+167.02** |
| 1 (flipped)   | 257 | 93  | 36.2% | -0.112% | **$-166.96** |

**This is the critical strategic statistic the operator needs.**

Reading: in **shadow**, the flip policy is clearly destructive — unflipped trades net $+167 while flipped trades net $-167. Same WR delta (-7 percentage points when flipped). In **bybit_demo**, the picture is reversed and confusing — flipped trades net $+211 while unflipped trades net $-8, but win rate is still 8.7 percentage points worse when flipped (it appears that flipped winners are bigger or flipped losers smaller, while unflipped is barely net-zero on a tiny sample of 124 trades).

Caveats:
- bybit_demo sample is only 2.3 days (335 trades total in the 30d-window column). Statistical strength is limited.
- The "flipped" set in shadow is over a long history (older system) where settings differed. Comparisons across mode need careful framing.

### 4.3 Flip-pair breakdown — bybit_demo

| apex_original_direction | apex_final_direction | N | wins | WR | avg pnl_pct | net pnl_usd |
|--------------------------|-----------------------|----|------|----|-------------|-------------|
| Buy  | Buy  (unflipped) | 32  | 12 | 37.5% | +0.093% | $+8.71    |
| Buy  | Sell (flipped Buy→Sell) | 176 | 54 | 30.7% | +0.020% | $+223.56  |
| Sell | Buy  (flipped Sell→Buy)  | 3   | 2  | 66.7% | -0.259% | $+5.93    |
| Sell | Sell (unflipped) | 113 | 33 | 29.2% | -0.113% | $-46.83   |

Reading: **Sell→Sell** (the system's default direction without flip) is the biggest losing cohort on bybit_demo ($-46.83 net). **Buy→Sell** (the flip cohort) is the biggest winner ($+223.56 net). **Buy→Buy** (small cohort, unflipped Buy) is the highest-WR cohort (37.5%).

### 4.4 DeepSeek post-hoc correctness verdict (`ds_optimal_direction`)

For 335 bybit_demo trades with a `ds_optimal_direction` populated:

| ds_optimal_direction | N | win-rate |
|----------------------|---|----------|
| YES (system direction was right) | 133 | 73.7% |
| NO  (system direction was wrong) | 190 |  3.7% |
| UNCLEAR                          |  12 |  8.3% |

**56.7%** of bybit_demo trades have DeepSeek's post-hoc verdict marking the system's direction as wrong. The correlation between this verdict and actual win-rate is extreme (74% vs 4%). The post-hoc-optimal direction is highly correlated with the actually-profitable direction.

This is a strong second signal: independent of any in-flight flip decision, the final direction the system traded was wrong on **the majority** of trades.

## 5. PRIMARY Baseline — Direction × Regime (bybit_demo, last 30d, from trade_intelligence)

| Direction | entry_regime    | N | wins | WR | avg pnl_pct | net pnl_usd |
|-----------|-----------------|----|------|----|-------------|-------------|
| Buy       | ranging         | 14  | 7  | 50.0% | +0.018% | $+42.94    |
| Buy       | trending_up     | 22  | 8  | 36.4% | +0.122% | $-19.34    |
| Sell      | ranging         | 148 | 41 | 27.7% | -0.028% | $-322.27   |
| Sell      | trending_down   |   5 |  1 | 20.0% | +0.025% | $+0.69     |
| Sell      | trending_up     | 139 | 47 | 33.8% | -0.019% | **$+502.61** |
| Sell      | volatile        |   7 |  2 | 28.6% | -0.056% | $-1.58     |

**Counter-intuitive but real**: Sell trades in `trending_up` regime are the BIGGEST net winner on bybit_demo (+$502). Sell in `ranging` is the BIGGEST loser (-$322). Buy in `ranging` is the highest-WR cohort (50%, n=14).

This is the texture the operator needs to weigh against the flip-policy question.

### 5.1 BTC regime distribution (last 7 days, from regime_history)

| Regime | N | % |
|--------|---|---|
| ranging       | 241 | 46.4% |
| dead          | 177 | 34.0% |
| trending_up   |  54 | 10.4% |
| trending_down |  42 |  8.1% |
| volatile      |  16 |  3.1% |

80% of regime observations are ranging/dead (regimes where APEX's pre-call direction lock is NOT applied — DeepSeek may flip freely). Only 18.5% trending-locked, 3.1% volatile-locked.

## 6. Issue 2 Baseline — SL Gateway

Today (~9 hours of logs):

| Tag                      | Count |
|--------------------------|-------|
| `SL_GATEWAY_ACCEPT`      | 160   |
| `SL_GATEWAY_REJECT`      | 164   |
| `TRAIL_HIT`              | **0** |

**Acceptance rate: 49.4%** — half of all submissions to the gateway are rejected.

Reject reasons (from current `workers.log`, 29 rejects sampled):
- `rsn=rate_limit`:    22 (75.9%)
- `rsn=step_exceeded`:  5 (17.2%)
- `rsn=too_close`:      1 (3.4%)
- `rsn=loosening`:      1 (3.4%)

TRAIL_HIT is zero across the entire ~9-hour window — confirming the spec's claim that the trail-SL peak-catcher never fires.

## 7. Issue 3 Baseline — mode4_stall_valve

Today (~9 hours of logs):
- `mode4_stall_valve` fires:        103
- `mode4_partial_fallback` fires:    28

Rate: ~11 fires/hour for stall_valve. Spec sampled 84 over 2 hours = 42/hour — current rate is lower. Either system load fell or recent fixes reduced the rate. **Investigation P.3.1 should re-sample.**

From trade_log close-reason counts (bybit_demo lifetime):
- `mode4_stall_valve` close: 7
- `mode4_partial`: 8
- `mode4_partial_fallback_full`: 4

Most stall_valve fires do not lead to a `close_reason=mode4_stall_valve` row — they may trigger partial closes or coordinator dedup.

## 8. Issue 4 Baseline — COORD_DOUBLE_CLOSE + writer overlap

Today (~9 hours):
- `COORD_DOUBLE_CLOSE`: 26 events

Three writers confirmed by Explore (per "Verified Code Anchors" above):
1. `profit_sniper.py:1566` (source=`profit_sniper_trail`)
2. `position_watchdog.py:954` (source ∈ {`wd_profit_take`, `wd_dl_action`, `wd_claude_action`})
3. `position_watchdog.py:1506` (source=`sentinel_deadline`)

All three contend for the same 30-second/symbol rate-limit token at `sl_gateway.py:463-475`.

## 9. Issue 5 Baseline — AGE_GUARD

Today (~9 hours):
- `TIME_DECAY_AGE_GUARD` (`time_decay_sl.py:344`): 414 events
- `SNIPER_AGE_GUARD` (`profit_sniper.py:2478`): 3,216 events

Both gates fire frequently — confirming the spec's "management dead zone for 5 minutes" characterization. SNIPER_AGE_GUARD fires ~7-8× more often than TIME_DECAY_AGE_GUARD, suggesting sniper checks more frequently.

## 10. Cascade-Fix Verification (prior fix series, per feedback memory)

| Check | Result | Status |
|-------|--------|--------|
| `DB_LOCK_WAIT` events today | 0 | OK — cascade fix holds |
| `trade_log` rows have non-null pnl_usd | 295/295 | OK schema-wise |
| `trade_log` rows with pnl_usd=0 (bybit_demo, all closed) | 117/295 (39.7%) | **CONCERN** |
| `positions` table rows | 0 | OK in stopped state (no open positions when services stopped) |
| `exchange_mode` column on `trade_log`, `orders`, `positions`, `account_snapshots`, `trade_history`, `trade_intelligence` | Present | OK — schema v30+v32 changes hold |

**Concern: pnl_usd=0 on closed trades.** 117/295 = 40% of bybit_demo closed trades have zero PnL despite having `exit_price` populated. Breakdown by close_reason:

| close_reason         | N | zero_pnl | %   |
|----------------------|---|----------|-----|
| `bybit_demo_sl_tp`   | 93 | 48 | 51.6% |
| `bybit_sl_hit`       | 76 | 28 | 36.8% |
| `emergency_manual`   | 28 |  6 | 21.4% |
| `bybit_tp_hit`       |  6 |  2 | 33.3% |
| strategic_review (multiple) | ~10 | ~6 | high |

This is most likely a downstream effect of how the bybit-demo close path captures fill data (the prior `project_p1_p10_bybit_demo_fixes` work touched related areas but P8's `backfill_p8_trade_log_exchange_mode.py` may not have addressed this). **Flag this finding to the operator** — it materially affects the PnL-by-direction baselines above. The pnl-aggregate columns include zeros, which understates true PnL magnitude (in absolute terms).

This is OUT OF SCOPE for the 5 issues but is a baseline anomaly the operator should be aware of when interpreting the win-rate / PnL numbers in this report.

## 11. DeepSeek/Qwen Sample (Phase 0 Step 0.4)

10 most recent `APEX_QWEN_OK` lines (from `data/logs/workers.log` and rotated peer). Format normalized:

```
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=3186 tokens_in=2894 tokens_out=228 cost_usd=0.001069 | did=d-1778521472349
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=2119 tokens_in=2722 tokens_out=286 cost_usd=0.001068 | did=d-1778521472349
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=1450 tokens_in=2756 tokens_out=300 cost_usd=0.001091 | did=d-1778522029410
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=798  tokens_in=2772 tokens_out=293 cost_usd=0.001089 | did=d-1778522029410
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=673  tokens_in=2681 tokens_out=311 cost_usd=0.001078 | did=d-1778522029410
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=2458 tokens_in=2843 tokens_out=262 cost_usd=0.001083 | did=d-1778522602130
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=679  tokens_in=2836 tokens_out=292 cost_usd=0.001108 | did=d-1778522602130
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=1926 tokens_in=2795 tokens_out=422 cost_usd=0.001210 | did=d-1778522602130
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=1118 tokens_in=2835 tokens_out=276 cost_usd=0.001093 | did=d-1778523103402
APEX_QWEN_OK | model=deepseek/deepseek-v3.2-20251201 latency_ms=1892 tokens_in=2840 tokens_out=248 cost_usd=0.001070 | did=d-1778523103402
```

Observations:
- Model: `deepseek/deepseek-v3.2-20251201` (config says `deepseek/deepseek-v3.2` — full versioned name comes back from the provider).
- Latency range: 673-3186 ms.
- Token-in ~2700-2900 consistently (the assembled prompt size is stable across calls).
- Token-out 228-422 (the response is small JSON).
- Cost ~$0.001/call.
- Multiple `APEX_QWEN_OK` lines share a `did=`, indicating retries or multi-stage calls per directive.

**Prompts and responses are NOT in plain text in current logs.** P.1.9 (sample DeepSeek responses) will require either (a) re-running APEX in test mode with explicit prompt+response dump, or (b) re-reading `qwen_client.py` to confirm that responses can be logged at DEBUG and configure that for a sample window. Operator decision may be needed at P.1.9 time.

## 12. Open Items For Operator (informational, not blocking)

1. **pnl_usd=0 on 40% of closed bybit_demo trades** is a measurement gap that distorts all PnL-by-direction figures above. Material to interpreting the strategic options at Phase 2. Recommend a separate ticket to investigate post-PRIMARY.
2. **bybit_demo only has 2.3 days of trade history** (since 2026-05-09 cutover). The 30-day window collapses to bybit_demo's full lifetime; the spec's "30 days" historical analysis is effectively constrained to "since cutover" on bybit_demo data. Shadow data covers a longer window and complements.
3. **XRAY drives more flips than APEX today** (19 vs 13). The spec frames the issue as primarily APEX-driven; this is incomplete. Both flip paths need scope coverage in PRIMARY Phase 1.

## 13. Phase 0 Verification Gate — Status: PASS

| Gate Item | Status |
|-----------|--------|
| Baseline metrics captured for PRIMARY (direction, regime, flipped vs unflipped, DeepSeek verdict) | DONE |
| Baseline metrics captured for Issues 2-5 | DONE |
| Spec line refs verified against current code | DONE (deltas noted) |
| Previous fixes confirmed intact (DB_LOCK, exchange_mode, schema versions) | DONE (pnl_usd=0 anomaly flagged separately) |
| DeepSeek/Qwen sample lines captured | PARTIAL (header lines captured; prompt/response bodies require live capture or DEBUG logging in P.1.9) |
| dev_notes/sell_bias_fixes/ directory created | DONE |

PRIMARY Phase 1 may proceed.
