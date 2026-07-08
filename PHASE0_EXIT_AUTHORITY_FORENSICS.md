# Phase 0 — The Exit Collision, Proven Against the Logs

This is the Phase 0 deliverable for the exit-authority consolidation. It contains no code change. It establishes the ground truth before any design: the complete inventory of every system that writes the stop-loss or blocks an exit, the gateway's arbitration behavior measured across both captured windows, and real traded examples that prove the collision mechanism. The phases that follow are gated on the operator's approval of this report.

The two windows are the captured bundles: window one runs 2026-06-13 22:45 to 2026-06-14 09:45 UTC, and window two runs 2026-06-14 10:45 to 21:45 UTC. All counts below are measured directly from those two files.

## Method

Every finding cites a code location or a measured log fact, re-verified against the current code. Where a real trade is traced, the ordered sequence of stop writes is taken from the gateway's own accept log and the sniper's selection log. Where the document's stated mechanism and the evidence diverge, the divergence is reported plainly rather than smoothed over.

## The single chokepoint and the four rules

Every stop-loss that reaches the exchange passes through one function: SLGateway.apply() in src/core/sl_gateway.py. There is no other path to set a stop. It is reached from five call sites: src/workers/profit_sniper.py at lines 1296, 2952, 3459, and 5155, and src/workers/position_watchdog.py at line 1292. The gateway is constructed in src/workers/manager.py at line 756. Its configuration lives in config.toml under the section beginning at line 920.

The gateway enforces four rules. R1, tighten-only, is never bypassable and rejects any write that would move the stop away from price (src/core/sl_gateway.py:495). R2, minimum-distance, clamps a stop that sits too close to current price, with an optional breakeven-floor carve-out (line 516). R3, maximum-step, clamps or bypasses a move larger than the per-tick cap depending on a per-source allowlist (line 639). R4, rate-limit, rejects writes that arrive too soon after the last accepted one (line 760).

The gateway is deliberately state-blind today. It has no notion of whether a trade is in profit or underwater. The only entry-aware input it can receive is the optional breakeven_floor_price. It does, however, already fetch the position (line 430) and resolve current price (line 473), so it has the raw material to learn trade state — which is the foundation the later phases build on.

## The complete writer-and-guard inventory

The following systems write the stop or decide an exit. Each is named with its module, what it does, when it fires, and how often it landed an accepted write across the two windows (accepted-write counts are from SL_GATEWAY_ACCEPT; force-closes do not write a stop and so have no accept count).

The opening stop. loss_atr_initial places the first protective stop at position open, sized from ATR, clamped never to sit looser than the sacred cap (src/workers/profit_sniper.py:1296). It fires once per position. It landed 119 writes in window one and 134 in window two — it is the second most active writer, because every new position needs its opening stop.

The profit ladder. profit_sniper_ladder is the staged profit-lock floor that ratchets up as the trade climbs through its rungs (selected in the sniper spine, written at src/workers/profit_sniper.py:2952). It fires every tick while a green trade is developing. It is by far the most active writer: 197 accepted writes in window one and 293 in window two, against 640 and 822 rejected attempts in the two windows. It alone accounts for the bulk of the gateway's traffic.

The Chandelier trail. profit_sniper_trail is the peak-anchored runner trail, high-water minus an ATR leash, for fast vertical runners (src/workers/profit_sniper.py:3459 and the spine). It fired only 3 and 5 times — it rarely wins selection because the ladder usually sits tighter.

The profit lock. profit_sniper_lock is the one-shot breakeven or first-rung profit lock (src/workers/profit_sniper.py:5155). profit_sniper_breakeven is a reserved source in the bypass allowlist, not actively used.

The safety sweeper. safety_sweeper re-asserts a protective floor; on a naked position (no stop at all) it joins the urgent lane via the _is_naked flag (src/workers/profit_sniper.py:2812). It is doing the catastrophic Head's job in the naked case and a green-side floor in the re-assert case.

The sacred cap. loss_cap places the absolute hard cap as a tighten-only stop, and loss_cap_emergency places a just-inside-price variant when the cap would sit on the wrong side of price (src/workers/profit_sniper.py:2747 and 2757). loss_cap landed 15 and 29 writes. The cap also has a force-close twin, loss_cap_force, that closes the position outright at the true ceiling (src/workers/profit_sniper.py:2717) and does not write a stop through the gateway.

The catastrophe stop. The volatility-spike catastrophe force-closes the position (closed_by loss_spike_force) and never writes a stop through the gateway. It is always on and independent of graduation state.

The structure stop. loss_structure places a stop just beyond the X-RAY invalidation level, the buffer shrinking with age (src/workers/profit_sniper.py:2773). It landed 29 writes in window one and 135 in window two — in window two it was the third most active writer.

The recovery trail. loss_recovery is the final-phase, history-aware bounce-capture trail on the loss side (src/workers/profit_sniper.py:2789). It landed 48 and 50 writes, against 106 and 115 rejected attempts.

The time-decay engine. time_decay is the five-model loss engine that tightens or force-closes underwater trades (written from src/workers/position_watchdog.py:1292, source time_decay; computed in src/risk/time_decay_sl.py). It landed 2 and 1 stop writes, but its primary effect is force-closing dead grinds, not writing stops.

The advisory writers. The following currently write the stop directly through the gateway as independent writers: brain_tighten, the Claude-directed tighten (2 and 5 writes); wd_brain_scoring, the watchdog's thirty-percent tighten on a reject-and-tighten verdict (3 and 3 writes); sentinel_deadline, the deadline-engine tier tighten (2 and 3 writes); sentinel_advisor, the portfolio-advisor tighten; and the watchdog's own green-side trails watchdog_lock_peak, watchdog_breakeven, trail_activation, and trail_update. The four watchdog green-side trails are already dormant in the current configuration because subordinate_watchdog_trail_exit is true (config.toml:2087), so the profit engine owns trailing and they do not fire.

## The guards, and why each exists

Each guard exists for a real reason; the consolidation must preserve every one at the correct level. They are concentrated in the loss engine (src/risk/time_decay_sl.py and the sniper loss block).

The grace period suppresses any loss action during a per-class settling window so early noise does not trigger a cut. The age guard, stricter still, suppresses both force-close and stop-tightening for the first three hundred seconds (the settling contract mirrored across the sniper, the watchdog, and time-decay). The MAE monotonic high-water hold ensures the worst-excursion tracking can only deepen, never regress, so a state reset cannot lose the high-water mark. The MAE-to-stop ratio gate suppresses any loss action until the drawdown reaches half the original stop distance, keeping a normally-developing trade untouched. The structural-invalidation gate permits a force-close only when real evidence exists (an X-RAY confidence drop, a setup drift, or a regime inversion), preventing a kill on decayed probability alone. The recovery guard holds a force-close on a trade that is recovering near breakeven. The stall-exit signs-of-life veto spares a slightly-building late-bloomer from a time-based cut. The monotonic-grind cut catches a dying low-volatility grind that the ratio gate never reaches. On the profit side, the profit guard blocks sub-fee bailouts so a winner is not exited for crumbs.

These guards are observed working correctly on genuine losers (traced below) and must not be removed or weakened — only reassigned so they no longer run on a green trade.

## The clamp-and-reject storm proves the collision

The gateway's own outcome counts, measured across the two windows, show it spending most of its effort arbitrating contradictions rather than passing clean writes.

In window one the gateway recorded 420 accepts, 748 rejects, 1000 R2 minimum-distance clamps, 236 breakeven overrides, and 50 wire failures. In window two it recorded 658 accepts, 943 rejects, 1276 R2 clamps, 329 breakeven overrides, and 96 wire failures. Combined, that is 1078 accepts against 1691 rejects and 2276 R2 clamps. The document's forensic summary stated clamping over twenty-two hundred times and rejecting nearly seventeen hundred times; the measured figures are 2276 and 1691, confirming the document's numbers almost exactly.

Nearly all of that traffic is one writer fighting the rules. The profit ladder alone accounts for 490 of the 1078 accepts and 1462 of the 1691 rejects across the two windows. The rejections are overwhelmingly clamp-noop (the ladder proposing a stop that does not improve on the current one) rather than genuine errors: the ladder hammers the gateway every tick and the gateway holds the line. This is the collision in numbers — many writers, one number, a referee clamping and rejecting the contradictions.

## The outcome the collision produces

Across window two, 213 trades closed because their stop was hit (closed_by bybit_sl_hit) and zero closed by reaching a take-profit. Of 141 thesis closes in that window, 82 — fifty-eight percent — closed at a loss. The system reaches profit and then loses it on the exit: stops get hit, targets never do.

## Traced trades — the clip, proven

AAVEUSDT, around 11:04 to 11:07 in window two, is a clean clipped winner. The trade peaked at plus zero point eight zero percent. For a full minute the score engine returned a full-close recommendation (score above the seventy threshold) but the action was downgraded to a tighten by the profit guard. At 11:05:44 an advisory writer, brain_tighten, wrote the stop directly on the still-green trade, pulling it from 66.30 to 66.21. The trade gave back to plus zero point four six percent and stopped out (bybit_sl_hit). Four distinct source systems wrote that symbol's stop across its trades that hour: loss_structure, profit_sniper_ladder, loss_atr_initial, and brain_tighten — including an advisory writer reaching directly onto a green trade.

GALAUSDT, the trade that closed at 11:55:42 in window two, is a second, faster clip. At 11:55:39 the trade was green at plus zero point one three percent, with a peak of plus zero point one three percent — below the plus zero point two zero percent arm threshold, so it had never graduated. The sniper spine selected the ladder as the winner and tightened the stop to 0.00262958, with the cap loss-candidate also present in the selection. Three seconds later the trade stopped out at minus zero point zero three four percent (bybit_sl_hit). A young green trade, its stop pinned tight, stopped out on a three-second pullback.

## Traced trades — genuine losers, correctly cut

Not every losing trade is a clipped winner, and the report says so plainly. GALAUSDT closing at minus zero point six nine percent and ORCAUSDT closing at minus one point zero three percent in window two were genuine losers. Their own per-tick classifiers labeled them loss from the first tick; their best excursion was breakeven; the only stop ever written on each was the initial loss-side ATR stop; the profit ladder never armed. They drifted adverse from entry and were stopped out. The document's statement that one hundred percent of trades ticked green is not borne out for these two; the clip is real and dominant but it is not universal, and Phase 1 must not assume every loss was once a winner.

The loss engine, where it acts, acts correctly — this is the protection to preserve. SOLUSDT and OPUSDT were cut by the monotonic-grind cut, and LINKUSDT by the stall exit. In each, the guard stack held the position while it was young, structurally stable, or under half the stop distance (the age guard, the MAE monotonic ratchet, the MAE ratio gate, the structure-guard defer, and the signs-of-life veto all firing in sequence), and only released to a hard cut once the position proved a dead, non-recovering grind. SOLUSDT exited at zero point two eight percent adverse against a one point five percent hard stop; OPUSDT at zero point four eight percent against one point five; LINKUSDT essentially at breakeven against a one point nine eight percent stop. The loss-cutters cut early and spared nothing recoverable. They must survive the consolidation intact.

## The mechanism, confirmed and refined

Two contested claims in the document are confirmed by the logs, with one refinement each.

Loss-side writers do touch green trades. The loss-authority block in the sniper is gated by a monotonic graduation latch, _graduated equals peak profit at or above the arm threshold (src/workers/profit_sniper.py:2595). While a trade has not yet graduated — its peak has never reached plus zero point two zero percent — the loss block runs and contributes the cap, structure, and recovery candidates, and these compete in the same highest-stop-wins selection as the profit candidates. So on a young green trade below the arm, a loss candidate can win the selection and cage the trade. The selection log shows loss-side candidates winning 331 times in window two and 198 times in window one; a clean example is ETHUSDT at 13:26:16, where the structure candidate won and tightened the stop while the trade was green at plus zero point one zero percent. The refinement: the per-tick decision log only ever records green ticks, so it cannot by itself prove the red side; cross-referenced against the spike log, loss-side writers touch both green and red trades. The claim that they touch green trades is firmly proven.

The early-life muzzle is real. The sniper age guard fired 6299 times in window two and 5759 times in window one, always blocking — it muzzles the smart stall-escape logic for the first three hundred seconds without exception. Meanwhile the mechanical path keeps writing: 468 accepted stop writes in window two landed before three hundred seconds of trade life (267 from the ladder, 133 from structure, 32 from recovery, 29 from the cap, and a handful from the trail and the sentinel). A textbook example is GALAUSDT in window two: the smart escape is blocked at age 8 through 77 seconds while the ladder tightens the stop at age 37 seconds. The refinement: the muzzled logic is the stall-escape force-close, and muzzling it is itself protective; the harm is that the mechanical ladder and the loss candidates write the stop during that same window with no coordinated owner, which is exactly what the consolidation fixes.

The graduation latch is the crux. Authority today is decided by a one-way peak latch, not by current state. Once a trade's peak crosses the arm threshold the loss block switches off for the rest of the trade's life, even if the trade later craters; and before it crosses, both engines write. The system already carries a default-off flag, graduation_crater_rearm_enabled (config.toml:2126), added for exactly the faded-winner case. Moving to one-owner-at-a-time by current state, with a breakeven hand-off, is therefore a genuine behavioral change from the running system — which is why the faded-winner behavior is reserved for the operator's decision at the Phase 1 gate.

## An observability gap found

The per-tick decision log records a trade's profit only when it is green; it does not log underwater ticks. This is why the document's green-versus-red evidence is one-sided and why a same-symbol decision line can be mis-attributed across two concurrent trades. The new owner-state log that Phase 1 adds will record the computed state and owner on every accepted write, closing this gap so the hierarchy's behavior is fully visible going forward.

## Conclusion

The exit is not one system; it is a committee of roughly fourteen writers contending over a single number, refereed by a gateway that clamps and rejects their contradictions twenty-two hundred and seventeen hundred times across two days. On a green trade, the profit ladder, the loss-side structure and cap candidates, and the advisory writers all reach the same stop, and the winner is whatever sits tightest — which on a young peak is often a stop pinned just above breakeven that the next normal pullback takes out. The collision is proven, the guards that work are identified and must be preserved, and the one true authority signal — current trade state relative to entry — is computable but unused today. Phase 1 establishes that signal as the owner switch.
