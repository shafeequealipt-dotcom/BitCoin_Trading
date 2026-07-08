"""Stage-1/2 end-to-end pipeline verification.

This is the companion to ``overhaul29_pipeline_test.py`` but focused on the
seven Stage-1/2 root-cause fixes. Each pipeline wires REAL components
against a temp DB, captures Loguru output to memory, exercises the code
path, and asserts the new log tags + side-effects fired.

Pipelines covered:
  1. WorkerManager.initialize() end-to-end — boots with real components
     and emits REGIME_SEED (Phase 4)
  2. MarketRepository.save_klines deferred cleanup (Phase 2)
  3. CleanupWorker._sweep_klines_retention hourly backstop (Phase 2)
  4. TACache candles-path + symbol-path unified key (Phase 1)
  5. TACache LRU eviction + TA_CACHE_SIZE log (Phase 6)
  6. TACache invalidate() preserves OrderedDict (audit bug-fix #1)
  7. RegimeDetector.detect() fallback sets _last_regime (audit bug-fix #2)
  8. SignalGenerator.generate_signal populates vol_surge + age_h (Phase 3)
  9. SignalWorker.tick() emits SIG_BATCH_STATS (Phase 3)
  10. BaseWorker BASE_WORKER_TICK_SLOW threshold (Phase 5)
  11. UrgentQueue.format_for_prompt char-cap + correct dropped count (Phase 7 + audit bug-fix #3)
  12. KlineWorker KLINE_WRITE_LAG diagnostic (Phase 2)

Usage:
    .venv/bin/python tests/stage1_2_pipeline_test.py

Exit code 0 = all pipelines green.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_results: list[tuple[str, bool, str]] = []
_log_buf: list[str] = []


class PipelineFailed(AssertionError):
    pass


def _check(name: str, ok: bool, evidence: str = "") -> None:
    _results.append((name, ok, evidence))
    marker = PASS if ok else FAIL
    msg = f"  {marker}  {name}"
    if evidence:
        msg += f"\n         evidence: {evidence}"
    print(msg)
    if not ok:
        raise PipelineFailed(name)


def _logs_contain(tag: str, since_index: int = 0) -> bool:
    return any(tag in line for line in _log_buf[since_index:])


def _grep_log(tag: str, since_index: int = 0) -> str:
    matches = [ln for ln in _log_buf[since_index:] if tag in ln]
    return matches[0] if matches else ""


def _setup_log_capture() -> None:
    from loguru import logger

    logger.remove()
    logger.add(
        lambda msg: _log_buf.append(str(msg).rstrip()),
        level="DEBUG",
        format="{name}:{function}:{line} | {message}",
        filter=lambda r: True,
    )


# ───────────────────────────────────────────────────────────────────
# Pipeline 1 — WorkerManager.initialize() emits REGIME_SEED (Phase 4)
# ───────────────────────────────────────────────────────────────────

async def pipeline_01_worker_manager_boot() -> None:
    print("\n■ Pipeline 1 — WorkerManager.initialize() emits REGIME_SEED")
    from src.workers.manager import WorkerManager
    from src.config.settings import Settings
    from src.database.connection import DatabaseManager

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "stage12.db")
        s = Settings.load("config.toml")
        # Override db path to isolate the test
        s.database.path = db_path
        db = DatabaseManager(db_path)

        wm = WorkerManager(s, db)
        log_idx = len(_log_buf)
        await wm.initialize()

        _check(
            "WorkerManager booted",
            len(wm.workers) > 0,
            f"workers={len(wm.workers)}",
        )
        _check(
            "regime_detector service registered",
            wm._services.get("regime_detector") is not None,
        )
        _check(
            "REGIME_SEED log emitted (Phase 4)",
            _logs_contain("REGIME_SEED", log_idx),
            _grep_log("REGIME_SEED", log_idx)[:200],
        )
        detector = wm._services.get("regime_detector")
        _check(
            "regime_detector._last_regime populated after pre-seed",
            detector is not None and detector._last_regime is not None,
            f"last_regime={detector._last_regime.regime.value if detector and detector._last_regime else None}",
        )
        _check(
            "TACache registered under 4 aliases (ta, ta_engine, ta_cache, ta_raw)",
            all(wm._services.get(k) is not None for k in ("ta", "ta_engine", "ta_cache", "ta_raw")),
        )
        # All three TACache aliases refer to the SAME instance (so fixes propagate)
        from src.analysis.ta_cache import TACache
        ta_cache = wm._services.get("ta_cache")
        _check(
            "ta_cache is a TACache instance (not raw TAEngine)",
            isinstance(ta_cache, TACache),
        )
        _check(
            "ta / ta_engine / ta_cache are the SAME object",
            wm._services["ta"] is wm._services["ta_engine"] is wm._services["ta_cache"],
        )
        # maxsize bound set per Phase 6
        _check(
            "TACache has maxsize bound (Phase 6)",
            ta_cache.get_stats()["maxsize"] > 0,
            f"maxsize={ta_cache.get_stats()['maxsize']}",
        )
        # Invalidate preserves OrderedDict (audit bug #1)
        from collections import OrderedDict
        _check(
            "TACache._cache is OrderedDict (audit bug #1 fix)",
            isinstance(ta_cache._cache, OrderedDict),
        )

        await db.disconnect()


# ───────────────────────────────────────────────────────────────────
# Pipeline 2 — MarketRepository.save_klines retention delegated to
# cleanup_worker (post-Layer-1 Phase 4)
# ───────────────────────────────────────────────────────────────────

async def pipeline_02_deferred_cleanup() -> None:
    print("\n■ Pipeline 2 — save_klines does ONLY insert; retention via cleanup_worker")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.database.repositories.market_repo import MarketRepository
    from src.core.types import OHLCV, TimeFrame
    from datetime import datetime, timezone, timedelta

    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(os.path.join(tmp, "p2.db"))
        await db.connect()
        await run_migrations(db)
        repo = MarketRepository(db)

        # Post-Layer-1 Phase 4: in-line DELETE is REMOVED. The repo
        # exposes no per-(symbol, timeframe) call counter, no
        # ``_inserts_since_cleanup`` state, and no ``KLINES_CLEANUP_DEFERRED``
        # log emission. save_klines does ONLY the INSERT OR IGNORE; the
        # hourly cleanup_worker._sweep_klines_retention is the sole
        # retention path. This test confirms (a) the legacy state is
        # gone, (b) inserts work, (c) the table grows past the retention
        # cap until cleanup_worker runs (which is correct behavior).
        log_idx = len(_log_buf)
        base = datetime.now(timezone.utc)

        _check(
            "no _inserts_since_cleanup attribute (Phase 4 removed it)",
            not hasattr(repo, "_inserts_since_cleanup"),
        )

        # Insert 400 rows (deliberately past the 300-row retention).
        for i in range(400):
            await repo.save_klines([OHLCV(
                symbol="TESTUSDT", timeframe=TimeFrame.M5,
                timestamp=base + timedelta(minutes=i * 5),
                open=1, high=1, low=1, close=1, volume=1, turnover=1,
            )])

        _check(
            "no KLINES_CLEANUP_DEFERRED emitted (path removed)",
            not _logs_contain("KLINES_CLEANUP_DEFERRED", log_idx),
        )

        rows = await db.fetch_all(
            "SELECT COUNT(*) AS c FROM klines WHERE symbol = ? AND timeframe = ?",
            ("TESTUSDT", "5"),
        )
        _check(
            "all 400 inserts persisted (no in-line trimming)",
            rows[0]["c"] == 400,
            f"rows={rows[0]['c']}",
        )

        # Now run the cleanup_worker sweep manually and verify it prunes
        # back to the retention cap. This closes the loop: the new sole
        # retention path works.
        from src.workers.cleanup_worker import CleanupWorker
        from unittest.mock import MagicMock
        # CleanupWorker requires a settings object for super().__init__,
        # but _sweep_klines_retention only uses self.db.
        fake_settings = MagicMock()
        fake_settings.workers.max_consecutive_failures = 5
        fake_settings.workers.restart_delay = 10
        worker = CleanupWorker(fake_settings, db)
        sweep_stats = await worker._sweep_klines_retention()
        _check(
            "cleanup_worker.sweep deleted rows past retention",
            sweep_stats["deleted"] >= 100,
            f"deleted={sweep_stats['deleted']}",
        )
        rows = await db.fetch_all(
            "SELECT COUNT(*) AS c FROM klines WHERE symbol = ? AND timeframe = ?",
            ("TESTUSDT", "5"),
        )
        _check(
            "klines table trimmed to retention (<=300 rows) after sweep",
            rows[0]["c"] <= 300,
            f"rows_after_sweep={rows[0]['c']}",
        )

        await db.disconnect()


# ───────────────────────────────────────────────────────────────────
# Pipeline 3 — CleanupWorker._sweep_klines_retention (Phase 2 backstop)
# ───────────────────────────────────────────────────────────────────

async def pipeline_03_cleanup_sweep() -> None:
    print("\n■ Pipeline 3 — CleanupWorker hourly sweep backstop")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.workers.cleanup_worker import CleanupWorker
    from src.config.settings import Settings
    from datetime import datetime, timezone, timedelta
    import types

    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(os.path.join(tmp, "p3.db"))
        await db.connect()
        await run_migrations(db)

        # Bulk-insert 500 rows for TESTUSDT/5 bypassing save_klines
        base = datetime.now(timezone.utc)
        bulk = []
        for i in range(500):
            bulk.append((
                "TESTUSDT", "5",
                (base + timedelta(minutes=i * 5)).isoformat(),
                1, 1, 1, 1, 1, 1,
            ))
        await db.executemany(
            """
            INSERT INTO klines
              (symbol, timeframe, timestamp, open, high, low, close, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            bulk,
        )

        # Bind the sweep method to a minimal object with .db
        s = Settings.load("config.toml")
        cw = CleanupWorker(s, db)
        log_idx = len(_log_buf)
        result = await cw._sweep_klines_retention()

        _check(
            "KLINES_RETENTION_SWEEP log emitted",
            _logs_contain("KLINES_RETENTION_SWEEP", log_idx),
            _grep_log("KLINES_RETENTION_SWEEP", log_idx)[:200],
        )
        _check(
            "sweep result reports correct pairs + deleted",
            result["pairs"] == 1 and result["deleted"] == 200,
            f"pairs={result['pairs']} deleted={result['deleted']} el_ms={result['el_ms']:.0f}",
        )
        rows = await db.fetch_all(
            "SELECT COUNT(*) AS c FROM klines WHERE symbol = ?", ("TESTUSDT",)
        )
        _check(
            "table trimmed to 300 rows by sweep",
            rows[0]["c"] == 300,
            f"rows={rows[0]['c']}",
        )

        await db.disconnect()


# ───────────────────────────────────────────────────────────────────
# Pipeline 4 — TACache candles-path + symbol-path UNIFIED KEY (Phase 1)
# ───────────────────────────────────────────────────────────────────

async def pipeline_04_tacache_unified_key() -> None:
    print("\n■ Pipeline 4 — TACache unified key (candles-path == symbol-path)")
    from src.analysis.ta_cache import TACache
    from src.analysis.engine import TAEngine
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.core.types import OHLCV, TimeFrame
    from src.database.repositories.market_repo import MarketRepository
    from datetime import datetime, timezone, timedelta

    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(os.path.join(tmp, "p4.db"))
        await db.connect()
        await run_migrations(db)

        repo = MarketRepository(db)
        # Seed 60 M5 + 60 H1 real klines so both timeframes can be analyzed
        base_m5 = datetime.now(timezone.utc) - timedelta(minutes=60 * 5)
        m5_klines = [OHLCV(
            symbol="BTCUSDT", timeframe=TimeFrame.M5,
            timestamp=base_m5 + timedelta(minutes=i * 5),
            open=100 + i * 0.1, high=101 + i * 0.1,
            low=99 + i * 0.1, close=100.5 + i * 0.1,
            volume=1000, turnover=100000,
        ) for i in range(60)]
        await repo.save_klines(m5_klines)
        base_h1 = datetime.now(timezone.utc) - timedelta(hours=60)
        h1_klines = [OHLCV(
            symbol="BTCUSDT", timeframe=TimeFrame.H1,
            timestamp=base_h1 + timedelta(hours=i),
            open=100 + i * 0.5, high=101 + i * 0.5,
            low=99 + i * 0.5, close=100.5 + i * 0.5,
            volume=10000, turnover=1000000,
        ) for i in range(60)]
        await repo.save_klines(h1_klines)

        engine = TAEngine(db)
        cache = TACache(engine, ttl_seconds=120.0)

        # Strategy worker prefetch (candles path)
        m5_from_db = await repo.get_klines("BTCUSDT", "5", 200)
        await cache.analyze(candles=m5_from_db)
        s1 = cache.get_stats()
        _check(
            "after candles-path write: 1 lookup, 1 recomputed, 0 hits",
            s1["lookups"] == 1 and s1["recomputed"] == 1 and s1["valid_hits"] == 0,
        )
        # Strategist read (symbol path) — should hit the candles-path entry
        await cache.analyze(symbol="BTCUSDT", timeframe=TimeFrame.M5, limit=100)
        s2 = cache.get_stats()
        _check(
            "symbol-path read HITS the candles-path entry (unified key)",
            s2["valid_hits"] == 1 and s2["recomputed"] == 1,
            f"lookups={s2['lookups']} valid_hits={s2['valid_hits']} recomputed={s2['recomputed']}",
        )
        # Different limit — still hits same key
        await cache.analyze(symbol="BTCUSDT", timeframe=TimeFrame.M5, limit=200)
        s3 = cache.get_stats()
        _check(
            "different-limit read still HITS (limit no longer in key)",
            s3["valid_hits"] == 2,
        )
        # Different timeframe — legitimate miss
        await cache.analyze(symbol="BTCUSDT", timeframe=TimeFrame.H1, limit=200)
        s4 = cache.get_stats()
        _check(
            "different-timeframe read MISSES (different key)",
            s4["recomputed"] == 2,
        )

        await db.disconnect()


# ───────────────────────────────────────────────────────────────────
# Pipeline 5 — TACache LRU eviction + TA_CACHE_SIZE log (Phase 6)
# ───────────────────────────────────────────────────────────────────

async def pipeline_05_tacache_lru() -> None:
    print("\n■ Pipeline 5 — TACache LRU eviction bounds growth")
    from src.analysis.ta_cache import TACache
    from src.core.types import TimeFrame
    from collections import OrderedDict

    class FakeTF:
        def __init__(self, v): self.value = v

    class FakeKline:
        def __init__(self, sym): self.symbol = sym; self.timeframe = FakeTF("5")

    class FakeEngine:
        def __init__(self): self.calls = 0
        async def analyze(self, **kw):
            self.calls += 1
            return {"rsi": 50}

    cache = TACache(FakeEngine(), ttl_seconds=60.0, maxsize=5)
    # Fill 5 entries
    for i in range(5):
        await cache.analyze(candles=[FakeKline(f"SYM{i}")])
    _check(
        "cache filled to maxsize=5, 0 evictions",
        len(cache._cache) == 5 and cache._evictions == 0,
    )

    # 6th entry — LRU eviction
    await cache.analyze(candles=[FakeKline("SYM5")])
    _check(
        "6th write triggers LRU eviction",
        len(cache._cache) == 5 and cache._evictions == 1,
    )
    _check(
        "LRU (SYM0) was evicted, not MRU",
        "SYM0:5" not in cache._cache and "SYM5:5" in cache._cache,
    )

    # Hit SYM1 — promotes it to MRU
    await cache.analyze(candles=[FakeKline("SYM1")])
    # Write SYM7 — should evict the current LRU (SYM2 now, since SYM1 was promoted)
    await cache.analyze(candles=[FakeKline("SYM7")])
    _check(
        "promoted entry (SYM1) survives next eviction",
        "SYM1:5" in cache._cache,
    )
    _check(
        "pre-promotion-LRU (SYM2) is the next to go",
        "SYM2:5" not in cache._cache,
    )

    # Stats contain new Phase 6 fields
    stats = cache.get_stats()
    _check(
        "get_stats includes maxsize + evictions (Phase 6)",
        "maxsize" in stats and "evictions" in stats,
        f"maxsize={stats['maxsize']} evictions={stats['evictions']}",
    )


# ───────────────────────────────────────────────────────────────────
# Pipeline 6 — TACache invalidate preserves OrderedDict (audit bug #1)
# ───────────────────────────────────────────────────────────────────

async def pipeline_06_invalidate_type_preservation() -> None:
    print("\n■ Pipeline 6 — invalidate() preserves OrderedDict (audit bug-fix #1)")
    from src.analysis.ta_cache import TACache
    from collections import OrderedDict

    class FakeTF:
        def __init__(self, v): self.value = v

    class FakeKline:
        def __init__(self, sym): self.symbol = sym; self.timeframe = FakeTF("5")

    class FakeEngine:
        async def analyze(self, **kw): return {}

    cache = TACache(FakeEngine(), ttl_seconds=60.0)
    for s in ["A", "B", "C", "D"]:
        await cache.analyze(candles=[FakeKline(s)])

    _check(
        "cache starts as OrderedDict",
        isinstance(cache._cache, OrderedDict),
    )

    cache.invalidate("B")
    _check(
        "after invalidate(symbol), cache is STILL OrderedDict",
        isinstance(cache._cache, OrderedDict),
    )
    _check(
        "invalidate removed the targeted symbol only",
        "B:5" not in cache._cache and "A:5" in cache._cache,
    )

    # Subsequent hit must not crash (move_to_end requires OrderedDict)
    try:
        await cache.analyze(candles=[FakeKline("A")])
        _check("hit after invalidate does not crash (move_to_end works)", True)
    except AttributeError as e:
        _check(
            "hit after invalidate does not crash",
            False,
            f"CRASHED: {e}",
        )


# ───────────────────────────────────────────────────────────────────
# Pipeline 7 — RegimeDetector fallback sets _last_regime (audit bug #2)
# ───────────────────────────────────────────────────────────────────

async def pipeline_07_regime_fallback_sets_last() -> None:
    print("\n■ Pipeline 7 — RegimeDetector fallback sets _last_regime")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.regime import RegimeDetector
    from src.analysis.engine import TAEngine
    from src.database.repositories.market_repo import MarketRepository
    from src.config.settings import Settings

    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(os.path.join(tmp, "p7.db"))
        await db.connect()
        await run_migrations(db)

        s = Settings.load("config.toml")
        det = RegimeDetector(s, TAEngine(db), MarketRepository(db))
        _check(
            "fresh detector starts with _last_regime = None",
            det._last_regime is None,
        )

        # Empty DB → fallback path (< 50 klines)
        state = await det.detect("BTCUSDT")
        _check(
            "detect() returns the fallback RangingState",
            state.regime.value == "ranging",
        )
        _check(
            "fallback now SETS _last_regime (audit bug-fix #2)",
            det._last_regime is not None,
            f"regime={det._last_regime.regime.value}",
        )
        _check(
            "get_last_regime() returns the fallback without re-calling detect",
            det.get_last_regime() is det._last_regime,
        )

        await db.disconnect()


# ───────────────────────────────────────────────────────────────────
# Pipeline 8 — SignalGenerator populates vol_surge + age_h (Phase 3)
# ───────────────────────────────────────────────────────────────────

async def pipeline_08_signal_generator_new_fields() -> None:
    print("\n■ Pipeline 8 — SignalGenerator.generate_signal carries vol_surge + age_h")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.intelligence.signals.signal_generator import SignalGenerator
    from src.intelligence.sentiment.aggregator import SentimentAggregator
    from src.intelligence.sentiment.scorer import SentimentScorer
    from src.core.types import OHLCV, TimeFrame, FearGreedData, FundingRate
    from src.core.utils import now_utc
    from src.database.repositories.market_repo import MarketRepository
    from src.database.repositories.altdata_repo import AltDataRepository
    from datetime import timedelta

    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(os.path.join(tmp, "p8.db"))
        await db.connect()
        await run_migrations(db)

        alt = AltDataRepository(db)
        await alt.save_fear_greed(FearGreedData(
            value=55, classification="Greed", timestamp=now_utc(),
        ))
        await alt.save_funding_rate(FundingRate(
            symbol="BTCUSDT", funding_rate=0.0003,
            next_funding_time=now_utc(), fetched_at=now_utc(),
        ))
        await alt.save_open_interest("BTCUSDT", 10000.0)

        repo = MarketRepository(db)
        base = now_utc() - timedelta(minutes=21 * 5)
        klines = [OHLCV(
            symbol="BTCUSDT", timeframe=TimeFrame.M5,
            timestamp=base + timedelta(minutes=i * 5),
            open=100, high=101, low=99, close=100,
            volume=(3000.0 if i == 20 else 1000.0),  # last candle 3× surge
            turnover=100000,
        ) for i in range(21)]
        await repo.save_klines(klines)

        gen = SignalGenerator(SentimentAggregator(db, SentimentScorer()), db)
        log_idx = len(_log_buf)
        sig = await gen.generate_signal("BTCUSDT")

        _check(
            "SIG_GEN log contains vol_surge field (Phase 3)",
            "vol_surge=" in _grep_log("SIG_GEN", log_idx),
            _grep_log("SIG_GEN", log_idx)[:240],
        )
        _check(
            "SIG_GEN log contains age_h field (Phase 3)",
            "age_h=" in _grep_log("SIG_GEN", log_idx),
        )
        _check(
            "volume surge ratio ~3.0 reflected in log",
            "vol_surge=3.00" in _grep_log("SIG_GEN", log_idx),
        )
        # 0 < confidence < 1 (not flat 0.30)
        _check(
            "signal.confidence is a real computed value",
            0 < sig.confidence < 1,
            f"confidence={sig.confidence:.3f}",
        )

        await db.disconnect()


# ───────────────────────────────────────────────────────────────────
# Pipeline 9 — SignalWorker emits SIG_BATCH_STATS (Phase 3)
# ───────────────────────────────────────────────────────────────────

async def pipeline_09_signal_worker_batch_stats() -> None:
    print("\n■ Pipeline 9 — SignalWorker.tick() emits SIG_BATCH_STATS")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.workers.signal_worker import SignalWorker
    from src.intelligence.signals.signal_generator import SignalGenerator
    from src.intelligence.sentiment.aggregator import SentimentAggregator
    from src.intelligence.sentiment.scorer import SentimentScorer
    from src.config.settings import Settings
    from src.core.types import FearGreedData
    from src.core.utils import now_utc

    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(os.path.join(tmp, "p9.db"))
        await db.connect()
        await run_migrations(db)

        # Minimal seed — F&G only
        from src.database.repositories.altdata_repo import AltDataRepository
        await AltDataRepository(db).save_fear_greed(FearGreedData(
            value=50, classification="Neutral", timestamp=now_utc(),
        ))

        s = Settings.load("config.toml")
        agg = SentimentAggregator(db, SentimentScorer())
        gen = SignalGenerator(agg, db)
        worker = SignalWorker(s, db, aggregator=agg, signal_generator=gen)

        log_idx = len(_log_buf)
        await worker.tick()

        _check(
            "SIG_BATCH_STATS log emitted",
            _logs_contain("SIG_BATCH_STATS", log_idx),
            _grep_log("SIG_BATCH_STATS", log_idx)[:240],
        )
        line = _grep_log("SIG_BATCH_STATS", log_idx)
        for field in ("n=", "conf_min=", "conf_max=", "conf_mean=", "conf_std="):
            _check(
                f"SIG_BATCH_STATS contains {field}",
                field in line,
            )

        await db.disconnect()


# ───────────────────────────────────────────────────────────────────
# Pipeline 10 — BaseWorker BASE_WORKER_TICK_SLOW (Phase 5)
# ───────────────────────────────────────────────────────────────────

async def pipeline_10_base_worker_tick_slow() -> None:
    print("\n■ Pipeline 10 — BaseWorker BASE_WORKER_TICK_SLOW diagnostic")
    from src.workers.base_worker import BaseWorker
    from src.config.settings import Settings

    class SlowTestWorker(BaseWorker):
        async def tick(self):
            await asyncio.sleep(2.5)  # > 2s threshold
            # Stop after one tick
            self.running = False

    class FastTestWorker(BaseWorker):
        async def tick(self):
            self.running = False  # one tick, fast

    s = Settings.load("config.toml")

    class MockDB:
        pass

    log_idx = len(_log_buf)
    slow = SlowTestWorker("p10_slow", 0.1, s, MockDB())
    await slow.start()
    _check(
        "BASE_WORKER_TICK_SLOW fired for slow tick",
        _logs_contain("BASE_WORKER_TICK_SLOW", log_idx),
        _grep_log("BASE_WORKER_TICK_SLOW", log_idx)[:200],
    )
    _check(
        "log line names the worker (p10_slow)",
        "name=p10_slow" in _grep_log("BASE_WORKER_TICK_SLOW", log_idx),
    )

    log_idx2 = len(_log_buf)
    fast = FastTestWorker("p10_fast", 0.1, s, MockDB())
    await fast.start()
    _check(
        "BASE_WORKER_TICK_SLOW does NOT fire for fast tick",
        not _logs_contain("BASE_WORKER_TICK_SLOW | name=p10_fast", log_idx2),
    )


# ───────────────────────────────────────────────────────────────────
# Pipeline 11 — UrgentQueue char cap + correct dropped count (Phase 7)
# ───────────────────────────────────────────────────────────────────

async def pipeline_11_urgent_queue_cap() -> None:
    print("\n■ Pipeline 11 — UrgentQueue format_for_prompt char cap + correct dropped count")
    from src.core.urgent_queue import UrgentQueue, WatchdogConcern

    q = UrgentQueue()
    # 10 concerns — will exceed 1500 char cap
    cs = []
    for i in range(10):
        cs.append(WatchdogConcern(
            symbol=f"C{i}USDT", pnl_pct=-5, warnings=["a", "b", "c"],
            current_price=1000, entry_price=1050, side="Buy",
            sl_proximity_pct=70, position_age_minutes=30, stop_loss=950,
            urgency=("CRITICAL" if i < 2 else "HIGH"),
        ))
    log_idx = len(_log_buf)
    out = q.format_for_prompt(cs)

    _check(
        "output stays within MAX_FORMAT_CHARS cap",
        len(out) <= q.MAX_FORMAT_CHARS,
        f"len={len(out)} cap={q.MAX_FORMAT_CHARS}",
    )
    # CRITICALs preserved
    _check(
        "CRITICAL concerns retained at the top",
        "C0USDT" in out and "C1USDT" in out,
    )
    # Dropped count is honest
    import re
    present = sum(1 for i in range(10) if f"C{i}USDT" in out)
    expected = 10 - present
    m = re.search(r"(\d+) additional urgent", out)
    claimed = int(m.group(1)) if m else 0
    _check(
        "tail 'N elided' count matches actual (audit bug-fix #3)",
        claimed == expected,
        f"claimed={claimed} actual={expected}",
    )
    _check(
        "URGENT_QUEUE_FORMAT_TRIMMED log emitted",
        _logs_contain("URGENT_QUEUE_FORMAT_TRIMMED", log_idx),
        _grep_log("URGENT_QUEUE_FORMAT_TRIMMED", log_idx)[:200],
    )


# ───────────────────────────────────────────────────────────────────
# Pipeline 12 — KlineWorker KLINE_WRITE_LAG diagnostic (Phase 2)
# ───────────────────────────────────────────────────────────────────

async def pipeline_12_kline_write_lag() -> None:
    print("\n■ Pipeline 12 — KlineWorker KLINE_WRITE_LAG diagnostic emits on stale klines")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.workers.kline_worker import KlineWorker
    from src.config.settings import Settings
    from src.core.types import OHLCV, TimeFrame
    from src.database.repositories.market_repo import MarketRepository
    from datetime import datetime, timezone, timedelta

    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(os.path.join(tmp, "p12.db"))
        await db.connect()
        await run_migrations(db)
        s = Settings.load("config.toml")

        # Seed a STALE M5 kline (>180s old)
        repo = MarketRepository(db)
        await repo.save_klines([OHLCV(
            symbol="TESTUSDT", timeframe=TimeFrame.M5,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=300),
            open=1, high=1, low=1, close=1, volume=1, turnover=1,
        )])

        # Mock market_service — its method won't be called since we set
        # _last_fetch to make the tick skip actual Bybit calls
        class NoOpMarketService:
            async def get_klines(self, *a, **kw): return []

        kw = KlineWorker(s, db, NoOpMarketService(), scanner=None)
        # Force tracked_symbols to include the one we seeded
        kw._tracked_symbols = ["TESTUSDT"]
        # Block actual fetch loop by marking recent fetches
        import time as _t
        now = _t.time()
        for tf in (TimeFrame.M5, TimeFrame.H1, TimeFrame.H4, TimeFrame.D1):
            kw._last_fetch[f"TESTUSDT:{tf.value}"] = now  # skip fetch

        log_idx = len(_log_buf)
        await kw.tick()

        _check(
            "KLINE_WRITE_LAG log emitted for stale kline (300s old > 180s threshold)",
            _logs_contain("KLINE_WRITE_LAG", log_idx),
            _grep_log("KLINE_WRITE_LAG", log_idx)[:200],
        )
        _check(
            "KLINE_WRITE_LAG identifies TESTUSDT as stale",
            "TESTUSDT=" in _grep_log("KLINE_WRITE_LAG", log_idx),
        )

        await db.disconnect()


# ───────────────────────────────────────────────────────────────────
# Driver
# ───────────────────────────────────────────────────────────────────

PIPELINES = [
    pipeline_01_worker_manager_boot,
    pipeline_02_deferred_cleanup,
    pipeline_03_cleanup_sweep,
    pipeline_04_tacache_unified_key,
    pipeline_05_tacache_lru,
    pipeline_06_invalidate_type_preservation,
    pipeline_07_regime_fallback_sets_last,
    pipeline_08_signal_generator_new_fields,
    pipeline_09_signal_worker_batch_stats,
    pipeline_10_base_worker_tick_slow,
    pipeline_11_urgent_queue_cap,
    pipeline_12_kline_write_lag,
]


async def _main() -> int:
    _setup_log_capture()

    failed: list[str] = []
    for pipeline in PIPELINES:
        try:
            await pipeline()
        except PipelineFailed as e:
            failed.append(f"{pipeline.__name__}: {e}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            failed.append(f"{pipeline.__name__}: {type(e).__name__}: {e}")

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print()
    print("=" * 64)
    print(f"  STAGE-1/2 PIPELINE RESULTS: {passed}/{total} assertions PASS")
    print(f"  PIPELINES: {len(PIPELINES) - len(failed)}/{len(PIPELINES)} green")
    if failed:
        print(f"  FAILED PIPELINES:")
        for f in failed:
            print(f"    - {f}")
    print(f"  Total log records captured: {len(_log_buf)}")
    print("=" * 64)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
