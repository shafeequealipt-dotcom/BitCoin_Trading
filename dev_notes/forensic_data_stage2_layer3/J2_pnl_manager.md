# J2 — DailyPnLManager

Collection timestamp: 2026-05-02 ~11:45 UTC
DB snapshot: /tmp/trading_snapshot_1777722335.db
Logs searched: workers.2026-05-02_04-31-00_392071.log, workers.log

---

## 1. File location & size

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/pnl_manager.py`
- Lines of code: **449** (`wc -l`)
- Class: `DailyPnLManager` at pnl_manager.py:16

### Public methods

| Method | Signature | File:line |
|---|---|---|
| `__init__` | `(settings, account_service=None, position_service=None, db=None)` | pnl_manager.py:26 |
| `initialize` | `() -> None` (async) | pnl_manager.py:69 |
| `update` | `() -> None` (async) | pnl_manager.py:141 |
| `get_current_mode` | `() -> dict` | pnl_manager.py:204 |
| `can_trade` | `() -> tuple[bool, str]` | pnl_manager.py:284 |
| `pause_manually` | `(reason: str = "operator") -> None` | pnl_manager.py:293 |
| `resume_manually` | `() -> None` | pnl_manager.py:299 |
| `is_manually_paused` | `@property -> bool` | pnl_manager.py:306 |
| `apply_restrictions` | `(setups: list[EnsembleResult], mode: dict) -> list[EnsembleResult]` | pnl_manager.py:310 |
| `reset` | `() -> None` | pnl_manager.py:335 |
| `on_trade_closed` | `(pnl: float, symbol: str = "") -> None` (async) | pnl_manager.py:359 |
| `on_exchange_switch` | `() -> None` | pnl_manager.py:423 |
| `get_summary` | `() -> dict` | pnl_manager.py:435 |

Private helpers: `_persist_daily_pnl` (102), `_check_new_day` (168), `_recalculate` (195).

---

## 2. can_trade() gate

Logic (pnl_manager.py:284-291):
```
def can_trade(self) -> tuple[bool, str]:
    """Quick check if trading is allowed."""
    if self._manual_pause:
        return False, f"manual pause: {self._manual_pause_reason or 'operator halt'}"
    mode = self.get_current_mode()
    if mode["mode"] == "HALTED":
        return False, mode["message"]
    return True, ""
```

Inputs consumed:
- **`self._manual_pause`** (pnl_manager.py:43) — set to True by `pause_manually(reason)` from Telegram `/pause` (pnl_manager.py:293-297). Cleared by `resume_manually()` (pnl_manager.py:299-304) and by `reset()` (pnl_manager.py:351-352).
- **`self.current_pnl_pct`** (pnl_manager.py:36) — read by `get_current_mode()` (pnl_manager.py:207). Set by `_recalculate()` (pnl_manager.py:195-202) as `(realized_pnl + unrealized_pnl) / starting_equity * 100`.
- The mode dict from `get_current_mode()` — only "HALTED" causes the gate to refuse.

`can_trade()` does **not** look at `_losses_today`, `_streak_count`, or `_max_drawdown_today` directly. They influence `_recalculate()` via realized_pnl only via `on_trade_closed()`.

Caller observability — `STRAT_PNL_GATE` line at strategy_worker.py:124-129:
```
log.info(
    f"STRAT_PNL_GATE | halted={'Y' if not can_trade else 'N'} "
    f"rsn={reason or 'ok'} pnl_pct={_gate_pnl:+.2f} "
    f"wins={_gate_wins} losses={_gate_losses} "
    f"el={_section_ms['gate']:.0f}ms | {ctx()}"
)
```
Sample (verbatim from logs):
```
2026-05-02 11:26:30.003 | INFO | src.workers.strategy_worker:tick:124 | STRAT_PNL_GATE | halted=N rsn=ok pnl_pct=+0.00 wins=0 losses=0 el=0ms | sid=s-1777721190003
2026-05-02 04:36:30.002 | INFO | src.workers.strategy_worker:tick:124 | STRAT_PNL_GATE | halted=N rsn=ok pnl_pct=+0.00 wins=5 losses=15 el=0ms | sid=s-1777696590002
```
26 STRAT_PNL_GATE lines in the 24h window; **all** show `halted=N rsn=ok` — gate has not triggered HALTED in the observed period.

---

## 3. Daily PnL tracking

### Where computed

- `_recalculate()` at pnl_manager.py:195-202:
  ```
  total_pnl = self.realized_pnl + self.unrealized_pnl
  self.current_pnl_usd = total_pnl
  if self.starting_equity > 0:
      self.current_pnl_pct = (total_pnl / self.starting_equity) * 100
  else:
      self.current_pnl_pct = 0.0
  ```

### DB queries vs in-memory

- **In-memory accumulation** for `realized_pnl`: `on_trade_closed(pnl, symbol)` at pnl_manager.py:362 does `self.realized_pnl += pnl`. Stats (`_trades_today`, `_wins_today`, `_losses_today`, `_streak_count`, `_streak_type`, `_avg_win_pct`, `_avg_loss_pct`, `_per_coin_stats`, `_total_win_pnl`, `_total_loss_pnl`) are all incremented in the same method (pnl_manager.py:363-399).
- **External fetch for unrealized PnL**: `update()` at pnl_manager.py:141-156 calls `await self.account_service.get_wallet_balance()` and pulls `account.unrealized_pnl`. The first call also captures `starting_equity = account.total_equity` if 0.
- **DB persistence (one-way write)**: `_persist_daily_pnl()` at pnl_manager.py:102-139 writes to `daily_pnl` via `INSERT OR REPLACE` keyed on `date`. Fields: `starting_equity`, `ending_equity`, `realized_pnl`, `total_trades`, `wins`, `losses`, `max_drawdown_pct`, `target_hit`, `halted`. Persists every 10 cycles (pnl_manager.py:163-166) and immediately on every trade close (pnl_manager.py:404-405) and on day rollover (pnl_manager.py:173).
- **No DB-side read of today's stats** — DailyPnLManager does not query `trade_thesis`. It maintains its own counters from `on_trade_closed` callbacks. (Contrast: PerformanceEnforcer queries `trade_thesis` directly — see J1.)

### Reconciliation with Bybit/Shadow

- Account fetch path (`update()`, pnl_manager.py:141-156) reads `account_service.get_wallet_balance()`. The actual exchange backing depends on whether `_client` is the Bybit live client or a Shadow stub — this is determined at WorkerManager wiring, not by DailyPnLManager.
- `on_exchange_switch()` (pnl_manager.py:423-433) is invoked via callback from `Transformer.register_switch_callback(...)` at workers/manager.py:1967-1969. It zeroes `starting_equity` so the next `update()` re-captures from the new exchange (no PnL carry-over).
- `realized_pnl` reconciliation: closed trades push their pnl-USD into `on_trade_closed()` from the `_callbacks_on_close` chain registered on TradeCoordinator (pnl_manager.py:359-405). The numbers are NOT cross-checked against `trade_thesis.actual_pnl_pct` or any exchange-side ledger inside the manager.

### DB snapshot reconciliation (today)

`SELECT * FROM daily_pnl ORDER BY date DESC LIMIT 5` (snapshot 11:45 UTC):
```
2026-05-02 | 0.0 | 6149.85 | -1.0025 | 29 | 5 | 24
2026-05-01 | 0.0 | 6185.09 | -0.1541 |  2 | 0 |  2
2026-04-30 | 0.0 | 6197.12 |  0.5928 |  9 | 4 |  5
2026-04-29 | 0.0 | 6206.25 |  0.0911 |  5 | 3 |  2
2026-04-28 | 0.0 | 6240.93 | -0.03   |  1 | 0 |  1
```
Live ENFORCER_STATE in J1 shows `trades=30 wins=5 losses=24 pnl=-1.00%`. The DailyPnLManager's `daily_pnl` row (29 trades / -1.00 USD realized) is **one trade behind** the enforcer's `trade_thesis`-sourced count (30 closed) at the snapshot time — consistent with `_persist_counter` only flushing every 10 cycles plus immediate-on-close, but slightly lagging if the most recent close hadn't yet propagated through the persist path or the `_persist_counter` had just rolled. starting_equity column shows 0.0 — `_persist_daily_pnl()` writes `current_equity` (line 119, sourced from a separate wallet fetch) into `ending_equity`, while `starting_equity` is never refreshed from runtime — likely persisted as 0 because the runtime field had not been initialized at persist time.

NOT FOUND — `PNL_DAILY` log lines in 24h window — searched: workers.2026-05-02_04-31-00_392071.log, workers.log via `grep "PNL_DAILY\|PNL_RESET\|PNL_LIMIT\|PNL_TRADE_ADD\|PNL_MANUAL"` → 0 hits. The `update()` method emits `PNL_DAILY` at pnl_manager.py:156 but the call-path (a worker tick that invokes `pnl_manager.update()`) is not firing in the observed window — only `STRAT_PNL_GATE` (which only consults `current_pnl_pct` and `_losses_today`/`_wins_today`) fires.

---

## 4. Loss circuit breaker

### Mode → restriction table (pnl_manager.py:204-282)

The "circuit breaker" is implemented as the seven-mode `get_current_mode()` ladder. Thresholds come from `settings.pnl_targets` (defaults at config/settings.py:1029-1033).

| Mode | Trigger | max_score_threshold | max_leverage | max_positions | allowed_risk | File:line |
|---|---|---|---|---|---|---|
| TARGET_HIT | `pct >= daily_target_pct` (default +5.0) | 90 | 2 | 1 | low | 209-219 |
| PROTECT | `pct >= protect_threshold_pct` (default +3.0) | 85 | 3 | 2 | low, medium | 220-230 |
| GOOD_DAY | `pct >= 1.0` | 55 | 5 | 3 | low, medium | 231-240 |
| NORMAL | `pct >= caution_threshold_pct` (default -1.0) | 50 | 5 | 10 | low, medium, high | 241-250 |
| CAUTION | `pct >= survival_threshold_pct` (default -3.0) | 80 | 3 | 3 | low, medium | 251-260 |
| SURVIVAL | `pct >= halt_threshold_pct` (default -5.0) | 80 | 3 | 2 | low | 261-271 |
| HALTED | `pct < halt_threshold_pct` | 100 | 0 | 0 | (none) | 272-282 |

Halt threshold field declaration: `_daily_loss_limit_pct` at pnl_manager.py:59-61 reads `getattr(settings.pnl_targets, "halt_threshold_pct", -5.0)`.

### Halt activation path

Two paths set `self.halted = True`:
1. `get_current_mode()` (pnl_manager.py:273): when `pct` falls below `halt_threshold_pct`, the HALTED branch returns and sets `self.halted = True`.
2. `on_trade_closed()` (pnl_manager.py:415-421): on each close, if `current_pnl_pct <= halt_threshold_pct` and not already halted, sets `self.halted = True` and emits `PNL_LIMIT | pnl_pct=… | limit=… | rsn=daily_loss_halt`.

Once HALTED is active, `can_trade()` returns `(False, mode["message"])` and the StrategyWorker exits its tick early at strategy_worker.py:130-132.

### Manual pause path

- `pause_manually(reason)` at pnl_manager.py:293-297 sets `_manual_pause=True` and emits `PNL_MANUAL_PAUSE | rsn='…'`.
- `resume_manually()` at pnl_manager.py:299-304 clears it and emits `PNL_MANUAL_RESUME | prev_rsn='…'`.
- `reset()` at pnl_manager.py:335-357 clears `_manual_pause`, halted, and PnL counters, emitting `PNL_MANUAL_RESET | prev_pnl=…% prev_mode=… | new_pnl=0.00% new_mode=NORMAL`.

NOT FOUND — `PNL_LIMIT`, `PNL_MANUAL_PAUSE`, `PNL_MANUAL_RESUME`, `PNL_MANUAL_RESET` events in 24h window — searched: workers.2026-05-02_04-31-00_392071.log, workers.log → 0 hits. No halt or manual-pause activity in the observed window. Live state is `halted=N rsn=ok` per STRAT_PNL_GATE.

### Consecutive-loss circuit breaker

NOT FOUND — explicit "consecutive losses" circuit breaker — searched: pnl_manager.py full file. The streak fields (`_streak_count`, `_streak_type` at pnl_manager.py:57-58) are tracked for telemetry but **not read by `can_trade()` or `get_current_mode()`**. The only loss-streak logic is in PerformanceEnforcer (`_streak_boost_threshold=-5`, performance_enforcer.py:69) which can lift enforcement level from 0→1 when streak ≤ -5 AND pnl < 0; that is enforcer-side, not pnl_manager-side.

---

## 5. apply_restrictions wiring

`apply_restrictions(setups, mode)` (pnl_manager.py:310-333) filters `EnsembleResult` setups by `mode["max_score_threshold"]` and `mode["allowed_coins"]`. It returns `[]` when `mode["mode"] == "HALTED"`.

Caller: `strategy_worker.py:681`:
```
filtered = self.pnl_manager.apply_restrictions(consensus_setups, mode)
```
The `mode` dict is fetched immediately above with `self.pnl_manager.get_current_mode()` (strategy_worker.py:680 area).
