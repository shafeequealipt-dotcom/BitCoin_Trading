#!/usr/bin/env python3
"""Phase 8 of the 1D briefing rewrite — A/B harness daily summary.

Reads ``data/logs/workers.log`` for the last 24 h and aggregates
per-mode metrics for the operator's Phase 9 cutover decision:

    - packages emitted per cycle (mean, p50, p95)
    - BRAIN_INSUFFICIENT_QUALITY rate
    - trade decisions emitted per cycle
    - state_label distribution (briefing mode only)
    - mean interestingness per cycle (briefing mode only)

The ScannerWorker stamps ``BRIEFING_AB_COMPARE | ab_mode=alternating
effective_mode={exclusion|briefing}`` once per cycle when the harness
is on, so the script can attribute downstream events to the right mode.

Usage:
    python scripts/observation_1d_briefing_ab.py [--hours N]
        [--log-path PATH] [--out PATH]

Output: a Markdown report ready to paste into Telegram or a dev_notes
file. Default output is ``dev_notes/phase8_1d_briefing/ab_summary_<UTC>.md``.

The script is idempotent — running it twice over the same window
produces the same report. No DB writes; no live system touches.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG = PROJECT_DIR / "data" / "logs" / "workers.log"


# Patterns that survive across modes — same shape under exclusion and briefing.
_RE_PACKAGE_BUILD_DONE = re.compile(
    r"SCANNER_PACKAGE_BUILD_DONE \| cycle_id=(?P<cid>[\w:.-]+) "
    r"packages=(?P<pkgs>\d+) "
)
_RE_AB_COMPARE = re.compile(
    r"BRIEFING_AB_COMPARE \| ab_mode=alternating "
    r"effective_mode=(?P<mode>\w+) "
)
_RE_BRAIN_INSUFFICIENT = re.compile(r"BRAIN_INSUFFICIENT_QUALITY \|")
_RE_BRAIN_NEW_TRADE = re.compile(r"BRAIN_DO_TRADE \|")
_RE_BRIEFING_SUMMARY = re.compile(
    r"SCANNER_BRIEFING_SUMMARY \| cycle_id=(?P<cid>[\w:.-]+) "
    r"total=(?P<total>\d+) with_label=(?P<with>\d+) "
    r"advisory_only=(?P<adv>\d+) "
    r"mean_interestingness=(?P<mi>[\d.]+) "
    r"top_label=(?P<tl>\S+)"
)


def _parse_log_ts(line: str) -> datetime | None:
    """Parse the loguru-formatted timestamp at the start of a log line."""
    # Loguru default: "2026-05-01 00:42:15.761 | INFO     | ..."
    if len(line) < 23:
        return None
    try:
        return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def aggregate(
    log_path: Path,
    hours: int = 24,
) -> dict:
    """Walk the log file in reverse-time order, collect per-mode metrics."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Per-cycle state — one entry per cycle_id.
    cycle_mode: dict[str, str] = {}
    cycle_packages: dict[str, int] = {}
    cycle_briefing: dict[str, dict] = {}
    insufficient_quality: list[datetime] = []
    new_trades: list[datetime] = []

    if not log_path.exists():
        return {"error": f"log not found: {log_path}"}

    with log_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            ts = _parse_log_ts(line)
            if ts is None or ts < cutoff:
                continue
            m = _RE_AB_COMPARE.search(line)
            if m:
                # The ab_compare line precedes the cycle's package build,
                # so we associate it with the next-emitted cycle_id by
                # remembering the most-recent ab mode and applying it
                # when we see a package_build_done.
                # Simpler: track current_ab_mode; apply on next pkg event.
                cycle_briefing.setdefault(
                    "_pending_mode", {"mode": m.group("mode"), "ts": ts},
                )
                cycle_briefing["_pending_mode"] = {
                    "mode": m.group("mode"), "ts": ts,
                }
                continue
            m = _RE_PACKAGE_BUILD_DONE.search(line)
            if m:
                cid = m.group("cid")
                cycle_packages[cid] = int(m.group("pkgs"))
                pending = cycle_briefing.pop("_pending_mode", None)
                if pending and (ts - pending["ts"]).total_seconds() < 30:
                    cycle_mode[cid] = pending["mode"]
                continue
            m = _RE_BRIEFING_SUMMARY.search(line)
            if m:
                cycle_briefing[m.group("cid")] = {
                    "with_label": int(m.group("with")),
                    "advisory_only": int(m.group("adv")),
                    "mean_interestingness": float(m.group("mi")),
                    "top_label": m.group("tl"),
                }
                continue
            if _RE_BRAIN_INSUFFICIENT.search(line):
                insufficient_quality.append(ts)
                continue
            if _RE_BRAIN_NEW_TRADE.search(line):
                new_trades.append(ts)

    # Bucket by mode.
    by_mode: dict[str, dict] = {
        "exclusion": {"cycles": [], "packages": []},
        "briefing": {"cycles": [], "packages": [], "interestingness": [],
                     "labels": Counter()},
    }
    for cid, pkgs in cycle_packages.items():
        mode = cycle_mode.get(cid, "exclusion")
        by_mode[mode]["cycles"].append(cid)
        by_mode[mode]["packages"].append(pkgs)
        if mode == "briefing":
            bd = cycle_briefing.get(cid)
            if bd:
                by_mode["briefing"]["interestingness"].append(
                    bd["mean_interestingness"]
                )
                by_mode["briefing"]["labels"][bd["top_label"]] += 1

    return {
        "window_hours": hours,
        "by_mode": by_mode,
        "brain_insufficient_quality_count": len(insufficient_quality),
        "brain_new_trade_count": len(new_trades),
    }


def render_markdown(report: dict) -> str:
    """Format the aggregate as a Markdown summary."""
    if "error" in report:
        return f"# A/B harness summary\n\nERROR: {report['error']}\n"

    lines: list[str] = [
        "# Layer 1D briefing-pack — A/B harness daily summary",
        "",
        f"Window: {report['window_hours']} h.",
        "",
        "## Per-mode metrics",
        "",
        "| Mode | Cycles | packages mean | packages p50 | packages p95 |",
        "|---|---|---|---|---|",
    ]
    for mode in ("exclusion", "briefing"):
        bucket = report["by_mode"][mode]
        cycles = bucket["cycles"]
        pkgs = sorted(bucket["packages"]) if bucket["packages"] else [0]
        n = len(cycles)
        mean = sum(pkgs) / n if n else 0
        p50 = pkgs[max(0, len(pkgs) // 2 - 1)] if pkgs else 0
        p95 = pkgs[max(0, int(len(pkgs) * 0.95) - 1)] if pkgs else 0
        lines.append(f"| {mode} | {n} | {mean:.1f} | {p50} | {p95} |")
    lines.append("")
    lines.append(
        f"BRAIN_INSUFFICIENT_QUALITY events: {report['brain_insufficient_quality_count']}"
    )
    lines.append(f"Trade decisions emitted: {report['brain_new_trade_count']}")

    briefing = report["by_mode"]["briefing"]
    if briefing["cycles"]:
        if briefing["interestingness"]:
            mi_avg = sum(briefing["interestingness"]) / len(briefing["interestingness"])
            lines.append(f"Briefing mean_interestingness avg: {mi_avg:.3f}")
        if briefing["labels"]:
            lines.append("Briefing top_label distribution:")
            for label, n in briefing["labels"].most_common(8):
                lines.append(f"  - {label}: {n}")

    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--log-path", type=Path, default=DEFAULT_LOG)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    report = aggregate(args.log_path, hours=args.hours)
    md = render_markdown(report)

    if args.out is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_dir = PROJECT_DIR / "dev_notes" / "phase8_1d_briefing"
        out_dir.mkdir(parents=True, exist_ok=True)
        args.out = out_dir / f"ab_summary_{ts}.md"

    args.out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nSaved: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
