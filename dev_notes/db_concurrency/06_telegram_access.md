# 06 — Telegram Database Access

Target: `src/telegram/` — all handler modules and the bot driver.

## 1. Direct DB access sites

26 direct `db.fetch_*` / `db.execute` call sites across 7 handler files. All other handlers delegate to services.

| File | Sites | Pattern | Notes |
|---|---|---|---|
| `handlers/dashboard_handler.py` | 6 | mixed reads | The main /dashboard endpoint — fetch_one + fetch_all across trade_log, trade_history, trade_thesis, switch_history. Heaviest single Telegram consumer. |
| `handlers/watchlist.py` | 6 | mixed | watchlist persistence (read symbols_json then update) |
| `handlers/system.py` | 3 | read | One fetch_all uses `LIKE '%error%' OR LIKE '%fail%' OR LIKE '%skip%'` on `brain_decisions` — slow query 8 in the audit; full scan that today returns 0 rows |
| `handlers/brain.py` | 3 | read | fetch_all on `brain_decisions` (0 rows), fetch_one on `discovered_patterns` and `generated_strategies` |
| `handlers/apex_handler.py` | 3 | read | apex/flip analytics — fetch_one + fetch_all on trade_thesis and trade_intelligence |
| `handlers/analysis.py` | 3 | read | technical analysis history |
| `handlers/portfolio.py` | 2 | read | portfolio summary |

(Other handlers — alerts, control, emergency, fund, journal, schedule, tias_handler, trading — go through repositories or services, no direct DB.)

## 2. Polling-style consumers

The `telegram_bot_worker` (workers/) and the `price_alert_worker` poll on a fixed cadence:

- `telegram_repo.get_active_alerts()` → `SELECT * FROM price_alerts WHERE triggered = 0` (telegram_repo.py:32). Polled by `price_alert_worker` every 10 s. **Audit's top cascade holder.** Table has 0 rows.
- `telegram_repo.get_active_reports()` → `SELECT * FROM scheduled_reports WHERE enabled = 1` (telegram_repo.py:79). Polled by `scheduled_report_worker` every 300 s. Table has 0 rows.

Why is a 0-row table a cascade holder?

The query itself is sub-millisecond (it acquires the lock, runs an indexed lookup that returns nothing, releases the lock). It does NOT cause cascades on its own. It SHOWS UP in cascade logs because of the way the instrumentation captures the holder: when a slow operation releases the lock and `price_alerts` happens to be the next waiter, it logs its own wait time. The audit's "top holder" ranking by SQL prefix is biased toward frequent acquirers, not toward genuinely slow operations.

The fix in Phase 5 is to stop polling the zero-row table entirely (and resume polling only when a row is ever inserted). This removes the noise from the cascade logs and also removes one source of lock pressure.

## 3. brain_decisions reads (zero-write target)

Two Telegram handlers query `brain_decisions`:

- `handlers/system.py:102` — `LIKE '%error%' OR LIKE '%fail%' OR LIKE '%skip%'` (full scan; returns 0 rows today).
- `handlers/brain.py:33` — `fetch_all` on `brain_decisions`.

Both return zero rows forever because the active strategist writes to `claude_decisions`, not `brain_decisions`. The `brain_decisions` table is a leftover from `brain_v2.py` (legacy path).

Phase 5 candidate: switch these reads to `claude_decisions` OR remove the handlers entirely. The choice depends on whether the operator wants Telegram-visible brain history (in which case it should read claude_decisions) or considers the handler obsolete.

## 4. Concurrency profile

Telegram reads are on-demand — they fire when a user types `/dashboard`, `/positions`, etc. Peak concurrency is bounded by user activity (rarely > 2 simultaneous requests). However, dashboard_handler's 6 sequential reads on /dashboard means a single command produces 6 lock acquisitions in tight succession, all under the same shared lock today.

Under the pooled model, those 6 reads run on independent reader connections (some on the same reader from the pool, others on different readers if available) and do not block worker writes.

## 5. Implications for the refactor

- No Telegram code changes in Phase 3.
- The /dashboard 6-read burst stops blocking workers — Telegram dashboards remain responsive even during kline ticks.
- The 0-row table polls and the brain_decisions reads are Phase 5 cleanup targets:
  - `p5-3` stops the `price_alerts` and `scheduled_reports` polls.
  - `p5-4` redirects or removes the `brain_decisions` Telegram handler reads.

End of `06_telegram_access.md`.
