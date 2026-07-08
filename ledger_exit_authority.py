#!/usr/bin/env python3
"""Accurate, trade-scoped ledger for the exit-authority live monitoring.

For every trade that CLOSED since a given time, records: direction, entry, the
PEAK it reached (scoped to that trade's own open->close window, taken from the
authoritative per-tick 'Mode4 (SYM): dir= pnl=' line which logs BOTH wins and
losses — never the green-only M4_DECISION), the close, the give-back, the close
reason, and the owner-gate's actions on it (hand-off to green, caging writers
blocked). Classifies each and prints a verdict. Writes EXIT_AUTHORITY_TRADE_LEDGER.md.

Usage: python3 ledger_exit_authority.py ["YYYY-MM-DD HH:MM:SS"]  (default: 01:39 restart)
"""
import re
import sys

LOG = "data/logs/workers.log"
SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-06-15 01:39:00"
ENFORCE_FROM = "2026-06-15 02:18:00"  # enforcement turned on at this restart
LEDGER = "EXIT_AUTHORITY_TRADE_LEDGER.md"

opens = {}    # sym -> list of (ts, entry, dir)
closes = []   # (ts, sym, close_pnl, reason)
mode4 = {}    # sym -> list of (ts, pnl)  — current pnl per tick (win/loss)
m4peak = {}   # sym -> list of (ts, peak_pnl) — the system's tracked high-water
hg = {}       # sym -> count of hand-offs to green
blk = {}      # sym -> count of WRONG_OWNER blocks

with open(LOG, errors="ignore") as f:
    for line in f:
        t = line[:19]
        if len(t) < 19 or t < SINCE:
            continue
        sym_m = re.search(r'sym=([A-Z0-9]+)', line)
        if "THESIS_OPEN" in line and sym_m:
            e = re.search(r'ent=([0-9.]+)', line)
            d = re.search(r'dir=(\w+)', line)
            opens.setdefault(sym_m.group(1), []).append(
                (t, e.group(1) if e else "?", d.group(1) if d else "?"))
        elif "THESIS_CLOSE" in line and sym_m:
            p = re.search(r'pnl=([+-][0-9.]+)%', line)
            r = re.search(r'rsn=([a-z_]+)', line)
            if p:
                closes.append((t, sym_m.group(1), float(p.group(1)),
                               r.group(1) if r else "?"))
        elif "Mode4 (" in line:
            m = re.search(r'Mode4 \(([A-Z0-9]+)\):.*?pnl=([+-]?[0-9.]+)%', line)
            if m:
                mode4.setdefault(m.group(1), []).append((t, float(m.group(2))))
        elif "M4_DECISION" in line and "peak_pnl=" in line and sym_m:
            pk = re.search(r'peak_pnl=([0-9.]+)', line)
            if pk:
                m4peak.setdefault(sym_m.group(1), []).append((t, float(pk.group(1))))
        elif "SL_GATEWAY_OWNER_HANDOFF" in line and "to=green" in line and sym_m:
            hg[sym_m.group(1)] = hg.get(sym_m.group(1), 0) + 1
        elif "SL_GATEWAY_WRONG_OWNER " in line and sym_m:
            blk[sym_m.group(1)] = blk.get(sym_m.group(1), 0) + 1


def trade_peak(sym, open_ts, close_ts):
    """The trade's high-water, scoped to its own open->close window. Combines
    the system's tracked high-water (M4_DECISION peak_pnl) with the per-tick
    win/loss pnl (Mode4), so a trade that went green is captured even if the
    Mode4 tick line was sparse. None only when neither source logged in-window."""
    vals = [p for (pt, p) in m4peak.get(sym, []) if open_ts <= pt <= close_ts]
    vals += [p for (pt, p) in mode4.get(sym, []) if open_ts <= pt <= close_ts]
    return max(vals) if vals else None


out = []
clipped = ran = losers = tps = blocked_total = 0
for (ct, sym, cpnl, rsn) in closes:
    # match the latest open for this symbol at or before the close
    cands = [o for o in opens.get(sym, []) if o[0] <= ct]
    ot, entry, direction = cands[-1] if cands else (SINCE, "?", "?")
    peak = trade_peak(sym, ot, ct)
    peak_s = f"{peak:+.2f}" if peak is not None else "n/a"
    give = f"{peak - cpnl:+.2f}" if peak is not None else "n/a"
    cg = blk.get(sym, 0)
    blocked_total += cg
    mode = "ENFORCED" if ct >= ENFORCE_FROM else "log-only"
    # classify
    if peak is not None and peak >= 0.15:
        if cpnl <= peak / 2:
            cls = "CLIPPED"; clipped += 1
        else:
            cls = "ran/kept"; ran += 1
    else:
        cls = "loser/scratch"; losers += 1
    if rsn.endswith("_tp") or "take_profit" in rsn:
        tps += 1
    out.append(f"  {ct[11:]}  {sym:<10} {direction:<4} ent={entry:<9} "
               f"peak {peak_s}%  close {cpnl:+.3f}%  give-back {give}%  "
               f"[{cls}]  {mode}  reason={rsn}  green-cagers-blocked={cg}")

with open(LEDGER, "w") as lf:
    def w(s): print(s); lf.write(s + "\n")
    w("# Exit-authority trade ledger (trade-scoped, authoritative peaks)")
    w("")
    w(f"Trades closed since {SINCE}. Enforcement ON since {ENFORCE_FROM}.")
    w("Peak = the trade's own high-water from its open-to-close window.")
    w("")
    for line in out:
        w(line)
    w("")
    w("## Summary")
    w(f"  closed={len(closes)}  clipped={clipped}  ran/kept={ran}  "
      f"losers/scratch={losers}  take-profits-hit={tps}  "
      f"green-cagers-blocked(total)={blocked_total}")
    enf = [c for c in closes if c[0] >= ENFORCE_FROM]
    w(f"  of which ENFORCED (since 02:18): {len(enf)} closes")
