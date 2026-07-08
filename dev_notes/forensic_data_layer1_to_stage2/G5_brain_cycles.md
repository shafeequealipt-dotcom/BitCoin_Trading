# G5 — Last 5 Brain CALL_A Invocations

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Sources:** `data/logs/brain.log` (ERR/INFO emissions from `src.brain.strategist`), `data/logs/general.log` (cross-reference for ALERT_SENT under same `did=`).
- **Per-cycle log tags:** `STRAT_CALL_A_START`, `STRATEGIST_PACKAGES_READ`, `STRAT_CALL_A_CTX`, `PROMPT_BUILD_DONE`, `STRAT_CALL_A`, `STRAT_CALL_A_PLAN`, `STRAT_DIRECTIVE`, `STRAT_CALL_A_NO_TRADES`, `STRAT_CALL_A_END`, `ALERT_SENT`.
- **NOT FOUND** — searched for `BRAIN_DECISION`, `BRAIN_DO_START`, `BRAIN_DO_END`, `BRAIN_TRADE_HALT`, `TRADE_EXEC`, `EXEC_PLACED`, `EXEC_BLOCKED`, `Claude trade failed`, `_execute_new_trades` in `brain.log` and `general.log`. No execution-result line was emitted in the 5 captured cycles. The closest signal of execution is the `ALERT_SENT | level=info` line under the same `did=` (telegram alert published).

---

## CALL_A #1 — `did=d-1777326781241` (2026-04-27 21:53:01 UTC)

```
21:53:01.241 STRAT_CALL_A_START | did=d-1777326781241
21:53:06.047 STRATEGIST_PACKAGES_READ | call=CALL_A count=0 age_min_s=0 age_max_s=0 reader=brain_call_a
21:53:06.155 STRAT_CALL_A_CTX | sections=22 chars=2716 el=4913ms
21:53:06.155 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=2737 sections=22 packages=0 elapsed_ms=4913
21:53:06.155 STRAT_CALL_A | chars=2737
21:55:18.370 STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Both tradeable coins showing clear bearish momentum with RSI mid-30s and strongl'
21:55:18.373 STRAT_DIRECTIVE | #1 sym=ETHUSDT dir=Sell lev=3 rsn='ETH bearish momentum: RSI=35 falling, MACD_hist=-3.91 strongly negative, ADX=32'
21:55:18.374 STRAT_DIRECTIVE | #2 sym=BTCUSDT dir=Sell lev=3 rsn='BTC bearish: RSI=36, MACD_hist=-117.84 deeply negative, -1.3% 24h. ADX=25 weaker'
21:55:18.374 STRAT_CALL_A_END | el=137133ms trades=2
21:55:19.120 ALERT_SENT | level=info len=652 (general.log:58539)
21:55:33.398 ALERT_SENT | level=info len=344
21:55:34.032 ALERT_SENT | level=info len=348
```

Reconstructed details:
- **Packages received:** 0 (count=0 — fell back to legacy path; coins=2 came from forced/legacy code)
- **Per-package completeness + key fields:** N/A — no packages
- **Prompt size:** 2737 bytes; 22 sections; build elapsed 4913 ms; chars=2716 in CTX
- **Decision:** `trades=2`, risk=`cautious`, action: 2 Sell directives (ETHUSDT lev=3, BTCUSDT lev=3)
- **Execution result:** NOT FOUND in logs — only ALERT_SENT lines (3) under same did
- **CALL_A_END elapsed:** 137133 ms (137 s)

---

## CALL_A #2 — `did=d-1777327303869` (2026-04-27 22:01:43 UTC)

```
22:01:43.869 STRAT_CALL_A_START | did=d-1777327303869
22:01:43.871 STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=163 age_max_s=163 reader=brain_call_a
22:01:44.310 STRAT_CALL_A_CTX | sections=49 chars=6946 el=440ms
22:01:44.310 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=6994 sections=49 packages=2 elapsed_ms=440
22:01:44.310 STRAT_CALL_A | chars=6994
22:03:28.093 STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Both BTC and ETH in strong bearish momentum approaching 24h lows. Late NY dead z'
22:03:28.093 STRAT_DIRECTIVE | #1 sym=ETHUSDT dir=Sell lev=3 rsn='TRENDING_DOWN regime (64% conf), ADX=32 confirms trend strength, RSI=35 in downt'
22:03:28.093 STRAT_DIRECTIVE | #2 sym=BTCUSDT dir=Sell lev=3 rsn='Global ranging but strong bearish momentum -2.12% 24h, RSI=34 declining, MACD de'
22:03:28.094 STRAT_CALL_A_END | el=104224ms trades=2
22:03:28.828 ALERT_SENT | level=info len=652 (general.log:58547)
22:03:43.566 ALERT_SENT | level=info len=344
22:03:44.215 ALERT_SENT | level=info len=346
```

- **Packages received:** 2; both packages aged 163 s (read from `_coin_packages` populated at scanner cycle c-21:55, tick 21:59:00 — matches age 163s back from 22:01:43)
- **Per-package completeness:** packages built at scanner cycle c-21:55 — BTCUSDT=0.89 ok, ETHUSDT=0.94 ok (per G4 cycle 2)
- **Prompt size:** 6994 bytes; 49 sections; build el=440 ms
- **Decision:** trades=2; ETHUSDT Sell lev=3; BTCUSDT Sell lev=3
- **Execution result:** NOT FOUND — 3 ALERT_SENT under same did
- **CALL_A_END elapsed:** 104224 ms (104 s)

---

## CALL_A #3 — `did=d-1777327727920` (2026-04-27 22:08:47 UTC)

```
22:08:47.920 STRAT_CALL_A_START | did=d-1777327727920
22:08:47.922 STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=287 age_max_s=287 reader=brain_call_a
22:08:48.567 STRAT_CALL_A_CTX | sections=41 chars=6517 el=647ms
22:08:48.568 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=6557 sections=41 packages=2 elapsed_ms=647
22:08:48.568 STRAT_CALL_A | chars=6557
22:10:26.000 STRAT_CALL_A_PLAN | trades=1 risk=cautious view='Extremely limited opportunity set — only 2 coins tradeable and BTC already has p'
22:10:26.001 STRAT_DIRECTIVE | #1 sym=ETHUSDT dir=Sell lev=3 rsn='ETHUSDT [TRENDING_DOWN 64%] — trading WITH per-coin regime. RSI=35 in downtrend'
22:10:26.001 STRAT_CALL_A_END | el=98081ms trades=1
22:10:26.725 ALERT_SENT | level=info len=666 (general.log:58551)
22:10:37.223 ALERT_SENT | level=info len=344
```

- **Packages received:** 2; both packages aged 287 s (= 4m47s — the package was built at scanner cycle c-22:00 tick 22:04:00, 287s before 22:08:47)
- **Per-package completeness:** scanner cycle c-22:00 had BTCUSDT=0.67 warn, ETHUSDT=0.73 warn (per G4 cycle 3)
- **Prompt size:** 6557 bytes; 41 sections; build el=647 ms
- **Decision:** trades=**1**; ETHUSDT Sell lev=3 (BTC dropped — `BTC already has position`)
- **Execution result:** NOT FOUND — 2 ALERT_SENT under same did
- **CALL_A_END elapsed:** 98081 ms (98 s)

---

## CALL_A #4 — `did=d-1777328223516` (2026-04-27 22:17:03 UTC)

```
22:17:03.516 STRAT_CALL_A_START | did=d-1777328223516
22:17:03.520 STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=183 age_max_s=183 reader=brain_call_a
22:17:04.080 STRAT_CALL_A_CTX | sections=44 chars=6973 el=564ms
22:17:04.080 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=7016 sections=44 packages=2 elapsed_ms=564
22:17:04.081 STRAT_CALL_A | chars=7016
22:18:22.829 STRAT_CALL_A_PLAN | trades=0 risk=cautious view='Only 2 coins available (BTCUSDT, ETHUSDT). ETHUSDT already has a position being '
22:18:22.829 [WARNING] STRAT_CALL_A_NO_TRADES | view='Only 2 coins available (BTCUSDT, ETHUSDT). ETHUSDT already has a position being managed by watchdog '
22:18:22.833 STRAT_CALL_A_END | el=79317ms trades=0
22:18:23.206 ALERT_SENT | level=info len=644 (general.log:58559)
```

- **Packages received:** 2; aged 183 s (= 3m03s — package built at scanner cycle c-22:10 tick 22:14:00, 183 s before 22:17:03)
- **Per-package completeness:** scanner cycle c-22:10 had BTCUSDT=0.67 warn, ETHUSDT=0.73 warn (per G4 cycle 5)
- **Prompt size:** 7016 bytes; 44 sections; build el=564 ms
- **Decision:** trades=**0** (`STRAT_CALL_A_NO_TRADES` WARNING)
- **Execution result:** N/A — no trades planned
- **CALL_A_END elapsed:** 79317 ms (79 s)

---

## CALL_A #5 — `did=d-1777328602866` (2026-04-27 22:23:22 UTC, MOST RECENT)

```
22:23:22.866 STRAT_CALL_A_START | did=d-1777328602866
22:23:23.019 STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=263 age_max_s=263 reader=brain_call_a
22:23:24.085 STRAT_CALL_A_CTX | sections=40 chars=6529 el=1207ms
22:23:24.085 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=6568 sections=40 packages=2 elapsed_ms=1207
22:23:24.119 STRAT_CALL_A | chars=6568
22:25:16.717 STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Late NY dead zone, both BTC and ETH sold off hard today (-2.3% and -3.5%). Low v'
22:25:16.717 STRAT_DIRECTIVE | #1 sym=ETHUSDT dir=Sell lev=3 rsn='TRENDING_DOWN 64% per-coin regime. Score 68 STRONG ensemble, short signal. RSI=3'
22:25:16.717 STRAT_DIRECTIVE | #2 sym=BTCUSDT dir=Buy lev=2 rsn='Global ranging regime default. RSI=34 oversold = mean-reversion buy opportunity'
22:25:16.717 STRAT_CALL_A_END | el=113851ms trades=2
22:25:17.459 ALERT_SENT | level=info len=652 (general.log:58560)
22:25:34.893 ALERT_SENT | level=info len=344
22:25:35.537 ALERT_SENT | level=info len=346
```

- **Packages received:** 2; aged 263 s (= 4m23s — package built at scanner cycle c-22:15 tick 22:19:00, 263s before 22:23:22)
- **Per-package completeness:** scanner cycle c-22:15 had BTCUSDT=0.89 ok, ETHUSDT=0.94 ok (per G4 cycle 6)
- **Prompt size:** 6568 bytes; 40 sections; build el=1207 ms
- **Decision:** trades=2; ETHUSDT Sell lev=3, BTCUSDT **Buy** lev=2 (note opposite directions in same plan)
- **Execution result:** NOT FOUND — 3 ALERT_SENT under same did
- **CALL_A_END elapsed:** 113851 ms (114 s)

---

## Cross-cycle aggregate

| # | did time | Packages | Pkg age (s) | Sections | Prompt bytes | Build ms | trades | symbols/dirs | CALL_A end ms |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 21:53:01 | 0 | 0 | 22 | 2737 | 4913 | 2 | ETHUSDT/Sell, BTCUSDT/Sell | 137133 |
| 2 | 22:01:43 | 2 | 163 | 49 | 6994 | 440 | 2 | ETHUSDT/Sell, BTCUSDT/Sell | 104224 |
| 3 | 22:08:47 | 2 | 287 | 41 | 6557 | 647 | 1 | ETHUSDT/Sell | 98081 |
| 4 | 22:17:03 | 2 | 183 | 44 | 7016 | 564 | 0 | (no_trades) | 79317 |
| 5 | 22:23:22 | 2 | 263 | 40 | 6568 | 1207 | 2 | ETHUSDT/Sell, BTCUSDT/Buy | 113851 |

Notes:
- Across all 5 cycles, the strategist received **either 0 or 2 packages**; never more.
- The 2 packages are always BTCUSDT + ETHUSDT (forced by open-position rule per G4 — qualified=0 every cycle).
- Package age at CALL_A start ranges 163-287s (= 2m43s - 4m47s). Scanner sweet-spot is 4:00 in the 5-min window; CALL_A fires every 150s (see config.toml:163 `strategic_interval = 150`); the offset between scanner write and brain read varies per cycle.
- `chars` vs `size_bytes` in PROMPT_BUILD_DONE: bytes is slightly larger (UTF-8 encoding, e.g., 6994 vs 6946). Prompt grew from 2737 bytes (no packages) to 6994 bytes when packages started flowing — +4257 bytes added by the 2 packages combined.
- `sections` count: 22 baseline (no packages) → 40-49 with 2 packages.
- **Execution outcome:** No EXEC_OK / EXEC_FAIL / TRADE_PLACED / Claude trade failed lines for any of these 5 dids in `brain.log` or `general.log`. Only ALERT_SENT (level=info) telegram messages under the same did. Whether the trades actually placed cannot be confirmed from the available log set — the relevant emission either lives in a log not in `data/logs/` or is not emitted at all under these dids.
