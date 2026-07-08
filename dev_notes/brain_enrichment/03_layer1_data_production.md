# Layer 1 Data Production Investigation

This report documents what Layer 1 (the 39-strategy scanning, scoring, ensemble voting, and briefing pipeline) actually produces today and how that production reaches the strategist. It is the third investigation in the brain-prompt-enrichment series and provides the structural baseline against which E1/E2/E3/E7 enrichment proposals are evaluated.

## Files Involved

The investigation reads end-to-end where required and selectively where the file is large:

- `src/strategies/ensemble.py` — 255 lines, full read.
- `src/workers/strategy_worker.py` — 2402 lines, focused on the vote-build path, the L1/L2/L3 sections, `_build_per_coin_*` builders, and the `STRAT_L1_DONE` / `STRAT_L4_HANDOFF` emissions.
- `src/core/layer_manager.py` — 1753 lines, focused on the consensus / votes / hints / scorer-components caches and their public accessors.
- `src/strategies/register_all.py` — 132 lines, full read.
- `src/strategies/categories/k1_claude_conviction.py` — 84 lines, full read.
- `src/strategies/categories/k2_pattern_memory.py` — 105 lines, full read.
- `src/strategies/categories/k3_ensemble.py` — 29 lines, full read.
- `src/strategies/categories/k4_adaptive_optimizer.py` — 29 lines, full read.
- `src/brain/strategist.py` — 3849 lines, focused on the vote-rendering integration around line 1370-1815 and the CALL_A package-prepend at line 2400-2490.
- `src/strategies/models/signal_types.py` — 224 lines, read for the dataclass shapes the rest of the pipeline produces and consumes.
- `src/workers/scanner_worker.py` — 1842 lines, scanned for the briefing-pipeline writers around line 735-890 and line 1048-1290 (full Target 8 coverage is deferred).
- `src/workers/scanner/state_labeler.py` — read for the label constants and `ACTION_HINTS` mapping.
- `src/config/settings.py` line 492 and line 2997 — for the `surface_briefing_fields` default.
- `config.toml` line 204 and line 827-828 — for the live production override and the ensemble thresholds.

## The 38-Strategy Registry

The registry is not actually 38 strategies. Reading `register_all.py` end-to-end shows 39 production strategies plus an optional `X1_always_trade` kickstart that registers only when `settings.bybit.testnet` is true. The 38 figure in the earlier recon was an undercount; the source-of-truth is the registration block.

Registration flows through two helpers and a top-level entry point. `register_strategies_a_to_f(registry)` (`src/strategies/register_all.py:10`) imports 19 strategy classes (`src/strategies/register_all.py:12-30`), instantiates them in a list (`src/strategies/register_all.py:32-52`), and loops `registry.register(strategy)` (`src/strategies/register_all.py:54-55`). `register_strategies_g_to_k(registry)` (`src/strategies/register_all.py:60`) imports 20 classes (`src/strategies/register_all.py:62-81`), instantiates them (`src/strategies/register_all.py:83-104`), and registers them the same way (`src/strategies/register_all.py:106-107`). The combined entry `register_all_strategies(registry)` (`src/strategies/register_all.py:112`) calls both helpers (`src/strategies/register_all.py:114-115`) and then conditionally appends `AlwaysTradeStrategy()` when `settings.bybit.testnet` is true (`src/strategies/register_all.py:118-123`). Registration failures for X1 are surfaced as warnings rather than silently swallowed (`src/strategies/register_all.py:125-130`) — this is a P1-13 fix that prevents the testnet kickstart from disappearing without a trace.

The 39 strategies by category, with names sourced from each module's `name` and `category` properties:

- A scalping (4): `A1_rsi_reversal` (`src/strategies/categories/a1_rsi_reversal.py:14`), `A2_vwap_bounce` (`src/strategies/categories/a2_vwap_bounce.py`), `A3_bb_squeeze_scalp` (`src/strategies/categories/a3_bb_squeeze_scalp.py`), `A4_ema_crossover` (`src/strategies/categories/a4_ema_crossover.py`).
- B momentum (4): `B1_volume_breakout` (`src/strategies/categories/b1_volume_breakout.py:14`), `B2_supertrend_follower`, `B3_ichimoku_breakout` (`src/strategies/categories/b3_ichimoku_breakout.py`), `B4_double_bottom_top`.
- C mean_reversion (2): `C1_bb_mean_reversion` (`src/strategies/categories/c1_bb_mean_reversion.py:14`), `C2_rsi_divergence`.
- D funding_arb (2): `D1_funding_fade` (`src/strategies/categories/d1_funding_rate_fade.py:14`), `D2_oi_divergence` (`src/strategies/categories/d2_oi_divergence.py`).
- E sentiment (3): `E1_fear_greed` (`src/strategies/categories/e1_fear_greed_extreme.py:27`), `E2_news_breakout` (`src/strategies/categories/e2_news_breakout.py`), `E3_sentiment_momentum`.
- F advanced (4): `F1_support_resistance` (`src/strategies/categories/f1_support_resistance.py:14`), `F2_multi_tf_alignment`, `F3_liquidation_hunt`, `F4_grid_recovery`. The user task labels F as "time-based"; the source-of-truth category property returns `"advanced"`.
- G predatory (4): `G1_stop_hunt` (`src/strategies/categories/g1_stop_hunt_sniper.py:14`), `G2_retail_sentiment_fade`, `G3_liquidation_frontrunner`, `G4_whale_shadow`.
- H microstructure (4): `H1_funding_predict` (`src/strategies/categories/h1_funding_prediction.py:14`), `H2_spread_basis`, `H3_volatility_switch`, `H4_order_flow`. The user task labels H as "cross-market"; the source-of-truth category property returns `"microstructure"`.
- I time_based (4): `I1_kill_zone` (`src/strategies/categories/i1_kill_zone.py:23`), `I2_weekend_gap`, `I3_options_expiry`, `I4_hourly_close`.
- J cross_market (4): `J1_btc_dominance` (`src/strategies/categories/j1_btc_dominance.py:14`), `J2_correlation_breakdown`, `J3_cross_exchange_lag`, `J4_altcoin_beta`.
- K ai_enhanced (4): `K1_claude_conviction` (`src/strategies/categories/k1_claude_conviction.py:18`), `K2_pattern_memory` (`src/strategies/categories/k2_pattern_memory.py:22`), `K3_ensemble` (`src/strategies/categories/k3_ensemble.py:14`), `K4_optimizer` (`src/strategies/categories/k4_adaptive_optimizer.py:14`).

Total: 4 + 4 + 2 + 2 + 3 + 4 + 4 + 4 + 4 + 4 + 4 = 39. Plus testnet-only `X1_always_trade` from `src/strategies/categories/x1_always_trade.py`.

## Ensemble Vote Aggregation

The ensemble is `EnsembleVoter` in `src/strategies/ensemble.py`. Its constructor takes a `StrategyRegistry` and `Settings` (`src/strategies/ensemble.py:31-33`). The single-setup entry point is `vote(setup, candles_map, ta_map, sentiment_data, altdata, regime)` (`src/strategies/ensemble.py:35-43`), and the batch entry point is `vote_batch(setups, ...)` (`src/strategies/ensemble.py:230-255`).

`vote()` extracts the originator strategy from `setup.raw_signal.strategy_name` (`src/strategies/ensemble.py:55`) and the active regime-filtered strategy set from the registry (`src/strategies/ensemble.py:57`). It loops every active strategy, skips the originator (`src/strategies/ensemble.py:62-64`), and calls `strategy.vote(symbol, direction, candles, ta_data, sentiment_data, altdata)` on each one (`src/strategies/ensemble.py:66-73`). Each return tuple `(vote_str, confidence, reasoning)` becomes an `EnsembleVote` dataclass entry with the strategy's `ensemble_weight` pulled from its performance record (`src/strategies/ensemble.py:74-83`). Failures inside a strategy's `vote()` are logged at WARNING and skipped (`src/strategies/ensemble.py:84-88`).

Weighted aggregation runs through `_capped_contribution(vote_str)` (`src/strategies/ensemble.py:101-115`), which sums `weight * confidence` for each side and applies a configurable single-strategy cap (`strategy_engine.single_strategy_max_share`, default 1.0 = no cap; commented at `src/strategies/ensemble.py:90-99`). `buy_votes` and `sell_votes` are the capped sums (`src/strategies/ensemble.py:117-118`); `neutral_votes` is the unweighted-confidence weight sum of NEUTRAL voters (`src/strategies/ensemble.py:119`).

Consensus is computed from the agreeing/opposing pair relative to the setup's direction (`src/strategies/ensemble.py:121-138`). The thresholds:

- STRONG: agreeing >= 4.0 AND opposing <= 1.5 (`src/strategies/ensemble.py:128-129`).
- GOOD: agreeing >= `cfg.min_ensemble_agreement` AND opposing <= `cfg.max_ensemble_opposition` (`src/strategies/ensemble.py:130-131`). Defaults are 5.0 / 1.0 in `src/config/settings.py:1271-1272`; the live `config.toml:827-828` overrides to 2.5 / 2.5.
- WEAK: agreeing >= 1.5 AND opposing <= 1.5 (`src/strategies/ensemble.py:132-133`).
- LEAN: agreeing > opposing otherwise (`src/strategies/ensemble.py:134-135`).
- CONFLICT: any remaining case (`src/strategies/ensemble.py:136-138`), with a WARN log at `src/strategies/ensemble.py:138`.

`size_multiplier` is keyed off consensus via `CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}` (`src/strategies/ensemble.py:126`) and then scaled by structural confidence in the XRAY Phase 5c block (`src/strategies/ensemble.py:142-158`). The scaling reads `setup.scoring_details["setup_type_confidence"]`, clamps it to `[0.5, 1.0]`, and multiplies into `size_mult`. Default when the field is absent: 0.85 (`src/strategies/ensemble.py:154-156`).

The result is materialised as an `EnsembleResult` (`src/strategies/models/signal_types.py:82-95`) carrying the full `votes` list, `buy_votes`, `sell_votes`, `neutral_votes`, `consensus_strength`, `consensus_direction`, `passed` (always True since `passed=True` at `src/strategies/ensemble.py:168`, with comment at `src/strategies/models/signal_types.py:91`), and `size_multiplier`. The dataclass also carries a `vote_distribution_dict(reasoning_truncate=140)` helper that flattens `votes` to a `{strategy_name: {vote, confidence, weight, reasoning}}` map (`src/strategies/models/signal_types.py:109-143`); reasoning is truncated to keep the per-coin cache bounded.

`STRAT_VOTE_TRACE` is emitted for every STRONG-consensus result (`src/strategies/ensemble.py:207-227`). The emission is gated by `strategy_engine.vote_trace_enabled` (default True) and includes the full vote list with per-strategy `name`, `vote`, `confidence`, and `weight`. Emission failures log `STRAT_VOTE_TRACE_FAIL` at DEBUG (`src/strategies/ensemble.py:224-227`). A second log marker `ENSEMBLE_VOTE_WEIGHTED` fires at INFO only when struct_conf < 0.85, i.e. when the conf scaling actually moved size_mult (`src/strategies/ensemble.py:187-194`). And `vote_batch` emits `ENSEMBLE_CONFLICT` (already covered at `src/strategies/ensemble.py:138`) and a cycle-level `ENSEMBLE | setups=... strong=... good=... weak=... conflict=...` rollup (`src/strategies/ensemble.py:250-253`).

The prior recon's "K1 trigger" claim — that the ensemble triggers K1 when score>=80 AND consensus=STRONG — is partially correct but mislocated. `K1_claude_conviction.scan()` (`src/strategies/categories/k1_claude_conviction.py:31-51`) gates on `altdata["k1_trigger"]` being present, `trigger["symbol"] == symbol`, `trigger["score"] >= 80`, and `trigger["consensus"] == "STRONG"`. However, a project-wide grep finds NO production callsite that injects `k1_trigger` into `altdata`. The only references to `k1_trigger` are inside `k1_claude_conviction.py` itself (the gate and a docstring). K1 is effectively wired to never fire today; see the K-class section below.

## K-Class Meta-Strategies — Individual Status

### K1 — claude_conviction

- File: `src/strategies/categories/k1_claude_conviction.py`, 84 lines.
- `scan()` contract (`src/strategies/categories/k1_claude_conviction.py:31`): returns a `RawSignal` only when (a) `altdata["k1_trigger"]` is present (`src/strategies/categories/k1_claude_conviction.py:38`), (b) the trigger's `symbol` matches the scan symbol (`src/strategies/categories/k1_claude_conviction.py:44-45`), (c) `trigger["score"] >= 80` AND `trigger["consensus"] == "STRONG"` (`src/strategies/categories/k1_claude_conviction.py:46-47`). Returns a RawSignal with `conditions_strength["conviction"] = min(score/100, 1.0)` (`src/strategies/categories/k1_claude_conviction.py:67`, `src/strategies/categories/k1_claude_conviction.py:78`).
- `vote()` contract (`src/strategies/categories/k1_claude_conviction.py:82-84`): hardcoded `return ("NEUTRAL", 0.0, "K1 does not vote — deep analysis only")`. Confidence is structurally 0.0 by design.
- WHY conf=0.0: intentional non-voter. The module's class docstring (`src/strategies/categories/k1_claude_conviction.py:1-5`) declares K1 the ONLY strategy that makes an external API call in `scan()`, and `vote()`'s reasoning string makes it explicit: K1's role is to generate a signal when triggered, not to weigh in on other strategies' setups.
- Dependencies: requires `altdata["k1_trigger"]` from an upstream injector. NOT FOUND in production — no callsite anywhere in `src/` (excluding the K1 file itself) writes to `altdata["k1_trigger"]`. `strategy_worker.py`'s altdata builder (`src/workers/strategy_worker.py:389-491`) populates only `fear_greed`, `fear_greed_value`, `funding`, `funding_rate`, and `oi_change_24h_pct` per symbol; it does not inject `k1_trigger`. The K1 scan loop runs (`src/workers/strategy_worker.py:580-587`) but the gate at line 38 always fails.
- Classification: DISABLED-BY-DESIGN for `vote()` (it never had a voting role), PENDING-IMPLEMENTATION for `scan()` (the trigger injector was never wired). The scan branch is reachable but unreachable in practice — Layer 4 (`brain/strategist.py`) is the production "claude conviction" path and K1 was likely deprecated when Layer 4 took over.

### K2 — pattern_memory

- File: `src/strategies/categories/k2_pattern_memory.py`, 105 lines.
- `scan()` contract (`src/strategies/categories/k2_pattern_memory.py:35`): returns a `RawSignal` when (a) `altdata["pattern_matches"]` is a list of >= 5 entries (`src/strategies/categories/k2_pattern_memory.py:45-47`), (b) the up_rate or down_rate >= 0.7 with confidence >= 0.5 (`src/strategies/categories/k2_pattern_memory.py:61-91`). Confidence ladder: total >= 20 AND up_rate > 0.85 → 0.85; total >= 10 AND up_rate > 0.8 → 0.7; total >= 5 AND up_rate > 0.7 → 0.5; else 0.0 (`src/strategies/categories/k2_pattern_memory.py:61-68`).
- `vote()` contract (`src/strategies/categories/k2_pattern_memory.py:94`): returns BUY when direction=BUY AND up_rate > 0.6, SELL when direction=SELL AND up_rate < 0.4, NEUTRAL otherwise (`src/strategies/categories/k2_pattern_memory.py:101-105`). Returns NEUTRAL with conf=0.2 when no pattern history is available (`src/strategies/categories/k2_pattern_memory.py:95-97`).
- WHY conf=0.0 (or nearly so): BROKEN-MISSING-DATA. K2's `vote()` is structurally able to return non-zero confidence, but only when `altdata["pattern_matches"]` is populated with at least 5 historical pattern outcomes. A project-wide grep confirms NO production callsite injects `altdata["pattern_matches"]`. `pattern_log` IS persisted: `src/database/migrations.py:300` creates the `pattern_log` table, `src/database/repositories/learning_repo.py:129` inserts patterns on signal creation, `src/database/repositories/learning_repo.py:137` updates outcomes on resolution, and `src/database/repositories/learning_repo.py:143` exposes a query. But `strategy_worker.py`'s altdata builder (`src/workers/strategy_worker.py:389-491`) does NOT query `pattern_log` — there is no `learning_repo` lookup in the build path. K2 therefore always falls into the "NEUTRAL, conf=0.2, 'No pattern history'" branch.
- Path that should fill `altdata["pattern_matches"]`: the altdata builder at `src/workers/strategy_worker.py:389-491` should call `learning_repo.query_resolved_patterns(symbol, pattern_signature)` and append matches to `altdata_per_sym[sym]["pattern_matches"]`. That call is NOT FOUND today.
- Classification: BROKEN-MISSING-DATA. The strategy's code is sound; the upstream wiring never landed.

### K3 — ensemble

- File: `src/strategies/categories/k3_ensemble.py`, 29 lines.
- `scan()` contract (`src/strategies/categories/k3_ensemble.py:25-26`): unconditional `return None` with comment "Ensemble logic is in ensemble.py".
- `vote()` contract (`src/strategies/categories/k3_ensemble.py:28-29`): hardcoded `return ("NEUTRAL", 0.0, "K3 does not vote — it IS the voting system")`.
- WHY conf=0.0: DISABLED-BY-DESIGN. The class docstring (`src/strategies/categories/k3_ensemble.py:10-11`) makes it explicit: K3 is the placeholder for the ensemble system itself, registered for registry completeness, but the actual logic is `EnsembleVoter` in `src/strategies/ensemble.py`. Voting on its own setups would create a circular reference.
- Dependencies: none.
- Classification: DISABLED-BY-DESIGN. Placeholder registration is correct as-is.

### K4 — adaptive_optimizer

- File: `src/strategies/categories/k4_adaptive_optimizer.py`, 29 lines.
- `scan()` contract (`src/strategies/categories/k4_adaptive_optimizer.py:25-26`): unconditional `return None` with comment "Optimizer logic is in optimizer.py".
- `vote()` contract (`src/strategies/categories/k4_adaptive_optimizer.py:28-29`): hardcoded `return ("NEUTRAL", 0.0, "K4 does not vote — it optimizes other strategies")`.
- WHY conf=0.0: DISABLED-BY-DESIGN. K4 is the weekly parameter/weight tuner (timeframe = `TimeFrame.W1` at `src/strategies/categories/k4_adaptive_optimizer.py:21`). It is a metadata strategy; its job is to update `StrategyPerformance.ensemble_weight` (defined at `src/strategies/models/signal_types.py:195`) so the ensemble's weighted sums reflect rolling win-rate, not to trade itself.
- Dependencies: lives outside the ensemble vote loop entirely.
- Classification: DISABLED-BY-DESIGN.

Summary across K-class: only K2 is broken in a way that prompt enrichment cares about. K1's scan path is also unwired, but K1's role is being filled by Layer 4 (Claude itself), so wiring it would duplicate effort. K3 and K4 are placeholders that never were meant to vote.

## Vote Data Persistence

Vote data lives in two LayerManager caches, both in-memory only. Neither is persisted to disk between processes; both are merged across cycles by `dict.update`.

The aggregate consensus cache is `LayerManager._strategy_consensus` (`src/core/layer_manager.py:104`), with the legacy summary alias `_strategy_consensus_summary` (`src/core/layer_manager.py:106`) for strategist reads. The per-coin entry shape is `{"consensus", "consensus_score", "vote_count", "direction", "last_updated"}` (`src/core/layer_manager.py:1660-1672`, accessor `get_strategy_consensus(symbol)`).

The full vote distribution cache is `LayerManager._strategy_votes` (`src/core/layer_manager.py:116`). Shape (documented in `src/core/layer_manager.py:1674-1711`):

```
{
  "votes": {
    "<strategy_name>": {
      "vote":       "BUY" | "SELL" | "NEUTRAL",
      "confidence": float,
      "weight":     float,
      "reasoning":  str (truncated to 140 chars),
    },
    ...
  },
  "buy_weighted":     float,
  "sell_weighted":    float,
  "neutral_weighted": float,
  "consensus":        str,
  "consensus_direction": str,
  "size_multiplier":  float,
  "last_updated":     float,
}
```

The memory budget is documented at `src/core/layer_manager.py:113-115`: ~320 KB for 50 coins × 25 strategies × 250 bytes per entry. The accessor is `get_strategy_votes(symbol)` (`src/core/layer_manager.py:1674`).

Both caches are written by `StrategyWorker` once per cycle after L3 (ensemble) completes. The consensus cache is built by `_build_per_coin_consensus(consensus_setups)` (`src/workers/strategy_worker.py:1251-1307`), which picks the highest-`total_score` setup per symbol (`src/workers/strategy_worker.py:1290-1300`) and stores the consensus aggregate. The votes cache is built by `_build_per_coin_votes(consensus_setups)` (`src/workers/strategy_worker.py:1309-1412`), which uses the same selection rule (`src/workers/strategy_worker.py:1391-1392`) so the two caches stay consistent. The vote distribution comes from `EnsembleResult.vote_distribution_dict()` (`src/workers/strategy_worker.py:1360-1361`, defined at `src/strategies/models/signal_types.py:109-143`).

The cache-merge logic is at `src/workers/strategy_worker.py:861-898`. The consensus cache merges via `existing.update(new_consensus)` (`src/workers/strategy_worker.py:874`) so coins not processed this tick keep their previous entry; the votes cache merges identically (`src/workers/strategy_worker.py:889-894`). Stale entries are intentional — `last_updated` lets the reader judge freshness. The legacy summary alias `_strategy_consensus_summary` is rebuilt from `filtered` (post-PnL-restriction) setups via `_build_consensus_summary` (`src/workers/strategy_worker.py:1232-1249`); shape is `{symbol: {"buy", "sell", "total_score"}}` — a counted-trades shape, not a weighted shape.

Observability for the cache write is `STRAT_CONSENSUS_WRITE` (`src/workers/strategy_worker.py:904-911`), which reports `full_count`, `filtered_count`, `setups_in`, `cache_size_after`, `votes_cache_size`, mode, and threshold. Per-coin transitions emit `STRAT_CONSENSUS_CHANGE` (`src/workers/strategy_worker.py:915-923`), and the cycle distribution emits `STRAT_CONSENSUS_SUMMARY` (`src/workers/strategy_worker.py:926-937`).

Lifetime: in-memory, persisted only across `dict.update` merges within the same process. The cache survives StrategyWorker ticks but does NOT survive a process restart. A cold-start window will return None from `get_strategy_votes` for every symbol until StrategyWorker completes its first cycle. There is no DB-backed mirror.

A separate cache `LayerManager._strategy_hints` (`src/core/layer_manager.py:118`) holds the top-20 filtered setups as a list of `{symbol, direction, strategy, score, consensus}` dicts (`src/workers/strategy_worker.py:945-958`). This cache is gated behind `layer_manager.is_layer_active(3)` (`src/workers/strategy_worker.py:939-966`); when Layer 3 is off the cache stays at its previous state. The hints cache is fundamentally legacy — its shape predates the per-coin votes cache and contains far less detail.

## Strategist Access To Vote Data Today

The strategist reads vote data in three distinct places. The default-on production path goes through `_format_packages_for_prompt` (`src/brain/strategist.py:1528`), which is called from the CALL_A prompt builder at `src/brain/strategist.py:2479-2481` when `[stage2].enable_full_layer_block` is False, or `_format_packages_for_prompt_full` (`src/brain/strategist.py:1814`) called at `src/brain/strategist.py:2475-2477` when enable_full_layer_block is True. Both formatters dispatch to the same vote-rendering helper.

The vote rendering helper is `_format_briefing_extras(lines, pkg)` (`src/brain/strategist.py:1728-1796`), called from `_format_packages_for_prompt` at line 1705 (inside the `if surface_briefing:` branch). It reads `layer_manager.get_strategy_votes(pkg.symbol)` (`src/brain/strategist.py:1737-1741`), then renders:

- A `Votes: BUY=X.XX vs SELL=Y.YY (N voters)` summary line (`src/brain/strategist.py:1766-1769`).
- A `Top BUY: <name (c0.XX,w1.00), ...>` line for the 3 highest BUY voters by `confidence * weight` (`src/brain/strategist.py:1748-1756`, rendered at `src/brain/strategist.py:1770-1774`).
- A `Top SELL: ...` line for the 3 highest SELL voters (`src/brain/strategist.py:1757-1765`, rendered at `src/brain/strategist.py:1775-1779`).
- A `State: cleanness=X.XX confluence=N top_components=[...]` line if `pkg.interestingness_breakdown` is populated (`src/brain/strategist.py:1781-1794`).

This rendering is gated by `[brain].surface_briefing_fields`. The defensive read at `src/brain/strategist.py:1561-1563` (and at `src/brain/strategist.py:1845-1847` for the full block) uses `getattr(_brain_cfg, "surface_briefing_fields", False)` as a fail-closed default. The dataclass default in `src/config/settings.py:492` is `True` (with comment at `src/config/settings.py:488-491` documenting the Phase 9 cutover flip), but the TOML loader at `src/config/settings.py:2997` defaults to `False` when the key is absent in config.toml. The actual production config.toml at line 204 (`config.toml:204`) sets `surface_briefing_fields = true`. So in production the briefing-extras render IS active.

The system-prompt suffix `BRIEFING_SYSTEM_PROMPT_SUFFIX` (`src/brain/strategist.py:196-250`) explains the Votes block to Claude. It is appended to the trade system prompt at `src/brain/strategist.py:688-691` when `surface_briefing_fields` is True.

The CALL_B (position-review) path and the legacy CALL_A path also touch vote data, but via the legacy `_strategy_hints` and `_strategy_consensus_summary` caches, not via `get_strategy_votes`. `_build_trade_prompt` (Stage 2 phase 1 path) at `src/brain/strategist.py:1376-1411` renders a "STRATEGY HINTS (automated signals)" block from `layer_manager._strategy_hints` (top 20) plus a "CONSENSUS PER COIN" sub-block from `_strategy_consensus_summary` (top 15 by total_score). The same block re-renders at `src/brain/strategist.py:2804-2839` in the alternative prompt path. Both legacy renderings use the counted-trades shape (`{symbol: {buy, sell, total_score}}`) — they show "N buy / M sell (total score: S)" per coin, not the per-strategy distribution.

The strategist therefore reads three different vote-related caches:

1. `get_strategy_votes(symbol)` — full distribution, rendered into the per-coin Top BUY / Top SELL block. Active only inside packages when `surface_briefing_fields=True`.
2. `_strategy_consensus_summary` — counted-trades aggregate, rendered into the legacy "CONSENSUS PER COIN" block. Active always.
3. `_strategy_hints` — top-20 filtered setups, rendered into "STRATEGY HINTS". Active always.

The prior claim "Per-coin vote summary infrastructure already exists. Need to verify enabled + format consistently across all candidate blocks" is confirmed for path 1 (the briefing-mode Top-3 block exists and is on in production), but partially refuted for the broader picture: legacy paths 2 and 3 render parallel, format-inconsistent information. A coin can appear with the Top-3 distribution AND the counted-trades summary in the same prompt; the brain has to reconcile them. The cap at 15 (path 2) and 20 (path 3) differs from the package-block cap (`[stage2].top_n_to_brain`, default 6, at `src/brain/strategist.py:2434-2435`), so the three blocks can describe different coin sets.

## Opposition + Category Split Data Availability

For E2 (vote opposition), `_strategy_votes[symbol]` already contains `buy_weighted`, `sell_weighted`, and `neutral_weighted` (`src/workers/strategy_worker.py:1394-1397`, source `src/strategies/ensemble.py:117-119`). These are the rest-of-ensemble weighted sums, capped per `single_strategy_max_share`. The agreeing/opposing pair is implicit: for a BUY-direction setup, agreeing=buy_weighted, opposing=sell_weighted; for SELL, reversed. The cached `consensus_direction` field (`src/workers/strategy_worker.py:1381-1383`, mirroring `EnsembleResult.consensus_direction` from `src/strategies/models/signal_types.py:90`) tells the reader which side is "agreeing". Today `_format_briefing_extras` renders both as `BUY=X.XX vs SELL=Y.YY` (`src/brain/strategist.py:1766-1769`) — the data is already there.

For E3 (category split), the votes cache stores the strategy NAME (e.g. `"A1_rsi_reversal"`) but NOT the strategy's CATEGORY. The category lives only on the strategy object via its `category` property (e.g. `src/strategies/categories/a1_rsi_reversal.py:16` returns `"scalping"`). The cache write path in `EnsembleResult.vote_distribution_dict()` (`src/strategies/models/signal_types.py:132-142`) only copies `strategy_name`, `vote`, `confidence`, `weight`, and `reasoning` — it does NOT include category. The fallback path in `_build_per_coin_votes` (`src/workers/strategy_worker.py:1362-1376`) is identical: no category.

To surface a category split (e.g. "BUY votes: scalping=3.2, momentum=2.1, sentiment=1.5"), the strategist would either need to (a) maintain a `{strategy_name → category}` lookup table built at registry-init time and join on read, or (b) extend `EnsembleVote` to carry category (`src/strategies/models/signal_types.py:71-78`) and propagate it through `vote_distribution_dict` and `_build_per_coin_votes`. Option (a) is non-invasive — the registry already exposes `get_active_for_regime()` strategy objects with `.category` available. The prefix of the strategy name (the leading letter A-K) also implicitly carries the category since registration is alphabetical and 1:1 with the category table; a simple `name.split("_")[0][0]` extraction would group correctly without any data-model change.

"Available for free" if the prompt were to surface it:

- E2 (opposition): YES. `buy_weighted`, `sell_weighted`, `neutral_weighted` already in `_strategy_votes[symbol]`. The Top-3 BUY / Top-3 SELL renderer already shows both sides per-strategy. A dedicated opposition line ("4.2 weighted opposition from sell side, conf>=0.7 on all 3") only needs the existing fields.
- E3 (category split): NO direct field. But trivially derivable via name-prefix grouping (no schema change) or via a 39-entry name-to-category map. The work is purely in the prompt-render layer, not the data layer.

## Layer 1D Briefing Pipeline (scanner_worker.py)

Target 8 will deepen this section; the summary below is bounded to what is necessary for Target 1.7 context.

There is no `briefing_full.jsonl` file in the codebase. A project-wide grep confirms no `.jsonl`, no `briefing_log`, and no `briefing_full*` writer in `src/`. The briefing-pipeline output is in-memory only, living in `LayerManager._coin_packages` (`src/core/layer_manager.py:123`) and read by the strategist via `layer_manager.get_coin_packages()` (`src/core/layer_manager.py:1713-1720`). Cache writes happen in `ScannerWorker` at `src/workers/scanner_worker.py:1284` (exclusion-mode) and `src/workers/scanner_worker.py:1816` (briefing-mode); both lines do `lm._coin_packages = packages`. There is no jsonl persistence; restart loses the cache.

Interestingness score reaches the prompt through `CoinPackage.interestingness_score`. It is computed inside `ScannerWorker._build_package` (`src/workers/scanner_worker.py:812-865`) by calling `compute_interestingness(...)` (`src/workers/scanner/interestingness.py` — Target 8 covers details) with weights pulled from `settings.scanner.briefing.interestingness_weights` (`src/workers/scanner_worker.py:817-830`). The resulting score and breakdown are attached to the `CoinPackage` (`src/workers/scanner_worker.py:885-888`). The strategist reads it via `getattr(pkg, "interestingness_score", 0.0)` for sorting (`src/brain/strategist.py:1575-1577`, `src/brain/strategist.py:2446-2447`) and rendering (`src/brain/strategist.py:1653-1654`, `src/brain/strategist.py:1656-1660`).

State labels are assigned by `label_state(...)` from `src/workers/scanner/state_labeler.py`, imported at `src/workers/scanner_worker.py:41` and called at `src/workers/scanner_worker.py:797-803`. The label constants are defined at `src/workers/scanner/state_labeler.py:59-82`: `TREND_PULLBACK_LONG/SHORT`, `RANGE_FADE_LONG/SHORT`, `BREAKOUT_PENDING`, `LIQUIDITY_SWEEP_REVERSAL_LONG/SHORT`, `FUNDING_EXTREME_FADE_LONG/SHORT`, `COUNTER_TRADE_LONG/SHORT`, `MOMENTUM_BURST_LONG/SHORT`, `OB_MITIGATED_FVG_ONLY_LONG/SHORT`, `KILL_ZONE_OPPORTUNITY`, `EXTREME_FEAR_LONG_BIAS`, `EXTREME_GREED_SHORT_BIAS`, and advisory-only `MANIPULATION_WINDOW`, `RECENT_LOSER_COOLDOWN`, `NO_TRADEABLE_STATE`, `OPEN_POSITION_HOLD_REVIEW`. The advisory set is a frozenset at `src/workers/scanner/state_labeler.py:84-88`. `ACTION_HINTS` (referenced by `src/brain/strategist.py:1806`) maps each primary label to a one-line action hint surfaced into the prompt by `_format_action_hint` (`src/brain/strategist.py:1798-1812`).

## Verdict

E1 — per-coin vote summary. Infrastructure exists end-to-end. Production-on. `LayerManager._strategy_votes` carries the full distribution (`src/core/layer_manager.py:116`); `get_strategy_votes(symbol)` returns it (`src/core/layer_manager.py:1674`); `_format_briefing_extras` renders Top-3 BUY / Top-3 SELL into the prompt (`src/brain/strategist.py:1728-1796`); `surface_briefing_fields` is on in `config.toml:204`. The gap is consistency: the legacy `_strategy_hints` (`src/brain/strategist.py:1380-1387`) and `_strategy_consensus_summary` (`src/brain/strategist.py:1394-1411`) render parallel, format-inconsistent vote information in the same prompt, on top-15/top-20 universes that differ from the package universe (default top-6). Enrichment work should consolidate.

E2 — vote opposition. Data fully available in `_strategy_votes[symbol]` as `buy_weighted`, `sell_weighted`, `neutral_weighted`, `consensus_direction` (`src/workers/strategy_worker.py:1394-1399`). The summary line already exists at `src/brain/strategist.py:1766-1769`. A more pointed "opposition strength" line ("opposing side weight 4.2 with 3 voters above conf 0.7") is renderable from existing fields with zero data-model change.

E3 — category split. Data NOT directly available. The cache stores strategy NAME but not CATEGORY (`src/strategies/models/signal_types.py:132-142` and `src/workers/strategy_worker.py:1362-1376`). However, derivation is trivial: either prefix-map the name (A/B/C/D/E/F/G/H/I/J/K → scalping/momentum/mean_reversion/funding_arb/sentiment/advanced/predatory/microstructure/time_based/cross_market/ai_enhanced) inside the renderer, or extend `EnsembleVote` (`src/strategies/models/signal_types.py:71-78`) to carry `strategy_category` and propagate it through `vote_distribution_dict`. Either approach is rendering-layer-only work.

E7 — K-class outputs. Of the four K strategies, only K2 is broken in a way that prompt enrichment cares about. K1 is unwired in scan (no `altdata["k1_trigger"]` injector exists in `src/`) but its role is filled by Layer 4; classifying it as DISABLED-BY-DESIGN for vote and PENDING-IMPLEMENTATION for scan reflects production reality. K2 is BROKEN-MISSING-DATA: the `pattern_log` table is persisted (`src/database/migrations.py:300`) and updated on resolution (`src/database/repositories/learning_repo.py:137`), but `strategy_worker.py`'s altdata builder (`src/workers/strategy_worker.py:389-491`) does NOT query it, so `altdata["pattern_matches"]` is never populated and K2's `vote()` always returns NEUTRAL conf=0.2 (`src/strategies/categories/k2_pattern_memory.py:96-97`). K3 and K4 are DISABLED-BY-DESIGN placeholders for the ensemble system itself and the weekly optimiser; their vote()'s returning ("NEUTRAL", 0.0, ...) is correct.

The honest count of currently-voting strategies inside the ensemble is therefore 39 registered minus K1 (intentional non-voter), K2 (data-starved non-voter), K3 (placeholder), K4 (placeholder), and the originator skip (`src/strategies/ensemble.py:63-64`) = 34 effective voters per setup (minus one for the originator = 33). Adding K2 to the active voter pool requires a one-line addition to the altdata builder (a `learning_repo.query_resolved_patterns` call mirroring the F&G and funding fetches) and would lift K2 from `("NEUTRAL", 0.2)` to a real BUY/SELL conf in the 0.5-0.85 band whenever historical patterns exist.
