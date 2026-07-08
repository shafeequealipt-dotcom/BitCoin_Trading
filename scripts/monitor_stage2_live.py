#!/usr/bin/env python3
"""Stage 2 live monitor — shows each cycle's input → Claude and the response.

Usage:
    # 1. (one-time) restart workers so the patched claude_code_client is loaded:
    #      sudo systemctl restart trading-workers
    # 2. enable dumping (sentinel — toggle anytime, no restart):
    #      touch data/stage2_dumps/.enabled
    # 3. run this monitor:
    #      python scripts/monitor_stage2_live.py
    #    or compact mode:
    #      python scripts/monitor_stage2_live.py --compact
    #    or skip showing prompt body (responses + metadata only):
    #      python scripts/monitor_stage2_live.py --no-prompt
    # 4. when done:
    #      rm data/stage2_dumps/.enabled

What you see per cycle:
    - cycle start (did) and timing breakdown (prompt build, Claude call, total)
    - top-N selection (input_count → output_count after Stage2 cap)
    - prompt size, sections, trim mode, and full prompt body (from dump)
    - full Claude response body (from dump)
    - parsed result: trade count, risk, view summary
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

PROJECT = Path(__file__).resolve().parents[1]
LOG_PATH = PROJECT / "data" / "logs" / "brain.log"
DUMP_DIR = PROJECT / "data" / "stage2_dumps"
SENTINEL = DUMP_DIR / ".enabled"

# ─── ANSI colors (auto-disabled if not a TTY) ───────────────────────────────
USE_COLOR = sys.stdout.isatty()


def c(text: str, code: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if USE_COLOR else text


BOLD = "1"
DIM = "2"
RED = "31"
GREEN = "32"
YELLOW = "33"
BLUE = "34"
MAGENTA = "35"
CYAN = "36"
GREY = "90"

# ─── Event extraction ───────────────────────────────────────────────────────
EVENT_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+\|\s+"
    r"(?P<level>\w+)\s+\|\s+\S+\s+\|\s+"
    r"(?P<event>[A-Z][A-Z0-9_]+)\s*\|\s*(?P<rest>.*)$"
)
DID_RE = re.compile(r"did=(d-\d+)")
KV_RE = re.compile(r"(\w+)=([^\s|]+(?:\s+[^=|]+(?=\s+\w+=))?)")


def parse_kv(rest: str) -> dict:
    """Extract simple key=value pairs (best-effort)."""
    out = {}
    for k, v in re.findall(r"(\w+)=([^\s|]+)", rest):
        out[k] = v
    return out


# Events we care about, in cycle order
INTERESTING = {
    "STRAT_CALL_A_START",
    "STRATEGIST_PACKAGES_READ",
    "STRAT_TOP_N_APPLIED",
    "STRAT_PROMPT_BUILD",
    "STRAT_PROMPT_SIZE",
    "CLAUDE_PROMPT_TRIMMED",
    "STRAT_CALL_A_CTX",
    "PROMPT_BUILD_DONE",
    "STRAT_CALL_A",
    "CLAUDE_CALL_START",
    "CLAUDE_PROC_SPAWNED",
    "CLAUDE_PROC_STALL_60S",
    "CLAUDE_CALL_OK",
    "CLAUDE_CALL_FAIL",
    "CLAUDE_CALL_TIMEOUT",
    "STRAT_CALL_A_PLAN",
    "STRAT_CALL_A_NO_TRADES",
    "STRAT_ZERO_TRADES_INTENTIONAL",
    "STRAT_CALL_A_END",
}


def hr(label: str = "", color: str = CYAN) -> str:
    width = 100
    if not label:
        return c("─" * width, color)
    pad = max(2, width - len(label) - 4)
    return c(f"── {label} " + "─" * pad, color)


def follow(path: Path):
    """tail -F replacement; survives log rotation."""
    while not path.exists():
        time.sleep(0.5)
    f = path.open("r", errors="replace")
    f.seek(0, os.SEEK_END)
    inode = path.stat().st_ino
    while True:
        line = f.readline()
        if line:
            yield line.rstrip("\n")
            continue
        time.sleep(0.25)
        try:
            if path.stat().st_ino != inode:
                f.close()
                f = path.open("r", errors="replace")
                inode = path.stat().st_ino
        except FileNotFoundError:
            time.sleep(0.5)


def find_dump(call_id: int, did: str, deadline_s: float = 5.0) -> Optional[Path]:
    """Wait briefly for the dump file matching this call_id to land on disk."""
    pattern_did = f"_{did}.json"
    pattern_call = f"_call{call_id:04d}_"
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        for p in DUMP_DIR.glob(f"*{pattern_call}*.json"):
            if p.name.endswith(pattern_did):
                return p
        time.sleep(0.2)
    # fallback: any file with this call_id
    matches = sorted(DUMP_DIR.glob(f"*{pattern_call}*.json"))
    return matches[-1] if matches else None


def show_dump(path: Path, *, show_prompt: bool, show_system: bool, response_only: bool):
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        print(c(f"  [dump load failed: {e}]", RED))
        return
    if show_system and data.get("system_prompt"):
        print(hr(f"SYSTEM PROMPT  ({data['system_prompt_chars']} chars)", MAGENTA))
        print(data["system_prompt"])
    if show_prompt and not response_only:
        print(hr(f"USER PROMPT  ({data['prompt_chars']} chars)", BLUE))
        print(data["prompt"])
    print(hr(f"CLAUDE RESPONSE  ({data['response_chars']} chars, {data['elapsed_ms']:.0f}ms)", GREEN))
    print(data["response"])
    print(c(f"  [dump file: {path.relative_to(PROJECT)}]", GREY))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compact", action="store_true", help="metadata only — never print prompt/response bodies")
    ap.add_argument("--no-prompt", action="store_true", help="show response body but skip prompt body")
    ap.add_argument("--show-system", action="store_true", help="also print the system prompt body")
    ap.add_argument("--response-only", action="store_true", help="print response only, no prompt")
    ap.add_argument("--from-start", action="store_true", help="start from beginning of brain.log instead of tailing live")
    args = ap.parse_args()

    show_bodies = not args.compact

    # Header
    print(hr("STAGE 2 LIVE MONITOR", CYAN))
    print(f"  log:      {LOG_PATH}")
    print(f"  dumps:    {DUMP_DIR}")
    sentinel_state = c("ENABLED ✓", GREEN) if SENTINEL.exists() else c("DISABLED — touch data/stage2_dumps/.enabled to capture full prompt/response bodies", YELLOW)
    print(f"  sentinel: {sentinel_state}")
    print(hr("", CYAN))
    sys.stdout.flush()

    # Per-cycle state, keyed by did
    cycles: dict[str, dict] = defaultdict(dict)
    # Recent timing window for rolling stats
    recent_call_ms = deque(maxlen=20)

    src = follow(LOG_PATH)
    if args.from_start:
        # rewind: open from beginning
        src = (line.rstrip("\n") for line in LOG_PATH.open("r", errors="replace"))

    for line in src:
        m = EVENT_RE.match(line)
        if not m:
            continue
        event = m.group("event")
        if event not in INTERESTING:
            continue
        rest = m.group("rest")
        ts = m.group("ts")
        did_m = DID_RE.search(rest)
        did = did_m.group(1) if did_m else "no_ctx"
        kv = parse_kv(rest)
        st = cycles[did]

        if event == "STRAT_CALL_A_START":
            st.clear()
            st["did"] = did
            st["t_start"] = ts
            print()
            print(hr(f"CYCLE START  did={did}  @ {ts}", CYAN))
            sys.stdout.flush()

        elif event == "STRATEGIST_PACKAGES_READ":
            st["pkg_count"] = kv.get("count")
            st["pkg_age_max"] = kv.get("age_max_s")
            print(f"  packages read:   count={kv.get('count')}  age_max={kv.get('age_max_s')}s")

        elif event == "STRAT_TOP_N_APPLIED":
            st["top_input"] = kv.get("input_count")
            st["top_cap"] = kv.get("cap")
            st["top_pinned"] = kv.get("pinned_positions")
            st["top_output"] = kv.get("output_count")
            print(f"  top-N applied:   input={kv.get('input_count')} → output={kv.get('output_count')} (cap={kv.get('cap')}, pinned_positions={kv.get('pinned_positions')})")

        elif event == "STRAT_PROMPT_BUILD":
            print(c(f"  prompt build:    {rest.split('|')[0].strip()}", DIM))

        elif event == "STRAT_PROMPT_SIZE":
            st["sections"] = kv.get("sections")
            st["chars"] = kv.get("chars")
            print(f"  prompt size:     sections={kv.get('sections')}  chars={kv.get('chars')}")

        elif event == "CLAUDE_PROMPT_TRIMMED":
            print(c(f"  trimmed:         mode={kv.get('mode')} reason={kv.get('reason')} sections {kv.get('sections_before')}→{kv.get('sections_after')} chars {kv.get('chars_before')}→{kv.get('chars_after')}", YELLOW))

        elif event == "PROMPT_BUILD_DONE":
            print(c(f"  prompt done:     coins={kv.get('coins')} packages={kv.get('packages')} elapsed={kv.get('elapsed_ms')}ms", DIM))

        elif event == "CLAUDE_CALL_START":
            st["call_id"] = int(kv.get("call_id", 0))
            st["in_chars"] = kv.get("in")
            st["sys_chars"] = kv.get("sys")
            st["hash"] = kv.get("hash")
            st["t_call_start"] = time.time()
            print(c(f"  → CLAUDE_CALL:   call_id={kv.get('call_id')} in={kv.get('in')} sys={kv.get('sys')} hash={kv.get('hash')} timeout={kv.get('timeout')}", BOLD))
            sys.stdout.flush()

        elif event == "CLAUDE_PROC_SPAWNED":
            print(c(f"    spawned:       pid={kv.get('pid')} spawn_ms={kv.get('spawn_ms')}", GREY))

        elif event == "CLAUDE_PROC_STALL_60S":
            print(c(f"    ⏳ stall:       pid={kv.get('pid')} elapsed={kv.get('elapsed')} (timeout_in={kv.get('timeout_in_s')}s)", YELLOW))

        elif event == "CLAUDE_CALL_OK":
            el_ms = int(kv.get("el", "0").rstrip("ms") or 0)
            recent_call_ms.append(el_ms)
            avg = sum(recent_call_ms) / len(recent_call_ms)
            print(c(f"  ← CLAUDE_OK:     el={el_ms}ms  out={kv.get('out')}  (rolling avg over {len(recent_call_ms)}: {avg:.0f}ms)", GREEN))
            # Show full prompt+response from dump
            if show_bodies and st.get("call_id"):
                dump = find_dump(st["call_id"], did)
                if dump:
                    show_dump(
                        dump,
                        show_prompt=not args.no_prompt,
                        show_system=args.show_system,
                        response_only=args.response_only,
                    )
                else:
                    if SENTINEL.exists():
                        print(c(f"  [no dump file found for call_id={st['call_id']} — restart workers so the patched claude_code_client.py is loaded]", YELLOW))
                    else:
                        print(c(f"  [dump sentinel disabled — `touch data/stage2_dumps/.enabled` to capture bodies]", YELLOW))

        elif event in ("CLAUDE_CALL_FAIL", "CLAUDE_CALL_TIMEOUT"):
            print(c(f"  ✗ {event}: {rest.split('|')[0].strip()}", RED))

        elif event == "STRAT_CALL_A_PLAN":
            view = ""
            mv = re.search(r"view='([^']*)'", rest)
            if mv:
                view = mv.group(1)
            print(c(f"  parsed plan:     trades={kv.get('trades')}  risk={kv.get('risk')}", BOLD))
            if view:
                print(c(f"    view: {view}", GREY))

        elif event == "STRAT_ZERO_TRADES_INTENTIONAL":
            print(c(f"  zero-trades intentional under {kv.get('contract')} contract", YELLOW))

        elif event == "STRAT_CALL_A_END":
            el_ms = int(kv.get("el", "0").rstrip("ms") or 0)
            print(c(f"  ▣ CYCLE END:     stage2 total elapsed={el_ms}ms  trades={kv.get('trades')}", CYAN))
            print(hr("", CYAN))
            sys.stdout.flush()
            cycles.pop(did, None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[stopped]")
