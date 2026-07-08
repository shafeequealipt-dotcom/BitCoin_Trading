# Phase 0 — Issue 5: Universe Flapping Investigation

**Date:** 2026-04-27
**Brief reference:** `IMPLEMENT_FIVE_CRITICAL_FIXES_PROFESSIONAL.md` § Issue 5, Phase 5

## A — The mechanism

`ScannerWorker` is a `SweetSpotWorker` (5-min window, 4:00 offset). Per tick it scores ~50 candidate coins, picks `max_coins = 30` (`config.toml [scanner]`) by composite score, and writes the result to:
- in-memory `MarketScanner._active_universe: list[str]` (`src/strategies/scanner.py:51`)
- `active_universe` table via `DELETE` + `executemany INSERT` (`scanner_worker.py:264-284`)

Composite score weights (`config.toml [scanner.scoring_weights]`):
- `structure = 0.30` (X-RAY setup score from structure_worker cache)
- `strategy = 0.30` (L2 total_score from strategy_worker)
- `signal = 0.15` (signal_worker confidence)
- `regime = 0.15` (regime_worker alignment)
- `funding = 0.10` (altdata_worker funding magnitude)

Selection at `scanner_worker.py:237`: `scored.sort(reverse=True); selected_top = scored[:top_n]`. **Pure top-N**, no minimum-score gate.

`MarketScanner._update_universe` (`src/strategies/scanner.py:65-183`):
- Force-include BTC/ETH (lines 92-94)
- Force-include open-position coins (HR-3, lines 115-123)
- Re-entry cooldown (line 131): a coin removed less than 300 s ago is blocked from re-entering, except when force-included.
- Compute `old_set` vs `new_set` diff (lines 140-147): any coin in `old_set` but not `new_set` is removed and recorded in `_removed_cooldown[sym] = now_ts` (line 147). Cleanup after 1 hour (lines 150-153).

**Hysteresis is missing.** A coin's score crossing the cutoff on a single scan triggers an entry; crossing back triggers an exit on the very next scan. With composite scores driven by stochastic indicators, marginal coins oscillate above/below the cutoff every 5 minutes — exactly the observed pattern.

Live observation (`dev_notes/layer1_layer7_realtime_observation_2026-04-26.md`):
- 5 rotations in 22 min (~ 14 / hour)
- FILUSDT: in/out 3×
- AVAXUSDT: in/out 2×
- Each rotation triggers KLINE_BACKFILL → cold start → STRAT_SKIP_STALE storm

## B — The dependencies

Downstream consumers of `active_universe`:
- **Stage 2 / strategist** — calls `scanner.get_active_universe()` once per cycle to scope analysis to the 30 selected coins.
- **StrategyWorker** — scores L2 setups for the 30 coins; its caches don't pre-populate for newly-rotated-in coins, hence STRAT_SKIP_STALE.
- **KlineWorker** — does NOT directly read active_universe (it operates on the full 50-coin watchlist), but newly-selected coins still exhibit COLD-START because their kline rows are old.
- **TIAS** — receives trade outcomes from the active_universe coins; rotation noise increases the post-trade analysis volume.
- **DB writers under D-3 lock** — `executemany INSERT INTO active_universe` at line 278 of scanner_worker is one of the small contention contributors; D-3 fix already mitigates the lock impact.

Persistence: `active_universe` table is rewritten in full each tick. Force-including open-position coins is verified by reading lines 115-123 of scanner.py.

## C — The constraints

- Scoring formula is **out of scope** (per brief Hard Rule 3).
- Force-include for open positions and BTC/ETH **must be preserved**.
- Cooldown is currently 300 s; raising it must not lock in stale picks under regime change. Brief raises to 600 s.
- KlineWorker watch-list (50 coins) is independent of active_universe; touching the watch-list is out of scope.

## D — The fix candidates (per brief Phase 5)

User implicitly accepted the brief's Phase 5 plan.

Two atomic commits:

1. **Consecutive-scan hysteresis + cooldown bump.** Add `_above_cutoff_streak` and `_below_cutoff_streak` per coin. Compute per-tick cutoff = score of bottom-30 coin. Update streaks; entry requires `_above_cutoff_streak[sym] >= entry_consecutive_scans` (default 2); exit requires `_below_cutoff_streak[sym] >= exit_consecutive_scans` (default 3). Bands defined by `entry_threshold_above_min = 5` and `exit_threshold_below_min = -5` (gap creates dead-zone for marginal coins). Force-include for BTC/ETH and open positions runs BEFORE hysteresis and bypasses both streak checks and the cooldown. Re-entry cooldown bumped to 600 s.
2. **Hysteresis observability.** Per-coin `SCANNER_HYSTERESIS | coin=... action=entry_pending|entry_confirmed|exit_pending|exit_confirmed consecutive_scans=N/M score=... cutoff=... cooldown_remaining=...` for transitional states. Extend `SCANNER_TICK_SUMMARY` with `entries_pending`, `exits_pending`, `entries_confirmed`, `exits_confirmed`, `force_included_overrides`, `cooldown_blocked_count`.

## E — The observability gap

Today emitted (line 329 of scanner_worker.py):
```
SCANNER_TICK_SUMMARY | watch_list=50 protected=3 scored=53 selected=30 top_n=30 forced_in=3 mean_score=58.2 top=BTCUSDT(62.1) el_ms=120 drift_ms=0
```

Missing:
- Per-coin entry/exit lifecycle.
- Streak counters and pending transitions.
- Cooldown blocked count.
- Force-include override count distinct from natural force-includes.

Phase 5 fills these.

## F — The verification approach

| Trial | Procedure | Pass criterion |
|---|---|---|
| 5.1 | 24 h rotation count | < 4 rotations / hour (was ~14) |
| 5.2 | Per-coin flap rate | No coin enters AND exits within the same hour |
| 5.3 | Average time in universe | > 15 min (was < 5 min for flappers) |
| 5.4 | Force-include sanity | Open-position coins always present in `active_universe`; force-include never overridden by hysteresis |
| 5.5 | Regime-change responsiveness | When BTC dumps and a previously-out coin spikes in score, it enters within 2 scans (10 min) — not held out by stale cooldown |

Edge cases:
- Sudden regime shift: a coin's score jumps 20 points; with `entry_consecutive_scans = 2`, it enters on the second confirming scan (10 min). Acceptable trade-off vs. flap suppression.
- Cooldown lockout: a coin with a real opportunity blocked for 600 s. If the operator observes this consistently, lower the cooldown — but only after data justifies it.

## G — The rollback path

Two commits, each reverts independently. Reverting commit 1 restores prior top-N + 300 s cooldown behaviour. Reverting commit 2 only loses observability.
