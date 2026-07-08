# Phase 0 Baseline — Brain Prompt Enrichment

Date: 2026-05-16
Branch: `fix/brain-prompt-enrichment` (created from `main`)
Parent commit on main: `36e51aa docs(strategist/phase-2): correct enable_prompt_compression docstring + saving estimate`

## Goal

Capture starting state and baseline metrics before any investigation modifies the system. Confirm or refute the implementation document's audit findings against current code and current live data.

## Working Tree And Branch State

- Pre-existing branch `fix/j1-orphan-positions` had three modified tracked files at session start:
  - `data/layer_state.json`
  - `data/logs/layer1c_full.jsonl`
  - `systemd/trading-workers.service`
  - These were stashed with message `j-series uncommitted state — pre brain-prompt-enrichment branch (2026-05-16)`. Recoverable via `git stash list | grep j-series`.
- Many untracked files in `dev_notes/` and `scripts/` from prior j-series investigations remain untracked on the new branch; they do not affect this work.
- Branched from `main` HEAD (commit `36e51aa`), not from `fix/j1-orphan-positions`. Per the operator's choice.

## Dependency Confirmation

### B1a Regime Detector Calibration — NOT ON MAIN

The implementation document asserts B1a is merged ("commit `6938c69` is verified working, do not touch regime.py"). Verification against current code shows:

- `config.toml` line 811-815 has the **OLD pre-B1a values**: `trending_adx_threshold=25`, `ranging_choppiness_threshold=60`, `dead_adx_threshold=15`.
- The B1a calibrated values (20 / 50 / 12 per `feedback_overhaul29_execution.md` and prior memory) are on branch `fix/regime-detector-b1a-2026-05-12` (commits 266c5a6 / dea18d8 / 3433010).
- `git merge-base --is-ancestor dea18d8 HEAD` returns NOT REACHABLE from `main`.
- `git merge-base --is-ancestor dea18d8 fix/j1-orphan-positions` returns REACHABLE — so the current running production has B1a (because it was started on j-series).

**Implication for this fix:** The `fix/brain-prompt-enrichment` branch sits on top of `main` and therefore does NOT contain B1a. The running production process (PID 424, started 03:41 on 2026-05-16 from `fix/j1-orphan-positions`) DOES contain B1a. When the brain-prompt-enrichment branch is eventually merged, the operator must either merge B1a first or rebase enrichment onto a base that includes B1a. **Flagged for operator awareness — not a blocker for investigation.**

Per the doc's Part C Rule, this fix does NOT modify `regime.py` or `apex/optimizer.py` regardless.

### Running System

- `workers.py` PID 424 — running since 03:41 today, 3 min CPU, 350 MB resident
- `server.py --transport sse --port 8080` PID 425 — running since 03:41
- Log tails active (PID 1743, 2364)
- `data/stage2_dumps/.enabled` present — sentinel-gated CALL_A/CALL_B dumper IS ACTIVE
- 884 cumulative dump files in `data/stage2_dumps/`

### DB Concurrency

Confirmed `data/trading.db` (not the root-level 4KB stub) is the active DB. SQLite WAL files present. No "cascades" log evidence in current workers.log. Continued monitoring required during investigation.

## Fresh Dump Capture

Two latest dumps copied to `dev_notes/brain_enrichment/phase0_fresh_dump/`:

| File | Original | Type | Size | Prompt chars | Sys prompt chars | Elapsed ms | Response chars |
|---|---|---|---|---|---|---|---|
| CALL_A_fresh.json | `20260516T035601_call0001_d-1778903555685.json` | CALL_A | 27 KB | 16,831 | 6,724 | 205,534 | 2,735 |
| CALL_B_fresh.json | `20260516T035952_call0002_d-1778903911810.json` | CALL_B | 5 KB | 2,009 | 1,783 | 80,415 | 861 |

Both fresh dumps confirm the document's anatomy:

- CALL_A opens with `Global regime: ranging (confidence=40%, Fear & Greed=31)`, lists 15 tradeable coins, renders 15 trade candidate blocks with XRAY + Structure + indicators, market data, sentiment, regime guidance, account state, strategy hints + consensus per coin (top BUY/SELL voters by symbol), then account section.
- CALL_B opens with `## MARKET REGIME: ranging (40%)`, `## SENTIMENT: Fear & Greed = 31`, **`## TODAY: PnL=+0.00%`** (this session's fresh dump still shows zero), then position management contract, per-position blocks, recent performance footer.

## Baseline Metrics

### Universal Confirmation: TODAY: PnL=+0.00% In Every Single Captured Dump

Across all 884 stage2 dumps spanning weeks of trading sessions and many regimes, **every single dump's CALL_B prompt shows `## TODAY: PnL=+0.00%`**. No exceptions. No non-zero values ever observed. This includes sessions that closed multiple trades within the same day before the dump was captured. The document's Finding 4 ("CALL_B shows 'TODAY: PnL=+0.00%' in every cycle") is universally true — not a snapshot quirk.

This makes Target 5 the single most well-defined fix in scope.

### CALL_A Prompt Metrics (n = 240 STRAT_CALL_A_END with non-zero prompt_chars, from current `brain.log` rotation)

- prompt_chars: min=0 (cancelled cycles), max=21,622, p50=7,244, p90=19,890
- Note: the p50 of 7,244 is misleading — it tracks the system_prompt_chars value when status=cancelled emits prompt_chars=0; cycles that produced a real CALL_A typically render 14,000-22,000 user-prompt chars per the dump-side observation.
- Fresh dump: 16,831 user prompt + 6,724 sys prompt = 23,555 total chars sent (under the 30 K cap)

### CALL_A Latency (n = 1027 STRAT_CALL_A_END events)

- min=0 ms (immediate cancel)
- p50=136,069 ms (≈ 2.3 minutes)
- p90=244,358 ms (≈ 4.1 minutes)
- max=6,691,557 ms (≈ 1 hour 52 min — captured Claude-CLI stall event, the J4 deferred fix)

### CALL_A Status Distribution (last rotation)

| Status | Count |
|---|---|
| (no status / parse skip) | 907 |
| success | 99 |
| skipped | 14 |
| cancelled | 7 |
| Total STRAT_CALL_A_END | 1027 |

### CALL_B Prompt Metrics (n = 644)

- prompt_chars: min=554, max=5,130, p50=2,345, p90=3,650
- Fresh dump: 2,009 user prompt + 1,783 sys prompt = 3,792 total chars
- All 6 STRAT_CALL_B_END events with failed=Y are from 2026-04-19 (pre-fix). Current run is healthy.

### Brain Rejection Rate (61 cycles across rotation)

| Metric | Value |
|---|---|
| Cycles with brain proposals | 61 |
| Trades proposed | 151 |
| Trades executed | 140 |
| Trades rejected | 11 |
| **Rejection rate** | **7.3%** |

**Major delta from document.** The implementation doc says "the gate rejects 130 of 175 brain proposals (74% rejection)". Current rejection rate is 7.3%, not 74%. Skip reasons in the rotation:

- `survival_block`: 7 (most common)
- `gate_rejected`: 3
- `order_reject`: 1

Interpretation: either (a) the doc's 74% was measured against a different, broader rejection set (pre-execution gate + scanner filter + briefing filter), (b) the j-series fixes already in production reduced rejection drastically (j6 re-entry learning gate, j7 direction-aware SL), or (c) the doc's measurement window covered a high-rejection period that has passed. Whichever the cause, the **success metric "rejection rate drops from 74% to 40%" prescribed in the doc's Part H is moot** — the system is already at 7.3%. The Phase 4 verification metrics for the enrichment must be re-anchored to **`wd_claude_action` loss frequency, prompt-size-budget compliance, brain reasoning citations to new fields, and direction-aligned trade quality** rather than rejection rate alone.

### Close Reason Breakdown (last 50 closes from `trade_history`)

| Reason | Count | Wins | Win % | Net PnL ($) |
|---|---|---|---|---|
| `wd_dl_action` (drawdown action) | 13 | 11 | 85% | +114.91 |
| `system_close` | 11 | 6 | 55% | -0.19 |
| **`wd_claude_action` (brain-forced)** | **10** | **1** | **10%** | **-144.34** |
| `bybit_sl_hit` | 10 | 4 | 40% | +60.38 |
| `wd_timeout` | 3 | 0 | 0% | -37.16 |
| `wd_profit_take` | 2 | 2 | 100% | +144.38 |
| `wd_trail` | 1 | 1 | 100% | +6.81 |

**Confirms document's Finding C:** `wd_claude_action` is by far the worst close category — 10% WR, -$144.34 net. Brain's force-close decisions are the dominant loss driver. The document's claim "6 of 16 losses were wd_claude_action totaling $94" is in the same regime; current window shows 10/25 losses are wd_claude with $144 of damage. Enrichment that improves brain's hold-vs-close judgment in CALL_B (E4 today_pnl, E5 dir_perf, E6 lesson context) has direct leverage on this category.

### Direction Win Rate (last 50 closes)

| Side | Count | Wins | WR | Avg pnl_pct | Net PnL ($) |
|---|---|---|---|---|---|
| Buy | 13 | 7 | 54% | 0.141% | +78.24 |
| Sell | 37 | 18 | 49% | 0.0199% | +66.55 |

Total: 25W/25L = 50% WR, +$144.79 net. Sell trades outnumber Buy 3:1 (37 vs 13) — consistent with the trending_down regime preference visible in CALL_A dumps. Buy direction has higher avg PnL per trade despite lower volume. The dir_perf enrichment (E5) would surface this asymmetry to the brain.

### Today's Closes (2026-05-16, partial day)

3 closes, all `system_close`, all profitable:
- ORCAUSDT Buy +$4.85 (+0.116%)
- KATUSDT Sell +$11.43 (+0.229%)
- ETHUSDT Sell +$0.27 (+0.008%)

Total today: +$16.56 realized.

### TIAS Lessons Pipeline State

`trade_intelligence` table: **1,755 total rows, 1,755 with `ds_lessons IS NULL`** — Phase 2 (DeepSeek analyzer) has never produced a single analyzed lesson. Confirms the document's Finding 6 and validates operator's choice to scope TIAS Phase 2 wire-in as commit `prompt-enrich/p3-0`.

## Audit Findings Verification

| Doc Finding | Status After Phase 0 Check |
|---|---|
| 1. CALL_A is well-designed, 14,989-17,974 chars, 40 sections | Confirmed. Fresh dump 16,831 chars, all sections present. |
| 2. 38-strategy per-coin vote breakdown not in prompt for non-top-3 coins | Partly refuted by Plan-mode recon: `strategist.py:1958-2000` already calls `layer_manager.get_strategy_votes(symbol)` and renders top-3 BUY/SELL voters. Gated by `[brain].surface_briefing_fields`. Phase 1.1 will confirm scope and config state. |
| 3. K-class meta-strategies all at conf=0.00 | Partly refuted. K1/K3/K4 are intentional non-voters by code design; only K2 is broken (missing `altdata["pattern_matches"]` data). To be definitively confirmed in Phase 1.3. |
| 4. CALL_B shows TODAY: PnL=+0.00% in every cycle | **Confirmed universally** — across 884 dumps, no non-zero value ever observed. Highest-confidence root-cause investigation target. |
| 5. News articles not in prompt | Confirmed. 59 articles ingested in last 24h, zero references in `strategist.py`. Doc-deferred enrichment (E8). |
| 6. TIAS lessons not in prompt | Confirmed. 1,755 rows captured, all NULL `ds_lessons`. |
| 7. Strategy category breakdown gap | Confirmed. Categories visible but not vote-split. Phase 1.8 designs the format. |
| 8. Vote opposition not shown | Confirmed. Phase 1.8 designs the format. |
| 9. RECENT_LOSER_COOLDOWN reaches brain | Confirmed. Already in state-labels block. Not a gap. |
| 10. Prompt is honest about uncertainty | Confirmed. Cited in CALL_A. |

**New finding (not in doc):** Brain rejection rate is 7.3%, not 74%. The doc's Part H success metric "rejection rate drops" is invalid and must be re-anchored.

## Working Directory Setup

```
dev_notes/brain_enrichment/
├── phase0_baseline.md (this file)
├── phase0_fresh_dump/
│   ├── CALL_A_fresh.json
│   └── CALL_B_fresh.json
├── 01_call_a_anatomy.md (to be written)
├── 02_call_b_anatomy.md (to be written)
├── 03_layer1_data_production.md (to be written)
├── 04_tias_lessons_pipeline.md (to be written)
├── 05_today_pnl_audit.md (to be written)
├── 06_direction_performance_audit.md (to be written)
├── 07_news_pipeline_audit.md (to be written)
├── 08_vote_opposition_audit.md (to be written)
├── 09_information_map.md (to be written)
└── MASTER_INVESTIGATION_REPORT.md (to be written in Phase 2)
```

## Phase 0 Verdict

Pre-flight verification PASSES with two flags for operator awareness:

1. **B1a regime calibration is on the j-series branch but NOT on `main`.** This enrichment branch therefore doesn't carry the B1a fix. The running production process does. Eventual merge ordering needs operator coordination.
2. **Brain rejection rate is already 7.3%, not 74%.** The doc's Part H success metric "rejection rate drops" is invalid. Phase 4 verification must use loss-category metrics, not rejection rate alone.

Investigation Phase 1 can begin.
