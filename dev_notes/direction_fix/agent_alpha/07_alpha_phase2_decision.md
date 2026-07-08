# ALPHA Phase 2 — Operator Decision Record

## Decision

Operator approved: **Option E + Option D** combined.

## Scope of approved fix

- Plumb `trade_direction` field from `StructuralAnalysis` through `src/apex/assembler.py:737` into a new `StructuralData.trade_direction` field on `src/apex/models.py`.
- Add observability to XRAY_CLASSIFY_SUMMARY: `trade_dir_long=N trade_dir_short=N counter_count=N` plus a new `XRAY_DIRECTION_SPLIT` periodic line per tick.
- Cross-agent dependency: BETA's `_check_direction_lock` will consume the new field in the same Phase 3 window (BETA already aware via R2 decision).

## Branch

`fix/r1-xray-counter-inversion` (created off HEAD 7320266 at Phase 0).

## Sequencing note

ALPHA's plumbing change must land BEFORE BETA's consumer code so the optimizer has something to consume. DELTA's synthesis confirms.

## Reference deliverables

- `04_fix_options.md` — full Option E and D specifications
- `05_alpha_synthesis.md` — recommendation + trial behavior + verification queries
- `06_alpha_phase2_report.md` — operator-facing presentation
