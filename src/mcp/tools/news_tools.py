"""News tools: latest news, symbol news, search, economic calendar (4 tools)."""

from typing import Callable
from mcp.types import Tool, TextContent


def register_news_tools(services: dict) -> tuple[list[Tool], dict[str, Callable]]:
    """Register all 4 news tools."""
    news = services.get("news")
    calendar = services.get("calendar")
    tools: list[Tool] = []
    handlers: dict[str, Callable] = {}

    # 13. get_latest_news
    tools.append(Tool(name="get_latest_news",
        description="Get the latest cryptocurrency news headlines with sentiment scores (-1 bearish to +1 bullish).",
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}, "required": []}))

    async def _latest_news(args):
        try:
            if not news: return [TextContent(type="text", text="News service not available")]
            articles = await news.fetch_latest_news(max_articles=args.get("limit", 10))
            if not articles: return [TextContent(type="text", text="No recent news available")]
            lines = [f"Latest Crypto News ({len(articles)} articles):"]
            for a in articles[:10]:
                sent = "Bullish" if a.sentiment_score > 0.2 else "Bearish" if a.sentiment_score < -0.2 else "Neutral"
                lines.append(f"  [{sent:>7}] {a.headline[:80]}")
                lines.append(f"           Source: {a.source} | Score: {a.sentiment_score:+.2f}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_latest_news"] = _latest_news

    # 14. get_news_for_symbol
    tools.append(Tool(name="get_news_for_symbol",
        description="Get recent news specifically about a cryptocurrency.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "hours": {"type": "integer", "default": 24}}, "required": ["symbol"]}))

    async def _news_symbol(args):
        try:
            if not news: return [TextContent(type="text", text="News service not available")]
            articles = await news.get_news_for_symbol(args["symbol"], args.get("hours", 24))
            if not articles: return [TextContent(type="text", text=f"No recent news for {args['symbol']}")]
            lines = [f"News for {args['symbol']} ({len(articles)} articles):"]
            for a in articles[:10]:
                lines.append(f"  [{a.sentiment_score:+.2f}] {a.headline[:80]}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_news_for_symbol"] = _news_symbol

    # 15. search_news
    tools.append(Tool(name="search_news",
        description="Search news articles by keyword.",
        inputSchema={"type": "object", "properties": {"keyword": {"type": "string"}, "limit": {"type": "integer", "default": 10}}, "required": ["keyword"]}))

    async def _search(args):
        try:
            if not news: return [TextContent(type="text", text="News service not available")]
            articles = await news.search_news(args["keyword"], args.get("limit", 10))
            if not articles: return [TextContent(type="text", text=f"No news found for '{args['keyword']}'")]
            lines = [f"Search results for '{args['keyword']}':"]
            for a in articles:
                lines.append(f"  {a.headline[:80]} ({a.source})")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["search_news"] = _search

    # 16. get_economic_calendar
    tools.append(Tool(name="get_economic_calendar",
        description="Get upcoming economic events (FOMC, CPI, NFP) that could impact crypto markets.",
        inputSchema={"type": "object", "properties": {"days": {"type": "integer", "default": 7}}, "required": []}))

    async def _calendar(args):
        try:
            if not calendar: return [TextContent(type="text", text="Calendar service not available")]
            events = await calendar.get_upcoming_events(args.get("days", 7))
            if not events: return [TextContent(type="text", text="No upcoming economic events")]
            lines = ["Upcoming Economic Events:"]
            for e in events[:10]:
                lines.append(f"  [{e.get('impact', '').upper():>6}] {e.get('event_name', '')} ({e.get('country', '')})")
                lines.append(f"           Time: {e.get('event_time', '')} | Est: {e.get('estimate', 'N/A')}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_economic_calendar"] = _calendar

    return tools, handlers
