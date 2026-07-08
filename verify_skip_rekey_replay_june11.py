"""Element 1 replay verification (Four-Element Prompt Recalibration,
2026-06-11) — READ-ONLY.

Part D trial condition: on a replay of the June-11 cycles, the
proven-toxic IMX and MON cases must satisfy the re-keyed skip criteria
so the brain's permission to decline them would have been explicit.

The re-keyed permission has two criteria, and a candidate is covered
when EITHER applies:
  (a) the dead-thin-zero-fired cluster — zero strategies fired AND a
      dead regime AND volume ratio at or below the configured
      threshold (all visible on the block's own lines);
  (b) the heavy losing session — attempted the configured count or
      more times today with negative net (IMX and MON were net losers
      throughout the window per the trade records, so the negative-net
      leg holds; the capture itself does not carry PnL).

This script walks the captured prompts cycle by cycle, evaluates every
IMX and MON candidate block against criterion (a), counts each coin's
submissions (cycles whose response traded it) to evaluate criterion (b),
and reports coverage of both the appearances and, decisively, the
eleven actual toxic submissions. It reads thresholds from config.toml
and never writes anything.

Output is plain prose for a screen reader: no tables, no emoji.
"""

import re
import sys

CAPTURE = "/home/inshadaliqbal786/CALL_A_PROMPTS_2026-06-11_01-15_to_11-45.txt"
TARGETS = ("IMXUSDT", "MONUSDT")
SEGMENT_MARKER = "Your aim is to exploit the current market situation"


def load_thresholds() -> tuple[float, int]:
    thin, heavy = 0.25, 6
    try:
        cfg = open("config.toml", encoding="utf-8").read()
        m = re.search(
            r"^quality_skip_thin_vol_ratio\s*=\s*([0-9.]+)", cfg, re.M,
        )
        if m:
            thin = float(m.group(1))
        m = re.search(
            r"^quality_skip_heavy_attempts\s*=\s*(\d+)", cfg, re.M,
        )
        if m:
            heavy = int(m.group(1))
        print(
            f"Thresholds loaded from config.toml: thin volume ratio "
            f"{thin:.2f}, heavy attempts {heavy}."
        )
    except Exception as e:
        print(
            f"Note: using default thresholds 0.25 and 6 (config read "
            f"failed: {e})."
        )
    return thin, heavy


def main() -> int:
    thin, heavy = load_thresholds()
    try:
        text = open(CAPTURE, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        print(f"FAIL: capture file not found at {CAPTURE}.")
        return 1
    segments = text.split(SEGMENT_MARKER)[1:]
    print(f"Parsed {len(segments)} captured cycles.")

    attempts = {s: 0 for s in TARGETS}
    appearance_total = appearance_cluster = 0
    submissions = []  # (sym, attempt_no, cluster_ok, heavy_ok)
    for seg in segments:
        for sym in TARGETS:
            block = re.search(
                r"### " + sym
                + r"[^\n]*\n(?:.*\n)*?\s+Regime:\s+(\w+)\s[^\n]*"
                + r"vol_ratio=([0-9.]+)(?:.*\n)*?\s+Strategies:\s+(\d+)\s+fired",
                seg,
            )
            traded = bool(re.search(r'"symbol"\s*:\s*"' + sym + '"', seg))
            cluster_ok = None
            if block:
                regime = block.group(1)
                vol_ratio = float(block.group(2))
                fired = int(block.group(3))
                cluster_ok = (
                    fired == 0 and regime == "dead" and vol_ratio <= thin
                )
                appearance_total += 1
                if cluster_ok:
                    appearance_cluster += 1
                elif not traded:
                    print(
                        f"{sym}: non-traded appearance outside the cluster "
                        f"(regime={regime}, vol_ratio={vol_ratio:.3f}, "
                        f"fired={fired})."
                    )
            if traded:
                attempts[sym] += 1
                heavy_ok = attempts[sym] >= heavy
                submissions.append(
                    (sym, attempts[sym], bool(cluster_ok), heavy_ok)
                )
                if not cluster_ok:
                    cover = (
                        "covered by the heavy-session criterion"
                        if heavy_ok else "NOT covered by either criterion"
                    )
                    print(
                        f"{sym}: submission number {attempts[sym]} fell "
                        f"outside the cluster — {cover}."
                    )

    print(
        f"Appearance coverage: {appearance_cluster} of {appearance_total} "
        f"IMX/MON candidate blocks satisfy the dead-thin-zero-fired "
        f"cluster at threshold {thin:.2f}."
    )
    total_sub = len(submissions)
    covered = sum(1 for _, _, c, h in submissions if c or h)
    print(
        f"Submission coverage (the decisive trial): {covered} of "
        f"{total_sub} actual IMX/MON submissions are covered by the "
        f"re-keyed permission (cluster or heavy session at {heavy} or "
        f"more attempts; negative net per the trade records)."
    )
    if total_sub == 0:
        print("FAIL: no IMX/MON submissions found — capture parse problem.")
        return 1
    if covered == total_sub:
        print(
            "PASS: every actual toxic submission of the June-11 window "
            "would have carried an explicit skip permission under the "
            "re-keyed criteria."
        )
        return 0
    print(
        "FAIL: at least one toxic submission is covered by neither "
        "criterion — escalate to the operator before shipping."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
