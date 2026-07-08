# Issue 3 — Phase 2 — Design + Aim-Bias Report

Date: 2026-05-18.

## Design Summary

Single in-memory dict `_reentry_cooldown: dict[tuple[str, str], float]` on `TradeCoordinator` mapping `(symbol, direction)` to monotonic expiry timestamp. Set on every close, checked per (symbol, direction) at the gate, lazily cleaned on read, periodically swept once per gate check to bound memory.

### State

```python
# In TradeCoordinator.__init__
self._reentry_cooldown: dict[tuple[str, str], float] = {}
```

Direction values are canonicalised to `"Buy"` / `"Sell"` on write; reads normalise `long`/`short` to the same namespace so legacy callers do not silently miss. Unknown/empty direction falls through to a no-op (a malformed close cannot leave a stale block).

### API

```python
def is_reentry_blocked(self, symbol: str, direction: str) -> tuple[bool, int]:
    """Return (blocked, remaining_seconds). Lazy-cleans expired entries."""

def clear_expired_reentry_cooldowns(self) -> int:
    """Periodic sweep — drops entries whose monotonic expiry has passed.
    Returns count cleared. Idempotent."""

def get_active_reentry_cooldowns(self) -> list[tuple[str, str, int]]:
    """Snapshot for brain-prompt consumers. Returns (symbol, direction, remaining_s) for active entries only."""
```

### Set on close

`on_trade_closed` adds:

```python
_dir = _side  # canonicalised earlier in the method
if _dir:
    expiry = time.monotonic() + self._reentry_cooldown_seconds
    self._reentry_cooldown[(symbol, _dir)] = expiry
    log.info(
        f"REENTRY_COOLDOWN_5MIN_SET | sym={symbol} dir={_dir} "
        f"cooldown_sec={self._reentry_cooldown_seconds} | {ctx()}"
    )
```

The 180/600/900 branch is deleted entirely. Uniform 300s for every close (win or loss).

### Lazy cleanup + cleared event

`is_reentry_blocked` pops expired entries on access and emits `REENTRY_COOLDOWN_5MIN_CLEARED` exactly once per entry. `clear_expired_reentry_cooldowns()` runs the same sweep across all entries (called by the gate once per check before the per-(symbol,direction) check) — bounds the dict to active cooldowns only and ensures `REENTRY_COOLDOWN_5MIN_CLEARED` fires for entries that nobody ever queries again.

### Gate

Single CHECK 6 replaces the old CHECK 6 + CHECK 6b:

```python
try:
    coordinator = self._services.get("trade_coordinator")
    if coordinator is not None and hasattr(coordinator, "is_reentry_blocked"):
        coordinator.clear_expired_reentry_cooldowns()
        _new_dir = str(trade.get("direction", "") or "")
        blocked, remaining = coordinator.is_reentry_blocked(symbol, _new_dir)
        if blocked:
            reason = f"reentry_cooldown_5min_{remaining}s"
            trade["_gate_rejected"] = reason
            modifications.append(f"REJECTED:{reason}")
            log.warning(
                f"REENTRY_COOLDOWN_5MIN_BLOCKED | layer=gate sym={symbol} "
                f"dir={_new_dir} remaining_s={remaining} | {ctx()}"
            )
except Exception as e:
    log.warning(f"GATE_REENTRY_COOLDOWN_CHECK | sym={symbol} err='{str(e)[:60]}' | {ctx()}")
```

### Brain prompt

Both blocks at `strategist.py:1597-1610` and `4084-4094` rewritten to call `coordinator.get_active_reentry_cooldowns()`:

```python
coordinator = self.services.get("trade_coordinator")
if coordinator and hasattr(coordinator, "get_active_reentry_cooldowns"):
    pairs = coordinator.get_active_reentry_cooldowns()
    if pairs:
        sections.append("\nRECENTLY CLOSED (wait for cooldown before re-entering):")
        for sym, direction, remaining in pairs:
            sections.append(f"  {sym} {direction}: {remaining}s remaining")
        sections.append("")
```

The header text stays identical so existing assertions and brain-prompt parsers do not break on the section name.

### Settings

`APEXSettings`:
- ADD `reentry_cooldown_seconds: int = 300` (in the slot occupied by the deleted reentry_learning_gate block).
- REMOVE `reentry_learning_gate_enabled`, `_lookback_hours`, `_min_loss_usd`, `_price_drift_pct` + their docstring block.

LEAVE `loss_cooldown_seconds: int = 300` at settings.py:809 — separate concept consumed by drawdown.py (out of scope per the prompt §opening).

## Five Aim-Bias Answers

1. **Trade frequency?** HIGHER. The session log shows 19 reentry_learning_gate blocks + 16 from the operator's recount; replacing complex matching with a deterministic 5-min window means brain proposals after 5 min go through unconditionally. Estimate: cuts reentry-class blocks by 50-80%.
2. **Aggression?** PRESERVED. 5 min is shorter than the current 600s loss cooldown (10 min) and 900s hard-stop cooldown (15 min) — system gets MORE aggressive after losing closes than before.
3. **Decision quality?** PREDICTABLE. Brain knows the rule precisely; no longer surprised by `same_conditions` blocks based on opaque regime/setup matching. The brain prompt also surfaces the active per-direction cooldowns so brain can plan around them rather than wasting CALL_A on doomed proposals.
4. **Passive-close advantage?** N/A. Entry-side gate; close paths untouched.
5. **Layer separation?** YES. State stays in coordinator; check stays in gate; brain prompt stays a read-only consumer. New API surface (`is_reentry_blocked`, `clear_expired_reentry_cooldowns`, `get_active_reentry_cooldowns`) is internally consistent and replaces multiple ad-hoc helpers (cleaner than before).

## Forbidden Anti-Patterns Explicitly Avoided (§C Rule 3)

- Old logic REMOVED, not co-existing.
- Not a config tweak — code paths are deleted.
- Not a rename without removal.
- Not per-symbol configurable (uniform 300s).
- Not per-direction-different durations (uniform).
- Not implementing only the new gate while leaving the old — single replacement commit (issue3/p3-3) removes the legacy in one shot after the new path is in place.

## Trial Scenarios (Phase 4 verification, mapped to prompt §D Step 3.5)

### Scenario 1 — Block at T+3min

1. Open AVAXUSDT Sell at T-100. Close at T0 (any reason).
2. Expect `REENTRY_COOLDOWN_5MIN_SET sym=AVAXUSDT dir=Sell cooldown_sec=300` at T0.
3. Attempt new AVAXUSDT Sell entry at T+180.
4. Expect gate `REENTRY_COOLDOWN_5MIN_BLOCKED sym=AVAXUSDT dir=Sell remaining_s=~120`.
5. `trade["_gate_rejected"] == "reentry_cooldown_5min_~120s"`.

### Scenario 2 — Allow at T+301

1. Same setup as Scenario 1.
2. Attempt AVAXUSDT Sell at T+301.
3. Expect gate to NOT set `_gate_rejected` from the cooldown check (other checks may still reject for unrelated reasons).
4. The implicit `REENTRY_COOLDOWN_5MIN_CLEARED` event has fired at some point between T+300 and T+301 (either via lazy cleanup on the gate check or via the periodic sweep).

### Scenario 3 — Opposite direction allowed

1. Close AVAXUSDT Sell at T0.
2. Attempt AVAXUSDT Buy at T+60.
3. Expect gate to NOT reject from cooldown — `(AVAXUSDT, Buy)` is not in `_reentry_cooldown` (only `(AVAXUSDT, Sell)` is).

### Scenario 4 — Re-arm on re-close

1. Close AVAXUSDT Sell at T0 (300s window opens).
2. Re-enter via opposite/different gate (e.g., Scenario 3 path) at T+60. New Buy position opens.
3. Close that Buy at T+180.
4. Now BOTH `(AVAXUSDT, Sell)` cleared/active and `(AVAXUSDT, Buy)` newly active at T+180+300=T+480.
5. Sell attempt at T+200 → still blocked (Sell timer was T0+300=T+300, not yet expired).
6. Sell attempt at T+301 → allowed (Sell timer expired).
7. Buy attempt at T+200 → blocked (Buy timer started at T+180, expires T+480).
8. Buy attempt at T+481 → allowed.

## Approval

Design is the literal operator intent from `IMPLEMENT_THREE_ISSUES_FIX.md` Issue 3 with the operator-confirmed per-direction state-shape and brain-visibility decisions captured in this session. No additional operator review needed before Phase 3.
