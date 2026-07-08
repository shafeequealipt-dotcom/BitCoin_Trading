# T2-3 Phase 1 — F11 Brain vs XRAY contradiction investigation

## 1. Defect statement

Three independent directional sanity layers operate on the same coin signal but enforce inconsistently:

1. **analysis.engine** (`src/analysis/engine.py:158`) — emits `Analysis complete for SYM TF: BUY/SELL/NEUTRAL` with score + confidence. Today's WAVE 7: ADAUSDT BUY conf=0.59, SOLUSDT SELL conf=0.58. **NOT consumed by the trade pipeline for direction enforcement.** The brain sees it (via APEX assembler `apex/assembler.py:234`) but no post-decision conflict gate fires.
2. **xray_flip** (`src/workers/strategy_worker.py:1756-1851`) — the ONLY layer that actually flips trade direction. Sets `_flip_source = "xray"` and rewrites `direction` on the trade dict. Live evidence: BLURUSDT brain=Buy → xray_flipped to Sell at 136.5x ratio (which subsequently turned out to be the wrong direction, -$21.69 loss).
3. **APEX flip** (`src/apex/optimizer.py:395,413,966-1002`) — APEX may propose a flip if its model disagrees with brain direction. Today guarded by confidence threshold (e.g. 0.70 for ranging regime, per F23 of the report). When APEX wants to flip but is below threshold, emits `APEX_FLIP_BLOCKED` and proceeds with brain's direction.

The system's `DIRECTION_DECISION` log line at `strategy_worker.py:2215` summarises the final outcome but does NOT include the analysis.engine verdict — making brain-vs-analysis disagreement invisible to post-hoc audit.

## 2. WAVE 7 evidence breakdown

ADAUSDT: brain=Sell rsn=RANGE_FADE_SHORT, analysis.engine 5m BUY score=+0.21 conf=0.59. No flip fired. Trade proceeded Sell.

SOLUSDT: brain=Buy rsn=TREND_PULLBACK_LONG, analysis.engine 5m SELL score=-0.24 conf=0.58. No flip fired. Trade proceeded Buy.

SKRUSDT: brain=Sell, analysis.engine SELL (agreement; trade proceeded fine).

2 of 3 trades had a brain-vs-analysis directional contradiction with no resolution mechanism. APEX did not flip (because its own model agreed with brain or confidence was below threshold). xray did not flip (because xray's structural assessment agreed with brain).

So the analysis.engine is a THIRD independent signal that is currently fully informational — it appears in logs and in the APEX assembler's context for the brain, but downstream of the brain's decision it has no enforcement role.

## 3. Design space

Three operator-policy choices for handling brain-vs-analysis disagreement:

| Layer | What it does today | What the fix could add |
|-------|---------------------|------------------------|
| analysis.engine | Logged only. No downstream enforcement. | (a) Emit BRAIN_VS_ANALYSIS_DISAGREEMENT at WARN when they conflict. (b) Downgrade the trade (size halve). (c) Hard reject the trade. |
| xray_flip | Flips trade direction when xray_ratio > threshold. | Unchanged. xray is the writer-of-record for direction. |
| APEX flip | Flips when APEX confidence > regime-dependent threshold. | Unchanged. |
| DIRECTION_DECISION log | Captures brain → APEX → xray → final + reason. | Add `analysis_dir` field so post-hoc audit can see "did analysis agree?". |

Critical aim-preservation note: the operator's aggressive-exploitation philosophy means more rejection of analysis-disagreeing trades may reduce trade frequency. The current behaviour ("notice, don't enforce") preserves frequency at the cost of some questionable directional calls.

## 4. Where to wire the analysis verdict

The verdict is already computed by APEX assembler (`src/apex/assembler.py:234`) — it just isn't propagated past the brain's prompt. To make it available at DIRECTION_DECISION time:

- Approach 1: Have the APEX assembler stamp `_analysis_verdict` on each per-coin section that flows to the strategist.
- Approach 2: Re-run `ta_cache.analyze` inside strategy_worker just before order placement (extra ~50-200 ms but local to the trade path).
- Approach 3: Read from a shared cache that other workers populate.

Approach 1 is cleanest (already computing the verdict).

## 5. Investigation conclusions

1. The analysis.engine is the 3rd direction signal but is wholly INFORMATIONAL today — no enforcement layer reads it post-brain-decision.
2. Other two layers (xray_flip, APEX flip) DO enforce, but inconsistently: xray flips ~always; APEX flips only above regime-conditional confidence threshold.
3. Wiring the analysis verdict into DIRECTION_DECISION is a simple visibility addition. Adding enforcement requires explicit operator design choice (downgrade vs reject).
4. The 1-of-3 BLUR counter-example (xray flipped 136.5x and was wrong) suggests that more aggressive enforcement is NOT obviously a win — disagreements are sometimes "the data engine is wrong, brain is right".

Phase 2 proposal follows.
