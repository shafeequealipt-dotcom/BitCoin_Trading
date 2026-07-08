"""Self-verification for E23 — collapse the strategy-hints block.

The strategy-hints + per-coin-consensus block emitted ~39 separate
sections.append() calls (3 headers + up to 20 hints + 1 header + up to 15
consensus rows), each its own trim-unit — the dominant prompt-size pressure
that triggered the trimmer. E23 collapses it to two joined sections, preserving
every field, in BOTH the live Call-A block and the dead legacy copy.

Confirms:
  A. STATIC: both blocks use the joined form ("\\n".join(_hint_lines) and
     ("\\n".join(_consensus_rows)); the old per-iteration
     `for h in hints[:20]: sections.append(...)` loop is gone; the "## STRATEGY
     HINTS" header is folded into the joined hints string (so it classifies
     IMPORTANT as a unit).
  B. BEHAVIORAL: applying the exact new join logic to 20 sample hints + 15
     sample consensus rows yields exactly 2 sections (down from ~39) AND every
     field value (strategy/symbol/direction/score/consensus;
     symbol/buy/sell/total_score) is preserved verbatim in the joined strings.

Read-only / in-memory.
"""


def static_check():
    s = open("src/brain/strategist.py").read()
    return {
        "joined hint form present in both blocks": s.count('"\\n".join(_hint_lines)') == 2,
        "joined consensus form present in both blocks": s.count('"\\n".join(_consensus_rows)') == 2,
        "old per-iteration hint append loop removed":
            "for h in hints[:20]:\n                sections.append(" not in s,
        "## STRATEGY HINTS header folded into the joined block":
            '_hint_header + ("\\n" + "\\n".join(_hint_lines)' in s,
    }


def behavioral_check():
    # The exact join logic the live block now uses (mirror of strategist.py).
    hints = [
        {"strategy": f"S{i}", "symbol": f"C{i}USDT", "direction": "long" if i % 2 else "short",
         "score": i, "consensus": f"{i} buy / 0 sell"}
        for i in range(20)
    ]
    summary = {
        f"C{i}USDT": {"buy": i, "sell": 20 - i, "total_score": float(i * 10)}
        for i in range(15)
    }
    _hint_header = (
        "\n## STRATEGY HINTS (automated signals — use as reference ONLY)\n"
        "These are outputs from 40 automated strategies.\n"
        "They are HINTS — often wrong. Make your OWN analysis."
    )
    _hint_lines = [
        f"  {h.get('strategy', '?')}: {h.get('symbol', '?')} "
        f"{h.get('direction', '?')} score={h.get('score', 0)} "
        f"{h.get('consensus', '?')}"
        for h in hints[:20]
    ]
    sections = []
    sections.append(_hint_header + ("\n" + "\n".join(_hint_lines) if _hint_lines else ""))
    _consensus_rows = [
        f"    {sym}: {data['buy']} buy / {data['sell']} sell "
        f"(total score: {data['total_score']:.0f})"
        for sym, data in sorted(summary.items(), key=lambda x: x[1]["total_score"], reverse=True)[:15]
    ]
    sections.append("\n  CONSENSUS PER COIN:\n" + "\n".join(_consensus_rows))

    two_sections = len(sections) == 2                      # was ~39
    blob = "\n".join(sections)
    # Every hint field preserved.
    hints_ok = all(
        h["strategy"] in blob and h["symbol"] in blob and h["direction"] in blob
        and f"score={h['score']}" in blob and h["consensus"] in blob
        for h in hints
    )
    # Every consensus field preserved.
    consensus_ok = all(
        f"{sym}: {d['buy']} buy / {d['sell']} sell" in blob
        and f"(total score: {d['total_score']:.0f})" in blob
        for sym, d in summary.items()
    )
    # Header classifies IMPORTANT (## STRATEGY HINTS in the first 200 chars).
    header_ok = "## STRATEGY HINTS" in sections[0][:200]
    return two_sections, hints_ok, consensus_ok, header_ok, len(sections)


def main():
    s = static_check()
    two, hints_ok, consensus_ok, header_ok, n = behavioral_check()
    print("E23 VERIFICATION — collapse strategy-hints block (completes #13 at the source)")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  BEHAVIORAL (20 hints + 15 consensus rows):")
    print(f"    sections emitted = {n} (was ~39): {two}")
    print(f"    all hint fields preserved: {hints_ok}")
    print(f"    all consensus fields preserved: {consensus_ok}")
    print(f"    header classifies IMPORTANT (in first 200 chars): {header_ok}")
    ok = all(s.values()) and two and hints_ok and consensus_ok and header_ok
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
