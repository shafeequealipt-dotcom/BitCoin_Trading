"""Exchange tools: switch / get_current_exchange / validate_switch (3 tools).

The switch_exchange_with_restart tool is the single source of truth for
the bybit_demo restart-based switching workflow. It is callable via
MCP (Claude / programmatic) and via the Telegram dashboard handler
(Phase 5) — both paths route through the same ExchangeSwitcher.

Restricted to target_mode in (shadow, bybit_demo). Live "bybit"
switching uses Transformer.switch_to() directly via the existing
dashboard buttons.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from mcp.types import TextContent, Tool

from src.core.modes import RESTART_SWITCHABLE_MODES
from src.exchanges.switching import ExchangeSwitcher

# JSON-schema enums consume a list, not a tuple. Materialize once so
# both schemas below stay in lockstep with src.core.modes.
_RESTART_MODE_ENUM: list[str] = list(RESTART_SWITCHABLE_MODES)


def register_exchange_tools(
    services: dict[str, Any],
    alert_manager: Any | None = None,
) -> tuple[list[Tool], dict[str, Callable]]:
    """Register the 3 exchange-switching tools.

    Args:
        services: Service registry from WorkerManager. Must contain
            "transformer" for the tools to function.
        alert_manager: Optional — used for pre-restart Telegram alerts.

    Returns:
        ``(tools, handlers)`` tuple matching the project's MCP tool
        registration contract.

    The ExchangeSwitcher writes through Transformer's public methods
    (``set_switching_state``, ``record_switch``, ``persist_target_mode``)
    so this registrar no longer takes a DatabaseManager — the prior
    ``db`` argument was unused after the encapsulation refactor.
    """
    tools: list[Tool] = []
    handlers: dict[str, Callable] = {}

    # ---- get_current_exchange ----------------------------------------- #

    tools.append(
        Tool(
            name="get_current_exchange",
            description=(
                "Return the active execution exchange (shadow / bybit_demo / "
                "bybit), with current equity and open position count. "
                "Read-only — does not modify state."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        )
    )

    async def _get_current_exchange(args: dict[str, Any]) -> list[TextContent]:
        try:
            transformer = services.get("transformer")
            if transformer is None:
                return [TextContent(type="text", text="Transformer not available")]
            equity_info = await transformer.get_current_equity()
            positions_info = await transformer.get_open_positions_summary()
            payload = {
                "mode": transformer.current_mode,
                "mode_label": transformer.mode_label,
                "equity": equity_info.get("equity"),
                "available": equity_info.get("available"),
                "open_positions": positions_info.get("count", 0),
                "is_switching": transformer.is_switching,
            }
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["get_current_exchange"] = _get_current_exchange

    # ---- validate_switch ---------------------------------------------- #

    tools.append(
        Tool(
            name="validate_switch",
            description=(
                "Check whether a switch to the given target_mode is currently "
                "possible. Read-only — does not perform the switch. Returns "
                "blocking_conditions if any (open positions, target unreachable, "
                "etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_mode": {
                        "type": "string",
                        "enum": _RESTART_MODE_ENUM,
                    }
                },
                "required": ["target_mode"],
            },
        )
    )

    async def _validate_switch(args: dict[str, Any]) -> list[TextContent]:
        try:
            target_mode = args["target_mode"]
            transformer = services.get("transformer")
            if transformer is None:
                return [TextContent(type="text", text="Transformer not available")]

            blocking: list[str] = []
            if target_mode not in RESTART_SWITCHABLE_MODES:
                blocking.append(
                    f"target_mode '{target_mode}' is not restart-switchable "
                    f"(use Transformer.switch_to for live bybit)"
                )
            if target_mode == transformer.current_mode:
                blocking.append(f"already on {target_mode}")
            if transformer.is_switching:
                blocking.append("a switch is already in progress")

            # Position count.
            try:
                positions_info = await transformer.get_open_positions_summary()
                open_count = positions_info.get("count", 0)
            except Exception as e:
                open_count = 0
                blocking.append(f"could not query positions: {e}")

            # Target adapter reachability — prefer the target's
            # health_check if exposed; fall back to a wallet-balance probe.
            target_reachable = False
            try:
                target_info = await transformer.get_target_equity(target_mode)
                # If the dict has equity, the call succeeded.
                target_reachable = target_info.get("equity") is not None
                if not target_reachable:
                    err = target_info.get("error", "unreachable")
                    blocking.append(f"target {target_mode} not reachable: {err}")
            except Exception as e:
                blocking.append(f"target {target_mode} probe error: {e}")

            payload = {
                "target_mode": target_mode,
                "current_mode": transformer.current_mode,
                "can_switch": len(blocking) == 0,
                "blocking_conditions": blocking,
                "open_positions": open_count,
                "target_reachable": target_reachable,
            }
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["validate_switch"] = _validate_switch

    # ---- switch_exchange_with_restart --------------------------------- #

    tools.append(
        Tool(
            name="switch_exchange_with_restart",
            description=(
                "Switch the active execution exchange to target_mode. Closes "
                "all open positions on the current exchange at market, "
                "persists target_mode, writes a post-switch sentinel, and "
                "triggers systemctl restart of trading-workers and "
                "trading-mcp-sse. The current process is killed by systemd "
                "shortly after this returns. After restart, an operator "
                "notification is delivered via Telegram once services are up. "
                "Restricted to target_mode in (shadow, bybit_demo) — live "
                "bybit uses the in-memory Transformer.switch_to path."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_mode": {
                        "type": "string",
                        "enum": _RESTART_MODE_ENUM,
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Required True when there are open positions to "
                            "close them at market. Default False rejects the "
                            "switch with an error if positions > 0."
                        ),
                        "default": False,
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional context for switch_history.",
                        "default": "mcp_tool",
                    },
                },
                "required": ["target_mode"],
            },
        )
    )

    async def _switch_exchange(args: dict[str, Any]) -> list[TextContent]:
        try:
            target_mode = args["target_mode"]
            force = bool(args.get("force", False))
            reason = str(args.get("reason", "mcp_tool"))[:120]

            transformer = services.get("transformer")
            if transformer is None:
                return [TextContent(type="text", text="Transformer not available")]

            switcher = ExchangeSwitcher(transformer, alert_manager)
            result = await switcher.execute_switch_with_restart(
                target_mode, force=force, reason=reason
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["switch_exchange_with_restart"] = _switch_exchange

    return tools, handlers
