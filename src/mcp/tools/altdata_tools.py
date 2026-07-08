"""Alternative data tools: Fear & Greed, funding rates, OI, market overview (5 tools)."""

from typing import Callable
from mcp.types import Tool, TextContent


def register_altdata_tools(services: dict) -> tuple[list[Tool], dict[str, Callable]]:
    """Register all 5 alt data tools."""
    fear_greed = services.get("fear_greed")
    funding = services.get("funding")
    oi = services.get("oi")
    onchain = services.get("onchain")
    tools: list[Tool] = []
    handlers: dict[str, Callable] = {}

    # 22. get_fear_greed_index
    tools.append(Tool(name="get_fear_greed_index",
        description="Get the Crypto Fear & Greed Index (0-100). Below 25 = extreme fear (buy opportunity). Above 75 = extreme greed (sell opportunity). Contrarian indicator.",
        inputSchema={"type": "object", "properties": {"include_history": {"type": "boolean", "default": False}}, "required": []}))

    async def _fg(args):
        try:
            if not fear_greed: return [TextContent(type="text", text="Fear & Greed not available")]
            fg = await fear_greed.get_latest()
            if not fg: return [TextContent(type="text", text="No Fear & Greed data available")]
            interp = "Extreme Fear (potential buying opportunity)" if fg.value <= 25 else "Extreme Greed (caution advised)" if fg.value >= 75 else "Fear" if fg.value <= 45 else "Greed" if fg.value >= 55 else "Neutral"
            text = f"Fear & Greed Index: {fg.value}/100 ({fg.classification})\n  Interpretation: {interp}"
            if args.get("include_history"):
                history = await fear_greed.get_history(7)
                if history:
                    text += "\n  Last 7 days:"
                    for h in history[-7:]:
                        text += f"\n    {h.timestamp.strftime('%m-%d')}: {h.value} ({h.classification})"
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_fear_greed_index"] = _fg

    # 23. get_funding_rates
    tools.append(Tool(name="get_funding_rates",
        description="Get perpetual contract funding rates. High positive = crowded longs. Negative = crowded shorts. Extreme rates often precede reversals.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": []}))

    async def _funding(args):
        try:
            if not funding: return [TextContent(type="text", text="Funding tracker not available")]
            sym = args.get("symbol")
            rates = await funding.fetch_current_rates([sym] if sym else None)
            if not rates: return [TextContent(type="text", text="No funding rate data")]
            lines = ["Funding Rates:"]
            for r in rates:
                pct = r.funding_rate * 100
                interp = "Crowded Longs" if pct > 0.5 else "Crowded Shorts" if pct < -0.5 else "Normal"
                lines.append(f"  {r.symbol}: {pct:+.4f}% ({interp})")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_funding_rates"] = _funding

    # 24. get_open_interest
    tools.append(Tool(name="get_open_interest",
        description="Get open interest data. Rising OI + rising price confirms trend. Significant OI changes signal upcoming volatility.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": []}))

    async def _oi_tool(args):
        try:
            if not oi: return [TextContent(type="text", text="OI tracker not available")]
            sym = args.get("symbol")
            data = await oi.fetch_current([sym] if sym else None)
            if not data: return [TextContent(type="text", text="No OI data")]
            lines = ["Open Interest:"]
            for d in data:
                change = d.get("change_24h_pct", 0)
                interp = "Rising" if change > 2 else "Falling" if change < -2 else "Stable"
                lines.append(f"  {d['symbol']}: {d['open_interest']:,.0f} ({change:+.1f}% — {interp})")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_open_interest"] = _oi_tool

    # 25. get_funding_history
    tools.append(Tool(name="get_funding_history",
        description="Get historical funding rates to identify patterns and extremes.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "hours": {"type": "integer", "default": 24}}, "required": ["symbol"]}))

    async def _fh(args):
        try:
            if not funding: return [TextContent(type="text", text="Funding tracker not available")]
            history = await funding.get_rate_history(args["symbol"], args.get("hours", 24))
            if not history: return [TextContent(type="text", text=f"No funding history for {args['symbol']}")]
            lines = [f"Funding Rate History for {args['symbol']}:"]
            for r in history[:20]:
                lines.append(f"  {r.fetched_at.strftime('%m-%d %H:%M')}: {r.funding_rate*100:+.4f}%")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_funding_history"] = _fh

    # 26. get_market_overview
    tools.append(Tool(name="get_market_overview",
        description="Get a comprehensive market snapshot: Fear & Greed, funding rates, OI, market cap, BTC dominance.",
        inputSchema={"type": "object", "properties": {}, "required": []}))

    async def _overview(args):
        try:
            parts = []
            if fear_greed:
                try:
                    fg = await fear_greed.get_latest()
                    if fg: parts.append(f"Fear & Greed: {fg.value}/100 ({fg.classification})")
                except: pass
            if onchain:
                try:
                    gm = await onchain.get_global_metrics()
                    cap = gm.get("total_market_cap_usd", 0)
                    parts.append(f"Total Market Cap: ${cap/1e12:.2f}T")
                    parts.append(f"BTC Dominance: {gm.get('btc_dominance', 0):.1f}%")
                except: pass
            if funding:
                try:
                    rates = await funding.fetch_current_rates()
                    if rates:
                        parts.append(f"Funding Rates: {len(rates)} symbols tracked")
                except: pass
            if not parts: return [TextContent(type="text", text="Market overview data not available")]
            return [TextContent(type="text", text="Market Overview:\n  " + "\n  ".join(parts))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_market_overview"] = _overview

    return tools, handlers
