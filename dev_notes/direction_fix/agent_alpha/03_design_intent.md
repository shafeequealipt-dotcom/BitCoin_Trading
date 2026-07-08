# 03 — Design Intent (Git Blame + Commit Messages + Design Docs)

Agent ALPHA Phase 1, Step 3. Authoritative source for why counter-setup logic
exists, what it was supposed to do, and how the data flow was wired.

## Commit lineage of the counter-setup logic

The counter variant was added in six atomic phases by the same author on
2026-04-29 to 2026-04-30. Commit timeline (from `git log --grep=COUNTER` and
`git log --grep=counter`):

| Commit | Date | Title |
|---|---|---|
| 61d0c1d | pre-2026-04-30 | phase1(xray-counter): add COUNTER setup variants to SetupType enum |
| 1692716 | pre-2026-04-30 | phase2(xray-counter): ATR-scaled distance windows for nearest FVG/OB finders |
| 3c579a8 | pre-2026-04-30 | phase3(xray-counter): extend `_find_nearest_*` contract to surface counter-direction zones |
| 3a59637 | 2026-04-30 22:31 | phase4(xray-counter): characterize-and-rank classifier with counter-direction branches + trade_direction |
| 94044f7 | 2026-05-05 | fix(xray/phase-1): restore XRAY confidence variance — sweep direction + drop 0.5 floor (also touches phase-5 confidence) |
| a3948c5 | post-Phase 4 | phase5d(xray-counter): brain prompt counter visual + XrayBlock trade_direction |
| ecfa3dd | post-Phase 5 | phase6(xray-counter): NONE reason enrichment + BoS retest relaxation with minor confidence cut |
| 4223910 | post-Phase 6 | xray-counter: real-project end-to-end pipeline verification |

Git blame on the BULLISH_FVG_OB_COUNTER branch body in
`structure_engine.py:1175-1194` attributes the trade_direction inversion line
("`analysis.trade_direction = \"long\"  # counter trade is LONG`") to commit
`3a59637` (Phase 4). That's the authoritative commit for the inversion semantic.

## Authoritative quote from commit 3a59637 (Phase 4) message

The Phase 4 commit message explicitly states the design intent. Key passages
(verbatim, indented):

> "The philosophical fix. Extends classify_setup() with two new branches:
>  BULLISH_FVG_OB_COUNTER and BEARISH_FVG_OB_COUNTER. They fire when the
>  suggested direction's in-direction zones are missing but the OPPOSITE
>  direction has tradeable FVG+OB structure near price (Phase 3's
>  nearest_fvg_counter / nearest_ob_counter, populated by the now-extended
>  `_find_nearest_*` contract)."

> "trade_direction field added to StructuralAnalysis so downstream consumers
>  can distinguish 'trade direction implied by setup' from 'suggested
>  direction implied by market structure.' For in-direction setups they
>  match; for counter setups trade_direction is OPPOSITE. classify_setup
>  mutates analysis.trade_direction as a side-effect."

> "Counter alignment helper rejects long-counter on uptrend (and mirror)
>  since counter trades make sense WITH the structural fade, not WITH
>  the trend itself."

The design intent is unambiguous:

- Counter setup is a **contrarian entry signal** — when in-direction structure is
  absent but counter-direction structure is present near price, the system
  characterizes the coin as having a tradeable opposite-side opportunity.
- The trade direction implied by the counter setup is the OPPOSITE of the
  regime label (`trade_direction != suggested_direction`).
- The author explicitly added a second field (`trade_direction`) to keep
  the regime-derived `suggested_direction` and the setup-derived
  `trade_direction` separately observable, so downstream consumers could choose
  which to consume.
- The confidence is reduced by 0.7x (default) because counter trades fight the
  regime — design acknowledges lower conviction.
- The alignment helper (`_counter_alignment`) excludes counter trades when the
  structure is the SAME as the counter direction (no long counter on an already
  uptrending coin); design rejects "double-long" scenarios.

## Authoritative quote from commit a3948c5 (Phase 5d, brain prompt)

> "ClaudeStrategist._format_packages_for_prompt renders counter setups with
>  explicit '(COUNTER-TRADE — trade direction is OPPOSITE to market structure
>  bias; lower conviction)' annotation, plus a `trade_direction=long|short` field
>  in the Setup line. Pre-fix the prompt only showed setup_type + confidence —
>  the brain had no structured way to know that bullish_fvg_ob_counter meant
>  LONG even though suggested_direction was SHORT."

> "The annotation 'lower conviction' is intentional. It nudges the brain
>  toward smaller position sizes / tighter SL on counter setups,
>  complementing the mechanical Phase 5a/b/c confidence weighting in
>  TradeScorer Quality, opportunity_score struct_norm, and ensemble
>  size_mult."

Design intent for the brain-prompt surface: the brain SEES the inverted
`trade_direction` AND the annotation. The annotation deliberately frames the
counter as "lower conviction" — the design did not want the brain to treat
counter setups as full conviction signals.

## Phase 0 verification doc quote

`dev_notes/xray_characterization_fix/phase0_verification.md:51` states:

> "Implication for Phase 4 lift: the dominant failure mode (47% —
>  `no_fresh_bullish_fvg` on uptrend coins like BTCUSDT/ETHUSDT/SOLUSDT) maps
>  directly to candidates for `BEARISH_FVG_OB_COUNTER` (suggested=long, only
>  bearish zones near price). The 19% mirror (`no_fresh_bearish_fvg` on
>  downtrend coins) maps to `BULLISH_FVG_OB_COUNTER`. Together: 66% of NONE
>  coins are counter-setup candidates by mechanism."

So the original motivation for adding counter setups was: 66% of "no setup" (NONE)
outcomes were actually coins where the in-direction structure was missing but
COUNTER-direction structure existed nearby. The author wanted those coins to be
classified and ranked as tradeable opportunities rather than discarded as NONE.

The design was an explicit **broadening of the opportunity surface**, not a
defensive measure. The aim was to FIND MORE TRADES (consistent with the project
aim "aggressive opportunity exploitation, NOT capital preservation").

## What counter was intended to be: a/b/c/d

The brief asks: was counter intended as (a) contrarian entry, (b) exit signal for
opposing position, (c) signal-with-reduced-confidence, (d) regime override?

Evidence from commit messages and the Phase 0 verification doc:

- (a) Contrarian entry: YES. The commit explicitly says "they FIRE when ...
  the OPPOSITE direction has tradeable FVG+OB structure" and emits a
  setup_type that ranks via the same Setup Scanner as in-direction setups.
- (c) Signal-with-reduced-confidence: YES. `counter_confidence_multiplier = 0.7`
  is the default; the brain prompt annotation says "lower conviction"; the
  ensemble size_mult floors at 0.5 for low-confidence counter setups (per the
  Phase 4 commit body and Phase 5a-c notes).
- (b) Exit signal: NO. There is no exit-only handling. Counter setups produce a
  new entry direction; they are not just used to flag "close opposite-side
  positions". CALL_B (manage-positions) reads `trade_direction` in the same
  per-coin block but the management decision is up to the brain, not a
  hard-coded exit rule.
- (d) Regime override: NO. Counter does NOT override the regime classification.
  `suggested_direction` (the regime label) is unchanged by counter logic. The
  counter signal coexists with the regime label — the brain reads both via
  the Setup annotation and the regime line.

So the operative answer is **(a) + (c)**: counter is a contrarian entry signal
with reduced confidence, NOT an exit-only signal, NOT a regime override.

## What counter was NOT intended to do

From the commit messages and the broader Phase 5 wiring:

- It was NOT designed to be a complete decision flip. The regime label
  `suggested_direction` was deliberately left intact so APEX (which reads
  `suggested_direction`) could still apply regime-aware logic.
- It was NOT designed to mutate the consensus voter, regime detector, or RR
  calculation. The Phase 4 commit body explicitly carves these out as out-of-
  scope: "Consensus voter, regime, RR, blockers — out of scope."
- The author did NOT mean for the inverted `trade_direction` to flow into
  APEX's direction lock. APEX assembler.py reads `suggested_direction` — that
  is the regime-context input, not the setup-payoff input. The split is by
  design.

## Where the design intent runs into the production bias

The split design (suggested_direction for regime, trade_direction for setup-
payoff) is logically clean for the brain to read both signals. But it produces
the bias chain that R2 (APEX_DIR_LOCK) exposes:

- APEX assembler.py:737 hand-rolls `StructuralData.suggested_direction =
  analysis.suggested_direction`. That field carries the regime label.
- APEX optimizer's `_check_direction_lock` (R2 territory) reads regime and
  applies a hard lock when regime is trending. The lock fires regardless of
  whether the COUNTER setup recommended an opposite trade.
- The brain CALL_A prompt shows `trade_direction=long` for a counter setup,
  the brain may choose Buy, but APEX's lock then forces back to Sell.

This is a CROSS-LAYER information loss: XRAY computes the counter inversion
correctly, exposes it to the brain prompt correctly (Phase 5d), but the
information is dropped between XRAY and APEX.

This is R1 in its honest form (vs the misnamed mechanism in the spec). The
ROOT cause for ALPHA's scope is "XRAY computes trade_direction correctly; the
information loss happens at the XRAY→APEX handoff in assembler.py:737". Whether
to fix this at the XRAY surface or at the APEX consumer side is the operator's
choice — and it interacts directly with R2 (BETA's scope).

## Architecture note (clean separation of concerns)

The split (suggested_direction = regime; trade_direction = setup-payoff) is
correct under the project's layer doctrine:

- Layer 1B (XRAY) is the structural intelligence layer. It produces multiple
  observable fields and explicitly does NOT make trade decisions.
- Layer 2 (Brain) is the deciding layer. It reads multiple inputs (including
  both fields) and picks a direction.
- Layer 3 (APEX) is the optimization+gate layer. It takes the brain's
  direction and applies regime-aware optimization.

The clean form: APEX reads the brain's chosen direction (post-decision) PLUS
the XRAY counter signal (so it knows the brain's choice was structurally
counter-supported). It then applies a regime-aware lock that respects both
regime AND structural counter-evidence.

The bug, in this layer view, is that APEX currently reads only
`suggested_direction` (regime) and not `trade_direction` (counter-aware
structural). That is a CROSS-AGENT issue — fixing it is BETA's territory if
the change is in optimizer.py, ALPHA's territory if the change is in
assembler.py's field plumbing.

## Summary for ALPHA's options

The design intent is preserved. The mechanism the spec called R1 is not what
the code does. The legitimate ALPHA-scope concern is "the inverted
trade_direction is exposed to the brain but is not reaching APEX, so the
brain can choose Buy on a counter setup but APEX still locks to Sell".

Fix options in 04 must:

1. Either change what APEX sees (Option B / E — likely cross-agent with BETA).
2. Or change what the brain weights (Option A — pure ALPHA scope).
3. Or change the configuration of the counter system (Option C — easiest
   rollback path).
4. Or change the XRAY public observability so operators don't read
   suggested_direction as the bias indicator (Option D).

The next file proposes these options with file:line specifics and the
aim-bias evaluation.
