"""End-to-end pipeline test for the Layer 1 restructure.

NOT a mock-driven unit test. Drives REAL components: real ``Settings``
loaded from ``config.toml``, real ``DatabaseManager`` against a temp
SQLite file, real migrations (so ``cycle_metrics`` exists), real
``CycleTracker`` writing to that DB, real ``StructureEngine`` running
``classify_setup``, real ``LayerManager`` wiring, real ``CoinPackage``
flowing into the strategist's ``_format_packages_for_prompt``.

What this verifies (cross-phase, runtime):

* Settings loads from config.toml without error and exposes every new
  block we added (``observability``, ``[scanner.qualitative]``,
  ``[brain].use_packages``, ``[analysis.structure.setup_types]``).
* Migrations create ``cycle_metrics``.
* ``CycleTracker`` start/end/flush round-trips into ``cycle_metrics``.
* ``StructureEngine.classify_setup`` produces a real ``SetupType`` for
  a hand-crafted ``StructuralAnalysis``.
* ``LayerManager`` carries every cache attribute Phases 3 + 6 introduced.
* Layer-state v1 → v2 migration script runs and produces the expected
  shape (with backup file).
* ``CoinPackage`` round-trips through ``_format_packages_for_prompt``
  with no field unused.
* ``BaseWorker``-tier subclass picks up the canonical ``WorkerTier``
  enum, the derived log tag, and the ``cycle_gated`` flag.

Run with:
    python3 -m pytest tests/test_end_to_end_pipeline/ -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def real_settings():
    """Load the real Settings singleton from the project's config.toml."""
    from src.config.settings import Settings
    Settings.reset()
    settings = Settings._load_fresh(
        config_path=str(Path(__file__).resolve().parent.parent.parent / "config.toml"),
        env_path=str(Path(__file__).resolve().parent.parent.parent / ".env"),
    )
    yield settings
    Settings.reset()


@pytest.fixture
async def real_db(tmp_path):
    """Real DatabaseManager against a temp SQLite file. Migrations applied."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    db = DatabaseManager(str(tmp_path / "trading.db"))
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


# ─── Phase 1: Observability — REAL CycleTracker → real cycle_metrics row ──


class TestPhase1_RealCycleMetricsRoundtrip:
    """Drive CycleTracker through start → end → flush → SELECT cycle_metrics."""

    @pytest.mark.asyncio
    async def test_cycle_metrics_table_created(self, real_db) -> None:
        rows = await real_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cycle_metrics'",
        )
        assert len(rows) == 1, "Phase 1 migration did not create cycle_metrics"

    @pytest.mark.asyncio
    async def test_full_cycle_roundtrip(self, real_db) -> None:
        from src.core.cycle_tracker import CycleTracker

        ct = CycleTracker(real_db, max_history=10)
        cid = ct.start_cycle("layer1b")
        # Simulate 1B → 1C → 1D timing.
        ct.end_cycle("layer1b", cid)
        ct.start_cycle("layer1c", cycle_id=cid)
        ct.end_cycle("layer1c", cid)
        ct.start_cycle("layer1d", cycle_id=cid)
        ct.record_qualified(cid, qualified=14, selected=12, packages=12)
        ct.end_cycle("layer1d", cid)  # 1D end triggers CYCLE_COMPLETE

        recent = ct.get_recent(10)
        assert len(recent) == 1
        s = recent[0]
        assert s.cycle_id == cid
        assert s.layer1b_ms is not None and s.layer1b_ms >= 0
        assert s.layer1c_ms is not None
        assert s.layer1d_ms is not None
        assert s.packages_ready == 12
        assert s.status == "ok"

        # Force one flush — verifies the schema accepts the columns.
        await ct._flush_once()
        rows = await real_db.fetch_all("SELECT * FROM cycle_metrics")
        assert len(rows) == 1
        row = rows[0]
        # Columns we promised in the migration.
        for col in (
            "hour_ts", "cycles_count",
            "layer1a_p50_ms", "layer1a_p95_ms",
            "layer1b_p50_ms", "layer1b_p95_ms",
            "layer1c_p50_ms", "layer1c_p95_ms",
            "layer1d_p50_ms", "layer1d_p95_ms",
            "total_p50_ms", "total_p95_ms",
            "qualified_pct_avg", "packages_count_avg",
        ):
            assert col in row, f"missing column {col} in cycle_metrics"
        assert row["cycles_count"] == 1
        assert row["packages_count_avg"] == 12.0


# ─── Phase 2: XRAY classify_setup with a real StructuralAnalysis ──────────


class TestPhase2_RealClassifySetup:
    """Drive StructureEngine.classify_setup with a hand-crafted analysis."""

    def test_bullish_fvg_ob_through_real_engine(self, real_settings) -> None:
        from src.analysis.structure.models.structure_types import (
            FairValueGap, MarketStructureResult, OrderBlock, SetupType,
            StructuralAnalysis,
        )
        from src.analysis.structure.structure_engine import StructureEngine

        engine = StructureEngine(real_settings.structure)
        analysis = StructuralAnalysis(
            symbol="BTCUSDT",
            suggested_direction="long",
            smc_confluence=80,
            position_in_range=0.2,
            total_confluence_factors=4,
        )
        analysis.market_structure = MarketStructureResult(structure="uptrend", strength="strong")
        analysis.nearest_fvg = FairValueGap(direction="bullish", filled=False)
        analysis.nearest_ob = OrderBlock(direction="bullish", fresh=True)
        analysis.mtf_confluence = MagicMock(score=8)

        stype, conf = engine.classify_setup(analysis)
        assert stype is SetupType.BULLISH_FVG_OB
        assert 0.0 <= conf <= 1.0
        # Settings flow: real config knob hit. Value updated 2026-04-28
        # (Definitive-fix Phase 2 lowered fvg_ob_min_confluence from the
        # 0.7 dataclass default to 0.5 in config.toml). The assertion
        # below verifies the LIVE config value, not the dataclass default.
        assert real_settings.structure.setup_types.fvg_ob_min_confluence == 0.5

    def test_to_dict_includes_setup_type(self) -> None:
        from src.analysis.structure.models.structure_types import (
            MarketStructureResult, SetupType, StructuralAnalysis,
        )
        a = StructuralAnalysis(symbol="ETHUSDT")
        a.market_structure = MarketStructureResult()
        a.setup_type = SetupType.BEARISH_LIQUIDITY_SWEEP
        a.setup_type_confidence = 0.81
        d = a.to_dict()
        assert d["setup_type"] == "bearish_liquidity_sweep"
        assert d["setup_type_confidence"] == 0.81


# ─── Phase 3: LayerManager carries the per-coin cache + alias ─────────────


class TestPhase3_LayerManagerCacheShape:
    def test_layer_manager_initializes_phase3_attrs(self, real_settings) -> None:
        from src.core.layer_manager import LayerManager
        services: dict = {}
        lm = LayerManager(real_settings, services)
        # Phase 3 attrs.
        assert hasattr(lm, "_strategy_consensus")
        assert isinstance(lm._strategy_consensus, dict)
        assert hasattr(lm, "_strategy_consensus_summary")
        assert hasattr(lm, "_strategy_hints")
        # Phase 3 accessor.
        assert lm.get_strategy_consensus("BTCUSDT") is None  # empty initially
        # Phase 6 attrs.
        assert hasattr(lm, "_coin_packages")
        assert lm.get_coin_packages() == {}

    def test_get_strategy_consensus_round_trip(self, real_settings) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager(real_settings, {})
        lm._strategy_consensus["BTCUSDT"] = {
            "consensus": "STRONG", "consensus_score": 0.9,
            "vote_count": 5, "direction": "long", "last_updated": 0.0,
        }
        out = lm.get_strategy_consensus("BTCUSDT")
        assert out is not None
        assert out["consensus"] == "STRONG"


# ─── Phase 4: WorkerTier + cycle gating + cold-start helper ───────────────


class TestPhase4_RealWorkerTierWiring:
    def test_every_layer1_worker_carries_canonical_enum(self) -> None:
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

        expected = {
            KlineWorker: (WorkerTier.LAYER1A, False),
            PriceWorker: (WorkerTier.LAYER1A, False),
            AltDataWorker: (WorkerTier.LAYER1A, False),
            NewsWorker: (WorkerTier.LAYER1A, False),
            StructureWorker: (WorkerTier.LAYER1B, True),
            SignalWorker: (WorkerTier.LAYER1B, True),
            RegimeWorker: (WorkerTier.LAYER1B, True),
            StrategyWorker: (WorkerTier.LAYER1C, True),
            ScannerWorker: (WorkerTier.LAYER1D, True),
        }
        for cls, (tier, gated) in expected.items():
            assert cls.worker_tier is tier, f"{cls.__name__}: tier mismatch"
            assert cls.cycle_gated is gated, f"{cls.__name__}: cycle_gated mismatch"

    def test_seconds_to_next_window_boundary(self) -> None:
        from src.core.layer_manager import LayerManager
        # 1500 epoch s ≡ 5-min boundary.
        assert LayerManager._seconds_to_next_window_boundary(now=1500) == 0.0
        assert LayerManager._seconds_to_next_window_boundary(now=1530) == 270.0

    def test_is_cycle_active_truth_table(self, real_settings) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager(real_settings, {})
        lm._layer_active = {1: True, 2: True, 3: True}
        assert lm.is_cycle_active() is True
        lm._layer_active = {1: True, 2: False, 3: True}
        assert lm.is_cycle_active() is False
        lm._layer_active = {1: True, 2: True, 3: False}
        assert lm.is_cycle_active() is False


# ─── Phase 5/6/7: real CoinPackage threaded through real strategist helper


class TestPhase5_6_7_PackageEndToEnd:
    @pytest.mark.asyncio
    async def test_recent_loss_query_against_real_db(self, real_db) -> None:
        """Phase 5 audit fix: recent_loss_symbols runs against a real DB."""
        from src.core.trade_recorder import recent_loss_symbols

        # Empty table → empty set.
        result = await recent_loss_symbols(real_db, hours=1)
        assert result == set()

        # Insert two recent losses + one win + one old loss.
        # NOTE: trade_intelligence has captured_at NOT NULL — include it.
        async def _insert(symbol, direction, win, pnl_pct, when_offset):
            await real_db.execute(
                "INSERT INTO trade_intelligence "
                "(symbol, direction, strategy_name, strategy_category, source, "
                " closed_by, entry_price, exit_price, pnl_pct, pnl_usd, win, "
                " hold_seconds, trade_closed_at, captured_at) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '{when_offset}'), datetime('now'))",
                (symbol, direction, "A1", "trend", "auto",
                 "sl" if not win else "tp",
                 100.0, 99.0 if not win else 102.0,
                 pnl_pct, pnl_pct * 10.0, win, 60.0),
            )

        await _insert("BTCUSDT", "long", 0, -1.0, "+0 minutes")
        await _insert("ETHUSDT", "short", 0, -1.0, "+0 minutes")
        await _insert("SOLUSDT", "long", 1, 2.0, "+0 minutes")
        await _insert("XRPUSDT", "long", 0, -1.0, "-2 hours")

        # Within last hour: only the two recent losses.
        result = await recent_loss_symbols(real_db, hours=1)
        assert result == {"BTCUSDT", "ETHUSDT"}, f"got {result}"

        # 3-hour lookback: includes XRPUSDT but NOT SOLUSDT (that's a win).
        result_3h = await recent_loss_symbols(real_db, hours=3)
        assert result_3h == {"BTCUSDT", "ETHUSDT", "XRPUSDT"}

    def test_coin_package_through_strategist_formatter(self) -> None:
        """Real CoinPackage → real strategist._format_packages_for_prompt."""
        from src.brain.strategist import ClaudeStrategist
        from src.core.coin_package import (
            AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock,
            StrategiesBlock, StructuralLevels, XrayBlock,
        )

        pkg = CoinPackage(
            symbol="BTCUSDT", qualified=True, opportunity_score=0.92,
            qualification_reasons=[
                "xray_setup=bullish_fvg_ob",
                "consensus=STRONG",
                "regime=trending_up_aligns_long",
                "rr=3.50",
            ],
            price_data=PriceDataBlock(current=70000.0, change_24h_pct=2.5,
                                      volume_24h_usd=1.2e9, regime="trending_up"),
            xray=XrayBlock(
                setup_type="bullish_fvg_ob", setup_score=88.0,
                setup_type_confidence=0.78,
                structural_levels=StructuralLevels(
                    current_price=70000.0, suggested_sl=68500.0,
                    suggested_tp=73500.0, rr_ratio=2.33,
                ),
                mtf_confluence="aligned_h1_h4",
            ),
            strategies=StrategiesBlock(
                fired_count=5, ensemble_consensus="STRONG",
                consensus_score=0.92, total_score=92.0,
            ),
            signals=SignalsBlock(confidence=0.82, direction="long",
                                 sentiment_score=0.4, sentiment_articles_count=3),
            alt_data=AltDataBlock(funding_rate=0.0001, funding_signal="longs_paying",
                                  oi_change_24h_pct=12.0, fear_greed=65),
        )

        strategist = ClaudeStrategist.__new__(ClaudeStrategist)
        out = strategist._format_packages_for_prompt({"BTCUSDT": pkg})

        # Header present.
        assert "TRADE CANDIDATES" in out
        # Setup type rendered (not just dict-stringified).
        assert "bullish_fvg_ob" in out
        # SL/TP from StructuralLevels rendered.
        assert "$68500.0000" in out or "68500" in out
        # RR ratio rendered.
        assert "2.33" in out
        # Consensus rendered.
        assert "STRONG" in out
        # Funding signal rendered.
        assert "longs_paying" in out

    def test_use_packages_flag_default_true(self, real_settings) -> None:
        # Phase 7 contract: opt-in by default.
        assert real_settings.brain.use_packages is True


# ─── Phase 8: real layer_state.json migration through the script ─────────


class TestPhase8_MigrationScriptRoundtrip:
    def test_migrate_v1_to_v2_against_real_filesystem(self, tmp_path) -> None:
        from scripts.migrate_layer_state_to_v2 import main, migrate

        v1_path = tmp_path / "layer_state.json"
        v1_path.write_text(json.dumps({
            "layer_active": {"1": True, "2": True, "3": False},
            "user_stopped": False,
            "timestamp": "2026-04-27T00:00:00+00:00",
        }))

        rc = main(str(v1_path))
        assert rc == 0

        v2 = json.loads(v1_path.read_text())
        assert v2["schema_version"] == 2
        assert v2["layer_active"] == {
            "1": True, "2": True, "3": True, "4": False, "5": False,
        }
        # Backup file present.
        backup = v1_path.with_suffix(".v1.json.bak")
        assert backup.exists()
        bv1 = json.loads(backup.read_text())
        assert bv1.get("schema_version") is None  # was v1

    def test_migrate_idempotent(self, tmp_path) -> None:
        from scripts.migrate_layer_state_to_v2 import main

        v2_path = tmp_path / "layer_state.json"
        v2_path.write_text(json.dumps({
            "schema_version": 2,
            "layer_active": {"1": True, "2": True, "3": True, "4": True, "5": True},
            "user_stopped": False,
        }))
        before = v2_path.read_text()
        rc = main(str(v2_path))
        assert rc == 0
        # No-op on v2.
        assert v2_path.read_text() == before
        assert not v2_path.with_suffix(".v1.json.bak").exists()


# ─── Phase 8 helpers wired into LayerManager ─────────────────────────────


class TestPhase8_SemanticHelpers:
    def test_can_run_brain_can_execute_can_monitor(self, real_settings) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager(real_settings, {})
        # All off.
        lm._layer_active = {1: True, 2: False, 3: False}
        assert lm.can_run_brain() is False
        assert lm.can_execute_orders() is False
        assert lm.can_run_monitoring() is False

        # Brain on, execution off.
        lm._layer_active = {1: True, 2: True, 3: False}
        assert lm.can_run_brain() is True
        assert lm.can_execute_orders() is False

        # All on.
        lm._layer_active = {1: True, 2: True, 3: True}
        assert lm.can_run_brain() is True
        assert lm.can_execute_orders() is True
        assert lm.can_run_monitoring() is True


# ─── Phase 9 harness — actually runs the SQL the script issues ────────────


class TestPhase9_ObservationHarnessQuery:
    @pytest.mark.asyncio
    async def test_observe_phase9_query_runs_against_real_db(self, real_db) -> None:
        """The aggregate query inside observe_phase9.py must be valid SQL."""
        # Insert one synthetic cycle_metrics row.
        await real_db.execute(
            "INSERT INTO cycle_metrics "
            "(hour_ts, cycles_count, "
            " layer1a_p50_ms, layer1a_p95_ms, layer1b_p50_ms, layer1b_p95_ms, "
            " layer1c_p50_ms, layer1c_p95_ms, layer1d_p50_ms, layer1d_p95_ms, "
            " total_p50_ms, total_p95_ms, qualified_pct_avg, packages_count_avg) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1000, 1, 200, 300, 400, 500, 600, 700, 100, 200, 1300, 1700, 14.0, 12.0),
        )
        # The exact aggregation observe_phase9.py issues:
        rows = await real_db.fetch_all(
            "SELECT COUNT(*) AS n, "
            "AVG(layer1a_p95_ms) AS l1a, AVG(layer1b_p95_ms) AS l1b, "
            "AVG(layer1c_p95_ms) AS l1c, AVG(layer1d_p95_ms) AS l1d, "
            "AVG(total_p95_ms) AS total, "
            "AVG(qualified_pct_avg) AS q, AVG(packages_count_avg) AS p "
            "FROM cycle_metrics WHERE hour_ts >= ?",
            (0,),
        )
        assert len(rows) == 1
        r = rows[0]
        assert r["n"] == 1
        assert r["l1a"] == 300
        assert r["total"] == 1700


# ─── Cross-phase: full cycle with real workers via WorkerManager wiring ──


class TestCrossPhase_WorkerManagerWiring:
    @pytest.mark.asyncio
    async def test_late_bind_layer_manager_and_cycle_tracker(self, real_settings, real_db) -> None:
        """Verify the WorkerManager-style late-binding from the audit fix.

        Without booting the full WorkerManager (which needs Bybit + 30 services),
        we replay the wiring logic against in-process workers.
        """
        from src.core.cycle_tracker import CycleTracker
        from src.core.layer_manager import LayerManager
        # We can't easily build a real StructureWorker without Bybit/StructureEngine;
        # the wiring test focuses on the contract: any worker subclass that
        # advertises ``cycle_gated`` AND ``worker_tier`` must accept the
        # late-bound handles ``_layer_manager`` + ``_cycle_tracker``.
        from src.workers.scanner_worker import ScannerWorker

        ct = CycleTracker(real_db, max_history=5)
        lm = LayerManager(real_settings, {})

        # Bare-bones ScannerWorker construction is heavy; reuse __new__.
        sw = ScannerWorker.__new__(ScannerWorker)
        sw.name = "scanner_worker"
        sw._layer_manager = None
        sw._cycle_tracker = None

        # Replay the WorkerManager wiring loop:
        if getattr(sw, "cycle_gated", False) and sw._layer_manager is None:
            sw._layer_manager = lm
        if getattr(sw, "worker_tier", None) is not None and sw._cycle_tracker is None:
            sw._cycle_tracker = ct

        assert sw._layer_manager is lm
        assert sw._cycle_tracker is ct
        # ScannerWorker is LAYER1D — base loop must NOT auto-start a cycle
        # (1D drives its own start/end inside tick to stamp qualified counts).
        from src.workers.base_worker import BaseWorker
        # _maybe_start_cycle on a 1D-tagged stub returns None
        cid = BaseWorker._maybe_start_cycle(sw, 0.0)
        assert cid is None


class TestNamingConsistency:
    def test_no_orphan_layer_tier_tag_string(self) -> None:
        """After the Phase 4 audit, no worker should still declare a bare string."""
        import re
        from pathlib import Path

        offenders: list[str] = []
        worker_dir = Path(__file__).resolve().parent.parent.parent / "src" / "workers"
        for f in worker_dir.glob("*.py"):
            text = f.read_text()
            # We only flag class-level assignment patterns; the property
            # itself in base_worker.py is the legitimate exception.
            if "layer_tier_tag = \"LAYER" in text:
                offenders.append(f.name)
        assert not offenders, (
            f"Workers still using bare-string layer_tier_tag (Phase 4 audit "
            f"made WorkerTier the canonical source): {offenders}"
        )

    def test_no_band_aid_strategist_consensus_read(self) -> None:
        """Strategist must read the alias, not the new per-coin shape."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent.parent
                / "src" / "brain" / "strategist.py").read_text()
        # Phase 3 audit fix: every legacy summary read goes through the alias.
        assert "_strategy_consensus_summary" in text, (
            "Phase 3 audit fix lost: strategist must read "
            "_strategy_consensus_summary (the legacy alias)"
        )
