# Phase 4 — Ensemble Combination Logic (Detailed)

This phase isolates the ensemble's combination math from the broader Phase 1 map. Source files: `src/strategies/ensemble.py` (390 lines), `src/strategies/registry.py`, `src/strategies/optimizer.py`.

## Vote Collection

For each `setup` from L2 scoring, the ensemble's `vote()` method (ensemble.py:149-305):

1. Reads `active = self.registry.get_active_for_regime(regime.regime)` (ensemble.py:171). **But `registry.get_active_for_regime` ignores its argument and returns every enabled strategy (registry.py:44-53). Regime is plumbed but functionally dead at this step.**

2. Iterates active strategies excluding the originator (ensemble.py:176-178). For each, calls `strategy.vote(setup, candles_map, ta_map, sentiment_data, altdata, regime)`. Strategy returns `(vote_str, confidence, reasoning)` where `vote_str ∈ {"BUY", "SELL", "NEUTRAL"}`.

3. Reads `weight = registry.get_performance(strategy.name).ensemble_weight` (ensemble.py:188-189). Default 1.0 (signal_types.py:195).

4. Stores per-vote record `EnsembleVote(strategy_name, vote, confidence, reasoning, weight)` (ensemble.py:193-198).

## Weighted Sum

Per-strategy contribution = `weight * confidence` (ensemble.py:218-220).

Side totals (ensemble.py:233-235):
- `buy_votes = _capped_contribution("BUY")`
- `sell_votes = _capped_contribution("SELL")`
- `neutral_votes = sum(v.weight for v in votes if v.vote == "NEUTRAL")` — **NEUTRAL uses WEIGHT only, not weight×confidence**

`_capped_contribution(vote_side)` (ensemble.py:217-231):
- If `cap_share >= 1.0` (default; cap disabled): sum all `contribution[i]` for that side.
- If `cap_share < 1.0`: each contribution capped at `ceiling = rest_total * cap_share / max(1 - cap_share, 1e-9)`. So no single strategy can be more than `cap_share` fraction of the side total.

Production setting: `single_strategy_max_share = 1.0` (settings.py:1601). The cap is disabled. Any single strategy's full contribution is included.

## Consensus Computation

```python
agreeing = buy_votes if direction == "BUY" else sell_votes      # ensemble.py:256
opposing = sell_votes if direction == "BUY" else buy_votes      # :257

if agreeing >= 4.0 and opposing <= 1.5:                          # :263 (HARDCODED)
    consensus = "STRONG"
elif agreeing >= cfg.min_ensemble_agreement and opposing <= cfg.max_ensemble_opposition:  # :265 (config)
    consensus = "GOOD"
elif agreeing >= 1.5 and opposing <= 1.5:                        # :267 (HARDCODED)
    consensus = "WEAK"
elif agreeing > opposing:                                        # :269 (HARDCODED)
    consensus = "LEAN"
else:
    consensus = "CONFLICT"                                       # :272 (HARDCODED)
```

Production values:
- STRONG: `agreeing >= 4.0 AND opposing <= 1.5` (HARDCODED)
- GOOD: `agreeing >= 2.5 AND opposing <= 2.5` (config.toml:983-984 overrides dataclass 5.0/1.0)
- WEAK: `agreeing >= 1.5 AND opposing <= 1.5` (HARDCODED)
- LEAN: `agreeing > opposing` (HARDCODED)
- CONFLICT: else (HARDCODED)

### Branch-order subtlety

STRONG fires BEFORE GOOD. STRONG's hardcoded bar (`opposing<=1.5`) is TIGHTER on opposition than GOOD's config-driven bar (`opposing<=2.5`) but LOOSER on agreement (`agreeing>=4.0` vs `>=2.5`). So:
- `agreeing=4.0, opposing=1.5` → STRONG.
- `agreeing=3.5, opposing=1.5` → GOOD (STRONG fails on agreeing).
- `agreeing=4.0, opposing=2.0` → GOOD (STRONG fails on opposing).
- `agreeing=2.5, opposing=2.5` → GOOD.
- `agreeing=2.0, opposing=1.5` → WEAK.

This ordering means GOOD is the most common label for moderate-agreement / moderate-opposition trades. The Phase 3 data confirms GOOD is the second-largest bucket (76 trades) and the WORST per-trade bucket (57.9% loss).

## Consensus → Size Mapping

```python
CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}  # ensemble.py:261
size_mult = CONSENSUS_SIZE.get(consensus, 0.3)                                                  # :275
```

This is the `base_size_mult` reported in `ENSEMBLE_VOTE_WEIGHTED` log events.

## Structural Confidence Modifier

```python
_raw_conf = setup.scoring_details.get("setup_type_confidence")     # :289
_struct_conf = float(_raw_conf) if _raw_conf is not None else 0.85  # :290 (default 0.85)
_conf_factor = max(0.5, min(1.0, _struct_conf))                    # :291 — clamped to [0.5, 1.0]
size_mult *= _conf_factor                                          # :293 → final_size_mult
```

`setup_type_confidence` comes from `structure_engine.classify_setup()` and reflects how confident the XRAY is in the structural setup. For most clean setups, ~0.55-0.85. For counter-setups, multiplied by `counter_mult=0.7` so values like 0.385 → clamped to **0.5** by the floor.

### `final_size_mult` domain

| Consensus | base_size_mult | × min `_conf_factor` (0.5) | × max `_conf_factor` (1.0) |
|---|---|---|---|
| STRONG | 1.00 | 0.50 | 1.00 |
| GOOD | 0.75 | 0.375 | 0.75 |
| LEAN | 0.50 | 0.25 | 0.50 |
| WEAK | 0.30 | 0.15 | 0.30 |
| CONFLICT | 0.15 | 0.075 | 0.15 |

Counter-setup on STRONG → 0.50 final_size_mult. Counter-setup on WEAK → 0.15.

## What Reads `final_size_mult` Downstream?

Per Phase 1 strategist agent finding: **NOTHING in the active CALL_A path reads `final_size_mult`**. The strategist surfaces only `pkg.strategies.ensemble_consensus` (the label STRING) to Claude, not the size multiplier. Claude is asked to choose `size_usd` under the per-trade ceiling. The ensemble's computed multiplier is **emitted to logs** (`ENSEMBLE_VOTE_WEIGHTED ... final_size_mult=X`) but does not propagate into the trade-execution path.

This is a major finding: the ensemble's sizing recommendation is computed, logged, and discarded. The downstream size choice is fully delegated to Claude based on the consensus LABEL alone.

Corollary: the data showing "more strategies agree → worse outcome" (Phase 3 Claim 3) means Claude is implicitly sizing UP on STRONG consensus (because it sees "STRONG" in the prompt) without the ensemble's multiplier actually being applied. The size amplification is happening in Claude's reasoning, not in code.

## Persistence Of Per-Strategy Votes

Per Phase 1: `ensemble_votes` SQL table declared at migrations.py:434-446 — never written to (zero INSERTs anywhere). Per-trade per-strategy votes live ONLY in:

- The `STRAT_VOTE_TRACE` log line (ensemble.py:354-358) — **fires only when consensus=="STRONG"** AND `vote_trace_enabled=True`. Non-STRONG trades have no per-strategy vote record anywhere.
- `data/logs/layer1c_full.jsonl` per-cycle dump (strategy_worker.py:1212).
- In-memory `layer_manager._strategy_votes` cache (strategy_worker.py:894).
- Trade-time aggregates (`ensemble_strength`, `ensemble_votes_for/against`) go to `strategy_trades` via brain_v2.py:509-521 (these are summary numbers, NOT per-strategy detail).

This is why the Phase 3 verification of Claim 3 (herding) had 212 of 225 trades with trace data — only 12 trades had no STRONG consensus recorded near entry. The remaining ~6% of trades is sampling noise.

## Weights — Sources Of Differentiation

### Default (boot)

Every strategy: `ensemble_weight = 1.0` (signal_types.py:195).

### Optimizer adjustments

`src/strategies/optimizer.py:67-90` is the only mutator. Calls `registry.set_ensemble_weight(name, new_weight)` (registry.py:96-104). Logic (optimizer.py):
- For high-performing strategies: `new_weight = perf.ensemble_weight * (1 + adj)`.
- For under-performing: `new_weight = perf.ensemble_weight * (1 - adj)`.
- Clamped to `[0.1, 3.0]` via registry.

### Persistence

**The optimizer's adjusted weights are NOT persisted to the database.** No table holds per-strategy `ensemble_weight` values. The `strategy_performance` table holds win-rate/profit-factor metrics, but not the ensemble weight field. On process restart, every strategy resets to 1.0.

Verified: `sqlite> SELECT * FROM strategy_performance` shows only `claude_trader` rows. No A1_rsi_reversal / A2_vwap_bounce / ... rows. The per-strategy weight differentiation that the Optimizer produces in-memory is ephemeral.

### Regime-conditional weighting? NO

There is no regime-conditional weight table. The same `ensemble_weight` applies in every regime. Phase 3 Claim 5 showed momentum/trend strategies are net-negative — but they cannot be down-weighted ONLY in ranging/volatile while up-weighted in trending. The system has no mechanism for that.

## Logged Events

| Event | Trigger | File:line |
|---|---|---|
| `ENSEMBLE_VOTE_WEIGHTED` | Per setup when `_struct_conf < 0.85` | ensemble.py:322-329 |
| `STRAT_VOTE_TRACE` | Per STRONG-consensus setup when `vote_trace_enabled=True` | :354-358 |
| `ENSEMBLE_CONFLICT` | When CONFLICT branch fires | :273 |
| `STRAT_VOTE_FAIL` | Per failed strategy.vote() | :201-204 |
| `ENSEMBLE_CACHE_WRITE_FAIL` | EnsembleStateCache.record() error | :251-254 |

## EnsembleStateCache (in-memory)

`EnsembleStateCache.record(symbol, buy, sell, neutral)` (ensemble.py:51-70) keeps the most recent vote tallies in-memory per symbol. Used by `get_current_consensus(symbol)` (ensemble.py:72-122) — a replay function that recomputes consensus from cached values.

**Discrepancy**: the cache's replay logic at ensemble.py:106-110 hardcodes the GOOD bound as `agreeing >= 5.0 AND opposing <= 1.0`. This matches the dataclass default but NOT the production toml override (2.5/2.5). The cache and the live voter disagree on consensus when the toml override is active. The cache won't track operator tuning of those keys.

## Summary

| Aspect | Status |
|---|---|
| How votes combined | Weighted sum: `weight × confidence` per strategy, separately for BUY and SELL sides |
| Are weights equal | YES at boot (1.0 each). Optimizer mutates in-memory only — non-persistent. |
| Are weights regime-conditional | NO |
| How consensus computed | Branch-order tree on (agreeing, opposing) totals; STRONG hardcoded, GOOD config-driven, others hardcoded |
| How consensus → size | `CONSENSUS_SIZE × clamp(setup_type_confidence, 0.5, 1.0)` |
| Where consensus → strategist prompt | Only the LABEL string (`pkg.strategies.ensemble_consensus`) flows; the size multiplier does NOT |
| Where consensus → sizing | Indirectly via Claude's interpretation; no code-side multiplication |
| Per-trade vote persistence | None to DB. Logs only (and STRAT_VOTE_TRACE only on STRONG) |
