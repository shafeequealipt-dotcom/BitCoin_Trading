# G7 — Worker Inventory

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Sources:**
  - Registration: `src/workers/manager.py:929-1378` (WorkerManager._create_workers)
  - Config gating: `config.toml` per section
  - Tier assignment: `src/workers/<name>.py:worker_tier = WorkerTier.LAYERnX`
  - Class: `src/workers/<name>.py`
  - Liveness aggregate: `WORKER_LIVENESS_HEARTBEAT total=19` (`workers.log`, e.g. line 5092 at 2026-04-27 23:18:41)
  - First-tick: `WORKER_FIRST_TICK | name=...` from `data/logs/workers.log` (current PID, 22:53:35-23:12 UTC) and `data/logs/workers.2026-04-27_01-31-00_169356.log`
  - Tick rate: `[HEARTBEAT] Worker 'X' alive | ticks=N | last_tick=...` lines in current `workers.log`. Each heartbeat re-emits the cumulative tick count for the current PID.

---

## Currently registered workers (19 total per WORKER_LIVENESS_HEARTBEAT)

The registrations executed in `manager.py._create_workers` (this session, current PID started at 22:53:35):

| # | Name | Tier | File location | Class | Config-gated by | First-tick (this PID) | Avg ticks/hour (24h) | Avg elapsed_ms / tick |
|---|---|---|---|---|---|---|---|---|
| 1 | `price_worker` | LAYER1A | `src/workers/price_worker.py` | `PriceWorker(BaseWorker)` | `_services["ws"]` present | 2026-04-27 22:53:40.962 (`el_to_first_tick_ms=576`) | continuous WS — heartbeat tick every ~45s = ~80/h | n/a (event-driven WS) |
| 2 | `kline_worker` | LAYER1A | `src/workers/kline_worker.py` | `KlineWorker(SweetSpotWorker)` | `_services["market"]` present | 2026-04-27 22:55:51.236 (`first_tick_el_ms=21235`) | 12/h (5-min sweet-spot) | 10433-21230 ms (M5+H1+H4+D1 mix) |
| 3 | `news_worker` | LAYER1A | `src/workers/news_worker.py` | `NewsWorker(BaseWorker)` | `_services["news"]` present (Finnhub `[finnhub].enabled=true`) | 2026-04-27 22:53:46.050 (`first_tick_el_ms=5086`) | ~12/h (news_interval=300s) | 1192-5518 ms |
| 4 | `altdata_worker` | LAYER1A | `src/workers/altdata_worker.py` | `AltDataWorker(SweetSpotWorker)` | any of `fear_greed`/`funding`/`oi`/`onchain` services available | 2026-04-27 22:56:54.192 (`first_tick_el_ms=9191`) | 12/h (5-min sweet-spot) | 4435-10137 ms |
| 5 | `signal_worker` | LAYER1B | `src/workers/signal_worker.py` | `SignalWorker(SweetSpotWorker)` | `ta`+`aggregator`+`signal_gen` services present | 2026-04-27 22:55:51 (estimated; first_tick line not separately captured this PID) | 12/h | 534-3352 ms |
| 6 | `regime_worker` | LAYER1B | `src/workers/regime_worker.py` | `RegimeWorker(SweetSpotWorker)` | `ta` service + scanner present | 2026-04-27 22:55:51 (estimated) | 12/h | 4044-9863 ms |
| 7 | `structure_worker` | LAYER1B | `src/workers/structure_worker.py` | `StructureWorker(SweetSpotWorker)` | `[analysis.structure].enabled=true` AND `structure_engine`+`structure_cache` services present | 2026-04-27 22:55:51 (estimated) | 12/h | 579-2303 ms |
| 8 | `strategy_worker` | LAYER1C | `src/workers/strategy_worker.py` | `StrategyWorker(SweetSpotWorker)` | `ta` + `scanner` + `regime_detector` services present | 2026-04-27 22:55:51 (estimated) | 12/h (cycle-gated) | 6011-6931 ms (TA), 8571-8870 ms (with prefetch_critical) |
| 9 | `scanner_worker` | LAYER1D | `src/workers/scanner_worker.py` | `ScannerWorker(SweetSpotWorker)` | `[scanner].enabled=true` AND `market_svc` present | 2026-04-27 22:53:35 register; 2026-04-27 22:09:00 prior PID first tick (current PID hadn't completed cycle by 23:00 — `LAYER1D_TICK_SKIP cycle_inactive` for 22:53+) | 12/h max (cycle-gated) | 19-66 ms |
| 10 | `position_watchdog` | UTILITY (BaseWorker, no tier set) | `src/workers/position_watchdog.py` | `PositionWatchdog(BaseWorker)` | `[watchdog].enabled=true` AND `position`+`market` services | 2026-04-27 22:53:41.592 (`first_tick_el_ms=627`) | 360/h (10s interval) | n/a in aggregate |
| 11 | `profit_sniper` | UTILITY | `src/workers/profit_sniper.py` | `ProfitSniper(BaseWorker)` | `[mode4].enabled=true` AND `position`+`market` services | 2026-04-27 22:53:41.567 (`first_tick_el_ms=600`) | 720/h (5s interval) | n/a |
| 12 | `enforcer_worker` | UTILITY | `src/workers/enforcer_worker.py` | `EnforcerWorker(BaseWorker)` | `[enforcer].enabled=true` | 2026-04-27 22:53:41.569 (`first_tick_el_ms=572`) | 60/h (60s interval) | n/a |
| 13 | `fund_manager_worker` | UTILITY | `src/workers/fund_manager_worker.py` | `FundManagerWorker(BaseWorker)` | `[fund_manager].enabled=true` | 2026-04-27 22:53:41.626 (`first_tick_el_ms=628`) | 60/h (60s interval) | n/a |
| 14 | `fund_reconciler` | UTILITY | `src/workers/fund_reconciler.py` | `FundReconciler(BaseWorker)` | `[fund_manager].reconcile_enabled=true` AND account_service present | 2026-04-27 22:53:41.627 (`first_tick_el_ms=627`) | 60/h | n/a |
| 15 | `cleanup_worker` | UTILITY | `src/workers/cleanup_worker.py` | `CleanupWorker(BaseWorker)` | always | 2026-04-27 22:53:46.040 (`first_tick_el_ms=5039`) | hourly | n/a |
| 16 | `telegram_bot_worker` | UTILITY | `src/workers/telegram_bot_worker.py` | `TelegramBotWorker(BaseWorker)` | `[telegram_interactive].enabled=true` | 2026-04-27 22:53:40.995 (`first_tick_el_ms=0`) | varies | n/a |
| 17 | `price_alert_worker` | UTILITY | `src/workers/price_alert_worker.py` | `PriceAlertWorker(BaseWorker)` | `[telegram_interactive].enabled=true` | 2026-04-27 22:53:41.563 (`first_tick_el_ms=567`) | 360/h (10s `[telegram_interactive].price_alert_check_interval=10`) | n/a |
| 18 | `scheduled_report_worker` | UTILITY | `src/workers/scheduled_report_worker.py` | `ScheduledReportWorker(BaseWorker)` | `[telegram_interactive].enabled=true` | 2026-04-27 22:53:41.565 (`first_tick_el_ms=569`) | varies (cron-scheduled) | n/a |
| 19 | `worker_liveness_watchdog` | UTILITY | `src/workers/worker_liveness_watchdog.py` | `WorkerLivenessWatchdog(BaseWorker)` | always (Phase 11 dead-workers fix) | 2026-04-27 22:53:41.002 (`first_tick_el_ms=1`) | 120/h (`[worker_liveness].watchdog_interval_sec=30`) | ~1 ms |

**Total: 19 workers** — matches `WORKER_LIVENESS_HEARTBEAT total=19`.

### 24h ticks/hour evidence

Heartbeat snapshot at 23:18:41 UTC reports `total=19 healthy=14 never_ticked=0 overdue=0 idle_cycle_gate=5 cycle_active=False`. The 5 `idle_cycle_gate` entries map to the 5 cycle-gated workers (kline_worker, structure_worker, signal_worker, regime_worker, strategy_worker, scanner_worker — all cycle_gated under L3=OFF). Wait — that's 6, not 5. Per `src/workers/scanner_worker.py:59 cycle_gated = True`, it is gated. But heartbeat says 5 idle. The 5 most likely are signal_worker, regime_worker, strategy_worker, scanner_worker, structure_worker (kline_worker is LAYER1A, may be exempt). **NOT FOUND** — exact gate-membership mapping for `idle_cycle_gate=5`.

Most-recent heartbeat tick counts (current PID, 22:53→23:00, ~7 min runtime):

| Worker | ticks (this PID) | minutes elapsed | impl. ticks/hour |
|---|---|---|---|
| profit_sniper | 296 | ~25 min | ≈710/h (cadence 5 s) |
| price_alert_worker | 149 | ~25 min | ≈360/h (10 s) |
| position_watchdog | 149 | ~25 min | ≈360/h (10 s) |
| worker_liveness_watchdog | 51 | ~25 min | ≈120/h (30 s) |
| price_worker | 29 | ~22 min | ≈80/h (45 s heartbeat) |
| telegram_bot_worker | 26 | ~25 min | ≈60/h (60 s) |
| fund_reconciler | 26 | ~25 min | ≈60/h (60 s) |
| fund_manager_worker | 26 | ~25 min | ≈60/h (60 s) |
| enforcer_worker | 26 | ~25 min | ≈60/h (60 s) |
| structure_worker | 7 | ~25 min | ≈12/h (5 min) |
| strategy_worker | 7 | ~25 min | ≈12/h (5 min) |
| signal_worker | 7 | ~25 min | ≈12/h (5 min) |
| scanner_worker | 7 | ~25 min | ≈12/h (5 min, but cycle-gated → many SKIP) |
| regime_worker | 7 | ~25 min | ≈12/h (5 min) |
| scheduled_report_worker | 6 | ~25 min | varies |
| news_worker | 6 | ~25 min | ≈12/h |
| altdata_worker | 5 | ~25 min | ≈12/h |
| kline_worker | 4 | ~22 min | ≈12/h |

Tick counts taken from grep `[HEARTBEAT] Worker '<name>' alive | ticks=N` lines, latest occurrence per worker name in current `workers.log`.

---

## NOT registered (manager.py never appends them)

The following 7 workers **exist as classes** but are **not registered** in this PID's WorkerManager:

| Worker | File | Class | Gating mechanism (verbatim) | Currently disabled? |
|---|---|---|---|---|
| `discovery_worker` | `src/workers/discovery_worker.py` | `DiscoveryWorker(BaseWorker)` | `manager.py:1195: if s.factory.enabled:` | **YES** — `[factory].enabled = false` (config.toml:545) |
| `live_monitor_worker` | `src/workers/live_monitor_worker.py` | `LiveMonitorWorker(BaseWorker)` | `manager.py:1195: if s.factory.enabled:` (same block) | **YES** — same `[factory].enabled = false` |
| `backtest_worker` | `src/workers/backtest_worker.py` | `BacktestWorker(BaseWorker)` | `manager.py:1195: if s.factory.enabled:` (same block) | **YES** — same `[factory].enabled = false` |
| `trial_monitor_worker` | `src/workers/trial_monitor_worker.py` | `TrialMonitorWorker(BaseWorker)` | `manager.py:1195: if s.factory.enabled:` (same block) | **YES** — same `[factory].enabled = false` |
| `reddit_worker` | `src/workers/reddit_worker.py` | `RedditWorker(BaseWorker)` | `manager.py:959: if self._services.get("reddit"):` — Reddit service is created only when `s.reddit.client_id` is set (manager.py:142). | **YES** — `[reddit].enabled = false` (config.toml:52) AND no `client_id` set (manager.py logs `REDDIT_DISABLED` WARNING at startup) |
| `optimization_worker` | `src/workers/optimization_worker.py` | `OptimizationWorker(BaseWorker)` | **No registration block in manager.py.** Comment at manager.py:1244 reads: *"AllocationWorker and OptimizationWorker removed — replaced by IntelligentFundManager (M1, M8 modules)"*. Class file still exists but is never instantiated. | **YES** — code path removed |
| `allocation_worker` | `src/workers/allocation_worker.py` | `AllocationWorker(BaseWorker)` | Same as above — removed at manager.py:1244 | **YES** — code path removed |

### Confirmation per worker

- `factory`-gated four (discovery, live_monitor, backtest, trial_monitor): all four registered together in the `if s.factory.enabled:` block at manager.py:1195-1227. Config `[factory].enabled = false  # Disabled: 0 patterns discovered, 0 backtests run — wasting CPU` (config.toml:545). **Intentional**.
- `reddit_worker`: gated by `_services.get("reddit")` at manager.py:959. Reddit service skipped at manager.py:141-156 because either `reddit.client_id` is unset or `[reddit].enabled = false`. The startup log emits `REDDIT_DISABLED | reason=no_credentials | impact=sentiment_degraded` (warning, manager.py:153-156). **Intentional** — config.toml:52 explicitly sets `[reddit].enabled = false`.
- `optimization_worker`, `allocation_worker`: **no registration code path exists in manager.py at all**. Replaced by `IntelligentFundManager` (M1/M8 modules) per the comment at line 1244. The class files (`src/workers/optimization_worker.py`, `src/workers/allocation_worker.py`) are dead code by registration but still importable. **Intentional removal**.

### NOT FOUND — `live_monitor_worker` log lines

Searched `data/logs/workers.log` and `data/logs/workers.2026-04-27_01-31-00_169356.log` for any line containing `DiscoveryWorker|OptimizationWorker|RedditWorker|LiveMonitorWorker|TrialMonitorWorker|BacktestWorker|AllocationWorker`. Zero matches. Confirms registration did not occur.

---

## Tier breakdown

| Tier | Workers | Count |
|---|---|---|
| LAYER1A (always-on data) | price_worker, kline_worker, news_worker, altdata_worker | 4 |
| LAYER1B (analyzers) | signal_worker, regime_worker, structure_worker | 3 |
| LAYER1C (strategy pipeline) | strategy_worker | 1 |
| LAYER1D (smart scanner) | scanner_worker | 1 |
| UTILITY (no `worker_tier`) | position_watchdog, profit_sniper, enforcer_worker, fund_manager_worker, fund_reconciler, cleanup_worker, telegram_bot_worker, price_alert_worker, scheduled_report_worker, worker_liveness_watchdog | 10 |
| **Total** | | **19** |

(LAYER4 — `Tier=4` per memory note for some legacy classification — does not have any *currently registered* worker assigned `worker_tier = WorkerTier.LAYER4` per grep of `src/workers/*.py`. The Layer 1 sub-layers map to LAYER1A/1B/1C/1D only.)
