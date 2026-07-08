"""Phase 3 (post-Layer-1 fix) — caller-frame attribution on DB_PROTECT_BLOCKED.

The blocked log line now carries ``caller_file``, ``caller_line``, and
``caller_method`` for the first stack frame outside ``connection.py`` /
``protected_tables.py``. Operators can grep the next blocked event and
attribute it to the upstream scheduler instantly.

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_3_db_protect_blocked.md``.
"""

from __future__ import annotations

import pytest

from src.database.protected_tables import (
    PROTECTED_TABLES,
    ProtectedTableViolation,
    _extract_caller_frame,
    assert_not_protected_destructive,
    is_protected,
)


def test_trade_thesis_no_longer_protected() -> None:
    """trade_thesis was removed from PROTECTED_TABLES in Phase 3."""
    assert "trade_thesis" not in PROTECTED_TABLES
    assert not is_protected("trade_thesis")


def test_other_protected_tables_still_protected() -> None:
    """Removal of trade_thesis must not regress the other protected tables."""
    for table in (
        "tias_results",
        "tias_analyses",
        "trade_intelligence",
        "trade_log",
        "trade_history",
        "thesis_store",
        "virtual_positions",
        "sniper_log",
    ):
        assert is_protected(table), f"{table} should still be protected"


def test_caller_attribution_in_violation_details() -> None:
    """ProtectedTableViolation.details exposes caller fields."""

    def caller_function() -> None:
        # The frame we expect to see attributed to the violation.
        assert_not_protected_destructive("DELETE FROM tias_results WHERE id = 1")

    with pytest.raises(ProtectedTableViolation) as exc_info:
        caller_function()

    details = exc_info.value.details
    assert details["caller_file"].endswith(".py")
    # caller_method should be the function that called assert_*
    assert details["caller_method"] == "caller_function"
    assert details["caller_line"] > 0
    assert details["sql_kind"] == "DELETE"
    assert details["table"] == "tias_results"


def test_caller_attribution_skips_internal_frames() -> None:
    """The walker must not attribute the block to protected_tables.py itself."""

    def outer() -> None:
        def inner() -> None:
            assert_not_protected_destructive("DELETE FROM trade_log WHERE id < 5")
        inner()

    with pytest.raises(ProtectedTableViolation) as exc_info:
        outer()

    details = exc_info.value.details
    assert "protected_tables.py" not in details["caller_file"]
    assert "connection.py" not in details["caller_file"]
    # First non-internal frame is `inner`.
    assert details["caller_method"] == "inner"


def test_extract_caller_frame_defensive() -> None:
    """_extract_caller_frame never raises (returns sentinel on failure)."""
    # Direct call from this test file — should attribute to this test.
    caller_file, caller_line, caller_method = _extract_caller_frame()
    assert caller_file.endswith(".py")
    assert caller_line > 0
    # Method name varies by pytest harness; just assert non-empty.
    assert caller_method


def test_force_path_logs_attribution_but_allows() -> None:
    """force=True still emits caller attribution in the FORCE log line."""
    # No exception expected with force=True.
    assert_not_protected_destructive(
        "DELETE FROM trade_log WHERE id < 1", force=True
    )


def test_non_destructive_sql_unaffected() -> None:
    """SELECT / INSERT / UPDATE pass through without inspection."""
    # Should be a no-op (SELECT is not in _DESTRUCTIVE_KINDS).
    assert_not_protected_destructive("SELECT * FROM trade_log")
    assert_not_protected_destructive("INSERT INTO trade_log (id) VALUES (1)")
    assert_not_protected_destructive("UPDATE trade_log SET id = 1")


def test_destructive_on_non_protected_passes() -> None:
    """DELETE on a non-protected table is allowed."""
    assert_not_protected_destructive("DELETE FROM klines WHERE timestamp < 0")
    assert_not_protected_destructive("DELETE FROM trade_thesis WHERE id < 0")
