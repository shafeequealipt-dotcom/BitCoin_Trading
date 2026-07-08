# Entry-Quality Gaps and the H1/M5 Timeframe Investigation: Findings

This is the single working notes file for the four-item investigation. It records, per item, the symptom, the reproduction against real bybit_demo data, the root cause or null result, and the gate decision. Every indicator value is labeled with its timeframe (five-minute or one-hour). No fix is applied before its decision gate is approved by the operator.

## Phase 0: baseline and scope

### Git and environment baseline

The working tree is on `main`. There are no unpushed commits and no unmerged side-branches. The only uncommitted changes are runtime files the trading system writes during operation (`data/layer_state.json`, `data/logs/layer1c_full.jsonl`), which the project rules explicitly permit. Several leftover `.bak_*` backup files from prior sessions sit untracked in the tree; they are out of scope for this task and are left untouched.

The active exchange adapter is `bybit_demo` (config.toml line 22, `mode = "bybit_demo"`, with `testnet = false`). This matches the operator decision to anchor all analysis on the live demo path.

### Analysis cutoff and dataset

The live database is actively being written by the running system (trade_intelligence grew from 2,767 rows at planning time to 2,888 during Phase 0). To keep every query reproducible, all analysis uses a frozen cutoff of `trade_closed_at <= '2026-05-26T18:00:00'` and `exchange_mode = 'bybit_demo'`.

At that cutoff the canonical dataset is 1,812 closed trades, 808 wins and 1,004 losses, a 44.6 percent win rate, spanning 2026-05-08 to 2026-05-26. Entry-feature availability on that set: the entry snapshot (`entry_regime`, `entry_rsi`, `entry_atr_pct`, `entry_macd_hist`) is present on 1,801 rows; `entry_score` on 521; `claude_confidence` on 527; `setup_id` (the ensemble-vote join key) on 486. The subset whose entries can be reconstructed from five-minute candles (closed on or after 2026-05-25, where the `klines` M5 history begins) is 371 trades.

Protected trade tables and their row counts at Phase 0 (read-only throughout; never modified): trade_intelligence 2,888, trade_log 3,354, trade_history 1,671, trade_thesis 3,225, ensemble_votes 170,201, klines 45,008, coin_regime_history 17,983, plus positions / position_snapshots / thesis_events. The task's protected-table list names `thesis_store` and `virtual_positions`; no tables exist under those exact names (the real tables are `thesis_events`, `positions`, `position_snapshots`), but all trade tables are treated as read-only regardless.

### Linked entry-feature dataset definition

The dataset that grounds Items 1 and 2 is, per trade row in `trade_intelligence` at the frozen cutoff: the outcome (`win` 0/1, `pnl_pct`, `pnl_usd`, `hold_seconds`, `closed_by`), the persisted entry snapshot (`entry_regime`, `entry_rsi` on five-minute, `entry_atr_pct` on five-minute, `entry_macd_hist` on five-minute), the entry decision context (`entry_score`, `claude_confidence`), and the ensemble vote balance (`supporting_count`, `opposing_count`, and a `setup_id` join to `ensemble_votes`). Entry-time volume ratio and entry-time ADX are not persisted; they are reconstructed where needed from `klines` (five-minute since 2026-05-25, one-hour since 2026-05-18) or read from `coin_regime_history` (one-hour, per-coin). The close-time columns `rsi`, `adx`, `atr_pct`, `volume_ratio` are explicitly NOT used as entry features because they are sampled at close and are contaminated by the trade's own outcome.

### Four items re-confirmed against current code

All four items still describe the current code as of this session.

- Item 1 premise holds: the APEX gate (`src/apex/gate.py`) has no volume or ADX hard filter. Its only entry-quality reject is Check 4 (lines 161-182), which fires only when X-RAY confidence and setup score and expected reward-to-risk are all at or below thresholds defaulting to 0.0 (inert). Check 6 is a five-minute reentry cooldown (timing, not entry-quality).
- Item 4 premise holds: `volume_sma_ratio = current_volume / SMA(volume, 20)` (`src/analysis/indicators/volume.py:159-172`); the close-time `volume_ratio` field is right-skewed.
- Item 2 premise holds: the per-trade entry snapshot is limited to regime, RSI, ATR percent, MACD histogram (`src/tias/collector.py:340-348`).
- Item 3 premise holds: the regime/trend thesis is one-hour (`src/strategies/regime.py:90`); the X-RAY structure that supplies stop-placement levels is one-hour (`src/analysis/structure/mtf_confluence.py:7-8`); the recommended stop is derived from the five-minute volatility class times a trend multiplier of 0.9 that tightens it (`src/analysis/volatility_profile.py:62-78`); the one-hour ATR is computed but unused in the stop formula; APEX floors the stop at `max(0.2, recommended_sl_pct*0.6)` capped at 5.0 (`src/apex/optimizer.py:1079-1084`).

The retracted items (the "fake trend below ADX 20" claim and the "INJ bought at ADX 8-14" claim) are understood and excluded; the regime detector's one-hour ADX trend floor and trend-labeling logic are not touched.

## Phase 4a: Item 4, the volume-ratio scale (gate verdict: explained, no mis-scaled-threshold defect)

### Symptom

The volume-ratio field sits below 0.8 for most readings, including winners, which made an earlier volume claim hard to verify and suggested the field might be on a different scale than a 0-to-1 threshold would assume.

### The field's true definition and scale (the deliverable Item 1 needs)

`volume_sma_ratio = current_volume / SMA(volume, 20)` (`src/analysis/indicators/volume.py:159-172`). It is a dimensionless ratio centered at 1.0, where 1.0 means the current candle's volume equals the trailing twenty-candle average. It is timeframe-agnostic as a function; the timeframe is whatever candles the caller passes. It is computed on five-minute candles in the scanner, scorer, APEX assembler, and the close-time collector (`src/tias/collector.py:460`, `timeframe=TimeFrame.M5`), and on one-hour candles inside the regime detector (`src/strategies/regime.py:90-122`). The distribution is right-skewed: because the twenty-period average is inflated by rare large volume spikes, a typical reading sits well below 1.0. Reproduced on the bybit_demo close-time five-minute field at the frozen cutoff: mean 1.096, with 63.7 percent of readings below 0.8 and 49.8 percent below 0.5, and 15.2 percent above 2.0. So "mostly below 0.8" is the expected right-skew of a current-over-average ratio, not a scaling error. The practical consequence for Item 1: a raw cutoff such as 0.8 is not a meaningful "low volume" line, because most ordinary readings fall below it; only the upper tail (above roughly 1.3) marks genuinely above-average volume.

### The forming-candle observation (a code fact, outcome-neutral, not the scoped defect)

The five-minute kline fetch (`src/trading/services/market_service.py:195-222`) requests candles from Bybit with no end-time bound, maps and saves every returned candle including the newest still-forming bucket, and applies no closed-versus-forming filter and no drop of the last candle (grep for any such handling returned nothing). The `klines` table's unique constraint on (symbol, timeframe, timestamp) overwrites the in-progress bucket on each fetch. So the live volume ratio at decision time uses a partially-accumulated newest bucket, which adds a downward bias to that single most-recent reading. However, a direct data test did not isolate this as the dominant cause of the low median: comparing each symbol's newest five-minute bucket to its trailing-twenty average gave 0.509 for the newest bucket versus 0.617, 0.316, and 0.503 for the next three (already-closed) buckets, with no monotonic newest-is-lower pattern. What the data shows robustly is that a typical bucket sits at only 0.3 to 0.6 of its trailing-twenty average across all recent ranks, so the low median is driven mainly by the intrinsic right-skew of volume, not by the forming candle. Crucially, the forming-candle inclusion is identical for winners and losers, so it cannot differentially explain outcomes and is not a profit leak.

### Consumer audit (none assumes a wrong scale)

Every consumer applies either an upper-tail spike test or a low/floor test; none treats 1.0 as the median or applies a cutoff that assumes a symmetric distribution. The scorer awards a bonus at above 1.3 and above 2.0 (`src/strategies/scorer.py:276-280`). The regime detector labels VOLATILE at above 2.0 and contributes to DEAD only when the one-hour volume ratio is below `dead_volume_ratio` (default 0.5) in conjunction with low one-hour ADX and a low ATR percentile (`src/strategies/regime.py:141,149`). The strategy categories use spike or low tests at sensible levels for a ratio centered at 1.0: i1 kill-zone requires above 1.5 (`i1_kill_zone.py:57,100`), h4 order-flow requires above 2.0 (`h4_order_flow.py:35`), f3 liquidation-hunt requires at least 2.5 (`f3_liquidation_hunt.py:43,64`), and j4 altcoin-beta deliberately wants a low ratio below 1.5 for a lagging alt (`j4_altcoin_beta.py:41,55`). The APEX assembler reads the five-minute value into the brain context with no threshold (`src/apex/assembler.py:265`), and the strategist only logs the one-hour per-coin value (`src/brain/strategist.py:2981`). No consumer applies a mis-scaled threshold.

### Root cause / null result

There is no mis-scaled-threshold defect. The field's scale is correct and every consumer interprets it correctly as a ratio centered at 1.0. The sub-1.0 median is the expected right-skew of a volume ratio, not a bug. The forming-candle inclusion is a real but minor sampling nuance that is outcome-neutral and is not the defect Item 4 was scoped to find.

### Gate decision (recommendation)

Close the scaling question as investigated, no defect: the field's scale is correct and every consumer interprets it correctly as a ratio centered at 1.0. The forming-candle inclusion is a real sampling nuance with no differential outcome effect. The operator elected at the gate to apply the optional closed-candle refinement (see below). The field's true meaning and scale are documented above and handed to Item 1.

### Applied enhancement (operator-approved, default off)

A flag `[ta] volume_ratio_use_closed_candle` (default false) was added. When enabled, the technical-analysis engine computes `volume_sma_ratio` on the last closed candle, excluding the still-forming newest bucket. Files changed: `src/config/settings.py` (TASettings field and the `_build_ta` reader), `config.toml` (`[ta]` flag with comment), `src/analysis/engine.py` (the volume block and a boot sentinel `VOL_RATIO_CLOSED_CANDLE_SENTINEL`). Only the ratio is affected; force_index and the other volume indicators are unchanged. Verification: the default load shows the flag false so legacy behaviour is preserved byte-for-byte; with the flag on, a synthetic series whose forming bucket carries one tenth the normal volume yields a ratio of 0.105 with the flag off (it reads the forming bucket) versus 1.000 with the flag on (it reads the last closed bucket), and the boot sentinel fires at engine construction. The change is frequency-neutral-to-positive: it can only let volume-gated strategy entries fire slightly more often, never less, so it does not conflict with the aim.

## Phase 1: Item 1, the volume/ADX entry gate (gate verdict: do not fix)

### Symptom

The trade gate has no hard volume or ADX entry filter, so thin-volume or low-strength entries can execute. An earlier session finding suggested winners had more volume than losers (roughly 1.36 versus 1.07).

### Reproduction and confirmation

The close-time volume gap is real but invalid as evidence of an entry edge. On the bybit_demo set the close-time five-minute volume_ratio is 1.119 for winners versus 0.894 for losers, but that is sampled at close and is contaminated by the trade's own behaviour during the hold (a winner that ran saw volume expand; a chopping loser saw it contract). To test for a genuine entry edge, entry-time five-minute volume_ratio was reconstructed from the klines table for the recent window where five-minute candles exist, using the last twenty closed five-minute buckets before each entry (the forming bucket excluded), entry time parsed from setup_id. Statistics are pure-numpy AUC and Cliff's delta (analysis script `analyze_entry_gaps_investigation.py`, read-only).

Result on 284 reconstructable bybit_demo trades: entry-time five-minute volume_ratio does NOT separate winners from losers. Winners mean 1.466, median 0.896; losers mean 1.449, median 0.953; AUC 0.485, Cliff's delta minus 0.029 — indistinguishable, and the tiny tilt is if anything toward losers. The entry-time one-hour volume_ratio (from coin_regime_history, 321 trades) corroborates: AUC 0.462. So the close-time gap is an outcome artifact, not an entry edge.

The volume signal was then controlled for trend strength to make sure it was not a disguised ADX effect. Within entry-time one-hour ADX bins the volume AUCs are 0.493 (ADX below 20), 0.434 (20 to 30), and 0.560 (ADX 30 and above, small sample) — no consistent edge. Within entry_regime the volume AUCs are 0.483 ranging, 0.450 trending_down, 0.481 trending_up, 0.527 volatile — again no consistent edge.

### Root cause / null result and the decisive aim check

There is no entry-time volume edge to gate on. Separately, ADX itself does not separate winners from losers at entry (entry one-hour ADX AUC 0.460; losers actually have marginally higher ADX, 22.3 versus 21.3). The decisive finding against any ADX-based gate: the low-ADX cohort is the profitable one. Entry one-hour ADX below 20 has a 52.0 percent win rate and net plus 278.66 dollars over 152 trades; entry one-hour ADX 20 and above has a 45.0 percent win rate and net minus 339.52 dollars over 169 trades. A hard ADX gate would remove the profitable low-ADX volatile group and keep the losing strong-trend group, which is the exact opposite of the project aim. A volume gate has no edge to stand on at all.

### Gate decision (recommendation)

Do not fix. No hard volume or ADX gate, and not even an advisory or sizing mechanism, because there is no entry-time edge to surface. The five aim-bias answers all fail for any volume/ADX gate: it would reduce frequency, reduce aggression, block decisions on no proven basis, cut the genuinely profitable low-ADX behaviour, and add a filter the data does not support. The gate's two existing rejects (Check 4 zero-conviction, Check 6 reentry cooldown) stay byte-identical. No code change, no commit. This is an evidence-based null result that prevents a harmful, frequency-reducing, aim-violating fix.

## Phase 2: Item 2, entry-quality separability (gate verdict: entry selection is not the win/loss leak; the leverage is winner magnitude)

### Symptom

Winners and losers are reportedly indistinguishable at entry across every measured feature, and there is no quantitative backstop that catches what the brain cannot.

### Reproduction and confirmation (binary win/loss is the primary target)

On 1,812 bybit_demo trades (44.6 percent win rate), no persisted entry feature separates winners from losers. Univariate AUCs, with "indistinguishable" defined up front as AUC within 0.45 to 0.55 and absolute Cliff's delta below 0.15: entry RSI (five-minute) 0.485, entry ATR percent (five-minute) 0.511, entry MACD histogram (five-minute) 0.487, entry_score 0.530, claude_confidence 0.463, ensemble vote balance 0.485 — every one inside the indistinguishable band. Win-rate by quintile is flat or perverse: entry_score runs 44, 40, 45, 50 percent across quintiles with no clean trend, and claude_confidence actually declines from 53 percent in its lowest quintile to 43 percent in its highest. A multivariate five-fold cross-validated logistic regression confirms it: the three always-present five-minute features give a cross-validated AUC of 0.506; adding entry_score and claude_confidence reaches only 0.537; stratified within regime the AUC is 0.507 ranging, 0.540 trending_down, 0.538 trending_up, 0.530 volatile — all at or barely above chance. Winners and losers are genuinely indistinguishable at entry. The brain cannot separate them because on the available data they look the same, and there is no backstop that could.

### The magnitude finding (the secondary target, and the valuable one)

The binary picture changes when the question shifts from "will it win" to "how big will the winner be," which is the economically important question given the known average-win-collapse leak. Defining big-winners as the top quartile of winner PnL (at least 10.96 dollars, 202 trades) and comparing them to all other trades: entry ATR percent (five-minute) separates them clearly, AUC 0.633 and Cliff's delta plus 0.265 (big-winner mean ATR 0.459 versus 0.285, median 0.267 versus 0.216); entry_score also separates, AUC 0.620; while entry RSI, entry MACD, and claude_confidence do not (AUCs near 0.5). So entry-time volatility does not predict whether a trade wins, but it does predict how large the winner is when it wins. This is consistent with the volatile and low-ADX cohorts being the profitable ones in Item 1: higher-volatility setups produce the bigger favourable moves, while losers are capped by the fixed-percent stop regardless of volatility, so a tape with too many low-volatility entries yields small winners against fixed-size losers and expectancy erodes.

### Root cause / null result

Entry selection is not the win/loss leak; the two groups are statistically inseparable at entry on every available feature and combination. This formally confirms the task's core hypothesis. The genuine, surfacable signal is about magnitude, not direction of outcome: entry volatility (five-minute ATR percent) predicts winner size. The leverage is therefore winner magnitude, governed by entry volatility and by how winners are managed (allowed to run), not by filtering entries for win/loss.

### Gate decision (recommendation)

Do not add any win/loss entry filter; there is no separator to base one on, and a filter would only cut frequency. Formally conclude that entry selection is not the win/loss leak. The evidence points the leverage at winner magnitude, which lives primarily in trade management (letting winners run) and secondarily in volatility-aware sizing; the substantive management lever is the separate, out-of-scope task. The operator elected at the gate to apply the frequency-preserving magnitude advisory (see below) as the aim-aligned step available now.

### Applied enhancement (operator-approved, default off)

A flag `[brain] entry_magnitude_advisory_enabled` (default false) was added. When enabled, the strategist appends an expected-winner-magnitude token (`MAG=HIGH(larger-winner-potential)`, `MAG=MED`, or `MAG=LOW(small-winner-likely)`) to each coin's existing volatility line in the prompt, derived from the entry five-minute volatility class. It is advisory context only: it never gates a trade, changes a size, or alters direction, so trade frequency and direction balance cannot change. Files changed: `src/config/settings.py` (BrainSettings field and the `_build_brain` reader), `config.toml` (`[brain]` flag with comment), `src/brain/strategist.py` (a `_magnitude_advisory_tag` helper, a call appended to the per-coin volatility line at both prompt-build sites, and a boot sentinel `MAGNITUDE_ADVISORY_SENTINEL`). Verification: the default load shows the flag false so no token appears (legacy prompt preserved); with the flag on, the helper returns `MAG=HIGH` for the high and extreme classes, `MAG=MED` for medium, `MAG=LOW` for low and dead, and an empty string when the profile is missing, and the boot sentinel fires at strategist construction. The five aim-bias answers all pass: frequency preserved (advisory cannot block), aggression preserved, decision quality improved (the brain sees the magnitude implication of volatility), genuinely useful behaviour preserved, and separation of concerns respected (no gate, no sizing change).

## Phase 3: Item 3, H1-regime / M5-execution stop coherence (gate verdict: do not fix; the coherence signal does not survive the counterfactual)

### Symptom

The regime and trend thesis is assessed on the one-hour timeframe, but the entry, the stop-loss, and the execution operate on the five-minute timeframe. A trend-following trade justified by an hourly trend could be stopped out by ordinary five-minute chop before the hourly thesis plays out, which would produce the right-direction-but-stopped-out pattern.

### Reproduction and confirmation

381 bybit_demo trades opened since 2026-05-25 (the window with five-minute candles) were traced from trade_thesis, computing each trade's realized stop distance as a percentage of entry, beside its entry-time five-minute ATR percent and entry-time one-hour ATR percent (Wilder NATR-14 computed from klines, every value timeframe-labeled). Two facts emerge. First, the stops are not sized to a single five-minute candle: the median stop is about three times the five-minute ATR (winners 3.94 times, losers 3.39 times), so the crude "stop equals five-minute noise" framing is too strong. Second, relative to the one-hour thesis horizon there is a surface coherence signal: among stop-loss losers the median stop is about one times the one-hour ATR, and 48 percent of stop-loss losers had a stop tighter than a single one-hour ATR (trending_down 0.87 times, 71 percent below one one-hour ATR), versus winners at 1.34 times and only 20 percent below. So losers did tend to carry stops that were tight relative to the hourly horizon. Reward-to-risk was healthy throughout (median above 3), so the tightness was not forced by reward-to-risk.

### The decisive counterfactual (why the surface signal is not a profit flaw)

A plausible signal with correct arithmetic can still have the wrong cause, so the stopped-out losers were tested directly: after the stop fired, did price recover in the thesis direction within the intended hold window. Within the designed hold (about thirty-five minutes) the stop-loss losers recovered above entry only 27 percent of the time, would have reached take-profit 0 percent of the time, and the median post-stop favourable excursion was minus 0.25 percent — price kept moving against the position. Even for the subset whose stop was tighter than one one-hour ATR, 0 percent would have reached take-profit. Extending the window to three hours, 68 percent eventually touched break-even but still only 5 percent would have reached take-profit, with a median favourable excursion of just plus 0.44 percent, far below the roughly three percent take-profit and below the roughly one percent stop distance. So widening the stop would not have rescued these trades into winners; it would have converted small stop-losses into larger losses for the roughly two thirds that continued against the position, with no take-profit conversion.

### Root cause / null result

The losses are driven by wrong-direction entries in a choppy, mean-reverting tape, not by premature five-minute-noise stops. This is consistent with Item 2 (winners and losers indistinguishable at entry, so the entry genuinely cannot tell which way price will go) and Item 1 (the strong-trend high-ADX cohort is the losing group). The stop is doing its job by cutting losers; the surface coherence signal (stops below one one-hour ATR) does not survive the counterfactual test.

### Gate decision (recommendation)

Do not fix. Do not widen stops and do not add a one-hour-coherence stop floor. The five aim-bias answers fail for a stop-widening fix: it would not preserve or improve outcomes (0 percent take-profit conversion), it would enlarge per-trade losses on the roughly two thirds of stopped-out trades that continue adversely, and it would raise risk per trade against the aim. The stop derivation (structural one-hour level, five-minute volatility-class recommended floor, APEX clamp) is left unchanged. No code change, no commit. The leverage is winner magnitude (Item 2) and trade management, not stop distance.

## Phase 5: self-audit and sign-off

### Per-item verdict summary

Item 4 (volume-ratio scale): investigated, no mis-scaled-threshold defect. The operator approved the optional closed-candle refinement, which was implemented and enabled. Item 1 (volume/ADX entry gate): do not fix; no entry-time volume or ADX edge exists, and the low-ADX cohort is the profitable group, so a gate would cut profit and frequency. No code change. Item 2 (entry separability): entry selection is not the win/loss leak (winners and losers are statistically indistinguishable at entry); the leverage is winner magnitude. The operator approved the optional magnitude advisory, which was implemented and enabled; the substantive sizing/management lever is the separate task. Item 3 (stop coherence): do not fix; the counterfactual proves widening stops would not convert losers into winners (0 percent would have reached take-profit) and would only enlarge losses. No code change.

### What was applied (and its live status)

Two enhancements, both implemented and verified, both originally default off and now enabled in config.toml at the operator's instruction: `[ta] volume_ratio_use_closed_candle = true` and `[brain] entry_magnitude_advisory_enabled = true`. The dataclass defaults remain False (the safe code default); the config values enable them. Activation takes effect on the next system restart, which the operator will perform. The changes are not committed; the operator will handle git and the restart.

Files modified (four): `config.toml`, `src/config/settings.py`, `src/analysis/engine.py`, `src/brain/strategist.py`. Timestamped backups of all four were taken beside the originals before editing (suffix `.bak_entrygaps_<timestamp>`); the original code is also recoverable from git HEAD. Boot sentinels: `VOL_RATIO_CLOSED_CANDLE_SENTINEL` (engine init) and `MAGNITUDE_ADVISORY_SENTINEL` (strategist init), both confirmed to fire when their flag is on.

### Expected behavioural effect on restart (honest note for monitoring)

The magnitude advisory (Item 2) is prompt-only: it appends a `MAG=HIGH/MED/LOW` token to each coin's volatility line, so it cannot change trade frequency or direction; it only adds context the brain may weigh. The closed-candle volume change (Item 4) does change the `volume_sma_ratio` value that flows into the regime detector, the scorer, the scanner, and the volume-gated strategy categories: with the forming bucket excluded the ratio is no longer biased low, so above-average and spike detections will occur somewhat more often and DEAD-regime labels somewhat less often, which can let volume-confirmed entries fire slightly more often. This is frequency-neutral-to-positive and aim-aligned, but it is a real signal-behaviour change worth watching after restart. Both flags are independently reversible by setting them back to false.

### Cross-cutting confirmations

Trade frequency and direction balance are not reduced: both flags default off (zero change unless enabled), and the enabled changes are advisory or frequency-neutral-to-positive, never frequency-reducing. The direction-flip switches remain off and untouched (`xray_dir_flip_enabled`, `xray_trade_suppression_enabled`, `apex_dir_flip_enabled` all false, none in the diff). The regime detector's one-hour ADX trend floor and labelling logic are unchanged (`regime.py` not modified). The APEX gate's two rejects (Check 4 zero-conviction, Check 6 reentry cooldown) are unchanged (`gate.py` not modified). The stop derivation is unchanged (`optimizer.py` and `volatility_profile.py` not modified). No protected table was modified: every database access in this task was read-only. No new branch and no new directory were created; the only new files are this findings note and the read-only analysis harness `analyze_entry_gaps_investigation.py`, both left untracked at the operator's request.

## Phase 5b: deep audit and full test battery (post-enable cross-check)

### Integration and wiring (verified, not assumed)

The live settings module is `src/config/settings.py` — every entrypoint (`container.py`, `manager.py`, `server.py`, `workers.py`, `brain.py`) imports it; `src/workers/settings.py` is a confirmed dead duplicate (not imported anywhere in src, and it defines its own separate BrainSettings class) and was correctly left untouched. The Item 4 flag propagates on the live path: `manager.py:195` builds `TAEngine(db, settings=self.settings)`, `manager.py:201` wraps it in `TACache`, and `ta_cache.py:142,162` delegates `analyze` to that settings-bearing engine while proxying all other attributes, so every volume-ratio consumer (scorer, scanner, regime, APEX assembler, the close-time collector) receives the flag's effect through the single shared cache. The regime detector receives the same TACache (`manager.py:1430`). The change is therefore placed at the correct architectural seam, not patched per-consumer. Dataclass safety: the mid-list `BrainSettings` field insertion is safe because every construction in the codebase is keyword or default (no positional construction exists), confirmed by grep.

### Observability completed to the rule

Both items now emit a boot sentinel and a per-decision log line. Item 4: `VOL_RATIO_CLOSED_CANDLE_SENTINEL` at engine construction, and `VOL_RATIO_CLOSED_CANDLE_ACTIVE` once per process the first time the closed-candle path executes. Item 2: `MAGNITUDE_ADVISORY_SENTINEL` at strategist construction, and `MAGNITUDE_ADVISORY_ACTIVE` rate-limited to one line per sixty seconds per process when the advisory is appended (idiomatic to the codebase's existing rate-limited logging). All four were confirmed firing.

### Test battery

Smoke: all touched, dependent, and entrypoint modules import cleanly and the core objects construct (Settings, TAEngine, TACache, ClaudeStrategist). Integration: the volume ratio computed through the real TACache-to-TAEngine path returns 1.000 with the flag on and 0.105 with it off on a synthetic series whose forming bucket carries one tenth the normal volume. Functional: both flags load enabled from config, the advisory helper maps each volatility class to the correct token and returns empty when off or when the profile is missing, and the closed-candle ratio is correct. Regression: roughly 1,047 tests across the analysis engine and indicators, strategies, brain enrichment, the Stage-2 and one-day-briefing prompt pipelines, strategist prompt builders, regime, scorer, scanner, APEX gate, sizing, observability, integration, end-to-end pipeline, kline worker, and market repository all pass, with one exception. The single failure, `test_apex_direction_lock.py::test_system_prompt_still_has_rsi_caution`, is pre-existing and unrelated: it asserts strings in the `STRATEGIST_SYSTEM_PROMPT` constant that a prior session moved into method bodies; the original git HEAD code fails it identically (same string counts), and this task never touched that constant. It is out of scope and was not modified.
