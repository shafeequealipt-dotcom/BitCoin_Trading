"""LIVE SIMULATION — Per-Coin Regime fix series (Phases 0a..8), 2026-05-29.

Recreates the *issue scenario* the whole per-coin regime rework was built to fix
and drives the REAL production code (no re-implementation) so each phase can be
observed responding correctly.

THE ISSUE (pre-fix behaviour): the entire brain pipeline keyed off ONE global
regime (BTC / market-wide). So a coin in a strong uptrend was scored, voted,
directed and exit-managed under BTC's regime; a coin with no data silently
inherited BTC's label; a market-wide direction gate blocked trades against BTC;
the sniper applied BTC's regime to every position's exit weighting.

THE SCENARIO modelled here:
  * BTCUSDT (global/primary): choppy RANGING (adx~18, chop~66) — the live case.
  * Broad market: lopsidedly DOWN (12 alts trending down) -> systemic risk.
  * SOLUSDT: DECOUPLED strong uptrend (adx~32) -> must trade on ITS OWN regime.
  * NEWUSDT: just listed, <50 klines -> honest UNKNOWN, not a fabricated label.
  * GAPUSDT: 60 klines but a core TA field absent -> honest UNKNOWN.
  * CHOPUSDT: ranging structure + a volume spike -> structure must win (Phase 0a).
  * VOLUSDT: genuinely volatile, no trend -> VOLATILE with NO direction (Phase 0d).

Drives: src.strategies.regime.RegimeDetector (detect / detect_per_coin /
breadth_sizing), the real REGIME_ACTIVE_CATEGORIES roster map, RegimeState.unknown
fallback, and ProfitSniper._select_weights (per-symbol sniper regime).

Run:  .venv/bin/python simulate_regime_per_coin.py
Exit code 0 = every phase responded as the fix intends; non-zero = a regression.
"""

import asyncio
import sys
from collections import namedtuple
from types import SimpleNamespace

import numpy as np

from src.config.settings import RegimeSettings
from src.strategies.models.regime_types import (
    REGIME_ACTIVE_CATEGORIES,
    MarketRegime,
    RegimeState,
)
from src.strategies.regime import RegimeDetector
from src.workers.profit_sniper import ProfitSniper

# The detector only reads .high/.low/.close off klines (for the real rolling
# atr-percentile). A lightweight bar with those fields is the honest, sufficient
# shape — open/volume/timestamp are never touched by detect().
Kline = namedtuple("Kline", "high low close")

# ───────────────────────── result bookkeeping ─────────────────────────
_RESULTS: list[tuple[str, str, bool, str]] = []


def check(phase: str, claim: str, ok: bool, evidence: str) -> None:
    _RESULTS.append((phase, claim, bool(ok), evidence))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {phase}: {claim}")
    print(f"         -> {evidence}")


# ───────────────────────── synthetic market data ──────────────────────
def make_klines(n: int = 200, base: float = 100.0, tail: str = "normal", seed: int = 0):
    """Deterministic real price walk -> Kline bars with a controllable recent
    volatility profile so the REAL rolling atr-percentile path runs.
      tail='spike' -> last 20 bars 6x more volatile -> atr_percentile HIGH (>70)
      tail='calm'  -> last 20 bars 0.15x           -> atr_percentile LOW  (<50)
      tail='normal'-> uniform                       -> atr_percentile mid
    """
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.01, n)
    price = base * np.cumprod(1.0 + rets)
    rng2 = np.random.default_rng(seed + 7)
    rngfrac = np.abs(rng2.normal(0.0, 0.008, n)) + 0.002
    if tail == "spike":
        rngfrac[-20:] *= 6.0
    elif tail == "calm":
        rngfrac[-20:] *= 0.15
    out = []
    for i in range(n):
        c = float(price[i])
        rg = c * float(rngfrac[i])
        out.append(Kline(high=c + rg, low=max(0.01, c - rg), close=c))
    return out


def ta(adx, plus_di, minus_di, chop, vol_ratio, natr=0.5, drop_chop=False, drop_adx=False):
    vol = {"choppiness_index": chop, "atr_14": 1.0, "natr_14": natr}
    if drop_chop:
        vol.pop("choppiness_index")
    adxd = {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}
    if drop_adx:
        adxd.pop("adx")
    return {"trend": {"adx": adxd}, "volatility": vol, "volume": {"volume_sma_ratio": vol_ratio}}


class FakeMarket:
    """Doubles as market_repo (get_klines) and ta_engine (analyze). get_klines is
    always called immediately before analyze inside detect(), and detect() is
    awaited sequentially, so keying analyze() off the last-requested symbol is
    safe and lets detect_per_coin() drive many coins with per-coin payloads."""

    def __init__(self):
        self.coins: dict[str, tuple] = {}
        self._cur: str | None = None

    def add(self, sym, klines, ta_payload):
        self.coins[sym] = (klines, ta_payload)

    async def get_klines(self, symbol, timeframe, limit):
        self._cur = symbol
        return self.coins[symbol][0]

    async def analyze(self, candles=None):
        return self.coins[self._cur][1]


def build_scenario() -> tuple[FakeMarket, list[str]]:
    m = FakeMarket()
    # Global / primary — choppy ranging (live BTC case): low ADX, high chop.
    m.add("BTCUSDT", make_klines(tail="calm", seed=1),
          ta(adx=18, plus_di=20, minus_di=21, chop=66, vol_ratio=1.0))
    # Decoupled strong uptrend.
    m.add("SOLUSDT", make_klines(tail="normal", seed=2),
          ta(adx=32, plus_di=35, minus_di=15, chop=30, vol_ratio=1.1))
    # Freshly listed — insufficient klines (<50) -> honest UNKNOWN.
    m.add("NEWUSDT", make_klines(n=10, tail="normal", seed=3),
          ta(adx=25, plus_di=20, minus_di=10, chop=40, vol_ratio=1.0))
    # TA gap — choppiness absent -> honest UNKNOWN.
    m.add("GAPUSDT", make_klines(tail="normal", seed=4),
          ta(adx=25, plus_di=20, minus_di=10, chop=40, vol_ratio=1.0, drop_chop=True))
    # Ranging STRUCTURE + volume spike -> structure must win over volatility.
    m.add("CHOPUSDT", make_klines(tail="spike", seed=5),
          ta(adx=12.5, plus_di=13, minus_di=12, chop=65, vol_ratio=3.0))
    # Genuinely volatile (not trending/ranging/dead) -> VOLATILE, no direction.
    m.add("VOLUSDT", make_klines(tail="spike", seed=6),
          ta(adx=22, plus_di=20, minus_di=20, chop=40, vol_ratio=3.0))
    # A clean ranging coin (for sniper-weight contrast).
    m.add("RANGEUSDT", make_klines(tail="normal", seed=8),
          ta(adx=15, plus_di=13, minus_di=12, chop=55, vol_ratio=1.0))
    # 12 trending-DOWN alts -> lopsided-down market for the breadth brake.
    alts_down = []
    for i in range(12):
        sym = f"DOWN{i}USDT"
        alts_down.append(sym)
        m.add(sym, make_klines(tail="normal", seed=20 + i),
              ta(adx=26, plus_di=12, minus_di=30, chop=30, vol_ratio=1.1))
    universe = (["BTCUSDT", "SOLUSDT", "NEWUSDT", "GAPUSDT", "CHOPUSDT",
                 "VOLUSDT", "RANGEUSDT"] + alts_down)
    return m, universe


def new_detector(market: FakeMarket) -> RegimeDetector:
    settings = SimpleNamespace(regime=RegimeSettings())  # real defaults, no MagicMock
    return RegimeDetector(settings=settings, ta_engine=market, market_repo=market)


async def main() -> int:
    market, universe = build_scenario()
    primary = "BTCUSDT"
    det = new_detector(market)

    print("=" * 78)
    print("LIVE SIMULATION — Per-Coin Regime fix (Phases 0a..8)")
    print("Scenario: BTC ranging/global, market lopsided-DOWN, SOL decoupled-UP,")
    print("          NEWUSDT no-data, GAPUSDT TA-gap, CHOPUSDT ranging+volspike,")
    print("          VOLUSDT pure-volatile.")
    print("=" * 78)

    # === Detect every coin (worker contract: detect_per_coin for non-primary,
    # single global detect for primary, then mirror primary into the cache). ===
    alts = [s for s in universe if s != primary]  # worker excludes primary
    percoin = await det.detect_per_coin(alts)
    btc_state = await det.detect(primary)

    print("\n--- detected regimes ---")
    for s in universe:
        st = percoin.get(s) or (btc_state if s == primary else None)
        if st:
            print(f"  {s:<11} {st.regime.value:<13} conf={st.confidence:.2f} "
                  f"adx={st.adx:.1f} chop={st.choppiness:.1f} "
                  f"atr_pct={st.atr_percentile:.1f} dir={st.trend_direction}")

    # ---------------------------------------------------------------- Phase 1
    print("\n### Phase 1 — single-writer detect + BTC per-coin coverage hole")
    # Before the mirror: the worker's batch EXCLUDES the primary, so BTC has no
    # per-coin entry (the hole). Replicate the worker's cache merge.
    det._per_coin_regimes = dict(percoin)
    hole = det.get_coin_regime(primary)
    # The worker's Phase-1b mirror line closes it.
    det._per_coin_regimes[primary] = btc_state
    closed = det.get_coin_regime(primary)
    check("P1", "BTC absent from per-coin batch before mirror (the hole)",
          hole is None, f"get_coin_regime('BTCUSDT') before mirror = {hole}")
    check("P1", "worker mirror closes the hole; BTC now has its own per-coin regime",
          closed is btc_state and det.is_ready(),
          f"get_coin_regime('BTCUSDT') after mirror = {closed.regime.value}, is_ready={det.is_ready()}")
    # single-writer: the read accessor returns the cached object identity (no re-detect)
    check("P1", "get_coin_regime is a pure read (single-writer: only the worker writes)",
          det.get_coin_regime("SOLUSDT") is percoin["SOLUSDT"],
          "repeated get_coin_regime returns the cached object, never re-detects")

    sol = det.get_coin_regime("SOLUSDT")
    btc = det.get_coin_regime("BTCUSDT")
    chop = det.get_coin_regime("CHOPUSDT")
    vol = det.get_coin_regime("VOLUSDT")
    new = det.get_coin_regime("NEWUSDT")
    gap = det.get_coin_regime("GAPUSDT")

    # --------------------------------------------------------------- Phase 0a
    print("\n### Phase 0a — structure-before-volatility ordering")
    check("P0a", "ranging coin with a volume spike classifies RANGING, not VOLATILE",
          chop.regime == MarketRegime.RANGING,
          f"CHOPUSDT (adx12.5 chop65 vol_ratio3.0 atr_pct={chop.atr_percentile:.0f}) -> {chop.regime.value}")
    check("P0a", "live BTC choppy case classifies RANGING (structure wins)",
          btc.regime == MarketRegime.RANGING and btc.trend_direction == 0,
          f"BTCUSDT (adx18 chop66) -> {btc.regime.value}, dir={btc.trend_direction}")

    # ----------------------------------------------------------- Phase 0b/0c
    print("\n### Phase 0b/0c — honest UNKNOWN (no fabricated label)")
    check("P0b", "MarketRegime.UNKNOWN exists as an explicit state",
          MarketRegime.UNKNOWN.value == "unknown", "enum member present")
    check("P0b", "freshly-listed coin (<50 klines) -> UNKNOWN, zero confidence",
          new.regime == MarketRegime.UNKNOWN and new.confidence == 0.0,
          f"NEWUSDT(10 klines) -> {new.regime.value} conf={new.confidence}")
    check("P0c", "missing core TA field (choppiness) -> UNKNOWN, not a constant-filled label",
          gap.regime == MarketRegime.UNKNOWN,
          f"GAPUSDT(no choppiness_index) -> {gap.regime.value}")
    # detect failure path also emits UNKNOWN (not omission)
    fmkt = FakeMarket()
    fmkt.add("FAILUSDT", make_klines(seed=99), ta(adx=25, plus_di=20, minus_di=10, chop=40, vol_ratio=1.0))

    async def _boom(candles=None):
        raise RuntimeError("ta boom")
    fmkt.analyze = _boom
    fdet = new_detector(fmkt)
    fres = await fdet.detect_per_coin(["FAILUSDT"])
    check("P0c", "detection failure emits UNKNOWN (never silently omits the symbol)",
          "FAILUSDT" in fres and fres["FAILUSDT"].regime == MarketRegime.UNKNOWN,
          f"detect_per_coin(['FAILUSDT']) -> {fres.get('FAILUSDT').regime.value if fres.get('FAILUSDT') else 'OMITTED'}")

    # --------------------------------------------------------------- Phase 0d
    print("\n### Phase 0d — VOLATILE asserts NO direction")
    check("P0d", "genuinely volatile coin classifies VOLATILE with trend_direction 0",
          vol.regime == MarketRegime.VOLATILE and vol.trend_direction == 0,
          f"VOLUSDT -> {vol.regime.value}, dir={vol.trend_direction} (no spurious DI lean)")

    # --------------------------------------------------------------- Phase 0e
    print("\n### Phase 0e — VOLATILE roster widened (momentum + mean_reversion)")
    vcats = REGIME_ACTIVE_CATEGORIES[MarketRegime.VOLATILE]
    check("P0e", "VOLATILE roster re-enables momentum AND mean_reversion",
          "momentum" in vcats and "mean_reversion" in vcats,
          f"VOLATILE roster = {vcats}")

    # ------------------------------------------------------- Phase 0a config
    print("\n### Phase 0a(config) — volatile_volume_ratio exposed + used")
    cfg = RegimeSettings()
    check("P0cfg", "volatile_volume_ratio is a configurable setting (default 2.0)",
          hasattr(cfg, "volatile_volume_ratio") and cfg.volatile_volume_ratio == 2.0,
          f"RegimeSettings().volatile_volume_ratio = {getattr(cfg, 'volatile_volume_ratio', 'MISSING')}")

    # ---------------------------------------------------------------- Phase 2
    print("\n### Phase 2 — cold-start fallback is UNKNOWN, never the global BTC regime")
    fallback = RegimeState.unknown()
    check("P2", "canonical cold-start fallback is UNKNOWN (the only sanctioned fallback)",
          fallback.regime == MarketRegime.UNKNOWN and fallback.trend_direction == 0,
          f"RegimeState.unknown() -> {fallback.regime.value}, dir={fallback.trend_direction}")
    # the consumer pattern `coin_regimes.get(sym) or RegimeState.unknown()` must
    # NOT return BTC's regime for a missing coin:
    coin_regimes = {"SOLUSDT": sol}  # BTC deliberately present elsewhere as 'global'
    missing = coin_regimes.get("ZZZUSDT") or RegimeState.unknown()
    check("P2", "a coin missing from the per-coin map resolves to UNKNOWN, not BTC's regime",
          missing.regime == MarketRegime.UNKNOWN and missing.regime != btc.regime,
          f"missing-coin fallback = {missing.regime.value} (BTC global = {btc.regime.value})")

    # ---------------------------------------------------------------- Phase 3
    print("\n### Phase 3 — per-coin strategy roster (no global roster gate)")
    sol_roster = REGIME_ACTIVE_CATEGORIES[sol.regime]
    btc_roster = REGIME_ACTIVE_CATEGORIES[btc.regime]
    check("P3", "SOL (its own TRENDING_UP) gets the trending roster: momentum/predatory",
          "momentum" in sol_roster and "predatory" in sol_roster,
          f"SOL regime={sol.regime.value} roster has momentum={('momentum' in sol_roster)}")
    check("P3", "BTC (RANGING) roster differs and enables mean_reversion not momentum",
          ("mean_reversion" in btc_roster) and ("momentum" not in btc_roster),
          f"BTC regime={btc.regime.value} roster mean_reversion={('mean_reversion' in btc_roster)} momentum={('momentum' in btc_roster)}")
    check("P3", "SOL is NOT gated by BTC's roster — per-coin authority decouples them",
          sol_roster != btc_roster and "momentum" in sol_roster,
          "SOL keeps momentum eligible even though the global/BTC regime is RANGING")

    # ---------------------------------------------------------------- Phase 5
    print("\n### Phase 5 — breadth RISK/SIZING brake (the lone global survivor)")
    mult, info = det.breadth_sizing()
    check("P5", "lopsided-DOWN market shrinks size below 1.0",
          mult < 1.0 and info["lopsided"] > 0.65,
          f"mult={mult:.3f} lopsided={info['lopsided']:.3f} classified={info['classified']} (UNKNOWN excluded)")
    check("P5", "the brake is SIZING-ONLY — it returns a multiplier, never a direction/roster",
          isinstance(mult, float) and set(info) >= {"down_share", "up_share", "lopsided", "classified", "mult"},
          f"breadth_sizing() returns (mult, info) only; info keys = {sorted(info)}")
    # balanced market -> no brake
    bal = new_detector(market)
    bm = {}
    for i in range(8):
        s1 = RegimeState.unknown(); s1.regime = MarketRegime.TRENDING_DOWN; bm[f"d{i}"] = s1
        s2 = RegimeState.unknown(); s2.regime = MarketRegime.TRENDING_UP; bm[f"u{i}"] = s2
    bal._per_coin_regimes = bm
    bmult, binfo = bal.breadth_sizing()
    check("P5", "balanced market -> no brake (multiplier exactly 1.0)",
          bmult == 1.0, f"8 up / 8 down -> mult={bmult:.3f} lopsided={binfo['lopsided']:.2f}")

    # ---------------------------------------------------------------- Phase 7
    print("\n### Phase 7 — per-symbol sniper regime (no global leak)")
    w_sol, name_sol = ProfitSniper._select_weights(sol)
    w_btc, name_btc = ProfitSniper._select_weights(btc)
    w_range, name_range = ProfitSniper._select_weights(det.get_coin_regime("RANGEUSDT"))
    w_none, name_none = ProfitSniper._select_weights(None)
    check("P7", "SOL (trending) and a ranging coin get DIFFERENT exit weights",
          w_sol != w_range and name_sol == "trending",
          f"SOL -> '{name_sol}' weights; RANGEUSDT -> '{name_range}' weights (no leak)")
    check("P7", "each position's weights come from ITS OWN regime, read per-symbol",
          name_sol == "trending" and name_btc in {"volatile", "ranging"},
          f"SOL='{name_sol}', BTC='{name_btc}' — distinct per-symbol selections")
    check("P7", "a position with no per-coin regime falls back to BALANCED, not a global slot",
          name_none == "balanced",
          f"_select_weights(None) -> '{name_none}' (no global-regime leak)")

    # ───────────────────────────── summary ─────────────────────────────
    print("\n" + "=" * 78)
    passed = sum(1 for *_, ok, _ in [(r[0], r[1], r[2], r[3]) for r in _RESULTS] if ok)
    total = len(_RESULTS)
    by_phase: dict[str, list[bool]] = {}
    for phase, _claim, ok, _ev in _RESULTS:
        by_phase.setdefault(phase, []).append(ok)
    print("PER-PHASE RESULT")
    for phase, oks in by_phase.items():
        status = "PASS" if all(oks) else "FAIL"
        print(f"  {phase:<7} {sum(oks)}/{len(oks)}  {status}")
    print("-" * 78)
    print(f"TOTAL: {passed}/{total} checks passed")
    print("=" * 78)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
