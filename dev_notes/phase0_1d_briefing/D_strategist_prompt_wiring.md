# Phase 0.D — Strategist Prompt Wiring

**File:** `src/brain/strategist.py`

## Entry path

```
_layer_brain_loop (layer_manager.py:702)
  → _run_brain_cycle (layer_manager.py:716)
    → strategist.create_trade_plan() (line 355)
      → _build_trade_prompt() (line 1265-1577)
        → _format_packages_for_prompt(packages) (line 1167-1263)
      → claude.send_message(prompt, system) (line 372)
    → _parse_trade_plan(response) (line 379)
  → execute (layer_manager.py:800)
```

## `_build_trade_prompt` sections (lines 1265-1577)

Order:
1. Lines 1287-1297 — coaching + recent trades (optional)
2. Lines 1300-1335 — regime + Fear & Greed
3. Lines 1389-1390 — UNIVERSE FILTER ("Trade ONLY from this list")
4. Lines 1393-1437 — TRADE CANDIDATES (Phase 7 packages block)
   - Calls `_format_packages_for_prompt()` at line 1430 IF `cfg.brain.use_packages == True`
5. Lines 1442-1575 — MARKET DATA (legacy per-coin loop, ~12-14 KB)
6. Lines 1579-1605 — data lake recording

Two prompt construction paths coexist today:
- `use_packages=True` (default; config.toml:180) → packages block prepended, market_data loop **also runs**
- `use_packages=False` → packages block skipped, market_data loop only

Combined size today: ~12-14 KB target per docstring at line 1265-1267.

## `_format_packages_for_prompt` (lines 1167-1263)

```python
def _format_packages_for_prompt(self, packages: dict) -> str:
    # Sort by opportunity_score descending  (line 1190-1194)
    sorted_pkgs = sorted(
        packages.values(),
        key=lambda p: p.opportunity_score,
        reverse=True,
    )

    # Skip non-qualified non-position packages  (line 1204-1205)
    for pkg in sorted_pkgs:
        if not pkg.qualified and pkg.open_position is None:
            continue
        # render per-coin block (lines 1206-1262)
```

Per-coin block today renders:
- `### {symbol} - score=X.XX [open-position?]`
- `Setup: {setup_type} confidence X.XX [COUNTER if applicable]`
- `Price: ${current} ({change_24h_pct}%) regime={regime}`
- `Suggested SL/TP: $X/$Y (RR Z)`
- `Strategies: N fired, ensemble {consensus}, score X`
- `Signal: confidence X direction Y`
- `Funding: rate (signal)`
- `Why: {qualification_reasons}`
- `** OPEN POSITION: ...` (if applicable)

## Phase 6 extension (from plan)

Behind `[brain].surface_briefing_fields = false` flag (default off):
- Add `state_label` line: `### {symbol} — interestingness X [LABEL_A, LABEL_B]`
- Add `Votes: STRONG BUY 4.7 vs 1.2 (12 voters)` summary
- Add `Top BUY: ...` and `Top SELL: ...` (top-3 each)
- Add `Sentiment: overall X (news, reddit, F&G, momentum)` breakdown
- Add `Risk envelope: SL X-Y%, TP X-Y%, max size $N, max lev N, hold N-N min`
- Add `Action hint: "..."` (label.action_hint)

## TRADE_SYSTEM_PROMPT (lines 65-147)

Static constant explaining:
- Role: "aggressive but intelligent crypto futures trader"
- Trade count: target 3-6, minimum 2
- Setup quality rules: STRONG/GOOD/NEUTRAL/WEAK consensus
- Direction by regime
- Fear & Greed extreme contrarian
- JSON response format

Phase 6 appends a new section explaining:
- 22 state labels and what each means
- The votes block (top-3 each side, conviction interpretation)
- Interestingness score ranges (≥0.70 clean / 0.50-0.70 typical / 0.30-0.50 thin / <0.30 surface-only)
- "The system briefs you. It does NOT filter; it presents. You are the analyst."

## Decision parser (`decision_parser.py`)

Three extraction strategies (lines 25-77):
1. Direct `json.loads()`
2. Markdown fence extraction
3. Brace matching

Then `_parse_trade_plan` at strategist.py:2500+ coerces fields via `_safe_float` / `_safe_int` (lines 40-61).

## Brain → execution boundary

`StrategicPlan.new_trades` → `asyncio.create_task(_execute_trades_background(plan))` at `layer_manager.py:800`.
`_execute_new_trades` (layer_manager.py:1173-1210) delegates to `strategy_worker._execute_claude_trade()`.
StrategyWorker owns order placement (symbol validation, SL/TP, qty rounding, OrderService).

## Risk footprint

The 18 KB hard cap at Phase 6 ensures Claude context isn't squeezed. Current 12-14 KB + 2.3 KB new fields = ~16 KB worst case. Phase 9 cutover requires ≥25% headroom check.

## use_packages flag

Default `true` per `config.toml:180`. Phase 10 deletes this flag once briefing path is the only path.
