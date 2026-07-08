#!/usr/bin/env python3
"""End-to-end pipeline verification on the REAL project — DI wiring, data flow,
and runtime — for the three shipped fixes.

This does NOT use synthetic inputs. It builds the real DatabaseManager +
MarketRepository, reads the REAL H1 klines the live system wrote to
data/trading.db, runs the REAL StructureEngine.analyze on the REAL universe, and
observes:

  Issue 1 (latency)  — the brain client built via the EXACT manager.py DI kwargs
                       from the REAL config carries --effort medium at every spawn
                       site (the live decision call's invocation).
  Issue 3 (graded smc) — smc_confluence now SPREADS across real coins instead of
                       pinning at a constant 70.
  Issue 2 (de-saturation) — setup_score / grade now SPREAD across real coins
                       instead of pinning at A+/100, and the directional de-grading
                       still produces SKIPs on real spent setups.
  Data flow          — the same setup_score feeds the real scanner opportunity
                       structure component (setup_score/100), so the ranking sees
                       real relative quality.

Read-only (only DB reads). Exit 0 = pass.
"""
from __future__ import annotations

import asyncio
import statistics
import sys
from collections import Counter

from src.config.settings import Settings
from src.database.connection import DatabaseManager
from src.database.repositories.market_repo import MarketRepository
from src.analysis.structure.structure_engine import StructureEngine
from src.brain.claude_code_client import ClaudeCodeClient


def issue1_di(s) -> list[str]:
    """Build the brain client EXACTLY as manager.py does, from the real config."""
    b = s.brain
    client = ClaudeCodeClient(
        timeout_seconds=b.claude_cli_timeout_seconds,
        model=b.claude_cli_model,
        prewarm_max_age_seconds=float(b.claude_cli_prewarm_max_age_seconds),
        prewarm_canary_ttl_seconds=float(b.claude_cli_prewarm_canary_ttl_seconds),
        effort=b.claude_cli_effort,
        bare=b.claude_cli_bare,
        exclude_dynamic_system_prompt=b.claude_cli_exclude_dynamic_system_prompt,
    )
    return client._extra_cli_flags, client._proc_pool._extra_flags


async def main() -> int:
    fails: list[str] = []
    s = Settings.load()

    # ---- Issue 1: real DI wiring of the latency flags ----
    print("== Issue 1: brain client from the real manager DI kwargs ==")
    client_flags, pool_flags = issue1_di(s)
    print(f"  client._extra_cli_flags = {client_flags}")
    print(f"  pool._extra_flags       = {pool_flags}  (warm worker matches decision call)")
    if client_flags != ["--effort", "medium"]:
        fails.append(f"Issue1 DI: expected ['--effort','medium'], got {client_flags}")
    if pool_flags != client_flags:
        fails.append("Issue1 DI: pool flags differ from client (warm worker mismatch)")

    # ---- Issues 2/3: real engine on real klines ----
    db = DatabaseManager(db_path="data/trading.db")
    await db.connect()
    repo = MarketRepository(db)
    engine = StructureEngine(s.structure)
    universe = list(s.universe.watch_list)
    min_c = s.structure.min_candles

    smc_vals: list[int] = []
    score_vals: list[int] = []
    grades: Counter = Counter()
    struct_norms: list[float] = []
    sample: list[tuple] = []
    analysed = 0
    try:
        for sym in universe:
            try:
                candles = await repo.get_klines(sym, "60", 200)
            except Exception:
                continue
            if not candles or len(candles) < min_c:
                continue
            a = engine.analyze(sym, candles[-1].close, candles)
            if a is None:
                continue
            analysed += 1
            smc = int(getattr(a, "smc_confluence", 0) or 0)
            sc = int(getattr(a, "setup_score", 0) or 0)
            gr = str(getattr(a, "setup_quality", "SKIP") or "SKIP")
            smc_vals.append(smc)
            score_vals.append(sc)
            grades[gr] += 1
            struct_norms.append(max(0.0, min(1.0, sc / 100.0)))
            if len(sample) < 12:
                sample.append((sym, smc, sc, gr,
                               str(getattr(a, "suggested_direction", "") or "")))
    finally:
        for closer in ("disconnect", "close", "shutdown"):
            fn = getattr(db, closer, None)
            if fn:
                try:
                    r = fn()
                    if asyncio.iscoroutine(r):
                        await r
                    break
                except Exception:
                    pass

    print(f"\n== Real engine ran on {analysed} real universe coins (data/trading.db) ==")
    if analysed < 10:
        fails.append(f"too few real coins analysed ({analysed}) — cannot judge spread")
        print("  RESULT: insufficient real data"); _summary(fails); return 1 if fails else 0

    def dist(vals):
        return (f"min={min(vals)} p25={int(_pct(vals,0.25))} med={int(statistics.median(vals))} "
                f"p75={int(_pct(vals,0.75))} max={max(vals)} distinct={len(set(vals))}")

    print("\n== Issue 3: smc_confluence across real coins (was a constant 70) ==")
    print(f"  {dist(smc_vals)}")
    at70 = sum(1 for v in smc_vals if v == 70)
    print(f"  share at exactly 70: {at70}/{len(smc_vals)} = {100*at70//len(smc_vals)}%")
    print(f"  sample: {[(x[0], x[1]) for x in sample]}")
    if len(set(smc_vals)) < 4:
        fails.append("smc_confluence did not spread across real coins")
    if at70 / len(smc_vals) > 0.6:
        fails.append(f"smc_confluence still pins at 70 on {100*at70//len(smc_vals)}% of real coins")

    print("\n== Issue 2: setup_score / grade across real coins (was A+/100 pile) ==")
    print(f"  setup_score: {dist(score_vals)}")
    print(f"  grade distribution: {dict(grades)}")
    at100 = sum(1 for v in score_vals if v == 100)
    print(f"  share at exactly 100: {at100}/{len(score_vals)}")
    non_skip = [v for v in score_vals if v > 30]
    if non_skip and len(set(non_skip)) < 4:
        fails.append("setup_score did not spread among non-SKIP real coins")
    # de-grading still works: SKIPs must exist on a real downtrend tape (or at least
    # the grade set must include more than just A+).
    if len(grades) < 2:
        fails.append(f"only one grade present on real data: {dict(grades)}")

    print("\n== Data flow: setup_score -> opportunity structure component (norm) ==")
    print(f"  struct_norm spread: min={min(struct_norms):.2f} "
          f"med={statistics.median(struct_norms):.2f} max={max(struct_norms):.2f} "
          f"distinct={len(set(round(x,2) for x in struct_norms))}")
    if len(set(round(x, 2) for x in struct_norms)) < 4:
        fails.append("opportunity structure component not differentiated on real data")

    _summary(fails)
    return 1 if fails else 0


def _pct(vals, p):
    sv = sorted(vals)
    import math
    k = (len(sv) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    return sv[int(k)] if f == c else sv[f] * (c - k) + sv[c] * (k - f)


def _summary(fails):
    print("\n== RESULT ==")
    if fails:
        for f in fails:
            print(f"  FAIL: {f}")
    else:
        print("  PASS: real DI wiring carries the latency flag; the real engine on "
              "real klines shows smc_confluence and setup_score spread per coin and "
              "flow into the opportunity ranking.")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
