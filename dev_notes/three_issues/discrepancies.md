# Audit/Memory vs Working Tree — Running Discrepancy Log

Updated as each issue's Phase 1 completes. Operator has directed: surface every discrepancy; we will fix together after approval.

## From Phase 0 baseline + Issue A Phase 1

| # | Topic | Audit/memory said | Working tree shows | Material? |
|---|---|---|---|---|
| 1 | APEX model name | `deepseek/deepseek-chat-v3-0324` | `deepseek/deepseek-v3.2` (`src/config/settings.py:1782`) | Cosmetic for now (verify in Issue B Phase 1) |
| 2 | `_build_trade_prompt` line number | 3073 | 2235 (line 3073 is the trim-emit site) | Cosmetic |
| 3 | Profit Sniper structure | "M1 through M9 phases" | regime-aware composite-score thresholds with 4 actions (HOLD / TIGHTEN / PARTIAL / FULL); `mode4_p9` is a phase tag in tick logs, not a close event | Material — reframes Issue C investigation |
| 4 | Issue C closure breakdown | "32 mode4_p9 + 17 SNIPER_STALL_ESCAPE + 4 M4_ACT_CLOSE = 53/64 sniper-driven (83%)" | 4 M4_ACT_CLOSE total in window; sniper full-close share ≈ 19%; the 32 occurrences of `mode4_p9` are phase-tag emits in non-close lines | Material — invalidates 83% premise; underlying concern (4/4 sniper closes were losing trades killed via mature-stall valve) is still real |
| 5 | Issue B affected coins | EGLDUSDT, ORCAUSDT, LDOUSDT (×2) | EGLDUSDT, ORCAUSDT, LDOUSDT, ONDOUSDT (each ×1) | Cosmetic |
| 6 | Issue A URGENT-drop count | "at least 2 events" | 14 events in 3h | Material — much worse than reported |
| 7 | Issue A `dropped_important > 0` | not flagged | 17 / 21 priority-mode events | Material — IMPORTANT category cascade is firing routinely |
| 8 | Issue C SNIPER_GRACE_BLOCKED | 154 | 241 | Cosmetic |
| 9 | Marker `OVERRIDE — URGENT WATCHDOG ALERTS` | implied to protect URGENT block in CALL_A | Dead marker — only matches text appended to the **system prompt** at `strategist.py:694`; the live CALL_A urgent block uses `## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED` from `urgent_queue.py:128`, which the marker does NOT substring-match | Material — root cause of URGENT drops |
| 10 | `Equity:` / `Available:` claimed essential by Explore agent during plan-phase | Explore-agent claim | Neither is in the marker tuple at `strategist.py:343–391`; both classified OPTIONAL by `_infer_section_priority` | Material — confirms agent assertions still need verification |

## From Issue B Phase 1

| # | Topic | Audit/memory said | Working tree shows | Material? |
|---|---|---|---|---|
| B-1 | APEX model name | `deepseek/deepseek-chat-v3-0324` | `deepseek/deepseek-v3.2` (`settings.py:1782`) — verified against config | Cosmetic (re-confirms Phase 0 finding #1) |
| B-2 | Affected coins on 2026-05-08 | EGLDUSDT, ORCAUSDT, LDOUSDT (×2 = 4 events) | EGLDUSDT, ORCAUSDT, LDOUSDT, ONDOUSDT (each ×1 in window) — same total count, different distribution | Cosmetic |
| B-3 | "Using defaults" semantics | "trades placed without proper APEX optimization use whatever default direction and sizing the system has, not what XRAY's analysis would have produced" | `is_fallback=True` returns Claude's **exact** pre-APEX directive in `layer_manager._apply_apex_optimization:1457`. The placeholder `sl_pct=2.0`/`tp_pct=1.5` in `_fallback` are dead code. Trade is placed with XRAY-grounded direction + Claude's chosen SL/TP/size/leverage. | Material reframe — degradation is real (loss of APEX flip-validation, TP-cap, leverage clamp, size adjustment) but smaller than implied |
| B-4 | TIAS-vs-APEX reliability gap | Not flagged | TIAS uses `response_format: {"type": "json_object"}` (`deepseek_client.py:129`); APEX does not. TIAS sees 0 "no choices" failures over same 4-day period; APEX sees 8 unique. Single material payload difference. | Material — points directly at the strongest fix candidate |
| B-5 | "Is the project's retry logic insufficient?" | Audit asks the question | NO retry at all by architectural choice (`qwen_client.py:7–9` docstring: "NO RETRY"). Compare to TIAS's `retryable=True` flag for HTTP 429/503 (`deepseek_client.py:148/153`). | Material |
| B-6 | Failure modes | Audit cites only "no choices" | Two distinct empty-content modes: 4× "no choices" + 4× empty/malformed `content` (markdown-fence-only or single-brace responses). Likely same upstream root cause at different OpenRouter pipeline stages. | Material — broadens the scope of the fix |
| B-7 | Total APEX failures over time | Implied as 4 in 3-hour audit window | 8 unique events across 4 days (`grep` of all log files). Sub-1% baseline rate, with bursty clustering (4 events in 16 minutes on 2026-05-08). | Material — quantifies the rate |

## From Issue C Phase 1

| # | Topic | Audit/memory said | Working tree shows | Material? |
|---|---|---|---|---|
| C-1 | Sniper architecture | "M1 through M9 phases" | Regime-aware score thresholds (one tighten/partial/full triplet per regime, `THRESHOLD_SETS` at `profit_sniper.py:57`); 4 actions {hold/tighten/partial/full}; `mode4_p9` is a hardcoded label string at `_execute_action:1931`, NOT a phase number. | Material — fundamentally reframes investigation |
| C-2 | "32 mode4_p9 (full close)" | Audit | 32 substring occurrences across tick-evaluation logs (M4_DECISION etc.); only **4** actual full-close events fired in the window (4 `COORD_CLOSE_END | by=mode4_p9`, 4 `M4_ACT_CLOSE`). | Material — invalidates 83% premise |
| C-3 | "53/64 sniper-driven (83%) closures" | Audit | 4 sniper full closures / ~21 total full closures = **19%**. CALL_B / watchdog strategic-review closures (9) outnumber sniper 2.25×. | Material — significantly narrows the fix scope |
| C-4 | "Mode4_p9 events kill trades" via score path | Audit | All 4 closures came via the **mature-stall valve** at `_stall_escape_action:2481–2494`, not the score path. Score path is gated by `current_pnl > 0` at `_determine_action:1590`; cannot fire on losing positions. | Material — identifies the actual code site to change |
| C-5 | Bybit demo affects sniper math | Audit speculates fill-latency / spread effects | Sniper consumes Shadow's authoritative `pos.net_pnl_usd` (line 2651, post-2026-04-26 fix). Price source is `MarketService.get_ticker()` 5 s cache backed by Bybit WebSocket — same source `_execute_claude_trade` uses. No code-path divergence found. | Material — narrows fix scope (rules out Bybit-specific recalibration) |
| C-6 | "17 SNIPER_STALL_ESCAPE" implies all are closures | Audit | 13 are `escalated_to=partial_close` (50% partials), 4 are `escalated_to=full_close` (the 4 mature-stall valve fires that became M4_ACT_CLOSE). | Material |
| C-7 | Phase 1 grace fix (commit `00f8eb1`) | Memory: shipped + verified | Confirmed shipped (241 SNIPER_GRACE_BLOCKED in window). Does NOT cover the mature-stall valve by design (`profit_sniper.py:2510` comment: "the forced-full path is the mature-stall valve and is unaffected"). | Cosmetic |
| C-8 | "Trades aren't surviving long enough for strategy edge to manifest" | Audit/operator | The 4 victims survived 7–15 minutes each. The legitimate concern is narrower: trades that briefly touched profit (peak ≤ +0.30 %) are killed at the −0.3 % development floor before recovery time. | Material reframe — fix is about "marginal-loss after brief profit" not "all trades being killed too fast" |
