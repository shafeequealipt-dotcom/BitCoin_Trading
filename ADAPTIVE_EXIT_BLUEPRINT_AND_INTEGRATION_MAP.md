# The Dynamic Adaptive Exit System — Complete Blueprint And Integration Map

A single reference describing how a per-trade, volatility-adaptive exit system should work and exactly how it plugs into the existing exit stack. This is a design blueprint, not an implementation instruction. It is written to be handed to the build program (the three-phase adaptive-exit prompt) as the concrete design Phase 3 should follow, and to be read by the operator as the full picture in one place.

This document uses heading structure and prose only, for screen-reader access. No emoji, no tables, no decorative separators.

## Part 1 — The problem this design solves, in one paragraph

Every threshold in the current exit stack is a fixed, hardcoded percentage applied identically to every coin: the ladder arms at 0.2 percent, its first rung is 0.6 percent, the trail minimum distance is 0.3 percent, the take-profit is 2.25 to 6 percent, the hard stop is minus 3 percent. But the coins move by wildly different amounts, and the trades the entries produce peak at a median of about 0.23 percent. So a 0.6 percent rung is unreachable on a quiet coin and trivial on a volatile one. The proven result is that about 97.6 percent of trades die in the dead band between the arm and the first rung, the trail never wins because it floors at breakeven on sub-0.3-percent moves, and the system clips every winner to a breakeven sliver. The cure is to stop using fixed percentages and instead derive every threshold from how much each coin actually moves, per trade, live.

## Part 2 — The core concept: one movement unit, R, drives everything

The entire design rests on a single number, computed per coin: how much this coin typically moves in a short window, expressed as a percentage of price. This is the coin's Average True Range as a percentage, and this document calls it the movement unit, R.

Every exit threshold becomes a multiple of R rather than a fixed percentage. The thresholds stop being "0.2 percent, 0.6 percent, 0.3 percent" and become "0.5R, 1.5R, 0.8R." The actual percentages then fall out of whatever R is for that coin at that moment.

A worked illustration. On a quiet coin where R equals 0.15 percent: the arm at 0.5R is 0.075 percent, the first rung at 1.5R is 0.225 percent, the trail at 0.8R is 0.12 percent. On a volatile coin where R equals 0.8 percent: the arm at 0.5R is 0.4 percent, the first rung at 1.5R is 1.2 percent, the trail at 0.8R is 0.64 percent. The same multipliers produce completely different percentages because R is different. The dead band disappears, because the first rung at 1.5R is reachable by construction: R is the scale of what the coin actually does, so a rung defined as a multiple of R is always in range.

This is the heart of the design. Everything else is refinement on this one idea.

## Part 3 — The fee floor: a hard ground beneath every profit threshold

R alone is not enough, because of fees. If R is very small, then a small R-multiple could sit below the round-trip trading cost, and the system would "lock profit" that fees erase. So every profit threshold is the larger of two things: the R-multiple, and a fee floor.

The fee floor is the trade's round-trip cost (about 0.11 percent taker fee each way, plus any slippage) times a small safety buffer. So the arm, for example, is the maximum of 0.5R and the fee floor. On a volatile coin, R dominates and the fee floor is irrelevant. On a very quiet coin, the fee floor dominates.

This produces a crucial side benefit. When a coin's R is so small that the fee floor is larger than the coin's typical move, the system is telling the operator that this coin cannot be traded profitably: its normal movement does not clear costs. The geometry makes that visible rather than hiding it. This is the bridge to the larger strategic question of which coins are worth trading at all: the adaptive exit not only captures the green on tradeable coins, it surfaces which coins are not worth the entry.

## Part 4 — How a single trade's geometry is built, step by step

This is the mechanism that runs for each open position, the moment it opens and continuously as it lives.

Step one, measure R. Pull the coin's current Average True Range as a percentage from the existing volatility profiler. This is the trade's movement unit.

Step two, compute the fee floor. Take this trade's round-trip cost and apply the safety buffer.

Step three, lay down the geometry as R-multiples, each floored at the fee floor where it is a profit threshold. The arm is the maximum of 0.5R and the fee floor. The ladder rungs are at 1.5R, 3R, and 5R. The trail distance is 1R behind the running peak, so it sits inside the move rather than flooring at breakeven. The take-profit is a reachable R-multiple the coin can actually attain, for example 4R to 6R, rather than a flat large percentage. The hard stop is a wide R-multiple, for example 8R to 10R, rather than a hardcoded minus 3 percent. These specific multiples are starting points to be tuned on the replay, not final values.

Step four, clamp to floors and ceilings. Each value is bounded by a hard minimum and maximum, so that an ATR spike cannot make the stop absurdly wide and an ATR collapse cannot make it absurdly tight. The bounds keep the geometry sane even when R behaves unusually.

Step five, recompute as the trade lives. Every tick, or every few seconds, R is updated. If the coin wakes up and starts moving, R grows and the rungs and trail widen with it. If it goes quiet, they tighten. The geometry breathes with the coin. R should be smoothed with a short moving average so it does not jitter tick to tick.

Step six, hand the values to the owner. The owner hierarchy decides who is writing the stop at this moment (the green owner when the trade is in profit, the red owner when it is underwater, the Head for catastrophe). This adaptive layer simply hands the owning engine the numbers to use. The separation is clean: the hierarchy decides who, the adaptive layer decides what.

## Part 5 — Staged capture: what makes this intelligent rather than just scaled

A scaled lock that still clips is not enough. The ladder should capture in stages relative to R, and it should reflect the trade's progress. As the high-water profit climbs:

When the trade reaches 1.5R, lock a little — move the stop to break-even-plus, so the trade becomes a free roll that can no longer lose. When it reaches 3R, lock a real chunk — move the stop to roughly 1.5R of secured profit. When it reaches 5R, lock most of the gain and let the remainder run on the 1R trail behind the peak.

Because these are R-multiples, a quiet coin hitting its own 1.5R receives the same staged treatment, proportionally, as a volatile coin hitting its 1.5R. The trail at 1R behind the peak means that once a trade is running, it keeps a stop one movement-unit back: close enough to capture the gain, far enough not to be tapped by the coin's normal noise. This is precisely the behavior that is broken today, where the trail floors at breakeven and never wins; with the trail at 1R it sits inside the move and trails a real peak.

## Part 6 — How the two special cases are handled naturally

The dead drifter. A trade that has lived a long time and never reached even 1R of movement is going nowhere relative to its own scale. The system recognizes this and triggers a scratch-exit: if the trade is above the fee floor, take the small net gain; otherwise close it flat. The drifter stops tying up capital for the rest of its deadline. This is the gap that currently lets a flat trade ride forty or more minutes.

The recovered fighter. A trade that went red and then climbs back toward green is handled because the geometry is always live and always R-relative. As the trade recovers and clears the fee floor, the arm engages and the trail at 1R captures the bounce at that coin's scale, rather than handing it back. The recovery is not a special hardcoded rule; it falls out of the geometry being continuously recomputed in R terms.

## Part 7 — The integration map: how this plugs into each existing system

This is the critical part. The adaptive layer does not replace any existing system. It changes what numbers they compute with. Here is how it touches each.

### The existing stack, organized under the owner hierarchy

The exit stack is roughly fourteen interacting systems, now organized under the owner hierarchy that was recently built and is enforcing. The Head is the catastrophic per-trade cap plus the force-close twins (spike-force and cap-force), always on and only tightening. The Green Owner, the profit-fetching system, comprises the stepped ladder, the Chandelier trail, the score-action engine, the profit guards, and the graduation latch. The Red Owner, the loss-cutting system, comprises the five-model time-decay engine, the force-close gate stack, the stall valve, the structure stop, the recovery logic, and the initial ATR stop. The Advisory systems are brain-tighten, the watchdog trails, and the sentinel and deadline tiers. The enforcer is the stop-loss gateway with its four rules and the owner switch.

### Integration with the Green Owner (profit-fetching)

The ladder is the largest change. Today its computation reads hardcoded config values: arm 0.2 percent, first rung 0.6 percent, lock 0.05 percent. The adaptive layer replaces those reads with R-multiples: the arm becomes the maximum of 0.5R and the fee floor, the rungs become 1.5R, 3R, and 5R. The ladder's logic, which locks a floor behind each crossed rung, is unchanged; it simply receives reachable rungs instead of unreachable ones. The same function runs, fed live numbers.

The Chandelier trail is the fix that makes it finally work. Today it floors at breakeven because its 0.3 percent minimum distance is wider than the entire move. The adaptive layer sets the trail distance to 1R and, critically, lowers the trail minimum-distance floor so that 1R on a quiet coin, for example 0.12 percent, is actually allowed rather than being clamped back up to 0.3 percent. With the distance at 1R and the floor lowered, the trail sits inside the move and can finally win the highest-stop-wins selection against the ladder. The trail's math is unchanged; its distance input and its minimum floor become R-derived.

The graduation latch is a small but important change. Today it latches profit ownership at the hardcoded 0.2 percent arm. It should latch at the adaptive arm, the maximum of 0.5R and the fee floor, so a volatile coin graduates later and a quiet coin earlier, each at its own scale.

The score-action engine and the profit guards are left alone in their logic. They are behavioral, not move-size thresholds. They continue to function as they do; they simply act on a trade whose stop geometry is now R-based.

### Integration with the Red Owner (loss-cutting)

The initial ATR stop is already volatility-aware: it is three times ATR. This is, in fact, the model for what the whole design does. The adaptive layer simply ensures it uses the same R that drives the rest of the geometry, for consistency.

The hard stop is currently a hardcoded minus 3 percent literal in the watchdog. It becomes a wide R-multiple, for example 8R to 10R, so it is wide enough for a volatile coin and not absurdly loose for a quiet one, while still sitting below the catastrophic cap.

The five-model time-decay engine and its force-close gate stack require care. These systems mostly work; they cut genuine losers correctly. The adaptive layer touches only their distance thresholds, the allowed-loss room, which is already partly ATR-scaled. It leaves the gate logic alone: the age gate, the MAE-to-stop-ratio gate, the monotonic-grind cut, the structural-invalidation gate, and the win-probability model are behavioral protections, not move-size thresholds, and scaling them would break working logic. The rule is to make the geometry R-based and leave the behavioral gates untouched.

The development guard and the dead-drifter logic. The development guard currently blocks any stall-cut while a trade is above minus 0.3 percent, which traps drifters. This threshold becomes R-relative, and a trade that has not moved 1R in its lifetime is recognized as dead, which triggers the scratch-exit described in Part 6. This closes a proven gap and is coordinated into the red owner; it is not a new gate.

### Integration with the enforcer (the gateway) — the most delicate interaction

This is the interaction that needs the most care, because it is where the clamp-noop failure lives. The gateway's minimum-distance rule is what currently kills the ladder's writes: across three days, 99.6 percent of rejected writes were clamp-noops, where the clamped value did not improve the existing stop. If R makes the locks small on a quiet coin, but the gateway's minimum distance remains a fixed wider value, the gateway will reject the adaptive locks exactly as it rejects the current ones. Therefore the gateway's minimum-distance rule must also become R-aware, or the profit-lock path must receive an exemption analogous to the existing breakeven-floor hold. This is the single place where the adaptive layer and an existing system can fight if they are not coordinated. Scaling the ladder beautifully will achieve nothing if the gateway still clamps the writes away. The gateway's other three rules, tighten-only, maximum-step, and rate-limit, stay in place; tighten-only and the catastrophic precedence are never weakened.

### Integration with the owner hierarchy — clean by design

The owner switch decides who writes the stop, by the trade's green or red state. The adaptive layer decides what value is written. These two concerns are orthogonal. The owner switch does not care whether the stop is 0.13 percent or 1.5R; it only enforces that the green owner writes the stop when the trade is green and the red owner when it is red. So the adaptive layer sits entirely inside each owner's value-computation and never touches the ownership logic. The hierarchy that was recently built stays exactly as it is. There is no conflict, by construction.

### Where R comes from — reusing what exists

R is the Average True Range as a percentage, which the existing volatility profiler already computes, and which already feeds the gateway's minimum-distance path. So the measurement is already in the system and already wired to one consumer. The adaptive layer extends that same source to feed the ladder, the trail, the graduation latch, the hard stop, and the scratch-exit logic. This is not a new volatility system; it is letting the existing one drive more thresholds.

## Part 8 — The interaction risks, stated honestly

Three places need care, and all are coordinatable.

The gateway minimum distance, described above, is the critical one. It must become R-aware or the adaptive locks will be clamped away just like the current ones. Any build of this design that scales the ladder and trail without also addressing the gateway minimum distance will fail to improve anything.

The behavioral gates must not be R-ified. The time-decay gate stack, the profit guard, and the win-probability model are protections, not move-size thresholds. Only the distance and geometry values become R-based. Scaling the behavioral gates would break logic that currently works.

The recompute cadence must not cause thrash. R updating every tick is good, but if R jitters, the stop could jitter, which would trip the gateway's rate-limit. So R must be smoothed with a short moving average, and the existing minimum-change throttle and rate-limit stay in place as the damping. The geometry should breathe with the coin, not vibrate with the noise.

## Part 9 — What this design changes and what it leaves untouched

It changes the numbers the profit and loss owners compute with: the ladder arm and rungs, the trail distance and its minimum floor, the graduation arm, the hard stop, the allowed-loss room, and the development-guard threshold all become R-derived and fee-floored. It adds a scratch-exit for the dead-drifter band, coordinated into the red owner. It requires the gateway minimum distance to become R-aware.

It leaves untouched: the owner hierarchy and the owner switch, the catastrophic cap and the force-close twins, the behavioral gates of the loss engine, the score-action and profit-guard logic, the spine selection mechanism, and the gateway's tighten-only, maximum-step, and rate-limit rules. The working protections are preserved; only the move-size geometry is made adaptive.

## Part 10 — How its success is measured

The design is replayed against the real logged trades from the captured windows before it is enabled live. The replay shows, on the actual trades, how many winners the R-based geometry would have locked and grown versus the flat values that clipped them, and confirms the outcomes are net of each trade's fees. The target to measure against is a materially higher win rate and real net-of-fee profit per trade, with winners no longer surrendering most of their peak.

It must be stated honestly that the adaptive exit can stop the give-back and make wins net-positive, which is a large and measurable gain, but the final win rate also depends on the entries and the realized move sizes, which this design measures through R and the fee floor but does not itself change. The R-based geometry is the right foundation regardless of that, because a system trading either small or large moves must size its exits to each coin's volatility. The remaining distance to any win-rate target, if it exists after the give-back is fixed, is the entries' to close, and the R and fee-floor data this design produces will show exactly how large that distance is, coin by coin.

## Part 11 — The design in one paragraph

One movement unit, R, the Average True Range as a percentage, is computed per coin per tick from the existing volatility profiler, smoothed to avoid jitter. Every exit threshold becomes a bounded multiple of R, floored at the trade's round-trip fee where it is a profit threshold: the arm at the larger of 0.5R and the fee floor, ladder rungs at 1.5R, 3R, and 5R, the trail at 1R behind the peak with its minimum floor lowered so it can sit inside small moves, the take-profit at a reachable 4R to 6R, the hard stop at a wide 8R to 10R below the cap. Profit is captured in stages as the trade clears each rung, the dead drifter is scratched once it is clear it will not move 1R, and the recovered fighter's bounce is captured because the geometry is always live. These values are handed to the existing owner hierarchy, which is unchanged and still decides who writes the stop; the adaptive layer only decides what value they write. The gateway's minimum-distance rule is made R-aware so it stops clamping the adaptive locks, while its tighten-only and rate-limit rules and the catastrophic cap stay exactly as they are. The behavioral gates of the loss engine are left untouched. The result is an exit that fits every coin, keeps the green it reaches, counts fees so wins are real, and surfaces which coins are too quiet to trade at all.
