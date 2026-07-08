"""Rule Engine — executes trades based on Claude's strategic plan.

Called every 45 seconds by strategy_worker.
Reads the cached StrategicPlan from LayerManager.
Checks if each setup matches the plan.
Calculates SL/TP from CURRENT price (never stale levels).
Enforces max_positions and max_per_coin.
Rounds quantity to instrument step size.
Only trades supported symbols.

This is the code that replaces both:
- BrainV2.evaluate_setups() (120 Claude calls/hour -> 0)
- The dumb bypass (caused 63 bad trades)
"""

import math

from src.core.log_context import ctx, get_did, new_trade_id
from src.core.logging import get_logger

log = get_logger("rule_engine")

# Single source of truth for supported symbols (no duplicate lists)
from src.config.constants import SUPPORTED_SYMBOLS as TESTNET_SUPPORTED

# Symbols that failed with "unsupported" — dynamically populated
_blacklisted_symbols: set = set()


class RuleEngine:
    """Decides which setups to trade based on Claude's strategic plan."""

    def __init__(self, services: dict, settings) -> None:
        self.services = services
        self.settings = settings

    async def evaluate_setups(
        self, setups: list, plan, current_positions: list
    ) -> list:
        """Evaluate setups against the strategic plan.

        Args:
            setups: scored + ensemble-voted setups from strategy pipeline
            plan: StrategicPlan from LayerManager (Claude's cached plan)
            current_positions: list of currently open positions

        Returns:
            list of (setup, trade_params) tuples ready for execution
        """
        log.info(f"RULE_EVAL_START | setups={len(setups)} open_pos={len(current_positions)} plan_ok={'Y' if plan and not getattr(plan, 'is_stale', True) else 'N'} | {ctx()}")

        if not plan or plan.is_stale:
            log.warning(
                "No strategic plan or plan is stale -- skipping trade evaluation"
            )
            return []

        # CHECK 0: Enforcer halt — performance too bad to trade
        enforcer = self.services.get("enforcer")
        if enforcer and hasattr(enforcer, "should_allow_trade"):
            allowed, reason = enforcer.should_allow_trade(leverage=1)
            if not allowed:
                log.warning(f"Trade evaluation HALTED by enforcer: {reason}")
                return []

        is_testnet = self.settings.bybit.testnet

        # Current position symbols
        position_symbols = set()
        for pos in current_positions:
            position_symbols.add(pos.symbol)

        total_positions = len(current_positions)
        approved = []
        _rejected_count = 0

        for setup_wrapper in setups:
            # Extract setup data
            setup = (
                setup_wrapper.scored_setup
                if hasattr(setup_wrapper, "scored_setup")
                else setup_wrapper
            )
            symbol = setup.raw_signal.symbol
            direction_enum = setup.raw_signal.direction
            direction = (
                direction_enum.value
                if hasattr(direction_enum, "value")
                else str(direction_enum)
            )
            score = setup.total_score
            consensus = (
                setup_wrapper.consensus_strength
                if hasattr(setup_wrapper, "consensus_strength")
                else "GOOD"
            )

            log.debug(f"RULE_CHECK | sym={symbol} dir={direction} score={score} cons={consensus} | {ctx()}")

            # CHECK 1: Supported symbol
            if is_testnet and symbol not in TESTNET_SUPPORTED:
                log.debug(f"RULE_REJECT | sym={symbol} reason=unsupported_symbol | {ctx()}")
                _rejected_count += 1
                continue
            if symbol in _blacklisted_symbols:
                log.debug(f"RULE_REJECT | sym={symbol} reason=blacklisted | {ctx()}")
                _rejected_count += 1
                continue

            # CHECK 1B: Mode 4 cooldown — reject trades on recently-closed symbols
            profit_sniper = self.services.get("profit_sniper")
            if profit_sniper and hasattr(profit_sniper, "is_in_cooldown") and profit_sniper.is_in_cooldown(symbol):
                remaining = profit_sniper.get_cooldown_remaining(symbol)
                log.info(
                    "Trade rejected: {sym} in Mode 4 cooldown ({r}s remaining)",
                    sym=symbol, r=remaining,
                )
                continue

            # CHECK 1B2: Per-(symbol, direction) reentry cooldown
            # (Issue 3, 2026-05-18). Replaces the legacy per-symbol
            # is_symbol_cooled_down + get_symbol_cooldown_remaining
            # pair removed in issue3/p3-3. The proposed direction is
            # available above (line 86) so the check is direction-aware
            # — closing AVAXUSDT Sell does NOT reject AVAXUSDT Buy here.
            coordinator = self.services.get("trade_coordinator")
            if coordinator and hasattr(coordinator, "is_reentry_blocked"):
                _blocked, _rem = coordinator.is_reentry_blocked(
                    symbol, direction,
                )
                if _blocked:
                    log.info(
                        "Trade rejected: {sym} {dir} in reentry cooldown ({r}s remaining)",
                        sym=symbol, dir=direction, r=_rem,
                    )
                    continue

            # CHECK 1C: REMOVED (per-coin-authority Phase 7, 2026-05-29).
            # This was a GLOBAL-regime DIRECTION gate (block Buy in a global
            # downtrend / Sell in a global uptrend at confidence > 0.60). It was
            # already DEAD: it read ``regime_detector._last_state`` — an attribute
            # that does not exist (the real one is ``_last_regime``) — so
            # regime_state was always None and the gate never fired; and the
            # RuleEngine is bypassed in production (manager.py RULE_ENGINE_INACTIVE).
            # It is REMOVED, not repaired: per-coin authority forbids a single
            # global label gating trade direction. Per-coin direction discipline
            # now lives in the scanner's _regime_aligns gate and the brain's
            # per-coin prompt; market-wide risk is the breadth SIZING brake only.

            # CHECK 2: Max positions
            if total_positions + len(approved) >= plan.max_positions:
                log.debug(
                    "Max positions ({max}) reached -- skipping {sym}",
                    max=plan.max_positions,
                    sym=symbol,
                )
                break

            # CHECK 3: Max per coin (always 1)
            if symbol in position_symbols:
                continue
            already_approved = {s.raw_signal.symbol for s, _ in approved}
            if symbol in already_approved:
                continue

            # CHECK 4: Plan allows this direction
            can_trade, reason = plan.can_trade_symbol(symbol, direction)
            if not can_trade:
                log.debug(
                    "Plan rejects {sym} {dir}: {reason}",
                    sym=symbol,
                    dir=direction,
                    reason=reason,
                )
                continue

            # CHECK 5: Get coin-specific parameters from plan
            directive = plan.get_directive(symbol)

            # CHECK 6: Calculate SL/TP from CURRENT price
            try:
                market_service = self.services.get("market_service")
                ticker = await market_service.get_ticker(symbol)
                if not ticker or not ticker.last_price or ticker.last_price <= 0:
                    continue

                current_price = ticker.last_price
                sl_pct = directive.sl_pct / 100  # e.g., 2.0 -> 0.02
                tp_pct = directive.tp_pct / 100

                if direction == "Buy":
                    sl_price = round(current_price * (1 - sl_pct), 8)
                    tp_price = round(current_price * (1 + tp_pct), 8)
                else:  # Sell
                    sl_price = round(current_price * (1 + sl_pct), 8)
                    tp_price = round(current_price * (1 - tp_pct), 8)

                # VERIFY: SL and TP make sense
                if direction == "Buy" and sl_price >= current_price:
                    sl_price = round(current_price * 0.975, 8)
                if direction == "Buy" and tp_price <= current_price:
                    tp_price = round(current_price * 1.025, 8)
                if direction == "Sell" and sl_price <= current_price:
                    sl_price = round(current_price * 1.025, 8)
                if direction == "Sell" and tp_price >= current_price:
                    tp_price = round(current_price * 0.975, 8)

            except Exception as e:
                log.error(
                    "Price/SL/TP calculation failed for {sym}: {err}",
                    sym=symbol,
                    err=str(e),
                )
                continue

            # CHECK 7: Calculate quantity with proper rounding
            try:
                amount = 0.0  # no fallback — Fund Manager must approve
                leverage = directive.leverage

                fund_manager = self.services.get("fund_manager")
                if fund_manager:
                    try:
                        sizing = await fund_manager.get_sizing_decision(
                            symbol=symbol,
                            side=direction,
                            setup_score=score,
                            setup_grade=setup.grade,
                            consensus_strength=consensus,
                            strategy_name=setup.raw_signal.strategy_name,
                            strategy_category=getattr(
                                setup.raw_signal, "strategy_category", "unknown"
                            ),
                            expected_hold_minutes=directive.max_hold_minutes,
                            stop_loss_pct=directive.sl_pct,
                        )
                        if sizing.final_amount_usd > 0:
                            amount = sizing.final_amount_usd
                            leverage = sizing.final_leverage
                        else:
                            log.info("Fund Manager rejected {sym}: {reason}",
                                     sym=symbol, reason=getattr(sizing, 'reasoning', 'amount=0'))
                            continue  # Skip — no fallback
                    except Exception as _fm_err:
                        log.warning(
                            "Fund Manager failed for {sym}: {err}",
                            sym=symbol,
                            err=str(_fm_err),
                        )
                        # Fallback: 3% of equity if FM entirely unavailable
                        account_svc = self.services.get("account_service")
                        if account_svc:
                            try:
                                acc = await account_svc.get_wallet_balance()
                                amount = acc.total_equity * 0.03
                            except Exception:
                                continue  # No sizing available — skip
                else:
                    account_svc = self.services.get("account_service")
                    if account_svc:
                        try:
                            acc = await account_svc.get_wallet_balance()
                            amount = acc.total_equity * 0.03
                        except Exception:
                            pass

                qty = (amount * leverage) / current_price

                # Round to instrument step size
                instrument_service = self.services.get("instrument_service")
                if instrument_service:
                    try:
                        info = await instrument_service.get_instrument_info(
                            symbol
                        )
                        if info and info.qty_step > 0:
                            qty = (
                                math.floor(qty / info.qty_step) * info.qty_step
                            )
                    except Exception:
                        pass

                if qty <= 0:
                    continue

            except Exception as e:
                log.error(
                    "Sizing failed for {sym}: {err}", sym=symbol, err=str(e)
                )
                continue

            # BUILD trade parameters
            trade_params = {
                "symbol": symbol,
                "direction": direction,
                "qty": qty,
                "leverage": leverage,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "max_hold_minutes": directive.max_hold_minutes,
                "trailing_activation_pct": plan.trailing_activation_pct,
                "amount_usd": amount,
                "score": score,
                "consensus": consensus,
                "strategy_name": setup.raw_signal.strategy_name,
                "strategy_category": getattr(
                    setup.raw_signal, "strategy_category", "unknown"
                ),
                "current_price": current_price,
                "plan_risk_level": plan.risk_level,
                "directive_reason": directive.reason,
            }

            approved.append((setup, trade_params))
            tid = new_trade_id(symbol)
            from src.core.utils import format_price
            log.info(
                f"RULE_PASS | sym={symbol} dir={direction} qty={qty:.4f} "
                f"amt=${amount:.0f} lev={leverage} sl=${format_price(sl_price)} tp=${format_price(tp_price)} | {ctx()}"
            )
            log.info(
                "Rule engine APPROVED: {dir} {sym} qty={qty:.4f} "
                "${amt:.0f} at {lev}x SL=${sl} TP=${tp} "
                "hold={hold}min score={score} {cons}",
                dir=direction,
                sym=symbol,
                qty=qty,
                amt=amount,
                lev=leverage,
                sl=format_price(sl_price),
                tp=format_price(tp_price),
                hold=directive.max_hold_minutes,
                score=score,
                cons=consensus,
            )

        log.info(f"RULE_EVAL_END | approved={len(approved)} rejected={_rejected_count} total={len(setups)} | {ctx()}")
        return approved

    @staticmethod
    def blacklist_symbol(symbol: str, reason: str = "") -> None:
        """Add a symbol to the blacklist after a failed order."""
        _blacklisted_symbols.add(symbol)
        log.warning(
            "Symbol blacklisted: {sym} -- {reason}", sym=symbol, reason=reason
        )

    @staticmethod
    def get_blacklist() -> set:
        return _blacklisted_symbols.copy()
