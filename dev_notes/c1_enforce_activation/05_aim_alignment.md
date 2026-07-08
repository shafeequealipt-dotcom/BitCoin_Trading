# C1 — Phase 1.5 Aim-Bias Five-Question Evaluation

The implementation prompt (Part A.8) requires every change to answer five aim-bias questions before it can be considered aligned with the project. This document walks through each question with evidence from the verified code map and the Phase 1.1–1.4b findings.

The project aim — restated for clarity — is **aggressive opportunity exploitation, NOT capital preservation**. The five questions are designed to surface band-aid patterns that drop trade frequency, dampen aggression, or hardcode direction.

## Question 1 — Does this preserve trade frequency?

**Answer: yes, preserved.**

Evidence:
- The scoring intercept is gated by `if act in ("close", "take_profit")` at `position_watchdog.py:3428`. Entry actions never cross this code path.
- The brain CALL_A flow (new-trade decisions) runs in a separate path inside `strategist._build_strategy_prompt` and `decision_parser.parse_strategic_plan` — neither references `wd_brain_scoring` nor any of its outputs.
- The gate (Layer 4 `pre-execution validation`), the executor (Layer 5 Bybit adapter), and the reconciler (Layer 7) are not touched.

Re-bucketing the watchdog scorer's `sl_consumption_pct` (Phase 1.4b) cannot affect entries because it only feeds `compute_brain_close_score`, which is consulted only for `act in ("close", "take_profit")`.

The brain prompt addition (dual SL% rendering at `strategist.py:4206-4264`) is in the CALL_B (position-management) prompt only. The CALL_A prompt builder at lines 1218+/3134+ is unchanged.

## Question 2 — Does this preserve aggression?

**Answer: yes, preserved.**

Evidence:
- The brain still proposes every close vote. The scoring intercept is a downstream quality gate; it does not propose anything itself.
- The scoring does not block the brain from taking aggressive position-management actions like partial closes, tighten-stops, or take-profits at >threshold composites.
- The `execute` recommendation (composite >= 6.0) allows the brain's close to fire unchanged. The scoring favours strong-winner closes (PnL bucket `strong_winner` = +3.0) and structurally-broken aged-losing closes (sum of age + velocity + xray + reasoning can reach +6).

The scoring does NOT punish aggression — it filters reflexive panic-closes on shallow losers. Aggression in the trade-entry sense (Layer 2 CALL_A, Layer 3 APEX, Layer 4 Gate) is untouched. Aggression in the position-management sense (CALL_B closes, take-profits, partial closes) is preserved when the close is justified.

## Question 3 — Does this improve decision quality (not block decisions)?

**Answer: yes, improved.**

Evidence:
- The seven-factor composite (`wd_brain_scoring.py:38-84`) uses objective inputs the brain cannot see in its own prompt — most notably `velocity_pct_per_s` and the structured `xray_match` direction-vs-position comparison.
- The historical record (Phase 1.1) shows 28 of 28 close votes correctly flagged as below-threshold, 27 of those 28 closes lost money. Decision quality of the scoring system itself is 27/28 = 96% accuracy on the broken-close class.
- The scoring does not block legitimate closes: composites >= 6.0 execute. Phase 0 showed zero such composites in the log-only window, which is consistent with the broken-close pattern the prompt describes.
- The "do nothing" rate (composite in `reject` band, between 0 and threshold) still applies pressure — when conditions improve, the next tick re-scores and a now-above-threshold close executes naturally.

## Question 4 — Does this preserve the passive-close advantage?

**Answer: strengthened.**

Evidence from Phase 0 14-day breakdown:
- `wd_dl_action` (passive deadline): 134 trades, 127 wins, +$1035.23
- `wd_profit_take` (trailing/passive profit): 15 trades, 15 wins, +$644.97
- `bybit_tp_hit` (TP fired): 22 trades, 20 wins, +$241.32
- `shadow_sl_tp` (Shadow auto-exit): 69 trades, 51 wins, +$383.10

Passive paths collectively contributed +$2304 over 14 days. Brain's active close `wd_claude_action` contributed −$952.

Activating enforce mode does not change the passive paths. It rejects the brain's panic-closes, which means more positions reach a passive exit (deadline, trail, TP). Passive paths take in those holds and either let them recover or close them via their own mechanics. The fraction of session volume on passive paths grows, the fraction on `wd_claude_action` shrinks.

The SL-tightening fallback (`_tighten_sl_breakeven_30pct`) on reject_and_tighten composites caps downside risk while the position waits for one of the passive exits. This is direction-agnostic and respects the existing tighter-only SL invariant via `_push_sl_to_shadow`.

## Question 5 — Does this respect structural separation of concerns?

**Answer: yes, preserved.**

Evidence:
- Scoring lives entirely in Layer 6 (Watchdog). The single new import in `position_watchdog.py` is from `src.risk.wd_brain_scoring`, which is also Layer 6 / Layer 3 risk-arbitrator infrastructure.
- The brain prompt change is the only Layer 2 (Brain) change. It is a pure text addition; CALL_A is untouched, the decision parser is untouched, the prompt assembly orchestration is untouched.
- Layer 1 (data: regime, ingestion, structure, signals, strategies, scanner): untouched.
- Layer 3 (APEX): untouched. No APEX field is consumed by the scoring intercept.
- Layer 4 (Gate): untouched. The gate is upstream of execution and only sees new-trade proposals.
- Layer 5 (Execute): untouched. The Bybit demo adapter HTTP/auth/signing/WS-parse layer is verified rock-solid and out of scope.
- Layer 7 (Reconcile): untouched.

The scoring is the watchdog's quality gate on the brain's CALL_B `close` decisions. The brain proposes; the watchdog scores; if the score clears, the close fires through the existing Layer 5 path. No layer's responsibility expanded; no layer's interface changed.

## Cross-check against the operator's hard rules

From `IMPLEMENT_C1_ENFORCE_ACTIVATION.md` Part C:

- **Rule 1** (investigation before activation): satisfied by Phase 0–1.6 documents and the operator decision gate at Phase 2.
- **Rule 2** (independently confirm issue): satisfied by Phase 0 and Phase 1.1 (28/28 events correlated with DB outcomes).
- **Rule 3** (understand scoring system completely before activating): satisfied by Phase 1.2 and Phase 1.3.
- **Rule 4** (investigate SL% divergence): satisfied by Phase 1.4 and 1.4b with formal alignment, the diagnostic, and an upper-bound proof that no composite flips.
- **Rule 5** (verify, do not assume): every claim in the documents has a file:line citation or a DB query result.
- **Rule 6** (root cause, not symptom): the scoring system was already root-cause-fixing the brain panic-close pattern; this activation flips the gate that was sitting in log-only.
- **Rule 7** (production-quality verification): in-flight via Phase 1.5b (boot sentinel) and Phase 1.5c (integration tests).
- **Rule 8** (commit on main, atomic and labeled): four atomic Phase 1.4/1.4b commits already on main (`be54fad`, `0a057ee`, `b6f844b`, `2e70c9f`).
- **Rule 9** (aim preservation): this document.
- **Rule 10** (operator interaction protocol): all documents use h1/h2/h3 headings, no emoji, plain prose.
- **Rule 11** (do not break what works): each commit preserves all listed shipped fixes (Shadow, mid-hold, direction-bias, prewarm, 5-min reentry, three-gaps, regime calibration, J-series, Tier 1/2, brain prompt enrichments). Verified by 68 watchdog/scoring tests + 44 strategist tests passing.
- **Rule 12** (staged activation with rollback): the flag flip is a single line in `config.toml:531`; the boot sentinel (Phase 1.5b) will confirm mode at startup.
- **Rule 13** (DB cascade absence): `BRAIN_FAILURE_CASCADE` count = 0 confirmed in Phase 0.
- **Rule 14** (recency-bias-aware): Phase 0 confirmed log-only is still active in the most recent worker logs.
- **Rule 15** (trial behaviour specification): Phase 4 of the plan specifies the expected event signatures.
- **Rule 16** (honest self-check after activation): Phase 4 of the plan instruments the self-check.

## Conclusion of Phase 1.5

All five aim-bias questions answer yes or improved. All sixteen operator hard rules are satisfied or actively in progress with concrete next steps. Enforce-mode activation is aim-aligned. The activation is a Layer 6 config flip with no cross-layer side effects.
