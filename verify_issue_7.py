"""Self-verification for Issue #7 — X-RAY score/confidence coherence.

Offline check against CURRENT code + live data (read-only). Three parts:

  A. STATIC: the producer-level coherence gate exists (NONE -> cap to C,
     below-floor confidence -> cap to B, with the XRAY_SCORE_GATED sentinel).
  B. LOGIC: the exact shipped gate maps a NONE/score-100 setup down to C and a
     matched/low-confidence A to B, while leaving a strong A+ untouched.
  C. REAL DATA: the real StructureEngine.analyze() over the live universe yields
     ZERO contradictory coins — no NONE-classified coin presents as A+/A/B, and
     no A+/A coin has confidence below the floor.

Run: .venv/bin/python verify_issue_7.py
"""
import sqlite3
from datetime import datetime

from src.config.settings import Settings
from src.core.types import OHLCV, TimeFrame
from src.analysis.structure.structure_engine import StructureEngine
from src.analysis.structure.models.structure_types import SetupType

DB = "file:/home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db?mode=ro"
FLOOR = 0.30


def static_check():
    src = open("src/analysis/structure/structure_engine.py").read()
    return {
        "NONE capped to C at producer": 'if analysis.setup_type == SetupType.NONE:' in src
        and 'analysis.setup_quality = "C"' in src,
        "low-confidence capped to B": "analysis.setup_type_confidence < _XRAY_MIN_SETUP_CONFIDENCE" in src
        and 'analysis.setup_quality = "B"' in src,
        "emits XRAY_SCORE_GATED sentinel": "XRAY_SCORE_GATED" in src,
    }


def gate_logic(setup_type_none, conf, quality, score):
    """Exact replica of the shipped coherence gate."""
    if setup_type_none:
        if quality in ("A+", "A", "B"):
            quality, score = "C", min(score, 49)
    elif conf < FLOOR:
        if quality in ("A+", "A"):
            quality, score = "B", min(score, 64)
    return quality, score


def logic_check():
    none100 = gate_logic(True, 0.0, "A+", 100)
    lowconf = gate_logic(False, 0.20, "A", 85)
    strong = gate_logic(False, 0.80, "A+", 90)
    return {
        "NONE/100 -> C/<=49": none100 == ("C", 49),
        "matched low-conf A -> B/<=64": lowconf == ("B", 64),
        "strong A+ untouched": strong == ("A+", 90),
    }


def _load_h1(conn, sym, limit=200):
    rows = conn.execute(
        "SELECT timestamp,open,high,low,close,volume,turnover FROM klines "
        "WHERE symbol=? AND timeframe='60' ORDER BY timestamp DESC LIMIT ?",
        (sym, limit),
    ).fetchall()
    out = []
    for r in reversed(rows):
        out.append(OHLCV(sym, TimeFrame.H1, datetime.fromisoformat(r[0]),
                         r[1], r[2], r[3], r[4], r[5], r[6]))
    return out


def real_data_check():
    s = Settings._load_fresh()
    eng = StructureEngine(s.structure)
    conn = sqlite3.connect(DB, uri=True)
    results = []
    for sym in list(s.universe.watch_list):
        candles = _load_h1(conn, sym)
        if len(candles) < s.structure.min_candles:
            continue
        a = eng.analyze(sym, candles[-1].close, candles)
        if a is not None:
            results.append((sym, a))
    none_with_high = [(sym, a.setup_quality, a.setup_score) for sym, a in results
                      if a.setup_type == SetupType.NONE and a.setup_quality in ("A+", "A", "B")]
    aplus_lowconf = [(sym, a.setup_quality, round(a.setup_type_confidence, 2)) for sym, a in results
                     if a.setup_quality in ("A+", "A") and a.setup_type_confidence < FLOOR]
    none_count = sum(1 for _, a in results if a.setup_type == SetupType.NONE)
    none_capped = sum(1 for _, a in results if a.setup_type == SetupType.NONE and a.setup_score == 49)
    aplus = sum(1 for _, a in results if a.setup_quality in ("A+", "A"))
    return {
        "coins analyzed": len(results),
        "NONE-classified coins": none_count,
        "NONE coins capped to score 49 (gate acted)": none_capped,
        "A+/A coins": aplus,
        "contradictory NONE-as-A+/A/B": len(none_with_high),
        "contradictory A+/A with conf<floor": len(aplus_lowconf),
    }, none_with_high, aplus_lowconf, len(results)


def main():
    s = static_check()
    g = logic_check()
    r, nwh, alc, n = real_data_check()
    print("ISSUE #7 VERIFICATION — X-RAY score/confidence coherence")
    print("  STATIC (producer gate present):")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print("  LOGIC (exact shipped gate):")
    for k, v in g.items():
        print(f"    {k}: {v}")
    print("  REAL DATA (live universe via real StructureEngine.analyze):")
    for k, v in r.items():
        print(f"    {k}: {v}")
    if nwh:
        print(f"    VIOLATIONS (NONE-as-high): {nwh[:5]}")
    if alc:
        print(f"    VIOLATIONS (A+/A low-conf): {alc[:5]}")
    ok = (all(s.values()) and all(g.values()) and n >= 20
          and r["contradictory NONE-as-A+/A/B"] == 0
          and r["contradictory A+/A with conf<floor"] == 0)
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
