# Phase 5 — Verification Framework And Operator Handoff

Phase 4 implementation has shipped on branch `fix/regime-detector-b1a-2026-05-12`. This document defines the verification protocol, captures the pre-deploy baseline for comparison, and documents the metrics to measure after the operator restarts the workers and lets the system trade for the minimum monitoring window (4-6 hours of active trading).

## Status at this point

- Branch: `fix/regime-detector-b1a-2026-05-12` (off `fix/sell-bias-fixes-2026-05-11` HEAD `848fe40`).
- Two atomic commits:
  - `266c5a6` — investigation deliverables + accuracy probe (Phase 0-3 work product).
  - `dea18d8` — B1a code change (config.toml + dataclass + tests).
- Targeted test sweep: 343 tests pass (15 new regime tests + 62 APEX + 86 XRAY + 25 Shadow + 17 scanner + 15 strategist + 3 ensemble + 135 strategies-dir umbrella).
- Workers process (pid 398) is still running the OLD config. The branch must be merged (or the workers pointed at the branch) and restarted for the new thresholds to apply.

## What changed (one-paragraph summary for the operator)

The regime detector's classification thresholds in `config.toml [regime]` and the matching `RegimeSettings` dataclass defaults were narrowed to close the ELSE-fallback gap that absorbed 73.9% of all regime emissions with a 12.5% accuracy on its `ranging` label. Specifically: `trending_adx_threshold` 25→20 (catches the [20, 25) transition band), `ranging_choppiness_threshold` 60→50 (matches crypto-norm flat-market definition), `volatile_atr_percentile` 150→70 (was unreachable from NATR-derived percentile that caps near 100), `dead_adx_threshold` 15→12 (tighter dead criteria). The trade-decision pipeline downstream (Stage 2 prompt construction, scanner score bonus, ensemble category gate, APEX direction lock, XRAY structural flip) is **unchanged** — only the upstream signal feeding them is recalibrated. APEX's PRIMARY sell-bias fix is preserved (62 APEX tests pass).

## Deploy checklist

The operator should run, in order:

1. Confirm tests pass on the branch one more time before deploy:
   ```
   cd /home/inshadaliqbal786/trading-intelligence-mcp
   .venv/bin/python -m pytest tests/test_strategies/test_regime.py tests/test_apex_flip_discipline.py tests/test_apex_sell_bias_gates.py tests/test_xray_dir_flip.py tests/test_shadow_kline_reader/ -q
   ```
2. (Optional) Merge the branch to the operating branch (`fix/sell-bias-fixes-2026-05-11` or main, depending on the deploy workflow).
3. Restart the workers process so it re-reads `config.toml` with the new thresholds.
4. Confirm services back up by running `pgrep -af 'workers.py|server.py'`.
5. Tail `data/logs/workers.log` for the first `REGIME |` emissions and verify the regime-label distribution is no longer monolithically `ranging conf=0.40`.

## Pre-deploy baseline (Phase 0 numbers, for comparison)

Wide window 2026-05-11 11:55 to 2026-05-12 07:47 UTC (~14h):

- Brain Buy share: 38.4%. Final Buy share: **5.8% (Sell share 94.2%)**.
- Of 33 brain Buys, 28 flipped (24 XRAY, 13 reason=apex_flip pre-unified-log).
- Regime distribution: ranging 77.9%, volatile 7.9%, trending_down 6.6%, dead 5.4%, trending_up 2.2%.

Post-deploy window 2026-05-11 22:23 to 2026-05-12 07:47 UTC (~9.4h, since PRIMARY fix unified log activated):

- Brain Buy share: 33.3%. Final Buy share: **9.5% (Sell share 90.5%)**.
- 5 XRAY flips. 0 APEX flips (PRIMARY fix preserving direction).
- Regime distribution: ranging 84.8%, trending_down 5.8%, volatile 3.5%, dead 3.0%, trending_up 2.9%.

Accuracy probe over 48h sample (96 valid samples across 12 symbols):

- Overall detector accuracy: 14.6%.
- False-ranging rate: 88.2%.
- ELSE-fallback accuracy: 12.5%.

Trade history 24h:

- Buy: 9 trades, -$12.21 PnL, 44.4% win rate.
- Sell: 110 trades, +$45.70 PnL, 46.4% win rate.
- Aggregate: +$33.49.

## Post-deploy metrics to capture (target window: 4-6 hours of active trading)

Run the same queries against post-deploy logs and the DB. Compare each metric below to the matching baseline number.

### Direction distribution

```
FILES="data/logs/workers.log <plus newly-rotated workers logs>"
grep -h "DIRECTION_DECISION" $FILES | grep -oE "brain_dir=[A-Za-z]+" | sort | uniq -c
grep -h "DIRECTION_DECISION" $FILES | grep -oE "final_dir=[A-Za-z]+" | sort | uniq -c
grep -h "DIRECTION_DECISION" $FILES | grep -oE "reason=[a-z_]+" | sort | uniq -c
grep -h "DIRECTION_DECISION" $FILES | grep -oE "flip_source=[a-z_]+" | sort | uniq -c
```

Expected direction:

- Final Sell share drops from 90.5% to a range ideally between 60% and 80%. A drop to ~70-75% Sell share would represent the Buy-share recovery the fix is targeting without over-correcting.
- XRAY flip count should drop materially (estimated 50-70% drop based on the q1b causation chain — most flips were enabled by ranging-mislabeled coins that will now classify as weakly trending and trigger APEX direction lock).
- `apex_locked=Y` share should rise from ~25% (current 5 events out of 21 post-deploy DIRECTION_DECISIONs) to ~50% (because trending labels will fire more often, triggering the APEX direction lock).

### Regime distribution

```
grep -hoE "rgm=[a-z_]+" $FILES | sort | uniq -c | sort -rn
```

Expected direction:

- `ranging` share drops from 84.8% to ~30-50%.
- `trending_up` rises from 2.9% to ~10-20%.
- `trending_down` rises from 5.8% to ~10-20%.
- `volatile` rises from 3.5% to perhaps 5-15% (depending on actual NATR distributions).
- `dead` stays roughly stable or decreases slightly.
- Combined trending share (up + down) should rise from 8.7% to ~25-40%.

### ELSE-fallback signature

```
grep -hoE "rgm=ranging conf=0.40" $FILES | wc -l
grep -hoE "rgm=ranging conf=[0-9.]+" $FILES | grep -v "conf=0.40" | wc -l
```

Expected direction:

- `conf=0.40` count should drop from 73.9% of all emissions to ~10-20%.
- Other `ranging` confidences (`0.50-0.90` range from the strict branch) should rise from 3.1% to ~25-35% of ranging emissions.

### Trade outcomes

```
sqlite3 data/trading.db "SELECT side, COUNT(*) AS n, ROUND(AVG(pnl),3) AS avg_pnl, ROUND(SUM(pnl),3) AS total_pnl, SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) AS wins FROM trade_history WHERE entry_time > datetime('now','-6 hours') GROUP BY side;"
```

Expected direction:

- Win rate maintained or improved. If win rate drops below 40% on either side, this is a regression signal.
- Buy count rises (~3-5x the baseline 9 per 14h).
- Aggregate PnL trajectory neutral or up.

### Regime accuracy re-measurement

After 6+ hours of post-deploy data:

```
.venv/bin/python scripts/regime_accuracy_probe.py
```

Expected direction:

- Overall accuracy rises from 14.6% to ~30-50% (still imperfect — the detector uses H1 inputs, the objective is 5-min, so some mismatch is structural).
- False-ranging rate drops from 88.2% to ~50-65%.
- Trending labels should now appear in the sample (currently the stratified selection caught zero trending detections).

## Decision tree after post-deploy measurement

1. **All metrics move in the expected direction** (regime distribution more varied, XRAY flip count down, final Sell share down toward 60-80%, win rate maintained) → Path B1a successful. Mark Phase 5 complete. Path A (XRAY threshold tune) not needed.

2. **Regime distribution improves but XRAY flip count is still high and final Sell share is still above 85%** → consider proceeding with Path A: change `config.toml [risk] xray_dir_flip_threshold_ratio` from `3.0` to `10.0` (operator-approved value). Implement as a separate atomic commit on a new branch.

3. **Win rate drops materially (more than 5 percentage points)** → investigate. The fix may have caused unintended ensemble category mis-selections (e.g., trending strategies activating in genuinely-ranging markets that just happen to hit the new wider trending criteria). Possible follow-up: tighten the trending criteria modestly (e.g., 22 instead of 20) or revisit the dead/volatile thresholds.

4. **Direction distribution overshoots** (final Sell share drops below 50% AND Buy trades show negative PnL) → over-correction. The trending labels are firing on too many genuinely-ranging markets. Possible revert path: raise `trending_adx_threshold` back partway (e.g., 22 instead of 20) and re-measure.

## Verification report template (to fill in after monitoring window)

This file becomes the final verification report after measurements complete. Fill in:

### What changed

- Branch / commits: `fix/regime-detector-b1a-2026-05-12` commits `266c5a6` + `dea18d8`.
- Deploy timestamp: <fill in>
- Restart confirmed at: <fill in>

### Pre-fix baseline (above, from Phase 0)

### Post-fix metrics (fill after 4-6h)

| Metric | Pre-fix baseline | Post-fix value | Delta | Direction |
|---|---|---|---|---|
| Brain Buy share | 38.4% | | | |
| Final Buy share | 9.5% | | | |
| Final Sell share | 90.5% | | | |
| XRAY flips (count / 6h normalized) | ~3 / 6h | | | |
| `apex_locked=Y` share | ~25% | | | |
| Regime `ranging` share | 84.8% | | | |
| Regime trending share | 8.7% | | | |
| Regime `dead` share | 3.0% | | | |
| Regime `volatile` share | 3.5% | | | |
| Regime `conf=0.40` share | 73.9% | | | |
| Accuracy probe overall | 14.6% | | | |
| Accuracy probe false-ranging | 88.2% | | | |
| Win rate (Buy) | 44.4% | | | |
| Win rate (Sell) | 46.4% | | | |
| Aggregate PnL (6h) | <compute> | | | |

### Comparison summary

<one paragraph: did the fix achieve its goal? key surprises? regressions?>

### Recommendation

<one of: keep / tune further (specify) / revert / proceed to Path A with threshold value X / proceed to Path B2 or B3>

### Operator sign-off

<operator confirms whether Phase 5 is complete or further action is needed>

## What is NOT verified by this work

Per the spec's Part H, the following remain out of scope and are not addressed by Path B1a:

- Profitability is not guaranteed; this fix addresses a mechanism, not a strategy edge.
- Sell-bias may not reach 50/50 even after the fix. Market conditions during the verification window may genuinely favor Sell.
- Brain's own Sell-bias at the LLM stage (Stage 2 prompt construction OUT OF SCOPE) is not addressed. Brain's 61.6% Sell share is a separate issue.
- Per-coin regime accuracy may improve modestly but is not yet evaluated per-symbol with high-confidence samples.
- The XRAY structural R:R threshold (3.0) is unchanged; if Path A is needed, it will be a separate atomic commit.
- Telegram blocking, sniper qty rounding, brain CLI stalls, Layer 1B signal calibration are out of scope.
