# T2-1 Phase 2 — F20 Soft cooldown proposal

## 1. Confirmed diagnosis

- Cooldown is a SIZE knob, not a circuit breaker: `apex/gate.py:232-245` halves size on cooled-down symbols and lets the trade proceed.
- Closing direction is NOT tracked in `coordinator._symbol_cooldowns` today.
- No reject mechanism exists in the gate's return contract.
- Today's evidence: 2 `size_halved_cooldown_*` events; both followed by force-closes (revenge trades that died).

## 2. Recommended solution

Three coordinated changes:

1. **Coordinator** — add `_loss_cooldown_direction: dict[str, str]` and populate it in `on_trade_closed` ONLY when `was_win=False`. Add public method `get_loss_cooldown_direction(symbol) -> str | None`. Clear together with the expiry on a successful opposite-direction trade or on expiry.
2. **Gate (CHECK 6)** — when symbol is cooled AND `get_loss_cooldown_direction(symbol)` equals the new trade's direction, set `trade["_gate_rejected"] = "loss_cooldown_same_direction_{remaining}s"` and return. Otherwise, retain existing `size_halved_cooldown_*` behaviour for opposite-direction / non-loss cooldown cases.
3. **Layer_manager** — after `gate.validate(trade)`, check `trade.get("_gate_rejected")` and skip with a structured `GATE_REJECT` WARN log. This reject hook is reusable for T2-2 (zero-conviction).

## 3. Options

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A (recommended)** | Hard reject same-direction during loss cooldown only. Opposite-direction stays size-halved. | Surgical. Preserves opposite-direction aggressive exploitation. | Adds new state + new gate flag. |
| B | Hard reject ALL same-direction during ANY cooldown (incl. win cooldown). | Simpler logic. | Loses winning-trend continuation case. Operator philosophy violated. |
| C | Hard reject ALL during cooldown regardless of direction. | Simplest. | Severely curtails trade frequency. Operator philosophy strongly violated. |

## 4. Recommendation: A

Single-line operator decision needed: A / B / C. A is the only option that preserves the aggressive-exploitation philosophy.

## 5. Aim preservation

- Opposite-direction trades during cooldown still flow with size_halved (current behavior).
- Non-loss cooldowns (win cooldown 180s, hard_stop cooldown 900s) still allow same-direction with size_halved.
- ONLY: same direction during a LOSS cooldown gets rejected.

## 6. Observability additions

- `GATE_REJECT layer=gate sym=X reason=loss_cooldown_same_direction_Ns prior_dir=X new_dir=X` — WARN, fires when gate rejects.
- Coordinator gains a new INFO `COORD_LOSS_COOLDOWN_SET sym=X dir=X expires_in=Ns`.

## 7. Test plan (smoke, ≤10 min)

`tests/test_t2_1_loss_cooldown.py` — 4 tests:

1. `get_loss_cooldown_direction` returns the direction after a losing close.
2. Returns None after a winning close (no loss direction tracking).
3. Returns None after cooldown expires.
4. Setting cooldown_direction also gets cleared when the cooldown expires.

## 8. Operator decision required

Please choose A / B / C. Default A (recommended).
