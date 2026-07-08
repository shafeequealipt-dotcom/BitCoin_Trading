# CRITICAL-1 Phase 1 — Downstream Propagation Paths

## Purpose

Trace how `record["pnl_pct"]`, `record["pnl_usd"]`, and `record["was_win"]` propagate from `TradeCoordinator.on_trade_closed`'s record dict into the three corrupted tables: `trade_log`, `trade_intelligence`, `trade_thesis`.

## Path A — trade_log (via data_lake)

### Callback registration

`src/workers/manager.py:1899` `coordinator.register_close_callback(_data_lake_close_callback)`

### Callback definition (lines 1860-1898)

```python
def _data_lake_close_callback(record):
    sym = record.get("symbol", "?")
    try:
        _xfm = self._services.get("transformer")
        _mode = ""
        if _xfm is not None:
            try:
                _mode = str(_xfm.current_mode or "")
            except Exception:
                _mode = ""
        _t = _dl_aio.get_event_loop().create_task(
            data_lake.write_trade(
                trade_id=record.get("trade_id", ""),
                symbol=record["symbol"],
                direction=record.get("direction", ""),
                entry_price=record.get("entry_price", 0),
                exit_price=record.get("close_price", 0),
                pnl_pct=record["pnl_pct"],          # ← reads bug source
                pnl_usd=record["pnl_usd"],          # ← reads bug source
                strategy=record.get("strategy_name", ""),
                close_reason=record["closed_by"],
                hold_minutes=record["hold_seconds"] / 60,
                closed_at=record.get("closed_at", ""),
                exchange_mode=_mode,
            )
        )
        _t.add_done_callback(_close_cb_done("data_lake", sym))
    except Exception as e:
        log.warning(f"CLOSE_CB_FAIL | cb=data_lake sym={sym} err='...' | {ctx()}")
```

Note: `opened_at` is NOT in the kwargs (CRITICAL-2 root). The `closed_at` comes from `record.get("closed_at", "")`, which the coordinator populates correctly with ISO format.

### data_lake.write_trade (src/core/data_lake.py:56-167)

Signature includes `opened_at: str = ""` (line 63) defaulting to empty string. The INSERT path at lines 138-167 uses two branches:

- Lines 140-151 (when `exchange_mode` non-empty): explicit INSERT with all 16 columns including opened_at, closed_at, exchange_mode.
- Lines 156-167 (fallback): INSERT without exchange_mode column, relying on column default 'shadow'. Triggers `DL_TRADE_NO_MODE` warning.

Both branches write `opened_at` from the parameter — which is `""` because the callback never passes it.

### DL_TRADE_SUSPECT alert (data_lake.py:93-115)

```python
if pnl_pct == 0 and entry_price > 0 and exit_price > 0 and entry_price != exit_price:
    log.error(f"DL_TRADE_SUSPECT | tid={trade_id} sym={symbol} ent={entry_price} "
              f"ext={exit_price} pnl=0.00 — DATA INTEGRITY ISSUE | {ctx()}")
    if self._alert_manager is not None:
        await self._alert_manager.send_risk_warning("DL_TRADE_SUSPECT", {...})
```

`send_risk_warning` is hardwired to `AlertLevel.CRITICAL` (per `alert_manager.py:98`). This fires per close in the audit window — 49 times in 2.85h.

## Path B — trade_thesis (via thesis_manager)

### Callback registration

`src/workers/manager.py:1853` `coordinator.register_close_callback(_thesis_close_callback)`

### Callback definition (lines 1829-1852)

```python
def _thesis_close_callback(record):
    sym = record.get("symbol", "?")
    try:
        _t = _thesis_aio.get_event_loop().create_task(
            thesis_manager.close_thesis(
                symbol=record["symbol"],
                close_price=record.get("close_price", 0),
                actual_pnl_pct=record["pnl_pct"],   # ← reads bug source
                actual_pnl_usd=record["pnl_usd"],   # ← reads bug source
                close_reason=record["closed_by"],
                order_id=record.get("order_id", "") or "",
            )
        )
        _t.add_done_callback(_close_cb_done("thesis", sym))
    except Exception as e:
        log.warning(f"CLOSE_CB_FAIL | cb=thesis sym={sym} err='...' | {ctx()}")
```

### thesis_manager.close_thesis (src/core/thesis_manager.py:174-266)

UPDATEs `trade_thesis`:

```sql
UPDATE trade_thesis
SET status = 'closed',
    closed_at = CURRENT_TIMESTAMP,
    close_price = ?,
    actual_pnl_pct = ?,           -- from record["pnl_pct"] (corrupt)
    actual_pnl_usd = ?,           -- from record["pnl_usd"] (corrupt)
    close_reason = ?,
    lesson = ?
WHERE symbol = ? AND order_id = ?
  AND (status = 'open'
       OR (status = 'closed' AND actual_pnl_usd = 0 AND close_reason = 'zombie_reconciler'))
```

Note: WHERE clause is symbol + order_id when order_id present (line 218-235), or symbol-only fallback (line 237-253). Both paths write the same corrupt pnl values.

The "zombie_reconciler" branch in the WHERE clause was added by P5 of P1-P10 to catch zombie-reconciled rows. Audit measured 38 percent of bybit_demo closed thesis rows have actual_pnl_usd=0 — partially explained by CRITICAL-1, partially by the zombie-reconciler race (HIGH-6, auto-resolved when CRITICAL-1 ships).

## Path C — trade_intelligence (via TIAS)

### Callback registration

`src/workers/manager.py:2106` `coordinator.register_close_callback(_tias_close_callback)`

### Callback chain

The TIAS callback (workers/manager.py:2069) wraps `TradeContextCollector.collect_and_save(record, repo, m4_snapshot)`.

### TradeContextCollector._extract_group_a (src/tias/collector.py:136-151)

```python
def _extract_group_a(self, record: dict) -> dict:
    return {
        "symbol": record.get("symbol", ""),
        "direction": record.get("direction", ""),
        "strategy_name": record.get("strategy_name", ""),
        "strategy_category": record.get("strategy_category", ""),
        "source": record.get("source", ""),
        "closed_by": record.get("closed_by", ""),
        "entry_price": float(record.get("entry_price", 0.0) or 0.0),
        "exit_price": float(record.get("close_price", 0.0) or 0.0),
        "pnl_pct": float(record.get("pnl_pct", 0.0) or 0.0),     # ← reads bug source
        "pnl_usd": float(record.get("pnl_usd", 0.0) or 0.0),     # ← reads bug source
        "win": bool(record.get("was_win", False)),                # ← reads bug source
        "hold_seconds": float(record.get("hold_seconds", 0.0) or 0.0),
    }
```

### TradeIntelligenceRepo.save (src/tias/repository.py:25-49)

```python
async def save(self, trade: TradeIntelligence) -> int:
    data = asdict(trade)
    data["win"] = 1 if data["win"] else 0
    ...
    cursor = await self._db.execute(
        f"INSERT INTO trade_intelligence ({col_names}) VALUES ({placeholders})",
        values,
    )
    return cursor.lastrowid or 0
```

Dynamic INSERT writes pnl_pct=0.0, pnl_usd=0.0, win=0 directly into trade_intelligence.

## Side-by-side: which downstream consumer breaks how

| Consumer | Reads | Effect when pnl=0/win=0 |
|---|---|---|
| trade_log | pnl_pct, pnl_usd | DL_TRADE_SUSPECT fires CRITICAL alert per close (49/3h) |
| trade_log dashboard / Telegram /pnl | aggregate sums | Shows zero PnL across all bybit_demo trades |
| trade_thesis | actual_pnl_pct, actual_pnl_usd | Lessons fed to DeepSeek learn from $0 outcomes |
| trade_intelligence | pnl_pct, pnl_usd, win | DeepSeek + APEX feedback learn from $0 outcomes; win rate appears 0% |
| Performance Enforcer | pnl_usd via aggregate query | Mode transitions blocked by zero PnL signal (artificially conservative) |
| TIAS lessons → strategist | lessons via thesis | Strategist sees no edge in any strategy |
| Telegram /history | per-row entries | Shows wrong PnL to operator |

## Shared root

All three downstream paths read from the same `record` dict built in `coordinator.on_trade_closed:713-760`. **Fixing the dict construction (or its inputs) at coordinator level fixes all three at once.** The 14-callback fan-out becomes a feature: one fix, three tables corrected.

## Findings

1. The 14 close callbacks are stateless consumers of the record dict; they never re-derive PnL from prices.
2. The `data_lake_close_callback` is the ONLY callback that triggers an alert on PnL anomalies (DL_TRADE_SUSPECT). The other two corrupt downstream consumers (trade_thesis, trade_intelligence) silently write the bad values.
3. The `was_win` flag is consumed only by TIAS (via `_extract_group_a:149`) and indirectly by the per-symbol cooldown calculation in coordinator (line 787, after the record is built — but the cooldown reads the function parameter `was_win` directly, not `record["was_win"]`). So fixing `was_win` in the function before record-build also fixes cooldown timing.
4. None of the three downstream callbacks have any defensive logic to handle pnl=0 — they trust the coordinator's contract.
5. Adding a pnl_pct back-derive at coordinator-level requires zero changes to any of the three downstream paths.
