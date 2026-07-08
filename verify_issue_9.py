"""Self-verification for Issue #9 — fake ATR percentile -> true rolling percentile.

Offline check against CURRENT code. Builds REAL synthetic candles, runs the
REAL TAEngine + RegimeDetector.detect() (so the new percentile is computed from
actual klines, not stubbed), and proves:

  1. atr_percentile is ALWAYS bounded [0, 100] (the old code produced a live
     max of 641 — impossible for a percentile).
  2. It is a real RANK: a window whose latest bar is the most volatile yields a
     HIGH percentile; one whose latest bar is the calmest yields a LOW one.
  3. VOLATILE confidence is non-degenerate (> 0.5) when VOLATILE fires — the old
     `min(atr_percentile/200, 1.0)` capped it at 0.5.

Run: .venv/bin/python verify_issue_9.py
"""
import asyncio
import random
import tomllib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.analysis.engine import TAEngine
from src.config.settings import _build_regime
from src.core.types import OHLCV, TimeFrame
from src.strategies.regime import RegimeDetector
from src.strategies.models.regime_types import MarketRegime

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def build_klines(ranges, base=100.0, volumes=None):
    """200-ish oscillating candles with a per-bar high-low fraction in `ranges`."""
    out = []
    price = base
    for i, r in enumerate(ranges):
        price = price * (1.0 + (0.0008 if i % 2 == 0 else -0.0008))  # tiny oscillation, no trend
        hl = price * r
        op = price - hl / 4.0
        cl = price + hl / 4.0
        vol = volumes[i] if volumes else 1000.0
        out.append(OHLCV("TESTUSDT", TimeFrame.H1, _T0 + timedelta(hours=i),
                         op, price + hl / 2.0, price - hl / 2.0, cl, vol))
    return out


class _Repo:
    def __init__(self):
        self.klines = []

    async def get_klines(self, symbol, tf, n):
        return self.klines


async def main():
    cfg = _build_regime(tomllib.load(open("config.toml", "rb")).get("regime", {}))
    settings = SimpleNamespace(regime=cfg)
    repo = _Repo()
    det = RegimeDetector(settings, TAEngine(), repo)

    async def run(klines, sym):
        repo.klines = klines
        return await det.detect(symbol=sym)

    # Case A: latest bar most volatile -> expect HIGH percentile.
    high_latest = await run(build_klines([0.004] * 180 + [0.045] * 20), "HIGH")
    # Case B: latest bar calmest (wide early, calm late) -> expect LOW percentile.
    low_latest = await run(build_klines([0.045] * 20 + [0.004] * 180), "LOW")
    # Case C: volume spike on last bar -> deterministically forces VOLATILE.
    vols = [1000.0] * 199 + [9000.0]
    vol_spike = await run(build_klines([0.006] * 200, volumes=vols), "VOLSPIKE")

    # Bound sweep: 40 random volatility profiles, assert percentile in [0,100].
    bound_ok = True
    max_seen = 0.0
    random.seed(7)
    for i in range(40):
        rngs = [random.uniform(0.001, 0.08) for _ in range(200)]
        st = await run(build_klines(rngs), f"R{i}")
        max_seen = max(max_seen, st.atr_percentile)
        if not (0.0 <= st.atr_percentile <= 100.0):
            bound_ok = False

    print("ISSUE #9 VERIFICATION — true ATR percentile")
    print(f"  bound sweep (40 cases) all in [0,100] : {bound_ok}  (expect True)")
    print(f"  max atr_percentile seen              : {max_seen:.1f}  (expect <= 100; old code hit 641)")
    print(f"  latest-most-volatile percentile      : {high_latest.atr_percentile:.1f}  (expect high)")
    print(f"  latest-calmest percentile            : {low_latest.atr_percentile:.1f}  (expect low)")
    print(f"  rank correct (high > low)            : {high_latest.atr_percentile > low_latest.atr_percentile}")
    print(f"  volume-spike regime                  : {vol_spike.regime.value} conf={vol_spike.confidence:.2f}")
    print(f"  high-vol regime                      : {high_latest.regime.value} conf={high_latest.confidence:.2f}")

    volatile_confs = [
        s.confidence for s in (high_latest, vol_spike) if s.regime == MarketRegime.VOLATILE
    ]
    nondegen = bool(volatile_confs) and all(c > 0.5 for c in volatile_confs)
    print(f"  VOLATILE confidence non-degenerate    : {nondegen}  (expect True; old cap was 0.5)")

    ok = (
        bound_ok
        and max_seen <= 100.0
        and high_latest.atr_percentile > low_latest.atr_percentile
        and high_latest.atr_percentile >= 60.0
        and low_latest.atr_percentile <= 40.0
        and nondegen
    )
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
