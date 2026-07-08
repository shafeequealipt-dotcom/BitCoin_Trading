# Phase 0 Precondition Check — Price-Source Divergence Fix

**Date:** 2026-05-03
**Operator:** Inshad
**Implementer:** Claude Code CLI
**Working file (not committed per INDEPTH Phase 0 spec).** Captures all pre-conditions, grep surveys, and baseline measurements required by INDEPTH Phase 0 (lines 295-365) before Phase 1 can begin.

---

## 1. Pre-Conditions

### 1.1 Pre-condition 1 — working tree clean

**Status:** NOT clean, but acceptable with mitigation.

`git status` shows:
- Modified: `data/layer_state.json` (runtime state, written by LayerManager during normal operation).
- Modified: `trading.db` (runtime state — note this is the one at project root, NOT `data/trading.db` which is gitignored).
- Untracked: `STAGE2_LAYER3_FORENSIC_BUNDLE_2026-05-02.md`, several `.bak` files (database snapshots), `dev_notes/IMPLEMENT_PRICE_SOURCE_DIVERGENCE_FIX_PROFESSIONAL.md` (earlier-session draft), `dev_notes/forensic_data_*` directories.

**Mitigation:** runtime state files (layer_state.json, trading.db) cannot be safely stashed because the running services have them open. Strategy: every phase commit will use **targeted `git add <specific-file>`** so runtime modifications never enter a phase commit.

The Pre-Phase A commit (b7331fc) followed this rule successfully — only `dev_notes/PROJECT_CONTEXT_2026-05-03.md` was added.

### 1.2 Pre-condition 2 — branch appropriate

**Status:** PASS. Current branch `main`, 27 commits ahead of `origin/main`. Pre-Phase A commit b7331fc is the most recent.

### 1.3 Pre-condition 3 — services running

**Status:** PASS. `systemctl is-active trading-workers trading-mcp-sse shadow` returns `active` for all three.

### 1.4 Pre-condition 4 — both DBs readable

**Status:** PASS.
- `trade_intelligence` row count: 821.
- `virtual_positions` row count: 959.

### 1.5 Pre-condition 5 — forensic bundle exists

**Status:** PASS. `dev_notes/price_source_divergence/FULL_BUNDLE.md` exists, 95217 bytes, dated 2026-05-02 11:43.

### 1.6 Pre-condition 6 — configuration values present

**Status:** PASS.
- `config.toml [price] local_max_age_seconds = 10.0`
- `config.toml [price] divergence_override_pct = 0.5`
- `config.toml [price] divergence_block_prompt_pct = 1.0`
- `shadow/config.toml [exchange] slippage_pct = 0.03`

(Main project also has `slippage_pct = 0.02` somewhere — that's a different concept, order-time slippage allowance, not Shadow's fill slippage. Verify if relevant.)

---

## 2. Grep Surveys

### 2.1 Grep 1 — `ticker_cache` consumers

Writers:
- `src/database/repositories/market_repo.py:268` — `INSERT OR REPLACE INTO ticker_cache` in `save_ticker()`.
- The PriceWorker WS callback at `src/workers/price_worker.py:218` was supposed to write but is currently broken (Bug 1).
- `src/trading/services/market_service.py:101` (REST path `_fetch_ticker`) writes via `market_repo.save_ticker`. This is the only path actually populating ticker_cache today.

Readers (the table — not the in-memory `_ticker_cache` dict on MarketService):
- `src/database/repositories/market_repo.py:295` — `SELECT * FROM ticker_cache WHERE symbol = ?` in `get_ticker()`.
- `src/core/transformer.py:667` — `SELECT last_price, updated_at FROM ticker_cache WHERE symbol = ?` in `_get_local_price`. (Phase 2 affects.)
- `src/intelligence/sentiment/aggregator.py:169` — `SELECT change_24h_pct FROM ticker_cache WHERE symbol = ?` in the momentum overlay. **Operator-confirmed: stays on ticker_cache (no migration to shadow).** Phase 3's proper WS-write fix keeps this table fresh.

Tests:
- `tests/test_protected_tables.py:99` — `DELETE FROM ticker_cache` (test cleanup).
- `tests/test_phase2/test_market_service.py:28` — reads ticker_cache to verify market_service's write path.
- `tests/test_sentiment_aggregator_tags.py:47` — DatabaseManager fixture for ticker_cache lookup.

False positives (these reference the in-memory `_ticker_cache` dict on MarketService, NOT the SQLite table):
- `src/core/freshness_guard.py:35-36`
- `src/trading/services/market_service.py:45, 60-68, 119, 151`
- `src/workers/scanner_worker.py:703-704` (calls `market.get_ticker_cached` — uses the in-memory cache).
- `src/workers/scanner/state_labeler.py:21`
- `src/workers/profit_sniper.py:94` (docstring reference).

### 2.2 Grep 2 — `_enrich_*_with_local_prices` callers

- `src/core/transformer.py:985` — `_PositionProxy.get_positions` calls `_enrich_positions_with_local_prices`.
- `src/core/transformer.py:991` — `_PositionProxy.get_position` calls `_enrich_positions_with_local_prices`.
- `src/core/transformer.py:1042` — `_AccountProxy.get_wallet_balance` calls `_enrich_balance_with_local_prices`.

No external callers. Phase 2's rename of these methods is contained.

### 2.3 Grep 3 — `_last_enrichment_max_divergence_pct` readers

Writers:
- `src/core/transformer.py:56` — initialized to 0.0 in `__init__`.
- `src/core/transformer.py:746` — reset to 0.0 at top of `_enrich_positions_with_local_prices`.
- `src/core/transformer.py:763-764` — running-max update inside the per-position loop.

Readers (must not break in Phase 2):
- `src/brain/strategist.py:292` — `_has_blocking_price_divergence` uses `getattr(tf, "_last_enrichment_max_divergence_pct", 0.0)`.
- `src/brain/strategist.py:509` — PROMPT_DEFERRED log emission reads the field.
- `tests/overhaul29_pipeline_test.py:217, 231` — tests set `_last_enrichment_max_divergence_pct` to specific values (1.2, 0.5) to exercise the gate. These tests must continue to pass after Phase 2.
- `tests/overhaul29_integration_test.py:176-177` — verifies the field initializes to 0.0.

### 2.4 Grep 4 — `coordinator.on_trade_closed` self-close sites

**Critical finding: 11 self-close sites total, not the 4 the forensic identified.** All currently pass `pos.unrealized_pnl` (the Transformer-overwritten value) as `pnl_usd`. All need the Phase 1 fix.

In `src/workers/position_watchdog.py`:
1. **Line 996** — `closed_by="time_decay_p_win_low"` — force-close from time-decay state machine.
2. **Line 1104** — `closed_by=f"sentinel_deadline_{tier}"` — sentinel deadline tier close.
3. **Line 1135** — `closed_by="plan_timer"` — plan timer expired.
4. **Line 1193** — `closed_by="trailing_stop"` — trailing stop hit.
5. **Line 1280** — `closed_by="early_exit"` — early exit on losing position.
6. **Line 1318** — `closed_by="hard_stop"` — hard stop, -3% limit.
7. **Line 1387** — `closed_by="timeout"` — time-used timeout.
8. **Line 1417** — `closed_by="profit_take"` — profit taken at time threshold.
9. **Line 2015** — `closed_by="watchdog"` — `_execute_full_close` (main watchdog action dispatch).

In `src/workers/profit_sniper.py`:
10. **Line 2407** — `closed_by=closed_by` (dynamic — mode4_p9, mode4 spike, anti_greed) — `_execute_full_close`.
11. **Line 2490** — `closed_by="mode4_partial_fallback_full"` — partial close degraded to full.

Existing external-detection path (already correct, do NOT touch):
- `src/workers/position_watchdog.py:2602` — `_detect_and_record_closes` flow. The Path A fix at lines 2569-2578 already prefers Shadow's `net_pnl_pct` / `net_pnl_usd` from `get_last_close`. This is the reference pattern Phase 1 extends to the 11 self-close sites.

Other `coordinator.on_trade_closed` references (NOT self-close — these are the coordinator's own `def on_trade_closed`):
- `src/core/trade_coordinator.py:405` — the method definition itself.
- `src/core/thesis_manager.py:198` (docstring).
- `src/tias/collector.py:493` (docstring).
- `src/workers/position_watchdog.py:392, 2455` (docstrings/comments).
- `src/trading/services/position_service.py:215` — REAL Bybit live-mode path. In live mode, `get_last_close` returns None (Bybit `PositionService` doesn't implement it per `transformer.py:1027`). Phase 1's helper must handle this gracefully — fall back to local computation. Live mode actually has a different root: Bybit's order response is authoritative and main project records the real fill price; the slippage gap doesn't exist because there's no simulation.

Other `*.on_trade_closed` references (different methods on different classes — NOT subject to Bug 3):
- `src/strategies/pnl_manager.py:359` — `PnLManager.on_trade_closed(pnl, symbol)`.
- `src/strategies/performance_enforcer.py:514` — `performance_enforcer.on_trade_closed(pnl_pct, was_win)`.
- `src/risk/risk_manager.py:214` — `RiskManager.on_trade_closed(pnl)`.
- `src/fund_manager/manager.py:465` — `FundManager.on_trade_closed(...)`.
- `src/workers/manager.py:1411, 1431, 1479, 1485` — WorkerManager fan-out to enforcer/fund_mgr/pnl_mgr after the coordinator's callback fires. These are downstream consumers that already receive the corrected `pnl_usd` after the coordinator's fix.

### 2.5 Grep 5 — `PRICE_OVERRIDE` log tag consumers

All references are internal to the codebase — no external dashboards/alerts grep for this tag.
- `src/config/settings.py:1796` (docstring).
- `src/core/transformer.py:774, 781, 788` (emit + event-buffer write + error log).

Phase 2 rename to `PRICE_DIVERGENCE_OBS` is safe — no lockstep external updates required. Operator confirmed they don't have external monitoring dashboards keying on this tag.

### 2.6 Grep 6 — `get_last_close` callers

- Implementation: `src/shadow/shadow_adapter.py:192-225` (`ShadowPositionService.get_last_close(symbol)` → GET `/api/position/{symbol}/last_close`, returns dict with `exit_price`, `net_pnl_pct`, `net_pnl_usd`, `close_trigger`, `closed_at`, `hold_duration_seconds` or None).
- Proxy: `src/core/transformer.py:1020-1030` (`_PositionProxy.get_last_close` — returns `None` when active service has no such method, i.e., Bybit live mode).
- Existing caller (the model): `src/workers/position_watchdog.py:2476-2478` — `_detect_and_record_closes` calls `await self.position_service.get_last_close(symbol)` to fetch authoritative close data.

The pattern at `position_watchdog.py:2569-2578` (the existing fix, KEEP intact):

```python
# Bug 2 fix: when Shadow returned usable close data, prefer its
# fee-inclusive net_pnl_pct / net_pnl_usd over our locally
# back-derived values. Shadow already accounts for entry/exit
# fees, slippage, and funding — we don't.
if price_source == "shadow_authoritative" and shadow_close:
    try:
        _s_pct = shadow_close.get("net_pnl_pct")
        _s_usd = shadow_close.get("net_pnl_usd")
        if _s_pct is not None:
            pnl_pct = float(_s_pct)
        if _s_usd is not None:
            pnl_usd = float(_s_usd)
    except (TypeError, ValueError):
        pass
```

Phase 1 extends this pattern to the 11 self-close sites via a coordinator-level helper.

---

## 3. Baseline Measurements

### 3.1 Baseline 1 — dashboard divergence snapshot

**NOT capturable live: zero open positions.** Both main `positions` table and Shadow `virtual_positions WHERE status='open'` return zero rows. Shadow's `/api/positions` returns `{"positions": []}`.

This is consistent with the forensic capture (2026-05-02 11:30 UTC, also no open positions). The Phase 6 verification report will capture this baseline live when a position is open after the fix ships.

### 3.2 Baseline 2 — recent closed-trade P&L divergence

**Strong forensic confirmation.** Last 10 closed trades, joining manually by `(symbol, trade_closed_at within ±90s)`:

| # | Symbol | Side | Main pnl_usd | Shadow net_pnl_usd | Δ (Main − Shadow) | closed_by (main) |
|---|---|---|---|---|---|---|
| 1 | ONDOUSDT | Buy | -0.288 | -0.523 | **+0.235** | time_decay_p_win_low |
| 2 | MANAUSDT | Buy | -0.145 | -0.380 | **+0.235** | time_decay_p_win_low |
| 3 | AXSUSDT | Buy | -0.063 | -0.278 | **+0.215** | mode4_p9 |
| 4 | DOGEUSDT | Sell | -0.601 | -0.601 | 0.000 | strategic_review |
| 5 | AXSUSDT | Buy | -0.269 | -0.426 | **+0.157** | mode4_p9 |
| 6 | DOGEUSDT | Sell | -0.126 | -0.345 | **+0.219** | time_decay_p_win_low |
| 7 | RENDERUSDT | Buy | -0.052 | -0.052 | 0.000 | strategic_review |
| 8 | SANDUSDT | Sell | -1.452 | -1.452 | 0.000 | shadow_sl_tp |
| 9 | AXSUSDT | Buy | -0.345 | -0.580 | **+0.235** | mode4_p9 |
| 10 | HYPEUSDT | Buy | -0.016 | (not joined; possibly older) | n/a | time_decay_p_win_low |

**Summary:** rows closed via `time_decay_p_win_low` (4 of the visible 9) and `mode4_p9` (3 of 9) all show ~$0.16-$0.24 divergence. Rows closed via `strategic_review` or `shadow_sl_tp` (3 of 9) match Shadow exactly. **Pattern matches the T1 forensic exactly, with no drift between the 2026-05-02 forensic capture and 2026-05-03.**

Sum of |Δ| on the 9 joined rows: **$1.30** of cumulative bias on these 9 trades. Extrapolated across the 821 lifetime `trade_intelligence` rows (with similar trigger distribution): potentially $50–$200+ cumulative bias in lifetime aggregations.

### 3.3 Baseline 3 — `ticker_cache` freshness

**Bug 1 fully confirmed.** `ticker_cache` has 205 total rows. **Zero rows are fresh (age < 60 seconds).** Oldest rows are over 3 million seconds old (~37 days). Top 20 ages range from 2.3M to 3.2M seconds — written by historical REST calls, never updated by WS.

### 3.4 Baseline 4 — strategist PROMPT_DEFERRED rate

**Zero in current logs.** Counts across recent log rotations:
- `workers.log` (current, since ~2026-05-03 04:31 UTC): 0
- `workers.2026-05-02_04-31-00_392071.log`: 0
- `workers.2026-05-01_00-01-33_829054.log`: 0
- `workers.2026-04-29_06-02-12_804938.log`: 0
- All other rotations: 0

The strategist's PROMPT_DEFERRED gate has not fired in any visible log. Reason: the chain is `_enrich_*` → `_get_local_price` returns None for stale rows (10s freshness gate) → divergence calculation never runs → `_last_enrichment_max_divergence_pct` stays at 0.0 → gate never fires. Phase 2's preservation of the field-update logic must keep this baseline (gate continues to stay at 0% deferral until ticker_cache becomes fresh after Phase 3, at which point divergence can be observed).

After Phase 3 ships and ticker_cache becomes fresh, Phase 2's observation-only logic will start updating the field with real divergence values, and the gate may begin firing if real Bybit-vs-Shadow drift exceeds 1.0%. Phase 6 verification confirms the rate stays in baseline ballpark (expected to remain near 0 because the two WSs typically agree within 0.1-0.3%).

### 3.5 Baseline 5 — `PRICE_OVERRIDE` event frequency

**Zero across all visible logs.** Counts:
- `workers.log` (current): 0
- All rotations checked: 0

The override has not fired. Reason: ticker_cache is too stale (Bug 1) for `_get_local_price` to return a non-None value, so the override branch is never reached. Phase 2's rename to `PRICE_DIVERGENCE_OBS` will produce the same 0 baseline initially, then fire whenever real divergence is observed after Phase 3 ticker_cache becomes fresh.

`PRICE_STALE` (the warning emitted at `transformer.py:702-705` when `_get_local_price` finds a stale row) appears at 17-50 occurrences per rotation — confirms the freshness gate is firing as expected.

`WD_CLOSE` log lines all show `price_src=shadow_authoritative` (the existing Path A fix is functioning correctly for external-detected closes).

---

## 4. Phase 0 Summary And Gate Status

All six pre-conditions: PASS (with documented mitigation for working-tree dirtiness — runtime files excluded via targeted `git add` per phase).

All six grep surveys: COMPLETE. **Critical finding:** 11 self-close sites need the Phase 1 fix, not 4 as the forensic identified. All documented above.

All five baseline measurements: CAPTURED (Baseline 1 reconstructive due to no open positions — same caveat as forensic S1/S2).

**Phase 0 gate status: PASS.** Ready to proceed to Phase 1.

---

## 5. Notes for Phase 1

- Helper signature: `_resolve_authoritative_pnl(self, symbol, fallback_pnl_usd, fallback_pnl_pct) -> tuple[float, float, str]` returning `(pnl_usd, pnl_pct, price_source)` with `price_source` ∈ {"shadow_authoritative", "local_fallback"}.
- Helper home: `src/core/trade_coordinator.py` as a method on `TradeCoordinator` (so all 11 self-close sites can call it via the coordinator they already have).
- Mode awareness: when `_PositionProxy.get_last_close` returns None (Bybit live mode), helper returns `(fallback_pnl_usd, fallback_pnl_pct, "local_fallback")` and emits a `WD_LAST_CLOSE_FALLBACK | reason=bybit_mode_no_op` log at INFO (not WARNING — this is expected in live mode, not an error).
- Race window: when `get_last_close` returns None or empty in shadow mode, log `WD_LAST_CLOSE_FALLBACK | reason=shadow_race_window` at WARNING and use local fallback. The race is real per INDEPTH lines 213-214 — Shadow may not have persisted the close record by the time the closing code calls `get_last_close` immediately after.
- Tests: extend `tests/test_watchdog/test_position_watchdog.py` and `tests/test_profit_sniper_*.py` (and possibly add `tests/test_trade_coordinator_authoritative_pnl.py`) with at least 3 cases per close site shape: shadow-authoritative success, shadow-race-window fallback, bybit-mode no-op fallback.
- Phase 1 commit message (per INDEPTH): `fix(price-source/phase-1): use Shadow's net_pnl_usd for time_decay and mode4 closes` — but expand the body to mention all 11 sites, not just time_decay/mode4 as INDEPTH's commit subject implies.
