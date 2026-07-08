#!/usr/bin/env python3
"""Issue 1 latency investigation — quantify what drives claude -p first-token time.

Read-only. Parses CALL_A_PHASE_TIMING lines from data/logs/brain.log* and
correlates first_token_ms against prompt_input_tokens_est and pool_hit, to
test whether the 60-163s wait tracks prompt size / inference depth (the real
cause) rather than process spawn (the warm-pool premise).

No data is modified. Output is plain text for a screen reader.
"""
from __future__ import annotations

import glob
import math
import re

FIELD_RE = re.compile(r"(\w+)=([\-\d.]+|True|False)")


def parse() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob("data/logs/brain.log*")):
        with open(path, "r", errors="ignore") as fh:
            for line in fh:
                if "CALL_A_PHASE_TIMING" not in line:
                    continue
                fields = dict(FIELD_RE.findall(line.split("CALL_A_PHASE_TIMING", 1)[1]))
                try:
                    rows.append({
                        "pool_hit": fields.get("pool_hit") == "True",
                        "first_token_ms": float(fields["first_token_ms"]),
                        "in_tok": float(fields["prompt_input_tokens_est"]),
                        "cold_spawn_ms": float(fields.get("cold_spawn_ms", 0)),
                        "pool_acquire_ms": float(fields.get("pool_acquire_ms", 0)),
                    })
                except (KeyError, ValueError):
                    continue
    return rows


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return float("nan")
    return cov / math.sqrt(vx * vy)


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = (len(s) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def band(rows: list[dict], lo: float, hi: float) -> list[dict]:
    return [r for r in rows if lo <= r["in_tok"] < hi]


def summarize(label: str, rows: list[dict]) -> None:
    if not rows:
        print(f"{label}: no samples")
        return
    ft = [r["first_token_ms"] / 1000.0 for r in rows]
    print(
        f"{label}: n={len(rows)} "
        f"first_token_s median={pct(ft, 0.5):.1f} "
        f"p10={pct(ft, 0.10):.1f} p90={pct(ft, 0.90):.1f} "
        f"min={min(ft):.1f} max={max(ft):.1f}"
    )


def main() -> None:
    rows = parse()
    print(f"Parsed {len(rows)} CALL_A_PHASE_TIMING samples.\n")

    print("== First-token time by prompt-size band (input tokens) ==")
    summarize("  small  (<3k tok, CALL_B-like)", band(rows, 0, 3000))
    summarize("  medium (3k-8k tok)", band(rows, 3000, 8000))
    summarize("  large  (>=8k tok, CALL_A)", band(rows, 8000, 1e9))
    print()

    print("== First-token time by pool_hit (controls for spawn) ==")
    summarize("  pool_hit=True ", [r for r in rows if r["pool_hit"]])
    summarize("  pool_hit=False", [r for r in rows if not r["pool_hit"]])
    print()

    print("== Within the large (>=8k) CALL_A band, split by pool_hit ==")
    large = band(rows, 8000, 1e9)
    summarize("  large pool_hit=True ", [r for r in large if r["pool_hit"]])
    summarize("  large pool_hit=False", [r for r in large if not r["pool_hit"]])
    print()

    xs = [r["in_tok"] for r in rows]
    ys = [r["first_token_ms"] for r in rows]
    print("== Correlations (Pearson r) ==")
    print(f"  first_token_ms vs prompt_input_tokens : r={pearson(xs, ys):+.3f}")
    hit = [1.0 if r["pool_hit"] else 0.0 for r in rows]
    print(f"  first_token_ms vs pool_hit (1/0)      : r={pearson(hit, ys):+.3f}")
    spawn = [r["cold_spawn_ms"] for r in rows]
    print(f"  cold_spawn_ms range                   : "
          f"min={min(spawn):.0f}ms max={max(spawn):.0f}ms "
          f"median={pct(spawn, 0.5):.0f}ms")
    print()
    print("Interpretation: a strong positive prompt-size correlation with a")
    print("near-zero pool_hit correlation, and warm hits still slow in the")
    print("large band, means the latency is inference/prompt-driven, not spawn —")
    print("so the warm pool cannot reduce it.")


if __name__ == "__main__":
    main()
