"""Analysis tools: TA, indicators, patterns, signals, recommendations (5 tools)."""

import json
from typing import Callable

from mcp.types import Tool, TextContent

from src.core.logging import get_logger
from src.core.types import TimeFrame
from src.database.connection import DatabaseManager
from src.database.repositories.market_repo import MarketRepository

log = get_logger("mcp")


def register_analysis_tools(services: dict, db: DatabaseManager) -> tuple[list[Tool], dict[str, Callable]]:
    """Register all 5 analysis tools."""
    ta = services.get("ta")
    signal_gen = services.get("signal_gen")
    market_repo = MarketRepository(db) if db else None
    tools: list[Tool] = []
    handlers: dict[str, Callable] = {}

    # 27. get_technical_analysis
    tools.append(Tool(name="get_technical_analysis",
        description="Run comprehensive technical analysis with 37+ indicators, candlestick patterns, support/resistance, and overall BUY/SELL signal with confidence and reasoning.",
        inputSchema={"type": "object", "properties": {
            "symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "15"},
            "limit": {"type": "integer", "default": 200}
        }, "required": ["symbol"]}))

    async def _ta(args):
        try:
            if not ta: return [TextContent(type="text", text="TA Engine not available")]
            tf = TimeFrame(args.get("timeframe", "15"))
            result = await ta.analyze(symbol=args["symbol"], timeframe=tf, limit=args.get("limit", 200))
            overall = result.get("overall", {})
            lines = [
                f"Technical Analysis: {args['symbol']} ({tf.value})",
                f"{'='*50}",
                f"SIGNAL: {overall.get('signal', 'N/A')} (score: {overall.get('score', 0):+.2f}, confidence: {overall.get('confidence', 0):.0%})",
                f"Bullish: {overall.get('bullish_indicators', 0)} | Bearish: {overall.get('bearish_indicators', 0)} | Neutral: {overall.get('neutral_indicators', 0)}",
                "",
                f"Trend: {result.get('trend', {}).get('trend_summary', 'N/A')}",
                f"  SMA 20: {result['trend'].get('sma_20')} | SMA 50: {result['trend'].get('sma_50')}",
                f"  MACD: {result['trend'].get('macd', {}).get('histogram')} | ADX: {result['trend'].get('adx', {}).get('adx')}",
                "",
                f"Momentum: {result.get('momentum', {}).get('momentum_summary', 'N/A')}",
                f"  RSI: {result['momentum'].get('rsi_14')} | Stoch K: {result['momentum'].get('stochastic', {}).get('k')}",
                "",
                f"Volatility: {result.get('volatility', {}).get('volatility_summary', 'N/A')}",
                f"  ATR: {result['volatility'].get('atr_14')} | BB Width: {result['volatility'].get('bollinger', {}).get('bandwidth')}",
                "",
                f"Volume: {result.get('volume', {}).get('volume_summary', 'N/A')}",
            ]
            patterns = result.get("patterns", {})
            cp = patterns.get("candlestick", [])
            chp = patterns.get("chart", [])
            if cp:
                pat_strs = [p["name"] + " (" + p["type"] + ")" for p in cp]
                lines.append("\nCandlestick Patterns: " + ", ".join(pat_strs))
            if chp:
                pat_strs = [p["name"] + " (" + p["type"] + ")" for p in chp]
                lines.append("Chart Patterns: " + ", ".join(pat_strs))
            sr = result.get("support_resistance", {})
            if sr:
                lines.append(f"\nSupport: {sr.get('support_levels', [])}")
                lines.append(f"Resistance: {sr.get('resistance_levels', [])}")
            reasons = overall.get("key_reasons", [])
            if reasons:
                lines.append(f"\nKey Reasons:")
                for r in reasons[:5]:
                    lines.append(f"  - {r}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error running TA: {e}")]
    handlers["get_technical_analysis"] = _ta

    # 28. get_indicator
    tools.append(Tool(name="get_indicator",
        description="Get a specific indicator value (rsi, macd, bollinger, atr, stochastic, adx, obv, vwap).",
        inputSchema={"type": "object", "properties": {
            "symbol": {"type": "string"}, "indicator": {"type": "string"},
            "timeframe": {"type": "string", "default": "15"}, "period": {"type": "integer"}
        }, "required": ["symbol", "indicator"]}))

    async def _indicator(args):
        try:
            if not ta or not market_repo: return [TextContent(type="text", text="TA not available")]
            tf = args.get("timeframe", "15")
            klines = await market_repo.get_klines(args["symbol"], tf, 200)
            if len(klines) < 50: return [TextContent(type="text", text=f"Not enough data for {args['symbol']}")]
            params = {"period": args["period"]} if args.get("period") else {}
            result = await ta.get_indicator(klines, args["indicator"], **params)
            if "error" in result: return [TextContent(type="text", text=result["error"])]
            lines = [f"{args['indicator'].upper()} for {args['symbol']}:"]
            for k, v in result.items():
                if k != "name":
                    lines.append(f"  {k}: {v}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_indicator"] = _indicator

    # 29. get_patterns
    tools.append(Tool(name="get_patterns",
        description="Detect candlestick and chart patterns (hammer, engulfing, double top, head & shoulders, etc.).",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "15"}}, "required": ["symbol"]}))

    async def _patterns(args):
        try:
            if not ta: return [TextContent(type="text", text="TA not available")]
            tf = TimeFrame(args.get("timeframe", "15"))
            result = await ta.analyze(symbol=args["symbol"], timeframe=tf)
            patterns = result.get("patterns", {})
            cp = patterns.get("candlestick", [])
            chp = patterns.get("chart", [])
            if not cp and not chp:
                return [TextContent(type="text", text=f"No patterns detected for {args['symbol']}")]
            lines = [f"Patterns for {args['symbol']}:"]
            for p in cp:
                lines.append(f"  Candlestick: {p['name']} ({p['type']}) confidence={p['confidence']:.0%}")
            for p in chp:
                lines.append(f"  Chart: {p['name']} ({p['type']}) confidence={p['confidence']:.0%}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_patterns"] = _patterns

    # 30. get_signal
    tools.append(Tool(name="get_signal",
        description="Get a comprehensive trading signal combining TA indicators with sentiment analysis.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}))

    async def _signal(args):
        try:
            if not signal_gen: return [TextContent(type="text", text="Signal generator not available")]
            sig = await signal_gen.generate_signal(args["symbol"])
            text = (f"Signal for {sig.symbol}: {sig.signal_type.value.upper()}\n"
                    f"  Confidence: {sig.confidence:.0%}\n"
                    f"  Source: {sig.source}\n"
                    f"  Reasoning: {sig.reasoning}\n"
                    f"  Components: {json.dumps(sig.components, indent=2)}")
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_signal"] = _signal

    # 31. get_trade_recommendation
    tools.append(Tool(name="get_trade_recommendation",
        description="Get a full trade recommendation with entry, stop-loss, take-profit, position size, and risk assessment.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "risk_pct": {"type": "number", "default": 2.0}}, "required": ["symbol"]}))

    async def _trade_rec(args):
        try:
            symbol = args["symbol"]
            parts = [f"Trade Recommendation for {symbol}:"]
            if signal_gen:
                sig = await signal_gen.generate_signal(symbol)
                parts.append(f"  Signal: {sig.signal_type.value.upper()} (confidence: {sig.confidence:.0%})")
                parts.append(f"  Reasoning: {sig.reasoning}")
            if ta:
                try:
                    tf = TimeFrame.M15
                    result = await ta.analyze(symbol=symbol, timeframe=tf)
                    sr = result.get("support_resistance", {})
                    current = sr.get("current_price", 0)
                    supports = sr.get("support_levels", [])
                    resistances = sr.get("resistance_levels", [])
                    parts.append(f"\n  Current Price: ${current:,.2f}")
                    if supports:
                        parts.append(f"  Stop Loss (support): ${supports[0]:,.2f}")
                    if resistances:
                        parts.append(f"  Take Profit (resistance): ${resistances[0]:,.2f}")
                except Exception as e:
                    log.warning("Trade rec: TA/SR lookup failed: {err}", err=str(e))
            account = services.get("account")
            if account:
                try:
                    balance = await account.get_available_balance()
                    risk = args.get("risk_pct", 2.0) / 100
                    risk_amount = balance * risk
                    parts.append(f"\n  Available Balance: ${balance:,.2f}")
                    parts.append(f"  Risk Amount ({args.get('risk_pct', 2.0)}%): ${risk_amount:,.2f}")
                except Exception as e:
                    log.warning("Trade rec: balance lookup failed: {err}", err=str(e))
            return [TextContent(type="text", text="\n".join(parts))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_trade_recommendation"] = _trade_rec

    return tools, handlers
