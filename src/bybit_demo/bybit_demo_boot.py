"""Boot-time validation for the Bybit demo adapter.

Called from :func:`src.workers.manager.WorkerManager.initialize` right
after the three Bybit demo service classes are constructed. Surfaces
adapter readiness via three structured tags so the operator (or a
post-restart sanity check) sees within seconds of boot whether
demo connectivity works:

  ``BYBIT_DEMO_BOOT_START`` (INFO)     — about to probe
  ``BYBIT_DEMO_BOOT_VALIDATED`` (INFO) — health + wallet probes succeeded
  ``BYBIT_DEMO_BOOT_FAIL`` (ERROR)     — one of the probes failed

The function never raises. Boot must come up even when the demo
adapter is misconfigured (wrong creds, network down) — Shadow's
precedent at ``src/workers/manager.py:298`` and the spec in
``IMPLEMENT_BYBIT_DEMO_LOGGING_OBSERVABILITY_INDEPTH.md`` Component 6.

The wallet probe bypasses :class:`BybitDemoAccountService` and calls
the underlying client directly. The service contract requires a zero
sentinel on error which would mask a failure here — a freshly funded
demo account legitimately has zero equity, so we cannot distinguish
"account empty" from "API down" via the sentinel. Direct client call
re-raises ``TradingMCPError`` and lets validate_boot tag accordingly.
"""

from __future__ import annotations

from typing import Any

from src.bybit_demo.bybit_demo_client import BybitDemoClient
from src.core.exceptions import TradingMCPError
from src.core.log_context import ctx
from src.core.logging import get_logger

_log = get_logger("bybit_demo")


async def validate_boot(
    client: BybitDemoClient,
    *,
    base_url: str,
    api_key_len: int,
    recv_window: int,
) -> dict[str, Any]:
    """Probe Bybit demo connectivity and emit boot tags.

    Args:
        client: The constructed :class:`BybitDemoClient`. Used directly
            (not through ``BybitDemoAccountService``) so the typed
            ``TradingMCPError`` from a failed wallet probe is observable.
        base_url: For BOOT_START / BOOT_FAIL fields.
        api_key_len: Length of the configured API key (no value leak).
            Zero indicates missing credentials.
        recv_window: Bybit recv_window setting (ms). For diagnostic.

    Returns:
        ``{"ok": True, "equity": float}`` on success, or
        ``{"ok": False, "step": "health_check" | "wallet" | "no_creds",
        "err": str}`` on failure. The dict is informational; the caller
        does NOT need to act on it (boot continues regardless).
    """
    _log.info(
        f"BYBIT_DEMO_BOOT_START | url={base_url} key_len={api_key_len} "
        f"recv_window={recv_window} | {ctx()}"
    )

    if api_key_len == 0:
        # Distinct failure mode — credentials missing. The adapter was
        # constructed but every signed call would fail. Catch this at
        # boot rather than letting the first trade attempt error out.
        _log.error(
            f"BYBIT_DEMO_BOOT_FAIL | step=no_creds "
            f"err='BYBIT_DEMO_API_KEY/SECRET unset' | {ctx()}"
        )
        return {"ok": False, "step": "no_creds", "err": "credentials missing"}

    # Probe 1: unsigned reachability via /v5/market/time. Fast (~50 ms)
    # and credentials-free so a green result means the demo cluster is
    # routable from this host.
    health_ok = await client.health_check()
    if not health_ok:
        _log.error(
            f"BYBIT_DEMO_BOOT_FAIL | step=health_check url={base_url} | {ctx()}"
        )
        return {
            "ok": False,
            "step": "health_check",
            "err": "health_check returned False",
        }

    # Probe 2: signed wallet-balance call. This is the first request
    # whose signature is verified by the demo cluster, so it catches
    # bad credentials, clock skew (10002), permission issues (10005),
    # and any rate-limit weirdness in one shot.
    try:
        envelope = await client.get(
            "/v5/account/wallet-balance",
            {"accountType": "UNIFIED"},
            op="boot_validate",
        )
    except TradingMCPError as e:
        _log.error(
            f"BYBIT_DEMO_BOOT_FAIL | step=wallet err={str(e)[:160]} | {ctx()}"
        )
        return {"ok": False, "step": "wallet", "err": str(e)[:160]}

    accounts = (envelope.get("result") or {}).get("list") or []
    equity = 0.0
    if accounts:
        try:
            equity = float(accounts[0].get("totalEquity") or 0.0)
        except (TypeError, ValueError):
            equity = 0.0

    _log.info(
        f"BYBIT_DEMO_BOOT_VALIDATED | url={base_url} equity={equity:.2f} | {ctx()}"
    )
    return {"ok": True, "equity": equity}
