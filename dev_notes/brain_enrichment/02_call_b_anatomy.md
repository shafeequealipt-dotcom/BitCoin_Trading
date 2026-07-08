# CALL_B Anatomy — `_build_position_prompt` end-to-end

Branch: `fix/brain-prompt-enrichment`. All citations are `path:line` against the current working tree. Total lines in `src/brain/strategist.py`: 3,849 (verified); `src/strategies/pnl_manager.py`: 449. Reference dump: `dev_notes/brain_enrichment/phase0_fresh_dump/CALL_B_fresh.json` — `prompt_chars=2,009`, `system_prompt_chars=1,783`, `response_chars=861`, `elapsed_ms=80,415.1`, `prompt_hash=5fc2bd829ec2`. Three open positions in the dump: KATUSDT Sell, ORCAUSDT Buy, ETHUSDT Sell.

## Files Involved

`_build_position_prompt` lives at `src/brain/strategist.py:3150-3390` and is invoked from `create_position_plan` at `src/brain/strategist.py:783-844` (the `await self._build_position_prompt()` call site is line 814). Pre-call divergence gate at `strategist.py:789-811` deferring the cycle when `_has_blocking_price_divergence()` (defined at line 490) returns True.

Services consulted during the build, each pulled from `self.services.get(...)`:

- `pnl_manager` (`DailyPnLManager`) — `strategist.py:3167-3169`. Reads `current_pnl_pct` attribute directly (no method call).
- `thesis_manager` — `strategist.py:3207`. `get_open_theses()` at line 3225.
- `position_service` — `strategist.py:3208`. `get_positions()` at line 3221 (post-refresh fallback).
- `trade_coordinator` — `strategist.py:3209`. `get_trade_plan(symbol)` at line 3247; `get_trade_info(symbol)` at line 3248; `_symbol_cooldowns` at line 3352-3358 for the recently-closed cooldown footer.
- `regime_detector` (`_rd`) — `strategist.py:3210`. `get_coin_regime(symbol)` at line 3243.
- `urgent_queue` — `strategist.py:3364`. `drain_concerns()` + `format_for_prompt()` at lines 3366-3368.
- `refresh_positions()` self method — `strategist.py:3219` (defined at line 510-536). Calls `position_service.get_positions()` again with a freshness contract that drains `_invalidated_positions`.

Cached state used (no per-call fetch):

- `self._last_regime_str`, `self._last_regime_confidence`, `self._last_fg_value` — written by `_build_trade_prompt` at lines 2310-2312 from the same-cycle regime + fear-greed fetches. CALL_B reads them at `strategist.py:3160, 3163`. This means CALL_B's regime/sentiment are stale by the CALL_A → CALL_B interval (typically 1-3 minutes in production).

NOT touched in CALL_B: `scanner`, `market_service`, `ta_cache`, `volatility_profiler`, `layer_manager`, `structure_cache`, `account_service`, `tiered_capital`, `event_buffer`, `data_lake`, `enforcer`.

## System Prompt Map

One variant: `POSITION_SYSTEM_PROMPT` at `strategist.py:162-178`. Length 1,783 chars (matches dump's `system_prompt_chars=1,783` exactly). Used unconditionally at `strategist.py:817` (`await self.claude.send_message(prompt, POSITION_SYSTEM_PROMPT)`). No briefing suffix, no urgent override, no zero_two variant — CALL_B has a single system prompt.

Sentinel constant `POSITION_SYSTEM_PROMPT_VERSION = 2` at `strategist.py:184`. Boot-time `STRAT_CALL_B_REFRAMED` log emission at lines 460-463 verifies the in-memory version on each restart; the docstring at `strategist.py:144-161` explains the version-2 reframe (CALL_B Framing Fix Phase 1B, 2026-05-06) removed the regime-reversal and thesis-broken close rules.

The dump confirms version 2 is live: system prompt opens with "You are managing open crypto futures positions. Your aim is to maximize the development of each position. Aggressive opportunity exploitation, not capital preservation." which matches `strategist.py:162-163` exactly. Numbered rules 1-7 match `strategist.py:164-178` byte-for-byte.

## CALL_B User Prompt Section-By-Section Map

Char costs measured against `phase0_fresh_dump/CALL_B_fresh.json` (total 2,009 chars).

### 1. MARKET REGIME — chars 0-32, cost 32

- Source: `strategist.py:3160`. `## MARKET REGIME: <regime> (<conf>%)`.
- Data source: `self._last_regime_str` / `self._last_regime_confidence` — cached during the preceding CALL_A at `strategist.py:2310-2311`.

### 2. SENTIMENT — chars 32-64, cost 32

- Source: `strategist.py:3163`. `## SENTIMENT: Fear & Greed = <value>`.
- Data source: `self._last_fg_value` — cached at `strategist.py:2312`.

### 3. TODAY: PnL — chars 64-86, cost 22

- Source: `strategist.py:3167-3171`. `## TODAY: PnL=<+0.00>%`.
- Data source: `pnl_manager.current_pnl_pct` (attribute read at line 3169). Wrapped in `try/except: pass` — if `pnl_manager` is absent, the section is silently omitted.
- This is the BUG section. See "TODAY: PnL Line — Critical Investigation Focus" below.

### 4. YOUR OPEN POSITIONS header — chars 86-172, cost 86

- Source: `strategist.py:3174`. `\n## YOUR OPEN POSITIONS — Review each and decide: hold, close, tighten_stop, set_exit`. Newline-prefixed.

### 5. CONTRACT — POSITION MANAGEMENT — chars 172-1,236, cost 1,064

- Source: `strategist.py:3184-3205`. Static text block injected next to position data per CALL_B Framing Fix Sub-phase 1D (2026-05-06). Listed actions, close-only criteria, anti-close gates, and FLIPPED-position guidance.
- Data source: none — entirely static. The block is hardcoded in the method body.
- Trim priority: not subject to size trim (CALL_B has no trim logic; see section below).
- Occupies 53% of the entire CALL_B prompt body. The actual per-position data accounts for only 524 chars; the contract restatement and headers consume the remainder.

### 6. Per-position blocks — chars 1,236-1,760, cost 524 (3 positions × ~175 chars each)

Each block emitted via the `for pos in positions` loop at `strategist.py:3230-3331`. Per-position emission at `strategist.py:3284-3291`:

```
### {symbol} [{side_val}]
  Entry: ${entry:.2f} | Now: ${mark:.2f} | PnL: {pnl_pct:+.2f}%
  SL: ${sl:.2f} | TP: ${tp:.2f} | Lev: {leverage}x
  Age: {age:.0f}min | Remaining: {remaining:.0f}min | Regime: {rgm_str}
  SL consumed: {sl_consumed:.0f}%
```

Field sources:

- `pos.entry_price`, `pos.mark_price` — fields on the Position object from `position_service.get_positions()` (line 3219/3221).
- `pnl_pct` — computed inline at `strategist.py:3235-3240`: `(mark - entry) / entry * 100`, sign-flipped for Sell/Short.
- `sl_price`, `tp` — read from `thesis_data.get("stop_loss_price"...)` / `thesis_data.get("take_profit_price"...)` (line 3272, 3287). `thesis_data` from `thesis_manager.get_open_theses()` (line 3225).
- `leverage` — `thesis_data.get("leverage", '?')` line 3288.
- `age`, `remaining` — from `coordinator.get_trade_plan(symbol).age_minutes` and `.remaining_minutes` at line 3249-3250.
- `rgm_str` — `regime_detector.get_coin_regime(symbol)` at line 3243, formatted as `{regime_value.upper()} {confidence*100:.0f}%`.
- `sl_consumed` — direction-aware computation at `strategist.py:3273-3282`: `moved = max(0, price_against_entry)`, clamped to `total_risk = abs(entry - sl)`, capped at 100%.

### 7. FLIPPED notice (conditional) — NOT in dump

Source: `strategist.py:3292-3331` (CALL_B Framing Fix Phase 1E). Emits `FLIPPED via XRAY` or `FLIPPED via APEX` with concrete RR justification when `thesis_data.get("xray_flip_source")` or `thesis_data.get("apex_flipped")` is set. None of the three positions in the fresh dump carry flip metadata, so this block is absent. Path is verified by reading the conditional gate at line 3302.

### 8. "No open positions" fallback — NOT in dump

Source: `strategist.py:3333-3334`. Emitted when `positions` is empty. Not relevant to this dump (three positions present).

### 9. RECENTLY CLOSED cooldowns (conditional) — NOT in dump

Source: `strategist.py:3352-3361`. Reads `coordinator._symbol_cooldowns` and emits one line per symbol whose cooldown has not expired. No cooldown entries in this cycle; block omitted.

### 10. URGENT queue (conditional) — NOT in dump

Source: `strategist.py:3363-3372`. `urgent_queue.drain_concerns()` + `format_for_prompt(concerns)`. Empty in this cycle.

### Sentinel: RECENT PERFORMANCE — present in dump (chars 1,760-2,009, cost 249) — NOT FOUND in current source

The dump emits:

```
## RECENT PERFORMANCE (last 50 closes — directional pattern only)
WR: 46% (23W / 27L)  |  Net PnL: $+84.10
By close reason: wd_dl_action 13 (W 85%) | bybit_sl_hit 12 (W 42%) | wd_claude_action 11 (W 9%) | system_close 8 (W 38%) | wd_timeout 3 (W 0%)
```

This footer is NOT FOUND in current `src/brain/strategist.py` on branch `fix/brain-prompt-enrichment`. Grep across the working tree (`src/brain/`, `src/core/`, `src/strategies/`) returns zero hits for the string "RECENT PERFORMANCE", "directional pattern only", "Net PnL", or "wd_dl_action" as code. Git archaeology locates the originator: commit `5e26007` "feat(t1-3/phase3c): strategist — pass closed-loop guards + aggregated stats block" on branch `fix/five-critical-fixes-2026-05-11`. That commit:

- Added `format_aggregated_stats_for_prompt(stats: dict) -> str` in `src/core/thesis_manager.py` which emits exactly the "## RECENT PERFORMANCE (last {count} closes — directional pattern only)" header, the WR line, and the "By close reason" line.
- Added `get_aggregated_stats(limit_closes=50)` in `src/core/thesis_manager.py`.
- Wired both into `_build_position_prompt` after the per-position loop with the closed-loop-immunity comment.

That commit is on a different branch and has NOT been merged into `fix/brain-prompt-enrichment`. Current `src/core/thesis_manager.py` is 314 lines and contains neither `format_aggregated_stats_for_prompt` nor `get_aggregated_stats`. Therefore the fresh dump's RECENT PERFORMANCE footer cannot have come from the current code path.

Reading order in the dump matches the comment placement in commit `5e26007` (after the per-position blocks, before the cooldown/urgent tail). The dump prompt was captured during a live run; if the live run was on a different branch from the working tree, this discrepancy explains it. Mark as: NOT FOUND in current branch source; present in commit `5e26007` on `fix/five-critical-fixes-2026-05-11`; emission verified in the dump.

## TODAY: PnL Line — Critical Investigation Focus

Known bug: every CALL_B prompt emits `## TODAY: PnL=+0.00%` regardless of actual daily PnL. The fresh dump confirms (line 11, char offset 64-86 of the `prompt` field).

### Prompt-side emission

Exact source at `src/brain/strategist.py:3166-3171`:

```python
try:
    pnl_manager = self.services.get("pnl_manager")
    if pnl_manager:
        sections.append(f"## TODAY: PnL={pnl_manager.current_pnl_pct:+.2f}%")
except Exception:
    pass
```

The append is gated on `pnl_manager` truthiness only. The value read is `pnl_manager.current_pnl_pct` — an attribute, not a method. Whatever value the attribute holds at the moment of read is what ships in the prompt. The bare `except Exception: pass` silently swallows any AttributeError if the attribute is missing, but the attribute is initialized to 0.0 in the constructor so this never triggers.

### `pnl_manager.current_pnl_pct` lifecycle

Defined and initialized at `src/strategies/pnl_manager.py:36`:

```python
self.current_pnl_pct: float = 0.0
```

Set in two places only:

1. `_recalculate` at `pnl_manager.py:195-202`:

   ```python
   def _recalculate(self) -> None:
       total_pnl = self.realized_pnl + self.unrealized_pnl
       self.current_pnl_usd = total_pnl
       if self.starting_equity > 0:
           self.current_pnl_pct = (total_pnl / self.starting_equity) * 100
       else:
           self.current_pnl_pct = 0.0
       self._max_drawdown_pct = self._max_drawdown_today
   ```

   Critical gate at line 198: when `starting_equity == 0`, `current_pnl_pct` is forced to 0.0. The division is skipped.

2. `reset()` at `pnl_manager.py:335-357` — manually called from Telegram `/enforcer_reset`. Sets `current_pnl_pct` to 0.0 (via `_recalculate` after zeroing the inputs).

`_recalculate` is invoked from:

- `initialize()` at `pnl_manager.py:100`.
- `update()` at `pnl_manager.py:155`.
- `on_trade_closed()` at `pnl_manager.py:401`.
- `on_exchange_switch()` at `pnl_manager.py:432`.
- `reset()` at `pnl_manager.py:353`.

### `starting_equity` lifecycle

Initialized to 0.0 at `pnl_manager.py:33`. Set only in two paths:

1. `initialize()` at `pnl_manager.py:94-95`:

   ```python
   account = await self.account_service.get_wallet_balance()
   if self.starting_equity == 0:
       self.starting_equity = account.total_equity
   ```

2. `update()` at `pnl_manager.py:148-149`:

   ```python
   if self.starting_equity == 0:
       self.starting_equity = account.total_equity
   ```

Both paths require `account_service.get_wallet_balance()` to succeed and require `initialize()` or `update()` to be CALLED.

### Caller analysis — smoking gun

`initialize()` (`pnl_manager.py:69`) is defined but grep across `src/` shows ZERO callers. Confirmed by `grep -rn "pnl_mgr.initialize\|pnl_manager.initialize\|await pnl.initialize\|pnl.initialize"` in src — empty. `WorkerManager._wire_strategy_engine` constructs `DailyPnLManager` at `src/workers/manager.py:1236-1245` and stashes it in `self._services["pnl_manager"]`, but never calls `initialize()`.

`update()` (`pnl_manager.py:141`) has callers only in operator-triggered Telegram handlers:

- `src/telegram/bot.py:562` — `/pnl` handler.
- `src/telegram/handlers/portfolio.py:22, 61` — portfolio handler.
- `src/telegram/handlers/system.py:29` — system handler.

There is NO periodic worker that calls `pnl_manager.update()`. No scheduler entry, no `PnLWorker`, no async loop. The cron-style call is absent.

`on_trade_closed()` (`pnl_manager.py:359`) is registered as a TradeCoordinator close-callback at `src/workers/manager.py:1551-1566`. This is the ONLY automatic path that touches `_recalculate` after boot. But `on_trade_closed` does NOT fetch the wallet — it only increments `realized_pnl` by the passed `pnl` value (line 362) and recalculates. Crucially, `_recalculate` will return `current_pnl_pct = 0.0` whenever `starting_equity == 0`, which is the boot state because nothing ever called `initialize()`.

### Workers log confirmation

`PNL_DAILY` is emitted inside `update()` at `pnl_manager.py:156`:

```python
log.info(f"PNL_DAILY | realized={self.realized_pnl:+.2f} | unrealized={self.unrealized_pnl:+.2f} | pnl_pct={self.current_pnl_pct:+.2f} | trades={self._trades_today} | wins={self._wins_today} | losses={self._losses_today} | {ctx()}")
```

Current `data/logs/workers.log` shows `grep -c PNL_DAILY` = 0. Zero PNL_DAILY events ever emitted in the live workers log. This is consistent with `update()` never being called.

### Definitive root-cause hypothesis

`pnl_manager.current_pnl_pct` stays at 0.0 for the entire lifetime of the process because:

1. `WorkerManager._wire_strategy_engine` constructs `DailyPnLManager` at `manager.py:1236-1241` and registers it as a service at `manager.py:1245`, but never calls `await pnl_mgr.initialize()`.
2. No periodic worker (`PnLWorker`, scheduler entry, or cron-tick) calls `pnl_mgr.update()` after boot. The only `update()` callers are operator-triggered Telegram paths.
3. `pnl_mgr.on_trade_closed()` is registered as a close-callback (`manager.py:1551-1566`) and DOES run on every close. But `on_trade_closed` never calls `_check_new_day` followed by a wallet fetch; it only adds to `realized_pnl` and calls `_recalculate`.
4. `_recalculate` checks `self.starting_equity > 0` at `pnl_manager.py:198`. Since `starting_equity` is initialized to 0 (`pnl_manager.py:33`) and only `initialize()` or `update()` fetch a wallet balance to set it, the gate fails. `current_pnl_pct` is forced to 0.0 (`pnl_manager.py:201`).
5. CALL_B reads `pnl_manager.current_pnl_pct` at `strategist.py:3169` and faithfully emits `## TODAY: PnL=+0.00%`.

The fix requires wiring a periodic `await pnl_mgr.update()` (or a one-shot `await pnl_mgr.initialize()` followed by per-cycle `update()`) into a worker. Most natural site is `WorkerManager` setup: `await pnl_mgr.initialize()` after construction at `manager.py:1245`. For ongoing freshness, a periodic tick from `EnforcerWorker` or a new lightweight `PnLWorker` calling `await pnl_mgr.update()` every 45-60 s would emit `PNL_DAILY` and keep `current_pnl_pct` aligned with actual realized + unrealized + wallet equity.

Note: `on_trade_closed` could be patched to call `_check_new_day` + wallet fetch to set `starting_equity` lazily on the first close. That would close the gap from "first trade closes" onward but leaves the pre-first-trade window broken. The clean fix is the periodic worker tick.

## Position Serialization

`positions` list obtained at `strategist.py:3219` via `await self.refresh_positions()` (defined at line 510-536). The helper does:

1. Calls `position_service.get_positions()` at line 521.
2. On success, drains `self._invalidated_positions` (a set populated by `invalidate_position(symbol)` at line 467-488, fed by the close-broadcast hub).
3. Emits `STRAT_PROMPT_REFRESH` (line 531-534).

Fallback at `strategist.py:3220-3221`: if `refresh_positions` returns empty AND `position_service` is wired, re-fetch directly.

Each `Position` has attributes accessed in the loop:

- `pos.symbol` — line 3231.
- `pos.side.value` (with `str(pos.side)` fallback) — line 3232.
- `pos.entry_price`, `pos.mark_price` — line 3236.

These match the Position dataclass exported by `position_service.get_positions()`. The `thesis_manager.get_open_theses()` result is built into a `{symbol: thesis_dict}` map at `strategist.py:3222-3228` and used to fetch SL/TP/leverage/flip metadata per symbol (line 3264-3331).

The "no open positions" guard at line 3333-3334 emits a fallback only when the loop produced zero rendered blocks.

## URGENT Injection Mechanism

Definition at `src/core/urgent_queue.py:35-118`. CALL_B drain at `strategist.py:3363-3372`:

```python
urgent_queue = self.services.get("urgent_queue")
if urgent_queue and urgent_queue.has_concerns:
    concerns = urgent_queue.drain_concerns()
    if concerns:
        urgent_text = urgent_queue.format_for_prompt(concerns)
        sections.append(urgent_text)
        log.info(
            f"STRAT_CALL_B_URGENT | injected={len(concerns)} | {ctx()}"
        )
```

`has_concerns` is a property at `urgent_queue.py:98`. `drain_concerns()` at `urgent_queue.py:79` returns the queued list and clears the queue. `format_for_prompt(concerns)` at `urgent_queue.py:111` renders the prompt-ready text.

In CALL_B the urgent block is appended at the tail (after the per-position loop, after cooldowns). Unlike CALL_A which sets a sticky `_has_urgent_concerns` flag to add an OVERRIDE addendum to the system prompt (CALL_A path at `strategist.py:2961-2973` + `692-702`), CALL_B simply appends the rendered text — no system-prompt mutation.

The fresh dump has no URGENT block — `urgent_queue.has_concerns` was False this cycle.

## Recent Performance Footer

NOT FOUND in current `src/brain/strategist.py` on `fix/brain-prompt-enrichment`. The dump shows it because the dump was captured on a different code path (commit `5e26007` on `fix/five-critical-fixes-2026-05-11` is the originator; see Section 10 above for the git archaeology).

If/when the T1-3 / F9 commit is brought into `fix/brain-prompt-enrichment`, the footer would render between the per-position loop and the cooldown block (commit `5e26007` placed the append at the `_tias_lessons_removed = True` site in `_build_position_prompt`, which corresponds to `strategist.py:3349` in the current code). The data source would be `thesis_manager.get_aggregated_stats(limit_closes=50)` (defined in commit `e318e51`, NOT FOUND in current `src/core/thesis_manager.py`), then rendered by `format_aggregated_stats_for_prompt(stats)` from the same commit.

The footer's data shape is closed-loop-immune by design — no symbol or per-trade narratives, only aggregate WR / Net PnL / close-reason distribution.

## Verdict

(a) Sections that work — currently rendered with real data: MARKET REGIME (cached from CALL_A), SENTIMENT (cached from CALL_A), YOUR OPEN POSITIONS header, CONTRACT — POSITION MANAGEMENT (static text), per-position blocks (entry / now / PnL / SL / TP / Lev / Age / Remaining / Regime / SL consumed). FLIPPED notice renders correctly when v28 thesis metadata is present.

(b) Gated off / removed sections: RECENT LESSONS block (Post-Execution Closure Fix Phase 1A, 2026-05-05). Source comment at `strategist.py:3336-3349` documents the removal; `thesis_manager.get_recent_lessons` is still defined but not called from CALL_B. The `_tias_lessons_removed = True` sentinel at line 3349 is logged in `STRAT_CALL_B_CTX` (line 3380) for regression detection.

(c) Dead code with no callers in the live CALL_B path: none in `_build_position_prompt` itself; the per-position block is fully wired. `review_positions` (line 593-618) and `_build_position_review_prompt` (line 3579-3664) are a separate code path (30-second watchdog review) not invoked by `create_position_plan`.

(d) Fetched but unused fields: `thesis_data` carries the full thesis row but only specific fields (`stop_loss_price`, `take_profit_price`, `leverage`, `xray_flip_*`, `apex_flipped`, `apex_original_direction`, `apex_reason`) are rendered. The free-text `thesis` column is intentionally NOT read per the CALL_B Framing Fix Phase 1C comment at `strategist.py:3252-3263` (the original thesis text contradicts flipped positions). MAE/MFE columns, time_to_breakeven, lesson text, and TIAS analysis fields on the row are not surfaced.

(e) TODAY: PnL root cause: `DailyPnLManager.initialize()` is never called, no periodic worker invokes `update()`, `on_trade_closed` does not refresh `starting_equity`, so `_recalculate` always hits the `starting_equity == 0` branch at `src/strategies/pnl_manager.py:198-201` and returns `current_pnl_pct = 0.0`. CALL_B faithfully emits the 0.0 value at `src/brain/strategist.py:3169`. Workers log confirms zero `PNL_DAILY` events emitted. Fix requires either `await pnl_mgr.initialize()` at `src/workers/manager.py:1245` immediately after construction, or a periodic `await pnl_mgr.update()` tick from a worker (most natural placement: `EnforcerWorker` already runs every 60 s per `feedback_layer1_restructure.md` references; extending it to also tick `pnl_mgr.update()` would emit `PNL_DAILY` and keep the prompt line truthful).
