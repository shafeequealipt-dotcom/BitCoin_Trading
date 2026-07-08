# H3 Phase 1 — FUND_INUSE_DRIFT Root Cause + Fix Design

## Evidence

Phase 0 baseline (2026-05-16 07:26 → 12:33 UTC, 5h07m window) captured 307 `FUND_INUSE_DRIFT` events at minute cadence. Sign is negative: `diff = inuse_bybit - inuse_local = -7,301.77` at 07:26 growing to `-17,612.44` by 07:28. Local OVER-counts.

Sample evidence:
```
FUND_INUSE_DRIFT | mode=bybit_demo
  inuse_bybit=82844.34 inuse_local=90146.11 diff=-7301.77 streak=15
FUND_INUSE_DRIFT | mode=bybit_demo
  inuse_bybit=82846.28 inuse_local=100469.35 diff=-17612.44 streak=17
```

## Code paths

### Local in_use (BROKEN)

`src/fund_manager/manager.py:181` (pre-H3):
```python
state.in_use = sum(abs(p.size * p.entry_price) for p in positions)
```

This is the **position notional** in USD (size × entry_price summed across positions). It is NOT the margin required by the exchange.

### Bybit in_use (CANONICAL)

`src/trading/services/account_service.py:54`:
```python
used_margin = float(account.get("totalInitialMargin", "0"))
```

Bybit's `totalInitialMargin` is the sum of initial margin required per open position, which on perpetual futures equals `notional / leverage` per position (with cross-margin adjustments).

The `account_service` already extracts this into `AccountInfo.used_margin`, but the `fund_manager.update_state` does NOT use it. The bug is structural: pre-H3 `fund_manager` re-derives `in_use` from positions with a wrong formula instead of consuming the canonical value already in hand.

## Why the drift grows

When new leveraged positions open during a session, the gap widens. Example: a $450k notional position at 3× leverage has $150k of actual margin. Pre-H3 local accounts for $450k (300% over-count). The drift compounds per open position.

The reconciler at `src/workers/position_reconciler.py:226-228` computes:
- `inuse_bybit = wallet.total_equity - wallet.available_balance`
- `inuse_local = local_total - local_avail` (where `local_avail = max(0, trading_capital - in_use)`)

With the wrong local formula, `local_avail` is too low → `inuse_local` is too high → `diff` is negative and growing.

## Downstream impact

`state.available = max(0, trading_capital - in_use)` (`manager.py:199`) is the capacity input to every sizing decision. When `in_use` is inflated, `available` is clamped to 0 prematurely, blocking new trades. The Sizing Gate 1 at `manager.py:251` rejects sizing requests when `state.available <= 0`. **Aim violation: aggressive opportunity exploitation is blocked by an accounting bug.**

## Fix options considered

| Option | Description | Selected? |
|---|---|---|
| A. Divide by leverage in local formula | `state.in_use = sum(abs(p.size * p.entry_price) / max(1, p.leverage))` | **Backup (fallback when wallet read fails)** |
| B. Use Bybit's `position_value` and `positionIM` per position | Sum per-position margins from `/v5/position/list` | Adds API surface; not needed when wallet already has `totalInitialMargin` |
| C. Source `state.in_use` from `wallet.used_margin` directly | Bybit single source of truth | **PRIMARY (selected)** |
| D. mark_price × size / leverage | Bybit charges margin on mark; option C already captures this | Subsumed by option C |

## Recommended fix (selected)

**Hybrid: Option C primary + Option A fallback.**

In `update_state`:
1. Read `account.used_margin` along with `total_equity` (already in the same response).
2. Compute leverage-aware position-derived value AND naive notional value for diagnostic.
3. Set `state.in_use`:
   - Bybit's `used_margin` when wallet read succeeded (canonical).
   - Leverage-aware position-derived value when wallet read failed (fallback).
   - Stale previous value when neither source is available (defensive).
4. Stash `state.in_use_notional` (naive notional) on `AccountState` for callers that explicitly want raw exposure.
5. Emit `FUND_INUSE_TRANSITION` on every change (delta + source).
6. Emit `FUND_INUSE_RECONCILE` comparing Bybit value vs position-derived value (gap signals orphan-positions / schema drift — a separate concern from H3).

## Heal action (operator's preference)

The operator approved "Prevention + one-shot heal at deploy". With option C:
- **The heal happens automatically on the first `update_state()` tick after deploy** because `state.in_use` is now sourced from Bybit directly. The existing residual ($20k+ pre-fix) evaporates instantly. No separate heal command is required.

## Aim-bias verdict (4 questions)

| Question | Verdict |
|---|---|
| 1. Trade frequency preserved? | **YES — RISES.** Correct `in_use` unclamps `state.available`, freeing real capacity for new trades. |
| 2. Aggression preserved? | **YES — UNLEASHED.** Sizing decisions see the true capital available. |
| 3. Decision speed or quality? | **YES (quality).** Capacity calc matches reality; sizing more accurate. |
| 4. Passive-close advantage preserved? | **YES (no impact).** Close path is unaffected. |

All four YES.

## Verification metrics (24 h soak)

| Metric | Baseline | Target |
|---|---|---|
| `FUND_INUSE_DRIFT` event count | 307 in 5 h (~60/hr) | < 5/hr |
| `|diff|` magnitude | $7-21 k | < $500 |
| New event `FUND_INUSE_TRANSITION` | n/a | fires on every open/close with correct delta |
| New event `FUND_INUSE_RECONCILE` | n/a | fires every tick; `diff_bybit_vs_pos` ≈ 0 unless orphans |
| Existing $21 k drift | growing | ZERO after first update_state() tick (auto-heal) |
| `state.available` correctness | clamped at 0 | matches `wallet.available_balance - reserves` |
| Trade frequency | 4.5/hr | HOLD or RISE |
| DB cascade events | 0 | 0 |
| Shadow path | working | working |

## Hard constraints honored

- No threshold raise to mask the alert.
- No periodic reset / suppression.
- No silent failure path (the fallback explicitly uses the leverage-aware value, not the broken naive formula).
- Defensive against missing `leverage` (max(1, lev) guard).
- Defensive against missing position attrs (`getattr(p, "leverage", 1) or 1`).
- Existing `state.available = max(0, ...)` clamp stays (correctness-preserving).

## Files modified

- `src/fund_manager/manager.py` — `update_state()` rewritten with the priority chain + observability.
- `src/fund_manager/models/fund_types.py` — `AccountState.in_use_notional` new field.

## Tests

`tests/test_h3_fund_inuse_drift.py` — 5 surgical tests:
1. `in_use_takes_bybit_value_when_account_available` — happy path.
2. `in_use_falls_back_to_leverage_aware_sum_when_wallet_fails` — fallback path.
3. `leverage_zero_falls_back_to_one` — defensive on bad data.
4. `empty_positions_zero_in_use` — degenerate case.
5. `available_capacity_correct_with_leveraged_position` — downstream correctness (the aim-relevant behavior).

Plus regression check: existing `tests/test_fund_reconciler.py` 9 tests still pass.
