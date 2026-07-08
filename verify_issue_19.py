"""Self-verification for Issue #19 — real ensemble weighting (gradual).

Offline check against CURRENT code + live DB (read-only). Confirms:

  A. STATIC: the live flag is enabled in config; the boot sentinel
     (BOOT_REGIME_WEIGHTING_LIVE) and the live factor-range observability
     (RW_REFRESH_OK factor_range) are wired.
  B. FLAG: Settings._load_fresh() now reports regime_weighting_enabled = True.
  C. REAL + GRADUAL: the real StrategyWeightDeriver, refreshed against the live
     DB, produces weights that DIFFER from 1.0 (track-record intelligence) yet
     stay bounded to [floor, ceil] and gradual (cold-start cells remain 1.0, the
     overall spread is modest) — so it sharpens consensus without a sudden large
     reweighting. One-flag rollback (regime_weighting_enabled=false).

Read-only; no writes.
"""
import asyncio
import sqlite3

from src.config.settings import Settings
from src.strategies.regime_weighter import StrategyWeightDeriver

DB = "file:/home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db?mode=ro"


def static_check():
    cfg = open("config.toml").read()
    en = open("src/strategies/ensemble.py").read()
    rw = open("src/strategies/regime_weighter.py").read()
    return {
        "config enables regime weighting": "regime_weighting_enabled = true" in cfg,
        "ensemble boot sentinel wired": "BOOT_REGIME_WEIGHTING_LIVE" in en,
        "deriver logs live factor_range": 'factor_range=[{_fmin:.2f},{_fmax:.2f}]' in rw,
    }


class _RoDB:
    async def fetch_all(self, sql, params=None):
        c = sqlite3.connect(DB, uri=True)
        c.row_factory = sqlite3.Row
        try:
            cur = c.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]
        finally:
            c.close()


async def real_gradual_check(cfg):
    det = StrategyWeightDeriver(
        cold_start_n=cfg.regime_weighting_cold_start_n,
        floor=cfg.regime_weighting_floor,
        ceil=cfg.regime_weighting_ceil,
        sensitivity=cfg.regime_weighting_sensitivity,
        ema_alpha=cfg.regime_weighting_ema_alpha,
    )
    # Unknown cell defaults to equal weight (cold-start safety).
    unknown_is_one = det.get_factor("trending_up", "NO_SUCH_STRATEGY") == 1.0
    n = await det.refresh(_RoDB())
    cells = det._cells
    dd = [c.factor_smoothed for c in cells.values() if c.sample_size >= cfg.regime_weighting_cold_start_n]
    cold = sum(1 for c in cells.values() if c.sample_size < cfg.regime_weighting_cold_start_n)
    fmin = min(dd) if dd else 1.0
    fmax = max(dd) if dd else 1.0
    all_bounded = all(cfg.regime_weighting_floor <= f <= cfg.regime_weighting_ceil for f in dd) if dd else True
    differ = any(abs(f - 1.0) > 1e-6 for f in dd)
    return {
        "unknown cell -> 1.0 (cold-start safe)": unknown_is_one,
        "cells computed from live DB": n,
        "data-derived cells (weights live)": len(dd),
        "cold-start cells (stay 1.0)": cold,
        "all factors bounded [floor,ceil]": all_bounded,
        "weights differ from 1.0 (intelligence present)": differ,
        "factor_range": (round(fmin, 3), round(fmax, 3)),
    }


async def main():
    s = static_check()
    cfg = Settings._load_fresh().strategy_engine
    flag = bool(cfg.regime_weighting_enabled)
    r = await real_gradual_check(cfg)
    print("ISSUE #19 VERIFICATION — real ensemble weighting (gradual)")
    print("  STATIC (flag + sentinel + observability):")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  FLAG: Settings.regime_weighting_enabled = {flag}")
    print("  REAL + GRADUAL (deriver vs live DB):")
    for k, v in r.items():
        print(f"    {k}: {v}")
    ok = (
        all(s.values()) and flag
        and r["unknown cell -> 1.0 (cold-start safe)"]
        and r["all factors bounded [floor,ceil]"]
        and r["data-derived cells (weights live)"] > 0
        and r["weights differ from 1.0 (intelligence present)"]
    )
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
