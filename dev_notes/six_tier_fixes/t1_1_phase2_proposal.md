# T1-1 Phase 2 — F18 Phantom close proposal

## 1. What the report said vs what current code shows

The report's recommended fix (one-line `if sym not in open_positions: return False` at the firewall) is based on a misreading of the firewall's current design.

Today's firewall (`src/sentinel/firewall.py:23-66`) is a **two-track** design:

- Track 1 (untrusted sources): `_BLOCKED_ACTIONS = {"close", "take_profit"}` are rejected.
- Track 2 (trusted sources `call_b`/`call_a_urgent`): bypass `_BLOCKED_ACTIONS` entirely; Claude's position-review judgement is respected.

Today's 8 phantom-close events all come from track 2 (`call_b` and `call_a_urgent`). The firewall is doing what it was designed to do. Simply rejecting `close` on closed symbol at the firewall overrides the trusted-source bypass; that is one valid defense layer but does not address the upstream cause.

## 2. Confirmed root cause

The brain prompts contain "URGENT WATCHDOG ALERTS" that include symbols whose positions have already closed.

Source: `UrgentQueue` (`src/core/urgent_queue.py`) accumulates watchdog concerns and has NO method to clear a symbol's concerns on position close. `coordinator.on_trade_closed` pops the symbol from `_trades` and fans out 14+ close callbacks (see Phase 1 inventory) but none of them touches the UrgentQueue. Concerns remain queued for up to `MAX_AGE_SECONDS=600`, drained into the next CALL_A or CALL_B (which runs every ~150s). Claude (reasonably) issues close directives for the stale concerns; the directive flows through the trusted-source firewall path; coordinator queues; layer_manager dispatches.

Secondary contributor: F19 WS staleness can produce watchdog snapshots that show a closed position as still open for 0-120s. Out of scope for T1-1 directly (covered by T5-4/T5-5).

`coordinator._trades` is the freshest authoritative source of "is this symbol open right now?" — the `pop()` happens synchronously at the top of `on_trade_closed`.

## 3. Three solution options

### Option A — Root cause only

Add `UrgentQueue.clear_for_symbol(symbol: str) -> int` method that removes all queued concerns for a symbol. Register a close-callback in `manager.py` that calls it on every `on_trade_closed`.

- Edits: 1 method (urgent_queue.py), 1 callback (manager.py), 1-2 smoke tests.
- LOC: ~25 added.
- Pros:
  - Addresses the root cause directly — phantom concerns never reach the brain.
  - Single source of fix; minimal blast radius.
- Cons:
  - Does NOT meet the prompt's hard constraint "three layers must have the guard".
  - Does NOT catch other stale-data sources (watchdog snapshot during WS-stale window, future regressions, manual operator actions that bypass UrgentQueue).
  - One bug in the close-callback registration silently re-opens the failure mode.

### Option B — Defense-in-depth only

Add an open-positions precondition at THREE layers, each emitting `PHANTOM_CLOSE_REJECTED` with structured context, while leaving the UrgentQueue untouched:

1. **Firewall** (`src/sentinel/firewall.py`): for `action in _BLOCKED_ACTIONS` (close, take_profit), check `symbol in coordinator._trades` even when source is trusted. If not in `_trades`, reject with `PHANTOM_CLOSE_REJECTED`. Trusted sources still bypass for non-blocked actions (tighten_stop, set_exit, hold).
2. **Coordinator** (`src/core/trade_coordinator.py:queue_strategic_action`): for `action in {"close","take_profit"}`, check `symbol in self._trades`. If not, log `PHANTOM_CLOSE_REJECTED` and return without queuing.
3. **Layer_manager** (`src/core/layer_manager.py:_execute_position_actions`): same check before queuing — catches future callers that bypass the firewall.

- Edits: 3 small precondition blocks. 1-2 smoke tests per layer (or 3 tests total).
- LOC: ~30 added.
- Pros:
  - Meets the prompt's hard constraint.
  - Catches ALL stale-data sources, not just UrgentQueue. Covers the F19 watchdog-snapshot path too.
  - Each layer's log line is independently auditable.
- Cons:
  - Doesn't fix the underlying issue — Claude still gets phantom alerts and wastes CALL_B cycles producing close directives that get rejected.
  - The firewall change subtly tightens the "trusted source" contract — `call_b` no longer fully bypasses for close on closed symbols. Needs operator sign-off as a contract change.

### Option C — Combined (Recommended)

Both A and B together.

- Root cause: UrgentQueue clear-on-close eliminates the dominant source of phantom directives. Saves CALL_B cycles spent on stale data.
- Defense-in-depth: 3-layer guard catches any other path (watchdog-snapshot lag, future regressions, manual actions).

- Edits: combined A + B. Atomic commits split per concern:
  - Commit 1: `feat(t1-1/phase3a)`: UrgentQueue.clear_for_symbol + close-callback registration.
  - Commit 2: `feat(t1-1/phase3b)`: firewall trusted-source close precondition.
  - Commit 3: `feat(t1-1/phase3c)`: coordinator queue_strategic_action precondition.
  - Commit 4: `feat(t1-1/phase3d)`: layer_manager dispatch precondition.
  - Commit 5: `test(t1-1/phase3e)`: smoke tests across all 4 layers.
- LOC: ~55 added across all commits.
- Pros:
  - Root cause fixed.
  - Three-layer defense per prompt constraint.
  - Cleanest log story: `URGENT_QUEUE_CLEAR` on every close, plus `PHANTOM_CLOSE_REJECTED` for any leak that makes it past the source-clearing.
- Cons:
  - More edits than A or B alone.
  - Risk of duplicate-checking adds slight CPU; negligible since checks are O(1) dict membership.

## 4. Recommendation

**Option C.** Reasons:

1. The prompt's hard constraint ("Three layers must have the guard") is satisfied.
2. Root-cause fix (UrgentQueue clear) prevents stale concerns from EVER reaching the brain — saves Claude calls and stops the noise in `STRAT_POS_ACT` audit logs.
3. Defense-in-depth covers other stale-data sources we haven't audited yet (watchdog snapshot during F19 WS-stale window — a future T5-4 fix benefits the most from this).
4. The contract change at the firewall (trusted sources no longer bypass for close-on-closed-symbol) is conservative — it only adds rejection in a specific case Claude shouldn't be deciding anyway.
5. Per-layer atomic commits keep each change independently revertable.

## 5. Aim preservation

Option C does not bias the system toward conservatism. All non-close actions from trusted sources still flow through unchanged. The change only rejects close on already-closed positions — these are no-ops at the exchange anyway. Trade frequency, win/loss decision-making, and aggressive-exploitation philosophy are unaffected.

## 6. Hard constraints satisfied

- Three layers have the guard.
- Each emits structured `PHANTOM_CLOSE_REJECTED` with symbol, action, brain decision_id, source.
- Existing legitimate actions still flow.
- Shadow mode unaffected (the changes are in firewall/coordinator/layer_manager, all of which are mode-agnostic).

## 7. Observability additions

New log tags:
- `URGENT_QUEUE_CLEAR sym=X cleared=N` — at INFO when at least one concern is cleared on close.
- `PHANTOM_CLOSE_REJECTED layer=<firewall|coordinator|layer_manager> sym=X act=close src=<source> reason=symbol_not_in_active_trades did=...` — at WARN.

## 8. Test plan (smoke, ≤10 min)

1. `tests/test_t1_1_phantom_close_guard.py` (single file, 3 small tests):
   - UrgentQueue.clear_for_symbol removes concerns and is idempotent.
   - Firewall rejects close on symbol not in `coordinator._trades` for both `call_b` and `call_a_urgent`.
   - Coordinator.queue_strategic_action drops close-on-closed symbol with PHANTOM_CLOSE_REJECTED log.

Each `timeout 20 python3 -m pytest tests/test_t1_1_phantom_close_guard.py` wrap.

## 9. Operator decision required

Please choose one of:

- **C (recommended)**: full combined fix, 4-5 commits.
- **A only**: root cause only, 1-2 commits.
- **B only**: defense-in-depth only, 3-4 commits.
- **Other** (with description).

Then state any threshold/policy changes you want, e.g. whether the firewall log line should be WARN or ERROR.

When you reply, I proceed to Phase 3 implementation.
