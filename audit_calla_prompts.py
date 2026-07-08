"""Forensic audit of LIVE Call-A prompts (post entry-quality fixes, 2026-06-10).

Reads the captured Claude-call dumps in data/stage2_dumps/, picks the Call-A
prompts (trade-finding), notes each one down, and audits the candidate-block
data for errors / flaws / bugs / skew / bias — the same forensic lens the
losing-window investigation used, now on the post-fix live prompts.

Appends a human-readable section per prompt to CALL_A_LIVE_PROMPT_AUDIT_2026-06-10.md
and prints a PASS/FLAG summary. Read-only; never touches the live system.
"""
from __future__ import annotations
import glob, json, re, sys
from collections import Counter
from datetime import datetime, timezone

OUT = "/home/inshadaliqbal786/CALL_A_LIVE_PROMPT_AUDIT_2026-06-10.md"


def is_calla(d) -> bool:
    sp = d.get("system_prompt", "") or ""
    return "BEST GENUINE plays" in sp or "exploit the current market" in sp


def parse_blocks(prompt: str):
    """Each briefed candidate is a '### SYM — interestingness=... ' block."""
    blocks = []
    parts = re.split(r'\n### ', prompt)
    for p in parts[1:]:
        head = p.splitlines()[0]
        m = re.match(r'([A-Z0-9]+USDT)\s+—\s+interestingness=([0-9.]+)\s+score=([0-9.]+)', head)
        if not m:
            continue
        sym, interest, score = m.group(1), float(m.group(2)), float(m.group(3))
        xray = re.search(r'XRAY:\s*setup=(\S+)\s+conf=([0-9.]+)\s+dir=(\S+)\s+score=([0-9-]+)\s+quality=(\S+)', p)
        struct = re.search(r'market_structure=(\S+)', p)
        rr = re.search(r'RR by direction:\s*long=([0-9.]+)\s+short=([0-9.]+)\s+better=(\S+)', p)
        sig = re.search(r'\bSignal:\s*([A-Z_]+)(?:\s+conf=?([0-9.]+))?', p)
        oi = re.search(r'oi_change_(?:24h_)?pct=([\-0-9.]+)', p)  # Fix 2: renamed key, accept both
        blocks.append(dict(
            sym=sym, interest=interest, score=score,
            xray_setup=xray.group(1) if xray else None,
            xray_conf=float(xray.group(2)) if xray else None,
            xray_dir=xray.group(3) if xray else None,
            xray_quality=xray.group(5) if xray else None,
            struct=struct.group(1) if struct else None,
            rr_long=float(rr.group(1)) if rr else None,
            rr_short=float(rr.group(2)) if rr else None,
            rr_better=rr.group(3) if rr else None,
            signal=sig.group(1) if sig else None,
            oi_change=float(oi.group(1)) if oi else None,
        ))
    return blocks


def audit(path):
    d = json.load(open(path))
    sp, pr, rs = d["system_prompt"], d["prompt"], d["response"]
    flags, notes = [], []

    # Fix 6 — mandate
    fix6 = ("2 to 5 BEST GENUINE plays" in sp and "QUALITY OVER QUOTA" in sp
            and not any(x in sp for x in ["MINIMUM of 3 trades", "Do not stop short of 3", "AT LEAST 3"]))
    if not fix6: flags.append("Fix6 mandate NOT reworded in this prompt")

    # Fix 3 — sentiment must be fully absent from the candidate data
    sent = ("overall_sentiment" in pr) or ("news_count" in pr) or bool(re.search(r'\bSentiment:\s*[-0-9]', pr))
    if sent: flags.append("Fix3 VIOLATED — sentiment field present in candidate data")

    blocks = parse_blocks(pr)
    confs = [b["xray_conf"] for b in blocks if b["xray_conf"] is not None]
    quals = Counter(b["xray_quality"] for b in blocks if b["xray_quality"])
    dirs = Counter(b["xray_dir"] for b in blocks if b["xray_dir"])
    sigs = Counter(b["signal"] for b in blocks if b["signal"])

    # Bias / quality distribution
    pct_low = (sum(1 for c in confs if c < 0.30) / len(confs) * 100) if confs else 0
    # Fix-1 style consistency: structure vs xray dir contradiction
    contra = [b["sym"] for b in blocks
              if b["struct"] and b["xray_dir"]
              and ((b["struct"] == "uptrend" and b["xray_dir"] == "short")
                   or (b["struct"] == "downtrend" and b["xray_dir"] == "long"))]
    # Data integrity: missing key fields
    missing = [b["sym"] for b in blocks if b["xray_conf"] is None or b["struct"] is None]

    # Brain response
    try:
        nt = json.loads(rs).get("new_trades", [])
        trade_dirs = Counter(t.get("direction") for t in nt)
        n_trades = len(nt)
    except Exception:
        nt, trade_dirs, n_trades = [], Counter(), -1

    notes.append(f"briefed coins={len(blocks)} | quality={dict(quals)} | xray_dir={dict(dirs)} | signal={dict(sigs)}")
    notes.append(f"xray_conf: n={len(confs)} min={min(confs) if confs else 0:.2f} max={max(confs) if confs else 0:.2f} %<0.30={pct_low:.0f}% (losing-window baseline ~50%)")
    notes.append(f"brain returned {n_trades} trade(s): {[(t.get('symbol'),t.get('direction')) for t in nt]} dirs={dict(trade_dirs)}")
    if contra: flags.append(f"structure-vs-xray-dir contradiction on {contra}")
    if missing: notes.append(f"coins missing xray/structure fields (info-absent, not error): {missing}")
    # Direction skew check across the briefed set
    if dirs and (set(dirs) == {"long"} or set(dirs) == {"short"}) and len(blocks) >= 4:
        flags.append(f"one-sided xray_dir skew: all {list(dirs)[0]} ({len(blocks)} coins)")

    return d, blocks, flags, notes, fix6, n_trades


def main():
    files = sorted(glob.glob("/home/inshadaliqbal786/trading-intelligence-mcp/data/stage2_dumps/*.json"))
    # only post-restart (>=20:00 2026-06-10) Call-A
    files = [f for f in files if re.search(r'20260610T20', f)]
    with open(OUT, "a") as out:
        out.write(f"\n\n# Call-A Live Prompt Audit (run {datetime.now(timezone.utc).strftime('%H:%M:%SZ')})\n")
        any_flag = False
        for f in files:
            try:
                d = json.load(open(f))
                if not is_calla(d):
                    continue
            except Exception:
                continue
            d, blocks, flags, notes, fix6, n_trades = audit(f)
            ts = d["ts_utc"]
            print(f"\n=== Call-A {ts} ({f.split('/')[-1]}) ===")
            out.write(f"\n## Call-A {ts}  (sys={d['system_prompt_chars']} prompt={d['prompt_chars']} resp={d['response_chars']})\n")
            for n in notes:
                print("  ", n); out.write(f"- {n}\n")
            if flags:
                any_flag = True
                for fl in flags:
                    print("   FLAG:", fl); out.write(f"- **FLAG:** {fl}\n")
            else:
                print("   no data flaws/bias flagged (sentiment absent, mandate reworded, distribution healthy)")
                out.write("- no data flaws/bias flagged (sentiment absent, mandate reworded, distribution healthy)\n")
            # note down the full briefed candidate table
            out.write("\n  | coin | interest | xray_conf | dir | quality | structure | RR(L/S) | signal | oi% |\n")
            out.write("  |---|---|---|---|---|---|---|---|---|\n")
            for b in blocks:
                out.write(f"  | {b['sym']} | {b['interest']:.2f} | {b['xray_conf']} | {b['xray_dir']} | {b['xray_quality']} | {b['struct']} | {b['rr_long']}/{b['rr_short']} | {b['signal']} | {b['oi_change']} |\n")
        print(f"\n==== {'FLAGS RAISED — see audit file' if any_flag else 'ALL AUDITED PROMPTS CLEAN'} ====")
        print(f"notes appended to {OUT}")


if __name__ == "__main__":
    main()
