# Exit System Code Map — A Complete, Code-Grounded Reference

This document maps the entire exit system of the trading-intelligence-mcp project, read directly
from the code, with exact file and line locations and exact current values. It is analysis only;
no code, config, or flag was changed to produce it. Its central purpose is to answer, from the
real formulas, exactly how a green trade gives back its profit, so a fix can be specified against
the exact function and the exact value rather than described by intent.

The working tree is on the main branch and nothing was modified.

## Plain-language summary

The exit system decides, on every price tick of an open trade, where the protective stop-loss
should sit, and then asks a gateway to place that stop on the exchange. The value of the stop is
computed from one quantity called R, the coin's recent volatility expressed as a percent of price
(its five-minute ATR as a percent). Every exit threshold — when the profit lock arms, where the
lock sits, how far the trail follows behind the running peak, and where the catastrophic hard stop
sits — is a fixed multiple of R, floored at the round-trip fee. The sniper computes the desired
stop each tick; a separate gateway then enforces four rules (never loosen, keep a minimum distance
from price, limit the step size, and rate-limit how often it writes) and can clamp, degrade, or
reject the stop; and an owner hierarchy decides which engine is allowed to write the stop given
whether the trade is currently in profit (green) or in loss (red). The geometry decides what value
the stop should be; the hierarchy decides who is allowed to write it.

The one-paragraph answer to the give-back. A green trade gives back its profit chiefly because the
profit lock is held a fixed distance of one half of R behind the running peak, and that distance
never tightens as profit grows. The trail distance is the formula trail equals peak minus trail_r
times R, with trail_r equal to 0.5 (src/analysis/vol_scale.py line 176; the coefficient is at
config.toml line 2293). For the proven HOMEUSDT example, R was 0.573 percent, so the lock sat
0.287 percent (one half of R) behind the peak for the trade's whole life; the trade peaked at plus
0.6953 percent and could therefore only ever lock about plus 0.41 percent, and after the further
loss of the gap between the throttled peak snapshot and the true peak, and the exchange fill, it
closed at plus 0.1485 percent — a total give-back of 0.547 percent on a trade that never went red.
The staged rungs that would secure more profit are set at 1.5 R and 3 R (about 0.86 percent and
1.72 percent for this coin) and were never reached, so the trail term was the only active lock.
The single responsible coefficient is trail_r equal to 0.5, compounded by the structural fact that
no adaptive-exit threshold tightens as the trade's profit grows.

## The movement unit R — how it is measured, cached, smoothed, and flows

R is the coin's Average True Range expressed as a percent of price, on five-minute candles. There
are two distinct sources of R in the code, used for different purposes; this is important and easy
to miss.

The canonical R is produced by the volatility profiler as the field atr_pct_5m. In
src/analysis/volatility_profile.py, the method _compute (line 190) fetches five-minute technical
analysis from the TA cache (lines 198 to 200) and reads the precomputed NATR-14 directly:
atr_pct_5m equals the natr_14 value (line 202), falling back to absolute ATR over price times 100
when NATR is missing (lines 205 to 213). The value is rounded to four decimals when stored
(line 258). The underlying math lives in src/analysis/indicators/volatility.py: the ATR uses true
range, defined as the maximum of high minus low, the absolute high minus previous close, and the
absolute low minus previous close (lines 61 to 67), with a period of 14 (line 41), seeded by a
simple mean of the first fourteen true ranges (line 70) and then smoothed by Wilder's recursive
formula, previous times thirteen plus the new true range, divided by fourteen (line 74). This is
classic Wilder ATR, not a simple or standard exponential average. NATR is that ATR divided by the
latest close, times 100 (volatility.py line 196). So canonical R equals NATR-14 on five-minute
candles.

The canonical R is cached in two layers. The TA cache (src/analysis/ta_cache.py) caches the raw
five-minute analysis with a time-to-live whose class default is 90 seconds (line 25) and whose
live value is 120 seconds per the in-code comment (lines 23 and 43); its key is symbol plus
timeframe and deliberately excludes the candle limit (lines 137 and 140). The profiler then has its
own per-symbol profile cache with a base time-to-live of 120 seconds (cache_ttl_seconds at
settings.py line 3818) plus a deterministic per-symbol jitter of plus or minus 30 seconds
(jitter_range_seconds at settings.py line 3821; the jitter computation is at volatility_profile.py
line 142, the effective time-to-live at line 144). The net effect is that canonical R is
step-stable per symbol over an effective band of roughly 90 to 150 seconds. There is no smoothing
of canonical R beyond the Wilder smoothing inside the ATR itself.

The second source of R is re-derived inside the sniper, and this is the R that drives the adaptive
profit ladder. In src/workers/profit_sniper.py, the ladder computes raw R as atr_value divided by
entry times 100 (line 2065), where atr_value is an absolute ATR resolved from a live five-minute
ATR, the ATR captured at entry, or a percent-of-price fallback (the resolver _pf_effective_atr at
lines 1729 to 1748; the fallback coefficient atr_zero_fallback_pct is 0.5 at settings.py line
4800). The adaptive ladder is fed real R only when the ATR source is live or entry-captured, never
the fabricated fallback (line 705). This sniper R has its own 30-second freshness cache (the check
at line 1558) and is then smoothed by a per-symbol exponential moving average in _adaptive_r (lines
1804 to 1819): the smoothed R equals alpha times the raw value plus one minus alpha times the
previous value (line 1817), with alpha equal to r_smoothing_alpha of 0.3 (config.toml line 2279,
settings.py line 4570). So the ladder's R is a 0.3-alpha exponential average of an absolute
ATR-over-entry measurement refreshed about every 30 seconds, while the catastrophic hard stop uses
the profiler's canonical atr_pct_5m unsmoothed (position_watchdog.py lines 2238 to 2241).

R's observed range is not pinned by any constant in the code; it is inferred from the volatility
class thresholds (dead below 0.05, low below 0.15, medium below 0.40, high below 1.00, else
extreme; settings.py lines 3823 to 3826) and from the worked examples in the configuration
comments. In the live data examined, R values for traded coins fell roughly between 0.1 percent and
2.4 percent, with the proven HOMEUSDT example at 0.573 percent.

## The R geometry — every formula and value

All adaptive-exit geometry is a set of pure functions in src/analysis/vol_scale.py (the block at
lines 100 to 195), taking R and the configuration and returning a threshold in percent. Every
coefficient is read from the adaptive_exit configuration section. The layer is live: config.toml
line 2270 sets enabled to true, which overrides the dataclass default of false at settings.py line
4559.

The fee floor is round_trip_fee_pct times fee_floor_buffer, which is 0.11 times 1.0, equal to 0.11
percent (formula at vol_scale.py lines 138 to 140; round_trip_fee_pct is 0.11 at config.toml line
2273, fee_floor_buffer is 1.0 at config.toml line 2274). This floor is the spine of the design:
every profit threshold is held at or above it so a locked win clears cost.

The arm is the profit the trade must reach before the lock, the trail, and graduation engage. It is
the larger of arm_r times R and the fee floor, bounded between arm_min_pct and arm_max_pct (formula
at vol_scale.py lines 150 to 153). The coefficient arm_r is 0.5 (config.toml line 2282), the floor
arm_min_pct is 0.0 and the ceiling arm_max_pct is 1.0 (config.toml lines 2283 and 2284). So on a
coin with R of 0.573 percent the lock does not arm until the trade reaches plus 0.287 percent
profit.

The profit lock is the central give-back mechanism and is computed by profit_lock_pct
(vol_scale.py lines 156 to 185). It returns nothing until the running peak reaches the arm. Once
armed, it is the maximum of three quantities: the fee floor; the trail, which is the running peak
minus trail_r times R (line 176); and a staged value. The staged value is the fee floor once the
peak crosses the first rung at rung_r index zero times R, which is 1.5 R (lines 180 to 181), and it
becomes secure_at_3r_r times R, which is 1.5 R, once the peak crosses the middle rung at rung_r
index one times R, which is 3 R (lines 178 to 179). The final lock is bounded below by the fee
floor and above by lock_max_pct (lines 182 to 183); lock_max_pct is 0.0, which by the convention of
the _bounded helper (lines 122 to 130, where a non-positive upper bound means no ceiling) means the
lock is bounded only naturally by the peak. The rung list is rung_r equal to 1.5, 3.0, 5.0
(config.toml line 2287), and secure_at_3r_r is 1.5 (config.toml line 2288). The third rung value of
5.0 is loaded but never referenced in the formula, so it is currently inert.

The critical fact for the give-back is the trail distance. The trail sits exactly trail_r times R
behind the running peak, with trail_r equal to 0.5 (config.toml line 2293, formula at vol_scale.py
line 176). This distance is fixed for the trade's life; it does not narrow as profit grows. The
only ways the lock changes as a trade climbs are that its level ratchets up with the monotonic peak
and that two discrete step-ups can occur, at peak equal to 1.5 R (a free-roll to the fee floor) and
at peak equal to 3 R (a secure of 1.5 R). Everything else is fixed by R and the fixed 0.11 percent
fee for the trade's entire life. There is no profit-magnitude-dependent tightening anywhere in the
adaptive-exit geometry. This was confirmed by reading the formula and all of its consumers.

The hard stop is the wide catastrophic backstop, hard_stop_r times R bounded between
hard_stop_min_pct and hard_stop_max_pct (vol_scale.py lines 191 to 194). The coefficient
hard_stop_r is 9.0, the floor is 2.5 percent and the ceiling is 10.0 percent (config.toml lines
2301 to 2303). When the adaptive layer is off, a legacy flat hard stop of 3.0 percent applies
(position_watchdog.py line 2244). The hard stop is the loss-side backstop, not part of the green
give-back.

There is also a separate, older minimum-distance helper, min_distance_for_class (vol_scale.py lines
50 to 97), which the gateway uses, not the profit geometry. It returns the maximum of an absolute
floor and the five-minute ATR times a multiplier, capped by a per-class ceiling; the multiplier
default is 0.5, the absolute floor default is 0.05 percent, and a cold-ATR fallback is 0.3 percent.
This helper reads the gateway configuration, not the adaptive_exit configuration.

## The ladder and trail in the sniper — how the geometry is used per tick

The sniper consumes the geometry on each tick through two parallel mechanisms: the R-based adaptive
ladder lock, and a separate Chandelier trail. They compete, and the higher (more protective) stop
wins.

The adaptive ladder lock is computed in _compute_ladder_floor (src/workers/profit_sniper.py,
definition near line 1972). It reads the running peak as state.peak_pnl_pct (near line 1995),
derives and smooths R (line 2065), and calls vol_scale.profit_lock_pct with the peak and R to get
the lock percent. It converts that to a stop price and applies it only if it tightens the existing
stop (the tighten-only ratchet check near line 2074). It emits the LADDER_ADAPTIVE log line with
the peak, R, arm, lock, and stop (near line 2079), throttled to at most once per sixty seconds per
symbol (the throttle near line 2077). This throttle matters: the logged peak can be below the true
peak the engine actually saw, because the engine computes on every tick but only logs every sixty
seconds.

The Chandelier trail is computed in _compute_trail_stop (definition near line 1821). Its distance
behind the peak is base_atr_multiplier times the absolute ATR times a regime factor times a profit
decay times a momentum factor (the product near line 1892). The regime factors are trending 1.3,
ranging 0.7, volatile 1.0, dead 0.6, and a balanced default of 0.85 (the table near lines 49 to
55). The profit decay is one over one plus 0.2 times the extension in ATR units, floored at 0.50.
The momentum factor steps from 1.1 down to 0.6 as momentum rises. The trail is floored at the
entry price (breakeven) on the long side near lines 1924 to 1925. This Chandelier trail is a
different and parallel mechanism from the adaptive profit lock; some trades are governed by it
rather than by profit_lock_pct, which matters for the give-back examples below.

The active stop is selected by the highest-stop-wins spine: the winner is the maximum stop price
for a long and the minimum for a short (near lines 2315 to 2319). The graduation latch hands a
trade to profit ownership once its peak reaches the graduation arm of 0.2 percent
(min_profit_to_arm_ladder_pct, the check near lines 2744 to 2745).

What happens to a green trade's stop as it climbs and pulls back: as the peak rises, the lock level
ratchets up monotonically, always held trail_r times R behind the highest peak seen. When price
pulls back from a peak by more than that fixed distance, it reaches the resting lock and the trade
closes there. Because the distance is fixed at one half of R and never tightens, a trade that
makes a modest peak and then pulls back surrenders that half-of-R distance plus whatever additional
gap exists between the last ratcheted stop and the true peak.

## The gateway — how the computed stop is placed, clamped, degraded, or rejected

The gateway (src/core/sl_gateway.py) receives the sniper's desired stop and enforces four rules in
order, plus degrade and guard steps, before writing to the exchange. The public entry point is a
thin wrapper that delegates to the rule engine and emits placement forensics; the rule engine
itself is unchanged by that wrapper.

Rule one, tighten-only, never loosens a stop; a proposed stop that would move away from price is
rejected. This rule is never bypassed by any caller.

Rule two, the minimum-distance clamp, keeps the stop at least an effective minimum distance from
price. The effective minimum is the maximum of the absolute floor of 0.05 percent and the
five-minute ATR times 0.5, capped by a per-class ceiling (the class ceilings are dead 0.30, low
0.50, medium 1.00, high 2.00, extreme 3.50, at config.toml lines 1076 to 1081; the multiplier
min_distance_atr_multiplier is 0.5 at config.toml line 976; the base min_distance_pct is 0.3 at
config.toml line 940). When a computed stop is inside this distance, rule two does not reject it
outright; it clamps it to the closest valid boundary. Two exemptions hold an armed lock through the
clamp rather than dropping it: the breakeven-floor exemption (r2_breakeven_floor_enabled true at
config.toml line 984), which holds a stop down to the trade's breakeven price for trusted sources,
and the profit-lock-floor exemption (r2_profit_lock_floor_enabled true at config.toml line 994,
whose dataclass default is false), which holds an armed R-derived lock at its value inside the
minimum distance so it writes rather than being dropped as a no-op.

The fresh-mark degrade is the mechanism by which a computed lock can fail to be placed on a fast
move. On a near-the-money stop it re-validates the stop against the freshest exchange mark price
(the same field the exchange enforces against), gated by a cheap pre-check using
fresh_mark_recheck_distance_mult of 2.0 (config.toml line 1013); if the stop is unplaceable against
the fresh mark, it degrades the stop to the closest placeable boundary rather than wiring an
invalid value, and if even that boundary cannot improve on the existing stop it holds the existing
stop as a no-op (r2_fresh_mark_degrade_enabled true at config.toml line 1007). This is a safety
mechanism that prevents wire failures; it is not a defect.

Rule three, the maximum-step rule, limits how far a single write can move the stop, with a step
cap of 0.25 percent (max_step_pct at config.toml line 947); the profit-lock and breakeven sources
bypass rule three so a legitimate large protective tighten is not throttled, but they still pass
rules one, two, and four.

Rule four, the rate limit, restricts how often the stop is written per symbol, with a window of 30
seconds (rate_limit_seconds at config.toml line 949). There is also an inert, source-aware
profit-lock window, profit_lock_rate_limit_seconds, currently 30 seconds and therefore identical to
the base (config.toml line 956); it would let the profit-lock lane write faster than other sources
if lowered, but at its current value it changes nothing.

After the rules, a terminal wrong-side guard refuses to wire any stop still on the wrong side of
price, as a final backstop. The catastrophic cap is not a gateway rule but a force-close executed
outside the gateway; within the gateway, the cap sources are always admitted by the owner gate and,
because tighten-only is never bypassed, the cap can only ever tighten.

## The owner hierarchy — who writes the stop when

The gateway also runs a trade-state owner hierarchy that decides which engine is allowed to write
the stop. There are four buckets, defined in configuration. The Head is the catastrophic cap,
sources loss_cap and loss_cap_emergency (config.toml line 1066), always admitted regardless of
trade state. The green owner is the profit engine, sources profit_sniper_ladder,
profit_sniper_trail, profit_sniper_lock, profit_sniper_breakeven, and micro_floor (config.toml line
1067), which owns the stop while the trade is in profit. The red owner is the loss engine, sources
time_decay, loss_structure, and loss_recovery (config.toml line 1068), which owns the stop while
the trade is in loss. The advisory systems, sources such as brain_tighten, the watchdog scorers,
and the sentinels (config.toml line 1069), do not write the stop directly; they advise the owning
engine. Two always-allowed sources, loss_atr_initial and safety_sweeper, are admitted regardless
(config.toml line 1070).

The owner switch is live and enforcing: owner_switch_enabled and owner_switch_enforce are both true
(config.toml lines 1032 and 1033), which is the opposite of the dataclass defaults of false. Trade
state, green or red relative to entry, decides which engine writes; the hand-off at the breakeven
boundary uses a deadband of 0.05 percent (breakeven_deadband_pct at config.toml line 1050) with
hysteresis so ownership does not thrash exactly at breakeven. The Head only seizes a green trade
when head_only_seizes_green is true (config.toml line 1058), and a once-green trade that craters
stays green-owned because faded_winner_rearm_red is false (config.toml line 1046). The hierarchy
decides who writes the stop; the geometry decides what value it is.

## The give-back mechanism — the central finding, answered from the code

The proven example is HOMEUSDT, a trade that was in profit for its entire life. From the live logs:
its true peak was plus 0.6953 percent (the maximum per-tick profit in the price path), and it closed
at plus 0.1485 percent by a stop hit, a total give-back of 0.547 percent. Its adaptive ladder
snapshot recorded a peak of 0.622 percent, an R of 0.573 percent, an arm of 0.287 percent, and a
lock of 0.336 percent. The lock value reproduces the formula exactly: 0.336 equals the peak 0.622
minus trail_r times R, which is 0.5 times 0.573, equal to 0.287 (vol_scale.py line 176). So the
trail term was binding and the formula is confirmed against the live value.

The give-back decomposes into three parts, only the first of which is a tunable coefficient in this
repository.

First, the by-design trail distance, 0.287 percent. The lock is held one half of R behind the
peak. At the true peak of plus 0.6953 percent the best lock the geometry could ever place was about
plus 0.41 percent (0.6953 minus 0.287). This 0.287 percent is surrendered behind the peak by
design and is the dominant, controllable contributor. The responsible coefficient is trail_r equal
to 0.5 (config.toml line 2293).

Second, the gap between the throttled peak snapshot and the true peak, about 0.07 percent. Because
the LADDER_ADAPTIVE log and the ratchet are observed at the engine's cadence and the log is
throttled to once per sixty seconds (profit_sniper.py near line 2077), the resting lock reflected a
peak of about 0.622 percent rather than the true 0.6953 percent, so the placed lock was about plus
0.34 percent rather than plus 0.41 percent. This is a measurement and cadence gap, not a single
coefficient.

Third, the slip between the placed stop and the realized fill, the remainder of the 0.547 percent.
The placement forensic for this trade shows the lock placed with a forgone tightening of only 0.066
percent and an outcome of placed, so the placeability mechanism is not the cause; the lock did
write. The realized close of plus 0.1485 percent is below the placed lock level, which is exchange
fill behavior on a market-trigger stop, not a coefficient visible in this repository.

The structural fact underlying all of this is that no adaptive-exit threshold tightens as the
trade's profit grows. The trail distance is a fixed one half of R; the staged secure rungs sit at
1.5 R and 3 R, which for this coin are about 0.86 percent and 1.72 percent, and the trade's peak of
0.6953 percent never reached even the first rung, so the only active lock term for its entire life
was the trail. A modest winner is therefore locked at peak minus one half of R with no progressive
securing, and any pullback past that fixed distance closes it. The single value a fix would target
to capture more of a green trade's peak is trail_r equal to 0.5 at config.toml line 2293, optionally
together with the rung spacing so that the secure rungs engage on sub-one-percent movers.

Two honesty notes on the other cited examples. BELUSDT, which peaked plus 0.515 percent and closed
minus 0.392 percent, was not governed by the adaptive ladder at all; it has no LADDER_ADAPTIVE line
and closed via the legacy Chandelier trail path (_compute_trail_stop), so its round-trip through
zero is governed by the ATR-based Chandelier distance, not by profit_lock_pct. SPCXUSDT, which
peaked plus 0.284 percent, is the one regime where the fee floor of 0.11 percent was binding rather
than the trail, because its trail term was smaller than the fee; trail_r is not the responsible
term there. So the trail coefficient is the dominant give-back driver for adaptive-ladder winners
like HOMEUSDT, but the Chandelier trail and the fee floor govern other cases and must be considered
separately by any fix.

## The configuration — every exit value and where it lives

The adaptive-exit values live in the adaptive_exit section of config.toml (lines 2269 to 2308),
loaded into the AdaptiveExitSettings dataclass (settings.py lines 4540 to 4598) by the
_build_adaptive_exit builder, which filters by field name so the rung list is preserved, and
validated by _validate_adaptive_exit. The current values are: enabled true (line 2270, dataclass
default false); round_trip_fee_pct 0.11 (line 2273); fee_floor_buffer 1.0 (line 2274);
r_smoothing_alpha 0.3 (line 2279); arm_r 0.5 (line 2282); arm_min_pct 0.0 (line 2283); arm_max_pct
1.0 (line 2284); rung_r 1.5, 3.0, 5.0 (line 2287); secure_at_3r_r 1.5 (line 2288); lock_max_pct 0.0
(line 2289); trail_r 0.5 (line 2293); hard_stop_r 9.0 (line 2301); hard_stop_min_pct 2.5 (line
2302); hard_stop_max_pct 10.0 (line 2303); dead_drifter_enabled true (line 2306, dataclass default
false); dead_drifter_age_fraction 0.70 (line 2307); dead_drifter_min_move_r 1.0 (line 2308).

The gateway values live in the sl_gateway section of config.toml, loaded into SLGatewaySettings
(settings.py line 1433 onward) by _build_sl_gateway and validated by _validate_sl_gateway. The
current values are: enabled true (line 935, dataclass default false); min_distance_pct 0.3 (line
940); max_step_pct 0.25 (line 947); rate_limit_seconds 30 (line 949); profit_lock_rate_limit_seconds
30 and therefore inert (line 956); min_distance_atr_multiplier 0.5 (line 976);
min_distance_abs_floor_pct 0.05 (line 977); the per-class minimum-distance ceilings dead 0.30, low
0.50, medium 1.00, high 2.00, extreme 3.50 (lines 1076 to 1081); r2_breakeven_floor_enabled true
(line 984); r2_profit_lock_floor_enabled true (line 994, dataclass default false);
r2_fresh_mark_degrade_enabled true (line 1007); fresh_mark_recheck_distance_mult 2.0 (line 1013);
owner_switch_enabled true (line 1032, dataclass default false); owner_switch_enforce true (line
1033, dataclass default false); advisory_enforce false (line 1034); faded_winner_rearm_red false
(line 1046); breakeven_deadband_pct 0.05 (line 1050); head_only_seizes_green true (line 1058); and
the four bucket source lists at lines 1066 to 1070. The placement_forensic_enabled observability
gate is true by default in the ObservabilitySettings dataclass.

The R measurement values live in the analysis.volatility_profile section (config.toml lines 3085 to
3101, dataclass settings.py lines 3810 to 3831): enabled true; cache_ttl_seconds 120.0;
jitter_range_seconds 30; and the class thresholds dead 0.05, low 0.15, medium 0.40, high 1.00.

Everything the give-back depends on is already a tunable configuration key; a fix to the trail
distance would change trail_r at config.toml line 2293 with no new key required, and a fix to make
the secure rungs engage earlier would change rung_r at config.toml line 2287.

## The verification harnesses that exist

Six exit-related scripts sit at the repository root. simulate_adaptive_exit_replay.py replays the
R-based geometry against an old one-hour log window, but it hardcodes its own R-multiples,
including a trail multiple of 1.0 at line 48, which differs from the live trail_r of 0.5, so its
numbers are not the live geometry. simulate_trail_recalibration_replay.py is the faithful harness
that drives the real gateway per tick on the captured twenty-eight-hour window and sweeps candidate
trail and cap values; it is the right tool to prove a trail fix against real trades.
verify_fresh_mark_degrade.py is the behaviour-neutrality test for the gateway degrade path and
passes nine of nine. verify_price_path.py and verify_adaptive_exit_wiring.py verify the price-path
logger and the adaptive-exit wiring. simulate_fix_verification.py reproduces the live placeability
and cadence situations and asserts the gateway responds correctly. A trail-distance fix would be
proven by extending simulate_trail_recalibration_replay.py to show that a smaller trail distance
captures more of the green-trade peaks without over-tightening the small movers, then confirmed live.

## Honest gaps and surprises

The third ladder rung value of 5.0 is loaded but never referenced in the lock formula; only the
1.5 R and 3 R rungs gate the ladder (vol_scale.py lines 178 and 180), so the documented third rung
is currently inert.

Several master switches default to false on their dataclasses but are turned on by config.toml at
runtime, and several module docstrings still describe the layer as dormant or off by default; the
live configuration is the authority. These are adaptive_exit.enabled, adaptive_exit
dead_drifter_enabled, sl_gateway.r2_profit_lock_floor_enabled, and the owner switch enable and
enforce flags. The give-back analysis here assumes the live configuration, in which the adaptive
ladder is active.

R's underlying candle window is not strictly determined by the profiler's request because the TA
cache key omits the candle limit, so the exact number of candles behind a given R reading depends
on which caller populated the cache entry. R's typical range is inferred from the class thresholds
and worked examples rather than pinned by a constant.

The third contributor to the HOMEUSDT give-back, the slip between the placed stop and the realized
fill, is exchange fill behavior on a market-trigger stop and is not a coefficient in this
repository; it cannot be tuned here. The dominant controllable contributor remains the trail
distance, trail_r equal to 0.5.

Finally, a stale comment: the header near profit_sniper.py line 84 describes the anti-greed
backstop as firing at a peak of 0.10 percent with a 75 percent pullback, but the actual code and
configuration require a peak of 5.0 percent for that rule (the check near line 3926); the 0.10
percent figure is only the floor for computing the pullback percentage. The anti-greed backstop
therefore does not act on sub-one-percent winners like the give-back examples.
