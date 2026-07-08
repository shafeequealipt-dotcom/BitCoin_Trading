# Phase 0.1 — ScannerWorker Investigation

**Investigated:** `src/workers/scanner_worker.py` (336 lines), `src/strategies/scanner.py`, `src/database/migrations.py` (active_universe schema), HEAD = `8dca492`.

## A. Current implementation

`ScannerWorker(SweetSpotWorker)` — wakes once per 5-min window at sweet spot `4:00`. Reads warm caches from the 7 data workers, computes a composite opportunity score per coin, picks `settings.scanner.max_coins` (currently 30), force-includes open-position symbols, writes to `active_universe` table, and updates `MarketScanner._active_universe`.

Constructor signature (`scanner_worker.py:46-61`): `__init__(settings, db, scanner: MarketScanner, services: dict | None = None)`. `services` is the `WorkerManager._services` dict — late-bound; missing services degrade to `None` per accessor.

## B. Callers

- `src/workers/manager.py` (instantiation; not yet read line-by-line, but `services` injection happens here).
- `src/brain/strategist.py:592` and `:1250` — `await scanner.get_active_universe()` reads the 30-coin output (NOT directly from this worker, but via `MarketScanner._active_universe` set at `scanner_worker.py:297`).
- `src/database/repositories/...` — anyone querying `active_universe` table.

## C. Dependencies (services dict accessors at scanner_worker.py:65-139)

| Accessor | Calls | Returns |
|---|---|---|
| `_get_setup_score` | `services["structure_worker"].get_setup_score(coin)` | `float | None`, 0-100 |
| `_get_strategy_score` | `services["strategy_worker"].get_score(coin)` | `float | None`, 0-100 (TradeScorer total) |
| `_get_signal_confidence` | `services["signal_worker"].get_signal(coin).confidence` | `float`, 0-1 |
| `_get_regime_alignment` | `services["regime_worker"].get_regime(coin)` | `float`, normalized -1..+1 |
| `_get_funding_strength` | `services["altdata_worker"].get_funding(coin)` | `float`, abs rate |
| `_open_position_symbols` | `services["position"].get_positions()` | `set[str]` |

All accessors are defensive — wrap in try/except and return `None`/`0.0`/`set()` on failure. Missing services tolerated (cycle still runs).

## D. Outputs

- `active_universe` table — `DELETE` then `INSERT OR REPLACE` (lines 264-284). Columns: `symbol, opportunity_score, volume_24h=0, change_24h_pct=0, funding_rate=0, spread_pct=0, coin_tier`. Auxiliary cols zeroed because they're computed elsewhere now (Phase 6 cleanup).
- `MarketScanner._active_universe` (line 297, via `scanner.set_active_universe(new_symbols)`). Always force-includes BTCUSDT/ETHUSDT (lines 294-296).
- Subscriber callbacks via `scanner.get_subscribers_snapshot()` (lines 304-310). Note: Phase 7 already removed worker subscribers; loop preserved for forward-compat.

## E. Scheduling

Sweet spot `4:00` within 5-min window via `SweetSpotWorker` base class (see `phase0_layer1_restructure/sweet_spot.md`). Drift exposed via `_last_drift_ms` (logged in `SCANNER_TICK_SUMMARY` at line 335).

## F. The exact scoring formula (lines 143-184)

```python
weights = self.settings.scanner.scoring_weights
struct_norm   = max(0.0, min(1.0, (struct_raw or 0.0) / 100.0))     # 0-1
strat_norm    = max(0.0, min(1.0, (strat_raw  or 0.0) / 100.0))     # 0-1
sig_norm      = max(0.0, min(1.0, signal_confidence or 0.0))        # 0-1
regime_norm   = (regime_align + 1.0) / 2.0                          # 0..1 from -1..+1
funding_norm  = max(0.0, min(1.0, (funding_raw or 0.0) / 0.001))    # saturate at 0.1%
score = (
    weights.structure * struct_norm +
    weights.strategy  * strat_norm  +
    weights.signal    * sig_norm    +
    weights.regime    * regime_norm +
    weights.funding   * funding_norm
)
```

Selection logic (lines 233-252): build `scored = [(coin, score, breakdown)]` for all `watch_list ∪ open_positions`, sort descending by score, take top `settings.scanner.max_coins`, then HR-3 force-include open-position coins not already in top-N (appended after).

Empty-cache behavior: each accessor returns `None`/`0.0`, contributing `0.0` to the composite. A coin with no warm data scores `weights.regime * 0.5 = small` (regime_align defaults to 0 → norm 0.5), so unlit coins float to bottom of ranking but are not crashed out.

## G. Restructure change plan (Phase 5)

1. Replace lines ~207-336 (the `tick()` body after universe read) with a **5-criterion qualitative filter** (`_qualifies(symbol) -> tuple[bool, dict]`) → quantitative ranking of survivors → top 10-15.
2. New helper `_check_blockers(symbol, structure, consensus) -> list[str]` (Phase 5).
3. New helper `_regime_aligns(regime: str, direction: str) -> bool` (Phase 5).
4. The composite formula (lines 171-177) is **reused** in Phase 5 for the ranking step (after qualitative gate). New weights `[scanner.weights]` block in config (sums to 1.0 explicitly): `structure=0.30, strategy=0.30, signal=0.20, regime=0.10, funding=0.10`.
5. The existing `[scanner.scoring_weights]` block stays for one cycle as a fallback, removed in Phase 8.
6. `set_active_universe(new_symbols)` and BTCUSDT/ETHUSDT reference-pair force-include (lines 294-296) are **preserved**.
7. `active_universe` table schema **unchanged** (still 7 columns).
8. Phase 6 adds `_build_package(symbol, score, record, force_included) -> CoinPackage` and writes `layer_manager._coin_packages` after selection, before active_universe write.

## H. Verification criteria

- New `_qualifies()` short-circuits at first failed criterion and populates `record["reasons_failed"]` for observability.
- After Phase 5, `SCANNER_SELECT | qualified=N selected=M forced=K` log line appears every cycle.
- Force-include works: opening a manual position on a low-score coin causes that coin to appear in next cycle's `final` with `forced=1`.
- Scoring formula identity preserved: `_compute_opportunity_score` still callable for ranking after qualification.
