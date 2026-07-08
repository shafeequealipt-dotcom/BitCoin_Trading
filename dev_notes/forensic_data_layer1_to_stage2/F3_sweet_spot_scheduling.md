# F3 — Sweet-Spot Scheduling Wiring

## F3.1 — Scheduler implementation

**File:** `src/workers/sweet_spot_scheduler.py` (255 lines).
**Last modified:** confirmed by `wc -l` output `255 /home/.../sweet_spot_scheduler.py`.

### Algorithm — verbatim from source

`parse_sweet_spot(value)` — `src/workers/sweet_spot_scheduler.py:26-61`:

```python
def parse_sweet_spot(value: str) -> tuple[int, int]:
    if not isinstance(value, str):
        raise ValueError(...)
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"sweet spot must be in MM:SS format, got {value!r}")
    try:
        m = int(parts[0])
        s = int(parts[1])
    except ValueError as e:
        raise ValueError(...)
    if m < 0 or m > 59:
        raise ValueError(f"sweet spot minute must be 0-59, got {m}")
    if s < 0 or s > 59:
        raise ValueError(f"sweet spot second must be 0-59, got {s}")
    return (m, s)
```

`seconds_until_next_sweet_spot(spot, *, window_minutes=5, now=None, skip_threshold_s=0.1)`
— `src/workers/sweet_spot_scheduler.py:64-101`:

```python
def seconds_until_next_sweet_spot(spot, *, window_minutes=5, now=None,
                                  skip_threshold_s=0.1):
    if now is None:
        now = time.time()
    window_s = window_minutes * 60
    spot_s = spot[0] * 60 + spot[1]
    pos_in_window = now % window_s
    delta = spot_s - pos_in_window
    if delta > skip_threshold_s:
        return delta
    return delta + window_s
```

`is_at_sweet_spot(spot, *, window_minutes=5, now=None, tolerance_s=1.0)`
— `src/workers/sweet_spot_scheduler.py:104-121`. Used by tests only.

`SweetSpotScheduler.__init__` — `sweet_spot_scheduler.py:162-180`:

```python
def __init__(self, worker_name, offset, window_minutes=5):
    self.worker_name = worker_name
    self.offset_str = offset
    self.offset = parse_sweet_spot(offset)
    self.window_minutes = int(window_minutes)
    if self.window_minutes < 1:
        raise ValueError(...)
    self.stats = SweetSpotStats()
    log.info(
        f"SWEET_SPOT_REGISTERED | worker={self.worker_name} "
        f"offset={self.offset_str} window_min={self.window_minutes} | {ctx()}"
    )
```

`SweetSpotScheduler.wait_for_sweet_spot` — `sweet_spot_scheduler.py:188-242`:

```python
async def wait_for_sweet_spot(self) -> float:
    delay_s = self.seconds_until_next()
    await asyncio.sleep(delay_s)

    now = time.time()
    window_s = self.window_minutes * 60
    spot_s = self.offset[0] * 60 + self.offset[1]
    pos_in_window = now % window_s
    drift_s = pos_in_window - spot_s
    if abs(drift_s - window_s) < abs(drift_s):
        drift_s -= window_s
    elif abs(drift_s + window_s) < abs(drift_s):
        drift_s += window_s
    drift_ms = drift_s * 1000.0

    self.stats.fires += 1
    self.stats.cumulative_drift_ms += abs(drift_ms)
    if abs(drift_ms) > self.stats.max_drift_ms:
        self.stats.max_drift_ms = abs(drift_ms)
    self.stats.last_drift_ms = drift_ms

    log.info(
        f"SWEET_SPOT_FIRED | worker={self.worker_name} "
        f"offset={self.offset_str} drift_ms={drift_ms:.0f} "
        f"fires={self.stats.fires} | {ctx()}"
    )
    try:
        from src.core.worker_liveness import get_default_tracker
        get_default_tracker().record_sweet_spot(self.worker_name)
    except Exception:
        pass
    return drift_ms
```

The scheduler is owned by every `SweetSpotWorker` (constructed in
`SweetSpotWorker.__init__` at `src/workers/base_worker.py:516-520`).
The run loop calls `wait_for_sweet_spot()` BEFORE every tick at
`base_worker.py:555` so the worker waits FIRST then ticks.

---

## F3.2 — Per-worker sweet-spot schedule

Configured in `config.toml:133-148` (verbatim):

```toml
[workers.sweet_spots]
window_minutes = 5
kline_worker = "0:30"
structure_worker = "0:45"
signal_worker = "1:00"
regime_worker = "1:15"
strategy_worker = "1:30"
scanner_worker = "4:00"

[workers.sweet_spots.altdata]
# Funding rates: MM:SS within window, between regime (1:15) and scanner (4:00).
funding_rates = "1:45"
# Open interest: every N minutes, independent of window.
open_interest_minutes = 5
# Fear & Greed: every M minutes, hourly default.
fear_greed_minutes = 60
```

Defaults match (in `src/config/settings.py:292-298` and `:251-253`).

### Per-worker tick interval and config source

| Worker | Tier | Sweet-spot offset | Tick body | Config source (file:line) | Cadence |
|--------|------|-------------------|-----------|---------------------------|---------|
| `price_worker` | LAYER1A | N/A (continuous WS) | health/reconnect heartbeat | `settings.workers.market_data_interval` (`config.toml:108` = 45s) | Every 45 s (BaseWorker fixed interval) |
| `kline_worker` | LAYER1A | `0:30` | M5/H1/H4/D1 fetch + DB write | `settings.workers.sweet_spots.kline_worker` (`config.toml:135`) | Once per 5-min window (sweet-spot) |
| `structure_worker` | LAYER1B | `0:45` | X-RAY analysis (batched 25/cycle) | `settings.workers.sweet_spots.structure_worker` (`config.toml:136`) | Once per 5-min window |
| `signal_worker` | LAYER1B | `1:00` | sentiment aggregation + signal generation | `settings.workers.sweet_spots.signal_worker` (`config.toml:137`) | Once per 5-min window |
| `regime_worker` | LAYER1B | `1:15` | global + per-coin regime detection | `settings.workers.sweet_spots.regime_worker` (`config.toml:138`) | Once per 5-min window |
| `strategy_worker` | LAYER1C | `1:30` | Layer 1-4 strategy pipeline | `settings.workers.sweet_spots.strategy_worker` (`config.toml:139`) | Once per 5-min window |
| `altdata_worker` | LAYER1A | `1:45` (funding) | funding/OI/F&G/onchain (per-source deadlines) | `settings.workers.sweet_spots.altdata.funding_rates` (`config.toml:144`) | Funding every wake (5 min); OI every 5 min; F&G every 60 min |
| `scanner_worker` | LAYER1D | `4:00` | qualify/rank/build packages/write active_universe | `settings.workers.sweet_spots.scanner_worker` (`config.toml:140`) | Once per 5-min window |
| `news_worker` | LAYER1A | N/A (fixed) | Finnhub poll | `settings.workers.news_interval` (`config.toml:110` = 300s) | Every 300 s |

Worker tier mapping (single source of truth) — from each worker file:
- `kline_worker.py:73`: `worker_tier = WorkerTier.LAYER1A`
- `price_worker.py:41`: `worker_tier = WorkerTier.LAYER1A`
- `altdata_worker.py:52`: `worker_tier = WorkerTier.LAYER1A`
- `structure_worker.py:46-48`: `worker_tier = WorkerTier.LAYER1B; cycle_gated = True`
- `signal_worker.py:42-44`: `worker_tier = WorkerTier.LAYER1B; cycle_gated = True`
- `regime_worker.py:38-40`: `worker_tier = WorkerTier.LAYER1B; cycle_gated = True`
- `strategy_worker.py:52-54`: `worker_tier = WorkerTier.LAYER1C; cycle_gated = True`
- `scanner_worker.py:57-59`: `worker_tier = WorkerTier.LAYER1D; cycle_gated = True`

### Chain ordering enforcement

`SweetSpotsSettings.__post_init__` at `src/config/settings.py:301-342`
validates strict chain order: every downstream worker's offset (in seconds)
must be `> prev_seconds` else `ConfigError` is raised at startup. The chain
checked: kline → structure → signal → regime → strategy → scanner.
Note: the altdata `1:45` and the strategy `1:30` are not in the strict
chain (altdata is independent), so altdata's `1:45` between regime
(1:15) and scanner (4:00) is intentional.

---

## F3.3 — Cycle gating

### `is_cycle_active()` definition

`src/core/layer_manager.py:1357-1370`:

```python
def is_cycle_active(self) -> bool:
    """Layer 1 restructure Phase 4 — should Layer 1B/1C/1D fire now?

    Today (pre-Phase-8 renumbering) a cycle is active iff both BRAIN
    (toggle 2) and EXECUTION (toggle 3) are intended on. Layer 1A
    always runs regardless. Phase 8 will rewire to toggle 2 alone
    (= ANALYSIS in the new scheme).
    """
    return self._layer_active.get(2, False) and self._layer_active.get(3, False)
```

Therefore:
- LAYER1B / LAYER1C / LAYER1D workers (cycle_gated=True) tick ONLY
  when both Layer 2 (BRAIN) and Layer 3 (EXECUTION) are active.
- LAYER1A workers (cycle_gated=False on the class) ALWAYS run while
  Layer 1 is on.

### How the layer toggle is applied

`SweetSpotWorker.start` at `src/workers/base_worker.py:550-599`:

```python
while self.running:
    try:
        self._last_drift_ms = await self._scheduler.wait_for_sweet_spot()
    ...
    if not self.running:
        break

    if (
        self.cycle_gated and self._layer_manager
        and hasattr(self._layer_manager, "is_cycle_active")
        and not self._layer_manager.is_cycle_active()
    ):
        if self.layer_tier_tag:
            _now_skip = time.monotonic()
            if _now_skip - self._last_skip_info_ts >= _SKIP_INFO_RATE_LIMIT_S:
                self._last_skip_info_ts = _now_skip
                log.info(
                    f"{self.layer_tier_tag}_TICK_SKIP | "
                    f"sub={self.name} reason=cycle_inactive "
                    f"drift_ms={self._last_drift_ms:.0f} "
                    f"rate_limited=true | {ctx()}"
                )
            else:
                log.debug(...)
        continue
```

Same pattern in `BaseWorker.start` at `base_worker.py:231-258` for the
fixed-interval workers. The skip happens AFTER `wait_for_sweet_spot`
returns (so wall-clock anchoring is preserved — no schedule drift)
and BEFORE the tick body. Skipping emits an INFO event rate-limited
to once per 600 s per worker (`_SKIP_INFO_RATE_LIMIT_S` at line 43).

LayerManager handle wiring: `BaseWorker._layer_manager` at line 171 is
late-bound by WorkerManager after instantiation. None when not wired
— gated workers fall through (don't skip) so a wiring oversight
doesn't silently halt all analysis.

### Live verification: with all layers ON, all workers tick

From `data/logs/workers.log` 22:25–23:01 window — every cycle_gated
worker fired its sweet spot AND ticked successfully:

```
22:25:30.001  SWEET_SPOT_FIRED | worker=kline_worker     offset=0:30 fires=7
22:25:51.373  KLINE_TICK_SUMMARY | universe=50 fetched=29997 saved=29997 ... el=21363ms
22:25:47.329  XRAY_TICK_SUMMARY | universe=50 batch=1/2 symbols=25 analyzed=25 ... el=2303ms
22:26:00.021  SWEET_SPOT_FIRED | worker=signal_worker    offset=1:00 fires=7
22:26:03.374  SIG_TICK_SUMMARY | universe=50 signals=50 mean_conf=0.21 el=3352ms
22:26:15.017  SWEET_SPOT_FIRED | worker=regime_worker    offset=1:15 fires=7
22:26:24.807  REGIME_TICK_SUMMARY | universe=50 global=ranging per_coin_size=49 el=9789ms
22:26:30.017  SWEET_SPOT_FIRED | worker=strategy_worker  offset=1:30 fires=7
22:26:39.110  STRAT_CYCLE_DONE | coins=50 signals=10 scored=10 hints=7 ... el=9092ms
22:26:45.018  SWEET_SPOT_FIRED | worker=altdata_worker   offset=1:45 fires=7
22:26:55.156  ALTDATA_TICK_DONE | total_ms=10137 ran=[funding,oi,onchain]
22:29:00.028  SWEET_SPOT_FIRED | worker=scanner_worker   offset=4:00 fires=8
```

The 22:30:00 cycle:
```
22:30:30.000  SWEET_SPOT_FIRED | worker=kline_worker     offset=0:30 fires=8
22:30:45.001  SWEET_SPOT_FIRED | worker=structure_worker offset=0:45 fires=8
22:31:00.001  SWEET_SPOT_FIRED | worker=signal_worker    offset=1:00 fires=8
22:31:15.000  SWEET_SPOT_FIRED | worker=regime_worker    offset=1:15 fires=8
22:31:30.000  SWEET_SPOT_FIRED | worker=strategy_worker  offset=1:30 fires=8
22:31:45.001  SWEET_SPOT_FIRED | worker=altdata_worker   offset=1:45 fires=8
22:34:00.002  SWEET_SPOT_FIRED | worker=scanner_worker   offset=4:00 fires=11
```

Drift values per `SWEET_SPOT_FIRED` line are typically `0–2 ms`, occasionally
`drift_ms=22` / `28` when an upstream tick ran long; never seen >50 ms
in the captured window. The chain is healthy.

OBSERVED ANOMALY: at `2026-04-27 22:53:46.050` to `22:53:40.963`, the
fires counter resets — `worker=scanner_worker offset=4:00 drift_ms=1 fires=1`
at 22:54:00, `worker=kline_worker offset=0:30 drift_ms=0 fires=1` at
22:55:30. The workers reset their `fires` counter to 1 — implies a
process restart at ~22:53. Workers.log between 22:46 and 22:53 has
no `SWEET_SPOT_FIRED` events for any sweet-spot worker (workers.log
shows only price_worker LAYER1A_TICK_DONE entries during that gap),
matching a worker-process restart.

### Layer toggle state — current (snapshot from logs)

The captured workers.log fragments do not include `LAYER_TOGGLE` /
`LAYER_STATE_SYNC` events in the 22:25–23:01 window. The persistent
state file `data/layer_state.json` is referenced at
`src/core/layer_manager.py:28` but its current contents were not
captured in this collection.

NOT FOUND — searched: workers.log captured fragment for
`LAYER_TOGGLE | layer=`, `LAYER_STATE_SYNC | match=`, or
`LAYER_STATE_PERSIST_OK` lines. The fact that all cycle_gated
workers (`structure_worker`, `signal_worker`, `regime_worker`,
`strategy_worker`, `scanner_worker`) tick and produce summary lines
implies `is_cycle_active() == True` at all observed cycles
(if it were False, only the `LAYER1{B,C,D}_TICK_SKIP` line would
appear, not the *_TICK_SUMMARY lines).
