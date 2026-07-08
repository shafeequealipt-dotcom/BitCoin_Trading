# Layer 1 Live Monitoring Report — 2026-04-27 06:23–06:52 UTC

**Scope:** real-time observation of every Layer-1 worker and everything upstream of Stage 2, on a freshly-restarted production system. Focus: data flow in/out per worker, latency, anomalies, errors.

**Operator:** asked at 06:22 to monitor live, find flaws, errors, anomalies. Stopped at 06:52.

**Observation window:** 06:23 → 06:52 UTC (29 min, ~6 full 5-min cycles after the cold-start `CYCLE_RESUME` at 06:20:00).

**Processes confirmed running (`pgrep -af python`):**
- PID 402 — `/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python workers.py`
- PID 403 — `/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python server.py --transport sse --port 8080`
- PID 394 — `/home/inshadaliqbal786/shadow/.venv/bin/python shadow.py`

**Log files (active):**
- `data/logs/workers.log` — all worker output
- `data/logs/general.log` — general loguru sink
- `data/logs/brain.log` — Stage 2 / Claude calls
- `data/logs/mcp.log` — MCP server

---

## 1. System health summary

### Layer 1A (always-on data fetchers) — HEALTHY

| Worker | Tick interval | Latest data lag at 06:23 | Tick latency | Errors | Notes |
|---|---:|---:|---:|---:|---|
| `price_worker` | 45s | 83s | <1ms | 0 | Bybit WS connected, msgs_per_min 7400-10254, 50/50 quotes cached |
| `kline_worker` | 5min | 193s | **9-20s** (threshold 8s) | 0 | All 50 syms × scheduled TF; tf_split alternates per cooldown; KLINE_FETCH `quality=ok errors=0` |
| `altdata_worker` | 5min | F&G 217s, FR 79s, OI 79s | **5-9s** (threshold 2s) | 0 | Three feeds in one worker, threshold too tight |
| `news_worker` | 5min | **3495s @ 06:23**; 1 article fetched at 06:33 | 1.07s | 0 | Feed naturally thin; worker fine |
| `reddit_worker` | n/a | **NEVER REGISTERED** | — | — | `REDDIT_CLIENT_ID=` empty in `.env` → `manager.py:129` skips registration → 0 rows in `reddit_posts` table |

### Layer 1B (cycle-gated analyzers) — HEALTHY (logic) but degraded outputs

| Worker | Tick | Output | Notes |
|---|---|---|---|
| `structure_worker` (XRAY) | 2 batches × 25 syms / cycle | `XRAY_TICK_SUMMARY universe=50 symbols=25 analyzed=25 errors=0 cached=50 setups=12 skips=13` | ~50% setup-detection rate. `XRAY_CLASSIFY` populating setup_type for ~12-25 of 50 per cycle |
| `signal_worker` | per-coin | 3850 signals in 30 min — **all `signal_type=NEUTRAL`, avg confidence 0.30** | Phase-29 confidence-gate downgrades. Working as designed but Scanner can't use direction |
| `regime_worker` | 5min | `STRAT_REGIME_DIST up=4 down=11 ranging=27 volatile=7 dead=0 total=49 global=trending_down` (stable 4 cycles) | One slow tick 5.4s (threshold 4s) |

### Layer 1C (strategy pipeline) — **PARTIALLY BROKEN**

`StrategyWorker._build_per_coin_consensus(filtered)` — see Finding #1.

| Cycle | total in cache | GOOD | STRONG | WEAK | LEAN |
|---|---:|---:|---:|---:|---:|
| 06:21:32 | 5 | 2 | 0 | 3 | 0 |
| 06:26:33 | 6 | 4 | 2 | 0 | 0 |
| 06:31:33 | 18 | 13 | 4 | 1 | 0 |
| 06:36:33 | 13 | 7 | 0 | 5 | 1 |
| 06:41:33 | 8 | 3 | 2 | 3 | 0 |
| 06:46:33 | 10 | 4 | 2 | 4 | 0 |

**Each cycle only writes 5–18 entries** (post-`apply_restrictions` filter), against a 50-symbol watchlist. Cumulative merge over time partially compensates but Scanner still sees `consensus=NONE` for many coins early in the run.

### Layer 1D (Scanner) — qualitative filter starved

| Time | qualified | selected | forced | top |
|---|---:|---:|---:|---|
| 06:24:00 | **0** | 2 | 2 | BTC(0.000) |
| 06:29:00 | **1** (DYDX) | 3 | 2 | DYDX(0.642) |
| 06:34:00 | **0** | 2 | 2 | BTC(0.000) |
| 06:39:00 | **1** | 3 | 2 | — |
| 06:44:00 | **1** | 3 | 2 | — |
| 06:49:00 | **2** | 4 | 2 | — |

Phase-5 plan target (T5.1): avg qualified 5–25 over 2h. **We're at 0–2.** Plan explicitly says: *"Escalate if avg qualified is 0 over 2 h (criteria too strict)."*

### Stage 2 → Brain → Execution — **FULLY BROKEN in shadow mode**

Every brain decision since restart has crashed at the Shadow adapter (Finding #2). Symbols affected so far: DYDX, ETH, RUNE.

---

## 2. Findings (root-cause analysis)

### Finding #1 — HIGH — StrategyWorker writes consensus cache from filtered list

**File:** `src/workers/strategy_worker.py:591`

```python
# strategy_worker.py:585-591  (relevant comment + buggy line)
# write it to the cache BEFORE the Layer 3 active check. Consensus
# is observability/data, not execution; ScannerWorker reads it
# whether Layer 3 is on or off. Stale entries (coins not processed
# this tick) are preserved via merge so a momentary gap doesn't
# zero the entry the selector reads.
if layer_manager:
    new_consensus = self._build_per_coin_consensus(filtered)   # ← BUG
```

**Symptom:** The comment block (5 lines above the bug) literally states the cache is "observability/data, not execution". But the implementation passes `filtered = self.pnl_manager.apply_restrictions(consensus_setups, mode)` (line 564), which filters by `max_score_threshold` (50 in NORMAL mode). Coins that didn't clear the PnL-mode threshold are invisible to ScannerWorker.

**Impact:** Phase-5 ScannerWorker `_qualifies()` calls `lm.get_strategy_consensus(symbol)` and short-circuits to `consensus=NONE` for any coin not in the cache. With 5-18 of 50 coins ever written per cycle, 32-45 coins permanently fail criterion 2 (`consensus in {STRONG,GOOD}`) on every tick. This starves the Stage 2 pipeline.

**Plan reference:** Phase 3 plan §"Implementation detail (the hand-off)" — *"in StrategyWorker after the gate at line ~575, write the cache OUTSIDE any `is_layer_active(3)` check — the cache is observability/data, not execution."* Same intent as the in-file comment; the implementation drifted.

**Fix scope:** one-line change at line 591 — pass `consensus_setups` instead of `filtered`. The summary alias at line 607 (`_strategy_consensus_summary`) should keep using `filtered` because legacy strategist reads at `strategist.py:1017/1587` expect the post-filter shape.

---

### Finding #2 — HIGH — `ShadowOrderService.place_order()` signature drift

**Files:**
- `src/shadow/shadow_adapter.py:393-403` — Shadow signature
- `src/trading/services/order_service.py:231-245` — Live signature
- `src/core/layer_manager.py:_execute_new_trades:888` — caller

```python
# order_service.py:231 — LIVE  (accepts purpose / layer_snapshot / force)
async def place_order(self, symbol, side, order_type, qty,
                      price=None, stop_loss=None, take_profit=None, leverage=None,
                      *, purpose="other", layer_snapshot=None, force=False) -> Order

# shadow_adapter.py:393 — SHADOW  (missing kwargs)
async def place_order(self, symbol, side, order_type, qty,
                      price=None, stop_loss=None, take_profit=None, leverage=None) -> Order
```

**Symptom:** Every brain trade in shadow mode crashes with:
```
ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
```

**Observed crashes during the 29-min window** (`grep "ShadowOrderService.place_order() got an unexpected keyword argument" data/logs/workers.log`):

```
06:34:50.866 ERROR  Claude trade failed for DYDXUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
06:34:50.866 WARN   TRADE_SKIP  sym=DYDXUSDT  rsn=exception  did=d-1777271555931
06:41:50.715 ERROR  Claude trade failed for DYDXUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
06:41:50.715 WARN   TRADE_SKIP  sym=DYDXUSDT  rsn=exception  did=d-1777271967501
06:48:20.718 ERROR  Claude trade failed for RUNEUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
06:48:20.718 WARN   TRADE_SKIP  sym=RUNEUSDT  rsn=exception  did=d-1777272379720
06:48:20.805 ERROR  Claude trade failed for ETHUSDT:  ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
06:48:20.805 WARN   TRADE_SKIP  sym=ETHUSDT   rsn=exception  did=d-1777272379720
```

**Impact:** **0 brain-driven trades have executed** since cold-start. Stage 2 logic is intact (decisions are made), but Shadow rejects all of them.

**Fix scope:** `ShadowOrderService.place_order` must accept the same 3 keyword-only args (`purpose`, `layer_snapshot`, `force`). Even if Shadow ignores them, the signature parity is the contract. Per CLAUDE.md root-cause rule: don't catch the TypeError — fix the signature.

---

### Finding #3 — MEDIUM — `SCANNER_FILTER_RESULT` is DEBUG-only

**File:** `src/workers/scanner_worker.py:485-589` (`_qualifies` method) — no INFO log on per-coin pass/fail.

**Impact:** When `qualified=0`, operators cannot tell which of the 5 criteria (xray, consensus, regime, RR, blockers) failed for which coin. During this monitoring window we had to *infer* the dominant cause from incidental XRAY_BLOCK lines emitted at execution time.

**Fix scope:** emit a single INFO summary line per cycle aggregating fail-reason counts, e.g.:
```
SCANNER_FILTER_AGGREGATE | cycle_id=… qualified=2 fail_no_xray=8 fail_setup_none=18 fail_consensus=15 fail_regime=4 fail_rr=3 fail_blockers=0
```
Plan Phase 5 specifies per-coin DEBUG only. An aggregate INFO line preserves the per-coin DEBUG path for forensics while making the cycle-level pattern visible at INFO.

---

### Finding #4 — MEDIUM — RedditWorker not registered

**File:** `src/workers/manager.py:129`, `.env` at `REDDIT_CLIENT_ID=` (empty)

```
manager.py:129  if getattr(settings, "reddit", None) and settings.reddit.client_id:
              # → False, RedditWorker never appended to self.workers
```

**Symptom:** `reddit_posts` table has 0 rows ever. Sentiment aggregator emits `SENT_UNKNOWN | rsn=no_news_no_reddit` for almost every coin. Tag distribution last 30 min: `SENT_UNKNOWN_CACHE_HIT` = 192 — the dominant tag in the workers log.

**Impact:** Sentiment scoring degrades to fear-greed only for ~50 coins. Stage 2 prompt `sentiment` section is sparse. The Layer-1 plan does NOT require reddit (`use_packages` paths read `aggregated_sentiment` which still works), but the system is operating with a known sentiment input missing.

**Fix scope:** config — provide reddit creds or accept the degraded mode explicitly (maybe log `REDDIT_DISABLED reason=no_creds` once at startup).

---

### Finding #5 — LOW — `active_universe` row enrichment incomplete

**Verified by:**
```sql
SELECT symbol, opportunity_score, volume_24h, change_24h_pct, funding_rate, spread_pct, coin_tier, updated_at
FROM active_universe ORDER BY opportunity_score DESC;

symbol    opportunity_score   volume_24h   change_24h_pct   funding_rate   spread_pct   coin_tier   updated_at
BTCUSDT   0.0                 0            0                0              0            1           2026-04-27 06:34:00
ETHUSDT   0.0                 0            0                0              0            1           2026-04-27 06:34:00
DYDXUSDT  0.642               0            0                0              0            3           2026-04-27 06:29:00
```

**Symptom:** scanner writes `opportunity_score` and `coin_tier` correctly; the four enrichment columns stay at 0 across every row. Likely the scanner write site does not pass them after the Phase-5 rewrite of `tick()`.

**Impact:** LOW — Stage 2 reads `_coin_packages`, not `active_universe`. But operators inspecting the table for diagnostics see misleading zeroes. Telegram `/status` and any UI surface reading this table show false zeros for vol/change/fr/spread.

**Fix scope:** at scanner write site, pass the full enrichment dict (already computed in `_compute_opportunity_score`). One write call.

---

### Finding #6 — LOW — `altdata_worker` consistently exceeds 2s threshold

**Observed slow ticks:**
```
06:06:49.826 BASE_WORKER_TICK_SLOW name=altdata_worker el=4824ms threshold_ms=2000
06:11:54.646 BASE_WORKER_TICK_SLOW name=altdata_worker el=9645ms threshold_ms=2000
06:21:54.340 BASE_WORKER_TICK_SLOW name=altdata_worker el=9340ms threshold_ms=2000
06:26:50.000 BASE_WORKER_TICK_SLOW name=altdata_worker el=4998ms threshold_ms=2000
06:31:54.148 BASE_WORKER_TICK_SLOW name=altdata_worker el=9146ms threshold_ms=2000
```

**Root cause hypothesis:** worker fetches three separate APIs serially (Fear & Greed, Funding Rates, Open Interest). At ~2s each they exceed the 2s soft bound. Not fatal — interval is 300s, plenty of slack.

**Fix scope:** raise threshold to 12s OR split into three workers OR `asyncio.gather` the three fetches.

---

### Finding #7 — INFO — MCP order failures (out of scope but flagged)

```
06:27:15.121 ORDER_RETRY_EXHAUSTED link_id=ti-04caef… sym=ETHUSDT attempts=2 purpose=mcp_tool err=ab not enough for new order (ErrCode: 110007)
06:27:16.040 ORDER_RETRY_EXHAUSTED link_id=ti-c9ce7c… sym=BTCUSDT attempts=2 purpose=mcp_tool err=ab not enough for new order (ErrCode: 110007)
```

ErrCode 110007 maps to `PositionError` (`Position not exists`) per `src/trading/client.py:57` — but the human error string says "not enough for new order" which is misleading. These are **MCP tool calls** (probably user-issued via Claude Code tool), not Layer-1. Two attempts → exhausted retries.

**Fix scope:** out of monitoring focus, but worth noting Bybit's retMsg vs retCode mismatch could mislead operators.

---

## 3. Raw log excerpts (for reference)

### 3.1 Cold-start sequence

```
06:19:35.528 INFO   src.core.layer_manager:start_layer:200 | CYCLE_RESUME_WAIT | next_boundary_in_sec=24 reason=cold_start_after_toggle
06:20:00.003 INFO   src.core.layer_manager:_await_resume_boundary:230 | CYCLE_RESUME | boundary=2026-04-27T06:20:00.003489+00:00
```

Phase-4 cold-start boundary wait worked correctly.

### 3.2 Scanner cycles (full sequence)

```
06:24:00.010 SCANNER_PACKAGE_BUILD_START  cycle_id=c-2026-04-27-06:20  packages_to_build=2
06:24:00.018 SCANNER_PACKAGE_BUILD_DONE   packages=2  total_size_bytes=1955  elapsed_ms=8
06:24:00.020 SCANNER_SELECT               qualified=0  selected=2  forced=2  watch_list=50
06:24:00.020 SCANNER_TICK_SUMMARY         scored=2 selected=2 top_n=15 forced_in=2 mean_score=0.000 top=BTCUSDT(0.000) el=20ms

06:29:00.035 SCANNER_PACKAGE_BUILD_START  packages_to_build=3
06:29:00.036 SCANNER_PACKAGE_BUILD_DONE   packages=3  total_size_bytes=2937  elapsed_ms=2
06:29:00.041 SCANNER_SELECT               qualified=1  selected=3  forced=2
06:29:00.042 SCANNER_TICK_SUMMARY         top=DYDXUSDT(0.642) el=41ms

06:34:00.008 SCANNER_PACKAGE_BUILD_DONE   packages=2  total_size_bytes=1967  elapsed_ms=2
06:34:00.013 SCANNER_SELECT               qualified=0  selected=2  forced=2

06:39:00.010 SCANNER_PACKAGE_BUILD_DONE   packages=3  total_size_bytes=2941  elapsed_ms=1
06:39:00.013 SCANNER_SELECT               qualified=1  selected=3  forced=2

06:44:00.010 SCANNER_SELECT               qualified=1  selected=3  forced=2

06:49:00.018 SCANNER_SELECT               qualified=2  selected=4  forced=2
```

Sweet-spot offset confirmed: scanner fires at `:X4:00` every 5 min.

### 3.3 Strategy worker cycles

```
06:21:32.835 STRAT_CONSENSUS_SUMMARY total=5  GOOD=2  WEAK=3
06:26:33.540 STRAT_CONSENSUS_SUMMARY total=6  GOOD=4  STRONG=2
06:31:33.572 STRAT_CONSENSUS_SUMMARY total=18 GOOD=13 STRONG=4 WEAK=1
06:36:33.951 STRAT_CONSENSUS_SUMMARY total=13 GOOD=7  LEAN=1  WEAK=5
06:41:33.773 STRAT_CONSENSUS_SUMMARY total=8  GOOD=3  STRONG=2 WEAK=3
06:46:33.247 STRAT_CONSENSUS_SUMMARY total=10 GOOD=4  STRONG=2 WEAK=4
```

Sweet-spot offset: `:X1:30` every 5 min. Strategy fires ~2:30 before scanner reads its cache.

### 3.4 XRAY classifier output (sample)

```
06:20:45.578 XRAY_SCANNER       total=25 qualified=25 skipped=13 #1=SEIUSDT(78) #2=AXSUSDT(73) #3=ATOMUSDT(68)
06:20:45.578 XRAY_TICK_SUMMARY  universe=50 batch=1/2 symbols=25 analyzed=25 errors=0 cached=25 setups=12 skips=13

06:25:45.469 XRAY_SCANNER       total=31 qualified=31 skipped=19 #1=DYDXUSDT(83) #2=ALGOUSDT(78) #3=BLURUSDT(76)
06:30:45.518 XRAY_SCANNER       total=25 qualified=25 skipped=13 #1=SEIUSDT(78) #2=AXSUSDT(73) #3=ATOMUSDT(68)
06:35:45.402 XRAY_SCANNER       total=31 qualified=31 skipped=19 #1=DYDXUSDT(83) #2=ALGOUSDT(78) #3=BLURUSDT(76)

XRAY_CLASSIFY_SUMMARY (latest):
06:35:45.401 total=25 none=18 bullish_fvg_ob=4 bearish_fvg_ob=2 bearish_structural_break=1
```

Note batches alternate between coin-set A (25 syms, top SEI/AXS/ATOM) and coin-set B (31 syms, top DYDX/ALGO/BLUR). Caches union to all 50.

### 3.5 Brain failure cluster (4 crashes, 3 distinct symbols)

```
06:34:50.866 ERROR Claude trade failed for DYDXUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
06:34:50.866 WARN  TRADE_SKIP sym=DYDXUSDT rsn=exception
06:34:50.953 WARN  XRAY_BLOCK sym=ETHUSDT quality=SKIP rr=0.2 | Trade rejected — structurally invalid
06:34:50.954 WARN  TRADE_SKIP sym=ETHUSDT rsn=xray_skip detail='quality=SKIP rr=0.20'

06:41:50.715 ERROR Claude trade failed for DYDXUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
06:41:51.104 WARN  XRAY_BLOCK sym=ETHUSDT quality=SKIP rr=0.2

06:48:20.718 ERROR Claude trade failed for RUNEUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
06:48:20.805 ERROR Claude trade failed for ETHUSDT:  ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
```

### 3.6 Slow tick warnings (full set in window)

```
06:05:40.943 BASE_WORKER_TICK_SLOW name=kline_worker     el=10942ms threshold_ms=8000
06:06:49.826 BASE_WORKER_TICK_SLOW name=altdata_worker   el=4824ms  threshold_ms=2000
06:10:45.990 BASE_WORKER_TICK_SLOW name=kline_worker     el=15988ms threshold_ms=8000
06:11:54.646 BASE_WORKER_TICK_SLOW name=altdata_worker   el=9645ms  threshold_ms=2000
06:15:40.019 BASE_WORKER_TICK_SLOW name=kline_worker     el=10018ms threshold_ms=8000
06:18:12.993 BASE_WORKER_TICK_SLOW name=cleanup_worker   el=5497ms  threshold_ms=2000
06:18:12.996 BASE_WORKER_TICK_SLOW name=news_worker      el=5518ms  threshold_ms=2000
06:20:50.515 BASE_WORKER_TICK_SLOW name=kline_worker     el=20513ms threshold_ms=8000
06:21:19.297 BASE_WORKER_TICK_SLOW name=regime_worker    el=4296ms  threshold_ms=4000
06:21:54.340 BASE_WORKER_TICK_SLOW name=altdata_worker   el=9340ms  threshold_ms=2000
06:25:39.754 BASE_WORKER_TICK_SLOW name=kline_worker     el=9752ms  threshold_ms=8000
06:26:20.379 BASE_WORKER_TICK_SLOW name=regime_worker    el=5378ms  threshold_ms=4000
06:26:50.000 BASE_WORKER_TICK_SLOW name=altdata_worker   el=4998ms  threshold_ms=2000
06:30:44.570 BASE_WORKER_TICK_SLOW name=kline_worker     el=14567ms threshold_ms=8000
06:31:54.148 BASE_WORKER_TICK_SLOW name=altdata_worker   el=9146ms  threshold_ms=2000
```

### 3.7 Sentiment aggregator state (sample)

```
06:21:00.628 SENT_UNKNOWN sym=LTCUSDT  rsn=no_news_no_reddit fg=47 change_24h=None
06:21:00.637 SENT_UNKNOWN sym=BCHUSDT  rsn=no_news_no_reddit fg=47 change_24h=None
06:21:00.668 SENT_UNKNOWN sym=ALICEUSDT rsn=no_news_no_reddit fg=47 change_24h=3.7545
06:21:00.668 SENT_NEUTRAL sym=ALICEUSDT rsn=no_news_no_reddit fg=47 change_24h=3.7545
06:21:00.671 SENT_AGG     sym=ALICEUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
```

`SENT_UNKNOWN_CACHE_HIT` was the highest-frequency tag in last 30 min (192 instances) — driven by missing reddit input.

### 3.8 Tag frequency last 30 min (workers.log)

```
192  SENT_UNKNOWN_CACHE_HIT
154  REGIME
150  SIG_GEN
108  SENT_AGG
 96  SENT_UNKNOWN
 96  SENT_NEUTRAL
 70  WD_TICK
 69  XRAY_ANALYZE
 30  SCANNER_HYSTERESIS
 20  XRAY_SCORE
 17  WM_START
 17  SWEET_SPOT_FIRED
 16  WORKER_FIRST_TICK
 15  PRICE_WS_HEALTH
 14  CAPITAL_TIER
 13  FUND_POOLS
 12  XRAY_CLASSIFY
 12  SYSTEM_HEALTH
 12  LAYER1A_TICK_DONE
 12  ENFORCER_BEAT
  8  ENFORCER_STATE
  8  BASE_WORKER_TICK_SLOW
  7  SWEET_SPOT_REGISTERED
  5  STRAT_CONSENSUS_CHANGE
  5  ENFORCER_GRACE
  3  XRAY_TICK_SUMMARY
  3  XRAY_SCANNER
  3  XRAY_CACHE_HEALTH
  3  STRAT_REGIME_DIST
  3  STRAT_PREFETCH
```

### 3.9 Heartbeat census at 06:23

```
profit_sniper          ticks=56  errors=0
position_watchdog      ticks=29  errors=0
price_alert_worker     ticks=29  errors=0
telegram_bot_worker    ticks=6   errors=0
scheduled_report       ticks=2   errors=0
enforcer_worker        ticks=6   errors=0
fund_manager_worker    ticks=6   errors=0
news_worker            ticks=2   errors=0
price_worker           ticks=8   errors=0
scanner_worker         ticks=2   errors=0  (just started post-resume)
```

All workers alive, zero error count. The TypeError at brain execution does not propagate as a worker error (caught at `_execute_new_trades`).

---

## 4. Database state snapshots

### 4.1 Data freshness at 06:23 UTC

```sql
table                    rows / latest_ts                        lag_s
klines                   190 distinct syms × TFs                 193
ticker_cache             200 rows                                 83
news_articles            1195                                   3495   ← 58 min stale
reddit_posts             0                                       n/a   ← never written
fear_greed_index         21358                                   217
funding_rates            202                                      79
open_interest            202                                      79
signals                  159358                                  133
aggregated_sentiment     (by symbol; many at 'unknown' level)     —
regime_history           (by symbol)                              —
active_universe          2 rows (BTC + ETH forced)                —
```

### 4.2 active_universe contents during run

```
06:24:00  BTCUSDT(0.0)  ETHUSDT(0.0)
06:29:00  DYDXUSDT(0.642)  BTCUSDT(0.0)  ETHUSDT(0.0)
06:34:00  BTCUSDT(0.0)  ETHUSDT(0.0)
06:39:00+ similar pattern
```

Enrichment columns (vol/change/fr/spread) all 0 per Finding #5.

### 4.3 Signals table — last 30 min distribution

```sql
SELECT signal_type, COUNT(*), AVG(confidence), MAX(created_at)
FROM signals WHERE created_at >= datetime('now','-30 minutes') GROUP BY signal_type;

signal_type   count   avg_conf   latest
neutral       3850    0.30       2026-04-27T06:21:00
```

Every signal NEUTRAL — Phase-29 confidence-gate works as designed but provides no directional input to ScannerWorker.

---

## 5. Pipeline state diagram (observed)

```
[Bybit WS] ─→ price_worker ─────→ ticker_cache  ✓ fresh (83s)
[Bybit REST] ─→ kline_worker ───→ klines        ✓ ok-but-slow ticks
[CoinAlpha/AlternativeMe] ─→ altdata_worker ──→ fear_greed/funding/oi  ✓ slow but ok
[CryptoPanic/RSS] ─→ news_worker ─→ news_articles  ✓ alive, feed slow
[Reddit] ─→ reddit_worker  ✗  NOT REGISTERED (no creds)

  ↓ (Layer 1A complete)

structure_worker (XRAY) ─→ in-memory _cache  ✓ 50% setups identified
                          ─→ XRAY_CLASSIFY emits setup_type per coin

signal_worker ─→ signals table  ✓ but ALL NEUTRAL (Phase-29 gate)

regime_worker ─→ regime_history  ✓ stable

  ↓ (Layer 1B complete)

strategy_worker ─→ EnsembleVoter ─→ consensus_setups (full 50)
                                  └→ apply_restrictions(filter) ─→ filtered
                                                                  └→ _build_per_coin_consensus(filtered) ✗ BUG #1
                                                                  └→ layer_manager._strategy_consensus = 5–18 entries

  ↓ (Layer 1C complete; cache sparse)

scanner_worker ─→ for each of 50 syms: _qualifies(symbol)
                  ├─ XRAY setup_type ?  ✓ many pass
                  ├─ consensus in {STRONG,GOOD} ?  ✗ 32–45 fail (cache sparse)
                  ├─ regime_aligns ?
                  ├─ rr_ratio >= 2.0 ?  ✗ many fail (legitimate market state)
                  └─ blockers ?
                ─→ qualified=0–2 + 2 forced ─→ packages 2–4 of 1.0–3.0 KB
                ─→ active_universe table (incomplete enrichment ─ BUG #5)

  ↓ (Layer 1D complete; selection starved)

Stage 2 / brain ─→ Claude reads packages ─→ trade decision
                                          └─ ShadowOrderService.place_order(purpose=…)  ✗ BUG #2 TypeError
                                          └─ TRADE_SKIP rsn=exception
                                          └─ 0 actual orders placed in 29 min
```

---

## 6. Recommended fix order (no fixes applied — observation only)

1. **Finding #2 (Shadow signature)** — single-file edit at `shadow_adapter.py:393`. Highest impact: unblocks every brain trade. CLAUDE.md root-cause rule applies (don't catch TypeError; fix signature).
2. **Finding #1 (StrategyWorker filter)** — single-line change at `strategy_worker.py:591`. Fills the consensus cache for all 50 coins, qualifying many more in Phase-5.
3. **Finding #3 (SCANNER_FILTER_AGGREGATE log)** — additive INFO line so future qualified=0 cycles are diagnosable without DEBUG.
4. **Finding #5 (active_universe enrichment)** — write the full enrichment dict at scanner write site.
5. **Finding #4 (Reddit creds)** — config; or explicit "disabled" log at startup.
6. **Finding #6 (altdata threshold)** — tune threshold or split fetcher.
7. **Finding #7 (MCP order misleading retMsg)** — out of scope but worth noting in Bybit error handling.

Per CLAUDE.md: every fix must be analysed-before-touched, not band-aided. None are applied here — observation report only.

---

## 7. Status when monitoring stopped (06:52 UTC)

- **All 16 workers in `manager.py` alive** with zero errors.
- **Layer 1A** healthy.
- **Layer 1B** healthy structurally; signal layer producing only NEUTRAL output (by design, but useless to scanner).
- **Layer 1C** producing partial consensus output (5-18 of 50 per cycle).
- **Layer 1D** producing 0-2 qualified picks per cycle (against plan target 5-25).
- **Stage 2 → execution** — 0 successful trades since restart due to Finding #2.

**Next scheduled cycles when monitoring stopped:**
- Strategy worker: 06:51:33
- Scanner worker: 06:54:00
- Layer 1A continues at native cadences.

End of report.
