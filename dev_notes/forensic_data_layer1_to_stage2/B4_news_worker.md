# B4 — NewsWorker

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.4.1 — File location, size, last modified

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/news_worker.py`
- Size: 2,870 bytes
- Lines of code: 75
- Last modified: 2026-04-27 04:32:34 UTC

## B.4.2 — Public methods (signatures + tick body)

Class declaration (line 17): `class NewsWorker(BaseWorker):` — `worker_tier = WorkerTier.LAYER1A` (line 28).

### `__init__` (line 30)
```python
def __init__(
    self,
    settings: Settings,
    db: DatabaseManager,
    news_service: NewsService,
    calendar_service: CalendarService | None = None,
) -> None:
    super().__init__(
        name="news_worker",
        interval_seconds=float(settings.workers.news_interval),
        settings=settings,
        db=db,
    )
    self.news_service = news_service
    self.calendar_service = calendar_service
    self._calendar_tick_count = 0
```

### `tick()` (line 47) — full body verbatim
```python
async def tick(self) -> None:
    """Fetch latest news and periodically update calendar."""
    t0 = time.monotonic()
    calendar_updated = False
    articles = await self.news_service.fetch_latest_news()

    self._calendar_tick_count += 1
    if self.calendar_service and self._calendar_tick_count >= 30:
        try:
            events = await self.calendar_service.get_upcoming_events()
            log.info("News worker: updated economic calendar ({n} events)", n=len(events))
            calendar_updated = True
        except Exception as e:
            log.warning("Calendar update failed: {err}", err=str(e))
        self._calendar_tick_count = 0

    el_ms = (time.monotonic() - t0) * 1000
    log.info(
        f"NEWS_TICK_SUMMARY | new={len(articles)} "
        f"calendar_updated={'Y' if calendar_updated else 'N'} "
        f"el={el_ms:.0f}ms | {ctx()}"
    )
    log.info(f"NEWS_FETCH | total={len(articles)} | {ctx()}")
    log.info("News worker: fetched {n} new articles", n=len(articles))
```

## B.4.3 — What it READS

- Finnhub crypto news via `NewsService.fetch_latest_news(category="crypto")` (default, news_service.py:46-67). The Finnhub HTTP call is `self._finnhub.get_general_news(category=category)` (news_service.py:67).
- Economic calendar via `CalendarService.get_upcoming_events()` every 30 ticks (news_worker.py:60).
- DB reads inside `news_service.fetch_latest_news`:
  - `await self._news_repo.headline_exists(headline)` (news_service.py:89) — dedup check.
- Config consumed:
  - `settings.workers.news_interval` → 300 s (config.toml:`[workers] news_interval = 300`) — tick cadence.
  - `settings.finnhub.max_articles_per_fetch` → 50 (config.toml:`[finnhub] max_articles_per_fetch = 50`).
  - `settings.finnhub.news_categories = ["crypto", "general"]` (only first/default consumed by tick).
  - `settings.finnhub.rate_limit_per_minute = 60`.

## B.4.4 — What it WRITES

In-memory:
- `self._calendar_tick_count: int` — counter (news_worker.py:45).

DB tables (via NewsService → NewsRepository):
- `news_articles` — `INSERT OR IGNORE INTO news_articles (id, headline, source, url, summary, sentiment_score, symbols, category, published_at, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)` at news_repo.py:84-101.
- `symbols` column stores JSON-encoded list of system tickers (e.g. `["BTCUSDT","ETHUSDT"]`) populated by `news_service.extract_symbols(text)` (news_service.py:99-100).

Schema:
```
CREATE TABLE news_articles (
    id TEXT PRIMARY KEY,
    headline TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    sentiment_score REAL NOT NULL DEFAULT 0,
    symbols TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT (datetime('now')),
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_news_published ON news_articles(published_at DESC);
CREATE INDEX idx_news_symbols ON news_articles(symbols);
```

## B.4.5 — Cadence

- Fixed-interval BaseWorker tick: `interval_seconds = settings.workers.news_interval = 300` (5 min).
- Per `news_service.fetch_latest_news`: requests Finnhub once (max 50 articles considered), filters by 24-h cutoff, dedup against DB, persists new rows.
- Calendar update: every 30 ticks (= 30 * 5 min = 150 min ≈ 2.5 h).

## B.4.SPECIAL — Article→symbol matching algorithm

Implementation lives in `src/intelligence/news/news_service.py` at lines 202-227 (`extract_symbols`):
```python
def extract_symbols(text: str) -> list[str]:
    lower = text.lower()
    found: set[str] = set()
    for name, symbol in SYMBOL_EXTRACTION_MAP.items():
        if len(name) <= 4:
            pattern = r'\b' + re.escape(name) + r'\b'
            if re.search(pattern, lower):
                found.add(symbol)
        else:
            if name in lower:
                found.add(symbol)
    return sorted(found)
```
Called from `NewsService.fetch_latest_news` (news_service.py:100):
```python
symbols = extract_symbols(text)
```
where `text = f"{headline} {summary}"` (news_service.py:94).

`SYMBOL_EXTRACTION_MAP` is defined verbatim in `src/intelligence/signals/signal_models.py:75-98`:
```python
SYMBOL_EXTRACTION_MAP: dict[str, str] = {
    # Full names
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "ripple": "XRPUSDT",
    "dogecoin": "DOGEUSDT",
    "cardano": "ADAUSDT",
    "avalanche": "AVAXUSDT",
    "polkadot": "DOTUSDT",
    "chainlink": "LINKUSDT",
    "polygon": "MATICUSDT",
    # Tickers (uppercase handled by caller)
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
    "doge": "DOGEUSDT",
    "ada": "ADAUSDT",
    "avax": "AVAXUSDT",
    "dot": "DOTUSDT",
    "link": "LINKUSDT",
    "matic": "MATICUSDT",
}
```

KEY OBSERVATION: the map covers **only 10 base assets** (BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, DOT, LINK, MATIC) — i.e. a strict subset of the 50-coin watch_list. Coins outside this map (BNB, ARB, NEAR, ATOM, INJ, RENDER, ONDO, ENA, PYTH, SEI, AERO, RUNE, GALA, MANA, SAND, AXS, LDO, CRV, DYDX, AAVE, ICP, IMX, HBAR, HYPE, GMT, FIL, MNT, MON, SKR, PLUME, EGLD, ALGO, BSB, KAT, HYPER, ORCA, BLUR, OP, APT, LTC, BCH, ALICE) are NEVER tagged on news rows by `news_worker` even when the article mentions them. There is a downstream fallback at `NewsRepository.get_by_symbol` (news_repo.py:121-160 → `extract_base_asset`) that retries with the stripped base asset when the literal `LIKE %BTCUSDT%` returns 0 rows, but that fallback can only succeed if the article was tagged in the first place using one of the map's keys.

## B.4.6 — Live measurements

Last 5 NEWS_FETCH / NEWS_TICK_SUMMARY events:
```
2026-04-27 22:33:13.351 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47
2026-04-27 22:33:13.352 | NEWS_TICK_SUMMARY | new=0 calendar_updated=N el=1065ms
2026-04-27 22:38:14.415 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47
2026-04-27 22:38:14.416 | NEWS_TICK_SUMMARY | new=0 calendar_updated=N el=1062ms
2026-04-27 22:43:15.521 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47
2026-04-27 22:43:15.522 | NEWS_TICK_SUMMARY | new=0 calendar_updated=N el=1105ms
2026-04-27 22:53:46.049 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=2 skipped_old=1 skipped_no_headline=0 skipped_dedup=47
2026-04-27 22:53:46.049 | NEWS_TICK_SUMMARY | new=2 calendar_updated=N el=5085ms
2026-04-27 22:58:47.116 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=1 skipped_no_headline=0 skipped_dedup=49
2026-04-27 22:58:47.116 | NEWS_TICK_SUMMARY | new=0 calendar_updated=N el=1064ms
```

LAYER1A_TICK_DONE for news:
```
2026-04-27 22:43:15.523 | LAYER1A_TICK_DONE | sub=news_worker elapsed_ms=1105 interval_s=300.0
2026-04-27 22:53:46.050 | LAYER1A_TICK_DONE | sub=news_worker elapsed_ms=5086 interval_s=300.0
2026-04-27 22:58:47.116 | LAYER1A_TICK_DONE | sub=news_worker elapsed_ms=1064 interval_s=300.0
```

### Article distribution per coin (last 1h, DB snapshot)

Query:
```sql
SELECT symbols, COUNT(*) FROM news_articles
WHERE published_at > datetime('now','-1 hour')
GROUP BY symbols ORDER BY 2 DESC;
```
Results (50 rows total, snapshot 2026-04-27 ≈ 22:59 UTC):
```
["BTCUSDT"]                                                          | 20
[]                                                                   | 20  (no symbols matched)
["ETHUSDT"]                                                          | 6
["XRPUSDT"]                                                          | 2
["ADAUSDT","BTCUSDT","DOGEUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]       | 1
["SOLUSDT"]                                                          | 1
```

Per-coin matched-article totals (counted by symbols-list membership, last 1h):
- BTCUSDT: 21 (20 + 1 from joint row)
- ETHUSDT: 7
- XRPUSDT: 3
- SOLUSDT: 2
- ADAUSDT: 1
- DOGEUSDT: 1
- All other 44 watch_list coins (BNB, ARB, NEAR, ATOM, INJ, RENDER, ONDO, ENA, PYTH, SEI, AERO, RUNE, GALA, MANA, SAND, AXS, LDO, CRV, DYDX, AAVE, ICP, IMX, HBAR, HYPE, GMT, FIL, MNT, MON, SKR, PLUME, EGLD, ALGO, BSB, KAT, HYPER, ORCA, BLUR, OP, APT, LTC, BCH, ALICE, AVAXUSDT, LINKUSDT): **0**.

20 articles in last 1h were tagged with empty symbols list `[]` — sample headlines from DB snapshot:
- "Canada advances bill to ban crypto political donations"
- "Tennessee crypto kiosk ban set to go into effect July 1"
- "Western Union eyeing stablecoin launch to settle global transactions without SWIFT, CEO says"
- "EU sanctions target Russian crypto exchanges, stablecoins and CBDC"
- "Curve founder pitches market-based fix for $700K bad debt in contrast to Aave bailout"  (mentions Aave/Curve but not in EXTRACTION_MAP)
- "Industry leaders are pouring hundreds of millions into a rescue plan for Aave users after massive crypto hack"  (mentions Aave but not mapped)
- "MiCA has made euro stablecoins safe but weak, new report argues"
- ...

Total `news_articles` row count: 1,226 (oldest 2026-03-29T02:24:30+00:00, newest 2026-04-27T21:50:17+00:00).

## B.4.7 — Failure modes (last 24h)

| Tag | Count | Source |
|-----|------:|--------|
| `Calendar update failed:` | 0 | news_worker.py:64 |
| `FINNHUB_*` ERROR / FAIL | 0 | (no error tag emitted by news_service) |

Coverage anomaly (NOT a failure but a structural data gap):
- Every observed FINNHUB_COVERAGE shows `returned=96 considered=50 skipped_dedup=47` — Finnhub's response contains 96 articles but only 50 are considered (capped by `max_articles_per_fetch`), and 47/50 are typically already persisted. New rows per tick: 0 - 2.

## B.4.8 — Dependencies (consumers)

- `src/database/repositories/news_repo.py:121` — `NewsRepository.get_by_symbol(symbol, hours, limit)` is the canonical reader. Caller list:
  - `src/intelligence/sentiment/aggregator.py:115` — `news_articles = await self._news_repo.get_by_symbol(symbol, hours=hours)` (`SentimentAggregator.aggregate_for_symbol`).
  - `src/intelligence/news/news_service.py:142` — `NewsService.get_news_for_symbol`.
- Aggregator further consumes `a.sentiment_score` from each row (aggregator.py:116 `news_scores = [a.sentiment_score for a in news_articles]`).
- `src/intelligence/news/news_service.py:168` — `get_news_summary(hours)` reads `news_articles` table for global summary.
- `news_service.search` (news_service.py:155) → `news_repo.search(keyword)`.
- Telegram + MCP tool exposures route through these same repo methods.
