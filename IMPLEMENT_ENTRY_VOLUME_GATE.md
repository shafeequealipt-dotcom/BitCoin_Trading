# IMPLEMENT: Entry Volume-Ratio Gate

> **Status:** DEPLOYED AND VERIFIED LIVE on the VM, `mode="enforce"` @ threshold 0.30. See §7-§9.
> **Date:** 2026-07-15
> **Evidence base:** 371 closed shadow trades on the VM (`trade_intelligence`, 2026-07-11 → 2026-07-14, all pre-dating the R:R fix `d1b1561`)
> **Depends on:** nothing. **Blocks:** nothing — operator chose to enforce on first deploy rather than gate on a live counterfactual (see §8).

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

---

## 7. Phase 0 implementation record (2026-07-15)

Shipped in observe-only mode. Files touched:

| File | Change |
|---|---|
| `config.toml` | New `[entry_volume_gate]` section: `enabled=true`, `mode="observe"`, `min_volume_ratio=0.30` |
| `src/config/settings.py` | New `EntryVolumeGateSettings` dataclass + `_build_entry_volume_gate()` builder, wired into `Settings` container and `_load_fresh()`, following the exact `LossCuttingSettings`/`_build_loss_cutting` pattern (unknown TOML keys silently ignored) |
| `src/core/entry_volume_gate.py` (new) | Pure-function `evaluate_entry_volume_gate(volume_ratio, min_volume_ratio) -> VolumeGateResult`. No I/O, no service deps — mirrors the `coin_package_validator.py` precedent |
| `src/workers/strategy_worker.py` | Gate check inserted in `_execute_claude_trade`, immediately after the `SLTPValidator.validate_pair` SKIP check (was line ~3184) and before the volatility-scaled-stop block. Reads `volume_ratio` via the shared TTL-cached `ta_cache.analyze(symbol, TimeFrame.M5, limit=100)` — same source the original analysis measured, and already called elsewhere in this function, so no added TA cost. Logs `ENTRY_VOLUME_GATE \| sym=... vr=... thr=... mode=... verdict=... would_block=... reason=...` on every proposed trade. In `mode="enforce"` (not yet active) a `would_block=True` verdict returns `(False, "entry_volume_gate_blocked")`, added to the function's documented reason-code enum |
| `tests/test_entry_volume_gate.py` (new) | 7 tests: threshold pass/block boundary, fail-open on `None`, kill-switch at `min_volume_ratio<=0`, settings defaults, and `__post_init__` validation rejections |

**Verified:** `py_compile` on all touched files; `Settings._load_fresh("config.toml", ".env")` round-trips `entry_volume_gate.{enabled,mode,min_volume_ratio}` correctly from the real config; full test run (`test_entry_volume_gate.py`, `test_coin_package_validator.py`, `test_phase0/test_settings.py`, `test_strategy_worker_consensus.py`) — 36/36 pass, no regressions. Not yet verified live on the VM (deploy pending).

**Superseded by §8:** the "not done yet" list originally here (deploy, wait
for the live counterfactual before enforcing) no longer applies — the
operator directed completing Phase 1 and Phase 2 before any deploy. See §8.

---

## 8. Phase 1 + Phase 2 implementation record (2026-07-15)

**Operator decision (Phase 1):** complete Phase 1 and Phase 2 in full before
the first deploy, rather than the originally planned observe-then-confirm
rollout. This means `mode="enforce"` ships on day one — the live
counterfactual described in §4 (≥3 days / ≥200 observed trades confirming
the would-block cohort underperforms) will NOT run before enforcement
begins. This is a deliberate acceptance of one-window-validation risk made
by the operator, documented here so it's auditable later. Mitigations kept
in place:
- Threshold set conservatively at **0.30**, not the stronger 0.40 split
  the analysis found (0.30 keeps ~57% of the baseline window's trades;
  0.40 keeps ~50%) — smaller blast radius if the split doesn't hold up.
- Every gate evaluation still logs `ENTRY_VOLUME_GATE` with `would_block`
  regardless of mode, so post-deploy the same live-counterfactual check
  can run retroactively against real enforced-mode data.
- Rollback is a single config line (`mode = "observe"`), no redeploy of
  code required — only a worker restart to pick up the config change.

`config.toml` `[entry_volume_gate]` updated: `mode = "observe"` → `"enforce"`.
No other Phase 1 code changes were needed — `strategy_worker.py` already
implemented the enforce path in Phase 0 (§7); flipping the mode was the
entire remaining Phase 1 scope.

**Phase 2 items completed:**

| Item | What was done |
|---|---|
| **2. Trade-data retention cron** | Extended `scripts/daily_trade_export.py` (not a new job, per the plan) with `archive_full_table()` — dumps the complete `trade_log` + `trade_intelligence` tables to dated, immutable CSVs under `<out-dir>/archive/` on every run, plus `prune_old_archives()` (default 90-day retention, filename-date driven). New flags `--archive-retention-days` (default 90) and `--skip-archive`. This directly fixes the gap that made the June log bundle's loss unrecoverable — future entry-quality re-validations no longer depend on log files surviving rotation. Tested locally against `data/trading.db`: archive files created with correct full-column dumps for both tables; retention pruning verified to remove only date-expired files; `--skip-archive` verified to suppress the step. |
| **Bonus fix found while touching this area** | `trading-export.service` and `trading-healthcheck.service` on the VM have been silently failing since 2026-07-13/14 — `scripts/daily_trade_export.py` and `scripts/check_bot_health.py` lost their executable bit in the 2026-07-08 deploy (`-rw-rw-r--`), so systemd's exec step failed with `Permission denied` on every scheduled run, with nothing surfacing the failure. Fixed with `chmod +x` on the VM directly (not a code change — a deployed-state repair). This means the archive extension above would have silently never run without this fix. |
| **3. `IDENTIFIED_ISSUES.md` correction** | Corrected the `win_prob_near_certain` misdiagnosis (issue #18): it is the watchdog's calibrated near-certain-loser cut, not brain overconfidence — confirmed via `src/risk/time_decay_sl.py:98` and independently via `ENTRIES_QUALITY_DIAGNOSIS.md` Finding 2. Added issues #19-21 documenting the volume-ratio gate, the retention-cron fix, and the systemd permission-bit fix. Updated the "Open Issues" list to remove the resolved misdiagnosis and add the R:R-fix-measurement and gate-threshold-tuning follow-ups. File is now tracked in git for the first time (was previously untracked working output). |

**Phase 2 items NOT completed (genuinely blocked, not skipped):**

| Item | Why it's blocked |
|---|---|
| **1. Threshold tuning toward 0.4-0.5** | Requires live enforced-mode data to avoid compounding the one-window risk with a second round of threshold-mining on the same window. Deferred until post-deploy data exists. |
| **4. R:R fix (`d1b1561`) live measurement** | Needs ~100 post-fix closed trades, which don't exist yet — nothing has traded since the fix landed and the system isn't deployed. Cannot be "completed" without live trading activity. Tracked in `IDENTIFIED_ISSUES.md` open issue #4 for the next session that has fresh trade data. |

**Verification performed:** `py_compile` on `scripts/daily_trade_export.py`;
local dry-run against `data/trading.db` producing correct archive CSVs;
retention-pruning behavior verified with an artificially-aged file;
`Settings._load_fresh` round-trips `mode="enforce"` correctly from the
real `config.toml`.

---

## 9. Phase 3 (deploy) implementation record (2026-07-15)

Deployed to the VM (140.245.230.251, `~/trading-bot`) and restarted
`trading-workers` + `trading-brain`. A real production bug was found and
fixed during live verification — not part of the original plan, recorded
here in full since it's the kind of thing that would otherwise re-surface
silently.

### Pre-deploy fix: executable bit

Before pulling, discovered git had `scripts/daily_trade_export.py` and
`scripts/check_bot_health.py` committed as mode `100644` despite systemd
invoking them directly (`ExecStart=.../scripts/*.py`, no `python3` prefix).
Pulling as-is would have silently re-broken the two systemd timers that
were manually `chmod +x`'d on the VM earlier in the Phase 2 work (see
`IDENTIFIED_ISSUES.md` #21). Fixed by setting the executable bit in git
itself (commit `97dc703`) before the deploy pull, so the fix is durable
across future deploys instead of living only as VM-side manual state.

### Bug found during deploy verification: `UnboundLocalError` on `TimeFrame`

After the first restart, the gate evaluated 40 consecutive live trades
over ~2.5 hours and returned `volume_ratio_unavailable` (`vr=NA`) on
every single one — despite an isolated reproduction of the exact same
`TACache.analyze()` call, for the exact same symbols, returning real
values immediately. The gate's `try/except` was silently swallowing the
real cause at `log.debug`, which is invisible at the deployed
`log_level=INFO`.

**Diagnosis path:**
1. Bumped the swallowed exception to `log.warning` (commit `3943fd3`) —
   deployed, waited, and got **zero** `ENTRY_VOLUME_GATE_TA_FETCH_FAIL`
   lines even though `vr` was still always `NA`. This ruled out an
   exception entirely — the failure was in normal control flow.
2. Added a temporary detailed dump of the raw TA dict shape (commit
   `9038ee7`, explicitly marked TEMP) — deployed, waited, and this time
   caught the real exception: `UnboundLocalError: local variable
   'TimeFrame' referenced before assignment`.
3. **Root cause:** `_execute_claude_trade` (the same function the gate
   lives in) has two *other*, pre-existing, purely-local
   `from src.core.types import TimeFrame` re-imports later in the
   function body (the T23 divergence-tracking block and the post-order
   observability capture block) — both redundant, since `TimeFrame` is
   already imported at module level (`strategy_worker.py:24`). Python
   determines variable scope at compile time for the *whole function*:
   because `TimeFrame` is assigned locally somewhere in the function
   (via those local imports), Python treats every reference to
   `TimeFrame` inside that function as local — including the gate's
   `TimeFrame.M5` reference, which runs *earlier* in execution order but
   is still inside the same function scope. Referencing a local before
   its assignment line raises `UnboundLocalError`. This bug was latent
   before the gate existed (nothing referenced `TimeFrame` earlier in the
   function), and became live the moment the gate's earlier reference was
   added.
4. **Fix** (commit `62b93b9`): removed both redundant local imports —
   `TimeFrame` resolves to the module-level import for the whole function
   again. Also removed the TEMP diagnostic dump now that the cause was
   found, keeping the `log.warning` bump (a legitimate permanent
   improvement — a silent `log.debug` here defeats the entire point of
   having a try/except safety net).

**Verified fix, live:** redeployed, restarted. First trade proposal after
the fix (10:45:30 UTC) shows:
```
ENTRY_VOLUME_GATE | sym=BZUSDT vr=2.596022 thr=0.30 mode=enforce
verdict=pass would_block=False reason=volume_ratio_ok
```
Real numeric value, correct threshold comparison, correct verdict.
`ENTRY_VOLUME_GATE_TA_FETCH_FAIL` total stayed at 1 (the single
pre-fix occurrence, not a new one). Zero trades blocked so far — expected,
since nothing pathological has been proposed yet.

**Also confirmed during this process (not bugs, expected behavior):**
- Cold-start warmup after any restart: the brain's first 1-2 cycles
  post-restart return `STRAT_CALL_A_SKIPPED reason=no_packages_available`
  until the scanner repopulates coin packages (~5-10 min) — matches
  `IDENTIFIED_ISSUES.md` #5.
- Fail-open behavior confirmed correct across all 40 pre-fix NA
  evaluations: `would_block=False` every time, zero trades wrongly
  blocked by the bug itself — the bug degraded the gate to a no-op, it
  did not cause any incorrect blocking.

### What's now live

`config.toml [entry_volume_gate]`: `enabled=true mode="enforce"
min_volume_ratio=0.30`, running in production on the VM, evaluating real
`volume_ratio` data, ready to block trades below threshold.

### Next steps (unchanged from §8, now actually actionable)

- Monitor the enforced week: block rate, trade count, win%, avg
  win/avg loss ratio, net PnL — compare against the baseline window's
  characteristics.
- Threshold tuning toward 0.4-0.5 once enforced-mode data exists.
- R:R fix (`d1b1561`) live measurement once enough post-fix trades close.
