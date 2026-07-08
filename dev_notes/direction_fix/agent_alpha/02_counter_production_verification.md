# 02 — Counter Production Verification (2026-05-16 session evidence)

Agent ALPHA Phase 1, Step 2. Direct evidence from the production log
`/home/inshadaliqbal786/ALL_LOGS_2026-05-16_13-40_to_18-30.log`
(93,467 lines, 22 MB, 2026-05-16 13:40 to 18:30).

The goal: prove with raw counts exactly what `suggested_direction` and
`trade_direction` reach the brain prompt for each setup variant, resolve the
Phase 0 drift flag, and document whether the spec's R1 mechanism is supported.

## Total event counts

| Event | Count |
|---|---|
| XRAY_CLASSIFY (non-NONE) | 2,644 |
| STRAT_DIRECTIVE (brain outputs) | 80 |
| bullish_fvg_ob_counter | 691 |
| bearish_fvg_ob_counter | 35 |
| bullish_fvg_ob (direct) | 313 |
| bearish_fvg_ob (direct) | 1,531 |
| bearish_structural_break | 74 |
| Any-counter combined | 726 |

Total XRAY_CLASSIFY events = 2,644 (matches COMPLETE_FINDINGS exactly).

## Setup-type breakdown matches COMPLETE_FINDINGS

The COMPLETE_FINDINGS table:

```
1,531 bearish_fvg_ob
  691 bullish_fvg_ob_counter
  313 bullish_fvg_ob
   74 bearish_structural_break
   35 bearish_fvg_ob_counter
─────
2,644 XRAY_CLASSIFY events
```

My replication query:

```
grep "XRAY_CLASSIFY |" ALL_LOGS_2026-05-16_13-40_to_18-30.log \
  | awk -F 'setup_type=' '{split($2, a, " "); print a[1]}' \
  | sort | uniq -c | sort -rn
```

Result:

```
   1531 bearish_fvg_ob
    691 bullish_fvg_ob_counter
    313 bullish_fvg_ob
     74 bearish_structural_break
     35 bearish_fvg_ob_counter
```

Exact match. The investigation evidence is reproducible.

## Per-counter direction field verification — the Phase 0 drift resolution

For 691 `bullish_fvg_ob_counter` events:

```
grep "setup_type=bullish_fvg_ob_counter" ALL_LOGS_2026-05-16_13-40_to_18-30.log \
  | awk '{ for(i=1;i<=NF;i++){
              if($i ~ /^trade_direction=/) td=$i;
              if($i ~ /^suggested_direction=/) sd=$i;
            } print td, sd }' \
  | sort | uniq -c
```

Result:

```
    691 trade_direction=long suggested_direction=short
```

ALL 691 events carry `trade_direction=long` AND `suggested_direction=short`.
There is zero variance. The counter setup ALWAYS inverts trade_direction relative
to the raw market-structure label.

For 35 `bearish_fvg_ob_counter` events:

```
     35 trade_direction=short suggested_direction=long
```

Mirror outcome: ALL 35 carry `trade_direction=short` AND
`suggested_direction=long`. Zero variance.

## Full direction-pair distribution across all 2,644 events

```
   1605 sd=short td=short    (1531 bearish_fvg_ob + 74 bearish_structural_break)
    691 sd=short td=long     (bullish_fvg_ob_counter — ALL of them)
    313 sd=long  td=long     (bullish_fvg_ob)
     35 sd=long  td=short    (bearish_fvg_ob_counter — ALL of them)
```

Where:
- `sd=short td=short` means market_structure said downtrend AND classifier recommends
  short trade. Both fields agree.
- `sd=short td=long` means market_structure said downtrend BUT counter logic says
  long trade. Fields disagree — counter inversion.
- `sd=long td=long` means market_structure said uptrend AND classifier recommends
  long trade. Both fields agree.
- `sd=long td=short` means market_structure said uptrend BUT counter logic says
  short trade. Counter inversion on the bullish-suggested side.

## Aggregate direction distributions

`suggested_direction` (raw market_structure label):
- short: 2,296 (86.8%)
- long: 348 (13.2%)

`trade_direction` (setup-implied payoff, what the brain prompt reads):
- short: 1,640 (62.0%)
- long: 1,004 (38.0%)

## Phase 0 drift verdict

The spec's R1 description states: "These are bullish FVG/OB structures detected
AGAINST a trending_down regime. The counter-trade logic collapses them to
`suggested_direction=short`."

This is FALSE. The production data shows unambiguously:

- The counter-trade logic does NOT mutate `suggested_direction`. The 691 bullish
  counter setups all keep `suggested_direction=short` because that is the raw
  market_structure label that was set BEFORE the counter logic ran. The counter
  logic just records that the actual trade payoff is `trade_direction=long`.
- The 87% short figure in COMPLETE_FINDINGS counted `suggested_direction`. That
  is the raw market_structure label, which is genuinely 87% short on this day
  because the market was 76% trending_down.
- The brain prompt does NOT read `suggested_direction`. It reads
  `trade_direction`. So whatever bias the brain SEES is the 62% short / 38%
  long distribution of `trade_direction`, not the 87%/13% of suggested_direction.
- The COMPLETE_FINDINGS narrative "Buy-favoring structural setups get RE-LABELED
  as Sell candidates in the Brain's view" is incorrect. They are CORRECTLY
  labelled as `trade_direction=long` in the brain prompt with an explicit
  "(COUNTER-TRADE — trade direction is OPPOSITE to market structure bias; lower
  conviction)" suffix.

## What the actual mechanism is

The actual direction-distribution mechanics for this session, in pipeline order:

1. Market_structure detects 76% trending_down on the universe (`coin_regime_history`
   DB — independent regime classifier, in agreement with structure_engine's own
   market_structure pass).
2. `analyze()` writes `suggested_direction=short` on ~87% of analyses because the
   raw label tracks regime.
3. `classify_setup()` runs the decision tree per analysis:
   - On 1,531 cases where in-direction bearish FVG+OB present: emits
     `BEARISH_FVG_OB`, `trade_direction=short`.
   - On 691 cases where in-direction bearish zones missing but bullish counter
     zones present: emits `BULLISH_FVG_OB_COUNTER`, `trade_direction=long`. The
     counter branch INVERTS the trade-payoff direction relative to the regime
     label.
   - On 313 cases the regime was up and bullish in-direction zones present:
     emits `BULLISH_FVG_OB`, `trade_direction=long`.
   - On 35 cases regime was up but counter zones bearish: emits
     `BEARISH_FVG_OB_COUNTER`, `trade_direction=short`.
4. ScannerWorker builds the CoinPackage with `pkg.xray.trade_direction` (NOT
   `suggested_direction`) — so the brain prompt sees the 62%/38% distribution.
5. APEX assembler.py copies `analysis.suggested_direction` into
   `StructuralData.suggested_direction` — so the APEX optimizer sees the
   87%/13% raw regime label.

The asymmetry of what reaches the brain vs what reaches APEX is the actual
issue, NOT a single misnamed inversion.

## Sample raw log lines (verbatim, 10 lines)

```
2026-05-16 13:40:45.064 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=BTCUSDT setup_type=bearish_fvg_ob confidence=0.55 score=100 trade_direction=short suggested_direction=short is_counter=false | no_ctx
2026-05-16 13:40:45.078 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=ETHUSDT setup_type=bearish_fvg_ob confidence=0.60 score=64 trade_direction=short suggested_direction=short is_counter=false | no_ctx
2026-05-16 13:40:45.181 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=ADAUSDT setup_type=bullish_fvg_ob_counter confidence=0.35 score=64 trade_direction=long suggested_direction=short is_counter=true | no_ctx
2026-05-16 13:40:45.194 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=DOGEUSDT setup_type=bearish_fvg_ob_counter confidence=0.32 score=30 trade_direction=short suggested_direction=long is_counter=true | no_ctx
2026-05-16 13:40:45.367 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=RENDERUSDT setup_type=bullish_fvg_ob_counter confidence=0.35 score=80 trade_direction=long suggested_direction=short is_counter=true | no_ctx
2026-05-16 13:40:45.444 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=ENAUSDT setup_type=bullish_fvg_ob_counter confidence=0.49 score=100 trade_direction=long suggested_direction=short is_counter=true | no_ctx
2026-05-16 13:40:45.680 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=LDOUSDT setup_type=bullish_fvg_ob_counter confidence=0.42 score=90 trade_direction=long suggested_direction=short is_counter=true | no_ctx
2026-05-16 13:40:45.698 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=CRVUSDT setup_type=bullish_fvg_ob_counter confidence=0.28 score=64 trade_direction=long suggested_direction=short is_counter=true | no_ctx
2026-05-16 13:45:45.153 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=DOGEUSDT setup_type=bearish_fvg_ob_counter confidence=0.32 score=30 trade_direction=short suggested_direction=long is_counter=true | no_ctx
2026-05-16 13:50:45.180 | INFO     | src.workers.structure_worker:tick:211 | XRAY_CLASSIFY | sym=DOGEUSDT setup_type=bearish_fvg_ob_counter confidence=0.32 score=30 trade_direction=short suggested_direction=long is_counter=true | no_ctx
```

Every counter line explicitly carries `is_counter=true` and the inverted
`trade_direction`. There is no hidden semantic, no special casing.

## Confidence percentile spot-check

The counter setups carry materially reduced confidence (multiplied by 0.7). Sample
confidence values:

- bullish_fvg_ob_counter samples: 0.28, 0.35, 0.42, 0.49 (across ADAUSDT, RENDERUSDT,
  ENAUSDT, LDOUSDT, CRVUSDT).
- bearish_fvg_ob_counter sample: 0.32 (DOGEUSDT, repeated across ticks).
- bearish_fvg_ob (direct) samples: 0.55, 0.60, 0.70.
- bullish_fvg_ob (direct) — would carry similar 0.5+ confidence.

So the brain prompt sees the inverted direction with an explicit lower-conviction
annotation AND a numerically lower confidence value (0.28-0.49 vs 0.55-0.70).
The mechanical down-weighting is in place.

## Does production support the spec's R1 mechanism?

Verdict: NO. The spec describes a mechanism that does not exist in the code. The
counter logic does NOT collapse bullish structures to suggested_direction=short
(that field is set earlier by market_structure, independent of the counter
logic, and never written by classify_setup). The 87% short suggested_direction
is the raw regime label — driven by genuine market state, not by any inversion.

The bias evidence remains valid:
- 89% Sell brain output.
- 91% Sell APEX_DIR_LOCK direction.
- 91% Sell orders placed.
- Buys historically win 55.6% / Sells 41.8% on this same dataset.

But the bias is NOT produced by counter-trade inversion at the suggested_direction
field. It is produced (per the four spec causes R1-R4) by some combination of:

- APEX_DIR_LOCK reading `suggested_direction` (87% short) and applying
  regime-based lock — that is R2's territory, BETA's scope. The 691 counter
  setups feed APEX with `suggested_direction=short` (the raw regime label,
  unchanged) and APEX has no awareness of the inverted trade_direction.
- The brain receiving `trade_direction` (62%/38%) but still preferring sells
  because the supporting context (regime, label, etc.) reinforces the bearish
  framing — partly a brain-prompt-quality issue, partly a downstream APEX
  override.
- Portfolio direction concentration absence (R4, GAMMA scope).
- Override threshold (R3, BETA scope).

## What R1 actually is, restated

R1 in light of this evidence becomes: "APEX assembler.py:737 receives
`suggested_direction` (the raw market-structure label) and feeds it to the APEX
lock; the inverted `trade_direction` from counter setups is dropped on the
floor between XRAY and APEX." This is the genuine information loss between
Layer 1B (XRAY) and Layer 3 (APEX).

The fix options in 04 are reframed around this corrected mechanism.
