# I2 — APEX IntelligenceAssembler

Forensic refresh: 2026-05-02 (replaces 2026-04-28 baseline).

## 1. File path & class

`src/apex/assembler.py:32` — `class IntelligenceAssembler`.

Constructor: `IntelligenceAssembler.__init__(self, services: dict, tias_repo: Any, db: Any = None)` (`assembler.py:50`). Wired in `src/workers/manager.py:1828`:
```
apex_assembler = IntelligenceAssembler(self._services, tias_repo, db)
```

## 2. All public methods

Module exposes one class with the following methods (verbatim signatures with file:line):

| Method | File:line | Purpose |
|--------|-----------|---------|
| `assemble(self, directive: dict) -> IntelligencePackage` | `assembler.py:55` | Build the complete 5-section intelligence package for one coin. |

All other methods (`_build_directive_context`, `_gather_coin_data`, `_populate_ta`, `_populate_mode4`, `_populate_orderbook`, `_populate_volatility_profile`, `_gather_symbol_history`, `_gather_situation_data`, `_get_market_conditions`, `_gather_structural_data`) are private helpers — names start with `_`. They are listed below for traceability.

| Private method | File:line |
|----------------|-----------|
| `_build_directive_context(directive)` | `assembler.py:99` |
| `_gather_coin_data(symbol)` | `assembler.py:118` |
| `_populate_ta(data, symbol)` | `assembler.py:202` |
| `_populate_mode4(data, symbol)` | `assembler.py:279` |
| `_populate_orderbook(data, symbol)` | `assembler.py:324` |
| `_populate_volatility_profile(data, symbol)` | `assembler.py:365` |
| `_gather_symbol_history(symbol, regime)` | `assembler.py:394` |
| `_gather_situation_data(regime, fear_greed)` | `assembler.py:514` |
| `_get_market_conditions(symbol)` | `assembler.py:575` |
| `_gather_structural_data(symbol)` | `assembler.py:649` |

Module-level helpers: `_last_valid_arr(arr)` (`assembler.py:670`), `_gather_structural_data_from_cache(services, symbol)` (`assembler.py:686`).

## 3. `_get_market_conditions` — regime_detector wiring

Defined at `src/apex/assembler.py:575`. The relevant block (`assembler.py:585-617`):

```python
detector = self._services.get("regime_detector")
if detector:
    coin_regime = detector.get_coin_regime(symbol)
    # Definitive-fix Phase 7 (2026-04-28) — REGIME_CACHE_QUERY
    # telemetry mirrors apex/gate.py.
    _hit = coin_regime is not None
    _cache_size = (
        len(getattr(detector, "_per_coin_regimes", {}) or {})
    )
    _ready = bool(getattr(detector, "is_ready", lambda: True)())
    log.info(
        f"REGIME_CACHE_QUERY | sym={symbol} reader=apex_assembler "
        f"hit={_hit} ready={_ready} cache_size={_cache_size} | {ctx()}"
    )
    if coin_regime is not None:
        # RegimeState.regime is a MarketRegime enum, .value is the string
        regime = str(coin_regime.regime.value)
    elif hasattr(detector, "_last_regime") and detector._last_regime:
        lr = detector._last_regime
        regime = str(lr.regime.value)
        log.warning(
            "REGIME_FALLBACK | sym={sym} source=assembler | "
            "per-coin unavailable, using global={r} | {ctx}",
            sym=symbol, r=regime, ctx=ctx(),
        )
```

VERIFIED: `regime_detector.get_coin_regime(symbol)` is called at `assembler.py:588`. Telemetry tag `REGIME_CACHE_QUERY` with `reader=apex_assembler` is emitted at `:596-599`.

### Fallback when unavailable

Two-tier fallback (`assembler.py:600-617`):
1. If `coin_regime is None` AND `detector._last_regime` exists: use the global regime, emit `REGIME_FALLBACK | source=assembler` at WARNING (`:606-610`).
2. If detector itself is missing or both per-coin and global lookups fail: silently keep the default `regime = "unknown"` initialized at `:581`. Exception path (`:611-617`) catches any failure and emits `APEX_REGIME_FAIL` at DEBUG.

Default values when nothing is available: `regime = "unknown"`, `fear_greed = 50` (`:581-582`).

The Fear & Greed value is sourced separately via direct DB query at `assembler.py:619-642`:
```sql
SELECT value FROM fear_greed_index
WHERE timestamp > datetime('now', '-24 hours')
ORDER BY timestamp DESC LIMIT 1
```
Falls back to 50 (neutral) with `FG_STALE` warning when no row in 24h.

## 4. All APEX inputs (caches / DB tables / services)

### Services consumed (via `self._services.get(...)`)

| Service key | First read | Use |
|-------------|------------|-----|
| `regime_detector` | `assembler.py:586` | Per-coin regime; fallback to global `_last_regime`. |
| `ta_cache` (or `ta`) | `assembler.py:204` | TA indicators (RSI, MACD, ADX, Bollinger, Stochastic, ATR, EMA20/50, volume ratio) on M5 timeframe with `limit=100`. |
| `price_worker` | `assembler.py:144` | WS quote cache (`get_ws_quote(symbol, max_age_s=5.0)`); first price fallback. |
| `market_service` (or `market`) | `assembler.py:162,332` | REST `get_ticker(symbol)` (final price fallback) and `get_orderbook(symbol, depth=10)` (top-5 levels). |
| `volatility_profiler` | `assembler.py:368` | `get_profile(symbol)` returning `volatility_class`, `recommended_tp_pct`, `recommended_sl_pct`, `recommended_hold_min`, `recommended_strategy`. |
| `structure_cache` | `assembler.py:693` (helper) | Synchronous `get(symbol)` returning `StructuralAnalysis` (X-RAY) — feeds Section 5. |

### DB tables read

| Table | Query | File:line |
|-------|-------|-----------|
| `sniper_log` | `SELECT composite_score, hurst_value, momentum_decay_score, extension_score, ev_ratio, volume_div_score FROM sniper_log WHERE symbol=? ORDER BY id DESC LIMIT 1` | `assembler.py:289-298` |
| `fear_greed_index` | `SELECT value FROM fear_greed_index WHERE timestamp > datetime('now','-24 hours') ORDER BY timestamp DESC LIMIT 1` | `assembler.py:622-625` |

### TIAS repository calls

| Method | File:line |
|--------|-----------|
| `tias_repo.get_symbol_full_history(symbol, limit=15, regime=_regime_filter)` | `assembler.py:402` |
| `tias_repo.get_symbol_full_history(symbol, limit=15)` (all-regime fallback when sparse) | `assembler.py:409` |
| `tias_repo.get_situation_stats(regime, fear_greed)` | `assembler.py:519` |

### In-memory caches consumed

- `regime_detector._per_coin_regimes` is read indirectly through `get_coin_regime(symbol)`; cache size also probed at `assembler.py:592-594` (`getattr(detector, "_per_coin_regimes", {})`).
- `regime_detector._last_regime` (global) used as fallback at `assembler.py:603-604`.
- `structure_cache.get(symbol)` (X-RAY) — synchronous in-memory lookup at `assembler.py:696`.
- `_signal_cache` and `StructureCache` (raw class names) — `_signal_cache` is NOT directly referenced in `assembler.py`; only `structure_cache` is consumed. NOT FOUND in this file — searched: `grep -n "_signal_cache\|signal_cache" src/apex/assembler.py`.

## 5. Output structure produced

`IntelligencePackage` dataclass (`src/apex/models.py:374-388`):

```python
@dataclass
class IntelligencePackage:
    directive: DirectiveContext         # Section 1: Claude's trade decision
    coin_data: CoinData                 # Section 2: current coin state
    symbol_history: TIASSymbolHistory   # Section 3: TIAS history for this coin
    situation_data: TIASSituationData   # Section 4: TIAS situation context
    structural_data: Optional[StructuralData] = None  # Section 5: X-RAY structural
```

Each section dataclass and its fields (file:line in `models.py`):

- **Section 1 — `DirectiveContext`** (`models.py:18-40`): `symbol, direction, sl, tp, leverage, size_usd, reasoning, plan_view, signal_score, strategy_name`.
- **Section 2 — `CoinData`** (`models.py:47-170`): `symbol, current_price, change_24h, rsi, macd_line, macd_signal, macd_hist, adx, bollinger_pct, stochastic_k, stochastic_d, ema_20, ema_50, ema_trend, atr, atr_pct, volume_ratio, m4_hurst, m4_momentum, m4_extension, m4_volume_div, m4_ev, m4_composite, m4_trail_sl, bid_depth, ask_depth, book_imbalance_pct, volatility_class, recommended_tp_pct, recommended_sl_pct, recommended_hold_min, recommended_strategy`. Has `format()` method (`:99`).
- **Section 3 — `TIASSymbolHistory`** (`models.py:177-207`): `symbol, total_trades, wins, losses, win_rate, avg_win_pct, avg_loss_pct, total_pnl_usd, ev_per_trade, profit_factor, avg_win_usd, avg_loss_usd, trades, pattern_summary, regime`.
- **Section 4 — `TIASSituationData`** (`models.py:214-238`): `regime, fear_greed, total_trades_in_condition, buy_win_rate, sell_win_rate, avg_buy_pnl, avg_sell_pnl, direction_bias, tp_performance, common_categories, condition_summary`.
- **Section 5 — `StructuralData`** (`models.py:245-367`): X-RAY structural fields (S/R, market structure, R:R, FVG, OB, sweep, liquidity, POC, fib, MTF, session, setup_rank). Has `format()` method (`:303`).

## 6. How the optimizer consumes `IntelligencePackage`

Consumption sites in `src/apex/optimizer.py`:

1. `optimizer.py:112` — `package = await self._assembler.assemble(translated)` (the call).
2. `optimizer.py:116` — `if package.coin_data.current_price <= 0: ...` (price validation, `APEX_SKIP_NO_PRICE`).
3. `optimizer.py:135-136` — `package.symbol_history.total_trades` and `package.situation_data.total_trades_in_condition` for tier classification.
4. `optimizer.py:158-165` — overwrites `package.symbol_history.pattern_summary` for Tier 2 (regime-fallback) optimization.
5. `optimizer.py:184` — `regime = package.situation_data.regime` used for direction-lock decision.
6. `optimizer.py:186-187` — passes `package` to `_check_direction_lock(package, claude_direction, regime)`.
7. `optimizer.py:194-198` — mutates `package.directive.reasoning` to inject lock instruction for DeepSeek.
8. `optimizer.py:206-216` — reads `package.coin_data.volatility_class` and `recommended_tp_pct` to compute the `APEX_TP_CAP`.
9. `optimizer.py:219` — `user_prompt = build_apex_user_prompt(package)` renders all 5 sections into the LLM user message (`prompts.py:82-226`).
10. `optimizer.py:298` — `optimized = self._apply_constraints(optimized, package.coin_data)` (per-class SL/TP floor from `coin_data.recommended_sl_pct` / `recommended_tp_pct`).
11. `optimizer.py:304-309` — enforces `APEX_TP_CAP` (`optimized.tp_pct > _tp_cap`) using `package.coin_data.recommended_tp_pct`.
12. `optimizer.py:317-320` — `_log_optimization(optimized, directive, regime=package.situation_data.regime, vol_class=getattr(package.coin_data, "volatility_class", None))`.

`_check_direction_lock` (`optimizer.py:665-711`) reads:
- `package.symbol_history.trades` (passed to `_check_flip_evidence`, `optimizer.py:704`).

Everything DeepSeek sees is built from `package` via `build_apex_user_prompt` (`prompts.py:82-226`):
- Section 1 from `package.directive` (`prompts.py:104-118`).
- Section 2 from `package.coin_data.format()` (`prompts.py:115-116`).
- Section 3 from `package.symbol_history` and `package.symbol_history.trades` (`prompts.py:121-179`).
- Section 4 from `package.situation_data` (`prompts.py:184-200`).
- Section 5 from `package.structural_data.format()` (`prompts.py:202-207`).
- Output JSON schema instruction (`prompts.py:210-224`).
