"""CoinPackage dataclass + size — Layer 1 restructure Phase 6."""

import json

from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StrategiesBlock,
    StructuralLevels,
    XrayBlock,
)


def test_default_construction() -> None:
    pkg = CoinPackage(symbol="BTCUSDT", qualified=True, opportunity_score=0.75)
    assert pkg.symbol == "BTCUSDT"
    assert pkg.qualified is True
    assert pkg.qualification_reasons == []
    assert isinstance(pkg.xray, XrayBlock)
    assert isinstance(pkg.alt_data, AltDataBlock)
    assert pkg.open_position is None
    assert pkg.blockers_observed == []
    assert pkg.built_at > 0


def test_to_dict_round_trip() -> None:
    pkg = CoinPackage(
        symbol="ETHUSDT", qualified=True, opportunity_score=0.5,
        qualification_reasons=["xray_setup=bullish_fvg_ob"],
        price_data=PriceDataBlock(current=2000.0, change_24h_pct=2.5, regime="trending_up"),
        xray=XrayBlock(
            setup_type="bullish_fvg_ob",
            setup_score=85.0,
            structural_levels=StructuralLevels(
                current_price=2000.0, suggested_sl=1950.0,
                suggested_tp=2100.0, rr_ratio=2.5,
            ),
        ),
        strategies=StrategiesBlock(
            fired_count=5, ensemble_consensus="STRONG", consensus_score=0.92,
        ),
        signals=SignalsBlock(confidence=0.78, direction="long"),
        alt_data=AltDataBlock(funding_rate=-0.0001, fear_greed=65),
    )
    d = pkg.to_dict()
    assert d["symbol"] == "ETHUSDT"
    assert d["xray"]["setup_type"] == "bullish_fvg_ob"
    assert d["xray"]["structural_levels"]["rr_ratio"] == 2.5
    # Round-trip via JSON to confirm primitives only.
    j = json.dumps(d, default=str)
    assert "ETHUSDT" in j


def test_size_bytes_reasonable() -> None:
    pkg = CoinPackage(symbol="X", qualified=True, opportunity_score=0.5)
    n = pkg.size_bytes()
    assert 100 < n < 5000  # tiny package; 50KB cap is for the whole batch


def test_open_position_serializes() -> None:
    pkg = CoinPackage(
        symbol="X", qualified=False, opportunity_score=0.0,
        open_position={"side": "long", "entry_price": 1.23},
    )
    assert pkg.to_dict()["open_position"]["side"] == "long"


class TestLayerManagerAccessor:
    def test_get_coin_packages_default_empty(self) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager.__new__(LayerManager)
        lm._coin_packages = {}
        assert lm.get_coin_packages() == {}

    def test_get_coin_packages_returns_dict(self) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager.__new__(LayerManager)
        pkg = CoinPackage(symbol="BTCUSDT", qualified=True, opportunity_score=0.9)
        lm._coin_packages = {"BTCUSDT": pkg}
        out = lm.get_coin_packages()
        assert "BTCUSDT" in out
        assert out["BTCUSDT"].symbol == "BTCUSDT"

    def test_missing_attr_returns_empty(self) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager.__new__(LayerManager)
        # No attribute set — accessor must default-empty.
        assert lm.get_coin_packages() == {}
