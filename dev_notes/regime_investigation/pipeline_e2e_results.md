# End-to-End Pipeline Check — Real Production Verification

Conducted 2026-05-12 08:56 UTC against the real `data/trading.db` and the real B1a config.

Script: `scripts/pipeline_e2e_check.py` (read-only; uses sqlite URI `mode=ro` so it does not contend with the live `workers.py` process at pid 398).

## What was verified end-to-end

```
config.toml -> Settings._load_fresh() -> RegimeSettings -> RegimeDetector(settings, ta_engine, repo)
                                                          |
                                                          v
                                                detector.detect(symbol)
                                                          |
                                                          v
                                       Real H1 klines from data/trading.db
                                                          |
                                                          v
                                              TAEngine.analyze(candles)
                                                          |
                                                          v
                                              Classification branch (lines 133-156)
                                                          |
                                                          v
                                              RegimeState + hysteresis cache + REGIME log
                                                          |
                                                          v
                                                detector.get_coin_regime(sym)
                                                          |
                                                          v
                                              Consumer-facing read by ensemble, scanner,
                                                  scorer, strategist, APEX, etc.
```

Every stage executes against real production code with no mocks (except the read-only sqlite wrapper).

## Stage results

### Stage 1 — Settings load

```
trending_adx_threshold       = 20  (expect 20)  PASS
ranging_adx_threshold        = 20                PASS
ranging_choppiness_threshold = 50  (expect 50)  PASS
volatile_atr_percentile      = 70  (expect 70)  PASS
dead_adx_threshold           = 12  (expect 12)  PASS
```

### Stage 2 — DI wiring

```
detector.settings.regime.trending_adx_threshold = 20
RegimeDetector wired with read-only repo against data/trading.db
STATUS: PASS
```

### Stage 3 — Detector against 12 real symbols (REAL DATA)

```
symbol      regime           conf     adx     +DI     -DI    chop    natr%   vol_r  klines
BTCUSDT     trending_down    0.44    22.0    20.5    43.3    32.5    30.93    0.14     200
ETHUSDT     trending_down    0.49    24.3    15.7    47.7    33.8    43.07    0.28     200
SOLUSDT     ranging          0.40    12.9    29.2    32.6    41.5    62.63    0.06     200
XRPUSDT     ranging          0.73    12.7    26.2    31.1    58.2    61.11    0.07     200
ARBUSDT     trending_down    0.44    22.0    19.8    36.2    43.2    87.27    0.05     200
ADAUSDT     ranging          0.40    18.4    17.6    35.9    47.3    58.29    0.05     200
AVAXUSDT    ranging          0.40    16.9    19.3    37.2    42.0    62.65    0.04     200
LINKUSDT    ranging          0.40    19.4    18.7    39.9    38.0    58.45    0.11     200
DOGEUSDT    ranging          0.76    13.2    25.9    33.9    60.6    68.39    0.03     200
BNBUSDT     ranging          0.40    14.8    27.0    36.6    46.2    43.78    0.01     200
NEARUSDT    volatile         0.48    12.5    36.0    25.5    38.1    95.98    0.13     200
ATOMUSDT    volatile         0.38    12.7    35.0    22.7    43.0    76.86    0.10     200
```

Distribution: trending_down=3 (25%), ranging=7 (58%), volatile=2 (17%), dead=0, trending_up=0.

### Key real-world wins from B1a (compared to the OLD config that the live workers.py still has)

| Symbol | ADX | Chop | NATR% | OLD config would label | NEW config labels | Change |
|---|---|---|---|---|---|---|
| BTCUSDT | 22.0 | 32.5 | 30.93 | `ranging` (ELSE, ADX<25) | **`trending_down`** | **CAUGHT TREND** |
| ETHUSDT | 24.3 | 33.8 | 43.07 | `ranging` (ELSE, ADX<25) | **`trending_down`** | **CAUGHT TREND** |
| ARBUSDT | 22.0 | 43.2 | 87.27 | `ranging` (ELSE, ADX<25) | **`trending_down`** | **CAUGHT TREND** |
| XRPUSDT | 12.7 | 58.2 | 61.11 | `ranging` (ELSE, chop<60) | **`ranging conf=0.73`** | **STRICT branch fires (not fallback)** |
| DOGEUSDT | 13.2 | 60.6 | 68.39 | `ranging conf=0.76` (strict) | `ranging conf=0.76` | unchanged (already strict) |
| NEARUSDT | 12.5 | 38.1 | 95.98 | `ranging` (ELSE, NATR<150 unreachable) | **`volatile`** | **CAUGHT VOLATILITY** |
| ATOMUSDT | 12.7 | 43.0 | 76.86 | `ranging` (ELSE) | **`volatile`** | **CAUGHT VOLATILITY** |
| SOLUSDT | 12.9 | 41.5 | 62.63 | `ranging` (ELSE) | `ranging` (ELSE) | unchanged (narrow ELSE still fires here — too far from strict ranging or trending) |

**3 symbols now correctly classify as `trending_down` instead of `ranging`**. This is exactly the bug the B1a fix targets: catching the [20, 25) ADX transition band that was previously falling through to ELSE = RANGING.

**2 symbols now correctly classify as `volatile`** because their NATR-derived atr_percentile is in [70, 100] (above the new threshold; old threshold 150 was unreachable).

**1 symbol (XRPUSDT)** now hits the strict ranging branch at chop=58.2 (previously needed >60, now needs >50) producing `conf=0.73` instead of fallback `conf=0.40`. This is an informative ranging signal vs an uninformative fallback.

### Stage 4 — Hysteresis state machine (real BTCUSDT)

```
First detect:  trending_down
Second detect: trending_down
_confirmed_regimes['BTCUSDT'] -> trending_down
detector._last_regime: trending_down
STATUS: PASS — hysteresis cache populated per-symbol
```

### Stage 5 — Consumer-facing read APIs

```
detector.is_ready() = True
detector._per_coin_regimes size = 12
get_coin_regime('BTCUSDT') -> trending_down  (7 active categories: scalping, momentum, advanced, ...)
get_coin_regime('ETHUSDT') -> trending_down  (7 active categories: scalping, momentum, advanced, ...)
get_coin_regime('SOLUSDT') -> ranging        (7 active categories: scalping, mean_reversion, funding_arb, ...)
get_coin_regime('XRPUSDT') -> ranging        (7 active categories: scalping, mean_reversion, funding_arb, ...)
get_last_regime() -> trending_down
STATUS: PASS
```

The `active_strategy_categories` correctly differs between trending_down (momentum + advanced + predatory) and ranging (mean_reversion + funding_arb + microstructure). This is the actual control-flow gate that consumers (ensemble.py, scorer.py) use.

### Stage 6 — Persistence schema check

```
Tables present: ['regime_history', 'coin_regime_history']
regime_history: 2868 rows total
coin_regime_history: 63189 rows total
STATUS: PASS — both regime persistence tables present and populated
```

Existing schema unchanged (no migrations needed for B1a). The running workers.py has been writing to both tables continuously.

## Live workers.py process state

The currently-running `workers.py` (pid 398) was started at 2026-05-12 07:02:40 — **before** the B1a fix was committed. It is therefore still running the OLD config with thresholds 25/60/150/15. Confirmation:

```
$ grep "REGIME |" data/logs/workers.log | tail -1
2026-05-12 07:02:52.134 | INFO | src.strategies.regime:detect:171 | REGIME | sym=BTCUSDT rgm=ranging conf=0.40 adx=17.3 chop=30.6 | no_ctx
```

Same coin, ~1h45m earlier (ADX has since moved from 17.3 to 22.0 on the H1 timescale). The OLD config classified it as ranging conf=0.40. **After restart, the new config will classify it as trending_down conf=0.44** — the exact downstream effect the B1a fix was designed to produce.

## Comparison with Phase 2 empirical findings

| Phase | Source | False-ranging rate | conf=0.40 share | Trending share |
|---|---|---|---|---|
| Phase 2 (pre-B1a, 48h logs) | OLD config (25/60/150/15) | 88.2% | 73.9% | 8.8% |
| Stage 3 pipeline check (post-B1a, live data) | NEW config (20/50/70/12) | 0/7 strict ranging matches expected pattern | 5/12 = 41.7% | 3/12 = 25.0% |

Stage 3 sample (n=12) is small but the directional shift matches the Phase 2 prediction: trending share rises (8.8% → 25.0%), ELSE-fallback share falls (73.9% → ~42%). Once the operator restarts workers.py, the live 48-hour distribution will quickly drift toward the Stage 3 pattern.

## What this proves

1. The fix is correctly woven into the code (Settings → RegimeDetector → consumers all use the new thresholds).
2. The fix correctly affects classification at the EXACT BOUNDARY points the investigation identified: ADX in [20, 25) now classifies as trending; chop in [50, 60] now classifies as strict ranging; NATR in [70, 100] now classifies as volatile.
3. The downstream consumer APIs (`get_coin_regime`, `get_last_regime`, `is_ready`, `active_strategy_categories`) produce correctly-typed values.
4. The persistence schema is intact; no migrations needed.
5. The live process needs only a restart to pick up the new behavior.

## Operator's next steps (Phase 5)

1. **Restart workers.py** (pid 398). The new config takes effect immediately on startup.
2. **Watch `data/logs/workers.log`** for the first hour of REGIME emissions; expect:
   - More variety in regime labels per 5-min bucket.
   - Fewer conf=0.40 emissions (target: drop from 73.9% to ~25-40%).
   - Trending labels (trending_up + trending_down) rising from 8.8% to ~20-30%.
3. **Watch APEX_FLIP_DECISION events**; expect:
   - More `dir_locked=Y` events on coins now correctly tagged trending.
   - Stable `flip_accepted=N` rate (PRIMARY fix still in place).
4. **Watch XRAY_DIR_FLIP**; expect: fewer fires because more coins now have APEX direction lock active.
5. **After 4-6 hours**, re-run `scripts/regime_accuracy_probe.py` and fill in the table in `phase5_verification.md`.
6. Decision per `phase5_verification.md` decision tree: keep Path B1a alone, or proceed with Path A (XRAY threshold tune to 10.0) if XRAY flip count remains high.
