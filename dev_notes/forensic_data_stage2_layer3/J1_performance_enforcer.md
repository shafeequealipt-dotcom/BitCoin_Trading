# J1 — PerformanceEnforcer

Collection timestamp: 2026-05-02 ~11:45 UTC
DB snapshot: /tmp/trading_snapshot_1777722335.db
Log files searched: workers.2026-05-02_04-31-00_392071.log (active, 4.5 MB), workers.log (current symlink, 320 KB)

---

## 1. File location & size

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/performance_enforcer.py`
- Lines of code: **577** (`wc -l`)
- Class: `PerformanceEnforcer` at performance_enforcer.py:31

### Public methods (16)

| Method | Signature | File:line |
|---|---|---|
| `__init__` | `(self, settings, db, services: dict)` | performance_enforcer.py:37 |
| `is_trading_halted` | `() -> bool` | performance_enforcer.py:87 |
| `should_allow_trade` | `(leverage: int = 1) -> tuple[bool, str]` | performance_enforcer.py:91 |
| `get_max_positions_override` | `() -> int \| None` | performance_enforcer.py:110 |
| `get_min_score_override` | `() -> int \| None` | performance_enforcer.py:118 |
| `get_size_multiplier` | `() -> float` | performance_enforcer.py:126 |
| `qualify_survival_trade` | `(symbol, structure_cache=None) -> tuple[bool, str]` | performance_enforcer.py:151 |
| `check_and_enforce` | `() -> dict` (async) | performance_enforcer.py:198 |
| `get_coaching_text` | `(structure_cache=None) -> str` | performance_enforcer.py:428 |
| `on_signal_generated` | `() -> None` | performance_enforcer.py:503 |
| `on_setup_sent_to_brain` | `() -> None` | performance_enforcer.py:506 |
| `on_trade_executed` | `() -> None` | performance_enforcer.py:509 |
| `on_trade_closed` | `(pnl_pct: float, was_win: bool) -> None` | performance_enforcer.py:514 |
| `get_urgency_level` | `() -> int` | performance_enforcer.py:534 |
| `get_status` | `() -> dict` | performance_enforcer.py:542 |
| `reset` | `() -> None` | performance_enforcer.py:563 |

Private helpers: `_check_recovery` (181), `_build_report` (289), `_get_level_change_reason` (303), `_collect_stats` (317), `_check_heartbeat` (380), `_check_day_reset` (411).

---

## 2. Role in pipeline

Module docstring (performance_enforcer.py:1-20) declares: *"Enforcer v2 — PnL-Based Intelligent Throttling. Primary signal: Daily PnL %. Secondary signal: Loss streak (only when PnL is already negative). Manages trade INTENSITY, never halts. Full halt is delegated to DailyPnLManager."*

Three enforcement levels (performance_enforcer.py:11-13):
- **Level 0 NORMAL**: PnL ≥ 0% — trade freely
- **Level 1 CAPITAL_PRESERVATION**: PnL < -2% — max 3 positions, max 3x leverage
- **Level 2 SURVIVAL**: PnL < -5% — max 2 positions (default `level_2_max_positions=2`), max 3x leverage (default `level_2_max_leverage=3`); quality-gate (A+/A) replaces BTC/ETH-only

Position in pipeline (verified by call-site grep):
- **Tick driver**: `EnforcerWorker` runs `enforcer.check_and_enforce()` on a 300 s default interval (enforcer_worker.py:15-22).
- **Pre-execution**, the enforcer participates in directive filtering via:
  - layer_manager.py:1219 — `should_allow_trade(leverage=1)` (LayerManager pre-execution check)
  - strategy_worker.py:1158 — `should_allow_trade(leverage=_lev)` (StrategyWorker before signal handoff)
  - strategy_worker.py:1175 — `qualify_survival_trade(symbol, _sc)` (L2 quality gate)
- **The enforcer does NOT sit between APEX and the order gate** in the runtime sense. APEX `TradeGate.validate()` (apex/gate.py:48) makes its own checks; it does **not** read the enforcer. The brief's "after APEX, before Gate" framing does not match the wiring — the enforcer affects sizing/coaching upstream and the leverage/quality gate at strategy-worker level, not between APEX and the order-side Layer 3 gate.

---

## 3. Performance stats collection

Implemented in `_collect_stats()` at performance_enforcer.py:317-378.

Source: **DB query** against `trade_thesis` (`status='closed' AND DATE(closed_at)=today`):

```
performance_enforcer.py:321-328
rows = await self.db.fetch_all(
    """SELECT symbol, direction, actual_pnl_pct, close_reason, exchange_mode
       FROM trade_thesis
       WHERE status = 'closed' AND DATE(closed_at) = ?
         AND (close_reason IS NULL OR close_reason != 'transformer_switch')
       ORDER BY closed_at DESC""",
    (today,),
)
```

Computed in-memory from the row set:
- `_trades_today` (line 330), `_wins_today` (331), `_losses_today` (333), `_profit_today_pct` (334).
- Streak detection (336-352): walks rows newest-first, +1 per consecutive win, –1 per consecutive loss; breakeven (pnl == 0) is skipped — does not break or extend streak.
- `_per_coin` dict (355-366), `_per_direction` dict (369-376).

Exception path emits `ENFORCER_STATS_FAIL | err='...' | {ctx()}` at line 378.

### ENFORCER_STATS event verification (gap from prior collection)

**Gap CONFIRMED.** No event named `ENFORCER_STATS` exists in source.

- Codebase grep (`grep -rn "ENFORCER_STATS" src/`): 1 hit — `ENFORCER_STATS_FAIL` at performance_enforcer.py:378 (error-only).
- Logs grep (`grep "ENFORCER_STATS" workers.2026-05-02_04-31-00_392071.log workers.log`): 0 hits.

The actual emitted event for stats is `ENFORCER_STATE` at performance_enforcer.py:280-285, fired by `check_and_enforce()` after `_collect_stats()` runs. 5 sample events (verbatim):

```
2026-05-02 06:22:41.141 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=29 | wins=5 | losses=23 | wr=0.17 | strk=-12 | pnl=-0.90% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
2026-05-02 06:29:41.174 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=30 | wins=5 | losses=24 | wr=0.17 | strk=-13 | pnl=-1.00% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
2026-05-02 11:22:45.990 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=30 | wins=5 | losses=24 | wr=0.17 | strk=-13 | pnl=-1.00% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
2026-05-02 11:31:46.066 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=30 | wins=5 | losses=24 | wr=0.17 | strk=-13 | pnl=-1.00% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
2026-05-02 11:47:46.136 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=30 | wins=5 | losses=24 | wr=0.17 | strk=-13 | pnl=-1.00% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
```

Companion `ENFORCER_BEAT` (enforcer_worker.py:24) sample:
```
2026-05-02 11:22:45.991 | INFO | src.workers.enforcer_worker:tick:24 | ENFORCER_BEAT | total=30T W=5 L=24 wr=16.7% strk=-13 hb=OK | no_ctx
```

NOT FOUND — `ENFORCER_STATS` (the named event from the task brief) — searched: workers.2026-05-02_04-31-00_392071.log, workers.log, src/. Confirmed: it does not exist; the emit is named `ENFORCER_STATE`.

NOT FOUND — `_collect_stats` elapsed_ms — searched: performance_enforcer.py:317-378. The method does not log its own elapsed time; only the parent `check_and_enforce()` writes the post-collect summary line, which doesn't break out the SELECT cost.

DB snapshot validates the in-log numbers (trade_thesis closed today=2026-05-02): 30 closed rows.

---

## 4. Coaching generation

Builder: `get_coaching_text(structure_cache=None) -> str` at performance_enforcer.py:428-499.

Format (line-by-line from the source):
- Header: `"PERFORMANCE COACH (your stats today):"` (436)
- Trades line: `f"  Trades: {self._trades_today} | Wins: {self._wins_today} | Losses: {self._losses_today}"` (437-439)
- Win-rate line: `f"  Win rate: {wr:.0%} | PnL: {pnl:+.2f}% | Streak: {streak:+d}"` (440)
- One of four "Session" lines depending on `(level, pnl)` (442-480):
  - `level==0 and pnl>=0`: "Session: PROFITABLE. Trade normally with full conviction…"
  - `level==0 and pnl<0`: "Session: SLIGHTLY NEGATIVE. Position sizes reduced to {sz_mult:.0%}…"
  - `level==1`: "CAPITAL PRESERVATION MODE. Max 3 positions, leverage capped at 3x. Only A+ setups with strong consensus. Protect capital."
  - `level==2`: "RISK MANAGEMENT MODE. Max {l2_max_pos} positions, leverage {l2_max_lev}x. Quality-gate: A+/A setups only with confluence>={l2_min_confluence} and RR>={l2_min_rr}…"
- Best/worst coin lines (482-486)
- Buy/Sell win-rate line (488-494)
- Optional heartbeat-stale warning (496-497)

Consumers: `brain/strategist.py:564-568` and `:1549-1553` call `enforcer.get_coaching_text(structure_cache=_sc)` and inject the string into Claude's prompt.

### 3 coaching outputs verbatim from logs

NOT FOUND — coaching text in logs — searched: workers.2026-05-02_04-31-00_392071.log, workers.log via `grep "PERFORMANCE COACH\|capital preservation\|RISK MANAGEMENT"` (0 hits). The string is built by `get_coaching_text()` and embedded directly into Claude's prompt; it is **not** log-emitted (no `log.info(...)` call in the function body, performance_enforcer.py:428-499).

For reference, what coaching text WOULD be produced from the current state (level=1, pnl=-1.00%, trades=30, wins=5, losses=24, streak=-13) per the format above:
```
PERFORMANCE COACH (your stats today):
  Trades: 30 | Wins: 5 | Losses: 24
  Win rate: 17% | PnL: -1.00% | Streak: -13
  CAPITAL PRESERVATION MODE. Max 3 positions, leverage capped at 3x. Only A+ setups with strong consensus. Protect capital.
  Best coin: <best> ({pnl:+.2f}%)
  Worst coin: <worst> ({pnl:+.2f}%)
  Buy win rate: ##% | Sell win rate: ##%
```
This is reconstruction, not a verbatim log capture.

---

## 5. Strategy filtering

The PerformanceEnforcer does **not** itself filter strategies by win-rate threshold. The "failing-strategy threshold" lives elsewhere in the codebase. The enforcer's filtering surface is via:
- `should_allow_trade(leverage)` — performance_enforcer.py:91-108 (blocks trades whose leverage exceeds level cap).
- `get_max_positions_override()` — performance_enforcer.py:110-116 (returns int cap or None).
- `get_min_score_override()` — performance_enforcer.py:118-124 (returns minimum score, default 80 at L1 / L2).
- `qualify_survival_trade(symbol, structure_cache)` — performance_enforcer.py:151-179 (rejects setups with quality<A, confluence<l2_min_confluence, or rr<l2_min_rr).

NOT FOUND — failing-strategy threshold inside performance_enforcer.py — searched: full file, grep "fail|disable|threshold". The min-score caps (level_1_min_score=80, level_2_min_score=80, performance_enforcer.py:75,78) are general signal-quality gates, not per-strategy disablers.

---

## 6. _trades_today

Set by `_collect_stats()` at performance_enforcer.py:330:
```
self._trades_today = len(rows)
```
…where `rows` is the result of the trade_thesis SELECT shown in §3.

NOT FOUND — `elapsed_ms` for the `_collect_stats()` SELECT — searched: performance_enforcer.py:317-378. The query is not timed; only the parent `check_and_enforce()` writes a post-collect summary line, which doesn't break out the SELECT cost.

DB-side timing reference (snapshot probe): `SELECT COUNT(*) FROM trade_thesis WHERE status='closed' AND DATE(closed_at)='2026-05-02'` returned 30 rows; consistent with the in-log `trades=30` field.

In-memory increments are intentionally **not** done on `on_trade_executed()` (performance_enforcer.py:509-512):
```
def on_trade_executed(self) -> None:
    # No in-memory increment — _collect_stats() is authoritative from DB.
    # Incrementing here would cause double-counting until the next stats cycle.
    pass
```

---

## 7. apply_restrictions(consensus_setups, mode)

The PerformanceEnforcer does **not** define `apply_restrictions`. That method belongs to `DailyPnLManager` (pnl_manager.py:310-333). It is called from `strategy_worker.py:681`:
```
filtered = self.pnl_manager.apply_restrictions(consensus_setups, mode)
```
See J2 (PnL Manager) for the seven modes (TARGET_HIT / PROTECT / GOOD_DAY / NORMAL / CAUTION / SURVIVAL / HALTED) and per-mode `max_score_threshold` thresholds (90/85/55/50/80/80/100).

The enforcer's "modes" are the three enforcement **levels** (NORMAL/PRESERVATION/SURVIVAL). They are computed in `check_and_enforce()` at performance_enforcer.py:241-258:

```
performance_enforcer.py:241-258
# ── Primary signal: Daily PnL ──
new_level = 0
if pnl >= 0:
    new_level = 0
elif pnl > self._pnl_caution_pct:        # 0% to -2%
    if streak <= self._streak_boost_threshold:
        new_level = 1   # PnL negative AND long streak = real problem
    else:
        new_level = 0
elif pnl > self._pnl_survival_pct:       # -2% to -5%
    new_level = 1   # Capital preservation
else:                                    # below -5%
    new_level = 2   # Survival
```

Level → score override mapping (`get_min_score_override`, performance_enforcer.py:118-124):
- `el >= 2` → `_l2_min_score` (config default 80)
- `el >= 1` → `_l1_min_score` (config default 80)
- otherwise → None (no override)

Level → max-positions mapping (`get_max_positions_override`, performance_enforcer.py:110-116):
- `el >= 2` → `_l2_max_pos` (config default 2)
- `el >= 1` → `_l1_max_pos` (config default 3)
- otherwise → None

Level → leverage gate (`should_allow_trade`, performance_enforcer.py:91-108):
- `el >= 2 and leverage > _l2_max_lev` (default 3) → block with reason `SURVIVAL: leverage=… exceeds limit of 3x`
- `el >= 1 and leverage > _l1_max_lev` (default 3) → block with reason `PRESERVATION: …`

Size-multiplier (`get_size_multiplier`, performance_enforcer.py:126-149):
- `pnl >= 0%` → 1.0
- `0 to -2%` → `_size_reduction_factor` (default 0.75)
- `-2% to -5%` → 0.50
- `< -5%` → 0.25 (or 0.40 / 0.50 if `_recovery_stage` ≥ 1 / ≥ 2)

---

## 8. Live state observed (24 h window)

- Enforcer level pinned at **el=1 (CAPITAL_PRESERVATION)** for the entire log span 04:31 → 11:48 UTC.
- One `ENFORCER_LEVEL` transition observed: 11:22:45.990 — `old_el=0 new_el=1 reason=streak_boost pnl=-1.00% strk=-13` (performance_enforcer.py:265-268). ENFORCER_STATE rows from 06:22 onward already show el=1, so a level reset must have happened between 06:32 (last 06:xx line) and 11:22 (first 11:xx line) — likely a process restart (no `ENFORCER_AUTO_RECOVERY` or `ENFORCER_MANUAL_RESET` event in 24h).
- `ENFORCER_TRADE_IN`: observed once at 06:29:10.278 — `pnl=-0.10 win=N strk=-13 recovery=0` (performance_enforcer.py:529-532).
- `ENFORCER_AUTO_RECOVERY`: 0 events in 24h.
- `ENFORCER_MANUAL_RESET`: 0 events in 24h.
- `ENFORCER_GRACE`: 0 events in 24h.
- `ENFORCER_STATS_FAIL`: 0 events in 24h.

---

## 9. Wiring summary

| Site | Purpose | File:line |
|---|---|---|
| `EnforcerWorker.tick()` | Drives `check_and_enforce()` every 300 s (configurable) | enforcer_worker.py:19-26 |
| `LayerManager._dispatch_claude_trades` | Calls `should_allow_trade(leverage=1)` for blanket level-2 leverage gate | layer_manager.py:1219 |
| `StrategyWorker._execute_claude_trade` (entry path) | `should_allow_trade(leverage=_lev)` → block if leverage exceeds level cap | strategy_worker.py:1158 |
| `StrategyWorker._execute_claude_trade` (entry path) | `qualify_survival_trade(symbol, _sc)` → reject low-quality during L2 | strategy_worker.py:1175 |
| `Strategist._build_prompt` (brain) | Pulls `get_coaching_text(structure_cache=_sc)` into Claude's prompt | strategist.py:564-568, :1549-1553 |
| `OrderService` | Does NOT consult enforcer — Layer 3 gate is the only OrderService-level gate | order_service.py:142-397 |
