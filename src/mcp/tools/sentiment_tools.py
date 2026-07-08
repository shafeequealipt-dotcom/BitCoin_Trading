"""Sentiment tools: Reddit, social buzz, aggregated sentiment (5 tools)."""

from typing import Callable
from mcp.types import Tool, TextContent


def register_sentiment_tools(services: dict) -> tuple[list[Tool], dict[str, Callable]]:
    """Register all 5 sentiment tools."""
    reddit = services.get("reddit")
    aggregator = services.get("aggregator")
    tools: list[Tool] = []
    handlers: dict[str, Callable] = {}

    # 17. get_reddit_sentiment
    tools.append(Tool(name="get_reddit_sentiment",
        description="Get social media sentiment for a cryptocurrency from Reddit crypto subreddits.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}))

    async def _reddit_sent(args):
        try:
            if not reddit: return [TextContent(type="text", text="Reddit service not available")]
            buzz = await reddit.get_symbol_buzz(args["symbol"])
            level = "Bullish" if buzz.get("avg_sentiment", 0) > 0.2 else "Bearish" if buzz.get("avg_sentiment", 0) < -0.2 else "Neutral"
            text = (f"Reddit Sentiment for {args['symbol']}:\n"
                    f"  Sentiment:   {level} ({buzz.get('avg_sentiment', 0):+.3f})\n"
                    f"  Mentions 12h: {buzz.get('mention_count_12h', 0)}\n"
                    f"  Mentions 24h: {buzz.get('mention_count_24h', 0)}\n"
                    f"  Trend:       {buzz.get('trend', 'stable')}\n"
                    f"  Direction:   {buzz.get('sentiment_direction', 'stable')}")
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_reddit_sentiment"] = _reddit_sent

    # 18. get_subreddit_hot
    tools.append(Tool(name="get_subreddit_hot",
        description="Get the hottest posts from a crypto subreddit to see what the community is discussing.",
        inputSchema={"type": "object", "properties": {"subreddit": {"type": "string"}}, "required": ["subreddit"]}))

    async def _sub_hot(args):
        try:
            if not reddit: return [TextContent(type="text", text="Reddit service not available")]
            mood = await reddit.get_subreddit_mood(args["subreddit"])
            text = (f"r/{args['subreddit']} Mood:\n"
                    f"  Sentiment: {mood.get('dominant_mood', 'neutral')} ({mood.get('avg_sentiment', 0):+.3f})\n"
                    f"  Posts:     {mood.get('post_count', 0)}\n"
                    f"  Top Post:  {mood.get('top_post', 'N/A')}")
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_subreddit_hot"] = _sub_hot

    # 19. get_social_buzz
    tools.append(Tool(name="get_social_buzz",
        description="Get the most mentioned cryptocurrencies on Reddit right now with sentiment.",
        inputSchema={"type": "object", "properties": {"hours": {"type": "integer", "default": 24}, "top_n": {"type": "integer", "default": 10}}, "required": []}))

    async def _social_buzz(args):
        try:
            if not reddit: return [TextContent(type="text", text="Reddit service not available")]
            mentions = await reddit.get_most_mentioned(args.get("hours", 24), args.get("top_n", 10))
            if not mentions: return [TextContent(type="text", text="No social buzz data available")]
            lines = ["Most Mentioned Cryptos on Reddit:"]
            for m in mentions:
                sent = "+" if m.get("avg_sentiment", 0) > 0 else ""
                lines.append(f"  {m['symbol']:<10} {m['mention_count']:>3} mentions  sentiment: {sent}{m.get('avg_sentiment', 0):.3f}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_social_buzz"] = _social_buzz

    # 20. get_aggregated_sentiment
    tools.append(Tool(name="get_aggregated_sentiment",
        description="Get comprehensive sentiment combining news, Reddit, and Fear & Greed index with weighted scoring.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "hours": {"type": "integer", "default": 24}}, "required": ["symbol"]}))

    async def _agg_sent(args):
        try:
            if not aggregator: return [TextContent(type="text", text="Aggregator not available")]
            result = await aggregator.aggregate_for_symbol(args["symbol"], args.get("hours", 24))
            text = (f"Aggregated Sentiment for {args['symbol']}:\n"
                    f"  Overall:  {result.get('level', 'N/A').upper()} (score: {result.get('overall_score', 0):+.4f})\n"
                    f"  News:     {result.get('news_score', 0):+.4f} ({result.get('news_count', 0)} articles)\n"
                    f"  Reddit:   {result.get('reddit_score', 0):+.4f} ({result.get('reddit_count', 0)} posts)\n"
                    f"  F&G:      {result.get('fear_greed_value', 50)} ({result.get('fear_greed_classification', 'N/A')})\n"
                    f"  Momentum: {result.get('momentum', 0):+.4f}")
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_aggregated_sentiment"] = _agg_sent

    # 21. get_sentiment_history
    tools.append(Tool(name="get_sentiment_history",
        description="Get how sentiment for a cryptocurrency has changed over time.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "hours": {"type": "integer", "default": 48}}, "required": ["symbol"]}))

    async def _sent_hist(args):
        try:
            if not aggregator: return [TextContent(type="text", text="Aggregator not available")]
            shift = await aggregator.get_sentiment_shift(args["symbol"], args.get("hours", 48))
            text = (f"Sentiment Shift for {args['symbol']}:\n"
                    f"  Current:  {shift.get('current_score', 0):+.4f}\n"
                    f"  Previous: {shift.get('previous_score', 0):+.4f}\n"
                    f"  Shift:    {shift.get('shift', 0):+.4f}\n"
                    f"  Direction: {shift.get('direction', 'stable')}")
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_sentiment_history"] = _sent_hist

    return tools, handlers
