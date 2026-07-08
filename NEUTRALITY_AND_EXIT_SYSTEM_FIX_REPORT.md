# Neutrality and Exit-System Fix — Combined Report (2026-05-30)

This report covers the seven issues in IMPLEMENT_NEUTRALITY_AND_EXIT_SYSTEM_FIX:
the profit-fetching exit system (C2), the strategy evidence (S1 through S5), and
the four neutrality and management issues (D1, H2, H3, H1). Each issue was
re-verified against the running code (HEAD fb1636d, the exact code that was
live-observed on 2026-05-29), root-caused with file and line evidence, fixed at
root, and self-verified with concrete values. Every fix is one atomic,
individually-revertible commit on the main branch. No new branch and no new
directory were created. The direction-flip switches remain off, and no protected
table, schema, or migration was touched.

## The commits

The six fixes are six commits on main, newest first:

- f039de8 — H1: cut near-certain losers (structure-guard yields)
- 74b62a5 — H3: respect the brain's weak-setup size; skip below the real exchange minimum
- 047033a — H2: let the data choose the better-reward side or skip
- 2e56853 — D1: remove the directional bias to neutral
- 4b9ea02 — Strategy evidence (S1 through S5): correct, complete, reconciled
- 9481d2e — C2: restore the profit-fetching exit system (gateway clamps instead of rejecting)

Each commit is independently revertible. Timestamped backups of every edited file
were taken into the existing backups directory before editing.

## C2 — the profit-fetching exit system was gated shut

Symptom: stop updates were accepted about 1.7 percent of the time (ten of nearly
six hundred). A winner rode to plus 0.88 percent and gave it all back because the
protective stop never moved, and a coin called NEAR round-tripped from plus 1.3
percent to minus 1.8 percent while its profit floor was re-spammed as an
impossible above-price stop (thirty wire failures).

Root cause, confirmed in src/core/sl_gateway.py: Rule R2 (minimum distance, lines
465 to 514) and Rule R3 (maximum step, lines 516 to 567) both rejected the whole
stop move rather than clamping it. The chandelier trail source was in neither
bypass set, and the ladder bypassed only R3 so it was still caught by R2. The live
reject split showed minimum-distance rejections dominated the maximum-step ones.

Fix: the gateway now clamps and applies instead of rejecting. R3 moves the stop
exactly the maximum step toward price and applies it, so the trail ratchets up
incrementally each tick. R2 places a too-close or wrong-side stop exactly at the
minimum-distance boundary on the correct side and applies it, which also fixes the
NEAR case by wiring the highest valid stop just below price. If the best valid stop
still cannot improve the current stop, the gateway holds the current stop as a
no-op with no exchange call, which stops the wire-failure re-spam loop.
Tighten-only, the rate limit, the ATR-scaled minimum distance, the deliberate
0.25 percent maximum step, and the ladder and safety bypass are all preserved. New
SL_GATEWAY_R2_CLAMP and SL_GATEWAY_R3_CLAMP log lines make every clamp visible; a
held move logs reason clamp_noop.

Before and after, in plain prose: before, a winning long whose trail wanted to sit
1.2 percent above the old stop was rejected, so the stop stayed at breakeven and
the position round-tripped to a loss. After, the same trail moves the stop up 0.25
percent this tick and again next tick, ratcheting upward and locking the gain; and
a floor that lands above price after a fast retrace is clamped down to the highest
valid stop just under price instead of being rejected and re-spammed.

Verification: seven concrete-value self-checks in verify_c2_gateway_clamp.py
(frozen-trail advance, too-close, NEAR wrong-side, no-op, tighten-only, normal
tighten, short symmetry) all pass; ninety-six gateway-related tests pass.

## S1 through S5 — strategy evidence for every coin

Symptom and root cause: some coins reached the brain with zero strategies fired and
an empty ensemble, leaving an ambiguous blank (S1). The framing told the brain the
strategies were often wrong, so it blanket-discounted even correct strategy
warnings (S2). Two strategy views were shown on disjoint coin sets with no
relationship explained (S3). The per-coin intelligence Signal and the strategy
ensemble could contradict with no guidance on how to weigh them (S4). A strong-trend
reading on near-zero relative volume passed through unflagged (S5).

Fix, all in the live rich-block prompt path: a coin with no strategy signal now
renders a truthful line instead of a blank, and a genuine no-signal is distinguished
from a data gap using the package provenance (S1). The framing is rebalanced toward
neutral — strategies are one input, neither blindly followed nor blindly dismissed,
and an ensemble that disagrees with the regime is worth investigating; the
crowded-trade sizing caution is preserved (S2). The strategy-hints section is
relabeled as additional or global signals, and states that a candidate coin's full
ensemble in its own block governs and the one-line hints do not override it (S3). A
precedence rule explains that Signal and the ensemble measure different things and
neither is automatically authoritative; on conflict the brain leans on regime and
structure and sizes smaller (S4). A strong-ADX-on-thin-volume reading is flagged as
low participation (S5). No strategy logic changed — only the completeness, honesty,
reconciliation, and framing of the evidence delivered.

Verification: seven concrete checks in verify_s_strategy_evidence.py driving the
live renderer all pass; ninety-three strategist and prompt tests pass.

## D1 — the directional bias removed to neutral

Symptom: every executed trade in the window was a long, including a long on a coin
in a trending-down regime, all driven by an "extreme fear creates strong
contrarian-buy windows" framing; just-closed losers were re-bought.

Root cause: the lean was entirely in framing the brain reads. There is no
code-level sentiment-to-direction mapping; the decision parser uses the brain's
direction verbatim, and both flip switches are off.

Fix, with no short lean introduced: the Fear and Greed section of both trade
prompts is reframed as neutral market context — it is explicitly not a direction
instruction, fear can confirm a short in a trending-down coin or mark an oversold
long in a trending-up coin (and the reverse for greed), and each coin's own regime
decides direction. The scanner's extreme-fear and extreme-greed labels were a
sentiment-to-direction surface ("contrarian long bias; smart money buys panic");
their displayed values are renamed to neutral, data-conditional setup names and
their descriptions de-editorialized, while the triggers are unchanged and still
fire only when the coin's own read already points that way, so frequency is
preserved. The recent-loser line is strengthened so a just-closed loser is not
re-bought on sentiment alone. Per-coin regime remains the sole stated direction
authority. The sentinel STRAT_REGIME_BLOCK_VERSION is bumped from 3 to 4.

Verification: eleven concrete checks in verify_d1_neutral_direction.py (lean
removed, framing symmetric with no new short bias, regime authority intact, labels
neutralized, sentinel bumped) all pass; forty-four scanner and strategist tests
pass.

## H2 — the data chooses the better-reward side or skips

Symptom: a coin was bought with long risk-reward 0.31 versus short risk-reward 1.95
(short about six times better) because the better-reward side had no trade history
and the structural veto is observe-only.

Root cause: in the live prompt the brain saw only one direction's risk-reward plus
the visible Buy-only history, so it took the worse side.

Fix, with the brain deciding from the data and no flip switch or veto enabled: the
live prompt now surfaces both directions' risk-reward (a new "RR by direction" line
sourced from the structural placement the system already computes), with an inline
instruction to take the better-reward side or skip and that a side with no trade
history is still tradeable. A systemic risk-reward check was added to both prompts'
direction guidance. A sentinel STRAT_RR_ASYMMETRY logs when a materially-better
opposite side exists.

Verification: six concrete checks in verify_h2_better_rr.py (the observed 0.31
versus 1.95 case renders with the better side marked short and the take-better-or-
skip instruction; systemic framing in both prompts; no flip or veto enabled) all
pass; the prompt-bound test was updated for the added direction-quality content.

## H3 — respect the brain's weak-setup size; skip below the real minimum

Symptom: the brain sized a small forty-dollar probe on a weak setup and the system
forced it up to one hundred dollars, oversizing the weakest trades.

Root cause: arbitrary product floors layered across the sizing path
(strategy_worker, the APEX optimizer, and the gate), none of which is the real
exchange minimum.

Fix: every arbitrary up-floor was removed so the brain's risk-based size stands.
The real exchange minimum is enforced unchanged downstream — when the size is too
small to buy one instrument step, the quantity rounds to zero and the trade is
skipped rather than oversized. The APEX log token was renamed to reflect that a
small size is now preserved, not floored.

Verification: eight concrete checks in verify_h3_size.py (a tiny size preserved, a
weak low-conviction size stands, a normal size unaffected, all four floors removed,
the exchange-minimum skip intact) all pass; twenty-seven sizing tests and ten
end-to-end pipeline tests pass; the earlier floor-holds-at-one-hundred test was
rewritten to assert preservation, by operator decision.

## H1 — cut near-certain losers

Symptom: a structure-guard blocked force-closing or tightening positions the model
itself rated at about five percent win probability, so known bleeders were held
until they stopped out.

Root cause, confirmed in src/risk/time_decay_sl.py lines 473 to 488: the guard
returned no action when win probability was below the force-close threshold and
structure looked stable, blocking the cut.

Fix, scoped strictly to the near-certain-loser case: a new configurable threshold,
near_certain_loser_p_win (default 0.10, in the time_decay config section), makes the
guard yield when win probability is at or below it, so the existing force-close
fires and the clear bleeder is cut. The guard's caution is preserved exactly where
it is right — positions in the ambiguous band between 0.10 and 0.15 are still held,
and healthy positions are untouched, so no aggressive tightening is re-introduced.
All existing backstops are unchanged: the minus three percent hard stop, the loser
timeout, the SENTINEL loss tiers, the 2.5 percent safety stop, and the minimum-age
and MAE gates that run before the guard. The profit-fetching single-writer spine is
not touched. A sentinel TIME_DECAY_STRUCT_GUARD_YIELD logs each cut. Setting the
threshold to zero disables the carve-out.

Verification: seven concrete checks in verify_h1_cut_losers.py (a near-certain loser
cut, the ambiguous band held, a healthy position untouched, the carve-out
disableable, the sentinel and full wiring present) all pass; seventy-three
time-decay and structure-guard tests pass.

## Combined verification result

All six self-verification scripts pass (exit code zero). The complete test suite was
then run in five chunks (excluding the pre-existing-broken tests/test_phase7 folder,
which has three collection errors from importing removed modules src.brain.executor
and src.brain.scheduler). Result: three thousand six hundred and twenty-seven tests
pass. The only remaining failures are two PROVEN pre-existing ones, unrelated to this
work and touching none of its files: test_apex_direction_lock rsi_caution (the phrase
"Oversold RSI in a downtrend" was removed from the system prompt by the 2026-05-05
aggressive-framing rewrite) and test_positions_exchange_mode SCHEMA_VERSION==32 pin
(the schema is at version 40). Both also fail at the base commit fb1636d, confirming
they predate this work. All touched modules import cleanly.

## Adversarial cross-check and the one regression it caught

After the six fixes shipped, an adversarial review was run with one skeptical reviewer
per fix plus a cross-cutting reviewer, each tasked with refuting that the fix was
correct, integrated, and professionally named. Five reviewers returned a clean pass.
The cross-cutting reviewer caught one real miss: the D1 commit bumped the sentinel
STRAT_REGIME_BLOCK_VERSION from 3 to 4 but did not update the test
tests/test_regime_block_symmetry.py, whose test_block_version_is_3 still asserted the
value 3, so that single test failed. This was fixed in a follow-up commit (the
assertion is now 4 and the test renamed); the rest of that test file was unaffected
and already passed. This is the honest correction to the earlier claim of verified
test health: there was one regression in the test suite from the sentinel bump, it is
now fixed, and the full suite passes apart from the two documented pre-existing
failures above.

The healthy parts are intact: per-coin regime detection, the fast upstream
assembly, the gateway's tighten-only discipline, and a sane minimum distance are all
preserved. The direction-flip switches remain off (xray_dir_flip_enabled false,
apex_dir_flip_enabled false, xray_trade_suppression_enabled false). Trade frequency
and aggression are preserved — the fixes redirect aggression onto unbiased,
correctly-analyzed opportunities rather than reducing it. No protected table,
schema, or migration was touched.

## What this does and does not mean

These fixes remove the directional bias, restore the exit system so winners are
protected, and guarantee correct strategy evidence, so the brain now decides on
honest, complete, unbiased inputs. They do not by themselves guarantee profit, and
they do not address the separate brain-latency and APEX-latency performance issues,
which are out of scope. The numeric values (the clamp boundaries, the 0.10
near-certain-loser threshold, the two-times risk-reward guidance) are reasonable
starting points and are tunable against live behavior.

## Real-project end-to-end pipeline check

A real-project pipeline harness (pipeline_check_neutrality_exit_fix.py) drives the
REAL component objects, constructed with the exact signatures the WorkerManager
uses (SLGateway with the real settings.sl_gateway; TradeOptimizer and TradeGate
with settings.apex, which is how apex_cfg is wired; TimeDecaySLCalculator from the
real time-decay config; ClaudeStrategist), loaded from the real config.toml through
Settings, with only the exchange boundary and the structure cache stubbed. It
proves dependency-injection construction, the config-to-settings-to-component data
flow, and actual runtime behavior for all six fixes. Result: twenty-three of
twenty-three checks pass.

At runtime it confirmed: the gateway clamps and actually wires the stop (the frozen
trail advances one max-step and the wire is recorded at the boundary; the NEAR
wrong-side floor clamps to the highest valid stop just below price and is wired; a
no-op makes no exchange call; tighten-only still rejects a loosening move); the
near-certain-loser cut fires from the real calculator built off the real config
value (0.10), while the ambiguous band is still held and a healthy position is
untouched; the real optimizer preserves a weak twenty-dollar size and the real
gate.validate does not floor a thirty-dollar size, while a below-exchange-minimum
size rounds to zero quantity and is skipped; and the real rich-block renderer emits
the truthful no-signal line, the thin-volume caveat, and the both-direction
risk-reward line, with the STRAT_EVIDENCE_SUMMARY sentinel firing. Two faithful
wiring details surfaced and were honored in the harness: the APEX optimizer and
gate are wired with settings.apex (not the full Settings), and the live config has
surface-briefing fields on, so a candidate package must carry a state label and
interestingness to render — both match the real system.

## Observability — how to confirm each fix is live after restart

After the operator restarts the trading workers, these log tokens confirm each fix
is active: SL_GATEWAY_R2_CLAMP and SL_GATEWAY_R3_CLAMP and a rising acceptance rate
for C2; STRAT_EVIDENCE_SUMMARY for S1; STRAT_REGIME_INSTR_REFRAMED with
block_version=4 for D1; STRAT_RR_ASYMMETRY for H2; APEX_SIZING_SMALL_SIZE and
matching claude_size and final_size in L4_BRAIN_SIZE_DECISION for H3; and
TIME_DECAY_STRUCT_GUARD_YIELD for H1.
