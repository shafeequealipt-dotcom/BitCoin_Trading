# 30-DAY TRADE ANALYSIS

Source table: trade_intelligence
Window: since 2026-06-09
Trades analyzed: 7

OVERALL: n=7 win%=14.3 PF=0.77 exp=$-1.47 net=$-10.30 avg%=-0.13

## BY STRATEGY

group                         n   win%        PF      exp$       net$    avg%
claude_trader                 7  14.3      0.77     -1.47     -10.30   -0.13

## BY STRATEGY CATEGORY

group                         n   win%        PF      exp$       net$    avg%
claude_direct                 7  14.3      0.77     -1.47     -10.30   -0.13

## BY REGIME

group                         n   win%        PF      exp$       net$    avg%
trending_up                   4  25.0      2.40      4.92     +19.70   +0.10
trending_down                 2   0.0      0.00     -5.44     -10.87   -0.48
ranging                       1   0.0      0.00    -19.12     -19.12   -0.32

## BY TIAS CATEGORY (ds_category)

group                         n   win%        PF      exp$       net$    avg%
CORRECT_ENTRY                 1 100.0 inf (no losses)     33.73     +33.73   +1.01
ENTRY_TOO_EARLY               1   0.0      0.00     -0.75      -0.75   -0.15
SIGNAL_NOISE                  1   0.0      0.00     -1.15      -1.15   -0.13
(unknown)                     2   0.0      0.00     -5.44     -10.87   -0.48
STOP_TOO_TIGHT                2   0.0      0.00    -15.63     -31.26   -0.33

## BY DIRECTION

group                         n   win%        PF      exp$       net$    avg%
Buy                           5  20.0      1.68      2.73     +13.64   +0.01
Sell                          2   0.0      0.00    -11.97     -23.94   -0.47

## BY SYMBOL

group                         n   win%        PF      exp$       net$    avg%
PYTHUSDT                      1 100.0 inf (no losses)     33.73     +33.73   +1.01
AAVEUSDT                      1   0.0      0.00     -0.75      -0.75   -0.15
BLURUSDT                      1   0.0      0.00     -1.15      -1.15   -0.13
SEIUSDT                       1   0.0      0.00     -4.81      -4.81   -0.63
BSBUSDT                       1   0.0      0.00     -6.06      -6.06   -0.33
MONUSDT                       1   0.0      0.00    -12.14     -12.14   -0.35
ONDOUSDT                      1   0.0      0.00    -19.12     -19.12   -0.32

## BY EXIT REASON (closed_by)

group                         n   win%        PF      exp$       net$    avg%
bybit_sl_hit                  4  25.0      1.05      0.43      +1.72   +0.05
bybit_demo_sl_tp              1   0.0      0.00     -1.15      -1.15   -0.13
wd_timeout                    1   0.0      0.00     -4.81      -4.81   -0.63
wd_dl_action                  1   0.0      0.00     -6.06      -6.06   -0.33

## BY APEX OPTIMIZED (0=raw, 1=APEX)

group                         n   win%        PF      exp$       net$    avg%
0                             7  14.3      0.77     -1.47     -10.30   -0.13

## BY APEX FLIPPED (0=no, 1=flipped)

group                         n   win%        PF      exp$       net$    avg%
0                             7  14.3      0.77     -1.47     -10.30   -0.13

## BEST STRATEGY x REGIME (min 5 trades)

  (not enough samples with the minimum trade count)

## TOP 10 INDIVIDUAL TRADES (by net USD)

  + PYTHUSDT Buy claude_trader | trending_up | CORRECT_ENTRY | raw | $+33.73
  + AAVEUSDT Buy claude_trader | trending_up | ENTRY_TOO_EARLY | raw | $-0.75
  + BLURUSDT Buy claude_trader | trending_up | SIGNAL_NOISE | raw | $-1.15
  + SEIUSDT Sell claude_trader | trending_down | None | raw | $-4.81
  + BSBUSDT Buy claude_trader | trending_down | None | raw | $-6.06
  + MONUSDT Buy claude_trader | trending_up | STOP_TOO_TIGHT | raw | $-12.14
  + ONDOUSDT Sell claude_trader | ranging | STOP_TOO_TIGHT | raw | $-19.12
