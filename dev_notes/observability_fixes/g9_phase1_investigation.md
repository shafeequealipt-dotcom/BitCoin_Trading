# G9 Phase 1 — Investigation: TIAS bridge closure visibility

## Headline finding (revised after deeper trace)

The initial mapping treated the audit's claim as a pure tag mismatch.
Deeper investigation reveals a more nuanced state:

- **Write side WORKS:** `TIAS_LESSON_BRIDGED` at
  `src/core/thesis_manager.py:456` fires 8/8 in the audited window
  matching 8 TIAS_SAVE events. Lessons ARE being written to DB via
  the bridge step.

- **Read side INTENTIONALLY DISABLED:** the active CALL_B prompt
  builder `_build_position_prompt` at `src/brain/strategist.py:3402`
  contains the sentinel `_tias_lessons_removed = True` (L3601) and a
  hardcoded `recency_lessons_count=0` in `STRAT_CALL_B_CTX`. Per the
  in-file comment (L3590-3600), per-trade TIAS lessons were
  intentionally removed from CALL_B as a closed-loop-immunity measure
  — a recent loss on symbol X was directly biasing the next cycle's
  decision against X.

- **The STRAT_CALL_B_LESSONS_INJECTED at L1414** lives in
  `_build_context_prompt`, which is called only from the legacy
  `create_strategic_plan` (NOT from the active `create_position_plan`).
  This is dead code for the production CALL_B path — confirmed by
  Phase 0 log: `STRAT_CALL_B_LESSONS_INJECTED` fires zero times in the
  audited window.

## What G9 fixes

The right observability work is to make the **disabled-by-design**
state visible alongside the actual **DB-side lesson availability**.
Currently, operators can only see `recency_lessons_count=0` and the
`tias_coaching_removed=True` sentinel — but cannot tell whether the
DB has zero lessons or hundreds of lessons that are being ignored.

G9 adds a single field — `lessons_in_db=N` — to STRAT_CALL_B_CTX so
the read-side state is fully transparent:

```
STRAT_CALL_B_CTX | positions=3 chars=4821 el=42ms
  tias_coaching_removed=True recency_lessons_count=0 lessons_in_db=12 | ...
```

Reading: "TIAS has written 12 lessons; CALL_B intentionally injects 0
of them." The mismatch is now grep-detectable; the audit's concern is
addressed by visibility, not by re-enabling injection (which would
undo the closed-loop-immunity fix).

## Behaviour preserved

- `_build_position_prompt` semantics unchanged (still builds the same
  prompt structure)
- Lessons are NOT re-injected into CALL_B (the design decision stands)
- `recency_lessons_count=0` hardcoded remains the regression sentinel
- The thesis_mgr query is best-effort wrapped in try/except — if the
  DB query fails, `lessons_in_db` defaults to 0 (no crash, no behavior
  change)
- All existing CALL_B tests pass unchanged

## Pairing diagram (full closure visibility)

```
  TRADE CLOSE
    └─ thesis_manager.close_thesis()
        └─ THESIS_CLOSE                     (close-side event)
        └─ TIAS_SAVE                        (write to trade_intelligence)
        └─ compose_lesson_from_tias()
        └─ TIAS_LESSON_BRIDGED              (write to trade_thesis.lesson)
                                            ✓ verified 8/8 in window

  CALL_B CYCLE
    └─ _build_position_prompt()
        └─ STRAT_CALL_B_CTX                 (NEW: + lessons_in_db=N)
            tias_coaching_removed=True      (existing sentinel)
            recency_lessons_count=0         (hardcoded — design choice)
            lessons_in_db=N                 (G9 — actual DB count)
```

## Tests

`tests/test_callb_lessons_injected_fields.py` (3 cases):
- `lessons_in_db=0` when DB returns no lessons
- `lessons_in_db=N` when DB returns N lessons; recency_lessons_count
  remains 0 (showing the disabled-by-design state)
- `lessons_in_db=0` graceful fallback when get_recent_lessons raises

## Phase 4 verification

After deploy, the new field reveals the actual learning-loop state:

  TIAS_LESSON_BRIDGED count over 1h × 24h ≈ lessons written
  STRAT_CALL_B_CTX → lessons_in_db over the same window ≈ snapshot
                                                          at each CALL_B

If `lessons_in_db` is non-zero while `recency_lessons_count=0`, the
gate is functioning as designed. If `lessons_in_db=0` while
TIAS_LESSON_BRIDGED is firing, there's a thesis-manager filter
problem worth investigating as G12+.

## Cluster F follow-ups (deferred)

- `TIAS_LESSON_SCORE` / `TIAS_LESSON_EXPIRE` from Cluster F do not
  exist in src/. If TIAS adopts a scoring/expiry pipeline later,
  these become candidates.
- The dead-code STRAT_CALL_B_LESSONS_INJECTED at L1414 in
  `_build_context_prompt` could be removed for cleanliness, but
  removing it is a separate refactor (Rule: do not delete unused code
  in observability-only commits).
