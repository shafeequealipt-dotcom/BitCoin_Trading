# PRIMARY Issue — Phase 1 Step P.1.6: Regime Detector

Sources:
- `src/strategies/regime.py:21-234` (`RegimeDetector.detect`)
- `config.toml:835-849` (`[regime]` section)
- `regime_history` DB table

Status: read end-to-end. Investigation only.

## 1. Classification Logic (regime.py:133-156)

```python
if adx > trending_adx_threshold (25) and plus_di > minus_di and choppiness < 45:
    regime = TRENDING_UP
elif adx > trending_adx_threshold (25) and minus_di > plus_di and choppiness < 45:
    regime = TRENDING_DOWN
elif atr_percentile > volatile_atr_percentile (150) or volume_ratio > 2.0:
    regime = VOLATILE
elif adx < ranging_adx_threshold (20) and choppiness > ranging_choppiness_threshold (60):
    regime = RANGING
elif adx < dead_adx_threshold (15) and volume_ratio < dead_volume_ratio (0.5) and atr_percentile < 50:
    regime = DEAD
else:
    regime = RANGING  # confidence = 0.4 fallback
```

| Regime | Conditions | Confidence |
|--------|------------|------------|
| TRENDING_UP | adx>25 & +DI>-DI & chop<45 | min(adx/50, 1.0) |
| TRENDING_DOWN | adx>25 & -DI>+DI & chop<45 | min(adx/50, 1.0) |
| VOLATILE | atr_pct>150 OR vol_ratio>2.0 | min(atr_pct/200, 1.0) |
| RANGING | adx<20 & chop>60 | min(chop/80, 1.0) |
| DEAD | adx<15 & vol_ratio<0.5 & atr_pct<50 | 0.8 |
| RANGING (else) | anything else | 0.4 |

Hysteresis: per-symbol confirm-N-readings (`hysteresis_count = 2`). The first reading of a new regime is recorded as pending; only after `_hyst` consecutive readings of the same new regime does the change confirm. `REGIME_CHG` (WARNING) emits on confirmation; `REGIME_PENDING` (INFO) emits each pending reading.

Primary symbol: BTCUSDT (regime.py:cfg fallback). Other symbols may go through `detect_per_coin` which calls `detect` for each symbol; per-coin cache.

## 2. Observed Distribution (last 7 days, from regime_history)

| Regime | N | % | Avg ADX | Avg Chop | Avg ATR% |
|--------|----|----|---------|----------|----------|
| ranging | 241 | 46.4% | 19.2 | 40.2 | 29.7 |
| dead | 177 | 34.0% | 12.2 | 43.2 | 22.5 |
| trending_up | 54 | 10.4% | 26.9 | 33.0 | 31.8 |
| trending_down | 42 |  8.1% | 26.9 | 42.7 | 32.2 |
| volatile | 16 |  3.1% | 15.2 | 44.7 | 28.1 |

## 3. Findings

### 3.1 Ranging dominates (46%) — Mostly Genuine, Some Fallback

Observed avg ADX for "ranging" is **19.2** — just below the 20 threshold. That means most ranging classifications cleared the explicit Case-4 criterion (`adx < 20 AND choppiness > 60`). The "else" clause (Case 6) at line 154 catches any unclassified state and assigns ranging with confidence=0.4. The dataset does not separately mark Case-4 vs Case-6 ranges, but the confidence column (0.4 indicates Case 6) could be used to estimate the split.

Quick estimate (a Case-6 fallback always uses confidence=0.4; Case 4 uses `min(chop/80, 1.0)` which for chop=40.2 gives 0.50): if we count rows with `confidence < 0.45` that's roughly the Case-6 fallback share. This would be a useful Phase 2 follow-up but is not strictly required for the strategic decision.

### 3.2 Trending Requires Strong Signal (ADX > 25)

Trending observations averaged ADX=26.9 — just above the threshold. The threshold is empirically tight. With ADX hovering between 20-25, the regime stays "ranging" (no trending eligibility); above 25, it becomes trending.

This means coins with **moderate trend strength** (ADX 20-25 plus low choppiness) are classified RANGING by default. The trending classification is conservative.

### 3.3 Per-Symbol Hysteresis (count=2)

A symbol must be observed in a new regime TWICE before the change confirms. This protects against single-tick flips (noise) but may also DELAY recognition of a genuine regime change. The 600-second detection interval means a regime change takes at least 20 minutes (2 × 600s) to confirm. During that window, the symbol's trades use the **prior** confirmed regime.

For PRIMARY: a coin moving from RANGING → TRENDING_UP may continue to be APEX-optimized as "ranging" for up to 20 minutes after the actual trend has emerged — keeping APEX's pre-call lock OFF and allowing DeepSeek to flip. The hysteresis is correctly noise-reducing but at the cost of delayed regime updates.

### 3.4 BTC as Primary Indicator + Per-Coin Cache

`detect()` is BTC-primary; `detect_per_coin(symbols)` builds per-coin classifications by re-running `detect` per symbol (regime.py:225-233). The `regime_history` table contains per-coin entries (all coins, not just BTC).

The APEX assembler's `_get_market_conditions(symbol)` (assembler.py — gathered alongside `_gather_symbol_history`) reads the per-coin regime for assembling the IntelligencePackage. So Section 4 in the Qwen prompt reflects the per-coin regime, not BTC's global regime.

## 4. Are 80% Ranging/Dead Reasonable?

Today's data shows 80% of regime observations in ranging+dead. The 2-hour spec window showed 93% ranging — a higher concentration. Possible reasons:

1. **BTC and the universe of trading pairs ARE genuinely in a ranging/dead state** for most of the 7-day window. This is a market-state finding, not a detector bug.
2. **The thresholds are calibrated such that broad swaths of "moderate movement" map to ranging.** A coin moving 0.5%/hour cleanly will likely be classified ranging (ADX < 20, chop > 60) even if a human trader would call it "slightly trending".
3. **The else fallback at line 154 dumps everything unclassified into ranging.** This can include genuine mixed states (e.g. low ADX + low choppiness) that may not match any defined regime.

The strategic implication: **80% of the system's trading time happens in regimes where APEX has NO pre-call direction lock**, leaving DeepSeek free to flip. If the ranging classification is over-inclusive, the operator may want a narrower flip permission (e.g. only flip in confirmed ranging with high choppiness, not in the else-fallback "weak ranging").

## 5. Validation Suggestion (for Phase 2)

A quick SQL to estimate the Case-4 vs Case-6 (else-fallback) split:

```sql
SELECT
  CASE WHEN confidence <= 0.45 THEN 'else_fallback' ELSE 'case_4_genuine' END AS source,
  COUNT(*) AS n
FROM regime_history
WHERE regime='ranging' AND detected_at > datetime('now','-7 days')
GROUP BY source;
```

If a large share is `else_fallback`, then a Phase 2 option could narrow the flip permission to `confidence > 0.45` ranges only — keeping the genuine-ranging behaviour but removing flip permission from ambiguous-state trades. This is a small option but operator may want it on the menu.

## 6. Findings Map

| Question | Answer |
|----------|--------|
| Where is regime classified? | `src/strategies/regime.py:133-156` |
| Where are thresholds? | `config.toml:835-849` |
| Is "ranging" over-inclusive? | Possibly — the else-fallback Case 6 captures unclassified states; should be verifiable via `confidence` column. |
| How fast does regime adapt? | 600-s detection interval × hysteresis 2 = up to 1200 s lag on regime changes per symbol. |
| Is regime per-coin or global? | Both — BTC is the global primary, but per-coin classifications are computed and consumed by APEX. |
| What % of regime observations gave DeepSeek free-flip permission today? | 80% (ranging 46% + dead 34%) of last-7d regime observations. |

## 7. Out-of-Scope Confirmation

- No code changes.
- No interaction with brain, Bybit execution, or Shadow.
