# Live Monitoring Report — Layer 1 + Workers

**Window:** 2026-04-27, 09:58:48 → 10:13:49 UTC (~15 min)
**System:** Trading Intelligence MCP — fresh restart at 09:58:48
**Operator action:** /control → Start Trading at 09:59:10 (via telegram)

---

## 🚨 P0 — Layer 1B/1C/1D workers SILENTLY DEAD since restart

**Five critical workers have not executed a single tick in 15 minutes:**

| Worker | Layer | Sweet-spot | Fires logged | First tick? |
|---|---|---|---|---|
| `structure_worker` (XRAY) | 1B | 0:45 | **3** | ❌ NEVER |
| `signal_worker` | 1B | 1:00 | **3** | ❌ NEVER |
| `regime_worker` | 1B | 1:15 | **3** | ❌ NEVER |
| `strategy_worker` | 1C | 1:30 | **3** | ❌ NEVER |
| `scanner_worker` | 1D | 4:00 | **3** | ❌ NEVER |

The sweet-spot scheduler is firing on schedule (every 5 min, 3 fires each), but `BaseWorker.tick()` never produces output afterwards. No `XRAY_TICK_SUMMARY`, no `SIG_TICK_SUMMARY`, no `REGIME_GLOBAL`, no `STRAT_CYCLE_DONE`, no `SCANNER_TICK_SUMMARY` since 09:58:48. Compare to the prior good run at 06:51 where every one of these tags fired cleanly per cycle.

**No errors / tracebacks / exceptions logged** — the failure is silent.

Likely candidates:
- `tick()` blocking forever on first I/O (DB read, shadow_kline_reader, ta_cache) and the timeout/watchdog isn't catching it
- Or a coroutine deadlock between sweet-spot scheduler and base_worker's tick wrapper for these specific 5 workers (kline + altdata both share the sweet-spot path and *do* tick — so the bug is specific to the 5 dead ones, possibly tied to the `structure_engine` / `shadow_kline_reader` / `ta_cache` services they all read).
- `EVENT_LOOP_BLOCKER lag=692ms top_tasks=[Task-28, telegram_bot_worker, structure_worker]` at boot named structure_worker as a blocker — possible early hang.

**Workers that DID get WORKER_FIRST_TICK** (13 total, all healthy):
price_worker, telegram_bot_worker, price_alert_worker, profit_sniper, scheduled_report_worker, enforcer_worker, position_watchdog, fund_manager_worker, fund_reconciler, news_worker, cleanup_worker, kline_worker, altdata_worker.

---

## 🚨 P0 — Layer 3 toggle persist bug (confirmed twice, live)

**Operator toggled Layer 3 ON twice (09:59:10, 10:10:37). Both toggles silently reverted within ~30s.**

Sequence on the second occurrence (live during this monitor):
- 10:10:37.720 `LAYER_TOGGLE | layer=3 from=False to=True` (memory only)
- 10:10:43.894 `LAYER_STATE_SYNC | match=false disk={3:F} memory={3:T}` — disk persist never fired
- 10:10:43.896 `LAYER_STATE_DRIFT | action=reload_from_disk` — memory reverts to {3:F}

**Disk file `data/layer_state.json`** has timestamp `2026-04-27T09:59:10.131958+00:00` and `"3": false` — written *during* the layer-2 toggle (09:59:10.131), BEFORE the cascaded layer-3 toggle at 09:59:10.132. The layer-3 persist call is missing or sequenced wrong.

**Visible impact:** every BRAIN_CYCLE_A produced 2 trades (10:00:48, 10:07:19) and `WARNING | Layer 3 inactive — skipped 2 new trades` killed all of them. **Zero trades reached Shadow in 15 min** despite the brain pipeline functioning.

**Note:** This is a regression of the P0_2 fix (commit ce02282). The `LAYER_STATE_SYNC` heartbeat was added to *detect* drift — and it does, but the underlying root cause (the persist call missing for cascaded layer-3 toggle) was never fixed. Likely fix in `src/core/layer_manager.py:start_layer` — when called for layer=3 after layer=2, ensure persist runs after the layer=3 in-memory mutation.

---

## ⚠️ P1 — Brain operating on empty / stale context

From `brain.log` 10:05:49:
```
PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=2737 sections=22 packages=0
                  | xray=0ms hints=0ms regime_global=0ms ... market_data=837ms
```

- `packages=0` — scanner_worker dead, no `CoinPackage` instances ever built
- `coins=2` — only BTC+ETH reaching brain (inline-analyzed via `TAEngine.analyze`), not the 10–15 packaged coins per restructure target
- `xray=0ms hints=0ms regime_global=0ms` — render times zero because there's no data to render (consensus/structure/regime caches empty post-restart since their workers never ticked)
- Strategist fell back: `STRAT_DIRECTIVE ... 'No per-coin regime → global trending_down default'` — using the startup `REGIME_SEED` (single BTC seed value) for every coin
- 30 coins are stuck at `SCANNER_HYSTERESIS streak=1/2` from the boot pre-pass; they'll never advance because scanner_worker doesn't tick

---

## ⚠️ P2 — kline_worker quality flag misreports gaps

| Tick | tf_split | Notes |
|---|---|---|
| 10:00:51 | `5:10000, 60:10000, 240:9994, D:9542` | First-run backfill, daily 95.4% complete, `quality=ok` |
| 10:05:41 | `5:10000, 60:10000, 240:0, D:0` | Incremental, no new bars on 4h/D — fine in principle |
| 10:10:46 | `5:10000, 60:10000, 240:9994, D:0` | 4h re-fetched 9994 bars (full history), D again 0 |

`quality=ok` reports green even when daily TF was 458 bars short and even when 4h re-fetches all history (wasteful Bybit traffic). Quality threshold likely too lax to catch fetcher inconsistencies.

---

## ⚠️ P3 — altdata_worker tick variance

- Tick 1 (cold): 9216ms (funding+oi+fg+onchain)
- Tick 2: 4838ms (`ran=[funding,oi,onchain]`)
- Tick 3: 9135ms (`ran=[funding,oi,onchain]`)

Same sub-feed list as tick 2 but ~2× slower. Right at edge of the 12s threshold (Issue 9 fix). Bybit funding/OI calls themselves are 8–9s for 50 coins parallelized — if Bybit is slow this will trip the WARN threshold.

---

## ✅ Healthy components

- `price_worker`: WS connected, 50/50 quotes, 6500–7800 msgs/min, 0–1ms ticks (correctly designed — tick is health-check; data is WebSocket-streamed)
- `news_worker`: Finnhub fetch, 96 articles → 0 new (dedup working), 1.1s/tick
- `kline_worker` core fetch: 5m and 1h timeframes always 100% (10000/10000 bars)
- `fund_reconciler`: bybit↔local drift +0.00% every minute
- `cleanup_worker`: deleted 18742 stale klines + 3 orderbook rows, VACUUM ok, 141.4 MB DB
- `LAYER_STATE_SYNC` heartbeat: doing its job (detected drift twice in 15 min)
- `EVENT_LOOP_LAG`: 692ms once at boot, never recurred (boot-time congestion only)

---

## Layer 1 cycle cadence (mapped from sweet-spot fires)
```
T+0:45 → structure (XRAY)     [DEAD]
T+1:00 → signal               [DEAD]
T+1:15 → regime               [DEAD]
T+1:30 → strategy             [DEAD]
T+4:00 → scanner              [DEAD]
T+4:30 → BRAIN_CYCLE_A reads  [running on empty]
```

---

## Recommended next steps (in order)

1. **Investigate why structure/signal/regime/strategy/scanner tick() never logs first-run output.** Add an `await_with_log` instrumentation around `tick()` invocation in BaseWorker's sweet-spot path, or attach `py-spy dump` / `asyncio.all_tasks()` to the running process to capture stack traces of the worker tasks. This is the largest unknown.
2. **Fix the Layer 3 persist bug** in `src/core/layer_manager.py:start_layer` — ensure cascaded layer toggles each call `_persist_state()` after their in-memory mutation.
3. **Tighten kline `quality` reporting** to flag tf_split shortfalls vs expected per-TF row counts.
4. **Add a watchdog** that alarms when `WORKER_FIRST_TICK` is missing N seconds after a worker's `WM_START` (this would have caught issue #1 within ~3 min).

---

## Evidence locations

- Workers log: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
- Brain log: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/brain.log`
- Disk layer state: `/home/inshadaliqbal786/trading-intelligence-mcp/data/layer_state.json`
- Process PIDs at observation start: workers=396, server=397, shadow=384
