# HIGH-2 Phase 1+2 — Investigation and Operator Discussion

## Audit reference

HIGH-2 — Missing `exchange_mode` columns on orders, trade_intelligence, account_snapshots.

## Phase 0 verification

PRAGMA results from `data/trading.db`:

| Table | exchange_mode column? | Notes |
|---|---|---|
| `trade_log` | yes (NOT NULL DEFAULT 'shadow') | Already migrated by P8 |
| `trade_thesis` | yes | Already migrated by P5 |
| `trade_intelligence` | **yes** (cid 94, NOT NULL DEFAULT 'shadow') | **Already migrated by P4 — audit assumption was stale here** |
| `orders` | no | Needs migration |
| `account_snapshots` | no | Needs migration |
| `trade_history` | no | Needs migration |

So HIGH-2's effective scope is THREE tables, not four: `orders`, `account_snapshots`, `trade_history`. `trade_intelligence` is already done.

## Investigation — writers per table

| Table | Writer | Knows mode? |
|---|---|---|
| `orders` | `trading_repo.save_order(order)` called from `bybit_demo_adapter` (3 sites) and live `position_service` | bybit_demo: yes; live: yes via service context |
| `account_snapshots` | `transformer._save_account_snapshot(balance)` (single writer) | transformer knows current_mode |
| `trade_history` | `trading_repo.save_trade(trade)` called from CRITICAL-3's new `_trade_history_close_callback` (and live `position_service`) | C3 callback: yes (resolves from transformer); live: yes |

## Backfill plans

| Table | Heuristic | Coverage |
|---|---|---|
| `orders` | `created_at >= '2026-05-08T11:19:26'` → bybit_demo; else shadow | All 88 existing rows are post-cutover, so 88 → bybit_demo |
| `account_snapshots` | `updated_at < '2026-05-08T11:19:26'` → shadow; else bybit_demo (HIGH-1 starts producing post-fix) | All 62,733 existing rows are pre-cutover → shadow |
| `trade_history` | `trade_id LIKE 'bd-%'` OR `entry_time >= '2026-05-08T11:19:26'` → bybit_demo; else shadow | All 30 existing rows have bd- prefix → bybit_demo |

The cutover timestamp `2026-05-08T11:19:26` is the documented mode flip per `transformer_state.last_switched_at`.

## Three options considered

### Option A — Schema + writer updates + backfill in one commit (recommended)

Single atomic commit:
1. Bump SCHEMA_VERSION 29 → 30
2. Append 3 ALTER TABLE + 3 UPDATE backfill statements to MIGRATIONS list
3. Update `trading_repo.save_order` and `save_trade` to accept `exchange_mode: str = ""` kwarg (defaults to empty so legacy callers fall through to the column DEFAULT)
4. Update `transformer._save_account_snapshot` to accept/insert `exchange_mode` (caller resolves from transformer.current_mode)
5. Update CRITICAL-3's `_trade_history_close_callback` to pass `exchange_mode=_mode` (already resolves mode for the data_lake callback nearby)
6. Update `bybit_demo_adapter.close_position` save_order calls to pass `exchange_mode="bybit_demo"`

Pros:
- All-or-nothing: a partial deploy (schema migrated but writers not updated) would leave new rows with the column DEFAULT 'shadow' which is wrong but recoverable; full commit eliminates the window
- Mirrors P4/P8 pattern (ALTER + backfill in same migration list)
- Backfill is idempotent (UPDATE ... WHERE filters distinguish)

Cons:
- Touches 5 files (migrations + repo + transformer + manager + adapter)

### Option B — Schema-only first, writers in a follow-up commit

ALTER TABLE first; new rows write to column DEFAULT 'shadow' (wrong but harmless). Writers in a second commit a few hours later.

Pros: smallest first commit.
Cons: violates atomicity — between commits, NEW rows are mistagged. Discouraged per the audit's universal correctness goal.

### Option C — Schema + writers without backfill

Apply migrations, update writers, leave existing rows untouched (rule 12 default).

Pros: even smaller diff (skip backfill UPDATEs).
Cons: legacy rows have column DEFAULT 'shadow' which is wrong for orders (88 are bybit_demo) and trade_history (30 are bybit_demo). The wrong tag would persist forever unless backfilled later.

## Recommendation

**Option A.** Atomic schema + writer + backfill commit. Backfill is provably correct (mode flip is timestamped; trade_id prefix is unambiguous).

## Implementation plan

Single atomic commit. Files modified:

1. `src/database/migrations.py`:
   - Bump `SCHEMA_VERSION` from 29 to 30.
   - Append three ALTER TABLE statements (orders, account_snapshots, trade_history) with default 'shadow'.
   - Append three UPDATE backfill statements (idempotent via WHERE filters).

2. `src/database/repositories/trading_repo.py`:
   - `save_order(order, *, exchange_mode: str = "")` — INSERT now includes the new column when non-empty.
   - `save_trade(trade, *, exchange_mode: str = "")` — same.

3. `src/core/transformer.py`:
   - `_save_account_snapshot(balance, *, exchange_mode: str = "")` — INSERT now includes the new column when non-empty.
   - `_AccountProxy.get_wallet_balance` resolves `exchange_mode` from `self._t.current_mode` and passes it.

4. `src/workers/manager.py:_trade_history_close_callback`:
   - Pass `exchange_mode=_mode` to `bd_trading_repo.save_trade(...)`.

5. `src/bybit_demo/bybit_demo_adapter.py:close_position`:
   - Pass `exchange_mode="bybit_demo"` to the `_trading_repo.save_order(...)` call (only one, since trade_history call was removed by CRITICAL-3).

6. Tests: `tests/test_high2_exchange_mode_columns.py` with 7+ tests covering each writer, the migration's ALTER + backfill semantics, and idempotency.

## Open questions

None blocking. Live `position_service.save_order/save_trade` calls (out of scope per prompt — live mode disabled) keep the empty-string default, falling back to column DEFAULT 'shadow'. Operator can supply `exchange_mode="bybit"` in a future commit when live mode is re-enabled.
