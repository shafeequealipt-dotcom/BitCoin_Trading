# Phase 5 — Integrated Verification (Template + Operator Checklist)

This is the template the operator follows AFTER all three issues are
deployed and each individual Phase 4 verification has passed. Live
data is captured here when the operator runs the post-deploy session.

## A. Branch + Commit Topology

All three branches are stacked off `main` HEAD `5b69233a8`:

```
main 5b69233a8
└── fix/remove-portfolio-cap (5 commits, b63db34..bcaae05)
    └── fix/5min-reentry-cooldown (6 commits, 9a156c7..497378c)
        └── fix/wd-scoring-brain-vote (4 commits, 21d772e..b77298a)
```

Issue 2 (cap removal) and Issue 3 (5-min cooldown) modify shared
files (`src/apex/gate.py`, `src/core/trade_coordinator.py`,
`src/config/settings.py`), hence the stacked layout. Operator merge
order: Issue 2 → Issue 3 → Issue 1 (left to right above).

15 atomic commits total. Per-commit log lives in the git history;
the per-issue summary lives in `dev_notes/three_issues_fix/issue{2,3,1}_phase{1,2}.md`.

## B. Pre-Deploy Baseline (from Phase 0)

Reference: `phase0_preflight.md`. Last 6h session (`workers.2026-05-17_09-41-32_552012.log`, 10 MB):
- `wd_claude_action` substring count: 30 in log.
- `portfolio_direction_cap` substring count: 31 in log.
- `reentry_learning_gate` substring count: 19 in log.

All-time `trade_log` table (DB):
- `wd_claude_action`: 56 closes, -$463.41 cumulative, 4 wins (7% WR), mean -$8.28/close.
- `wd_dl_action` (deadline): 79 closes, +$593.04, 75 wins (95% WR).
- `wd_profit_take`: 8 closes, +$419.18, 100% WR.
- `wd_trail`: 3 closes, +$43.03, 100% WR.

## C. Post-Deploy Live Verification Commands

Run these against the operator's post-deploy session log (find the
newest `workers.<timestamp>.log` in `data/logs/`):

### C.1 Issue 2 — portfolio cap absence

```bash
SESSION_LOG=data/logs/workers.<NEW_TIMESTAMP>.log
grep -c "portfolio_direction_cap\|PORTFOLIO_CAP\|PORTFOLIO_CONCENTRATION\|PORTFOLIO_DIRECTION_PERMITTED\|GATE_PORTFOLIO_DIR_CHECK" "$SESSION_LOG"
# Expected: 0
grep -rn "portfolio_direction_cap\|get_direction_counts\|CHECK_15" src/ tests/ scripts/
# Expected: 0 hits in production code; dev_notes/direction_fix/agent_gamma/ keeps the historical design docs.
```

### C.2 Issue 3 — 5-min cooldown active, legacy gone

```bash
grep -c "reentry_learning_gate\|REENTRY_REGIME_DRIFT_CHECK\|GATE_RECALIBRATION_ALLOW\|REENTRY_LEARNING_GATE" "$SESSION_LOG"
# Expected: 0
grep -c "loss_cooldown_same_direction\|COORD_LOSS_COOLDOWN_SET" "$SESSION_LOG"
# Expected: 0
grep -c "REENTRY_COOLDOWN_5MIN_SET" "$SESSION_LOG"
# Expected: > 0 (one per close)
grep -c "REENTRY_COOLDOWN_5MIN_BLOCKED" "$SESSION_LOG"
# Expected: > 0 if any brain proposals fall inside the 5-min window
grep -c "REENTRY_COOLDOWN_5MIN_CLEARED" "$SESSION_LOG"
# Expected: > 0 as windows expire
```

Time-edge cross-check (paste any blocked event into a Python script):

```python
import re
events = []
for line in open("$SESSION_LOG"):
    m = re.search(r"REENTRY_COOLDOWN_5MIN_(SET|BLOCKED|CLEARED).*sym=(\S+).*dir=(\S+)", line)
    if m:
        events.append((line.split()[0], m.group(1), m.group(2), m.group(3)))
# For each BLOCKED, find the matching SET timestamps and confirm delta < 300s.
```

Per-direction independence — find a session example:

```bash
grep -E "REENTRY_COOLDOWN_5MIN_SET.*sym=AVAXUSDT.*dir=Sell" "$SESSION_LOG"
grep -E "GATE_REJECT.*sym=AVAXUSDT" "$SESSION_LOG"  # Should NOT show Buy rejected because Sell is on cooldown
```

### C.3 Issue 1 — Phase 1 log-only data review

```bash
grep -c "BRAIN_CLOSE_VOTE_RECEIVED" "$SESSION_LOG"
# Expected: matches the count of brain close votes (one per drain_strategic_actions close action)
grep -c "WATCHDOG_CLOSE_SCORE_COMPUTED" "$SESSION_LOG"
# Expected: same as above
grep -c "WD_CLOSE_SCORE_LOG_ONLY" "$SESSION_LOG"
# Expected: > 0 while enforce flag is False
```

Per-event distribution of composite scores:

```bash
grep "WATCHDOG_CLOSE_SCORE_COMPUTED" "$SESSION_LOG" | sed -nE 's/.*composite=([-0-9.]+).*/\1/p' | sort -n | uniq -c
```

Pairing scores with actual post-close PnL (manual review per close):

For each `WATCHDOG_CLOSE_SCORE_COMPUTED` event:
1. Note the symbol + composite + recommendation.
2. Find the matching `STRAT_ACTION_CLOSE` (immediately after).
3. Query `trade_log` for the close PnL.
4. Was the close "right" (won) or "wrong" (lost)?
5. Would the scoring have prevented the wrong closes without blocking the right ones?

Acceptance threshold for Phase 2 enforce flip: ≥80% of closes that
would have been REJECTED were losers in the actual trade_log, AND
≥80% of closes that would have been EXECUTED (composite ≥ +6)
were winners.

### C.4 Phase 2 (enforce) verification — after operator flips flag

```bash
grep -c "WATCHDOG_CLOSE_EXECUTED" "$SESSION_LOG_POST_FLIP"
# Expected: drops sharply from baseline ~30/6h (only high-conviction votes fire)
grep -c "WATCHDOG_CLOSE_REJECTED" "$SESSION_LOG_POST_FLIP"
# Expected: > 0 (most sub-threshold votes blocked)
grep -c "WATCHDOG_CLOSE_OVERRIDE_TIGHTEN" "$SESSION_LOG_POST_FLIP"
# Expected: > 0 (composite < 0 cases tighten SL)
```

DB query — verify `wd_claude_action` losses drop:

```bash
sqlite3 data/trading.db "
SELECT COUNT(*), ROUND(SUM(pnl_usd),2), SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END)
FROM trade_log
WHERE close_reason='wd_claude_action' AND closed_at > strftime('%s','now','-24 hour')
"
# Expected post-enforce: << 10 events, total PnL much closer to zero or positive
```

## D. Cross-Issue Trade Frequency Check (aim preservation)

The combined effect of all three fixes should be MORE trades, not
fewer. Verify session trade volume rose:

```bash
# Count successful entries (after gate) in pre-fix vs post-fix sessions
sqlite3 data/trading.db "
SELECT DATE(opened_at, 'unixepoch') AS day,
       COUNT(*) AS opens,
       ROUND(SUM(pnl_usd), 2) AS day_pnl
FROM trade_log
WHERE opened_at > strftime('%s','now','-7 day')
GROUP BY day
ORDER BY day DESC
"
```

If trade frequency DROPS after deploy: investigate before declaring
done. Possible causes:
- The reentry cooldown is firing more aggressively than intended (check `REENTRY_COOLDOWN_5MIN_BLOCKED` count).
- Scoring in enforce mode is suppressing legitimate closes that then get re-entered.
- A regression elsewhere in the gate validation chain.

## E. Shadow Path Smoke Check (Rule 10)

```bash
# Boot the workers process locally with Shadow mode enabled.
# Confirm Shadow demo trade end-to-end produces an entry + close cycle.
# No new exceptions referencing removed code paths.
```

## F. DB Cascade Recheck (Rule 13)

```bash
# Re-confirm zero cascade events in the post-deploy session.
sqlite3 data/trading.db "
SELECT close_reason, COUNT(*), ROUND(SUM(pnl_usd), 2)
FROM trade_log
WHERE closed_at > strftime('%s','now','-24 hour')
  AND close_reason LIKE '%cascade%'
"
# Expected: 0 rows
```

## G. Sign-Off Checklist

Operator marks each line as the verification clears:

- [ ] Issue 2: portfolio_direction_cap_* events absent in 24h+ post-deploy log.
- [ ] Issue 2: src/ grep for removed names returns zero hits.
- [ ] Issue 3: reentry_learning_gate_* events absent in post-deploy log.
- [ ] Issue 3: loss_cooldown_same_direction_* events absent.
- [ ] Issue 3: REENTRY_COOLDOWN_5MIN_BLOCKED fires within the 5-min window.
- [ ] Issue 3: REENTRY_COOLDOWN_5MIN_CLEARED fires after expiry.
- [ ] Issue 3: per-direction independence confirmed (closing Sell does not block Buy).
- [ ] Issue 1 Phase 1: log-only data reviewed; ≥80% predictive accuracy (operator-judged).
- [ ] Issue 1 Phase 2: enforce flipped; wd_claude_action losses drop near zero.
- [ ] Trade frequency stable or up (no aim regression).
- [ ] Shadow path still works (smoke check).
- [ ] DB cascades: zero.

When every line is checked: the three-issue fix series is complete.
Pull these numbers into a final memory note (auto-memory entry) so
future sessions can reference the deployment outcome.
