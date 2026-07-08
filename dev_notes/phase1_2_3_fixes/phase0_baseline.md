# Phase 0 — Pre-Flight Baseline

Captured at the start of the three-phase Telegram-stuck fix series. Establishes the empirical ground truth against which every subsequent fix will be measured.

## 1. Repository State

- **Project root:** `/home/inshadaliqbal786/trading-intelligence-mcp/`
- **Current branch:** `audit/all-tier2-combined`
- **Working tree:** only `data/layer_state.json` and `data/logs/layer1c_full.jsonl` modified (runtime state, never tracked changes to source files).

## 2. Prior Fixes — Verified Shipped on Current Branch

| Fix | Commit(s) | Evidence |
|-----|-----------|----------|
| B1a regime detector | `dea18d8` + `6938c69` (docs) | `src/config/settings.py:1267,1269,1271` thresholds 20/50/12 |
| T1-1 MAE during AGE_GUARD | `c6e2240` | merged on branch |
| T1-2 MAE HWM across state recreation | `0093664` | merged on branch |
| T1-3 sniper trail floor | `169393a` | merged on branch |
| T1-4 incremental_vacuum code | `03ff7c5`, `617e3b2`, `8ba873b` | `cleanup_worker.py:208–251` wired; migration script present |
| T1-4 qty quantization | `bb0d74e` | merged on branch |
| T2-1 prewarm pool | `5b0d78a`, `6204720` | `claude_code_client.py:158–296` |
| T2-6 sniper rate-limit-aware | `9202289` | `profit_sniper.py:1794–1800` short-circuit |
| T2-8 pnl source resolution | `f9375b3` | merged on branch |
| T2-10 sniper trail HWM | `9a17eff` | `profit_sniper.py:1495–1523` |

All claimed prior fixes are present in code. None require re-implementation.

## 3. The 30-Minute Pattern — Empirically Confirmed

Source: `/home/inshadaliqbal786/ALL_LOGS_2026-05-11_17-47_to_22-47.log` (10.6 MB, 48 889 lines, covering 5 h).

### CALL_A cadence

`STRAT_CALL_A_START` timestamps:

```
17:53, 18:03, 18:11, 18:19, 18:27, 18:37, 18:46, 18:56,
19:05, 19:15, 19:23, 19:37,
[GAP 152.5 min]
22:10, 22:15, 22:20, 22:29, 22:36
```

**One CALL_A gap > 15 min in the 5-hour window: 9152 seconds (152.5 min) from 19:37:36 → 22:10:08.** Exactly matches the operator's observation. Pre-gap cadence: 5–10 min. Post-gap cadence: 5–10 min.

### Stalls bracketing the gap

`CLAUDE_PROC_STALL_240S` (ERROR-level): **2 occurrences**, both inside the gap:

- 2026-05-11 19:27:18.861
- 2026-05-11 19:41:37.163

This is the smoking gun: the freeze begins coincident with 240-second subprocess stalls in the Claude CLI invocation path.

## 4. Signature-Event Counts (5-Hour Window)

| Signature | Count | Notes |
|-----------|-------|-------|
| `STRAT_CALL_A_START` | 17 | normal cadence around the 152-min gap |
| `CLAUDE_PROC_STALL` (any severity) | 44 | many stalls below 240 s |
| `CLAUDE_PROC_STALL_240S` (ERROR) | 2 | the gap-trigger events |
| `CLEANUP_LARGE_BATCH` | 5 | hourly cleanup pending > 1000 rows |
| `DB_AUTO_VACUUM_NOT_INCREMENTAL` | 2 | warning fires on every DB reconnect |
| `DB_LOCK_WAIT` | 23 | mostly 1–3 s |
| `STRAT_PREFETCH_CRITICAL` | **0** | bug not firing in this window |
| `BYBIT_DEMO_TIMESTAMP_FAIL` | **0** | bug not firing in this window |
| `ALERT_FAIL` | 4 | Telegram delivery failures |
| Telegram-related lines (`grep -ic telegram`) | 111 | volume around the failures |
| `fetch_all:SELECT * FROM price_alerts` (as holder) | 1 of 23 DB_LOCK_WAIT | actual holders dominated by `fetch_all:SELECT symbol, direction, actual_pnl_pct, close_*` and `fetch_all:SELECT * FROM scheduled_reports WHERE enabled =` |
| `SL_GATEWAY_REJECT` | 36 | distribution below |

### SL_GATEWAY_REJECT by source

```
26 src=profit_sniper_trail
 7 src=sentinel_deadline
 3 src=trail_update
```

(`sentinel_advisor` and `trail_activation` produced 0 in this window but the code-paths lack the T2-6 short-circuit — confirmed during plan-mode investigation.)

Loosening rejections: **1 in 5 h** — `rsn=loosening` count.

## 5. Live Database Diagnostics (P1-1 + P1-2 evidence)

```
file: /home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db
size: 197 480 448 B (≈197 MB)
PRAGMA auto_vacuum  = 0   ← CONFIRMS T1-4 migration script never run on live DB
PRAGMA page_size    = 4096
PRAGMA page_count   = 48 213
PRAGMA freelist_count = 858  (≈3.35 MB reclaimable)
PRAGMA journal_mode = WAL
-wal file: 9 253 552 B (≈8.8 MB)
-shm file: 32 768 B
```

`price_alerts` (P1-2 cited cascade table):

```
columns: id, chat_id, symbol, condition, target_price,
         current_price_at_set, indicator, triggered,
         triggered_at, created_at
indexes:
  idx_price_alerts_symbol
  idx_price_alerts_active          ← covers (triggered, symbol)
  idx_price_alerts_chat
  sqlite_autoindex_price_alerts_1  (PK)
EXPLAIN QUERY PLAN for the cited query:
  SEARCH price_alerts USING INDEX idx_price_alerts_active (triggered=?)
row counts: total=0, untriggered=0
```

**Two consequences:**

1. The cited 53.7 s cascade from this query cannot reproduce in current state — the table is empty and the index would short-circuit the scan even if populated.
2. The actual `DB_LOCK_WAIT` holders in the 5-hour log are different queries (see signature table). P1-2 is correctly deferred; the real lock-cascade culprit needs separate investigation if/when it recurs.

## 6. Services and Runtime

```
trading-workers.service     active running   pid 396  workers.py
trading-mcp-sse.service     active running   pid 397  server.py
trading-backup.service      inactive dead              (daily backup job)
```

Both services launch from `/home/inshadaliqbal786/trading-intelligence-mcp/.venv/`. systemctl operations on these services will require sudo.

## 7. Backups On Disk

```
data/trading_testnet_backup_20260326.db  (18 MB, March 26 — stale, testnet)
```

No fresh production backup exists. The P1-1 migration must take one as the first action.

## 8. CLAUDE.md Project Rules

`/home/inshadaliqbal786/trading-intelligence-mcp/CLAUDE.md` mandates: read every file end-to-end, grep all usages, no band-aid fixes, no assumptions. Aligns 1:1 with the prompt's Hard Rules. Applied throughout this work.

## 9. Active vs Deferred Bug List

Evidence-grounded (see plan):

**Active:**

- **P1-1** — operational migration run (live DB mode=0; code is correct)
- **P2-1** — investigate why pool isn't suppressing the 240 s stalls
- **P2-2** — Telegram detach for INFO-level alerts (7 awaited call sites confirmed)
- **P3-2** — extend T2-6 coordination to remaining 3–4 SL update sources

**Deferred with re-evaluation criteria:**

- **P1-2** — index already present + table empty; revisit if a non-empty cascade reproduces
- **P1-3** — 0 STRAT_PREFETCH_CRITICAL events in 5 h; revisit if P95 > 5 s after Phase 1
- **P3-1** — 0 BYBIT_DEMO_TIMESTAMP_FAIL events in 5 h; revisit on 24-h final test
- **P3-3** — T2-10 + gateway R1 catch this; revisit if rate > 5 / 24 h

## 10. Verification-Gate Anchor Values

These are the before-fix numbers each phase will compare against:

- **CALL_A > 15-min gaps per 5 h:** 1
- **CLAUDE_PROC_STALL_240S per 5 h:** 2
- **CLAUDE_PROC_STALL (any) per 5 h:** 44
- **DB_LOCK_WAIT per 5 h:** 23
- **CLEANUP_LARGE_BATCH per 5 h:** 5
- **SL_GATEWAY_REJECT per 5 h:** 36 (26+7+3)
- **rsn=loosening per 5 h:** 1
- **ALERT_FAIL per 5 h:** 4
- **Live DB `PRAGMA auto_vacuum`:** 0

Targets after all active fixes ship + 24-h soak: CALL_A > 15-min gaps = 0, CLAUDE_PROC_STALL_240S = 0, SL_GATEWAY_REJECT from `trail_update`/`sentinel_deadline`/`sentinel_advisor` → 0, `auto_vacuum` = 2.
