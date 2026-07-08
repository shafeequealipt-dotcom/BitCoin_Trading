"""Loguru sink that turns critical Bybit demo / exchange-switch log tags
into Telegram alerts via :class:`AlertManager`.

Why a sink and not direct AlertManager calls in adapter code:
  - Shadow's adapter contract has zero AlertManager calls — the
    BybitDemoAdapter mirrors that. Adding direct calls would diverge
    the two adapters' patterns and require future Shadow code to
    adopt the same pattern OR live with asymmetric coverage.
  - A sink keeps the alerting concern out of the trading code path.
    The trading code only logs; this module observes and dispatches.
  - The sink is reusable for Shadow later — change the filter to
    accept SHADOW_* tags too.

Why content-hash dedup is sufficient:
  - :class:`AlertManager` uses SHA256[:16] of the formatted message
    with a 5-minute window. Repeated identical errors (e.g., a retry
    storm under sustained auth failure) collapse to one alert per
    window. CRITICAL alerts bypass the rate limit but still dedup.

Lifecycle:
  - :meth:`register` adds a single sink to the loguru root logger.
  - :meth:`unregister` removes it (used in tests; production keeps it
    for the process lifetime).

Threading:
  - Loguru calls the sink synchronously (or on a worker thread when
    ``enqueue=True``). AlertManager methods are async, so dispatch
    uses :func:`asyncio.run_coroutine_threadsafe` against the event
    loop captured at registration time. Failures are caught and
    logged as ``BYBIT_DEMO_ALERT_RELAY_FAIL`` — never raise out of a
    sink (would break logging system-wide).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional

from loguru import logger as loguru_logger

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import AlertLevel

_log = get_logger("worker")


# Components whose log records the relay considers. Other components
# are filtered out cheaply at the sink boundary.
_OBSERVED_COMPONENTS: frozenset[str] = frozenset({"bybit_demo", "worker"})


@dataclass(frozen=True)
class _AlertSpec:
    """How a tag prefix maps to an :class:`AlertManager` call.

    Attributes:
        method: Name of the AlertManager method to call (string so the
            relay can be unit-tested against a mock without importing
            AlertManager).
        level: AlertLevel passed through (or used to construct details).
        kind: Either ``"error"`` (calls ``send_error_alert(component,
            msg, level)``) or ``"risk"`` (calls
            ``send_risk_warning(warning_type, details_dict)`` — always
            CRITICAL inside AlertManager).
        component_or_warning_type: For ``"error"`` this is the
            ``component`` arg; for ``"risk"`` it's the ``warning_type``
            arg. Both are used in the alert body and in dedup hashes.
    """

    method: str
    level: AlertLevel
    kind: str  # "error" | "risk"
    component_or_warning_type: str


# Trigger table. Tag prefix -> AlertSpec. Order does not matter — first
# matching prefix wins. Keep coarse: identical prefix + identical
# fields collapse via AlertManager's content-hash dedup, so a retry
# storm under a single root cause produces ONE alert per 5-min window.
_TRIGGERS: dict[str, _AlertSpec] = {
    # Adapter / client layer
    "BYBIT_DEMO_AUTH_FAIL": _AlertSpec(
        method="send_error_alert",
        level=AlertLevel.CRITICAL,
        kind="error",
        component_or_warning_type="bybit_demo",
    ),
    "BYBIT_DEMO_BOOT_FAIL": _AlertSpec(
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="bybit_demo_boot",
    ),
    "BYBIT_DEMO_TIMESTAMP_FAIL": _AlertSpec(
        method="send_error_alert",
        level=AlertLevel.WARNING,
        kind="error",
        component_or_warning_type="bybit_demo",
    ),
    "BYBIT_DEMO_RATE_LIMIT_HIT": _AlertSpec(
        method="send_error_alert",
        level=AlertLevel.WARNING,
        kind="error",
        component_or_warning_type="bybit_demo_rate_limit",
    ),
    # Exchange-switch failure paths
    "EXCHANGE_SWITCH_ABORT_OPEN_POSITIONS": _AlertSpec(
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="exchange_switch_abort",
    ),
    "EXCHANGE_SWITCH_DB_FLIP_FAIL": _AlertSpec(
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="exchange_switch_db_flip",
    ),
    "EXCHANGE_SWITCH_RESTART_FAIL": _AlertSpec(
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="exchange_switch_restart",
    ),
    "EXCHANGE_SWITCH_NO_SYSTEMCTL": _AlertSpec(
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="exchange_switch_systemctl",
    ),
    # Post-restart verification — wallet probe failure means the new
    # adapter is unreachable; flag CRITICAL so the operator knows
    # the system is up but degraded.
    "POST_SWITCH_VERIFY_WALLET_FAIL": _AlertSpec(
        method="send_error_alert",
        level=AlertLevel.CRITICAL,
        kind="error",
        component_or_warning_type="post_switch_verify",
    ),
    "POST_SWITCH_VERIFY_POSITIONS_FAIL": _AlertSpec(
        method="send_error_alert",
        level=AlertLevel.WARNING,
        kind="error",
        component_or_warning_type="post_switch_verify",
    ),
    # P10 of P1-P10 fix series — surface the audit-flagged silent
    # error tags so the operator gets real-time visibility instead of
    # discovering failures hours later in workers.log. Severity tiers
    # below match the documented operator-impact analysis (CRITICAL =
    # capital-threatening or unrecoverable, WARNING = degraded but
    # functional, INFO = informational only and intentionally NOT
    # surfaced via the WARNING-level relay).
    #
    # PARTIAL_FILL is intentionally NOT in the trigger table: it
    # logs at INFO and is normal market behaviour on illiquid pairs.
    # Surfacing it would create alert spam without operator-actionable
    # signal.
    "BYBIT_DEMO_ORDER_REJECT": _AlertSpec(
        method="send_error_alert",
        level=AlertLevel.WARNING,
        kind="error",
        component_or_warning_type="bybit_demo",
    ),
    "BYBIT_DEMO_INSUFFICIENT_BALANCE": _AlertSpec(
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="bybit_demo_balance",
    ),
    "BYBIT_DEMO_SET_SL_FAIL": _AlertSpec(
        # Position without SL = unbounded loss risk; CRITICAL.
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="bybit_demo_sl_missing",
    ),
    # CRITICAL-5 (2026-05-09): adapter-side defensive rejection of
    # wrong-side SL/TP attempts. WARNING (not CRITICAL) because the
    # local rejection means we did NOT submit a bad value to Bybit and
    # did NOT lose SL protection; the prior SL is still active. Caller
    # bug needs a follow-up though, so surface it.
    "BYBIT_DEMO_SET_SL_DIRECTION_BUG": _AlertSpec(
        method="send_error_alert",
        level=AlertLevel.WARNING,
        kind="error",
        component_or_warning_type="bybit_demo_sl_direction",
    ),
    "BYBIT_DEMO_SET_TP_DIRECTION_BUG": _AlertSpec(
        method="send_error_alert",
        level=AlertLevel.WARNING,
        kind="error",
        component_or_warning_type="bybit_demo_tp_direction",
    ),
    "BYBIT_DEMO_SET_TP_FAIL": _AlertSpec(
        # Profit-taking degraded; not loss-side risk.
        method="send_error_alert",
        level=AlertLevel.WARNING,
        kind="error",
        component_or_warning_type="bybit_demo",
    ),
    "BYBIT_DEMO_LEVERAGE_FAIL": _AlertSpec(
        # Trade may proceed without leverage change; size could be wrong.
        method="send_error_alert",
        level=AlertLevel.WARNING,
        kind="error",
        component_or_warning_type="bybit_demo",
    ),
    "BYBIT_DEMO_CLOSE_REJECT": _AlertSpec(
        # Cannot exit position — operator must manually intervene.
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="bybit_demo_close_reject",
    ),
    "BYBIT_DEMO_WALLET_FAIL": _AlertSpec(
        # Capital state unknown; downstream sizing may be incorrect.
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="bybit_demo_wallet",
    ),
    # REDUCE_FALLBACK is un-prefixed by historical operator preference
    # (see project_bybit_demo_adapter_status.md note from prior gap-fill).
    # Adding it to triggers without renaming preserves existing log
    # analysis tooling that grep'd for the bare tag.
    "REDUCE_FALLBACK": _AlertSpec(
        # Partial reduce failed → fell back to full close; the close
        # itself succeeded but the precision is degraded.
        method="send_error_alert",
        level=AlertLevel.WARNING,
        kind="error",
        component_or_warning_type="bybit_demo_reduce_fallback",
    ),
    # P1 (Phase 3b) introduced this tag — surface it when the WS
    # subscriber gives up after exhausting reconnect attempts.
    # Polling continues as fallback so the system is degraded but
    # not broken; CRITICAL so the operator notices and investigates.
    "BYBIT_DEMO_WS_DEAD": _AlertSpec(
        method="send_risk_warning",
        level=AlertLevel.CRITICAL,
        kind="risk",
        component_or_warning_type="bybit_demo_ws_dead",
    ),
}


def _extract_tag(message_text: str) -> Optional[str]:
    """Return the leading tag (first whitespace-delimited token) or None."""
    if not message_text:
        return None
    head = message_text.split("|", 1)[0].strip()
    if not head:
        return None
    # Defensive: tags are uppercase + underscore by convention.
    return head if head.replace("_", "").isalnum() else None


class BybitDemoAlertRelay:
    """Loguru sink that dispatches CRITICAL/WARNING tagged events as alerts.

    Args:
        alert_manager: A live :class:`AlertManager` instance with
            ``send_error_alert`` and ``send_risk_warning`` coroutines.
            Duck-typed so unit tests can substitute a mock with the
            same coroutine signatures.
        loop: The asyncio event loop the relay should schedule alert
            coroutines on. Captured at registration so the sync sink
            (which may run on a worker thread when ``enqueue=True``)
            can hand off safely.
        triggers: Optional override of the default ``_TRIGGERS`` table
            for testing.
    """

    def __init__(
        self,
        alert_manager: Any,
        *,
        loop: asyncio.AbstractEventLoop,
        triggers: Optional[dict[str, _AlertSpec]] = None,
    ) -> None:
        self._am = alert_manager
        self._loop = loop
        self._triggers = triggers if triggers is not None else _TRIGGERS
        self._sink_id: Optional[int] = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def register(self) -> int:
        """Add the sink to the loguru root logger. Returns the sink id."""
        if self._sink_id is not None:
            return self._sink_id
        self._sink_id = loguru_logger.add(
            self._sink,
            filter=self._filter,
            level="WARNING",
            enqueue=True,
            # diagnose=False / backtrace=False — sinks shouldn't expand
            # variable values; we only consume the formatted message.
            backtrace=False,
            diagnose=False,
        )
        _log.info(
            f"BYBIT_DEMO_ALERT_RELAY_REGISTERED | "
            f"triggers={len(self._triggers)} sink_id={self._sink_id} | {ctx()}"
        )
        return self._sink_id

    def unregister(self) -> None:
        """Remove the sink. Idempotent."""
        if self._sink_id is None:
            return
        try:
            loguru_logger.remove(self._sink_id)
        except (ValueError, KeyError):
            # Sink already gone (logger was reconfigured). Safe to ignore.
            pass
        self._sink_id = None

    # ------------------------------------------------------------------ #
    # Sink internals                                                      #
    # ------------------------------------------------------------------ #

    def _filter(self, record: dict) -> bool:
        """Cheap pre-check before parsing the message.

        Filters by component (only bybit_demo + worker) and by tag
        prefix presence. Loguru calls this on every log emission so
        keep allocations minimal.
        """
        component = record.get("extra", {}).get("component", "")
        if component not in _OBSERVED_COMPONENTS:
            return False
        message = record.get("message", "")
        # Fast prefix check — avoids splitting on every record.
        for prefix in self._triggers:
            if message.startswith(prefix):
                return True
        return False

    def _sink(self, message: Any) -> None:
        """Receive a loguru Message and schedule an alert dispatch.

        Loguru passes a ``loguru.Message`` (str-like) with a ``.record``
        attribute. We extract the tag, look up the spec, and hand the
        coroutine to the captured event loop.
        """
        try:
            record = getattr(message, "record", None) or {}
            text = record.get("message", "")
            tag = _extract_tag(text)
            if tag is None or tag not in self._triggers:
                # Fast path: filter already narrowed this; if we miss
                # here it's a benign edge (truncated record) and we
                # silently drop rather than raise out of the sink.
                return
            spec = self._triggers[tag]
            coro = self._build_coro(spec, text, record)
            if coro is None:
                return
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as e:
            # Sink MUST NOT raise — would propagate into loguru's
            # internal task and disable subsequent logging. We log
            # via the worker logger which routes to workers.log; if
            # that itself fails (extremely unlikely), there's nothing
            # left to do.
            try:
                _log.warning(
                    f"BYBIT_DEMO_ALERT_RELAY_FAIL | "
                    f"err={str(e)[:160]} | {ctx()}"
                )
            except Exception:
                pass

    def _build_coro(
        self,
        spec: _AlertSpec,
        text: str,
        record: dict,
    ) -> Optional[Any]:
        """Construct the coroutine for the alert dispatch.

        Truncates the message to keep Telegram payloads compact and
        avoids leaking secret-shaped substrings (already not logged
        anywhere upstream, but defensive).
        """
        msg_short = text[:280]
        if spec.kind == "error":
            method: Callable[..., Any] = getattr(self._am, spec.method, None)
            if method is None:
                return None
            return method(spec.component_or_warning_type, msg_short, spec.level)
        if spec.kind == "risk":
            method = getattr(self._am, spec.method, None)
            if method is None:
                return None
            details = {
                "tag": spec.component_or_warning_type,
                "level": spec.level.value if hasattr(spec.level, "value") else str(spec.level),
                "log_line": msg_short,
                "log_level": record.get("level", {}).name if hasattr(record.get("level", {}), "name") else "",
            }
            return method(spec.component_or_warning_type, details)
        return None
