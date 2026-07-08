"""Funding rate tracker: fetches perpetual contract funding rates via BybitClient."""

from datetime import datetime, timezone

from src.core.decorators import retry, timed
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import FundingRate
from src.core.utils import now_utc, timestamp_to_datetime
from src.database.connection import DatabaseManager
from src.database.repositories.altdata_repo import AltDataRepository
from src.trading.client import BybitClient

log = get_logger("intelligence")


class FundingRateTracker:
    """Tracks perpetual contract funding rates via Bybit.

    Args:
        bybit_client: Connected BybitClient.
        db: Database manager.
    """

    def __init__(self, bybit_client: BybitClient, db: DatabaseManager) -> None:
        self._client = bybit_client
        self._db = db
        self._repo = AltDataRepository(db)

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def fetch_current_rates(self, symbols: list[str] | None = None) -> list[FundingRate]:
        """Fetch current funding rates for symbols.

        Uses Bybit tickers which include funding rate info.

        Args:
            symbols: List of trading pairs. Defaults to configured symbols.

        Returns:
            List of FundingRate dataclasses.
        """
        if symbols is None:
            symbols = self._client._settings.bybit.default_symbols

        rates = []
        for symbol in symbols:
            try:
                result = await self._client.call(
                    "get_tickers", category="linear", symbol=symbol
                )
                items = result.get("list", [])
                if not items:
                    continue

                data = items[0]
                funding_rate_str = data.get("fundingRate", "0")
                next_time_str = data.get("nextFundingTime", "0")

                fr = FundingRate(
                    symbol=symbol,
                    funding_rate=float(funding_rate_str),
                    next_funding_time=timestamp_to_datetime(int(next_time_str)) if next_time_str != "0" else now_utc(),
                    predicted_rate=0.0,
                    fetched_at=now_utc(),
                )
                await self._repo.save_funding_rate(fr)
                rates.append(fr)

            except Exception as e:
                err_str = str(e).lower()
                # Phase 12 Gap G2 (output-quality obs): categorise the
                # failure so operators see WHICH class dominates over
                # 1 hour. timeout / rate_limit / invalid / generic each
                # require different operator response.
                if "timeout" in err_str:
                    _cat = "timeout"
                elif "rate limit" in err_str or "429" in err_str or "10003" in err_str:
                    _cat = "rate_limit"
                elif "invalid symbol" in err_str or "symbol not" in err_str or "10001" in err_str:
                    _cat = "invalid"
                else:
                    _cat = "error"
                if _cat == "invalid":
                    log.debug("Funding rate skipped for {s} (invalid symbol): {err}", s=symbol, err=str(e))
                else:
                    # Phase 12.1 (lifecycle-logging-audit Gap 1.4-G2):
                    # added missing | {ctx()} suffix for cycle correlation.
                    log.warning(
                        f"FUNDING_FETCH_FAIL | sym={symbol} "
                        f"category={_cat} err='{str(e)[:80]}' | {ctx()}"
                    )

        log.debug("Fetched {n} funding rates", n=len(rates))
        return rates

    @timed
    async def get_rate_for_symbol(self, symbol: str) -> FundingRate | None:
        """Get the latest funding rate for a symbol.

        Args:
            symbol: Trading pair.

        Returns:
            FundingRate or None.
        """
        return await self._repo.get_latest_funding_rate(symbol)

    @timed
    async def get_extreme_rates(self, threshold: float = 0.01) -> list[FundingRate]:
        """Find symbols with abnormally high or low funding rates.

        Extreme funding rates can signal crowded trades and potential reversals.

        Args:
            threshold: Absolute threshold for "extreme" (default 1%).

        Returns:
            List of FundingRate with |rate| > threshold.
        """
        symbols = self._client._settings.bybit.default_symbols
        rates = await self.fetch_current_rates(symbols)
        return [r for r in rates if abs(r.funding_rate) >= threshold]

    async def get_rate_history(self, symbol: str, hours: int = 24) -> list[FundingRate]:
        """Get funding rate history from the database.

        Args:
            symbol: Trading pair.
            hours: How far back.

        Returns:
            List of FundingRate.
        """
        return await self._repo.get_funding_rates(symbol, hours)
