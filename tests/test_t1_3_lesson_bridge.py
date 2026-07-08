"""T1-3 / F9 lesson bridge + aggregated stats smoke tests.

Six-tier-fixes 2026-05-11. Covers:
1. compose_lesson_from_tias produces a concise lesson from non-empty
   ds_what_should_done.
2. compose_lesson_from_tias returns None when both ds_what_should_done
   and ds_how_to_exploit are empty.
3. compose_lesson_from_tias falls back to ds_how_to_exploit when
   ds_what_should_done is absent.
4. compose_lesson_from_tias truncates over-long content with ellipsis.
5. format_aggregated_stats_for_prompt returns empty string when count is 0.
6. format_aggregated_stats_for_prompt renders a WR / by-reason block.

Pure-function tests; no IO.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_compose_lesson_uses_ds_what_should_done():
    from src.core.thesis_manager import compose_lesson_from_tias

    analysis = {
        "ds_what_should_done": "Should have waited for support break before entering",
        "ds_how_to_exploit": "Use D1 structure as confirmation",
        "ds_category": "premature_entry",
    }
    lesson = compose_lesson_from_tias(
        analysis=analysis,
        close_reason="time_decay_force_close",
        hold_seconds=300.0,
        pnl_pct=-0.4,
    )
    assert lesson is not None
    assert "5m hold" in lesson
    assert "time_decay_force_close" in lesson
    assert "-0.40%" in lesson
    assert "cat=premature_entry" in lesson
    assert "Should have waited" in lesson


def test_compose_lesson_returns_none_when_empty_analysis():
    from src.core.thesis_manager import compose_lesson_from_tias

    lesson = compose_lesson_from_tias(
        analysis={"ds_what_should_done": "", "ds_how_to_exploit": None},
        close_reason="bybit_sl_hit",
        hold_seconds=120.0,
        pnl_pct=-0.5,
    )
    assert lesson is None


def test_compose_lesson_falls_back_to_ds_how_to_exploit():
    from src.core.thesis_manager import compose_lesson_from_tias

    lesson = compose_lesson_from_tias(
        analysis={
            "ds_what_should_done": "",
            "ds_how_to_exploit": "Exploit the breakout retest pattern next time",
            "ds_category": "",
        },
        close_reason="trailing_stop",
        hold_seconds=900.0,
        pnl_pct=+0.8,
    )
    assert lesson is not None
    assert "15m hold" in lesson
    assert "Exploit the breakout" in lesson


def test_compose_lesson_truncates_with_ellipsis():
    from src.core.thesis_manager import compose_lesson_from_tias

    body = "x " * 300  # 600 chars of body
    lesson = compose_lesson_from_tias(
        analysis={"ds_what_should_done": body, "ds_category": "test"},
        close_reason="trailing_stop",
        hold_seconds=60.0,
        pnl_pct=0.5,
        max_chars=120,
    )
    assert lesson is not None
    assert len(lesson) <= 120
    assert lesson.endswith("…")  # ellipsis


def test_format_aggregated_stats_empty_returns_empty_string():
    from src.core.thesis_manager import format_aggregated_stats_for_prompt

    assert format_aggregated_stats_for_prompt({"count": 0}) == ""
    assert format_aggregated_stats_for_prompt({}) == ""


def test_format_aggregated_stats_renders_wr_and_reasons():
    from src.core.thesis_manager import format_aggregated_stats_for_prompt

    stats = {
        "count": 50,
        "wins": 28,
        "losses": 22,
        "wr_pct": 56.0,
        "net_pnl_usd": 48.72,
        "by_reason": {
            "trailing_stop": {"count": 31, "wins": 24},
            "time_decay_force_close": {"count": 9, "wins": 2},
            "bybit_sl_hit": {"count": 10, "wins": 3},
        },
    }
    rendered = format_aggregated_stats_for_prompt(stats)
    assert "RECENT PERFORMANCE" in rendered
    assert "50 closes" in rendered
    assert "WR: 56%" in rendered
    assert "(28W / 22L)" in rendered
    assert "$+48.72" in rendered
    assert "trailing_stop" in rendered
    # Confirm no symbol-specific narrative leaks in (closed-loop-immune).
    assert "USDT" not in rendered


# ─────────────────────── get_recent_lessons SQL param-order ───────────────────────
#
# Integration test using an in-memory aiosqlite DB to catch the param-order
# bug found in the six-tier-fixes audit. Previously, when BOTH
# min_age_seconds and exclude_symbols were passed, the params tuple was
# built in the wrong order vs the SQL placeholders.


class _AiosqliteCursorWrapper:
    """Minimal wrapper to mimic the project's DatabaseManager.fetch_all."""

    def __init__(self, conn):
        self._conn = conn

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        import aiosqlite  # type: ignore
        async with self._conn.execute(sql, params) as cur:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) async for row in cur]


import pytest


@pytest.mark.asyncio
async def test_get_recent_lessons_with_both_filters_uses_correct_param_order():
    """Both min_age_seconds AND exclude_symbols set must work together.

    Audit found that the previous builder mis-ordered params, producing a
    SQL execution where min_age_seconds was bound to a symbol placeholder
    and a symbol was bound to the time-delta comparison. Either crashes
    with a type mismatch or silently returns the wrong rows.
    """
    import aiosqlite
    from src.core.thesis_manager import ThesisManager

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute(
            "CREATE TABLE trade_thesis ("
            "symbol TEXT, direction TEXT, entry_price REAL, close_price REAL, "
            "actual_pnl_pct REAL, actual_pnl_usd REAL, close_reason TEXT, "
            "lesson TEXT, thesis TEXT, opened_at TEXT, closed_at TEXT, "
            "status TEXT)"
        )
        # Seed: 3 lessons. Two recent (closed 1 min ago), one old (10 min ago).
        # ABCUSDT is "open" (we want to exclude it).
        await conn.execute(
            "INSERT INTO trade_thesis (symbol, direction, actual_pnl_pct, "
            "close_reason, lesson, closed_at, status) VALUES "
            "(?, 'Buy', 0.5, 'trailing_stop', 'older lesson', "
            "datetime('now', '-10 minutes'), 'closed')",
            ("OLDSYM",),
        )
        await conn.execute(
            "INSERT INTO trade_thesis (symbol, direction, actual_pnl_pct, "
            "close_reason, lesson, closed_at, status) VALUES "
            "(?, 'Sell', -0.3, 'bybit_sl_hit', 'fresh-but-excluded lesson', "
            "datetime('now', '-1 minutes'), 'closed')",
            ("ABCUSDT",),
        )
        await conn.execute(
            "INSERT INTO trade_thesis (symbol, direction, actual_pnl_pct, "
            "close_reason, lesson, closed_at, status) VALUES "
            "(?, 'Buy', 0.2, 'trailing_stop', 'fresh-keepable lesson', "
            "datetime('now', '-1 minutes'), 'closed')",
            ("XYZUSDT",),
        )
        await conn.commit()

        tm = ThesisManager(db=_AiosqliteCursorWrapper(conn))

        # Filter: must be > 5 min old (excludes the two fresh ones) AND
        # not in {ABCUSDT}. Only OLDSYM should remain.
        lessons = await tm.get_recent_lessons(
            limit=10,
            min_age_seconds=300,
            exclude_symbols=frozenset({"ABCUSDT"}),
        )
        assert len(lessons) == 1
        assert lessons[0]["symbol"] == "OLDSYM"

        # Filter: ONLY age (no exclude). Should return OLDSYM only.
        lessons2 = await tm.get_recent_lessons(limit=10, min_age_seconds=300)
        assert len(lessons2) == 1
        assert lessons2[0]["symbol"] == "OLDSYM"

        # Filter: ONLY exclude (no age). Should return OLDSYM + XYZUSDT.
        lessons3 = await tm.get_recent_lessons(
            limit=10, exclude_symbols=frozenset({"ABCUSDT"}),
        )
        syms = {l["symbol"] for l in lessons3}
        assert syms == {"OLDSYM", "XYZUSDT"}

        # Filter: no filters. Legacy behavior — all 3.
        lessons4 = await tm.get_recent_lessons(limit=10)
        assert len(lessons4) == 3
    finally:
        await conn.close()
