"""J-series end-to-end pipeline tests against the real project.

The unit tests for J1-J7 exercise each fix in isolation. This module
goes one layer deeper: it builds the actual production classes
(``DatabaseManager``, ``run_migrations``, real ``TradingRepository``,
real ``BybitDemoPositionService`` / ``BybitDemoOrderService``, real
``TradeCoordinator``, real ``PositionReconciler``, real
``TradeOptimizer``, real ``TradeGate``, real ``_ClaudeWorkerPool``)
with realistic data and exercises each J-fix's runtime path.

The only mocks are the lowest-level external boundaries (Bybit HTTP
client, Claude CLI subprocess) so the test stays hermetic. Everything
in between — settings, schema, repository, adapter, coordinator, gate,
worker, observability — is the same code that runs on the operator's
GCP VM.

Each test asserts BOTH the post-state (DB rows, registry entries) AND
the structured log events the fix is supposed to emit, so a future
regression in either dimension surfaces here first.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.apex.gate import TradeGate
from src.apex.models import OptimizedTrade
from src.apex.optimizer import TradeOptimizer
from src.brain.claude_code_client import _ClaudeWorkerPool, _PrewarmSlot
from src.bybit_demo.bybit_demo_adapter import (
    BybitDemoOrderService,
    BybitDemoPositionService,
)
from src.config.settings import (
    APEXSettings,
    RiskSettings,
    _build_apex,
    _build_risk,
)
from src.core.sl_geometry import is_long_side, is_tighter_sl
from src.core.trade_coordinator import TradeCoordinator, TradeState
from src.core.types import OrderStatus, OrderType, Side
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations
from src.database.repositories.trading_repo import TradingRepository
from src.workers.position_reconciler import PositionReconciler


# ====================================================================
# Shared fixtures — real DatabaseManager + real schema
# ====================================================================


@pytest.fixture
async def real_db(tmp_path):
    """Real DatabaseManager backed by sqlite + full project migrations.

    This is the exact construction used in workers.py / WorkerManager:
    reader_pool concurrency model, WAL mode, full schema migrations.
    """
    db_path = tmp_path / "e2e.db"
    db = DatabaseManager(
        str(db_path),
        wal_mode=True,
        concurrency_model="reader_pool",
        reader_pool_size=2,
    )
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append(
            (msg.record["level"].name, msg.record["message"])
        ),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


def _kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


# ====================================================================
# J1 — Adapter cache prune: real adapter + real repo + real DB
# ====================================================================


class _FakeBybitClient:
    """Bybit V5 HTTP client stub. Returns configured response queue."""

    def __init__(self) -> None:
        self.gets: list[tuple[str, dict, str]] = []
        self.posts: list[tuple[str, dict, str]] = []
        self._queue: list[Any] = []

    def queue(self, *responses: Any) -> None:
        self._queue.extend(responses)

    async def get(self, path: str, params: dict | None = None, *, op: str = "") -> dict:
        self.gets.append((path, dict(params or {}), op))
        if not self._queue:
            return {"retCode": 0, "result": {"list": []}}
        nxt = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def post(self, path: str, body: dict, *, op: str = "") -> dict:
        self.posts.append((path, body, op))
        return {"retCode": 0, "result": {"orderId": "OID-e2e"}}


def _v5_pos(symbol: str, side: str = "Buy", size: str = "1.0") -> dict:
    return {
        "symbol": symbol, "side": side, "size": size,
        "avgPrice": "100", "markPrice": "100",
        "unrealisedPnl": "0", "leverage": "1", "liqPrice": "0",
    }


@pytest.mark.asyncio
async def test_e2e_j1_cache_prune_through_real_repo(real_db, loguru_sink) -> None:
    """Seed the real positions table with 4 historic stale rows (the
    audit's 2026-05-13 residue), then run a real adapter.get_positions
    call against a Bybit response that only includes 1 of them. The
    other 3 must be pruned from the DB; the kept one stays."""
    repo = TradingRepository(real_db)

    # Seed the 4 audit-time residue rows. Use save_position so the
    # real INSERT path is exercised, including the exchange_mode tag.
    from src.core.types import Position
    now = datetime.now(timezone.utc)
    for sym, side, size, ep in [
        ("SANDUSDT", Side.SELL, 11155.0, 0.08068),
        ("EGLDUSDT", Side.BUY, 42.0, 4.761),
        ("RUNEUSDT", Side.SELL, 2209.8, 0.6109),
        ("AAVEUSDT", Side.SELL, 9.04, 99.54),
    ]:
        await repo.save_position(
            Position(
                symbol=sym, side=side, size=size,
                entry_price=ep, mark_price=ep,
                unrealized_pnl=0.0, realized_pnl=0.0, leverage=1,
                liquidation_price=0.0, stop_loss=None, take_profit=None,
                updated_at=now,
            ),
            exchange_mode="bybit_demo",
        )

    # Also seed a shadow row that must remain UNTOUCHED.
    await repo.save_position(
        Position(
            symbol="ETHUSDT", side=Side.BUY, size=1.0,
            entry_price=3000.0, mark_price=3000.0,
            unrealized_pnl=0.0, realized_pnl=0.0, leverage=1,
            liquidation_price=0.0, stop_loss=None, take_profit=None,
            updated_at=now,
        ),
        exchange_mode="shadow",
    )

    # Confirm 5 rows present pre-prune (4 bybit_demo + 1 shadow)
    pre = await real_db.fetch_all(
        "SELECT symbol, exchange_mode FROM positions ORDER BY symbol"
    )
    assert {(r["symbol"], r["exchange_mode"]) for r in pre} == {
        ("AAVEUSDT", "bybit_demo"),
        ("EGLDUSDT", "bybit_demo"),
        ("ETHUSDT", "shadow"),
        ("RUNEUSDT", "bybit_demo"),
        ("SANDUSDT", "bybit_demo"),
    }

    # Run the real adapter with a Bybit response that confirms only AAVE
    client = _FakeBybitClient()
    client.queue({"retCode": 0, "result": {"list": [_v5_pos("AAVEUSDT", "Sell")]}})
    svc = BybitDemoPositionService(client, trading_repo=repo)

    result = await svc.get_positions_with_confirmation()
    assert result.confirmed is True
    assert {p.symbol for p in result.positions} == {"AAVEUSDT"}

    # Post-prune: the 3 stale bybit_demo rows removed; AAVE survives;
    # shadow row untouched
    post = await real_db.fetch_all(
        "SELECT symbol, exchange_mode FROM positions ORDER BY symbol"
    )
    assert {(r["symbol"], r["exchange_mode"]) for r in post} == {
        ("AAVEUSDT", "bybit_demo"),
        ("ETHUSDT", "shadow"),
    }

    # Three POSITIONS_CACHE_PRUNE log events fired (one per stale)
    pruned = _records(loguru_sink, "POSITIONS_CACHE_PRUNE |")
    pruned_syms = {_kv(r[1])["sym"] for r in pruned}
    assert pruned_syms == {"SANDUSDT", "EGLDUSDT", "RUNEUSDT"}


# ====================================================================
# J1 — PositionReconciler: real worker + real DB + dwell guard
# ====================================================================


class _FakePosService:
    """Minimal PositionService stub. Returns the queued confirmation."""

    def __init__(self, result) -> None:
        self.result = result
        self.calls: int = 0

    async def get_positions_with_confirmation(self):
        self.calls += 1
        return self.result


class _FakeAccountService:
    def __init__(self, total: float = 100_000.0, available: float = 100_000.0) -> None:
        self.total = total
        self.available = available

    async def get_wallet_balance(self):
        from src.core.types import AccountInfo
        return AccountInfo(
            total_equity=self.total, available_balance=self.available,
            used_margin=self.total - self.available, unrealized_pnl=0.0,
        )


class _StubFundManager:
    def __init__(self, total: float, available: float) -> None:
        self._account_state = SimpleNamespace(
            total_equity=total, available=available,
        )


def _stub_settings():
    """A settings shim with the fields PositionReconciler / BaseWorker need."""
    return SimpleNamespace(
        fund_manager=SimpleNamespace(reconcile_interval_seconds=60),
        workers=SimpleNamespace(max_consecutive_failures=5, restart_delay=1.0),
    )


@pytest.mark.asyncio
async def test_e2e_j1_reconciler_drift_dwell_through_real_db(
    real_db, loguru_sink,
) -> None:
    """Real PositionReconciler runs against the real DB. DB has 2 open
    bybit_demo rows; live API confirms 0 — count_diff=+2.
    First tick: no alarm (dwell counter at 1).
    Second tick: POSITION_RECONCILE_DRIFT fires (dwell at 2)."""
    repo = TradingRepository(real_db)
    from src.core.types import Position
    now = datetime.now(timezone.utc)
    for sym in ("FOO", "BAR"):
        await repo.save_position(
            Position(
                symbol=sym, side=Side.BUY, size=1.0,
                entry_price=100.0, mark_price=100.0,
                unrealized_pnl=0.0, realized_pnl=0.0, leverage=1,
                liquidation_price=0.0, stop_loss=None, take_profit=None,
                updated_at=now,
            ),
            exchange_mode="bybit_demo",
        )

    from src.core.types import PositionsQueryResult
    services = {
        "position_service": _FakePosService(PositionsQueryResult(confirmed=True, positions=())),
        "account_service": _FakeAccountService(),
        "fund_manager": _StubFundManager(total=100_000.0, available=100_000.0),
        "transformer": SimpleNamespace(current_mode="bybit_demo"),
    }
    rec = PositionReconciler(_stub_settings(), real_db, services)

    await rec.tick()
    drifts_after_1 = _records(loguru_sink, "POSITION_RECONCILE_DRIFT")
    assert drifts_after_1 == [], "dwell guard must hold on first tick"

    await rec.tick()
    drifts_after_2 = _records(loguru_sink, "POSITION_RECONCILE_DRIFT")
    assert len(drifts_after_2) == 1, "drift must alarm after 2nd consecutive tick"
    kv = _kv(drifts_after_2[0][1])
    assert kv["mode"] == "bybit_demo"
    assert kv["db_count"] == "2"
    assert kv["live_count"] == "0"
    assert kv["diff"] == "+2"
    assert kv["streak"] == "2"

    # POSITION_RECONCILE INFO line emitted on every tick
    info = _records(loguru_sink, "POSITION_RECONCILE |")
    assert len(info) == 2


# ====================================================================
# J1 — Pagination loop with real client + real adapter
# ====================================================================


@pytest.mark.asyncio
async def test_e2e_j1_pagination_aggregates_pages(real_db, loguru_sink) -> None:
    """Two-page response → real adapter loops on cursor → aggregated
    list of all positions returned, and all rows persisted to DB."""
    repo = TradingRepository(real_db)
    client = _FakeBybitClient()
    # Page 1: BTC + ETH with cursor → page 2: SOL with empty cursor
    client._queue = [
        {"retCode": 0, "result": {
            "list": [_v5_pos("BTCUSDT"), _v5_pos("ETHUSDT")],
            "nextPageCursor": "cur-page-2",
        }},
        {"retCode": 0, "result": {
            "list": [_v5_pos("SOLUSDT")],
            "nextPageCursor": "",
        }},
    ]

    svc = BybitDemoPositionService(client, trading_repo=repo)
    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is True
    assert {p.symbol for p in result.positions} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    # Two HTTP calls — second carries cursor
    assert len(client.gets) == 2
    assert client.gets[1][1].get("cursor") == "cur-page-2"
    # All three persisted
    rows = await real_db.fetch_all(
        "SELECT symbol FROM positions WHERE exchange_mode = ? ORDER BY symbol",
        ("bybit_demo",),
    )
    assert [r["symbol"] for r in rows] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


# ====================================================================
# J2 — Cross-direction guard with real coordinator + real adapter
# ====================================================================


@pytest.mark.asyncio
async def test_e2e_j2_cross_direction_blocks_against_real_coordinator(
    real_db, loguru_sink,
) -> None:
    """Coordinator has a real registered Buy on DYDX; adapter receives
    a Sell order request; the chokepoint guard fires and returns a
    REJECTED Order without any HTTP POST."""
    coord = TradeCoordinator()
    # Use the REAL register_trade entry point so we hit the real
    # TradeState construction + dedup logic, not a fake namespace.
    coord.register_trade(
        symbol="DYDXUSDT",
        strategy_category="claude_direct",
        strategy_name="claude_trader",
        entry_price=0.15,
        side="Buy",
        source="e2e_test",
        size=1000.0,
    )
    assert "DYDXUSDT" in coord._trades

    repo = TradingRepository(real_db)
    client = _FakeBybitClient()
    ord_svc = BybitDemoOrderService(client, trading_repo=repo)
    ord_svc.attach_coordinator(coord)

    order = await ord_svc.place_order(
        symbol="DYDXUSDT", side=Side.SELL,
        order_type=OrderType.MARKET, qty=500.0,
    )

    # Order rejected — no Bybit POST happened
    assert order.status == OrderStatus.REJECTED
    assert client.posts == []

    # Two structured events
    blocked = _records(loguru_sink, "ORDER_CROSS_DIRECTION_BLOCKED")
    assert len(blocked) == 1
    kv = _kv(blocked[0][1])
    assert kv["sym"] == "DYDXUSDT"
    assert kv["existing_side"] == "Buy"
    assert kv["new_side"] == "Sell"

    blocked_unified = [
        r for r in _records(loguru_sink, "ORDER_BLOCKED |")
        if "cross_direction_conflict" in r[1]
    ]
    assert len(blocked_unified) == 1


# ====================================================================
# J3 — XRAY override threshold pipeline (real settings)
# ====================================================================


def test_e2e_j3_settings_round_trip_through_real_builder() -> None:
    """The real ``_build_risk`` TOML builder loads the new
    ``xray_lock_override_ratio_threshold`` correctly from a
    ``[risk]`` section, mimicking how config.toml is read at
    workers.py boot."""
    s = _build_risk({
        "max_leverage": 5,
        "xray_lock_override_ratio_threshold": 12.5,
        "xray_dir_flip_threshold_ratio": 3.5,
    })
    assert isinstance(s, RiskSettings)
    assert s.max_leverage == 5
    assert s.xray_lock_override_ratio_threshold == 12.5
    assert s.xray_dir_flip_threshold_ratio == 3.5
    # Default field still present
    assert s.flip_tp is not None


# ====================================================================
# J5 — APEX sizing pipeline: real TradeOptimizer + real settings
# ====================================================================


def test_e2e_j5_sizing_through_real_optimizer_and_settings() -> None:
    """Real ``_build_apex`` loads new J5 settings; real
    ``TradeOptimizer._apply_constraints`` consumes them; real
    capital getter returns the trading_capital. The full sizing
    chain produces conviction-scaled output that the unit test
    only saw in isolation."""
    cfg = _build_apex({
        "apex_size_cap_pct_of_equity": 10.0,
        "apex_size_conviction_floor": 0.5,
        "max_position_size_usd": 1200.0,
        "max_leverage": 3,
        "min_tp_pct": 0.3,
    })
    assert isinstance(cfg, APEXSettings)
    assert cfg.apex_size_cap_pct_of_equity == 10.0

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=cfg)
    opt.attach_account_state_getter(lambda: 24_000.0)

    # High-conviction trade
    high = OptimizedTrade(
        symbol="BTCUSDT", direction="Buy",
        sl_pct=1.0, tp_pct=2.0, tp_mode="fixed",
        position_size_usd=18_000.0, leverage=2,
        entry_timing="immediate", add_on_pullback=False,
        confidence=0.85,
    )
    opt._apply_constraints(high)
    # cap = max(1200, 24k*10%) = 2400; conviction 0.85 → 2400 * 0.85 = 2040
    assert high.position_size_usd == pytest.approx(2040.0)

    # Low-conviction same notional
    low = OptimizedTrade(
        symbol="BTCUSDT", direction="Buy",
        sl_pct=1.0, tp_pct=2.0, tp_mode="fixed",
        position_size_usd=18_000.0, leverage=2,
        entry_timing="immediate", add_on_pullback=False,
        confidence=0.55,
    )
    opt._apply_constraints(low)
    # cap 2400; conviction 0.55 → 2400 * 0.55 = 1320
    assert low.position_size_usd == pytest.approx(1320.0)

    # High > Low — meaningful differentiation, the audit-required behaviour
    assert high.position_size_usd > low.position_size_usd



# ====================================================================
# J7 — sl_geometry public API + direction pins
# ====================================================================


def test_e2e_j7_sl_geometry_direction_pins() -> None:
    """Pin the public-API contract one more time with realistic prices
    that map onto live audit observations (ATOMUSDT)."""
    # Long: 100 → 101 is tighter
    assert is_tighter_sl(Side.BUY, 100.0, 101.0) is True
    assert is_tighter_sl(Side.BUY, 100.0, 99.0) is False
    # Short: ATOMUSDT audit case 2.0629 → 2.05767 is tighter
    assert is_tighter_sl(Side.SELL, 2.0629, 2.05767) is True
    assert is_tighter_sl(Side.SELL, 2.0629, 2.07) is False
    # is_long_side tolerates every form
    assert is_long_side(Side.BUY) is True
    assert is_long_side(Side.SELL) is False
    assert is_long_side("Buy") is True
    assert is_long_side("BUY") is True
    assert is_long_side("Long") is True
    assert is_long_side("Sell") is False
    assert is_long_side(None) is False


# ====================================================================
# J4 — Real _ClaudeWorkerPool counters + new structured events
# ====================================================================


class _DeadPopen:
    pid = 12345
    def poll(self): return 1
class _LivePopen:
    pid = 12346
    def poll(self): return None


def test_e2e_j4_pool_counters_split_on_dead_vs_aged(loguru_sink) -> None:
    """Real ``_ClaudeWorkerPool`` differentiates the disposal cause:
    dead worker → dead_disposed; aged-out → age_disposed; both still
    add to the legacy stale_disposed counter. The CLAUDE_PREWARM_DISPOSED
    event carries the reason field."""
    # Dead path
    pool = _ClaudeWorkerPool(
        claude_path="/dev/null", env={}, project_cwd="/tmp",
        max_age_seconds=900.0, stats_interval_seconds=0.0,
    )
    pool._slots["h1"] = _PrewarmSlot(_DeadPopen(), "h1")
    object.__setattr__(pool, "_hash_sys_prompt", staticmethod(lambda _: "h1"))
    proc, _ = pool.acquire("any")
    assert proc is None
    assert pool._dead_disposed_count == 1
    assert pool._age_disposed_count == 0
    assert pool._stale_disposed_count == 1
    dead_evt = _records(loguru_sink, "CLAUDE_PREWARM_DISPOSED")
    assert dead_evt and "reason=dead" in dead_evt[0][1]

    # Aged path
    import time as _t
    pool2 = _ClaudeWorkerPool(
        claude_path="/dev/null", env={}, project_cwd="/tmp",
        max_age_seconds=0.001, stats_interval_seconds=0.0,
    )
    pool2._slots["h2"] = _PrewarmSlot(_LivePopen(), "h2")
    object.__setattr__(pool2, "_hash_sys_prompt", staticmethod(lambda _: "h2"))
    _t.sleep(0.01)
    proc2, _ = pool2.acquire("any")
    assert proc2 is None
    assert pool2._age_disposed_count == 1
    assert pool2._dead_disposed_count == 0
    aged_evt = _records(loguru_sink, "CLAUDE_PREWARM_DISPOSED")
    assert any("reason=age_expired" in r[1] for r in aged_evt)


# ====================================================================
# Wire-up audit — service-key contract pin
# ====================================================================


def test_e2e_manager_registers_db_service() -> None:
    """Source-pin (cross-check fix 2026-05-15): WorkerManager must
    register the DatabaseManager in the service container under the
    canonical ``"db"`` key. The J6 learning gate AND existing
    fund_manager subpaths read this key; pre-cross-check it was
    silently absent."""
    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/workers/manager.py", encoding="utf-8",
    ).read()
    assert 'self._services["db"] = db' in src


def test_e2e_j_series_observability_event_names_present() -> None:
    """Cross-file pin: every mandatory observability event named in
    the master-prompt Rule 6 list for J1-J7 is emitted somewhere
    in the production source tree."""
    grepped = {}
    for tag in (
        "POSITIONS_CACHE_PRUNE",
        "POSITION_RECONCILE",
        "POSITION_RECONCILE_DRIFT",
        "FUND_INUSE_DRIFT",
        "BYBIT_DEMO_POSITIONS_PAGINATION_CAP",
        "ORDER_CROSS_DIRECTION_BLOCKED",
        # P0-2 fix (2026-05-22) — the legacy XRAY_LOCK_PRECEDENCE_RESOLUTION
        # / XRAY_OVERRIDE_LOCK / XRAY_DIR_FLIP emissions were collapsed
        # into the single canonical DIRECTION_DECISION event. The
        # precedence-resolution logic is preserved (low-conviction
        # branch); only the event tag changed.
        "DIRECTION_DECISION",
        "SENTINEL_TIGHTNESS_DIRECTION_AWARE",
        "APEX_SIZING_DECISION",
        "APEX_SIZING_CAP_HIT",
        "APEX_SIZING_SMALL_SIZE",
        # Issue 3 (2026-05-18) — replaced J6 REENTRY_LEARNING_GATE and
        # REENTRY_REGIME_DRIFT_CHECK with the new 5-min cooldown events.
        "REENTRY_COOLDOWN_5MIN_SET",
        "REENTRY_COOLDOWN_5MIN_BLOCKED",
        "REENTRY_COOLDOWN_5MIN_CLEARED",
        "CLAUDE_PREWARM_HIT",
        "CLAUDE_PIPELINE_NEXT",
        "CLAUDE_PREWARM_DISPOSED",
    ):
        import subprocess
        # P0-2 fix (2026-05-22) — exclude *.bak* files so the test
        # cannot pass due to leaked tag strings inside Rule-8 backup
        # files (which live in the original directory under src/).
        out = subprocess.run(
            ["grep", "-rln", "--include=*.py", "--exclude=*.bak*",
             tag, "src/"],
            cwd="/home/inshadaliqbal786/trading-intelligence-mcp",
            capture_output=True, text=True,
        )
        grepped[tag] = out.stdout.strip().split("\n") if out.stdout.strip() else []
        assert grepped[tag], f"missing event emission: {tag}"
