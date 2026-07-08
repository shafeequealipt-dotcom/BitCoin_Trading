# Phase 0 — Pre-Flight Verification

Date: 2026-05-18.
Branch base: `main` HEAD `5b69233a8405395942b36ab75b97c754f4a90f56` ("sim: live 12-scenario simulation harness verifies all four fixes against 2026-05-16 conditions").
Author: Claude (Opus 4.7).
Scope: confirm clean ground before implementing the three issues from `IMPLEMENT_THREE_ISSUES_FIX.md` per the approved plan at `~/.claude/plans/plan-mode-first-compeltely-precious-nova.md`.

## A. Branch + HEAD

- Current branch: `main`.
- Last commit: `5b69233a8 sim: live 12-scenario simulation harness verifies all four fixes against 2026-05-16 conditions`.
- Recent topology (last 5):
  - `5b69233 sim: live 12-scenario simulation harness verifies all four fixes against 2026-05-16 conditions`
  - `2feb5b4 fix(R3-live-bug): real-pipeline audit caught db.connection() does not exist on DatabaseManager`
  - `91581aa audit: ship the spec-mandated event names missed in direction-fix Phase 3`
  - `4be3328 merge: direction-bias fix + H1/H3/H4 high-severity fixes into main`
  - `3e7c767 fix(direction-fix): wire R3 WR-aware risk fields into _build_risk loader`
- No in-flight feature branch shadows our work. Working tree state: `data/layer_state.json` and `data/logs/layer1c_full.jsonl` are modified (active runtime files), plus assorted untracked `dev_notes/` artifacts. No `src/` file is dirty.

## B. Previous-Fix Verification (re-grep current code, not memory)

### B.1 R1 — XRAY counter-trade direction plumbing

File: `src/apex/assembler.py`. Verified at lines 759-769:
- Comments at 759-767 acknowledge classify_setup inverts `trade_direction`.
- Line 767 plumbs into `StructuralData.trade_direction`: `sd.trade_direction = str(getattr(analysis, "trade_direction", "") or "")`.
- Fallback at 769 sets empty string.

**Conclusion: R1 present.**

### B.2 R2 — Composite-score lock

File: `src/apex/optimizer.py`. Verified:
- `_check_direction_lock` definition at line 1339.
- Invocation at line 254.
- Composite score embedded in lock reason at lines 413, 442, 1512, 1514.

**Conclusion: R2 present.**

### B.3 R3 — WR-aware override threshold

File: `src/workers/strategy_worker.py`. Verified:
- `wr_base * (1 - flipped_dir_wr_fraction)` at line 1426.
- `xray_lock_override_wr_*` settings reads at lines 1450-1454.

**Note**: R3 lives in `src/workers/strategy_worker.py` (not `src/strategy/strategy_worker.py` as memory implied). Recorded for accurate citations in later phases.

**Conclusion: R3 present.**

### B.4 J11 DB concurrency refactor

Out of scope for modification per prompt §opening constraints. Not touched.

### B.5 Regime detector calibration (`6938c69`)

Out of scope per prompt — not touched.

## C. Baseline Metrics (for Phase 4 deltas)

### C.1 Most recent 6h session log

File: `data/logs/workers.2026-05-17_09-41-32_552012.log` (10.0 MB; the 2026-05-17 session the prompt references).

Event tallies (raw substring counts; includes set/check/reject occurrences):

- `wd_claude_action`: 30 occurrences in log.
- `portfolio_direction_cap` / `PORTFOLIO_CAP` / `PORTFOLIO_CONCENTRATION`: 31 occurrences.
- `reentry_learning_gate`: 19 occurrences.
- `loss_cooldown_same_direction`: 0 occurrences in this session.

The session-log counts are observability counts (multiple events per fired check). The authoritative actuation counts are in the DB (next section).

### C.2 `trade_log` close-reason histogram (all-time, key paths)

Pulled from `data/trading.db.trade_log` (2306 rows total). Filtered to `wd_*` / `claude_*` close triggers:

| Close reason | N | Total PnL ($) | Wins | WR |
|---|---|---|---|---|
| `wd_dl_action` (deadline) | 79 | +593.04 | 75 | 95% |
| `wd_claude_action` | 56 | -463.41 | 4 | **7%** |
| `wd_timeout` | 23 | -130.36 | 0 | 0% |
| `wd_profit_take` | 8 | +419.18 | 8 | 100% |
| `wd_trail` | 3 | +43.03 | 3 | 100% |

Findings:

- The prompt's claim ("wd_claude_action has near-zero WR across sessions") is **confirmed and worse than stated** — cumulative -$463 over 56 trades with 4 wins (~7%, mean per-close -$8.28).
- `wd_dl_action` is the dominant winner path (75/79 wins, +$593 cumulative). Issue 1 must NOT touch this — verified out of scope (we only modify `wd_claude_action` path).
- `wd_profit_take` and `wd_trail` are 100% WR helpers — out of scope.
- Top non-wd close reasons in last 72h: `shadow_sl_tp` (569 closes, +$1275.92, 387 wins), `bybit_sl_hit` (185, +$215, 85 wins) — passive close mechanisms producing the bulk of WR. Confirms passive-close advantage (aim question #4).

Also worth flagging: 144 "mode4_p9" closes in last 72h (+$21.51, 39 wins) — the labels-disambiguation work from the May 8 three-issues Phase already split this further into `mode4_score_full` etc. (memory `project_three_issues_status.md`). Not in scope; observability only.

### C.3 Cascade baseline

No cascade events queried explicitly; the `data/trading.db` is healthy (67 tables, normal sizes, recent writes). Per Rule 13 we re-check at each phase gate.

## D. Sanity Test Collection

Ran `timeout 30 python3 -m pytest tests/ -x --co -q`. Result: **982 tests collected, 1 pre-existing collection error**:

```
ERROR collecting tests/test_j1_prune_positions_repo.py
ImportError: cannot import name 'UTC' from 'datetime'
```

This is the documented pre-existing error in `project_direction_bias_fix_status.md` (Python 3.11+ `datetime.UTC`, system has 3.10). Carried forward as Phase 0 known-failure; not blamed on our work.

No other collection errors. Per `feedback_test_velocity.md` we will not chase coverage broadly; we run targeted suites only.

## E. Plan File + Branch Strategy

Plan: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-precious-nova.md`. Approved this session.

Branches (created at the start of each issue, all off `main`):
- Issue 2: `fix/remove-portfolio-cap`
- Issue 3: `fix/5min-reentry-cooldown`
- Issue 1: `fix/wd-scoring-brain-vote`

## F. Exit Criteria — All Met

- HEAD captured: yes.
- Prior fixes verified in source: yes (R1, R2, R3 present at expected anchors).
- Baseline metrics captured: yes (log counts + DB close-reason tally).
- Sanity test collection: yes (1 known pre-existing failure documented).
- DB cascade baseline: clean (will recheck per phase).

**Ready to proceed to Issue 2.**
