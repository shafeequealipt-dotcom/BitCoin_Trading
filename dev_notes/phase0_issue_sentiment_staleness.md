# Phase 0 — Issue Investigation: Sentiment Freshness (Issue #5)

**Issue:** SIG_BATCH_STATS shows `conf_mean=0.291 conf_std=0.036` — extremely tight band around the neutral default. Reported as "sentiment data 4.68 hours stale."

## Section A — The mechanism

### A.1 NewsWorker tick

**File:** `src/workers/news_worker.py:33-55`

- Interval: `news_interval` from `config.toml [workers]` (default 300s = 5 min)
- Tick calls `self.news_service.fetch_latest_news()` and logs `NEWS_FETCH | total={N}` at INFO
- Calendar update every 30 ticks (~2.5 hours)
- No try/except in tick body — exceptions bubble to BaseWorker's exponential-backoff handler

### A.2 NewsService — the upstream filter

**File:** `src/intelligence/news/news_service.py:43-110`

```python
@timed
async def fetch_latest_news(self, category="crypto", max_articles=None) -> list[NewsArticle]:
    raw_articles = await self._finnhub.get_general_news(category=category)  # line 61
    cutoff = now_utc() - timedelta(hours=24)                                 # line 62 — HARD 24H CUTOFF
    new_articles: list[NewsArticle] = []

    for raw in raw_articles[:max_articles]:
        ts = raw.get("datetime", 0)
        published = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else now_utc()
        if published < cutoff:
            continue                                                         # line 70 — DROP
        headline = raw.get("headline", "")
        if not headline:
            continue                                                         # line 74 — DROP
        if await self._news_repo.headline_exists(headline):
            continue                                                         # line 78 — DROP (dedup)
        ...
        await self._news_repo.save_article(article)
        new_articles.append(article)

    log.info("Fetched {total} articles, {new} new after dedup", total=..., new=...)
```

Three filter gates: 24h cutoff, missing-headline, duplicate-headline.

### A.3 Finnhub client

**File:** `src/intelligence/news/finnhub_client.py:33-69`

- `@retry(max_attempts=3, delay=2.0)` and `@rate_limit(calls_per_second=1.0)` on `get_general_news`
- Raises `FinnhubError` on API failure
- Returns empty list when API has no articles for the category

### A.4 Sentiment aggregator — zero-coverage cache

**File:** `src/intelligence/sentiment/aggregator.py`

- `_ZERO_COVERAGE_TTL_SECONDS = 30 * 60` at line 31 (hardcoded)
- When a symbol has no news AND no reddit data, caches "unknown" verdict for 30 min (lines 66-75) — Phase 6 optimization
- Default `overall_score=0.0` for empty-input symbols (line 129)
- Maps to `SentimentLevel.NEUTRAL` per scorer.py:141-142

### A.5 Empirical state (queried 2026-04-26 at planning time)

```
news_articles:
  MAX(published_at) = 2026-04-26T12:00:00+00:00
  COUNT(*)          = 1207
aggregated_sentiment:
  MAX(created_at)   = 2026-04-26T13:36:32 (continuous writes)
  COUNT(*)          = 289187
```

**Diagnosis:**
- Aggregator IS running. 289k rows, latest minutes ago.
- News pipeline IS persisting articles, but the latest article is ~1.5h old at query time.
- Spec's "4.68h stale" was at the original observation time; current is ~1.5h. Variable, but always non-recent.

The **upstream signal** — Finnhub returning fresh articles — is the bottleneck. Either:
1. Finnhub has poor crypto coverage at the relevant time-of-day
2. Most articles returned are >24h old (gated out by `news_service.py:69`)
3. Most fresh articles already exist in the DB (gated out by `headline_exists` at line 77)

## Section B — The dependencies

| Consumer | File | What it reads |
|---|---|---|
| SignalWorker | `src/workers/signal_worker.py` | `aggregated_sentiment` per coin → confidence weighting |
| Strategist (brain prompt) | `src/brain/...` | `get_news_for_symbol(symbol, hours=24)` summary |
| Operator dashboards | logs | `SIG_BATCH_STATS conf_mean/std` |

The brain prompt is the most visible consumer — when sentiment is sparse, the prompt loses signal-rich context.

## Section C — The constraints

- **Cannot drop dedup.** Re-ingesting the same headline pollutes scores.
- **Cannot remove the 24h cutoff** without re-evaluating `aggregated_sentiment` semantics — the 24h window is the implicit "freshness" boundary.
- **Cannot exceed Finnhub rate limits.** Currently 1.0 calls/sec.
- **Free-tier Finnhub** likely has limited crypto coverage; this is a data-source constraint, not a code bug.

## Section D — The fix candidates

### D.1 Diagnostic logging (Phase 7.1, 7.2)

- Add `NEWS_TICK_SUMMARY | fetched={f} new={n} duplicates={d} skipped_old={s} el={ms}ms` to news_worker.
- Add `FINNHUB_COVERAGE | requested=all returned={n} fresh_under_24h={k}` inside `fetch_latest_news` to surface the cutoff drop rate.

### D.2 Cache TTL configurability (Phase 7.3)

Move `_ZERO_COVERAGE_TTL_SECONDS` to `config.toml [intelligence.sentiment] zero_coverage_ttl_seconds`. Default unchanged (1800s).

### D.3 Conditional fixes (Phase 7.4 — diagnosis-driven)

- **If NewsWorker isn't ticking** → debug BaseWorker exception handler.
- **If 24h cutoff filters >95%** → relax to 48h with documented rationale.
- **If Finnhub returns sparse crypto** → document as a known data limitation; consider a second source as a follow-up ticket. Do not over-fit a code fix to a data-supply problem.

### D.4 What NOT to do

- Don't shorten the zero-coverage cache TTL further — it exists to limit the DB read storm for unknown coins.
- Don't disable dedup — Finnhub repeats headlines across categories.
- Don't lower the rate limit — Finnhub free tier will throttle.

## Verification

After Phase 7:
- `news_articles MAX(published_at)` within last hour for the most-tracked symbols.
- `aggregated_sentiment` continues writing.
- `SIG_BATCH_STATS conf_std` rises above 0.04 from 0.036 (signal of meaningful variance, not just neutral default).

## Verified citations

| Claim | File:Line |
|---|---|
| NewsWorker interval = 300s | `src/workers/news_worker.py:33` (via config) |
| `fetch_latest_news` 24h cutoff | `src/intelligence/news/news_service.py:62, 69` |
| Dedup via `headline_exists` | `src/intelligence/news/news_service.py:77` |
| Finnhub rate limit 1.0/sec | `src/intelligence/news/finnhub_client.py:34` |
| Finnhub `@retry(max_attempts=3, delay=2.0)` | `src/intelligence/news/finnhub_client.py:33` |
| `_ZERO_COVERAGE_TTL_SECONDS = 30*60` | `src/intelligence/sentiment/aggregator.py:31` |
| Default neutral score 0.0 | `src/intelligence/sentiment/aggregator.py:129` |
| Save aggregated row | `src/intelligence/sentiment/aggregator.py:201` |
