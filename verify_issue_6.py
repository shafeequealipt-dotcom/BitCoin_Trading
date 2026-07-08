"""Self-verification for Issue #6 — regime ELSE dead-zone tiling.

Offline check against the CURRENT code (no live process, no DB writes).
Drives the real RegimeDetector.detect() over a grid of synthetic TA values
with stubbed market_repo + ta_engine, and proves:

  1. Every (adx, choppiness, atr, volume) cell is classified (no None, no crash).
  2. All confidences are in [0, 1].
  3. The fabricated RANGING/0.40 dead-zone is gone: cells that UNDER THE OLD
     logic fell through to `else: RANGING conf=0.40` now carry a COMPUTED,
     signal-bearing confidence (a function of choppiness) or a meaningful
     weak-trend label — never the flat constant.
  4. Concrete before/after on two real former-dead-zone points.

Run: .venv/bin/python verify_issue_6.py
"""
import asyncio
import tomllib
from types import SimpleNamespace

from src.config.settings import _build_regime
from src.strategies.regime import RegimeDetector
from src.strategies.models.regime_types import MarketRegime


def _make_ta(adx, plus_di, minus_di, chop, atr_pct, vol):
    # detect() reads natr_14 then sets atr_percentile = natr * 100 (pre-#9),
    # so feed natr_14 = atr_pct / 100 to land the desired atr_percentile.
    return {
        "trend": {"adx": {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}},
        "volatility": {"choppiness_index": chop, "atr_14": 1.0, "natr_14": atr_pct / 100.0},
        "volume": {"volume_sma_ratio": vol},
    }


class _StubTA:
    def __init__(self):
        self.next_ta = None

    async def analyze(self, candles=None):
        return self.next_ta


class _StubRepo:
    async def get_klines(self, symbol, tf, n):
        return list(range(200))  # detect() only checks len() >= 50


def _old_else(adx, plus_di, minus_di, chop, atr_pct, vol, cfg):
    """Replicate the OLD branch predicate to identify fall-through cells."""
    if adx > cfg.trending_adx_threshold and plus_di > minus_di and chop < 45:
        return False
    if adx > cfg.trending_adx_threshold and minus_di > plus_di and chop < 45:
        return False
    if atr_pct > cfg.volatile_atr_percentile or vol > 2.0:
        return False
    if adx < cfg.ranging_adx_threshold and chop > cfg.ranging_choppiness_threshold:
        return False
    if adx < cfg.dead_adx_threshold and vol < cfg.dead_volume_ratio and atr_pct < 50:
        return False
    return True  # would have hit `else: RANGING conf=0.40`


async def main():
    cfg = _build_regime(tomllib.load(open("config.toml", "rb")).get("regime", {}))
    settings = SimpleNamespace(regime=cfg)
    ta = _StubTA()
    det = RegimeDetector(settings, ta, _StubRepo())

    adxs = [5, 11, 15, 21, 30]
    chops = [25, 35, 40, 46, 55, 65]
    atrs = [20, 55, 85]
    vols = [0.3, 1.0, 2.5]
    dis = [(25.0, 10.0), (10.0, 25.0)]

    total = 0
    none_count = 0
    bad_conf = 0
    old_else_cells = 0
    old_else_now_ranging_computed = 0
    old_else_now_trending = 0
    fabricated_flat = 0  # former-else RANGING cells whose conf is NOT chop-derived
    i = 0
    for adx in adxs:
        for chop in chops:
            for atr_pct in atrs:
                for vol in vols:
                    for plus_di, minus_di in dis:
                        i += 1
                        total += 1
                        ta.next_ta = _make_ta(adx, plus_di, minus_di, chop, atr_pct, vol)
                        st = await det.detect(symbol=f"T{i}")  # unique sym => first reading
                        if st is None:
                            none_count += 1
                            continue
                        if not (0.0 <= st.confidence <= 1.0):
                            bad_conf += 1
                        if _old_else(adx, plus_di, minus_di, chop, atr_pct, vol, cfg):
                            old_else_cells += 1
                            if st.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
                                old_else_now_trending += 1
                            elif st.regime == MarketRegime.RANGING:
                                expected = round(max(0.30, min(chop / 100.0, 0.95)), 2)
                                if round(st.confidence, 2) == expected:
                                    old_else_now_ranging_computed += 1
                                else:
                                    fabricated_flat += 1

    # Concrete before/after on two real former-dead-zone points.
    ta.next_ta = _make_ta(16, 25, 10, 35, 40, 1.0)  # weak adx, low chop -> old else
    p1 = await det.detect(symbol="POINT_B_region")
    ta.next_ta = _make_ta(22, 25, 10, 48, 55, 1.0)  # adx>20 but choppy -> old else
    p2 = await det.detect(symbol="POINT_A_region")

    print("ISSUE #6 VERIFICATION — regime tiling")
    print(f"  grid cells tested        : {total}")
    print(f"  unclassified (None)      : {none_count}  (expect 0)")
    print(f"  confidence out of [0,1]  : {bad_conf}  (expect 0)")
    print(f"  former-ELSE dead-zone cells: {old_else_cells}")
    print(f"    now meaningful weak-trend: {old_else_now_trending}")
    print(f"    now RANGING w/ computed conf: {old_else_now_ranging_computed}")
    print(f"    still fabricated flat conf : {fabricated_flat}  (expect 0)")
    print("  before/after example points (old logic => RANGING/0.40):")
    print(f"    adx16 chop35 atr40: now rgm={p1.regime.value} conf={p1.confidence:.2f}")
    print(f"    adx22 chop48 atr55: now rgm={p2.regime.value} conf={p2.confidence:.2f}")

    ok = (
        none_count == 0
        and bad_conf == 0
        and fabricated_flat == 0
        and old_else_cells > 0
        and (old_else_now_trending + old_else_now_ranging_computed) == old_else_cells
        and p2.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN)
    )
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
