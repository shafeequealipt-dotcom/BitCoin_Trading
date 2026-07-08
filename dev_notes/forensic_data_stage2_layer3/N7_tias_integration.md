# N7 — TIAS Integration Points

**Collected:** 2026-05-02 ~11:47 UTC
**Sources:** src/tias/*, src/workers/manager.py, src/core/trade_coordinator.py,
src/database/migrations.py, snapshot DB.

---

## A. Trigger on trade close — wiring

**File / line:** `src/workers/manager.py:1646–1763` (block headed
`# TIAS — Trade Intelligence Autopsy System (#9)`).

- `manager.py:1655` — `tias_repo = TradeIntelligenceRepo(db)`
- `manager.py:1657` — `tias_collector = TradeContextCollector(self._services, db)`
- `manager.py:1662–1668` — when `tias_cfg.enabled and tias_cfg.api_key`:
  build `DeepSeekClient(api_key=tias_cfg.api_key,
  api_url=tias_cfg.api_url, http_referer=tias_cfg.http_referer,
  x_title=tias_cfg.x_title)`
- `manager.py:1669` — `tias_analyzer = TradeAnalyzer(client=tias_client,
  settings=tias_cfg)`
- `manager.py:1714–1723` — `_tias_async_task(record, m4_snapshot)`:
  Phase 1 (collect+save) → Phase 2 (analyze in background via
  `asyncio.get_event_loop().create_task(...)`)
- `manager.py:1725–1761` — `_tias_close_callback(record)`:
  - SYNC: read `profit_sniper.get_closed_snapshot(sym)` first
    (Phase 3 fix — snapshot preserved before `_profit_states[sym]`
    delete in `profit_sniper._on_position_closed`).
  - Fallback: direct `_profit_states[sym]` read for race case.
  - ASYNC: `loop.create_task(_tias_async_task(record, m4_snapshot))`
- `manager.py:1762` — `coordinator.register_close_callback(_tias_close_callback)`
- `manager.py:1763` — `log.info("TIAS: trade context collector
  registered as close callback #9")`

**Sync vs async:** Trigger is registered as a synchronous
`coordinator.register_close_callback`. The callback grabs the M4
snapshot synchronously and then schedules the actual collect+save+analyze
as a background asyncio task. Phase 2 (DeepSeek analyze) runs as a
nested background task spawned from inside the Phase-1 task — so even
the Phase-1 DB write does not block the close path.

`coordinator.on_trade_closed` (src/core/trade_coordinator.py:405) is
the function that fires the callback registry.

---

## B. TIAS DeepSeek call — endpoint + timeout + retries

**File:** `src/tias/deepseek_client.py`
**Class:** `DeepSeekClient` (alias `OpenRouterClient`)

- `deepseek_client.py:75` — Default endpoint:
  `api_url: str = "https://openrouter.ai/api/v1/chat/completions"`
- `deepseek_client.py:76` — `http_referer: str =
  "https://github.com/trading-intelligence-mcp"`
- `deepseek_client.py:77` — `x_title: str = "TIAS-TradeAnalysis"`
- `deepseek_client.py:103` — Default `temperature: float = 0.3`
- `deepseek_client.py:104` — Default `max_tokens: int = 1500`
- `deepseek_client.py:105` — **Default `timeout_seconds: int = 45`**
- `deepseek_client.py:135` — `timeout = aiohttp.ClientTimeout(total=timeout_seconds)`
- `deepseek_client.py:147–155` — HTTP 429 / 503 raise `TIASAnalysisError(retryable=True)`
- `deepseek_client.py:198–203` — `aiohttp.ServerTimeoutError` → retryable=True
- `deepseek_client.py:204–208` — `aiohttp.ClientError` → retryable=True

**Effective timeout in production:** `[tias].timeout_seconds = 45` in
config.toml line 948 (the dataclass default and the config value match).
**The memory note "was 30s, now 60s" is INCORRECT — current value is 45s.**

**Retry policy:**
- `[tias].max_retries = 1` (config.toml line 949) — only one retry on
  retryable errors.
- Retry is implemented inside `TradeAnalyzer.analyze` (src/tias/analyzer.py),
  which calls `self._client.analyze(...)` once, then on `retryable=True`
  fails over to `fallback_model` (one shot). Non-retryable → no retry.

---

## C. trade_intelligence table schema

`sqlite3 /tmp/trading_snapshot_1777722335.db ".schema trade_intelligence"`
yields the migrations.py source:

`src/database/migrations.py:1113–1183` — CREATE TABLE:

```
trade_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Group A: Trade Outcome (always populated)
    symbol TEXT NOT NULL, direction TEXT NOT NULL, strategy_name TEXT NOT NULL,
    strategy_category TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
    closed_by TEXT NOT NULL, entry_price REAL NOT NULL, exit_price REAL NOT NULL,
    pnl_pct REAL NOT NULL, pnl_usd REAL NOT NULL, win INTEGER NOT NULL,
    hold_seconds REAL NOT NULL,

    -- Group B: Entry Decision Context
    leverage REAL, position_size_usd REAL,
    claude_thesis TEXT, claude_signal TEXT, claude_confidence REAL,
    entry_score REAL, ensemble_votes TEXT,

    -- Group C: Market Conditions at Close
    regime TEXT, fear_greed_value INTEGER, fear_greed_label TEXT,

    -- Group D: Technical Indicators at Close
    rsi REAL, macd_hist REAL, macd_signal REAL, bollinger_pct REAL,
    ema_20 REAL, ema_50 REAL, stochastic_k REAL, stochastic_d REAL,
    adx REAL, atr_value REAL, atr_pct REAL, volume_ratio REAL, price_vs_vwap REAL,

    -- Group E: Mode4 Profit Tracking Data
    m4_peak_pnl_pct REAL, m4_ticks_in_profit INTEGER, m4_ticks_total INTEGER,
    m4_composite_score REAL, m4_hurst_value REAL, m4_momentum_decay REAL,
    m4_extension_score REAL, m4_ev_ratio REAL, m4_volume_div_score REAL,

    -- Group F: DeepSeek Analysis (Phase 2 — NULL until analyzed)
    ds_why TEXT, ds_what_worked TEXT, ds_what_failed TEXT,
    ds_lessons TEXT, ds_category TEXT, ds_confidence REAL,
    ds_analyzed_at TEXT,

    -- Group G: Metadata
    trade_id TEXT, trade_closed_at TEXT NOT NULL, captured_at TEXT NOT NULL
)
```

**Indices:**
- `idx_ti_symbol`, `idx_ti_win`, `idx_ti_ds_why`,
  `idx_ti_trade_closed_at`, `idx_ti_ds_category`

**v18 ALTER TABLE additions** (`migrations.py:1193–1209`):
`ds_correct_direction TEXT, ds_what_should_done TEXT,
ds_how_to_exploit TEXT, ds_optimal_direction TEXT,
ds_optimal_sl_pct REAL, ds_optimal_tp_pct REAL,
ds_optimal_size_usd REAL, ds_optimal_leverage INTEGER,
ds_raw_response TEXT, ds_response_time_ms INTEGER,
ds_input_tokens INTEGER, ds_output_tokens INTEGER, ds_cost_usd REAL,
ds_model TEXT`

(Plus later: `analysis_version`, `analysis_attempts`, `entry_regime`,
`entry_rsi`, `entry_macd_hist`, `entry_atr_pct`, and the apex_*
columns — see snapshot `PRAGMA table_info(trade_intelligence)` in N3
context). 94 columns total.

### Sample 10 most recent rows

```
SELECT id, symbol, direction, source, closed_by, regime, pnl_pct,
       leverage, ds_category, captured_at
FROM trade_intelligence
ORDER BY captured_at DESC LIMIT 10;
```

Result (from snapshot):
```
821 ONDOUSDT     Buy  claude_direct time_decay_p_win_low ranging      -0.104   2.0  REGIME_MISMATCH    2026-05-02T06:29:10
820 MANAUSDT     Buy  claude_direct time_decay_p_win_low dead         -0.052   3.0  REGIME_MISMATCH    2026-05-02T06:13:38
819 AXSUSDT      Buy  claude_direct mode4_p9             trending_up  -0.010   3.0  STOP_TOO_TIGHT     2026-05-02T06:05:17
818 DOGEUSDT     Sell claude_direct strategic_review:..  ranging      -0.134   2.0  (NULL)             2026-05-02T05:58:36
817 AXSUSDT      Buy  claude_direct mode4_p9             trending_up  -0.140   2.0  (NULL)             2026-05-02T05:35:14
816 DOGEUSDT     Sell claude_direct time_decay_p_win_low ranging      -0.049   2.0  (NULL)             2026-05-02T05:35:05
815 RENDERUSDT   Buy  claude_direct strategic_review:..  ranging      -0.009   3.0  (NULL)             2026-05-02T05:06:49
814 SANDUSDT     Sell claude_direct shadow_sl_tp         ranging      -0.129   5.0  (NULL)             2026-05-02T04:54:07
813 AXSUSDT      Buy  claude_direct mode4_p9             trending_up  -0.120   3.0  (NULL)             2026-05-02T04:51:39
812 HYPEUSDT     Buy  claude_direct time_decay_p_win_low ranging      -0.005   3.0  (NULL)             2026-05-02T04:29:30
```

(Total trade_intelligence rows in snapshot: 821. `source=claude_direct`
for all 24h rows. `closed_by` distribution heavily skewed to
`time_decay_p_win_low`, `mode4_p9`, `shadow_sl_tp`, `strategic_review`.)

---

## D. Coaching feedback loop — TIAS output → next prompt

**Wiring file:** `src/brain/strategist.py:565–571` (CALL_A) and
`strategist.py:1550–1557` (alternate path).

```python
# strategist.py:565
if enforcer and hasattr(enforcer, "get_coaching_text"):
    ...
    coaching = enforcer.get_coaching_text(structure_cache=_sc)
    if coaching:
        sections.append(f"## {coaching}")
```

The coaching text is generated by `PerformanceEnforcer.get_coaching_text`
inside `src/strategies/performance_enforcer.py`. It pulls aggregates
from `tias_repo` (recent loss summaries, win-rate-by-regime, per-coin
WR/PF) and emits a markdown block prepended at section position-2 of
the brain prompt.

**Live observation (last 24h):** every `STRAT_PROMPT_BUILD` log line
shows `coaching=0ms` — meaning the coaching block was either empty
(fast path) or cached and free to emit. NOT FOUND — explicit
COACHING_TEXT or COACH_BLOCK log lines (no specific log emitted on
build); inferred only via prompt size.

**Format:** prepended Markdown section starting `## …` (line 569);
visible inside the prompt right after coin briefings. Inserted via
`sections.append(f"## {coaching}")` so it lands as a top-level
section.

---

## E. TIAS Phase-3 data gaps — verification per memory note

The memory note states the following gaps exist. Each verified against
the current code+DB:

### 1. Claude directive text not stored at entry time
**STATUS: PARTIALLY FIXED.**
- `claude_thesis` column IS populated at TIAS save time
  (collector.py:243–244 — `if record.get("claude_directive"):
  result["claude_thesis"] = record["claude_directive"]`). Sample row 821
  has `claude_thesis = '[APEX OPTIMIZED] TIAS shows no history for
  ONDOUSDT...'` (non-NULL).
- However, the `claude_thesis` is read FROM `record` at trade-close
  time. The "directive at entry" text comes via the trade-coordinator
  record, which is set at order placement. Confirmed: claude_thesis
  for trades 817-821 contains both the "[APEX OPTIMIZED]…" reasoning
  and the "Claude:" original-thesis text concatenated.
- The `claude_signal` column (sample: "Claude: STRONG ensemble 76.7,
  highest buy consensus...") is also populated at save time from
  `record["claude_plan_view"]` (collector.py:246).
- **GAP STATUS: APPEARS RESOLVED — entry-time directive text IS
  present in current rows.**

### 2. Mode4 data deleted by ProfitSniper before TIAS reads
**STATUS: FIXED via Phase 3 snapshot mechanism.**
- `src/workers/profit_sniper.py:781–795` — snapshot is saved
  to `_closed_snapshots[symbol]` BEFORE `_profit_states.pop(symbol)`
  on line 803.
- `src/workers/manager.py:1736–1739` — `_tias_close_callback` reads
  via `profit_sniper.get_closed_snapshot(sym)` first (preferred
  path), then falls back to direct `_profit_states` read.
- **However:** sample rows show `m4_peak_pnl_pct=0.0` for trades
  817, 819, 820, 821 — only 818 has `0.00256`. Many m4_* fields
  appear unpopulated. The snapshot wiring exists but field
  values are still mostly zero/null in 24h sample, suggesting the
  snapshot is saved but the M4 statistics rarely accumulate
  meaningful values within the typical 2–3-min hold time.
- **GAP STATUS: WIRING FIXED, DATA STILL SPARSE.**

### 3. Entry-time market conditions lost
**STATUS: PARTIALLY FIXED.**
- Schema has `entry_regime`, `entry_rsi`, `entry_macd_hist`,
  `entry_atr_pct` (added in v18+). NOT FOUND — the values from
  query for ID 821: only `regime` (close-time), `fear_greed_value`
  (close-time) populated; entry_regime/entry_rsi NOT in 10-row
  sample (column likely null). collector.py reads close-time
  values from caches (`_collect_group_c`, `_collect_group_d`),
  not entry-time. The `entry_*` columns exist but are only
  populated if record carries them at close.
- **GAP STATUS: SCHEMA ADDED, COLLECTOR DOES NOT POPULATE
  entry-time values yet — close-time values stored as
  rsi/macd_hist/etc.**

### 4. Signal score + strategy name not forwarded
**STATUS: FIXED.**
- `collector.py:225` — `result["entry_score"] = st_row.get("score")`
  (reads from strategy_trades by trade_id).
- `collector.py:248` — `if record.get("signal_score") is not None:
  result["entry_score"] = record["signal_score"]` (record overrides
  if available).
- `collector.py:141` — `"strategy_name": record.get("strategy_name", "")`
  populated unconditionally from record.
- Sample rows: `strategy_name=claude_trader` (verified via N4 query
  earlier in this collection). `entry_score` not visible in the 10-
  row sample (queried subset doesn't include) — needs separate query.
- **GAP STATUS: WIRING PRESENT, populated for current 24h rows.**

### Summary of TIAS gaps (now)
| Gap | Memory note status | Current code/DB status |
|---|---|---|
| Claude directive at entry | broken | wiring fixed; values present in rows 817–821 |
| Mode4 deleted before read | broken | wiring fixed; data values still mostly 0.0 (short holds) |
| Entry-time market conds | broken | schema fixed; collector still reads close-time only |
| Signal score / strategy name | broken | wiring fixed; populated in 24h rows |
