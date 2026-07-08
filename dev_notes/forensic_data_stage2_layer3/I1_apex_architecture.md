# I1 — APEX Architecture

Forensic refresh: 2026-05-02 (replaces 2026-04-28 baseline).
Project root: `/home/inshadaliqbal786/trading-intelligence-mcp`.

## 1. `src/apex/` directory contents

Top-level listing (`ls -la /home/inshadaliqbal786/trading-intelligence-mcp/src/apex/`):

| File | LOC | One-line description |
|------|-----|----------------------|
| `__init__.py` | 1 | Package docstring only — `"""APEX -- Aggressive Profit Extraction & Exploitation."""` (`__init__.py:1`). |
| `assembler.py` | 769 | `IntelligenceAssembler` builds the 5-section `IntelligencePackage` (Claude directive, coin data, TIAS symbol history, TIAS situation, X-RAY structural) consumed by the optimizer (`assembler.py:32`). |
| `gate.py` | 474 | `TradeGate` runs the 14 hard-safety checks between optimizer and Shadow execution (`gate.py:29`). |
| `models.py` | 435 | Dataclasses: `DirectiveContext`, `CoinData`, `TIASSymbolHistory`, `TIASSituationData`, `StructuralData`, `IntelligencePackage`, `OptimizedTrade` (`models.py:18,47,177,214,245,374,394`). |
| `optimizer.py` | 743 | `TradeOptimizer.optimize()` orchestrator: assemble → tier check → direction-lock → DeepSeek call → parse → constraints → flip-discipline → log (`optimizer.py:36,61`). |
| `prompts.py` | 226 | `APEX_SYSTEM_PROMPT` constant + `build_apex_user_prompt(package)` builder (`prompts.py:21,82`). |
| `qwen_client.py` | 248 | `QwenClient` — async OpenRouter HTTP client (despite the name, the model called is DeepSeek per `_DS_COST_PER_M_INPUT/OUTPUT` constants and config) (`qwen_client.py:47`). |

## 2. Main entry point

`TradeOptimizer.optimize(directive, plan)` defined at `src/apex/optimizer.py:61` is the public entry point.

It is invoked from `src/core/layer_manager.py:1258` inside an `asyncio.gather` over all `plan.new_trades` (parallel optimization, see `layer_manager.py:1254-1271`).

The class is constructed and registered on the service container at `src/workers/manager.py:1829`:
```
apex_optimizer = TradeOptimizer(qwen_client, apex_assembler, apex_cfg)
self._services["apex_optimizer"] = apex_optimizer
```
(`workers/manager.py:1816-1840`)

`apex_gate` (TradeGate) is constructed at `workers/manager.py:1834` and registered as `self._services["apex_gate"]`.

## 3. APEX role per `dev_notes/APEX_COMPLETE_INTEGRATION_PROMPT.md`

NOT FOUND — searched: `find /home/inshadaliqbal786/trading-intelligence-mcp -maxdepth 4 -type f -iname "*apex*integration*"` returned only test files. The file `dev_notes/APEX_COMPLETE_INTEGRATION_PROMPT.md` does not exist.

Closest available source for APEX role description: `dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md:567-575` ("Stage 3.1 — APEX (post-Claude trade-parameter optimizer)"), which states "Takes Claude's directive and runs DeepSeek (via OpenRouter) to optimize SL/TP/size/leverage/direction."

### Philosophy "never reject, never skip — only optimize parameters"

Phrase NOT FOUND verbatim — searched: `grep -rn "never reject" src/apex/ dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md`, `grep -rn "never skip" ...`, `grep -rn "only optimize" ...`. All returned no matches.

Equivalent text found in code:
- `src/apex/prompts.py:25-26` (system prompt to DeepSeek):
  ```
  YOU DO NOT REJECT TRADES. YOU DO NOT SAY "SKIP."
  Every directive you receive WILL be traded. Your job is to make it the most profitable version possible.
  ```
- `src/apex/optimizer.py:4-5` (module docstring):
  ```
  Takes Claude's trade directive and returns DeepSeek-optimized parameters for
  maximum profit extraction. If DeepSeek fails for ANY reason, returns Claude's
  original parameters unchanged. APEX failure NEVER blocks a trade.
  ```
- `src/apex/optimizer.py:38-41` (class docstring):
  ```
  If DeepSeek fails for any reason (timeout, bad JSON, API error), the optimizer
  returns an OptimizedTrade with is_fallback=True that preserves Claude's
  original parameters. APEX failure never blocks trade execution.
  ```
- `src/apex/gate.py:2-4`:
  ```
  The gate NEVER blocks a trade. It adjusts parameters within safe bounds.
  ```

### Where in pipeline

Per `dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md:131`: "Layer 3 (EXECUTION) APEX → TradeGate → OrderService". Concrete chain:

1. Claude Brain emits `plan.new_trades` (list of directive dicts).
2. `core/layer_manager.py:1248-1253` stamps `_claude_original_size_usd` on every directive (Phase 5 ceiling reference).
3. `core/layer_manager.py:1254-1271` — `apex.optimize` is invoked for every directive in parallel via `asyncio.gather(return_exceptions=True)`.
4. For each trade, `core/layer_manager.py:1314-1316` calls `_apply_apex_optimization(trade, optimized_results[i])` which converts pct→price (`layer_manager.py:1382-1466`).
5. `core/layer_manager.py:1318-1323` calls `gate.validate(trade)` (TradeGate, never blocks).
6. `core/layer_manager.py:1326-1328` calls `strategy_worker._execute_claude_trade(trade, ...)` which routes to `OrderService.place_order`.

## 4. APEX_FLIP behavior

### Where direction flips

Direction flip is computed by DeepSeek inside the LLM response and detected at `src/apex/optimizer.py:387,446`:
```
qwen_dir = analysis.get("direction", original_dir)
...
was_flipped=(qwen_dir != original_dir),
```

The flip log line is emitted at `src/apex/optimizer.py:599-608` (`APEX_FLIP | sym=... claude=... apex=...`).

### Trigger inputs

Inputs to DeepSeek that influence flip choice (see `src/apex/prompts.py:40-47`):
- TIAS direction breakdown for symbol in current regime (`prompts.py:131-141`).
- TIAS situation data (regime + F&G, all-coin direction-bias) (`prompts.py:184-194`).
- Current regime, fed in via `package.situation_data.regime` from `assembler._get_market_conditions` (`assembler.py:575-643`).

### Confidence threshold for flip

Pre-call code-level direction lock (`src/apex/optimizer.py:665-711`):
- `trending_up` / `trending_down`: always lock (`optimizer.py:691-699`).
- `volatile`: lock unless `_check_flip_evidence` shows ≥70% WR over ≥8 opposite-direction trades (`optimizer.py:702-707`, `_check_flip_evidence` at `optimizer.py:650-663`).
- `ranging` / `dead` / `unknown`: NOT locked pre-call (`optimizer.py:709-711`).

Post-parse confidence-gated flip discipline (`src/apex/optimizer.py:713-743`):
- Threshold: `apex_min_flip_confidence`, default `0.90` (`config/settings.py:1445`, `config.toml:997`).
- If regime in {ranging, dead, unknown} AND DeepSeek flipped AND confidence < 0.90, flip is reverted with `APEX_FLIP_BLOCKED` log (`optimizer.py:266-275`).
- Suspenders: if direction was code-locked AND DeepSeek still flipped, `APEX_DIR_LOCK_OVERRIDE` reverts (`optimizer.py:240-251`).

### Count of APEX_FLIP events in last 24h

Time window: 2026-05-01 11:48:00 → 2026-05-02 11:49:30 UTC (logs scanned: `workers.log`, `workers.2026-05-02_04-31-00_392071.log`, `workers.2026-05-01_00-01-33_829054.log`):

| Tag | Count |
|-----|------:|
| `APEX_FLIP` (allowed flips) | 6 |
| `APEX_FLIP_BLOCKED` (reverted by confidence gate) | 2 |
| `APEX_FLIP_RESIZE_BLOCKED` (size forced back) | 6 |
| `APEX_DIR_LOCK` (lock pre-call) | 19 |
| `APEX_DIR_LOCK_OVERRIDE` | 0 |
| `APEX_OK` (no flip) | 51 |
| `APEX_TIER` (total optimizations) | 57 |

Allowed-flip rate = 6 / 57 = 10.5%. Two confidence-gated reverts in the same window prove the discipline is firing.

## 5. `apex_optimized` flag — distribution in DB snapshot

Snapshot: `/tmp/trading_snapshot_1777722335.db` (size 145 MB, mtime 2026-05-02 11:45 UTC).

Total `trade_intelligence` rows: 821 (range `trade_closed_at` 2026-04-06 → 2026-05-02 06:29 UTC).

| `apex_optimized` value | Row count |
|------------------------|----------:|
| 0 | 227 |
| 1 | 594 |

Last-24-hour slice (`trade_closed_at > datetime('now','-24 hours')`):

| `apex_optimized` | `apex_flipped` | Rows |
|------------------|----------------|-----:|
| 1 | 0 | 31 |
| 1 | 1 | 3 |
| 0 | * | 0 |

So the prior memory note ("apex_optimized was 0 for all trades") is no longer true as of this snapshot — 594 of 821 historical rows and all 34 last-24-hour rows have the flag set.

### Where the flag is set

There are two distinct write sites:

1. **In-memory directive flag (`_apex_optimized`)** — set on the directive dict during execution by `src/core/layer_manager.py`:
   - Line 1429: `modified["_apex_optimized"] = True` (partial-apply path when current price unavailable).
   - Line 1453: `modified["_apex_optimized"] = True` (full-apply path).
   - Read by gate at `src/apex/gate.py:201`: `if trade.get("_apex_optimized"):` to gate Checks 8-12.

2. **Persisted DB column (`apex_optimized` in `trade_intelligence`)** — written by TIAS:
   - Schema: `src/database/migrations.py:1221` (`ADD COLUMN apex_optimized INTEGER DEFAULT 0`) + index `:1238`.
   - Set in TIAS collector: `src/tias/collector.py:497-498` (defaults), `:517-519` (override when APEX record present): `result["apex_optimized"] = True; result["apex_flipped"] = bool(record.get("apex_was_flipped", False))`.
   - Repository writes the flag: `src/tias/repository.py:37` (`data["apex_optimized"] = 1 if data.get("apex_optimized") else 0`).
   - The TIAS model dataclass: `src/tias/models.py:87-88` (`apex_optimized: bool = False`, `apex_flipped: bool = False`).

### `apex_optimized` in other tables

Searched via `sqlite3 PRAGMA table_info(orders)` and `PRAGMA table_info(trade_history)`:
- `orders`: NO `apex_optimized` column (schema confirmed — only `order_id, symbol, side, order_type, price, qty, status, filled_qty, avg_fill_price, stop_loss, take_profit, created_at, updated_at`).
- `trade_history`: NO `apex_optimized` column.
- `trade_intelligence` is the sole persisted carrier of `apex_optimized` / `apex_flipped`.
- `trade_thesis` has `apex_flipped` (`migrations.py:1242`) populated from `core/thesis_manager.py:37,52,59,81`.

## 6. APEX-related columns on `trade_intelligence`

From `PRAGMA table_info(trade_intelligence)` (snapshot):

| Col # | Name | Type |
|------:|------|------|
| 75 | `apex_optimized` | INTEGER DEFAULT 0 |
| 76 | `apex_flipped` | INTEGER DEFAULT 0 |
| 77 | `apex_original_direction` | TEXT |
| 78 | `apex_final_direction` | TEXT |
| 79 | `apex_original_sl` | REAL |
| 80 | `apex_final_sl` | REAL |
| 81 | `apex_original_tp` | REAL |
| 82 | `apex_final_tp` | REAL |
| 83 | `apex_original_size` | REAL |
| 84 | `apex_final_size` | REAL |
| 85 | `apex_confidence` | REAL |
| 86 | `apex_tp_mode` | TEXT |
| 87 | `apex_reasoning` | TEXT |
| 88 | `apex_model` | TEXT |
| 89 | `apex_response_ms` | INTEGER |
| 90 | `apex_cost_usd` | REAL |
| 92 | `apex_tp_fill_rate` | REAL |

`gate_adjustments` (col 91) is the persisted GATE adjustment string, written by `core/layer_manager.py:_apply_apex_optimization` chain via TIAS collector.
