# TIAS Lessons Pipeline — Investigation Report

Scope: trace the Trade Intelligence Autopsy System (TIAS) end-to-end and establish
the definitive reason no TIAS-derived lesson lines reach the brain prompt today.

## Files Involved

| Path | Lines | Role |
|---|---|---|
| `src/tias/collector.py` | 577 | Phase 1 — collect/save trade context at close |
| `src/tias/repository.py` | 526 | DB I/O for `trade_intelligence` table |
| `src/tias/analyzer.py` | 212 | Phase 2 — orchestrate DeepSeek call + response mapping |
| `src/tias/backfill.py` | 218 | Phase 4 — periodic retry worker for unanalyzed rows |
| `src/tias/deepseek_client.py` | 248 | OpenRouter HTTP client (used by analyzer + sentinel) |
| `src/tias/prompts.py` | 177 | DeepSeek system prompt + user-prompt builder |
| `src/tias/models.py` | 135 | `TradeIntelligence` dataclass |
| `src/core/thesis_manager.py` | 314 | Thesis lifecycle + `get_recent_lessons` (NOT TIAS) |
| `src/workers/manager.py` (excerpt 1719-1883) | 165 | TIAS wire-up, close callback, backfill loop |
| `src/brain/strategist.py` (excerpt 2235-3390) | n/a | Two prompt builders consuming `trade_thesis` lessons |
| `src/config/settings.py:1738-1751` | 14 | `TIASSettings` dataclass |
| `config.toml:1244-1255` | 12 | `[tias]` block |
| `src/database/migrations.py:1172-1174` | 3 | `ds_what_worked`/`ds_what_failed`/`ds_lessons` column defs |
| `src/sentinel/advisor.py` | n/a | Imports `DeepSeekClient` only — separate concern |
| `src/telegram/handlers/tias_handler.py` | n/a | Read-only dashboard queries against `tias_repo` |
| `src/apex/assembler.py` | n/a | Reads `tias_repo.get_symbol_full_history` for APEX |

## TIAS Schema

The `trade_intelligence` table (defined `src/database/migrations.py:1130-1180` and
extended via ALTER TABLE statements documented inline in the schema dump) groups
columns by capture source. The dataclass mirror lives at
`src/tias/models.py:10-135`.

- Group A (`models.py:26-38`) — trade outcome. Always populated:
  `symbol, direction, strategy_name, strategy_category, source, closed_by,
  entry_price, exit_price, pnl_pct, pnl_usd, win, hold_seconds`.
- Group B (`models.py:40-52`) — entry decision context, possibly None:
  `leverage, position_size_usd, claude_thesis, claude_signal, claude_confidence,
  entry_score, ensemble_votes` plus entry-time snapshot
  `entry_regime, entry_rsi, entry_macd_hist, entry_atr_pct`.
- Group C (`models.py:54-58`) — market conditions at close:
  `regime, fear_greed_value, fear_greed_label, regime_verified`.
- Group D (`models.py:60-73`) — technical indicators at close from `TACache`:
  `rsi, macd_hist, macd_signal, bollinger_pct, ema_20, ema_50, stochastic_k,
  stochastic_d, adx, atr_value, atr_pct, volume_ratio, price_vs_vwap`.
- Group E (`models.py:75-84`) — Mode4 profit-tracking from `ProfitSniper`:
  `m4_peak_pnl_pct, m4_ticks_in_profit, m4_ticks_total, m4_composite_score,
  m4_hurst_value, m4_momentum_decay, m4_extension_score, m4_ev_ratio,
  m4_volume_div_score`.
- Group APEX (`models.py:86-104`) — `apex_optimized`, `apex_flipped`,
  `apex_original_*` / `apex_final_*` (sl, tp, size, direction),
  `apex_confidence, apex_tp_mode, apex_reasoning, apex_model, apex_response_ms,
  apex_cost_usd, gate_adjustments, apex_tp_fill_rate`.
- Group F (`models.py:106-130`) — DeepSeek-analyzed fields (`ds_*`):
  `ds_why, ds_what_worked, ds_what_failed, ds_lessons, ds_category,
  ds_confidence, ds_analyzed_at, ds_correct_direction, ds_what_should_done,
  ds_how_to_exploit, ds_optimal_direction, ds_optimal_sl_pct,
  ds_optimal_tp_pct, ds_optimal_size_usd, ds_optimal_leverage, ds_raw_response,
  ds_response_time_ms, ds_input_tokens, ds_output_tokens, ds_cost_usd,
  ds_model, analysis_version`.
- Group G (`models.py:132-135`) — metadata: `trade_id, trade_closed_at,
  captured_at`. Schema also stores `exchange_mode` (P4 wire-in, schema v29).

## Phase 1 — Capture Pipeline (Currently Working)

Phase 1 fires as a close-callback registered by `WorkerManager`.
`src/workers/manager.py:1798-1835` defines `_tias_close_callback(record)`; the
callback is registered at `manager.py:1835` via
`coordinator.register_close_callback(_tias_close_callback)`.

The callback first synchronously captures the ephemeral `ProfitSniper` state
(`manager.py:1804-1821`) — preferring `get_closed_snapshot(sym)` and falling
back to direct read of `_profit_states[sym]`. It then schedules
`_tias_async_task(record, m4_snapshot)` on the event loop
(`manager.py:1823-1827`). That async task calls
`tias_collector.collect_and_save(record, tias_repo, m4_snapshot)`
(`manager.py:1789-1791`) and on success spawns the Phase 2 background task
(`manager.py:1792-1796`).

`TradeContextCollector.collect_and_save` (`collector.py:46-130`) runs the five
group extractors:

- Group A (`collector.py:136-151`) — `_extract_group_a` reads outcome fields
  directly off the close-callback record dict. No DB calls. Always populated.
- Group B (`collector.py:157-261`) — `_collect_group_b` queries
  `trade_thesis` for `leverage / size_usd / thesis / consensus`
  (`collector.py:176-185`) and `strategy_trades` for `score /
  ensemble_strength / ensemble_votes_*` (`collector.py:201-223`). Phase 3
  override at `collector.py:243-259` rewrites the result from
  `record["claude_directive" / "claude_plan_view" / "signal_score" /
  "ensemble_score" / "entry_regime" / "entry_rsi" / "entry_macd_hist" /
  "entry_atr_pct"]` because the strategy worker now forwards entry-time
  snapshots through the close record (more accurate than re-querying).
- Group C (`collector.py:267-326`) — `_collect_group_c` calls
  `services["regime_detector"].get_coin_regime(symbol)` and falls back to
  `regime_detector._last_regime` (`collector.py:294-305`); also reads the
  latest `fear_greed_index` row (`collector.py:314-319`).
- Group D (`collector.py:332-427`) — `_collect_group_d` calls
  `services["ta_cache"].analyze(symbol, TimeFrame.M5, limit=100)` then maps
  the engine output into the 13 indicator columns. The field paths are
  documented inline (`collector.py:336-348`). `bollinger_pct` and
  `price_vs_vwap` are computed locally from the close price.
- Group E (`collector.py:433-482`) — `_collect_group_e` merges the synchronous
  `m4_snapshot` (`peak_pnl_pct / ticks_in_profit / ticks_total`) with the
  latest `sniper_log` row for `composite_score / hurst_value /
  momentum_decay_score / extension_score / ev_ratio / volume_div_score`.

After group collection, `_collect_apex_data` (`collector.py:488-557`) lifts
APEX original/final values off the record. A second `trade_thesis` query
(`collector.py:78-87`) supplies `apex_final_sl / apex_final_tp` from the
post-flip thesis row when APEX optimized the trade.

The combined `TradeIntelligence` dataclass is then INSERTed by
`TradeIntelligenceRepo.save` (`repository.py:25-49`). On success the row id
is returned; the manager wrapper logs `TIAS_SAVE` (`collector.py:109-120`).

Verification of capture health: `data/trading.db` holds 1,755 rows for
`trade_intelligence` with `MIN(ds_analyzed_at)=2026-04-06T14:41:00Z` and
`MAX(ds_analyzed_at)=2026-05-16T04:05:00Z`. Phase 1 has been running
end-to-end for at least 40 days.

## Phase 2 — Analyzer Pipeline

`TradeAnalyzer.analyze(trade)` (`analyzer.py:46-75`) builds the user prompt
via `build_user_prompt(trade)` (`prompts.py:49-177`), then calls
`_call_with_fallback` (`analyzer.py:77-132`) which attempts
`s.primary_model` and, on retryable failure only, falls back to
`s.fallback_model`. The DeepSeek HTTP request runs in
`DeepSeekClient.analyze` (`deepseek_client.py:98-208`) — a JSON POST to
OpenRouter with `response_format: json_object`, `temperature`, and
`max_tokens` from settings.

The DeepSeek system prompt (`prompts.py:19-43`) instructs the model to return
a JSON object with exactly these fields: `why, category, correct_direction,
what_should_have_done, how_to_exploit_next_time, optimal_sl_pct,
optimal_tp_pct, optimal_size_usd, optimal_leverage, confidence`.

`TradeAnalyzer._map_response` (`analyzer.py:134-212`) maps DeepSeek keys to DB
columns:

- `why` -> `ds_why` (`analyzer.py:192`)
- `category` -> `ds_category` (`analyzer.py:193`)
- `correct_direction` -> both `ds_correct_direction` and `ds_optimal_direction`
  (`analyzer.py:194-195`)
- `what_should_have_done` -> `ds_what_should_done` (`analyzer.py:196`)
- `how_to_exploit_next_time` -> `ds_how_to_exploit` (`analyzer.py:197`)
- `optimal_sl_pct / optimal_tp_pct / optimal_size_usd / optimal_leverage` ->
  matching `ds_optimal_*` columns (`analyzer.py:198-201`)
- `confidence` -> `ds_confidence` (`analyzer.py:202`)
- Metadata: `ds_analyzed_at, ds_raw_response, ds_model,
  ds_response_time_ms, ds_input_tokens, ds_output_tokens, ds_cost_usd,
  analysis_version` (`analyzer.py:204-211`).

The wire-in is at `src/workers/manager.py:1733-1796`:
- `manager.py:1735-1750` constructs `TradeAnalyzer` only when
  `tias_cfg.enabled and tias_cfg.api_key` is true.
- `manager.py:1752-1785` defines `_tias_analyze_background(row_id, trade_obj,
  symbol)` which calls `analyzer.analyze(trade_obj)` then
  `tias_repo.update_analysis(row_id, analysis)` (`manager.py:1759-1760`).
- `manager.py:1792-1796` spawns `_tias_analyze_background` via
  `_aio.get_event_loop().create_task(...)` only when `tias_analyzer is not
  None and row_id > 0 and trade_obj is not None`.
- `manager.py:1842-1875` adds a 30-minute backfill loop wrapping
  `TIASBackfillWorker.run_once` (`backfill.py:58-141`), which fetches up to
  10 rows where `ds_why IS NULL AND analysis_attempts < 3`
  (`backfill.py:68-71`, query in `repository.py:97-121`).

`config.toml:1244-1255` confirms `[tias].enabled = true` with
`primary_model = "deepseek/deepseek-chat-v3-0324"` and
`fallback_model = "deepseek/deepseek-chat"`. `TIASSettings`
(`settings.py:1738-1751`) defines `enabled: bool = False` and `api_key: str
= ""` as defaults; the runtime branch in `manager.py:1735` requires both.

## Why `ds_lessons` Never Populates — Root Cause

The original brief stated `ALL 1,755 rows with ds_lessons IS NULL`. Direct DB
query confirms that AND adds the critical complement:

```
SELECT 'total:'||COUNT(*), 'ds_lessons NULL:'||SUM(ds_lessons IS NULL),
       'ds_why NULL:'||SUM(ds_why IS NULL),
       'ds_analyzed_at NULL:'||SUM(ds_analyzed_at IS NULL)
FROM trade_intelligence;
-> total:1755 | ds_lessons NULL:1755 | ds_why NULL:0 | ds_analyzed_at NULL:0
```

`ds_why`, `ds_analyzed_at`, `ds_category`, `ds_confidence`, `ds_model` are all
populated on every row (sampled rows 1748-1755 confirm
`ds_model=deepseek/deepseek-chat-v3-0324` and recent
`ds_analyzed_at` between 2026-04-06 and 2026-05-16). **Phase 2 has been
running for 40 days, on every captured trade.**

So the question is not "why is Phase 2 inert" — it is "why does the analyzed
output never include `ds_lessons`, `ds_what_worked`, or `ds_what_failed`?"

Root cause: schema/dataclass-versus-analyzer divergence.

- `src/database/migrations.py:1172-1174` defines three placeholder columns
  named `ds_what_worked, ds_what_failed, ds_lessons` of type TEXT.
- `src/tias/models.py:108-110` declares the matching dataclass fields.
- `src/tias/repository.py:63-64` whitelists those keys in
  `update_analysis(...)` so the repo would accept them if a caller supplied
  them.
- But the DeepSeek system prompt (`prompts.py:28-39`) lists exactly the
  current schema keys (`why, category, correct_direction,
  what_should_have_done, how_to_exploit_next_time, optimal_sl_pct,
  optimal_tp_pct, optimal_size_usd, optimal_leverage, confidence`) — no
  `lessons`, no `what_worked`, no `what_failed`.
- `TradeAnalyzer._map_response` (`analyzer.py:190-211`) returns a dict whose
  keys NEVER include `ds_lessons / ds_what_worked / ds_what_failed`. Search
  across the entire codebase confirms: only `repository.py:64`, `models.py:108-110`,
  and the migration line reference these names; no producer ever writes them.

The summary: `ds_lessons / ds_what_worked / ds_what_failed` are
**vestigial placeholder columns** that survived the Phase 2 schema shift to
the actionable-fields format (`ds_why / ds_category / ds_what_should_done /
ds_how_to_exploit / ds_correct_direction / ds_optimal_*`). The prompt no
longer asks for them, and the mapper no longer produces them, but the
columns and dataclass slots were never removed.

For brain-prompt enrichment purposes, the live lessons surface is
`ds_why + ds_category + ds_what_should_done + ds_how_to_exploit` — those
ARE populated. The "ds_lessons NULL on every row" finding from
`phase0_baseline.md:151` is correct but slightly misleading: the system has
analyzed every trade, just into different columns than the placeholder name
suggests.

Verdict on classification: **wired-but-vestigial** — Phase 2 is wired,
running, and producing analysis; the `ds_lessons` column is wired but its
producer never emits the key.

## Sample Captured Data

Five most recent rows from `trade_intelligence` (id desc) — Group A/B/C
visible, ds_why populated, ds_lessons NULL:

```
id=1755 ETHUSDT  Sell pnl=+0.008% closed_by=system_close
  regime=ranging      ds_category=CORRECT_ENTRY
  claude_thesis="[APEX OPTIMIZED] Trader's original Sell direction is correct..."
  ds_why="The trade succeeded due to correctly identifying a ranging market..."
  ds_lessons=NULL  ds_what_worked=NULL  ds_what_failed=NULL

id=1754 KATUSDT  Sell pnl=+0.229% closed_by=system_close
  regime=trending_down  ds_category=CORRECT_ENTRY
  claude_thesis="[APEX OPTIMIZED] Direction locked Sell aligns with..."
  ds_why="The trade succeeded primarily due to alignment with the prevailing..."

id=1752 GMTUSDT  Sell pnl=-0.468% closed_by=system_close
  regime=trending_down  ds_category=TREND_REVERSAL
  claude_thesis="[APEX OPTIMIZED] Direction remains Sell as per trader's..."
  ds_why="The trade lost because the entry was made at a range top in a..."
  ds_what_should_done="Waited for a stronger confirmation of continuation..."

id=1749 ALICEUSDT Sell pnl=-0.135% closed_by=wd_claude_action
  regime=trending_down  ds_category=TREND_REVERSAL
  ds_why="The trade lost primarily because the market reversed shortly..."

id=1748 CRVUSDT  Sell pnl=-0.953% closed_by=wd_claude_action
  regime=trending_down  ds_category=ENTRY_TOO_EARLY
  ds_why="The trade lost primarily due to entering a short position during..."
```

This confirms: the actionable analysis is there, just under different column
names than the originally-scoped placeholders.

## Lesson Bridge to Brain Prompt

### Status today

`thesis_manager.get_recent_lessons` (`thesis_manager.py:211-227`) queries
`trade_thesis` — the older "Data A" thesis lifecycle store, NOT
`trade_intelligence`. It returns `symbol, direction, entry_price,
close_price, actual_pnl_pct, actual_pnl_usd, close_reason, lesson, thesis,
opened_at, closed_at`. The `lesson` column on `trade_thesis` is a free-text
field supplied by `close_thesis(...)` callers and is mostly auto-generated
(e.g. the "transformer_switch" template at `thesis_manager.py:163-167`).
**This is unrelated to TIAS Group F.**

Today's only TIAS consumer is `IntelligenceAssembler`
(`src/apex/assembler.py`) which reads `tias_repo.get_symbol_full_history`
(`repository.py:322-422`) to feed APEX optimization. The brain itself never
reads TIAS. There is no bridge today.

Brain prompt build paths:

- CALL_A — `Strategist._build_trade_prompt` (`strategist.py:2235-2924`).
  Reads `thesis_manager.get_recent_lessons(limit=10)` at
  `strategist.py:1277` (legacy formatter path) but that lives in the older
  `_build_context_prompt` flow, not the active `_build_trade_prompt`. The
  active CALL_A appends a "## LESSONS FROM RECENT TRADES" block; the
  comment at `strategist.py:3341-3345` explicitly flags this as the
  surviving recency-bias surface still pending follow-up.
- CALL_B — `Strategist._build_position_prompt` (`strategist.py:3150-3390`).
  `strategist.py:3336-3349` documents the deliberate removal of recent
  lessons from CALL_B as part of "Post-Execution Closure Fix Phase 1A
  (2026-05-05)". The closed-loop failure mode is captured in the comment:
  one bad close became a CALL_B "lesson" that drove the next close, etc.

### Where E6 enrichment would attach

- CALL_A per-coin block — the briefing-mode "TRADE CANDIDATES" section
  (`strategist.py:1565` and `:1900`) renders one block per qualified coin
  emitted by `ScannerWorker`. The scanner already feeds the
  `RECENT_LOSER_COOLDOWN` advisory label via `record["blockers"]`
  (`scanner_worker.py:1156-1162`), and the label set is documented at
  `strategist.py:226-228`. E6 should add one extra inline line per coin —
  `Last loss: SHORT @ 2025-05-15 18:33 closed -0.47% TREND_REVERSAL. Cause:
  range-top short reversed quickly` — adjacent to the
  `RECENT_LOSER_COOLDOWN` label so the brain has the rationale, not just
  the flag.
- CALL_B per-position block — `strategist.py:3174` ("## YOUR OPEN
  POSITIONS"). After the position's PnL/thesis line, inject a one-line
  TIAS pull keyed on `(symbol, direction, regime)` matching the open
  position. Place it ABOVE the FLIPPED notice (`:3324`) so it informs the
  same decision.

### Proposed SQL

For E6 the production query needs to return at most 1-2 rows: the most
recent losing trade for `symbol` in (optionally) the same regime, with all
the fields the renderer needs. Indexes already exist:
`idx_ti_symbol, idx_ti_win, idx_ti_trade_closed_at` (see schema dump).

```sql
SELECT id, symbol, direction, ROUND(pnl_pct, 3) AS pnl_pct, hold_seconds,
       closed_by, regime, ds_category,
       substr(ds_why, 1, 140)              AS ds_why_short,
       substr(ds_what_should_done, 1, 140) AS lesson_short,
       trade_closed_at
FROM trade_intelligence
WHERE symbol = ?
  AND win   = 0
  AND (? IS NULL OR regime = ?)
  AND trade_closed_at >= datetime('now', '-72 hours')
ORDER BY trade_closed_at DESC
LIMIT 2;
```

`recent_loss_symbols` (`src/core/trade_recorder.py:24-52`) already
demonstrates the SCAN pattern over `trade_intelligence` and is the natural
sibling helper to introduce alongside the renderer.

## Proposed Lesson-Line Format

The renderer should produce one tight line per loss, suitable for inline
injection inside the per-coin / per-position block. Goal: 60-100 chars of
signal-dense text the brain can read mid-decision.

Post-Phase-2 format (uses populated `ds_*`):

```
Last loss: SHORT @ 2026-05-15 18:33  pnl=-0.47%  TREND_REVERSAL
  Lesson: Waited for stronger continuation confirmation (~140c excerpt).
```

Pre-Phase-2 fallback (Group A/B/C only — should never apply now, but kept
for robustness if `ds_why` ever rolls back to NULL):

```
Last loss: SHORT @ 2026-05-15 18:33  pnl=-0.47%  closed_by=wd_claude_action  regime=trending_down
```

Concrete example, generated against id=1752 (GMTUSDT):

```
Last loss: SELL @ 2026-05-15 16:42  pnl=-0.47%  hold=33m  TREND_REVERSAL
  Cause: range-top short reversed quickly despite downtrend confirmation.
  Lesson: Wait for stronger continuation confirmation (lower-high break).
```

That maps directly onto:
- direction from `direction` (`models.py:28`)
- timestamp from `trade_closed_at` (`models.py:134`)
- PnL from `pnl_pct` (`models.py:35`)
- hold minutes from `hold_seconds / 60` (`models.py:38`)
- category from `ds_category` (`models.py:111`)
- cause excerpt from `ds_why` (first 90 chars, `models.py:107`)
- lesson excerpt from `ds_what_should_done` (first 90 chars,
  `models.py:116`).

If the operator prefers an even tighter single line for the CALL_A scanner
strip:

```
LAST_LOSS: 2026-05-15 -0.47% TREND_REVERSAL (range-top short reversed)
```

That is 60 chars. CALL_A's per-coin blocks already routinely sit around
1000-1400 chars (`strategist.py:1819`), so adding 60-100 chars per
RECENT_LOSER coin is well within budget.

## Verdict

- Phase 1 capture: working. 1,755 rows captured across 40 days,
  `src/workers/manager.py:1798-1835` registers the callback,
  `src/tias/collector.py:46-130` executes the five group extractors plus
  APEX tracking on every trade close.
- Phase 2 analyzer: working. All 1,755 rows have `ds_why /
  ds_analyzed_at / ds_category / ds_model` populated;
  `src/workers/manager.py:1752-1796` runs the analyzer per row,
  `src/tias/backfill.py:58-141` re-tries every 30 min for any nulls.
- `ds_lessons / ds_what_worked / ds_what_failed`: vestigial placeholder
  columns. The current DeepSeek schema (`src/tias/prompts.py:28-39`)
  doesn't request them and `analyzer._map_response`
  (`src/tias/analyzer.py:190-211`) doesn't produce them. They are
  effectively dead columns.
- Bridge to brain prompt: zero. CALL_A's surviving "## LESSONS FROM
  RECENT TRADES" block (`src/brain/strategist.py:1277`) reads
  `thesis_manager.get_recent_lessons`, which returns `trade_thesis` rows,
  not TIAS rows. CALL_B's lessons block was deliberately removed
  (`src/brain/strategist.py:3336-3349`).
- E6 pre-requisite: build a TIAS lesson renderer that pulls
  `ds_why + ds_category + ds_what_should_done` per `(symbol, regime, win=0)`
  with the SQL above, and inject one-line summaries adjacent to the
  existing `RECENT_LOSER_COOLDOWN` advisory in CALL_A's per-coin block and
  alongside each open position in CALL_B. NO schema work needed; the
  data is already there. Operator-approved commit handle is
  `prompt-enrich/p3-0`.
