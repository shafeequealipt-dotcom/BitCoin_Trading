# I4 — APEX TradeGate

Forensic refresh: 2026-05-02 (replaces 2026-04-28 baseline).

## 1. File path & class

`src/apex/gate.py:29` — `class TradeGate`. File length: 474 lines (`wc -l`).

Constructor: `TradeGate.__init__(self, services: dict, settings: Any)` (`gate.py:41-46`):
```python
self._services = services
self._settings = settings
self._conviction_cache: dict[str, tuple[float, float]] = {}
self._conviction_cache_ttl: float = 300.0  # 5 minutes
```

Wired in `src/workers/manager.py:1833-1835`:
```python
from src.apex.gate import TradeGate
apex_gate = TradeGate(self._services, apex_cfg)
self._services["apex_gate"] = apex_gate
```

## 2. Public methods

| Method | Signature | File:line | Purpose |
|--------|-----------|-----------|---------|
| `__init__` | `(services, settings)` | `:41` | Stores services dict and settings; initializes 5-minute conviction cache. |
| `validate` | `async (trade: dict) -> dict` | `:48` | Runs the 14 hard-safety checks. NEVER blocks; mutates `trade` in place and returns it. |

Private helper:

| Method | File:line |
|--------|-----------|
| `_get_conviction_weight(symbol)` | `:356` |

## 3. `_get_conviction_weight` — regime_detector wiring

VERIFIED: `_get_conviction_weight` calls `regime_detector.get_coin_regime(symbol)` at `src/apex/gate.py:370`. Verbatim block (`:367-396`):

```python
detector = self._services.get("regime_detector")
if detector:
    coin_regime = detector.get_coin_regime(symbol)
    # Definitive-fix Phase 7 (2026-04-28) — emit per-call
    # cache-query telemetry so REGIME_FALLBACK frequency can
    # be correlated with the cold-start window. ``hit`` is
    # True only when the per-coin cache was populated for
    # this symbol; ``cache_size`` shows whether the cache
    # is even warm yet.
    _hit = coin_regime is not None
    _cache_size = (
        len(getattr(detector, "_per_coin_regimes", {}) or {})
    )
    _ready = bool(getattr(detector, "is_ready", lambda: True)())
    log.info(
        f"REGIME_CACHE_QUERY | sym={symbol} reader=apex_gate "
        f"hit={_hit} ready={_ready} cache_size={_cache_size} | {ctx()}"
    )
    if coin_regime is not None:
        _regime = str(coin_regime.regime.value)
    elif hasattr(detector, "_last_regime") and detector._last_regime:
        _regime = str(detector._last_regime.regime.value)
        log.warning(
            "REGIME_FALLBACK | sym={sym} source=gate | "
            "per-coin unavailable, using global={r} | {ctx}",
            sym=symbol, r=_regime, ctx=ctx(),
        )
```

### What conviction weight is for

Conviction weight scales the per-trade capital allocation in `validate` Check 4 (`gate.py:123-160`). It's a multiplier (0.5x – 2.0x) applied to a 40% base capital fraction (`base_pct = 0.4`).

`_get_conviction_weight` (`gate.py:356-474`) computes a profit-factor-based weight from TIAS history:

| Profit factor | Weight |
|---------------|-------:|
| > 3.0 | `2.0` |
| > 2.0 | `1.5` |
| > 1.0 | `1.0` |
| > 0.5 | `0.7` |
| ≤ 0.5 | `0.5` |
| `total_lost == 0` | capped at PF=10.0 then mapped to 2.0 |
| trades < `conviction_min_trades` (3) | default `0.75` |

Implementation steps:
1. Resolve regime via `get_coin_regime` (`gate.py:367-396`).
2. Query `tias_repo.get_symbol_full_history(symbol, limit=20, regime=_regime)` (`gate.py:413-415`).
3. If regime-filtered total < `conviction_min_trades`, fall back to all-regime query (`gate.py:419-421`).
4. Compute profit factor from `pnl_usd` aggregates (`gate.py:434-449`).
5. Map to weight (`gate.py:452-461`).
6. Cache result for 5 minutes keyed on `f"{symbol}:{_regime or 'all'}"` (`gate.py:399-404, 425, 463`).
7. Emit `CONVICTION_WEIGHT | sym=... pf=... weight=...x` at INFO (`gate.py:464-469`).

After computing weight, Check 4 (`gate.py:138-147`) ALSO multiplies a Layer 2 score modifier:
- `signal_score >= 80`: weight × 1.20 (A+).
- `>= 68`: no change (A).
- `>= 56`: weight × 0.90 (B).
- `> 0`: weight × 0.80 (C/D).

Combined `weighted_pct = base_pct (0.40) × weight`, clamped to `[0.05, 0.40]` (`gate.py:148-150`). The trade size is then capped at `available × weighted_pct`.

## 4. Decision logic — allow / block / modify

The gate NEVER blocks. From `gate.py:7-16`:
> Runs 12 checks. Each check MAY adjust parameters but NEVER blocks. Modifications are logged at INFO level and attached to the trade dict as `_gate_adjustments` for TIAS feedback.

(Class docstring says 12; current code has 14 — see truth doc `:587-606`.)

### The 14 checks (verbatim labels, file:line)

| # | Check | File:line | Action |
|---|-------|-----------|--------|
| 0 | Claude directive size cap (Phase 5) — final size ≤ `claude_original × gate_apex_size_cap_mult` (1.5×) | `:65-92` | Modify (clamp size). Logs `CONVICTION_SIZE_CAP`. |
| 1 | Maximum position size (`max_position_size_usd`, default 1200) | `:94-99` | Modify (clamp). |
| 2 | Maximum leverage (`max_leverage`, default 5) | `:101-106` | Modify (clamp). |
| 3 | Maximum concurrent positions (5) — if at max, scale size to 30% | `:108-121` | Modify. |
| 4 | Capital availability (conviction-weighted) — `weight × signal_score modifier × 40% base` | `:123-160` | Modify (size cap). |
| 5 | Duplicate-symbol position → halve size | `:162-172` | Modify. |
| 6 | Recent cooldown (`coordinator.is_symbol_cooled_down`) → halve size | `:174-186` | Modify. |
| 7 | Minimum position size floor ($50) | `:188-193` | Modify. |
| 8 | TP floor — APEX TP cannot cross Claude's TP direction | `:221-237` | Modify (revert TP). Logs `APEX_GUARDRAIL_TP_FLOOR`. |
| 9 | Trail activation floor (`gate_trail_activation_floor_pct_of_tp`, default 50% of TP distance) | `:239-256` | Modify trail param. |
| 10 | Trail distance floor (`gate_trail_distance_floor_pct`, default 40%) | `:257-268` | Modify trail param. |
| 11 | Mode override — `trail_only` → `trail_with_ceiling` (uses Claude TP as ceiling) | `:270-277` | Modify mode. |
| 12 | Confidence-based size scaling (`gate_confidence_floor`, default 0.50) | `:279-291` | Modify size when confidence < floor. Logs `APEX_CONF_SIZE`. |
| 13 | R:R ratio sanity (rr=0 → ×0.25; 0<rr<0.5 → ×0.5) | `:295-311` | Modify size. |
| 14 | TP/SL sanity — if differ <0.1%, nudge TP by ±2% | `:313-327` | Modify TP. Logs `TPSL_IDENTICAL`. |

### Inputs (services consumed)

| Service / key | Used by | File:line |
|---------------|---------|-----------|
| `position_service` | Check 3 (`get_positions`), Check 5 (`get_position`) | `:111, 165` |
| `fund_manager` | Check 4 (read `_account_state.available`) | `:125-130` |
| `regime_detector` | `_get_conviction_weight` (regime classification) | `:368-396` |
| `tias_repo` | `_get_conviction_weight` (`get_symbol_full_history`) | `:408-421` |
| `trade_coordinator` | Check 6 (`is_symbol_cooled_down`, `get_symbol_cooldown_remaining`) | `:176-184` |
| `market_service` | Check 9 (`get_ticker` for entry estimate) | `:213-216` |
| `structure_cache` | Check 13 (`get(symbol).structural_placement.rr_ratio`) | `:297-300` |

Inputs (settings on `APEXSettings`, `config/settings.py:1389-1446`):
- `max_position_size_usd` (1200), `max_leverage` (5).
- `gate_apex_size_cap_mult` (1.5), `gate_tp_floor_enabled` (True), `gate_trail_activation_floor_pct_of_tp` (15.0; runtime default 50.0 — see code path `:241`), `gate_trail_distance_floor_pct` (40.0), `gate_mode_override_enabled` (True), `gate_confidence_floor` (0.50).
- `conviction_enabled` (True), `conviction_min_trades` (3).

Inputs (trade dict):
- `_apex_optimized` flag (set by `core/layer_manager.py:1429,1453`) gates Checks 8-12 at `gate.py:201`.
- `_apex_tp_mode`, `_apex_confidence`, `_apex_original_tp`, `_apex_original_sl`, `_apex_original_size`, `_claude_original_size_usd`.

### Outputs

- `trade["size_usd"]`, `trade["leverage"]`, `trade["take_profit_price"]`, `trade["stop_loss_price"]` mutated in place.
- `trade["_gate_adjustments"]` — comma-joined modification labels (`gate.py:333`). Persisted to `trade_intelligence.gate_adjustments` (col 91).
- `trade["_gate_validation_ms"]` — total validate elapsed ms (`gate.py:331`).
- `trade["_apex_trail_activation_pct"]`, `trade["_apex_trail_distance_pct"]` — set by Checks 9-10 for downstream `TradePlan`.

Logs:
- `GATE_ADJUST | sym=... changes=[...]` at INFO when modifications applied (`:334-337`).
- `GATE_PASS | sym=... no_changes` at DEBUG when nothing changed (`:339`).
- `GATE_TIMING | sym=... el=...ms modifications=N` at INFO (`:342-345`); `GATE_TIMING_SLOW` at WARNING when `el > 500ms` (`:346-350`).

## 5. APEX → Enforcer handoff

There is NO direct call from `TradeGate` to `PerformanceEnforcer`. The handoff is mediated by the brain orchestrator and strategy worker.

Order of operations in `core/layer_manager.py:_execute_new_trades` (`:1184-1380`):

1. APEX optimize fan-out (`layer_manager.py:1254-1271`).
2. `_apply_apex_optimization` per trade (`layer_manager.py:1316`).
3. **TradeGate validate** (`layer_manager.py:1318-1323`):
   ```python
   gate = self.services.get("apex_gate")
   if gate:
       _t0 = time.time()
       trade = await gate.validate(trade)
       _gate_ms = (time.time() - _t0) * 1000
   ```
4. `strategy_worker._execute_claude_trade(trade, position_symbols, plan)` (`layer_manager.py:1326-1328`).

The `PerformanceEnforcer` (`src/strategies/performance_enforcer.py:31`) is queried INSIDE `_execute_claude_trade` to apply the size multiplier at `get_size_multiplier(:126-149)` before `OrderService.place_order` is called.

So the chain post-APEX is:
**APEX optimizer → `_apply_apex_optimization` → TradeGate.validate → StrategyWorker._execute_claude_trade → PerformanceEnforcer (size multiplier inside execute) → OrderService.place_order.**

The exact handoff point from APEX gate to the next stage is `core/layer_manager.py:1326`:
```python
success, _reason_code = await strategy_worker._execute_claude_trade(
    trade, position_symbols, plan,
)
```

## 6. Live evidence (24h window, 2026-05-01 11:48 → 2026-05-02 11:49 UTC)

| Tag | Count |
|-----|------:|
| `GATE_ADJUST` (modifications applied) | many; sample below |
| `CONVICTION_SIZE_CAP` (Check 0 binding) | observed (e.g. AEROUSDT $500→$750 at 1.5×) |
| `APEX_GUARDRAIL_TP_FLOOR` (Check 8) | 23 |
| `APEX_CONF_SIZE` (Check 12) | 1 |
| `CONVICTION_WEIGHT` | observed every TIAS-resolved gate call |

Representative `GATE_ADJUST` lines:
- `2026-05-02 04:41:02.656 GATE_ADJUST | sym=INJUSDT changes=[conviction_cap=$247(w=0.5x)]`
- `2026-05-02 05:18:07.214 CONVICTION_SIZE_CAP | sym=AEROUSDT claude=$500 requested=$800 capped=$750 mult=1.5x`
- `2026-05-02 06:02:33.034 GATE_ADJUST | sym=MANAUSDT changes=[conviction_cap=$246(w=0.5x), APEX_GUARDRAIL_TP_FLOOR(apex=0.09->claude=0.09), APEX_CONF_SIZE(30%<50%,size_scale=60%)]`
- `2026-05-02 03:50:43.207 GATE_ADJUST | sym=ONDOUSDT changes=[conviction_cap=$370(w=0.8x), APEX_GUARDRAIL_TP_FLOOR(apex=0.27->claude=0.27)]`

The combined log (`MANAUSDT` above) shows three independent checks — Check 4 (conviction), Check 8 (TP floor), Check 12 (confidence size scaling) — firing on the same trade in a single `validate()` call, demonstrating that the gate runs the full 14-step chain regardless of which earlier check has already adjusted the trade.
