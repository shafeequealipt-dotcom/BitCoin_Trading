"""Self-verification for Issue E17 — precise structureless-high-score reject.

Confirms:
  A. STATIC: the new reject is wired AFTER the existing all-low AND reject
     (which is left intact), keyed on confidence-floor AND high-score.
  B. CONFIG: config.toml enables the live values (conf_floor 0.05, score_min 65).
  C. REAL GATE: the real TradeGate.validate() rejects a structureless coin
     (xray_conf 0.0, setup 100) with reason structureless_high_score, while a
     legitimate aggressive entry (confidence 0.55, score 70, rr 1.8) is NOT
     rejected (the over-reject safeguard), and a #7-capped coin (score 49) is
     NOT rejected by E17 (no double-jeopardy).

Read-only.
"""
import asyncio

from src.config.settings import Settings


def static_check():
    src = open("src/apex/gate.py").read()
    return {
        "E17 reject present": "reason=structureless_high_score" in src,
        "keyed on conf-floor AND high-score": "_xray <= _se_conf_floor and _setup >= _se_score_min" in src,
        "existing all-low AND reject intact": "REJECTED:zero_conviction" in src,
        "boot sentinel reports e17 thresholds": "e17_score_min=" in src,
    }


async def real_gate_check(apex_settings):
    from src.apex.gate import TradeGate
    gate = TradeGate(services={}, settings=apex_settings)

    async def _w(_sym):
        return 1.0
    gate._get_conviction_weight = _w

    structureless = {"symbol": "STRUCTLESS", "_setup_score": 100.0, "_xray_confidence": 0.0,
                     "_expected_rr": 1.5, "size_usd": 600, "side": "Buy"}
    aggressive = {"symbol": "AGGRO", "_setup_score": 70.0, "_xray_confidence": 0.55,
                  "_expected_rr": 1.8, "size_usd": 600, "side": "Buy"}
    sharp7_capped = {"symbol": "CAPPED", "_setup_score": 49.0, "_xray_confidence": 0.0,
                     "_expected_rr": 1.5, "size_usd": 600, "side": "Buy"}
    r_s = await gate.validate(dict(structureless))
    r_a = await gate.validate(dict(aggressive))
    r_c = await gate.validate(dict(sharp7_capped))
    return r_s, r_a, r_c


def main():
    s = static_check()
    cfg = Settings._load_fresh().apex
    cf = float(getattr(cfg, "gate_structureless_conf_floor", 0.0))
    sm = float(getattr(cfg, "gate_structureless_score_min", 999.0))
    try:
        r_s, r_a, r_c = asyncio.run(real_gate_check(cfg))
        structureless_rejected = "structureless_high_score" in str(r_s.get("_gate_rejected") or "")
        aggressive_kept = not r_a.get("_gate_rejected")
        capped_kept = "structureless_high_score" not in str(r_c.get("_gate_rejected") or "")
        real_ok = structureless_rejected and aggressive_kept and capped_kept
        real_note = (f"structureless(100/0.0): rejected={structureless_rejected}; "
                     f"aggressive(70/0.55): kept={aggressive_kept}; "
                     f"#7-capped(49/0.0): not-E17-rejected={capped_kept}")
    except Exception as e:
        real_ok = False
        real_note = f"real-gate call raised: {str(e)[:120]}"

    print("ISSUE E17 VERIFICATION — precise structureless-high-score reject")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  CONFIG: conf_floor={cf} score_min={sm} (live 0.05 / 65)")
    print(f"  REAL GATE: {real_note}")
    ok = all(s.values()) and cf == 0.05 and sm == 65.0 and real_ok
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
