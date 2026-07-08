# Phase 0.3 — StrategyWorker / Stage 1 Investigation

**Investigated:** `src/workers/strategy_worker.py` (1309 lines, key spots grep'd), `src/strategies/scorer.py` (467 lines), `src/strategies/ensemble.py` (162 lines), `src/strategies/registry.py`, `src/strategies/__init__.py`. HEAD = `8dca492`.

## A. Current implementation

`StrategyWorker(SweetSpotWorker)` — sweet spot `1:30`. Hosts Stage 1's 4 internal layers per truth doc Section 5.1: Strategy Scanner (40 strategies, K1-K4 are ensemble strategies, X1 testnet-only) → Trade Scorer (0-105) → Ensemble Voter (consensus categories) → Hand-off.

Slow-tick threshold 10s (`_TICK_SLOW_PER_WORKER["strategy_worker"]`).

## B. Score / consensus exposure (key facts for Phases 3 + 5)

- `self._score_cache: dict[str, float] = {}` — initialized at line 88.
- Populated at line 518 (`self._score_cache[_sym] = float(_ss.total_score)`) — per coin, per scored setup.
- Public accessor `def get_score(coin: str) -> float | None` at line 675 (returns `self._score_cache.get(coin)`).
- ScannerWorker reads this via `services["strategy_worker"].get_score(coin)` (`scanner_worker.py:74-81`).

- `layer_manager._strategy_hints = hints` written at line 598. `hints` is a `list` (Phase 7's wrapper-list — see line 591 `consensus_strength` access pattern).
- `layer_manager._strategy_consensus = self._build_consensus_summary(filtered)` written at line 599. **Today this is a SUMMARY DICT, not per-coin keyed.** `_build_consensus_summary` defined at line 679.

## C. EnsembleResult and consensus categories (`ensemble.py:99-113`)

```python
CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}
if   agreeing >= 4.0  and opposing <= 1.5: consensus = "STRONG"
elif agreeing >= cfg.min_ensemble_agreement and opposing <= cfg.max_ensemble_opposition: consensus = "GOOD"
elif agreeing >= 1.5  and opposing <= 1.5: consensus = "WEAK"
elif agreeing >  opposing                : consensus = "LEAN"
else                                      : consensus = "CONFLICT"
```

**5 categories**, not 4. The blueprint mentions only STRONG/GOOD/WEAK/CONFLICT. Phase 5 must treat `LEAN` as failing the `min_consensus="GOOD"` qualification (configurable via `min_consensus`).

`EnsembleResult` (line 115-125) carries: `scored_setup, votes, buy_votes, sell_votes, neutral_votes, consensus_strength, consensus_direction, passed=True (size, not eligibility), size_multiplier`.

## D. ScannerWorker access pattern today

- Score: `services["strategy_worker"].get_score(coin)` → `_score_cache.get(coin)` → `float | None`.
- Consensus: NOT available per-coin today. ScannerWorker doesn't read consensus categorically yet — it reads only the numeric score.

Strategist reads `layer_manager._strategy_consensus` (the summary dict) at lines 1017 and 1587 of `strategist.py`, defensively via `getattr(...).get(...)` — so the shape change in Phase 3 is safe if we alias.

## E. Restructure change plan (Phase 3)

1. **Add `_build_per_coin_consensus(filtered) -> dict[str, dict]`** new method in `strategy_worker.py`. Inner dict: `consensus, consensus_score, vote_count, direction, last_updated`.
2. **At line 599**, replace `layer_manager._strategy_consensus = self._build_consensus_summary(filtered)` with:
   ```python
   new_consensus = self._build_per_coin_consensus(filtered)
   existing = getattr(layer_manager, "_strategy_consensus", {}) or {}
   if not isinstance(existing, dict) or "consensus" in existing:
       existing = {}  # migrate from legacy summary shape on first run
   existing.update(new_consensus)  # only updates processed coins; preserves stale
   layer_manager._strategy_consensus = existing
   layer_manager._strategy_consensus_summary = self._build_consensus_summary(filtered)  # legacy alias
   ```
3. **Strategist tolerance** — verify `strategist.py:1017` and `:1587` reads tolerate the new shape. Both use `getattr(layer_manager, "_strategy_consensus", {})` then `.get(...)` — defensive. The new shape is `dict[str, dict]`; the old summary was `dict` with summary keys. To prevent strategist confusing per-coin keys for summary keys, keep `_strategy_consensus_summary` alias and update strategist reads to prefer the alias for summary access.
4. **Stale preservation rule**: if a coin wasn't processed this tick (e.g., StrategyWorker filtered it out), do NOT touch its existing entry. ScannerWorker reads `last_updated` to assess freshness.
5. **LayerManager accessor**: add `def get_strategy_consensus(self, symbol: str) -> dict | None: return getattr(self, "_strategy_consensus", {}).get(symbol)`. Initialize attribute as `_strategy_consensus: dict[str, dict] = {}` in `LayerManager.__init__`.
6. **Observability**:
   - `STRAT_CONSENSUS_SUMMARY | cycle=… STRONG=8 GOOD=15 WEAK=12 LEAN=4 CONFLICT=3 NONE=8` (cycle, INFO).
   - `STRAT_CONSENSUS_CHANGE | sym=… from=WEAK to=STRONG reason=…` (per-coin INFO on transition; track `_prev_consensus: dict[str, str]` worker-level).

## F. Verification criteria

- After Phase 3, one cycle later: `_strategy_consensus` has entries for all coins StrategyWorker processed.
- 24h distribution roughly STRONG 5-15%, GOOD 15-30%, WEAK+LEAN 30-50%, CONFLICT 5-15%.
- For a coin processed last cycle but skipped this cycle: its `_strategy_consensus` entry is NOT cleared; its `last_updated` reflects last successful update.
- Strategist still works (no JSON parse errors, no schema regressions in CALL_A/CALL_B).
