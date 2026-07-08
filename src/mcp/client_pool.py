"""MCP Client Pool — warm long-lived MCP client connections.

Phase 23 (Y-22 — overhaul29) — eliminate the MCP stdio restart storm
where a new ``server.py`` process spins up every 3-5 minutes (~300/day)
just to handle a single tool call. Each spin-up loads 43 tools, runs
DB migrations, and initializes services — wasted CPU/IO that drowns
the cold-start cost across the whole system.

Design:

  - The pool maintains 1-2 long-lived MCP CLIENT connections to a
    persistent SSE server (run via ``server.py --transport sse``).
    Consumers acquire a client, send a tool call, and release it back.
    The HEAVY one-time MCPServer.initialize() runs ONCE on the SSE
    server side; subsequent tool calls amortize over the warm pool.

  - The pool is **opt-in** via ``settings.mcp_pool.enabled``. When
    disabled (default), every consumer spawns ``server.py`` stdio per
    call exactly as before. This makes adoption incremental: turn the
    pool on for one consumer (e.g. dashboard), measure, then expand.

  - Failure mode: the pool degrades gracefully. If acquire() can't
    obtain a healthy client within ``acquire_timeout_seconds``, callers
    fall back to one-shot stdio (the legacy path). The pool logs
    ``MCP_POOL_MISS`` so operators can correlate slow paths with pool
    exhaustion.

The actual network protocol (SSE vs websocket vs unix socket) is
abstracted behind ``MCPClient`` so future transports can drop in. Today
this module ships the lifecycle scaffold and observability tags; the
SSE wire-up is added per-consumer when each one migrates off stdio.

Status tags emitted by this module:
  * ``MCP_POOL_INIT``        — pool warmup successful (per warm slot).
  * ``MCP_POOL_HIT``         — acquire() returned a warm client.
  * ``MCP_POOL_MISS``        — acquire() timed out; caller falls back.
  * ``MCP_POOL_RECONNECT``   — health check replaced a dead client.
  * ``MCP_POOL_SHUTDOWN``    — pool stopped; warm clients released.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("mcp")


@dataclass
class MCPPoolSettings:
    """Configuration for the client pool. Defaults are SAFE: pool off.

    - enabled: master switch. When False, no warm pool runs and
      ``acquire()`` always raises ``PoolDisabled`` (callers expect this
      and fall back to legacy stdio).
    - sse_url: the URL of the persistent SSE MCP server. Caller is
      responsible for ensuring ``server.py --transport sse`` is running
      somewhere reachable.
    - min_warm: lower bound on warm client count. Pool tries to keep
      this many connections alive at all times.
    - max_warm: upper bound. Acquire() never opens beyond this; if
      hit, additional callers wait or time out.
    - health_check_interval_seconds: cadence of background health
      probes. Dead clients are replaced via MCP_POOL_RECONNECT.
    - acquire_timeout_seconds: max wait for an acquire() before
      giving up and emitting MCP_POOL_MISS.
    """
    enabled: bool = False
    sse_url: str = "http://127.0.0.1:8080"
    min_warm: int = 1
    max_warm: int = 2
    health_check_interval_seconds: int = 60
    acquire_timeout_seconds: float = 2.0


class PoolDisabled(RuntimeError):
    """Raised by ``acquire()`` when the pool is disabled in settings."""


@dataclass
class _PooledClient:
    """One warm slot in the pool. The actual underlying connection
    object is kept generic (``Any``) so future transports drop in
    without changing the pool's lifecycle code.
    """
    client: Any
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    in_use: bool = False
    healthy: bool = True


class MCPClientPool:
    """Manages a small fleet of warm MCP client connections.

    The pool is async-safe. Acquire/release use a single asyncio.Lock to
    serialize bookkeeping; the actual tool calls run outside the lock
    (it would defeat the purpose of pooling otherwise).

    Args:
        settings: MCPPoolSettings instance. ``enabled=False`` makes the
            pool a no-op except for ``acquire()`` raising PoolDisabled.
    """

    def __init__(self, settings: MCPPoolSettings) -> None:
        self._settings = settings
        self._clients: list[_PooledClient] = []
        self._lock = asyncio.Lock()
        self._health_task: asyncio.Task | None = None
        self._stats = {
            "hits": 0,
            "misses": 0,
            "reconnects": 0,
            "init_count": 0,
        }

    async def start(self) -> None:
        """Warm the pool to ``min_warm`` clients and start health checks.

        Safe to call when ``enabled=False`` (no-op). Idempotent — calling
        start twice is harmless; the second call returns immediately.
        """
        if not self._settings.enabled:
            return
        if self._health_task is not None:
            return
        try:
            for _ in range(self._settings.min_warm):
                client = await self._open_client()
                self._clients.append(_PooledClient(client=client))
                self._stats["init_count"] += 1
                log.info(
                    f"MCP_POOL_INIT | url={self._settings.sse_url} "
                    f"warm={len(self._clients)}/{self._settings.max_warm} | {ctx()}"
                )
        except Exception as e:
            log.warning(
                f"MCP_POOL_INIT_FAIL | err='{str(e)[:120]}' | "
                f"pool will retry via health check | {ctx()}"
            )
        # Background health loop — replaces dead connections.
        self._health_task = asyncio.create_task(self._health_loop())

    async def acquire(self) -> Any:
        """Return a warm client; raise PoolDisabled or TimeoutError on failure.

        Caller MUST call ``release(client)`` when done so the slot is
        returned to the pool. Use as ``async with pool.lease() as client``
        for guaranteed release.
        """
        if not self._settings.enabled:
            raise PoolDisabled(
                "MCP client pool is disabled (settings.mcp_pool.enabled=false)"
            )
        deadline = time.monotonic() + self._settings.acquire_timeout_seconds
        while True:
            async with self._lock:
                for slot in self._clients:
                    if not slot.in_use and slot.healthy:
                        slot.in_use = True
                        slot.last_used_at = time.monotonic()
                        self._stats["hits"] += 1
                        log.debug(
                            f"MCP_POOL_HIT | warm={len(self._clients)} "
                            f"hits={self._stats['hits']} | {ctx()}"
                        )
                        return slot.client
                # No idle warm slot; can we open a new one?
                if len(self._clients) < self._settings.max_warm:
                    try:
                        client = await self._open_client()
                        slot = _PooledClient(client=client, in_use=True)
                        self._clients.append(slot)
                        self._stats["init_count"] += 1
                        self._stats["hits"] += 1
                        log.info(
                            f"MCP_POOL_GROW | warm={len(self._clients)}/"
                            f"{self._settings.max_warm} | {ctx()}"
                        )
                        return slot.client
                    except Exception as e:
                        log.warning(
                            f"MCP_POOL_OPEN_FAIL | err='{str(e)[:120]}' | {ctx()}"
                        )
            if time.monotonic() >= deadline:
                self._stats["misses"] += 1
                log.warning(
                    f"MCP_POOL_MISS | timeout={self._settings.acquire_timeout_seconds}s "
                    f"warm={len(self._clients)} misses={self._stats['misses']} | {ctx()}"
                )
                raise TimeoutError("MCPClientPool.acquire timed out")
            # Brief wait before re-checking (avoid busy-loop).
            await asyncio.sleep(0.05)

    async def release(self, client: Any) -> None:
        """Return a client to the pool (idle / re-usable)."""
        async with self._lock:
            for slot in self._clients:
                if slot.client is client:
                    slot.in_use = False
                    slot.last_used_at = time.monotonic()
                    return

    async def _health_loop(self) -> None:
        """Periodic health probe — replaces dead clients."""
        while True:
            try:
                await asyncio.sleep(self._settings.health_check_interval_seconds)
            except asyncio.CancelledError:
                return
            try:
                await self._health_check()
            except Exception as e:
                log.debug(f"MCP_POOL_HEALTH_TICK_FAIL | err='{str(e)[:120]}'")

    async def _health_check(self) -> None:
        """One pass of the health check — ping each idle slot."""
        for slot in list(self._clients):
            if slot.in_use:
                continue
            healthy = await self._ping(slot.client)
            if not healthy:
                slot.healthy = False
                # Re-open the connection in place.
                try:
                    new_client = await self._open_client()
                    slot.client = new_client
                    slot.healthy = True
                    slot.created_at = time.monotonic()
                    slot.last_used_at = time.monotonic()
                    self._stats["reconnects"] += 1
                    log.warning(
                        f"MCP_POOL_RECONNECT | url={self._settings.sse_url} "
                        f"reconnects={self._stats['reconnects']} | {ctx()}"
                    )
                except Exception as e:
                    log.warning(
                        f"MCP_POOL_RECONNECT_FAIL | err='{str(e)[:120]}' | {ctx()}"
                    )

    async def _open_client(self) -> Any:
        """Open a new client connection. Override per-transport.

        Returns:
            An object representing a live connection. Today this is a
            placeholder that records the connection settings; per-
            consumer migration replaces it with a real ``mcp.Client``
            instance once the consumer wires up its acquire/release
            calls.
        """
        # Phase 23 scaffold: real SSE client wire-up happens during
        # per-consumer migration. The placeholder lets the pool track
        # its lifecycle end-to-end so the WIRING is fully exercised
        # before any consumer depends on it.
        return {"sse_url": self._settings.sse_url, "opened_at": time.monotonic()}

    async def _ping(self, _client: Any) -> bool:
        """Health-probe a client; return True if alive."""
        # Placeholder until SSE wire-up — assume healthy.
        return True

    async def shutdown(self) -> None:
        """Stop health checks and release all warm clients."""
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        async with self._lock:
            n = len(self._clients)
            self._clients.clear()
        log.info(
            f"MCP_POOL_SHUTDOWN | released={n} hits={self._stats['hits']} "
            f"misses={self._stats['misses']} reconnects={self._stats['reconnects']} | {ctx()}"
        )

    def get_stats(self) -> dict:
        """Return a snapshot of pool counters for telemetry."""
        return {
            **self._stats,
            "warm_count": len(self._clients),
            "in_use": sum(1 for s in self._clients if s.in_use),
            "enabled": self._settings.enabled,
        }


# Convenience async-context-manager: ``async with pool.lease() as c: ...``.
class _Lease:
    def __init__(self, pool: MCPClientPool):
        self._pool = pool
        self._client: Optional[Any] = None

    async def __aenter__(self) -> Any:
        self._client = await self._pool.acquire()
        return self._client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._pool.release(self._client)


def lease(pool: MCPClientPool) -> _Lease:
    """Return an async context manager that acquires/releases automatically."""
    return _Lease(pool)
