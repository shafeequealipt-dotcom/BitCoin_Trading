"""Dynamic universe selector — the movement-score engine.

The selection engine for the daily universe-refresh feature. It rebuilds
the contents of the trading universe around coins in a genuine active
phase, selected on MULTI-DAY activity (never the last-24h move, which
catches exhausted pumps).

Why a two-pass hybrid (operator-approved): the single bulk ticker call
(``MarketService.get_all_linear_tickers``) carries only 24-hour figures
(``high_24h``/``low_24h``/``change_24h_pct``/``volume_24h``), and the
system stores multi-day candles only for the current watch_list, not the
~582 tradeable coins. So a single bulk call cannot produce a multi-day
score. Instead:

  Pass one (cheap, all coins): apply the hard liquidity floor (dollar
  volume, spread, price) from the bulk tickers, then coarse-rank the
  survivors by 24h activity and keep the top ``shortlist_size``.

  Pass two (bounded, shortlist only): fetch multi-day daily candles (and
  open interest) for the shortlist and compute the true multi-day score —
  recent realized volatility (the backbone, highest weight) plus a volume
  surge and open-interest expansion, the whole gated by a directionality
  filter that drops choppy whipsaw coins and demotes the rest.

This module is PURE and IO-free except through injected async fetchers, so
it is unit-testable and can be proven offline before it touches the live
universe (implement-doc Rule 6). It constructs the universe between
trading; it is NOT a per-trade gate (Rule 2).

Honest limit (Rule 16): this picks coins with a much-better-than-random
chance of moving; it cannot pick the day's best 50 in advance.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Sequence

from src.config.settings import UniverseRefreshSettings
from src.core.logging import get_logger
from src.core.types import OHLCV, Ticker

log = get_logger(__name__)

# Injected fetchers (kept abstract so the engine stays IO-free and testable).
DailyFetcher = Callable[[str], Awaitable[Sequence[OHLCV]]]
OIFetcher = Callable[[str], Awaitable[Sequence[float]]]

# Only USDT-perp symbols are selectable: the bulk linear-ticker call can also
# return USDC pairs, and the universe must match the watch_list format that
# UniverseSettings validates (^[A-Z0-9]+USDT$), or applying it would fail.
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+USDT$")


@dataclass
class CoinScore:
    """Per-coin multi-day score and its components (all observable)."""

    symbol: str
    score: float = 0.0
    volatility_pct: float = 0.0      # avg daily range %, the backbone
    volume_surge: float = 1.0        # recent vol / baseline vol
    oi_expansion_pct: float | None = None
    directionality: float = 0.0      # net move / total travel, 0..1
    coarse_activity: float = 0.0     # 24h pre-rank used to build the shortlist
    n_days: int = 0
    # tier: "eligible" (>= strict floor), "reserve" (softened..strict, only
    # used in a last-resort soften), "" when dropped before tiering.
    tier: str = ""
    dropped: bool = False
    reason: str = ""                 # why dropped, for the logs


@dataclass
class SelectionResult:
    """The outcome of one selection run — everything the gate needs to see."""

    selected: list[str] = field(default_factory=list)
    scored: list[CoinScore] = field(default_factory=list)   # shortlist, ranked
    forced_kept: list[str] = field(default_factory=list)     # open positions + core
    added: list[str] = field(default_factory=list)           # vs the current list
    removed: list[str] = field(default_factory=list)
    total_tickers: int = 0
    floored_out: int = 0
    shortlist: list[str] = field(default_factory=list)
    eligible_count: int = 0          # coins at/above the strict floor
    reserve_count: int = 0           # coins in the softened..strict band
    dropped_whipsaw: int = 0         # below the last-resort softened floor
    dropped_ceiling: int = 0         # above the volatility ceiling (pumps)
    dropped_insufficient: int = 0
    softened: bool = False           # last-resort soften was triggered
    softened_added: int = 0          # reserve coins admitted by softening


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def passes_liquidity_floor(t: Ticker, p: UniverseRefreshSettings) -> bool:
    """Hard pre-filter: a mover you cannot enter and exit is worthless.

    Dollar volume must clear the floor, price must clear the dust floor,
    and (when computable) the bid/ask spread must be tight enough.
    Excluded instruments (tokenised commodities / index products) are
    never tradeable here regardless of activity.
    """
    if not _SYMBOL_RE.match(t.symbol):
        return False
    if t.symbol in p.exclude_symbols:
        return False
    if t.volume_24h < p.liquidity_floor_usd:
        return False
    if t.last_price < p.min_price:
        return False
    if t.bid > 0.0 and t.ask > 0.0:
        mid = (t.bid + t.ask) / 2.0
        if mid > 0.0:
            spread_pct = (t.ask - t.bid) / mid * 100.0
            if spread_pct > p.max_spread_pct:
                return False
    return True


def coarse_activity(t: Ticker) -> float:
    """Cheap 24h activity proxy used only to pick the shortlist.

    The daily range as a percent of price, plus half the absolute 24h
    change. This is intentionally a coarse pre-rank — the real, multi-day
    score is computed in pass two on the survivors of this cut.
    """
    if t.last_price <= 0.0:
        return 0.0
    range_pct = (t.high_24h - t.low_24h) / t.last_price * 100.0
    return range_pct + abs(t.change_24h_pct) * 0.5


def realized_volatility_pct(daily: Sequence[OHLCV], lookback_days: int) -> float:
    """Average daily range as a percent of close, over the last N days.

    The backbone factor and the metric used to compare a selected list's
    activity against the current static list (the offline proof).
    """
    bars = [c for c in daily if c.close > 0.0][-lookback_days:]
    if not bars:
        return 0.0
    ranges = [(c.high - c.low) / c.close * 100.0 for c in bars]
    return sum(ranges) / len(ranges)


def directionality_ratio(daily: Sequence[OHLCV]) -> float:
    """Net move over total travel across the bars (0 = chop, 1 = clean trend).

    The whipsaw filter: a coin that travelled far in one direction scores
    high; one with a wide range but near-zero net move scores low.
    """
    bars = [c for c in daily if c.close > 0.0]
    if len(bars) < 2:
        return 0.0
    net = abs(bars[-1].close - bars[0].close)
    travel = sum((c.high - c.low) for c in bars)
    # Clamp to [0,1]: across daily UTC boundaries a gap can make net exceed the
    # summed intraday ranges (>1), which would over-amplify the score beyond the
    # 0..1 contract; cap it so score stays bounded by the weighted raw sum.
    return _clamp01((net / travel) if travel > 0.0 else 0.0)


def compute_multiday_score(
    symbol: str,
    daily: Sequence[OHLCV],
    p: UniverseRefreshSettings,
    *,
    oi_series: Sequence[float] | None = None,
    coarse: float = 0.0,
) -> CoinScore:
    """Score one coin on multi-day activity from its daily candles.

    Volatility (backbone) + volume surge + optional OI expansion, each
    normalised onto 0..1 by its saturation point, summed by the configured
    weights, then multiplied by directionality (net move over total
    travel) so a volatile-but-choppy coin sinks in the ranking.

    Tiering by directionality (the score is always computed so the
    last-resort soften can rank the reserve):
      - >= strict floor                -> tier "eligible"
      - softened floor .. strict floor -> tier "reserve" (soften only)
      - < softened floor               -> dropped (truly choppy)
    A coin above the volatility ceiling is dropped as a thin pump, and a
    coin with too little history is dropped as insufficient.
    """
    bars = [c for c in daily if c.close > 0.0][-p.volatility_lookback_days:]
    cs = CoinScore(symbol=symbol, coarse_activity=coarse, n_days=len(bars))
    if len(bars) < 2:
        cs.dropped = True
        cs.reason = "insufficient_klines"
        return cs

    # Backbone: average daily range %.
    cs.volatility_pct = realized_volatility_pct(bars, p.volatility_lookback_days)

    # Quality ceiling: thin parabolic pumps cannot be exited cleanly.
    if p.volatility_ceiling_pct > 0.0 and cs.volatility_pct > p.volatility_ceiling_pct:
        cs.dropped = True
        cs.reason = "volatility_ceiling"
        return cs

    # Directionality: net move over total travel (0 = pure chop, 1 = clean trend).
    cs.directionality = directionality_ratio(bars)

    # Volume surge: recent dollar volume vs the window baseline.
    turnovers = [c.turnover for c in bars]
    baseline = sum(turnovers) / len(turnovers)
    recent_n = min(2, len(turnovers))
    recent = sum(turnovers[-recent_n:]) / recent_n
    cs.volume_surge = (recent / baseline) if baseline > 0.0 else 1.0

    # Optional OI expansion %.
    if p.oi_enabled and oi_series:
        ois = [v for v in oi_series if v > 0.0]
        if len(ois) >= 2 and ois[0] > 0.0:
            cs.oi_expansion_pct = (ois[-1] - ois[0]) / ois[0] * 100.0

    # Normalise and combine; demote by directionality.
    vol_norm = _clamp01(cs.volatility_pct / p.volatility_saturation_pct)
    denom = (p.volume_surge_saturation - 1.0) or 1.0
    surge_norm = _clamp01((cs.volume_surge - 1.0) / denom)
    oi_norm = 0.0
    if cs.oi_expansion_pct is not None:
        oi_norm = _clamp01(cs.oi_expansion_pct / p.oi_expansion_saturation_pct)
    raw = (
        p.volatility_weight * vol_norm
        + p.volume_surge_weight * surge_norm
        + p.oi_weight * oi_norm
    )
    cs.score = raw * cs.directionality

    # Tier by directionality. The strict floor is the bar; the softened
    # band is held in reserve for a last-resort fill only.
    if cs.directionality >= p.whipsaw_min_directionality:
        cs.tier = "eligible"
    elif cs.directionality >= p.softened_min_directionality:
        cs.tier = "reserve"
    else:
        cs.dropped = True
        cs.reason = "below_softened_floor"
    return cs


async def _gather_bounded(
    symbols: Sequence[str],
    fetch: Callable[[str], Awaitable],
    concurrency: int = 8,
) -> dict[str, object]:
    """Run an async fetch over symbols with bounded concurrency.

    Failures map to None for that symbol (the caller scores without it),
    so one bad symbol never sinks the whole refresh.
    """
    sem = asyncio.Semaphore(max(1, concurrency))
    out: dict[str, object] = {}

    async def _one(sym: str) -> None:
        async with sem:
            try:
                out[sym] = await fetch(sym)
            except Exception as e:  # pragma: no cover - defensive
                log.warning(f"UNIVERSE_FETCH_FAIL | sym={sym} err='{str(e)[:80]}'")
                out[sym] = None

    await asyncio.gather(*(_one(s) for s in symbols))
    return out


async def select_universe(
    tickers: Sequence[Ticker],
    p: UniverseRefreshSettings,
    *,
    fetch_daily: DailyFetcher,
    fetch_oi: OIFetcher | None = None,
    force_keep: set[str] | None = None,
    current: Sequence[str] | None = None,
    concurrency: int = 8,
) -> SelectionResult:
    """Run the full two-pass selection and return the new universe.

    ``force_keep`` (open-position coins plus any stable core) are always in
    the result regardless of score — the open-position safety property and
    the stable-core decision are honored here, not in the scoring.
    ``current`` is the list being replaced, used only to report add/remove.
    """
    # Force-keep bypasses scoring (open positions / stable core must stay), but
    # must still be a valid universe symbol or it would poison the downstream
    # UniverseSettings validation on apply. Drop any non-conforming symbol — the
    # position watchdog manages open positions independently of the watch_list,
    # so a dropped odd-format symbol is still managed, just not re-listed.
    _raw_force = set(force_keep or set())
    force_keep = {s for s in _raw_force if _SYMBOL_RE.match(s)}
    _dropped_force = _raw_force - force_keep
    if _dropped_force:
        log.warning("UNIVERSE_FORCE_KEEP_DROPPED | non_usdt_perp={d} | still managed by watchdog",
                    d=sorted(_dropped_force))
    current_list = list(current or [])
    res = SelectionResult(total_tickers=len(tickers), forced_kept=sorted(force_keep))

    # --- Pass one: liquidity floor + coarse pre-rank over ALL coins ---
    survivors: list[tuple[Ticker, float]] = []
    for t in tickers:
        if not passes_liquidity_floor(t, p):
            res.floored_out += 1
            continue
        survivors.append((t, coarse_activity(t)))
    survivors.sort(key=lambda x: x[1], reverse=True)
    shortlist = [t.symbol for t, _ in survivors[: p.shortlist_size]]
    coarse_by_sym = {t.symbol: c for t, c in survivors}
    res.shortlist = shortlist

    # --- Pass two: multi-day score for the shortlist only ---
    daily_by_sym = await _gather_bounded(shortlist, fetch_daily, concurrency)
    oi_by_sym: dict[str, object] = {}
    if p.oi_enabled and fetch_oi is not None:
        oi_by_sym = await _gather_bounded(shortlist, fetch_oi, concurrency)

    scored: list[CoinScore] = []
    for sym in shortlist:
        daily = daily_by_sym.get(sym) or []
        oi_series = oi_by_sym.get(sym) if isinstance(oi_by_sym.get(sym), (list, tuple)) else None
        cs = compute_multiday_score(
            sym, daily, p, oi_series=oi_series, coarse=coarse_by_sym.get(sym, 0.0)
        )
        scored.append(cs)
        if cs.reason == "below_softened_floor":
            res.dropped_whipsaw += 1
        elif cs.reason == "volatility_ceiling":
            res.dropped_ceiling += 1
        elif cs.reason == "insufficient_klines":
            res.dropped_insufficient += 1

    # Eligible: at/above the strict floor, ranked by full score. Reserve:
    # the softened band, ranked least-choppy first (only used to soften).
    eligible = sorted(
        (c for c in scored if not c.dropped and c.tier == "eligible"),
        key=lambda c: c.score, reverse=True,
    )
    reserve = sorted(
        (c for c in scored if not c.dropped and c.tier == "reserve"),
        key=lambda c: (c.directionality, c.score), reverse=True,
    )
    res.eligible_count = len(eligible)
    res.reserve_count = len(reserve)
    res.scored = sorted(scored, key=lambda c: c.score, reverse=True)

    # --- Build the universe: force-kept first, then eligible up to target ---
    # The universe is allowed to run SHORT (down to min_universe_size)
    # rather than admit choppy coins. Only below the minimum is the floor
    # softened, just enough to reach it, and the day is flagged.
    selected: list[str] = []
    seen: set[str] = set()
    for sym in sorted(force_keep):
        if sym not in seen:
            selected.append(sym)
            seen.add(sym)
    for cs in eligible:
        if len(selected) >= p.target_universe_size:
            break
        if cs.symbol not in seen:
            selected.append(cs.symbol)
            seen.add(cs.symbol)

    if len(selected) < p.min_universe_size and reserve:
        # Last resort only: a genuinely trendless market. Take the
        # least-choppy reserve coins just to reach the minimum, and flag it.
        for cs in reserve:
            if len(selected) >= p.min_universe_size:
                break
            if cs.symbol not in seen:
                selected.append(cs.symbol)
                seen.add(cs.symbol)
                res.softened_added += 1
        if res.softened_added > 0:
            res.softened = True
            log.warning(
                "UNIVERSE_SOFTENED | eligible={el} below_min={mn} "
                "softened_added={sa} softened_floor={sf} strict_floor={st} | "
                "this universe is compromised — too few trenders this cycle",
                el=res.eligible_count, mn=p.min_universe_size,
                sa=res.softened_added, sf=p.softened_min_directionality,
                st=p.whipsaw_min_directionality,
            )

    res.selected = selected
    cur_set = set(current_list)
    sel_set = set(selected)
    res.added = sorted(sel_set - cur_set)
    res.removed = sorted(cur_set - sel_set)

    log.info(
        "UNIVERSE_SELECTION | total={tot} floored_out={fo} shortlist={sl} "
        "eligible={el} reserve={rv} ceiling_dropped={cd} choppy_dropped={wd} "
        "insufficient={ins} selected={sel} target={tg} min={mn} softened={soft} "
        "forced={fk} added={ad} removed={rm}",
        tot=res.total_tickers, fo=res.floored_out, sl=len(shortlist),
        el=res.eligible_count, rv=res.reserve_count, cd=res.dropped_ceiling,
        wd=res.dropped_whipsaw, ins=res.dropped_insufficient,
        sel=len(selected), tg=p.target_universe_size, mn=p.min_universe_size,
        soft=res.softened, fk=len(force_keep), ad=len(res.added), rm=len(res.removed),
    )
    return res
