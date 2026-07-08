# N4 — Live State Snapshots

**Snapshot timestamp:** 2026-05-02 11:50:46 UTC (latest worker heartbeat in
workers.log line 1824)

**DB:** /tmp/trading_snapshot_1777722335.db
**Logs cutoffs:** workers.log line 1839 (~12:00 UTC); brain.log line 18327
(11:24:01.390 UTC). brain.log has been silent since 11:24 UTC — no CALL_A
or CALL_B events between 11:24:01 and snapshot time.

---

## A. Brain state

- **Last CALL_A:** `2026-05-02 11:22:46.952 UTC` did=d-1777720966952
  brain.log:18308 → ended 11:24:01.390 with trades=2 (DYDXUSDT Buy lev=2,
  MONUSDT Buy lev=2). Result: BRAIN_NO_PACKAGES (empty packages cache),
  trades_dropped=2 (workers.log:372).
- **Last CALL_B:** `2026-05-02 11:26:32.606 UTC` did=d-1777720966952
  workers.log:1135 → ended 11:26:32.663 with `BRAIN_CYCLE_B_SKIP |
  rsn='no open positions'` (workers.log:1171). Note: CALL_B re-uses the
  same did as CALL_A inside the cycle.
- **Alternation state (which is next):** A→B alternation observed:
  CALL_A at 11:22:46 → CALL_B at 11:26:32 (+~4min). With strategic_interval
  150s, next CALL_A would be expected around 11:29:02 UTC. NOT FOUND —
  no further STRAT_CALL_A_START or STRAT_CALL_B_START in brain.log
  after 11:24:01. Brain has been quiet since (cycle_active=False — see
  `WORKER_LIVENESS_HEARTBEAT | total=19 healthy=14 ... cycle_active=False`
  appearing every 30s from 11:24+).
- **Pending actions queue:** NOT FOUND — no actions since 11:26:32. The
  2 CALL_A directives at 11:24:01 (DYDXUSDT, MONUSDT) were dropped by the
  BRAIN_NO_PACKAGES gate before APEX. Queue is empty.

---

## B. APEX state

- **Last optimization:** `2026-05-02 06:26:33.827 UTC`
  workers.2026-05-02_04-31-00_392071.log:21559
  `APEX_FLIP | sym=NEARUSDT claude=Sell apex=Buy sl=0.3% tp=0.5% cls=low
  sz=$500→$500 mode=fixed conf=95% regime=ranging ms=2099`
  (last APEX_OK at 06:26:26.225 for ONDOUSDT).
- **In-flight:** None. APEX has not been invoked since 06:26 UTC because
  Brain has not produced executable directives (BRAIN_NO_PACKAGES at 11:24
  bypassed APEX entirely).
- **APEX_FLIP rate over last hour:** 0 (no APEX activity at all in
  10:50–11:50 UTC window). Over the entire 24h window:
  - APEX_FLIP: 7 events
  - APEX_FLIP_RESIZE_BLOCKED: 7 events
  - APEX_FLIP_BLOCKED: 3 events
  All 24h events occurred 02:44 UTC ↔ 06:26 UTC (workers.* logs).

---

## C. Enforcer state

- **Today's PnL (DB query):**
  ```sql
  SELECT * FROM daily_pnl WHERE date = '2026-05-02';
  -- → 2026-05-02 | start=0.0 | end=6149.85 | realized=-1.0025 |
  --     trades=29 | wins=5 | losses=24 | mdd=0.0
  ```
  Computed pnl_pct from trade_intelligence (2026-05-02 only):
  COUNT=29, SUM(pnl_pct)=-1.0025, SUM(pnl_usd)=2.34, wins=5.
  (Live ENFORCER_BEAT line 11:48:46 in workers.log:1773 reads
  `total=30T W=5 L=24 wr=16.7% strk=-13` — 30 trades = 29 from
  trade_intelligence + 1 ENFORCER counter increment timing diff.)
- **Consecutive losses:** strk=-13 (per ENFORCER_STATE line 11:48:46:
  `strk=-13`).
- **Active mode (level):** el=1 (capital preservation) — escalated
  `2026-05-02 11:22:45.990` from el=0 → el=1 (workers.log:245)
  `ENFORCER_LEVEL | old_el=0 new_el=1 | reason=streak_boost | pnl=-1.00%
  strk=-13`. Currently sz_mult=0.75. Level-1 caps: max_positions=3,
  max_leverage=3, min_score=75 (per [enforcer] config).
- **Coaching cache contents:** NOT FOUND — searched workers.log/brain.log
  for COACHING, COACH_CACHE, _coaching_cache. No emit observed; all
  STRAT_PROMPT_BUILD lines show `coaching=0ms` indicating empty/no-op.

---

## D. Gate state

- **Per-symbol cooldowns (from logs):**
  Recent SCANNER_LABELED (workers.2026-05-02_04-31-00_392071.log around
  06:29:00) tagged with `secondary=RECENT_LOSER_COOLDOWN`:
  - INJUSDT (rank=2, conf=0.60)
  - DYDXUSDT (rank=3, conf=0.55)
  - AXSUSDT (rank=4, conf=0.55)
  - DOGEUSDT (rank=5, conf=0.55)
  - SANDUSDT (rank=7, conf=0.55)
  - ALGOUSDT (rank=8, conf=0.55)
  This is the recent_failure_blocker_hours=1 from
  `[scanner.qualitative]`. NOT FOUND — explicit
  per-symbol cooldown timestamp emit (only labelled in scanner output).
- **Open position counts (DB):** `SELECT COUNT(*) FROM positions; → 0`.
- **Recent block reasons:**
  Last 4 ORDER_BLOCKED in workers.log (all from 05:10–06:01 UTC,
  outside 1h window before snapshot):
  - 05:10:34 INJUSDT — `lm_deadline_exceeded deadline_s=60.0 elapsed_s=9848.2`
  - 05:10:35 ONDOUSDT — `lm_deadline_exceeded deadline_s=60.0 elapsed_s=9849.3`
  - 06:01:57 AXSUSDT — `lm_deadline_exceeded deadline_s=60.0 elapsed_s=12931.2`
  - 06:01:57 MANAUSDT — `lm_deadline_exceeded deadline_s=60.0 elapsed_s=12932.0`
  All 4 from `purpose=mcp_tool` (operator-driven via MCP, not Stage 2
  cycle). XRAY_DIR_BLOCK count last 24h = 20 events; STRAT_EXEC_BLOCKED
  enforcer leverage = 7 events.

---

## E. OrderService state

- **Last 5 orders placed (DB):** orders table is empty (0 rows in
  snapshot). Source-of-truth is logs:
  Last 5 SHADOW_ORD_RESP (in workers.2026-05-02_04-31-00_392071.log):
  1. 06:26:33.999 ONDOUSDT — oid=0f9a8af3-703a-4468-af08-ad04e2666483
     fill=0.270081 status=FILLED did=d-1777703051893
  2. 06:02:32.167 AXSUSDT — qty=401.5 lev=3 sl=1.3669854 tp=1.413885
     did=d-1777701650866
  3. 04:48:52.499 AXSUSDT — qty=402.3 lev=3 sl=1.3661926 tp=1.413065
     did=d-1777697151599
  4. 04:07:52.485 AXSUSDT — qty=218.1 lev=3 sl=1.3631205 tp=1.4098875
     did=d-1777694725555 (workers.2026-05-01_*.log:43126)
  5. 03:16:51.126 AXSUSDT — qty=218.4 lev=3 sl=1.3609403 tp=1.4076325
     did=d-1777691657786 (workers.2026-05-01_*.log:30758)
- **Last 5 blocks:** see D (4 ORDER_BLOCKED + 7 STRAT_EXEC_BLOCKED in
  24h). No blocks in last 5h.
- **Last 5 fails (Bybit error):** NOT FOUND in 2026-05-01..02 logs —
  searched `Retry exhausted`, `place_order after`, `BybitError`,
  `InvalidOrderError` filtered to last 24h: zero. (Last failures of
  this kind were 2026-04-26 LDOUSDT/INJUSDT — see general.log:42053.)

---

## F. Fund manager state

Most recent FUND_POOLS log line (workers.log:1824):
- `2026-05-02 11:50:46.474` — `FUND_POOLS | cap=1229.97 | available=1229.97
  | in_use=0.00`
  - cap = 1229.97 USDT
  - available = 1229.97 USDT
  - in_use = 0.00 USDT (no open positions)

Most recent FUND_RECONCILE (workers.log:1822):
- `2026-05-02 11:50:46.385` — `FUND_RECONCILE | bybit_total=6149.85
  bybit_available=6149.85 local_total=6149.85 local_cap=1229.97
  local_avail=1229.97 drift_pct=+0.00 auto_correct=false`
  - Last balance fetch from Bybit: 11:50:46.385 (bybit_total=6149.85)
  - Last drift: +0.00% (zero drift).
  - reconcile_interval_seconds=60 → next fetch ~11:51:46.

(Note the gap: total_equity=6149.85 USDT but fund_manager cap=1229.97 USDT
≈ 20% of equity. This is starting_unlock_pct=20 from [fund_manager]
config.)
