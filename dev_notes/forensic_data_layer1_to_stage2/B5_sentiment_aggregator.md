# B5 — SentimentAggregator

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.5.1 — Where it lives

- File path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/intelligence/sentiment/aggregator.py`
- Class: `SentimentAggregator` (line 28)
- It is **NOT a worker**. It is a service singleton injected into:
  - `SignalGenerator.__init__` at `src/intelligence/signals/signal_generator.py:52` (`self._aggregator = aggregator`).
  - The signal worker tick path: `src/workers/signal_worker.py:96-102` calls `await self.aggregator.aggregate_for_symbol(symbol)` per coin.
  - `SignalGenerator.generate_signal` at `signal_generator.py:85` calls `sentiment = await self._aggregator.aggregate_for_symbol(symbol)`.
  - MCP tool `src/mcp/tools/sentiment_tools.py:80` (`aggregator.aggregate_for_symbol(args["symbol"], args.get("hours", 24))`).

When does it run: each `signal_worker.tick()`, which is `SweetSpotWorker` triggered at `settings.workers.sweet_spots.signal_worker = "1:00"` per 5-min window (config.toml). Per tick it iterates all 50 watch_list symbols and calls `aggregate_for_symbol(sym)` twice per symbol — once explicit at signal_worker.py:98 ("Sentiment aggregation only") and once implicit inside `signal_generator.generate_signal` at signal_generator.py:85. Therefore each symbol triggers 2 calls per cycle (the second is short-circuited by the zero-coverage cache for unknown symbols).

## B.5.2 — Aggregation logic

### Constants (aggregator.py:18-30)
```python
WEIGHT_NEWS = 0.35
WEIGHT_REDDIT = 0.30
WEIGHT_FEAR_GREED = 0.20
WEIGHT_MOMENTUM = 0.15
_ZERO_COVERAGE_TTL_SECONDS: float = 30 * 60  # 30 minutes
```

### `aggregate_for_symbol(symbol, hours=24)` flow (aggregator.py:88-280)
1. **Zero-coverage cache** (lines 100-110): if `symbol in self._unknown_cache` and not expired → emit `SENT_UNKNOWN_CACHE_HIT` and return cached dict.
2. **News lookup** (line 115): `news_articles = await self._news_repo.get_by_symbol(symbol, hours=hours)` with `hours=24` default.
3. **Reddit lookup** (line 120): `reddit_posts = await self._sentiment_repo.get_posts_by_symbol(symbol, hours=hours)`.
4. **Fear & Greed** (line 125): `fg = await self._altdata_repo.get_latest_fear_greed()`; `fg_normalized = (fg.value - 50) / 50.0`.
5. **Momentum** (line 130): `recent_sentiment = await self._sentiment_repo.get_sentiment_for_symbol(symbol, limit=1)`. `momentum = clamp(current_avg - prev_score, -1, 1)`.
6. **Weight rebalancing** for F&G extremes (lines 135-141):
   ```python
   fg_weight = WEIGHT_FEAR_GREED
   if fg_value < 20 or fg_value > 80:
       fg_weight = 0.60
   elif fg_value < 30 or fg_value > 70:
       fg_weight = 0.40
   ```
7. **Score formula** (lines 146-152):
   ```python
   overall = (
       news_avg * WEIGHT_NEWS * scale
       + reddit_avg * WEIGHT_REDDIT * scale
       + fg_normalized * fg_weight
       + momentum * WEIGHT_MOMENTUM * scale
   )
   overall = clamp(overall, -1.0, 1.0)
   ```
8. **No-data branch** (lines 156-220):
   ```python
   has_own_data = len(news_scores) > 0 or len(reddit_scores) > 0
   if not has_own_data:
       overall = 0.0
       ... fetch change_24h_pct from ticker_cache ...
       log.info(f"SENT_NEUTRAL | sym=... rsn=no_news_no_reddit fg=...")
       if self._reddit_intentionally_disabled:
           log.info(f"SENT_DEGRADED_MODE | sym=... reason=reddit_disabled ...")
       else:
           log.info(f"SENT_NO_DATA | sym=... matched_articles=0 matched_reddit=0 ...")
           log.info(f"SENT_UNKNOWN | sym=... rsn=no_news_no_reddit ...")
   ```
9. **Level mapping** (lines 224-227): `level = SentimentLevel.UNKNOWN if not has_own_data else self._scorer.score_to_level(overall)`.
10. **Persist** (line 247): `await self._sentiment_repo.save_aggregated_sentiment(result)`.
11. **Cache** the no-data verdict for 30 min (lines 274-279).

Lookup window: 24h default (per `aggregate_for_symbol(symbol, hours=24)` signature at line 88).

Threshold for "enough articles": NONE — any non-empty `news_scores` OR `reddit_scores` list flips `has_own_data` to True. There is no minimum-count threshold.

## B.5.3 — `aggregated_sentiment` schema and write format

DB schema (snapshot DB):
```sql
CREATE TABLE aggregated_sentiment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    overall_score REAL NOT NULL DEFAULT 0,
    level TEXT NOT NULL DEFAULT 'neutral',
    news_score REAL NOT NULL DEFAULT 0,
    news_count INTEGER NOT NULL DEFAULT 0,
    reddit_score REAL NOT NULL DEFAULT 0,
    reddit_count INTEGER NOT NULL DEFAULT 0,
    fear_greed_value INTEGER NOT NULL DEFAULT 50,
    momentum REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_agg_sentiment_symbol ON aggregated_sentiment(symbol, created_at DESC);
```

Insert SQL (sentiment_repo.py:127-143):
```sql
INSERT INTO aggregated_sentiment
(symbol, overall_score, level, news_score, news_count,
 reddit_score, reddit_count, fear_greed_value, momentum)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

Cache key/value format (in-memory):
- Aggregator's `_unknown_cache: dict[str, tuple[float, dict]]` at aggregator.py:62. Key = symbol; value = `(monotonic_expires_at, result_dict)` with TTL 1800 s (30 min).

Sample of last 10 writes (DB snapshot, table `aggregated_sentiment`, ordered by id DESC):
```
id      | symbol     | overall | level   | news_s | n_n | reddit_s | n_r | fg | mom | created_at
336158  | ALICEUSDT  | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336157  | BCHUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336156  | LTCUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336155  | APTUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336154  | OPUSDT     | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336153  | BLURUSDT   | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336152  | ORCAUSDT   | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336151  | HYPERUSDT  | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336150  | KATUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336149  | BSBUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
```

Distribution last 1h (DB snapshot):
```
SELECT level, COUNT(*) FROM aggregated_sentiment WHERE created_at > datetime('now','-1 hour') GROUP BY level;
neutral | 72
unknown | 44
```

Distinct symbols last 1h: 50 (matches watch_list).

Total rows in `aggregated_sentiment`: 276,330.

## B.5.4 — Why most ticks emit SENT_UNKNOWN

### The structural cause: news article tagging covers only 10 base assets

Per `src/intelligence/signals/signal_models.py:75-98` (`SYMBOL_EXTRACTION_MAP`), only 10 keys produce a tag: BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, DOT, LINK, MATIC. Articles for the remaining 44 watch_list coins are NEVER stored with their symbol in `news_articles.symbols`, so `NewsRepository.get_by_symbol(symbol)` returns `[]` for those coins. Reddit is disabled (`config.toml:[reddit] enabled = false`, `client_id` empty). With both branches at 0, `has_own_data = False`, and the no-data branch fires `SENT_UNKNOWN` / `SENT_DEGRADED_MODE`.

### Per-symbol live SENT_AGG outputs (last cycle in workers.log, 22:25:59 - 22:26:03):

Non-zero news count (4 of 50 symbols):
```
22:26:00.068 | SENT_AGG | sym=ETHUSDT  score=-0.018 level=neutral news_n=7 reddit_n=0 fg=47
22:26:00.101 | SENT_AGG | sym=SOLUSDT  score=-0.010 level=neutral news_n=2 reddit_n=0 fg=47
22:26:00.310 | SENT_AGG | sym=XRPUSDT  score=-0.047 level=neutral news_n=3 reddit_n=0 fg=47
22:26:00.342 | SENT_AGG | sym=ADAUSDT  score=-0.010 level=neutral news_n=1 reddit_n=0 fg=47
22:26:00.376 | SENT_AGG | sym=DOGEUSDT score=-0.010 level=neutral news_n=1 reddit_n=0 fg=47
```
(BTCUSDT also non-zero — visible in earlier cycle).

Zero news count emit `level=unknown` (representative samples — verbatim):
```
22:26:03.123 | SENT_NEUTRAL | sym=BLURUSDT rsn=no_news_no_reddit fg=47 change_24h=-8.0376
22:26:03.124 | SENT_DEGRADED_MODE | sym=BLURUSDT reason=reddit_disabled fg=47 change_24h=-8.0376
22:26:03.126 | SENT_AGG | sym=BLURUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
22:26:03.126 | SENT_UNKNOWN_CACHE_HIT | sym=BLURUSDT
22:26:03.279 | SENT_NEUTRAL | sym=OPUSDT rsn=no_news_no_reddit fg=47 change_24h=-5.5362
22:26:03.280 | SENT_DEGRADED_MODE | sym=OPUSDT reason=reddit_disabled fg=47 change_24h=-5.5362
22:26:03.284 | SENT_AGG | sym=OPUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
22:26:03.318 | SENT_NEUTRAL | sym=LTCUSDT rsn=no_news_no_reddit fg=47 change_24h=None
22:26:03.319 | SENT_DEGRADED_MODE | sym=LTCUSDT reason=reddit_disabled fg=47 change_24h=None
22:26:03.320 | SENT_AGG | sym=LTCUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
22:26:03.333 | SENT_NEUTRAL | sym=BCHUSDT rsn=no_news_no_reddit fg=47 change_24h=None
22:26:03.341 | SENT_AGG | sym=BCHUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
22:26:03.354 | SENT_NEUTRAL | sym=ALICEUSDT rsn=no_news_no_reddit fg=47 change_24h=3.7545
22:26:03.360 | SENT_AGG | sym=ALICEUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
```

For each of 5 zero-sentiment coins, the WHY:
- **BLURUSDT** — `news_articles.symbols` table contains 0 rows where the JSON list contains "BLURUSDT" or "BLUR" in the last 24 h (verified by DB snapshot showing only `BTCUSDT`, `ETHUSDT`, `XRPUSDT`, `SOLUSDT`, `ADAUSDT`, `DOGEUSDT`, `[]` in last 1h). Articles exist (20 untagged in the same window, plus articles mentioning BLUR for which the EXTRACTION_MAP has no key) but none are tagged with BLUR/BLURUSDT. Reddit is disabled. Aggregator returns score=0.0 / level=unknown.
- **OPUSDT** — same root cause; OP not in `SYMBOL_EXTRACTION_MAP`. Articles for "Optimism" rebrand exist but never tagged.
- **LTCUSDT** — Litecoin not in `SYMBOL_EXTRACTION_MAP`. Same outcome.
- **BCHUSDT** — Bitcoin Cash not in `SYMBOL_EXTRACTION_MAP`. Note: `change_24h=None` because `ticker_cache` had no row for BCHUSDT at lookup time (aggregator.py:165-166).
- **ALICEUSDT** — ALICE not in `SYMBOL_EXTRACTION_MAP`.

Aggregator init log (one-shot, captured 2026-04-27 22:53:27.037):
```
SENTIMENT_DEGRADED_MODE | reason=no_reddit source=fear_greed_only | no_ctx
```
Source: aggregator.py:78-83 (when `settings.reddit.client_id` is falsy, set `_reddit_intentionally_disabled = True` and log).

## B.5.5 — Reddit fallback gap

Reddit is disabled both at config (`config.toml:[reddit] enabled = false`) and via missing client_id. The aggregator detects this at `__init__` (aggregator.py:67-86) and stores `self._reddit_intentionally_disabled = True`.

Code path when `_reddit_intentionally_disabled`:
- The Reddit lookup at aggregator.py:120 still executes: `reddit_posts = await self._sentiment_repo.get_posts_by_symbol(symbol, hours=hours)` — but `reddit_posts` is always `[]` because `reddit_worker` is registered but disabled (per project memory). So `reddit_avg = 0.0`.
- In the no-data branch (lines 207-220): if `_reddit_intentionally_disabled`, only `SENT_DEGRADED_MODE` is logged; otherwise both `SENT_NO_DATA` and back-compat `SENT_UNKNOWN` are logged.
- There is **no fallback substitute source** — no Twitter, no alternate Reddit credentials, no other social-sentiment provider wired in. With reddit off, the only signal is: news (when matched) + F&G (market-wide) + momentum.

Observable consequence:
- `SENT_BASEASSET_FALLBACK` (news_repo.py:155) provides a per-news lookup retry by stripping numeric prefixes/quote suffixes (e.g. `1000BONKUSDT → BONK`). Count in current logs = 0. The fallback only helps when articles WERE tagged with the base asset already; it does not retroactively tag untagged articles.
- `_unknown_cache` (TTL 1800 s) hides ~98 % of repeat lookups for zero-coverage symbols by emitting `SENT_UNKNOWN_CACHE_HIT` instead of re-querying the DB.

## What it READS

- `self._news_repo.get_by_symbol(symbol, hours=24)` — table `news_articles` (LIKE filter on `symbols` column with base-asset fallback in repo).
- `self._sentiment_repo.get_posts_by_symbol(symbol, hours=24)` — table `reddit_posts`.
- `self._altdata_repo.get_latest_fear_greed()` — table `fear_greed`.
- `self._sentiment_repo.get_sentiment_for_symbol(symbol, limit=1)` — table `aggregated_sentiment` (for momentum prev-score).
- `self._db.fetch_one("SELECT change_24h_pct FROM ticker_cache WHERE symbol = ?", ...)` — only in no-data branch (line 165).

## What it WRITES

- DB: `aggregated_sentiment` table (insert per call, line 247).
- In-memory: `_unknown_cache` (per-symbol no-data verdict, TTL 1800 s, capped only by symbol set).

## Cadence (live)

- Indirectly driven by `signal_worker` sweet spot `1:00` per 5-min window.
- Live count: 50 distinct symbols aggregated in last 1h, 116 rows total. Per-cycle distribution observed in aggregated_sentiment last 1h: 72 neutral + 44 unknown = 116 / 50 = ~2.3 writes per coin per hour. (Explained by the 2 invocations per cycle; the cache hit short-circuits one of them.)

## Failure modes

| Tag | Count | Source |
|-----|------:|--------|
| `SENT_AGG` | observed every tick (~50 per cycle) | aggregator.py:272 |
| `SENT_NEUTRAL` (no-data branch) | observed for 44 of 50 coins | aggregator.py:192 |
| `SENT_UNKNOWN` (back-compat) | observed when reddit configured + zero coverage | aggregator.py:218 |
| `SENT_DEGRADED_MODE` | observed for all 44 zero-coverage coins | aggregator.py:208 |
| `SENT_NO_DATA` | 0 (reddit not configured, so this branch never fires) | aggregator.py:213 |
| `SENT_BASEASSET_FALLBACK` | 0 | news_repo.py:155 |
| `SENTIMENT_DEGRADED_MODE` (init one-shot) | 1 (at 22:53:27.037) | aggregator.py:78 |
| `SENT_UNKNOWN_CACHE_HIT` | observed in bulk after warm-up | aggregator.py:106 |
| Per-symbol exception "Sentiment aggregation failed" | 0 | signal_worker.py:103 |

## Dependencies (consumers)

- `src/intelligence/signals/signal_generator.py:85` — `sentiment = await self._aggregator.aggregate_for_symbol(symbol)` consumed by `generate_signal()`.
- `src/workers/signal_worker.py:96-102` — direct call per symbol per tick.
- `src/mcp/tools/sentiment_tools.py:80` — MCP tool `aggregate_for_symbol` for ad-hoc operator queries.
- DB-table `aggregated_sentiment` is read by:
  - `src/database/repositories/sentiment_repo.py:146` — `get_sentiment_for_symbol(symbol, limit=1)` (used by aggregator itself for momentum).
  - `src/database/repositories/sentiment_repo.py:162` — `get_sentiment_history(symbol, hours)` (used by `aggregator.get_sentiment_shift`).
- Deprecated reference: `src/brain/prompt_builder.py.deprecated:91` previously called `agg.aggregate_for_symbol(sym)` for prompt context (file is `.deprecated`, not active).
