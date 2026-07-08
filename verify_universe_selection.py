#!/usr/bin/env python3
"""Phase 1 proof — does the universe selector pick coins that actually move?

Runs the two-pass selection engine (src/strategies/universe_selector.py)
against live, read-only market data and answers the Phase 1 gate question:
do the selected coins have materially higher recent realized movement than
the current static watch_list they would replace?

It is READ-ONLY: it fetches tickers, daily candles, and open interest
directly from the exchange client and never writes to the database or
changes the live universe. Nothing here touches a protected table or the
running system.

Output is plain prose for a screen reader — no emoji, no tables.

Run from the project root:  python verify_universe_selection.py
"""

from __future__ import annotations

import asyncio

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.types import OHLCV, TimeFrame
from src.core.utils import timestamp_to_datetime
from src.database.connection import DatabaseManager
from src.strategies.universe_selector import (
    realized_volatility_pct,
    select_universe,
)
from src.trading.client import BybitClient
from src.trading.services.market_service import MarketService

log = get_logger(__name__)


def _map_klines(symbol: str, raw_list: list, tf: TimeFrame) -> list[OHLCV]:
    """Map a raw Bybit get_kline response (newest-first) to chronological OHLCV."""
    out: list[OHLCV] = []
    for item in raw_list:
        out.append(
            OHLCV(
                symbol=symbol,
                timeframe=tf,
                timestamp=timestamp_to_datetime(int(item[0])),
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
                volume=float(item[5]),
                turnover=float(item[6]) if len(item) > 6 else 0.0,
            )
        )
    out.reverse()  # Bybit returns newest first; we want oldest -> newest
    return out


async def _gather_daily(
    bybit: BybitClient, symbols: list[str], limit: int, concurrency: int = 8
) -> dict[str, list[OHLCV]]:
    """Fetch daily candles for many symbols, read-only, bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, list[OHLCV]] = {}

    async def _one(sym: str) -> None:
        async with sem:
            try:
                res = await bybit.call(
                    "get_kline",
                    category="linear",
                    symbol=sym,
                    interval=TimeFrame.D1.value,
                    limit=limit,
                )
                out[sym] = _map_klines(sym, res.get("list", []), TimeFrame.D1)
            except Exception as e:
                log.warning(f"daily fetch failed sym={sym} err={str(e)[:80]}")
                out[sym] = []

    await asyncio.gather(*(_one(s) for s in symbols))
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


async def main() -> None:
    settings = Settings.load()
    p = settings.universe.refresh
    current = list(settings.universe.watch_list)
    lookback = p.volatility_lookback_days
    kline_limit = lookback + 3

    db = DatabaseManager(
        settings.database.path,
        lock_wait_warn_ms=settings.database.db_lock_wait_threshold_ms,
        concurrency_model=settings.database.concurrency_model,
        reader_pool_size=settings.database.reader_pool_size,
    )
    await db.connect()
    bybit = BybitClient(settings, db)
    await bybit.connect()
    market = MarketService(
        bybit, db, kline_save_chunk_size=settings.database.kline_save_chunk_size
    )

    # --- Live, read-only fetchers injected into the pure engine ---
    async def fetch_daily(sym: str) -> list[OHLCV]:
        res = await bybit.call(
            "get_kline",
            category="linear",
            symbol=sym,
            interval=TimeFrame.D1.value,
            limit=kline_limit,
        )
        return _map_klines(sym, res.get("list", []), TimeFrame.D1)

    async def fetch_oi(sym: str) -> list[float]:
        res = await bybit.call(
            "get_open_interest",
            category="linear",
            symbol=sym,
            intervalTime="1d",
            limit=lookback + 2,
        )
        items = res.get("list", [])
        vals = [float(it.get("openInterest", "0")) for it in items]
        vals.reverse()  # newest-first -> oldest-first
        return vals

    print("Universe selection proof. Read-only. No live changes.")
    print(f"Lookback for multi-day metrics: {lookback} days.")
    print("Fetching all linear tickers from the exchange...")
    tickers = await market.get_all_linear_tickers()
    print(f"Exchange returned {len(tickers)} linear USDT tickers.")

    res = await select_universe(
        tickers,
        p,
        fetch_daily=fetch_daily,
        fetch_oi=fetch_oi if p.oi_enabled else None,
        force_keep=set(),          # offline proof: no open positions, no core
        current=current,
    )

    # --- Realized-movement comparison: selected vs current static list ---
    cmp_syms = sorted(set(res.selected) | set(current))
    daily_map = await _gather_daily(bybit, cmp_syms, kline_limit)
    sel_vols = [
        realized_volatility_pct(daily_map.get(s, []), lookback)
        for s in res.selected
        if daily_map.get(s)
    ]
    cur_vols = [
        realized_volatility_pct(daily_map.get(s, []), lookback)
        for s in current
        if daily_map.get(s)
    ]
    sel_mean = _mean(sel_vols)
    cur_mean = _mean(cur_vols)
    ratio = (sel_mean / cur_mean) if cur_mean > 0 else 0.0

    # --- Report (plain prose, screen-reader friendly) ---
    lines: list[str] = []
    lines.append("")
    lines.append("Universe selection proof results.")
    lines.append("")
    lines.append("Funnel.")
    lines.append(f"Total tickers from the exchange: {res.total_tickers}.")
    lines.append(f"Removed by the liquidity floor and exclude list before scoring: {res.floored_out}.")
    lines.append(f"Shortlisted into the multi-day pass: {len(res.shortlist)}.")
    lines.append(f"Dropped as thin pumps above the volatility ceiling: {res.dropped_ceiling}.")
    lines.append(f"Coins at or above the strict directionality floor (eligible): {res.eligible_count}.")
    lines.append(f"Coins in the softened reserve band: {res.reserve_count}.")
    lines.append(f"Dropped below the last-resort softened floor (truly choppy): {res.dropped_whipsaw}.")
    lines.append(f"Dropped for insufficient candle history: {res.dropped_insufficient}.")
    lines.append(f"Coins selected into the new universe: {len(res.selected)} (target {p.target_universe_size}, minimum {p.min_universe_size}).")
    if res.softened:
        lines.append(
            f"NOTE: the strict floor was SOFTENED this run — only {res.eligible_count} "
            f"coins cleared it, below the minimum {p.min_universe_size}, so "
            f"{res.softened_added} least-choppy reserve coins were admitted. "
            f"This universe is compromised; a normal market should not soften."
        )
    elif len(res.selected) < p.target_universe_size:
        lines.append(
            f"NOTE: the universe ran SHORT of the target {p.target_universe_size} "
            f"(only {res.eligible_count} coins cleared the strict floor) and was "
            f"left short rather than padded with choppy coins — this is by design."
        )
    lines.append("")
    lines.append("Movement comparison, the Phase 1 gate question.")
    lines.append(
        f"Average recent realized movement of the selected list: "
        f"{sel_mean:.2f} percent average daily range over {lookback} days."
    )
    lines.append(
        f"Average recent realized movement of the current static list: "
        f"{cur_mean:.2f} percent average daily range over {lookback} days."
    )
    lines.append(
        f"The selected list is {ratio:.2f} times as active as the current list."
    )
    kept = sorted(set(res.selected) & set(current))
    lines.append(
        f"Of the current {len(current)} coins, {len(kept)} would be kept and "
        f"{len(res.removed)} would be replaced by fresher movers."
    )
    lines.append("")
    lines.append("Top 15 selected coins by multi-day score, with their factors.")
    top = [c for c in res.scored if not c.dropped][:15]
    for i, c in enumerate(top, 1):
        oi = "n/a" if c.oi_expansion_pct is None else f"{c.oi_expansion_pct:+.1f} percent"
        lines.append(
            f"{i}. {c.symbol}. Score {c.score:.3f}. "
            f"Volatility {c.volatility_pct:.2f} percent daily range. "
            f"Directionality {c.directionality:.2f}. "
            f"Volume surge {c.volume_surge:.2f} times. "
            f"Open interest expansion {oi}."
        )
    lines.append("")
    lines.append("Coins added versus the current list:")
    lines.append(", ".join(res.added) if res.added else "none.")
    lines.append("")
    lines.append("Coins removed versus the current list:")
    lines.append(", ".join(res.removed) if res.removed else "none.")
    lines.append("")

    report = "\n".join(lines)
    print(report)
    with open("verify_universe_selection_report.txt", "w") as f:
        f.write(report)
    print("Report written to verify_universe_selection_report.txt.")

    # Best-effort cleanup (bounded — the report is already saved, and the
    # client's connector can otherwise keep the event loop alive).
    try:
        await asyncio.wait_for(bybit.disconnect(), timeout=5.0)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
