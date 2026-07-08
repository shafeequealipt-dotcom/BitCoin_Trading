"""Message templates for every alert type. Uses HTML formatting for Telegram."""

from typing import TYPE_CHECKING

from src.alerts.formatter import AlertFormatter as F
from src.core.types import AlertLevel, BrainDecision, Order, Position, Side, Signal, WatchdogDecision
from src.core.utils import now_utc

if TYPE_CHECKING:
    from src.core.price_formatter import PriceFormatter


class AlertTemplates:
    """Pre-built message templates for Telegram alerts."""

    def __init__(self, price_formatter: "PriceFormatter | None" = None) -> None:
        # Canonical PriceFormatter (exact exchange tick-size precision),
        # injected by AlertManager. The PRICE-rendering methods are instance
        # methods that format through :meth:`_price`; the remaining template
        # methods are stateless and stay ``@staticmethod``. ``None`` is fully
        # supported — _price falls back to AlertFormatter's magnitude-aware
        # formatting (tests/unwired callers still render sub-cent correctly).
        self._pf = price_formatter

    def _price(self, price, symbol: str = "") -> str:
        """Render a price at exact exchange tick precision (magnitude fallback).

        $-prefixed. Callers guard ``None`` prices before calling.
        """
        if self._pf is not None:
            return self._pf.format(price, symbol)
        return F.format_price(price, symbol)

    def trade_executed(self, order: Order, account_balance: float | None = None) -> str:
        side_str = F.format_side(order.side)
        ts = F.format_timestamp()
        sl_str = self._price(order.stop_loss, order.symbol) if order.stop_loss else "Not set"
        tp_str = self._price(order.take_profit, order.symbol) if order.take_profit else "Not set"
        bal = f"\n\U0001f4b0 Balance: {F.format_currency(account_balance)}" if account_balance else ""

        return (
            "\u2705 <b>TRADE EXECUTED</b>\n\n"
            f"{side_str} <b>{order.symbol}</b>\n\n"
            f"\U0001f4ca <b>Order Details</b>\n"
            f"\u2022 Type: {order.order_type.value}\n"
            f"\u2022 Qty: {order.qty}\n"
            f"\u2022 Price: {self._price(order.price, order.symbol)}\n\n"
            f"\U0001f6e1\ufe0f <b>Risk Management</b>\n"
            f"\u2022 Stop Loss: {sl_str}\n"
            f"\u2022 Take Profit: {tp_str}\n"
            f"{bal}\n"
            f"\U0001f550 {ts}"
        )

    def position_closed(self, symbol: str, side: Side, entry_price: float, exit_price: float, pnl: float, pnl_pct: float) -> str:
        pnl_str = F.format_pnl(pnl, pnl_pct)
        side_name = "Long" if side == Side.BUY else "Short"
        return (
            "\U0001f4e4 <b>POSITION CLOSED</b>\n\n"
            f"<b>{symbol}</b> {side_name}\n"
            f"\u2022 Entry: {self._price(entry_price, symbol)}\n"
            f"\u2022 Exit: {self._price(exit_price, symbol)}\n"
            f"\u2022 PnL: {pnl_str}\n\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    @staticmethod
    def signal_detected(signal: Signal) -> str:
        sig_str = F.format_signal(signal.signal_type)
        conf_str = F.format_confidence(signal.confidence)
        components = signal.components or {}
        comp_lines = []
        for k, v in list(components.items())[:5]:
            comp_lines.append(f"\u2022 {k}: {v}")
        comp_section = "\n".join(comp_lines) if comp_lines else "\u2022 N/A"

        return (
            "\U0001f4e1 <b>SIGNAL DETECTED</b>\n\n"
            f"{sig_str} \u2014 <b>{signal.symbol}</b>\n"
            f"Confidence: {conf_str}\n\n"
            f"\U0001f4cb <b>Components</b>\n"
            f"{comp_section}\n\n"
            f"\u23f3 Awaiting Brain analysis...\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    @staticmethod
    def brain_decision(decision: BrainDecision, trigger: str, cost_usd: float) -> str:
        if decision.action == "hold":
            action_str = "\u23f8\ufe0f HOLD"
        elif decision.action == "buy":
            action_str = f"\U0001f7e2 BUY <b>{decision.symbol}</b>"
        elif decision.action == "sell":
            action_str = f"\U0001f534 SELL <b>{decision.symbol}</b>"
        elif decision.action == "close":
            action_str = f"\U0001f4e4 CLOSE <b>{decision.symbol}</b>"
        else:
            action_str = decision.action.upper()

        conf_str = F.format_confidence(decision.confidence)
        reasoning = F.truncate(decision.reasoning, 200) if decision.reasoning else "No reasoning provided"
        risk = F.truncate(decision.risk_notes, 150) if decision.risk_notes else ""
        risk_section = f"\n\n\u26a0\ufe0f <b>Risk Notes</b>\n{risk}" if risk else ""

        return (
            "\U0001f9e0 <b>BRAIN DECISION</b>\n\n"
            f"Action: {action_str}\n"
            f"Confidence: {conf_str}\n"
            f"Trigger: {trigger}\n\n"
            f"\U0001f4ad <b>Reasoning</b>\n{reasoning}"
            f"{risk_section}\n\n"
            f"\U0001f4b5 API Cost: ${cost_usd:.4f}\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    @staticmethod
    def error_alert(component: str, error_message: str, severity: AlertLevel) -> str:
        emoji = "\U0001f534" if severity == AlertLevel.CRITICAL else "\u26a0\ufe0f"
        return (
            f"{emoji} <b>ERROR</b>\n\n"
            f"Component: {component}\n"
            f"Severity: {severity.value.upper()}\n\n"
            f"{F.truncate(error_message, 300)}\n\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    @staticmethod
    def daily_summary(data: dict) -> str:
        pnl = data.get("total_pnl", 0)
        pnl_str = F.format_pnl(pnl, data.get("total_pnl_pct", 0))
        trades = data.get("trades_count", 0)
        wins = data.get("wins", 0)
        losses = trades - wins
        win_rate = f"{wins/trades*100:.0f}%" if trades > 0 else "N/A"

        positions_lines = []
        for p in data.get("positions", []):
            positions_lines.append(f"\u2022 {p.get('symbol', '?')}: {F.format_pnl(p.get('pnl', 0))}")
        pos_section = "\n".join(positions_lines) if positions_lines else "\u2022 None"

        fg = data.get("fear_greed", {})
        fg_str = F.format_fear_greed(fg.get("value", 50), fg.get("classification", "N/A")) if fg else "N/A"

        brain_calls = data.get("brain_calls", 0)
        brain_cost = data.get("brain_cost", 0)

        # Win-rate enhancement Phase E (2026-07-07) \u2014 expectancy, fee drag,
        # entry-quality filter counts. expectancy_usd defaults to 0.0 (safe
        # when trades=0); entry_quality_* default to None so an
        # unavailable counter renders "N/A" instead of a misleading zero.
        expectancy = data.get("expectancy_usd", 0.0)
        fee_drag = data.get("fee_drag_est_usd", 0.0)
        eq_passed = data.get("entry_quality_passed")
        eq_rejected = data.get("entry_quality_rejected")
        eq_by_reason = data.get("entry_quality_by_reason") or {}
        if eq_passed is None and eq_rejected is None:
            eq_line = "N/A (apex_gate unavailable)"
        else:
            eq_reason_str = ", ".join(
                f"{k.replace('entry_quality_', '')}={v}"
                for k, v in eq_by_reason.items() if v
            ) or "none"
            eq_line = (
                f"{eq_passed or 0} passed / {eq_rejected or 0} rejected "
                f"({eq_reason_str})"
            )

        return (
            f"\U0001f4ca <b>DAILY SUMMARY</b>\n"
            f"\U0001f4c5 {now_utc().strftime('%B %d, %Y')}\n\n"
            f"\U0001f4b0 <b>Performance</b>\n"
            f"\u2022 Total PnL: {pnl_str}\n"
            f"\u2022 Trades: {trades} ({wins}W / {losses}L)\n"
            f"\u2022 Win Rate: {win_rate}\n"
            f"\u2022 Expectancy/trade: {F.format_pnl(expectancy)}\n"
            f"\u2022 Est. fee drag: ${fee_drag:.2f}\n\n"
            f"\U0001f4c8 <b>Open Positions</b>\n{pos_section}\n\n"
            f"\U0001f3af <b>Entry-Quality Filters</b>\n"
            f"\u2022 {eq_line}\n\n"
            f"\U0001f30d <b>Market</b>\n"
            f"\u2022 Fear & Greed: {fg_str}\n\n"
            f"\U0001f9e0 <b>Brain</b>\n"
            f"\u2022 Decisions: {brain_calls}\n"
            f"\u2022 API Cost: ${brain_cost:.4f}\n\n"
            f"\u2699\ufe0f Workers: {data.get('workers_running', 0)}/{data.get('workers_total', 0)}\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    @staticmethod
    def worker_crash(worker_name: str, error: str, restart_count: int, max_restarts: int) -> str:
        if restart_count >= max_restarts:
            return (
                f"\U0001f534 <b>WORKER DOWN</b>\n\n"
                f"Worker <b>{worker_name}</b> STOPPED\n"
                f"Exceeded max restarts ({restart_count}/{max_restarts})\n"
                f"Error: {F.truncate(error, 200)}\n\n"
                f"Manual intervention may be needed.\n"
                f"\U0001f550 {F.format_timestamp()}"
            )
        return (
            f"\u26a0\ufe0f <b>WORKER ALERT</b>\n\n"
            f"Worker <b>{worker_name}</b> crashed\n"
            f"Error: {F.truncate(error, 200)}\n"
            f"Restart: {restart_count}/{max_restarts} (auto-recovering)\n\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    @staticmethod
    def risk_warning(warning_type: str, details: dict) -> str:
        detail_lines = [f"\u2022 {k}: {v}" for k, v in details.items()]
        return (
            f"\U0001f6a8 <b>RISK WARNING</b>\n\n"
            f"\u26a0\ufe0f {warning_type}\n\n"
            f"{chr(10).join(detail_lines)}\n\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    def price_alert(self, symbol: str, current_price: float, change_pct: float, timeframe_minutes: int) -> str:
        emoji = "\U0001f4c8" if change_pct > 0 else "\U0001f4c9"
        direction = "+" if change_pct > 0 else ""
        return (
            f"\U0001f680 <b>PRICE SPIKE</b>\n\n"
            f"<b>{symbol}</b> moved {emoji} {direction}{change_pct:.1f}% in {timeframe_minutes} minutes!\n"
            f"Current: {self._price(current_price, symbol)}\n\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    @staticmethod
    def system_startup(mode: str, symbols: list[str], workers: int) -> str:
        mode_str = "Paper Trading (Testnet)" if mode == "paper" else "LIVE TRADING"
        return (
            f"\U0001f7e2 <b>SYSTEM STARTED</b>\n\n"
            f"Mode: {mode_str}\n"
            f"Symbols: {', '.join(symbols[:5])}\n"
            f"Workers: {workers} active\n\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    @staticmethod
    def system_shutdown(reason: str) -> str:
        return (
            f"\U0001f534 <b>SYSTEM STOPPED</b>\n\n"
            f"Reason: {reason}\n\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    def watchdog_alert(
        self,
        position: Position,
        current_price: float,
        pnl_pct: float,
        warnings: list[str],
        severity: AlertLevel,
    ) -> str:
        side_str = F.format_side(position.side)
        pnl_str = F.format_pnl(position.unrealized_pnl, pnl_pct)
        severity_emoji = "\U0001f6a8" if severity == AlertLevel.CRITICAL else "\u26a0\ufe0f"
        warning_lines = "\n".join(f"\u2022 {w}" for w in warnings)
        sl_str = self._price(position.stop_loss, position.symbol) if position.stop_loss else "None"

        return (
            f"{severity_emoji} <b>POSITION WARNING</b>\n\n"
            f"{side_str} <b>{position.symbol}</b>\n"
            f"Entry: {self._price(position.entry_price, position.symbol)}  \u2192  "
            f"Now: {self._price(current_price, position.symbol)}\n"
            f"PnL: {pnl_str}\n"
            f"SL: {sl_str} | Leverage: {position.leverage}x\n\n"
            f"\U0001f6a8 <b>Warnings:</b>\n{warning_lines}\n\n"
            f"\u23f3 Analyzing with Claude Brain...\n"
            f"\U0001f550 {F.format_timestamp()}"
        )

    def watchdog_decision(
        self,
        position: Position,
        decision: WatchdogDecision,
        cost_usd: float = 0.0,
    ) -> str:
        action_map = {
            "hold": "\u23f8\ufe0f HOLD",
            "tighten_stop": "\U0001f6e1\ufe0f TIGHTEN STOP",
            "partial_close": "\u2702\ufe0f PARTIAL CLOSE",
            "full_close": "\U0001f6aa FULL CLOSE",
        }
        action_str = action_map.get(decision.action, decision.action.upper())
        conf_str = F.format_confidence(decision.confidence)
        reasoning = F.truncate(decision.reasoning, 200) if decision.reasoning else "No reasoning provided"
        risk = F.truncate(decision.risk_notes, 150) if decision.risk_notes else ""
        risk_section = f"\n\u26a0\ufe0f {risk}" if risk else ""

        details = ""
        if decision.action == "tighten_stop" and decision.new_stop_loss is not None:
            old_sl = self._price(position.stop_loss, position.symbol) if position.stop_loss else "None"
            details = f"\nSL: {old_sl} \u2192 {self._price(decision.new_stop_loss, position.symbol)}"
        elif decision.action == "partial_close":
            details = f"\nClosing {int(decision.risk_notes.split('%')[0]) if '%' in (decision.risk_notes or '') else 50}% of position"
        elif decision.action == "full_close":
            details = "\nClosing entire position"

        return (
            f"\U0001f9e0 <b>WATCHDOG DECISION</b>\n\n"
            f"<b>{position.symbol}</b> {F.format_side(position.side)}\n"
            f"Action: {action_str}\n"
            f"Confidence: {conf_str}\n\n"
            f"\U0001f4ad {reasoning}"
            f"{risk_section}{details}\n\n"
            f"\U0001f4b5 Cost: ${cost_usd:.4f}\n"
            f"\U0001f550 {F.format_timestamp()}"
        )
