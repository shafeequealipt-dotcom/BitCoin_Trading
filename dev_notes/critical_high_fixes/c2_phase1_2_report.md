# CRITICAL-2 Phase 1+2 — Investigation and Operator Discussion (Combined)

## Audit reference

CRITICAL-2, Section 2 of `AUDIT_BYBIT_COMPLETE_DATA_FLOW_FINDINGS.md`. Every `trade_log` row has empty string in `opened_at`. Affects both bybit_demo (116/116) AND shadow (1597/1597). Universal callback omission.

## Phase 0 confirmation

Verified 2026-05-09 baseline: `SELECT exchange_mode, COUNT(*) FILTER(WHERE opened_at IS NULL OR opened_at='') FROM trade_log GROUP BY exchange_mode;` returns:
- bybit_demo: 116 of 116 (100 percent)
- shadow: 1597 of 1597 (100 percent)

## Investigation

### Where opened_at is set

`TradeState` dataclass (`trade_coordinator.py:32-33`) carries TWO fields:
- `opened_at: float = 0.0` (epoch seconds, used by hold-time math)
- `opened_at_dt: datetime` (UTC datetime, used for ISO output)

Both are populated at `register_trade` (lines 280-281):
```python
opened_at=time.time(),
opened_at_dt=datetime.now(timezone.utc),
```

The dual representation exists exactly so the close path can serialize ISO without re-conversion.

### Why opened_at goes missing in the close record

`on_trade_closed` builds the record dict at `trade_coordinator.py:713-760`. The dict includes `closed_at` (line 723: `datetime.now(timezone.utc).isoformat()`) but does NOT include `opened_at`. There is no mapping from `state.opened_at_dt` into the record.

### Why the data_lake write loses it

`workers/manager.py:1878-1891` defines `_data_lake_close_callback`. It calls `data_lake.write_trade(...)` with 12 named arguments. `opened_at` is NOT among them.

`data_lake.write_trade` signature (`data_lake.py:56-65`) accepts `opened_at: str = ""`. INSERT writes the empty default at lines 145/150 (with mode branch) and 161/166 (without mode branch).

### Format requirement

`closed_at` is ISO string via `datetime.now(timezone.utc).isoformat()`. `opened_at` must match the same format for downstream SQL `WHERE opened_at >= '<iso>'` queries to work consistently. `state.opened_at_dt.isoformat()` produces the matching format.

### Other consumers of opened_at

Grepped `src/`. Findings:

| Consumer | Reads | Concern |
|---|---|---|
| `migrations.py:866, 914` | DDL only | none |
| `trade_coordinator.py:328-945` | reads `state.opened_at` (epoch) for hold-time math | unaffected — the epoch field stays |
| `data_lake.py:63-166` | accepts opened_at parameter, writes to column | benefits from fix |
| `tias/collector.py:80, 181` | reads `trade_thesis.opened_at` (different table) | unaffected |
| `strategist.py:1381` | formats `t['opened_at']` for display | benefits from fix when SQL serves trade_log to strategist |
| `trade_plan.py:39-80` | TradePlan's own `opened_at` (different dataclass) | unaffected |

No code path filters `trade_log.opened_at` for anything; the empty value silently degrades any query that would. After fix, downstream consumers see ISO strings and can sort/filter normally.

## Backfill question

For the existing 116 bybit_demo + 1597 shadow rows: candidate derivation `opened_at = closed_at − hold_seconds`. Both `closed_at` and `hold_minutes` (= hold_seconds / 60) are present in current rows, so `closed_at − hold_minutes * 60` is computable. Decision per Rule 12: **leave existing rows untouched**. A separate backfill pass can be scoped later if the operator wants. The empty-string sentinel makes pre-fix vs post-fix rows distinguishable.

## Three options considered

### Option A — Coordinator-side population (recommended)

Add `"opened_at": state.opened_at_dt.isoformat() if state else "",` to the record dict at `trade_coordinator.py:713-760` (alongside `closed_at`). Add `opened_at=record.get("opened_at", ""),` to the `_data_lake_close_callback` kwargs at `workers/manager.py:1878-1891`.

Pros:
- Matches the `closed_at` pattern symmetrically
- Single source of truth: `state.opened_at_dt` is already an ISO-friendly datetime
- Fixes both bybit_demo and shadow at once
- Two-line change, zero risk

Cons:
- None

### Option B — Compute in data_lake from `closed_at − hold_minutes`

Add a fallback in `data_lake.write_trade`: if `opened_at` is empty AND `closed_at` and `hold_minutes` are non-empty, compute `opened_at = closed_at - hold_minutes * 60`.

Pros:
- Zero changes to coordinator/manager
- Could backfill historical rows on the fly

Cons:
- Derives the value (loses precision on the original entry time); `state.opened_at_dt` is the actual entry time
- Adds derivation logic to a write-path function
- Doesn't match the `closed_at` pattern

### Option C — Schema-level NOT NULL with on-write default

Make `opened_at` NOT NULL with a `datetime('now')` default like `trade_thesis.opened_at`.

Pros:
- Hides the bug

Cons:
- Hides the bug — every row gets the close-time as opened_at, which is wrong
- Migration cost
- Doesn't actually fix the upstream omission

## Recommendation

**Option A.** It's the smallest, safest change and matches the existing `closed_at` pattern. The fix is two lines.

## Implementation plan

Single atomic commit. Files modified:

1. `src/core/trade_coordinator.py`: insert one field in record dict at line 723 area (alongside `closed_at`).
2. `src/workers/manager.py`: insert one kwarg in `data_lake.write_trade` call at line 1878 area.

Tests added:
- 1 unit test: record dict carries `opened_at` ISO string from `state.opened_at_dt`
- 1 unit test: empty string fallback when `state` is None (double-close race; existing guard covers this; defensive)
- 1 unit test: format matches `closed_at` (both end with timezone offset)
- 1 integration test: callback chain forwards opened_at to write_trade

Total: 4 new tests.

## Open questions

None. Proceeding with Option A in the same atomic-commit pattern as CRITICAL-1.
