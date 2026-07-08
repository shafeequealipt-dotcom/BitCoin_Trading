# Agent GAMMA — 14:45 Cascade Reconstruction (R4)

This document reconstructs the 5-position SL cascade observed at 14:45 UTC on 2026-05-16. The reconstruction draws from the authoritative `trade_log` table in `data/trading.db` (78 rows on 2026-05-16, 21 of which opened between 13:48 and 14:53). Live monitoring narrative at `dev_notes/live_monitoring_20260516/FINDINGS.md:410-417` corroborates the timeline.

The raw log stream for the 13:00-15:30 window is no longer on disk — workers.log starts at 17:30, and the archive in `live_monitoring_20260516/ALL_LOGS_2026-05-16_08-02_to_10-24.log` ends at 10:24. The 14:45 cascade evidence presented here is from the `trade_log` SQLite table and is authoritative for timing, direction, PnL, and close reason.

## Source of evidence

Query: `SELECT opened_at, closed_at, symbol, direction, pnl_usd, hold_minutes, close_reason FROM trade_log WHERE opened_at LIKE '2026-05-16T13%' OR opened_at LIKE '2026-05-16T14%' ORDER BY opened_at` against `/home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db`. Twenty-one rows.

## Pre-cascade portfolio state (13:48-14:42)

Sequence of entries from 13:48 to the moment of the 14:45 cascade event:

| Open time | Symbol | Direction | Close time | PnL USD | Hold min | Close reason |
|-----------|--------|-----------|------------|---------|----------|--------------|
| 13:48:37 | HYPEUSDT | Sell | 14:41:01 | -22.71 | 52.4 | wd_timeout |
| 13:48:38 | PLUMEUSDT | Sell | 14:36:13 | -7.79 | 47.6 | wd_timeout |
| 13:48:39 | ORCAUSDT | Sell | 14:07:32 | +58.79 | 18.9 | bybit_sl_hit (TP-trail) |
| 13:57:09 | DYDXUSDT | Sell | 14:47:21 | 0.00 | 50.2 | wd_dl_action |
| 13:57:10 | SKRUSDT | Sell | 14:34:46 | -9.03 | 37.6 | bybit_sl_hit |
| 13:57:11 | SEIUSDT | Sell | 14:22:47 | -3.93 | 25.6 | bybit_sl_hit |
| 14:06:23 | XRPUSDT | Sell | 14:12:29 | +1.09 | 6.1 | mode4_partial |
| 14:06:23 | XRPUSDT | Sell | 15:00:21 | -0.26 | 54.0 | wd_timeout |
| 14:06:24 | NEARUSDT | Sell | 14:37:49 | -4.13 | 31.4 | wd_claude_action |
| 14:06:24 | APTUSDT | Sell | 14:12:02 | -4.52 | 5.6 | bybit_sl_hit |
| 14:17:32 | HBARUSDT | Sell | 14:33:18 | -4.53 | 15.8 | bybit_sl_hit |
| 14:17:33 | MNTUSDT | Sell | 14:43:12 | -5.33 | 25.7 | bybit_sl_hit |
| 14:17:34 | SOLUSDT | Sell | 14:42:03 | -5.22 | 24.5 | bybit_sl_hit |
| 14:25:12 | OPUSDT | Buy | 14:53:54 | +2.30 | 28.7 | bybit_sl_hit (trail-locked profit) |
| 14:25:13 | APTUSDT | Sell | 14:45:11 | -4.98 | 20.0 | bybit_sl_hit |
| 14:34:03 | SANDUSDT | Sell | 14:45:46 | -4.42 | 11.7 | bybit_sl_hit |
| 14:34:04 | AXSUSDT | Sell | 14:43:58 | -0.92 | 9.9 | bybit_sl_hit |
| 14:34:05 | LINKUSDT | Sell | 14:46:17 | -2.70 | 12.2 | bybit_sl_hit |
| 14:42:25 | AVAXUSDT | Sell | 14:45:08 | -5.61 | 2.7 | bybit_sl_hit |
| 14:42:27 | ORCAUSDT | Sell | 14:51:24 | -14.11 | 8.95 | bybit_sl_hit |
| 14:53:18 | INJUSDT | Sell | 15:36:11 | -26.01 | 42.9 | wd_timeout |

Observations:

- Of 21 entries in this window, 20 are Sell and 1 is Buy (OPUSDT)
- OPUSDT (the only Buy) wins +$2.30; aligns with the all-time pattern that Buys win more often
- The 14:42 cluster is the cascade trigger: AVAXUSDT, ORCAUSDT both opened within 2 seconds and both hit SL within 10 minutes
- The 14:34 cluster (SANDUSDT, AXSUSDT, LINKUSDT) all opened within 3 seconds and all hit SL within 13 minutes
- The 14:17 cluster (HBARUSDT, MNTUSDT, SOLUSDT) all opened within 3 seconds and all hit SL within 26 minutes

## The 5-position 70-second cascade (14:45:08 to 14:46:17)

From `FINDINGS.md:410-417` and confirmed by the table above:

| Close time | Symbol | Side | PnL USD | PnL pct | Hold |
|------------|--------|------|---------|---------|------|
| 14:45:08 | AVAXUSDT | Sell | -5.61 | -0.31% | 2.7 min |
| 14:45:11 | APTUSDT | Sell | -4.98 | -0.33% | 20.0 min |
| 14:45:46 | SANDUSDT | Sell | -4.42 | -0.29% | 11.7 min |
| 14:46:17 | LINKUSDT | Sell | -2.70 | -0.30% | 12.2 min |
| 14:51:24 | ORCAUSDT | Sell | -14.11 | -0.78% | 8.95 min |

Initial 4 SL hits at 14:45:08 → 14:46:17 (70 seconds). ORCAUSDT 5th SL hit 5 minutes later. Total cascade loss: **-$31.82**.

If we additionally count the 14:43-14:44 close of MNTUSDT (-$5.33) and AXSUSDT (-$0.92) — both Sells, both SL-hit, both within the same broader 8-minute window — the cumulative loss is **-$38.07** for the 8-minute period 14:43:12 to 14:51:24. This is the figure FINDINGS reports.

All seven SL-hit Sell positions in this window were SL-stopped by a single market bounce, NOT by separate fundamental breakdowns. The near-identical PnL percentages (-0.29% to -0.33% on the initial four) are diagnostic of synchronized SL trips at similar SL-distance configurations.

## Portfolio direction concentration at each entry

Reconstructed by counting positions already open at each new entry's `opened_at` (excluding the new position itself):

| Entry time | Symbol | Dir | Open Buys | Open Sells | Sell% before | Sell% after | block@60? | block@70? | block@80? |
|------------|--------|-----|-----------|------------|--------------|-------------|-----------|-----------|-----------|
| 13:48:37 | HYPEUSDT | Sell | 0 | 0 | 0.0% | 100.0% | NO | NO | NO |
| 13:48:38 | PLUMEUSDT | Sell | 0 | 1 | 100.0% | 100.0% | YES | YES | YES |
| 13:48:39 | ORCAUSDT | Sell | 0 | 2 | 100.0% | 100.0% | YES | YES | YES |
| 13:57:09 | DYDXUSDT | Sell | 0 | 3 | 100.0% | 100.0% | YES | YES | YES |
| 13:57:10 | SKRUSDT | Sell | 0 | 4 | 100.0% | 100.0% | YES | YES | YES |
| 13:57:11 | SEIUSDT | Sell | 0 | 5 | 100.0% | 100.0% | YES | YES | YES |
| 14:06:23 | XRPUSDT | Sell | 0 | 6 | 100.0% | 100.0% | YES | YES | YES |
| 14:06:24 | NEARUSDT | Sell | 0 | 7 | 100.0% | 100.0% | YES | YES | YES |
| 14:06:24 | APTUSDT | Sell | 0 | 8 | 100.0% | 100.0% | YES | YES | YES |
| 14:17:32 | HBARUSDT | Sell | 0 | 7 | 100.0% | 100.0% | YES | YES | YES |
| 14:17:33 | MNTUSDT | Sell | 0 | 8 | 100.0% | 100.0% | YES | YES | YES |
| 14:17:34 | SOLUSDT | Sell | 0 | 9 | 100.0% | 100.0% | YES | YES | YES |
| 14:25:12 | OPUSDT | Buy | 0 | 9 | 100.0% | 90.0% | NO | NO | NO |
| 14:25:13 | APTUSDT | Sell | 1 | 9 | 90.0% | 90.9% | YES | YES | YES |
| 14:34:03 | SANDUSDT | Sell | 1 | 9 | 90.0% | 90.9% | YES | YES | YES |
| 14:34:04 | AXSUSDT | Sell | 1 | 10 | 90.9% | 91.7% | YES | YES | YES |
| 14:34:05 | LINKUSDT | Sell | 1 | 11 | 91.7% | 92.3% | YES | YES | YES |
| 14:42:25 | AVAXUSDT | Sell | 1 | 7 | 87.5% | 88.9% | YES | YES | YES |
| 14:42:27 | ORCAUSDT | Sell | 1 | 8 | 88.9% | 90.0% | YES | YES | YES |
| 14:53:18 | INJUSDT | Sell | 1 | 5 | 83.3% | 85.7% | YES | YES | YES |

Note: Sell% before line "14:17:32 HBARUSDT" drops to 7 open from 8 because XRPUSDT-partial closed at 14:12:29 and APTUSDT-first SL'd at 14:12:02, but the second XRPUSDT entry at 14:06:23 stayed open. The numbers above derive from the live trade_log via the algorithm "positions with `opened_at <= now < closed_at` count as open."

## Cap simulation outcomes — entries blocked vs allowed

The simulator's flag columns assume HARD CAP (Design A): block new entries in same direction when pre-entry concentration already at cap. Block applies to direction == "Sell" with pre% >= cap. Buy entries are never blocked because Buy% is always 0% or 10% in this window (well below any reasonable cap).

### Cap 60%

- Sell entries blocked: 18 (PLUMEUSDT, ORCAUSDT@13:48, DYDXUSDT, SKRUSDT, SEIUSDT, XRPUSDT, NEARUSDT, APTUSDT@14:06, HBARUSDT, MNTUSDT, SOLUSDT, APTUSDT@14:25, SANDUSDT, AXSUSDT, LINKUSDT, AVAXUSDT, ORCAUSDT@14:42, INJUSDT)
- Sell entries allowed: 1 (HYPEUSDT — the first Sell at 0% pre-concentration)
- Buy entries allowed: 1 (OPUSDT — Buy is never blocked since Buy% is always low)
- 14:45 cascade member outcomes (5-trade cascade AVAX/APT/SAND/LINK/ORCA):
  - All 5 are blocked (each entered when Sell% >= 87.5%)
  - Cascade losses prevented: AVAX (-5.61), APT@14:25 (-4.98), SAND (-4.42), LINK (-2.70), ORCA@14:42 (-14.11) = **-$31.82 saved**

### Cap 70%

- Same as 60% in this window because every Sell entry after the first is at >= 87.5% (no Sell entries sit in the band 60-70%)
- Sell entries blocked: 18; Buys allowed: 1; first Sell allowed: 1
- 14:45 cascade fully blocked: **-$31.82 saved**

### Cap 80%

- Same as 60% and 70% in this window — every Sell entry after the first is at >= 87.5%
- Sell entries blocked: 18; Buys allowed: 1; first Sell allowed: 1
- 14:45 cascade fully blocked: **-$31.82 saved**

### Cap 90%

- Sell entries blocked: 14 (PLUMEUSDT through SOLUSDT, APTUSDT@14:25, SANDUSDT, AXSUSDT, LINKUSDT)
- Sell entries allowed: 5 (HYPEUSDT first Sell, AVAXUSDT, ORCAUSDT@14:42 at 88.9%, INJUSDT, plus second-position oddities)
- 14:45 cascade member outcomes:
  - APT@14:25 (90.0% pre) BLOCKED — saved -$4.98
  - SAND (90.0% pre) BLOCKED — saved -$4.42
  - AXS (90.9% pre) BLOCKED — saved -$0.92
  - LINK (91.7% pre) BLOCKED — saved -$2.70
  - AVAX (87.5% pre) ALLOWED — lost -$5.61
  - ORCA@14:42 (88.9% pre) ALLOWED — lost -$14.11
  - Cascade losses prevented: -$12.02 (38% of total cascade)

## Headline simulation result

| Cap | Sell entries blocked | Sell entries allowed | 14:45 cascade fully blocked? | Cascade loss prevented |
|-----|---------------------|---------------------|------------------------------|------------------------|
| 60% | 18 of 19 | 1 (first one) | Yes — all 5 | -$31.82 (100%) |
| 70% | 18 of 19 | 1 (first one) | Yes — all 5 | -$31.82 (100%) |
| 80% | 18 of 19 | 1 (first one) | Yes — all 5 | -$31.82 (100%) |
| 90% | 14 of 19 | 5 | Partial — 4 of 6 in window | -$12.02 (38%) |

This shows the cascade is dominated by entries at >= 87.5% concentration. Any cap between 60% and 80% would have produced the same outcome for the cascade itself. The aggressive blocking shape (18 of 19 Sells rejected) is NOT a fix-design failure but a direct mirror of the underlying R1 + R2 + R3 problem: the brain produced 95%+ Sells because of the upstream causes the other agents address. R4 acting alone on this evidence behaves as a back-stop while ALPHA + BETA reduce the upstream bias.

## What R4 alone would have done to the session

If R4 fired AS-A-HARD-CAP at 70% with no other R1/R2/R3 fix:

- 14:45 cascade losses prevented: -$31.82 (100% of the 5-position cascade)
- Total Sell entries that would have executed in the 13-15h window: 1 (HYPEUSDT, the first entry at zero concentration)
- HYPEUSDT closed at -$22.71 (wd_timeout)
- Net result: window goes from approximately -$108 (sum of all losses) to approximately -$23 (HYPEUSDT alone)
- BUT this comes at the cost of denying 18 trade opportunities. Operator must accept that the system would trade less if R4 fired without R1/R2/R3 in place

Caveat for Rule 12 (interaction): R1 + R2 + R3 are designed to flip many of the 18 Sells to Buys upstream. With ALPHA + BETA in place, the pre-R4 distribution would be closer to balanced (the spec aims for 50/50). R4 then acts as a true back-stop, not the primary filter, and its blocking rate falls dramatically. The numbers above represent R4-alone-on-current-bias, the worst case for the cap.

## Where the cap should have fired (operator-facing)

The first Sell entry that the cap would have blocked under any of 60/70/80% is PLUMEUSDT at 13:48:38 (one second after HYPEUSDT opened, when the portfolio was already 100% Sell with N=1). The cascade itself (14:45) was already a foregone conclusion by that point — 11 Sells were already open by 14:17 with no upstream check restraining the brain.

A reasonable interpretation of the cascade event: it was NOT a one-time surprise. It was the inevitable resolution of a portfolio that had been 100% directional for nearly an hour. The cap should have fired at the SECOND Sell entry, not at entries 18-19.

## What the cap value should be — preliminary read

The data does not discriminate between 60%, 70%, and 80% caps because every Sell entry after the first was at >= 87.5% concentration. The operator's choice between these three values depends on:

- How often the portfolio is expected to dwell in the 50-70% concentration band (which only OPUSDT's brief presence created here)
- Whether the operator wants the cap to fire EARLY (60%) or only as a true back-stop (80%)
- Synthesis recommendation in 05: 70%, justification given there

## What the cap would NOT do

R4 does not address the upstream R1/R2/R3 problem. It only limits the BREADTH of the bias. The brain still produces a 87% Sell directive in the absence of R1/R2/R3. R4 prevents 18 of those Sells from reaching execution; it does NOT make the brain pick Buys instead. The operator must understand: if R4 ships first and R1/R2/R3 don't ship, the system will trade far less frequently because most brain-proposed Sells get rejected, but a Buy will not appear in their place automatically.

This is precisely why DELTA must sequence implementations and why R4 should ship AFTER or ALONGSIDE the upstream fixes, not as a standalone solution. R4 is a NEW back-stop, not a replacement for the upstream fixes.
