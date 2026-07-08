"""Layer 4 Commit 4 — Rule 16 self-checks (no hardcoded cap + brain-size-
by-agreement + herding monitor).

The Layer 4 fix is a TRUTH-FIX, not a blocking fix. Rule 16 requires
self-checks that GUARD this distinction automatically:

  (a) BOOT_L4_NO_HARDCODED_CAP — a regex scan at worker boot that
      asserts no non-comment code path mutates size based on a
      consensus count. Catches the cardinal anti-pattern before any
      cycle runs.

  (b) L4_BRAIN_SIZE_BY_AGREEMENT — hourly group-by aggregator that
      logs avg brain-chosen size per (narrow/moderate/broad)
      supporting-count bucket. Success signal: avg size on the 6+
      bucket drops below the 4-5 bucket. If it doesn't, the brain
      is ignoring the truthful framing (Rule 11 finding).

  (c) L4_HERDING_MONITOR — hourly group-by aggregator that logs
      avg pnl_pct per supporting-count bucket. Ongoing measurement
      of whether the herding effect persists, shrinks, or inverts.

  (d) L4_BRAIN_INVERTED_SIZING — fires at WARNING when (b) shows
      the brain still sizes UP on crowded trades (broad > moderate).
      Visible signal that the truthful framing is not changing
      the brain's sizing.
"""
from __future__ import annotations

import io
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest


@contextmanager
def capture_logs():
    from loguru import logger
    buf = io.StringIO()
    hid = logger.add(buf, level="DEBUG", format="{level} | {message}")
    try:
        yield buf
    finally:
        logger.remove(hid)


def test_boot_check_passes_on_clean_live_source() -> None:
    """The live strategy_worker.py + layer_manager.py must pass the
    boot regex scan today — zero violations of the anti-pattern."""
    import re
    from pathlib import Path
    _src_root = Path("/home/inshadaliqbal786/trading-intelligence-mcp/src")
    _targets = [
        _src_root / "workers" / "strategy_worker.py",
        _src_root / "core" / "layer_manager.py",
    ]
    _consensus_re = re.compile(r"\b(supporting_count|agreeing|opposing)\b")
    _size_mutation_re = re.compile(
        r"(size_usd\s*[*+\-]?=|qty\s*[*+\-]?=|"
        r"size\s*[*+\-]?=\s*[^=]|_size_mult\s*=)"
    )
    violations = []
    for path in _targets:
        for lineno, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1,
        ):
            stripped = raw.lstrip()
            if stripped.startswith("#"):
                continue
            if "log." in stripped:
                continue
            if '"""' in stripped or "'''" in stripped:
                continue
            if not _consensus_re.search(raw):
                continue
            if not _size_mutation_re.search(raw):
                continue
            violations.append(f"{path.name}:{lineno}")
    assert violations == [], (
        f"Live source violates Layer 4 anti-pattern: {violations}. "
        f"A line mutates size based on consensus count — that "
        f"hardcodes the sizing decision and betrays the Layer 4 aim. "
        f"Investigate."
    )


def test_boot_check_detects_synthetic_violation(tmp_path) -> None:
    """Synthetic source file with the forbidden pattern → the check
    must detect it. Verifies the regex actually triggers on the
    cardinal anti-pattern."""
    import re
    bad = tmp_path / "synthetic_bad.py"
    # The exact pattern Layer 4 forbids: an if on consensus count
    # that then mutates size.
    bad.write_text(
        "def execute(trade, supporting_count):\n"
        "    size_usd = 100\n"
        "    if supporting_count > 5: size_usd *= 0.5\n"  # <- VIOLATION
        "    return size_usd\n"
    )
    _consensus_re = re.compile(r"\b(supporting_count|agreeing|opposing)\b")
    _size_mutation_re = re.compile(
        r"(size_usd\s*[*+\-]?=|qty\s*[*+\-]?=|"
        r"size\s*[*+\-]?=\s*[^=]|_size_mult\s*=)"
    )
    matches = []
    for lineno, raw in enumerate(
        bad.read_text(encoding="utf-8").splitlines(), 1,
    ):
        stripped = raw.lstrip()
        if stripped.startswith("#") or "log." in stripped:
            continue
        if not _consensus_re.search(raw):
            continue
        if not _size_mutation_re.search(raw):
            continue
        matches.append(lineno)
    assert len(matches) == 1, (
        f"Forbidden pattern not detected — boot check is broken. "
        f"Matched lines: {matches}"
    )


@pytest.mark.asyncio
async def test_l4_brain_size_by_agreement_aggregates_per_bucket() -> None:
    """Seed trade_intelligence with rows across the 3 supporting-count
    buckets (1-3, 4-5, 6+) and verify L4_BRAIN_SIZE_BY_AGREEMENT log
    fires with the correct per-bucket avg size."""
    from src.core.cycle_tracker import CycleSummary, CycleTracker
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            now = datetime.now(timezone.utc).replace(
                minute=10, second=0, microsecond=0,
            )
            hour_start_ts = int(now.timestamp() // 3600 * 3600)
            mid_ts = hour_start_ts + 600
            mid_iso = datetime.fromtimestamp(
                mid_ts, tz=timezone.utc,
            ).isoformat()

            # narrow_1_3: 3 trades at size=$200, pnl=+1.5%
            for _ in range(3):
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, "
                    "strategy_name, strategy_category, source, closed_by, "
                    "entry_price, exit_price, pnl_pct, pnl_usd, win, "
                    "hold_seconds, supporting_count, position_size_usd, "
                    "trade_closed_at, captured_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("X", "Buy", "x", "y", "test", "tp", 100.0, 101.5,
                     1.5, 3.0, 1, 60.0, 2, 200.0, mid_iso, mid_iso),
                )
            # moderate_4_5: 3 trades at size=$500, pnl=+0.5%
            for _ in range(3):
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, "
                    "strategy_name, strategy_category, source, closed_by, "
                    "entry_price, exit_price, pnl_pct, pnl_usd, win, "
                    "hold_seconds, supporting_count, position_size_usd, "
                    "trade_closed_at, captured_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("X", "Buy", "x", "y", "test", "tp", 100.0, 100.5,
                     0.5, 2.5, 1, 60.0, 5, 500.0, mid_iso, mid_iso),
                )
            # broad_6_plus: 3 trades at size=$800, pnl=-1.0% (herding)
            for _ in range(3):
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, "
                    "strategy_name, strategy_category, source, closed_by, "
                    "entry_price, exit_price, pnl_pct, pnl_usd, win, "
                    "hold_seconds, supporting_count, position_size_usd, "
                    "trade_closed_at, captured_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("X", "Buy", "x", "y", "test", "sl", 100.0, 99.0,
                     -1.0, -8.0, 0, 60.0, 7, 800.0, mid_iso, mid_iso),
                )

            tracker = CycleTracker(db=db)
            tracker._history.append(CycleSummary(
                cycle_id="c1", completed_at_unix=float(mid_ts),
                layer1a_ms=80, layer1b_ms=100, layer1c_ms=70, layer1d_ms=40,
                packages_ready=28, qualified_pct=44.0,
            ))
            with capture_logs() as buf:
                await tracker._flush_once()
            log_text = buf.getvalue()
            assert "L4_BRAIN_SIZE_BY_AGREEMENT" in log_text
            assert "narrow_1_3=n3/$200" in log_text
            assert "moderate_4_5=n3/$500" in log_text
            assert "broad_6_plus=n3/$800" in log_text
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_l4_brain_inverted_sizing_fires_when_broad_bigger_than_moderate() -> None:
    """When the brain sizes UP on crowded trades (broad avg size >
    moderate avg size) the Rule 11 inverted-sizing signal must fire
    at WARNING so the operator sees the truthful framing isn't
    changing the brain's sizing."""
    from src.core.cycle_tracker import CycleSummary, CycleTracker
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            now = datetime.now(timezone.utc).replace(
                minute=10, second=0, microsecond=0,
            )
            hour_start_ts = int(now.timestamp() // 3600 * 3600)
            mid_ts = hour_start_ts + 600
            mid_iso = datetime.fromtimestamp(
                mid_ts, tz=timezone.utc,
            ).isoformat()

            # broad bucket avg size $900 > moderate bucket avg size $400
            for sup, sz in [(5, 400.0)] * 3 + [(7, 900.0)] * 3:
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, "
                    "strategy_name, strategy_category, source, closed_by, "
                    "entry_price, exit_price, pnl_pct, pnl_usd, win, "
                    "hold_seconds, supporting_count, position_size_usd, "
                    "trade_closed_at, captured_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("X", "Buy", "x", "y", "test", "tp", 100.0, 100.5,
                     0.5, 1.0, 1, 60.0, sup, sz, mid_iso, mid_iso),
                )

            tracker = CycleTracker(db=db)
            tracker._history.append(CycleSummary(
                cycle_id="c1", completed_at_unix=float(mid_ts),
                layer1a_ms=80, layer1b_ms=100, layer1c_ms=70, layer1d_ms=40,
                packages_ready=28, qualified_pct=44.0,
            ))
            with capture_logs() as buf:
                await tracker._flush_once()
            log_text = buf.getvalue()
            assert "L4_BRAIN_INVERTED_SIZING" in log_text
            assert "WARNING" in log_text
            assert "brain_not_responding_to_truthful_framing" in log_text
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_l4_herding_monitor_fires_with_per_bucket_pnl() -> None:
    """L4_HERDING_MONITOR must fire each flush with avg pnl per
    supporting-count bucket so the operator can track whether
    broad agreement still correlates with losses."""
    from src.core.cycle_tracker import CycleSummary, CycleTracker
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            now = datetime.now(timezone.utc).replace(
                minute=10, second=0, microsecond=0,
            )
            hour_start_ts = int(now.timestamp() // 3600 * 3600)
            mid_ts = hour_start_ts + 600
            mid_iso = datetime.fromtimestamp(
                mid_ts, tz=timezone.utc,
            ).isoformat()
            # narrow wins +2%, broad loses -1%
            for sup, pnl in ([(2, 2.0)] * 3 + [(7, -1.0)] * 3):
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, "
                    "strategy_name, strategy_category, source, closed_by, "
                    "entry_price, exit_price, pnl_pct, pnl_usd, win, "
                    "hold_seconds, supporting_count, position_size_usd, "
                    "trade_closed_at, captured_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("X", "Buy", "x", "y", "test",
                     "tp" if pnl > 0 else "sl",
                     100.0, 100.0 + pnl, pnl, pnl,
                     1 if pnl > 0 else 0, 60.0, sup, 200.0, mid_iso, mid_iso),
                )
            tracker = CycleTracker(db=db)
            tracker._history.append(CycleSummary(
                cycle_id="c1", completed_at_unix=float(mid_ts),
                layer1a_ms=80, layer1b_ms=100, layer1c_ms=70, layer1d_ms=40,
                packages_ready=28, qualified_pct=44.0,
            ))
            with capture_logs() as buf:
                await tracker._flush_once()
            log_text = buf.getvalue()
            assert "L4_HERDING_MONITOR" in log_text
            assert "narrow_1_3_pnl=+2.000%" in log_text
            assert "broad_6_plus_pnl=-1.000%" in log_text
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_l4_self_checks_handle_zero_trades_gracefully() -> None:
    """An hour with no trade_intelligence rows must not crash either
    self-check. Empty buckets → n0/$0 in the log."""
    from src.core.cycle_tracker import CycleSummary, CycleTracker
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            now = datetime.now(timezone.utc).replace(
                minute=10, second=0, microsecond=0,
            )
            mid_ts = int(now.timestamp() // 3600 * 3600) + 600
            tracker = CycleTracker(db=db)
            tracker._history.append(CycleSummary(
                cycle_id="c1", completed_at_unix=float(mid_ts),
                layer1a_ms=80, layer1b_ms=100, layer1c_ms=70, layer1d_ms=40,
                packages_ready=28, qualified_pct=44.0,
            ))
            with capture_logs() as buf:
                await tracker._flush_once()
            log_text = buf.getvalue()
            assert "L4_BRAIN_SIZE_BY_AGREEMENT" in log_text
            assert "L4_HERDING_MONITOR" in log_text
            assert "L4_BRAIN_INVERTED_SIZING" not in log_text  # no data, no signal
            assert "L4_RULE16_SELF_CHECK_FAIL" not in log_text  # no crash
        finally:
            await db.disconnect()
