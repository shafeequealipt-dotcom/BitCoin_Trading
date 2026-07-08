# Target 6 — Direction-Specific Performance Audit

Audit date: 2026-05-16. Audit only — no code changed.

## Why It Matters

CALL_B is the hold-vs-close decision path (`src/brain/strategist.py:783-844` `create_position_plan` → `src/brain/strategist.py:3150-3390` `_build_position_prompt`). Today the prompt shows per-position state (entry, mark, PnL, SL/TP, regime, age, FLIPPED notice) and a contract block, but no aggregate view of how the directions themselves are performing. The DB shows direction asymmetry is real and persistent (14-day split below — 2026-05-12 had 80 Sell trades to 4 Buy trades, Sell at 69% WR vs Buy at 25%). When the brain decides whether to close a losing short or hold it through a wobble, knowing "today shorts won 13/33 for -$122 net" is a factual prior on the regime, not a directive to avoid shorts. The trade-off documented at `src/brain/strategist.py:2344-2353` is that the same data fed prescriptively into CALL_A taught Claude to avoid the currently-losing direction (recency bias, not edge). The question for Target 6: does a fact-framed line in CALL_B only — never CALL_A — clear that bar.

## Files Involved

- `src/brain/strategist.py` (3849 lines) — hosts `_build_direction_performance` at line 3490 and both live prompt builders (`_build_trade_prompt:2235`, `_build_position_prompt:3150`).
- `src/core/thesis_manager.py` (314 lines) — `get_open_theses`, `close_thesis`, `get_recent_lessons`, `reconcile_with_shadow`. No `get_aggregated_stats` method exists (NOT FOUND — verified end-to-end).
- `src/core/trade_recorder.py` (117 lines) — module-level `recent_loss_symbols` (lines 24-52) and `record_strategy_trade` (lines 55-117). No direction-WR helper.
- `src/database/repositories/trading_repo.py` (239 lines) — `save_trade` (188-216), `get_trade_history` (218-238). No direction-GROUP-BY query.
- `src/strategies/performance_enforcer.py` — already aggregates per-direction in-memory: `_per_direction = {"Buy": {"wins": 0, "losses": 0}, "Sell": {"wins": 0, "losses": 0}}` (lines 65, 610-617, 662, 741-745, 808). Surfaced via `get_status()` consumed by the Telegram bot at `src/telegram/bot.py:420`.
- `src/config/settings.py:443-515` — `BrainSettings` class, existing flag naming (`enabled`, `use_packages`, `surface_briefing_fields`).
- `data/trading.db` — `trade_history` columns confirmed via `.schema`: `side`, `pnl`, `pnl_pct`, `exit_time`, `exchange_mode`.

## Existing `_build_direction_performance` Method

- Signature: `def _build_direction_performance(self) -> str` (`src/brain/strategist.py:3490`). Synchronous, no args.
- Data source: `self.services.get("trade_coordinator")._closed_trades` (`src/brain/strategist.py:3496-3500`) — in-memory ring on `TradeCoordinator`, NOT the `trade_history` table.
- Window: last 20 trades — `recent = closed[-20:]` (`src/brain/strategist.py:3505`). Not time-bounded; a quiet 8h leaves "last 20" spanning >24h.
- Output format: header `## DIRECTION PERFORMANCE (last 20 trades — read carefully)` (`src/brain/strategist.py:3513-3515`) plus:
  - `  BUY/LONG: {wins}W/{losses}L (WR={wr:.0%}) PnL=${pnl:+.2f}` (`src/brain/strategist.py:3525-3528`); same shape for SELL/SHORT (`src/brain/strategist.py:3543-3553`).
  - When ≥5 trades and WR<40%: prescriptive `WARNING: BUY DIRECTION FAILING: ... lean SHORT this cycle. Reduce BUY size by 50%.` (`src/brain/strategist.py:3529-3533, 3547-3551`) — the recency-bias text commit `3d49f69` called out.
  - `RECOMMENDATION: BUY is outperforming SELL. Favor LONG setups.` when WR gap >15pp with ≥3 trades on each side (`src/brain/strategist.py:3562-3568`).
- Observability: one `log.debug` `STRAT_DIR_PERF | buy_n=... sell_n=... warnings=...` (`src/brain/strategist.py:3572-3575`).
- Returns "" when trade_coordinator missing or `_closed_trades` empty (`src/brain/strategist.py:3496-3502, 3510-3511`).

## Callers Today (And Why None)

Grep across `src/` for `_build_direction_performance(` yields exactly three hits, all inside `src/brain/strategist.py`: the call site at line 907, a deletion-sentinel comment at line 2351, and the definition at line 3490.

The only call site (line 907) lives inside `_build_context_prompt` (`src/brain/strategist.py:846-1524`). That method is itself invoked only from `create_strategic_plan` (`src/brain/strategist.py:538-591`, call at line 544). Grep across `src/` for `create_strategic_plan` returns ZERO external callers. The live cycle entries are `create_trade_plan` (`src/brain/strategist.py:622`, called by `src/core/layer_manager.py:757`, CALL_A) and `create_position_plan` (`src/brain/strategist.py:783`, called by `src/core/layer_manager.py:899`, CALL_B). Both bypass `_build_context_prompt` entirely. Net: `_build_direction_performance` is reachable only from dead code.

Removal commit `3d49f69` (`fix(strategist): strip Direction Performance section from prompt`, 2026-05-05 14:16:36 +0000) documents the reasoning verbatim: "recent win/loss split by direction ... was training Claude to avoid whichever direction was recently losing. Two losing shorts in a row don't make the next short a worse setup; that's recency bias, not edge." The deletion sentinel at `src/brain/strategist.py:2344-2353` records the same reasoning and notes the method "stays defined (no callers outside this site after the deletion); OBS-4 garbage-collection pass will retire it." The prior agent's claim is correct: ZERO live callers, dead by deliberate design.

## Underlying Data Availability

Confirmed via direct query against `data/trading.db` on 2026-05-16.

Last 24h:

```
Buy   18  9W   avg pnl_pct=+0.0780%  net=+$34.20
Sell  43  23W  avg pnl_pct=+0.0602%  net=+$86.78
```

Combined 24h WR: Buy 50%, Sell 53%. Symmetric today.

Last 14 days, per-day per-direction:

```
2026-05-16  Buy 1/1    +$4.85      Sell 2/2     +$11.70
2026-05-15  Buy 8/17   +$29.35     Sell 21/41   +$75.07
2026-05-14  Buy 16/30  -$11.68     Sell 13/33   -$122.70
2026-05-13  Buy 4/12   +$107.28    Sell 10/29   -$53.89
2026-05-12  Buy 1/4    -$7.55      Sell 55/80   +$338.85
2026-05-11  Buy 5/9    -$12.21     Sell 51/110  +$45.70
2026-05-10  Buy 5/8    +$30.81     Sell 31/75   -$91.10
```

Real day-to-day asymmetry: 2026-05-12 Sell 69% WR vs Buy 25% WR; 2026-05-13 Buy 33% WR +$107 net vs Sell 34% WR -$53 net. Data is indexed (`idx_trade_history_symbol`), queryable in O(1) per cycle with one `GROUP BY side` against a one-day partition.

Three viable data sources:

- `trade_history` (`src/database/repositories/trading_repo.py:188-216`) — durable, restart-resilient. ~1 ms per CALL_B.
- `trade_coordinator._closed_trades` (what dead `_build_direction_performance` uses, `src/brain/strategist.py:3500`) — in-memory ring, lost on restart, zero cost.
- `PerformanceEnforcer._per_direction` (`src/strategies/performance_enforcer.py:65, 610-617`) — already populated from `trade_thesis` rows (`actual_pnl_pct`), resets daily at `_check_day_reset` (lines 652-665), surfaced via `get_status()["per_direction"]` and already consumed at `src/telegram/bot.py:420`.

Enforcer is cheapest: already exists, already runs the GROUP BY, already resets cleanly at day-rollover. One lookup per CALL_B build, no DB hit.

## Proposed Enrichment — E5 (CALL_B Only)

Inject one factual line into `_build_position_prompt`. Insertion point: between section 3 (PnL line, `src/brain/strategist.py:3165-3171`) and section 4 (YOUR OPEN POSITIONS, `src/brain/strategist.py:3174`). Append before line 3174 so the line sits with the top-of-prompt context (regime, sentiment, PnL) rather than between the contract and the per-position rows.

Format — concise, fact-only, no normative framing:

```
## TODAY DIRECTION PERF: Longs 3W/1L (75% WR, +$34) | Shorts 23W/20L (53% WR, +$87)
```

~95-110 chars typical, well inside the 5-8K CALL_B envelope (`src/brain/strategist.py:3151`). Tight ~80-char variant if budget-pressed:

```
## TODAY DIR: L 3/4 (75% +$34) | S 23/43 (53% +$87)
```

Window — operator-decidable, default `24h`:

- `today` — `date(exit_time) = date('now')`. Stable mid-session, may be empty post-UTC-rollover.
- `24h` — `exit_time >= datetime('now','-1 day')`. **Default.** Smooths UTC-rollover, more stable denominator.
- `last_50` — last 50 trades regardless of time. Most stable but drifts on quiet weekends.

Source — `PerformanceEnforcer.get_status()["per_direction"]` (`src/strategies/performance_enforcer.py:741-808`) primary; falls back to `SELECT side, COUNT(*), SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END), ROUND(SUM(pnl),2) FROM trade_history WHERE exit_time >= datetime('now','-1 day') GROUP BY side` if enforcer unavailable.

## Recency-Bias Risk

Commit `3d49f69` + `src/brain/strategist.py:2344-2353` removed direction-perf because the brain read the recent split as a directive to avoid the losing direction. The dead `_build_direction_performance` body still contains the smoking gun: `WARNING: BUY DIRECTION FAILING: ... lean SHORT this cycle. Reduce BUY size by 50%.` (`src/brain/strategist.py:3530-3533`) and `RECOMMENDATION: BUY is outperforming SELL. Favor LONG setups.` (`src/brain/strategist.py:3566`). Those normative lines, not the numbers, were the failure mode.

In operator terms: if today's shorts have 0/3 WR by accident (sample=3), the brain over-anchors on "shorts are losing today" and skips an otherwise high-conviction short — the avoidance behaviour the 2026-05-05 commit fought.

Mitigations in E5:

- Fact-only framing — no `WARNING`, no `RECOMMENDATION`, no `lean SHORT`, no `Favor LONG`. Just counts and percentages.
- Always show absolute counts (0/3 reads differently than 0/50); format includes `wins/total` not just a percentage.
- Default window 24h smooths quiet-morning empty splits.
- Optional close-reason mix per direction as a second line when operator wants more nuance. 24h sample shows Sell-side losses dominated by `wd_dl_action` (13), `bybit_sl_hit` (10), `wd_claude_action` (8), `system_close` (7); Buy-side `wd_dl_action` (4), `system_close` (4), `bybit_sl_hit` (4), `wd_claude_action` (3). "Shorts lost to wd_claude, not stops" tells Claude the losses came from its own close decisions, not market structure rejecting the direction.
- CALL_B only. CALL_A stays untouched (sentinel at `src/brain/strategist.py:2344-2353` intact). CALL_B decides hold vs close on an already-open position, not entry-time direction selection — a different decision against which the recency-bias argument carries less weight.

## Where In CALL_A (If Operator Wants It Back)

Default: leave CALL_A as is. If the operator later wants data back factually, safest location is an OPTIONAL block above the candidate list (TRADE CANDIDATES, `src/brain/strategist.py:2390-2403`), with explicit framing: "Direction perf is OBSERVED, not PRESCRIPTIVE — match the candidate's setup quality, not yesterday's win rate." Out of scope for E5.

## Feature Flag + Reversibility

Add one boolean to `BrainSettings` (`src/config/settings.py:443-515`):

```
emit_direction_perf_in_callb: bool = False
```

Default False — deploy is a no-op. Naming follows existing pattern (`use_packages`, `surface_briefing_fields`, `signal_triggered`, `enable_full_layer_block`). Operator flips to True in `config.toml` to enable; revert by flipping back. Insertion wrapped in `if getattr(self.settings.brain, "emit_direction_perf_in_callb", False):` so prompt shape is byte-identical when off.

## Logging

One INFO emit per CALL_B build when the line is rendered, matching the `STRAT_CALL_B_CTX` style at `src/brain/strategist.py:3376-3381`:

```
DIR_PERF_COMPUTED | window=24h longs_n=N1 longs_w=W1 longs_pnl=P1 shorts_n=N2 shorts_w=W2 shorts_pnl=P2 source=enforcer | did=...
```

Plus one DEBUG line documenting which source path was hit (enforcer vs trade_history fallback).

## Verification Plan For E5 (Phase 4)

- Soak: 8+ hours of live trading with the flag True.
- Sentinel A: count of CALL_B prompts containing `## TODAY DIRECTION PERF:` rises from 0 to N (CALL_B cycles in window). Tail `STRAT_CALL_B_CTX` and the new `DIR_PERF_COMPUTED` line.
- Sentinel B: zero rendering crashes — grep brain logs for `Direction performance build failed:` (mirroring `src/brain/strategist.py:902-903` pattern).
- Brain reasoning evidence: dump CALL_B responses via `scripts/monitor_stage2_live.py` (per `project_stage2_live_monitor.md`). Count responses referencing "direction", "today's longs", "today's shorts", "shorts won less" — rise from 0. Regression sanity check: any "avoid shorts" or "lean long" reference means the framing failed.
- Trade quality: not primary for an 8h soak. Cross-reference per-day breakdown in this audit against the post-deploy window.

## Verdict

- `_build_direction_performance` exists at `src/brain/strategist.py:3490-3577` as dead code (intentional removal, commit `3d49f69`, 2026-05-05).
- Underlying data available three ways: `TradeCoordinator._closed_trades` (in-memory), `PerformanceEnforcer._per_direction` (in-memory, daily reset, already surfaced), `trade_history` (durable). Enforcer is cheapest and already aggregates correctly.
- Re-introducing in CALL_B only, fact-framed (no WARNING / no RECOMMENDATION / no "lean X"), gated behind `[brain].emit_direction_perf_in_callb=False`, with absolute counts always shown — low-risk.
- Complexity: < 1 day. Work is one field + factory entry in `src/config/settings.py:443-515` (factory ~line 2996), a new helper reading enforcer status with DB fallback, and a four-line insertion in `_build_position_prompt` around line 3173. Tests cover settings parsing, helper fallback, and the gated rendering branch.
- Do NOT rehabilitate the dead `_build_direction_performance` body — its `WARNING`/`RECOMMENDATION` lines (`src/brain/strategist.py:3530-3551, 3566-3568`) are the exact recency-bias text the 2026-05-05 commit removed. Write a fresh helper; let the OBS-4 garbage-collection pass retire the old method.
- Operator should approve framing (window default, char budget, source priority, optional close-reason secondary line) before implementation.
