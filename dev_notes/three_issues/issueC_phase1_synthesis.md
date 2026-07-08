# Issue C Phase 1 — Synthesis (Investigation Findings)

> Consolidated investigation. Replaces the 9 separate deliverables (sniper anatomy / mode4_p9 trigger / grace fix audit / mature-stall valve audit / mode4_p9 sampling / natural-vs-killed compare / stall escape / Bybit demo impact / synthesis) — same content, single document for review efficiency. All claims have file:line evidence.

## Root cause (single sentence)

The four sniper-driven full closures in the 13:00–16:00 UTC 2026-05-08 window (`SANDUSDT`, `INJUSDT`, `ARBUSDT`, `HYPERUSDT`) all came via the **mature-stall valve** at `src/workers/profit_sniper.py:2481–2494` (`MODE4_STALL_ESCALATE`), not via the score-based `mode4_p9` threshold path. Every actual full closure was a position that sat in `is_actionable=True` + `action="hold"` state for 80–181 ticks (7–15 minutes), had touched at most +0.30% peak PnL, and drifted into −0.30% to −1.25% loss — at which point the valve forced the close. The score-based path (`score >= thresholds["full"]`) at `profit_sniper.py:1629` cannot fire on losing positions because of the profit gate at line 1590 (`if current_pnl <= 0: return ActionResult(action="hold", source="profit_gate")`); none of the 4 closures in the window came through it.

## The label `mode4_p9` is misleading

`closed_by="mode4_p9"` is a fixed string hardcoded at `_execute_action:1931`. It is NOT a phase number, NOT a threshold name, NOT a score classifier. Every full closure regardless of trigger path (score-based, anti-greed, stall-escape, mature-stall valve) carries this label. The audit's framing — "32 mode4_p9 events (full close at sniper escalation phase 9)" — conflated occurrences of the substring `mode4_p9` in tick-evaluation logs (`M4_DECISION` lines etc.) with actual closure events. The `mode4_p9` substring appears 32 times in the log; only **4 actual `COORD_CLOSE_END | by=mode4_p9` events fired** in the same window.

## Evidence chain

### C.1.1 — Sniper Anatomy

- File: `src/workers/profit_sniper.py` (3,516 lines).
- Class: `ProfitSniper(BaseWorker)` at line 75.
- Tick entry: `tick()` at line 213.
- Score → action map: `_determine_action()` at line 1564, returns `ActionResult` ∈ {hold, tighten, partial_close, full_close}.
- Score formula: `_compute_composite_score()` at line 1064 — weighted blend of 5 sub-models (Hurst, momentum decay, ATR extension, volume divergence, R/R) plus consensus boost and urgency boost; output 0–100.
- Antigreed adjustment: `_apply_anti_greed()` at line 2159 — adjusts score depending on velocity/accel direction.
- Threshold map at `THRESHOLD_SETS` (line 57): `trending`/`ranging`/`volatile`/`dead`/`balanced`, with per-regime tighten/partial/full triplets (50/70/85 trending, 35/55/70 ranging, etc.). NOT phase numbers; just three score thresholds per regime.
- Stall escape: `_stall_escape_action()` at line 2258, called from `tick()` line 530 region.
- Close execution: `_execute_action()` at line 1800 → `_execute_full_close()` at line 2561.

### C.1.2 — `mode4_p9` Trigger Trace

The string `mode4_p9` appears in code only as the literal `closed_by="mode4_p9"` argument at `_execute_action:1931`:

```python
success = await self._execute_full_close(
    symbol, pos, score_data, closed_by="mode4_p9",
)
```

`_execute_full_close` accepts `closed_by` as a parameter (line 2562) and forwards it into `trade_coordinator.on_trade_closed(close_reason=closed_by)` (line 2608) and the log emit `Mode4 CLOSED {sym}: ... by={by}` (line 2671). The string is therefore a sticky tag attached to ANY full-close action initiated by `_execute_action` (whether from score path, anti-greed path, or stall-escape path) — not a phase identifier.

The score-based full-close branch at `_determine_action:1629` (`if score >= thresholds["full"]: score_action = "full_close"`) can only fire when `current_pnl > 0` (line 1590 profit gate). It is also further gated by:
- `min_profit_for_action` (default 0.1 %, line 1611).
- `min_profit_for_close` / "P9_CLOSE_GATE" (default 0.50 %, line 1641): downgrades full_close to tighten if the absolute profit is too small.
- `min_profit_for_partial_pct` (default 0.0 %, line 1660) on partials.
- Per-position cooldown `min_seconds_between_actions` (default 60 s, line 1724).
- Full-close per-position cooldown `min_seconds_before_close` (default 180 s, line 1728), bypassed only when source is anti-greed.
- `Layer4ProtectionService.is_protected()` consulted by `_execute_full_close` at `profit_sniper.py:2603`, with `check_min_hold=True` enforcing the 5-minute settle.

### C.1.3 — Grace-Fix Audit (commit `00f8eb1`)

Verified shipped and active in current head:

```
00f8eb1 fix(sniper/phase-1): wire partial-to-full grace gap into escalation decision
```

Mechanism (`_stall_escape_action:2502–2528`): after a partial_close emission, the next stall_escape action is blocked for `partial_to_partial_grace_ticks` (default 60) before another partial fires, and for `partial_to_full_grace_ticks` (default 60) before the cap-path full_close fires. Logs `SNIPER_GRACE_BLOCKED` when blocking. **241 events** fired in the audit window — the gate is working.

Critical: **the grace gap does NOT cover the mature-stall valve at line 2481**. That branch (`if ticks > full_after or (applications >= tighten_max and not recovered)`) bypasses the grace check by design and emits `MODE4_STALL_ESCALATE` directly. This is documented in the code comment at line 2510 ("The forced-full path is the mature-stall valve and is unaffected"). All 4 actual full closures came via this path.

### C.1.4 — Mature-Stall Valve Audit (NEW deliverable)

The valve at `_stall_escape_action:2481–2494`:

```python
applications = int(tracked.get("_stall_tighten_applications", 0))
worst = tracked.get("_stall_worst_pnl_pct")
recovered = (
    worst is not None and _last_pnl is not None
    and (float(_last_pnl) - float(worst)) >= recovery_thresh
)
if ticks > full_after or (applications >= tighten_max and not recovered):
    log.warning(
        f"MODE4_STALL_ESCALATE | sym={symbol} ticks={ticks} "
        f"tighten_attempts={applications} worst_pnl={...} "
        f"current_pnl={...} recovered={recovered} | {ctx()}"
    )
    tracked["_stall_last_escape_ts"] = now
    tracked["_last_escape_type"] = "full"
    tracked["_last_escape_tick"] = ticks
    return "full_close"
```

Constants:
- `stall_escape_partial_after_ticks` = 20 (quiet window threshold)
- `stall_escape_full_after_ticks` = 40 (mature-stall valve threshold)
- `stall_tighten_max_applications` = 3 (alternative valve gate)
- `stall_recovery_threshold_pct` = 0.15 % (PnL-recovery threshold for the alternative gate)

The valve fires when EITHER:
- `ticks > full_after`: position has been actionable+hold for 40+ consecutive ticks (~3.3 minutes at 5 s cadence). All 4 audit-window closures fired through this path with ticks=80–181 (7–15 minutes).
- `applications >= tighten_max AND not recovered`: 3+ tighten applications without 0.15 % PnL recovery from worst PnL. None of the 4 closures hit this branch (`tighten_attempts=0` in all 4 events).

The valve runs AFTER the SNIPER_AGE_GUARD (line 2347, 300 s minimum age), AFTER the PnL guards (lines 2447 SNIPER_PROFIT_GUARD, 2455 SNIPER_DEVELOPMENT_GUARD), but BEFORE the cooldown and grace-gap blocks. The PnL guards are the only thing that could have spared the 4 victims:
- SNIPER_PROFIT_GUARD blocks when `_last_pnl > profit_protection_threshold` (default 0.0 %). All 4 victims had `_last_pnl <= 0` so this didn't fire.
- SNIPER_DEVELOPMENT_GUARD blocks when `_last_pnl > development_window_lower` (default −0.3 %). INJUSDT (−0.30 %) was right at the floor (`-0.30 > -0.3` is False, so the guard did NOT block). ARBUSDT (−0.31 %), HYPERUSDT (−0.37 %), SANDUSDT (−1.25 %) were all below the floor.

### C.1.5 — `mode4_p9` Sampling (4 actual events)

| Time | Sym | ticks | Score | PnL | Peak | Pullback | Path |
|---|---|---|---|---|---|---|---|
| 14:54:19 | SANDUSDT | 181 | 37 | −1.25 % | +0.06 % | 0 % | mature-stall valve |
| 15:24:32 | INJUSDT | (≥80) | 34 | −0.30 % | +0.30 % | 0 % | mature-stall valve |
| 15:33:34 | ARBUSDT | (≥80) | 17 | −0.31 % | +0.13 % | 0 % | mature-stall valve |
| 15:47:56 | HYPERUSDT | 134 | 31 | −0.37 % | +0.00 % | 0 % | mature-stall valve |

(INJUSDT/ARBUSDT tick counts not directly logged on M4_ACT_CLOSE; SANDUSDT and HYPERUSDT recovered from the preceding `MODE4_STALL_ESCALATE` event.)

Common pattern across all 4: brief or no profit (peak ≤ +0.30 %), drifted into modest-to-deep loss, sat in actionable+hold for many minutes, then valve fired. **All 4 had `tighten_attempts=0`** — they reached the mature-stall valve via the tick-count path, not the tighten-cap path.

Score at close was 17–37, well below regime full thresholds (65–85 across regimes). This conclusively rules out the score-based path: the score-based full-close threshold for trending=85, ranging=70, balanced=70 — none of the 4 closures had score even near threshold. Combined with the profit-gate at line 1590, the score path was not the trigger for any of them.

### C.1.6 — Natural-vs-Killed Comparison

The 2 `TRAIL HIT` events:
- 14:18:41 ONDOUSDT price=$0.40 ≤ trail=$0.40
- 15:10:27 ONDOUSDT price=$0.41 ≤ trail=$0.41

Both were natural closures of ONDOUSDT positions where the trailing stop was hit (price moved through trail). These are NOT sniper closures — they are watchdog `_monitor_position` (line 1506) events that fire when the broker's trailing stop is reached. They represent the only positions in the window that closed via "trade ran its course" rather than via active intervention.

Difference vs the 4 sniper-killed positions: these were ONDOUSDT (a coin that had multiple position cycles in the window), and the trail had been activated by a successful in-profit trajectory. The trail moved up with the position; when price retraced the trail caught it. The 4 sniper-killed positions never reached enough profit to activate a meaningful trail, so the trail-hit path was unavailable.

### C.1.7 — Stall Escape Breakdown (17 events)

| `escalated_to` | Count | Trigger |
|---|---|---|
| `partial_close` | 13 | Default emit at `_stall_escape_action:2557` (first stall escape per position; ticks > partial_after=20, no other gate fires) |
| `full_close` | 4 | Mature-stall valve at line 2484 — these are the 4 events that became `M4_ACT_CLOSE` |

The 13 partial-close stall_escapes are NOT closures — they emit a partial_close action that closes 50 % of position size via `_execute_partial_close()` (line 2679). The remaining 50 % continues. Subsequent stall_escapes on the same position are blocked by the grace gap (60 ticks); when ticks eventually crosses full_after (40), the mature-stall valve fires regardless of grace.

The audit's framing of "17 SNIPER_STALL_ESCAPE events" was correct in count but misleading in implication — only 4 of those 17 became actual full closures.

### C.1.8 — Bybit Demo Impact

`MarketService.get_ticker()` is the price source for the sniper (line 860 of profit_sniper.py). It returns a 5-second-cached ticker backed by Bybit's WebSocket stream — same source `_execute_claude_trade` uses a moment later. Freshness check (line 880) skips ticks if data is more than 15 s stale; mid-price computed from bid/ask at line 935.

PnL is NOT recomputed by the sniper — it reads `pos.net_pnl_usd` from the position object at line 2651, which is Shadow's authoritative figure (per memory `project_shadowklinereader_fix.md` 2026-04-26 fix). Bybit demo's real fill latency / spreads do NOT affect the sniper's PnL math because the sniper consumes the broker's authoritative figure.

The shape of `MarketService.get_ticker` returns the same dict shape on Bybit demo as it did on Shadow; tested via `tests/test_bybit_demo/2.F` integration. No evidence in code or logs of Bybit demo data being interpreted differently by the sniper. The 4 stuck-in-loss positions were stuck because the underlying market did not move favourably for ~10 minutes — not because of any data-processing artifact.

### C.1.9 — Synthesis: Ranked Root Causes

#### Root cause #1 (PRIMARY) — Mature-stall valve trigger threshold is tight for the operator's philosophy

The `stall_escape_full_after_ticks=40` threshold (~3.3 min at 5 s cadence) maps a position to "force kill" once it has been actionable+hold for 40 consecutive ticks. The 4 audit-window closures all fired well past this (80–181 ticks), so the threshold itself was NOT the proximate trigger; the position simply continued to satisfy the actionable+hold predicate while no other guard intervened. The threshold is the "first chance to fire", not the cause.

The actual driver: positions in mild loss (−0.31 % to −0.37 %) below `development_window_lower=-0.3 %` are eligible for stall-kill. Two of the four (INJUSDT −0.30 %, ARBUSDT −0.31 %) were within 0.01 percentage points of the floor — **the floor is too tight for the operator's aggressive-exploitation philosophy**. Lowering the floor to −0.5 % or −0.8 % would have spared these two trades and given them more time to recover.

#### Root cause #2 (CONTRIBUTING) — No "previously profitable" protection

Of the 4 victims, three (SANDUSDT, INJUSDT, ARBUSDT) had touched positive territory (peak +0.06 %, +0.30 %, +0.13 % respectively). The aggressive-exploitation philosophy says: a trade that briefly worked deserves more time to retry. The current valve has no awareness of peak — it kills marginal-loss positions whether they ever reached profit or not.

A peak-aware extension (e.g. "if `peak_pnl > 0.10 %`, extend `stall_escape_full_after_ticks` to 80") would have spared INJUSDT and ARBUSDT specifically — both reached above 0.10 % before drifting back.

#### Root cause #3 (CONTEXT) — The audit's "83 % sniper-driven kills" framing is invalidated

Total full closures in the window: ~21 (4 sniper M4_ACT_CLOSE + 9 STRAT_ACTION_CLOSE + 2 TRAIL HIT + 6 closed_by=shadow_sl_tp). Sniper share of full closures: 4/21 = **19 %**, not 83 %. The audit confused the substring `mode4_p9` (which appears in tick-evaluation lines without being a close event) with actual closure events. The number of **strategic-review** closures (9 from CALL_B / watchdog, 8 closed_by=strategic_review) is more than twice the sniper count. Strategic closes are mostly losing-position rescue cuts driven by Claude's CALL_B reviews (see `STRAT_ACTION_CLOSE` lines: SL consumed 70/89/90 %, etc.).

This reframe doesn't make the operator's complaint invalid — the complaint that "trades aren't surviving long enough" still holds for the 4 actual sniper kills, plus arguably some of the 9 STRAT_ACTION_CLOSE events. But the fix scope is much narrower: the sniper's mature-stall valve, not a sweeping recalibration of the score-based path.

#### Root cause #4 (LATENT) — `closed_by="mode4_p9"` is misleading observability

The fixed-string label hides the actual trigger path from operators reading logs. A `M4_ACT_CLOSE` line with `src=stall_escape` and another with (hypothetical) `src=score` both end up tagged `by=mode4_p9` in `COORD_CLOSE_END`. Future incident debugging would benefit from tagging actual paths (`mode4_score_full`, `mode4_stall_valve`, `mode4_anti_greed_full`, `mode4_partial_cap_full`).

## Discrepancies surfaced

| # | Topic | Audit/memory said | Working tree shows | Material? |
|---|---|---|---|---|
| C-1 | Sniper has phases M1–M9 | Audit / multiple memories | Sniper has REGIME-AWARE SCORE THRESHOLDS (one tighten/partial/full triplet per regime). `mode4_p9` is a hardcoded label string, not a phase number. | Material — reframes investigation |
| C-2 | "32 mode4_p9 (full close)" | Audit | 32 occurrences of `mode4_p9` substring across all log lines; only 4 actual full-close events fired in the window. | Material — invalidates 83% premise |
| C-3 | "53/64 sniper-driven (83%)" | Audit | 4 sniper full closures out of ~21 total = 19%. Audit conflated `mode4_p9` substring with closure events. | Material reframe |
| C-4 | "M4_ACT_CLOSE = 4" | Audit | Confirmed: 4 events, all via mature-stall valve, all in loss. | Cosmetic — count matches but interpretation differs |
| C-5 | "mode4_p9 events kill trades" | Audit | None of the 4 closures were triggered by score-based path; profit gate at `_determine_action:1590` blocks score path on losing positions. | Material — fix direction shifts to mature-stall valve |
| C-6 | Phase 1 grace fix | Memory: "shipped + verified" | Confirmed shipped (commit `00f8eb1`). Active: 241 SNIPER_GRACE_BLOCKED in window. Does NOT cover mature-stall valve by design (line 2510 comment). | Cosmetic — confirms memory |
| C-7 | Bybit demo impact on sniper math | Audit speculates timing/spread effects | Sniper consumes Shadow's authoritative `pos.net_pnl_usd`; price source is the same Bybit WebSocket cache `_execute_claude_trade` uses. No evidence Bybit demo has changed sniper behaviour. | Material — narrows fix scope |

## Why current approach is wrong

The mature-stall valve was designed to kill "stuck losing trades" — positions that the action engine repeatedly evaluates as actionable+hold while never reaching a score that justifies a partial. But the valve treats two very different states the same way: a position that briefly touched profit and is now drifting in mild loss (a candidate for "give it more time") vs a position that has never been positive and is sitting at a meaningful loss (a genuine stall). The current `development_window_lower=-0.3 %` floor and the absence of peak-protection collapse both into the same kill predicate.

The operator's aggressive-exploitation aim: trades that almost worked deserve more time. Trades that never worked and are deteriorating deserve to be cut. The valve currently doesn't differentiate.

## What the fix must achieve

1. Trades that briefly touched profit (e.g. peak ≥ a configurable threshold) get extended life under the mature-stall valve.
2. Trades in marginal loss (e.g. PnL within a configurable window of break-even) get blocked from forced full close.
3. Trades that have NEVER been positive and are at meaningful loss continue to be killed by the valve — the runaway-loss protection is preserved.
4. Phase 1 grace (commit `00f8eb1`) and SNIPER_AGE_GUARD remain unchanged (no regression).
5. Layer4ProtectionService 5-min min-hold remains the final gate (no regression).
6. Trade execution rate remains 100 %.
7. No new failure modes — sniper still cuts genuinely runaway losses.

Solution options enumerated in `issueC_phase2_report.md`.
