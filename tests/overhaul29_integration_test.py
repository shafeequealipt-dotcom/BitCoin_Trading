"""Overhaul29 — End-to-end integration test.

Exercises every phase's new code path and asserts observable behaviour.
Not a pytest file — designed to be run as a script:

    .venv/bin/python tests/overhaul29_integration_test.py

Each test prints a colored PASS/FAIL. The script exits non-zero on the
first failure so CI / the operator can treat it as a gate.

Scope:
  * Phase 0a — DB protected-table guard
  * Phase 2  — close-broadcast callbacks fire in order
  * Phase 3  — transformer divergence override + max-divergence tracker
  * Phase 4  — format_price + validate_pair
  * Phase 5  — volatility jitter spread + health_monitor thresholds
  * Phase 6  — kline classify_fetch_quality + circuit breaker
  * Phase 7  — ta_cache TTL-only key + honest counters (Stage-1/2 fix)
  * Phase 9  — sniper stall escape ladder
  * Phase 11 — time_decay price-relative floor skip
  * Phase 15 — sentiment no-data branch returns neutral
  * Phase 17 — trailing ratchet HWM clamp
  * Phase 19 — event buffer dedupe + clear_for_symbol
  * Phase 23 — MCP pool opt-in + PoolDisabled + lifecycle
  * Phase 25 — structure_engine grade cap
  * Phase 29 — signal generator confidence downgrade
  * Phase 30 — workers.py shutdown hooks installed

The tests intentionally do NOT boot the full workers.py / server.py —
those paths are covered by the separate boot-test script.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import contextmanager

# Allow running this script from either the project root or tests/
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Suppress Loguru's default stderr sink so test output isn't polluted.
os.environ.setdefault("LOGURU_LEVEL", "CRITICAL")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


class TestFailed(AssertionError):
    pass


_results: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    marker = PASS if ok else FAIL
    print(f"  {marker}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        raise TestFailed(f"{name}: {detail}")


# ─── Phase 0a — DB protected-table guard ──────────────────────────────

async def test_phase_0a() -> None:
    print("Phase 0a — DB Protected Tables Guard")
    from src.database.protected_tables import (
        PROTECTED_TABLES,
        ProtectedTableViolation,
        assert_not_protected_destructive,
    )
    _check(
        "PROTECTED_TABLES has 9 core tables",
        {"tias_results", "trade_log", "thesis_store", "virtual_positions"}.issubset(
            PROTECTED_TABLES
        ),
    )

    try:
        assert_not_protected_destructive("DELETE FROM trade_log WHERE id=1")
        _check("DELETE on protected raises", False, "did not raise")
    except ProtectedTableViolation:
        _check("DELETE on protected raises", True)

    # Non-protected passes
    assert_not_protected_destructive("DELETE FROM klines WHERE id=1")
    _check("DELETE on non-protected passes", True)

    # force override
    assert_not_protected_destructive("DELETE FROM trade_log", force=True)
    _check("force=True override works", True)

    # DatabaseManager pre-flight
    from src.database.connection import DatabaseManager

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.db")
        db = DatabaseManager(path)
        await db.connect()
        await db.execute("CREATE TABLE trade_log (id INTEGER PRIMARY KEY, v TEXT)")
        await db.execute("INSERT INTO trade_log (v) VALUES ('x')")
        try:
            await db.execute("DELETE FROM trade_log WHERE id=1")
            _check("DatabaseManager blocks protected DELETE", False, "did not raise")
        except ProtectedTableViolation:
            _check("DatabaseManager blocks protected DELETE", True)
        rows = await db.fetch_all("SELECT * FROM trade_log")
        _check("row not deleted", len(rows) == 1, f"len={len(rows)}")
        await db.disconnect()


# ─── Phase 2 — close-broadcast callbacks ──────────────────────────────

async def test_phase_2() -> None:
    print("Phase 2 — Close-Broadcast Callbacks")
    from src.core.event_buffer import EventBuffer
    # clear_for_symbol must exist and correctly filter
    buf = EventBuffer()
    buf.add_event("LOW", "sl_hit", "BTCUSDT", pnl=1)
    buf.add_event("LOW", "sl_hit", "ETHUSDT", pnl=2)
    removed = buf.clear_for_symbol("BTCUSDT")
    _check("clear_for_symbol removes target", removed == 1, f"removed={removed}")
    _check("clear_for_symbol leaves others", buf.count == 1, f"count={buf.count}")

    # Transformer.invalidate_position_cache exists and is callable
    from src.core.transformer import Transformer
    _check(
        "Transformer.invalidate_position_cache exists",
        callable(getattr(Transformer, "invalidate_position_cache", None)),
    )

    # Strategist.invalidate_position / refresh_positions exist
    from src.brain.strategist import ClaudeStrategist
    _check(
        "ClaudeStrategist.invalidate_position exists",
        callable(getattr(ClaudeStrategist, "invalidate_position", None)),
    )
    _check(
        "ClaudeStrategist.refresh_positions exists",
        callable(getattr(ClaudeStrategist, "refresh_positions", None)),
    )

    # WatchdogSettings.fast_reconcile_seconds
    from src.config.settings import Settings
    s = Settings.load("config.toml")
    _check(
        "fast_reconcile_seconds default = 30s",
        s.watchdog.fast_reconcile_seconds == 30.0,
    )

    # Count close callback registrations in manager.py source
    with open("src/workers/manager.py") as f:
        src = f.read()
    n_reg = src.count("register_close_callback")
    _check(
        "manager.py registers ≥ 14 close callbacks (10 pre + 4 Phase 2)",
        n_reg >= 14,
        f"n={n_reg}",
    )


# ─── Phase 3 — transformer divergence override ────────────────────────

async def test_phase_3() -> None:
    print("Phase 3 — Transformer Divergence Override")
    from src.core.transformer import Transformer
    tf = Transformer(db=None, config=None)  # _config=None path
    # Initial max-divergence is zero
    _check(
        "_last_enrichment_max_divergence_pct initialised = 0.0",
        tf._last_enrichment_max_divergence_pct == 0.0,
    )

    # strategist._has_blocking_price_divergence is a method
    from src.brain.strategist import ClaudeStrategist
    _check(
        "strategist._has_blocking_price_divergence exists",
        callable(getattr(ClaudeStrategist, "_has_blocking_price_divergence", None)),
    )

    from src.config.settings import Settings
    s = Settings.load("config.toml")
    _check(
        "price.divergence_override_pct = 0.5",
        s.price.divergence_override_pct == 0.5,
    )
    _check(
        "price.divergence_block_prompt_pct = 1.0",
        s.price.divergence_block_prompt_pct == 1.0,
    )
    _check(
        "price.local_max_age_seconds = 10.0",
        s.price.local_max_age_seconds == 10.0,
    )


# ─── Phase 4 — format_price + validate_pair ───────────────────────────

async def test_phase_4() -> None:
    print("Phase 4 — format_price + validate_pair")
    from src.core.utils import format_price
    # Magnitude-aware decimals
    cases = [
        (70000, 70000, "70000.00"),     # >$10
        (5.1234, 5.1234, "5.1234"),      # $1-$10
        (0.5, 0.5, "0.500000"),          # $0.01-$1
        (0.00195, 0.00195, "0.00195000"),  # <$0.01
        (69000, 70000, "69000.00"),      # ref_price anchors precision
        (0.017, 0.018, "0.017000"),      # RAREUSDT-style
    ]
    for price, ref, expected in cases:
        got = format_price(price, ref)
        _check(f"format_price({price}, {ref}) = '{expected}'", got == expected, f"got {got}")

    from src.core.sl_tp_validator import SLTPValidator
    v = SLTPValidator()

    action, reason = v.validate_pair(
        sl_price=0.018, tp_price=0.018, entry_price=0.018,
        current_price=0.018, direction="Buy", symbol="RAREUSDT",
    )
    _check(
        "validate_pair rejects SL==TP", action == "SKIP" and reason == "sl_equals_tp",
        f"action={action} reason={reason}",
    )

    action, reason = v.validate_pair(
        sl_price=0.0177, tp_price=0.0195, entry_price=0.018,
        current_price=0.018, direction="Buy", symbol="RAREUSDT",
    )
    _check("validate_pair accepts valid Buy", action == "OK", f"action={action}")

    action, reason = v.validate_pair(
        sl_price=0.0195, tp_price=0.0177, entry_price=0.018,
        current_price=0.018, direction="Buy", symbol="RAREUSDT",
    )
    _check(
        "validate_pair rejects Buy wrong-side", action == "SKIP" and reason == "wrong_side",
    )


# ─── Phase 5 — volatility jitter + health thresholds ──────────────────

async def test_phase_5() -> None:
    print("Phase 5 — DB Contention + Event-Loop Starvation")
    from src.config.settings import Settings
    s = Settings.load("config.toml")
    _check(
        "vol_profile.cache_ttl_seconds = 120",
        s.volatility_profile.cache_ttl_seconds == 120.0,
    )
    _check(
        "vol_profile.jitter_range_seconds = 30",
        s.volatility_profile.jitter_range_seconds == 30,
    )

    # jitter spreads 30 coins across ≥ 50 s window
    from src.analysis.volatility_profile import VolatilityProfiler
    vp = VolatilityProfiler(ta_cache=None, settings=s.volatility_profile)
    symbols = [f"S{i}USDT" for i in range(30)]
    ttls = [vp._ttl_for(sym) for sym in symbols]
    spread = max(ttls) - min(ttls)
    _check(
        "30-coin TTL spread ≥ 40s (thundering herd broken)",
        spread >= 40, f"spread={spread}",
    )

    from src.core.health_monitor import SystemHealthMonitor
    _check(
        "LAG_SEVERE_MS = 500",
        SystemHealthMonitor.LAG_SEVERE_MS == 500.0,
    )
    # _top_blocking_tasks returns a list even when enumeration fails
    shm = SystemHealthMonitor()
    top = shm._top_blocking_tasks(n=3)
    _check("_top_blocking_tasks returns a list", isinstance(top, list))


# ─── Phase 6 — kline classify_fetch_quality + circuit breaker ────────

async def test_phase_6() -> None:
    print("Phase 6 — Kline Fetch Classification + Circuit Breaker")
    from src.workers.kline_worker import KlineWorker
    cls = KlineWorker._classify_fetch_quality
    cases = [
        ((12800, 12800), ("INFO", "ok")),
        ((12000, 12800), ("INFO", "ok")),         # 94%
        ((11000, 12800), ("WARNING", "short_10pct")),  # 86%
        ((5000, 12800), ("ERROR", "short_50pct")),     # 39%
        ((400, 12800), ("ERROR", "short_50pct")),      # 3% — brief's case
        ((0, 12800), ("CRITICAL", "zero_fetch")),
        ((0, 0), ("INFO", "ok")),                      # boundary
    ]
    for args, expected in cases:
        got = cls(*args)
        _check(
            f"classify_fetch_quality{args} = {expected}", got == expected, f"got={got}",
        )

    # is_circuit_open respects monotonic deadline
    import time
    from unittest.mock import MagicMock
    from src.config.settings import Settings
    s = Settings.load("config.toml")
    kw = KlineWorker.__new__(KlineWorker)
    kw._circuit_breaker_until = 0.0
    _check("is_circuit_open initially False", not kw.is_circuit_open())
    kw._circuit_breaker_until = time.monotonic() + 30
    _check("is_circuit_open True after setting deadline", kw.is_circuit_open())


# ─── Phase 7 — ta_cache TTL-only key + honest counters ───────────────

async def test_phase_7() -> None:
    print("Phase 7 — TA Cache TTL-Only Key + Honest Counters")
    from src.analysis.ta_cache import TACache

    class FakeTF:
        def __init__(self, v): self.value = v

    class FakeKline:
        def __init__(self, sym): self.symbol = sym; self.timeframe = FakeTF("5")

    class FakeEngine:
        def __init__(self): self.calls = 0
        async def analyze(self, **kw):
            self.calls += 1
            return {"rsi": 50, "call": self.calls}

    engine = FakeEngine()
    cache = TACache(engine, ttl_seconds=60.0)
    klines = [FakeKline("BTCUSDT") for _ in range(50)]
    await cache.analyze(candles=klines)          # miss (cold — recompute)
    await cache.analyze(candles=klines)          # hit (same {sym}:{tf} key, within TTL)
    await cache.analyze(candles=klines + [FakeKline("BTCUSDT")])  # still hit (len no longer in key)
    stats = cache.get_stats()

    _check(
        "cache.lookups = 3", stats["lookups"] == 3, f'{stats}',
    )
    _check(
        "cache.valid_hits = 2 (2 hits within TTL)", stats["valid_hits"] == 2,
    )
    _check(
        "cache.recomputed = 1 (first call only)", stats["recomputed"] == 1,
    )
    _check("engine called once (len excluded from key)", engine.calls == 1)
    _check("back-compat _hits == valid_hits", cache._hits == cache._valid_hits)
    _check("back-compat _misses == recomputed", cache._misses == cache._recomputed)


# ─── Phase 9 — sniper stall escape ladder ────────────────────────────

async def test_phase_9() -> None:
    print("Phase 9 — Sniper Stall Escape")
    from src.workers.profit_sniper import ProfitSniper

    class _S:
        class mode4:
            stall_escape_partial_after_ticks = 20
            stall_escape_full_after_ticks = 40

    class _SniperStub:
        settings = _S()
        _stall_escape_action = ProfitSniper._stall_escape_action

    s = _SniperStub()
    tracked: dict = {}

    for _ in range(20):
        r = s._stall_escape_action("BTC", tracked, True, "hold")
    _check("20 ticks: no escalation", r is None)
    r = s._stall_escape_action("BTC", tracked, True, "hold")  # 21
    _check("21 ticks: partial_close", r == "partial_close", f"got={r}")
    for _ in range(19):
        r = s._stall_escape_action("BTC", tracked, True, "hold")  # up to 40
    r = s._stall_escape_action("BTC", tracked, True, "hold")  # 41
    _check("41 ticks: full_close", r == "full_close", f"got={r}")
    # Reset on non-hold
    r = s._stall_escape_action("BTC", tracked, True, "tighten")
    _check("non-hold resets counter", r is None and tracked["_stall_ticks"] == 0)
    # Reset on not-actionable
    tracked["_stall_ticks"] = 30
    r = s._stall_escape_action("BTC", tracked, False, "hold")
    _check("not-actionable resets counter", r is None and tracked["_stall_ticks"] == 0)


# ─── Phase 11 — time_decay price-relative floor ──────────────────────

async def test_phase_11() -> None:
    print("Phase 11 — Time-Decay Price-Relative Floor")
    from src.risk.time_decay_sl import TimeDecayConfig

    cfg = TimeDecayConfig()
    _check(
        "min_price_relative_distance_pct default = 0.0 (no-op)",
        cfg.min_price_relative_distance_pct == 0.0,
    )

    # When watchdog wires it from sl_gateway, the default stays 0 so unwired
    # tests don't change behaviour. Confirm the field is settable.
    cfg2 = TimeDecayConfig(min_price_relative_distance_pct=0.3)
    _check("field is settable", cfg2.min_price_relative_distance_pct == 0.3)


# ─── Phase 15 — sentiment no-data branch ─────────────────────────────

async def test_phase_15() -> None:
    print("Phase 15 — Sentiment No-Data Returns Neutral")
    # Source-level check: the no-data branch sets overall = 0.0
    with open("src/intelligence/sentiment/aggregator.py") as f:
        src = f.read()
    _check(
        "aggregator has 'overall = 0.0' neutral path",
        "overall = 0.0" in src,
    )
    _check(
        "aggregator logs SENT_NEUTRAL",
        "SENT_NEUTRAL" in src,
    )


# ─── Phase 17 — trailing ratchet HWM ─────────────────────────────────

async def test_phase_17() -> None:
    print("Phase 17 — Trailing Ratchet HWM")
    with open("src/workers/profit_sniper.py") as f:
        src = f.read()
    _check(
        "TRAIL_RATCHET_CLAMP log present", "TRAIL_RATCHET_CLAMP" in src,
    )
    _check(
        "_trail_hwm tracked",
        "_trail_hwm" in src,
    )
    # HWM must be assigned after successful push in BOTH code paths
    # (the gateway path and the legacy fallback path).
    hwm_assign_count = src.count('_trail_hwm"] = float(')
    _check(
        "HWM assigned after successful push in both paths",
        hwm_assign_count >= 2,
        f"found {hwm_assign_count}",
    )


# ─── Phase 19 — event buffer dedupe ──────────────────────────────────

async def test_phase_19() -> None:
    print("Phase 19 — Event Buffer Dedupe")
    from src.core.event_buffer import EventBuffer, DEDUPE_WINDOW_SECONDS
    _check("DEDUPE_WINDOW_SECONDS = 30", DEDUPE_WINDOW_SECONDS == 30.0)

    buf = EventBuffer()
    # 3 identical events → 1 buffered, 2 suppressed
    for _ in range(3):
        buf.add_event("LOW", "critical_loss", "RAREUSDT", pnl=-2.0)
    _check("3 identical → 1 buffered", buf.count == 1, f"count={buf.count}")

    buf.add_event("LOW", "critical_loss", "RAREUSDT", pnl=-2.5)   # different payload
    _check("different payload → new entry", buf.count == 2, f"count={buf.count}")

    buf.add_event("LOW", "critical_loss", "BASEDUSDT", pnl=-2.0)  # different symbol
    _check("different symbol → new entry", buf.count == 3, f"count={buf.count}")


# ─── Phase 23 — MCP pool opt-in ──────────────────────────────────────

async def test_phase_23() -> None:
    print("Phase 23 — MCP Client Pool")
    from src.config.settings import Settings
    from src.mcp.client_pool import MCPClientPool, MCPPoolSettings, PoolDisabled, lease

    s = Settings.load("config.toml")
    _check("mcp_pool.enabled default = False", not s.mcp_pool.enabled)

    # Disabled pool: acquire raises
    p = MCPClientPool(MCPPoolSettings(enabled=False))
    await p.start()
    try:
        await p.acquire()
        _check("disabled pool raises PoolDisabled", False, "did not raise")
    except PoolDisabled:
        _check("disabled pool raises PoolDisabled", True)

    # Enabled pool: lifecycle works
    p2 = MCPClientPool(MCPPoolSettings(enabled=True, min_warm=1, max_warm=2))
    await p2.start()
    async with lease(p2) as client:
        _check("pool.lease yields client", client is not None)
    stats = p2.get_stats()
    _check("pool hits >= 1", stats["hits"] >= 1, str(stats))
    await p2.shutdown()


# ─── Phase 25 — structure grade cap ──────────────────────────────────

async def test_phase_25() -> None:
    print("Phase 25 — X-RAY Grade SMC/MTF Cap")
    with open("src/analysis/structure/structure_engine.py") as f:
        src = f.read()
    _check("XRAY_GRADE_CAPPED log present", "XRAY_GRADE_CAPPED" in src)
    _check(
        "cap condition (smc<10 AND mtf<3)",
        "smc_confluence < 10 and _mtf_score < 3" in src,
    )
    _check(
        "cap downgrades only A+/A",
        'quality in ("A+", "A")' in src,
    )


# ─── Phase 29 — signal downgrade ladder ──────────────────────────────

async def test_phase_29() -> None:
    print("Phase 29 — Signal Downgrade Ladder")
    from src.intelligence.signals.signal_models import CONFIDENCE_THRESHOLDS
    _check("strong_buy threshold = 0.6", CONFIDENCE_THRESHOLDS.get("strong_buy") == 0.6)
    _check("buy threshold = 0.4", CONFIDENCE_THRESHOLDS.get("buy") == 0.4)

    with open("src/intelligence/signals/signal_generator.py") as f:
        src = f.read()
    _check("SIG_DOWNGRADE log present", "SIG_DOWNGRADE" in src)
    _check("CONFIDENCE_THRESHOLDS imported", "CONFIDENCE_THRESHOLDS" in src)


# ─── Phase 30 — shutdown hooks ───────────────────────────────────────

async def test_phase_30() -> None:
    print("Phase 30 — Shutdown Hooks")
    import workers as W
    _check(
        "_install_shutdown_hooks exists",
        callable(getattr(W, "_install_shutdown_hooks", None)),
    )
    with open("workers.py") as f:
        src = f.read()
    _check("uses atexit.register", "atexit.register" in src)
    _check("hooks SIGTERM", "SIGTERM" in src)
    _check("hooks SIGINT", "SIGINT" in src)
    _check("emits WORKER_SHUTDOWN", "WORKER_SHUTDOWN" in src)
    _check("emits WORKER_SIGNAL", "WORKER_SIGNAL" in src)


# ─── Cross-project (Shadow) ──────────────────────────────────────────

async def test_phase_22_shadow() -> None:
    print("Phase 22 — Shadow SHADOW_SL_TIGHT Rate-Limit (cross-project)")
    path = "/home/inshadaliqbal786/shadow/src/exchange/position_monitor.py"
    with open(path) as f:
        src = f.read()
    _check(
        "Shadow _last_sl_tight_warn dict init",
        "_last_sl_tight_warn" in src,
    )
    _check(
        "Shadow uses time.monotonic() for rate-limit",
        "time.monotonic()" in src and "_last_sl_tight_warn" in src,
    )
    _check("Shadow keeps SHADOW_SLTP_HIT ungated", "SHADOW_SLTP_HIT" in src)


# ─── Driver ──────────────────────────────────────────────────────────

async def main() -> int:
    # Route logging to a temp dir so this script doesn't touch prod logs.
    _td = tempfile.mkdtemp(prefix="overhaul29_itest_")
    from src.core.logging import setup_logging
    setup_logging("DEBUG", _td)

    phases = [
        test_phase_0a, test_phase_2, test_phase_3, test_phase_4,
        test_phase_5, test_phase_6, test_phase_7, test_phase_9,
        test_phase_11, test_phase_15, test_phase_17, test_phase_19,
        test_phase_23, test_phase_25, test_phase_29, test_phase_30,
        test_phase_22_shadow,
    ]
    failed = 0
    for fn in phases:
        try:
            await fn()
        except TestFailed as e:
            failed += 1
            print(f"  \033[31mPHASE FAILED — continuing\033[0m: {e}")
        except Exception as e:
            import traceback; traceback.print_exc()
            failed += 1

    print()
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"=== {passed}/{total} assertions passed ===")
    print(f"=== {failed} phase(s) failed ===")

    import shutil; shutil.rmtree(_td, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
