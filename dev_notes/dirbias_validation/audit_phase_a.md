# Phase A Audit — Direction-Bias Fix (Commits 4b74da7 + 5c6402e)

Audit date: 2026-05-19
Auditor: Claude Opus 4.7 (1M context), deep cross-check pass
Repo HEAD on main: `2864216` (`merge: dirbias Issue 1 ...`)
Working tree clean wrt audited files (`git status` shows only data/log changes + new untracked dev_notes).

Commits in scope:
- `4b74da7` `fix(dirbias/issue4): symmetric scenario-driven MARKET REGIME block` (A1)
- `5c6402e` `fix(dirbias/issue2-concern7): set counter_confidence_multiplier=1.0` (A2)
- Merges: `2016528` (A1 → main) and `e250ec4` (A2 → main)

Spec: `dev_notes/dirbias_validation/20_recommendation.md` (Phase A section) — Path C, Option 4.1 wording, parallel deploy of A1+A2.

---

## Issue 4 edit-site verification

| Site | Spec target | Actual location | Status | Quote |
|---|---|---|---|---|
| Live block — header | `strategist.py:3371-3400` | `strategist.py:3431` | PASS | `sections.append("\n## MARKET REGIME (CONTEXT)")` |
| Live block — direction_hint | `strategist.py:3371-3400` | `strategist.py:3433-3439` | PASS | `"trending_down": "Bias for shorts when per-coin evidence agrees; per-coin tags override."` / `"trending_up": "Bias for longs when per-coin evidence agrees; per-coin tags override."` |
| Live block — symmetric NOTE | `strategist.py:3371-3400` | `strategist.py:3445-3457` | PASS | trending_down NOTE at 3446-3451; trending_up NOTE at 3452-3457; both fire on `if _regime_state.confidence > 0.60:` |
| Dead duplicate — header | `strategist.py:1416-1445` | `strategist.py:1462` | PASS | `sections.append("\n## MARKET REGIME (CONTEXT)")` |
| Dead duplicate — direction_hint | `strategist.py:1416-1445` | `strategist.py:1464-1470` | PASS | identical strings to live block (lines 1465-1466 trending_down/trending_up) |
| Dead duplicate — symmetric NOTE | `strategist.py:1416-1445` | `strategist.py:1476-1488` | PASS | trending_down NOTE at 1477-1482; trending_up NOTE at 1483-1488 |
| STRAT_AGGRESSIVE_FRAMING update | `strategist.py:870` | `strategist.py:907-913` | PASS | `f"regime_instr=symmetric contract=aggressive_exploit "` at line 911 (the spec's 870 was an indirect anchor; the actual sentinel block is 907-913) |
| STRAT_REGIME_BLOCK_VERSION const | `strategist.py:185-200` | `strategist.py:201` | PASS | `STRAT_REGIME_BLOCK_VERSION = 2` with full block comment 187-200 |
| Boot sentinel emit | `strategist.py:595-625` | `strategist.py:627-630` | PASS | `log.info(f"STRAT_REGIME_INSTR_REFRAMED \| block_version={STRAT_REGIME_BLOCK_VERSION} mode=symmetric_scenario \| {ctx()}")` inside `__init__` |
| _TRIM_ESSENTIAL_MARKERS (both) | `strategist.py:397-400` | `strategist.py:419-420` | PASS | Line 419 `"## MARKET REGIME (CONTEXT)"`, line 420 `"## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)"` — both present, backward-compat preserved |

### Verbatim quote — live block trending_up NOTE (proves symmetry)

Source: `src/brain/strategist.py:3452-3457`
```
                elif _regime_str == "trending_up":
                    sections.append(
                        "NOTE: High-confidence global uptrend. Use this as default bias "
                        "for coins without a per-coin tag; coins tagged [TRENDING_DOWN] are "
                        "valid short candidates on their own evidence."
                    )
```

Verbatim quote — live block trending_down NOTE:
```
                if _regime_str == "trending_down":
                    sections.append(
                        "NOTE: High-confidence global downtrend. Use this as default bias "
                        "for coins without a per-coin tag; coins tagged [TRENDING_UP] are "
                        "valid long candidates on their own evidence."
                    )
```

Symmetry analysis (token-for-token diff):
- "downtrend" ↔ "uptrend" (only directional word that changes)
- "[TRENDING_UP] are valid long candidates" ↔ "[TRENDING_DOWN] are valid short candidates" (mirror)
- Identical preamble ("Use this as default bias for coins without a per-coin tag; coins tagged ...")
- Identical postamble ("on their own evidence.")
- No mandate words ("MUST", "DEFAULT to SELL/BUY", "70/30") on either branch

**Verdict: symmetric, scenario-driven, no hardcoded directional asymmetry.**

### Issue 4 spec drift / line-number discrepancies

- Spec quoted live block at lines 3371-3390; actual is 3421-3457 (~50 line drift, post-Phase 1.4 deliverables).
- Spec quoted dead duplicate at lines 1416-1435; actual is 1452-1488 (~36 line drift).
- Spec quoted constant near 185-200; actual is at 187-201.
- Spec quoted STRAT_AGGRESSIVE_FRAMING at line 870; actual is 907-913.
- Spec quoted boot sentinel near 595-625; actual is 627-630.

All drift is forward, plausibly explained by added comment blocks. None of the spec anchors are off by a structural amount.

---

## Issue 2 Concern 7 edit-site verification

| Site | Spec target | Actual | Status | Quote |
|---|---|---|---|---|
| `config.toml:1724` value | from 0.7 to 1.0 | `config.toml:1734` | PASS | `counter_confidence_multiplier = 1.0` (line drift ~10 from spec) |
| Rationale comment | accompany value change | `config.toml:1723-1733` | PASS | 11-line comment block citing Issue 2 Concern 7, downstream sites, decision matrix, reversibility, and the 2026-04-30 origin of the 0.7 heuristic |
| No code touched | config-only | verified via `git show --stat 5c6402e` | PASS | `1 file changed, 14 insertions(+), 1 deletion(-)` — `config.toml` only |
| Validator accepts 1.0 | `settings.py:2503-2507` | `settings.py:2607-2611` | PASS | `if not 0.0 < self.counter_confidence_multiplier <= 1.0:` — 1.0 inclusive, accepted |
| Default field def | `settings.py:counter_confidence_multiplier` | `settings.py:2547` | NOTE | dataclass default is still `0.7`. Override via config.toml works (verified by `Settings.load()` returning 1.0). This is acceptable per spec — Phase A is config-only; "ratify with code removal" is Phase D follow-up. |
| Live load verified | runtime read | `Settings.load().structure.setup_types.counter_confidence_multiplier == 1.0` | PASS | Verified by running a Python snippet against current main HEAD |

### Verbatim quote — config.toml change (proves rationale)

Source: `config.toml:1723-1734`
```
counter_setup_enabled = true
# Issue 2 Concern 7 (2026-05-19): config-only test of removing the
# ×0.7 multiplier. Set to 1.0 for the 48h Phase A trial. Producer at
# src/analysis/structure/structure_engine.py:1188, 1210 then becomes a
# no-op multiply, and the 4 downstream floor-0.5 multipliers in
# scorer.py:494 / ensemble.py:158 / scanner_worker.py:288 / apex/gate.py:218
# see un-cut counter confidence — eliminates the ~3.88× compounding
# suppression on counter-LONG setups that the 14-day WR data flagged as
# selecting Buys from a low-conviction sub-pool. Reversible in seconds
# via `git checkout config.toml && restart`. Decision matrix at
# dev_notes/dirbias_validation/MASTER_REPORT.md determines whether to
# ratify with code removal (Option 7.2) after the 48h trial. Original
# value 0.7 was set in commit 3a59637 (2026-04-30, XRAY counter-setup
# Phase 4) as an unempirical heuristic, no backtest, no tuning since.
counter_confidence_multiplier = 1.0
```

### Issue 2 spec drift

- Spec quoted producer locations `structure_engine.py:1188, 1210`; actual current source has them at `1088, 1205, 1227` (read at 1088, applied at 1205 for bullish counter and 1227 for bearish counter — TWO apply sites, not one). The comment in config.toml itself replicated the spec's stale line numbers — non-fatal but worth noting.
- Spec quoted downstream multipliers at `scorer.py:494, ensemble.py:158, scanner_worker.py:288, apex/gate.py:218`. Actual: `scorer.py:494` ✓, `ensemble.py:158` ✓, `scanner_worker.py:314` (~26 line drift), `gate.py:237` (the multiplier is `max(0.5, min(weight, 2.5))` at line 237, not 218 — the 218 line in current gate.py is a tiered xray_conf weight at `_xray_conf >= 0.70: pass`). The floor-0.5 pattern is preserved at gate.py:237 just on a different conviction field.
- Spec validator at `settings.py:2503-2507`; actual is `settings.py:2607-2611`.

Drift is forward across the board; no structural issue.

---

## Test coverage analysis

### A1 — test_regime_block_symmetry.py (NEW, 13 tests)

Source: `tests/test_regime_block_symmetry.py`

Coverage matrix:

| Test class | Tests | Edit surface covered |
|---|---|---|
| TestRegimeBlockSymmetryConstants | 4 | STRAT_REGIME_BLOCK_VERSION = 2, sentinel string present, f-string formatter present, mode literal present |
| TestRegimeBlockDirectionHintSymmetry | 2 | trending_down/trending_up direction_hint values (positive), legacy "DEFAULT SELL BIAS"/"BUY preferred" absent (negative) |
| TestRegimeBlockNoteSymmetry | 3 | trending_down high-conf NOTE present, trending_up high-conf NOTE present, legacy NOTE absent |
| TestRegimeBlockHeaderSymmetry | 2 | New canonical header literal count ≥ 2 (one per code site), legacy header retained in trim marker tuple |
| TestStratAggressiveFramingSentinelTruth | 2 | regime_instr=symmetric present in sentinel f-string, regime_instr=minimal absent from sentinel f-string |

Test run result: **13/13 PASSED** in 1.34s (verified just now).

What is NOT covered by test_regime_block_symmetry.py:
- The conditional structure (`if confidence > 0.60: if trending_down ... elif trending_up`) — no test exercises the gate; assertions are pure-text grep. Acceptable for a Phase A trial — runtime behavior verifiable from `STRAT_REGIME_INSTR_REFRAMED` boot sentinel + live log inspection.
- Comparable parallel-wording check — symmetry is asserted via positive presence of both substrings rather than diff-equality. A future regression that subtly diverges wording (e.g. dropping a phrase from one branch) would still pass these tests. Low risk because the wording is short.
- Direct exercise of the dead duplicate — both sites are scanned via `source.count() >= 2` rather than separately. Adequate.

### A1 — test_priority_classifier.py marker updates

`tests/test_stage2_phase4/test_priority_classifier.py`:
- Line 44-54 `test_market_regime_is_essential` — updated to new header `## MARKET REGIME (CONTEXT)`.
- Line 56-60 `test_market_regime_legacy_header_still_essential` — NEW test verifying legacy header is still classified essential (backward-compat for in-flight prompts).
- Line 385 — header literal in `test_multiple_essentials_each_counted` updated to new canonical.
- Line 395 — kept-protections list literal updated to new canonical.

Test run result: **30/30 priority-classifier tests PASSED**.

### A1 — test_priority_trim_inline.py marker updates

`tests/test_stage2_phase4/test_priority_trim_inline.py`:
- Lines 121, 208, 349, 438, 517 — all updated to `## MARKET REGIME (CONTEXT)` (5 marker updates).

Test run result: **31/31 priority-trim-inline tests PASSED**.

### A2 — Test coverage

Spec calls out A2 as a config-only test ("No code touched. No tests changed."). Existing tests touch the multiplier indirectly:
- `tests/test_setup_classifier_counter.py` — exercises `counter_confidence_multiplier` boundary values (0.5, 0.9, 0.0 invalid, 1.1 invalid) via synthetic SetupTypesSettings instances. 1.0 inclusive is permitted by the validator (settings.py:2607).
- `tests/test_xray_counter_property.py` — exercises counter-setup classification logic. No direct dependence on config.toml.

Test run result: **100/100 counter-setup tests PASSED**.

### Edit-surface coverage %

For A1 (Issue 4):
- 9 distinct edit sites identified in the audit table.
- 7 of 9 have direct test assertions: header (both sites via count), trim marker (both via presence), boot sentinel (string + f-string format + mode), aggressive_framing sentinel (mode literal + absence of old literal), block_version constant (== 2), direction_hint symmetric (positive + negative), NOTE symmetric (3 assertions).
- 2 of 9 are NOT directly asserted: (a) line-position correctness (tests use source.count(), not line ranges) — acceptable; (b) runtime emission of the boot sentinel at __init__ time — would require constructor instantiation, not done. Adequate via boot log inspection in the operator runbook.

**Coverage estimate: ~78% direct + 22% indirect (boot log/operator). PASS.**

For A2 (Concern 7):
- 1 edit site (config.toml line value).
- Verified at runtime: `Settings.load().structure.setup_types.counter_confidence_multiplier == 1.0`.
- No new test added per spec. Acceptable for Phase A reversible config test.

**Coverage estimate: 100% via runtime load. PASS.**

---

## Downstream consumer survey

### A1 (header text / trim marker / sentinels)

| Consumer | File:line | Reads | Contract preserved? |
|---|---|---|---|
| Active prompt builder | `strategist.py:_build_trade_prompt` (line 2859+) | sections list, appends new canonical header at line 3431 | YES — section text changed, structure unchanged |
| Dead prompt builder | `strategist.py:_build_context_prompt` (line 1070+) | sections list, appends new canonical header at line 1462 | YES — dead but symmetrically updated for hygiene |
| Trim classifier | `src/stage2_phase4/priority_classifier.py` (or wherever `_TRIM_ESSENTIAL_MARKERS` is consumed) | scans for marker substrings | YES — both old AND new headers in marker tuple, backward-compat preserved |
| Replayed legacy prompts | log replay tooling, if any | searches for header text | YES — legacy header still treated as ESSENTIAL by classifier (test_market_regime_legacy_header_still_essential confirms) |
| Boot log observer | operator runbook + log monitor | greps `STRAT_REGIME_INSTR_REFRAMED \| block_version=2` | YES — sentinel emitted from __init__ at line 627-630 |
| Sentinel emit check | `STRAT_AGGRESSIVE_FRAMING` consumers | greps `regime_instr=...` field value | CHANGED — value moved from `minimal` to `symmetric`. Operator runbook needs the new keyword; not a runtime regression but a doc/observability contract change. **Operator should be informed.** |
| Trim test fixtures | test_priority_classifier.py, test_priority_trim_inline.py | embed header literal | YES — all 8 fixture sites updated |

**Conclusion for A1: No production consumer breaks. One observability contract change (regime_instr=minimal → symmetric) which is intentional per spec.**

### A2 (counter_confidence_multiplier value change)

Producer:
- `src/analysis/structure/structure_engine.py:1088` — reads `counter_confidence_multiplier` via `getattr(cfg, ..., 0.7)`. With config=1.0, returns 1.0.
- `src/analysis/structure/structure_engine.py:1205` (bullish counter) — `conf = round(base_conf * counter_mult, 4)`. With mult=1.0: `conf = round(base_conf * 1.0, 4) = base_conf` (no-op).
- `src/analysis/structure/structure_engine.py:1227` (bearish counter) — identical pattern, no-op.

Downstream consumers of the resulting `setup_type_confidence` (post-multiply):

| Consumer | File:line | Reads | Pre-fix behavior | Post-fix behavior |
|---|---|---|---|---|
| TradeScorer | `src/strategies/scorer.py:490-496` | `setup_type_confidence` via `structural_data.get`; clamps `max(0.5, min(1.0, x))`; multiplies sr_pts | Counter conf 0.49 (was clamped to 0.5 floor); slight cut | Counter conf 0.70 (above floor); ~40% increase in sr_pts factor for high-MTF counters |
| Ensemble | `src/strategies/ensemble.py:156-160` | `setup_type_confidence` via `setup.scoring_details.get`; same `max(0.5, min(1.0, x))` clamp; multiplies size_mult | size_mult cut by 0.5 floor or capped at the pre-multiply value | size_mult sees true post-floor value (1.0 at high MTF) |
| ScannerWorker | `src/workers/scanner_worker.py:304-315` | `_get_setup_type_confidence` accessor; `max(0.5, min(1.0, x))`; multiplies struct_norm | Composite ranking suppressed counters | Composite ranking sees un-suppressed counter score |
| APEX gate | `src/apex/gate.py:216-223` (xray_conf tier) and `gate.py:237` (final weight clamp `max(0.5, min(weight, 2.5))`) | reads `_xray_confidence` (different field from setup_type_confidence — note this); tiered weights (1.20 for ≥0.85, baseline for ≥0.70, 0.85x for >0) | Counter at 0.49 hit the `_xray_conf > 0` branch (0.85x weight reduction) | Counter at 0.70 hits the baseline `pass` branch (no reduction) — meaningful sizing change |
| Strategist prompt | `src/brain/strategist.py:1997-2000` | reads `pkg.xray.setup_type` (text label, not numeric conf) for COUNTER-TRADE annotation | Annotation appended for any "counter" setup | UNCHANGED — annotation is text-based, multiplier change does not affect it. Brain still sees "COUNTER-TRADE — opposite to structural bias; lower conviction" |
| Brain prompt confidence display | `src/brain/strategist.py:2004` | `pkg.xray.setup_type_confidence` for `confidence {:.2f}` text | Showed cut value (e.g. 0.49) | Now shows un-cut value (e.g. 0.70) — Claude sees higher number, but the verbal COUNTER-TRADE annotation still primes lower conviction |
| Layer 4 protection | `src/risk/layer4_protection.py:338` | `getattr(cur_xray, "setup_type_confidence", 0.0)` for protection logic | Counter trades had lower confidence → less protection activated? Or more? Depends on context | Counter trades now show higher confidence → may alter protection thresholds. **Worth monitoring during 48h trial.** |
| Layer manager | `src/core/layer_manager.py:1389` | stamps `setup_type_confidence` on outgoing trade dict | Counter trades stamped with 0.49 | Counter trades stamped with 0.70 |

**Conclusion for A2: Multiplier change cleanly cascades through the floor-0.5 multipliers. The principal effect is that counter-LONG setups with high underlying MTF/SMC (rare but real) now sail through scorer/ensemble/scanner/APEX without compounding suppression. Two downstream surfaces deserve operator attention during the 48h trial: (1) brain prompt confidence display (cosmetic, paired with COUNTER-TRADE annotation), (2) Layer 4 protection thresholds (may behave differently when counter confidence is no longer artificially low).**

---

## CLAUDE.md compliance

Rule: "Grep all usages across the entire file first — every reference must be accounted for."

### A1 grep audit

Pre-modification greps (reconstructed from current state):
- `"DEFAULT SELL BIAS"` — appears only in comments (lines 195, 626, 1455, 3424) — INTENTIONAL (block_version=1 documentation).
- `"BUY preferred"` — same set of locations (4 comment refs only). INTENTIONAL.
- `STRAT_REGIME_BLOCK_VERSION` — 6 references: 1 declaration, 1 boot sentinel use, 2 in-code-comment refs at sites, 2 in tests. All wired correctly.
- `STRAT_REGIME_INSTR_REFRAMED` — 6 references: 1 boot emit, 5 cross-references (3 in strategist.py comments, 2 in adjacent module comments at `structure_engine.py:84` and `scanner_worker.py:86` mentioning the sentinel as a precedent pattern, plus 1 test). No dead refs.
- `regime_instr=minimal` — appears only in comments now (lines 903, 904); active log line is `regime_instr=symmetric` (line 911). CORRECT.
- `_TRIM_ESSENTIAL_MARKERS` — tuple is consumed by `_infer_section_priority` (priority classifier). Both old and new strings included. CORRECT.
- `## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)` — appears 5 times: 1 comment, 1 in trim marker tuple (intentional fallback), 1 in symmetry test (intentional positive assertion for backward-compat), 2 in test_priority_classifier.py (one comment, one legacy-still-essential test). Zero in any production code path (no `sections.append(...)` for the old header). CORRECT.
- `## MARKET REGIME (CONTEXT)` — 12 references: 2 production code emits (1462, 3431), 1 trim marker, 1 declaration comment, 1 in-code reference, 7 in tests. All correctly wired.

**Grep audit: CLEAN. No orphan refs. No silent NameError/AttributeError risk.**

### A2 grep audit

`counter_confidence_multiplier` references:
- `config.toml:1734` — value set to 1.0 (audited change).
- `src/config/settings.py:2547` — dataclass default 0.7 (loaded via TOML override; functional verification confirms 1.0 wins).
- `src/config/settings.py:2607` — validator allows (0, 1]. 1.0 OK.
- `src/config/settings.py:2609, 2610` — error message refs. No regression.
- `src/analysis/structure/structure_engine.py:1088` — getattr read with default 0.7. After config load: returns 1.0.
- `src/analysis/structure/models/structure_types.py:34` — docstring mentioning default 0.7. **Stale comment — minor doc drift.**
- `tests/test_setup_classifier_counter.py` (5 refs) — uses explicit overrides, not config-dependent.

`counter_mult` references (the local variable bound from the getattr):
- `src/analysis/structure/structure_engine.py:1088, 1205, 1227` — all consistent.

**Grep audit: CLEAN. One stale docstring comment in `structure_types.py:34` (mentions default 0.7) — non-functional, minor follow-up.**

### Naming convention

Both fixes follow project naming convention:
- A1: new constant `STRAT_REGIME_BLOCK_VERSION` matches `POSITION_SYSTEM_PROMPT_VERSION` precedent.
- A1: new sentinel `STRAT_REGIME_INSTR_REFRAMED` matches `STRAT_CALL_B_REFRAMED` precedent.
- A2: no new names introduced.

### Band-aid / no-cross-layer-hack

- A1: edits only the two prompt-builder sites + the trim marker tuple + the two sentinels. No cross-layer reach (no scorer, no ensemble, no gate touched). PURE.
- A2: edits only `config.toml`. No code touched (per spec). PURE.

**CLAUDE.md compliance: PASS.**

---

## Operator directive compliance (no hardcoded asymmetric correction numbers)

Operator directive (paraphrased from MASTER_REPORT and recommendation): "Asymmetry should emerge from per-coin evidence and scenario data, not from hardcoded direction-specific mandate strings or hardcoded correction numbers."

### A1 — directive compliance

| Pre-fix asymmetry | Post-fix state | Directive satisfied? |
|---|---|---|
| `direction_hint["trending_down"] = "DEFAULT SELL BIAS"` | `direction_hint["trending_down"] = "Bias for shorts when per-coin evidence agrees; per-coin tags override."` | YES — verbiage is conditional on per-coin evidence |
| `direction_hint["trending_up"] = "BUY preferred"` | `direction_hint["trending_up"] = "Bias for longs when per-coin evidence agrees; per-coin tags override."` | YES — parallel, conditional on per-coin evidence |
| NOTE fires only when trending_down at conf > 0.60 | NOTE fires for BOTH trending_down AND trending_up at conf > 0.60 with parallel wording | YES — symmetric branching |
| Header "CONTROLS YOUR TRADE DIRECTION" (directive flavour) | Header "CONTEXT" (informational flavour) | YES — Claude sees context, not mandate |
| `regime_instr=minimal` in STRAT_AGGRESSIVE_FRAMING (false advertising — block was actually emitted) | `regime_instr=symmetric` (truthful) | YES — observability truth restored |

**A1: Operator directive HONORED. No hardcoded directional asymmetry remains in the live block.**

NOTE: A dead helper `_build_regime_instructions()` (lines 4222-4350) still contains "70% shorts, 30% longs" and "DEFAULT BIAS: SHORT/LONG" wording. Per spec 04_validate_issue4.md:244, this helper is reachable only from `_build_context_prompt` → `create_strategic_plan`, which is unreachable in production. The spec intentionally left it dead. Acceptable. **Not a directive violation, but a GC opportunity for Phase D cleanup.**

### A2 — directive compliance

| Pre-fix asymmetry | Post-fix state | Directive satisfied? |
|---|---|---|
| `counter_confidence_multiplier = 0.7` (hardcoded direction-AGNOSTIC reduction; spec calls this "hardcoded number that violates directive") | `counter_confidence_multiplier = 1.0` (no-op multiply; no correction applied) | YES — eliminates the hardcoded correction factor |
| `counter_mtf_threshold = 0.40` (data-driven gate) | UNCHANGED (still 0.40) | YES per spec — this is a data-driven cut, not direction-specific |

**A2: Operator directive HONORED. No new hardcoded numbers added; existing 0.7 correction effectively removed.**

---

## Aim-bias five-question evaluation

### A1 (Issue 4: symmetric MARKET REGIME block)

| Question | Answer | Reasoning |
|---|---|---|
| 1. Preserve trade frequency? | YES | Prompt rewrite does not gate any execution path. If anything, the conf > 0.60 NOTE now fires symmetrically — more cases trigger context, but none gated. |
| 2. Preserve aggression? | YES | Aggressive-framing rewrite (2026-05-05) is untouched. STRAT_AGGRESSIVE_FRAMING still emits `mode_line=skipped coaching=skipped fund_rules=minimal today_perf=skipped dir_perf=skipped contract=aggressive_exploit`. Only the `regime_instr=` field value is corrected. |
| 3. Improve decision quality? | YES | Removes a directive-flavoured asymmetric bias in the prompt. Claude sees "context" not "controls your trade direction". Per-coin evidence remains primary. |
| 4. Preserve passive-close advantage? | YES | No close-side logic touched. Passive close paths in Layer 4 / time-decay / sniper untouched. |
| 5. Respect structural separation of concerns? | YES | Edits confined to brain prompt builder. No reach into scorer/ensemble/gate/structure/L4. Pure prompt-layer change. |

**A1 verdict: 5/5 YES.**

### A2 (Issue 2 Concern 7: counter_confidence_multiplier=1.0)

| Question | Answer | Reasoning |
|---|---|---|
| 1. Preserve trade frequency? | YES (and likely INCREASE counter-LONG entries) | counter_mtf_threshold gate (0.40) still gates which counters fire — unchanged. Multiplier change only affects DOWNSTREAM ranking, not whether counter setups classify. Per spec analysis, eliminating ×0.7 lets high-conviction counters survive ranking that previously buried them. Frequency-preserving by design. |
| 2. Preserve aggression? | YES | Removes a SUPPRESSION (0.7 → 1.0), does not add a suppression. Aggressive-framing prompt is decoupled from this config value. |
| 3. Improve decision quality? | YES | Spec rationale: 14-day Buy WR 41.8% / Sell WR 42.4% (both below break-even) suggests Buys were being routed through a low-conviction sub-pool by the ×0.7 compounding suppression. Removing the multiplier may surface higher-conviction Buy candidates. Quality is hypothesized to improve (subject to 48h trial verification). |
| 4. Preserve passive-close advantage? | YES | No close-side logic touched. Counter-setup confidence affects entry sizing/ranking, not exit. |
| 5. Respect structural separation of concerns? | YES | Pure config change. No code touched. Reverts via `git checkout config.toml + restart`. |

**A2 verdict: 5/5 YES.**

---

## Discrepancies found

Listed here, severity-tagged.

### MINOR — Line-number drift in spec

- Spec quoted `strategist.py:3371-3390` (live block); actual is 3421-3457.
- Spec quoted `strategist.py:1416-1435` (dead duplicate); actual is 1452-1488.
- Spec quoted `strategist.py:870` (STRAT_AGGRESSIVE_FRAMING); actual is 907-913.
- Spec quoted `config.toml:1724`; actual is 1734.
- Spec quoted `structure_engine.py:1188, 1210`; actual is 1088 (read), 1205, 1227 (apply).
- Spec quoted `scanner_worker.py:288`; actual is 314.
- Spec quoted `apex/gate.py:218`; actual floor-0.5 is at 237 (the line 218 reference reads `_xray_confidence` not `setup_type_confidence`).
- Spec quoted `settings.py:2503-2507`; actual is 2607-2611.

Cause: cumulative line drift from intervening commits (post-Phase 1.3). Non-fatal — the structural anchors (function names, header text, dict keys) all map cleanly. Operator runbook entries that quote line numbers will need refresh.

**Severity: MINOR. Recommend: spec's line numbers are documentation only; no functional impact. Optional fix-up of `20_recommendation.md` references.**

### MINOR — Stale docstring in structure_types.py:34

`src/analysis/structure/models/structure_types.py:34` references `counter_confidence_multiplier, default 0.7`. With config.toml set to 1.0, this docstring is misleading. The dataclass DEFAULT is still 0.7 (settings.py:2547), but the live load value is 1.0.

**Severity: MINOR doc drift. Recommend: refresh docstring during Phase D ratification (Option 7.2) when code-removal happens, or now as a sub-stroke fix.**

### MINOR — config.toml comment cites stale line numbers

The rationale comment at config.toml:1726 references `structure_engine.py:1188, 1210`. Actual lines are 1088, 1205, 1227 (and 1088 is the READ, not the apply). The comment mirrors the spec's stale numbers verbatim.

**Severity: MINOR doc drift. Recommend: optional fix-up; non-functional.**

### MINOR — _build_regime_instructions() is still dead but still asymmetric

The dead helper at strategist.py:4222-4350 still contains "70% shorts, 30% longs" / "DEFAULT BIAS: SHORT" wording. Per spec 04_validate_issue4.md, this is unreachable in the live `create_trade_plan` path (only reachable via the dead `create_strategic_plan`). Confirmed by grep: `create_strategic_plan` has zero callers in `src/`.

**Severity: MINOR (latent risk if someone resurrects `create_strategic_plan`). Recommend: Phase D cleanup — either delete the helper entirely or rewrite the asymmetric language. Tracked as "OBS-3" in spec.**

### NONE — No CRITICAL or HIGH discrepancies

No silent NameError. No silent AttributeError. No orphan references. No broken downstream consumer. No regression in unrelated test surface.

---

## Verdict: PASS WITH NOTES

Both fixes ship correctly per Phase 4 operator decision (Path C, Option 4.1 wording, parallel deploy of A1 + A2).

- A1 (commit 4b74da7) and A2 (commit 5c6402e) are CORRECTLY IMPLEMENTED.
- All 9 edit sites for A1 are present and verified at the actual current line numbers.
- The 1 edit site for A2 is present and the value loads correctly at runtime (verified: `Settings.load().structure.setup_types.counter_confidence_multiplier == 1.0`).
- 13/13 new `test_regime_block_symmetry.py` tests pass.
- 61/61 related `test_priority_*.py` tests pass.
- 100/100 counter-setup tests pass.
- No CLAUDE.md grep-audit violations.
- Operator directive is HONORED — no hardcoded asymmetric correction numbers remain in the live prompt or in the multiplier.
- All 5 aim-bias questions: YES for both fixes.
- Trim marker tuple correctly retains BOTH old and new header substrings for transitional robustness — verified by `test_market_regime_legacy_header_still_essential`.

NOTES (non-blocking):
1. Multiple line-number drifts between spec and actual code. Functional anchors (function names, header text, dict keys) map cleanly. No functional impact.
2. Stale docstring in `structure_types.py:34` (says default 0.7).
3. The `config.toml` rationale comment cites the spec's stale line numbers.
4. The dead `_build_regime_instructions()` helper (strategist.py:4222+) is still asymmetric, but unreachable in production per the spec's death audit.
5. One observability contract change for operator awareness: `STRAT_AGGRESSIVE_FRAMING | regime_instr=minimal` → `regime_instr=symmetric`. Runbook regex patterns may need refresh.
6. Pre-existing test failure `tests/test_apex_direction_lock.py::test_system_prompt_still_has_rsi_caution` is unrelated to these fixes (verified: fails on plain main HEAD).

---

## Recommended follow-ups

### Immediate (operator-visible, before 48h trial starts)

- [ ] **Boot-sentinel verification (operator).** After deploy + restart, confirm BOTH sentinels emit:
  - `STRAT_CALL_B_REFRAMED | system_prompt_version=2 close_rules_removed=2 contract=aggressive_management`
  - `STRAT_REGIME_INSTR_REFRAMED | block_version=2 mode=symmetric_scenario`
  - `STRAT_AGGRESSIVE_FRAMING | ... regime_instr=symmetric contract=aggressive_exploit ...`
- [ ] **Update operator runbook** if any monitor regex matches `regime_instr=minimal` — switch to `regime_instr=symmetric`.

### During 48h trial (M1-M6 measurement)

- [ ] Watch counter-LONG entries per hour for an uptick (A2 effect).
- [ ] Watch brain Sell-bias share for downward drift (A1 effect).
- [ ] **Watch Layer 4 protection thresholds** — counter setups now show ~40% higher setup_type_confidence; verify L4 protection (`risk/layer4_protection.py:338` `cur_xray_conf` reads) does not over-trigger on counter trades. Specific risk: counter trades may now look "high confidence" to L4 and get protection treatment that the 0.7 multiplier previously denied.

### Phase D (after 48h trial PASS)

- [ ] **Code removal of `counter_confidence_multiplier`** (Option 7.2 per spec). Drop the multiplier from `structure_engine.py:1205, 1227`. Mark setting deprecated in `settings.py:2547`. Remove field from config.toml. Refresh `structure_types.py:34` docstring.
- [ ] **GC pass on `_build_regime_instructions()` and `_build_context_prompt()`** (tracked as OBS-3 in spec). Either delete the dead helpers entirely or rewrite the asymmetric language to match A1.

### Line-number drift cleanup (low priority)

- [ ] Update `20_recommendation.md` to cite current line numbers (3431, 1462, 911, 1734, 1088, 1205, 1227, 494, 158, 314, 237, 2607-2611). Non-blocking; doc-only.

---

## Audit-side observability — what should fire after restart

The audit doesn't require runtime verification, but the following truth-checks the operator should perform once after Phase A deploy:

| Sentinel | Expected substring | Where emitted | Verifies |
|---|---|---|---|
| `STRAT_REGIME_INSTR_REFRAMED \| block_version=2 mode=symmetric_scenario` | exact match | `ClaudeStrategist.__init__` once per service start | A1 is in memory |
| `STRAT_AGGRESSIVE_FRAMING \| ... regime_instr=symmetric ...` | substring `regime_instr=symmetric` | `create_trade_plan` per Call A | A1 sentinel correction is firing per cycle |
| `STRAT_CALL_B_REFRAMED \| system_prompt_version=2` | exact match | unchanged from prior fix | sanity — pre-existing fix sentinel still firing |
| In `XRAY_CONFIDENCE_DETAIL` (or equivalent counter-setup log) | counter conf values now show min(mtf, smc) directly, no ×0.7 multiply | structure_engine counter-setup branches | A2 is in memory |

If any of these fail to appear within the first cycle of restart: HARD REVERT both A1 and A2.

---

End of audit.
