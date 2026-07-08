# B3 — AltDataWorker

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.3.1 — File location, size, last modified

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/altdata_worker.py`
- Size: 11,457 bytes
- Lines of code: 274
- Last modified: 2026-04-27 08:49:18 UTC

## B.3.2 — Public methods (signatures + tick body)

Class declaration (line 30): `class AltDataWorker(SweetSpotWorker):` — `worker_tier = WorkerTier.LAYER1A` (line 52).

### `__init__` (line 54)
```python
def __init__(self, settings, db, fear_greed, funding, oi_tracker, onchain):
    super().__init__(
        name="altdata_worker",
        sweet_spot=settings.workers.sweet_spots.altdata.funding_rates,
        settings=settings, db=db,
        window_minutes=settings.workers.sweet_spots.window_minutes,
    )
    self.fear_greed = fear_greed
    self.funding = funding
    self.oi_tracker = oi_tracker
    self.onchain = onchain
    self.symbols: list[str] = list(settings.universe.watch_list)
    self._scanner = None
    self._next_oi_mono: float = 0.0
    self._next_fg_mono: float = 0.0
    self._oi_interval_s: float = float(
        settings.workers.sweet_spots.altdata.open_interest_minutes * 60
    )
    self._fg_interval_s: float = float(
        settings.workers.sweet_spots.altdata.fear_greed_minutes * 60
    )
    self._funding_cache: dict[str, float] = {}
```

### `tick()` (line 92) — full body verbatim
```python
async def tick(self) -> None:
    t0 = time.monotonic()
    universe = list(self.settings.universe.watch_list)
    if not universe:
        log.warning(
            f"ALTDATA_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
        )
        return
    self.symbols = universe

    fire_funding = self.funding is not None
    fire_oi = self.oi_tracker is not None and t0 >= self._next_oi_mono
    fire_fg = self.fear_greed is not None and t0 >= self._next_fg_mono
    fire_onchain = self.onchain is not None  # piggybacks funding cadence

    async def _timed(label: str, coro):
        t_sub = time.monotonic()
        try:
            result = await coro
            return (label, (time.monotonic() - t_sub) * 1000.0, result, None)
        except Exception as e:
            return (label, (time.monotonic() - t_sub) * 1000.0, None, e)

    tasks: list = []
    if fire_funding:
        tasks.append(_timed("funding", self._fetch_funding_rates()))
    if fire_oi:
        tasks.append(_timed("oi", self._fetch_open_interest()))
    if fire_fg:
        tasks.append(_timed("fear_greed", self._fetch_fear_greed()))
    if fire_onchain:
        tasks.append(_timed("onchain", self._fetch_onchain()))

    if not tasks:
        log.warning("AltData worker: no sources due this tick")
        return

    results = await asyncio.gather(*tasks)

    fg_val = None
    funding_count = 0
    oi_count = 0
    funding_el_ms = 0.0
    oi_el_ms = 0.0
    fg_el_ms = 0.0
    onchain_el_ms = 0.0

    gather_el_ms = (time.monotonic() - t0) * 1000

    for label, sub_el_ms, result, err in results:
        if err is not None:
            log.warning(
                f"ALTDATA_SOURCE_FAIL | src={label} el_ms={sub_el_ms:.0f} "
                f"err={str(err)[:120]} | {ctx()}"
            )
            if label == "funding":
                funding_el_ms = sub_el_ms
            elif label == "oi":
                oi_el_ms = sub_el_ms
            elif label == "fear_greed":
                fg_el_ms = sub_el_ms
            elif label == "onchain":
                onchain_el_ms = sub_el_ms
            continue
        if label == "fear_greed" and result:
            fg_val = result.value
            fg_el_ms = sub_el_ms
        elif label == "funding" and isinstance(result, list):
            funding_count = len(result)
            funding_el_ms = sub_el_ms
            for fr in result:
                sym = getattr(fr, "symbol", None)
                rate = getattr(fr, "funding_rate", None)
                if sym and rate is not None:
                    try:
                        self._funding_cache[sym] = float(rate)
                    except (TypeError, ValueError):
                        continue
        elif label == "oi" and isinstance(result, list):
            oi_count = len(result)
            oi_el_ms = sub_el_ms
        elif label == "onchain":
            onchain_el_ms = sub_el_ms

    now_mono = time.monotonic()
    if fire_oi:
        self._next_oi_mono = now_mono + self._oi_interval_s
    if fire_fg:
        self._next_fg_mono = now_mono + self._fg_interval_s

    if fire_funding:
        log.info(
            f"ALTDATA_FUNDING_TICK | universe={len(universe)} "
            f"fetched={funding_count} cached_size={len(self._funding_cache)} "
            f"el={funding_el_ms:.0f}ms drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )
    if fire_oi:
        log.info(
            f"ALTDATA_OI_TICK | universe={len(universe)} "
            f"fetched={oi_count} el={oi_el_ms:.0f}ms "
            f"next_in_s={self._oi_interval_s:.0f} | {ctx()}"
        )
    if fire_fg:
        log.info(
            f"ALTDATA_FG_TICK | value={fg_val} el={fg_el_ms:.0f}ms "
            f"next_in_s={self._fg_interval_s:.0f} | {ctx()}"
        )

    log.info(
        f"ALTDATA | fg={fg_val} funding={funding_count} oi={oi_count} "
        f"el={gather_el_ms:.0f}ms | {ctx()}"
    )

    ran = ",".join(
        label
        for label, fired in (
            ("funding", fire_funding),
            ("oi", fire_oi),
            ("fear_greed", fire_fg),
            ("onchain", fire_onchain),
        )
        if fired
    )
    log.info(
        f"ALTDATA_TICK_DONE | funding_ms={funding_el_ms:.0f} "
        f"oi_ms={oi_el_ms:.0f} fg_ms={fg_el_ms:.0f} "
        f"onchain_ms={onchain_el_ms:.0f} total_ms={gather_el_ms:.0f} "
        f"ran=[{ran}] | {ctx()}"
    )
```

### Public accessor `get_funding(coin) -> float | None` (line 254)
```python
def get_funding(self, coin: str) -> float | None:
    return self._funding_cache.get(coin)
```

### Private fetchers (lines 264-274)
```python
async def _fetch_fear_greed(self):
    return await self.fear_greed.fetch_current()

async def _fetch_funding_rates(self):
    return await self.funding.fetch_current_rates(self.symbols)

async def _fetch_open_interest(self):
    return await self.oi_tracker.fetch_current(self.symbols)

async def _fetch_onchain(self):
    return await self.onchain.get_global_metrics()
```

## B.3.3 — What it READS

Sub-feed clients are constructor-injected:
- `self.fear_greed: FearGreedClient | None` — wraps `https://api.alternative.me/fng/` (per `src/intelligence/altdata/fear_greed.py`).
- `self.funding: FundingRateTracker | None` — wraps Bybit funding-rate REST (per `src/intelligence/altdata/funding_rates.py:31` `fetch_current_rates`).
- `self.oi_tracker: OpenInterestTracker | None` — wraps Bybit `get_open_interest` (per `src/intelligence/altdata/open_interest.py:28-44`).
- `self.onchain: OnChainClient | None` — wraps CoinGecko `get_global_metrics` (per `src/intelligence/altdata/onchain.py:32`).

Universe: `settings.universe.watch_list` (50 coins) re-read every tick (altdata_worker.py:102).

Config consumed:
- `settings.workers.sweet_spots.altdata.funding_rates` → `"1:45"` (config.toml).
- `settings.workers.sweet_spots.altdata.open_interest_minutes` → `5`.
- `settings.workers.sweet_spots.altdata.fear_greed_minutes` → `60`.
- `settings.workers.sweet_spots.window_minutes` → `5`.

## B.3.4 — What it WRITES

In-memory:
- `self._funding_cache: dict[str, float]` — key = symbol, value = funding_rate float (altdata_worker.py:90, written :190).
- `self._next_oi_mono: float` — monotonic deadline for next OI fire (line 80, advanced :204).
- `self._next_fg_mono: float` — monotonic deadline for next F&G fire (line 81, advanced :206).
- `self.symbols: list[str]` — refreshed every tick from watch_list (line 108).

DB writes (delegated to sub-clients):
- `OpenInterestTracker.fetch_current()` calls `self._repo.save_open_interest(symbol, current_oi)` at `src/intelligence/altdata/open_interest.py:58` — writes `open_interest` table (verified via altdata_repo).
- `FearGreedClient.fetch_current()` writes Fear & Greed records via its own repo (per fear_greed.py:39 + altdata_repo).
- `FundingRateTracker.fetch_current_rates()` writes via altdata_repo (used by `get_funding_rates` reader at altdata_repo.py:99).
- `OnChainClient.get_global_metrics()` returns dict; persistence is delegated to its repo path.

## B.3.5 — Cadence

- Sweet-spot wakeup: `1:45` within every 5-min window.
- Per-source cadences:
  - **funding**: every wakeup (no deadline gate; `fire_funding = self.funding is not None`).
  - **oi**: every `_oi_interval_s = 5 * 60 = 300 s` via `t0 >= self._next_oi_mono`.
  - **fear_greed**: every `_fg_interval_s = 60 * 60 = 3600 s` via `t0 >= self._next_fg_mono`.
  - **onchain**: piggybacks funding (every wakeup; only gated by `self.onchain is not None`).
- Initial deadlines = 0.0 (constructor, lines 80-81), so first tick fires every source once.

## B.3.SPECIAL — Sub-feed schedule (live verification)

Verbatim ALTDATA_TICK_DONE events (last 7 in `data/logs/workers.log`):
```
2026-04-27 22:21:52.290 | ALTDATA_TICK_DONE | funding_ms=7269 oi_ms=0    fg_ms=0    onchain_ms=2763 total_ms=7273 ran=[funding,onchain]
2026-04-27 22:26:55.155 | ALTDATA_TICK_DONE | funding_ms=10137 oi_ms=9940 fg_ms=0    onchain_ms=2661 total_ms=10137 ran=[funding,oi,onchain]
2026-04-27 22:31:50.191 | ALTDATA_TICK_DONE | funding_ms=5190  oi_ms=0    fg_ms=0    onchain_ms=2670 total_ms=5190  ran=[funding,onchain]
2026-04-27 22:36:54.158 | ALTDATA_TICK_DONE | funding_ms=8444  oi_ms=9155 fg_ms=0    onchain_ms=2678 total_ms=9156  ran=[funding,oi,onchain]
2026-04-27 22:41:50.061 | ALTDATA_TICK_DONE | funding_ms=5059  oi_ms=0    fg_ms=0    onchain_ms=2637 total_ms=5059  ran=[funding,onchain]
2026-04-27 22:56:54.192 | ALTDATA_TICK_DONE | funding_ms=9020  oi_ms=9190 fg_ms=976  onchain_ms=2669 total_ms=9190  ran=[funding,oi,fear_greed,onchain]
```

Cadence verified: tick interval ≈ 5 min (sweet-spot wakeup). OI fires every 2nd tick (300 s ≈ 5 min cadence with skip). F&G appears once across the captured window (3600 s cadence). Funding+onchain fire every tick.

## B.3.SPECIAL2 — Why some ticks have only 3 of 4 sub-feeds

The "F&G missing" pattern (`ran=[funding,oi,onchain]`) arises from the deadline gate at altdata_worker.py:113:
```
fire_fg = self.fear_greed is not None and t0 >= self._next_fg_mono
```
After F&G fires, line 206 advances the deadline:
```
if fire_fg:
    self._next_fg_mono = now_mono + self._fg_interval_s    # +3600 s
```
With `_fg_interval_s = 3600 s` and the 5-min sweet-spot wakeup, F&G fires roughly once every 12 wakeups. All other 11 wakeups satisfy `t0 < self._next_fg_mono` and the F&G branch is omitted from `tasks`. This is by design (config: `fear_greed_minutes = 60`).

Same mechanism for OI with a 300 s deadline (line 204). Live observation confirms: 22:21:52 had no OI (skipped because `_next_oi_mono` was set on the previous fire); 22:26:55 had OI (deadline expired); 22:31:50 had no OI again. Ratio matches the 5-min cooldown.

ALTDATA_FG_TICK observed (line 222) verbatim:
```
2026-04-27 22:56:54.192 | ALTDATA_FG_TICK | value=47 el=976ms next_in_s=3600 | no_ctx
```
Single F&G fire in the captured window with `value=47`.

## B.3.6 — Live measurements

Funding cache size: `cached_size=50` per ALTDATA_FUNDING_TICK (line 213 in code; observed across all post-22:26 fires).

ALTDATA_FUNDING_TICK examples:
```
2026-04-27 22:26:55.155 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=10137ms drift_ms=18
2026-04-27 22:31:50.191 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=5190ms drift_ms=1
2026-04-27 22:36:54.158 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=8444ms drift_ms=2
2026-04-27 22:41:50.061 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=5059ms drift_ms=1
2026-04-27 22:56:54.192 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=9020ms drift_ms=1
```

ALTDATA_OI_TICK examples:
```
2026-04-27 22:26:55.156 | ALTDATA_OI_TICK | universe=50 fetched=50 el=9940ms next_in_s=300
2026-04-27 22:36:54.158 | ALTDATA_OI_TICK | universe=50 fetched=50 el=9155ms next_in_s=300
2026-04-27 22:56:54.192 | ALTDATA_OI_TICK | universe=50 fetched=50 el=9190ms next_in_s=300
```

LAYER1A_TICK_DONE for altdata:
```
2026-04-27 22:41:50.061 | LAYER1A_TICK_DONE | sub=altdata_worker elapsed_ms=5060 drift_ms=1
2026-04-27 22:56:54.193 | LAYER1A_TICK_DONE | sub=altdata_worker elapsed_ms=9191 drift_ms=1
```

## B.3.7 — Failure modes (last 24h)

| Tag | Count | Source |
|-----|------:|--------|
| `ALTDATA_SOURCE_FAIL` | 0 | altdata_worker.py:163 |
| `ALTDATA_UNIVERSE_EMPTY` | 0 | altdata_worker.py:105 |
| `AltData worker: no sources due this tick` | 0 | altdata_worker.py:142 |

GAP: log retention covers ≈ 1 hour 20 min only. F&G shows `value=None` in the legacy ALTDATA aggregate line on the 5 ticks where it didn't fire (e.g. `ALTDATA | fg=None funding=50 oi=50 el=...`) — the `None` is by-design (only assigned when the fear_greed task fired in this cycle), not a fetch failure.

## B.3.8 — Dependencies (consumers)

`get_funding(coin)` consumers:
- `src/workers/scanner_worker.py:154-164` — `_get_funding_strength()` reads `services.get("altdata_worker").get_funding(coin)`.
- `src/workers/scanner_worker.py:280-282` — direction/blocker check uses `adw.get_funding(symbol)`.
- `src/workers/scanner_worker.py:424-428` — composite scoring path also reads `adw.get_funding(symbol)`.

`altdata_worker` itself is registered into `self._services["altdata_worker"]` at `src/workers/manager.py:972` so any worker with `self.services` access can `services.get("altdata_worker")`.

DB-table consumers (indirect, via repos):
- `funding_rates` table → `AltDataRepository.get_funding_rates(symbol, hours)` at altdata_repo.py:99 — used by `funding_rates.py:132` (`get_funding_history`).
- `open_interest` table → `AltDataRepository.save_open_interest` (open_interest.py:58 caller).
- `fear_greed` table → `AltDataRepository.get_latest_fear_greed()` — read by `src/intelligence/sentiment/aggregator.py:131` (`fg = await self._altdata_repo.get_latest_fear_greed()`).
- `OnChainClient.get_global_metrics()` returns global market data (BTC dominance, total market cap) — consumed wherever onchain context is read.

`_funding_cache` is a private dict; only the `get_funding()` accessor is public.
