"""Definitive-fix series — real-project end-to-end pipeline verification.

Each test exercises one of the twelve fixes through the production wiring:
real ``Settings.load()`` from ``config.toml``, real classes constructed
end-to-end, no mocked-out behaviour where the production behaviour is
the contract under test. External I/O (exchange, Reddit, OpenRouter,
Claude CLI) is mocked at the boundary, but every internal cache,
service registry entry, dataclass, and method call is the real one.

Pipelines covered, in the order they fire on a real cycle:

  Phase 1  — StructureCache freshness contract under full-sweep tick.
  Phase 2  — classify_setup honours the 0.5 fvg_ob_min threshold from
             config.toml.
  Phase 3  — ranging-market alignment broadens with mtf >= 0.55.
  Phase 4  — ScannerWorker direction-aware RR + 6-component composite
             score with weights summing to 1.0 from config.
  Phase 5  — SignalGenerator default-construction picks up the new
             buy_threshold / *_min_active calibration.
  Phase 6  — LayerManager cold-start gate end-to-end with the real
             config.toml [brain.cold_start_protection] block.
  Phase 7  — RegimeDetector.is_ready transitions correctly; the three
             consumer call sites (apex/gate, apex/assembler,
             tias/collector) all exist as imports.
  Phase 8  — ThesisManager close_thesis honours order_id end-to-end
             through the schema the production code stores.
  Phase 9  — APEX TradeOptimizer flip-confidence gate is wired to
             the apex_min_flip_confidence value from config.
  Phase 10 — ProfitSniper.max_partials_per_position from config.toml.
  Phase 12 — EnsembleVoter cap + vote-trace toggles read from
             real settings.strategy_engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from src.config.settings import Settings


# =====================================================================
# Module-scope fixture: real Settings loaded from production config.toml.
# =====================================================================
@pytest.fixture(scope="module")
def real_settings() -> Settings:
    """Load real ``Settings`` from the project's ``config.toml``.

    Uses ``_load_fresh`` to bypass the singleton so we can re-load
    without cross-test contamination. This is the same code path
    ``WorkerManager`` uses on system startup.
    """
    return Settings._load_fresh(config_path="config.toml")


# =====================================================================
# Phase 1 — StructureCache freshness contract.
# =====================================================================
class TestPhase1CacheFreshness:
    def test_batch_size_50_in_real_settings(self, real_settings: Settings) -> None:
        """config.toml drives StructureSettings.batch_size = 50."""
        assert real_settings.structure.batch_size == 50

    def test_full_sweep_per_tick_contract(self, real_settings: Settings) -> None:
        """With batch_size >= |watch_list|, one tick covers the whole universe."""
        from src.analysis.structure.structure_cache import StructureCache

        cache = StructureCache(ttl_seconds=300.0)
        # Stand-in analysis stub — cache only stores the value.
        for sym in real_settings.universe.watch_list:
            cache.set(sym, MagicMock(setup_score=50.0))

        breakdown = cache.get_freshness_breakdown()
        # Every coin entered the cache within this scope, so all are fresh.
        assert breakdown["total"] == len(real_settings.universe.watch_list)
        assert breakdown["fresh"] == breakdown["total"]
        assert breakdown["stale"] == 0


# =====================================================================
# Phase 2 + 3 — classify_setup honours config thresholds, alignment
# broadens with ranging.
# =====================================================================
class TestPhase2And3StructureClassifier:
    def test_real_settings_have_lowered_threshold_and_new_key(
        self, real_settings: Settings,
    ) -> None:
        st = real_settings.structure.setup_types
        assert st.fvg_ob_min_confluence == 0.5
        assert st.ranging_market_mtf_threshold == 0.55

    def test_classify_setup_passes_at_calibrated_threshold(
        self, real_settings: Settings,
    ) -> None:
        from src.analysis.structure.models.structure_types import (
            FairValueGap, MarketStructureResult, OrderBlock, SetupType,
            StructuralAnalysis,
        )
        from src.analysis.structure.structure_engine import StructureEngine

        eng = StructureEngine.__new__(StructureEngine)
        eng._settings = real_settings.structure

        a = StructuralAnalysis.__new__(StructuralAnalysis)
        a.suggested_direction = "long"
        a.market_structure = MarketStructureResult.__new__(MarketStructureResult)
        a.market_structure.structure = "uptrend"
        a.market_structure.last_bos = None
        a.nearest_fvg = FairValueGap.__new__(FairValueGap)
        a.nearest_fvg.direction = "bullish"
        a.nearest_fvg.filled = False
        a.nearest_ob = OrderBlock.__new__(OrderBlock)
        a.nearest_ob.direction = "bullish"
        a.nearest_ob.fresh = True
        a.active_sweep_signal = None
        # mtf=0.55 (rejected at old 0.7, passes at new 0.5).
        a.mtf_confluence = type("M", (), {"score": 5.5})()
        a.smc_confluence = 60.0
        a.position_in_range = 0.5
        a.total_confluence_factors = 5

        setup_type, _ = eng.classify_setup(a)
        assert setup_type == SetupType.BULLISH_FVG_OB

    def test_classify_setup_in_ranging_with_high_mtf(
        self, real_settings: Settings,
    ) -> None:
        """ranging+long+mtf=0.65 → BULLISH_FVG_OB (previously NONE)."""
        from src.analysis.structure.models.structure_types import (
            FairValueGap, MarketStructureResult, OrderBlock, SetupType,
            StructuralAnalysis,
        )
        from src.analysis.structure.structure_engine import StructureEngine

        eng = StructureEngine.__new__(StructureEngine)
        eng._settings = real_settings.structure

        a = StructuralAnalysis.__new__(StructuralAnalysis)
        a.suggested_direction = "long"
        a.market_structure = MarketStructureResult.__new__(MarketStructureResult)
        a.market_structure.structure = "ranging"
        a.market_structure.last_bos = None
        a.nearest_fvg = FairValueGap.__new__(FairValueGap)
        a.nearest_fvg.direction = "bullish"
        a.nearest_fvg.filled = False
        a.nearest_ob = OrderBlock.__new__(OrderBlock)
        a.nearest_ob.direction = "bullish"
        a.nearest_ob.fresh = True
        a.active_sweep_signal = None
        a.mtf_confluence = type("M", (), {"score": 6.5})()
        a.smc_confluence = 60.0
        a.position_in_range = 0.5
        a.total_confluence_factors = 5

        setup_type, _ = eng.classify_setup(a)
        assert setup_type == SetupType.BULLISH_FVG_OB


# =====================================================================
# Phase 4 — Scanner direction-aware RR + 6-component composite.
# =====================================================================
class TestPhase4ScannerRR:
    def test_scoring_weights_six_components_sum_to_one(
        self, real_settings: Settings,
    ) -> None:
        w = real_settings.scanner.scoring_weights
        total = w.structure + w.strategy + w.signal + w.regime + w.funding + w.rr
        assert abs(total - 1.0) < 1e-9, f"weights sum {total} != 1.0"
        assert w.rr == 0.10

    def test_min_rr_ratio_is_calibrated(self, real_settings: Settings) -> None:
        # Tuned 2026-04-29 from 1.3 → 1.1 based on live observation:
        # 1.3 was the dominant cut (14 of 16 consensus-passers fail per
        # cycle in Asian/ranging markets), keeping qualified=2-3 vs
        # blueprint §10.2 target band of 5-25. 1.1 keeps the breakeven
        # quality floor (1:1 reward-to-risk) while letting more setups
        # through. See config.toml:441 for the inline rationale.
        assert real_settings.scanner.qualitative.min_rr_ratio == 1.1

    def test_directional_rr_reads_correct_field(
        self, real_settings: Settings,
    ) -> None:
        from src.workers.scanner_worker import ScannerWorker

        sw = ScannerWorker.__new__(ScannerWorker)
        sw.settings = real_settings

        # Build a structure stub with asymmetric rr_long / rr_short.
        structure_stub = MagicMock()
        structure_stub.structural_placement = MagicMock(
            rr_long=2.5, rr_short=0.8, rr_ratio=2.5,
        )
        cache = MagicMock(); cache.get.return_value = structure_stub
        structure_worker = MagicMock(); structure_worker._cache = cache
        layer_manager = MagicMock()
        layer_manager.get_strategy_consensus.return_value = {"direction": "short"}
        sw.services = {
            "structure_worker": structure_worker,
            "layer_manager": layer_manager,
        }
        # Short consensus → must read rr_short (0.8), NOT rr_best (2.5).
        rr = sw._get_directional_rr("BTCUSDT")
        assert rr == 0.8


# =====================================================================
# Phase 5 — Signal calibration via real Settings.
# =====================================================================
class TestPhase5SignalCalibration:
    def test_real_settings_have_calibrated_thresholds(
        self, real_settings: Settings,
    ) -> None:
        ms = real_settings.signal_generator.multi_source
        assert ms.buy_threshold == 0.18
        assert ms.funding_min_active == 0.10
        assert ms.oi_min_active == 0.10

    def test_default_constructed_settings_match_real_settings(self) -> None:
        """Dataclass defaults align with config.toml so a no-TOML construction
        does not silently revert to the pre-fix conservative values."""
        from src.config.settings import SignalGeneratorMultiSourceSettings
        s = SignalGeneratorMultiSourceSettings()
        assert s.buy_threshold == 0.18
        assert s.funding_min_active == 0.10
        assert s.oi_min_active == 0.10


# =====================================================================
# Phase 6 — LayerManager cold-start gate end-to-end.
# =====================================================================
class TestPhase6ColdStartGate:
    def test_real_settings_carry_cold_start_protection(
        self, real_settings: Settings,
    ) -> None:
        cs = real_settings.brain.cold_start_protection
        assert cs.enabled is True
        assert cs.boot_grace_period_sec == 600
        # Issue E12 (2026-05-27) relaxed these so honest lower completeness
        # scores cannot block the new-trade batch (0.95->0.80, 0.85->0.70).
        assert cs.boot_grace_completeness == 0.80
        assert cs.min_avg_completeness == 0.70

    def test_gate_blocks_cold_start_packages(
        self, real_settings: Settings,
    ) -> None:
        import time
        from src.core.layer_manager import LayerManager

        lm = LayerManager.__new__(LayerManager)
        lm.services = {}
        lm.settings = real_settings
        lm._coin_packages = {
            "BTCUSDT": MagicMock(completeness=0.67),
            "ETHUSDT": MagicMock(completeness=0.70),
        }
        lm._boot_time = time.time() - 60.0  # 1 min after boot — in grace
        plan = MagicMock(new_trades=[1, 2, 3])
        msg = lm._cold_start_block_or_none(plan)
        assert msg is not None
        assert "BRAIN_COLD_START_BLOCK" in msg
        # Issue E12: boot-grace threshold relaxed to 0.80 (avg 0.685 < 0.80
        # still blocks, demonstrating the gate works at the new threshold).
        assert "0.80" in msg  # boot-grace threshold reported in the log


# =====================================================================
# Phase 7 — Regime cache cold-start.
# =====================================================================
class TestPhase7RegimeWarmup:
    def test_is_ready_transitions(self) -> None:
        from src.strategies.regime import RegimeDetector
        # Bypass __init__ — only the cache state matters for is_ready.
        d = RegimeDetector.__new__(RegimeDetector)
        d._per_coin_regimes = {}
        assert d.is_ready() is False
        d._per_coin_regimes = {"BTCUSDT": MagicMock()}
        assert d.is_ready() is True

    def test_three_consumer_sites_import_cleanly(self) -> None:
        # Must import without error so the REGIME_CACHE_QUERY emit sites exist.
        import src.apex.gate
        import src.apex.assembler
        import src.tias.collector
        # Smoke: the log tag string is present in the module sources.
        assert "REGIME_CACHE_QUERY" in (
            open("src/apex/gate.py").read()
            + open("src/apex/assembler.py").read()
            + open("src/tias/collector.py").read()
        )


# =====================================================================
# Phase 8 — ThesisManager order_id end-to-end (real schema, real query).
# =====================================================================
class TestPhase8ThesisOrderId:
    @pytest.mark.asyncio
    async def test_close_with_order_id_scopes_to_matching_row(self) -> None:
        import sqlite3
        from src.core.thesis_manager import ThesisManager

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Production schema (relevant columns).
        conn.executescript("""
            CREATE TABLE trade_thesis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, direction TEXT,
                entry_price REAL, stop_loss_price REAL, take_profit_price REAL,
                size_usd REAL, leverage INTEGER, max_hold_minutes INTEGER,
                trailing_activation_pct REAL,
                thesis TEXT, market_context TEXT, strategy_hints TEXT,
                consensus TEXT, status TEXT DEFAULT 'open',
                order_id TEXT DEFAULT '',
                exchange_mode TEXT DEFAULT 'shadow',
                apex_flipped INTEGER DEFAULT 0,
                apex_original_direction TEXT DEFAULT '',
                apex_reason TEXT DEFAULT '',
                -- Time-Decay Force-Close Definitive Fix Phase 3 (2026-05-06) v27
                entry_xray_confidence REAL NOT NULL DEFAULT 0.0,
                entry_setup_type TEXT NOT NULL DEFAULT '',
                entry_regime_at_open TEXT NOT NULL DEFAULT '',
                entry_regime_confidence REAL NOT NULL DEFAULT 0.0,
                -- CALL_B Framing Fix Phase 1E (2026-05-06) v28 — XRAY flip metadata
                xray_flip_source TEXT NOT NULL DEFAULT '',
                xray_flip_ratio REAL NOT NULL DEFAULT 0.0,
                xray_flip_rr_long REAL NOT NULL DEFAULT 0.0,
                xray_flip_rr_short REAL NOT NULL DEFAULT 0.0,
                -- Mid-Hold Trade Management Fix Phase 3.1 (2026-05-19) v34
                thesis_invalidation TEXT NOT NULL DEFAULT '',
                thesis_source TEXT NOT NULL DEFAULT 'brain_stated',
                thesis_snapshot TEXT NOT NULL DEFAULT '{}',
                thesis_state TEXT NOT NULL DEFAULT 'VALID',
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP, close_price REAL,
                actual_pnl_pct REAL, actual_pnl_usd REAL,
                close_reason TEXT, lesson TEXT
            )
        """)

        class _DB:
            async def execute(self, sql: str, params: tuple = ()) -> None:
                conn.execute(sql, params)
                conn.commit()
            async def fetch_all(self, sql: str, params: tuple = ()):
                cur = conn.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]

        mgr = ThesisManager(_DB())
        await mgr.save_thesis(
            symbol="ETHUSDT", direction="Buy", entry_price=2000.0,
            stop_loss_price=1900.0, take_profit_price=2200.0,
            size_usd=200.0, leverage=3, max_hold_minutes=30,
            trailing_activation_pct=0.5, thesis="A", order_id="ORDER-A",
        )
        await mgr.save_thesis(
            symbol="ETHUSDT", direction="Sell", entry_price=2050.0,
            stop_loss_price=2150.0, take_profit_price=1850.0,
            size_usd=200.0, leverage=3, max_hold_minutes=30,
            trailing_activation_pct=0.5, thesis="B", order_id="ORDER-B",
        )
        # Close ONLY ORDER-A.
        await mgr.close_thesis(
            symbol="ETHUSDT", close_price=2100.0,
            actual_pnl_pct=5.0, actual_pnl_usd=10.0,
            close_reason="tp_hit", order_id="ORDER-A",
        )
        rows = await mgr.get_open_theses()
        assert len(rows) == 1
        assert rows[0]["direction"] == "Sell"


# =====================================================================
# Phase 9 — APEX flip discipline end-to-end.
# =====================================================================
class TestPhase9APEXFlipDiscipline:
    def test_real_settings_carry_flip_thresholds(
        self, real_settings: Settings,
    ) -> None:
        # Phase 3 of dir-block-fix (2026-05-05) lowered the floor from
        # 0.90 → 0.70 and added the RR-weighted boost knobs. The
        # apex_block_flip_resize semantic was narrowed to "block flip-driven
        # UPSIZE only" by Post-Execution Closure Fix Phase 2 (commit
        # 0795aca) but the field name and default stay True.
        a = real_settings.apex
        assert a.apex_min_flip_confidence == 0.70
        assert a.apex_block_flip_resize is True
        assert a.apex_flip_rr_boost_threshold == 3.0
        assert a.apex_flip_rr_boost_amount == 0.15

    def test_low_confidence_flip_in_ranging_blocked(
        self, real_settings: Settings,
    ) -> None:
        from src.apex.optimizer import TradeOptimizer

        opt = TradeOptimizer.__new__(TradeOptimizer)
        opt._settings = real_settings.apex

        @dataclass
        class _Opt:
            direction: str = "Buy"
            # Phase 3 of dir-block-fix (2026-05-05): with the floor at
            # 0.70 the previous 0.70-confidence test sample now passes
            # the gate. Use 0.55 < 0.70 so the block path still fires.
            confidence: float = 0.55
            was_flipped: bool = True
            position_size_usd: float = 600.0
            original_size: float = 600.0

        revert, reason = opt._enforce_flip_confidence(_Opt(), "Sell", "ranging")
        assert revert is True
        assert "0.55" in reason and "0.70" in reason


# =====================================================================
# Phase 10 — ProfitSniper M4 partial cap.
# =====================================================================
class TestPhase10ProfitSniperPartialCap:
    # Layer 4 Realignment Phase 1D (2026-05-06) raised the cap default
    # from 1 to 3 so positions get multiple recovery attempts before
    # the forced full close fires. The first assertion was updated to
    # the new default; the behavioural test was adjusted to set cap=1
    # explicitly so the cap-reached "first partial then full" pattern
    # remains exercised.
    def test_real_settings_set_max_partials_to_three(
        self, real_settings: Settings,
    ) -> None:
        assert real_settings.mode4.max_partials_per_position == 3

    def test_first_partial_then_full_close_at_cap(
        self, real_settings: Settings,
    ) -> None:
        from src.workers.profit_sniper import ProfitSniper

        sw = ProfitSniper.__new__(ProfitSniper)
        # Tighten timing so the test runs fast. Override cap=1
        # explicitly because Phase 1D's new default of 3 would
        # otherwise change the expected emission sequence — this
        # behavioural test stays focused on the cap-reached path.
        cfg = real_settings.mode4
        cfg.stall_escape_partial_after_ticks = 1
        cfg.stall_escape_full_after_ticks = 9999
        cfg.stall_escape_cooldown_seconds = 0
        cfg.max_partials_per_position = 1
        # Sniper-Latency-Size Fix Phase 1 (2026-05-07) — disable the
        # tick-based grace gate so this rapid-fire test that emits
        # multiple escapes within one loop iteration continues to test
        # the cap-reached path. The grace gate has its own tests in
        # tests/test_layer4_sniper/test_grace_gap.py.
        cfg.partial_to_partial_grace_ticks = 0
        cfg.partial_to_full_grace_ticks = 0
        sw.settings = MagicMock(); sw.settings.mode4 = cfg

        tracked = {"_partials_emitted": 0, "last_score": {"pnl_pct": -0.5}}
        emissions: list[str] = []
        for _ in range(10):
            a = sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
            if a is not None:
                emissions.append(a)

        assert emissions[0] == "partial_close"
        assert emissions[1] == "full_close"
        assert tracked["_partials_emitted"] == 1


# =====================================================================
# Phase 12 — EnsembleVoter cap + vote-trace via real settings.
# =====================================================================
class TestPhase12EnsembleVoter:
    def test_real_settings_carry_vote_trace_and_cap(
        self, real_settings: Settings,
    ) -> None:
        se = real_settings.strategy_engine
        assert se.vote_trace_enabled is True
        # E28 (2026-05-28): the dominance cap is now ENABLED at 0.4 (completes
        # #19) so a STRONG consensus requires breadth, not one loud strategy.
        # This test verifies the value is carried from config.toml.
        assert se.single_strategy_max_share == 0.4


# =====================================================================
# Cross-cutting: Settings constructs end-to-end and every section is
# present.
# =====================================================================
class TestSettingsLoadsAllSections:
    def test_every_definitive_fix_section_present(
        self, real_settings: Settings,
    ) -> None:
        # Phase 1
        assert hasattr(real_settings.structure, "batch_size")
        # Phase 2 + 3
        assert hasattr(real_settings.structure, "setup_types")
        assert hasattr(real_settings.structure.setup_types, "ranging_market_mtf_threshold")
        # Phase 4
        assert hasattr(real_settings.scanner.scoring_weights, "rr")
        # Phase 5
        assert hasattr(real_settings.signal_generator.multi_source, "buy_threshold")
        # Phase 6
        assert hasattr(real_settings.brain, "cold_start_protection")
        # Phase 9
        assert hasattr(real_settings.apex, "apex_min_flip_confidence")
        # Phase 10
        assert hasattr(real_settings.mode4, "max_partials_per_position")
        # Phase 12
        assert hasattr(real_settings.strategy_engine, "vote_trace_enabled")
