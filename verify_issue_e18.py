"""Self-verification for Issue E18 — confidence floor on the A+ size boost.

Confirms:
  A. STATIC: the boost is gated on gate_a_plus_conf_floor and, when below the
     floor, only appends A_PLUS_BOOST_WITHHELD (never sets _gate_rejected).
  B. CONFIG: config.toml enables the floor live (0.70).
  C. REAL GATE: the real TradeGate.validate() on a weak-confidence A+ trade
     (score 85, xray_conf 0.20 — below the 0.70 floor but above E17's 0.05, so
     this isolates E18) WITHHOLDS the boost and does NOT reject; on a confident
     A+ (score 88, xray_conf 0.88) the boost is NOT withheld. Sizing-only.

Read-only.
"""
import asyncio

from src.config.settings import Settings


def static_check():
    src = open("src/apex/gate.py").read()
    return {
        "boost gated on conf floor": "_ap_floor = float(getattr(self._settings, \"gate_a_plus_conf_floor\"" in src,
        "withheld appends modification (not reject)": "A_PLUS_BOOST_WITHHELD" in src
        and "_gate_rejected" not in src.split("A_PLUS_BOOST_WITHHELD")[1].split("elif _signal_score >= 68")[0],
        "boot sentinel present": "APEX_STRUCTURELESS_GUARD_SENTINEL" in src,
    }


async def real_gate_check(apex_settings):
    from src.apex.gate import TradeGate
    gate = TradeGate(services={}, settings=apex_settings)

    async def _w(_sym):
        return 1.0
    gate._get_conviction_weight = _w   # isolate the boost from TIAS

    weak = {"symbol": "WEAKUSDT", "_setup_score": 85.0, "_xray_confidence": 0.20,
            "_expected_rr": 1.8, "size_usd": 600, "side": "Buy"}
    strong = {"symbol": "STRONGUSDT", "_setup_score": 88.0, "_xray_confidence": 0.88,
              "_expected_rr": 2.5, "size_usd": 600, "side": "Buy"}
    r_weak = await gate.validate(dict(weak))
    r_strong = await gate.validate(dict(strong))
    return r_weak, r_strong


def _adj(trade) -> str:
    # The gate joins its adjustments into the _gate_adjustments STRING on the
    # non-reject path (_gate_modifications is only set when a trade is rejected).
    return str(trade.get("_gate_adjustments") or "")


def main():
    s = static_check()
    cfg = Settings._load_fresh().apex
    floor = float(getattr(cfg, "gate_a_plus_conf_floor", 0.0))
    try:
        r_weak, r_strong = asyncio.run(real_gate_check(cfg))
        weak_withheld = "A_PLUS_BOOST_WITHHELD" in _adj(r_weak)
        weak_not_rejected = not r_weak.get("_gate_rejected")
        strong_withheld = "A_PLUS_BOOST_WITHHELD" in _adj(r_strong)
        strong_not_rejected = not r_strong.get("_gate_rejected")
        real_ok = weak_withheld and weak_not_rejected and (not strong_withheld) and strong_not_rejected
        real_note = (f"weak(score85,conf0.20): withheld={weak_withheld} rejected={not weak_not_rejected}; "
                     f"strong(score88,conf0.88): withheld={strong_withheld} rejected={not strong_not_rejected}")
    except Exception as e:
        real_ok = False
        real_note = f"real-gate call raised: {str(e)[:120]}"

    print("ISSUE E18 VERIFICATION — confidence floor on the A+ size boost")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  CONFIG: gate_a_plus_conf_floor = {floor} (live; 0.70 enables)")
    print(f"  REAL GATE: {real_note}")
    ok = all(s.values()) and floor == 0.70 and real_ok
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
