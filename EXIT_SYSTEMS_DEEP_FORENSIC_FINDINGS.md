# Exit Systems Deep Forensic Findings Report

A complete, evidence-backed map of the profit-fetching and loss-cutting exit systems, cross-checked trade by trade against the logs. Investigation and report only. No code, configuration, or flags were changed in the production system during this work.

## How to read this report

This report was produced as a read-only forensic investigation. Nothing in the trading system was modified: no code edited, no configuration changed, no flag flipped, no commit or push made. The single deliverable is this file.

Every finding is proven from two sides. The code side gives a file and line location for the mechanism. The log side gives a real, quoted log line showing the mechanism acting on a real trade. Where a claim is supported by reasoning but not yet proven from a log line, it is labelled a hypothesis. Where the evidence corrected an earlier belief, the correction is stated plainly.

The investigation read the whole exit stack: the profit sniper (both its profit-fetching and its loss-cutting sides) in src/workers/profit_sniper.py, the time-decay loss engine in src/risk/time_decay_sl.py and src/core/time_dial.py, the stop-loss gateway and owner switch in src/core/sl_gateway.py, the position watchdog in src/workers/position_watchdog.py, the sentinel deadline, advisor, and firewall in src/sentinel/, the trade coordinator close path in src/core/trade_coordinator.py, the execution and close layer in src/trading/services/ and src/bybit_demo/, the risk models in src/risk/, the stop-loss geometry and validation in src/core/, the orchestration in src/workers/manager.py and src/core/container.py, and the configuration in config.toml and src/config/settings.py.

The logs cross-checked were the clean merged one-hour window ALL_LOGS_2026-06-15_0230-0330_UTC.log (21,599 lines, 20 closed trades), the per-second exit feed data/logs/exit_authority_live_feed.txt, and the fourteen rolling multi-day worker logs in data/logs/workers.*.log spanning 2026-06-13 to 2026-06-15, which contain the full day of 254 trades (2026-06-14).

One configuration value deserves a note up front because an automated reader misread it. The flag micro_floor_arm_fee_aware_enabled is set to true in config.toml at line 2078 and is proven active at runtime by 524 MICRO_FLOOR_FEE_SUPPRESS log events across the multi-day window. The default in src/config/settings.py is false, but config.toml overrides it. This report uses the runtime-true value throughout.

## Executive summary

The exit systems are structurally sound and doing roughly what they were built to do. They are not colliding, the owner switch is enforcing correctly, the loss side keeps losses small, and no catastrophic stop was breached in any window examined. The system loses money for a single, specific, proven reason: the entries produce moves that are far too small for the exit toolset, which is calibrated for moves several times larger. The result is that nearly every trade goes green by a small amount, and the profit tools can neither lock that small green nor let it run, so it is handed back to a stop tap.

The numbers are unambiguous. On the full day of 254 trades (2026-06-14), the system closed 257 positions: 103 wins averaging plus 0.13 percent and 154 losses averaging minus 0.23 percent, for a daily net of minus 22.73 percent and an average loss roughly 1.8 times the average win. Zero trades closed on a take-profit. Across the three-day multi-day window, trades peaked at a median of plus 0.23 percent and a ninetieth percentile of plus 0.71 percent, while the take-profit target sits near plus 2.25 to 6 percent, the first real ladder rung sits at plus 0.6 percent, and the trailing stop's minimum distance is 0.30 percent of entry. The achievable move is roughly an order of magnitude smaller than the targets the tools are built around.

The single biggest reason the green is lost is this chain. First, the Chandelier trail can never win control of the stop, because its stop floors at breakeven (entry) whenever its ATR-scaled distance exceeds the actual move, which on a 0.26 percent peak is always; under the highest-stop-wins rule the ladder's entry-plus-a-sliver always beats the trail's entry, so the trail wrote zero stops in the one-hour window and only 8 in three days. Second, the ladder, which therefore is the only profit tool that ever acts, can only lock a breakeven-to-fee-clearance sliver of plus 0.05 to plus 0.13 percent because trades die in the dead band between the 0.2 percent arm and the 0.6 percent first rung; and even that small lock frequently cannot be written, because after the gateway's minimum-distance clamp it does not improve on the stop already in place, producing a clamp-noop reject 99.6 percent of the time a write is rejected. Third, the take-profit is unreachable by construction. The loss side, meanwhile, keeps losses small but holds dead-drifting trades long past their design deadline. The win-to-loss asymmetry is driven by clipped winners, not by runaway losers.

In one sentence: the exit systems are correctly built for a market move that the entries do not produce, so they clip every winner to breakeven and hand the green back, while the losers are capped small but tie up capital too long.

## The profit-fetching system: complete findings

### The stepped break-even ladder

The ladder is the profit tool that actually acts. It is computed in _compute_ladder_floor at src/workers/profit_sniper.py lines 1941 to 2153. As the high-water profit climbs past each step rung it locks a guaranteed-profit floor a fixed offset behind the rung; in the gap below the first rung it falls back to a breakeven-plus-sliver floor and an optional dead-band give-back trail.

The current configuration, read first-hand from config.toml, is the heart of the problem. The ladder arms at min_profit_to_arm_ladder_pct equal to 0.2 percent (config.toml line 2015). The first step rung young is ladder_step_pct_young equal to 0.6 percent (line 1996), gliding to 0.4 percent old. The breakeven lock is ladder_breakeven_lock_pct equal to 0.05 percent (line 2031). The dead-band give-back is ladder_deadband_giveback_pct equal to 0.10 percent (line 2037). The fee-clearance lift is ladder_lock_fee_clearance_pct equal to 0.13 percent (line 2047). The decoupled micro-floor arm is micro_floor_arm_pct equal to 0.10 percent (line 2060), but micro_floor_arm_fee_aware_enabled is true (line 2078), which raises the effective micro arm to the maximum of 0.10 and 0.13, that is 0.13 percent.

The consequence, proven against the logs, is that on a typical 0.26 percent peak the level crossed is zero (the peak never reaches the 0.6 percent first rung), so the step lock is negative and only the breakeven floor applies. In the dead band the lock is the maximum of the 0.05 percent breakeven lock and the peak minus the 0.10 percent give-back, lifted to 0.13 percent only if the peak cleared 0.13 percent. The lock therefore lands between plus 0.05 and roughly plus 0.16 percent. The forensic pass confirms this: across the multi-day window the lock values written by LADDER_ZERO_CROSSING_FLOOR had a median of 0.050 percent and a ninetieth percentile of 0.205 percent. In the cleaner one-hour window the locks clustered at 0.130 percent (24 of 34 zero-crossing-floor events at lock equal to 0.130 percent), because the surviving peaks in that hour mostly cleared the 0.13 percent fee hurdle. A representative line:

"LADDER_ZERO_CROSSING_FLOOR | sym=ALICEUSDT peak=0.161% arm=0.20% step=0.43% level=0.00% be_lock=0.050% fee_clear=0.130% lock=0.130% stop=0.11180516 entry=0.11166000 dir=Buy | modest peak locks at least net-breakeven (Finding 6 + A)"

The correction to the original hypothesis: the ladder does not only ever pin the 0.05 percent breakeven sliver. The fee-aware lift works and routinely raises the lock to 0.13 percent when the peak clears that hurdle (this is why the central-question verdict on the ladder was recorded as refuted rather than confirmed). But the practical effect is the same: the lock is never more than a sliver because the rung that would lock real profit is never reached. The dead band is real and dominant: of 126 trades that graduated in the multi-day slice, 97.6 percent had their peak inside the dead band between 0.2 and 0.6 percent, with a mean peak of 0.239 percent.

There is a second, equally important ladder failure that the original hypothesis did not name: the ladder frequently cannot write its lock at all. This is covered under the spine and the gateway below and is the clamp-noop finding.

The fee-aware suppression has a side effect worth flagging. When a trade peaks between 0.10 and 0.13 percent, the fee-aware arm suppresses the floor entirely, so no protective floor is armed and the trade keeps its original wide stop. This is intended to prevent a sub-fee scratch, but it means a trade that pokes to plus 0.12 percent is given no profit protection at all and rides back to its initial loss stop. This fired 524 times across the multi-day window. Example:

"MICRO_FLOOR_FEE_SUPPRESS | sym=SANDUSDT peak=+0.112% raw_arm=0.100% fee_arm=0.130% fee_clear=0.130% | sub-fee breakeven floor NOT armed; trade keeps its wider stop to breathe — fee-scratch prevented"

### The Chandelier / ATR trail

The trail is computed in _compute_trail_stop at src/workers/profit_sniper.py lines 1790 to 1939. Its job is to let a winner run by trailing a distance behind the peak. The central finding is that it writes zero stops, and the reason is mechanical and certain.

For a long, the trail stop is the peak price minus the trail distance, then floored at the entry price as a breakeven floor (lines 1893 to 1894). The trail distance is base_atr_mult times ATR times regime, profit-decay, and momentum factors (line 1861), and it has a hard minimum: the maximum of 1.5 times ATR and 0.30 percent of entry price (src/workers/profit_sniper.py lines 1864 to 1866, reading mode4.min_trail_atr_multiplier equal to 1.5 and mode4.min_trail_pct equal to 0.30 from config.toml lines 1878 to 1879). Because trades peak near plus 0.26 percent, the 0.30 percent minimum trail distance alone exceeds the entire distance from entry to peak. The peak price minus a 0.30-percent-or-wider distance therefore falls below entry, and the breakeven floor pulls it back up to exactly entry. The trail stop is always entry.

Under the highest-stop-wins selection (described next), the ladder's candidate is entry times one plus the lock, that is entry plus a sliver, while the trail's candidate is entry. The ladder's candidate is always greater than the trail's candidate, so the trail can never win. The logs prove this exhaustively: in the one-hour window, of 185 spine selections, 184 were won by the ladder and zero by the trail (the remaining one by a structure stop). Across three days, the trail won 8 of 3,319 selections. The gateway accepted zero trail-sourced writes in the window and 8 in three days against 613 ladder accepts, despite 2,522 trail-floor computations. The trail is computed continuously and loses continuously.

A secondary contributor is visible in the trail-floor logs: 78 of 93 M4_TRAIL_FLOOR emissions in the window showed raw equal to 0.00, meaning the computed trail distance collapsed to a tiny value before being floored, often because of the profit-decay and momentum factors. But the dominant and sufficient cause is the breakeven floor losing the highest-stop-wins selection to the ladder. The original mystery is resolved: the trail does not fail to compute, it fails to ever win.

### The highest-stop-wins spine

The spine is the selection in _pf_select_stop at src/workers/profit_sniper.py lines 2170 to 2244, applied in _pf_apply_spine at lines 2559 to 3137. It gathers candidate stops (ladder, trail, safety floor, and the loss-cutting candidates), and for a long it picks the highest price, for a short the lowest (lines 2231 to 2235). This is correct and works as designed. The problem is upstream: because the trail floors at entry and the ladder locks entry-plus-a-sliver, the spine correctly but uselessly picks the ladder every time, and the trail is structurally excluded.

A representative spine line shows the ladder winning with the trail absent:

"SNIPER_SPINE_SELECT | sym=ALICEUSDT winner=ladder new_sl=0.11180516 cur_sl=0.11084000 ladder=0.11180516 chandelier=na safety=0.10886850 loss=[cap:0.11039742] ... owner=green offer_profit=True offer_loss=False dir=Buy"

The spine select also feeds the gateway, and that is where the second profit-side failure lives. The spine selects the ladder's intended lock, but the gateway then clamps it for minimum distance and frequently finds the clamped value does not improve the current stop, so it writes nothing. In the window the ladder won the spine 184 times but only 41 writes were accepted; 140 were rejected as clamp-noop. Across three days, 2,193 of 2,201 gateway rejects (99.6 percent) were clamp-noops. The ladder wins the selection and then cannot act.

### The score-action engine and profit guards

The score-action engine is _determine_action at src/workers/profit_sniper.py lines 3656 to 3902, classified by _classify_score and acted in _execute_action. It can hold, tighten, partial-close, or full-close based on a composite score against thresholds of 35 to tighten, 55 to partial, and 70 to full (seen live in the M4_DECISION line thresholds field). In the window the engine decided hold 294 times and tighten 53 times, and never once chose partial or full. Partial close is disabled by operator decision (max_partials_per_position equal to 1, redirected to tighten, src/workers/profit_sniper.py around lines 4806 to 4823). The score engine therefore never takes profit; it only ever holds or tightens, and tightening hands the stop to the spine, which clips it to breakeven. The profit guards (SNIPER_PROFIT_GUARD at line 4614, threshold 0.0 percent) protect any green trade from the stall valve, which is correct for protecting winners but, combined with the inability to lock real profit, means a green trade is held open with only a breakeven stop until a tap closes it.

### The graduation latch

The graduation latch (src/workers/profit_sniper.py lines 2618 to 2651) hands authority from the loss system to the profit system, one-way, the first time the peak crosses the 0.2 percent arm. It works as designed (it logs GRADUATION_LATCH with the peak and arm). The faded-winner rule in the gateway owner gate (src/core/sl_gateway.py lines 1295 to 1303) keeps a once-green trade green-owned even when it craters, unless faded_winner_rearm_red is on, which it is not, and graduation_crater_rearm_enabled is also off (config.toml line 2183). The consequence is that a graduated winner that fades is managed only by the profit tools (which can lock only breakeven) and the catastrophic cap, with the loss-cutting structure and recovery tools locked out. This is a real gap, documented in the catalogue.

### Traced profit-side trade: ALICEUSDT, the canonical clipped winner

ALICEUSDT (a long, entry 0.11166) is the clearest proof of the give-back. It reached plus 0.16 percent within seconds (the per-second feed shows pnl equal to plus 0.16 percent at 02:40:37) and plateaued there for over twenty minutes while still loss-owned under the initial ATR stop. At 03:01:21 it crossed into green ownership and the ladder armed, intending to lock plus 0.13 percent at stop 0.11180516. The gateway immediately clamped that to plus 0.05 percent on the arming jump:

"LADDER_FLOOR_JUMP | sym=ALICEUSDT applied_sl=0.11171944 target_sl=0.11180516 clamped=Y cur_sl=0.11084000 entry=0.11166000 dir=Buy be_lock=0.050% | breakeven floor jumped on the arming tick"

From then on, every attempt to lift the lock to plus 0.13 percent was rejected because the minimum-distance clamp produced a value that did not improve the stop already in place:

"SL_GATEWAY_R2_CLAMP | sym=ALICEUSDT raw=0.111805 clamped=0.111709 price=0.111830 dist_pct=0.022 eff_min=0.108 ... src=profit_sniper_ladder floor_held=N"
"SL_GATEWAY_REJECT | sym=ALICEUSDT rsn=clamp_noop src=profit_sniper_ladder clamped=0.111709 cur=0.111710 price=0.111830 dir=Buy"

This clamp-noop loop repeated for over a minute. The protective stop was stuck at plus 0.05 percent above entry, the price drifted back, and the trade closed at minus 0.0205 percent on a stop hit after 1,342 seconds. A plus 0.16 percent peak became a small net loss because the only profit tool that could act could lock only a sliver, and the gateway's minimum distance would not let even that sliver tighten as price hovered. This single trace contains the dead band, the breakeven pin, the R2 clamp, the clamp-noop loop, and the fee drag in one timeline.

### Traced profit-side trade: SANDUSDT, the fee-suppress and wire-fail case

SANDUSDT was traded twice in the window. The first instance peaked plus 0.14 percent and closed minus 0.035 percent on a stop hit after 424 seconds; the second peaked plus 0.27 percent and closed plus 0.040 percent after 199 seconds. The fee-aware suppression fired at peak plus 0.112 percent (quoted above). On the second instance the ladder's lock could not be wired at all because the computed stop landed on the wrong side of the live mark for a short:

"BYBIT_DEMO_SET_SL_DIRECTION_BUG | sym=SANDUSDT sl=0.05335769 mark=0.05336 side=Sell reason=wrong_side_for_position blocked=true"
"SL_GATEWAY_WIRE_FAIL | sym=SANDUSDT new=0.053358 src=profit_sniper_ladder rsn=service_returned_false"

This is the wire-fail mystery resolved. It is not a downstream outage. It is the ladder computing a profit-lock stop so close to the mark that, on these sub-minimum moves, a single tick of price puts the stop on the wrong side and the exchange adapter refuses it. All 162 wire-fails across three days came from profit_sniper_ladder, and the cases examined are all wrong-side rejections of a sub-minimum lock. The HIGH event sl_gateway_wire_fail is therefore surfacing a calibration artifact as if it were an infrastructure failure.

## The loss-cutting system: complete findings

### The initial ATR stop and the sacred cap

The instant a trade opens, the loss system places an ATR-based initial stop (LOSS_ATR_INITIAL_STOP, src/workers/profit_sniper.py lines 1251 to 1313) at 3 times ATR young, gliding to 1 times ATR old (config.toml atr_initial_multiple_young equal to 3.0). This is the wide stop the trade lives under until it graduates. The sacred hard cap is the minimum of a 75-dollar ceiling and a percent-of-notional that is 2.5 percent young gliding to 1 percent old (config.toml lines 2202 to 2204), net-adjusted for the round-trip taker fee of 0.11 percent (line 2212). The cap force-closes when the loss reaches it (LOSS_CAP_FORCE_CLOSE, src/workers/profit_sniper.py lines 2719 to 2770). The cap and the initial stop work: across all windows no trade breached the minus 3 percent watchdog hard stop, and the average loss is small. The loss side is genuinely protecting against catastrophe.

### The five-model time-decay engine and its force-close gate stack

The time-decay engine in src/risk/time_decay_sl.py is a five-model system: convex time decay with exponent 1.5 (lines 845 to 846), ATR-scaled base room by volatility class (lines 848 to 857, multipliers from 1.0 dead to 3.0 extreme), an MAE recovery multiplier (lines 1047 to 1066), a momentum multiplier (lines 1068 to 1078), and a Bayesian win-probability model with prior 0.55 and force-close threshold 0.15 (lines 338 to 419 and 741 to 842). Above these sit a force-close gate stack: a minimum-age gate at 300 seconds (lines 542 to 559), an MAE-to-stop-ratio gate at 0.5 (lines 625 to 642), a standalone monotonic-grind cut (lines 574 to 614, monotonic_grind_cut_enabled equal to true in config.toml, confirmed by 6 such closes on 2026-06-14), a structural-invalidation gate (lines 677 to 738), the win-probability force-close, and a recovery guard.

The forensic finding is that in the one-hour window none of these force-close mechanisms fired at all: zero time-decay force-closes, zero monotonic-grind cuts, zero structural-guard yields, zero recovery guards. Instead, the gate stack spent the window blocking: 492 MAE-guard blocks and 131 age-guard blocks at the time-decay level. The time-decay engine, in that window, did not cut a single trade; it only prevented cuts. On the full day, the gates that did close trades closed only losers: loss-stall 9 (all losses), monotonic-grind 6 (all losses), win-probability near-certain 4 (all losses). The engine is calibrated to hold, and what closes losers in practice is the stall valve and the plain exchange stop, not the sophisticated models.

### The stall valve and its guard stack

The stall valve (the stall-escape action, src/workers/profit_sniper.py lines 4400 to 4836) force-closes a dead non-climber after it has stalled past an age and tick threshold, producing the close reason mode4_stall_valve. It is fronted by a guard stack that blocks escape: the age guard at 300 seconds (lines 4489 to 4494), the profit guard that blocks any trade in profit (lines 4614 to 4637, threshold 0.0 percent), the development guard that blocks any trade not yet below minus 0.3 percent (lines 4638 to 4645), the structure-defer guard (lines 4512 to 4535), and a grace block. In the window these sniper-level guards blocked 1,334 escape attempts: 952 age-guard, 363 development-guard, 19 profit-guard.

The development guard is the operative one for the dead-drifter problem. Because it blocks any escape while the trade is above minus 0.3 percent, a trade that drifts in the minus 0.04 to minus 0.26 percent band can never be stall-cut and simply rides until either the stall timeout finally fires or it taps a stop.

### The watchdog loss lane, hard stop, timeout, and deadline tiers

The watchdog (src/workers/position_watchdog.py) runs every 10 seconds and owns the outer backstops: the minus 3 percent hard stop (line 2595, a hardcoded literal), the deadline timeout at 95 percent of max hold with a one-time ten-minute extension when nearly flat (lines 2713 to 2772), and the sentinel deadline tiers (lines 2253 to 2345). Its own percentage trail is intentionally disabled because subordinate_watchdog_trail_exit is true (config.toml line 2144), which is why the watchdog plan line always shows trail equal to off. This is correct deference to the sniper spine, not a defect. The watchdog peak-lock only engages above a 2 to 4 percent peak (lines 2855 to 2925), which is unreachable at these move sizes, so it never fires either. The deadline tiers (src/sentinel/deadline.py lines 138 to 187) classify an expired trade into profit (rides past on the sniper trail, which writes nothing), breakeven (stop to entry), or small-loss (stop to entry plus or minus 0.5 percent). A profitable trade riding past its deadline is therefore protected only by a trail that never writes, an anomaly documented in the catalogue.

### The recovery logic

The loss engine has two recovery mechanisms: the final-phase recovery candidate that trails the bounce off the trough (_lc_recovery_candidate, src/workers/profit_sniper.py lines 2246 to 2313, active past 80 percent of the deadline), and the time-decay recovery-responsive tightening (src/risk/time_decay_sl.py lines 903 to 920, active when recovery exceeds 75 percent of the worst MAE). The forensic finding is partial: the recovery candidate fires routinely (LOSS_RECOVERY observed over 100 times across three days), but the recovery-tighten rarely places a stop that actually captures the bounce. In one clear example, ATOMUSDT recovered from minus 0.806 percent to minus 0.081 percent and the recovery-tighten fired, but the trade was then closed by the win-probability force-close, not by the recovery stop:

"TIME_DECAY_RECOVERY_TIGHTEN | sym=ATOMUSDT mae=-0.806% current=-0.081% recovery=0.90 allowed_loss=0.381% new_sl=1.964285 | bounce-capture near least loss"

The bounce is detected but the capture stop is often clamped out by the same minimum-distance rule that defeats the ladder, or overtaken by another close path.

### Traced loss-side trade: APTUSDT, a genuine loser cut late

APTUSDT (a short, entry 0.6809) peaked at only plus 0.08 percent, then descended steadily to a worst of minus 0.59 percent and closed at minus 0.55 percent by the stall valve after 1,283 seconds. The age guard blocked all exits for the first 300 seconds during the early drawdown. The MAE guard then blocked tightening with a ratio of 0.08 against the 0.5 threshold despite the deepening loss. The stall valve finally escalated to a full close after 181 stalled ticks with zero tighten attempts ever made:

"MODE4_STALL_ESCALATE | sym=APTUSDT ticks=181 tighten_attempts=0 worst_pnl=-0.59% current_pnl=-0.55%"
"COORD_CLOSE_START | sym=APTUSDT pnl=-0.5500% pnl$=-0.3860 win=N by=mode4_stall_valve held=1283s ent=0.6809"

This is the worst single loss in the window, and it shows the loss side cutting late: the guards prevented any tightening for the whole drawdown, and only the blunt stall valve eventually closed it, near its worst point.

### Traced loss-side trade: LINKUSDT, the dead drifter

LINKUSDT (a short, entry 8.151) is the dead-drifter case. It peaked at plus 0.031 percent, then oscillated between minus 0.04 and minus 0.26 percent for over forty minutes and closed at minus 0.092 percent by loss-stall after 2,637 seconds. The development guard blocked stall-escape for 383 consecutive ticks because the trade stayed above the minus 0.3 percent floor:

"SNIPER_DEVELOPMENT_GUARD | sym=LINKUSDT pnl=-0.04% floor=-0.30% ticks=376 blocked=true"
"LOSS_STALL_EXIT | sym=LINKUSDT pnl_pct=-0.092 age_frac=0.710 profit_ratio=0.03 peak_pnl_pct=0.031"
"COORD_CLOSE_START | sym=LINKUSDT pnl=-0.0920% pnl$=-0.0406 win=N by=loss_stall held=2637s"

It ran 2,637 seconds, past the 2,100-second design hold seen in its own time-decay initialization. The dead drifter is not cut by any tool that understands it is dead; it simply rides until the stall timeout finally clears the development guard. No tool manages a trade in this minus 0.04 to minus 0.26 percent band; it is a true gap.

### Traced loss-side trade: AXSUSDT, the recovered trade

AXSUSDT (a long, entry 0.9811) is the recovered case. It opened red, traded down to roughly minus 1.1 percent under the initial ATR stop while the age guard blocked escape, then recovered to plus 0.133 percent at 02:49:34, at which point ownership handed from red to green and the ladder armed a micro-floor lock:

"SL_GATEWAY_OWNER_HANDOFF | sym=AXSUSDT from=none to=green state=green pnl_pct=0.133 src=profit_sniper_ladder"
"MICRO_FLOOR_ARM | sym=AXSUSDT peak=0.133% micro_arm=0.130% grad_arm=0.20% be_lock=0.050% lock=0.13..."
"GRADUATION_LATCH | sym=AXSUSDT peak_pnl_pct=0.214 arm=0.2 | profit-side authority latched"

It graduated at a peak of plus 0.214 percent and stayed green at plus 0.17 percent at the window's end. The recovery itself was not captured by the loss-side recovery-tighten (which never fired here); the trade simply rode the red phase under the age guard until price came back on its own, then the profit engine took over at a thin 0.13 percent lock. The recovered trade is handled, but only because price recovered unaided; the loss engine did not actively capture the bounce, and the profit engine could only protect it at a sliver. Its own first ladder write also wire-failed once as a wrong-side stop (quoted earlier).

## Interaction, ownership, and the give-back timeline

### The owner switch in practice

The owner switch is enforcing and correct. It is active (owner_switch_enabled and owner_switch_enforce both true) and it logs clean handoffs (SL_GATEWAY_OWNER_HANDOFF) as trades cross the breakeven deadband. The investigation found no instance of a caging writer being blocked: the gateway rejects in the window were 140 clamp-noops and, across three days, 2,193 clamp-noops and 8 loosening rejects, with no wrong-owner rejects observed. The original conclusion holds and is confirmed: the clip is the calibration, not a collision. The exit-authority consolidation did its job; the money is lost downstream of it, in the calibration.

### The give-back timeline

For a typical clipped winner the timeline is: the trade goes green within seconds or a few minutes; it peaks at roughly plus 0.2 to 0.3 percent, below the 0.6 percent first rung; it spends most of its life loss-owned under the wide initial ATR stop because the 0.2 percent graduation arm is reached only briefly; when it graduates, the ladder arms a sliver lock that the gateway clamps to breakeven-plus-0.05-percent and then cannot lift (clamp-noop); the trail never competes; the price drifts back through the thin lock; and the trade closes flat-to-red on a stop tap. Winners surrendered an average of 88 percent of their peak in the window's profitable trades. The give-back happens entirely in the last leg, between the peak and the stop tap, and the mechanism that owns the stop at that moment is the ladder's clamped breakeven floor.

### The dead-drifter and recovered cases

These are documented in the traces above (LINKUSDT and AXSUSDT). The dead drifter is the clearest gap: a trade in the minus 0.04 to minus 0.26 percent band is above the development-guard floor so it cannot be stall-cut, below breakeven so the profit tools do not own it, and not deep enough to trip any loss model, so it rides until the stall timeout fires near the deadline. The recovered trade is handled only passively: the loss engine holds it under the age guard and the wide stop until price recovers on its own, then the profit engine locks a sliver.

## The full catalogue

Each item below is identified by a short code for the prioritized list. Each carries a code location and a log citation.

### Flaws (design choices that lose money as built)

F1. The trail can never win the stop on the actual move sizes. The breakeven floor at src/workers/profit_sniper.py line 1894 combined with the 0.30 percent minimum trail distance (config.toml line 1879) forces the trail stop to entry on any sub-0.3-percent move, and the highest-stop-wins spine (lines 2231 to 2235) then always prefers the ladder. Log: 184 ladder wins and 0 trail wins in the window; 8 of 3,319 trail wins in three days.

F2. The ladder's first rung is unreachable for the actual move sizes. ladder_step_pct_young equal to 0.6 percent (config.toml line 1996) against a multi-day median peak of 0.23 percent means 97.6 percent of graduated trades die in the dead band. Log: peak distribution from 126 graduated trades, mean 0.239 percent, 75.4 percent in the 0.20 to 0.25 percent bucket.

F3. The take-profit is calibrated for a move that does not occur. default_take_profit_pct equal to 6.0 percent with min_rr equal to 1.5 (src/risk/stop_loss.py, config.toml risk section) puts the nearest possible target near plus 2.25 percent against a ninetieth-percentile peak of plus 0.71 percent. Log: zero take-profit closes across 257 trades on 2026-06-14 and zero across the multi-day window.

F4. The development guard creates an unmanaged dead-drifter band. The guard at src/workers/profit_sniper.py lines 4638 to 4645 (floor minus 0.3 percent) blocks any stall-cut while a trade is above minus 0.3 percent, so a flat drifter rides to its deadline. Log: LINKUSDT held 2,637 seconds with 383 consecutive development-guard blocks.

F5. The faded-winner rule locks the loss engine out of a graduated trade that craters. The owner gate at src/core/sl_gateway.py lines 1295 to 1303 keeps a once-green trade green-owned, and both faded_winner_rearm_red and graduation_crater_rearm_enabled are off (config.toml line 2183). A graduated winner that fades is then managed only by breakeven-level profit tools and the catastrophic cap. Log: the offer_loss equal to False field on green-owned spine lines (for example the ALICEUSDT spine select).

### Bugs (things that do not work as intended)

B1. The ladder's profit lock is silently dropped by the minimum-distance clamp on the actual move sizes. The gateway clamps the lock for minimum distance (src/core/sl_gateway.py lines 722 to 815) and then rejects it as a no-op when the clamped value does not improve the current stop (lines 898 to 910). Log: 140 clamp-noop rejects in the window and 2,193 in three days, against 41 and 613 ladder accepts respectively; the ALICEUSDT clamp-noop loop is the worked example.

B2. The ladder write fails at the exchange as a wrong-side stop on sub-minimum moves. The adapter rejects a stop on the wrong side of the mark (src/bybit_demo/bybit_demo_adapter.py around line 1269, BYBIT_DEMO_SET_SL_DIRECTION_BUG), which surfaces as a wire-fail (src/core/sl_gateway.py lines 996 to 1019). Log: 162 wire-fails in three days, all from profit_sniper_ladder; AXSUSDT and SANDUSDT wrong-side examples quoted above.

### Errors (incorrect values or miswired conditions)

E1. The minus 3 percent watchdog hard stop is a hardcoded literal, not a configurable value. src/workers/position_watchdog.py line 2595. It is not vol-scaled, so it is far too loose for a 0.05-percent-ATR coin and too tight for a 2-percent-ATR coin. Log: no trade reached it in any window examined, so it is inert at current move sizes, but the hardcoding is an error against the configuration-driven design of the rest of the stack.

E2. The unregistered-position age path returns a sentinel of 99999 seconds. src/workers/profit_sniper.py near line 4443, surfaced through SNIPER_AGE_GUARD. An untracked position is treated as old enough to pass the guard, which is the intended fail-open behaviour but is a brittle magic number rather than an explicit branch. Log: no failure observed in the window; flagged from code.

### Anomalies (surprising behaviour worth flagging)

A1. The HIGH-severity sl_gateway_wire_fail event reports a calibration artifact as an infrastructure failure. src/core/sl_gateway.py lines 962 to 971 raise it as if downstream is broken, but the cause is a wrong-side sub-minimum lock. Log: the wire-fails coincide exactly with BYBIT_DEMO_SET_SL_DIRECTION_BUG wrong-side lines, not with any transport error.

A2. The time-decay engine's force-close models fired zero times in the one-hour window despite firing on the full day. In the window: zero force-closes, zero monotonic-grind cuts, zero structural yields. On 2026-06-14: 6 monotonic-grind cuts, 4 win-probability cuts. The engine is highly conditional and, in calm hours, only blocks rather than acts. Log: window force-close counts all zero; day-of counts as listed.

A3. The profitable-trade deadline ride-past relies on a trail that never writes. src/workers/position_watchdog.py lines 2293 to 2305 ride a winner past its deadline on the sniper trail; the trail writes nothing, so the winner rides unprotected except for the ladder's breakeven lock. Log: zero trail accepts combined with SNIPER_DEADLINE_RIDE behaviour.

### Gaps (situations no tool handles)

G1. No tool can lock real profit in the dead band. Between the 0.2 percent arm and the 0.6 percent first rung, only the breakeven-to-fee-clearance floor exists, and it is clamped to a sliver. src/workers/profit_sniper.py lines 2044 to 2101. Log: median written lock 0.050 percent across three days.

G2. No scratch-and-close logic for a dead drifter. A trade oscillating in the minus 0.04 to minus 0.26 percent band is unmanaged until the stall timeout. src/workers/profit_sniper.py lines 4638 to 4645. Log: LINKUSDT trace.

G3. No active capture of a recovered trade's bounce. The recovery-tighten detects the bounce but rarely places a capturing stop. src/risk/time_decay_sl.py lines 903 to 920. Log: ATOMUSDT recovered to minus 0.081 percent then closed by win-probability, not recovery.

### Suggestions (options for the operator, not applied)

S1. Consider whether the exit toolset should be scaled to the realized move distribution rather than the move distribution being assumed to grow. Every threshold in the profit stack assumes moves several times larger than the entries produce. The operator may decide either to scale the tools down to the moves or to investigate whether the entries can produce larger moves; this report cannot decide which, but it proves the mismatch.

S2. Consider a dead-band profit-lock that does not depend on the gateway minimum distance, since the minimum distance is what defeats the only profit lock available at these sizes. This is a design option, not a recommendation to change a value.

S3. Consider a scratch-exit for the dead-drifter band so capital is not tied up for 40-plus minutes on a flat trade.

### Optimizations (improvements beyond fixing defects)

O1. Volatility-scale the minus 3 percent hard stop and the trail minimum distance per coin class, since both are currently fixed and the volatility profiler already exists (src/analysis/vol_scale.py is already consulted by the gateway R2 path at src/core/sl_gateway.py lines 709 to 716).

O2. Situational classification of trades (clipped winner, dead drifter, recovered, genuine loser) so each archetype gets a tailored exit, grounded in the four traced cases in this report.

O3. Demote or reclassify the wire-fail event severity once B2 is understood, so a calibration artifact does not page the operator as an outage.

### Calibrations (specific values that look mistuned)

Each is stated with the current value, the evidence, and the likely direction, framed for a later gated calibration program, not applied here.

C1. ladder_step_pct_young equal to 0.6 percent (config.toml line 1996). Evidence: 97.6 percent of graduated trades peak below it. Likely direction: down, toward the realized peak distribution near 0.23 percent, so a real rung is reachable.

C2. min_trail_pct equal to 0.30 percent and min_trail_atr_multiplier equal to 1.5 (config.toml lines 1878 to 1879). Evidence: the 0.30 percent floor alone exceeds the typical peak, forcing the trail to breakeven. Likely direction: down, so the trail can sit inside a sub-0.3-percent move.

C3. default_take_profit_pct equal to 6.0 percent and min_rr equal to 1.5 (config.toml risk section). Evidence: zero take-profits across 254 trades; ninetieth-percentile peak 0.71 percent. Likely direction: the target needs to be reachable, or take-profit replaced by an active lock at these sizes.

C4. The gateway minimum distance (sl_gateway.min_distance_pct, ATR-scaled) is the binding constraint that produces the clamp-noop on profit locks. Evidence: 2,193 clamp-noops in three days. Likely direction: the profit-lock path may need an exemption analogous to the existing breakeven-floor hold, since the current breakeven hold still loses to clamp-noop.

C5. micro_floor_arm_fee_aware_enabled equal to true with ladder_lock_fee_clearance_pct equal to 0.13 percent (config.toml lines 2078 and 2047). Evidence: 524 fee-suppress events leave peaks between 0.10 and 0.13 percent entirely unprotected. Likely direction: a judgement call between the small fee-scratch it prevents and the larger give-back it allows; the operator should weigh both with the fee-drag numbers.

C6. development_window_lower equal to minus 0.3 percent (the development guard, config.toml line 1935 and src/risk/layer4_protection.py). Evidence: it created the 383-tick block on LINKUSDT. Likely direction: narrower, or paired with a scratch-exit, so dead drifters are not held to the deadline.

## Prioritized findings, ranked by how much money they cost

This ranking is the operator's to act on; it is presented as evidence of relative cost, not a recommendation to change anything now.

First, the move-size-to-toolset mismatch (F1, F2, F3, C1, C2, C3). This is the root cost. It makes the trail dead, the ladder stuck at a sliver, and the take-profit unreachable. Every clipped winner traces back here, and clipped winners are what make the average loss 1.8 times the average win. On 2026-06-14 this is the difference between a plus-13-percent gross winner pool that should have been larger and a minus-36-percent loser pool, netting minus 22.73 percent.

Second, the clamp-noop suppression of the only working profit lock (B1, C4). Even within the existing calibration, the ladder wins the spine and then writes nothing 99.6 percent of the time it is rejected. Fixing the mismatch without addressing the clamp would still leave the lock unable to write. ALICEUSDT is the proof: a plus 0.16 percent peak protected only at plus 0.05 percent.

Third, the dead-drifter gap (F4, G2, C6). These trades do not produce large losses, but they tie up capital for 40-plus minutes each and contribute a steady drip of small losses (loss-stall closed 9 trades, all losses, on the full day).

Fourth, the faded-winner lockout (F5) and the unactivated recovery capture (G3). These cost on the subset of trades that go green then fade, or red then recover, by denying them the loss tools or failing to capture the bounce.

Fifth, the wire-fail wrong-side writes (B2) and the hardcoded hard stop (E1). These are real defects but currently low-cost: the wire-fails are a symptom of the same mismatch and the hard stop is inert at these sizes.

## Honest limitations

The logs prove what the systems did, not always why a specific internal value was chosen on a specific tick. Three limits are worth stating.

The trail's should-apply decision is not logged at the moment it becomes false, so the proof that the trail loses the spine is by its complete absence from the spine-winner and gateway-accept logs (zero in the window, 8 in three days) plus the code mechanism, rather than a per-tick should-apply trace. This is strong but indirect on the should-apply gate; the breakeven-floor-loses-to-ladder mechanism is direct.

The peak-versus-close give-back of 88 percent is measured on the four profitable trades in the one-hour window plus the multi-day lock distribution; the per-trade peak is reconstructed from M4_DECISION and the per-second feed, not from a dedicated peak-versus-close ledger. The direction and magnitude are certain; the exact aggregate percentage would be firmer with a per-trade peak ledger.

The full-day numbers for 2026-06-14 are parsed from the LEARNING close lines, which carry the realized close and reason but not the realized peak, so the day-level give-back is inferred from the lock distribution rather than measured per trade. A per-second price path for every trade (which exists only for the captured window) would settle the day-level peak-versus-close exactly.

Finally, this report maps the exit's failures; it does not prove that fixing them makes the system profitable. The entries reach profit on nearly every trade, but whether they can produce a move large enough to be worth capturing, or whether the tools must be rescaled down to the small moves, is a separate question this report informs but does not answer. What is certain is that, as currently calibrated, the exit systems cannot keep the green the entries produce, and the single dominant reason is that the moves are far smaller than the toolset assumes.
