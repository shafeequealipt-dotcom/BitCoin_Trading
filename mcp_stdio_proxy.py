"""MCP stdio → SSE proxy (Phase Y-22 long-lived rework).

Claude CLI spawns an MCP stdio server on every brain call. The previous
``server.py --transport stdio`` path did a full 18-service init, opened
a new DB connection, migrated the schema, registered 43 tools, and
connected a "standalone" Telegram bot — every single call. That
wasted 2-5 s of latency and created 4 Telegram reconnects per 20-minute
session (observed in `observability_02-24_to_02-44_2026-04-24.log`).

This proxy forwards the stdio MCP protocol to the already-running SSE
MCP server owned by ``trading-mcp-sse.service`` (localhost:8080). All
heavy state — DB, trading services, Telegram bot, tool registry — lives
in that single long-lived process. The proxy itself only speaks the MCP
transport: no services, no DB, no Telegram.

CRITICAL: the MCP stdio protocol uses stdout/stderr for JSON-RPC
framing. Any write to either stream by this process would corrupt a
live call. Logging must go exclusively to files — ``setup_logging``
removes loguru's default stderr sink as its first action, so the
``from src.core.logging import ...`` import below MUST remain before any
``log.*`` call.

Claude CLI passes through the parent environment, so ``MCP_AUTH_TOKEN``
(loaded from ``.env`` by systemd for the workers service) is available
here via ``os.environ``.
"""

from __future__ import annotations

import os
import sys
import threading

import anyio

# File-only logging FIRST (protects the stdio MCP channel).
from src.core.logging import setup_logging, get_logger  # noqa: E402

setup_logging(log_level="INFO", log_dir="data/logs")
log = get_logger("mcp")

from mcp.client.sse import sse_client  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402


_DEFAULT_UPSTREAM = "http://127.0.0.1:8080/sse"
_DEFAULT_HTTP_TIMEOUT = 10.0
_DEFAULT_SSE_READ_TIMEOUT = 600.0  # 10 min: generous ceiling for one tool call

# Hard cap on async-cleanup after the proxy has logically finished.
# httpx' AsyncClient + aconnect_sse teardown can linger several seconds
# waiting for connection-pool drain; Claude CLI treats the subprocess as
# alive until it actually exits, so we bound the teardown.
_SHUTDOWN_GRACE_S = 2.0


def _arm_shutdown_watchdog() -> None:
    """Schedule a hard ``os._exit`` ``_SHUTDOWN_GRACE_S`` seconds from now.

    Called from inside the proxy's own ``finally`` once the task group
    has cancelled. If the outer async cleanup finishes in time we also
    take a fast exit via ``main``'s ``os._exit(0)``. This watchdog only
    matters when cleanup lingers past the grace window.
    """
    def _deadline():
        try:
            from loguru import logger as _lg
            _lg.bind(component="mcp").info(
                f"MCP_PROXY_FORCE_EXIT | grace_s={_SHUTDOWN_GRACE_S}"
            )
            _lg.complete()
        except Exception:
            pass
        os._exit(0)
    t = threading.Timer(_SHUTDOWN_GRACE_S, _deadline)
    t.daemon = True
    t.start()


async def _pipe(direction: str, source, sink, cancel_scope) -> None:
    """Forward ``SessionMessage``s from ``source`` to ``sink`` until either closes.

    ``source`` may yield an ``Exception`` (per MCP SDK convention on the
    read stream); we log and skip rather than propagate so one malformed
    frame does not tear down the whole proxy.

    When ``source`` closes (e.g. stdin EOF from Claude CLI), the opposite
    pipe should not keep the task group alive — we cancel the shared
    ``cancel_scope`` so the proxy exits cleanly in both directions.
    """
    reason = "source_closed"
    try:
        async for msg in source:
            if isinstance(msg, Exception):
                log.warning(
                    f"MCP_PROXY_MSG_ERR | dir={direction} "
                    f"err_type={type(msg).__name__} err='{str(msg)[:120]}'"
                )
                continue
            try:
                await sink.send(msg)
            except anyio.ClosedResourceError:
                reason = "sink_closed"
                break
            except Exception as e:
                log.warning(
                    f"MCP_PROXY_SINK_ERR | dir={direction} "
                    f"err_type={type(e).__name__} err='{str(e)[:160]}'"
                )
                reason = "sink_error"
                break
    except anyio.ClosedResourceError:
        reason = "source_closed"
    except Exception as e:
        log.warning(
            f"MCP_PROXY_SOURCE_ERR | dir={direction} "
            f"err_type={type(e).__name__} err='{str(e)[:160]}'"
        )
        reason = "source_error"
    finally:
        log.info(f"MCP_PROXY_PIPE_END | dir={direction} reason={reason}")
        cancel_scope.cancel()


async def _run(upstream: str, auth_token: str) -> None:
    import time as _t

    _t0 = _t.time()
    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with stdio_server() as (stdio_read, stdio_write):
        try:
            async with sse_client(
                upstream,
                headers=headers,
                timeout=_DEFAULT_HTTP_TIMEOUT,
                sse_read_timeout=_DEFAULT_SSE_READ_TIMEOUT,
            ) as (sse_read, sse_write):
                connect_ms = (_t.time() - _t0) * 1000
                log.info(
                    f"MCP_PROXY_CONNECT | upstream={upstream} "
                    f"connect_ms={connect_ms:.0f} auth={'y' if auth_token else 'n'}"
                )
                try:
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(
                            _pipe, "in", stdio_read, sse_write, tg.cancel_scope
                        )
                        tg.start_soon(
                            _pipe, "out", sse_read, stdio_write, tg.cancel_scope
                        )
                finally:
                    log.info("MCP_PROXY_DISCONNECT | reason=session_end")
                    # Arm the watchdog here (not in main()) because the
                    # lingering cleanup we're guarding against happens on
                    # the way out of this ``async with`` block.
                    _arm_shutdown_watchdog()
        except Exception as e:
            # Unwrap ExceptionGroup (anyio task-group wrapping) so the log
            # surfaces the underlying connect error rather than the generic
            # "unhandled errors in a TaskGroup" wrapper.
            inner = e
            while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
                inner = inner.exceptions[0]
            log.error(
                f"MCP_PROXY_UPSTREAM_FAIL | upstream={upstream} "
                f"err_type={type(inner).__name__} err='{str(inner)[:240]}'"
            )
            # Flush loguru then force-exit. sys.exit inside an async task
            # group gets wrapped in ExceptionGroup and leaks a noisy trace
            # to stderr; os._exit is the clean path. Exit 2 so Claude CLI
            # surfaces the MCP failure rather than silently proceeding
            # without tools. Falling back to the full-init server.py path
            # would defeat Y-22 and hide breakage.
            try:
                from loguru import logger as _lg
                _lg.complete()
            except Exception:
                pass
            os._exit(2)


def main() -> None:
    upstream = os.environ.get("MCP_PROXY_UPSTREAM", _DEFAULT_UPSTREAM)
    auth_token = os.environ.get("MCP_AUTH_TOKEN", "")
    try:
        anyio.run(_run, upstream, auth_token)
    except KeyboardInterrupt:
        pass
    # httpx' AsyncClient shutdown can linger on connection-pool drain after
    # the SSE session has logically ended. Claude CLI waits for the
    # subprocess to exit before tearing down its side, so any lingering
    # cleanup delays the next brain call. Force-flush logs and exit via
    # os._exit so the proxy terminates promptly. Safe because the proxy
    # holds no durable state — only in-flight network sockets which the
    # kernel reclaims on process exit.
    try:
        from loguru import logger as _loguru_logger
        _loguru_logger.complete()
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    main()
