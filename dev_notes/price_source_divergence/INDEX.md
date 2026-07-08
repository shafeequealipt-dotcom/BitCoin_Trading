# Price-Source Divergence Forensic Bundle — INDEX

**Collection timestamp:** 2026-05-02 11:30:27 UTC
**Collector:** Claude Code CLI (single-pass, auto-mode)
**Project paths:**
- Main: `/home/inshadaliqbal786/trading-intelligence-mcp`
- Shadow: `/home/inshadaliqbal786/shadow`
**Live state at capture:**
- Main `workers.py` PID 398 — running
- Shadow `shadow.py` PID 390 — running, API at `http://127.0.0.1:9090`
- Shadow `/api/health`: `running`, 50 coins tracked, 50,886 WS msgs total
- Open positions in Shadow: **ZERO** (`/api/positions` → `{"positions": []}`)
- Most recent closed trade: `ONDOUSDT` at `2026-05-02T06:29:09` UTC (~5 h before capture)

> **Pre-condition gap (Hard Rule 5):** Module S target — at least one open
> position — is NOT met at capture time. S1/S2 are reconstructive: they
> reference the most recent closed trades for cross-source comparison and
> document the live capture surface that would be exercised on a live
> position.  All read/write path tracing in P / Q / R / U / V is complete
> regardless.

## Files in this bundle

| File | Module | Status |
|---|---|---|
| `INDEX.md` | — | this file |
| `P1_price_worker.md` | Main project — PriceWorker | complete |
| `P2_main_project_consumers.md` | Main project — every reader of price | complete |
| `P3_klines_vs_tickers.md` | Klines vs tickers (timeframe distinction) | partial — see notes |
| `Q1_shadow_architecture.md` | Shadow process / dirs / endpoints | complete |
| `Q2_shadow_price_feed.md` | Shadow's price-feed origin (a/b/c question) | complete — answer = **(a)** |
| `Q3_shadow_endpoints.md` | Shadow API endpoints with sample payloads | complete |
| `R1_telegram_handlers.md` | Telegram bot handlers for `/positions`, `/performance` | complete |
| `R2_telegram_price_source.md` | Definitive answer: where Telegram reads P&L from | complete |
| `S1_live_divergence.md` | Single-instant cross-source capture | reconstructive (no open pos) |
| `S2_temporal_divergence.md` | Repeated capture | reconstructive (no open pos) |
| `T1_closed_trade_forensics.md` | Cross-source comparison for 5 closed trades | complete |
| `U1_ipc.md` | Cross-process IPC main↔Shadow | complete |
| `U2_shared_storage.md` | Shared databases / files / env | complete |
| `V1_price_source_matrix.md` | Per-component → price source matrix | complete |
| `W1_e2e_trace.md` | End-to-end timeline of a single WS tick | complete |
| `W2_anomalies.md` | Anomaly catalog (the bug list) | complete |

## TL;DR — the headline divergence

The system has **TWO independent Bybit WebSocket connections** running in two separate processes:

1. **Main project's `PriceWorker`** (PID 398) — uses `pybit.unified_trading.WebSocket`, populates in-memory `self._ws_quotes` dict, also tries to mirror to `ticker_cache` SQLite table via `market_repo.save_ticker(...)`.
2. **Shadow's `WebSocketManager`** (PID 390) — uses raw `websockets` library, populates in-memory `self._latest_tickers` dict, also writes periodic snapshots to `ticker_snapshots` table in `shadow.db`.

The dashboard's `/positions` and `/performance` numbers are produced by a multi-stage pipeline that re-reads Shadow's response and **OVERWRITES** Shadow's `mark_price` and **RECOMPUTES** `unrealized_pnl` from main project's `ticker_cache` SQLite table. That table is silently 5+ hours stale because the PriceWorker WS callback's `loop.create_task(...)` write path fails inside the pybit thread-pool callback (no running event loop in that thread → swallowed `RuntimeError`).

See `W2_anomalies.md` for the full list. The headline is captured in:
- `R2_telegram_price_source.md` (where Telegram reads from)
- `T1_closed_trade_forensics.md` (concrete numeric divergences for 7 closed trades)
- `W2_anomalies.md` (root-cause catalog)
