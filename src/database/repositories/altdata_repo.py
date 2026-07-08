"""Alternative data repository: Fear & Greed, funding rates, open interest, signals."""

import json
from datetime import datetime, timedelta

from src.core.logging import get_logger
from src.core.types import FearGreedData, FundingRate, Signal, SignalType
from src.core.utils import now_utc
from src.database.connection import DatabaseManager

log = get_logger("database")

# OI delta lookbacks (hours). Named here — the repo's single source of truth —
# so the windows are tuning-ready in one place. Each lookback is slightly
# SHORTER than its nominal window so the "most recent row at least this old"
# query lands on the intended snapshot given the 5-minute storage spacing
# (e.g. the 15m window uses a 12.5-minute lookback: rows at -10 and -15
# minutes straddle the cutoff and the query picks -15). The WINDOW choices
# and blend weights live in config [signal_generator.multi_source].
OI_LOOKBACK_24H_HOURS = 23.0
OI_LOOKBACK_1H_HOURS = 50.0 / 60.0
# Five-Fix Follow-Up — Fix 2 (2026-06-10): 15-minute window for the fresh
# directional read.
OI_LOOKBACK_15M_HOURS = 12.5 / 60.0


class AltDataRepository:
    """Repository for alternative data persistence.

    Args:
        db: Active DatabaseManager instance.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # --- Fear & Greed ---

    async def save_fear_greed(self, data: FearGreedData) -> None:
        """Save a Fear & Greed reading.

        Args:
            data: FearGreedData dataclass.
        """
        await self._db.execute(
            "INSERT INTO fear_greed_index (value, classification, timestamp) VALUES (?, ?, ?)",
            (data.value, data.classification, data.timestamp.isoformat()),
        )

    async def get_latest_fear_greed(self) -> FearGreedData | None:
        """Fetch the most recent Fear & Greed value.

        Returns:
            FearGreedData or None.
        """
        row = await self._db.fetch_one(
            "SELECT * FROM fear_greed_index ORDER BY timestamp DESC LIMIT 1"
        )
        if row is None:
            return None
        return FearGreedData(
            value=row["value"],
            classification=row["classification"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )

    async def get_fear_greed_history(
        self, days: int = 30, *, limit: int = 10000,
    ) -> list[FearGreedData]:
        """Fetch Fear & Greed history.

        The query is now bounded both by ``days`` (cutoff) AND by
        ``limit`` (row cap). The Phase 0 baseline of the cascade-fix
        series found 21,516 rows in this table; an unbounded
        ``ORDER BY timestamp ASC`` against TEXT-typed timestamps forces
        SQLite to scan and sort the entire range under the global
        connection mutex. Schema v31 added ``idx_fear_greed_ts_asc`` so
        the ORDER BY is now index-served, and the LIMIT caps the worst
        case at ``limit`` rows regardless of how many rows fall after
        the cutoff.

        Args:
            days: How many days back.
            limit: Maximum rows to return. Defaults to 10,000 — large
                enough for any plausible UI/MCP use (well over a year of
                hourly samples) yet small enough to bound mutex hold
                time even if the table grows. Pass a smaller value
                (e.g. ``limit=200``) when the consumer only needs a
                short tail.

        Returns:
            List of FearGreedData ordered ascending by timestamp.
        """
        cutoff = (now_utc() - timedelta(days=days)).isoformat()
        rows = await self._db.fetch_all(
            "SELECT * FROM fear_greed_index WHERE timestamp > ? "
            "ORDER BY timestamp ASC LIMIT ?",
            (cutoff, int(limit)),
        )
        log.debug(
            "FEAR_GREED_HISTORY_QUERY | days={d} limit={l} returned={n}",
            d=days, l=int(limit), n=len(rows),
        )
        return [
            FearGreedData(
                value=r["value"],
                classification=r["classification"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
            )
            for r in rows
        ]

    # --- Funding Rates ---

    async def save_funding_rate(self, rate: FundingRate) -> None:
        """Save a funding rate record.

        Args:
            rate: FundingRate dataclass.
        """
        await self._db.execute(
            """
            INSERT INTO funding_rates (symbol, funding_rate, next_funding_time, predicted_rate, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                rate.symbol,
                rate.funding_rate,
                rate.next_funding_time.isoformat(),
                rate.predicted_rate,
                rate.fetched_at.isoformat(),
            ),
        )

    async def get_funding_rates(self, symbol: str, hours: int = 24) -> list[FundingRate]:
        """Fetch funding rate history for a symbol.

        Args:
            symbol: Trading pair.
            hours: How far back.

        Returns:
            List of FundingRate.
        """
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        rows = await self._db.fetch_all(
            "SELECT * FROM funding_rates WHERE symbol = ? AND fetched_at > ? ORDER BY fetched_at DESC",
            (symbol, cutoff),
        )
        return [
            FundingRate(
                symbol=r["symbol"],
                funding_rate=r["funding_rate"],
                next_funding_time=datetime.fromisoformat(r["next_funding_time"]),
                predicted_rate=r.get("predicted_rate", 0.0),
                fetched_at=datetime.fromisoformat(r["fetched_at"]),
            )
            for r in rows
        ]

    async def get_latest_funding_rate(self, symbol: str) -> FundingRate | None:
        """Fetch the most recent funding rate for a symbol.

        Args:
            symbol: Trading pair.

        Returns:
            FundingRate or None.
        """
        row = await self._db.fetch_one(
            "SELECT * FROM funding_rates WHERE symbol = ? ORDER BY fetched_at DESC LIMIT 1",
            (symbol,),
        )
        if row is None:
            return None
        return FundingRate(
            symbol=row["symbol"],
            funding_rate=row["funding_rate"],
            next_funding_time=datetime.fromisoformat(row["next_funding_time"]),
            predicted_rate=row.get("predicted_rate", 0.0),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )

    # --- Open Interest ---

    async def save_open_interest(self, symbol: str, oi_value: float) -> None:
        """Save an open interest snapshot.

        Args:
            symbol: Trading pair.
            oi_value: Open interest value.
        """
        await self._db.execute(
            "INSERT INTO open_interest (symbol, open_interest_value) VALUES (?, ?)",
            (symbol, oi_value),
        )

    async def save_open_interest_at(
        self, symbol: str, oi_value: float, timestamp_iso: str,
    ) -> None:
        """Insert an OI snapshot with an explicit historical ``timestamp``.

        ``save_open_interest`` lets the column DEFAULT stamp ``datetime('now')``
        — correct for the live 5-minute fetch. This variant instead persists
        the exchange's *real* snapshot time and is used only by the startup
        backfill (``OpenInterestTracker.backfill_history``) to seed prior
        snapshots, so the 24h / 1h / 15m lookback windows land on genuine
        historical values from cycle 1 rather than reading 0.0 for the first
        ~23h of a fresh deployment.
        """
        await self._db.execute(
            "INSERT INTO open_interest (symbol, open_interest_value, timestamp) "
            "VALUES (?, ?, ?)",
            (symbol, oi_value, timestamp_iso),
        )

    async def has_open_interest_older_than(self, symbol: str, hours: float) -> bool:
        """Return True if ``symbol`` already has a snapshot at least ``hours`` old.

        Lets ``backfill_history`` skip symbols whose stored history already
        spans the 24h lookback window — keeping the backfill idempotent across
        restarts without a schema-level unique constraint. Uses the same
        ``datetime(timestamp)`` normalisation as the delta queries so mixed
        space/'T' timestamp formats compare correctly.
        """
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        row = await self._db.fetch_one(
            "SELECT 1 FROM open_interest WHERE symbol = ? "
            "AND datetime(timestamp) <= datetime(?) LIMIT 1",
            (symbol, cutoff),
        )
        return row is not None

    async def get_open_interest(self, symbol: str, hours: int = 24) -> list[dict]:
        """Fetch open interest history.

        Args:
            symbol: Trading pair.
            hours: How far back.

        Returns:
            List of dicts with open_interest_value and timestamp.
        """
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        # OI-midnight-delta fix (2026-05-27), applied here too for consistency:
        # normalise both sides with datetime() so the space-format stored
        # timestamps and the 'T'-format cutoff compare correctly regardless of
        # the date boundary (same root cause as get_latest_open_interest).
        rows = await self._db.fetch_all(
            "SELECT * FROM open_interest WHERE symbol = ? "
            "AND datetime(timestamp) > datetime(?) ORDER BY datetime(timestamp) DESC",
            (symbol, cutoff),
        )
        return [dict(r) for r in rows]

    async def get_latest_open_interest(self, symbol: str) -> dict | None:
        """Fetch the most recent OI snapshot enriched with computed deltas.

        The on-disk ``open_interest`` table stores raw snapshots only —
        ``id, symbol, open_interest_value, timestamp``. The 24-hour and
        1-hour percentage changes consumed by ``SignalGenerator``'s
        multi-source classifier are reconstructed at read time from the
        most recent prior snapshot at least ``lookback_hours`` old.

        Returns 0.0 for a delta when no prior snapshot exists or the
        prior value is non-positive — gracefully degrades on fresh
        deployments where <24 h of history is in the table.

        Args:
            symbol: Trading pair (e.g. ``"BTCUSDT"``).

        Returns:
            Dict with keys:
              ``id``, ``symbol``, ``open_interest_value``, ``timestamp``
              (verbatim from the latest row), plus
              ``change_24h_pct`` (float, % delta vs the closest snapshot
              at least 23 h old; 0.0 fallback),
              ``change_1h_pct`` (float, % delta vs the closest snapshot
              at least 50 min old; 0.0 fallback) and
              ``change_15m_pct`` (float, % delta vs the closest snapshot
              at least 12.5 min old; 0.0 fallback — Five-Fix Follow-Up
              Fix 2, the fresh directional window).
            ``None`` if no row exists for ``symbol``.
        """
        # OI-midnight-delta fix (2026-05-27): order by datetime(timestamp), not
        # the raw string. Stored rows use the space format from
        # ``datetime('now')`` ("YYYY-MM-DD HH:MM:SS") while cutoffs/other writers
        # may use isoformat ("...T...+00:00"); a raw lexical compare mis-orders a
        # space row vs a 'T' row (space 0x20 < 'T' 0x54). datetime() parses both
        # to a canonical UTC form so ordering is correct regardless of format.
        latest = await self._db.fetch_one(
            "SELECT * FROM open_interest WHERE symbol = ? "
            "ORDER BY datetime(timestamp) DESC LIMIT 1",
            (symbol,),
        )
        if latest is None:
            return None
        result = dict(latest)
        try:
            current_oi = float(result.get("open_interest_value") or 0.0)
        except (TypeError, ValueError):
            current_oi = 0.0
        # 24-hour delta — context window for SignalGenerator (Fix 2 made the
        # short windows the directional drivers; 24h renders as context).
        result["change_24h_pct"] = await self._compute_oi_delta_pct(
            symbol=symbol, current_oi=current_oi,
            lookback_hours=OI_LOOKBACK_24H_HOURS,
        )
        # 1-hour delta — directional driver window.
        result["change_1h_pct"] = await self._compute_oi_delta_pct(
            symbol=symbol, current_oi=current_oi,
            lookback_hours=OI_LOOKBACK_1H_HOURS,
        )
        # 15-minute delta — the freshest directional driver window
        # (Five-Fix Follow-Up Fix 2, 2026-06-10).
        result["change_15m_pct"] = await self._compute_oi_delta_pct(
            symbol=symbol, current_oi=current_oi,
            lookback_hours=OI_LOOKBACK_15M_HOURS,
        )
        return result

    async def _compute_oi_delta_pct(
        self,
        *,
        symbol: str,
        current_oi: float,
        lookback_hours: float,
    ) -> float:
        """Return percentage change of ``current_oi`` vs the closest prior
        snapshot at least ``lookback_hours`` old.

        Picks the most-recent row whose ``timestamp`` is no more recent
        than ``now - lookback_hours``.

        OI-midnight-delta fix (2026-05-27): the comparison and ordering use
        ``datetime(timestamp) <= datetime(?)`` rather than a raw string compare.
        Stored rows use the space format from ``datetime('now')`` while the
        cutoff uses isoformat ("...T...+00:00"); a raw lexical compare made a
        same-date space-format row wrongly sort at-or-below a 'T'-format cutoff
        (space 0x20 < 'T' 0x54 at the separator position), so for the ~1 hour
        each day when ``now - lookback_hours`` shares the current UTC date the
        query selected the latest row as its own "prior" and the delta collapsed
        to ~0. Normalising both sides with datetime() (which parses space, 'T',
        fractional seconds and timezone to a canonical UTC form) makes the
        comparison correct at every hour and robust to mixed stored formats.
        The OI table is tiny per symbol (hourly snapshots), so dropping the
        raw-index hit for the datetime() normalisation is negligible.

        Returns 0.0 when no prior snapshot is available or the prior
        value is non-positive — the only safe default for a delta.
        """
        if current_oi <= 0:
            return 0.0
        cutoff = (now_utc() - timedelta(hours=lookback_hours)).isoformat()
        row = await self._db.fetch_one(
            "SELECT open_interest_value FROM open_interest "
            "WHERE symbol = ? AND datetime(timestamp) <= datetime(?) "
            "ORDER BY datetime(timestamp) DESC LIMIT 1",
            (symbol, cutoff),
        )
        if row is None:
            return 0.0
        try:
            prior = float(row["open_interest_value"] or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if prior <= 0:
            return 0.0
        return round((current_oi - prior) / prior * 100.0, 4)

    # --- Signals ---

    async def save_signal(self, signal: Signal) -> None:
        """Save a trading signal.

        Layer 2 Defect 3 (2026-05-22) promotes two values that already live
        in ``signal.components`` (per the Phase 4B fix at signal_generator.py:
        296-297) to top-level columns so Layer 4 label-quality analysis can
        query them directly without JSON parsing:

            - ``signal_type_pre_downgrade``: the classifier's pre-downgrade
              emission (e.g., ``strong_buy``) before the Phase 29 confidence
              gate downgraded it to ``signal_type``. None when the components
              dict lacks the field (legacy callers).
            - ``confidence_floor_failed``: 0/1 flag indicating the downgrade
              event happened. None when the components dict lacks the field.

        Args:
            signal: Signal dataclass.
        """
        _orig_label = signal.components.get("original_signal_type") if signal.components else None
        _downgrade_flag = signal.components.get("confidence_floor_failed") if signal.components else None
        # Coerce the boolean flag to INTEGER (0/1) for SQLite; preserve None.
        _downgrade_int: int | None
        if _downgrade_flag is None:
            _downgrade_int = None
        else:
            _downgrade_int = 1 if _downgrade_flag else 0
        await self._db.execute(
            """
            INSERT INTO signals (
                symbol, signal_type, confidence, source, components,
                reasoning, created_at,
                signal_type_pre_downgrade, confidence_floor_failed
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.symbol,
                signal.signal_type.value,
                signal.confidence,
                signal.source,
                json.dumps(signal.components),
                signal.reasoning,
                signal.created_at.isoformat(),
                _orig_label,
                _downgrade_int,
            ),
        )

    async def get_latest_signal(self, symbol: str) -> Signal | None:
        """Fetch the most recent signal for a symbol.

        Args:
            symbol: Trading pair.

        Returns:
            Signal or None.
        """
        row = await self._db.fetch_one(
            "SELECT * FROM signals WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
            (symbol,),
        )
        if row is None:
            return None
        components = row.get("components", "{}")
        if isinstance(components, str):
            try:
                components = json.loads(components)
            except (json.JSONDecodeError, TypeError):
                components = {}
        return Signal(
            symbol=row["symbol"],
            signal_type=SignalType(row["signal_type"]),
            confidence=row["confidence"],
            source=row.get("source", ""),
            components=components,
            reasoning=row.get("reasoning", ""),
            created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else now_utc(),
        )
