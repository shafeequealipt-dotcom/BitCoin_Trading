# Upstream Brain-Quality — Fourth Five Implementation Report

**Date:** 2026-05-28
**Branch:** main (direct-to-main, atomic commit per issue)
**Batch theme:** latent-landmine defusing + loose-end closure (NOT active PnL bleeds)
**Spec:** `IMPLEMENT_UPSTREAM_BRAIN_QUALITY_FOURTH_FIVE.md`

## Honest value framing

This batch removes silent-regression risk and closes loops. With one
deliberate, operator-approved exception (E26 Part B — the APEX prompt line),
**live trading behavior is unchanged by construction**. Two of the five planned
items were closed as no-action after the code disproved their premise (E14
deliberate-decision; E3 premise refuted at implementation time). That is the
correct outcome for a hygiene batch — we do not "fix" things that are already
right, and we do not remove a working guard on a shaky redundancy claim.

## Commits (all pushed to origin/main)

| Order | Commit | Issue | Live effect today |
|------|--------|-------|-------------------|
| 1 | `798d807` | #18 + E15 | none (config already overrides) |
| 2 | `442499f` | E27 | none (latent — flips disabled) |
| 3 | `b208425` | E26 | one added APEX prompt line (Part B); Part A latent |
| 4 | `d1f589b` | E20 | none (boot-time validator only) |

E14 and E3: no commit (documented below).

---

## #18 + E15 — inverted ensemble-tier GOOD defaults + auto-correct boot check (`798d807`)

- **Symptom:** the config-less `StrategyEngineSettings` GOOD consensus floor
  (`min_ensemble_agreement` 5.0 / `max_ensemble_opposition` 1.0) was STRICTER
  than the STRONG floor (4.0 / 1.5) — an inverted ladder. A vote could satisfy
  STRONG while failing GOOD.
- **Root cause:** the GOOD defaults were never corrected when the STRONG tier
  was promoted to config; the live `config.toml` (2.5 / 2.5) masked it, so the
  inversion would only bite a config-less deploy or a dropped override.
- **Wiring:** `StrategyEngineSettings` defaults (settings.py) → loader
  fallbacks `_build_strategy_engine` (E15) → a third copy in
  `EnsembleStateCache.__init__` (ensemble.py) → the boot self-check in
  `EnsembleVoter.__init__` that wires cache + live thresholds.
- **Fix:** GOOD defaults 5.0/1.0 → 2.5/2.5 in all three places (STRONG
  unchanged at 4.0/1.5 — a correct ladder). The boot self-check's
  misconfigured branch was upgraded from log-error-only to **AUTO-CORRECT**:
  it clamps STRONG to be at least as strict as GOOD on both axes, writes the
  corrected values back to cfg + cache, and logs
  `BOOT_ENSEMBLE_THRESHOLDS_AUTOCORRECTED`.
- **Before/after:** runtime byte-identical (config.toml still supplies
  2.5/2.5/4.0/1.5). The change removes the config-less-deploy landmine and
  makes any future re-inversion self-healing + loud.
- **Verification:** `verify_issue_18_e15.py` — static defaults + config-less
  ladder (STRONG > GOOD) + live-unchanged + auto-correct trips on a re-inverted
  config. 5/5 unit tests in `tests/test_layer1_defect6_ensemble_thresholds.py`.

## E27 — raise apex_min_trades_for_flip 5 → 8 (`442499f`)

- **Symptom / risk:** a direction flip was licensed once the flipped direction
  had ≥ 5 trades for the symbol in the regime. 5 is a thin sample.
- **Wiring:** `APEXSettings.apex_min_trades_for_flip` (settings.py) +
  `config.toml` value + two `getattr` fallbacks in optimizer.py (caller ~626 and
  `_check_insufficient_data_for_flip` ~1626). The gate is reached only under
  `was_flipped`, behind `apex_dir_flip_enabled` (disabled).
- **Fix:** raised to 8 in the dataclass default, the live config value, and both
  fallbacks. The DeepSeek system-prompt advisory (`src/apex/prompts.py`, "fewer
  than 5") was intentionally **left unchanged** — the code gate is stricter and
  authoritative, so touching the prompt would be an unapproved live change for
  zero functional gain.
- **Before/trigger (latent):** no live effect (flips off). When flips are
  re-enabled, a flip now needs a more durable 8-trade history.
- **Verification:** `verify_issue_e27.py` — static (default 8, config 8, both
  fallbacks 8, prompt advisory untouched) + config-load + functional gate
  (7 blocked, 8 allowed). 18/18 in `tests/test_apex_sell_bias_gates.py`.

## E26 — per-symbol + per-venue flip evidence (`b208425`)

- **Symptom:** the flip insufficient-data gate and the APEX prompt counted a
  coin's directional history pooled across ALL `exchange_mode`s
  (demo + live + paper), so a flip could be licensed on the wrong venue's
  record, and the prompt's per-coin view was venue-blind.
- **Wiring / fix (two parts):**
  - **Part A — flip gate (LATENT):** new `SymbolFlipEvidence` model +
    `flip_evidence` field on `IntelligencePackage` (defaulted None — safe for
    all 7 constructions); new `TradeIntelligenceRepo.get_symbol_flip_evidence`
    (venue-isolated directional counts + win rates); assembler
    `_gather_flip_evidence` resolves the live mode from
    `transformer.current_mode` (fail-permissive "") and attaches the evidence;
    the optimizer gate prefers the venue-isolated count when a venue filter was
    applied (logs `APEX_FLIP_EVIDENCE_VENUE`), else falls back to the pooled
    trades list. No live effect (flips off).
  - **Part B — APEX prompt (DELIBERATE LIVE CHANGE, operator-approved):**
    `prompts.py` SECTION 4b renders a per-coin + per-venue directional win-rate
    line ALONGSIDE (not replacing) the all-coins situation data, sample-gated at
    `_FLIP_EVIDENCE_MIN_SAMPLE = 8` so DeepSeek is never fed sparse per-coin
    rates. This adds one context line to each APEX user prompt when the venue
    sample is sufficient.
- **Plan deviation (honest):** the plan named `_gather_situation_data` as the
  render site; the actual live prompt (prompts.py SECTION 4) renders from model
  fields, not `condition_summary`, so the line was added in prompts.py — the
  real prompt path. Functionally equivalent intent, correct location.
- **Verification:** `verify_issue_e26.py` — static + temp-DB venue isolation
  (live Sells do not leak into the demo count) + gate preference/fallback +
  prompt sample-gating. 8/8 in `tests/test_e26_flip_evidence_venue.py`.

## E20 — extend the sweet-spot chain validator to altdata (`d1f589b`)

- **Symptom / risk:** `altdata.funding_rates` was validated only for MM:SS
  shape; nothing asserted it fires before the scanner consumes the window. A
  future config could schedule it at/after the scanner offset, silently feeding
  the scanner stale funding data.
- **Wiring / fix:** `SweetSpotsSettings.__post_init__`, after the chain-order
  check, now asserts `altdata.funding_rates` fires strictly BEFORE
  `scanner_worker` and raises `ConfigError` otherwise. The
  altdata < strategy_worker edge is intentionally NOT enforced — altdata at 1:45
  just after strategy at 1:30 is the known-benign #10 staleness, and enforcing
  it would break the shipped config at boot.
- **Before/after:** current config (1:45 < 4:00) passes unchanged; the validator
  now catches a misordered altdata offset at boot.
- **Verification:** `verify_issue_e20.py` — current config + `Settings._load_fresh()`
  pass; == scanner (4:00) and after scanner (4:30) trip; before scanner (3:00)
  and the benign 1:45 pass. 29/29 sweet-spot tests (3 new E20 cases).

---

## No-action items (documented, no commit)

### E14 — flip-confidence asymmetry — DELIBERATE OPERATOR DECISION
The flat 0.70/0.70 buy↔sell flip-confidence in `config.toml` is the operator's
2026-05-19 symmetric realignment (documented inline with a revert note). The
code dataclass defaults retain the original asymmetry (0.95/0.70) for a future
re-enable. Flips remain off. No change.

### E3 — XRAY_GRADE_CAPPED guard — PREMISE REFUTED, GUARD KEPT
The plan claimed the `XRAY_GRADE_CAPPED` cap (structure_engine.py ~1880) was
dead/redundant with #7. **At implementation time the code disproved this:**

- The guard caps an A grade (score ≥ 65) to B when `smc < 10 AND mtf_score < 3`.
- Score 65 is **reachable without SMC or MTF**: structure-alignment (+20) +
  R:R ≥ 3 (+20) + BOS-in-direction (+10) + strong structure (+5) + volume
  confluence (+5) + fibonacci confluence (+8) = 68 → A, with `smc=0, mtf=none`.
  That is exactly the Phase 25 (Y-24) defect this guard was built to stop
  ("operators trust A as structurally supported, but the structure is empty").
- #7 caps on a *different* quantity (`setup_type_confidence < 0.30`, or
  `setup_type == NONE`) than Phase 25 (`smc < 10 AND mtf_score < 3`). An
  independent re-derivation showed that, **under the current config**, every
  `classify_setup` branch's confidence does fall below 0.30 whenever
  `smc<10 AND mtf<3` (the FVG/OB branches gate on mtf ≥ 0.4-0.5; the others
  derive confidence from min/max of the same smc/mtf inputs) — so #7 *is* an
  outcome-superset today. BUT the two guards were authored independently, key
  off different metrics, and share NO constant: the redundancy hinges on three
  operator-tunable thresholds all sitting at/above #7's hardcoded 0.30 float.
  It is redundant-in-outcome now, not provably-permanent.
- The 0 live fires reflect that this combination is rare in practice (a coin
  strong enough for A usually also has a near-price OB/sweep → smc ≥ 30) AND
  that Phase 25 runs before #7 (so it would fire first), not that the case is
  unreachable. "0 fires ⇒ dead" is a non-sequitur — the A grade is reachable
  with `smc=0, mtf=0` (the arithmetic above clamps to 100).

Removing a correct, cheap, documented backstop whose redundancy depends on
separately-tunable config values aligning with a hardcoded float is exactly the
config-drift landmine this codebase's rules warn against — near-zero value
against a non-zero risk of re-opening a documented defect, the opposite of this
batch's purpose. **Guard kept (defense-in-depth).**

---

## Verification summary

- All four verifiers PASS: `verify_issue_18_e15.py`, `verify_issue_e27.py`,
  `verify_issue_e26.py`, `verify_issue_e20.py`.
- New/updated unit tests all green: defect6 (5), sell-bias (18), e26 (8),
  sweet-spot (29).
- Regression (relevant suites, `-k apex/tias/ensemble/sweet_spot/layer1_defect6/
  layer3/strategy_engine/consensus`): **316 passed, 1 skipped, 1 failed, 6 errors.**
  - The 1 failure is the known pre-existing
    `test_apex_direction_lock::test_system_prompt_still_has_rsi_caution`, which
    asserts on `STRATEGIST_SYSTEM_PROMPT` — untouched by this batch.
  - The 6 errors are pre-existing/environmental collection failures in THIS
    analysis sandbox (Python 3.10): `from datetime import UTC` needs 3.11
    (`test_positions_exchange_mode`, `test_ticker_cache_buffer`, two
    `test_phase7` files, `test_j1_prune_positions_repo`) and a removed module
    `src.brain.prompt_builder` (`test_phase7/test_prompt_builder`). None import
    any file this batch touched; they collect fine on the 3.11 deployment VM.
  - Cross-check caught + fixed an integration gap E27 introduced:
    `test_apex_flip_decision_log.py` built 5-trade fixtures that the new
    8-trade minimum now reverts as insufficient_data; bumped to 8 (commit
    `8733ca2`). 9/9 pass.
- Full A–Z regression (top-level `tests/test_*.py`, 4 alphabetical chunks,
  `--continue-on-collection-errors`): **1797 passed.** Non-passing items, all
  pre-existing / environmental, NONE attributable to this batch:
  - 1 fail `test_apex_direction_lock::test_system_prompt_still_has_rsi_caution`
    (asserts on `STRATEGIST_SYSTEM_PROMPT` — untouched).
  - 2 fails `test_j1_position_reconciler` + ~4 collection errors
    (`test_j1_prune_positions_repo`, `test_positions_exchange_mode`,
    `test_ticker_cache_buffer`, `test_phase7/*`): all the Python-3.10 sandbox
    artifact `from datetime import UTC` (3.11-only) or the removed
    `src.brain.prompt_builder` module. These collect/run on the 3.11 VM.
- Three independent read-only cross-check agents audited the batch
  (settings/ensemble/sweet-spot; the APEX E26+E27 chain; and the E3
  keep-decision): all returned PASS / decision-confirmed. The E3 agent
  independently re-derived that the A grade is reachable with `smc=0, mtf=0`
  and confirmed keeping the guard is correct (redundant-in-outcome today but
  not provably-permanent). Two non-blocking doc-drift items they found were
  fixed: the stale `config.toml` `BOOT_ENSEMBLE_THRESHOLDS_MISCONFIGURED`
  comment (now describes the auto-correct), and the dead `src/workers/settings.py`
  duplicate (zero importers — left untouched per plan, noted).
- Live-path integration cross-check (REAL objects, `Settings._load_fresh()`):
  StrategyEngine ladder correct (GOOD 2.5/2.5 < STRONG 4.0/1.5); APEX
  min_trades_for_flip=8; sweet-spots validates at load (funding 1:45 < scanner
  4:00); a real `EnsembleVoter` wires the cache to the live corrected values;
  the `flip_evidence` field is on `IntelligencePackage`; the live optimizer
  calls the edited `assemble()` (attaches evidence) and `build_apex_user_prompt()`
  (renders the per-coin line) inside `optimize()`.
- Flip switches still off: `apex_dir_flip_enabled=false`,
  `xray_dir_flip_enabled=false`, `xray_trade_suppression_enabled=false`.
- Protected tables untouched: the only new DB access is a SELECT against
  `trade_intelligence` in `get_symbol_flip_evidence`.

## Observability added

- `BOOT_ENSEMBLE_THRESHOLDS_OK` / `BOOT_ENSEMBLE_THRESHOLDS_AUTOCORRECTED`
  (#18/E15) — boot now states the ladder is sane or self-heals loudly.
- `APEX_ASSEMBLE_FLIP_EV` (E26 assembler) + `APEX_FLIP_EVIDENCE_VENUE` (E26 gate)
  + `TIAS_REPO_FLIP_EVIDENCE_FAIL` (E26 repo) — venue evidence is traceable.
- `APEX_FLIP_INSUFFICIENT_DATA` now reflects min=8 (E27).
- The E20 chain validator's coverage now includes altdata (visible at config load).

---

## Honest assessment of remaining audit items (deliver at sign-off)

These were out of scope for this batch. Verdicts:

- **E22 (held-symbols trim crowd-out):** worth attention — a real capital-
  allocation edge when many positions are held; revisit after the bleed work.
- **E25 (regime-freshness split):** low/medium — mostly a clarity refinement of
  the Call-B fresh-regime fix (#11); not urgent.
- **E28 (single-strategy cap):** medium — guards against one strategy dominating
  the ensemble; reasonable to schedule.
- **E7 (brain OI field) / E21 (OI strategies starved):** related; both depend on
  the OI data-quality work (#8 relabel shipped earlier). E21 is the higher value
  of the two — strategies starved of OI signal — but needs the OI feed verified
  live first.
- **E4 / E5 / E6 (dead-code cleanup):** low value, low risk — pure hygiene;
  bundle into a future cleanup pass, not worth a dedicated session.

No item here is an active bleed. The genuine PnL leak remains structural (entry
R:R, wd_timeout) per the live-monitoring notes — that is the next substantive
target, separate from this hygiene batch.
