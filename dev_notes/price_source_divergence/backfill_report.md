# Phase 5 — trade_intelligence Backfill Report

**Generated:** 2026-05-03T06:46:08.010225+00:00
**Mode:** DRY-RUN
**Threshold (skip-if-below):** $0.05

## Summary

- Main rows scanned: 821
- Matched to Shadow row: 785
- Unmatched (skipped): 36
- Already match (|Δ| < $0.05): 73
- Would update / updated: 712
- Total dollar correction: $+994.6911

## Updated rows (top 50 by |Δ|)

| symbol | closed_at | closed_by | main_pnl_usd | shadow_net_pnl_usd | Δ |
|---|---|---|---|---|---|
| RAVEUSDT | 2026-04-18T11:22:51.457747+00:00 | hard_stop | -10.9143 | -59.7783 | +48.8640 |
| BSBUSDT | 2026-04-23T18:23:26.398115+00:00 | hard_stop | -0.0741 | -32.0178 | +31.9437 |
| ENJUSDT | 2026-04-19T10:14:22.165132+00:00 | profit_take | -15.7552 | +12.5714 | -28.3266 |
| GENIUSUSDT | 2026-04-19T18:58:13.191382+00:00 | early_exit | -36.5507 | -62.9819 | +26.4312 |
| BSBUSDT | 2026-04-21T09:21:02.133331+00:00 | profit_take | +8.0237 | +32.6886 | -24.6650 |
| ARIAUSDT | 2026-04-12T06:18:49.328199+00:00 | watchdog | -40.7460 | -16.5115 | -24.2346 |
| REDUSDT | 2026-04-07T08:36:33.377271+00:00 | shadow_sl_tp | -29.3209 | -50.8962 | +21.5752 |
| ARIAUSDT | 2026-04-12T12:51:40.132623+00:00 | profit_take | +32.1792 | +17.2764 | +14.9028 |
| STRKUSDT | 2026-04-22T21:44:18.805984+00:00 | shadow_sl_tp | +16.3421 | +2.9064 | +13.4357 |
| RAVEUSDT | 2026-04-09T19:50:15.283490+00:00 | shadow_sl_tp | -68.3136 | -81.5367 | +13.2231 |
| RAVEUSDT | 2026-04-13T21:21:48.422409+00:00 | hard_stop | -40.8486 | -53.4178 | +12.5692 |
| NAORISUSDT | 2026-04-21T18:59:09.062116+00:00 | shadow_sl_tp | -90.4938 | -78.7894 | -11.7044 |
| MERLUSDT | 2026-04-20T08:31:12.858851+00:00 | sentinel_deadline_profit | +11.4858 | +23.1167 | -11.6309 |
| COREUSDT | 2026-04-22T22:26:18.750896+00:00 | mode4_p9 | +44.3065 | +32.8133 | +11.4932 |
| RIVERUSDT | 2026-04-24T17:11:09.709074+00:00 | hard_stop | -4.3759 | -15.3877 | +11.0119 |
| ARIAUSDT | 2026-04-12T10:06:58.652620+00:00 | shadow_sl_tp | -18.6506 | -29.3888 | +10.7382 |
| HIGHUSDT | 2026-04-19T18:17:13.122333+00:00 | trailing_stop | +20.0921 | +9.4031 | +10.6890 |
| STRKUSDT | 2026-04-22T21:59:39.954745+00:00 | shadow_sl_tp | +12.6744 | +2.0750 | +10.5995 |
| FARTCOINUSDT | 2026-04-08T20:59:40.754398+00:00 | shadow_sl_tp | +12.3612 | +22.6975 | -10.3364 |
| ZECUSDT | 2026-04-08T12:27:47.149423+00:00 | watchdog | -11.5167 | -21.5172 | +10.0005 |
| BSBUSDT | 2026-04-21T14:16:05.493054+00:00 | hard_stop | -18.1278 | -27.8320 | +9.7043 |
| ENJUSDT | 2026-04-08T21:05:36.102742+00:00 | emergency_manual | -26.2986 | -35.0039 | +8.7054 |
| RAVEUSDT | 2026-04-12T10:49:33.295098+00:00 | shadow_sl_tp | +15.5370 | +7.0543 | +8.4827 |
| ARIAUSDT | 2026-04-12T04:19:22.772008+00:00 | shadow_sl_tp | +40.1790 | +31.8293 | +8.3497 |
| ARIAUSDT | 2026-04-12T03:19:26.562079+00:00 | profit_take | +52.3762 | +44.7485 | +7.6276 |
| STRKUSDT | 2026-04-22T22:52:14.720977+00:00 | emergency_manual | +6.8393 | -0.7127 | +7.5520 |
| TAOUSDT | 2026-04-08T08:09:09.154851+00:00 | shadow_sl_tp | +2.7110 | -4.7268 | +7.4379 |
| REDUSDT | 2026-04-07T07:38:56.694054+00:00 | shadow_sl_tp | +73.9417 | +66.5806 | +7.3611 |
| CFGUSDT | 2026-04-19T19:02:18.890768+00:00 | profit_take | +6.6920 | +13.9288 | -7.2368 |
| ARIAUSDT | 2026-04-12T17:00:16.654434+00:00 | shadow_sl_tp | +63.4073 | +56.3046 | +7.1027 |
| RAVEUSDT | 2026-04-12T16:54:09.136215+00:00 | shadow_sl_tp | +12.1608 | +5.0659 | +7.0949 |
| RIVERUSDT | 2026-04-19T18:33:38.693043+00:00 | early_exit | -46.7994 | -53.8411 | +7.0417 |
| RAVEUSDT | 2026-04-13T23:49:52.404612+00:00 | shadow_sl_tp | -10.7808 | -17.3998 | +6.6190 |
| ARIAUSDT | 2026-04-12T12:21:15.581683+00:00 | shadow_sl_tp | +62.2472 | +55.6832 | +6.5640 |
| DASHUSDT | 2026-04-10T16:07:33.695210+00:00 | shadow_sl_tp | +1.6096 | -4.5400 | +6.1496 |
| RAVEUSDT | 2026-04-21T14:30:03.125160+00:00 | trailing_stop | +6.9817 | +0.8460 | +6.1357 |
| RAVEUSDT | 2026-04-21T08:44:04.828409+00:00 | shadow_sl_tp | +3.4288 | -2.6050 | +6.0338 |
| GENIUSUSDT | 2026-04-18T11:15:31.575500+00:00 | shadow_sl_tp | +3.9350 | -1.9145 | +5.8495 |
| LIGHTUSDT | 2026-04-18T10:54:12.449072+00:00 | shadow_sl_tp | -10.4760 | -4.6478 | -5.8282 |
| WETUSDT | 2026-04-14T00:03:20.957342+00:00 | emergency_manual | -2.4452 | +3.2710 | -5.7162 |
| BLURUSDT | 2026-04-19T08:57:36.456957+00:00 | profit_take | +11.3309 | +17.0450 | -5.7141 |
| BSBUSDT | 2026-04-22T00:04:01.235717+00:00 | shadow_sl_tp | -53.3414 | -58.8903 | +5.5489 |
| SOONUSDT | 2026-04-23T18:42:05.894285+00:00 | sentinel_deadline_profit | +5.6216 | +0.2541 | +5.3675 |
| ZECUSDT | 2026-04-24T17:08:53.433370+00:00 | time_decay_p_win_low | -6.1999 | -11.5372 | +5.3373 |
| STRKUSDT | 2026-04-22T16:50:12.359466+00:00 | profit_take | +31.9102 | +26.7508 | +5.1594 |
| COREUSDT | 2026-04-21T23:26:15.972442+00:00 | shadow_sl_tp | -16.0564 | -21.2095 | +5.1531 |
| RAVEUSDT | 2026-04-17T19:13:56.370532+00:00 | shadow_sl_tp | -6.0502 | -10.8886 | +4.8384 |
| TAOUSDT | 2026-04-08T12:16:28.188860+00:00 | shadow_sl_tp | +8.0720 | +3.2401 | +4.8319 |
| CLUSDT | 2026-04-17T18:35:19.676510+00:00 | early_exit | -29.3517 | -34.1568 | +4.8051 |
| MAGMAUSDT | 2026-04-10T16:07:44.089846+00:00 | shadow_sl_tp | -2.5978 | -7.2804 | +4.6826 |

_(table truncated; full update set: 712 rows)_

## Unmatched main rows (sample, first 30)

These rows in trade_intelligence have no Shadow virtual_positions counterpart within ±90s of trade_closed_at for the same symbol. Possible reasons: pre-Shadow rows, manual closes that bypassed Shadow, or imported test data.

| symbol | trade_closed_at | pnl_usd | closed_by |
|---|---|---|---|
| PNUTUSDT | 2026-04-17T19:13:51.149071+00:00 | +11.7645 | shadow_sl_tp |
| RAVEUSDT | 2026-04-18T10:57:45.284960+00:00 | +16.9772 | shadow_sl_tp |
| HUSDT | 2026-04-18T11:02:47.486592+00:00 | -5.0050 | shadow_sl_tp |
| ALICEUSDT | 2026-04-18T11:12:25.845505+00:00 | -1.4595 | shadow_sl_tp |
| AXLUSDT | 2026-04-18T11:12:27.751447+00:00 | -4.0381 | shadow_sl_tp |
| THETAUSDT | 2026-04-18T11:42:02.534420+00:00 | -38.0416 | shadow_sl_tp |
| BIOUSDT | 2026-04-19T09:28:32.357117+00:00 | -44.1871 | shadow_sl_tp |
| ZROUSDT | 2026-04-19T18:45:51.130154+00:00 | -1.5599 | shadow_sl_tp |
| AAVEUSDT | 2026-04-19T19:19:31.436502+00:00 | -65.6528 | shadow_sl_tp |
| HYPEUSDT | 2026-04-20T08:10:35.952013+00:00 | +7.4999 | shadow_sl_tp |
| BASEDUSDT | 2026-04-20T08:13:50.616222+00:00 | +7.5355 | shadow_sl_tp |
| TRBUSDT | 2026-04-20T08:31:46.236783+00:00 | -1.6073 | shadow_sl_tp |
| CFGUSDT | 2026-04-20T08:32:39.048488+00:00 | -4.7970 | shadow_sl_tp |
| MNTUSDT | 2026-04-20T09:08:37.920438+00:00 | -10.6084 | shadow_sl_tp |
| SIRENUSDT | 2026-04-20T09:08:48.712005+00:00 | +3.1663 | shadow_sl_tp |
| LDOUSDT | 2026-04-20T09:09:50.203029+00:00 | -7.7199 | shadow_sl_tp |
| IRYSUSDT | 2026-04-20T10:10:14.415953+00:00 | +5.1546 | shadow_sl_tp |
| EIGENUSDT | 2026-04-20T10:26:22.724142+00:00 | -8.1243 | shadow_sl_tp |
| PRLUSDT | 2026-04-21T09:16:09.134960+00:00 | +11.9242 | trailing_stop |
| ARIAUSDT | 2026-04-21T09:23:48.048322+00:00 | +14.4845 | shadow_sl_tp |
| SENTUSDT | 2026-04-21T09:48:36.456592+00:00 | +3.2046 | shadow_sl_tp |
| GIGGLEUSDT | 2026-04-21T14:32:34.108959+00:00 | +6.7005 | shadow_sl_tp |
| ARIAUSDT | 2026-04-21T15:04:11.745409+00:00 | -26.2011 | shadow_sl_tp |
| SPXUSDT | 2026-04-21T15:04:28.493320+00:00 | -10.9865 | shadow_sl_tp |
| XLMUSDT | 2026-04-21T15:04:39.730935+00:00 | -12.8174 | shadow_sl_tp |
| NEWTUSDT | 2026-04-21T15:04:51.509641+00:00 | -59.9668 | shadow_sl_tp |
| NEWTUSDT | 2026-04-21T19:17:38.081621+00:00 | +0.6192 | shadow_sl_tp |
| DASHUSDT | 2026-04-22T00:12:14.645126+00:00 | +8.3143 | shadow_sl_tp |
| SEIUSDT | 2026-04-22T15:08:32.681048+00:00 | +0.6336 | shadow_sl_tp |
| RIVERUSDT | 2026-04-22T15:08:33.865308+00:00 | +1.8291 | shadow_sl_tp |

_(table truncated; full unmatched set: 36 rows)_

## Notes

- Shadow's ``virtual_positions.net_pnl_usd`` is post-fee post-slippage; main's pre-fix ``pnl_usd`` was pre-slippage and missing exit fee for self-initiated closes.
- The ±0.03% entry-price slippage gap is by design (``shadow/config.toml [exchange] slippage_pct = 0.03``); the join key is ``(symbol, trade_closed_at within ±90s)``, not ``entry_price``.
- After APPLY, updated rows carry ``pnl_source = 'shadow_authoritative_backfill_2026-05-03'``. Phase 1's helper at ``trade_coordinator.py:resolve_authoritative_pnl`` ensures new closes going forward will record Shadow's value directly (the row's pnl_source remains the default ``'main_local'`` because the writer isn't aware of the column; the bypass is via the helper's own WD_LAST_CLOSE_AUTH log line).
- This was a DRY-RUN. Re-run with ``--apply`` to write changes; a backup will be taken first.