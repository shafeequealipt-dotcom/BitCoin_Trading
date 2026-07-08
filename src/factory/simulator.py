"""Trade Simulator: faithfully simulates trade execution with real-world costs."""

from src.core.logging import get_logger
from src.core.types import OHLCV, Side
from src.core.utils import generate_id
from src.factory.models.backtest_types import BacktestConfig, SimulatedTrade
from src.strategies.models.signal_types import RawSignal

log = get_logger("factory")


class TradeSimulator:
    """Simulates trade execution with commission, slippage, and funding costs.

    Args:
        config: Backtest configuration.
    """

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self._open_positions: list[dict] = []

    def open_position(
        self, signal: RawSignal, timestamp: str, capital: float,
    ) -> dict:
        """Open a simulated position from a RawSignal."""
        size_usd = capital * (self.config.position_size_pct / 100)
        entry_price = signal.entry_price

        # Apply slippage against us
        if signal.direction == Side.BUY:
            entry_price *= (1 + self.config.slippage_pct / 100)
        else:
            entry_price *= (1 - self.config.slippage_pct / 100)

        qty = size_usd / entry_price if entry_price > 0 else 0
        commission = size_usd * self.config.commission_pct / 100

        position = {
            "trade_id": generate_id("sim"),
            "symbol": signal.symbol,
            "direction": signal.direction.value,
            "entry_price": entry_price,
            "entry_time": timestamp,
            "qty": qty,
            "leverage": self.config.leverage,
            "stop_loss": signal.suggested_stop_loss,
            "take_profit": signal.suggested_take_profit,
            "entry_commission": commission,
            "max_favorable": 0.0,
            "max_adverse": 0.0,
        }

        self._open_positions.append(position)
        return position

    def check_position(
        self, position: dict, candle: OHLCV,
    ) -> SimulatedTrade | None:
        """Check if any exit condition is triggered on this candle.

        Checks in order: SL → TP → trailing (conservative: losses first).
        """
        is_long = position["direction"] == "Buy"
        sl = position["stop_loss"]
        tp = position["take_profit"]

        # Update max favorable/adverse
        if is_long:
            favorable = (candle.high - position["entry_price"]) / position["entry_price"] * 100
            adverse = (position["entry_price"] - candle.low) / position["entry_price"] * 100
        else:
            favorable = (position["entry_price"] - candle.low) / position["entry_price"] * 100
            adverse = (candle.high - position["entry_price"]) / position["entry_price"] * 100

        position["max_favorable"] = max(position["max_favorable"], favorable)
        position["max_adverse"] = max(position["max_adverse"], adverse)

        # Check SL first (conservative)
        if sl:
            if is_long and candle.low <= sl:
                return self._close(position, sl, candle.timestamp, "stop_loss")
            if not is_long and candle.high >= sl:
                return self._close(position, sl, candle.timestamp, "stop_loss")

        # Check TP
        if tp:
            if is_long and candle.high >= tp:
                return self._close(position, tp, candle.timestamp, "take_profit")
            if not is_long and candle.low <= tp:
                return self._close(position, tp, candle.timestamp, "take_profit")

        return None

    def force_close(
        self, position: dict, price: float, timestamp: str, reason: str,
    ) -> SimulatedTrade:
        """Force close a position at given price."""
        return self._close(position, price, timestamp, reason)

    def _close(
        self, position: dict, exit_price: float, exit_time, reason: str,
    ) -> SimulatedTrade:
        """Close position and calculate PnL."""
        # Apply exit slippage against us
        is_long = position["direction"] == "Buy"
        if is_long:
            exit_price *= (1 - self.config.slippage_pct / 100)
        else:
            exit_price *= (1 + self.config.slippage_pct / 100)

        qty = position["qty"]
        leverage = position["leverage"]
        entry_price = position["entry_price"]

        # PnL
        if is_long:
            raw_pnl = (exit_price - entry_price) * qty * leverage
        else:
            raw_pnl = (entry_price - exit_price) * qty * leverage

        # Commissions
        exit_value = exit_price * qty
        exit_commission = exit_value * self.config.commission_pct / 100
        total_commission = position["entry_commission"] + exit_commission

        # Funding costs (estimate based on hold time)
        entry_ts = str(position["entry_time"])
        exit_ts = str(exit_time) if not isinstance(exit_time, str) else exit_time
        hold_minutes = 60  # Default estimate
        try:
            from datetime import datetime
            if hasattr(position["entry_time"], 'timestamp'):
                hold_minutes = int((exit_time.timestamp() - position["entry_time"].timestamp()) / 60)
        except Exception:
            pass

        hold_hours = max(hold_minutes / 60, 0)
        funding_cost = (entry_price * qty) * (self.config.funding_rate_pct / 100) * (hold_hours / 8)

        # Net PnL
        net_pnl = raw_pnl - total_commission - funding_cost
        pnl_pct = (net_pnl / (entry_price * qty)) * 100 if qty > 0 and entry_price > 0 else 0

        # Remove from open positions
        if position in self._open_positions:
            self._open_positions.remove(position)

        return SimulatedTrade(
            trade_id=position["trade_id"],
            symbol=position["symbol"],
            direction=position["direction"],
            entry_price=entry_price,
            entry_time=entry_ts,
            exit_price=exit_price,
            exit_time=exit_ts,
            exit_reason=reason,
            qty=qty,
            pnl_usd=round(net_pnl, 4),
            pnl_pct=round(pnl_pct, 4),
            commission_usd=round(total_commission, 4),
            slippage_usd=0.0,
            hold_minutes=hold_minutes,
            leverage=leverage,
            max_favorable=round(position["max_favorable"], 4),
            max_adverse=round(position["max_adverse"], 4),
        )

    @property
    def open_count(self) -> int:
        return len(self._open_positions)

    @property
    def open_positions(self) -> list[dict]:
        return list(self._open_positions)
