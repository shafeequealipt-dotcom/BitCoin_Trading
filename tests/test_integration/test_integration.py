"""Integration tests — verify all components are properly wired."""

import pytest


class TestImports:
    """Every module must import without errors."""

    @pytest.mark.parametrize("module", [
        "src.core.container",
        "src.strategies.base_strategy", "src.strategies.registry",
        "src.strategies.register_all", "src.strategies.scanner",
        "src.strategies.regime", "src.strategies.scorer",
        "src.strategies.ensemble", "src.strategies.pnl_manager",
        "src.strategies.smart_leverage", "src.strategies.optimizer",
        "src.factory.discoverer", "src.factory.generator",
        "src.factory.validator", "src.factory.backtester",
        "src.factory.simulator", "src.factory.metrics",
        "src.factory.walk_forward", "src.factory.monte_carlo",
        "src.factory.lifecycle", "src.factory.trial_manager",
        "src.factory.live_monitor",
        "src.portfolio.kelly", "src.portfolio.correlation",
        "src.portfolio.allocator", "src.portfolio.risk_budget",
        "src.portfolio.optimizer", "src.portfolio.stress_test",
        "src.portfolio.analytics",
        "src.telegram.bot", "src.telegram.auth", "src.telegram.router",
        "src.telegram.conversation",
        "src.telegram.handlers.portfolio", "src.telegram.handlers.analysis",
        "src.telegram.handlers.trading", "src.telegram.handlers.brain",
        "src.telegram.handlers.system", "src.telegram.handlers.emergency",
        "src.telegram.handlers.alerts", "src.telegram.handlers.watchlist",
        "src.telegram.handlers.journal", "src.telegram.handlers.schedule",
        "src.telegram.ai.context_builder", "src.telegram.ai.question_handler",
        "src.telegram.features.price_alerts", "src.telegram.features.risk_checker",
        "src.telegram.ui.buttons", "src.telegram.ui.cards",
        "src.telegram.ui.charts", "src.telegram.ui.formatters",
        "src.brain.brain_v2", "src.brain.decision_parser",
        "src.brain.claude_client", "src.brain.cost_tracker",
        "src.workers.position_watchdog", "src.workers.strategy_worker",
        "src.workers.scanner_worker", "src.workers.regime_worker",
        "src.workers.discovery_worker", "src.workers.live_monitor_worker",
        "src.workers.backtest_worker", "src.workers.trial_monitor_worker",
        "src.workers.allocation_worker", "src.workers.optimization_worker",
        "src.workers.telegram_bot_worker", "src.workers.price_alert_worker",
        "src.workers.scheduled_report_worker",
        "src.database.repositories.factory_repo",
        "src.database.repositories.backtest_repo",
        "src.database.repositories.portfolio_repo",
        "src.database.repositories.telegram_repo",
    ])
    def test_import(self, module):
        __import__(module)


class TestConfig:
    """All config sections must load."""

    def test_all_sections_present(self):
        from src.config.settings import Settings
        Settings.reset()
        s = Settings._load_fresh()
        required = [
            "general", "bybit", "finnhub", "reddit", "altdata",
            "database", "workers", "brain", "risk", "alerts", "mcp",
            "watchdog", "scanner", "regime", "strategy_engine",
            "pnl_targets", "leverage", "optimizer",
            "factory", "backtesting", "trial",
            "portfolio", "telegram_interactive",
        ]
        for sec in required:
            assert hasattr(s, sec), f"Missing config section: {sec}"
        Settings.reset()


class TestStrategies:
    """All 39 strategies must register."""

    def test_all_register(self):
        from src.strategies.register_all import register_all_strategies
        from src.strategies.registry import StrategyRegistry
        r = StrategyRegistry()
        register_all_strategies(r)
        assert r.count >= 39  # 39 base + X1 on testnet = 40

    def test_all_categories_present(self):
        from src.strategies.register_all import register_all_strategies
        from src.strategies.registry import StrategyRegistry
        r = StrategyRegistry()
        register_all_strategies(r)
        cats = {s.category for s in r.get_all()}
        expected = {
            "scalping", "momentum", "mean_reversion", "funding_arb",
            "sentiment", "advanced", "predatory", "microstructure",
            "time_based", "cross_market", "ai_enhanced",
        }
        assert expected.issubset(cats)  # May also have "kickstart" on testnet


class TestServiceContainer:
    """ServiceContainer must initialize."""

    def test_container_importable(self):
        from src.core.container import ServiceContainer
        assert ServiceContainer is not None


class TestFactoryNoCircular:
    """Factory imports must not be circular."""

    def test_factory_repo(self):
        from src.database.repositories.factory_repo import FactoryRepository
        assert FactoryRepository is not None

    def test_backtest_repo(self):
        from src.database.repositories.backtest_repo import BacktestRepository
        assert BacktestRepository is not None

    def test_discoverer(self):
        from src.factory.discoverer import PatternDiscoverer
        assert PatternDiscoverer is not None

    def test_lifecycle(self):
        from src.factory.lifecycle import StrategyLifecycleManager
        assert StrategyLifecycleManager is not None
