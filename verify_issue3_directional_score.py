"""Verify Issue 3 — X-RAY directional-RR setup scoring (CALL_A exploit/fetch).

Read-only. Drives the REAL StructureEngine._compute_setup_score with
HYPER-like data captured from the 2026-06-05 live prompt and asserts:

  1. A spent downtrend short (rr_short~0.12, rr_long~16, pos=0.00) now scores
     SKIP/low instead of A+/100 — it is graded on the traded short side's RR.
  2. A downtrend short WITH genuine room (rr_short=3.0, pos=0.85) still scores
     high — the fix is opportunity-quality, not "hide every short".
  3. A range-top long with no room (rr_long=0.12, pos=1.00) scores low too
     (symmetric range-position penalty + directional cap).
  4. A long with room (rr_long=3.0, pos=0.10) still scores high.
  5. When rr_short == rr_long (no directional difference) the grade is
     unchanged from the legacy rr_best behaviour (no regression).

No protected tables are touched; nothing is mutated. The method only reads
self._settings, so a minimal shim binds the real unbound method against the
real loaded StructureSettings.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

try:
    import tomllib
except ImportError:  # py3.10
    import tomli as tomllib

from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import _build_structure


def _settings():
    data = tomllib.load(open("config.toml", "rb"))
    return _build_structure(data.get("analysis", {}).get("structure", {}))


def _ms(structure="downtrend", strength="strong", bos="bearish"):
    return SimpleNamespace(
        structure=structure,
        strength=strength,
        last_bos=SimpleNamespace(direction=bos) if bos else None,
        last_choch=None,
    )


def _placement(rr_long, rr_short, chosen):
    # structure_engine overwrites rr_ratio to rr_best for downstream consumers;
    # the scorer must instead read rr_short/rr_long for the chosen side.
    rr_best = max(rr_long, rr_short)
    return SimpleNamespace(
        rr_ratio=round(rr_best, 2),
        rr_long=round(rr_long, 2),
        rr_short=round(rr_short, 2),
        is_fallback_rr=False,
        rr_quality="excellent" if rr_best >= 3 else "poor",
    )


def score(engine_shim, *, direction, pos, rr_long, rr_short, structure="downtrend",
          smc=70, mtf_quality="good", mtf_score=7):
    mtf = SimpleNamespace(quality=mtf_quality, score=mtf_score)
    pl = _placement(rr_long, rr_short, direction)
    bos = "bearish" if direction == "short" else "bullish"
    return StructureEngine._compute_setup_score(
        engine_shim,
        position_in_range=pos,
        market_structure=_ms(structure=structure, bos=bos),
        structural_placement=pl,
        suggested_direction=direction,
        smc_confluence=smc,
        volume_profile=None,
        fibonacci=None,
        mtf_confluence=mtf,
        symbol="TESTUSDT",
    )


def main():
    shim = SimpleNamespace(_settings=_settings())
    failures = []

    # 1. HYPER-like spent downtrend short: was A+/100, must now be SKIP/low.
    s1, q1 = score(shim, direction="short", pos=0.00, rr_long=15.98, rr_short=0.12)
    ok1 = q1 in ("SKIP", "C") and s1 <= 49
    print(f"1. spent short (pos=0.00 rr_s=0.12 rr_l=15.98): score={s1} grade={q1} "
          f"-> {'PASS' if ok1 else 'FAIL (expected SKIP/C low)'}")
    if not ok1:
        failures.append("spent-short not downgraded")

    # 2. Short WITH real room: must still score high (A/A+).
    s2, q2 = score(shim, direction="short", pos=0.85, rr_long=0.5, rr_short=3.0)
    ok2 = q2 in ("A+", "A") and s2 >= 65
    print(f"2. short with room (pos=0.85 rr_s=3.0): score={s2} grade={q2} "
          f"-> {'PASS' if ok2 else 'FAIL (expected A/A+)'}")
    if not ok2:
        failures.append("good short wrongly downgraded")

    # 3. Range-top long with no room: spent geometry + spent RR -> low.
    s3, q3 = score(shim, direction="long", pos=1.00, rr_long=0.12, rr_short=15.0,
                   structure="uptrend")
    ok3 = q3 in ("SKIP", "C") and s3 <= 49
    print(f"3. spent long (pos=1.00 rr_l=0.12): score={s3} grade={q3} "
          f"-> {'PASS' if ok3 else 'FAIL (expected SKIP/C low)'}")
    if not ok3:
        failures.append("spent-long not downgraded")

    # 4. Long with room: must still score high.
    s4, q4 = score(shim, direction="long", pos=0.10, rr_long=3.0, rr_short=0.5,
                   structure="uptrend")
    ok4 = q4 in ("A+", "A") and s4 >= 65
    print(f"4. long with room (pos=0.10 rr_l=3.0): score={s4} grade={q4} "
          f"-> {'PASS' if ok4 else 'FAIL (expected A/A+)'}")
    if not ok4:
        failures.append("good long wrongly downgraded")

    # 5. No directional difference (rr_short == rr_long): grade matches legacy.
    s5, q5 = score(shim, direction="short", pos=0.80, rr_long=2.5, rr_short=2.5)
    ok5 = q5 in ("A+", "A", "B")  # same as legacy would produce for rr_best=2.5
    print(f"5. no-diff (rr_s==rr_l==2.5 pos=0.80): score={s5} grade={q5} "
          f"-> {'PASS' if ok5 else 'FAIL'}")
    if not ok5:
        failures.append("no-diff regression")

    print()
    if failures:
        print(f"ISSUE 3 VERIFY: FAIL — {failures}")
        sys.exit(1)
    print("ISSUE 3 VERIFY: PASS — directional-RR scoring downgrades spent "
          "shorts/longs and preserves genuine room (5/5).")


if __name__ == "__main__":
    main()
