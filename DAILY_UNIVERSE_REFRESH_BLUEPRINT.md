# The Dynamic Daily Universe-Refresh System — Complete Self-Contained Blueprint

A single, complete reference for a system that rebuilds the 50-coin trading universe twice a day (and on manual demand) around coins that are actually moving, so the brain trades coins that travel far enough to make money instead of the calm, static list it trades today. This document contains everything discussed: the situation, the aim, the plan, the selection factors, the timing and its reasoning, the manual control, the warm-up logic, the open-position safety, the honest limitations, and the question-and-answer reasoning behind every choice. Nothing else needs to be read alongside it.

This document uses heading structure and prose only, for screen-reader access. No emoji, no tables, no decorative separators.

A standing instruction for whoever implements this: this is a NEW feature being implemented and integrated into a live system. Do not rely on this blueprint alone. Read the actual project code, line by line, file by file, and confirm every assumption here against what the code really does before building. Where this blueprint and the code disagree, the code is the truth and the disagreement must be surfaced, not silently resolved. This blueprint says what to build and why; the code says what is really there to build it into.

## Part 1 — The situation: why this system is needed

### What is wrong, proven from the data

The trading system loses money, and a long investigation has narrowed the cause to something specific and now certain. The system is structurally honest (the scoreboard is truthful, the inputs are fresh, the brain executes as instructed, the exit-authority collision is resolved), and a great deal of work has gone into the exit systems. But across every window examined, the dominant fact is this: the coins the system trades do not move enough to make money.

In the one-hour window of 2026-06-15 between 01:44 and 02:44 UTC, seventeen positions were opened, and the largest peak any coin reached was plus 0.55 percent. Most peaked under plus 0.21 percent. Not one trade reached even plus 0.6 percent of movement in a full hour. The system set profit targets of 2.5 to 5 percent on these trades; the coins moved a tenth of that at most. The wins that resulted were plus 0.07 percent, plus 0.01 percent, plus 0.006 percent — not profit, fee-scratches that happened to land barely positive. The one real loss in the window, OPUSDT at minus 1.35 percent, was larger than all seven wins combined. The system had a 58 percent win rate and still lost money, because the wins were rounding errors and a single loss erased a dozen of them.

This is not an exit problem at root. No exit calibration can make a coin that moves plus 0.3 percent into a profitable trade; the most you can capture from a plus 0.3 percent move, after fees, is a sliver. The binding constraint is upstream: the coins themselves do not travel far enough. The exit work (the adaptive R-based geometry, the owner hierarchy) is correct and necessary and waits downstream, but it operates on whatever moves the coins make, and right now the coins make almost none.

### Why the coins do not move — the universe is the cause

The universe of coins the system may trade is a fixed list of 50, read from config.toml [universe].watch_list, organised as 12 majors, 23 mid-caps, and 15 aggressive hunters, and updated by manual operator review roughly weekly. The exchange exposes about 582 tradeable USDT coins; the system discards 532 of them at the very first step and only ever looks at the 50.

Two structural flaws in that list cause the no-movement problem. First, the list is anchored to the calmest coins in the market: the 12 majors that lead it are BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, ARB, NEAR, ATOM — the largest, most established, lowest-volatility coins there are, the ones that move 1 to 2 percent on a normal day. A universe that leads with the 12 biggest coins is structurally a low-movement universe. Second, the list is refreshed far too slowly. Crypto's big movers rotate daily; the coin that is up 30 percent today on a catalyst is almost never the coin that was moving last week. A weekly hand-review cannot keep up, so by the time a coin earns its way onto the list, its move is usually over.

The logs prove the consequence directly: the trades in that window were LINK, SOL, BNB (majors), AAVE, ICP, DYDX, IMX, SAND (mid-caps) — the two calmest tiers — and they did what calm coins do, which is nothing. The system spent the hour trading the quietest coins it owns, in the quietest hours of the day, and they went nowhere.

### The aim of this system

The aim is to put coins that actually move into the 50, every day, so the brain has something worth trading and the exit work has moves worth keeping. Stated plainly: stop trading a static list of mostly-calm coins reviewed weekly, and start trading a freshly-selected list of coins that are in an active, volatile, trending phase, rebuilt around what is genuinely in play. This does not change the runtime architecture (the system still operates on a fixed 50); it changes the contents of the 50, on a daily cadence, toward movers.

### An honest statement of what this can and cannot do

This system cannot pick the best 50 coins of the day. The best movers of a day are knowable only after the day is over, and selection happens before the moves. No system can know in advance which coins will be the day's top movers; anyone claiming otherwise is wrong. What this system can do is shift the odds substantially: it identifies coins in an active phase, and active phases persist (volatility clusters), so a coin moving strongly for several days is far more likely than a random coin to keep moving. The realistic claim is not a perfect 50, it is a dramatically better 50 than the static calm list, going from a universe that structurally cannot win to one that can. False positives (coins that were active and go quiet) and missed movers (quiet coins that explode on news) are unavoidable and expected. The goal is not perfection; it is moving the universe from roughly one in ten coins capable of a real move to a clear majority, which is the difference between a system that cannot make money and one that can.

## Part 2 — The plan in one view

Twice a day, and whenever the operator presses a manual button, the system pauses finding new trades, makes a single bulk call to the exchange to read every coin's recent activity, ranks all coins by a movement score built from sustained multi-day activity, rebuilds the 50-coin watch list around the genuine movers while keeping a small stable liquid core, gives the newly added coins a warm-up window so their analysis data populates, and then resumes the brain on the fresh universe. Throughout the entire process, every open position keeps its full exit management running; the refresh only ever pauses the finding of new trades, never the management of existing ones.

The two scheduled refresh windows are 23:00 to 00:00 UTC and 11:00 to 12:00 UTC. The manual button triggers the same process on demand with a confirmation step. The selection is based on multi-day activity, not last-24-hour price change, because the latter catches exhausted moves and the former catches coins still in play.

## Part 3 — The selection factors: what decides which coins enter the 50

This is the heart of the system. The score that ranks coins is built from the following factors. Each is explained with what it measures, why it is included, and the principle that governs all of them.

### The governing principle: multi-day activity, never a single-day move

Question: why measure activity over several days rather than the last 24 hours?

Answer: because there are two different things one could measure, and only one of them avoids a fatal trap. Measuring what moved in the last 24 hours (the 24-hour price change) catches coins whose move is already over; a coin that pumped 30 percent yesterday shows a huge 24-hour change, but that move is spent, and selecting it means buying the exhausted top, after which it rests during the trading day while a new, unseen set of coins starts moving. This is the exhaustion trap: ranking on yesterday's move means perpetually trading rested coins and missing fresh ones. Measuring sustained activity over several days (recent volatility, rising volume, expanding open interest) instead catches coins in an active phase, which is a multi-day state that persists, so the coin is still in play tomorrow whenever its favored session arrives. Every factor below is therefore measured over a multi-day window, as a measure of ongoing activity, not as a measure of a single completed move. This is the single most important design choice in the selection, and it is the one that makes the daily cadence correct: the in-play set rotates over days, so a daily refresh tracks it without chasing hourly spikes.

### Factor one: recent realized volatility — the backbone

What it measures: the coin's average daily trading range, expressed as a percentage of price, over the last several days.

Why it is the backbone, with the highest weight: it directly answers the only question that ultimately matters, which is does this coin move. A coin averaging 6 to 8 percent daily ranges is a mover; a coin averaging 1 percent is dead, no matter what else is true of it. And it persists: a coin that has been ranging wide this week tends to keep moving, because volatility clusters. This factor is the heart of the score; everything else adjusts around it.

### Factor two: volume — as a hard floor and as a surge signal

What it measures, in two uses. As a hard floor: the coin's daily dollar volume must clear a minimum (the current floor is 5,000,000 dollars; a higher floor may be chosen for safety) so the coin can actually be entered and exited. As a scored signal: the coin's recent volume compared to its own recent baseline, so that rising volume scores higher.

Why both uses matter. The floor is a disqualifier, applied before scoring, because a coin that moves 15 percent but has 500,000 dollars of volume is untradeable: you would move the price yourself and never fill cleanly, so a mover you cannot trade is worthless and must be removed before it can be ranked. The surge signal matters because rising volume means money is flowing into the coin, it is waking up, and large moves follow volume; a coin moving on rising volume is real, while a coin moving on flat volume is suspect.

### Factor three: open interest expansion — the positioning signal

What it measures: how fast the coin's futures open interest is growing over recent days.

Why it is included: rising open interest means traders are building leveraged positions in the coin, which is the market getting positioned, and positioning often precedes volatility. It is a slightly forward-looking signal because positions build before the move resolves. A note on cost: this factor may require more than the single bulk call, so it may be computed only on the coins that already survived the liquidity floor and ranked near the top, or omitted entirely if its cost is not justified; the bulk call alone, giving volatility, volume, and directionality, gets most of the way without it.

### Factor four: directionality, or trend strength — the whipsaw filter

What it measures: whether the coin's recent movement is directional (trending) or merely thrashing (chopping), computed as the ratio of the net move to the total range over recent days. A coin that traveled 8 percent net in one direction scores high; a coin with an 8 percent range but near-zero net move scores low or negative.

Why it is non-negotiable: this is the factor that separates good movers from traps. Without it, a selection that chases the most volatile coins fills the list with coins that move violently in both directions and stop a trade out twice, which is worse than a calm coin. The aim is coins that trend, not coins that vibrate. A coin that trended 5 percent in a direction is a far better selection than one that whipsawed 10 percent and went nowhere. This factor gates the others: a coin with high volatility but poor directionality is knocked down, because its movement is the dangerous kind.

### How the factors combine

The liquidity floor (volume and spread) is applied first as a hard pre-filter; coins that fail it are removed before scoring and can never be selected. The remaining coins are scored on volatility (the backbone, highest weight) plus the volume surge plus the open interest expansion, with the whole score gated or multiplied by the directionality filter so that a volatile-but-choppy coin is demoted. The scored coins are ranked, and the top of the ranking fills the movement slots of the 50.

### Where the factor data comes from, and why this is cheap

Question: is it not impossible to pull and process data for 582 coins?

Answer: it would be expensive to fetch full per-coin data for 582 coins, but that is not what this does. The exchange exposes a single endpoint that returns a 24-hour ticker summary for every coin at once, in one call (the system already uses this, get_all_linear_tickers, which returns about 582 tickers in one response). That single response contains, per coin, the price change, the high and low (so the range, which is volatility), the volume, and the turnover. So volatility, volume, and directionality for every coin come from one call that the system already makes, with light processing of the result, not 582 separate scans. Open interest is the only factor that may need more, and it is fetched only on the top survivors or omitted. The whole selection therefore costs essentially one bulk call plus light computation, which is trivial, especially because it runs only twice a day rather than every cycle. The fact that the exchange hands over all coins' activity in a single response is what makes scanning the whole market feasible.

## Part 4 — The timing: when the refresh happens and why

### Why a fixed time at all, and why not continuous

Question: since the system runs 24 hours a day, seven days a week, when should the universe change?

Answer: because the in-play set of coins rotates over days, not minutes, the universe should refresh on a daily cadence, not continuously. Refreshing continuously would churn the universe on noise; refreshing on a multi-day-activity basis a couple of times a day tracks the real rotation. The system cannot make the 50 dynamic at runtime (the architecture requires a stable 50 during operation), but it can swap the 50 on a schedule, which is exactly this.

### Why two windows, at 23:00 and 11:00 UTC

Question: why two refreshes rather than one?

Answer: because different coins are active in different regional sessions, and two refreshes let each one tune the universe to the session that is about to start. The 23:00 UTC refresh captures the full day that just completed (Asia, Europe, and US sessions all finished) and sets the universe for the Asian session ahead, which runs roughly 00:00 to 08:00 UTC. The 11:00 UTC refresh captures the Asian and early-European activity and sets the universe for the Europe and US session ahead, which runs roughly 13:00 to 21:00 UTC. Each refresh therefore loads a universe tuned to the coins most likely to be active in the hours immediately following it.

### Why 11:00 rather than 12:00 for the second window

Question: does the second pause cost more than the first, and how is that handled?

Answer: the first pause (23:00 to 00:00) sits in a quiet handoff between the US winding down and Asia not yet hot, so pausing the finding of new trades there costs almost nothing. A pause at 12:00 to 13:00 would be more expensive, because it would resume the brain right as the US session is starting (US movement picks up around 13:30 to 14:00 UTC), meaning the system would be warming up exactly when it should be trading. Moving the second window earlier, to 11:00 to 12:00, solves this: the refresh and warm-up happen during the quieter late-European-morning lull, and the brain is back and trading the fresh, US-tuned universe by 12:00, before US prime time begins. This keeps the benefit of a session-tuned universe without paying the cost of going dark during the US open. Both chosen times, 23:00 and 11:00, also sit clear of the funding settlements at 00:00, 08:00, and 16:00 UTC, so the activity data they read is not distorted by settlement spikes.

### The honest caveat about the dead hours

Question: if coins go quiet overnight anyway, what is the use of selecting them?

Answer: the universe refresh selects which coins are in the 50, but it cannot make those coins move during the dead overnight hours (roughly 02:00 to 06:00 UTC), because that is when the whole market, movers included, rests. So even a perfectly selected list will mostly sit still during the dead hours. This is a real limitation, and it is a separate problem from the universe: the universe fix gets the right coins into the 50, but whether a 24-hour system should trade as hard during the dead hours as during the active ones is its own question, worth returning to later. The reason the refresh still works despite this is the governing principle: because selection is on sustained multi-day activity rather than the last hour's move, a selected coin is in-play across the whole day and will move during whichever session it favors, and because the system trades all sessions, it will be there when the coin moves. The dead hours are quiet for everyone; the selection ensures that when movement does come, in whatever session, the right coins are present for it.

### Whether two refreshes are even necessary

Question: if selection is on multi-day activity, will the two lists not be nearly the same?

Answer: possibly, and this should be measured rather than assumed. If a coin is genuinely in-play, its multi-day activity will place it in both the 23:00 and the 11:00 selection, so the two lists may overlap heavily. If they overlap heavily (for example 80 percent the same coins), the second refresh adds little and the system could run once daily to avoid the second pause. If the US session genuinely brings a substantially different set of coins into play, the second refresh earns its place. The build should make the divergence between the two lists visible so this can be judged on evidence; the principle is not to pay for a second refresh that picks the same coins.

## Part 5 — The refresh sequence, step by step

What happens, in order, at each scheduled window (and on a manual trigger):

First, the brain is paused. No new Call-A (find-new-trades) is issued. This is the only thing paused.

Second, every open position continues to be fully managed. Its exit engines (the owner switch, the stops, the loss-cutting, the catastrophic cap) keep running every second, exactly as during normal operation. Open positions are force-kept in the universe regardless of the refresh, as they already are. The refresh changes the candidate universe for new trades; it never abandons or neglects a live position. This is a non-negotiable safety property and must be verified to hold across a universe swap, not merely during a normal cycle.

Third, the refresh runs: one bulk ticker call reads every coin's recent activity; the multi-day activity score is computed for every coin from that response (plus open interest on the top survivors, if used); the liquidity floor removes untradeable coins before scoring; the coins are ranked; and the 50 is rebuilt around the top movers while keeping the stable liquid core and any open-position coins.

Fourth, the new 50 is written to the watch list, and the newly added coins begin a warm-up window during which the analysis workers populate their data.

Fifth, after the warm-up, the brain resumes with its first Call-A on the fresh universe, now with the new coins carrying real analysis data rather than blank profiles.

## Part 6 — The warm-up: why the brain waits before trading the new coins

### Why a warm-up is needed at all

Question: why not resume the brain the instant the new 50 is written?

Answer: because a coin that has just entered the universe has a blank analysis profile. The technical-analysis workers compute each coin's structural read, regime classification, signal, and strategy votes over time as they observe the coin. A brand-new coin has none of that yet. If the brain were briefed the instant the universe changed, it would be deciding on coins with missing or partial analysis, which is exactly the silent data-quality gap that must be avoided. The warm-up is the time the analysis workers need to populate real, gate-passing data on the newly added coins before the brain trades them.

### How long the warm-up should be, and the fact that decides it

Question: should the warm-up be one hour, or would half an hour do?

Answer: it depends entirely on one fact about the system that must be confirmed in the code, not assumed. If the analysis workers backfill a new coin's recent candle history immediately when the coin is added (fetching the last several hours of klines in one go), then the data is ready in minutes, and a short warm-up such as thirty minutes is plenty. If instead the workers build a new coin's data only from live observation going forward, accumulating candles tick by tick, then real elapsed time is required for enough candles to form (the freshness gates require around 50 candles and a couple of regime readings, which at 5-minute candles is over four hours), and in that case even an hour would be insufficient and a deeper design issue exists. The single fact that decides the warm-up length is therefore: when a coin is added to the universe, does the system backfill its candle history immediately, or does it start collecting from zero. This must be checked in the actual code during the build.

### The safe design for the warm-up

Rather than guess a fixed thirty or sixty minutes, the safe design is to make the warm-up data-gated: the brain resumes when the newly added coins' data actually passes the existing freshness gates, however long that takes, with a sensible maximum. If the data is ready in twenty minutes, the brain resumes at twenty; if it takes longer, it waits. The freshness checks already exist in the system and can be reused for this. As a default until the backfill question is confirmed, an hour-long window is retained (it is also the dead-hour window for the 23:00 refresh, so it costs little there), but built to resume early when the data is genuinely ready, so the pause is never longer than it must be and the brain never trades a blank-data coin.

## Part 7 — The manual control: the operator's on-demand refresh

### Why a manual button exists

Question: if there are two scheduled refreshes, why also a manual one?

Answer: because the schedule handles the routine, but the operator will want to refresh on demand for the cases the schedule does not cover: a coin goes parabolic mid-afternoon, the market regime shifts after a news event, or the current 50 is visibly going stale and the operator wants fresh movers now rather than waiting for the next window. A manual trigger means the operator is not locked to the clock when something changes. It also serves as a test harness: the operator can press it, see exactly which 50 coins the system selects, and judge whether they are genuine movers, without waiting for a scheduled window, which is a way to validate the selection logic.

### How the manual button works

The button triggers the exact same refresh process as the scheduled windows, on demand. It does not build a second system; it is a third trigger for the one engine. On press, it first asks for confirmation (for example, confirm: stop finding new trades, refresh the universe, resume in about an hour), because the action pauses new trades for the warm-up duration and should not be triggered by an accidental tap. On confirmation, it pauses Call-A, runs the refresh and warm-up exactly as a scheduled refresh does, keeps all open positions fully managed throughout, and resumes the brain after the warm-up. It posts clear status updates to Telegram at each stage: refresh started and trading paused; the new 50 coins that were selected; warm-up in progress with the resume time; and trading resumed. Showing the selected 50 lets the operator see and judge the picks.

### The safety guards on the manual button

Three guards. First, open positions stay fully managed through the manual refresh, exactly as in the scheduled ones. Second, the confirmation step guards against an accidental press costing an hour of paused trading. Third, an overlap guard handles the case where the manual press coincides with a scheduled refresh or another manual press: if a refresh is already running or imminent, the manual press is either ignored with a message that a refresh is already in progress, or it reuses the one already happening, rather than stacking two refreshes on top of each other.

### The honest cost of a manual refresh during active hours

A manual refresh during the dead hours costs little, like the scheduled 23:00 one. A manual refresh during active hours (for example mid-US-session) costs more, because the warm-up pause means going dark during good movement. This is a real tradeoff, and it is acceptable precisely because it is a deliberate operator choice: the operator presses the button only when they have seen something that makes fresh coins worth an hour of pause. This is the reason the on-demand refresh is a manual button rather than an automatic trigger; a deliberate choice carries the judgment that an automatic rule could not.

## Part 8 — How much of the 50 refreshes: the stable core question

Question: should each refresh rebuild the entire 50, or keep part of it stable?

Answer: this is a genuine design choice with two reasonable answers, to be decided and stated in the build. One option is a full rebuild every refresh, which always gives the freshest session-tuned set but churns more coins and requires warming up more new coins each time. The other option is a stable liquid core (a handful of major, liquid coins kept for ballast and reliable execution) plus rotating opportunity slots (the larger remainder, refreshed toward movers each time), which churns fewer coins and shortens the warm-up because fewer coins are new each refresh. The earlier inclination was a stable core plus rotating slots, keeping roughly eight to ten liquid majors stable and refreshing the rest toward movers. Whichever is chosen, two things are fixed: any coin with an open position is always kept regardless, and the selection that fills the rotating or rebuilt slots uses the multi-day activity factors of Part 3.

## Part 9 — What this system does not touch, and the safety boundaries

This system changes the contents of the watch list on a schedule and on demand. It does not change the runtime trading architecture: the system still operates on a fixed 50 during operation; only the contents of the 50 change, between refreshes. It does not touch the exit systems, the owner hierarchy, the stops, or the catastrophic cap. It does not touch the brain's decision logic. It does not touch the analysis workers' computations; it only triggers their warm-up on new coins. Open positions are never abandoned, neglected, or force-closed by a refresh; they keep full exit management throughout and are always force-kept in the universe. The protected tables (tias_results, trade_log, trade_history, thesis_store, virtual_positions) are never touched. No new trading gate is added; the selection is a universe-construction process, not a per-trade filter that refuses trades at decision time.

## Part 10 — The implementation mandate

This is a new feature integrated into a live system, so the implementation must be investigation-first and careful. The following are required of whoever builds it.

Analyze the real project deeply and do not rely on this blueprint alone. Read the code line by line, file by file: where the watch list is read and how the system would react to it changing at runtime; the market scanner and the bulk ticker call (get_all_linear_tickers) and exactly what fields its response carries; how the analysis workers populate data on a coin and, critically, whether they backfill a new coin's history on add or start from zero (the fact that decides the warm-up); how open positions are force-kept and how to guarantee they stay managed across a universe swap; how the brain is paused and resumed; and how the Telegram control surface issues commands and confirmations. Confirm every assumption in this blueprint against the code, and where they disagree, surface it rather than silently resolving it.

These are fixes and additions woven into the existing system, not a separate subsystem, and not a new per-trade gate. Work on the main branch with no new branch and no new directory. Make changes as atomic, individually-revertible commits with plain-language messages, with timestamped backups before editing any file. Centralize every configurable value (the refresh times, the warm-up maximum, the factor weights, the liquidity floor, the stable-core size, the whipsaw threshold) in named configuration with a boot sentinel, introducing no new scattered hardcoded values. Make the system observable: each refresh logs the bulk call, the scoring, the selected 50, the coins added and removed, the warm-up duration, and the resume; and the manual button posts its status to Telegram including the selected 50. Verify before trusting: confirm a refresh selects genuine movers (the selected coins really do have higher recent activity than the static list they replace), confirm open positions stay managed across a swap, confirm the warm-up resumes only on gate-passing data, confirm the manual button's confirmation and overlap guards work, and confirm the dead-hour and funding-settlement timing behaves as intended. The operator approves at a decision gate before the system is enabled live, and the bar is high because this changes what the whole system trades.

A final honesty requirement, to be stated plainly and not oversold: this system does not pick the best 50 coins of the day, because that is knowable only in hindsight; it picks 50 coins with a much-better-than-random chance of moving, which is a large improvement over the static calm list but is not a guarantee. False positives and missed movers are expected. The measure of success is whether the traded coins move materially more than they do today, which is verifiable by comparing the selected coins' realized movement against the old list's, and the build should produce that comparison so the improvement is proven on evidence rather than asserted.

## Part 11 — The whole system in one paragraph

Twice a day, at 23:00 and 11:00 UTC, and on a confirmed manual Telegram press, the system pauses finding new trades while keeping every open position fully managed, makes one bulk exchange call that returns every coin's recent activity, scores all coins on sustained multi-day activity (recent realized volatility as the backbone, plus a volume surge and open interest expansion, all gated by a directionality filter that demotes choppy whipsaw coins, with a hard liquidity floor removing untradeable coins first), ranks them, and rebuilds the 50-coin watch list around the genuine movers while keeping a small stable liquid core and all open-position coins; the newly added coins then warm up until their analysis data passes the existing freshness gates (a window that is data-gated, defaulting near an hour but resuming early when ready, its true minimum decided by whether the system backfills a new coin's history on add), after which the brain resumes on the fresh universe. The selection is on multi-day activity rather than the last day's move, so it catches coins still in play rather than exhausted pumps; the two windows are timed to tune the universe to the Asian and the US sessions while avoiding the dead-hour and funding-settlement distortions; and the manual button gives the operator an on-demand refresh and a way to see and judge the selected coins. The runtime architecture, the exit systems, the brain logic, and the protected tables are untouched; only the contents of the 50 change, on a daily cadence, toward coins that actually move — because the proven root cause of the system's losses is that the coins it currently trades do not move far enough to make money, and no downstream exit work can fix a universe that cannot win.
