# PRIMARY Issue — Phase 2: Structural Sell-Bias Investigation Report

Date: 2026-05-11
Investigation duration: Phase 0 + Phase 1 (all 10 sub-steps)
Decision required: strategic policy on direction-flip behavior
This report supersedes the spec's reference figures with currently-verified data.

# Section 1 — The Strategic Question

## 1.1 What is happening

Over the bybit_demo lifetime (2026-05-09 through today, 295 trades, 2.3 days), the system has placed **268 Sell trades and 27 Buy trades** — a 90.8% Sell skew. This pattern is constant across all three days. On the brain (Claude) decision level, the split is closer to 60% Sell / 40% Buy. Between brain and final order, two flip mechanisms (APEX-driven via DeepSeek, and XRAY-driven via structural placement) systematically reroute most Buy intents into Sell trades.

## 1.2 What the operator must decide

The flip mechanisms are operating as coded. The strategic question is **whether the resulting Sell-bias is intentional policy or an emergent side effect**, and what behaviour the operator wants going forward. This is not a bug fix — the code does what it was written to do. It is a policy decision about whether the resulting behavior matches the operator's intent.

## 1.3 What this report provides

The data needed to make that decision: historical performance of flipped vs unflipped trades, per-regime breakdown, DeepSeek's post-hoc verdict on its own decisions, and a menu of fix options. The operator chooses; the investigation does not pre-decide.

# Section 2 — How The Flip Mechanism Works

## 2.1 Two independent flip paths

The system has **two flip mechanisms** that can change brain's direction:

### Path A: APEX-driven (via DeepSeek)

APEX optimizes every trade. It builds an intelligence package, sends it with the trader's directive to DeepSeek (`deepseek-v3.2`), and applies the JSON response. DeepSeek can flip direction; APEX has three gates that police whether the flip stands:

1. **Pre-call lock** at `src/apex/optimizer.py:885-931` — for trending and volatile regimes, APEX locks the direction before even calling DeepSeek. For ranging and dead regimes, no pre-call lock.
2. **Confidence gate** at `src/apex/optimizer.py:933-977` — post-parse. The flip is reverted if DeepSeek's reported confidence is below `apex_min_flip_confidence` (currently 0.70).
3. **Resize policy** at `src/apex/optimizer.py:979-1032` — if a flip stands and DeepSeek tried to size up, cap to original; if sized down, accept.

### Path B: XRAY-driven (deterministic code, no LLM)

After APEX returns, strategy_worker runs an XRAY check at `src/workers/strategy_worker.py:1604-1779`. XRAY computes a ratio:
- For Buy direction: `_ratio = rr_short / rr_long`
- For Sell direction: `_ratio = rr_long / rr_short`

If `_ratio > xray_dir_flip_threshold_ratio` (currently 3.0), XRAY flips direction to the opposite side and uses the structural placement for the new direction's SL/TP. Since Issue 1 fix (2026-05-11, the current branch), XRAY respects APEX_DIR_LOCK and emits `XRAY_FLIP_SUPPRESSED_BY_LOCK` instead of overriding.

## 2.2 The cascade

The two paths combine: APEX runs first, then XRAY. APEX may flip Buy→Sell or Sell→Buy. XRAY then runs on the APEX-output direction and may flip again. The final direction is logged in `DIRECTION_DECISION` at `src/workers/strategy_worker.py:2254`.

Today's path counts (9 hours of logs):

- APEX flips: 23 total. Of these, 16 Buy→Sell and 7 Sell→Buy. APEX is symmetric.
- XRAY flips: 19 total. Of these, 18 Buy→Sell and 1 Sell→Buy. XRAY is asymmetric.
- Final DIRECTION_DECISION outcomes: 62 Sell, 3 Buy (95.4% Sell).

The combined cascade produces the Sell-bias even though APEX alone is balanced. The 7 APEX Sell→Buy flips are reversed by XRAY's structural check on most of them.

## 2.3 Why XRAY is asymmetric today

XRAY's flip code is symmetric, but its inputs are asymmetric. Of today's 19 XRAY flips, 18 had `rr_long` between 0.0 and 0.2 (essentially zero) while `rr_short` ranged 2.9 to 8.2. This happens when current price is near resistance levels — long setups have a tiny take-profit distance and short setups have a large one. The market state for the coins the scanner triggered on today has prices systematically near resistance.

## 2.4 Why APEX flips at all in ranging

APEX's confidence gate requires `effective_confidence >= 0.70`. Today's APEX_FLIP events show DeepSeek's confidence quantizing at 0.65 (blocked) and 0.85 (passes). The threshold filters out about 18% of flip attempts (5 blocked vs 23 passing today).

The intended safety valve — an RR-weighted confidence boost at `src/apex/optimizer.py:343-387` that would lower the threshold when X-RAY structure favors the flipped direction — has a one-character typo. Line 367 reads `getattr(package, "structure_data", None)`; the actual attribute is `structural_data` (with the `al`). The boost has never engaged in production. All today's `APEX_FLIP_BLOCKED` events show `rr_boost=0.00`.

The typo means the gate is currently MORE conservative than designed. Fixing it in isolation would increase the flip-pass rate by allowing some sub-0.70-raw flips through.

## 2.5 The feedback loop

DeepSeek receives a per-coin direction breakdown in the prompt (Section 3 of the user prompt — `src/apex/prompts.py:139-141`). Observed behavior across 5 sample reasoning fields: when one direction has substantially more trades than the other for that coin, DeepSeek prefers that direction regardless of win rate. Sample text from `apex_reasoning` column: "TIAS history shows 6 Sell trades (1W/5L) vs 0 Buy trades in ranging regime. Although Sell WR is low (17%), Buy has zero data, making a flip to Buy unjustified (<5 trades)" — and the trade was flipped Buy→Sell.

The system prompt says "If fewer than 5 trades exist for a direction in the current regime, that is NOT enough to justify a flip. Keep the trader's original direction." DeepSeek interprets this differently — it treats the low-data direction as untrustworthy and defaults to the high-data direction. This creates a self-reinforcing Sell-bias: every Sell trade increases the historical Sell count for that coin; next time, the bias is stronger.

## 2.6 The contradiction the operator should notice

The global TIAS data — the same data the system's own analysis uses post-hoc — says **Buy outperforms Sell in ranging regime** (45.9% WR vs 33.0% WR, verified live across both modes). DeepSeek receives this evidence in Section 4 of every prompt. It also has access to a `direction_bias` field that is computed and equals `"buy"` in ranging.

DeepSeek ignores this global evidence and follows the per-coin (Section 3) data instead. The strategic question is partly whether the system should rely on DeepSeek to weigh these correctly, or whether code-level gates should enforce the policy more firmly.

# Section 3 — The Data

## 3.1 Direction distribution (bybit_demo, 2.3 days, 295 trades)

| Day | Buy | Sell | Sell % |
|-----|----:|----:|-------:|
| 2026-05-09 | 12 | 104 | 89.7% |
| 2026-05-10 |  8 |  75 | 90.4% |
| 2026-05-11 |  7 |  89 | 92.7% |

The Sell-bias is steady-state, not a transient.

## 3.2 Per-symbol direction (top 11 by volume)

7 of the top 11 symbols have **zero** Buy trades on bybit_demo. Three more have exactly 1 Buy each. The bias is system-wide, not symbol-specific.

## 3.3 Performance by direction (bybit_demo)

From `trade_log` (the canonical record):

| Direction | Trades | WR | Net pnl_usd |
|-----------|-------:|---:|------------:|
| Buy  |  27 | 29.6% | $-4.69 |
| Sell | 268 | 27.2% | $-85.12 |
| **All** | **295** | **27.5%** | **$-89.81** |

The system is currently net losing at sub-30% win rate.

Caveat: 117 of 295 closed bybit_demo trades have pnl_usd = 0 (40% measurement gap; PnL is recorded but came back zero). This is most likely a downstream gap in how the close path captures fill prices. It affects the dollar aggregates but not the win/loss counts (those are based on the same data the gap obscures). Worth flagging as a separate follow-up.

## 3.4 Direction × regime (bybit_demo, from `trade_intelligence`)

| Direction | Regime | Trades | WR | Net pnl_usd |
|-----------|--------|-------:|----|------------:|
| Buy  | ranging      |  14 | **50.0%** | $+42.94 |
| Buy  | trending_up  |  22 | 36.4% | $-19.34 |
| Sell | ranging      | 148 | 27.7% | **$-322.27** |
| Sell | trending_up  | 139 | 33.8% | $+502.61 |
| Sell | trending_down |  5 | 20.0% | $+0.69 |
| Sell | volatile     |   7 | 28.6% | $-1.58 |

Buy × ranging has the highest WR of any cohort (50.0% on 14 trades). Sell × ranging is the largest losing cohort ($-322 net).

The Sell × trending_up cohort being net positive ($+502.61) is counter-intuitive. The hypothesis: per-coin regime can differ from BTC's regime, and the "trending_up" classification here may reflect BTC's overall state while individual coins were consolidating or pulling back.

## 3.5 Flipped vs unflipped performance (THE central statistic)

From `trade_intelligence.apex_flipped` over 30 days:

### Bybit_demo cohort

| apex_flipped | Trades | WR | Net pnl_usd |
|--------------|-------:|---:|------------:|
| 0 (unflipped) | 124 | 37.1% | $-8.36 |
| 1 (flipped)   | 211 | 28.4% | $+211.40 |

### Shadow cohort (longer history, larger sample)

| apex_flipped | Trades | WR | Net pnl_usd |
|--------------|-------:|---:|------------:|
| 0 (unflipped) | 661 | 43.1% | **$+167.02** |
| 1 (flipped)   | 257 | 36.2% | **$-166.96** |

### Direction-pair detail

The cleanest read uses the original-vs-final direction pair:

| Mode | Original → Final | Trades | WR | Net pnl_usd |
|------|------------------|-------:|---:|------------:|
| shadow | Buy → Buy (unflipped)  | 402 | **47.8%** | **$+252.23** |
| shadow | Buy → Sell (flipped)   | 189 | 31.7% | **$-238.11** |
| shadow | Sell → Buy (flipped)   |  76 | 51.3% | $+74.13 |
| shadow | Sell → Sell (unflipped) | 259 | 40.9% | $+143.12 |
| bybit_demo | Buy → Buy   |  32 | 37.5% | $+8.71 |
| bybit_demo | Buy → Sell  | 176 | 30.7% | $+223.56 |
| bybit_demo | Sell → Buy  |   3 | 66.7% | $+5.93 |
| bybit_demo | Sell → Sell | 113 | 29.2% | $-46.83 |

### What the data says

- **In the larger shadow sample, Buy → Sell flips destroy win rate by 16.1 percentage points and swing PnL by approximately $-490 per cohort.**
- Sell → Buy flips help in shadow (+10.4 pp WR, +$74 net) and may help in bybit_demo (small sample).
- In bybit_demo, Buy → Sell flips appear net positive in dollar terms ($+223), but win rate is still 6.8 pp worse than the unflipped cohort. The dollar positivity is partly an artifact of `_apply_flip_resize_policy` capping flipped trade sizes down (avg $259 flipped vs $312 unflipped), making losses smaller per trade.

## 3.6 DeepSeek post-hoc verdict on its own decisions

The `ds_optimal_direction` column in `trade_intelligence` is DeepSeek's own assessment, run post-close, of whether the system's direction was correct. For 335 bybit_demo trades:

| ds_optimal_direction | Trades | Actual WR |
|----------------------|-------:|----------:|
| YES (system direction was right) | 133 | 73.7% |
| NO (system direction was wrong)  | 190 |  3.7% |
| UNCLEAR                          |  12 |  8.3% |

DeepSeek says the system's chosen direction was wrong on **56.7%** of trades. The correlation between this verdict and actual win rate is extreme (74% vs 4%). The verdict is highly reliable as a post-hoc signal.

## 3.7 Confidence calibration

DeepSeek's self-reported confidence on flipped bybit_demo trades averages 0.83. The actual win rate of these trades is 28.4%. The confidence is overconfident — a 0.70 threshold does not effectively discriminate winning flips from losing flips. Today's flips bunch at confidence 0.85; today's blocks bunch at confidence 0.65.

# Section 4 — The Options

Each option preserves the aggressive opportunity exploitation philosophy. None propose disabling APEX or kneecapping trade frequency without operator approval.

## Option 1 — Brain-priority gating

When brain conviction (signal score) is above a threshold X, APEX cannot flip the direction. Below threshold, APEX may flip with logging. Threshold operator-configurable.

Mechanism: add a pre-check in `optimizer.optimize` that reads `directive["signal_score"]` and short-circuits the flip permission when score exceeds X.

Expected effect: high-conviction Buys reach final_dir = Buy. Low-conviction Buys may still be flipped. Sell-bias relaxes for the high-conviction half of brain decisions.

Touches: `src/apex/optimizer.py` only.

Trade-off: relies on brain's signal-score calibration. If brain's scores are noisy, the threshold needs validation.

## Option 2 — Regime-specific policy tuning

Tighten the flip rule in ranging specifically. Examples:

- Require Section 4 (global) AND Section 3 (per-coin) to both agree on flip direction before flip permission.
- Code-enforce the "<5 trades = no flip" rule that DeepSeek mis-reads.
- Block any flip when the per-coin Buy count is below 5 in the current regime (the EGLDUSDT-style cases).

Mechanism: add a `_check_ranging_flip_legitimate` gate after parse, before applying flip.

Expected effect: the feedback-loop flips would stop. Per-coin Sell counts would stop growing without legitimate evidence. Other regime behaviour unchanged.

Touches: `src/apex/optimizer.py`.

Trade-off: requires code-level interpretation of the rules the prompt currently delegates to DeepSeek. More robust, but adds a small layer of logic.

## Option 3 — Confidence-weighted flip (raise threshold)

Raise `apex_min_flip_confidence` from 0.70 to a higher value (operator-tunable, e.g. 0.90). Optionally require XRAY ratio confirmation (only flip if XRAY ratio agrees AND confidence ≥ X).

Mechanism: change config value. Optionally add a `require_xray_concordance` setting.

Expected effect: today's 23 flips at confidence 0.70-0.95 reduce to roughly 10-15. Per-trade quality may improve. Trade frequency drops modestly.

Touches: `config.toml` (one value); optionally `optimizer.py` if XRAY-concordance check added.

Trade-off: simplest fix. Most conservative. Operator may need to recalibrate threshold over a few days.

## Option 4 — Asymmetric flip thresholds

The data shows Buy → Sell flips harm, Sell → Buy flips help. Encode this asymmetry: e.g. Buy → Sell needs confidence ≥ 0.95, Sell → Buy needs ≥ 0.70.

Mechanism: change `_enforce_flip_confidence` to use different thresholds based on `(claude_direction, qwen_direction)`.

Expected effect: most Buy → Sell flips today (confidence 0.70-0.85) blocked. Sell → Buy flips survive. Direction distribution shifts substantially toward final_dir = Buy.

Touches: `src/apex/optimizer.py` and config additions.

Trade-off: encodes a current-data asymmetry into the policy. If market regime shifts (e.g. coins start hovering near support instead of resistance), the asymmetry could invert and the threshold structure may need re-tuning.

## Option 5 — Brain authority restoration

APEX optimizes size, SL, TP only. APEX cannot flip direction. XRAY can flip only with an extreme ratio (e.g. raise from 3.0× to 10×).

Mechanism: in `optimizer._parse_response`, force `qwen_dir = original_dir` (drop DeepSeek's direction field). In config, raise XRAY threshold.

Expected effect: APEX-driven flips → 0. XRAY-driven flips → rare (only when structure overwhelmingly indicates). Final direction much closer to brain's choice.

Touches: `src/apex/optimizer.py`, `config.toml`.

Trade-off: most aggressive fix. Disables a designed feature. May regress legitimate flips where DeepSeek's reasoning catches a missed pattern. Highest blast radius if brain is unreliable.

## Option 6 — Hybrid

Combine multiple options. Plausible combinations:

- **(1 + 3)**: Brain priority for high-conviction trades AND raised confidence threshold for everyone else.
- **(2 + 3)**: Code-enforce <5-trade rule AND raise confidence threshold.
- **(4 + 5)**: Asymmetric thresholds AND tighten XRAY ratio.

Expected effect: multiple layers of safeguarding. Most robust to noise.

Trade-off: more variables to tune. Higher complexity.

## Option 7 — Status quo + better logging

Add a comprehensive `APEX_FLIP_DECISION` structured log at every direction-modification point so the operator can measure ongoing flip-quality more easily, without changing the flip behavior.

The spec calls for this option in case the historical data showed flips were profitable. **The data does NOT support this.** Documented for completeness; it is the do-nothing option.

## Side note: the typo bug

Independent of any chosen option, the `package.structure_data` typo at `src/apex/optimizer.py:367` should be fixed. But fixing it in isolation INCREASES flip-through-rate (the boost lowers the gate by 0.15). It should be addressed jointly with whichever threshold change the operator picks.

# Section 5 — Recommendation (deferred to operator)

This recommendation is provided as informational input. The decision belongs to the operator per spec Part B Rule 2.

The investigator suggests considering **Option 4 (asymmetric thresholds) combined with the typo fix and a code-level enforcement of the "<5 trades" rule (the relevant part of Option 2)**:

- **Asymmetric thresholds** reflect the strongest signal in the data: Buy → Sell flips destroy WR by 6.8-16.1 percentage points across both modes; Sell → Buy flips help.
- **Code enforcement of "<5 trades"** addresses the root cause identified in P.1.9: DeepSeek misreads the prompt rule. A code-level gate removes the ambiguity.
- **Typo fix** restores the RR-boost as designed. Paired with raised thresholds, the net effect is still more conservative than current state.

Secondary preference, if the operator wants a simpler single-knob change: **Option 3 (raise threshold)** to 0.90. This is a one-line config change. It is less precise than Option 4 but materially reduces flip volume without code touches.

The investigator does NOT recommend Option 5 (full brain authority restoration). It is too aggressive — DeepSeek does occasionally catch legitimate flips (the Sell → Buy 51.3% WR cohort in shadow). Removing the mechanism entirely sacrifices that signal.

The investigator does NOT recommend Option 7. The data shows flips are not profitable as a class.

# Section 6 — What I Need From The Operator

To proceed to Phase 3 (implementation), please answer:

1. **Which option do you choose?** (1, 2, 3, 4, 5, 6, or 7. Or a custom variant you define.)
2. **If Option 3 or 4, what threshold values?** (default suggestion: Option 3 raise to 0.90; Option 4 use 0.95 for Buy→Sell, 0.70 for Sell→Buy.)
3. **Should the typo bug be fixed in this fix series?** (recommended yes, paired with whichever threshold you pick.)
4. **Any additional constraints for Phase 3?** (e.g. preserve specific behaviour, additional logging, etc.)

Once decided, I proceed to Phase 3 implementation on branch `fix/sell-bias-fixes-2026-05-11` with per-issue commit prefix `p/`. Phase 3 includes unit tests, integration tests, comprehensive structured logging (`APEX_FLIP_DECISION` tag), and shadow-mode verification. Phase 4 (24-48 h live verification) follows deploy.

# Appendix A — Reading Order For Phase 1 Deliverables

- `phase0_baseline.md` — baseline metrics for all 5 issues
- `p_phase1_apex_optimizer_anatomy.md` — APEX optimize() flow, gates, RR-boost dead code
- `p_phase1_apex_gate_assembler.md` — gate does not mutate direction; typo bug in detail
- `p_phase1_qwen_client.md` — full prompts pasted; TIAS Section 4 says BUY but DeepSeek ignores
- `p_phase1_strategy_worker.md` — final_dir resolution; 5-code reason taxonomy
- `p_phase1_xray_flip.md` — XRAY's structural asymmetry origin
- `p_phase1_regime_detector.md` — 80% ranging+dead = 80% no-pre-call-lock
- `p_phase1_historical_distribution.md` — direction distribution, per-day/symbol/strategy
- `p_phase1_flip_performance.md` — THE flipped-vs-unflipped data
- `p_phase1_deepseek_responses.md` — feedback loop + typo bug confirmed in live logs
- `p_phase1_synthesis.md` — four-question answer, options menu

Operator may read this report (`p_phase2_report.md`) alone, or follow the appendix for full investigation depth. All file paths under `dev_notes/sell_bias_fixes/`.

# Appendix B — Out-Of-Scope Confirmation

The investigation read but did not modify:

- Brain (Stage 2 prompt construction, Claude CLI subprocess)
- Transformer overall architecture
- Shadow adapter (only mirrored, not modified)
- Existing strategies (analyzed for flip-direction pattern only)
- Layer 1 scanner pipeline
- Bybit demo HTTP/auth/signing/WS-parse layer

No code was changed in Phase 0 or Phase 1. Services remain stopped per operator's instruction at plan time.

# Appendix C — Aim Preservation

Every recommended option preserves the operator's stated philosophy of aggressive opportunity exploitation. None reduce trade frequency without operator approval. None bias toward capital preservation as a default. The fixes adjust **which** direction trades go, not **how often** they happen.

If the operator chooses Option 3 (raise confidence threshold), trade frequency may dip modestly while DeepSeek recalibrates; this is the only option with a small frequency-side trade-off, and it is explicit.
