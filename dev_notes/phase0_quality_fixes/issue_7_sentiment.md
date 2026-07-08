# Phase 0 — Quality Issue 7: Sentiment Categorical Reason Refinement

## A — Current observed behaviour

`SENT_UNKNOWN_CACHE_HIT` is the dominant sentiment-related log tag in the 09:58 process — observed 6,190 times in the recent log tail. Pre-09:58 process logged 192 `SENT_UNKNOWN | rsn=no_news_no_reddit` per 30-min window.

**Why this is by-design (Phase 15 of the existing work):**

`src/intelligence/sentiment/aggregator.py:163-165`:
```python
has_own_data = len(news_scores) > 0 or len(reddit_scores) > 0
if not has_own_data:
    overall = 0.0    # Phase 15 — UNKNOWN, not NEUTRAL
```

Combined with:
- Reddit intentionally disabled (no `client_id` in `config.toml`) → logged `SENTIMENT_DEGRADED_MODE | reason=no_reddit source=fear_greed_only` once at boot
- Finnhub free tier covers only majors → 29/50 altcoins have zero news

So 97.9% of coins have no news AND no reddit → enter the zero-coverage branch.

The aggregator at line 192-198 currently emits a single `SENT_UNKNOWN` tag. **This is the diagnostic gap.** Operators see a flood of `SENT_UNKNOWN` and cannot tell which of these is happening:
- True zero coverage (genuinely no data anywhere)
- Reddit disabled by config (intentional)
- Some news but below quorum threshold
- F&G-only mode (degraded but not absent)

## B — Expected behaviour

Replace the single tag with categorical reasons:

| Tag | Meaning | Operator action |
|---|---|---|
| `SENT_NO_DATA | sym=<s> matched=0 lookback_h=<h>` | True zero — no news for this symbol | Investigate Finnhub coverage / symbol mapping |
| `SENT_DEGRADED_MODE | sym=<s> reason=reddit_disabled fg=<x> change_24h=<x>` | Reddit disabled by config (the dominant case today) | Operational state; consider re-enabling Reddit |
| `SENT_INSUFFICIENT | sym=<s> matched=<n> min_required=<m>` | Some news but below threshold | Tune `min_signals_per_hour` if too strict |
| `SENT_DEGRADED | sym=<s> reason=fg_only news_unavailable=true` | F&G + momentum only | News pipeline is failing for THIS symbol specifically |

**Behaviour unchanged.** Aggregator still returns `overall_score=0.0, level=UNKNOWN` for all four cases. Only the log tag differs.

## C — Root cause

This is **NOT a behavioural fix** — Phase 15 design (the zero-coverage rule) is intentional and correct. The fix is purely diagnostic clarity.

Per user Q4 = "Categorical reason refinement only — no fallback":
- DO NOT change `overall = 0.0` semantics
- DO NOT add F&G + momentum fallback aggregation
- Just refine the log tag to tell operators WHICH zero-coverage situation triggered

## D — Verification approach (post-fix)

| Metric | Measure | Target |
|---|---|---|
| Each tag fires for the right reason | inject 4 test fixtures (no-data, reddit-disabled, low-news, F&G-only); verify each | each test produces the expected tag |
| Volume distribution | grep workers.log over 1 hour | most events `SENT_DEGRADED_MODE` (matches today's Reddit-disabled state); some `SENT_NO_DATA` for altcoins |
| No behaviour change | `aggregated_sentiment` table rows shape unchanged | same columns, same values for the same inputs |
| Stage 2 prompt unchanged | grep `PROMPT_BUILD_DONE` for sentiment fields | unchanged structure |

## E — Rollback path

Phase 7 is purely log-tag refinement. New tag constants in `log_tags.py`. The existing `SENT_UNKNOWN` tag can be kept as an alias if any downstream parser depends on it. Rollback: `git revert <phase7-commits>` reverts to the single `SENT_UNKNOWN` tag.

## Files end-to-end mapped

| File | Lines | Role |
|---|---|---|
| `src/intelligence/sentiment/aggregator.py` | 88-261 (aggregate_for_symbol), **154-200 (no-data branch — fix target)** | Where the 4 categorical reasons must differentiate |
| `src/core/log_tags.py` | (existing tag list) | **Fix target — add 4 new constants** |
| `tests/test_sentiment_aggregator_tags.py` | NEW | One test per branch |

## Phase 7 fix outline (preview)

2 atomic commits:
1. Differentiate the no-data branch in `aggregator.py:192-198` to emit one of `SENT_NO_DATA / SENT_DEGRADED_MODE / SENT_INSUFFICIENT / SENT_DEGRADED` based on actual conditions. Add new tag constants in `log_tags.py`.
2. Unit tests verifying each branch emits the right tag.
