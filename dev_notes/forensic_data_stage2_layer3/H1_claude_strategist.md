# H1 — ClaudeStrategist core

Collected: 2026-05-02 (snapshot DB: /tmp/trading_snapshot_1777722335.db)
Logs window: last 24h (2026-05-01 12:00 UTC → 2026-05-02 11:48 UTC)

## File metadata

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/brain/strategist.py`
- Lines of code: 2864
- Last modified: 2026-05-01 02:14:18 UTC
- Class: `ClaudeStrategist` (strategist.py:237)
- Module-level constants: `TRADE_SYSTEM_PROMPT` (strategist.py:65), `POSITION_SYSTEM_PROMPT` (strategist.py:150), `STRATEGIST_SYSTEM_PROMPT = TRADE_SYSTEM_PROMPT` (strategist.py:171), `BRIEFING_SYSTEM_PROMPT_SUFFIX` (strategist.py:180)
- Module-level helpers: `_safe_float` (strategist.py:32), `_safe_int` (strategist.py:50)

## Methods (one-liner each)

| line | signature | purpose |
|---|---|---|
| 240 | `__init__(self, claude_client, services: dict, settings)` | wires deps; inits `_last_regime_str/_last_regime_confidence/_last_fg_value`, `_has_urgent_concerns`, `_invalidated_positions` |
| 257 | `invalidate_position(self, symbol: str) -> None` | close-broadcast hook; stages a symbol as stale until next prompt build; emits `POSITION_INVALIDATED` + legacy `STRAT_POS_INVALIDATE` |
| 280 | `_has_blocking_price_divergence(self) -> bool` | reads `transformer._last_enrichment_max_divergence_pct` vs `settings.price.divergence_block_prompt_pct` |
| 300 | `async refresh_positions(self) -> list` | force-fetch live positions via `position_service.get_positions()`; clears `_invalidated_positions` on success |
| 328 | `async create_strategic_plan(self) -> StrategicPlan|None` | legacy combined-call entry — `_build_context_prompt`, send, parse via `_parse_plan` |
| 383 | `async review_positions(self, positions) -> dict` | 30-second compact position review |
| 412 | `async create_trade_plan(self) -> StrategicPlan|None` | **CALL A**: builds `_build_trade_prompt`, sends `TRADE_SYSTEM_PROMPT`, parses with `_parse_trade_plan` |
| 495 | `async create_position_plan(self) -> StrategicPlan|None` | **CALL B**: builds `_build_position_prompt`, sends `POSITION_SYSTEM_PROMPT`, parses with `_parse_position_plan`; defers if `_has_blocking_price_divergence()` |
| 558 | `async _build_context_prompt(self) -> str` | legacy combined market+positions prompt |
| 1240 | `_format_packages_for_prompt(self, packages: dict) -> str` | renders `CoinPackage` dict into TRADE CANDIDATES block (legacy + briefing modes) |
| 1440 | `_format_briefing_extras(self, lines: list, pkg) -> None` | Phase-6 votes block + interestingness breakdown |
| 1510 | `_format_action_hint(self, lines: list, pkg) -> None` | Phase-6 action_hint surfacing |
| 1526 | `async _build_trade_prompt(self) -> str` | **CALL A prompt builder**, target ~12-14K chars |
| 2234 | `async _build_position_prompt(self) -> str` | **CALL B prompt builder**, target 5-8K chars |
| 2407 | `_build_regime_instructions(self, regime, confidence, fear_greed) -> str` | regime-specific trading directives prepended after coaching |
| 2505 | `_build_direction_performance(self) -> str` | last-20-closed buy/sell W/L + warning text |
| 2594 | `async _build_position_review_prompt(self, positions) -> str` | compact prompt for `review_positions` |
| 2681 | `_parse_plan(self, data: dict) -> StrategicPlan` | combined parser — `new_trades`, `coin_directives`, `position_actions` |
| 2738 | `_parse_trade_plan(self, data: dict) -> StrategicPlan` | CALL A parser — `new_trades` + optional `coin_directives` |
| 2780 | `_parse_position_plan(self, data: dict) -> StrategicPlan` | CALL B parser — validates `position_actions`, downgrades invalid `tighten_stop`/`set_exit` to `hold`, emits `STRAT_CALL_B_PARSED` |

## CALL_A vs CALL_B alternation

- **Driver**: `LayerManager._brain_review_loop` (`src/core/layer_manager.py:698`).
- **Interval**: `self.brain_interval_seconds = 150` set at layer_manager.py:85; overridden by `WorkerManager` from `settings.brain.strategic_interval` (default 150) at `src/workers/manager.py:570`. Telegram `/control` can flip it to 60/180/300 (`src/telegram/handlers/control_handler.py:339-343`).
- **Loop body**: `while self._layer_active[2]: await self._run_brain_cycle(); await asyncio.sleep(150)` (layer_manager.py:712-722). Mandatory sleep — comment at 710: "do NOT reintroduce event-trigger bypasses".
- **Strict-alternation switch**: held in `self._call_type` (initial value `"A"`). After CALL_A body, the last line of the success path is `self._call_type = "B"` (layer_manager.py:874); CALL_B branch ends with `self._call_type = "A"` (layer_manager.py:935). Failure paths still flip the switch (layer_manager.py:755, 897) so a failed cycle never starves the other call type.
- **Pre-CALL_B short-circuit**: if `position_service.get_positions()` returns `[]`, layer_manager.py:884 emits `BRAIN_CYCLE_B_SKIP | rsn='no open positions'` and flips `_call_type` to `"A"` without invoking the strategist. Observed once in window — `did=d-1777720966952` at 11:26:32 (account had no positions).

### Last 20 STRAT_CALL_A_START events (last 24h)

```
2026-05-02 04:13:55.927 STRAT_CALL_A_START | did=d-1777695235927
2026-05-02 04:22:22.678 STRAT_CALL_A_START | did=d-1777695742678
2026-05-02 04:31:22.345 STRAT_CALL_A_START | did=d-1777696282345
2026-05-02 04:38:01.969 STRAT_CALL_A_START | did=d-1777696681969
2026-05-02 04:45:51.599 STRAT_CALL_A_START | did=d-1777697151599
2026-05-02 04:54:53.903 STRAT_CALL_A_START | did=d-1777697693903
2026-05-02 05:02:05.354 STRAT_CALL_A_START | did=d-1777698125354
2026-05-02 05:09:05.524 STRAT_CALL_A_START | did=d-1777698545524
2026-05-02 05:15:56.113 STRAT_CALL_A_START | did=d-1777698956113
2026-05-02 05:22:50.291 STRAT_CALL_A_START | did=d-1777699370291
2026-05-02 05:29:56.383 STRAT_CALL_A_START | did=d-1777699796383
2026-05-02 05:38:39.292 STRAT_CALL_A_START | did=d-1777700319292
2026-05-02 05:46:03.208 STRAT_CALL_A_START | did=d-1777700763208
2026-05-02 05:53:27.375 STRAT_CALL_A_START | did=d-1777701207375
2026-05-02 06:00:50.866 STRAT_CALL_A_START | did=d-1777701650866
2026-05-02 06:08:39.444 STRAT_CALL_A_START | did=d-1777702119444
2026-05-02 06:16:58.197 STRAT_CALL_A_START | did=d-1777702618197
2026-05-02 06:24:11.893 STRAT_CALL_A_START | did=d-1777703051893
2026-05-02 06:32:35.781 STRAT_CALL_A_START | did=d-1777703555781
2026-05-02 11:22:46.952 STRAT_CALL_A_START | did=d-1777720966952
```

### Last 20 STRAT_CALL_B_START events (last 24h)

```
2026-05-02 02:38:03.061 STRAT_CALL_B_START | did=d-1777689483061
2026-05-02 02:46:29.211 STRAT_CALL_B_START | did=d-1777689989211
2026-05-02 03:02:29.814 STRAT_CALL_B_START | did=d-1777690949814
2026-05-02 03:10:14.264 STRAT_CALL_B_START | did=d-1777691414264
2026-05-02 03:19:04.514 STRAT_CALL_B_START | did=d-1777691944514
2026-05-02 03:28:04.945 STRAT_CALL_B_START | did=d-1777692484945
2026-05-02 03:36:02.098 STRAT_CALL_B_START | did=d-1777692962098
2026-05-02 03:44:30.155 STRAT_CALL_B_START | did=d-1777693470155
2026-05-02 03:52:51.330 STRAT_CALL_B_START | did=d-1777693971330
2026-05-02 04:01:32.725 STRAT_CALL_B_START | did=d-1777694492725
2026-05-02 04:10:08.107 STRAT_CALL_B_START | did=d-1777695008107
2026-05-02 04:18:24.699 STRAT_CALL_B_START | did=d-1777695504699
2026-05-02 04:27:33.353 STRAT_CALL_B_START | did=d-1777696053353
2026-05-02 04:51:03.487 STRAT_CALL_B_START | did=d-1777697463487
2026-05-02 05:05:55.145 STRAT_CALL_B_START | did=d-1777698355145
2026-05-02 05:34:40.246 STRAT_CALL_B_START | did=d-1777700080246
2026-05-02 05:57:54.112 STRAT_CALL_B_START | did=d-1777701474112
2026-05-02 06:04:44.628 STRAT_CALL_B_START | did=d-1777701884628
2026-05-02 06:13:09.333 STRAT_CALL_B_START | did=d-1777702389333
2026-05-02 06:28:50.620 STRAT_CALL_B_START | did=d-1777703330620
```

(Window totals: 34 CALL_A + 20 CALL_B. The gap from 06:32 → 11:22 corresponds to a worker-process restart visible at the rolling-log boundary `workers.2026-05-02_04-31-00_392071.log` → `workers.log`.)

## `_build_trade_prompt` (CALL A)

Signature: `async def _build_trade_prompt(self) -> str:` — strategist.py:1526.

Section-by-section assembly. The `STRAT_PROMPT_BUILD` log emits per-section ms (last sample: 14 named buckets — `coaching/regime_fetch/regime_instr/dir_perf/trading_mode/universe/market_data/data_lake/xray/sentiment/regime_global/held_symbols/hints/account`).

| order | section | source service / cache | strategist.py line |
|---|---|---|---|
| 1 | `coaching` block (PERFORMANCE COACH) | `services.get("enforcer").get_coaching_text(structure_cache=...)` (`src/strategies/performance_enforcer.py:428`) | 1549-1557 |
| 2 | early regime fetch (cached) | `services.get("regime_detector").get_last_regime()` else `await detect()` | 1568-1583 |
| 3 | early Fear & Greed | `services.get("fear_greed").get_latest()` | 1585-1592 |
| 4 | REGIME-SPECIFIC TRADING INSTRUCTIONS | `_build_regime_instructions(_regime_str, _regime_confidence, _fear_greed_value)` (line 2407) | 1602-1610 |
| 5 | DIRECTION PERFORMANCE last-20 | `_build_direction_performance()` (line 2505) reads `coordinator._closed_trades` | 1615-1622 |
| 6 | TRADING MODE | `services.get("trading_mode").mode.get_claude_mode_instruction()` (`src/core/trading_mode.py:65`) | 1625-1631 |
| 7 | TRADEABLE COINS THIS CYCLE | `services.get("scanner").get_active_universe()` ∩ `SUPPORTED_SYMBOLS` (testnet filter) | 1633-1652 |
| 8 | TRADE CANDIDATES (`_format_packages_for_prompt`) | `services.get("layer_manager").get_coin_packages()` — only when `settings.brain.use_packages` (default True) | 1659-1698 |
| 9 | MARKET DATA per coin (price, %24h, RSI, MACD, ADX, [POS] tag, [REGIME] tag, VOL=class ATR%) | `market_service.get_all_linear_tickers()` bulk + per-symbol `ta_cache.analyze` H1 + `regime_detector.get_coin_regime` + `volatility_profiler.get_profile` | 1707-1812 |
| 10 | REGIME DIVERGENCE list | `regime_detector.get_coin_regime` per coin vs global | 1815-1833 |
| 11 | data lake snapshot write (side-effect) | `services.get("data_lake").write_market_snapshot(btc, eth, sol)` | 1843-1862 |
| 12 | SESSION header (`current_session`/`session_phase`/`trading_recommendation`) | `services.get("structure_cache").get_all()` first analysis with `session_context` | 1869-1891 |
| 13 | X-RAY STRUCTURAL SETUPS (top 8) + skip-coins line | `structure_cache.get_top_setups(n=8)`/`get_all()` — strength/touches/RR/FVG/OB/SWEEP/SMC/POC/FIB/MTF/CONFL | 1893-1946 |
| 14 | SENTIMENT (Fear & Greed value + classification) | already-fetched `_fg_data` | 1959-1963 |
| 15 | MARKET REGIME (CONTROLS YOUR TRADE DIRECTION) | `_regime_state` already fetched | 1969-1988 |
| 16 | HELD SYMBOLS warning OR "No open positions" | `services.get("position_service").get_positions()` | 1994-2008 |
| 17 | STRATEGY HINTS (top 20) | `services.get("layer_manager")._strategy_hints` | 2017-2025 |
| 18 | CONSENSUS PER COIN (top 15 by total_score) | `layer_manager._strategy_consensus_summary` (alias) | 2032-2049 |
| 19 | ACCOUNT (Equity, Available) + TIERED CAPITAL limits + TODAY'S PERFORMANCE (PnL%, trades) + EVENT BUFFER + URGENT QUEUE | `account_service.get_wallet_balance` / `tiered_capital.get_limits` / `pnl_manager` / `event_buffer.get_prompt_text(max_events=settings.brain.prompt_event_buffer_max_events)` / `urgent_queue.drain_concerns` | 2057-2140 |

NOT FOUND — a canonical 19-section list inside `dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md`. The file exists but does not enumerate the sections in order; the table above is reconstructed from the assembly code in `_build_trade_prompt`.

After assembly: `STRAT_PROMPT_BUILD` log at strategist.py:2149 with per-section ms; size gate at 2186 trims trailing sections when count > 80 OR chars > 14000 (emits `CLAUDE_PROMPT_TRIMMED | site=size`); final `PROMPT_BUILD_DONE` at 2223.

### 5 PROMPT_BUILD_DONE CALL_A events

```
2026-05-02 06:08:40.470 PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17115 sections=31 packages=15 elapsed_ms=1025 | did=d-1777702119444
2026-05-02 06:16:58.546 PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17192 sections=31 packages=15 elapsed_ms=348  | did=d-1777702618197
2026-05-02 06:24:12.846 PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17137 sections=31 packages=15 elapsed_ms=952  | did=d-1777703051893
2026-05-02 06:32:35.907 PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17231 sections=31 packages=15 elapsed_ms=126  | did=d-1777703555781
2026-05-02 11:22:51.485 PROMPT_BUILD_DONE | call=CALL_A coins=30 size_bytes=4077  sections=32 packages=0  elapsed_ms=4532 | did=d-1777720966952
```

The 11:22 build had `packages=0` (post-restart, before scanner_worker rebuilt the cache); workers.log emitted `BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped=2` at 11:24:01 (layer_manager.py:792).

CALL_A trim events (last 24h): the 17K-byte builds consistently exceed the 14K char cap and trip `CLAUDE_PROMPT_TRIMMED | site=size reason=chars`, e.g. `2026-05-02 05:15:57.336 CLAUDE_PROMPT_TRIMMED | site=size reason=chars sections_before=66 sections_after=31 chars_before=18948 chars_after=17278`.

## `_build_position_prompt` (CALL B)

Signature: `async def _build_position_prompt(self) -> str:` — strategist.py:2234.

Section assembly:

| order | section | source | strategist.py line |
|---|---|---|---|
| 1 | MARKET REGIME (cached `_last_regime_str`/`_last_regime_confidence` from CALL A) | self-cached | 2244 |
| 2 | SENTIMENT (cached `_last_fg_value`) | self-cached | 2247 |
| 3 | TODAY PnL line | `services.get("pnl_manager").current_pnl_pct` | 2250-2255 |
| 4 | YOUR OPEN POSITIONS header + per-position block (Entry/Now/PnL%/SL/TP/Lev/Age/Remaining/Regime/SL consumed%/Thesis/[APEX-FLIPPED]) | `refresh_positions()` then per-position: `thesis_manager.get_open_theses()`, `coordinator.get_trade_plan/get_trade_info`, `regime_detector.get_coin_regime` | 2258-2347 |
| 5 | RECENT LESSONS (5 closed-trade entries, filtered to positioned syms when available) | `thesis_manager.get_recent_lessons(limit=10)` | 2350-2369 |
| 6 | RECENTLY CLOSED with cooldowns | `coordinator._symbol_cooldowns` | 2372-2381 |
| 7 | URGENT QUEUE residue | `services.get("urgent_queue").drain_concerns()` | 2384-2392 |

Differences from CALL A:
- No market scan, no per-coin TA, no X-RAY, no strategy hints, no HELD-SYMBOLS section (positions ARE the subject), no consensus rollup.
- Caches regime/F&G from CALL A (does not re-fetch). Source for both reads: `self._last_regime_str/_last_regime_confidence/_last_fg_value` written at strategist.py:1595-1597.
- CALL B does NOT consume `_coin_packages`; reads positions directly via `refresh_positions()`.

### 5 actual CALL B prompts (size + section count)

```
2026-05-02 05:34:40.255 PROMPT_BUILD_DONE | call=CALL_B positions=2 size_bytes=1069 sections=10 elapsed_ms=8 | did=d-1777700080246
2026-05-02 05:57:54.120 PROMPT_BUILD_DONE | call=CALL_B positions=1 size_bytes=623  sections=7  elapsed_ms=7 | did=d-1777701474112
2026-05-02 06:04:44.637 PROMPT_BUILD_DONE | call=CALL_B positions=1 size_bytes=1000 sections=13 elapsed_ms=8 | did=d-1777701884628
2026-05-02 06:13:09.342 PROMPT_BUILD_DONE | call=CALL_B positions=1 size_bytes=1159 sections=14 elapsed_ms=8 | did=d-1777702389333
2026-05-02 06:28:50.628 PROMPT_BUILD_DONE | call=CALL_B positions=1 size_bytes=1067 sections=12 elapsed_ms=7 | did=d-1777703330620
```

CALL_B prompts run in 7-8 ms (no TA / X-RAY / scanner reads).

## Package consumption from `_coin_packages` cache

- Read site: strategist.py:1665 — `packages = lm.get_coin_packages()` where `lm = self.services.get("layer_manager")`.
- Format expected: `dict[str, CoinPackage]`. Each entry has `state_label`, `interestingness_score`, `opportunity_score`, `xray.setup_type`, `xray.structural_levels`, `strategies.fired_count/ensemble_consensus/total_score`, `signals.confidence/direction`, `alt_data.funding_rate/funding_signal`, `qualification_reasons`, `open_position`, `built_at`. Renderer: `_format_packages_for_prompt` (strategist.py:1240).
- Observability: `STRATEGIST_PACKAGES_READ` event at strategist.py:1684 — emits `count`, `age_min_s`, `age_max_s`, `reader=brain_call_a`.

### 5 PROMPT_BUILD events with packages count

```
2026-05-02 05:46:03.209 STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=123 age_max_s=123 | did=d-1777700763208
2026-05-02 05:53:27.377 STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=267 age_max_s=267 | did=d-1777701207375
2026-05-02 06:00:50.868 STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=110 age_max_s=110 | did=d-1777701650866
2026-05-02 06:24:11.894 STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=11  age_max_s=11  | did=d-1777703051893
2026-05-02 11:22:50.089 STRATEGIST_PACKAGES_READ | call=CALL_A count=0  age_min_s=0   age_max_s=0   | did=d-1777720966952
```

When `count=0`, `BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped=N` fires at layer_manager.py:792 (CALL A drops the trades; CALL B is unaffected because it reads positions, not packages).

## Coaching block source (TIAS feedback)

- Defined: `PerformanceEnforcer.get_coaching_text(structure_cache=None)` — `src/strategies/performance_enforcer.py:428`.
- Read site: strategist.py:1553 — `coaching = enforcer.get_coaching_text(structure_cache=_sc)`. Same logic in `_build_context_prompt` at strategist.py:565-572 (legacy combined call).
- Format (reproduced from lines 436-499):
  ```
  PERFORMANCE COACH (your stats today):
    Trades: <n> | Wins: <w> | Losses: <l>
    Win rate: <wr>% | PnL: <pct>% | Streak: <streak>
    [tier text — PROFITABLE / SLIGHTLY NEGATIVE / CAPITAL PRESERVATION MODE / RISK MANAGEMENT MODE]
    Best coin: <sym> (<pnl>%)
    Worst coin: <sym> (<pnl>%)
    Buy win rate: <pct>% | Sell win rate: <pct>%
    [WARNING: Claude heartbeat stale (>10min since last call)] (when _check_heartbeat fails)
  ```
- Sample text from last 3 prompts (the prompt body itself is not logged verbatim; CALL A directives routinely echo the coaching tier name back as the first token of `reasoning`):
  - `did=d-1777720966952` 11:24:01 → directive `"CAPITAL PRESERVATION. RSI=26 deeply oversold..."` → coaching at level 1.
  - `did=d-1777701207375` 05:55:24 → `"CAPITAL PRESERVATION. Trending_up 100% conf, ADX=51..."`.
  - `did=d-1777700763208` 05:48:27 → `"CAPITAL PRESERVATION. Trending_up regime with ADX=51..."`.
- TIAS feedback: TIAS-derived per-coin (`p_win`, profit factor) is wired into the level-2 coaching text via the `top_picks` X-RAY enrichment path (lines 458-480) and into `STRAT_POS_ACT` reasoning. Sample: `STRAT_POS_ACT | sym=AXSUSDT act=close rsn='Thesis is broken. TIAS shows 1W/7L (12% WR, PF=0.12) — historically disastrous b'` (did=d-1777701884628 06:06:09). The lessons block in CALL B (strategist.py:2350-2369) uses `thesis_manager.get_recent_lessons(limit=10)` which surfaces the `trade_thesis.lesson` column populated by TIAS analysis.

## Direction performance computer

- Location: `_build_direction_performance` — strategist.py:2505.
- Inputs: `services.get("trade_coordinator")._closed_trades` — `recent = closed[-20:]`. Each closed-trade dict has `direction`, `was_win`, `pnl_usd`.
- Outputs to prompt:
  ```
  ## DIRECTION PERFORMANCE (last 20 trades — read carefully)
    BUY/LONG: <wins>W/<losses>L (WR=<pct>%) PnL=$<usd>
    SELL/SHORT: <wins>W/<losses>L (WR=<pct>%) PnL=$<usd>
    WARNING: <DIR> DIRECTION FAILING: ... (only when n≥5 AND wr<0.40)
    RECOMMENDATION: BUY|SELL is outperforming ... (delta ≥ 15%, n≥3 each)
  ```
- Side-effect log: `STRAT_DIR_PERF | buy_n=N sell_n=M warnings=K` (strategist.py:2587).

## Trading mode manager

- Location: `src/core/trading_mode.py`. Class `TradingMode` (line 24), enum `TradingModeType` (line 18), manager `TradingModeManager` (line 112).
- Modes: `TESTNET` and `MAINNET` (TradingModeType enum, lines 18-20).
- Service key: `services["trading_mode"]`. Read by strategist at strategist.py:1626-1628 → `trading_mode_mgr.mode.get_claude_mode_instruction()`.
- Mode change mechanism: `await TradingModeManager.set_mode(mode_type)` (line 138-149). Persists to `fund_manager_state` table key `trading_mode` (line 144-147). Loaded on startup from same row (line 122-132).
- Current mode: TESTNET. The mode is initialised from `settings.bybit.testnet` (trading_mode.py:117-120). The `is_testnet = ...settings.bybit.testnet` read at strategist.py:1635 was True every CALL_A in the window (the `SUPPORTED_SYMBOLS` testnet filter at 1644-1645 was applied each cycle, capping coin universe to the curated testnet list).
- TESTNET instruction text (lines 67-75) emphasises synthetic prices ("BTC testnet might be $340,000 while real BTC is $87,000") and pins the model to in-prompt data only.

## Output parsing

- Format Claude returns: bare JSON or fenced markdown JSON. CALL_A schema per `TRADE_SYSTEM_PROMPT` line 116:
  `{"new_trades":[{"symbol","direction","stop_loss_price","take_profit_price","max_hold_minutes","leverage","size_usd","trailing_activation_pct","reasoning"}],"market_view","risk_level","max_positions","default_leverage","default_sl_pct","default_tp_pct","default_hold_minutes","trailing_activation_pct","focus_coins":[],"avoid_coins":[]}`
- CALL_B schema per `POSITION_SYSTEM_PROMPT` line 153:
  `{"position_actions":{"SYMBOL":{"action":"hold|close|tighten_stop|set_exit","new_sl":price_or_null,"exit_price":price_or_null,"reasoning"}}}`
- Parser entry sites:
  - CALL A: strategist.py:447-450 — `self.claude.extract_json(raw_response)` (`ClaudeCodeClient.extract_json` at `src/brain/claude_code_client.py:505`) then `_parse_trade_plan` (strategist.py:2738).
  - CALL B: strategist.py:531-534 then `_parse_position_plan` (strategist.py:2780).
- `extract_json` strategies (claude_code_client.py:512-556): (1) ```` ```json ... ``` ```` fence; (2) first `{` … last `}`; (3) first `[` … last `]` (wrapped as `{"decisions": [...]}`); (4) raw `json.loads`. On final failure: `CLAUDE_PARSE_FAIL | reason=json_decode err='...' raw_response='...'` (line 552), raises `ValueError`.
- Parse-failure handling: ValueError propagates to `create_trade_plan`/`create_position_plan` exception handler (strategist.py:489-493 / 552-556) which emits `STRAT_CALL_A_FAIL` / `STRAT_CALL_B_FAIL` and returns `None`. Layer manager logs `BRAIN_CYCLE_A_FAIL` / `BRAIN_CYCLE_B_FAIL` (layer_manager.py:751-756 / 893-898) and flips `_call_type` to the other call so the next cycle proceeds.
- `_parse_position_plan` defensive logic (strategist.py:2780-2864):
  - Valid actions: `{"hold","close","tighten_stop","set_exit","take_profit"}` (line 2804).
  - Unknown action → downgraded to `"hold"` with `STRAT_CALL_B_BAD_ACTION_TYPE` warning (line 2820-2826).
  - `tighten_stop` with `new_sl<=0` → `"hold"` + `STRAT_CALL_B_DOWNGRADE` (line 2831).
  - `set_exit` with `exit_price<=0` → `"hold"` + `STRAT_CALL_B_DOWNGRADE` (line 2837).
  - Non-dict `position_actions` → `STRAT_CALL_B_BAD_ACTIONS` warning, returns empty plan.
  - Final emit `STRAT_CALL_B_PARSED | total=N hold=A close=B tighten=C set_exit=D take_profit=E` (line 2857).

### 3 actual Claude responses verbatim with parsing path

The strategist does NOT log the full raw response on success — only `out=<chars>` length on `CLAUDE_CALL_OK`. Verbatim raw text is captured ONLY on parse failure. The single parse-failure raw text in the 24h window (truncated by the warning logger):

```
2026-05-02 05:10:56.106 CLAUDE_PARSE_FAIL | reason=json_decode err='Expecting value: line 1 column 1 (char 0)' raw_response='System status check blocked by permissions. Here's the situation:'
```

(Claude CLI returned a refusal instead of JSON for did=d-1777698545524. Followed by `STRAT_CALL_A_FAIL` at 05:10:56.107 and `STRAT_CALL_A_END | el=110583ms trades=0 failed=Y`.)

For two non-failing examples we capture the structured derivative (parsed core fields):

Example #1 — did=d-1777720966952 (parse path: extract_json → _parse_trade_plan):
```
STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime with fear at 39. Account in critical drawdown — pure capit'
STRAT_DIRECTIVE   | #1 sym=DYDXUSDT dir=Buy lev=2 rsn='CAPITAL PRESERVATION. RSI=26 deeply oversold in ranging global regime = textbook'
STRAT_DIRECTIVE   | #2 sym=MONUSDT  dir=Buy lev=2 rsn='CAPITAL PRESERVATION. ADX=50 strong trend + RSI=55 healthy momentum zone + MEDIU'
```

Example #2 — did=d-1777703051893 (parse path: extract_json → _parse_trade_plan):
```
STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime with fear sentiment (39). Asian late session with low volu'
STRAT_DIRECTIVE   | #1 sym=ONDOUSDT dir=Buy lev=2 rsn='STRONG ensemble 76.7, highest buy consensus (6.0 votes) across all candidates. R'
STRAT_DIRECTIVE   | #2 sym=NEARUSDT dir=Sell lev=2 rsn='GOOD ensemble 62.4, strong sell votes (3.45). A+ x-ray setup, pos=82% near range'
```

Example #3 — did=d-1777701884628 (parse path: extract_json → _parse_position_plan; CALL B):
```
STRAT_CALL_B_PLAN | acts=1
STRAT_POS_ACT     | sym=AXSUSDT act=close rsn='Thesis is broken. TIAS shows 1W/7L (12% WR, PF=0.12) — historically disastrous b'
```

NOT FOUND — full verbatim Claude JSON text bodies in last 24h. Searched: `raw_response`, `Raw response`, `response='`, `Brain v2 raw response` in /tmp/h_collect/brain_24h.log and /tmp/h_collect/workers_24h.log. The brain only logs the first 100-200 chars of `raw_response` on parse failure; successful CALL A/B raw text is consumed by `extract_json` and never re-emitted at INFO level. Only summary fields (`market_view` first 80-200 chars, `STRAT_DIRECTIVE` reasoning prefix 80 chars, `STRAT_POS_ACT` reasoning prefix 80 chars) are persisted to log + the `claude_decisions` table.

## Failure modes (last 24h grep)

| tag | count | sample |
|---|---:|---|
| `CLAUDE_CALL_FAIL` | 0 | — |
| `CLAUDE_PARSE_FAIL` | 1 | `2026-05-02 05:10:56 reason=json_decode err='Expecting value: line 1 column 1 (char 0)' raw_response='System status check blocked by permissions...'` |
| `CLAUDE_CALL_TIMEOUT` | 0 | — |
| `CLAUDE_PROC_STALL` (legacy DEBUG tag) | 66 | DEBUG-level (not surfaced in WARNING grep alone) |
| `CLAUDE_PROC_STALL_60S` | 50 | `2026-05-02 06:14:09 pid=16852 elapsed=60s stdout_so_far=0 timeout_in_s=240` |
| `CLAUDE_PROC_STALL_120S` | 16 | `2026-05-02 05:31:58 pid=14380 elapsed=120s stdout_so_far=0 timeout_in_s=180 state=S wchan=ep_poll` |
| `CLAUDE_PROC_STALL_240S` | 0 | — |
| `BRAIN_CYCLE_A_FAIL` | 0 | — |
| `BRAIN_CYCLE_B_FAIL` | 0 | — |
| `STRAT_CALL_A_FAIL` | 1 | `2026-05-02 05:10:56 err='Cannot extract JSON from response:...' did=d-1777698545524` (caused by the parse-fail above) |
| `STRAT_CALL_B_FAIL` | 0 | — |
| `STRAT_PLAN_FAIL` | 0 | — |
| `BRAIN_NO_PACKAGES` | 1 | `2026-05-02 11:24:01 reason=empty_packages_cache trades_dropped=2 did=d-1777720966952` (post-restart) |

PROC_STALL_60S firings are informational (Claude CLI takes 60-90 s of stdout silence on most successful calls by design — stalls log at INFO, per claude_code_client.py:1195-1200). PROC_STALL_120S WARNINGs occur when total subprocess wall time exceeds 120 s — most calls in the window run 60-130 s and a third of them breach 120 s. None reached 240 s (the SIGKILL pre-warning). No `CLAUDE_PROC_PREKILL`, no `CLAUDE_PROC_KILLED`, no `BRAIN_FAILURE_CASCADE`. No `CLAUDE_AUTH` failures — but one preflight `CLAUDE_PREFLIGHT_REFRESH` fired at 11:22:51 (`mins_left=-82.7`, refresh recovered to 480 min).
