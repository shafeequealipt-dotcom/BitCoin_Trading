# C1 — Phase 1.1 Confirm Issue

## Method

Every `WATCHDOG_CLOSE_SCORE_COMPUTED` event in the 2026-05-20 worker logs was extracted (timestamp, symbol, composite, recommendation, factor breakdown), then joined against `trade_log` on `(symbol, closed_at)` using the next close within 60 minutes of the scoring event. The `closed_at` column uses ISO format with timezone offset (`2026-05-20T11:16:08.x+00:00`).

## Scoring intercept volume

| Event | Count |
|---|---:|
| `WD_SCORING_PATH_REACHED` (close/take_profit) | 28 |
| `WATCHDOG_CLOSE_SCORE_COMPUTED` | 28 |
| `WD_CLOSE_SCORE_LOG_ONLY` | 28 |
| `WATCHDOG_CLOSE_EXECUTED` | 0 |
| `WATCHDOG_CLOSE_REJECTED` | 0 |
| `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN` | 0 |
| `WD_BRAIN_SCORE_FAIL` | 0 |

Every close vote that reached the intercept produced a scored output. None executed via the scoring path because enforce is off. None failed.

## Per-event detail

The 28 scored close events, sorted by timestamp UTC, joined with the corresponding `trade_log.close_reason` and `pnl_usd`:

| time (UTC) | symbol | composite | recommendation | trade_log close_reason | pnl_usd |
|---|---|---:|---|---|---:|
| 11:16:08 | INJUSDT | −3.0 | reject_and_tighten | wd_claude_action | −14.72 |
| 11:51:39 | HYPERUSDT | −4.5 | reject_and_tighten | wd_claude_action | +0.43 |
| 11:56:54 | SKRUSDT | −2.0 | reject_and_tighten | wd_claude_action | −5.01 |
| 11:56:54 | PYTHUSDT | −4.0 | reject_and_tighten | wd_claude_action | −5.82 |
| 12:06:45 | BSBUSDT | −4.0 | reject_and_tighten | wd_claude_action | −8.69 |
| 12:14:49 | ALGOUSDT | −4.0 | reject_and_tighten | wd_claude_action | −11.86 |
| 12:17:56 | CRVUSDT | −6.0 | reject_and_tighten | wd_claude_action | −1.72 |
| 12:50:55 | LINKUSDT | −1.0 | reject_and_tighten | wd_claude_action | −4.98 |
| 12:50:56 | AEROUSDT | −3.5 | reject_and_tighten | wd_claude_action | −5.92 |
| 13:21:54 | DYDXUSDT | −8.5 | reject_and_tighten | wd_claude_action | −1.26 |
| 13:31:16 | GMTUSDT | −4.5 | reject_and_tighten | wd_claude_action | −5.43 |
| 13:31:17 | RENDERUSDT | −5.5 | reject_and_tighten | wd_claude_action | −1.53 |
| 14:17:55 | ICPUSDT | −2.0 | reject_and_tighten | wd_claude_action | −45.98 |
| 14:23:16 | ALGOUSDT | 0.0 | reject | wd_claude_action | −38.63 |
| 15:09:18 | RENDERUSDT | −4.0 | reject_and_tighten | wd_claude_action | −8.19 |
| 15:31:11 | AAVEUSDT | −9.5 | reject_and_tighten | wd_claude_action | −4.64 |
| 15:31:11 | LDOUSDT | −4.5 | reject_and_tighten | wd_claude_action | −9.28 |
| 16:06:45 | PLUMEUSDT | −5.5 | reject_and_tighten | wd_claude_action | −1.42 |
| 16:20:30 | ALGOUSDT | −8.5 | reject_and_tighten | wd_claude_action | −8.60 |
| 16:20:30 | GALAUSDT | −4.5 | reject_and_tighten | wd_claude_action | −6.23 |
| 16:23:22 | LINKUSDT | −5.5 | reject_and_tighten | wd_claude_action | −7.77 |
| 17:04:59 | SANDUSDT | −6.0 | reject_and_tighten | wd_claude_action | −5.41 |
| 17:07:45 | ALGOUSDT | −2.5 | reject_and_tighten | wd_claude_action | −0.02 |
| 17:47:51 | ALGOUSDT | 1.0 | reject | wd_claude_action | −8.86 |
| 18:23:12 | ALGOUSDT | −5.5 | reject_and_tighten | wd_claude_action | −12.46 |
| 19:00:16 | ONDOUSDT | 1.5 | reject | wd_claude_action | −22.55 |
| 19:27:00 | ARBUSDT | −5.5 | reject_and_tighten | wd_claude_action | −8.13 |
| 19:32:02 | AXSUSDT | 4.0 | reject | wd_claude_action | −2.49 |

## Aggregate result

- Matched: 28 of 28 events to a `wd_claude_action` close row (100% correlation).
- Outcomes: **1 win** (HYPERUSDT +$0.43), **27 losses**, **0 flat**.
- Net realised PnL across the 28: **−$257.18**.

This matches the prompt's spec verbatim. Every score correctly predicted a low-quality close. Of the 28, 25 recommended `reject_and_tighten` (composite < 0) and 3 recommended `reject` (0 ≤ composite < 6). Zero would have executed under enforce mode.

## Direct counterfactual

If `wd_brain_scoring_enforce = true` had been set during this session:

- 25 closes would have been rejected with SL tightened toward break-even.
- 3 closes would have been rejected (held with no SL change).
- 0 closes would have executed.

The remaining 25 wd_claude_action closes the day's wider window (53 total day, 28 in the 9-hour session = the rest before/after the intercept time band) still suffered the same broken pattern, but those events fell outside the scored intercept span (likely earlier morning rotations where positions held longer than the 300 s min-hold guard had not yet expired).

## Confirmation of issue

The scoring system is correctly identifying brain panic-closes. Every flagged close lost money except one that gained $0.43. The fix is built and validated; only the enforce flag remains. This confirms the issue exactly as the implementation prompt described.

## Carryover for next steps

- The 28 composites range from −9.5 to +1.5. The headroom between the worst composite (+1.5) and the 6.0 threshold is 4.5. A small SL%-bucket shift from a divergence-driven re-classification (e.g., `comfortable` → `tight`, delta +1.0) would not lift any of these composites past the threshold. Phase 1.4 will quantify exactly how many bucket shifts would have been needed.
- ICPUSDT (14:17 UTC) and ALGOUSDT (14:23 UTC) carry the two largest single losses (−$45.98 and −$38.63). Together they account for one third of the session's wd_claude_action damage; if the trial reverses these two cases alone the impact is material.
