# Phase 3 — Claim 4 Verification: SIG_CLASSIFY Label Calibration

## The Claim

> The strong_buy signal label is mis-calibrated. strong_buy claimed to lose 55%, plain buy 44% — the highest-confidence label performing worse than the lower one, yet driving larger size.

## Method

`SIG_CLASSIFY` label (`strong_buy / buy / neutral / sell / strong_sell`) is produced by `signal_generator._evaluate_signal()` (signal_generator.py:393-545; see Phase 1 for the formula). The label is logged via the `SIG_CLASSIFY` event AND persisted to `signals` table.

**Discovery during verification**: in the recent 2026-05-20+ window, the `signals` DB table contains ONLY `buy` and `neutral` rows (`sell`, `strong_buy`, `strong_sell` rows are missing). Older periods do contain the full label set. The log file `SIG_CLASSIFY` events show the full label distribution in the window:

```
1522 type=strong_buy
2219 type=buy
 359 type=neutral
```

Cause of the DB gap is **out of scope for this Phase 3 task**; documented as a data-integrity finding for the Phase 8 synthesis.

For verification, the latest `SIG_CLASSIFY` log line for the symbol before the trade's open was attached. Window: 2026-05-20 05:46 → 2026-05-21 12:40. DB trades: 225. With SIG label attached: 221.

## Result — Label × Outcome

| label | n | L | W | loss% | net USD |
|---|---|---|---|---|---|
| **strong_buy** | 113 | 62 | 51 | **54.9%** | **+$198.54** |
| buy | 90 | 40 | 50 | **44.4%** | **+$3.60** |
| neutral | 18 | 10 | 8 | 55.6% | −$27.32 |

## Verification Result

| Sub-claim | Prior analysis | DB+log-verified | Verdict |
|---|---|---|---|
| `strong_buy` loss% | 55.2% | **54.9%** | **Reproduced** |
| `buy` loss% | 43.8% | **44.4%** | **Reproduced** |
| `strong_buy` loses more than `buy` | yes | YES | **Reproduced** |
| `strong_buy` net | +$180.84 | **+$198.54** | **Reproduced** (more positive) |
| `buy` net | −$3.49 | **+$3.60** | **Slight flip** (basically zero) |

The headline holds: **the `strong_buy` label has HIGHER loss rate than the `buy` label** (54.9% vs 44.4%, a 10.5-percentage-point gap), confirming mis-calibration of the threshold (`strong_threshold=0.55`, `buy_threshold=0.18`, signal_generator.py:515-524 / settings.py:3037-3038).

But the **NET PnL** picture is the opposite of the implied prior-analysis story:
- `strong_buy` is net +$198.54 — STRONGLY POSITIVE despite the high loss rate. The 51 wins were big enough to offset the 62 losses.
- `buy` is net +$3.60 — essentially breakeven. The lower loss rate did NOT translate into more money made; the wins were smaller.

So the mis-calibration is more subtle than "strong_buy is a worse signal." It is: **strong_buy fires on bigger-move setups (which lose more often but win bigger), and `buy` fires on slower-grind setups (which win more often but produce smaller wins).**

## Cross-tab: Label × Side

| label | side | n | L | W | loss% | net USD |
|---|---|---|---|---|---|---|
| strong_buy | Sell | 57 | 30 | 27 | 52.6% | +$79.54 |
| strong_buy | Buy | 56 | 32 | 24 | **57.1%** | +$119.00 |
| buy | Sell | 48 | 17 | 31 | **35.4%** | +$24.14 |
| buy | Buy | 42 | 23 | 19 | 54.8% | **−$20.54** |
| neutral | Sell | 15 | 8 | 7 | 53.3% | −$20.79 |
| neutral | Buy | 3 | 2 | 1 | 66.7% | −$6.53 |

**The worst cell on net basis: `buy + Buy` (-$20.54)** — a "weakly bullish" SIG label combined with a Buy side decision is the lowest-edge trade type.

**The best cell on per-trade basis: `buy + Sell` (+$24.14 / 48 trades, $0.50 mean, 35% loss rate)** — when the SIG label is weak-buy but the strategist takes a SELL, the trade tends to work. This suggests the strategist is correctly reading the over-bullishness as a SELL opportunity (fade signal).

**The biggest-magnitude winners come from `strong_buy + Buy` (+$119 net on 56 trades, 57% loss rate)** — the strategist agreeing with a STRONG bull signal produces volatile outcomes: most trades lose but a few win big.

## Signal_generator BUY-bias confirmation

The `signal_generator.multi_source.buy_threshold = 0.18` (settings.py:3034-3038) is asymmetrically lower than `strong_threshold = 0.55`. The label-mapping logic at signal_generator.py:515-524:
- `direction_score >= 0.55` → STRONG_BUY
- `direction_score >= 0.18` → BUY
- `direction_score <= -0.55` → STRONG_SELL
- `direction_score <= -0.18` → SELL

The comment in settings.py:3034-3038 confirms: "buy_threshold 0.25 → 0.18 to match the typical BUY-leaning direction_score observed in forensic data." This is an asymmetric calibration: the BUY label fires at a less-strict positive direction_score than SELL would require negative direction_score.

In production, the LOG counts for the window are:
- 1522 strong_buy, 2219 buy, 359 neutral
- (sell / strong_sell missing from window — likely because direction_score is rarely <= −0.18 in this window, OR because the DB persistence is bugged as noted above)

The classifier label distribution is **heavily skewed toward BUY**. This is structural in the classifier itself, not in the trades — the strategist still produces 122 Sell trades vs 103 Buy in the same window, meaning the strategist is OVERRIDING the BUY-skewed signal label.

## Status

Claim 4 reproduced for loss-rate ranking. Refined: `strong_buy` is mis-calibrated against the `buy` label on loss rate but produces net-positive PnL via larger per-win magnitude. The asymmetric BUY-threshold (0.18 vs 0.55) is a hardcoded structural skew in the classifier. The data persistence gap (recent rows missing `sell`/`strong_*` labels in the DB) is a separate finding for the Phase 8 synthesis.
