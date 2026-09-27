# Trading Overhaul Plan

Written 2026-09-27. Every number below comes from the three scripts in
`scripts/research/` run against the live databases on the new server
(152.67.162.0). Run them from the repo root to reproduce any figure.

---

## 1. The bottom line

**The bot loses because its trade direction has no predictive edge. It is
statistically indistinguishable from a coin flip.**

This was proven by replaying all 1,090 trades minute by minute against 11.4
million stored one-minute candles (232 coins, July 9 to today).

Everything fixed over the last two months was real, but it was all machinery
*around* a core that does not predict. That includes the entry gates, the R:R
rule, the trailing stop, the exchange-tradeability fix, the freeze fix and the
server move. Better machinery around a coin flip cannot produce profit. That
is why each fix exposed the next problem without the results improving.

"Trading like a Wall Street desk" does not mean adding more indicators or a
smarter prompt. It means one discipline: **never trade a signal until it has
proven, measured edge after costs, on data it was not tuned on.** This plan
builds that process, then uses it to find a real edge, or to prove there
isn't one before more time is spent.

**What this plan does not promise:** profit. No honest plan can.
**What it does promise:** every go/no-go decision is made by numbers agreed in
advance, and the answer comes in weeks of research instead of months of live
trial and error.

---

## 2. The flops, with evidence

### Entry quality: the root cause

**F1. The direction is a coin flip.**
- 30 minutes after entry, price had moved the chosen way 46.1% of the time.
- Average move was −0.063%, with a t-statistic of −1.04 (not significant).
- The "edge ratio" is how far price moved in the trade's favour versus against it.
  It was 0.93 to 1.05 at every horizon from 5 minutes to 4 hours.
- Random entries score about 1.0. A usable entry signal should be clearly above
  1.0, around 1.15 or more.

**F2. The brain's confidence carries no information.**
- Its correlation with being right is −0.006.
- The top 20% most-confident trades were right 47.6% of the time. The
  bottom 20% were right 50.3%.
- All other recorded entry features also show correlations within ±0.04: ADX,
  ATR, volume ratio, RSI, fear and greed, ensemble votes for and against,
  leverage and size.

**F3. Choosing the direction added nothing over "always go long at those
moments."**
- At 30 minutes, the bot's own direction averaged −0.063%. Going long at the
  same moments averaged −0.059%.
- Those moments were themselves slightly bad to buy: price dipped after entry
  55% of the time, even though BTC rose 34% over the period.
- A likely reason: the universe selector ranks coins by volatility and volume
  surge, so the bot tends to arrive just after a spike, and short-term
  reversal follows.
- This is statistically weak. It is a research lead (B5 below), not a fix.

**F4. No exit rule rescues these entries.**
- Replaying the same entries through 40 simple stop, target and hold
  combinations gave **zero profitable combinations**.
- That holds even at limit-order costs, and in both halves of the data.

**F5. The bot loses before fees.**
- Gross P&L: −$419 over 1,090 trades. Fees then added −$643, for −$741 net.
- Costs make it worse, but they are not the root cause.
- *Correction:* earlier notes said fees exceed a small positive edge
  (+0.094%). That was one good two-week window. Across all trades, gross is
  negative.

### Structure: why good fixes did not move the result

**F6. The timeframe is too short for the costs.**
- The average trade moved 0.48%. A round trip costs 0.17% (0.11% fees plus
  0.06% slippage).
- So costs eat about 35% of a typical move. With multi-hour or multi-day holds
  targeting moves of 3% or more, the same cost is about 5%.

**F7. The intervention exits look guilty but are not.**
- The 182 "manual" closes (the bot's own exits) won 4.4% of the time and
  account for −$492.
- But replaying them to their original stop or target gives −0.466% instead of
  the actual −0.478%. Holding would have hit the stop 63 times and the target
  only 11 times.
- They close trades that were already losing. They add complexity, not value.

**F8. Trading volume collapsed with no gain in quality.**
- Trades fell from 751 in July to 321 in August to 18 in September.
- The edge ratio after August 15 was 0.57. That sample is small (31 trades),
  but it is certainly not better.
- Filtering a random signal gives fewer random trades.

**F9. There is a long bias.**
- 62% of trades were longs.
- Longs were right 43.2% of the time; shorts 51.0%.

**F10. Leverage is set by the brain's "conviction", but conviction means
nothing (F2).**
- Higher leverage produced bigger losses per trade.
- Leverage has no relationship to being right (correlation +0.026).

**F11. Exits keep 37% of the in-trade peak on winners.**
- 17% of losers were up at least 0.34% before closing red.
- This is real, but secondary to F4.

**F12. Only 12 trades ever reached their take-profit, and those 12 made
+$115.** Large moves exist; this system rarely holds long enough to catch
them.

**F13. The architecture is built from patches.**
- There are about 8 overlapping exit systems, 6 entry gates, and size and
  leverage chosen by the LLM.
- Every one of them was added to patch a symptom.
- This is the direct cause of the test, fix, find-the-next-bug cycle.

### Tooling

**F14. There is no backtester.** Every idea has been tested live on paper, so
each answer took weeks.
- The paper exchange uses fixed fees and slippage, and supports market orders
  only. It cannot test cheaper limit-order entries.
- The `entry_score` field is empty on every trade.

**F15. Infrastructure is mostly fixed.**
- Fixed: the freeze guard (commit `13e5078`), the new server, and the backup
  script.
- Still open: the AI provider is a free tier (four have died so far), and the
  Bybit demo API key is invalid.

---

## 3. What a professional desk does differently

1. **Edge first.** Nothing trades until it shows, on data it wasn't tuned on:
   positive expectancy after costs, statistical significance, and a clear win
   over both a random baseline and a mirror (opposite-direction) baseline.
2. **Research is separate from production.** Ideas are tested on history in
   hours, not on live paper over weeks.
3. **Risk is sized, not guessed.** Each trade risks a fixed fraction of
   equity. There are portfolio-wide exposure caps, correlated coins count as
   one bet, and drawdown circuit breakers.
4. **Execution is a cost to minimise.** Use limit orders and measure slippage.
5. **Human-style judgment is an overlay, not the engine.** The LLM vetoes
   trades around news and events and reviews trades. It does not pick
   direction or size.
6. **Promotion rules are written before the test starts, with kill
   switches.** No tinkering mid-test.

---

## 4. The plan

### Phase A: research harness (first, about 1 week)

**Build:**
- A backtester over the data we already have: 1-minute candles, funding rates
  and open-interest history, all in `shadow.db`.
- It must have no look-ahead, use conservative fills, and model costs at 0.17%
  (market orders) and 0.04% (limit orders).
- It must report, for every strategy, automatically: expectancy per trade,
  profit factor, win rate, payoff, Sharpe, max drawdown, edge ratio,
  t-statistic, and random and mirror baselines.
- Walk-forward testing: tune on earlier data, test on later data, report both.

**Backfill 1–2 years of history** for the universe (candles, funding, open
interest) from Bybit's free public API. We currently have 80 days, almost all
of it one market regime (BTC +34%). That is too little to trust anything.

**Exit criteria:** fed the live trades' real entries, the harness reproduces
the live ledger's results within a small tolerance. If it can't reproduce
reality, nothing it says can be trusted.

### Phase B: signal research (about 1 week)

Every candidate below is a documented, repeatable effect in crypto perpetual
futures, and we have the data to test each one. All of them are judged by the
same bar.

- **B1. Trend and breakout on 4-hour to daily timeframes.** Targets moves of
  several percent, so costs become small (F6).
- **B2. Cross-sectional momentum or reversal.** Rank the universe; go long the
  strongest and short the weakest. This is market-neutral, so it removes BTC
  direction risk (F9).
- **B3. Funding-rate extremes.** Crowded positioning: fade it, or collect the
  carry.
- **B4. Open-interest shocks.** An OI spike plus a sharp move often means a
  liquidation cascade, followed by a reversal.
- **B5. Fade the surge.** The lead from F3: short-term reversal after the exact
  volatility and volume spikes the current scanner selects.
- **B6. The current LLM brain, judged by the same bar.** Its 21,151 logged
  decisions (`claude_decisions`) can be replayed. It stays only if it passes
  like any other signal.

**Promotion bar, fixed now before any results are seen.** All of these must
hold:
- Net expectancy after 0.17% costs is above 0, on data not used for tuning.
- t-statistic above 2.
- Edge ratio of 1.15 or higher.
- Beats both the random and mirror baselines.
- Positive in at least 2 of 3 time folds.
- At least 100 out-of-sample trades.

A candidate that fails is discarded. It is not re-tuned until it passes;
re-tuning to pass is exactly how backtests lie.

**Honest expectation:** most candidates will fail. That is fine. Failing
costs an hour in research, not a month live.

### Phase C: risk and portfolio engine (in parallel with B)

- **Sizing.** Risk a fixed fraction of equity per trade, for example 0.25–0.5%.
  Position size equals that risk divided by the stop distance. Leverage becomes
  an output of sizing, never an input (F10).
- **Exposure caps.**
  - Total open risk stays at or below 2–3%.
  - Net market direction (BTC exposure) is capped.
  - Highly correlated coins count as one bet.
- **Drawdown breakers.** Daily and weekly loss limits halt new entries.
- **Per-strategy kill switch.** A strategy stops automatically if its live
  results fall a set number of standard deviations below its backtest.

### Phase D: execution upgrade

- Add limit-order (post-only) support and realistic fill simulation to the
  paper exchange. It currently supports market orders only, at fixed costs.
- Model slippage from the bid/ask spread and order size.
- Measure fill rate and adverse selection before assuming any limit-order
  savings.

### Phase E: simplify production

- Each promoted strategy carries its own entry rule and **one** exit policy
  (stop, target, trail or time limit), defined in research.
- Retire the overlapping exit systems and gates that don't earn their keep
  (F7, F13). Do it behind flags, and only after the new path is proven.
- The LLM becomes an overlay: news and event veto, trade journal, daily
  review.

### Phase F: promotion pipeline

Research pass, then paper trading on the shadow exchange for a pre-set number
of trades, with pass/fail criteria written before it starts. Anything beyond
paper is the operator's decision alone. Every stage has a written stop rule.

---

## 5. Timeline and decision points

| When | What | Decision |
|---|---|---|
| Week 1 | Phase A: harness and data backfill | Harness reproduces live results, or it is fixed before anything else |
| Week 2 | Phase B: test B1–B6 | Does anything pass the pre-set bar? |
| Week 3 | Phases C and D (in parallel) | Risk engine and limit orders proven on paper |
| Weeks 4–7 | Paper-trade whatever passed, untouched | Pass/fail on the pre-set numbers |

**If nothing passes Phase B**, the honest recommendation will be to stop
trading this system. That would be a real result, reached in about two weeks
instead of more months.

---

## 6. The current bot meanwhile

**Recommended:** leave it running unchanged on paper as a control, and stop
tuning it. Its results have already told us what they can. More tuning is
exactly the loop this plan exists to end.

---

## 7. Decisions needed from the operator

1. **Approve the research-first approach**, knowing it does not promise
   profit.
2. **AI provider.** With the brain demoted to an overlay, the free tier may be
   enough.
3. **Bybit demo API key.** It is invalid and needs regenerating on your side.
4. **Real money.** Any move beyond paper trading is your decision, made on
   the numbers.
