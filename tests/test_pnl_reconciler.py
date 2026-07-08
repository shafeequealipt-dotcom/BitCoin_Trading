"""Focused tests for the PnL reconciler (Phase 1D, 2026-06-07).

Verifies the indexer-lag safety net: a provisionally-booked close is captured,
retried against the exchange, and on a match the corrected net is fanned out
via the reconcile channel — never re-firing the non-idempotent consumers, and
correctly skipping authoritative and shadow closes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.trade_coordinator import TradeCoordinator
from src.workers.pnl_reconciler import PnLReconciler


def _settings(max_attempts: int = 3, exit_div_pct: float = 3.0) -> SimpleNamespace:
    return SimpleNamespace(
        bybit_demo=SimpleNamespace(
            enabled=True,
            close_pnl_reconcile=True,
            close_pnl_provisional=True,
            close_pnl_reconcile_interval_s=1.0,
            close_pnl_reconcile_max_attempts=max_attempts,
            close_pnl_reconcile_total_budget_s=30.0,
            close_pnl_reconcile_max_exit_divergence_pct=exit_div_pct,
        ),
        workers=SimpleNamespace(max_consecutive_failures=5, restart_delay=1.0),
    )


class _FakeCoord:
    def __init__(self, resolve_result):
        self._resolve_result = resolve_result
        self.fired: list[dict] = []
        self.close_cbs: list = []

    def register_close_callback(self, cb):
        self.close_cbs.append(cb)

    def fire_reconcile(self, rec):
        self.fired.append(rec)

    async def reresolve_close_pnl(self, symbol, **kw):
        return self._resolve_result


def _prov_record() -> dict:
    # a fallback-booked close that looks like a win locally
    return {
        "symbol": "DOGEUSDT", "trade_id": "t-1", "order_id": "oid-1",
        "size": 100.0, "pnl_usd": 5.0, "pnl_pct": 0.06, "was_win": True,
        "price_source": "local_fallback_stale", "exchange_mode": "bybit_demo",
    }


def test_fire_reconcile_only_hits_reconcile_channel():
    """The reconcile channel is isolated from the normal close channel."""
    coord = TradeCoordinator()
    recon_hits: list = []
    close_hits: list = []
    coord.register_reconcile_callback(lambda r: recon_hits.append(r))
    coord.register_close_callback(lambda r: close_hits.append(r))
    coord.fire_reconcile({"symbol": "X", "trade_id": "t"})
    assert len(recon_hits) == 1
    assert len(close_hits) == 0  # close channel NOT fired by reconcile


@pytest.mark.asyncio
async def test_capture_then_reconcile_books_exchange_net():
    """A provisional win is captured and corrected to the exchange loss."""
    coord = _FakeCoord((-4.36, -0.0616, "exchange_authoritative", 0.08125))
    recon = PnLReconciler(_settings(), db=None, services={"trade_coordinator": coord})
    assert recon._registered  # capture callback registered

    recon._capture(_prov_record())
    assert len(recon._jobs) == 1

    await recon.tick()
    assert len(coord.fired) == 1
    corrected = coord.fired[0]
    assert corrected["pnl_usd"] == pytest.approx(-4.36)   # exchange net booked
    assert corrected["was_win"] is False
    assert corrected["price_source"] == "exchange_authoritative_reconciled"
    assert not recon._jobs  # job consumed


@pytest.mark.asyncio
async def test_capture_skips_authoritative_and_shadow():
    coord = _FakeCoord((0.0, 0.0, "exchange_authoritative", None))
    recon = PnLReconciler(_settings(), db=None, services={"trade_coordinator": coord})

    already = _prov_record(); already["price_source"] = "exchange_authoritative"
    recon._capture(already)
    shadow = _prov_record(); shadow["exchange_mode"] = "shadow"
    recon._capture(shadow)
    assert len(recon._jobs) == 0  # neither enqueued


@pytest.mark.asyncio
async def test_reconcile_exhausts_without_double_book():
    """If the exchange row never indexes, the job is dropped after max attempts
    and the reconcile channel is never fired (provisional value kept)."""
    coord = _FakeCoord((5.0, 0.06, "local_fallback", None))
    recon = PnLReconciler(_settings(max_attempts=3), db=None,
                          services={"trade_coordinator": coord})
    recon._capture(_prov_record())
    for _ in range(3):
        await recon.tick()
    assert len(coord.fired) == 0   # never reconciled
    assert len(recon._jobs) == 0   # dropped after budget


# ── Phase 1 residual fix (2026-06-08): reconcile exit-plausibility gate ──
# Proven live: a NEAR reconcile resolved an out-of-band exit (2.3379 vs the
# trade's ~2.07 close) and flipped a -$75.83 loss into a +$18.52 phantom win,
# because the reconcile channel bypasses the on_trade_closed staleness gate and
# the resolver's qty gate is inert once the trade state is popped.


@pytest.mark.asyncio
async def test_reconcile_rejects_stale_out_of_band_exit():
    """A resolved row whose exit is implausible vs the provisional close's exit
    is rejected (the NEAR phantom): the provisional is kept, never booked."""
    coord = _FakeCoord((18.52, 0.39, "exchange_authoritative", 2.3379))
    recon = PnLReconciler(_settings(max_attempts=3), db=None,
                          services={"trade_coordinator": coord})
    rec = _prov_record()
    rec["symbol"] = "NEARUSDT"
    rec["close_price"] = 2.07          # the real close (a ~cap loss)
    rec["pnl_usd"] = -75.83            # provisional loss (right sign)
    recon._capture(rec)
    for _ in range(3):                  # retries keep getting the stale row
        await recon.tick()
    assert len(coord.fired) == 0       # phantom NEVER booked
    assert len(recon._jobs) == 0       # exhausted; provisional kept


@pytest.mark.asyncio
async def test_reconcile_accepts_fee_flip_same_exit():
    """A fee-driven sign flip keeps the same exit price, so it passes the gate
    and the legitimate exchange net is still booked (the ETH case)."""
    coord = _FakeCoord((-4.93, -0.018, "exchange_authoritative", 1685.97))
    recon = PnLReconciler(_settings(), db=None,
                          services={"trade_coordinator": coord})
    rec = _prov_record()
    rec["symbol"] = "ETHUSDT"
    rec["close_price"] = 1685.0        # provisional exit ~= resolved exit
    rec["pnl_usd"] = 3.87              # provisional gross win
    recon._capture(rec)
    await recon.tick()
    assert len(coord.fired) == 1       # legitimate correction kept
    assert coord.fired[0]["pnl_usd"] == pytest.approx(-4.93)
    assert coord.fired[0]["was_win"] is False


@pytest.mark.asyncio
async def test_gate_disabled_books_anything():
    """exit_div_pct=0 disables the gate (off-switch / back-compat)."""
    coord = _FakeCoord((18.52, 0.39, "exchange_authoritative", 2.3379))
    recon = PnLReconciler(_settings(exit_div_pct=0.0), db=None,
                          services={"trade_coordinator": coord})
    rec = _prov_record()
    rec["close_price"] = 2.07
    recon._capture(rec)
    await recon.tick()
    assert len(coord.fired) == 1       # no gate → stale exit booked
