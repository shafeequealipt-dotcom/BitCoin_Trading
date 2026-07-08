# Phase 0.E — Strategy Consensus Cache Wiring

## Producer

`StrategyWorker._build_per_coin_consensus()` at `src/workers/strategy_worker.py:923-979`.

Called from `tick()` after ensemble votes are aggregated. Writes to `layer_manager._strategy_consensus` (line 720-734).

## Cache shape (per coin, today)

```python
layer_manager._strategy_consensus[symbol] = {
    "consensus": "STRONG" | "GOOD" | "LEAN" | "WEAK" | "CONFLICT",
    "consensus_score": float,    # size_multiplier 0.15-1.0
    "vote_count": int,           # number of strategies that voted
    "direction": "long" | "short" | "neutral",
    "last_updated": float,       # unix timestamp
}
```

Defined at `layer_manager.py:104` as `self._strategy_consensus: dict[str, dict] = {}`.

## Consumers

| Consumer | File:line | Reads |
|---|---|---|
| `_qualifies()` Crit 2 | `scanner_worker.py:794` | `consensus.get("consensus")` |
| `_build_package` (StrategiesBlock) | `scanner_worker.py:637` | `consensus["consensus"], vote_count, consensus_score` |
| `_compute_opportunity_score` regime alignment | `scanner_worker.py:227` | `consensus.get("direction")` |
| `_get_directional_rr` | `scanner_worker.py:208` | `consensus.get("direction")` |
| `strategist._build_trade_prompt` | `strategist.py:1017, 1587` | `consensus.get("consensus")` |
| `LayerManager.get_strategy_consensus` | `layer_manager.py:1589-1601` | dict get |

## Gap 1 — full vote distribution NOT cached

`EnsembleVoter.vote()` at `src/strategies/ensemble.py:35-228` produces an `EnsembleResult` with `votes: list[EnsembleVote]`. Each vote carries:

```python
EnsembleVote(
    strategy_name: str,
    vote: "BUY" | "SELL" | "NEUTRAL",
    confidence: float,
    weight: float,
    reasoning: str,
)
```

(Defined at `ensemble.py:62-83`.)

**The full `votes` list is logged once in `STRAT_VOTE_TRACE` (lines 219-222) but NOT cached.** `_build_per_coin_consensus` discards it after computing the aggregate consensus.

## Phase 2 extension (from plan)

Add a parallel cache `_strategy_votes` populated alongside `_strategy_consensus` in `_build_per_coin_consensus()`:

```python
# scanner_worker.py:923 area
new_consensus = self._build_per_coin_consensus(consensus_setups)
new_votes = self._build_per_coin_votes(consensus_setups)   # NEW

existing = getattr(layer_manager, "_strategy_consensus", {}) or {}
existing.update(new_consensus)
layer_manager._strategy_consensus = existing

existing_votes = getattr(layer_manager, "_strategy_votes", {}) or {}
existing_votes.update(new_votes)
layer_manager._strategy_votes = existing_votes
```

New cache schema:

```python
layer_manager._strategy_votes[symbol] = {
    "votes": [
        {
            "strategy": "a3_bb_squeeze_scalp",
            "vote": "BUY",
            "confidence": 0.85,
            "weight": 1.0,
            "reasoning": "...",   # truncated to 140 chars
        },
        # ... up to len(active_strategies) entries
    ],
    "buy_weighted": float,
    "sell_weighted": float,
    "neutral_weighted": float,
    "consensus": str,
    "consensus_direction": str,
    "size_multiplier": float,
    "last_updated": float,
}
```

Add accessor `LayerManager.get_strategy_votes(symbol)` near line 1589 (next to `get_strategy_consensus`).

## Memory

50 coins × ~25 active strategies × ~250 bytes per vote dict ≈ 312 KB.
Plus 4 scalar fields × 50 = 8 KB.
Total: ~320 KB. Negligible.

## Concurrency

No locks needed. Both consumer reads (scanner, strategist) and producer writes (strategy_worker) run on the same asyncio event loop. Wholesale dict replacement at write is atomic at GIL level.

## Backward compat

Adding `vote_distribution` key to existing `_strategy_consensus` dict OR adding parallel `_strategy_votes` dict — plan favors parallel dict to avoid breaking any consumer that does `for k in consensus.keys()` iteration.
