# Phase 8 — End-to-End Pipeline Check Through the Real Project

Date: 2026-05-22. Final integration verification that exercises every layer of the real production code path — from `config.toml` parsing through the `Settings._load_fresh` entry point used by `workers.py`, through the `WorkerManager`'s DI wiring, into the actual `StrategyWorker` and `PositionWatchdog` constructors, and out through the `compute_brain_close_score` pure function and the log emissions consumed by operator dashboards.

## H1 — Goal

Move beyond unit tests and source-grep checks. Drive the real settings loader, instantiate the real worker classes with the real DI signatures, observe the actual boot-sentinel log emissions, and validate the high-conviction definition + scoring composite against the real `StructuralAnalysis` and `MarketRegime` types from the project's type system.

## H1 — Pipeline 1: DI Wiring Trace

`workers.py` is the production entry point. It instantiates `Settings._load_fresh()` then constructs `WorkerManager(settings, db)`. The `WorkerManager.initialize()` async method registers a service container at `self._services` (keyed string registry — confirmed at `src/workers/manager.py:56–57`) and constructs every worker by name.

Two worker constructions are relevant to this audit:

- `PositionWatchdog` at `src/workers/manager.py:1418–1453`. Receives 19 named kwargs covering every dependency the P0-3 hard-floor + brain-vote-factor logic needs: `settings`, `position_service`, `market_service`, `coordinator`, `thesis_manager`, `structure_cache`, `ensemble_state_cache`, etc. No new dependencies introduced by P0-3 — existing fields are read.
- `StrategyWorker` at `src/workers/manager.py:1722–1737`. Receives `settings`, `regime_detector`, `services=self._services` (the full container) plus the worker-specific dependencies. The P0-2 high-conviction logic reads `self.regime_detector._per_coin_regimes` (existing access pattern shared by 4 other production sites) and `services["structure_cache"]` (existing service).

DI is clean injection-by-name with no new dependency edges.

## H1 — Pipeline 2: Real Settings Round-Trip + Boot Sentinels

Executed via `Settings._load_fresh(config_path="config.toml")` — the same call path `workers.py:127` uses. Result:

```
risk.xray_high_conviction_protection_enabled = True
risk.xray_dir_flip_threshold_ratio          = 3.0
watchdog.wd_brain_scoring_enabled           = True
watchdog.wd_brain_scoring_enforce           = True
watchdog.wd_brain_scoring_threshold         = 6.0
watchdog.wd_hard_risk_floor_sl_pct          = 85.0
```

Constructed `StrategyWorker(settings=settings, ...)` with the real `Settings` object and stub services. Captured the boot output:

```
P0_2_SENTINEL | high_conviction_protection=True flip_threshold=3.00 dual_logging=removed canonical_event=DIRECTION_DECISION
```

Constructed `PositionWatchdog(settings=settings, ...)` with the same real `Settings`. Captured:

```
WD_SCORING_ENFORCE_ACTIVE | scoring_enabled=True enforce=True threshold=6.00
P0_3_SENTINEL | brain_vote_factor=on hard_risk_floor_sl_pct=85.0 threshold=6.00 enforce_mode=True
```

Both sentinels fire with the expected values. Pre-existing `WD_SCORING_ENFORCE_ACTIVE` still fires (additive change, no replacement).

## H1 — Pipeline 3: Scoring Pipeline With Real 2026-05-22 Data

Recreated the WATCHDOG_CLOSE_SCORE_COMPUTED log inputs from the actual session and ran them through `compute_brain_close_score` with `brain_vote_present=True` (matching the watchdog wire-up at `position_watchdog.py:3784`):

| Case | Pre-fix composite | Post-fix composite | Recommendation | Hard floor active? |
| --- | --- | --- | --- | --- |
| INJUSDT 16:05 (82.7% SL, structural reasoning) | 2.0 (reject) | 4.0 (reject) | reject | False at 85% floor (would fire if floor=80%) |
| ICPUSDT 16:50:40 (74.6% SL, deep_loser, broken XRAY) | 4.5 (reject) | 6.5 | execute | False |
| C1 regression (vague panic on sound) | n/a (didn't reach scoring pre-C1) | -5.5 | reject_and_tighten | False |
| Automated path (no brain vote) | 4.5 (unchanged) | 4.5 | reject | n/a |

The math runs through the actual production function. C1 anti-churn is preserved (vague panic + supportive structure → reject_and_tighten). The 2026-05-22 INJ case at 82.7% SL stays in `reject` band under the 85% floor, but the operator can lower the floor to 80% to capture it (or both fixes can fire together — brain-vote-factor + a tighter floor).

## H1 — Pipeline 4: High-Conviction Definition Against Real Type System

Loaded the real `StructuralAnalysis` from `src/analysis/structure/models/structure_types.py:536` and `MarketRegime` from `src/strategies/models/regime_types.py:8`. Truth-table verification:

| brain dir | per-coin regime | trade_direction | regime aligned? | td aligned? | high-conviction? | XRAY authority |
| --- | --- | --- | --- | --- | --- | --- |
| Buy | trending_up | long | yes | yes | **yes** | VETO only (no silent reversal) |
| Buy | volatile | "" | no | no | no | flip permitted with single DIRECTION_DECISION |
| Buy | trending_up | short (counter-setup) | yes | no | no | flip permitted |
| Sell | trending_down | short | yes | yes | **yes** | VETO only |
| Sell | trending_up | long | no | no | no | flip permitted |

All cases match the design intent. The counter-setup case (trending_up regime + structural trade_direction=short, the BULLISH_FVG_OB_COUNTER scenario) correctly classifies as low-conviction so XRAY's structural-rr can still flip the brain's instinct when the structure itself recommends the opposite direction.

## H1 — Pipeline 5: WATCHDOG_CLOSE_SCORE_COMPUTED Log Format Integration

Drove `as_log_dict()` through `compute_brain_close_score` and verified the serialized log line carries all 24 expected fields:

```
composite, threshold, recommendation,
pnl_pct, pnl_bucket, pnl_factor,
time_remaining_s, time_bucket, time_factor,
age_s, age_bucket, age_factor,
velocity, velocity_bucket, velocity_factor,
sl_pct, sl_bucket, sl_factor,
xray_bucket, xray_factor,
reasoning_bucket, reasoning_factor,
brain_vote_bucket, brain_vote_factor
```

Plus the two suffix fields injected at `position_watchdog.py:3812–3813`: `hard_floor_pct` and `hard_floor_active`. Total 26 fields in the WATCHDOG_CLOSE_SCORE_COMPUTED line. Pre-existing log consumers grepping for `composite=X` and `recommendation=Y` continue to work — those fields appear at the start of the line.

## H1 — Pipeline 6: Cross-System Consumer Audit

Grepped every `.py` file under `src/` and `tests/` (excluding `.bak*` files) for each new identifier:

| Identifier | Read/emit sites | Production sites + tests |
| --- | --- | --- |
| `xray_high_conviction_protection_enabled` | 6 | settings.py:867, settings.py:3806-3807, strategy_worker.py:124, strategy_worker.py:1929, test_j3:175 |
| `wd_hard_risk_floor_sl_pct` | 5 | settings.py:1045, settings.py:3907-3908, position_watchdog.py:423, position_watchdog.py:3798 |
| `DIRECTION_DECISION` emission | 5 | strategy_worker.py emits at 2003 (veto), 2046 (hold), 2079 (block-missing-levels), 2111 (block-post-flip-conflict), 2168 (flip) |
| `brain_vote_factor` / `brain_vote_bucket` | 8 | wd_brain_scoring.py (dataclass, as_log_dict, compute), position_watchdog.py (read via as_log_dict) |
| `WATCHDOG_HARD_FLOOR_HIT` emission | 1 | position_watchdog.py:3830 |
| `brain_vote_present` parameter | 1 producer + 1 consumer | wd_brain_scoring.py:325 (param), position_watchdog.py:3784 (only production caller) |

Each consumer-producer pair is traceable end-to-end. No orphan emissions and no orphan reads.

## H1 — What This Pipeline Check Confirms

- **DI wiring** is correct: WorkerManager passes the right services to StrategyWorker and PositionWatchdog at the right time.
- **Settings flow** is correct: `config.toml` → `Settings._load_fresh` → `RiskSettings` / `WatchdogSettings` → worker constructors → boot sentinels emit the right values.
- **Boot sentinels** fire at process start under the real settings loader, with the expected k=v key values.
- **Scoring pipeline** runs through the real production function and produces the design-intended outcomes for the 2026-05-22 INJ + ICP cases.
- **High-conviction definition** reads correctly from the real `StructuralAnalysis` and `MarketRegime` types — including the counter-setup edge case.
- **Log format integration** is compatible with pre-fix consumers (existing fields appear at expected positions) while extending with new fields at the end of the line.
- **Cross-system wiring** has zero orphan references — every identifier reads from a settings field or writes to a log emission that exists.

## H1 — What This Pipeline Check Does NOT Confirm

- The behaviour of the system under live trading conditions. That requires the operator's restart-and-observe trial (per spec Rule 13 / Phase 5 integrated trial).
- Whether the 85% hard-floor is the right operational floor for the operator's typical position-state distribution. The trial measures rejected-and-held outcomes; the operator can re-tune via config.toml without code changes.
- Whether the high-conviction definition's pair of conditions (regime + trade_direction) covers every edge case in the wild. The trial will surface any cases where the definition is too tight or too loose; both are operator-tunable via the kill-switch.

## H1 — Conclusion

The P0-2 and P0-3 fixes are integrated into the real project at every layer this audit can verify offline:

- File-by-file changes are wired correctly into the DI container.
- Settings are persisted in config.toml, loaded by the production settings loader, and propagated to the worker constructors.
- Boot sentinels fire at the right moments with the right values.
- The pure-function scoring math produces the design-intended outcomes for the historical session's headline cases.
- The high-conviction definition reads correctly from the real type system.
- Log emissions are format-compatible with existing consumers and carry every new field with traceable producer-consumer pairs.

The remaining verification step is the operator's live trial after restart. The two verification scripts (`verify_p0_2.py`, `verify_p0_3.py`) and the boot sentinels make the live result observable from the operator's log queries.
