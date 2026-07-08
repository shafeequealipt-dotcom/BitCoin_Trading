# Price-Aware Open-Interest Fix — Live Verification Report

Date: 2026-06-10. Program: Five-Fix Follow-Up, Phase 4. This phase ships proof, not code. Verdict up front: the price-aware open-interest mapping is loaded, live, firing on natural data, and two-sided. No blocking finding.

## What was verified

The mapping shipped in the eight-fix program (commit eb305d1, hardened by c10e6ed): rising open interest with a falling price reads bearish (shorts piling in), never bullish. The function is `_condition_oi_score` in `src/intelligence/signals/signal_generator.py`, called per window from `_blend_oi_windows`, feeding both the classifier and the confidence calculation. The project's standing principle required live proof, because the one strong-buy case in the six-cycle capture (KAT, open interest up twenty point four percent with price RISING) reads buy under both the old and the new mapping and therefore proved nothing.

## Proof one — the fix is loaded and live

The boot sentinel `BOOT_SENT_NORM_OK` fired at both boots of 2026-06-10 — 19:47:17 (the restart that first loaded the eight-fix code) and 20:45:15 (the machine reboot) — carrying the Fix 1 configuration keys `oi_price_window_h=24.0` and `oi_price_dead_band=0.0`. The live call chain is SignalWorker tick to `generate_signal` to `_blend_oi_windows` to `_condition_oi_score`; the per-coin `SIG_OI_WINDOWS` log line at `signal_generator.py` proves the chain executes every cycle. Source: `data/logs/workers.log`.

## Proof two — natural inverted cases, live, on real data

The losing-window root case (RUNE: open interest rising while price falls, read as a buy) now reads bearish on naturally occurring live data. In `workers.log` for 2026-06-10 there are 262 lines where a coin's 24-hour open interest was RISING while its 24-hour price was FALLING, and in every one the conditioned score is NEGATIVE (bearish). Five examples from the 20:41 cycle, quoted from the log:

APTUSDT: open interest plus 3.05 percent, price minus 5.65 percent, conditioned score minus 0.610 (raw would have been plus 0.610 — inverted to bearish).

BCHUSDT: open interest plus 10.94 percent, price minus 4.24 percent, conditioned score minus 1.000.

OPUSDT: open interest plus 2.09 percent, price minus 5.31 percent, conditioned score minus 0.418.

ORCAUSDT: open interest plus 1.08 percent, price minus 4.67 percent, conditioned score minus 0.216.

BLURUSDT: open interest plus 0.69 percent, price minus 3.49 percent, conditioned score minus 0.138.

ETHUSDT showed the same at the 19:56 cycle (plus 1.16 against minus 1.60, conditioned to minus 0.232).

## Proof three — the recorded RUNE and SKR cases replayed through the live code path

`verify_eq_fix1_oi_price_conditioning.py` (run fresh 2026-06-10 evening, all checks pass): the forensic RUNE values (open interest rising, price minus 2.0 percent) read non-buy, and SKR (open interest plus 11.11 percent, price minus 3.5 percent) reads bearish. `simulate_entry_quality_scenarios.py` scenario 1 (run fresh, passes): BEFORE the fix RUNE produced buy and SKR strong_buy; AFTER, RUNE produces sell and SKR strong_sell. Genuine long accumulation (open interest up, price up) still reads bullish — the mapping is a correction, not an inversion of everything.

## Proof four — the distribution is two-sided, not a standing short flip

Signals generated in the live post-fix window (19:56 to 20:42, ten full cycles, 500 signals): neutral 372, sell 101, buy 24, strong buy 3. Both directions present. The sell lean matches the market itself that evening — the same log lines show 24-hour price changes negative on most coins (ETH minus 1.6, ALGO minus 2.9, OP minus 5.3, APT minus 5.7) — which is the price conditioning following each coin's own falling price, not a hardcoded bias. The replay grid in the verification harness confirms both strong_buy and strong_sell are reachable.

## Proof five — the new inversion tags are truthful

The Five-Fix Follow-Up Fix 2 commit added annotation-only `cond_24h`, `cond_1h` and `cond_15m` tags to `SIG_OI_WINDOWS` (inv means the conditioning flipped that window's sign, pass means unchanged, na means no data; the score math is untouched). Truthfulness was proven on a replayed inverted case through the live blend path: `verify_eq_fix2_signal_freshness.py` test `test_cond_tags_are_truthful_annotations` passes — opposite-sign windows tag inv with the score being the exact negation, same-sign windows tag pass, missing windows tag na. After the next workers restart the tags appear on every live `SIG_OI_WINDOWS` line, so this verification never again requires sign inference.

## Situation note, stated plainly

At the time of this report the trading cycle is INACTIVE (layer state on disk has only layer 1 active; the signal worker logs `LAYER1B_TICK_SKIP reason=cycle_inactive` since the 20:45 reboot). The system is idle by operator state, not by defect. The live evidence above comes from the 19:56 to 20:41 active window, which ran the eight-fix code. The Five-Fix Follow-Up commits (components purity, fresh open interest, volatility stops, and the tags cited above) are committed but NOT yet running — they load at the next workers restart, and the cycle must be resumed for trading to continue.

## Continuing check

After restart and cycle resume, the standing checks are: grep `SIG_OI_WINDOWS` for `cond_` tags showing a mix of inv and pass across coins; and the signals-table distribution staying two-sided across regimes (both buy-lean and sell-lean days, following each coin's own price).
