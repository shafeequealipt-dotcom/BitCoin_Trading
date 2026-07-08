# Phase 7 — Sentiment Categorical Reason Refinement Report

**Date:** 2026-04-27
**Commits:** 2 atomic — source change, unit tests.

## Summary

Behaviour-preserving log tag refinement per user Q4 = "Categorical reason refinement only, no fallback".

The no-data branch in `aggregator.py:163-200` formerly emitted a single `SENT_UNKNOWN` tag for both "Reddit disabled by config" (98% of cases) and "Reddit configured but empty" (genuine data gap). Operators saw 6,190 SENT_UNKNOWN events and could not differentiate.

Now:
- **`SENT_DEGRADED_MODE | reason=reddit_disabled`** — when Reddit is intentionally disabled (no `client_id` in config). The dominant case today.
- **`SENT_NO_DATA | matched_articles=0 matched_reddit=0`** — when Reddit IS configured but no rows for this symbol. Genuine data gap; operator should investigate Finnhub coverage / symbol mapping.
- **`SENT_UNKNOWN`** — back-compat alias retained, fires only on the genuine-data-gap branch (matching the original signal-of-interest semantics).
- **`SENT_NEUTRAL`** — preserved across both branches (downstream analysis scripts depend on it).

## Behaviour unchanged

- `overall_score = 0.0`
- `level = SentimentLevel.UNKNOWN`
- `aggregated_sentiment` table rows shape unchanged
- Stage 2 prompt sentiment block unchanged

## Files

| File | Change |
|---|---|
| `src/intelligence/sentiment/aggregator.py` | No-data branch: differentiated emit on `self._reddit_intentionally_disabled` |
| `tests/test_sentiment_aggregator_tags.py` (NEW) | 3 cases — disabled-mode, no-data, SENT_NEUTRAL invariant |

## Verification — automated

```
pytest tests/test_sentiment_aggregator_tags.py — 3 passed
Full regression — 126 passed
```

## Verification — operator-driven (post-deploy, 1 hour)

| # | Trial | Pass criterion |
|---|---|---|
| 7.1 | Tag distribution | grep one hour: most events `SENT_DEGRADED_MODE` (Reddit-disabled state); few `SENT_NO_DATA` |
| 7.2 | Behaviour unchanged | `aggregated_sentiment` rows have same shape; Stage 2 prompt unchanged |
| 7.3 | Back-compat preserved | downstream parsers grepping `SENT_UNKNOWN` still find the genuine-data-gap cases |
| 7.4 | Operator can identify genuine gaps | grep `SENT_NO_DATA` over 1 hour shows the small subset of coins where the news pipeline genuinely failed (investigation target) |
