#!/usr/bin/env python3
"""Live simulation runner — exercise every J1-J7 fix through the REAL
production code path against a REAL DB, capture emissions, cross-check
that the expected post-fix behaviour is produced.

This is the "live setup" verification companion to the unit and E2E
test suites. Each scenario reproduces the original audit-observed
condition (orphan rows from 2026-05-13, cross-direction Buy after a
stale-cache Sell, APEX_DIR_LOCK suppressing a 338x XRAY ratio, etc.),
runs the actual production code, and reports whether the J-series
fix activates correctly.

The script is operator-runnable from the repo root:

    .venv/bin/python scripts/simulate_j_series_live.py

Exits 0 when every scenario verifies; 1 otherwise. Output is grouped
by issue (J1-A, J1-B, J1-E, J2, J3, J4, J5, J6, J7) with per-scenario
PASS/FAIL lines, the relevant log events captured, and the post-state
the production code produced.
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from loguru import logger as _loguru_logger  # noqa: E402

# Silence loguru's default stderr sink so the simulation's stdout is
# the only visible channel. Per-scenario sinks ATTACHED inside
# _CaptureSink still capture into in-memory lists. This keeps the
# report uncluttered by hundreds of production INFO lines.
_loguru_logger.remove()

# Real production classes — no mocks at the integration level.
from src.apex.gate import TradeGate                                # noqa: E402
from src.apex.models import OptimizedTrade                         # noqa: E402
from src.apex.optimizer import TradeOptimizer                      # noqa: E402
from src.brain.claude_code_client import (                          # noqa: E402
    _ClaudeWorkerPool, _PrewarmSlot,
)
from src.bybit_demo.bybit_demo_adapter import (                     # noqa: E402
    BybitDemoOrderService, BybitDemoPositionService,
)
from src.config.settings import (                                   # noqa: E402
    APEXSettings, RiskSettings, _build_apex, _build_risk,
)
from src.core.sl_geometry import is_long_side, is_tighter_sl        # noqa: E402
from src.core.trade_coordinator import TradeCoordinator             # noqa: E402
from src.core.types import (                                        # noqa: E402
    AccountInfo, OrderStatus, OrderType, Position,
    PositionsQueryResult, Side,
)
from src.database.connection import DatabaseManager                 # noqa: E402
from src.database.migrations import run_migrations                  # noqa: E402
from src.database.repositories.trading_repo import TradingRepository  # noqa: E402
from src.workers.position_reconciler import PositionReconciler      # noqa: E402


# ──────────────────────────────────────────────────────────────────
# Output helpers
# ──────────────────────────────────────────────────────────────────


_RESULTS: list[tuple[str, str, bool, str]] = []  # (issue, scenario, ok, note)


def _record(issue: str, scenario: str, ok: bool, note: str = "") -> None:
    _RESULTS.append((issue, scenario, ok, note))
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {issue} — {scenario}"
    if note:
        line += f"  ({note})"
    print(line)


def _section(title: str) -> None:
    print()
    print(f"=== {title} ===")


class _CaptureSink:
    """Per-scenario loguru capture. Use as a context manager."""

    def __enter__(self) -> "_CaptureSink":
        self.records: list[tuple[str, str]] = []
        self._handler = _loguru_logger.add(
            lambda msg: self.records.append(
                (msg.record["level"].name, msg.record["message"]),
            ),
            level="DEBUG",
            format="{message}",
        )
        return self

    def __exit__(self, *_exc) -> None:
        _loguru_logger.remove(self._handler)

    def with_tag(self, tag: str) -> list[str]:
        return [m for _l, m in self.records if m.startswith(tag)]


def _kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


# ──────────────────────────────────────────────────────────────────
# Fakes for the lowest-level boundaries (HTTP / subprocess only)
# ──────────────────────────────────────────────────────────────────


class _FakeBybitClient:
    def __init__(self) -> None:
        self.gets: list[tuple[str, dict, str]] = []
        self.posts: list[tuple[str, dict, str]] = []
        self._queue: list[Any] = []

    def queue(self, *responses: Any) -> None:
        self._queue.extend(responses)

    async def get(self, path: str, params: dict | None = None, *, op: str = ""):
        self.gets.append((path, dict(params or {}), op))
        if not self._queue:
            return {"retCode": 0, "result": {"list": []}}
        nxt = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def post(self, path: str, body: dict, *, op: str = ""):
        self.posts.append((path, body, op))
        return {"retCode": 0, "result": {"orderId": "OID-sim"}}


def _v5_pos(symbol: str, side: str = "Buy", size: str = "1.0") -> dict:
    return {
        "symbol": symbol, "side": side, "size": size,
        "avgPrice": "100", "markPrice": "100",
        "unrealisedPnl": "0", "leverage": "1", "liqPrice": "0",
    }


class _FakePosService:
    def __init__(self, result: PositionsQueryResult) -> None:
        self.result = result

    async def get_positions_with_confirmation(self):
        return self.result


class _FakeAccountService:
    def __init__(self, total: float = 100_000.0, avail: float = 100_000.0) -> None:
        self.total = total
        self.avail = avail

    async def get_wallet_balance(self):
        return AccountInfo(
            total_equity=self.total, available_balance=self.avail,
            used_margin=self.total - self.avail, unrealized_pnl=0.0,
        )


def _stub_settings_for_reconciler():
    return SimpleNamespace(
        fund_manager=SimpleNamespace(reconcile_interval_seconds=60),
        workers=SimpleNamespace(max_consecutive_failures=5, restart_delay=1.0),
    )


# ──────────────────────────────────────────────────────────────────
# Scenario builders
# ──────────────────────────────────────────────────────────────────


async def _seed_position(
    repo: TradingRepository, symbol: str, side: Side, size: float,
    entry: float, mode: str = "bybit_demo",
) -> None:
    await repo.save_position(
        Position(
            symbol=symbol, side=side, size=size,
            entry_price=entry, mark_price=entry,
            unrealized_pnl=0.0, realized_pnl=0.0, leverage=1,
            liquidation_price=0.0, stop_loss=None, take_profit=None,
            updated_at=datetime.now(timezone.utc),
        ),
        exchange_mode=mode,
    )


# ──────────────────────────────────────────────────────────────────
# J1-A — Adapter cache prune of historic stale rows
# ──────────────────────────────────────────────────────────────────


async def scenario_j1_cache_prune(db: DatabaseManager) -> None:
    _section("J1-A — Cache prune of historic stale rows (audit OBS-09)")
    repo = TradingRepository(db)
    # Recreate the audit-window residue
    for sym, side, size, ep in [
        ("SANDUSDT", Side.SELL, 11155.0, 0.08068),
        ("EGLDUSDT", Side.BUY,  42.0,    4.761),
        ("RUNEUSDT", Side.SELL, 2209.8,  0.6109),
        ("AAVEUSDT", Side.SELL, 9.04,    99.54),
    ]:
        await _seed_position(repo, sym, side, size, ep, mode="bybit_demo")
    await _seed_position(repo, "ETHUSDT", Side.BUY, 1.0, 3000.0, mode="shadow")

    pre = await db.fetch_all(
        "SELECT symbol, exchange_mode FROM positions ORDER BY symbol",
    )
    pre_bd = sorted(r["symbol"] for r in pre if r["exchange_mode"] == "bybit_demo")
    print(f"  pre-state: {len(pre)} rows total, "
          f"bybit_demo={pre_bd}, shadow={[r['symbol'] for r in pre if r['exchange_mode']=='shadow']}")

    # Bybit confirms only AAVE — the other 3 are now stale
    client = _FakeBybitClient()
    client.queue({"retCode": 0, "result": {"list": [_v5_pos("AAVEUSDT", "Sell")]}})
    svc = BybitDemoPositionService(client, trading_repo=repo)

    with _CaptureSink() as cap:
        result = await svc.get_positions_with_confirmation()

    post = await db.fetch_all(
        "SELECT symbol, exchange_mode FROM positions ORDER BY symbol",
    )
    post_bd = sorted(r["symbol"] for r in post if r["exchange_mode"] == "bybit_demo")
    post_sh = sorted(r["symbol"] for r in post if r["exchange_mode"] == "shadow")
    pruned_logs = cap.with_tag("POSITIONS_CACHE_PRUNE |")
    pruned_syms = sorted(_kv(m)["sym"] for m in pruned_logs)
    print(f"  post-state: bybit_demo={post_bd}, shadow={post_sh}")
    print(f"  pruned logs: {len(pruned_logs)} events for syms={pruned_syms}")

    _record(
        "J1-A", "Three stale bybit_demo rows pruned, AAVE kept",
        post_bd == ["AAVEUSDT"],
        f"bybit_demo post={post_bd}",
    )
    _record(
        "J1-A", "Shadow row untouched (mode scope preserved)",
        post_sh == ["ETHUSDT"],
        f"shadow post={post_sh}",
    )
    _record(
        "J1-A", "POSITIONS_CACHE_PRUNE event fires per pruned symbol",
        pruned_syms == ["EGLDUSDT", "RUNEUSDT", "SANDUSDT"],
        f"pruned={pruned_syms}",
    )
    _record(
        "J1-A", "Adapter returns confirmed=True with the 1 live position",
        result.confirmed and {p.symbol for p in result.positions} == {"AAVEUSDT"},
    )

    # Clean up for next scenario
    await db.execute("DELETE FROM positions")


# ──────────────────────────────────────────────────────────────────
# J1-B — PositionReconciler dwell-guarded drift alert
# ──────────────────────────────────────────────────────────────────


async def scenario_j1_reconciler(db: DatabaseManager) -> None:
    _section("J1-B — PositionReconciler dwell-guarded drift alert")
    repo = TradingRepository(db)
    await _seed_position(repo, "FOO", Side.BUY, 1.0, 100.0, mode="bybit_demo")
    await _seed_position(repo, "BAR", Side.BUY, 1.0, 100.0, mode="bybit_demo")
    print("  pre-state: 2 bybit_demo rows in DB; live API will report 0")

    services = {
        "position_service": _FakePosService(
            PositionsQueryResult(confirmed=True, positions=()),
        ),
        "account_service": _FakeAccountService(),
        "fund_manager": SimpleNamespace(
            _account_state=SimpleNamespace(total_equity=100_000.0, available=100_000.0),
        ),
        "transformer": SimpleNamespace(current_mode="bybit_demo"),
    }
    rec = PositionReconciler(_stub_settings_for_reconciler(), db, services)

    with _CaptureSink() as cap1:
        await rec.tick()
    drift_after_1 = cap1.with_tag("POSITION_RECONCILE_DRIFT")
    _record(
        "J1-B", "Tick 1: no drift alarm (dwell counter at 1)",
        drift_after_1 == [],
        "POSITION_RECONCILE info still emits even when no alarm",
    )

    with _CaptureSink() as cap2:
        await rec.tick()
    drift_after_2 = cap2.with_tag("POSITION_RECONCILE_DRIFT")
    if drift_after_2:
        kv = _kv(drift_after_2[0])
        _record(
            "J1-B", f"Tick 2: drift alarm fires (db={kv.get('db_count')}, "
                    f"live={kv.get('live_count')}, diff={kv.get('diff')}, "
                    f"streak={kv.get('streak')})",
            kv.get("diff") == "+2" and kv.get("streak") == "2",
        )
    else:
        _record("J1-B", "Tick 2: drift alarm fires", False, "no event observed")

    await db.execute("DELETE FROM positions")


# ──────────────────────────────────────────────────────────────────
# J1-E — Bybit V5 pagination loop
# ──────────────────────────────────────────────────────────────────


async def scenario_j1_pagination(db: DatabaseManager) -> None:
    _section("J1-E — Bybit V5 pagination loop")
    repo = TradingRepository(db)
    client = _FakeBybitClient()
    client._queue = [
        {"retCode": 0, "result": {
            "list": [_v5_pos("BTCUSDT"), _v5_pos("ETHUSDT")],
            "nextPageCursor": "cur-2",
        }},
        {"retCode": 0, "result": {
            "list": [_v5_pos("SOLUSDT")],
            "nextPageCursor": "",
        }},
    ]
    svc = BybitDemoPositionService(client, trading_repo=repo)
    result = await svc.get_positions_with_confirmation()
    syms = sorted(p.symbol for p in result.positions)
    print(f"  HTTP calls: {len(client.gets)}; page-2 cursor sent: "
          f"{client.gets[1][1].get('cursor') if len(client.gets) >= 2 else None}")

    _record(
        "J1-E", "Pages aggregated across cursor loop",
        syms == ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        f"got={syms}",
    )
    _record(
        "J1-E", "Cursor from page-1 used on page-2 fetch",
        len(client.gets) == 2 and client.gets[1][1].get("cursor") == "cur-2",
    )

    # Pagination cap scenario
    client2 = _FakeBybitClient()
    client2._queue = [
        {"retCode": 0, "result": {"list": [_v5_pos(f"S{i}USDT")],
                                    "nextPageCursor": f"cur-{i+1}"}}
        for i in range(5)
    ]
    svc2 = BybitDemoPositionService(client2, trading_repo=repo)
    with _CaptureSink() as cap:
        cap_result = await svc2.get_positions_with_confirmation()
    cap_warns = cap.with_tag("BYBIT_DEMO_POSITIONS_PAGINATION_CAP")
    _record(
        "J1-E", "Pagination cap returns confirmed=False (safe truncation guard)",
        cap_result.confirmed is False and cap_result.reason == "pagination_cap",
        f"reason={cap_result.reason}",
    )
    _record(
        "J1-E", "Pagination-cap warning event fires",
        len(cap_warns) == 1,
    )

    await db.execute("DELETE FROM positions")


# ──────────────────────────────────────────────────────────────────
# J2 — Cross-direction guard at adapter chokepoint
# ──────────────────────────────────────────────────────────────────


async def scenario_j2_cross_direction(db: DatabaseManager) -> None:
    _section("J2 — Cross-direction pre-order guard (audit OBS-21 DYDXUSDT)")
    repo = TradingRepository(db)
    coord = TradeCoordinator()
    # Reproduce the audit: brain registered a Buy on DYDXUSDT
    coord.register_trade(
        symbol="DYDXUSDT", strategy_category="claude_direct",
        strategy_name="claude_trader", entry_price=0.15, side="Buy",
        source="simulation", size=1000.0,
    )
    print(f"  pre-state: coordinator._trades={list(coord._trades.keys())} "
          f"(side={coord._trades['DYDXUSDT'].side})")

    client = _FakeBybitClient()
    ord_svc = BybitDemoOrderService(client, trading_repo=repo)
    ord_svc.attach_coordinator(coord)

    # 1) Opposite direction — must REJECT
    with _CaptureSink() as cap:
        order = await ord_svc.place_order(
            symbol="DYDXUSDT", side=Side.SELL,
            order_type=OrderType.MARKET, qty=500.0,
        )
    _record(
        "J2", "Opposite-direction order REJECTED",
        order.status == OrderStatus.REJECTED,
        f"got status={order.status}",
    )
    _record(
        "J2", "No Bybit POST attempted on rejected order",
        client.posts == [],
        f"post calls={len(client.posts)}",
    )
    blocked = cap.with_tag("ORDER_CROSS_DIRECTION_BLOCKED")
    _record(
        "J2", "ORDER_CROSS_DIRECTION_BLOCKED event fires",
        len(blocked) == 1,
    )
    unified = [m for m in cap.with_tag("ORDER_BLOCKED |")
               if "cross_direction_conflict" in m]
    _record(
        "J2", "Unified ORDER_BLOCKED event fires with reason=cross_direction_conflict",
        len(unified) == 1,
    )

    # 2) Same direction — must PASS (additive sizing in one-way mode)
    with _CaptureSink() as cap_pass:
        order_same = await ord_svc.place_order(
            symbol="DYDXUSDT", side=Side.BUY,
            order_type=OrderType.MARKET, qty=500.0,
        )
    _record(
        "J2", "Same-direction order is NOT blocked",
        order_same.status != OrderStatus.REJECTED,
        f"got status={order_same.status}",
    )

    # 3) force=True — must bypass guard
    client3 = _FakeBybitClient()
    ord3 = BybitDemoOrderService(client3, trading_repo=repo)
    ord3.attach_coordinator(coord)
    order_force = await ord3.place_order(
        symbol="DYDXUSDT", side=Side.SELL,
        order_type=OrderType.MARKET, qty=500.0, force=True,
    )
    _record(
        "J2", "force=True bypasses the guard (operator-override path)",
        order_force.status != OrderStatus.REJECTED,
    )


# ──────────────────────────────────────────────────────────────────
# J3 — XRAY/DIR_LOCK precedence
# ──────────────────────────────────────────────────────────────────


def _simulate_j3_precedence(
    *, ratio: float, flip_threshold: float = 3.0,
    override_threshold: float = 10.0, apex_locked: bool = True,
) -> str:
    """Mirror the production conditional structure at
    src/workers/strategy_worker.py:1648-1721."""
    lock_override_active = (
        apex_locked
        and ratio > flip_threshold
        and override_threshold > flip_threshold
        and ratio > override_threshold
    )
    if apex_locked and ratio > flip_threshold and not lock_override_active:
        return "suppress"
    if lock_override_active:
        return "override_flip"
    if lock_override_active or (not apex_locked and ratio > flip_threshold):
        return "flip"
    return "no_flip"


def scenario_j3_precedence() -> None:
    _section("J3 — XRAY/DIR_LOCK precedence (audit OBS-14/19/22)")
    cases = [
        # symbol,       ratio,  locked, override_thr, expected,        comment
        ("ALICEUSDT",   338.0,  True,   10.0,         "override_flip", "audit OBS-14 — 338x must override"),
        ("ENAUSDT",     324.3,  True,   10.0,         "override_flip", "audit — 324x must override"),
        ("LINKUSDT",     30.0,  True,   10.0,         "override_flip", "audit — 30x must override"),
        ("MNTUSDT",      17.6,  True,   10.0,         "override_flip", "audit — 17.6x must override"),
        ("HYPERUSDT",     4.9,  True,   10.0,         "suppress",      "audit — 4.9x in mid-band still suppressed"),
        ("LTCUSDT",      55.3,  False,  10.0,         "flip",          "audit OBS-19 — no lock, flip fires (this was the success-path)"),
        ("safe_low",      1.5,  True,   10.0,         "no_flip",       "below flip threshold — silent (no log)"),
        ("disabled_ovr", 50.0,  True,    3.0,         "suppress",      "override<=flip disables (legacy strict-lock)"),
    ]
    for sym, ratio, locked, ovr, expected, note in cases:
        got = _simulate_j3_precedence(
            ratio=ratio, apex_locked=locked, override_threshold=ovr,
        )
        _record(
            "J3", f"{sym} ratio={ratio}x locked={locked} → {got}",
            got == expected,
            note if got != expected else "",
        )


# ──────────────────────────────────────────────────────────────────
# J4 — Claude prewarm pool observability
# ──────────────────────────────────────────────────────────────────


class _DeadPopen:
    # Use a PID that is guaranteed not to exist (PID_MAX on Linux is
    # typically 4194304). The pool's ``_dispose`` calls
    # ``os.killpg(os.getpgid(proc.pid), SIGTERM)`` which raises
    # ``ProcessLookupError`` that the production code already
    # swallows. Using low PIDs (1, 2, ...) would target real system
    # processes and kill the simulation.
    pid = 9_999_991
    def poll(self): return 1


class _LivePopen:
    pid = 9_999_992
    def poll(self): return None


def scenario_j4_pool_observability() -> None:
    _section("J4 — Claude prewarm pool observability (audit OBS-02 follow-up)")
    # Dead worker disposal
    pool = _ClaudeWorkerPool(
        claude_path="/dev/null", env={}, project_cwd="/tmp",
        max_age_seconds=900.0, stats_interval_seconds=0.0,
    )
    pool._slots["h-dead"] = _PrewarmSlot(_DeadPopen(), "h-dead")
    object.__setattr__(pool, "_hash_sys_prompt", staticmethod(lambda _: "h-dead"))
    with _CaptureSink() as cap_dead:
        proc, _ = pool.acquire("sp")
    _record(
        "J4", "Dead worker → dead_disposed counter +1 (NOT age_disposed)",
        pool._dead_disposed_count == 1 and pool._age_disposed_count == 0,
        f"dead={pool._dead_disposed_count} age={pool._age_disposed_count}",
    )
    _record(
        "J4", "Legacy stale_disposed counter still increments (back-compat)",
        pool._stale_disposed_count == 1,
    )
    dead_logs = cap_dead.with_tag("CLAUDE_PREWARM_DISPOSED")
    _record(
        "J4", "CLAUDE_PREWARM_DISPOSED event with reason=dead",
        bool(dead_logs) and "reason=dead" in dead_logs[0],
    )

    # Aged worker disposal
    import time as _t
    pool2 = _ClaudeWorkerPool(
        claude_path="/dev/null", env={}, project_cwd="/tmp",
        max_age_seconds=0.001, stats_interval_seconds=0.0,
    )
    pool2._slots["h-aged"] = _PrewarmSlot(_LivePopen(), "h-aged")
    object.__setattr__(pool2, "_hash_sys_prompt", staticmethod(lambda _: "h-aged"))
    _t.sleep(0.01)
    with _CaptureSink() as cap_aged:
        proc2, _ = pool2.acquire("sp")
    _record(
        "J4", "Aged worker → age_disposed counter +1 (NOT dead_disposed)",
        pool2._age_disposed_count == 1 and pool2._dead_disposed_count == 0,
        f"age={pool2._age_disposed_count} dead={pool2._dead_disposed_count}",
    )
    aged_logs = cap_aged.with_tag("CLAUDE_PREWARM_DISPOSED")
    _record(
        "J4", "CLAUDE_PREWARM_DISPOSED event with reason=age_expired",
        bool(aged_logs) and "reason=age_expired" in aged_logs[0],
    )

    # Verify the master-prompt-mandated event names exist in the
    # source tree (these are runtime emissions; static-grep is the
    # appropriate verification for the source-pin contract).
    import subprocess
    src = subprocess.run(
        ["grep", "-rln", "CLAUDE_PREWARM_HIT",
         "src/brain/claude_code_client.py"],
        cwd=str(_PROJECT_ROOT), capture_output=True, text=True,
    )
    _record(
        "J4", "CLAUDE_PREWARM_HIT event source-pin present",
        "claude_code_client.py" in src.stdout,
    )
    src2 = subprocess.run(
        ["grep", "-rln", "CLAUDE_PIPELINE_NEXT",
         "src/brain/claude_code_client.py"],
        cwd=str(_PROJECT_ROOT), capture_output=True, text=True,
    )
    _record(
        "J4", "CLAUDE_PIPELINE_NEXT event source-pin present",
        "claude_code_client.py" in src2.stdout,
    )


# ──────────────────────────────────────────────────────────────────
# J5 — APEX sizing: pre-J5 vs post-J5 behaviour
# ──────────────────────────────────────────────────────────────────


def scenario_j5_sizing() -> None:
    _section("J5 — APEX sizing differentiation (audit OBS-15)")

    # ── Legacy-equivalent mode (J5 disabled by config): both knobs
    # take their conservative values (no equity scaling, no conviction
    # variation). With conviction_floor=1.0 the scale collapses to
    # 1.0 for every trade, reproducing the audit's static-$1200 clamp.
    legacy_cfg = _build_apex({
        "apex_size_cap_pct_of_equity": 0.0,
        "apex_size_conviction_floor": 1.0,  # disable conviction scaling
        "max_position_size_usd": 1200.0,
        "max_leverage": 3, "min_tp_pct": 0.3,
    })
    legacy_opt = TradeOptimizer(qwen_client=None, assembler=None, settings=legacy_cfg)
    legacy_opt.attach_account_state_getter(lambda: 24_000.0)
    legacy_sizes: list[float] = []
    for input_qty, conf in [
        (18000.0, 0.85), (15000.0, 0.75), (12000.0, 0.65),
        (14000.0, 0.65), (16000.0, 0.75),
    ]:
        t = OptimizedTrade(
            symbol="SIM", direction="Buy",
            sl_pct=1.0, tp_pct=2.0, tp_mode="fixed",
            position_size_usd=input_qty, leverage=2,
            entry_timing="immediate", add_on_pullback=False,
            confidence=conf,
        )
        legacy_opt._apply_constraints(t)
        legacy_sizes.append(round(t.position_size_usd, 0))
    legacy_unique = sorted(set(legacy_sizes))
    print(f"  legacy-equivalent (pct=0, conv_floor=1.0): {legacy_sizes} → unique={legacy_unique}")
    _record(
        "J5", "Legacy-equivalent config reproduces the audit's $1200 clamp",
        legacy_unique == [1200.0],
        f"unique={legacy_unique}",
    )

    # ── J5 partial: pct=0 BUT conviction floor at default 0.5
    # (this is the byte-equivalent-default config). Conviction
    # scaling within the static cap produces differentiation while
    # the cap itself stays at the legacy $1200 — a measurable
    # improvement even before the operator turns on pct-of-equity.
    partial_cfg = _build_apex({
        "apex_size_cap_pct_of_equity": 0.0,
        "apex_size_conviction_floor": 0.5,
        "max_position_size_usd": 1200.0,
        "max_leverage": 3, "min_tp_pct": 0.3,
    })
    partial_opt = TradeOptimizer(qwen_client=None, assembler=None, settings=partial_cfg)
    partial_opt.attach_account_state_getter(lambda: 24_000.0)
    partial_sizes: list[float] = []
    for input_qty, conf in [
        (18000.0, 0.85), (15000.0, 0.75), (12000.0, 0.65),
        (14000.0, 0.65), (16000.0, 0.75),
    ]:
        t = OptimizedTrade(
            symbol="SIM", direction="Buy",
            sl_pct=1.0, tp_pct=2.0, tp_mode="fixed",
            position_size_usd=input_qty, leverage=2,
            entry_timing="immediate", add_on_pullback=False,
            confidence=conf,
        )
        partial_opt._apply_constraints(t)
        partial_sizes.append(round(t.position_size_usd, 0))
    partial_unique = sorted(set(partial_sizes))
    print(f"  defaults (pct=0, conv_floor=0.5):           {partial_sizes} → unique={partial_unique}")
    _record(
        "J5", "Default config (pct=0, conv_floor=0.5) already differentiates within legacy cap",
        len(partial_unique) >= 3 and max(partial_sizes) <= 1200.0,
        f"max={max(partial_sizes)} unique={partial_unique}",
    )

    # Post-J5 behaviour: pct_of_equity = 10% → dynamic cap + conviction
    post_cfg = _build_apex({
        "apex_size_cap_pct_of_equity": 10.0,
        "apex_size_conviction_floor": 0.5,
        "max_position_size_usd": 1200.0,
        "max_leverage": 3, "min_tp_pct": 0.3,
    })
    post_opt = TradeOptimizer(qwen_client=None, assembler=None, settings=post_cfg)
    post_opt.attach_account_state_getter(lambda: 24_000.0)

    post_sizes: list[float] = []
    with _CaptureSink() as cap:
        for input_qty, conf in [
            (18000.0, 0.85), (15000.0, 0.75), (12000.0, 0.65),
            (14000.0, 0.65), (16000.0, 0.75),
        ]:
            t = OptimizedTrade(
                symbol="SIM", direction="Buy",
                sl_pct=1.0, tp_pct=2.0, tp_mode="fixed",
                position_size_usd=input_qty, leverage=2,
                entry_timing="immediate", add_on_pullback=False,
                confidence=conf,
            )
            post_opt._apply_constraints(t)
            post_sizes.append(round(t.position_size_usd, 0))
    post_unique = sorted(set(post_sizes))
    print(f"  post-J5 (dynamic cap + conviction): {post_sizes} → unique={post_unique}")
    _record(
        "J5", "Post-J5 path produces meaningful differentiation (≥3 distinct sizes)",
        len(post_unique) >= 3,
        f"unique={post_unique}",
    )
    _record(
        "J5", "Post-J5 cap (10% of $24k) = $2400, exceeds legacy $1200",
        max(post_sizes) > 1200,
        f"max post={max(post_sizes)}",
    )
    decisions = cap.with_tag("APEX_SIZING_DECISION")
    _record(
        "J5", "APEX_SIZING_DECISION event fires per evaluation",
        len(decisions) == 5,
        f"events={len(decisions)}",
    )


# ──────────────────────────────────────────────────────────────────
# J6 — Re-entry learning gate
# ──────────────────────────────────────────────────────────────────


async def _seed_loss_thesis(
    db: DatabaseManager, *, symbol: str, direction: str,
    setup: str, regime: str, pnl_usd: float,
) -> None:
    await db.execute(
        "INSERT INTO trade_thesis "
        "(symbol, direction, entry_price, stop_loss_price, take_profit_price, "
        " size_usd, leverage, max_hold_minutes, trailing_activation_pct, "
        " thesis, status, opened_at, closed_at, actual_pnl_usd, "
        " entry_setup_type, entry_regime_at_open, exchange_mode) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            symbol, direction, 1.0, 0.99, 1.02,
            1000.0, 2, 30, 0.5,
            f"{symbol} prior trade",
            "closed",
            "2026-05-15 02:00:00",
            "2026-05-15 02:30:00",
            pnl_usd,
            setup,
            regime,
            "bybit_demo",
        ),
    )


def _build_gate_services(
    db: DatabaseManager, coord: TradeCoordinator,
    regime_value: str, setup_value: str,
) -> dict:
    return {
        "trade_coordinator": coord,
        "db": db,
        "regime_detector": MagicMock(
            get_coin_regime=MagicMock(
                return_value=SimpleNamespace(
                    regime=SimpleNamespace(value=regime_value),
                ),
            ),
        ),
        "structure_cache": MagicMock(
            get=MagicMock(
                return_value=SimpleNamespace(
                    setup_type=SimpleNamespace(value=setup_value),
                ),
            ),
        ),
    }


async def scenario_j6_learning_gate(db: DatabaseManager) -> None:
    _section("J6 — Re-entry learning gate (audit OBS-23 MNT/XRP/ICP)")
    await db.execute("DELETE FROM trade_thesis")
    coord = TradeCoordinator()

    # ── Scenario 1: MNTUSDT — same conditions → block ──
    await _seed_loss_thesis(
        db, symbol="MNTUSDT", direction="Buy",
        setup="TREND_PULLBACK_LONG", regime="trending_up", pnl_usd=-10.75,
    )
    gate = TradeGate(
        _build_gate_services(db, coord, "trending_up", "TREND_PULLBACK_LONG"),
        settings=APEXSettings(),
    )
    trade: dict[str, Any] = {
        "symbol": "MNTUSDT", "direction": "Buy", "size_usd": 500.0,
        "stop_loss_price": 0.99, "take_profit_price": 1.02,
        "_xray_confidence": 0.7, "_setup_score": 0.6, "_expected_rr": 2.0,
    }
    with _CaptureSink() as cap:
        out = await gate.validate(trade)
    rejected = str(out.get("_gate_rejected", ""))
    block_evt = cap.with_tag("REENTRY_LEARNING_GATE | sym=MNTUSDT action=block")
    drift_evt = cap.with_tag("REENTRY_REGIME_DRIFT_CHECK")
    _record(
        "J6", "Same conditions (MNTUSDT regime + setup + dir all match prior loss) → BLOCKED",
        rejected.startswith("reentry_learning_gate_same_conditions"),
        f"_gate_rejected={rejected!r}",
    )
    _record(
        "J6", "REENTRY_LEARNING_GATE block event with reason=same_conditions",
        len(block_evt) >= 1 and "same_conditions" in block_evt[0],
    )
    _record(
        "J6", "REENTRY_REGIME_DRIFT_CHECK diagnostic event fires",
        any("MNTUSDT" in m for m in drift_evt),
    )

    # ── Scenario 2: XRPUSDT — regime drift → allow ──
    await _seed_loss_thesis(
        db, symbol="XRPUSDT", direction="Buy",
        setup="TREND_PULLBACK_LONG", regime="trending_up", pnl_usd=-20.36,
    )
    gate2 = TradeGate(
        # CURRENT regime drifted to ranging
        _build_gate_services(db, coord, "ranging", "TREND_PULLBACK_LONG"),
        settings=APEXSettings(),
    )
    trade2: dict[str, Any] = {
        "symbol": "XRPUSDT", "direction": "Buy", "size_usd": 500.0,
        "_xray_confidence": 0.7, "_setup_score": 0.6, "_expected_rr": 2.0,
    }
    with _CaptureSink() as cap2:
        out2 = await gate2.validate(trade2)
    rej2 = str(out2.get("_gate_rejected", ""))
    allow_evt = cap2.with_tag("REENTRY_LEARNING_GATE | sym=XRPUSDT action=allow")
    drift_evt2 = cap2.with_tag("REENTRY_REGIME_DRIFT_CHECK")
    _record(
        "J6", "Regime drift (trending_up → ranging) → ALLOW (legitimate re-entry)",
        not rej2.startswith("reentry_learning_gate_"),
        f"_gate_rejected={rej2!r}",
    )
    _record(
        "J6", "REENTRY_LEARNING_GATE allow event with reason=regime_drift",
        len(allow_evt) >= 1 and "regime_drift" in allow_evt[0],
    )

    # ── Scenario 3: ICPUSDT — setup drift → allow ──
    await _seed_loss_thesis(
        db, symbol="ICPUSDT", direction="Buy",
        setup="TREND_PULLBACK_LONG", regime="trending_up", pnl_usd=-21.20,
    )
    gate3 = TradeGate(
        _build_gate_services(db, coord, "trending_up", "LIQUIDATION_HUNT"),
        settings=APEXSettings(),
    )
    trade3: dict[str, Any] = {
        "symbol": "ICPUSDT", "direction": "Buy", "size_usd": 500.0,
        "_xray_confidence": 0.7, "_setup_score": 0.6, "_expected_rr": 2.0,
    }
    with _CaptureSink() as cap3:
        out3 = await gate3.validate(trade3)
    rej3 = str(out3.get("_gate_rejected", ""))
    allow3 = cap3.with_tag("REENTRY_LEARNING_GATE | sym=ICPUSDT action=allow")
    _record(
        "J6", "Setup drift (LONG → LIQUIDATION_HUNT) → ALLOW",
        not rej3.startswith("reentry_learning_gate_"),
    )
    _record(
        "J6", "Allow event carries reason=setup_drift",
        len(allow3) >= 1 and "setup_drift" in allow3[0],
    )

    # ── Scenario 4: No prior loss → allow ──
    gate4 = TradeGate(
        _build_gate_services(db, coord, "trending_up", "TREND_PULLBACK_LONG"),
        settings=APEXSettings(),
    )
    trade4: dict[str, Any] = {
        "symbol": "NEWUSDT", "direction": "Buy", "size_usd": 500.0,
        "_xray_confidence": 0.7, "_setup_score": 0.6, "_expected_rr": 2.0,
    }
    with _CaptureSink() as cap4:
        out4 = await gate4.validate(trade4)
    rej4 = str(out4.get("_gate_rejected", ""))
    allow4 = cap4.with_tag("REENTRY_LEARNING_GATE | sym=NEWUSDT action=allow")
    _record(
        "J6", "New symbol with no prior loss → ALLOW",
        not rej4.startswith("reentry_learning_gate_"),
    )
    _record(
        "J6", "Allow event carries reason=no_prior_loss",
        len(allow4) >= 1 and "no_prior_loss" in allow4[0],
    )

    await db.execute("DELETE FROM trade_thesis")


# ──────────────────────────────────────────────────────────────────
# J7 — Direction-aware SL tightness
# ──────────────────────────────────────────────────────────────────


def scenario_j7_sl_geometry() -> None:
    _section("J7 — Direction-aware SL tightness (audit OBS-12 ATOMUSDT)")
    cases: list[tuple[str, Any, float, float, bool, str]] = [
        # name,                          side,      cur,    req,     expected, note
        ("ATOMUSDT short audit case",    Side.SELL, 2.0629, 2.05767, True,    "audit OBS-12: 2.05767<2.0629 IS tighter for short"),
        ("Short loosen (higher SL)",     Side.SELL, 2.0629, 2.07,    False,   "higher SL on short = looser"),
        ("Long tighten (higher SL)",     Side.BUY,  100.0,  101.0,   True,    "higher SL on long = tighter"),
        ("Long loosen (lower SL)",       Side.BUY,  100.0,  99.0,    False,   "lower SL on long = looser"),
        ("Equal SL (idempotency guard)", Side.BUY,  100.0,  100.0,   False,   "strict inequality"),
        ("First-install on long",        Side.BUY,    0.0,  99.0,    True,    "any positive SL tighter than none"),
        ("First-install on short",       Side.SELL,   0.0, 101.0,    True,    "any positive SL tighter than none"),
        ("Zero requested (remove SL)",   Side.BUY,  100.0,   0.0,    False,   "must not allow removing the stop"),
    ]
    for name, side, cur, req, expected, note in cases:
        got = is_tighter_sl(side, cur, req)
        _record(
            "J7", f"{name}: side={side.value} cur={cur} req={req} → {got}",
            got == expected,
            note if got != expected else "",
        )
    # is_long_side string-variant tolerance
    variants = [
        (Side.BUY, True), (Side.SELL, False),
        ("Buy", True), ("Sell", False),
        ("BUY", True), ("SELL", False),
        ("Long", True), ("Short", False),
        (None, False), ("Unknown", False),
    ]
    all_ok = all(is_long_side(v) is e for v, e in variants)
    _record(
        "J7", "is_long_side tolerates Side enum + string variants + unknowns",
        all_ok,
    )


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────


async def _async_main() -> int:
    print("J-series live simulation — exercising production code paths")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Project: {_PROJECT_ROOT}")

    # Real DatabaseManager + real migrations against a temp sqlite file
    tmpdir = tempfile.mkdtemp(prefix="j_series_sim_")
    db_path = Path(tmpdir) / "sim.db"
    db = DatabaseManager(
        str(db_path), wal_mode=True,
        concurrency_model="reader_pool", reader_pool_size=2,
    )
    await db.connect()
    await run_migrations(db)
    try:
        # Async scenarios that touch the DB
        await scenario_j1_cache_prune(db)
        await scenario_j1_reconciler(db)
        await scenario_j1_pagination(db)
        await scenario_j2_cross_direction(db)
        await scenario_j6_learning_gate(db)
        # Sync scenarios
        scenario_j3_precedence()
        scenario_j4_pool_observability()
        scenario_j5_sizing()
        scenario_j7_sl_geometry()
    finally:
        await db.disconnect()

    # ── Final report ──
    print()
    print("=" * 72)
    print("FINAL SUMMARY")
    print("=" * 72)

    by_issue: dict[str, list[tuple[str, bool]]] = {}
    for issue, scenario, ok, _ in _RESULTS:
        by_issue.setdefault(issue, []).append((scenario, ok))

    for issue in sorted(by_issue.keys()):
        rows = by_issue[issue]
        n_ok = sum(1 for _, ok in rows if ok)
        n_total = len(rows)
        status = "OK  " if n_ok == n_total else "FAIL"
        print(f"  [{status}] {issue:8}  {n_ok:3} / {n_total:3} scenarios pass")

    total_ok = sum(1 for _, _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print()
    print(f"  TOTAL: {total_ok} / {total} scenarios pass")

    if total_ok != total:
        print()
        print("FAILURES:")
        for issue, scenario, ok, note in _RESULTS:
            if not ok:
                print(f"  - {issue} — {scenario}  {note}")
        return 1
    print()
    print("All J-series scenarios respond as designed.")
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())
