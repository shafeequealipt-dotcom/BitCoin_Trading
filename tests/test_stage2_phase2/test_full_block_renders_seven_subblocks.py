"""Stage 2 phase 2 — rich block surfaces XRAY/signals/regime/scorer.

Verifies that with all services wired the new formatter renders the
seven sub-blocks: header, XRAY, signals, regime, votes,
TradeScorer, funding, position context.
"""

from types import SimpleNamespace

from src.brain.strategist import ClaudeStrategist
from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StateLabelBlock,
    StrategiesBlock,
    StructuralLevels,
    XrayBlock,
)
from src.core.types import Signal, SignalType
from src.strategies.models.regime_types import MarketRegime, RegimeState


class _FakeStructureCache:
    def __init__(self, data: dict) -> None:
        self._data = data

    def get(self, symbol):
        return self._data.get(symbol)


class _FakeSignalWorker:
    def __init__(self, sig_map: dict) -> None:
        self._sig_map = sig_map

    def get_signal(self, symbol):
        return self._sig_map.get(symbol)


class _FakeRegimeDetector:
    def __init__(self, reg_map: dict) -> None:
        self._reg_map = reg_map

    def get_coin_regime(self, symbol):
        return self._reg_map.get(symbol)


class _FakeLayerManager:
    def __init__(self, scorer_components: dict) -> None:
        self._scorer_components = scorer_components

    def get_scorer_components(self, symbol):
        return self._scorer_components.get(symbol)


def _structural_analysis(symbol: str):
    return SimpleNamespace(
        symbol=symbol,
        setup_quality="A+",
        position_in_range=0.18,
        smc_confluence=78,
        market_structure=SimpleNamespace(structure="uptrend"),
        # Issue #4: real FairValueGap/OrderBlock carry polarity on `.direction`
        # (bullish/bearish), NOT `.kind`. Match the real dataclass field names so
        # the test exercises the same attribute the renderer reads.
        nearest_fvg=SimpleNamespace(direction="bullish", midpoint=0.0421),
        nearest_ob=SimpleNamespace(direction="bullish", midpoint=0.0418),
        active_sweep_signal=SimpleNamespace(signal="high_probability_long_entry"),
        mtf_confluence=SimpleNamespace(quality="good"),
        mtf_confluence_score=72,
        total_confluence_factors=4,
        volume_profile=SimpleNamespace(),
        poc_price=0.0420,
        fib_key_level=0.0419,
        session_context=SimpleNamespace(
            current_session="ny", session_phase="mid",
            manipulation_likely=False,
        ),
    )


def _stub_strategist_with_services() -> ClaudeStrategist:
    sig = Signal(
        symbol="ETHUSDT", signal_type=SignalType.STRONG_BUY,
        confidence=0.78, source="signal_worker",
        components={"rsi": 0.42, "macd_hist": 0.31, "vol_ratio": 0.21,
                    "sentiment": 0.05, "funding": -0.03},
    )
    rs = RegimeState(
        regime=MarketRegime.TRENDING_UP, confidence=0.72,
        adx=32.5, atr_percentile=68.0, choppiness=24.0,
        volume_ratio=1.4, trend_direction=1,
        active_strategy_categories=["trend_following", "momentum"],
    )
    services = {
        "structure_cache": _FakeStructureCache({"ETHUSDT": _structural_analysis("ETHUSDT")}),
        "signal_worker": _FakeSignalWorker({"ETHUSDT": sig}),
        "regime_detector": _FakeRegimeDetector({"ETHUSDT": rs}),
        "layer_manager": _FakeLayerManager({
            "ETHUSDT": {
                "base": 33.0, "confluence": 18.0, "context": 12.5,
                "quality": 9.0, "total": 72.5, "grade": "A",
                "last_updated": 0.0,
            },
        }),
    }
    s = ClaudeStrategist.__new__(ClaudeStrategist)
    s.services = services
    s.settings = SimpleNamespace(
        brain=SimpleNamespace(surface_briefing_fields=False),
        scanner=SimpleNamespace(
            briefing=SimpleNamespace(prompt_floor_interestingness=0.20),
        ),
    )
    return s


def _eth_pkg() -> CoinPackage:
    return CoinPackage(
        symbol="ETHUSDT",
        qualified=True,
        opportunity_score=0.72,
        qualification_reasons=["xray=bullish_fvg_ob", "consensus=GOOD"],
        price_data=PriceDataBlock(
            current=0.0420, change_24h_pct=1.5, regime="trending_up",
        ),
        xray=XrayBlock(
            setup_type="bullish_fvg_ob",
            setup_score=72,
            setup_type_confidence=0.85,
            trade_direction="long",
            structural_levels=StructuralLevels(
                suggested_sl=0.0410, suggested_tp=0.0445, rr_ratio=2.50,
            ),
        ),
        strategies=StrategiesBlock(
            fired_count=4, ensemble_consensus="GOOD", total_score=72.5,
        ),
        signals=SignalsBlock(confidence=0.78, direction="long"),
        alt_data=AltDataBlock(
            funding_rate=0.0001, funding_signal="longs_paying",
            oi_change_24h_pct=2.5, fear_greed=42,
        ),
        state_label=StateLabelBlock(primary="MOMENTUM_RUN", confidence=0.7),
    )


class TestSubBlocks:
    def test_renders_xray_subblock(self) -> None:
        s = _stub_strategist_with_services()
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "XRAY: setup=bullish_fvg_ob" in out
        assert "Structure: market_structure=uptrend" in out
        assert "SMC:" in out
        # Issue #4 regression guard: the SMC line must render the FVG/OB polarity
        # from `.direction` (bullish/bearish), NOT the old always-'n/a' from the
        # nonexistent `.kind` attribute. These fail on the pre-fix code.
        assert "fvg=bullish@" in out
        assert "ob=bullish@" in out
        assert "fvg=n/a" not in out
        assert "ob=n/a" not in out
        assert "MTF: quality=good" in out
        assert "Volume profile: poc=" in out
        assert "Session: ny mid" in out
        # Price-precision fix: prompt prices now render via magnitude-aware
        # format_price (sub-cent no longer mangled). For $0.04-range values
        # this yields 6dp; the assertion tracks the canonical formatter output.
        assert "Levels: SL=$0.041000 TP=$0.044500 RR=2.50" in out

    def test_renders_signal_components(self) -> None:
        s = _stub_strategist_with_services()
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "Signal: type=strong_buy" in out
        assert "Components:" in out
        assert "rsi=" in out

    def test_renders_regime_breakdown(self) -> None:
        s = _stub_strategist_with_services()
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "Regime: trending_up" in out
        assert "ADX=32.5" in out
        assert "atr_percentile=68" in out

    def test_renders_scorer_4_components(self) -> None:
        s = _stub_strategist_with_services()
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "Score: total=72.5 grade=A" in out
        assert "base=33.0/40" in out
        assert "confluence=18.0/25" in out
        assert "context=12.5/20" in out
        assert "quality=9.0/20" in out

    def test_issue5_low_quality_grade_annotation(self) -> None:
        """Issue 5 (2026-06-09): the stub's quality sub-score is 9.0/20, below
        the default floor (10), so the Score line carries the always-on
        'quality LOW' annotation flagging the grade is driven by the other
        sub-scores."""
        s = _stub_strategist_with_services()
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "quality LOW" in out
        assert "driven by base/confluence/context" in out

    def test_issue5_annotation_suppressed_when_floor_zero(self) -> None:
        """A quality floor of 0 disables the annotation (no setup is below it)."""
        s = _stub_strategist_with_services()
        s.settings.strategy_engine = SimpleNamespace(grade_quality_floor=0.0)
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "quality LOW" not in out

    def test_renders_funding_and_strategies_lines(self) -> None:
        s = _stub_strategist_with_services()
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "Strategies: 4 fired" in out
        assert "ensemble GOOD" in out
        assert "Funding: 0.0001 (longs_paying)" in out

    def test_open_position_subblock(self) -> None:
        s = _stub_strategist_with_services()
        pkg = _eth_pkg()
        pkg.open_position = {"side": "Buy", "entry_price": 0.0421}
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "OPEN POSITION: Buy from $0.0421" in out

    def test_issue1a_signal_dir_conflict_note(self) -> None:
        """Issue 1a (2026-06-09): the intelligence Signal (STRONG_BUY → LONG)
        conflicts with the X-RAY structural direction (short) on the same coin
        — the SKR case. A labeled NOTE must surface so the brain does not read
        strong_buy as authoritative against the structure. Presentation only;
        the signal value is unchanged."""
        s = _stub_strategist_with_services()
        s.settings.brain.emit_direction_disagreement_notes = True
        pkg = _eth_pkg()
        pkg.xray.trade_direction = "short"  # structure says short; signal says strong_buy
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "this Signal is an independent" in out
        assert "CONFLICTS with the X-RAY structure (SHORT)" in out

    def test_issue1a_no_conflict_note_when_signal_agrees(self) -> None:
        """No false positive: signal LONG + X-RAY long → no conflict NOTE."""
        s = _stub_strategist_with_services()
        s.settings.brain.emit_direction_disagreement_notes = True
        pkg = _eth_pkg()  # xray.trade_direction defaults to "long"
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "CONFLICTS with the X-RAY structure" not in out

    def test_issue1a_conflict_note_suppressed_when_flag_off(self) -> None:
        """Instant rollback: flag False removes the signal conflict NOTE."""
        s = _stub_strategist_with_services()
        s.settings.brain.emit_direction_disagreement_notes = False
        pkg = _eth_pkg()
        pkg.xray.trade_direction = "short"
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "CONFLICTS with the X-RAY structure" not in out

    def test_issue3_absent_sentiment_omitted_and_funding_precision(self) -> None:
        """Issue 3 (2026-06-09): a true-absence sentiment/news input (stored
        None when the sentiment level is UNKNOWN) must be OMITTED from the
        Components line rather than rendered as a misleading 0.000, and a real
        small funding rate must render at 4 decimals (not round to -0.000)."""
        s = _stub_strategist_with_services()
        sig = Signal(
            symbol="ETHUSDT", signal_type=SignalType.NEUTRAL, confidence=0.50,
            source="intelligence_aggregator",
            components={
                "overall_sentiment": None,   # UNKNOWN -> true absence
                "fear_greed": 10,
                "funding_rate": -0.0002,     # real, small — must stay visible
                "oi_change_24h_pct": 0.547,
                "news_count": None,
                "reddit_count": None,
            },
        )
        s.services["signal_worker"] = _FakeSignalWorker({"ETHUSDT": sig})
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "funding_rate=-0.0002" in out          # 4-decimal precision
        assert "overall_sentiment" not in out          # None -> omitted
        assert "news_count" not in out                 # None -> omitted

    def _fg_signal(self, fg=10):
        return Signal(
            symbol="ETHUSDT", signal_type=SignalType.NEUTRAL, confidence=0.50,
            source="intelligence_aggregator",
            components={
                "fear_greed": fg,
                "funding_rate": -0.0002,
                "oi_change_24h_pct": 0.547,
            },
        )

    def test_issue4_fear_greed_demoted_and_tagged(self) -> None:
        """Issue 4 (2026-06-09): fear_greed is held out of the magnitude-ranked
        components and appended once, tagged global/direction-inactive, after
        the real per-coin components."""
        s = _stub_strategist_with_services()
        s.settings.brain.fear_greed_components_demote_enabled = True
        s.services["signal_worker"] = _FakeSignalWorker({"ETHUSDT": self._fg_signal()})
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        # Tagged and present as the integer index.
        assert "fear_greed=10 (global, direction-inactive)" in out
        # Real per-coin components present; fear_greed appended after them.
        comp_line = next(ln for ln in out.splitlines() if "Components:" in ln)
        assert "oi_change_24h_pct=" in comp_line
        assert "funding_rate=" in comp_line
        assert comp_line.index("oi_change_24h_pct") < comp_line.index("fear_greed")

    def test_issue4_flag_off_restores_ranking(self) -> None:
        """Rollback: flag False keeps fear_greed in the magnitude ranking with
        no tag (prior behavior)."""
        s = _stub_strategist_with_services()
        s.settings.brain.fear_greed_components_demote_enabled = False
        s.services["signal_worker"] = _FakeSignalWorker({"ETHUSDT": self._fg_signal()})
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "(global, direction-inactive)" not in out
        assert "fear_greed=" in out

    def _diag_signal(self):
        """Signal shaped like the live capture: market inputs PLUS the Phase
        4B classifier diagnostics interleaved in the same components dict."""
        return Signal(
            symbol="ETHUSDT", signal_type=SignalType.NEUTRAL, confidence=0.50,
            source="intelligence_aggregator",
            components={
                "fear_greed": 9,
                "funding_rate": -0.0002,
                "oi_change_24h_pct": -2.6095,
                "original_signal_type": "strong_buy",
                "confidence_floor_failed": True,
                "confidence_below_strong": True,
                "confidence_below_buy": False,
            },
        )

    def test_fix1_diagnostics_excluded_from_components_line(self) -> None:
        """Five-Fix Follow-Up Fix 1 (2026-06-10): the rendered Components line
        carries ONLY market inputs; the classifier diagnostics are absent from
        the prompt entirely (operator decision), while the components dict
        keeps every key for the DB/X-RAY consumers."""
        s = _stub_strategist_with_services()
        sig = self._diag_signal()
        s.services["signal_worker"] = _FakeSignalWorker({"ETHUSDT": sig})
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        comp_line = next(ln for ln in out.splitlines() if "Components:" in ln)
        assert "oi_change_24h_pct=" in comp_line
        assert "funding_rate=" in comp_line
        assert "confidence_floor_failed" not in out
        assert "confidence_below_strong" not in out
        assert "confidence_below_buy" not in out
        assert "original_signal_type" not in out
        # The dict itself is untouched — storage-side consumers unaffected.
        assert sig.components["confidence_floor_failed"] is True
        assert sig.components["original_signal_type"] == "strong_buy"

    def test_fix3_vol_stop_floor_line_renders_when_prefetched(self) -> None:
        """Five-Fix Follow-Up Fix 3 (2026-06-10): when the caller prefetches
        volatility floors (scaling flag on), each candidate shows its own
        Vol stop floor line; with no prefetch (flag off) the line is absent
        and the prompt is unchanged."""
        s = _stub_strategist_with_services()
        out_on = s._format_packages_for_prompt_full(
            {"ETHUSDT": _eth_pkg()}, vol_floors={"ETHUSDT": 2.4},
        )
        assert "Vol stop floor: 2.40%" in out_on
        assert "absolute min 1.5%" in out_on
        out_off = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "Vol stop floor" not in out_off

    def _conflict_sig(self):
        """A SELL signal to conflict with a LONG-side X-RAY."""
        return Signal(
            symbol="ETHUSDT", signal_type=SignalType.SELL, confidence=0.60,
            source="intelligence_aggregator",
            components={"fear_greed": 12, "funding_rate": -0.0002,
                        "oi_change_24h_pct": -1.0},
        )

    def _pkg_with_xray(self, setup_type, score, direction, primary=""):
        from src.core.coin_package import StateLabelBlock
        pkg = _eth_pkg()
        pkg.xray.setup_type = setup_type
        pkg.xray.setup_score = score
        pkg.xray.trade_direction = direction
        if primary:
            pkg.state_label = StateLabelBlock(primary=primary, confidence=0.5)
        return pkg

    def test_xray_weak_no_authority_in_signal_conflict_note(self) -> None:
        """Conditional authority (2026-06-11): a counter-trade or skip-grade
        X-RAY read may not claim direction authority over a conflicting
        Signal — the note flips to the WEAK-read wording."""
        s = _stub_strategist_with_services()
        s.services["signal_worker"] = _FakeSignalWorker(
            {"ETHUSDT": self._conflict_sig()}
        )
        pkg = self._pkg_with_xray("bullish_fvg_ob_counter", 64, "long")
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "X-RAY read is WEAK" in out
        assert "counter-trade setup" in out
        assert "structure is NOT authoritative here" in out
        assert "structure and regime are authoritative" not in out

    def test_xray_skip_grade_also_weak(self) -> None:
        """The HBAR live shape: a skip-grade score (30 < 45) yields the
        WEAK-read note even for a plain (non-counter) setup."""
        s = _stub_strategist_with_services()
        s.services["signal_worker"] = _FakeSignalWorker(
            {"ETHUSDT": self._conflict_sig()}
        )
        pkg = self._pkg_with_xray("bullish_fvg_ob", 30, "long")
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "X-RAY read is WEAK" in out
        assert "skip-grade score 30<45" in out

    def test_xray_strong_keeps_authority_wording(self) -> None:
        """A tradeable X-RAY read (normal setup, score above the SKIP floor)
        keeps the existing authority framing unchanged."""
        s = _stub_strategist_with_services()
        s.services["signal_worker"] = _FakeSignalWorker(
            {"ETHUSDT": self._conflict_sig()}
        )
        pkg = self._pkg_with_xray("bullish_fvg_ob", 72, "long")
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "structure and regime are authoritative" in out
        assert "X-RAY read is WEAK" not in out

    def test_action_hint_withheld_when_weak_same_side(self) -> None:
        """A command-shaped hint whose side matches a weak X-RAY read is
        withheld with the reason; a strong read keeps the hint."""
        s = _stub_strategist_with_services()
        s.settings.brain.surface_briefing_fields = True
        weak = self._pkg_with_xray(
            "bearish_fvg_ob_counter", 64, "short",
            primary="TREND_PULLBACK_SHORT",
        )
        out_weak = s._format_packages_for_prompt_full({"ETHUSDT": weak})
        assert "Action hint withheld" in out_weak
        assert "WEAK X-RAY read" in out_weak
        strong = self._pkg_with_xray(
            "bearish_fvg_ob", 72, "short", primary="TREND_PULLBACK_SHORT",
        )
        out_strong = s._format_packages_for_prompt_full({"ETHUSDT": strong})
        assert "Action hint:" in out_strong
        assert "Action hint withheld" not in out_strong

    def test_xray_authority_flag_off_restores_unconditional(self) -> None:
        """Rollback lever: with the flag off, even a weak read keeps the
        prior unconditional authority wording."""
        s = _stub_strategist_with_services()
        s.settings.brain.xray_authority_conditional_enabled = False
        s.services["signal_worker"] = _FakeSignalWorker(
            {"ETHUSDT": self._conflict_sig()}
        )
        pkg = self._pkg_with_xray("bullish_fvg_ob_counter", 30, "long")
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "structure and regime are authoritative" in out
        assert "X-RAY read is WEAK" not in out

    def test_fix1_bools_never_render_even_with_flag_off(self) -> None:
        """The bool render guard is UNCONDITIONAL: with the exclusion flag
        flipped off, a True flag must still never render as 1.0000 in the
        magnitude ranking (bool subclasses int — the pre-fix leak)."""
        s = _stub_strategist_with_services()
        s.settings.brain.components_diagnostics_excluded = False
        s.services["signal_worker"] = _FakeSignalWorker(
            {"ETHUSDT": self._diag_signal()}
        )
        out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
        assert "confidence_floor_failed" not in out
        assert "confidence_below_strong" not in out
        assert "confidence_below_buy" not in out
        # Market inputs still render normally.
        comp_line = next(ln for ln in out.splitlines() if "Components:" in ln)
        assert "oi_change_24h_pct=" in comp_line


class TestRegimeConsistencyAndVolDisplay:
    """Issue #2 (regime full consistency) + Issue #3A (vol_ratio honesty).

    The candidate `Regime:` line must show the SCORING word with the metrics
    from the SAME scored snapshot (not the live cache), append an explicit
    drift note when the live detector has moved off the scored regime, render
    a genuinely-low vol_ratio at real precision (not floored to 0.00) and
    `n/a` for missing volume; and the Consensus Context line must use the
    scoring regime.
    """

    def _stub(self, cache_rs: RegimeState) -> ClaudeStrategist:
        services = {
            "structure_cache": _FakeStructureCache(
                {"ETHUSDT": _structural_analysis("ETHUSDT")}
            ),
            "signal_worker": _FakeSignalWorker({"ETHUSDT": None}),
            "regime_detector": _FakeRegimeDetector({"ETHUSDT": cache_rs}),
            "layer_manager": _FakeLayerManager({}),
        }
        s = ClaudeStrategist.__new__(ClaudeStrategist)
        s.services = services
        s.settings = SimpleNamespace(
            brain=SimpleNamespace(surface_briefing_fields=False),
            scanner=SimpleNamespace(
                briefing=SimpleNamespace(prompt_floor_interestingness=0.20),
            ),
        )
        return s

    def _pkg(self, **strat_kwargs) -> CoinPackage:
        return CoinPackage(
            symbol="ETHUSDT",
            qualified=True,
            opportunity_score=0.5,
            qualification_reasons=["xray=bullish_fvg_ob"],
            price_data=PriceDataBlock(
                current=0.0420, change_24h_pct=0.7, regime="dead",
            ),
            xray=XrayBlock(
                setup_type="bullish_fvg_ob", setup_score=49,
                setup_type_confidence=0.70, trade_direction="long",
                structural_levels=StructuralLevels(
                    suggested_sl=0.0410, suggested_tp=0.0445, rr_ratio=2.50,
                ),
            ),
            strategies=StrategiesBlock(
                fired_count=23, ensemble_consensus="GOOD", total_score=79.8,
                **strat_kwargs,
            ),
            signals=SignalsBlock(confidence=0.4, direction="long"),
            alt_data=AltDataBlock(
                funding_rate=0.0001, funding_signal="longs_paying",
                oi_change_24h_pct=3.9, fear_greed=28,
            ),
            state_label=StateLabelBlock(primary="TREND_PULLBACK_LONG", confidence=0.6),
        )

    def _cache(self, regime: MarketRegime, **kw) -> RegimeState:
        base = dict(
            confidence=0.80, adx=10.9, atr_percentile=1.0, choppiness=38.0,
            volume_ratio=0.50, trend_direction=0,
        )
        base.update(kw)
        return RegimeState(regime=regime, **base)

    def test_scored_word_with_scored_metrics_and_drift_note(self):
        # Live cache drifted to DEAD; the coin was SCORED under RANGING with its
        # own metrics. The line must show ranging + the SCORED ADX (22.0), not the
        # cache ADX (10.9), plus the live-drift note.
        s = self._stub(self._cache(MarketRegime.DEAD, adx=10.9))
        pkg = self._pkg(
            scoring_regime="ranging",
            scoring_regime_confidence=0.55, scoring_regime_adx=22.0,
            scoring_regime_atr_percentile=30.0, scoring_regime_choppiness=42.0,
            scoring_regime_volume_ratio=0.80, scoring_regime_volume_ratio_known=True,
            scoring_regime_trend_direction=0,
        )
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "Regime: ranging " in out
        assert "ADX=22.0" in out          # scored metric, not cache's 10.9
        assert "ADX=10.9" not in out
        assert "(live conditions now read dead)" in out
        # (Consensus Context's use of the scoring regime is covered by
        #  test_layer4_consensus_context.py, which has the Settings-backed
        #  harness the flag-gated block requires.)

    def test_no_drift_note_when_scored_equals_cache(self):
        s = self._stub(self._cache(MarketRegime.RANGING, adx=22.0))
        pkg = self._pkg(
            scoring_regime="ranging",
            scoring_regime_adx=22.0, scoring_regime_volume_ratio=0.80,
            scoring_regime_volume_ratio_known=True,
        )
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "Regime: ranging " in out
        assert "live conditions now read" not in out

    def test_low_vol_ratio_not_floored_to_zero(self):
        # Issue #3A: a real ~0.06 must render at precision, never as 0.00.
        s = self._stub(self._cache(MarketRegime.DEAD))
        pkg = self._pkg(
            scoring_regime="dead",
            scoring_regime_adx=10.0, scoring_regime_volume_ratio=0.062,
            scoring_regime_volume_ratio_known=True,
        )
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "vol_ratio=0.062" in out
        assert "vol_ratio=0.00 " not in out

    def test_missing_volume_renders_na(self):
        # Issue #3A: missing volume shows n/a, not a fabricated number.
        s = self._stub(self._cache(MarketRegime.RANGING))
        pkg = self._pkg(
            scoring_regime="ranging",
            scoring_regime_adx=15.0, scoring_regime_volume_ratio=1.0,
            scoring_regime_volume_ratio_known=False,
        )
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "vol_ratio=n/a" in out

    def test_fallback_uses_cache_when_unscored(self):
        # No scoring_regime -> word + metrics both come from the live cache.
        s = self._stub(self._cache(MarketRegime.VOLATILE, adx=33.0, volume_ratio=1.4))
        pkg = self._pkg()  # scoring_regime defaults to ""
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "Regime: volatile " in out
        assert "ADX=33.0" in out
        assert "vol_ratio=1.400" in out
        assert "live conditions now read" not in out

    def test_cache_miss_but_scored_renders_scored_metrics(self):
        # Issue #2 fallback completion: live detector cache MISSES (get_coin_regime
        # -> None) but the coin WAS scored this cycle. The line must render the
        # scored word WITH the scored metrics carried on the package, not the bare
        # "(per-coin detail not yet cached)" word-only fallback.
        s = ClaudeStrategist.__new__(ClaudeStrategist)
        s.services = {
            "structure_cache": _FakeStructureCache({"ETHUSDT": _structural_analysis("ETHUSDT")}),
            "signal_worker": _FakeSignalWorker({"ETHUSDT": None}),
            "regime_detector": _FakeRegimeDetector({}),  # <-- cache MISS for ETHUSDT
            "layer_manager": _FakeLayerManager({}),
        }
        s.settings = SimpleNamespace(
            brain=SimpleNamespace(surface_briefing_fields=False),
            scanner=SimpleNamespace(briefing=SimpleNamespace(prompt_floor_interestingness=0.20)),
        )
        pkg = self._pkg(
            scoring_regime="ranging", scoring_regime_adx=22.0,
            scoring_regime_volume_ratio=0.062, scoring_regime_volume_ratio_known=True,
        )
        out = s._format_packages_for_prompt_full({"ETHUSDT": pkg})
        assert "Regime: ranging " in out
        assert "ADX=22.0" in out                     # scored metric rendered despite cache miss
        assert "vol_ratio=0.062" in out
        assert "per-coin detail not yet cached" not in out
