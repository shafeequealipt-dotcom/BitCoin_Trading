# Stage 2 Live Monitoring — Observations & Findings

**Window:** 2026-05-05 06:10:38 – 06:38:00 UTC (≈27 minutes, 5 brain cycles captured)
**Trigger:** Operator request — observe each Stage 2 cycle in real time, full prompt + full Claude response, latency, and complete analysis.
**System under observation:** `trading-intelligence-mcp` brain, post `sudo systemctl restart trading-workers` at 06:10:38.
**Phase flags at start of window** (from `config.toml [stage2]`): `top_n_to_brain=6`, `enable_full_layer_block=true`, `enable_zero_two_contract=true`, `enable_priority_trim=true` — all on.

---

## 1. Method

1. Verified `claude_code_client.py` carries the sentinel-gated dumper (`_DUMP_DIR`, `_DUMP_SENTINEL`, `_maybe_dump_call`) at lines 75–103, 344, 426. Service was restarted at 06:10:38 — patched binary loaded.
2. Enabled dump capture: `touch data/stage2_dumps/.enabled` (06:12:00 UTC).
3. Started a persistent background tail of `data/logs/brain.log` filtered to Stage 2 events (`STRAT_CALL_A_*`, `STRATEGIST_PACKAGES_READ`, `STRAT_TOP_N_APPLIED`, `STRAT_PROMPT_SIZE`, `CLAUDE_PROMPT_TRIMMED`, `CLAUDE_CALL_*`, `STRAT_RICH_BLOCK_*`, `STRAT_ZERO_TRADES_INTENTIONAL`).
4. Each `CLAUDE_CALL_OK` triggered the dumper to write the full `system_prompt + prompt + response` to JSON in `data/stage2_dumps/`.
5. Stopped monitor and removed sentinel on operator request (≈06:38).

---

## 2. Five cycles at a glance

| # | did                | start    | el (s) | prompt B | system B | resp B | sections | packages | trades | risk     |
|---|--------------------|----------|--------|----------|----------|--------|----------|----------|--------|----------|
| 1 | d-1777961453096    | 06:10:53 | 132.5  | 3,693    | 7,287    | 1,027  | 29       | **0**    | 0      | cautious |
| 2 | d-1777961890160    | 06:18:10 | 69.6   | 6,592    | 7,287    | 1,383  | 43       | **0**    | 0      | cautious |
| 3 | d-1777962261719    | 06:24:21 | 82.7   | 13,724   | 7,287    | 682    | 47       | 15→6     | 0      | cautious |
| 4 | d-1777962645482    | 06:30:45 | **24.5** | 13,770 | 7,287    | 656    | 40       | 15→6     | 0      | cautious |
| 5 | d-1777962971724    | 06:36:11 | 85.6   | 13,692   | 7,287    | 733    | 38       | 15→6     | 0      | cautious |

- `el` = `CLAUDE_CALL_OK` elapsed (Claude wall time, includes subprocess spawn, auth, Claude inference, parse).
- `system B` is constant — system prompt is built once per worker boot.
- All 5 cycles emitted `STRAT_ZERO_TRADES_INTENTIONAL contract=0_2` (Phase 3 contract). System is paper-trading and Claude judged conditions never met the STRONG bar.

### Latency stats (5 calls)

- min 24.5s · max 132.5s · mean **78.0s** · median 83s
- Cycle 1 (cold-start) was outlier high; cycle 4 was outlier low (likely Claude side-channel cache hit on near-identical 13.7K prompt).
- 60s stall warnings fired on cycles 2, 3, 5 (`CLAUDE_PROC_STALL_60S` — informational; 240s remaining on the 300s timeout).
- Subprocess spawn 23–37 ms across all cycles. Healthy.

---

## 3. Phase verification

| Phase | Flag | Verified? | Evidence |
|-------|------|-----------|----------|
| 1 — top-N=6 cap | (always-on) | ✅ | Cycles 3,4,5: `STRAT_TOP_N_APPLIED input=15 cap=6 output=6 pinned=0` |
| 2 — full Layer 1B/1C rich block | `enable_full_layer_block` | ✅ | Cycles 3,4,5 prompts contain `## TRADE CANDIDATES (full Layer 1B/1C evidence)` with 6 coins × 7 sub-blocks (XRAY 12 phases, structure, SMC, MTF, regime full, **TradeScorer 4-component breakdown**, votes top-3, levels, action hint) |
| 3 — 0-2 contract | `enable_zero_two_contract` | ✅ | System prompt 7,287 B carries STRICT 0-2 + STRONG criteria; all 5 cycles emitted `STRAT_ZERO_TRADES_INTENTIONAL contract=0_2` |
| 4 — priority trim | `enable_priority_trim` | ⚪ dormant | All post-restart prompts <14k chars → trim path not exercised this window. Verified working in earlier cycles today (05:41, 05:47): `mode=priority dropped_optional=3 dropped_important=0` |

---

## 4. Anomalies & findings

### 🔴 Finding 1 — TradeScorer is unstable cycle-to-cycle (HIGH severity)

ALICEUSDT, three consecutive 5-minute cycles, **identical** structural inputs:

| Component       | Cycle 3 (06:24)   | Cycle 4 (06:30)   | Cycle 5 (06:36)   | Δ (worst)  |
|-----------------|--------------------|--------------------|--------------------|-----------|
| Base /40        | 31.0               | 31.0               | 37.0               | +6        |
| Confluence /25  | 20.0               | 15.0               | 20.0               | -5 / +5   |
| Context /20     | 13.0               | **3.0**            | 11.0               | **-10 / +8** |
| Quality /20     | 13.4               | 10.4               | 13.4               | -3 / +3   |
| **Total**       | **77.4 (A)**       | **59.4 (B)**       | **81.4 (A+)**      | **-18 / +22** |

Inputs that did NOT change across the 3 cycles:

- XRAY: `setup=bullish_fvg_ob conf=0.55 dir=long score=100 quality=A+`
- Structure: `market_structure=uptrend range_pos=0.85 smc_confluence=55`
- MTF: `quality=maximum score=8 factors=7`
- Regime: `ranging conf=0.40 ADX=10.7 atr_pct=81 chop=48 vol_ratio=0.24`
- Levels: SL/TP/RR `$0.1456 / $0.1521 / 4.01`

What did move (in the noise floor):

- Signal `conf` 0.32 → 0.24 → 0.38
- Vote weighted BUY 4.73 → 4.27 → 4.31
- Top voter conf B2_supertrend 0.88 → 0.87 → 0.91

**Conclusion:** The Context sub-component (and to a lesser extent Confluence) is over-sensitive to micro-changes in signal/vote inputs. ALICEUSDT crosses the STRONG bar (TradeScorer ≥ 70) in cycles 3 and 5 but fails it by a wide margin in cycle 4 — purely on scoring noise, not on any change a trader would consider meaningful. Real trade decisions could flip on this if XRAY conf and regime conf weren't independently gating the STRONG bar.

**Action:** Audit `src/strategies/scorer.py` Context computation. Find which sub-input drives the 10-point swing on ALICEUSDT 06:30 vs 06:24/06:36. Likely a divisor / lookup-failure path that returns a low default.

---

### 🔴 Finding 2 — Cold-start scanner gate skips one M5 boundary (HIGH severity)

Worker manager restarted at **06:10:38**. `scanner_worker` started at **06:10:51** with `interval=300s, sweet_spot=4:00`. The next briefing build was expected at **06:14:00** (next M5 + 4:00). Actual first post-restart briefing build: **06:19:00** — **the 06:14:00 boundary was skipped entirely**.

Consequence: brain ticks at 06:10:53 (cycle 1) and 06:18:10 (cycle 2) both saw `STRATEGIST_PACKAGES_READ count=0 reader=brain_call_a`. They ran on a **stripped prompt** (3,693 B and 6,592 B respectively) with **no `## TRADE CANDIDATES` block** — Claude had no XRAY 12 phases, no per-coin scorer, no votes. Cycle 1 was even missing per-coin regime tags in the market data block.

Effective post-restart degradation window: **~9 minutes** during which Stage 2 ran on raw TA only.

The cold-start brain gate (per memory `project_cold_start_resume_fix.md`) prevents brain from acting on cycle_gated ticks until M5 boundary. The complementary scanner-side gate is what's letting the M5+4:00 briefing slot lapse. Investigate `scanner_worker._tick_briefing_mode` start path and whether `cycle_gated` is suppressing the first eligible tick.

---

### 🟡 Finding 3 — `FUND RULES` mis-classified by Phase 4 priority trim (MEDIUM severity)

`src/brain/strategist.py:354-361` — `_TRIM_OPTIONAL_MARKERS` and `_TRIM_ESSENTIAL_MARKERS` lists. The section that begins `FUND RULES (non-negotiable):` matches **none** of the markers, so `_infer_section_priority` returns `_TRIM_PRIORITY_OPTIONAL` (default fallback at line 386).

`FUND RULES` carries:

```
Total equity: $6,008
Starting equity: $168,000
Tier: 1 — CONSERVATIVE (unproven)
Capital allocation: 30% of equity
Usable capital: $1,802
Max single trade: $451
Max positions: 6
Size your trades within available capital.
```

Earlier today's trim events at 05:41 and 05:47 (prompts ≥14,097 B) dropped this exact section as part of `dropped_optional=3, dropped_labels=['Trades today: 0', 'Daily PnL: +0.00%', 'FUND RULES (non-negotiable):']`. Without FUND RULES Claude has no sizing constraint — it could request `size_usd=$5,000` in the response despite the fund manager only allowing `$451`.

**Action:** Add `"FUND RULES"` (or the exact `"FUND RULES (non-negotiable):"` prefix) to `_TRIM_ESSENTIAL_MARKERS` (`src/brain/strategist.py:327-339`). Three-line change. Same fix for `"## TODAY'S PERFORMANCE"` if the per-line splits are intended to be ESSENTIAL/IMPORTANT rather than OPTIONAL.

---

### 🟡 Finding 4 — Short-side coins never score (MEDIUM severity)

In cycles 3, 4, 5 the rich block consistently shows `DOGEUSDT` and `INJUSDT` as `RANGE_FADE_SHORT` setups (XRAY `bearish_fvg_ob`, `direction=short`, structure `downtrend`). Their scorer block reads:

```
Strategies: 0 fired, ensemble NONE, total_score 0.0
```

while LONG-side candidates (ALICE, FIL, HYPE, BLUR) on the same cycles report `38 fired, ensemble STRONG, total_score 68.4–81.4`.

The candidates are present in the briefing → strategy_worker computed scoring for long candidates → but didn't compute scoring for these short setups. This effectively excludes shorts from the STRONG bar consideration (which requires `TradeScorer ≥ 70`). Claude noted this in cycle 5 response: *"DOGEUSDT and INJUSDT have zero strategy votes (ensemble NONE)"*.

**Action:** Confirm whether `StrategyRegistry` is filtering on direction/regime, or whether `LayerManager._scorer_components` writer is short-blind. Read `src/workers/strategy_worker.py:tick` and the symbols it iterates.

---

### 🟡 Finding 5 — Vote-count vs ensemble-label mismatch (LOW-MEDIUM severity)

Long candidates show `Strategies: 38 fired, ensemble STRONG, total_score 77.4` but the votes block shows `BUY=4.73 vs SELL=0.00 (38 voters)` — a weighted total of 4.73 over 38 voters means an average voter conf of **~0.12**, with only the top-3 voters above conf 0.5. The "STRONG ensemble" label is being driven by total_score (computed elsewhere) rather than by voter conviction distribution. Claude's STRONG bar rule is `dominant side has >= 3 voters with confidence >= 0.65 AND opposing side has zero voters above 0.5` — it can satisfy the rule from the top-3 even when the broad consensus is thin.

**Action:** Surface a `voters_above_0.5` count alongside `38 fired` in the rich block so Claude can apply its rule deterministically. One-line addition in `_format_packages_for_prompt_full`.

---

### 🟢 Finding 6 — 0-2 contract behaving correctly (positive observation)

Cycle 3: ALICEUSDT had TradeScorer 77.4 (≥70 ✓), clean vote winner (top-3 conf 0.88/0.85/0.70 ✓), F&G 50 neutral (no override). But XRAY conf 0.55 < 0.7 AND regime conf 0.40 < 0.6 → both gates failed → Claude returned `[]`. Cycle 5 same logic, even with score 81.4. Claude is honoring the STRONG criteria rather than padding to fill quota — exactly the behavior Phase 3 was designed for.

Response chars correlate inversely with prompt richness: 1027/1383 chars when explaining absent data (cycles 1,2) → 682/656/733 chars when citing concrete scores (cycles 3,4,5). Claude is more terse with better data — sign the contract works.

---

### 🟢 Finding 7 — Per-coin regime tags healthy

`## MARKET DATA` lines correctly carry tags like `[TRENDING_DOWN 78%]`, `[RANGING 40%]`, `[VOLATILE 90%]`, `[TRENDING_UP 53%]` from cycle 2 onwards. Layer 1A regime emit working post-restart.

---

### 🟢 Finding 8 — XRAY structural setups always present

`## X-RAY STRUCTURAL SETUPS (ranked by confluence)` block populates from cycle 2 onwards (cycle 1 was too cold). 8 coins per cycle with full structure/SMC/POC/FIB/MTF/CONFL/RR/setup metrics. This is independent of the briefing-package pipeline (reads from `services.get("structure_engine")`), which is why it survives the 06:10–06:19 cold-start gap.

---

## 5. Cycle 3 representative dump (full annotated)

The cycle 3 dump (`20260505T062545_call0003_d-1777962261719.json`) is the canonical record of a healthy full-rich Stage 2 call. Key sections in the user prompt:

1. **PERFORMANCE COACH** — daily session stats (trades / wins / losses / win rate / PnL / streak / per-side win rates).
2. **REGIME-SPECIFIC TRADING INSTRUCTIONS** — global regime + F&G + per-coin regime override doctrine.
3. **GLOBAL REGIME GUIDANCE** — RANGE PLAY playbook (3-5 trades, mean-reversion, BUY support / SELL resistance, tight stops).
4. **MODE: MAINNET** — real-money safeguards.
5. **TRADEABLE COINS THIS CYCLE (15 coins)** — universe pin.
6. **## TRADE CANDIDATES (full Layer 1B/1C evidence)** — 6 coins × 7 sub-blocks each. ~1.4–1.8K chars per coin.
7. **## MARKET DATA** — TA snapshot for non-package coins (ranged, ATR, RSI, MACD, ADX, % change).
8. **## SESSION** — Asian late, time remaining, next session.
9. **## X-RAY STRUCTURAL SETUPS** — universe-wide structural ranking.
10. **## SENTIMENT** — F&G index.
11. **## MARKET REGIME** — global direction summary.
12. **## STRATEGY HINTS** — 12 top per-strategy picks + per-coin consensus tally.
13. **## ACCOUNT** + **FUND RULES** — equity, capital allocation, sizing limits.
14. **## TODAY'S PERFORMANCE** — daily PnL & trade count.

System prompt (constant 7,287 B) carries:

- Disciplined-trader role + paper-trading framing.
- TRADE COUNT — STRICT 0-2 CONTRACT (allowed range 0/1/2; ZERO is valid; THREE is hard violation).
- STRONG conviction gate: TradeScorer ≥70 AND clear vote winner AND XRAY conf ≥0.7 AND regime conf ≥0.6 AND RR setup good/excellent.
- WHEN TO RETURN ZERO TRADES — explicit skip conditions.
- DIRECTION BY REGIME (per-coin override).
- FEAR & GREED contrarian thresholds.
- Per-trade required fields (symbol, direction, SL, TP, hold, leverage, size, trailing, reasoning citing specific block).
- POSITION GATE — no new trades on `[POS]` coins.
- Briefing-mode field reference (interestingness scoring, state labels, votes block, action hint).

Claude's response was a tight 682-byte JSON with `new_trades:[]` plus `market_view`, `risk_level=cautious`, defaults, `focus_coins=[ALICEUSDT,INJUSDT]`, `avoid_coins=[BLURUSDT,HYPEUSDT]`. The view explicitly cited `score 77.4`, `XRAY 0.55 (needs >=0.7)`, `regime 0.40 (needs >=0.6)`, `FIL 68.8`, `BLUR 46.1` — proof Claude is reading the rich block.

---

## 6. Suggested follow-ups (priority order)

1. **Fix `_TRIM_ESSENTIAL_MARKERS`** to keep `FUND RULES` from being trimmed (3-line edit, `src/brain/strategist.py:327-339`).
2. **Audit TradeScorer Context sub-component** — find why ALICEUSDT swung 13→3→11 with no input change. Likely a stale-cache or default-on-missing path in `src/strategies/scorer.py`.
3. **Investigate scanner_worker cold-start gate** — figure out why the 06:14:00 briefing slot was skipped post-restart. Read `src/workers/scanner_worker.py:_tick_briefing_mode` startup path.
4. **Investigate short-side scoring** — why DOGE/INJ never get `STRAT_SCORER_COMPONENTS_WRITE` entries.
5. **Add `voters_above_0.5` count to rich-block votes line** so Claude can deterministically apply its STRONG vote-winner rule.

---

## 7. Files in this directory

| File | What it is |
|------|------------|
| `REPORT.md` | This document |
| `brain_log_slice.log` | Full `data/logs/brain.log` slice from 06:10:38 onwards (covers 5 cycles + restart events) |
| `workers_log_slice.log` | Filtered `data/logs/workers.log` slice (scanner ticks, package builds, scorer writes, WM_START) |
| `20260505T061310_call0001_*.json` | Cycle 1 dump — cold-start, packages=0, 132.5s, 1027-byte response |
| `20260505T061921_call0002_*.json` | Cycle 2 dump — gate-skip, packages=0, 69.6s, 1383-byte response (X-RAY visible, no TRADE CANDIDATES) |
| `20260505T062545_call0003_*.json` | Cycle 3 dump — first full rich, packages=15→6, 82.7s, 682-byte response (canonical good cycle) |
| `20260505T063111_call0004_*.json` | Cycle 4 dump — full rich, 24.5s (fastest), 656-byte response (ALICE score crashed to 59.4) |
| `20260505T063738_call0005_*.json` | Cycle 5 dump — full rich, 85.6s, 733-byte response (ALICE score recovered to 81.4) |

Each JSON dump contains `system_prompt` + `prompt` + `response` + `elapsed_ms` + `prompt_hash` + size metadata. Open with any JSON viewer.

---

## 8. Operator runbook for re-enabling

```
# Re-enable dump capture (no restart needed):
touch /home/inshadaliqbal786/trading-intelligence-mcp/data/stage2_dumps/.enabled

# Watch live:
.venv/bin/python /home/inshadaliqbal786/trading-intelligence-mcp/scripts/monitor_stage2_live.py

# Disable dump capture:
rm /home/inshadaliqbal786/trading-intelligence-mcp/data/stage2_dumps/.enabled
```

The dumper is sentinel-gated — when the file is absent, the dump path exits in one `stat()` call. Zero overhead when off.

---

*Generated 2026-05-05 ~06:40 UTC. Live monitor (`bz7idcfoz`) was stopped and the sentinel was removed at the operator's request.*
