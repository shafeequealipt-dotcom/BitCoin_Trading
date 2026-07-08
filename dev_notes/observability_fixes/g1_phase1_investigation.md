# G1 Phase 1 — Investigation: STRAT_CALL_A pairing & completion event

## Headline finding

The audit's claimed gap (`STRAT_CALL_A_DONE` 0/12) reflects two separate
realities:

1. **Tag-name mismatch:** the canonical completion tag is
   `STRAT_CALL_A_END` (matches the STRAT cluster's `_END` convention),
   not `_DONE`. It fires on every controlled exit.
2. **Real pairing gap:** in the audited window, `STRAT_CALL_A_START`
   fires 12 times and `STRAT_CALL_A_END` fires only 10 times — a
   **2-event pairing gap**. The same gap propagates upward:
   `BRAIN_CYCLE_A` 12 vs `BRAIN_CYCLE_A_DONE` 10. Two CALL_A cycles
   started and never emitted a completion event at either layer.

The most plausible root cause is **`asyncio.CancelledError` (a
`BaseException`, not an `Exception`)** during the
`await self.claude.send_message(...)` step. The current `try/except
Exception` block at `strategist.py:892` does not catch `BaseException`,
so the end-of-cycle log line at L895 is skipped on cancellation. The
same blind-spot exists at the brain-cycle wrapper
(`layer_manager.py:758`).

The fix is a `try/finally` so the END line is guaranteed on every exit
path — including cancellation. **No behavior change**: the cycle still
returns `None` or `plan` exactly as before, and exceptions still
propagate to the caller.

---

## Anatomy of `create_trade_plan` (`src/brain/strategist.py:734-896`)

```
L734  async def create_trade_plan(...) -> StrategicPlan | None:
L736      _cycle_start = time.time()
L737      did = new_decision_id()
L738      log.info("STRAT_CALL_A_START | did={did} | {ctx()}")           ← (A)

L749      try:                                                           ┐
L753-758    if _use_packages and layer_manager has packages:             │  Pre-check
L757            if packages empty:                                       │
L759                log.warning("STRAT_CALL_A_SKIPPED | reason=...")     │
L764                log.info("STRAT_CALL_A_END | el=... skipped=Y")     ─┤  exit ① (skip)
L768                return None                                          │
L769      except Exception as e:                                          │
L777        log.warning("STRAT_CALL_A_PRECHECK_ERR | err=...")            │  falls through
                                                                          ┘

L781      try:                                                           ┐
L782          prompt = await self._build_trade_prompt()                  │
L783          log.info("STRAT_CALL_A | chars={len(prompt)}")             │  Main block
L827-833      log.info("STRAT_AGGRESSIVE_FRAMING | ...")                 │
L835          raw_response = await self.claude.send_message(...)         │  ← LONG await
L837-842      plan_data = extract_json(raw_response); plan = _parse(...) │
L845-857      if urgent_concerns: parse position_actions; emit URGENT_ACTS
L861-863      log.info("STRAT_CALL_A_PLAN | trades=... view=...")        │
L865-870      for each trade: log.info("STRAT_DIRECTIVE | ...")          │
L872          if not plan.new_trades:                                    │
L873            log.warning("STRAT_CALL_A_NO_TRADES | view=...")         │
L881-886        if _zero_two: log.info("STRAT_ZERO_TRADES_INTENTIONAL")  │
L888          _elapsed = ...                                             │
L889          log.info("STRAT_CALL_A_END | el=... trades=...")          ─┤  exit ② (success)
L890          return plan                                                │
                                                                         │
L892      except Exception as e:                                         │  exit ③ (caught error)
L893          log.error("STRAT_CALL_A_FAIL | err=...")                   │
L894-895      log.info("STRAT_CALL_A_END | el=... failed=Y")             │
L896          return None                                                ┘

  exit ④ (uncaught: BaseException / CancelledError)                       ← SILENT, no END
```

### Exit-path table

| # | Path | END fires? | Currently emits |
|---|------|------------|------------------|
| ① | Skip (no packages) | yes (L765) | `STRAT_CALL_A_SKIPPED` + `STRAT_CALL_A_END skipped=Y` |
| ② | Success (plan returned) | yes (L889) | `STRAT_CALL_A_END` |
| ③ | Caught `Exception` (parse/build/etc.) | yes (L895) | `STRAT_CALL_A_FAIL` + `STRAT_CALL_A_END failed=Y` |
| ④ | Uncaught `BaseException` (`CancelledError`, `KeyboardInterrupt`, `SystemExit`) | **NO** | nothing |

### Evidence in the audited window

| Counter | Count | Where in code | Notes |
|---------|-------|---------------|-------|
| `STRAT_CALL_A_START` | 12 | L738 | one per cycle entry |
| `STRAT_CALL_A_SKIPPED` | 2 | L760 | exit ① |
| `STRAT_CALL_A_END (skipped=Y)` | 2 of 10 | L765 | exit ① END |
| `STRAT_CALL_A_PLAN` | 8 | L862 | reached the post-Claude success branch |
| `STRAT_CALL_A_END (success)` | 8 of 10 | L889 | exit ② END |
| `STRAT_CALL_A_FAIL` | 0 | L893 | exit ③ — no caught exceptions in window |
| `STRAT_CALL_A_END (failed=Y)` | 0 of 10 | L895 | exit ③ END |
| **Missing ENDs** | **2** | — | **2 cycles took exit ④** |

Arithmetic check: 2 skip-ENDs + 8 success-ENDs + 0 fail-ENDs = 10 ✓ vs 12 STARTs = **2 missing**.

The 8-vs-12 PLAN count is the smoking gun: 2 cycles entered the main
`try` block (got past the pre-check) but never reached the PLAN log
line. The longest await inside that block before the PLAN log is
`await self.claude.send_message(...)` at L835. If that await is
cancelled, control unwinds through both `try/except Exception` blocks
without firing the END.

### Outer-wrapper mirror (`src/core/layer_manager.py:753-880`)

The brain cycle wraps `create_trade_plan()` the same way:

```
L753  if call_type == "A":
L755      log.info("BRAIN_CYCLE_A | ...")                                 ← (A)
L756      try:
L757          plan = await strategist.create_trade_plan()
L758      except Exception as _e:                                          ← also Exception, not BaseException
L760-762      log.error("BRAIN_CYCLE_A_FAIL | el=... err=...")
L765-766      return
L767      elapsed_ms = ...
L769-870  if plan: ... ; log.info("BRAIN_CYCLE_A_DONE | el=... trades=...") ← (B)
L876-880  else:    log.info("BRAIN_CYCLE_A_DONE | el=... empty_plan=Y")    ← (B)
```

`BRAIN_CYCLE_A` 12 vs `BRAIN_CYCLE_A_DONE` 10 confirms the inner gap
cascades to the outer wrapper for the exact same reason: `CancelledError`
escapes both `try/except Exception` blocks. **Fix must apply at both
layers** to guarantee a paired completion event at each level.

### CALL_B parity

`STRAT_CALL_B_START` (10) = `STRAT_CALL_B_END` (10). No gap in the audited
window. **However the same structural vulnerability exists** at
`strategist.py:898-959` — same `try/except Exception` pattern. If a
cancellation hits while awaiting `claude.send_message(...)` (L932), the
END at L958 is bypassed.

Per Rule 14 (gap-cluster awareness), the fix MUST also harden CALL_B by
the same try/finally pattern, even though the gap isn't yet visible in
this window. Same applies to `BRAIN_CYCLE_B` (only 11 vs 10 — 1-event
gap, less reproducible but same root cause).

---

## Brain cluster sweep (Prompt Part D, Cluster A)

| Tag | In src/ | In window | Status |
|-----|---------|-----------|--------|
| `STRAT_CALL_A_START` | yes | 12 | OK |
| `STRAT_CALL_A_END` | yes (3 emission sites: L765, L889, L895) | 10 | **gap 2 events** |
| `STRAT_CALL_A_FAIL` | yes (L893) | 0 | OK (no failures in window) |
| `STRAT_CALL_A_SKIPPED` | yes (L760) | 2 | OK |
| `STRAT_CALL_A_PRECHECK_ERR` | yes (L777) | 0 | OK (no pre-check exceptions) |
| `STRAT_CALL_A_TIMEOUT` | **no** | 0 | no timeout instrumentation exists |
| `STRAT_CALL_A_RETRY` | **no** | 0 | no retry instrumentation exists |
| `STRAT_CALL_A_ERROR` | **no** (uses `_FAIL`) | 0 | tag-name alternative; not a gap |
| `STRAT_CALL_B_*` siblings | yes | START 10 / END 10 | OK in window; same structural vuln |
| `BRAIN_CYCLE_A` | yes (layer_manager.py:755) | 12 | OK |
| `BRAIN_CYCLE_A_DONE` | yes (L873, L879) | 10 | **gap 2 events (mirrors)** |
| `BRAIN_CYCLE_A_FAIL` | yes (L761) | 0 | OK (no caught fails) |
| `BRAIN_CYCLE_B_DONE` | yes | 10 | OK (1 missing from 11 — same vuln) |
| `BRAIN_DO_START`/`DONE`/`FAIL`/`SKIP` | yes | 8/8/0/0 | OK in window |

### Additional gaps surfaced (candidates for G12+)

1. **STRAT_CALL_A_TIMEOUT not instrumented.** `claude.send_message` can
   stall indefinitely; there is no observable timeout marker. If a
   per-call timeout is later added as a behavioral change (out of
   scope here), the event should accompany it.
2. **STRAT_CALL_A_RETRY not instrumented.** Retry loops exist inside
   `claude_code_client` (visible via `CLAUDE_REFRESH_OK`/`_FAIL`,
   `CLAUDE_POST_REFRESH_FAIL`, etc.) but not at the strategist level.
   Surfaces only via cross-tag correlation. Probably acceptable; flag
   for operator.
3. **CALL_A position-actions block isn't paired with a completion
   counter.** `STRAT_CALL_A_URGENT_ACTS` (3) has no `_END_URGENT`
   counterpart. Out of scope for this gap.

---

## Schema proposal

### Tag name

Keep `STRAT_CALL_A_END` (canonical within the STRAT cluster; renaming
to `_DONE` would break the established `_START`/`_END` pair and
diverge from sibling `STRAT_CALL_B_END`, `STRAT_CYCLE_END`,
`RULE_EVAL_END`, etc.).

### Field set

| Field | Currently emitted | Added by this fix | Source |
|-------|--------------------|--------------------|--------|
| `did=` | yes (via `ctx()`) | unchanged | `log_context.get_did()` |
| `el=` | yes | unchanged | `(time.time() - _cycle_start) * 1000` |
| `status=` | partial (`skipped=Y`/`failed=Y` flags) | **standardized** into one `status=` field with values `success`/`skipped`/`failed`/`cancelled` | computed at emission |
| `trades=` | yes (success/fail) | unchanged | `len(plan.new_trades)` |
| `prompt_chars=` | currently in `STRAT_CALL_A` (L783), not in END | **added** to END | `len(prompt)` (cached in local var) |
| `sys_prompt_chars=` | not currently emitted | **added** to END | `len(system)` |

`prewarmed` was in the audit's wish list but it is already observable
via the separate `CLAUDE_PROC_POOL_ACQUIRE` (prewarm hit) vs
`CLAUDE_PROC_SPAWNED ... pool_miss=true` (cold spawn) events emitted in
`claude_code_client.py`. Correlation is via `did=`. Including it in
STRAT_CALL_A_END would require either a return-value contract change
from `claude.send_message` (FORBIDDEN — behavior change) or a thread-
local read-back (fragile). **Not in scope** for this gap.

### Log level

INFO on success / skipped / cancelled (all expected outcomes).
ERROR remains for the `FAIL` event preceding the END.

### Emission strategy

Replace the existing `try/except Exception` at `strategist.py:781-896`
with `try/finally`:

- `try` block contains all the existing logic unchanged
- `finally` block computes `_elapsed`, derives `status` based on local
  flags, and emits `STRAT_CALL_A_END`
- The `except Exception` inside the try is preserved (emits the
  `_FAIL` event), but no longer emits END itself — that becomes the
  responsibility of the finally
- `BaseException` (including `CancelledError`) is NOT caught — it
  re-raises after `finally` runs, preserving cancellation semantics

The same pattern is applied at:
- `strategist.py:898-959` (`create_position_plan`)
- `layer_manager.py:753-880` (`BRAIN_CYCLE_A` wrapper) — emits
  `BRAIN_CYCLE_A_DONE` in finally
- `layer_manager.py:886-959` (`BRAIN_CYCLE_B` wrapper) — emits
  `BRAIN_CYCLE_B_DONE` in finally

### Cancelled-status field

When the finally runs as a result of cancellation (i.e., a
`BaseException` is propagating), the `status` field is set to
`cancelled`. Python provides this via the `sys.exc_info()` shape or via
a local flag tracked in the try body. The cleaner approach is a
try/except/finally with a narrow `except BaseException` that logs the
cancellation and re-raises:

```python
_status = "success"  # set when normal return
try:
    ...
    plan = self._parse_trade_plan(plan_data)
    ...
    _status = "success"
    return plan
except Exception as e:
    log.error(f"STRAT_CALL_A_FAIL | err=...")
    _status = "failed"
    return None
except BaseException:
    _status = "cancelled"
    raise  # MUST re-raise; cancellation is not a normal flow
finally:
    _elapsed = (time.time() - _cycle_start) * 1000
    log.info(f"STRAT_CALL_A_END | el={_elapsed:.0f}ms status={_status} ... | {ctx()}")
```

This pattern preserves the existing return contract and exception
propagation exactly. Only an additional log line is emitted in the
previously-silent cancellation path. Per Rule 3, **no behavior change**.

### Shadow parity

The Explore pass confirmed Shadow re-uses the same `Strategist`
instance (no parallel shadow strategist). The fix lands in
`src/brain/strategist.py` once and applies to both Shadow and Bybit-demo
paths. `src/core/layer_manager.py` is also shared. No additional shadow
fork to edit.

---

## Synthesis (WHERE / WHAT / WHY)

**WHERE** the fix lands:
- `src/brain/strategist.py:734-896` — `create_trade_plan()` body
- `src/brain/strategist.py:898-959` — `create_position_plan()` body (preventive parity)
- `src/core/layer_manager.py:753-884` — CALL_A brain-cycle wrapper
- `src/core/layer_manager.py:886-959` — CALL_B brain-cycle wrapper (preventive parity)

**WHAT** changes:
- Replace `try/except Exception` with `try/except Exception/except BaseException/finally`
- Emit `STRAT_CALL_A_END` / `STRAT_CALL_B_END` / `BRAIN_CYCLE_A_DONE` / `BRAIN_CYCLE_B_DONE` in the `finally` (single emission per call)
- Add `status=` field (success/failed/skipped/cancelled) and `prompt_chars`/`sys_prompt_chars` fields to STRAT_CALL_A_END and STRAT_CALL_B_END

**WHY** this is safe:
- Behavior identical: same return values, same exception propagation
- Loguru emission is non-blocking; no hot-path latency
- `BaseException` is re-raised after the finally runs
- Existing `_FAIL` events continue firing on the `Exception` path
- The skip path (exit ①) keeps its dedicated SKIPPED + END pair; we simply
  fold its END emission into a controlled `return None` path that also
  flows through the unified finally — semantically identical, one less
  duplicate emission to maintain

**Test plan (Phase 3 deliverable):**
- Unit test: simulate a `CancelledError` raised from a mocked
  `claude.send_message`, assert STRAT_CALL_A_END fires with
  `status=cancelled` and that the exception propagates.
- Unit test: simulate success path, assert END has
  `status=success trades=N prompt_chars=P sys_prompt_chars=S`.
- Unit test: simulate `Exception` from `_parse_trade_plan`, assert
  `STRAT_CALL_A_FAIL` fires AND END fires with `status=failed`.
- Unit test: skip path (no packages) emits SKIPPED + END(status=skipped).
- Pairing test (integration): caplog harness records emissions; assert
  every `_START` has exactly one matching `_END` for both A and B.

**Volume impact:** zero net new emissions on success/fail/skip paths
(the END already fires there). Adds 1 emission per cancellation event
(rare). The standardized `status=` field replaces today's mixed
`skipped=Y`/`failed=Y` flags — semantic clarification only.

---

## Open question (one) for operator

The G1 fix as proposed lands at **four sites** (CALL_A + CALL_B + their
brain-cycle wrappers) even though only CALL_A shows a gap in the
audited window. The rationale: same vulnerability, same fix, same
file/function family. Bundling them per the prompt's Rule 14
("consistent schema across cluster") is the right move.

**Alternative:** Land only the CALL_A + BRAIN_CYCLE_A fix this round;
defer CALL_B + BRAIN_CYCLE_B to a follow-up. Pro: smaller diff. Con:
leaves the same structural blind spot in place.

Recommendation: **bundle all four sites in this gap**, keep the commit
focused on the structural change (try/finally + status field). Phase 4
verification then checks all four pairs at once.

This is the only operator decision needed before Phase 2/3 proceeds.

---

## Next

Phase 2: write the Phase 2 report and request operator schema
confirmation, then proceed to Phase 3 implementation on branch
`obs/g1-strat-call-a-done`.
