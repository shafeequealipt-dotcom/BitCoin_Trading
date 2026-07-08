"""Post-restart verifier for the bybit_demo restart-based switch.

Called near the end of ``WorkerManager.initialize()`` after services
are wired and the Transformer has applied the new mode. Reads the
sentinel JSON written by :class:`ExchangeSwitcher`, runs sanity probes
against the new active adapter (wallet balance + position list), sends
a Telegram confirmation, and deletes the sentinel.

If the sentinel is missing (normal boot, not a switch), this is a
no-op and returns silently. If verification probes fail, it logs an
ERROR but does not abort boot — the operator already lost their
old-exchange state, so the system MUST come up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("worker")


# Re-exported from .exchange_switcher; importing it here would create
# a cyclic import (verifier ← __init__ ← switcher ← verifier). Use a
# duplicate constant — both modules write/read this exact path.
POST_SWITCH_SENTINEL_PATH = Path("data/post_switch_sentinel.json")


async def verify_post_switch(
    transformer: Any,
    alert_manager: Any | None = None,
    db: Any | None = None,
) -> bool:
    """Run post-restart verification if a switch sentinel is present.

    Args:
        transformer: Active :class:`Transformer` after ``initialize()``.
        alert_manager: Optional alert manager for the operator notification.
        db: Optional :class:`DatabaseManager` (currently unused; reserved
            for future verification queries).

    Returns:
        ``True`` if a sentinel was found and verification ran (even if
        probes failed). ``False`` if no sentinel — meaning this was a
        normal boot, not a switch.
    """
    # Single read attempt — collapses the prior exists()+read_text() pair
    # (two syscalls on every boot) into one. The boot path fires this
    # unconditionally, so cutting one stat() per boot matters more than
    # the marginal code-clarity loss.
    sentinel: dict[str, Any] = {}
    try:
        raw = POST_SWITCH_SENTINEL_PATH.read_text()
    except FileNotFoundError:
        return False
    except OSError as e:
        log.warning(
            f"POST_SWITCH_SENTINEL_READ_FAIL | err={str(e)[:160]} | {ctx()}"
        )
        return False
    try:
        sentinel = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning(
            f"POST_SWITCH_SENTINEL_PARSE_FAIL | err={str(e)[:160]} | {ctx()}"
        )

    from_mode = sentinel.get("from_mode", "?")
    to_mode = sentinel.get("to_mode", "?")
    positions_closed = sentinel.get("positions_closed", 0)

    log.info(
        f"POST_SWITCH_VERIFY_BEGIN | from={from_mode} to={to_mode} "
        f"closed={positions_closed} active_mode={transformer.current_mode} | {ctx()}"
    )

    # Probe 1: wallet balance on the new active adapter.
    equity: float | None = None
    try:
        acc = transformer.active_account_service
        if acc is not None:
            bal = await acc.get_wallet_balance()
            equity = getattr(bal, "total_equity", None)
    except Exception as e:
        log.error(
            f"POST_SWITCH_VERIFY_WALLET_FAIL | err={str(e)[:160]} | {ctx()}"
        )

    # Probe 2: position list on the new active adapter (should be empty
    # since the switcher closed everything before restart).
    positions_after: int | None = None
    try:
        pos_svc = transformer.active_position_service
        if pos_svc is not None:
            positions = await pos_svc.get_positions()
            positions_after = len(positions)
    except Exception as e:
        log.error(
            f"POST_SWITCH_VERIFY_POSITIONS_FAIL | err={str(e)[:160]} | {ctx()}"
        )

    log.info(
        f"POST_SWITCH_VERIFY_DONE | active={transformer.current_mode} "
        f"equity={equity} positions={positions_after} | {ctx()}"
    )

    # Operator notification.
    if alert_manager is not None:
        try:
            equity_str = f"${equity:,.2f}" if isinstance(equity, (int, float)) else "unknown"
            pos_str = (
                str(positions_after) if positions_after is not None else "unknown"
            )
            await alert_manager.send_custom(
                f"Restart complete. Now trading on {transformer.current_mode}. "
                f"Equity: {equity_str}. Open positions: {pos_str}. "
                f"Previously {from_mode}; closed {positions_closed} position(s) at switch."
            )
        except Exception as e:
            log.warning(
                f"POST_SWITCH_VERIFY_ALERT_FAIL | err={str(e)[:160]} | {ctx()}"
            )

    # Remove the sentinel so subsequent boots don't fire again.
    try:
        POST_SWITCH_SENTINEL_PATH.unlink(missing_ok=True)
    except Exception as e:
        log.warning(
            f"POST_SWITCH_SENTINEL_UNLINK_FAIL | err={str(e)[:160]} | {ctx()}"
        )

    return True
