# HIGH-1 Phase 1+2 — Investigation and Operator Discussion

## Audit reference

HIGH-1 — `account_snapshots` table dormant since 2026-05-08T11:19:21.

## Phase 0 evidence

- `MAX(updated_at)` from account_snapshots: `2026-05-08T11:19:21.750969+00:00`
- 62,733 total snapshots — all from shadow era
- Mode flipped to bybit_demo at `2026-05-08T11:19:26.785051+00:00` (5 seconds AFTER the last snapshot)

The 5-second correlation is decisive evidence that the writer is conditioned on shadow mode.

## Investigation

### Where the snapshot is written

`src/core/transformer.py:1160` defines `_save_account_snapshot(self, balance) -> None`. INSERT into `account_snapshots` table with 6 columns: total_equity, available_balance, used_margin, unrealized_pnl, margin_level_pct, updated_at. Note: `exchange_mode` is NOT a column — HIGH-2 will address.

### The shadow-only gate

`src/core/transformer.py:1334-1336` in `_AccountProxy.get_wallet_balance`:

```python
async def get_wallet_balance(self, *args, **kwargs):
    balance = await self._t.active_account_service.get_wallet_balance(*args, **kwargs)
    if self._t.is_shadow:
        balance = await self._t._enrich_balance_with_local_prices(balance)
        await self._t._save_account_snapshot(balance)
    return balance
```

The `if self._t.is_shadow:` block does TWO things:
1. Enrich balance with local prices (Shadow's prices come from the local Bybit ticker cache)
2. Save the snapshot

The docstring at `_save_account_snapshot:1163-1165` says: "In Bybit mode this is handled by AccountService internally. In Shadow mode, account data comes from the adapter and needs explicit snapshot saving after enrichment."

This claim that "Bybit mode this is handled by AccountService internally" was true at one point but is no longer accurate. The current `BybitDemoAccountService.get_wallet_balance` returns a balance object but does NOT write to `account_snapshots`.

### Verifying the claim

Let me grep for any other writers of account_snapshots.

```
grep -rn "INSERT INTO account_snapshots" src/
```

Result: only one writer (`transformer.py:1168`). So the docstring's claim is false — Bybit mode has no snapshot writer at all. That's the root cause.

### Cadence

`get_wallet_balance` is called from many places:
- TradeGate before each new trade (rate ~6/min during trading)
- Brain before strategic review (~6/h)
- Telegram /balance command (on-demand, rare)
- Worker manager periodic checks

Each call results in a snapshot write (when in shadow mode). This produced 62,733 rows in ~3 weeks of shadow operation = ~125 rows/h. That's well within DB bounds.

For bybit_demo, the same call frequency would produce similar cadence. No DB overload risk.

### Schema gap

account_snapshots has no `exchange_mode` column. Pre-HIGH-2, all rows are implicitly shadow. After HIGH-1 ships, NEW rows will be from bybit_demo but there's no column to disambiguate. This is acceptable as a stepping stone IF we coordinate with HIGH-2 to add the column in the next commit.

Operator decision per Rule 12: do we add `exchange_mode` here (mixed scope) or in HIGH-2 (separate)? Default per plan: keep HIGH-1 minimal, add column in HIGH-2 commit which already plans this for orders / trade_intelligence / trade_history / account_snapshots.

## Three options considered

### Option A — Remove the shadow-only gate (recommended)

Make the snapshot save unconditional (or branch on Bybit-mode separately):

```python
async def get_wallet_balance(self, *args, **kwargs):
    balance = await self._t.active_account_service.get_wallet_balance(*args, **kwargs)
    if self._t.is_shadow:
        # Shadow needs local-price enrichment before snapshot
        balance = await self._t._enrich_balance_with_local_prices(balance)
    # Both modes: snapshot the balance for equity curve
    await self._t._save_account_snapshot(balance)
    return balance
```

Pros:
- Minimum diff (one indent change + one comment)
- Both modes get equity history
- Enrichment stays shadow-only (correct — bybit_demo balances come pre-enriched from Bybit)
- Updates the stale docstring on `_save_account_snapshot`

Cons:
- account_snapshots gets mixed-mode rows post-fix without an exchange_mode column to filter. HIGH-2 will add the column.

### Option B — Add a separate scheduled snapshot task

Move snapshot writing to a periodic worker tick that fires every N seconds.

Pros: decoupled from get_wallet_balance call sites.
Cons: larger change; introduces a new worker; same DB write volume.

### Option C — Bybit-only branch

Add a `if self._t.is_bybit_demo:` branch for bybit_demo snapshot capture (no enrichment, just save).

Pros: explicit per-mode logic.
Cons: more code, same effective behavior as A.

## Recommendation

**Option A.** Smallest diff, correct behavior, leaves enrichment scoped to shadow where it's needed.

## Implementation plan

Single atomic commit. Files modified:

1. `src/core/transformer.py:1334-1336` — restructure the `if self._t.is_shadow:` block so enrichment stays shadow-only but snapshot save runs for both modes.
2. `src/core/transformer.py:1160-1166` — update docstring to reflect that the method now serves both modes.
3. `tests/test_high1_account_snapshots.py` — 3 tests:
   - bybit_demo mode: get_wallet_balance triggers a snapshot save
   - shadow mode: snapshot save still works (enrichment + save)
   - Snapshot writer is mode-agnostic

## Open questions

None blocking. The exchange_mode column will be added by HIGH-2 in the next commit (along with similar additions for orders, trade_history). HIGH-1's snapshots immediately after the fix will be implicitly bybit_demo (rule: any row after the mode flip at 2026-05-08T11:19:26 is bybit_demo); HIGH-2 backfill plan covers this disambiguation.
