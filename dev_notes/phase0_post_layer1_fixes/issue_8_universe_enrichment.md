# Issue 8 — active_universe enrichment columns written as zeros

**Status:** PRESENT — Phase-5 rewrite intentionally writes zeros; live state confirmed.
**Tier:** 3 (cosmetic; operator UX).
**Source observation:** `dev_notes/layer1_live_monitor_2026-04-27.md` lines 176-194 (Finding #5); current DB query confirms.

## A. Mechanism

`active_universe` table holds the per-coin selection state visible to Telegram `/status` and any operator-facing surface. Schema includes `volume_24h`, `change_24h_pct`, `funding_rate`, `spread_pct` columns plus `opportunity_score`, `coin_tier`, `updated_at`.

After the Phase-5 ScannerWorker rewrite, the write site at `src/workers/scanner_worker.py:768-797` writes 0.0 for the four enrichment columns with explicit comments:

```python
0.0,  # volume_24h — no longer fetched at scoring time
0.0,  # change_24h_pct — same
0.0,  # funding_rate — read from altdata cache, not stored here
0.0,  # spread_pct — same
```

The comment block (~lines 769-771) documents the intent: "Auxiliary columns... are no longer produced by this scanner; write 0.0 placeholders to keep the schema contract intact."

Live DB state (`SELECT * FROM active_universe LIMIT 5`):
```
BLURUSDT  0.627  0.0  0.0  0.0  0.0  3  2026-04-27 06:49:00
DYDXUSDT  0.606  0.0  0.0  0.0  0.0  3  2026-04-27 06:49:00
BTCUSDT   0.0    0.0  0.0  0.0  0.0  1  2026-04-27 06:49:00
ETHUSDT   0.0    0.0  0.0  0.0  0.0  1  2026-04-27 06:49:00
```

The data IS available within the same scope: `_compute_opportunity_score` (~line 181) reads funding from the altdata cache; `_build_package` (~lines 437-439) reads `change_24h_pct` and `volume_24h` from `market_service.get_ticker_cached`. The post-Phase-5 rewrite simply lost the pass-through to the table write.

## B. Dependencies

- **Readers of `active_universe`:**
  - Telegram `/status` and `/health` handlers (likely in `src/telegram/handlers/`)
  - Any dashboard or external monitoring (none known in repo)
  - `dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md` documents this table as an operator-visible surface
- **Stage 2 / Brain:** reads from `_coin_packages` (Phase-7 redesign), NOT this table. So the brain is unaffected.
- **DDL:** in `src/database/migrations/` — Phase 8 implementation will read it to confirm column types.

## C. Constraints

- Must NOT change DDL — operator surfaces depend on column names.
- Must NOT introduce a new market data fetch in the write path; reuse data already computed in `_compute_opportunity_score` and `_build_package`.
- Funding rate may legitimately be 0 outside settlement windows — preserve the ability to write a real 0.
- If `_compute_opportunity_score` doesn't run for forced-include coins (BTC/ETH), they may have no fresh enrichment; that's acceptable as long as the field is still 0 (matching legitimate absence) but documented as expected.

## D. Fix candidates

1. **Build enrichment dict at write site, pass to INSERT (chosen).**
   - In ScannerWorker.tick, in the same scope where `_compute_opportunity_score` and `_build_package` already populate enrichment, construct a `enrichment = {volume_24h, change_24h_pct, funding_rate, spread_pct}` dict.
   - Pass to the active_universe write helper (replaces the four `0.0` placeholders).
   - For forced-include coins (BTC/ETH) without fresh enrichment, attempt one more pass-through; if absent, write 0.0 (legitimate).
   - Add startup schema validation: `PRAGMA table_info(active_universe)` and assert columns exist.
2. Restore market data fetch at write time. Rejected — duplicates work; introduces network latency.
3. Drop the columns entirely. Rejected — operator surfaces would break.

## E. Observability gap

- No "active_universe write complete with N rows enriched" event today. Hard to tell from logs whether enrichment is populating.
- Schema validation event at startup: `ACTIVE_UNIVERSE_SCHEMA_OK | columns=N` or fail-loud.

## F. Verification approach

- Unit test (write helper): pass synthetic enrichment dict, assert all four columns land non-zero.
- Live trial: post one cycle → `SELECT * FROM active_universe LIMIT 5` shows `volume_24h, change_24h_pct, funding_rate, spread_pct` non-zero (or legitimately zero in funding's case during off-settlement windows).
- Telegram `/status`: shows real values per coin.
- Schema-drift test: alter the test DB to drop a column, run startup → fail-loud with clear error.

## G. Rollback path

Single-file revert of `src/workers/scanner_worker.py`. The four `0.0` placeholders return; existing rows retain their last-written values until next write. No DDL rollback needed.
