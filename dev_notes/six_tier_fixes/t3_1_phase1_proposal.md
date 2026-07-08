# T3-1 Phase 1+2 — F-4 Six safety gates absent on bybit_demo path

## 1. Defect statement

The Phase 5 report flagged six safety gates that live `OrderService.place_order` enforces but `BybitDemoAdapter.place_order` does not:

1. ~~orderLinkId idempotent retry~~ (already shipped — adapter.py:968).
2. position-size cap (% of equity)
3. per-trade max-loss cap (2% of equity)
4. mandatory-SL guard (reject naked positions)
5. leverage cap (e.g. operator-set tier ceiling)
6. post-place SL verify (re-fetch position to confirm SL attached)

`src/trading/services/order_guards.py` was added in P6 to mirror gate 0 (L3 toggle); the rest were "Out of scope (deferred to P11+)" per the docstring at lines 16-21.

## 2. Implementation cost spectrum

| Gate | Pure-fn predicate? | I/O needed | Complexity |
|------|---------------------|-----------|------------|
| 4. mandatory-SL guard | Yes | None | Trivial |
| 5. leverage cap | Yes | Settings read only | Trivial |
| 2. position-size cap | Mostly pure | wallet_balance + instrument_info + ticker | Medium |
| 3. per-trade max-loss | Mostly pure | wallet_balance + ticker | Medium |
| 6. post-place SL verify | Yes (predicate) | get_position after order | Medium |

Trivial gates are 1-line checks. Medium gates need 1-2 service calls each.

## 3. Three solution options

### Option A — Minimal: mandatory SL + leverage cap (recommended)

Two pure-function predicates in `order_guards.py`:
- `check_mandatory_sl_for_bybit_demo(stop_loss: float | None) -> tuple[bool, str]` — reject when stop_loss is None or <= 0. Operator can disable via settings (e.g. `mandatory_sl_for_bybit_demo: bool = True`).
- `check_leverage_cap_for_bybit_demo(leverage: int | None, max_leverage: int) -> tuple[bool, str]` — reject when leverage > max.

Caller: Transformer's `_OrderProxy.place_order` for `current_mode == "bybit_demo"`, BEFORE delegating to the adapter (same insertion point as the existing L3 gate check).

- LOC: ~50 (2 predicates, 2 caller checks, 4 smoke tests).
- Pros: Catches the most operator-impactful regressions (naked positions and rogue leverage) with minimal surface.
- Cons: Position-size cap and per-trade max-loss still absent. Post-place SL verify still absent.

### Option B — Mid-scope: A + position-size cap

Adds the position-size predicate. Reads wallet_balance + instrument_info + ticker price; computes notional value; rejects if > `max_position_size_pct` of equity.

- LOC: ~120.
- Pros: Catches the rogue-large-position scenario.
- Cons: Adds I/O on every place_order (cached but still 2-3 await points).

### Option C — Full: all 5 remaining gates

Includes per-trade max-loss + post-place SL verify.

- LOC: ~250.
- Pros: Bybit_demo achieves full parity with live.
- Cons: Most complex; post-place SL verify is an extra round-trip on every order.

## 4. Recommendation

**Option A (minimal: mandatory SL + leverage cap).**

Reasons:
1. The two trivial gates catch the highest-impact regressions: naked positions and rogue leverage. Both are operator-philosophy-aligned: aggressive trading does not require naked positions or 50x leverage on conservative capital.
2. Option B/C add I/O latency on every order placement. The bybit_demo path is currently fast (145-230 ms roundtrip); adding 2-3 awaits would noticeably degrade.
3. Bybit's server-side caps STILL apply for position-size and leverage (Bybit will reject if over). The bybit-side cap is the safety net; the local gate adds early-rejection observability.
4. Per-trade max-loss is partially mitigated upstream at `fund_manager.manager:343` when stop_loss_pct > 0.
5. Post-place SL verify is the only non-trivial parity gap — but it requires a second HTTP call per place_order. Defer.

## 5. Aim preservation

Both gates strengthen safety without restricting trade frequency unless the operator's actions are out of bounds:
- mandatory-SL: aggressive-exploitation does not require naked positions. Operator either passes a SL or the directive should be rejected.
- leverage-cap: operator-configured ceiling; rejects only when a directive exceeds the operator's own setting.

## 6. Operator decision required

A / B / C.
