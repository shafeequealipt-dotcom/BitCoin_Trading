#!/usr/bin/env python3
"""Watch data/stage2_dumps/ in real time and write every CALL_A and CALL_B
prompt+response to a single growing markdown log so the operator can read
each cycle's full prompts as they are produced.

Classification:
    - CALL_A (Strategist):       system_prompt starts with "Your aim is to exploit"
    - CALL_B (Position Manager): system_prompt starts with "You are managing open crypto futures"
    - other:                     unknown — recorded verbatim with a warning

Usage:
    python scripts/watch_both_calls_live.py --out ~/LIVE_CLAUDE_CALLS_<ts>.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT = Path(__file__).resolve().parents[1]
DUMP_DIR = PROJECT / "data" / "stage2_dumps"


def classify(system_prompt: str) -> str:
    s = system_prompt.lstrip()[:80].lower()
    if s.startswith("your aim is to exploit"):
        return "CALL_A"
    if s.startswith("you are managing open crypto futures"):
        return "CALL_B"
    return "UNKNOWN"


def render(path: Path, data: dict) -> str:
    kind = classify(data.get("system_prompt", ""))
    lines = []
    lines.append("")
    lines.append("=" * 100)
    lines.append(f"## {kind}  ({path.name})")
    lines.append(
        f"- ts_utc: {data.get('ts_utc')}  call_id: {data.get('call_id')}  did: {data.get('did')}"
    )
    lines.append(
        f"- elapsed_ms: {data.get('elapsed_ms')}  prompt_hash: {data.get('prompt_hash')}"
    )
    lines.append(
        f"- sys_chars: {data.get('system_prompt_chars')}  prompt_chars: {data.get('prompt_chars')}  response_chars: {data.get('response_chars')}"
    )
    lines.append("")
    lines.append("### SYSTEM PROMPT")
    lines.append("```")
    lines.append(data.get("system_prompt", ""))
    lines.append("```")
    lines.append("")
    lines.append("### USER PROMPT")
    lines.append("```")
    lines.append(data.get("prompt", ""))
    lines.append("```")
    lines.append("")
    lines.append("### CLAUDE RESPONSE")
    lines.append("```")
    lines.append(data.get("response", ""))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output markdown file (appended)")
    ap.add_argument("--raw-dir", required=True,
                    help="directory to write byte-exact .txt prompts (system/user/response per call)")
    ap.add_argument("--from-existing", action="store_true",
                    help="also include dumps that already exist before start time")
    ap.add_argument("--poll-s", type=float, default=1.0, help="poll interval seconds")
    args = ap.parse_args()

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = Path(args.raw_dir).expanduser()
    raw_dir.mkdir(parents=True, exist_ok=True)

    start_ts = time.time()
    seen: set[str] = set()

    if not args.from_existing:
        # Seed `seen` with every file that exists right now so we only catch NEW ones.
        for p in DUMP_DIR.glob("*.json"):
            seen.add(p.name)

    header = (
        f"# Live Claude Calls (CALL_A + CALL_B)\n\n"
        f"- started: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(start_ts))}\n"
        f"- watching: `{DUMP_DIR}`\n"
        f"- from-existing: {args.from_existing}\n\n"
    )
    with out_path.open("a") as fh:
        fh.write(header)
        fh.flush()

    print(f"watching {DUMP_DIR}", flush=True)
    print(f"writing  {out_path}", flush=True)
    print(f"(Ctrl-C to stop)", flush=True)

    counts = {"CALL_A": 0, "CALL_B": 0, "UNKNOWN": 0}

    while True:
        try:
            new_files = sorted(
                p for p in DUMP_DIR.glob("*.json")
                if p.name not in seen
            )
            for p in new_files:
                seen.add(p.name)
                try:
                    data = json.loads(p.read_text())
                except Exception as e:
                    print(f"  skip {p.name}: {e}", flush=True)
                    continue
                block = render(p, data)
                with out_path.open("a") as fh:
                    fh.write(block)
                    fh.flush()
                kind = classify(data.get("system_prompt", ""))
                # Byte-exact raw text files for analysis (no JSON wrapper, no markdown fences)
                base = raw_dir / f"{data.get('ts_utc')}_call{int(data.get('call_id',0)):04d}_{kind}"
                try:
                    (base.with_name(base.name + "_system.txt")).write_text(data.get("system_prompt", ""))
                    (base.with_name(base.name + "_user.txt")).write_text(data.get("prompt", ""))
                    (base.with_name(base.name + "_response.txt")).write_text(data.get("response", ""))
                except Exception as e:
                    print(f"  raw-write failed for {p.name}: {e}", flush=True)
                counts[kind] = counts.get(kind, 0) + 1
                print(
                    f"  {time.strftime('%H:%M:%S')} "
                    f"{kind} call_id={data.get('call_id')} did={data.get('did')} "
                    f"sys={data.get('system_prompt_chars')} prompt={data.get('prompt_chars')} "
                    f"resp={data.get('response_chars')} el={data.get('elapsed_ms')}ms "
                    f"[total A={counts['CALL_A']} B={counts['CALL_B']}]",
                    flush=True,
                )
            time.sleep(args.poll_s)
        except KeyboardInterrupt:
            print("\n[stopped]")
            return


if __name__ == "__main__":
    main()
