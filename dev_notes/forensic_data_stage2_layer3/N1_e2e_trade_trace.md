# N1 — END-TO-END TRADE TRACE

**Collected:** 2026-05-02 ~11:47 UTC
**Snapshot DB:** /tmp/trading_snapshot_1777722335.db
**Logs:** brain.log, workers.2026-05-02_04-31-00_392071.log, workers.log

---

## NOTE ON did=d-1777720966952 (the example provided)

The provided did=d-1777720966952 (CALL_A at 11:22:46 UTC) is NOT a successful order
example. From workers.log:372 / brain.log:18308–18327:

- 11:22:50.089 — `STRATEGIST_PACKAGES_READ | call=CALL_A count=0 age_min_s=0 age_max_s=0`
  (zero packages — empty cache)
- 11:24:01.388 — `CLAUDE_CALL_OK | call_id=1 attempt=1/3 el=69537ms out=2439`
- 11:24:01.389 — `STRAT_DIRECTIVE | #1 sym=DYDXUSDT dir=Buy lev=2`
- 11:24:01.389 — `STRAT_DIRECTIVE | #2 sym=MONUSDT dir=Buy lev=2`
- 11:24:01.390 — `BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped=2`
  (workers.log:372)
- 11:24:01.390 — `BRAIN_CYCLE_A_DONE | el=74437ms trades=2`

So Claude returned 2 trade directives, but the cycle was short-circuited at the
LayerManager because `_coin_packages` was empty. The 2 directives were dropped
before APEX/Gate/OrderService were called. **No SHADOW_ORD_SEND for this did.**

The most recent successful placed order is **ONDOUSDT did=d-1777703051893** at
06:26:33 UTC (workers.2026-05-02_04-31-00_392071.log:21601–21605). Trace below.

---

## SUCCESSFUL TRADE — did=d-1777703051893 (ONDOUSDT Buy)

All timestamps UTC. Source: brain.log + workers.2026-05-02_04-31-00_392071.log.

### 1. Brain CALL_A trigger
- `2026-05-02 06:24:11.893` — brain.log:18258
  `STRAT_CALL_A_START | did=d-1777703051893`

### 2. Package read from _coin_packages
- `2026-05-02 06:24:11.894` — brain.log:18259
  `STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=11 age_max_s=11 reader=brain_call_a`
  (15 packages, age 11s — fresh)

### 3. Prompt build start
- `2026-05-02 06:24:11.894` — implicit (immediately after package read)

### 4. Prompt build done
- `2026-05-02 06:24:12.844` — brain.log:18260
  `STRAT_PROMPT_BUILD | sections=35 | coaching=0ms regime_fetch=1ms regime_instr=0ms dir_perf=0ms trading_mode=0ms universe=1ms market_data=936ms data_lake=1ms xray=0ms sentiment=0ms regime_global=0ms held_symbols=3ms hints=0ms account=9ms`
- `2026-05-02 06:24:12.845` — brain.log:18261 `STRAT_PROMPT_SIZE | sections=35 chars=17423`
- `2026-05-02 06:24:12.845` — brain.log:18262
  `CLAUDE_PROMPT_TRIMMED | site=size reason=chars sections_before=35 sections_after=31 chars_before=17423 chars_after=17107 cap_sections=80 cap_chars=14000`
  (chars cap = 14000, but final 17107 — trimming sections to fit; see strategist.py:2184–2185)
- `2026-05-02 06:24:12.846` — brain.log:18264
  `PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17137 sections=31 packages=15 elapsed_ms=952`

### 5. Claude CLI subprocess spawn
- `2026-05-02 06:24:12.848` — brain.log:18266
  `CLAUDE_CALL_START | call_id=50 in=17137 sys=8985 timeout=300s hash=52aba7c32c75`

### 6. Claude CLI first stdout
- NOT FOUND — searched: brain.log for CLAUDE_PROC_FIRST_STDOUT, CLAUDE_FIRST_TOKEN,
  stdout_so_far. Only stall warnings emit on silence; no first-byte log exists.
  Implied first stdout < 60s after spawn (no stall_60s log fired for this call's
  call_id=50 / pid range).

### 7. Claude CLI completion
- `2026-05-02 06:26:20.612` — brain.log:18270
  `CLAUDE_CALL_OK | call_id=50 attempt=1/3 el=127756ms out=2128 calls=50`
  (total elapsed 127.756s, 2128 chars output)

### 8. Response parse start
- `2026-05-02 06:26:20.612` — implicit (immediately after CLAUDE_CALL_OK)

### 9. Response parse done (validation)
- `2026-05-02 06:26:20.613` — brain.log:18271
  `STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime with fear sentiment (39). Asian late session with low volu'`
- `2026-05-02 06:26:20.613` — brain.log:18272
  `STRAT_DIRECTIVE | #1 sym=ONDOUSDT dir=Buy lev=2 rsn='STRONG ensemble 76.7, highest buy consensus (6.0 votes) across all candidates. R'`
- `2026-05-02 06:26:20.614` — brain.log:18273
  `STRAT_DIRECTIVE | #2 sym=NEARUSDT dir=Sell lev=2 rsn='GOOD ensemble 62.4, strong sell votes (3.45). A+ x-ray setup, pos=82% near range'`
- `2026-05-02 06:26:20.614` — brain.log:18274
  `STRAT_CALL_A_END | el=128721ms trades=2`

### 10. Decision routed (LayerManager queues for APEX)
- `2026-05-02 06:26:20.614` — workers.2026-05-02_04-31-00_392071.log:21482
  `BRAIN_CYCLE_A_DONE | el=128721ms trades=2 view='Ranging global regime with fear sentiment (39)...'`
- `2026-05-02 06:26:20.615` — workers.2026-05-02_04-31-00_392071.log:21483
  `DL_DECISION | type=call_a trades=2 acts=0 el=128721ms prompt=0`
- `2026-05-02 06:26:20.615` — workers.2026-05-02_04-31-00_392071.log:21484
  `BRAIN_DO_START | trades=2`
- `2026-05-02 06:26:20.622` — workers.2026-05-02_04-31-00_392071.log:21485
  `ENFORCER_STATE | trades=29 | wins=5 | losses=23 | wr=0.17 | strk=-12 | pnl=-0.90% | el=1 | sz_mult=0.75 | trigger=streak_boost`
  (Enforcer level 1 = capital preservation; lev limit=3; size mult 0.75)

### 11. APEX assembler invoked
- `2026-05-02 06:26:20.645` — workers.2026-05-02_04-31-00_392071.log:21487
  `APEX_PRICE_SOURCE | sym=ONDOUSDT source=ws price=0.27`
- `2026-05-02 06:26:20.739` — workers.2026-05-02_04-31-00_392071.log:21491
  `REGIME_CACHE_QUERY | sym=ONDOUSDT reader=apex_assembler hit=True ready=True cache_size=49`

### 12. APEX optimizer DeepSeek call start
- `2026-05-02 06:26:20.744` — workers.2026-05-02_04-31-00_392071.log:21492
  `APEX_TIER | tier=2 sym=ONDOUSDT sym_trades=0 regime_trades=59 regime=ranging action=regime_fallback`
- `2026-05-02 06:26:20.745` — workers.2026-05-02_04-31-00_392071.log:21493
  `APEX_REGIME | sym=ONDOUSDT sym_trades=0 regime_trades=59 regime=ranging`
  (tier=2 ⇒ regime fallback path; DeepSeek call still made — see ms=5479
  in APEX_TIMING below)

### 13. APEX optimizer DeepSeek response
- `2026-05-02 06:26:26.224` — workers.2026-05-02_04-31-00_392071.log:21497
  `APEX_TP_CAP | sym=ONDOUSDT qwen_tp=1.4% cap=1.4% cls=medium recTP=1.1% mult=1.30x | Capped to class-aware recTP`
- `2026-05-02 06:26:26.225` — workers.2026-05-02_04-31-00_392071.log:21498
  `APEX_OK | sym=ONDOUSDT dir=Buy sl=0.8% tp=1.4% cls=medium lev=2x no_param_changes conf=65% regime=ranging ms=1580`
- `2026-05-02 06:26:26.225` — workers.2026-05-02_04-31-00_392071.log:21499
  `APEX_TIMING | sym=ONDOUSDT el=5600ms | assemble=119ms deepseek=5479ms parse=0ms constraints=0ms`
  (DeepSeek elapsed 5479ms; total APEX 5600ms)

### 14. APEX gate decision
- `2026-05-02 06:26:33.978` — workers.2026-05-02_04-31-00_392071.log:21593
  `REGIME_CACHE_QUERY | sym=ONDOUSDT reader=apex_gate hit=True ready=True cache_size=49`
- `2026-05-02 06:26:33.980` — workers.2026-05-02_04-31-00_392071.log:21594
  `CONVICTION_WEIGHT | sym=ONDOUSDT regime=ranging trades=0 (< min 3) weight=0.75x(default)`
  (insufficient TIAS history → default 0.75x)
- `2026-05-02 06:26:33.984` — workers.2026-05-02_04-31-00_392071.log:21595
  `GATE_ADJUST | sym=ONDOUSDT changes=[conviction_cap=$369(w=0.8x)]`
- `2026-05-02 06:26:33.985` — workers.2026-05-02_04-31-00_392071.log:21596
  `GATE_TIMING | sym=ONDOUSDT el=9ms modifications=1`

### 15. APEX → Enforcer handoff (size enforcement)
- `2026-05-02 06:26:33.985` — workers.2026-05-02_04-31-00_392071.log:21597
  `ENFORCER_SIZE | sym=ONDOUSDT orig=$369 mult=0.75 final=$277`

### 16. Enforcer evaluation (within strategy_worker._execute_claude_trade)
- (Same line as #15 — Enforcer applies sz_mult=0.75 multiplier; lev=2
  passes since limit=3 at el=1.)

### 17. Enforcer → SL/TP validation handoff
- `2026-05-02 06:26:33.986` — workers.2026-05-02_04-31-00_392071.log:21598
  `XRAY_SLTP | sym=ONDOUSDT sl=$0.2678 struct_rr=0.78 rr_quality=skip`
- `2026-05-02 06:26:33.986` — workers.2026-05-02_04-31-00_392071.log:21599
  `XRAY_TP_NOTE | sym=ONDOUSDT tp=$0.2737 beyond_resistance=$0.2689 (TP may not be reached)`

### 18. TradeGate evaluation (Layer-3 boot/lm gate inside OrderService)
- IMPLICIT — passed (no ORDER_GATE_LM_DEADLINE_EXCEEDED, no ORDER_BLOCKED for did).
  See `_enforce_layer3_gate` in src/trading/services/order_service.py:251 — only
  emits a log when blocking. Pass-through is silent.

### 19. TradeGate → OrderService handoff
- `2026-05-02 06:26:33.987` — workers.2026-05-02_04-31-00_392071.log:21600
  `SHADOW_ORDER_RECEIVED | sym=ONDOUSDT side=Buy qty=2050.0 purpose=layer3_entry layer_snapshot_keys=[captured_at_monotonic,captured_at_wall,layer_active] force=False`

### 20. OrderService.place_order called (full args)
- `2026-05-02 06:26:33.987` — workers.2026-05-02_04-31-00_392071.log:21601
  `SHADOW_ORD_SEND | sym=ONDOUSDT side=Buy qty=2050.0 lev=2 sl=0.26784 tp=0.273699`
  Full args: symbol=ONDOUSDT side=Buy qty=2050.0 lev=2 sl=0.26784 tp=0.273699
  purpose=layer3_entry force=False

### 21. Pre-flight validation
- IMPLICIT — passed (place_order in src/trading/services/order_service.py
  does qty/leverage/SL/TP/min-order-value checks before sending; no
  warning/error logs emitted between SHADOW_ORD_SEND and SHADOW_ORD_RESP).

### 22. Shadow API call
- `2026-05-02 06:26:33.987` — same line as #20 (SHADOW_ORD_SEND fires
  immediately before HTTP POST to http://127.0.0.1:9090).

### 23. API response
- `2026-05-02 06:26:33.999` — workers.2026-05-02_04-31-00_392071.log:21602
  `SHADOW_ORD_RESP | sym=ONDOUSDT oid=0f9a8af3-703a-4468-af08-ad04e2666483 fill=0.270081 st=FILLED`
  (Shadow latency: 12ms send → response)

### 24. Position state update (TradeCoordinator registration)
- `2026-05-02 06:26:33.999` — workers.2026-05-02_04-31-00_392071.log:21603
  `COORD_REG | sym=ONDOUSDT src=claude_direct cat=claude_direct immunity=120s did= order_id=0f9a8af3-703a-4468-af08-ad04e2666483`
- `2026-05-02 06:26:34.000` — workers.2026-05-02_04-31-00_392071.log:21604
  `TradePlan: ONDOUSDT Buy target=$0.27 SL=$0.27 hold=45min trail@1.0% tier=claude_direct`

### 25. Order/thesis persisted to DB
- `2026-05-02 06:26:34.005` — workers.2026-05-02_04-31-00_392071.log:21605
  `THESIS_OPEN | id=1587 sym=ONDOUSDT dir=Buy ent=0.27 sl=0.26784 tp=0.273699 lev=2`
- `2026-05-02 06:26:34.408` — workers.2026-05-02_04-31-00_392071.log:21608
  `ProfitSniper: new position ONDOUSDT Buy @ $0.27, buffer pre-filled with 36 points, atr_entry=0.000430`
- `2026-05-02 06:26:34.775` — workers.2026-05-02_04-31-00_392071.log:21609
  `STRAT_EXEC | sym=ONDOUSDT dir=Buy qty=2050.0000 sz=$277x2 sl=$0.267840 tp=$0.273699`
- `2026-05-02 06:26:34.775` — workers.2026-05-02_04-31-00_392071.log:21610
  `BRAIN_DO_TRADE | sym=ONDOUSDT [1/2] el=875ms | apex_apply=74ms apex_ds=1580ms gate=9ms exec=791ms rsn=ok`

NOTE: orders table in snapshot DB is empty (0 rows). The order is recorded
only in thesis_manager and trade_coordinator state plus eventually in
trade_intelligence on close.

### 26. Telegram alert sent
- NOT FOUND for this entry trade — searched workers.* for ALERT_SENT around
  06:26:34. No entry-time Telegram alert fired (the only ALERT_SENT entries
  are critical/info-level on close). Closing alert at 06:29:10.277:
  `ALERT_SENT | level=info len=449 | tid=t-ONDOUSDT-mon wid=w-1777703349462`
  (general.log:60064)

### Position closed (post-trace)
- `2026-05-02 06:28:39.367` — workers.2026-05-02_04-31-00_392071.log:21732
  `TIME_DECAY_INIT | sym=ONDOUSDT dir=Buy sl=0.80% atr=0.16% cls=medium p_win=0.65 regime_conf=0.40 max_hold_s=2700 grace_s=120 atr_mult=2.00`
- `2026-05-02 06:29:10.282` — workers.2026-05-02_04-31-00_392071.log:21826
  `DL_TRADE | tid=t-ONDOUSDT-1777703350 sym=ONDOUSDT dir=Buy ent=0.27 ext=0.269719 pnl=-0.1040% pnl$=-0.2880 rsn=time_decay_p_win_low held=2.6min`
- `2026-05-02 06:29:10.338` — workers.2026-05-02_04-31-00_392071.log:21834
  `TIAS_SAVE | id=821 sym=ONDOUSDT dir=Buy pnl=-0.10% win=False regime=ranging rsi=60.727209`

---

## BLOCKED TRADE — did=d-1777698125354 (ONDOUSDT XRAY_DIR_BLOCK)

Source: workers.2026-05-02_04-31-00_392071.log

### Steps 1-13 (similar — APEX optimization completed)
- `2026-05-02 05:03:46.715` — line 5956
  `APEX_OK | sym=ONDOUSDT dir=Buy sl=0.3% tp=0.5% cls=low lev=3x sz=$500→$300 conf=60% regime=ranging ms=1941 | did=d-1777698125354`

### Step 14 — XRAY direction block (in strategy_worker._execute_claude_trade:1251)
- `2026-05-02 05:03:47.691` — line 5980
  `XRAY_DIR_BLOCK | sym=ONDOUSDT chosen=Buy rr_long=0.1 rr_short=1.5 ratio=21.7x | did=d-1777698125354`
  (Block reason: XRAY's RR for short was 21.7× the RR for long → direction
  contradiction with Claude/APEX's Buy. Trade dropped at strategy_worker
  level. No SHADOW_ORD_SEND for this did.)

There is no APEX_BLOCKED, GATE_BLOCK, ENFORCER_BLOCK, OrderService block,
or Bybit-side reject for this trace — the block is purely the X-RAY
direction filter inside strategy_worker.

A second blocked example (ENFORCER): same did=d-1777703051893 second
directive NEARUSDT was blocked by Enforcer:
- `2026-05-02 06:26:34.854` — workers.2026-05-02_04-31-00_392071.log:21615
  `STRAT_EXEC_BLOCKED | sym=NEARUSDT dir=Buy rsn='PRESERVATION: leverage=5 exceeds limit of 3x (PnL=-0.90%)'`
- `2026-05-02 06:26:34.854` — workers.2026-05-02_04-31-00_392071.log:21616
  `TRADE_SKIP | sym=NEARUSDT rsn=enforcer_block detail='PRESERVATION: leverage=5 exceeds limit of 3x (PnL=-0.90%)'`
- `2026-05-02 06:26:34.855` — workers.2026-05-02_04-31-00_392071.log:21617
  `BRAIN_DO_TRADE | sym=NEARUSDT [2/2] el=79ms | apex_apply=70ms apex_ds=2099ms gate=8ms exec=1ms rsn=enforcer_block`

(NEARUSDT was APEX-flipped Sell→Buy at conf=95% with lev=5 from APEX, but
Enforcer level=1 caps lev at 3 → blocked.)

---

## CLAUDE STALL/TIMEOUT TRACE — pid=17370 (CALL_A did=d-1777702618197)

No full CLAUDE_CALL_TIMEOUT in 2026-05-01..02; the closest stall (recovered
within timeout) is below. Searched: brain.log for CLAUDE_CALL_TIMEOUT,
CLAUDE_PROC_TIMEOUT_PARTIAL with date-prefix `^2026-05-0[12]` — zero hits.
20 stall_120s events did fire in 24h (all eventually recovered — workers
log shows CLAUDE_CALL_OK after each).

### Sequence
- `2026-05-02 06:16:58.197` — brain.log:18241
  `STRAT_CALL_A_START | did=d-1777702618197`
- `2026-05-02 06:16:58.546` — brain.log:18248
  `STRAT_CALL_A | chars=17192`
- `2026-05-02 06:16:58.547` — brain.log:18249
  `CLAUDE_CALL_START | call_id=49 in=17192 sys=8985 timeout=300s hash=e507ed26d18e | did=d-1777702618197`
- `2026-05-02 06:16:58.576` — brain.log:18250
  `CLAUDE_PROC_SPAWNED | pid=17370 spawn_ms=19`
- `2026-05-02 06:17:58.605` — brain.log:18251
  `CLAUDE_PROC_STALL_60S | pid=17370 elapsed=60s stdout_so_far=0 timeout_in_s=240`
  (60s of zero stdout — INFO level, claude_code_client.py:1201)
- `2026-05-02 06:18:58.621` — brain.log:18252
  `CLAUDE_PROC_STALL_120S | pid=17370 elapsed=120s stdout_so_far=0 timeout_in_s=180 state=S wchan=ep_poll`
  (120s — WARNING level; process state=S(sleeping) wchan=ep_poll waiting on
  network/file event — likely Claude API responding slowly)
- `2026-05-02 06:19:11.884` — brain.log:18253
  `CLAUDE_CALL_OK | call_id=49 attempt=1/3 el=133327ms out=2112 calls=49 | did=d-1777702618197`
  (Recovered after 133s — under the 300s timeout. No retry; no kill.)

For a true CLAUDE_CALL_TIMEOUT example (older — last in brain.log):
- `2026-04-23 13:36:15.100` (brain.log)
  `CLAUDE_CALL_TIMEOUT | call_id=9 attempt=1/3 timeout=300s err='claude CLI timed out after 300s' | did=d-1776950222428`
- followed by `CLAUDE_RETRY | call_id=9 attempt=1/3 err='claude CLI timed out after 300s' interval=4.0s`
- spawn `CLAUDE_PROC_SPAWNED | pid=6340 spawn_ms=319`
- 5 min later `CLAUDE_PROC_KILLED | pid=6340`
- `2026-04-23 13:42:04.934` `CLAUDE_CALL_TIMEOUT | call_id=9 attempt=2/3 timeout=300s`
- (3-attempt retry ladder: 4s/8s sleep between; 300s timeout each per
  config.toml [brain].claude_cli_timeout_seconds=300)
