# P0-3 Anatomy — Close-Authority Path (Dependency Map)

Date: 2026-05-22. Source files read in full before this map was produced.

## H1 — Scope

Map the complete brain-close-vote → watchdog-veto → close-or-tighten path, with the composite scoring formula, all factor weights, and a worked example demonstrating the composite ceiling. Required by Rule 3.

## H1 — Brain Close Vote Path

1. **Brain emits close vote.** Layer 2 CALL_B (`src/brain/strategist.py` `create_position_plan`) produces a `StrategicPlan.position_actions: list[dict]` with action items like `{"symbol": "INJUSDT", "action": "close", "reason": "..."}`. The reason field carries the brain's natural-language reasoning text.
2. **Coordinator queues action.** The strategist's plan is consumed by the watchdog via `coordinator.drain_strategic_actions()`.
3. **Watchdog drains and dispatches.** `src/workers/position_watchdog.py:3350–3859` (`_execute_strategic_actions`). For each action with `act in ("close", "take_profit", "set_exit", "tighten_stop")`:
   - **Position re-verify** (3382–3392): if position has already closed since the brain cycle started, `POS_ACTION_SKIP` and continue.
   - **Min-hold guard** (3412–3453): for `close` and `take_profit`, blocks actions on positions younger than `strategic_action_min_hold_seconds` (default 300) unless the reason text contains an allowed phrase (e.g., "stop loss hit", "structure invalidated", "regime change"). Emits `STRAT_ACTION_CLOSE_BLOCKED`. Fail-closed: missing coordinator → `_age_sec=0` → blocked.
   - **Scoring intercept** (3463–3813): see below.
   - **Skip flag check** (3815–3816): `if _scoring_skip_close: continue`.
   - **Close execution** (3818–3821): `position_service.close_position(symbol, close_trigger="wd_claude_action")`, log `STRAT_ACTION_CLOSE`.

## H1 — Composite-Scoring Intercept

`src/workers/position_watchdog.py:3463–3813`. Activated when `act in ("close", "take_profit")` and `wd_brain_scoring_enabled=True`. Steps:

1. Pre-emit `WD_SCORING_PATH_REACHED` (3479–3485) for every close/take_profit so operators can correlate paths.
2. Read settings: `wd_brain_scoring_enforce` (currently True per `config.toml:531`), `wd_brain_scoring_threshold` (default 6.0).
3. Re-fetch position state (3501–3514).
4. Emit `BRAIN_CLOSE_VOTE_RECEIVED` (3516).
5. Compute factor inputs:
   - `pnl_pct` via `_calculate_pnl_pct` (3536–3540).
   - `sl_consumption` via `_calculate_sl_proximity` (3542–3548). Note: this reads `pos.stop_loss` (current, possibly trailed SL), not entry-time SL.
   - `time_remaining_s`, `age_s` via coordinator's TradePlan (3665–3687).
   - `velocity` from `TimeDecayState.prev_velocity` if available, else derived from `_brain_score_prev_pnl` cache (3689–3712).
   - `xray_match`: compare `structure_cache.get(symbol).trade_direction` to position side, with staleness guard at 60s (3714–3752).
   - `reasoning_text = reason or ""` (3761) — the brain's natural-language reasoning.
6. C1 Phase 1.4 diagnostic `WD_SL_PCT_DIVERGENCE` (3550–3657): reads thesis entry-time SL alongside current SL, reports both percentages and whether the bucket flipped due to trailing. Read-only — does not feed the composite.
7. Call `compute_brain_close_score` at `src/risk/wd_brain_scoring.py:288–415` with the inputs above and the threshold.
8. Emit `WATCHDOG_CLOSE_SCORE_COMPUTED` with all factor breakdowns (3767–3770).
9. Branch on enforce flag and recommendation:
   - `enforce=False`: log `WD_CLOSE_SCORE_LOG_ONLY`, fall through to close.
   - `enforce=True + recommendation="execute"`: log `WATCHDOG_CLOSE_EXECUTED`, fall through.
   - `enforce=True + recommendation="reject"`: log `WATCHDOG_CLOSE_REJECTED`, set `_scoring_skip_close=True`.
   - `enforce=True + recommendation="reject_and_tighten"`: log `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN`, call `_tighten_sl_breakeven_30pct(_pos_for_score)`, set `_scoring_skip_close=True`.
10. `_scoring_skip_close=True` → skip the actual `position_service.close_position` call.

## H1 — Composite-Scoring Formula

`src/risk/wd_brain_scoring.py:288–415` (`compute_brain_close_score`). Pure function, no I/O. Seven factors:

| Factor | Buckets and weights (default) |
| --- | --- |
| **pnl** | strong_winner (>1.0%) +3.0, mild_winner (0.3-1.0) +1.5, weak_winner (0-0.3) +0.5, shallow_loser (-0.5 to 0) **-3.0**, moderate_loser (-1.5 to -0.5) -1.0, deep_loser (<-1.5) +0.5 |
| **time_remaining** | deep (>20m) -2.0, moderate (10-20m) -1.0, shallow (5-10m) 0.0, imminent (<5m) +1.0 |
| **age** | infant (<3m) -2.0, young (3-10m) -1.0, mature (10-30m) 0.0, aged_losing (>30m + pnl<0) +1.0 |
| **velocity** | strong_positive (≥0.01%/s) -2.0, mild_positive (0.002-0.01) -1.0, stationary 0.0, mild_negative (-0.01 to -0.002) +1.0, strong_negative (≤-0.01) +2.0 |
| **sl_consumption** | spacious (<30%) -2.0, comfortable (30-60%) -1.0, tight (60-80%) 0.0, imminent (>80%) +1.0 |
| **xray** | supports (XRAY agrees) -2.0, neutral/stale/unavailable 0.0, broken (XRAY opposes) +2.0 |
| **reasoning** | structural (cites STRUCTURAL_KEYWORDS) +2.0, vague (non-empty, no keywords) +0.5, empty 0.0 |

`composite = sum(all seven factors)`. Recommendation:
- `composite >= 6.0` → `"execute"`
- `0 <= composite < 6.0` → `"reject"`
- `composite < 0` → `"reject_and_tighten"` (also tightens SL toward break-even by 30%)

**Max theoretical composite:** +3 + 1 + 1 + 2 + 1 + 2 + 2 = **+12.0** (strong winner with all closing signals positive).

**Max composite for a typical loser scenario (the P0-3 case):** depends on PnL bucket:
- `shallow_loser` (PnL between -0.5% and 0%): -3 + 1 + 1 + 2 + 1 + 2 + 2 = **+6.0** (exactly at threshold, narrow alignment required).
- `moderate_loser` (-1.5% to -0.5%): -1 + 1 + 1 + 2 + 1 + 2 + 2 = **+8.0** (executable).
- `deep_loser` (<-1.5%): +0.5 + 1 + 1 + 2 + 1 + 2 + 2 = **+9.5** (executable).

The alignment required to reach +6 for a shallow_loser is: imminent time (<5m), aged_losing (>30m loss), strong_negative velocity, imminent SL (>80%), broken XRAY, structural reasoning — six narrow conditions simultaneously.

## H1 — The 2026-05-22 Composite Ceiling — Worked Examples

### ICPUSDT 2026-05-22 16:50:40 — composite = 4.5 (ceiling observed)

From the log line: `pnl_pct=-1.8615 pnl_bucket=deep_loser pnl_factor=0.5 time_remaining_s=1368 time_bucket=deep time_factor=-2.0 age_s=1332 age_bucket=mature age_factor=0.0 velocity=-0.014892 velocity_bucket=strong_negative velocity_factor=2.0 sl_pct=74.6 sl_bucket=tight sl_factor=0.0 xray_bucket=broken xray_factor=2.0 reasoning_bucket=structural reasoning_factor=2.0` → composite 0.5 + (-2.0) + 0.0 + 2.0 + 0.0 + 2.0 + 2.0 = **4.5**.

This is a deep_loser with strong evidence pointing toward closing: strong_negative velocity, broken XRAY, structural reasoning. The composite still doesn't reach 6.0 because:
- `time_factor = -2.0` (1368 seconds = 22.8 minutes remaining; deep bucket >20m).
- `age_factor = 0.0` (1332 seconds = 22.2 minutes; mature, not aged_losing >30m).
- `sl_factor = 0.0` (74.6%; tight bucket 60-80%, not imminent >80%).

To reach 6.0, the position would need either (a) less time remaining (moderate or imminent), (b) older age (>30m losing), or (c) more SL consumption (>80%). At 16:50 it had 22.8 minutes left and 74.6% SL consumed.

### INJUSDT 2026-05-22 16:05:19 — composite = 2.0 (despite brain saying "85% SL, one tick from stop")

From the log line: `pnl_pct=-0.8609 pnl_bucket=moderate_loser pnl_factor=-1.0 time_bucket=deep time_factor=-2.0 age_s=1216 age_bucket=mature age_factor=0.0 velocity=-0.001892 velocity_bucket=stationary velocity_factor=0.0 sl_pct=82.7 sl_bucket=imminent sl_factor=1.0 xray_bucket=broken xray_factor=2.0 reasoning_bucket=structural reasoning_factor=2.0` → composite -1.0 + (-2.0) + 0.0 + 0.0 + 1.0 + 2.0 + 2.0 = **2.0**.

Here the brain's CRITICAL reasoning ("85% SL consumed, one tick from stop, -0.89%") is fed via reasoning_factor=+2.0, but the velocity was stationary (-0.0019 %/s), the time was deep (>20m remaining), and the position was mature (not yet >30m). Despite SL at 82.7% (imminent +1.0) and broken XRAY (+2.0), the composite is 2.0.

This case shows the structural problem most clearly: the brain has explicit structural reasoning and the SL is approaching, but the composite is FAR below threshold. The brain's explicit close vote contributes only through `reasoning_factor` (+2.0 maximum); the *fact* that the brain voted close is not a factor input.

## H1 — Min-Hold Guard Path (Out of Scope for P0-3 But Worth Mapping)

The min-hold guard at 3412–3453 fires before scoring. For close/take_profit on positions younger than 300s, the action is blocked unless reason text contains an allowed-list phrase ("stop loss hit", "structure invalidated", etc.). This is correct anti-churn protection for fresh positions and does not interact with the P0-3 root cause.

The 2026-05-22 incident-window votes were all on positions older than 300s by the time the brain emitted them (cycle-time alone is ~3min), so the min-hold guard was not the reason for the rejections.

## H1 — SL-Tightening Fallback Path

`src/workers/position_watchdog.py:1202–1255` (`_tighten_sl_breakeven_30pct`). When `recommendation="reject_and_tighten"`:

1. Compute new_sl = current_sl + 30% × (entry_price - current_sl).
2. Direction-aware: for longs, new_sl moves up toward entry; for shorts, new_sl moves down toward entry.
3. Call `_push_sl_to_shadow` with source="wd_brain_scoring" — the helper enforces tighter-only (never wider, never past entry) at lines 1005–1200.
4. Log `WD_BRAIN_SCORE_TIGHTEN` on success or `WD_BRAIN_SCORE_TIGHTEN_FAIL` on error.

This is the C1 anti-churn mechanism — when a brain panic-close is rejected, the SL is tightened toward break-even so the position is protected without loss-locking. **It is preserved by the P0-3 fix.** The fix only affects which closes are rejected vs executed; the SL-tightening fallback continues to fire on composite<0 cases as it does today.

## H1 — Dependent Files (must read before edit)

- `src/workers/position_watchdog.py` lines 3463–3813 — primary edit site (scoring intercept).
- `src/risk/wd_brain_scoring.py` lines 288–415 — primary edit site (composite formula).
- `src/risk/wd_brain_scoring.py` lines 439–497 (`compute_sl_consumption_pct`) — read, no edit.
- `src/workers/position_watchdog.py` lines 1005–1200 (`_push_sl_to_shadow`), 1202–1255 (`_tighten_sl_breakeven_30pct`) — read, no edit.
- `src/config/settings.py` — `Watchdog` dataclass; will add new fields `wd_brain_vote_weight`, `wd_hard_risk_floor_sl_pct` if the fix introduces them.
- `config.toml` — add tunables alongside existing `wd_brain_scoring_*` keys (lines 519–532).

## H1 — Log Tag Inventory (P0-3 surface)

| Tag | File:line | Today's role | After fix |
| --- | --- | --- | --- |
| `WD_SCORING_ENFORCE_ACTIVE` | watchdog.py:413 | boot sentinel | keep |
| `WD_SCORING_PATH_REACHED` | watchdog.py:3479 | every close action | keep |
| `BRAIN_CLOSE_VOTE_RECEIVED` | watchdog.py:3516 | brain vote enters | keep |
| `WATCHDOG_CLOSE_SCORE_COMPUTED` | watchdog.py:3767 | factor breakdown | extended with `brain_vote_factor` + `hard_floor_active` |
| `WD_CLOSE_SCORE_LOG_ONLY` | watchdog.py:3774 | log-only mode | keep (rarely fires now that enforce is on) |
| `WATCHDOG_CLOSE_EXECUTED` | watchdog.py:3782 | composite ≥ threshold | keep |
| `WATCHDOG_CLOSE_REJECTED` | watchdog.py:3788 | composite in reject band | keep |
| `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN` | watchdog.py:3795 | composite < 0 → SL tighten | keep |
| `WATCHDOG_HARD_FLOOR_HIT` | (new) | SL% ≥ floor → force close | NEW |
| `WD_BRAIN_SCORE_TIGHTEN_FAIL` | watchdog.py:1252 | SL-tightening errored | keep |
| `WD_SL_PCT_DIVERGENCE` | watchdog.py:3646 | C1 diagnostic | keep |
| `P0_3_SENTINEL` | (new) at watchdog __init__ | boot sentinel | NEW |
