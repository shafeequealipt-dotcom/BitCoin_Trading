# Phase 5 — Regime Usage Map

Where the per-coin regime is computed, where it IS consumed in the entry decision, and where it is AVAILABLE but NOT used.

## Producer

`RegimeDetector` in `src/strategies/regime.py:78-223` produces:
- `_last_regime` — global (BTCUSDT default).
- `_per_coin_regimes` dict — populated by `RegimeWorker.detect_per_coin()` (regime_worker.py:186) into the detector's instance.
- `_confirmed_regimes[symbol]` — hysteresis state (2-of-2 confirmation default).

Producer runs every 5 minutes at sweet-spot 1:15 (regime_worker.py:48). Note: this is AFTER `structure_worker` (0:45) and `signal_worker` (1:00), so setup_type and SIG_CLASSIFY are computed before regime is refreshed for the cycle.

The regime decision tree (regime.py:90-156) — already mapped in Phase 1.

## Consumers — Where Regime IS Used

| # | Site | File:line | Effect |
|---|---|---|---|
| 1 | `ScannerWorker._get_regime_alignment` | scanner_worker.py:185-211 | maps regime → {+1, +0.5, 0, -1} as 1 of 6 components in opportunity score (weight 0.13) |
| 2 | `ScannerWorker._regime_aligns` | scanner_worker.py:378-392 | HARD GATE in exclusion mode (rejects long if regime ∉ {trending_up, ranging}, etc.) |
| 3 | Scanner package metadata | scanner_worker.py:744-755 | regime tag written to `pkg.price_data.regime` for the strategist |
| 4 | `state_labeler.py` regime soft-haircut | state_labeler.py:291-295, :311-315, :338-342, :357-361, :422-426, :441-445, :557-561, :576-580 | multiplies trigger confidence by `regime_haircut` (default 0.5) when regime doesn't match label expectation. 8 of 22 labels apply this. |
| 5 | `state_labeler.py` BREAKOUT_PENDING positive condition | state_labeler.py:370-371 | range_compression + regime ∈ {ranging, dead} required (rare — input not populated; see Phase 1 dead-label finding) |
| 6 | `Strategist user prompt` regime tag | strategist.py:3363-3389 | per-coin `[TRENDING_UP 75%]` tag appended to market-data line |
| 7 | `Strategist REGIME DIVERGENCE` block | strategist.py:3402-3420 | per-coin vs global divergence listed in prompt — Claude advisory |
| 8 | `Strategist MARKET REGIME (CONTEXT)` block | strategist.py:3570-3596 | global regime + soft-bias NOTE on trending_down/trending_up at confidence>0.60 |
| 9 | `APEX assembler` symbol-history filter | assembler.py:79, :434 | TIAS history filtered by regime; falls back to all-regime + warning text at `:440-451` |
| 10 | `APEX assembler` situation block | assembler.py:82, :551, :554 | TIAS situation stats keyed by regime |
| 11 | `APEX optimizer` Tier 2 fallback | optimizer.py:227-234 | regime name embedded in pattern_summary text |
| 12 | `APEX optimizer` lock signal | optimizer.py:1413-1417 | regime_signal: +1 if regime supports Claude's direction (trending_up + Buy or trending_down + Sell), -1 if opposed, 0 otherwise. Contributes via weight `apex_lock_regime_weight=1.0` to composite score (threshold default 0.0) |
| 13 | `APEX optimizer` RR-boost scope | optimizer.py:469-470 | RR-weighted confidence boost ONLY applied when regime NOT in (trending_up, trending_down, volatile) |
| 14 | `APEX optimizer` flip-confidence gate scope | optimizer.py:1691 | confidence gate ONLY fires when regime NOT in (trending_up, trending_down, volatile) |
| 15 | `APEX gate` conviction-weight cache key | gate.py:511-538 (cache key at :542) | regime used in cache key only — does not drive any decision |
| 16 | `APEX prompt` regime rules | prompts.py:41-47 | LLM-instructional text; non-binding on code |

## Where Regime Is Available But NOT Used

| # | Site | File:line | Note |
|---|---|---|---|
| 1 | `structure_engine.classify_setup` | structure_engine.py:1061-1362 | Setup_type tree never reads regime. Uses `market_structure.structure` (uptrend/downtrend/ranging) which is structural, not regime. |
| 2 | `signal_generator._evaluate_signal` | signal_generator.py:393-545 | Zero regime references. SIG_CLASSIFY is regime-blind. |
| 3 | `confidence.calculate` | confidence.py:19-71 | Zero regime references. Confidence is regime-blind. |
| 4 | `strategy_worker` strategy selection | strategy_worker.py:573 | Calls `registry.get_active_for_regime(regime)` but registry.py:44-53 IGNORES regime argument. |
| 5 | `ensemble.vote` strategy selection | ensemble.py:171 | Same — `get_active_for_regime` is a no-op. |
| 6 | `ensemble` consensus thresholds | ensemble.py:261-275 | Same bars in every regime |
| 7 | `ensemble` final_size_mult | ensemble.py:275-293 | Same CONSENSUS_SIZE map in every regime |
| 8 | Per-strategy `ensemble_weight` | registry.py / signal_types.py:195 | Uniform 1.0 across all regimes; Optimizer mutates same value across regimes |
| 9 | Strategist CALL_A direction-decision | strategist.py | No code-side refusal of any direction × regime combination. Only LLM-instructional text. |
| 10 | Strategist CALL_A conviction/size | strategist.py:106 | Claude chooses; system prompt does not regime-condition the size choice |
| 11 | APEX gate Check 0-14 | gate.py:71-470 | None of the 14 gate checks vary by regime |
| 12 | APEX gate `zero_conviction` reject | gate.py:161-182 | Uses uniform mins — not regime-conditional |
| 13 | APEX gate `reentry_cooldown` | gate.py:292-319 | 5-min cooldown applies in every regime |
| 14 | TRADE_SYSTEM_PROMPT direction rules | strategist.py:82-88 | Text rules; non-binding on code |
| 15 | TRADE_SYSTEM_PROMPT F&G contrarian | strategist.py:90-97 | Same |
| 16 | Sentiment / fear-greed normalisation | signal_generator.py | Same scaling formula in every regime |

## The Three Regime-Conditional Decision Points That MATTER

Among the 16 "regime is used" sites, only 3 have a HARD effect on the trade outcome:

### Point 1: Scanner exclusion-mode `_regime_aligns` HARD GATE

Code: `scanner_worker.py:378-392`. Long aligns with `{trending_up, ranging}`. Short aligns with `{trending_down, ranging}`. Mismatch → rejected from the qualified list.

**BUT**: this gate fires only in `[scanner] mode = "exclusion"`. Production default is `mode = "briefing"` (config.toml:763). In briefing mode the gate does not run. **In production, the only place a Buy in `volatile + counter` regime would be rejected is the briefing-mode interestingness floor, which is a soft score threshold, not a regime check.**

### Point 2: APEX optimizer lock signal (regime contribution)

Code: `optimizer.py:1413-1417`. regime_signal: +1 if Claude's direction matches the trending regime; -1 if opposed; 0 for ranging/volatile/dead/unknown. Contributes to a composite score with 4 other signals (structural, trade_dir, wr, symbol_evidence), all weighted equally at 1.0. The composite must be below `apex_lock_score_threshold=0.0` to LOCK.

For a Buy in trending_down regime: regime_signal = −1. The composite is biased toward locking. But it locks only if the other 4 signals don't dominate it. Net effect: regime can BLOCK a flip but cannot prevent the original entry.

### Point 3: State labeler soft haircut

8 labels apply `regime_haircut = 0.5` to confidence when regime doesn't match expectation. This LOWERS the label's confidence by 50%. The label still fires (no hard kill). The lowered confidence affects:
- Briefing-mode ranking via `interestingness_score`.
- The `state_label.primary` selection (lower confidence ⇒ less likely to be primary).

Effect on trade outcome: indirect. A label whose confidence was halved is less likely to be the briefing primary, which makes it less likely to be among the top-N CoinPackages sent to the strategist. But once sent, the strategist's decision is unaffected by the haircut.

## The Regime-Blind Decision Sequence

Walking the live entry path from raw signals to executed trade, the explicit regime-conditional STEPS are:

1. (1B) Setup_type computed — **regime-blind**
2. (1B) SIG_CLASSIFY computed — **regime-blind**
3. (1B) Regime detected — produces the value (not a consumer here)
4. (1C) Strategy votes collected — **regime-blind** (registry no-ops the regime filter)
5. (1C) Ensemble consensus computed — **regime-blind**
6. (1C) `final_size_mult` computed — **regime-blind** (not consumed downstream anyway)
7. (1D) Scanner opportunity score — **regime-aware** (weight 0.13 of 1.00)
8. (1D) Scanner briefing/exclusion ranking — **regime-aware** in exclusion mode; soft in briefing mode (production default)
9. (1D) State labeler — **regime soft-haircut** on 8 of 22 labels
10. (2) Strategist prompt — **regime surfaced to Claude as text** (advisory)
11. (2) Strategist CALL_A direction-decision — **regime-blind in code**; Claude decides
12. (2) Strategist size — **regime-blind in code**; Claude decides under per-trade ceiling
13. (3) APEX optimizer Tier selection — **regime-aware** in Tier 2
14. (3) APEX optimizer lock — **regime-aware via composite signal** (1 of 5 weighted)
15. (3) APEX optimizer RR-boost scope — **regime-conditional**
16. (3) APEX optimizer flip-confidence gate scope — **regime-conditional**
17. (3) APEX gate Check 0-14 — **regime-blind**
18. (3) APEX gate `zero_conviction` reject — **regime-blind**
19. (3) APEX gate `reentry_cooldown` — **regime-blind**
20. (4) Trade execution — **regime-blind**

Of 20 sequential decision points, **only 6 are regime-conditional in any way**. Of those 6, only 2 (scanner exclusion gate and state-labeler haircut) are firmly in the path that affects whether a trade happens. The remaining 4 are inside APEX, which is a post-strategist optimization stage — they affect modifications, not entry initiation.

## What This Means

The entry pipeline is **structurally regime-blind**. The producer (regime_worker) emits regime, several consumers READ regime as a metadata tag, but very few consumers CONDITION their decision on regime in a code-enforced way. The system mostly delegates "is this trade good for this regime?" to Claude's prompt-time reasoning.

The Phase 3 verification showed that direction × regime is one of the strongest correlations in the loss data (`trending_up + Buy` is −$77 net; `ranging + Buy` is −$59 net). But the only place this could have been STOPPED by code is:
- Scanner exclusion-mode gate — disabled in production (briefing mode default).
- APEX lock signal — only blocks flips, not entries.

Everywhere else, regime is data, not policy.
