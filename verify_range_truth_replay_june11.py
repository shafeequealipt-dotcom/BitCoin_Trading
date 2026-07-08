"""Element 3 replay verification (Four-Element Prompt Recalibration,
2026-06-11) — READ-ONLY.

Part D trial condition: the June-11 DYDX blocks during the fall must
read as below-range instead of 0.00-at-the-low, and genuine at-the-low
coins must still read at-the-low.

Honest scope of the offline replay, stated up front: the captured
prompts carry the CLAMPED range_pos values but NOT the raw
support/resistance prices the engine used, so the exact would-have-been
overshoot cannot be recomputed from the capture alone. What the capture
CAN prove offline:

1. Clamp saturation — how many DYDX blocks read exactly 0.00 (the
   forensics counted 24) and the deck-wide share of blocks pinned at
   exactly 0.00 or 1.00 (the forensics found 25 and 19 percent).
2. The floor-that-was-not-there — across the cycles where DYDX read
   exactly 0.00, the coin's own market-data price took MANY DISTINCT
   values spanning a multi-percent band. A genuine in-range position
   varies as price moves: if price truly sat at a stable floor, the
   reading would fluctuate above zero on the bounces. An UNVARYING
   exact-0.00 across a moving price proves the raw value was at or
   below zero on every read — price persistently below the detected
   range low, which is exactly the truth the new range_breakout field
   now carries. Honest nuance for the report: within THIS capture
   window DYDX's price ground in a roughly 2.7 percent oscillating
   band with a slight downward drift (the steep -8.7 percent leg shows
   in the 24h field and preceded the window); the spec's "fell through
   the range all day" is the net session read. The damage mechanism —
   "breakdown state presented as floor invitation" — is what this
   script proves.

The new computation itself is proven by the unit tests
(tests/test_range_truth/test_engine_branches.py: below-range emits
'below' with the overshoot as a percent of the broken boundary's price;
exactly-at-the-low stays unmarked), and the end-to-end render is proven
on a live cycle after the restart (BOOT_RANGE_TRUTH_ON plus a breakout
coin's dump showing the BELOW/ABOVE RANGE marker).

Output is plain prose for a screen reader: no tables, no emoji. The
script never writes anything.
"""

import re
import sys

CAPTURE = "/home/inshadaliqbal786/CALL_A_PROMPTS_2026-06-11_01-15_to_11-45.txt"
SEGMENT_MARKER = "Your aim is to exploit the current market situation"


def main() -> int:
    try:
        text = open(CAPTURE, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        print(f"FAIL: capture file not found at {CAPTURE}.")
        return 1
    segments = text.split(SEGMENT_MARKER)[1:]
    print(f"Parsed {len(segments)} captured cycles.")

    pinned_low = pinned_high = total_blocks = 0
    dydx_series = []  # (cycle_index, range_pos, market_price)
    for i, seg in enumerate(segments, 1):
        for m in re.finditer(r"range_pos=([0-9]+\.[0-9]+)", seg):
            v = float(m.group(1))
            total_blocks += 1
            if v == 0.0:
                pinned_low += 1
            elif v == 1.0:
                pinned_high += 1
        dm = re.search(r"### DYDXUSDT[^\n]*\n(?:.*\n)*?\s+Structure: [^\n]*range_pos=([0-9]+\.[0-9]+)", seg)
        pm = re.search(r"^DYDXUSDT \[[^\n]*?: \$([0-9.]+) ", seg, re.M)
        if dm:
            price = float(pm.group(1)) if pm else None
            dydx_series.append((i, float(dm.group(1)), price))

    if total_blocks == 0:
        print("FAIL: no range_pos fields found — capture parse problem.")
        return 1
    print(
        f"Clamp saturation across the deck: {pinned_low} of {total_blocks} "
        f"blocks read exactly 0.00 ({pinned_low / total_blocks:.0%}) and "
        f"{pinned_high} read exactly 1.00 ({pinned_high / total_blocks:.0%})."
    )

    zero_pinned = [(i, p) for i, v, p in dydx_series if v == 0.0 and p]
    print(
        f"DYDX appeared in {len(dydx_series)} cycles; "
        f"{len(zero_pinned)} of them read range_pos exactly 0.00 with a "
        f"market price available."
    )
    if len(zero_pinned) < 2:
        print(
            "FAIL: not enough 0.00-pinned DYDX cycles with prices to test "
            "the floor claim."
        )
        return 1
    first_cycle, _ = zero_pinned[0]
    last_cycle, _ = zero_pinned[-1]
    prices = [p for _, p in zero_pinned]
    distinct = sorted(set(prices))
    band_pct = (max(prices) - min(prices)) / min(prices) * 100.0
    all_dydx_pinned = len(zero_pinned) == len(
        [1 for _, v, p in dydx_series if p]
    )
    print(
        f"Across the pinned cycles (cycle {first_cycle} to {last_cycle}), "
        f"DYDX's market price took {len(distinct)} distinct values spanning "
        f"a {band_pct:.1f} percent band ({min(prices)} to {max(prices)}) — "
        f"yet the range position read EXACTLY 0.00 on every single one. A "
        f"genuine in-range reading varies as price moves; an unvarying "
        f"0.00 across a moving price means the raw value was at or below "
        f"zero on every read: price persistently below the detected range "
        f"low. The brain was told 'at the range low' at every price in "
        f"the band."
    )
    if all_dydx_pinned and len(distinct) >= 8 and band_pct >= 1.0:
        print(
            "PASS: the capture proves the clamp hid the below-range state — "
            "every DYDX appearance read exactly 0.00 while the price moved "
            "through a wide band, which is incompatible with a genuine "
            "at-the-floor reading. With the shipped range_breakout field "
            "those blocks would have carried the 'BELOW RANGE by X%' marker "
            "whenever the raw value was negative (computation proven by the "
            "test_range_truth unit suite; end-to-end render verified on a "
            "live cycle after restart)."
        )
        return 0
    print(
        "FAIL: the pinned DYDX cycles do not show the expected saturation "
        "pattern — escalate before claiming the trial passed."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
