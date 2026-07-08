#!/usr/bin/env python3
"""Phase 3 replay — the R-based adaptive exit geometry against the real logged trades.

Read-only. No DB, no exchange, no protected-table access. It reconstructs each
trade in the captured one-hour window (ALL_LOGS_2026-06-15_0230-0330_UTC.log)
from the logged truth and asks one question per trade: given the same realized
price path, what would the per-trade R-based adaptive geometry have locked,
net of the trade's own round-trip fee, versus what the flat hardcoded geometry
actually locked?

Sources of logged truth (no fabrication; honest gaps are reported):
  - COORD_CLOSE_START: the real flat outcome (sym, entry, realized pnl%, reason,
    held seconds, close timestamp). This IS the flat result — what actually happened.
  - M4_DECISION: the sniper's own per-tick pnl% and running peak_pnl% (~5s), tid-
    keyed, across the full window — the realized path and the true peak.
  - VOL_PROFILE: the coin's atr_pct (the movement unit R) and volatility class.

The adaptive geometry (blueprint starting points, to be tuned): arm = max(0.5R,
fee_floor); the ladder rungs at 1.5R/3R/5R with staged locks; the trail at 1R
behind the running peak, every profit lock floored at the round-trip fee so a
locked win is net-positive. The flat geometry is the current config: arm 0.2%,
first rung 0.6%, breakeven lock 0.05% lifted to 0.13% on fee-clearance.

It also drives the REAL SLGateway on the canonical clamp-noop trace (ALICEUSDT)
to show, on the unmodified gateway, that holding the lock at its R-derived value
(passed as breakeven_floor_price from a trusted source) writes where the current
breakeven-only hold is dropped as a clamp-noop.

The headline the operator asked for is reported explicitly: the count of trades
whose peak never cleared their own round-trip fee — the bridge to the entry
question, because no exit can make those net-positive.
"""
import asyncio
import re
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway

LOG = "ALL_LOGS_2026-06-15_0230-0330_UTC.log"
ROUND_TRIP_FEE_PCT = 0.11  # taker 0.055% each way (config cap_round_trip_fee_pct)
FEE_FLOOR_PCT = ROUND_TRIP_FEE_PCT  # a lock must clear this to be net-positive

# Adaptive R-multiples (blueprint starting points, bounded; tuned on this replay).
ARM_R = 0.5
RUNG_R = (1.5, 3.0, 5.0)
TRAIL_R = 1.0
SECURE_AT_3R = 1.5  # secured profit (in R) once 3R reached


# ── parsing the logged truth ──────────────────────────────────────────────
def _sod(ts: str) -> float:
    """seconds-of-day from 'HH:MM:SS.mmm'."""
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_closes(path):
    """Each COORD_CLOSE_START -> a trade instance (the real flat outcome)."""
    rx = re.compile(
        r"(\d\d:\d\d:\d\d\.\d+) .*COORD_CLOSE_START \| sym=(?P<sym>\S+) "
        r"pnl=(?P<pnl>[-+0-9.]+)% pnl\$=\S+ win=(?P<win>\w) by=(?P<by>\S+) "
        r"held=(?P<held>\d+)s ent=(?P<ent>[0-9.]+)"
    )
    out = []
    for line in open(path, errors="ignore"):
        m = rx.search(line)
        if not m:
            continue
        close_sod = _sod(m.group(1))
        held = int(m.group("held"))
        out.append({
            "sym": m.group("sym"),
            "flat_pnl": float(m.group("pnl")),
            "win": m.group("win") == "Y",
            "by": m.group("by"),
            "held": held,
            "entry": float(m.group("ent")),
            "close_sod": close_sod,
            "open_sod": close_sod - held,
        })
    return out


def parse_m4(path):
    """sym -> list of (sod, pnl, peak_pnl) from M4_DECISION (the realized path)."""
    rx = re.compile(
        r"(\d\d:\d\d:\d\d\.\d+) .*M4_DECISION \| sym=(?P<sym>\S+).*? "
        r"pnl=(?P<pnl>[-+0-9.]+)% peak_pnl=(?P<peak>[-+0-9.]+)%"
    )
    out = {}
    for line in open(path, errors="ignore"):
        m = rx.search(line)
        if not m:
            continue
        out.setdefault(m.group("sym"), []).append(
            (_sod(m.group(1)), float(m.group("pnl")), float(m.group("peak")))
        )
    return out


def parse_vol(path):
    """sym -> median atr_pct (R) and the last class seen."""
    rx = re.compile(r"VOL_PROFILE\w* \| sym=(?P<sym>\S+) class=(?P<cls>\w+) atr_pct=(?P<atr>[0-9.]+)%")
    acc = {}
    for line in open(path, errors="ignore"):
        m = rx.search(line)
        if not m:
            continue
        d = acc.setdefault(m.group("sym"), {"atr": [], "cls": "medium"})
        d["atr"].append(float(m.group("atr")))
        d["cls"] = m.group("cls")
    out = {}
    for sym, d in acc.items():
        vals = sorted(d["atr"])
        med = vals[len(vals) // 2] if vals else 0.0
        out[sym] = {"R": med, "cls": d["cls"]}
    return out


# ── the two geometries (pnl%-space; long/short symmetric in pnl terms) ─────
def flat_lock(peak):
    """Current ladder lock as a function of peak pnl% (the flat geometry)."""
    if peak < 0.10:           # below micro-floor arm
        return None
    if peak >= 0.6:           # crossed the first real rung
        return peak - 0.3 if peak - 0.3 > 0 else 0.05
    # dead band [arm, first rung): breakeven lock, lifted to fee-clearance
    lock = max(0.05, peak - 0.10)
    if peak >= 0.13:
        lock = max(lock, 0.13)
    return lock


def adaptive_lock(peak, R, trail_r=TRAIL_R, fee=FEE_FLOOR_PCT):
    """R-based staged lock + trail behind the peak, floored at the fee."""
    arm = max(ARM_R * R, fee)
    if peak < arm:
        return None
    trail = peak - trail_r * R           # trail_r behind the running peak
    staged = 0.0
    if peak >= RUNG_R[1] * R:            # >=3R: secure 1.5R
        staged = SECURE_AT_3R * R
    elif peak >= RUNG_R[0] * R:          # >=1.5R: break-even-plus (free roll)
        staged = fee
    return max(fee, trail, staged)


def walk(path_pts, R, trail_r=TRAIL_R):
    """Walk the (sod,pnl,peak) path; return the locked exit pnl% (gross).

    Tighten-only: the stop only ratchets up. If pnl falls to the resting stop,
    the trade exits there (the lock captured the gain). The caller appends the
    realized close as the final point so the trail is tested against the real
    give-back, never an optimistic mid-path value.
    """
    stop = None  # resting locked level in pnl%
    for _sod_, pnl, peak in path_pts:
        lk = adaptive_lock(peak, R, trail_r)
        if lk is not None:
            stop = lk if stop is None else max(stop, lk)  # ratchet
        if stop is not None and pnl <= stop:
            return stop  # gave back to the lock -> exit captured here
    return path_pts[-1][1] if path_pts else 0.0


def net(g):
    """Net of the round-trip fee."""
    return g - ROUND_TRIP_FEE_PCT


# ── real-gateway clamp-noop validation on the canonical trace ──────────────
def _build_gw(profit_lock_enabled):
    s = Settings._load_fresh()
    s.sl_gateway.owner_switch_enforce = False  # isolate the R2 clamp question
    s.sl_gateway.rate_limit_seconds = 0
    s.sl_gateway.r2_profit_lock_floor_enabled = profit_lock_enabled

    class P:
        async def get_position(self, x): return None
        async def set_stop_loss(self, x, v): return True
    class M:
        async def get_ticker(self, x): return None
    class E:
        def add_event(self, *a, **k): pass

    class VP:  # stub profiler -> the logged ALICEUSDT R and class
        async def get_profile(self, sym):
            return SimpleNamespace(atr_pct_5m=0.23, volatility_class="medium")

    return SLGateway(settings=s, position_service=P(), market_service=M(),
                     event_buffer=E(), volatility_profiler=VP())


def gateway_check():
    """Drive the canonical ALICEUSDT clamp-noop trace through the REAL gateway,
    exercising the explicit profit_lock_floor_price parameter (Commit 1).

    ALICEUSDT: long, entry 0.11166, price at the +0.16% peak, the ladder wants
    to lock +0.13% (just under price), the stop stuck at the +0.05% sliver.
    """
    loop = asyncio.get_event_loop()
    entry = 0.11166
    price = entry * (1 + 0.16 / 100)
    cur_sl = entry * (1 + 0.05 / 100)
    lock = entry * (1 + 0.13 / 100)

    def run(gw, sym, **extra):
        return loop.run_until_complete(gw.apply(
            symbol=sym, new_sl=lock, source="profit_sniper_ladder",
            direction="Buy", current_sl=cur_sl, current_price=price,
            entry_price=entry, bypass_step_cap_for_breakeven=True,
            bypass_rate_limit=True, **extra))

    # FLAT (current behavior): breakeven floor only, exemption irrelevant.
    flat = run(_build_gw(False), "ALICE_FLAT", breakeven_floor_price=entry)
    # OFF: pass the R-lock but the flag is off -> must stay inert (clamp-noop),
    # proving Commit 1 changes nothing in production until enabled.
    off = run(_build_gw(False), "ALICE_OFF", profit_lock_floor_price=lock)
    # ON: the explicit profit-lock floor, flag enabled -> the lock is held & writes.
    on = run(_build_gw(True), "ALICE_ON", profit_lock_floor_price=lock)
    return flat, off, on


# ── main ───────────────────────────────────────────────────────────────────
def main():
    asyncio.set_event_loop(asyncio.new_event_loop())
    closes = parse_closes(LOG)
    m4 = parse_m4(LOG)
    vol = parse_vol(LOG)

    print("ADAPTIVE EXIT REPLAY — R-based geometry vs flat, on the real logged trades")
    print(f"window: ALL_LOGS_2026-06-15_0230-0330_UTC.log | trades closed: {len(closes)} | "
          f"round-trip fee: {ROUND_TRIP_FEE_PCT}%\n")

    # base per-trade data (independent of the trail multiple)
    base = []
    for t in closes:
        sym = t["sym"]
        v = vol.get(sym, {"R": 0.0, "cls": "?"})
        R = v["R"]
        pts = [p for p in m4.get(sym, []) if t["open_sod"] - 5 <= p[0] <= t["close_sod"] + 5]
        peak = max([p[2] for p in pts], default=max(t["flat_pnl"], 0.0))
        # append the realized close so the trail is tested against the real
        # give-back, never an optimistic mid-path value.
        pts_full = (pts + [(t["close_sod"], t["flat_pnl"], peak)]) if pts else []
        base.append({"sym": sym, "R": R, "cls": v["cls"], "peak": peak,
                     "flat_net": net(t["flat_pnl"]), "flat_pnl": t["flat_pnl"],
                     "pts": pts_full, "cleared_fee": (peak >= FEE_FLOOR_PCT)})

    # sweep the trail multiple; for each, adaptive net per trade
    TRAILS = [1.0, 0.75, 0.5]
    sweep = {}
    for tr in TRAILS:
        for b in base:
            if b["R"] <= 0:
                b[f"adp_{tr}"] = b["flat_net"]
                continue
            adp_g = walk(b["pts"], b["R"], tr) if b["pts"] else b["flat_pnl"]
            b[f"adp_{tr}"] = net(adp_g)
        s = sum(b[f"adp_{tr}"] for b in base)
        w = sum(1 for b in base if b[f"adp_{tr}"] > 0)
        sweep[tr] = (w, s)
    best_tr = max(TRAILS, key=lambda tr: sweep[tr][1])

    n = len(base)
    flat_wins = sum(1 for b in base if b["flat_net"] > 0)
    flat_sum = sum(b["flat_net"] for b in base)

    print(f"Per trade at the best trail ({best_tr}R) "
          f"(sym | R | peak | flat net | adaptive net | delta | cleared fee):")
    for b in base:
        a = b[f"adp_{best_tr}"]
        print(f"  {b['sym']:<11} R={b['R']:.2f}% peak={b['peak']:+.3f}%  "
              f"flat={b['flat_net']:+.3f}%  adaptive={a:+.3f}%  delta={a-b['flat_net']:+.3f}%  "
              f"fee_cleared={'Y' if b['cleared_fee'] else 'N'}")

    print("\nTRAIL-MULTIPLE SWEEP (net of fee, the multiples are tuned here):")
    print(f"  FLAT (current):   win {flat_wins}/{n} = {100*flat_wins/n:.0f}%   net sum {flat_sum:+.3f}%")
    for tr in TRAILS:
        w, s = sweep[tr]
        star = "  <- best" if tr == best_tr else ""
        print(f"  adaptive {tr}R:    win {w}/{n} = {100*w/n:.0f}%   net sum {s:+.3f}%   "
              f"improvement {s-flat_sum:+.3f}%{star}")

    no_fee = [b for b in base if not b["cleared_fee"]]
    yes_fee = [b for b in base if b["cleared_fee"]]
    nf_flat = sum(b["flat_net"] for b in no_fee)
    yf_flat = sum(b["flat_net"] for b in yes_fee)
    yf_adp = sum(b[f"adp_{best_tr}"] for b in yes_fee)
    print(f"\n  >>> trades whose PEAK never cleared their own fee ({FEE_FLOOR_PCT}%): "
          f"{len(no_fee)}/{n} <<<")
    print(f"      {', '.join(b['sym'] for b in no_fee)}")
    print("      (no exit geometry can make these net-positive — this is the entry question.)")
    print("\nDECOMPOSITION (the exit fix vs the entry question):")
    print(f"  {len(no_fee)} unsaveable trades (peak < fee): flat {nf_flat:+.3f}%, adaptive {nf_flat:+.3f}% "
          f"(unchanged — the moves never cleared cost)")
    print(f"  {len(yes_fee)} fee-clearing trades: flat {yf_flat:+.3f}% -> adaptive {yf_adp:+.3f}% "
          f"(the give-back the exit fix recovers)")
    print(f"  => the exit fix turns the {len(yes_fee)} normal trades from {yf_flat:+.3f}% to {yf_adp:+.3f}%; "
          f"the residual loss is concentrated in the {len(no_fee)} trades the entries must fix.")

    # real-gateway clamp-noop validation
    print("\nREAL-GATEWAY VALIDATION (ALICEUSDT canonical clamp-noop, owner gate isolated):")
    flat, off, on = gateway_check()
    print(f"  FLAT  (breakeven-only hold):           accepted={flat.accepted} reason={flat.reason}")
    print(f"  OFF   (R-lock passed, flag disabled):  accepted={off.accepted} reason={off.reason}")
    print(f"  ON    (R-lock held, flag enabled):     accepted={on.accepted} reason={on.reason}")
    ok = (not flat.accepted) and (not off.accepted) and on.accepted
    print(f"  => {'CONFIRMED' if ok else 'NOT CONFIRMED'}: the R-lock writes only when the "
          f"exemption is enabled; flag-off and breakeven-only both clamp-noop (Commit 1 inert by default).")


if __name__ == "__main__":
    main()
