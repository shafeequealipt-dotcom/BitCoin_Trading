# CRITICAL-1 Phase 1 — Coordinator Anatomy

## Purpose

Document `src/core/trade_coordinator.py` `on_trade_closed` end-to-end (lines 639-794). Identify every branch and the exact gates blocking pnl_pct back-derivation.

## TradeState (lines 25-78)

The dataclass populated at trade open and consumed at close. Relevant fields:

- `opened_at: float = 0.0` — epoch seconds, set at register_trade time
- `opened_at_dt: datetime` — already an ISO-friendly datetime (line 33)
- `entry_price: float = 0.0`
- `side: str = ""` — values "Buy", "Sell", "Long", "Short" appear elsewhere in the codebase
- `size: float = 0.0`
- `order_id: str = ""` — exchange order id
- Plus 18 TIAS / APEX entry-context fields forwarded into the close record

## on_trade_closed signature (line 639-665)

```python
def on_trade_closed(
    self,
    symbol: str,
    pnl_pct: float,
    pnl_usd: float,
    was_win: bool,
    closed_by: str = "watchdog",
    exit_price: float | None = None,
    price_source: str | None = None,
) -> None:
```

Synchronous (despite the WS subscriber awaiting it — the await schedules on the event loop, not because it returns awaitable).

## Function flow (lines 666-794)

| Lines | What happens |
|---|---|
| 666 | `state = self._trades.pop(symbol, None)` — atomic pop (L2 dedup) |
| 670-675 | If `state is None`: log `COORD_DOUBLE_CLOSE` and return (race guard) |
| 677 | `hold_seconds = time.time() - state.opened_at` — used in record |
| 681-682 | Resolve `entry_price` and `_side` from state (with defensive defaults) |
| 686-693 | Resolve `close_price`: prefer caller-supplied `exit_price`; fallback back-derive from `pnl_pct` (the elif at 689 is **dead code** when subscriber always passes `exit_price`) |
| 696-707 | **Back-derive `pnl_usd`** ONLY if `pnl_usd == 0 AND pnl_pct != 0 AND entry_price > 0` |
| 709-711 | Resolve `_trade_id` |
| 713-760 | Build `record` dict with 30+ fields. **Includes `closed_at` (ISO string from datetime.now). DOES NOT include `opened_at`.** |
| 762-764 | Append to `_closed_trades` history (capped at 100) |
| 766-770 | Log `COORD_CLOSE_START` |
| 772-774 | Pop per-symbol caches (`_last_brain_context`, `_trade_plans`, `_trade_info`) |
| 776-784 | Fire each registered close callback in registration order; log `COORD_CB_OK` or `COORD_CB_FAIL` per callback |
| 786-794 | Set per-symbol cooldown (180s win, 600s normal loss, 900s hard stop) |

## The bug: back-derive gates (lines 685-707)

```python
# Line 686-693 — close_price resolution
close_price = 0.0
if exit_price is not None and exit_price > 0:
    close_price = float(exit_price)                   # ← always hit (subscriber passes exit_price)
elif entry_price > 0 and pnl_pct != 0:                # ← never hit (dead branch given above)
    if _side in ("Sell", "Short"):
        close_price = entry_price * (1 - pnl_pct / 100)
    else:
        close_price = entry_price * (1 + pnl_pct / 100)

# Line 696-707 — pnl_usd back-derive
if pnl_usd == 0 and pnl_pct != 0 and entry_price > 0:   # ← gate: pnl_pct != 0
    _size = getattr(state, "size", 0) if state else 0
    if _size > 0:
        pnl_usd = pnl_pct / 100 * abs(_size * entry_price)
    else:
        # Fallback: compute notional from _trade_info (amount_usd * leverage)
        info = self._trade_info.get(symbol, {})
        amount_usd = info.get("amount_usd", 0)
        leverage = info.get("leverage", 1)
        if amount_usd > 0:
            notional = amount_usd * leverage
            pnl_usd = pnl_pct / 100 * notional
```

Both back-derive branches require `pnl_pct != 0`. The subscriber always passes `pnl_pct=0`. So:

1. `close_price` IS set correctly (via the first if branch, lines 687-688) because the subscriber DOES pass `exit_price > 0`.
2. `pnl_usd` IS NOT back-derived (the gate at line 696 fails because `pnl_pct == 0`).
3. **`pnl_pct` itself is never back-derived anywhere in this function.** No branch computes it from `entry_price + close_price + side`. The audit's diagnosis is exactly right.

## Record dict construction (lines 713-760)

The dict carries 30+ fields. PnL-related fields:

- `"pnl_pct": pnl_pct,` — passes the input value through (0.0 from the WS path)
- `"pnl_usd": pnl_usd,` — same (0.0 unless back-derive ran)
- `"was_win": was_win,` — passes the input value through (False from the WS path)
- `"close_price": round(close_price, 6),` — correct (resolved from exit_price)
- `"direction": _side,` — correct (from state.side)
- `"entry_price": entry_price,` — correct (from state.entry_price)
- `"closed_at": datetime.now(timezone.utc).isoformat(),` — correct
- **No `opened_at` field** — the source of CRITICAL-2

The `direction` field at line 726 means the record DOES carry side information, which is what a back-derive of pnl_pct would need.

## Callback fan-out (lines 776-784)

```python
for i, callback in enumerate(self._callbacks_on_close):
    try:
        callback(record)
        log.debug(f"COORD_CB_OK | #{i+1} {cb_name} sym={symbol} | {ctx()}")
    except Exception as e:
        log.error(f"COORD_CB_FAIL | #{i+1} {cb_name} sym={symbol} err='{str(e)[:500]}' | {ctx()}")
```

14 callbacks fire per close. Each receives the same record dict. Callbacks reading `record["pnl_pct"]`, `record["pnl_usd"]`, or `record["was_win"]` get the corrupted values.

## Findings

1. The function has zero branches that compute `pnl_pct` from price data. The audit's CRITICAL-1 diagnosis is accurate.
2. The fix point is unambiguous: insert a new branch BEFORE line 696 that computes `pnl_pct` from `entry_price`, `close_price` (already resolved at line 687-688), and `_side` when `pnl_pct == 0` and both prices are non-zero. Then the existing `pnl_usd` back-derive at line 696 will run.
3. The record dict is built ONCE at line 713-760 and broadcast to 14 callbacks. Fixing it in coordinator fixes ALL downstream consumers in one place.
4. The comment in TradeState about `opened_at_dt` (line 33) means an ISO datetime is already on hand at the open — but the close path does not forward it. CRITICAL-2's fix can use either `state.opened_at` (epoch → convert) or `state.opened_at_dt.isoformat()` (already ISO).
5. `direction` (record key at line 726) maps from `_side` which maps from `state.side`. So the canonical direction source for the back-derive is `state.side` — the same source the existing `pnl_pct` back-derive of close_price uses (lines 690-693).

## Direction value enumeration

The existing back-derive code at lines 690-693 treats two values as Sell-equivalent: `("Sell", "Short")`. Any pnl_pct back-derive must use the same convention to stay consistent.

## Implication for the fix

The minimum-blast-radius coordinator-side fix is one new code block of ~6-8 lines, inserted between line 693 (after close_price resolution) and line 696 (before pnl_usd back-derive):

```python
# CRITICAL-1 fix: back-derive pnl_pct from prices + side when caller
# passed sentinel zero (e.g., bybit_demo WS subscriber).
if pnl_pct == 0 and entry_price > 0 and close_price > 0:
    if _side in ("Sell", "Short"):
        pnl_pct = ((entry_price - close_price) / entry_price) * 100
    else:
        pnl_pct = ((close_price - entry_price) / entry_price) * 100
    # was_win flips from the new pnl_pct
    was_win = pnl_pct > 0
```

This is one of three Phase 2 options; the operator chooses.
