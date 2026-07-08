"""Restart-based exchange switcher for Shadow ↔ Bybit Demo.

The operator-facing switching path for the bybit_demo adapter. Closes
all positions on the CURRENT exchange, persists the target mode to the
database, writes a sentinel file, and triggers a process restart via
systemd. On the next boot, ``Transformer.initialize()`` reads the new
mode from the DB and the post-switch verifier sends a Telegram
notification.

Why restart vs hot-swap:
  - Live-bybit (real-money) uses ``Transformer.switch_to()`` for
    in-memory hot-swap. That stays untouched.
  - bybit_demo prefers a process restart for clean state — every cache
    starts fresh, every worker initializes against the new exchange,
    no mid-cycle state to reconcile, no in-flight Claude calls
    spanning two exchanges. Boot grace + Layer 1→2→3 sequencing
    handles cold-start cleanly anyway, so the restart cost is just
    the ~60 s boot window.

Why a separate class vs an extra method on Transformer:
  - Transformer's job is routing. The switcher's job is orchestration.
  - The switcher reuses Transformer's public primitives
    (``active_position_service``, ``record_switch``,
    ``set_switching_state``) without coupling Transformer to systemd /
    sentinel-file concerns.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.modes import RESTART_SWITCHABLE_MODES

log = get_logger("worker")


# Sentinel path is shared with the post-switch verifier so both modules
# read/write the same file.
POST_SWITCH_SENTINEL_PATH = Path("data/post_switch_sentinel.json")


class ExchangeSwitcher:
    """Orchestrates a restart-based switch between Shadow and Bybit Demo.

    Args:
        transformer: The project's :class:`Transformer` instance. All
            DB writes (state, history, target-mode persistence) flow
            through Transformer's public surface
            (:meth:`Transformer.set_switching_state`,
            :meth:`Transformer.record_switch`,
            :meth:`Transformer.persist_target_mode`) — the switcher
            never touches ``transformer_state`` directly.
        alert_manager: Optional :class:`AlertManager` for pre-restart
            Telegram notifications. If ``None``, switch proceeds
            silently (still logs structured events for forensics).
    """

    def __init__(
        self,
        transformer: Any,
        alert_manager: Any | None = None,
    ) -> None:
        self._t = transformer
        self._alerts = alert_manager
        self._log = log

    async def execute_switch_with_restart(
        self,
        target_mode: str,
        *,
        force: bool = False,
        reason: str = "telegram_button",
    ) -> dict[str, Any]:
        """Close all positions on current adapter, persist target mode, restart.

        Workflow phases (each emits a structured log event):
          A. Validate target mode + current state
          B. Inventory current open positions
          C. Pre-flight: refuse if positions > 0 and force=False
          D. Set is_switching=True (DB), close all positions, verify
          E. Persist current_mode = target_mode (DB), record switch history,
             write the post-switch sentinel
          F. Send pre-restart Telegram alert
          G. Trigger ``systemctl restart trading-workers trading-mcp-sse``
             (asynchronous; the calling process is killed shortly after)

        Returns:
            ``{success: True, ...}`` on a successful restart trigger
            (the caller should not assume normal completion — the
            process is dying), or ``{success: False, error: ...}`` on
            any pre-restart failure (positions stuck open, DB write
            failed, etc.). On failure, the existing in-memory mode is
            preserved and is_switching is reverted to False.
        """
        old_mode = self._t.current_mode

        # ── Phase A: Validate ────────────────────────────────────
        if target_mode not in RESTART_SWITCHABLE_MODES:
            return {
                "success": False,
                "error": (
                    f"Restart-based switching supports only "
                    f"{RESTART_SWITCHABLE_MODES}; got '{target_mode}'. "
                    f"Use Transformer.switch_to() for live bybit."
                ),
            }
        if target_mode == old_mode:
            return {"success": False, "error": f"Already on {target_mode}"}
        if self._t.is_switching:
            return {"success": False, "error": "Switch already in progress"}

        self._log.info(
            f"EXCHANGE_SWITCH_VALIDATE | from={old_mode} to={target_mode} "
            f"force={force} reason='{reason[:80]}' | {ctx()}"
        )

        # ── Phase B: Position inventory ──────────────────────────
        try:
            pos_svc = self._t.active_position_service
            positions = await pos_svc.get_positions() if pos_svc else []
        except Exception as e:
            self._log.error(
                f"EXCHANGE_SWITCH_INVENTORY_FAIL | err={str(e)[:160]} | {ctx()}"
            )
            return {
                "success": False,
                "error": f"Cannot inventory positions: {e}",
            }
        position_count = len(positions)

        # ── Phase C: Pre-flight ──────────────────────────────────
        if position_count > 0 and not force:
            return {
                "success": False,
                "error": (
                    f"{position_count} open position(s). Pass force=True "
                    f"to close all at market and proceed."
                ),
                "open_positions": position_count,
            }

        # ── Phase D: Mark switching + close all positions ────────
        await self._t.set_switching_state(target_mode, switching=True)
        self._log.info(
            f"EXCHANGE_SWITCH_CLOSE_BEGIN | n={position_count} | {ctx()}"
        )

        close_results: list[dict[str, Any]] = []
        if position_count > 0:
            # Close concurrently — Bybit matches per symbol independently
            # so serial waits are pure overhead. return_exceptions=True
            # keeps one failure from canceling the rest of the batch.
            raw = await asyncio.gather(
                *[
                    pos_svc.close_position(pos.symbol, purpose="exchange_switch")
                    for pos in positions
                ],
                return_exceptions=True,
            )
            for pos, item in zip(positions, raw):
                if isinstance(item, BaseException):
                    close_results.append(
                        {"symbol": pos.symbol, "success": False, "error": str(item)}
                    )
                else:
                    close_results.append({"symbol": pos.symbol, "success": True})

            # Settle window for closures to propagate, then verify.
            await asyncio.sleep(2.0)
            try:
                remaining = await pos_svc.get_positions()
            except Exception:
                remaining = positions  # conservative — assume not closed

            if remaining:
                # One retry round for stragglers.
                self._log.warning(
                    f"EXCHANGE_SWITCH_RETRY | remaining={len(remaining)} | {ctx()}"
                )
                for pos in remaining:
                    try:
                        await pos_svc.close_position(pos.symbol, purpose="exchange_switch_retry")
                    except Exception:
                        pass
                await asyncio.sleep(2.0)
                try:
                    remaining = await pos_svc.get_positions()
                except Exception:
                    remaining = []

            if remaining:
                # Abort. Revert is_switching so live trading can resume.
                await self._t.set_switching_state(None, switching=False)
                self._log.error(
                    f"EXCHANGE_SWITCH_ABORT_OPEN_POSITIONS | "
                    f"remaining={len(remaining)} | {ctx()}"
                )
                return {
                    "success": False,
                    "error": (
                        f"{len(remaining)} position(s) failed to close. "
                        f"Switch aborted, system unchanged."
                    ),
                    "close_results": close_results,
                    "open_positions": len(remaining),
                }

        positions_closed = sum(1 for r in close_results if r["success"])
        self._log.info(
            f"EXCHANGE_SWITCH_CLOSE_DONE | closed={positions_closed} | {ctx()}"
        )

        # ── Phase E: Persist target mode + record switch + sentinel ──
        try:
            await self._persist_target_mode(target_mode)
        except Exception as e:
            await self._t.set_switching_state(None, switching=False)
            self._log.error(
                f"EXCHANGE_SWITCH_DB_FLIP_FAIL | err={str(e)[:160]} | {ctx()}"
            )
            return {
                "success": False,
                "error": f"DB flip failed: {e}",
                "close_results": close_results,
            }
        self._log.info(
            f"EXCHANGE_SWITCH_DB_FLIP | from={old_mode} to={target_mode} | {ctx()}"
        )

        # Use Transformer's record_switch helper for the switch_history
        # row so the schema/format is consistent across both switching
        # paths (hot-swap + restart-based).
        try:
            await self._t.record_switch(
                from_mode=old_mode,
                to_mode=target_mode,
                positions_closed=positions_closed,
                close_results=close_results,
                reason=f"telegram_restart_switch:{reason[:60]}",
                success=True,
                error_message=None,
            )
        except Exception as e:
            # Non-fatal — history record failure must not block the
            # restart. We've already persisted the new mode in DB so
            # boot will pick up correctly.
            self._log.warning(
                f"EXCHANGE_SWITCH_HISTORY_FAIL | err={str(e)[:160]} | {ctx()}"
            )

        try:
            self._write_sentinel(
                from_mode=old_mode,
                to_mode=target_mode,
                positions_closed=positions_closed,
                reason=reason,
            )
        except Exception as e:
            # Non-fatal — sentinel only drives the post-restart
            # notification; boot still succeeds without it.
            self._log.warning(
                f"EXCHANGE_SWITCH_SENTINEL_FAIL | err={str(e)[:160]} | {ctx()}"
            )

        # ── Phase F: Pre-restart Telegram alert ──────────────────
        if self._alerts is not None:
            try:
                await self._alerts.send_custom(
                    f"Exchange switch: closed {positions_closed} position(s). "
                    f"Switching {old_mode} → {target_mode}. "
                    f"Restarting system. ETA ~60 seconds. "
                    f"You will receive a confirmation once services are back up."
                )
            except Exception as e:
                self._log.warning(
                    f"EXCHANGE_SWITCH_ALERT_FAIL | err={str(e)[:160]} | {ctx()}"
                )

        # ── Phase G: Trigger systemd restart ──────────────────────
        # start_new_session=True so the child survives this process
        # being killed by systemd. stdout/stderr → DEVNULL so the
        # grandchild doesn't keep the trading-workers stdio pipe open.
        self._log.info(
            f"EXCHANGE_SWITCH_RESTART_TRIGGER | "
            f"services=trading-workers,trading-mcp-sse | {ctx()}"
        )
        try:
            subprocess.Popen(
                [
                    "systemctl",
                    "restart",
                    "trading-workers",
                    "trading-mcp-sse",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            # systemctl missing (e.g., dev environment without systemd).
            await self._t.set_switching_state(None, switching=False)
            self._log.error(
                f"EXCHANGE_SWITCH_NO_SYSTEMCTL | err={str(e)[:160]} | {ctx()}"
            )
            return {
                "success": False,
                "error": f"systemctl not available: {e}",
                "close_results": close_results,
            }
        except Exception as e:
            await self._t.set_switching_state(None, switching=False)
            self._log.error(
                f"EXCHANGE_SWITCH_RESTART_FAIL | err={str(e)[:160]} | {ctx()}"
            )
            return {
                "success": False,
                "error": f"Restart trigger failed: {e}",
                "close_results": close_results,
            }

        return {
            "success": True,
            "from_mode": old_mode,
            "to_mode": target_mode,
            "positions_closed": positions_closed,
            "close_results": close_results,
            "restart_triggered_at": _now_iso(),
        }

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    async def _persist_target_mode(self, target_mode: str) -> None:
        """Persist the new mode through Transformer's public surface.

        Delegates to :meth:`Transformer.persist_target_mode` so the
        switcher never reaches into ``transformer_state`` directly.
        Kept as a thin wrapper so the workflow phases above stay
        readable and the exchange-switch log tag still corresponds to
        a single named phase (``EXCHANGE_SWITCH_DB_FLIP``).
        """
        await self._t.persist_target_mode(target_mode)

    def _write_sentinel(
        self,
        from_mode: str,
        to_mode: str,
        positions_closed: int,
        reason: str,
    ) -> None:
        """Write the post-switch sentinel JSON file.

        The file lives under ``data/`` (already excluded from git via
        ``.gitignore``). The post-switch verifier reads + deletes it on
        the next boot.
        """
        POST_SWITCH_SENTINEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "from_mode": from_mode,
            "to_mode": to_mode,
            "positions_closed": positions_closed,
            "reason": reason,
            "written_at": _now_iso(),
            "written_at_monotonic": time.monotonic(),
        }
        # Write atomically via tmp + rename so a partial write never
        # leaves a half-written sentinel that the verifier reads.
        tmp = POST_SWITCH_SENTINEL_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, POST_SWITCH_SENTINEL_PATH)


def _now_iso() -> str:
    """ISO 8601 UTC timestamp (matches Transformer's _now_iso convention)."""
    return datetime.now(timezone.utc).isoformat()
