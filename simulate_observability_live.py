#!/usr/bin/env python3
"""Live-like simulation of both observability instruments against the real
project classes, recreating the 'clipped winner' the prior exit analysis found.

It proves each instrument does what its AIM requires:

  System 2 (price path): a long trade spikes to +0.80% intrabar (at a second OFF
    the 5-second grid) then gives it back to ~breakeven and closes. The
    per-second logger captures the +0.80% peak and the full giveback shape;
    a 5-second sampling of the SAME path misses the spike (sees only ~+0.55%) —
    which is exactly why the exit calibration needs per-second resolution. The
    captured path is read back from a real rotated price_path.log.

  System 1 (prompt capture): a realistic Call-A (multi-coin candidate blocks)
    and Call-B (manage a position) are captured; a coin's data is reconstructed
    from the captured prompt and matched to what the brain was shown; the
    hourly retention sweep then bounds the directory.

Observability-only and self-contained: temp dirs only, never the real
price_path.log / stage2_dumps / trading DB / protected tables.

Usage:  .venv/bin/python simulate_observability_live.py
"""
import os
import re
import tempfile
import time as RT

import src.workers.price_path_logger as M
import src.brain.claude_code_client as cc
from src.config.settings import Settings
from src.core.trade_coordinator import TradeCoordinator
from src.workers.price_worker import PriceWorker
from src.core.logging import setup_logging, get_logger

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))


class Clock:
    def __init__(self, t):
        self.t = t
    def time(self):
        return self.t
    def monotonic(self):
        return self.t


# Realistic clipped-winner pnl path for a LONG, by second offset 0..40.
def build_pnl_path():
    pnl = {}
    for t in range(0, 21):
        pnl[t] = round(0.55 * t / 20.0, 4)          # rise to +0.55% at t=20 (on grid)
    pnl[21] = 0.68
    pnl[22] = 0.80                                   # SHARP intrabar peak, OFF the 5s grid
    pnl[23] = 0.66
    pnl[24] = 0.52
    for t in range(25, 41):                          # decline +0.50% -> +0.05%
        pnl[t] = round(0.50 + (0.05 - 0.50) * (t - 25) / 15.0, 4)
    return pnl


def main():
    tmp = tempfile.mkdtemp(prefix="sim_obs_")
    logdir = os.path.join(tmp, "logs")
    os.makedirs(logdir)
    # Route loguru to the temp dir so price points land in a REAL rotated
    # price_path.log we can read back (the replay tool's actual source).
    setup_logging(log_level="INFO", log_dir=logdir)

    s = Settings.load("config.toml")
    obs = s.observability

    print("LIVE SIMULATION — observability instruments vs a clipped-winner trade")
    print("Temp dir:", tmp)

    # ───────────────────────── System 2 ─────────────────────────
    print("\nSystem 2 — per-second price path of a clipped winner (SOLUSDT long)")
    ENTRY = 100.0
    pnl_path = build_pnl_path()
    prices = {t: round(ENTRY * (1 + p / 100.0), 6) for t, p in pnl_path.items()}
    CLOSE_PRICE = round(ENTRY * (1 + 0.04 / 100.0), 6)   # closes at +0.04%

    coord = TradeCoordinator()
    pw = PriceWorker.__new__(PriceWorker)
    pw._ws_quotes = {}
    coord.register_trade(symbol="SOLUSDT", entry_price=ENTRY, side="Buy",
                         decision_id="d-sol-clip", size=10.0, order_id="o-sol")

    base = RT.monotonic() + 100000.0          # fake wall-clock for M.time/dedup
    clk = Clock(base)
    M.time = clk
    ppl = M.PricePathLogger(pw, coord, obs)
    coord.register_close_callback(ppl.on_trade_closed)

    # Drive one sample per simulated second across the trade's life.
    for t in range(0, 41):
        clk.t = base + t
        # WS quote timestamp uses the REAL monotonic so get_ws_quote sees it
        # as fresh (staleness is checked against price_worker's real clock).
        pw._ws_quotes["SOLUSDT"] = (prices[t], RT.monotonic())
        ppl._sample_once()
    ppl._flush_all()
    # Real close fan-out at the give-back price.
    coord.on_trade_closed("SOLUSDT", pnl_pct=0.04, pnl_usd=0.40, was_win=True,
                          exit_price=CLOSE_PRICE)

    RT.sleep(0.4)  # let enqueue=True background thread flush to disk
    pp_log = os.path.join(logdir, "price_path.log")
    txt = open(pp_log).read() if os.path.exists(pp_log) else ""
    pts = []
    for line in txt.splitlines():
        if "PRICE_PATH | " not in line or "sym=SOLUSDT" not in line:
            continue
        m_pnl = re.search(r"pnl=([+-][0-9.]+)%", line)
        m_px = re.search(r"px=([0-9.]+)", line)
        if m_pnl and m_px:
            pts.append((float(m_px.group(1)), float(m_pnl.group(1)),
                        "close=Y" in line))

    captured_peak = max((p for _, p, _ in pts), default=None)
    close_pts = [p for _, p, c in pts if c]
    # 5-second sampling of the SAME path (what the cheaper tap would have seen)
    grid_peak = max(pnl_path[t] for t in range(0, 41, 5))

    ok("price_path.log was created and round-tripped", bool(pts),
       f"{len(pts)} points")
    ok("per-second path captured the +0.80% intrabar peak",
       captured_peak is not None and abs(captured_peak - 0.80) < 1e-6,
       f"captured_peak={captured_peak}")
    ok("a 5-second sampling of the SAME path MISSES the spike (sees ~+0.55%)",
       grid_peak < 0.80, f"5s_peak={grid_peak} vs 1s_peak={captured_peak}")
    ok("final close point present (close=Y at the give-back price)",
       len(close_pts) >= 1 and any(abs(px - CLOSE_PRICE) < 1e-6 for px, _, c in pts if c),
       f"close_pnls={close_pts}")
    ok("path is complete entry->close (>=40 points, no big gaps)", len(pts) >= 40,
       f"{len(pts)} points")
    ok("trade dropped from coordinator after close",
       "SOLUSDT" not in coord.active_symbols())
    giveback = (captured_peak - 0.04) if captured_peak is not None else None
    print(f"  reconstructed: peak={captured_peak}% close~+0.04% giveback={giveback:.2f}% "
          f"(this is the clip the calibration must see; 5s would have shown only {grid_peak}%)")

    # ───────────────────────── System 1 ─────────────────────────
    print("\nSystem 1 — capture + audit a realistic Call-A and Call-B")
    dd = os.path.join(tmp, "stage2_dumps")
    os.makedirs(dd)
    cc.configure_brain_capture(True, dd)

    call_a_prompt = (
        "MARKET CONTEXT: BTC regime=trending, alt breadth=expanding.\n"
        "CANDIDATES:\n"
        "  SOLUSDT  entry=142.55  atr%=1.8  votes=4/5  state=breakout\n"
        "  ARBUSDT  entry=0.9123  atr%=2.4  votes=3/5  state=reclaim\n"
        "INSTRUCTION: return new_trades JSON.\n"
    )
    call_a_resp = '{"new_trades":[{"symbol":"SOLUSDT","side":"long","leverage":5,"size_usd":200}],"market_view":"risk-on"}'
    call_b_prompt = (
        "OPEN POSITIONS:\n"
        "  SOLUSDT long entry=142.55 cur=143.66 pnl=+0.78% peak=+0.80% age=22s thesis=breakout-continuation\n"
        "INSTRUCTION: return position_actions JSON.\n"
    )
    call_b_resp = '{"position_actions":{"SOLUSDT":{"action":"hold","reasoning":"thesis intact, let it run"}}}'

    cc._maybe_dump_call(101, call_a_prompt, "TRADE SYS PROMPT", call_a_resp, 18450.0, "ha", "call_a")
    cc._maybe_dump_call(102, call_b_prompt, "POSITION SYS PROMPT", call_b_resp, 9200.0, "hb", "call_b")

    import json
    files = os.listdir(dd)
    a_file = next((f for f in files if "_call_a_" in f), None)
    b_file = next((f for f in files if "_call_b_" in f), None)
    ok("Call-A captured and labelled", a_file is not None)
    ok("Call-B captured and labelled", b_file is not None)
    if a_file:
        rec = json.loads(open(os.path.join(dd, a_file)).read())
        # audit: reconstruct a coin's candidate data from the captured prompt
        ok("audit: SOLUSDT candidate (entry=142.55) reconstructable from capture",
           "SOLUSDT  entry=142.55" in rec["prompt"])
        ok("audit: ARBUSDT candidate present too (complete prompt, not a fragment)",
           "ARBUSDT  entry=0.9123" in rec["prompt"])
        ok("audit: full response captured (the brain's actual picks)",
           rec["response"] == call_a_resp and rec["call_type"] == "call_a")

    # retention sweep bounds the directory
    from src.workers.cleanup_worker import CleanupWorker

    class _Obs:
        capture_dir = dd
        capture_retention_days = 7
        capture_max_files = 1   # force the count cap to act

    class _S:
        observability = _Obs()
    cw = CleanupWorker.__new__(CleanupWorker)
    cw.settings = _S()
    cw._sweep_stage2_dumps()
    remaining = [f for f in os.listdir(dd) if f.endswith(".json")]
    ok("retention sweep bounds the directory (count cap enforced)",
       len(remaining) <= 1, f"{len(remaining)} json remain")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
        raise SystemExit(1)
    print("LIVE SIMULATION PASSED — both instruments behave per their aim")


if __name__ == "__main__":
    main()
