# IMPLEMENT: Entry-Quality Selectivity — fewer trades, higher accuracy

> **Status:** PLANNED — not yet implemented
> **Date:** 2026-07-16
> **Operator goal (verbatim intent):** fewer trades is fine; improve accuracy, take only confident setups, make genuine PnL. No indiscriminate trade-pulling.
> **Evidence base:** 342 trades Jul 13–16 (`trade_intelligence`, split at the R:R fix deploy 2026-07-14T19:05 UTC), 546 live entry-gate evaluations, honest entry-time joins.
> **Builds on:** `IMPLEMENT_ENTRY_VOLUME_GATE.md` (gate machinery, deployed + verified), `ENTRIES_QUALITY_DIAGNOSIS.md` (exits exonerated; entries are the problem).

---

## 0. Where the system stands (post-R:R-fix, 214 trades)

The R:R fix worked: win 56.1%, avg win +0.385% / avg loss −0.398% (≈1.0x,
was 0.64x), **net +8.80%** vs −3.31% pre-fix. The book is now profitable but
carries a heavy drag: roughly half the trades (the low-quality half) lose
money and dilute the other half's genuine edge. This plan removes the drag.

## 1. The evidence — what actually separates winners from losers now

### 1a. `entry_atr_pct` (ATR% at entry, honestly captured at entry) — THE signal

Post-fix window, monotonic across buckets:

| entry ATR% | n | win% | cum PnL% |
|---|---|---|---|
| 0 – 0.10 | 20 | 35.0 | −0.74 |
| 0.10 – 0.20 | 81 | 46.9 | −4.36 |
| 0.20 – 0.30 | 17 | 58.8 | −0.40 |
| 0.30 – 0.50 | 41 | **70.7** | **+7.63** |
| ≥ 0.50 | 51 | 64.7 | +6.27 |

Single split at **0.25**: kept 103 trades → **68.0% win, +14.59%** ; dropped
111 → 45.0% win, −5.80%. At **0.20**: kept 109 → 66.1% win, +13.49%; dropped
105 → 45.7%, −4.70%. Pre-fix window agrees directionally (kept −1.15% vs
dropped −2.16%; win 60.7% vs 42.9%). The entire post-fix profit lives in the
ATR ≥ 0.20 book.

**Mechanism (not just correlation):** a coin with entry ATR ≤ 0.2% is barely
moving. Its TP is structurally hard to reach before stall/timeout, fees eat
the small wins (~0.11% round trip vs +0.24% avg win pre-fix), and the losers
grind out through `loss_stall`/`timeout`. This is the same "dead tape"
failure identified independently by the fee-drag analysis and the June
diagnosis.

**Fine print (honest):** within-symbol validation was not possible — no
symbol had ≥5 trades on both sides of 0.25 in this window, so the filter
partly selects *which coins are currently moving* rather than timing within
a coin. That is acceptable here: it is exactly the selectivity requested,
and ATR is a live property (a coin crosses the line as its volatility
changes — this is NOT a static coin ban list). Risk mitigated by
observe-first rollout (§3 Phase A).

### 1b. Entry-time volume_ratio — floor only, do NOT raise (honest negative)

Joining all 110 executed trades since the gate went live to their true
entry-time vr from `ENTRY_VOLUME_GATE` logs: winners median 0.998, losers
median 1.070 — **no gradient above the 0.30 floor**. The strong separation
in the original 371-trade analysis came from `trade_intelligence.volume_ratio`,
which §4 of this doc corrects: that column is captured at **close** (TIAS
collector runs from `on_trade_closed`), and winners systematically close on
volume expansion (TP hits) while stall-losers close in dead tape — a
close-time confound. Decision: **keep `min_volume_ratio = 0.30`** as a
dead-tape floor (the gate has blocked 209 of 546 proposals, including
vr=0.02 entries that are indefensible), **do not raise it**.

### 1c. The brain's own confidence is still inverted — cannot be the selector

`claude_confidence`: losers median 0.515 vs winners 0.470 (post-fix). Any
"take only confident trades" logic keyed on the model's self-reported
confidence would select the WRONG trades. Selectivity must come from
market-measured features (ATR), not model self-assessment.

### 1d. Hold time: trades that work, work immediately (re-confirmed post-fix)

<10 min: 74.1% win, +12.50%. 10–20 min: 43.1% win, −2.55%. >20 min: 24.3%
win, −1.16%. A trade that hasn't worked by ~12 min is a coin flip decaying
toward certain loss.

### 1e. Sizing is anti-correlated with outcome

Losers are sized larger than winners (median $194 vs $168 in the 371-trade
baseline; the conviction that drives size up is the same inverted confidence
from 1c). The brain is betting biggest on its worst ideas.

### 1f. Cleared of suspicion (do not touch)

- **strategic_review closes** — the 8 post-fix losers were violent adverse
  moves (−1.2 to −1.9% within 5–12 min, most never green) on high-ATR coins;
  review closed them FAST. It is a rescue mechanism working, same verdict as
  `win_prob_near_certain`. 6 of 8 would NOT have been caught by the ATR
  filter (they had high ATR) — they are the cost of trading moving coins,
  and the R:R math absorbs them (B3/XEC are net-positive symbols overall).
- **Re-entry after loss ("revenge trading")** — 57 such trades net +4.20%.
  Not a leak. No cooldown changes needed.
- **Exit system** — sixth consecutive analysis confirming exits are fine.

---

## 2. Design decisions

1. **One lever at a time.** Ship the ATR gate alone, measure, then decide on
   sizing (Phase B) and time-fuse (Phase C). Shipping all three at once makes
   attribution impossible.
2. **Reuse the existing gate machinery.** `src/core/entry_volume_gate.py` +
   the `[entry_volume_gate]` config section + the strategy_worker wiring are
   deployed, tested, and already fetch the exact TA result that contains
   `natr_14` (entry ATR). Add an ATR check to the same gate rather than
   building a parallel one.
3. **Independent modes per check.** The vr check stays `enforce`; the new ATR
   check gets its own `atr_mode` so it can observe while vr keeps enforcing.
4. **Observe-first this time.** The vr gate's enforce-on-day-one shortcut was
   the operator's call and it worked out, but 1a's within-symbol gap is a
   real unknown. 24–48h of `would_block` logging on live proposals costs
   nothing and directly measures the kept/dropped split before any trade is
   refused.

## 3. Phases

### Phase A — ATR entry gate (the main event)

**A1. Extend the gate module** (`src/core/entry_volume_gate.py`):
`evaluate_entry_quality_gate(volume_ratio, atr_pct, min_volume_ratio,
min_atr_pct, ...)` returning per-check verdicts + a combined `would_block`.
Fail-open on missing ATR (same convention). Pure function, extend the
existing tests.

**A2. Config** (`[entry_volume_gate]`, keeping the section name for
compatibility):
```toml
min_atr_pct = 0.20      # start conservative; 0 = check disabled
atr_mode = "observe"    # flip to "enforce" after A4 review
```

**A3. Wiring** — same place in `_execute_claude_trade`: the TA result is
already fetched for vr; read `(_evg_ta.get("volatility") or {}).get("natr_14")`
from the SAME result (zero extra TA calls). Extend the `ENTRY_VOLUME_GATE`
log line with `atr=… atr_would_block=…`.

**A4. Review after 24–48h of observe data** (≥150 proposals): compute the
would-block cohort's realized PnL from the log join. Exit criteria to
enforce: kept-book win% ≥ 60 AND dropped-book cum PnL < 0. Then flip
`atr_mode = "enforce"`.

**A5. Tune 0.20 → 0.25** only after ≥200 enforced trades, from archive data.

**Projected effect (in-sample, treat as estimate not promise):** trade count
roughly halves (~107/day → ~50/day); win% ~56 → ~66-68; the −4.7 to −5.8%
daily drag from the low-ATR book disappears.

### Phase B — sizing sanity (after A is enforced and measured)

Investigate what drives `size_usd` dispersion (brain conviction sizing under
brain-authoritative mode). Evidence says conviction-scaled size is value-
destroying while confidence stays inverted (1c/1e). Candidate fix: flatten
per-trade size toward the median (narrow band), keeping total exposure
unchanged. Requires its own small analysis + operator sign-off since it
touches the sacred sizing philosophy ([apex] brain-authoritative sizing,
operator decision 2026-05-31).

### Phase C — time-fuse (only if needed after A+B)

If the >12-min book is still a coin flip after the ATR gate (it may fix
itself — dead coins are the ones that stall), tighten the `loss_stall` fuse
so flat-at-12-min trades exit at scratch instead of decaying. Do not touch
before A/B are attributed — the exit system is calibrated and every prior
"fix the exit" instinct has been wrong.

### Explicit non-actions (as important as the actions)

- Do NOT raise `min_volume_ratio` above 0.30 (1b — no gradient).
- Do NOT use `claude_confidence` for anything selective (1c — inverted).
- Do NOT add re-entry cooldowns (1f — revenge trades are net positive).
- Do NOT ban individual symbols (1000XECUSDT is simultaneously the biggest
  strategic_review loser AND the 2nd-best symbol; static bans select on
  noise).
- Do NOT touch the R:R config (`d1b1561`) — it flipped the book positive.

## 4. Correction to the record: `trade_intelligence.volume_ratio` is close-time

The TIAS collector (`src/tias/collector.py`, invoked from
`TradeCoordinator.on_trade_closed`) captures the unprefixed TA columns
(`volume_ratio`, `rsi`, `adx`, …) at trade **close**, not entry. Only the
`entry_*`-prefixed columns are entry-time. The original 371-trade volume
analysis unknowingly used close-time data, which inflated the separation
(§1b). The vr gate survives on its floor-level merits, but all future
entry-feature research MUST use either `entry_*` columns or the
`ENTRY_VOLUME_GATE` log join. Consider adding `entry_volume_ratio` to the
coordinator's entry capture (one line next to `entry_atr_pct`) so the honest
feature lands in the DB for free.

## 5. Measurement protocol

- Daily: archived CSVs (`data/trade_logs/archive/`) → win%, avg win/loss,
  net PnL, split by kept/dropped cohort.
- Checkpoints at ~200 enforced trades per phase before the next lever.
- Success for the plan overall: win% ≥ 60, avg-win/avg-loss ≥ 1.2, net
  positive after fees on a ≥400-trade sample.
- Rollback for every phase is config-only (`atr_mode="observe"`, or
  `min_atr_pct=0`).
