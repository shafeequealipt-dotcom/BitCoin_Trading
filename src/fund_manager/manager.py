"""IntelligentFundManager — The brain that controls ALL capital decisions.

Every trade in the system goes through this manager before execution.
It answers: "Can we trade? How much? What leverage? From which pool?"
"""

import time

from src.core.logging import get_logger
from src.core.log_context import ctx
from src.fund_manager.models.fund_types import (
    AccountLevel, AccountState, CapitalPool, CapitalVelocity,
    MarketEmotion, RiskWeather, RiskWeatherReport,
    SizingDecision, TimeHorizon,
)

log = get_logger("fund_manager")


class IntelligentFundManager:
    """Central fund management orchestrator.

    ALL trading decisions flow through this manager:
    1. StrategyWorker finds a setup -> asks manager "how much?"
    2. Manager runs ALL 22 modules
    3. Manager returns: amount, leverage, pool, or REJECT
    4. OrderService uses manager's decision for the trade
    """

    def __init__(self, settings, db, services: dict) -> None:
        self.settings = settings
        self.db = db
        self.services = services

        from src.fund_manager.capital_allocator import CapitalAllocator
        from src.fund_manager.position_sizer import PositionSizer
        from src.fund_manager.capital_reserves import CapitalReserves
        from src.fund_manager.correlation_guard import CorrelationGuard
        from src.fund_manager.time_pools import TimePoolManager
        from src.fund_manager.volatility_scaler import VolatilityScaler
        from src.fund_manager.sector_rotation import SectorRotation
        from src.fund_manager.strategy_budgets import StrategyBudgetManager
        from src.fund_manager.momentum_allocator import MomentumAllocator
        from src.fund_manager.risk_weather import RiskWeatherAssessor
        from src.fund_manager.capital_velocity import CapitalVelocityTracker
        from src.fund_manager.recovery_planner import RecoveryPlanner
        from src.fund_manager.opportunity_cost import OpportunityCostCalculator
        from src.fund_manager.profit_ratchet import ProfitRatchet
        from src.fund_manager.time_sync import TimeSync
        from src.fund_manager.emotion_detector import MarketEmotionDetector
        from src.fund_manager.ecosystem_health import EcosystemHealthMonitor
        from src.fund_manager.anti_fragile import AntiFrag
        from src.fund_manager.loss_harvester import LossHarvester
        from src.fund_manager.compound_optimizer import CompoundOptimizer
        from src.fund_manager.liquidity_mapper import LiquidityMapper
        from src.fund_manager.fee_optimizer import FeeOptimizer

        self.m1_allocator = CapitalAllocator(settings, db)
        self.m2_sizer = PositionSizer(settings)
        self.m3_reserves = CapitalReserves(settings)
        self.m4_correlation = CorrelationGuard(settings, services)
        self.m5_time_pools = TimePoolManager(settings)
        self.m6_volatility = VolatilityScaler(settings, services)
        self.m7_rotation = SectorRotation(settings, services)
        self.m8_budgets = StrategyBudgetManager(settings, services)
        self.m9_momentum = MomentumAllocator(settings, db)
        self.m10_weather = RiskWeatherAssessor(settings, services)
        self.m11_velocity = CapitalVelocityTracker(settings, db)
        self.m12_recovery = RecoveryPlanner(settings, db)
        self.m13_opportunity = OpportunityCostCalculator(settings, services)
        self.m14_ratchet = ProfitRatchet(settings, db)
        self.m15_time_sync = TimeSync(settings)
        self.m16_emotion = MarketEmotionDetector(settings, services)
        self.m17_ecosystem = EcosystemHealthMonitor(settings, services)
        self.m18_antifrag = AntiFrag(settings, services)
        self.m19_loss_harvest = LossHarvester(settings, db, services)
        self.m20_compound = CompoundOptimizer(settings, db)
        self.m21_liquidity = LiquidityMapper(settings, services)
        self.m22_fees = FeeOptimizer(settings)

        self._account_state: AccountState | None = None

        # Phase 1 (post-Layer-1 fix): track consecutive balance/position
        # read failures across update_state() calls. Three failures in a
        # row escalates the silent ``except: pass`` to a structured ERROR
        # log. Single failures continue to log at WARNING. Resets to 0 on
        # any success, so transient blips during a Shadow restart don't
        # accumulate into a false-positive alert.
        self._consecutive_balance_fails = 0
        self._consecutive_position_fails = 0
        self._FAIL_ALERT_THRESHOLD = 3

        # Profit floor removed (#4) — replaced by tiered capital system
        # See src/fund_manager/tiered_capital.py

    async def initialize(self) -> None:
        """Initialize fund manager with current account state."""
        account_svc = self.services.get("account_service") or self.services.get("account")
        if not account_svc:
            log.warning("Fund Manager: account_service not available, using defaults")
            self._account_state = AccountState(total_equity=10000, starting_balance=10000)
            return

        try:
            account = await account_svc.get_wallet_balance()
            starting = await self._load_starting_balance()
            if starting == 0:
                starting = account.total_equity
                await self._save_starting_balance(starting)

            self._account_state = AccountState(
                total_equity=account.total_equity,
                starting_balance=starting,
            )

            await self.m1_allocator.initialize(self._account_state)
            await self.m14_ratchet.initialize(self._account_state)
            await self.m12_recovery.initialize(self._account_state)
            await self.update_state()

            log.info(
                "Fund Manager initialized: equity=${eq:.2f}, starting=${st:.2f}, "
                "level={lev}, trading_capital=${tc:.2f}",
                eq=account.total_equity, st=starting,
                lev=self._account_state.level.value,
                tc=self._account_state.trading_capital,
            )
        except Exception as e:
            log.error("Fund Manager init failed: {err}", err=str(e))
            self._account_state = AccountState(total_equity=10000, starting_balance=10000)

    async def update_state(self) -> None:
        """Update account state — called every cycle (60 seconds).

        Both the balance read and the position read used to swallow
        exceptions silently (``except: pass``), which masked Shadow
        outages and left ``total_equity`` stale and ``in_use`` at 0
        without any operator visibility. Phase 1 of the post-Layer-1
        fix replaces those silent paths with structured logs and a
        consecutive-failure counter that escalates to ERROR after
        ``_FAIL_ALERT_THRESHOLD`` cycles.

        H3 (2026-05-16) — FUND_INUSE_DRIFT root cause fix. Pre-fix
        ``state.in_use`` was derived as ``sum(abs(p.size * p.entry_price)
        for p in positions)`` — the position notional, NOT the margin
        actually required by the exchange. Bybit's perpetual margin is
        ``notional / leverage`` (the ``totalInitialMargin`` field on
        the wallet response). Pre-fix observation: in a 5h baseline
        FUND_INUSE_DRIFT fired every minute with sign negative (local
        OVER-counts) and magnitude growing from $-7k to $-17k as new
        leveraged positions opened. ``state.available`` was correspondingly
        starved, blocking aggressive sizing.

        Fix: read ``account.used_margin`` (Bybit's totalInitialMargin)
        and use it directly as the canonical local ``state.in_use``.
        Bybit is single source of truth; the formula drift is gone by
        construction. As a defense-in-depth fallback, when the wallet
        read fails we compute a leverage-aware position-derived value
        (``sum(notional / leverage)``) instead of the broken naive sum.

        For diagnostic observability the function also computes the
        leverage-aware position-derived value whenever positions are
        available and emits FUND_INUSE_RECONCILE comparing the two —
        a non-trivial gap there signals orphan-positions or schema
        drift (J1 territory), distinct from the H3 formula bug.

        Operator's heal preference (2026-05-16 plan session):
        "Prevention + one-shot heal at deploy". The heal happens
        automatically on the very first update_state() call after
        deploy because state.in_use is now derived from Bybit directly
        — the existing $20k+ residual evaporates on the first tick.
        No separate heal command required.
        """
        account_svc = self.services.get("account_service") or self.services.get("account")
        pos_svc = self.services.get("position_service") or self.services.get("position")

        # H3 — fetch the Bybit-reported margin (canonical source of
        # truth) alongside total_equity. Both come from the same
        # /v5/account/wallet-balance response so cost is amortized.
        bybit_used_margin: float | None = None
        if account_svc:
            try:
                account = await account_svc.get_wallet_balance()
                self._account_state.total_equity = account.total_equity
                bybit_used_margin = float(
                    getattr(account, "used_margin", 0.0) or 0.0
                )
                self._consecutive_balance_fails = 0
            except Exception as e:
                self._consecutive_balance_fails += 1
                if self._consecutive_balance_fails >= self._FAIL_ALERT_THRESHOLD:
                    log.error(
                        f"FUND_MGR_BALANCE_FAIL_PERSISTENT | "
                        f"consecutive={self._consecutive_balance_fails} "
                        f"err={str(e)[:120]} | {ctx()}"
                    )
                else:
                    log.warning(
                        f"FUND_MGR_BALANCE_FAIL | "
                        f"consecutive={self._consecutive_balance_fails} "
                        f"threshold={self._FAIL_ALERT_THRESHOLD} "
                        f"err={str(e)[:120]} | {ctx()}"
                    )

        state = self._account_state
        if state.starting_balance > 0:
            state.growth_multiplier = state.total_equity / state.starting_balance
        else:
            state.growth_multiplier = 1.0

        self.m1_allocator.update_level(state)
        self.m14_ratchet.update(state)
        state.trading_capital = state.total_equity * (state.unlock_pct / 100)
        self.m3_reserves.update_pools(state)

        # H3 — position-derived value (leverage-aware) computed for
        # diagnostic and fallback purposes. Naive notional sum
        # (pre-H3 formula) is preserved as ``in_use_notional`` on the
        # state for callers that explicitly want it.
        in_use_position_derived: float | None = None
        in_use_naive_notional: float | None = None
        if pos_svc:
            try:
                positions = await pos_svc.get_positions()
                in_use_naive_notional = sum(
                    abs((p.size or 0) * (p.entry_price or 0)) for p in positions
                )
                in_use_position_derived = sum(
                    abs((p.size or 0) * (p.entry_price or 0))
                    / max(1, int(getattr(p, "leverage", 1) or 1))
                    for p in positions
                )
                self._consecutive_position_fails = 0
            except Exception as e:
                self._consecutive_position_fails += 1
                if self._consecutive_position_fails >= self._FAIL_ALERT_THRESHOLD:
                    log.error(
                        f"FUND_MGR_POSITIONS_FAIL_PERSISTENT | "
                        f"consecutive={self._consecutive_position_fails} "
                        f"err={str(e)[:120]} | {ctx()}"
                    )
                else:
                    log.warning(
                        f"FUND_MGR_POSITIONS_FAIL | "
                        f"consecutive={self._consecutive_position_fails} "
                        f"threshold={self._FAIL_ALERT_THRESHOLD} "
                        f"err={str(e)[:120]} | {ctx()}"
                    )

        # H3 — set state.in_use. Priority: Bybit-reported (canonical) >
        # leverage-aware position-derived (fallback when wallet read
        # failed) > previous value (defensive, never go to 0 silently).
        _prev_in_use = float(state.in_use or 0.0)
        if bybit_used_margin is not None:
            _new_in_use = bybit_used_margin
            _source = "bybit_wallet"
        elif in_use_position_derived is not None:
            _new_in_use = in_use_position_derived
            _source = "position_derived_leverage_aware"
        else:
            _new_in_use = _prev_in_use
            _source = "stale_no_source"
        state.in_use = _new_in_use
        # Stash the naive-notional value on state so any reader that
        # specifically wants notional exposure has it. Default to 0
        # when positions weren't readable.
        state.in_use_notional = float(in_use_naive_notional or 0.0)
        state.available = max(0, state.trading_capital - state.in_use)

        # H3 — FUND_INUSE_TRANSITION fires whenever in_use changes
        # (delta + reason + source). Spec Rule 6 mandated event.
        # Tolerance for noise-level changes: only emit when |delta| >= 0.01.
        _delta = _new_in_use - _prev_in_use
        if abs(_delta) >= 0.01 or _prev_in_use == 0.0:
            log.info(
                f"FUND_INUSE_TRANSITION | "
                f"prev={_prev_in_use:.2f} new={_new_in_use:.2f} "
                f"delta={_delta:+.2f} source={_source} | {ctx()}"
            )

        # H3 — FUND_INUSE_RECONCILE compares Bybit vs position-derived
        # whenever both are available. A non-trivial gap signals orphan
        # positions or schema drift (J1 territory). Emit at INFO so the
        # operator's grep tools can find it; the position_reconciler
        # separately escalates to WARNING when the |diff| exceeds the
        # operator-configured threshold.
        if bybit_used_margin is not None and in_use_position_derived is not None:
            _rec_diff = bybit_used_margin - in_use_position_derived
            log.info(
                f"FUND_INUSE_RECONCILE | "
                f"bybit_margin={bybit_used_margin:.2f} "
                f"position_derived={in_use_position_derived:.2f} "
                f"naive_notional={in_use_naive_notional or 0.0:.2f} "
                f"diff_bybit_vs_pos={_rec_diff:+.2f} | {ctx()}"
            )

        log.info(f"FUND_POOLS | cap={state.trading_capital:.2f} | available={state.available:.2f} | in_use={state.in_use:.2f} | {ctx()}")

    async def get_sizing_decision(
        self,
        symbol: str,
        side: str,
        setup_score: float,
        setup_grade: str,
        consensus_strength: str,
        strategy_name: str,
        strategy_category: str,
        expected_hold_minutes: int,
        stop_loss_pct: float,
    ) -> SizingDecision:
        """THE MAIN METHOD — called for every potential trade."""
        # Phase 8: total sizing-decision elapsed (observability only)
        _fm_t0 = time.time()
        await self.update_state()
        state = self._account_state
        decision = SizingDecision(symbol=symbol)
        reasons = []

        # ══════════════ GATE 1: Can we trade AT ALL? ══════════════

        weather = await self.m10_weather.assess()
        if weather.level == RiskWeather.NUCLEAR:
            if self.settings.bybit.testnet:
                log.warning("NUCLEAR weather but paper mode — will apply minimum sizing")
            else:
                decision.final_amount_usd = 0
                decision.reasoning = "REJECTED: Risk weather NUCLEAR — all trading halted"
                log.warning(f"FUND_REJECT | sym={symbol} | dir={side} | reason=NUCLEAR_weather | {ctx()}")
                return decision

        # Profit floor removed (#4) — capital limits handled by tiered_capital.py

        recovery = self.m12_recovery.get_plan()
        if recovery.active:
            safe_cats = {"funding_arb", "mean_reversion", "scalping"}
            if strategy_category not in safe_cats:
                decision.final_amount_usd = 0
                decision.reasoning = "REJECTED: Recovery mode — only safe strategies"
                log.warning(f"FUND_REJECT | sym={symbol} | dir={side} | reason=recovery_mode_unsafe_strategy | {ctx()}")
                return decision

        if not await self.m21_liquidity.is_liquid_enough(symbol):
            decision.final_amount_usd = 0
            decision.reasoning = f"REJECTED: {symbol} liquidity too thin"
            log.warning(f"FUND_REJECT | sym={symbol} | dir={side} | reason=liquidity_too_thin | {ctx()}")
            return decision

        if state.available <= 0:
            decision.final_amount_usd = 0
            decision.reasoning = "REJECTED: No available capital"
            log.warning(f"FUND_REJECT | sym={symbol} | dir={side} | reason=no_available_capital | {ctx()}")
            return decision

        # ══════════════ GATE 2: Which pool? ══════════════

        pool, pool_available = self.m3_reserves.get_pool_for_setup(
            setup_grade, setup_score, weather.level, state,
        )
        decision.capital_pool_used = pool

        # ══════════════ STEP 1: Base size ══════════════

        base_pct = self.m2_sizer.get_base_pct(setup_grade, state.level)
        base_amount = state.trading_capital * (base_pct / 100)
        decision.raw_amount_usd = base_amount
        decision.quality_multiplier = base_pct / 5.0

        # ══════════════ STEP 2: Multipliers ══════════════

        streak = await self._get_current_streak()
        decision.streak_multiplier = self.m2_sizer.get_streak_multiplier(streak)
        if abs(streak) > 3:
            reasons.append(f"Streak {streak}: x{decision.streak_multiplier:.2f}")

        daily_pnl = await self._get_daily_pnl_pct()
        decision.pnl_multiplier = self.m2_sizer.get_pnl_multiplier(daily_pnl)

        decision.volatility_multiplier = await self.m6_volatility.get_multiplier(symbol)
        if decision.volatility_multiplier < 0.8:
            reasons.append(f"High vol: x{decision.volatility_multiplier:.2f}")

        decision.consensus_multiplier = self.m2_sizer.get_consensus_multiplier(consensus_strength)

        decision.correlation_multiplier = await self.m4_correlation.get_multiplier(symbol, side)
        if decision.correlation_multiplier < 0.8:
            reasons.append(f"Correlated: x{decision.correlation_multiplier:.2f}")

        decision.time_multiplier = self.m15_time_sync.get_multiplier()

        decision.weather_multiplier = weather.allocation_multiplier
        if weather.level != RiskWeather.CLEAR:
            reasons.append(f"Weather {weather.level.value}: x{weather.allocation_multiplier:.2f}")

        emotion = await self.m16_emotion.detect()
        decision.emotion_multiplier = self.m16_emotion.get_multiplier(emotion, side)
        if emotion != MarketEmotion.NEUTRAL:
            reasons.append(f"Emotion {emotion.value}: x{decision.emotion_multiplier:.2f}")

        decision.momentum_multiplier = await self.m9_momentum.get_multiplier(strategy_name)

        velocity = await self.m11_velocity.get_current(state.trading_capital)
        decision.velocity_multiplier = self.m11_velocity.get_multiplier(velocity.current_velocity)
        if velocity.status != "healthy":
            reasons.append(f"Velocity {velocity.status}: x{decision.velocity_multiplier:.2f}")

        # Anti-fragile override
        if self.m18_antifrag.is_antifragile(strategy_category, weather.level):
            decision.weather_multiplier = max(decision.weather_multiplier, 1.0)
            reasons.append("Anti-fragile override")

        # ══════════════ STEP 3: Final amount ══════════════

        combined = decision.combined_multiplier
        final_amount = base_amount * combined

        # ══════════════ STEP 4: Caps ══════════════

        final_amount = min(final_amount, pool_available)

        level_max_pct = self.m1_allocator.get_max_trade_pct(state.level)
        level_max_usd = state.trading_capital * (level_max_pct / 100)
        final_amount = min(final_amount, level_max_usd)

        horizon = self.m5_time_pools.classify(expected_hold_minutes)
        time_pool_available = self.m5_time_pools.get_available(horizon, state.trading_capital)
        final_amount = min(final_amount, time_pool_available)
        decision.time_horizon = horizon

        strategy_budget = self.m8_budgets.get_budget(strategy_name, state.trading_capital)
        final_amount = min(final_amount, strategy_budget)

        sector_available = await self.m7_rotation.get_available(symbol, state.trading_capital)
        final_amount = min(final_amount, sector_available)

        if recovery.active:
            recovery_max = state.trading_capital * (recovery.max_trade_size_pct / 100)
            final_amount = min(final_amount, recovery_max)

        if stop_loss_pct > 0:
            max_loss_allowed = state.trading_capital * 0.02
            max_amount_for_risk = max_loss_allowed / (stop_loss_pct / 100)
            final_amount = min(final_amount, max_amount_for_risk)

        min_profitable = self.m22_fees.min_profitable_trade(symbol)
        if final_amount < min_profitable:
            decision.final_amount_usd = 0
            decision.reasoning = (
                f"REJECTED: ${final_amount:.0f} too small (min ${min_profitable:.0f})"
            )
            log.warning(f"FUND_REJECT | sym={symbol} | dir={side} | reason=below_min_profitable | amt={final_amount:.0f} | min={min_profitable:.0f} | {ctx()}")
            return decision

        # ══════════════ STEP 5: Leverage ══════════════

        max_lev = self.m1_allocator.get_max_leverage(state.level)
        if weather.max_leverage_override < max_lev:
            max_lev = weather.max_leverage_override

        smart_lev = self.services.get("smart_leverage")
        if smart_lev:
            try:
                leverage = smart_lev.calculate(
                    symbol=symbol,
                    direction=side,
                    confidence=setup_score / 100,
                    regime=None,
                    coin_tier=self.m7_rotation.get_coin_tier(symbol),
                    volatility_percentile=await self.m6_volatility.get_percentile(symbol),
                    ensemble_strength=consensus_strength,
                )
                leverage = min(leverage, max_lev)
            except Exception:
                leverage = min(3, max_lev)
        else:
            leverage = min(3, max_lev)

        decision.final_leverage = leverage

        # ══════════════ STEP 6: Opportunity cost ══════════════

        opp = await self.m13_opportunity.is_best_use(
            symbol=symbol,
            amount=final_amount,
            expected_return_pct=setup_score / 20,
            probability=setup_score / 100,
        )
        if not opp["is_best"]:
            final_amount *= 0.7
            reasons.append(f"Opp cost: {opp.get('better_option', 'hold')}")

        # ══════════════ STEP 7: Finalize ══════════════

        decision.final_amount_usd = round(final_amount, 2)
        decision.max_loss_usd = final_amount * (stop_loss_pct / 100) * leverage
        decision.reasoning = " | ".join(reasons) if reasons else "Standard sizing"
        decision.all_multipliers = {
            "quality": decision.quality_multiplier,
            "streak": decision.streak_multiplier,
            "pnl": decision.pnl_multiplier,
            "volatility": decision.volatility_multiplier,
            "consensus": decision.consensus_multiplier,
            "correlation": decision.correlation_multiplier,
            "time": decision.time_multiplier,
            "weather": decision.weather_multiplier,
            "emotion": decision.emotion_multiplier,
            "momentum": decision.momentum_multiplier,
            "velocity": decision.velocity_multiplier,
            "combined": combined,
        }

        # Portfolio Optimizer hierarchy: Fund Manager cannot exceed strategic allocation
        try:
            alloc_row = await self.db.fetch_one(
                "SELECT allocated_pct FROM portfolio_allocations WHERE strategy_name = ? ORDER BY computed_at DESC LIMIT 1",
                (strategy_name,),
            )
            if alloc_row and alloc_row["allocated_pct"] > 0:
                max_from_optimizer = state.trading_capital * (alloc_row["allocated_pct"] / 100)
                if decision.final_amount_usd > max_from_optimizer and max_from_optimizer > 0:
                    decision.final_amount_usd = max_from_optimizer
                    reasons.append(f"Capped by portfolio allocation: {alloc_row['allocated_pct']:.1f}%")
                    decision.reasoning = " | ".join(reasons)
        except Exception:
            pass  # No portfolio data — Fund Manager operates standalone

        # Paper trading: never return $0. Minimum $25 micro-position for data collection.
        if self.settings.bybit.testnet and decision.final_amount_usd < 25.0:
            old_amt = decision.final_amount_usd
            decision.final_amount_usd = 25.0
            decision.final_leverage = max(decision.final_leverage, 1)
            if old_amt <= 0:
                reasons.append(f"Paper minimum: $0 -> $25 micro-position")
            else:
                reasons.append(f"Paper minimum: ${old_amt:.0f} -> $25")
            decision.reasoning = " | ".join(reasons)

        log.info(
            "Fund: {sym} {side} -> ${amt:.0f} at {lev}x "
            "(base=${base:.0f} x {comb:.2f}) pool={pool} level={level} | {reason}",
            sym=symbol, side=side, amt=decision.final_amount_usd,
            lev=leverage, base=base_amount, comb=combined,
            pool=pool.value, level=state.level.value,
            reason=decision.reasoning[:100],
        )
        _fm_el_ms = (time.time() - _fm_t0) * 1000
        log.info(f"FUND_SIZE | sym={symbol} | dir={side} | score={setup_score:.1f} | alloc={decision.final_amount_usd:.2f} | tier={state.level.value} | weather={weather.level.value} | lev={leverage} | el={_fm_el_ms:.0f}ms | {ctx()}")
        if _fm_el_ms > 2000:
            log.warning(
                f"FM_SIZING_SLOW | sym={symbol} el={_fm_el_ms:.0f}ms "
                f"— inspect async sub-modules (weather/volatility/correlation/velocity/emotion/opportunity/liquidity) | {ctx()}"
            )

        return decision

    async def on_trade_opened(
        self, symbol: str, amount: float, pool: CapitalPool, horizon: TimeHorizon,
    ) -> None:
        self.m5_time_pools.on_capital_locked(horizon, amount)
        self.m11_velocity.on_trade(amount)
        await self.update_state()

    async def on_trade_closed(
        self, symbol: str, pnl_usd: float, pnl_pct: float,
        was_win: bool, amount: float = 0, horizon: TimeHorizon = TimeHorizon.FAST,
    ) -> None:
        self.m5_time_pools.on_capital_released(horizon, amount)
        self.m14_ratchet.on_profit(pnl_usd)
        self.m11_velocity.on_trade(amount)
        if self.m12_recovery.get_plan().active:
            self.m12_recovery.on_trade_result(pnl_usd)
        await self.update_state()

    async def get_full_status(self) -> dict:
        await self.update_state()
        state = self._account_state
        weather = await self.m10_weather.assess()
        velocity = await self.m11_velocity.get_current(state.trading_capital)
        ecosystem = await self.m17_ecosystem.assess()
        emotion = await self.m16_emotion.detect()
        recovery = self.m12_recovery.get_plan()

        return {
            "account": {
                "equity": state.total_equity,
                "starting": state.starting_balance,
                "growth": f"{state.growth_multiplier:.2f}x",
                "level": state.level.value,
                "unlock_pct": state.unlock_pct,
                "trading_capital": state.trading_capital,
                "available": state.available,
                "in_use": state.in_use,
                "locked_profits": state.locked_profits,
                "profit_floor": 0,  # Removed (#4) — tiered capital system
            },
            "pools": {
                "active": state.active_pool,
                "aplus_reserve": state.aplus_reserve,
                "emergency": state.emergency_reserve,
            },
            "risk_weather": {
                "level": weather.level.value,
                "score": weather.score,
                "multiplier": weather.allocation_multiplier,
                "warnings": weather.warnings,
            },
            "market_emotion": emotion.value,
            "capital_velocity": {
                "current": velocity.current_velocity,
                "status": velocity.status,
            },
            "ecosystem_health": {
                "score": ecosystem.score,
                "status": ecosystem.health_status,
            },
            "recovery": {
                "active": recovery.active,
                "progress": f"{recovery.progress_pct:.0f}%" if recovery.active else "N/A",
            },
            "next_level": self._get_next_level_info(state),
        }

    def _get_next_level_info(self, state: AccountState) -> dict:
        next_map = {
            "rookie": "proven", "proven": "veteran",
            "veteran": "elite", "elite": "master",
        }
        next_name = next_map.get(state.level.value, "")
        target = state.level_thresholds.get(next_name, 0)
        return {"target": target, "current_multiplier": state.growth_multiplier}

    async def _get_current_streak(self) -> int:
        try:
            trades = await self.db.fetch(
                "SELECT pnl FROM trade_history ORDER BY exit_time DESC LIMIT 20"
            )
            if not trades:
                return 0
            streak = 0
            first_dir = 1 if trades[0]["pnl"] > 0 else -1
            for t in trades:
                if (t["pnl"] > 0 and first_dir > 0) or (t["pnl"] <= 0 and first_dir < 0):
                    streak += first_dir
                else:
                    break
            return streak
        except Exception:
            return 0

    async def _get_daily_pnl_pct(self) -> float:
        pnl_mgr = self.services.get("pnl_manager")
        if pnl_mgr and hasattr(pnl_mgr, "current_pnl_pct"):
            return pnl_mgr.current_pnl_pct
        return 0.0

    async def _load_starting_balance(self) -> float:
        try:
            result = await self.db.fetch_one(
                "SELECT value FROM user_preferences WHERE key = 'starting_balance'"
            )
            return float(result["value"]) if result else 0.0
        except Exception:
            return 0.0

    async def _save_starting_balance(self, balance: float) -> None:
        try:
            await self.db.execute(
                "INSERT OR REPLACE INTO user_preferences (key, value) "
                "VALUES ('starting_balance', ?)",
                (str(balance),),
            )
        except Exception:
            pass

    # Profit floor system removed (#4) — replaced by tiered capital system
    # See src/fund_manager/tiered_capital.py
