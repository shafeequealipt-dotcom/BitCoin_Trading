"""One-shot diagnostic — why XRAY emits setup_type=none for many coins.

Reads H1 klines for a sample of NONE coins and a sample of FVG_OB coins,
runs the FVG and OB detectors directly, and dumps active/fresh/filled
counts so we can see the actual signal density per coin.

Run from project root:
    .venv/bin/python scripts/xray_none_root_cause_probe.py
"""
import asyncio
import sys

# NONE coins observed in cycle 21:20 (subset)
NONE_SAMPLE = [
    "BTCUSDT",   # no_fresh_bullish_fvg;no_fresh_bullish_ob — uptrend, mtf 0.50, smc 0.00
    "ETHUSDT",   # no_fresh_bullish_fvg                       uptrend, mtf 0.70, smc 0.45
    "SOLUSDT",   # no_fresh_bullish_fvg                       uptrend, mtf 0.60, smc 0.30
    "XRPUSDT",   # no_bullish_bos                              ranging, mtf 0.40, smc 0.00
    "DOGEUSDT",  # no_fresh_bearish_ob                         downtrend, mtf 0.70, smc 0.55
    "AAVEUSDT",  # mtf_score=0.40<fvg_ob_min=0.50              downtrend, mtf 0.40, smc 0.00
]

# Coins that DID classify in cycle 21:20 (pass set)
PASS_SAMPLE = [
    "BNBUSDT",   # bearish_fvg_ob score=78
    "ADAUSDT",   # bullish_fvg_ob score=30
    "LINKUSDT",  # bearish_fvg_ob score=98
    "DYDXUSDT",  # bearish_fvg_ob score=100 (the lone scanner pick)
    "BCHUSDT",   # bearish_fvg_ob score=98
    "NEARUSDT",  # bearish_fvg_ob score=100
]


async def main() -> None:
    # Lazy imports — must run from project root.
    from src.database.connection import DatabaseManager
    from src.database.repositories.market_repo import MarketRepository
    from src.config.settings import Settings
    from src.analysis.structure.fair_value_gap import FairValueGapDetector
    from src.analysis.structure.order_blocks import OrderBlockDetector
    from src.analysis.structure.market_structure import MarketStructureDetector
    from src.core.types import TimeFrame
    import numpy as np

    settings = Settings.load()
    db = DatabaseManager(settings.database.path)
    await db.connect()
    market_repo = MarketRepository(db)

    fvg_det = FairValueGapDetector(settings.structure)
    ob_det = OrderBlockDetector(settings.structure)
    ms_anal = MarketStructureDetector(settings.structure)

    print(f"FVG config:  min_gap_pct={settings.structure.fvg_min_gap_pct}  "
          f"max_age={settings.structure.fvg_max_age_candles}")
    print(f"OB  config:  displacement_min={settings.structure.ob_displacement_min}  "
          f"max_age={settings.structure.ob_max_age_candles}")
    print()

    header = (
        f"{'symbol':<12} {'group':<5} {'candles':>7} "
        f"{'fvg_n':>5} {'fvg_active':>10} "
        f"{'fvg_bull_unfilled':>17} {'fvg_bear_unfilled':>17} "
        f"{'ob_n':>4} {'ob_fresh':>8} "
        f"{'ob_bull_fresh':>13} {'ob_bear_fresh':>13} "
        f"{'last_close':>11} {'atr_pct':>8}"
    )
    print(header)
    print("-" * len(header))

    async def probe(symbol: str, group: str) -> None:
        candles = await market_repo.get_klines(symbol, TimeFrame.H1.value, 200)
        if not candles or len(candles) < 50:
            print(f"{symbol:<12} {group:<5} insufficient_klines={len(candles) if candles else 0}")
            return

        highs  = np.array([c.high  for c in candles], dtype=np.float64)
        lows   = np.array([c.low   for c in candles], dtype=np.float64)
        closes = np.array([c.close for c in candles], dtype=np.float64)
        opens  = np.array([c.open  for c in candles], dtype=np.float64)
        last_close = closes[-1]

        # ATR% over last 14
        tr = np.maximum.reduce([
            highs[-14:] - lows[-14:],
            np.abs(highs[-14:] - closes[-15:-1]),
            np.abs(lows[-14:]  - closes[-15:-1]),
        ])
        atr = float(tr.mean())
        atr_pct = (atr / last_close) * 100.0 if last_close > 0 else 0.0

        fvgs = fvg_det.detect(highs, lows, closes, opens, last_close)
        fvg_active = [f for f in fvgs if not f.filled]
        fvg_bull_unfilled = [f for f in fvg_active if f.direction == "bullish"]
        fvg_bear_unfilled = [f for f in fvg_active if f.direction == "bearish"]

        ms = ms_anal.detect(highs, lows, closes)
        obs = ob_det.detect(highs, lows, closes, opens, last_close, fvgs, ms)
        ob_fresh = [o for o in obs if o.fresh]
        ob_bull_fresh = [o for o in ob_fresh if o.direction == "bullish"]
        ob_bear_fresh = [o for o in ob_fresh if o.direction == "bearish"]

        # Engine logic: direction-filtered + distance < 2% (FVG) / 3% (OB).
        # See structure_engine._find_nearest_fvg / _find_nearest_ob.
        def find_engine_fvg(direction):
            expected = "bullish" if direction == "long" else "bearish"
            for f in fvgs:
                if f.filled or f.direction != expected:
                    continue
                if abs(f.midpoint - last_close) / last_close * 100 < 2.0:
                    return f
            return None

        def find_engine_ob(direction):
            expected = "bullish" if direction == "long" else "bearish"
            for o in obs:
                if not o.fresh or o.direction != expected:
                    continue
                if abs(o.midpoint - last_close) / last_close * 100 < 3.0:
                    return o
            return None

        long_fvg = find_engine_fvg("long")
        long_ob  = find_engine_ob("long")
        short_fvg = find_engine_fvg("short")
        short_ob  = find_engine_ob("short")
        nf = (
            f"L:{'Y' if long_fvg else 'N'}/S:{'Y' if short_fvg else 'N'}"
        )
        no = (
            f"L:{'Y' if long_ob else 'N'}/S:{'Y' if short_ob else 'N'}"
        )
        struct = ms.structure if ms else "?"

        print(
            f"{symbol:<12} {group:<5} {len(candles):>7} "
            f"{len(fvgs):>5} {len(fvg_active):>10} "
            f"{len(fvg_bull_unfilled):>17} {len(fvg_bear_unfilled):>17} "
            f"{len(obs):>4} {len(ob_fresh):>8} "
            f"{len(ob_bull_fresh):>13} {len(ob_bear_fresh):>13} "
            f"{last_close:>11.4f} {atr_pct:>7.2f}% "
            f"struct={struct:<10} eng_fvg[{nf}] eng_ob[{no}]"
        )

    print(">>> NONE coins (XRAY emits setup_type=none) <<<")
    for s in NONE_SAMPLE:
        try:
            await probe(s, "NONE")
        except Exception as e:
            print(f"{s:<12} NONE  err={e}")

    print()
    print(">>> PASS coins (XRAY emits bullish/bearish_fvg_ob) <<<")
    for s in PASS_SAMPLE:
        try:
            await probe(s, "PASS")
        except Exception as e:
            print(f"{s:<12} PASS  err={e}")

    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
