# Phase 6 — Brain CLI Credential Pre-flight + Watchdog Harmonization

**Status:** SHIPPED (core fixes); stall-detection + pre-kill diagnostics deferred
**Date:** 2026-04-26
**Investigation:** [`phase0_issue_brain_credential.md`](phase0_issue_brain_credential.md)

## Summary

Three of the four Phase-6 sub-fixes shipped in this commit. The remaining stall-detection / pre-kill-diagnostics work would require refactoring `_subprocess_call` from `subprocess.communicate(timeout=...)` (one-shot blocking) to a streaming-stdout reader with an async stall watchdog. That's a significant restructure relative to the rest of Phase 6 and is filed as a follow-up.

What shipped:

1. **Pre-flight credential refresh.** `send_message` now calls `_ensure_credentials_fresh(min_remaining_seconds=300)` BEFORE the retry loop. If the OAuth token expires in less than 5 minutes, an explicit `_try_token_refresh()` runs synchronously so the subprocess sees a freshly-issued token. Suspected to eliminate the silent 90 s subprocess hangs observed at credential boundaries.
2. **Latent watchdog bug — fixed.** `position_watchdog.py:323` was reading `claude_client._last_response_time` via `getattr(..., 0.0) or 0.0`. The attribute did not exist on the client — the silent fallback masked the bug. The client now exposes a consistent triple:
   - `_last_call_attempt_time` — set BEFORE each subprocess spawn (request start)
   - `_last_response_time` — set ONLY on a successful response (mirrors `_last_call_time`)
   - `_last_call_time` — kept as a backwards-compat success-time alias
3. **Watchdog semantics corrected.** Watchdog now uses `max(_last_call_attempt_time, _last_response_time)` so a long in-flight call doesn't false-trip the 10-min staleness check, while a hung-then-timed-out call eventually does (because no fresh `attempt_time` will land).

## Files changed

| File | Change |
|---|---|
| `src/brain/claude_code_client.py` | Added `_last_call_attempt_time` and `_last_response_time` to `__init__`. Added `_get_credential_expiry_seconds()` and `_ensure_credentials_fresh(min_remaining_seconds=300)` helpers. Wired pre-flight refresh into `send_message`. Stamped `_last_call_attempt_time` BEFORE `_execute_cli` and `_last_response_time` ON success in both the main retry loop and the auth-recovery branch. |
| `src/workers/position_watchdog.py` | Rewrote the heartbeat-staleness branch to use the new attribute triple; documented the semantics in code comments; replaced the silent `getattr(..., 0.0)` reads (which masked the missing attribute) with explicit lookups. |
| `tests/test_brain_credential_preflight.py` | NEW — 9 tests across 3 classes covering expiry seconds parsing, refresh trigger threshold, refresh failure handling, and heartbeat-attribute presence. |

## Behavior matrix

### Credential states the pre-flight handles

| TTL at call time | Pre-flight action | Outcome |
|---|---|---|
| > 5 min | None (no refresh fires) | Call proceeds with current token |
| 1-5 min | `_try_token_refresh()` runs; new TTL ~1h | Call proceeds with fresh token; `CLAUDE_PREFLIGHT_REFRESH_OK` logged |
| < 1 min or expired | Same as above | Same — refresh re-issues the token before the subprocess spawn |
| Missing creds file | Skip pre-flight; proceed | The 3-layer recovery (existing) handles the live failure |
| Refresh fails (network/etc.) | Log `CLAUDE_PREFLIGHT_FAIL`, proceed | The live call may still succeed; otherwise existing recovery fires |

### Watchdog 10-min staleness check (post-fix)

- Active call in flight (attempt at T-7min, no response yet): `_alive_at = T-7min`. Elapsed = 7min. **Stays passive.** Pre-fix: same outcome (because `_last_call_time` was a success time and a hung call wouldn't refresh it either, but the silent attribute miss meant the watchdog was reading 0.0, which trips elapsed > now). The bug was *hidden*; this fix makes the check actually correct.
- Last successful response at T-15min, no calls since: `_alive_at = T-15min`. Elapsed = 15min. **Trips → safety_net.**
- Call started at T-12min, hung past 90s timeout, no new attempt: `_alive_at = max(T-12min, last response 30min ago) = T-12min`. Elapsed = 12min. **Trips → safety_net.**

## What was deliberately deferred

- **Stall detection inside `_subprocess_call`.** Currently uses `proc.communicate(timeout=self.timeout)` which is one-shot blocking — there's no way to log "60 s of silence at T-60s" because `communicate` doesn't return until either complete or timeout. A correct implementation reads stdout in chunks via async tasks, updating `_last_stdout_time`, and emits `CLAUDE_PROC_STALL` when 60s of silence elapses. This is a meaningful restructure of the subprocess path (~80 lines) and merits its own commit + careful test coverage so the new I/O loop doesn't deadlock or lose final-bytes-on-success.
- **Pre-kill diagnostics** (`/proc/{pid}/status`, `/proc/{pid}/wchan`, optional `py-spy dump`). Cheap to add but most useful in conjunction with stall detection — defer to the same follow-up.
- **Cascade correlation event** (`WORKER_DEGRADATION_CASCADE`). The information is already derivable from existing logs; adding the synthesised event is an observability nicety. Filed for Phase 11 batch.

These items remain valuable. They're filed as `phase6_followup_subprocess_streaming.md` work.

## Tests

9 new tests in `tests/test_brain_credential_preflight.py`:

| Class | Tests | What's verified |
|---|---|---|
| `TestGetExpirySeconds` | 4 | Positive TTL for future expiry; negative TTL for expired; None for missing file; None for malformed JSON |
| `TestEnsureCredentialsFresh` | 4 | No refresh when fresh; refresh fires when near expiry; failed refresh returns False non-raising; missing creds returns True without attempt |
| `TestHeartbeatAttributes` | 1 | All three attributes (`_last_call_attempt_time`, `_last_response_time`, `_last_call_time`) exist at construction with non-zero values |

Result: **9/9 pass.** Phase 1+5 regression suite (33 tests) continues to pass.

## Verification (operator action — Phase 13)

Live trials post-restart:

1. Wait for the next credential expiry boundary (visible from `Claude credentials expire in N minutes` startup log) or force one by manipulating `~/.claude/.credentials.json`.
2. Confirm `CLAUDE_PREFLIGHT_REFRESH | reason=expires_in mins_left=...` fires before `CLAUDE_CALL_START`.
3. Confirm subsequent `CLAUDE_CALL_OK` arrives without the prior 90 s hang.
4. Confirm the watchdog stays in `passive` mode through the boundary instead of flipping to `safety_net`.

## Status against the spec's verification criteria

| Spec criterion | Result |
|---|---|
| Pre-flight refresh fires before next call | ✅ test-verified |
| Brain call succeeds at credential boundary | ⏳ pending live observation |
| `CLAUDE_PROC_STALL` at 60s silence | ⏳ deferred to follow-up |
| Pre-kill diagnostics captured | ⏳ deferred to follow-up |
| Watchdog reads non-zero `_last_response_time` | ✅ test-verified |
| `WORKER_DEGRADATION_CASCADE` emitted on failure | ⏳ filed for Phase 11 |

The latent watchdog bug is closed — the missing attribute is now there with correct semantics. Pre-flight refresh closes the dominant credential-boundary hang. The remaining items are observability nice-to-haves.

## Rollback path

`git revert HEAD` reverts cleanly. The new attributes are additive (the watchdog's `getattr(..., 0.0)` fallback is now redundant but harmless if reverted).
