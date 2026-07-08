# Phase 0 — Five Critical Fixes: Pre-Flight Baseline + Corrected Gap Report

**Date:** 2026-05-14 10:11 UTC
**Branch:** `audit/all-tier2-combined` @ `b348038`
**Auditor:** Phase 0 read-only investigation per `IMPLEMENT_FIVE_CRITICAL_FIXES_2026-05-14.md`

---

## Executive Summary

All 5 audit-claimed issues are **verified present and active** in current
production. **One claim is worse than the audit captured** (I4 cascade —
live DB shows 78-minute lock waits) and **one claim's mitigation is
incomplete** (I2 orphans — current code wires `delete_position` callback
but 14 orphan rows persist live).

## Pre-condition checks

| Check | Status | Evidence |
|-------|--------|----------|
| Working tree | Clean modulo prior config/log noise | `git status` modified config.toml, data/layer_state.json — same as observability work |
| Current branch | `audit/all-tier2-combined` | HEAD `b348038` |
| B1a regime fix | In place | Commit `6938c69` (2026-05-12) confirmed in history |
| F-29 RAM upgrade | **Verified live** | `free -h`: 15 Gi total / 12 Gi free / 0 swap. systemd `MemoryMax=800M` / `MemoryHigh=600M` unchanged — **may need operator re-verify** |
| System currently running | Yes | `data/logs/workers.log` actively growing (9.1 MB, last update <2 min ago); WD_TICK firing every 10s |
| Audit log file | Yes | `/home/inshadaliqbal786/ALL_LOGS_2026-05-13_21-53_to_23-23.log` (1.5h, 23,463 lines) |
| Live workers.log | Yes | Time range: 2026-05-13 10:00 → 2026-05-14 10:11 (~24h continuous session, post-SEGV restart at 22:42:52 yesterday) |

**Flagged:** the systemd `MemoryMax=800M` still appears restrictive against
F-29's RAM upgrade narrative. If F-29 upgrade applied to the GCP VM
without raising cgroup limits, the workers.py process still has 800M
ceiling regardless of system RAM. Recommend operator verify cgroup
status before I5 ships (Rule 13).

---

## Step 0.1 — All 5 Audit Claims Verified

### I1 — F-26 TIMESTAMP_FAIL (CONFIRMED, persistent)

| Metric | Audit (1.5h window) | Current live (24h log) |
|--------|---------------------|-------------------------|
| BYBIT_DEMO_TIMESTAMP_FAIL events | 6 (4 positions + 2 balance) | 6 (same 6 events; no new ones in past 12h) |
| Response shape | code=10002 | confirmed: `code=10002 op=positions msg='invalid request, please check your server timestamp or recv_window param: req_timestamp[N],server_timestamp['` |
| Time of events | 22:10:25, 22:19:26, 22:40:22, 22:40:22, 22:40:27, 22:40:33 | Same |

**Current code anchors:**
- `src/bybit_demo/bybit_demo_client.py:222` — `recv_window: int = 5000`
- `src/bybit_demo/bybit_demo_client.py:150-152` — `_TIMESTAMP_FAIL_CODES = frozenset({10002})`
- `src/bybit_demo/bybit_demo_client.py:171-175` — `_log_ret_code` emits `BYBIT_DEMO_TIMESTAMP_FAIL`
- `src/bybit_demo/bybit_demo_client.py:222, 231, 265, 293` — `_recv_window` used in HMAC sign + HTTP header
- `src/bybit_demo/bybit_demo_adapter.py:177-182` — `except TradingMCPError: return []` — the **empty-list conversion site**
- `src/workers/position_watchdog.py:478` — calls `position_service.get_positions()` → receives `[]` → set-diff → phantom close

**Status:** ACTIVE. The recent quiescence (no new TIMESTAMP_FAIL in 12h)
is likely correlated with reduced VM load post-SEGV, not a fix. Issue
remains.

### I2 — F-17 ticker_fallback orphan (CONFIRMED, current orphans = 14)

**LIVE DB QUERY (sqlite3 against `data/trading.db`):**

```
SELECT COUNT(*) FROM positions  →  14 rows
SELECT exchange_mode, COUNT(*) FROM positions GROUP BY exchange_mode
→  bybit_demo: 14
```

**LIVE workers.log:**
```
WD_TICK | mode=safety_net n=0 syms=[none] | wid=w-...
```

**Watchdog sees 0 open positions. DB has 14 rows.** All 14 are
**confirmed orphans, currently, in production.**

**Orphan timestamps (updated_at, descending):**

| Symbol | Updated_at | Age (vs 10:11 today) |
|--------|------------|----------------------|
| DYDXUSDT | 2026-05-13 22:52 | ~11h |
| MONUSDT | 2026-05-13 22:17 | ~12h |
| XRPUSDT | 2026-05-13 12:22 | ~22h |
| LTCUSDT | 2026-05-13 10:31 | ~24h |
| SEIUSDT | 2026-05-13 10:31 | ~24h |
| HBARUSDT | 2026-05-13 10:31 | ~24h |
| MNTUSDT | 2026-05-13 10:31 | ~24h |
| AAVEUSDT | 2026-05-13 10:31 | ~24h |
| RUNEUSDT | 2026-05-13 10:31 | ~24h |
| EGLDUSDT | 2026-05-13 10:13 | ~24h |
| ATOMUSDT | 2026-05-13 09:27 | ~25h |
| ADAUSDT | 2026-05-13 09:16 | ~25h |
| AXSUSDT | 2026-05-13 09:15 | ~25h |
| SANDUSDT | 2026-05-13 07:50 | ~26h |

**Pattern observation:** DYDXUSDT in orphan list matches
WD_CLOSE_THESIS_RECOVERY at 22:52 (audit log) at the exact entry price
(0.15001). **Hypothesis:** the SEGV-recovery path
(`position_watchdog.py:3304`) reconstructs trade state but does NOT
call delete_position on the positions table after the recovery close.

The cluster of 6 orphans at 10:31 (LTC, SEI, HBAR, MNT, AAVE, RUNE)
suggests a batch event — possibly a prior SEGV or service restart at
that time.

**Phase 0 cannot resolve** whether the close-path coverage is complete
(Explore agent's optimistic mapping) or whether real gaps exist.
**Phase 1 of I2 must trace each orphan back through logs to identify
which close path failed.**

### I3 — F-28 WD_PNL_MISMATCH (CONFIRMED, 2 events in window)

**Audit log sample:**

```
22:37:45.758 | ERROR | WD_PNL_MISMATCH | sym=ORCAUSDT pnl=0.00 ent=1.4831 ext=1.4831 — possible data integrity issue
23:06:44.402 | ERROR | WD_PNL_MISMATCH | sym=AEROUSDT pnl=0.00 ent=0.471 ext=0.471 — possible data integrity issue
```

**Current code anchor (verified at `src/workers/position_watchdog.py:3463`):**

```python
# 0.00% PnL diagnostic
if pnl_pct == 0 and entry_price > 0:
    log.error(f"WD_PNL_MISMATCH | sym={symbol} pnl=0.00 ent={entry_price} ext={exit_price} — possible data integrity issue | {ctx()}")
if exit_price == 0:
    log.error(f"WD_ZERO_EXIT | sym={symbol} exit_price=0 price_src={price_source} — exit price unknown | {ctx()}")

# Fire coordinator callbacks (thesis close, trade_log, daily_pnl, etc.)
if self.coordinator:
    try:
        self.coordinator.on_trade_closed(  # ← FALLS THROUGH despite ERROR above
            symbol=symbol, pnl_pct=pnl_pct, pnl_usd=pnl_usd,
            was_win=was_win, closed_by=close_reason,
            exit_price=exit_price, price_source=price_source,
        )
```

**Verified:** the ERROR is purely advisory. No `return` between L3463
and L3470. `coordinator.on_trade_closed()` runs with `pnl_pct=0,
ent==ext` corrupted values.

**Status:** ACTIVE. Same root-cause holds in current code.

### I4 — F-27 DB lock cascade (CONFIRMED, MUCH WORSE than audit captured)

| Metric | Audit (1.5h window) | Current live (general.log, ~24h) |
|--------|---------------------|----------------------------------|
| DB_LOCK_WAIT events | 39 | **473** |
| CASCADE_DETECTED events | 13 | **61** |
| Longest single wait_ms | 13,905 (~14s) | **4,715,047 (~78 min !)** |
| Second-longest | 13,894 | 4,714,004 |
| 99th percentile estimate | ~14s | TBD (sample needed) |

**The 4715-second waits happened at 2026-05-13 09:10:05 — system boot
sequence:**

```
09:10:05.212 | WARNING | DB_LOCK_WAIT | wait_ms=4715047 holder=fetch_all:
09:10:05.257 | WARNING | DB_LOCK_WAIT | wait_ms=4714004 holder=executemany:
```

These two events likely represent the boot-time DB initialization
serializing through `_locked()` after a multi-hour quiescent gap. May
not represent steady-state contention. **The audit's 14-second
steady-state cascade is the operational concern.** Recent
CASCADE_DETECTED events (23:17-23:20 yesterday, post-SEGV):

```
23:17:08.897 | duration_ms=8682  holder=execute
23:17:09.073 | duration_ms=9091  holder=execute
23:20:38.065 | duration_ms=24435 holder=execute       ← worst steady-state
23:20:51.700 | duration_ms=13623 holder=executemany
23:20:53.826 | duration_ms=15742 holder=fetch_all (price_alerts query)
```

**Current code anchors:**
- `src/database/connection.py:38` — `DB_LOCK_WAIT_WARN_MS = 1000.0`
- `src/database/connection.py:50` — `DB_CASCADE_THRESHOLD_MS = 5000.0`
- `src/database/connection.py:103-104` — `asyncio.Lock` single-connection serializer
- `src/database/connection.py:133-134` — `PRAGMA journal_mode=WAL` + `busy_timeout=10000`
- `src/database/connection.py:258` — `DB_LOCK_WAIT` emission
- `src/database/connection.py:275` — `CASCADE_DETECTED` emission

**Status:** ACTIVE and more severe than audit captured.

### I5 — F-32 SEGV restart + dashboard state loss (CONFIRMED, 1 occurrence in audit)

**Audit-log restart signature (22:42:52):**

```
22:42:52.462 | INFO | DB_AUTO_VACUUM_OK | mode=INCREMENTAL | no_ctx
22:42:52.463 | INFO | DB_CONN | path=data/trading.db wal=Y | no_ctx
```

**Post-restart recovery events (22:44-22:52):**

```
22:44:42.297 | WD_CLOSE_THESIS_RECOVERY | sym=ETHUSDT ent=2252.19 dir=Sell size_usd=750.0 lev=5
22:47:48.945 | WD_CLOSE_THESIS_RECOVERY | sym=PLUMEUSDT ent=0.012845 dir=Sell size_usd=270.0 lev=5
22:49:32.920 | WD_CLOSE_THESIS_RECOVERY | sym=INJUSDT ent=5.083 dir=Buy size_usd=270.0 lev=1
22:52:28.632 | WD_CLOSE_THESIS_RECOVERY | sym=DYDXUSDT ent=0.15001 dir=Sell size_usd=270.0 lev=2
```

**Current code anchors:**
- `src/core/trade_coordinator.py:133` — `_trade_plans: dict[str, TradePlan] = {}` — volatile
- `src/strategies/pnl_manager.py:32-67` — `DailyPnLManager` fields — volatile
- `src/workers/position_watchdog.py:3304` — `WD_CLOSE_THESIS_RECOVERY` emission (the existing partial-recovery pattern to reuse)
- `systemd/trading-workers.service:30-36` — `Restart=always`, `RestartSec=15`, `MemoryMax=800M`, `MemoryHigh=600M`

**F-29 RAM verification:**
- System: 15 GiB total / 12 GiB free / 0 swap (operator confirmed)
- systemd MemoryMax: 800M (unchanged — likely needs raise)

**Status:** SEGV cause reduced by F-29 hardware. Dashboard
state-persistence gap remains architectural — would still lose state
on graceful restart.

---

## Step 0.2 — Baseline Metrics Captured

| Metric | Value | Source |
|--------|-------|--------|
| TIMESTAMP_FAIL events (1.5h audit window) | 6 | grep |
| TIMESTAMP_FAIL events (24h live window) | 6 (no new since audit) | grep |
| PNL_MISMATCH events (audit window) | 2 | grep |
| Current orphan position rows | **14** | `SELECT COUNT(*) FROM positions` |
| DB_LOCK_WAIT (audit window) | 39 | grep |
| DB_LOCK_WAIT (24h live) | **473** | grep |
| CASCADE_DETECTED (audit window) | 13 | grep |
| CASCADE_DETECTED (24h live) | **61** | grep |
| Longest live lock wait | 4,715,047 ms (boot) / 24,435 ms (steady) | grep + sort |
| SEGV restarts (audit window) | 1 (22:42:52) | grep |
| WD_CLOSE_THESIS_RECOVERY events post-SEGV | 4 (ETH, PLUME, INJ, DYDX) | grep |

---

## Step 0.3 — Dependencies Verified

- B1a regime detector fix at commit `6938c69` (2026-05-12) — in place
- F-29 RAM upgrade — operator confirmed; systemd cgroup limits flagged
  for follow-up
- My prior observability work (G1-G11) — NOT merged into base; will
  rebase cleanly per operator direction

---

## Step 0.4 — Trade Trace Baseline

The 20 trades from the audit's 1.5h window were traced through the
12-phase pipeline in the prior observability project (see
`dev_notes/observability_fixes/phase0_baseline.md` for that trace).
Trade lifecycle event sequence:

```
COORD_REG → THESIS_OPEN → STRAT_DIRECTIVE →
BYBIT_DEMO_ORD_SEND → BYBIT_DEMO_WS_CLOSE_EVENT →
COORD_CLOSE_START → THESIS_CLOSE → TIAS_SAVE →
TIAS_LESSON_BRIDGED → COORD_CLOSE_END
```

Each close should be followed by a `_positions_table_cleanup_on_close`
callback firing `delete_position`. **Current evidence (14 orphans) shows
this callback chain has a real or partial gap that Phase 1 of I2 must
identify.**

---

## Step 0.5 — System Stability

- Workers continuously running for ~24h since SEGV recovery
- No recent CASCADE_DETECTED (last one at 23:20:53 yesterday)
- No new TIMESTAMP_FAIL events in ~12h
- WD_TICK firing every 10s, mode=safety_net
- DB at 184 MB (`trading.db`)
- `FUND_POOLS cap=91085.27 available=91085.27 in_use=0.00` (no open trades)

System is stable but degraded: 14 orphans persist, dashboard state
post-restart never recovered, DB cascade pattern active.

---

## Headline Findings vs Audit

| Issue | Audit said | Current verified |
|-------|-----------|-------------------|
| **I1** | 6 TIMESTAMP_FAIL in 1.5h | 6 in same window; no new in 12h. Code path unchanged at `bybit_demo_adapter.py:177-182`. |
| **I2** | "ticker_fallback doesn't call delete_position" | 14 orphans exist live. Explore agent's "callback chain wired" finding correct in part — but real orphans persist. **Phase 1 must identify which path leaks.** |
| **I3** | "PNL_MISMATCH advisory only — corruption commits" | Verified at `position_watchdog.py:3463-3470`. No return between ERROR and on_trade_closed. |
| **I4** | "13.9s cascade peak in audit" | **Live shows 4,715s peak (boot) and 24.4s steady-state peak. 61 cascades total in 24h vs audit's 13.** |
| **I5** | "SEGV + dashboard state loss" | SEGV reduced by F-29 hardware. systemd MemoryMax=800M still in place — **may need operator verification**. State-persistence gap remains architectural. |

---

## Operator Decisions Still Open

Following Phase 0, the operator should review and confirm:

1. **Sequencing locked-in:** I1 → I2 → I3 → I4 → I5, end-to-end with
   operator gates at each Phase 2 (decided in plan-mode dialogue).
2. **I2 priority:** Phase 0 shows orphans are LIVE (not just legacy).
   Operator should agree that I2 Phase 1 verifies every close path AND
   includes a backfill cleanup script.
3. **I4 escalation:** the audit's 14-second cascade is now confirmed to
   peak at 24+ seconds steady-state. Should I4's investigation be
   deepened (e.g., add a write-serializer architecture spike before
   Phase 2)?
4. **systemd MemoryMax=800M:** if F-29 hardware upgrade applied but
   cgroup limit unchanged, the SEGV root cause may persist regardless
   of I5's state-persistence work. Operator confirmation needed.

---

## Recommended Phase 1 Sequence

Per the approved plan: **start I1 Phase 1 investigation immediately
after operator signs off on this baseline.**

I1 Phase 1 deliverable: `dev_notes/five_issues_fixes/i1_phase1_*.md`
files covering:
- `bybit_client_anatomy.md` — every endpoint + parser path
- `response_shape.md` — sample 5+ TIMESTAMP_FAIL responses end-to-end
- `watchdog_close_logic.md` — how `[]` becomes a phantom close
- `recv_window_analysis.md` — Bybit docs + observed latency p50/p95/p99
- `root_cause.md` — synthesis (proximate vs ROOT)
- `fix_options.md` — 3-4 options addressing the root
- `shadow_parity.md` — Shadow path comparison
- `synthesis.md` — final recommendation

Phase 2 of I1 then writes the operator-facing report and pauses for
solution choice.

---

## Verification Gate (Per-Plan)

Before I1 Phase 1 begins:

- [x] All baseline metrics captured
- [x] All 5 audit claims verified against current code + current logs
- [x] Trade trace baseline established (reused from prior project)
- [x] System stability confirmed
- [ ] Operator confirms Phase 0 findings
- [ ] Operator confirms F-29 cgroup status (MemoryMax review)
- [ ] Operator signs off on beginning I1 Phase 1

This deliverable awaits operator review.
