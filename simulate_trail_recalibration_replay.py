#!/usr/bin/env python3
"""Stage B Phase 1 — faithful, gateway-driven replay of trail-recalibration candidates.

Read-only. No DB, no exchange, no protected-table access. It reconstructs every
trade in the captured 28-hour window (the real big movers) from the logged truth and
asks, per candidate trail geometry: given the same realized per-second price path,
what would the REAL SLGateway actually have PLACED, and therefore captured, net of
the round-trip fee — versus the live baseline (trail_r=0.5) and versus the trade's
actual realized close?

Why a new harness (not simulate_adaptive_exit_replay.py): that one runs on the old
pre-universe-fix one-hour window and computes captures with an idealized in-process
walk that assumes every lock is placed perfectly. The operator requires the capture
numbers to come from the REAL gateway with the placeability mechanism (fresh-mark
degrade) live, on the new window's real movers. This harness does exactly that.

Faithfulness and its honest limits:
  - Each tick calls the real SLGateway.apply with the candidate lock as the proposed
    stop, the resting placed stop as current_sl, the logged price as current_price,
    the trade's R/class via the volatility-profiler interface, source=profit_sniper_
    ladder with the profit-lock and breakeven floors passed as production passes them.
    The real R2 clamp, profit-lock exemption, tighten-only, fresh-mark degrade, and
    terminal guard all run unmodified. The accepted stop is what would be placed.
  - The fresh mark the gateway validates against is modelled by the NEXT observed
    price tick — the freshest available proxy. Per-second data cannot resolve the
    ~150ms live-mark latency, so this OVER-states the gap and therefore over-counts
    (never under-counts) the fresh-mark-degrade no-ops: the conservative direction.
  - Owner gate and rate limit are isolated (as the existing harness does) to study the
    geometry+placeability question cleanly; both are unchanged in production.
  - apply() is invoked only when the running peak makes a new high (the lock is
    monotonic in peak; on flat-peak ticks the ladder re-proposes the same lock and the
    gateway tighten-only no-ops it, leaving the resting stop unchanged). The exit check
    runs on EVERY tick. This is faithful and fast.

Self-validation: candidate B0 (base, trail_r=0.5) IS the live geometry, so its replayed
captures must track the actual realized closes; the harness reports the B0-vs-actual
mean absolute error as a credibility check before any candidate is trusted.
"""
import asyncio
import dataclasses
import logging
import re
import statistics as stats
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace

# Silence the gateway's loguru sink: ~100k apply() calls would otherwise flood output.
try:
    from loguru import logger as _loguru
    _loguru.remove()
except Exception:
    pass
logging.disable(logging.CRITICAL)

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway
from src.analysis import vol_scale as vs

LOG = "/root/log_bundle_2026-06-17T0130_to_2026-06-18T0530_UTC.log"


# ── timestamp helpers ──────────────────────────────────────────────────────
def _lead_ts(line):
    """Epoch seconds from the leading 'YYYY-MM-DD HH:MM:SS.mmm' (the trading clock)."""
    try:
        return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except Exception:
        return None


def _iso_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


# ── parse the logged truth ─────────────────────────────────────────────────
RX_OPEN = re.compile(
    r"THESIS_OPEN \| id=(?P<id>\d+) sym=(?P<sym>\S+) dir=(?P<dir>\S+) "
    r"ent=(?P<ent>[0-9.]+) sl=(?P<sl>[0-9.]+).*?order_id=(?P<oid>[a-z0-9-]+)")
RX_CLOSE = re.compile(
    r"THESIS_CLOSE \| sym=(?P<sym>\S+) order_id=(?P<oid>[a-z0-9-]+) "
    r"pnl=(?P<pnl>[-+0-9.]+)%.*?rsn=(?P<rsn>\w+)")
RX_PATH = re.compile(
    r"PRICE_PATH \| ts=(?P<ts>\S+) sym=(?P<sym>\S+) tid=(?P<oid>\S+) "
    r"px=(?P<px>[0-9.]+) pnl=(?P<pnl>[-+0-9.]+)%")
RX_LAD = re.compile(r"LADDER_ADAPTIVE \| sym=(?P<sym>\S+) peak=[0-9.]+% R=(?P<R>[0-9.]+)%")


def parse():
    opens, closes = {}, {}
    paths = defaultdict(list)             # oid -> [(tick_ts, px, pnl)]
    lad = defaultdict(list)               # sym -> [(lead_ts, R)]
    with open(LOG, errors="ignore") as fh:
        for line in fh:
            if "THESIS_OPEN |" in line:
                m = RX_OPEN.search(line)
                if m:
                    opens[m["oid"]] = {
                        "sym": m["sym"], "dir": m["dir"], "entry": float(m["ent"]),
                        "init_sl": float(m["sl"]), "open_ts": _lead_ts(line)}
            elif "THESIS_CLOSE |" in line:
                m = RX_CLOSE.search(line)
                if m:
                    closes[m["oid"]] = {
                        "pnl": float(m["pnl"]), "rsn": m["rsn"], "close_ts": _lead_ts(line)}
            elif "PRICE_PATH |" in line:
                m = RX_PATH.search(line)
                if m:
                    paths[m["oid"]].append((_iso_ts(m["ts"]), float(m["px"]), float(m["pnl"])))
            elif "LADDER_ADAPTIVE |" in line:
                m = RX_LAD.search(line)
                if m:
                    lad[m["sym"]].append((_lead_ts(line), float(m["R"])))
    return opens, closes, paths, lad


def build_trades(opens, closes, paths, lad):
    """Join into reconstructable trades; report honest gaps."""
    trades, dropped = [], defaultdict(int)
    for oid, o in opens.items():
        if oid not in closes:
            dropped["no_close"] += 1
            continue
        pts = sorted((t for t in paths.get(oid, []) if t[0] is not None), key=lambda x: x[0])
        if len(pts) < 5:
            dropped["too_few_ticks"] += 1
            continue
        # R for the trade: median of its symbol's LADDER_ADAPTIVE R within [open,close].
        o_ts, c_ts = o["open_ts"], closes[oid]["close_ts"]
        rs = [r for (ts, r) in lad.get(o["sym"], []) if ts and o_ts and c_ts and o_ts - 5 <= ts <= c_ts + 5]
        if not rs:
            dropped["no_R"] += 1
            continue
        R = stats.median(rs)
        ticks = pts  # (ts, px, pnl) per tick, sorted by ts — ts needed for the throttle clock
        peak = max(p for (_ts, _px, p) in ticks)
        trades.append({
            "oid": oid, "sym": o["sym"], "dir": o["dir"], "entry": o["entry"],
            "init_sl": o["init_sl"], "R": R, "ticks": ticks, "peak": peak,
            "actual_close": closes[oid]["pnl"], "rsn": closes[oid]["rsn"]})
    return trades, dropped


# ── parameterized candidate geometry (mirrors vol_scale.profit_lock_pct) ────
def cand_lock(peak, R, ae, fee, kind, val):
    """The candidate profit lock. kind 'base' MUST equal vol_scale.profit_lock_pct."""
    if R <= 0:
        return None
    if kind == "decay":
        # Phase 1 give-back fix candidate. `val` is a cloned AdaptiveExitSettings
        # carrying the candidate trail_r_floor/knee/scale; route to the REAL
        # production geometry so the harness proves the exact shipped function
        # (maximal faithfulness) rather than a re-implementation of it.
        return vs.profit_lock_pct(peak, R, val, fee)
    arm = vs.arm_pct(R, ae, fee)
    if peak < arm:
        return None
    rungs = list(ae.rung_r)
    trail_r = float(ae.trail_r)
    if kind == "base":
        d = trail_r * R
    elif kind == "flat":
        d = val * R
    elif kind == "cap":
        d = min(trail_r * R, val)
    else:
        raise ValueError(kind)
    trail = peak - d
    staged = 0.0
    if len(rungs) >= 2 and peak >= rungs[1] * R:
        staged = float(ae.secure_at_3r_r) * R
    elif len(rungs) >= 1 and peak >= rungs[0] * R:
        staged = fee
    lock = max(fee, trail, staged)
    lo, hi = fee, float(ae.lock_max_pct)
    if lock < lo:
        lock = lo
    if hi > 0 and lock > hi:
        lock = hi
    return lock


# ── gateway stubs ──────────────────────────────────────────────────────────
class _VP:
    def __init__(self):
        self.R = 0.0
        self.cls = "medium"
    async def get_profile(self, sym):
        return SimpleNamespace(atr_pct_5m=self.R, volatility_class=self.cls)


class _POS:
    def __init__(self):
        self.fresh = 0.0
    async def get_position(self, sym):
        return SimpleNamespace(mark_price=self.fresh)
    async def set_stop_loss(self, sym, new_sl):
        return True   # the wire always succeeds in replay; placeability is judged upstream


class _MKT:
    async def get_ticker(self, x):
        return None


class _EVT:
    def add_event(self, *a, **k):
        pass


def build_gw(settings, vp, pos):
    return SLGateway(settings=settings, position_service=pos, market_service=_MKT(),
                     event_buffer=_EVT(), volatility_profiler=vp)


def ideal_capture(t, ae, fee, kind, val):
    """Idealized capture: the lock ALWAYS places and ratchets (no gateway, no
    placeability). pnl-space, valid for both directions (log pnl is signed by
    direction: positive = profit). The upper bound the trail geometry could reach
    if every computed lock were placed perfectly."""
    stop_pct = None
    peak = -1e9
    for _ts, _px, pnl in t["ticks"]:
        if pnl > peak:
            peak = pnl
            lk = cand_lock(peak, t["R"], ae, fee, kind, val)
            if lk is not None:
                stop_pct = lk if stop_pct is None else max(stop_pct, lk)
        if stop_pct is not None and pnl <= stop_pct:
            return stop_pct - fee
    return t["ticks"][-1][2] - fee


# ── replay one trade through the REAL gateway for one candidate ─────────────
async def replay_trade(gw, vp, pos, t, ae, fee, kind, val, cadence_n=None, sniper_tick=5.0):
    """Replay one trade through the REAL gateway.

    cadence_n=None  -> optimistic ceiling: attempt a placement on every new peak
                       high, no throttle (the original per-second behaviour).
    cadence_n=<sec> -> FAITHFUL throttle: the sniper evaluates the spine every
                       ~sniper_tick seconds and may PLACE only when >= cadence_n
                       seconds have elapsed since the last ACCEPT — mirroring the
                       per-symbol rate-limit clock (_last_change) plus the sniper's
                       next_eligible_in_seconds short-circuit. The lock is computed
                       from the MONOTONIC running peak, so a peak that formed during
                       an ineligible window is RETRIED at the next eligible tick
                       (often wrong-side/unplaceable by then, so the fresh-mark
                       degrade holds the existing looser stop — exactly the live
                       2,370 no-ops). Exit (price crossing the resting placed stop)
                       is checked EVERY tick, at exchange granularity.
    """
    is_long = (t["dir"] == "Buy")
    entry = t["entry"]
    resting = t["init_sl"]
    peak = -1e9
    ticks = t["ticks"]            # (ts, px, pnl)
    n = len(ticks)
    symkey = f"{t['sym']}::{t['oid'][:8]}::{kind}{val}::n{cadence_n}"
    last_accept_ts = -1e18
    last_eval_ts = -1e18
    degrades = 0
    exit_pnl = None
    for i in range(n):
        ts, px, pnl = ticks[i]
        new_high = pnl > peak
        if new_high:
            peak = pnl
        # sniper evaluation gate: every new-high (ceiling) or every ~sniper_tick (throttle)
        do_eval = new_high if cadence_n is None else (ts - last_eval_ts >= sniper_tick)
        if do_eval:
            last_eval_ts = ts
            eligible = (cadence_n is None) or (ts - last_accept_ts >= cadence_n)
            if eligible:
                lk = cand_lock(peak, t["R"], ae, fee, kind, val)
                if lk is not None:
                    lock_price = entry * (1 + lk / 100) if is_long else entry * (1 - lk / 100)
                    would_tighten = ((is_long and lock_price > resting)
                                     or ((not is_long) and lock_price < resting))
                    if would_tighten:
                        pos.fresh = ticks[i + 1][1] if i + 1 < n else px
                        res = await gw.apply(
                            symbol=symkey, new_sl=lock_price, source="profit_sniper_ladder",
                            direction=t["dir"], current_sl=resting, current_price=px,
                            entry_price=entry, profit_lock_floor_price=lock_price,
                            breakeven_floor_price=entry, bypass_step_cap_for_breakeven=True,
                            bypass_rate_limit=True)
                        if res.accepted and res.new_sl_applied:
                            resting = res.new_sl_applied
                            last_accept_ts = ts
                        elif res.reason and ("fresh_degrade" in res.reason
                                             or res.reason == "clamp_noop"):
                            degrades += 1
        # exit check every tick (exchange granularity)
        if is_long and px <= resting:
            exit_pnl = (resting / entry - 1) * 100
            break
        if (not is_long) and px >= resting:
            exit_pnl = (1 - resting / entry) * 100
            break
    if exit_pnl is None:
        exit_pnl = ticks[-1][2]
    return exit_pnl - fee, degrades


# ── cohorts / reporting ─────────────────────────────────────────────────────
def cohort(t):
    p = t["peak"]
    if p < 0.5:
        return "small (<0.5%)"
    if p < 1.0:
        return "mid (0.5-1%)"
    return "big (>=1%)"


async def main():
    print("Parsing the 28h log ...", flush=True)
    opens, closes, paths, lad = parse()
    trades, dropped = build_trades(opens, closes, paths, lad)
    print(f"opens={len(opens)} closes={len(closes)} reconstructable trades={len(trades)}")
    print(f"dropped: {dict(dropped)}\n")

    settings = Settings._load_fresh()
    settings.sl_gateway.owner_switch_enforce = False
    settings.sl_gateway.rate_limit_seconds = 0
    ae = settings.adaptive_exit
    fee = vs.fee_floor_pct(ae)

    # self-check: base candidate == production profit_lock_pct
    for (pk, R) in [(1.902, 1.842), (0.977, 0.811), (0.43, 0.81)]:
        a = cand_lock(pk, R, ae, fee, "base", None)
        b = vs.profit_lock_pct(pk, R, ae)
        assert (a is None and b is None) or abs((a or 0) - (b or 0)) < 1e-9, (pk, R, a, b)
    print("self-check OK: base candidate reproduces vol_scale.profit_lock_pct exactly\n")

    # Phase 1 give-back fix — the profit-scaled "decay" trail. Each candidate is a
    # cloned config with a different (trail_r_floor, knee, scale); the harness drives
    # the REAL vol_scale.profit_lock_pct with it. base (trail_r=0.5) is the live
    # geometry and the self-check / faithfulness anchor. The flat/cap candidates were
    # already disproven (uniform tightening does not recover the give-back), so the
    # grid is now the profit-scaled sweep.
    def _decay_cfg(floor, knee, scale):
        return dataclasses.replace(ae, trail_r_floor=floor,
                                   trail_tighten_knee_r=knee, trail_tighten_scale_r=scale)

    decay_grid = [
        (0.30, 1.0, 1.0),
        (0.25, 1.0, 1.0),
        (0.20, 1.0, 1.0),
        (0.20, 1.0, 2.0),
        (0.20, 1.5, 1.0),
        (0.15, 1.0, 1.0),
        (0.15, 1.5, 2.0),
    ]
    candidates = [("base", None, "B0 baseline trail_r=0.5")]
    for (fl, kn, sc) in decay_grid:
        candidates.append(("decay", _decay_cfg(fl, kn, sc),
                           f"D fl{fl:.2f} kn{kn:.1f} sc{sc:.1f}"))

    vp, pos = _VP(), _POS()
    results = {}            # label -> per-trade list of (t, net, degrades)
    for kind, val, label in candidates:
        gw = build_gw(settings, vp, pos)
        rows = []
        for t in trades:
            vp.R = t["R"]
            net, deg = await replay_trade(gw, vp, pos, t, ae, fee, kind, val)
            rows.append((t, net, deg))
        results[label] = rows

    # actual realized (net of fee) for the same trades
    actual_net = {t["oid"]: t["actual_close"] - fee for t in trades}

    # ---- self-validation: B0 vs actual ----
    b0 = results["B0 baseline trail_r=0.5"]
    mae = stats.mean(abs(net - actual_net[t["oid"]]) for (t, net, _d) in b0)
    print(f"SELF-VALIDATION: B0 replayed vs actual realized close, mean abs error = {mae:.3f}% "
          f"(small = the gateway-driven replay tracks reality)\n")

    # ---- apples-to-apples reality baseline on the SAME 299 trades ----
    act_nets = [actual_net[t["oid"]] for t in trades]
    act_wins = [x for x in act_nets if x > 0]
    print(f"ACTUAL reality on these {len(trades)} trades: net sum {sum(act_nets):+.2f}%  "
          f"win {100*len(act_wins)/len(trades):.0f}%  medWin "
          f"{stats.median(act_wins) if act_wins else 0:+.3f}%")
    print("(NOTE: these are sums of per-trade pnl PERCENTAGES, not dollar PnL; trade sizes "
          "differ and 100 trades without a clean R were dropped, so this is not the window's "
          "dollar bottom line — it is an apples-to-apples per-trade-% basis for comparison.)\n")

    # ============ CADENCE SWEEP — the placeability fix (primary deliverable) ============
    # Model the 30s rate-limit throttle on the baseline geometry and sweep the
    # profit-lock-lane cadence. N=None is the optimistic per-second ceiling; N=30
    # must reproduce reality (the faithfulness gate); N=15/10/5 are the candidate
    # faster cadences. The honest recoverable is the gain over N=30, discounted by
    # the N=30-vs-reality residual (the irreducible per-second optimism).
    print("\n" + "=" * 78)
    print("CADENCE SWEEP — 30s-throttle model on baseline geometry (the placeability fix)")
    print("=" * 78)
    cohorts_list = ["small (<0.5%)", "mid (0.5-1%)", "big (>=1%)"]
    CADENCES = [None, 30, 15, 10, 5]
    cad_results = {}
    for N in CADENCES:
        gw = build_gw(settings, vp, pos)
        rows = []
        for t in trades:
            vp.R = t["R"]
            net, deg = await replay_trade(gw, vp, pos, t, ae, fee, "base", None, cadence_n=N)
            rows.append((t, net, deg))
        cad_results[N] = rows
    print(f"\n  {'cadence':>9} {'net sum':>9} {'win%':>6} {'medWin':>7}   cohort net mean (small / mid / big)")
    for N in CADENCES:
        rows = cad_results[N]
        net_sum = sum(net for _t, net, _d in rows)
        wins = [net for _t, net, _d in rows if net > 0]
        winr = 100 * len(wins) / len(rows)
        medwin = stats.median(wins) if wins else 0.0
        cm = {}
        for c in cohorts_list:
            cn = [net for (t, net, _d) in rows if cohort(t) == c]
            cm[c] = stats.mean(cn) if cn else 0.0
        label = "ceiling" if N is None else f"{N}s"
        print(f"  {label:>9} {net_sum:>+8.2f}% {winr:>5.0f}% {medwin:>+6.3f}%   "
              f"{cm['small (<0.5%)']:+.3f} / {cm['mid (0.5-1%)']:+.3f} / {cm['big (>=1%)']:+.3f}")
    # faithfulness gate + recoverable
    areal = sum(actual_net[t["oid"]] for t in trades)
    arealw = 100 * sum(1 for t in trades if actual_net[t["oid"]] > 0) / len(trades)
    n30 = sum(net for _t, net, _d in cad_results[30])
    n30w = 100 * sum(1 for _t, net, _d in cad_results[30] if net > 0) / len(trades)
    residual = n30 - areal
    print(f"\n  FAITHFULNESS GATE: N=30 -> net {n30:+.2f}% / win {n30w:.0f}%   "
          f"vs reality {areal:+.2f}% / {arealw:.0f}%")
    print(f"    residual (N=30 minus reality) = {residual:+.2f}% net = the irreducible per-second")
    print(f"    optimism the throttle cannot remove; discount each cadence's gain by it.")
    print(f"\n  HONEST RECOVERABLE (gross gain over the 30s baseline, then discount the residual):")
    for N in [15, 10, 5]:
        gain = sum(net for _t, net, _d in cad_results[N]) - n30
        honest = gain - max(0.0, residual)
        print(f"    N={N:>2}s: +{gain:.2f}% gross over 30s  ->  ~{honest:+.2f}% honest (residual-discounted)")

    # ---- headline: median peak vs median win per candidate ----
    med_peak = stats.median([t["peak"] for t in trades])
    print(f"\nmedian realized peak across {len(trades)} trades: {med_peak:+.3f}%")
    print("\nCANDIDATE COMPARISON (net of fee, through the REAL gateway):")
    hdr = f"  {'candidate':<28} {'net sum':>9} {'win%':>6} {'medWin':>7} {'deg':>6}"
    print(hdr)
    for kind, val, label in candidates:
        rows = results[label]
        net_sum = sum(net for _t, net, _d in rows)
        wins = [net for _t, net, _d in rows if net > 0]
        winr = 100 * len(wins) / len(rows)
        medwin = stats.median(wins) if wins else 0.0
        degs = sum(d for _t, _n, d in rows)
        print(f"  {label:<28} {net_sum:>+8.2f}% {winr:>5.0f}% {medwin:>+6.3f}% {degs:>6}")

    # ---- cohort split: is the small cohort regressed vs B0? ----
    print("\nCOHORT CAPTURE (mean kept % of peak; watch the small cohort for regression):")
    cohorts = ["small (<0.5%)", "mid (0.5-1%)", "big (>=1%)"]
    counts = {c: sum(1 for t in trades if cohort(t) == c) for c in cohorts}
    print(f"  cohorts: " + ", ".join(f"{c} n={counts[c]}" for c in cohorts))
    print(f"  {'candidate':<28} " + " ".join(f"{c.split()[0]:>8}" for c in cohorts))
    for kind, val, label in candidates:
        rows = results[label]
        line = f"  {label:<28} "
        for c in cohorts:
            keeps = []
            for (t, net, _d) in rows:
                if cohort(t) == c and t["peak"] > 0:
                    gross = net + fee
                    keeps.append(100 * gross / t["peak"])
            line += f"{(stats.mean(keeps) if keeps else 0):>7.0f}% "
        print(line)

    # ---- three-way decomposition: idealized vs gateway-driven vs actual ----
    # idealized (perfect placement) - gateway (per-second placeability) - actual reality.
    # The gap idealized->gateway is the placeability cost the per-second replay CAN see;
    # gateway->actual is the sub-second placeability cost the per-second data CANNOT see
    # (its sign/size shows how much the real fresh-mark leak exceeds what we can model).
    print("\nPLACEABILITY DECOMPOSITION (net sum, %, across all 299 trades):")
    actual_sum = sum(actual_net[t["oid"]] for t in trades)
    print(f"  {'candidate':<28} {'idealized':>10} {'gateway':>9} {'ideal-gw':>9}")
    for kind, val, label in candidates:
        ideal_sum = sum(ideal_capture(t, ae, fee, kind, val) for t in trades)
        gw_sum = sum(net for _t, net, _d in results[label])
        print(f"  {label:<28} {ideal_sum:>+9.2f}% {gw_sum:>+8.2f}% {ideal_sum-gw_sum:>+8.2f}%")
    print(f"  actual realized (reality, B0 only):  {actual_sum:>+8.2f}%")
    print(f"  => gateway(B0) {sum(n for _t,n,_d in b0):+.2f}% vs actual {actual_sum:+.2f}%: "
          f"the {sum(n for _t,n,_d in b0)-actual_sum:+.2f}% gap is the sub-second placeability "
          f"leak the per-second replay cannot reproduce (replay is optimistic on fast movers).")

    # ---- cohort-level B0 fidelity (where the replay tracks reality vs not) ----
    print("\nB0 FIDELITY BY COHORT (gateway-replay vs actual; large gap = replay too optimistic):")
    for c in cohorts:
        rows = [(t, net) for (t, net, _d) in b0 if cohort(t) == c]
        if not rows:
            continue
        mae = stats.mean(abs(net - actual_net[t["oid"]]) for t, net in rows)
        gwmean = stats.mean(net for _t, net in rows)
        acmean = stats.mean(actual_net[t["oid"]] for t, _net in rows)
        print(f"  {c:<14} n={len(rows):>3}  gateway {gwmean:+.3f}%  actual {acmean:+.3f}%  MAE {mae:.3f}%")

    # ---- the BEAT worked example across candidates ----
    print("\nBEAT worked example (the diagnosis trade) — net captured per candidate:")
    beat = [t for t in trades if t["sym"] == "BEATUSDT"]
    beat = sorted(beat, key=lambda t: -t["peak"])[:1]
    for t in beat:
        print(f"  BEAT oid={t['oid'][:8]} peak={t['peak']:+.2f}% R={t['R']:.2f}% "
              f"actual_close={t['actual_close']:+.2f}%")
        for kind, val, label in candidates:
            net = next(n for (tt, n, _d) in results[label] if tt["oid"] == t["oid"])
            print(f"      {label:<28} net={net:+.3f}%")


if __name__ == "__main__":
    asyncio.run(main())
