# Issue 6 — SCANNER_FILTER_RESULT logging is DEBUG-only (no per-cycle aggregate)

**Status:** PRESENT — observability-only.
**Tier:** 3 (operator UX).
**Source observation:** `dev_notes/layer1_live_monitor_2026-04-27.md` lines 145-156 (Finding #3).

## A. Mechanism

`ScannerWorker._qualifies` at `src/workers/scanner_worker.py:485-589` evaluates 5 criteria per coin (XRAY setup, ensemble consensus, regime alignment, RR ratio, blockers). On each per-coin pass/fail it emits:

```python
log.debug(f"SCANNER_FILTER_RESULT | sym={coin} qualified=false ...")   # line 665
log.debug(f"SCANNER_FILTER_RESULT | sym={coin} qualified={qualified} ...")  # line 672
```

DEBUG level — invisible at default INFO log level. The cycle-level aggregate today is `SCANNER_SELECT | qualified={count} selected={len} forced={count} watch_list=50` at line 845 (INFO) — useful but doesn't break down which criteria failed. When `qualified=0` the operator can only enumerate per-coin DEBUG by raising log level temporarily.

The 5 criteria (verified at scanner_worker.py):
1. `no_xray_analysis | no_xray_setup_type` (line 524) — XRAY setup type detected
2. `consensus=LABEL` not in {STRONG, GOOD} (line 539) — ensemble consensus pass
3. `regime={label}_vs_{direction}` (line 561) — regime alignment with direction
4. `rr={value}_below_{threshold}` (line 576) — RR ratio ≥ min threshold
5. `blockers=funding_against,manipulation,recent_loss` (line 581-587) — no blockers

## B. Dependencies

- `_qualifies` consumers: just the cycle-level `tick` flow.
- Per-coin DEBUG output is referenced by zero downstream code (greppable forensics only).
- `/health` Telegram handler exists somewhere in `src/telegram/handlers/` — investigation will locate.
- `SCANNER_TICK_SUMMARY` (line 855) already emits cycle-level metrics; we're additive.

## C. Constraints

- Must NOT remove the per-coin DEBUG (operators rely on it for forensic dives).
- Must NOT raise the per-coin events to INFO — 50 lines/cycle * 12 cycles/hour = 600 lines/hour just for scanner. Noise.
- The aggregate event must add ZERO additional database calls — counters live in the existing `_qualifies` loop.
- `/health` Telegram surface is read-only and must not slow down `tick`.

## D. Fix candidates

1. **Counter dict accumulated inside `_qualifies` loop, single INFO line at cycle end (chosen).**
   - In `_qualifies`, increment one of `fail_no_xray | fail_setup_none | fail_consensus | fail_regime | fail_rr | fail_blockers | pass_xray | pass_consensus_strong | pass_consensus_good`.
   - At cycle end (after `_qualifies` for-loop completes), emit one INFO line `SCANNER_FILTER_AGGREGATE | cycle_id={id} total=50 qualified={q} forced={f} fail_no_xray=... fail_setup_none=... ...`.
   - Add `/health` Telegram surface reads from a small in-memory ring buffer (last 10 cycles).
2. Promote per-coin to INFO. Rejected — 600 lines/hour noise for low signal; operators already have the per-cycle qualified count.
3. Database table for per-coin filter audit. Rejected — premature; cycle-level INFO suffices.
4. Telegram alert when qualified=0 for N cycles. Rejected — operator alert noise; `/health` is sufficient operator-pull.

## E. Observability gap

- No greppable single line per cycle telling the whole filter story. Operators infer fail mix from the `XRAY_BLOCK` events at execution time (which only fire for *selected* coins, missing the filter-failure majority).
- Adding `SCANNER_FILTER_AGGREGATE` makes "qualified=0" cycles immediately diagnosable: operator sees `fail_consensus=42 fail_no_xray=5 fail_blockers=3` and knows the consensus cache is starving (Issue 4) before reading any other log.

## F. Verification approach

- Snapshot test: synthesize 50 coins through `_qualifies` with predetermined fail reasons, assert aggregate counts match.
- Live trial: 30-min window post-deploy → 6 `SCANNER_FILTER_AGGREGATE` events; counts sum to 50 each cycle (`qualified + forced + sum(fail_*) ≈ 50`; small variance for forced-include logic at top).
- /health Telegram: run command, see "Scanner (last 10 cycles avg): Qualified: X/50 avg | Top fail reasons: consensus(N), no_xray(M)..."

## G. Rollback path

Single-commit revert of `src/workers/scanner_worker.py` (and the `/health` handler change). No DB migration, no state mutation. The aggregate is in-memory only. Rollback time: < 1 minute.
