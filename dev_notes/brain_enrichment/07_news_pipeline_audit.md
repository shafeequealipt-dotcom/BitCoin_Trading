# News Pipeline Audit — Gap Mapping for Deferred E8

Scope: trace the Finnhub news ingestion pipeline end-to-end and document the gap
between persisted articles and the brain prompt. News injection (E8) was scoped
out of the current enrichment batch by the operator — this report is
analysis-only.

## Files Involved

| Path | Lines | Role |
|---|---|---|
| `src/workers/news_worker.py` | 75 | Layer 1A worker — periodic Finnhub poll loop |
| `src/intelligence/news/news_service.py` | 240 | Fetch / filter / dedup / score / persist orchestrator |
| `src/intelligence/news/finnhub_client.py` | 124 | Async wrapper around the sync `finnhub-python` SDK |
| `src/database/repositories/news_repo.py` | 259 | `news_articles` table I/O + symbol lookup with base-asset fallback |
| `src/intelligence/sentiment/scorer.py` | 214 | Keyword-based `SentimentScorer.score_text` (used by `NewsService`) |
| `src/database/migrations.py:130-150` | 21 | `news_articles` schema + indexes |
| `src/brain/strategist.py` | 3849 | Brain prompt assembler — **zero** news references |

## Ingestion Pipeline (Worker → Service → Client → DB)

The worker is a thin Layer 1A periodic loop (`src/workers/news_worker.py:17-44`).
`worker_tier = WorkerTier.LAYER1A` (`news_worker.py:28`) and the cadence is
`settings.workers.news_interval` (`news_worker.py:40`), which observation
confirms is 300 s — five-minute ticks.

Each tick (`news_worker.py:47-75`) calls
`news_service.fetch_latest_news()` (default `category="crypto"`,
`max_articles = settings.finnhub.max_articles_per_fetch`,
`news_service.py:67-68`). Every 30 ticks (~2.5 h) the worker also refreshes the
economic calendar (`news_worker.py:57-65`).

The service (`news_service.py:47-132`) does the actual work:

1. Pull raw articles from Finnhub
   (`news_service.py:70` → `FinnhubClient.get_general_news`).
2. Drop articles older than 24 h (`news_service.py:71`, `news_service.py:82-84`).
3. Drop empty headlines (`news_service.py:86-89`).
4. Dedup against the DB by exact headline match
   (`news_service.py:92-94` → `news_repo.headline_exists`).
5. Score sentiment with the keyword-based scorer (`news_service.py:100`).
6. Extract crypto symbols from headline+summary
   (`news_service.py:102-103` → `extract_symbols(text, extraction_map)` at
   `news_service.py:205-240`, using the runtime `coin_aliases` map).
7. Persist via `news_repo.save_article` with `INSERT OR IGNORE`
   (`news_service.py:118`, `news_repo.py:78-102`).

The Finnhub client (`finnhub_client.py:19-69`) wraps the synchronous
`finnhub-python` SDK with `asyncio.to_thread`, applies a 1 call/s rate limit
(`@rate_limit(calls_per_second=1.0)`, `finnhub_client.py:34`), and retries 3×
with exponential backoff on `FinnhubError` (`@retry(max_attempts=3, delay=2.0)`,
`finnhub_client.py:33`). The same wrapper covers `general_news`,
`crypto_news` (an alias), and `economic_calendar` (`finnhub_client.py:36-124`).

Funnel observability lives in two structured log lines emitted per tick:

- `FINNHUB_COVERAGE` (`news_service.py:121-126`) — per-stage counters
  (`returned`, `considered`, `new`, `skipped_old`, `skipped_no_headline`,
  `skipped_dedup`).
- `NEWS_TICK_SUMMARY` (`news_worker.py:68-72`) — wall-clock latency plus
  `new` count + `calendar_updated` flag.

## DB Schema + 24 h Snapshot

Schema (`migrations.py:130-150`, verified live via
`sqlite3 -readonly data/trading.db ".schema news_articles"`):

```sql
CREATE TABLE news_articles (
    id              TEXT PRIMARY KEY,
    headline        TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    sentiment_score REAL NOT NULL DEFAULT 0,
    symbols         TEXT NOT NULL DEFAULT '[]',
    category        TEXT NOT NULL DEFAULT '',
    published_at    TEXT NOT NULL DEFAULT (datetime('now')),
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_news_published ON news_articles(published_at DESC);
CREATE INDEX idx_news_symbols   ON news_articles(symbols);
```

`symbols` is a JSON array of base-asset tickers (BTC, ETH, BONK), not
derivative pairs — see the dedicated comment block at `news_repo.py:16-25`
that explains why `get_by_symbol` retries with a normalised base asset on a
miss.

Live counts (executed against `data/trading.db` on 2026-05-16, query window =
last 24 h):

- Total rows where `published_at >= now - 24h`: **57**.
- Rows with `sentiment_score != 0`: **13** (≈ 23 % of the window).
- avg / min / max sentiment: **0.027 / -0.30 / 0.50**.

Top-5 most recent headlines (sampled via
`SELECT id, substr(headline,1,80), source, published_at, ROUND(sentiment_score,2)
FROM news_articles ORDER BY published_at DESC LIMIT 5`):

```
7950526 | Bhutan 'doesn't recall' selling any bitcoin, disputing widely-tracked ... | CoinDesk      | 2026-05-16T02:30:00Z | 0.00
7950423 | US CLARITY Act brings 'major spike of euphoria' to Bitcoin: Santiment      | Cointelegraph | 2026-05-16T02:13:23Z | 0.50
7949617 | House committee leaders urge Trump to nominate CFTC members, citing ...    | Cointelegraph | 2026-05-15T23:10:35Z | 0.00
7949618 | Bitcoin Depot filing casts doubt on company's future amid lawsuits         | Cointelegraph | 2026-05-15T22:17:38Z | 0.00
7949412 | ICE, CME press US regulators to 'rein in' Hyperliquid energy trading       | Cointelegraph | 2026-05-15T21:42:18Z | 0.00
```

## Sentiment Scoring — Computed but Sparse

Scoring is **computed** (called for every new article at `news_service.py:100`)
but **mostly zero**. The scorer is a keyword bag-of-words classifier
(`src/intelligence/sentiment/scorer.py:48-126`) with six fixed lists totalling
~60 keywords (`scorer.py:54-81`) and weights ±0.15…±0.30 per match
(`scorer.py:83-90`), clamped to [-1, 1].

Why 77 % of the 24 h window scores 0: headlines like "Bhutan doesn't recall
selling any bitcoin" and "House committee leaders urge Trump to nominate CFTC
members" don't contain any of the six keyword lists. The "euphoria" headline
hits `STRONG_GREED` (+0.20) plus implicit greed language → +0.50. The scorer
**works** but is too narrow for regulatory / corporate / on-chain news, which
rarely uses the meme-bullish vocabulary it matches on. Separate from the
prompt gap — even with full scoring, none of the articles reach the brain.

## NEWS_TICK Live Log Sample

`grep "NEWS_TICK\|FINNHUB_COVERAGE" data/logs/workers.log | tail -10` shows
five consecutive ticks on 2026-05-16 between 04:07 and 04:37 UTC:

```
04:07:01 FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=0 skipped_no_headline=0 skipped_dedup=50
04:07:01 NEWS_TICK_SUMMARY | new=0 calendar_updated=N el=1115ms
... (identical pattern through 04:37:08) ...
```

`returned=96`, `considered=50` (`max_articles=50` cap), `skipped_dedup=50` every
tick — every considered article was already persisted. Per-tick elapsed
~1.1 s. `new=0` is the expected steady state; the 57-row 24 h count above
accumulates between rare `new>0` ticks.

## Strategist Reference Check — Confirmed Zero Prompt Path

```
grep -in "news\|finnhub\|article" src/brain/strategist.py
```

returns exclusively:

- A header comment listing what the brain *sees* (`strategist.py:8`,
  "Fear & Greed, funding rates, sentiment"). No mention of articles.
- Two `SENTIMENT` section emitters that surface only the Fear & Greed value
  (`strategist.py:1214-1219` for Call A, `strategist.py:2748-2755` for Call A
  variant, `strategist.py:3162-3163` for Call B).
- A single TIAS-lesson hint line that mentions "F&G confirms bearish
  sentiment" (`strategist.py:3427`).

None of `news_service`, `news_repo`, `NewsRepository`, `NewsService`,
`get_news_for_symbol`, `get_news_summary`, or `news_articles` are imported or
referenced anywhere in `src/brain/strategist.py`. The `## SENTIMENT` section,
in both Call A and Call B, renders **only** the Fear & Greed integer
(`strategist.py:1216-1219`, `strategist.py:2750-2753`, `strategist.py:3163`).

## Proposed Injection Point — for the Deferred E8 Fix

The cleanest insertion point is the existing `## SENTIMENT` section, because
the brain already sees that header and treats it as optional/trimmable
(`strategist.py:404-411` lists `## SENTIMENT` in `_TRIM_OPTIONAL_MARKERS`).
Two formats are viable depending on what the operator wants to spend on
prompt tokens:

**Aggregate (top of `## SENTIMENT`, after F&G line; ~80 chars):**

```
News (24 h): 57 articles avg=+0.03 [bullish=US CLARITY Act spike; bearish=Bitcoin Depot lawsuit]
```

Data already available via
`news_service.get_news_summary(hours=24)` (`news_service.py:160-202`) which
returns `total_articles, avg_sentiment, top_bullish, top_bearish,
most_mentioned_symbols`. Zero extra DB queries — one async call per Call A.

**Per-coin (inside the per-coin block, optional; ~50-90 chars/coin):**

```
News: BTC 3 articles, sent=+0.05 (CLARITY Act euphoria | Bhutan dispute)
```

Data via `news_service.get_news_for_symbol(symbol, hours=24)`
(`news_service.py:134-145`) which already handles the base-asset fallback
(`news_repo.py:121-160`). Cost: one DB query per coin (15 coins × ~50 ms =
~750 ms extra Call A latency, plus ~75-1 350 prompt chars).

## Gap Mapping for Future E8 Fix

| Gap | Evidence | Required work |
|---|---|---|
| Strategist never injects news into prompt | `grep` for `news/finnhub/article` in `src/brain/strategist.py` returns zero matches in prompt-construction code | Add a `_inject_news_block` helper that calls `news_service.get_news_summary` and/or `get_news_for_symbol`, gated by a config flag |
| `## SENTIMENT` section renders F&G only | `strategist.py:1214-1219, 2748-2755, 3162-3163` | Append a `News (24 h):` line after the F&G line; reuse the existing trim-priority bucket |
| Scorer hit-rate is 23 % | live DB query: 13 of 57 articles non-zero in 24 h | Out of scope for E8 — flag as a follow-up. A richer scorer (Claude/DeepSeek classification, or an expanded keyword list keyed to crypto-policy / on-chain terms) is needed before scores become reliable |
| Symbol extraction is alias-based | `news_service.py:205-240`, alias map = `settings.universe.extraction_map` | Already works (BTC↔BTCUSDT, BONK↔1000BONKUSDT via the base-asset normaliser at `news_repo.py:31-65`); no E8 work required |
| Calendar events untouched | `news_worker.py:57-65` saves to `economic_calendar` table; nothing in `strategist.py` references it | Separate enrichment; not part of E8 |

## Verdict

- **Ingestion: works.** Five-minute tick cadence is healthy; 57 articles in
  the live 24 h window; rate limit + retry + dedup all function; `news_repo`
  has thoughtful base-asset fallback for derivative symbols.
- **Sentiment scoring: works but sparse.** Only 23 % of articles in the last
  24 h have a non-zero score because the keyword lists don't cover the
  vocabulary of regulatory / corporate crypto news. Scores are real where
  they exist (e.g. +0.50 on "euphoria" headlines) but the floor is 0.0 for
  most stories.
- **Prompt path: absent.** Zero news references in `src/brain/strategist.py`.
  The data is ingested, persisted, indexed, and scored — and then never
  read by the brain. Closing the gap is a 30-50 line change inside
  `_build_trade_prompt` / `_build_position_prompt`, gated by a config flag,
  reusing existing `NewsService` methods.

The E8 fix is well-scoped when the operator chooses to land it: one new
section in the brain prompt, one or two `await news_service.*` calls,
optional per-coin enrichment, plus a config-flag deprecation path. The
scorer hit-rate problem is orthogonal and should be tracked separately —
landing E8 without it still gives the brain headlines + symbols + 24 h
volume signal, which is more than zero.
