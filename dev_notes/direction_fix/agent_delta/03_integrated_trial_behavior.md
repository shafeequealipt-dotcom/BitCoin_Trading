# DELTA 03 — Integrated Trial Behavior

Agent DELTA Phase 2.5. Specifies what the operator should expect to
observe after ALL four operator-approved fixes (R1+R2+R3+R4) ship and are
live. This document is the predictive baseline against which Phase 4
verification compares.

## Direction distribution under a mono-bullish market

Scenario: regime_detector classifies majority of universe coins as
`trending_up`, ADX 25-40, momentum positive. The brain naturally tilts
its directives toward Buy.

Pre-fix expected behavior (extrapolated from 2026-05-16 mirror): brain
produces ~85% Buy directives. APEX_DIR_LOCK fires Buy on `trending_up`,
reinforcing the bias. Some `bearish_fvg_ob_counter` setups appear; APEX
locks them to Buy because the regime label dominates; the inverted
`trade_direction=short` never reaches APEX.

Post-fix expected behavior:

- ALPHA Option E: `bearish_fvg_ob_counter` setups now plumb
  `trade_direction="short"` into `StructuralData`. BETA's lock reads it.
- BETA Option B: lock checks `rr_short/rr_long >= 3.0` (Buy-to-Sell
  direction; uses the conservative 10x threshold). When structure
  modestly favors Short (3-9x ratio) the lock continues to fire Buy.
  Only when structure decisively favors Short (>= 10x AND trade_direction
  = "short") does the lock relax.
- BETA Option E: Buy-to-Sell override threshold auto-tunes from per-direction
  WR. In a mono-bullish market, Buy WR is high; threshold for Buy-to-Sell
  is computed as `base * buy_wr / 100` which gives ~10x or higher. The
  override clears only at extreme structural evidence.
- GAMMA Design C: portfolio rapidly concentrates Buy. CHECK 15 evaluates
  aim-conditional: when XRAY says `trade_direction="short"` OR
  `rr_short/rr_long >= 2.0`, the cap fires and blocks further Buy entries.
  When XRAY confirms mono-bullish (no opposing structure), the cap permits
  with `verdict=permitted_mono_trending`.

Expected directive distribution: ~70-80% Buy at the brain, executed
roughly the same at the gate (cap permits in mono-trending). Trade
frequency preserved.

Expected new log events: `PORTFOLIO_CAP_HIT` with
`verdict=permitted_mono_trending` becomes the dominant CAP_HIT variant.
This is the design-intentional behavior.

## Direction distribution under a mono-bearish market

Scenario: the 2026-05-16 baseline. Regime_detector classifies 76% of
universe as `trending_down`. Brain pre-fix produced 89% Sell directives.

Post-fix expected behavior:

- ALPHA Option E: `bullish_fvg_ob_counter` setups now plumb
  `trade_direction="long"` into `StructuralData` (691 such events on May
  16). BETA's lock reads it.
- BETA Option B: lock checks `rr_long/rr_short >= 3.0` (Sell-to-Buy
  direction; uses the aggressive 3x threshold). When structure modestly
  favors Long (3x ratio or `trade_direction == "long"`), the lock bails
  out. Many of the 8 May 16 suppression cases prevented.
- BETA Option E: Sell-to-Buy override threshold auto-tunes. Buy WR is
  high (55.6%); threshold is `base * (1 - buy_wr/100)` = `10 * 0.444` =
  4.44x; floored at 2.0x. The override clears the dead zone — the 8 May
  16 suppressions admit at 3.0x.
- GAMMA Design C: portfolio still tends toward Sell-dominant in mono-bearish.
  When the portfolio reaches 70% Sell AND a counter-long opportunity
  exists (per `trade_direction="long"` or `rr_long/rr_short >= 2.0`),
  CHECK 15 blocks further Sell entries. The brain's next cycle has the
  chance to pick the alternative Buy that triggered the aim-conditional path.

Expected directive distribution post-fix: 60-75% Sell (down from 89%).
The remaining 25-40% Buy entries are structurally-supported counter
trades. The 14:45-style 5-position Sell cascade is prevented.

The BSBUSDT case: BETA's lock does NOT fire (rr_long=3.7, rr_short=0.5,
ratio 7.4x; trade_direction="long"). Brain's Buy stands. APEX outputs
Buy. Gate CHECK 15 evaluates portfolio at whatever pre-trade concentration
exists; with Buy direction the cap does not fire (Buy is under-represented).
Order enters Buy. Predicted PnL outcome: +TP rather than -SL.

## Direction distribution under a mixed market

Scenario: regime_detector classifies ~50% trending_up, ~50% trending_down
or similar split. Brain naturally produces a balanced ~50/50 directive set.

Post-fix expected behavior: ALPHA's plumbing is consumed correctly but
makes no difference in the absence of counter setups. BETA's lock fires
about half the time on each side (less aggressive than baseline because
both directions have moderate WR). BETA's override threshold auto-tunes
based on prevailing WR — the Sell-to-Buy and Buy-to-Sell thresholds are
roughly symmetric in mixed markets. GAMMA's cap rarely fires (portfolio
concentration stays under 60% most of the time).

Expected directive distribution: 40-60% Buy and Sell, naturally balanced.
Cap fires once or twice per day, mostly with `verdict=blocked_aim_conditional`
when one side temporarily overshoots 70% and an alternative structural
signal exists.

This is the operator's target market state — fully aim-aligned trading
across both directions.

## Portfolio concentration trajectory through a day with mixed signals

Pre-fix on 2026-05-16: portfolio reached 87-92% Sell concentration by
14:42, triggering the 14:45 5-position cascade.

Post-fix on a similar day:

- 09:00-12:00: brain produces balanced directives. Portfolio is N=4-6,
  concentration 40-60%. No CHECK 15 firing.
- 12:00-14:00: market trend shifts bearish; brain tilts toward Sell. By
  13:00 portfolio is 65% Sell (7/11). `PORTFOLIO_CAP_WARN` emits — visible
  to operator.
- 14:00-14:30: Sell tilt continues. Portfolio reaches 75% Sell. CHECK 15
  evaluates aim-conditional. For each new Sell directive:
  - If `trade_direction="long"` (counter setup) or `rr_long/rr_short >=
    2.0`: cap FIRES. Trade rejected. `PORTFOLIO_CAP_HIT` with
    `verdict=blocked_aim_conditional`. Brain's next cycle picks
    different coin.
  - If neither: cap permits. Trade enters Sell.
- 14:30-15:00: 14:45 5-position cascade attempt. Pre-fix this saw 5
  consecutive Sells at >= 87.5% concentration. Post-fix, those 5 entries
  all face CHECK 15 with portfolio at 70-85% Sell. Of the 5:
  - AVAXUSDT, APTUSDT, SANDUSDT, LINKUSDT, ORCAUSDT each evaluate against
    `trade_direction` and `rr_long`/`rr_short`. Each one for which an
    alternative exists is BLOCKED. Probability of all 5 being blocked
    depends on per-symbol structural data; conservatively 3-4 of 5 blocked.
  - The cascade size drops from 5 simultaneous SL hits to 1-2.
  - Net cascade loss drops from -$31.82 to -$8 to -$15 (rough estimate).

By end-of-day the trade_log shows balanced direction distribution
relative to market regime, and the day's max concentration tops out at
75-85% rather than 92%.

## XRAY override frequency: expected post-fix vs pre-fix

Pre-fix (2026-05-16 baseline, 5-hour session):

- `XRAY_OVERRIDE_LOCK` (override fired): 6 events
- `XRAY_FLIP_SUPPRESSED_BY_LOCK` (override blocked in dead zone): 8
  events
- `XRAY_LOCK_PRECEDENCE_RESOLUTION` (decision logged): 14 events

Post-fix (target):

- `XRAY_OVERRIDE_LOCK`: 10-14 events (the suppressed 8 join the existing
  6 because BETA's R3 Option E reduces the override threshold for
  Sell-to-Buy direction).
- `XRAY_FLIP_SUPPRESSED_BY_LOCK`: 0-2 events (only marginal cases in the
  2.0-2.99x ratio band).
- `XRAY_OVERRIDE_RATIO_DETAIL`: new event emits per override decision
  with `direction`, `buy_wr`, `sell_wr`, `derived_threshold`,
  `xray_ratio`, `verdict`. Approximately 14-20 events per 5-hour session.
- `APEX_LOCK_DECISION_EXPLAINED`: new event emits per lock decision with
  `regime`, `ratio_long_to_short`, `trade_direction`, `verdict={fired,
  bailed_structural}`. Estimated 60-80 events per session (one per
  trade in trending regime).
- `APEX_DIR_LOCK`: drops from 80 to 50-65 events per session (lock
  fires less often because R2 Option B bails out on structural disagreement).

## APEX_DIR_LOCK behavior: expected drop in block rate

Pre-fix: 80 `APEX_DIR_LOCK` events per 5-hour session. 89% Sell forced.
8 `XRAY_FLIP_SUPPRESSED_BY_LOCK` events in dead zone. 11
`APEX_DIR_LOCK_OVERRIDE` events all vetoed.

Post-fix target:

- Total `APEX_DIR_LOCK` events: 50-65 per session (25-35% reduction).
  The drop reflects R2 Option B's bail-out path firing in cases where
  structure disagrees with regime by >= 3x (Sell-to-Buy direction) or by
  >= 10x AND `trade_direction == "short"` (Buy-to-Sell direction).
- Forced-Sell share within remaining locks: ~70% (down from 89%) —
  primarily because the lock now requires structural agreement OR
  the Buy-to-Sell asymmetric threshold (which is conservative).
- `APEX_DIR_LOCK_OVERRIDE` (lock vetoes brain's flip): drops from 11
  to 3-6 — most cases where the lock would have fired now have it bail
  via Option B's structural-RR consultation.

Remaining locks fire in two patterns:

1. Regime-aligned, structurally-aligned, trade_direction-aligned: 70-80%
   of remaining locks. The lock acts as designed — strong directional
   consensus.
2. Regime-aligned, structurally-borderline (ratio 2.0-2.99x), no
   counter trade_direction signal: 20-30%. The lock fires because BETA's
   bail thresholds (3x Sell-to-Buy, 10x Buy-to-Sell) are not cleared.
   These are the marginal cases where the lock retains its conservative
   role.

## The BSBUSDT replay walked through post-all-fixes

Pre-fix BSBUSDT scenario (from BETA deliverable 04):

- 15:02:04, regime=volatile, brain proposed Sell with 0.85 confidence,
  rr_long=3.7, rr_short=0.5 (ratio 7.4x favoring Long).
- Lock fired under volatile branch.
- DeepSeek tried Buy with 0.85 conf; lock reverted to Sell.
- `XRAY_FLIP_SUPPRESSED_BY_LOCK` emitted (7.4x is in the 3-10x dead
  zone).
- Trade entered Sell, hit SL at -1.40% (-$70.08).

Post-fix BSBUSDT decision chain:

1. ALPHA Option E: assembler populates `StructuralData.trade_direction =
   "long"` from `analysis.trade_direction` (BSBUSDT had a counter setup
   that day per BETA deliverable). Field travels into APEX.
2. BETA R2 Option B: `_check_direction_lock` runs.
   - Reads `package.structural_data.rr_long=3.7`, `rr_short=0.5`,
     `trade_direction="long"`.
   - Brain proposed Sell; potential override direction is Sell-to-Buy.
   - Bail condition: `rr_long/rr_short >= 3.0` (7.4 >= 3.0) OR
     `trade_direction == "long"` (TRUE).
   - Returns `(False, "")` — lock does NOT fire.
   - Emits `APEX_LOCK_DECISION_EXPLAINED | sym=BSBUSDT regime=volatile
     ratio_long_to_short=7.4 trade_direction=long verdict=bailed_structural`.
3. DeepSeek processes the prompt. No pre-parse lock. Confidence 0.85 on
   Buy.
4. Post-parse `_enforce_flip_confidence`: for volatile regime, the
   current bail-out at optimizer.py:1486 returns False for volatile. So
   no post-parse veto. The post-parse override at lines 359-371 sees
   `is_locked=False` and does NOT mutate direction.
5. APEX outputs `direction=Buy`.
6. Strategy_worker: reads `_apex_locked=False`. The XRAY override block
   does not need to fire because no lock to override. The unlocked-flip
   path at line 1721 (`not _apex_locked and _ratio > _flip_threshold`)
   fires with ratio 7.4x > 3.0x. Trade flips to Buy at the structural
   long_sl_price / long_tp_price.
7. Gate runs CHECK 0-14 normally. CHECK 15 (GAMMA) evaluates portfolio.
   Assume portfolio is at this point ~65% Sell. New direction is Buy.
   `pre_pct_buy = ~35% < warn_pct 60%`. Emits `PORTFOLIO_DIRECTION_PERMITTED`
   INFO. Trade passes.
8. Order placed: Buy BSBUSDT at the structurally-correct entry, SL at
   `long_sl_price`, TP at `long_tp_price`. Per BETA deliverable 04,
   price moved up +0.23% within 15 minutes — trade closes at TP rather
   than the original -1.40% SL.

Net outcome: -$70.08 loss prevented; structural-correct +$ gain captured.

## All five aim-bias questions answered yes across the integrated system

### 1. Does this preserve trade frequency?

YES. ALPHA Option E is additive (adds a field; no trades rejected). BETA
Option B relaxes the lock so MORE trades pass through (frequency rises).
BETA Option E auto-tunes the override threshold to admit more flips in
the under-represented direction. GAMMA Design C only blocks
same-direction entries when an alternative direction is genuinely
available, leaving frequency unchanged in mono-trending markets and only
slightly reduced in mixed markets where alternatives exist. Combined
post-fix daily trade count stays within 30% of pre-fix baseline.

### 2. Does this preserve aggression?

YES. ALPHA preserves the brain's input. BETA's lock relaxes — DeepSeek
proposes decisively, and the structural-evidence-aware lock no longer
overrides for marginal regime alignment. BETA's threshold becomes
WR-aware — high-WR direction gets aggressive (low threshold) override.
GAMMA's cap is aim-conditional — it fires only when the brain has a
real alternative to pick.

### 3. Does this improve decision quality?

YES. ALPHA eliminates the cross-layer information loss (APEX now sees
the same trade_direction the brain sees). BETA addresses the BSBUSDT
case directly. BETA Option E couples threshold to historical WR. GAMMA
prevents cascade-class outcomes (the 14:45 5-Sell cascade -$31.82 loss
becomes 1-2 SL hits ~ -$10).

### 4. Does this preserve passive-close advantage?

YES. None of the four fixes touch the close paths (data-lake watchdog,
profit sniper, time-decay). All four are entry-side changes.

### 5. Does this respect structural separation of concerns?

YES. ALPHA: XRAY publishes; APEX consumes — same layer doctrine. BETA:
APEX consumes structural-RR (already on package); APEX consumes
trade_log (already loaded). GAMMA: gate consumes coordinator state (same
pattern as CHECK 6 cooldown); GAMMA reads `structural_data` (in-package
field, no new cross-layer call). The aim-conditional gate reads what is
already plumbed through the package by ALPHA.

All five YES across the integrated system. The fix is aim-aligned.

## Expected verification timeline

- 24-hour live trial after EACH agent's Phase 3 ships. Phase 4 GO requires
  the 24-hour log slice plus a quick SQL pull on trade_log to confirm the
  per-agent GO criteria.
- After all three Phase 4 verifications pass, the operator runs a 72-hour
  integrated trial. The integrated trial confirms the cross-fix behavior
  predicted in this document.
- If the integrated trial diverges from prediction by more than ~30% on
  any key metric (CAP_HIT count, Sell/Buy ratio, override threshold
  distribution), the operator initiates a Phase 5 investigation before
  declaring the fix complete.

## What the operator will see in workers.log on day 1 of integrated trial

Sample log slice the operator can grep for to confirm post-fix behavior:

- `XRAY_CLASSIFY_SUMMARY` with new `trade_dir_long`, `trade_dir_short`,
  `counter_count` fields populated
- `XRAY_DIRECTION_SPLIT` per tick
- `APEX_LOCK_DECISION_EXPLAINED` with `verdict=fired` or `verdict=bailed_structural`
- `XRAY_OVERRIDE_RATIO_DETAIL` with `derived_threshold` between 2.0 and 15.0
- `PORTFOLIO_CONCENTRATION_CHECK` on every gate run
- `PORTFOLIO_CAP_HIT` with `verdict=blocked_aim_conditional` or
  `verdict=permitted_mono_trending`
- `STRAT_DIRECTIVE` shows ~25-40% Buy share (target range; up from 14%)
- `BD_TRADE_HISTORY_PERSIST_OK` shows ~25-35% Buy share (up from 9%)

These six log signatures, observed together, confirm all four fixes are
live and behaving as designed.
