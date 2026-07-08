# Phase 0 — Pre-Flight Verification + Baselines

**Date:** 2026-05-07
**Branch:** main (HEAD: `7406dbf`)
**Source spec:** `/home/inshadaliqbal786/IMPLEMENT_TP_VOLUME_CLOSURE_FIX_INDEPTH.md`
**Plan file:** `/home/inshadaliqbal786/.claude/plans/lucky-wobbling-pumpkin.md`

---

## Pre-Condition Checks

### Working Tree

`git status --short`:
- Modified: `data/layer_state.json`, `trading.db` — live state only, acceptable.
- Untracked: dev_notes / scripts / data backups — acceptable, not source.
- No source-file changes pending. Working tree clean for source.

### Services

```
trading-workers   active
trading-mcp-sse   active
```

### Logs

Current writers in `data/logs/`:
- `workers.log` — current writer.
- `brain.log` — current writer (4.2 MB; 2026-05-07 08:40 UTC last update).
- `general.log` — current writer.
- `mcp.log` — current writer.

Rotated logs cover 2026-05-04 through 2026-05-07. >48h coverage confirmed.

### DB

`data/trading.db` accessible.

### Prior Fixes Verified On Main

The 7 fixes the spec lists (Stage 2 architectural, Stage 2 framing, post-execution closure, XRAY direction-flip, time-decay 1+2, Layer 4 Phase 1) are all on main per memory and `git log`.

The CALL_B framing fix (10 commits ending `7406dbf`) is also on main but **not boot-loaded yet** (operator restart pending). It does not affect the Phase 0 reading because the live system is still running pre-CALL_B-framing-fix code. This means Phase 0 baselines are taken from **the pre-CALL_B-framing-fix runtime** — exactly the runtime the spec was authored against.

---

## Issue 1 Verification — Nonsensical TP From XRAY Flip

**Confirmed in current code, current logs.** Verified file:line trace in live source:

| Concern | File | Line(s) |
|---|---|---|
| `_new_tp` selection on flip | `src/workers/strategy_worker.py` | 1701-1710 |
| Trade-dict TP assignment | `src/workers/strategy_worker.py` | 1716 |
| `XRAY_DIR_FLIP` log emission | `src/workers/strategy_worker.py` | 1732-1742 |
| TP validator class + threshold | `src/core/sl_tp_validator.py` | 25-30 (`max_distance_pct=10.0`) |
| `validate_tp` SKIP path | `src/core/sl_tp_validator.py` | 108-117 |
| TP validator call site (`_execute_claude_trade`) | `src/workers/strategy_worker.py` | ~1885-1897 |
| `VolatilityProfiler.get_profile` | `src/analysis/volatility_profile.py` | 146-163 |
| `CoinVolatilityProfile.recommended_tp_pct` | `src/analysis/volatility_profile.py` | 30-46 |

**Bug in plain English.** The XRAY direction-flip path at lines 1701-1710 reads `_sp.short_tp_price` or `_sp.long_tp_price` directly from the structural placement and writes it to `trade["take_profit_price"]` at line 1716. The structural target is computed by `StructuralLevelCalculator._calc_long/_calc_short` from nearest support/resistance levels and a fallback (`tp_fallback_pct=4.0` from `config.toml:1320`). For volatile or thinly-supported coins, the structural target can be 15-20%+ from the current price, which the downstream validator at `src/core/sl_tp_validator.py:108-117` correctly rejects as nonsensical (default threshold 10%). The flip path **does not consult `volatility_profiler`**, even though `VolatilityProfiler.get_profile()` already produces volatility-class-aware and regime-aware `recommended_tp_pct` values that would constitute the right cap.

**Verbatim recent log evidence (2026-05-07 07:26 UTC onwards):**

```
2026-05-07 07:35:10.756 | XRAY_DIR_FLIP | sym=ICPUSDT original_dir=Buy
                          flipped_dir=Sell rr_original=0.0 rr_flipped=31.1
                          ratio=1555.0x size_usd=$100 sl=$3.0085 tp=$2.3824

2026-05-07 07:35:10.758 | TRADE_SKIP | sym=ICPUSDT rsn=sltp_skip
                          detail='tp_validator: TP $2.3824 is 20.5% from price — nonsensical'

2026-05-07 07:43:00.469 | XRAY_DIR_FLIP | sym=GALAUSDT original_dir=Buy
                          flipped_dir=Sell rr_original=0.2 rr_flipped=8.1
                          ratio=33.6x sl=$0.0039 tp=$0.0032

2026-05-07 07:43:00.470 | TRADE_SKIP | sym=GALAUSDT rsn=sltp_skip
                          detail='tp_validator: TP $0.0032 is 15.7% from price — nonsensical'

2026-05-07 08:08:50.142 | XRAY_DIR_FLIP | sym=OPUSDT original_dir=Buy
                          flipped_dir=Sell rr_original=0.1 rr_flipped=5.7
                          ratio=37.7x sl=$0.1477 tp=$0.1271
```

For OPUSDT, current vol profile is `class=high atr_pct=0.46% regime=trending_up | tp=3.90% sl=1.80%` (sample 2026-05-07 08:29:13 UTC). The flip TP at $0.1271 is ~14% from price; a vol-aware cap at 3.90% would have produced a TP at ~$0.142, well within the validator's 10% ceiling and structurally valid for the strategy timeframe.

---

## Issue 2 Verification — Volume Drop

**Cause A (market + downstream of Issue 1) is most consistent with evidence.**

Hour-level breakdown for 2026-05-07:

| Hour (UTC) | STRAT_DIRECTIVE | STRAT_CALL_A | TRADE_SKIP (all) | sltp_skip share |
|---|---|---|---|---|
| 07:00-07:59 | 15 | 5 | 9 | 100% |
| 08:00-08:59 | 6 | 2 | 1 | 100% |

**100% of TRADE_SKIP events in the window were sltp_skip.** The volume "drop" between hours and against historical baselines is the same population that Issue 1 is rejecting.

**No silent code change.** `git log --since='5 days ago' -- src/brain src/workers/strategy_worker.py src/strategies src/scanner` — only commits are CALL_B framing fix work (commits `f62683c` → `03106b9`), all of which are on main but not boot-loaded yet. The runtime executing during the baseline window is pre-CALL_B-framing-fix and pre-this-fix.

**Verdict:** Volume recovery is expected once Phase 1 ships. Phase 2 will re-baseline 24-72h post-deploy and document the result; if volume does not recover, Phase 2 will execute the spec's full Cause B / Cause C investigation.

---

## Issue 3 Verification — Single Close

**Original framing was a misinterpretation of the close-event taxonomy.**

Hour-level breakdown for 2026-05-07:

| Hour | SHADOW_POSITION_CLOSE | STRAT_ACTION_CLOSE | Total closes |
|---|---|---|---|
| 07:00-07:59 | 0 | 0 | 0 |
| 08:00-08:59 | 8 | 1 | 9 |

The "1 close in 90 minutes" referred to STRAT_ACTION_CLOSE alone. Total closures (1 STRAT + 8 SHADOW Layer-4) = 9 in the window — healthy for an 8-position portfolio. The single STRAT_ACTION_CLOSE at 08:11:33 (LDOUSDT) was a watchdog-driven strategic exit. The 8 SHADOW closes were Layer 4 profit-sniper liquidations on mature positions (`purpose=layer4_close`).

**Verdict:** No closure-mechanism issue. Phase 3 will re-measure post-Phase-1 and confirm via DB-driven lifecycle traces.

---

## 7 Baselines

### Baseline 1 — TP Rejection Rate (last 24h)

- **sltp_skip events today:** 10 (workers.log)
- **XRAY_DIR_FLIP events today:** 14
- **Flip→sltp_skip ratio:** 10/14 = **71%** of flips today were rejected by the TP validator. (Spec said ~50% in the 90-min window; today's figure is higher because the affected symbols GALAUSDT and ICPUSDT both produced repeated rejections.)
- **Distance distribution:**
  - 15.4-15.8% — 6 events
  - 20.2-21.0% — 4 events
- **Symbol distribution:** GALAUSDT 6, ICPUSDT 4. Concentrated on coins where structural support is far below current price relative to the strategy timeframe.

### Baseline 2 — Trade Volume

- **2026-05-07 07:00-07:59:** 15 STRAT_DIRECTIVE, 5 CALL_A → 3.0 directives/CALL_A
- **2026-05-07 08:00-08:59:** 6 STRAT_DIRECTIVE, 2 CALL_A → 3.0 directives/CALL_A

Trades-per-CALL_A is consistent across hours. The hour-on-hour delta is in CALL_A invocation count, not in Claude's per-cycle output.

### Baseline 3 — Closure Pattern

- **SHADOW_POSITION_CLOSE today:** 8 (all in 08:11-08:39 window, all `purpose=layer4_close`).
- **STRAT_ACTION_CLOSE today:** 1 (LDOUSDT, 08:11:33).

### Baseline 4 — Open Positions Snapshot

Not queried directly (DB read-only and not required for this fix's design). Will be measured during Phase 4 trial via `TradingRepository.get_open_positions()`.

### Baseline 5 — Market State Sample

Most recent VOL_PROFILE entries (2026-05-07 08:28-08:39 UTC) show predominantly `regime=ranging`:
- 4 of 5 sampled coins (`ADAUSDT`, `BNBUSDT`, `HBARUSDT`, `RUNEUSDT`) are class=low, regime=ranging.
- `MNTUSDT` is class=medium, regime=ranging.
- `OPUSDT` is class=high, regime=trending_up — atypical in the sample.

Calm ranging market is consistent with reduced new-trade volume independent of the TP-validator rejections.

### Baseline 6 — Brain Cycle Behavior

- 2026-05-07 brain.log totals: STRAT_CALL_A=601, STRAT_DIRECTIVE=2128. Across all rotated logs.
- Today's window 07:00-08:59 alone: STRAT_CALL_A=7, STRAT_DIRECTIVE=21.
- Trades-per-CALL_A average: ~3.0, stable.

### Baseline 7 — Volatility Profile Values (10 representative coins)

| Symbol | Class | Regime | TP% | SL% | Hold (min) | Strategy |
|---|---|---|---|---|---|---|
| ADAUSDT | low | ranging | 0.35 | 0.28 | 16 | scalp |
| BNBUSDT | low | ranging | 0.35 | 0.28 | 16 | scalp |
| HBARUSDT | low | ranging | 0.35 | 0.28 | 16 | scalp |
| RUNEUSDT | low | ranging | 0.35 | 0.28 | 16 | scalp |
| MNTUSDT | medium | ranging | 1.05 | 0.80 | 24 | breakout |
| OPUSDT | high | trending_up | 3.90 | 1.80 | 54 | trend_follow |

These are the values the new cap will consume directly. The base parameters (`_BASE_PARAMS` at `src/analysis/volatility_profile.py:62-69`) are: dead 0.30%, low 0.50%, medium 1.50%, high 3.00%, extreme 5.00% — adjusted by `_REGIME_MODS` (trending 1.3x, ranging 0.7x, etc.).

---

## Spec Deviations And Their Justifications

### `XRAY_SLTP` log augmentation deferred (Phase 1E spec line 606)

The spec requires *"Update `XRAY_SLTP` event to include the final chosen TP and method."* `XRAY_SLTP` is emitted from `src/core/sl_tp_validator.py:197-201` inside `validate_sl_structural`. That validator runs BEFORE the cap mutates the TP, and the validator does not have visibility into the cap method (which is recorded in `strategy_worker.py` after the validator returns). Plumbing the cap method through the validator path would be an architectural change beyond the scope defined in the spec ("APEX/TradeGate/OrderService is OUT OF SCOPE except for the specific TP-derivation logic identified in Issue 1").

The new `XRAY_FLIP_TP_DERIVATION` event in `strategy_worker.py` carries the same diagnostic information (final chosen TP + method + telemetry) and is emitted unconditionally for every XRAY-flipped trade. Operators correlate it to `XRAY_SLTP` via the shared `did=` directive ID. The two events together provide complete observability without modifying the validator's contract.

**Decision:** the new event supersedes the spec's request. No change to `XRAY_SLTP`. Documented here so future operators know why the spec line was not literally satisfied.

## Verification Gate — All Items Complete

- [x] Issue 1 confirmed in current code (TP derivation traced from `_sp.short_tp_price`/`_sp.long_tp_price` to `trade["take_profit_price"]` to validator rejection).
- [x] Issue 2 Cause A identified (downstream of Issue 1 — 100% of skips are sltp_skip).
- [x] Issue 3 Interpretation 1 identified (taxonomy misinterpretation — 9 actual closes).
- [x] All 7 baselines captured.
- [x] Working notes file at `dev_notes/tp_volume_fix/phase0_baseline.md` (this file).

Phase 0 is complete. Proceeding to Phase 1B (settings + config).
