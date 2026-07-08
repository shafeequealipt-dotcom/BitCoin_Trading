# Gap 1 Phase 1 Synthesis — `is_structurally_invalid` consumer decision

Date: 2026-05-19  
Trial-data dependency note: spec Anti-pattern 10 (line 751) warns that "some Gap 1 decisions depend on full trial data." The Phase 1A/1B 48-72h trial T0 was 13:44:48 UTC; we're at T0+1h. Layer 2/3 are OFF. The trial data Gap 1 needs is from the EARLIER 2026-05-19 10:55-13:04 monitoring window, preserved in the rotated `workers.2026-05-19_11-26-15_574407.log`.

## Step 1.1 — Audit clamp activations in trial data

The XRAY_LEVELS DEBUG event (which carries `invalid=Y/N` per placement) is filtered at INFO threshold; the rotated trial log captured 0 lines. Clamp activations are inferred from `XRAY_DIR_FLIP` events with `rr_original` at the clamp-floor signature (≤0.2 = direct 0.5% floor / 2.5% SL math; 0.3-0.5 is ambiguous).

### Clamp-signature flips (rr_original ≤ 0.2) — definitive clamp activations

| Time | Symbol | Direction | rr_original | rr_flipped | Ratio | Final trade | Outcome |
|---|---|---|---|---|---|---|---|
| 11:34:30 | MNTUSDT | Buy→Sell | 0.2 | 5.4 | 21.6x | Sell qty=2005.4 | **WIN +$4.41** at 11:44:47, bybit_tp_hit (10.3 min hold) |
| 12:02:13 | MNTUSDT | Buy→Sell | 0.2 | 4.5 | 18.1x | Sell qty=797.3 | **LOSS -$2.23** at 12:54:38, wd_timeout (52.4 min hold) |

**n=2, 1W/1L, net +$2.18.**

### Ambiguous-signature flips (rr_original ~0.5)

| Time | Symbol | Direction | rr_original | rr_flipped | Ratio | Final trade | Outcome |
|---|---|---|---|---|---|---|---|
| 12:33:19 | ONDOUSDT | Sell→Buy | 0.5 | 3.9 | 7.9x | Buy qty=3695 | WIN +$3.33 at 13:08:31, system_close (35.2 min hold) |

Could be a weak-but-real edge OR a less-extreme clamp activation. Without DEBUG logs, can't disambiguate.

### Statistical inference

n=2 definitive clamp events is **too small** to draw any statistical conclusion. 1W/1L is consistent with:
- Random outcome (no relationship between clamp flag and PnL)
- Real edge (clamp-flipped Sells succeed when structural Sell is genuine)
- Anti-edge (clamp-flipped trades will lose more on a larger sample)

**Decisive evidence is NOT available.** Anti-pattern 10 specifically warns against shipping Gap 1 behavioral fixes without trial evidence.

## Step 1.2 — Path decision

| Path | Aim-bias risk | Trial evidence required | Implementation effort | Verdict |
|---|---|---|---|---|
| A — No consumer | NONE | None | 0 | Acceptable but loses observability opportunity |
| B — Logging-only consumer | NONE | None | LOW | **RECOMMENDED** |
| C — APEX sizing reduction (×multiplier when invalid) | LOW only if config-driven with default 1.0 | Required to set non-1.0 multiplier responsibly | MEDIUM | Premature without trial signal |
| D — APEX gate skip when both directions invalid | HIGH per Rule 4 anti-pattern | Required by spec | HIGH (needs Gap 2 bidirectional flags as prereq) | **Rejected per anti-pattern (no trial signal of harm)** |

### Why Path B is recommended

- **Closes Gap 1's information gap** without changing behavior.
- **Aligns with spec Rule 4**: "If a fix is purely restrictive without making decisions better, reject." Path B makes decisions better via visibility.
- **Aligns with operator directive**: "Information should be SURFACED to the layer that needs it, not hidden." Path B surfaces clamp activations to the operator's log stream.
- **Survives any future re-evaluation**: when more trial data accumulates, the operator can grep `XRAY_CLAMP_DETECTED` events and pair with `DL_TRADE` outcomes to compute clamp-trade WR vs non-clamp WR. If a real anti-edge emerges, the operator can then justify Path C or D.
- **Aim-bias 5/5 YES**: zero behavior change, pure observability, single emit site, single architectural layer (1B).

### What Path B does NOT do

- Does NOT block any trade
- Does NOT downsize any trade
- Does NOT inject any "avoid invalid" guidance into the brain prompt (Gap 2 already surfaces the flag informationally)
- Does NOT change watchdog or close-side logic

## Step 1.3 — Recommended implementation (Path B)

**Single emit site in `src/analysis/structure/structure_engine.py`**, after the bidirectional flags are populated (Gap 2 marshalling code at ~line 357):

```python
if structural_placement and (
    structural_placement.is_long_invalid or structural_placement.is_short_invalid
):
    log.info(
        f"XRAY_CLAMP_DETECTED | sym={symbol} "
        f"long_invalid={structural_placement.is_long_invalid} "
        f"short_invalid={structural_placement.is_short_invalid} "
        f"rr_long={structural_placement.rr_long:.2f} "
        f"rr_short={structural_placement.rr_short:.2f} "
        f"chosen_dir={structural_placement.direction or 'n/a'}"
    )
```

INFO level. Emits once per cycle per coin when either side hit the clamp floor. Operator can:
- `grep XRAY_CLAMP_DETECTED data/logs/workers.log | awk '{print $4}' | sort | uniq -c` to see clamp frequency by symbol
- Cross-reference with `DL_TRADE` events to manually compute clamp-trade outcomes
- Build longer-term trial dataset to inform future Path C/D decision

## Aim-bias 5/5 evaluation

1. **Preserves trade frequency?** YES — no blocking.
2. **Preserves aggression?** YES — no behavior change.
3. **Improves decision quality?** YES — operator gains visibility for future data-driven policy decisions.
4. **Preserves passive-close advantage?** YES — close path untouched.
5. **Respects structural separation of concerns?** YES — emit at the layer that computes the flag (Layer 1B), no cross-layer reach.

## Trial behavior specification

After deployment:
1. Restart picks up the new code.
2. When a coin's structure_engine cycle hits a clamp on either direction, `XRAY_CLAMP_DETECTED` fires at INFO level.
3. `grep XRAY_CLAMP_DETECTED data/logs/workers.log` returns matching events.
4. Operator can later query `DL_TRADE` outcomes for any symbol that triggered a clamp event in the same cycle.
5. NO `XRAY_CLAMP_DETECTED` events fire when both placements are healthy.

## Recommendation

**Path B**: ship a logging-only consumer. Implementation surface: one log emit in `structure_engine.py`. Zero behavior change.

If after 30+ days of trial data the operator wants to upgrade to Path C (sizing reduction) or Path D (skip both-invalid), the data accumulated via Path B's emits + the existing `DL_TRADE` records provide the audit trail.
