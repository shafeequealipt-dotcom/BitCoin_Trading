# BETA 04 — BSBUSDT $70 Loss Decision Chain Reconstruction

This document is a full verbatim reconstruction of the BSBUSDT trade decided at 15:02 UTC on 2026-05-16 and closed at -1.40 % / -$70.08 USD via SL hit at 15:34. Every line in the chain is quoted exactly from `/home/inshadaliqbal786/ALL_LOGS_2026-05-16_13-40_to_18-30.log`. The trade is the canonical example of the R2+R3 failure mode.

## Setup at 15:02:04 — APEX run begins

```
2026-05-16 15:02:04.146 | INFO  | APEX_PRICE_SOURCE | sym=BSBUSDT source=ws price=0.3922
2026-05-16 15:02:04.246 | INFO  | VOL_PROFILE | sym=BSBUSDT class=medium atr_pct=0.35% regime=volatile | tp=1.65% sl=1.20% hold=27min strategy=breakout
2026-05-16 15:02:04.247 | INFO  | REGIME_CACHE_QUERY | sym=BSBUSDT reader=apex_assembler hit=True ready=True cache_size=49
2026-05-16 15:02:04.361 | INFO  | APEX_ASSEMBLE_DONE | sym=BSBUSDT populated=[ta,m4,ob,vol,xray,tias_sym] count=6/7
2026-05-16 15:02:04.361 | INFO  | APEX_TIER | tier=1 sym=BSBUSDT sym_trades=6 regime_trades=116 regime=volatile action=full_optimize
```

State at entry: BSBUSDT in VOLATILE regime per the per-coin volatility profile (NOT the global BTC regime). 6 prior trades in this symbol's history; 116 in the regime population. Tier 1 — sufficient data to run a full optimization.

## 15:02:04.361 — The lock fires

```
2026-05-16 15:02:04.361 | INFO  | src.apex.optimizer:optimize:251 | APEX_DIR_LOCK | sym=BSBUSDT dir=Sell regime=volatile reason='volatile regime, insufficient flip evidence' | did=d-1778943487345
```

Brain's directive was Sell. The lock fires under the volatile branch of `_check_direction_lock()` (optimizer.py:1302-1307). Reason: `_check_flip_evidence(trades, "Sell")` returned False because the symbol had fewer than 8 trades in the opposite direction (only 6 trades total) and/or fewer than 70 % WR for the opposite direction. The lock is now in effect: any Qwen flip will be vetoed.

## 15:02:22.565 — DeepSeek tried to flip, lock vetoed

```
2026-05-16 15:02:22.565 | WARNING | src.apex.optimizer:optimize:360 | APEX_DIR_LOCK_OVERRIDE | sym=BSBUSDT qwen_tried=Buy locked_to=Sell regime=volatile | did=d-1778943487345
```

DeepSeek returned `direction=Buy` despite the in-prompt instruction. The post-parse override gate at optimizer.py:359-371 hard-reverted to `claude_direction=Sell`. The lock has now silently negated DeepSeek's TIAS-aware decision.

## 15:02:22.565 — APEX_FLIP_DECISION confirms the lock-override path

```
2026-05-16 15:02:22.565 | INFO  | src.apex.optimizer:optimize:611 | APEX_FLIP_DECISION | sym=BSBUSDT brain_dir=Sell apex_dir=Sell flip_attempted=Y flip_accepted=N decision_reason=lock_override regime=volatile raw_conf=0.85 eff_conf=0.85 rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00 dir_locked=Y lock_reason='volatile regime, insufficient flip evidence' flip_dir_trades=-1 qwen_initial_dir=Buy
```

This is the unified decision log. Notable facts:
- `flip_attempted=Y flip_accepted=N`. DeepSeek did try.
- `raw_conf=0.85` — DeepSeek's confidence in the Buy flip was 0.85. This would have CLEARED the asymmetric `apex_min_flip_confidence_sell_to_buy = 0.70` threshold. But the asymmetric threshold never engaged because the lock vetoed the flip BEFORE the confidence gate ran (`_enforce_flip_confidence` is gated by `regime in ("trending_up","trending_down","volatile")` — exactly the regimes the lock dominates).
- `rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00` — the RR-boost path was never evaluated because the lock had already vetoed the flip. The structural-RR data was AVAILABLE at this point (it became visible at line 15:02:24.787 below) but the lock-veto pre-empted any structural consideration.
- `qwen_initial_dir=Buy` — recorded for post-hoc audit. DeepSeek's actual recommendation is preserved here.

## 15:02:22.566 — APEX_OK confirms the locked direction will be executed

```
2026-05-16 15:02:22.566 | INFO  | APEX_OK | sym=BSBUSDT dir=Sell sl=1.2% tp=2.1% cls=medium lev=5x sz=$14000→$1020 conf=85% regime=volatile ms=689
```

The optimized trade exits APEX with `dir=Sell, sl=1.2%, tp=2.1%, lev=5×, size=$1020`. The 85 % confidence is preserved on the OptimizedTrade — but it was a confidence in BUY, not Sell. Because the lock reverted the direction without re-running the optimization, the trade carries an 85 % confidence score that no longer matches the parameter direction.

## 15:02:24.712 — Conviction weight 2.0× signals proven Sell history

```
2026-05-16 15:02:24.712 | INFO  | src.apex.gate:_get_conviction_weight:784 | CONVICTION_WEIGHT | sym=BSBUSDT regime=volatile pf=4.41 won=$89.96 lost=$20.42 trades=6 weight=2.0x
```

The 6 prior BSBUSDT trades in volatile regime produced a profit factor of 4.41 ($89.96 won / $20.42 lost). The conviction-weight system reads this and assigns a 2.0× size multiplier — the maximum boost. But conviction is direction-agnostic — it does not distinguish Buy vs Sell history. The 4.41 PF is computed over BOTH-direction trades. Reading the conviction signal as "Sell is good for BSBUSDT" is an inference the system does not actually make; conviction weights size only, not direction.

## 15:02:24.787 — XRAY_DIR_MISMATCH reveals the structural truth

```
2026-05-16 15:02:24.787 | WARNING | src.workers.strategy_worker:_execute_claude_trade:1598 | XRAY_DIR_MISMATCH | sym=BSBUSDT dir=Sell rr_long=3.7 rr_short=0.5 | Claude chose Sell but LONG has better R:R | did=d-1778943487345
```

The structural placement says LONG has rr_long=3.7 vs rr_short=0.5. Ratio = 3.7 / 0.5 = 7.4× favoring Long. The chosen direction (Sell) has 0.5 R:R — structurally bad. The "better" direction has 3.7 R:R — structurally excellent.

## 15:02:24.787 — Lock precedence resolved: SUPPRESS

```
2026-05-16 15:02:24.787 | INFO  | src.workers.strategy_worker:_execute_claude_trade:1683 | XRAY_LOCK_PRECEDENCE_RESOLUTION | sym=BSBUSDT ratio=7.3x flip_threshold=3.0 override_threshold=10.0 action=suppress
```

The strategy_worker computes ratio=7.3× (`rr_opposite / rr_chosen = 3.7 / 0.5 = 7.4`, rounded to 7.3 in the emit). Ratio > 3.0 (would normally flip). Ratio < 10.0 (override threshold). Action = suppress. This is the dead-zone decision: structural evidence is strong but not extreme enough.

## 15:02:24.788 — Flip suppressed, locked direction stands

```
2026-05-16 15:02:24.788 | WARNING | src.workers.strategy_worker:_execute_claude_trade:1689 | XRAY_FLIP_SUPPRESSED_BY_LOCK | sym=BSBUSDT dir=Sell ratio=7.3x rr_long=3.7 rr_short=0.5 lock_reason='volatile regime, insufficient flip evidence' | flip suppressed — APEX locked direction
```

The flip is suppressed. The trade proceeds as Sell.

## 15:02:24.788 — SIZE_DERIVATION confirms size + confidence at submission

```
2026-05-16 15:02:24.788 | INFO  | src.core.sizing_orchestrator:log_size_derivation:131 | SIZE_DERIVATION | sym=BSBUSDT claude=$14000 apex=$1020 apex_opt=True gate_c0=$1020 gate_c4=$1020 enforcer_mult=1.00 enforcer_pre=$1000 final=$1000 lev=5x xray_conf=0.55 setup_score=83.0 expected_rr=3.38
```

`expected_rr=3.38` is the structurally-correct RR for the LONG direction (close to rr_long=3.7). The system recorded 3.38 expected RR alongside a Sell direction that has rr_short=0.5. The internal arithmetic is contradictory; the lock is the reason.

## 15:02:24.932 — DIRECTION_DECISION confirms the final result

```
2026-05-16 15:02:24.932 | INFO  | src.workers.strategy_worker:_execute_claude_trade:2314 | DIRECTION_DECISION | sym=BSBUSDT brain_dir=Sell final_dir=Sell flipped=N flip_source=none apex_locked=Y lock_reason='volatile regime, insufficient flip evidence' xray_ratio=0.0x reason=xray_flip_suppressed_by_lock analysis_dir=Buy analysis_score=+0.35 analysis_conf=0.58
```

Note `analysis_dir=Buy analysis_score=+0.35`. A separate scanner analysis also said Buy. This is the THIRD signal pointing to Long:
1. Qwen's direction (DeepSeek attempted Buy → blocked at 15:02:22).
2. XRAY structural R:R (3.7 vs 0.5 = 7.3× → blocked at 15:02:24).
3. Analysis layer (analysis_dir=Buy → noted at 15:02:24).

All three said Buy. The lock vetoed all three.

## 15:02:24.933 — BRAIN_VS_ANALYSIS_DISAGREEMENT logged

```
2026-05-16 15:02:24.933 | WARNING | BRAIN_VS_ANALYSIS_DISAGREEMENT | sym=BSBUSDT brain_dir=Sell analysis_dir=Buy analysis_score=+0.35 analysis_conf=0.58 flip_source=none final_dir=Sell
```

The system explicitly logged that brain and analysis disagree. The final direction was Sell. This is the observability that exists; the missing piece is the gate that does something about it.

## 15:02:25.272 — Order placed

```
2026-05-16 15:02:25.272 | INFO  | BYBIT_DEMO_WS_EXEC_NON_CLOSE | sym=BSBUSDT oid=4c30ebd4-782 side=Sell exec_price=0.3924 exec_qty=12742.0 exec_fee=5.49995688 closed_size=0.0 exec_type=Trade partial=N
```

Sell 12,742 BSBUSDT at $0.3924. $5,000 notional at 5× lev (≈ $1,000 margin).

## 15:02:25.618 — Thesis opened

```
2026-05-16 15:02:25.618 | INFO  | THESIS_OPEN | id=2593 sym=BSBUSDT dir=Sell ent=0.3924 sl=0.3971088 tp=0.3841596 target_pct=2.100 stop_pct=1.200 lev=5 size_usd=1000 max_hold_min=40 order_id=4c30ebd4-782f-4ea4-b765-6b40ca6f7be3
```

## 15:07 - 15:33 — PnL drift toward SL

The position tracked sideways for 5 minutes then drifted negative. Sample PLAN events:

```
15:07:32 PLAN: BSBUSDT Sell PnL=-0.10%
15:13:23 PLAN: BSBUSDT Sell PnL=-0.33%
15:17:35 PLAN: BSBUSDT Sell PnL=+0.23%   ← brief positive at minute 15
15:22:34 PLAN: BSBUSDT Sell PnL=+0.15%
15:27:38 PLAN: BSBUSDT Sell PnL=+0.20%   ← peak, but sniper held due to cooldown
15:33:01 PLAN: BSBUSDT Sell PnL=-0.38%
15:33:21 PLAN: BSBUSDT Sell PnL=-0.46%
```

The structure had said LONG was 7.3× better. The price moved UP (favoring Long, against the Sell position). The trade decayed from +0.23 % to -0.46 % over the 16-minute window after entry. The watchdog and sniper observed but did not close (profit_sniper attempted partial_close but was blocked by 60-second cooldown).

## 15:34:25 — SL hit

```
2026-05-16 15:34:25.749 | INFO  | src.core.thesis_manager:close_thesis:365 | Thesis closed: BSBUSDT PnL=-1.40% reason=bybit_sl_hit
2026-05-16 15:34:25.758 | INFO  | BD_TRADE_HISTORY_PERSIST_OK | tid=bd-4c30ebd4-782f-4ea4-b765-6b40ca6f7be3 sym=BSBUSDT pnl_usd=-70.0810 pnl_pct=-1.4016% qty=12742.0 side=Sell mode=bybit_demo
```

**Final loss: $70.08 (-1.40 %) via SL hit at 15:34:25.**

## Where the chain locked in the wrong direction

Six explicit decision points could each have prevented this loss. Each was governed by a check that did not consider the structural evidence:

1. **15:02:04.361 — APEX_DIR_LOCK fired**. The lock reason was `volatile regime, insufficient flip evidence`. The lock at this stage knew the regime was volatile and the symbol history had only 6 trades. It did NOT know that the LONG direction had rr=3.7 vs the Sell direction's rr=0.5. **Had the lock considered structural R:R, it would not have fired.**
2. **15:02:22.565 — APEX_DIR_LOCK_OVERRIDE blocked Qwen's Buy**. DeepSeek's flip was reverted. **Had DeepSeek's 0.85 confidence been allowed to enter the post-parse confidence gate (which would have applied the 0.70 sell_to_buy threshold and PASSED), the trade would have been Buy.**
3. **15:02:22.565 — APEX_FLIP_DECISION recorded `rr_chosen=0.00 rr_flipped=0.00`**. The structural data was available in the assembled package but was not surfaced to the lock decision. **Had the structural-RR informed the lock, it would have prevented Sell.**
4. **15:02:24.712 — CONVICTION_WEIGHT was 2.0×**. High conviction signal, but direction-agnostic. **Had conviction been per-direction-aware, the system would have seen that the proven-winner pattern was on the OTHER side.**
5. **15:02:24.787 — XRAY_FLIP_SUPPRESSED_BY_LOCK at ratio 7.3×**. The override threshold of 10.0 vetoed the flip. **Had the threshold been 5.0× (R3 Option B) or 3.0× (R3 Option A) or asymmetric in favor of Sell→Buy (R3 Option C), the trade would have been Buy.**
6. **15:02:24.933 — BRAIN_VS_ANALYSIS_DISAGREEMENT was logged**. The system explicitly noted the disagreement. **Had the system acted on the disagreement (e.g., abort the trade when 3 of 3 corroborating signals disagree with the brain), the trade would have been skipped or flipped.**

## Which fix would have prevented this trade

Each candidate fix evaluated against the BSBUSDT chain:

| Fix | Would have prevented BSBUSDT loss? |
|---|---|
| R2 Option A (lock fires only when regime confidence > X%) | NO — volatile regime had high confidence on May 16 |
| R2 Option B (lock fires only when XRAY ratio supports same direction) | **YES** — the 7.3× ratio favored Long, so the lock would NOT have fired |
| R2 Option C (lock fires only when conviction history supports same direction) | PARTIAL — conviction is direction-agnostic in code; would need direction-aware reconception |
| R2 Option D (lock becomes advisory; Qwen can override at threshold) | **YES** — DeepSeek's 0.85 confidence would have stood |
| R2 Option E (combine A+B+C+D) | YES |
| R3 Option A (lower 10× to 3×) | **YES** — 7.3× > 3.0× clears the new threshold |
| R3 Option B (lower 10× to 5×) | **YES** — 7.3× > 5.0× clears the new threshold |
| R3 Option C (asymmetric: lower for Buy override) | **YES** — the trade was a flip from Sell to Buy, lower threshold would apply |
| R3 Option D (conviction-aware: lower when XRAY conviction high) | YES — xray_conf was 0.55 (medium); a threshold proportional to conviction would have admitted this |
| R3 Option E (aim-bias evidence aware) | **YES** — Buys are 55.6 % WR, threshold for Buy override should be lower |

**Strong candidates for preventing the BSBUSDT loss: R2 Option B, R2 Option D, R3 Option A or B (alone or combined). R3 alone is sufficient — lowering the threshold from 10× to anything ≤ 7× would have admitted this trade.**

## Why R2 alone is not sufficient and R3 alone is not sufficient

Even if R2 fixed the lock so that BSBUSDT did not see the lock fire, the resulting trade direction would still depend on which downstream path acted. If APEX returns Qwen's Buy (because no lock), then BSBUSDT entered as Buy and the structural signal aligns. **Both fixes (R2 + R3) approach the same problem from opposite sides** — R2 prevents the lock from firing when structure disagrees; R3 allows the override to fire at lower ratios. Either path alone would have prevented BSBUSDT. The combined fix provides redundancy.

## Summary for synthesis

- The lock fired at the volatile branch with no awareness of structural R:R.
- DeepSeek correctly identified Buy as the right direction at 0.85 confidence — the lock vetoed.
- The structural override threshold was 10×; the actual ratio was 7.3× — three points inside the dead zone.
- The trade entered Sell, traveled briefly into profit, then lost via SL hit for -$70.08.
- The fix that most directly prevents this trade is R3 (lower threshold) since the structural evidence at 7.3× was sufficient — only the threshold setting was wrong.
- The fix that most cleanly addresses the root cause is R2 Option B (lock consults structure) — the underlying issue is that the lock did not consider the obviously-available structural data.
