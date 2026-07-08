"""Database & Memory System.

Provides SQLite storage with WAL mode for concurrent read/write access.
Organized into 4 memory layers: Market, Intelligence, Trading, Learning.
"""

from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations
from src.database.repositories.altdata_repo import AltDataRepository
from src.database.repositories.context_repo import ContextRepository
from src.database.repositories.learning_repo import LearningRepository
from src.database.repositories.market_repo import MarketRepository
from src.database.repositories.news_repo import NewsRepository
from src.database.repositories.sentiment_repo import SentimentRepository
from src.database.repositories.trading_repo import TradingRepository

__all__ = [
    "DatabaseManager",
    "run_migrations",
    "AltDataRepository",
    "ContextRepository",
    "LearningRepository",
    "MarketRepository",
    "NewsRepository",
    "SentimentRepository",
    "TradingRepository",
]
