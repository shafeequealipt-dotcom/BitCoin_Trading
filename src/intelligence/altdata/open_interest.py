"""Open interest tracker: fetches OI data via BybitClient."""

from datetime import datetime, timezone

from src.core.decorators import retry, timed
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.altdata_repo import (
    OI_LOOKBACK_24H_HOURS,
    AltDataRepository,
)
from src.trading.client import BybitClient

log = get_logger("intelligence")


class OpenInterestTracker:
    """Tracks open interest data via Bybit.

    Args:
        bybit_client: Connected BybitClient.
        db: Database manager.
    """

    def __init__(self, bybit_client: BybitClient, db: DatabaseManager) -> None:
        self._client = bybit_client
        self._db = db
        self._repo = AltDataRepository(db)
        # Five-Fix Follow-Up — Fix 2 (fresh OI, 2026-06-10): the snapshot
        # granularity requested from the exchange is config-driven. The fetch
        # CADENCE was already 5-minute, but with intervalTime="1h" every fetch
        # received the same hourly value, so the stored series was a staircase
        # of 50-minute plateaus and every delta computed from it pinned
        # identical across cycles (the frozen oi_change_pct the brain saw).
        self._interval = str(
            getattr(
                getattr(
                    getattr(
                        getattr(self._client._settings, "workers", None),
                        "sweet_spots", None,
                    ),
                    "altdata", None,
                ),
                "open_interest_interval", "5min",
            )
        )
        log.info(
            f"BOOT_OI_FETCH | interval={self._interval} "
            f"effect={'fresh_snapshot_each_fetch' if self._interval == '5min' else 'coarser_snapshots_deltas_may_plateau'} "
            f"| {ctx()}"
        )

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def fetch_current(self, symbols: list[str] | None = None) -> list[dict]:
        """Fetch current open interest for symbols.

        Args:
            symbols: Trading pairs. Defaults to configured symbols.

        Returns:
            List of dicts with symbol, open_interest, and timestamp.
        """
        if symbols is None:
            symbols = self._client._settings.bybit.default_symbols

        results = []
        for symbol in symbols:
            try:
                result = await self._client.call(
                    "get_open_interest",
                    category="linear",
                    symbol=symbol,
                    # Fix 2 (2026-06-10): config-driven granularity (default
                    # "5min") — items[0] is then the latest 5-minute snapshot
                    # instead of the hourly plateau that froze the deltas.
                    intervalTime=self._interval,
                    limit=2,
                )
                items = result.get("list", [])
                if not items:
                    continue

                current_oi = float(items[0].get("openInterest", "0"))

                await self._repo.save_open_interest(symbol, current_oi)

                # Issue #8 fix (2026-05-27): source the OI delta from the DB —
                # the SAME true ~24h delta the signal generator uses
                # (AltDataRepository._compute_oi_delta_pct, ~23h lookback) —
                # NOT the old 1h-over-1h delta that was mislabeled as 24h (a
                # roughly 25x magnitude understatement that starved the
                # OI-gated strategies). The raw OI was just saved above, so
                # get_latest_open_interest includes it as the latest point.
                # This unifies OI everywhere (strategies, brain, signal) on one
                # honest, correct-magnitude value. The signal-generator path is
                # unchanged — it reads the repo directly, not this fetch.
                _latest = await self._repo.get_latest_open_interest(symbol)
                change_24h = float(_latest.get("change_24h_pct", 0.0)) if _latest else 0.0

                results.append({
                    "symbol": symbol,
                    "open_interest": current_oi,
                    "change_24h_pct": change_24h,
                    "timestamp": now_utc().isoformat(),
                })
            except Exception as e:
                err_str = str(e).lower()
                # Phase 12.1 (lifecycle-logging-audit Gap 1.4-G1): replace
                # prose with structured OI_FETCH_FAIL parallel to funding's
                # FUNDING_FETCH_FAIL pattern. Categorize the failure so
                # operators can see WHICH class dominates.
                if "timeout" in err_str:
                    _cat = "timeout"
                elif "rate limit" in err_str or "429" in err_str or "10003" in err_str:
                    _cat = "rate_limit"
                elif "invalid symbol" in err_str or "symbol not" in err_str or "10001" in err_str:
                    _cat = "invalid"
                else:
                    _cat = "error"
                if _cat == "invalid":
                    log.debug("OI skipped for {s} (invalid symbol): {err}", s=symbol, err=str(e))
                else:
                    log.warning(
                        f"OI_FETCH_FAIL | sym={symbol} category={_cat} "
                        f"err='{str(e)[:80]}' | {ctx()}"
                    )

        log.debug("Fetched OI for {n} symbols", n=len(results))
        return results

    async def backfill_history(self, symbols: list[str] | None = None) -> dict:
        """Seed historical OI snapshots so the 24h / 1h / 15m deltas read true
        values from the first fetch instead of 0.0 during the ~23h cold start
        of a fresh deployment.

        The on-disk deltas (``AltDataRepository._compute_oi_delta_pct``)
        compare the latest snapshot against the closest prior one at least
        ``lookback_hours`` old. On a clean database no such prior exists, so
        the 24h window reads 0.0 until ~23h of live 5-minute fetches accrue.
        This mirrors Shadow's kline backfill: for each symbol lacking
        sufficient history it pulls Bybit's historical OI series in a single
        page (``intervalTime="15min", limit=200`` ≈ 50h of coverage — well
        past the 23h lookback) and stores each point at its REAL exchange
        timestamp via ``save_open_interest_at``.

        Idempotent: symbols that already span the 24h window are skipped, so
        this is safe to invoke on every boot. Returns a summary for the log.

        Args:
            symbols: Trading pairs. Defaults to configured symbols.

        Returns:
            Dict with ``seeded``, ``skipped``, ``failed`` and ``rows`` counts.
        """
        if symbols is None:
            symbols = self._client._settings.bybit.default_symbols

        seeded = skipped = failed = rows = 0
        for symbol in symbols:
            try:
                # Skip symbols whose stored history already covers the 24h
                # lookback — keeps the backfill a true one-time cold-start seed.
                if await self._repo.has_open_interest_older_than(symbol, OI_LOOKBACK_24H_HOURS):
                    skipped += 1
                    continue

                result = await self._client.call(
                    "get_open_interest",
                    category="linear",
                    symbol=symbol,
                    # 15min × 200 = ~50h in ONE page, past the 23h lookback.
                    # Live 5-minute fetches refine the fresh 15m/1h windows
                    # going forward; this only needs to seed the older anchors.
                    intervalTime="15min",
                    limit=200,
                )
                items = result.get("list", []) or []
                n = 0
                for item in items:
                    try:
                        oi = float(item.get("openInterest", "0") or 0.0)
                        ts_ms = int(item.get("timestamp", "0") or 0)
                    except (TypeError, ValueError):
                        continue
                    if oi <= 0 or ts_ms <= 0:
                        continue
                    ts_iso = datetime.fromtimestamp(
                        ts_ms / 1000.0, tz=timezone.utc,
                    ).isoformat()
                    await self._repo.save_open_interest_at(symbol, oi, ts_iso)
                    n += 1
                rows += n
                if n:
                    seeded += 1
            except Exception as e:
                failed += 1
                log.warning(
                    f"OI_BACKFILL_FAIL | sym={symbol} err='{str(e)[:80]}' | {ctx()}"
                )

        log.info(
            f"OI_BACKFILL_DONE | seeded={seeded} skipped={skipped} "
            f"failed={failed} rows={rows} | {ctx()}"
        )
        return {"seeded": seeded, "skipped": skipped, "failed": failed, "rows": rows}

    @timed
    async def get_for_symbol(self, symbol: str) -> dict | None:
        """Get latest open interest for a symbol.

        Args:
            symbol: Trading pair.

        Returns:
            Dict or None.
        """
        return await self._repo.get_latest_open_interest(symbol)

    @timed
    async def get_significant_changes(self, threshold_pct: float = 5.0) -> list[dict]:
        """Find symbols with significant OI changes.

        Args:
            threshold_pct: Minimum absolute change percentage.

        Returns:
            List of dicts for symbols with |change| > threshold.
        """
        all_oi = await self.fetch_current()
        return [oi for oi in all_oi if abs(oi.get("change_24h_pct", 0)) >= threshold_pct]

    async def get_history(self, symbol: str, hours: int = 24) -> list[dict]:
        """Get OI history from database.

        Args:
            symbol: Trading pair.
            hours: How far back.

        Returns:
            List of OI dicts.
        """
        return await self._repo.get_open_interest(symbol, hours)
