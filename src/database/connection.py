"""SQLite database connection manager with async support via aiosqlite.

Provides thread-safe, WAL-mode connections with helper methods for
common operations. Uses the repository pattern so only this file
changes during PostgreSQL migration.

Phase conn-pool/p3-9 (db-concurrency-refactor 2026-05-14):
The legacy single-aiosqlite-connection + single-asyncio.Lock engine
(``_LegacyEngine``) was removed after 2 hours of stable production on
the pooled engine with zero cascade events and 99%+ lock-wait
reduction vs the pre-cutover baseline. ``DatabaseManager`` is now a
thin facade over ``_PooledDatabaseEngine``: N reader connections in a
pool + one dedicated writer connection. The ``concurrency_model``
constructor parameter is retained for backward-compat (old callers
that pass ``"reader_pool"`` work unchanged; ``"single_lock"`` is now
rejected with a clear error message pointing at the migration).
"""

import asyncio
import time
import traceback
from collections import Counter, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from src.core.exceptions import DatabaseError
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.protected_tables import (
    ProtectedTableViolation,
    assert_not_protected_destructive,
)

log = get_logger("database")

# Phase 9 (post-Layer-1 fix): DB lock-wait instrumentation thresholds.
# A wait above DB_LOCK_WAIT_WARN_MS is the operationally interesting
# tail — sub-100 ms acquires happen constantly and don't deserve log
# noise. The HIST_SAMPLE_LIMIT bounds memory of the rolling buffer
# used for percentile reporting via ``log_lock_histogram()``.
#
# Phase 1 (D-3 fix): the warn threshold is now configurable via
# ``DatabaseSettings.db_lock_wait_threshold_ms`` — pass it to the
# DatabaseManager constructor. This module-level value remains the
# fallback for legacy/test construction sites that don't have a
# Settings reference.
DB_LOCK_WAIT_WARN_MS = 1000.0
DB_LOCK_HIST_SAMPLE_LIMIT = 1000

# T2-4 (2026-05-12): cascade-detection threshold. A DB lock wait
# above this is severe enough that downstream workers (sniper /
# watchdog) will likely go OVERDUE waiting for their next DB
# operation — the spec's F40 case captured 12.6 s waits triggering
# a cascade. CASCADE_DETECTED fires WARNING-level so operators see
# the trigger event in real-time without needing to grep for the
# downstream symptoms. Threshold is intentionally MUCH larger than
# DB_LOCK_WAIT_WARN_MS (which fires on the merely-slow tail at 1 s)
# so cascade events are rare and high-signal.
DB_CASCADE_THRESHOLD_MS = 5000.0

# Phase conn-pool/p3-2: reader-pool acquire-wait warn threshold (ms).
# Under healthy pooled operation reader acquires return immediately
# (queue has a free connection). A wait above this means the pool is
# saturated and a coroutine queued. Lower than the writer threshold
# because reader contention is operationally rare; if it happens, the
# pool size is likely undersized.
CONN_POOL_WAIT_WARN_MS = 500.0


def _extract_external_caller_frame() -> str:
    """Return the first ``file:line`` in the call stack that is OUTSIDE
    ``src/database/connection.py``.

    Phase 1 (D-3 fix). The existing ``DB_LOCK_WAIT`` log named the
    DatabaseManager method that triggered the slow acquire (executemany,
    fetch_all, etc.) but not the upstream worker that initiated the
    operation. With ~6 workers all calling these methods, operators
    couldn't tell which worker was holding things up. This helper walks
    the traceback once — only on the warn path — and returns the first
    frame outside this module so the warn carries that attribution.

    Phase conn-pool/p3-2: limit raised from 20 to 40 to skip past the
    contextlib frames that masked the worker frame in pre-refactor logs
    (``contextlib.py:204`` was the resolved frame for ~9 of the audit's
    warn lines — that's the @asynccontextmanager __aenter__).

    Returns:
        ``"<filename>:<lineno>"`` of the upstream caller, or ``"unknown"``
        on any failure (must never raise from within the lock-warn path).
    """
    try:
        stack = traceback.extract_stack(limit=40)
        # Walk from the deepest frame upward; skip our own module and
        # the contextlib wrapper.
        for frame in reversed(stack):
            if not frame.filename:
                continue
            if "database/connection.py" in frame.filename:
                continue
            if "contextlib.py" in frame.filename:
                continue
            # Use the basename + line for compactness.
            fname = frame.filename.rsplit("/", 1)[-1]
            return f"{fname}:{frame.lineno}"
    except Exception:
        pass
    return "unknown"


async def _apply_pragmas(conn: aiosqlite.Connection, *, wal_mode: bool) -> None:
    """Apply the canonical per-connection PRAGMA set to ``conn``.

    Phase conn-pool/p3-2. Extracted from the legacy ``connect()`` so
    every connection — the legacy single connection, the pooled writer,
    and every pooled reader — gets identical configuration. Drift between
    a reader and the writer here would manifest as silent inconsistency
    (e.g. one reader on an older WAL snapshot than the rest), so this
    helper exists to make that drift impossible.

    Pragma order matches the legacy code exactly so log lines diff clean.

    Args:
        conn: An already-open aiosqlite.Connection.
        wal_mode: Whether to set ``journal_mode=WAL`` (true for production;
            false for in-memory test DBs).
    """
    if wal_mode:
        await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA busy_timeout=10000")
    await conn.execute("PRAGMA foreign_keys=ON")
    # Performance pragmas: larger page cache + relaxed sync (safe under WAL).
    # cache_size=-65536 -> 64 MiB (negative = KiB). synchronous=NORMAL is the
    # SQLite-recommended pairing with WAL (durability preserved, fewer fsyncs).
    await conn.execute("PRAGMA cache_size=-65536")
    await conn.execute("PRAGMA synchronous=NORMAL")
    # Contention pragmas (Phase 4): amortise WAL checkpoint cost, cap -wal
    # file growth, push temp sort/group to memory, and memory-map 256 MiB
    # of the DB file so hot reads bypass pread syscalls. Safe under WAL.
    await conn.execute("PRAGMA wal_autocheckpoint=2000")
    await conn.execute("PRAGMA journal_size_limit=104857600")
    await conn.execute("PRAGMA temp_store=MEMORY")
    await conn.execute("PRAGMA mmap_size=268435456")


class _HolderInstrumentation:
    """Shared bookkeeping used by both engines for ``DB_LOCK_*`` emits.

    Encapsulates the wait-sample ring buffer, per-caller wait-time
    counters (bounded at 64 keys), and ``_current_holder`` /
    ``_last_holder`` tracking. Behaviour matches the legacy
    ``DatabaseManager._locked`` exactly — only the location changes.
    """

    __slots__ = (
        "current_holder",
        "last_holder",
        "wait_samples",
        "caller_wait_counts",
        "caller_wait_total_ms",
    )

    def __init__(self) -> None:
        self.current_holder: str | None = None
        self.last_holder: str | None = None
        self.wait_samples: deque[float] = deque(maxlen=DB_LOCK_HIST_SAMPLE_LIMIT)
        self.caller_wait_counts: Counter[str] = Counter()
        self.caller_wait_total_ms: Counter[str] = Counter()

    def record_acquire(self, op: str, wait_ms: float) -> str | None:
        """Update state at acquire-time. Returns the previous holder
        (the coroutine that blocked us). Bounds the per-caller counter
        dict at 64 entries; smallest contributor evicted on overflow.
        """
        self.wait_samples.append(wait_ms)
        prev_holder = self.last_holder
        self.current_holder = op
        self.last_holder = op
        self.caller_wait_counts[op] += 1
        self.caller_wait_total_ms[op] += wait_ms
        if len(self.caller_wait_counts) > 64:
            smallest = min(self.caller_wait_counts.items(), key=lambda kv: kv[1])
            self.caller_wait_counts.pop(smallest[0], None)
            self.caller_wait_total_ms.pop(smallest[0], None)
        return prev_holder

    def record_release(self) -> None:
        """Clear ``current_holder`` (the dashboard view). ``last_holder``
        is preserved so the next waiter can name who blocked them.
        """
        self.current_holder = None

    def top5_summary(self) -> str:
        """Return a comma-joined top-5 contributor summary for emit."""
        top_pairs = sorted(
            self.caller_wait_total_ms.items(), key=lambda kv: -kv[1]
        )[:5]
        if not top_pairs:
            return "none"
        return ",".join(
            f"{caller[:48]}={total_ms:.0f}ms"
            for caller, total_ms in top_pairs
        )

    def histogram_snapshot(self) -> dict[str, Any] | None:
        """Build a snapshot for ``DB_LOCK_HIST`` emission. Resets the
        per-caller counters; the wait-samples deque is NOT cleared
        (it is bounded and slides naturally).
        """
        samples = list(self.wait_samples)
        if not samples:
            return None
        samples.sort()
        n = len(samples)
        p50 = samples[int(n * 0.50)]
        p95 = samples[int(n * 0.95)] if n > 1 else samples[-1]
        max_ms = samples[-1]
        top_pairs = sorted(
            self.caller_wait_total_ms.items(), key=lambda kv: -kv[1]
        )[:5]
        top_str = ",".join(
            f"{caller}={total_ms:.0f}ms(n={self.caller_wait_counts[caller]})"
            for caller, total_ms in top_pairs
        ) or "none"
        snapshot = {
            "n": n,
            "p50": p50,
            "p95": p95,
            "max_ms": max_ms,
            "current_holder": self.current_holder,
            "top_callers": top_str,
        }
        # Reset per-caller counters so the next emit window is independent.
        self.caller_wait_counts.clear()
        self.caller_wait_total_ms.clear()
        return snapshot


class _ReaderPool:
    """Bounded pool of aiosqlite.Connection instances used for SELECTs.

    Phase conn-pool/p3-2. The pool exposes an ``asyncio.Queue``-backed
    acquire/release API. Initial size is ``size``; dynamic growth up to
    ``hard_cap`` is allowed when an acquire would otherwise block (no
    free reader available). ``CONN_POOL_EXHAUSTED`` warns when a waiter
    queues even at hard_cap.

    Connections are returned to the queue on release (not closed). On
    process shutdown ``close()`` drains and closes all connections.
    """

    def __init__(
        self,
        db_path: str,
        *,
        size: int,
        hard_cap: int,
        wal_mode: bool,
    ) -> None:
        if size < 1:
            raise ValueError(f"reader pool size must be >= 1, got {size}")
        if hard_cap < size:
            raise ValueError(
                f"reader pool hard_cap ({hard_cap}) must be >= size ({size})"
            )
        self.db_path = db_path
        self.size = size
        self.hard_cap = hard_cap
        self.wal_mode = wal_mode
        # Connections currently owned by this pool (regardless of whether
        # they're checked out or available).
        self._conns: list[aiosqlite.Connection] = []
        # Available connections (free for immediate acquire). Items are
        # subset of ``_conns``.
        self._available: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        # Coordination lock for growth (prevents two concurrent acquires
        # from both deciding to grow).
        self._grow_lock = asyncio.Lock()
        # Stats counters surfaced via ``stats()``.
        self.acquires: int = 0
        self.waits_total_ms: float = 0.0
        self.exhausted_count: int = 0
        self.growths: int = 0
        self.reconnects: int = 0
        self.peak_in_use: int = 0

    async def open(self) -> None:
        """Open ``size`` reader connections."""
        for _ in range(self.size):
            conn = await self._make_conn()
            self._conns.append(conn)
            self._available.put_nowait(conn)

    async def close(self) -> None:
        """Drain the queue and close every connection. Called once at
        shutdown.
        """
        for conn in self._conns:
            try:
                await conn.close()
            except Exception as e:
                log.warning(
                    f"CONN_POOL_CLOSE_ERR | err='{str(e)[:120]}' | {ctx()}"
                )
        self._conns.clear()
        # Drain queue (best-effort).
        while not self._available.empty():
            try:
                self._available.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _make_conn(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        await _apply_pragmas(conn, wal_mode=self.wal_mode)
        return conn

    async def acquire(self) -> tuple[aiosqlite.Connection, float]:
        """Acquire a reader connection. Returns ``(conn, wait_ms)``.

        Grow strategy: if no connection is available and we haven't hit
        ``hard_cap`` yet, open a new connection rather than waiting.
        ``CONN_POOL_GROW`` info-level log fires on growth. Beyond
        hard_cap, the call blocks on the queue and ``CONN_POOL_EXHAUSTED``
        warns once on the queue transition.
        """
        t0 = time.monotonic()
        # Fast path: free connection available right now.
        if not self._available.empty():
            conn = self._available.get_nowait()
            wait_ms = (time.monotonic() - t0) * 1000.0
            self._record_acquire(wait_ms)
            return conn, wait_ms
        # Try to grow if below hard_cap. Coordinated to avoid two waiters
        # both opening new connections.
        async with self._grow_lock:
            if not self._available.empty():
                conn = self._available.get_nowait()
                wait_ms = (time.monotonic() - t0) * 1000.0
                self._record_acquire(wait_ms)
                return conn, wait_ms
            if len(self._conns) < self.hard_cap:
                new_conn = await self._make_conn()
                self._conns.append(new_conn)
                self.growths += 1
                log.info(
                    f"CONN_POOL_GROW | from={len(self._conns) - 1} "
                    f"to={len(self._conns)} hard_cap={self.hard_cap} | {ctx()}"
                )
                wait_ms = (time.monotonic() - t0) * 1000.0
                self._record_acquire(wait_ms)
                return new_conn, wait_ms
        # Hard cap reached. Queue and wait. Warn once per exhaustion.
        self.exhausted_count += 1
        log.warning(
            f"CONN_POOL_EXHAUSTED | size={self.size} hard_cap={self.hard_cap} "
            f"in_flight={self.hard_cap} acquires={self.acquires} | {ctx()}"
        )
        conn = await self._available.get()
        wait_ms = (time.monotonic() - t0) * 1000.0
        self._record_acquire(wait_ms)
        return conn, wait_ms

    def release(self, conn: aiosqlite.Connection) -> None:
        """Return a connection to the pool. Called from the locked()
        finally block so this must NOT be async.
        """
        # If the connection is no longer in our pool (e.g. was replaced
        # during a reconnect), close it directly.
        if conn not in self._conns:
            try:
                asyncio.create_task(conn.close())
            except Exception:
                pass
            return
        self._available.put_nowait(conn)

    def _record_acquire(self, wait_ms: float) -> None:
        self.acquires += 1
        self.waits_total_ms += wait_ms
        # in_use = total - available in queue (approximate; readable)
        in_use = len(self._conns) - self._available.qsize()
        if in_use > self.peak_in_use:
            self.peak_in_use = in_use

    def stats(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "hard_cap": self.hard_cap,
            "owned": len(self._conns),
            "available": self._available.qsize(),
            "in_use": len(self._conns) - self._available.qsize(),
            "peak_in_use": self.peak_in_use,
            "acquires": self.acquires,
            "waits_total_ms": self.waits_total_ms,
            "exhausted_count": self.exhausted_count,
            "growths": self.growths,
            "reconnects": self.reconnects,
        }


class _PooledDatabaseEngine:
    """Reader pool + single writer connection engine.

    Phase conn-pool/p3-1. Selected when
    ``settings.database.concurrency_model == "reader_pool"``.

    - Reads route to a pooled reader connection (concurrent reads OK).
    - Writes serialise on a single writer connection guarded by an
      ``asyncio.Lock`` (matches SQLite's single-writer WAL semantics).
    - ``transaction()`` holds the writer lock for the duration.
    - ``checkpoint()`` runs on the writer (PRAGMA wal_checkpoint must
      run on a writer in WAL mode).
    """

    def __init__(
        self,
        db_path: str,
        *,
        wal_mode: bool,
        reader_pool_size: int,
        lock_wait_warn_ms: float,
    ) -> None:
        self.db_path = db_path
        self.wal_mode = wal_mode
        self._writer: aiosqlite.Connection | None = None
        self._writer_lock = asyncio.Lock()
        self._writer_inst = _HolderInstrumentation()
        self._writer_lock_wait_warn_ms = float(lock_wait_warn_ms)
        self._reader_inst = _HolderInstrumentation()
        self._pool = _ReaderPool(
            db_path,
            size=reader_pool_size,
            hard_cap=2 * reader_pool_size,
            wal_mode=wal_mode,
        )

    @property
    def writer(self) -> aiosqlite.Connection:
        if self._writer is None:
            raise DatabaseError("Database not connected. Call connect() first.")
        return self._writer

    async def connect(self) -> None:
        self._writer = await aiosqlite.connect(self.db_path)
        self._writer.row_factory = aiosqlite.Row
        await _apply_pragmas(self._writer, wal_mode=self.wal_mode)
        await self._pool.open()
        log.info(
            f"CONN_POOL_INIT | readers={self._pool.size} "
            f"hard_cap={self._pool.hard_cap} writer=ready | {ctx()}"
        )

    async def disconnect(self) -> None:
        if self._writer:
            await self._writer.close()
            self._writer = None
        await self._pool.close()

    @asynccontextmanager
    async def writer_locked(
        self, op: str
    ) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire the writer connection (single asyncio.Lock). Yields
        the writer connection. ``WRITER_LOCK_WAIT`` warns above threshold.
        """
        t0 = time.monotonic()
        await self._writer_lock.acquire()
        wait_ms = (time.monotonic() - t0) * 1000.0
        prev_holder = self._writer_inst.record_acquire(op, wait_ms)
        if wait_ms >= self._writer_lock_wait_warn_ms:
            _emit_lock_wait_warn(
                wait_ms=wait_ms,
                prev_holder=prev_holder,
                op=op,
                warn_threshold_ms=self._writer_lock_wait_warn_ms,
                tag="WRITER_LOCK_WAIT",
                inst=self._writer_inst,
            )
        try:
            yield self.writer
        finally:
            self._writer_inst.record_release()
            self._writer_lock.release()

    @asynccontextmanager
    async def reader_acquired(
        self, op: str
    ) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire a reader connection from the pool. Yields the reader.
        Records pool-wait metrics on the shared ``_reader_inst``.
        """
        conn, wait_ms = await self._pool.acquire()
        prev_holder = self._reader_inst.record_acquire(op, wait_ms)
        if wait_ms >= CONN_POOL_WAIT_WARN_MS:
            # Reader-wait is operationally less severe than writer-lock
            # contention (no exclusive write held), so emit at INFO unless
            # very long. CONN_POOL_EXHAUSTED already warned at hard_cap.
            level = log.warning if wait_ms >= 2000 else log.info
            caller_frame = _extract_external_caller_frame()
            level(
                f"CONN_POOL_WAIT | wait_ms={wait_ms:.0f} "
                f"prev_holder={prev_holder or 'none'} caller={op} "
                f"frame={caller_frame} threshold_ms={CONN_POOL_WAIT_WARN_MS:.0f} "
                f"| {ctx()}"
            )
        try:
            yield conn
        finally:
            self._reader_inst.record_release()
            self._pool.release(conn)

    def log_lock_histogram(self) -> None:
        """Emit DB_LOCK_HIST for the writer side + CONN_POOL_STATS for
        the reader pool.
        """
        writer_snap = self._writer_inst.histogram_snapshot()
        if writer_snap is not None:
            log.info(
                f"DB_LOCK_HIST | n={writer_snap['n']} "
                f"p50={writer_snap['p50']:.0f}ms p95={writer_snap['p95']:.0f}ms "
                f"max={writer_snap['max_ms']:.0f}ms "
                f"current_holder={writer_snap['current_holder'] or 'none'} "
                f"top_callers=[{writer_snap['top_callers']}] | {ctx()}"
            )
        reader_snap = self._reader_inst.histogram_snapshot()
        stats = self._pool.stats()
        if reader_snap is not None or stats["acquires"] > 0:
            avg_wait_ms = (
                (stats["waits_total_ms"] / stats["acquires"])
                if stats["acquires"] > 0
                else 0.0
            )
            log.info(
                f"CONN_POOL_STATS | size={stats['size']} owned={stats['owned']} "
                f"available={stats['available']} in_use={stats['in_use']} "
                f"peak_in_use={stats['peak_in_use']} acquires={stats['acquires']} "
                f"avg_wait_ms={avg_wait_ms:.1f} "
                f"exhausted={stats['exhausted_count']} growths={stats['growths']} "
                f"reconnects={stats['reconnects']} | {ctx()}"
            )


def _emit_lock_wait_warn(
    *,
    wait_ms: float,
    prev_holder: str | None,
    op: str,
    warn_threshold_ms: float,
    tag: str,
    inst: _HolderInstrumentation,
) -> None:
    """Centralized DB_LOCK_WAIT / WRITER_LOCK_WAIT + CASCADE_DETECTED emit.

    Phase conn-pool/p3-2. Pulled out of the legacy ``_locked`` so both
    engines emit identical lines (operators grepping for ``DB_LOCK_WAIT``
    keep their existing tooling; pooled mode also surfaces
    ``WRITER_LOCK_WAIT`` separately to make residual writer contention
    distinguishable from pre-refactor reads-block-reads noise).
    """
    caller_frame = _extract_external_caller_frame()
    log.warning(
        f"{tag} | wait_ms={wait_ms:.0f} "
        f"holder={prev_holder or 'none'} caller={op} "
        f"frame={caller_frame} threshold_ms={warn_threshold_ms:.0f} "
        f"| {ctx()}"
    )
    if wait_ms >= DB_CASCADE_THRESHOLD_MS:
        log.warning(
            f"CASCADE_DETECTED | trigger={tag.lower()} "
            f"duration_ms={wait_ms:.0f} "
            f"threshold_ms={DB_CASCADE_THRESHOLD_MS:.0f} "
            f"holder={prev_holder or 'none'} caller={op} "
            f"frame={caller_frame} "
            f"expected_downstream=sniper_overdue,watchdog_poll_lag "
            f"| {ctx()}"
        )
        # Top-5 contributor breakdown — same format as the legacy emit.
        log.warning(
            f"DB_LOCK_BREAKDOWN | trigger=cascade "
            f"total_callers={len(inst.caller_wait_total_ms)} "
            f"top5={inst.top5_summary()} | {ctx()}"
        )


class DatabaseManager:
    """Async SQLite connection manager — pooled-engine facade.

    Phase conn-pool/p3-9 (db-concurrency-refactor 2026-05-14):
    ``_LegacyEngine`` was removed after 2 hours of stable production on
    the pooled engine. ``DatabaseManager`` now always constructs a
    ``_PooledDatabaseEngine`` (N reader connections + one writer
    connection guarded by ``asyncio.Lock``). All existing callers (117
    importing files, 477 call sites) see an unchanged public API: the
    six methods (``execute``, ``executemany``, ``fetch_one``,
    ``fetch_all``, ``transaction``, ``checkpoint``) plus the
    backward-compat instrumentation properties.

    Args:
        db_path: Path to the SQLite database file.
        wal_mode: Enable WAL mode for concurrent reads during writes.
        lock_wait_warn_ms: Writer-lock-acquire wait threshold (ms) above
            which a ``WRITER_LOCK_WAIT`` warning is emitted. Plumbed
            from ``settings.database.db_lock_wait_threshold_ms``.
        concurrency_model: Retained for backward compat. Must be
            ``"reader_pool"`` (the only supported engine since
            ``_LegacyEngine`` was removed). Passing ``"single_lock"``
            raises ``DatabaseError`` with a migration message.
        reader_pool_size: Number of reader connections opened at boot.
            Hard cap on dynamic growth is ``2 * reader_pool_size``.
    """

    def __init__(
        self,
        db_path: str,
        wal_mode: bool = True,
        lock_wait_warn_ms: float = DB_LOCK_WAIT_WARN_MS,
        concurrency_model: str = "reader_pool",
        reader_pool_size: int = 4,
    ) -> None:
        self.db_path = db_path
        self.wal_mode = wal_mode
        self._lock_wait_warn_ms = float(lock_wait_warn_ms)
        self.concurrency_model = concurrency_model
        self.reader_pool_size = int(reader_pool_size)
        if concurrency_model == "single_lock":
            raise DatabaseError(
                "concurrency_model='single_lock' is no longer supported "
                "(removed Phase conn-pool/p3-9 2026-05-14). The pooled "
                "engine is the only supported engine. Update config.toml "
                "[database].concurrency_model to 'reader_pool' (env var: "
                "DATABASE_CONCURRENCY_MODEL=reader_pool).",
                details={"value": concurrency_model},
            )
        if concurrency_model != "reader_pool":
            raise DatabaseError(
                f"Unknown concurrency_model: {concurrency_model!r}. "
                "Expected 'reader_pool'.",
                details={"value": concurrency_model},
            )
        self._engine: _PooledDatabaseEngine = _PooledDatabaseEngine(
            db_path,
            wal_mode=wal_mode,
            reader_pool_size=self.reader_pool_size,
            lock_wait_warn_ms=self._lock_wait_warn_ms,
        )

    async def connect(self) -> None:
        """Open the database connection(s) and configure pragmas."""
        try:
            await self._engine.connect()
            # T1-4 / F4 (six-tier-fixes 2026-05-11) — surface auto_vacuum
            # status at connect time. The cleanup_worker uses PRAGMA
            # incremental_vacuum(N) to reclaim freelist pages hourly with
            # no exclusive-lock cascade; this requires auto_vacuum=2
            # (INCREMENTAL). A 0 (NONE) or 1 (FULL) value means the
            # one-time migration has not yet run on this DB file. Surface
            # at WARN so operators see it on the next restart; the
            # cleanup_worker repeats the warning daily until migrated.
            #
            # Phase conn-pool/p3-1: the probe runs against whichever
            # primary connection the engine surfaces. For the legacy
            # engine this is the only connection. For the pooled engine
            # this is the writer; readers share the same DB file so the
            # mode applies engine-wide.
            try:
                probe_conn = self._primary_connection()
                _av_cur = await probe_conn.execute("PRAGMA auto_vacuum")
                _av_row = await _av_cur.fetchone()
                await _av_cur.close()
                _av_mode = int((_av_row or (0,))[0])
                if _av_mode != 2:
                    log.warning(
                        f"DB_AUTO_VACUUM_NOT_INCREMENTAL | "
                        f"current_mode={_av_mode} expected=2 | "
                        f"run scripts/t1_4_migrate_to_incremental_vacuum.sh "
                        f"once to enable hourly PRAGMA incremental_vacuum "
                        f"reclamation | {ctx()}"
                    )
                else:
                    log.info(
                        f"DB_AUTO_VACUUM_OK | mode=INCREMENTAL | {ctx()}"
                    )
            except Exception as _ave:
                log.debug(
                    f"DB_AUTO_VACUUM_PROBE_FAIL | err='{str(_ave)[:120]}'"
                )
            log.info(
                f"DB_CONN | path={self.db_path} "
                f"wal={'Y' if self.wal_mode else 'N'} "
                f"engine={self.concurrency_model} | {ctx()}"
            )
            log.info(
                f"DB_PRAGMAS | journal_mode={'WAL' if self.wal_mode else 'DELETE'} "
                f"cache_size=64MiB synchronous=NORMAL busy_timeout=10000ms "
                f"foreign_keys=ON | {ctx()}"
            )
            log.info(
                f"DB_PRAGMA | wal_autocheckpoint=2000 jsize_lim=100MiB "
                f"temp_store=MEMORY mmap_size=256MiB | {ctx()}"
            )
            log.info("Database connected: {path}", path=self.db_path)
        except Exception as e:
            raise DatabaseError(
                f"Failed to connect to database: {e}",
                details={"path": self.db_path},
            )

    async def disconnect(self) -> None:
        """Close the database connection(s)."""
        try:
            await self._engine.disconnect()
        finally:
            log.info("Database disconnected")

    def _primary_connection(self) -> aiosqlite.Connection:
        """Return the writer connection. Used for one-shot probes
        (auto_vacuum) at connect time only — production code paths use
        the public ``execute`` / ``fetch_*`` methods which dispatch
        through the engine's locking.
        """
        return self._engine.writer

    @property
    def db(self) -> aiosqlite.Connection:
        """Return the writer connection, raising if not connected.

        Phase conn-pool/p3-1: under the legacy engine this is the single
        shared connection (unchanged contract). Under the pooled engine
        this is the writer. **Callers should not hold this reference
        across awaits** — public methods on ``DatabaseManager`` are the
        supported API. The property is preserved for the few internal
        sites that grabbed the raw connection in the past.
        """
        return self._primary_connection()

    @property
    def _db(self) -> aiosqlite.Connection | None:
        """Backward-compat: return the writer connection or ``None`` if
        not yet connected.

        Phase conn-pool/p3-1: pre-refactor, ``DatabaseManager._db`` was a
        direct instance attribute holding the single ``aiosqlite.Connection``
        (or None). The MCP system-status tool (and possibly other call
        sites) checks ``db._db is not None`` to detect connectivity. The
        attribute now lives inside ``_PooledDatabaseEngine`` — this
        property re-exposes it without raising on the disconnected path,
        preserving the ``is not None`` idiom.
        """
        return self._engine._writer

    # ------------------------------------------------------------------
    # Phase conn-pool/p3-1 backward-compat: instrumentation state used to
    # live on ``DatabaseManager`` itself (pre-refactor). It now lives on
    # the active engine's ``_HolderInstrumentation``. Tests and observability
    # tooling that read these names continue to work via these properties.
    # Under the pooled engine, the WRITER-side instrumentation is the
    # closest analog to the legacy single lock (the writer lock guards the
    # same write-path semantics the single lock used to guard).
    # ------------------------------------------------------------------

    def _active_instrumentation(self) -> "_HolderInstrumentation":
        """Return the writer-side instrumentation object. Used by the
        backward-compat properties that pre-refactor read directly from
        ``DatabaseManager`` instance state (now relocated to the engine's
        ``_HolderInstrumentation``).
        """
        return self._engine._writer_inst

    @property
    def _caller_wait_counts(self) -> Counter[str]:
        """Per-caller acquire-count counter (bounded at 64 keys)."""
        return self._active_instrumentation().caller_wait_counts

    @property
    def _caller_wait_total_ms(self) -> Counter[str]:
        """Per-caller cumulative wait-time counter (ms)."""
        return self._active_instrumentation().caller_wait_total_ms

    @property
    def _wait_samples(self) -> deque[float]:
        """Bounded ring buffer of lock-acquire wait times (ms)."""
        return self._active_instrumentation().wait_samples

    @property
    def _current_holder(self) -> str | None:
        """Op tag of the coroutine currently holding the lock, or None."""
        return self._active_instrumentation().current_holder

    @property
    def _last_holder(self) -> str | None:
        """Op tag of the coroutine that most recently held the lock."""
        return self._active_instrumentation().last_holder

    @asynccontextmanager
    async def _writer_locked(
        self, op: str
    ) -> AsyncIterator[aiosqlite.Connection]:
        """Internal: acquire the writer lock + yield the writer connection."""
        async with self._engine.writer_locked(op) as conn:
            yield conn

    @asynccontextmanager
    async def _reader_acquired(
        self, op: str
    ) -> AsyncIterator[aiosqlite.Connection]:
        """Internal: acquire a pool reader + yield it."""
        async with self._engine.reader_acquired(op) as conn:
            yield conn

    def log_lock_histogram(self) -> None:
        """Emit periodic histogram. ``DB_LOCK_HIST`` from both engines;
        ``CONN_POOL_STATS`` from the pooled engine additionally.

        Called from a maintenance worker (cleanup_worker) at whatever
        cadence the operator wants visibility.
        """
        self._engine.log_lock_histogram()

    async def execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        force_protected: bool = False,
    ) -> aiosqlite.Cursor:
        """Execute a single SQL statement with automatic retry on lock.

        Phase 0a defense-in-depth: any DELETE/TRUNCATE/DROP targeting a
        PROTECTED table (see ``src.database.protected_tables``) is refused
        BEFORE the lock is acquired, so the failure cannot be hidden by
        the busy-retry loop. Pass ``force_protected=True`` only with
        explicit authorization for a documented maintenance scenario.

        Args:
            sql: SQL query string.
            params: Query parameters.
            force_protected: Bypass the protected-table guard (logged loudly).

        Returns:
            The cursor after execution.
        """
        # Pre-flight guard — raises ProtectedTableViolation on hit; no retry.
        assert_not_protected_destructive(sql, force=force_protected)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with self._writer_locked(f"execute:{sql[:48]}") as conn:
                    cursor = await conn.execute(sql, params)
                    await conn.commit()
                    return cursor
            except ProtectedTableViolation:
                # Defensive: should not reach here (pre-flight raised), but
                # if a future code path triggers the guard from inside the
                # driver, do NOT retry — protected violations are terminal.
                raise
            except Exception as e:
                err_str = str(e).lower()
                if "locked" in err_str and attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                log.error(f"DB_ERR | err='{str(e)[:150]}' sql='{sql[:80]}' | {ctx()}")
                raise DatabaseError(f"Execute failed: {e}", details={"sql": sql[:200]})

    async def executemany(
        self,
        sql: str,
        params_list: list[tuple[Any, ...]],
        *,
        force_protected: bool = False,
    ) -> None:
        """Execute a SQL statement for each set of params with retry on lock.

        Args:
            sql: SQL query string with placeholders.
            params_list: List of parameter tuples.
            force_protected: Bypass the protected-table guard (logged loudly).
        """
        # Pre-flight guard — raises ProtectedTableViolation on hit; no retry.
        assert_not_protected_destructive(sql, force=force_protected)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with self._writer_locked(f"executemany:{sql[:48]}") as conn:
                    await conn.executemany(sql, params_list)
                    await conn.commit()
                    return
            except ProtectedTableViolation:
                raise
            except Exception as e:
                err_str = str(e).lower()
                if "locked" in err_str and attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise DatabaseError(f"Executemany failed: {e}", details={"sql": sql[:200]})

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Fetch a single row as a dict.

        Args:
            sql: SELECT query.
            params: Query parameters.

        Returns:
            Dict of column_name -> value, or None if no row found.
        """
        try:
            async with self._reader_acquired(f"fetch_one:{sql[:48]}") as conn:
                cursor = await conn.execute(sql, params)
                row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)
        except Exception as e:
            raise DatabaseError(f"Fetch one failed: {e}", details={"sql": sql[:200]})

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Fetch all rows as a list of dicts.

        Args:
            sql: SELECT query.
            params: Query parameters.

        Returns:
            List of dicts.
        """
        try:
            async with self._reader_acquired(f"fetch_all:{sql[:48]}") as conn:
                cursor = await conn.execute(sql, params)
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            raise DatabaseError(f"Fetch all failed: {e}", details={"sql": sql[:200]})

    async def checkpoint(self, mode: str = "PASSIVE") -> dict[str, int]:
        """Run ``PRAGMA wal_checkpoint(<mode>)`` and log the result.

        Phase 2 of the post-Layer-1 fix work. The ``-wal`` file was
        observed pinned at the configured 100 MiB cap during long-running
        live observation: SQLite's auto-checkpoint (every 2000 frames by
        default in this database) only opportunistically truncates when
        no readers hold an open snapshot. With a heavily-loaded
        DatabaseManager seeing reads and writes interleaved at a fast
        cadence, those snapshots can keep the WAL pinned indefinitely.

        Calling ``wal_checkpoint(PASSIVE)`` from a quiet tick gives the
        SQLite engine an explicit opportunity to truncate without
        blocking new readers/writers. ``mode``:

        - ``PASSIVE`` (default) — never blocks; truncates only what's
          safe right now. Best for routine maintenance.
        - ``FULL`` / ``RESTART`` / ``TRUNCATE`` — increasingly aggressive;
          may briefly block writers. Reserve for explicit reclamation.

        Phase conn-pool/p3-1: runs on the writer connection in pooled
        mode (PRAGMA wal_checkpoint must be issued from a writer).

        Returns:
            ``{"busy": int, "log_pages": int, "ckpt_pages": int, "mode": str}``.
            ``busy`` is non-zero when a reader prevented full checkpoint.
            ``log_pages`` and ``ckpt_pages`` come straight from SQLite.
        """
        async with self._writer_locked(f"checkpoint:{mode}") as conn:
            cur = await conn.execute(f"PRAGMA wal_checkpoint({mode})")
            row = await cur.fetchone()
        # SQLite returns three columns: busy, log frames, checkpointed frames.
        if row is None:
            log.warning(f"WAL_CHECKPOINT_NORESULT | mode={mode} | {ctx()}")
            return {"busy": -1, "log_pages": -1, "ckpt_pages": -1, "mode": mode}
        busy, log_pages, ckpt_pages = row[0], row[1], row[2]
        out = {
            "busy": int(busy),
            "log_pages": int(log_pages),
            "ckpt_pages": int(ckpt_pages),
            "mode": mode,
        }
        if busy:
            log.warning(
                f"WAL_CHECKPOINT_BUSY | mode={mode} busy={busy} "
                f"log={log_pages} ckpt={ckpt_pages} | {ctx()}"
            )
        else:
            log.info(
                f"WAL_CHECKPOINT | mode={mode} busy={busy} "
                f"log={log_pages} ckpt={ckpt_pages} | {ctx()}"
            )
        return out

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager for explicit transactions.

        Commits on success, rolls back on exception.

        Phase conn-pool/p3-1: holds the writer lock for the duration of
        the context. Matches SQLite's single-writer WAL semantics; under
        the legacy engine this is the only lock. Note: ``transaction()``
        is defined but currently has zero callers in ``src/`` and
        ``tests/`` (confirmed via grep at commit 461f7c6).
        """
        async with self._writer_locked("transaction") as conn:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
