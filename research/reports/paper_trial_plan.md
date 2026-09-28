# B1v2 live paper-trading trial — plan, written before the engine starts

Written 2026-09-28, before `research/paper_trade/engine.py` is started for
the first time. Per the same discipline as the rest of this process: the
criteria below are fixed now and are not to be loosened after seeing early
results.

## What is being tested

`research/strategies/b1v2_trend_breakout_trailing.py` — the only strategy
to pass the Phase B research bar (see `research/reports/round2_results_2026_09_28.md`):
OOS n=51,965, expectancy +0.1136%/trade at real market cost, t=2.83, edge
ratio 1.531, positive in all 3 backtest folds.

This trial checks whether that backtested behavior holds up when the exact
same signal and exit code runs against live, streaming data instead of
history — the reason this step exists at all: a backtest can be internally
correct and still fail live for reasons a backtest cannot see (data
snapshot artifacts, latency, a regime the 2-year window didn't cover).

## Setup

- Own paper wallet, starting at $10,000 (matches the live bot's own
  original starting balance, for an easy side-by-side read).
- Own database (`research/paper_trade/data/ledger.db`), own systemd unit
  (`trading-b1v2-paper.service`), own logs. Never touches shadow.db or the
  current bot's equity in any way.
- Same cost model the backtest passed under: 0.17% round-trip (market
  order), applied to every simulated fill.
- Risk limits (`research/engine/sizing.py::RiskLimits`, defaults, fixed
  before starting): 0.5% of equity risked per trade, max 3% total open
  risk, max 10 concurrent positions, 5% daily loss limit (pauses new
  entries for the rest of the day), 15% total drawdown limit (halts new
  entries entirely — requires the operator to resume).
- Signal and exit logic are the EXACT SAME Python functions the backtest
  used (`generate_signals()`, `research.engine.trailing_logic`) — not a
  reimplementation.

## Because live trade frequency is unknown in advance

The 2-year backtest saw ~118K raw signal-bar events across 172 symbols;
live, only one position per symbol is ever held and only the freshest
signal is acted on, so the real cadence of NEW trades opening could be
anywhere from a few per day to a few per week. Rather than one fixed end
date, this trial is assessed at checkpoints, once enough trades exist to
say anything:

- **Minimum sample for any verdict: 30 closed trades.** Below that,
  "status" reports are descriptive only — no pass/fail language.
- **Checkpoints: 2 weeks, 4 weeks, 8 weeks** from first trade (not from
  engine start, since the first trade may take days to appear).

## Pass / fail, at each checkpoint once the 30-trade minimum is met

- **Continue unchanged** if: net expectancy per trade is positive, and
  drawdown has stayed under the 15% automatic halt.
- **Flag for review** (not an automatic stop) if: net expectancy is
  negative but drawdown is still within limits — small samples are noisy;
  a negative early read alone does not override 51,965 backtested trades,
  but is reported plainly rather than hidden.
- **Automatic halt** (built into the engine, not a manual step): total
  drawdown hits 15% from peak equity. New entries stop; open positions
  still manage themselves to their existing stops. Requires the operator
  to resume.
- **Recommend stopping** if, at ANY checkpoint with the 30-trade minimum
  met: net expectancy is negative AND worse than what a same-timing mirror
  (opposite direction) or random-entry baseline would have scored over the
  same live window — the same "is this a real edge or not" test used
  throughout Phase B, now applied to the live result instead of history.

## What this trial is not

Not a decision about real money. Every prior stage of this plan has been
paper only; this stays paper only. A pass here is the condition for
raising that question with the operator explicitly — it does not answer
it by itself.
