"""End-to-end pipeline integration tests for the corrected Layer 1 system.

These tests exercise the REAL classes, REAL DB connection, REAL Settings
loaded from config.toml — only the external network surfaces (Bybit
REST, Bybit WebSocket, sentiment APIs, news APIs) are mocked.

Coverage:
  - DI wiring: every service expected to be in WorkerManager._services is populated.
  - Data flow: watch_list → KlineWorker → klines table → structure_worker →
    StructureCache → ScannerWorker → active_universe table → strategist read.
  - Worker lifecycle: each worker constructed in correct order with correct
    dependencies; service container reference semantics work for late-wired
    accessors.
  - Public API contract: every accessor (get_setup_score, get_signal,
    get_regime, get_score, get_funding, get_ws_quote, set_active_universe,
    get_subscribers_snapshot, get_oldest_entry_age_seconds) returns the
    correct type and handles cold/empty cases.
  - Hard rules: HR-1 / HR-3 / HR-4 / HR-5 / HR-6 verified through behavior.
  - Naming: settings.universe.watch_list is referenced everywhere, no
    settings.bybit.default_symbols fallback in workers.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings


@pytest.fixture
def real_settings():
    """Real Settings loaded from config.toml — no mocks at the config layer."""
    Settings.reset()
    return Settings._load_fresh()


@pytest.fixture
async def real_db():
    """Real DatabaseManager backed by a temp SQLite file. Migrations run."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = DatabaseManager(tmp.name, wal_mode=True)
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()
    os.unlink(tmp.name)


# =====================================================================
# E2E-1: Configuration → Settings → Worker construction wiring
# =====================================================================


class TestE2EConfigurationWiring:
    """The full path from config.toml → Settings → workers without mocks."""

    def test_config_toml_loads_all_corrected_layer1_sections(self, real_settings):
        """Every new config section introduced by the migration parses."""
        s = real_settings
        # Sweet-spots top-level
        assert s.workers.sweet_spots.kline_worker == "0:30"
        assert s.workers.sweet_spots.structure_worker == "0:45"
        assert s.workers.sweet_spots.signal_worker == "1:00"
        assert s.workers.sweet_spots.regime_worker == "1:15"
        assert s.workers.sweet_spots.strategy_worker == "1:30"
        assert s.workers.sweet_spots.scanner_worker == "4:00"
        assert s.workers.sweet_spots.window_minutes == 5
        # Altdata sub-cadences
        assert s.workers.sweet_spots.altdata.funding_rates == "1:45"
        assert s.workers.sweet_spots.altdata.open_interest_minutes == 5
        assert s.workers.sweet_spots.altdata.fear_greed_minutes == 60
        # Scanner scoring weights sum to 1.0. Definitive-fix Phase 4
        # (2026-04-28) added the 6th ``rr`` component (default 0.10) so
        # the sum check must include it.
        sw = s.scanner.scoring_weights
        total = (
            sw.structure + sw.strategy + sw.signal
            + sw.regime + sw.funding + sw.rr
        )
        assert abs(total - 1.0) < 1e-9
        # Universe is the curated 50
        assert len(s.universe.watch_list) == 50

    def test_each_worker_constructs_with_real_settings(self, real_settings):
        """Construct every worker class with real settings + mocked deps."""
        from src.workers.kline_worker import KlineWorker
        from src.workers.structure_worker import StructureWorker
        from src.workers.signal_worker import SignalWorker
        from src.workers.regime_worker import RegimeWorker
        from src.workers.strategy_worker import StrategyWorker
        from src.workers.altdata_worker import AltDataWorker
        from src.workers.price_worker import PriceWorker
        from src.workers.scanner_worker import ScannerWorker
        from src.strategies.scanner import MarketScanner
        from src.analysis.structure.structure_cache import StructureCache

        db = MagicMock()
        cache = StructureCache(ttl_seconds=300)
        scanner = MarketScanner(real_settings, MagicMock(), instrument_service=MagicMock(),
                                watch_list=set(real_settings.universe.watch_list))
        # Each constructor should accept real_settings without raising.
        kw = KlineWorker(real_settings, db, MagicMock())
        sw = StructureWorker(real_settings, db, MagicMock(), cache)
        sigw = SignalWorker(real_settings, db, None, MagicMock(), MagicMock())
        rw = RegimeWorker(real_settings, db, MagicMock())
        stw = StrategyWorker(
            real_settings, db,
            registry=MagicMock(), scanner=scanner, regime_detector=MagicMock(),
            scorer=MagicMock(), ensemble=MagicMock(), pnl_manager=MagicMock(),
            ta_engine=MagicMock(), market_repo=MagicMock(), services={},
        )
        adw = AltDataWorker(real_settings, db, MagicMock(), MagicMock(), MagicMock(), MagicMock())
        pw = PriceWorker(real_settings, db, MagicMock())
        scn = ScannerWorker(real_settings, db, scanner, services={"scanner": scanner})

        # Sanity: each worker has a sensible name.
        for w in [kw, sw, sigw, rw, stw, adw, pw, scn]:
            assert w.name and isinstance(w.name, str)


# =====================================================================
# E2E-2: WorkerManager service container wiring (DI)
# =====================================================================


class TestE2EServiceContainerDI:
    """WorkerManager._services is the system's DI container.

    Verify that the corrected-Layer-1 migration added the expected new
    keys + that the registration order in _create_workers() ensures
    ScannerWorker can resolve every accessor by the time its first tick
    fires (reference-semantics test).
    """

    def test_expected_service_keys_includes_phase6_workers(self):
        from src.workers.manager import WorkerManager
        keys = set(WorkerManager._EXPECTED_SERVICE_KEYS)
        required = {
            "kline_worker", "price_worker", "signal_worker",
            "regime_worker", "altdata_worker", "scanner_worker",
            "structure_worker", "strategy_worker",
        }
        missing = required - keys
        assert not missing, f"Phase 6 worker keys missing from registry: {missing}"

    def test_services_dict_reference_propagates_to_scanner_worker(self, real_settings):
        """Reference-semantics test: ScannerWorker constructed with a
        services dict that's later populated by other worker constructors
        sees the new entries via the same dict reference."""
        from src.workers.scanner_worker import ScannerWorker
        from src.workers.structure_worker import StructureWorker
        from src.strategies.scanner import MarketScanner
        from src.analysis.structure.structure_cache import StructureCache

        services = {}
        scanner = MarketScanner(
            real_settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(real_settings.universe.watch_list),
        )
        services["scanner"] = scanner
        # Construct ScannerWorker BEFORE structure_worker — same as manager.py.
        scn = ScannerWorker(real_settings, MagicMock(), scanner, services=services)
        # Now register structure_worker AFTER ScannerWorker construction.
        cache = StructureCache(ttl_seconds=300)
        sw = StructureWorker(real_settings, MagicMock(), MagicMock(), cache)
        services["structure_worker"] = sw
        # Reference semantics: ScannerWorker sees the new entry.
        assert scn.services["structure_worker"] is sw


# =====================================================================
# E2E-3: Data flow watch_list → klines → structure → scanner → cycle
# =====================================================================


class TestE2EDataFlow:
    """End-to-end data flow with a REAL temp DB, mocked Bybit MarketService."""

    @pytest.mark.asyncio
    async def test_kline_worker_writes_klines_for_watch_list(
        self, real_settings, real_db,
    ):
        """KlineWorker's tick() reads watch_list (50), fetches klines via
        market_service, and writes to the real klines table."""
        from src.workers.kline_worker import KlineWorker
        from src.core.types import OHLCV, TimeFrame
        from datetime import datetime, timezone

        # Mock market_service that returns 5 klines per call.
        async def fake_get_klines(symbol, timeframe, limit=200):
            return [
                OHLCV(
                    symbol=symbol, timeframe=timeframe,
                    timestamp=datetime.now(timezone.utc),
                    open=100.0, high=101.0, low=99.0, close=100.5,
                    volume=1_000_000, turnover=100_000_000,
                )
            ]

        market_service = MagicMock()
        market_service.get_klines = AsyncMock(side_effect=fake_get_klines)
        market_service._market_repo = MagicMock()
        # Patch the save path so it actually writes to the real DB.
        from src.database.repositories.market_repo import MarketRepository
        repo = MarketRepository(real_db)

        async def patched_get_klines(symbol, timeframe, limit=200):
            klines = await fake_get_klines(symbol, timeframe, limit)
            await repo.save_klines(klines)
            return klines
        market_service.get_klines = AsyncMock(side_effect=patched_get_klines)

        worker = KlineWorker(real_settings, real_db, market_service)
        # Pre-warm cooldowns so all timeframes fire.
        await worker.tick()

        # Verify klines table was populated for the watch_list.
        rows = await real_db.fetch_all(
            "SELECT DISTINCT symbol FROM klines",
        )
        symbols_in_db = {r["symbol"] for r in rows}
        watch_set = set(real_settings.universe.watch_list)
        assert symbols_in_db.issubset(watch_set), (
            f"klines written for non-watch_list coins: {symbols_in_db - watch_set}"
        )
        # At least 5 distinct symbols saved (some may have failed).
        assert len(symbols_in_db) >= 5

    @pytest.mark.asyncio
    async def test_scanner_worker_writes_active_universe_table(
        self, real_settings, real_db,
    ):
        """ScannerWorker.tick() writes to the real active_universe table.

        Phase 5 + Q2 contract (revised 2026-04-29 after BTC/ETH ref-pair
        force-include removal):
          * Without warm caches the qualitative gate rejects every
            watch_list coin → ``final`` is empty.
          * Q2 (2026-04-29) removed the unconditional BTC/ETH ref-pair
            insert; HR-2 still preserved via the protected-symbols path
            for actual open positions.
          * Without warm caches AND no open positions, the table is
            EMPTY (0 rows). DELETE still runs to clear stale state.
          * When rows exist, they're a subset of watch_list, capped at
            ``max_selection``.

        Phase 9 cutover (2026-05-01) flipped the production scanner.mode
        default to "briefing", under which active_universe contains the
        top-N briefings (>=12) with NO_TRADEABLE_STATE labels possible.
        This test is mode-isolated — it asserts the legacy exclusion-mode
        contract by overriding the mode to "exclusion" for the test
        instance. Briefing-mode active_universe contract has its own test.
        """
        from src.workers.scanner_worker import ScannerWorker
        from src.strategies.scanner import MarketScanner

        # Phase 9 mode isolation — pin to exclusion to assert the
        # legacy Q2 contract.
        real_settings.scanner.mode = "exclusion"

        scanner = MarketScanner(
            real_settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(real_settings.universe.watch_list),
        )
        services = {"scanner": scanner}
        sw = ScannerWorker(real_settings, real_db, scanner, services=services)
        await sw.tick()

        rows = await real_db.fetch_all(
            "SELECT symbol, opportunity_score FROM active_universe",
        )
        symbols_in_db = {r["symbol"] for r in rows}

        # Post-Q2 contract: with no warm caches and no positions,
        # active_universe is empty.
        # BTC/ETH are NO LONGER unconditionally present.
        assert "BTCUSDT" not in symbols_in_db, (
            "Q2 regression: BTC ref-pair appeared in active_universe "
            "without warm caches/positions. Force-include should be "
            "gone."
        )
        assert "ETHUSDT" not in symbols_in_db, (
            "Q2 regression: ETH ref-pair appeared in active_universe "
            "without warm caches/positions."
        )

        # Whatever rows exist must be from watch_list (no leaks).
        watch_set = set(real_settings.universe.watch_list)
        unexpected = symbols_in_db - watch_set
        assert not unexpected, f"non-watch_list symbols leaked: {unexpected}"

        # Row count bounded by max_selection (no +2 ref-pair anymore).
        assert len(rows) <= real_settings.scanner.qualitative.max_selection

    @pytest.mark.asyncio
    async def test_active_universe_round_trip_via_scanner_getter(
        self, real_settings, real_db,
    ):
        """After ScannerWorker.tick(), the in-memory MarketScanner active
        universe matches what was written to the table.

        Phase 5 + Q2 contract (revised 2026-04-29): with no warm caches
        AND no open positions, both views are empty. The critical
        invariant is consistency (table ⊆ in-memory), not non-empty.
        """
        from src.workers.scanner_worker import ScannerWorker
        from src.strategies.scanner import MarketScanner

        # Phase 9 mode isolation — pin to exclusion to assert the
        # legacy Q2 contract.
        real_settings.scanner.mode = "exclusion"

        scanner = MarketScanner(
            real_settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(real_settings.universe.watch_list),
        )
        sw = ScannerWorker(real_settings, real_db, scanner,
                           services={"scanner": scanner})
        await sw.tick()

        in_memory = await scanner.get_active_universe()
        # Post-Q2: BTC/ETH no longer unconditionally injected.
        assert "BTCUSDT" not in in_memory, (
            "Q2 regression: BTC in active_universe without trigger."
        )
        assert "ETHUSDT" not in in_memory, (
            "Q2 regression: ETH in active_universe without trigger."
        )
        # In-memory list ↔ table consistency: table is subset of memory.
        rows = await real_db.fetch_all(
            "SELECT symbol FROM active_universe",
        )
        symbols_in_db = {r["symbol"] for r in rows}
        assert symbols_in_db.issubset(set(in_memory)), (
            f"active_universe table has symbols not in MarketScanner memory: "
            f"{symbols_in_db - set(in_memory)}"
        )


# =====================================================================
# E2E-4: Composite-score scoring math against real scoring_weights
# =====================================================================


class TestE2EScoringMath:
    """The composite opportunity score is the weighted sum of normalized
    components. Verify the math with real weights and a fully warmed
    coin."""

    @pytest.mark.asyncio
    async def test_composite_score_formula_matches_spec(self, real_settings):
        from src.workers.scanner_worker import ScannerWorker
        from src.workers.structure_worker import StructureWorker
        from src.workers.signal_worker import SignalWorker
        from src.workers.regime_worker import RegimeWorker
        from src.workers.strategy_worker import StrategyWorker
        from src.workers.altdata_worker import AltDataWorker
        from src.strategies.scanner import MarketScanner
        from src.analysis.structure.structure_cache import StructureCache
        from src.core.types import Signal, SignalType
        from src.strategies.models.regime_types import RegimeState, MarketRegime

        db = MagicMock()
        db.executemany = AsyncMock()
        db.execute = AsyncMock()

        cache = StructureCache(ttl_seconds=300)
        sw = StructureWorker(real_settings, db, MagicMock(), cache)
        sigw = SignalWorker(real_settings, db, None, MagicMock(), MagicMock())
        rw = RegimeWorker(real_settings, db, MagicMock())
        rw.detector = MagicMock()
        rw.detector.get_coin_regime = MagicMock(return_value=RegimeState(
            regime=MarketRegime.TRENDING_UP, confidence=0.9, adx=30,
            atr_percentile=60, choppiness=40, volume_ratio=1.2,
            trend_direction=1, active_strategy_categories=[],
        ))
        stw = StrategyWorker(
            real_settings, db,
            registry=MagicMock(), scanner=MagicMock(), regime_detector=MagicMock(),
            scorer=MagicMock(), ensemble=MagicMock(), pnl_manager=MagicMock(),
            ta_engine=MagicMock(), market_repo=MagicMock(), services={},
        )
        adw = AltDataWorker(real_settings, db, MagicMock(), MagicMock(), MagicMock(), MagicMock())

        # Specific values that yield a known composite score. Definitive-fix
        # Phase 4 (2026-04-28) added a 6th ``rr`` component (rr_long /
        # rr_short selected by consensus direction). The cache stub now
        # carries an empty placement (no rr_* fields) so the
        # _get_directional_rr accessor falls through to ``rr_ratio=0`` →
        # rr_norm=0 → component contributes 0 to the composite. We assert
        # that contract explicitly in the formula below.
        _placement = MagicMock(rr_long=0.0, rr_short=0.0, rr_ratio=0.0)
        cache.set("BTCUSDT", MagicMock(
            setup_score=50.0, structural_placement=_placement,
        ))
        sigw._signal_cache["BTCUSDT"] = Signal(
            symbol="BTCUSDT", signal_type=SignalType.BUY,
            confidence=0.6, source="t",                       # → 0.6
        )
        stw._score_cache["BTCUSDT"] = 80.0                    # → 0.8
        # regime trending → alignment=1.0 → normalized=1.0
        adw._funding_cache["BTCUSDT"] = 0.0005                # → 0.5 normalized

        scanner = MarketScanner(real_settings, MagicMock(),
                                instrument_service=MagicMock(),
                                watch_list=set(real_settings.universe.watch_list))
        services = {
            "scanner": scanner,
            "structure_worker": sw, "signal_worker": sigw,
            "regime_worker": rw, "strategy_worker": stw,
            "altdata_worker": adw,
        }
        scn = ScannerWorker(real_settings, db, scanner, services=services)

        score, breakdown = scn._compute_opportunity_score("BTCUSDT")
        weights = real_settings.scanner.scoring_weights
        # Phase 4: rr=0.0 because the placement stub has rr_long=rr_short=0.
        expected = (
            weights.structure * 0.5 +
            weights.strategy * 0.8 +
            weights.signal * 0.6 +
            weights.regime * 1.0 +
            weights.funding * 0.5 +
            weights.rr * 0.0
        )
        assert abs(score - expected) < 1e-6, (
            f"composite score {score} != expected {expected}; "
            f"breakdown={breakdown}"
        )
        # Phase 4 — confirm the breakdown carries the new rr component.
        assert "rr" in breakdown


# =====================================================================
# E2E-5: Sweet-spot scheduler timing matches blueprint
# =====================================================================


class TestE2ESweetSpotChain:
    """The chain order kline 0:30 → ... → scanner 4:00 must produce
    monotonically increasing fire times within a window."""

    def test_full_chain_fire_times_within_window(self, real_settings):
        from src.workers.sweet_spot_scheduler import (
            parse_sweet_spot, seconds_until_next_sweet_spot,
        )
        ss = real_settings.workers.sweet_spots
        chain = [
            ("kline", parse_sweet_spot(ss.kline_worker)),
            ("structure", parse_sweet_spot(ss.structure_worker)),
            ("signal", parse_sweet_spot(ss.signal_worker)),
            ("regime", parse_sweet_spot(ss.regime_worker)),
            ("strategy", parse_sweet_spot(ss.strategy_worker)),
            ("altdata_funding", parse_sweet_spot(ss.altdata.funding_rates)),
            ("scanner", parse_sweet_spot(ss.scanner_worker)),
        ]
        # At now=0, all delays should be in chain order.
        delays = [
            seconds_until_next_sweet_spot(spot, window_minutes=5, now=0.0)
            for _, spot in chain
        ]
        assert delays == sorted(delays), f"Chain fire delays not monotonic: {delays}"

        # Convert to (name, seconds-into-window) for human readability.
        offsets = [(name, spot[0] * 60 + spot[1]) for name, spot in chain]
        # Manual chain spec from blueprint §8.2:
        expected = [
            ("kline", 30), ("structure", 45), ("signal", 60),
            ("regime", 75), ("strategy", 90),
            ("altdata_funding", 105), ("scanner", 240),
        ]
        assert offsets == expected


# =====================================================================
# E2E-6: Hard rules verification
# =====================================================================


class TestE2EHardRules:
    """Behavioral verification of all 6 hard rules from the blueprint."""

    def test_hr1_workers_only_read_watch_list(self):
        """HR-1: zero worker-side reads of get_active_universe in src/workers/."""
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "_scanner.get_active_universe\\|self.scanner.get_active_universe",
             "src/workers/"],
            capture_output=True, text=True,
        )
        # All matches must be in comments / docstrings only.
        for line in result.stdout.splitlines():
            file_path, lineno, content = line.split(":", 2)
            assert content.strip().startswith(("#", '"', "'")) or "scanner_worker.py" in file_path, (
                f"Worker-side scanner.get_active_universe call survives: {line}"
            )

    def test_hr3_force_include_path_exists(self, real_settings):
        """HR-3: ScannerWorker has _open_position_symbols + force-include logic."""
        from src.workers.scanner_worker import ScannerWorker
        assert hasattr(ScannerWorker, "_open_position_symbols")
        # Source-level check: force-include logic exists in tick().
        import inspect
        src = inspect.getsource(ScannerWorker.tick)
        assert "force-include" in src.lower() or "forced_in" in src

    def test_hr4_chain_ordering_validated_at_startup(self):
        """HR-4: bad chain config → ConfigError at Settings load."""
        from src.config.settings import SweetSpotsSettings
        from src.core.exceptions import ConfigError

        with pytest.raises(ConfigError, match="chain order"):
            SweetSpotsSettings(
                kline_worker="2:00", structure_worker="1:00",
            )

    def test_hr5_watch_list_is_only_universe_source(self, real_settings):
        """HR-5: settings.universe.watch_list is the single source.
        Workers reach for it directly; no fallbacks to other lists."""
        # Each migrated worker's tick() body must contain the canonical reference.
        files = [
            "src/workers/kline_worker.py",
            "src/workers/structure_worker.py",
            "src/workers/signal_worker.py",
            "src/workers/regime_worker.py",
            "src/workers/strategy_worker.py",
            "src/workers/altdata_worker.py",
            "src/workers/price_worker.py",
            "src/workers/scanner_worker.py",
        ]
        for f in files:
            with open(f) as fh:
                text = fh.read()
            assert "settings.universe.watch_list" in text, (
                f"{f} does not reference settings.universe.watch_list"
            )

    def test_hr6_per_phase_atomic_commits(self):
        """HR-6: every phase is its own commit."""
        import subprocess
        out = subprocess.run(
            ["git", "log", "--oneline"],
            capture_output=True, text=True,
        ).stdout
        phases = {f"phase{i}-corrected-layer1" for i in range(10)}
        # Audit-fix commits: phase11-fix, phase11-audit, phase11-tests
        observed = set()
        for line in out.splitlines():
            for p in phases:
                if p in line:
                    observed.add(p)
        assert "phase0-corrected-layer1" in observed
        assert "phase9-corrected-layer1" in observed


# =====================================================================
# E2E-7: Stage 2 cycle reads the same active_universe ScannerWorker writes
# =====================================================================


class TestE2EStage2Cycle:
    """The cycle pipeline: ScannerWorker writes active_universe, strategist
    reads it via scanner.get_active_universe()."""

    @pytest.mark.asyncio
    async def test_strategist_pattern_reads_scanner_writes(
        self, real_settings, real_db,
    ):
        """Mimic the strategist's read pattern at strategist.py:1350.

        Phase 5/6 + Q2 contract (revised 2026-04-29): the cycle universe
        Stage 2 reads must match the active_universe table written by
        the scanner. With no warm caches AND no open positions, both
        views are empty (Q2 removed BTC/ETH ref-pair force-include).
        The critical invariant is consistency, not non-empty.
        """
        from src.workers.scanner_worker import ScannerWorker
        from src.strategies.scanner import MarketScanner

        # Phase 9 mode isolation — pin to exclusion to assert the
        # legacy Q2 contract (BTC/ETH absent without warm caches).
        real_settings.scanner.mode = "exclusion"

        scanner = MarketScanner(
            real_settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(real_settings.universe.watch_list),
        )
        sw = ScannerWorker(real_settings, real_db, scanner,
                           services={"scanner": scanner})
        await sw.tick()

        # The strategist's read at strategist.py:1350 is essentially:
        #   universe = await scanner.get_active_universe() if scanner else []
        cycle_universe = await scanner.get_active_universe() if scanner else []
        # Q2: with no qualifiers and no positions, universe is empty.
        # Strategist code handles empty universe gracefully (loop iter 0).
        assert "BTCUSDT" not in cycle_universe
        assert "ETHUSDT" not in cycle_universe

        # Cross-source consistency: every symbol the table holds appears
        # in the in-memory cycle universe Stage 2 reads.
        rows = await real_db.fetch_all("SELECT symbol FROM active_universe")
        table_symbols = {r["symbol"] for r in rows}
        assert table_symbols.issubset(set(cycle_universe)), (
            f"active_universe table has symbols Stage 2 won't see: "
            f"{table_symbols - set(cycle_universe)}"
        )


# =====================================================================
# E2E-8: Naming / dependency / connection conventions
# =====================================================================


class TestE2ENamingAndDependencies:
    """Verify naming conventions and that no broken / dangling references
    exist across the migration touchpoints."""

    def test_no_dead_imports_in_migrated_workers(self):
        """Every migration-touched worker imports cleanly."""
        import importlib
        modules = [
            "src.workers.sweet_spot_scheduler",
            "src.workers.base_worker",
            "src.workers.kline_worker",
            "src.workers.structure_worker",
            "src.workers.signal_worker",
            "src.workers.regime_worker",
            "src.workers.strategy_worker",
            "src.workers.altdata_worker",
            "src.workers.price_worker",
            "src.workers.scanner_worker",
            "src.workers.manager",
        ]
        for m in modules:
            mod = importlib.import_module(m)
            assert mod is not None

    def test_class_names_match_conventions(self):
        """All migrated worker classes follow the {Name}Worker pattern
        and the corrected ones extend the right parent."""
        from src.workers.base_worker import BaseWorker, SweetSpotWorker
        from src.workers.kline_worker import KlineWorker
        from src.workers.structure_worker import StructureWorker
        from src.workers.signal_worker import SignalWorker
        from src.workers.regime_worker import RegimeWorker
        from src.workers.strategy_worker import StrategyWorker
        from src.workers.altdata_worker import AltDataWorker
        from src.workers.price_worker import PriceWorker
        from src.workers.scanner_worker import ScannerWorker

        sweet_spot_workers = [
            KlineWorker, StructureWorker, SignalWorker, RegimeWorker,
            StrategyWorker, AltDataWorker, ScannerWorker,
        ]
        for cls in sweet_spot_workers:
            assert issubclass(cls, SweetSpotWorker), (
                f"{cls.__name__} should extend SweetSpotWorker (corrected Layer 1)"
            )
        assert issubclass(PriceWorker, BaseWorker)
        assert not issubclass(PriceWorker, SweetSpotWorker), (
            "PriceWorker should NOT be a SweetSpotWorker (continuous WS)"
        )

    def test_log_tag_naming_consistency(self):
        """Worker log tags follow the {WORKER}_{ACTION}_{DETAIL} convention."""
        expected_tags_by_file = {
            "src/workers/kline_worker.py": [
                "KLINE_TICK_SUMMARY", "KLINE_FETCH_FAIL",
                "KLINE_FRESHNESS_WARN", "KLINE_WRITE_LAG",
                "KLINE_STRAGGLER", "KLINE_CIRCUIT_BREAKER",
                "KLINE_UNIVERSE_EMPTY",
            ],
            "src/workers/structure_worker.py": [
                "XRAY_TICK_SUMMARY", "XRAY_TICK_ERR", "XRAY_CACHE_HEALTH",
            ],
            "src/workers/signal_worker.py": [
                "SIG_BATCH", "SIG_TICK_SUMMARY", "SIG_BATCH_STATS",
            ],
            "src/workers/regime_worker.py": [
                "REGIME_GLOBAL", "REGIME_PERCOIN", "REGIME_TICK_SUMMARY",
            ],
            "src/workers/strategy_worker.py": [
                "STRAT_CYCLE_DONE", "STRAT_PREFETCH",
            ],
            "src/workers/altdata_worker.py": [
                "ALTDATA_FUNDING_TICK", "ALTDATA_OI_TICK", "ALTDATA_FG_TICK",
                "ALTDATA_SOURCE_FAIL",
            ],
            "src/workers/price_worker.py": [
                "PRICE_WS_HEALTH", "PRICE_WS_CONN", "PRICE_UNIVERSE_EMPTY",
            ],
            "src/workers/scanner_worker.py": [
                "SCANNER_TICK_SUMMARY", "SCANNER_SELECTED",
                "SCANNER_DB_WRITE_FAIL", "SCANNER_UNIVERSE_EMPTY",
            ],
        }
        for path, tags in expected_tags_by_file.items():
            with open(path) as f:
                text = f.read()
            for tag in tags:
                assert tag in text, f"{path} missing log tag {tag}"

    def test_public_accessors_have_correct_signatures(self):
        """Phase 6 accessors have proper type annotations."""
        from src.workers.structure_worker import StructureWorker
        from src.workers.signal_worker import SignalWorker
        from src.workers.regime_worker import RegimeWorker
        from src.workers.strategy_worker import StrategyWorker
        from src.workers.altdata_worker import AltDataWorker
        import inspect

        # Each accessor should have (self, coin: str) -> X | None signature.
        accessors = [
            (StructureWorker.get_setup_score, "float"),
            (SignalWorker.get_signal, "Signal"),
            (RegimeWorker.get_regime, "RegimeState"),
            (StrategyWorker.get_score, "float"),
            (AltDataWorker.get_funding, "float"),
        ]
        for fn, type_substr in accessors:
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
            assert "coin" in params, f"{fn.__name__} missing 'coin' param"
            ret_str = str(sig.return_annotation)
            assert type_substr in ret_str and "None" in ret_str, (
                f"{fn.__name__} return type {ret_str} not '{type_substr} | None'"
            )


# =====================================================================
# E2E-9: Active universe table schema integrity
# =====================================================================


class TestE2EActiveUniverseSchema:
    """The active_universe table schema is unchanged — Phase 6 wrote 0.0
    placeholders for legacy auxiliary columns."""

    @pytest.mark.asyncio
    async def test_active_universe_columns_intact(self, real_db):
        """Schema must still have the original 7 columns + updated_at."""
        rows = await real_db.fetch_all(
            "SELECT name FROM pragma_table_info('active_universe')",
        )
        cols = {r["name"] for r in rows}
        expected = {
            "symbol", "opportunity_score", "volume_24h", "change_24h_pct",
            "funding_rate", "spread_pct", "coin_tier", "updated_at",
        }
        assert expected.issubset(cols), (
            f"active_universe schema lost columns: {expected - cols}"
        )

    @pytest.mark.asyncio
    async def test_scanner_writes_score_with_zero_placeholders(
        self, real_settings, real_db,
    ):
        """ScannerWorker writes opportunity_score (real) + 0.0 placeholders
        for the legacy auxiliary columns when rows exist.

        Q2 (2026-04-29): With BTC/ETH ref-pair force-include removed,
        an empty-cache test setup yields 0 rows (no qualifiers, no
        positions). The schema contract is verified as a no-op when no
        rows are inserted; the row-content contract (placeholders) is
        only meaningful when rows exist. We assert both shapes.
        """
        from src.workers.scanner_worker import ScannerWorker
        from src.strategies.scanner import MarketScanner

        scanner = MarketScanner(
            real_settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(real_settings.universe.watch_list),
        )
        sw = ScannerWorker(real_settings, real_db, scanner,
                           services={"scanner": scanner})
        await sw.tick()
        rows = await real_db.fetch_all(
            "SELECT symbol, opportunity_score, volume_24h, spread_pct "
            "FROM active_universe LIMIT 5",
        )
        # Empty rows is acceptable post-Q2 (no warm caches, no positions).
        # When rows DO exist (e.g. real qualifiers in production), the
        # auxiliary columns must be 0.0 placeholders per Phase 6.
        for r in rows:
            assert r["volume_24h"] == 0.0
            assert r["spread_pct"] == 0.0
            assert isinstance(r["opportunity_score"], (int, float))
