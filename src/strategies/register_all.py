"""Register all trading strategies with the StrategyRegistry."""

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.strategies.registry import StrategyRegistry

log = get_logger("strategies")


def register_strategies_a_to_f(registry: StrategyRegistry) -> None:
    """Register strategies A1 through F4 (19 strategies)."""
    from src.strategies.categories.a1_rsi_reversal import RSIReversalScalp
    from src.strategies.categories.a2_vwap_bounce import VWAPBounceScalp
    from src.strategies.categories.a3_bb_squeeze_scalp import BBSqueezeScalp
    from src.strategies.categories.a4_ema_crossover import EMACrossoverMomentum
    from src.strategies.categories.b1_volume_breakout import VolumeBreakout
    from src.strategies.categories.b2_supertrend_follower import SupertrendFollower
    from src.strategies.categories.b3_ichimoku_breakout import IchimokuBreakout
    from src.strategies.categories.b4_double_bottom_top import DoubleBottomTop
    from src.strategies.categories.c1_bb_mean_reversion import BBMeanReversion
    from src.strategies.categories.c2_rsi_divergence import RSIDivergence
    from src.strategies.categories.d1_funding_rate_fade import FundingRateFade
    from src.strategies.categories.d2_oi_divergence import OIDivergence
    from src.strategies.categories.e1_fear_greed_extreme import FearGreedExtreme
    from src.strategies.categories.e2_news_breakout import NewsBreakout
    from src.strategies.categories.e3_sentiment_momentum import SentimentMomentum
    from src.strategies.categories.f1_support_resistance import SupportResistanceBounce
    from src.strategies.categories.f2_multi_tf_alignment import MultiTFAlignment
    from src.strategies.categories.f3_liquidation_hunt import LiquidationHunt
    from src.strategies.categories.f4_grid_recovery import GridRecovery

    strategies = [
        RSIReversalScalp(),
        VWAPBounceScalp(),
        BBSqueezeScalp(),
        EMACrossoverMomentum(),
        VolumeBreakout(),
        SupertrendFollower(),
        IchimokuBreakout(),
        DoubleBottomTop(),
        BBMeanReversion(),
        RSIDivergence(),
        FundingRateFade(),
        OIDivergence(),
        FearGreedExtreme(),
        NewsBreakout(),
        SentimentMomentum(),
        SupportResistanceBounce(),
        MultiTFAlignment(),
        LiquidationHunt(),
        GridRecovery(),
    ]

    for strategy in strategies:
        registry.register(strategy)

    log.info("Registered {n} strategies (A1-F4)", n=len(strategies))


def register_strategies_g_to_k(registry: StrategyRegistry) -> None:
    """Register strategies G1 through K4 (21 strategies)."""
    from src.strategies.categories.g1_stop_hunt_sniper import StopHuntSniper
    from src.strategies.categories.g2_retail_sentiment_fade import RetailSentimentFade
    from src.strategies.categories.g3_liquidation_frontrunner import LiquidationFrontrunner
    from src.strategies.categories.g4_whale_shadow import WhaleShadow
    from src.strategies.categories.h1_funding_prediction import FundingPrediction
    from src.strategies.categories.h2_spread_basis import SpreadBasisExploit
    from src.strategies.categories.h3_volatility_switch import VolatilitySwitch
    from src.strategies.categories.h4_order_flow import OrderFlowImbalance
    from src.strategies.categories.i1_kill_zone import KillZoneTrading
    from src.strategies.categories.i2_weekend_gap import WeekendGapExploit
    from src.strategies.categories.i3_options_expiry import OptionsExpiryPlay
    from src.strategies.categories.i4_hourly_close import HourlyCloseMomentum
    from src.strategies.categories.j1_btc_dominance import BTCDominanceRotation
    from src.strategies.categories.j2_correlation_breakdown import CorrelationBreakdown
    from src.strategies.categories.j3_cross_exchange_lag import CrossExchangeLag
    from src.strategies.categories.j4_altcoin_beta import AltcoinBetaAmplification
    from src.strategies.categories.k1_claude_conviction import ClaudeConviction
    from src.strategies.categories.k2_pattern_memory import PatternMemory
    from src.strategies.categories.k3_ensemble import MultiStrategyEnsemble
    from src.strategies.categories.k4_adaptive_optimizer import AdaptiveOptimizer

    strategies = [
        StopHuntSniper(),
        RetailSentimentFade(),
        LiquidationFrontrunner(),
        WhaleShadow(),
        FundingPrediction(),
        SpreadBasisExploit(),
        VolatilitySwitch(),
        OrderFlowImbalance(),
        KillZoneTrading(),
        WeekendGapExploit(),
        OptionsExpiryPlay(),
        HourlyCloseMomentum(),
        BTCDominanceRotation(),
        CorrelationBreakdown(),
        CrossExchangeLag(),
        AltcoinBetaAmplification(),
        ClaudeConviction(),
        PatternMemory(),
        MultiStrategyEnsemble(),
        AdaptiveOptimizer(),
    ]

    for strategy in strategies:
        registry.register(strategy)

    log.info("Registered {n} strategies (G1-K4)", n=len(strategies))


def register_all_strategies(registry: StrategyRegistry) -> None:
    """Register ALL strategies (A1-K4 + X1 on testnet)."""
    register_strategies_a_to_f(registry)
    register_strategies_g_to_k(registry)

    # Fix 3 (sentiment removal, 2026-06-10): the two strategies that consume the
    # per-coin sentiment input are disabled now that sentiment is severed from
    # the signal pipeline. They already no-op (strategy_worker passes
    # sentiment_data=None, so each returns None), so this is bookkeeping that
    # keeps the active set honest. Fear-greed (E1) and news-breakout (E2) are
    # NOT sentiment aggregation and stay enabled.
    for _dead_sent_strat in ("E3_sentiment_momentum", "G2_retail_fade"):
        try:
            registry.set_enabled(_dead_sent_strat, False)
        except Exception as e:
            log.warning(
                f"BOOT_SENTIMENT_STRAT_DISABLE_FAIL | str={_dead_sent_strat} "
                f"err='{str(e)[:80]}' | {ctx()}"
            )
    log.info(
        "BOOT_SENTIMENT_STRATS_DISABLED | "
        "strs=[E3_sentiment_momentum,G2_retail_fade] "
        f"reason=fix3_sentiment_removal_2026-06-10 | {ctx()}"
    )

    # X1 kickstart strategy — testnet only
    try:
        from src.config.settings import Settings
        settings = Settings.load()
        if settings.bybit.testnet:
            from src.strategies.categories.x1_always_trade import AlwaysTradeStrategy
            registry.register(AlwaysTradeStrategy())
            log.info("Registered X1_always_trade (testnet kickstart)")
    except Exception as e:
        # Phase 14 (P1-13) — was silent `except: pass`. A failed
        # AlwaysTradeStrategy registration silently dropped the strategy
        # in testnet mode, leaving the operator without the kickstart
        # they were expecting. Log at warning so the loss is visible.
        log.warning(f"Suppressed: {e} (X1_always_trade registration) | {ctx()}")

    log.info("Total strategies registered: {n}", n=registry.count)
