# Issue 2 — Phase 1 — Cap Removal Scope (Re-Verified)

Date: 2026-05-18. HEAD: `5b69233a8`. Source of design: `IMPLEMENT_THREE_ISSUES_FIX.md` Issue 2 (lines 218-265).

## A. CHECK 15 Block — Exact Boundaries

File: `src/apex/gate.py`.

- Block start: line 649 — `# ═══ CHECK 15: Portfolio Direction Concentration Cap (R4) ═══`.
- Block content: lines 649-842.
- Block end: line 842 — close of the `except` defensive logger (`GATE_PORTFOLIO_DIR_CHECK`).
- Preceding context: CHECK 14 ends at line 644 (TPSL_IDENTICAL adjust), with its own defensive except at 645-647.
- Following context: line 843 blank, line 844 `# ═══ Attach gate metadata and log ═══` — the gate footer that attaches `_gate_validation_ms` and emits the gate summary log.

Surgical cut: delete lines 649-842 inclusive. The blank line at 648 between CHECK 14's except-handler and CHECK 15's section header can stay or go — keeping it preserves visual separation between CHECK 14 footer and the gate-attach footer.

### All references inside CHECK 15

Settings getattr calls (lines 664, 668, 673, 680, 687) — all consumed only inside this block.

Log events (lines 700, 784, 794, 806, 818, 829, 840):
- `PORTFOLIO_CONCENTRATION_CHECK` (INFO, line 700) — skip when total < min_positions.
- `PORTFOLIO_CAP_XRAY_FAIL` (WARNING, line 784) — exception during XRAY viability check.
- `PORTFOLIO_CAP_HIT verdict=blocked_aim_conditional` (WARNING, line 794).
- `PORTFOLIO_CAP_HIT verdict=permitted_mono_trending` (WARNING, line 806).
- `PORTFOLIO_CAP_WARN` (INFO, line 818).
- `PORTFOLIO_DIRECTION_PERMITTED` (INFO, line 829).
- `GATE_PORTFOLIO_DIR_CHECK` (WARNING, line 840) — defensive exception.

Reject string: `_gate_rejected = f"portfolio_direction_cap_{new_dir_norm}_{int(post_pct*100)}pct_aim_conditional"` at line 789-790.

## B. Settings Fields + Docstring

File: `src/config/settings.py`.

- Docstring header at lines 2294-2308 (the R4 cap section comment).
- Fields:
  - Line 2309 `portfolio_direction_cap_enabled: bool = True`
  - Line 2310 `portfolio_direction_cap_pct: float = 0.70`
  - Line 2311 `portfolio_direction_cap_warn_pct: float = 0.60`
  - Line 2312 `portfolio_direction_cap_min_positions: int = 3`
  - Lines 2313-2321 in-field docstring for `opposite_ratio_threshold`
  - Line 2322 `portfolio_direction_cap_opposite_ratio_threshold: float = 2.0`
- Boundary check: line 2323 blank, line 2324 blank, line 2325 blank, line 2326 `@dataclass` for `SentinelSettings`. Clean cut: delete lines 2294-2322 inclusive.
- Field above (still in APEXSettings): `apex_lock_symbol_evidence_wr_floor_pct: float = 70.0` at line 2292 — keep.

## C. Coordinator Helper

File: `src/core/trade_coordinator.py`.

- Method: `get_direction_counts(self) -> dict[str, int]` lines 361-398.
- Above (line 359 returns `frozenset(self._trades.keys())` from `active_symbols`).
- Below (line 399 blank, then next method).
- Clean cut: delete lines 361-398. Leave one blank line above/below as separator.
- Exclusive consumer: CHECK 15 (verified `grep -rn "get_direction_counts" src/ tests/`). Only the audit script reference at `scripts/pipeline_audit_full_chain.py:176` outside production code.

## D. Test File

`tests/test_gamma_r4_portfolio_cap.py` — 297 lines, 12 test functions, all cap-specific. `git rm` entire file.

## E. Audit Scripts

- `scripts/pipeline_audit_R4_live.py` (7252 bytes) — purpose-built R4 audit. `git rm`.
- `scripts/pipeline_audit_full_chain.py:176` contains one line referencing `coord2.get_direction_counts()`. Confirmed during edit whether the file has standalone value (likely yes — it's a broader chain audit). Default action: delete the offending line; leave rest of file. Confirm during the commit.

## F. Documentation

- `CLAUDE.md`: grep found NO cap references. No update needed.
- `PROJECT_BIBLE.md`, `PROJECT_BLUEPRINT.md`, `README.md`: grep returned no hits.
- `dev_notes/direction_fix/agent_gamma/` design docs: historical record, preserve.

## G. Downstream Consumer Check (the safety question)

Grepped src/ for the six cap log event names. **Only producer is `src/apex/gate.py`.** No consumer in:
- watchdog
- telegram handlers
- dashboard / observability
- brain prompt
- sentinel
- risk modules

Field references for the 5 APEXSettings keys: only in `src/apex/gate.py` (the cap itself) and `scripts/pipeline_audit_R4_live.py` (the audit being deleted). No production consumer.

**Verdict: safe to remove. No `[BLOCKER]`.**

## H. Test Surface

Targeted test commands post-change:
- `timeout 30 python3 -m pytest tests/test_apex_gate*.py tests/test_apex/ -x -q` (gate / APEX regression).
- `timeout 30 python3 -m pytest tests/ -x -q --ignore=tests/test_j1_prune_positions_repo.py -q 2>&1 | tail -40` (overall regression with the documented pre-existing collection error excluded).

## I. Atomic Commit Plan (locked, ready to execute)

Branch: `fix/remove-portfolio-cap` from `main` `5b69233a8`.

1. `issue2/p3-1 fix(gate): remove CHECK 15 portfolio direction concentration cap` — gate.py lines 649-842 deleted.
2. `issue2/p3-2 fix(coordinator): remove get_direction_counts helper (cap-exclusive)` — trade_coordinator.py lines 361-398 deleted.
3. `issue2/p3-3 fix(config): remove portfolio_direction_cap settings fields` — settings.py lines 2294-2322 deleted.
4. `issue2/p3-4 test: remove cap-specific test file` — `git rm tests/test_gamma_r4_portfolio_cap.py`.
5. `issue2/p3-5 chore(scripts): remove R4 audit scripts` — `git rm scripts/pipeline_audit_R4_live.py`; remove the single cap line in `scripts/pipeline_audit_full_chain.py:176`.

No `issue2/p3-6` needed: CLAUDE.md grep returned nothing.
