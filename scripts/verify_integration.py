#!/usr/bin/env python3
"""Verify all system components are properly integrated."""

import sys
sys.path.insert(0, ".")

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  OK: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} — {detail}")
        failed += 1


def main():
    global passed, failed

    # 1. Config
    print("Config sections...")
    from src.config.settings import Settings
    Settings.reset()
    s = Settings._load_fresh()
    for sec in ["general", "bybit", "brain", "risk", "alerts", "watchdog",
                 "scanner", "regime", "strategy_engine", "pnl_targets",
                 "leverage", "optimizer", "factory", "backtesting", "trial",
                 "portfolio", "telegram_interactive", "mcp"]:
        check(sec, hasattr(s, sec), "missing from Settings")
    Settings.reset()

    # 2. Imports
    print("\nImports...")
    modules = [
        "src.core.container",
        "src.strategies.base_strategy", "src.strategies.registry",
        "src.strategies.register_all", "src.strategies.scanner",
        "src.strategies.regime", "src.strategies.scorer",
        "src.strategies.ensemble", "src.strategies.pnl_manager",
        "src.factory.discoverer", "src.factory.generator",
        "src.factory.validator", "src.factory.backtester",
        "src.factory.lifecycle", "src.factory.trial_manager",
        "src.portfolio.kelly", "src.portfolio.correlation",
        "src.portfolio.allocator", "src.portfolio.optimizer",
        "src.telegram.bot", "src.telegram.router",
        "src.telegram.handlers.portfolio", "src.telegram.handlers.trading",
        "src.telegram.handlers.emergency",
        "src.workers.position_watchdog", "src.workers.strategy_worker",
        "src.workers.telegram_bot_worker",
        "src.brain.brain_v2",
        "src.database.repositories.factory_repo",
        "src.database.repositories.backtest_repo",
        "src.database.repositories.portfolio_repo",
        "src.database.repositories.telegram_repo",
    ]
    for mod in modules:
        try:
            __import__(mod)
            check(mod, True)
        except Exception as e:
            check(mod, False, str(e)[:80])

    # 3. Strategies
    print("\nStrategies...")
    from src.strategies.register_all import register_all_strategies
    from src.strategies.registry import StrategyRegistry
    r = StrategyRegistry()
    register_all_strategies(r)
    check(f"{r.count} strategies registered", r.count >= 39, f"only {r.count}")

    # 4. Summary
    print(f"\n{'='*50}")
    print(f"PASSED: {passed}")
    print(f"FAILED: {failed}")
    print(f"{'='*50}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
