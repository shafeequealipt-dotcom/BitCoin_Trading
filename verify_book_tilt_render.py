"""Render-proof for Brain-Awareness Addition 2 (book-tilt) — shows the exact
ACCOUNT-section lines the brain would see for representative open-book mixes,
using the REAL label helper and the REAL config thresholds. Read-only; no DB,
no live-process contact. Run: .venv/bin/python verify_book_tilt_render.py
"""

from src.config.settings import Settings
from src.brain.strategist import _book_tilt_label


def render_tilt_block(long_count, short_count, brain_cfg):
    """Mirror the exact inline rendering in strategist._build_trade_prompt."""
    if not brain_cfg.book_tilt_enabled or (long_count + short_count) == 0:
        return None  # no line on a flat book / when disabled
    label = _book_tilt_label(
        long_count, short_count,
        int(brain_cfg.book_tilt_small_count),
        float(brain_cfg.book_tilt_one_sided_ratio),
    )
    block = f"Book tilt: {long_count} long / {short_count} short — {label}"
    if label != "balanced":
        block += (
            "\n  Consider whether a new same-direction position adds balance or "
            "concentrates an already one-sided book (awareness only — your call)."
        )
    return block, label


def main():
    cfg = Settings.load().brain
    print("Config: book_tilt_enabled=%s small_count=%s one_sided_ratio=%s"
          % (cfg.book_tilt_enabled, cfg.book_tilt_small_count, cfg.book_tilt_one_sided_ratio))
    print("=" * 70)
    # Representative books, incl. the failure mode this addition targets:
    # a 7th short piled onto an all-short book.
    mixes = [(0, 0), (3, 2), (1, 0), (0, 7), (6, 1), (5, 2), (2, 2)]
    ok = True
    for lo, sh in mixes:
        res = render_tilt_block(lo, sh, cfg)
        print(f"\nOpen book: {lo} long / {sh} short")
        if res is None:
            print("  (no Book tilt line — flat book or disabled)")
            ok = ok and (lo + sh == 0)
            continue
        block, label = res
        for line in block.splitlines():
            print("  | " + line)
        # invariants: line present, counts correct, note only when tilted
        assert f"{lo} long / {sh} short" in block
        assert ("Consider whether" in block) == (label != "balanced")

    # The targeted case must read heavily one-sided and carry the note.
    block, label = render_tilt_block(0, 7, cfg)
    targeted_ok = ("heavily short-tilted" in block) and ("Consider whether" in block)
    ok = ok and targeted_ok
    print("\n" + "=" * 70)
    print("Targeted all-short pile-on (0 long / 7 short): "
          + ("reads 'heavily short-tilted' with neutral note — PASS" if targeted_ok
             else "FAIL"))
    print("RESULT: " + ("BOOK-TILT RENDERS CORRECTLY" if ok else "RENDER MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
