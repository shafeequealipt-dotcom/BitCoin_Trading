# H4 Phase 1 — Rejection Trace + Bucket Analysis

## Method

Extracted every `REENTRY_REGIME_DRIFT_CHECK` event from `data/logs/workers.log` and `data/logs/workers.2026-05-16_07-26-32_801275.log` over 2026-05-16 07:26:32 → 12:33:41 (~5h07m). Joined each rejection (`reason=same_conditions`) against the underlying losing thesis in `trade_thesis` (sqlite query against `data/trading.db`) to recover the prior loss timestamp.

This is the **centerpiece evidence** for the H4 root-cause finding.

## Aggregate reason distribution

| Reason | Count | % of evaluations |
|---|---|---|
| `same_conditions` (BLOCK) | 31 | 57.4 % |
| `setup_drift` (allow) | 13 | 24.1 % |
| `direction_drift` (allow) | 6 | 11.1 % |
| `regime_drift` (allow) | 4 | 7.4 % |
| **Total evaluations** | **54** | 100.0 % |

23 of 54 evaluations passed; 31 blocked. Block rate **57.4 %** — matches the spec's "60 % rejection rate" claim within sampling noise.

## Per-(symbol, setup, prior_pnl) cluster — every block cites a single prior loss

| Block count | Symbol | Setup | Prior PnL (USD) | Prior trade closed at | Age at rejection time (approx) |
|---|---|---|---|---|---|
| 8 | XRPUSDT | bearish_fvg_ob | -11.33 | 2026-05-16 07:41:27 | 1–3 hours |
| 6 | LINKUSDT | bearish_fvg_ob | -21.45 | 2026-05-16 07:41:27 | 1–2 hours |
| 5 | SEIUSDT | bearish_fvg_ob | -10.48 | 2026-05-16 07:41:25 | 1–3 hours |
| 4 | SKRUSDT | bearish_structural_break | **-1.57** | **2026-05-15 16:30:18** | **~17–18 hours** |
| 3 | AVAXUSDT | bearish_fvg_ob | -1.03 | 2026-05-16 07:41:26 | ~1.5–2 hours |
| 2 | LDOUSDT | bearish_fvg_ob | -32.88 | **2026-05-15 16:29:34** | **~16 hours** |
| 1 | ETHUSDT | bearish_fvg_ob | -7.09 | 2026-05-16 09:45:21 | minutes |
| 1 | DYDXUSDT | bearish_structural_break | -8.64 | **2026-05-13 22:10:25** | **~60 hours / 2.5 days** |
| 1 | ARBUSDT | bearish_fvg_ob | **-0.04** | **2026-05-14 21:47:26** | **~34 hours / 1.4 days** |

(Block count = number of times that specific prior-loss row was cited.)

## Bucketing per Phase 1 method

| Bucket | Symptom | Count (of 31) | Evidence |
|---|---|---|---|
| **(c) Prior loss is stale (older than ~6h)** | gate has no recency clamp | **8** (SKRUSDT×4 + LDOUSDT×2 + DYDXUSDT×1 + ARBUSDT×1) | Closed 16h–60h before the rejection |
| **(b) Categorically identical but contextually different** | gate is too coarse | **all 31** | The same single losing thesis is cited up to 8 times across hours; XRP/LINK/SEI all from the same 07:41 cohort event |
| **(b') Magnitude-noise prior loss** | gate has no magnitude floor | **8** (ARBUSDT $-0.04, AVAXUSDT $-1.03×3, SKRUSDT $-1.57×4) | Losses under $2; ARBUSDT is literally a 4-cent loss |
| **(a) Truly identical AND brain is the only failure** | brain should have moved on | 0 — buckets (b) and (c) dominate every row | n/a (compound — see below) |
| **(d) Malformed thesis** | data quality | 0 | All cited rows have populated regime/setup/direction |

**Key observation:** Every single one of the 31 blocks falls into bucket (b). 8 of 31 are ALSO in bucket (c). 8 of 31 are ALSO in bucket (b') — losses below a meaningful magnitude. Buckets are non-exclusive.

## Concrete examples (most damning)

### Example 1: DYDXUSDT, 60-hour-old loss

```
2026-05-16 10:37:38 REENTRY_REGIME_DRIFT_CHECK | sym=DYDXUSDT
  cur_regime=trending_down cur_setup=bearish_structural_break cur_dir=Sell
  prior_regime=trending_down prior_setup=bearish_structural_break prior_dir=Sell
  prior_pnl=-8.64 reason=same_conditions
```

The prior $-8.64 loss closed at 2026-05-13 22:10:25. **Sixty hours later**, on a fresh setup, the gate still cites it. Market regime has churned through multiple cycles; setup labels match by coincidence of categorical bucket. Trading aim ("aggressively exploit opportunities") is being actively undermined.

### Example 2: ARBUSDT, 4-cent loss

```
prior_pnl=-0.04 (closed 2026-05-14 21:47:26)
```

A $0.04 loss is below transaction-cost noise (taker fee on a $50 position ≈ $0.03; this loss is essentially fee + slippage rounding). The system "learned" nothing from this trade, yet it's blocking re-entry 1.4 days later.

### Example 3: SKRUSDT, 17-hour-old $1.57 loss, blocking 4 times in 5 hours

```
prior_pnl=-1.57 (closed 2026-05-15 16:30:18)
SKRUSDT rejected at 10:12, 10:21, 10:28, 10:37 on 2026-05-16
```

$1.57 is sub-noise. 17 hours later, the market has moved through a regime cycle. Yet the block fires 4 times in 25 minutes because brain keeps proposing the same identical-cell setup.

### Example 4: The 07:41 cohort wash

XRPUSDT $-11.33, LINKUSDT $-21.45, SEIUSDT $-10.48, AVAXUSDT $-1.03 ALL closed within 2 seconds at 07:41 (same exit cohort — likely a coordinated SL hit on the trending_down move). All four then become indefinite blockers for the rest of the session on `bearish_fvg_ob Sell`. Combined: **22 of 31 blocks** trace to this single 2-second window.

## Concentration of blockers — single-cohort tail

The 4 same-cohort losses (XRP/LINK/SEI/AVAX) account for 22 / 31 (71 %) of all blocks in the window. The 5 older losses account for 9 / 31 (29 %). A single 2-second market event is producing 71 % of all rejections for the next 5 hours.

## What brain saw (R3/R5 contribution)

Brain re-proposed XRP 8 times, LINK 6 times, SEI 5 times — clearly NOT diversifying away from blocked names. This is real R3/R5 contribution. BUT: even if brain diversified perfectly, the 8 stale-prior-loss blocks (ARB/SKR/DYDX/LDO) would still fire whenever the brain genuinely picked one of those symbols on a setup that happens to share the categorical cell. Brain visibility alone does not eliminate the root.

## Verdict

The dominant root cause is **R1: gate is too coarse and time-unbounded**. Three orthogonal failure modes inside R1:
1. **No recency clamp** — losses from 1+ days ago still block.
2. **No magnitude floor** — $0.04 / $1 / $1.57 losses block as effectively as $32 losses.
3. **No market-shift escape** — even when the same cohort event spawned 4 blockers, a 5-hour drift in price / volatility / structure does not unlock them.

R5 (no feedback to brain) is a secondary contributor but cannot be the primary fix — the stale 8 rejections would persist with or without brain visibility.

R2 (candidate pipeline narrowness) and R3 (brain selection bias) are not the dominant root either, but contribute (brain re-selecting XRP 8x while plenty of other coins were on the menu).

The recommended fix is R1 (gate calibration) as primary; R5 (brain visibility) as optional secondary if Phase 4 verification of R1 shows rejection rate still above target. Both are aim-aligned. R1 alone is forecast to drop the rejection rate to under 15 % based on the bucket distribution.
