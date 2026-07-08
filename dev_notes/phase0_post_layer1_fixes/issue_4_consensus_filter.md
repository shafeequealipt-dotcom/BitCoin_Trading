# Issue 4 — StrategyWorker writes _strategy_consensus from filtered list

**Status:** PRESENT — root cause unfixed; commit `0afd4e2` only patched the strategist *reader*.
**Tier:** 2 (selection pipeline starvation; one-line fix).
**Source observation:** `dev_notes/layer1_live_monitor_2026-04-27.md` lines 78-99 (Finding #1).

## A. Mechanism

`StrategyWorker.tick` builds `consensus_setups` (full universe, ~50 coins worth of ensemble decisions), then runs `apply_restrictions(consensus_setups, mode)` (line 564) which filters by `max_score_threshold` (50 in NORMAL mode). Output: `filtered`.

```python
# strategy_worker.py:585-591  (the comment block is correct; line 591 contradicts it)
# write it to the cache BEFORE the Layer 3 active check. Consensus
# is observability/data, not execution; ScannerWorker reads it
# whether Layer 3 is on or off. Stale entries (coins not processed
# this tick) are preserved via merge so a momentary gap doesn't
# zero the entry the selector reads.
if layer_manager:
    new_consensus = self._build_per_coin_consensus(filtered)   # ← BUG (should be consensus_setups)
    existing = getattr(layer_manager, "_strategy_consensus", {}) or {}
    existing.update(new_consensus)
    layer_manager._strategy_consensus = existing
    ...
    layer_manager._strategy_consensus_summary = self._build_consensus_summary(filtered)
```

The cache exposes "what consensus does each coin currently show?" categorically (STRONG/GOOD/LEAN/WEAK/CONFLICT). ScannerWorker's `_qualifies()` reads it via `lm.get_strategy_consensus(symbol)` (`scanner_worker.py:536-543`) and short-circuits to `consensus=NONE` when the symbol is missing. Criterion 2 of the qualitative filter requires `consensus in {STRONG, GOOD}` — `NONE` always fails.

Live impact (from monitor table): each cycle writes 5–18 of 50 coins to the cache. Cumulative merge across cycles helps but the per-cycle delta starves coins whose ensemble score happens to land below 50 in any given cycle. ScannerWorker `qualified` count: 0–2 across the 29-min window vs Phase-5 plan target of 5–25.

## B. Dependencies

- **Reader:** `ScannerWorker._qualifies` at `scanner_worker.py:485-589`, specifically the `consensus in {STRONG, GOOD}` branch around 536-543.
- **Accessor:** `LayerManager.get_strategy_consensus(symbol)` at `core/layer_manager.py:1146-1158`.
- **Sibling cache:** `_strategy_consensus_summary` (line 607) is the LEGACY shape `{symbol → {buy, sell, total_score}}` consumed by the strategist at `strategist.py:1017` and `1587`. Commit `0afd4e2` made the strategist read this fallback; the contract is that this cache stays post-filter (only top setups get summarized).
- **Builder:** `_build_per_coin_consensus(setups)` at `strategy_worker.py:761-817` — agnostic to whether input is filtered or not.

## C. Constraints

- Must NOT change `apply_restrictions` semantics — that's the PnL-mode filter used to actually trade.
- Must NOT touch strategist consumer lines (`strategist.py:1017, 1587`) — those expect the post-filter summary.
- Must NOT default missing `_strategy_consensus` lookups to STRONG (forbidden by Hard Rule 1 — masks the bug).
- The pre-existing comment block at lines 585-590 already documents the correct intent; this fix aligns implementation to the comment.
- `0afd4e2` shipped 2026-04-27 04:29 UTC and added defensive filtering on the strategist reader (skips rows missing `{buy, sell, total_score}` keys). That defensive read remains correct after this fix because we still write the legacy shape to `_strategy_consensus_summary` from `filtered`.

## D. Fix candidates

1. **One-line change at line 591 (chosen).** Replace `filtered` with `consensus_setups` in the `_build_per_coin_consensus(...)` call. Keep line 607 (`_strategy_consensus_summary`) on `filtered`. Add observability event.
2. Default missing entries to STRONG in ScannerWorker. Rejected — masks bug.
3. Maintain a single cache from `consensus_setups` and remove `_strategy_consensus_summary`. Rejected — breaks legacy strategist contract that `0afd4e2` just stabilized.
4. Pass `consensus_setups` into `apply_restrictions` differently. Rejected — `apply_restrictions` is the right semantic for PnL filtering; the cache is about observability and should be unfiltered.

## E. Observability gap

- No log today says "consensus cache size after this cycle = N". Operators infer the gap from `STRAT_CONSENSUS_SUMMARY` (line 607-equivalent emission) which only summarizes the post-filter set.
- Add `STRAT_CONSENSUS_WRITE | cycle={c} full_count={n_full} filtered_count={n_filtered} mode={m} threshold={t}` at INFO after the cache write. Operators see the gap close immediately.

## F. Verification approach

- Unit test: construct StrategyWorker with mock setups for 50 coins (mix above/below threshold). Call the consensus build. Assert `_strategy_consensus` size == 50, `_strategy_consensus_summary` size <= 50.
- Strategist reader sanity: existing `tests/test_strategist_*` should pass unchanged (the data shape they read from `_strategy_consensus_summary` is preserved).
- Live trial: 60-min window post-deploy. Count `qualified` per cycle. Target average 5–25. Compare `STRAT_CONSENSUS_WRITE.full_count=50` vs `filtered_count` typically 5–18.
- DB-side: no DB read needed (caches are in-memory).

## G. Rollback path

Single-file revert of `src/workers/strategy_worker.py`. No state migration. Reverting restores the bug; ScannerWorker qualified count drops back to 0–2. Rollback time: < 30 seconds (`git revert <phase4_commit>`).
