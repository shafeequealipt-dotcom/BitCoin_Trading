"""Strategy Factory — AI-powered pattern discovery, backtesting, and lifecycle management.

Imports are lazy to prevent circular dependency with database repositories.
Use: `from src.factory.discoverer import PatternDiscoverer` (direct import).
"""

__all__ = [
    "PatternDiscoverer", "StrategyGenerator", "CodeValidator", "LivePatternMonitor",
    "BacktestEngine", "TradeSimulator", "MetricsCalculator",
    "WalkForwardValidator", "MonteCarloSimulator",
    "StrategyLifecycleManager", "TrialManager",
]


def __getattr__(name):
    """Lazy imports to break circular dependency chain."""
    _imports = {
        "PatternDiscoverer": "src.factory.discoverer",
        "StrategyGenerator": "src.factory.generator",
        "CodeValidator": "src.factory.validator",
        "LivePatternMonitor": "src.factory.live_monitor",
        "BacktestEngine": "src.factory.backtester",
        "TradeSimulator": "src.factory.simulator",
        "MetricsCalculator": "src.factory.metrics",
        "WalkForwardValidator": "src.factory.walk_forward",
        "MonteCarloSimulator": "src.factory.monte_carlo",
        "StrategyLifecycleManager": "src.factory.lifecycle",
        "TrialManager": "src.factory.trial_manager",
    }
    if name in _imports:
        import importlib
        module = importlib.import_module(_imports[name])
        return getattr(module, name)
    raise AttributeError(f"module 'src.factory' has no attribute {name}")
