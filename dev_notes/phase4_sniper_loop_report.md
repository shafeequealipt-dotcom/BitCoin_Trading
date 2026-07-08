# Phase 4 — Sniper-Loop Bug Report

**Date:** 2026-04-27
**Commit:** `50f89cd`
**Status:** Implementation complete (single bundled commit).

## Root cause and fix

Two distinct bugs in `src/workers/profit_sniper.py`:

1. **Cooldown defeated by alternating actions** (line 1664). The legacy gate only blocked the next partial when `last_type == "partial_close"`. An alternating tighten ↔ partial sequence reset `last_type` to `tighten` between partials, defeating the cooldown — INJUSDT 21:48 reproduction fired 4× partials in 60 s and closed at a loss.
2. **No PROFIT GATE on partials** (lines 1615-1622). The P9_CLOSE_GATE only enforced 0.50 % profit on full_close. Partials had zero profit floor, so a position that had just gone red could keep firing partials, locking in losses.

User chose 4.1 + 4.2 (root-cause).

Three sub-fixes landed in one commit:

1. **Type-agnostic per-position cooldown** — gate uses only the per-position monotonic `_last_action_time`. Partial requires `elapsed >= max(min_seconds_between_actions, partial_close_cooldown_seconds)` (default 60 s). Full_close from the score branch requires `elapsed >= max(min_seconds_before_close, partial_close_cooldown_seconds)` (default 180 s). Anti-greed pullback backstop bypasses by design.
2. **PROFIT GATE on partials** — before the cooldown check, if `score_action == "partial_close" AND current_pnl < min_profit_for_partial_pct`, downgrade to `hold` and emit `M4_GATED reason=profit_gate`. Default `0.0` (require break-even).
3. **`M4_DECISION` and `M4_GATED` observability** — `M4_DECISION` fires on every evaluation with score, thresholds, gate verdict, cooldown elapsed, regime; `M4_GATED` fires whenever a proposed action is downgraded by cooldown or profit gate.

## Files modified

- `src/config/settings.py` + `config.toml` `[mode4]` — `min_seconds_between_actions` (60), `min_seconds_before_close` (180), `min_profit_for_partial_pct` (0.0)
- `src/workers/profit_sniper.py` — replaced cooldown gate (lines ~1654-1667), inserted PROFIT GATE between P9_CLOSE_GATE and anti-greed (around line 1623), added `M4_DECISION` and `M4_GATED` log emissions

## Operator runbook

| Trial | Procedure | Pass criterion |
|---|---|---|
| 4.1 | 24 h tracking of open positions | No position receives 4× partial in 60 s; max 1 partial / 60 s |
| 4.2 | All M4 actions, 24 h | Every partial has pnl > min_profit_for_partial_pct at action time |
| 4.3 | 7-day win rate vs baseline | Sniper-intervened win rate trends toward natural 84 % SL/TP baseline |
| 4.4 | Position lifetime distribution | Average lifetime > 78 s; no closes < 60 s without anti-greed bypass |

## Rollback

`git revert 50f89cd` restores legacy partial-cooldown logic. Risk: reverts both fixes simultaneously. If only one needs reverting, the diff is straightforward (cooldown block at one site, PROFIT GATE at another).
