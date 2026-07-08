"""Live-style SIMULATION of the original issue situations for the brain-awareness
work, showing each fix responding BEFORE (issue present) vs AFTER (shipped) with a
FIXED / NOT-FIXED verdict, through the REAL production components.

Phase A — book-tilt: the brain about to pile a 7th short onto an all-short book.
  BEFORE = flag off (the original situation: the brain sees only the open-trade
  count, blind to the all-short concentration). AFTER = the tilt line + neutral
  note. Rendered by the REAL _build_trade_prompt with real Position objects.

Phase B — regime_weighter grain inflation: a strategy with a true ~50% win-rate
  whose per-setup rows are duplicated (production grain). BEFORE = the old query
  (SUM over JOIN-duplicated rows / COUNT DISTINCT) inflates win-rate to an
  impossible value and pins the factor to the ceiling. AFTER = the REAL shipped
  refresh() de-dups to the true ~0.50 win-rate and a factor ~1.0.

SAFE: fresh components + a tempfile DB only; no running-process contact, no live
DB writes, no exchange/Claude. Run:
  .venv/bin/python verify_brain_awareness_live_simulation.py
"""

import asyncio
import os
import tempfile

from src.config.settings import Settings
from src.core.types import Side
from verify_brain_awareness_pipeline_runtime import (
    _PosSvc, _Tiered, _pos, _account_section,
)

VERDICTS = []


def verdict(phase, aim, before_lines, after_lines, fixed, note=""):
    VERDICTS.append((phase, fixed))
    print("\n" + "=" * 74)
    print(f"PHASE {phase}")
    print(f"AIM: {aim}")
    print("-" * 74)
    print("BEFORE (issue present):")
    for l in before_lines or ["  (nothing)"]:
        print("   " + l)
    print("AFTER (shipped):")
    for l in after_lines or ["  (nothing)"]:
        print("   " + l)
    if note:
        print("NOTE: " + note)
    print("VERDICT: " + ("FIXED — responds as intended" if fixed else "NOT FIXED"))


# ── Phase A — book-tilt, real _build_trade_prompt, flag off vs on ────────────
async def _render(mix, enabled):
    from src.brain.strategist import ClaudeStrategist
    s = Settings.load()
    s.brain.book_tilt_enabled = enabled
    strat = ClaudeStrategist(
        claude_client=None,
        services={"position_service": _PosSvc([_pos(x) for x in mix]),
                  "tiered_capital": _Tiered()},
        settings=s,
    )
    return _account_section(await strat._build_trade_prompt())


def _tilt_lines(acct):
    return [ln.strip() for ln in acct.splitlines()
            if "Book tilt" in ln or "Open trades:" in ln or "Consider whether" in ln]


def phase_a_book_tilt():
    all_short = [Side.SELL] * 7  # the original failure: 7th short onto all-short book
    off = asyncio.run(_render(all_short, enabled=False))
    on = asyncio.run(_render(all_short, enabled=True))
    fixed = ("Book tilt: 0 long / 7 short — heavily short-tilted" in on
             and "Book tilt:" not in off)
    verdict(
        "A — book-tilt awareness (the all-short pile-on)",
        "the brain must SEE it is piling a new short onto an already one-sided book",
        _tilt_lines(off) + ["(no Book tilt line — brain blind to the all-short concentration)"],
        _tilt_lines(on),
        fixed,
        "AWARENESS only — the line does not block the trade; the brain still decides.",
    )
    # no false alarm on a balanced book
    bal = asyncio.run(_render([Side.BUY, Side.BUY, Side.BUY, Side.SELL, Side.SELL], enabled=True))
    bal_ok = ("Book tilt: 3 long / 2 short — balanced" in bal and "Consider whether" not in bal)
    verdict(
        "A — no false alarm on a balanced book",
        "a balanced book reads 'balanced' with no consider-note (no nagging)",
        ["(n/a — checking the AFTER behavior only)"],
        _tilt_lines(bal),
        bal_ok,
    )


# ── Phase B — regime_weighter grain inflation, old query vs real refresh ─────
_OLD_BUGGY_Q = """
SELECT ev.strategy_name AS strategy_name, ti.entry_regime AS regime,
       COUNT(DISTINCT ti.setup_id) AS sample_size,
       SUM(ti.pnl_pct) AS sum_pnl_pct,
       SUM(CASE WHEN ti.win = 1 THEN 1 ELSE 0 END) AS wins
FROM ensemble_votes ev JOIN trade_intelligence ti ON ev.setup_id = ti.setup_id
WHERE UPPER(ev.vote) = UPPER(ti.direction)
  AND ti.entry_regime IS NOT NULL AND ti.entry_regime != '' AND ev.strategy_name IS NOT NULL
GROUP BY ev.strategy_name, ti.entry_regime
"""

_TI_INS = (
    "INSERT INTO trade_intelligence (symbol, direction, strategy_name, "
    "strategy_category, source, closed_by, entry_price, exit_price, pnl_pct, "
    "pnl_usd, win, hold_seconds, setup_id, entry_regime, exchange_mode, "
    "trade_closed_at, captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


async def _grain_sim():
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "sim.db"))
        await db.connect()
        try:
            await run_migrations(db)
            # 30 setups for B1 in trending_up: a TRUE 0.667 win-rate (20 wins at
            # +3%, 10 losses at -1% -> avg_pnl +1.667% -> a real factor ~1.5).
            # Each setup's vote + analysis row is duplicated 3x to reproduce the
            # production grain (re-analysis + per-cycle re-votes), which inflates
            # BOTH win-rate and avg_pnl (and thus the factor) under the old query.
            for i in range(30):
                sid = f"sim_{i}"
                win = 1 if i < 20 else 0
                pnl = 3.0 if win else -1.0
                for _ in range(3):
                    await db.execute(
                        "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                        "strategy_name, vote, confidence, weight) VALUES (?,?,?,?,?,?,?)",
                        (sid, "BTCUSDT", "Buy", "B1", "BUY", 0.7, 1.0),
                    )
                    await db.execute(
                        _TI_INS,
                        ("BTCUSDT", "Buy", "B1", "momentum", "test",
                         "tp" if win else "sl", 100.0, 100.0 + pnl, pnl, pnl * 5,
                         win, 120.0, sid, "trending_up", "shadow",
                         "2026-05-22T12:00:00+00:00", "2026-05-22T12:00:00+00:00"),
                    )
            # BEFORE: the old buggy query
            old = await db.fetch_all(_OLD_BUGGY_Q)
            r = next(x for x in old if x["strategy_name"] == "B1")
            old_wr = r["wins"] / r["sample_size"]
            old_avg = r["sum_pnl_pct"] / r["sample_size"]
            old_factor_raw = max(0.3, min(3.0, 1.0 + 0.3 * old_avg))
            # AFTER: the REAL shipped refresh
            rw = StrategyWeightDeriver(cold_start_n=20, floor=0.3, ceil=3.0,
                                       sensitivity=0.3, ema_alpha=1.0)
            await rw.refresh(db)
            cell = rw._cells[("trending_up", "B1")]
            return (r["sample_size"], r["wins"], old_wr, old_avg, old_factor_raw,
                    cell.sample_size, cell.win_rate, cell.avg_pnl_pct,
                    cell.factor_smoothed)
        finally:
            await db.disconnect()


def phase_b_grain():
    (o_n, o_w, o_wr, o_avg, o_fac, n_n, n_wr, n_avg, n_fac) = asyncio.run(_grain_sim())
    # FIXED iff: old win-rate is impossible (>1) AND old factor pinned to the
    # ceiling (over-weighting), AND the real refresh restores the true win-rate
    # (~0.667) and the correct factor (~1.5).
    fixed = (o_wr > 1.0 and o_fac >= 2.99
             and abs(n_wr - 0.667) < 0.01 and abs(n_fac - 1.5) < 0.02)
    verdict(
        "B — regime_weighter grain inflation (true 0.667 win-rate, 3x-duplicated rows)",
        "the Layer-3 weighting must compute the TRUE win-rate (~0.67) and the "
        "correct factor (~1.5), not an inflated value that over-weights the strategy",
        [f"old query: sample={o_n} wins={o_w} -> win_rate={o_wr:.2f} "
         f"(IMPOSSIBLE >1), avg_pnl={o_avg:.1f}%, factor would clamp to ceil "
         f"({o_fac:.2f})"],
        [f"real refresh(): sample={n_n} win_rate={n_wr:.2f} (true), "
         f"avg_pnl={n_avg:.1f}%, factor={n_fac:.2f} (neutral)"],
        fixed,
        "Shadow today (regime_weighting_enabled=False) — but correct before that "
        "flag is ever enabled.",
    )


def main():
    print("#" * 74)
    print("BRAIN-AWARENESS LIVE SIMULATION — each fix on its original situation")
    print("#" * 74)
    phase_a_book_tilt()
    phase_b_grain()
    print("\n" + "#" * 74)
    print("SUMMARY")
    print("#" * 74)
    allok = True
    for ph, ok in VERDICTS:
        allok = allok and ok
        print(("FIXED     " if ok else "NOT FIXED ") + ph)
    print("\n" + ("RESULT: ALL PHASES RESPOND AS INTENDED ON THE ORIGINAL SITUATIONS"
                  if allok else "RESULT: ONE OR MORE PHASES DID NOT RESPOND"))
    return 0 if allok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
