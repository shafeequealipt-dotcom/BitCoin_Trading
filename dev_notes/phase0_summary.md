# Phase 0 — Cross-Issue Summary

**Date:** 2026-04-27
**Brief reference:** `IMPLEMENT_FIVE_CRITICAL_FIXES_PROFESSIONAL.md`
**Plan:** `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-recursive-parasol.md`

## Verified citations (line numbers as of 2026-04-27)

| Item | File | Line | Status |
|---|---|---|---|
| `DatabaseManager._locked()` | `src/database/connection.py` | 116-160 | verified |
| `DatabaseManager.executemany` | `src/database/connection.py` | 229-251 | verified |
| `MarketRepository.save_klines` | `src/database/repositories/market_repo.py` | 43-95 (executemany at :80) | verified |
| `MarketService.get_klines` save site | `src/trading/services/market_service.py` | 210 | verified |
| `KlineWorker.tick` save site | `src/workers/kline_worker.py` | 181 | verified |
| `LayerManager` (canonical) | `src/core/layer_manager.py` | 25-40 init; 317/351/410 gates | verified |
| `src/workers/layer_manager.py` | — | — | **ORPHAN — 0 imports** |
| ServiceContainer registration | `src/workers/manager.py` | 506-507 | verified |
| `OrderService.place_order` | `src/trading/services/order_service.py` | 86-297 | verified — no L3 gate |
| `PositionService.close_position` | `src/trading/services/position_service.py` | 130-160, 259+ | verified — bypasses OrderService, calls Bybit directly |
| `OrderService` callers | brain_v2:487, strategy_worker:1094, telegram bot:689, telegram trading:88, mcp trading_tools:166-197, transformer:945-958 | various | verified |
| `ProfitSniper` cooldown bug site | `src/workers/profit_sniper.py` | 1654-1667 | verified |
| `ProfitSniper` PROFIT GATE for full only | `src/workers/profit_sniper.py` | 1615-1622 | verified |
| `claude_code_client._ensure_credentials_fresh` | `src/brain/claude_code_client.py` | 566-611 | verified (current 1233 LOC) |
| `claude_code_client._try_token_refresh` | `src/brain/claude_code_client.py` | 612-693 | verified |
| Subprocess thread executor | `src/brain/claude_code_client.py` | 815 | verified |
| Stall watcher constant | `src/brain/claude_code_client.py` | 821 | verified |
| `MarketScanner._update_universe` | `src/strategies/scanner.py` | 65-183 (cooldown at :131) | verified |
| `ScannerWorker` tick summary | `src/workers/scanner_worker.py` | 329 | verified |

## Corrections vs. earlier (plan-mode) findings

1. **`brain_v2.py` is at `src/brain/brain_v2.py:487`**, NOT `src/core/brain_v2.py`. The plan file references `core/` in one place; treat as drift and use the verified path.
2. **Layer 4 closes (sniper, watchdog) bypass OrderService.** They go via `PositionService.close_position → BybitClient.call("place_order")`. So the 18:03 `ORDER_RETRY_EXHAUSTED` events were Layer 3 entries, not Layer 4 closes. The fix at OrderService is correct; we additionally annotate `POS_CLOSE_START` with a `purpose=` field for full audit symmetry.
3. **`src/workers/layer_manager.py` is genuinely orphaned** — zero imports. Phase 2a is a clean delete, not a careful merge. The src/core/ version is 9 KB larger than the orphan and contains BRAIN_HEALTH cycle-time tracking and richer observability — confirming src/core/ is the actively-maintained one.
4. **TransformerProxy** at `src/core/transformer.py:945-958` is an additional indirection that proxies `OrderService.place_order` when the transformer is active. Phase 2 must thread the new `purpose=` kwarg through this proxy.

## Cross-issue dependencies

| Phase | Depends on | Why |
|---|---|---|
| 1 (D-3) | — | Independent prerequisite for cleaner verification of 2-5 |
| 2a | Phase 1 not strictly required, but lands first to keep commit history clean | Sensitive cross-cutting refactor |
| 2 | Phase 2a | Phase 2 modifies LayerManager-related code; must be done on the canonical file only |
| 3 | None | Independent of others |
| 4 | None | Independent of others |
| 5 | Phase 1 (D-3) | D-3-induced staleness contributes to score volatility; with D-3 fixed, score volatility drops naturally and hysteresis tuning becomes more reliable |

Phase order remains: 0 → 1 → 2a → 2 → 3 → 4 → 5. Phase 6 (24-48h observation) is dropped per user.

## Pre-existing uncommitted work in repo

`git status` at investigation time shows ~637 lines across 10 files modified but not committed (including `src/analysis/structure/shadow_kline_reader.py` — the prior phase fix per memory) and several untracked dev_notes from prior phases. **These are not part of this engagement.** This Phase 0 commit and all subsequent Phase 1-5 commits will use explicit `git add <file>` for our changes only; the in-progress prior work stays untouched.

## Phase 0 deliverable status

- `phase0_issue_1_d3_investigation.md` — written
- `phase0_issue_2_sniper_investigation.md` — written
- `phase0_issue_3_layer3_investigation.md` — written
- `phase0_issue_4_credentials_investigation.md` — written
- `phase0_issue_5_flapping_investigation.md` — written
- `phase0_summary.md` — this file

All six files written before any code change.

## Verification gate (per brief)

Concrete answers, one sentence each:

1. **D-3 mechanism:** `MarketRepository.save_klines` issues a single `executemany` (`market_repo.py:80`) under `DatabaseManager._locked()` (`connection.py:116-160`); ~45 such saves per kline_worker tick chain the lock without yielding, while `wal_autocheckpoint=2000` is opportunistic and `cleanup_worker`'s hourly checkpoint is the only force, leaving the WAL pinned at 100 MB.
2. **Sniper 4× pattern:** `profit_sniper.py:1664` only enforces the partial cooldown when the **previous** action was `partial_close`; an alternating tighten ↔ partial sequence sets `_last_action_type` to `tighten` between partials, defeating the gate. Compounded by the absence of any PROFIT GATE on partials (the P9_CLOSE_GATE at `:1615-1622` only catches `full_close`).
3. **Layer 3 leak code path:** `OrderService.place_order` (`order_service.py:86-297`) has zero `is_layer_active(3)` check; the LayerManager-level gates (`layer_manager.py:317/351/410`) only short-circuit Claude directives upstream, so any code path that reaches OrderService directly (brain_v2:487, strategy_worker:1094, etc.) is currently ungated.
4. **Brain hang trigger:** Pre-flight refresh margin is 300 s hardcoded (`claude_code_client.py:243`), and `_try_token_refresh` (`:612-693`) is single-attempt synchronous urllib with a 30 s HTTP timeout and no retry; near a credential boundary, the subprocess inherits the expiring token and the in-process refresh inside the Claude CLI hangs silently for the 300 s subprocess timeout.
5. **Universe flap cause:** `MarketScanner._update_universe` (`scanner.py:65-183`) selects pure top-N with no consecutive-scan hysteresis; marginal coins oscillating around the cutoff (volatility at 5-min cadence) flip in/out every scan. The 300 s re-entry cooldown at line 131 is too short to dampen this rate.

All five mechanisms identified concretely. Phase 0 complete; proceed to Phase 1.
