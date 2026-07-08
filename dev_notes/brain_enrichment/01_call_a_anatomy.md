# CALL_A Anatomy — `_build_trade_prompt` end-to-end

Branch: `fix/brain-prompt-enrichment`. All citations are `path:line` against the current working tree. Total lines in `src/brain/strategist.py`: 3,849 (verified). Reference dump: `dev_notes/brain_enrichment/phase0_fresh_dump/CALL_A_fresh.json` — `prompt_chars=16,831`, `system_prompt_chars=6,724`, `response_chars=2,735`, `elapsed_ms=205,533.6`, `prompt_hash=976db2d64eac`.

## Files Involved

`_build_trade_prompt` lives at `src/brain/strategist.py:2235-3146` and is invoked from `create_trade_plan` at `src/brain/strategist.py:622-781` (the `await self._build_trade_prompt()` call site is line 667). The orchestrator additionally references the system prompts defined at the top of the same file: `TRADE_SYSTEM_PROMPT` at lines 65-141, `BRIEFING_SYSTEM_PROMPT_SUFFIX` at lines 196-250, and `TRADE_SYSTEM_PROMPT_ZERO_TWO` at lines 267-326.

Services consulted during the build, each pulled from `self.services.get(...)` constructor-injected by `WorkerManager._wire_strategist` at `src/workers/manager.py:1236-1263`:

- `enforcer` (`PerformanceEnforcer`) — `src/brain/strategist.py:852` (used by the dead `_build_context_prompt` only; the live `_build_trade_prompt` no longer touches it, see lines 2257-2273).
- `regime_detector` — `strategist.py:2284, 2511, 2566, 2605` (fetched twice; once via `get_last_regime()` for global, once stored as `_rd` for per-coin tags).
- `fear_greed` — `strategist.py:2301` via `get_latest()`.
- `scanner` — `strategist.py:2374`, `get_active_universe()` at `strategist.py:2379`.
- `market_service` — `strategist.py:2375, 2519, 2532, 2641`. Bulk fetch `get_all_linear_tickers()` at line 2519 (30 s cache).
- `ta` or `ta_cache` — `strategist.py:2376`; `analyze()` per symbol at `strategist.py:2536`.
- `volatility_profiler` — `strategist.py:2377, 2574`.
- `thesis_manager` — `strategist.py:2372, 2502` for `get_open_theses()` (used to compute the `[POS]` tag set).
- `layer_manager` — `strategist.py:2399, 2807-2839`. `get_coin_packages()` at line 2401; `_strategy_hints` at line 2809; `_strategy_consensus_summary` alias at line 2822.
- `data_lake` — `strategist.py:2634, 2650` (writes a market snapshot side effect; no prompt contribution).
- `structure_cache` — `strategist.py:2659-2741` for the X-RAY block.
- `position_service` — `strategist.py:2785, 2891`; `get_positions()` at lines 2787 and 2894.
- `account_service` — `strategist.py:2858`; `get_wallet_balance()` at line 2860.
- `tiered_capital` — `strategist.py:2886`; `get_limits()` at line 2899.
- `event_buffer` — `strategist.py:2935, 2944`. `get_prompt_text(max_events=…)` at line 2944.
- `urgent_queue` — `strategist.py:2963`. `drain_concerns()` at line 2965 and `format_for_prompt()` at line 2967.
- `transformer` — accessed indirectly via `_has_blocking_price_divergence` at `strategist.py:499`; relevant to CALL_B not CALL_A.

`StateLabeler` is imported on-demand inside the package formatter at `strategist.py:1601-1605` and `strategist.py:1880-1884` for the `LABEL_NO_TRADEABLE_STATE` constant. The action-hint table `ACTION_HINTS` is imported at `strategist.py:1806`.

## System Prompt Map

Two base variants exist, selected at `strategist.py:677-681`:

- `TRADE_SYSTEM_PROMPT` (`strategist.py:65-141`) — legacy `2-4 trades` aggressive-exploit contract. Mid-2026-05-05 reframe; 80 numbered/headered lines. Char count of the source string is approximately 4,940 chars after the docstring stripping. This is the prompt used when `[stage2].enable_zero_two_contract = False`.
- `TRADE_SYSTEM_PROMPT_ZERO_TWO` (`strategist.py:267-326`) — strict bounded-count rewrite of the same. Char count approximately 3,250 chars. Used when `[stage2].enable_zero_two_contract = True`.

`config.toml:278` sets `enable_zero_two_contract = true`. Confirmed in the fresh dump: the system_prompt opens with "Your aim is to exploit the current market situation…" and contains "Return between 2 and 4 trades" with "When zero trades qualify, return new_trades: []" — these are the `_ZERO_TWO` variant markers from `strategist.py:278, 311`.

`BRIEFING_SYSTEM_PROMPT_SUFFIX` (`strategist.py:196-250`) is appended at `strategist.py:688-691` when `[brain].surface_briefing_fields` is True. Settings default is `True` (`src/config/settings.py:492`) and `config.toml:204` sets `surface_briefing_fields = true`. The fresh dump confirms the suffix is concatenated: the `system_prompt` ends with the "═══ BRIEFING-MODE FIELDS" header through the "regime-aware second opinion on each coin's state." closing line (these strings are anchored at `strategist.py:198, 249`). Suffix char cost ≈ 2,310 chars.

Optional urgent addendum (`strategist.py:692-702`) is concatenated only when `self._has_urgent_concerns` is set; it adds "OVERRIDE — URGENT WATCHDOG ALERTS:" 543 chars. The fresh dump does NOT contain this addendum (concerns flag was False this cycle).

`system_prompt_chars = 6,724` reconciles with `_ZERO_TWO` (~3,250) + `BRIEFING_SYSTEM_PROMPT_SUFFIX` (~2,310) + interstitial newlines plus the small ZERO_TWO rule block at the tail. Branch selection is deterministic: log line `STRAT_AGGRESSIVE_FRAMING` (`strategist.py:712-718`) records `zero_two_flag` per call.

## CALL_A User Prompt Section-By-Section Map

Char costs measured against `phase0_fresh_dump/CALL_A_fresh.json` via section-boundary regex (total 16,831 chars). Sections are appended in the order below.

### 1. Global regime line — chars 0-57, cost 57

- Source: `strategist.py:2334-2338`. Emits `Global regime: <regime> (confidence=NN%, Fear & Greed=NN)`.
- Data source: `regime_detector.get_last_regime()` at line 2288, fallback `await regime_detector.detect()` at line 2293; `fear_greed.get_latest()` at line 2303.
- Trim priority: matched by no marker, defaults to OPTIONAL (`strategist.py:404-411` + `_infer_section_priority` at 414-436). Index 0, however, is hard-pinned ESSENTIAL via the index==0 short-circuit at `strategist.py:427-428`.
- Cannot be elided when present.

### 2. TRADEABLE COINS — chars 57-293, cost 236

- Source: `strategist.py:2384-2388`. Format: header + comma-separated universe + reminder line.
- Data source: `scanner.get_active_universe()` at line 2379, then filtered by `SUPPORTED_SYMBOLS` from `src/config/constants` (line 2381 when testnet flag is on).
- Trim priority: ESSENTIAL via the `"TRADEABLE COINS THIS CYCLE"` marker (`strategist.py:351`).

### 3. TRADE CANDIDATES (full Layer 1B/1C evidence) — chars 293-12,048, cost 11,755

- Source: `strategist.py:2462-2487`. The actual rendering is delegated to `_format_packages_for_prompt_full` (`strategist.py:1814-2233`) when `[stage2].enable_full_layer_block = True` else `_format_packages_for_prompt` (`strategist.py:1528-1726`). Config has `enable_full_layer_block = true` at `config.toml:277` — confirmed by the dump header `"## TRADE CANDIDATES (full Layer 1B/1C evidence; open-position coins included for HR-2 management)"` which matches the `_full` header at `strategist.py:1900-1901`.
- Data source: `layer_manager.get_coin_packages()` at line 2401. Each `CoinPackage` is then enriched per coin via direct queries to:
  - `structure_cache.get(symbol)` at `strategist.py:1964` for XRAY 12-phase block (lines 1973-2035).
  - `signal_worker.get_signal(symbol)` at `strategist.py:2045` for the per-component signal breakdown (lines 2055-2087).
  - `regime_detector.get_coin_regime(symbol)` at `strategist.py:2106` for per-coin RegimeState (lines 2116-2135).
  - `_format_briefing_extras(coin_lines, pkg)` at line 2157 which calls `layer_manager.get_strategy_votes(symbol)` at `strategist.py:1739` (Top BUY / Top SELL block) and reads `pkg.interestingness_breakdown` for the top-3 components line.
  - `layer_manager.get_scorer_components(symbol)` at `strategist.py:2168` for the TradeScorer 4-component breakdown (line 2173-2180).
  - `pkg.alt_data` for the Funding line (line 2190-2195).
  - `_format_action_hint(coin_lines, pkg)` at line 2201 reading `ACTION_HINTS` from `state_labeler`.
- Coin cap: `[stage2].top_n_to_brain` (default 10 per `src/config/settings.py:560`, current config 10). Pinning logic at `strategist.py:2436-2461` keeps open-position coins ahead of (interestingness, opportunity)-sorted fillers. Cap log: `STRAT_TOP_N_APPLIED` (`strategist.py:2455-2459`).
- Trim priority: ESSENTIAL via `"## TRADE CANDIDATES"` marker (`strategist.py:347`).
- Char attribution per coin: dump shows 10 coins rendered (`ORCAUSDT`, `KATUSDT`, `DYDXUSDT`, `FILUSDT`, `ALICEUSDT`, `ETHUSDT`, `XRPUSDT`, `MANAUSDT`, `SOLUSDT`, `MNTUSDT`), averaging ~1,170 chars each. Each per-coin block carries header + XRAY + Structure + (optional SMC) + MTF + Volume profile + Session + Levels + Signal + Components + Regime + Active categories + Strategies + Votes + State + Score + Funding + Action hint — 16-18 lines per coin on the full renderer path.

### 4. MARKET DATA — chars 12,048-13,649, cost 1,601

- Source: `strategist.py:2498-2625`. Header at line 2498 (`"## MARKET DATA"`), per-symbol lines at 2591-2594.
- Data source: bulk ticker `market_service.get_all_linear_tickers()` at line 2519, per-symbol `ta_cache.analyze()` at line 2536 for RSI/MACD/ADX, `volatility_profiler.get_profile(symbol)` at line 2574, `regime_detector.get_coin_regime(symbol)` at line 2566. Skip-filter at lines 2557-2563: `abs(change) > 3.0 or rsi < 30/>70 or adx > 30 or is_major or has_position`.
- Per-coin emission shape (`strategist.py:2591-2594`): `SYM [tag][regime][vol]: $price (±X% 24h) RSI=NN MACD_hist=N ADX=NN min=$N`.
- The `## REGIME DIVERGENCE` sub-section at `strategist.py:2616-2623` is included in this MARKET DATA block only when divergent coins are found. The fresh dump has zero divergent coins (regime is "ranging" with no opposite-direction per-coin overrides), so this sub-block is absent from the dump.
- Trim priority: ESSENTIAL via `"## MARKET DATA"` marker (`strategist.py:344`).

### 5. SESSION — chars 13,649-13,804, cost 155

- Source: `strategist.py:2673-2681`. Format: `## SESSION: <SESSION> (<phase>) | Nmin elapsed, Nmin remaining \n recommendation \n Next: <next> in Nmin`.
- Data source: `structure_cache.get_ranked_setups()` at line 2661; falls back to scanning `structure_cache.get_all()` at line 2668 for any analysis with a `session_context`.
- Trim priority: OPTIONAL via `"## SESSION"` marker (`strategist.py:406`).

### 6. X-RAY STRUCTURAL SETUPS — chars 13,804-16,032, cost 2,228

- Source: `strategist.py:2683-2736`. Header at line 2685; per-setup line built at 2686-2725; skip-coins summary at 2733-2734.
- Data source: `structure_cache.get_top_setups(n=8)` at line 2683 for the ranked list; `structure_cache.get_all()` at line 2728 for the skip tail. Each setup contributes a one-line summary of S/R levels, market structure, position-in-range, RR, FVG, OB, sweep signal, SMC confluence, POC, fib level, MTF score, total confluence factors, and setup quality.
- Trim priority: OPTIONAL via `"## X-RAY STRUCTURAL SETUPS"` marker (`strategist.py:407`).

### 7. SENTIMENT — chars 16,032-16,070, cost 38

- Source: `strategist.py:2749-2753`. Header + single line: `Fear & Greed: <value> (<classification>)`.
- Data source: `fear_greed.get_latest()` result captured earlier at line 2303 (cached in `_fg_data`).
- Trim priority: OPTIONAL via `"## SENTIMENT"` marker (`strategist.py:405`).

### 8. MARKET REGIME — chars 16,070-16,174, cost 104

- Source: `strategist.py:2759-2778`. Header + Global line; optional high-confidence trending_down NOTE at lines 2773-2778.
- Data source: `regime_detector.get_last_regime()` result (cached `_regime_state`). The direction-hint dict at lines 2761-2767 maps regime string to single-phrase guidance.
- Trim priority: ESSENTIAL via `"## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)"` marker (`strategist.py:354`).

### 9. HELD SYMBOLS / no-positions line — chars 16,174-16,233, cost 59

- Source: `strategist.py:2783-2796`. Either `"\nHELD SYMBOLS … : <list>"` (with rejection reminder) or `"\nNo open positions — you can trade any coin from the list."`.
- Data source: `position_service.get_positions()` at line 2787.
- Trim priority: no explicit marker; defaults to OPTIONAL. The legacy non-priority trim path won't reach it because trim only fires above the 14,000-char cap.

### 10. STRATEGY HINTS — chars 16,233-16,598, cost 365

- Source: `strategist.py:2803-2841`. Header + intro lines (2804-2806); up to 20 hint lines at 2810-2815 (`{strategy}: {symbol} {direction} score={score} {consensus}`).
- Data source: `layer_manager._strategy_hints` at line 2809 (populated by `StrategyWorker._build_strategy_hints` and assigned at `src/workers/strategy_worker.py:966`).
- Trim priority: IMPORTANT via `"## STRATEGY HINTS"` marker (`strategist.py:397`).

### 11. CONSENSUS PER COIN — chars 16,598-16,715, cost 117

- Source: `strategist.py:2828-2839`. Sub-block inside STRATEGY HINTS; header `\n  CONSENSUS PER COIN:` then top 15 rows of `{sym}: N buy / N sell (total score: N)`.
- Data source: `layer_manager._strategy_consensus_summary` (`strategist.py:2822-2825`) with explicit fallback to `_strategy_consensus`. Written by `StrategyWorker` at `src/workers/strategy_worker.py:878`.
- Trim priority: inherits from the parent STRATEGY HINTS block (IMPORTANT).

### 12. ACCOUNT — chars 16,715-16,769, cost 54

- Source: `strategist.py:2855-2862`. Header + Equity + Available lines.
- Data source: `account_service.get_wallet_balance()` at line 2860.
- Trim priority: ESSENTIAL via `"## ACCOUNT"` marker (`strategist.py:345`).

### 13. Per-trade size limit + Maximum positions — chars 16,769-16,831, cost 62

- Source: `strategist.py:2900-2905`. Two lines (no header): `Per-trade size limit: $N` and `Maximum concurrent positions: N`.
- Data source: `tiered_capital.get_limits(equity, deployed)` at line 2899; `deployed` computed from `position_service.get_positions()` at line 2894-2896.
- Trim priority: ESSENTIAL via `"Per-trade size limit"` marker (`strategist.py:375`).

### 14. (Removed) TODAY'S PERFORMANCE — NOT in dump

The aggressive-framing rewrite removed the daily PnL / trades-today block (`strategist.py:2911-2927`). The marker `## TODAY'S PERFORMANCE` is still listed ESSENTIAL at `strategist.py:389-390`, kept as defense-in-depth; the code path that would emit it is now a comment block.

### 15. (Conditional) Event buffer block — NOT in dump

Source: `strategist.py:2935-2959`. Renders watchdog event text via `event_buffer.get_prompt_text(max_events=20)` (line 2944), then clears the buffer. The dump shows no event-buffer text (no watchdog events queued this cycle); the block is appended only when `events_text` is truthy.

### 16. (Conditional) URGENT queue block — NOT in dump

Source: `strategist.py:2961-2973`. `urgent_queue.drain_concerns()` + `urgent_queue.format_for_prompt(concerns)` (lines 2965-2967). Sets `self._has_urgent_concerns = True` which then concatenates the URGENT addendum to the system prompt (`strategist.py:692-702`) in the SAME cycle. The fresh dump shows no urgent queue payload — `_has_urgent_concerns` is False at line 712 of the log emission.

## Trim / Compression Logic

Caps: `_SECTION_CAP = 80`, `_CHAR_CAP = 14000` at `strategist.py:3017-3018`. The dump is 16,831 chars (above the 14k cap) at ~18-22 sections. Two trim modes exist, switched by `[stage2].enable_priority_trim` (`config.toml:279 = true`):

- Priority mode at `strategist.py:3032-3082`: classify each section via `_infer_section_priority` (`strategist.py:414-436`), then drop OPTIONAL (priority 3) from the tail, then IMPORTANT (priority 2); ESSENTIAL (priority 1) never drops. Marker tables at `strategist.py:343-411`.
- Legacy mode at `strategist.py:3084-3110`: pop-from-end with a 30-section floor.

The dump has no synthetic `"(... N trailing sections trimmed …)"` line (the emitter is at `strategist.py:3068-3072` / `3100-3104`). Reading the priority pass: the OPTIONAL pool is small (SESSION 155, X-RAY 2228, SENTIMENT 38, held-symbols 59) and even dropping all of it leaves the prompt well above 14k because TRADE CANDIDATES alone is 11,755 chars and ESSENTIAL. The trim block likely entered, found no further droppable tail, and exited without appending the sentinel. Result: the 14k cap is advisory and is not enforced when ESSENTIAL content alone overflows it.

Observability: `STRAT_PROMPT_SIZE` (`strategist.py:3013-3016`), `STRAT_PROMPT_BUILD` per-section timings (`strategist.py:2982-2984`), `PROMPT_BUILD_DONE` (`strategist.py:3118-3122`), `PROMPT_COMPRESS` (`strategist.py:3135-3140`), `STRAT_CALL_A_CTX` legacy summary (line 3114). `enable_prompt_compression` (`config.toml:292 = false`) is wired in `_format_packages_for_prompt_full` at lines 2079-2086 and 2132-2134 (precision + separator tightening); currently inert.

## Aggressive-Framing Flag State

`STRAT_AGGRESSIVE_FRAMING` sentinel (`strategist.py:712-718`) emits one log line per CALL_A with six framing-removal switches hardcoded:

- `mode_line=skipped` — the trading-mode header (`MODE: SHADOW/TESTNET/MAINNET`) is removed. Source comment at `strategist.py:2356-2367` documents the removal; replaced by empty `_t_sec = time.time()` reset. There is NO config flag — this is hardcoded.
- `coaching=skipped` — `PerformanceEnforcer.get_coaching_text(...)` injection at the top of the prompt is removed. Source comment at `strategist.py:2257-2273`. Hardcoded. The `enforcer` service is still wired; `get_coaching_text` stays defined at `src/strategies/performance_enforcer.py:669` but the only live caller is the dead `_build_context_prompt` at `strategist.py:852-860`.
- `fund_rules=minimal` — replaced the full `FundLimits.to_prompt_text()` block (header, equity, growth %, tier label, deployed/available, max single trade) with two clean lines (`Per-trade size limit` + `Maximum concurrent positions`). Source at `strategist.py:2869-2905`. Hardcoded.
- `today_perf=skipped` — daily PnL + trades-today removed. Source comment at `strategist.py:2911-2927`. Hardcoded.
- `dir_perf=skipped` — `_build_direction_performance()` call removed. Source comment at `strategist.py:2344-2353`. Hardcoded.
- `regime_instr=minimal` — full prescriptive `_build_regime_instructions(...)` block replaced with the single factual line emitted at `strategist.py:2334-2338`. Source comment at `strategist.py:2316-2331`. Hardcoded.
- `contract=aggressive_exploit` — system prompt is one of the `_AGGRESSIVE` variants. Always reported as `aggressive_exploit`.
- `zero_two_flag=<bool>` — runtime config flag value, captured from `_zero_two` at `strategist.py:678-680`.

None of the six removals is gated by a config flag. To re-enable them, code edits in `_build_trade_prompt` are required.

## Pre-Computed Hooks Already In Place For Enrichment

### `[brain].surface_briefing_fields`

Defined at `src/config/settings.py:492` (default `True`). Set in `config.toml:204` (`surface_briefing_fields = true`). Loaded at `src/config/settings.py:2997` (`bool(data.get("surface_briefing_fields", False))` — note the TOML loader default is False; the dataclass default of True applies only when the field is omitted from the TOML).

Checked in code at:

- `strategist.py:688-691` — when True, `system += BRIEFING_SYSTEM_PROMPT_SUFFIX` (CALL_A system prompt).
- `strategist.py:1561-1563` — controls sort key in `_format_packages_for_prompt` (briefing-mode sorts by interestingness, legacy sorts by opportunity_score).
- `strategist.py:1616-1632` — controls per-coin skip rule (briefing-mode skips only when primary is NO_TRADEABLE_STATE + no position + interestingness below floor; legacy skips when `not qualified` + no position).
- `strategist.py:1640-1665` — controls per-coin header shape (briefing adds interestingness + state label).
- `strategist.py:1704-1705`, `1718-1719`, `2156-2157`, `2200-2201` — controls whether briefing extras (`_format_briefing_extras`) and action hint (`_format_action_hint`) lines are emitted.
- `strategist.py:1845-1847`, `1863-1877`, `1905-1920`, `1927-1953` — equivalent gates in `_format_packages_for_prompt_full`.

This flag is wired, defaulted on, and live. No further work required.

### Per-coin strategy vote summary — `layer_manager.get_strategy_votes`

Defined at `src/core/layer_manager.py:1674`. Cached in `_per_coin_strategy_votes` (declared near line 109-118). Called only from `_format_briefing_extras` at `strategist.py:1739`. The Top BUY / Top SELL lines render at `strategist.py:1766-1779`. Fresh dump confirms it is live: ORCAUSDT shows `Votes: BUY=3.38 vs SELL=0.00 (38 voters)` + `Top BUY: F2_multi_tf_alignment (c0.85,w1.00), B2_supertrend …` at the dump prompt offsets 660-820. Wired, called, populated.

### `_build_direction_performance` — line 3490

Defined at `strategist.py:3490-3577`. Live callers: grep across `src/` shows only one — the dead `_build_context_prompt` at `strategist.py:906-911`. The aggressive-framing comment at `strategist.py:2344-2353` documents the removal: "stays defined (no callers outside this site after the deletion); OBS-4 garbage-collection pass will retire it." Status: dead code in the CALL_A path.

### `get_coaching_text` — `PerformanceEnforcer`

Defined at `src/strategies/performance_enforcer.py:669`. Live callers: `strategist.py:853-856` inside `_build_context_prompt` (the dead path). The aggressive-framing comment at `strategist.py:2257-2273` confirms the live CALL_A path does NOT call it; the method stays defined because the dead path also calls it (preserved per the FIX Rule 5 cited in the comment). Status: dead in the CALL_A path; still wired for the dead `_build_context_prompt`.

### `_build_regime_instructions` — line 3392

Defined at `strategist.py:3392-3488`. Live callers: grep shows only one — `_build_context_prompt` at `strategist.py:897-901` (the dead path). Aggressive-framing comment at `strategist.py:2316-2331` documents removal from the live path. Status: dead in the CALL_A path.

## Data Fetched But Elided

`PerformanceEnforcer` is in `self.services` (boot wiring) but `get_coaching_text` is no longer called from `_build_trade_prompt`. The enforcer continues to drive sizing-multiplier / qualify-survival-trade off-prompt.

`DailyPnLManager` is wired but `_build_trade_prompt` does not read it (the removed TODAY'S PERFORMANCE block at lines 2911-2927 was the only consumer). CALL_B does read it — see report 02.

`trade_coordinator._closed_trades` is the data source for `_build_direction_performance`. `_build_trade_prompt` never invokes it; the method is dead from CALL_A's perspective.

`thesis_manager.get_recent_lessons` is NOT called from current `_build_trade_prompt` (T1-3 / F9 lesson-bridge from `fix/five-critical-fixes-2026-05-11` not merged).

`scanner.get_briefing_summary()` or cycle-level rollup — NOT FOUND. Per-coin briefing fields surface inside TRADE CANDIDATES only.

`structure_cache.get_all()` is consulted twice (lines 2668, 2728) but only for session-context fallback and the skip-tail list. Full per-symbol structural analysis is rendered only for the top-8 setups.

## Verdict — What Works, What's Missing

(a) Sections that work: TRADE CANDIDATES (full Layer 1B/1C evidence), MARKET DATA, SESSION (when structure_cache has session context), X-RAY STRUCTURAL SETUPS, SENTIMENT, MARKET REGIME, STRATEGY HINTS + CONSENSUS PER COIN, ACCOUNT, Per-trade size limit. The global regime line, TRADEABLE COINS, held-symbols line are stable. The briefing suffix is appended to the system prompt.

(b) Sections gated off by the aggressive-framing rewrite (hardcoded, no config knob): trading-mode line, coaching block, TODAY'S PERFORMANCE block, direction-performance block, prescriptive regime-instructions block, full FundLimits to_prompt_text block. All six are commented-out in `_build_trade_prompt` with explanatory headers at lines 2257-2367 and 2911-2927.

(c) Dead code with no callers in the live CALL_A path: `_build_regime_instructions` (line 3392), `_build_direction_performance` (line 3490), the inline coaching/today-performance/trading-mode emissions. Each is still callable from the dead `_build_context_prompt` (`strategist.py:846-1524`), so removal must coordinate with retiring that method.

(d) Data fetched but not used: `data_lake.write_market_snapshot` (line 2650) is intentional persistence. The MARKET DATA loop fetches `ticker`, `ta`, `volatility_profiler`, and per-coin regime for every symbol but skips emission for "neutral" coins (`strategist.py:2557-2602`); a summary line `({skipped_count} neutral coins omitted for brevity)` acknowledges them. Per-symbol TA/regime/vol fields for skipped coins are computed and discarded.

(e) Missing relative to `fix/five-critical-fixes-2026-05-11`: the T1-3 / F9 aggregated-stats block (commit `5e26007`) and the per-trade lesson bridge with `min_age_seconds=300` + `exclude_symbols=open_set` guards on `thesis_manager.get_recent_lessons` are not present on this branch. The format helper `format_aggregated_stats_for_prompt` (introduced by commit `e318e51` in `src/core/thesis_manager.py`) is NOT FOUND in the current `src/core/thesis_manager.py`. The "## RECENT PERFORMANCE (last N closes — directional pattern only)" footer in `phase0_fresh_dump/CALL_B_fresh.json` is therefore not emitted by the current code path — see report 02 for the CALL_B side.
