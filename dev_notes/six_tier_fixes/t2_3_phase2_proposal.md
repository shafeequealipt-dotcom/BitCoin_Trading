# T2-3 Phase 2 — F11 Brain vs XRAY contradiction proposal

## 1. Confirmed diagnosis

- Three independent direction sanity layers (analysis.engine, xray_flip, APEX flip) operate on the same coin signal but enforce inconsistently.
- The analysis.engine verdict is INFORMATIONAL only today — never consumed by post-brain-decision enforcement.
- The DIRECTION_DECISION log emits final state but does not include analysis_dir.
- WAVE 7 today: 2 of 3 new trades had brain disagreeing with analysis with no resolution.

## 2. Three solution options

### Option A — Visibility only (recommended for first pass)

Wire the analysis verdict (already computed in APEX assembler) onto the trade dict. Update `DIRECTION_DECISION` log to include `analysis_dir` and emit a structured `BRAIN_VS_ANALYSIS_DISAGREEMENT` WARN when brain_dir != analysis_dir AND no flip occurred. Do NOT enforce — log only.

- Edits: APEX assembler stamps `_analysis_verdict`, `_analysis_score`, `_analysis_conf` on each per-coin output; strategy_worker:2215 reads them; layer_manager / strategy_worker emits the conflict warn.
- LOC: ~25.
- Pros:
  - Makes the issue visible without changing behaviour.
  - Operator can collect statistics (flip_win_rate vs unflipped_win_rate) for future enforcement decisions.
  - Zero impact on trade frequency.
- Cons:
  - Doesn't actively resolve disagreements.
  - The system continues to make some directionally-questionable trades.

### Option B — Downgrade on N-of-3 disagreement

Same wiring as A. Additionally, when at least 2 of 3 layers disagree with the brain (e.g. brain=Sell but both analysis and APEX wanted Buy, and xray didn't flip): set `_gate_downgrade_for_disagreement` and halve the trade size in apex/gate.py. Doesn't reject, just sizes down.

- Edits: A's edits plus a 5-line size-halve block in apex/gate.py.
- LOC: ~35.
- Pros:
  - Adds soft resolution without rejecting trades.
  - Preserves aggressive-exploitation but reduces exposure on contested setups.
- Cons:
  - Doubles size-halve sources (T2-1 cooldown already halves; this would also halve). Risk: too-small final size.

### Option C — Hard reject on N-of-3 disagreement

Same wiring as A. When at least 2 of 3 layers disagree with the brain: set `_gate_rejected = "direction_consensus_failed"` and skip via the layer_manager path introduced in T2-1.

- Edits: A's edits plus a 5-line reject block.
- LOC: ~35.
- Pros:
  - Strong resolution: contested-direction trades never execute.
- Cons:
  - Reduces trade frequency (aggressive-exploitation aim is impacted).
  - BLUR counter-example shows agreement-checking can be WRONG (xray was 136.5x confident and lost). Reject-on-disagreement could prevent legitimate brain insights.

## 3. Recommendation

**Option A (visibility only) for this engagement.**

Reasons:

1. The BLUR counter-example (xray 136.5x confident, wrong direction, -$21.69 loss) shows that enforcement against brain direction is NOT obviously correct.
2. Today's data is insufficient to conclude whether brain-disagreeing-with-analysis is BAD (brain hallucinating) or GOOD (brain seeing what data engines miss).
3. Visibility-first lets the operator collect 24-48h of evidence (BRAIN_VS_ANALYSIS_DISAGREEMENT counts vs trade outcomes) before committing to enforcement.
4. The plan explicitly notes T2-3 is "a design decision the operator must own" — the safest first step is data collection.

After 1-2 weeks of visibility data, the operator can decide on enforcement in a separate engagement.

## 4. Aim preservation

Option A preserves trade frequency 100%. Options B and C reduce it. Pick A.

## 5. Observability additions

- `_analysis_verdict`, `_analysis_score`, `_analysis_conf` stamped on trade dict by APEX assembler.
- `DIRECTION_DECISION` log adds `analysis_dir=<BUY|SELL|NEUTRAL>` field.
- `BRAIN_VS_ANALYSIS_DISAGREEMENT sym=X brain_dir=Y analysis_dir=Z conf=N flipped=Y/N flip_source=X` — WARN, fires when brain != analysis AND no flip occurred. Allows hourly counts for decision-quality monitoring.

## 6. Test plan (smoke, ≤10 min)

`tests/test_t2_3_direction_disagreement.py` — 3 tests around the predicate that decides whether to emit BRAIN_VS_ANALYSIS_DISAGREEMENT.

## 7. Operator decision required

A / B / C. Default A (recommended). Operator can pick more aggressive enforcement at their discretion.
