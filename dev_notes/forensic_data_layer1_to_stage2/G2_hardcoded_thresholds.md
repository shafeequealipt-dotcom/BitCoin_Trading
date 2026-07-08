# G2 — Hardcoded Thresholds in Layer 1 Code

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Scope:** `src/workers/{price,kline,news,altdata,signal,regime,structure,strategy,scanner}_worker.py`, `src/strategies/{scanner,regime,scorer,ensemble}.py`, `src/analysis/structure/*.py`, `src/core/{coin_package_validator,freshness_guard}.py`
- **Method:** Module-level constants + inline numeric comparisons grepped via `^\s*_?[A-Z][A-Z0-9_]*\s*=\s*[0-9]`, `[<>]=?\s*[0-9.]+`, `min_/max_/threshold` substring; verified with file:line reads.

The `Config?` column flags whether the value should arguably live in `config.toml`. `Y` = belongs in config; `N` = legitimately hardcoded (algorithmic constant, structural minimum, data-shape invariant).

---

## Layer 1A — `src/workers/price_worker.py` (264 lines)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| price_worker.py:186 | `if last_price <= 0` | `0` | reject non-positive WS quote (data-shape invariant) | N |
| price_worker.py:239 | `def get_ws_quote(..., max_age_s: float = 5.0)` | `5.0` s | default WS-quote freshness window for callers | Y |
| price_worker.py:255 | `if _time.monotonic() - ts > max_age_s` | param | freshness gate using parameter above | (n/a) |
| price_worker.py:257 | `return price if price > 0 else None` | `0` | reject non-positive quote | N |

No module-level constants in price_worker.py.

---

## Layer 1A — `src/workers/kline_worker.py` (494 lines)

Module-level constants (lines 32-50):
```
TIMEFRAME_SCHEDULE = {
    TimeFrame.M5: 60,
    TimeFrame.H1: 60,
    TimeFrame.H4: 300,
    TimeFrame.D1: 3600,
}
_KLINE_FRESHNESS_THRESHOLD_S = 600.0
_LAG_QUERY_MAX_SYMBOLS = 500
```

| File:line | Variable | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| kline_worker.py:33 | `TIMEFRAME_SCHEDULE[M5]` | `60` s | per-tick min interval — M5 fetched on every kline_worker tick | Y |
| kline_worker.py:34 | `TIMEFRAME_SCHEDULE[H1]` | `60` s | min interval between H1 fetches | Y |
| kline_worker.py:35 | `TIMEFRAME_SCHEDULE[H4]` | `300` s | min interval between H4 fetches | Y |
| kline_worker.py:36 | `TIMEFRAME_SCHEDULE[D1]` | `3600` s | min interval between D1 fetches | Y |
| kline_worker.py:44 | `_KLINE_FRESHNESS_THRESHOLD_S` | `600.0` | KLINE_FRESHNESS_WARN trigger after 2 missed M5 closes | Y |
| kline_worker.py:50 | `_LAG_QUERY_MAX_SYMBOLS` | `500` | SQLite IN-clause cap (algorithmic — `SQLITE_MAX_VARIABLE_NUMBER=999`) | N |
| kline_worker.py:140 | `if ratio < 0.5` | `0.5` | quality bucket: <50% expected klines → "critical" | Y |
| kline_worker.py:142 | `elif ratio < 0.9` | `0.9` | quality bucket: <90% → "warning" | Y |
| kline_worker.py:340 | `_M5_PERIOD_S = 300` | `300` s | M5 candle period (algorithmic constant) | N |
| kline_worker.py:341 | `_LAG_BUFFER_S = 60` | `60` s | tolerated lag past M5 close | Y |
| kline_worker.py:342 | `_LAG_THRESHOLD_S = _M5_PERIOD_S + _LAG_BUFFER_S` | `360` s | derived | (derived) |

---

## Layer 1A — `src/workers/altdata_worker.py` (274 lines)

Grep `^\s*[A-Z_][A-Z_0-9]*\s*=\s*[0-9]` returned nothing. Grep `[<>]=?\s*[0-9.]+|threshold|min_|max_` against the file body returned nothing. AltDataWorker pulls all cadences from `settings.workers.sweet_spots.altdata.*` and `settings.altdata.*`. **No hardcoded thresholds detected.**

---

## Layer 1A — `src/workers/news_worker.py` (not separately greped — Reddit gating handled in manager.py only)

Grep `^\s*[A-Z_][A-Z_0-9]*\s*=\s*[0-9]` returned nothing for news_worker.py.

---

## Layer 1A — `src/workers/price_worker.py`

Already covered above.

---

## Layer 1B — `src/workers/structure_worker.py` (368 lines)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| structure_worker.py:127 | `if not candles or len(candles) < self.settings.structure.min_candles` | settings | min candles for structural analysis (already in config) | (already config) |
| structure_worker.py:219 | `_p50 = _sorted[max(0, int(0.50 * (_n - 1)))]` | `0.50` | percentile (algorithmic) | N |
| structure_worker.py:220 | `_p95 = _sorted[max(0, int(0.95 * (_n - 1)))]` | `0.95` | percentile (algorithmic) | N |
| structure_worker.py:283 | `if stats["cached_entries"] > 0` | `0` | guard for division (algorithmic) | N |
| structure_worker.py:354 | `if candles and len(candles) >= self.settings.structure.min_candles` | settings | already in config | (already config) |
| structure_worker.py:363 | `if candles and len(candles) >= self.settings.structure.min_candles` | settings | already in config | (already config) |

Module-level constants: none.

---

## Layer 1B — `src/workers/signal_worker.py` (178 lines)

Grep `^\s*[A-Z_][A-Z_0-9]*\s*=\s*[0-9]` returned nothing. Grep `[<>]=?\s*[0-9.]+` returned nothing. **No hardcoded thresholds detected.**

---

## Layer 1B — `src/workers/regime_worker.py` (313 lines)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| regime_worker.py:235 | `if divergent > 0` | `0` | non-zero guard (algorithmic) | N |
| regime_worker.py:282 | `if self._cleanup_counter >= 100` | `100` | run cleanup every 100 ticks | Y |

Module-level constants: none.

---

## Layer 1C — `src/workers/strategy_worker.py` (1592 lines)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| strategy_worker.py:248 | `_kline_max_age_s = 300.0` | `300.0` s | STRAT_SKIP_STALE gate: skip TA on klines older than 5 min | Y |
| strategy_worker.py:275 | `if len(_stale_syms) < 5` | `5` | sample size for STRAT_SKIP_STALE_AGG log | N |
| strategy_worker.py:283 | `if len(klines) >= 50` | `50` | min bars for TA (algorithmic — TA libraries fail < 50) | N |
| strategy_worker.py:289 | `if _coin_ms > 200` | `200` ms | per-coin TA "slow" classification | Y |
| strategy_worker.py:339 | `if not klines_h1 or len(klines_h1) < 50` | `50` | min H1 bars for TA pre-population | N |
| strategy_worker.py:447 | `if _section_ms["prefetch"] > 5000` | `5000` ms | STRAT_PREFETCH_SLOW WARN | Y |
| strategy_worker.py:459 | `if _section_ms["prefetch"] > 8000` | `8000` ms | STRAT_PREFETCH_CRITICAL ERROR (24h shows 5 fires) | Y |
| strategy_worker.py:501 | `if _strat_ms > 2000` | `2000` ms | per-strategy slow warn | Y |
| strategy_worker.py:510 | `if _section_ms["l1"] > 5000` | `5000` ms | L1 slow warn | Y |
| strategy_worker.py:629 | `if _section_ms["l2"] > 2000` | `2000` ms | L2 slow warn | Y |
| strategy_worker.py:872 | `if _cycle_el > 30000` | `30000` ms | STRAT_TICK_SLOW threshold (>30s) | Y |
| strategy_worker.py:880 | `if len(self._tick_times) >= 10` | `10` | rolling window for tick timing stats | N |
| strategy_worker.py:1067 | `if _structural.setup_quality == "SKIP" and _rr is not None and _rr < 0.5` | `0.5` | RR-skip gate when structure marks SKIP | Y |
| strategy_worker.py:1099 | `if direction == "Buy" and _sp.rr_long < 1.0 and _sp.rr_short >= 2.0` | `1.0`, `2.0` | direction-flip detector: weak long but strong short | Y |
| strategy_worker.py:1105 | `elif direction == "Sell" and _sp.rr_short < 1.0 and _sp.rr_long >= 2.0` | `1.0`, `2.0` | direction-flip detector: weak short but strong long | Y |
| strategy_worker.py:1123 | `if _ratio > 5.0` | `5.0` | "block at >5×" RR-asymmetry block | Y |
| strategy_worker.py:1135 | `if _ratio > 3.0` | `3.0` | "size-reduce at >3×" RR-asymmetry warn | Y |
| strategy_worker.py:1238 | `if sz_mult < 1.0` | `1.0` | size multiplier guard | N |
| strategy_worker.py:1310-1320 | `sl >= current_price`, `tp <= current_price`, etc. | `0` | direction-consistent SL/TP guards (data-shape) | N |
| strategy_worker.py:1371 | `if qty <= 0` | `0` | reject non-positive qty | N |

Module-level constants: none — `_kline_max_age_s` etc. are function-local literals.

---

## Layer 1D — `src/workers/scanner_worker.py` (1090 lines)

Composite scoring component normalization constants (lines 187-200):
```
struct_norm = max(0.0, min(1.0, (struct_raw or 0.0) / 100.0))
strat_norm  = max(0.0, min(1.0, (strat_raw or 0.0) / 100.0))
sig_norm    = max(0.0, min(1.0, self._get_signal_confidence(coin) or 0.0))
regime_norm = (regime_align + 1.0) / 2.0
funding_norm = max(0.0, min(1.0, (funding_raw or 0.0) / 0.001))
```

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| scanner_worker.py:147 | `return 1.0` (regime trending) | `1.0` | regime alignment factor for trending_up/down | Y |
| scanner_worker.py:149 | `return 0.5` (regime volatile) | `0.5` | regime alignment factor for volatile | Y |
| scanner_worker.py:151 | `return 0.0` (regime ranging) | `0.0` | regime alignment factor for ranging | Y |
| scanner_worker.py:152 | `return -1.0` (regime dead/unknown) | `-1.0` | regime alignment factor for dead/unknown | Y |
| scanner_worker.py:187 | `(struct_raw or 0.0) / 100.0` | `100.0` | structure_score divisor (X-RAY 0-100 → 0-1) | N |
| scanner_worker.py:190 | `(strat_raw or 0.0) / 100.0` | `100.0` | strategy_score divisor (TradeScorer 0-100 → 0-1) | N |
| scanner_worker.py:200 | `(funding_raw or 0.0) / 0.001` | `0.001` (0.1%) | funding-rate saturation threshold for normalization | Y |
| scanner_worker.py:285 | `if rate > cfg.funding_blocker_threshold_pct` | settings | already in config (`scanner.qualitative.funding_blocker_threshold_pct = 0.001`) | (already config) |
| scanner_worker.py:289 | `elif rate < -cfg.funding_blocker_threshold_pct` | settings | mirror | (already config) |
| scanner_worker.py:432-433 | `"longs_paying" if rate > 0 else "shorts_paying" if rate < 0` | `0` | sign label (algorithmic) | N |
| scanner_worker.py:591 | `if rr < cfg.min_rr_ratio` | settings | already in config (`scanner.qualitative.min_rr_ratio = 2.0`) | (already config) |
| scanner_worker.py:778 | `n_max = int(cfg_q.max_selection)` | settings | already in config (`scanner.qualitative.max_selection = 15`) | (already config) |
| scanner_worker.py:779 | `n_min = int(cfg_q.min_selection)` | settings | already in config (`scanner.qualitative.min_selection = 0`) | (already config) |
| scanner_worker.py:839 | `_fail_below = float(getattr(_vld_cfg, "fail_below", 0.50)) if _vld_cfg else 0.50` | `0.50` (fallback) | validator fail cutoff fallback when settings missing (config has `coin_package_validator.fail_below = 0.50`) | (fallback) |
| scanner_worker.py:840 | `_warn_below ... 0.85` | `0.85` (fallback) | validator warn cutoff fallback (config has `coin_package_validator.warn_below = 0.85`) | (fallback) |
| scanner_worker.py:842-843 | `staleness_fail_seconds ... 300.0` | `300.0` s (fallback) | validator staleness fallback (config has `coin_package_validator.staleness_fail_seconds = 300.0`) | (fallback) |
| scanner_worker.py:939 | `_pct_or_unk(klines_ages, 0.50)` | `0.50` | percentile (algorithmic) | N |
| scanner_worker.py:940 | `_pct_or_unk(klines_ages, 0.95)` | `0.95` | percentile (algorithmic) | N |

Module-level constants: none.

---

## `src/strategies/scanner.py` (`MarketScanner`, NOT the worker)

Module-level: `CACHE_TTL_SECONDS = 300` (line 18).

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| scanner.py:18 | `CACHE_TTL_SECONDS = 300` | `300` s | TTL for the active-universe cache (already there is also `[scanner].scan_interval_seconds = 300`) | Y |
| scanner.py:188 | `if now_ts - t < 3600` | `3600` s | 1-hour cooldown registry purge window | Y |
| scanner.py:262 | `sorted_scored[max_coins - 1].get("score", 0)` | settings | uses settings.scanner.max_coins | (already config) |
| scanner.py:268 | `entry_floor = cutoff + hyst_cfg.entry_threshold_above_min` | settings | uses scanner.hysteresis.entry_threshold_above_min | (already config) |
| scanner.py:269 | `exit_ceiling = cutoff + hyst_cfg.exit_threshold_below_min` | settings | mirror | (already config) |
| scanner.py:424 | `if vol < 5_000_000` | `5_000_000` USDT | hardcoded volume reject (separate from `[scanner].min_volume_24h = 5000000` — duplicate constant!) | Y |
| scanner.py:426 | `if price < 0.0001` | `0.0001` | hardcoded micro-price reject | Y |
| scanner.py:433 | `if spread_pct > 0.5` | `0.5` % | hardcoded spread reject (separate from `[scanner].max_spread_pct = 0.15` — different number!) | Y |
| scanner.py:449 | `if change_abs >= 10` | `10` | tiered momentum bucket | Y |
| scanner.py:451 | `elif change_abs >= 5` | `5` | tier | Y |
| scanner.py:453 | `elif change_abs >= 3` | `3` | tier | Y |
| scanner.py:455 | `elif change_abs >= 1.5` | `1.5` | tier | Y |
| scanner.py:457 | `elif change_abs >= 0.8` | `0.8` | tier | Y |
| scanner.py:463 | `if daily_range_pct >= 8` | `8` | tiered daily-range bucket | Y |
| scanner.py:465 | `elif daily_range_pct >= 5` | `5` | tier | Y |
| scanner.py:467 | `elif daily_range_pct >= 3` | `3` | tier | Y |
| scanner.py:469 | `elif daily_range_pct >= 1.5` | `1.5` | tier | Y |
| scanner.py:475 | `if trend_ratio >= 0.6` | `0.6` | tiered trend-ratio bucket | Y |
| scanner.py:477 | `elif trend_ratio >= 0.4` | `0.4` | tier | Y |
| scanner.py:479 | `elif trend_ratio >= 0.25` | `0.25` | tier | Y |
| scanner.py:485 | `if vol >= 500_000_000` | `500_000_000` USDT | tiered volume bucket | Y |
| scanner.py:487 | `elif vol >= 100_000_000` | `100_000_000` | tier | Y |
| scanner.py:489 | `elif vol >= 50_000_000` | `50_000_000` | tier | Y |
| scanner.py:491 | `elif vol >= 20_000_000` | `20_000_000` | tier | Y |
| scanner.py:493 | `elif vol >= 5_000_000` | `5_000_000` | tier | Y |
| scanner.py:497 | `if spread_pct <= 0.02` | `0.02` % | tiered spread bucket | Y |
| scanner.py:499 | `elif spread_pct <= 0.05` | `0.05` % | tier | Y |
| scanner.py:501 | `elif spread_pct <= 0.10` | `0.10` % | tier | Y |

---

## `src/strategies/regime.py` (`RegimeDetector`)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| regime.py:80 | `if len(klines) < 50` | `50` | min bars for regime detection (algorithmic) | N |
| regime.py:122 | `if adx > cfg.trending_adx_threshold and ... and choppiness < 45` | `45` | choppiness inverse-cutoff hardcoded for trending classification (config has `trending_adx_threshold` but not the choppiness counter-threshold) | Y |
| regime.py:126 | `elif adx > cfg.trending_adx_threshold and ... and choppiness < 45` | `45` | mirror | Y |
| regime.py:130 | `elif atr_percentile > cfg.volatile_atr_percentile or volume_ratio > 2.0` | `2.0` | hardcoded volatile-volume multiplier | Y |
| regime.py:134 | `elif adx < cfg.ranging_adx_threshold and choppiness > cfg.ranging_choppiness_threshold` | settings | already in config | (already config) |
| regime.py:138 | `elif adx < cfg.dead_adx_threshold and volume_ratio < cfg.dead_volume_ratio and atr_percentile < 50` | `50` | hardcoded ATR-percentile counter-threshold for dead | Y |

---

## `src/strategies/scorer.py` (`TradeScorer`)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| scorer.py:55 | `if total >= 70` | `70` | score-bucket cutoff | Y |
| scorer.py:58 | `if total >= 80` | `80` | quality label | Y |
| scorer.py:60 | `elif total >= 68` | `68` | quality label | Y |
| scorer.py:62 | `elif total >= 56` | `56` | quality label | Y |
| scorer.py:64 | `elif total >= 45` | `45` | quality label | Y |
| scorer.py:119 | `if strength > 0.8` | `0.8` | bucket | Y |
| scorer.py:121 | `elif strength > 0.6` | `0.6` | bucket | Y |
| scorer.py:123 | `elif strength > 0.4` | `0.4` | bucket | Y |
| scorer.py:190 | `if ta_conf > 0.6` | `0.6` | bucket | Y |
| scorer.py:200 | `(is_buy and sent_score > 0.2) or (not is_buy and sent_score < -0.2)` | `0.2` | sentiment direction gate | Y |
| scorer.py:208 | `if fg_val < 15` | `15` | F&G "extreme fear" bucket | Y |
| scorer.py:210 | `elif fg_val < 25` | `25` | bucket | Y |
| scorer.py:212 | `elif fg_val < 35` | `35` | bucket | Y |
| scorer.py:214 | `elif fg_val > 85` | `85` | "extreme greed" bucket | Y |
| scorer.py:216 | `elif fg_val > 75` | `75` | bucket | Y |
| scorer.py:218 | `elif fg_val > 65` | `65` | bucket | Y |
| scorer.py:231 | `(is_buy and fr < -0.01) or (not is_buy and fr > 0.01)` | `0.01` (1%) | funding-rate strong direction gate | Y |
| scorer.py:233 | `elif abs(fr) > 0.005` | `0.005` (0.5%) | funding-rate medium gate | Y |
| scorer.py:259 | `if vol_ratio and vol_ratio > 2.0` | `2.0` | volume surge bucket | Y |
| scorer.py:261 | `elif vol_ratio and vol_ratio > 1.3` | `1.3` | bucket | Y |
| scorer.py:279 | `if dist_pct < 1.0` | `1.0` % | "near support" gate | Y |

---

## `src/strategies/ensemble.py` (`EnsembleVoter`)

Module-local constant inside method (line 99):
```
CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}
```
| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| ensemble.py:99 | `CONSENSUS_SIZE[STRONG]` | `1.0` | size multiplier per consensus tier | Y |
| ensemble.py:99 | `CONSENSUS_SIZE[GOOD]` | `0.75` | tier | Y |
| ensemble.py:99 | `CONSENSUS_SIZE[LEAN]` | `0.50` | tier | Y |
| ensemble.py:99 | `CONSENSUS_SIZE[WEAK]` | `0.30` | tier | Y |
| ensemble.py:99 | `CONSENSUS_SIZE[CONFLICT]` | `0.15` | tier | Y |
| ensemble.py:101 | `if agreeing >= 4.0 and opposing <= 1.5` | `4.0`, `1.5` | STRONG-tier classification | Y |
| ensemble.py:103 | `elif agreeing >= cfg.min_ensemble_agreement and opposing <= cfg.max_ensemble_opposition` | settings | already in config (`strategy_engine.min_ensemble_agreement = 2.5`, `max_ensemble_opposition = 2.5`) | (already config) |
| ensemble.py:105 | `elif agreeing >= 1.5 and opposing <= 1.5` | `1.5`, `1.5` | LEAN/WEAK tier classification | Y |

---

## `src/analysis/structure/setup_scanner.py`

Module-level constants (lines 18-19):
```
MAX_SETUPS = 12
MIN_QUALIFYING_CRITERIA = 3
```

| File:line | Variable | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| setup_scanner.py:18 | `MAX_SETUPS` | `12` | max setups returned to caller (Claude) | Y |
| setup_scanner.py:19 | `MIN_QUALIFYING_CRITERIA` | `3` | must pass at least 3/6 criteria | Y |
| setup_scanner.py:113 | `qual["rr_adequate"] = sp.rr_ratio >= 2.0` | `2.0` | RR adequacy criterion (also `[analysis.structure].min_rr_ratio = 2.0`) | (duplicate) |
| setup_scanner.py:124 | `qual["confluence_good"] = mtf.score >= 5` | `5` | confluence score threshold | Y |
| setup_scanner.py:243 | `if sp.rr_ratio >= 4.0` | `4.0` | A+ setup classification | Y |
| setup_scanner.py:245 | `elif sp.rr_ratio >= 3.0` | `3.0` | A setup classification | Y |
| setup_scanner.py:247 | `elif sp.rr_ratio >= 2.0` | `2.0` | B setup classification | Y |

---

## `src/analysis/structure/fibonacci.py`

Module-level constants (lines 23-30):
```
RETRACE_RATIOS = {"0.236": 0.236, "0.382": 0.382, "0.500": 0.500, "0.618": 0.618, "0.786": 0.786}
EXTEND_RATIOS  = {"1.000": 1.000, "1.272": 1.272, "1.618": 1.618, "2.000": 2.000}
CONFLUENCE_TOLERANCE_PCT = 0.5
MIN_SWING_PCT = 2.0
```
All five values are mathematical Fibonacci constants except `CONFLUENCE_TOLERANCE_PCT = 0.5` and `MIN_SWING_PCT = 2.0` — both could move to config.

---

## `src/analysis/structure/fair_value_gap.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| fair_value_gap.py:25 | `if ratio >= 0.75` | `0.75` | FVG strength bucket | Y |
| fair_value_gap.py:27 | `elif ratio >= 0.5` | `0.5` | bucket | Y |
| fair_value_gap.py:70 | `if n < 10` | `10` | min candles for FVG scan (algorithmic) | N |
| fair_value_gap.py:211 | `partially = max_penetration > 0.3` | `0.3` | partial FVG mitigation threshold | Y |

---

## `src/analysis/structure/order_blocks.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| order_blocks.py:65 | `if n < 10` | `10` | min candles (algorithmic) | N |
| order_blocks.py:121 | `abs(f_idx - i) <= 2` | `2` | FVG-OB index proximity | Y |
| order_blocks.py:148 | `if body_ratio >= 0.75` | `0.75` | OB strength bucket | Y |
| order_blocks.py:150 | `elif body_ratio >= 0.5` | `0.5` | bucket | Y |
| order_blocks.py:174 | `if ob.strength_score >= 40.0 and ob.retests < 3` | `40.0`, `3` | strength + retests gating | Y |

---

## `src/analysis/structure/structural_levels.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| structural_levels.py:217 | `if rr >= 3.0` | `3.0` | quality A+ tier | Y |
| structural_levels.py:219 | `elif rr >= 2.0` | `2.0` | A tier (also config: `min_rr_ratio = 2.0`) | (duplicate) |
| structural_levels.py:221 | `elif rr >= 1.5` | `1.5` | B tier | Y |
| structural_levels.py:228 | `if position < 0.15` | `0.15` | position-in-range bucket | Y |
| structural_levels.py:230 | `elif position < 0.30` | `0.30` | bucket | Y |
| structural_levels.py:232 | `elif position <= 0.70` | `0.70` | bucket | Y |
| structural_levels.py:239 | `if position > 0.85` | `0.85` | bucket | Y |
| structural_levels.py:241 | `elif position > 0.70` | `0.70` | bucket | Y |
| structural_levels.py:243 | `elif position >= 0.30` | `0.30` | bucket | Y |

---

## `src/analysis/structure/liquidity.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| liquidity.py:28 | `if ratio >= 0.6` | `0.6` | strength bucket | Y |
| liquidity.py:30 | `elif ratio >= 0.35` | `0.35` | bucket | Y |
| liquidity.py:37 | `if rev_ratio >= 0.5 and depth_pct >= 0.1` | `0.5`, `0.1` | sweep validity | Y |
| liquidity.py:39 | `elif rev_ratio >= 0.3 or depth_pct >= 0.05` | `0.3`, `0.05` | sweep partial | Y |
| liquidity.py:188 | `if n < 5` | `5` | min candles (algorithmic) | N |

---

## `src/analysis/structure/market_structure.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| market_structure.py:59 | `if n < 20` | `20` | min candles for MS detection | N |
| market_structure.py:75 | `if len(swing_highs) < 2 and len(swing_lows) < 2` | `2` | min swings (algorithmic) | N |
| market_structure.py:197 | `if dominant >= 4` | `4` | strength bucket | Y |
| market_structure.py:199 | `elif dominant >= 2` | `2` | bucket | Y |

---

## `src/analysis/structure/mtf_confluence.py`

(line 38-39 are docstring references, not code; `>= 40` and `>= 2.0` are documented as criteria but the actual code path was not exhaustively dumped.)

---

## `src/core/freshness_guard.py`

Module-level constants (lines 16-17):
```
MAX_TICKER_AGE = 120
MAX_KLINE_AGE = 300
```

| File:line | Variable | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| freshness_guard.py:16 | `MAX_TICKER_AGE` | `120` s | freshness gate for tickers | Y |
| freshness_guard.py:17 | `MAX_KLINE_AGE` | `300` s | freshness gate for klines (matches `[coin_package_validator].staleness_fail_seconds = 300.0`) | Y |

---

## `src/core/coin_package_validator.py`

Function-default fallbacks (line 75-77 of `validate_package`):
```
fail_below: float = 0.50
warn_below: float = 0.85
staleness_fail_seconds: float = 300.0
```
All three are also present in `[coin_package_validator]` section of config.toml — these are defensive fallbacks, not pure hardcodes.

Module-level constants used as verdict labels (lines 49-51):
```
VERDICT_OK = "ok"
VERDICT_WARN = "warn"
VERDICT_FAIL = "fail"
```
String enum values — not thresholds.

---

## Summary of "should-be-in-config" thresholds NOT currently in config.toml

The following appear to be tunable values still hardcoded (`Config? = Y`, no override path through settings):

1. **kline_worker.py: TIMEFRAME_SCHEDULE per-tf cooldowns** (M5=60, H1=60, H4=300, D1=3600 s)
2. **kline_worker.py:140/142:** quality bucket cutoffs (0.5, 0.9)
3. **kline_worker.py:341:** `_LAG_BUFFER_S = 60`
4. **kline_worker.py:44:** `_KLINE_FRESHNESS_THRESHOLD_S = 600.0`
5. **regime_worker.py:282:** cleanup-every-100-ticks
6. **strategy_worker.py:248:** `_kline_max_age_s = 300.0` (STRAT_SKIP_STALE)
7. **strategy_worker.py:289/447/459/501/510/629/872:** TA/prefetch/cycle slow-warn ms thresholds (200, 5000, 8000, 2000, 5000, 2000, 30000)
8. **strategy_worker.py:1067/1099/1105/1123/1135:** RR direction-flip and RR-asymmetry block/warn thresholds (0.5, 1.0, 2.0, 5.0, 3.0)
9. **scanner_worker.py:147-152:** regime-alignment factors (1.0, 0.5, 0.0, -1.0)
10. **scanner_worker.py:200:** funding normalize divisor (0.001)
11. **scanner.py (MarketScanner):18:** `CACHE_TTL_SECONDS = 300`
12. **scanner.py:188:** registry-purge cooldown 3600s
13. **scanner.py:424/426/433:** legacy hardcoded volume/price/spread rejects (5_000_000, 0.0001, 0.5%) — note divergence from config `[scanner].max_spread_pct = 0.15`
14. **scanner.py:449-501:** five tiered scoring buckets (change_abs / daily_range_pct / trend_ratio / vol / spread_pct), 25 hardcoded values
15. **regime.py:122/126:** `choppiness < 45` counter-thresholds
16. **regime.py:130:** `volume_ratio > 2.0` volatile multiplier
17. **regime.py:138:** `atr_percentile < 50` dead counter-threshold
18. **scorer.py:55-218:** 30+ hardcoded score-bucket / sentiment / F&G / funding / volume / position-distance thresholds
19. **ensemble.py:99/101/105:** consensus-tier size multipliers (1.0/0.75/0.5/0.3/0.15) and STRONG/LEAN agreement counts (4.0, 1.5)
20. **setup_scanner.py:18/19/124/243/245:** `MAX_SETUPS = 12`, `MIN_QUALIFYING_CRITERIA = 3`, `mtf.score >= 5`, `rr >= 4.0/3.0`
21. **fair_value_gap.py:25/27/211:** FVG strength buckets and partial-mitigation threshold
22. **order_blocks.py:121/148/150/174:** index proximity, body-ratio buckets, strength+retests gates
23. **structural_levels.py:217-243:** RR-tier and position-in-range buckets (9 values)
24. **liquidity.py:28/30/37/39:** sweep-strength tiers and validity gates
25. **market_structure.py:197/199:** dominant-swing-count buckets
26. **freshness_guard.py:16/17:** MAX_TICKER_AGE=120, MAX_KLINE_AGE=300 (latter duplicates `[coin_package_validator].staleness_fail_seconds`)
27. **fibonacci.py:27/30:** `CONFLUENCE_TOLERANCE_PCT = 0.5`, `MIN_SWING_PCT = 2.0`

## Already configurable (cited for completeness)

- `scanner_worker.py:285/289/591/778/779/839/840/842`: all read from `settings.scanner.qualitative` and `settings.coin_package_validator`.
- `regime.py:122/126/134`: read from `settings.regime`.
- `strategy_worker.py`: ensemble agreement/opposition from `settings.strategy_engine`.
- `structure_worker.py:127/354/363`: `settings.structure.min_candles`.

## Notes / divergences

- `[scanner].max_spread_pct = 0.15` (config) vs `scanner.py:433: spread_pct > 0.5` (hardcoded). Different number used in different paths.
- `[scanner].min_volume_24h = 5000000` (config) vs `scanner.py:424: vol < 5_000_000` (hardcoded). Same number in both, but two sources of truth.
- `[analysis.structure].min_rr_ratio = 2.0` (config) vs `setup_scanner.py:113: sp.rr_ratio >= 2.0` AND `structural_levels.py:219: elif rr >= 2.0` (both hardcoded). Three sources of truth for the same threshold.
- `[coin_package_validator].staleness_fail_seconds = 300.0` (config) vs `freshness_guard.MAX_KLINE_AGE = 300` (hardcoded). Two sources of truth.
