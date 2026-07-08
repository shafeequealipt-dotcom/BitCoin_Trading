"""CoinGecko on-chain metrics client via aiohttp."""

import aiohttp

from src.config.settings import Settings
from src.core.decorators import rate_limit, retry, timed
from src.core.exceptions import APIError
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.intelligence.signals.signal_models import COINGECKO_SYMBOL_MAP

log = get_logger("intelligence")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class OnChainClient:
    """CoinGecko on-chain and market metrics client.

    Args:
        settings: Application settings.
        db: Database manager.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self._settings = settings
        self._db = db

    @retry(max_attempts=2, delay=5.0, exceptions=(APIError, aiohttp.ClientError, Exception))
    @rate_limit(calls_per_second=0.3)
    @timed
    async def get_global_metrics(self) -> dict:
        """Fetch global crypto market metrics.

        Returns:
            Dict with total_market_cap, btc_dominance, eth_dominance,
            active_cryptocurrencies, market_cap_change_24h.
        """
        try:
            data = await self._get(f"{COINGECKO_BASE}/global")
            gd = data.get("data", {})
            return {
                "total_market_cap_usd": gd.get("total_market_cap", {}).get("usd", 0),
                "btc_dominance": gd.get("market_cap_percentage", {}).get("btc", 0),
                "eth_dominance": gd.get("market_cap_percentage", {}).get("eth", 0),
                "active_cryptocurrencies": gd.get("active_cryptocurrencies", 0),
                "market_cap_change_24h_pct": gd.get("market_cap_change_percentage_24h_usd", 0),
            }
        except APIError:
            raise
        except Exception as e:
            raise APIError(f"CoinGecko global metrics error: {e}", details={"error": str(e)})

    @retry(max_attempts=2, delay=5.0, exceptions=(APIError, aiohttp.ClientError, Exception))
    @rate_limit(calls_per_second=0.3)
    @timed
    async def get_coin_metrics(self, coin_id: str = "bitcoin") -> dict:
        """Fetch detailed metrics for a specific coin.

        Args:
            coin_id: CoinGecko coin ID (e.g. "bitcoin", "ethereum").

        Returns:
            Dict with market_cap, volume_24h, circulating_supply, etc.
        """
        try:
            data = await self._get(
                f"{COINGECKO_BASE}/coins/{coin_id}",
                params={"localization": "false", "tickers": "false",
                        "community_data": "true", "developer_data": "false"},
            )
            market = data.get("market_data", {})
            community = data.get("community_data", {})
            symbol = COINGECKO_SYMBOL_MAP.get(coin_id, coin_id.upper() + "USDT")

            return {
                "coin_id": coin_id,
                "symbol": symbol,
                "market_cap_usd": market.get("market_cap", {}).get("usd", 0),
                "volume_24h_usd": market.get("total_volume", {}).get("usd", 0),
                "circulating_supply": market.get("circulating_supply", 0),
                "price_change_24h_pct": market.get("price_change_percentage_24h", 0),
                "price_change_7d_pct": market.get("price_change_percentage_7d", 0),
                "reddit_subscribers": community.get("reddit_subscribers", 0),
                "twitter_followers": community.get("twitter_followers", 0),
            }
        except APIError:
            raise
        except Exception as e:
            raise APIError(f"CoinGecko coin metrics error: {e}", details={"coin_id": coin_id})

    @retry(max_attempts=2, delay=5.0, exceptions=(APIError, aiohttp.ClientError, Exception))
    @rate_limit(calls_per_second=0.3)
    @timed
    async def get_market_dominance(self) -> dict:
        """Fetch BTC and ETH market dominance.

        Returns:
            Dict with btc_dominance and eth_dominance percentages.
        """
        metrics = await self.get_global_metrics()
        return {
            "btc_dominance": metrics.get("btc_dominance", 0),
            "eth_dominance": metrics.get("eth_dominance", 0),
        }

    async def _get(self, url: str, params: dict | None = None) -> dict:
        """Make a GET request to CoinGecko.

        Args:
            url: Full URL.
            params: Query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            APIError: On HTTP errors.
        """
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 429:
                    raise APIError("CoinGecko rate limit exceeded", details={"status": 429})
                if resp.status != 200:
                    raise APIError(
                        f"CoinGecko returned status {resp.status}",
                        details={"status": resp.status, "url": url},
                    )
                return await resp.json()
