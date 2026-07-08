# G8 Phase 1 — Investigation: THESIS_OPEN field completeness

## Headline

Audit claim (`THESIS_SAVE` 0/20): **tag mismatch**.

The canonical save-side event is `THESIS_OPEN` at
`src/core/thesis_manager.py:183`. It fires 20 times in the audited
window — exactly matches 20 opened trades.

Phase 0 tag analysis: `_SAVE` exists in only ONE place across 986
unique tags (TIAS_SAVE on the close-side). The `_OPEN`/`_CLOSE`
suffix pair is the established lifecycle pattern (THESIS_OPEN /
THESIS_CLOSE; XRAY_SHADOW_CONN_OPEN). Renaming THESIS_OPEN to
THESIS_SAVE would diverge from this pattern and break dashboards.

## Field completeness check

Audit-required fields:

| Audit field | Pre-G8 emission | Post-G8 |
|-------------|------------------|----------|
| sym | yes | yes |
| thesis_id | yes (`id=`) | yes |
| rationale_hash | NO | NOT ADDED — see below |
| target_pnl_pct | NO | yes (`target_pct=`) |
| stop_pct | NO | yes (`stop_pct=`) |
| expected_hold_min | NO | yes (`max_hold_min=`) |

Additional fields added for grep-friendliness:
- `size_usd=` (audit also requested in cluster E persistence sweep)
- `order_id=` (mirrors COORD_REG order_id pairing for cross-event correlation)

`rationale_hash` is intentionally NOT added. The thesis text is
already persisted in DB (`trade_thesis.thesis` column) and the audit's
benefit was identity-tracking. A short hash adds 4-8 bytes per line
without changing the observability story — operators correlate via
thesis_id, not by rationale hash. Defer to a future cluster fix if
needed.

## Schema (extended)

```
THESIS_OPEN | id=42 sym=BTCUSDT dir=long ent=80000 sl=78000 tp=84000
  target_pct=5.000 stop_pct=2.500 lev=5 size_usd=500
  max_hold_min=120 order_id=ORD-AB-1234 | did=...
```

`target_pct` and `stop_pct` are absolute distances from entry
(direction-agnostic). For long: `(tp - entry)/entry * 100` and
`(entry - sl)/entry * 100`. For short: `(entry - tp)/entry * 100` and
`(sl - entry)/entry * 100`. The audit's signed pct semantics map
cleanly to the direction field (also in the same line).

Degenerate entry=0 is guarded via `max(entry_price, 1e-9)` divisor —
emits sensible numbers without ZeroDivisionError.

## Behaviour preserved

- `save_thesis` signature unchanged
- DB INSERT unchanged
- THESIS_FLIP_PERSISTED secondary emission preserved
- Free-text "Thesis saved: #N {dir} {sym} — {thesis}" log preserved
- thesis_id return value unchanged

## Tests

`tests/test_thesis_save_observability.py` (4 cases):
- Full field set with correct percentage math for long
- Same for short (direction-agnostic absolute distances)
- Zero-entry guard does not crash
- Empty order_id falls back to `-`

20 existing thesis-related tests pass.
