# Phase 0 — Issue 2: Sniper-Loop Bug Investigation

**Date:** 2026-04-27
**Brief reference:** `IMPLEMENT_FIVE_CRITICAL_FIXES_PROFESSIONAL.md` § Issue 2, Phase 4

## A — The mechanism

`ProfitSniper` (`src/workers/profit_sniper.py`, total 3089 LOC; class definition at line 75 as `BaseWorker` subclass; tick interval 5 s) evaluates open positions and emits one of `hold | tighten | partial_close | full_close` per position per tick.

Action thresholds (regime-aware, e.g. trending: `tighten=50`, `partial=70`, `full=85`) are read at `:57-63`. The decision at `:1602-1622` collapses model scores into a single composite, then maps to an action via:

```python
if score >= thresholds["full"]:
    score_action = "full_close"
elif score >= thresholds["partial"]:
    score_action = "partial_close"
elif score >= thresholds["tighten"]:
    score_action = "tighten"
else:
    score_action = "hold"
```

The P9_CLOSE_GATE at `:1615-1622` enforces a profit floor on **full closes only**: `if final_action == "full_close" and pnl < min_profit_for_close (0.50%): downgrade to "tighten"`.

The cooldown logic at `:1654-1667` is the bug:

```python
last_type = self._last_action_type.get(symbol, "hold")        # :1657
elapsed = now - self._last_action_at.get(symbol, 0)
if final_action == "tighten" and elapsed < cfg.tighten_cooldown_seconds:
    final_action = "hold"
elif final_action == "partial_close" and last_type == "partial_close" \
        and elapsed < cfg.partial_close_cooldown_seconds:                  # :1664
    final_action = "tighten"
```

The partial-close cooldown ONLY trips when the **previous** action was also `partial_close`. After every executed action, `_last_action_type[symbol]` is updated:
- `:1720` — set to `"tighten"` after a tighten action
- `:1759` and `:1786` — set to `"partial_close"` after a partial
- `:1819` — set to `"full_close"` after a full close

So an alternating sequence `tighten → partial → tighten → partial …` defeats the cooldown:

| t (s) | score | proposed | last_type | elapsed | applied gate | executed | new last_type |
|---|---|---|---|---|---|---|---|
| 0 | 75 | partial | hold | — | none | partial | partial |
| 5 | 68 | hold | partial | 5 | none (hold) | hold | partial |
| 10 | 72 | partial | partial | 10 | < 120 s & partial→partial → downgrade | tighten | tighten |
| 15 | 75 | partial | tighten | 5 | last_type≠"partial_close", gate skipped | partial | partial |
| 20 | 72 | partial | partial | 5 | < 120 s & partial→partial → downgrade | tighten | tighten |
| 25 | 75 | partial | tighten | 5 | last_type≠"partial_close", gate skipped | partial | partial |
| ... | ... | ... | ... | ... | ... | ... | ... |
| 60 | 75 | partial | tighten | 5 | gate skipped | partial | partial |

Result: 4 partials in 60 seconds, exactly matching the INJUSDT 21:48 UTC observation.

## A.1 — INJUSDT 21:48 UTC timeline reconstruction

From `dev_notes/layer1_layer7_realtime_observation_2026-04-26.md` (live observation):
- Trade 1 INJUSDT entered, closed in 2 minutes ($0.02 loss) via 4× M4_ACT_PARTIAL → final `mode4_p9` close.
- Trade 2 INJUSDT entered, closed in 78 seconds ($0.65 loss), same alternating pattern.

Sentinel firewall summary corroborates the broader pattern: **26/31 wins from natural SL/TP exits (84%, +$115); ALL 8 strategic-review closes were losses (0%, −$22).** Strategic interventions (where Layer 4 forces the close) are net destructive.

## A.2 — PROFIT GATE not enforced on partials

The same partial path can fire when `pnl_pct ≤ 0`. Lines 1605-1610 emit `"partial_close"` whenever `score ≥ thresholds["partial"]`, regardless of PnL. The P9_CLOSE_GATE (`:1615-1622`) only catches `full_close`. So a position that has just gone red can still be partialed every 60 s, locking in losses.

## B — The dependencies

- **PositionService.close_position** (`src/trading/services/position_service.py:130-148, :259`) — invoked by sniper at `profit_sniper.py:2274`. This is a direct call to `self._client.call("place_order", ..., reduceOnly=True, ...)` against Bybit; it does NOT go through `OrderService.place_order`. So Layer 4 closes do not currently emit `ORDER_START`; they emit `POS_CLOSE_START` with a separate `link_id`.
- **PositionWatchdog** (`src/workers/position_watchdog.py`) issues 12 separate `position_service.close_position(...)` calls. Watchdog and Sniper share the same close path and share the bug surface area only insofar as they share `_last_action_type` semantics — but `_last_action_type` is per-Sniper, not shared with watchdog.
- **TIAS** (post-trade intelligence) ingests sniper close events via the trade event stream; a fixed sniper changes the distribution of TIAS inputs but no schema change.
- **Event buffer** (Claude awareness): closes of the form `mode4_p9` are surfaced to Claude in the next cycle's APEX context. After the fix, fewer such closes will appear, which is the intended outcome.

## C — The constraints

- The 5 mathematical models (`src/workers/sniper_models.py`, ~988 LOC) and their composite scoring stay untouched (Approach 4.3 is rejected as out-of-scope risky).
- The P9 anti-greed pullback backstop logic (around line 1624-1639) stays untouched.
- Existing config keys (`tighten_cooldown_seconds`, `partial_close_cooldown_seconds`) keep their semantics; new keys layer on top.
- Test harness for sniper has historically been under `tests/` — must keep its conventions for the new tests.

## D — The fix candidates

| Option | Approach | Selected? | Reason |
|---|---|---|---|
| 4.1 | Type-agnostic per-position cooldown — `(now - _last_action_at[sym]) >= min_seconds_between_actions` for all action types; `min_seconds_before_close` for full_close | YES | Root-cause fix for the 4×-partial bug |
| 4.2 | PROFIT GATE for partials — partial does not fire if `pnl_pct < min_profit_for_partial_pct` | YES | Closes the second leak: partials on losing positions |
| 4.3 | Tune model thresholds | NO | Risky; out of scope |
| 4.4 | Disable partials globally via flag | NO (rejected by user) | Defensive but doesn't fix the bug |

User decision: **4.1 + 4.2** (root-cause).

Implementation (Phase 4) is therefore three atomic commits:
1. Type-agnostic cooldown (replace `_last_action_type`-conditional gate with monotonic `_last_action_at[symbol]`-only gate).
2. PROFIT GATE on partial branch (downgrade to hold if `pnl_pct < min_profit_for_partial_pct`).
3. `M4_DECISION` and `M4_GATED` observability.

## E — The observability gap

Today emitted:
- `M4_ACT_TIGHTEN` (line 1729)
- `M4_ACT_PARTIAL` (line 1761)
- `M4_ACT_CLOSE` (line 1820)
- `M4_ACT_SKIP` (line 1807)

What's missing:
- `M4_DECISION` for **every** evaluation (including hold), with model_scores, composite, thresholds, gate_status, cooldown_status.
- `M4_GATED` whenever a proposed action is downgraded by cooldown or PROFIT GATE.

Phase 4 adds both.

## F — The verification approach

24-hour observation post-deploy:
- Track every position's M4 history. **No position receives 4× partial in 60 s; max 1 partial per 60 s per position; max 2 partials per 120 s per position.**
- For every executed partial, `pnl_pct > min_profit_for_partial_pct` at action time.
- 7-day win rate of sniper-intervened trades trends toward the 84% baseline of natural SL/TP exits.
- Average position lifetime > 78 s (the worst observed sniper-killed lifetime); positions held until either natural SL/TP or score-driven close that satisfies all gates.

Edge cases:
- Position enters then immediately drops to deep loss (–2 %): a partial should NOT fire because PROFIT GATE blocks it; trade reaches natural SL.
- Position climbs then alternates score 65/75 around `partial=70` threshold: cooldown blocks repeated partials regardless of last_type.

## G — The rollback path

Three commits, each reverts independently. Reverting commit 1 (cooldown) restores prior buggy behaviour. Reverting commit 2 (PROFIT GATE) removes the partial-loss block. Reverting commit 3 (observability) only loses logs. The fix can be peeled back incrementally if any sub-fix introduces a regression.

Operational fallback if all else fails: set `[mode4] partials_enabled = false` (Approach 4.4) — would require a small additional code path; not implemented unless explicitly requested.
