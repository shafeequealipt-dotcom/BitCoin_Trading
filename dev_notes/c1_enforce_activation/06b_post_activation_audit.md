# C1 — Post-Activation Verification Audit

Cross-check run immediately after the Phase 3 flag flip (HEAD `3bfb5e4`) on 2026-05-21. The audit covers integration correctness, test pass rate, naming, imports, and regression impact across the touched modules and the rest of the codebase.

## Summary

All 22 audit checks pass. The activation is complete, the implementation is professionally integrated, and the only test failures in the broad sweep are pre-existing Python 3.11 compatibility issues that predate C1 work by seven days.

## Audit results

### Code health

| audit | check | result |
|---|---|---|
| 1 | watchdog + scoring + integration tests | 89/89 pass |
| 2 | strategist tests (brain prompt change) | 44/44 pass |
| 6 | imports resolve, helper identity matches between scorer and strategist | one-source-of-truth confirmed |
| 7 | AST syntax check on every touched Python file | 5/5 OK |
| 10 | grep for TODO/FIXME/XXX/HACK in C1 diffs | none added |
| 11 | import discipline — helper defined once, imported twice | clean |
| 20 | helper purity — imports `math`/`dataclasses`/`typing` only | no I/O, no time, no random |

### Behaviour at runtime

| audit | check | result |
|---|---|---|
| 5 | config.toml flag value | `wd_brain_scoring_enforce = true` |
| 8 | boot sentinel fires on construction | `WD_SCORING_ENFORCE_ACTIVE | scoring_enabled=True enforce=True threshold=6.00` |
| 9 | enforce-mode close vote produces correct event sequence | 1 each of `WD_SCORING_PATH_REACHED`, `WD_SL_PCT_DIVERGENCE`, `WATCHDOG_CLOSE_SCORE_COMPUTED`, `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN`; 0 each of `WD_CLOSE_SCORE_LOG_ONLY`, `WATCHDOG_CLOSE_REJECTED`, `WATCHDOG_CLOSE_EXECUTED`, `WD_BRAIN_SCORE_FAIL` |
| 17 | helper resolves a realistic CRVUSDT-style divergence case | 29 percentage points (entry-budget 29% vs current-stop 58%), bucket flip detected (spacious → comfortable, +1.0 composite shift — within the 04_sl_divergence.md +3.0 upper bound) |

### Git state

| audit | check | result |
|---|---|---|
| 3 | working tree | only runtime files modified (exempt per CLAUDE.md) |
| 4 | unpushed commits | 0 |
| 18 | per-commit isolation | 7 atomic C1 commits, sizes 1 to 435 lines, each independently revertible |
| 19 | flag flip diff | single line, 1 insertion + 1 deletion |

### Documentation

| audit | check | result |
|---|---|---|
| 12 | config.toml comments still accurate | yes |
| 14 | Phase 1 dev_notes present | 8 documents (phase0 + 01..06 + 04b) |
| 15 | Phase 4 trial doc intentionally absent until operator writes it | confirmed |
| 16 | 16 hard rules from the prompt | all satisfied |

### Regression / cross-codebase impact

| audit | check | result |
|---|---|---|
| 13 | broad test sweep | 3399 passed, 12 skipped, 2 failed |
| 21 | 2 failures investigation | `test_j1_position_reconciler.py` `from datetime import UTC` — added 2026-05-14 commit `daf1384`, requires Python 3.11+, local is 3.10.12. Pre-existing, unrelated to C1. |
| 22 | C1-related modules only (watchdog + scoring + strategist + integration) | 66/66 pass with zero regressions |

## Cross-check against the prompt's 16 hard rules

Each rule, with the artefact that satisfies it:

| rule | status | evidence |
|---|---|---|
| 1 — investigation before activation | done | 8 dev_notes documents written before flip; Phase 2 AskUserQuestion gate |
| 2 — independently confirm issue | done | Phase 1.1 — 28 of 28 scored events correlated to DB closes (1 win, 27 losses, −$257.18) |
| 3 — understand scoring system completely | done | Phase 1.2 documents every factor / weight / threshold |
| 4 — investigate SL% divergence | done | Phase 1.4 theoretical bound + Phase 1.4b alignment + diagnostic |
| 5 — verify, do not assume | done | every claim has a file:line / DB query / log count |
| 6 — root cause, not symptom | done | scoring filters panic-closes via objective seven-factor composite, no band-aid |
| 7 — production-quality verification | done | boot sentinel + 6 integration tests + dev_notes |
| 8 — commit on main, atomic, labeled | done | 9 atomic commits, each c1-prefixed, on `main` |
| 9 — aim preservation | done | Phase 1.5 walks all five questions with explicit evidence |
| 10 — operator interaction protocol | done | h1/h2/h3 headings, no emoji, plain prose in all artefacts |
| 11 — do not break what works | done | 89/89 touched-module tests + 3399/3413 broader sweep pass; 2 failures pre-existing |
| 12 — staged activation with rollback | done | single-line revert; boot sentinel confirms mode |
| 13 — DB cascade absence | done | Phase 0 confirmed `BRAIN_FAILURE_CASCADE = 0` |
| 14 — recency-bias-aware | done | Phase 0 used most recent 2026-05-20 + live `workers.log` |
| 15 — trial behaviour specification | done | synthesis specifies expected event signatures |
| 16 — honest self-check after activation | done | synthesis includes Phase 4 trial criteria and rollback trigger |

## Integration quality

The work integrates cleanly with the existing project structure:

- **One source of truth for the SL% formula** — `compute_sl_consumption_pct` in `src/risk/wd_brain_scoring.py`. Imported by `src/workers/position_watchdog.py` (line 45) and `src/brain/strategist.py` (line 29). Verified at runtime that both imports resolve to the same function object (audit 6).
- **No duplication** — the watchdog's `_calculate_sl_proximity` is now a thin wrapper over the helper; the brain prompt calls the helper twice with the two different SL inputs. The four other call sites of `_calculate_sl_proximity` (lines 795, 2525, 2730, 2832) are unchanged and continue to work via the wrapper.
- **No new dependencies** — the helper uses only `math`, `dataclasses`, `typing`. No I/O, no time, no random.
- **No public API breakage** — the existing module exports `BrainCloseScore`, `BrainCloseScoreFactors`, `DEFAULT_THRESHOLD`, `DEFAULT_WEIGHTS`, `STRUCTURAL_KEYWORDS`, `compute_brain_close_score`. The C1 work adds one more export (`compute_sl_consumption_pct`) without touching the existing six.
- **Settings layer untouched** — the existing `WatchdogSettings` fields `wd_brain_scoring_*` were already present and wired. C1 did not touch `src/config/settings.py`.
- **No new modules** — every change goes into pre-existing files at the natural location for that concern.
- **Test layer additive** — one new test file `tests/test_wd_scoring_enforce_integration.py` (435 LOC, 6 tests), plus 18 new tests appended to `tests/test_wd_brain_scoring.py`. No existing tests modified.

## Naming conventions verified

- Log events: ALL_CAPS_WITH_UNDERSCORES (existing pattern). New events: `WD_SCORING_ENFORCE_ACTIVE`, `WD_SL_PCT_DIVERGENCE`, `WD_SL_PCT_DIVERGENCE_FAIL`. Match the surrounding convention.
- Settings fields: `snake_case` with `wd_brain_scoring_*` prefix. Match existing.
- Helper function: `compute_sl_consumption_pct` — verb-noun snake_case matching `compute_brain_close_score` already in the same module.
- Test functions: `test_<descriptive_snake_case>` matching existing patterns. All 24 new test functions follow the convention.
- Dev-notes documents: numbered `NN_topic.md` and `NNb_topic.md` matching the existing structure under `dev_notes/<feature>/`.
- Commit messages: `c1[(scope)]: imperative summary` matching the existing `<scope>: <subject>` style (e.g., the prior `issue1/p3-1: ...`, `feat(wd-scoring): ...`).

## What ships under enforce mode

When the operator restarts workers, the new behaviour is:

1. **Boot**: `WD_SCORING_ENFORCE_ACTIVE | scoring_enabled=True enforce=True threshold=6.00`.
2. **Per brain close vote**: produces `WD_SCORING_PATH_REACHED`, `BRAIN_CLOSE_VOTE_RECEIVED`, `WD_SL_PCT_DIVERGENCE`, `WATCHDOG_CLOSE_SCORE_COMPUTED`, and exactly one of `WATCHDOG_CLOSE_EXECUTED` / `WATCHDOG_CLOSE_REJECTED` / `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN`.
3. **CALL_B prompt**: dual SL% rendering when the SL has been trailed (`SL consumed: A% (entry-budget) / B% (current-stop)`); single value when SL has not been trailed.
4. **Reject branch**: brain close blocked; position held. Passive paths (`wd_dl_action`, trail SL) take over.
5. **Reject-and-tighten branch**: brain close blocked; SL pulled 30% toward break-even via `_push_sl_to_shadow(source="wd_brain_scoring")` with full tighter-only safety.
6. **Execute branch**: brain close fires unchanged.

## Pre-existing test failures (not blocking)

| test | failure | cause |
|---|---|---|
| `test_j1_position_reconciler.py::test_emits_per_tick_info_line_with_count_diff` | ImportError | `from datetime import UTC` requires Python 3.11+; local is 3.10.12 |
| `test_j1_position_reconciler.py::test_streak_resets_when_drift_clears` | ImportError | same as above |

Both were introduced on 2026-05-14 (commit `daf1384`), seven days before C1 work began. Neither references any module touched by C1 (`wd_brain_scoring`, `position_watchdog._calculate_sl_proximity`, `strategist._build_position_prompt`). They would fail with or without C1. Fix scope: Python compatibility, separate from C1.

The other previously-known pre-existing failures (per `project_five_issues_fix_status` memory: `test_apex_direction_lock`, two `test_bybit_demo/test_websocket_subscriber`, `test_positions_exchange_mode`) are also out of C1 scope.

## Final state

- `git status --short`: only `data/layer_state.json` and `data/logs/layer1c_full.jsonl` modified (runtime auto-updates, exempt).
- `git log origin/main..main --oneline`: empty.
- `git branch --no-merged main`: empty.
- HEAD: `3bfb5e4 c1: activate wd_brain_scoring_enforce (operator-approved 2026-05-21)`.
- Config: `wd_brain_scoring_enforce = true`.
- All 9 C1 commits pushed to `origin/main`.

The activation is verified live and integrated. Phase 4 trial window begins on the next operator-initiated worker restart.
