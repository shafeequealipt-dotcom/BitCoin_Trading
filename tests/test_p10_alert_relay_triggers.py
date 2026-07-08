"""P10 — verify the audit-flagged silent BYBIT_DEMO_* tags are now in
the alert-relay trigger table.
"""

from __future__ import annotations

from src.core.types import AlertLevel
from src.observability.bybit_demo_alert_relay import _TRIGGERS


def test_p10_critical_tags_present_with_correct_severity() -> None:
    """The 8 newly-added trigger tags + BYBIT_DEMO_WS_DEAD."""
    expected_critical = {
        "BYBIT_DEMO_INSUFFICIENT_BALANCE",
        "BYBIT_DEMO_SET_SL_FAIL",
        "BYBIT_DEMO_CLOSE_REJECT",
        "BYBIT_DEMO_WALLET_FAIL",
        "BYBIT_DEMO_WS_DEAD",
    }
    expected_warning = {
        "BYBIT_DEMO_ORDER_REJECT",
        "BYBIT_DEMO_SET_TP_FAIL",
        "BYBIT_DEMO_LEVERAGE_FAIL",
        "REDUCE_FALLBACK",
    }
    for tag in expected_critical:
        assert tag in _TRIGGERS, f"missing CRITICAL trigger {tag}"
        assert _TRIGGERS[tag].level == AlertLevel.CRITICAL, (
            f"{tag} should be CRITICAL"
        )
    for tag in expected_warning:
        assert tag in _TRIGGERS, f"missing WARNING trigger {tag}"
        assert _TRIGGERS[tag].level == AlertLevel.WARNING, (
            f"{tag} should be WARNING"
        )


def test_p10_partial_fill_intentionally_not_in_triggers() -> None:
    """PARTIAL_FILL is normal market behaviour on illiquid pairs and
    logs at INFO. The relay filters at WARNING+ so it would never
    fire anyway; documented exclusion to prevent future addition."""
    assert "BYBIT_DEMO_PARTIAL_FILL" not in _TRIGGERS
