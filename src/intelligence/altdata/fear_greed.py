"""Fear & Greed Index client: fetches from Alternative.me API via aiohttp."""

from datetime import datetime, timezone

import aiohttp

from src.config.settings import Settings
from src.core.decorators import retry, timed
from src.core.exceptions import APIError
from src.core.logging import get_logger
from src.core.types import FearGreedData
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.altdata_repo import AltDataRepository

log = get_logger("intelligence")

FEAR_GREED_URL = "https://api.alternative.me/fng/"


class FearGreedClient:
    """Client for the Crypto Fear & Greed Index.

    Args:
        settings: Application settings.
        db: Database manager for persistence.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self._settings = settings
        self._db = db
        self._repo = AltDataRepository(db)
        self._cached_value: FearGreedData | None = None
        self._cached_at: float = 0.0
        self._cache_ttl: float = 3600.0  # 1 hour

    @retry(max_attempts=3, delay=2.0, exceptions=(APIError, aiohttp.ClientError, Exception))
    @timed
    async def fetch_current(self) -> FearGreedData:
        """Fetch the current Fear & Greed Index value.

        Returns:
            FearGreedData with value, classification, and timestamp.

        Raises:
            APIError: On network or parsing failure.
        """
        import time as _time
        now = _time.time()
        if self._cached_value is not None and (now - self._cached_at) < self._cache_ttl:
            log.debug(
                "Fear & Greed: cache hit {v} (age={age:.0f}s)",
                v=self._cached_value.value, age=now - self._cached_at,
            )
            return self._cached_value

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(FEAR_GREED_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        # Phase 12 Gap G1 (output-quality obs): log
                        # status + URL + truncated body BEFORE raising
                        # so the failure context survives even if the
                        # APIError is caught by an upstream broad
                        # except.
                        try:
                            _body = (await resp.text())[:80]
                        except Exception:
                            _body = "<read_failed>"
                        log.warning(
                            f"FEAR_GREED_FETCH_FAIL | url={FEAR_GREED_URL} "
                            f"status={resp.status} body='{_body}'"
                        )
                        raise APIError(
                            f"Fear & Greed API returned status {resp.status}",
                            details={"status": resp.status},
                        )
                    data = await resp.json()

            items = data.get("data", [])
            if not items:
                raise APIError("Fear & Greed API returned empty data")

            item = items[0]
            fg = FearGreedData(
                value=int(item.get("value", "50")),
                classification=item.get("value_classification", "Neutral"),
                timestamp=datetime.fromtimestamp(
                    int(item.get("timestamp", "0")), tz=timezone.utc
                ),
            )

            await self._repo.save_fear_greed(fg)
            self._cached_value = fg
            self._cached_at = _time.time()
            log.info("Fear & Greed Index: {v} ({c})", v=fg.value, c=fg.classification)
            return fg

        except APIError:
            raise
        except aiohttp.ClientError as e:
            raise APIError(f"Fear & Greed network error: {e}", details={"error": str(e)})
        except Exception as e:
            raise APIError(f"Fear & Greed fetch error: {e}", details={"error": str(e)})

    @timed
    async def get_latest(self) -> FearGreedData | None:
        """Get the latest Fear & Greed value, fetching if stale.

        Returns:
            FearGreedData or None if unavailable.
        """
        cached = await self._repo.get_latest_fear_greed()
        if cached is not None:
            age_hours = (now_utc() - cached.timestamp).total_seconds() / 3600
            interval_hours = self._settings.altdata.fear_greed_interval / 3600
            if age_hours < interval_hours:
                return cached

        try:
            return await self.fetch_current()
        except Exception as e:
            # Phase 12 Gap G3 (output-quality obs): when the live fetch
            # fails and we fall back to the cached value, log the
            # cached value's age so operators can SEE we're serving
            # potentially-stale data.
            try:
                _age_h = (
                    (now_utc() - cached.timestamp).total_seconds() / 3600.0
                    if cached and cached.timestamp else -1.0
                )
                _max_h = self._settings.altdata.fear_greed_interval / 3600.0
                log.warning(
                    f"FEAR_GREED_FALLBACK | cached_value="
                    f"{cached.value if cached else 'None'} "
                    f"age_h={_age_h:.1f} max_age_h={_max_h:.1f} "
                    f"reason='{str(e)[:60]}'"
                )
            except Exception:
                log.warning("Failed to fetch Fear & Greed, using cached: {err}", err=str(e))
            return cached

    async def get_history(
        self, days: int = 30, *, limit: int = 10000,
    ) -> list[FearGreedData]:
        """Fetch Fear & Greed history from the database.

        Both ``days`` and ``limit`` are clamped to defensive maxima so
        any UI/MCP caller cannot accidentally trigger an unbounded scan
        of the ``fear_greed_index`` table (Phase 0 baseline observed
        21,516 rows). ``days`` is capped at 365 (one year of history is
        more than any current consumer needs); ``limit`` is capped at
        10,000 (the repo default).

        Args:
            days: How many days of history. Clamped to [1, 365].
            limit: Maximum rows to return. Clamped to [1, 10000].

        Returns:
            List of FearGreedData ordered ascending by timestamp.
        """
        days_clamped = max(1, min(int(days), 365))
        limit_clamped = max(1, min(int(limit), 10000))
        return await self._repo.get_fear_greed_history(
            days=days_clamped, limit=limit_clamped,
        )
