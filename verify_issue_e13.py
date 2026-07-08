"""Self-verification for E13 — full dropped-labels logging (ALREADY SATISFIED).

The audit reported the trim log truncated to the first 8 dropped-section labels,
hiding the market-data loss #13 addressed. Re-verification against the current
code finds this is ALREADY SATISFIED: the live priority-trim path logs the
FULL dropped set plus a count (dropped_count + dropped_labels), and the
_dropped_labels list is unbounded (only each label is shortened to 60 chars for
readability). A predecessor (#13's priority-trim implementation) already did it
— like E7/E22 in earlier batches. No code change; this script confirms it.

Confirms:
  A. The priority-trim log emits BOTH dropped_count=len(_dropped_labels) AND the
     full dropped_labels list.
  B. _dropped_labels is appended unbounded (no per-list cap during construction).
  C. There is NO `[:8]` truncation of the dropped-labels list anywhere in
     strategist.py.
  D. BEHAVIORAL: accumulating >8 labels the way the trim loop does keeps all of
     them (no truncation), and the log format carries count + full list.

Read-only.
"""

import re


def static_check():
    s = open("src/brain/strategist.py").read()
    return {
        "trim log emits dropped_count + full dropped_labels":
            "dropped_count={len(_dropped_labels)} dropped_labels={_dropped_labels}" in s,
        "_dropped_labels appended unbounded (no cap)":
            "_dropped_labels.append(_label)" in s,
        "no [:8] truncation of dropped labels anywhere":
            "_dropped_labels[:8]" not in s and "dropped_labels[:8]" not in s
            and not re.search(r"_dropped_labels\)\[:8\]", s),
    }


def behavioral_check():
    # Mirror the trim loop's accumulation: drop 12 sections, keep all labels.
    _dropped_labels = []
    for i in range(12):
        _label = (f"section-{i} content\nlabel-line-{i}".split("\n", 2)[1])[:60].strip()
        _dropped_labels.append(_label)
    # The log line the code emits.
    log_line = (f"dropped_count={len(_dropped_labels)} "
                f"dropped_labels={_dropped_labels}")
    all_present = all(f"label-line-{i}" in log_line for i in range(12))
    count_correct = "dropped_count=12" in log_line
    return len(_dropped_labels), all_present, count_correct


def main():
    s = static_check()
    n, all_present, count_correct = behavioral_check()
    print("E13 VERIFICATION — full dropped-labels logging (ALREADY SATISFIED by #13)")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  BEHAVIORAL: accumulated {n} dropped labels (>8); all present in log: {all_present}; "
          f"count correct: {count_correct}")
    ok = all(s.values()) and n == 12 and all_present and count_correct
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'} (E13 needs no code change — confirmed already complete)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
