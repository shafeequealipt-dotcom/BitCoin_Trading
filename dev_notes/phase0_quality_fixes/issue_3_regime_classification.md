# Phase 0 — Quality Issue 3: RegimeWorker Classification Verification

## A — Current observed behaviour

**Live measurement from log tail:**
- Global regime: `REGIME_GLOBAL | rgm=trending_down conf=0.63 adx=31.4 chop=39.5` — sustained throughout observation
- ADX 31.4 (>25 → trending threshold), choppiness 39.5 (<45 → confirms trend), confidence 0.63 (= min(31.4/50, 1.0))
- **Zero `REGIME_PERCOIN` events in INFO-level log tail.** Per-coin regime distribution invisible to operator.
- `REGIME_FLIP` events only emitted on hysteresis transitions — sample shows few/none in steady state

The data is being computed (`regime_worker.py:164-240` runs `detect_per_coin()` for the universe minus primary) but the logs only carry global state and per-coin divergence count (no per-coin detail).

## B — Expected behaviour

- Per-coin regime distribution visible per cycle (mixed: ≥3 categories represented)
- For 5 visually-divergent coins (BTC trending, alts ranging), per-coin regime ≠ global
- 0–2 regime flips per coin per hour — not zero (over-sticky), not flapping
- ScannerWorker criterion 3 (regime alignment) has non-zero pass and non-zero fail counts

## C — Root cause

**Two issues:**

1. **Hardcoded thresholds.** `src/strategies/regime.py:117-145` thresholds are NOT in `config.toml`:
   - `trending_adx_threshold` (≈25 inferred)
   - `ranging_adx_threshold` (≈25-30)
   - `dead_adx_threshold` (≈10)
   - `ranging_choppiness_threshold` (≈50-60)
   - `volatile_atr_percentile` (≈100)
   - `dead_volume_ratio` (≈0.5)
   - Hysteresis count (`if new_count >= 2` at line 185) hardcoded — not config

   Operators cannot tune without redeploying.

2. **Observability gap.** `REGIME_PERCOIN` distribution is computed but not emitted. The aggregate log at line 200-211 counts divergent coins (vs global) but doesn't list per-coin regimes for operator visibility.

3. **Verification gap.** Need to confirm `ScannerWorker` reads `_per_coin_regimes` (per-coin), not just global. Per the prompt's risk: "if per-coin regime not being set OR not being read, brain defaults to global".

## D — Verification approach (post-fix)

| Metric | Measure | Target |
|---|---|---|
| Per-coin regime distribution | `REGIME_PERCOIN_SUMMARY` over 1 hour | ≥3 of 5 categories represented |
| Per-coin overrides global | for 5 hand-picked coins, compare REGIME_PERCOIN to REGIME_GLOBAL | ≥3 differ |
| Stickiness reasonable | `REGIME_FLIP` count per coin per hour | 0-2 |
| ScannerWorker criterion 3 effective | `SCANNER_FILTER_AGGREGATE` | both pass and fail counts > 0 |
| Thresholds in config | grep config.toml `[regime]` | all values present + comments |

## E — Rollback path

Phase 3 adds config exposure + observability. Behavioral change is config defaults matching current hardcoded values — operators see the same behavior unless they tune. Rollback: `git revert <phase3-commits>` reverts cleanly.

## Files end-to-end mapped

| File | Lines | Role |
|---|---|---|
| `src/workers/regime_worker.py` | 23-277 (RegimeWorker), **164-240 (per-coin detection), 200-211 (divergence aggregate — fix target)** | tick() loop; compute global + per-coin; persist to DB |
| `src/strategies/regime.py` | 21-217 (RegimeDetector), **117-145 (classification thresholds — fix target), 162-207 (hysteresis), 185 (count=2 — fix target)** | Detection logic + stickiness |
| `src/config/settings.py` | (RegimeSettings dataclass) | **Fix target — extend with new threshold fields** |
| `config.toml` | (existing `[regime]` section, partial) | **Fix target — add new keys** |
| `src/workers/scanner_worker.py` | (criterion 3 logic) | **Verification target — confirm reads `_per_coin_regimes`** |

## Phase 3 fix outline (preview)

Three atomic commits:
1. Extend `RegimeSettings` with the 6 threshold fields + hysteresis count; add `[regime]` keys with current hardcoded values as defaults; validate via `__post_init__`.
2. Modify `regime.py` to read from settings instead of hardcoded constants; add `REGIME_PERCOIN`, `REGIME_FLIP`, `REGIME_PERCOIN_SUMMARY` emits.
3. Audit `ScannerWorker` criterion 3 — confirm it reads per-coin (not just global). Commit either the verification doc OR the fix if needed.
