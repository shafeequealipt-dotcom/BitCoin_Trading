"""Brain v2: enhanced Claude Brain for the 4-layer strategy architecture.

Receives pre-filtered, pre-scored, consensus-approved setups and makes
the final execute/skip/modify decision. Single Claude call per cycle
for cost efficiency.
"""

import json

from src.alerts.alert_manager import AlertManager
from src.core.utils import format_price
from src.brain.claude_client import ClaudeClient
from src.brain.cost_tracker import CostTracker
from src.brain.decision_parser import DecisionParser
from src.brain.prompts.setup_review import (
    SETUP_REVIEW_PROMPT,
    SETUP_REVIEW_SYSTEM_PROMPT,
)
from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.types import OrderType, Side
from src.core.utils import generate_id, now_utc
from src.risk.risk_manager import RiskManager
from src.strategies.models.regime_types import RegimeState
from src.strategies.models.signal_types import EnsembleResult, TradeDecision
from src.strategies.pnl_manager import DailyPnLManager
from src.strategies.scanner import MarketScanner
from src.strategies.smart_leverage import SmartLeverage
from src.trading.services.account_service import AccountService
from src.trading.services.order_service import OrderService
from src.trading.services.position_service import PositionService

log = get_logger("brain")


class BrainV2:
    """Enhanced Brain that works with the 4-layer strategy architecture.

    Args:
        settings: Application settings.
        claude_client: For calling Claude API.
        cost_tracker: For budget enforcement.
        decision_parser: For JSON extraction.
        order_service: For placing orders.
        position_service: For fetching positions.
        account_service: For account balance.
        risk_manager: For trade validation.
        alert_manager: For notifications.
        pnl_manager: For daily PnL mode.
        smart_leverage: For leverage calculation.
        scanner: For coin tier lookups.
    """

    def __init__(
        self,
        settings: Settings,
        db=None,
        claude_client: ClaudeClient | None = None,
        cost_tracker: CostTracker | None = None,
        decision_parser: DecisionParser | None = None,
        order_service: OrderService | None = None,
        position_service: PositionService | None = None,
        account_service: AccountService | None = None,
        risk_manager: RiskManager | None = None,
        alert_manager: AlertManager | None = None,
        pnl_manager: DailyPnLManager | None = None,
        smart_leverage: SmartLeverage | None = None,
        scanner: MarketScanner | None = None,
        trade_coordinator=None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.claude_client = claude_client
        self.cost_tracker = cost_tracker
        self.decision_parser = decision_parser or DecisionParser()
        self.order_service = order_service
        self.position_service = position_service
        self.account_service = account_service
        self.risk_manager = risk_manager
        self.alert_manager = alert_manager
        self.pnl_manager = pnl_manager
        self.smart_leverage = smart_leverage
        self.scanner = scanner
        self.coordinator = trade_coordinator
        self.fund_manager = None  # Set by WorkerManager after creation

    async def evaluate_setups(
        self,
        setups: list[EnsembleResult],
        regime: RegimeState,
    ) -> list[TradeDecision]:
        """Evaluate pre-filtered setups with Claude Brain.

        Sends up to max_setups_to_brain setups in a single Claude call.

        Args:
            setups: Consensus-approved setups from Layer 3.
            regime: Current market regime.

        Returns:
            List of TradeDecision (execute/skip/modify).
        """
        if not setups:
            return []

        max_setups = self.settings.strategy_engine.max_setups_to_brain
        setups = setups[:max_setups]

        if not self.claude_client or not self.cost_tracker:
            log.warning("Brain v2: Claude client not available, skipping evaluation")
            return []

        if not self.cost_tracker.can_afford_call():
            log.warning("Brain v2: daily budget exceeded")
            return []

        # Build context
        positions_section = "No open positions"
        equity = 0.0
        available = 0.0
        exposure_pct = 0.0

        if self.account_service:
            try:
                account = await self.account_service.get_wallet_balance()
                equity = account.total_equity
                available = account.available_balance
                if equity > 0:
                    exposure_pct = (account.used_margin / equity) * 100
            except Exception:
                pass

        if self.position_service:
            try:
                positions = await self.position_service.get_positions()
                if positions:
                    lines = []
                    for p in positions:
                        side = "LONG" if p.side == Side.BUY else "SHORT"
                        lines.append(
                            f"  {p.symbol} {side} {p.leverage}x | "
                            f"Entry: ${format_price(p.entry_price)} | "
                            f"PnL: ${p.unrealized_pnl:+,.2f}"
                        )
                    positions_section = "\n".join(lines)
            except Exception:
                pass

        # Build setups section
        setup_lines: list[str] = []
        for i, setup in enumerate(setups):
            sig = setup.scored_setup.raw_signal
            sc = setup.scored_setup
            direction = "BUY" if sig.direction == Side.BUY else "SELL"
            setup_lines.append(
                f"### Setup {i}: {sig.symbol} {direction}\n"
                f"Strategy: {sig.strategy_name} ({sig.strategy_category})\n"
                f"Score: {sc.total_score:.0f}/100 (Grade: {sc.grade})\n"
                f"  Base={sc.base_score:.0f} Confluence={sc.confluence_score:.0f} "
                f"Context={sc.context_score:.0f} Quality={sc.quality_score:.0f}\n"
                f"Consensus: {setup.consensus_strength} "
                f"(buy={setup.buy_votes:.1f} sell={setup.sell_votes:.1f})\n"
                f"Entry: ${format_price(sig.entry_price)} | SL: ${format_price(sig.suggested_stop_loss)} "
                f"| TP: ${format_price(sig.suggested_take_profit)}\n"
                f"Timeframe: {sig.timeframe}"
            )

        pnl_mode = "NORMAL"
        pnl_pct = 0.0
        if self.pnl_manager:
            mode = self.pnl_manager.get_current_mode()
            pnl_mode = mode["mode"]
            pnl_pct = self.pnl_manager.current_pnl_pct

        # Fetch Fear & Greed for brain context
        fg_value = 50
        fg_class = "neutral"
        market_sentiment = "neutral"
        try:
            fg_svc = getattr(self, "_fear_greed", None)
            if not fg_svc and hasattr(self, "scanner") and self.scanner:
                fg_svc = getattr(self.scanner, "_services", {}).get("fear_greed")
            if fg_svc:
                fg_data = await fg_svc.get_latest()
                if fg_data:
                    fg_value = fg_data.value
                    fg_class = getattr(fg_data, "classification", "neutral")
                    if fg_value < 20:
                        market_sentiment = "EXTREME FEAR — strong contrarian BUY signal"
                    elif fg_value < 35:
                        market_sentiment = "FEAR — lean bullish (contrarian)"
                    elif fg_value > 80:
                        market_sentiment = "EXTREME GREED — strong contrarian SELL signal"
                    elif fg_value > 65:
                        market_sentiment = "GREED — lean bearish (contrarian)"
                    else:
                        market_sentiment = "neutral"
        except Exception:
            pass

        prompt = SETUP_REVIEW_PROMPT.format(
            regime=regime.regime.value,
            regime_confidence=regime.confidence,
            fear_greed_value=fg_value,
            fear_greed_class=fg_class,
            market_sentiment=market_sentiment,
            daily_pnl_pct=pnl_pct,
            pnl_mode=pnl_mode,
            positions_section=positions_section,
            equity=f"{equity:,.2f}",
            available=f"{available:,.2f}",
            exposure_pct=exposure_pct,
            setups_section="\n\n".join(setup_lines),
        )

        try:
            response = await self.claude_client.send_message(
                prompt=prompt,
                system_prompt=SETUP_REVIEW_SYSTEM_PROMPT,
            )
            # ClaudeClient returns dict {"text": ...}, ClaudeCodeClient returns str
            if isinstance(response, dict):
                cost_usd = response.get("cost_usd", 0.0)
                raw_text = response.get("text", "")
            else:
                cost_usd = 0.0
                raw_text = str(response)
            data = self.decision_parser._extract_json(raw_text)
            log.info(
                "Brain v2 raw response keys: {keys} | text preview: {preview}",
                keys=list(data.keys()) if isinstance(data, dict) else type(data).__name__,
                preview=raw_text[:200].replace("\n", " "),
            )
        except Exception as e:
            log.error("Brain v2 call failed: {err}", err=str(e))
            return []

        decisions: list[TradeDecision] = []

        # Handle multiple response formats
        raw_decisions = []
        if isinstance(data, dict):
            raw_decisions = data.get("decisions", [])
            if not raw_decisions and data.get("action"):
                raw_decisions = [data]
        elif isinstance(data, list):
            raw_decisions = data

        if not raw_decisions:
            log.warning(
                "Brain v2: No decisions extracted. Data type={t}, keys={k}",
                t=type(data).__name__,
                k=list(data.keys()) if isinstance(data, dict) else "N/A",
            )
            return []

        def _safe_float(val, default=0.0):
            """Convert to float safely — handles None, empty strings, non-numeric."""
            if val is None or val == "" or val == "None":
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        for raw in raw_decisions:
            try:
                idx = raw.get("setup_index", 0)
                if idx >= len(setups):
                    idx = 0

                setup = setups[idx]
                sig = setup.scored_setup.raw_signal
                action = raw.get("action", "execute").lower()
                if action not in ("execute", "skip", "modify"):
                    action = "execute"

                # PAPER TRADING OVERRIDE: force execute if Brain says skip
                is_paper = self.settings.bybit.testnet
                if action == "skip" and is_paper:
                    log.warning(
                        "Brain SKIP OVERRIDE: {sym} — forcing execute (paper mode). "
                        "Brain reason: {reason}",
                        sym=sig.symbol,
                        reason=raw.get("reasoning", "unknown")[:80],
                    )
                    action = "execute"
                    # Use conservative defaults for overridden trades
                    if not raw.get("leverage"):
                        raw["leverage"] = 2
                    if not raw.get("stop_loss_pct") and not raw.get("stop_loss"):
                        raw["stop_loss_pct"] = 2.5
                    if not raw.get("take_profit_pct") and not raw.get("take_profit_1"):
                        raw["take_profit_pct"] = 4.0

                leverage = max(1, min(int(_safe_float(raw.get("leverage", 3), 3)), 5))

                # Parse SL/TP — handle absolute prices OR percentages
                direction = raw.get("direction", sig.direction.value if hasattr(sig.direction, "value") else "Buy")
                is_buy = direction in ("Buy", "buy", "BUY")
                entry = sig.entry_price if sig.entry_price > 0 else 1

                # Try absolute price first, then percentage, then defaults
                sl = _safe_float(raw.get("stop_loss_price"), 0) or _safe_float(raw.get("stop_loss"), 0)
                tp1 = _safe_float(raw.get("target_price"), 0) or _safe_float(raw.get("take_profit_1"), 0)

                if sl == 0:
                    sl_pct = _safe_float(raw.get("stop_loss_pct"), 2.5)
                    sl = entry * (1 - sl_pct / 100) if is_buy else entry * (1 + sl_pct / 100)

                if tp1 == 0:
                    tp_pct = _safe_float(raw.get("take_profit_pct"), 4.0)
                    tp1 = entry * (1 + tp_pct / 100) if is_buy else entry * (1 - tp_pct / 100)

                # Size tier mapping
                size_tier = raw.get("size_tier", "medium")
                SIZE_TIER_PCT = {"high": 10.0, "medium": 5.0, "low": 3.0, "micro": 1.5}
                size_pct = SIZE_TIER_PCT.get(size_tier, _safe_float(raw.get("position_size_pct", 5), 5))
                size_pct = max(1.0, min(size_pct, 12.0))

                max_hold = int(_safe_float(raw.get("max_hold_minutes", 120), 120))
                max_hold = max(5, min(max_hold, 1440))

                decision = TradeDecision(
                    ensemble_result=setup,
                    action=action,
                    leverage=leverage,
                    stop_loss=sl,
                    take_profit_1=tp1,
                    take_profit_2=None,
                    position_size_pct=size_pct,
                    reasoning=raw.get("reasoning", ""),
                    risk_notes=data.get("market_assessment", ""),
                    claude_cost_usd=cost_usd / max(len(raw_decisions), 1),
                )
                decisions.append(decision)

                # Build TradePlan for watchdog monitoring
                from src.core.trade_plan import TradePlan
                trade_plan = TradePlan(
                    symbol=sig.symbol,
                    direction=direction,
                    entry_price=entry,
                    target_price=tp1,
                    stop_loss_price=sl,
                    max_hold_minutes=max_hold,
                    trailing_activation_pct=_safe_float(raw.get("trailing_activation_pct", 0.5), 0.5),
                    trailing_distance_pct=_safe_float(raw.get("trailing_distance_pct", 50), 50),
                    size_tier=size_tier,
                    risk_reward_ratio=_safe_float(raw.get("risk_reward_ratio", 2.0), 2.0),
                    reasoning=raw.get("reasoning", ""),
                )
                # Attach plan to decision for _execute_trade to use
                decision._trade_plan = trade_plan

                await self._log_decision(decision, setup, regime, cost_usd)

                if action == "execute":
                    log.info(
                        "Brain PLAN: {sym} {dir} lev={lev}x SL=${sl} TP=${tp} "
                        "hold={hold}min tier={tier}",
                        sym=sig.symbol, dir=direction,
                        lev=leverage, sl=format_price(sl), tp=format_price(tp1),
                        hold=max_hold, tier=size_tier,
                    )
                    await self._execute_trade(decision)
                else:
                    log.info(
                        "Brain SKIP: {sym} — {reason}",
                        sym=sig.symbol,
                        reason=raw.get("reasoning", "no reason")[:100],
                    )
            except Exception as e:
                log.error(
                    "Brain v2: Failed to parse decision {idx}: {err}",
                    idx=raw.get("setup_index", "?"), err=str(e),
                )

        log.info(
            "Brain v2: evaluated {n} setups, {ex} executed, cost=${c:.4f}",
            n=len(setups),
            ex=sum(1 for d in decisions if d.action == "execute"),
            c=cost_usd,
        )
        return decisions

    async def _log_decision(self, decision, setup, regime, cost_usd) -> None:
        """Save every brain decision to database."""
        try:
            import json as _json
            sig = setup.scored_setup.raw_signal
            await self.db.execute(
                "INSERT INTO brain_decisions "
                "(prompt_hash, action_taken, decision_json, cost_usd, trigger, created_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (
                    generate_id("bd"),
                    decision.action,
                    _json.dumps({
                        "symbol": sig.symbol, "direction": sig.direction.value,
                        "strategy": sig.strategy_name, "score": setup.scored_setup.total_score,
                        "consensus": setup.consensus_strength,
                        "leverage": decision.leverage, "reasoning": decision.reasoning,
                        "regime": regime.regime.value if regime else "unknown",
                    }),
                    cost_usd,
                    "strategy_engine",
                ),
            )
        except Exception as e:
            log.error("Failed to log brain decision: {err}", err=str(e))

    async def _execute_trade(self, decision: TradeDecision) -> None:
        """Execute a trade decision."""
        sig = decision.ensemble_result.scored_setup.raw_signal

        if self.risk_manager:
            try:
                valid, issues = await self.risk_manager.validate_trade(
                    symbol=sig.symbol,
                    side=sig.direction,
                    qty=0,
                    stop_loss=decision.stop_loss,
                    leverage=decision.leverage,
                )
                if not valid:
                    log.warning(
                        "Brain v2: trade rejected by risk manager: {issues}",
                        issues="; ".join(issues),
                    )
                    decision.action = "skip"
                    decision.risk_notes = f"Risk rejected: {'; '.join(issues)}"
                    return
            except Exception as e:
                log.error("Risk validation failed: {err}", err=str(e))

        if not self.order_service:
            log.warning("Brain v2: order service not available")
            return

        try:
            # Get current price
            ticker = await self.position_service._client.call(
                "get_tickers", category="linear", symbol=sig.symbol,
            ) if self.position_service else None
            price = sig.entry_price
            if ticker and ticker.get("list"):
                price = float(ticker["list"][0].get("lastPrice", sig.entry_price))

            # Fund Manager sizing (replaces flat equity * pct)
            if self.fund_manager:
                sc = decision.ensemble_result.scored_setup
                sl_pct = abs(sig.entry_price - decision.stop_loss) / sig.entry_price * 100 if sig.entry_price > 0 else 2.0
                sizing = await self.fund_manager.get_sizing_decision(
                    symbol=sig.symbol,
                    side=sig.direction.value,
                    setup_score=sc.total_score,
                    setup_grade=sc.grade,
                    consensus_strength=decision.ensemble_result.consensus_strength,
                    strategy_name=sig.strategy_name,
                    strategy_category=getattr(sig, "strategy_category", "default"),
                    expected_hold_minutes=30,
                    stop_loss_pct=sl_pct,
                )
                if sizing.final_amount_usd <= 0:
                    log.warning(
                        "Brain v2: Fund Manager rejected {sym}: {reason}",
                        sym=sig.symbol, reason=sizing.reasoning[:100],
                    )
                    decision.action = "skip"
                    decision.risk_notes = f"Fund manager: {sizing.reasoning}"
                    return
                position_usd = sizing.final_amount_usd
                decision.leverage = sizing.final_leverage
            else:
                # Fallback to original behavior
                equity = 10000
                if self.account_service:
                    try:
                        acc = await self.account_service.get_wallet_balance()
                        equity = acc.total_equity
                    except Exception:
                        pass
                position_usd = equity * (decision.position_size_pct / 100)

            qty = (position_usd * decision.leverage) / price if price > 0 else 0.001

            order = await self.order_service.place_order(
                symbol=sig.symbol,
                side=sig.direction,
                order_type=OrderType.MARKET,
                qty=qty,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit_1,
                leverage=decision.leverage,
                purpose="layer3_entry",
            )
            log.info(
                "Brain v2: executed {sym} {dir} lev={lev}x SL={sl} TP={tp}",
                sym=sig.symbol,
                dir=sig.direction.value,
                lev=decision.leverage,
                sl=decision.stop_loss,
                tp=decision.take_profit_1,
            )

            # Record strategy trade for optimizer feedback
            from src.core.trade_recorder import record_strategy_trade
            sc = decision.ensemble_result.scored_setup
            await record_strategy_trade(
                self.db,
                symbol=sig.symbol,
                strategy_name=sig.strategy_name if hasattr(sig, "strategy_name") else "brain_v2",
                direction=sig.direction.value,
                score=sc.total_score if hasattr(sc, "total_score") else 0,
                ensemble_strength=decision.ensemble_result.consensus_strength if hasattr(decision, "ensemble_result") else "",
                ensemble_votes_for=getattr(decision.ensemble_result, "buy_votes", 0),
                ensemble_votes_against=getattr(decision.ensemble_result, "sell_votes", 0),
                leverage_used=decision.leverage,
                regime=getattr(decision, "regime", "unknown"),
                source="brain_v2",
            )

            # Register trade + plan with coordinator
            if self.coordinator:
                trade_cat = sig.strategy_category if hasattr(sig, "strategy_category") else "default"
                self.coordinator.register_trade(
                    symbol=sig.symbol,
                    strategy_category=trade_cat,
                    strategy_name=sig.strategy_name if hasattr(sig, "strategy_name") else "",
                    entry_price=price,
                    side=sig.direction.value,
                    source="brain_v2",
                )
                # Store trade plan for watchdog monitoring
                plan = getattr(decision, "_trade_plan", None)
                if plan and hasattr(self.coordinator, "register_trade_plan"):
                    plan.entry_price = price
                    self.coordinator.register_trade_plan(sig.symbol, plan)
        except Exception as e:
            log.error(
                "Brain v2: order execution failed for {sym}: {err}",
                sym=sig.symbol, err=str(e),
            )
