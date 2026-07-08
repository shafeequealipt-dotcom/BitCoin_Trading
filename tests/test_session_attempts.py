"""Four-Element Prompt Recalibration, Element 2 (2026-06-11) — the
per-coin session-attempt memory line.

Covers:
1. The pure renderer ``_session_attempts_line`` boundaries: zero
   attempts renders nothing; the base line format; the HEAVY LOSING
   SESSION suffix fires only at-or-above the shared heavy threshold
   AND with negative net; a custom threshold is honored.
2. The ``session_attempts_today`` query against a real temp SQLite DB:
   partial-close rows (shared ``opened_at``) collapse to one attempt
   while their PnL portions all sum; yesterday's entries are excluded;
   other exchange modes are excluded; fresh coins are absent from the
   result; empty inputs short-circuit without a DB hit.
3. Wiring contract: both candidate-block formatters consume
   ``session_attempts_by_sym`` via the renderer, and the prefetch
   resolves the exchange mode from the transformer (never hardcoded).
"""

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from src.brain.strategist import ClaudeStrategist, _session_attempts_line
from src.core.trade_recorder import session_attempts_today


# ── 1. Pure renderer ─────────────────────────────────────────────────

def test_line_empty_on_zero_attempts():
    assert _session_attempts_line(0, 0.0) == ""
    assert _session_attempts_line(-1, -5.0) == ""


def test_line_basic_format_below_heavy():
    line = _session_attempts_line(3, -0.42, heavy_min=6)
    assert line == "  Session today: 3 attempts, net -0.42 USD"
    assert "HEAVY" not in line


def test_heavy_losing_suffix_fires_at_threshold_with_negative_net():
    line = _session_attempts_line(6, -1.20, heavy_min=6)
    assert line.startswith("  Session today: 6 attempts, net -1.20 USD")
    assert "HEAVY LOSING SESSION" in line
    # Element 1's exact permission vocabulary — awareness, not a gate.
    assert "QUALITY OVER QUOTA" in line
    assert "declining it is correct trading" in line


def test_heavy_suffix_requires_negative_net():
    assert "HEAVY" not in _session_attempts_line(6, 0.10, heavy_min=6)
    assert "HEAVY" not in _session_attempts_line(9, 0.0, heavy_min=6)


def test_heavy_suffix_respects_threshold_boundary():
    assert "HEAVY" not in _session_attempts_line(5, -9.0, heavy_min=6)
    assert "HEAVY" in _session_attempts_line(8, -0.01, heavy_min=8)
    assert "HEAVY" not in _session_attempts_line(7, -0.01, heavy_min=8)


def test_positive_net_renders_signed():
    line = _session_attempts_line(2, 0.55)
    assert "net +0.55 USD" in line


# ── 2. Query against a real temp SQLite ──────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.mark.asyncio
async def test_query_against_real_sqlite(tmp_path):
    from src.database.connection import DatabaseManager

    db = DatabaseManager(str(tmp_path / "t.db"))
    await db.connect()
    try:
        await db.execute(
            "CREATE TABLE trade_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT UNIQUE, "
            "symbol TEXT NOT NULL, direction TEXT NOT NULL DEFAULT 'Buy', "
            "pnl_usd REAL DEFAULT 0, opened_at TEXT DEFAULT '', "
            "closed_at TEXT DEFAULT '', "
            "exchange_mode TEXT NOT NULL DEFAULT 'shadow')"
        )
        now = datetime.now(timezone.utc)
        entry1 = _iso(now - timedelta(hours=2))
        entry2 = _iso(now - timedelta(hours=1))
        yesterday = _iso(now - timedelta(days=1, hours=1))
        rows = [
            # AAA entry 1: full close.
            ("t1", "AAAUSDT", -0.50, entry1, "bybit_demo"),
            # AAA entry 2: two partial rows + final, all sharing opened_at
            # (the live partial-close writer reuses state.opened_at_dt).
            ("t2-partial-1", "AAAUSDT", 0.10, entry2, "bybit_demo"),
            ("t2-partial-2", "AAAUSDT", 0.05, entry2, "bybit_demo"),
            ("t2", "AAAUSDT", -0.40, entry2, "bybit_demo"),
            # AAA yesterday: excluded by the UTC-day window.
            ("t0", "AAAUSDT", -9.99, yesterday, "bybit_demo"),
            # AAA today but in shadow mode: excluded by the mode filter.
            ("t3", "AAAUSDT", -7.77, entry2, "shadow"),
            # BBB: one entry today.
            ("t4", "BBBUSDT", 0.33, entry1, "bybit_demo"),
            # Legacy empty opened_at row: excluded lexically.
            ("t5", "AAAUSDT", -5.55, "", "bybit_demo"),
        ]
        for tid, sym, pnl, opened, mode in rows:
            await db.execute(
                "INSERT INTO trade_log (trade_id, symbol, pnl_usd, "
                "opened_at, exchange_mode) VALUES (?, ?, ?, ?, ?)",
                (tid, sym, pnl, opened, mode),
            )
        out = await session_attempts_today(
            db,
            symbols=["AAAUSDT", "BBBUSDT", "CCCUSDT"],
            exchange_mode="bybit_demo",
        )
        assert set(out.keys()) == {"AAAUSDT", "BBBUSDT"}
        # Two distinct entries (partials collapsed), net sums all booked
        # portions: -0.50 + 0.10 + 0.05 - 0.40 = -0.75.
        assert out["AAAUSDT"]["attempts"] == 2
        assert out["AAAUSDT"]["net_usd"] == pytest.approx(-0.75)
        assert out["BBBUSDT"]["attempts"] == 1
        assert out["BBBUSDT"]["net_usd"] == pytest.approx(0.33)
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_query_safety_sentinels_no_db_hit():
    class _Boom:
        async def fetch_all(self, *a, **k):  # pragma: no cover
            raise AssertionError("DB must not be hit on empty inputs")

    assert await session_attempts_today(
        _Boom(), symbols=[], exchange_mode="bybit_demo",
    ) == {}
    assert await session_attempts_today(
        _Boom(), symbols=["AAAUSDT"], exchange_mode="",
    ) == {}


@pytest.mark.asyncio
async def test_query_swallows_db_errors():
    class _Err:
        async def fetch_all(self, *a, **k):
            raise RuntimeError("locked")

    assert await session_attempts_today(
        _Err(), symbols=["AAAUSDT"], exchange_mode="bybit_demo",
    ) == {}


# ── 3. Wiring contracts (source-inspection, the established pattern) ─

def test_both_formatters_consume_session_attempts():
    for fn in (
        ClaudeStrategist._format_packages_for_prompt,
        ClaudeStrategist._format_packages_for_prompt_full,
    ):
        src = inspect.getsource(fn)
        assert "session_attempts_by_sym" in src, fn.__name__
        assert "_session_attempts_line" in src, fn.__name__
        # The shared threshold key — never a separate drift-prone copy.
        assert "quality_skip_heavy_attempts" in src, fn.__name__


def test_prefetch_resolves_mode_from_transformer_never_hardcoded():
    src = inspect.getsource(ClaudeStrategist._prefetch_session_attempts)
    assert "current_mode" in src
    assert '"bybit_demo"' not in src
    assert "session_attempts_enabled" in src
    # Rule 4 honesty: unresolvable mode renders nothing.
    assert "return out" in src


def test_build_trade_prompt_threads_the_dict_to_both_formatters():
    src = inspect.getsource(ClaudeStrategist._build_trade_prompt)
    assert src.count("session_attempts_by_sym=session_attempts_by_sym") == 2
    assert "_prefetch_session_attempts(packages)" in src
