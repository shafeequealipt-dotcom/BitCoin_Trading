"""Finnhub API client wrapper with async support, rate limiting, and error handling.

The finnhub-python SDK is synchronous — all calls are wrapped with
asyncio.to_thread() to avoid blocking the event loop.
"""

import asyncio

import finnhub

from src.config.settings import Settings
from src.core.decorators import rate_limit, retry, timed
from src.core.exceptions import FinnhubError
from src.core.logging import get_logger

log = get_logger("intelligence")


class FinnhubClient:
    """Finnhub API client for news and economic calendar data.

    Args:
        settings: Application settings with finnhub.api_key.
    """

    def __init__(self, settings: Settings) -> None:
        api_key = settings.finnhub.api_key
        if not api_key:
            log.warning("Finnhub API key not set — news features will not work")
        self._client = finnhub.Client(api_key=api_key)
        self._settings = settings

    @retry(max_attempts=3, delay=2.0, exceptions=(FinnhubError, Exception))
    @rate_limit(calls_per_second=1.0)
    @timed
    async def get_general_news(self, category: str = "crypto", min_id: int = 0) -> list[dict]:
        """Fetch general news from Finnhub.

        Args:
            category: News category ("general", "crypto", "forex", "merger").
            min_id: Minimum article ID for pagination.

        Returns:
            List of raw news article dicts from Finnhub.

        Raises:
            FinnhubError: On API failure.
        """
        try:
            result = await asyncio.to_thread(
                self._client.general_news, category, min_id=min_id
            )
            log.debug("Fetched {n} general news articles ({cat})", n=len(result), cat=category)
            return result
        except finnhub.FinnhubAPIException as e:
            raise FinnhubError(
                f"Finnhub API error fetching news: {e}",
                details={"category": category},
            )
        except finnhub.FinnhubRequestException as e:
            raise FinnhubError(
                f"Finnhub request error: {e}",
                details={"category": category},
            )
        except Exception as e:
            raise FinnhubError(
                f"Unexpected error fetching Finnhub news: {e}",
                details={"category": category, "error": str(e)},
            )

    @retry(max_attempts=3, delay=2.0, exceptions=(FinnhubError, Exception))
    @rate_limit(calls_per_second=1.0)
    @timed
    async def get_crypto_news(self) -> list[dict]:
        """Fetch crypto-specific news.

        Returns:
            List of crypto news article dicts.
        """
        return await self.get_general_news(category="crypto")

    @retry(max_attempts=3, delay=2.0, exceptions=(FinnhubError, Exception))
    @rate_limit(calls_per_second=1.0)
    @timed
    async def get_economic_calendar(self, from_date: str, to_date: str) -> list[dict]:
        """Fetch economic calendar events.

        Args:
            from_date: Start date in YYYY-MM-DD format.
            to_date: End date in YYYY-MM-DD format.

        Returns:
            List of economic event dicts.

        Raises:
            FinnhubError: On API failure.
        """
        try:
            result = await asyncio.to_thread(
                self._client.economic_calendar,
                _from=from_date,
                to=to_date,
            )
            events = result.get("economicCalendar", [])
            log.debug(
                "Fetched {n} economic events ({f} to {t})",
                n=len(events), f=from_date, t=to_date,
            )
            return events
        except finnhub.FinnhubAPIException as e:
            raise FinnhubError(
                f"Finnhub API error fetching calendar: {e}",
                details={"from": from_date, "to": to_date},
            )
        except finnhub.FinnhubRequestException as e:
            raise FinnhubError(
                f"Finnhub request error: {e}",
                details={"from": from_date, "to": to_date},
            )
        except Exception as e:
            raise FinnhubError(
                f"Unexpected error fetching calendar: {e}",
                details={"from": from_date, "to": to_date, "error": str(e)},
            )
