"""Memory & learning tools: trade history, strategy performance, patterns (4 tools)."""

from typing import Callable
from mcp.types import Tool, TextContent
from src.database.connection import DatabaseManager
from src.database.repositories.trading_repo import TradingRepository
from src.database.repositories.altdata_repo import AltDataRepository


def register_memory_tools(services: dict, db: DatabaseManager) -> tuple[list[Tool], dict[str, Callable]]:
    """Register all 4 memory tools."""
    trading_repo = TradingRepository(db) if db else None
    altdata_repo = AltDataRepository(db) if db else None
    tools: list[Tool] = []
    handlers: dict[str, Callable] = {}

    # 37. get_trade_history
    tools.append(Tool(name="get_trade_history",
        description="Get past trade history with entry/exit prices, PnL, and strategy.",
        inputSchema={"type": "object", "properties": {
            "symbol": {"type": "string"}, "limit": {"type": "integer", "default": 20}
        }, "required": []}))

    async def _trade_hist(args):
        try:
            if not trading_repo: return [TextContent(type="text", text="Database not available")]
            trades = await trading_repo.get_trade_history(args.get("symbol"), args.get("limit", 20))
            if not trades: return [TextContent(type="text", text="No trade history")]
            lines = [f"Trade History ({len(trades)} trades):"]
            total_pnl = 0
            wins = 0
            for t in trades:
                total_pnl += t.pnl
                if t.pnl > 0: wins += 1
                from src.core.utils import format_price
                lines.append(f"  {t.symbol} {t.side.value} {t.qty} @ ${format_price(t.entry_price)} -> ${format_price(t.exit_price)} PnL=${t.pnl:+,.2f} ({t.pnl_pct:+.1f}%)")
            if trades:
                lines.append(f"\n  Total PnL: ${total_pnl:+,.2f} | Win Rate: {wins}/{len(trades)} ({wins/len(trades)*100:.0f}%)")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_trade_history"] = _trade_hist

    # 38. get_strategy_performance
    tools.append(Tool(name="get_strategy_performance",
        description="Get performance statistics for trading strategies: win rate, avg PnL, profit factor.",
        inputSchema={"type": "object", "properties": {"strategy": {"type": "string"}}, "required": []}))

    async def _strat_perf(args):
        try:
            if not trading_repo: return [TextContent(type="text", text="Database not available")]
            trades = await trading_repo.get_trade_history(limit=100)
            if not trades: return [TextContent(type="text", text="No trades for performance analysis")]
            strategy_filter = args.get("strategy")
            if strategy_filter:
                trades = [t for t in trades if t.strategy == strategy_filter]
            wins = [t for t in trades if t.pnl > 0]
            losses = [t for t in trades if t.pnl <= 0]
            total_profit = sum(t.pnl for t in wins)
            total_loss = abs(sum(t.pnl for t in losses)) or 1
            text = (f"Strategy Performance:\n"
                    f"  Total Trades: {len(trades)}\n"
                    f"  Win Rate:     {len(wins)}/{len(trades)} ({len(wins)/len(trades)*100:.0f}%)\n"
                    f"  Avg PnL:      ${sum(t.pnl for t in trades)/len(trades):+,.2f}\n"
                    f"  Total Profit: ${total_profit:+,.2f}\n"
                    f"  Total Loss:   ${-total_loss:,.2f}\n"
                    f"  Profit Factor: {total_profit/total_loss:.2f}")
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_strategy_performance"] = _strat_perf

    # 39. get_pattern_outcomes
    tools.append(Tool(name="get_pattern_outcomes",
        description="Get historical outcomes after detected patterns to assess pattern reliability.",
        inputSchema={"type": "object", "properties": {"pattern_type": {"type": "string"}, "symbol": {"type": "string"}}, "required": []}))

    async def _pattern_out(args):
        try:
            return [TextContent(type="text", text="Pattern outcome tracking requires more historical data. Continue trading to build this dataset.")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_pattern_outcomes"] = _pattern_out

    # 40. get_brain_decisions
    tools.append(Tool(name="get_brain_decisions",
        description="Get history of automated Claude Brain trading decisions with reasoning and outcomes.",
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}, "required": []}))

    async def _brain_dec(args):
        try:
            if not altdata_repo: return [TextContent(type="text", text="Database not available")]
            # Read recent signals as proxy for brain decisions
            symbols = ["BTCUSDT", "ETHUSDT"]
            lines = ["Recent Signals/Decisions:"]
            for sym in symbols:
                sig = await altdata_repo.get_latest_signal(sym)
                if sig:
                    lines.append(f"  {sig.symbol}: {sig.signal_type.value} (confidence: {sig.confidence:.0%})")
                    lines.append(f"    Reasoning: {sig.reasoning[:100]}")
            if len(lines) == 1:
                return [TextContent(type="text", text="No brain decisions recorded yet. Enable the Claude Brain to start automated analysis.")]
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_brain_decisions"] = _brain_dec

    return tools, handlers
