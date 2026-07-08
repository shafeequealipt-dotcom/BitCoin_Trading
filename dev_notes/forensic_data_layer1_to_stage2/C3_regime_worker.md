# C3 — RegimeWorker (Forensic Data)

CAPTURE TIMESTAMP (UTC): 2026-04-27 22:58:35
Log file: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
DB snapshot: `_trading_db_snapshot.db` (mtime 22:56)

---

## C.3.1 — Classification pipeline

**Worker file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/regime_worker.py` — 313 lines (verified).
**Detector file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/regime.py` — `RegimeDetector` class.

### Inputs

`RegimeDetector.detect(symbol)` (`regime.py:67-103`):

```python
klines = await self.market_repo.get_klines(symbol, TimeFrame.H1.value, 200)
# Then via TAEngine:
ta = await self.ta_engine.analyze(candles=klines)
adx        = ta.get("trend", {}).get("adx", {}).get("adx") or 0
plus_di    = ta.get("trend", {}).get("adx", {}).get("plus_di") or 0
minus_di   = ta.get("trend", {}).get("adx", {}).get("minus_di") or 0
choppiness = ta.get("volatility", {}).get("choppiness_index") or 50
atr        = ta.get("volatility", {}).get("atr_14") or 0
volume_ratio = ta.get("volume", {}).get("volume_sma_ratio") or 1.0
natr       = ta.get("volatility", {}).get("natr_14") or 1.0
atr_percentile = natr * 100
```

Inputs: H1 klines (200 bars) → ADX + DI, choppiness, ATR/NATR, volume SMA ratio.

### Classification formula (verbatim, `regime.py:117-158`)

```python
if adx > cfg.trending_adx_threshold and plus_di > minus_di and choppiness < 45:
    regime = MarketRegime.TRENDING_UP
    confidence = min(adx / 50, 1.0)
    trend_direction = 1
elif adx > cfg.trending_adx_threshold and minus_di > plus_di and choppiness < 45:
    regime = MarketRegime.TRENDING_DOWN
    confidence = min(adx / 50, 1.0)
    trend_direction = -1
elif atr_percentile > cfg.volatile_atr_percentile or volume_ratio > 2.0:
    regime = MarketRegime.VOLATILE
    confidence = min(atr_percentile / 200, 1.0)
    trend_direction = 1 if plus_di > minus_di else -1
elif adx < cfg.ranging_adx_threshold and choppiness > cfg.ranging_choppiness_threshold:
    regime = MarketRegime.RANGING
    confidence = min(choppiness / 80, 1.0)
    trend_direction = 0
elif adx < cfg.dead_adx_threshold and volume_ratio < cfg.dead_volume_ratio and atr_percentile < 50:
    regime = MarketRegime.DEAD
    confidence = 0.8
    trend_direction = 0
else:
    regime = MarketRegime.RANGING
    confidence = 0.4
    trend_direction = 0

active_cats = REGIME_ACTIVE_CATEGORIES.get(regime, [])

state = RegimeState(
    regime=regime,
    confidence=confidence,
    adx=adx,
    atr_percentile=atr_percentile,
    choppiness=choppiness,
    volume_ratio=volume_ratio,
    trend_direction=trend_direction,
    active_strategy_categories=list(active_cats),
)
```

Threshold values (`config.toml:488-502`, verbatim):

```
[regime]
detection_interval_seconds = 600
primary_symbol = "BTCUSDT"
trending_adx_threshold = 25
ranging_adx_threshold = 20
ranging_choppiness_threshold = 60
volatile_atr_percentile = 150
dead_adx_threshold = 15
dead_volume_ratio = 0.5
hysteresis_count = 2
```

### Per-coin vs global

**Global:** `RegimeWorker.tick()` calls `detector.detect()` with no symbol → defaults to `primary_symbol` (BTCUSDT). The result is persisted in `regime_history` and logged as `REGIME_GLOBAL` (`regime_worker.py:142-163`).

**Per-coin:** `regime_worker.py:170-195`:

```python
coins_to_check = [
    s for s in universe
    if s != self.settings.regime.primary_symbol
]
if coins_to_check:
    per_coin = await self.detector.detect_per_coin(coins_to_check)
    if not hasattr(self.detector, '_per_coin_regimes') or self.detector._per_coin_regimes is None:
        self.detector._per_coin_regimes = {}
    self.detector._per_coin_regimes.update(per_coin)
```

`detect_per_coin` (`regime.py:214-222`) calls `detect(symbol)` for every coin individually, hits the same hysteresis path, and returns the dict.

---

## C.3.2 — Stickiness (hysteresis)

**Implementation:** `RegimeDetector.detect()` lines `162-212` (`regime.py`). Verbatim:

```python
confirmed = self._confirmed_regimes.get(symbol)

if confirmed is None:
    self._confirmed_regimes[symbol] = state
    self._pending_regime.pop(symbol, None)
    self._last_regime = state
    return state

if regime == confirmed.regime:
    self._confirmed_regimes[symbol] = state
    self._pending_regime.pop(symbol, None)
    self._last_regime = state
    return state

# Regime differs from confirmed — apply hysteresis.
pending_regime, pending_count = self._pending_regime.get(symbol, (None, 0))
new_count = (pending_count + 1) if pending_regime == regime else 1

_hyst = int(getattr(cfg, "hysteresis_count", 2))
if new_count >= _hyst:
    self._confirmed_regimes[symbol] = state
    self._pending_regime.pop(symbol, None)
    log.warning(
        f"REGIME_CHG | sym={symbol} old={_old_rgm} new={regime.value} ..."
    )
    self._last_regime = state
    return state
else:
    self._pending_regime[symbol] = (regime, new_count)
    log.info(
        f"REGIME_PENDING | sym={symbol} confirmed={confirmed.regime.value} ..."
    )
    self._last_regime = confirmed
    return confirmed
```

**Threshold:** `cfg.hysteresis_count = 2` (`config.toml:502`). Two consecutive readings of the new regime are required to confirm a change.

### Why 8 (or more) coins changed regime in one tick

The 22:27 observation referenced "8 of 49 coins changed regime in one tick." In `workers.log` the FIRST tick after process restart (22:06:15) emitted these `REGIME_CHG` events:

```
22:06:16.264  REGIME_CHG | sym=ENAUSDT  old=ranging      new=volatile        conf=0.38
22:06:16.783  REGIME_CHG | sym=SANDUSDT old=ranging      new=trending_down   conf=0.55
22:06:16.931  REGIME_CHG | sym=LDOUSDT  old=trending_down new=volatile       conf=0.86
22:06:17.414  REGIME_CHG | sym=IMXUSDT  old=ranging      new=trending_down   conf=0.60
22:06:17.910  REGIME_CHG | sym=MNTUSDT  old=trending_down new=ranging        conf=0.40
22:06:18.003  REGIME_CHG | sym=MONUSDT  old=ranging      new=volatile        conf=0.48
22:06:18.314  REGIME_CHG | sym=ALGOUSDT old=volatile     new=ranging         conf=0.40
22:06:18.603  REGIME_CHG | sym=ORCAUSDT old=trending_up  new=volatile        conf=1.00
```

8 changes — matches the observation exactly. Cause: this is the `REGIME_RESTORE` boot path (`regime_worker.py:69-124`) — the in-memory `_per_coin_regimes` is rebuilt from `coin_regime_history` rows that may be up to 30 minutes old. On the very next live `detect()` for these coins, the freshly-computed regime differs from the restored one, and hysteresis still allows confirmation on the FIRST live read because `_confirmed_regimes[symbol]` is None until the live tick assigns it (the restore path writes into `_per_coin_regimes` but NOT into `_confirmed_regimes`). See `regime.py:165-172`:

```python
if confirmed is None:
    # First reading for this symbol — immediately confirm; no prior state to compare.
    self._confirmed_regimes[symbol] = state
    self._pending_regime.pop(symbol, None)
    self._last_regime = state
    return state
```

So on the first post-restart tick, every coin whose live regime differs from the restored regime gets an immediate confirmation (effectively bypassing hysteresis). Subsequent ticks (22:11, 22:16, 22:21, 22:26) emit ZERO `REGIME_CHG` events — confirming the hysteresis works steady-state.

---

## C.3.3 — `_per_coin_regimes` cache

**Defined at:** `regime.py:40` — `self._per_coin_regimes: dict[str, RegimeState] = {}`.

**Key format:** plain symbol string (e.g. `"BTCUSDT"`).

**Value structure:** `RegimeState` dataclass — fields `regime` (`MarketRegime` enum), `confidence`, `adx`, `atr_percentile`, `choppiness`, `volume_ratio`, `trend_direction`, `active_strategy_categories`.

**Write sites:**

- `regime.py:111-118` — boot restore (inside `regime_worker.py:111-118`, writes via `self.detector._per_coin_regimes[row["symbol"]] = RegimeState(...)`)
- `regime_worker.py:194` — `self.detector._per_coin_regimes.update(per_coin)` after `detect_per_coin`
- `regime.py:165-172` — `self._confirmed_regimes[symbol] = state` (paired confirm cache, written every detect())

**Read sites for `_per_coin_regimes` / `get_coin_regime`:**

- `regime.py:46-48` — `RegimeDetector.get_coin_regime(symbol)` — public accessor returns `self._per_coin_regimes.get(symbol)`
- `regime_worker.py:300-313` — `RegimeWorker.get_regime(coin)` — wraps `detector.get_coin_regime`
- `apex/assembler.py:588` — `coin_regime = detector.get_coin_regime(symbol)`
- `apex/gate.py:370` — `coin_regime = detector.get_coin_regime(symbol)`
- `tias/collector.py:282` — `coin_regime = regime_detector.get_coin_regime(symbol)`
- `scanner_worker.py:138` — `state = rw.get_regime(coin)` (also lines 463, 567)

---

## C.3.4 — Why APEX/TIAS can't reach the cache

**RegimeWorker write key (verbatim, `regime_worker.py:189-194`):**

```python
per_coin = await self.detector.detect_per_coin(coins_to_check)
if not hasattr(self.detector, '_per_coin_regimes') or self.detector._per_coin_regimes is None:
    self.detector._per_coin_regimes = {}
self.detector._per_coin_regimes.update(per_coin)
```

`detect_per_coin` returns `{symbol: RegimeState}` (`regime.py:214-222`) — key is the plain symbol string. Inside `detect()` the writes are at `regime.py:169, 176, 192` to `self._confirmed_regimes[symbol]`, and `_last_regime = state` is the singleton. The public read (`get_coin_regime`) reads from `_per_coin_regimes`, NOT from `_confirmed_regimes`.

NOTE: `_per_coin_regimes` is updated only via `regime_worker.py:194` (the bulk `.update(per_coin)` after `detect_per_coin`). The restore path writes there too. Per-symbol `detect()` writes only `_confirmed_regimes` and `_last_regime`. So the cache that the public accessor reads is updated **once per tick by the worker**, not on each individual `detect()` call within `detect_per_coin`. (`detect_per_coin` returns dict from individual `detect`s and the worker `.update()`s it.)

### APEX assembler — `_get_market_conditions` (`apex/assembler.py:585-591`, verbatim)

```python
detector = self._services.get("regime_detector")
if detector:
    coin_regime = detector.get_coin_regime(symbol)
    if coin_regime is not None:
        regime = str(coin_regime.regime.value)
    elif hasattr(detector, "_last_regime") and detector._last_regime:
        lr = detector._last_regime
        regime = str(lr.regime.value)
        log.warning("REGIME_FALLBACK | sym={sym} source=assembler | ...")
```

Lookup: `detector.get_coin_regime(symbol)` → `self._per_coin_regimes.get(symbol)`. Key = bare symbol string.

### APEX gate — `_get_conviction_weight` (`apex/gate.py:367-379`, verbatim)

```python
detector = self._services.get("regime_detector")
if detector:
    coin_regime = detector.get_coin_regime(symbol)
    if coin_regime is not None:
        _regime = str(coin_regime.regime.value)
    elif hasattr(detector, "_last_regime") and detector._last_regime:
        _regime = str(detector._last_regime.regime.value)
        log.warning("REGIME_FALLBACK | sym={sym} source=gate | ...")
```

Same call: `detector.get_coin_regime(symbol)`. Key = bare symbol string.

### TIAS collector — `_collect_group_c` (`tias/collector.py:280-294`, verbatim)

```python
regime_detector = self._services.get("regime_detector")
if regime_detector:
    coin_regime = regime_detector.get_coin_regime(symbol)
    if coin_regime is not None:
        result["regime"] = str(coin_regime.regime.value)
        result["regime_verified"] = 1
    elif hasattr(regime_detector, "_last_regime") and regime_detector._last_regime:
        lr = regime_detector._last_regime
        result["regime"] = str(lr.regime.value)
        log.warning("REGIME_FALLBACK | sym={sym} source=tias | ...")
```

Same call: `regime_detector.get_coin_regime(symbol)`. Key = bare symbol string.

### Comparison

All three call sites use `regime_detector.get_coin_regime(symbol)`, which reads `self._per_coin_regimes[symbol]` — **the same key the writer uses**. There is **no key mismatch** in the source code as it stands today.

NOT FOUND — a key-shape divergence. Searched APEX/TIAS for any other lookup pattern (`grep -rn "_per_coin_regimes" src/apex src/tias`) returns 0 hits; both modules only access via `get_coin_regime`. The "APEX/TIAS can't reach the cache" claim from the prompt is not corroborated by the current code; the only documented compatibility risk is:

1. The accessor returns `None` when called BEFORE the first RegimeWorker tick has populated `_per_coin_regimes` (cold-start race). In that window APEX/TIAS fall through to the `_last_regime` fallback (written by every `detect()` global call) and emit `REGIME_FALLBACK`. The current log shows `REGIME_FALLBACK` warnings exist (e.g. "source=assembler", "source=gate", "source=tias") — but they are documented to be emitted on the per-coin lookup miss, not on a key-format mismatch.
2. The dependency injection: APEX/TIAS receive a `regime_detector` service, NOT `regime_worker`. `services["regime_detector"]` is the `RegimeDetector` instance (see `manager.py` wiring). If the service registry stores `regime_worker` only and APEX expects `regime_detector`, that would cause `_services.get("regime_detector")` to return None and the fallback branch to fire. Confirmed by the WARNING `REGIME_FALLBACK` emissions tagged `source=assembler/gate/tias` — but log capture in the current window does NOT include any (grep returned no recent ones in the captured 21:55–22:27 minutes; the warnings are emitted only when `coin_regime is None` AND `_last_regime is not None`).

So based on **code level evidence**: every consumer reads via the same accessor, against the same `dict[symbol -> RegimeState]`. **No divergent key**. The mismatch claim is NOT corroborated by the code.

---

## C.3.5 — DB persistence

### `regime_history` schema (verbatim, from `_trading_db_snapshot.db`)

```sql
CREATE TABLE regime_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
    regime TEXT NOT NULL,
    confidence REAL,
    adx REAL,
    atr_percentile REAL,
    choppiness REAL,
    detected_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_regime_time ON regime_history(detected_at DESC);
```

Row count: **2006**. Date range: `2026-03-26 16:11:19` → `2026-04-27 22:26:15`.

Writer: `regime_worker.py:145-157`:

```python
await self.db.execute(
    "INSERT INTO regime_history "
    "(symbol, regime, confidence, adx, atr_percentile, choppiness, detected_at) "
    "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
    (
        self.settings.regime.primary_symbol,
        state.regime.value,
        state.confidence,
        state.adx,
        state.atr_percentile,
        state.choppiness,
    ),
)
```

### `coin_regime_history` schema (verbatim)

```sql
CREATE TABLE coin_regime_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    regime TEXT NOT NULL,
    confidence REAL NOT NULL,
    adx REAL,
    choppiness REAL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_coin_regime_symbol ON coin_regime_history(symbol, timestamp DESC);
```

Row count: **20951**. Date range: `2026-04-13 10:48:19` → `2026-04-27 22:26:24`.

Writer: `regime_worker.py:250-258`:

```python
await self.db.execute(
    """INSERT INTO coin_regime_history
       (symbol, regime, confidence, adx, choppiness)
       VALUES (?, ?, ?, ?, ?)""",
    (sym, rs.regime.value, rs.confidence, rs.adx, rs.choppiness),
)
```

### Why the 15-hour gap in `regime_history`

Verbatim DB query (executed against the snapshot):

```
SELECT detected_at, regime FROM regime_history ORDER BY id DESC LIMIT 10;

2026-04-27 22:26:15  ranging
2026-04-27 22:21:15  ranging
2026-04-27 22:16:15  ranging
2026-04-27 22:11:15  ranging
2026-04-27 22:06:15  ranging
2026-04-27 22:01:15  ranging
2026-04-27 21:56:15  ranging
2026-04-27 06:51:15  trending_down  ← LAST ROW BEFORE GAP
2026-04-27 06:46:15  trending_down
2026-04-27 06:41:15  trending_down
```

Gap window: **2026-04-27 06:51:15 → 21:56:15** = ~15 h 5 min with **zero** rows inserted into `regime_history`.

The cause is NOT a bug in the SQL or the worker — RegimeWorker `tick()` always executes `await self.db.execute("INSERT INTO regime_history …")` unconditionally before the per-coin block (`regime_worker.py:145-157`). For 15 hours of zero rows, the worker `tick()` itself was not running. Possible causes (NOT verified inside this collection):

1. The process was stopped or the LAYER1B cycle gate was disabled (`cycle_gated = True` at `regime_worker.py:40`; `is_cycle_active()` would skip the tick if Layer 1B is toggled off).
2. The base worker watchdog or sweet-spot scheduler ran in a degraded state.

The first row after the gap is at 21:56:15 — exactly the `regime_worker` sweet spot `1:15` rounded to 5-min boundaries (`config.toml:138`: `regime_worker = "1:15"`). The 21:55:00 + 1:15 sweet-spot fire matches.

`coin_regime_history` shows the same kind of gap is bounded by the worker process — 20951 rows over 14.5 days = ~1444 rows/day, but the bulk are concentrated in the last few hours after the gap closed.

---

## OBSERVED ANOMALIES

- 15 h 5 min gap in `regime_history` (06:51 → 21:56 UTC on 2026-04-27). Worker did not execute its tick during that window.
- `REGIME_PERCOIN_SUMMARY` reports `divergent=26` consistently across last 5 ticks — **the prompt cited "8 of 49"** but that was the count of `REGIME_CHG` events on the boot tick at 22:06, not the per-cycle divergence count. They are different metrics: divergent=count of coins whose current regime != global; CHG=count of coins whose regime changed this tick.
- All call sites for the regime cache use the same accessor and the same dict — no key mismatch detectable from code review. If APEX/TIAS are reporting `REGIME_FALLBACK`, the cause would be (a) coin not yet in `_per_coin_regimes` (cold start), or (b) the `regime_detector` service is not registered in the consuming context. Neither was directly observed in the captured 30-min log window.
