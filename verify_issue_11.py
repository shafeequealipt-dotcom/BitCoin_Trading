"""Self-verification for Issue #11 — stale Call-B regime.

Offline check against CURRENT code. Two parts:

  A. STATIC: the Call-B builder (_build_position_prompt) now re-reads
     regime_detector.get_last_regime() at build time, emits the
     CALLB_REGIME_FRESH sentinel, and the rendered "MARKET REGIME" line uses
     the freshly-read local value, NOT the stale self._last_regime_str cached
     by the previous Call A.

  B. CONTRACT: get_last_regime() returns the most-recently-committed regime
     from detect() with a freshly-stamped detected_at — so re-reading it at
     Call-B time bounds staleness to one RegimeWorker detection cycle (the
     property the fix relies on), at zero recompute cost.

Run: .venv/bin/python verify_issue_11.py
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from src.config.settings import _build_regime
from src.strategies.regime import RegimeDetector
import tomllib


def static_check():
    src = open("src/brain/strategist.py").read()
    i = src.index("async def _build_position_prompt")
    j = src.index("\n    async def ", i + 1)
    body = src[i:j]
    mr_lines = [ln for ln in body.splitlines() if "## MARKET REGIME:" in ln]
    checks = {
        "reads get_last_regime() in Call-B": "get_last_regime(" in body,
        "emits CALLB_REGIME_FRESH sentinel": "CALLB_REGIME_FRESH" in body,
        "uses fresh local _rb_regime_str": "_rb_regime_str" in body,
        "rendered line not bound to stale cache": bool(mr_lines)
        and all("self._last_regime_str" not in ln for ln in mr_lines),
    }
    return checks


class _StubTA:
    def __init__(self):
        self.next_ta = None

    async def analyze(self, candles=None):
        return self.next_ta


class _StubRepo:
    async def get_klines(self, symbol, tf, n):
        return list(range(200))


async def contract_check():
    cfg = _build_regime(tomllib.load(open("config.toml", "rb")).get("regime", {}))
    ta = _StubTA()
    det = RegimeDetector(SimpleNamespace(regime=cfg), ta, _StubRepo())
    # A clean uptrend so the regime is deterministic.
    ta.next_ta = {
        "trend": {"adx": {"adx": 35.0, "plus_di": 30.0, "minus_di": 10.0}},
        "volatility": {"choppiness_index": 30.0, "atr_14": 1.0, "natr_14": 0.5},
        "volume": {"volume_sma_ratio": 1.0},
    }
    st = await det.detect(symbol="BTCUSDT")
    last = det.get_last_regime()
    age = (datetime.now(timezone.utc) - last.detected_at).total_seconds()
    return {
        "get_last_regime() returns detect() result": last is st,
        "regime committed (trending_up)": last.regime.value == "trending_up",
        "detected_at populated & recent (<5s)": 0.0 <= age < 5.0,
    }


async def main():
    s = static_check()
    c = await contract_check()
    print("ISSUE #11 VERIFICATION — fresh Call-B regime")
    print("  STATIC (Call-B builder):")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print("  CONTRACT (get_last_regime freshness):")
    for k, v in c.items():
        print(f"    {k}: {v}")
    ok = all(s.values()) and all(c.values())
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
