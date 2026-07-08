# Phase 1.6 — Smoking Gun Verification

Spec lines 470-479: verify the WR-derived asymmetric threshold claim from the prior report.

## The cited "smoking gun" event

Prior report Section 0.2 cited:
```
XRAY_OVERRIDE_RATIO_DETAIL | flipped_dir=Buy buy_wr=46.0 sell_wr=49.1
                            buy_n=37 sell_n=163 derived_threshold=5.41 xray_ratio=0.15 source=wr
```
as evidence of WR-derived feedback bias.

## Independent verification

### The log event exists

`grep XRAY_OVERRIDE_RATIO_DETAIL` returns **85 events** in the 5.5h window. The cited event (SOLUSDT at 10:20:46.946) is present verbatim:

```
2026-05-18 10:20:46.946 | INFO     | src.workers.strategy_worker:_execute_claude_trade:1803 | XRAY_OVERRIDE_RATIO_DETAIL | sym=SOLUSDT flipped_dir=Buy buy_wr=46.0 sell_wr=49.1 buy_n=37 sell_n=163 derived_threshold=5.41 xray_ratio=0.15 source=wr | did=d-1779099483160
```

### The buy_n / sell_n asymmetry is real

Sample of (buy_n, sell_n) pairs observed:

| buy_n | sell_n | sell_n / buy_n |
|---:|---:|---:|
| 26 | 174 | 6.7× |
| 27 | 173 | 6.4× |
| 28 | 172 | 6.1× |
| 29 | 171 | 5.9× |
| 31 | 169 | 5.5× |
| 32 | 168 | 5.3× |
| 33 | 167 | 5.1× |
| 35 | 165 | 4.7× |
| 36 | 164 | 4.6× |
| 37 | 163 | 4.4× |

The closed-loop feedback bug is **real**: the per-direction WR is being computed over a sample with 4–7× more Sell trades than Buy. This makes Sell WR statistically more stable, and Buy WR more noise-prone.

### The asymmetric threshold derivation IS skewed

Formula (from R3 fix): `derived_threshold = wr_base * (1.0 - flipped_dir_wr / 100.0)` with `wr_base = 10.0`.

- For `flipped_dir=Buy` (brain says Sell, override wants Buy): uses `buy_wr=46.0` → `threshold = 10.0 × (1 - 0.46) = 5.40` (live shows 5.41 — rounding).
- For `flipped_dir=Sell` (brain says Buy, override wants Sell): uses `sell_wr=49.1` → `threshold = 10.0 × (1 - 0.491) = 5.09`.

**Asymmetry**: 5.41 (flip into Buy) vs 5.09 (flip into Sell). 6.3% lower threshold for flipping INTO Sell.

### But: does this asymmetry actually fire on any flip?

Modal `xray_ratio` values seen:

| xray_ratio | source | count |
|---:|---|---:|
| 0.06 | cold | 8 |
| 0.26 | cold | 3 |
| 3.15 | wr | 2 |
| 29.19 | wr | 2 |
| 17.61 | cold | 2 |

The 8 `xray_ratio=0.06` events are FAR below either 5.41 or 5.09 threshold — these flips would NOT have triggered regardless of which direction's threshold applied. Same for 0.26, 0.41, 0.22, 0.19 etc.

Only the high-ratio events (3.15, 29.19, 17.61) are at or near threshold. Of those:
- `xray_ratio=3.15 source=wr` fired 2 times — below 5.09 (Sell threshold) AND below 5.41 (Buy threshold), so flip suppressed regardless.
- `xray_ratio=29.19 source=wr` fired 2 times — vastly above both thresholds, flip triggered.
- `xray_ratio=17.61 source=cold` fired 2 times — above the cold-start fallback (10.0), flip triggered.

The 6.3% asymmetric threshold (5.41 vs 5.09) **does not actually decide any flip in this audit window**. Every flip is either far below both thresholds (suppressed) or far above both (triggered). The threshold asymmetry exists but is inert.

## Verdict

- The closed-loop WR feedback skew (4-7× more Sell sample) is **real and present** in the live data.
- The asymmetric threshold (5.41 vs 5.09) is **real but inert** — no flip in this window is decided by the asymmetry.
- The smoking gun is more of a "latent feedback loop" than an active driver of bias right now.

This is consistent with the prior report's own admission (Appendix B point 3 in `DIRECTION_BIAS_ROOT_CAUSE_AND_FIX_OPTIONS_2026-05-19.md`): "The 6.3% asymmetry will get worse as more Sells accumulate, eventually flipping more borderline cases."

## Implications

- R3 (WR-aware threshold) is doing what it was designed for (data-driven asymmetry, NOT hard-coded). It honors the operator's directive.
- The feedback skew is a long-term concern (months of data accumulation) but not the proximate driver of the current 89% Sell ratio.
- Issue 2 (counter ×0.7) and Issue 4 (asymmetric prompt) are more likely the proximate drivers than the R3 threshold.
- Concern about Issue 1 Phase A2 being a band-aid (Concern 1) is supported by this finding: the existing 3.0× and 5.4× thresholds already have a "guard" semantics; adding another RR floor is layering. But the data shows 8/11 flips in window are collapse-driven (`chosen_rr ≤ 0.3` per the 01_validate_issue1.md spot-check), which Phase A2 *would* catch.
