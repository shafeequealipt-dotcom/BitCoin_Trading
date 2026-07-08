"""Layer 4 Realignment Phase 4.4 — sniper consults protection service.

Smoke tests for the Layer4ProtectionService hookup at the entry of
``ProfitSniper._execute_full_close`` and ``_execute_partial_close``.
Verifies:
- Service-blocked close emits SNIPER_PROTECTED and returns False.
- Service-allowed close proceeds to the underlying position service.
- Service-unwired path fails loud (SNIPER_PROTECTION_SERVICE_UNWIRED
  ERROR log + False return), preserving fail-safe semantics.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.risk.layer4_protection import ProtectionResult
from src.workers.profit_sniper import ProfitSniper


def _make_sniper(*, protection_service: object | None) -> ProfitSniper:
    """Build a minimal ProfitSniper with the protection service slot
    set. Mirrors the ``__new__`` pattern used elsewhere in the suite."""
    sw = ProfitSniper.__new__(ProfitSniper)
    sw.settings = MagicMock()
    sw.position_service = MagicMock()
    sw.position_service.close_position = AsyncMock()
    sw.position_service.reduce_position = AsyncMock(
        return_value=MagicMock(qty=10, filled_qty=10),
    )
    sw.event_buffer = None
    sw.trade_coordinator = None
    sw.layer4_protection = protection_service
    return sw


def _pos() -> MagicMock:
    p = MagicMock()
    p.symbol = "ETHUSDT"
    p.side = "Buy"
    p.size = 10
    p.unrealized_pnl = 0.0
    return p


def test_full_close_blocked_when_service_returns_protected() -> None:
    """Service.is_protected returns protected=True → close blocked,
    underlying position_service NOT called."""
    svc = MagicMock()
    svc.is_protected = AsyncMock(return_value=ProtectionResult(
        protected=True,
        reason="min_hold:age=120s<300s",
        evidence={"age_seconds": 120.0},
    ))
    sw = _make_sniper(protection_service=svc)
    pos = _pos()

    result = asyncio.run(sw._execute_full_close(
        symbol="ETHUSDT",
        pos=pos,
        score_data={"exploit_score": 80, "pnl_pct": -0.4},
        closed_by="mode4_p9",
    ))
    assert result is False, "blocked close must return False"
    sw.position_service.close_position.assert_not_called()
    svc.is_protected.assert_awaited_once()


def test_full_close_proceeds_when_service_returns_unprotected() -> None:
    """Service returns protected=False → close proceeds normally."""
    svc = MagicMock()
    svc.is_protected = AsyncMock(return_value=ProtectionResult(
        protected=False,
        reason="no_protection",
        evidence={},
    ))
    sw = _make_sniper(protection_service=svc)
    pos = _pos()

    asyncio.run(sw._execute_full_close(
        symbol="ETHUSDT",
        pos=pos,
        score_data={"exploit_score": 80, "pnl_pct": -1.0},
        closed_by="mode4_p9",
    ))
    # Phase 12.7 (lifecycle-logging-audit Gap 7.4-G1): close_position is
    # now called with a close_trigger= kwarg that propagates to BybitDemo's
    # BYBIT_DEMO_POSITION_CLOSE log for source attribution. Sniper passes
    # the closed_by string as the trigger.
    sw.position_service.close_position.assert_awaited_once_with(
        "ETHUSDT", close_trigger="mode4_p9",
    )


def test_full_close_fails_loud_when_service_unwired() -> None:
    """Service is None → method returns False without calling
    position_service. The SNIPER_PROTECTION_SERVICE_UNWIRED ERROR log
    is the operator-visible signal that DI is broken."""
    sw = _make_sniper(protection_service=None)
    pos = _pos()

    result = asyncio.run(sw._execute_full_close(
        symbol="ETHUSDT",
        pos=pos,
        score_data={"exploit_score": 80, "pnl_pct": -0.4},
        closed_by="mode4_p9",
    ))
    assert result is False, "unwired service must fail-loud + fail-safe"
    sw.position_service.close_position.assert_not_called()


def test_partial_close_consults_service_with_partial_reason() -> None:
    """Partial close path also consults service with close_reason=
    'mode4_partial' so service logs distinguish partial blocks from
    full blocks."""
    svc = MagicMock()
    svc.is_protected = AsyncMock(return_value=ProtectionResult(
        protected=True,
        reason="min_hold:age=200s<300s",
        evidence={"age_seconds": 200.0},
    ))
    sw = _make_sniper(protection_service=svc)
    pos = _pos()

    result = asyncio.run(sw._execute_partial_close(
        symbol="ETHUSDT",
        pos=pos,
        close_pct=50,
        score_data={"exploit_score": 70, "pnl_pct": -0.4},
    ))
    assert result is False
    sw.position_service.reduce_position.assert_not_called()
    # Verify the service was called with the partial-specific reason
    call_kwargs = svc.is_protected.call_args.kwargs
    assert call_kwargs["close_reason"] == "mode4_partial"
    assert call_kwargs["check_min_hold"] is True
    assert call_kwargs["check_profit"] is False
    assert call_kwargs["check_structural"] is False
