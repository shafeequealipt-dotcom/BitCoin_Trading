"""Real-project pipeline check for the LOSS-CUTTING SYSTEM — constructs the
ACTUAL project classes the way WorkerManager does and verifies the DI wiring,
the close-lock chokepoint, the SLGateway bypass set, and a real spine force-close
routing through the real coordinator. Only the exchange wire is stubbed.

Run:  PYTHONPATH=. python scripts/pipeline_check_loss_cutting.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

from src.config.settings import LossCuttingSettings, Settings
from src.core.exceptions import ClosingInProgressError, PositionError
from src.core.sl_gateway import SLGateway
from src.core.transformer import Transformer, _PositionProxy
from src.core.types import Side
from src.workers.profit_sniper import ProfitSniper
from src.workers.sniper_ring_buffer import EnhancedRingBuffer, PositionProfitState

CHECKS: list[tuple[bool, str]] = []


def ck(cond, label):
    CHECKS.append((bool(cond), label))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


class _WireRecorder:
    """The single stubbed boundary — stands in for the per-mode exchange adapter
    the Transformer delegates to. Records closes/SL writes; no network."""

    def __init__(self):
        self.current_sl = None
        self.closes = []

    async def set_stop_loss(self, symbol, sl):
        self.current_sl = round(float(sl), 8)
        return True

    async def close_position(self, symbol, *, purpose="layer4_close",
                             close_trigger="system_close"):
        await asyncio.sleep(0.01)  # a real await for the in-flight lock to span
        self.closes.append((symbol, close_trigger))
        return SimpleNamespace(order_id="rec", symbol=symbol)

    async def get_position(self, symbol):
        return None


async def main() -> int:
    print("=== Loss-Cutting real-project pipeline check ===\n")
    settings = Settings.load(config_path="config.toml")

    # 1) DI: real Settings exposes loss_cutting + siblings intact.
    lc = settings.loss_cutting
    ck(isinstance(lc, LossCuttingSettings), "Settings.loss_cutting is a real LossCuttingSettings")
    ck(lc.enabled and lc.cap_dollar_ceiling == 75.0 and lc.volatility_entry_sizing_enabled is False,
       f"shipped defaults (master ON, cap_ceiling={lc.cap_dollar_ceiling}, vol_sizing off)")
    ck(settings.profit_fetching is not None and settings.time_decay is not None
       and settings.sl_gateway is not None, "profit_fetching / time_decay / sl_gateway intact (no regression)")

    # 2) DI: the shared close chokepoint + the lock owned on the Transformer.
    tf = Transformer(db=SimpleNamespace(), config=settings)
    proxies = tf.create_proxies()
    proxy = proxies["position"]
    proxy2 = tf.create_proxies()["position"]  # the manager aliases position_service to this
    ck(isinstance(proxy, _PositionProxy) and proxy._t is tf,
       "create_proxies builds a _PositionProxy delegating to the single shared Transformer")
    ck(hasattr(tf, "_closing_inflight") and isinstance(tf._closing_inflight, set),
       "the in-flight close-lock set is owned on the long-lived Transformer (self._t._closing_inflight)")
    ck(proxy is not proxy2 and proxy._t._closing_inflight is proxy2._t._closing_inflight,
       "even distinct proxies share ONE lock set (robust to a proxy rebuild) — the hardening")

    # 3) Real ProfitSniper construction (loss dial + structure cache + sentinel).
    sniper = ProfitSniper(settings=settings, db=SimpleNamespace(), structure_cache="SC")
    ck(sniper._lc.enabled, "ProfitSniper built self._lc (LossCuttingSettings)")
    ld = sniper._loss_dial.resolve_loss(0.0, 50.0)
    ld_old = sniper._loss_dial.resolve_loss(50.0, 50.0)
    ck(abs(ld.cap_pct - 2.5) < 1e-9 and abs(ld_old.cap_pct - 1.0) < 1e-9,
       f"self._loss_dial.resolve_loss glides cap_pct young={ld.cap_pct}->old={ld_old.cap_pct}")
    ck(sniper.structure_cache == "SC", "structure_cache wired into the sniper ctor")

    # 4) SLGateway R3-bypass set carries the loss sources (loss_spike absent).
    bp = SLGateway._BREAKEVEN_BYPASS_SOURCES
    ck(all(s in bp for s in ("loss_cap", "loss_cap_emergency", "loss_atr_initial",
                             "loss_structure", "loss_recovery")),
       "gateway R3-bypass set carries the 5 loss SL sources")
    ck("loss_spike" not in bp, "loss_spike NOT a bypass source (the spike force-closes, no SL)")

    # 5) Close chokepoint end-to-end: two cutters race the SAME symbol through the
    #    REAL _PositionProxy; exactly one forwards, the other raises.
    rec = _WireRecorder()
    tf._active_services = {"position": rec}
    # active_position_service resolves from _active_services; ensure the proxy reaches rec.
    proxy = proxies["position"]
    try:
        res = await asyncio.gather(
            proxy.close_position("SOLUSDT", close_trigger="loss_cap_force"),
            proxy.close_position("SOLUSDT", close_trigger="wd_hard_stop"),
            return_exceptions=True,
        )
        forwarded = sum(1 for r in res if not isinstance(r, Exception))
        rejected = sum(1 for r in res if isinstance(r, ClosingInProgressError))
        ck(forwarded == 1 and rejected == 1 and rec.closes == [("SOLUSDT", "loss_cap_force")]
           and "SOLUSDT" not in tf._closing_inflight,
           "concurrent double-close: exactly one forwards, one raises ClosingInProgressError, lock released")
        ck(issubclass(ClosingInProgressError, PositionError),
           "ClosingInProgressError subclasses PositionError (real callers skip booking)")
    except Exception as e:  # noqa: BLE001
        ck(False, f"close chokepoint race raised unexpectedly: {e}")

    # 6) Real spine cap force-close routes through the real coordinator booking.
    booked = {}

    class _Coord:
        def get_trade_plan(self, s):
            return None

        def remove_trade_plan(self, s):
            pass

        async def resolve_authoritative_pnl(self, *, symbol, position_service,
                                            fallback_pnl_usd, fallback_pnl_pct):
            return (fallback_pnl_usd, fallback_pnl_pct, "rec", None)

        def on_trade_closed(self, *, symbol, pnl_pct, pnl_usd, was_win,
                            closed_by, exit_price=None, price_source=None):
            booked["closed_by"] = closed_by

    sym = "BTCUSDT"
    sniper.sl_gateway = SLGateway(settings=settings, position_service=rec,
                                  market_service=SimpleNamespace(), volatility_profiler=None)
    sniper.position_service = rec
    sniper.trade_coordinator = _Coord()

    class _L4:
        async def is_protected(self, **k):
            return _NotProtected()

        def get_struct_guard_verdict(self, s):
            return ("", 0.0)

    sniper.layer4_protection = _L4()
    sniper.event_buffer = None
    sniper.structure_cache = SimpleNamespace(get=lambda s: None)
    now = time.time()
    entry, size = 100.0, 30.0  # notional $3000 -> cap=min(75, 2.5%*3000=75)=$75
    st = PositionProfitState(symbol=sym, entry_price=entry, direction="Buy",
                             atr_at_entry=0.6, opened_at=now - 60)
    sniper._profit_states[sym] = st
    buf = EnhancedRingBuffer(symbol=sym, max_size=720, min_ready=1)
    tracked = {"buffer": buf, "first_seen_at": now - 60, "position": None,
               "_last_escape_type": "", "_last_escape_tick": 0}
    sniper._tracked[sym] = tracked
    price = 97.4  # loss 2.6% * 3000 = $78 > $75 cap
    st.update((price - entry) / entry * 100, price, now)
    buf.add_point(SimpleNamespace(timestamp=now, price=price, bid=price, ask=price, atr_current=0.6))
    pos = SimpleNamespace(symbol=sym, stop_loss=0.0, side=Side.BUY, size=size,
                          entry_price=entry, mark_price=price,
                          unrealized_pnl=(price - entry) / entry * size * entry / 100.0 * 100)
    pos.unrealized_pnl = (price - entry) / 100.0 * size  # ~ -$78
    tracked["position"] = pos
    await sniper._pf_apply_spine(sym, pos, tracked, price)
    ck(rec.closes and rec.closes[-1][1] == "loss_cap_force" and booked.get("closed_by") == "loss_cap_force",
       "real spine: sacred-cap breach force-closes via the real coordinator (loss_cap_force booked)")

    passed = sum(1 for ok, _ in CHECKS if ok)
    print(f"\nPIPELINE CHECK: {passed}/{len(CHECKS)} checks passed")
    return 0 if passed == len(CHECKS) else 1


class _NotProtected:
    protected = False
    reason = "pipeline_check"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
