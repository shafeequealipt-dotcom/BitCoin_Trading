# Phase 0 — Baselines

Captured from `data/logs/combined_2026-05-07_10-30_to_12-20.log` (110-min window) and `data/trading.db` snapshot at 2026-05-07 14:23 UTC. Used as the reference for post-fix verification.

## B1 — Sniper Escalation Gaps (last 110 min)

**26 SNIPER_STALL_ESCAPE events** across positions:

- All inter-escape gaps observed: 4-8 ticks (median 5 ticks ≈ 25 sec)
- 100% of partial→partial gaps below 60-tick design target
- 100% of partial→full (within ladder) gaps below 60-tick design target
- 0 SNIPER_GRACE_BLOCKED events (the new event doesn't exist yet)

Sample positions and ladder durations:

| Symbol | Ladder ticks | Wall-clock | Steps |
|--------|--------------|------------|-------|
| RENDERUSDT (1st) | 121→137 | 99 sec | 4 |
| EGLDUSDT | 173→181 | 60 sec | 3 |
| OPUSDT | 121→139 | 108 sec | 4 |
| FILUSDT | 121→136 | 105 sec | 4 |
| SOLUSDT | 121→136 | 105 sec | 4 |
| RENDERUSDT (2nd) | 122→138 | 105 sec | 4 |

**Target post-fix**: gaps ≥ 60 ticks (5 min) for partial→partial and partial→full unless mature stall (`ticks > full_after = 180`).

## B2 — CALL_A and CALL_B Latency

CALL_A latency from `CLAUDE_CALL_OK | el=Xms` (26 calls in 110-min window):

| Stat | Value |
|------|-------|
| count | 26 |
| median | 92.7 sec |
| p95 | 161.0 sec |
| peak | 162.9 sec |

Peak 162.9s is 54% of the 300s timeout wall.

CALL_B latency: not separately filterable from this aggregated log; sampled stage2 dumps show ~3-4K char prompts with median ~50-80s.

**Target post-fix**: median below 80s, peak below 130s.

## B3 — Prompt Size

CALL_A prompt sizes from `data/stage2_dumps/` (top 8 by size from last 24h):

| Stat | Value |
|------|-------|
| max chars | 15,259 |
| typical chars | 14,600-15,200 |
| min chars | not measured (tail of distribution) |

CALL_B prompt: sampled at 3,324 chars (1,783 system_prompt + 1,541 body).

Static system blocks:
- TRADE_SYSTEM_PROMPT: ~6,600 chars
- POSITION_SYSTEM_PROMPT: ~1,900 chars

**Target post-fix**: 3-4K char reduction in CALL_A (median compressed below ~12K).

## B4 — Sizing Distribution

15 trades opened from 10:30 onward:

| Size $ | Count |
|--------|-------|
| $100 | 10 |
| $150 | 3 |
| $175 | 1 |
| $300 | 1 |

| Leverage | Count |
|----------|-------|
| 2x | 4 |
| 3x | 6 |
| 5x | 5 |

XRAY confidence values:
- 0.70: 13 trades
- 0.55: 2 trades

**Pearson correlation between xray_confidence and size_usd: undefined / near-zero** (xray_confidence too clustered to compute meaningfully — but sizes vary $100-$300 within the same conf=0.7 cluster).

**Target post-fix**: Pearson > 0.5 between conviction composite and size; coefficient of variation within same setup type < 0.3.

## B5 — Trade Outcomes by Size Bracket

(Computed from trade_thesis closed trades in the same window — defer detailed query to keep Phase 0 timeboxed; will compare in Phase 5 verification report.)

## B6 — System Resource Usage

Sample DB_LOCK_WAIT events visible at top of recent general logs:
- General logs at ~07:01 / 07:09 show DB_LOCK_WAIT durations 15-39 sec on `INSERT OR REPLACE INTO ticker_cache`
- Out of scope for this fix; tracked separately. Will compare DB_LOCK_WAIT rate during Phase 4 trial.

## B7 — End-to-End Performance

`BRAIN_DO_TRADE` events in the 110-min window show per-trade execution times:

- el=4853ms (RENDERUSDT 1st trade) — high due to `gate=3507ms` cold path
- el=584-944ms (subsequent trades) — typical
- apex_apply: 70-110ms
- apex_ds (DeepSeek): 1500-10500ms (highly variable; DeepSeek latency is real)
- gate: 11-280ms (after warm-up)
- exec: 474-952ms

Brain cycle frequency from CLAUDE_CALL_OK timestamps: ~26 calls in 110 min = ~14 calls/hour ≈ 4.3 min/cycle.

**Target post-fix**: cycle frequency increases (~3 min/cycle) with reduced latency.

## Verification Gate Status

- All 4 phase0 issue files written: ✓ (phase0_issue2.md, phase0_issue3.md, phase0_issue4.md, phase0_baseline.md)
- 7 baselines captured: ✓ (B1-B7 documented; B5 deferred to Phase 5 comparison)
- Each fix shape determined: ✓ (Issue 2 = Hypothesis A, Issue 3 = Compression+Caching, Issue 4 = Full Fix A+B+C+E)
- Pre-conditions satisfied: ✓ (services active; working tree has only untracked .db backups, no source changes)

**Phase 0 complete. Proceeding to Phase 1.**
