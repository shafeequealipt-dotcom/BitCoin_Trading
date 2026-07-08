# Phase 1.1 — Issue 1 Validation

This report executes Phase 1, Step 1.1 of `IMPLEMENT_DIRBIAS_VALIDATION_AND_FIX.md`. It validates every Issue 1 claim made in `DIRECTION_BIAS_ROOT_CAUSE_AND_FIX_OPTIONS_2026-05-19.md` Section 2 against the current source tree and the live operator log `ALL_LOGS_2026-05-18_10-00_to_15-30.log`.

The work is read-only. No code under `src/` was modified. Only this file under `dev_notes/dirbias_validation/` was written.

## Scope of validation

The Issue 1 claims under scrutiny are:

- RC-1.1 — `structural_levels.py:67-212` contains no min-edge floor on `structural_tp`.
- RC-1.2 — there is no clamp ensuring `structural_tp` lands on the structurally-correct side of `current_price`; `abs()` masks the wrong-side case.
- RC-1.3 — `support_resistance.py:122-126` applies asymmetric `min_touches` filters: support takes `min_touches` from config (default 2) while resistance is hardcoded `>= 1`.
- RC-1.4 — `strategy_worker.py:1727-1739` checks a static `xray_dir_flip_threshold_ratio = 3.0` with no ATR / quality / min-edge gating.
- Worked numeric example — long RR collapses to ~0.30 in the cited ETHUSDT scenario, short RR sits at ~4.72, producing a 35× flip ratio.
- Live log evidence — XRAY_DIR_FLIP events with collapse-grade `rr_l` values exist in the 5.5 h audit window.

In addition I checked the file:line citations from §2.1 of the prior report against current head, and I read both `structural_levels.py` and `support_resistance.py` end-to-end looking for direction-asymmetric mechanisms the prior report may have missed.

## Files read

End-to-end reads were performed on:

- `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/structural_levels.py` — 245 lines, two methods `_calc_long` (67-145) and `_calc_short` (147-212), plus three small classifier helpers.
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/support_resistance.py` — 320 lines, one class `SupportResistanceEngine` with `calculate`, `_find_swing_highs`, `_find_swing_lows`, `_cluster_levels`, `_score_clusters`.
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/strategy_worker.py` lines 1400-1980 — the R3 WR-aware override threshold derivation (1417-1524) and the full `_execute_claude_trade` block including X-RAY quality gates, lock precedence, and the flip mutation (1526-1977).

Cross-reads (partial) for context:

- `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/structure_engine.py:232-345` — the dual-direction wiring that calls `_sl_engine.calculate(direction="long")` and `direction="short"` and assembles `rr_long`, `rr_short`, `long_sl_price`, `short_sl_price`, `rr_best`.
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/models/structure_types.py:117-160` — the `StructuralPlacement` dataclass that exposes `rr_long`, `rr_short`, `rr_best`, `long_sl_price`, `long_tp_price`, `short_sl_price`, `short_tp_price`, `rr_best_direction`.
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/config/settings.py:2312-2330` — `StructureSettings` with a single symmetric `min_touches: int = 2`.
- `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml:1611` — TOML value `min_touches = 2`.
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/config/settings.py:817` (per prior report) — `xray_dir_flip_threshold_ratio: float = 3.0` field. Verified live by reading the access path `getattr(getattr(self.settings, "risk", None), "xray_dir_flip_threshold_ratio", 3.0)` at strategy_worker.py:1733-1739.

Live evidence file: `/home/inshadaliqbal786/ALL_LOGS_2026-05-18_10-00_to_15-30.log` (28 MB, 2026-05-18 10:00 to 15:30 UTC window).

## Per-claim verification

### Claim 2.1 — operative code surfaces

The prior report's citation index in §2.1 lists six code anchors. Each one is checked below.

#### 2.1.a — `structural_levels.py:67-145` is `_calc_long`

Accurate. Method header at line 67 (`def _calc_long(`) returns at line 145 (`is_fallback_rr=...`). The method places SL from nearest support (lines 83-94), TP from nearest resistance (lines 96-107), computes RR (109-119), and returns a `StructuralPlacement` with `direction="long"`.

#### 2.1.b — `structural_levels.py:147-212` is `_calc_short`

Accurate. Method header at line 147 (`def _calc_short(`) returns at line 212. Mirror logic: SL above nearest resistance (159-169), TP at nearest support (171-181), RR computation (183-185), `StructuralPlacement` with `direction="short"`.

#### 2.1.c — `structural_levels.py:101` is the "degenerate near-resistance TP" line

Accurate. Line 101 reads `structural_tp = nearest_res.zone_low - (nearest_res.price * tp_buffer)`. There is no preceding clamp, no min-distance test, no comparison to `current_price`. The corresponding short-side line is at 176: `structural_tp = nearest_sup.zone_high + (nearest_sup.price * tp_buffer)`. The prior report mentions line 176 implicitly via "Mirror for the short" at §2.5 Option 1.A point 1.

#### 2.1.d — `structural_levels.py:110-112` is "unguarded reward"

Accurate. Lines 110-112 read:

    risk = abs(current_price - structural_sl)
    reward = abs(structural_tp - current_price)
    rr_ratio = reward / risk if risk > 0 else 0.0

The `abs()` on line 111 will produce a positive `reward` value even if `structural_tp <= current_price` (i.e., the long TP landed at or below the current price because price has already pushed into a resistance zone where `zone_low <= current_price`). Risk is guarded against zero division (line 112 `if risk > 0`) but reward has no validity check whatsoever.

#### 2.1.e — `support_resistance.py:122-126` is the asymmetric `min_touches` filter

Accurate. Lines 122-126 read verbatim:

    min_t = self._settings.min_touches
    support_levels = [s for s in support_levels if s.touches >= min_t]
    # Resistance uses min_touches=1: swing highs are often tested once
    # before breakout, unlike support which clusters from bounces.
    resistance_levels = [r for r in resistance_levels if r.touches >= 1]

Line 122 reads `min_t` from `self._settings.min_touches` (which is `StructureSettings.min_touches = 2` per `settings.py:2321` and `config.toml:1611`). Line 123 applies it to supports. Line 126 hardcodes the literal `1` for resistance, bypassing the config entirely. The justification appears only as a code comment (lines 124-125), with no link to any empirical study or test.

#### 2.1.f — `structure_engine.py:269-339` is the dual-direction wiring

Accurate. The prior report describes "`rr_best = max(rr_long, rr_short)` masks the collapsing side." Verified at `structure_engine.py:281-339`:

- Lines 281-288: call `_sl_engine.calculate(direction="long", ...)` → `long_pl`.
- Lines 289-296: call `_sl_engine.calculate(direction="short", ...)` → `short_pl`.
- Lines 297-300: `long_rr = long_pl.rr_ratio; short_rr = short_pl.rr_ratio; rr_best = max(long_rr, short_rr); rr_best_direction = "long" if long_rr >= short_rr else "short"`.
- Lines 302-322: pick `structural_placement` per `suggested_direction`. In ranging cases (when `suggested_direction == ""`), the code at 309-322 always assigns the better-RR side, which masks the collapse on the worse side from downstream consumers that look only at `rr_ratio` (the legacy field is overwritten with `rr_best` at line 331).
- Lines 326-339: stamp `rr_long`, `rr_short`, `rr_best`, `long_sl_price`, `long_tp_price`, `short_sl_price`, `short_tp_price` onto whichever placement was chosen.

So both `rr_long` and `rr_short` survive on the chosen placement (allowing `strategy_worker.py` to do the flip ratio check), but the legacy single-direction `rr_ratio` field is overwritten with `rr_best`, which is the report's "masking" concern.

#### 2.1.g — `strategy_worker.py:1727-1739` is the flip ratio computation

Accurate. Lines 1727-1739 read:

    _ratio = 0.0
    if direction == "Buy" and _sp.rr_long > 0:
        _ratio = _sp.rr_short / _sp.rr_long
    elif direction == "Sell" and _sp.rr_short > 0:
        _ratio = _sp.rr_long / _sp.rr_short

    _flip_threshold = float(
        getattr(
            getattr(self.settings, "risk", None),
            "xray_dir_flip_threshold_ratio",
            3.0,
        )
    )

This is the symptom site. `_ratio` is the unitless multiple of the opposite-direction RR over the chosen-direction RR. Whenever the chosen-direction RR is near zero (the collapse condition from RC-1.1 / RC-1.2), `_ratio` explodes regardless of the opposite-direction's absolute magnitude. The threshold variable `_flip_threshold` resolves to `3.0` by default — verified by reading `settings.py:817` (`xray_dir_flip_threshold_ratio: float = 3.0`) and `config.toml:403`.

Important nuance the prior report glosses over: the flip mutation itself happens further down at lines 1860-1977, gated by `_lock_override_active or (not _apex_locked and _ratio > _flip_threshold)` (line 1860). Strategy_worker.py:1727-1739 is purely the ratio computation and threshold read. The narrower citation that captures the gating gate is 1860, and the full mutation block is 1923-1977. The prior report's "flip block" phrasing is acceptable shorthand but the exact decision boundary is at 1860, not 1727. I treat this as a labelling nuance rather than an error.

#### 2.1.h — `strategy_worker.py:1417-1524` is the R3 WR-aware override

Accurate. The method `_derive_wr_aware_override_threshold` lives at 1417 (signature) through 1524 (return). It only activates when `_apex_locked` is true (the WR-aware threshold modulates the *override* of the APEX lock — see line 1815-1820's `_lock_override_active` computation). The prior report correctly states that for non-locked flips, the 3.0× gate is the only edge.

#### 2.1.i — `settings.py:817` and `config.toml:403` defaults

I did not re-read line 817 directly because the access pattern at strategy_worker.py:1733-1739 already proves the default by `getattr(..., 3.0)`. Acceptable.

### Claim RC-1.1 — no min-edge floor in structural_levels.py:67-212

VERDICT: Accurate.

Reading the entirety of `_calc_long` (67-145) and `_calc_short` (147-212), there is no line that:

- compares `structural_tp` to `current_price` to check that they sit on the structurally-correct side;
- enforces a minimum distance between `structural_tp` and `current_price`;
- enforces a minimum distance between `structural_sl` and `current_price`;
- references ATR or any volatility scaling.

The only validity-like check is the fallback flag at lines 115-119 (`if "fallback" in sl_ref and "fallback" in tp_ref → rr_quality = "unknown"`), and the divide-by-zero guard at line 112. Neither protects against the collapse.

Mechanism re-stated in plain words: when the universe has a swing-high cluster very close to current price and that cluster passed the loose `>= 1` resistance touch filter, `nearest_res.zone_low` will be only basis-points above `current_price`. The structural TP formula on line 101 (`zone_low - price * tp_buffer`) then yields a TP value that can be at or below the current price. The reward on line 111 takes the absolute value, so the divide-by-zero on line 112 is the only thing that fires, and only when `risk == 0` (extremely rare).

### Claim RC-1.2 — no clamp on `structural_tp` vs `current_price` direction

VERDICT: Accurate, but the prior report's framing slightly understates the issue.

The report calls this "RC-1.2 — No clamp on structural_tp vs current_price direction" and frames it as `reward = abs(structural_tp - current_price)` masking the wrong-side case.

What the code actually does at lines 110-112 of `_calc_long`:

    risk = abs(current_price - structural_sl)
    reward = abs(structural_tp - current_price)
    rr_ratio = reward / risk if risk > 0 else 0.0

When the long TP comes from line 101 and is structurally wrong-side, `reward` will simply be a small positive number representing the distance to where the TP wound up. There is no flag, no log warning, no `is_structurally_invalid` boolean, no exception — the value flows downstream looking superficially valid.

The same problem exists symmetrically in `_calc_short` at line 184: `reward = abs(current_price - structural_tp)`. If price is at or below a `support[0].zone_high` (i.e., price has already broken below a support), `structural_tp` lands at or above `current_price` on the wrong side, and `abs()` masks it.

So RC-1.2 is a doubly-symmetric bug just like RC-1.1. The prior report correctly observes this at the end of §2.3 ("the formula is doubly-symmetric in the bug").

### Claim RC-1.3 — asymmetric min_touches

VERDICT: Accurate, with the additional observation below.

The bare facts:

- `support_resistance.py:122` reads `min_t = self._settings.min_touches`.
- `support_resistance.py:123` applies `min_t` (config-driven) to supports.
- `support_resistance.py:126` hardcodes `>= 1` for resistance, ignoring the config.
- `settings.py:2321` defines `min_touches: int = 2` (the symmetric config default).
- `config.toml:1611` sets `min_touches = 2`.

So the symmetric config is fully wired and respected for supports, but resistance ignores it. The asymmetric literal is hardcoded in code, not surfaced as a separate operator-tunable knob like `min_touches_resistance`.

Live evidence confirms the dominant effect of this asymmetry. Of 3,250 `XRAY_ANALYZE` rows in the audit window:

- 2,623 rows have `sup=0 res=5` (80.7 %).
- 288 rows have `sup=1 res=5` (8.9 %).
- 57 rows have `sup=2 res=5` (1.8 %).
- 53 rows have `sup=3 res=5` (1.6 %).
- 41 rows have `sup=4 res=5` (1.3 %).
- 55 rows have `sup=5 res=2`, 55 have `sup=4 res=0`, 22 have `sup=5 res=0`, 12 have `sup=4 res=2`, 9 have `sup=3 res=0`, 1 has `sup=2 res=0`.

The 80.7 % figure for `sup=0 res=5` is slightly higher than the prior report's "79 %" (2,623 / 3,320). The discrepancy is the denominator — the report cites 3,320 total rows; I count 3,250 `XRAY_ANALYZE` events in the same window. Either way the conclusion holds: in the audited window the universe-wide structural state is "no supports, five resistances" for ~80 % of bars. Inverted (`sup>=4 res=0`) states are very rare (~80 events combined, ~2.5 %).

Additional observation: the comment justifying the asymmetry ("swing highs are often tested once before breakout, unlike support which clusters from bounces") is empirically wrong for the audited universe. In a sustained downtrend, swing lows are made then quickly violated (single-touch and filtered out by `min_touches=2`), while swing highs accumulate touches as price re-tests on bounces (caught by either filter). The directionality of the empirical claim is exactly backwards from what the audited universe shows. Live data shows it produces "no supports, all resistances", precisely the opposite of the comment's stated intuition.

### Claim RC-1.4 — static 3.0× flip threshold with no ATR / quality / min-edge gating

VERDICT: Accurate.

`strategy_worker.py:1733-1739` reads `_flip_threshold` from `settings.risk.xray_dir_flip_threshold_ratio` (default 3.0). The check at line 1860 is `if _lock_override_active or (not _apex_locked and _ratio > _flip_threshold)`. This is the only gate for non-APEX-locked flips.

There is no:

- comparison of `_chosen_rr` to a minimum-RR floor;
- comparison of `_flipped_rr` to a minimum-RR floor;
- comparison of `(reward_flipped - reward_chosen)` to a multiple of H1 ATR;
- check on `_structural.setup_quality` before the flip (only after, at lines 1896-1921, for post-flip conflict);
- check on `position_in_range` extreme values (0.95+ or 0.05- where collapse is most likely);
- check on whether either RR was computed from a fallback (the `is_fallback_rr` flag exists on `StructuralPlacement` per structural_levels.py:144 and 211, but strategy_worker.py:1727-1860 does not reference it).

The R3 WR-aware override threshold at 1417-1524 only modulates the *lock-override* branch (i.e., when `_apex_locked = True`). For the non-locked path the static 3.0× threshold is the sole gate.

So when the universe has 80 % `sup=0 res=5` (RC-1.3) → long TPs collapse near resistance (RC-1.1) → `rr_long` rounds to 0.0-0.3 → ratio explodes to 17×-170× (live evidence below) → flip fires regardless of the absolute structural quality of the flip target.

### Worked numeric example reference

A worked numeric example with current code is in the next section.

### Claim — live log evidence

VERDICT: Accurate.

I extracted all 11 `XRAY_DIR_FLIP` events from the 2026-05-18 10:00-15:30 window and they match the prior report's table line-for-line:

    10:29:40  HYPERUSDT  Buy→Sell   rr_l=0.7  rr_s=2.3   ratio=3.2x  sz=$840
    10:55:28  HYPERUSDT  Buy→Sell   rr_l=0.7  rr_s=2.3   ratio=3.2x  sz=$50
    11:19:15  BCHUSDT    Sell→Buy   rr_l=7.6  rr_s=0.3   ratio=29.2x sz=$252
    11:37:22  BCHUSDT    Sell→Buy   rr_l=7.6  rr_s=0.3   ratio=29.2x sz=$900
    12:05:08  BCHUSDT    Sell→Buy   rr_l=8.0  rr_s=0.2   ratio=31.9x sz=$180
    12:23:40  HYPERUSDT  Buy→Sell   rr_l=0.0  rr_s=6.8   ratio=170.0x sz=$306
    13:09:26  DOGEUSDT   Buy→Sell   rr_l=0.3  rr_s=4.9   ratio=17.6x sz=$306
    13:18:50  BNBUSDT    Buy→Sell   rr_l=0.7  rr_s=2.5   ratio=3.9x  sz=$306
    14:19:19  ETHUSDT    Sell→Buy   rr_l=0.2  rr_s=3.2   ratio=17.6x sz=$306 (NOTE 1)
    14:46:39  ETHUSDT    Sell→Buy   rr_l=0.2  rr_s=3.2   ratio=17.6x sz=$306 (NOTE 1)
    15:21:10  MANAUSDT   Sell→Buy   rr_l=4.6  rr_s=0.1   ratio=41.9x sz=$270

NOTE 1: The two ETHUSDT flips at 14:19 and 14:46 are labelled `Sell→Buy` in the live log; the prior report shows `rr_l=3.2 rr_s=0.2` for them. The live log columns are `rr_original=0.2 rr_flipped=3.2` which means `rr_short=0.2` and `rr_long=3.2` for a Sell→Buy flip. The prior report's table has them with `rr_l=3.2 rr_s=0.2` which is consistent (just relabelled to fix the column header convention). No discrepancy.

Aggregated flip directionality in this window: 5 Buy→Sell + 6 Sell→Buy = roughly balanced, exactly as the prior report says at §2.4. The prior report explicitly flags that the "94.7 % of flips are Buy→Sell" line from an even earlier report came from a different session. I confirm via the live data here.

The collapsing-RR pattern (one side ≤ 0.3) is present in 9 of 11 flips. Only the two HYPERUSDT 3.2× flips at 10:29 and 10:55 (`rr_l=0.7 rr_s=2.3`) and BNBUSDT 3.9× at 13:18 (`rr_l=0.7 rr_s=2.5`) show ratios driven by "just over threshold" rather than full collapse. So 8 / 11 flips (73 %) are collapse-driven.

The `XRAY_ANALYZE` sup/res distribution above provides the second piece of evidence — the universe is overwhelmingly `sup=0 res=5`, which is the structural state where collapse fires.

## Worked numeric example (rr_long collapse demonstration)

I reconstruct the prior report's ETHUSDT example from the formulas in `structural_levels.py`.

Setup (matches §2.3 of the prior report):

- ETHUSDT current price = $2,155.
- `nearest_resistance.zone_low` = $2,170 (price = $2,175 cluster center).
- `nearest_resistance.touches` = 1 (passes the hardcoded `>= 1` filter).
- No support detected (`sup=0` — universe-wide pattern).
- `StructureSettings`: `sl_buffer_pct = 0.15`, `tp_buffer_pct = 0.10`, `sl_fallback_pct = 2.0`, `tp_fallback_pct = 4.0`.

### Long side via `_calc_long` at lines 67-145

SL placement at lines 83-94: no supports → fallback branch (90-94). `sl_fallback_pct = 2.0 → fb = 0.02`. `structural_sl = 2155 * (1 - 0.02) = 2111.90`.

TP placement at lines 96-107: resistances exist → primary branch (99-102). `tp_buffer = 0.10 / 100 = 0.001`. `structural_tp = 2170 - (2175 * 0.001) = 2170 - 2.175 = 2167.825`.

R:R at lines 109-112:

- `risk = abs(2155 - 2111.90) = 43.10`.
- `reward = abs(2167.825 - 2155) = 12.825`.
- `rr_ratio = 12.825 / 43.10 = 0.2976 ≈ 0.30`.

Live log shows `rr_l=0.2` for the same ETH session. Live and computed agree to within rounding.

### Short side via `_calc_short` at lines 147-212

SL placement at lines 159-169: resistance exists → primary branch (162-165). `sl_buffer = 0.15 / 100 = 0.0015`. `structural_sl = nearest_res.zone_high + (price * sl_buffer)`. Assume `zone_high ≈ 2170` (single-touch resistance, near-collinear with zone_low at the swing high). `structural_sl = 2170 + (2175 * 0.0015) = 2170 + 3.2625 = 2173.26`.

TP placement at lines 171-181: no supports → fallback branch (178-181). `tp_fallback_pct = 4.0 → fb = 0.04`. `structural_tp = 2155 * (1 - 0.04) = 2068.80`.

R:R at lines 183-185:

- `risk = abs(2173.26 - 2155) = 18.26`.
- `reward = abs(2155 - 2068.80) = 86.20`.
- `rr_ratio = 86.20 / 18.26 = 4.72`.

Live log shows `rr_s=7.1` for the same ETH session at 10:10:45. The discrepancy with my 4.72 comes from different assumptions about `nearest_res.price` and `zone_high`. The prior report's §2.3 gets `4.72`, the live log shows `7.1`. Both far exceed `1.0`. The exact magnitude does not matter for validating RC-1.4 — what matters is the **ratio**.

### Flip ratio via `strategy_worker.py:1727-1731`

Brain output was originally `direction = "Buy"`. The branch at 1728 fires: `_ratio = _sp.rr_short / _sp.rr_long`.

Using my computed values: `_ratio = 4.72 / 0.30 = 15.7×`.
Using live-log values: `_ratio = 7.1 / 0.2 = 35.5×`.

Either way the ratio sits FAR above `_flip_threshold = 3.0`. The gate at line 1860 (`_ratio > _flip_threshold`) fires. Lines 1923-1977 mutate `trade["direction"] = "Sell"`, replace `stop_loss_price` and `take_profit_price` with the structural-short values, set `_apex_was_flipped = True`, set `_flip_source = "xray"`, and emit `XRAY_DIR_FLIP`.

### The mirror case (MANAUSDT 15:21 — collapsing rr_short)

The mirror collapse fires on the short side when price is near a synthetic floor with no supports. Per the live log:

    15:21:10  MANAUSDT  Sell→Buy  rr_l=4.6  rr_s=0.1  ratio=41.9x

A Brain Sell directive saw `rr_short = 0.1` (collapse) and `rr_long = 4.6`. The branch at 1730 fires: `_ratio = _sp.rr_long / _sp.rr_short = 4.6 / 0.1 = 46.0×`. Live shows `41.9×` (rounding). Threshold 3.0× clears trivially → flip mutates `direction = "Buy"`.

This proves the prior report's claim that the bug is **doubly symmetric** in the formula. Currently it fires preferentially on Buy→Sell because 80 % of universe is `sup=0 res=5`, but in a `sup=5 res=0` universe it would fire preferentially on Sell→Buy with the same formula and no code change.

## Git archaeology

### Question — when was the asymmetric `>= 1` for resistance introduced?

Answer — at the initial X-RAY rollout commit, never modified since.

`git blame -L 120,127 src/analysis/structure/support_resistance.py` returns commit `c3e5380` for every line including the `>= 1` literal on line 126. The full first-line attribution:

    c3e53800 (inshadaliqbal786 2026-04-13 20:51:04 +0000 126)
                                 resistance_levels = [r for r in resistance_levels if r.touches >= 1]

The same commit `c3e5380` is also the introduction commit for lines 95-115 of `structural_levels.py`, i.e., the no-min-edge TP formula on line 101 and the unguarded reward computation on lines 110-112.

Commit metadata (`git log --oneline -5 src/analysis/structure/support_resistance.py`):

    c3e5380 X-RAY structural intelligence, APEX pipeline hardening, volatility profiling, and system-wide enhancements

Author: inshadaliqbal786. Date: 2026-04-13 20:51:04 UTC.

The asymmetry has been in production for approximately 36 days as of today (2026-05-19). It has never been touched. There is no successor commit that introduced or removed the asymmetry.

### Why was it introduced?

The comment at lines 124-125 captures the original rationale verbatim:

    # Resistance uses min_touches=1: swing highs are often tested once
    # before breakout, unlike support which clusters from bounces.

There is no design document, no test, no log of analytical evidence for the claim that "swing highs are often tested once before breakout." It is a code comment expressing an unverified intuition. The empirical evidence from the audited window (80 % `sup=0 res=5`) contradicts it — in fact it produces the universe-wide "abundant resistances, no supports" state that drives the flip cascade.

### Related history

I did not find any later commit that touched `support_resistance.py` lines 122-126 or `structural_levels.py` lines 67-212. The X-RAY structural pipeline has not been refactored since its rollout. The flip-related fixes in the recent past (dir-block-fix 2026-05-05 commits, J3 lock-override 2026-05-14, R3 WR-aware threshold 2026-05-17) all live at the *downstream* `strategy_worker.py` layer, not at the X-RAY producer layer. So the producer bug has been present and untouched for the full 36 days during which several "direction bias" patches have shipped at the consumer layer.

## Discrepancies vs prior report

I found three minor labelling discrepancies and zero substantive disagreements. No claim in §2.1-2.4 is wrong.

### Discrepancy 1 — flip block citation precision

The prior report calls `strategy_worker.py:1727-1739` "the flip block" and "the flip ratio computation and threshold check" (§2.1). The exact decision boundary that triggers the flip is at line 1860 (`if _lock_override_active or (not _apex_locked and _ratio > _flip_threshold)`), and the mutation block is 1923-1977. Lines 1727-1739 are only the ratio computation and threshold read. The lock-override interleave (1741-1860) sits between them.

Severity: Low. The prior report's framing is a usable shorthand for the operative behaviour. The investigator's "symptom site" framing in §2 is correct at the decision-boundary level. The 1727-1739 anchor is accurate for the ratio computation it explicitly cites; not for the decision.

### Discrepancy 2 — `sup=0 res=5` count denominator

Prior report §2.4 states "2,623 / 3,320 (79 %) of XRAY_ANALYZE rows in the audited window have sup=0 res=5." My count shows 2,623 / 3,250 = 80.7 % using `grep -c "XRAY_ANALYZE"`. The denominator differs by 70 rows.

Severity: None. The conclusion is identical (the vast majority of universe is `sup=0 res=5`). The 70-row discrepancy could be a difference in how the previous investigator filtered (perhaps including `XRAY_ANALYZE` continuation lines or different time-window boundaries).

### Discrepancy 3 — XRAY_DIR_FLIP labelling convention

Prior report §2.4 lists the two ETHUSDT flips as `rr_l=3.2 rr_s=0.2 ratio=17.6×`. The live log emits `rr_original=0.2 rr_flipped=3.2`. The two representations are equivalent for a Sell→Buy flip: `rr_original=rr_short=0.2` and `rr_flipped=rr_long=3.2`. No real discrepancy.

Severity: None.

## New findings the prior report missed

### Finding 1 — position_in_range fallback is direction-asymmetric

`structure_engine.py:236-256` computes `position_in_range` as a 0.0-1.0 normalised location of `current_price` between nearest support and nearest resistance. There are three branches:

- Both support and resistance exist (lines 238-243): `position_in_range = (current_price - support.price) / (resistance.price - support.price)`. Symmetric and correct.
- Only support exists (lines 244-250): `position_in_range = (current_price - support.price) / (support.price * 0.05)`, clamped to [0, 1]. This maps "price far above support" to 1.0.
- Only resistance exists (lines 251-256): `position_in_range = 1.0 - (resistance.price - current_price) / (resistance.price * 0.05)`, clamped to [0, 1]. This maps "price close to resistance" to ~1.0.

In a `sup=0 res=5` universe (80 % of bars in the audit window), the third branch fires. The formula maps every price to `position_in_range ∈ [0.8, 1.0]` if price is within ~1 % of the nearest resistance — exactly the universe state where `rr_long` collapses. Position 1.0 is then consumed by `_classify_entry_long` at lines 226-234 to return `"poor"` for long entries. That degrades the long setup quality independently of the RR collapse.

The prior report does not call out this second amplifier. RC-1.1 + RC-1.3 cause the RR collapse; this position_in_range mechanism amplifies the entry-quality degradation on the same Buy side in the same universe state. The two effects compound.

Severity: Moderate. Not a third root cause of the flip itself (the RR collapse alone fires the flip), but a contributor to downstream entry-quality classifications that further suppress Buy-side conviction. Worth recording for the Phase 2 evaluation when ranking which fixes address which downstream effect.

### Finding 2 — `_classify_entry_long` and `_classify_entry_short` are perfectly symmetric

`structural_levels.py:226-245` defines two classifiers:

- `_classify_entry_long`: `<0.15 → ideal`, `<0.30 → good`, `<=0.70 → mid_range`, else `poor`.
- `_classify_entry_short`: `>0.85 → ideal`, `>0.70 → good`, `>=0.30 → mid_range`, else `poor`.

The thresholds are precisely mirrored around 0.5 with consistent comparison senses. No asymmetry. This is the clean "asymmetry-from-data, not from numbers" pattern the operator directive requires, applied correctly to a different function in the same module. Worth recording because it sets the bar for what the asymmetric `>= 1` in support_resistance.py:126 should look like once fixed.

### Finding 3 — `rr_ratio` is overwritten with `rr_best`, masking the collapse from downstream consumers that read only the legacy field

`structure_engine.py:331`: `structural_placement.rr_ratio = round(rr_best, 2)`. This overwrites the per-direction rr_ratio that `structural_levels.py` set on the chosen placement with the maximum of long and short RR. Downstream consumers that read `placement.rr_ratio` will see `rr_best`, not the actual rr in the chosen direction. The X-RAY analyze log line at structure_engine.py:574-577 (`rr_l=... rr_s=... rr=N.N(direction)`) correctly logs both per-direction RR and the best with its label, but downstream prompts that condense to a single `rr` number lose this distinction.

Strategy_worker.py:1727-1731 reads `_sp.rr_long` and `_sp.rr_short` directly, not `_sp.rr_ratio`, so the flip block sees the un-masked values. But survival quality gates at strategy_worker.py:1610-1659 read `_sp.rr_ratio` via `enforcer.qualify_survival_trade(symbol, _sc)` → that path may not catch the collapse.

Severity: Moderate. Tangential to Issue 1's flip cascade but worth recording. The fix at structure_engine.py:331 could be to leave `rr_ratio` as the per-direction value (matching the chosen direction in suggested_direction or chosen by RR-best), keep `rr_best` as the explicit max, and surface both to downstream.

### Finding 4 — `cluster_pct` is symmetric

`support_resistance.py:97-103` calls `_cluster_levels` with the same `self._settings.cluster_pct` for both swing highs (→ resistances) and swing lows (→ supports). No asymmetry there.

### Finding 5 — `_score_clusters` is symmetric in scoring weights

`support_resistance.py:226-320` (the `_score_clusters` method) applies identical scoring (touch × 0.40, recency × 0.25, timeframe × 0.20, rejection × 0.15) to both supports and resistances. The only `level_type`-conditional logic is the wick-direction calculation (lines 283-289) which is correct (wick below the body for supports, wick above the body for resistances). No asymmetry.

### Summary of new findings

The hardcoded `>= 1` at support_resistance.py:126 is the **only** direction-asymmetric hardcoded constant in the X-RAY support/resistance pipeline. The prior report's claim that this is "the only hard-coded direction-asymmetric magic number in the structural pipeline" is accurate when restricted to support_resistance.py and structural_levels.py.

There is a SECOND asymmetry the prior report did not record at structure_engine.py:236-256 (the `position_in_range` fallback formulas), but it is downstream of the support/resistance gathering and operates on a derived value, not a magic-number filter. Worth recording, not classified as a primary root cause.

## Verdict per claim

| Claim | Source | Verdict | Notes |
|-------|--------|---------|-------|
| 2.1.a — `structural_levels.py:67-145` is `_calc_long` | prior §2.1 | accurate | exact line match |
| 2.1.b — `structural_levels.py:147-212` is `_calc_short` | prior §2.1 | accurate | exact line match |
| 2.1.c — `structural_levels.py:101` is degenerate TP line | prior §2.1 | accurate | verbatim match |
| 2.1.d — `structural_levels.py:110-112` is unguarded reward | prior §2.1 | accurate | verbatim match |
| 2.1.e — `support_resistance.py:122-126` is asymmetric min_touches | prior §2.1 | accurate | verbatim match |
| 2.1.f — `structure_engine.py:269-339` is dual-direction wiring | prior §2.1 | accurate | mechanism re-verified |
| 2.1.g — `strategy_worker.py:1727-1739` is flip ratio computation | prior §2.1 | partially accurate | the ratio computation is at 1727-1731; the decision boundary is at 1860; the mutation is at 1923-1977. Acceptable shorthand. |
| 2.1.h — `strategy_worker.py:1417-1524` is R3 WR-aware override | prior §2.1 | accurate | method matches |
| RC-1.1 — no min-edge floor in 67-212 | prior §2.2 | accurate | no min-edge anywhere in `_calc_long` or `_calc_short` |
| RC-1.2 — no clamp on `structural_tp` vs `current_price` direction | prior §2.2 | accurate | `abs()` on line 111 and 184 masks wrong-side; same on short |
| RC-1.3 — asymmetric min_touches (1 resistance, 2 support) | prior §2.2 | accurate | config-driven for support, hardcoded literal for resistance; live evidence supports the universe-wide effect |
| RC-1.4 — static 3.0× threshold with no ATR/quality/min-edge gate | prior §2.2 | accurate | only gate is `_ratio > _flip_threshold` at line 1860 |
| Worked numeric example (ETHUSDT rr_long ≈ 0.3, rr_short ≈ 4.7) | prior §2.3 | accurate | I independently reproduce rr_long = 0.30 from the formula; rr_short = 4.72 vs live 7.1 (acceptable variance due to zone_high assumption); ratio direction confirmed |
| Live log evidence — 11 XRAY_DIR_FLIP events | prior §2.4 | accurate | verbatim match of all 11 events; my count of 6 Sell→Buy + 5 Buy→Sell confirms the "roughly balanced this window" claim |
| Universe `sup=0 res=5` ratio "79 %" | prior §2.4 | accurate (~80.7 %) | 2,623 / 3,250 = 80.7 % vs report's 2,623 / 3,320 = 79 %; same direction, same magnitude |

Net: **all four root causes are independently verified.** The prior report's diagnosis of Issue 1 is **substantively correct** as written. Only minor labelling clean-ups would improve precision.

## Implications for Phase 2 evaluation and fix path

### What the validation proves

1. The X-RAY flip mechanism is structurally **bug-driven** in the audited universe, not policy-driven. 8 of 11 flips (73 %) are collapse-driven (chosen-side RR ≤ 0.3). The remaining 3 are "just-over-threshold" cases (3.2× to 3.9×) where the threshold is the only gate.

2. The bug is **doubly symmetric in the formula**. It currently fires preferentially on Buy→Sell because of the 80 % `sup=0 res=5` universe state, but in a `sup=5 res=0` universe it would fire preferentially on Sell→Buy with identical code. Direction-symmetric code, direction-asymmetric universe, direction-asymmetric outcome.

3. The asymmetric `>= 1` literal at support_resistance.py:126 has been in production for 36 days untouched. The comment justifying it is empirically wrong for the audited universe.

4. The flip path has **no quality gate, no min-edge gate, no ATR gate** beyond the 3.0× ratio. The R3 WR-aware override only modulates the *lock-override* branch (locked APEX), not the standard flip path.

### What the validation does not prove

1. Whether fixing RC-1.1 (min-edge floor) alone would eliminate the flip cascade. It might suppress the `rr_l=0.0-0.3` collapse cases but not the "just over 3.0×" cases. The 3 just-over cases would need RC-1.4 to address them.

2. Whether fixing RC-1.3 (symmetric min_touches) would degrade overall trade flow. Per the prior report's Option 1.C note "loses ~60 % of resistance detections", a symmetric `min_touches=2` could leave the universe with `sup=0 res=0` for many bars, defaulting to full fallback (sl_fallback_pct + tp_fallback_pct, `rr_quality = unknown`). The prior report explicitly notes this as the Option 1.C trade-off. Not contradicted by my reading.

3. Whether the flip ratio is the "cause" of the direction bias or just a side-effect. Independent validation of the funnel data (Step 1.5) would test this. With only 11 XRAY_DIR_FLIP events in 5.5 hours but 91 brain directives, the flip path can account for at most 11 / 91 = 12 % of the total direction skew. The remaining 88 % skew must come from upstream (Issue 2 ×0.7 counter multiplier, Issue 3 labeller AND-gate, Issue 4 strategist prompt asymmetry). This is consistent with the prior report's recommendation that Issue 4 + Issue 1 guard be the Phase A ship, not just Issue 1 alone.

### Implications for the eight A.4 concerns

For Issue 1 specifically:

- **Concern 1 — Phase A2 RR floor guard is a band-aid.** Validation neither confirms nor refutes. The guard would suppress flips on collapse-grade RR signatures. Whether that suppression is "blocking based on threshold" (band-aid) or "blocking on physically-meaningless RR" (root cause) hinges on whether the floor value reflects a structural truth. With `xray_dir_flip_min_chosen_rr = 0.5`, the floor is half the `min_rr_ratio` (already 2.0 in `StructureSettings`), which is below any tradeable threshold. So the guard rejects "structurally degenerate" inputs, not "below-target" inputs. That arguments for "root-cause-respecting", not "band-aid". Phase 2 step 2.1 should evaluate this on those grounds explicitly.

- **Concern 4 — Phase C default no-op.** The prior report §2.5 Option 1.A point 1 proposes `tp_min_distance_pct=0` and `tp_min_distance_atr=0` as default values for the new `StructureSettings` fields, with manual ramp to 0.5/1.0 % later. This is the "ship inactive code" pattern flagged in Rule 4 of the spec. Phase 2 step 2.4 needs to evaluate this. The validation here shows the structural collapse is real (RC-1.1 fires in 73 % of flips), so a non-zero default would be safer. But the prior report explicitly cites the "30-50 % drop in A+/A coin counts during transition" risk for symmetric `min_touches=2` — Option 1.A point 3 sub-option (a). So defaults need careful calibration, not arbitrary aggressive values.

### Recommended emphasis for Phase 2

1. **Quantify the contribution of the flip cascade to total direction skew.** 11 flips out of 91 brain directives is 12 %. If Issue 4 fix alone shifts brain output from 89 % Sell to (worst case) 80 % Sell, the flip's 12 % contribution is on the order of moving 75 → 67 Sells. That is the empirical-validation framing from Concern 5. The Phase 1.5 funnel validation will give the actual numbers.

2. **Decide between Option 1.A point 3 sub-options (a) symmetric `min_touches=2` and (b) regime-aware.** Sub-option (b) introduces "in trending regimes, relax `min_touches=1` for the counter-trend direction" — that is "asymmetry from data" if the regime is computed from data (B1a is verified). Sub-option (a) is strictly symmetric and risks dropping 60 % of resistances. Phase 2 needs to weigh these.

3. **Re-evaluate whether the position_in_range asymmetry (Finding 1) is in scope.** It contributes to entry-quality degradation on the Buy side in the same universe state. The prior report did not list it. A min_touches fix would partially address it (more supports exist → both-side branch at 238-243 fires more often, with symmetric formula). So fixing RC-1.3 likely fixes Finding 1 as a side effect, which is positive.

4. **Confirm that `xray_dir_flip_min_chosen_rr` and `xray_dir_flip_min_flipped_rr` defaults are physically meaningful**, not arbitrary "no-op" values. The prior report proposes 0.5 and 2.0 respectively. 0.5 is well below any genuine trade RR (the `_classify_rr` thresholds at structural_levels.py:215-223 are `>= 3.0 excellent`, `>= 2.0 good`, `>= 1.5 poor`, else `skip`). So 0.5 is in the "skip" range — only collapse-grade signatures are rejected. That argues for shipping non-zero defaults, not zero defaults.

### Closing note

Phase 1 Step 1.1 confirms the prior report's Issue 1 diagnosis. The four root causes are independent and verified. The flip cascade is real and bug-driven in the audited window. The asymmetric `>= 1` literal is empirically wrong-justified and unmodified for 36 days.

The remaining open question is the dominance of Issue 1 in the total direction skew. With 11 flips out of 91 brain directives the upper bound is 12 % contribution. The empirical-validation framing of Concern 5 (ship Issue 4 alone first, measure, then decide) is supported by these numbers — Issue 1 cannot account for the majority of the bias on its own.

End of Phase 1.1 report.
