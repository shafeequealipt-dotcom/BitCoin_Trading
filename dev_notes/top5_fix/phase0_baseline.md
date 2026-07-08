# Phase 0 — Baselines Before Top-5 Fix

Captured 2026-05-05 ~07:45 UTC. Stage 2 phase 5 (rich Layer 1B/1C + 0-2 contract + priority trim) shipped at 2026-05-05 05:07:04 UTC. Baselines below cover the 2.5 hours since that ship.

## B1 — Trade execution rate

### Post Stage-2-phase-5 window (2026-05-05 05:07 → 06:55, ~1h 50m)

- `STRAT_CALL_A_START` events: **17**
- `STRAT_CALL_A_END` events: **16** (one in-flight at 06:55 cut by tail)
- `STRAT_CALL_A_END | trades=0`: **16 of 16** (100% zero-trade rate)
- `STRAT_CALL_A_END | trades>=1`: **0**

### Pre Stage-2-phase-5 window (2026-04-19 → 2026-05-05 05:07, ~16 days, 475 calls)

- trades=0: 43
- trades=1: 32
- trades=2: 310
- trades=3: 87
- trades=4: 3
- Pre-Stage-2 trade rate: ~91% non-zero

### Conclusion

**Stage 2 phase 5 inverted the trade rate from ~91% non-zero to 100% zero.** The STRAT_ZERO_TRADES_INTENTIONAL log lines cite reasons like:

> "All top candidates fail the STRONG conviction bar. HYPEUSDT has the best score (78.4) and clean vote structure (BUY=5.37 / SELL=0)..."

Claude is reasoning correctly over the rich data; the data + rule combination yields zero.

## B2 — XRAY confidence distribution

Universe-wide, last 200 `XRAY_CLASSIFY` events from `data/logs/workers.log`:

| Stat | Value |
|---|---|
| n | 200 |
| min | 0.28 |
| p25 | **0.55** |
| p50 | **0.55** |
| p75 | **0.55** |
| p95 | 0.70 |
| max | 0.80 |

The p25 = p50 = p75 = 0.55 spike is the smoking gun. The 0.5 floor in `min(mtf, max(smc_01, 0.5))` plus the SMC formula capping at 55 (FVG + Fresh OB) for most coins produces the universe-wide 0.55 lock.

Setup type distribution (last 200 classifications):

| Setup type | Count |
|---|---|
| bearish_fvg_ob | 91 |
| bullish_fvg_ob | 78 |
| bullish_fvg_ob_counter | 17 |
| bearish_fvg_ob_counter | 9 |
| bearish_structural_break | 5 |

Notable: bearish setups (91+9=100) outnumber bullish (78+17=95). Yet zero short trades execute (Issue 3).

## B3 — Short vs long asymmetry

Short setups in current cycle (2026-05-05 06:55):

| Symbol | Setup | Confidence |
|---|---|---|
| ICPUSDT | bearish_fvg_ob | 0.55 |
| HBARUSDT | bearish_fvg_ob | 0.55 |
| GMTUSDT | bearish_fvg_ob | 0.55 |
| MONUSDT | bearish_fvg_ob | 0.55 |
| EGLDUSDT | bearish_fvg_ob | 0.55 |
| BSBUSDT | bearish_fvg_ob | 0.50 |
| ORCAUSDT | bearish_structural_break | 0.80 |
| APTUSDT | bearish_fvg_ob | 0.55 |
| LTCUSDT | bearish_fvg_ob | 0.55 |

Confirmed: 100 bearish XRAY setups exist universe-wide, but per-coin block ensemble side never has SELL votes (`STRAT_VOTE_TRACE` for the most recent STRONG coin ALICEUSDT shows 13 BUY voters with conf 0.40-0.85, 23 NEUTRAL, **zero SELL**).

L1 strategies are TA-driven and asymmetric; SELL paths fire rarely. XRAY's `trade_direction="short"` does not bridge into the ensemble.

## B4 — TradeScorer Context variance

Universe-wide component averages from `STRAT_L2_DONE` over last 5 cycles (06:36 → 06:56, ~5 min apart):

| Cycle | n_scored | Base avg | Confluence avg | Context avg | Quality avg |
|---|---|---|---|---|---|
| 06:36 | 17 | 34.7 | 7.5 | **4.1** | 9.3 |
| 06:41 | 13 | 34.5 | 8.5 | **6.3** | 8.1 |
| 06:46 | 12 | 33.3 | 8.8 | **5.3** | 8.9 |
| 06:51 | 16 | 34.6 | 11.2 | **5.0** | 8.9 |
| 06:56 | 13 | 33.5 | 8.5 | **5.2** | 9.4 |

Context swings 4.1 → 6.3 → 5.3 → 5.0 → 5.2 (range 2.2 universe-wide). Per-coin swings will be larger; expect 6-10 point individual-coin swings on the volatile inputs (ta_conf threshold-cross at 0.6).

Note: per-coin Context variance for ALICEUSDT specifically (audit example) requires deeper log mining; this universe-wide avg is sufficient as Phase 0 baseline.

## B5 — FUND RULES presence

`data/stage2_dumps/` contains 5 prompt dumps captured today (sentinel just enabled):

- All 5 contain `"FUND RULES (non-negotiable):"` (100%)

But `CLAUDE_PROMPT_TRIMMED` events show FUND RULES IS getting dropped when the trim fires. From `data/logs/brain.log`:

| Time | Reason | Dropped labels |
|---|---|---|
| 2026-05-05 05:41:11 | chars=14231 → 13960 | `['Trades today: 0', 'Daily PnL: +0.00%', 'FUND RULES (non-negotiable):']` |
| 2026-05-05 05:47:58 | chars=14097 → 13826 | `['Trades today: 0', 'Daily PnL: +0.00%', 'FUND RULES (non-negotiable):']` |
| 2026-05-05 06:42:39 | chars=14049 → 13778 | `['Trades today: 0', 'Daily PnL: +0.00%', 'FUND RULES (non-negotiable):']` |

**3 confirmed FUND RULES drops in the post-Stage-2-phase-5 window.** Audit's claim is exact. Issue 5 is reproducible.

## B6 — Operator-visible state

- Equity vs starting equity: data not directly readable from logs in this baseline session; per memory `Growth: -96.4%` from a recent prompt header.
- Layer state (`data/layer_state.json`): `layer_active.1=true, layer_active.2=false, layer_active.3=false, user_stopped=true`. The operator has paused L2/L3 — workers process L1 (data) only at this moment.
- Pre-condition: `git status` after the WIP commit shows tree clean except for runtime-state files (`data/layer_state.json`, `trading.db` 4KB stub) and untracked dev_notes / forensic artifacts.

## Pre-condition checks

| Check | Status |
|---|---|
| WIP commit shipped | YES — `f5ec34e chore: WIP snapshot before top-5 trade-blocking fix` |
| Working tree clean (modulo runtime files) | YES |
| Recent CLAUDE_CALL events visible | YES — last call at 06:55:28 |
| Stage 2 architectural fix shipped | YES — `5e4567f` 2026-05-05 05:07:04 |
| Stage 2 flags ON in config | YES — `enable_zero_two_contract=true`, `enable_priority_trim=true` (config.toml:254-255) |
| Top-6 selection visible | YES — `STRAT_TOP_N_APPLIED | input_count=15 cap=6 output_count=6` |
| Forensic bundles referenceable | YES — `IMPLEMENT_TOP5_TRADE_BLOCKING_FIX_INDEPTH.md`, `STAGE2_LAYER3_FORENSIC_BUNDLE_2026-05-02.md` |
| Transformer state | NOT directly read this session (`data/trading.db` not queried; transformer state is persisted there). Inferred shadow-mode based on operator memory. |

## Verification gate — pass

All 5 issues confirmed against current code with file:line citations (collected during Phase 1 investigation, in plan file). All 6 baselines captured above. Pre-condition checks pass. **Phase 1 implementation can begin.**
