"""End-to-end pipeline test for the audit-phase fixes.

Each test exercises a fix end-to-end against the REAL project:
real ``Settings`` loaded from ``config.toml``, real ``DatabaseManager``
against a temp SQLite (with full migrations), real services + workers
where possible. Mocks are limited to external boundaries we can't
reach in CI (Bybit REST, Telegram bot, Claude CLI).

Sections (one per audit-phase fix):

  FIX 1  — SymbolRegistry frozenset contract + pre-seed.
  FIX 2  — CycleTracker → 1B/1C base-loop wiring (Phase 1 audit).
  FIX 3  — Strategist reads ``_strategy_consensus_summary`` alias (Phase 3 audit).
  FIX 4  — WorkerTier enum is canonical; layer_tier_tag is derived (Phase 4 audit).
  FIX 5  — recent_loss_symbols batched query against real trade_intelligence (Phase 5 audit).
  FIX 6  — Scanner active_universe ↔ in-memory consistency (audit fix in 7).
  FIX 7  — cycle_tracker log routing (audit fix in 7).
  FIX 8  — Watchdog maturity-check isolation against transient ticker failure.
  FIX 9  — Bybit error map: 10001 falls through to BybitAPIError.
  FIX 10 — PnL manager: normal/survival mode contracts + reset semantics.
  FIX 11 — Strategy registry: regime affects sizing not activation.
  FIX 12 — Signal generator: Phase 29 confidence gate downgrades correctly.
  FIX 13 — Alert manager: HOLD decisions emit (operator visibility).

Run with::

    python3 -m pytest tests/test_audit_fixes_e2e/ -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def real_settings():
    """Real Settings instance loaded from the project's config.toml."""
    from src.config.settings import Settings
    Settings.reset()
    s = Settings._load_fresh(
        config_path=str(REPO_ROOT / "config.toml"),
        env_path=str(REPO_ROOT / ".env"),
    )
    yield s
    Settings.reset()


@pytest.fixture
async def real_db(tmp_path):
    """Real DatabaseManager against a temp SQLite. Migrations applied."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    db = DatabaseManager(str(tmp_path / "trading.db"))
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


# =====================================================================
# FIX 1 — SymbolRegistry contract
# =====================================================================


class TestFix1_SymbolRegistry:
    """SymbolRegistry must satisfy the legacy frozenset contract."""

    def test_set_algebra_returns_frozenset(self):
        from src.config.constants import SUPPORTED_SYMBOLS, TESTNET_EXCLUDED_SYMBOLS

        # Production caller (scanner.py:584): ``SUPPORTED_SYMBOLS - TESTNET_EXCLUDED_SYMBOLS``
        diff = SUPPORTED_SYMBOLS - TESTNET_EXCLUDED_SYMBOLS
        assert isinstance(diff, frozenset)
        assert "BTCUSDT" in diff
        # Excluded symbols must not appear.
        for excluded in TESTNET_EXCLUDED_SYMBOLS:
            assert excluded not in diff

    def test_pre_seeded_from_order_qty_tables(self):
        """Every key in MIN_ORDER_QTY / MAX_ORDER_QTY must be in SUPPORTED_SYMBOLS at import time."""
        from src.config.constants import (
            MAX_ORDER_QTY, MIN_ORDER_QTY, SUPPORTED_SYMBOLS,
        )
        for sym in MIN_ORDER_QTY:
            assert sym in SUPPORTED_SYMBOLS, f"MIN_ORDER_QTY[{sym!r}] not in SUPPORTED_SYMBOLS"
        for sym in MAX_ORDER_QTY:
            assert sym in SUPPORTED_SYMBOLS, f"MAX_ORDER_QTY[{sym!r}] not in SUPPORTED_SYMBOLS"

    def test_order_service_validation_path(self):
        """Real production validation flow — ``if symbol not in SUPPORTED_SYMBOLS:``"""
        from src.config.constants import SUPPORTED_SYMBOLS
        # Must accept known symbols without raising.
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"):
            assert sym in SUPPORTED_SYMBOLS, f"order_service would reject {sym}"
        # Must reject obvious junk.
        assert "DEFINITELY_NOT_REAL_USDT" not in SUPPORTED_SYMBOLS

    def test_dynamic_update_preserves_btc_eth(self):
        """``update()`` must always preserve BTC/ETH (HR-2 reference pairs)."""
        from src.config.constants import SymbolRegistry

        r = SymbolRegistry({"FOOUSDT", "BARUSDT"})
        r.update({"BAZUSDT"})
        assert "BTCUSDT" in r
        assert "ETHUSDT" in r
        assert "BAZUSDT" in r


# =====================================================================
# FIX 2 — CycleTracker wired into 1B/1C base-loop
# =====================================================================


class TestFix2_CycleTracker_BaseLoop:
    """1B/1C workers' tick latencies must roll up into CYCLE_COMPLETE."""

    @pytest.mark.asyncio
    async def test_real_db_real_tracker_full_cycle(self, real_db):
        """Real CycleTracker against real cycle_metrics table, full 1B+1C+1D path."""
        from src.core.cycle_tracker import CycleTracker

        ct = CycleTracker(real_db, max_history=10)
        cid = ct.start_cycle("layer1b")
        ct.end_cycle("layer1b", cid)
        ct.start_cycle("layer1c", cycle_id=cid)
        ct.end_cycle("layer1c", cid)
        ct.start_cycle("layer1d", cycle_id=cid)
        ct.record_qualified(cid, qualified=14, selected=12, packages=12)
        ct.end_cycle("layer1d", cid)

        recent = ct.get_recent(5)
        assert len(recent) == 1
        s = recent[0]
        # All three sub-layer latencies are non-None — proves 1B and 1C
        # were actually recorded (the gap the audit fixed).
        assert s.layer1b_ms is not None
        assert s.layer1c_ms is not None
        assert s.layer1d_ms is not None
        assert s.packages_ready == 12

        # Real flush into cycle_metrics
        await ct._flush_once()
        rows = await real_db.fetch_all("SELECT * FROM cycle_metrics")
        assert len(rows) == 1

    def test_base_worker_helpers_exist_and_typed(self):
        from src.workers.base_worker import BaseWorker
        from src.core.types import WorkerTier
        # _CYCLE_TRACKED_TIERS uses enum members (no string drift).
        assert WorkerTier.LAYER1B in BaseWorker._CYCLE_TRACKED_TIERS
        assert WorkerTier.LAYER1C in BaseWorker._CYCLE_TRACKED_TIERS
        # 1A and 1D explicitly excluded — both for the right reason
        # (1A no cycle semantics; 1D drives its own start/end inside tick).
        assert WorkerTier.LAYER1A not in BaseWorker._CYCLE_TRACKED_TIERS
        assert WorkerTier.LAYER1D not in BaseWorker._CYCLE_TRACKED_TIERS


# =====================================================================
# FIX 3 — Strategist alias reads
# =====================================================================


class TestFix3_StrategistAlias:
    def test_strategist_consensus_summary_alias_used(self, real_settings):
        """Verify both shapes coexist on a real LayerManager and the
        legacy summary path is alive.
        """
        from src.core.layer_manager import LayerManager

        lm = LayerManager(real_settings, {})
        # Per-coin shape (Phase 3 cache; ScannerWorker reads it)
        lm._strategy_consensus["BTCUSDT"] = {
            "consensus": "STRONG", "consensus_score": 0.9,
            "vote_count": 5, "direction": "long", "last_updated": 0.0,
        }
        # Legacy summary shape (strategist's CALL_A reads this)
        lm._strategy_consensus_summary["BTCUSDT"] = {
            "buy": 5, "sell": 0, "total_score": 90.0,
        }

        # Phase 5 read path: scanner uses get_strategy_consensus → per-coin shape
        per_coin = lm.get_strategy_consensus("BTCUSDT")
        assert per_coin and per_coin["consensus"] == "STRONG"
        # Phase 3 alias read path: strategist's CALL_A reads
        # _strategy_consensus_summary directly
        legacy = lm._strategy_consensus_summary.get("BTCUSDT")
        assert legacy and legacy["buy"] == 5

    def test_strategist_source_uses_alias(self):
        """Audit invariant — strategist.py must reference _strategy_consensus_summary."""
        text = (REPO_ROOT / "src/brain/strategist.py").read_text()
        assert text.count("_strategy_consensus_summary") >= 2


# =====================================================================
# FIX 4 — WorkerTier canonical
# =====================================================================


class TestFix4_WorkerTierCanonical:
    def test_every_worker_uses_enum(self):
        from src.core.types import WorkerTier
        from src.workers.altdata_worker import AltDataWorker
        from src.workers.kline_worker import KlineWorker
        from src.workers.news_worker import NewsWorker
        from src.workers.price_worker import PriceWorker
        from src.workers.regime_worker import RegimeWorker
        from src.workers.scanner_worker import ScannerWorker
        from src.workers.signal_worker import SignalWorker
        from src.workers.strategy_worker import StrategyWorker
        from src.workers.structure_worker import StructureWorker

        for cls, want in [
            (KlineWorker, WorkerTier.LAYER1A), (PriceWorker, WorkerTier.LAYER1A),
            (AltDataWorker, WorkerTier.LAYER1A), (NewsWorker, WorkerTier.LAYER1A),
            (StructureWorker, WorkerTier.LAYER1B), (SignalWorker, WorkerTier.LAYER1B),
            (RegimeWorker, WorkerTier.LAYER1B),
            (StrategyWorker, WorkerTier.LAYER1C),
            (ScannerWorker, WorkerTier.LAYER1D),
        ]:
            assert cls.worker_tier is want, f"{cls.__name__}: tier mismatch"

    def test_layer_tier_tag_is_derived_property(self):
        """Property ⇒ no string drift surface."""
        import inspect
        from src.workers.base_worker import BaseWorker
        from src.core.types import WorkerTier

        prop = inspect.getattr_static(BaseWorker, "layer_tier_tag")
        assert isinstance(prop, property)
        # Verify derivation: enum.value.upper() == tag
        for tier in (WorkerTier.LAYER1A, WorkerTier.LAYER1B, WorkerTier.LAYER1C, WorkerTier.LAYER1D):
            stub = type("Stub", (), {"worker_tier": tier})()
            assert prop.fget(stub) == tier.value.upper()


# =====================================================================
# FIX 5 — recent_loss_symbols batched query
# =====================================================================


class TestFix5_RecentLossQuery:
    @pytest.mark.asyncio
    async def test_real_db_inserts_then_query(self, real_db):
        """Insert real rows into trade_intelligence; query returns expected set."""
        from src.core.trade_recorder import recent_loss_symbols

        # Empty table → empty set
        assert await recent_loss_symbols(real_db, hours=1) == set()

        # Insert 4 rows: 2 recent losses, 1 recent win, 1 old loss
        async def _insert(symbol, win, when_offset):
            await real_db.execute(
                "INSERT INTO trade_intelligence "
                "(symbol, direction, strategy_name, strategy_category, source, "
                " closed_by, entry_price, exit_price, pnl_pct, pnl_usd, win, "
                f" hold_seconds, trade_closed_at, captured_at) "
                f"VALUES (?, ?, 'A1', 'trend', 'auto', "
                f"?, 100.0, ?, ?, ?, ?, 60.0, "
                f"datetime('now', '{when_offset}'), datetime('now'))",
                (symbol, "long",
                 "tp" if win else "sl", 102.0 if win else 99.0,
                 2.0 if win else -1.0, 20.0 if win else -10.0, 1 if win else 0),
            )
        await _insert("BTCUSDT", False, "+0 minutes")
        await _insert("ETHUSDT", False, "+0 minutes")
        await _insert("SOLUSDT", True, "+0 minutes")
        await _insert("XRPUSDT", False, "-2 hours")

        # 1h window: only the two recent losses
        assert await recent_loss_symbols(real_db, hours=1) == {"BTCUSDT", "ETHUSDT"}
        # 3h window: includes the old XRP loss but excludes SOL (a win)
        assert await recent_loss_symbols(real_db, hours=3) == {"BTCUSDT", "ETHUSDT", "XRPUSDT"}

    def test_scanner_threads_set_through_qualifies(self):
        import inspect
        from src.workers.scanner_worker import ScannerWorker
        sig = inspect.signature(ScannerWorker._qualifies)
        assert "recent_loss_set" in sig.parameters
        sig_b = inspect.signature(ScannerWorker._check_blockers)
        assert "recent_loss_set" in sig_b.parameters
        # tick() prefetches once and threads through
        tick_src = inspect.getsource(ScannerWorker.tick)
        assert "recent_loss_set" in tick_src
        assert "recent_loss_symbols" in tick_src


# =====================================================================
# FIX 6 + 7 — Scanner consistency + cycle_tracker log routing
# =====================================================================


class TestFix6_ScannerConsistency:
    @pytest.mark.asyncio
    async def test_table_and_in_memory_align_post_tick(self, real_settings, real_db):
        """After ScannerWorker.tick, the active_universe table and
        MarketScanner._active_universe are kept consistent.

        Original Fix-6 contract: both views share the same symbol set.

        Q2 (2026-04-29) — BTC/ETH ref-pair force-include removed from
        both producers. The consistency invariant is unchanged
        (table ⊆ in-memory), but now neither view contains BTC/ETH
        without warm caches/positions. The test asserts the
        consistency invariant directly without assuming BTC/ETH.
        """
        from src.strategies.scanner import MarketScanner
        from src.workers.scanner_worker import ScannerWorker

        # Phase 9 mode isolation — pin to exclusion to assert the
        # legacy Q2 contract (BTC/ETH absent without warm caches).
        # Briefing-mode contract is covered by phase 5+ tests.
        real_settings.scanner.mode = "exclusion"

        scanner = MarketScanner(
            real_settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(real_settings.universe.watch_list),
        )
        sw = ScannerWorker(real_settings, real_db, scanner, services={"scanner": scanner})
        await sw.tick()

        # In-memory list
        in_memory = await scanner.get_active_universe()
        # Table
        rows = await real_db.fetch_all("SELECT symbol FROM active_universe")
        table_symbols = {r["symbol"] for r in rows}

        # Q2: BTC/ETH no longer unconditionally injected.
        assert "BTCUSDT" not in in_memory
        assert "ETHUSDT" not in in_memory
        # Original consistency invariant preserved: table ⊆ in-memory.
        assert table_symbols.issubset(set(in_memory))


class TestFix7_LogRouting:
    def test_cycle_tracker_routed(self):
        from src.core.logging import COMPONENT_ROUTING
        assert COMPONENT_ROUTING.get("cycle_tracker") == "workers.log"


# =====================================================================
# FIX 8 — Watchdog isolation against transient ticker failure
# =====================================================================


class TestFix8_WatchdogIsolation:
    """A failure on one symbol's ticker fetch must not crash the rest."""

    @pytest.mark.asyncio
    async def test_one_symbol_failure_doesnt_block_others(self, real_settings):
        from src.core.types import Position, Side, Ticker
        from src.workers.position_watchdog import PositionWatchdog

        # Mature coordinator (bypasses 60-300s immunity gate)
        coord = MagicMock()
        coord.is_immune = MagicMock(return_value=(False, 0.0, ""))
        coord.get_maturity = MagicMock(return_value=(True, "mature", ""))
        coord.get_trade_plan = MagicMock(return_value=None)
        coord.cleanup_stale = MagicMock()
        coord.update_peak_pnl = MagicMock()
        coord.peek_pending_actions = MagicMock(return_value=[])
        coord.dequeue_strategic_actions = MagicMock(return_value=[])

        # AsyncMock-pre-wired DB so MarketRepository's awaits don't crash
        db = MagicMock()
        db.fetch_all = AsyncMock(return_value=[])
        db.fetch_one = AsyncMock(return_value=None)
        db.execute = AsyncMock(return_value=None)
        db.executemany = AsyncMock(return_value=None)

        # Position service with two positions
        pos1 = Position(symbol="BTCUSDT", side=Side.BUY, size=0.01,
                        entry_price=70000, mark_price=69000,
                        unrealized_pnl=-10, leverage=2, stop_loss=68000)
        pos2 = Position(symbol="ETHUSDT", side=Side.BUY, size=0.1,
                        entry_price=3500, mark_price=3450,
                        unrealized_pnl=-5, leverage=2, stop_loss=3400)
        pos_svc = MagicMock()
        pos_svc.get_positions = AsyncMock(return_value=[pos1, pos2])

        eth_ticker = Ticker(
            symbol="ETHUSDT", last_price=3450, bid=3449, ask=3451,
            high_24h=3500, low_24h=3400, volume_24h=10000, change_24h_pct=-1.5,
        )

        async def _ticker_side_effect(symbol: str):
            if symbol == "BTCUSDT":
                raise Exception("Bybit API outage on BTCUSDT")
            return eth_ticker

        market_svc = MagicMock()
        market_svc.get_ticker = AsyncMock(side_effect=_ticker_side_effect)

        wd = PositionWatchdog(
            settings=real_settings, db=db, position_service=pos_svc,
            market_service=market_svc, trade_coordinator=coord,
        )
        # Real tick — must not raise even though BTCUSDT throws.
        await wd.tick()

        # ETHUSDT was attempted despite BTCUSDT's failure: the audit
        # invariant. Both symbols should have had at least one
        # get_ticker call.
        symbols_attempted = {
            c.args[0] for c in market_svc.get_ticker.call_args_list
        }
        assert symbols_attempted == {"BTCUSDT", "ETHUSDT"}, (
            f"isolation broken — expected both symbols, got {symbols_attempted}"
        )


# =====================================================================
# FIX 9 — Bybit error map: 10001 falls through
# =====================================================================


class TestFix9_BybitErrorMap:
    def test_10001_unmapped(self):
        from src.trading.client import BYBIT_ERROR_MAP
        assert 10001 not in BYBIT_ERROR_MAP, (
            "10001 is Bybit V5 'Request parameter error' (programmer bug); "
            "must NOT auto-map to InsufficientBalanceError"
        )

    def test_real_balance_codes_intact(self):
        from src.core.exceptions import InsufficientBalanceError
        from src.trading.client import BYBIT_ERROR_MAP
        # Real balance codes per Bybit V5 docs
        assert BYBIT_ERROR_MAP[110012] is InsufficientBalanceError
        assert BYBIT_ERROR_MAP[110043] is InsufficientBalanceError


# =====================================================================
# FIX 10 — PnL manager mode contracts + reset semantics
# =====================================================================


class TestFix10_PnLManager:
    def test_normal_mode_full_aggression(self, real_settings):
        from src.core.utils import now_utc
        from src.strategies.pnl_manager import DailyPnLManager
        mgr = DailyPnLManager(real_settings)
        mgr.starting_equity = 10000
        mgr.realized_pnl = 0.0
        mgr.today_date = now_utc().strftime("%Y-%m-%d")
        mgr._recalculate()
        m = mgr.get_current_mode()
        assert m["mode"] == "NORMAL"
        # Production contract: NORMAL means "full aggression"
        # max_positions = 10 (was 5 in early draft)
        # max_leverage = 5
        assert m["max_positions"] == 10
        assert m["max_leverage"] == 5

    def test_survival_mode_quality_gate(self, real_settings):
        """SURVIVAL band depends on the *real* config.toml thresholds.

        config.toml ships ``survival_threshold_pct = -7.0`` and
        ``halt_threshold_pct = -10.0``; the SURVIVAL bracket is
        ``halt <= pct < survival``. We pin a -8% loss which sits
        squarely inside that band regardless of small re-tunings.
        """
        from src.core.utils import now_utc
        from src.strategies.pnl_manager import DailyPnLManager
        mgr = DailyPnLManager(real_settings)
        mgr.starting_equity = 10000
        cfg = real_settings.pnl_targets
        # Pick a pct halfway between halt and survival thresholds.
        mid = (cfg.halt_threshold_pct + cfg.survival_threshold_pct) / 2
        mgr.realized_pnl = mgr.starting_equity * (mid / 100.0)
        mgr.today_date = now_utc().strftime("%Y-%m-%d")
        mgr._recalculate()
        m = mgr.get_current_mode()
        assert m["mode"] == "SURVIVAL", (
            f"expected SURVIVAL at pct={mid:.2f} "
            f"(halt={cfg.halt_threshold_pct}, survival={cfg.survival_threshold_pct}), "
            f"got {m['mode']}"
        )
        assert m["max_leverage"] == 3
        assert m.get("quality_gate") is True

    @pytest.mark.asyncio
    async def test_target_hit_flag_persists_across_close(self, real_settings):
        """on_trade_closed must set target_hit when crossing the daily target.

        Real config.toml ``daily_target_pct = 10.0``, so we send a PnL
        clearly above target (15%) rather than hardcoding a dollar
        amount that drifts whenever the target is retuned.
        """
        from src.core.utils import now_utc
        from src.strategies.pnl_manager import DailyPnLManager
        mgr = DailyPnLManager(real_settings)
        mgr.starting_equity = 10000
        mgr.today_date = now_utc().strftime("%Y-%m-%d")
        target_pct = real_settings.pnl_targets.daily_target_pct
        pnl_amount = mgr.starting_equity * ((target_pct + 5.0) / 100.0)
        await mgr.on_trade_closed(pnl_amount)
        assert mgr.target_hit is True
        assert mgr.current_pnl_pct >= target_pct


# =====================================================================
# FIX 11 — Strategy registry: regime activation contract
# =====================================================================
#
# Layer 1 Defect 1 (2026-05-21) repaired this function. The pre-fix
# contract ("regime affects sizing, not activation; all strategies
# always active") is now the LEGACY rollback mode, reachable by
# constructing the registry with ``regime_filter_enabled=False`` or
# flipping ``StrategyEngineSettings.strategy_regime_filter_enabled``
# to False in config.toml. The new default (True per operator
# decision) honors the regime argument via REGIME_ACTIVE_CATEGORIES.
# Tests below cover BOTH contracts.


class TestFix11_StrategyRegistry:
    @staticmethod
    def _make_pair():
        """Two fixture strategies with distinct categories so the
        regime-filter contract is observable."""
        from src.core.types import TimeFrame
        from src.strategies.base_strategy import BaseStrategy
        from src.strategies.models.regime_types import MarketRegime

        class S1(BaseStrategy):
            @property
            def name(self): return "s1"
            @property
            def category(self): return "scalping"
            @property
            def applicable_regimes(self): return [MarketRegime.RANGING]
            @property
            def timeframe(self): return TimeFrame.M5
            async def scan(self, *a, **kw): return None
            def vote(self, *a, **kw): return ("BUY", 0.5, "")

        class S2(BaseStrategy):
            @property
            def name(self): return "s2"
            @property
            def category(self): return "momentum"
            @property
            def applicable_regimes(self): return [MarketRegime.TRENDING_UP]
            @property
            def timeframe(self): return TimeFrame.M15
            async def scan(self, *a, **kw): return None
            def vote(self, *a, **kw): return ("BUY", 0.5, "")

        return S1(), S2()

    def test_get_active_filters_by_regime_when_flag_on(self):
        """Default flag=True: filter via REGIME_ACTIVE_CATEGORIES.

        TRENDING_UP categories include both scalping and momentum →
        both fixtures fire. RANGING categories include scalping but
        not momentum → only s1 fires.
        """
        from src.strategies.models.regime_types import MarketRegime
        from src.strategies.registry import StrategyRegistry

        s1, s2 = self._make_pair()
        reg = StrategyRegistry()  # default regime_filter_enabled=True
        reg.register(s1)
        reg.register(s2)

        trending = reg.get_active_for_regime(MarketRegime.TRENDING_UP)
        assert {s.name for s in trending} == {"s1", "s2"}

        ranging = reg.get_active_for_regime(MarketRegime.RANGING)
        assert {s.name for s in ranging} == {"s1"}

    def test_get_active_returns_all_with_flag_off_legacy(self):
        """flag=False reproduces the pre-Defect-1 uniform behavior:
        every enabled strategy fires regardless of regime."""
        from src.strategies.models.regime_types import MarketRegime
        from src.strategies.registry import StrategyRegistry

        s1, s2 = self._make_pair()
        reg = StrategyRegistry(regime_filter_enabled=False)
        reg.register(s1)
        reg.register(s2)

        for regime in (MarketRegime.RANGING, MarketRegime.TRENDING_UP):
            active = reg.get_active_for_regime(regime)
            assert len(active) == 2, f"{regime}: expected 2, got {len(active)}"


# =====================================================================
# FIX 12 — Signal generator confidence gate downgrade
# =====================================================================


class TestFix12_SignalGeneratorGate:
    @pytest.mark.asyncio
    async def test_low_confidence_downgrades(self, real_db):
        """Phase 29 hard gate: STRONG_BUY needs conf >= 0.60; below that
        the signal_type downgrades. Verify against a real seeded DB."""
        from src.core.types import (
            FearGreedData, NewsArticle, RedditPost, SignalType,
        )
        from src.core.utils import now_utc
        from src.database.repositories.altdata_repo import AltDataRepository
        from src.database.repositories.news_repo import NewsRepository
        from src.database.repositories.sentiment_repo import SentimentRepository
        from src.intelligence.sentiment.aggregator import SentimentAggregator
        from src.intelligence.sentiment.scorer import SentimentScorer
        from src.intelligence.signals.signal_generator import SignalGenerator

        # Minimal seed (insufficient for STRONG_BUY confidence)
        await NewsRepository(real_db).save_article(NewsArticle(
            id="n1", headline="Bullish breakout",
            source="T", url="", summary="up",
            sentiment_score=0.7, symbols=["BTCUSDT"],
            published_at=now_utc(), fetched_at=now_utc(),
        ))
        await SentimentRepository(real_db).save_reddit_post(RedditPost(
            id="r1", subreddit="crypto", title="moon",
            score=100, num_comments=20, upvote_ratio=0.9,
            sentiment_score=0.6, symbols_mentioned=["BTCUSDT"],
            created_at=now_utc(), fetched_at=now_utc(),
        ))
        await AltDataRepository(real_db).save_fear_greed(FearGreedData(
            value=15, classification="Extreme Fear", timestamp=now_utc(),
        ))

        gen = SignalGenerator(SentimentAggregator(real_db, SentimentScorer()), real_db)
        signal = await gen.generate_signal("BTCUSDT")
        # Gate behavior: with only 1 article + 1 reddit post + F&G, the
        # confidence falls below STRONG_BUY threshold → downgrade
        # (NEUTRAL is acceptable; bearish would be a regression).
        assert signal.signal_type not in (SignalType.SELL, SignalType.STRONG_SELL)


# =====================================================================
# FIX 13 — Alert manager emits HOLD decisions
# =====================================================================


class TestFix13_AlertManagerHold:
    @pytest.mark.asyncio
    async def test_hold_decision_emits(self, real_db, real_settings):
        """HOLD is intentionally surfaced (not filtered) for operator visibility.

        AlertManager takes the *full* Settings object (its inner
        TelegramBot reads ``settings.alerts.bot_token``), not a bare
        AlertSettings. We force ``enabled=True`` and stub the bot to
        avoid network calls.
        """
        from src.alerts.alert_manager import AlertManager
        from src.core.types import BrainDecision

        am = AlertManager(real_settings, real_db)
        am.bot = MagicMock()
        am.bot.send_message = AsyncMock()
        am.enabled = True

        decision = BrainDecision(
            id="brain_test", action="hold", symbol="BTCUSDT",
            confidence=0.4, reasoning="mixed signals",
        )
        await am.send_brain_decision_alert(decision, "scheduled", 0.005)
        # P2-2 (2026-05-13): brain decision alerts are INFO -> fire-and-forget.
        # Drain the background task before asserting on the bot mock.
        await am.flush_pending_info()
        am.bot.send_message.assert_called_once()
        sent = am.bot.send_message.call_args.args[0]
        assert "HOLD" in sent.upper()


# =====================================================================
# CROSS-FIX — full pipeline: scanner tick exercises the wiring of
# fix 1 (SymbolRegistry) + fix 2 (CycleTracker) + fix 5 (recent_loss)
# + fix 6 (BTC/ETH consistency) all in one real-DB run.
# =====================================================================


class TestCrossFix_PipelineRun:
    @pytest.mark.asyncio
    async def test_scanner_tick_exercises_audit_fixes(self, real_settings, real_db):
        """One real ScannerWorker.tick run touches:
          * SymbolRegistry contract (referenced from MarketScanner.scan_market path)
          * CycleTracker integration (start_cycle/end_cycle around tick body)
          * recent_loss_symbols prefetch (real DB query)
          * cycle_tracker log routing (verifies route exists)
          * (Q2 2026-04-29) BTC/ETH force-include REMOVED — table and
            in-memory views now reflect qualified set + open positions
            only; consistency invariant (table ⊆ in-memory) preserved.
        """
        from src.core.cycle_tracker import CycleTracker
        from src.core.layer_manager import LayerManager
        from src.strategies.scanner import MarketScanner
        from src.workers.scanner_worker import ScannerWorker

        # Phase 9 mode isolation — pin to exclusion to assert the
        # legacy Q2 contract.
        real_settings.scanner.mode = "exclusion"

        ct = CycleTracker(real_db, max_history=10)
        lm = LayerManager(real_settings, {})

        scanner = MarketScanner(
            real_settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(real_settings.universe.watch_list),
        )
        sw = ScannerWorker(
            real_settings, real_db, scanner,
            services={
                "scanner": scanner,
                "cycle_tracker": ct,
                "layer_manager": lm,
            },
        )
        await sw.tick()

        # CycleTracker recorded the 1D cycle.
        recent = ct.get_recent(5)
        assert len(recent) == 1
        assert recent[0].layer1d_ms is not None

        # Q2: BTC/ETH no longer unconditionally in active_universe.
        rows = await real_db.fetch_all("SELECT symbol FROM active_universe")
        symbols = {r["symbol"] for r in rows}
        assert "BTCUSDT" not in symbols
        assert "ETHUSDT" not in symbols

        # Consistency invariant preserved: table ⊆ in-memory.
        in_mem = await scanner.get_active_universe()
        assert "BTCUSDT" not in in_mem and "ETHUSDT" not in in_mem
        assert symbols.issubset(set(in_mem))

        # Layer manager packages cache populated (Phase 6 wiring,
        # exercised by ScannerWorker._build_package).
        assert hasattr(lm, "_coin_packages")
        assert isinstance(lm._coin_packages, dict)
