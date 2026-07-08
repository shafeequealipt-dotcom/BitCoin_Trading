# Top-5 Fix — Cross-Check And Verification Report

This report documents the systematic cross-check performed on 2026-05-05
after Phase 1c (`78b22ac`) and Phase 5b (`8df13d7`) shipped. Verifies
that every fix is correctly implemented, integrated, named, and tested.

## 1. What was implemented this session

| Phase | Commit | Change |
|---|---|---|
| 1c | `78b22ac` | `_check_swept` canonical sweep+reclaim semantic with recency bound |
| 1c-docs | `72deea0` | Phase 7 verification report + Phase 6 trial monitor updates |
| 5b | `8df13d7` | Promote `## TODAY'S PERFORMANCE` and `## TODAY:` to ESSENTIAL |

## 2. Phase 1c — Verification Checklist

### 2.1 Signature changes propagated

| Site | Before | After | Verified |
|---|---|---|---|
| `liquidity.py:341` `_check_swept` | `@staticmethod (zone, highs, lows, n)` | instance method `(self, zone, highs, lows, closes, n)` | YES |
| `liquidity.py:172` caller | `self._check_swept(zone, highs, lows, n)` | `self._check_swept(zone, highs, lows, closes, n)` | YES |
| `LiquidityZone` schema | `swept_at: float` | `swept_at: float`, new `reclaimed_at: float \| None = None` | YES |
| `to_dict()` | excluded `swept_at`, `reclaimed_at` | includes both | YES |

### 2.2 Configuration

| Setting | Type | Default | In code | In config.toml |
|---|---|---|---|---|
| `sweep_recency_bars` | `int` | 30 | YES (settings.py:1668) | YES (config.toml:1184) |
| `sweep_require_reclaim` | `bool` | True | YES (settings.py:1669) | YES (config.toml:1185) |

`StructureSettings` is built via `_build_structure` which uses
`hasattr` filtering so the new fields propagate from config.toml
without any builder-function change required (verified by
`.venv/bin/python -c 'from src.config.settings import StructureSettings; s = StructureSettings(); print(s.sweep_recency_bars, s.sweep_require_reclaim)'`
which returns `30 True`).

### 2.3 Downstream consumers

`grep -rn "\.swept\b\|\.swept_at\|\.reclaimed_at" src/` enumerated all
consumers:

| Consumer | Path | Behavior change |
|---|---|---|
| `nearest_unswept_liquidity` filter | `structure_engine.py:416` | Reads `z.swept` only — gets MORE unswept zones post-fix → can find a zone for the +15 component when previously couldn't |
| `_compute_smc_confluence` liq path | `structure_engine.py:895` | Same — `if lz.swept: continue` skip — but now skips fewer zones, allowing +15 to fire when an unswept zone exists in target direction |
| `detect_sweeps` skip | `liquidity.py:231` | Reads `zone.swept` — now sees zones unswept in the recency-only sense, so it gets to inspect more zones for fresh single-candle sweep events |
| `detect_sweeps` write | `liquidity.py:270, 301` | Writes `zone.swept = True` and `zone.swept_at = float(i)` after detecting a sweep — unchanged |
| `XRAY_LIQ` log | `liquidity.py:180-194` | Extended with `reclaimed=N` count |

No consumer reads `reclaimed_at` outside of tests/observability — the
field is additive and backward-compatible.

### 2.4 Cooperation with `detect_sweeps`

The split between `_check_swept` and `detect_sweeps` is the critical
correctness invariant:

- `_check_swept` → multi-bar violation+reclaim only (skips same-candle)
- `detect_sweeps` → single-candle wick+close-back patterns within
  `sweep_max_age_candles=10`

Verified by integration test (`Test 4`): a single-candle bearish sweep
at index 47 (within last 10 bars) was NOT marked by `_check_swept`
(test confirmed `swept=False` after the call), and `detect_sweeps`
then produced a `high_probability_short` LiquiditySweep on that same
zone. Both contributions to SMC confluence (+15 liq AND +30 sweep)
remain reachable post-fix.

### 2.5 Boundary conditions

- Stale violation outside recency window (200 bars ago, recency=30):
  unswept (verified `test_buy_side_stale_violation_unswept`)
- Violation with no later reclaim within window: unswept (verified
  `test_buy_side_violation_no_reclaim_unswept`)
- Multiple violations with single reclaim: earliest violation pairs
  with first later reclaim (verified
  `test_first_violation_paired_with_first_later_reclaim`)
- `sweep_require_reclaim=False` fallback: wick-only but still
  recency-bounded (verified `TestWickOnlyFallback` 2 tests)

## 3. Phase 5b — Verification Checklist

### 3.1 Marker tuple correctness

```python
_TRIM_ESSENTIAL_MARKERS = (
    "## MARKET DATA", "## ACCOUNT", "## CAPITAL POSITION",
    "## TRADE CANDIDATES", "## OPEN POSITIONS", "## CURRENT POSITIONS",
    "## BYBIT EXCHANGE POSITIONS", "TRADEABLE COINS THIS CYCLE",
    "OVERRIDE — URGENT WATCHDOG ALERTS",
    "## REGIME-SPECIFIC TRADING INSTRUCTIONS",
    "## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)",
    "FUND RULES",            # Phase 5
    "## TODAY'S PERFORMANCE", # Phase 5b — new
    "## TODAY:",              # Phase 5b — new
)

_TRIM_IMPORTANT_MARKERS = (
    "## DIRECTION PERFORMANCE", "## REGIME DIVERGENCE",
    "## STRATEGY HINTS",
    # Phase 5b — TODAY'S PERFORMANCE / TODAY: removed (now ESSENTIAL)
    "## DAILY", "Trading Mode:", "## SETUP",
)
```

The two markers were moved (not duplicated): they appear in
`_TRIM_ESSENTIAL_MARKERS` ONCE and were correctly removed from
`_TRIM_IMPORTANT_MARKERS`. Verified via direct read of
`strategist.py:332-379`.

### 3.2 Classification semantics

`_infer_section_priority` checks ESSENTIAL markers FIRST (line 408),
then IMPORTANT (line 411), then defaults to OPTIONAL. Substring match
on first 200 chars. Both new markers `## TODAY'S PERFORMANCE` and
`## TODAY:` are found in the content header by `marker in head`
(verified in runtime test: classifies as priority 1 = ESSENTIAL).

### 3.3 Test coverage updates

| Test | File | Updated | New |
|---|---|---|---|
| `test_today_performance_is_essential` | `test_priority_classifier.py` | Renamed from `test_today_is_important`, assertion flipped |  |
| `test_today_short_marker_is_essential` | `test_priority_classifier.py` |  | YES |
| `TestTodayPerformanceSurvivesTrim::test_today_performance_survives_under_cap` | `test_priority_trim_inline.py` |  | YES |
| `TestTodayPerformanceSurvivesTrim::test_today_short_marker_survives_under_cap` | `test_priority_trim_inline.py` |  | YES |

## 4. Test results — all green

Targeted suites:

- `tests/test_xray_phase1c/`: 16 passed
- `tests/test_xray_phase1/`: 27 passed (regression — Phase 1 still works)
- `tests/test_stage2_phase4/`: 29 passed (Phase 4 + 5 + 5b combined)
- All XRAY-related: 132 passed
- `tests/test_trading_mode/` + `test_stage2_phase3/`: 47 passed
- `tests/test_phase4/` (TA EMA smoothing): 101 passed

End-to-end integration test (custom synthetic data through full
structure pipeline): 7/7 passed.

Pre-existing broken modules (NOT regression — these have been broken
before this session): `tests/test_phase7/test_executor.py`,
`test_prompt_builder.py`, `test_scheduler.py` — reference removed
modules `src.brain.executor`, `src.brain.prompt_builder`,
`src.brain.scheduler`. Out of scope for this fix.

## 5. Code-quality audit

### 5.1 Type hints

All Phase 1c and 5b code has full type hints:

- `_check_swept(self, zone: LiquidityZone, highs: FloatArray, lows: FloatArray, closes: FloatArray, n: int) -> None`
- `LiquidityZone.reclaimed_at: float | None = None`
- `StructureSettings.sweep_recency_bars: int = 30`
- `StructureSettings.sweep_require_reclaim: bool = True`

### 5.2 Documentation

- `_check_swept` has a multi-paragraph docstring explaining the
  canonical SMC semantic, why same-candle is excluded, fallback mode,
  and side effects.
- New config knobs in `StructureSettings` carry a 25-line comment
  block explaining the fix rationale, the consequences of the legacy
  behavior, and operator tunability.
- `LiquidityZone` dataclass has an updated docstring explaining the
  semantic of `swept_at` vs `reclaimed_at` and what `reclaimed_at=None`
  means for unswept-vs-pending-reclaim.

### 5.3 Logging

- Existing `XRAY_LIQ` debug log extended with `reclaimed=N` count
  (one operator-grep target for confirming Phase 1c is firing).
- Existing `XRAY_CONFIDENCE_DETAIL` (Phase 1) per-coin breakdown log
  remains in place.
- Existing `XRAY_SWEEP` debug log unchanged.
- Existing priority-trim `CLAUDE_PROMPT_TRIMMED` log carries
  `dropped_labels` — operators can confirm `## TODAY'S PERFORMANCE`
  and `## TODAY:` no longer appear in `dropped_labels` post-Phase-5b.

### 5.4 Naming conventions

All identifiers follow the project's existing snake_case for fields
and PascalCase for classes. Constants (`_TRIM_ESSENTIAL_MARKERS`)
match existing pattern. No new external-facing names introduced.

## 6. Integration with the full Top-5 fix sequence

| Issue | Fix path | Status |
|---|---|---|
| Issue 1 (XRAY 0.55 cap) | Phase 1 (94044f7) sweep direction labels + drop 0.5 floor; **Phase 1c (78b22ac) `_check_swept` canonical semantic** | Both shipped |
| Issue 2 (MAINNET framing) | Phase 2 (5d53dc4) SHADOW variant + transformer-driven derivation | Shipped |
| Issue 3 (shorts zero score) | Phase 3 (6d1e28e) Path C judgment-based prompt language | Shipped |
| Issue 4 (Context flapping) | Phase 4 (d7102b1) `confidence_ema_alpha=0.4` smoothing at TAEngine source | Shipped |
| Issue 5 (FUND RULES trim) | Phase 5 (b25148c) FUND RULES marker; **Phase 5b (8df13d7) TODAY'S PERFORMANCE marker** | Both shipped |

All five issues now have shipped fixes. Phase 1c addresses the
deferred root cause for Issue 1. Phase 5b addresses the secondary gap
in Issue 5 (the audit's other dropped labels).

## 7. Pending — for the operator

System is paused. Worker pid 399 is on PRE-Phase-1c code. To deploy:

1. Stop the running worker process.
2. Start a fresh `python workers.py` from the venv (loads new code).
3. Edit `data/layer_state.json` to set `user_stopped: false,
   layer_active.2: true, layer_active.3: true`.
4. Watch `data/logs/workers.log` for first cycle:
   - `XRAY_LIQ` should show non-zero `reclaimed=N` counts
   - `XRAY_CLASSIFY_SUMMARY` should show confidence p50/p95 spread
     above the previous 0.55 lock
5. Watch `data/logs/brain.log`:
   - First Stage 2 prompt header should read `MODE: SHADOW (paper trading on real Bybit market data)`
   - `CLAUDE_PROMPT_TRIMMED` events (if any) should NOT include
     `FUND RULES`, `## TODAY'S PERFORMANCE`, `Trades today:`, or
     `Daily PnL:` in `dropped_labels`
   - `STRAT_CALL_A_END` should eventually return non-empty
     `new_trades` once XRAY confidence reaches the implicit STRONG bar

Trial procedure: `dev_notes/top5_fix/phase6_trial.md`.
Verification template: `dev_notes/top5_fix/phase7_verification_report.md`.
