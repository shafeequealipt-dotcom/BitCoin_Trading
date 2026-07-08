# Phase 1 — Investigation Synthesis

## Purpose

Independent investigation of the prior session loss analysis (`SESSION_LOSS_ANALYSIS_2026_05_19.md`), the operator's chosen design (Options 1A + 2A + 2C with Approach C primary + Approach A fallback), and the current code surfaces relevant to implementation. Per IMPLEMENT_MIDHOLD doc Rule 1 + Phase 1 deliverable.

Date: 2026-05-19
Status: Investigation complete. Ready for operator decision gate (Phase 2).

## 1. Validation of prior session loss analysis (IMPLEMENT doc Phase 1 step 1.1)

### Claims confirmed against current code

| Claim | Verification |
|---|---|
| "CALL_B does not consult ensemble during hold" (§5.1) | CONFIRMED. `_build_position_prompt()` at `src/brain/strategist.py:3908+` builds prompt from `regime_detector.get_coin_regime`, persisted `coordinator.get_trade_plan(symbol)`, and `thesis_manager.get_open_theses()`. No path reads `EnsembleVoter.vote()` or any per-symbol ensemble state for an open position. |
| "Three losses (SOL/ETH/DOGE) share decision ID `d-1779207311293`" (§3.2) | CONFIRMED structurally. The brain emits one CALL_A batch per cycle and assigns a single decision ID across all trades in the batch (see `Strategist.create_trade_plan` decision-ID generation). |
| "Strategy ensemble flipped to STRONG BUY at 16:36 ETH / 16:41 SOL" (§4.5, §4.6) | TIMING UNVERIFIED at log-line granularity in this synthesis (would require grepping `STRAT_VOTE_TRACE` events in `data/logs/workers.log`). The mechanism (ensemble votes recompute on signal-worker cadence) is structurally verified: `EnsembleVoter.vote()` at `src/strategies/ensemble.py:35-230` is stateless and recomputes per signal. Operator can confirm via grep `STRAT_VOTE_TRACE.*ETHUSDT.*16:36` per §12.2. |
| "Brain held all three Sells through the flips" (§4.5–4.7) | CONFIRMED mechanism: brain only sees ensemble at entry (`Strategist.create_trade_plan` consumes ensemble votes via signals from signal_worker). After entry, brain receives `_build_position_prompt` content which does not include current ensemble state. The mechanism makes the claim necessary. |
| "Brain cited structural justification for SOL/ETH/DOGE but not for DYDX/XRP" (§4.2 vs §4.5–4.7) | CONFIRMED through rationale-text patterns in §3.2 trade list. SOL/ETH/DOGE rationales contain "X-RAY: bearish downtrend… MTF=8-9/10… CONFL=7, fresh bearish OB"; DYDX rationale is "Only coin with real strategy votes — BUY=1.35 vs SELL=0.00"; XRP rationale is "TREND_PULLBACK_SHORT in trending_down regime". Three structural; two regime-based; consistent with Options 1A + 2A intent (1A catches ensemble flips on either; 2A catches level invalidation on structural). |

### Discrepancies and refinements

- **Schema version**: memory cited v32; actual current version is **v33** at `src/database/migrations.py:12`. No code impact — new migrations will target v34 and v35. Memory will be updated post-implementation.
- **Reentry cooldown duration**: memory cited "5-minute reentry cooldown"; actual `reentry_cooldown_seconds` default is **600 seconds = 10 minutes** at `src/config/settings.py:1281`. Branch name suggests 5-min was original intent. Not directly relevant to this fix; flag for memory hygiene.

## 2. CALL_A construction map (Phase 1 step 1.3)

Entry: `Strategist.create_trade_plan()` at `src/brain/strategist.py:797-903`.
Builds dynamic context via `_build_context_prompt()` at `src/brain/strategist.py:1070+`.

### Sections (in order, with file:line markers)

| Section | Range | Insertion-relevance |
|---|---|---|
| Coaching + Recent Trades (top) | 1075-1084 | — |
| Regime Instructions | 1119-1127 | — |
| Direction Performance | 1129-1135 | — |
| Trading Mode Instruction | 1137-1140 | — |
| Supported Symbols | 1142-1151 | — |
| Minimum Trade Sizes | 1153-1176 | — |
| Market Data (filtered) | 1178-1281 | [POS] tag on open-position rows at 1246; per-coin regime tag at 1250 |
| Regime Divergence | 1283-1303 | — |
| X-RAY Structural Intelligence | 1327-1469 | — |
| Fear & Greed Sentiment | 1471-1476 | — |
| Market Regime Summary | 1478-1502 | — |
| **YOUR OPEN POSITIONS** | **1516-1545** | **PRIMARY insertion point for 2C thesis state surfacing** — per-position thesis already rendered at 1534 |
| Recent Lessons | 1549-1607 | — |
| Aggregated Stats | 1590-1598 | — |
| Bybit Live Positions | 1609+ | — |

Open positions referenced via `thesis_manager.get_open_theses()` at line 1196-1200 (fetch for market-data filtering) and 1517 (full render).

System prompt for response schema: `src/brain/prompts/trade_decision.py:41-42`:
```json
{"action": "buy"|"sell"|"close"|"hold", "symbol": "BTCUSDT", "confidence": 0.0, "order_type": "market", "limit_price": null, "qty_pct": 0.0, "stop_loss": null, "take_profit": null, "leverage": 1, "reasoning": "", "risk_notes": ""}
```

## 3. CALL_B construction map (Phase 1 step 1.2)

Entry: `Strategist.create_position_plan()` at `src/brain/strategist.py:990-1068`.
Builds dynamic context via `_build_position_prompt()` at `src/brain/strategist.py:3908+`.

### Sections

| Section | Range | Insertion-relevance |
|---|---|---|
| Market Regime Summary | 3918 | — |
| Sentiment / F&G | 3921 | — |
| Daily PnL | 3927 | — |
| Direction Performance | 3941-3977 | — |
| **CONTRACT — POSITION MANAGEMENT** | **3991-4012** | — |
| **Per-position detail loop** | **4037-4098** | **PRIMARY insertion point for 2C thesis state + queued events** |
| APEX/XRAY Flip metadata | 4099-4135 | **CRITICAL — Phase 1C boundary**: flipped positions are visible here |
| Recent lessons | ~4149 | — |
| Aggregated stats | 4163-4170 | — |

### Critical constraint: CALL_B Framing Fix Phase 1C (`strategist.py:4061`)

```
# The free-text `thesis` column is intentionally NOT read here as of
# CALL_B Framing Fix Phase 1C (2026-05-06). The original thesis text was
# written for the pre-flip direction; on a flipped position it
# contradicts the current state shown in the same block, which Claude
# was reading as 'thesis broken' and using to drive premature closes.
```

Our new `thesis_invalidation` rendering must respect this. **Operator decision**: render with explicit "post-flip; informational only" annotation for APEX/XRAY-flipped positions. This avoids regressing Phase 1C by clearly framing the criterion as situational context, not as instruction.

System prompt for response schema: `src/brain/prompts/position_review.py:63-64`:
```json
{"action": "hold"|"tighten_stop"|"partial_close"|"full_close", "symbol": "...", "confidence": 0.0, "new_stop_loss": null, "reasoning": "...", "risk_notes": "..."}
```

CALL_B per-position rendering loop at line 4037; single Claude call covers all positions; response parsed into `{"position_actions": {"SYMBOL": {...}}}`.

## 4. Brain response parsing (Phase 1 step 1.4)

### Current shape

Brain responses are JSON (often within markdown fences). `DecisionParser._extract_json()` at `src/brain/decision_parser.py:42-80` has three fallback strategies:

1. Direct JSON parse (line 52)
2. Markdown code fences (line 58-64)
3. Find first `{` and last `}` (line 67-74)

`_build_decision()` at `src/brain/decision_parser.py:82-120` extracts documented keys and silently allows extra fields via:

```python
decision._fieldname = data.get("fieldname")
```

(line 106-110). This pattern means `thesis_invalidation` can be added without parser refactor — but strict validation must be added per Rule 4 (forbidden band-aid: "Detecting flips but not surfacing them to brain (silent observability is useless)").

Validation logic at `_build_decision` line 177-211 (`validate_decision`): required `action, symbol, confidence`; optional with clamping `order_type, leverage (1-5x), qty_pct, stop_loss, take_profit`. **No existing validation for extra fields.**

Today, invalid fields are silently dropped. The new `thesis_invalidation` must NOT be silently dropped — it must emit `BRAIN_THESIS_INVALIDATION_INVALID` and trigger Approach A fallback per Rule 16.

## 5. Strategy ensemble anatomy (Phase 1 step 1.5)

`EnsembleVoter.vote()` at `src/strategies/ensemble.py:35-230`.

### Consensus computation

Line 119-139:
- `agreeing = buy_votes if direction == Side.BUY else sell_votes`
- `opposing = sell_votes if direction == Side.BUY else buy_votes`
- **STRONG**: `agreeing >= 4.0 AND opposing <= 1.5` (hardcoded line 130)
- **GOOD**: `agreeing >= cfg.min_ensemble_agreement (default 5.0) AND opposing <= cfg.max_ensemble_opposition (default 1.0)` (line 132-133)
- **WEAK**: `agreeing >= 1.5 AND opposing <= 1.5`
- **LEAN**: `agreeing > opposing`
- **CONFLICT**: else

Configurable tunables in `src/config/settings.py:1518-1519`:
```python
min_ensemble_agreement: float = 5.0
max_ensemble_opposition: float = 1.0
```

### Consensus state storage

**Stateless** — `EnsembleVoter` recomputes on every call. There is no per-symbol history cache today. Signal-worker invokes ensemble during signal aggregation; the consensus value is then attached to a `Signal` object and written to DB via `signals` repository, but there is no read-friendly per-symbol most-recent-consensus accessor for the watchdog.

### What we need to add

Per Phase 3.4 of the plan: a new `EnsembleStateReader` (or attach a state cache to `EnsembleVoter`) with public method `get_current_consensus(symbol) -> {consensus, agreeing, opposing, agreeing_dir, ts}`. State populated by signal_worker after each ensemble vote (write-through). Read by watchdog in `_monitor_position()`.

This is purely additive — the existing stateless `vote()` API remains unchanged.

## 6. Structural levels for invalidation detection (Phase 1 step 1.6)

### Level identification

OB detection at `src/analysis/structure/order_blocks.py:43-120`. FVG detection at `src/analysis/structure/fair_value_gap.py:49-120`. Both produce dataclasses in `src/analysis/structure/structure_types.py`:

- `OrderBlock` (line 223-252): `direction`, `high`, `low`, `midpoint`, `created_index`, `created_at`, `fresh`, `strength_score`
- `FairValueGap` (line 192-219): `direction`, `top`, `bottom`, `filled`, `partially_filled`, `fill_percentage`, `displacement_strength`, `gap_size_pct`

Both rolled into `StructuralAnalysis` at `structure_types.py:536-656` (`fvgs`, `nearest_fvg`, `order_blocks`, `nearest_ob`, plus `market_structure.invalidation_level`).

### Persistence and state

**No per-level persistent monitoring**. Levels are recomputed every analysis cycle. `OB.fresh` and `FVG.filled` are recalculated from current price each cycle. This is by design for the engine, but creates a gap for our use case:

For Approach A fallback (heuristic monitoring of entry-time levels), we must **capture a snapshot at entry** and persist it in the trade row (as `thesis_snapshot` JSON). The watchdog then reads the snapshot and checks current price against the captured level — not the current engine output (which may have re-detected different levels).

### Invalidation semantics

"Invalidation" = price closes beyond the level. Current SL buffer at `src/analysis/structure/structural_levels.py:80`:
```python
sl_buffer = self._settings.sl_buffer_pct / 100.0  # default 0.5%
```

For our fix:
- DEGRADING transition: wick beyond level + 0.1% buffer (operator-tunable)
- INVALIDATED transition: M5 close beyond level + 0.5% buffer (operator-tunable, mirrors existing `sl_buffer_pct`)

### Existing watchdog invalidation logic

`src/workers/position_watchdog.py:1138-1215` — **deprecated** `_compute_structural_invalidation()` (still live as fallback when `layer4_protection is None`).
`src/workers/position_watchdog.py:1489` — canonical `layer4_protection.compute_structural_invalidation()`.

For Phase 3.5 we will:
1. Leverage `layer4_protection.compute_structural_invalidation()` where applicable (canonical path)
2. Add new criterion-driven logic when `thesis_source == 'brain_stated'` (compares against brain's stated price level or signal keyword)
3. Add new fallback logic when `thesis_source == 'heuristic_fallback'` (parses `thesis_snapshot` JSON, monitors nearest aligned OB/FVG per operator decision)

## 7. Watchdog anatomy (Phase 1 step 1.7)

`PositionWatchdog(BaseWorker)` at `src/workers/position_watchdog.py:91`.

### Lifecycle

- `tick()` at `position_watchdog.py:467`
- Per-position monitor: `_monitor_position(pos)` at `position_watchdog.py:1611`, called per-position via `asyncio.gather` with 3s timeout

### Existing per-position state dicts

- `_position_peaks[symbol]`
- `_last_prices[symbol]`
- `_last_pnls[symbol]`
- `_last_brain_call[symbol]`
- `_hold_suppression[symbol]`
- `_consecutive_holds[symbol]`
- `_last_alert_time[symbol]`
- `_last_skip_log[(symbol, reason)]`
- `_pnl_mismatch_retries[symbol]`
- `_position_open_times[symbol]` (legacy fallback)
- `_position_strategies[symbol]` (legacy)

**Will add**:
- `_position_consensus_state[symbol]` for Phase 3.4 — last known consensus + direction
- `_position_thesis_invalidation_state[symbol]` for Phase 3.5 — VALID/DEGRADING/INVALIDATED with last-state-change timestamp (in-memory mirror of DB column; DB is authoritative)

### Injected services

Mirror the `regime_detector` injection pattern in `src/workers/manager.py:131, 159, 672-679`:
- `self._services["regime_detector"]` → watchdog reads via `self.regime_detector.get_coin_regime(symbol)`

For Phase 3.4 we will add `self._services["ensemble_state_reader"]` so watchdog reads via `self.ensemble_state_reader.get_current_consensus(symbol)`.

### Existing event-queue infrastructure

Two in-memory event/queue surfaces already exist:

- `EventBuffer` at `src/core/event_buffer.py:49-286` — deque(maxlen=50), 30s dedupe per (symbol, event_type), drained by strategist into prompt
- `UrgentQueue` at `src/core/urgent_queue.py:35-207` — per-position concerns in PASSIVE mode, 150s per-symbol cooldown, 600s max age, formatted into CALL_A/CALL_B

These work today. **Operator decision**: for the new `thesis_events` (ensemble_flip + thesis_invalidation events) we use a **DB-backed table** (`thesis_events`) instead, to survive restarts. EventBuffer and UrgentQueue continue handling their existing event types unchanged.

### Insertion point for 3.4 + 3.5

Within `_monitor_position(pos)` at line 1611. After SENTINEL/deadline checks (~1643-1690) and before time-decay (~1750). The 3.4 ensemble-flip detection runs first; 3.5 thesis-invalidation detection follows. Both queue events via `thesis_manager.queue_thesis_event` (Phase 3.6 helper). Neither calls Claude directly.

## 8. Trade lifecycle and persistence (Phase 1 step 1.8)

### Entry path

1. `Strategist.create_trade_plan()` returns `BrainDecision` objects
2. `StrategyWorker._execute_claude_trade()` (`src/workers/strategy_worker.py:~1701`) validates, calls `OrderService.place_order()`, then `TradeCoordinator.register_trade()`
3. `TradeCoordinator.register_trade()` (`src/core/trade_coordinator.py:400-450`) populates `TradeState` in-memory at `_trades[symbol]`
4. `StrategyWorker._execute_claude_trade()` at line ~2678 calls `thesis_mgr.save_thesis()` with brain output → INSERT into `trade_thesis`

### `ThesisManager` (file: `src/core/thesis_manager.py`)

- `save_thesis()` at line 108-218 — INSERT (already accepts entry-context: APEX flip, XRAY entry anchors, regime context)
- `get_open_theses()` at line 220 — SELECT all `status='open'`
- `close_thesis()` at line 278-370 — UPDATE on close
- `get_recent_lessons()` at line 372
- `attach_transformer()` at line 427+ — late-bind mode-aware filtering (exchange_mode-aware queries)

**Will extend**:
- `save_thesis()` signature: add `thesis_invalidation`, `thesis_source`, `thesis_snapshot` parameters
- New `record_thesis_state(symbol, order_id, new_state)` — UPDATE filtered by symbol+order_id+status=open
- New `evaluate_thesis_state(thesis_row, current_price, structure_snapshot)` — pure function returning VALID/DEGRADING/INVALIDATED
- New `queue_thesis_event(symbol, order_id, event_type, payload)` — INSERT into `thesis_events`
- New `get_unseen_events(symbol_list)` — SELECT where `consumed_at IS NULL`
- New `mark_events_consumed(event_ids, consumer)` — UPDATE
- New `purge_events_for_closed_position(order_id)` — DELETE on position close

### Restart recovery

`TradeCoordinator.recover_state_from_db()` at `src/core/trade_coordinator.py:183-288` queries `trade_thesis WHERE status='open'` on boot and rebuilds `_trades`. With v34 columns added, the recovery picks up `thesis_invalidation`, `thesis_source`, `thesis_snapshot`, `thesis_state` automatically.

For event queue: DB-backed `thesis_events` table survives restarts. Unseen events for a still-open position will surface in the next CALL_A or CALL_B after restart.

## 9. Approach A fallback feasibility (Phase 1 step 1.9)

At entry time, `structure_cache.get(symbol)` returns the most recent `StructuralAnalysis` (populated by structure_worker on a 15s cadence). `StructuralAnalysis.to_dict()` at `structure_types.py:658-685` is fully serializable to JSON.

Per operator decision (Approach A scope = "nearest aligned level only"), we extract a compact subset for the snapshot:

```python
thesis_snapshot = {
    "captured_at": iso_now,
    "structure_engine_version": "phase_X",  # if available, else omit
    "current_price_at_entry": analysis.current_price,
    "direction": "Buy" | "Sell",
    "nearest_aligned_level": {
        "type": "ob" | "fvg" | "none",
        "side": "bullish" | "bearish",
        "high": ...,
        "low": ...,
        "midpoint": ...,
    },
    "market_structure_invalidation_level": analysis.market_structure.invalidation_level,
}
```

Selection logic:
- Sell entry → bearish OB above entry (if `nearest_ob.direction == "bearish"` and `nearest_ob.low > entry_price`), else bearish FVG above entry, else `none`
- Buy entry → bullish OB below entry, else bullish FVG below entry, else `none`

If `nearest_aligned_level.type == "none"` (no structural justification at entry, e.g., trend-pullback or APEX-flipped trade), the watchdog emits `THESIS_INVALIDATION_NO_ANCHOR` periodically and does not monitor. Per Rule 16, the brain still gets a `THESIS_INVALIDATION: source=heuristic_fallback state=NO_ANCHOR` line in prompts so it knows there is no fallback monitoring.

## 10. Event queue design (Phase 1 step 1.10)

### Storage (operator-decided): DB-backed `thesis_events` table

Schema v35 migration:

```sql
CREATE TABLE IF NOT EXISTS thesis_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  order_id TEXT NOT NULL,
  thesis_id INTEGER,
  event_type TEXT NOT NULL,   -- 'ensemble_flip' | 'thesis_invalidation'
  payload TEXT NOT NULL,      -- JSON detail
  created_at TEXT NOT NULL,
  consumed_at TEXT,           -- NULL = unseen by brain
  consumed_by TEXT,           -- 'CALL_A' | 'CALL_B' on consume
  FOREIGN KEY (thesis_id) REFERENCES trade_thesis(id)
);
CREATE INDEX IF NOT EXISTS idx_thesis_events_unconsumed ON thesis_events(symbol, consumed_at) WHERE consumed_at IS NULL;
```

### Lifecycle

| Event | Action |
|---|---|
| Position opens (after `thesis_mgr.save_thesis()`) | No explicit init — empty result set for that symbol is natural |
| Watchdog detects ensemble flip | `queue_thesis_event(symbol, order_id, 'ensemble_flip', payload)` INSERT |
| Watchdog detects invalidation | `queue_thesis_event(symbol, order_id, 'thesis_invalidation', payload)` INSERT |
| CALL_A or CALL_B is built | `get_unseen_events([symbol1, symbol2, ...])` → render in prompt → after Claude response: `mark_events_consumed([ids], 'CALL_A')` (or `'CALL_B'`) |
| Position closes (via `close_thesis()`) | `purge_events_for_closed_position(order_id)` — DELETE |

### Tunables (operator-controlled)

```python
position_event_queue_max_per_symbol: int = 10           # cap; oldest dropped if exceeded
position_event_queue_retention_seconds: int = 0          # 0 = clear on consume; >0 = keep after consume for audit
ensemble_flip_dedupe_window_seconds: float = 300.0
```

### Why DB-backed (not in-memory)

Per operator decision: survives restarts. EventBuffer and UrgentQueue are in-memory because their event types are sub-minute alerts whose loss-on-restart is acceptable. `thesis_events` events represent thesis-state observations during multi-minute hold windows; losing them on restart would leave brain blind to mid-hold flips after a process restart. DB-backed solves this with minimal cost.

## 11. Final architectural design

### Data flow

```
Entry path:
  CALL_A (brain) → returns {action, symbol, direction, ..., thesis_invalidation: {type, value}}
    → DecisionParser validates thesis_invalidation
      → if valid: thesis_source = 'brain_stated'
      → if missing: thesis_source = 'heuristic_fallback'; capture XRAY snapshot
      → if invalid: thesis_source = 'heuristic_fallback'; capture XRAY snapshot
    → StrategyWorker._execute_claude_trade → OrderService.place_order
    → TradeCoordinator.register_trade (in-memory)
    → ThesisManager.save_thesis (DB INSERT with v34 columns)

Hold path (per watchdog tick):
  _monitor_position(pos):
    # 3.4 — Ensemble flip detection
    current = ensemble_state_reader.get_current_consensus(pos.symbol)
    if current.consensus == 'STRONG' and current.agreeing_dir != pos.direction:
      if dedupe_passed:
        log ENSEMBLE_FLIP_DETECTED
        thesis_manager.queue_thesis_event(symbol, order_id, 'ensemble_flip', payload)

    # 3.5 — Thesis-invalidation detection
    thesis_row = thesis_manager.get_open_thesis_for_symbol(symbol, order_id)
    new_state = thesis_manager.evaluate_thesis_state(thesis_row, current_price, structure_snapshot)
    if new_state != thesis_row.thesis_state:
      thesis_manager.record_thesis_state(symbol, order_id, new_state)
      log THESIS_LEVEL_MONITORED (or _DETECTED if INVALIDATED)
      if new_state == 'INVALIDATED':
        thesis_manager.queue_thesis_event(symbol, order_id, 'thesis_invalidation', payload)

Surfacing path (per Claude call):
  CALL_A build:
    open_positions = thesis_manager.get_open_theses()
    for each: render THESIS_INVALIDATION line + state
    unseen = thesis_manager.get_unseen_events([symbols])
    render QUEUED_EVENTS lines
    → submit prompt → on response: thesis_manager.mark_events_consumed(ids, 'CALL_A')

  CALL_B build:
    same as CALL_A, except for flipped positions render with _PRE_FLIP_INFORMATIONAL prefix

Close path:
  ThesisManager.close_thesis → also calls purge_events_for_closed_position(order_id)
```

### Specific file:line implementation surfaces

| Phase | File | Line | Operation |
|---|---|---|---|
| 3.1 | `src/database/migrations.py` | 12 + MIGRATIONS list | Bump `SCHEMA_VERSION` 33→35; append v34 + v35 SQL |
| 3.1 | `src/core/thesis_manager.py` | 108 (`save_thesis`), append new methods | Extend save signature; add 7 new methods |
| 3.2 | `src/brain/prompts/trade_decision.py` | 41-42 | Extend JSON schema example |
| 3.3 | `src/brain/decision_parser.py` | 82-120 (`_build_decision`) | Add extraction + validation |
| 3.3 | `src/workers/strategy_worker.py` | ~1701, ~2678 | Capture parsed criterion + XRAY snapshot |
| 3.4 | `src/strategies/ensemble.py` | append new `EnsembleStateReader` class | New stateful cache |
| 3.4 | `src/workers/manager.py` | 131, 159, 672-679 | Wire `ensemble_state_reader` into watchdog services |
| 3.4 | `src/workers/position_watchdog.py` | `_monitor_position` ~1750 (post-SENTINEL pre-time-decay) | Add detection block |
| 3.5 | `src/workers/position_watchdog.py` | same `_monitor_position` | Add invalidation block after 3.4 |
| 3.6 | `src/core/thesis_manager.py` | append | Add 4 queue methods |
| 3.6 | `src/core/trade_coordinator.py` | close-callback path (also `src/workers/manager.py:1407`) | Wire `purge_events_for_closed_position` |
| 3.7 | `src/brain/strategist.py` | 1516-1545 | Extend per-position rendering with thesis state + events |
| 3.7 | `src/brain/strategist.py` | post-CALL_A response (around `create_trade_plan`) | `mark_events_consumed(ids, 'CALL_A')` |
| 3.8 | `src/brain/strategist.py` | 4037-4098 | Per-position rendering with flip annotation |
| 3.8 | `src/brain/strategist.py` | post-CALL_B response | `mark_events_consumed(ids, 'CALL_B')` |
| All sub-phases | `src/core/log_tags.py` | append | New tag string constants |
| All sub-phases | `src/config/settings.py` | `WatchdogSettings` block | New operator-tunable fields |

### Five aim-bias question re-check (with file:line evidence)

| Question | Answer | Evidence |
|---|---|---|
| 1. Preserves trade frequency? | YES | No new entry gates; no rejection logic; no candidate pre-filter. Verified by enumerating Phase 3.x file changes — none touch `Strategist.create_trade_plan` rejection logic or `_validate_decision` |
| 2. Preserves aggression? | YES | Brain still decides; only information is surfaced. No new clamp/cap on direction or size |
| 3. Improves decision quality? | YES | Brain sees mid-hold flips + thesis state via new prompt sections |
| 4. Preserves passive-close advantage? | YES | No changes to `wd_dl_action`, `wd_timeout`, SL, TP. Watchdog adds *detection* and *queuing*, not new force-close paths |
| 5. Respects structural separation? | YES | Watchdog detects (position_watchdog.py); ThesisManager persists (thesis_manager.py); brain decides (strategist.py / brain LLM); structure_engine provides levels (analysis/structure/) |

## 12. Phase 1 sign-off

All 11 IMPLEMENT doc Phase 1 sub-steps either confirmed or refined. No discrepancies with the operator's chosen design. Architectural surfaces identified by file:line. Ready for Phase 2 operator decision gate.

Phase 1 complete. Proceeding to Phase 3.1 (after operator decision gate — implicit in plan-mode approval per session protocol).
