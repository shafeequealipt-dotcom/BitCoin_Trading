#!/usr/bin/env python3
"""Live monitor for Layer 1C strategy pipeline (L1 -> L2 -> L3 -> L4).

Tails workers.log, groups STRAT_* tags by strategy_id (sid), and pretty-prints
each cycle's input / output / timing per pipeline stage as cycles complete.

Usage:
    python scripts/layer1c_monitor.py                 # tail forever
    python scripts/layer1c_monitor.py --replay 5      # replay last 5 cycles, then tail
    python scripts/layer1c_monitor.py --no-color      # plain text
    python scripts/layer1c_monitor.py --votes         # include per-coin vote traces
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

LOG_PATH = Path("/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log")

# Tags this monitor cares about. STRAT_VOTE_TRACE is opt-in (per-coin verbose).
TRACK_TAGS = {
    "STRAT_PNL_GATE", "STRAT_REGIME_DIST", "STRAT_TA_DONE", "STRAT_PREFETCH",
    "STRAT_L1_DONE", "STRAT_L1", "STRAT_L1_SLOW", "STRAT_L1_SLOW_STRATEGY",
    "STRAT_L2_DONE", "STRAT_L2", "STRAT_L2_SLOW",
    "STRAT_L3_DONE", "STRAT_L3", "STRAT_VOTE_TRACE",
    "STRAT_CONSENSUS_WRITE", "STRAT_CONSENSUS_SUMMARY", "STRAT_CONSENSUS_CHANGE",
    "STRAT_L4_HANDOFF", "STRAT_L4",
    "STRAT_CYCLE_DONE", "STRAT_TICK_SLOW",
    "STRAT_UNIVERSE_EMPTY", "STRAT_SKIP_CIRCUIT", "STRAT_PREFETCH_DB_FAIL",
    "STRAT_SKIP_STALE", "STRAT_SKIP_STALE_AGG",
}

LINE_RE = re.compile(
    r"^(?P<ts>\S+ \S+)\s\|\s(?P<lvl>\w+)\s+\|\s.*?\|\s(?P<tag>STRAT_[A-Z0-9_]+)\s\|(?P<body>.*?)\|\ssid=(?P<sid>s-\d+)\s*$"
)
KV_RE = re.compile(r"(\w+)=([^\s|]+)")


class Color:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GREY = "\033[90m"


def c(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{color}{text}{Color.RESET}"


def parse_line(line: str) -> dict | None:
    m = LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    tag = m.group("tag")
    if tag not in TRACK_TAGS:
        return None
    body = m.group("body").strip()
    kv = dict(KV_RE.findall(body))
    return {
        "ts": m.group("ts"),
        "tag": tag,
        "sid": m.group("sid"),
        "kv": kv,
        "raw_body": body,
    }


class Cycle:
    """Accumulator for one strategy cycle, keyed by sid."""

    def __init__(self, sid: str):
        self.sid = sid
        self.first_ts: str | None = None
        self.events: list[dict] = []
        self.by_tag: dict[str, list[dict]] = {}
        self.done = False

    def add(self, ev: dict) -> None:
        if self.first_ts is None:
            self.first_ts = ev["ts"]
        self.events.append(ev)
        self.by_tag.setdefault(ev["tag"], []).append(ev)
        if ev["tag"] == "STRAT_CYCLE_DONE":
            self.done = True

    def get(self, tag: str) -> dict | None:
        evs = self.by_tag.get(tag)
        return evs[-1] if evs else None

    def all(self, tag: str) -> list[dict]:
        return self.by_tag.get(tag, [])


def fmt_cycle(cy: Cycle, color: bool, show_votes: bool) -> str:
    out: list[str] = []
    sep = c("─" * 78, Color.GREY, color)
    title = c(f" CYCLE {cy.sid}  start={cy.first_ts} ", Color.BOLD + Color.CYAN, color)
    out.append(sep)
    out.append(title)

    # ── Gate
    gate = cy.get("STRAT_PNL_GATE")
    if gate:
        kv = gate["kv"]
        halted = kv.get("halted", "?")
        flag = c("HALTED", Color.RED, color) if halted == "Y" else c("OK", Color.GREEN, color)
        out.append(
            f"  {c('[GATE]', Color.MAGENTA, color):<24} {flag}  "
            f"rsn={kv.get('rsn')}  pnl={kv.get('pnl_pct')}%  "
            f"wins={kv.get('wins')} losses={kv.get('losses')}  el={kv.get('el')}"
        )

    # ── Regime
    regime = cy.get("STRAT_REGIME_DIST")
    if regime:
        kv = regime["kv"]
        out.append(
            f"  {c('[REGIME]', Color.MAGENTA, color):<24} global={c(kv.get('global','?'), Color.YELLOW, color)}  "
            f"up={kv.get('up')} down={kv.get('down')} ranging={kv.get('ranging')} "
            f"volatile={kv.get('volatile')} dead={kv.get('dead')}  total={kv.get('total')}"
        )

    # ── Prefetch
    pf = cy.get("STRAT_PREFETCH")
    if pf:
        kv = pf["kv"]
        out.append(
            f"  {c('[PREFETCH]', Color.MAGENTA, color):<24} coins={kv.get('coins')}  "
            f"el={c(kv.get('el','?'), Color.YELLOW, color)}  "
            f"db={kv.get('db')} ta={kv.get('ta')} h1_db={kv.get('h1_db')} h1_ta={kv.get('h1_ta')}  "
            f"h1_cache={kv.get('h1_valid')}/{kv.get('h1_lookups')}"
        )
    ta = cy.get("STRAT_TA_DONE")
    if ta:
        kv = ta["kv"]
        out.append(
            f"  {c('[TA]', Color.MAGENTA, color):<24} fast={kv.get('fast')} slow={kv.get('slow')} "
            f"max_ms={kv.get('max_ms')} total_ms={kv.get('total_ms')}"
        )

    # ── L1 Scan
    l1 = cy.get("STRAT_L1_DONE") or cy.get("STRAT_L1")
    if l1:
        kv = l1["kv"]
        out.append(c("  ┌─ L1 SCAN  (input: coins+strategies → output: raw_signals)", Color.BOLD + Color.BLUE, color))
        out.append(
            f"  │  IN : coins={kv.get('coins')}  strategies={kv.get('strategies')}"
        )
        out.append(
            f"  │  OUT: signals={c(kv.get('signals','0'), Color.GREEN, color)}  "
            f"avg/strategy={kv.get('per_strategy_avg','?')}"
        )
        if "top_firing" in l1["raw_body"]:
            tf = re.search(r"top_firing=\[([^\]]*)\]", l1["raw_body"])
            nf = re.search(r"non_firing=\[([^\]]*)\]", l1["raw_body"])
            if tf:
                out.append(f"  │  top_firing : {tf.group(1)}")
            if nf:
                out.append(f"  │  non_firing : {nf.group(1)}")
        slow = cy.all("STRAT_L1_SLOW_STRATEGY")
        for s in slow[:5]:
            skv = s["kv"]
            out.append(c(f"  │  SLOW strat={skv.get('strategy')} sym={skv.get('sym')} el={skv.get('el')}", Color.YELLOW, color))
        out.append(f"  └─ time: {c(kv.get('el','?'), Color.YELLOW, color)}")

    # ── L2 Score
    l2 = cy.get("STRAT_L2_DONE") or cy.get("STRAT_L2")
    l2_short = cy.get("STRAT_L2")
    if l2:
        kv = l2["kv"]
        skv = l2_short["kv"] if l2_short else {}
        out.append(c("  ┌─ L2 SCORE (input: raw_signals → output: scored setups)", Color.BOLD + Color.BLUE, color))
        l1_sig = (l1["kv"].get("signals") if l1 else "?")
        out.append(f"  │  IN : signals={l1_sig}")
        out.append(
            f"  │  OUT: scored={c(kv.get('scored','0'), Color.GREEN, color)}  "
            f"best={skv.get('best', kv.get('best','?'))} grade={skv.get('grade', kv.get('grade','?'))}"
        )
        if "score_p25" in kv:
            out.append(
                f"  │  score percentiles  p25={kv.get('score_p25')} p50={kv.get('score_p50')} "
                f"p75={kv.get('score_p75')} p95={kv.get('score_p95')}"
            )
        comp = re.search(r"score_components_avg=\[([^\]]*)\]", l2["raw_body"])
        if comp:
            out.append(f"  │  components avg    {comp.group(1)}")
        out.append(f"  └─ time: {c(kv.get('el','?'), Color.YELLOW, color)}")

    # ── L3 Ensemble
    l3 = cy.get("STRAT_L3_DONE") or cy.get("STRAT_L3")
    l3_short = cy.get("STRAT_L3")
    if l3:
        kv = l3["kv"]
        skv = l3_short["kv"] if l3_short else {}
        out.append(c("  ┌─ L3 ENSEMBLE (input: scored → output: consensus setups)", Color.BOLD + Color.BLUE, color))
        l2_scored = (l2["kv"].get("scored") if l2 else "?")
        out.append(f"  │  IN : scored={l2_scored}")
        out.append(
            f"  │  OUT: consensus={c(kv.get('consensus','0'), Color.GREEN, color)}  "
            f"top={skv.get('top', kv.get('top','?'))} str={skv.get('str', kv.get('str','?'))}"
        )
        dist = re.search(r"consensus_dist=\[([^\]]*)\]", l3["raw_body"])
        if dist:
            out.append(f"  │  dist : {dist.group(1)}  size_mult_avg={kv.get('size_mult_avg','?')}")
        if show_votes:
            for v in cy.all("STRAT_VOTE_TRACE")[:10]:
                vkv = v["kv"]
                votes_str = re.search(r"votes=\[(.*)\]", v["raw_body"])
                summary = ""
                if votes_str:
                    parts = [p.strip() for p in votes_str.group(1).split(";")]
                    counts = {"BUY": 0, "SELL": 0, "NEUTRAL": 0}
                    for p in parts:
                        m2 = re.search(r"vote=(\w+)", p)
                        if m2 and m2.group(1) in counts:
                            counts[m2.group(1)] += 1
                    summary = f" BUY={counts['BUY']} SELL={counts['SELL']} NEUTRAL={counts['NEUTRAL']}"
                out.append(
                    f"  │  vote: sym={vkv.get('sym')} cons={vkv.get('consensus')} "
                    f"agree={vkv.get('agreeing')} oppose={vkv.get('opposing')}{summary}"
                )
        out.append(f"  └─ time: {c(kv.get('el','?'), Color.YELLOW, color)}")

    # ── Consensus cache write
    cw = cy.get("STRAT_CONSENSUS_WRITE")
    if cw:
        kv = cw["kv"]
        out.append(
            f"  {c('[CONSENSUS_CACHE]', Color.MAGENTA, color):<24} "
            f"full={kv.get('full_count')} filtered={kv.get('filtered_count')} "
            f"setups_in={kv.get('setups_in')} cache_after={kv.get('cache_size_after')} "
            f"votes_cache={kv.get('votes_cache_size')} mode={kv.get('mode')} thr={kv.get('threshold')}"
        )
    cs = cy.get("STRAT_CONSENSUS_SUMMARY")
    if cs:
        body = cs["raw_body"]
        out.append(f"  {c('[CONSENSUS_DIST]', Color.MAGENTA, color):<24} {body}")
    changes = cy.all("STRAT_CONSENSUS_CHANGE")
    if changes:
        out.append(f"  {c('[CONSENSUS_CHG]', Color.MAGENTA, color):<24} {len(changes)} transitions:")
        for ch in changes[:10]:
            kv = ch["kv"]
            out.append(
                f"      {kv.get('sym'):<12} {kv.get('from'):>6} -> "
                f"{c(kv.get('to','?'), Color.YELLOW, color):<6}  "
                f"votes={kv.get('votes')} score={kv.get('score')}"
            )
        if len(changes) > 10:
            out.append(f"      ... ({len(changes)-10} more)")

    # ── L4 Hand-off
    l4 = cy.get("STRAT_L4_HANDOFF") or cy.get("STRAT_L4")
    if l4:
        kv = l4["kv"]
        out.append(c("  ┌─ L4 HAND-OFF (output: hints written for Claude / scanner)", Color.BOLD + Color.BLUE, color))
        out.append(
            f"  │  score_cache_size={kv.get('score_cache_size','?')}  "
            f"consensus_size={kv.get('consensus_size','?')}  "
            f"summary_size={kv.get('consensus_summary_size','?')}"
        )
        l4_short = cy.get("STRAT_L4")
        hints = (l4_short["kv"].get("hints") if l4_short else kv.get("hints_top20_size", "?"))
        filt = (l4_short["kv"].get("filtered_from") if l4_short else "?")
        out.append(
            f"  │  hints_written={c(str(hints), Color.GREEN, color)}  filtered_from={filt}"
        )
        out.append(f"  └─ time: {c(kv.get('el','?'), Color.YELLOW, color)}")

    # ── Cycle summary
    cd = cy.get("STRAT_CYCLE_DONE")
    if cd:
        kv = cd["kv"]
        body = cd["raw_body"]
        # Extract per-section ms from the second half
        nice = (
            f"coins={kv.get('coins')} signals={kv.get('signals')} "
            f"scored={kv.get('scored')} hints={kv.get('hints')} urg={kv.get('urg')}"
        )
        out.append(c(f"  ━━ CYCLE_DONE  el={kv.get('el','?')}", Color.BOLD + Color.GREEN, color))
        out.append(f"     {nice}")
        # Pull the "| gate=... L1=... L2=..." trailing summary
        m_summary = re.search(r"\|\s(gate=.*drift_ms=\S+)", body)
        if m_summary:
            out.append(f"     {m_summary.group(1)}")
    slow = cy.get("STRAT_TICK_SLOW")
    if slow:
        out.append(c(f"  !! TICK_SLOW : {slow['kv'].get('el')} (>30s)", Color.RED, color))
    out.append("")
    return "\n".join(out)


def follow(path: Path, from_start: bool):
    """Generator yielding new lines as the file grows. Handles rotation."""
    while not path.exists():
        time.sleep(0.5)
    f = path.open("r", encoding="utf-8", errors="replace")
    if not from_start:
        f.seek(0, os.SEEK_END)
    inode = os.fstat(f.fileno()).st_ino
    while True:
        line = f.readline()
        if line:
            yield line
            continue
        time.sleep(0.4)
        try:
            st = os.stat(path)
            if st.st_ino != inode:
                f.close()
                f = path.open("r", encoding="utf-8", errors="replace")
                inode = os.fstat(f.fileno()).st_ino
        except FileNotFoundError:
            time.sleep(0.5)


def replay_last_n(path: Path, n: int) -> list[str]:
    """Read the file backwards enough to return lines covering the last N completed cycles."""
    if n <= 0 or not path.exists():
        return []
    # Cheap: read entire tail (last ~4 MB) and split.
    size = path.stat().st_size
    chunk = min(size, 4 * 1024 * 1024)
    with path.open("rb") as f:
        f.seek(size - chunk)
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    # find last N STRAT_CYCLE_DONE indexes
    done_idx = [i for i, ln in enumerate(lines) if "STRAT_CYCLE_DONE" in ln]
    if not done_idx:
        return []
    cutoff_done = done_idx[-n] if len(done_idx) >= n else done_idx[0]
    # Need to also include lines preceding the first cycle's start (PNL_GATE).
    start = 0
    for i in range(cutoff_done, -1, -1):
        if "STRAT_PNL_GATE" in lines[i]:
            start = i
            break
    return lines[start:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(LOG_PATH), help="path to workers.log")
    ap.add_argument("--replay", type=int, default=1, help="show last N cycles before tailing")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--votes", action="store_true", help="include per-coin vote traces")
    ap.add_argument("--once", action="store_true", help="print replay and exit (no tail)")
    args = ap.parse_args()

    color = sys.stdout.isatty() and not args.no_color
    path = Path(args.log)

    cycles: "OrderedDict[str, Cycle]" = OrderedDict()
    printed: set[str] = set()
    MAX_CYCLES = 20  # cap memory

    def handle_line(line: str):
        ev = parse_line(line)
        if not ev:
            return
        sid = ev["sid"]
        cy = cycles.get(sid)
        if cy is None:
            cy = Cycle(sid)
            cycles[sid] = cy
            while len(cycles) > MAX_CYCLES:
                cycles.popitem(last=False)
        cy.add(ev)
        if cy.done and sid not in printed:
            printed.add(sid)
            print(fmt_cycle(cy, color, args.votes), flush=True)

    # Replay
    if args.replay > 0:
        for ln in replay_last_n(path, args.replay):
            handle_line(ln)

    if args.once:
        return 0

    print(c(f"# tailing {path}  (Ctrl-C to exit)", Color.GREY, color), flush=True)
    try:
        for ln in follow(path, from_start=False):
            handle_line(ln)
    except KeyboardInterrupt:
        print(c("\n# stopped", Color.GREY, color))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
