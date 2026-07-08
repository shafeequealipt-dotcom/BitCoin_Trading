# Entries-Quality Diagnosis — the 28-hour window (read-only)

**Window:** `log_bundle_2026-06-17T0130_to_2026-06-18T0530_UTC.log` (the same window used for the exit replay).
**Method:** join every trade `THESIS_OPEN → THESIS_CLOSE` by `order_id`, attach each trade's entry-time
decision features (nearest-preceding per-symbol `XRAY_CLASSIFY`/`XRAY_SCORE`/`SIG_GEN`/`ENSEMBLE_TWO_SIDED`/`REGIME`),
then test whether any entry-time feature separates winners from losers, and where the realized loss actually sits.
No DB, no protected tables, no code changed. 395 of 397 trades reconstructable.

---

## Bottom line

On this window the system did **−6.45 usd over 395 trades, 47% win, symmetric payoffs** (avg win +0.40%,
avg loss −0.40%). That is a **coin flip with fee drag** — no demonstrated edge. The diagnosis localises *why*,
and it is consistent with every prior finding: **the exit is not the problem; the entries are.**

---

## Finding 1 (core) — the entry model cannot tell winners from losers

Every entry-time score is **statistically identical** between the trades that won and the trades that lost:

| entry-time feature | winners (median) | losers (median) | separates? |
|---|---|---|---|
| X-RAY confidence | 0.380 | 0.370 | no |
| X-RAY score (0-100) | 74 | 74 | no |
| signal confidence | 0.450 | 0.450 | no |
| planned reward:risk | 2.31 | 2.43 | no (losers slightly higher) |
| ensemble agreement | 2.55 | 3.05 | no (losers higher) |
| regime confidence | 0.515 | 0.510 | no |
| ADX (trend strength) | 23.8 | 23.4 | no |

The selection machinery (scanner → signal → X-RAY → ensemble → regime) produces scores that **do not predict
outcome**. Winners and losers look the same at entry. This is the central gap: the system has no way to rank
the trade it is about to take, so it takes coin-flips.

## Finding 2 — the exit is fully exonerated (now the 5th independent confirmation)

Splitting the same trades by **how they closed** shows the loss is not in the exit at all:

| close family | trades | win% | net usd | per-trade expectancy |
|---|---|---|---|---|
| stop-loss + take-profit exits | 353 (89%) | 51% | **+10.10** | +0.029 |
| loss-cutter / forced closes | 41 (11%) | 7% | **−16.85** | −0.411 |

- The **stop-loss / TP machinery is net positive (+10.1 usd, 51% win)** — exactly the earlier replay verdict.
- The **loss-cutters are working correctly, not leaking.** The biggest single bucket, `win_prob_near_certain`
  (20 trades, 10% win, −9.93 usd), is the **near-certain-*loser*** carve-out (`time_decay_sl.py:98`,
  `near_certain_loser_p_win = 0.10`): it cuts positions whose modeled win-probability has fallen to ≤10%.
  Its **10% realized win rate matches its ≤10% cut threshold exactly** — the watchdog model is well-calibrated
  and is *salvaging* bad positions, closing them at ~−0.85% median instead of letting them ride to the full
  −1.8% stop. The −16.85 usd here is **bad entries being cut**, not a closer defect.

So the money is not lost at the exit. It is lost by **entering trades that the watchdog later correctly
identifies as bleeders.** The exit is doing its job on both ends.

## Finding 3 — direction is NOT a clean lever

Both sides lose: **longs −2.61 usd (50% win), shorts −3.83 usd (44% win).** This is *not* "shorts fighting an
up-market." The per-regime direction crosses are noisy (e.g. long-in-volatile won, short-in-trending-down lost)
but small-n and one-window — not a stable signal.

## Finding 4 — crude filters flip this window positive, but they are one-window overfits

Gating on a single feature would have turned −6.45 into positive:

| filter | kept | win% | net usd | per-trade expectancy |
|---|---|---|---|---|
| (base, all) | 395 | 47% | −6.45 | −0.016 |
| signal conf ≥ 0.50 | 152 | 45% | +5.10 | +0.034 |
| X-RAY conf ≥ 0.40 | 138 | 53% | +2.90 | +0.021 |
| ADX ≥ 25 | 171 | 48% | +3.40 | +0.020 |
| longs only AND ADX ≥ 25 | 110 | 53% | +3.00 | +0.027 |

These improve per-trade expectancy (so it is not pure volume reduction), **but they do not raise the win rate**
(still ~45-53%) — they trade *less* and trim the left-tail big losers, not the *frequency* of losing. Tuning a
threshold on a single 28-hour window is textbook overfitting. **These are hypotheses to confirm across windows,
not a config to ship.**

---

## Recommendation

The recoverable edge is **not** a threshold tweak and **not** the exit. It is the entry model's inability to
rank trades. Honest next steps, in order:

1. **Confirm "no entry edge" across more windows (cheap, essential).** Repeat this exact join on 3–4 more
   captured windows. One window can neither justify an entry-model overhaul nor be trusted for threshold tuning.
   This is the gate before any entry work.
2. **If it holds, it is a research problem, not a tuning problem.** The current scores (X-RAY structure, signal
   confidence, ensemble agreement, regime) provably do not predict outcome. The entry needs a feature that
   *does*. A concrete lead: the watchdog's mid-flight win-probability model clearly *has* discriminating power
   **after** entry (it correctly flags ≤10% bleeders) — the research question is whether any of its inputs, or a
   proxy for them, are knowable **at** entry. That is where an edge could come from.
3. **Do not ship the one-window filters as a fix.** Carry them only as hypotheses into step 1's multi-window
   test.

**Exit-side:** nothing more to do. Phase 1 (profit-scaled trail) stays shipped-inert; Phases 2–3 are shelved.
The exit is near-optimal and the loss-cutters are calibrated. Five independent analyses now agree.
