# T1-1 Phase 1 — F18 Phantom close investigation

## 1. Defect statement

The brain (Stage 2 strategist) issues `act=close` directives for symbols whose positions have already closed (Bybit SL/TP hit, watchdog force-close, sniper-driven exit). The directive flows through firewall → coordinator → layer_manager unimpeded. By the time it reaches the bybit_demo adapter, the position is gone — best case the adapter no-ops, worst case it interacts with a re-entered position of the same symbol.

Today's evidence (2026-05-11 workers.log, 11:55-14:45):

- 14:18:54 CRVUSDT close via `call_b` — passed firewall + dispatched.
- 14:32:37 SEIUSDT close via `call_a_urgent` — passed firewall + dispatched.
- 14:36:42 SEIUSDT close via `call_b` — brain's own reason text reads "Position appears already in recently closed cooldown (457s remaining). Watchdog [also flagged]" — same pattern as the prior FILUSDT 13:44 and ADAUSDT 14:02 events from the live report.

Total: 8 `SENTINEL_FIREWALL_ALLOW act=close` events in 2h50m today (≈3/hour), matching the report's "~7/hour" estimate.

## 2. Report's diagnosis vs current code

The live report (`LIVE_OBSERVATION_REPORT_2026-05-11.md` section F18 and WAVE 11 / WAVE 16) framed F18 as "the firewall should reject; add `if sym not in open_positions: return False`." **This framing is incorrect for the current firewall design.** Current firewall code (`src/sentinel/firewall.py`):

```python
# Lines 21-23 — Actions strategic review may not perform
_BLOCKED_ACTIONS = frozenset({"close", "take_profit"})

# Lines 25-28 — Sources that bypass _BLOCKED_ACTIONS
_TRUSTED_SOURCES = frozenset({"call_b", "call_a_urgent"})

# Lines 51-66 — flow
if source in _TRUSTED_SOURCES:
    log.info("SENTINEL_FIREWALL_ALLOW ...")
    return (True, "allowed: trusted source ...")
if action in _BLOCKED_ACTIONS:
    log.warning("SENTINEL_FIREWALL_BLOCK ...")
    return (False, "...")
return (True, "allowed")
```

The firewall's docstring explicitly states: "Trusted sources bypass the firewall. 'call_b' (position review cycle) and 'call_a_urgent' (urgent watchdog-driven position actions from Call A) are both Claude-decided — they review positions with full context (PnL, regime, SL consumed, thesis validity) and their close decisions are respected."

All 8 phantom-close events today flowed through `call_b` or `call_a_urgent`. The firewall is doing what it was designed to do. The fix the report suggests (simple `if sym not in open_positions: return False`) would override the trusted-source bypass — that is one valid option, but it does not address the upstream issue: how the brain came to ask for the close in the first place.

## 3. Root cause — upstream

### 3.1 The UrgentQueue accumulates concerns; it is not cleared on close

`src/core/urgent_queue.py` (full file read):

- `add_concern(concern)` — line 52. Adds concern with per-symbol `COOLDOWN_SECONDS=150` (suppress re-add for 150s). Replaces existing concern for same symbol; latest wins.
- `drain_concerns()` — line 79. Atomic return + clear. Filters stale (older than `MAX_AGE_SECONDS=600`).
- There is NO `clear_for_symbol` or similar method.

Watchdog adds concerns at `src/workers/position_watchdog.py:2062-2084`. Concerns are added when:
- a position has critical_loss alerts, AND
- 150s have passed since the last add for that symbol.

Strategist drains at:
- `src/brain/strategist.py:3115-3122` (CALL_A)
- `src/brain/strategist.py:3568-3576` (CALL_B)

### 3.2 The timing window

Sequence:

```
T-N seconds: position X is open, watchdog emits HIGH critical_loss for X.
T-N seconds: UrgentQueue.add_concern(X) succeeds.
T seconds:   position X closes (any path).
T seconds:   coordinator.on_trade_closed(X) — pops X from _trades.
T seconds:   close callbacks fan out (see 3.3).
T+M seconds: next CALL_A or CALL_B fires (M is up to ~150s for CALL_B).
T+M seconds: strategist drains UrgentQueue — receives concern for X (stale, but
             X is still within MAX_AGE_SECONDS=600).
T+M seconds: prompt to Claude includes "URGENT WATCHDOG ALERTS — IMMEDIATE
             ACTION REQUIRED: X PnL -X% SL consumed N%".
T+M+L:       Claude (in good faith on stale data) produces act=close for X.
             Reason text often acknowledges the contradiction: "Already
             listed in RECENTLY CLOSED with cooldown ... Watchdog alert
             appears stale."
T+M+L:       _execute_position_actions(plan, source="call_b") fires.
             Firewall allows (trusted source). Coordinator queues. Dispatch.
```

### 3.3 The close-callback fan-out registered in manager.py

`src/workers/manager.py` registers many close callbacks via `coordinator.register_close_callback`. Verified inventory:

- manager.py:593   enforcer-side callback
- manager.py:1836  `_enforcer_close_callback`
- manager.py:1860  `_fund_close_callback`
- manager.py:1878  `_perf_close_callback`
- manager.py:1891  `_registry_callback`
- manager.py:1909  `_pnl_close_callback`
- manager.py:1940  `_thesis_close_callback`
- manager.py:1993  `_data_lake_close_callback`
- manager.py:2136  `_trade_history_close_callback`
- manager.py:2193  `_positions_table_cleanup_on_close`
- manager.py:2217  `_sniper_unsubscribe_on_close`
- manager.py:2234  `_event_buffer_clear_on_close`
- manager.py:2251  `_transformer_cache_clear_on_close` (line 2244 calls `tf.invalidate_position_cache(sym)`)
- manager.py:2268  `_strategist_position_invalidate_on_close` (line 2261 calls `strat.invalidate_position(sym)`)
- manager.py:2271  (another callback — line not yet inspected)
- manager.py:2400  `_tias_close_callback`

**There is NO `urgent_queue_clear_on_close` callback.** This is the root-cause gap.

### 3.4 Coordinator's authoritative in-memory state

`coordinator._trades` is the freshest in-memory source of truth for "is this symbol an active trade right now?":

- Set: `register_trade()` at `src/core/trade_coordinator.py:298` — `self._trades[symbol] = TradeState(...)`.
- Cleared: `on_trade_closed()` at `src/core/trade_coordinator.py:687` — `state = self._trades.pop(symbol, None)`. The pop is at the TOP of `on_trade_closed`, before any callbacks fire.
- COORD_DOUBLE_CLOSE warns when the same symbol is closed twice (line 692-697) but only logs — does not block upstream issuance.

This means: by the time the strategist's next CALL_B fires (T+M), `coordinator._trades` already does NOT contain the closed symbol. The phantom-close detection can use this as the authoritative reference.

## 4. Secondary contributing factor — `RECENTLY CLOSED` section in prompt

`src/brain/strategist.py:3555-3565` includes a "RECENTLY CLOSED (wait for cooldown before re-entering)" section in the CALL_B prompt, listing symbols with active cooldowns. This is informational and is structured to discourage re-entry, not to drive close actions. The phantom-close-on-closed-symbol behaviour is NOT primarily caused by this section — it is caused by the URGENT WATCHDOG ALERTS section above it. But the "RECENTLY CLOSED" line is what the brain quotes when it acknowledges the contradiction in its own reason text.

## 5. Tertiary: watchdog snapshot vs WS staleness (F19 cross-link)

F19 (Bybit demo WS goes stale every 2-4 min) creates a 0-120s window in which exec events (fills, close) are not pushed to the system. Watchdog falls back to REST polling (`get_positions`) for reconciliation. If a position closes during the WS-stale window, the watchdog snapshot may still list the position for up to one REST poll cycle (typically 5-15s).

This adds a SECOND source of stale data: even with the UrgentQueue fixed, the watchdog could emit a fresh critical_loss for an already-closed position based on its stale snapshot. The fix for this is in T5-4/T5-5 (WS ping heartbeat), but the defense-in-depth check at firewall/coordinator/layer_manager would catch it regardless of which source produced the stale concern.

## 6. Layers where phantom-close could be blocked

In order of upstream-to-downstream:

| # | Layer | Cost to add guard | What gets blocked |
|---|-------|--------------------|---------------------|
| 0 | UrgentQueue clear-on-close | Low (1 method + 1 callback) | Prevents stale concerns from reaching the brain — eliminates the largest source. |
| 1 | Watchdog snapshot freshness | Higher (WS heartbeat, separate scope T5-4/T5-5) | Prevents watchdog itself from emitting stale concerns. |
| 2 | Strategist post-Claude check | Medium (one check before queuing) | Catches Claude's stale-data directive before it reaches the dispatch path. |
| 3 | Firewall override for trusted sources | Low (one precondition) | Trusted sources still get bypassed for non-close actions; close requires open-position. |
| 4 | Coordinator queue_strategic_action | Low (one precondition) | Same as 3 but also catches non-firewall callers. |
| 5 | Layer_manager `_execute_position_actions` | Low (one precondition) | Final catch before dispatch. |
| 6 | bybit_demo adapter close_position | Already exists (`BYBIT_DEMO_CLOSE_NO_POSITION` at adapter.py:378) | Already emits a warn — but the call already happened. |

The prompt's hard constraint says THREE layers must have the guard. Layers 3+4+5 are the minimal triad to satisfy the prompt.

## 7. Investigation conclusions

1. **The firewall is not broken — it is doing what it was designed to do.** The report's framing oversimplifies. Fix must either override the trusted-source bypass for `close` action specifically, or address upstream causes.
2. **Root cause is `UrgentQueue` carrying stale concerns past close.** No clear-on-close callback exists. This is the single highest-impact fix.
3. **Secondary cause is watchdog snapshot staleness driven by F19.** Out of scope for T1-1 directly; covered by T5-4/T5-5.
4. **`coordinator._trades` is the freshest "is open?" source.** Should be the reference for any defense-in-depth check.
5. **Defense-in-depth at firewall+coordinator+layer_manager is achievable in 3 small edits.** Each emits `PHANTOM_CLOSE_REJECTED` with structured context.

The optimal fix is BOTH: clear UrgentQueue on close (root cause) AND add the three defense-in-depth guards (catches any other stale-data source including the future watchdog snapshot path before F19 is fixed).

Phase 2 proposal follows.
