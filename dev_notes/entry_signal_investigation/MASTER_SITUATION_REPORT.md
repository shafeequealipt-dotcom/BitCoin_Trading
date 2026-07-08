# MASTER SITUATION REPORT — Entry Signal Pipeline

Read-only investigation. No code changed. No fixes proposed. This report is the verified map of the entry pipeline as it stands on 2026-05-21. The operator reads this before any fix is designed.

Scope: the entry path from raw signals to executed trade — Layers 1A/1B/1C/1D, Layer 2 (strategist CALL_A), Layer 3 (APEX assembler/gate/optimizer). Out of scope: Layer 4 protection, position management (CALL_B), execution adapters.

This synthesis distils the eight phase reports in `dev_notes/entry_signal_investigation/`. Cross-references to those files are in the body.

---

## 1. The Five Correlation Claims — Verdicts

Detail: `phase3_verification_summary.md`. Sample: 225 closed bybit_demo trades in 2026-05-20 05:46 → 2026-05-21 12:40.

### Claim 1 — "Buy-side broken in every regime"

**PARTIALLY reproduced.** The Buy/Sell loss-rate headline holds (Buy 55.3% L vs Sell 46.7% L). The specific cell claim "volatile + Buy is 79% loss / −$53.70" is **CONTRADICTED** — the DB shows volatile + Buy at 51% loss, +$135 net. The Buy weakness concentrates in `trending_up + Buy` (65% loss, −$77) and `ranging + Buy` (56% loss, −$59), NOT in volatile.

The prior log-based analysis suffered from a ~34% sampling bias: it matched `REGIME_PERCOIN` events within ±30 s of `BRAIN_DO_TRADE`, leaving 80/234 trades in a "?" bucket that happened to be net-positive. The DB's `entry_regime` column is the authoritative source (99% coverage in window).

### Claim 2 — "bullish_structural_break × volatile is heavily negative"

**PARTIALLY reproduced.** The cell exists and lost 100% in this window (3 trades, −$120). Sample is smaller than the prior claim (3 vs 8). Broader claim that `bullish_structural_break` overall is a losing setup (14 trades, 71% loss, −$200 net) REPRODUCES cleanly. Counter-claim: `bullish_fvg_ob × volatile` is the BEST cell (+$184 / 43 trades). "Volatile is bad" does not generalize.

### Claim 3 — "Ensemble herding inversely correlated with outcome"

**FULLY reproduced.** Trades where ≤4 strategies agreed: 107 trades, 44% loss, **+$300 net**. Trades where 5+ strategies agreed: combined negative or barely positive. WEAK consensus (40% loss) outperforms GOOD (58% loss) and STRONG (49% loss) on per-trade basis.

### Claim 4 — "strong_buy label is mis-calibrated"

**REPRODUCED on loss rate, REFINED on PnL.** `strong_buy` 54.9% loss rate (113 trades, +$199 net). `buy` 44.4% loss rate (90 trades, +$4 net). The loss-rate inversion is real. But `strong_buy` IS net-positive — its higher per-win magnitude offsets the higher loss rate. The mis-calibration is about distribution shape, not edge direction.

### Claim 5 — "Momentum strategies net-negative; contrarian/event-driven positive"

**FULLY reproduced.** A4_ema_crossover −$196 (was −$200), H4_order_flow −$162 (was −$161), B1_volume_breakout −$92 (was −$90); G4_whale_shadow +$66 (was +$61), I4_hourly_close +$107 (was +$106), B2_supertrend +$71 (was +$70), G1_stop_hunt +$48 (was +$48). All 8 momentum/trend/breakout strategies net-negative. All 4 contrarian/event-driven (G1, G4, I4, B2) net-positive. Ensemble weights all uniform at 1.0 by default.

---

## 2. Entry Pipeline Map — Verified Topology

Detail: `phase1_pipeline_code_map.md` (full file-by-file), `phase2_config_map.md` (config).

```
schedule order each 5-min cycle:

  0:45  structure_worker  → XRAY setup_type (regime-blind)        [Layer 1B]
  1:00  signal_worker     → SIG_CLASSIFY label  (regime-blind)    [Layer 1B]
  1:15  regime_worker     → per-coin regime (HERE the value is produced)
  2:00  strategy_worker   → 36 strategies vote (regime-no-op)     [Layer 1C]
        ensemble.vote     → consensus + final_size_mult (regime-no-op)
  4:00  scanner_worker    → briefing/exclusion ranking (regime-weighted)
  5:00  strategist CALL_A → Claude picks trades, sides, sizes     [Layer 2]
        APEX assembler    → IntelligencePackage (regime in TIAS filter)
        APEX optimizer    → DeepSeek call, parse, lock, clamp     [Layer 3]
        APEX gate         → 14 checks (regime in 0 of them)
        Execute
```

Critical structural facts:
- **Setup_type and SIG_CLASSIFY are computed BEFORE the cycle's per-coin regime is refreshed** (structure @ 0:45 and signal @ 1:00 vs regime @ 1:15).
- **`registry.get_active_for_regime(regime)` ignores its argument** (registry.py:44-53). Strategy_worker and ensemble call it with regime, but it returns every enabled strategy regardless. Regime is plumbed but inert at the strategy-activation layer.
- **The ensemble's `final_size_mult` is computed and discarded.** It lives on `EnsembleResult.size_multiplier` but no field on `CoinPackage` carries it forward, so the strategist never reads it.

---

## 3. Regime-Blind Spots

Detail: `phase5_regime_usage.md`. Of 20 sequential decision points in the entry path, only 6 are regime-conditional. Of those 6, only 2 directly affect whether a trade happens (scanner exclusion gate; state-labeler soft haircut). The remaining 4 (inside APEX) affect modifications, not initiation.

### Where regime is consulted (16 sites)
Listed in `phase5_regime_usage.md` Section "Consumers". Highlights:
1. Scanner opportunity score (1 of 6 weighted components, weight=0.13).
2. Scanner exclusion-mode `_regime_aligns` HARD GATE — **but production runs `briefing` mode**, where this gate does NOT run.
3. State-labeler soft regime haircut (default 0.5×) on 8 of 22 label triggers.
4. Strategist user prompt regime tags — advisory text to Claude, not code-enforced.
5. APEX assembler TIAS history regime-filter.
6. APEX optimizer composite lock signal (regime contributes ±1 with weight 1.0; threshold default 0.0).
7. APEX optimizer RR-boost and confidence-gate SCOPE (only fire when regime is NOT trending/volatile).

### Where regime is available but NOT used (16 sites)
1. `structure_engine.classify_setup` — setup_type classification.
2. `signal_generator._evaluate_signal` — SIG_CLASSIFY classification.
3. `confidence.calculate`.
4. `strategy_worker` strategy selection.
5. `ensemble.vote` strategy selection.
6. Ensemble consensus thresholds.
7. Ensemble `final_size_mult`.
8. Per-strategy `ensemble_weight` (uniform 1.0).
9. Strategist CALL_A direction-decision (in code).
10. Strategist CALL_A conviction/size (in code).
11. APEX gate Check 0-14.
12. APEX gate `zero_conviction` reject.
13. APEX gate `reentry_cooldown`.
14. TRADE_SYSTEM_PROMPT direction rules (text-only).
15. TRADE_SYSTEM_PROMPT F&G contrarian (text-only).
16. SIG normalisation formulas.

### The single visible regime-conditional refusal in production

The state-labeler's regime soft-haircut at `0.5×` (config.toml:803 `counter_regime_confidence_haircut`). This LOWERS a label's confidence by half when the label's expected regime doesn't match. It does NOT block — the label still fires. Net effect: the labeled trade is less likely to be selected as briefing's `primary` label or rank highly in interestingness, indirectly reducing prompt inclusion. But once a trade is in the prompt, the strategist's decision is unaffected by this haircut.

**Conclusion**: the entry pipeline is structurally regime-blind. There is no code path that says "do not take Buy in trending_up regime" or "skip bullish_structural_break in volatile". These checks rely entirely on Claude's interpretation of regime-tagged text in the prompt.

---

## 4. Where Size Rises With Herding

Detail: `phase4_ensemble_logic.md` + `phase7_sizing_logic.md` + `phase3_claim3_herding.md`.

The system has 4 layers of sizing influence:

1. **Ensemble `final_size_mult`** = `CONSENSUS_SIZE[label] × clamp(setup_type_confidence, 0.5, 1.0)`. Domain `[0.075, 1.0]`. **DISCARDED — not read by anything downstream.**
2. **Claude picks `size_usd`** under the per-trade ceiling. The ensemble consensus LABEL string is in the prompt; Claude sizes up on STRONG. This is where the herding-amplification actually happens — in Claude's reasoning, not in code.
3. **APEX optimizer** clamps `[max(100, _conviction_floor × proposed), 1200]`. `conviction_floor=0.5`.
4. **APEX gate** runs 7 size-modifying checks. Check 4 (conviction-weighted capital ceiling) is the main computed cap.

### Is there a code-level cap or inversion above STRONG consensus? NO

`CONSENSUS_SIZE = {STRONG:1.0, GOOD:0.75, LEAN:0.50, WEAK:0.30, CONFLICT:0.15}` is monotonic in agreement strength. The `single_strategy_max_share` setting (default 1.0 = disabled) caps any single strategy's contribution, but does NOT cap the consensus → size relationship.

### Data point that captures the mismatch
Phase 3 Claim 3: ≤4 supporters made +$300 / 107 trades; 9+ supporters made +$20 / 29 trades. The system's size-up-on-STRONG behaviour (via Claude) amplifies the worse cohort. There is no code mechanism to invert this.

---

## 5. Where strong_buy Drives Size Despite Calibration

Detail: `phase6_signal_classifier.md` + `phase3_claim4_label_calibration.md`.

The label `strong_buy` fires at `direction_score >= 0.55`. The label `buy` fires at `direction_score >= 0.18` (lowered from 0.25, deliberately — settings.py:3034-3038). The thresholds compare absolute values but observed `direction_score` is BUY-leaning, so BUY fires earlier than SELL would in mirror conditions.

In the analysis window:
- `strong_buy`: 113 trades, 54.9% loss, +$199 net.
- `buy`: 90 trades, 44.4% loss, +$4 net.

**The label hierarchy is INVERTED**: `strong_buy` has higher loss rate than `buy`. Yet `strong_buy` produces bigger per-win magnitude — Claude (and possibly the strategist's prompt construction) treats `strong_buy` as a high-conviction signal and selects sizes accordingly.

### Where does `strong_buy` drive size in code? NOWHERE DIRECTLY

Per Phase 1 strategist map: the SIG_CLASSIFY label STRING does NOT reach the CALL_A prompt unless `[stage2].enable_full_layer_block=True` (default False). The strategist surfaces only `pkg.signals.confidence` and `pkg.signals.direction`, not the label name.

So `strong_buy` does NOT propagate as a string into Claude's CALL_A prompt in production. Yet the size effect is visible in outcomes. Mechanism: confidence value tied to the strong_buy label (typically high, >0.60) carries through to the prompt, and Claude sees a high `Signal: confidence X.XX direction Buy` line. Claude infers strong conviction from the high confidence number, even without seeing "strong_buy" as a string.

This means the `buy_threshold = 0.18` asymmetric calibration matters because it changes which trades get labeled — and a higher confidence is computed when labeled `strong_buy`. The label name doesn't matter, but the threshold that gates the label name DOES affect downstream confidence numbers.

---

## 6. Where Buy-Side Diverges From Sell-Side In The Pipeline

Detail across `phase1_pipeline_code_map.md` and `phase3_claim1_regime_side.md`.

### Asymmetries in code

1. **`buy_threshold=0.18` vs `strong_threshold=0.55`** (signal_generator.py / settings.py:3045-3046). Asymmetric only in absolute terms because input distribution is BUY-skewed.
2. **`apex_min_flip_confidence_buy_to_sell=0.95` vs `apex_min_flip_confidence_sell_to_buy=0.70`** (settings.py:2299-2300). The APEX confidence gate makes flipping a Buy → Sell MUCH HARDER than the reverse. The system actively PROTECTS Buy trades from APEX flipping. Per the comment at settings.py:2287-2293, this is intentional.
3. **TRADE_SYSTEM_PROMPT** text at strategist.py:151: "Use leverage 3-5x on testnet — this is paper money, we need meaningful results." — direction-neutral but encourages aggression. The May-19 direction-bias fix replaced the older asymmetric "DEFAULT SELL BIAS" wording with symmetric scenario language (strategist.py:200-210, :441-447, :1553-1561, :3561-3596). Per `STRAT_REGIME_BLOCK_VERSION=2`.

### Asymmetries in outcome (from Phase 3)

- Buy overall 55.3% loss / +$116 net (103 trades).
- Sell overall 46.7% loss / +$79 net (122 trades).
- `trending_up + Buy`: 65% loss, −$77 net (17 trades) — worst regime cell.
- `ranging + Buy`: 56% loss, −$59 net (39 trades).
- `volatile + Buy`: 51% loss, +$135 net (39 trades) — winner.
- `trending_up + Sell`: 53% loss, +$14 net.
- `ranging + Sell`: 44% loss, −$13 net.
- `volatile + Sell`: 44% loss, +$100 net.

The Buy weakness is REAL but REGIME-CONDITIONAL. The pipeline has no regime-conditional Buy filter — see Section 3.

---

## 7. The Hardcoded Value Catalogue (Aggregate)

Detail: `phase1_pipeline_code_map.md` + `phase2_config_map.md`.

Total counted: **~150+ hardcoded literals** governing entry decisions across the pipeline. By file:

| File | Approx hardcoded gates | Notes |
|---|---|---|
| structure_engine.py classify_setup | 10 | fvg_ob_min=0.7, sweep_min_pct=0.5, counter_mult=0.7, etc. |
| signal_generator.py + signal_models.py + confidence.py | 27 | including 5 sets of DEAD constants in signal_models.py |
| ensemble.py | 11 | STRONG threshold (4.0/1.5), WEAK threshold (1.5/1.5), CONSENSUS_SIZE map |
| regime.py | 9 | choppiness ceilings, confidence formulas, fallback values |
| scanner_worker.py | 5 | normalisation divisors, default validator thresholds |
| state_labeler.py | 18 | every trigger threshold; docstring promised a config block that never landed |
| strategist.py | 30+ | system prompt rules, parser defaults, prompt size caps |
| optimizer.py | 25+ | tier thresholds, lock weights, size caps |
| gate.py | 17 | 14 checks with various clamps and multiplier bands |
| prompts.py | 8 | volatility-class TP/SL ranges, regime rules in text |

The single most prominent regime-blind hardcode cluster is in `state_labeler.py` (18 hardcoded thresholds). The docstring at state_labeler.py:48-51 explicitly says a `[scanner.briefing.label_thresholds]` config block was planned for Phase 4 but never landed.

The hardcoded ensemble STRONG threshold (`agreeing>=4.0 AND opposing<=1.5` at ensemble.py:263) is particularly impactful — it cannot be tuned without code change, and it's the single threshold that produces ~50% of the entry sample bearing the "STRONG" label.

---

## 8. What Data Exists vs What's Missing

Detail: `phase0_preflight.md` + discoveries throughout Phase 3.

### Data that EXISTS and is reliable
- `trade_intelligence` (2,345 rows) — rich entry-time context: `entry_regime, regime, claude_signal, claude_confidence, fear_greed_value, entry_score (always 0!), apex_*` fields, technical indicators at close, mode4 metrics.
- `trade_history` (1,104 rows) — closed-trade ledger with PnL.
- `trade_log` (2,787 rows) — lifecycle with thesis text + close_reason + hold_minutes.
- `signals` (123,210 rows) — but recent rows missing `sell` / `strong_buy` / `strong_sell` labels (see below).
- `claude_decisions` (2,888 rows) — strategist CALL records.
- `coin_regime_history` (18,081 rows) — per-coin regime audit.

### Data that DOES NOT EXIST or is empty
- **`ensemble_votes` table is EMPTY** (0 rows). Declared at migrations.py:434-446; **NEVER WRITTEN TO**. Per-trade per-strategy votes have no DB record.
- **`trade_intelligence.ensemble_votes` JSON column is NULL for all 2,345 rows.** Designed to hold per-trade vote JSON; never populated.
- **`trade_intelligence.entry_score` is 0 for all 230 in-window rows.** Never written.
- **`brain_decisions` table is EMPTY.** Active path is `claude_decisions` instead.
- **`active_strategies` table is EMPTY** (0 rows).
- **Per-strategy `ensemble_weight` is purely in-memory.** The Optimizer mutates weights in `StrategyRegistry`'s in-memory dict, but `strategy_performance` table has no `ensemble_weight` column. On restart, every strategy resets to 1.0.
- **`signals` table recent persistence gap.** Window data has only `buy` and `neutral`; logs for the same window show 1,522 `strong_buy` + 2,219 `buy` + 359 `neutral`. The DB persistence is dropping ~60% of recent SIG_CLASSIFY events. Older periods worked.
- **`cycle_metrics` columns** for `signal_buy_pct/sell_pct/neutral_pct/xray_setup_type_count/regime_distribution_json` (migrations.py:1288-1292) are durably NULL. The per-cycle aggregator was a "wired in a follow-up commit" promise that never landed.
- **`regime_history` table is write-only audit** per cleanup_worker.py:82 — no src/ code reads it. `coin_regime_history` is the live path.
- **`coin_regime_history` restore loses `volume_ratio` and `atr_percentile`** — schema has only regime/confidence/adx/choppiness. On cold-start restore, the worker fabricates volume_ratio=1.0 and atr_percentile=0 (regime_worker.py:107-117).

### Per-trade reconstruction sources (when DB lacks the data)
- Per-strategy votes per trade → only via `STRAT_VOTE_TRACE` log line, which **fires only when consensus=="STRONG"** AND `vote_trace_enabled=True`. Non-STRONG trades have NO per-strategy record anywhere.
- Setup_type per trade → only via `TIME_DECAY_STRUCT_GUARD` log line, which fires DURING the hold. Trades that close quickly may not have a STRUCT log record.
- SIG_CLASSIFY label per trade → log `SIG_CLASSIFY` event, attached by symbol+time. Reliable.
- Entry regime per trade → DB `trade_intelligence.entry_regime` (99% coverage in window). Reliable.

---

## 9. Discrepancies Caught During The Investigation

Not failures, findings. Each documented for the operator.

1. **Two competing normaliser ladders in signal_generator.py.** Confidence path uses F&G `/50`, funding `*100`, OI `/20`. Classifier path uses F&G `/30`, funding `/0.005`, OI `/5`. Same raw input, different magnitudes in the two paths.
2. **`_sentiment_consumption_enabled` default mismatch.** signal_generator.py:76 defaults `True`; settings.py:1825 defaults `False`.
3. **`registry.get_active_for_regime` ignores its argument.** Both strategy_worker (`:573`) and ensemble (`:171`) call it with regime, but registry.py:44-53 returns every enabled strategy regardless.
4. **Ensemble STRONG hardcode is LOWER than GOOD threshold.** STRONG `agreeing>=4.0 AND opposing<=1.5` is reachable; GOOD config-driven `agreeing>=2.5 AND opposing<=2.5` is the second branch. Branch ordering means trades that meet the 4.0/1.5 floor are STRONG, not "ambiguous between STRONG and GOOD."
5. **EnsembleStateCache replay disagrees with EnsembleVoter on GOOD threshold.** Cache hardcodes `>=5.0/<=1.0` (ensemble.py:108); live voter reads config (`>=2.5/<=2.5` in production). The cache and the live voter disagree when toml overrides are active.
6. **APEX TP-cap mismatch.** Optimizer enforces `{medium:1.6, high:1.8, extreme:2.0}`; prompt shows DeepSeek `{medium:1.3, high:1.4, extreme:1.5}` (models.py:149-153). DeepSeek likely respects the tighter displayed cap.
7. **APEX flip-confidence asymmetry.** `apex_min_flip_confidence_buy_to_sell=0.95` vs `_sell_to_buy=0.70` — actively protects Buy entries from APEX flipping.
8. **`_cap_mult_map` between models.py and optimizer.py disagree.** See #6.
9. **State labeler funding-extreme boundary disagrees with qualitative gate.** Labeler `0.0015` (state_labeler.py:399) vs qualitative `funding_blocker_threshold_pct=0.001` (settings.py:1159) — 50% gap.
10. **State labeler BREAKOUT_PENDING bearish arm is broken.** Labeler at `:368` expects `bearish_range_breakout`; producer at structure_types.py:48 emits `bearish_range_breakdown`. String mismatch — bearish arm never fires.
11. **Three state-labeler labels are functionally dead** because scanner never populates their required inputs: `OB_MITIGATED_FVG_ONLY_*` (in_direction_fvg/ob_present), the second branch of BREAKOUT_PENDING (range_compression), MOMENTUM_BURST volume_ratio gate, RANGE_FADE/FUNDING_EXTREME_FADE position_in_range gate.
12. **Scanner exclusion-mode `_enrich_for` bug.** Reads wrong attribute names (`pkg.price` instead of `pkg.price_data`) at scanner_worker.py:1954-1956. Exclusion-mode `active_universe` writes always have zero volume/change/funding. Dormant in production (default = briefing) but active in `ab_mode="alternating"`.
13. **The strategist's setup × regime gate does not exist.** The string `bullish_structural_break` is absent from strategist.py. No code path refuses any setup × regime combination.
14. **The May-19 three-agent direction-bias fix has no `R1/R2/R3/R4/ALPHA/BETA/GAMMA` markers in strategist.py.** Memory note `project_direction_bias_fix_status.md` references the integrated branch at SHA 3e7c767; the strategist-side surface is the regime-block symmetrisation (lines 1553-1589 + 3561-3596 + `STRAT_REGIME_BLOCK_VERSION=2` constant). The agent code presumably lives elsewhere in the tree.
15. **`detection_interval_seconds=300` in `[regime]` is unused.** RegimeWorker is sweet-spot-scheduled, not interval-scheduled.
16. **`coin_regime_history` schema loses `volume_ratio` and `atr_percentile` on restart restore.**

---

## 10. Where The Pipeline Could Have Stopped The Losing Trades — But Didn't

Detail across all Phase 3 reports + Phase 5.

The losing-trade signatures (Phase 3 verified):

1. `trending_up + Buy` — 17 trades, 65% loss, −$77.
2. `ranging + Buy` — 39 trades, 56% loss, −$59.
3. `bullish_structural_break` (any regime) — 14 trades, 71% loss, −$200.
4. 5+ supporting strategies — 105 trades combined, 56-59% loss, −$88 net.
5. Momentum/trend/breakout strategies in support — 8 strategies, all net-negative.

For EACH signature, the code path that could have stopped the trade:

| Signature | Possible code stopper | Why it didn't fire |
|---|---|---|
| `trending_up + Buy` losing | Scanner exclusion-mode `_regime_aligns` | Production mode is `briefing`, not `exclusion`. Long-in-trending_up is regime-aligned anyway, so the gate would NOT have rejected even if active. |
| `ranging + Buy` losing | Scanner exclusion-mode `_regime_aligns` | Production mode is briefing. Long-in-ranging is regime-aligned. Same — gate would have passed. |
| `bullish_structural_break` losing | Setup × regime gate in strategist | Does not exist in code. |
| 5+ supporters herding loss | Ensemble cap on size at high consensus | `single_strategy_max_share=1.0` is the cap, but it's PER-strategy not PER-aggregate. No aggregate cap or inversion exists. |
| Momentum strategy net-neg | Regime-conditional weighting | `registry.get_active_for_regime` is a no-op. Per-strategy weights are uniform 1.0. The Optimizer mutates weights but resets on restart. |

The pipeline as currently constructed has NO STOP MECHANISM for any of these signatures. Each is either:
- Mitigated only by Claude's prompt-time reasoning (advisory regime tags, advisory direction rules).
- Possible to stop via a setting that defaults to OFF (exclusion mode) or via a setting that doesn't compose correctly (`single_strategy_max_share`).
- Not modelled in code at all (setup × regime).

---

## 11. Open Questions A Fix Would Need To Answer

These are NOT proposed fixes. They are the open questions the operator will face when the next task (the fix) begins. Each is grounded in a specific verified data point.

1. **Is the BUY-skew in `buy_threshold=0.18` something to undo, or something to compensate for?** The threshold was lowered to match observed BUY-leaning direction_scores. If the underlying inputs are genuinely BUY-skewed (e.g., contrarian F&G normalisation in fear-zone markets), reverting the threshold would just produce fewer BUY labels without changing the underlying signal. If the BUY-skew is an artifact of a normalisation bug, reverting the threshold fixes nothing; the normaliser does.

2. **Should the ensemble's `final_size_mult` be wired into the actual size selection?** Today it is computed and discarded. The herding-vs-outcome inverse correlation (Claim 3 verified) suggests the system's belief that "STRONG = bigger" is wrong, but Claude is the one enforcing that belief in the absence of `final_size_mult` consumption. Wiring `final_size_mult` into the code path would let the fix invert or cap the relationship — but it would also REMOVE the herding amplifier, which is currently producing some big winners.

3. **Should `registry.get_active_for_regime` actually filter by regime?** Today it doesn't. Fixing it to honor its argument would activate `REGIME_ACTIVE_CATEGORIES` (regime_types.py:18-38). The data (Phase 3 Claim 5) shows momentum/trend strategies net-negative — they'd be deactivated in non-momentum regimes. But strategy weights would still be uniform 1.0 within each regime cohort; this fix alone wouldn't address the "all momentum strategies net-negative" pattern.

4. **Should `ensemble_weight` be persisted to DB?** Today it's in-memory and resets on restart. Persisting it would let the Optimizer build cumulative weight differentiation over time. But the optimizer would need calibration against per-strategy outcomes — and per-strategy outcomes are only reconstructable from `STRAT_VOTE_TRACE` logs (which fire only on STRONG consensus). The data-collection gap precedes the persistence gap.

5. **Should `bullish_structural_break` setup detection be tightened, or its acceptance gated downstream?** The setup_type classifier is a top-down decision tree (structure_engine.py:1061-1362). The criterion for `bullish_structural_break` is `last_bos.direction=="bullish" AND direction=="long" AND (not require_retest OR bos.significance=="major")`. The data shows this fires when a breakout has already played out. Tightening would require either (a) adding a momentum-confirmation gate (e.g. price must still be near breakout zone), or (b) refusing the setup in volatile regime explicitly. Either is a code-level fix the next task will design.

6. **Should the strategist's `Per-trade size limit` be regime-conditioned?** Today it's a single number from `tiered_capital.get_limits`. Conditioning on regime would let the fix say "size down on Buy in trending_up" by passing a smaller ceiling. But this couples a sizing decision into a tiered-capital module that has its own logic.

7. **Should `apex_min_flip_confidence_buy_to_sell=0.95` (the Buy-protecting asymmetry) be removed?** It is the strongest code-level Buy bias in the pipeline. Removing it would let APEX flip Buy→Sell at the same confidence threshold as Sell→Buy. But the operator's intent (per the comment) was to protect Buy trades from APEX over-flipping. The data does not unambiguously support either keeping or removing it; deeper APEX-flip outcome data is needed.

8. **Is the `signals` DB persistence gap fixable without affecting any consumer?** Recent rows are missing `strong_buy/sell/strong_sell` labels. Restoring full persistence is a bugfix that depends on root-cause analysis (out of scope for this task). But if downstream consumers rely on the gap (which they don't appear to per Phase 1), fixing it is safe.

9. **What does the operator want regarding the dead labels?** `OB_MITIGATED_FVG_ONLY_*`, the bearish arm of `BREAKOUT_PENDING`, and the position_in_range/range_compression/volume_ratio gates on multiple labels are dormant. Reviving them by wiring the scanner-side inputs would change the label distribution. Alternatively, the unused parameters could be removed.

10. **Should the strategist's CALL_A prompt include a hard refusal section?** Today the prompt advises Claude on regime alignment but does not say "you may not return a Buy for X coin." A hard refusal would close the gap between regime advisory text and execution.

---

## 12. Summary

The verified entry-pipeline picture as of 2026-05-21:

- **Most losses correlate with specific entry signatures** (`trending_up + Buy`, `ranging + Buy`, `bullish_structural_break × volatile`, 5+ strategies agreeing).
- **The pipeline is structurally regime-blind** at the classification layers. Regime is detected and tagged but rarely enforced in code.
- **The ensemble's sizing recommendation is discarded.** Claude reads the consensus label and sizes accordingly — the amplification of size on STRONG consensus happens in Claude's reasoning, not in code.
- **The momentum/trend strategy family is net-negative** in the analysis window. The ensemble weights them uniformly with contrarian/event-driven strategies that are net-positive. Per-strategy weight differentiation is ephemeral (in-memory, resets on restart).
- **Asymmetric BUY-side calibration is deliberate and persistent**: `buy_threshold=0.18` in the SIG classifier and `apex_min_flip_confidence_buy_to_sell=0.95` in APEX. Both bias the system toward keeping Buy trades.
- **One label-naming bug, three dormant labels, and one normalisation discrepancy** were caught in the labeler/classifier code during the investigation.
- **Data persistence has multiple gaps**: empty `ensemble_votes` table, empty `brain_decisions` table, recent SIG_CLASSIFY persistence missing 60% of events, zero entry_score, dead `cycle_metrics` columns.

The investigation produced **9 reports totalling 7 phase files plus this synthesis**, all under `dev_notes/entry_signal_investigation/`. Every claim is backed by either file:line, SQL query result, or log query result.

Nothing in the system has been changed. The next task — the fix — starts from this map.

---

## Companion files

- `phase0_preflight.md` — environment, tables, log coverage.
- `phase1_pipeline_code_map.md` — 17,463 lines of source mapped end-to-end.
- `phase2_config_map.md` — every value, hardcoded vs tunable, regime-conditional or blind.
- `phase3_claim1_regime_side.md` — Buy/Sell × regime DB verification.
- `phase3_claim2_setup_regime.md` — setup × regime DB+log verification.
- `phase3_claim3_herding.md` — supporting-strategy bucket DB+log verification.
- `phase3_claim4_label_calibration.md` — SIG_CLASSIFY label outcome verification.
- `phase3_claim5_strategy_attribution.md` — per-strategy DB+log verification.
- `phase3_verification_summary.md` — synthesis of the five claims.
- `phase4_ensemble_logic.md` — ensemble math in detail.
- `phase5_regime_usage.md` — regime consumed/blind in 32 sites.
- `phase6_signal_classifier.md` — SIG_CLASSIFY classifier in detail.
- `phase7_sizing_logic.md` — sizing path from ensemble to gate.
- `MASTER_SITUATION_REPORT.md` — this file.
