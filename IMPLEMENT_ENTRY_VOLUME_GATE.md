# IMPLEMENT: Entry Volume-Ratio Gate

> **Status:** PLANNED — not yet implemented
> **Date:** 2026-07-15
> **Evidence base:** 371 closed shadow trades on the VM (`trade_intelligence`, 2026-07-11 → 2026-07-14, all pre-dating the R:R fix `d1b1561`)
> **Depends on:** nothing. **Blocks:** nothing.

---

## 1. Why this exists

The June entries-quality diagnosis (`ENTRIES_QUALITY_DIAGNOSIS.md`) proved the exit
system is fine and the entries have no edge — but found no entry-time feature that
separates winners from losers. This analysis found one: **`volume_ratio`**
(current volume vs its SMA, at entry).

### The core evidence (371 trades)

| volume_ratio at entry | n | win% | net $ |
|---|---|---|---|
| < 0.2 | 123 | 48.8% | **−35.51** |
| 0.2 – 0.4 | 63 | 55.6% | **−35.66** |
| 0.4 – 0.7 | 57 | 66.7% | −7.08 |
| 0.7 – 1.2 | 46 | 63.0% | **+29.28** |
| ≥ 1.2 | 82 | 70.7% | **+27.12** |

A single `volume_ratio ≥ 0.4` split: kept **+$49.31** (185 trades, 67.6% win) vs
dropped **−$71.17** (186 trades, 51.1% win). Trades that later needed force-closing
(the −$120 bleeder bucket) entered at median volume_ratio **0.289** vs **0.452**
for clean SL/TP closes.

### Robustness checks passed (all on this window)

1. **Per-day (4 sub-windows):** kept-side positive and dropped-side negative on
   Jul 11, 12, 13; Jul 14 directionally right but weak (small n=38).
2. **Leave-one-symbol-out:** excluding each of the 8 biggest PnL contributors one
   at a time, the split survives in every case (kept +$21.91…+$70.25, dropped
   −$44…−$93).
3. **Within-symbol:** for the same coin, high-vr entries beat low-vr entries in
   6 of 9 symbols with ≥6 trades on each side — not merely a symbol proxy.
4. **Threshold sensitivity:** every threshold 0.25 → 0.6 keeps positive / drops
   negative. Not a knife-edge artifact.
5. **Chronological halves:** first half kept +$19.31 / dropped −$17.87; second
   half kept +$30.00 / dropped −$53.30.

### Honest caveats

- **One 4-day market window.** The June log bundle used by the prior diagnosis
  rotated off the VM and cannot be re-tested. A different regime (strong trend,
  crash) may behave differently. This is why Phase 0 is observe-only.
- Even the kept book is thin (+$0.27/trade). The gate **stops the bleeding**; it
  does not create a large edge by itself.
- The mechanism is plausible (thin-volume moves are drifty and conviction-less)
  but unproven causally.

---

## 2. Wiring facts (verified by reading the code — do not skip re-verifying)

| Fact | Where |
|---|---|
| The measured feature is `result['volume']['volume_sma_ratio']` from `ta_cache.analyze(symbol=…, timeframe=TimeFrame.M5, limit=100)` | `src/tias/collector.py:499` (capture), `src/analysis/engine.py` (computation; optional closed-candle mode via `ta.volume_ratio_use_closed_candle`) |
| The coin package carries a **different** field: `scoring_regime_volume_ratio` (+ `_known` flag), derived from regime consensus, defaulting to `0.0` with `known=True` | `src/core/coin_package.py:89-90`, populated in `src/workers/scanner_worker.py:847-848`, read in `src/brain/strategist.py:245-282, 4480` |
| Entry flow: scanner builds packages → `pkg.qualified` (label + interestingness, `scanner_worker.py:1515`) → strategist prompt → brain proposes trades → `strategy_worker.py` validates SL/TP (`SLTPValidator`, ~line 3052-3169) → order placed | `src/workers/scanner_worker.py`, `src/brain/strategist.py`, `src/workers/strategy_worker.py` |
| Precedent: per-label volume gates already exist (MOMENTUM_BURST needs vr ≥ 1.5) and **fail-open when input is None** ("volume_ratio gate bypassed when input is None") | `src/workers/scanner/state_labeler.py:534-558`, boot notice `scanner_worker.py:215` |

**CRITICAL — feature fidelity:** the gate MUST read the same value the analysis
measured (M5 `volume_sma_ratio` via `ta_cache`), NOT `scoring_regime_volume_ratio`,
unless the two are first proven equivalent. They come from different code paths.

---

## 3. Design decisions

1. **Gate location: `strategy_worker.py`, pre-placement** — after the brain
   proposes a trade, before order placement (alongside the SLTPValidator block).
   Rationale: (a) it is the last point before money moves, so it catches every
   entry path; (b) `strategy_worker` can call the same `ta_cache.analyze(...)`
   the tias collector uses, guaranteeing feature fidelity; (c) gating at the
   scanner would starve the brain's context of coins that are still worth
   *watching*, and prompt-level rules are unreliable with the current free-tier
   model.
2. **Fail-open on missing data** (`volume_ratio is None` → allow, log the bypass).
   Consistent with the existing state-labeler convention. A data outage must not
   silently halt all trading.
3. **Config-driven, no magic numbers** — new keys in `config.toml`, read via
   `src/config/settings.py`. Kill switch = set threshold to `0`.
4. **Observe first, enforce second** (Phase 0 → Phase 1). One-window evidence
   does not justify skipping a live counterfactual measurement.

---

## 4. Phases

### Phase 0 — Observe-only (ship first, ~1 session)

Add the gate in **log-only mode**: compute entry-time `volume_sma_ratio` for every
brain-proposed trade, log a structured line, never block.

```
ENTRY_VOLUME_GATE | sym=X vr=0.31 thr=0.30 mode=observe would_block=true | ctx
```

**Changes:**
- `config.toml` — new section:
  ```toml
  [entry_volume_gate]
  enabled = true          # master switch for the gate machinery
  mode = "observe"        # "observe" | "enforce"
  min_volume_ratio = 0.30 # conservative start; 0 disables even in enforce mode
  ```
- `src/config/settings.py` — typed dataclass section + defaults (follow the
  pattern of the 23 existing sections).
- `src/workers/strategy_worker.py` — gate check just before the SLTPValidator
  block (~line 3052). Reuse/attach to the services' `ta_cache` exactly as
  `src/tias/collector.py` does (`ta_cache.analyze(symbol, TimeFrame.M5, limit=100)`).
  Cache per-cycle per-symbol to avoid duplicate TA calls.

**Before touching anything (per CLAUDE.md):** grep every usage of the touched
blocks in `strategy_worker.py`; confirm `ta_cache` is in `self._services` for
strategy_worker's runtime (the tias collector gets it via
`self._services.get("ta_cache") or self._services.get("ta")`).

**Exit criteria for Phase 0 (gate to Phase 1):** ≥3 days / ≥200 proposed trades
logged, and the would-block cohort's realized PnL is worse than the pass cohort's
(same join as this analysis, run against fresh `trade_intelligence` rows).
If the live counterfactual does NOT reproduce the split — stop, do not enforce,
write up the discrepancy.

### Phase 1 — Enforce at 0.30 (only after Phase 0 passes)

- Flip `mode = "enforce"`. Blocked trades log
  `ENTRY_VOLUME_GATE | … mode=enforce blocked=true` and are skipped (the brain's
  decision is recorded in `brain_decisions` as usual; the trade is not placed).
- Start at **0.30**, not 0.40: keeps ~212 of 371 trades (+$20.85 vs −$42.71
  dropped on the baseline window) instead of halving trade volume on day one.
- Watch for one week: trade count, win%, avg win/avg loss ratio, net PnL, and
  the gate's block rate per day.

### Phase 2 — Tune + related work (separate commits, same theme)

1. **Threshold tuning** toward 0.4–0.5 if the enforced week's data supports it
   (0.5 showed the best kept-net on the baseline window: +$64.95, but that is
   threshold-mining until confirmed live).
2. **Trade-data retention cron** — the June log bundle is gone and blocked true
   cross-window validation. Add a daily cron on the VM exporting
   `trade_log` + `trade_intelligence` to dated CSVs (~300 KB/window) under
   `~/trading-bot/data/trade_logs/archive/`. There is already a
   `daily_export` job writing to `data/trade_logs/` — extend it rather than
   adding a second job (analyse its script first).
3. **Correct `IDENTIFIED_ISSUES.md` open issue #1** — `win_prob_near_certain`
   is the watchdog's near-certain-LOSER cut (`src/risk/time_decay_sl.py:98`,
   threshold `near_certain_loser_p_win = 0.10`), not brain overconfidence. Its
   10% realized win rate matches the ≤10% cut threshold — well-calibrated,
   working as designed. Close the issue as a misdiagnosis.
4. **Measure the R:R fix (`d1b1561`)** — the 371-trade baseline is entirely
   pre-fix: win/loss ratio 0.64x (avg win +0.321% / avg loss −0.499%). After
   ~100 post-fix closed trades, re-run the ratio. Target: ≥1.5x. Also verify
   the brain actually obeys the "TP ≥ 2× SL" prompt rule (the
   `apex_final_sl/tp` columns were empty in this export — investigate why
   while at it).

---

## 5. Also observed in the data (context, no action planned)

- **Hold-time decay is monotonic:** <10 min = 68.7% win (+$21.21); every bucket
  past 10 min is net negative; `timeout` closes are 0% win (n=8). If the gate
  ships and the book is still negative, the next lever is a shorter fuse on
  stalled trades (the existing `loss_stall` machinery is adjacent).
- **Sizing is anti-correlated with outcome:** losers median $194 vs winners
  $168. Worth a separate investigation into what drives `size_usd` up on the
  losing setups.
- **Combined <10min AND vr≥0.4 cohort:** 75.6% win, +$39.58 on 127 trades —
  the system's profitable core, for reference.

---

## 6. Verification & rollback

**Verify (Phase 0):** deploy to VM, confirm `ENTRY_VOLUME_GATE` lines appear in
`workers.log` within one brain cycle; confirm zero behavior change (trade counts
unchanged vs prior days).

**Verify (Phase 1):** confirm blocked proposals are logged and absent from
`trade_thesis`/orders; daily block-rate sanity check (expect roughly 40-50% at
thr 0.30 based on baseline distribution — if it's 90%, something is wrong, revert
to observe).

**Rollback:** `mode = "observe"` (instant, config-only) or `enabled = false`.
No schema changes anywhere in this plan, so rollback is always config-only.

**Session hygiene (per CLAUDE.md):** each phase lands as atomic commits direct to
`main`, pushed same session. Analysis scripts stay in scratchpad — do not commit.
