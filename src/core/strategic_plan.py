"""Strategic Plan — Claude's high-level trading directives.

Updated every 3 minutes by Claude. Read by rule engine every 45 seconds.
Read by watchdog every 10 seconds. This is the SINGLE SOURCE OF TRUTH
for what the system should be doing.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CoinDirective:
    """Claude's instruction for a specific coin."""

    symbol: str
    direction: str = "both"  # "buy_only", "sell_only", "both", "avoid"
    reason: str = ""
    leverage: int = 2
    sl_pct: float = 2.0
    tp_pct: float = 2.5
    max_hold_minutes: int = 30
    priority: int = 5  # 1=highest, 10=lowest


@dataclass
class PositionAction:
    """Claude's instruction for an existing position."""

    symbol: str
    action: str = "hold"  # "hold", "close", "tighten_stop", "set_exit", "take_profit"
    reason: str = ""
    exit_price: float = 0  # specific exit price if action is "set_exit"
    new_sl: float = 0  # new SL price if action is "tighten_stop"


@dataclass
class StrategicPlan:
    """The complete strategic plan from Claude."""

    # Market assessment
    market_view: str = ""
    risk_level: str = "normal"  # "conservative", "normal", "aggressive"

    # Position limits
    max_positions: int = 4
    max_per_coin: int = 1  # ALWAYS 1 — never stack

    # Default parameters (used when coin-specific not available)
    default_direction: str = "both"
    default_sl_pct: float = 2.0
    default_tp_pct: float = 2.5
    default_hold_minutes: int = 30
    default_leverage: int = 2
    trailing_activation_pct: float = 0.5

    # Per-coin directives
    coin_directives: dict[str, CoinDirective] = field(default_factory=dict)

    # Position actions
    position_actions: dict[str, PositionAction] = field(default_factory=dict)

    # Focus coins (ordered by priority)
    focus_coins: list[str] = field(default_factory=list)
    avoid_coins: list[str] = field(default_factory=list)

    # Claude's direct trade commands (new_trades from response)
    new_trades: list[dict] = field(default_factory=list)

    # Metadata
    created_at: float = 0.0
    created_at_dt: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    review_interval_minutes: int = 3
    raw_reasoning: str = ""

    @property
    def age_seconds(self) -> float:
        if self.created_at == 0:
            return 9999
        return time.time() - self.created_at

    @property
    def is_stale(self) -> bool:
        """Plan is stale if older than 2x review interval."""
        return self.age_seconds > (self.review_interval_minutes * 60 * 2)

    def get_directive(self, symbol: str) -> CoinDirective:
        """Get directive for a coin. Falls back to defaults."""
        if symbol in self.coin_directives:
            return self.coin_directives[symbol]
        return CoinDirective(
            symbol=symbol,
            direction=self.default_direction,
            leverage=self.default_leverage,
            sl_pct=self.default_sl_pct,
            tp_pct=self.default_tp_pct,
            max_hold_minutes=self.default_hold_minutes,
        )

    def can_trade_symbol(self, symbol: str, direction: str) -> tuple[bool, str]:
        """Check if a trade is allowed by the plan.
        Returns (allowed, reason).
        """
        if symbol in self.avoid_coins:
            return False, f"Claude says avoid {symbol}"

        directive = self.get_directive(symbol)

        if directive.direction == "avoid":
            return False, f"Claude says avoid {symbol}: {directive.reason}"

        if directive.direction == "buy_only" and direction.lower() in (
            "sell",
            "short",
        ):
            return False, f"Claude says buy_only for {symbol}, signal is sell"

        if directive.direction == "sell_only" and direction.lower() in (
            "buy",
            "long",
        ):
            return False, f"Claude says sell_only for {symbol}, signal is buy"

        return True, "matches plan"

    def to_telegram_text(self) -> str:
        """Format plan for Telegram display — EVERY parameter visible."""
        lines = [
            "<b>STRATEGIC PLAN</b>",
            f"<b>Age:</b> {int(self.age_seconds)}s | <b>Stale:</b> {'YES' if self.is_stale else 'No'}",
            "",
            "<b>MARKET VIEW</b>",
            f"{self.market_view[:150]}",
            "",
            "<b>RISK PARAMETERS</b>",
            f"<b>Risk level:</b> {self.risk_level}",
            f"<b>Max positions:</b> {self.max_positions}",
            f"<b>Max per coin:</b> {self.max_per_coin}",
            "",
            "<b>DEFAULT VALUES</b>",
            f"<b>Stop-loss:</b> {self.default_sl_pct}%",
            f"<b>Take-profit:</b> {self.default_tp_pct}%",
            f"<b>Hold time:</b> {self.default_hold_minutes} min",
            f"<b>Leverage:</b> {self.default_leverage}x",
            f"<b>Trailing at:</b> +{self.trailing_activation_pct}%",
            f"<b>Direction:</b> {self.default_direction}",
            "",
            "<b>COIN DIRECTIVES</b>",
        ]

        if self.coin_directives:
            for sym, d in self.coin_directives.items():
                emoji = {
                    "buy_only": "UP",
                    "sell_only": "DN",
                    "both": "<>",
                    "avoid": "NO",
                }.get(d.direction, "??")
                lines.append(
                    f"[{emoji}] <b>{sym}</b>: {d.direction}\n"
                    f"    Lev={d.leverage}x | SL={d.sl_pct}% | TP={d.tp_pct}% | Hold={d.max_hold_minutes}min\n"
                    f"    Reason: {d.reason[:60]}"
                )
        else:
            lines.append("  No specific directives -- using defaults")

        if self.position_actions:
            lines.append("")
            lines.append("<b>POSITION ACTIONS</b>")
            from src.core.utils import format_price
            for sym, a in self.position_actions.items():
                action_detail = ""
                if a.action == "set_exit" and a.exit_price > 0:
                    action_detail = f" -> ${format_price(a.exit_price)}"
                elif a.action == "tighten_stop" and a.new_sl > 0:
                    action_detail = f" -> SL ${format_price(a.new_sl)}"
                lines.append(
                    f"<b>{sym}</b>: {a.action}{action_detail}\n    {a.reason[:80]}"
                )

        lines.append("")
        lines.append("<b>FOCUS & AVOID</b>")
        lines.append(
            f"<b>Focus:</b> {', '.join(self.focus_coins[:5]) or 'none'}"
        )
        if self.avoid_coins:
            lines.append(f"<b>Avoid:</b> {', '.join(self.avoid_coins)}")

        lines.append(
            f"\n<b>Next review in:</b> {max(0, self.review_interval_minutes * 60 - int(self.age_seconds))}s"
        )

        return "\n".join(lines)
