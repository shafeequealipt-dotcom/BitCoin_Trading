"""Self-verification for Issue E10 — render stale_fields to the brain.

E10 completes batch-1 #12: completeness, missing_fields and blockers already
render on the per-coin "Data quality" line; stale_fields was the one provenance
field computed-but-never-rendered. This confirms (A) the stale render is wired
into the SAME line (no second data-quality block) and (B) the render logic
emits stale=[...] for a stale package and nothing for a clean one.

Read-only; replicates the shipped render gate.
"""


def static_check():
    src = open("src/brain/strategist.py").read()
    return {
        "stale_fields read": '_stale = list(getattr(pkg, "stale_fields", []) or [])' in src,
        "stale in render gate": "or _stale:" in src,
        "stale appended to the data-quality line": 'f" stale={_stale}"' in src,
        "single data-quality render (no duplicate)": src.count('Data quality: completeness=') == 1,
    }


def _render(completeness, missing, blockers, stale):
    """Exact replica of strategist.py's #12/E10 data-quality gate."""
    _prov = None
    if completeness < 1.0 or missing or blockers or stale:
        _prov = f"  Data quality: completeness={completeness:.2f}"
        if missing:
            _prov += f" missing={missing}"
        if blockers:
            _prov += f" source_failed={blockers}"
        if stale:
            _prov += f" stale={stale}"
    return _prov


def main():
    s = static_check()
    stale_line = _render(0.80, [], [], ["built_at"])
    clean_line = _render(1.0, [], [], [])
    print("ISSUE E10 VERIFICATION — render stale_fields to the brain")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print("  BEHAVIORAL (render gate replica):")
    print(f"    stale package -> {stale_line!r}")
    print(f"    clean package -> {clean_line!r}")
    ok = (
        all(s.values())
        and stale_line is not None and "stale=['built_at']" in stale_line
        and clean_line is None
    )
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
