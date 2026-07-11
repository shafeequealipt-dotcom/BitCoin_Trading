"""Worker Manager: creates, starts, monitors, and gracefully stops all workers."""

import asyncio
import signal as signal_mod

from src.config.settings import Settings
from src.core.health_monitor import SystemHealthMonitor
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations
from src.workers.base_worker import BaseWorker
from src.workers.health import WorkerHealthMonitor

log = get_logger("worker")

# Phase 11 (logging overhaul): interval between SYSTEM_HEALTH probes.
# 60s matches the spec default; cheap enough to run this often without
# adding meaningful load.
_SYSTEM_HEALTH_INTERVAL_SECONDS: float = 60.0


class WorkerManager:
    """Orchestrates all background worker lifecycle.

    Creates service dependencies, initializes workers, starts them
    concurrently, and handles graceful shutdown.

    Args:
        settings: Application settings.
        db: Database manager.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self.settings = settings
        self.db = db
        self.health = WorkerHealthMonitor()
        # Phase 11: event-loop / process-level health probe. Distinct from
        # self.health (worker-registry) — see src/core/health_monitor.py.
        self._system_health = SystemHealthMonitor()
        # Phase 11 (dead-workers fix): per-worker liveness tracker. The
        # watchdog (appended in initialize()) probes this every 30 s and
        # emits WORKER_NEVER_TICKED / WORKER_TICK_OVERDUE warnings.
        # Set as the module-level singleton so BaseWorker.start and
        # SweetSpotScheduler.wait_for_sweet_spot record into the same
        # instance — see src/core/worker_liveness.py for the design.
        from src.core.worker_liveness import (
            WorkerLivenessTracker,
            set_default_tracker,
        )
        self._worker_liveness = WorkerLivenessTracker()
        set_default_tracker(self._worker_liveness)
        self.workers: list[BaseWorker] = []
        self.tasks: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()
        self._services: dict = {}
        self._services["worker_liveness"] = self._worker_liveness

    async def initialize(self) -> None:
        """Create all service dependencies and workers.

        Connects to APIs, initializes services, and creates worker instances.
        """
        # Connect database and run migrations
        await self.db.connect()
        await run_migrations(self.db)

        settings = self.settings
        db = self.db

        # J6 cross-check (2026-05-14) — register DatabaseManager in the
        # service container under the canonical "db" key. Pre-J6 the
        # apex_gate.validate path (and the fund_manager subpaths in
        # strategy_budgets.py:54 / ecosystem_health.py:181) consulted
        # ``self._services.get("db")`` and silently degraded to no-op
        # because no caller had registered it. Wiring it once here
        # makes the existing consumers functional. Originally added to
        # unblock the J6 learning gate's DB query; that gate was
        # removed in issue3/p3-3 but ``db`` is still consumed by the
        # other paths above so the wiring stays.
        self._services["db"] = db

        # --- Layer 1 restructure Phase 1: cycle latency tracker ---
        # Created early so any subsequent service that wants to publish
        # cycle markers can pick it up from ``self._services["cycle_tracker"]``.
        # Hourly flush task starts inside ``start_all`` so the database is
        # already migrated by then.
        try:
            from src.core.cycle_tracker import CycleTracker
            cycle_tracker = CycleTracker(
                db,
                max_history=settings.observability.cycle_tracker_history,
            )
            self._services["cycle_tracker"] = cycle_tracker
        except Exception as e:
            log.warning("CycleTracker init failed: {err}", err=str(e))
            self._services["cycle_tracker"] = None

        # --- Transformer (state machine — T1) ---
        try:
            from src.core.transformer import Transformer
            transformer = Transformer(db=db, config=settings)
            await transformer.initialize()
            self._services["transformer"] = transformer
            log.info("Transformer: mode={mode}", mode=transformer.current_mode.upper())
        except Exception as e:
            log.warning("Transformer init failed: {err}", err=str(e))

        # --- Create services ---
        # Trading
        try:
            from src.trading.client import BybitClient
            from src.trading.websocket import BybitWebSocket
            from src.trading.services.market_service import MarketService

            bybit = BybitClient(settings, db)
            await bybit.connect()
            ws = BybitWebSocket(settings, db)
            market_svc = MarketService(
                bybit,
                db,
                kline_save_chunk_size=settings.database.kline_save_chunk_size,
            )
            self._services["bybit"] = bybit
            self._services["ws"] = ws
            self._services["market"] = market_svc
            self._services["market_service"] = market_svc
        except Exception as e:
            log.warning("Bybit services unavailable: {err}", err=str(e))
            self._services["bybit"] = None
            self._services["ws"] = None
            self._services["market"] = None

        # Intelligence
        try:
            from src.intelligence.sentiment.scorer import SentimentScorer
            from src.intelligence.news.finnhub_client import FinnhubClient
            from src.intelligence.news.news_service import NewsService
            from src.intelligence.news.calendar_service import CalendarService
            from src.intelligence.sentiment.reddit_client import RedditClient
            from src.intelligence.sentiment.reddit_service import RedditService
            from src.intelligence.sentiment.aggregator import SentimentAggregator
            from src.intelligence.altdata.fear_greed import FearGreedClient
            from src.intelligence.altdata.funding_rates import FundingRateTracker
            from src.intelligence.altdata.open_interest import OpenInterestTracker
            from src.intelligence.altdata.onchain import OnChainClient
            from src.intelligence.signals.signal_generator import SignalGenerator

            scorer = SentimentScorer()
            finnhub = FinnhubClient(settings)
            news_svc = NewsService(finnhub, scorer, db, settings)
            calendar_svc = CalendarService(finnhub, db)
            reddit_svc = None
            if getattr(settings, "reddit", None) and settings.reddit.client_id:
                reddit_client = RedditClient(settings)
                reddit_svc = RedditService(reddit_client, scorer, db, settings)
            else:
                # Phase 10 (post-Layer-1 fix). Promoted from INFO to
                # WARNING + structured event tag. INFO buried the
                # disable notice in normal traffic; operators couldn't
                # quickly see whether Reddit was intentionally off vs
                # silently broken. The downstream aggregator picks up
                # the same disabled state via settings introspection
                # and suppresses ~600/hour per-coin SENT_UNKNOWN noise.
                log.warning(
                    "REDDIT_DISABLED | reason=no_credentials "
                    "| impact=sentiment_degraded"
                )
            fear_greed = FearGreedClient(settings, db)
            funding = FundingRateTracker(self._services["bybit"], db) if self._services.get("bybit") else None
            oi_tracker = OpenInterestTracker(self._services["bybit"], db) if self._services.get("bybit") else None
            onchain = OnChainClient(settings, db)
            # Phase 10 (post-Layer-1 fix). Pass settings so the aggregator
            # can detect intentionally-disabled Reddit and suppress the
            # ~600/hour per-coin SENT_UNKNOWN spam.
            aggregator = SentimentAggregator(db, scorer, settings)
            # Phase 1 (output-quality): pass settings so the multi-source
            # classifier reads thresholds from [signal_generator.multi_source]
            # in config.toml. Falls back to dataclass defaults if missing.
            signal_gen = SignalGenerator(aggregator, db, settings=settings)

            self._services.update({
                "news": news_svc, "calendar": calendar_svc,
                "reddit": reddit_svc, "fear_greed": fear_greed,
                "funding": funding, "oi": oi_tracker, "onchain": onchain,
                "aggregator": aggregator, "signal_gen": signal_gen,
            })
        except Exception as e:
            log.warning("Intelligence services unavailable: {err}", err=str(e))

        # TA Engine (wrapped with cache to eliminate duplicate computation)
        try:
            from src.analysis.engine import TAEngine
            from src.analysis.ta_cache import TACache
            ta_engine_raw = TAEngine(db, settings=self.settings)
            # TTL=120s spans two StrategyWorker cycles (45s each) so H1 TA
            # pre-populated by the worker's prefetch stays hot for the next
            # strategist prompt build. H1 data changes at bar close (hourly),
            # so 120s staleness is trivial. Bumped from 30s in the Prefetch-
            # Performance Fix — see PREFETCH_PERFORMANCE_AND_OBSERVABILITY_FIX.
            ta_cache = TACache(ta_engine_raw, ttl_seconds=120.0)
            self._services["ta"] = ta_cache
            self._services["ta_engine"] = ta_cache
            self._services["ta_cache"] = ta_cache
            self._services["ta_raw"] = ta_engine_raw
        except Exception as e:
            log.warning("TA Engine unavailable: {err}", err=str(e))
            self._services["ta"] = None

        # Volatility Profiler — per-coin adaptive TP/SL/hold parameters
        try:
            from src.analysis.volatility_profile import VolatilityProfiler
            vp_settings = getattr(settings, "volatility_profile", None)
            if vp_settings and vp_settings.enabled:
                volatility_profiler = VolatilityProfiler(
                    ta_cache=self._services.get("ta"),
                    regime_detector=None,  # Late-wired after RegimeDetector creation
                    settings=vp_settings,
                )
                self._services["volatility_profiler"] = volatility_profiler
                log.info("VolatilityProfiler initialized (TTL={ttl}s)", ttl=vp_settings.cache_ttl_seconds)
        except Exception as e:
            log.warning("VolatilityProfiler unavailable: {err}", err=str(e))

        # Dynamic Adaptive Exit System (2026-06-15) — boot sentinel. The
        # R-and-fee geometry lives in src/analysis/vol_scale.py and reads the
        # [adaptive_exit] config; this one-shot line confirms the section loaded
        # and shows the live coefficients. `enabled` gates whether any consumer
        # reads them (dormant until flipped on after per-commit verification).
        try:
            _ae = getattr(settings, "adaptive_exit", None)
            if _ae is not None:
                log.info(
                    f"ADAPTIVE_EXIT_CONFIG | enabled={_ae.enabled} "
                    f"fee_floor_pct={_ae.round_trip_fee_pct * _ae.fee_floor_buffer:.3f} "
                    f"arm_r={_ae.arm_r} rung_r={list(_ae.rung_r)} trail_r={_ae.trail_r} "
                    f"trail_r_floor={getattr(_ae, 'trail_r_floor', _ae.trail_r)} "
                    f"trail_knee_r={getattr(_ae, 'trail_tighten_knee_r', 1.0)} "
                    f"trail_scale_r={getattr(_ae, 'trail_tighten_scale_r', 1.0)} "
                    f"trail_tighten={'ON' if getattr(_ae, 'trail_r_floor', _ae.trail_r) < _ae.trail_r else 'inert'} "
                    f"hard_stop_r={_ae.hard_stop_r} r_alpha={_ae.r_smoothing_alpha} "
                    f"drifter={_ae.dead_drifter_enabled}@{_ae.dead_drifter_age_fraction} | "
                    f"config_sentinel"
                )
        except Exception as e:
            log.warning(f"ADAPTIVE_EXIT_CONFIG_FAIL | err='{str(e)[:120]}'")

        # X-RAY Structural Intelligence
        try:
            if settings.structure.enabled:
                from src.analysis.structure.structure_engine import StructureEngine
                from src.analysis.structure.structure_cache import StructureCache
                structure_engine = StructureEngine(settings.structure)
                structure_cache = StructureCache(
                    ttl_seconds=float(settings.structure.cache_ttl_seconds),
                )
                self._services["structure_engine"] = structure_engine
                self._services["structure_cache"] = structure_cache

                # X-RAY shadow.db reader for the structure_worker fallback path.
                # (Phase 6 cleanup: CoinDiscovery and the scan_full_market gate
                # were removed in this commit — structure_worker now reads
                # ScannerWorker.get_active_universe() exclusively.)
                try:
                    from src.analysis.structure.shadow_kline_reader import ShadowKlineReader
                    shadow_path = settings.structure.shadow_db_path
                    shadow_reader = ShadowKlineReader(shadow_db_path=shadow_path)
                    # Eager open of the persistent read-only connection.
                    # Failure here raises DatabaseError; the surrounding
                    # try/except logs "X-RAY shadow_kline_reader unavailable"
                    # and leaves shadow_kline_reader unregistered, so
                    # StructureWorker silently bypasses the fallback.
                    await shadow_reader.connect()
                    self._services["shadow_kline_reader"] = shadow_reader
                    log.info(
                        "X-RAY: shadow_kline_reader ready (Shadow DB: {path})",
                        path=shadow_path,
                    )
                except Exception as e:
                    log.warning("X-RAY shadow_kline_reader unavailable: {err}", err=str(e))

                log.info(
                    "X-RAY: StructureEngine initialized (TTL={ttl}s)",
                    ttl=settings.structure.cache_ttl_seconds,
                )
        except Exception as e:
            log.warning("X-RAY unavailable: {err}", err=str(e))

        # --- T3: Create BOTH service sets and route through Transformer ---

        # Bybit services (always created if bybit client is available)
        bybit_order = None
        bybit_position = None
        bybit_account = None
        if self._services.get("bybit"):
            try:
                from src.trading.services.position_service import PositionService
                from src.trading.services.order_service import OrderService
                from src.trading.services.account_service import AccountService

                bybit_client = self._services["bybit"]
                bybit_position = PositionService(bybit_client, db, settings)
                bybit_order = OrderService(bybit_client, db, settings)
                bybit_account = AccountService(bybit_client, db)
                log.info("Bybit services: created")

                # Instrument service for qty rounding
                try:
                    from src.trading.services.instrument_service import InstrumentService
                    inst_svc = InstrumentService(bybit_client)
                    self._services["instrument_service"] = inst_svc
                except Exception as e:
                    log.debug("init instrument service failed: {err}", err=str(e))
            except Exception as e:
                log.warning("Bybit trading services unavailable: {err}", err=str(e))

        # Shadow adapters (always created)
        shadow_order = None
        shadow_position = None
        shadow_account = None
        try:
            import aiohttp
            from src.shadow.shadow_adapter import (
                ShadowOrderService, ShadowPositionService, ShadowAccountService,
            )
            shadow_url = getattr(settings.general, "shadow_api_url", "http://127.0.0.1:9090")
            self._shadow_session = aiohttp.ClientSession()
            shadow_position = ShadowPositionService(self._shadow_session, shadow_url)
            shadow_order = ShadowOrderService(self._shadow_session, shadow_url)
            shadow_account = ShadowAccountService(self._shadow_session, shadow_url)
            log.info("Shadow adapters: created (API: {url})", url=shadow_url)
        except Exception as e:
            log.warning("Shadow adapters unavailable: {err}", err=str(e))

        # Bybit demo adapters (paper-money exec via api-demo.bybit.com).
        # Created only when settings.bybit_demo.enabled is True. The
        # adapter mirrors Shadow's contract — three service classes
        # returning Order/Position/AccountInfo dataclasses, never raises.
        # See dev_notes/bybit_demo_adapter/phase1_synthesis.md.
        bybit_demo_order = None
        bybit_demo_position = None
        bybit_demo_account = None
        bd_settings = getattr(settings, "bybit_demo", None)
        if bd_settings is not None and bd_settings.enabled:
            try:
                import aiohttp
                from src.bybit_demo import (
                    BybitDemoAccountService,
                    BybitDemoClient,
                    BybitDemoOrderService,
                    BybitDemoPositionService,
                )
                # Reuse the shared session if already created for Shadow;
                # otherwise create a session dedicated to bybit_demo.
                if getattr(self, "_shadow_session", None) is None:
                    self._shadow_session = aiohttp.ClientSession()
                bd_session = self._shadow_session

                if not bd_settings.api_key or not bd_settings.api_secret:
                    log.warning(
                        "Bybit demo adapter: credentials missing — "
                        "set BYBIT_DEMO_API_KEY and BYBIT_DEMO_API_SECRET. "
                        "Adapter will instantiate but every call will fail."
                    )

                bd_client = BybitDemoClient(
                    session=bd_session,
                    base_url=bd_settings.base_url,
                    api_key=bd_settings.api_key,
                    api_secret=bd_settings.api_secret,
                    recv_window=bd_settings.recv_window,
                    timeout_seconds=bd_settings.timeout_seconds,
                    retry_attempts=bd_settings.retry_attempts,
                    retry_base_delay_seconds=bd_settings.retry_base_delay_seconds,
                )
                # P7 of P1-P10: inject TradingRepository so demo
                # place_order / close_position write through to
                # trading.db (orders, trade_history, positions tables)
                # — same persistence contract as live OrderService /
                # PositionService. Pre-P7 these tables were empty in
                # bybit_demo mode; fund_manager.momentum_allocator and
                # alert_manager silently degraded to "no trade history".
                from src.database.repositories.trading_repo import (
                    TradingRepository,
                )
                _bd_trading_repo = TradingRepository(db)
                # CRITICAL-3 fix — expose the bybit_demo TradingRepository
                # in services so the new _trade_history_close_callback
                # (registered after the coordinator wiring around line
                # 1906) can persist trade_history rows for ALL bybit_demo
                # closes (system-initiated AND WS-only). Pre-fix the repo
                # was held only by the order/position adapters and the
                # adapter wrote trade_history directly with a colliding
                # trade_id — see c3_phase1_2_report.md.
                self._services["bybit_demo_trading_repo"] = _bd_trading_repo
                bybit_demo_order = BybitDemoOrderService(
                    bd_client, trading_repo=_bd_trading_repo,
                )
                bybit_demo_position = BybitDemoPositionService(
                    bd_client,
                    trading_repo=_bd_trading_repo,
                    # T1-4 (2026-05-12): pass the live InstrumentService so
                    # reduce_position can floor-quantize qty to qtyStep
                    # before POSTing to Bybit V5. Singleton constructed at
                    # ~line 274; .get() returns None when Bybit live wiring
                    # failed at boot — adapter handles None gracefully via
                    # BYBIT_DEMO_QTY_QUANTIZE_UNAVAILABLE + full-close
                    # fallback rather than silently submitting raw qty.
                    instrument_service=self._services.get("instrument_service"),
                )
                bybit_demo_account = BybitDemoAccountService(bd_client)
                log.info(
                    "Bybit demo adapters: created (API: {url})",
                    url=bd_settings.base_url,
                )

                # Boot validation — emits BYBIT_DEMO_BOOT_START /
                # _VALIDATED / _FAIL via the bybit_demo logger. Never
                # raises (boot must come up). The result is stashed so
                # the alert-relay wiring below can dispatch a one-shot
                # CRITICAL Telegram alert once AlertManager exists
                # (it isn't constructed until line ~466, well after
                # this point).
                try:
                    from src.bybit_demo.bybit_demo_boot import validate_boot
                    self._services["bybit_demo_boot_result"] = await validate_boot(
                        bd_client,
                        base_url=bd_settings.base_url,
                        api_key_len=len(bd_settings.api_key or ""),
                        recv_window=bd_settings.recv_window,
                    )
                except Exception as e:
                    # Defense in depth — validate_boot is designed to
                    # never raise, but if it ever does we must not
                    # block boot.
                    log.warning(
                        "Bybit demo boot validation crashed: {err}",
                        err=str(e),
                    )
                    self._services["bybit_demo_boot_result"] = {
                        "ok": False,
                        "step": "exception",
                        "err": str(e)[:160],
                    }
            except Exception as e:
                log.warning("Bybit demo adapters unavailable: {err}", err=str(e))
        else:
            log.info(
                "Bybit demo adapters: not enabled "
                "(set [bybit_demo].enabled=true in config.toml + provide creds)"
            )

        # Feed all three sets to the Transformer and create proxies
        transformer = self._services.get("transformer")
        if transformer:
            transformer.set_services(
                shadow_order=shadow_order,
                shadow_position=shadow_position,
                shadow_account=shadow_account,
                bybit_order=bybit_order,
                bybit_position=bybit_position,
                bybit_account=bybit_account,
                bybit_demo_order=bybit_demo_order,
                bybit_demo_position=bybit_demo_position,
                bybit_demo_account=bybit_demo_account,
            )
            # Re-initialize to set active services based on DB mode
            await transformer.initialize()

            proxies = transformer.create_proxies()
            pos_svc = proxies["position"]
            ord_svc = proxies["order"]
            acc_svc = proxies["account"]
        else:
            # Fallback if Transformer failed — use Shadow or Bybit directly
            log.warning("Transformer not available — using direct services")
            pos_svc = shadow_position or bybit_position
            ord_svc = shadow_order or bybit_order
            acc_svc = shadow_account or bybit_account

        self._services["position"] = pos_svc
        self._services["order"] = ord_svc
        self._services["account"] = acc_svc
        self._services["position_service"] = pos_svc
        self._services["order_service"] = ord_svc
        self._services["account_service"] = acc_svc

        # Brain services — provider-switched (2026-07-06, operator request).
        # "glm_cloudflare" replaces Claude entirely with GLM-5.2 via Cloudflare
        # Workers AI (src/brain/glm_client.py); "claude_code" (the prior
        # default) keeps the battle-tested Claude Max subscription path. Set
        # [brain] provider back to "claude_code" in config.toml to revert.
        # Either way the client lands in services["claude_client"] under the
        # same key — every other module (watchdog, sniper, telegram handlers)
        # reads that key generically and doesn't know which provider it is.
        try:
            from src.brain.claude_code_client import ClaudeCodeCostTracker
            from src.brain.decision_parser import DecisionParser

            _brain_cfg = self.settings.brain
            cost_tracker = ClaudeCodeCostTracker()
            decision_parser = DecisionParser()
            self._services["cost_tracker"] = cost_tracker
            self._services["decision_parser"] = decision_parser

            if _brain_cfg.provider == "glm_cloudflare":
                from src.brain.glm_client import GLMClient

                claude_client = GLMClient(
                    api_key=_brain_cfg.glm_api_key,
                    account_id=_brain_cfg.glm_account_id,
                    model=_brain_cfg.glm_model,
                    timeout_seconds=_brain_cfg.glm_timeout_seconds,
                    max_tokens=_brain_cfg.glm_max_tokens,
                    temperature=_brain_cfg.glm_temperature,
                    max_retries=_brain_cfg.glm_max_retries,
                )
                self._services["claude_client"] = claude_client
                log.info(
                    f"Using GLM-5.2 via Cloudflare Workers AI as brain provider "
                    f"(model={_brain_cfg.glm_model})"
                )
            else:
                from src.brain.claude_client import ClaudeClient

                claude_client = ClaudeClient(
                    settings=self.settings,
                    cost_tracker=cost_tracker,
                )
                self._services["claude_client"] = claude_client
                log.info(
                    f"Using OpenRouter API client (model={claude_client.model})"
                )
        except Exception as e:
            log.warning("Brain services unavailable: {err}", err=str(e))

        # Price formatter (display precision) — the single seam that renders
        # prices at exact exchange tick size (via the InstrumentService cache)
        # with magnitude-aware fallback. Built before AlertManager so the alert
        # templates render at the same precision as the dashboard. The resolver
        # is the ONLY coupling between the display layer and instrument
        # metadata; if instrument_service is unavailable the formatter falls
        # back to magnitude precision (tick_resolver=false in the sentinel).
        _price_fmt = None
        try:
            from src.core.log_tags import PRICE_FORMATTER_WIRED
            from src.core.price_formatter import PriceFormatter
            _inst_svc = self._services.get("instrument_service")
            _price_fmt = PriceFormatter(
                decimals_resolver=_inst_svc.price_decimals if _inst_svc is not None else None
            )
            self._services["price_formatter"] = _price_fmt
            log.info(
                f"{PRICE_FORMATTER_WIRED} | tick_resolver={_price_fmt.has_tick_resolver} | {ctx()}"
            )
        except Exception as e:
            log.warning("PriceFormatter wiring failed: {err}", err=str(e))
            _price_fmt = None

        # Alert Manager
        # If interactive bot is enabled, AlertManager's bot connection will be
        # wired later by InteractiveTelegramBot.start() — unified single connection.
        # If interactive bot is disabled, AlertManager connects its own bot (fallback).
        try:
            from src.alerts.alert_manager import AlertManager
            # Win-rate enhancement Phase E (2026-07-07): pass services so the
            # daily scorecard can read live apex_gate entry-quality counters.
            alert_mgr = AlertManager(
                settings, db, price_formatter=_price_fmt, services=self._services,
            )
            interactive_enabled = hasattr(settings, 'telegram_interactive') and settings.telegram_interactive.enabled
            if not interactive_enabled:
                await alert_mgr.initialize()
            else:
                alert_mgr.enabled = settings.alerts.telegram_enabled
            self._services["alert_manager"] = alert_mgr
            # Phase E: start_daily_summary_scheduler had no caller anywhere in
            # the codebase — [alerts].daily_summary=true never actually fired
            # a summary. Wire it in, gated on the same config flag.
            if settings.alerts.daily_summary:
                await alert_mgr.start_daily_summary_scheduler()
                log.info(
                    f"DAILY_SUMMARY_SCHEDULER_START | time={settings.alerts.daily_summary_time} UTC"
                )
        except Exception as e:
            log.warning("AlertManager unavailable: {err}", err=str(e))

        # OpenRouter-based brain client — no OAuth login needed, so no
        # Telegram auth-alert callback (that was for ClaudeCodeClient CLI).

        # Risk Manager
        try:
            from src.risk.risk_manager import RiskManager
            risk_mgr = RiskManager(settings, db, self._services)
            await risk_mgr.initialize()
            self._services["risk_manager"] = risk_mgr
        except Exception as e:
            log.warning("RiskManager unavailable: {err}", err=str(e))

        # FreshnessGuard — prevents trading on stale data
        from src.core.freshness_guard import FreshnessGuard
        freshness_guard = FreshnessGuard(db, self._services)
        self._services["freshness_guard"] = freshness_guard

        # TradeCoordinator — shared state between Brain, Watchdog, Enforcer
        from src.core.trade_coordinator import TradeCoordinator
        trade_coordinator = TradeCoordinator()
        self._services["trade_coordinator"] = trade_coordinator

        # P2 of P1-P10: wire transformer into coordinator so its
        # pop_close_reason fallback returns the mode-aware string
        # f"{current_mode}_sl_tp" instead of the audit-flagged literal
        # "shadow_sl_tp". Late-bound (attach_transformer) avoids the
        # circular DI between Transformer and TradeCoordinator.
        _transformer = self._services.get("transformer")
        if _transformer is not None:
            trade_coordinator.attach_transformer(_transformer)

        # PF/LC Top-15 Problem 1.3 (2026-06-04) — wire the position-service
        # proxy (the same _PositionProxy the watchdog uses and that
        # resolves authoritative net PnL via get_last_close →
        # /v5/position/closed-pnl) into the coordinator so the WebSocket
        # self-close path books the exchange's real net closedPnl instead
        # of a gross fee-free fallback. The proxy was set into
        # self._services["position"] earlier in this build, so it exists here.
        _pos_proxy = self._services.get("position")
        if _pos_proxy is not None:
            trade_coordinator.attach_position_service(_pos_proxy)
        # Phantom-loss fix (2026-06-05) Commit 3: wire the price-decimals
        # resolver so the staleness gate's exit tolerance is exact per
        # instrument (sub-cent coins included). Late-bound, like the others.
        _inst_for_gate = self._services.get("instrument_service")
        if _inst_for_gate is not None and hasattr(
            trade_coordinator, "attach_tick_resolver"
        ):
            trade_coordinator.attach_tick_resolver(_inst_for_gate.price_decimals)

        # Issue 3 (5-min reentry cooldown, 2026-05-18) — wire APEX
        # operator-configurable cooldown duration into the coordinator.
        # The coordinator clamps non-positive values to its 300s default
        # so a misconfigured override cannot silently disable the
        # cooldown.
        try:
            _apex_settings = getattr(self.settings, "apex", None)
            if _apex_settings is not None:
                _cooldown_s = int(
                    getattr(_apex_settings, "reentry_cooldown_seconds", 300)
                    or 300,
                )
                trade_coordinator.set_reentry_cooldown_seconds(_cooldown_s)
                # F9 (2026-06-09): loss-only cooldown + selection exclusion.
                _loss_cd = bool(
                    getattr(_apex_settings, "loss_cooldown_enabled", False)
                )
                if hasattr(trade_coordinator, "set_loss_cooldown_enabled"):
                    trade_coordinator.set_loss_cooldown_enabled(_loss_cd)
                log.info(
                    f"BOOT_REENTRY_COOLDOWN_WIRED | cooldown_sec={_cooldown_s} "
                    f"loss_only={_loss_cd} | {ctx()}"
                )
        except Exception as _e:
            log.warning(
                f"BOOT_REENTRY_COOLDOWN_WIRE_FAIL | err='{str(_e)[:80]}' | "
                f"{ctx()}"
            )
        # F5 centralization (2026-06-09): wire the MARK-referenced exit-divergence
        # band into the coordinator from the SAME centralized key the reconciler
        # reads ([bybit_demo].close_pnl_reconcile_max_exit_divergence_pct), so the
        # coordinator staleness gates and the reconciler exit-plausibility gate are
        # one source of truth and tunable from config without a code edit. Read
        # from bybit_demo (a different group than apex), with its own fail-safe so
        # the coordinator's 3.0 default stands if wiring fails.
        try:
            _bd_settings = getattr(self.settings, "bybit_demo", None)
            if _bd_settings is not None and hasattr(
                trade_coordinator, "set_close_exit_divergence_pct"
            ):
                _div_pct = float(
                    getattr(
                        _bd_settings,
                        "close_pnl_reconcile_max_exit_divergence_pct",
                        3.0,
                    )
                    or 0.0
                )
                trade_coordinator.set_close_exit_divergence_pct(_div_pct)
                log.info(
                    f"BOOT_CLOSE_EXIT_DIVERGENCE_WIRED | divergence_pct={_div_pct} "
                    f"| coordinator + reconciler now share one centralized band "
                    f"| {ctx()}"
                )
        except Exception as _e:
            log.warning(
                f"BOOT_CLOSE_EXIT_DIVERGENCE_WIRE_FAIL | err='{str(_e)[:80]}' | "
                f"{ctx()}"
            )

        # Issue I5 (F-32, 2026-05-14) — restart-resilient state recovery.
        # On boot, read open theses from trade_thesis and rebuild the
        # coordinator's in-memory _trades map so the dashboard reflects
        # accumulated state instead of zeros after a SEGV or graceful
        # restart. Best-effort: any failure logs and continues with the
        # empty map (the watchdog's WD_CLOSE_THESIS_RECOVERY safety net
        # still catches up reactively).
        try:
            _restored = await trade_coordinator.recover_state_from_db(self.db)
            if _restored:
                log.info(
                    f"BOOT_STATE_RECOVERED | scope=trade_coordinator "
                    f"restored={_restored} | {ctx()}"
                )
        except Exception as _se:
            log.warning(
                f"BOOT_STATE_RECOVER_FAIL | scope=trade_coordinator "
                f"err='{str(_se)[:120]}' | {ctx()}"
            )

        # Attach coordinator to PositionService for close notifications
        pos_svc = self._services.get("position")
        if pos_svc:
            pos_svc.coordinator = trade_coordinator

        # Issue 4 fix (2026-05-11) — attach coordinator to the
        # BybitDemoPositionService so reduce_position can call
        # mark_partial_close_pending BEFORE its reduceOnly POST. Without
        # this, the WS subscriber would route the partial fill through
        # the full on_trade_closed path (Issue 4 pre-fix behaviour).
        # bybit_demo_position is None when the demo adapter wasn't
        # configured at boot; attach_coordinator is only called when the
        # adapter is present and exposes the method.
        if bybit_demo_position is not None and hasattr(
            bybit_demo_position, "attach_coordinator"
        ):
            bybit_demo_position.attach_coordinator(trade_coordinator)

        # J2 (2026-05-14) — wire the coordinator into the bybit_demo
        # OrderService so its pre-order cross-direction guard can
        # consult ``_trades`` before any /v5/order/create is sent.
        # See dev_notes/seven_fixes/j2_phase1_*.md.
        if bybit_demo_order is not None and hasattr(
            bybit_demo_order, "attach_coordinator"
        ):
            bybit_demo_order.attach_coordinator(trade_coordinator)

        # SLGateway — single entry point for all stop-loss modifications.
        # Consolidates 10 push paths (watchdog _push_sl_to_shadow chokepoint
        # + Profit Sniper _apply_trail_stop) behind tighten-only +
        # min-distance + max-step + rate-limit rules.
        try:
            from src.core.sl_gateway import SLGateway
            market_svc = self._services.get("market")
            if pos_svc and market_svc:
                sl_gateway = SLGateway(
                    settings=settings,
                    position_service=pos_svc,
                    market_service=market_svc,
                    event_buffer=self._services.get("event_buffer"),
                    # Volatility profiler wired for R2 ATR-scaled min_distance.
                    # Registered earlier at ~line 154; at THIS point in the
                    # bootstrap the profiler is already in _services. When
                    # missing (e.g. vp.enabled=false), SLGateway falls back
                    # to the legacy static cfg.min_distance_pct.
                    volatility_profiler=self._services.get("volatility_profiler"),
                )
                self._services["sl_gateway"] = sl_gateway

                # Reset gateway's per-symbol rate-limit/last-SL state on
                # every position close so a new trade on the same symbol
                # doesn't inherit the old trade's rate-limit budget or
                # step-size baseline. Close record carries `symbol`.
                def _sl_gateway_reset_on_close(record: dict) -> None:
                    sym = record.get("symbol")
                    if sym:
                        sl_gateway.reset_symbol(sym)

                trade_coordinator.register_close_callback(
                    _sl_gateway_reset_on_close
                )
            else:
                log.warning(
                    "SLGateway not constructed: position_service={p} market_service={m}",
                    p=bool(pos_svc), m=bool(market_svc),
                )
        except Exception as e:
            log.warning("SLGateway unavailable: {err}", err=str(e))

        # ThesisManager — tracks Claude's trade reasoning (Issue #2)
        from src.core.thesis_manager import ThesisManager
        thesis_manager = ThesisManager(db)
        self._services["thesis_manager"] = thesis_manager
        # P4 of P1-P10: wire transformer for mode-aware get_open_theses
        # filter. attach_transformer is the late-bound entry point.
        _xfm_for_thesis = self._services.get("transformer")
        if _xfm_for_thesis is not None:
            thesis_manager.attach_transformer(_xfm_for_thesis)
        # Finding 8 (2026-06-02): wire the position service so the zombie
        # reconciler can recover a downtime-closed position's true exchange
        # closedPnl (via get_last_close) instead of booking it at zero.
        # Same active proxy the watchdog uses; set at line ~458 before this.
        _pos_for_thesis = self._services.get("position")
        if _pos_for_thesis is not None:
            thesis_manager.attach_position_service(_pos_for_thesis)

        # Durable-open (2026-06-17): boot-time resolution of any leftover
        # status='reserving' thesis rows from a prior run that died between the
        # reserve and its finalize/void (the thesis-before-order open path).
        # Adopt rows whose live position entry-matches (flip ->'open' so the
        # watchdog-managed position's eventual close books PnL); void the rest.
        # The 5-min watchdog sweep is the periodic backstop — this just shrinks
        # the resolution window to ~boot. MUST use a GROUND-TRUTH-CONFIRMED
        # snapshot: get_positions() swallows API errors into [] (I1/F-26), which
        # would mass-void live reservations, so we gate on confirmation exactly
        # like the watchdog tick and SKIP (defer to the periodic sweep) when the
        # snapshot is unconfirmed.
        try:
            import inspect as _inspect
            _gpwc = getattr(_pos_for_thesis, "get_positions_with_confirmation", None)
            if _gpwc is not None and _inspect.iscoroutinefunction(_gpwc):
                _pr = await _gpwc()
                if getattr(_pr, "confirmed", False):
                    await thesis_manager.sweep_reserving_theses(list(_pr.positions))
                else:
                    log.warning(
                        f"RESERVING_SWEEP_BOOT_SKIP | reason={getattr(_pr, 'reason', None) or 'unconfirmed'} "
                        f"| boot positions not ground-truth-confirmed; the periodic "
                        f"watchdog sweep will resolve reserving rows | {ctx()}"
                    )
            else:
                log.info(
                    f"RESERVING_SWEEP_BOOT_SKIP | reason=no_confirmation_api | "
                    f"periodic watchdog sweep will resolve | {ctx()}"
                )
        except Exception as _e:
            log.warning(f"RESERVING_SWEEP_BOOT_FAIL | err='{str(_e)[:100]}' | {ctx()}")

        # Mid-Hold Trade Management Fix Phase 3.4 (2026-05-19) —
        # EnsembleStateCache is the per-symbol cache of the latest
        # weighted vote counts produced by EnsembleVoter.vote(). The
        # PositionWatchdog reads it in _monitor_position to detect
        # mid-hold ensemble flips on open positions. The cache is
        # instantiated here (before both EnsembleVoter creation at
        # ~line 1647 and PositionWatchdog creation at ~line 1386) so a
        # single instance is shared via the services dict.
        from src.strategies.ensemble import EnsembleStateCache
        ensemble_state_cache = EnsembleStateCache()
        self._services["ensemble_state_cache"] = ensemble_state_cache

        # SLTPValidator — headspace buffer for SL/TP (Issue #5) + F37 minimum
        # stop-loss distance (the brain-output safety net, centralized in [risk]).
        from src.core.sl_tp_validator import SLTPValidator
        _risk_cfg = getattr(self.settings, "risk", None)
        _min_sl_dist = float(getattr(_risk_cfg, "min_sl_distance_pct", 1.5))
        sl_validator = SLTPValidator(
            headspace_pct=2.5,
            max_distance_pct=25.0,
            min_sl_distance_pct=_min_sl_dist,
        )
        self._services["sl_validator"] = sl_validator
        # F37 boot sentinel — confirm the minimum-SL-distance clamp loaded.
        log.info(
            f"SLTP_MIN_DISTANCE_CONFIG | min_sl_distance_pct={_min_sl_dist:.2f} "
            f"headspace_pct=2.5 max_distance_pct=25.0 | {ctx()}"
        )
        # Fix 7 boot sentinel — confirm the volatility-stop-scaling config loaded.
        _vss_cfg = getattr(_risk_cfg, "volatility_stop_scaling", None)
        log.info(
            f"BOOT_STOP_SCALING | enabled={getattr(_vss_cfg, 'enabled', False)} "
            f"reference_stop_pct={getattr(_vss_cfg, 'reference_stop_pct', 1.5)} "
            f"max_cap_pct={getattr(_vss_cfg, 'max_cap_pct', 5.0)} "
            f"use_profiler={getattr(_vss_cfg, 'use_profiler_recommended_sl', True)} "
            f"scalar={getattr(_vss_cfg, 'recommended_sl_scalar', 1.0)} | {ctx()}"
        )

        # DataLakeWriter — writes to 6 data lake tables (#10)
        from src.core.data_lake import DataLakeWriter
        data_lake = DataLakeWriter(db)
        self._services["data_lake"] = data_lake

        # EventBuffer — collects watchdog events for Claude (#8)
        from src.core.event_buffer import EventBuffer
        event_buffer = EventBuffer(data_lake=data_lake)
        self._services["event_buffer"] = event_buffer
        # T6: Wire event buffer to Transformer for switch notifications
        _t6_transformer = self._services.get("transformer")
        if _t6_transformer:
            _t6_transformer.set_event_buffer(event_buffer)
        # SL Hierarchy overhaul (2026-04-22): late-wire EventBuffer into
        # SLGateway so gateway rejects + wire failures surface to Claude.
        # Gateway was constructed in an earlier layer (before EventBuffer
        # existed), so this is the matching late-wire step.
        _sl_gateway = self._services.get("sl_gateway")
        if _sl_gateway is not None:
            _sl_gateway.set_event_buffer(event_buffer)

        # UrgentQueue — watchdog concerns piggybacked into strategist calls
        from src.core.urgent_queue import UrgentQueue
        urgent_queue = UrgentQueue()
        self._services["urgent_queue"] = urgent_queue

        # TradingModeManager — Shadow/Testnet/Mainnet prompt-text mode (#6).
        # XRAY phase-2 fix: pass the Transformer so the manager derives
        # SHADOW when transformer routes orders to the local virtual
        # exchange. A switch callback re-derives mode after every
        # transformer.switch_to so prompt framing follows routing in
        # lockstep without a service restart.
        try:
            from src.core.trading_mode import TradingModeManager
            _transformer_for_mode = self._services.get("transformer")
            trading_mode_mgr = TradingModeManager(
                db, settings, transformer=_transformer_for_mode,
            )
            await trading_mode_mgr.initialize()
            self._services["trading_mode"] = trading_mode_mgr
            if _transformer_for_mode is not None:
                # Closure captures the local mgr — Transformer fires the
                # callback synchronously after the routing flip.
                def _refresh_after_switch(old_mode: str, new_mode: str) -> None:
                    trading_mode_mgr.refresh()
                _transformer_for_mode.register_switch_callback(_refresh_after_switch)
        except Exception as e:
            log.warning("TradingMode unavailable: {err}", err=str(e))

        # Wire mode indicator into alert manager (#6)
        _alert_mgr = self._services.get("alert_manager")
        _trading_mode = self._services.get("trading_mode")
        if _alert_mgr and _trading_mode:
            _alert_mgr._trading_mode = _trading_mode
        # Wire Transformer into alert manager for mode prefix (T5)
        _transformer = self._services.get("transformer")
        if _alert_mgr and _transformer:
            _alert_mgr._transformer = _transformer

        # Phase 12.9 (lifecycle-logging-audit Gap 9.3-G2): wire AlertManager
        # into DataLakeWriter so DL_TRADE_SUSPECT data integrity violations
        # surface as CRITICAL Telegram alerts (audit's #2 named gap).
        _data_lake = self._services.get("data_lake")
        if _alert_mgr and _data_lake and hasattr(_data_lake, "set_alert_manager"):
            _data_lake.set_alert_manager(_alert_mgr)
            log.info("DATA_LAKE_ALERT_WIRED | source=workers/manager._init_services")

        # TieredCapitalManager — progressive capital system (Issue #4)
        try:
            from src.fund_manager.tiered_capital import TieredCapitalManager
            # Fetch actual equity instead of hardcoded value
            _acc_for_equity = self._services.get("account")
            _live_equity = 168000.0  # fallback
            if _acc_for_equity:
                try:
                    _bal = await _acc_for_equity.get_wallet_balance()
                    if _bal and _bal.total_equity > 0:
                        _live_equity = _bal.total_equity
                        log.info(
                            "TieredCapital: using live equity ${eq:,.2f}",
                            eq=_live_equity,
                        )
                except Exception:
                    log.warning("TieredCapital: could not fetch live equity, using fallback")
            tiered_capital = TieredCapitalManager(db, starting_equity=_live_equity)
            await tiered_capital.initialize()
            self._services["tiered_capital"] = tiered_capital
        except Exception as e:
            log.warning("TieredCapital unavailable: {err}", err=str(e))

        # Strategist — calls Claude Code every 3 min for strategic plan
        try:
            from src.brain.strategist import ClaudeStrategist
            claude_client = self._services.get("claude_client")
            if claude_client:
                strategist = ClaudeStrategist(claude_client, self._services, settings)
                self._services["strategist"] = strategist
        except Exception as e:
            log.warning("Strategist unavailable: {err}", err=str(e))

        # Rule Engine — executes trades based on cached Claude plan
        try:
            from src.core.rule_engine import RuleEngine
            rule_engine = RuleEngine(self._services, settings)
            self._services["rule_engine"] = rule_engine
            # Phase 12.4 (lifecycle-logging-audit Gap 4.X follow-up):
            # RuleEngine is BYPASSED in production. Strategy hints are
            # passed to Claude as context (STRAT_L4 | hints=N) rather
            # than executed through rule_engine.evaluate. Startup log
            # makes the inactive state explicit so operators don't grep
            # RULE_EVAL_START and assume something's broken.
            from src.core.log_context import ctx as _ctx
            log.info(
                "RULE_ENGINE_INACTIVE | reason=hints_passed_to_claude_as_context "
                "replaced_by=strategy_hints execution=claude_reasoning | " + _ctx()
            )
        except Exception as e:
            log.warning("Rule Engine unavailable: {err}", err=str(e))

        # Layer Manager — 3-layer architecture controller
        try:
            from src.core.layer_manager import LayerManager
            layer_manager = LayerManager(settings, self._services)
            layer_manager.brain_interval_seconds = getattr(settings.brain, 'strategic_interval', 150)  # 2.5 min: alternating Call A/B
            self._services["layer_manager"] = layer_manager
            # P6 of P1-P10: wire LayerManager into Transformer so the
            # bybit_demo pre-dispatch L3 gate in _OrderProxy.place_order
            # has its dependency.
            _xfm_for_lm = self._services.get("transformer")
            if _xfm_for_lm is not None:
                _xfm_for_lm.attach_layer_manager(layer_manager)
            # Phase 2 (Layer 3 enforcement). OrderService was constructed
            # before LayerManager (it does not need layer state at init);
            # inject the LayerManager now so OrderService can gate
            # layer3_entry placements when L3 is OFF. Idempotent.
            #
            # ``self._services["order_service"]`` is normally a
            # ``_OrderProxy`` (created by Transformer.create_proxies for
            # live shadow/bybit switching). The proxy does NOT expose
            # ``attach_layer_manager``, so the hasattr check below
            # silently skips when the proxy is in place. Without the
            # additional underlying-service attach below, the bybit
            # OrderService inside the transformer never gets ``_layer_
            # manager`` set — defeating the Phase 2 gate entirely in
            # live Bybit mode and causing the Phase 1 (post-Layer-1)
            # boot-policy to reject every entry-side trade.
            #
            # Phase 2 (post-Layer-1 fix, audit-discovered). Walk the
            # transformer's owned service sets and attach the LM to any
            # underlying instance that exposes the method. ShadowOrderService
            # does not (Shadow has no L3 gate by design); BybitOrderService
            # does. Idempotent — attach is a no-op when called twice.
            order_svc = self._services.get("order_service") or self._services.get("order")
            if order_svc and hasattr(order_svc, "attach_layer_manager"):
                order_svc.attach_layer_manager(layer_manager)
            transformer = self._services.get("transformer")
            if transformer is not None:
                for svc_set_name in ("_bybit_services", "_shadow_services"):
                    svc_set = getattr(transformer, svc_set_name, None)
                    if not svc_set:
                        continue
                    underlying = svc_set.get("order")
                    if underlying and hasattr(underlying, "attach_layer_manager"):
                        underlying.attach_layer_manager(layer_manager)
            # Phase 2 (post-Layer-1 fix) — disk/memory state sync heartbeat.
            # Reads data/layer_state.json every state_sync_interval_sec
            # seconds and reloads in-memory state on drift. Disk is the
            # operator's source of truth (Telegram toggles persist to
            # disk synchronously); the heartbeat closes the gap when
            # something else writes the file out-of-band.
            try:
                lm_settings = getattr(settings, "layer_manager", None)
                interval = float(getattr(lm_settings, "state_sync_interval_sec", 60.0)) if lm_settings else 60.0
                # Phase 11 (dead-workers fix). Drift recovery direction
                # comes from settings; default flipped to "rewrite_disk"
                # (memory wins) to fix the Layer 3 toggle revert
                # regression. Operators can override to "reload_memory"
                # (legacy) via [layer_manager.state_sync] config block
                # if the new semantics surface a pathology.
                drift_action = (
                    str(getattr(lm_settings, "on_drift_action", "rewrite_disk"))
                    if lm_settings else "rewrite_disk"
                )
                layer_manager.start_state_sync(
                    interval_sec=interval,
                    on_drift_action=drift_action,
                )
            except Exception as e:
                log.warning(
                    "Layer state sync heartbeat unavailable: {err}", err=str(e)
                )
        except Exception as e:
            log.warning("Layer Manager unavailable: {err}", err=str(e))

        # --- Create workers ---
        self._create_workers()

        # Layer 1 restructure Phase 4 — late-bind the LayerManager handle
        # onto every worker that gates on is_cycle_active(). The cycle
        # gate inside BaseWorker.start uses ``self._layer_manager`` to
        # check; without this wiring, gated workers would fall through
        # (run unconditionally) which is the safe default if wiring
        # regresses. Idempotent — only sets the attribute if missing.
        layer_mgr = self._services.get("layer_manager")
        if layer_mgr:
            for w in self.workers:
                if getattr(w, "cycle_gated", False) and getattr(w, "_layer_manager", None) is None:
                    w._layer_manager = layer_mgr
            # Phase 11 (dead-workers fix): WorkerLivenessWatchdog is NOT
            # cycle_gated (must always run), but DOES need LM to call
            # is_cycle_active() for cycle-gate-aware classification.
            # Wire it explicitly here.
            wd = self._services.get("worker_liveness_watchdog")
            if wd is not None and getattr(wd, "_layer_manager", None) is None:
                wd._layer_manager = layer_mgr
            log.info(
                f"WORKER_LAYER_MANAGER_WIRED | gated_workers="
                f"{sum(1 for w in self.workers if getattr(w, 'cycle_gated', False))} "
                f"watchdog_wired={wd is not None} | {ctx()}"
            )

        # Layer 1 restructure Phase 1 — late-bind the CycleTracker so
        # 1B/1C workers' tick latencies aggregate into CYCLE_COMPLETE.
        # 1A workers (cycle_gated=False) skip — no cycle semantics. 1D
        # (ScannerWorker) drives its own start/end inside tick() to
        # stamp qualified/selected/packages onto the rollup; the base
        # loop opt-out for LAYER1D is enforced inside _maybe_start_cycle.
        # Idempotent: sets only when missing so a manual override stays.
        cycle_tracker = self._services.get("cycle_tracker")
        if cycle_tracker:
            wired = 0
            for w in self.workers:
                if (
                    getattr(w, "layer_tier_tag", None) is not None
                    and getattr(w, "_cycle_tracker", None) is None
                ):
                    w._cycle_tracker = cycle_tracker
                    wired += 1
            log.info(f"WORKER_CYCLE_TRACKER_WIRED | tagged_workers={wired} | {ctx()}")

        # Wire TradeCoordinator close callbacks (after all services/workers created)
        self._wire_coordinator_callbacks()

        # Initialize fund manager (needs all services to be wired first)
        fund_mgr = self._services.get("fund_manager")
        if fund_mgr:
            try:
                await fund_mgr.initialize()
            except Exception as e:
                log.warning("Fund Manager initialization failed: {err}", err=str(e))

        # Run initial scanner scan so universe is populated before workers start
        scanner = self._services.get("scanner")
        if scanner:
            try:
                await scanner.scan_market()
                universe = await scanner.get_active_universe()
                log.info("Initial scan: {n} coins in universe", n=len(universe))
            except Exception as e:
                log.warning("Initial scanner run failed: {err}", err=str(e))

        # Observability: emit a single-line manifest of every wired service.
        # Missing services are logged at WARNING so they are immediately visible
        # instead of surfacing as silent None-checks downstream.
        self._emit_services_wired()

        # One-shot klines retention enforcement. Must run AFTER DB is connected
        # and BEFORE any worker tick. Idempotent (skips if table already small).
        try:
            await self._startup_klines_cleanup()
        except Exception as e:
            log.warning("Startup klines cleanup failed: {err}", err=str(e))

        # Phase 4 (Stage-1/2 fix): pre-seed the regime detector before any
        # worker tick. The strategist's first prompt build otherwise finds
        # ``regime_detector.get_last_regime() is None`` and falls through to
        # ``await detect()`` inline — observed in the 2026-04-24 window as a
        # 6,397 ms ``regime_fetch`` sub-phase spike on Call-A #1, dropping to
        # 5 ms by Call-A #3 once RegimeWorker's 300s cadence had populated
        # ``_last_regime`` at least once. Seeding here eliminates the
        # boot-race variance entirely.
        try:
            await self._startup_regime_seed()
        except Exception as e:
            log.warning("Startup regime seed failed: {err}", err=str(e))

        # Phase 4.D (bybit_demo_adapter): post-switch verifier.
        # If a previous run wrote data/post_switch_sentinel.json before
        # triggering systemctl restart, this picks it up, probes the new
        # active adapter (wallet + positions), sends a Telegram
        # confirmation, and deletes the sentinel. No-op + returns False
        # silently when no sentinel (normal boot, not a switch).
        try:
            from src.exchanges.switching import verify_post_switch
            transformer = self._services.get("transformer")
            alert_manager = self._services.get("alert_manager")
            if transformer is not None:
                await verify_post_switch(transformer, alert_manager, self.db)
        except Exception as e:
            log.warning("Post-switch verifier failed: {err}", err=str(e))

        # Bybit demo logging Phase 4: register the alert relay AFTER
        # alert_manager is wired and post-switch verification has run
        # (so the relay does not double-fire on POST_SWITCH_VERIFY_*
        # tags emitted during this very boot's verification path).
        # The relay is a loguru sink that translates CRITICAL/WARNING
        # tagged events from bybit_demo / worker components into
        # AlertManager calls. Adapter code stays log-only.
        try:
            alert_manager = self._services.get("alert_manager")
            if alert_manager is not None:
                from src.observability import BybitDemoAlertRelay
                relay = BybitDemoAlertRelay(
                    alert_manager,
                    loop=asyncio.get_running_loop(),
                )
                relay.register()
                self._services["bybit_demo_alert_relay"] = relay

                # One-shot replay of boot-validation failure. The boot
                # validator runs near line ~355 — well before
                # AlertManager exists or the relay is registered, so
                # an early BYBIT_DEMO_BOOT_FAIL log never reaches the
                # relay's sink. We bridge that gap explicitly so the
                # operator sees the boot failure on Telegram on the
                # very same boot, not the next one.
                boot_result = self._services.get("bybit_demo_boot_result")
                if isinstance(boot_result, dict) and boot_result.get("ok") is False:
                    try:
                        await alert_manager.send_risk_warning(
                            "bybit_demo_boot",
                            {
                                "step": boot_result.get("step", "unknown"),
                                "err": boot_result.get("err", ""),
                                "stage": "early_boot_replay",
                            },
                        )
                    except Exception as e:
                        log.warning(
                            "Bybit demo boot-fail replay alert failed: {err}",
                            err=str(e),
                        )
        except Exception as e:
            log.warning("Bybit demo alert relay registration failed: {err}", err=str(e))

        log.info(
            "WorkerManager initialized with {n} workers",
            n=len(self.workers),
        )

    # Canonical list of service keys that _should_ be populated by a full
    # initialization. Generated from grepping every ``self._services[...] = ...``
    # assignment in this module. Workers that fail to wire (network unavailable,
    # feature disabled) will appear in SERVICES_MISSING and that is fine —
    # the intent is visibility, not enforcement.
    _EXPECTED_SERVICE_KEYS: tuple[str, ...] = (
        # Core / market plumbing
        "transformer", "bybit", "ws", "market", "market_service",
        "instrument_service",
        # Intelligence / altdata
        "news", "calendar", "reddit", "fear_greed", "funding", "oi", "onchain",
        "aggregator", "signal_gen",
        # TA stack
        "ta", "ta_engine", "ta_cache", "ta_raw", "volatility_profiler",
        # Structural / X-RAY
        "structure_engine", "structure_cache",
        "shadow_kline_reader",
        # Trading services
        "position", "order", "account",
        "position_service", "order_service", "account_service",
        # Brain + alerts
        "cost_tracker", "claude_client", "decision_parser", "alert_manager",
        "risk_manager",
        # Coordination + state
        "freshness_guard", "trade_coordinator", "thesis_manager", "sl_validator",
        "data_lake", "event_buffer", "urgent_queue", "trading_mode",
        "tiered_capital",
        # Strategic pipeline
        "strategist", "rule_engine", "layer_manager",
        # Scanner + regime
        "scanner", "regime_detector", "registry", "pnl_manager",
        # Workers surfaced on the dict for cross-referencing.
        # Phase 6 (corrected-Layer-1) added kline_worker, price_worker,
        # signal_worker, regime_worker, altdata_worker, scanner_worker
        # so ScannerWorker can call their accessors at scoring time.
        "strategy_worker", "position_watchdog", "profit_sniper",
        "structure_worker", "telegram_bot",
        "kline_worker", "price_worker", "signal_worker",
        "regime_worker", "altdata_worker", "scanner_worker",
        # Risk sizing
        "enforcer", "fund_manager", "risk_budget", "kelly", "correlation_tracker",
        # Apex / sentinel / TIAS
        "apex_optimizer", "apex_gate", "sentinel_advisor", "tias_repo",
        # Layer 4 Realignment Phase 4.2 (2026-05-06) — shared
        # Layer4ProtectionService consumed by PositionWatchdog (post-init)
        # and ProfitSniper (constructor kwarg). Listed here so the
        # SERVICES_WIRED boot-time manifest tracks it; missing in the
        # manifest would silently mask a DI regression.
        "layer4_protection",
    )

    def _emit_services_wired(self) -> None:
        """Log a boot-time manifest of wired vs missing services."""
        keys = self._EXPECTED_SERVICE_KEYS
        present = [k for k in keys if self._services.get(k) is not None]
        missing = [k for k in keys if self._services.get(k) is None]
        log.info(
            f"SERVICES_WIRED | present={len(present)}/{len(keys)} "
            f"keys=[{','.join(present)}] | {ctx()}"
        )
        if missing:
            log.warning(
                f"SERVICES_MISSING | count={len(missing)} "
                f"keys=[{','.join(missing)}] | {ctx()}"
            )

    async def _startup_klines_cleanup(self) -> None:
        """One-shot bulk retention on the klines table at startup.

        The per-insert sweep in ``MarketRepository.save_klines`` keeps the table
        bounded during normal operation, but an existing bloated database
        (observed: 328k rows / 180 MB) needs a one-time collapse. Idempotent —
        ``if before <= threshold: return`` so subsequent restarts are no-ops.
        """
        threshold = 50000
        keep_per_symtf = 300
        before_row = await self.db.fetch_one("SELECT COUNT(*) AS cnt FROM klines")
        before = (before_row or {}).get("cnt", 0) or 0
        if before <= threshold:
            log.info(
                f"KLINES_CLEANUP_SKIP | rows={before} threshold={threshold} | {ctx()}"
            )
            return
        log.info(
            f"KLINES_CLEANUP_START | rows={before} "
            f"target={keep_per_symtf}_per_sym_tf | {ctx()}"
        )
        # ROW_NUMBER() OVER (PARTITION BY ...) is supported on SQLite >= 3.25
        # (host has 3.37.2). The subquery list of rowids to keep is small; the
        # outer DELETE scans the table once.
        await self.db.execute(
            """
            DELETE FROM klines WHERE rowid NOT IN (
              SELECT rowid FROM (
                SELECT rowid, ROW_NUMBER() OVER (
                  PARTITION BY symbol, timeframe
                  ORDER BY timestamp DESC
                ) AS rn FROM klines
              ) WHERE rn <= ?
            )
            """,
            (keep_per_symtf,),
        )
        # T1-4 / F4 fix (six-tier-fixes 2026-05-11) — boot VACUUM removed.
        # The hourly PRAGMA incremental_vacuum(N) in cleanup_worker reclaims
        # freelist pages continuously without taking the long exclusive
        # lock the legacy daily/boot VACUUM caused (live evidence of 21s
        # freezes today). When the DB has not yet been migrated to
        # auto_vacuum=INCREMENTAL the cleanup_worker emits
        # DB_VACUUM_MIGRATION_REQUIRED at WARN; operator runs
        # scripts/t1_4_migrate_to_incremental_vacuum.sh once and the
        # incremental path takes over on the next hourly tick.
        vacuumed = "N (incremental on hourly tick)"
        after_row = await self.db.fetch_one("SELECT COUNT(*) AS cnt FROM klines")
        after = (after_row or {}).get("cnt", 0) or 0
        log.info(
            f"KLINES_CLEANUP_DONE | before={before} after={after} "
            f"deleted={before - after} vacuumed={vacuumed} | {ctx()}"
        )

    async def _startup_regime_seed(self) -> None:
        """Prime the regime detector so strategist's first prompt hits a warm path.

        Phase 4 (Stage-1/2 fix). Must run AFTER ``_startup_klines_cleanup``
        (so the DB has H1 klines available) and BEFORE any worker starts
        ticking. One ``await detect()`` call costs ~150-200 ms from cold
        and populates ``_last_regime``; every subsequent strategist prompt
        build then takes the zero-cost ``get_last_regime()`` path at
        ``src/brain/strategist.py:1180``. Idempotent — safe to re-run.
        """
        detector = self._services.get("regime_detector")
        if detector is None:
            log.info(f"REGIME_SEED_SKIP | reason=no_detector | {ctx()}")
            return
        # If the detector somehow already has a last regime (e.g. the
        # WorkerManager was re-initialised), honour that and skip.
        if getattr(detector, "_last_regime", None) is not None:
            log.info(
                f"REGIME_SEED_SKIP | reason=already_primed "
                f"rgm={detector._last_regime.regime.value} | {ctx()}"
            )
            return
        import time as _t
        _t0 = _t.time()
        try:
            state = await detector.detect()
            el_ms = (_t.time() - _t0) * 1000
            log.info(
                f"REGIME_SEED | rgm={state.regime.value} "
                f"conf={state.confidence:.2f} adx={state.adx:.1f} "
                f"el={el_ms:.0f}ms | {ctx()}"
            )
        except Exception as e:
            el_ms = (_t.time() - _t0) * 1000
            log.warning(
                f"REGIME_SEED_FAIL | el={el_ms:.0f}ms err='{str(e)[:150]}' | {ctx()}"
            )

    async def _log_apex_startup_stats(self, db, apex_cfg) -> None:
        """One-shot snapshot of APEX's durable state at startup.

        Scheduled from ``_create_workers`` to run soon after the manager
        initializes. The brief's Phase 3 self-verification expects
        visibility into how many trades APEX has to work with; emitting
        this early means the next tier-2/3 decision a few seconds later
        is interpretable (we know how much history was available and
        the threshold it was judged against).
        """
        import time as _t
        try:
            _t0 = _t.time()
            _tot = await db.fetch_one(
                "SELECT COUNT(*) AS n FROM trade_intelligence"
            )
            _dist = await db.fetch_one(
                "SELECT COUNT(DISTINCT symbol) AS s, "
                "COUNT(DISTINCT regime) AS r "
                "FROM trade_intelligence "
                "WHERE regime IS NOT NULL AND regime != ''"
            )
            _by_regime = await db.fetch_all(
                "SELECT regime, COUNT(*) AS n FROM trade_intelligence "
                "WHERE regime IS NOT NULL AND regime != '' "
                "GROUP BY regime ORDER BY n DESC"
            )
            _min_regime = getattr(
                apex_cfg, "min_regime_trades_for_fallback", 10
            )
            _breakdown = ",".join(
                f"{r['regime']}={r['n']}"
                for r in (_by_regime or [])
            )
            log.info(
                f"APEX_STARTUP_STATS | total_rows={_tot['n'] if _tot else 0} "
                f"symbols={_dist['s'] if _dist else 0} "
                f"regimes={_dist['r'] if _dist else 0} "
                f"min_tias={apex_cfg.min_tias_trades_for_optimization} "
                f"min_regime={_min_regime} "
                f"conviction_min={getattr(apex_cfg, 'conviction_min_trades', 3)} "
                f"by_regime=[{_breakdown}] "
                f"query_ms={(_t.time() - _t0) * 1000:.0f} | {ctx()}"
            )
        except Exception as e:
            log.warning(f"APEX_STARTUP_STATS_FAIL | err='{str(e)[:160]}' | {ctx()}")

    def _create_workers(self) -> None:
        """Instantiate all worker classes with their dependencies."""
        from src.workers.price_worker import PriceWorker
        from src.workers.kline_worker import KlineWorker
        from src.workers.news_worker import NewsWorker
        from src.workers.reddit_worker import RedditWorker
        from src.workers.altdata_worker import AltDataWorker
        from src.workers.signal_worker import SignalWorker
        from src.workers.cleanup_worker import CleanupWorker
        from src.workers.bybit_demo_ws_worker import BybitDemoWSWorker

        s = self.settings
        db = self.db

        # Scanner reference for dynamic symbol tracking (created later, set after)
        _scanner_ref = self._services.get("scanner")

        if self._services.get("ws"):
            # Issue 2 of cascade-fix series (2026-05-10): construct the
            # TickerCacheBuffer BEFORE PriceWorker so it can be injected
            # into the worker. The buffer's drainer task is started by
            # PriceWorker.tick() once the asyncio loop is captured.
            # MarketRepository instance is shared with PriceWorker (both
            # construct their own; the repo is stateless apart from the
            # injected db, so two instances are equivalent). The buffer
            # also gets registered in the service container so readers
            # (transformer._get_local_price, market_repo.get_ticker) can
            # consult it for sub-flush-interval freshness without a DB
            # hop.
            from src.workers.ticker_cache_buffer import TickerCacheBuffer
            from src.database.repositories.market_repo import (
                MarketRepository as _MR,
            )
            _ticker_buffer = TickerCacheBuffer(
                _MR(db, kline_save_chunk_size=s.database.kline_save_chunk_size),
                # Issue 2.10 (2026-06-07): preventive anomalous-tick rejection
                # threshold, sourced from [price].spike_reject_pct (data-driven).
                spike_reject_pct=float(
                    getattr(getattr(s, "price", None), "spike_reject_pct", 0.0) or 0.0
                ),
            )
            self._services["ticker_cache_buffer"] = _ticker_buffer
            # Issue 2.10 boot sentinel (Pass-3 audit): the TICKER_BUFFER_START
            # line fires inside the buffer's start() on the first price tick, not
            # at boot, so the spike-guard config was invisible in the boot window.
            # Emit the config here at construction so an operator can confirm it
            # alongside the other 2.x boot sentinels (drainer still logs START).
            log.info(
                f"TICKER_BUFFER_CONFIG | spike_reject_pct={_ticker_buffer._spike_reject_pct:.4f} "
                f"spike_guard={'ON' if _ticker_buffer._spike_reject_pct > 0.0 else 'OFF'} "
                f"| drainer=deferred_to_first_tick | {ctx()}"
            )
            _price_worker = PriceWorker(
                s, db, self._services["ws"], scanner=_scanner_ref,
                ticker_buffer=_ticker_buffer,
            )
            self.workers.append(_price_worker)
            # Phase 6: expose PriceWorker so APEX assembler can consult the
            # in-memory WS quote cache before falling back to REST ticker.
            self._services["price_worker"] = _price_worker
            # Issue 2 cascade-fix: attach the buffer to the Transformer so
            # ``_get_local_price`` consults it before the DB SELECT. The
            # transformer is constructed earlier in _setup; if for some
            # reason it is missing here, the attach is a no-op and the
            # transformer falls back to its DB-only path.
            _xfm_for_buf = self._services.get("transformer")
            if _xfm_for_buf is not None and hasattr(
                _xfm_for_buf, "attach_ticker_buffer",
            ):
                _xfm_for_buf.attach_ticker_buffer(_ticker_buffer)

        # Bybit demo private WebSocket subscriber (P1 of P1-P10 fix series).
        # Pushes Bybit-side close events to coordinator.on_trade_closed in
        # under 100ms instead of waiting for the watchdog's 10s poll +
        # the 35% closed-pnl indexer race. Polling at position_watchdog
        # remains as fallback unchanged.
        # Constructed when (a) bybit_demo is enabled in config, (b) demo
        # credentials are present, AND (c) the trade_coordinator exists.
        # Otherwise skipped — the watchdog's poll path is sufficient
        # (degraded but functional, the original pre-P1 behaviour).
        bd_settings_for_ws = getattr(s, "bybit_demo", None)
        coord_for_ws = self._services.get("trade_coordinator")
        if (
            bd_settings_for_ws is not None
            and bd_settings_for_ws.enabled
            and bd_settings_for_ws.api_key
            and bd_settings_for_ws.api_secret
            and coord_for_ws is not None
        ):
            from src.bybit_demo.bybit_demo_websocket_subscriber import (
                BybitDemoWebSocketSubscriber,
            )
            _bd_ws_subscriber = BybitDemoWebSocketSubscriber(
                settings=s,
                db=db,
                coordinator=coord_for_ws,
                # asyncio.get_running_loop() is the Python 3.10+ canonical
                # call inside an async function; matches the pre-existing
                # pattern at manager.py line ~892. get_event_loop() is
                # deprecated for this case (DeprecationWarning since 3.10).
                loop=asyncio.get_running_loop(),
            )
            _bd_ws_worker = BybitDemoWSWorker(
                name="bybit_demo_ws_worker",
                interval_seconds=60.0,
                settings=s,
                db=db,
                subscriber=_bd_ws_subscriber,
            )
            self.workers.append(_bd_ws_worker)
            self._services["bybit_demo_ws_subscriber"] = _bd_ws_subscriber
            self._services["bybit_demo_ws_worker"] = _bd_ws_worker
            log.info(
                "BybitDemoWSWorker: registered "
                "(60s health-tick, push-driven close detection)"
            )
        elif bd_settings_for_ws is not None and bd_settings_for_ws.enabled:
            log.warning(
                "BybitDemoWSWorker: skipped — "
                "creds_present={c} coordinator_present={p}",
                c=bool(bd_settings_for_ws.api_key and bd_settings_for_ws.api_secret),
                p=bool(coord_for_ws),
            )

        if self._services.get("market"):
            # Phase 6 (P0-5): expose KlineWorker so strategy_worker can
            # check the fetch-collapse circuit breaker before running TA.
            _kline_worker = KlineWorker(s, db, self._services["market"], scanner=_scanner_ref)
            self.workers.append(_kline_worker)
            self._services["kline_worker"] = _kline_worker
        if self._services.get("news") and s.finnhub.api_key:
            self.workers.append(NewsWorker(s, db, self._services["news"], self._services.get("calendar")))
        if self._services.get("reddit"):
            self.workers.append(RedditWorker(s, db, self._services["reddit"]))

        # AltData worker needs at least some sources
        fg = self._services.get("fear_greed")
        funding = self._services.get("funding")
        oi = self._services.get("oi")
        onchain = self._services.get("onchain")
        if any([fg, funding, oi, onchain]):
            _altdata_worker = AltDataWorker(s, db, fg, funding, oi, onchain)
            self.workers.append(_altdata_worker)
            # Phase 6 (corrected-Layer-1): register so the new ScannerWorker
            # can call get_funding(coin) for the composite opportunity score.
            self._services["altdata_worker"] = _altdata_worker

        if self._services.get("ta") and self._services.get("aggregator") and self._services.get("signal_gen"):
            _signal_worker = SignalWorker(
                s, db, self._services["ta"],
                self._services["aggregator"], self._services["signal_gen"],
            )
            self.workers.append(_signal_worker)
            # Phase 6 (corrected-Layer-1): register for ScannerWorker's
            # composite opportunity scoring (get_signal accessor).
            self._services["signal_worker"] = _signal_worker

        # Position Watchdog
        if s.watchdog.enabled and self._services.get("position") and self._services.get("market"):
            from src.workers.position_watchdog import PositionWatchdog
            watchdog = PositionWatchdog(
                settings=s,
                db=db,
                position_service=self._services["position"],
                market_service=self._services["market"],
                order_service=self._services.get("order"),
                account_service=self._services.get("account"),
                claude_client=self._services.get("claude_client"),
                cost_tracker=self._services.get("cost_tracker"),
                decision_parser=self._services.get("decision_parser"),
                risk_manager=self._services.get("risk_manager"),
                alert_manager=self._services.get("alert_manager"),
                ta_engine=self._services.get("ta"),
                trade_coordinator=self._services.get("trade_coordinator"),
                event_buffer=self._services.get("event_buffer"),
                data_lake=self._services.get("data_lake"),
                transformer=self._services.get("transformer"),
                regime_detector=self._services.get("regime_detector"),
                urgent_queue=self._services.get("urgent_queue"),
                volatility_profiler=self._services.get("volatility_profiler"),
                sl_gateway=self._services.get("sl_gateway"),
                thesis_manager=self._services.get("thesis_manager"),
                # Time-Decay Force-Close Definitive Fix Phase 3 (2026-05-06)
                # — structure_cache feeds the watchdog's structural-
                # invalidation detector inside _handle_time_decay. Service
                # is registered earlier in this method (~line 223) so
                # direct injection works without late-binding.
                structure_cache=self._services.get("structure_cache"),
                # Mid-Hold Trade Management Fix Phase 3.4 (2026-05-19) —
                # EnsembleStateCache for the 1A ensemble-flip detection
                # lane. Service registered earlier in this method (~line
                # 691) so direct injection works.
                ensemble_state_cache=self._services.get("ensemble_state_cache"),
            )
            self.workers.append(watchdog)
            self._services["position_watchdog"] = watchdog
        elif s.watchdog.enabled:
            log.warning("Position Watchdog skipped: position/market services unavailable")

        # Layer 4 Protection Service (Phase 4.2, 2026-05-06).
        # Built AFTER the watchdog so it can reuse the watchdog's
        # TimeDecaySLCalculator (the calculator's cfg holds the
        # xray_drop / regime_inversion thresholds the service reads
        # in compute_structural_invalidation). Registered in the
        # service container so the sniper picks it up via DI below;
        # the watchdog gets the service via post-init assignment so
        # Phase 4.3 can switch its `_compute_structural_invalidation`
        # call site to the shared service.
        try:
            from src.risk.layer4_protection import Layer4ProtectionService
            _watchdog_for_calc = self._services.get("position_watchdog")
            _td_calc = (
                getattr(_watchdog_for_calc, "_time_decay", None)
                if _watchdog_for_calc is not None
                else None
            )
            layer4_protection = Layer4ProtectionService(
                settings=s,
                coordinator=self._services.get("trade_coordinator"),
                structure_cache=self._services.get("structure_cache"),
                regime_detector=self._services.get("regime_detector"),
                time_decay_calculator=_td_calc,
            )
            self._services["layer4_protection"] = layer4_protection
            # Post-init wiring: watchdog adopts the service in Phase 4.3.
            if _watchdog_for_calc is not None:
                _watchdog_for_calc.layer4_protection = layer4_protection
            log.info(
                "Layer4ProtectionService registered (td_calc={has_calc})",
                has_calc=_td_calc is not None,
            )
        except Exception as e:
            log.warning(
                "Layer4ProtectionService unavailable: {err} — sniper "
                "will fail-loud on stall escape, watchdog will fall back "
                "to its inline _compute_structural_invalidation",
                err=str(e),
            )
            self._services["layer4_protection"] = None

        # Profit Sniper (Mode 4)
        if hasattr(s, "mode4") and s.mode4.enabled and self._services.get("position") and self._services.get("market"):
            try:
                from src.workers.profit_sniper import ProfitSniper
                sniper = ProfitSniper(
                    settings=s,
                    db=db,
                    position_service=self._services["position"],
                    market_service=self._services["market"],
                    order_service=self._services.get("order"),
                    account_service=self._services.get("account"),
                    claude_client=self._services.get("claude_client"),
                    alert_manager=self._services.get("alert_manager"),
                    transformer=self._services.get("transformer"),
                    trade_coordinator=self._services.get("trade_coordinator"),
                    event_buffer=self._services.get("event_buffer"),
                    ta_cache=self._services.get("ta"),
                    regime_detector=self._services.get("regime_detector"),
                    volatility_profiler=self._services.get("volatility_profiler"),
                    sl_gateway=self._services.get("sl_gateway"),
                    # Layer 4 Realignment Phase 4.2 (2026-05-06) — sniper
                    # consults the shared protection service before
                    # firing stall-escape closes. None means service
                    # construction failed; sniper logs ERROR and
                    # refuses to escalate (fail-loud, fail-safe).
                    layer4_protection=self._services.get("layer4_protection"),
                    # Loss-Cutting Technique 3 (2026-05-31) — X-RAY structure
                    # cache for the structure-based stop. Optional; the sniper
                    # fail-safes to the ATR/cap candidates when it is missing.
                    structure_cache=self._services.get("structure_cache"),
                )
                self.workers.append(sniper)
                self._services["profit_sniper"] = sniper
            except Exception as e:
                log.warning("Profit Sniper (Mode 4) unavailable: {err}", err=str(e))
        elif hasattr(s, "mode4") and s.mode4.enabled:
            log.warning("Profit Sniper skipped: position/market services unavailable")

        # Strategy Engine Workers (Scanner, Regime, Strategy)
        try:
            from src.strategies.scanner import MarketScanner
            from src.strategies.regime import RegimeDetector
            from src.strategies.scorer import TradeScorer
            from src.strategies.ensemble import EnsembleVoter
            from src.strategies.pnl_manager import DailyPnLManager
            from src.strategies.smart_leverage import SmartLeverage
            from src.strategies.registry import StrategyRegistry
            from src.workers.scanner_worker import ScannerWorker
            from src.workers.regime_worker import RegimeWorker
            from src.workers.strategy_worker import StrategyWorker
            from src.database.repositories.market_repo import MarketRepository

            market_svc = self._services.get("market")
            ta = self._services.get("ta")

            if s.scanner.enabled and market_svc:
                inst_svc = self._services.get("instrument_service")
                # Layer 1 universe alignment (Phase 2): bound the scanner's
                # input set to the curated watch_list ∪ open positions
                # (HR-1 single source of truth, HR-2 positions always
                # included). Empty watch_list falls back to legacy "score
                # all Bybit tickers" behavior — backward compatible.
                _watch_list = set(getattr(s.universe, "watch_list", []) or [])
                scanner = MarketScanner(
                    s,
                    market_svc,
                    instrument_service=inst_svc,
                    watch_list=_watch_list,
                )
                self._services["scanner"] = scanner
                # Phase 6 (corrected-Layer-1): pass the services dict by
                # reference so ScannerWorker can look up the 7 data
                # workers' accessors at scoring time. Workers registered
                # AFTER this line (structure_worker, regime_worker,
                # strategy_worker) become available to ScannerWorker via
                # the same dict reference once their constructors run.
                _scanner_worker = ScannerWorker(s, db, scanner, services=self._services)
                self.workers.append(_scanner_worker)
                self._services["scanner_worker"] = _scanner_worker

                # Wire scanner to data workers for dynamic symbol tracking
                for w in self.workers:
                    if hasattr(w, "_scanner") and w._scanner is None:
                        w._scanner = scanner

                # Late-wire position_service to scanner for position protection
                pos_svc = self._services.get("position")
                if pos_svc:
                    scanner._position_service = pos_svc

                # Phase 7 (corrected-Layer-1): the master universe-change
                # callback dispatcher was removed here. Under the corrected
                # architecture, workers operate on the full watch_list and
                # don't need to react to scanner rotations — there are no
                # rotation-driven backfills/cleanups (the pre-corrected
                # behavior wired here previously). See blueprint §13.5.

            # X-RAY Structure Worker
            if hasattr(s, "structure") and s.structure.enabled:
                se = self._services.get("structure_engine")
                sc = self._services.get("structure_cache")
                if se and sc:
                    try:
                        from src.workers.structure_worker import StructureWorker
                        # Corrected Layer 1 (Phase 3): structure_worker reads
                        # settings.universe.watch_list directly. The ``scanner``
                        # injection is None-safe legacy compatibility (the
                        # worker itself no longer consults it).
                        sw = StructureWorker(
                            settings=s, db=db, engine=se, cache=sc,
                            scanner=self._services.get("scanner"),
                            shadow_kline_reader=self._services.get("shadow_kline_reader"),
                        )
                        self.workers.append(sw)
                        self._services["structure_worker"] = sw
                        log.info(
                            "X-RAY Structure Worker registered (sweet_spot={ss})",
                            ss=s.workers.sweet_spots.structure_worker,
                        )
                    except Exception as e:
                        log.warning("X-RAY Structure Worker unavailable: {err}", err=str(e))

            if ta:
                market_repo = MarketRepository(
                    db, kline_save_chunk_size=s.database.kline_save_chunk_size
                )
                detector = RegimeDetector(s, ta, market_repo)
                self._services["regime_detector"] = detector
                _regime_worker = RegimeWorker(
                    s, db, detector, scanner=self._services.get("scanner"),
                )
                self.workers.append(_regime_worker)
                # Phase 6 (corrected-Layer-1): expose the worker (in addition
                # to the detector) so ScannerWorker's get_regime accessor has
                # a stable handle even if RegimeDetector internals change.
                self._services["regime_worker"] = _regime_worker

                # Late-wire regime_detector to watchdog (created earlier before detector existed)
                _wd = self._services.get("position_watchdog")
                if _wd:
                    _wd.regime_detector = detector

                # Late-wire regime_detector to VolatilityProfiler
                _vp = self._services.get("volatility_profiler")
                if _vp:
                    _vp._regime_detector = detector

                # Late-wire regime_detector to scanner
                _scanner = self._services.get("scanner")
                if _scanner:
                    _scanner.regime_detector = detector

                # Issue 5 of cascade-fix series (2026-05-10): late-wire
                # regime_detector to Layer4ProtectionService. The L4
                # service is constructed at line ~1323, BEFORE the
                # RegimeDetector is built (here, line ~1469), so its
                # ``regime_detector`` attribute was captured as None at
                # __init__. Without this late-wire,
                # ``compute_structural_invalidation`` returned
                # ``(False, "no_data:services_unwired")`` perpetually,
                # which the time-decay calculator
                # (time_decay_sl.py:397-412) treats as
                # "structure intact" and blocks every force-close.
                # Phase 0 baseline observed 130 services_unwired events
                # in a 2-hour window, with a perfect 1:1 match against
                # 130 TIME_DECAY_STRUCT_GUARD blocks — confirming the
                # gate was firing constantly and silently disabling
                # loser-lane force-closes.
                # Pattern matches the watchdog/profiler/scanner late-
                # wires above (which already work correctly).
                # structure_cache is created earlier (line ~217-223,
                # before L4 construction) so it does not need a
                # late-wire — but we re-attach it defensively to make
                # the wire idempotent against future reorderings.
                _l4 = self._services.get("layer4_protection")
                if _l4:
                    _l4.regime_detector = detector
                    _l4.structure_cache = self._services.get("structure_cache")
                    log.info(
                        f"L4_LATE_WIRE | "
                        f"regime_detector={'ok' if _l4.regime_detector else 'MISSING'} "
                        f"structure_cache={'ok' if _l4.structure_cache else 'MISSING'} "
                        f"| {ctx()}"
                    )

                # Strategy engine requires scanner + regime + ta
                if self._services.get("scanner"):
                    registry = StrategyRegistry(
                        regime_filter_enabled=s.strategy_engine.strategy_regime_filter_enabled,
                    )
                    registry._paper_mode = s.bybit.testnet  # All strategies active on testnet
                    scorer = TradeScorer(s)
                    # Mid-Hold Trade Management Fix Phase 3.4 — pass the
                    # shared EnsembleStateCache so vote() writes through
                    # per-symbol consensus for the watchdog to read.
                    # Layer 3 (2026-05-22) — construct the regime weight
                    # deriver and pass it to EnsembleVoter so the shadow
                    # path computes regime-conditional consensus each cycle.
                    # All bounds + cold-start threshold + sensitivity +
                    # smoothing come from operator-approved settings
                    # (regime_weighting_*). Default flag is False so this
                    # is shadow-only at first boot; flipping to True
                    # promotes shadow to live with no code change.
                    from src.strategies.regime_weighter import StrategyWeightDeriver
                    regime_weighter = StrategyWeightDeriver(
                        cold_start_n=s.strategy_engine.regime_weighting_cold_start_n,
                        floor=s.strategy_engine.regime_weighting_floor,
                        ceil=s.strategy_engine.regime_weighting_ceil,
                        sensitivity=s.strategy_engine.regime_weighting_sensitivity,
                        ema_alpha=s.strategy_engine.regime_weighting_ema_alpha,
                    )
                    self._services["regime_weighter"] = regime_weighter
                    ensemble = EnsembleVoter(
                        registry, s,
                        state_cache=self._services.get("ensemble_state_cache"),
                        regime_weighter=regime_weighter,
                    )
                    pnl_mgr = DailyPnLManager(
                        s,
                        account_service=self._services.get("account"),
                        position_service=self._services.get("position"),
                        db=db,
                    )
                    smart_lev = SmartLeverage(s)

                    self._services["registry"] = registry
                    self._services["pnl_manager"] = pnl_mgr

                    strat_worker = StrategyWorker(
                        settings=s, db=db,
                        registry=registry,
                        scanner=self._services["scanner"],
                        regime_detector=detector,
                        scorer=scorer,
                        ensemble=ensemble,
                        pnl_manager=pnl_mgr,
                        ta_engine=ta,
                        market_repo=market_repo,
                        services=self._services,
                    )
                    # Wire altdata services for scoring context
                    strat_worker._fear_greed = self._services.get("fear_greed")
                    strat_worker._funding_tracker = self._services.get("funding")
                    self.workers.append(strat_worker)
                    self._services["strategy_worker"] = strat_worker
        except Exception as e:
            log.warning("Strategy engine workers unavailable: {err}", err=str(e))

        # Strategy Factory Workers (Discovery + Live Monitor)
        if s.factory.enabled:
            try:
                from src.factory.discoverer import PatternDiscoverer
                from src.factory.generator import StrategyGenerator
                from src.factory.validator import CodeValidator
                from src.factory.live_monitor import LivePatternMonitor
                from src.workers.discovery_worker import DiscoveryWorker
                from src.workers.live_monitor_worker import LiveMonitorWorker

                discoverer = PatternDiscoverer(db, s)
                generator = StrategyGenerator(
                    s,
                    claude_client=self._services.get("claude_client"),
                    cost_tracker=self._services.get("cost_tracker"),
                )
                validator = CodeValidator(s)
                monitor = LivePatternMonitor(db, s)

                self.workers.append(DiscoveryWorker(s, db, discoverer, generator, validator))
                self.workers.append(LiveMonitorWorker(s, db, monitor))

                # Backtest + Trial workers
                from src.factory.backtester import BacktestEngine
                from src.factory.lifecycle import StrategyLifecycleManager
                from src.factory.trial_manager import TrialManager
                from src.workers.backtest_worker import BacktestWorker
                from src.workers.trial_monitor_worker import TrialMonitorWorker

                bt_engine = BacktestEngine(s)
                lifecycle = StrategyLifecycleManager(db, s)
                trial_mgr = TrialManager(db, s, lifecycle)
                self.workers.append(BacktestWorker(s, db, bt_engine, lifecycle, trial_mgr))
                self.workers.append(TrialMonitorWorker(s, db, trial_mgr))
            except Exception as e:
                log.warning("Factory workers unavailable: {err}", err=str(e))

        # Portfolio Services (used by Fund Manager — workers replaced by fund_manager_worker)
        if hasattr(s, 'portfolio') and s.portfolio.enabled:
            try:
                from src.portfolio.risk_budget import RiskBudgetManager
                from src.portfolio.kelly import KellyCalculator
                from src.portfolio.correlation import CorrelationTracker

                risk_budget = RiskBudgetManager(s, db)
                kelly = KellyCalculator(s)
                corr_tracker = CorrelationTracker(db, s)
                self._services["risk_budget"] = risk_budget
                self._services["kelly"] = kelly
                self._services["correlation_tracker"] = corr_tracker
                # AllocationWorker and OptimizationWorker removed —
                # replaced by IntelligentFundManager (M1, M8 modules)
            except Exception as e:
                log.warning("Portfolio services unavailable: {err}", err=str(e))

        # Interactive Telegram Bot + Price Alert Workers
        if hasattr(s, 'telegram_interactive') and s.telegram_interactive.enabled:
            try:
                from src.telegram.bot import InteractiveTelegramBot
                from src.telegram.features.price_alerts import PriceAlertEngine
                from src.workers.telegram_bot_worker import TelegramBotWorker
                from src.workers.price_alert_worker import PriceAlertWorker

                tg_bot = InteractiveTelegramBot(s, db, self._services)
                self._services["telegram_bot"] = tg_bot
                self.workers.append(TelegramBotWorker(s, db, tg_bot))

                alert_engine = PriceAlertEngine(db)
                self.workers.append(PriceAlertWorker(
                    s, db, alert_engine,
                    market_service=self._services.get("market"),
                ))

                from src.telegram.features.scheduled_reports import ScheduledReportEngine
                from src.workers.scheduled_report_worker import ScheduledReportWorker
                report_engine = ScheduledReportEngine(db)
                self.workers.append(ScheduledReportWorker(s, db, report_engine))
            except Exception as e:
                log.warning("Telegram interactive workers unavailable: {err}", err=str(e))

        # Daily universe-refresh (Phase 2): shared Call-A pause state +
        # orchestrator. Registered unconditionally so the scheduled worker
        # (Phase 3) and the Telegram button (Phase 4) can drive it; the
        # selection only runs when invoked. The LayerManager reads the state
        # to pause ONLY Call-A during a refresh (open positions keep their
        # full exit management throughout).
        try:
            from src.core.universe_refresh import (
                UniverseRefreshState,
                UniverseRefreshOrchestrator,
            )
            if "universe_refresh_state" not in self._services:
                self._services["universe_refresh_state"] = UniverseRefreshState()
            self._services["universe_refresh"] = UniverseRefreshOrchestrator(
                s, db, self._services,
            )
            # Phase 3: the scheduled trigger (23:00 / 11:00 UTC). Registered
            # unconditionally; it self-gates on [universe.refresh].enabled, so
            # it stays dormant until the feature is turned on at the Phase 5 gate.
            from src.workers.universe_refresh_worker import UniverseRefreshWorker
            self.workers.append(UniverseRefreshWorker(s, db, self._services))
            _ur = s.universe.refresh
            log.info(
                "UNIVERSE_REFRESH_CONFIG | enabled={en} schedule_utc={sch} "
                "strict_floor={fl} min={mn} target={tg} softened_floor={sf} "
                "ceiling={cl} excludes={ex} | universe refresh wired",
                en=_ur.enabled, sch=_ur.schedule_hours_utc,
                fl=_ur.whipsaw_min_directionality, mn=_ur.min_universe_size,
                tg=_ur.target_universe_size, sf=_ur.softened_min_directionality,
                cl=_ur.volatility_ceiling_pct, ex=len(_ur.exclude_symbols),
            )
        except Exception as e:
            log.warning("Universe refresh wiring unavailable: {err}", err=str(e))

        # Register ALL strategies into the registry
        registry = self._services.get("registry")
        if registry:
            try:
                from src.strategies.register_all import register_all_strategies
                if registry.count == 0:
                    register_all_strategies(registry)
            except Exception as e:
                log.warning("Strategy registration failed: {err}", err=str(e))

        # Performance Enforcer
        if hasattr(s, 'enforcer') and s.enforcer.enabled:
            try:
                from src.strategies.performance_enforcer import PerformanceEnforcer
                from src.workers.enforcer_worker import EnforcerWorker

                enforcer = PerformanceEnforcer(s, db, self._services)
                self._services["enforcer"] = enforcer
                self.workers.append(EnforcerWorker(s, db, enforcer))

                # Wire enforcer into strategy worker
                for w in self.workers:
                    if hasattr(w, 'name') and w.name == "strategy_worker":
                        w._enforcer = enforcer
            except Exception as e:
                log.warning("Enforcer unavailable: {err}", err=str(e))

        # Intelligent Fund Manager
        if hasattr(s, 'fund_manager') and s.fund_manager.enabled:
            try:
                from src.fund_manager.manager import IntelligentFundManager
                from src.workers.fund_manager_worker import FundManagerWorker

                fund_mgr = IntelligentFundManager(s, db, self._services)
                self._services["fund_manager"] = fund_mgr
                self.workers.append(FundManagerWorker(s, db, fund_mgr))

                # Fund manager is now accessed via services dict by RuleEngine
            except Exception as e:
                log.warning("Fund Manager unavailable: {err}", err=str(e))

        # Phase 5 (post-Layer-1 fix). Fund Reconciler — periodic
        # disk-vs-exchange wallet drift detector. Independent of the
        # fund_manager_worker so the cadence and alert thresholds can
        # tune separately. Skipped when account_service is missing
        # (paper-only deployments without a Bybit wallet).
        if (
            hasattr(s, "fund_manager")
            and getattr(s.fund_manager, "reconcile_enabled", True)
            and (
                self._services.get("account_service")
                or self._services.get("account")
            )
        ):
            try:
                from src.workers.fund_reconciler import FundReconciler
                self.workers.append(FundReconciler(s, db, self._services))
            except Exception as e:
                log.warning("Fund Reconciler unavailable: {err}", err=str(e))
        else:
            log.info(
                "FUND_RECONCILER_DISABLED | reason=no_account_service_or_disabled "
                "| impact=balance_drift_undetected"
            )

        # J1 Phase 3 Step B (2026-05-14) — PositionReconciler sibling.
        # Pure observability (no auto-correct). Detects position-count
        # and margin-in-use drift that fund_reconciler's equity-only
        # comparison is structurally blind to. See
        # dev_notes/seven_fixes/j1_phase1_reconciler_gaps.md.
        #
        # Mirrors fund_reconciler's gating: requires position_service
        # or it would skip every tick. Independent of fund_manager —
        # the count-comparison dimension still works when fund_manager
        # is absent; the margin dimension silently degrades.
        if self._services.get("position_service") or self._services.get("position"):
            try:
                from src.workers.position_reconciler import PositionReconciler
                self.workers.append(
                    PositionReconciler(s, db, self._services),
                )
            except Exception as e:
                log.warning(
                    "Position Reconciler unavailable: {err}", err=str(e),
                )
        else:
            log.info(
                "POSITION_RECONCILER_DISABLED | reason=no_position_service "
                "| impact=position_count_drift_undetected"
            )

        # ── PnL-truth (2026-06-07) ──
        # Boot sentinel: one greppable line confirming the close-PnL provenance
        # path is active (ws_exec) and the reconcile settings are loaded — so an
        # operator can verify the truth path is on, not the legacy stale-row path.
        _pnl_bd = getattr(s, "bybit_demo", None)
        if _pnl_bd is not None:
            log.info(
                "PNL_TRUTH_SENTINEL | close_pnl_source={src} provisional={pv} "
                "reconcile={rc} max_attempts={ma} interval_s={iv} budget_s={bg} "
                "| no_ctx".format(
                    src=getattr(_pnl_bd, "close_pnl_source", "?"),
                    pv=getattr(_pnl_bd, "close_pnl_provisional", "?"),
                    rc=getattr(_pnl_bd, "close_pnl_reconcile", "?"),
                    ma=getattr(_pnl_bd, "close_pnl_reconcile_max_attempts", "?"),
                    iv=getattr(_pnl_bd, "close_pnl_reconcile_interval_s", "?"),
                    bg=getattr(_pnl_bd, "close_pnl_reconcile_total_budget_s", "?"),
                )
            )
        # PnL reconciler — corrects provisionally-booked closes to the
        # exchange-authoritative net once Bybit's closed-pnl indexer catches up.
        # Gated on bybit_demo enabled + close_pnl_reconcile (the WS in-call retry
        # covers the common short lag; this is the tail safety net).
        if (
            _pnl_bd is not None
            and getattr(_pnl_bd, "enabled", False)
            and getattr(_pnl_bd, "close_pnl_reconcile", True)
        ):
            try:
                from src.workers.pnl_reconciler import PnLReconciler
                self.workers.append(PnLReconciler(s, db, self._services))
            except Exception as e:
                log.warning("PnL Reconciler unavailable: {err}", err=str(e))
        else:
            log.info(
                "PNL_RECONCILER_DISABLED | reason=bybit_demo_off_or_reconcile_disabled"
            )

        # Cleanup always runs
        self.workers.append(CleanupWorker(s, db))

        # Phase 11 (dead-workers fix) — WorkerLivenessWatchdog. Appended
        # last so every other worker is already in self.workers when the
        # watchdog's first tick runs. Self-contained: no shared services
        # that the dead workers depend on, so a failure of (say)
        # structure_engine cannot also disable the watchdog. AlertManager
        # is optional — the watchdog still emits structured warnings to
        # workers.log when it's None.
        try:
            from src.workers.worker_liveness_watchdog import (
                WorkerLivenessWatchdog,
            )
            wl_settings = getattr(s, "worker_liveness", None)
            if wl_settings is None:
                # Defensive default (matches dataclass defaults).
                _wl_interval = 30.0
                _wl_grace = 90.0
                _wl_overdue = 2.0
                _wl_rate_limit = 3600.0
            else:
                _wl_interval = float(wl_settings.watchdog_interval_sec)
                _wl_grace = float(wl_settings.first_tick_grace_sec)
                _wl_overdue = float(wl_settings.overdue_multiplier)
                _wl_rate_limit = float(wl_settings.alert_rate_limit_sec)
            liveness_watchdog = WorkerLivenessWatchdog(
                s,
                db,
                tracker=self._worker_liveness,
                watchdog_interval_sec=_wl_interval,
                first_tick_grace_sec=_wl_grace,
                overdue_multiplier=_wl_overdue,
                alert_rate_limit_sec=_wl_rate_limit,
                alert_manager=self._services.get("alert_manager"),
            )
            self.workers.append(liveness_watchdog)
            self._services["worker_liveness_watchdog"] = liveness_watchdog
        except Exception as e:
            # Watchdog construction must never block boot — log loudly
            # and proceed without it. Operators can grep for this tag.
            log.warning(
                "WORKER_LIVENESS_WATCHDOG_INIT_FAIL | err='{err}' | "
                "manager_continues_without_watchdog",
                err=str(e)[:120],
            )

        for w in self.workers:
            self.health.register(w)

    def _wire_coordinator_callbacks(self) -> None:
        """Wire TradeCoordinator close callbacks after all services exist."""
        coordinator = self._services.get("trade_coordinator")
        if not coordinator:
            return

        # Done-callback factory: surfaces async-task exceptions as CLOSE_CB_FAIL
        # log lines. Without this, coroutines spawned from close callbacks
        # swallow errors and only leak to stderr as "Task exception was never
        # retrieved" — too quiet to ever act on.
        def _close_cb_done(label: str, symbol: str):
            def _inner(task):
                exc = task.exception()
                if exc is not None:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb={label} sym={symbol} "
                        f"err='{str(exc)[:150]}' | {ctx()}"
                    )
            return _inner

        # Enforcer gets notified of trade closes
        enforcer = self._services.get("enforcer")
        if enforcer and hasattr(enforcer, "on_trade_closed"):
            def _enforcer_close_callback(record):
                try:
                    enforcer.on_trade_closed(record["pnl_pct"], record["was_win"])
                except Exception as e:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb=enforcer sym={record.get('symbol','?')} "
                        f"err='{str(e)[:150]}' | {ctx()}"
                    )
            coordinator.register_close_callback(_enforcer_close_callback)

        # Fund Manager gets notified of trade closes
        fund_mgr = self._services.get("fund_manager")
        if fund_mgr:
            import asyncio as _aio

            def _fund_close_callback(record):
                sym = record.get("symbol", "?")
                try:
                    _t = _aio.get_event_loop().create_task(
                        fund_mgr.on_trade_closed(
                            symbol=record["symbol"],
                            pnl_usd=record["pnl_usd"],
                            pnl_pct=record["pnl_pct"],
                            was_win=record["was_win"],
                        )
                    )
                    _t.add_done_callback(_close_cb_done("fund", sym))
                except Exception as e:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb=fund sym={sym} err='{str(e)[:150]}' | {ctx()}"
                    )

            coordinator.register_close_callback(_fund_close_callback)

        # Strategy performance DB update on trade close
        db = self.db

        def _perf_close_callback(record):
            import asyncio as _perf_aio
            sym = record.get("symbol", "?")
            try:
                _t = _perf_aio.get_event_loop().create_task(
                    self._update_strategy_performance(db, record)
                )
                _t.add_done_callback(_close_cb_done("perf", sym))
            except Exception as e:
                log.warning(
                    f"CLOSE_CB_FAIL | cb=perf sym={sym} err='{str(e)[:150]}' | {ctx()}"
                )

        coordinator.register_close_callback(_perf_close_callback)

        # Strategy registry in-memory performance update
        registry = self._services.get("registry")
        if registry and hasattr(registry, "update_performance"):
            def _registry_callback(record):
                try:
                    name = record.get("strategy_name", "")
                    if name:
                        registry.update_performance(name, record["pnl_pct"], record["was_win"])
                except Exception as e:
                    log.warning("Registry perf update failed: {err}", err=str(e))

            coordinator.register_close_callback(_registry_callback)

        # PnL manager trade close tracking
        pnl_mgr = self._services.get("pnl_manager")
        if pnl_mgr and hasattr(pnl_mgr, "on_trade_closed"):
            def _pnl_close_callback(record):
                import asyncio as _pnl_aio
                sym = record.get("symbol", "?")
                try:
                    # PnL-truth fix (2026-05-26): accumulate NET DOLLARS
                    # (record["pnl_usd"], now the exchange's real closedPnl)
                    # instead of record["pnl_pct"]. The dashboard prints
                    # realized_pnl as "$", and the daily aggression mode is
                    # driven by current_pnl_pct = realized/starting_equity;
                    # feeding percent here produced a meaningless dollar
                    # figure and a wrong mode%. Operator chose "truth
                    # everywhere", so the mode now reacts to real PnL.
                    _t = _pnl_aio.get_event_loop().create_task(
                        pnl_mgr.on_trade_closed(
                            record["pnl_usd"],
                            symbol=record.get("symbol", ""),
                            pnl_pct=record.get("pnl_pct", 0.0),
                        )
                    )
                    _t.add_done_callback(_close_cb_done("pnl", sym))
                except Exception as e:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb=pnl sym={sym} err='{str(e)[:150]}' | {ctx()}"
                    )

            coordinator.register_close_callback(_pnl_close_callback)

        # Telegram trade-close notification (every completed trade)
        alert_mgr = self._services.get("alert_manager")
        if alert_mgr and alert_mgr.enabled:
            def _tg_close_callback(record):
                import asyncio as _tg_aio
                from src.core.types import Side as _Side
                sym = record.get("symbol", "?")
                try:
                    side_str = record.get("direction", "")
                    if not side_str:
                        return
                    _t = _tg_aio.get_event_loop().create_task(
                        alert_mgr.send_position_closed_alert(
                            symbol=sym,
                            side=_Side(side_str),
                            entry_price=record.get("entry_price", 0.0),
                            exit_price=record.get("close_price", 0.0),
                            pnl=record.get("pnl_usd", 0.0),
                            pnl_pct=record.get("pnl_pct", 0.0),
                        )
                    )
                    _t.add_done_callback(_close_cb_done("telegram", sym))
                except Exception as e:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb=telegram sym={sym} err='{str(e)[:150]}' | {ctx()}"
                    )
            coordinator.register_close_callback(_tg_close_callback)

            # F5 part 3 (2026-06-09 phantom-close follow-up): also register the
            # DailyPnLManager on the CORRECTION channel. Its running totals
            # accumulate on the close channel and persist/restore their own daily
            # row, so they do NOT self-heal when a reconcile FLIPS a provisional
            # win into a real loss (unlike the enforcer, which recomputes from
            # trade_thesis each tick). on_trade_corrected reverses the wrong booking
            # and applies the authoritative one. Fires ONLY on a genuine flip, so it
            # never double-counts a normal fee-only correction.
            if hasattr(pnl_mgr, "on_trade_corrected") and hasattr(
                coordinator, "register_correction_callback"
            ):
                def _pnl_correction_callback(record):
                    import asyncio as _pnlc_aio
                    sym = record.get("symbol", "?")
                    try:
                        _tc = _pnlc_aio.get_event_loop().create_task(
                            pnl_mgr.on_trade_corrected(record)
                        )
                        _tc.add_done_callback(_close_cb_done("pnl_correction", sym))
                    except Exception as e:
                        log.warning(
                            f"CORRECTION_CB_FAIL | cb=pnl sym={sym} "
                            f"err='{str(e)[:150]}' | {ctx()}"
                        )

                coordinator.register_correction_callback(_pnl_correction_callback)

        # Thesis manager — close theses when trades close (Issue #2)
        thesis_manager = self._services.get("thesis_manager")
        if thesis_manager:
            import asyncio as _thesis_aio

            def _thesis_close_callback(record):
                sym = record.get("symbol", "?")
                try:
                    _t = _thesis_aio.get_event_loop().create_task(
                        thesis_manager.close_thesis(
                            symbol=record["symbol"],
                            close_price=record.get("close_price", 0),
                            actual_pnl_pct=record["pnl_pct"],
                            actual_pnl_usd=record["pnl_usd"],
                            close_reason=record["closed_by"],
                            # Definitive-fix Phase 8 — forward exchange
                            # order_id so close_thesis can scope the
                            # UPDATE by (symbol, order_id) instead of
                            # symbol alone. record carries this from
                            # TradeCoordinator.on_trade_closed.
                            order_id=record.get("order_id", "") or "",
                        )
                    )
                    _t.add_done_callback(_close_cb_done("thesis", sym))
                    # Mid-Hold Trade Management Fix Phase 3.6 (2026-05-19)
                    # — purge any unconsumed thesis_events rows for the
                    # closed order_id so the queue stays lean and a re-
                    # entry on the same symbol starts with a clean slate.
                    # Fire-and-forget alongside close_thesis; both
                    # operate on disjoint tables so no ordering needed.
                    _oid = record.get("order_id", "") or ""
                    if _oid:
                        try:
                            _tp = _thesis_aio.get_event_loop().create_task(
                                thesis_manager.purge_events_for_closed_position(_oid)
                            )
                            _tp.add_done_callback(
                                _close_cb_done("thesis_events_purge", sym),
                            )
                        except Exception as _ee:
                            log.warning(
                                f"THESIS_EVENTS_PURGE_SCHEDULE_FAIL | sym={sym} "
                                f"err='{str(_ee)[:120]}' | {ctx()}"
                            )
                except Exception as e:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb=thesis sym={sym} err='{str(e)[:150]}' | {ctx()}"
                    )

            coordinator.register_close_callback(_thesis_close_callback)

            # PnL-truth reconcile (2026-06-07; hardened after the Pass-3 runtime
            # audit). The original wiring re-fired _thesis_close_callback on the
            # reconcile channel assuming close_thesis was idempotent — but its
            # UPDATE is gated to status='open' (or a zero-pnl zombie row) to guard
            # the S5 cross-close regression, so it is a NO-OP on a normally-closed
            # row and never carried the reconciler's correction to trade_thesis.
            # Use a dedicated reconcile callback that rewrites ONLY the outcome
            # fields of the already-closed row by (symbol, order_id).
            def _thesis_reconcile_callback(record):
                sym = record.get("symbol", "?")
                _oid = record.get("order_id", "") or ""
                try:
                    _t = _thesis_aio.get_event_loop().create_task(
                        thesis_manager.update_outcome_by_order_id(
                            symbol=record["symbol"],
                            order_id=_oid,
                            actual_pnl_usd=record["pnl_usd"],
                            actual_pnl_pct=record["pnl_pct"],
                            close_price=record.get("close_price", 0) or 0,
                        )
                    )
                    _t.add_done_callback(_close_cb_done("thesis_reconcile", sym))
                except Exception as e:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb=thesis_reconcile sym={sym} "
                        f"err='{str(e)[:150]}' | {ctx()}"
                    )

            coordinator.register_reconcile_callback(_thesis_reconcile_callback)

        # Data Lake — record trade close (#10)
        data_lake = self._services.get("data_lake")
        if data_lake:
            import asyncio as _dl_aio

            def _data_lake_close_callback(record):
                sym = record.get("symbol", "?")
                try:
                    # P8 of P1-P10: resolve current mode from transformer
                    # so trade_log.exchange_mode is tagged correctly. The
                    # audit-flagged 116-row mistag (all "shadow" tagged
                    # rows since 2026-05-08 11:27 enable were actually
                    # bybit_demo) re-occurs without this. Falls back to
                    # empty string when transformer unavailable; data_lake
                    # WARNING surfaces the gap.
                    _xfm = self._services.get("transformer")
                    _mode = ""
                    if _xfm is not None:
                        try:
                            _mode = str(_xfm.current_mode or "")
                        except Exception:
                            _mode = ""
                    _t = _dl_aio.get_event_loop().create_task(
                        data_lake.write_trade(
                            trade_id=record.get("trade_id", ""),
                            symbol=record["symbol"],
                            direction=record.get("direction", ""),
                            entry_price=record.get("entry_price", 0),
                            exit_price=record.get("close_price", 0),
                            pnl_pct=record["pnl_pct"],
                            pnl_usd=record["pnl_usd"],
                            strategy=record.get("strategy_name", ""),
                            close_reason=record["closed_by"],
                            hold_minutes=record["hold_seconds"] / 60,
                            # CRITICAL-2 fix — forward opened_at populated
                            # by trade_coordinator.on_trade_closed from
                            # state.opened_at_dt. Without this, trade_log
                            # rows have empty opened_at (audit: 116/116
                            # bybit_demo + 1597/1597 shadow) and any
                            # WHERE opened_at >= ... query returns empty.
                            opened_at=record.get("opened_at", ""),
                            closed_at=record.get("closed_at", ""),
                            exchange_mode=_mode,
                        )
                    )
                    _t.add_done_callback(_close_cb_done("data_lake", sym))
                except Exception as e:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb=data_lake sym={sym} err='{str(e)[:150]}' | {ctx()}"
                    )

            coordinator.register_close_callback(_data_lake_close_callback)
            # PnL-truth reconcile (2026-06-07): write_trade is INSERT OR REPLACE
            # by trade_id (idempotent), so re-fire it to correct the trade_log row.
            coordinator.register_reconcile_callback(_data_lake_close_callback)
            # Issue 4 fix (2026-05-11) — also register on the partial
            # close-callback list so coordinator.on_partial_close writes
            # a trade_log row per partial event. The callback reads
            # record["trade_id"], record["size"] (closed qty for partials),
            # and record["pnl_usd"] (computed on closed-portion notional),
            # which on_partial_close already builds correctly. No
            # callback-side change needed; just dual-register.
            if hasattr(coordinator, "register_partial_close_callback"):
                coordinator.register_partial_close_callback(
                    _data_lake_close_callback,
                )

        # ── CRITICAL-3 (2026-05-09) — trade_history close callback ─────
        # Fixes the audit's CRITICAL-3 (86 of 116 bybit_demo closes
        # missing from trade_history) AND ISSUE 1.4-A (trade_id collision
        # via the `bd-{symbol}-close` fallback). The adapter's direct
        # save_trade at bybit_demo_adapter.py:413 has been removed in
        # favour of this single coordinator-level writer that fires for
        # ALL coordinator paths (WS event, watchdog poll, sniper,
        # time-decay) — same pattern as trade_log/intelligence/thesis.
        #
        # Mode-gated to bybit_demo so shadow's existing behavior is
        # unchanged (shadow has its own persistence pattern; the prompt
        # explicitly requires "Shadow's behavior must not change without
        # operator approval").
        bd_trading_repo = self._services.get("bybit_demo_trading_repo")
        if bd_trading_repo is not None:
            import asyncio as _th_aio

            def _trade_history_close_callback(record: dict) -> None:
                sym = record.get("symbol", "?")
                # Mode gate — bybit_demo only. shadow path unchanged.
                _xfm = self._services.get("transformer")
                _mode = ""
                if _xfm is not None:
                    try:
                        _mode = str(_xfm.current_mode or "")
                    except Exception:
                        _mode = ""
                if _mode != "bybit_demo":
                    return

                try:
                    from datetime import datetime, timezone

                    from src.core.types import Side, TradeRecord

                    # Trade_id derivation: state.order_id (open-side
                    # exchange orderId, unique per trade) is the canonical
                    # identifier. Falls back to opened_at-anchored epoch
                    # ms when state was popped or never had order_id —
                    # both forms are unique and avoid the audit-flagged
                    # `bd-{symbol}-close` collision pattern.
                    open_oid = record.get("order_id", "") or ""
                    if open_oid:
                        trade_id = f"bd-{open_oid}"
                    else:
                        opened_iso = record.get("opened_at", "") or ""
                        try:
                            opened_dt = datetime.fromisoformat(opened_iso)
                            opened_ms = int(opened_dt.timestamp() * 1000)
                        except Exception:
                            opened_ms = int(__import__("time").time() * 1000)
                        trade_id = f"bd-{sym}-{opened_ms}"

                    side_str = record.get("direction", "Buy") or "Buy"
                    side_enum = Side.SELL if side_str in ("Sell", "Short") else Side.BUY

                    entry = float(record.get("entry_price", 0.0) or 0.0)
                    exit_p = float(record.get("close_price", 0.0) or 0.0)
                    qty = float(record.get("size", 0.0) or 0.0)
                    pnl_usd = float(record.get("pnl_usd", 0.0) or 0.0)
                    pnl_pct = float(record.get("pnl_pct", 0.0) or 0.0)

                    opened_iso = record.get("opened_at", "")
                    closed_iso = record.get("closed_at", "")
                    try:
                        opened_dt = (
                            datetime.fromisoformat(opened_iso)
                            if opened_iso
                            else datetime.now(timezone.utc)
                        )
                    except Exception:
                        opened_dt = datetime.now(timezone.utc)
                    try:
                        closed_dt = (
                            datetime.fromisoformat(closed_iso)
                            if closed_iso
                            else datetime.now(timezone.utc)
                        )
                    except Exception:
                        closed_dt = datetime.now(timezone.utc)

                    notes = (
                        f"closed_by={record.get('closed_by', '')} "
                        f"price_source={record.get('price_source', '')}"
                    )
                    trade = TradeRecord(
                        trade_id=trade_id,
                        symbol=sym,
                        side=side_enum,
                        entry_price=entry,
                        exit_price=exit_p,
                        qty=qty,
                        pnl=pnl_usd,
                        pnl_pct=pnl_pct,
                        strategy=record.get("strategy_name", "")[:120],
                        notes=notes[:500],
                        entry_time=opened_dt,
                        exit_time=closed_dt,
                    )

                    async def _do_save() -> None:
                        try:
                            # HIGH-2 fix (2026-05-09): pass exchange_mode
                            # so the new trade_history.exchange_mode
                            # column is tagged correctly. _mode is
                            # already resolved above and gated to
                            # bybit_demo.
                            await bd_trading_repo.save_trade(
                                trade, exchange_mode=_mode,
                            )
                            log.info(
                                f"BD_TRADE_HISTORY_PERSIST_OK | tid={trade_id} "
                                f"sym={sym} pnl_usd={pnl_usd:+.4f} "
                                f"pnl_pct={pnl_pct:+.4f}% qty={qty} "
                                f"side={side_str} mode={_mode} | {ctx()}"
                            )
                        except Exception as e:
                            log.warning(
                                f"BD_TRADE_HISTORY_PERSIST_FAIL | tid={trade_id} "
                                f"sym={sym} err='{str(e)[:150]}' | {ctx()}"
                            )

                    _t = _th_aio.get_event_loop().create_task(_do_save())
                    _t.add_done_callback(_close_cb_done("trade_history", sym))
                except Exception as e:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb=trade_history sym={sym} "
                        f"err='{str(e)[:150]}' | {ctx()}"
                    )

            coordinator.register_close_callback(_trade_history_close_callback)
            # PnL-truth reconcile (2026-06-07): save_trade is INSERT OR REPLACE
            # by trade_id (idempotent), so re-fire it to correct trade_history.
            coordinator.register_reconcile_callback(_trade_history_close_callback)
            # Issue 4 fix (2026-05-11) — also register on the partial
            # close-callback list. on_partial_close derives a unique
            # trade_id via the -partial-{idx} suffix on order_id (see
            # trade_coordinator.on_partial_close), so the
            # _trade_history_close_callback's trade_id derivation
            # (f"bd-{open_oid}") produces non-colliding rows for
            # successive partials and the eventual final close. record
            # ["size"] is the closed qty for partials (not state.size)
            # so trade_history.qty reflects what actually closed.
            if hasattr(coordinator, "register_partial_close_callback"):
                coordinator.register_partial_close_callback(
                    _trade_history_close_callback,
                )

        # ── Issue 2 fix (2026-05-11) — positions-table cleanup ─────────
        # Pre-fix, the positions table relied on
        # BybitDemoOrderService.close_position calling save_position with
        # size=0 to trigger the DELETE-on-zero path at trading_repo.py:
        # 180-184. External SL/TP closes (which fire on Bybit's matching
        # engine, NOT through our close_position adapter) never triggered
        # that DELETE, so external closes leaked 100% of their rows into
        # the zombie set today (6/6 of externally-closed positions
        # remained in the table after close per phase0_baseline.md).
        #
        # This callback runs on EVERY on_trade_closed dispatch — WS-
        # driven, watchdog-driven, sniper M4, manual, etc. — and issues
        # a DELETE FROM positions WHERE symbol=?. Idempotent: empty row
        # → no-op. Mode-gated to bybit_demo so shadow's existing
        # contract stays untouched.
        if bd_trading_repo is not None:
            import asyncio as _pt_aio

            async def _delete_position_with_log(sym: str) -> None:
                """Issue I2 (F-17, 2026-05-14) — emit POSITION_ROW_DELETED
                on success so operators can verify cleanup is firing.
                Pre-I2 the callback ran fire-and-forget with no SUCCESS
                emission; only failures surfaced via CLOSE_CB_FAIL. The
                resulting silence made the 14 live orphan rows
                invisible (Phase 0 baseline)."""
                try:
                    await bd_trading_repo.delete_position(sym)
                    log.info(
                        f"POSITION_ROW_DELETED | sym={sym} "
                        f"src=close_callback | {ctx()}"
                    )
                except Exception as e:
                    # Surface failure so operators see the gap. The
                    # _close_cb_done done-callback would ALSO catch
                    # this, but emitting here ensures the symbol is
                    # in the message body for grep.
                    log.warning(
                        f"POSITION_ROW_DELETE_FAIL | sym={sym} "
                        f"err='{str(e)[:150]}' | {ctx()}"
                    )
                    raise

            def _positions_table_cleanup_on_close(record: dict) -> None:
                """Issue I2 (F-17, 2026-05-14) — fixed two leak paths:

                  1. Pre-fix used ``transformer.current_mode`` as the
                     gating mode; if the transformer wasn't yet attached
                     or current_mode briefly returned a non-bybit_demo
                     value (boot, mid-exchange-switch, SEGV recovery),
                     the callback silently skipped — every close during
                     that window leaked.

                  2. Pre-fix used ``asyncio.get_event_loop()`` which is
                     deprecated and can return a closed loop after
                     shutdown.

                Fixes:
                  * Read ``exchange_mode`` from the close record itself.
                    The record's mode was captured at register_trade time
                    (see G6 work in trade_coordinator) so it reflects
                    the TRADE'S mode, not the GLOBAL mode at close-time.
                  * Use ``asyncio.get_running_loop()`` which raises if
                    no loop is active (turning a silent failure into a
                    visible CLOSE_CB_FAIL).
                  * Emit ``POSITION_ROW_DELETED`` on success (Rule 6).
                  * Log a structured skip event when the mode gates the
                    cleanup, so operators see the gate trip rather than
                    silent skip.
                """
                sym = record.get("symbol", "")
                if not sym:
                    return
                _mode = str(record.get("exchange_mode", "") or "")
                if _mode != "bybit_demo":
                    log.debug(
                        f"POSITION_ROW_DELETE_SKIP | sym={sym} "
                        f"reason=mode_not_bybit_demo mode={_mode or 'empty'} "
                        f"| {ctx()}"
                    )
                    return
                try:
                    _loop = _pt_aio.get_running_loop()
                    _t = _loop.create_task(_delete_position_with_log(sym))
                    _t.add_done_callback(_close_cb_done("positions_cleanup", sym))
                except Exception as e:
                    log.warning(
                        f"CLOSE_CB_FAIL | cb=positions_cleanup sym={sym} "
                        f"err='{str(e)[:150]}' | {ctx()}"
                    )

            coordinator.register_close_callback(_positions_table_cleanup_on_close)

        # ── Phase 2 (P0-1) — close-broadcast cleanup callbacks ─────────
        # These four callbacks ensure that EVERY subsystem with per-symbol
        # state drops it the moment a position closes — eliminating ghost
        # state where the watchdog detected the close but the sniper /
        # event_buffer / transformer / strategist still treat the symbol
        # as live. Each callback is defensive: missing service → no-op.

        def _sniper_unsubscribe_on_close(record: dict) -> None:
            sym = record.get("symbol")
            if not sym:
                return
            sniper = self._services.get("profit_sniper")
            if sniper is None or not hasattr(sniper, "_on_position_closed"):
                return
            try:
                sniper._on_position_closed(sym)
            except Exception as e:
                log.warning(
                    f"CLOSE_CB_FAIL | cb=sniper_unsubscribe sym={sym} "
                    f"err='{str(e)[:150]}' | {ctx()}"
                )

        coordinator.register_close_callback(_sniper_unsubscribe_on_close)

        def _event_buffer_clear_on_close(record: dict) -> None:
            sym = record.get("symbol")
            if not sym:
                return
            ev_buf = self._services.get("event_buffer")
            if ev_buf is None or not hasattr(ev_buf, "clear_for_symbol"):
                return
            try:
                ev_buf.clear_for_symbol(sym)
            except Exception as e:
                log.warning(
                    f"CLOSE_CB_FAIL | cb=event_buffer_clear sym={sym} "
                    f"err='{str(e)[:150]}' | {ctx()}"
                )

        coordinator.register_close_callback(_event_buffer_clear_on_close)

        def _transformer_cache_clear_on_close(record: dict) -> None:
            sym = record.get("symbol")
            if not sym:
                return
            tf = self._services.get("transformer")
            if tf is None or not hasattr(tf, "invalidate_position_cache"):
                return
            try:
                tf.invalidate_position_cache(sym)
            except Exception as e:
                log.warning(
                    f"CLOSE_CB_FAIL | cb=transformer_cache_clear sym={sym} "
                    f"err='{str(e)[:150]}' | {ctx()}"
                )

        coordinator.register_close_callback(_transformer_cache_clear_on_close)

        def _strategist_position_invalidate_on_close(record: dict) -> None:
            sym = record.get("symbol")
            if not sym:
                return
            strat = self._services.get("strategist")
            if strat is None or not hasattr(strat, "invalidate_position"):
                return
            try:
                strat.invalidate_position(sym)
            except Exception as e:
                log.warning(
                    f"CLOSE_CB_FAIL | cb=strategist_invalidate sym={sym} "
                    f"err='{str(e)[:150]}' | {ctx()}"
                )

        coordinator.register_close_callback(_strategist_position_invalidate_on_close)

        # T1-1 / F18 phantom-close fix (six-tier-fixes 2026-05-11) — drop
        # any UrgentQueue concerns queued for the closing symbol so they
        # do not leak into the next CALL_A or CALL_B and drive Claude
        # to issue close directives on positions that no longer exist.
        # See dev_notes/six_tier_fixes/t1_1_phase1_investigation.md and
        # t1_1_phase2_proposal.md.
        def _urgent_queue_clear_on_close(record: dict) -> None:
            sym = record.get("symbol", "")
            if not sym:
                return
            uq = self._services.get("urgent_queue")
            if uq is None or not hasattr(uq, "clear_for_symbol"):
                return
            try:
                uq.clear_for_symbol(sym)
            except Exception as e:
                log.warning(
                    f"CLOSE_CB_FAIL | cb=urgent_queue_clear sym={sym} "
                    f"err='{str(e)[:150]}' | {ctx()}"
                )

        coordinator.register_close_callback(_urgent_queue_clear_on_close)

        # Learning log — track outcomes for pattern improvement
        coordinator.register_close_callback(
            lambda record: log.info(
                "LEARNING: {strat} on {sym} -> {outcome} {pnl:+.2f}% "
                "(held {hold:.0f}s, closed by {by})",
                strat=record["strategy_name"] or "unknown",
                sym=record["symbol"],
                outcome="WIN" if record["was_win"] else "LOSS",
                pnl=record["pnl_pct"],
                hold=record["hold_seconds"],
                by=record["closed_by"],
            )
        )

        # TIAS — Trade Intelligence Autopsy System (#9)
        # Phase 1: captures full market context at trade close.
        # Phase 2: fires DeepSeek analysis as a non-blocking background task.
        try:
            from src.tias.analyzer import TradeAnalyzer
            from src.tias.collector import TradeContextCollector
            from src.tias.deepseek_client import DeepSeekClient, TIASAnalysisError
            from src.tias.repository import TradeIntelligenceRepo

            tias_repo = TradeIntelligenceRepo(db)
            self._services["tias_repo"] = tias_repo  # Phase 5: Telegram dashboard access
            tias_collector = TradeContextCollector(self._services, db)

            # Issue #2/#3 fix (2026-05-25): boot sentinel confirming the category
            # semantic contract is live — definitions in the prompt, validation in
            # the analyzer, and the win=0 failure filter in the situation query.
            # Lets the operator confirm both fixes are active from one log line
            # after restart, without guessing.
            from src.tias.categories import CONTRACT_SUMMARY as _CAT_CONTRACT
            log.info(
                "TIAS_CATEGORY_CONTRACT | {s} validation=on issue2_filter=win0 | "
                "category semantic contract loaded",
                s=_CAT_CONTRACT,
            )

            # Phase 2 — build analyzer if enabled and API key is present
            tias_analyzer: TradeAnalyzer | None = None
            tias_cfg = self.settings.tias
            if tias_cfg.enabled and tias_cfg.api_key:
                tias_client = DeepSeekClient(
                    api_key=tias_cfg.api_key,
                    api_url=tias_cfg.api_url,
                    http_referer=tias_cfg.http_referer,
                    x_title=tias_cfg.x_title,
                )
                tias_analyzer = TradeAnalyzer(client=tias_client, settings=tias_cfg)
                log.info(
                    "TIAS: DeepSeek analyzer ENABLED | model={model} version={v}",
                    model=tias_cfg.primary_model,
                    v=tias_cfg.analysis_version,
                )
            else:
                reason = "disabled in config" if not tias_cfg.enabled else "no API key"
                log.info("TIAS: DeepSeek analyzer DISABLED ({reason})", reason=reason)

            async def _tias_analyze_background(
                row_id: int,
                trade_obj,
                symbol: str,
                order_id: str = "",
                close_reason: str = "",
                hold_seconds: float = 0.0,
                pnl_pct: float = 0.0,
            ) -> None:
                """Non-blocking Phase 2 analysis — called via create_task().

                T1-3 / F9 fix (six-tier-fixes 2026-05-11) — after the
                DeepSeek roundtrip lands, compose a concise lesson from
                ``ds_what_should_done`` / ``ds_how_to_exploit`` and
                bridge it to ``trade_thesis.lesson`` via
                ``thesis_manager.update_lesson`` so the strategist's
                CALL_A "LESSONS FROM RECENT TRADES" block (with the
                new age + symbol-scope guards) sees real content.
                Bridge is best-effort; failure does not block the TIAS
                Phase 2 success log.
                """
                try:
                    analysis = await tias_analyzer.analyze(trade_obj)  # type: ignore[union-attr]
                    await tias_repo.update_analysis(row_id, analysis)
                    log.info(
                        "TIAS_ANALYZED | id={id} sym={sym} cat={cat} "
                        "conf={conf} cost=${cost:.6f} ms={ms}",
                        id=row_id,
                        sym=symbol,
                        cat=analysis.get("ds_category", "?"),
                        conf=analysis.get("ds_confidence", 0.0),
                        cost=analysis.get("ds_cost_usd", 0.0),
                        ms=analysis.get("ds_response_time_ms", 0),
                    )

                    # T1-3 / F9 lesson bridge.
                    tm = self._services.get("thesis_manager")
                    if tm is not None and hasattr(tm, "update_lesson"):
                        from src.core.thesis_manager import compose_lesson_from_tias
                        try:
                            lesson_text = compose_lesson_from_tias(
                                analysis=analysis,
                                close_reason=close_reason,
                                hold_seconds=hold_seconds,
                                pnl_pct=pnl_pct,
                            )
                            if lesson_text:
                                await tm.update_lesson(
                                    symbol=symbol,
                                    order_id=order_id,
                                    lesson=lesson_text,
                                )
                            else:
                                log.debug(
                                    f"TIAS_LESSON_BRIDGE_SKIP | sym={symbol} "
                                    f"reason=empty_ds_what | {ctx()}"
                                )
                        except Exception as _be:
                            log.warning(
                                f"TIAS_LESSON_BRIDGE_FAIL | sym={symbol} "
                                f"err='{str(_be)[:120]}' | {ctx()}"
                            )
                except TIASAnalysisError as e:
                    log.warning(
                        "TIAS_FAIL | id={id} sym={sym} retryable={r} err='{err}'",
                        id=row_id,
                        sym=symbol,
                        r=e.retryable,
                        err=str(e)[:200],
                    )
                except Exception as e:
                    log.error(
                        "TIAS_FAIL_UNEXPECTED | id={id} sym={sym} err='{err}'",
                        id=row_id,
                        sym=symbol,
                        err=str(e)[:200],
                    )

            async def _tias_async_task(record: dict, m4_snapshot) -> None:
                """Phase 1 save, then Phase 2 analysis (non-blocking).

                T1-3 / F9: forwards order_id, close_reason, hold_seconds,
                pnl_pct so the Phase 2 background task can bridge
                lesson content to trade_thesis after the DeepSeek
                analysis lands.
                """
                row_id, trade_obj = await tias_collector.collect_and_save(
                    record, tias_repo, m4_snapshot
                )
                if tias_analyzer is not None and row_id > 0 and trade_obj is not None:
                    import asyncio as _aio
                    _aio.get_event_loop().create_task(
                        _tias_analyze_background(
                            row_id,
                            trade_obj,
                            record.get("symbol", ""),
                            order_id=record.get("order_id", "") or "",
                            close_reason=record.get("closed_by", "") or "",
                            hold_seconds=float(record.get("hold_seconds", 0) or 0),
                            pnl_pct=float(record.get("pnl_pct", 0) or 0),
                        )
                    )

            def _tias_close_callback(record):
                # SYNC: Capture ephemeral ProfitSniper state IMMEDIATELY.
                # Phase 3: Use get_closed_snapshot() first — ProfitSniper now saves
                # a snapshot in _on_position_closed() before deleting _profit_states.
                # Fallback to direct _profit_states read for the race-condition case
                # where TIAS callback fires before ProfitSniper's tick detects the close.
                profit_sniper = self._services.get("profit_sniper")
                m4_snapshot = None
                if profit_sniper:
                    sym = record["symbol"]
                    # Preferred: dedicated snapshot (guaranteed to exist if ProfitSniper tick ran first)
                    if hasattr(profit_sniper, "get_closed_snapshot"):
                        snap = profit_sniper.get_closed_snapshot(sym)
                        if snap:
                            m4_snapshot = snap
                    # Fallback: direct state read (exists if TIAS callback fires before tick cleanup)
                    if m4_snapshot is None:
                        ps = getattr(profit_sniper, "_profit_states", {}).get(sym)
                        if ps is not None:
                            m4_snapshot = {
                                "peak_pnl_pct": getattr(ps, "peak_pnl_pct", None),
                                "ticks_in_profit": getattr(ps, "ticks_in_profit", 0),
                                "ticks_total": getattr(ps, "ticks_total", 0),
                            }
                # ASYNC: Phase 1 collect+save, then Phase 2 analysis
                import asyncio as _tias_aio
                try:
                    _tias_aio.get_event_loop().create_task(
                        _tias_async_task(record, m4_snapshot)
                    )
                except Exception as e:
                    log.error(
                        "TIAS_CB_FAIL | sym={sym} err={err}",
                        sym=record.get("symbol", ""),
                        err=str(e)[:150],
                    )

            coordinator.register_close_callback(_tias_close_callback)
            log.info("TIAS: trade context collector registered as close callback #9")

            # PnL-truth reconcile (2026-06-07): the TIAS close callback INSERTs a
            # new row, so it must NOT be on the reconcile channel (re-firing would
            # duplicate the row and double-count the APEX win-rates). Instead a
            # dedicated reconcile callback UPDATEs the existing row by trade_id to
            # the corrected exchange-authoritative net — going-forward only, one
            # row, the protected table otherwise untouched.
            def _tias_reconcile_callback(record):
                _tid = record.get("trade_id")
                if not _tid:
                    return
                import asyncio as _tias_rec_aio
                try:
                    _t = _tias_rec_aio.get_event_loop().create_task(
                        tias_repo.update_outcome(
                            _tid,
                            pnl_usd=float(record.get("pnl_usd") or 0.0),
                            pnl_pct=float(record.get("pnl_pct") or 0.0),
                            win=bool(record.get("was_win")),
                        )
                    )
                    _t.add_done_callback(
                        _close_cb_done("tias_reconcile", record.get("symbol", ""))
                    )
                except Exception as e:
                    log.warning(
                        f"RECONCILE_CB_FAIL | cb=tias sym={record.get('symbol')} "
                        f"err='{str(e)[:150]}' | {ctx()}"
                    )
            coordinator.register_reconcile_callback(_tias_reconcile_callback)
            log.info("TIAS: outcome-reconcile callback registered (UPDATE by trade_id)")

            # Phase 4 — Backfill worker: retry failed DeepSeek analyses every 30 min.
            # Only launched when the analyzer is active (API key present + enabled).
            # Isolated in its own try/except so a backfill init failure never
            # disables the TIAS callback that was already registered above.
            if tias_analyzer is not None:
                try:
                    from src.tias.backfill import TIASBackfillWorker

                    _tias_backfill = TIASBackfillWorker(tias_repo, tias_analyzer, tias_cfg)

                    async def _tias_backfill_loop() -> None:
                        """Background loop: run TIAS backfill on startup then every 30 minutes.

                        Runs immediately on first iteration (60s warm-up delay so the system
                        fully initialises before making API calls), then every 30 minutes.
                        This ensures trades that closed while TIAS was disabled or the API
                        key was missing are analysed as soon as the system comes online.
                        """
                        first_run = True
                        while True:
                            try:
                                if first_run:
                                    await asyncio.sleep(60)  # Brief warm-up, then analyse
                                    first_run = False
                                else:
                                    await asyncio.sleep(1800)  # 30 minutes between cycles
                                await _tias_backfill.run_once()
                            except asyncio.CancelledError:
                                break
                            except Exception as _bf_err:
                                log.error(
                                    "TIAS_BACKFILL_LOOP_ERR | err='{err}'",
                                    err=str(_bf_err)[:100],
                                )
                                await asyncio.sleep(60)  # Brief pause, then continue

                    asyncio.get_event_loop().create_task(_tias_backfill_loop())
                    log.info("TIAS: backfill worker started (30-min interval)")
                except Exception as _bf_init_err:
                    log.warning(
                        "TIAS backfill worker failed to start (non-critical): {err}",
                        err=str(_bf_init_err),
                    )

        except Exception as e:
            log.warning("TIAS collector unavailable: {err}", err=str(e))

        # APEX — Trade Optimizer (DeepSeek via OpenRouter)
        # Optimizes Claude's directives before order execution.
        # Depends on: tias_repo (TIAS block above), db (DatabaseManager).
        try:
            from src.apex.qwen_client import QwenClient
            from src.apex.assembler import IntelligenceAssembler
            from src.apex.optimizer import TradeOptimizer

            apex_cfg = self.settings.apex
            if apex_cfg.enabled and apex_cfg.api_key:
                qwen_client = QwenClient(
                    api_key=apex_cfg.api_key,
                    api_url=apex_cfg.api_url,
                    http_referer=apex_cfg.http_referer,
                    x_title=apex_cfg.x_title,
                )
                apex_assembler = IntelligenceAssembler(self._services, tias_repo, db)
                apex_optimizer = TradeOptimizer(qwen_client, apex_assembler, apex_cfg)
                # J5 (2026-05-14) — wire a late-bound trading-capital
                # getter so the dynamic per-trade cap can read the
                # fund_manager's account state. The fund_manager is
                # constructed earlier in this method (around line 1781)
                # but its ``_account_state`` is initialised in
                # ``fund_mgr.initialize()`` which runs later (and may
                # re-initialise on transformer-switch). The getter is
                # therefore closed over ``self._services`` and reads
                # at call time rather than at construction — that way
                # the optimizer always sees the current state, never a
                # stale snapshot. When fund_manager is absent or its
                # state is missing, the getter returns None and the
                # optimizer falls back to the static dollar cap.
                def _trading_capital_getter() -> float | None:
                    _fm = self._services.get("fund_manager")
                    if _fm is None:
                        return None
                    _state = getattr(_fm, "_account_state", None)
                    if _state is None:
                        return None
                    try:
                        return float(getattr(_state, "trading_capital", 0.0) or 0.0)
                    except Exception:
                        return None
                if hasattr(apex_optimizer, "attach_account_state_getter"):
                    apex_optimizer.attach_account_state_getter(
                        _trading_capital_getter,
                    )
                self._services["apex_optimizer"] = apex_optimizer

                # TradeGate: safety limits between optimizer and execution
                from src.apex.gate import TradeGate
                apex_gate = TradeGate(self._services, apex_cfg)
                self._services["apex_gate"] = apex_gate

                log.info(
                    "APEX: TradeOptimizer ENABLED + TradeGate wired | model={model}",
                    model=apex_cfg.model,
                )
                # APEX Direction-Flip Switch boot sentinel
                # (IMPLEMENT_APEX_FLIP_SWITCH, 2026-05-25). Operator queries
                # this single line to confirm whether APEX may REVERSE the
                # brain's direction this session. When off, APEX still
                # optimizes (SL/TP/size/leverage); only the reversal is gated.
                log.info(
                    "APEX_FLIP_SWITCH_SENTINEL | apex_dir_flip_enabled={enabled} "
                    "gates=direction_reversal_only optimization=preserved | no_ctx",
                    enabled=bool(getattr(apex_cfg, "apex_dir_flip_enabled", False)),
                )
                # Issue 2.3 (2026-06-07): leverage-override kill-switch state.
                log.info(
                    "APEX_LEVERAGE_OVERRIDE_SENTINEL | apex_leverage_override_enabled={enabled} "
                    "gates=leverage_only honors_brain_when_off=Y optimization=preserved | no_ctx",
                    enabled=bool(getattr(apex_cfg, "apex_leverage_override_enabled", False)),
                )

                # Phase 3 session-stability (brief verification): APEX has
                # no in-memory cache to hydrate — the assembler queries
                # ``trade_intelligence`` on every optimization (see
                # assembler.py:_gather_symbol_history). The brief's
                # self-verification expects visibility into the state
                # APEX will "see" on its first call, so emit a one-shot
                # snapshot of the durable store during startup: total
                # rows, distinct symbols, distinct regime buckets, per-
                # regime breakdown. ``_create_workers`` is sync but
                # runs inside the running event loop (called from the
                # ``await initialize()`` path), so schedule the stats
                # query as a background task — failure is isolated and
                # the log appears within a few seconds of startup.
                try:
                    import asyncio as _asyncio
                    _loop = _asyncio.get_event_loop()
                    _loop.create_task(
                        self._log_apex_startup_stats(db, apex_cfg)
                    )
                except Exception as _e:
                    log.warning(
                        f"APEX_STARTUP_STATS_SCHEDULE_FAIL | err='{str(_e)[:120]}'"
                    )
            else:
                reason = "disabled in config" if not apex_cfg.enabled else "no API key"
                log.info("APEX: TradeOptimizer DISABLED ({reason})", reason=reason)
        except Exception as e:
            log.warning("APEX optimizer unavailable: {err}", err=str(e))

        # ── SENTINEL — Exit Firewall + Deadline Engine + Portfolio Advisor ──
        try:
            sentinel_cfg = self.settings.sentinel
            if sentinel_cfg.enabled:
                # Part 2: Deadline Engine — injected into Watchdog
                from src.sentinel.deadline import DeadlineEngine
                sentinel_deadline = DeadlineEngine(sentinel_cfg)
                watchdog = self._services.get("position_watchdog")
                if watchdog:
                    watchdog._sentinel_deadline = sentinel_deadline
                    log.info("SENTINEL: Deadline Engine wired to Watchdog")

                # Part 3: Portfolio Advisor — background loop
                if sentinel_cfg.advisor_enabled and sentinel_cfg.advisor_api_key:
                    from src.sentinel.advisor import PortfolioAdvisor
                    from src.tias.deepseek_client import DeepSeekClient as _SentinelDS

                    sentinel_client = _SentinelDS(
                        api_key=sentinel_cfg.advisor_api_key,
                        api_url="https://openrouter.ai/api/v1/chat/completions",
                        http_referer="https://github.com/trading-intelligence-mcp",
                        x_title="SENTINEL-PortfolioAdvisor",
                    )
                    sentinel_advisor = PortfolioAdvisor(sentinel_client, sentinel_cfg)
                    self._services["sentinel_advisor"] = sentinel_advisor

                    if watchdog:
                        watchdog._sentinel_advisor = sentinel_advisor

                    # Background loop: assess portfolio every N seconds
                    position_service = self._services.get("position_service")

                    async def _sentinel_advisor_loop() -> None:
                        """SENTINEL: Portfolio risk assessment loop."""
                        await asyncio.sleep(150)  # 2.5 min offset from Claude's cycle
                        while True:
                            try:
                                context_lines = []
                                if position_service:
                                    positions = await position_service.get_positions()
                                    for p in positions:
                                        pnl = 0.0
                                        if p.entry_price and p.entry_price > 0:
                                            pnl = ((p.mark_price - p.entry_price) / p.entry_price) * 100
                                            side_val = p.side.value if hasattr(p.side, "value") else str(p.side)
                                            if side_val in ("Sell", "Short"):
                                                pnl = -pnl
                                        from src.core.utils import format_price
                                        sl_str = f"${format_price(p.stop_loss)}" if p.stop_loss else "NONE"
                                        context_lines.append(
                                            f"{p.symbol}: {p.side.value if hasattr(p.side, 'value') else p.side} "
                                            f"entry=${format_price(p.entry_price)} mark=${format_price(p.mark_price)} "
                                            f"PnL={pnl:+.2f}% SL={sl_str} size={p.size}"
                                        )

                                if context_lines:
                                    portfolio_text = (
                                        f"Open positions ({len(context_lines)}):\n"
                                        + "\n".join(context_lines)
                                    )
                                    await sentinel_advisor.assess(portfolio_text)

                            except asyncio.CancelledError:
                                break
                            except Exception as e:
                                log.error(f"SENTINEL_ADVISOR_LOOP_ERR | err='{str(e)[:100]}'")

                            await asyncio.sleep(sentinel_cfg.advisor_interval_seconds)

                    asyncio.get_event_loop().create_task(_sentinel_advisor_loop())
                    log.info(
                        "SENTINEL: Portfolio Advisor ENABLED | model={model} interval={i}s",
                        model=sentinel_cfg.advisor_model,
                        i=sentinel_cfg.advisor_interval_seconds,
                    )
                else:
                    reason = "disabled" if not sentinel_cfg.advisor_enabled else "no API key"
                    log.info("SENTINEL: Portfolio Advisor DISABLED ({reason})", reason=reason)

                log.info(
                    "SENTINEL: system ENABLED (firewall={fw}, deadline=ON, advisor={adv})",
                    fw=sentinel_cfg.firewall_enabled,
                    adv=sentinel_cfg.advisor_enabled and bool(sentinel_cfg.advisor_api_key),
                )
            else:
                log.info("SENTINEL: system DISABLED in config")
        except Exception as e:
            log.warning("SENTINEL init failed (non-critical): {err}", err=str(e))

        cb_count = len(coordinator._callbacks_on_close)
        log.info("TradeCoordinator: {n} close callbacks registered", n=cb_count)

        # Register exchange switch callbacks on Transformer
        transformer = self._services.get("transformer")
        if transformer and hasattr(transformer, "register_switch_callback"):
            pnl_mgr = self._services.get("pnl_manager")
            if pnl_mgr:
                transformer.register_switch_callback(
                    lambda old, new: pnl_mgr.on_exchange_switch()
                )
                log.info("Transformer: PnL manager switch callback registered")

    @staticmethod
    async def _update_strategy_performance(db: DatabaseManager, record: dict) -> None:
        """Update strategy_performance table when a trade closes."""
        strategy_name = record.get("strategy_name", "")
        if not strategy_name or strategy_name in ("", "unknown"):
            return

        pnl_pct = record.get("pnl_pct", 0.0)
        was_win = record.get("was_win", False)
        symbol = record.get("symbol", "all")
        closed_at = record.get("closed_at", "")

        try:
            existing = await db.fetch_one(
                "SELECT * FROM strategy_performance WHERE strategy = ? AND symbol = ? AND timeframe = 'all'",
                (strategy_name, symbol),
            )

            if existing:
                total = existing["total_trades"] + 1
                wins = existing["winning_trades"] + (1 if was_win else 0)
                losses = existing["losing_trades"] + (0 if was_win else 1)
                wr = wins / total if total > 0 else 0.0
                total_pnl = existing["avg_pnl_pct"] * existing["total_trades"] + pnl_pct
                avg_pnl = total_pnl / total if total > 0 else 0.0

                await db.execute(
                    """UPDATE strategy_performance SET
                       total_trades = ?, winning_trades = ?, losing_trades = ?,
                       win_rate = ?, avg_pnl_pct = ?, updated_at = ?
                       WHERE strategy = ? AND symbol = ? AND timeframe = 'all'""",
                    (total, wins, losses, round(wr, 4), round(avg_pnl, 4),
                     closed_at or "now", strategy_name, symbol),
                )
            else:
                await db.execute(
                    """INSERT INTO strategy_performance
                       (strategy, symbol, timeframe, total_trades, winning_trades,
                        losing_trades, win_rate, avg_pnl_pct, updated_at)
                       VALUES (?, ?, 'all', 1, ?, ?, ?, ?, ?)""",
                    (strategy_name, symbol,
                     1 if was_win else 0, 0 if was_win else 1,
                     1.0 if was_win else 0.0, round(pnl_pct, 4),
                     closed_at or "now"),
                )

            result_str = "WIN" if was_win else "LOSS"
            log.info(
                "Strategy perf updated: {s} on {sym} -> {result} {pnl:+.2f}%",
                s=strategy_name, sym=symbol, result=result_str, pnl=pnl_pct,
            )
        except Exception as e:
            log.error("Failed to update strategy_performance: {err}", err=str(e))

    async def start_all(self) -> None:
        """Start all workers concurrently.

        Sets up signal handlers for graceful shutdown and runs all workers
        as asyncio tasks. One worker crash does not stop others.
        """
        # Signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal_mod.SIGTERM, signal_mod.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._handle_shutdown(s)))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        log.info(f"WM_INIT | workers={len(self.workers)} | {ctx()}")
        log.info("Starting {n} workers...", n=len(self.workers))

        # Layer 1 restructure Phase 1 — start the CycleTracker hourly
        # flush. The tracker itself was created in initialize(); the
        # background flush task starts only once the event loop is up.
        cycle_tracker = self._services.get("cycle_tracker")
        if cycle_tracker:
            try:
                await cycle_tracker.start_hourly_flush_task(
                    flush_seconds=self.settings.observability.cycle_metrics_flush_seconds,
                )
            except Exception as e:
                log.warning(
                    f"CYCLE_TRACKER_FLUSH_START_FAIL | err='{str(e)[:120]}' | {ctx()}"
                )

        self.tasks = [
            asyncio.create_task(self._run_worker(w), name=w.name)
            for w in self.workers
        ]
        # Phase 11: event-loop health probe runs alongside workers. Not a
        # BaseWorker subclass — it must remain reactive even when every
        # worker is stuck, so it has its own dedicated task with minimal
        # dependencies.
        self.tasks.append(
            asyncio.create_task(self._system_health_loop(), name="system_health")
        )
        log.info(
            f"SYSTEM_HEALTH_START | interval={_SYSTEM_HEALTH_INTERVAL_SECONDS:.0f}s | {ctx()}"
        )

        # System 2 (observability): per-second open-trade price path logger. A
        # standalone background task (like _system_health_loop, not a
        # BaseWorker) that samples each open trade's already-in-memory WS price
        # ~once per second and flushes to the dedicated rotated price_path.log.
        # Zero new exchange API calls; sits entirely beside the trade path.
        try:
            _obs = self.settings.observability
            if getattr(_obs, "price_path_logging_enabled", False):
                _pw = self._services.get("price_worker")
                _tc = self._services.get("trade_coordinator")
                if _pw is not None and _tc is not None:
                    from src.workers.price_path_logger import PricePathLogger
                    _ppl = PricePathLogger(_pw, _tc, _obs)
                    self._services["price_path_logger"] = _ppl
                    _tc.register_close_callback(_ppl.on_trade_closed)
                    self.tasks.append(
                        asyncio.create_task(_ppl.run(), name="price_path_logger")
                    )
                    log.info(
                        f"PRICE_PATH_LOGGER_WIRED | enabled=True "
                        f"resolution_s={_obs.price_path_resolution_seconds} "
                        f"flush_s={_obs.price_path_flush_seconds} "
                        f"ws_max_age_s={_obs.price_path_ws_max_age_seconds} "
                        f"file={_obs.price_path_filename} | {ctx()}"
                    )
                else:
                    log.warning(
                        f"PRICE_PATH_LOGGER_SKIP | reason=missing_dep "
                        f"price_worker={_pw is not None} "
                        f"trade_coordinator={_tc is not None} | {ctx()}"
                    )
            else:
                log.info(f"PRICE_PATH_LOGGER_DISABLED | enabled=False | {ctx()}")
        except Exception as _ppe:
            log.warning(
                f"PRICE_PATH_LOGGER_WIRE_FAIL | err='{str(_ppe)[:150]}' | {ctx()}"
            )

        # Auto-start 3-layer architecture (respects persisted user_stopped flag)
        layer_manager = self._services.get("layer_manager")
        if layer_manager:
            try:
                if layer_manager.user_stopped:
                    # User explicitly stopped trading before last restart — only start Data
                    await layer_manager.start_layer(1)
                    log.warning(
                        "User previously stopped trading — only Data layer auto-started. "
                        "Use /control → Start Trading to resume."
                    )
                else:
                    await layer_manager.start_layer(1)  # Data
                    await asyncio.sleep(2)
                    await layer_manager.start_layer(2)  # Brain (needs Data)
                    await asyncio.sleep(2)
                    await layer_manager.start_layer(3)  # Execution (needs Brain)
                    log.info("All 3 layers started: DATA -> BRAIN -> EXECUTION")
            except Exception as e:
                log.error("Layer startup failed: {err}", err=str(e))

        # Wait for shutdown or all tasks to complete
        done, pending = await asyncio.wait(
            self.tasks + [asyncio.create_task(self._shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If shutdown event triggered, stop everything
        if self._shutdown_event.is_set():
            await self.stop_all()

    async def _run_worker(self, worker: BaseWorker) -> None:
        """Run a single worker with crash isolation."""
        # Phase 11 (dead-workers fix): register the worker with the
        # liveness tracker BEFORE the WM_START log so wm_start_ts and
        # the visible WM_START line agree on when the worker began.
        # The tier_tag exposes "LAYER1A" / ... for tier-tagged workers
        # and None for utility workers — drives /health rendering.
        try:
            self._worker_liveness.register(
                worker.name,
                expected_interval_s=float(worker.interval),
                cycle_gated=bool(getattr(worker, "cycle_gated", False)),
                tier=worker.layer_tier_tag,
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(
                "WORKER_LIVENESS_REGISTER_FAIL | name={n} err='{err}' | "
                "worker_continues_without_liveness_tracking",
                n=worker.name, err=str(e)[:80],
            )
        # Phase 14 Gap J1 (output-quality obs): include per-instance wid
        # so two restarts of the same worker are distinguishable in logs.
        _wid = getattr(worker, "wid", "?")
        _hid = f"hid=h-{worker.name}"
        log.info(
            f"WM_START | worker={worker.name} wid={_wid} "
            f"interval={worker.interval}s | {_hid}"
        )
        try:
            await worker.start()
            log.info(f"WM_STOP | worker={worker.name} wid={_wid} | {_hid}")
        except Exception as e:
            log.critical(
                f"WM_CRASH | worker={worker.name} wid={_wid} "
                f"err='{str(e)[:150]}' | {_hid}"
            )
            log.error(
                "Worker '{name}' terminated: {err}",
                name=worker.name, err=str(e),
            )

    async def _system_health_loop(self) -> None:
        """Phase 11: periodic SYSTEM_HEALTH emission until shutdown.

        Contract:
         * One check() per interval; exceptions inside check() are swallowed
           so the loop survives a malfunctioning probe.
         * Sleep is implemented as `wait_for(self._shutdown_event.wait(),
           timeout=interval)` rather than `asyncio.sleep(interval)` so the
           loop exits within milliseconds of shutdown, not after the full
           60-second interval.
         * SYSTEM_HEALTH_STOP is emitted exactly once from `finally` —
           guaranteed even under CancelledError (BaseException in py3.8+,
           not caught by `except Exception`). The exit reason is derived
           from whether the shutdown event was set before the exit.
        """
        _exit_reason = "unexpected_exit"
        try:
            while not self._shutdown_event.is_set():
                try:
                    await self._system_health.check()
                except Exception as e:
                    log.error(
                        f"SYSTEM_HEALTH_ERR | err='{str(e)[:150]}' | {ctx()}"
                    )
                # Sleep-or-shutdown: exits the wait immediately if the event
                # is set, so shutdown is responsive.
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=_SYSTEM_HEALTH_INTERVAL_SECONDS,
                    )
                    # wait returned → shutdown flagged → next while-check exits
                except asyncio.TimeoutError:
                    # wait timed out → loop period elapsed, run next check
                    pass
            _exit_reason = "shutdown"
        except asyncio.CancelledError:
            _exit_reason = "cancelled"
            raise
        finally:
            log.info(f"SYSTEM_HEALTH_STOP | reason={_exit_reason} | {ctx()}")

    async def stop_all(self) -> None:
        """Stop all workers gracefully."""
        log.info("Stopping all workers...")

        for w in self.workers:
            w.running = False

        # Give workers time to finish current tick
        for w in self.workers:
            try:
                await asyncio.wait_for(w.stop(), timeout=10.0)
            except (asyncio.TimeoutError, Exception) as e:
                log.warning("Worker '{name}' stop timed out: {err}", name=w.name, err=str(e))

        # Cancel remaining tasks
        for t in self.tasks:
            if not t.done():
                t.cancel()

        # Phase 2 (post-Layer-1 fix): cancel the LayerManager state-sync
        # heartbeat before tearing down services. Without this, the
        # asyncio task created by ``layer_manager.start_state_sync()``
        # would survive to event-loop teardown and emit a "Task was
        # destroyed but it is pending" warning. ``stop_state_sync`` is
        # idempotent + defensive: returns immediately if the task was
        # never started or has already completed.
        layer_manager = self._services.get("layer_manager")
        if layer_manager is not None and hasattr(layer_manager, "stop_state_sync"):
            try:
                await layer_manager.stop_state_sync()
            except Exception as e:
                log.debug("layer_manager stop_state_sync failed: {err}", err=str(e))

        # Close services
        bybit = self._services.get("bybit")
        if bybit and hasattr(bybit, "disconnect"):
            try:
                await bybit.disconnect()
            except Exception as e:
                log.debug("bybit disconnect failed: {err}", err=str(e))

        ws = self._services.get("ws")
        if ws and hasattr(ws, "disconnect"):
            try:
                await ws.disconnect()
            except Exception as e:
                log.debug("websocket disconnect failed: {err}", err=str(e))

        # Close the X-RAY shadow-db reader's persistent read-only connection
        # before disposing of the main DatabaseManager. The reader is a
        # SHARED service (singleton in self._services), not owned by any
        # one worker, so it is cleaned up here rather than in
        # BaseWorker.cleanup().
        shadow_reader = self._services.get("shadow_kline_reader")
        if shadow_reader is not None and hasattr(shadow_reader, "close"):
            try:
                await shadow_reader.close()
            except Exception as e:
                log.debug("shadow_kline_reader close failed: {err}", err=str(e))

        # T2-1 (2026-05-12): dispose pre-spawned Claude CLI workers.
        # Without this, the pool's primed subprocesses survive teardown
        # as orphan claude.*-p processes consuming sockets/credentials.
        # The shutdown helper is idempotent + best-effort so it never
        # blocks the rest of the teardown chain.
        claude_client_for_shutdown = self._services.get("claude_client")
        if claude_client_for_shutdown is not None and hasattr(
            claude_client_for_shutdown, "shutdown"
        ):
            try:
                claude_client_for_shutdown.shutdown()
            except Exception as e:
                log.debug(
                    "claude_client shutdown failed: {err}", err=str(e)
                )

        await self.db.disconnect()
        log.info("All workers stopped")

    async def _handle_shutdown(self, sig) -> None:
        """Handle shutdown signal."""
        log.info("Received signal {sig}, initiating graceful shutdown...", sig=sig)
        self._shutdown_event.set()
        # Orphan-prevention (2026-06-17): flag shutdown in the shared services
        # dict BEFORE the DB is torn down, so the trade-open path stops placing
        # NEW orders. Without this, a restart can close the DB mid-open and the
        # post-order save_thesis fails, orphaning the trade (live on the
        # exchange, no local record -> its close never books PnL). Open
        # positions keep full management; only new entries are blocked.
        try:
            self._services["shutting_down"] = True
            log.info("SHUTDOWN_FLAG_SET | new trade opens are now blocked | {c}", c=ctx())
        except Exception:
            pass

    def get_health(self) -> dict:
        """Get system health report."""
        return self.health.get_system_health()
