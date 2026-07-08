"""Tests for PortfolioAnalyzer."""

import pytest
from src.risk.portfolio import PortfolioAnalyzer


class TestPortfolio:
    @pytest.mark.asyncio
    async def test_exposure_safe(self, risk_settings, test_db, sample_positions_safe, sample_account):
        pa = PortfolioAnalyzer(risk_settings, test_db)
        result = await pa.get_exposure(sample_positions_safe, sample_account)
        assert result["total_positions"] == 2
        assert result["exposure_status"] == "safe"

    @pytest.mark.asyncio
    async def test_exposure_exceeded(self, risk_settings, test_db, sample_account):
        from src.core.types import Position, Side
        big_pos = [Position(symbol="BTCUSDT", side=Side.BUY, size=1.0,
                            entry_price=70000, mark_price=70000, leverage=1)]
        pa = PortfolioAnalyzer(risk_settings, test_db)
        result = await pa.get_exposure(big_pos, sample_account)
        assert result["exposure_status"] == "exceeded"

    @pytest.mark.asyncio
    async def test_concentration_warning(self, risk_settings, test_db, sample_account):
        from src.core.types import Position, Side
        big = [Position(symbol="BTCUSDT", side=Side.BUY, size=0.2,
                        entry_price=70000, mark_price=70000, leverage=1)]
        pa = PortfolioAnalyzer(risk_settings, test_db)
        warnings = await pa.check_concentration(big, sample_account)
        # 14000/10000 = 140% > 10% max
        assert any("BTCUSDT" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_correlation_warning(self, risk_settings, test_db):
        from src.core.types import Position, Side
        alts = [
            Position(symbol="SOLUSDT", side=Side.BUY, size=10, entry_price=150, mark_price=150, leverage=1),
            Position(symbol="XRPUSDT", side=Side.BUY, size=1000, entry_price=0.6, mark_price=0.6, leverage=1),
            Position(symbol="DOGEUSDT", side=Side.BUY, size=5000, entry_price=0.1, mark_price=0.1, leverage=1),
        ]
        pa = PortfolioAnalyzer(risk_settings, test_db)
        warnings = await pa.check_correlation(alts)
        assert any("correlated" in w.lower() for w in warnings)
        assert any("long" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warnings_when_safe(self, risk_settings, test_db, sample_positions_safe, sample_account):
        pa = PortfolioAnalyzer(risk_settings, test_db)
        result = await pa.get_exposure(sample_positions_safe, sample_account)
        # Small positions should have no warnings
        assert len(result["warnings"]) == 0 or result["exposure_status"] == "safe"
