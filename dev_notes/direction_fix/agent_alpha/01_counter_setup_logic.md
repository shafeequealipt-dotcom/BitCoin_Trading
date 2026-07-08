# 01 — Counter Setup Logic (Decoded from current code)

Agent ALPHA Phase 1, Step 1. Authoritative file:line tracing of the XRAY classifier
branches that populate `suggested_direction` and `trade_direction` on the
`StructuralAnalysis` dataclass, with a specific focus on the consumer chain that
reaches the brain CALL_A prompt.

## How suggested_direction is set (one place only)

`src/analysis/structure/structure_engine.py:269-275` — inside `StructureEngine.analyze()`:

```
suggested_direction = ""
if market_structure.structure == "uptrend":  suggested_direction = "long"
elif market_structure.structure == "downtrend": suggested_direction = "short"
```

There is one additional override at lines 309-315 in the same function where, when
`market_structure.structure` is neither uptrend nor downtrend, the structural-placement
branch falls back to whichever side has a higher rr_ratio and rewrites
`suggested_direction` to match. Tied case prefers long.

`classify_setup()` (lines 1008-1309) DOES NOT mutate `suggested_direction`. It only
reads it from `analysis.suggested_direction` at line 1083.

## How trade_direction is set (the Phase 4 counter contract)

`structure_engine.py:1104` initialises `analysis.trade_direction = direction` where
`direction = (analysis.suggested_direction or "").lower()`. Every classifier branch that
later returns a non-NONE SetupType either keeps this default OR rewrites it.

Per-branch decode follows. All line numbers refer to the current
`investigate/direction-fix-phase0` HEAD.

### BULLISH_FVG_OB (in-direction long, full confidence)

- Trigger: lines 1133-1141. nearest_fvg.direction=="bullish" AND not filled AND
  nearest_ob.direction=="bullish" AND nearest_ob.fresh AND `_bull_alignment()` AND
  mtf_score_01 >= fvg_ob_min (0.7).
- `_bull_alignment()` lines 1106-1121: direction must be "long", structure must be
  "uptrend" OR (ranging AND mtf_score_01 >= 0.55).
- `analysis.trade_direction` = "long" (default from line 1104, suggested_direction was
  already "long").
- Confidence: min(mtf_score_01, smc_01). No multiplier.
- Downstream: ScannerWorker reads `setup_type` + `trade_direction` from
  StructuralAnalysis; both reach `pkg.xray` on the CoinPackage.

### BEARISH_FVG_OB (in-direction short, full confidence)

- Trigger: lines 1151-1159. Mirror of BULLISH_FVG_OB.
- `analysis.trade_direction` = "short" (default, suggested_direction was "short").
- Confidence: min(mtf_score_01, smc_01).
- Downstream: identical to BULLISH_FVG_OB.

### BULLISH_FVG_OB_COUNTER (counter long, x0.7 confidence) — the disputed branch

- Trigger: lines 1175-1194. Reached only when in-direction branches above did NOT
  fire. Requires `counter_enabled` (default True), `direction == "short"` (the
  suggested label from market_structure was downtrend), nearest_fvg_counter is
  bullish + not filled, nearest_ob_counter is bullish + fresh,
  `_counter_alignment("long", struct, cfg)` allows it (i.e. structure is downtrend,
  ranging, or volatile when not strict), AND mtf_score_01 >= counter_mtf_min (0.40).
- Line 1189: `analysis.trade_direction = "long"` — explicit mutation. This is the
  ONLY place in classify_setup that overwrites the default.
- Confidence: min(mtf_score_01, smc_01) * counter_mult (0.7).
- `analysis.suggested_direction` is NOT mutated. It remains "short" as set by
  `analyze()` at line 273.
- Downstream observability: XRAY_CLASSIFY at structure_worker.py:212-219 emits both
  `trade_direction=long` and `suggested_direction=short` and `is_counter=true`.

### BEARISH_FVG_OB_COUNTER (counter short, x0.7 confidence) — mirror

- Trigger: lines 1196-1216. Mirror of BULLISH_FVG_OB_COUNTER. Requires
  `direction == "long"` (suggested was uptrend), bearish counter FVG + OB present.
- Line 1211: `analysis.trade_direction = "short"` — explicit mutation.
- `analysis.suggested_direction` remains "long".
- Confidence: min(mtf_score_01, smc_01) * 0.7.

### BULLISH_STRUCTURAL_BREAK (in-direction long, full or minor)

- Trigger: lines 1218-1241. `last_bos.direction == "bullish"`, `direction == "long"`,
  retest condition.
- `analysis.trade_direction` stays "long" (default).
- Confidence: max(mtf_score_01, smc_01); * 0.8 for minor BoS.

### BEARISH_STRUCTURAL_BREAK (mirror)

- Trigger: lines 1243-1256. Mirror.
- `analysis.trade_direction` stays "short" (default).

### BULLISH_LIQUIDITY_SWEEP / BEARISH_LIQUIDITY_SWEEP

- Trigger: lines 1263-1277. active_sweep present with sufficient sweep_depth_pct and
  matching direction.
- `analysis.trade_direction` stays equal to `direction` (default).
- Confidence: mtf_score_01 (no multiplier).

### BULLISH_RANGE_BREAKOUT / BEARISH_RANGE_BREAKDOWN

- Trigger: lines 1286-1303. position_in_range threshold and direction alignment.
- `analysis.trade_direction` stays equal to `direction` (default).

### NONE (no setup)

- Lines 1305-1309. `analysis.trade_direction = ""` is reset; suggested_direction is
  left untouched.

## What field reaches the brain CALL_A prompt — definitive trace

The brain CALL_A new-trade prompt is built in `src/brain/strategist.py` via two
parallel formatters: `_format_packages_for_prompt` (legacy) and
`_format_packages_for_prompt_full` (full-context variant; both render the per-coin
block from the CoinPackage `pkg.xray` substructure).

Per-coin Setup line, file:line:

- `strategist.py:1941-1953` — reads `pkg.xray.setup_type` and renders the suffix
  "(COUNTER-TRADE — trade direction is OPPOSITE to market structure bias; lower
  conviction)" when the setup_type substring contains "counter". The same block
  emits `trade_direction=<value>` on the Setup line. Source: `pkg.xray.trade_direction`.
- `strategist.py:2540-2548` — the full-context formatter renders the same payload
  with the suffix "(COUNTER-TRADE — opposite to structural bias)" plus
  `dir=<trade_direction>`.

Both formatters read `pkg.xray.trade_direction`, not `suggested_direction`.

`pkg.xray.trade_direction` is populated in `src/workers/scanner_worker.py:615-626`
with a fallback chain `trade_direction OR suggested_direction OR ""`. For counter
setups Phase 4 already wrote the inverted `trade_direction` onto the
StructuralAnalysis, so the brain prompt sees the counter-trade payoff direction.

`pkg.xray.suggested_direction` does NOT exist as a field on the XrayBlock dataclass
(see `src/core/coin_package.py:36-58`). `suggested_direction` is only used to
populate `state_label.label_state(...)` at scanner_worker.py:775-776 and to
populate `interestingness.compute_interestingness(...)` at scanner_worker.py:840-842
(both consume it as labelling/score context, not as a prompt-visible direction
field).

## What field reaches the APEX optimizer's lock decision input

`src/apex/assembler.py:737` writes `StructuralData.suggested_direction =
analysis.suggested_direction` directly. `StructuralData` does NOT carry
`trade_direction`. APEX's `_check_direction_lock` (R2 territory) operates on regime
plus `StructuralData.suggested_direction`; it does NOT see the counter inversion.

So:

- Brain CALL_A prompt sees `trade_direction` (the inverted counter payoff for
  BULLISH/BEARISH_FVG_OB_COUNTER).
- APEX lock sees `suggested_direction` (the raw market-structure label, NOT
  inverted).

## What field reaches the XRAY suggested_direction telemetry

`XRAY_CLASSIFY` log line, emitted at `src/workers/structure_worker.py:211-219` for
every non-NONE classification, emits BOTH `trade_direction` and `suggested_direction`
verbatim. The 87% short figure that COMPLETE_FINDINGS counted from the production
log is `suggested_direction=short`, not `trade_direction=short`.

This is the Phase 0 drift. The spec describes R1 as "counter logic collapses
bullish FVG/OB structures to `suggested_direction=short`". That phrasing is
incorrect. The code:

- Computes `suggested_direction` purely from market_structure (uptrend → long,
  downtrend → short) at lines 269-275.
- For 691 bullish-FVG-OB-counter cases this leaves `suggested_direction=short`
  unchanged.
- Sets `trade_direction = "long"` to record the counter-trade payoff direction.

The `suggested_direction` field was never going to be "long" for a counter setup —
the setup classifier doesn't write to it. The 691 counter events do NOT "collapse
bullish structures to short". They CORRECTLY label the regime as down (short
suggested_direction) AND record that the trade payoff would be long.

The bias is real, but the mechanism the spec described is wrong. The actual
mechanism (what COMPLETE_FINDINGS counted) is:

- 1531 bearish_fvg_ob events fire with both fields = short (direct shorts).
- 691 bullish_fvg_ob_counter events fire with suggested=short, trade=long.
- 313 bullish_fvg_ob events fire with both fields = long (direct longs).
- 74 bearish_structural_break events fire with both fields = short.
- 35 bearish_fvg_ob_counter events fire with suggested=long, trade=short.

If the COMPLETE_FINDINGS tally was on `suggested_direction`, it sums to
1531+691+74 = 2296 short / 313+35 = 348 long → 86.8% short, which matches the
"87% short" headline.

If the same tally were on `trade_direction`, it sums to 1531+35+74 = 1640 short /
691+313 = 1004 long → 62.0% short. Materially less biased.

## Implication for R1 fix proposals

R1 must NOT be reasoned as "stop the counter logic from collapsing bullish
structures to short suggestions" — that is not what the code does and not what
reaches the brain prompt.

R1 IS valid as "the public XRAY direction telemetry (suggested_direction in
XRAY_CLASSIFY log) is the raw market-structure label, NOT the trade direction
implied by the setup, and operators counting bias from that field over-state the
short-side dominance compared to what the brain prompt actually sees".

R1 IS also valid as "the brain prompt sees the inverted trade_direction with a
small textual annotation but no structured way to up-weight or down-weight the
counter-trade context; the brain may still under-weight the long signal when
suggested_direction=short looks dominant at the regime level".

The spec's Options A/B/C/D will be re-evaluated under this corrected mechanism in
04_fix_options.md. The fact that the spec's stated mechanism is wrong does not
invalidate the bias evidence — but it does change which fix options are honest
addresses of the bias vs band-aids over a misdiagnosis.

## Confidence multiplier summary

| Setup | trade_direction | Multiplier | mtf gate | Notes |
|---|---|---|---|---|
| BULLISH_FVG_OB | long (= suggested) | 1.0 | mtf >= 0.7 | full conviction |
| BEARISH_FVG_OB | short (= suggested) | 1.0 | mtf >= 0.7 | full conviction |
| BULLISH_FVG_OB_COUNTER | long (opposite suggested=short) | 0.7 | mtf >= 0.4 | counter, lower conviction; alignment via `_counter_alignment` |
| BEARISH_FVG_OB_COUNTER | short (opposite suggested=long) | 0.7 | mtf >= 0.4 | mirror |
| BULLISH_STRUCTURAL_BREAK | long | 1.0 (or 0.8 minor BoS) | none beyond BoS direction match | no counter variant |
| BEARISH_STRUCTURAL_BREAK | short | 1.0 (or 0.8 minor BoS) | none | mirror |
| BULLISH_LIQUIDITY_SWEEP | long | 1.0 | active_sweep + min depth | no counter variant |
| BEARISH_LIQUIDITY_SWEEP | short | 1.0 | mirror | mirror |
| BULLISH_RANGE_BREAKOUT | long | 1.0 | position_in_range >= 0.95 + confluence | no counter variant |
| BEARISH_RANGE_BREAKDOWN | short | 1.0 | position_in_range <= 0.05 + confluence | mirror |
| NONE | "" | — | no fired branch | trade_direction reset to empty |

## Downstream readers of trade_direction (full inventory)

- `src/workers/scanner_worker.py:615-626` populates `XrayBlock.trade_direction` in
  the CoinPackage with a fallback to `suggested_direction`.
- `src/workers/scanner_worker.py:774` and `:839` pass `trade_direction` into
  `state_labeler.label_state(...)` and `compute_interestingness(...)`.
- `src/workers/scanner/state_labeler.py` (line 250+ per grep) — many state-label
  helpers read `trade_direction` to decide which LONG/SHORT label fires.
- `src/brain/strategist.py:1948-1952` renders the Setup line in CALL_A prompt.
- `src/brain/strategist.py:2543-2547` renders the same in the full formatter.
- `src/brain/strategist.py:2217-2220` derives "side" for TIAS recent-loss lookup
  from `trade_direction`.

Downstream readers of suggested_direction (full inventory):

- `src/analysis/structure/setup_scanner.py:105`, `:185`, `:200` — used for
  qualification, description, and ranking inside the X-RAY's own scanner.
- `src/apex/assembler.py:737` — copied into `StructuralData.suggested_direction`
  passed to APEX optimizer.
- `src/workers/scanner_worker.py:775-776` and `:840-841` — passed to state labeler
  and interestingness scorer alongside trade_direction.
- `src/workers/structure_worker.py:217` — surfaced in `XRAY_CLASSIFY` log line
  alongside `trade_direction`.

## Conclusion

The split design is intentional. `suggested_direction` carries the market-structure
label (regime context). `trade_direction` carries the trade-payoff direction the
setup recommends. For all in-direction setups they match. For the two counter
variants they differ.

The brain prompt reads `trade_direction`. APEX assembler.py:737 reads
`suggested_direction`. The XRAY public log emits both. R1's actual surface is
NOT a brain-prompt inversion bug; it's potentially an
asymmetry-of-observability + APEX-feed-loses-counter-context issue.

The 04_fix_options.md file analyses the candidate fixes under this corrected
mechanism.
