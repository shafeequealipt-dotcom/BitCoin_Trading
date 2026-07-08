# EXPERIMENT COMPARISON

Baseline : 2000-01-01 .. 2026-07-06  (6 trades)
Treatment: 2026-07-07 .. 2026-07-08  (2 trades)

## OVERALL
metric          baseline   treatment       delta
n                      6           2          -4
win%               16.7        0.0      -16.7
PF                  0.86        0.00
exp$               -0.91      -11.49      -10.57
net$               -5.49      -22.97      -17.48

## PER-STRATEGY (expectancy delta = treatment - baseline)
strategy                  base_exp treat_exp delta_exp   verdict
claude_trader                -0.91    -11.49    -10.57 REGRESSED

TOP IMPROVED (top 1):
  + claude_trader: exp delta $-10.57 (REGRESSED)
BOTTOM REGRESSED (top 1):
  - claude_trader: exp delta $-10.57 (REGRESSED)
