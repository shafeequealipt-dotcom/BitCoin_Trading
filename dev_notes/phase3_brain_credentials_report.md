# Phase 3 — Brain CLI Credential Pre-flight Report

**Date:** 2026-04-27
**Commit:** `fb59a60`
**Status:** Implementation complete (single bundled commit).

## Root cause and fix

`_ensure_credentials_fresh` used a hardcoded 300 s margin and `_try_token_refresh` was a single 30 s urllib attempt with no retry. When the access token expired mid-call, the CLI subprocess hung silently for the full 300 s timeout; cascade: SIGKILL → strategist STALE → enforcer STALE → watchdog flip to safety_net for ~110 s.

Five sub-fixes landed:

1. **Configurable refresh margin** — `BrainSettings.credential_refresh_margin_seconds` (default 600 s = 10 min, was hardcoded 300).
2. **Multi-attempt refresh** — `_try_token_refresh_with_retries` wraps `_try_token_refresh` with 3-attempt exponential backoff (1 s / 3 s / 7 s).
3. **Raise-on-blocking-failure** — refresh failure INSIDE the margin now raises `CredentialRefreshError` instead of returning False; the call aborts before spawning a doomed subprocess. Outside the margin, behaviour unchanged.
4. **Progressive stall detection** — replaced the single 60 s rate-limited warn with named events at 60 s / 120 s / 240 s of silence, each firing exactly once. The 120 s and 240 s buckets capture lightweight `/proc/{pid}/{status,wchan}` snapshots so the operator sees what the subprocess is doing well before SIGKILL. Legacy `CLAUDE_PROC_STALL` rate-limited 60 s warn preserved for dashboard backwards compat.
5. **Cascade attribution** — on subprocess timeout, `BRAIN_FAILURE_CASCADE` log fires with `reason=credential_hang|network_or_cli`, `duration_ms`, `cred_ttl_s`, and `cred_margin_s`. Reason classified heuristically: `credential_hang` if cred TTL was inside margin at call entry, else `network_or_cli`.

## Files modified

- `src/core/exceptions.py` — `CredentialRefreshError` under `BrainError`
- `src/config/settings.py` + `config.toml` — `[brain]` `credential_refresh_margin_seconds` (600), `credential_refresh_max_attempts` (3), `stall_warn_buckets_seconds` ((60, 120, 240))
- `src/brain/claude_code_client.py` — full set of changes above; new helpers `_try_token_refresh_with_retries` and `_collect_stall_diagnostics`
- `src/workers/manager.py` — pass new BrainSettings keys at ClaudeCodeClient construction
- `tests/test_brain_credential_preflight.py` — updated test for new raise contract

## Operator runbook

| Trial | Procedure | Pass criterion |
|---|---|---|
| 3.1 | Backdate `~/.claude/.credentials.json` `expiresAt` to within 600 s | Pre-flight refresh fires; on success call proceeds; no cascade |
| 3.2 | Block Anthropic API DNS | `CLAUDE_PROC_STALL_60S/120S/240S` fire in order; `CLAUDE_PROC_PREKILL` at 300 s; `BRAIN_FAILURE_CASCADE` correlated |
| 3.3 | 24 h reliability run | Brain success rate > 95 %; 0 cascades attributed to `credential_hang` |

## Rollback

`git revert fb59a60` restores legacy behaviour (300 s hardcoded margin, single-attempt refresh, generic 60 s stall warns, no cascade attribution). Risk-free; observability-only changes can be peeled independently.
