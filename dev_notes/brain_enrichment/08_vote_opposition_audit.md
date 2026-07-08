# Vote Opposition Audit — E1/E2/E3 Enrichment Plan

Scope: confirm the existing per-coin vote rendering (E1), then propose
prompt-ready formats for the vote-opposition flag (E2) and the category
split (E3). Both proposed enrichments reuse the existing
`_strategy_votes[symbol]` cache — no new DB queries, no new pipelines.

## Files Involved

| Path | Lines | Role |
|---|---|---|
| `src/strategies/ensemble.py` | 256 | `EnsembleVoter.vote` — weighted vote aggregator, source of `STRAT_VOTE_TRACE` |
| `src/strategies/register_all.py` | 132 | Registers 39 strategies (19 in `a-f`, 20 in `g-k`, + X1 testnet) |
| `src/strategies/registry.py` | n/a | `StrategyRegistry.get_by_category` (`registry.py:55-57`) and JSON export with `category` (`registry.py:113`) |
| `src/workers/strategy_worker.py:1309-1412` | 104 | `_build_per_coin_votes` — populates `_strategy_votes` cache |
| `src/core/layer_manager.py:110-123` | 14 | `_strategy_votes: dict[str, dict]` cache declaration |
| `src/core/layer_manager.py:1674-1711` | 38 | `get_strategy_votes(symbol)` public accessor |
| `src/brain/strategist.py:1728-1796` | 69 | `_format_briefing_extras` — current per-coin vote renderer (E1) |

## Strategy Count and Category Mapping

`register_strategies_a_to_f` registers 19 strategies (`register_all.py:10-57`)
and `register_strategies_g_to_k` registers 20 (`register_all.py:60-109`), total
39. X1 (`AlwaysTradeStrategy`) is registered only on testnet
(`register_all.py:118-130`), so live mainnet runs at 39.

The 10 categories (defined as a `category` property on each strategy module —
see `src/strategies/categories/a1_rsi_reversal.py:16` "scalping",
`b2_supertrend_follower.py:16` "momentum", etc.):

| Category | Strategies | Count |
|---|---|---|
| `scalping` | A1, A2, A3, A4 | 4 |
| `momentum` | B1, B2, B3, B4 | 4 |
| `mean_reversion` | C1, C2 | 2 |
| `funding_arb` | D1, D2 | 2 |
| `sentiment` | E1, E2, E3 | 3 |
| `advanced` | F1, F2, F3, F4 | 4 |
| `predatory` | G1, G2, G3, G4 | 4 |
| `microstructure` | H1, H2, H3, H4 | 4 |
| `time_based` | I1, I2, I3, I4 | 4 |
| `cross_market` | J1, J2, J3, J4 | 4 |
| `ai_enhanced` | K1, K2, K3, K4 | 4 |

That's 11 categories totalling 39 (plus the testnet-only X1). The categories
are accessible via `StrategyRegistry.get_by_category(name)`
(`registry.py:55-57`) — no additional plumbing needed, the data is already in
the registry.

## STRAT_VOTE_TRACE Field Schema

Emitted from `ensemble.py:219-223` for every STRONG-consensus vote when
`settings.strategy_engine.vote_trace_enabled` is `True` (default True per
`ensemble.py:211`). Live sample from `data/logs/workers.log` (cycle
`sid=s-1778862990002`, 2026-05-15 16:36:32 UTC, CRVUSDT):

```
STRAT_VOTE_TRACE | sym=CRVUSDT consensus=STRONG agreeing=5.62 opposing=0.00
votes=[name=A1_rsi_reversal,vote=NEUTRAL,conf=0.30,weight=1.00;
       name=A2_vwap_bounce,vote=NEUTRAL,conf=0.30,weight=1.00;
       name=A4_ema_crossover,vote=BUY,conf=0.65,weight=1.00;
       name=B1_volume_breakout,vote=BUY,conf=0.70,weight=1.00;
       name=B2_supertrend,vote=BUY,conf=0.77,weight=1.00;
       ... 36 more ...
       name=K4_optimizer,vote=NEUTRAL,conf=0.00,weight=1.00] | sid=...
```

Top-level fields:

- `sym` — trading pair, mirrors `setup.raw_signal.symbol`.
- `consensus` — `STRONG | GOOD | LEAN | WEAK | CONFLICT` per
  `ensemble.py:128-138`.
- `agreeing`, `opposing` — float weighted contributions on the **directional**
  side (i.e. if direction=BUY then `agreeing=buy_votes`, `opposing=sell_votes`,
  per `ensemble.py:121-122`).

Per-vote tuple inside `votes=[…]` (built via the list comprehension at
`ensemble.py:214-218`):

- `name` — strategy identifier (`A1_rsi_reversal`, `B2_supertrend`, …).
- `vote` — `BUY | SELL | NEUTRAL`.
- `conf` — `EnsembleVote.confidence`, rendered to 2 dp.
- `weight` — `EnsembleVote.weight`, rendered to 2 dp
  (currently uniform 1.00 because `ensemble_weight` per
  `StrategyRegistry.get_performance` defaults to 1.0 until performance data
  accumulates).

The sample CRVUSDT row carries 37 votes (one strategy skipped because the
originator is filtered at `ensemble.py:63-64`). On the GALAUSDT sample 35
strategies voted — the active set is regime-filtered via
`StrategyRegistry.get_active_for_regime` (`ensemble.py:57`).

**Confirmed:** `buy_weighted`, `sell_weighted`, `neutral_weighted` are NOT in
the `STRAT_VOTE_TRACE` log line — those are cache-level fields. The log shows
the **directional** aggregates `agreeing` / `opposing`. The buy/sell/neutral
breakdown lives in the `_strategy_votes` cache (next section).

## `_strategy_votes[symbol]` Cache Schema

Declared at `src/core/layer_manager.py:116`
(`self._strategy_votes: dict[str, dict] = {}`). Schema documented inline at
`layer_manager.py:110-115` and again at the accessor docstring
`layer_manager.py:1681-1698`. Each entry shape, written by
`_build_per_coin_votes` (`strategy_worker.py:1393-1403`):

```python
{
    "votes": {
        "<strategy_name>": {
            "vote":       "BUY" | "SELL" | "NEUTRAL",
            "confidence": float,
            "weight":     float,
            "reasoning":  str,    # truncated to 140 chars
        },
        ...   # one entry per voting strategy
    },
    "buy_weighted":      float,   # rounded to 4 dp
    "sell_weighted":     float,
    "neutral_weighted":  float,
    "consensus":         str,     # STRONG | GOOD | LEAN | WEAK | CONFLICT
    "consensus_direction": str,   # BUY | SELL
    "size_multiplier":   float,
    "last_updated":      float,   # unix ts
}
```

Memory budget: ~320 KB for 50 coins × 25 strategies × 250 bytes
(`layer_manager.py:114-115`).

Accessor: `LayerManager.get_strategy_votes(symbol)` at `layer_manager.py:1674`
returns the full entry or `None` if StrategyWorker hasn't processed the coin
yet (stale entries are merge-preserved per `layer_manager.py:1701-1702`).

## E1 — Per-Coin Vote Summary: Already Rendered

The renderer is `ClaudeStrategist._format_briefing_extras` at
`strategist.py:1728-1796`, gated by `[brain].surface_briefing_fields`
(`strategist.py:1731`). It already produces three lines per coin when votes
are available:

Read at `strategist.py:1766-1779`:

```python
lines.append(
    f"  Votes: BUY={buy_w:.2f} vs SELL={sell_w:.2f} "
    f"({total_voters} voters)"
)
if buy_voters:
    s = ", ".join(
        f"{n} (c{c:.2f},w{w:.2f})" for n, c, w in buy_voters
    )
    lines.append(f"    Top BUY: {s}")
if sell_voters:
    s = ", ".join(
        f"{n} (c{c:.2f},w{w:.2f})" for n, c, w in sell_voters
    )
    lines.append(f"    Top SELL: {s}")
```

Top-N selection at `strategist.py:1748-1765` — top 3 BUY and top 3 SELL voters
ranked by `confidence × weight`, descending. Aggregates `buy_w` / `sell_w` are
read directly from the cache (`strategist.py:1744-1746`,
`votes_entry.get("buy_weighted")` / `get("sell_weighted")`). Total voter count
is `len(votes_dict)` (`strategist.py:1746`).

So the prior-agent claim "Top-3 BUY / Top-3 SELL already renders" is **verified**.
Example output for a STRONG-BUY coin:

```
  Votes: BUY=5.62 vs SELL=0.00 (37 voters)
    Top BUY: F2_multi_tf_alignment (c0.85,w1.00), B2_supertrend (c0.77,w1.00), G4_whale_shadow (c0.75,w1.00)
```

E1 is shipped and rendering. **No change required for E1.**

## E2 — Vote Opposition Flag: Proposed Format

The cache already exposes `sell_weighted` (or `buy_weighted` for SELL trades)
as the "opposing" measure, plus a vote count derivable from
`{name: vote for ...}` in `votes_dict`. Three rendering options, from
compact to verbose:

**Option A — STRONG/MODERATE/WEAK label (~45 chars):**

```
  Opposition: STRONG (4 voters at conf>=0.6, wsum=2.85)
  Opposition: WEAK (1 voter at conf>=0.6, wsum=0.30)
  Opposition: NONE
```

Computation: count voters whose `vote` is the opposite direction AND
`confidence >= 0.6`. Bucket by count: `0 → NONE`, `1-2 → WEAK`, `3-4 →
MODERATE`, `5+ → STRONG`. `wsum = Σ (weight × confidence)` for that filtered
set. Drop the line when count=0 to save tokens (the absence is itself signal).

**Option B — compact agree/oppose pair (~30 chars):**

```
  agree=10/5.10w  opp=4/2.85w
  agree=8/4.20w   opp=0/0.00w
```

Where `agree` = count of voters in the consensus direction, `opp` = count in
the opposite direction, suffix `w` denotes weighted sum (the `buy_weighted` /
`sell_weighted` already in the cache).

**Option C — single-line "opp ratio" (~40 chars):**

```
  Opposition: 4/14 voters (29 %), 2.85w vs 5.62w (0.51 ratio)
```

Where ratio = `opp_weighted / agree_weighted`. Operator gets a numeric
"how-much-pushback" feature in one number.

**Recommendation: Option A.** It's the most prompt-readable, matches the
existing prose style of `_format_briefing_extras`, and provides a discrete
label the brain can reason over ("STRONG opposition → demand harder thesis").
Numeric tail (`wsum=...`) lets the brain calibrate.

Implementation effort: **trivial**. Insertion goes inside the existing
`if votes_entry and isinstance(votes_entry, dict):` block at
`strategist.py:1742`, between the `Votes:` line and the `Top BUY:` line.
Compute opposition-direction from `consensus_direction` (or the trade's own
direction from `pkg`), filter `votes_dict.values()` by `vote != direction` and
`confidence >= threshold`, format. ~12 lines of code, zero new queries.

## E3 — Category Split: Proposed Format

Categories are not in the cache, but they're derivable in two ways:

1. **Strategy-name regex** — strategy keys in `_strategy_votes[…].votes` start
   with `A1_`, `B2_`, etc. The first letter maps to category by table at the
   top of this report.
2. **Registry lookup** — `StrategyRegistry.get_by_category(name)`
   (`registry.py:55-57`) is the existing API; `strat.category` is also exposed
   in `to_dict` output at `registry.py:113`.

Option 1 is simpler (one-line lambda) but coupled to naming convention. Option
2 is cleaner but requires passing the registry through to the strategist (the
strategist already holds `self.services` so injecting a `strategy_registry`
service is one line). Either works; option 1 is faster to ship.

Three rendering options:

**Option A — full per-category breakdown (~120-150 chars/coin):** all 11
categories, mostly zeros. Hard to read; skip.

**Option B — non-zero categories only (~50-80 chars/coin):**

```
  Cats: scalping 2B | momentum 4B | advanced 2B | predatory 1B
  Cats: scalping 1B/1S | momentum 0/2S | sentiment 0/1S
```

Drop categories with zero non-neutral votes. Format `<cat> <N>B[/MS]`. Drops
empty cells to keep the line scannable.

**Option C — single dominant + opposition (~30-40 chars/coin):**

```
  Cats: momentum dominates 4B | adv 2B follows | no SELL
  Cats: scalping 2B vs momentum 2S | mixed
```

Heuristic-classifies the category mix. Useful editorialised summary but loses
the raw numbers.

**Recommendation: Option B.** Same prose style as the existing Votes line.
Operator can see "this is a momentum / advanced-driven STRONG, not a scalping
agreement" at a glance. Hide the line entirely when only one category fires
(common for low-consensus coins).

Implementation effort: **small but not trivial.** Build a `strategy_name →
category` dict at startup (either from registry or from the first-letter
convention above), then in `_format_briefing_extras` group `votes_dict.items()`
by category, count B/S per group, render with Option B. ~25-35 lines, ~1-day
work at most.

## Prompt-Size Budget Math

Assumption: 15 coins in the briefing block (matches the current observed
volume — see `_format_briefing_extras` is called per-coin in the briefing
loop). Per-line costs measured against the existing `Top BUY:` line which
runs ~80 chars in production.

| Enrichment | Chars per coin (avg) | Chars × 15 coins | Approx tokens |
|---|---|---|---|
| E1 (already shipped) — `Votes:` + `Top BUY:` + `Top SELL:` | ~80 + 80 + 80 = ~240 | ~3 600 | ~900 |
| E2 — `Opposition: <LABEL> (...)` | ~45 (drop line when NONE → ~30 avg) | ~450 | ~110 |
| E3 — `Cats: <cat> NB[/MS] | ...` | ~70 (Option B, non-zero only) | ~1 050 | ~265 |
| **E2 + E3 combined** | ~115 | **~1 500** | **~375** |

Prompt budget target is ~10 000 tokens for Call A (per the existing trim
heuristics at `strategist.py:400-411`). Adding ~375 tokens for the combined
enrichment is **<4 %** of the budget — well within tolerance. The trim path
at `_TRIM_OPTIONAL_MARKERS` (`strategist.py:404-411`) covers `## SENTIMENT`
and `## X-RAY STRUCTURAL SETUPS` for evictability; the per-coin block lives
outside that list, so the enrichment will only be trimmed if the briefing
itself spills the budget — at which point the existing trimmer will deal
with it.

## Verdict

- **E1 — verified shipped** at `strategist.py:1766-1779`. No change required.
- **E2 — trivial.** ~12 lines in the existing renderer using `buy_weighted` /
  `sell_weighted` / `votes_dict` already in the cache. ~30-45 chars/coin
  (~110 tokens for 15 coins). Recommend Option A (STRONG/MODERATE/WEAK
  label with wsum tail). Sub-day work.
- **E3 — small.** ~25-35 lines plus a strategy-name → category map (10 lines
  from first-letter convention, or one-line registry pull-through). ~70
  chars/coin (~265 tokens for 15 coins). Recommend Option B (non-zero
  categories only). ~1-day work.
- **Combined E2 + E3** ≈ 375 tokens / 15 coins, well under the 10 K prompt
  headroom.
- **Format approval needed** from operator — three options each above,
  recommendations called out.

Both share the same cache (`_strategy_votes`), accessor (`get_strategy_votes`),
and renderer (`_format_briefing_extras`). No new pipeline, no new DB query,
no new worker. Data has been computed and cached every cycle since the
briefing-rewrite Phase 2 — the brain just isn't reading the *opposition* or
*category structure* views of it.
