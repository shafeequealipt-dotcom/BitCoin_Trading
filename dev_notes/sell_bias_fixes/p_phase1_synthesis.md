# PRIMARY Issue — Phase 1 Step P.1.10: Synthesis

Synthesizes findings from P.1.1 through P.1.9 to answer the four required questions and prepare the operator-facing Phase 2 report.

Status: investigation complete. No code changes. Awaiting operator decision at Phase 2.

## 1. WHERE Does The Flip Happen? (file:line precision)

There are **two independent flip paths**:

### Path A — APEX-driven (via DeepSeek)

| Step | File:line | Action |
|------|-----------|--------|
| Pre-call lock check | `src/apex/optimizer.py:885-931` | Trending/volatile lock; ranging/dead unlocked. |
| Lock instruction injection | `src/apex/optimizer.py:232-236` | Reasoning prefix tells DeepSeek "DO NOT change direction" if locked. |
| Build prompt | `src/apex/prompts.py:82-226` | 5-section package. System prompt has explicit regime + flip rules. |
| Call DeepSeek | `src/apex/qwen_client.py:134-274` | OpenRouter HTTPS POST, JSON-mode forced. |
| Parse response → was_flipped | `src/apex/optimizer.py:631` | `was_flipped = (qwen_dir != original_dir)`. |
| Lock override (suspenders) | `src/apex/optimizer.py:330-341` | If locked + qwen flipped, revert (`APEX_DIR_LOCK_OVERRIDE`). |
| RR-boost computation (DEAD CODE) | `src/apex/optimizer.py:367` | `getattr(package, "structure_data", None)` — typo, returns None always. |
| Confidence gate | `src/apex/optimizer.py:933-977` | Revert if `effective_conf < 0.70`. Emits `APEX_FLIP_BLOCKED`. |
| Resize policy | `src/apex/optimizer.py:979-1032` | Cap if upsized; accept if downsized. Emits `APEX_FLIP_RESIZE_*`. |
| Log | `src/apex/optimizer.py:802` | `APEX_FLIP` (WARNING) — final flip decision. |

### Path B — XRAY-driven (deterministic code, no LLM)

| Step | File:line | Action |
|------|-----------|--------|
| Ratio computation | `src/workers/strategy_worker.py:1618-1622` | `_ratio = rr_opposite / rr_chosen`. |
| APEX_DIR_LOCK interlock | `src/workers/strategy_worker.py:1648-1660` | Suppress flip if APEX locked (Issue 1 fix 2026-05-11). |
| Dual-levels precondition | `src/workers/strategy_worker.py:1663-1691` | Skip trade entirely if both directions lack structural levels. |
| Post-flip conflict recheck | `src/workers/strategy_worker.py:1697-1722` | Skip if flipped direction creates fresh conflict on weak setup. |
| Apply flip | `src/workers/strategy_worker.py:1724-1778` | Mutate `trade["direction"]`, SL, TP, `_flip_source = "xray"`. |
| Log | `src/workers/strategy_worker.py:1769` | `XRAY_DIR_FLIP` (WARNING). |

### Final Resolution

| File:line | Action |
|-----------|--------|
| `src/workers/strategy_worker.py:2185-2266` | Build `_brain_dir`, `_flip_source`, `_was_flipped`, `_dir_reason`; emit `DIRECTION_DECISION` (INFO). |
| `src/workers/strategy_worker.py:2183` | `side_enum = Side.BUY if direction == "Buy" else Side.SELL` — the final write to the order. |

## 2. WHY Does It Flip In Ranging? — Root Causes

### Root Cause #1 — DeepSeek misreads "INSUFFICIENT DATA"

The system prompt (prompts.py:47) says: *"If fewer than 5 trades exist for a direction in the current regime, that is NOT enough to justify a flip. Keep the trader's original direction."*

DeepSeek's observed behavior (P.1.9 reasoning samples): it interprets this as "the LOW-DATA direction can't be trusted; default to the HIGH-DATA direction." On a brain-Buy signal where Section 3 (per-coin history) has 0-2 Buy trades and 6-15 Sell trades, DeepSeek flips to Sell despite both directions being below the 5-trade floor.

This produces a **self-reinforcing feedback loop**: each Sell trade increases the historical Sell count; next time the same coin signals Buy, DeepSeek sees even more Sell history and flips again.

### Root Cause #2 — Per-coin (Section 3) overrides global (Section 4)

TIAS Section 4 globally says `direction_bias = "buy"` in ranging (Buy 45.9% WR vs Sell 33.0% WR — verified live). DeepSeek **ignores** the global evidence and weighs per-coin Section 3 (which is skewed by the feedback loop).

The system prompt's regime instruction for ranging is "use DIRECTION BREAKDOWN data" — ambiguous between Section 3 and Section 4. DeepSeek uses the more proximate per-coin data.

### Root Cause #3 — XRAY structural placement is asymmetric today

For 18 of 19 XRAY_DIR_FLIPs today, `rr_long ∈ [0.0, 0.2]` and `rr_short ∈ [2.9, 8.2]`. Ratio 17×-668×, way above the 3.0× threshold. **The structural data is doing what it's designed to do** — when current price is near resistance levels, longs have terrible R:R. But this means brain's Buy signals **arrive at the wrong price location** (near resistance), and XRAY's deterministic flip is the structural rationalizer.

### Root Cause #4 — The RR-boost safety valve is dead code

`optimizer.py:367` reads `package.structure_data` but the attribute is `package.structural_data` (P.1.2 typo). All `APEX_FLIP_BLOCKED` events today show `rr_boost=0.00`. The boost was designed to lower the confidence threshold by 0.15 when X-RAY structure favors the flipped direction; it has never engaged.

Consequence: the confidence gate runs only on DeepSeek's raw confidence. DeepSeek's confidence quantizes at 0.65 (blocked) / 0.85 (passes). The boost would have changed nothing on the 23 flips that cleared 0.70; it would have changed something on the 5 blocked-at-0.65 trades (boosting them to 0.80 if RR conditions met). The bug currently makes the gate **more** conservative than designed.

### Root Cause #5 — APEX flips both ways, XRAY undoes Sell→Buy flips

APEX direction-pair distribution today: 16 Buy→Sell + 7 Sell→Buy = balanced.
XRAY direction-pair distribution today: 18 Buy→Sell + 1 Sell→Buy = unbalanced.

The cascade: APEX flips Sell→Buy (7 times). XRAY then runs on `direction = Buy`. If `rr_short / rr_long > 3`, XRAY flips Buy→Sell again. Net: many of APEX's Sell→Buy decisions are reversed by XRAY. The combined cascade produces the observed 95% final-Sell distribution.

### Root Cause #6 — Regime is mostly "ranging" by default

80% of regime observations are ranging+dead. The "ranging" classification's else-fallback (regime.py:154) catches unclassified states. This means most trades happen in a regime where APEX has **no** pre-call lock, allowing the chain above to fire.

## 3. IS It Historically Profitable? — Data-Driven Answer

**The flip policy is NOT profitable in the data.** Strongest evidence:

### Win rate evidence (most robust signal)

| Mode | Direction-pair | Δ WR vs unflipped |
|------|----------------|--------------------|
| shadow | Buy → Sell (flipped vs Buy → Buy unflipped) | **-16.1 pp** (47.8 → 31.7) |
| shadow | Sell → Buy (flipped vs Sell → Sell unflipped) | +10.4 pp (40.9 → 51.3) |
| bybit_demo | Buy → Sell | -6.8 pp |
| bybit_demo | Sell → Buy (tiny n=3) | +37.5 pp (low confidence) |

**Buy → Sell flips destroy win rate.** Sell → Buy flips help (small samples).

### Net PnL evidence

| Mode | Cohort | Net pnl_usd |
|------|--------|-------------|
| shadow | Buy → Buy unflipped (n=402) | **$+252.23** |
| shadow | Buy → Sell flipped (n=189)  | **$-238.11** |
| bybit_demo (ranging) | unflipped (n=70) | $-65.70 |
| bybit_demo (ranging) | flipped (n=86) | **$-213.87** |

**The shadow data is unambiguous** — Buy → Sell flips swing PnL by $-490 per cohort. The bybit_demo ranging-regime data confirms: flipped trades lose 3× more than unflipped.

### DeepSeek post-hoc verdict

`ds_optimal_direction` on bybit_demo: 56.7% of trades marked as wrong direction. WR for "wrong direction" cohort = 3.7%. WR for "right direction" cohort = 73.7%.

### Confidence calibration

DeepSeek's avg confidence on flipped trades = 0.83 but actual WR = 28%. **The confidence is overconfident.** A 0.70 threshold does not discriminate well.

## 4. WHAT Options Exist For The Fix?

Each option is presented neutrally with expected effect on the data shown above.

### Option 1 — Brain-Priority Gating
Block flips when brain conviction is above threshold X (e.g. raise an `apex_min_brain_conviction_to_flip` floor). Mechanism: add a pre-check in optimizer.optimize that uses `directive["signal_score"]` to gate flip permission.

- **Effect on data**: high-conviction Buy decisions reach final_dir=Buy. Low-conviction Buys may still be flipped.
- **Trade-off**: needs brain signal-score calibration; not a no-op if brain's score is noisy.
- **Touches**: optimizer.py (post-parse), no contract changes.

### Option 2 — Regime-Specific Policy Tuning
Add explicit ranging-regime conditions before allowing a flip — e.g. require Section 4 (global) AND Section 3 (per-coin) to BOTH support the flipped direction. Tighten the "INSUFFICIENT DATA" interpretation in code.

- **Effect on data**: the EGLDUSDT-style flips (0 Buy trades, 6 Sell with low WR) would be blocked.
- **Trade-off**: requires code-level data interpretation rather than relying on DeepSeek to follow the prompt.
- **Touches**: optimizer.py (new gate function), no other consumer changes.

### Option 3 — Confidence-Weighted Flip (Raise Threshold)
Raise `apex_min_flip_confidence` from 0.70 to 0.90 (or operator-tunable). Optionally also require XRAY ratio confirmation (only flip if XRAY ratio agrees + confidence ≥ X).

- **Effect on data**: today's 23 flips at conf 0.70-0.95 → maybe 10-15 would pass at 0.90. The blocked-at-0.65 cohort grows. Per-trade quality may improve but volume drops.
- **Trade-off**: simplest one-line config change. Most conservative.
- **Touches**: config.toml only (one value); no code change required.

### Option 4 — Asymmetric Flip Thresholds
Buy → Sell flips need conf ≥ 0.95; Sell → Buy flips need conf ≥ 0.70. Reflects the data: Buy→Sell destroys WR, Sell→Buy may help.

- **Effect on data**: most Buy → Sell flips today (conf 0.70-0.85) would be blocked; the 7 Sell → Buy APEX flips would survive.
- **Trade-off**: requires a small code change in `_enforce_flip_confidence`. Mid-complexity.
- **Touches**: optimizer.py.

### Option 5 — Brain Authority Restoration
APEX may optimize size/SL/TP but cannot flip direction. Direction can only change via XRAY with a much higher ratio (e.g. 10× instead of 3×).

- **Effect on data**: APEX-driven flips → 0. XRAY-driven flips still possible but rarer. Final direction much closer to brain's choice.
- **Trade-off**: most aggressive fix. Disables a designed feature. May regress some legitimate flips (where DeepSeek's reasoning genuinely catches a missed pattern).
- **Touches**: optimizer.py (remove direction from DeepSeek's response application), config.toml (raise XRAY ratio threshold).

### Option 6 — Hybrid
Combine 1+3, 2+3, or 4+5. For example: raise confidence floor to 0.85, require Section 4 alignment for ranging flips, and tighten XRAY ratio to 5×.

- **Effect on data**: combines multiple safeguards; likely most robust.
- **Trade-off**: more variables to tune.

### Option 7 — Status Quo + Better Logging
Per spec Part C — only justified if data showed flips were profitable. **The data does NOT support this option.** Documented for completeness.

### Bonus: Typo Fix For Free Boost Engagement
Independent of any option, the `package.structure_data` → `package.structural_data` typo should be fixed. But fixing it in isolation INCREASES flip-through-rate (the boost lowers the threshold by 0.15). It should be addressed jointly with whichever threshold change the operator picks.

## 5. Investigator's Recommendation (deferred to operator per spec Rule 2)

The data points most strongly to **Option 4 (asymmetric thresholds)** + **fixing the typo** + **a `(<5 trades)` block in code**:

1. Asymmetric thresholds reflect the WR data directly: Buy→Sell harms, Sell→Buy may help.
2. A code-level enforcement of "<5 trades = no flip" addresses the prompt-misreading root cause without changing model behavior.
3. Fixing the typo bug restores the RR-boost as intended; pairing with raised threshold keeps net effect conservative.

Secondary preference: **Option 2** (regime-specific tuning), specifically requiring Section 4 alignment before any flip in ranging. This breaks the feedback loop by anchoring decisions to global stats rather than per-coin compounding history.

**The operator chooses.** This recommendation is for context only.

## 6. Phase 2 Deliverable — Operator Report Outline

The Phase 2 report (`p_phase2_report.md`) will be written using h1/h2/h3 headings (screen-reader safe, no emoji) and present:

1. The strategic question
2. The mechanics (file:line, decision tree)
3. The data (direction distribution, WR by direction, flipped vs unflipped, DeepSeek verdict)
4. The 7 options (above)
5. Recommendation with reasoning
6. Explicit deferral: operator chooses

## 7. Out-of-Scope Confirmation

- No code changes in this stretch.
- SQL was read-only.
- Brain prompt construction, Layer 1 scanner, Bybit execution untouched.
