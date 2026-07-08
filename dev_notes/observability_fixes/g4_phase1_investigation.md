# G4 Phase 1 — Investigation: BYBIT_DEMO_WS_POS_UPDATE for non-flat snapshots

## Headline finding

Audit claim: `BYBIT_DEMO_WS_POSITION` fires zero times. Verified.
The position handler at
`src/bybit_demo/bybit_demo_websocket_subscriber.py:258-282` only emits
on the size==0 path (`BYBIT_DEMO_WS_POS_FLAT` — 18 events in window).
Non-flat state changes are silent.

Bybit's position WS emits on:
- size change (fill, reduce, close)
- entryPrice / avgPrice change
- SL / TP modification
- leverage change
- positionStatus change (Normal/Liq/Adl)

Currently invisible. F-26 ground-truth divergence (operator-observed:
system thinks 2 positions open, exchange has 5) would have surfaced
immediately if non-flat updates were logged.

## Schema decision

New event: `BYBIT_DEMO_WS_POS_UPDATE` (separate from POS_FLAT — clear
lifecycle separation: UPDATE during life, FLAT at end).

Fields (best-effort reads with sentinel defaults):
- sym, side, qty (= size)
- entry_price (entryPrice or avgPrice fallback)
- mark_price (markPrice — for divergence correlation)
- unrealized_pnl (unrealisedPnl, with US-spelling fallback)
- sl_price, tp_price (stopLoss, takeProfit)
- lev (leverage)
- status (positionStatus)

## Volume estimate

Position events per trade lifecycle: ~3-5 (open, SL set, TP set, periodic
size confirmation, close-via-flat). 20 trades / 1.5h ≈ 60-100 events.
Comfortable under volume budget.

## Behaviour preserved

- Coordinator NOT called from this handler (execution stream remains
  canonical close source)
- POS_FLAT continues firing on size==0 (legacy)
- Parse-fail path unchanged
- Multiple-position-per-message handled (was already the case)

## Tests

`tests/test_ws_position_observability.py` (5 cases):
- Non-flat → POS_UPDATE with all fields, NO POS_FLAT
- Flat → POS_FLAT only, NO POS_UPDATE
- Missing optional fields → emit with empty defaults
- Multiple positions in one message → one event per position
- Malformed size → defaults to flat
