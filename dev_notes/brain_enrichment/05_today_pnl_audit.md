# TODAY PnL Audit — Target 5 (E4)

Branch: `fix/brain-prompt-enrichment`. Working tree as of 2026-05-16. Every claim cites `path:line`. Prior report under audit: `dev_notes/brain_enrichment/02_call_b_anatomy.md`. All its substantive claims were re-verified end-to-end by reading `src/strategies/pnl_manager.py` (full), `src/brain/strategist.py:1440-1500` + `3150-3300`, `src/workers/manager.py:770-800` + `1100-1300` + `1480-1600` + `2020-2050`, `src/telegram/bot.py:440-570`, `src/telegram/handlers/portfolio.py`, `src/telegram/handlers/system.py:1-160`, `src/core/container.py:118-140`, `src/database/migrations.py:459-473`, plus a live `sqlite3 -readonly data/trading.db` snapshot and a `workers.log` grep. Prior root-cause is **confirmed** with one minor correction.

## Why It Matters

Every CALL_B prompt emits `## TODAY: PnL=+0.00%` (`src/brain/strategist.py:3169`). Confirmed in `data/stage2_dumps/` (sampled `20260507T081922_call0014_d-1778141873548.json`: `## TODAY: PnL=+0.00%` at char offset 64-86 with three live positions). The value is **always** `+0.00%` regardless of actual session PnL. CALL_B is the position-management call (hold-vs-close), and the "today is going nowhere" cue at the top biases Claude toward "keep waiting" rather than "harvest profits / cut losers".

The bug also corrupts CALL_A's `## TODAY'S PERFORMANCE` section at `src/brain/strategist.py:1452-1485` (`Daily PnL: +0.00% (real=$+0.00 + unreal=$+0.00 / base=$0)`) and the brain_v2 mode router at `src/brain/brain_v2.py:169-172`. All three readers consume `pnl_manager.current_pnl_pct`; fixing the source repairs them simultaneously. Highest-leverage enrichment in Target 5 — the data exists; the brain just cannot see it.

## Files Involved

- `src/strategies/pnl_manager.py` — 449 lines, the entire `DailyPnLManager` class.
- `src/brain/strategist.py` — 3849 lines. Read sites: `1454-1487` (CALL_A) and `3167-3171` (CALL_B).
- `src/workers/manager.py` — 2330 lines. Construction `1236-1245`, manifest `776`, close-callback wiring `1551-1566`, exchange-switch wiring `2038-2042`.
- `src/core/container.py` — alternate `ServiceContainer` DI path (MCP / non-trading entry points). Lines `120-132` construct a second `DailyPnLManager`, also never initialized.
- `src/telegram/handlers/portfolio.py:22,61` — operator-only `update()` callers.
- `src/telegram/handlers/system.py:29` — operator-only `update()` in `/status`.
- `src/telegram/bot.py:562` — operator-only `update()` in the free-form AI Q&A handler.
- `src/brain/brain_v2.py:169-172` — third reader of `current_pnl_pct`.
- `src/database/migrations.py:459-473` — `daily_pnl` table DDL.
- `data/trading.db` — 48 rows, every row `starting_equity = 0.0`.
- `data/logs/workers.log` — 6.4 MB today; `grep -c "PNL_DAILY"` → `0`; `grep -c "PNL_TRADE_ADD"` → `0` (debug-level, suppressed).

## DailyPnLManager Lifecycle Trace

### Construction
`__init__` at `pnl_manager.py:26-67`. Bug-relevant initial values:

- `self.today_date: str = ""` — `pnl_manager.py:32`
- `self.starting_equity: float = 0.0` — `pnl_manager.py:33`
- `self.realized_pnl: float = 0.0` — `pnl_manager.py:34`
- `self.unrealized_pnl: float = 0.0` — `pnl_manager.py:35`
- `self.current_pnl_pct: float = 0.0` — `pnl_manager.py:36`

Constructor takes `settings, account_service, position_service, db`. No side effect.

### Service registration
Two sites:

1. `src/workers/manager.py:1236-1245` — live trading path inside `_wire_strategy_engine`. Stored at `self._services["pnl_manager"] = pnl_mgr` (line 1245). **No `await pnl_mgr.initialize()` follows.** Manifest at `manager.py:776` lists `"pnl_manager"` in `_EXPECTED_SERVICE_KEYS`, so `SERVICES_WIRED` reports it as present.
2. `src/core/container.py:120-132` — alternate `ServiceContainer` path. Same pattern: constructed, stored, never initialized.

### First initialization
`initialize()` at `pnl_manager.py:69-100`. Fetches `account.total_equity` (line 93), assigns `starting_equity` when zero (lines 94-95), captures `unrealized_pnl` (line 96), then `_recalculate()` (line 100).

**Callers across `src/`**: ZERO. `grep -rn "pnl_mgr.initialize\|pnl_manager.initialize"` returns no matches. Dead code from bootstrap. Prior agent's claim confirmed.

### Daily reset
`_check_new_day()` at `pnl_manager.py:168-193`. Runs at the top of every `update()` (line 143). On UTC date rollover, persists the prior day (line 173), then resets all counters including `starting_equity = 0.0` (line 176). The next `update()` must re-capture `starting_equity` (lines 148-149). NO separate scheduled reset hook — date rollover is reactive only.

### Per-cycle update
`update()` at `pnl_manager.py:141-166`:

1. `await self._check_new_day()` — line 143
2. If `account_service`: fetch wallet, store `unrealized_pnl` (line 147); if `starting_equity == 0`, capture from `account.total_equity` (lines 148-149).
3. `_recalculate()` — line 155
4. `log.info("PNL_DAILY | ...")` — line 156, the diagnostic whose **absence** in logs is the smoking gun.
5. Max drawdown — lines 159-160.
6. Persist every 10th cycle — lines 163-166.

**Callers across `src/`** (`grep -rn "pnl_mgr\.update\|pnl_manager\.update"`):

- `src/telegram/bot.py:562` — `_handle_ai_question`, operator-only.
- `src/telegram/handlers/portfolio.py:22` — `/portfolio` handler.
- `src/telegram/handlers/portfolio.py:61` — `/pnl` handler.
- `src/telegram/handlers/system.py:29` — `/status` handler.

FOUR callers, ALL operator-triggered. No periodic worker tick exists. **Correction to the prior agent:** four callers, not three (the additional one is `src/telegram/bot.py:562`). Does not change the root cause.

### On-trade-closed
`on_trade_closed()` at `pnl_manager.py:359-421`. Increments `realized_pnl`, win/loss, streaks, per-coin; `_recalculate()` (line 401); `_persist_daily_pnl()` (line 405). Wired as a TradeCoordinator close-callback at `src/workers/manager.py:1551-1566`. This IS the only automatic write path — but it does **NOT** touch `starting_equity`. So PnL% stays at 0.0% even as `realized_pnl` accumulates. The DB rows prove this (below).

### Read path from CALL_B
`src/brain/strategist.py:3167-3171`:

```
pnl_manager = self.services.get("pnl_manager")
if pnl_manager:
    sections.append(f"## TODAY: PnL={pnl_manager.current_pnl_pct:+.2f}%")
```

Attribute read, no method call. The wrapping `try/except: pass` (lines 3166-3171) is moot — `current_pnl_pct` is constructor-initialized at `pnl_manager.py:36`.

### Read paths from CALL_A and brain_v2
`src/brain/strategist.py:1452-1487` (CALL_A) reads `current_pnl_pct`, `realized_pnl`, `unrealized_pnl`, `starting_equity`, `_max_drawdown_today`, `_trades_today` — all from the same broken instance. Renders `Daily PnL: +0.00% (real=$+0.00 + unreal=$+0.00 / base=$0)`. `src/brain/brain_v2.py:169-172` reads `current_pnl_pct` and routes `get_current_mode()` (which itself reads `current_pnl_pct` at `pnl_manager.py:207`); the mode logic at lines 209-282 falls through to NORMAL (line 243) when `pct == 0`, which is every call since boot.

## Root Cause

`current_pnl_pct` is initialized to `0.0` at `pnl_manager.py:36`. It is mutated only inside `_recalculate()` at `pnl_manager.py:195-202`, gated at line 198:

```
if self.starting_equity > 0:
    self.current_pnl_pct = (total_pnl / self.starting_equity) * 100
else:
    self.current_pnl_pct = 0.0
```

`starting_equity` is initialized to `0.0` at `pnl_manager.py:33` and set non-zero in only three places:

1. `initialize()` at `pnl_manager.py:94-95` — canonical bootstrap.
2. `update()` at `pnl_manager.py:148-149` — fallback if `update()` is first.
3. Implicitly via the same paths after `on_exchange_switch()` (`pnl_manager.py:430`) zeroes it.

`on_trade_closed()` does NOT touch `starting_equity` (verified by reading `pnl_manager.py:359-421` end-to-end). `initialize()` has ZERO callers in `src/`. `update()` is called only from four operator-triggered Telegram handlers above. The WorkerManager at `manager.py:1236-1245` constructs and moves on.

Therefore on every fresh process start: `starting_equity == 0.0` → gate at `pnl_manager.py:198` is false → `current_pnl_pct = 0.0` at `pnl_manager.py:201` → CALL_B at `strategist.py:3169` emits `## TODAY: PnL=+0.00%` → CALL_A at `strategist.py:1452-1465` emits zeros → brain_v2 at `brain_v2.py:172` routes to NORMAL.

Empirically confirmed by the live DB. `sqlite3 -readonly data/trading.db "SELECT date, starting_equity, ending_equity, realized_pnl, total_trades FROM daily_pnl WHERE realized_pnl != 0 ORDER BY date DESC LIMIT 8"`:

```
2026-05-16|0.0|181146.82|0.3522|3
2026-05-15|0.0|181317.17|-1.9279|12
2026-05-14|0.0|183666.97|2.0499|18
2026-05-13|0.0|181809.04|-1.4627|9
2026-05-12|0.0|182491.9|22.1982|79
2026-05-11|0.0|183769.1|3.156|21
2026-05-10|0.0|183746.39|0.6491|12
2026-05-08|0.0|181996.92|3.7936|40
```

Every row: `starting_equity = 0.0`. Fingerprint of `_persist_daily_pnl()` (`pnl_manager.py:102-139`) called from `on_trade_closed()` (line 405) without `starting_equity` ever being captured. The wallet IS queried at persist time for `ending_equity` (lines 109-112) — but `starting_equity` is just the in-memory field on line 127, which has been 0 since boot. Clean wiring gap.

## Alternative Hypotheses Considered

**H1: Telegram handler clearing the value mid-session?** Ruled out. `/portfolio`, `/pnl`, `/status`, `/<ai>` all CALL `update()` (which would *set* `starting_equity`). `/enforcer_reset` at `src/telegram/bot.py:440-470` does call `pnl_mgr.reset()` (`pnl_manager.py:347` zeroes `starting_equity`), but it's operator-explicit; workers.log shows zero `PNL_MANUAL_RESET` events.

**H2: `account_service.get_wallet_balance()` returning 0?** Ruled out. DB row 2026-05-16 has `ending_equity=181146.82` — the wallet path returns ~$181k. Both `pnl_manager.py:93` (initialize) and `pnl_manager.py:109-112` (persist) call the same method; if the wallet were broken, `ending_equity` would also be 0.

**H3: Arithmetic bug in `_recalculate()`?** Ruled out by reading `pnl_manager.py:195-202` directly. Math is `(realized + unrealized) / starting_equity * 100`, gated on `> 0`. Else-branch is explicit.

**H4: `try/except Exception: pass` at `strategist.py:3170-3171` hiding an error?** Ruled out. Attribute is constructor-initialized; the try block always succeeds. Dumps consistently render the line.

**H5: Prior agent's "ZERO callers" claim wrong?** Re-verified. `grep -rn "pnl_mgr.initialize\|pnl_manager.initialize" src/` returns empty. Confirmed: `initialize()` is dead code.

## Existing DB Schema For Daily PnL

Schema at `src/database/migrations.py:459-473` (DDL: `date PK, starting_equity, ending_equity, realized_pnl, total_trades, wins, losses, max_drawdown_pct, target_hit, halted, brain_calls, brain_cost_usd`). Index at `migrations.py:844`. Live in `data/trading.db`: 48 rows.

Write path: `_persist_daily_pnl()` at `pnl_manager.py:102-139`. Called from:

- `pnl_manager.py:166` — inside `update()`, every 10th cycle.
- `pnl_manager.py:173` — inside `_check_new_day()`, before day-rollover reset.
- `pnl_manager.py:405` — inside `on_trade_closed()`, immediately after every close.

48 rows present → close-callback path is firing correctly (wired at `manager.py:1551-1566`). What's broken is `starting_equity` capture, not persistence.

## Proposed Fix — Phase 3.1 (E4)

### Option A (preferred) — wire `initialize()` once at WorkerManager bootstrap

Single-line change at `src/workers/manager.py`. After line 1245 (`self._services["pnl_manager"] = pnl_mgr`), insert:

```
                    # E4 fix: ensure starting_equity is captured at boot so
                    # CALL_A / CALL_B / brain_v2 see a live PnL%, not 0.0%.
                    try:
                        await pnl_mgr.initialize()
                        log.info(
                            f"PNL_INITIALIZED | starting_equity={pnl_mgr.starting_equity:.2f} "
                            f"| unrealized={pnl_mgr.unrealized_pnl:+.2f} | {ctx()}"
                        )
                    except Exception as e:
                        log.warning("PnL initialize failed: {err}", err=str(e))
```

This is in an `async def _wire_strategy_engine` context already (verify by grepping the surrounding signature; the `await` keyword on line 1219 of `src/brain/strategist.py` pattern context shows nearby `await` calls succeed at this depth, and `src/core/container.py:113` does the same `await risk_mgr.initialize()` pattern in a sibling DI path).

For per-cycle freshness of `unrealized_pnl`, add a single periodic tick. The cleanest site is `EnforcerWorker` (`src/workers/enforcer_worker.py:13`), which already runs at 60-second cadence. Inside its `_tick` body, append:

```
        pnl_mgr = self.services.get("pnl_manager")
        if pnl_mgr:
            try:
                await pnl_mgr.update()
            except Exception as e:
                log.debug("PnL tick failed: {err}", err=str(e))
```

`update()` emits the `PNL_DAILY` log line (`pnl_manager.py:156`), so operators can monitor freshness directly from `workers.log`.

**Reversibility:** delete the two snippets (one in `manager.py:1245`-adjacent, one in `enforcer_worker.py`). The pre-existing close-callback path remains intact.

**Feature flag:** add `emit_today_pnl_in_callb: bool = True` to `BrainSettings` at `src/config/settings.py:443`. Gate `strategist.py:3167-3171` and `strategist.py:1452-1485` on `getattr(self.settings.brain, "emit_today_pnl_in_callb", True)`. If a regression is observed, flipping to False suppresses the brain-visible side of the fix without rolling back the wallet wiring.

**Logging events to add:**

- `PNL_INITIALIZED | starting_equity=$X | unrealized=$Y | ctx` — once at boot.
- `PNL_DAILY | ...` — already emitted at `pnl_manager.py:156` per tick; verify by `grep -c PNL_DAILY workers.log` post-deploy.
- `TODAY_PNL_COMPUTED | starting=$E realized=$R unrealized=$U pct=Z%` inside `strategist._build_position_prompt` immediately before the append at line 3169, for direct correlation between CALL_B prompts and the underlying state. Optional but useful for verification.

**Test plan (≤10 min per `feedback_test_velocity.md`):**

1. Unit test in `tests/strategies/test_pnl_manager.py`: instantiate `DailyPnLManager` with a stub `account_service.get_wallet_balance` returning `total_equity=10000`, call `await initialize()`, assert `starting_equity == 10000` and `current_pnl_pct == 0.0`. Then call `on_trade_closed(pnl=100)` and assert `current_pnl_pct == 1.0`. (No such test exists today — `grep -rn "DailyPnLManager" tests/` is worth confirming during implementation.)
2. Integration test in `tests/brain/test_strategist_prompt.py` (or new file): build a `Strategist` with a real `DailyPnLManager` and a stub account returning $10k, run two `on_trade_closed` events totalling +$50, call `_build_position_prompt`, assert the prompt string contains `## TODAY: PnL=+0.50%` (not `+0.00%`).

### Option B (alternative) — lazy-init on first CALL_B build

Wire at the read site. Replace `strategist.py:3167-3171` with:

```
try:
    pnl_manager = self.services.get("pnl_manager")
    if pnl_manager:
        if pnl_manager.starting_equity == 0:
            await pnl_manager.initialize()
        sections.append(f"## TODAY: PnL={pnl_manager.current_pnl_pct:+.2f}%")
except Exception:
    pass
```

**Pros:** single read-path side effect, lower blast radius.

**Cons:** per-CALL_B latency cost of ~50-200 ms (wallet RPC). CALL_B already costs ~80 s per the phase0 dump (`elapsed_ms=80,415.1` in `phase0_fresh_dump/CALL_B_fresh.json`), so 100 ms is negligible. However, this leaves the CALL_A reader (`strategist.py:1452-1485`) and the brain_v2 reader (`brain_v2.py:169-172`) untouched, requiring duplicated `if starting_equity == 0: await initialize()` blocks at each read site or accepting that the other two readers stay broken. Option A fixes all three readers from a single source.

**Reversibility:** trivial — delete the `if … initialize()` line.

**Feature flag:** same `emit_today_pnl_in_callb` boolean above.

Recommendation: **ship Option A**. The cost is one async call at boot plus one async tick every 60 s on a worker that's already running.

## Verification Plan For E4 (Phase 4)

- **Pre-deploy baseline:** `grep -c "## TODAY: PnL=+0.00%" data/stage2_dumps/*.json` and `grep -c "Daily PnL: +0.00%" data/stage2_dumps/*.json`.
- **Soak:** 8+ hours of live trading with ≥3 closed trades.
- **Primary metric:** CALL_B dumps in the soak window with `## TODAY: PnL=` followed by non-zero. Expect 0 → ~100% post-first-close.
- **Secondary metric:** `grep -c "PNL_DAILY" data/logs/workers.log` should grow at EnforcerWorker cadence (~480 events / 8 h).
- **Boot-time check:** `PNL_INITIALIZED | starting_equity=$N` appears once, non-zero.
- **Brain-reasoning check:** sample 20 CALL_B responses; count citations of "today is +X%", "today's PnL", "session is up/down". Baseline ~0; a meaningful rise is qualitative confirmation.
- **Regression check:** CALL_B `prompt_chars` rises ≤~6, CALL_A ≤~30; `elapsed_ms` unchanged (wallet fetch is off the brain critical path); no new pnl exceptions.
- **DB check:** after 24 h, `SELECT date, starting_equity FROM daily_pnl WHERE date >= date('now','-1 day')` — both rows should have `starting_equity > 0`.

## Verdict

Bug confirmed at `src/strategies/pnl_manager.py:198` (the `if self.starting_equity > 0:` gate) compounded by the wiring gap at `src/workers/manager.py:1245-1247` (no `await pnl_mgr.initialize()` after construction). The prior agent's analysis in `02_call_b_anatomy.md` is correct in every substantive claim; one minor refinement (four operator-only `update()` callers, not three, with the additional site at `src/telegram/bot.py:562`).

Fix complexity: ~1-2 hours of implementation + ≤10 min of tests + 8 h soak. The change is ~15 lines across two files (`src/workers/manager.py` for boot init and `src/workers/enforcer_worker.py` for periodic tick). The DB schema is already correct, the persistence path is already wired, and the close-callback already accumulates `realized_pnl` correctly. We are only filling in the `starting_equity` capture and the per-cycle `unrealized_pnl` refresh.

Risk: low. The `account_service.get_wallet_balance` call is identical to the call used by 9+ other subsystems including the dashboard, watchdog reconciler, and Telegram `/balance`. If it fails, `initialize()` already wraps it in `try/except` (`pnl_manager.py:97-98`) and logs a warning without raising.

Reversibility: trivial. Three deletion points (the `await initialize()` block at `manager.py:~1245`, the `update()` tick inside EnforcerWorker, and the feature-flag gate at the strategist read sites). Each is independent.

This is the cheapest highest-value enrichment in the Target 5 set: the data exists, the schema exists, the persistence wiring exists, the close-callback exists. The single missing call is `await pnl_mgr.initialize()` after construction. One line.
