# Phase 1c — `_check_swept` Canonical Sweep+Reclaim Semantic

## Why this phase exists

The original Phase 1 (`94044f7`) fixed two real issues in the XRAY confidence
formula:

1. `liquidity._classify_signal` returned a directionless `"weak_signal"`
   string, dropping the +30 sweep contribution to SMC confluence on weak-but-
   real reversals.
2. `structure_engine.classify_setup` floored confidence at 0.5
   via `max(smc_01, 0.5)` — this artificially promoted weak SMC values.

Both fixes shipped. But the Phase 0 baseline captured immediately after
showed the universe-wide cap **persisted** at 0.55:

| Stat | Phase 0 baseline value |
|---|---|
| p25 | 0.55 |
| p50 | 0.55 |
| p75 | 0.55 |
| p95 | 0.70 |

The p25 = p50 = p75 = 0.55 lock indicated a deeper cause beyond the
floor-and-direction-label fix. Phase 7 verification report Section 8
explicitly listed this as deferred work:

> "Modifying liquidity-sweep `_check_swept` historical-window semantics (1B deferred)."

Phase 1c addresses that deferred item.

## Root cause located

`src/analysis/structure/liquidity.py:340-354` — pre-fix `_check_swept`:

```python
@staticmethod
def _check_swept(zone, highs, lows, n) -> None:
    """Check if a zone has been swept historically."""
    for j in range(n):
        if zone.zone_type == "buy_side" and highs[j] > zone.level:
            zone.swept = True
            zone.swept_at = float(j)
        elif zone.zone_type == "sell_side" and lows[j] < zone.level:
            zone.swept = True
            zone.swept_at = float(j)
```

Iterates the full candle window (`range(n)`, typically 200 bars) and marks
the zone swept if **any** historical bar wicked through the level. Over a
~200-candle H1 window, virtually every level above current price has been
wicked at some point and every level below has been pierced. The result:
nearly every zone enters `_compute_smc_confluence` already-marked swept.

Two consequences cascade:

- `_compute_smc_confluence` at `structure_engine.py:893-900` checks
  `if lz.swept: continue` and skips → the +15 unswept-liquidity contribution
  is 0 universe-wide.
- `LiquidityMapper.detect_sweeps` at `liquidity.py:218-219` also checks
  `if zone.swept: continue` → no fresh sweep events get produced for these
  zones either, dropping the +30 active-sweep contribution.

Net SMC max for the dominant FVG_OB setup type:
FVG (25) + OB (30) + 0 + 0 = **55**, normalized = 0.55. The confidence
formula `min(mtf_score_01, smc_01)` then caps at 0.55 for any coin with
MTF score ≥ 0.55.

## Fix shape (operator-confirmed: sweep + reclaim with recency)

`_check_swept` now requires the canonical SMC sweep+reclaim pattern within
a configurable recency window:

- **Buy-side zone**: violation `highs[j] > zone.level`, then a later bar
  `closes[k] < zone.level` for some `k > j` within `sweep_recency_bars`.
- **Sell-side zone**: mirror — violation `lows[j] < zone.level`, then
  later `closes[k] > zone.level`.
- Same-candle reclaim (close-back-inside in the same candle as the
  violation) is **intentionally not caught** here — that pattern is
  detected by `LiquidityMapper.detect_sweeps`, which produces the
  `LiquiditySweep` record powering the +30 active-sweep component.
  If `_check_swept` short-circuited the single-candle pattern, the
  `detect_sweeps` skip-already-swept logic at line 219 would prevent
  the sweep event from being recorded.
- `sweep_require_reclaim=False` falls back to wick-only detection
  within the recency window (still bounded — an improvement over the
  unbounded legacy scan).

Stale violations beyond the recency window leave the zone unswept on
the assumption that liquidity re-forms over time.

## Files modified

| File | Change |
|---|---|
| `src/analysis/structure/liquidity.py` | Rewrote `_check_swept` (lines 340-end). Added `closes` parameter. Updated caller in `detect_zones` (line 169). Extended `XRAY_LIQ` debug log to include `reclaimed` count. |
| `src/analysis/structure/models/structure_types.py` | Added `LiquidityZone.reclaimed_at: float \| None = None`. Updated `to_dict()` to include `swept_at` and `reclaimed_at`. |
| `src/config/settings.py` | Added `sweep_recency_bars: int = 30` and `sweep_require_reclaim: bool = True` to `StructureSettings` with detailed comment block. |
| `config.toml` | Added `sweep_recency_bars = 30` and `sweep_require_reclaim = true` under `[analysis.structure]` with rationale comment. |
| `tests/test_xray_phase1c/test_check_swept.py` | New test file — 16 unit tests covering recency window, reclaim requirement, same-candle exclusion, fallback mode, multi-violation pairing, integration with `detect_zones`, and schema round-trip. |

## Test coverage

16 new tests in `tests/test_xray_phase1c/test_check_swept.py`:

- **TestRecencyWindow** (3): stale violations outside window stay unswept; recent violation+reclaim is processed.
- **TestRequireReclaim** (3): violation without reclaim leaves zone unswept; violation+reclaim within window marks swept; sell-side mirror works.
- **TestSameCandlePattern** (2): single-candle sweeps not pre-empted (left for `detect_sweeps`).
- **TestWickOnlyFallback** (2): `sweep_require_reclaim=False` mode still bounded by recency window.
- **TestQuietZone** (2): zones with no violation activity stay unswept on both sides.
- **TestMultipleViolations** (1): earliest violation pairs with first later reclaim.
- **TestDetectZonesIntegration** (1): `detect_zones` call path threads `closes` through.
- **TestSchema** (2): `LiquidityZone.to_dict` round-trips `reclaimed_at` for both unswept and swept states.

Combined with the existing 27 Phase 1 tests, the broader 132-test XRAY
regression suite remains green.

## Expected operational impact

After the fix is deployed and Layer 2/3 are re-enabled:

- Zones whose violation+reclaim is older than `sweep_recency_bars=30` will
  be unswept again — the +15 unswept-liquidity contribution can fire.
- Zones with active sweep+reversal in the most recent 10 bars will produce
  `LiquiditySweep` records via `detect_sweeps` — the +30 active-sweep
  contribution can fire.
- The previously universe-wide 0.55 confidence cap for FVG_OB setups
  should distribute upward: high-conviction setups with full
  FVG+OB+liq+sweep confluence reach SMC=100 → smc_01=1.0 → `conf =
  min(mtf_score_01, 1.0) = mtf_score_01`. With `mtf_score_01` typically
  in 0.55-0.85, confidence values should now span 0.55-0.85 instead of
  flatlining at 0.55.

## Trial monitoring

The Phase 6 trial (`phase6_trial.md`) M3 monitor — XRAY confidence
distribution — is the primary signal for Phase 1c effectiveness:

- Pre-1c baseline: p25 = p50 = p75 = 0.55, p95 = 0.70, max = 0.80
- Post-1c target: p25 ≥ 0.55, p50 in 0.60-0.70, p95 ≥ 0.70, max ≥ 0.85,
  and the count of confidence values > 0.70 should be non-trivial
  (>10% of the universe).

Operator can grep `XRAY_LIQ` in `data/logs/workers.log` post-deploy:
the new `reclaimed=N` field shows how many zones triggered the canonical
sweep+reclaim path. If `reclaimed` is consistently 0 across many cycles,
the recency window or reclaim threshold may need tuning.

## Rollback

`git revert <Phase 1c sha>` cleanly reverses all five files. The two
new config knobs default to their pre-fix-equivalent behavior under
`sweep_require_reclaim=false` + a very large `sweep_recency_bars`
(though the fix's strict bounds are the recommended live values).
