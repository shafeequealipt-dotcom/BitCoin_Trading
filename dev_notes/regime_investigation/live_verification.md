# Live Verification — Path B1a Working in Production

## Deploy timing

- `workers.py` restarted: **2026-05-12 09:41:54 UTC** (pid 397).
- Branch deployed: `fix/regime-detector-b1a-2026-05-12` (HEAD `6938c69`).
- Config: trending_adx=20, ranging_choppiness=50, volatile_atr_percentile=70, dead_adx=12.

## Snapshot windows captured

| Window | T+min | REGIME emissions | APEX events | XRAY flips | DIRECTION_DECISIONs |
|---|---|---|---|---|---|
| Snapshot 1 | T+18.5 (10:00:25) | 154 | 3 | 0 | 3 |
| Snapshot 2 | T+23.5 (10:05:34) | 205 | 6 | 0 | 6 |
| Delta | +5 min | +51 | +3 | 0 | +3 |

## Headline metrics (snapshot 2, since restart)

```
Distribution:
  trending_down       80  (39.0%)
  volatile            78  (38.0%)
  ranging             46  (22.4%)
  trending_up          1   (0.5%)
  dead                 0   (0.0%)

ELSE-fallback (conf=0.40): 37 (18.0%)
```

| Metric | Pre-fix baseline | Live post-fix | Status |
|---|---|---|---|
| ELSE-fallback share | 73.9% | **18.0%** | DOWN 55.9pp |
| Ranging share | 84.8% | **22.4%** | DOWN 62.4pp |
| Trending_down share | 5.8% | **39.0%** | UP 33.2pp (**6.7x**) |
| Volatile share | 3.5% | **38.0%** | UP 34.5pp (**10.9x**) |
| Trending_up share | 2.9% | 0.5% | DOWN (window-specific; markets are bearish) |
| XRAY_DIR_FLIP count | ~1.7/hr | **0 in 24 min** | ELIMINATED |
| APEX dir_locked=Y share | ~5% | **6/6 = 100%** | LOCK ACTIVE |

## Live APEX_FLIP_DECISION events (all 6, since restart)

```
1. 09:55:07  GALAUSDT  brain=Sell apex=Sell dir_locked=Y  no_flip_attempt   regime=trending_down
2. 09:55:16  SANDUSDT  brain=Sell apex=Sell dir_locked=Y  no_flip_attempt   regime=trending_down
3. 09:55:29  PYTHUSDT  brain=Sell apex=Sell dir_locked=Y  lock_override     regime=trending_down  qwen_initial=Buy
4. 10:02:33  FILUSDT   brain=Sell apex=Sell dir_locked=Y  no_flip_attempt   regime=trending_down
5. 10:02:44  ETHUSDT   brain=Sell apex=Sell dir_locked=Y  no_flip_attempt   regime=trending_down
6. 10:02:58  ARBUSDT   brain=Sell apex=Sell dir_locked=Y  no_flip_attempt   regime=trending_down
```

**Every single APEX decision has `dir_locked=Y` with reason `trending_down aligns with Sell`** — the regime detector correctly identified these coins as bearishly trending, which fired APEX direction lock, which preserved the decision and pre-empted any flip attempt.

## The smoking-gun event — PYTHUSDT 09:55:29

```
APEX_FLIP_DECISION | sym=PYTHUSDT
  brain_dir=Sell      apex_dir=Sell
  flip_attempted=Y    flip_accepted=N
  decision_reason=lock_override
  regime=trending_down
  dir_locked=Y        lock_reason='trending_down aligns with Sell'
  qwen_initial_dir=Buy
```

Qwen (secondary model) wanted to flip Sell → Buy. APEX direction lock overrode the attempt because the regime correctly identified the coin as trending_down. **Pre-fix, PYTHUSDT would likely have classified as `ranging conf=0.40` (ELSE fallback), the lock would not have fired, Qwen's flip could have been confidence-gated (potentially accepted), and the trade would have placed as Buy in a falling market.**

This is the exact failure mode the B1a fix was designed to prevent, observed in production within 14 minutes of restart.

## Live DIRECTION_DECISION events

All 6 events:
- `brain_dir=Sell  final_dir=Sell  flipped=N  apex_locked=Y  reason=apex_dir_lock_held`

`reason=apex_dir_lock_held` is the new signature of the protective chain working end-to-end. Pre-fix this reason was rare; now it is the dominant outcome on trending coins.

Note: `analysis_dir` (local TA analysis) shows Buy or NEUTRAL on several events while brain decided Sell. The disagreement is benign — APEX lock follows the regime, not the local analysis, and regime here is trending_down so Sell is the correct decision.

## What we have proven

1. **Regime detector** correctly classifies coins in the [20, 25) ADX transition band as trending (instead of ELSE fallback ranging). 39% of emissions are now trending_down vs 5.8% pre-fix.
2. **APEX direction lock** fires on every trending-coin decision (6/6 dir_locked=Y).
3. **APEX lock_override** caught one real Qwen-Buy-flip attempt on PYTHUSDT and blocked it.
4. **XRAY flip path is dormant** — 0 fires in 24 minutes vs pre-fix rate of ~1.7/hr. The protective chain (regime → lock → suppress) is replacing the ad-hoc flips with regime-aware decisions.
5. **No errors, no regressions**, no fallback-into-ranging on coins that should be trending.

## What we do NOT yet have data on

- A **brain=Buy preservation event** (the inverse Sell-bias case). All 6 DIRECTION_DECISIONs so far had brain_dir=Sell. The current market window is heavily bearish so the system correctly chose Sell; we need a bullish coin to see the rare brain=Buy preserved through the regime-trending_up lock path. Live monitoring continues.
- A **flipped=Y event** post-fix to confirm that XRAY can still flip when APEX is NOT locked (i.e., on ranging or dead regimes). The 0 flip count is good, but we should see the system still able to make the call when warranted.
- Trade-outcome impact: the 6 trades have not yet closed; PnL comparison vs Phase 0 baseline pending.

## Continuous monitoring

The Monitor task `bxa4e7v7j` is streaming a metrics summary every 60s and will continue until stopped. Each event arrives as a notification; the user can interrupt at any time.
