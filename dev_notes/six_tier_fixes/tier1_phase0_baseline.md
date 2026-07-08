# Tier 1 Phase 0 — Tier-specific pre-flight

## 1. Purpose

Refresh references for Tier 1's four issues (T1-1 F18, T1-2 F8, T1-3 F9, T1-4 F4) immediately before Tier 1 work begins. Re-confirm against current code.

## 2. References reconfirmed

| Issue | File:line | Status |
|-------|-----------|--------|
| T1-1 F18 firewall | `src/sentinel/firewall.py:23,28,31,52,58` | Verified. `_BLOCKED_ACTIONS = {"close","take_profit"}` AND `_TRUSTED_SOURCES = {"call_b","call_a_urgent"}` both present. The firewall intentionally bypasses _BLOCKED_ACTIONS for trusted sources (this is by design — not the bug). |
| T1-1 F18 coordinator queue | `src/core/trade_coordinator.py:189-199` | Verified. `queue_strategic_action` exists. NO open-positions precondition today. |
| T1-1 F18 dispatch | `src/core/layer_manager.py:1110-1156` (`_execute_position_actions`) | Verified. Sources today: `call_b` (line 922) and `call_a_urgent` (line 855). |
| T1-1 F18 UrgentQueue | `src/core/urgent_queue.py` (full file, 182 lines) | NEW finding — no `clear_for_symbol` method exists. See T1-1 investigation. |
| T1-2 F8 watchdog | `src/workers/position_watchdog.py:2858` | Verified. `_max_step = ... 0.5 default`. Config override below. |
| T1-2 F8 gateway | `src/core/sl_gateway.py:103,552,570` | Verified. REASON_STEP_EXCEEDED constant present. |
| T1-2 F8 config | `config.toml:533` | `max_step_pct = 0.25` confirms 0.25 cap. Watchdog default of 0.5 is overridden. |
| T1-3 F9 sentinel | `src/brain/strategist.py:3540-3584` | Verified. `_tias_lessons_removed = True` flag and hard-coded `recency_lessons_count=0`. |
| T1-3 F9 TIAS | `src/tias/analyzer.py:192-202`, `src/tias/prompts.py:19-43`, `src/core/thesis_manager.py:255` | Verified. THESIS_CLOSE emission at thesis_manager:255. |
| T1-4 F4 cleanup | `src/workers/cleanup_worker.py:205` | Verified. VACUUM with 3-retry loop. |
| T1-4 F4 boot | `src/workers/manager.py:1075` | Verified. Boot-time VACUUM. |
| T1-4 F4 connection | `src/database/connection.py:122,133-136,219` | Verified. WAL on, contention pragmas tuned, DB_LOCK_WAIT emission. |

## 3. New findings vs report

- **T1-1 F18 firewall**: the report assumes the firewall should reject phantom closes. Current firewall design INTENTIONALLY trusts `call_b` / `call_a_urgent` sources. The actual root cause is upstream — see T1-1 investigation. This significantly changes the fix scope.
- **T1-2 F8**: rejected step_pct values today (1.0-1.25x cap, i.e. 4-5x over the 0.25 cap) are LARGER than reported (2.04-2.27x). Issue is worsening.
- **T1-4 F4**: zero `DB_LOCK_WAIT` events in the current 2h50m workers.log window. VACUUM cascade may have shifted timing. Phase 1 of T1-4 must look at brain.log and general.log for cascade evidence, not workers.log.

## 4. Tier 1 ready to begin

T1-1 Phase 1 investigation is next. No code changes in Phase 0.
