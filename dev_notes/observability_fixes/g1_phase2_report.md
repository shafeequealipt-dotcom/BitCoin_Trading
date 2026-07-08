# G1 Phase 2 — Schema Decision & Implementation Plan

## Audit said vs current code shows

| Audit claimed | Current state | Resolution |
|---------------|---------------|------------|
| `STRAT_CALL_A_DONE` 0 events | `STRAT_CALL_A_END` 10 events (canonical) | Keep `_END` tag; standardize fields. Naming-convention analysis (Phase 0 §0.5) confirms `_END` is the STRAT cluster's canonical complete suffix. |
| 12 START unpaired | 12 START : 10 END (2-event gap) | Add `try/finally` to guarantee END emission on every exit path, including `BaseException`/`CancelledError`. |
| Need `did`, `el`, `status`, `directive_count`, `prompt_chars`, `sys_prompt_chars`, `prewarmed` | Current END has `el`, `trades` + flags | Add `status` (4-value enum) and `prompt_chars`/`sys_prompt_chars`. `did=` already via `ctx()`. `directive_count` already covered by `trades=`. `prewarmed` left to existing `CLAUDE_PROC_POOL_ACQUIRE` / `CLAUDE_PROC_SPAWNED` events correlated by `did=`. |

## Decisions (capturing absent operator review)

| # | Decision | Reasoning |
|---|----------|-----------|
| 1 | **Keep tag `STRAT_CALL_A_END`** (do not rename to `_DONE`) | Per Phase 0 naming analysis — `_END` is canonical within STRAT cluster (4 sibling tags use it); renaming breaks dashboards and audit history. |
| 2 | **Bundle CALL_A + CALL_B + both brain-cycle wrappers in this gap** | Same root-cause structural vulnerability (`try/except Exception` doesn't catch `BaseException`). Rule 14 — cluster fields must match. Splitting them creates two-commit churn for the same fix. |
| 3 | **Add `status=` field with values success/failed/skipped/cancelled** | Standardizes today's mixed `skipped=Y` / `failed=Y` flags into one enum. Backward-compatible parsing — flags are still derivable from the status value. |
| 4 | **Add `prompt_chars` / `sys_prompt_chars` to END only on the success/fail paths** | These are computed during the cycle and available locally. Cancelled/skipped paths lack the prompt build, so fields default to `prompt_chars=0 sys_prompt_chars=0`. |
| 5 | **Do not add `prewarmed` field** | Would require behavior-change to `claude.send_message` contract (FORBIDDEN). Already observable via `CLAUDE_PROC_POOL_ACQUIRE`/`CLAUDE_PROC_SPAWNED` events correlated by `did=`. |
| 6 | **Apply `try/finally` at four sites** | `strategist.create_trade_plan` (L734), `strategist.create_position_plan` (L898), `layer_manager` CALL_A wrapper (L753), `layer_manager` CALL_B wrapper (L886). |
| 7 | **Re-raise `BaseException` after finally** | Cancellation must continue propagating. The finally only ADDS a log line; semantics unchanged. |
| 8 | **No behavior change** | Return values, exception propagation, prompt build, claude call — all unchanged. Only logging gains coverage. |

## Final schema

### `STRAT_CALL_A_END`

```
STRAT_CALL_A_END | el={ms}ms status={success|failed|skipped|cancelled} trades={n} prompt_chars={p} sys_prompt_chars={s} | {ctx()}
```

### `STRAT_CALL_B_END` (parity)

```
STRAT_CALL_B_END | el={ms}ms status={success|failed|deferred|cancelled} acts={n} prompt_chars={p} sys_prompt_chars={s} | {ctx()}
```

Note: CALL_B has `deferred` instead of `skipped` (the price-divergence
defer path uses that wording in `PROMPT_DEFERRED`).

### `BRAIN_CYCLE_A_DONE` (parity)

```
BRAIN_CYCLE_A_DONE | el={ms}ms status={success|failed|empty_plan|cancelled} trades={n} view='...' | {ctx()}
```

### `BRAIN_CYCLE_B_DONE` (parity)

```
BRAIN_CYCLE_B_DONE | el={ms}ms status={success|failed|skip|cancelled} acts={n} | {ctx()}
```

Per layer's exit-path enum varies (the layer_manager has `empty_plan`
and `skip` cases that strategist doesn't, and vice-versa). This is
intentional — the status enum carries the semantics of the layer it
fires from.

## Implementation plan

1. Create branch `obs/g1-strat-call-a-done` off `audit/all-tier2-combined`.
2. Edit `src/brain/strategist.py`:
   - `create_trade_plan` body: convert to try/except/finally; emit END once in finally with the new field set.
   - `create_position_plan` body: same pattern.
3. Edit `src/core/layer_manager.py`:
   - CALL_A branch: try/except/finally around the await, emit DONE in finally.
   - CALL_B branch: same.
4. Add unit tests under `tests/brain/test_strategist_call_pairing.py` (new file) covering:
   - success path emits END with status=success
   - exception path emits END with status=failed + the existing FAIL event still fires
   - cancellation path emits END with status=cancelled + exception propagates
   - skip path emits END with status=skipped
5. Add unit tests under `tests/core/test_layer_manager_brain_cycle.py` (new file) for the four DONE paths.
6. Run `pytest -x tests/brain/test_strategist_call_pairing.py tests/core/test_layer_manager_brain_cycle.py` until green.
7. Run `pytest -x tests/` to confirm no regressions in the broader suite.
8. Run `ruff check src/brain/strategist.py src/core/layer_manager.py` clean.
9. Commit: one atomic commit, message format from the recent history (e.g., `fix(obs-g1): try/finally pairing on STRAT_CALL_A/B + BRAIN_CYCLE_A/B`).

## Phase 4 verification criteria

- Live 2h soak after deploy
- `STRAT_CALL_A_START : STRAT_CALL_A_END` ratio = 1:1 (no gap)
- `STRAT_CALL_B_START : STRAT_CALL_B_END` ratio = 1:1
- `BRAIN_CYCLE_A : BRAIN_CYCLE_A_DONE` ratio = 1:1
- `BRAIN_CYCLE_B : BRAIN_CYCLE_B_DONE` ratio = 1:1
- All END events include the new `status=` field with one of the documented enum values
- `prompt_chars` and `sys_prompt_chars` non-zero on success path; zero on cancelled/skipped
- No regression in trade open/close counts vs baseline
