"""Self-verification for E22 — protect the held-symbols block from trim.

The Call-B context prompt appends the held-symbols hard constraint as a plain
"You ALREADY HOLD: ..." section with no "##" header, so it defaulted to
OPTIONAL and could be trimmed under prompt-size pressure (risking a duplicate
entry on a held coin). E22 binds it to ESSENTIAL via #13's priority-marker
mechanism.

Confirms:
  A. STATIC: "ALREADY HOLD" is in _TRIM_ESSENTIAL_MARKERS.
  B. BEHAVIORAL: the real _infer_section_priority classifies the actual held
     block (exact text from strategist.py:1845-1849) as ESSENTIAL, while an
     unmarked section stays OPTIONAL (the pre-fix default the held block had).
     Since the trim drops OPTIONAL/IMPORTANT before ESSENTIAL, ESSENTIAL
     classification guarantees the held constraint always survives.

Read-only / in-memory.
"""


def main():
    from src.brain.strategist import (
        _infer_section_priority,
        _TRIM_ESSENTIAL_MARKERS,
        _TRIM_PRIORITY_ESSENTIAL,
        _TRIM_PRIORITY_OPTIONAL,
    )

    static_ok = "ALREADY HOLD" in _TRIM_ESSENTIAL_MARKERS

    # The exact held-symbols section the Call-B prompt appends (strategist.py:1845).
    held = ("\nYou ALREADY HOLD: BTCUSDT, ETHUSDT\n"
            "DO NOT suggest new trades for these symbols. "
            "The system will REJECT them.")
    held_pri = _infer_section_priority(held, index=7)   # non-zero index (not the coaching block)
    held_essential = held_pri == _TRIM_PRIORITY_ESSENTIAL

    # Control: an unmarked section (what the held block WAS before E22) -> OPTIONAL.
    plain = "\nSome ancillary note with no protected header marker in it."
    plain_pri = _infer_section_priority(plain, index=8)
    control_optional = plain_pri == _TRIM_PRIORITY_OPTIONAL

    # Ordering invariant the trim relies on: ESSENTIAL is kept over OPTIONAL.
    ordering_ok = _TRIM_PRIORITY_ESSENTIAL < _TRIM_PRIORITY_OPTIONAL

    print("E22 VERIFICATION — protect held-symbols block from trim (completes #13)")
    print(f"  STATIC: 'ALREADY HOLD' in _TRIM_ESSENTIAL_MARKERS: {static_ok}")
    print(f"  BEHAVIORAL: held block classifies ESSENTIAL (priority {held_pri}): {held_essential}")
    print(f"  CONTROL: an unmarked section stays OPTIONAL (priority {plain_pri}): {control_optional}")
    print(f"  INVARIANT: ESSENTIAL kept over OPTIONAL ({_TRIM_PRIORITY_ESSENTIAL} < {_TRIM_PRIORITY_OPTIONAL}): {ordering_ok}")

    ok = static_ok and held_essential and control_optional and ordering_ok
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
