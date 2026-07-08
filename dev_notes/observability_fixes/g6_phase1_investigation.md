# G6 Phase 1 — Investigation: COORD_REG fields + DUPLICATE_REGISTER

## Headline

Audit claim (`COORD_REGISTER` 0/20): **tag mismatch**. The canonical
event is `COORD_REG` and it fires 20 times in the audited window
(matches 20 opened trades exactly). The audit was looking for a
non-existent tag.

Phase 0 tag analysis: `_REGISTER` does not exist anywhere in the 986
unique src/ tags. The codebase's pattern for "open" lifecycle is
`_OPEN` (THESIS_OPEN), `_START` (COORD_CLOSE_START), or abbreviation
(COORD_REG). Renaming `COORD_REG` → `COORD_REGISTER` would create a
single-tag outlier and break any log consumer indexing on `COORD_REG`.

**Decision:** Keep `COORD_REG`. Two real gaps remain:

1. **Field completeness.** Audit's required fields:
   `sym, side, qty, entry_price, sl, tp, leverage, size_usd, trade_plan_id`.
   Current emission has: `sym, src, cat, immunity, did, order_id`.
   Missing immediately-available fields: `side, qty, entry_price`.
   (SL, TP, leverage, size_usd are not directly available at
   `register_trade` time — they arrive via the subsequent
   `register_trade_plan` call. Adding them to COORD_REG would require
   the caller to pass extra state; defer to a future cluster fix if
   needed.)

2. **COORD_DUPLICATE_REGISTER.** Surfaced from the cluster sweep
   (Prompt Part D Cluster D). `register_trade` silently overwrites
   `self._trades[symbol]` if a prior entry exists. The downstream
   cooldown gate normally prevents this, but **observability has to
   confirm the gate held — not assume it.**

## Schema

### COORD_REG (extended)

Before:
```
COORD_REG | sym=BTCUSDT src=brain_v2 cat=claude_direct immunity=60s did=d-X order_id=ORD-Y | did=d-X
```

After:
```
COORD_REG | sym=BTCUSDT src=brain_v2 cat=claude_direct side=Buy qty=0.05 entry_price=82000.5 immunity=60s did=d-X order_id=ORD-Y | did=d-X
```

Empty `side` falls back to `-` for grep-friendliness.

### COORD_DUPLICATE_REGISTER (new, WARNING)

```
COORD_DUPLICATE_REGISTER | sym=BTCUSDT prior_did=d-first prior_age_s=1.2 new_did=d-second new_src=claude_direct | {ctx()}
```

Fires before the `self._trades[symbol] = TradeState(...)` overwrite.
No behavior change — the overwrite is preserved.

## Behaviour preserved

- `register_trade` signature unchanged (still accepts the same kwargs).
- Overwrite semantics on duplicate registration unchanged.
- Downstream `register_trade_plan` and immunity gate unchanged.
- `COORD_REG` continues firing once per call (semantics unchanged).

## Tests

`tests/test_coord_register_observability.py` (5 cases):
- COORD_REG carries side, qty, entry_price
- Empty side → `side=-`
- Duplicate registration → DUPLICATE warning with prior/new ids
- First-time registration → no DUPLICATE warning
- Overwrite is preserved (TradeState replaced, not merged)

26 existing coordinator-adjacent tests pass.
