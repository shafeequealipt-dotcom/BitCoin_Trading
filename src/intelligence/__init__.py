"""Intelligence Layer: news, sentiment, alternative data, and signal generation."""

from src.intelligence.altdata.fear_greed import FearGreedClient
from src.intelligence.altdata.funding_rates import FundingRateTracker
from src.intelligence.altdata.onchain import OnChainClient
from src.intelligence.altdata.open_interest import OpenInterestTracker
from src.intelligence.news.calendar_service import CalendarService
from src.intelligence.news.finnhub_client import FinnhubClient
from src.intelligence.news.news_service import NewsService
from src.intelligence.sentiment.aggregator import SentimentAggregator
from src.intelligence.sentiment.reddit_client import RedditClient
from src.intelligence.sentiment.reddit_service import RedditService
from src.intelligence.sentiment.scorer import SentimentScorer
from src.intelligence.signals.signal_generator import SignalGenerator

__all__ = [
    "FinnhubClient",
    "NewsService",
    "CalendarService",
    "RedditClient",
    "RedditService",
    "SentimentScorer",
    "SentimentAggregator",
    "FearGreedClient",
    "FundingRateTracker",
    "OpenInterestTracker",
    "OnChainClient",
    "SignalGenerator",
]
