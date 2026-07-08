# ALPHA Phase 4 — Verification Report

## Scope

Verification of R1 implementation (Option E + Option D) on branch
`fix/r1-xray-counter-inversion`. Three atomic commits:

- `712ccb8` alpha/phase3-1 — plumb trade_direction StructuralAnalysis -> StructuralData
- `478dd2f` alpha/phase3-2 — XRAY_CLASSIFY_SUMMARY + new XRAY_DIRECTION_SPLIT
- `465eed9` alpha/phase3-3 — six propagation tests

## Test results

Direct R1 tests + neighbor regression suite:

```
tests/test_alpha_r1_trade_direction.py       6 passed
tests/test_apex_direction_lock.py            29 passed, 1 failed (pre-existing)
tests/test_apex_lock_propagation.py          13 passed
tests/test_xray_counter_property.py          74 passed
tests/test_xray_dir_flip.py                  3 passed
tests/test_setup_classifier_counter.py       26 passed
                                            ---
total                                       150 passed, 1 failed
```

Pipeline integration neighbors:

```
tests/test_apex_pipeline_integration.py      passing
tests/test_corrected_layer1_integration.py   passing
tests/test_corrected_layer1_pipeline_e2e.py  passing
                                            59 passed
```

Combined: **209 passed, 1 pre-existing failure**.

### The single failure is pre-existing

`test_apex_direction_lock.py::test_system_prompt_still_has_rsi_caution`
asserts the string "Oversold RSI in a downtrend" is in
`STRATEGIST_SYSTEM_PROMPT`. The string is no longer in the prompt (the
brain-enrichment work upstream rewrote that section). I verified the
identical failure exists at HEAD `7320266` BEFORE any ALPHA change —
ALPHA introduces no regression. This is a stale assertion that should
be updated when the brain prompt's stable RSI guidance is renamed.

## Verification metrics (against DELTA 04 criteria)

| Metric | Status |
|---|---|
| V1 — XRAY_CLASSIFY_SUMMARY emits `trade_dir_long`, `trade_dir_short`, `counter_count` | VERIFIED — code shipped + test_xray_direction_split_log_format asserts the format |
| V2 — APEX_DIR_LOCK lines include `trade_direction` field | DEFERRED to BETA Phase 3 (BETA's consumer code emits the field in APEX_LOCK_DECISION_EXPLAINED) |
| V3 — counter setups no longer blocked at APEX (`APEX_DIR_LOCK ... counter ... locked=False`) | DEFERRED to BETA Phase 3 |
| V4 — brain Buy directive share rises to >= 30% | DEFERRED to integrated live trial |
| V5 — 100% of `bullish_fvg_ob_counter` events show `trade_direction=long suggested_direction=short` | VERIFIED in unit tests; live confirmation deferred to operator trial |

V1 + V5 verified now. V2-V4 require BETA's consumer code or live trial.

## Cross-cutting safety checks

- Shadow unaffected: zero changes to `src/shadow/` or `shadow_adapter.py`. The shadow_kline_reader.py is read but unmodified.
- DB cascade absence: zero changes to `src/database/` or `src/core/` connection layer. SQLite WAL paths untouched.
- Brain prompt format: zero changes to `src/brain/prompts.py` or `src/brain/strategist.py`. The brain already reads `trade_direction` (verified in ALPHA Phase 1); this implementation does not change what the brain sees.
- Backward compat: `StructuralData.trade_direction` defaults to empty string. Every existing consumer that does not reference the field continues to work unchanged. Existing positional `StructuralData(...)` calls are NOT affected because the new field has a default.
- Type hints: `trade_direction: str = ""` is fully typed.
- Logging convention: uses `log.info(...)` with `ctx()` per project pattern.
- Import smoke: `python3 -c "from src.apex.models import StructuralData; from src.apex.assembler import _gather_structural_data_from_cache; from src.workers.structure_worker import StructureWorker"` succeeds.

## Files modified

- `src/apex/models.py` — +12 lines (new `trade_direction` field on `StructuralData` with docstring)
- `src/apex/assembler.py` — +13 lines (propagation block after the setup_type propagation)
- `src/workers/structure_worker.py` — +44 lines, -1 line (accumulator init, accumulation in success branch, augmented `XRAY_CLASSIFY_SUMMARY`, new `XRAY_DIRECTION_SPLIT` line)
- `tests/test_alpha_r1_trade_direction.py` — +243 lines (new file, 6 tests)

Total diff: +312 / -1 across 4 files.

## What is NOT in this branch

- Consumer code in `optimizer.py` for `trade_direction` — that ships on BETA's `fix/r2-r3-apex-direction-lock` branch as part of R2 Option B's structural-RR consultation
- Consumer code in `apex/gate.py` for `trade_direction` — that ships on GAMMA's `fix/r4-portfolio-direction-cap` branch as part of R4 Design C's aim-conditional cap
- WR-aware override threshold — that ships on BETA's branch as part of R3 Option E

## GO criterion for next phase

Per DELTA 02 implementation sequence:

ALPHA Phase 4 GO requires:
- All 6 R1 unit tests pass (PASS)
- Neighbor regression suite passes minus the pre-existing RSI failure (PASS)
- Shadow unaffected (PASS — zero shadow code touched)
- DB cascade absence holds (PASS — zero database code touched)
- Import smoke (PASS)

**GO recorded.** BETA Phase 3 may begin on `fix/r2-r3-apex-direction-lock`.

## Operator notes

The R1 fix is a pure-plumbing addition. By itself it has no behavior effect — APEX will start CONSUMING the new field in BETA's R2 implementation, and GAMMA's R4 will also read it. The observability lines (`XRAY_CLASSIFY_SUMMARY` augmented + new `XRAY_DIRECTION_SPLIT`) DO immediately take effect — the operator will see the trade_direction distribution next time the structure_worker emits the per-tick summary.

If the operator wants to verify in production after ALPHA's commits land but BEFORE BETA's: run the system for one structure_worker tick and grep workers.log for `XRAY_DIRECTION_SPLIT` — should see one line with `trade_dir_long=N trade_dir_short=N` for the current universe.
