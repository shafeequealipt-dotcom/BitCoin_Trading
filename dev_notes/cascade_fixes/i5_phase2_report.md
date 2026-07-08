# Issue 5 — Phase 2 Operator Discussion Report

## Summary

`Layer4ProtectionService` is constructed at `manager.py:1323` while `regime_detector` is built later at `manager.py:1469`. So L4 captures `regime_detector=None` at construction. Watchdog/VolatilityProfiler/Scanner all have late-wire blocks at `manager.py:1480-1493` to attach `regime_detector` after it exists — L4 has no equivalent late-wire.

Result: L4's `compute_structural_invalidation` returns `(False, "no_data:services_unwired")` perpetually. The time-decay calculator treats this as "structure intact" and BLOCKS every loser-lane force-close.

Phase 0 baseline confirmed 130 `services_unwired` events in a 2-hour window, with a perfect 1:1 match against 130 `TIME_DECAY_STRUCT_GUARD blocked=true` events. The safety check is silently disabled.

## Evidence

### Boot order (current code refs)

- L4 constructed: `manager.py:1323-1329` (regime_detector arg = `None`)
- RegimeDetector built: `manager.py:1469-1470`
- Watchdog late-wire (works): `manager.py:1480-1483`
- Profiler late-wire (works): `manager.py:1485-1488`
- Scanner late-wire (works): `manager.py:1490-1493`
- L4 late-wire: **does not exist**

### Gate (verbatim from current code, `layer4_protection.py:213-243`)

```python
def compute_structural_invalidation(self, *, symbol, side, state):
    td_cfg = self._time_decay.cfg if self._time_decay is not None else None
    if td_cfg is None:
        return (False, "no_data:no_calculator_cfg")
    if self.structure_cache is None or self.regime_detector is None:
        return (False, "no_data:services_unwired")     # ← perpetual
```

### Consumer impact (`time_decay_sl.py:397-412`)

When the gate returns `(False, "no_data:services_unwired")`:
- `TIME_DECAY_STRUCT_GUARD ... blocked=true` warning fires
- Force-close decision suppressed (returns `None` instead of `-1.0`)
- Positions stay open past their structural validity window

Sample (current `workers.log`):
```
TIME_DECAY_STRUCT_GUARD | sym=LINKUSDT p_win=0.098 pnl=-0.60% mae=-0.61%
  entry_xray=0.70 entry_setup=bullish_fvg_ob entry_regime=ranging
  reason='no_data:services_unwired' blocked=true
```

`p_win=0.098` is well below the 0.25 force-close threshold; the position should have been force-closed for structural reasons.

## Solution chosen

**Option A (late-wire after regime construction, recommended)**:

Add immediately after the scanner late-wire at `manager.py:1493`:

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

`structure_cache` is re-attached idempotently in case future reorderings break the original construction-time wiring.

## Trade-offs

### Pros
- Smallest possible change (8 lines including comment)
- Matches the established late-wire convention (watchdog, profiler, scanner)
- L4 attribute is settable (no internal capture-by-value)
- One log line per startup for observability — visible in workers.log
- No schema or DB changes
- Reversible via `git revert`

### Cons / Risks
- **Behavioral change**: gate restoration may release a backlog of force-closes for positions that were silently held past structural validity. Operator should observe the first 1-2 hours of force-closes after deploy.
- This is the RESTORATION of an intended safety check, not a new restriction — aim-preservation is satisfied.

### Alternatives considered

- **Option B (restructure boot order)**: move L4 construction after RegimeDetector. Larger change. Risks: ProfitSniper construction (line 1351) depends on `services.get("layer4_protection")` and would need to move too. Subtle init-order regressions possible.
- **Option C (lazy lookup)**: have L4 read `services.get("regime_detector")` on every call. Most architecturally pure but requires passing the service container into L4 constructor. Bigger blast radius.

## Verification plan

After deploy:
1. New `L4_LATE_WIRE | regime_detector=ok structure_cache=ok` line in workers.log on next worker manager startup
2. `services_unwired` count: drops to 0 within first hour of operation
3. `TIME_DECAY_STRUCT_GUARD blocked=true reason='no_data:services_unwired'` events: drop to 0
4. New `TIME_DECAY_STRUCT_GUARD blocked=true reason='no_data:no_entry_anchor'` events may appear (different gate branch — anchor missing rather than service unwired) — that's fine, expected for positions opened pre-fix
5. New `TIME_DECAY_STRUCT_GUARD blocked=true reason='structurally_intact'` events may appear — also fine, this is the gate doing its real job
6. Some positions may force-close that previously stayed open. Operator inspects first 1-2 hours and confirms appropriateness.
7. Shadow mode unaffected: switch mode=shadow, observe one cycle, late-wire still fires (shadow uses the same boot path)

Tests:
- 4 tests: pre-fix `services_unwired` reproducer (regime path), pre-fix `services_unwired` reproducer (cache path), post-fix gate proceeds past services_unwired branch, source-level pin in manager.py.
