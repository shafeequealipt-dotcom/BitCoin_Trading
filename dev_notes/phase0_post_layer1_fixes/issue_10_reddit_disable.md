# Issue 10 — RedditWorker silent disable + SENT_UNKNOWN per-coin spam

**Status:** PARTIAL — startup INFO log exists; per-coin spam unmodified.
**Tier:** 4 (log noise).
**Source observation:** `dev_notes/layer1_live_monitor_2026-04-27.md` lines 159-172 (Finding #4); 192 `SENT_UNKNOWN_CACHE_HIT` events in 30 min — dominant log tag.

## A. Mechanism

At `src/workers/manager.py:128-133`, the registration check:

```python
reddit_svc = None
if getattr(settings, "reddit", None) and settings.reddit.client_id:
    reddit_client = RedditClient(settings)
    reddit_svc = RedditService(reddit_client, scorer, db, settings)
else:
    log.info("Reddit: skipped (no API credentials configured)")
```

Worker registration at lines 862-863 is conditional on `reddit_svc` existence:

```python
if self._services.get("reddit"):
    self.workers.append(RedditWorker(s, db, self._services["reddit"]))
```

The startup INFO log exists but is at INFO level, which mixes with high-volume INFO traffic. Operators may miss it. Tag is also informal; not greppable as a structured event.

The downstream noise: `src/intelligence/sentiment/aggregator.py:127-154` evaluates per-coin sentiment. When `len(news_scores) == 0 AND len(reddit_scores) == 0`, both `SENT_NEUTRAL` (line 147-150) and `SENT_UNKNOWN` (line 151-154) emit per coin per cycle. With 50 coins x 12 cycles/hour, that's 600 + 600 = 1200 events/hour; live monitor counted 192 `SENT_UNKNOWN_CACHE_HIT` in 30 min (cache hits, slightly different tag).

The aggregator emits `SENT_UNKNOWN` to distinguish "no data exists" from "mixed news → neutral score". Phase-6 design rationale (per code comments).

## B. Dependencies

- **Reddit reader paths:** `RedditService`, `SentimentAggregator`. With Reddit disabled, the aggregator runs `news_only` path.
- **Aggregated sentiment consumers:** Stage 2 prompt builder reads `aggregated_sentiment` table — gracefully handles UNKNOWN as low-quality input.
- **Tag consumers:** No code greps `SENT_UNKNOWN` tag for behavior; only operator forensics.

## C. Constraints

- Must NOT break the UNKNOWN vs NEUTRAL distinction for the legitimate "Reddit configured but no rows for this coin this cycle" case.
- Must NOT modify `aggregated_sentiment` table writes (downstream consumer unchanged).
- Must NOT remove the per-cycle aggregator emission entirely — at least one event per cycle is helpful.

## D. Fix candidates

1. **Promote startup log to WARNING + degraded-mode aggregator emission (chosen).**
   - Change `manager.py:133` from `log.info(...)` to `log.warning("REDDIT_DISABLED | reason=no_credentials | impact=sentiment_degraded")` — structured event, WARNING level.
   - In aggregator: detect `settings.reddit` is None or empty client_id, set a `_reddit_intentionally_disabled` flag at init.
   - When the flag is set, suppress per-coin `SENT_UNKNOWN`. Instead emit one `SENTIMENT_DEGRADED_MODE | reason=no_reddit | source=fear_greed_only` per cycle.
   - When the flag is NOT set but a coin still has `reddit_n=0` (real data gap), keep emitting per-coin `SENT_UNKNOWN`. That's a different signal — a transient data gap, not a config decision.
   - Continue scoring sentiment from `fear_greed` alone (existing behavior).
2. Auto-acquire Reddit credentials. Rejected — out of scope; operator must opt-in.
3. Drop UNKNOWN distinction entirely. Rejected — loses Phase-6 design value.

## E. Observability gap

- Startup log at INFO is missable. Promote to WARNING.
- Aggregator emits per-coin per-cycle; missing a per-cycle "we are running degraded because Reddit is off" structured event.
- No way to count "we ran in degraded mode for N minutes" from logs without grepping per-coin events.

## F. Verification approach

- Unit test (aggregator with Reddit disabled): set `settings.reddit = None`, run aggregator over 50 mock coins → zero per-coin `SENT_UNKNOWN`, exactly one `SENTIMENT_DEGRADED_MODE`.
- Unit test (aggregator with Reddit enabled but empty rows): set `settings.reddit` valid, no rows in `reddit_posts`, run → per-coin `SENT_UNKNOWN` still fires for transient gaps.
- Live trial: 30-min window post-deploy → log diff `SENT_UNKNOWN` count drops from ~192 to <10 (only legitimate data gaps remain). Single `REDDIT_DISABLED` at startup. One `SENTIMENT_DEGRADED_MODE` per cycle.

## G. Rollback path

Two atomic commits:
- Revert manager.py log change → INFO restored.
- Revert aggregator change → per-coin `SENT_UNKNOWN` returns; `SENTIMENT_DEGRADED_MODE` removed.

No DB or state changes.
