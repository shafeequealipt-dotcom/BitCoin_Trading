"""Phase 3.4 — Mid-Hold Trade Management Fix: ensemble-flip detection.

Tests both the data layer (``EnsembleStateCache``) and the watchdog
detection path (``PositionWatchdog._detect_ensemble_flip``):

  - Cache.record + cache.get_current_consensus shape + classification.
  - Dominant-direction selection + threshold mapping.
  - Watchdog detects opposite STRONG consensus → queues event.
  - Watchdog dedupes within ensemble_flip_dedupe_window_seconds.
  - Watchdog clears dedupe on re-align.
  - Detection short-circuits on kill switch / missing services.

Mocking strategy: a real EnsembleStateCache + real (in-memory) DB-backed
ThesisManager are used; PositionWatchdog is exercised via a thin
constructor that bypasses real services it doesn't need for the flip
check.
"""

from __future__ import annotations

import os
import re
import tempfile
from unittest.mock import MagicMock, AsyncMock

import pytest
from loguru import logger as _loguru_logger

from src.strategies.ensemble import EnsembleStateCache


# ════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append((msg.record["level"].name, msg.record["message"])),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


def _parse_kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


@pytest.fixture
async def real_db():
    """Real DatabaseManager with v34/v35 schema for thesis_events."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "midhold_p3_4.db")
        db = DatabaseManager(path)
        await db.connect()
        await run_migrations(db)
        try:
            yield db
        finally:
            await db.disconnect()


# ════════════════════════════════════════════════════════════════════
# 1. EnsembleStateCache — pure data layer
# ════════════════════════════════════════════════════════════════════


def test_cache_empty_returns_none() -> None:
    cache = EnsembleStateCache()
    assert cache.get_current_consensus("ETHUSDT") is None


def test_cache_record_then_read_strong_buy() -> None:
    cache = EnsembleStateCache()
    cache.record("ETHUSDT", buy_votes=6.5, sell_votes=0.5, neutral_votes=1.0)
    rec = cache.get_current_consensus("ETHUSDT")
    assert rec is not None
    assert rec["consensus"] == "STRONG"
    assert rec["dominant_dir"] == "BUY"
    assert rec["agreeing"] == 6.5
    assert rec["opposing"] == 0.5


def test_cache_strong_sell() -> None:
    cache = EnsembleStateCache()
    cache.record("ETHUSDT", buy_votes=0.2, sell_votes=5.5, neutral_votes=1.0)
    rec = cache.get_current_consensus("ETHUSDT")
    assert rec["consensus"] == "STRONG"
    assert rec["dominant_dir"] == "SELL"


def test_cache_strong_threshold_tunable() -> None:
    """Lower the STRONG floor and votes that were GOOD now classify as STRONG."""
    cache = EnsembleStateCache()
    cache.record("ETHUSDT", buy_votes=3.5, sell_votes=0.5, neutral_votes=0.0)
    # Default floor 4.0 → not STRONG
    default = cache.get_current_consensus("ETHUSDT", strong_threshold=4.0)
    assert default["consensus"] != "STRONG"
    # Tighter floor 3.0 → STRONG
    tighter = cache.get_current_consensus("ETHUSDT", strong_threshold=3.0)
    assert tighter["consensus"] == "STRONG"


def test_cache_conflict_when_equal() -> None:
    cache = EnsembleStateCache()
    cache.record("ETHUSDT", buy_votes=2.0, sell_votes=2.0, neutral_votes=1.0)
    rec = cache.get_current_consensus("ETHUSDT")
    assert rec["consensus"] == "CONFLICT"


def test_cache_weak_when_low_votes() -> None:
    cache = EnsembleStateCache()
    cache.record("ETHUSDT", buy_votes=1.6, sell_votes=1.4, neutral_votes=2.0)
    rec = cache.get_current_consensus("ETHUSDT")
    assert rec["consensus"] == "WEAK"


def test_cache_overwrite_replaces_previous() -> None:
    cache = EnsembleStateCache()
    cache.record("ETHUSDT", buy_votes=6.0, sell_votes=0.0, neutral_votes=0.0)
    cache.record("ETHUSDT", buy_votes=0.0, sell_votes=6.0, neutral_votes=0.0)
    rec = cache.get_current_consensus("ETHUSDT")
    assert rec["dominant_dir"] == "SELL"


# ════════════════════════════════════════════════════════════════════
# 2. Watchdog _detect_ensemble_flip — integrated mocked
# ════════════════════════════════════════════════════════════════════


def _make_watchdog_for_flip_test(
    db,
    enabled: bool = True,
    strong_threshold: float = 4.0,
    dedupe_window: float = 300.0,
):
    """Build a PositionWatchdog with only the dependencies the flip
    detector touches. Other services are mocked to None so calling
    real watchdog ticks would fail — but ``_detect_ensemble_flip`` only
    touches ensemble_state_cache, thesis_manager, settings, and the
    in-memory state dict."""
    from src.core.thesis_manager import ThesisManager
    from src.workers.position_watchdog import PositionWatchdog

    settings = MagicMock()
    settings.watchdog.check_interval_seconds = 10.0
    settings.watchdog.ensemble_flip_detection_enabled = enabled
    settings.watchdog.ensemble_flip_strong_threshold = strong_threshold
    settings.watchdog.ensemble_flip_dedupe_window_seconds = dedupe_window
    # Empty / null-ish defaults for BaseWorker's __init__ usage.
    settings.time_decay = None

    cache = EnsembleStateCache()
    thesis_manager = ThesisManager(db)

    # We bypass __init__ since it constructs lots of unrelated state.
    # The flip detector only reads these attributes:
    wd = PositionWatchdog.__new__(PositionWatchdog)
    wd.settings = settings
    wd.ensemble_state_cache = cache
    wd.thesis_manager = thesis_manager
    wd._position_consensus_state = {}
    return wd, cache, thesis_manager


@pytest.mark.asyncio
async def test_detect_flip_opposite_strong_buy_on_open_sell(real_db, loguru_sink) -> None:
    """Open Sell + ensemble flips STRONG BUY → ENSEMBLE_FLIP_DETECTED event queued."""
    wd, cache, thesis = _make_watchdog_for_flip_test(real_db)
    # Open a Sell thesis for ETH.
    await thesis.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="bearish OB",
        order_id="ORD-eth-flip1",
    )
    # Ensemble flips to STRONG BUY.
    cache.record("ETHUSDT", buy_votes=6.36, sell_votes=0.0, neutral_votes=2.0)
    pos = MagicMock()
    pos.symbol = "ETHUSDT"
    pos.side = "Sell"

    await wd._detect_ensemble_flip(pos)

    detected = _records_with_tag(loguru_sink, "ENSEMBLE_FLIP_DETECTED ")
    assert len(detected) == 1
    kv = _parse_kv(detected[0][1])
    assert kv["sym"] == "ETHUSDT"
    assert kv["pos_dir"] == "SELL"
    assert kv["ensemble_dir"] == "BUY"

    # IMPLEMENT_MIDHOLD doc Rule 7: the watchdog-layer EVENT_QUEUED tag
    # fires after the row lands in the DB queue. Pair with the lower-
    # level THESIS_EVENT_QUEUED from thesis_manager (both surface).
    queued = _records_with_tag(loguru_sink, "ENSEMBLE_FLIP_EVENT_QUEUED ")
    assert len(queued) == 1

    # Event row in thesis_events.
    rows = await real_db.fetch_all(
        "SELECT symbol, order_id, event_type FROM thesis_events"
    )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETHUSDT"
    assert rows[0]["event_type"] == "ensemble_flip"
    assert rows[0]["order_id"] == "ORD-eth-flip1"


@pytest.mark.asyncio
async def test_no_flip_when_dominant_matches_position(real_db, loguru_sink) -> None:
    """Open Sell + STRONG SELL consensus → no event."""
    wd, cache, thesis = _make_watchdog_for_flip_test(real_db)
    await thesis.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="bearish OB",
        order_id="ORD-eth-noflip",
    )
    cache.record("ETHUSDT", buy_votes=0.0, sell_votes=6.0, neutral_votes=0.0)
    pos = MagicMock()
    pos.symbol = "ETHUSDT"
    pos.side = "Sell"

    await wd._detect_ensemble_flip(pos)

    assert len(_records_with_tag(loguru_sink, "ENSEMBLE_FLIP_DETECTED ")) == 0
    rows = await real_db.fetch_all("SELECT id FROM thesis_events")
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_dedupe_collapses_repeated_flips(real_db, loguru_sink) -> None:
    """Two consecutive ticks with same flip direction → one event."""
    wd, cache, thesis = _make_watchdog_for_flip_test(real_db, dedupe_window=300.0)
    await thesis.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="bearish OB",
        order_id="ORD-eth-dedupe",
    )
    cache.record("ETHUSDT", buy_votes=6.0, sell_votes=0.0, neutral_votes=0.0)
    pos = MagicMock()
    pos.symbol = "ETHUSDT"
    pos.side = "Sell"

    await wd._detect_ensemble_flip(pos)
    await wd._detect_ensemble_flip(pos)
    await wd._detect_ensemble_flip(pos)

    detected = _records_with_tag(loguru_sink, "ENSEMBLE_FLIP_DETECTED ")
    assert len(detected) == 1
    rows = await real_db.fetch_all("SELECT id FROM thesis_events")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_dedupe_clears_when_consensus_realigns(real_db, loguru_sink) -> None:
    """STRONG BUY → STRONG SELL (matches position Sell) → STRONG BUY again → 2 events."""
    wd, cache, thesis = _make_watchdog_for_flip_test(real_db)
    await thesis.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="bearish OB",
        order_id="ORD-eth-realign",
    )
    pos = MagicMock()
    pos.symbol = "ETHUSDT"
    pos.side = "Sell"

    cache.record("ETHUSDT", buy_votes=6.0, sell_votes=0.0, neutral_votes=0.0)
    await wd._detect_ensemble_flip(pos)  # fires
    cache.record("ETHUSDT", buy_votes=0.0, sell_votes=6.0, neutral_votes=0.0)
    await wd._detect_ensemble_flip(pos)  # re-aligns, clears dedupe
    cache.record("ETHUSDT", buy_votes=6.5, sell_votes=0.0, neutral_votes=0.0)
    await wd._detect_ensemble_flip(pos)  # fires again

    detected = _records_with_tag(loguru_sink, "ENSEMBLE_FLIP_DETECTED ")
    assert len(detected) == 2
    rows = await real_db.fetch_all("SELECT id FROM thesis_events")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_kill_switch_disables_detection(real_db, loguru_sink) -> None:
    wd, cache, thesis = _make_watchdog_for_flip_test(real_db, enabled=False)
    await thesis.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="bearish OB",
        order_id="ORD-eth-disabled",
    )
    cache.record("ETHUSDT", buy_votes=6.0, sell_votes=0.0, neutral_votes=0.0)
    pos = MagicMock()
    pos.symbol = "ETHUSDT"
    pos.side = "Sell"

    await wd._detect_ensemble_flip(pos)

    assert len(_records_with_tag(loguru_sink, "ENSEMBLE_FLIP_DETECTED ")) == 0


@pytest.mark.asyncio
async def test_no_thesis_row_means_no_queue(real_db, loguru_sink) -> None:
    """When no open thesis exists for symbol → no queued event (FK semantics)."""
    wd, cache, thesis = _make_watchdog_for_flip_test(real_db)
    cache.record("ETHUSDT", buy_votes=6.0, sell_votes=0.0, neutral_votes=0.0)
    pos = MagicMock()
    pos.symbol = "ETHUSDT"
    pos.side = "Sell"

    await wd._detect_ensemble_flip(pos)

    rows = await real_db.fetch_all("SELECT id FROM thesis_events")
    assert len(rows) == 0
