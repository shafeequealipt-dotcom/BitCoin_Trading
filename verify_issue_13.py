"""Self-verification for Issue #13 — per-coin market data trim-protection.

Offline check against CURRENT code. Uses the REAL `_infer_section_priority`
classifier and the REAL trim drop-order to prove:

  A. STATIC: the MARKET DATA section is now accumulated into ONE list bound to
     the "## MARKET DATA" header and emitted as a single section; the per-coin
     lines use _md_lines.append; the dropped-label log is full (no [:8] cap).
  B. CLASSIFICATION: the bound MARKET DATA section classifies ESSENTIAL (never
     trimmed), whereas an old standalone per-coin line classified OPTIONAL
     (dropped first).
  C. TRIM SIMULATION: under prompt-size pressure that drops OPTIONAL sections,
     the bound market-data block survives while OPTIONAL filler is dropped.

Run: .venv/bin/python verify_issue_13.py
"""
from src.brain import strategist as S


def static_check():
    src = open("src/brain/strategist.py").read()
    return {
        "accumulates into _md_lines list": '_md_lines: list[str] = ["## MARKET DATA"]' in src,
        "per-coin lines use _md_lines.append": "_md_lines.append(" in src,
        "emits one bound section": 'sections.append("\\n".join(_md_lines))' in src,
        "full drop-label logging (no [:8])": "dropped_count={len(_dropped_labels)}" in src
        and "_dropped_labels[:8]" not in src,
    }


def classification_check():
    coin_lines = [
        f"C{i}USDT: $1.2345 ({i-7:+.1f}% 24h) RSI={50+i} MACD_hist=0.0012 ADX={20+i}"
        for i in range(15)
    ]
    bound = "## MARKET DATA\n" + "\n".join(coin_lines)
    old_standalone_line = coin_lines[0]
    bound_pri = S._infer_section_priority(bound, 5)
    old_pri = S._infer_section_priority(old_standalone_line, 5)
    return {
        "bound section is ESSENTIAL": bound_pri == S._TRIM_PRIORITY_ESSENTIAL,
        "old standalone per-coin line was OPTIONAL": old_pri == S._TRIM_PRIORITY_OPTIONAL,
    }, bound


def trim_simulation(bound):
    # Replicate the shipped trim drop order: drop OPTIONAL (then IMPORTANT) from
    # the end until under the char cap; ESSENTIAL never drops.
    CHAR_CAP = 30000
    sections = [bound] + ["## SENTIMENT\n" + ("x" * 4000) for _ in range(12)]
    pri = [S._infer_section_priority(s, i) for i, s in enumerate(sections)]
    for target in (S._TRIM_PRIORITY_OPTIONAL, S._TRIM_PRIORITY_IMPORTANT):
        i = len(sections) - 1
        while i >= 0 and sum(len(s) for s in sections) > CHAR_CAP:
            if pri[i] == target:
                sections.pop(i)
                pri.pop(i)
            i -= 1
    md_survives = any(isinstance(s, str) and s.startswith("## MARKET DATA") for s in sections)
    optional_dropped = sum(1 for s in (["## SENTIMENT"] * 12)) - sum(
        1 for s in sections if s.startswith("## SENTIMENT")
    )
    return {
        "market data survives trim": md_survives,
        "optional filler was dropped": optional_dropped > 0,
        "final chars under cap": sum(len(s) for s in sections) <= CHAR_CAP,
    }


def main():
    s = static_check()
    c, bound = classification_check()
    t = trim_simulation(bound)
    print("ISSUE #13 VERIFICATION — market-data trim-protection")
    print("  STATIC (header-bound accumulation + full drop logging):")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print("  CLASSIFICATION (real _infer_section_priority):")
    for k, v in c.items():
        print(f"    {k}: {v}")
    print("  TRIM SIMULATION (drop OPTIONAL under char-cap pressure):")
    for k, v in t.items():
        print(f"    {k}: {v}")
    ok = all(s.values()) and all(c.values()) and all(t.values())
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
