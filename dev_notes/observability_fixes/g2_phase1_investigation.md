# G2 Phase 1 — Investigation: SNIPER_TICK heartbeat

## Headline finding

The audit's claim is correct. `SNIPER_TICK` literally does not exist as
an event tag in `src/`. The 11 sniper-emitted tags currently in the log
are all **state events** (SNIPER_AGE_GUARD, SNIPER_STRUCT_GUARD_DEFER,
SNIPER_SPIKE, SNIPER_STALL_ESCAPE, etc.) — none of them are guaranteed
per-tick, so silence is currently ambiguous: it could mean "sniper is
alive and quiet" or "sniper is dead".

The fix is to **add a sampled SNIPER_TICK heartbeat** (every 12 ticks
≈ 60 s) that fires regardless of position count. Sampling at 1/min
gives ~60 events/hour — well within the volume budget — while still
producing a detectable signal: if SNIPER_TICK is silent for >2 minutes,
the sniper is hung.

---

## Anatomy of `tick()` (`src/workers/profit_sniper.py:292-805`)

```
L292  async def tick(self) -> None:
L298      self._tick_count += 1                              ← always advances
L301      if transformer.is_switching: return                ← exit A (idle skip)
L305      positions = await self._get_positions()
L306      if positions is None: return                       ← exit B (error skip)
L309-345  position open/close detection + cleanup
L336-345  periodic summary (every 60 ticks, unstructured)    ← current observability
L367-799  per-symbol model loop (M3 work)
L805      set_tid("")                                        ← end of tick
```

### Exit paths

| # | Path | Tick body executes? | Currently logged? |
|---|------|---------------------|---------------------|
| A | Transformer switching | No work | NO event |
| B | _get_positions failed | No work | NO event |
| C | Normal completion | Full work | Only the unstructured "ProfitSniper: tracking..." every 5 min |

### Existing tag inventory (from Phase 0 log analysis)

| Tag | Count | Trigger |
|-----|-------|---------|
| SNIPER_AGE_GUARD | 904 | Position age guard fired |
| SNIPER_STRUCT_GUARD_DEFER | 618 | Structural guard deferred action |
| SNIPER_DEVELOPMENT_GUARD | 441 | Development-mode guard |
| SNIPER_SPIKE | 183 | Spike detected |
| SNIPER_GRACE_BLOCKED | 164 | Grace period block |
| SNIPER_PROFIT_GUARD | 74 | Profit guard |
| SNIPER_CAP | 38 | Cap enforced |
| SNIPER_RATE_LIMIT_AWARE_SKIP | 23 | Rate-limit short-circuit |
| SNIPER_STALL_ESCAPE | 7 | Stall escape escalation |
| SNIPER_TOO_CLOSE | 4 | SL too close to entry |
| SNIPER_TRAIL_FLOOR_CLAMP | 2 | Trail floor clamp |
| **SNIPER_TICK** | **0** | (the gap) |

All existing events are **conditional state emissions** — none fire on
every tick. A sniper that hangs after entering tick() but before any
state event triggers would produce identical log silence to a sniper
that is alive but operating normally on no positions.

### Cadence verification

- `tick()` is scheduled from `BaseWorker` with interval from
  `settings.mode4.check_interval_seconds = 5` (verified via grep).
- 5 s ticks → 720 ticks/hour → 1,080 ticks per 1.5 h window.

The audit estimate was ~1,000 ticks per window. Verified.

---

## Worker cluster sweep (Prompt Part D, Cluster B)

| Worker | Heartbeat tag | Cadence | Style |
|--------|---------------|---------|-------|
| scanner_worker | `SCANNER_TICK_SUMMARY` | per tick (~10s) | INFO, includes el_ms |
| position_watchdog | `WD_TICK` / `WD_TICK_DONE` | per tick (~5s) | INFO, structured |
| worker_liveness_watchdog | `WORKER_LIVENESS_HEARTBEAT` | every minute | INFO, structured |
| profit_sniper | **(missing)** | — | — |
| altdata_worker | `ALTDATA_FG_TICK`, `ALTDATA_FUNDING_TICK`, `ALTDATA_OI_TICK`, `ALTDATA_TICK_DONE` | per feed | INFO, structured |
| kline_worker | various K* tags | per fetch | mixed |
| regime_worker | `REGIME` | per cycle | INFO |

### Additional cluster gaps to surface (G12+ candidates)

1. **KLINE_WORKER_TICK** — kline worker has no explicit heartbeat event.
2. **REGIME_WORKER_TICK** — regime worker emits the REGIME state event but
   no heartbeat marker.
3. **CYCLE_TRACKER_TICK** — visible only via CYCLE_RESUME / CYCLE_RESUME_WAIT.

These match the audit's "Cluster B — Workers" investigation list. Each
becomes its own gap (G12+) after operator review.

---

## Schema proposal

### Tag name: `SNIPER_TICK`

Matches the audit's expected tag (no naming-convention conflict — the
suffix `_TICK` is consistent with `WD_TICK`, `ALTDATA_FG_TICK`,
`SWEET_SPOT_FIRED` and the heartbeat cluster).

### Sampling: every 12 ticks (~60 s)

- 1,080 ticks/1.5h → 90 SNIPER_TICK events/1.5h = **60 events/hour**
- Well within the volume budget (Phase 0 cap = +30% of baseline)
- Operator stall detection: silence > 2 min ⇒ alarm

### Field set

| Field | Source | Purpose |
|-------|--------|---------|
| `tick=` | `self._tick_count` | Monotonic counter — operators see progress |
| `el=` | `(time.time() - _tick_start) * 1000` | Latency distribution measurable |
| `n=` | `len(self._tracked)` | Position count |
| `syms=` | first 5 of `self._tracked.keys()` | Small sample, deterministic |
| `mode=` | `getattr(self.transformer, "current_mode", "?")` | Active exchange mode |
| `tick_count_total=` | implicit via `tick=` | sanity |

Optional: keep the existing 5-min `ProfitSniper: tracking ...`
unstructured emission untouched (preserves prior log narrative).

### Log level: INFO

Standard for heartbeats; matches WD_TICK and SCANNER_TICK_SUMMARY.

### Emission strategy: try/finally

The tick has two early-exit paths (transformer switching, position
fetch failure). To keep the heartbeat detectable on those paths too,
the sample-and-emit lives in a `try/finally` that runs regardless of
exit path. The sampling check (`self._tick_count % 12 == 0`) stays
the gate — early-exit ticks still increment `_tick_count` and so still
contribute to the sample cadence.

```python
async def tick(self) -> None:
    self._tick_count += 1
    _tick_start = time.time()
    try:
        # ... existing tick body unchanged ...
    finally:
        if self._tick_count % 12 == 0:
            _tick_el = (time.time() - _tick_start) * 1000
            _syms = list(self._tracked.keys())
            _syms_str = ",".join(_syms[:5])
            _more = f"+{len(_syms) - 5}" if len(_syms) > 5 else ""
            _mode = getattr(self.transformer, "current_mode", "?") if self.transformer else "?"
            log.info(
                f"SNIPER_TICK | tick={self._tick_count} el={_tick_el:.0f}ms "
                f"n={len(_syms)} syms=[{_syms_str}{_more}] mode={_mode} | {ctx()}"
            )
```

### Behaviour preserved

- Tick body unchanged
- Per-symbol tid scoping unchanged
- Early-exit returns unchanged (transformer-switch, get_positions failure)
- The new emission only runs on the sample tick — adds ~50 µs of
  latency on those ticks (well under hot-path budget)

### Shadow parity

ProfitSniper is exchange-agnostic — it tracks positions exposed by the
transformer/position service, not directly by the exchange. So a single
emission covers both Shadow and Bybit-demo paths.

---

## Synthesis

**WHERE:** `src/workers/profit_sniper.py:292-805` — `tick()` body wrapped in try/finally.

**WHAT:** new `SNIPER_TICK` event at INFO level sampled every 12 ticks
(~60 s) emitting tick counter, tick latency, position count, symbol
sample, transformer mode.

**WHY:** the audit's concern is liveness detectability. Currently a
hung sniper produces the same log silence as a healthy idle sniper.
The sampled tick gives operators an unambiguous "sniper is alive"
signal at 1/minute resolution.

**Test plan:**
- Unit test (no exchange): instantiate ProfitSniper, run 12 ticks, assert
  SNIPER_TICK fires exactly once with correct field shape.
- Unit test: run 24 ticks, assert exactly 2 SNIPER_TICK emissions.
- Unit test: tick 12 with `transformer.is_switching = True` — heartbeat
  still emits (liveness preserved across the idle skip).
- Unit test: tick 12 with `_get_positions` returning None — heartbeat
  still emits.

**Volume impact:** +60 events/hour. Phase 0 budget allows +30% of
baseline ≈ +6,000 events/h. Negligible.

---

## Phase 2 decisions

| # | Decision | Reasoning |
|---|----------|-----------|
| 1 | Tag name `SNIPER_TICK` | Matches audit; matches `WD_TICK`/`ALTDATA_*_TICK` cluster convention |
| 2 | Sample every 12 ticks (~60 s) | 1/min cadence balances liveness signal vs volume |
| 3 | Wrap tick body in try/finally | Heartbeat fires on every exit path including the two early-exit returns |
| 4 | Keep existing unstructured "ProfitSniper: tracking..." emission | Preserves prior log narrative; no behavior change |
| 5 | Fields: tick, el, n, syms (truncated), mode | Per audit-required list; symbol truncation prevents large positions from bloating the line |
| 6 | INFO level | Heartbeat default in this codebase |

Phase 3 implementation proceeds on this branch (`obs/g2-sniper-tick`).
