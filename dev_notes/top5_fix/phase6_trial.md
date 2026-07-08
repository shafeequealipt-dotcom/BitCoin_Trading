# Phase 6 — Live Trial (3-5 days)

All five fixes shipped. Phase 6 observes the combined effect end-to-end. Daily snapshots populate the monitors below.

## Commits in scope

| Phase | SHA | Change |
|---|---|---|
| 0 | `f5ec34e` | WIP snapshot |
| 1 | `94044f7` | XRAY confidence formula — sweep direction + 0.5 floor |
| 1c | `78b22ac` | `_check_swept` canonical sweep+reclaim semantic — restores SMC variance |
| 2 | `5d53dc4` | Prompt mode follows transformer (SHADOW variant) |
| 3 | `6d1e28e` | Strip strict STRONG rule — Path C judgment language |
| 4 | `d7102b1` | EMA-smooth ta_conf to eliminate Context flapping |
| 5 | `b25148c` | FUND RULES essential marker |

## Pre-trial deploy checklist

Before observation begins:

1. **Restart services so the new code loads.** systemd units affected:
   - `trading-workers.service` — picks up Phase 1, 2, 4 changes (structure_engine, trading_mode, engine).
   - `trading-mcp-sse.service` — picks up Phase 2, 4 wiring (TAEngine settings).
   - `shadow.service` — unaffected; no need to restart.

2. **Re-enable Layer 2 and Layer 3** (`data/layer_state.json` currently shows `user_stopped: true`, layer 2/3 off — operator paused before the fix).

3. **Verify the SHADOW prompt header.** `tail -F data/logs/brain.log | grep MODE` should show `MODE: SHADOW (paper trading on real Bybit market data)` after the next CALL_A. If it still says MAINNET, the transformer state may be `bybit` — toggle via Telegram dashboard "Switch to Shadow" button.

4. **Confirm `[ta]` config section is in place.** `grep -A1 '\[ta\]' config.toml` should show `confidence_ema_alpha = 0.4`.

5. **Verify FUND RULES survival in a fresh prompt.** Touch the dump sentinel: `touch data/stage2_dumps/.enabled`. Wait one cycle. The newest JSON dump in `data/stage2_dumps/` should contain `FUND RULES (non-negotiable):`.

6. **Forensic observability.** New log lines to grep for:
   - `XRAY_CONFIDENCE_DETAIL` — per-coin per-cycle SMC breakdown + final conf.
   - `XRAY_LIQ` — Phase 1c extends with `reclaimed=N` count; non-zero reclaimed
     across multiple cycles confirms the canonical sweep+reclaim path is
     firing. If reclaimed stays 0 universe-wide, `sweep_recency_bars`
     (default 30) or `sweep_require_reclaim` (default true) may need
     tuning in `[analysis.structure]` of config.toml.
   - `MODE_TRANSITION` — emitted by TradingModeManager.refresh() on transformer flip.
   - `TA | ... conf=... conf_raw=...` — both smoothed and raw confidence visible.

## Daily monitors

Capture each monitor once per 24h. Cite the log line / file path used.

### M1 — Trade execution rate

Daily counts grepped from `data/logs/brain.log`:

- `STRAT_CALL_A_END | el=...` count: ___
- `STRAT_CALL_A_END | trades=0` count: ___
- `STRAT_CALL_A_END | trades=1` count: ___
- `STRAT_CALL_A_END | trades=2` count: ___
- Orders actually placed (Shadow): query trade_intelligence table or grep `ORDER_PLACED` in workers.log.

Baseline (pre-fix, post-Stage-2-phase-5): 17 starts → 16 trades=0, 0 trades=1+. Target: trades resume at 5-30/day in active markets.

### M2 — Direction balance

For each placed trade, the direction (Buy/Sell). Sum daily.

- Buy count: ___
- Sell count: ___
- Long-short ratio: ___

Baseline: 100% long (no shorts ever placed). Target: roughly proportional to market.

### M3 — XRAY confidence distribution

Universe-wide percentiles from the new `XRAY_CONFIDENCE_DETAIL` log line.

```
grep -h 'XRAY_CONFIDENCE_DETAIL' data/logs/workers.log | tail -200 | grep -oE 'final_conf=[0-9.]+' | sort -t= -k2 -n
```

- p25: ___
- p50: ___
- p75: ___
- p95: ___
- max: ___
- count > 0.7: ___

Baseline: p25=p50=p75=0.55, p95=0.70, max=0.80. Target: spread broadens, top decile reaches > 0.7.

### M4 — Trade outcomes

Closed trades each day (from Shadow):

- Win rate: ___
- Average win: ___
- Average loss: ___
- Average RR achieved: ___

Three-five-day samples are not statistically significant. Trend direction is what matters.

### M5 — Score stability

For 3 coins appearing in ≥ 5 consecutive cycles, capture per-coin Context score per cycle. Compute stdev.

```
grep -h 'STRAT_VOTE_TRACE\|STRAT_L2_DONE' data/logs/workers.log | tail -200 | ...
```

- ALICEUSDT Context stdev: ___ (baseline > 3)
- Coin 2 Context stdev: ___
- Coin 3 Context stdev: ___

Target: < 2 (vs baseline > 3).

### M6 — Prompt completeness

Sample 50 prompt dumps from `data/stage2_dumps/` since deploy.

- FUND RULES present in ___/50 prompts (target: 50/50)
- MODE: SHADOW present in ___/50 prompts (target: 50/50)
- Any `CLAUDE_PROMPT_TRIMMED` events with `FUND RULES` in dropped_labels: ___ (target: 0)

### M7 — Claude response quality

Sample 10-20 brain.log decisions. For each, note:

- Did reasoning cite specific structural elements? (XRAY components, ensemble votes, scorer breakdown)
- Direction balance — does Claude take shorts when XRAY says short and the rest of the data supports it?
- Refusals — does Claude still return zero trades when no candidate has coherent evidence?

### M8 — System stability

Counts since deploy:

- `STALL_60S`: ___ (vs baseline)
- `STALL_120S`: ___
- `STALL_240S`: ___
- `CLAUDE_CALL_TIMEOUT`: ___
- New ERROR/CRITICAL log tags introduced by the fix: ___ (target: 0)

## Decision criteria

### Trial succeeds if

- Trades execute at meaningful frequency (5-30/day in active markets).
- Direction balance reflects market — both long and short trades occur when warranted.
- Score stability improved (M5 stdev < 2).
- FUND RULES + SHADOW mode present in every prompt sampled.
- System stability not degraded (M8 within or below baseline).

### Trial fails if

- Zero trades for the full trial period.
- All trades same direction (shorts still broken).
- Score still flapping wildly (M5 stdev > 3).
- FUND RULES dropped in any sampled cycle.
- New failure modes appear in M8.

### If trial fails

1. Document the failure precisely.
2. Diagnose: unanticipated interaction, calibration miss, prompt drift?
3. Decide adjustments or rollback per-phase. Each commit is independently revertable via `git revert <sha>`.

## Daily journal

(Operator fills in date-by-date observations below as the trial unfolds.)

### Day 1 — YYYY-MM-DD

(observations)

### Day 2 — YYYY-MM-DD

(observations)

### Day 3 — YYYY-MM-DD

(observations)

### Day 4 — YYYY-MM-DD

(observations)

### Day 5 — YYYY-MM-DD

(observations)
