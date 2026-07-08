# Issue 5 — Phase 1 Investigation Synthesis

## What the report said

`PHASE5_LIVE_MONITORING_REPORT.md` (Finding A5):

> ``Layer4ProtectionService`` is constructed at ``manager.py:1323`` with
> the parameter ``regime_detector=services.get("regime_detector")``.
> But ``regime_detector`` is built later in the boot sequence (around
> ``manager.py:1380+``). So at construction time, ``services.get(
> "regime_detector")`` returns None, and L4 captures None.
>
> The watchdog has a late-wire at ``manager.py:1480-1483`` that
> attaches ``regime_detector`` after construction. But there's no
> analogous late-wire for ``Layer4ProtectionService``. So L4's
> ``regime_detector`` and ``structure_cache`` stay None for the entire
> process lifetime.

## What current code shows

### Boot sequence (verified at current refs)

| Line | Component | Action |
|-----:|-----------|--------|
| 217-223 | `StructureCache` | Created and registered in `services["structure_cache"]` |
| 1295-1304 | `PositionWatchdog` | Constructed with `regime_detector=services.get("regime_detector")` (returns None) |
| 1323-1329 | `Layer4ProtectionService` | Constructed with `regime_detector=services.get("regime_detector")` (returns **None**) and `structure_cache=services.get("structure_cache")` (returns the cache — already exists) |
| 1330 | Service registration | `services["layer4_protection"] = layer4_protection` |
| 1331-1333 | Watchdog adopts L4 | `_watchdog_for_calc.layer4_protection = layer4_protection` |
| 1351-1373 | `ProfitSniper` | Constructed with `layer4_protection=services.get("layer4_protection")` — receives the broken-regime instance |
| 1469-1470 | `RegimeDetector` | **Finally created**: `detector = RegimeDetector(s, ta, market_repo); services["regime_detector"] = detector` |
| 1480-1483 | Watchdog late-wire | `_wd.regime_detector = detector` (works) |
| 1485-1488 | VolatilityProfiler late-wire | `_vp._regime_detector = detector` (works) |
| 1490-1493 | Scanner late-wire | `_scanner.regime_detector = detector` (works) |
| **MISSING** | **L4 late-wire** | **Does not exist** — L4 stays unwired |

### Gate behavior — `src/risk/layer4_protection.py:213-243`

```python
def compute_structural_invalidation(
    self, *, symbol: str, side: str, state: TimeDecayState,
) -> tuple[bool, str]:
    td_cfg = self._time_decay.cfg if self._time_decay is not None else None
    if td_cfg is None:
        return (False, "no_data:no_calculator_cfg")
    if self.structure_cache is None or self.regime_detector is None:
        return (False, "no_data:services_unwired")  # ← perpetual block
    ...
```

### Consumer behavior — `src/risk/time_decay_sl.py:397-412`

When the gate returns `(False, "no_data:services_unwired")`:
- The `structural_invalidation` flag stays `False`
- The structural-invalidation guard (line 397) condition fires
- `TIME_DECAY_STRUCT_GUARD` warning logs `blocked=true`
- `calculate()` returns `None` instead of `-1.0` (force-close sentinel)
- Watchdog's force-close decision is suppressed
- Position stays open past its time-decay structural invalidation window

### Phase 0 baseline evidence

```
WD_TICK_SLOW                : 14   (per 2h)
services_unwired            : 130  (per 2h)
TIME_DECAY_STRUCT_GUARD     : 130  (per 2h)
```

The 130:130 perfect match between `services_unwired` and `TIME_DECAY_STRUCT_GUARD` confirms every time-decay structural check is being silently blocked because L4 cannot compute invalidation.

Sample log line:
```
TIME_DECAY_STRUCT_GUARD | sym=LINKUSDT p_win=0.098 pnl=-0.60% mae=-0.61%
  entry_xray=0.70 entry_setup=bullish_fvg_ob entry_regime=ranging
  reason='no_data:services_unwired' blocked=true
```

`p_win=0.098` is well under the typical 0.25 force-close threshold; the position should have been force-closed for structural reasons, but the gate silently blocked the close.

### Watchdog has the same logic but works

`src/workers/position_watchdog.py:935-985` has a duplicate (deprecated, kept as fallback) `_compute_structural_invalidation`. The watchdog itself works correctly because:
1. Its own `regime_detector` IS late-wired (line 1480-1483)
2. Its primary path now calls `self.layer4_protection.compute_structural_invalidation` (line 1266-1268), which IS broken because L4's `regime_detector` is None
3. So the watchdog's L4 calls also return `services_unwired` — the duplicate code in the watchdog is dead code in the current branch

## Recommended fix point

Add late-wire block right after the existing scanner late-wire:

```python
_l4 = self._services.get("layer4_protection")
if _l4:
    _l4.regime_detector = detector
    _l4.structure_cache = self._services.get("structure_cache")
    log.info(
        f"L4_LATE_WIRE | "
        f"regime_detector={'ok' if _l4.regime_detector else 'MISSING'} "
        f"structure_cache={'ok' if _l4.structure_cache else 'MISSING'} "
        f"| {ctx()}"
    )
```

Re-attaching `structure_cache` is idempotent (it was already passed correctly at construction) but defensive against future reorderings.

## Estimated impact

- `services_unwired` count: 130 → 0
- `TIME_DECAY_STRUCT_GUARD blocked=true reason=no_data:services_unwired` events: 130 → 0
- Time-decay calculator can now return `-1.0` (force-close) when structural invalidation fires
- **Behavioral change for operators to monitor:** previously-silently-held losing positions may now force-close. Operator should observe the first 1-2 hours of force-closes after deploy and confirm they look appropriate. The aim-preservation rule (no fixes that block trades) is preserved — this fix RESTORES the safety check that was already supposed to be active, it does not add new blocking.
- Shadow mode unaffected (file is mode-agnostic; structure cache and regime detector are wired the same way in both modes)
- One additional log line per worker-manager startup (`L4_LATE_WIRE`)
