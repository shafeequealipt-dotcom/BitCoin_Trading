"""C2 self-verification — SL Gateway clamp-and-apply (Profit-Fetching restoration).

Confirms with concrete values that the gateway now CLAMPS-and-applies instead of
rejecting wholesale, so a protective stop actually moves. Reproduces the live
failure cases:

  1. BSBUSDT-style frozen trail: a long whose trail wants a >0.25% step now
     advances exactly max_step_pct toward price (R3 clamp) instead of freezing.
  2. Too-close trail: a long whose trail lands inside the min-distance now sits
     exactly at the min-distance boundary (R2 clamp) instead of being rejected.
  3. NEAR-style wrong-side ladder: a long whose ladder floor lands ABOVE price
     after a fast retrace is clamped to the highest valid stop just below price
     and WIRED (no SL_GATEWAY_WIRE_FAIL re-spam) instead of rejected.
  4. No-op: when no valid stop improves on the current SL, the gateway holds the
     current SL and does NOT wire (stops the re-spam loop).
  5. Tighten-only (R1) still rejects a loosening move.
  6. A normal valid tighten passes through unchanged (no clamp).

Run: .venv/bin/python verify_c2_gateway_clamp.py
Does not touch any data; uses in-memory stubs.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.core.sl_gateway import (
    SLGateway,
    REASON_LOOSENING,
    REASON_CLAMP_NOOP,
)


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        log_only_global=False,
        min_distance_pct=0.3,
        max_step_pct=0.25,
        rate_limit_seconds=30,
        log_only_tighten_only=False,
        log_only_min_distance=False,
        log_only_max_step=False,
        log_only_rate_limit=False,
        min_distance_atr_multiplier=0.5,
        min_distance_abs_floor_pct=0.05,
    )


class _PosSvc:
    def __init__(self, sl: float | None) -> None:
        self._sl = sl
        self.wired: list[float] = []

    async def get_position(self, symbol: str):
        return SimpleNamespace(stop_loss=self._sl)

    async def set_stop_loss(self, symbol: str, new_sl: float) -> bool:
        self.wired.append(new_sl)
        return True


class _MktSvc:
    def __init__(self, price: float) -> None:
        self._price = price

    async def get_ticker(self, symbol: str):
        return SimpleNamespace(last_price=self._price)


def _gw(price: float, sl: float | None):
    pos = _PosSvc(sl)
    settings = SimpleNamespace(sl_gateway=_cfg())
    gw = SLGateway(
        settings=settings,
        position_service=pos,
        market_service=_MktSvc(price),
        event_buffer=None,
        volatility_profiler=None,  # eff_min falls back to min_distance_pct=0.3%
    )
    return gw, pos


PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, PASS if ok else FAIL, detail))


async def main() -> int:
    # ── Case 1: BSBUSDT frozen trail — long, current_sl far below price,
    # trail proposes a big upward step (>0.25%). Expect R3 clamp to exactly
    # current_sl * 1.0025 and wire it.
    price = 0.9000
    cur = 0.8800  # current SL ~2.2% below price
    raw = 0.8980  # trail wants +2.05% step from cur -> step_exceeded pre-fix
    gw, pos = _gw(price, cur)
    r = await gw.apply(
        symbol="BSBUSDT", new_sl=raw, source="profit_sniper_trail",
        direction="Buy", current_sl=cur, current_price=price,
    )
    expect = round(cur * (1.0 + 0.25 / 100.0), 8)  # 0.8822
    ok = r.accepted and pos.wired and abs(pos.wired[-1] - expect) < 1e-6
    check("1 R3 clamp advances frozen trail",
          ok, f"accepted={r.accepted} wired={pos.wired} expect~{expect}")

    # ── Case 2: too-close trail — long, trail lands inside the 0.3% min-dist.
    # current_sl below price but proposed stop within 0.3% of price. Expect R2
    # clamp to price*(1-0.003) and wire (it is a tighten vs current_sl).
    price = 0.9000
    cur = 0.8900  # 1.11% below price (valid, room to tighten)
    raw = 0.8995  # only 0.056% below price -> too_close pre-fix
    gw, pos = _gw(price, cur)
    r = await gw.apply(
        symbol="ARBUSDT", new_sl=raw, source="profit_sniper_trail",
        direction="Buy", current_sl=cur, current_price=price,
    )
    boundary = round(price * (1.0 - 0.3 / 100.0), 8)  # 0.89730
    # R3 then caps the step from cur (0.89 -> 0.8973 = 0.82% > 0.25%) to
    # cur*1.0025 = 0.892225, which is the binding constraint here.
    r3cap = round(cur * (1.0 + 0.25 / 100.0), 8)
    final = min(boundary, r3cap)
    ok = r.accepted and pos.wired and abs(pos.wired[-1] - final) < 1e-6
    check("2 R2/R3 clamp applies valid tighten",
          ok, f"accepted={r.accepted} wired={pos.wired} expect~{final} "
              f"(r2_boundary={boundary} r3cap={r3cap})")

    # ── Case 3: NEAR wrong-side ladder — long, ladder floor ABOVE price after
    # a retrace, with the breakeven R3 bypass. Expect R2 clamp DOWN to just
    # below price and WIRE a valid stop (no wire-fail, no re-spam).
    price = 2.5244
    cur = 2.4932  # initial loss-cap below entry
    raw = 2.5483  # ladder floor lands ABOVE price (wrong side) pre-fix
    gw, pos = _gw(price, cur)
    r = await gw.apply(
        symbol="NEARUSDT", new_sl=raw, source="profit_sniper_ladder",
        direction="Buy", current_sl=cur, current_price=price,
        bypass_step_cap_for_breakeven=True,  # ladder is an R3-bypass source
    )
    boundary = round(price * (1.0 - 0.3 / 100.0), 8)  # 2.5168 (just below price)
    ok = (r.accepted and pos.wired
          and abs(pos.wired[-1] - boundary) < 1e-6
          and pos.wired[-1] < price)  # the key: a VALID, wireable stop
    check("3 NEAR wrong-side ladder -> highest valid stop",
          ok, f"accepted={r.accepted} wired={pos.wired} expect~{boundary} "
              f"below_price={pos.wired and pos.wired[-1] < price}")

    # ── Case 4: no-op — long, current_sl already at/above the R2 boundary
    # (no valid stop improves on it). Expect REASON_CLAMP_NOOP and NO wire.
    price = 0.9000
    cur = 0.8990  # already 0.11% below price (inside the 0.3% min-dist)
    raw = 0.8995  # even closer; clamps to boundary 0.8973 which LOOSENS vs cur
    gw, pos = _gw(price, cur)
    r = await gw.apply(
        symbol="SEIUSDT", new_sl=raw, source="profit_sniper_trail",
        direction="Buy", current_sl=cur, current_price=price,
    )
    ok = (not r.accepted) and r.reason == REASON_CLAMP_NOOP and not pos.wired
    check("4 no-op holds current SL, no wire",
          ok, f"accepted={r.accepted} reason={r.reason} wired={pos.wired}")

    # ── Case 5: tighten-only (R1) still rejects loosening.
    price = 0.9000
    cur = 0.8900
    raw = 0.8800  # below current SL -> loosening for a long
    gw, pos = _gw(price, cur)
    r = await gw.apply(
        symbol="LOOSEUSDT", new_sl=raw, source="profit_sniper_trail",
        direction="Buy", current_sl=cur, current_price=price,
    )
    ok = (not r.accepted) and r.reason == REASON_LOOSENING and not pos.wired
    check("5 R1 tighten-only still rejects loosening",
          ok, f"accepted={r.accepted} reason={r.reason} wired={pos.wired}")

    # ── Case 6: a normal valid tighten passes unchanged (no clamp).
    price = 0.9000
    cur = 0.8900
    raw = 0.8915  # +0.17% step, 0.94% below price -> valid on both rules
    gw, pos = _gw(price, cur)
    r = await gw.apply(
        symbol="OKUSDT", new_sl=raw, source="profit_sniper_trail",
        direction="Buy", current_sl=cur, current_price=price,
    )
    ok = r.accepted and pos.wired and abs(pos.wired[-1] - raw) < 1e-9
    check("6 valid tighten passes unchanged",
          ok, f"accepted={r.accepted} wired={pos.wired} expect={raw}")

    # ── Case 7: short-side R3 clamp (symmetry).
    price = 100.0
    cur = 102.0  # SL above price (short), 2% away
    raw = 100.3  # wants -1.67% step from cur -> step_exceeded pre-fix
    gw, pos = _gw(price, cur)
    r = await gw.apply(
        symbol="SHORTUSDT", new_sl=raw, source="profit_sniper_trail",
        direction="Sell", current_sl=cur, current_price=price,
    )
    expect = round(cur * (1.0 - 0.25 / 100.0), 8)  # 101.745
    ok = r.accepted and pos.wired and abs(pos.wired[-1] - expect) < 1e-6
    check("7 short R3 clamp tightens downward",
          ok, f"accepted={r.accepted} wired={pos.wired} expect~{expect}")

    print("\nC2 GATEWAY CLAMP — SELF-VERIFICATION\n")
    n_pass = 0
    for name, status, detail in results:
        print(f"  [{status}] {name}")
        print(f"         {detail}")
        if status == PASS:
            n_pass += 1
    print(f"\n  {n_pass}/{len(results)} checks passed\n")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
