# PRIMARY Issue — Phase 1 Step P.1.9: DeepSeek/Qwen Response Inspection

Sources:
- `trade_intelligence.apex_reasoning` column (DeepSeek's free-text reasoning, captured per trade)
- Live `APEX_FLIP` / `APEX_FLIP_BLOCKED` log lines from today

Coverage: 324 / 335 trade_intelligence rows have `apex_reasoning` populated. Sample of 5+5 read in detail. 23 live APEX_FLIPs from today categorized.

Status: investigation only — no code changes.

## 1. APEX_FLIP_BLOCKED Sample — Typo Bug Confirmed

5 sample lines (from today's logs):

```
APEX_FLIP_BLOCKED | sym=MANAUSDT  reason='flip Sell→Buy in regime=ranging blocked: conf=0.65<0.70' raw_conf=0.65 eff_conf=0.65 rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00 regime=ranging
APEX_FLIP_BLOCKED | sym=MANAUSDT  reason='flip Buy→Sell in regime=ranging blocked: conf=0.65<0.70' raw_conf=0.65 eff_conf=0.65 rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00 regime=ranging
APEX_FLIP_BLOCKED | sym=XRPUSDT   reason='flip Buy→Sell in regime=ranging blocked: conf=0.65<0.70' raw_conf=0.65 eff_conf=0.65 rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00 regime=ranging
APEX_FLIP_BLOCKED | sym=MONUSDT   reason='flip Sell→Buy in regime=ranging blocked: conf=0.65<0.70' raw_conf=0.65 eff_conf=0.65 rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00 regime=ranging
APEX_FLIP_BLOCKED | sym=HBARUSDT  reason='flip Buy→Sell in regime=ranging blocked: conf=0.65<0.70' raw_conf=0.65 eff_conf=0.65 rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00 regime=ranging
```

**Every blocked event shows `rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00`.** This confirms the typo bug from P.1.2:
- `optimizer.py:367` reads `getattr(package, "structure_data", None)` — wrong attribute name
- The correct attribute on `IntelligencePackage` is `structural_data`
- The RR-boost path is dead code; it has never engaged in production

Notable secondary finding: **DeepSeek reports raw_conf = 0.65 on blocked flips**, suggesting DeepSeek is calibrating its self-reported confidence around 0.65-0.85 with a noticeable cluster at 0.65 (just below the 0.70 threshold). The 5 blocks are bunched at exactly conf=0.65 — DeepSeek may have a quantization tendency.

## 2. APEX_FLIP Sample — Both Directions Flipped

5 APEX_FLIP samples (today):

```
APEX_FLIP | sym=FILUSDT   claude=Buy  apex=Sell sl=0.8% tp=1.4% cls=medium sz=$10000→$1200 mode=fixed conf=85% regime=ranging
APEX_FLIP | sym=ADAUSDT   claude=Sell apex=Buy  sl=0.5% tp=0.6% cls=medium sz=$16000→$100  mode=fixed conf=70% regime=ranging
APEX_FLIP | sym=CRVUSDT   claude=Sell apex=Buy  sl=1.6% tp=2.9% cls=high   sz=$15000→$1200 mode=fixed conf=75% regime=ranging
APEX_FLIP | sym=NEARUSDT  claude=Sell apex=Buy  sl=0.5% tp=1.4% cls=medium sz=$14000→$100  mode=fixed conf=95% regime=ranging
APEX_FLIP | sym=GMTUSDT   claude=Buy  apex=Sell sl=0.8% tp=1.4% cls=medium sz=$16000→$1200 mode=fixed conf=85% regime=ranging
```

Today's full APEX_FLIP direction distribution (23 flips):
- Buy → Sell: **16 (69.6%)**
- Sell → Buy: **7 (30.4%)**

**APEX flips BOTH directions.** It is NOT one-directional. The 23 APEX flips were 16 Buy→Sell and 7 Sell→Buy.

## 3. The Combined Flip Picture (APEX + XRAY)

| Path | Today's count | Buy→Sell | Sell→Buy |
|------|---------------|----------|----------|
| APEX_FLIP (from optimizer)   | 23 | 16 | 7 |
| XRAY_DIR_FLIP (from strategy_worker) | 19 | 18 | 1 |

XRAY accounts for the asymmetry — its 18/19 Buy→Sell pattern reflects the structural-placement-based asymmetry from P.1.5 (`rr_long ≈ 0`, `rr_short` large on most trades).

**Critical sequence**: After APEX runs, XRAY runs. If APEX flipped Sell→Buy (7 cases), XRAY's gate then runs on the new `direction = "Buy"`. The structural placement check happens; if `rr_short / rr_long > 3`, XRAY flips Buy→Sell again. **So APEX's 7 Sell→Buy flips can be reversed by XRAY**, producing a final Sell anyway.

Net result: even though APEX is symmetric (flips both ways), the cascade with XRAY produces a Sell-biased final state — corroborated by today's DIRECTION_DECISION distribution (62/65 = 95.4% final_dir=Sell).

## 4. DeepSeek Reasoning Samples — The Real Bias Source

Five `apex_reasoning` samples for bybit_demo flipped trades, ordered by recency:

### Sample 1 — FILUSDT (Buy → Sell, ranging, conf 0.85)
> "TIAS shows Sell has 47% WR (7W/8L) in ranging regime with net $+15.32 profit, while Buy has 0 trades. Profit Factor=2.07 indicates wins are larger than losses in dollars. X-RAY shows downtrend structu[re]..."

DeepSeek's logic: per-coin TIAS shows 15 Sell trades with 47% WR; **Buy has 0 trades**. The "INSUFFICIENT DATA" rule from the system prompt says "<5 trades for a direction is NOT enough to justify a flip — keep trader's original direction". But DeepSeek **inverts** the rule: it keeps the direction with MORE data (Sell), not the trader's original (Buy). The rule says "if no Buy data, keep Buy"; DeepSeek reads "if no Buy data, default to Sell".

### Sample 2 — ARBUSDT (Buy → Sell, ranging, conf 0.85)
> "Trader's direction (Buy) is correct. TIAS shows Buy has 50% WR (2 trades) vs Sell 0% WR (3 trades) in ranging regime, with Buy net profit +$9.85 vs Sell -$2.20. X-RAY setup is A+ with bullish OB, uptr[end]..."

DeepSeek's text **acknowledges Buy is correct** and even states the data shows Buy is better. Yet the recorded `apex_final_direction = Sell`. **This means the final Sell came from XRAY's later flip, not from APEX**. APEX likely returned `direction = Buy` in this case (no flip), and `XRAY_DIR_FLIP` overrode it later. The `apex_reasoning` field reflects DeepSeek's text but not the XRAY post-mutation.

### Sample 3 — HBARUSDT (Buy → Sell, trending_up, conf 0.75, LOCKED)
> "[DIR LOCKED to Buy] TIAS history shows 4 Sell trades in trending_up regime with 25% win rate but net profit of $+1.42 and profit factor 2.12, indicating Sell captures larger wins despite lower win rate..."

The prompt was injected with "[DIRECTION LOCKED: Buy — trending_up aligns with Buy. Do NOT change direction.]" — and DeepSeek's reasoning includes the "[DIR LOCKED to Buy]" prefix. **APEX did NOT flip** (the lock was respected). The final Sell came from **XRAY bypassing the lock** — this is a pre-Issue-1-fix trade, before XRAY learned to suppress flips on APEX-locked directions. The Issue 1 fix shipped 2026-05-11 (today).

### Sample 4 — ALICEUSDT (Buy → Sell, ranging, conf 0.85)
> "TIAS history shows Buy is 0W/2L (0% WR) in ranging regime, while Sell is 1W/0L (100% WR). Regime is ranging, so direction flip is allowed. TIAS data overwhelmingly supports Sell. Trader's TP=0.1565 (3..."

Tiny sample (2 Buy + 1 Sell) yet DeepSeek calls it "overwhelming". The INSUFFICIENT DATA threshold (<5 trades) was not respected by DeepSeek's interpretation — both directions are below the floor, yet DeepSeek treats the cohort as overwhelming evidence.

### Sample 5 — EGLDUSDT (Buy → Sell, ranging, conf 0.7)
> "TIAS history shows 6 Sell trades (1W/5L) vs 0 Buy trades in ranging regime. Although Sell WR is low (17%), Buy has zero data, making a flip to Buy unjustified (<5 trades). X-RAY shows excellent short..."

This is the **clearest demonstration of the feedback loop**:
- Brain says Buy.
- TIAS shows 6 prior Sell trades (1 win, 5 losses) + 0 Buy.
- DeepSeek reads: "Buy has zero data → flip to Buy unjustified → keep Sell" (but this is a Buy → Sell flip, not "keep Sell")
- The reasoning is internally inconsistent: the trader's direction IS Buy, and "flip to Buy unjustified" should mean "keep Buy", not "flip to Sell".
- DeepSeek's mental model appears to be "Buy and Sell each need ≥5 trades; whichever has more data wins" — which is **not what the system prompt says**.

### Pattern Synthesis Across All 5 Reasoning Samples

DeepSeek's prompt-followed logic in ranging:
1. Look at per-coin direction breakdown (Section 3).
2. If one direction has noticeably more trades, **prefer that direction**, even if its WR is mediocre or absent.
3. The "INSUFFICIENT DATA" clause is interpreted as "I can't trust the LOW-DATA direction; default to the HIGH-DATA direction".
4. This produces a **self-reinforcing Sell-bias**: every Sell trade strengthens the Sell historical count; every Buy trade is too rare to count.

**This is the prompt-instruction failure mode.** The system prompt asks DeepSeek to "Keep the trader's original direction" when one direction has <5 trades. DeepSeek instead reads the rule as a license to flip to the data-richer direction.

## 5. The Feedback Loop In One Diagram

```
Brain says: Buy (per-coin Buy data thin)
   ↓
APEX prompt shows Section 3: Buy=0-2 trades, Sell=6-15 trades
   ↓
DeepSeek interprets: "flip to Sell — Buy has insufficient data"
   ↓
APEX returns: direction=Sell with conf=0.85
   ↓
Confidence gate (0.70 threshold): passes
   ↓
XRAY runs on direction=Sell: rr_short usually fine, no flip
   ↓
Trade placed: Sell
   ↓
Outcome: ~30% WR (per data)
   ↓
TIAS stores: another Sell trade for this coin
   ↓
Next Buy signal for this coin: Section 3 shows even more Sell history
   ↓
DeepSeek flips again → tighter Sell-bias
   ↓
LOOP
```

## 6. The HBARUSDT Lock Bypass (pre-Issue-1-fix)

The HBARUSDT sample shows:
- APEX issued `APEX_DIR_LOCK` (trending_up → keep Buy)
- DeepSeek respected the lock (reasoning prefixed `[DIR LOCKED to Buy]`)
- The trade still placed as Sell

This is **only possible if XRAY flipped the trade after APEX's lock**. Before Issue 1 fix (2026-05-11), XRAY did NOT respect APEX_DIR_LOCK. The 122 flipped trades in trending_up regime found in P.1.8 are these XRAY-bypassing-lock trades. After Issue 1 fix, `XRAY_FLIP_SUPPRESSED_BY_LOCK` should fire instead (and did fire 1 time today).

## 7. DeepSeek Confidence Calibration

From P.1.8 sizing data plus today's APEX_FLIP samples:
- Avg confidence on flipped trades (bybit_demo): 0.83
- Avg confidence on unflipped trades (bybit_demo): 0.77
- Today's APEX_FLIP confidences: 0.70, 0.75, 0.85, 0.85, 0.85, 0.95 (mode at 0.85)
- Today's APEX_FLIP_BLOCKED confidences: all 0.65 (mode quantization)

**DeepSeek's confidence is not well-calibrated to outcome**: 28-30% actual WR on flipped trades despite 0.83 avg self-reported confidence. The 0.70 threshold (`apex_min_flip_confidence`) does not discriminate winning flips from losing flips — DeepSeek's confidence is overconfident across both.

A natural Phase 2 option: raise the threshold to 0.90 or 0.95 to filter out the bulk of mid-confidence flips. The data does not show 0.95-confidence flips are wins — but the sample is too small to assert.

## 8. Findings Map

| Question | Answer |
|----------|--------|
| Does the RR-boost actually engage? | NO — typo bug at optimizer.py:367 (P.1.2). Every APEX_FLIP_BLOCKED today shows rr_boost=0.00. |
| Does the prompt bias DeepSeek toward Sell? | Indirectly: per-coin TIAS Section 3 is more recently Sell-heavy due to feedback loop. DeepSeek over-weighs per-coin data and follows the dominant direction. |
| Is DeepSeek violating the INSUFFICIENT DATA rule? | YES — observed in 2 of 5 reasoning samples. The rule says "keep trader's direction" when <5 trades; DeepSeek interprets as "default to data-richer direction". |
| Does APEX flip both directions or just Buy→Sell? | Both. 16 Buy→Sell and 7 Sell→Buy today. The final Sell bias comes from XRAY flipping APEX-flipped Sell→Buy trades back to Sell. |
| Does APEX_DIR_LOCK work? | Within APEX yes (lock-override path verified). But pre-Issue-1-fix, XRAY bypassed the lock — HBARUSDT example shows this. Post-fix, `XRAY_FLIP_SUPPRESSED_BY_LOCK` fires (1 case today). |
| Is DeepSeek confidence calibrated? | No — 83% avg confidence corresponds to 28% actual WR. |

## 9. Implications For Phase 2

- The feedback loop is the **root mechanism**. Fixes that break the loop are highest leverage:
  - Block flips when one direction has <5 trades (true to the prompt rule) — would prevent the EGLDUSDT / FILUSDT type
  - Use **global** TIAS direction breakdown (Section 4) as the deciding statistic instead of per-coin (Section 3) — Section 4 correctly shows Buy is better in ranging
  - Decay older Sell-history when computing the per-coin breakdown so the loop doesn't compound across days
- The HBARUSDT lock-bypass case is **already fixed by Issue 1 (2026-05-11)** — no Phase 2 work needed there.
- Raising `apex_min_flip_confidence` from 0.70 is supported by data: today's flips bunch at 0.65-0.85, so 0.90 would block most of them.
- The typo bug should be fixed as part of whichever Phase 2 option is chosen — but in isolation, fixing it INCREASES flip-through-rate (the boost was designed to lower the threshold). Operator should decide whether to fix it + raise threshold, or leave the boost dormant.

## 10. Out-of-Scope Confirmation

- No code changes.
- No live capture of new DeepSeek calls (services are stopped). Sampling is from existing logs and persisted `trade_intelligence.apex_reasoning`.
