# CALL_B Current State — Complete Investigation Report

Read-only data capture per CAPTURE_CALLB_CURRENT_STATE.md. No source, configuration, prompts, or DB state were modified.

- Date of capture: 2026-05-17
- Project root: `/home/inshadaliqbal786/trading-intelligence-mcp`
- Most recent monitoring log: `/home/inshadaliqbal786/SYSTEM_LOGS_2026-05-17_05-00_to_11-00.log` (122,635 lines)
- Stage 2 dumps directory: `/home/inshadaliqbal786/trading-intelligence-mcp/data/stage2_dumps/` (1,082 files; latest cluster timestamped 20260517T*)
- Output: this single file at `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/callb_capture/CALLB_CURRENT_STATE_COMPLETE.md`
- Investigation method: end-to-end reading of strategist.py (4,610 lines), position_watchdog.py (3,754 lines), trade_coordinator.py (1,915 lines), decision_parser.py (211 lines), claude_code_client.py (2,167 lines), plus stage2 dumps and the SYSTEM_LOGS log.

---

## 00 — Executive Summary

### What CALL_B is

CALL_B is the position-management Claude call, invoked every 180 s by the brain strategist at `src/brain/strategist.py:953` (`create_position_plan`). It takes the static `POSITION_SYSTEM_PROMPT` (strategist.py:163–179) plus a dynamic user prompt built by `_build_position_prompt` (strategist.py:3813–4151), sends them to Claude, and parses the response into `PositionAction` objects (strategist.py:4526–4610).

### What goes into the prompt

In order: market regime + Fear & Greed, today's PnL, today's direction performance (only if `brain.emit_direction_perf_in_callb` is True), a static CONTRACT section restating close criteria, one block per open position (entry/now/PnL/SL/TP/lev/age/remaining/regime/SL-consumed plus optional FLIPPED notice with RR evidence), aggregated last-50-closes stats, cooldowns, and an optional URGENT WATCHDOG ALERTS section injected from `UrgentQueue`. Per-trade lessons (TIAS) are intentionally not in CALL_B (sentinel `tias_coaching_removed=True` in `STRAT_CALL_B_CTX`). The most recent observed prompt was 2,843 chars with system prompt 1,783 chars (dump `20260517T094438_call0060_d-1779010995797.json`).

### Response schema brain produces

```
{"position_actions": {"SYMBOL": {"action": "hold|close|tighten_stop|set_exit|take_profit",
                                 "new_sl": price_or_null,
                                 "exit_price": price_or_null,
                                 "reasoning": "..."}}}
```

The action string is lowercase; unknown action downgrades to `hold` with `STRAT_CALL_B_BAD_ACTION_TYPE`. `tighten_stop` without a valid `new_sl > 0` and `set_exit` without `exit_price > 0` are downgraded to `hold` with `STRAT_CALL_B_DOWNGRADE`. No `confidence` or `priority` field exists for position actions. Invalid JSON raises `CLAUDE_PARSE_FAIL` and the cycle returns `None` (`STRAT_CALL_B_FAIL`).

### How a close decision flows

Brain `close`/`take_profit` action → `_parse_position_plan` → `_execute_position_actions` in `src/core/layer_manager.py` (~lines 1156–1250) → `coordinator.set_close_reason(symbol, "strategic_review")` → `coordinator.queue_strategic_action(...)` (`src/core/trade_coordinator.py:319–345`) → watchdog drains via `drain_strategic_actions()` in `position_watchdog.py:2935–3065` → minimum-hold guardrail (`strategic_action_min_hold_seconds`, default 300 s, with whitelisted reasons) → `position_service.close_position(symbol, close_trigger="wd_claude_action")` at `position_watchdog.py:3025`. Brain does NOT bypass the watchdog; the watchdog is the only executor of strategic close actions, and the string `"wd_claude_action"` is set verbatim at that single line.

### Watchdog close paths (independent of brain)

Nine in total: `wd_dl_action` (SENTINEL deadline), `wd_plan_timer` (legacy fallback), `wd_trail` (trail SL hit), `time_decay_force_close` (TimeDecaySLCalculator force-close), `wd_hard_stop` (-3 % loss), `wd_timeout` (>95 % of allotted time + losing), `wd_profit_take` (>+1.5 % + past 50 % of hold), `wd_early_exit` (gated, disabled by default), and `wd_claude_action` (brain-driven). An emergency-mode close-all path also exists when `_session_pnl_pct` < threshold. Brain concerns in PASSIVE mode are queued via `urgent_queue`, NOT executed by the watchdog directly.

### Factors available to the watchdog for scoring

PnL %, position age, remaining time, SL distance/consumption %, TP distance, current vs entry, velocity/acceleration of PnL, choppiness index, XRAY structural verdict, per-coin regime + confidence, trail-SL state, TimeDecayState (`p_win`, `mae_pct`, velocity, acceleration), Sentinel deadline/advisor outputs, conviction/consensus (context only, not used in auto gates), peak PnL, session PnL. Profit-sniper hints are NOT pushed to the watchdog — the two services operate independently and only share `layer4_protection.get_struct_guard_verdict`.

### What brain typically said when it closed (2026-05-17 monitoring window)

Of 13 `wd_claude_action` closes in the window, 6 were matched to stage2 dumps; the matched reasoning was specific in all 6 (SL %, time remaining, directional WR, regime, peak-to-current drawdown, per-tick price levels). None used vague loss-avoidance language. The dominant cited factors were SL consumption (38–68 %), time remaining (3–16 min), directional WR (longs at 17–20 % WR that day), regime mismatch (longs in ranging regime where shorts dominated), and price acceleration toward SL. Recent aggregated stats shown to brain reported `wd_claude_action 10 (W 0%)` in the last 50 closes, so brain saw its own close history's poor record while still issuing closes.

### Key open data points / Notes for operator

- 7 of 13 `wd_claude_action` events in the audit window did not match a stage2 dump by `decision_id`; some may have closed via the legacy `_ask_brain` direct path or had dumps outside the captured cluster. See Section 07 for the full table.
- Post-close price-trajectory verification ("did it recover?") is not retrievable from the current log window; the system writes close events but not the next 5–15 min of ticker data into the same SYSTEM_LOGS file. Retrospective verdicts in Section 07 are inferred from SL %, time remaining, and regime/WR context at close, not from forward price.
- The aggressive-framing sentinel `STRAT_AGGRESSIVE_FRAMING | mode_line=skipped coaching=skipped fund_rules=minimal today_perf=skipped dir_perf=skipped regime_instr=minimal` (strategist.py:870–876) refers to CALL_A framing reductions — CALL_B has its own prompt and does not consume those flags. The CALL_B-specific knob is `brain.emit_direction_perf_in_callb` (settings.py:637, default True).

### File map for the rest of this document

| Section | Target |
|---|---|
| 01 | CALL_B prompt construction code |
| 02 | Verbatim system prompt for CALL_B |
| 03 | Verbatim user prompt structure for CALL_B |
| 04 | Response schema and parsing |
| 05 | Close-action execution flow |
| 06 | Position watchdog close paths |
| 07 | wd_claude_action close events from recent logs |
| 08 | Brain reasoning analysis on close decisions |
| 09 | Available factors for watchdog scoring |
| 10 | Appendix — off-topic items noted but not investigated |

---

## 01 — CALL_B Prompt Construction Code

### 01.1 Entry point

`src/brain/strategist.py:953` — `create_position_plan(positions, regime, fg_value)`.

Key logged checkpoints: `STRAT_CALL_B_START` (line 962), `STRAT_CALL_B` (line 994, with `chars=`), `STRAT_CALL_B_PLAN` (line 1007, `acts=`), `STRAT_CALL_B_END` (line 1028 with `status=success|failed|cancelled|deferred`). Price-divergence deferral (default 1 %) is at lines 970–989 and yields `status=deferred`.

### 01.2 Section-by-section assembly (in emit order)

Built by `_build_position_prompt` (strategist.py:3813–4151). Each section below is identified by file:line of the builder code.

#### Section A — Market regime + sentiment header

- `strategist.py:3822–3826`
- Data sources: `RegimeWorker.get_last_regime()` → `regime.value` + `confidence`; `FearGreedService.get_latest()` → integer 0–100.
- Always emitted; falls back to defaults on fetch failure.
- Output:
  ```text
  ## MARKET REGIME: {regime_str} ({confidence:.0%})
  ## SENTIMENT: Fear & Greed = {value}
  ```

#### Section B — Today PnL

- `strategist.py:3828–3834`
- Data: `PnLManager.current_pnl_pct`.
- Skipped if the `pnl_manager` service is not registered.
- Output: `## TODAY: PnL={pnl_pct:+.2f}%`.

#### Section C — Today direction performance (CALL_B only)

- `strategist.py:3836–3883` (build), with a gate at line 3850: `_emit_dp = bool(getattr(_brain_cfg, "emit_direction_perf_in_callb", True))`.
- Data: `PerformanceEnforcer._per_direction` dict.
- Skipped if total day-closes are zero or flag is False.
- Output: `## TODAY DIRECTION PERF: Longs {bw}W/{bl}L ({buy_wr:.0f}% WR) | Shorts {sw}W/{sl_}L ({sell_wr:.0f}% WR)`.
- Log event on emit: `DIR_PERF_COMPUTED` (line 3878).

#### Section D — Open-positions header

- `strategist.py:3886–3894`
- Output: `## YOUR OPEN POSITIONS — Review each and decide: hold, close, tighten_stop, set_exit`.

#### Section E — Static CONTRACT block

- `strategist.py:3896–3917`
- Hardcoded per-cycle restatement of close criteria — see Section 03.5 below for verbatim text.

#### Section F — One block per open position

- `strategist.py:3942–4043`
- Per-position formatting at lines 3996–4002:
  ```python
  lines.append(f"### {symbol} [{side_str}]")
  lines.append(f"  Entry: ${entry_price:.2f} | Now: ${mark_price:.2f} | PnL: {pnl_pct:+.2f}%")
  lines.append(f"  SL: ${sl_price:.2f} | TP: ${take_profit_price:.2f} | Lev: {leverage}x")
  lines.append(f"  Age: {age:.0f}min | Remaining: {remaining:.0f}min | Regime: {rgm_str}")
  lines.append(f"  SL consumed: {sl_consumed:.0f}%")
  ```
- SL-consumed calculation (3986–3994):
  - Buy: `moved = max(0, entry - mark)`
  - Sell: `moved = max(0, mark - entry)`
  - `pct = (moved / total_risk) * 100`, clamped to `[0, 100]`.
- PnL (3946–3952):
  ```python
  pnl_pct = ((mark - entry) / entry * 100) if entry > 0 else 0
  if side in ("Sell", "Short"): pnl_pct = -pnl_pct
  ```
- FLIPPED block (4004–4043) — two variants depending on `xray_flip_source` (XRAY-driven, 4016–4027) or legacy `apex_flipped` (4034–4038). Emits log `STRAT_CALL_B_FLIP_NOTICE` (line 4029 / 4041).
- Original thesis text is deliberately NOT emitted (decision noted at strategist.py:3965–3975).

#### Section G — Aggregated last-50 closes

- `strategist.py:3067–4082`
- Data: `ThesisManager.get_aggregated_stats(limit_closes=50)` → `format_aggregated_stats_for_prompt()`.
- Failure log: `STRAT_CALL_B_STATS_FAIL` (line 4081).

#### Section H — Recently closed cooldowns

- `strategist.py:4084–4094`
- Data: `TradeCoordinator._symbol_cooldowns` dict.
- Output:
  ```text
  RECENTLY CLOSED (wait for cooldown before re-entering):
    {SYMBOL}: cooldown ({remaining_seconds}s remaining)
  ```

#### Section I — Urgent watchdog alerts (optional, dynamic)

- `strategist.py:4096–4105`
- Source: `UrgentQueue.has_concerns` → `drain_concerns()` → `UrgentQueue.format_for_prompt(concerns)` (external in `src/core/urgent_queue.py`).
- Emit log: `STRAT_CALL_B_URGENT | injected={count}` (line 4104).
- Brain is told it MUST include `position_actions` for each alerted symbol (literal in the formatted alert block — see 03.7).

### 01.3 STRAT_AGGRESSIVE_FRAMING flags

Hardcoded log emitted at strategist.py:870–876:

```text
STRAT_AGGRESSIVE_FRAMING | mode_line=skipped coaching=skipped fund_rules=minimal today_perf=skipped dir_perf=skipped regime_instr=minimal
```

These six labels describe what was removed from CALL_A's reframing rewrite. They do not flow into CALL_B prompt content. CALL_B has independent build code; the only CALL_B-specific knob from `BrainSettings` is `emit_direction_perf_in_callb` (settings.py:637, default True). Other settings.py CALL_B-relevant entries:

| Setting | Default | Effect on CALL_B |
|---|---|---|
| `brain.emit_direction_perf_in_callb` | True | Toggles Section C |
| `brain.strategic_interval` | 180 s | Cadence of CALL_B |
| `brain.model` | `"claude-sonnet-4-20250514"` | Model used |
| `brain.max_tokens` | 4096 | Output cap |
| `brain.temperature` | 0.3 | Sampling temp |
| `brain.emit_vote_opposition` | True | CALL_A only |
| `brain.emit_category_split` | True | CALL_A only |
| `brain.surface_briefing_fields` | True | CALL_A only |

### 01.4 Final assembly log

`STRAT_CALL_B_CTX` (strategist.py:4138) emits at the end of the build:

```text
STRAT_CALL_B_CTX | positions={n} chars={sum} el={ms} tias_coaching_removed=True lessons_in_db={n}
```

`tias_coaching_removed=True` is hardcoded — TIAS per-trade lessons are intentionally not in CALL_B (closed-loop immunity, decision dated 2026-05-05 per the post-execution-closure fix).

---

## 02 — Verbatim System Prompt for CALL_B

### 02.1 Source

- File: `src/brain/strategist.py`
- Lines: 163–179
- Constant: `POSITION_SYSTEM_PROMPT`
- Version constant: `POSITION_SYSTEM_PROMPT_VERSION = 2` (strategist.py:185)
- Boot sentinel: `STRAT_CALL_B_REFRAMED` (logged at strategist.py:596 when the module loads)

### 02.2 Full verbatim text

```text
You are managing open crypto futures positions. Your aim is to maximize the development of each position. Aggressive opportunity exploitation, not capital preservation.

RULES:
1. Output ONLY valid JSON: {"position_actions": {"SYMBOL": {"action": "hold|close|tighten_stop|set_exit", "new_sl": price_or_null, "exit_price": price_or_null, "reasoning": "..."}}}
2. Review EVERY open position — do not skip any.
3. Actions:
   - hold: Position is developing within normal parameters — let it run.
   - tighten_stop: Lock partial profit when significantly profitable. Provide new_sl price.
   - set_exit: Set a specific exit price target at a structural level. Provide exit_price.
   - close: Genuine invalidation only — see the CONTRACT section in the per-cycle prompt for the precise close criteria.
4. Decision framework (the per-cycle prompt restates the contract right next to the position data — read it):
   - If profitable (PnL > +1.5%) and structure suggests give-back risk: TIGHTEN_STOP to lock gains.
   - If PnL > +3% and position aging: TIGHTEN_STOP aggressively or SET_EXIT at the next strong level.
   - Otherwise: HOLD by default. Close only on genuine structural invalidation, SL approach with no recovery, or TP approach.
5. Do NOT close based on regime alignment alone, on the original thesis text, or on small-sample recency bias. Some positions are intentionally counter-regime when RR justifies — the system flips direction when the flipped RR is materially better than the original, and the prompt marks those positions as FLIPPED with the concrete RR comparison so you can verify the choice.
6. Do NOT suggest new trades — only manage existing positions.
7. When tightening stops, set new_sl at a logical level (e.g., breakeven, recent swing, or halfway to entry).
```

### 02.3 Notes on the system prompt

- The literal allowed-action enumeration in Rule 1 is `hold|close|tighten_stop|set_exit`. The parser at strategist.py:4550–4552 also accepts `take_profit` even though the system prompt does not advertise it. Brain will occasionally emit `take_profit` and it is handled identically to `close` downstream.
- Rule 5 reinforces the "do not close based on original thesis" position — paired with the deliberate omission of original thesis text from the per-cycle prompt (strategist.py:3965–3975).
- The TIGHTEN_STOP "PnL > +1.5%" / SET_EXIT "PnL > +3%" thresholds are advisory in the prompt only; there is no code-side validation gating them.

---

## 03 — Verbatim User Prompt Structure for CALL_B

### 03.1 Full real example (verbatim from a dump)

Source: `data/stage2_dumps/20260517T094438_call0060_d-1779010995797.json` (CALL_B, 2026-05-17 09:44:38 UTC, prompt 2,843 chars, system 1,783 chars, elapsed 82,248 ms).

```text
## MARKET REGIME: ranging (40%)
## SENTIMENT: Fear & Greed = 27
## TODAY: PnL=+0.00%
## TODAY DIRECTION PERF: Longs 1W/5L (17% WR) | Shorts 19W/14L (58% WR)

## YOUR OPEN POSITIONS — Review each and decide: hold, close, tighten_stop, set_exit

## CONTRACT — POSITION MANAGEMENT

Manage these open positions to maximize their development.

For each position:
- HOLD if the position is developing within normal parameters.
- TIGHTEN_STOP to lock partial profit when significantly profitable (PnL > +1.5%).
- SET_EXIT or take_profit at strong structural levels.

CLOSE only when:
- The setup that triggered entry is genuinely invalidated by structural change (XRAY confidence drop, setup-type drift, regime inversion at >=60% confidence).
- SL is approaching and recovery looks unlikely.
- TP is approaching and you want to lock the win.

Do NOT close based on:
- Regime alignment alone — some positions are intentionally counter-regime when RR justifies.
- The original thesis text — the system may have flipped direction; trust the current state shown above.
- Recency-bias from past similar trades — small samples don't define what works.

For positions marked FLIPPED below: the flip was made because the flipped direction had materially better RR. Manage based on the CURRENT direction, not the original.

### LDOUSDT [Sell]
  Entry: $0.36 | Now: $0.36 | PnL: -0.08%
  SL: $0.36 | TP: $0.35 | Lev: 3x
  Age: 11min | Remaining: 29min | Regime: RANGING 40%
  SL consumed: 12%
  FLIPPED via XRAY from Buy to Sell: RR_chosen=1.93 vs RR_rejected=0.15 (12.9x better)

### ORCAUSDT [Sell]
  Entry: $1.49 | Now: $1.49 | PnL: -0.00%
  SL: $1.53 | TP: $1.46 | Lev: 5x
  Age: 11min | Remaining: 34min | Regime: VOLATILE 78%
  SL consumed: 0%
  FLIPPED via XRAY from Buy to Sell: RR_chosen=7.17 vs RR_rejected=0.04 (179.2x better)

### HYPEUSDT [Buy]
  Entry: $42.92 | Now: $42.87 | PnL: -0.12%
  SL: $42.53 | TP: $43.99 | Lev: 5x
  Age: 30min | Remaining: 10min | Regime: TRENDING_UP 42%
  SL consumed: 13%

### LINKUSDT [Sell]
  Entry: $9.76 | Now: $9.77 | PnL: -0.05%
  SL: $9.79 | TP: $9.50 | Lev: 5x
  Age: 30min | Remaining: 15min | Regime: TRENDING_DOWN 52%
  SL consumed: 17%

## RECENT PERFORMANCE (last 50 closes — directional pattern only)
WR: 56% (28W / 22L)  |  Net PnL: $+29.80
By close reason: wd_dl_action 16 (W 100%) | system_close 11 (W 73%) | wd_claude_action 10 (W 0%) | bybit_sl_hit 6 (W 33%) | wd_timeout 5 (W 0%)

## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED
These positions need your attention. For each, decide: hold, close, tighten_stop, or set_exit.
You MUST include position_actions for each alerted symbol in your response.

[URGENT] HYPEUSDT [Buy] — PnL: -0.34%
  Entry: $42.92 | Now: $42.77 | SL: $42.53
  SL consumed: 38% | Age: 25min
  Warnings: Dropped 0.34% from peak profit, Price 38% of the way to stop-loss
```

### 03.2 All fields per position

| Field | Source | Format | Example |
|---|---|---|---|
| Symbol | `pos.symbol` | uppercase | `LDOUSDT` |
| Side | `pos.side.value` | `Buy` / `Sell` | `[Sell]` |
| Entry | `pos.entry_price` | `${price:.2f}` | `$0.36` |
| Now (mark) | `pos.mark_price` | `${price:.2f}` | `$0.36` |
| PnL % | derived, sign-flipped for shorts | `{:+.2f}%` | `-0.08%` |
| SL | `thesis_data['stop_loss_price']` | `${price:.2f}` | `$0.36` |
| TP | `thesis_data['take_profit_price']` | `${price:.2f}` | `$0.35` |
| Leverage | `thesis_data.get('leverage', '?')` | `{lev}x` | `3x` |
| Age | `plan.age_minutes` | `{m:.0f}min` | `11min` |
| Remaining | `plan.remaining_minutes` | `{m:.0f}min` | `29min` |
| Coin regime | `RegimeDetector.get_coin_regime()` | `{REGIME} {conf:.0%}` | `RANGING 40%` |
| SL consumed % | derived | `{p:.0f}%` | `12%` |
| FLIPPED (optional) | `xray_flip_*` / `apex_flipped` | sentence | see XRAY example above |

### 03.3 Market header section

```text
## MARKET REGIME: ranging (40%)
## SENTIMENT: Fear & Greed = 27
```

Regime tags: `trending_up`, `trending_down`, `ranging`, `volatile`, `dead`, `unknown`. Confidence is shown as integer percent. F&G is 0–100.

### 03.4 Today and direction-perf section (optional)

```text
## TODAY: PnL=+0.00%
## TODAY DIRECTION PERF: Longs 1W/5L (17% WR) | Shorts 19W/14L (58% WR)
```

Direction-perf is gated by `emit_direction_perf_in_callb`. Day window resets at midnight UTC per `PerformanceEnforcer` semantics.

### 03.5 Static CONTRACT block (verbatim, strategist.py:3896–3917)

```text
## CONTRACT — POSITION MANAGEMENT

Manage these open positions to maximize their development.

For each position:
- HOLD if the position is developing within normal parameters.
- TIGHTEN_STOP to lock partial profit when significantly profitable (PnL > +1.5%).
- SET_EXIT or take_profit at strong structural levels.

CLOSE only when:
- The setup that triggered entry is genuinely invalidated by structural change (XRAY confidence drop, setup-type drift, regime inversion at >=60% confidence).
- SL is approaching and recovery looks unlikely.
- TP is approaching and you want to lock the win.

Do NOT close based on:
- Regime alignment alone — some positions are intentionally counter-regime when RR justifies.
- The original thesis text — the system may have flipped direction; trust the current state shown above.
- Recency-bias from past similar trades — small samples don't define what works.

For positions marked FLIPPED below: the flip was made because the flipped direction had materially better RR. Manage based on the CURRENT direction, not the original.
```

### 03.6 Aggregated last-50 closes block

Example verbatim (from dump 20260517T094438):

```text
## RECENT PERFORMANCE (last 50 closes — directional pattern only)
WR: 56% (28W / 22L)  |  Net PnL: $+29.80
By close reason: wd_dl_action 16 (W 100%) | system_close 11 (W 73%) | wd_claude_action 10 (W 0%) | bybit_sl_hit 6 (W 33%) | wd_timeout 5 (W 0%)
```

Source: `src/core/thesis_manager.py` → `format_aggregated_stats_for_prompt()`. Aggregate-only (no per-symbol narratives) by design to prevent closed-loop feedback.

### 03.7 Urgent watchdog alert block (optional)

Example verbatim (from dump 20260517T094438):

```text
## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED
These positions need your attention. For each, decide: hold, close, tighten_stop, or set_exit.
You MUST include position_actions for each alerted symbol in your response.

[URGENT] HYPEUSDT [Buy] — PnL: -0.34%
  Entry: $42.92 | Now: $42.77 | SL: $42.53
  SL consumed: 38% | Age: 25min
  Warnings: Dropped 0.34% from peak profit, Price 38% of the way to stop-loss
```

Formatter: `UrgentQueue.format_for_prompt(concerns)` in `src/core/urgent_queue.py`. The "You MUST include position_actions" line is part of that formatter and is the strongest close-pressure language in the entire user prompt.

---

## 04 — Response Schema and Parsing

### 04.1 Top-level structure

The brain returns a single JSON object whose only required top-level key is `position_actions` (a dict keyed by symbol). The expected schema, declared in Rule 1 of `POSITION_SYSTEM_PROMPT` (strategist.py:166):

```json
{
  "position_actions": {
    "SYMBOL": {
      "action": "hold|close|tighten_stop|set_exit|take_profit",
      "new_sl": price_or_null,
      "exit_price": price_or_null,
      "reasoning": "string"
    }
  }
}
```

### 04.2 Parsing entry point

`src/brain/strategist.py:996–1003`:

```python
raw_response = await self.claude.send_message(prompt, POSITION_SYSTEM_PROMPT)
if hasattr(self.claude, "extract_json"):
    plan_data = self.claude.extract_json(raw_response)
else:
    plan_data = json.loads(raw_response)
plan = self._parse_position_plan(plan_data)
```

`extract_json` strategies live in `src/brain/claude_code_client.py:1012–1063`:

1. Markdown code fences ` ```json ... ``` ` (lines 1019–1025).
2. First `{` to last `}` (lines 1028–1034).
3. First `[` to last `]`, converted to `{"decisions": result}` (lines 1036–1046).
4. Raw `json.loads` (lines 1048–1063). On final failure: `CLAUDE_PARSE_FAIL | reason=json_decode err='...' raw_response='...'` then `raise ValueError(...)`.

### 04.3 Valid action types

`src/brain/strategist.py:4550–4552`:

```python
valid_actions = {"hold", "close", "tighten_stop", "set_exit", "take_profit"}
```

Note that `take_profit` is accepted by the parser even though `POSITION_SYSTEM_PROMPT` Rule 1 does not list it.

### 04.4 Per-action fields

| Field | Required? | Type | Notes |
|---|---|---|---|
| `action` | Yes | string | Lowercased on parse; unknown → `hold` with `STRAT_CALL_B_BAD_ACTION_TYPE` (strategist.py:4566–4572). |
| `new_sl` | Required only for `tighten_stop` | float or null | If ≤ 0, action is downgraded to `hold` with `STRAT_CALL_B_DOWNGRADE` (strategist.py:4577–4582). |
| `exit_price` | Required only for `set_exit` | float or null | If ≤ 0, action is downgraded to `hold` with `STRAT_CALL_B_DOWNGRADE` (strategist.py:4583–4588). |
| `reason` / `reasoning` | No | string ≤500 chars | Parser reads `reason` first, falls back to `reasoning` (strategist.py:4590–4592). |

No `confidence`, `priority`, or `urgency` field exists in the CALL_B schema. (Confidence exists in CALL_A only.)

### 04.5 Action construction

`src/brain/strategist.py:4594–4600`:

```python
plan.position_actions[symbol] = PositionAction(
    symbol=symbol,
    action=action,
    reason=reason,
    exit_price=exit_price,
    new_sl=new_sl,
)
```

Counted log: `STRAT_CALL_B_PARSED | total=N hold=N close=N tighten=N set_exit=N take_profit=N` (strategist.py:4603–4608).

### 04.6 Failure modes

| Failure | Where | Result |
|---|---|---|
| Invalid JSON | `claude_code_client.py:1059–1063` | `CLAUDE_PARSE_FAIL`, ValueError, caught at `strategist.py:1018–1021`, emits `STRAT_CALL_B_FAIL`, returns `None`. No actions queued. |
| CLI/process timeout | `claude_code_client.py:958–997` | Retried up to `max_retries` (default 3). On final failure, raises `TimeoutError`; same `STRAT_CALL_B_FAIL` path. |
| Truncation | Same path as invalid JSON (incomplete object). |
| Unknown action string | `strategist.py:4566–4572` | Downgrade to `hold`, log `STRAT_CALL_B_BAD_ACTION_TYPE`. |
| Missing `new_sl` for tighten | `strategist.py:4577–4582` | Downgrade to `hold`, log `STRAT_CALL_B_DOWNGRADE`. |
| Missing `exit_price` for set_exit | `strategist.py:4583–4588` | Downgrade to `hold`, log `STRAT_CALL_B_DOWNGRADE`. |

### 04.7 Close-action specifics

- Action value for close is exactly the lowercase string `"close"`. There is no `closeRequest`/boolean variant.
- Reason field: aliased; brain may write `reason` or `reasoning`; both accepted. Free-form text, no constraint dictionary, truncated to 500 chars.
- No confidence/priority surfaces in the action object — the urgency signal is solely whether the symbol appeared in the URGENT WATCHDOG ALERTS section of the user prompt.

---

## 05 — Close-Action Execution Flow

The path from "brain says close X" to "Bybit close order placed" has three layers (LayerManager → TradeCoordinator → PositionWatchdog) and one external call (`position_service.close_position`).

### 05.1 Hop-by-hop trace

| # | File:Line | Action |
|---|---|---|
| 1 | `src/brain/strategist.py:4526–4610` | `_parse_position_plan` builds `PositionAction` objects |
| 2 | `src/brain/strategist.py:4603–4608` | Log `STRAT_CALL_B_PARSED \| total=N close=N ...` |
| 3 | `src/brain/strategist.py:1005–1013` | Log `STRAT_CALL_B_PLAN \| acts=N` and per-action `STRAT_POS_ACT` |
| 4 | `src/core/layer_manager.py` (~ line 961–991) | `_execute_position_actions(plan)` gated on Layer 3 active; emits `BRAIN_CYCLE_B_DONE` |
| 5 | `src/core/layer_manager.py:1190–1194` | Snapshot of `active_symbols` from coordinator |
| 6 | `src/core/layer_manager.py:1211–1218` | Phantom-close defense: `if symbol not in active_symbols: log CALL_B_STALE_SNAPSHOT_DETECTED; continue` |
| 7 | `src/core/layer_manager.py:1221–1228` | SENTINEL firewall check (if enabled) |
| 8 | `src/core/trade_coordinator.py:406–416` | `set_close_reason(symbol, "strategic_review")`, log `POSITION_CLOSE_REASON` |
| 9 | `src/core/trade_coordinator.py:319–345` | `queue_strategic_action(symbol, "close", reason, new_sl, exit_price)`; phantom-close secondary defense at lines 319 → `PHANTOM_CLOSE_REJECTED`; emit `COORD_QUEUE \| sym=... act=close` |
| 10 | `src/core/layer_manager.py:1250` | Emit `STRAT_POS_ACT \| sym=... act=close rsn='...'` |
| 11 | `src/workers/position_watchdog.py:698` | Watchdog tick loop |
| 12 | `src/workers/position_watchdog.py:2935–2945` | `_execute_strategic_actions()` calls `coordinator.drain_strategic_actions()` |
| 13 | `src/workers/position_watchdog.py:2950–2960` | Re-verify position still exists; on miss emit `POS_ACTION_SKIP` |
| 14 | `src/workers/position_watchdog.py:2980–3021` | Minimum-hold guardrail: block if `age_sec < strategic_action_min_hold_seconds` (default 300 s) AND reason not in the whitelist. Whitelist (lines 2985–2994): `["stop loss hit", "sl hit", "take profit hit", "tp hit", "structure invalidated", "setup broken", "regime change", "regime shift", "manual operator close", "manual close"]`. Block log: `STRAT_ACTION_CLOSE_BLOCKED \| sym=... age=...s min_hold=...s` |
| 15 | `src/workers/position_watchdog.py:3024–3026` | `if act in ("close","take_profit"): await self.position_service.close_position(symbol, close_trigger="wd_claude_action"); log STRAT_ACTION_CLOSE` |
| 16 | position-service implementation (bybit or shadow adapter) | Sends actual Bybit POST close order; `close_trigger` is recorded in close attribution. |

### 05.2 Where `by=wd_claude_action` is set

Single location: `src/workers/position_watchdog.py:3025`:

```python
await self.position_service.close_position(symbol, close_trigger="wd_claude_action")
```

The literal string `"wd_claude_action"` is passed as the `close_trigger` keyword argument. Downstream, the position service attaches it to the close event, and trade-history rows record it as the close reason.

### 05.3 Does brain bypass the watchdog?

No. Three structural facts:

1. LayerManager calls `coordinator.queue_strategic_action(...)`, not `position_service.close_position(...)`. The action is queued, not executed.
2. The coordinator's `_strategic_actions` list is read by only one consumer — the watchdog's `_execute_strategic_actions()` (position_watchdog.py:2939).
3. The watchdog performs three checks (position-still-alive, min-hold guardrail, error handling) before issuing the Bybit close.

There is a legacy fallback path (`_ask_brain` → `_execute_decision` at position_watchdog.py:2362–2365 → 2569–2610) that calls Claude directly when `urgent_queue` is unavailable and tags the close `close_trigger="wd_full_close"` (line 2703). That path uses `wd_full_close`, NOT `wd_claude_action`, so the audit in Section 07 is specifically capturing the LayerManager→queue→watchdog path.

### 05.4 Verbatim log events emitted

| Phase | Event | Source |
|---|---|---|
| Parse | `STRAT_CALL_B_PARSED` | strategist.py:4603–4608 |
| Per-action note | `STRAT_POS_ACT` | strategist.py:1009–1013 |
| Plan summary | `STRAT_CALL_B_PLAN` | strategist.py:1006–1008 |
| Brain cycle end | `BRAIN_CYCLE_B_DONE` | layer_manager.py ~988–991 |
| Stale-snapshot drop | `CALL_B_STALE_SNAPSHOT_DETECTED` | layer_manager.py:1211–1218 |
| Reason set | `POSITION_CLOSE_REASON` | trade_coordinator.py:414–416 |
| Phantom defense | `PHANTOM_CLOSE_REJECTED` | trade_coordinator.py:319–345 |
| Queue insert | `COORD_QUEUE` | trade_coordinator.py:345 |
| Watchdog block | `STRAT_ACTION_CLOSE_BLOCKED` | position_watchdog.py:3014–3020 |
| Watchdog skip (missing) | `POS_ACTION_SKIP` | position_watchdog.py:2954–2957 |
| Watchdog gone | `STRAT_ACTION_GONE` | position_watchdog.py:3062 |
| Watchdog exec err | `STRAT_ACTION_ERR` | position_watchdog.py:3064 |
| Close issued | `STRAT_ACTION_CLOSE` | position_watchdog.py:3026 |

---

## 06 — Position Watchdog Close Paths

The watchdog has nine close paths plus an emergency-mode "close-all" trigger. Each path below lists trigger condition, log event, close-reason string, and inspected state with file:line references in `src/workers/position_watchdog.py`.

### 06.1 Path 1 — SENTINEL Deadline (`wd_dl_action`)

- Lines: 1643–1708.
- Condition: `plan.is_expired` AND `_sentinel_deadline` available AND `_dl_action.should_close` is True.
- Close call: `position_service.close_position(pos.symbol, close_trigger="wd_dl_action")` (line 1677).
- Log: `SENTINEL_DEADLINE_CLOSE` (lines 1654–1657, with tier/pnl/age/reason).
- Coordinator callback: `closed_by=f"sentinel_deadline_{_dl_action.tier.value}"` (line 1702).
- State inspected: `plan.is_expired`, `pnl_from_plan`, `_dl_action.tier.value`.

### 06.2 Path 2 — Plan-Timer Fallback (`wd_plan_timer`)

- Lines: 1713–1749.
- Condition: SENTINEL not wired AND `plan.is_expired`.
- Close call: `close_position(pos.symbol, close_trigger="wd_plan_timer")` (line 1714+).
- Log: `PLAN TIMER` (line 1714).
- Coordinator callback: `closed_by="plan_timer"` (line 1742).
- State inspected: `plan.is_expired`, `plan.age_minutes`, `plan.max_hold_minutes`, `pnl_from_plan`.

### 06.3 Path 3 — Trailing Stop Hit (`wd_trail`)

- Lines: 1770–1818.
- Condition: `plan.trailing_active` AND `plan.should_trail_exit(current_price)`.
- Close call: `close_position(pos.symbol, close_trigger="wd_trail")` (line 1785).
- Log: `TRAIL HIT` (line 1785).
- Coordinator callback: `closed_by="trailing_stop"` (line 1811).
- State inspected: `plan.trailing_active`, `plan.trailing_stop_price`, `plan.trailing_activation_pct`, `current_price`.

### 06.4 Path 4 — Time-Decay Force-Close (`time_decay_force_close`)

- Lines: 1260–1596 (`_handle_time_decay`).
- Condition: `outcome == -1.0` returned by TimeDecaySLCalculator, gated by `state.p_win < td_cfg.p_win_force_close`, plus regime/structural sub-gates (regime inversion check + Layer4 XRAY structural verdict).
- Close call: `close_position(pos.symbol, close_trigger="time_decay_force_close")` (line 1530+).
- Log: `TIME_DECAY_CLOSE` (line 1540).
- Coordinator callback: `closed_by="time_decay_p_win_low"` (line 1570).
- State inspected: `TimeDecayState` (`p_win`, `mae_pct`, velocity, acceleration, tick_count), `regime_still_supports`, `struct_inv` from `layer4_protection.compute_structural_invalidation`, `min_age_seconds`.

### 06.5 Path 5 — Hard Stop (`wd_hard_stop`)

- Lines: 1928–1965.
- Condition: `pnl_pct < -3.0`.
- Close call: `close_position(pos.symbol, close_trigger="wd_hard_stop")` (line 1928+).
- Log: `HARD STOP` (line 1932).
- Coordinator callback: `closed_by="hard_stop"` (line 1958).
- State inspected: `pnl_pct`. No other gates.

### 06.6 Path 6 — Timeout (`wd_timeout`)

- Lines: 2013–2061.
- Condition: `pnl_pct < 0` AND `time_used_pct = age/max_hold * 100 > timeout_threshold_pct` (default 95).
- Extension: one-time grace if `pnl_pct >= -0.5` and not already extended → `plan.max_hold_minutes += 10` (lines 2018–2026).
- Close call: `close_position(pos.symbol, close_trigger="wd_timeout")` (line 2028+).
- Log: `TIMEOUT` (line 2029).
- Coordinator callback: `closed_by="timeout"` (line 2054).
- State inspected: `plan`, `pnl_pct`, `plan.age_minutes`, `plan.max_hold_minutes`, `_timeout_pct`, `_extended` flag.

### 06.7 Path 7 — Profit Take (`wd_profit_take`)

- Lines: 2063–2102.
- Condition: `pnl_pct > 1.5` AND `time_used_pct > 50`.
- Close call: `close_position(pos.symbol, close_trigger="wd_profit_take")` (line 2066+).
- Log: `PROFIT TAKEN` (line 2069).
- Coordinator callback: `closed_by="profit_take"` (line 2095).
- State inspected: `pnl_pct`, `plan.age_minutes`, `plan.max_hold_minutes`.

### 06.8 Path 8 — Early Exit (`wd_early_exit`)

- Lines: 1820–1916.
- Condition: `pnl_pct < -0.5` AND `time_pct > 50` AND `pnl_pct < -1.0` AND ALL of `brain_said_hold`, `regime_aligned`, `sl_buffer_ok` are False AND `early_exit_enabled` is True.
- Close call: `close_position(pos.symbol, close_trigger="wd_early_exit")` (line 1882+).
- Log: `EARLY EXIT` (line 1883).
- Coordinator callback: `closed_by="early_exit"` (line 1909).
- State inspected: `pnl_pct`, `time_pct`, `_consecutive_holds[symbol]`, regime alignment, SL-buffer.
- Default: disabled (`early_exit_enabled = False`).

### 06.9 Path 9 — Strategic Action / Brain (`wd_claude_action`)

- Lines: 2935–3065.
- Condition: `action["action"] in ("close","take_profit")` AND minimum-hold guardrail passed (see 05.1 hop 14).
- Close call: `close_position(symbol, close_trigger="wd_claude_action")` (line 3025).
- Log: `STRAT_ACTION_CLOSE` (line 3026).
- Coordinator callback: none from the watchdog (the strategic action originates from the brain via LayerManager; close attribution is set upstream at `coordinator.set_close_reason("strategic_review")` then overridden by `close_trigger` downstream).
- State inspected: queued action's reason vs. whitelist, position age, position still-exists.

### 06.10 Emergency-mode close-all

- Lines: ~414–660 (multiple touchpoints).
- Condition: `self._session_pnl_pct < session_threshold` (default −5 %).
- Closes all positions with watchdog session-emergency reason. Logged with `session_pnl=` context.

### 06.11 Where "PASSIVE" brain concerns live

In PASSIVE mode the watchdog does NOT close from brain hints directly. Instead, between lines 2331–2360 it pushes concerns into `urgent_queue.push_concern(...)`. Those are then formatted by `UrgentQueue.format_for_prompt` and injected into the NEXT CALL_B prompt (see 03.7). The brain then decides; if it returns a close, that path goes through Path 9 (`wd_claude_action`). The legacy direct path at 2362–2365 (when `urgent_queue` is None) calls `_ask_brain` → `_execute_decision`, which uses tags like `wd_full_close` (line 2703), `wd_partial_close`, or `wd_tighten_sl`.

### 06.12 Summary of close-reason strings emitted

| Reason string | Path | Default-enabled? |
|---|---|---|
| `wd_dl_action` | SENTINEL deadline | Yes |
| `wd_plan_timer` | Plan-timer fallback | Yes (when SENTINEL absent) |
| `wd_trail` | Trailing stop | Yes (when activated) |
| `time_decay_force_close` | TimeDecay calculator | Yes |
| `wd_hard_stop` | -3 % loss | Yes |
| `wd_timeout` | >95 % time used + losing | Yes |
| `wd_profit_take` | >+1.5 % PnL after 50 % time | Yes |
| `wd_early_exit` | Multi-gate early exit | No (disabled by default) |
| `wd_claude_action` | Brain strategic action | Yes |
| `wd_full_close` / `wd_partial_close` / `wd_tighten_sl` | Legacy direct-brain path | Only when `urgent_queue` is unavailable |

---

## 07 — wd_claude_action Close Events From Recent Logs

### 07.1 Source and method

- Source log: `/home/inshadaliqbal786/SYSTEM_LOGS_2026-05-17_05-00_to_11-00.log`.
- Match command: `grep 'COORD_CLOSE_START' SYSTEM_LOGS_2026-05-17_05-00_to_11-00.log | grep 'wd_claude_action'`.
- For each match, the surrounding context (entry, exit, size, PnL, time held) was extracted from neighboring `COORD_*` and `POS_CLOSED` lines. Decision-id matching was attempted via `STRAT_CALL_B_PARSED | close>=1` events with the same symbol in a +/- 5-minute window, then cross-referenced against `data/stage2_dumps/` filenames (which encode `d-<decision_id>`).
- Post-close price-trajectory verification was not possible from the SYSTEM_LOGS file alone (ticker data is not in this log). Retrospective verdicts are inferred from SL %, time remaining at close, and contextual regime/WR — not from forward price.

### 07.2 Audit table

| # | Timestamp UTC | Symbol | Side | Entry | Exit | Size | PnL % (USD) | Time held | Decision_id | Inferred verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 06:33:54 | LTCUSDT | Buy | 56.27 | 56.18 | 23.9 | −0.16 % (−2.15) | 50.4 min | d-1778999608296 | Worsened — 53 % SL consumed, 5 min remaining, long in TRENDING_UP 44 % while market regime was ranging |
| 2 | 07:23:47 | ARBUSDT | Sell | 0.1216 | 0.12184 | 12,582 | −0.20 % (−3.02) | 17.8 min | NOT FOUND | Unknown |
| 3 | 07:36:32 | FILUSDT | Sell | 0.9731 | 0.9776 | 1,572 | −0.46 % (−7.07) | 30.6 min | d-1779003303131 | Worsened — 64 % SL consumed, 16 min remaining |
| 4 | 07:42:46 | SANDUSDT | Sell | 0.07306 | 0.07342 | 20,941 | −0.49 % (−7.54) | 27.7 min | NOT FOUND | Unknown |
| 5 | 08:01:03 | AAVEUSDT | Sell | 90.55 | 90.74 | 16.89 | −0.21 % (−3.21) | 46.0 min | NOT FOUND | Unknown |
| 6 | 08:22:44 | RUNEUSDT | Buy | 0.4571 | 0.4538 | (n/a) | −0.72 % (−7.04) | 31.2 min | d-1779006078145 | Worsened — 41–43 % SL consumed, 15 min remaining, buy in ranging regime |
| 7 | 08:46:36 | BSBUSDT | Buy | 0.4706 | 0.4647 | (n/a) | −1.25 % (−28.21) | 8.3 min | d-1779007742639 | Worsened — 39 % SL consumed, 3 min remaining, longs 20 % WR |
| 8 | 09:23:28 | MONUSDT | Buy | 0.027876 | 0.028087 | (n/a) | −0.76 % (−7.66) | 36.5 min | NOT FOUND | Unknown |
| 9 | 09:23:28 | ORCAUSDT | Buy | 1.4727 | 1.4824 | (n/a) | −0.66 % (−7.56) | 27.0 min | NOT FOUND | Unknown |
| 10 | 09:23:29 | ICPUSDT | Buy | 2.614 | 2.619 | (n/a) | −0.19 % (−1.94) | 27.1 min | NOT FOUND | Unknown |
| 11 | 09:44:47 | HYPEUSDT | Buy | 42.917 | 42.83 | (n/a) | −0.20 % (−2.74) | 31.7 min | d-1779010995797 | Worsened — 38 % SL consumed, 10 min remaining, longs 17 % WR |
| 12 | 09:52:50 | LINKUSDT | Buy | 9.763 | 9.782 | (n/a) | −0.19 % (−2.23) | 39.7 min | d-1779011542019 | Worsened — 68 % SL consumed, 6 min remaining, price one tick from SL |
| 13 | 10:06:25 | ORCAUSDT | Buy | 1.4854 | 1.4945 | (n/a) | −0.61 % (−11.72) | 34.3 min | NOT FOUND | Unknown |

13 events total. 6 matched to stage2 dumps (events 1, 3, 6, 7, 11, 12). All 13 were losing closes; aggregate `wd_claude_action` PnL line in the dump's RECENT PERFORMANCE block independently reported "wd_claude_action 10 (W 0%)" in the trailing 50-close window.

### 07.3 Notes for operator (Section 07)

- The 7 unmatched events may have been closed via the legacy direct path (Section 06.11) — that path tags `wd_full_close`, not `wd_claude_action`, so this hypothesis is weaker. More likely: brain emitted a close in a CALL_B cycle whose stage2 dump rotated out before the cluster cluster captured. Operator may want to widen the dump retention or correlate against the `data/decisions/*.json` artifacts if those exist.
- Position-size for events 6–13 was not parseable from the immediately adjacent log lines and was returned as `(n/a)` rather than guessed.
- Forward-price (was the close justified?) requires correlating against a ticker source (ShadowKlineReader DB or live kline log) that was not in scope for this read-only investigation.

---

## 08 — Brain Reasoning Analysis on Close Decisions

Six matched stage2 dumps. The full `reasoning` strings are verbatim from the dump JSON. Each is classified specific vs. vague per the spec.

### 08.1 LTCUSDT (d-1778999608296)

```text
SL consumed 53% with only 5min remaining. Long position in weak trending up regime (44%) while market regime is ranging and shorts are 80% WR today. Price at 56.18 with SL at 56.10 — recovery unlikely in remaining time. Structural invalidation: regime doesn't support longs.
```

Cites: SL %, time remaining, regime divergence, daily WR, price level. Classification: specific, multi-factor.

### 08.2 FILUSDT (d-1779003303131)

```text
URGENT — 64% SL consumed, -0.51% PnL, only 16min remaining. Price steadily drifting toward SL with no sign of reversal. Recovery unlikely in remaining time window. Close to avoid full SL hit.
```

Cites: SL %, PnL, price trajectory, time. Classification: specific, SL-focused.

### 08.3 RUNEUSDT (d-1779006078145)

```text
URGENT: -0.74% loss with 41-43% SL consumed and only 15min remaining. Buy position at -0.74% loss, dropped 0.83% from peak. Buy in a ranging regime (40%) where shorts dominate. No recovery time left, drifting toward SL. Close to preserve capital.
```

Cites: PnL, SL %, peak-to-current drawdown, regime, time. Classification: specific, regime-mismatch.

### 08.4 BSBUSDT (d-1779007742639)

```text
WATCHDOG ALERT. Buy position at -0.20% with 39% SL consumed and only 3min remaining. Longs are 1W/4L (20% WR) today. Not flipped — original long in a ranging regime where shorts dominate. No recovery time left, drifting toward SL. Close to avoid further give-back.
```

Cites: PnL, SL %, time remaining, daily directional WR, regime, FLIPPED-status. Classification: specific, time-pressure dominant.

### 08.5 HYPEUSDT (d-1779010995797)

```text
URGENT: SL consumed 38% and accelerating toward stop with only 10min remaining. Price dropped from $42.92 to ~$42.77. Insufficient time for recovery. Longs performing poorly today (17% WR). Closing to avoid full SL hit on a position that's running out of runway.
```

Cites: SL %, price movement, time remaining, daily directional WR. Classification: specific, WR + SL acceleration.

### 08.6 LINKUSDT (d-1779011542019)

```text
SL consumed 68% with only 6min remaining. Price at 9.78 vs SL at 9.79 — one tick from stop. No realistic recovery window. Close to avoid full SL hit.
```

Cites: SL %, price-to-SL proximity, time remaining. Classification: specific, critical-state.

### 08.7 Patterns

- 6 / 6 matched events used specific data points; 0 / 6 used vague loss-avoidance language.
- Dominant cited factors:
  - SL consumption (6 / 6, range 38–68 %).
  - Time remaining (6 / 6, range 3–16 min).
  - Directional WR (3 / 6, all sessions where the position's direction was 17–25 % WR).
  - Regime mismatch (3 / 6, longs in ranging or short-favored regimes).
  - Peak-to-current drawdown or price acceleration toward SL (2 / 6).
- All 6 events closed losing positions whose SL was 30–70 % consumed with less than 20 minutes left in the trade window. Brain was internally consistent about the close criteria — its reasoning aligned with the system prompt's "SL is approaching and recovery looks unlikely" clause.
- Forward-price retrospective (would the position have recovered?) is not in scope; see 07.3.

---

## 09 — Available Factors for Watchdog Scoring

The watchdog already has live access to the following factors. Each entry lists the access method and a file:line in `src/workers/position_watchdog.py` unless noted otherwise.

### 09.1 Direct factors

| Factor | Access | File:line |
|---|---|---|
| Current PnL % | `_calculate_pnl_pct(pos, current_price)` | 2812–2819 |
| Stored last PnL | `self._last_pnls[symbol]` | 2134 |
| Position age (minutes) | `plan.age_minutes` | 1822, 1919, 2014–2016 |
| Position age (seconds) | `coordinator.get_age_seconds(symbol)` | 3002 |
| Position open time | `self._position_open_times[symbol]` | 334, 2429 |
| Remaining time | `plan.remaining_minutes`, `plan.is_expired`, `plan.max_hold_minutes` | 1644, 1924, 2016 |
| SL distance / proximity % | `_calculate_sl_proximity(pos, current_price)` | 2822–2842 |
| Take-profit price | `pos.take_profit`, `plan.target_price` | 2495, 2790 |
| Current price | `ticker.last_price` from MarketService | 1613 |
| Price change tick-to-tick | `price_change_pct = (current - prev) / prev * 100` | 2119–2120 |
| PnL velocity / acceleration | `td_observe(state, pnl_pct)` returns `(velocity, acceleration)` | 1456 |
| Choppiness index | `ta_engine.analyze(candles=klines).volatility.choppiness_index` | 2218, 2627 |
| XRAY structural verdict | `layer4_protection.compute_structural_invalidation(...)` | 1478 |
| Recorded struct verdict | `layer4_protection.record_struct_guard_verdict(symbol, verdict)` | 1521 |
| Per-coin regime | `regime_detector.get_coin_regime(symbol)` | 1196, 1318, 1466, 1833 |
| Entry-time regime | Loaded from thesis table | 1355–1385 |
| Trail-SL state | `plan.trailing_active`, `plan.trailing_stop_price`, `plan.trailing_activation_pct` | 1752, 1771 |
| TimeDecayState | `self._td_states[symbol]` (fields: `p_win`, `mae_pct`, `velocity_pct_per_s`, `acceleration_pct_per_s2`, `tick_count`, `last_pnl_pct`) | 293, 1395–1456 |
| MAE high-water | `self._td_mae_high_water[symbol]` | 1588, 1999 |
| Sentinel deadline action | `_sentinel_deadline.evaluate(symbol, pnl_pct, entry_price, direction)` | 1645–1648 |
| Sentinel advisor recommendations | `_sentinel_advisor.drain_recommendations()` | 3068, 3072 |
| Strategy / score / consensus (context only) | `coordinator.get_trade_info(symbol)` | 2757, 2795–2797 |
| Peak PnL (USD) | `self._position_peaks[symbol]` | 309, 2107–2108 |
| Session PnL % | `self._session_pnl_pct` | 346, 418 |
| Consecutive brain holds | `self._consecutive_holds[symbol]` | 1824–1858 |

### 09.2 Factors NOT directly accessible to the watchdog

- **Profit-sniper hints**: `src/workers/profit_sniper.py` runs as a separate worker. The two services share only `layer4_protection.get_struct_guard_verdict` and the trade coordinator's `on_trade_closed` callback. There is no API for the sniper to feed hints into the watchdog's close decision in real time; the sniper closes positions independently with reasons like `mode4_stall_valve`.
- **Cross-position correlation** (e.g., are 3 of my longs all in the same coin family?). The watchdog ticks per-symbol; no aggregated view is built in the close decision path.
- **Forward price (next-N-minute projection)**: not computed.

### 09.3 Availability matrix

| Factor | Available? | Clean interface? | Read in close decisions today? |
|---|---|---|---|
| PnL % | Yes | `_calculate_pnl_pct` | Yes (every path) |
| Position age | Yes | `plan.age_minutes` | Yes |
| Time remaining | Yes | `plan.is_expired`, `plan.max_hold_minutes` | Yes |
| SL distance / consumption | Yes | `_calculate_sl_proximity` | Yes |
| TP distance | Yes | `pos.take_profit` | Logged only |
| Current vs entry | Yes | implicit in PnL calc | Yes |
| Price velocity / acceleration | Yes | `td_observe` | Yes (TimeDecay path) |
| Choppiness | Yes | `ta_engine.analyze` | Used for rapid-move gate |
| XRAY structural state | Yes | `layer4_protection.compute_structural_invalidation` | Yes (TimeDecay path) |
| Per-coin regime | Yes | `regime_detector.get_coin_regime` | Yes (early-exit, time-decay) |
| Trail-SL state | Yes | `plan.trailing_*` | Yes |
| TimeDecayState | Yes | `self._td_states` | Yes |
| Sentinel signals | Yes | `_sentinel_deadline.evaluate`, advisor | Yes |
| Conviction / consensus | Yes (context only) | `coordinator.get_trade_info` | No automated use |
| Peak PnL | Yes | `self._position_peaks` | Yes (smart trail, breakeven) |
| Session PnL | Yes | `self._session_pnl_pct` | Yes (emergency mode) |
| Profit-sniper hints | No | n/a | No |

### 09.4 Notes for operator (Section 09)

- All factors used in the existing nine close paths are also available at the moment a `wd_claude_action` would fire — the watchdog reads everything in 09.1 every tick regardless of close path.
- The watchdog already runs through this state-snapshot when deciding `wd_dl_action`, `wd_timeout`, `wd_profit_take`, `wd_early_exit`, and `time_decay_force_close`. A scoring layer that intercepts `wd_claude_action` would be able to reuse the same snapshot without additional plumbing.
- `coordinator.get_trade_info(symbol)` exposes `strategy_name`, `score`, `consensus` (conviction). It is read for logging at lines 2795–2797 but is not currently consumed by any automated gate.

---

## 10 — Appendix: Off-Topic Items Noted but Not Investigated

Per Rule 7 (stay on CALL_B and watchdog), the following observations were noticed during reading but were not pursued. They are recorded for the operator's situational awareness only.

1. The aggregated last-50-closes block emitted to brain (`wd_claude_action 10 (W 0%)` in dump 20260517T094438) flags an already-known issue — brain is being shown its own poor close-track record yet continues to issue closes when SL > 30 % and time remaining < 20 min. This is the motivating data for the Option D fix; investigation of why the prompt language is not deterring brain was out of scope.
2. The legacy direct-brain path (`position_watchdog.py:2362–2365` → `_execute_decision` at 2569–2610) uses different close tags (`wd_full_close`, `wd_partial_close`, `wd_tighten_sl`). If the operator widens this audit beyond `wd_claude_action`, those should be grep'd separately.
3. `claude_code_client.py:1036–1046` accepts top-level JSON arrays and rewrites them to `{"decisions": result}`. CALL_B's parser expects `position_actions`, so an array response would currently fail downstream — flag as a robustness gap to revisit later.
4. The TimeDecaySLCalculator and SENTINEL deadline engine are wired but their configuration knobs were not enumerated. If the watchdog-override scoring relies on them, their `td_cfg.*` and sentinel tier thresholds will need their own capture pass.
5. `data/stage2_dumps/` retention policy was not inspected. 7 / 13 `wd_claude_action` events in the audit window had no matching dump; if dumps rotate aggressively, the operator may want to enable a longer retention before the next audit.

---

End of report.
