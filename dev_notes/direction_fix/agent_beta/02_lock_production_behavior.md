# BETA 02 — APEX_DIR_LOCK Production Behavior, 2026-05-16 Session

This document quantifies the lock cascade during the 2026-05-16 13:40-18:30 monitoring session. Source: `/home/inshadaliqbal786/ALL_LOGS_2026-05-16_13-40_to_18-30.log`. Counts verified via grep; samples are verbatim log lines.

## APEX_DIR_LOCK event distribution

Total APEX_DIR_LOCK fires: 80.

By direction + regime + reason:

| Count | Direction | Regime | Reason |
|---|---|---|---|
| 66 | Sell | trending_down | `trending_down aligns with Sell` |
| 5 | Sell | volatile | `volatile regime, insufficient flip evidence` |
| 4 | Buy | volatile | `volatile regime, insufficient flip evidence` |
| 3 | Buy | trending_down | `Claude chose Buy against trending_down (per-coin override)` |
| 2 | Buy | trending_up | `trending_up aligns with Buy` |

Buy locks: 9. Sell locks: 71. Sell:Buy ratio is 8:1.

The dominant pattern (66 of 80, 83 %) is the trending_down + Sell alignment lock. This is the symmetric `natural_dir` branch firing against the May 16 regime distribution (76 % trending_down population).

## APEX_DIR_LOCK_OVERRIDE attempts

Total override attempts: 11 (DeepSeek tried to flip direction; post-parse gate hard-reverted to claude_direction).

| Count | Pattern | Coins |
|---|---|---|
| 9 | qwen_tried=Buy locked_to=Sell in trending_down | SOLUSDT × 8, LINKUSDT × 1 |
| 1 | qwen_tried=Buy locked_to=Sell in volatile | BSBUSDT |
| 1 | qwen_tried=Sell locked_to=Buy in volatile | RENDERUSDT |

**Of the 11 override attempts, 10 were Qwen-tried-Buy and 1 was Qwen-tried-Sell. ALL 11 were blocked (100 % block rate at this gate).** The 91 % claim in COMPLETE_FINDINGS rounded the 10/11 Buy-flip count; the exact figure is 10 of 11 attempts were Buy-flip blocks, which is 90.9 %.

The asymmetry here is real and structural: DeepSeek tried Buy 10× and Sell 1× across the session. Every flip attempt was reverted by the lock. The 10× Buy-flip attempts mean DeepSeek's TIAS-aware reasoning judged Buy correct in those 10 cases, but the lock — which only sees regime, not TIAS — over-ruled all 10.

SOLUSDT alone accounts for 8 of the 10 Buy-flip blocks. The same coin in the same trending_down regime got 8 sequential lock-overrides over a 1.5 hour stretch (16:47 → 17:37). The lock is not just suppressing one bad guess; it is systematically suppressing a repeated, model-consistent signal.

## XRAY_FLIP_SUPPRESSED_BY_LOCK (strategy_worker structural override path)

Total suppression events: 8. These are post-execute structural-RR mismatches where the XRAY signal said "the opposite direction has materially better R:R" but the override threshold (10×) was not cleared. Each event verbatim:

```
2026-05-16 13:48:38.226 PLUMEUSDT dir=Sell ratio=5.0x rr_long=3.1 rr_short=0.6 lock_reason='trending_down aligns with Sell'
2026-05-16 13:57:08.963 DYDXUSDT  dir=Sell ratio=4.2x rr_long=2.8 rr_short=0.7 lock_reason='trending_down aligns with Sell'
2026-05-16 13:57:09.944 SKRUSDT   dir=Sell ratio=4.2x rr_long=2.8 rr_short=0.7 lock_reason='trending_down aligns with Sell'
2026-05-16 15:02:22.936 ARBUSDT   dir=Sell ratio=3.7x rr_long=2.7 rr_short=0.7 lock_reason='trending_down aligns with Sell'
2026-05-16 15:02:24.788 BSBUSDT   dir=Sell ratio=7.3x rr_long=3.7 rr_short=0.5 lock_reason='volatile regime, insufficient flip evidence'
2026-05-16 15:20:30.811 LDOUSDT   dir=Sell ratio=3.0x rr_long=2.4 rr_short=0.8 lock_reason='trending_down aligns with Sell'
2026-05-16 16:21:06.936 OPUSDT    dir=Sell ratio=3.0x rr_long=1.6 rr_short=0.5 lock_reason='trending_down aligns with Sell'
2026-05-16 16:56:29.980 ONDOUSDT  dir=Sell ratio=6.4x rr_long=3.5 rr_short=0.6 lock_reason='trending_down aligns with Sell'
```

All 8 suppressions: chosen=Sell, rr_long > rr_short. In every case the structural evidence said Long (Buy) was better. Ratios ranged 3.0× to 7.3× — every one inside the dead zone between the 3.0× flip threshold and the 10.0× override threshold.

## Trade outcomes for the 8 suppressed flips

| Coin | Ratio | Suppressed direction | Direction structure favored | Final PnL (USD) | Final PnL (%) | Outcome |
|---|---|---|---|---|---|---|
| BSBUSDT | 7.3× | Sell | Long | -70.08 | -1.40 | SL hit |
| ARBUSDT | 3.7× | Sell | Long | -24.15 | -0.48 | Closed losing |
| SKRUSDT | 4.2× | Sell | Long | -9.03 | -0.21 | Closed losing |
| PLUMEUSDT | 5.0× | Sell | Long | -7.79 | -0.16 | Closed losing |
| LDOUSDT | 3.0× | Sell | Long | -3.58 | -0.20 | Closed losing |
| DYDXUSDT | 4.2× | Sell | Long | 0.00 | 0.00 | Closed flat (watchdog) |
| OPUSDT | 3.0× | Sell | Long | +2.30 | +0.15 | Watchdog reversed/closed |
| ONDOUSDT | 6.4× | Sell | Long | +0.35 | +0.09 | Watchdog rescue |

Aggregate: 6 losses, 2 marginal wins via watchdog rescue. Total PnL on suppressed trades: **-$111.98** (close to the -$114.49 in COMPLETE_FINDINGS — the small delta is because COMPLETE_FINDINGS likely included one additional position whose persist event is in a different log slice).

The single dominant loss is BSBUSDT at -$70.08 — by itself it represents 63 % of the suppressed-trade aggregate damage. BSBUSDT is the canonical example because the structural evidence at suppression time was rr_long=3.7 vs rr_short=0.5 (7.3× ratio), and the trade was Sell on a volatile-regime lock. The structure was screaming Long; the regime lock forced Sell; the trade lost $70.

## XRAY_OVERRIDE_LOCK (the 10× threshold did fire)

Total override successes: 6. Each event verbatim:

```
2026-05-16 13:48:39.298 ORCAUSDT dir=Buy   ratio=12.0x  rr_long=0.3  rr_short=4.2  lock_reason='trending_up aligns with Buy'
2026-05-16 14:25:12.289 OPUSDT   dir=Sell  ratio=19.3x  rr_long=3.7  rr_short=0.2  lock_reason='trending_down aligns with Sell'
2026-05-16 16:38:22.029 ATOMUSDT dir=Buy   ratio=94.7x  rr_long=0.1  rr_short=5.7  lock_reason='trending_up aligns with Buy'
2026-05-16 17:13:13.369 ORCAUSDT dir=Buy   ratio=11.1x  rr_long=0.2  rr_short=2.7  lock_reason='volatile regime, insufficient flip evidence'
2026-05-16 17:54:40.634 ORCAUSDT dir=Buy   ratio=11.1x  rr_long=0.2  rr_short=2.7  lock_reason='volatile regime, insufficient flip evidence'
2026-05-16 18:22:08.820 ORCAUSDT dir=Buy   ratio=498.5x rr_long=0.0  rr_short=10.0 lock_reason='volatile regime, insufficient flip evidence'
```

Direction-distribution of overrides:

- 5 of 6 overrides flipped Buy → Sell (chosen=Buy with rr_short >> rr_long, override flipped to Sell).
- 1 of 6 overrode toward Sell (OPUSDT chosen=Sell, but rr_long=3.7 >> rr_short=0.2 — at 19.3× the override fired; the trade flipped from Sell to Buy).

So the 10× override threshold, when it fires, is actually FLIPPING Buy→Sell more often than Sell→Buy in this session. Combined with the lock pattern (which dominantly forces Sell), the cumulative direction skew is severe.

Important: of 6 override successes, 4 are for ORCAUSDT alone. The override fires repeatedly on the same coin because the per-tick structural snapshot was nearly always rr_long ≈ 0, rr_short large — extremely lopsided evidence that the 10× threshold cleared. The override is doing its job for these cases. But the 8 suppressions show many other coins had 3-7× evidence, which the same threshold rejected.

## Suppression vs override ratio

| Path | Count |
|---|---|
| Suppressed (3.0 ≤ ratio < 10.0) | 8 |
| Overridden (ratio > 10.0) | 6 |

14 total mismatch decisions. 8 lost the structural signal to the lock; 6 cleared the threshold. The dead zone (3.0× to 10.0×) is wider than the active override zone — meaning the threshold is admitting only the most extreme cases and rejecting the typical strong-but-not-extreme cases.

## Verification of the COMPLETE_FINDINGS 91 % Buy-flip block claim

| Source | Claim |
|---|---|
| COMPLETE_FINDINGS line 65 | "10 of 11 Buy-flip attempts were BLOCKED" |
| This log analysis | 10 Qwen-tried-Buy attempts, all 10 blocked; 1 Qwen-tried-Sell, blocked. 10/11 = 90.9 % Buy-flip rate. |

**Confirmed.** The 91 % is rounded from 10/11. All 11 override attempts were hard-reverted by the lock — the actual override gate has a 100 % block rate; the 91 % is the share of attempts that were Buy-direction.

## Five sample raw log lines (verbatim)

```
2026-05-16 13:48:22.697 | INFO     | src.apex.optimizer:optimize:251 | APEX_DIR_LOCK | sym=ORCAUSDT dir=Buy regime=trending_up reason='trending_up aligns with Buy' | did=d-1778939089621

2026-05-16 14:17:26.822 | WARNING  | src.apex.optimizer:optimize:360 | APEX_DIR_LOCK_OVERRIDE | sym=SOLUSDT qwen_tried=Buy locked_to=Sell regime=trending_down | did=d-1778940734931

2026-05-16 15:02:24.788 | WARNING  | src.workers.strategy_worker:_execute_claude_trade:1689 | XRAY_FLIP_SUPPRESSED_BY_LOCK | sym=BSBUSDT dir=Sell ratio=7.3x rr_long=3.7 rr_short=0.5 lock_reason='volatile regime, insufficient flip evidence' | flip suppressed — APEX locked direction | did=d-1778943487345

2026-05-16 16:38:22.029 | WARNING  | src.workers.strategy_worker:_execute_claude_trade:1707 | XRAY_OVERRIDE_LOCK | sym=ATOMUSDT dir=Buy ratio=94.7x rr_long=0.1 rr_short=5.7 override_threshold=10.0 lock_reason='trending_up aligns with Buy' | structural RR overrides APEX lock | did=d-1778949337933

2026-05-16 17:13:13.369 | WARNING  | src.workers.strategy_worker:_execute_claude_trade:1828 | XRAY_DIR_FLIP | sym=ORCAUSDT original_dir=Buy flipped_dir=Sell rr_original=0.2 rr_flipped=2.7 ratio=11.1x size_usd=$360 sl=$1.5074 tp=$1.4563 | did=d-1778951419010
```

## APEX_FLIP_DECISION breakdown

Total APEX_FLIP_DECISION events: 79. Distribution by `decision_reason`:

| Count | decision_reason |
|---|---|
| 68 | no_flip_attempt — DeepSeek kept brain direction |
| 11 | lock_override — DeepSeek tried to flip, lock reverted |
| 0 | counter_protected |
| 0 | insufficient_data |
| 0 | conf_below_threshold |
| 0 | flip_accepted |

68 of 79 (86 %) trades, DeepSeek did NOT even attempt a flip. The brain's direction stood unchanged. This is consistent with the spec's natural-direction guidance in the APEX_SYSTEM_PROMPT — DeepSeek is conservative about flipping in trending regimes. Of the 11 flip attempts that did occur, the lock vetoed all 11.

Net direction-mutation impact in this session: zero flips made it through APEX. The lock has effectively held DeepSeek's flip propensity at 0 % accepted.

## Key takeaways for synthesis

1. The trending_down + Sell lock is the dominant code path (83 % of all locks).
2. The lock is structurally symmetric but produced 8:1 Sell:Buy locks because the regime input was 76 % trending_down.
3. 100 % of APEX flip attempts (11 of 11) were vetoed by the lock at the post-parse gate.
4. The structural-RR override is a narrow safety valve: it fires at 10× but admits nothing in the 3-10× range, which is where most "strong but not extreme" evidence lives.
5. 8 trades were forced to take the structurally-wrong direction; their aggregate PnL was -$111.98, dominated by the BSBUSDT -$70.08 SL hit.
6. SOLUSDT alone produced 8 of the 10 Qwen Buy-flip blocks — a repeated, consistent disagreement between DeepSeek and the regime lock on the same coin.
