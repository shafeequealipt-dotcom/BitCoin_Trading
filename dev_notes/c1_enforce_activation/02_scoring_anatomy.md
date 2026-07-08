# C1 — Phase 1.2 Scoring Anatomy

This document maps the brain-close scoring system end-to-end. Two modules: the pure-function scorer (`src/risk/wd_brain_scoring.py`) and the watchdog intercept that calls it (`src/workers/position_watchdog.py:3419–3665`). Together they replace nothing — they wrap the existing brain close path with a composite-score quality gate.

## Module 1 — `src/risk/wd_brain_scoring.py`

Pure function, no I/O, no `time`, no `datetime`. Deterministic and unit-testable.

### Public API

- `compute_brain_close_score(*, pnl_pct, time_remaining_s, age_s, velocity_pct_per_s, sl_consumption_pct, xray_match, reasoning_text, threshold=6.0, weights=None) -> BrainCloseScore` (line 288).
- `BrainCloseScore` dataclass (line 145): `factors`, `composite`, `threshold`, `recommendation`, `notes`. `.as_log_dict()` flattens to a single-line log dictionary (line 153).
- `BrainCloseScoreFactors` dataclass (line 114): per-factor pnl_pct/bucket/factor, time_remaining_s/bucket/factor, etc.
- `DEFAULT_THRESHOLD = 6.0` (line 36).
- `DEFAULT_WEIGHTS` (lines 38–84): the seven factor tables.
- `STRUCTURAL_KEYWORDS` frozenset (line 86): the reasoning-bucket keyword set.

### Factor tables (verified against `wd_brain_scoring.py:38–84`)

`pnl` (line 39):
- `strong_winner` +3.0 (pnl_pct > 1.0)
- `mild_winner` +1.5 (0.3 < pnl_pct ≤ 1.0)
- `weak_winner` +0.5 (0 < pnl_pct ≤ 0.3)
- `shallow_loser` −3.0 (−0.5 ≤ pnl_pct ≤ 0)
- `moderate_loser` −1.0 (−1.5 ≤ pnl_pct < −0.5)
- `deep_loser` +0.5 (pnl_pct < −1.5)  ← intentional: SL handles deep losers

`time_remaining` (line 47):
- `deep` −2.0 (>20 min)
- `moderate` −1.0 (10–20 min)
- `shallow` 0.0 (5–10 min)
- `imminent` +1.0 (<5 min)

`age` (line 53):
- `infant` −2.0 (<3 min)
- `young` −1.0 (3–10 min)
- `mature` 0.0 (10–30 min)
- `aged_losing` +1.0 (>30 min and pnl_pct < 0)

`velocity` (line 59) — pnl_pct per second:
- `strong_positive` −2.0 (v ≥ 0.01)
- `mild_positive` −1.0 (0.002 ≤ v < 0.01)
- `stationary` 0.0
- `mild_negative` +1.0 (−0.01 < v ≤ −0.002)
- `strong_negative` +2.0 (v ≤ −0.01)

`sl_consumption` (line 66):
- `spacious` −2.0 (0–30%)
- `comfortable` −1.0 (30–60%)
- `tight` 0.0 (60–80%)
- `imminent` +1.0 (>80%)

`xray` (line 72):
- `supports` −2.0 (xray direction matches position side)
- `neutral` / `stale` / `unavailable` 0.0
- `broken` +2.0 (xray direction opposes position side)

`reasoning` (line 79):
- `structural` +2.0 (contains any keyword from STRUCTURAL_KEYWORDS)
- `vague` +0.5 (non-empty, no structural keywords)
- `empty` 0.0

`STRUCTURAL_KEYWORDS` (line 86): `structure`, `invalidate*`, `broken`, `breakdown`, `breakout`, `setup`, `regime`, `reversal`, `fvg`, `ob`, `order block`, `support`, `resistance`, `trendline`, `trend reversal`.

### Composite formula and recommendation (lines 370–385)

```
composite = pnl_factor + time_factor + age_factor + velocity_factor
          + sl_factor + xray_factor + reasoning_factor

if composite >= threshold:     recommendation = "execute"
elif composite >= 0:           recommendation = "reject"
else:                          recommendation = "reject_and_tighten"
```

Plain sum, no normalisation, no weighting per layer beyond the bucket weights. Threshold is `DEFAULT_THRESHOLD` (6.0) unless caller overrides.

### Maximum and minimum reachable composites

Theoretical max (all positive weights selected) = 3.0 + 1.0 + 1.0 + 2.0 + 1.0 + 2.0 + 2.0 = **+12.0**.
Theoretical min (all negative weights selected) = −3.0 − 2.0 − 2.0 − 2.0 − 2.0 − 2.0 + 0.0 = **−13.0** (deep_loser uses +0.5 not the worst − so min from pnl side is −3.0; reasoning min is 0.0 — empty reason).

Achievable max = +12.0 requires: strong winner, imminent deadline, aged_losing (impossible by definition — winner ≥0 contradicts aged_losing requiring pnl<0), strong_negative velocity (also contradicts winner), imminent SL, broken XRAY, structural reasoning. Mutually exclusive constraints reduce achievable max well below +12.

Practically, the highest plausible composite for a winner is around +7 to +8 (strong_winner, mature/imminent, mild_negative velocity unwinding, tight/imminent SL, broken xray, structural reasoning).

For a loser, max plausible is around +5 to +6: shallow_loser caps pnl at −3.0; imminent deadline gives +1.0; aged_losing gives +1.0; strong_negative velocity gives +2.0; imminent SL gives +1.0; broken xray gives +2.0; structural reasoning gives +2.0 = **+6.0**.

This explains the historical distribution. The 28 scored events were all losers; their plausible upper bound is +6.0; their actual maximum was +1.5. The 6.0 threshold thus only fires for either strong winners (a profit-take signal) or a maximally-fundamental loser exit (aged + accelerating + at SL + structurally broken + clearly reasoned).

### Input sanitisation (lines 336–352)

- NaN `pnl_pct` → 0.0, note added.
- NaN `time_remaining_s` → 0.0, note added.
- Negative `time_remaining_s` → 0.0.
- NaN or negative `age_s` → 0.0, note added.
- `velocity_pct_per_s` `None` or NaN → 0.0, note added.
- `sl_consumption_pct` `None` or NaN → 50.0 (midpoint), note added.

Fail-soft on all numeric inputs. The function cannot raise on bad data.

## Module 2 — Watchdog intercept (`src/workers/position_watchdog.py:3419–3665`)

The intercept lives inside `_execute_strategic_actions()` and is invoked once per brain-vote per strategic-actions iteration.

### Gating (lines 3428–3450)

- `if act in ("close", "take_profit"):` — only close votes get scored; tighten/hold/profit_take_partial bypass.
- `WD_SCORING_PATH_REACHED` (line 3443) fires unconditionally for every close/take_profit before the scoring runs, so logs can correlate close volume vs scored volume.
- `_scoring_enabled` reads `settings.watchdog.wd_brain_scoring_enabled` (default True).
- `_enforce_flag` reads `settings.watchdog.wd_brain_scoring_enforce` (default False).

If `_scoring_enabled` is False the scoring path is skipped entirely and brain's close fires immediately — the kill switch.

### Factor collection (lines 3485–3612)

Resolved per-call, each guarded by try/except:

- `_pnl_pct` from `_calculate_pnl_pct(pos, mark_price)`; falls back to 0.0.
- `_sl_consumption` from `_calculate_sl_proximity(pos, current_price)`; falls back to `None`.
- `_time_remaining_s` from `coordinator.get_trade_plan(symbol).remaining_minutes * 60`; falls back to 0.0.
- `_age_s` from `coordinator.get_age_seconds(symbol)`; falls back to 0.0.
- `_velocity` — first preference is `self._td_states[symbol].prev_velocity` (only populated for loser-lane positions); fallback is the derived velocity `(pnl_now - pnl_prev) / (ts_now - ts_prev)` using `self._brain_score_prev_pnl` cache (line 341).
- `_xray_match` — compares `structure_cache.get(symbol).trade_direction` to position side; emits `stale` if age_seconds > 60, `supports` / `broken` / `neutral` otherwise; `unavailable` on exception.
- `reasoning_text` — the brain's `PositionAction.reason` string.

The cache `_brain_score_prev_pnl` survives across ticks but is pruned in `_serialize_state`/`_reset_state` (lines 4362–4410). It contains only `(pnl_pct, monotonic_ts)` tuples per symbol — small footprint.

### Scoring call and log emission (lines 3603–3619)

```python
_score = compute_brain_close_score(
    pnl_pct=_pnl_pct,
    time_remaining_s=_time_remaining_s,
    age_s=_age_s,
    velocity_pct_per_s=_velocity,
    sl_consumption_pct=_sl_consumption,
    xray_match=_xray_match,
    reasoning_text=reason or "",
    threshold=_threshold,
)
log.warning(
    f"WATCHDOG_CLOSE_SCORE_COMPUTED | sym={symbol} {factor_line} | {ctx()}"
)
```

The flattened log uses `_score.as_log_dict()` so every per-factor bucket and weight is visible in logs.

### Branching (lines 3621–3653)

```
if not _enforce:
    log.info("WD_CLOSE_SCORE_LOG_ONLY | composite=X would_be=R")
    # falls through to existing close
else:
    if recommendation == "execute":
        log.warning("WATCHDOG_CLOSE_EXECUTED | composite=X")
        # falls through to existing close
    elif recommendation == "reject":
        log.warning("WATCHDOG_CLOSE_REJECTED | composite=X threshold=T")
        _scoring_skip_close = True
    else:  # reject_and_tighten
        log.warning("WATCHDOG_CLOSE_OVERRIDE_TIGHTEN | composite=X")
        if _pos_for_score is not None:
            await self._tighten_sl_breakeven_30pct(_pos_for_score)
        _scoring_skip_close = True

if _scoring_skip_close:
    continue  # skips the existing close call below
```

The skip is via a local flag `_scoring_skip_close` that gates the `continue` (line 3664). The existing close logic at line 3667 onward only runs when the flag is False — under log-only mode, under enforce-execute, or when the scoring path is disabled.

### Fail-soft (lines 3654–3662)

Any exception inside the scoring path:
- Logs `WD_BRAIN_SCORE_FAIL | sym=X err='msg' enforce=skipped`.
- Sets `_scoring_skip_close = False` implicitly (it was initialised at line 3427).
- Brain's close fires.

This ensures a scoring bug cannot block a legitimate close path.

## Module 3 — SL-tightening fallback (`position_watchdog.py:1173–1226`)

`_tighten_sl_breakeven_30pct(pos)`:

- Reads `entry = pos.entry_price`, `current_sl = pos.stop_loss`.
- Returns False if either is non-positive.
- Computes `delta = 0.30 * (entry - current_sl)`.
- Computes `new_sl = current_sl + delta`.
- The same arithmetic works for both BUY (`current_sl < entry`, delta positive, new_sl closer to entry from below) and SELL (`current_sl > entry`, delta negative, new_sl closer to entry from above).
- Delegates to `_push_sl_to_shadow(symbol, new_sl, plan, current_shadow_sl, direction, source="wd_brain_scoring")`.

The push helper enforces tighter-only and break-even safety. See `03_enforce_path_verification.md` for the per-line trace of the push helper.

## Settings wiring (`src/config/settings.py`)

`WatchdogSettings` (lines 1018–1020):
```python
wd_brain_scoring_enabled: bool = True
wd_brain_scoring_enforce: bool = False
wd_brain_scoring_threshold: float = 6.0
```

Builder mapping (lines 3797–3805) reads each from `config.toml` with the same defaults.

`config.toml` (lines 530–532) carries the current values: `enabled = true`, `enforce = false`, `threshold = 6.0`. Inline comment at lines 514–528 documents the Phase 1 → Phase 2 transition contract.

## Tests (existing)

- `tests/test_wd_brain_scoring.py` — pure-function unit tests covering historical worked examples, NaN edges, weight overrides, structural keywords, stale XRAY.
- `tests/test_wd_scoring_thesis_invalidation_integration.py` — pure-function scenario tests for thesis_invalidation closes under multiple state combinations.

Both test files exercise `compute_brain_close_score()` directly without the watchdog harness. There is no test of the watchdog-side `_handle_claude_action` enforce branches. That gap is Step 1.5c.

## Conclusion of Phase 1.2

The scoring module is small, pure, deterministic, and fully sanitised. The watchdog intercept guards every call with try/except. The four execution branches (log-only / execute / reject / reject_and_tighten) are wired with distinct log signatures so the operator can attribute every close to one branch. The SL-tightening fallback delegates to a battle-tested push helper. The settings and config plumbing is complete. The system is ready for Phase 1.3 enforce-path verification.
