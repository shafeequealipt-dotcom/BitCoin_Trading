# Phase 8 — Cycle Code Review (Post-Migration Audit)

**Engagement:** Layer 1 corrected migration.
**Date:** 2026-04-26
**Phase 7 commit:** `d8f6d5b` (preceded this).

## Method

Re-ran the Phase 0 cross-cutting greps against the post-migration tree. Classified every `get_active_universe()` call site and every `watch_list` reader.

## `get_active_universe()` call sites — post-migration

| File | Line | Class | Notes |
|---|---|---|---|
| `src/brain/strategist.py` | 592 | **Cycle** ✓ | `_build_context_prompt` — reads the 30-coin focus to render Claude's prompt. **Correct under corrected arch.** |
| `src/brain/strategist.py` | 1250 | **Cycle** ✓ | `_build_trade_prompt` — same purpose, Call A path. **Correct under corrected arch.** |
| `src/workers/manager.py` | 532 | **Init-time** ✓ | One-shot startup log line: `Initial scan: {n} coins in universe`. Benign — keeps existing behavior of seeding `_active_universe` so first-cycle has a non-empty list before ScannerWorker's first sweet-spot fire (~4 min). |
| `src/strategies/scanner.py` | 445 | **API** | `get_active_universe()` accessor itself. Reads `_active_universe`, falls back to `_cache` (legacy testnet path) if cold. Correct. |
| `src/workers/scanner_worker.py` | 11, 218, 283 | **Doc** | Docstrings referring to the API contract. Documentation, not behavior. |

**No worker-side reads of `get_active_universe()` remain.** All 7 data workers now read `settings.universe.watch_list` directly.

## `watch_list` readers — post-migration

| File | Reader | Phase introduced |
|---|---|---|
| `src/config/settings.py` | UniverseSettings dataclass + builder | (existing) |
| `src/workers/manager.py:897` | MarketScanner construction (input bound) | (existing) |
| `src/workers/kline_worker.py` | tick() + init seed | Phase 2 |
| `src/workers/structure_worker.py` | `_get_universe()` + docstrings | Phase 3 |
| `src/workers/signal_worker.py` | tick() | Phase 4a |
| `src/workers/regime_worker.py` | tick() + restore filter | Phase 4b |
| `src/workers/strategy_worker.py` | tick() | Phase 4c |
| `src/workers/altdata_worker.py` | tick() + init seed | Phase 5a |
| `src/workers/price_worker.py` | tick() | Phase 5b |
| `src/workers/scanner_worker.py` | tick() (input pool for scoring) | Phase 6 |

**Every data worker + ScannerWorker now reads from watch_list.** Cycle code (strategist) reads from `active_universe` via `scanner.get_active_universe()`. Layers cleanly separated.

## MCP / Telegram / Factory / Fund_manager

`rg -n 'active_universe|watch_list' src/mcp/ src/telegram/ src/factory/ src/fund_manager/` returned **zero results**. All of these layers remain universe-agnostic, as the Phase 0 investigation predicted.

## Cycle pipeline integrity

**Stage 2 → Claude → APEX → Gate → Execute:**
- Stage 2 (strategist): reads `active_universe` for 30 coins, builds prompt — unchanged path.
- Claude: receives the same prompt format — unchanged.
- APEX: reads per-coin TA, ws_quote, structural data — unchanged.
- Gate: validates trades — unchanged.
- Execute: places orders — unchanged.

**No Phase 8 code changes needed.** Phase 0 had already verified the cycle was layer-clean; the post-migration grep confirms it remains so.

## Verification (Trial 8.x)

- **Trial 8.1 (Stage 2 builds Claude's prompt with 30 coins):** Strategist reads `await scanner.get_active_universe()` which now returns the new ScannerWorker's selection (top-30 by composite opportunity score, plus open-position coins force-included). Verified by code path inspection.
- **Trial 8.2 (each of those 30 has fresh data):** All 7 workers now maintain caches for ALL 50 watch_list coins. The 30 selected are guaranteed to have fresh structure/signal/regime/strategy outputs because ScannerWorker fires AFTER all 7 data workers' sweet spots (4:00 vs. last data worker at 1:30; 2.5 minutes of buffer). Verified architecturally; concrete drift measurements deferred to Phase 9.
- **Trial 8.3 (no silent missing-data failures):** ScannerWorker's defensive accessor lookups return `None` when a worker is missing or hasn't ticked yet; the composite score falls to 0 for that component. Coins that are warm-cached produce real scores; cold-cached coins simply rank lower. **No exception path crashes the cycle.**
- **Trial 8.4 (telegram bot correct):** Zero references to `active_universe` or `watch_list` in `src/telegram/` — telegram handlers query position_service / market_service / thesis_manager directly. They display whatever the trading system is doing without requiring universe knowledge.

## Hard rule check (cumulative, post Phase 0–7)

- HR-1 (workers on watch_list): YES, verified by grep.
- HR-2 (no inter-worker sync): YES — rotation handlers deleted in Phase 7.
- HR-3 (open positions force-included): YES, scanner_worker.py:230–243.
- HR-4 (sweet-spot chain order): YES, validated at startup by `SweetSpotsSettings.__post_init__`.
- HR-5 (watch_list as truth): YES.
- HR-6 (per-phase commits): 8 phases × 1+ commits each, all atomic with rollback paths.

## Next phase

Phase 9 — live observation period. The migration code is complete; Phase 9 verifies behavior under sustained load.
