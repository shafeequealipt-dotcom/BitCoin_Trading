# CRITICAL-1 Phase 1 — was_win Flag Analysis

## Purpose

Trace where `was_win` is consumed downstream of `coordinator.on_trade_closed`. Determine whether the CRITICAL-1 fix must explicitly flip `was_win` based on the back-derived `pnl_pct`, or whether downstream consumers compute their own win flag.

## How was_win arrives at the coordinator

`bybit_demo_websocket_subscriber.py:493`: `was_win=False` hardcoded. The dispatch comment claims back-derive: "back-derived in coordinator from state.entry_price + exit_price". This claim is currently false — the coordinator passes the value through unchanged.

System-initiated closes (via adapter) do not call coordinator directly, so was_win is never set by that path. They go through the WS path (with the same hardcoded False) when the market order fills.

## Coordinator handling of was_win

`trade_coordinator.on_trade_closed:644` accepts `was_win: bool` and uses it in two places:

1. Line 717: `record["was_win"] = was_win` — stored in the dict for callbacks.
2. Lines 786-792: per-symbol cooldown branching:
   ```python
   if was_win:
       cooldown_sec = 180  # 3 min after win
   elif closed_by in ("hard_stop", "mode4_crash"):
       cooldown_sec = 900  # 15 min after hard stop
   else:
       cooldown_sec = 600  # 10 min after normal loss
   ```

Currently `was_win=False` always → cooldowns are 600s for ordinary closes (or 900s for hard stops). After fix, real wins get 180s cooldown — the system re-enters winning symbols faster.

## Downstream consumers reading record["was_win"]

| Consumer | Site | Effect when was_win is wrong |
|---|---|---|
| `_enforcer_close_callback` → `performance_enforcer.on_trade_closed` | `workers/manager.py:1743` reads `record["was_win"]`; passes to `enforcer.on_trade_closed(pnl_pct, was_win)` (`performance_enforcer.py:801`) | Streak counter wrong; mode-transition signal wrong; `_recent_results` history skewed |
| `_fund_close_callback` | `workers/manager.py:1764` `was_win=record["was_win"]` passed to fund_manager | Capital-pool decisions skewed |
| `_perf_close_callback` (registry) | `workers/manager.py:1800` `registry.update_performance(name, pnl_pct, was_win)` | Per-strategy win-rate tracking wrong |
| `_pnl_close_callback` | `workers/manager.py:1983` `outcome="WIN" if record["was_win"] else "LOSS"` | Telegram /pnl command shows wrong outcomes |
| `_tias_close_callback` | `tias/collector.py:149` `"win": bool(record.get("was_win", False))` | trade_intelligence.win column always 0 → DeepSeek learns from no-win dataset |
| `_learning_repo_close_callback` (workers/manager.py:2324) | `was_win = record.get("was_win", False)` then `learning_repo.update_strategy_stats` | Strategy-symbol learning stats permanently wrong (currently shows 100% loss) |

Six downstream consumers depend on `was_win`. All currently see False for every bybit_demo close. The cumulative effect:

- Performance Enforcer never increments win streak → cannot promote modes
- Strategy registry shows every strategy at 0% win rate
- learning_repo permanently records every trade as loss; future Claude prompts include "this strategy has 0 wins"
- TIAS / DeepSeek learns from a "you never win" dataset
- Telegram /pnl shows everything as LOSS
- Cooldowns are always set to loss-grade (600s instead of 180s for wins) — slower recovery between trades

## Where the flip must happen

`was_win = (pnl_pct > 0)` after the back-derive of pnl_pct.

The flip MUST happen in the coordinator (not the subscriber) because:

1. The coordinator owns the back-derived `pnl_pct`. The subscriber doesn't see the back-derive output.
2. The 6 downstream consumers all read from the record dict (built at line 715). The coordinator is the only place that can set the dict's `was_win` correctly.
3. The cooldown logic at line 787 reads the function parameter `was_win`, not `record["was_win"]`. Updating the parameter (not just the dict field) is required to fix the cooldown.

Both the parameter `was_win` and the dict field `record["was_win"]` must reflect the back-derived value.

## Edge cases

| Case | was_win value |
|---|---|
| Normal win (pnl_pct > 0) | True |
| Normal loss (pnl_pct < 0) | False |
| Flat trade (pnl_pct == 0; entry == exit) | False (treated as non-win; matches existing semantics) |
| Back-derive skipped (entry or exit zero) | False (input value preserved; matches current behavior) |

The "flat = not a win" choice matches existing code: `performance_enforcer.py:804` `if was_win: ...` and `else: ...` has no separate flat branch. Treating flat as "not a win" keeps the current semantics intact.

## Existing system-initiated path (sanity check)

The system-initiated path through `bybit_demo_adapter.close_position` does not invoke `coordinator.on_trade_closed` directly. The corresponding WS execution event flows through the same buggy path with hardcoded `was_win=False`. So the existing `_enforcer_close_callback` AND `_perf_close_callback` AND `_pnl_close_callback` ALL see was_win=False even for system-initiated closes that produced real wins.

This means CRITICAL-1's was_win fix simultaneously:
- Restores Performance Enforcer mode signals
- Restores per-strategy win-rate tracking
- Restores learning_repo win counters
- Restores Telegram /pnl outcome labels
- Restores TIAS win flag for DeepSeek / APEX learning
- Restores faster cooldowns on wins

Six downstream restorations from one fix.

## Findings

1. `was_win` has six downstream consumers, all of which currently see False for every bybit_demo close.
2. The fix must update both the coordinator's local `was_win` parameter AND the record dict's `was_win` field. Updating the parameter alone fixes cooldown; updating the dict field alone fixes the six callbacks. Both updates are needed.
3. The fix is simple: `was_win = pnl_pct > 0` immediately after the pnl_pct back-derive.
4. No downstream consumer recomputes its own win flag — they all trust the coordinator's signal.
5. The cumulative restoration includes Performance Enforcer mode behavior — operator should expect mode transitions to start happening again after CRITICAL-1 ships (Risk 8 in the prompt's Risk Register).
