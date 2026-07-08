# L4 — Fund Manager Forensic Data

Refreshed: 2026-05-02 ~11:45 UTC. Replaces 2026-04-28 baseline.

Source path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/fund_manager/manager.py`
NOTE: prompt referenced `src/services/fund_manager.py` — that path does NOT exist (`src/services/` is not a directory). The actual fund manager lives at `src/fund_manager/manager.py`. Class `IntelligentFundManager`.

Measured line count: **579** lines.
Reconciler source: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/fund_reconciler.py` (240 lines).

---

## 1. `IntelligentFundManager` Class & Public Methods

Class: `IntelligentFundManager` (manager.py:20).

Constructor (manager.py:30-95): wires 22 sub-modules `m1_allocator..m22_fees`. Stored:
- `self.settings`, `self.db`, `self.services` (the ServiceContainer dict)
- `self._account_state: AccountState | None`
- `_consecutive_balance_fails: int = 0`, `_consecutive_position_fails: int = 0`, `_FAIL_ALERT_THRESHOLD = 3`

22 sub-modules at construction time (manager.py:58-79):
| Module | File | Purpose |
|---|---|---|
| m1_allocator | `capital_allocator.py` | Progressive level unlock |
| m2_sizer | `position_sizer.py` | Base % + multipliers |
| m3_reserves | `capital_reserves.py` | Pool selection |
| m4_correlation | `correlation_guard.py` | Correlation multiplier |
| m5_time_pools | `time_pools.py` | Horizon-based pools |
| m6_volatility | `volatility_scaler.py` | Volatility multiplier |
| m7_rotation | `sector_rotation.py` | Sector available + coin tier |
| m8_budgets | `strategy_budgets.py` | Strategy budget |
| m9_momentum | `momentum_allocator.py` | Strategy momentum mult |
| m10_weather | `risk_weather.py` | Risk weather assessment |
| m11_velocity | `capital_velocity.py` | Capital velocity tracking |
| m12_recovery | `recovery_planner.py` | Recovery mode plan |
| m13_opportunity | `opportunity_cost.py` | Best-use check |
| m14_ratchet | `profit_ratchet.py` | Profit lock-in |
| m15_time_sync | `time_sync.py` | Time-of-day mult |
| m16_emotion | `emotion_detector.py` | Market emotion |
| m17_ecosystem | `ecosystem_health.py` | Ecosystem score |
| m18_antifrag | `anti_fragile.py` | Anti-fragile override |
| m19_loss_harvest | `loss_harvester.py` | Loss harvesting |
| m20_compound | `compound_optimizer.py` | Compound logic |
| m21_liquidity | `liquidity_mapper.py` | Liquidity gate |
| m22_fees | `fee_optimizer.py` | Min profitable trade |

Public methods:
| Method | File:Line | Signature |
|---|---|---|
| `initialize` | 96 | `(self) -> None` |
| `update_state` | 132 | `(self) -> None` |
| `get_sizing_decision` | 202 | `(self, symbol, side, setup_score, setup_grade, consensus_strength, strategy_name, strategy_category, expected_hold_minutes, stop_loss_pct) -> SizingDecision` |
| `on_trade_opened` | 458 | `(self, symbol, amount, pool, horizon) -> None` |
| `on_trade_closed` | 465 | `(self, symbol, pnl_usd, pnl_pct, was_win, amount=0, horizon=TimeHorizon.FAST) -> None` |
| `get_full_status` | 476 | `(self) -> dict` |

Private helpers: `_get_next_level_info` (525), `_get_current_streak` (534), `_get_daily_pnl_pct` (552), `_load_starting_balance` (558), `_save_starting_balance` (567).

---

## 2. Fund Pool Logic — `FUND_POOLS` Emission

Emission site: `manager.py:200` (inside `update_state`):
```python
state.available = max(0, state.trading_capital - state.in_use)
log.info(f"FUND_POOLS | cap={state.trading_capital:.2f} | available={state.available:.2f} | in_use={state.in_use:.2f} | {ctx()}")
```

### `cap` / `available` / `in_use` computation

`update_state` flow (manager.py:132-200):

1. **Balance read** (lines 146-165): `account_svc.get_wallet_balance()` -> `state.total_equity = account.total_equity`. On Exception: increments `_consecutive_balance_fails`. If `>= _FAIL_ALERT_THRESHOLD` (3), logs `FUND_MGR_BALANCE_FAIL_PERSISTENT` at ERROR (line 154). Otherwise `FUND_MGR_BALANCE_FAIL` at WARNING (line 161).
2. **Growth multiplier** (lines 168-171): `state.growth_multiplier = state.total_equity / state.starting_balance` (or 1.0 if starting==0).
3. **Level update** (line 173): `m1_allocator.update_level(state)` — sets `state.level`.
4. **Profit ratchet update** (line 174): `m14_ratchet.update(state)`.
5. **Trading capital** (line 175): `state.trading_capital = state.total_equity * (state.unlock_pct / 100)`. The `unlock_pct` is set by the level (capital_allocator.py LEVEL_CONFIG: ROOKIE 20%, PROVEN 30%, VETERAN 40%, ELITE 50%, MASTER 60%).
6. **Reserve pool update** (line 176): `m3_reserves.update_pools(state)`.
7. **Position read** (lines 178-197): `pos_svc.get_positions()` -> `state.in_use = sum(abs(p.size * p.entry_price) for p in positions)` (line 181). On Exception: same consecutive-fail mechanism with `FUND_MGR_POSITIONS_FAIL_PERSISTENT` / `_FAIL`.
8. **Available** (line 199): `state.available = max(0, state.trading_capital - state.in_use)`.
9. **Emit FUND_POOLS log** (line 200).

### Recent FUND_POOLS samples (last 24h)

```
2026-05-02 06:24:44.040 FUND_POOLS | cap=1230.28 | available=1230.28 | in_use=0.00 | no_ctx
2026-05-02 06:25:44.051 FUND_POOLS | cap=1230.28 | available=1230.28 | in_use=0.00 | no_ctx
2026-05-02 06:26:44.155 FUND_POOLS | cap=1230.19 | available=676.52 | in_use=553.67 | no_ctx
2026-05-02 06:27:44.168 FUND_POOLS | cap=1230.14 | available=676.48 | in_use=553.67 | no_ctx
2026-05-02 06:28:44.181 FUND_POOLS | cap=1230.14 | available=676.48 | in_use=553.67 | no_ctx
2026-05-02 06:29:10.312 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | tid=t-ONDOUSDT-mon wid=w-1777703349462
2026-05-02 06:29:44.193 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | no_ctx
2026-05-02 06:30:44.205 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | no_ctx
2026-05-02 06:31:44.216 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | no_ctx
2026-05-02 06:32:44.232 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | no_ctx
```

`cap ≈ 1230` matches `bybit_total ≈ 6150 × 20% unlock_pct` — consistent with ROOKIE level.

---

## 3. Reconciliation Worker

File: `src/workers/fund_reconciler.py`, class `FundReconciler(BaseWorker)` (line 43).

### Tick cadence

Settings (settings.py:1361-1370):
```python
reconcile_enabled: bool = True
reconcile_interval_seconds: int = 60
reconcile_drift_alert_threshold_pct: float = 5.0
reconcile_auto_correct: bool = False
```

Wired at `FundReconciler.__init__` (fund_reconciler.py:65-77):
```python
interval = float(getattr(settings.fund_manager, "reconcile_interval_seconds", 60))
super().__init__(name="fund_reconciler", interval_seconds=interval, ...)
```
Default 60s tick. Threshold 5.0% drift. Auto-correct OFF by default.

### Drift detection algorithm

`tick()` (fund_reconciler.py:93-205):
1. Skip with DEBUG log if `account_service` or `fund_manager` missing in services container.
2. Read Bybit-side: `account_svc.get_wallet_balance()` -> `bybit_total`, `bybit_available`. Exception -> `FUND_RECONCILE_FAIL | source=bybit` at WARNING (line 125).
3. Read local-side: `fund_manager._account_state` -> `local_total`, `local_cap`, `local_avail`. None -> `FUND_RECONCILE_FAIL | source=local reason=no_account_state` at WARNING (line 134).
4. Drift formula (line 148): `drift_pct = ((local_total - bybit_total) / bybit_total) * 100.0`. Comment at lines 144-146 documents this uses `total_equity` (single authoritative axis); `available` diverges by design (local subtracts unlock_pct + in_use).
5. Emit `FUND_RECONCILE` at INFO (line 152).
6. If `abs(drift_pct) > threshold`: emit `FUND_RECONCILE_DRIFT` WARNING + Telegram alert via `services.get("telegram").send_alert(...)` (lines 161-185).
7. If `_auto_correct == True`: overwrite `local_state.total_equity = bybit_total`, increment `_reconcile_corrections_today`, emit `FUND_RECONCILE_AUTO_CORRECT` WARNING (lines 188-202).
8. `_maybe_emit_daily_summary` (line 207): once per UTC day, emits `FUND_DAILY_SUMMARY | start_balance=... end_balance=... pnl_realized=... reconcile_corrections=...`.

### 24-hour event counts

| Event | 24h count |
|---|---|
| `FUND_RECONCILE` (INFO baseline) | 420 |
| `FUND_RECONCILE_DRIFT` (WARNING) | 0 |
| `FUND_RECONCILE_AUTO_CORRECT` | 0 |
| `FUND_RECONCILE_FAIL` | 0 |
| `FUND_MGR_BALANCE_FAIL` / `_PERSISTENT` | 0 |
| `FUND_MGR_POSITIONS_FAIL` / `_PERSISTENT` | 0 |

### 5 verbatim FUND_RECONCILE events

```
2026-05-01 00:00:01.861 FUND_RECONCILE | bybit_total=6197.12 bybit_available=6197.12 local_total=6197.12 local_cap=1239.42 local_avail=1239.42 drift_pct=+0.00 auto_correct=false | no_ctx
2026-05-01 00:01:15.924 FUND_RECONCILE | bybit_total=6197.12 bybit_available=6197.12 local_total=6197.12 local_cap=1239.42 local_avail=1239.42 drift_pct=+0.00 auto_correct=false | no_ctx
2026-05-02 04:31:41.513 FUND_RECONCILE | bybit_total=6163.30 bybit_available=6163.30 local_total=6163.30 local_cap=1232.66 local_avail=1232.66 drift_pct=+0.00 auto_correct=false | no_ctx
2026-05-02 04:32:41.526 FUND_RECONCILE | bybit_total=6163.30 bybit_available=6163.30 local_total=6163.30 local_cap=1232.66 local_avail=1232.66 drift_pct=+0.00 auto_correct=false | no_ctx
2026-05-02 04:33:41.538 FUND_RECONCILE | bybit_total=6163.30 bybit_available=6163.30 local_total=6163.30 local_cap=1232.66 local_avail=1232.66 drift_pct=+0.00 auto_correct=false | no_ctx
```

Drift consistently 0.00% — local matches Bybit exactly because local is ALSO sourced from `account_svc.get_wallet_balance()` (which proxies through transformer to Shadow's `/api/balance` in shadow mode). In paper mode, "Bybit" and "local" come from the same source so drift detection is degenerate. (This is a structural observation; not a finding to act on.)

A maximum momentary drift of +0.01% appears at 2026-05-02 06:26:42 (one tick where `bybit_total` updated 0.21 USD before local refreshed) — well under the 5% threshold.

---

## 4. Capital Allocation Strategy

### Tiered (level-based) allocation

`m1_allocator` is `CapitalAllocator` (`src/fund_manager/capital_allocator.py:60-94`). LEVEL_CONFIG dict at lines 18-65:
| Level | unlock_pct | max_leverage | max_positions | max_trade_pct | growth_threshold |
|---|---|---|---|---|---|
| ROOKIE | 20.0 | 3 | 3 | 5.0 | 1.0 |
| PROVEN | 30.0 | 4 | 5 | 7.0 | 1.5 |
| VETERAN | 40.0 | 5 | 7 | 10.0 | 2.0 |
| ELITE | 50.0 | 5 | 10 | 12.0 | 3.0 |
| MASTER | 60.0 | 5 | 10 | 15.0 | 5.0 |

Demotion thresholds (capital_allocator.py:73-75):
- `DEMOTION_DROP_PCT = 10.0` (10% drop from level-up equity)
- `CONSECUTIVE_LOSS_DAYS = 3` (3 consecutive losing days)
- `EMERGENCY_DRAWDOWN_PCT = 15.0` (15% from peak → force ROOKIE)

### Tiered-capital alternative

Comment at manager.py:93-94 and 234: "Profit floor removed (#4) — replaced by tiered capital system. See `src/fund_manager/tiered_capital.py`". Tiered tiers (tiered_capital.py file header):
- Tier 1: equity < 2x starting -> 20% usable (CONSERVATIVE)
- Tier 2: equity 2x-4x starting -> 30% usable (MODERATE)
- Tier 3: equity > 4x starting -> 40% usable (AGGRESSIVE)
- User override via Telegram supported.

### Per-coin / sector limits

Per-coin: handled by `m7_rotation` (`SectorRotation`) — `m7_rotation.get_available(symbol, state.trading_capital)` at manager.py:335 returns the sector-available cap, then `final_amount = min(final_amount, sector_available)` at line 336. Coin tier comes from `m7_rotation.get_coin_tier(symbol)` (used at line 370 for smart leverage). Detail of tier mapping is in `sector_rotation.py` (out of scope here).

Per-strategy budget: `m8_budgets.get_budget(strategy_name, state.trading_capital)` at manager.py:332.

Per-trade caps (manager.py:319-345):
- Pool available: `min(final_amount, pool_available)` (line 321)
- Level max trade %: `level_max_pct = m1_allocator.get_max_trade_pct(state.level)`; `level_max_usd = trading_capital * (level_max_pct/100)` (lines 323-325)
- Time-pool available: `min(final_amount, time_pool_available)` (line 329)
- Strategy budget: `min(final_amount, strategy_budget)` (line 333)
- Sector available: `min(final_amount, sector_available)` (line 336)
- Recovery max (if active): `recovery.max_trade_size_pct/100 * trading_capital` (lines 338-340)
- 2% per-trade-loss cap: `max_loss_allowed = trading_capital * 0.02; max_amount_for_risk = max_loss_allowed / (stop_loss_pct/100); min(final_amount, max_amount_for_risk)` (lines 342-345)
- Min profitable trade: if `final_amount < m22_fees.min_profitable_trade(symbol)` -> REJECT (lines 347-354)

Portfolio Optimizer hierarchy override (manager.py:415-427): a SQL fetch on `portfolio_allocations` table caps `final_amount` at the strategic-allocator percentage if present; failures swallowed silently (`except Exception: pass`).

Paper-trade minimum (manager.py:430-438): if `bybit.testnet` and `final_amount < 25.0`, force `final_amount = 25.0`.

### Leverage selection (manager.py:357-380)

```python
max_lev = m1_allocator.get_max_leverage(state.level)
if weather.max_leverage_override < max_lev:
    max_lev = weather.max_leverage_override
smart_lev = self.services.get("smart_leverage")
if smart_lev:
    leverage = smart_lev.calculate(symbol, direction=side, confidence=score/100, regime=None,
                                   coin_tier=..., volatility_percentile=...,
                                   ensemble_strength=consensus_strength)
    leverage = min(leverage, max_lev)
else:
    leverage = min(3, max_lev)
```

Hard global caps from settings.py:
- `risk.max_leverage: int = 3` (settings.py:515) — enforced by OrderService at order-placement time.
- `risk.max_position_size_pct: float = 10.0` (settings.py:519) — enforced by OrderService FIX 2 cap.

---

## 5. Failure Modes — ErrCode 110007

`110007` is `RC_POSITION_NOT_EXISTS`, mapped to `PositionError` at `client.py:57`:
```python
110007: PositionError,               # Position not exists
```

### Handling in fund_manager

GAP — `grep -rn "110007|PositionError" src/fund_manager/` returned **0 matches**. The fund manager itself does NOT catch `PositionError`. Position read failures are caught generically at `manager.py:183-197`:
```python
try:
    positions = await pos_svc.get_positions()
    state.in_use = sum(abs(p.size * p.entry_price) for p in positions)
    self._consecutive_position_fails = 0
except Exception as e:
    self._consecutive_position_fails += 1
    ...
```
Any `PositionError` (110007) bubbling from `pos_svc.get_positions()` would be caught here, increment `_consecutive_position_fails`, and (after 3 consecutive) emit `FUND_MGR_POSITIONS_FAIL_PERSISTENT` ERROR. `state.in_use` is NOT updated on failure (stays at last known value).

### 110007 count in last 24h

GAP — `grep -hE "110007|PositionError" data/logs/workers.*.log | grep "2026-05-0[12]"` returned **0 matches**. No 110007 events occurred in the last 24h.

### FUND_REJECT events 24h

`grep "FUND_REJECT" workers.*.log | grep "2026-05-0[12]"` returned **0 matches**. No fund-manager-side trade rejections in the last 24h.

---

## 6. DB Tables

| Table | Schema | Status |
|---|---|---|
| `fund_manager_state` | `(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))` | 4 rows present |
| `fund_manager_log` | `(id INT PK AUTOINC, event_type TEXT NOT NULL, symbol TEXT '', details_json TEXT '{}', created_at TEXT)` + INDEX `idx_fm_log` | EMPTY (0 rows in 24h) |
| `capital_level_history` | `(id INT PK AUTOINC, level TEXT, equity REAL, direction TEXT, reason TEXT '', created_at TEXT)` | not queried |

`fund_manager_state` rows in snapshot:
```
starting_equity   | 168000.0    | 2026-04-10 21:00:21
capital_override_pct | 0.5      | 2026-04-14 09:46:52
profit_ratchet    | {"total_locked": 539.97..., "equity_high": 164958.0, ...} | 2026-05-02 04:09:50
peak_equity       | 50000.0     | 2026-05-02 11:22:43
```

Note: `starting_equity` in the DB is 168000 but reconciler logs show local_total ~6151. The DB value reflects an older starting value; the live `_account_state.starting_balance` is loaded via `_load_starting_balance` (manager.py:558-565) reading from `user_preferences` table key `'starting_balance'`. There's a mismatch between which key holds the canonical starting balance (`fund_manager_state.starting_equity` vs `user_preferences.starting_balance`); manager.py only reads the latter.

---

## 7. Notes & Gaps

- Path discrepancy: prompt referenced `src/services/fund_manager.py`. That file/dir does not exist; actual location is `src/fund_manager/manager.py`.
- The `IntelligentFundManager` is constructed by `WorkerManager` and registered in the services container as `"fund_manager"`; `FundReconciler` looks it up there (fund_reconciler.py:106).
- Telegram alert path on drift (lines 171-185) uses `services.get("telegram") or services.get("telegram_bot")`. If neither is present (current paper-mode setup with telegram disabled), the alert is silently skipped via the bare except at line 182-185.
- `_account_state.starting_balance` is initialized with default `10000` if `account_svc` missing (manager.py:101-102). Init failure also defaults to 10000 (line 130).
- 22-module wiring is monolithic in `__init__`; no DI of sub-modules. Each sub-module receives `(settings, db)` or `(settings, services)` per its constructor (e.g. correlation_guard takes services for accessing position_service).
