# T2-1 Phase 1 — F20 Soft cooldown / revenge trade investigation

## 1. Defect statement

After a position closes at a loss, `coordinator._symbol_cooldowns[symbol]` is set to expire 600 s later (10 min). The APEX gate (`src/apex/gate.py:232-245` CHECK 6) detects symbols in cooldown but HALVES SIZE rather than REJECTING. The same direction (e.g. Sell) that just lost can be re-entered ~30 s later with half size — a revenge-trade vulnerability.

Today's evidence: 2 `size_halved_cooldown_*` events observed in workers.log (matches the live report's 13:50 FILUSDT 16s-cooldown + SKRUSDT 100s-cooldown re-entries — both same direction, both subsequently force-closed by time-decay structural invalidation).

## 2. Cooldown semantics today

`src/core/trade_coordinator.py:893-901` (inside `on_trade_closed`):

```python
if was_win:
    cooldown_sec = 180   # 3 min after win
elif closed_by in ("hard_stop", "mode4_crash"):
    cooldown_sec = 900   # 15 min after hard stop / flash crash
else:
    cooldown_sec = 600   # 10 min after normal loss
self._symbol_cooldowns[symbol] = time.time() + cooldown_sec
```

- All closes get a cooldown (including wins).
- Direction of the closed trade is NOT recorded.
- The 600s "normal loss" branch covers most losing closes.

`is_symbol_cooled_down(symbol)` just returns a bool. Direction is unavailable.

## 3. Gate enforcement today

`src/apex/gate.py:232-245`:

```python
if coordinator.is_symbol_cooled_down(symbol):
    size = float(trade.get("size_usd", 600) or 600)
    trade["size_usd"] = round(size * 0.5, 2)
    remaining = ...
    modifications.append(f"size_halved_cooldown_{remaining}s")
```

Sizes down; trade proceeds. No reject branch in the gate's return contract.

## 4. Layer_manager dispatch

`src/core/layer_manager.py:1404` runs `trade = await gate.validate(trade)` and unconditionally proceeds to `strategy_worker._execute_claude_trade(trade, ...)`. There is no existing `_rejected` check.

## 5. Root cause + scope

The cooldown was implemented as a "size knob" rather than a circuit breaker. To convert it to a circuit breaker for the revenge-trade case specifically (same direction after a loss), two changes are needed:

1. Coordinator must track the closing direction of LOSING trades, exposed via a new public method `get_loss_cooldown_direction(symbol)`.
2. Gate's CHECK 6 must reject (not size-halve) when prior was loss AND new direction matches. Opposite-direction trades still flow with size_halved (riding the bounce is OK; doubling down on the losing direction is not).

## 6. Investigation conclusions

1. Confirmed F20 is a soft-cooldown bug — the cooldown was designed as a size knob, not a circuit breaker.
2. Required additions: track closing direction in the coordinator (only for losses); expose via public method; add a reject mechanism to the gate.
3. The reject mechanism (`trade["_gate_rejected"] = reason`) is reusable for T2-2 zero-conviction reject.
4. Aim-preservation: opposite-direction trades during cooldown continue to flow with size_halved (operator's aggressive-exploitation philosophy preserved).

Phase 2 proposal follows.
