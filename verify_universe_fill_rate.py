#!/usr/bin/env python3
"""Phase 1 fill-rate replay — how often does the strict floor run the universe short?

Answers the operator's gate question before any softening default is set:
across recent days, how often does the strict directionality floor leave
fewer than the target 50 coins, and by how much? This shows whether the
"run short rather than admit choppy coins" policy keeps a healthy universe
or whether the market routinely forces a short list.

Method (READ-ONLY, no DB writes, no live changes): take the realistic
candidate pool (the most liquid non-excluded coins now), fetch their daily
candles, and for each of the last N complete days recompute each coin's
trailing multi-day directionality and volatility AS OF that day, then count
how many clear the strict floor (with the per-day liquidity floor and the
volatility ceiling applied). Report the distribution of that daily count
against the target and the minimum.

Output is plain prose for a screen reader — no emoji, no tables.

Run from the project root:  python verify_universe_fill_rate.py
"""

from __future__ import annotations

import asyncio

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.types import OHLCV, TimeFrame
from src.core.utils import timestamp_to_datetime
from src.database.connection import DatabaseManager
from src.strategies.universe_selector import (
    directionality_ratio,
    realized_volatility_pct,
)
from src.trading.client import BybitClient
from src.trading.services.market_service import MarketService

log = get_logger(__name__)

REPLAY_DAYS = 14        # how many recent complete days to evaluate
POOL_SIZE = 220         # most-liquid non-excluded coins to consider


def _map_klines(symbol: str, raw_list: list, tf: TimeFrame) -> list[OHLCV]:
    out: list[OHLCV] = []
    for item in raw_list:
        out.append(
            OHLCV(
                symbol=symbol, timeframe=tf,
                timestamp=timestamp_to_datetime(int(item[0])),
                open=float(item[1]), high=float(item[2]), low=float(item[3]),
                close=float(item[4]), volume=float(item[5]),
                turnover=float(item[6]) if len(item) > 6 else 0.0,
            )
        )
    out.reverse()  # newest-first -> chronological
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


async def main() -> None:
    settings = Settings.load()
    p = settings.universe.refresh
    lookback = p.volatility_lookback_days
    limit = lookback + REPLAY_DAYS + 5

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

    print("Universe fill-rate replay. Read-only. No live changes.")
    tickers = await market.get_all_linear_tickers()
    pool = [
        t for t in tickers
        if t.symbol not in p.exclude_symbols and t.last_price > 0.0
    ]
    pool.sort(key=lambda t: t.volume_24h, reverse=True)
    pool_syms = [t.symbol for t in pool[:POOL_SIZE]]
    print(f"Candidate pool: {len(pool_syms)} most-liquid non-excluded coins.")
    print(f"Fetching {limit} daily candles each and replaying {REPLAY_DAYS} days...")

    sem = asyncio.Semaphore(8)
    daily: dict[str, list[OHLCV]] = {}

    async def _one(sym: str) -> None:
        async with sem:
            try:
                res = await bybit.call(
                    "get_kline", category="linear", symbol=sym,
                    interval=TimeFrame.D1.value, limit=limit,
                )
                daily[sym] = _map_klines(sym, res.get("list", []), TimeFrame.D1)
            except Exception as e:
                log.warning(f"daily fetch failed sym={sym} err={str(e)[:80]}")
                daily[sym] = []

    await asyncio.gather(*(_one(s) for s in pool_syms))

    # For each of the last REPLAY_DAYS complete days, gather the trailing
    # directionality of every coin that passes the per-day liquidity floor
    # and the volatility ceiling. Excludes the newest (likely partial) bar.
    # Storing the raw directionalities lets us sweep several candidate
    # floors over the SAME data without re-fetching.
    # Per day, keep every coin that passes the per-day liquidity floor and
    # the volatility ceiling, as (symbol, directionality, volatility). This
    # lets us sweep floors AND list the actual coins a floor admits on a
    # typical day versus a choppy day, over the SAME fetched data.
    per_day_coins: list[list[tuple[str, float, float]]] = []
    for k in range(1, REPLAY_DAYS + 1):
        coins: list[tuple[str, float, float]] = []
        for sym in pool_syms:
            bars = daily.get(sym) or []
            end = len(bars) - 1 - k          # inclusive index of the as-of day
            start = end - lookback + 1
            if start < 0 or end < 0:
                continue
            window = bars[start:end + 1]
            if len(window) < 2:
                continue
            if bars[end].turnover < p.liquidity_floor_usd:
                continue
            vol = realized_volatility_pct(window, lookback)
            if p.volatility_ceiling_pct > 0.0 and vol > p.volatility_ceiling_pct:
                continue
            coins.append((sym, directionality_ratio(window), vol))
        per_day_coins.append(coins)

    target = p.target_universe_size
    minimum = p.min_universe_size
    n_days = len(per_day_coins) or 1

    def outcome(floor: float) -> tuple[list[int], float, int, int, int, float]:
        counts = [sum(1 for (_s, d, _v) in coins if d >= floor) for coins in per_day_coins]
        full = sum(1 for c in counts if c >= target)
        short = sum(1 for c in counts if minimum <= c < target)
        below = sum(1 for c in counts if c < minimum)
        avg = _mean([float(c) for c in counts])
        return counts, avg, full, short, below, 0.0

    # Sweep candidate floors plus the configured one.
    floors = sorted({0.15, 0.20, 0.25, 0.30, round(p.whipsaw_min_directionality, 2)})

    lines: list[str] = []
    lines.append("")
    lines.append("Universe fill-rate replay results.")
    lines.append("")
    lines.append(
        f"Settings: target {target} coins, minimum {minimum}, configured strict "
        f"floor {p.whipsaw_min_directionality}, lookback {lookback} days, "
        f"liquidity floor {p.liquidity_floor_usd:.0f} dollars, "
        f"volatility ceiling {p.volatility_ceiling_pct} percent."
    )
    lines.append(f"Candidate pool size: {len(pool_syms)} coins. Days replayed: {n_days}.")
    lines.append("")
    lines.append(
        "Floor sensitivity. For each candidate strict floor, how the daily "
        "count of qualifying coins behaves across the replayed days. The aim "
        "is a floor where most days reach the minimum on their own, so "
        "softening stays the rare last resort it is meant to be."
    )
    lines.append("")
    for floor in floors:
        counts, avg, full, short, below = outcome(floor)[:5]
        tag = " (configured)" if abs(floor - p.whipsaw_min_directionality) < 1e-9 else ""
        lines.append(f"Strict floor {floor:.2f}{tag}:")
        lines.append(
            f"  Average qualifying per day {avg:.1f} "
            f"(minimum {min(counts)}, maximum {max(counts)})."
        )
        lines.append(
            f"  Reached the target {target} on {full} of {n_days} days "
            f"({full*100//n_days} percent); ran short but at or above the "
            f"minimum {minimum} on {short} ({short*100//n_days} percent); "
            f"below the minimum, would have softened, on {below} "
            f"({below*100//n_days} percent)."
        )
        lines.append(f"  Per-day counts, most recent first: {', '.join(str(c) for c in counts)}.")
        lines.append("")

    # Eyeball check at the configured floor: the actual coins it admits on
    # a typical (highest-count) day and a choppy (lowest-count) day, so the
    # operator can judge whether the bar admits genuine trends or borderline.
    cf = p.whipsaw_min_directionality
    cf_counts = [sum(1 for (_s, d, _v) in coins if d >= cf) for coins in per_day_coins]
    if cf_counts:
        typical_k = max(range(len(cf_counts)), key=lambda i: cf_counts[i])
        choppy_k = min(range(len(cf_counts)), key=lambda i: cf_counts[i])

        def list_day(k: int, label: str) -> None:
            admitted = sorted(
                (c for c in per_day_coins[k] if c[1] >= cf),
                key=lambda c: c[1], reverse=True,
            )
            shown = admitted[: max(target, minimum)]
            lines.append(
                f"{label} (day {k + 1} back from today): {len(admitted)} coins "
                f"clear the {cf:.2f} floor. The selection would take up to "
                f"{target} of these, never fewer than {minimum}. Coins, most "
                f"directional first, with directionality and 7-day average daily range:"
            )
            for s, d, v in shown:
                lines.append(f"  {s}: directionality {d:.2f}, volatility {v:.1f} percent.")
            lines.append("")

        lines.append(f"Eyeball check at the configured floor {cf:.2f}.")
        lines.append("")
        list_day(typical_k, "A TYPICAL (more active) day")
        list_day(choppy_k, "A CHOPPY (least active) day")

    lines.append(
        "Reading: pick the strict floor at which softening (days below the "
        "minimum) is rare rather than routine, while the floor still keeps "
        "genuinely choppy coins out. A floor that softens most days is too "
        "high for this market; one that never runs short may be too low to "
        "exclude whipsaw. The minimum and the last-resort softened floor can "
        "then be set around the chosen strict floor."
    )
    lines.append("")

    report = "\n".join(lines)
    print(report)
    with open("verify_universe_fill_rate_report.txt", "w") as f:
        f.write(report)
    print("Report written to verify_universe_fill_rate_report.txt.")

    try:
        await asyncio.wait_for(bybit.disconnect(), timeout=5.0)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
