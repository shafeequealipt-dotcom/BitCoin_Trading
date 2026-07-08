"""Fixtures for portfolio tests."""

import pytest
from src.config.settings import Settings, PortfolioSettings


@pytest.fixture
def factory_settings(tmp_path):
    """Reuse factory_settings name for consistency."""
    s = Settings()
    s.portfolio = PortfolioSettings(
        daily_risk_budget_pct=5.0,
        proven_strategies_budget_pct=55.0,
        ai_strategies_budget_pct=30.0,
        trial_strategies_budget_pct=10.0,
        cash_reserve_pct=5.0,
    )
    return s
