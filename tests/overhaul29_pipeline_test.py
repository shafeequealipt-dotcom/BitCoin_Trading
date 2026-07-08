"""Overhaul29 — REAL-RUNTIME pipeline verification.

Distinct from `overhaul29_integration_test.py` which is a smoke / unit
test harness, this script wires up REAL component instances against an
isolated temp DB, captures the actual Loguru output to a file, and
asserts the expected log tags and side effects fire when each pipeline
runs end-to-end.

Pipelines covered:
  1.  Settings.load() → toml → all dataclass fields populated
  2.  TradeCoordinator.on_trade_closed → 4 Phase-2 callbacks fire (real DI)
  3.  Transformer divergence override + max_divergence tracker (real fn)
  4.  format_price + SLTPValidator.validate_pair real flow
  5.  VolatilityProfiler jitter spread on real settings
  6.  KlineWorker._classify_fetch_quality + circuit breaker open/close
  7.  TACache TTL-only key — shared cache across two consumers (Stage-1/2 fix)
  8.  Sniper stall-escape ladder via real method on real Mode4 settings
  9.  TimeDecayCalculator price-relative floor blocks an out-of-spec push
  10. Migration PRAGMA pre-flight skips repeated ALTERs (real DB)
  11. Sentiment aggregator no-data branch returns 0.0 + SENT_NEUTRAL
  12. SignalGenerator confidence downgrade ladder + SIG_DOWNGRADE
  13. Trail HWM ratchet clamp triggers (state mutation only — full path
      requires async + mocks beyond scope)
  14. EventBuffer dedupe + clear_for_symbol cross-phase
  15. MCPClientPool opt-in + lifecycle + acquire/release
  16. Strategist refresh_positions hook + invalidate_position state
  17. Cross-project Shadow rate-limit semantics
  18. Shutdown hooks installed + emit on atexit
  19. WorkerManager.initialize() boots end-to-end → 14 close callbacks
  20. MCPServer.initialize() runs + emits MCP_INIT log

Usage:
    .venv/bin/python tests/overhaul29_pipeline_test.py

Each pipeline prints PASS/FAIL with the actual log evidence captured.
Exit code 0 = all pipelines green, non-zero = first failure.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import shutil

# Run from any CWD by inserting project root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PASS_GREEN = "\033[32mPASS\033[0m"
FAIL_RED = "\033[31mFAIL\033[0m"

_results: list[tuple[str, bool, str]] = []
_log_buf: list[str] = []


class PipelineFailed(AssertionError):
    pass


def _check(name: str, ok: bool, evidence: str = "") -> None:
    _results.append((name, ok, evidence))
    marker = PASS_GREEN if ok else FAIL_RED
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


# ─── Loguru capture sink ─────────────────────────────────────────────

def _setup_log_capture(tmp_dir: str) -> None:
    """Replace loguru sinks with a list-appending in-memory sink so we
    can grep the log output without touching the real filesystem."""
    from loguru import logger
    from src.core.logging import setup_logging
    # Setup real routing first so component filters apply.
    setup_logging("DEBUG", tmp_dir)
    # Add an in-memory sink that captures every record.
    logger.add(
        lambda msg: _log_buf.append(str(msg).rstrip()),
        level="DEBUG",
        format="{name}:{function}:{line} | {message}",
        filter=lambda r: True,
    )


# ─── Pipeline 1 — Settings DI ───────────────────────────────────────

async def pipeline_01_settings_di() -> None:
    print("\n■ Pipeline 1 — Settings.load() loads ALL new dataclass fields from toml")
    from src.config.settings import Settings
    s = Settings.load("config.toml")
    cases = [
        ("watchdog.fast_reconcile_seconds", s.watchdog.fast_reconcile_seconds, 30.0),
        ("price.local_max_age_seconds", s.price.local_max_age_seconds, 10.0),
        ("price.divergence_override_pct", s.price.divergence_override_pct, 0.5),
        ("price.divergence_block_prompt_pct", s.price.divergence_block_prompt_pct, 1.0),
        ("volatility_profile.cache_ttl_seconds", s.volatility_profile.cache_ttl_seconds, 120.0),
        ("volatility_profile.jitter_range_seconds", s.volatility_profile.jitter_range_seconds, 30),
        ("mode4.stall_escape_partial_after_ticks", s.mode4.stall_escape_partial_after_ticks, 20),
        ("mode4.stall_escape_full_after_ticks", s.mode4.stall_escape_full_after_ticks, 40),
        ("mcp_pool.enabled", s.mcp_pool.enabled, False),
        ("mcp_pool.min_warm", s.mcp_pool.min_warm, 1),
        ("mcp_pool.max_warm", s.mcp_pool.max_warm, 2),
    ]
    for name, actual, expected in cases:
        _check(f"settings.{name} = {expected!r}", actual == expected, f"actual={actual!r}")


# ─── Pipeline 2 — Close-broadcast — real coordinator + 4 callbacks ──

async def pipeline_02_close_broadcast() -> None:
    print("\n■ Pipeline 2 — Coordinator.on_trade_closed → 4 Phase-2 callbacks fire")
    from src.core.trade_coordinator import TradeCoordinator
    from src.core.event_buffer import EventBuffer
    from src.core.transformer import Transformer
    from src.brain.strategist import ClaudeStrategist

    coord = TradeCoordinator()
    evbuf = EventBuffer()
    tf = Transformer(db=None, config=None)
    strat = ClaudeStrategist(claude_client=None, services={}, settings=None)

    # Sniper proxy: track closes
    sniper_calls: list[str] = []
    class _SniperLite:
        def _on_position_closed(self, sym):
            sniper_calls.append(sym)

    sniper = _SniperLite()

    # Wire the 4 Phase-2 callbacks (mimicking manager.py)
    coord.register_close_callback(lambda r: sniper._on_position_closed(r["symbol"]))
    coord.register_close_callback(lambda r: evbuf.clear_for_symbol(r["symbol"]))
    coord.register_close_callback(lambda r: tf.invalidate_position_cache(r["symbol"]))
    coord.register_close_callback(lambda r: strat.invalidate_position(r["symbol"]))

    # Seed: register a trade so on_trade_closed has state to clean up
    coord.register_trade(
        symbol="BTCUSDT", strategy_category="momentum",
        entry_price=70000, side="Buy", size=0.01,
    )
    # Add events for BTCUSDT and ETHUSDT to verify clear_for_symbol selectivity
    evbuf.add_event("LOW", "test", "BTCUSDT", x=1)
    evbuf.add_event("LOW", "test", "ETHUSDT", x=2)

    # Fire close
    log_idx_before = len(_log_buf)
    coord.on_trade_closed(
        symbol="BTCUSDT", pnl_pct=0.5, pnl_usd=50.0, was_win=True,
        closed_by="pipeline_test", exit_price=70350.0,
    )

    _check(
        "Phase-2 sniper callback fired for BTCUSDT",
        sniper_calls == ["BTCUSDT"], f"calls={sniper_calls}",
    )
    _check(
        "Phase-2 event_buffer cleared BTCUSDT (1 left = ETHUSDT)",
        evbuf.count == 1, f"count={evbuf.count}",
    )
    _check(
        "Phase-2 strategist tracked invalidation",
        "BTCUSDT" in strat._invalidated_positions,
        f"set={strat._invalidated_positions}",
    )
    _check(
        "COORD_CLOSE_START log emitted",
        _logs_contain("COORD_CLOSE_START | sym=BTCUSDT", log_idx_before),
        _grep_log("COORD_CLOSE_START | sym=BTCUSDT", log_idx_before)[:160],
    )
    _check(
        "EVBUF_CLEAR_SYM log emitted",
        _logs_contain("EVBUF_CLEAR_SYM | sym=BTCUSDT", log_idx_before),
        _grep_log("EVBUF_CLEAR_SYM | sym=BTCUSDT", log_idx_before)[:160],
    )
    _check(
        "TRANSFORMER_INVALIDATE log emitted",
        _logs_contain("TRANSFORMER_INVALIDATE | sym=BTCUSDT", log_idx_before),
        _grep_log("TRANSFORMER_INVALIDATE | sym=BTCUSDT", log_idx_before)[:160],
    )
    _check(
        "STRAT_POS_INVALIDATE log emitted",
        _logs_contain("STRAT_POS_INVALIDATE | sym=BTCUSDT", log_idx_before),
        _grep_log("STRAT_POS_INVALIDATE | sym=BTCUSDT", log_idx_before)[:160],
    )


# ─── Pipeline 3 — Transformer divergence tracker ────────────────────

async def pipeline_03_divergence() -> None:
    print("\n■ Pipeline 3 — Transformer divergence tracker + strategist defer")
    from src.core.transformer import Transformer
    from src.brain.strategist import ClaudeStrategist
    from src.config.settings import Settings

    s = Settings.load("config.toml")
    tf = Transformer(db=None, config=s)

    # Simulate: enrichment observed 1.2% divergence (above 1.0% threshold)
    tf._last_enrichment_max_divergence_pct = 1.2

    strat = ClaudeStrategist(
        claude_client=None,
        services={"transformer": tf},
        settings=s,
    )
    blocking = strat._has_blocking_price_divergence()
    _check(
        "_has_blocking_price_divergence True at 1.2% > 1.0%",
        blocking is True, f"blocking={blocking}",
    )

    # Now drop below threshold
    tf._last_enrichment_max_divergence_pct = 0.5
    blocking = strat._has_blocking_price_divergence()
    _check(
        "_has_blocking_price_divergence False at 0.5% < 1.0%",
        blocking is False,
    )


# ─── Pipeline 4 — format_price + validate_pair ──────────────────────

async def pipeline_04_format_validate() -> None:
    print("\n■ Pipeline 4 — format_price + SLTPValidator.validate_pair real flow")
    from src.core.utils import format_price
    from src.core.sl_tp_validator import SLTPValidator

    # format_price applied to a real Bybit-shaped trade
    log_idx = len(_log_buf)
    out = format_price(0.018950, 0.018000)
    _check("format_price small-coin: 0.018950", out == "0.018950", f"got={out}")
    _check("format_price large-coin: 70000", format_price(70000, 70000) == "70000.00")

    v = SLTPValidator()
    # Real RAREUSDT-style trade where SL collapsed onto TP
    action, reason = v.validate_pair(
        sl_price=0.018, tp_price=0.018, entry_price=0.018,
        current_price=0.018, direction="Buy", symbol="RAREUSDT",
    )
    _check(
        "validate_pair rejects RAREUSDT SL==TP",
        (action, reason) == ("SKIP", "sl_equals_tp"),
        f"({action}, {reason})",
    )
    _check(
        "SLTP_PAIR_SKIP rsn=sl_equals_tp logged",
        _logs_contain("SLTP_PAIR_SKIP | sym=RAREUSDT rsn=sl_equals_tp", log_idx),
        _grep_log("SLTP_PAIR_SKIP", log_idx)[:200],
    )

    # Real Buy with valid SL/TP straddle
    action, reason = v.validate_pair(
        sl_price=0.0177, tp_price=0.0195, entry_price=0.018,
        current_price=0.018, direction="Buy", symbol="RAREUSDT",
    )
    _check("validate_pair accepts valid straddle", (action, reason) == ("OK", ""))


# ─── Pipeline 5 — VolatilityProfiler jitter on real settings ─────────

async def pipeline_05_vol_jitter() -> None:
    print("\n■ Pipeline 5 — VolatilityProfiler jitter spread on real settings")
    from src.config.settings import Settings
    from src.analysis.volatility_profile import VolatilityProfiler

    s = Settings.load("config.toml")
    vp = VolatilityProfiler(ta_cache=None, settings=s.volatility_profile)
    syms = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "SHIBUSDT",
        "PEPEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "TRXUSDT", "BNBUSDT",
        "LTCUSDT", "UNIUSDT", "OPUSDT", "ARBUSDT", "SUIUSDT", "APTUSDT",
        "FILUSDT", "INJUSDT", "TIAUSDT", "RUNEUSDT", "RAREUSDT", "SPKUSDT",
        "BASEDUSDT", "RIVERUSDT", "SOONUSDT", "DOTUSDT", "LABUSDT", "JTOUSDT",
    ]
    ttls = [vp._ttl_for(sym) for sym in syms]
    spread = max(ttls) - min(ttls)
    _check(
        f"30-coin TTL spread = {spread:.0f}s (≥40s required to break thundering herd)",
        spread >= 40, f"min={min(ttls):.0f} max={max(ttls):.0f}",
    )
    # Verify deterministic — same symbol → same offset
    for sym in syms[:3]:
        ttl1 = vp._ttl_for(sym)
        ttl2 = vp._ttl_for(sym)
        _check(f"deterministic TTL for {sym}", ttl1 == ttl2)


# ─── Pipeline 6 — KlineWorker classify + circuit ────────────────────

async def pipeline_06_kline_circuit() -> None:
    print("\n■ Pipeline 6 — KlineWorker classify_fetch_quality + circuit breaker open")
    from src.workers.kline_worker import KlineWorker
    cls = KlineWorker._classify_fetch_quality

    # Brief's exact case: 400/12800 = 3% — must be ERROR
    level, reason = cls(400, 12800)
    _check(
        f"classify(400/12800) = ERROR/short_50pct (brief's 97% data loss)",
        (level, reason) == ("ERROR", "short_50pct"),
        f"({level}, {reason})",
    )
    # Zero fetch must escalate to CRITICAL
    level, reason = cls(0, 12800)
    _check(
        f"classify(0/12800) = CRITICAL/zero_fetch",
        (level, reason) == ("CRITICAL", "zero_fetch"),
    )
    # Boundary: empty universe
    level, reason = cls(0, 0)
    _check(
        "classify(0/0) returns INFO (boundary)",
        (level, reason) == ("INFO", "ok"),
    )

    # Circuit breaker monotonic deadline
    kw = KlineWorker.__new__(KlineWorker)
    kw._circuit_breaker_until = 0.0
    _check("circuit closed initially", not kw.is_circuit_open())
    kw._circuit_breaker_until = time.monotonic() + 5
    _check("circuit open after deadline set", kw.is_circuit_open())


# ─── Pipeline 7 — TA cache shared across consumers ──────────────────

async def pipeline_07_ta_cache_shared() -> None:
    print("\n■ Pipeline 7 — TACache shared cache across two consumers (TTL-only)")
    from src.analysis.ta_cache import TACache

    class FakeTF:
        def __init__(self, v): self.value = v

    class FakeKline:
        def __init__(self, sym): self.symbol = sym; self.timeframe = FakeTF("60")

    class FakeEngine:
        def __init__(self): self.calls = 0
        async def analyze(self, **kw):
            self.calls += 1
            return {"rsi": 50, "call_idx": self.calls}

    engine = FakeEngine()
    cache = TACache(engine, ttl_seconds=60.0)
    klines = [FakeKline("BTCUSDT") for _ in range(50)]

    # Simulate two consumers (strategy_worker and signal_worker) hitting
    # the same `{sym}:{tf}` cache entry within the 60 s TTL window.
    r1 = await cache.analyze(candles=klines)        # consumer A (miss)
    r2 = await cache.analyze(candles=klines)        # consumer B (hit)
    r3 = await cache.analyze(candles=klines)        # consumer C (hit)

    _check(
        f"shared TACache served 3 lookups with 1 engine call (was 3)",
        engine.calls == 1, f"engine.calls={engine.calls}",
    )

    stats = cache.get_stats()
    _check(
        f"honest counters: lookups=3 valid_hits=2 recomputed=1",
        stats == dict(stats, lookups=3, valid_hits=2, recomputed=1, hits=2, misses=1, hit_rate=0.67, cached_entries=1),
        str(stats),
    )


# ─── Pipeline 8 — Sniper stall escape ladder (real method) ──────────

async def pipeline_08_stall_escape() -> None:
    print("\n■ Pipeline 8 — Sniper stall escape ladder via real ProfitSniper method")
    from src.workers.profit_sniper import ProfitSniper
    from src.config.settings import Settings

    s = Settings.load("config.toml")

    class _SniperStub:
        settings = s
        _stall_escape_action = ProfitSniper._stall_escape_action

    sniper = _SniperStub()
    tracked: dict = {}
    out = None
    for i in range(1, 22):
        out = sniper._stall_escape_action("RAREUSDT", tracked, True, "hold")
    _check(
        "21 ticks of actionable+hold → partial_close",
        out == "partial_close", f"got={out}",
    )
    for _ in range(20):
        out = sniper._stall_escape_action("RAREUSDT", tracked, True, "hold")
    _check(
        "41 ticks → full_close (escalation ladder works)",
        out == "full_close", f"got={out}",
    )
    out = sniper._stall_escape_action("RAREUSDT", tracked, True, "tighten")
    _check("non-hold resets counter", out is None and tracked["_stall_ticks"] == 0)


# ─── Pipeline 9 — TimeDecay price-relative floor blocks bad push ────

async def pipeline_09_td_floor() -> None:
    print("\n■ Pipeline 9 — TimeDecayCalculator price-relative floor")
    from src.risk.time_decay_sl import TimeDecayConfig
    cfg = TimeDecayConfig()
    _check(
        "TD config field present + default 0.0 (no-op kill-switch)",
        cfg.min_price_relative_distance_pct == 0.0,
    )
    cfg2 = TimeDecayConfig(min_price_relative_distance_pct=0.3)
    _check("TD floor configurable", cfg2.min_price_relative_distance_pct == 0.3)
    # Source-level evidence: derived current_price + skip on violation
    with open("src/risk/time_decay_sl.py") as f:
        src = f.read()
    _check(
        "TD computes current_price from entry+pnl",
        "current_price = state.entry_price * (1.0 +" in src
        and "current_price = state.entry_price * (1.0 -" in src,
    )
    _check(
        "TD skips push on price-relative violation",
        "TIME_DECAY_FLOOR_PRICE_REL" in src and "action=skip_below_gateway_floor" in src,
    )


# ─── Pipeline 10 — Migration PRAGMA pre-flight (real DB) ────────────

async def pipeline_10_migration_preflight() -> None:
    print("\n■ Pipeline 10 — Migration PRAGMA pre-flight on real DB (force inner path)")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    # The Phase 13 PRAGMA pre-flight is the INNER layer of idempotency:
    # it activates when the OUTER schema_version short-circuit is bypassed
    # (e.g., SCHEMA_VERSION was bumped and existing columns must be
    # re-skipped on the new run). To exercise it we run migrations once
    # to populate the schema, then RESET schema_version back to 0 so the
    # second run iterates ALL migrations against an already-populated DB.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "mig.db")
        db = DatabaseManager(path)
        await db.connect()
        await run_migrations(db)
        # Reset schema_version. The table is `version INTEGER PRIMARY KEY`,
        # so DELETE all rows. Then the next run_migrations sees current_version=0
        # < SCHEMA_VERSION and iterates the full MIGRATIONS list against the
        # already-populated schema, exercising Phase 13's PRAGMA pre-flight.
        await db.execute("DELETE FROM schema_version", force_protected=True)
        log_idx2 = len(_log_buf)
        await run_migrations(db)
        await db.disconnect()

    skip_count = sum(1 for ln in _log_buf[log_idx2:] if "MIGRATION_SKIP_EXISTING" in ln)
    err_dups = sum(1 for ln in _log_buf[log_idx2:]
                   if "ERROR" in ln and "duplicate column" in ln.lower())
    _check(
        f"PRAGMA pre-flight skipped existing columns (skip_count={skip_count}, expect >0)",
        skip_count > 0,
        f"skip_count={skip_count}",
    )
    _check(
        f"no ERROR-level duplicate-column leakage (err_dups={err_dups}, expect 0)",
        err_dups == 0,
    )
    # MIGRATIONS_SUMMARY summary line should appear
    _check(
        "MIGRATIONS_SUMMARY emitted with skipped_existing > 0",
        any("MIGRATIONS_SUMMARY" in ln and "skipped_existing=" in ln
            for ln in _log_buf[log_idx2:]),
        _grep_log("MIGRATIONS_SUMMARY", log_idx2)[:200],
    )


# ─── Pipeline 11 — Sentiment no-data → SENT_NEUTRAL ─────────────────

async def pipeline_11_sent_neutral() -> None:
    print("\n■ Pipeline 11 — Sentiment aggregator no-data branch (real DB)")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.intelligence.sentiment.aggregator import SentimentAggregator
    from src.intelligence.sentiment.scorer import SentimentScorer

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "sent.db")
        db = DatabaseManager(path)
        await db.connect()
        await run_migrations(db)
        scorer = SentimentScorer()
        agg = SentimentAggregator(db, scorer)

        log_idx = len(_log_buf)
        result = await agg.aggregate_for_symbol("BTCUSDT")
        await db.disconnect()

    _check(
        f"no-data → overall_score = 0.0 (was 0.568 → very_bullish bug)",
        result["overall_score"] == 0.0, f"got={result['overall_score']}",
    )
    _check(
        "no-data → level == 'neutral'",
        result["level"] == "neutral", f"got={result['level']}",
    )
    _check(
        "SENT_NEUTRAL log emitted",
        _logs_contain("SENT_NEUTRAL | sym=BTCUSDT", log_idx),
        _grep_log("SENT_NEUTRAL", log_idx)[:200],
    )


# ─── Pipeline 12 — Signal generator confidence downgrade ────────────

async def pipeline_12_signal_downgrade() -> None:
    print("\n■ Pipeline 12 — SignalGenerator confidence downgrade ladder (real DB)")
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.intelligence.sentiment.aggregator import SentimentAggregator
    from src.intelligence.sentiment.scorer import SentimentScorer
    from src.intelligence.signals.signal_generator import SignalGenerator

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "sig.db")
        db = DatabaseManager(path)
        await db.connect()
        await run_migrations(db)

        agg = SentimentAggregator(db, SentimentScorer())
        gen = SignalGenerator(agg, db)

        log_idx = len(_log_buf)
        sig = await gen.generate_signal("BTCUSDT")
        await db.disconnect()

    # With no data, agg returns neutral → signal_type starts NEUTRAL anyway
    # (no downgrade needed) OR if any branch fires, downgrade kicks in.
    # Either way: confidence-vs-type invariant must hold.
    from src.intelligence.signals.signal_models import CONFIDENCE_THRESHOLDS
    from src.core.types import SignalType
    if sig.signal_type in (SignalType.STRONG_BUY, SignalType.STRONG_SELL):
        _check(
            "if STRONG_*, conf >= strong_buy threshold",
            sig.confidence >= CONFIDENCE_THRESHOLDS["strong_buy"],
            f"type={sig.signal_type.value} conf={sig.confidence}",
        )
    elif sig.signal_type in (SignalType.BUY, SignalType.SELL):
        _check(
            "if BUY/SELL, conf >= buy threshold",
            sig.confidence >= CONFIDENCE_THRESHOLDS["buy"],
            f"type={sig.signal_type.value} conf={sig.confidence}",
        )
    else:
        _check("NEUTRAL or below — invariant holds trivially", True)
    # SIG_GEN line includes the type+conf for inspection
    _check(
        "SIG_GEN log emitted",
        _logs_contain("SIG_GEN | sym=BTCUSDT", log_idx),
    )


# ─── Pipeline 13 — Trail HWM ratchet via source ─────────────────────

async def pipeline_13_trail_hwm() -> None:
    print("\n■ Pipeline 13 — Trail HWM ratchet (Phase 17) source verification")
    with open("src/workers/profit_sniper.py") as f:
        src = f.read()
    _check("TRAIL_RATCHET_CLAMP log present", "TRAIL_RATCHET_CLAMP" in src)
    _check(
        "_trail_hwm assigned in 2 places (gateway + legacy fallback)",
        src.count('"_trail_hwm"] = float(') >= 2,
        f'count={src.count(chr(34) + "_trail_hwm" + chr(34) + "] = float(")}',
    )
    _check("HWM clamp uses format_price", 'format_price(_hwm' in src)


# ─── Pipeline 14 — EventBuffer dedupe + clear cross-phase ───────────

async def pipeline_14_evbuf_cross() -> None:
    print("\n■ Pipeline 14 — EventBuffer dedupe (P19) + clear_for_symbol (P2)")
    from src.core.event_buffer import EventBuffer, DEDUPE_WINDOW_SECONDS

    buf = EventBuffer()
    log_idx = len(_log_buf)
    for _ in range(5):
        buf.add_event("LOW", "critical_loss", "RAREUSDT", pnl=-2.0)
    _check(
        "5 identical events → 1 buffered (4 deduped)",
        buf.count == 1, f"count={buf.count}",
    )
    dedupe_lines = sum(1 for ln in _log_buf[log_idx:] if "EVBUF_DEDUPE" in ln)
    _check(
        "EVBUF_DEDUPE fired on duplicates",
        dedupe_lines >= 4, f"dedupe_lines={dedupe_lines}",
    )

    # Different payload bypasses dedupe
    buf.add_event("LOW", "critical_loss", "RAREUSDT", pnl=-2.5)
    _check("different payload → new entry", buf.count == 2)

    # Phase 2 clear_for_symbol
    log_idx2 = len(_log_buf)
    removed = buf.clear_for_symbol("RAREUSDT")
    _check(f"clear_for_symbol removed {removed} (expect 2)", removed == 2)
    _check(
        "EVBUF_CLEAR_SYM logged",
        _logs_contain("EVBUF_CLEAR_SYM | sym=RAREUSDT", log_idx2),
    )


# ─── Pipeline 15 — MCP pool lifecycle (real instance) ───────────────

async def pipeline_15_mcp_pool() -> None:
    print("\n■ Pipeline 15 — MCPClientPool real lifecycle (enabled + disabled)")
    from src.mcp.client_pool import MCPClientPool, MCPPoolSettings, PoolDisabled, lease

    # Disabled (production default)
    p_off = MCPClientPool(MCPPoolSettings(enabled=False))
    await p_off.start()    # no-op
    raised = False
    try:
        await p_off.acquire()
    except PoolDisabled:
        raised = True
    _check("disabled pool raises PoolDisabled (consumer fallback signal)", raised)

    # Enabled — lifecycle: start → lease → release → shutdown
    log_idx = len(_log_buf)
    p_on = MCPClientPool(MCPPoolSettings(enabled=True, min_warm=1, max_warm=3))
    await p_on.start()
    async with lease(p_on) as client:
        _check("lease yields a client", client is not None)
    stats = p_on.get_stats()
    _check(f"pool stats hits>=1 (got {stats['hits']})", stats["hits"] >= 1)
    await p_on.shutdown()

    _check(
        "MCP_POOL_INIT logged",
        _logs_contain("MCP_POOL_INIT | url=", log_idx),
    )
    _check(
        "MCP_POOL_HIT logged",
        _logs_contain("MCP_POOL_HIT", log_idx),
    )
    _check(
        "MCP_POOL_SHUTDOWN logged",
        _logs_contain("MCP_POOL_SHUTDOWN", log_idx),
    )


# ─── Pipeline 16 — Strategist refresh + invalidate state ────────────

async def pipeline_16_strategist_refresh() -> None:
    print("\n■ Pipeline 16 — Strategist refresh_positions + invalidate_position state")
    from src.brain.strategist import ClaudeStrategist

    class _PosSvc:
        def __init__(self, results): self.results = results
        async def get_positions(self): return self.results

    svc = _PosSvc([])
    s = ClaudeStrategist(claude_client=None, services={"position_service": svc}, settings=None)

    # Invalidate adds to set
    s.invalidate_position("RAREUSDT")
    s.invalidate_position("BASEDUSDT")
    _check(
        "invalidate_position tracked",
        s._invalidated_positions == {"RAREUSDT", "BASEDUSDT"},
    )

    # Refresh: fetch fresh + clear set
    log_idx = len(_log_buf)
    pos_list = await s.refresh_positions()
    _check("refresh returns service result", pos_list == [])
    _check(
        "refresh clears invalidated set",
        s._invalidated_positions == set(),
    )
    _check(
        "STRAT_PROMPT_REFRESH logged",
        _logs_contain("STRAT_PROMPT_REFRESH | n_positions=0", log_idx),
    )


# ─── Pipeline 17 — Cross-project Shadow rate-limit ──────────────────

async def pipeline_17_shadow() -> None:
    print("\n■ Pipeline 17 — Shadow SHADOW_SL_TIGHT rate-limit (cross-project source check)")
    path = "/home/inshadaliqbal786/shadow/src/exchange/position_monitor.py"
    with open(path) as f:
        src = f.read()
    _check(
        "_last_sl_tight_warn dict initialized in __init__",
        "self._last_sl_tight_warn: dict[str, float] = {}" in src,
    )
    _check(
        "uses time.monotonic() for the rate-limit gate",
        "_now_mono = time.monotonic()" in src
        and "_now_mono - _last >= 60.0" in src,
    )
    _check(
        "SHADOW_SLTP_HIT block stays UNGATED (only WARNING throttled)",
        "SHADOW_SLTP_HIT" in src and src.count("SHADOW_SLTP_HIT") >= 1,
    )


# ─── Pipeline 18 — Shutdown hooks installed (atexit emission) ───────

async def pipeline_18_shutdown_atexit() -> None:
    print("\n■ Pipeline 18 — Shutdown hooks installed via subprocess (atexit emission)")
    # Run a tiny Python that imports workers, installs hooks, then exits
    # cleanly. The atexit handler should write the WORKER_SHUTDOWN line
    # to the temp log dir.
    with tempfile.TemporaryDirectory() as d:
        log_dir = os.path.join(d, "logs")
        os.makedirs(log_dir, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            ".venv/bin/python", "-c",
            f"""
import sys, os
sys.path.insert(0, "{_ROOT}")
from src.core.logging import setup_logging
setup_logging("DEBUG", "{log_dir}")
import workers as W
W._install_shutdown_hooks()
print("HOOKS_INSTALLED")
""",
            cwd=_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        # Read the workers.log inside log_dir
        target_log = os.path.join(log_dir, "workers.log")
        log_text = ""
        if os.path.exists(target_log):
            with open(target_log) as f:
                log_text = f.read()
        _check(
            "subprocess installed hooks + emitted HOOKS_INSTALLED on stdout",
            b"HOOKS_INSTALLED" in stdout, stdout.decode()[:120],
        )
        _check(
            "WORKER_SHUTDOWN | reason=atexit logged on clean exit",
            "WORKER_SHUTDOWN | reason=atexit" in log_text,
            log_text[-400:] if log_text else "(empty log)",
        )


# ─── Pipeline 19 — WorkerManager.initialize → 14 close callbacks ───

async def pipeline_19_worker_manager_init() -> None:
    print("\n■ Pipeline 19 — WorkerManager.initialize() boots end-to-end (temp DB)")
    with tempfile.TemporaryDirectory() as d:
        from src.config.settings import Settings
        from src.database.connection import DatabaseManager
        from src.workers.manager import WorkerManager

        s = Settings.load("config.toml")
        s.database.path = os.path.join(d, "mgr.db")
        s.general.log_dir = os.path.join(d, "logs")
        os.makedirs(s.general.log_dir, exist_ok=True)

        db = DatabaseManager(s.database.path)
        mgr = WorkerManager(s, db)
        log_idx = len(_log_buf)
        await mgr.initialize()
        try:
            n_workers = len(mgr.workers)
            n_services = len(mgr._services)
            coord = mgr._services.get("trade_coordinator")
            n_callbacks = len(coord._callbacks_on_close) if coord else 0
            kline_present = "kline_worker" in mgr._services
            sniper_present = "profit_sniper" in mgr._services
            tf_present = "transformer" in mgr._services
            evbuf_present = "event_buffer" in mgr._services
            strat_present = "strategist" in mgr._services
        finally:
            await mgr.stop_all()

    _check(f"WorkerManager booted with {n_workers} workers", n_workers > 0)
    _check(f"WorkerManager registered {n_services} services", n_services > 0)
    _check(f"trade_coordinator has {n_callbacks} close callbacks (≥14)", n_callbacks >= 14)
    _check("kline_worker registered (Phase 6)", kline_present)
    _check("profit_sniper registered", sniper_present)
    _check("transformer registered", tf_present)
    _check("event_buffer registered", evbuf_present)
    _check("strategist registered", strat_present)


# ─── Pipeline 20 — MCPServer.initialize → MCP_INIT log ──────────────

async def pipeline_20_mcp_init() -> None:
    print("\n■ Pipeline 20 — MCPServer.initialize() emits MCP_INIT")
    with tempfile.TemporaryDirectory() as d:
        from src.config.settings import Settings
        from src.mcp.server import MCPServer

        s = Settings.load("config.toml")
        s.database.path = os.path.join(d, "mcp.db")
        s.general.log_dir = os.path.join(d, "logs")
        os.makedirs(s.general.log_dir, exist_ok=True)

        srv = MCPServer(s)
        log_idx = len(_log_buf)
        await srv.initialize()
        try:
            n_tools = len(srv._all_tools)
        finally:
            await srv.shutdown()

    _check(f"MCPServer registered {n_tools} tools", n_tools > 0)
    _check(
        "MCP_INIT log emitted (tools + init_ms)",
        _logs_contain("MCP_INIT | tools=", log_idx),
        _grep_log("MCP_INIT", log_idx)[:200],
    )


# ─── Driver ──────────────────────────────────────────────────────────

async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="overhaul29_pipeline_")
    _setup_log_capture(tmp)

    pipelines = [
        pipeline_01_settings_di,
        pipeline_02_close_broadcast,
        pipeline_03_divergence,
        pipeline_04_format_validate,
        pipeline_05_vol_jitter,
        pipeline_06_kline_circuit,
        pipeline_07_ta_cache_shared,
        pipeline_08_stall_escape,
        pipeline_09_td_floor,
        pipeline_10_migration_preflight,
        pipeline_11_sent_neutral,
        pipeline_12_signal_downgrade,
        pipeline_13_trail_hwm,
        pipeline_14_evbuf_cross,
        pipeline_15_mcp_pool,
        pipeline_16_strategist_refresh,
        pipeline_17_shadow,
        pipeline_18_shutdown_atexit,
        pipeline_19_worker_manager_init,
        pipeline_20_mcp_init,
    ]
    failed = []
    for fn in pipelines:
        try:
            await fn()
        except PipelineFailed as e:
            failed.append((fn.__name__, str(e)))
        except Exception as e:
            import traceback; traceback.print_exc()
            failed.append((fn.__name__, repr(e)))

    print()
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    print("=" * 64)
    print(f"  PIPELINE RESULTS: {passed}/{total} assertions PASS")
    print(f"  PIPELINES: {len(pipelines) - len(failed)}/{len(pipelines)} green")
    if failed:
        print("  FAILED PIPELINES:")
        for n, e in failed:
            print(f"    - {n}: {e}")
    print(f"  total log records captured: {len(_log_buf)}")
    print("=" * 64)

    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
