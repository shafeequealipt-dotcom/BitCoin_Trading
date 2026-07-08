# Phase 0.4 — Stage 2 Prompt Builder Investigation

**Investigated:** `src/brain/strategist.py` lines 339-1934 (key methods grep'd). HEAD = `8dca492`.

## A. Entry points

- `create_trade_plan()` at line 339 → calls `_build_trade_prompt()` at line 1141 (CALL_A; "find new trades").
- `create_position_plan()` at line 412 → calls `_build_position_prompt()` at line 1712 (CALL_B; "manage positions").

Brain loop in `LayerManager._brain_review_loop()` alternates A → sleep(150s) → B → sleep(150s) → A …

## B. CALL_A sections (preliminary; line spans grep'd, NOT enumerated end-to-end)

The blueprint claims "19 sections" (Section 4.4). The strategist file is large (≥1934 lines). Grep'd anchors:

| Anchor | Line | Section purpose |
|---|---|---|
| `enforcer.get_coaching_text(...)` | 1157-1162 | Performance coaching block (top-of-context) |
| `regime instructions` (comment) | 524 in another fn (`create_trade_plan`); also embedded in `_build_trade_prompt`) | Regime context |
| `await scanner.get_active_universe()` | 1250 | The 30-coin universe (becomes packages in Phase 7) |
| `layer_manager._strategy_hints` | 1579-1580 | Top-N strategy detail |
| `layer_manager._strategy_consensus` | 1587 | Strategy consensus summary |

Per the plan, Phase 0 originally requires enumerating all 19 sections with their data sources. **Tightened scope:** for Phase 7, the per-coin sections are the ones to migrate to packages. Globally-sourced sections (coaching, regime, account, daily PnL, urgent_queue, event_buffer, fear_greed, drawdown) stay as service queries.

The detailed section enumeration (per-coin vs global) is a Phase 7 sub-task — `phase7_layer1_restructure_report.md` will list each section with its line range, data source, and migration disposition (per-coin → package, or global → query).

## C. Per-coin sections (will become packages in Phase 7)

Per the blueprint Section 11.2, the package shape covers:

- `price_data` — `market.get_ticker(symbol)` per coin
- `xray` — `structure_cache.get(symbol)` per coin (setup_type added in Phase 2)
- `strategies` — `_strategy_hints` filtered to coin + `_strategy_consensus[coin]` (Phase 3)
- `signals` — `_signal_cache.get(coin)` + sentiment per coin
- `alt_data` — `_funding_cache.get(coin)` + OI + fear_greed (global, but stored once)
- `open_position` — `position_service.get_position(symbol)` per force-included coin

**These are the sections to migrate.** ScannerWorker (Phase 6) builds `CoinPackage` per selected coin reading these caches once; strategist (Phase 7) reads packages instead of querying 12 services per coin.

## D. Global sections (stay as service queries)

- Coaching (`enforcer.get_coaching_text`)
- Regime instructions (`regime_detector.get_market_regime`)
- Account (`account_service.get_wallet_balance`)
- Daily PnL (`pnl_manager`)
- Urgent queue (`urgent_queue.get_prompt_text`)
- Event buffer (`event_buffer.get_prompt_text`)
- Fear & Greed (single value, but referenced once globally + once per coin)
- Drawdown summary (`pnl_manager`)

## E. Restructure change plan (Phase 7)

1. **New helper `_format_packages_for_prompt(packages: dict[str, CoinPackage]) -> str`** per blueprint Section 7 example.
2. **Replace per-coin loop in `_build_trade_prompt`** (the block iterating `await scanner.get_active_universe()` at line 1250 and querying 6+ services per coin) with one call to `_format_packages_for_prompt(packages)`.
3. **Read packages from `layer_manager._coin_packages`** (Phase 6's output). Defensive: `packages = getattr(self.layer_manager, "_coin_packages", {})`.
4. **Fallback** when packages empty: log `PROMPT_PACKAGES_EMPTY | reason=no_qualified_coins` and emit "No qualifying setups this cycle".
5. **CALL_B rewire** (`_build_position_prompt` at 1712): read `[pkg for pkg in packages.values() if pkg.open_position]` and emit position-management blocks. Global sections (account, drawdown, urgent_queue) stay.
6. **Backward-compat shim**: `[brain].use_packages = true` flag (default true). Set false to fall back to legacy service-query path during Phase 9 if regression. Removed in cleanup commit after Phase 9 success.
7. **Observability**: `PROMPT_BUILD_DONE | call=CALL_A coins=12 size_bytes=7800 sections_global=11 sections_packages=12 elapsed_ms=85`.

## F. Verification criteria

- Median CALL_A prompt size in [6000, 9000] bytes (was 12000-14000).
- Claude responses succeed: no JSON parse errors, no schema regressions.
- Top trade picks Claude makes appear in the packages list.
- Every CoinPackage field appears in at least one prompt output.
- CALL_B still functions for open-position management.

## G. Open question (escalation point)

**The full 19-section enumeration is deferred to Phase 7's investigation step**. The Phase 0 verification gate question #4 ("EXACT 19 sections of CALL_A and where each section's data comes from") is partially answered above — the per-coin vs global distinction is established; the exhaustive section enumeration with line ranges happens at the start of Phase 7 to keep Phase 0 fast and the section list aligned with whatever the prompt actually contains at Phase 7 deploy time (which is not necessarily the same as today after Phases 2/3/5/6 have shipped).
