# H4 Phase 1 — Gate Anatomy

## Scope

Full read of `src/apex/gate.py` (TradeGate) and the J6 reentry gate primitive `src/core/trade_coordinator.py::check_reentry_learning_gate`.

## TradeGate (`src/apex/gate.py`)

`TradeGate.validate(trade: dict) -> dict` runs 12 sequential checks. Each may *adjust* the trade or *reject* it (via `trade["_gate_rejected"]`). Returns the modified trade dict; `layer_manager` then skips on `_gate_rejected`.

### Check inventory

| # | Name | Action | Site |
|---|---|---|---|
| 0 | Claude directive size cap | adjust (CONVICTION_SIZE_CAP) | gate.py:65-97 |
| 1 | Maximum position size (`max_position_size_usd`) | adjust | gate.py:99-104 |
| 2 | Maximum leverage (`max_leverage`) | adjust | gate.py:106-111 |
| 3 | Maximum concurrent positions (5) | adjust (size×0.3 when at cap) | gate.py:113-129 |
| 4 | Capital availability + conviction weight + zero-conviction REJECT | REJECT (zero_conviction) / adjust | gate.py:131-261 |
| 5 | Duplicate position on same symbol | adjust (size×0.5) | gate.py:263-274 |
| 6 | Recent cooldown — revenge-trade defense | REJECT (loss_cooldown_same_direction) / adjust (size×0.5) | gate.py:276-320 |
| 6b | **J6 reentry learning gate** | REJECT (reentry_learning_gate_*) / allow | gate.py:322-427 |
| 7 | Minimum position size floor | adjust | gate.py:429+ (read separately) |
| 8-12 | APEX guardrails (TP floor, trail floor, RR, etc.) | adjust | (out of H4 scope) |

Only checks 4, 6, and 6b produce hard rejections. H4 concerns only the 6b path (`reentry_learning_gate_*`).

### Check 6b flow (gate.py:337-427)

1. Reads current `direction` from trade dict.
2. Reads current `setup_type`: from trade dict; if absent, from `structure_cache.get(symbol).setup_type.value`.
3. Reads current `regime`: from `regime_detector.get_coin_regime(symbol).regime.value`.
4. Calls `coordinator.check_reentry_learning_gate(db, symbol, current_regime, current_setup_type, current_direction)`.
5. Logs `REENTRY_REGIME_DRIFT_CHECK` (INFO, gate.py:398-405) with cur_* vs prior_* values + reason.
6. If `not gate_result["allow"]`:
   - Sets `trade["_gate_rejected"] = f"reentry_learning_gate_{reason}"` (gate.py:408).
   - Logs `REENTRY_LEARNING_GATE | sym=... action=block reason=... cur_regime=... cur_setup=... cur_dir=... prior_pnl=...` at WARNING (gate.py:412-417).
7. Else logs at INFO (gate.py:419-422).

The setting `reentry_learning_gate_enabled` (default True) is the only toggle. Exception fallback is allow-with-warning (gate.py:423-427) — defensive.

## `check_reentry_learning_gate` (`src/core/trade_coordinator.py:1478-1599`)

Signature: `async def check_reentry_learning_gate(db, symbol, current_regime, current_setup_type, current_direction) -> dict[str, Any]`.

### Query (coordinator.py:1542-1552)

```sql
SELECT entry_regime_at_open, entry_setup_type, direction, actual_pnl_usd
FROM trade_thesis
WHERE symbol = ?
  AND status = 'closed'
  AND actual_pnl_usd < 0
ORDER BY closed_at DESC
LIMIT 1
```

**Critical properties:**
- **NO time bound.** A loss from 7 days ago is treated identically to a loss from 30 minutes ago.
- **NO magnitude filter.** A loss of $0.04 is treated identically to a loss of $32.88.
- **NO loser-cohort awareness.** A single losing trade can block re-entries indefinitely; subsequent winning trades on the same symbol do not reset state.
- **LIMIT 1** — only the single most recent loss matters; older losses are ignored.

### Equivalence test (coordinator.py:1579-1599)

After loading prior values:
- Direction drift → allow (`reason=direction_drift`).
- Regime drift (current_regime != prior_regime) → allow (`reason=regime_drift`).
- Setup drift (current_setup_type != prior_setup_type) → allow (`reason=setup_drift`).
- Fallthrough: **all three categorical fields match → block** (`reason=same_conditions`).

Defensive escapes:
- DB error → allow with `reason=db_error`.
- Missing prior data (any of regime/setup/direction empty) → allow with `reason=no_prior_loss`.

### Return dict

```python
{
  "allow": bool,
  "reason": "no_prior_loss" | "regime_drift" | "setup_drift"
          | "direction_drift" | "same_conditions" | "db_error",
  "prior_regime": str | None,
  "prior_setup_type": str | None,
  "prior_direction": str | None,
  "prior_pnl_usd": float | None,
}
```

## How prior fields get populated (the supply side)

`entry_regime_at_open` and `entry_setup_type` are stamped onto `trade_thesis` rows at thesis open. Per the j6 comment in gate.py:344-352, `entry_setup_type` is populated AFTER the order is placed by `strategy_worker._execute_claude_trade`. Cardinality:

- `entry_regime_at_open` values (from `RegimeDetector` enum): `trending_up`, `trending_down`, `ranging`, `chop`, `dead`, etc. (a small finite set).
- `entry_setup_type` values (from `XRAYStructure.setup_type` enum): `bearish_fvg_ob`, `bearish_structural_break`, `bullish_*` variants, etc. (a moderate finite set).
- `direction`: `Buy` or `Sell`.

**Categorical product cardinality ≈ 6 regimes × 10 setups × 2 directions = ~120 cells.** Given the system selects from 50 coins, each (symbol, cell) pair is sparsely populated; once a single loss falls into a cell, every future trade in that cell is blocked indefinitely.

## Logged events in current code

| Event | File:Line | Level | Purpose |
|---|---|---|---|
| `REENTRY_REGIME_DRIFT_CHECK` | gate.py:398 | INFO | Per-evaluation context (always fires) |
| `REENTRY_LEARNING_GATE action=block` | gate.py:412 | WARNING | Block decision |
| `REENTRY_LEARNING_GATE action=allow` | gate.py:419 | INFO | Allow decision |
| `REENTRY_LEARNING_GATE_FAIL` | gate.py:424 | WARNING | Exception path (fallthrough to allow) |
| `REENTRY_LEARNING_GATE_DB_FAIL` | coord.py:1554 | WARNING | DB query failure |
| `TRADE_SKIP rsn=gate_rejected` | layer_manager.py:1481 | WARNING | Downstream skip |

Persistence: **none**. Rejections are observable only in live log streams; no `gate_rejections` table exists.

## Summary

The gate is correct in its defensive design (allow on missing data / DB error) but its **block condition is too coarse and time-unbounded**. The 3-tuple `(regime, setup_type, direction)` plus "any loss ever" is the entire block criterion. A meaningful re-entry that arrives after a single small loss in the same cell — even days later, even after intervening winners — is blocked.

This anatomy is the substrate; the rejection trace (next file) shows the consequence in production.
