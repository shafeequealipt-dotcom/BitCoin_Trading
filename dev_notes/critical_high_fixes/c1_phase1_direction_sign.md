# CRITICAL-1 Phase 1 — Direction-Sign Analysis

## Purpose

Identify the canonical source of trade direction (`side`) in the coordinator's record, the value enumeration in use, and the formula for converting `(entry, exit, side)` into a signed `pnl_pct`.

## Side enum (src/core/types.py:17-21)

```python
class Side(str, Enum):
    """Order/position side."""
    BUY = "Buy"
    SELL = "Sell"
```

Two values only. The string values are "Buy" and "Sell". The class inherits from `str` so direct string comparison works.

## How state.side gets populated

`TradeCoordinator.register_trade` (line 221-263) accepts `side: str = ""`. Callers:

| Caller | File:line | Pattern |
|---|---|---|
| BrainV2 | `src/brain/brain_v2.py:420` | `side=sig.direction` (where `sig.direction` is a `Side` enum) |
| BrainV2 | `src/brain/brain_v2.py:455` | `side=sig.direction.value` (the string "Buy" or "Sell") |
| BrainV2 | `src/brain/brain_v2.py:489` | `side=sig.direction` |
| BrainV2 | `src/brain/brain_v2.py:531` | `side=sig.direction.value` |
| StrategyWorker | `src/workers/strategy_worker.py:2272` | `coordinator.register_trade(symbol=symbol, ...)` (side passed via TradePlan flow) |

Mixed pattern: some callers pass the enum (which `dataclass` stores as the enum object), others pass `.value` (a string). Because `Side` inherits from `str`, both forms compare equal to the string literals "Buy" and "Sell".

## Value coverage at the coordinator

The coordinator's existing close_price back-derive at `trade_coordinator.py:690` checks:

```python
if _side in ("Sell", "Short"):
    close_price = entry_price * (1 - pnl_pct / 100)
else:
    close_price = entry_price * (1 + pnl_pct / 100)
```

Two observations:

1. **"Sell" matches the canonical Side.SELL value.** The string "Short" is a defensive alias from an earlier code era — not produced by any current caller, but the check is harmless.
2. **"Buy" / "Long" / anything else falls into the `else` branch** (treated as long). This is correct for "Buy" (canonical) and "Long" (legacy alias), but would also incorrectly treat any future enum value as long. Acceptable given Side has only two values.

The CRITICAL-1 fix should mirror this exact convention to stay consistent with the existing back-derive of close_price.

## Canonical PnL formula

From `bybit_demo_adapter.close_position:392-401` (the trusted inline computation):

```python
pnl_price_delta = (exit_price - pos.entry_price) * pos.size
if pos.side == Side.SELL:
    pnl_price_delta = -pnl_price_delta

pnl_pct_val = (
    ((exit_price - pos.entry_price) / pos.entry_price) * 100.0
    if pos.entry_price > 0
    else 0.0
)
if pos.side == Side.SELL:
    pnl_pct_val = -pnl_pct_val
```

For pnl_pct only (the CRITICAL-1 target):

| Direction | Formula | Mnemonic |
|---|---|---|
| Buy / Long | `((exit - entry) / entry) * 100` | Up = profit |
| Sell / Short | `((entry - exit) / entry) * 100` (equivalent to negating the Buy formula) | Down = profit |

Identity: the Sell formula equals the negative of the Buy formula. Both rely on `entry > 0` to avoid division by zero.

## Available data at the coordinator's fix point

When the back-derive runs (between line 693 and line 696 in `on_trade_closed`):

| Variable | Value | Source |
|---|---|---|
| `entry_price` | `state.entry_price` (resolved at line 681) | TradeState.entry_price set at register_trade |
| `close_price` | `exit_price` from caller (set at line 687-688) | WS subscriber's `execPrice` (authoritative Bybit fill) |
| `_side` | `state.side` (resolved at line 682) | One of "Buy" or "Sell" |

All three are reliably present. The fix point therefore has the data it needs.

## Edge cases for the fix

| Case | Behaviour |
|---|---|
| `entry_price == 0` (rare; opens with zero entry should be impossible) | Skip back-derive; pnl_pct stays 0; record continues |
| `close_price == 0` (rare; should not happen given subscriber always passes WS execPrice) | Skip back-derive; pnl_pct stays 0; record continues |
| `entry_price == close_price` (flat trade) | pnl_pct computes to exactly 0.0; this is correct, not a bug |
| `_side == ""` (state somehow created without side) | Treated as Buy by the existing fallback. Inherited risk; not a CRITICAL-1 regression |
| `pnl_pct != 0` already passed by caller (system-initiated edge case) | Skip back-derive (gate on `pnl_pct == 0`); preserve caller's value |

The flat-trade case is benign: when `entry_price == close_price`, the formula gives 0.0 and DL_TRADE_SUSPECT does NOT fire (the guard at `data_lake.py:93` requires `entry_price != exit_price`). Confirmed via the KATUSDT example in the data sample (entry = exit = 0.01031 → pnl=0 in both trade_log and trade_history).

## Minimum viable back-derive snippet

```python
# CRITICAL-1 fix (Phase 2 option A) — back-derive pnl_pct when caller
# passed sentinel zero. Direction sign matches close_position inline
# (bybit_demo_adapter.py:392-401) and existing close_price back-derive
# (lines 689-693).
if pnl_pct == 0 and entry_price > 0 and close_price > 0:
    if _side in ("Sell", "Short"):
        pnl_pct = ((entry_price - close_price) / entry_price) * 100
    else:
        pnl_pct = ((close_price - entry_price) / entry_price) * 100
    was_win = pnl_pct > 0
```

Insert location: immediately after line 693 (the existing close_price back-derive's else clause closes), before line 696 (the existing pnl_usd back-derive). The pnl_usd back-derive then runs normally and produces a non-zero value, since its gate `pnl_pct != 0` is now satisfied.

## Findings

1. The Side enum is binary ("Buy" / "Sell"). The "Short" defensive alias in coordinator's code is harmless legacy.
2. State.side is reliably populated by all current callers (mix of enum-pass and value-pass patterns; both work with str-based Enum equality).
3. The fix-point variables (`entry_price`, `close_price`, `_side`) are all resolved by line 693, ready for use.
4. The canonical formula matches the existing close_price back-derive's direction handling exactly, so the new pnl_pct branch can use the same `("Sell", "Short")` membership test.
5. The flat-trade edge (entry == exit) computes pnl_pct=0.0 naturally and DL_TRADE_SUSPECT does not fire on these. No special case needed.
