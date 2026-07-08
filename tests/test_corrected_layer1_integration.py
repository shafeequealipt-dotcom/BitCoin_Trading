"""Behavioral integration tests for the corrected Layer 1 architecture.

Each test verifies an end-to-end behavior against the spec in
LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md and the deliverables of
Phases 0-9 of IMPLEMENT_LAYER1_CORRECTED_MIGRATION_PROFESSIONAL.md.

These tests do NOT touch the network, the real DB, or the real Bybit
API. They use mocks for external services and exercise the actual
worker classes + ScannerWorker scoring path + cycle reads.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings


@pytest.fixture
def settings():
    """Fresh Settings instance loaded from config.toml."""
    Settings.reset()
    return Settings._load_fresh()


@pytest.fixture
def db_mock():
    """Async-aware DB mock — execute / executemany / fetch_all all async."""
    db = MagicMock()
    db.execute = AsyncMock()
    db.executemany = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[])
    return db


# ── F-1: Sweet-spot chain ordering ─────────────────────────────────────


class TestSweetSpotChain:
    """HR-4 — chain order is enforced at startup AND respected at runtime."""

    def test_chain_order_enforced_in_settings(self, settings):
        """SweetSpotsSettings.__post_init__ rejects chain-order violations."""
        from src.config.settings import SweetSpotsSettings
        from src.core.exceptions import ConfigError

        # Default chain (kline 0:30 → ... → scanner 4:00) must pass.
        SweetSpotsSettings()  # OK

        # Invert kline + structure — must raise.
        with pytest.raises(ConfigError, match="chain order violated"):
            SweetSpotsSettings(
                kline_worker="0:45",
                structure_worker="0:30",
            )

    def test_runtime_fire_order_matches_chain(self, settings):
        """At any wall-clock moment within a window, the next-fire delays
        for the 6 chain workers must increase monotonically when sorted by
        their offsets."""
        from src.workers.sweet_spot_scheduler import (
            parse_sweet_spot,
            seconds_until_next_sweet_spot,
        )

        ss = settings.workers.sweet_spots
        chain = [
            ("kline_worker", parse_sweet_spot(ss.kline_worker)),
            ("structure_worker", parse_sweet_spot(ss.structure_worker)),
            ("signal_worker", parse_sweet_spot(ss.signal_worker)),
            ("regime_worker", parse_sweet_spot(ss.regime_worker)),
            ("strategy_worker", parse_sweet_spot(ss.strategy_worker)),
            ("scanner_worker", parse_sweet_spot(ss.scanner_worker)),
        ]
        # At now=0 (start of a window), all 6 fire in chain order.
        delays = {n: seconds_until_next_sweet_spot(spot, window_minutes=5, now=0.0)
                  for n, spot in chain}
        ordered = [delays[n] for n, _ in chain]
        assert ordered == sorted(ordered), f"Chain delays not monotonic: {ordered}"


# ── F-2: Worker scope = watch_list (HR-1, HR-5) ────────────────────────


class TestWorkerScope:
    """HR-1 / HR-5 — every data worker reads settings.universe.watch_list."""

    @pytest.mark.parametrize("worker_class,extra_args", [
        ("KlineWorker", lambda: (MagicMock(),)),
        ("StructureWorker", lambda: (MagicMock(), MagicMock())),
        ("SignalWorker", lambda: (None, MagicMock(), MagicMock())),
        ("RegimeWorker", lambda: (MagicMock(),)),
        ("AltDataWorker", lambda: (MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        ("PriceWorker", lambda: (MagicMock(),)),
    ])
    def test_init_seeds_from_watch_list(self, settings, db_mock, worker_class, extra_args):
        """Pre-seed test: every worker that exposes a tracked-symbols list
        starts with the full watch_list, not a fallback default."""
        from src.workers import (
            kline_worker, structure_worker, signal_worker,
            regime_worker, altdata_worker, price_worker,
        )
        cls_map = {
            "KlineWorker": kline_worker.KlineWorker,
            "StructureWorker": structure_worker.StructureWorker,
            "SignalWorker": signal_worker.SignalWorker,
            "RegimeWorker": regime_worker.RegimeWorker,
            "AltDataWorker": altdata_worker.AltDataWorker,
            "PriceWorker": price_worker.PriceWorker,
        }
        cls = cls_map[worker_class]
        w = cls(settings, db_mock, *extra_args())
        # Per-worker symbol attribute names vary; check the most-likely candidates.
        seeded = (
            getattr(w, "_tracked_symbols", None)
            or getattr(w, "symbols", None)
        )
        if seeded is not None:
            # If init seeds, it should match watch_list. RegimeWorker doesn't
            # pre-seed (no per-coin tracked list), so seeded is None there.
            assert seeded == list(settings.universe.watch_list), (
                f"{worker_class} did not seed from watch_list: {seeded}"
            )


# ── F-3: ScannerWorker composite-score scoring ─────────────────────────


class TestScannerComposite:
    """Phase 6 — ScannerWorker computes composite opportunity scores from
    warm worker caches."""

    def _build_warmed_services(self, settings, db_mock):
        """Construct a wired-up services dict with sample warm caches."""
        from src.workers.structure_worker import StructureWorker
        from src.workers.signal_worker import SignalWorker
        from src.workers.regime_worker import RegimeWorker
        from src.workers.strategy_worker import StrategyWorker
        from src.workers.altdata_worker import AltDataWorker
        from src.strategies.scanner import MarketScanner
        from src.analysis.structure.structure_cache import StructureCache
        from src.core.types import Signal, SignalType
        from src.strategies.models.regime_types import RegimeState, MarketRegime

        cache = StructureCache(ttl_seconds=300)
        sw = StructureWorker(settings, db_mock, MagicMock(), cache)
        sigw = SignalWorker(settings, db_mock, None, MagicMock(), MagicMock())
        rw = RegimeWorker(settings, db_mock, MagicMock())
        rw.detector = MagicMock()
        rw.detector.get_coin_regime = MagicMock(side_effect=lambda c: (
            RegimeState(
                regime=MarketRegime.TRENDING_UP, confidence=0.9, adx=30,
                atr_percentile=60, choppiness=40, volume_ratio=1.2,
                trend_direction=1, active_strategy_categories=[],
            ) if c == "BTCUSDT" else None
        ))
        stw = StrategyWorker(
            settings, db_mock,
            registry=MagicMock(), scanner=MagicMock(), regime_detector=MagicMock(),
            scorer=MagicMock(), ensemble=MagicMock(), pnl_manager=MagicMock(),
            ta_engine=MagicMock(), market_repo=MagicMock(), services={},
        )
        adw = AltDataWorker(settings, db_mock, MagicMock(), MagicMock(), MagicMock(), MagicMock())

        # Warm BTCUSDT only.
        cache.set("BTCUSDT", MagicMock(setup_score=80.0))
        sigw._signal_cache["BTCUSDT"] = Signal(
            symbol="BTCUSDT", signal_type=SignalType.BUY,
            confidence=0.9, source="t",
        )
        stw._score_cache["BTCUSDT"] = 75.0
        adw._funding_cache["BTCUSDT"] = 0.0002

        scanner = MarketScanner(
            settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(settings.universe.watch_list),
        )
        return {
            "scanner": scanner,
            "structure_worker": sw,
            "signal_worker": sigw,
            "regime_worker": rw,
            "strategy_worker": stw,
            "altdata_worker": adw,
        }

    def test_warmed_coin_scores_higher_than_cold(self, settings, db_mock):
        """A coin with all five components populated must score higher
        than a cold coin (no warmed caches)."""
        from src.workers.scanner_worker import ScannerWorker

        services = self._build_warmed_services(settings, db_mock)
        scanner_worker = ScannerWorker(settings, db_mock, services["scanner"], services=services)

        warm_score, _ = scanner_worker._compute_opportunity_score("BTCUSDT")
        cold_score, _ = scanner_worker._compute_opportunity_score("ZZYYXXUSDT")
        assert warm_score > cold_score, (
            f"warmed BTCUSDT ({warm_score}) should exceed cold ({cold_score})"
        )

    def test_score_breakdown_components_normalized(self, settings, db_mock):
        """Composite breakdown components are all in [0, 1]."""
        from src.workers.scanner_worker import ScannerWorker

        services = self._build_warmed_services(settings, db_mock)
        scanner_worker = ScannerWorker(settings, db_mock, services["scanner"], services=services)

        _, breakdown = scanner_worker._compute_opportunity_score("BTCUSDT")
        for component, value in breakdown.items():
            assert 0.0 <= value <= 1.0, f"{component}={value} out of [0,1]"


# ── F-4: HR-3 force-include for open positions ─────────────────────────


class TestHR3ForceInclude:
    """HR-3 — open-position coins force-included even when scoring zero."""

    @pytest.mark.asyncio
    async def test_offlist_position_force_included(self, settings, db_mock):
        """A position on a coin OUTSIDE watch_list must end up in
        active_universe and in MarketScanner._active_universe."""
        from src.workers.scanner_worker import ScannerWorker
        from src.strategies.scanner import MarketScanner

        scanner = MarketScanner(
            settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(settings.universe.watch_list),
        )
        # Position service returns a coin not in watch_list.
        pos = MagicMock()
        pos.symbol = "OFFLISTUSDT"
        pos_svc = MagicMock(get_positions=AsyncMock(return_value=[pos]))
        services = {"scanner": scanner, "position": pos_svc}
        scanner_worker = ScannerWorker(settings, db_mock, scanner, services=services)
        await scanner_worker.tick()

        universe = await scanner.get_active_universe()
        assert "OFFLISTUSDT" in universe, (
            "open-position coin not force-included (HR-3 violated)"
        )


# ── F-5: Cycle-side reads (Stage 2) — strategist.py:592 + :1250 ────────


class TestCycleReadsStage2:
    """Phase 8 — Stage 2 reads active_universe through MarketScanner.get_active_universe()."""

    @pytest.mark.asyncio
    async def test_strategist_reads_active_universe_via_public_getter(self, settings):
        """The strategist queries scanner.get_active_universe() — our
        accessor — to obtain the cycle's 30-coin focus."""
        from src.strategies.scanner import MarketScanner

        scanner = MarketScanner(
            settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(settings.universe.watch_list),
        )
        scanner.set_active_universe(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        u = await scanner.get_active_universe()
        # BTC/ETH always force-included by set_active_universe? No — that's
        # only done by ScannerWorker.tick. set_active_universe stores as-is.
        assert u == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    def test_strategist_call_sites_remain(self):
        """The two cycle-side reads in strategist.py:592 and :1250
        must remain — Phase 8 verified these stay under the corrected arch."""
        with open("src/brain/strategist.py") as f:
            text = f.read()
        # Two call sites.
        assert text.count("scanner.get_active_universe()") >= 2


# ── F-6: Phase 7 — rotation handlers all gone ──────────────────────────


class TestPhase7CleanupComplete:
    """Phase 7 — _on_universe_change deleted from all 7 data workers + ScannerWorker."""

    def test_zero_workers_have_on_universe_change(self):
        from src.workers.kline_worker import KlineWorker
        from src.workers.structure_worker import StructureWorker
        from src.workers.signal_worker import SignalWorker
        from src.workers.regime_worker import RegimeWorker
        from src.workers.strategy_worker import StrategyWorker
        from src.workers.altdata_worker import AltDataWorker
        from src.workers.price_worker import PriceWorker
        from src.workers.scanner_worker import ScannerWorker

        all_workers = [
            KlineWorker, StructureWorker, SignalWorker, RegimeWorker,
            StrategyWorker, AltDataWorker, PriceWorker, ScannerWorker,
        ]
        for cls in all_workers:
            assert not hasattr(cls, "_on_universe_change"), (
                f"{cls.__name__} still has _on_universe_change (Phase 7 incomplete)"
            )

    def test_manager_dispatcher_removed(self):
        """The master callback dispatcher (closure inside _create_workers)
        and scanner.subscribe(_on_universe_change) registration must be gone."""
        with open("src/workers/manager.py") as f:
            text = f.read()
        # Master dispatcher closure should be replaced with a Phase-7 comment.
        assert "Phase 7 (corrected-Layer-1)" in text
        # Specifically the call ``scanner.subscribe(_on_universe_change)`` is gone.
        assert "scanner.subscribe(_on_universe_change)" not in text


# ── F-7: Worker accessors all callable ──────────────────────────────────


class TestPhase6Accessors:
    """Phase 6 — every required accessor exists with correct signature."""

    def test_get_setup_score(self, settings, db_mock):
        from src.workers.structure_worker import StructureWorker
        from src.analysis.structure.structure_cache import StructureCache
        sw = StructureWorker(settings, db_mock, MagicMock(), StructureCache())
        assert sw.get_setup_score("UNKUSDT") is None
        # Warm one
        sw._cache.set("BTCUSDT", MagicMock(setup_score=42.5))
        assert sw.get_setup_score("BTCUSDT") == 42.5

    def test_get_signal(self, settings, db_mock):
        from src.workers.signal_worker import SignalWorker
        from src.core.types import Signal, SignalType
        sw = SignalWorker(settings, db_mock, None, MagicMock(), MagicMock())
        assert sw.get_signal("UNKUSDT") is None
        sw._signal_cache["BTCUSDT"] = Signal(
            symbol="BTCUSDT", signal_type=SignalType.BUY,
            confidence=0.95, source="t",
        )
        sig = sw.get_signal("BTCUSDT")
        assert sig is not None and sig.confidence == 0.95

    def test_get_regime(self, settings, db_mock):
        from src.workers.regime_worker import RegimeWorker
        from src.strategies.models.regime_types import RegimeState, MarketRegime
        w = RegimeWorker(settings, db_mock, MagicMock())
        w.detector = MagicMock()
        sample = RegimeState(
            regime=MarketRegime.TRENDING_UP, confidence=0.88, adx=28,
            atr_percentile=70, choppiness=35, volume_ratio=1.4,
            trend_direction=1, active_strategy_categories=[],
        )
        w.detector.get_coin_regime = MagicMock(return_value=sample)
        assert w.get_regime("BTCUSDT") is sample

    def test_get_score(self, settings, db_mock):
        from src.workers.strategy_worker import StrategyWorker
        sw = StrategyWorker(
            settings, db_mock,
            registry=MagicMock(), scanner=MagicMock(), regime_detector=MagicMock(),
            scorer=MagicMock(), ensemble=MagicMock(), pnl_manager=MagicMock(),
            ta_engine=MagicMock(), market_repo=MagicMock(), services={},
        )
        assert sw.get_score("UNKUSDT") is None
        sw._score_cache["BTCUSDT"] = 87.0
        assert sw.get_score("BTCUSDT") == 87.0

    def test_get_funding(self, settings, db_mock):
        from src.workers.altdata_worker import AltDataWorker
        w = AltDataWorker(settings, db_mock, MagicMock(), MagicMock(), MagicMock(), MagicMock())
        assert w.get_funding("UNKUSDT") is None
        w._funding_cache["BTCUSDT"] = 0.0001
        assert w.get_funding("BTCUSDT") == 0.0001


# ── F-8: Public APIs replace private mutations ─────────────────────────


class TestPublicAPIs:
    """Audit B fixes — workers use public MarketScanner / StructureCache APIs."""

    def test_scanner_public_setter(self, settings):
        from src.strategies.scanner import MarketScanner
        scanner = MarketScanner(
            settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(settings.universe.watch_list),
        )
        before_version = scanner._universe_version
        scanner.set_active_universe(["BTCUSDT", "ETHUSDT"])
        assert scanner._active_universe == ["BTCUSDT", "ETHUSDT"]
        assert scanner._universe_version == before_version + 1

    def test_scanner_public_subscribers_snapshot(self, settings):
        from src.strategies.scanner import MarketScanner
        scanner = MarketScanner(
            settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(settings.universe.watch_list),
        )
        snapshot = scanner.get_subscribers_snapshot()
        assert isinstance(snapshot, list)
        # Mutation of the returned snapshot must NOT affect the scanner.
        snapshot.append(lambda *a, **kw: None)
        assert len(scanner.get_subscribers_snapshot()) == 0

    def test_structure_cache_oldest_age(self):
        from src.analysis.structure.structure_cache import StructureCache
        c = StructureCache(ttl_seconds=300)
        assert c.get_oldest_entry_age_seconds() == 0.0
        c.set("BTCUSDT", MagicMock())
        # Age is non-negative (just set, so very small).
        assert c.get_oldest_entry_age_seconds() >= 0.0


# ── F-9: ScannerWorker tick uses batched executemany (perf) ────────────


class TestBatchedDB:
    """Audit B-3 fix — N+1 INSERTs replaced with one executemany."""

    @pytest.mark.asyncio
    async def test_scanner_uses_executemany_not_n_inserts(self, settings, db_mock):
        """B-3 contract: when scanner has rows to insert, it uses ONE
        executemany call (batch), not N execute calls.

        Q2 (2026-04-29): with BTC/ETH ref-pair force-include removed,
        an empty-cache run produces 0 rows → executemany is NOT called
        (guarded by ``if insert_rows:`` in scanner_worker.py:1234). The
        contract is "if any rows, ONE executemany" — i.e., never N
        execute calls. We assert ``executemany <= 1`` and ``no per-row
        execute INSERT`` calls.
        """
        from src.workers.scanner_worker import ScannerWorker
        from src.strategies.scanner import MarketScanner

        scanner = MarketScanner(
            settings, MagicMock(), instrument_service=MagicMock(),
            watch_list=set(settings.universe.watch_list),
        )
        sw = ScannerWorker(settings, db_mock, scanner, services={"scanner": scanner})
        await sw.tick()
        # B-3 contract: at most ONE executemany call. With 0 rows it's 0;
        # with N rows it's exactly 1.
        assert db_mock.executemany.call_count <= 1
        # Per-row INSERTs would violate B-3 — assert NONE exist.
        per_row_inserts = [
            c for c in db_mock.execute.call_args_list
            if "INSERT" in str(c).upper() and "INTO active_universe" in str(c).upper()
        ]
        assert len(per_row_inserts) == 0, (
            f"B-3 regression: per-row INSERTs detected: {len(per_row_inserts)}"
        )
        # Exactly ONE DELETE call (always runs to clear stale state).
        delete_calls = [c for c in db_mock.execute.call_args_list
                        if "DELETE" in str(c).upper()]
        assert len(delete_calls) == 1


# ── F-10: KlineWorker consolidates lag + freshness queries ─────────────


class TestKlineConsolidatedQuery:
    """Audit B-1 fix — KLINE_WRITE_LAG and KLINE_FRESHNESS_WARN share one SELECT."""

    def test_kline_tick_only_one_select_query(self):
        """Inspect kline_worker.py to verify only ONE 'FROM klines' query
        in the tick body (the consolidated lag+freshness scan)."""
        with open("src/workers/kline_worker.py") as f:
            text = f.read()
        # Count grouped SELECT queries inside tick(). Should be exactly 1.
        # Excludes the docstring mentions.
        # Count the actual SELECT...FROM klines statements.
        select_count = text.count("FROM klines")
        # 1 in the consolidated tick scan, 0 elsewhere (cleanup_worker owns retention)
        assert select_count == 1, (
            f"Expected 1 'FROM klines' in kline_worker.py, found {select_count}"
        )
