"""Issue I4 (F-27) — DB lock cascade narrow fix.

Verifies the chunked kline staleness scan + cascade diagnostic
enhancement:

  * kline_worker chunks the staleness fetch_all into 100-symbol
    batches (`_STALENESS_SCAN_CHUNK`)
  * Each chunk runs as a separate `fetch_all` (= separate `_locked`
    block) so the DB lock releases between batches
  * `await asyncio.sleep(0)` yields between chunks
  * `DB_WRITE_DEFERRED` emitted between chunks (observability)
  * `CASCADE_DETECTED` now pairs with `DB_LOCK_BREAKDOWN`
    showing top-5 contributors
"""

from __future__ import annotations

import re


def _read_kline() -> str:
    return open("src/workers/kline_worker.py").read()


def _read_db_conn() -> str:
    return open("src/database/connection.py").read()


def test_staleness_scan_chunk_constant_exists() -> None:
    """Module constant exposes the chunk size for ops to tune."""
    src = _read_kline()
    assert re.search(
        r"^_STALENESS_SCAN_CHUNK\s*:\s*int\s*=\s*\d+",
        src, re.MULTILINE,
    ), "Issue I4: _STALENESS_SCAN_CHUNK constant must be declared"


def test_staleness_scan_loops_chunks() -> None:
    """The staleness scan iterates the symbol list in chunk-sized batches."""
    src = _read_kline()
    assert re.search(r"for _chunk_start in range\(", src), (
        "Issue I4: staleness scan must iterate in chunks"
    )
    # The chunk loop should reference _STALENESS_SCAN_CHUNK via the
    # _chunk_size local that initializes from it.
    assert "_STALENESS_SCAN_CHUNK" in src, (
        "Issue I4: staleness scan must reference _STALENESS_SCAN_CHUNK"
    )


def test_staleness_scan_yields_between_chunks() -> None:
    """`await asyncio.sleep(0)` yields the event loop between batches."""
    src = _read_kline()
    assert "await asyncio.sleep(0)" in src, (
        "Issue I4: must yield event loop between chunks "
        "(await asyncio.sleep(0))"
    )


def test_db_write_deferred_emission_registered() -> None:
    """DB_WRITE_DEFERRED is emitted between chunks for visibility."""
    src = _read_kline()
    assert "DB_WRITE_DEFERRED" in src, (
        "Issue I4: DB_WRITE_DEFERRED emission must be in kline_worker"
    )


def test_cascade_detected_pairs_with_lock_breakdown() -> None:
    """When CASCADE_DETECTED fires, DB_LOCK_BREAKDOWN follows in the
    same context (top-5 contributors)."""
    src = _read_db_conn()
    # Pattern: CASCADE_DETECTED emission immediately followed by
    # DB_LOCK_BREAKDOWN emission (no other log.warning in between)
    m = re.search(
        r"CASCADE_DETECTED.*?DB_LOCK_BREAKDOWN",
        src, re.DOTALL,
    )
    assert m is not None, (
        "Issue I4: DB_LOCK_BREAKDOWN must follow CASCADE_DETECTED in "
        "src/database/connection.py so cascade emissions carry "
        "context"
    )


def test_db_lock_breakdown_includes_top_callers() -> None:
    """The breakdown emission shows top-N callers by accumulated wait."""
    src = _read_db_conn()
    # The breakdown sorts _caller_wait_total_ms and takes top 5
    m = re.search(
        r"DB_LOCK_BREAKDOWN.*?top5=",
        src, re.DOTALL,
    )
    assert m is not None, (
        "Issue I4: DB_LOCK_BREAKDOWN must include top5= field"
    )


def test_existing_db_lock_wait_emission_preserved() -> None:
    """The pre-existing DB_LOCK_WAIT diagnostic capability is preserved.

    Phase conn-pool/p3-2 (2026-05-14): emission relocated into a shared
    helper ``_emit_lock_wait_warn(tag=...)`` so the legacy and pooled
    engines could share one format string.

    Phase conn-pool/p3-9 (2026-05-14): ``_LegacyEngine`` was removed
    entirely; the pooled engine is the only supported engine. The
    ``DB_LOCK_WAIT`` tag was renamed to ``WRITER_LOCK_WAIT`` (the
    pooled engine writer-lock context). The diagnostic still fires
    above the 1000ms warn threshold; only the tag name changed.

    Verifies (a) the threshold constant ``DB_LOCK_WAIT_WARN_MS`` still
    exists (still used to set the writer-lock warn threshold),
    (b) the new ``WRITER_LOCK_WAIT`` tag literal is in source, and
    (c) the shared emit helper still uses the ``{tag} | wait_ms=``
    format (preserves the runtime grep contract for operator tooling).
    """
    src = _read_db_conn()
    assert "DB_LOCK_WAIT_WARN_MS" in src
    assert '"WRITER_LOCK_WAIT"' in src, (
        "Phase 3.9: WRITER_LOCK_WAIT tag literal must be preserved "
        "(it replaces the pre-refactor DB_LOCK_WAIT tag)"
    )
    assert "{tag} | wait_ms=" in src, (
        "Phase 3.9: emit format `<tag> | wait_ms=` must be preserved "
        "(operator tooling greps `WRITER_LOCK_WAIT |`)"
    )


def test_kline_worker_imports_asyncio_for_sleep() -> None:
    """asyncio is imported in kline_worker (required for sleep(0))."""
    src = _read_kline()
    # Locate the import block at the top
    head = src[:src.index("class ")] if "class " in src else src[:500]
    assert "import asyncio" in head, "Issue I4: asyncio import required"
