"""Configuration loader: reads config.toml + .env and maps to typed dataclasses.

Environment variables override config.toml values. Provides a singleton via
Settings.load() for convenience, while keeping constructors injectable for testing.
"""

import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# tomli is in stdlib as tomllib from Python 3.11+
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from src.core.exceptions import ConfigError


def _env(key: str, default: str = "") -> str:
    """Read an environment variable with fallback."""
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    """Read an environment variable as boolean."""
    val = os.environ.get(key, "")
    if not val:
        return default
    return val.lower() in ("true", "1", "yes")


@dataclass
class GeneralSettings:
    """Top-level general configuration."""
    mode: str = "paper"
    shadow_api_url: str = "http://127.0.0.1:9090"
    timezone: str = "UTC"
    log_level: str = "INFO"
    log_dir: str = "data/logs"


@dataclass
class BybitSettings:
    """Bybit exchange connection settings."""
    testnet: bool = True
    default_symbols: list[str] = field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
    )
    rate_limit_per_second: int = 10
    ws_ping_interval: int = 20
    ws_reconnect_delay: int = 5
    recv_window: int = 5000
    api_key: str = ""
    api_secret: str = ""

    @property
    def base_url(self) -> str:
        """REST API base URL based on testnet flag."""
        if self.testnet:
            return "https://api-testnet.bybit.com"
        return "https://api.bybit.com"

    @property
    def ws_url(self) -> str:
        """WebSocket URL based on testnet flag."""
        if self.testnet:
            return "wss://stream-testnet.bybit.com"
        return "wss://stream.bybit.com"


@dataclass
class BybitDemoSettings:
    """Bybit demo (paper-money) execution adapter settings.

    The demo adapter runs against ``api-demo.bybit.com`` with separate
    credentials from live Bybit. Activated when ``general.mode`` is
    ``"bybit_demo"`` (additive — does NOT replace the existing ``"bybit"``
    live-mainnet slot). Switching is operator-driven via Telegram with
    a process restart between exchanges (see Phase 4 ExchangeSwitcher).

    Credentials are resolved from env vars at boot time so they never
    land in config.toml or git history.
    """
    enabled: bool = False
    base_url: str = "https://api-demo.bybit.com"
    recv_window: int = 5000
    timeout_seconds: float = 10.0
    retry_attempts: int = 5
    retry_base_delay_seconds: float = 0.2
    api_key: str = ""
    api_secret: str = ""
    # ── PnL-truth provenance (2026-06-07) ──
    # How the WS self-close path resolves the authoritative close PnL.
    #   "ws_exec" — thread the close order_id/qty so the exchange
    #               /v5/position/closed-pnl row is identity-matched (the TRUTH
    #               path; this is the fix — the field never existed before, so
    #               the subscriber's getattr default forced "legacy" and the
    #               truth path never activated).
    #   "gated"   — match on price+qty (no order_id).
    #   "legacy"  — single-shot rows[0] (rollback only; the stale-row source).
    close_pnl_source: str = "ws_exec"
    # When the exchange closed-pnl row is not yet indexed at book time, book the
    # WS net tagged PROVISIONAL (never as a final win) and reconcile it when the
    # exchange row arrives — rather than painting the gross fallback as truth.
    close_pnl_provisional: bool = True
    close_pnl_reconcile: bool = True
    close_pnl_reconcile_max_attempts: int = 10
    close_pnl_reconcile_interval_s: float = 1.0
    close_pnl_reconcile_total_budget_s: float = 30.0
    # Phase 1 residual fix (2026-06-08) — reconcile exit-plausibility gate.
    # The reconcile channel bypasses on_trade_closed's staleness gate and the
    # resolver's qty gate is inert post-pop, so a stale same-symbol closed-pnl
    # row (qty-only match) could book a phantom PnL. A reconcile is rejected
    # (provisional kept) when the resolved exchange exit price diverges from the
    # provisional close's exit by more than this percent. 0 disables the gate.
    # Scope (F5, 2026-06-09): this single key now drives THREE gates — the
    # reconciler exit-plausibility gate AND the TradeCoordinator MARK-referenced
    # staleness gates in resolve_authoritative_pnl and on_trade_closed (the poll /
    # sniper / watchdog-strategic self-close paths). WorkerManager wires it into
    # the coordinator at boot via set_close_exit_divergence_pct, so tuning this one
    # value affects all three sites consistently. Tune with care: generous enough
    # for ordinary slippage and a fee-driven sign flip, tight enough to catch a
    # wrong-trade / stale closed-pnl row.
    close_pnl_reconcile_max_exit_divergence_pct: float = 3.0


@dataclass
class FinnhubSettings:
    """Finnhub news API settings."""
    enabled: bool = True
    rate_limit_per_minute: int = 60
    news_categories: list[str] = field(default_factory=lambda: ["crypto", "general"])
    max_articles_per_fetch: int = 50
    api_key: str = ""


@dataclass
class RedditSettings:
    """Reddit/PRAW sentiment settings."""
    enabled: bool = True
    subreddits: list[str] = field(
        default_factory=lambda: ["cryptocurrency", "bitcoin", "ethtrader"]
    )
    max_posts_per_sub: int = 25
    min_score: int = 10
    rate_limit_per_minute: int = 60
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    password: str = ""


@dataclass
class AltDataSettings:
    """Alternative data source settings."""
    enabled: bool = True
    fear_greed_interval: int = 3600
    funding_rate_interval: int = 300
    open_interest_interval: int = 600
    coingecko_rate_limit_per_minute: int = 10


@dataclass
class TASettings:
    """Technical Analysis engine settings.

    XRAY phase-4 fix: ``confidence_ema_alpha`` controls the smoothing
    applied to ``overall.confidence`` in
    ``TAEngine._compute_overall_signal``. The pre-fix formula was
    ``confidence = max(bullish, bearish) / total_indicators`` which
    swung 0.14 on a single indicator flip (RSI crossing 50, MACD
    histogram zero-cross). That noise propagated into the
    TradeScorer Context block (threshold-cross at 0.6 → 10 ↔ 0 swing)
    and produced cycle-to-cycle Context flapping on identical
    structural inputs.

    The new formula EMA-smooths confidence against a per-symbol
    history:
        confidence = alpha * raw + (1 - alpha) * prev_confidence

    alpha = 0.4 keeps the response within ~3 cycles for genuine state
    changes while halving cycle-to-cycle variance on indicator noise.
    Set ``confidence_ema_alpha = 1.0`` to disable smoothing (legacy
    behaviour).
    """
    confidence_ema_alpha: float = 0.4
    # Item 4 (entry-gaps investigation, 2026-05-26): when True, volume_sma_ratio
    # is computed on the last CLOSED candle, excluding the still-forming newest
    # bucket. The kline fetch (market_service.get_klines) has no end-bound and
    # includes the forming bucket, whose partial volume biases the ratio low.
    # Default False preserves legacy behaviour (ratio uses the forming bucket).
    volume_ratio_use_closed_candle: bool = False


@dataclass
class DatabaseSettings:
    """SQLite / future PostgreSQL settings."""
    path: str = "data/trading.db"
    wal_mode: bool = True
    pool_size: int = 5
    query_timeout: int = 30
    vacuum_interval: int = 24
    # Phase 1 (D-3 fix): chunk size for ``MarketRepository.save_klines``.
    # The historical implementation issued a single ``executemany`` for the
    # full per-(symbol, timeframe) batch under DatabaseManager's global lock,
    # creating 12-20 s lock-hold spikes for ~9k-row payloads. Chunking the
    # saves and yielding the event loop between chunks lets other workers
    # acquire the lock without queueing behind one heavy save. 500 rows is
    # ~2-3 ms per executemany on the observed hardware.
    kline_save_chunk_size: int = 500
    # Phase 1 (D-3 fix): WAL checkpoint scheduler cadence in kline_worker
    # ticks. ``wal_autocheckpoint=2000`` is opportunistic and only fires
    # when no readers hold a snapshot. Under continuous load that
    # condition is rare, leaving the WAL file pinned at the
    # ``journal_size_limit`` cap (100 MiB observed). ``cleanup_worker``
    # runs ``PRAGMA wal_checkpoint(PASSIVE)`` hourly which is too coarse.
    # Triggering a PASSIVE checkpoint every N kline_worker ticks lets the
    # WAL truncate during quiet windows; PASSIVE never blocks new
    # readers/writers so this is safe to schedule frequently.
    wal_checkpoint_every_n_kline_ticks: int = 50
    # Phase 1 (D-3 fix): if PASSIVE checkpoints report ``busy != 0`` for
    # this many consecutive scheduled checkpoints, escalate the next one
    # to ``TRUNCATE``. TRUNCATE briefly blocks writers but is the only
    # mode that fully reclaims WAL space when readers consistently pin
    # snapshots. 3 is conservative — reaches escalation only after
    # ~10-12 minutes of sustained busy results.
    wal_checkpoint_truncate_after_busy_count: int = 3
    # Phase 1 (D-3 fix): wait threshold (ms) above which DatabaseManager
    # emits ``DB_LOCK_WAIT``. 1000 ms preserves prior behaviour — sub-1s
    # acquires happen constantly and don't deserve log noise. Tightening
    # to e.g. 500 ms during verification gives a finer-grained view of
    # contention without changing production code.
    db_lock_wait_threshold_ms: int = 1000
    # Phase conn-pool/p3-9 (db-concurrency-refactor 2026-05-14): the only
    # supported value is ``"reader_pool"`` — the historical
    # ``"single_lock"`` engine was removed after 2 hours of stable
    # production on the pool (99%+ lock-wait reduction, 100% cascade
    # elimination). ``DatabaseManager`` raises ``DatabaseError`` if any
    # other value is passed. Field retained for backward-compat with
    # existing config.toml files that already set it.
    concurrency_model: str = "reader_pool"
    # Phase conn-pool/p3-1: number of aiosqlite reader connections opened
    # at boot under the "reader_pool" engine. Default 4 is a placeholder;
    # the final value is chosen from Phase 3.5 stress-test results
    # (smallest N that holds all 5 scenarios with > 50% headroom on pool
    # occupancy and zero ``CONN_POOL_EXHAUSTED`` events). Hard cap on
    # dynamic growth is 2*reader_pool_size. Each reader carries its own
    # 64 MiB page cache + 256 MiB mmap, so 4 readers = ~1.3 GiB virtual
    # memory budget on this 32 GiB host (well within margin).
    reader_pool_size: int = 4

    def __post_init__(self) -> None:
        if not isinstance(self.kline_save_chunk_size, int) or self.kline_save_chunk_size < 1:
            raise ConfigError(
                "[database.kline_save_chunk_size] must be a positive integer, "
                f"got {self.kline_save_chunk_size!r}",
                details={"value": self.kline_save_chunk_size},
            )
        if (
            not isinstance(self.wal_checkpoint_every_n_kline_ticks, int)
            or self.wal_checkpoint_every_n_kline_ticks < 1
        ):
            raise ConfigError(
                "[database.wal_checkpoint_every_n_kline_ticks] must be a "
                f"positive integer, got {self.wal_checkpoint_every_n_kline_ticks!r}",
                details={"value": self.wal_checkpoint_every_n_kline_ticks},
            )
        if (
            not isinstance(self.wal_checkpoint_truncate_after_busy_count, int)
            or self.wal_checkpoint_truncate_after_busy_count < 1
        ):
            raise ConfigError(
                "[database.wal_checkpoint_truncate_after_busy_count] must be a "
                f"positive integer, got {self.wal_checkpoint_truncate_after_busy_count!r}",
                details={"value": self.wal_checkpoint_truncate_after_busy_count},
            )
        if (
            not isinstance(self.db_lock_wait_threshold_ms, int)
            or self.db_lock_wait_threshold_ms < 1
        ):
            raise ConfigError(
                "[database.db_lock_wait_threshold_ms] must be a positive "
                f"integer, got {self.db_lock_wait_threshold_ms!r}",
                details={"value": self.db_lock_wait_threshold_ms},
            )
        # Phase conn-pool/p3-9: validate the concurrency-engine selector.
        # ``single_lock`` was removed; ``reader_pool`` is the only supported
        # engine. Fail fast on misconfig so the runtime never tries to
        # dispatch through an unknown engine name.
        _valid_engines = ("reader_pool",)
        if self.concurrency_model == "single_lock":
            raise ConfigError(
                "[database.concurrency_model] = 'single_lock' is no longer "
                "supported (removed Phase conn-pool/p3-9 2026-05-14). "
                "Set [database].concurrency_model = 'reader_pool' in "
                "config.toml (or unset DATABASE_CONCURRENCY_MODEL env var).",
                details={"value": self.concurrency_model},
            )
        if self.concurrency_model not in _valid_engines:
            raise ConfigError(
                "[database.concurrency_model] must be one of "
                f"{_valid_engines}, got {self.concurrency_model!r}",
                details={"value": self.concurrency_model},
            )
        if (
            not isinstance(self.reader_pool_size, int)
            or self.reader_pool_size < 1
        ):
            raise ConfigError(
                "[database.reader_pool_size] must be a positive integer, "
                f"got {self.reader_pool_size!r}",
                details={"value": self.reader_pool_size},
            )


def _validate_sweet_spot(field_path: str, value: str, *, max_minute: int) -> tuple[int, int]:
    """Validate and parse a "MM:SS" sweet-spot offset string.

    Used by ``SweetSpotsSettings.__post_init__`` and
    ``AltDataSweetSpotsSettings.__post_init__``. Raises ``ConfigError`` with
    a readable path on bad input so workers fail-fast on misconfig instead
    of producing silent drift later.

    Args:
        field_path: Dotted path of the offending field (e.g.
            "workers.sweet_spots.kline_worker"). Surfaced in the error.
        value: Raw string from config.
        max_minute: Maximum allowed minute (inclusive). For a 5-min window
            this is 4.

    Returns:
        ``(minutes, seconds)`` tuple.
    """
    if not isinstance(value, str):
        raise ConfigError(
            f"[{field_path}] must be a string in MM:SS format, "
            f"got {type(value).__name__}: {value!r}",
            details={"field": field_path, "value": value},
        )
    parts = value.split(":")
    if len(parts) != 2:
        raise ConfigError(
            f"[{field_path}] must be in MM:SS format, got {value!r}",
            details={"field": field_path, "value": value},
        )
    try:
        m = int(parts[0])
        s = int(parts[1])
    except ValueError:
        raise ConfigError(
            f"[{field_path}] MM:SS components must be integers, got {value!r}",
            details={"field": field_path, "value": value},
        )
    if m < 0 or m > max_minute:
        raise ConfigError(
            f"[{field_path}] minute must be 0-{max_minute} (within window), got {m}",
            details={"field": field_path, "value": value},
        )
    if s < 0 or s > 59:
        raise ConfigError(
            f"[{field_path}] second must be 0-59, got {s}",
            details={"field": field_path, "value": value},
        )
    return (m, s)


@dataclass
class AltDataSweetSpotsSettings:
    """Per-source sweet-spot offsets for AltDataWorker.

    AltDataWorker has three sub-cadences with different natural rhythms:
    funding rates align to the 5-min window (MM:SS within window),
    open interest fires every N minutes (independent of window), and
    Fear & Greed fires every M minutes (typically hourly).

    Reference: LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md §8.4 and
    IMPLEMENT_LAYER1_CORRECTED_MIGRATION_PROFESSIONAL.md §"PHASE 1".
    """
    funding_rates: str = "1:45"
    open_interest_minutes: int = 5
    fear_greed_minutes: int = 60
    # Five-Fix Follow-Up — Fix 2 (fresh OI, 2026-06-10) — the Bybit
    # get_open_interest snapshot granularity. The fetch CADENCE was already
    # 5-minute (open_interest_minutes above), but the fetcher asked the
    # exchange for "1h" snapshots, so every 5-minute fetch stored the same
    # hourly plateau value and every delta computed from it pinned identical
    # for up to an hour (the frozen oi_change_pct the brain saw). "5min"
    # makes each fetch store a genuinely fresh snapshot. Allowed values are
    # the exchange's own intervals.
    open_interest_interval: str = "5min"

    def __post_init__(self) -> None:
        _validate_sweet_spot(
            "workers.sweet_spots.altdata.funding_rates",
            self.funding_rates,
            max_minute=4,  # within 5-min window
        )
        if not isinstance(self.open_interest_minutes, int) or self.open_interest_minutes < 1:
            raise ConfigError(
                "[workers.sweet_spots.altdata.open_interest_minutes] must be a "
                f"positive integer, got {self.open_interest_minutes!r}",
                details={"value": self.open_interest_minutes},
            )
        _oi_allowed = ("5min", "15min", "30min", "1h", "4h", "1d")
        if self.open_interest_interval not in _oi_allowed:
            raise ConfigError(
                "[workers.sweet_spots.altdata.open_interest_interval] must be "
                f"one of {_oi_allowed}, got {self.open_interest_interval!r}",
                details={"value": self.open_interest_interval},
            )
        if not isinstance(self.fear_greed_minutes, int) or self.fear_greed_minutes < 1:
            raise ConfigError(
                "[workers.sweet_spots.altdata.fear_greed_minutes] must be a "
                f"positive integer, got {self.fear_greed_minutes!r}",
                details={"value": self.fear_greed_minutes},
            )


@dataclass
class SweetSpotsSettings:
    """Per-worker sweet-spot offsets within a 5-minute window.

    The corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md §8)
    decouples worker scheduling from the cycle. The 7 data workers fire at
    these offsets every ``window_minutes`` so each downstream worker reads
    fresh upstream data. Chain ordering is enforced:

        kline_worker → structure_worker → signal_worker
        → regime_worker → strategy_worker → scanner_worker

    Validation runs in ``__post_init__`` and raises ``ConfigError`` if any
    offset is malformed or the chain order is violated. The
    ``AltDataSweetSpotsSettings`` (independent of the chain) is validated
    separately.
    """
    kline_worker: str = "0:30"
    structure_worker: str = "0:45"
    signal_worker: str = "1:00"
    regime_worker: str = "1:15"
    strategy_worker: str = "1:30"
    scanner_worker: str = "4:00"
    window_minutes: int = 5
    altdata: AltDataSweetSpotsSettings = field(default_factory=AltDataSweetSpotsSettings)

    def __post_init__(self) -> None:
        if not isinstance(self.window_minutes, int) or self.window_minutes < 1:
            raise ConfigError(
                "[workers.sweet_spots.window_minutes] must be a positive integer, "
                f"got {self.window_minutes!r}",
                details={"value": self.window_minutes},
            )
        max_minute = self.window_minutes - 1
        chain = [
            ("kline_worker", self.kline_worker),
            ("structure_worker", self.structure_worker),
            ("signal_worker", self.signal_worker),
            ("regime_worker", self.regime_worker),
            ("strategy_worker", self.strategy_worker),
            ("scanner_worker", self.scanner_worker),
        ]
        # Step 1: parse + range-check every offset.
        parsed: list[tuple[str, str, int]] = []  # (name, raw, total_seconds)
        for name, val in chain:
            m, s = _validate_sweet_spot(
                f"workers.sweet_spots.{name}", val, max_minute=max_minute,
            )
            parsed.append((name, val, m * 60 + s))
        # Step 2: enforce strict chain ordering (downstream must come AFTER upstream).
        prev_seconds = -1
        prev_name = None
        prev_raw = None
        for name, raw, total in parsed:
            if total <= prev_seconds:
                raise ConfigError(
                    f"[workers.sweet_spots] chain order violated: "
                    f"{name}={raw} ({total}s) must come strictly AFTER "
                    f"{prev_name}={prev_raw} ({prev_seconds}s)",
                    details={
                        "chain_order": [(n, r) for n, r, _ in parsed],
                        "violator": name,
                        "previous": prev_name,
                    },
                )
            prev_seconds = total
            prev_name = name
            prev_raw = raw

        # E20 (2026-05-28): extend the chain validator to the altdata
        # funding_rates offset. funding_rates is independent of the 6-worker
        # data chain, but it MUST still fire strictly BEFORE the scanner
        # consumes the window, so funding data is fresh when the scanner ranks.
        # We deliberately do NOT require funding_rates < strategy_worker:
        # altdata firing at 1:45 — just after strategy at 1:30 — is the
        # known-benign #10 staleness, and enforcing that edge would break the
        # shipped config at boot. Only the altdata < scanner edge is enforced.
        _scanner = next(
            ((raw, total) for name, raw, total in parsed
             if name == "scanner_worker"),
            None,
        )
        _fr_raw = getattr(self.altdata, "funding_rates", None)
        if _scanner is not None and _fr_raw:
            _scanner_raw, _scanner_seconds = _scanner
            _fr_m, _fr_s = _validate_sweet_spot(
                "workers.sweet_spots.altdata.funding_rates",
                _fr_raw, max_minute=max_minute,
            )
            _fr_seconds = _fr_m * 60 + _fr_s
            if _fr_seconds >= _scanner_seconds:
                raise ConfigError(
                    f"[workers.sweet_spots] altdata.funding_rates={_fr_raw} "
                    f"({_fr_seconds}s) must fire strictly BEFORE "
                    f"scanner_worker={_scanner_raw} ({_scanner_seconds}s) so "
                    f"funding data is fresh when the scanner ranks",
                    details={
                        "altdata_funding_rates": _fr_raw,
                        "altdata_seconds": _fr_seconds,
                        "scanner_worker": _scanner_raw,
                        "scanner_seconds": _scanner_seconds,
                    },
                )


@dataclass
class WorkerSettings:
    """Background worker configuration.

    The ``sweet_spots`` sub-section drives the corrected Layer 1
    architecture's per-worker MM:SS scheduling (see ``SweetSpotsSettings``).
    Legacy interval fields (``market_data_interval`` etc.) remain the source
    of truth for workers that still use ``BaseWorker``'s fixed-interval
    pattern (NewsWorker, RedditWorker, CleanupWorker, PriceWorker, etc.).
    """
    enabled: bool = True
    market_data_interval: int = 60
    news_interval: int = 300
    reddit_interval: int = 600
    altdata_interval: int = 300
    health_check_interval: int = 60
    max_consecutive_failures: int = 5
    restart_delay: int = 10
    sweet_spots: SweetSpotsSettings = field(default_factory=SweetSpotsSettings)


@dataclass
class BrainColdStartProtection:
    """Definitive-fix Phase 6 (2026-04-28) — cold-start completeness gate.

    The brain auto-execute path used to fire on whatever ``_coin_packages``
    contained at the moment ``create_trade_plan`` returned. During the
    first 10 minutes after a restart, upstream caches haven't fully
    populated yet (StructureCache, RegimeWorker per-coin, AltDataWorker
    F&G fetch), so packages routinely arrived at completeness 0.67 and
    the brain placed losing trades on incomplete data.

    This block configures the gate. It applies BEFORE Claude is called —
    if the gate trips, the cycle is short-circuited and a Telegram alert
    fires so the operator sees the silent skip.

    Attributes:
        enabled: Master toggle. Default True. Disable only for debugging.
        min_avg_completeness: Once past the boot grace window, the
            average completeness across all packages must be at least
            this value to allow execution. Default 0.70 (relaxed from
            0.85 by Issue E12 so honest failure-default scoring does not
            block the batch).
        min_per_package_completeness: Per-package floor used when
            counting "qualified" packages. Default 0.75.
        min_qualified_packages: Once past the boot grace window, at
            least this many packages must be at or above the per-package
            floor. Default 1 (relaxed from 3 in the Phase 7 rollout).
        boot_grace_period_sec: Window after process start during which
            the stricter ``boot_grace_completeness`` applies. Default
            600 s (10 min) — matches the typical warmup of the slowest
            upstream cache (AltDataWorker F&G hourly fetch).
        boot_grace_completeness: Stricter average-completeness gate
            during the boot grace window. Default 0.80 (relaxed from
            0.95 by Issue E12).
    """
    enabled: bool = True
    # Issue E12 (2026-05-27): defaults relaxed (avg 0.85 -> 0.70, boot-grace
    # 0.95 -> 0.80). E12 makes the validator count failure-defaults, so
    # honestly-scored degraded packages now lower the cycle average; this
    # batch-wide gate must not block all new trades on those honest scores.
    # min_per_package_completeness + min_qualified_packages still enforce
    # "at least one truly-warm package", preserving cache-warmup safety.
    # config.toml [brain.cold_start_protection] carries the live values.
    min_avg_completeness: float = 0.70
    min_per_package_completeness: float = 0.75
    # Phase 7 of the 1D briefing rewrite — relaxed from 3 to 1.
    # Rationale: this gate's purpose is cold-start CACHE-WARMUP safety,
    # not minimum-cohort enforcement. One well-formed package proves
    # caches are warm. The per-package floor + this count still detect
    # cache-degradation. The exclusion-mode legacy path is unaffected
    # (it produced 1-3 qualified historically and the legacy quorum
    # dropped trades anyway — Phase 0 baseline). Briefing-mode (Phase 5)
    # emits >=12 packages so the relaxation is the consummate companion
    # to the new pipeline.
    min_qualified_packages: int = 1
    boot_grace_period_sec: int = 600
    boot_grace_completeness: float = 0.80


@dataclass
class BrainSettings:
    """Claude Brain autonomous trading configuration."""
    enabled: bool = False
    use_claude_code: bool = True
    strategic_interval: int = 180
    watchdog_interval: int = 30
    analysis_interval: int = 1800
    signal_triggered: bool = True
    min_signal_confidence: float = 0.7
    max_calls_per_hour: int = 10
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.3
    api_key: str = ""
    # Brain-provider switch (2026-07-06, operator request) — "claude_code"
    # (default, $0 via Max subscription) or "glm_cloudflare" (GLM-5.2 via
    # Cloudflare Workers AI, see src/brain/glm_client.py). Selects which
    # client workers/manager.py instantiates as services["claude_client"].
    provider: str = "claude_code"
    glm_model: str = "@cf/zai-org/glm-5.2"
    glm_account_id: str = ""
    glm_api_key: str = ""
    # GLM-5.2 is a reasoning model (hidden chain-of-thought before the final
    # answer) — needs a generous timeout and max_tokens budget that covers
    # BOTH the reasoning trace and the final JSON, or content comes back
    # truncated/empty. See glm_client.py docstring.
    glm_timeout_seconds: float = 180.0
    glm_max_tokens: int = 8000
    glm_temperature: float = 0.3
    glm_max_retries: int = 2
    # Item 2 (entry-gaps investigation, 2026-05-26): when True, the strategist
    # appends an expected-winner-magnitude advisory (MAG=HIGH/MED/LOW) to each
    # coin's volatility line in the prompt. Entry M5 volatility predicts winner
    # SIZE, not win/loss — big-winners separated by entry ATR% (AUC 0.633).
    # Advisory only; never gates or resizes a trade. Default False.
    entry_magnitude_advisory_enabled: bool = False

    # Claude CLI subprocess timing (moved out of manager.py hardcodes)
    claude_cli_timeout_seconds: int = 300
    claude_cli_max_retries: int = 2
    claude_cli_min_interval: float = 2.0
    # Brain-CLI model pin (2026-05-30). Passed to `claude -p --model <id>` at
    # every spawn site so the latency-critical trade calls use a fast-enough,
    # JSON-reliable tier instead of the CLI default (now the slow
    # claude-opus-4-8[1m], ~240s on the full prompt, breaching the deadline).
    # Empty string disables the flag (CLI default). See config.toml [brain].
    claude_cli_model: str = "claude-opus-4-7"
    # P2-1 (2026-05-13): first-byte deadline, separate from total timeout.
    # The 300 s total timeout is too coarse for the operator-observable
    # stuck pattern — when the Claude API holds a subprocess for minutes
    # without producing a single stdout byte (state=S wchan=ep_poll), the
    # parent's executor thread can drift far past the total deadline
    # before the timeout check actually fires (observed: 300 s timeout
    # firing at 112 min wall time on 2026-05-13 10:30 call_id=30). The
    # first-byte deadline aborts the subprocess as soon as N seconds
    # pass without ANY stdout byte, and the retry loop spawns a fresh
    # process. Default 90 s caps the worst-case stall ladder at
    # 90 s × (max_retries + 1) ≈ 270 s. Setting this to 0 disables the
    # check (falls back to total-timeout-only behaviour).
    claude_cli_first_byte_timeout_seconds: int = 90
    # P2-1 (2026-05-13): T2-1's prewarm pool retains one primed
    # subprocess per system_prompt hash. The legacy default of 60 s
    # meant CALL_A's prewarmed worker was always stale by the next
    # cycle (5-10 min cadence) and was disposed without use — pool
    # hit rate measured at 0 / 1424 calls on 2026-05-13. A 900 s
    # (15 min) freshness window covers the worst-case CALL_A cadence
    # while still respecting the 1 hour ``credential_refresh_margin``
    # so we never hand back a worker close to credential expiry.
    claude_cli_prewarm_max_age_seconds: int = 900
    # P2-1 (2026-05-13): periodic CLAUDE_POOL_STATS emit cadence. Pool
    # tracks hits/misses/stale-disposals cumulatively and emits a stats
    # line every N seconds (default 300 s). Helps operators verify pool
    # effectiveness without grepping raw acquire events.
    claude_cli_prewarm_stats_interval_seconds: int = 300
    # F28 (2026-06-05): warm-pool canary TTL. Reuse of a prewarmed worker is
    # gated on an out-of-band canary that keepalive-parks a throwaway worker and
    # confirms it still responds (the CLI 2.1.158/2.1.160 hang produced zero
    # stdout). The canary is re-confirmed every this-many seconds (and on the
    # first calls after boot), so a future CLI auto-update that reintroduces the
    # hang self-disables reuse within one TTL instead of starving the brain.
    claude_cli_prewarm_canary_ttl_seconds: int = 600
    # Issue 1 (latency, 2026-06-06): `claude -p` flags that cut the dominant
    # CALL_A first-token cost — the model THINKING before it answers (proven:
    # first_token_ms tracks prompt size, not spawn; the warm pool is already on
    # and irrelevant). Passed to EVERY spawn site (canary, prewarm, cold spawn).
    # Defaults here are OFF so an absent config reverts to current behaviour; the
    # chosen activation lives in config.toml [brain]. claude_cli_effort="" drops
    # --effort (CLI default); the bools gate --bare and
    # --exclude-dynamic-system-prompt-sections.
    claude_cli_effort: str = ""
    claude_cli_bare: bool = False
    claude_cli_exclude_dynamic_system_prompt: bool = False
    # Base multiplier for the timeout-retry backoff ladder:
    # sleep = (attempt+1) * base, so 10s → 20s → 30s (was 30/60/90).
    # Cuts the brain-outage window after a single timeout without
    # losing exponential character.
    claude_cli_retry_timeout_backoff_base_seconds: int = 10

    # Phase 3 (Brain credentials): pre-flight refresh margin. The CLI
    # subprocess is observed to hang silently when credentials expire
    # mid-call. Starting refresh ``credential_refresh_margin_seconds``
    # before expiry — and aborting the call entirely if the refresh
    # fails inside that margin — eliminates the hang surface. Default
    # 600 s (10 min) gives generous headroom; the hardcoded 300 s used
    # previously was too tight for slow OAuth responses.
    credential_refresh_margin_seconds: int = 600

    # Layer 1 restructure Phase 7 — when True, the strategist reads
    # per-coin sections from ``layer_manager._coin_packages`` instead
    # of querying 12 services per cycle. Default True. Set False to
    # fall back to the legacy service-query path during Phase 9
    # observation if a regression is detected.
    use_packages: bool = True
    # Phase 6 of the 1D briefing rewrite — when True, the strategist
    # surfaces the Phase-3/4/5 briefing fields (state_label, action_hint,
    # interestingness_score, interestingness_breakdown, vote distribution)
    # in the per-coin TRADE CANDIDATES block AND extends TRADE_SYSTEM_PROMPT
    # with one new section explaining what those fields mean.
    # Phase 9 cutover (2026-05-01): flipped default False → True so the
    # brain reads briefing-mode packages with their full state on by
    # default. Operator sets to False in config.toml to roll back
    # instantly to the legacy prompt shape.
    surface_briefing_fields: bool = True

    # Brain-prompt-enrichment Phase 3.1 (2026-05-16) — number of strategy
    # voters surfaced per candidate in the TRADE CANDIDATES block. The
    # legacy Phase-6 render emitted "Top BUY" + "Top SELL" sub-blocks of
    # 3 each (so brain saw up to 6 names per coin). The enrichment fix
    # replaces those with a single combined "Top-N" line, ranked across
    # ALL directions by ``confidence × weight``. ``N`` defaults to 10.
    # Operator can roll back by setting this to 3 (or any value ≤ 3 to
    # restore the prior 6-name budget) without touching code. Setting
    # 0 disables the line entirely while keeping the rest of the votes
    # block. Gated by ``surface_briefing_fields`` — the legacy flag still
    # controls whether the votes block renders at all.
    surface_top_n_voters: int = 10

    # Issue 4 (CALL_A exploit/fetch, 2026-06-05) — strategy-evidence freshness.
    # The brain's "Strategies: N fired" line is sourced from the per-coin
    # consensus cache while the "Votes:" two-sided poll is read live at render
    # time; the two caches refresh on interleaving worker ticks and can briefly
    # disagree. A coin's strategy read older than this many seconds is treated
    # as STALE and labelled as such on the zero-fired line, so the brain weighs a
    # stale lean cautiously instead of as a fresh fact. About 1.2x the ~300s
    # strategy cadence. Tuning starting point.
    consensus_freshness_seconds: int = 360

    # Issue 5 (CALL_A exploit/fetch, 2026-06-05) — exploitation breadth targets.
    # The CALL_A system prompt directs the brain to WORK to surface the genuine
    # plays it overlooks (around three per cycle), preferring shorter holds and
    # smaller sizes — while keeping the anti-fabrication rule (never invent a
    # counter-evidence trade). These two values are the named, tunable reference
    # the per-cycle STRAT_CALL_A_ACTIVITY observability line measures against
    # (target play count and the preferred upper hold band). They are NOT a quota
    # and NOT an enable lever — the prompt's prose carries the instruction; these
    # make the intended target explicit and tuning-ready. Tuning starting points.
    brain_target_play_count: int = 3
    brain_preferred_hold_minutes_max: int = 25

    # Brain-prompt-enrichment Phase 3.2 (2026-05-16) — vote opposition
    # characterization per candidate. The legacy votes block surfaced
    # weighted BUY vs SELL aggregates but did not tell the brain WHICH
    # side was the opposing side, how many strong voters opposed, or
    # how the opposition compared to the leading side. When True, a
    # one-line "Opposition: <tier> (<n> <dir> voters at conf>=0.6,
    # opp_wsum=<w> vs agree_wsum=<w>)" follows the Top-N line. Tiers:
    # NEGLIGIBLE / WEAK / MODERATE / STRONG, thresholded against the
    # ratio of opposing weighted sum to agreeing weighted sum. Default
    # True; flip to False to suppress the line.
    emit_vote_opposition: bool = True

    # Brain-prompt-enrichment Phase 3.3 (2026-05-16) — strategy category
    # split per candidate. Brain reads category-level vote distribution
    # so cross-category agreement (more robust) is distinguishable from
    # one-category cluster (weaker). When True, a one-line "Cats:
    # <category> <NB[+MS]>, ..." follows the opposition line. Only
    # categories with at least one non-NEUTRAL vote are listed.
    # ``N B`` for buy-only, ``M S`` for sell-only, ``N B+M S`` when both
    # sides cast. NEUTRAL votes are excluded — they carry no
    # directional signal in this view. Default True; flip to False to
    # suppress.
    emit_category_split: bool = True

    # Candidate-Block Data Integrity Fix — Issue 1 (2026-06-09) — direction
    # disagreement labeling. When True, the CALL_A candidate block surfaces an
    # explicit, labeled "inputs disagree" note whenever an independent
    # directional read contradicts the X-RAY structural direction on the same
    # coin: (a) the intelligence Signal direction vs X-RAY (the SKR strong_buy
    # on a short-structure coin), and (b) the strategy-ensemble lean vs X-RAY
    # (the BSB "ensemble WEAK long" on a downtrend). It also relabels the
    # one-sided "Votes" line as the confirmed-direction tally and renders the
    # Two-sided poll whenever two-sided polling is active (not only when the
    # opposing weight is non-zero), so the Votes line, poll line and Opposition
    # tier describe the same contest. PRESENTATION ONLY — no signal/vote value
    # is recomputed; the genuine disagreement is shown as a labeled contest the
    # brain weighs, with structure/regime authoritative for direction. Flipping
    # this False is an instant rollback that removes the notes and restores the
    # prior one-sided rendering without a code change.
    emit_direction_disagreement_notes: bool = True

    # Candidate-Block Data Integrity Fix — Issue 4 (2026-06-09) — fear-greed
    # demotion in the per-coin Components line. fear_greed is a GLOBAL,
    # direction-inactive market index on a 0-100 scale; in the magnitude-ranked
    # top-5 it always crowds out the real per-coin components (which live on
    # ~[-1,1]) and reads like a live per-coin directional input even though it is
    # identical on every coin. When True, fear_greed is held out of the ranking
    # and appended once, tagged "(global, direction-inactive)", as the integer
    # index — visible but unambiguous. Presentation only; the
    # fear-greed-inactive-for-direction fix is preserved regardless of this flag.
    # Flip False to restore the prior magnitude ranking.
    fear_greed_components_demote_enabled: bool = True

    # Five-Fix Follow-Up — Fix 1 (components purity, 2026-06-10) — diagnostics
    # exclusion from the per-coin Components line. The signal classifier writes
    # internal bookkeeping flags (confidence_floor_failed,
    # confidence_below_strong, confidence_below_buy, original_signal_type) into
    # the same components dict as the genuine market inputs; the renderer's
    # numeric type check passed Python bools (bool subclasses int), so
    # True/False rendered as 1.0000/0.0000 interleaved with real market data.
    # When True, the named diagnostics are held out of the rendered Components
    # line entirely (operator decision 2026-06-10: no separated note).
    # Presentation only — the components dict, the DB JSON, the promoted
    # columns and every code consumer keep all keys. Booleans are additionally
    # blocked from rendering by an unconditional type guard regardless of this
    # flag (printing True as 1.0000 is always wrong); the flag is the rollback
    # lever for any future NUMERIC diagnostic key.
    components_diagnostics_excluded: bool = True

    # Conditional X-RAY authority (2026-06-11, operator-approved after live
    # wrong-side evidence). The direction-disagreement notes crowned
    # structure/X-RAY over an opposing ensemble UNCONDITIONALLY — even when
    # X-RAY itself graded the setup SKIP (HBAR: score 30 shorted against a
    # unanimous 26-strategy long poll at the range floor) or tagged it
    # COUNTER-TRADE (HYPE: conf 0.22 shorted at range_pos 0.00). When True,
    # the authority clause applies ONLY when the X-RAY read is tradeable by
    # its own flags; a counter-trade or skip-grade read yields a WEAK-read
    # note that points the brain at the ensemble lean and per-coin regime
    # instead, and the same-side action hint is withheld with the reason.
    # xray_authority_min_score mirrors the scorer's SKIP cutoff (C >= 45).
    # Set False to restore the prior unconditional authority framing.
    xray_authority_conditional_enabled: bool = True
    xray_authority_min_score: float = 45.0

    # Brain-Awareness Prompt Additions — Addition 2 (2026-06-09) — book-tilt
    # awareness. When True, the ACCOUNT section shows the directional composition
    # of the OPEN book (count long vs short) and a compact tilt label, plus a
    # NEUTRAL consider-note when the book is tilted — so the brain can SEE when a
    # new same-direction trade piles onto an already one-sided book. AWARENESS
    # ONLY: it does not block, cap, or suppress anything (the enforcement layer /
    # portfolio breaker is separate). Flip False for instant rollback. The tilt
    # label boundaries are centralized below:
    #   book_tilt_small_count: when abs(long - short) <= this, the book reads
    #     "balanced" regardless of ratio (avoids labeling a 1-vs-0 book as heavy).
    #   book_tilt_one_sided_ratio: when the majority/minority count ratio >= this
    #     (or one side is zero), the book reads "heavily <dir>-tilted"; in between
    #     it reads "<dir>-leaning".
    book_tilt_enabled: bool = True
    book_tilt_small_count: int = 2
    book_tilt_one_sided_ratio: float = 3.0

    # Four-Element Prompt Recalibration, Element 1 (2026-06-11) — the
    # quality-over-quota skip-permission thresholds, injected into both
    # CALL_A system prompts via placeholder tokens (see
    # _resolve_prompt_calibration in strategist.py). June-11 forensics:
    # the old keys (X-RAY SKIP grade, interestingness below 0.30) never
    # fired on the candidates that destroyed the window, while the
    # dead-thin-zero-fired cluster (zero strategies fired AND dead
    # regime AND volume ratio at or below quality_skip_thin_vol_ratio)
    # was pure poison (IMX vol_ratio 0.229, MON 0.043 — 11 submissions,
    # all losses) and every coin attempted quality_skip_heavy_attempts
    # or more times in the session lost (DYDX 24, INJ 9, IMX 7; every
    # winner was at 5 or fewer). 0.25 is the tightest round value above
    # IMX's observed 0.229 so the proven-toxic cases satisfy the stated
    # cluster; it binds only inside the three-way conjunction. These are
    # PERMISSION-language thresholds, not gates — the brain still
    # decides freely on every coin. quality_skip_heavy_attempts is the
    # SINGLE source of truth shared with Element 2's rendered
    # session-attempts line so the prompt's words and the rendered fact
    # can never drift apart.
    quality_skip_thin_vol_ratio: float = 0.25
    quality_skip_heavy_attempts: int = 6

    # Four-Element Prompt Recalibration, Element 2 (2026-06-11) — the
    # per-coin session-attempt memory line in CALL_A ("Session today: N
    # attempts, net X USD"). The strongest June-11 correlation: every
    # coin submitted 6 or more times in the session lost, and the brain
    # could not see the count — its 24th DYDX attempt looked identical
    # to its 1st. Computed READ-ONLY from trade_log (the truthful
    # ledger) for the current UTC day in the ACTIVE exchange mode.
    # Awareness only — no gate; a fresh coin renders nothing. The heavy
    # threshold is the SHARED quality_skip_heavy_attempts above. Set
    # False to suppress rendering without touching the query helper.
    session_attempts_enabled: bool = True

    # Four-Element Prompt Recalibration, Element 4 (2026-06-11) — the
    # session-liveness market-context line ("Session liveness: thin — 4
    # of 5 measured candidates at or below volume ratio 0.25."). June-11
    # evidence: 40 percent of blocks carried a volume ratio below 0.05
    # and 49 of 62 loss-coin submissions fell in the 04:00-10:00 UTC
    # trough, yet every cycle presented as equally tradeable. Aggregated
    # from the finalized candidate set's MEASURED volume ratios (zero
    # new I/O; unknown ratios excluded; zero measured ratios renders
    # nothing). Context only — NOT a clock gate; the brain remains free
    # to take a genuine play at any hour. The thin threshold is a
    # SEPARATE key from quality_skip_thin_vol_ratio (same default) so
    # the skip cluster and the session read are independently tunable.
    # Classification: thin share >= thin_min_thin_share reads "thin";
    # <= live_max_thin_share reads "live"; between reads "mixed".
    session_liveness_enabled: bool = True
    session_liveness_thin_vol_ratio: float = 0.25
    session_liveness_live_max_thin_share: float = 0.20
    session_liveness_thin_min_thin_share: float = 0.60

    # Brain-prompt-enrichment Phase 3.4 (2026-05-16) — direction-specific
    # performance line in CALL_B (position management). When True, the
    # _build_position_prompt builder emits a single line after the
    # TODAY PnL line: "## TODAY DIRECTION PERF: Longs NW/ML (X% WR) |
    # Shorts NW/ML (X% WR)". Data sourced from PerformanceEnforcer's
    # today-only per_direction counter (resets on day boundary).
    # Suppressed when no trades have closed today on either side (line
    # would be misleading). Intentionally CALL_B-ONLY — the aggressive-
    # framing rewrite removed dir_perf from CALL_A to avoid recency
    # bias on new-trade decisions; this re-introduction is observation
    # for position management only, not prescription. Default True.
    emit_direction_perf_in_callb: bool = True

    # Brain-prompt-enrichment Phase 3.5 (2026-05-16) — recent-loss
    # context bridge. Pulls one-line lessons from the
    # ``trade_intelligence`` table (TIAS Phase 2 DeepSeek-analyzed
    # ``ds_why`` + ``ds_what_should_done``) into the CALL_A per-coin
    # block for candidates flagged RECENT_LOSER_COOLDOWN. The lesson
    # gives the brain the specific cause behind the recent loss so
    # "thesis materially changed?" judgement has concrete grounding.
    #
    # CALL_B intentionally NOT in scope. The Post-Execution Closure
    # Fix (2026-05-05, strategist.py:3551-3564) removed TIAS lessons
    # from CALL_B after a closed-loop failure: CALL_B read "X just
    # lost -0.23%" → Claude force-closed a fresh 3-min-old X position
    # → that close became the next cycle's lesson, etc. Adding
    # per-position lesson context back to CALL_B would re-create that
    # failure mode. Operator approval required before any CALL_B
    # extension of this flag.
    #
    # Look-back window controlled by ``recent_loss_lookback_hours``
    # (default 336 = 14 days). Max lessons per coin controlled by
    # ``recent_loss_max_lessons`` (default 2). Default True; flip to
    # False to suppress rendering without affecting the underlying SQL
    # helper.
    emit_recent_loss_context: bool = True
    recent_loss_lookback_hours: int = 336
    recent_loss_max_lessons: int = 2
    # Direction-reconcile fix (2026-06-04, Problem 6 / F22) — max characters of a
    # TIAS loss-lesson "Cause:" excerpt shown to the brain. The old hard 57-char
    # cut dropped the failure pattern mid-sentence; 120 keeps the decision-
    # relevant context while staying within the per-coin block budget. Truncation
    # is clause/sentence-boundary aware so it never cuts mid-word.
    tias_cause_max_chars: int = 120
    # Phase 3 (Brain credentials): retry budget for ``_try_token_refresh``.
    # Refresh is a single-attempt 30 s urllib HTTP call today; transient
    # network blips cause a doomed subprocess spawn. With 3 attempts and
    # exponential backoff (1 s / 3 s / 7 s) the in-margin success rate
    # rises substantially.
    credential_refresh_max_attempts: int = 3
    # Phase 3 (Brain credentials): progressive subprocess-stall warning
    # buckets in seconds. The legacy single-warn (``_STALL_LOG_EVERY_S``
    # = 60) emits the same generic message every 60 s of silence; named
    # buckets at 60/120/240 give operators a visual escalation cue.
    stall_warn_buckets_seconds: tuple[int, ...] = (60, 120, 240)

    # Defence-in-depth cap on event_buffer injection into the Call A
    # URGENT prompt. Prevents unbounded prefix growth during
    # position-pressure storms. Existing 3000-char truncation in
    # ``EventBuffer.get_prompt_text`` still applies.
    prompt_event_buffer_max_events: int = 20

    # Definitive-fix Phase 6 (2026-04-28) — completeness gate before
    # auto-execute. See ``BrainColdStartProtection`` docstring.
    cold_start_protection: BrainColdStartProtection = field(
        default_factory=BrainColdStartProtection,
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.credential_refresh_margin_seconds, int)
            or self.credential_refresh_margin_seconds < 1
        ):
            raise ConfigError(
                "[brain.credential_refresh_margin_seconds] must be a positive "
                f"integer, got {self.credential_refresh_margin_seconds!r}",
                details={"value": self.credential_refresh_margin_seconds},
            )
        if (
            not isinstance(self.credential_refresh_max_attempts, int)
            or self.credential_refresh_max_attempts < 1
        ):
            raise ConfigError(
                "[brain.credential_refresh_max_attempts] must be a positive "
                f"integer, got {self.credential_refresh_max_attempts!r}",
                details={"value": self.credential_refresh_max_attempts},
            )


@dataclass
class Stage2Settings:
    """Stage 2 (strategist -> Claude) prompt-richness knobs.

    Independent of [scanner]: the scanner still selects top-N packages
    by interestingness (default 15); Stage 2 keeps the top-N of those
    for the prompt body so prompt size stays bounded as the per-coin
    block grows under enable_full_layer_block.

    Phased rollout: enable_full_layer_block, enable_zero_two_contract,
    and enable_priority_trim default False so each phase ships
    inert and is activated together at the live trial.

    Default ``top_n_to_brain`` raised to 10 (2026-05-05) to widen the
    candidate set Claude evaluates. Paired with the bounded-count
    contract in ``TRADE_SYSTEM_PROMPT_ZERO_TWO`` which now requests
    2-4 trades per cycle (was 1-2). The ``enable_zero_two_contract``
    flag name is retained for backward-compat; its semantic now
    selects the bounded-count contract regardless of the specific
    range, which is encoded in the prompt body.
    """

    top_n_to_brain: int = 10
    enable_full_layer_block: bool = False
    enable_zero_two_contract: bool = False
    enable_priority_trim: bool = False
    # Per-coin-authority Phase 6 (2026-05-29): the cutover + ROLLBACK flag for
    # removing the global-regime DIRECTION lead from the brain prompt. Default
    # True = per-coin authority IS the direction policy (no global "use this as
    # default bias" mandate; each coin trades on its OWN regime; the only
    # market-wide signal is the breadth SIZING brake from Phase 5). Set False to
    # instantly roll back to the old global short/long-bias NOTE if a trial shows
    # the (structurally-losing) long side bleeding once the global short-bias is
    # removed. This is the single economically-risky switch — see Phase 6.
    per_coin_direction_enabled: bool = True
    # Sniper-Latency-Size Fix Phase 2 (2026-05-07) — gated rollout of
    # identity-preserving prompt compression in the full-layer
    # formatter. When True, ``_format_packages_for_prompt_full``:
    # 1. Drops one decimal of precision on the per-coin Components
    #    line (.3f -> .2f); the underlying scoring resolution is below
    #    0.01 so the precision drop is cosmetic.
    # 2. Uses single-space separators (instead of ``", "``) on the
    #    Components and Active categories lines; Claude reads both
    #    forms identically and the comma+space is purely visual.
    # No fields are removed and no abbreviation table is needed, so
    # flipping the flag back to False recovers the legacy rendering
    # byte-for-byte. Realistic saving is on the order of ~150-300
    # chars per CALL_A on a 10-coin top-N (~1-2 % of a 15K prompt) —
    # the bigger latency win is via Anthropic prompt caching, which
    # is documented in dev_notes/sniper_latency_size_fix/
    # phase2d_caching_finding.md as deferred (incompatible with the
    # current ``--system-prompt`` invocation in claude_code_client.py).
    # Default False so rollout is operator-controlled; flip in
    # config.toml to enable.
    enable_prompt_compression: bool = False
    # Candidate-Block Data Integrity Fix — Issue 3 (2026-06-09) — decimal places
    # for the per-coin "Components:" line in the full-layer candidate block. Was
    # hardcoded to 3, which rounded a real small funding rate (e.g. -0.0002) to
    # -0.000, making a live input look dead. Default 4 matches the dedicated
    # "Funding:" line so a genuine small value is visible. Only the
    # non-compressed render path uses this; the compressed path keeps its own
    # tighter precision by design.
    component_precision_decimals: int = 4

    def __post_init__(self) -> None:
        if not isinstance(self.top_n_to_brain, int) or self.top_n_to_brain <= 0:
            raise ConfigError(
                f"[stage2.top_n_to_brain] must be a positive integer, "
                f"got {self.top_n_to_brain!r}",
                details={"value": self.top_n_to_brain},
            )
        if self.top_n_to_brain > 15:
            raise ConfigError(
                f"[stage2.top_n_to_brain] ({self.top_n_to_brain}) cannot "
                f"exceed [scanner.briefing.top_n_packages] (15). Raise the "
                f"scanner cap first if you intend more.",
                details={"value": self.top_n_to_brain},
            )


@dataclass
class FlipTPSettings:
    """Cap parameters for the XRAY-direction-flip TP derivation.

    Used by `src/core/flip_tp_capper.py` to bound the structural TP that
    `strategy_worker._execute_claude_trade` attaches to a flipped trade.
    Without this cap, the structural target (from `_sp.short_tp_price` /
    `_sp.long_tp_price`) can sit 15-20% from current price for thinly-
    supported coins, which the downstream SLTPValidator at
    `src/core/sl_tp_validator.py` correctly rejects as nonsensical and
    we lose the trade entirely.

    Defaults are chosen so the cap is strict (matches the spec literally)
    but tunable by the operator without code change. Raise
    `structural_buffer_multiplier` above 1.0 to allow more structural
    preservation in trial; raise `hard_ceiling_pct` only with care since
    the validator's own ceiling is 10%.
    """
    enabled: bool = True
    hard_ceiling_pct: float = 5.0
    fallback_tp_distance_pct: float = 2.0
    structural_buffer_multiplier: float = 1.0


@dataclass
class VolatilityStopScalingSettings:
    """Fix 7 (volatility-scaled entry stop, 2026-06-10).

    The constant ~1.5% minimum stop sat INSIDE volatile coins' normal noise
    band (91% chop stop-out in the losing window vs 44-72% in trend). When
    enabled, the entry stop is WIDENED to the coin's volatility-recommended
    distance (the profiler's recommended_sl_pct, which already encodes class +
    regime), floored at reference_stop_pct (so quiet coins keep the existing
    minimum-distance — the stop is never tightened below it) and capped at
    max_cap_pct. A wider stop is paired with a proportionally SMALLER position (a
    tighten-only size haircut) so the dollar risk at the stop stays within the
    same reference budget — the per-trade margin cap is untouched and absolute.

    Default OFF: the path is byte-identical until enabled after the offline
    replay confirms the risk budget holds.
    """
    enabled: bool = False
    reference_stop_pct: float = 1.5
    max_cap_pct: float = 5.0
    use_profiler_recommended_sl: bool = True
    recommended_sl_scalar: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 < self.reference_stop_pct <= self.max_cap_pct:
            raise ValueError(
                "risk.volatility_stop_scaling: must have 0 < reference_stop_pct "
                f"({self.reference_stop_pct}) <= max_cap_pct ({self.max_cap_pct})"
            )
        if self.recommended_sl_scalar <= 0.0:
            raise ValueError(
                "risk.volatility_stop_scaling.recommended_sl_scalar must be > 0, "
                f"got {self.recommended_sl_scalar}"
            )


@dataclass
class RiskSettings:
    """Risk management parameters (non-negotiable)."""
    max_leverage: int = 3
    mandatory_stop_loss: bool = True
    default_stop_loss_pct: float = 2.0
    default_take_profit_pct: float = 4.0
    # F37 (2026-06-05) — downstream MINIMUM stop-loss distance (percent of entry)
    # enforced by SLTPValidator.validate_sl on the brain's PROPOSED stop before
    # execution. The CALL_A prompt says "SL at least 1.5% from entry" but nothing
    # downstream enforced it, so a correct-side stop inside 1.5% (or at/through
    # entry) passed straight to the exchange. This clamps any too-close stop out to
    # the minimum sane distance. Safety net on the ENTRY stop only — it does NOT
    # touch the exit systems (trailing/ladder/chandelier/cap/stall). Distinct from
    # the headspace buffer (a wrong-side correction width) and from the unrelated
    # time-decay ``min_sl_pct``.
    min_sl_distance_pct: float = 1.5
    max_position_size_pct: float = 10.0
    max_open_positions: int = 5
    daily_loss_limit_pct: float = 5.0
    max_total_exposure_pct: float = 50.0
    max_drawdown_pct: float = 15.0
    min_order_value_usdt: float = 10.0
    loss_cooldown_seconds: int = 300
    # Maker-entry experiment (win-rate enhancement Phase D, 2026-07-07).
    # "market" (default) = today's taker entry, byte-identical behaviour.
    # "limit" = passive GTC limit at current price offset entry_limit_
    # offset_bps toward the passive side; strategy_worker then waits up
    # to entry_limit_timeout_seconds for the fill and CANCELS + SKIPS on
    # timeout (no chase — a missed entry is a skipped trade). Cuts the
    # entry side from 0.055% taker toward 0.02% maker. Ships "market";
    # activate only after the entry-quality filters' effect is measured
    # (one variable at a time).
    entry_order_type: str = "market"
    entry_limit_timeout_seconds: float = 20.0
    entry_limit_offset_bps: float = 0.0
    # Direction-flip threshold for the XRAY direction recheck in
    # strategy_worker._execute_claude_trade. When the opposite-direction
    # R:R is more than this multiple of the chosen-direction R:R, the
    # trade is flipped (Phase 1 of dir-block-fix, 2026-05-05). Default
    # 3.0 keeps the prior boundary at which the legacy code began
    # halving size; under the flip mechanism, ratio >= threshold means
    # FLIP, not REDUCE or BLOCK.
    xray_dir_flip_threshold_ratio: float = 3.0
    # X-RAY Direction-Flip Switch (IMPLEMENT_XRAY_FLIP_SWITCH, 2026-05-25).
    # Master on/off gate for X-RAY's ability to REVERSE trade direction in
    # strategy_worker._execute_claude_trade. When False (operator default,
    # 2026-05-25), the low-conviction structural-RR flip does not fire and
    # the sanctioned brain-then-APEX direction (with its SL/TP) executes
    # unchanged; X-RAY scoring, grading, selection, prompt/APEX rendering,
    # the structural-validity blocks (XRAY_BLOCK / XRAY_CONFLICT), and the
    # high-conviction veto (xray_high_conviction_protection_enabled) are
    # all unaffected. When True, behavior is exactly as before this switch
    # existed. Reversible at runtime via [risk] xray_dir_flip_enabled.
    xray_dir_flip_enabled: bool = False
    # J3 (2026-05-14) — XRAY structural-RR override of APEX_DIR_LOCK.
    # Audit OBS-14 saw ALICEUSDT enter Buy at ratio=338x (rr_long=0.0,
    # rr_short=6.8) because APEX locked the direction; LTCUSDT at
    # ratio=55.3x correctly flipped only because APEX timed out and
    # no lock was set. The fix: when the lock is set AND the
    # opposite-direction R:R ratio exceeds this threshold, the XRAY
    # flip overrides the lock. Default 10.0 is data-driven from the
    # audit-window suppression distribution (4.9x, 17.6x, 30x, 324x,
    # 338x — 10.0 admits the upper four but holds the lock at the
    # low end where regime alignment legitimately wins).
    # Operator-tunable via [risk] xray_lock_override_ratio_threshold
    # in config.toml. Set to a value <= the flip threshold to disable
    # override (lock stays absolute).
    xray_lock_override_ratio_threshold: float = 10.0

    # R3 direction-fix (2026-05-17) — WR-aware override threshold.
    # The legacy single 10.0x threshold above suppressed 8 trades in
    # the 3.0-9.99x dead zone on 2026-05-16 (aggregate -$111.98,
    # BSBUSDT -$70.08). The new mechanism derives the per-direction
    # threshold from measured per-direction WR over a rolling window.
    # Formula:
    #   threshold_for_override_into_dir =
    #     wr_base * (1 - dir_wr_fraction)
    # bounded by [floor, ceiling]. Asymmetric only because the WR data
    # is asymmetric — same formula applies to Buy and Sell. With
    # neutral 50%/50% WR the threshold equals wr_base * 0.5 = 5.0
    # (the midpoint of the legacy dead zone). With Buy WR 60% the
    # Sell->Buy threshold drops to wr_base * 0.4 = 4.0; with Buy WR
    # 80% it drops to the floor 2.0. The legacy 10.0 is the
    # cold-start fallback when fewer than ``xray_lock_override_wr_
    # window_min`` recent trades exist for the relevant direction.
    xray_lock_override_wr_base: float = 10.0
    xray_lock_override_wr_floor: float = 2.0
    xray_lock_override_wr_ceiling: float = 15.0
    xray_lock_override_wr_window_trades: int = 200
    xray_lock_override_wr_window_min: int = 30

    # P0-2 fix (2026-05-22) — high-conviction protection. When the
    # brain's directive is high-conviction (per-coin regime aligns with
    # direction AND structural_data.trade_direction agrees), XRAY is
    # allowed to VETO (skip the trade with a single-reason log) but
    # NEVER allowed to silently reverse the direction. Protects
    # high-conviction directives in trending regimes from being
    # silently flipped by structural-rr asymmetry that arises when
    # price hugs a level. When low-conviction (volatile/ranging regime
    # or trade_direction disagreement), the existing XRAY override
    # path remains, but the dual APEX_DIR_LOCK + XRAY_DIR_FLIP logging
    # is replaced by a single DIRECTION_DECISION line per trade.
    # Kill-switch: set false to revert to pre-P0-2 behavior.
    xray_high_conviction_protection_enabled: bool = True

    # X-RAY Trade-Suppression Switch (IMPLEMENT_XRAY_SUPPRESS_SWITCH,
    # 2026-05-25). Master on/off gate for X-RAY's ability to SUPPRESS
    # (block/skip) a trade the brain + APEX already decided, inside
    # strategy_worker._execute_claude_trade. When False (operator default,
    # 2026-05-25) X-RAY blocks NOTHING: each would-be block (xray_skip,
    # xray_conflict, xray_veto_high_conviction, xray_dir_block,
    # xray_dir_flip_blocked) emits one XRAY_BOOKLOG line and the brain's
    # direction proceeds to execution. X-RAY scoring, grading, selection,
    # structural_placement, and the analysis logs (XRAY_DIR_MISMATCH /
    # XRAY_OVERRIDE_RATIO_DETAIL) are UNAFFECTED — X-RAY keeps running and
    # journaling. When True, behavior is exactly as before this switch
    # (X-RAY blocks). Orthogonal to xray_dir_flip_enabled (which gates
    # REVERSAL); composes with xray_high_conviction_protection_enabled
    # (which only decides whether the veto is computed when suppression is
    # ON). Reversible at runtime via [risk] xray_trade_suppression_enabled.
    xray_trade_suppression_enabled: bool = False

    # TP-cap parameters for the flip path (TP-Volume-Closure fix Phase 1B,
    # 2026-05-07). Loaded from `[risk.flip_tp]` in config.toml.
    flip_tp: FlipTPSettings = field(default_factory=FlipTPSettings)

    # Fix 7 (volatility-scaled entry stop, 2026-06-10). Loaded from
    # `[risk.volatility_stop_scaling]`. Default OFF (byte-identical).
    volatility_stop_scaling: VolatilityStopScalingSettings = field(
        default_factory=VolatilityStopScalingSettings
    )


@dataclass
class AlertSettings:
    """Telegram alert configuration."""
    telegram_enabled: bool = False
    alert_levels: list[str] = field(default_factory=lambda: ["WARNING", "CRITICAL"])
    daily_summary: bool = True
    daily_summary_time: str = "00:00"
    max_alerts_per_minute: int = 10
    trade_alerts: bool = True
    signal_alerts: bool = True
    error_alerts: bool = True
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class WatchdogEmergencySettings:
    """Layer 4 Realignment Phase 3.2 (2026-05-06) — configurable
    thresholds for the watchdog's system-initiated emergency-mode
    triggers.

    Pre-fix the watchdog hardcoded ``session_pnl < -5.0`` and
    ``hard_stops_this_hour >= 3`` as triggers for ``mode=emergency``,
    which caused EVERY position to be force-closed via
    ``position_service.close_position()`` directly (bypassing every
    Layer 4 protection). The ``hard_stops >= 3/h`` threshold can fire
    on a noisy hour with normal-loss trades; raising it to 5 reduces
    false-positive emergencies and gives the regular loss-management
    paths (CALL_B, time-decay, sniper) more room to act before the
    nuclear option triggers.

    Live in config under ``[watchdog.emergency]`` so operators can
    tune without code change. Field names are intentionally explicit
    so the relationship to the watchdog ``_determine_mode`` checks is
    obvious to future readers.
    """

    # Session circuit-breaker. Emergency fires when the session-level
    # PnL drops below this value (negative percent). Pre-fix hardcode
    # was -5.0; preserved as default. More-negative values give more
    # rope before the emergency triggers (operator preference for
    # aggressive trading).
    session_pnl_threshold_pct: float = -5.0

    # Per-hour hard-stop counter. Emergency fires when this many SL
    # hits accumulate in a 60-min rolling window. Pre-fix hardcode
    # was 3; raised to 5 to absorb noisy hours where normal-loss
    # trades happen to cluster.
    hard_stops_per_hour_threshold: int = 5


@dataclass
class WatchdogSettings:
    """Position watchdog configuration."""
    enabled: bool = True
    check_interval_seconds: float = 10.0
    loss_warning_pct: float = 1.0
    trailing_loss_pct: float = 0.5
    sl_proximity_pct: float = 30.0
    rapid_move_pct: float = 0.5
    brain_trigger_loss_pct: float = 1.5
    brain_cooldown_seconds: int = 120
    partial_close_pct: float = 50.0
    max_brain_calls_per_hour: int = 10
    timeout_threshold_pct: float = 95.0  # % of max_hold_minutes before timeout close
    early_exit_enabled: bool = False  # 0% historical win rate (24/24 losses) — SL handles exits; flip true to re-enable
    # Phase 2 (P0-1): fast set-diff reconcile cadence — independent of the
    # 5-min thesis reconcile. 0.0 disables the fast loop (kill switch).
    fast_reconcile_seconds: float = 30.0
    # Post-Execution Closure Fix Phase 1B (2026-05-05) — minimum-hold
    # guardrail for strategic close/take_profit actions queued by CALL_B.
    # Defense-in-depth against recency-bias closure language sneaking back
    # into the position-management prompt. Hold/tighten/set_exit are
    # unaffected; only soft-reason close/take_profit on positions younger
    # than ``strategic_action_min_hold_seconds`` get blocked. Any close
    # whose reason matches a substring in
    # ``strategic_action_allowed_early_close_reasons`` (case-insensitive)
    # bypasses the gate and executes immediately, so genuine SL/TP/
    # structure/regime/manual closures are NOT blocked.
    strategic_action_min_hold_seconds: float = 300.0
    strategic_action_allowed_early_close_reasons: list[str] = field(
        default_factory=lambda: [
            "stop loss hit", "sl hit",
            "take profit hit", "tp hit",
            "structure invalidated", "setup broken",
            "regime change", "regime shift",
            "manual operator close", "manual close",
            # Mid-Hold Trade Management Fix Audit Hotfix (2026-05-19) —
            # when brain cites the new THESIS_INVALIDATION surface (the
            # fix's reason for closing), the existing strategic_action
            # gate must not block it. Brain is acting on evidence
            # (the watchdog's M5 close-beyond-criterion detection),
            # not recency bias. Caught live in production at 21:30:04:
            # STRAT_ACTION_CLOSE_BLOCKED | sym=DYDXUSDT age=158s
            #   min_hold=300s rsn='THESIS_INVALIDATION state=INVALIDATED...'
            #   reason_allowed=false close_skipped=true
            # The mid-hold fix's design intent is brain-decides; the
            # existing min_hold gate must yield to the new
            # evidence-based reason. Two substrings cover both the
            # exact uppercase event name and the lowercase prose form.
            "thesis_invalidation", "thesis invalidated",
            "ensemble_flip", "ensemble flip",
        ],
    )
    # Layer 4 Realignment Phase 3.2 (2026-05-06) — emergency-mode
    # trigger thresholds (configurable via [watchdog.emergency]).
    emergency: WatchdogEmergencySettings = field(
        default_factory=WatchdogEmergencySettings,
    )
    # Mid-Hold Trade Management Fix Phase 3.4 (2026-05-19) —
    # ensemble-flip event detection tunables.
    #
    # ensemble_flip_detection_enabled: kill switch. False short-circuits
    #   _monitor_position's flip check; the cache and prompt-surfacing
    #   still work, but no ENSEMBLE_FLIP_DETECTED events are queued.
    # ensemble_flip_strong_threshold: agreeing-votes floor for STRONG
    #   consensus. Mirrors EnsembleVoter.vote() line 130 default (4.0).
    #   Phase 3.10 may tune.
    # ensemble_flip_dedupe_window_seconds: throttle window per (symbol,
    #   direction). Ensemble can oscillate STRONG-GOOD-STRONG within a
    #   single 5-min signal cycle in choppy markets; this dedupe
    #   collapses such oscillations into a single event.
    ensemble_flip_detection_enabled: bool = True
    ensemble_flip_strong_threshold: float = 4.0
    ensemble_flip_dedupe_window_seconds: float = 300.0
    # Mid-Hold Trade Management Fix Phase 3.5 (2026-05-19) —
    # thesis-invalidation monitoring tunables.
    #
    # thesis_invalidation_detection_enabled: kill switch for the 2A
    #   level-monitoring lane.
    # thesis_invalidation_close_buffer_pct: percent buffer beyond the
    #   level required to declare INVALIDATED (M5 close-beyond).
    #   Default 0.5% mirrors structural_levels.sl_buffer_pct.
    # thesis_invalidation_wick_buffer_pct: percent buffer for DEGRADING
    #   (wick-beyond on current price). Smaller than the close buffer.
    thesis_invalidation_detection_enabled: bool = True
    thesis_invalidation_close_buffer_pct: float = 0.5
    thesis_invalidation_wick_buffer_pct: float = 0.1

    # Issue 1 (2026-05-18) — multi-factor scoring on brain-driven
    # close votes (``wd_claude_action`` path). See
    # ``src/risk/wd_brain_scoring.py`` for the scoring function and
    # ``IMPLEMENT_THREE_ISSUES_FIX.md`` Issue 1 §B for the factor
    # table. Default rollout is Phase 1 (log-only); operator flips
    # ``wd_brain_scoring_enforce`` to True after ~24-48h of log
    # validation per Phase 4 of the prompt.
    #
    # - ``wd_brain_scoring_enabled`` kill-switch: when False the
    #   scoring module is not invoked and the existing 300s min-hold
    #   guardrail is the sole arbiter (legacy behaviour pre-Issue 1).
    # - ``wd_brain_scoring_enforce`` Phase 1 vs Phase 2 selector:
    #   False = scoring runs and logs every factor + composite, but
    #   the brain's close still fires (log-only). True = composite
    #   decides — execute if >= threshold, hold if 0 <= c < threshold,
    #   hold + tighten SL if c < 0.
    # - ``wd_brain_scoring_threshold`` (default 6.0) matches the
    #   prompt's operator-validated default; operator may tune via
    #   config.toml.
    wd_brain_scoring_enabled: bool = True
    wd_brain_scoring_enforce: bool = False
    wd_brain_scoring_threshold: float = 6.0

    # P0-3 fix (2026-05-22) — hard_risk_floor. When the current SL
    # consumption exceeds this percentage, force-close the position
    # regardless of the composite scoring recommendation. Catches the
    # edge cases where the composite is mathematically below threshold
    # but the position is already burning through its risk budget
    # (e.g., the 2026-05-22 INJUSDT case at 82.7% SL consumed with the
    # brain texting "one tick from stop" and the watchdog rejecting the
    # close). Default 85.0; operator-tunable. Set to 100.0 or higher
    # to disable (the SL itself will fire at 100%).
    wd_hard_risk_floor_sl_pct: float = 85.0


@dataclass
class SLGatewaySettings:
    """Single-entry-point stop-loss gateway configuration.

    The gateway consolidates ALL SL modifications (Time-Decay, SENTINEL,
    Profit Sniper trail, watchdog trail, brain tighten, etc.) behind one
    validator that enforces tighten-only, min-distance, max-step, and
    rate-limit rules.

    `enabled=false` runs in symmetric pass-through mode: the gateway still
    calls position_service.set_stop_loss and still tracks per-symbol
    last_sl state, but skips all four rule checks. This preserves log/count
    parity with the pre-gateway baseline so observers can safely A/B-test.

    `log_only_*` flags downgrade a would-be REJECT to a
    SL_GATEWAY_REJECT_WOULD log and still apply the SL — used for staged
    enforcement rollout.
    """
    enabled: bool = False
    min_distance_pct: float = 0.3
    # Phase 2 of dir-block-fix (2026-05-05): lowered 0.5 → 0.25 so each
    # trail tighten only moves SL by a quarter of the remaining distance,
    # cutting peak give-back. config.toml is the source of truth at
    # runtime; this default applies only when the field is missing.
    max_step_pct: float = 0.25
    rate_limit_seconds: int = 30
    # Fast-move placeability A/B lever (default OFF/inert). The profit-lock lane
    # (_PROFIT_LOCK_SOURCES in the gateway) may re-tighten faster than
    # rate_limit_seconds so a sustained big-mover peak is tracked at finer
    # cadence. Clamped to <= rate_limit_seconds at read time; the default 30
    # (== rate_limit_seconds) is INERT — byte-identical behaviour to today.
    # Tighten-only, the fresh-mark degrade, and the wrong-side guard are
    # unchanged and run before R4 with no bypass, so a faster cadence can only
    # ratchet the stop UP toward a placeable value and cannot weaken the
    # wire-fail safety. Set lower (e.g. 10; not below the 5s sniper tick) to A/B
    # the cadence live against the placement forensics.
    profit_lock_rate_limit_seconds: int = 30
    # Enforcement flags — default all off (enforce) EXCEPT during rollout.
    log_only_global: bool = False
    log_only_tighten_only: bool = False  # keep False: tighten-only is safety-critical
    log_only_min_distance: bool = False
    log_only_max_step: bool = False
    log_only_rate_limit: bool = False

    # ATR-scaled min_distance (R2). When the coin's atr_5m_pct is available,
    # the effective min_distance becomes:
    #     max(min_distance_abs_floor_pct, atr_5m_pct * min_distance_atr_multiplier)
    # clamped to min_distance_class_ceiling[class]. Falls back to
    # min_distance_pct when atr_5m_pct <= 0 (profiler unavailable or cold).
    # See src/analysis/vol_scale.py::min_distance_for_class.
    min_distance_atr_multiplier: float = 0.5
    min_distance_abs_floor_pct: float = 0.05
    # PF/LC Top-15 Problem 1.1 — breakeven floor on the R2 min-distance clamp.
    # When an armed ladder breakeven floor is being placed, R2 may clamp the
    # stop toward price but never PAST breakeven (the trade's entry price), so a
    # high-volatility coin whose eff_min would otherwise force the floor
    # sub-breakeven instead holds it at breakeven. R3 already has this carve-out;
    # this gives R2 the symmetric one. Off-switch (set false) reverts to the
    # unconditional R2 clamp. R1 tighten-only and the cap are never affected.
    r2_breakeven_floor_enabled: bool = True
    # Dynamic Adaptive Exit (2026-06-15) — R2 profit-lock-floor exemption. When
    # true, an armed R-derived profit lock from a trusted profit source is held
    # at its value inside the min-distance instead of being dropped as a
    # clamp-noop. Off by default (inert); enabled with the rescaling in Commit 2.
    # Tighten-only, wrong-side, max-step, rate-limit, and cap precedence are
    # never weakened.
    r2_profit_lock_floor_enabled: bool = False
    # Dynamic Adaptive Exit FIX (2026-06-15) — fresh-mark placeability degrade.
    # The R2 placeability math judges against the caller's current_price SNAPSHOT,
    # which the wire latency (~150 ms) can stale. On a fast retrace a value that is
    # placeable against the snapshot is wrong-side of the LIVE mark, so the exchange
    # blocks it and NOTHING is placed — the green trade rides its old wide stop back
    # to a loss (the profit-lock wire-fail give-back, confirmed live on PYTHUSDT /
    # MONUSDT / EGLDUSDT). When true, the gateway re-validates a near-the-money final
    # stop against the freshest mark (the SAME field the adapter enforces) and
    # DEGRADES it to the closest placeable stop — floored at breakeven for a trusted
    # source — instead of emitting the unplaceable value. R1 tighten-only is
    # re-checked; never loosens; falls back to a no-op (keep the existing stop) when
    # even the fresh boundary cannot improve. Off-switch reverts to the prior
    # emit-and-wire-fail behaviour. log_only variant observes the would-degrade.
    r2_fresh_mark_degrade_enabled: bool = True
    log_only_fresh_mark_degrade: bool = False
    # The fresh-mark recheck only fires for NEAR-the-money stops (where the wire
    # latency could realistically flip placeability). A stop farther than this
    # multiple of the effective min-distance from the snapshot price cannot flip
    # wrong-side in ~150 ms, so the recheck (and its extra get_position call) is
    # skipped — real winners and far loss stops never pay the cost. Centralized
    # here (not an inline literal) so the at-risk window is tunable.
    fresh_mark_recheck_distance_mult: float = 2.0
    min_distance_class_ceiling: dict = field(default_factory=lambda: {
        "dead": 0.30, "low": 0.50, "medium": 1.00,
        "high": 2.00, "extreme": 3.50,
    })

    # ── Phase 1: trade-state owner switch (exit-authority consolidation) ──
    # When owner_switch_enabled, the gateway computes each trade's state
    # (green/red relative to entry, with the breakeven_deadband_pct band as a
    # hysteresis zone) and classifies every writer into a bucket: HEAD (always
    # allowed, only tightens — the catastrophic cap), GREEN owner (profit
    # engine, writes only when green), RED owner (loss engine, writes only when
    # red), ADVISORY (brain/sentinel/watchdog-scoring — demoted in Phase 5), and
    # ALWAYS (the opening stop and the naked-position safety sweeper, never
    # blocked). owner_switch_enforce gates HARD blocking: when false the gate is
    # log-only — it computes state/owner and logs SL_GATEWAY_OWNER_HANDOFF and
    # SL_GATEWAY_WRONG_OWNER_WOULD but never blocks a write, mirroring the
    # gateway's own log_only rollout. advisory_enforce gates the Phase-5
    # demotion of advisory writers (kept false until Phase 5).
    # faded_winner_rearm_red decides whether a once-green ('graduated') trade
    # that crosses back below breakeven hands the stop to the loss engine
    # (Rule 5) or stays green-owned with only the Head protecting it (today's
    # graduation-latch behavior). Default false preserves current behavior; it
    # is decided at the Phase-1 gate with live log-only evidence and, when
    # turned on, is coordinated with the sniper's graduation_crater_rearm so the
    # red owner has its tools. All bucket sets are centralized here (Rule 9) so
    # the hierarchy is auditable and tuning-ready; the boot sentinel
    # SL_GATEWAY_BUCKETS prints them at startup.
    owner_switch_enabled: bool = False
    owner_switch_enforce: bool = False
    advisory_enforce: bool = False
    faded_winner_rearm_red: bool = False
    breakeven_deadband_pct: float = 0.05
    # Phase 2 — the Head as sole override (operator's Option A, profit-priority).
    # When true, a running GREEN trade may be seized only by the Head (the
    # catastrophic cap) or its own green owner; every other writer — the loss
    # engine and the advisory systems — is deferred on a green trade. This is
    # what lets a winner run: nothing but catastrophe interrupts the profit
    # engine. The Head can only ever tighten (R1 beneath the gate guarantees it
    # never loosens). Set false to let advisory writers touch a green trade
    # again (pre-Phase-2 behavior).
    head_only_seizes_green: bool = True
    head_sources: list = field(default_factory=lambda: [
        "loss_cap", "loss_cap_emergency",
    ])
    green_sources: list = field(default_factory=lambda: [
        "profit_sniper_ladder", "profit_sniper_trail", "profit_sniper_lock",
        "profit_sniper_breakeven", "micro_floor",
    ])
    red_sources: list = field(default_factory=lambda: [
        "time_decay", "loss_structure", "loss_recovery",
    ])
    advisory_sources: list = field(default_factory=lambda: [
        "brain_tighten", "watchdog_tighten", "wd_brain_scoring",
        "sentinel_advisor", "sentinel_deadline", "sentinel_breakeven",
        "watchdog_lock_peak", "watchdog_breakeven",
        "trail_activation", "trail_update",
    ])
    always_allowed_sources: list = field(default_factory=lambda: [
        "loss_atr_initial", "safety_sweeper",
    ])


@dataclass
class ScannerScoringWeights:
    """Composite opportunity-score weights for the corrected Layer 1 scanner.

    The corrected ScannerWorker (Phase 6) reads warm caches from the 7 data
    workers and computes a per-coin opportunity score as a weighted sum of
    six normalized components. Weights are configurable so the operator
    can re-balance based on observed trade outcomes.

    Definitive-fix Phase 4 (2026-04-28): added ``rr`` weight so RR is
    both a (relaxed) gate AND a ranking signal. Coins with marginal RR
    (just above ``min_rr_ratio``) still qualify but rank below coins
    with strong RR. Weights re-balanced so the sum stays 1.0:
        structure 0.30 → 0.27
        strategy  0.30 → 0.27
        signal    0.15 → 0.13
        regime    0.15 → 0.13
        funding   0.10 → 0.10
        rr               0.10  (new)

    See ``LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md`` §9.3.
    """
    structure: float = 0.27
    strategy: float = 0.27
    signal: float = 0.13
    regime: float = 0.13
    funding: float = 0.10
    rr: float = 0.10


@dataclass
class ScannerHysteresisSettings:
    """Phase 5 (Universe flapping fix) — consecutive-scan hysteresis on the
    active_universe membership decision.

    Without hysteresis, a coin oscillating around the cutoff score enters
    and exits the universe every scan, triggering KLINE_BACKFILL → cold
    start → STRAT_SKIP_STALE storms (live observation: 14 rotations/hour
    on 2026-04-26). The streak gates dampen this:

    - A coin enters only if its score has been ``>= cutoff +
      entry_threshold_above_min`` for ``entry_consecutive_scans`` ticks.
    - A coin exits only if its score has been ``<= cutoff +
      exit_threshold_below_min`` (which is negative) for
      ``exit_consecutive_scans`` ticks.
    - In the dead-band between the two thresholds, both streaks reset
      so transient noise does not advance either gate.

    Force-include for BTC/ETH and open-position coins runs BEFORE the
    streak check and is unaffected.
    """
    enabled: bool = True
    entry_consecutive_scans: int = 2
    exit_consecutive_scans: int = 3
    entry_threshold_above_min: int = 5
    exit_threshold_below_min: int = -5


@dataclass
class ScannerQualitativeSettings:
    """Layer 1 restructure Phase 5 — qualitative checklist for ScannerWorker.

    Coins must pass all 5 criteria to be considered for selection:
    XRAY setup type ≠ NONE, ensemble consensus in ``min_consensus`` set,
    regime aligned with proposed direction, RR ≥ ``min_rr_ratio``, and
    no blockers (extreme funding against direction, manipulation_likely
    session, recent failure within ``recent_failure_blocker_hours``).

    Force-include of open-position symbols and BTCUSDT/ETHUSDT
    reference pairs runs BEFORE the qualitative gate and is unaffected.

    Attributes:
        min_rr_ratio: Minimum reward-to-risk to qualify.
        min_consensus: Lowest-tier consensus that still qualifies. The
            implementation maps GOOD → {STRONG, GOOD}; STRONG → {STRONG}.
            LEAN/WEAK/CONFLICT always fail by default.
        require_regime_alignment: Toggle the regime-direction check.
        funding_blocker_threshold_pct: |funding rate| above this value
            blocks the coin if its sign is against the proposed direction.
            Expressed as fractional rate (0.001 = 0.1%).
        recent_failure_blocker_hours: Lookback hours for the
            recent-failure (negative-PnL) blocker.
        max_selection: Cap on the size of the selected universe.
        min_selection: Floor below which the implementation outputs all
            qualifying coins rather than padding with unqualified.
    """
    min_rr_ratio: float = 1.3
    min_consensus: str = "GOOD"
    require_regime_alignment: bool = True
    funding_blocker_threshold_pct: float = 0.001
    recent_failure_blocker_hours: int = 1
    max_selection: int = 15
    min_selection: int = 0

    def __post_init__(self) -> None:
        valid = {"STRONG", "GOOD"}
        if self.min_consensus not in valid:
            raise ValueError(
                f"scanner.qualitative.min_consensus must be one of {valid}, "
                f"got {self.min_consensus!r}"
            )
        if self.min_rr_ratio <= 0:
            raise ValueError(
                f"scanner.qualitative.min_rr_ratio must be > 0, "
                f"got {self.min_rr_ratio}"
            )
        if self.max_selection < self.min_selection:
            raise ValueError(
                f"scanner.qualitative.max_selection ({self.max_selection}) "
                f"must be >= min_selection ({self.min_selection})"
            )


@dataclass
class ScannerBriefingInterestingnessWeights:
    """Phase 4 of the 1D briefing rewrite — interestingness component weights.

    Sum is validated to 1.0 (±1e-6). Adjusting weights tunes how much
    the ranker rewards each state aspect; defaults are calibrated so a
    typical 50-coin universe in current market conditions has its
    top-15 cut comfortably seating ≥12 actionable coins per cycle.
    """

    cleanness: float = 0.20
    confluence: float = 0.20
    extremity: float = 0.15
    label_strength: float = 0.20
    structural_quality: float = 0.15
    mtf_alignment: float = 0.07
    open_position_floor: float = 0.03

    def __post_init__(self) -> None:
        total = (
            self.cleanness + self.confluence + self.extremity
            + self.label_strength + self.structural_quality
            + self.mtf_alignment + self.open_position_floor
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                "scanner.briefing.interestingness_weights must sum to 1.0; "
                f"got {total} (cleanness={self.cleanness}, "
                f"confluence={self.confluence}, extremity={self.extremity}, "
                f"label_strength={self.label_strength}, "
                f"structural_quality={self.structural_quality}, "
                f"mtf_alignment={self.mtf_alignment}, "
                f"open_position_floor={self.open_position_floor})"
            )
        for name, val in (
            ("cleanness", self.cleanness),
            ("confluence", self.confluence),
            ("extremity", self.extremity),
            ("label_strength", self.label_strength),
            ("structural_quality", self.structural_quality),
            ("mtf_alignment", self.mtf_alignment),
            ("open_position_floor", self.open_position_floor),
        ):
            if val < 0.0 or val > 1.0:
                raise ValueError(
                    f"scanner.briefing.interestingness_weights.{name} "
                    f"must be in [0, 1]; got {val}"
                )


@dataclass
class ScannerBriefingSettings:
    """Phase 4 of the 1D briefing rewrite — briefing-pipeline configuration.

    Phase 5 introduces ``mode = "exclusion" | "briefing"`` to gate the
    briefing-mode scanner path; until then this section's settings are
    only consumed by the per-package interestingness call inside
    ``_build_package`` (additive surface — score is computed and
    surfaced even under the legacy exclusion-mode tick).

    Attributes:
        top_n_packages: Briefing-mode scanner takes this many coins
            (forced-include open positions on top). Phase 5 reads.
        min_briefing_packages: Soft floor — pad up to this many
            coins from the top of the unselected tail when ``top_n``
            falls short. The brain ALWAYS sees ≥ this many briefings.
        qualified_threshold: Interestingness floor below which a
            briefing-mode package is marked ``qualified=False`` for the
            cold-start gate count (still rendered to the brain, just
            doesn't count toward ``min_qualified_packages``).
        prompt_floor_interestingness: Brain prompt's per-coin block is
            skipped when interestingness < this AND no labels fired AND
            no open position. The coin still appears in the per-cycle
            briefing summary stats.
        interestingness_weights: Component weights for the formula —
            see :class:`ScannerBriefingInterestingnessWeights`.
    """

    top_n_packages: int = 15
    min_briefing_packages: int = 12
    qualified_threshold: float = 0.30
    prompt_floor_interestingness: float = 0.20
    interestingness_weights: ScannerBriefingInterestingnessWeights = field(
        default_factory=ScannerBriefingInterestingnessWeights
    )

    def __post_init__(self) -> None:
        if self.top_n_packages <= 0:
            raise ValueError(
                f"scanner.briefing.top_n_packages must be > 0, "
                f"got {self.top_n_packages}"
            )
        if self.min_briefing_packages > self.top_n_packages:
            raise ValueError(
                f"scanner.briefing.min_briefing_packages "
                f"({self.min_briefing_packages}) must be <= top_n_packages "
                f"({self.top_n_packages})"
            )
        if not 0.0 <= self.qualified_threshold <= 1.0:
            raise ValueError(
                f"scanner.briefing.qualified_threshold must be in [0, 1]; "
                f"got {self.qualified_threshold}"
            )
        if not 0.0 <= self.prompt_floor_interestingness <= 1.0:
            raise ValueError(
                f"scanner.briefing.prompt_floor_interestingness "
                f"must be in [0, 1]; got {self.prompt_floor_interestingness}"
            )


@dataclass
class LabellerSettings:
    """Issue 3 of 2026-05-19 direction-bias fix Phase B — state labeller
    regime-haircut configuration.

    The 8 per-trigger regime hard-kill predicates in
    ``src/workers/scanner/state_labeler.py`` (lines 253, 268, 283, 301,
    356, 371, 477, 491) historically returned ``None`` when the current
    market regime did not match the trigger's expected regime. After
    the Issue 3 fix, those predicates compute the base confidence and
    multiply by ``counter_regime_confidence_haircut`` when regime
    mismatches, allowing the brain to see lower-confidence
    counter-regime labels rather than no signal at all.

    Attributes:
        counter_regime_confidence_haircut: Multiplier applied to base
            confidence when a trigger's expected regime does not match
            the current regime. ``0.0`` reproduces the legacy hard-kill
            (label suppressed); ``0.5`` is the default — labels fire at
            half their normal confidence in mismatched regime;
            ``1.0`` removes the regime gate entirely (labels fire at
            full confidence regardless of regime). Range: ``[0.0, 1.0]``.
    """

    counter_regime_confidence_haircut: float = 0.5
    # Phase 1 calibration (2026-06-08) — extreme-sentiment contrarian label
    # (EXTREME_FEAR_CONTRARIAN_LONG / EXTREME_GREED_CONTRARIAN_SHORT). The
    # label's confidence was a GLOBAL F&G-only scalar, so it was the
    # uniform-confidence primary on ~87% of candidates in a sentiment extreme.
    #   extreme_sentiment_conviction_floor: minimum multiplier applied to the
    #     fear/greed-extremity when the coin has NO structural conviction, so a
    #     structure-blind coin's label confidence floors low and loses primary
    #     to a real structural label. 1.0 = pre-calibration (no scaling).
    #   extreme_sentiment_offtrend_haircut: when True, broaden the
    #     counter-regime haircut from trend-against-only to also cover
    #     dead/balanced regimes (any regime that is not ranging / with-trend /
    #     volatile). False = pre-calibration behaviour.
    extreme_sentiment_conviction_floor: float = 0.35
    extreme_sentiment_offtrend_haircut: bool = True
    # Four-Element Prompt Recalibration, Element 3 (2026-06-11) — when True,
    # scanner_worker passes the structure engine's pre-clamp range truth
    # (range_breakout) into label_state, where a contradicting break
    # suppresses the range-fade and funding-fade labels whose
    # mean-reversion premise it falsifies: RANGE_FADE_LONG cannot fire on
    # a price BELOW the range (a breakdown is not a floor), mirrored for
    # shorts above. In-range labelling is byte-identical either way
    # (position_in_range stays unplumbed). Rule 5 trial: fade labels fire
    # only on genuine in-range extremes. Set false to restore the legacy
    # labels without touching the prompt markers.
    range_fade_breakout_guard_enabled: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.counter_regime_confidence_haircut <= 1.0:
            raise ValueError(
                f"scanner.labeller.counter_regime_confidence_haircut "
                f"must be in [0.0, 1.0]; got "
                f"{self.counter_regime_confidence_haircut}"
            )
        if not 0.0 <= self.extreme_sentiment_conviction_floor <= 1.0:
            raise ValueError(
                f"scanner.labeller.extreme_sentiment_conviction_floor "
                f"must be in [0.0, 1.0]; got "
                f"{self.extreme_sentiment_conviction_floor}"
            )


@dataclass
class ScannerSettings:
    """Market scanner configuration."""
    enabled: bool = True
    scan_interval_seconds: int = 300
    min_volume_24h: float = 50_000_000
    max_coins: int = 15
    max_spread_pct: float = 0.1
    # Phase 6 (corrected-Layer-1): composite opportunity scoring weights.
    scoring_weights: ScannerScoringWeights = field(
        default_factory=ScannerScoringWeights
    )
    # Phase 5 (Universe flapping fix). Hysteresis dampens rotation.
    hysteresis: ScannerHysteresisSettings = field(
        default_factory=ScannerHysteresisSettings
    )
    # Phase 5 (Universe flapping fix) — re-entry cooldown bumped from
    # the legacy hardcoded 300 s to a configurable 600 s default. A
    # coin removed from the active universe cannot re-enter for this
    # many seconds (force-included coins bypass).
    reentry_cooldown_seconds: int = 600
    # Layer 1 restructure Phase 5 — qualitative checklist before ranking.
    qualitative: ScannerQualitativeSettings = field(
        default_factory=ScannerQualitativeSettings
    )
    # Phase 4 of the 1D briefing rewrite — briefing-pipeline configuration.
    briefing: ScannerBriefingSettings = field(
        default_factory=ScannerBriefingSettings
    )
    # Issue 3 of 2026-05-19 direction-bias fix Phase B — state labeller
    # regime-haircut multiplier (replaces the 8 per-trigger regime
    # hard-kills with soft confidence multipliers).
    labeller: LabellerSettings = field(default_factory=LabellerSettings)
    # Phase 5 of the 1D briefing rewrite — pipeline mode flag.
    # "exclusion" = legacy 5-criterion gate (default; current production
    #               behavior preserved verbatim).
    # "briefing"  = new briefing-mode scanner: characterise every coin,
    #               score by interestingness, take top-N with soft floor,
    #               brain receives >=12 packages per cycle.
    # Phase 9 flips the default to "briefing" after the A/B harness
    # in Phase 8 confirms the briefing path produces sustained >=12
    # packages and trade rate stays within +/-20% of baseline.
    # Phase 9 cutover (2026-05-01): flipped default exclusion → briefing.
    # Legacy "exclusion" mode is preserved on the path; operators set
    # mode = "exclusion" in config.toml to roll back instantly.
    mode: str = "briefing"
    # Phase 8 of the 1D briefing rewrite — A/B harness flag.
    # "off"         = mode flag governs every cycle (default).
    # "alternating" = cycle-parity alternation: even-indexed cycles use
    #                 exclusion mode, odd-indexed cycles use briefing
    #                 mode. Index is the cycle_id's 5-min slot (e.g.
    #                 c-...-00:00 → 0 (exclusion), c-...-00:05 → 1
    #                 (briefing), c-...-00:10 → 2 (exclusion), ...).
    # Phase 9 cutover restores "off" (and flips mode default to
    # "briefing"). The harness is for measurement only — not a
    # production target.
    ab_mode: str = "off"

    def __post_init__(self) -> None:
        valid_modes = {"exclusion", "briefing"}
        if self.mode not in valid_modes:
            raise ValueError(
                f"scanner.mode must be one of {valid_modes}, "
                f"got {self.mode!r}"
            )
        valid_ab_modes = {"off", "alternating"}
        if self.ab_mode not in valid_ab_modes:
            raise ValueError(
                f"scanner.ab_mode must be one of {valid_ab_modes}, "
                f"got {self.ab_mode!r}"
            )


# Compiled once at module load for the UniverseSettings validator.
import re as _re

_UNIVERSE_SYMBOL_PATTERN = _re.compile(r"^[A-Z0-9]+USDT$")
_UNIVERSE_MIN_SIZE = 10


@dataclass
class UniverseRefreshSettings:
    """[universe.refresh] — the dynamic daily universe-refresh feature.

    Rebuilds the contents of the watch_list around coins in a genuine
    active phase, twice a day and on a confirmed manual Telegram press,
    selected on MULTI-DAY activity (never the last-24h move). This block
    holds every tunable value of that feature so nothing is hardcoded
    (implement-doc Rule 11). Disabled by default; the operator enables it
    at the Phase 5 decision gate.

    Selection is a two-pass hybrid (operator-approved, because the bulk
    ticker call carries only 24h figures): pass one liquidity-floors and
    coarse-ranks all ~582 coins from the single bulk call; pass two
    fetches multi-day daily candles (and open interest) only for the top
    ``shortlist_size`` survivors and computes the true multi-day score on
    them. ``stable_core_size`` is left at 0 (full rebuild) until decided
    at the Phase 5 gate.
    """

    enabled: bool = False
    # Scheduled refresh hours (UTC). 23:00 tunes the Asian session ahead;
    # 11:00 tunes Europe/US and finishes warm-up before US prime time.
    # Both sit clear of the 00:00/08:00/16:00 funding settlements.
    schedule_hours_utc: list[int] = field(default_factory=lambda: [23, 11])
    # Warm-up is data-gated (resumes early when the new coins pass the
    # existing freshness gates); this is only the safety ceiling.
    warmup_max_minutes: int = 60
    warmup_poll_seconds: int = 60
    target_universe_size: int = 50
    # Top survivors taken from pass one into the multi-day pass two.
    shortlist_size: int = 120
    # 0 = full rebuild each refresh; >0 = keep this many stable liquid
    # majors and rotate the rest (set at the Phase 5 gate).
    stable_core_size: int = 0
    # Liquidity floor (applied BEFORE scoring — untradeable movers removed).
    liquidity_floor_usd: float = 5_000_000.0
    max_spread_pct: float = 0.15
    min_price: float = 0.0001
    # Multi-day scoring (pass two).
    volatility_lookback_days: int = 7
    volatility_weight: float = 0.60
    volume_surge_weight: float = 0.25
    oi_weight: float = 0.15
    oi_enabled: bool = True
    # Directionality / whipsaw filter (net move over total travel, 0..1).
    # The STRICT floor is the bar for "tradeable": coins below it stay out
    # of the universe on a normal week. The universe is allowed to run
    # short rather than admit choppy coins that thrash and stop trades out.
    # Calibrated 2026-06-16 from a 14-day fill-rate replay: at 0.30 the
    # universe softened on 71% of days (too strict for this market); 0.20
    # (~10% net weekly move) keeps a real directionality bar while softening
    # only ~1 day in 14. Defaults are kept aligned to config.toml so a
    # dropped key cannot silently revert to the rejected 0.30.
    whipsaw_min_directionality: float = 0.20
    # The universe targets ``target_universe_size`` but may run short to
    # this minimum before any softening is considered. A clean short list
    # of trenders beats 50 padded with choppy coins. 24 keeps the universe
    # comfortably above the downstream scanner's 15-coin pick so the scanner
    # retains real discrimination.
    min_universe_size: int = 24
    # Last-resort floor: ONLY when fewer than ``min_universe_size`` coins
    # clear the strict floor (a genuinely trendless market) is the floor
    # softened to this, taking the least-choppy of the remainder just to
    # reach the minimum — and the day is flagged ``UNIVERSE_SOFTENED`` so
    # the operator knows that universe was compromised. Never the default.
    softened_min_directionality: float = 0.12
    # Quality guard: reject coins whose average daily range exceeds this
    # (thin parabolic pumps that cannot be exited cleanly); 0 disables.
    volatility_ceiling_pct: float = 60.0
    # Symbols never selected — tokenised commodities, index products, and
    # tokenised US equities/ETFs are not crypto movers. There is no clean
    # metadata flag distinguishing them from crypto on Bybit (same
    # contractType), so this is an explicit denylist that needs occasional
    # maintenance as new stock tokens are listed; the refresh logs the
    # selected 50, so any leaker surfaces and can be added here. Centralized
    # and tunable; empty list disables.
    exclude_symbols: list[str] = field(
        default_factory=lambda: [
            # Tokenised commodities and index products.
            "XAUUSDT", "XAGUSDT", "XAUTUSDT", "PAXGUSDT", "SPXUSDT",
            # Tokenised US equities / ETFs observed leaking into selection.
            "SOXLUSDT", "INTCUSDT", "EWYUSDT", "MRVLUSDT", "MUUSDT", "SNDKUSDT",
        ]
    )
    # Saturation points that normalise each raw factor onto 0..1.
    volatility_saturation_pct: float = 8.0
    volume_surge_saturation: float = 3.0
    oi_expansion_saturation_pct: float = 30.0

    def __post_init__(self) -> None:
        if self.enabled:
            for h in self.schedule_hours_utc:
                if not (0 <= int(h) <= 23):
                    raise ConfigError(
                        f"[universe.refresh] schedule_hours_utc entry out of range: {h}",
                        details={"hour": h},
                    )
        if self.target_universe_size < _UNIVERSE_MIN_SIZE:
            raise ConfigError(
                f"[universe.refresh] target_universe_size must be >= {_UNIVERSE_MIN_SIZE}",
                details={"target_universe_size": self.target_universe_size},
            )
        if self.shortlist_size < self.target_universe_size:
            raise ConfigError(
                "[universe.refresh] shortlist_size must be >= target_universe_size",
                details={
                    "shortlist_size": self.shortlist_size,
                    "target_universe_size": self.target_universe_size,
                },
            )
        if self.stable_core_size < 0 or self.stable_core_size >= self.target_universe_size:
            raise ConfigError(
                "[universe.refresh] stable_core_size must be in [0, target_universe_size)",
                details={"stable_core_size": self.stable_core_size},
            )
        _wsum = self.volatility_weight + self.volume_surge_weight + self.oi_weight
        if _wsum <= 0:
            raise ConfigError(
                "[universe.refresh] factor weights cannot all be zero",
                details={"weight_sum": _wsum},
            )
        if not (0.0 <= self.whipsaw_min_directionality <= 1.0):
            raise ConfigError(
                "[universe.refresh] whipsaw_min_directionality must be in [0, 1]",
                details={"whipsaw_min_directionality": self.whipsaw_min_directionality},
            )
        if not (0 < self.min_universe_size <= self.target_universe_size):
            raise ConfigError(
                "[universe.refresh] min_universe_size must be in (0, target_universe_size]",
                details={
                    "min_universe_size": self.min_universe_size,
                    "target_universe_size": self.target_universe_size,
                },
            )
        if not (0.0 <= self.softened_min_directionality <= self.whipsaw_min_directionality):
            raise ConfigError(
                "[universe.refresh] softened_min_directionality must be in "
                "[0, whipsaw_min_directionality]",
                details={
                    "softened_min_directionality": self.softened_min_directionality,
                    "whipsaw_min_directionality": self.whipsaw_min_directionality,
                },
            )
        if self.volatility_ceiling_pct < 0.0:
            raise ConfigError(
                "[universe.refresh] volatility_ceiling_pct must be >= 0 (0 disables)",
                details={"volatility_ceiling_pct": self.volatility_ceiling_pct},
            )
        # Saturation constants are bare divisors in the scorer — must be strictly
        # positive (and volume_surge > 1, since its denominator is sat - 1) or a
        # refresh would hit ZeroDivisionError / a degenerate normalization.
        if self.volatility_saturation_pct <= 0.0:
            raise ConfigError(
                "[universe.refresh] volatility_saturation_pct must be > 0",
                details={"volatility_saturation_pct": self.volatility_saturation_pct},
            )
        if self.volume_surge_saturation <= 1.0:
            raise ConfigError(
                "[universe.refresh] volume_surge_saturation must be > 1.0",
                details={"volume_surge_saturation": self.volume_surge_saturation},
            )
        if self.oi_expansion_saturation_pct <= 0.0:
            raise ConfigError(
                "[universe.refresh] oi_expansion_saturation_pct must be > 0",
                details={"oi_expansion_saturation_pct": self.oi_expansion_saturation_pct},
            )
        if self.volatility_lookback_days < 2:
            raise ConfigError(
                "[universe.refresh] volatility_lookback_days must be >= 2",
                details={"volatility_lookback_days": self.volatility_lookback_days},
            )
        # Warm-up and floor bounds (a 0 poll would busy-loop; negatives break math).
        if self.warmup_max_minutes < 0:
            raise ConfigError(
                "[universe.refresh] warmup_max_minutes must be >= 0",
                details={"warmup_max_minutes": self.warmup_max_minutes},
            )
        if self.warmup_poll_seconds < 1:
            raise ConfigError(
                "[universe.refresh] warmup_poll_seconds must be >= 1",
                details={"warmup_poll_seconds": self.warmup_poll_seconds},
            )
        if self.liquidity_floor_usd < 0.0 or self.min_price < 0.0 or self.max_spread_pct < 0.0:
            raise ConfigError(
                "[universe.refresh] liquidity_floor_usd, min_price, and max_spread_pct must be >= 0",
                details={"liquidity_floor_usd": self.liquidity_floor_usd,
                         "min_price": self.min_price, "max_spread_pct": self.max_spread_pct},
            )
        if self.enabled and not self.schedule_hours_utc:
            raise ConfigError(
                "[universe.refresh] schedule_hours_utc cannot be empty when enabled",
                details={"schedule_hours_utc": self.schedule_hours_utc},
            )


@dataclass
class UniverseSettings:
    """Layer 1 universe alignment — the manually-curated watch list.

    The single source of truth for "which coins is the system focused on?"
    Both the workers process (ScannerWorker filters its input set to this
    list) and the Shadow process (CoinSelector reads this list as the
    base of its WebSocket subscription set) consume it.

    Hard Rule 2 from the blueprint: open-position coins are always
    included by the consumers even if not in this list. The consumers,
    not this dataclass, enforce the union with open positions.

    Validation (enforced in ``__post_init__``):
        - watch_list non-empty
        - watch_list length >= ``_UNIVERSE_MIN_SIZE`` (10)
        - every watch_list entry matches ``^[A-Z0-9]+USDT$``
        - no duplicate watch_list entries
        - coin_aliases keys are watch_list members (no orphans)
        - coin_aliases values are non-empty list[str]
        - no alias collides with a different symbol's ticker / alias

    Computed at the end of ``__post_init__``:
        - ``extraction_map`` (lowercase alias -> watch_list symbol):
          auto-derives base-asset tickers from watch_list via
          ``extract_base_asset`` and overlays the optional
          ``[universe.coin_aliases]`` entries. Consumed by
          ``NewsService`` / ``RedditService`` for sentiment tagging.
    """

    watch_list: list[str] = field(
        default_factory=lambda: [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
            "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT",
        ]
    )
    # Optional full-name (or alternate-ticker) aliases per watch_list symbol,
    # consumed by news/sentiment extract_symbols(). Tickers are auto-derived
    # from watch_list and need NOT be re-listed here. See
    # ``[universe.coin_aliases]`` in config.toml for the operator-facing form.
    coin_aliases: dict[str, list[str]] = field(default_factory=dict)
    # [universe.refresh] — the dynamic daily universe-refresh feature's
    # tunables. Defaults are inert (enabled=False) so this is a no-op until
    # the operator turns it on at the Phase 5 gate.
    refresh: "UniverseRefreshSettings" = field(default_factory=UniverseRefreshSettings)
    # Computed in __post_init__ from watch_list (auto-tickers via
    # extract_base_asset) + coin_aliases. ``init=False`` keeps the field out
    # of the generated __init__ so callers cannot set it.
    extraction_map: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        # Fail-fast validation — workers refuse to start on bad config.
        # Operator gets a clear ConfigError telling them what to fix.
        if not self.watch_list:
            raise ConfigError(
                "[universe] watch_list cannot be empty",
                details={"watch_list_size": 0},
            )
        if len(self.watch_list) < _UNIVERSE_MIN_SIZE:
            raise ConfigError(
                f"[universe] watch_list must have at least {_UNIVERSE_MIN_SIZE} "
                f"entries; got {len(self.watch_list)}",
                details={"watch_list_size": len(self.watch_list)},
            )
        seen: set[str] = set()
        for sym in self.watch_list:
            if not isinstance(sym, str):
                raise ConfigError(
                    f"[universe] watch_list entry must be str, got {type(sym).__name__}: {sym!r}",
                    details={"bad_entry": repr(sym)},
                )
            if not _UNIVERSE_SYMBOL_PATTERN.match(sym):
                raise ConfigError(
                    f"[universe] watch_list entry {sym!r} does not match "
                    f"^[A-Z0-9]+USDT$ (must be uppercase USDT-quoted symbol)",
                    details={"bad_entry": sym},
                )
            if sym in seen:
                raise ConfigError(
                    f"[universe] watch_list contains duplicate entry: {sym!r}",
                    details={"duplicate": sym},
                )
            seen.add(sym)

        # Build the lowercase-keyed extraction_map used by NewsService and
        # RedditService to tag inbound articles. Steps:
        #   1) Auto-derive the base-asset ticker for each watch_list symbol
        #      via extract_base_asset (e.g. BTCUSDT -> "btc",
        #      1000PEPEUSDT -> "pepe").
        #   2) Layer in the optional coin_aliases entries (full names like
        #      "bitcoin", alternate spellings like "ether").
        # Collisions (one alias mapping to two different symbols) raise
        # ConfigError so operators catch typos at boot.
        from src.database.repositories.news_repo import extract_base_asset
        for sym in self.watch_list:
            ticker = extract_base_asset(sym)
            if ticker is None:
                # Symbol passed the watch_list regex but has a non-standard
                # quote-suffix (e.g. BTCUSD). Skip the auto-derivation; the
                # operator can supply an explicit alias if they want this
                # symbol matched in news copy.
                continue
            key = ticker.lower()
            existing = self.extraction_map.get(key)
            if existing and existing != sym:
                raise ConfigError(
                    f"[universe] watch_list ticker collision: {key!r} -> "
                    f"both {existing!r} and {sym!r}",
                    details={"ticker": key, "first": existing, "second": sym},
                )
            self.extraction_map[key] = sym
        for sym, aliases in self.coin_aliases.items():
            if sym not in seen:
                raise ConfigError(
                    f"[universe.coin_aliases] symbol {sym!r} is not in "
                    f"watch_list — remove the alias entry or add the symbol",
                    details={"orphan_symbol": sym},
                )
            if not isinstance(aliases, list):
                raise ConfigError(
                    f"[universe.coin_aliases] {sym!r} must be a list of strings",
                    details={"bad_value": repr(aliases)},
                )
            for alias in aliases:
                if not isinstance(alias, str) or not alias.strip():
                    raise ConfigError(
                        f"[universe.coin_aliases] {sym!r} contains "
                        f"non-string or empty alias: {alias!r}",
                        details={"symbol": sym, "bad_alias": repr(alias)},
                    )
                key = alias.strip().lower()
                existing = self.extraction_map.get(key)
                if existing and existing != sym:
                    raise ConfigError(
                        f"[universe.coin_aliases] alias {key!r} maps to both "
                        f"{existing!r} and {sym!r}",
                        details={"alias": key, "first": existing, "second": sym},
                    )
                self.extraction_map[key] = sym


@dataclass
class RegimeSettings:
    """Market regime detector configuration.

    Phase 3 (output-quality) — added ``hysteresis_count`` to expose the
    per-symbol confirm-N-readings logic as a config knob. Pre-fix it was
    hardcoded to 2 in ``src/strategies/regime.py:185``. Operators can
    now tune it via ``config.toml [regime]`` without redeploy:
    higher → more sticky regimes (fewer flips); lower → more responsive
    (potentially flapping).
    """
    detection_interval_seconds: int = 300
    primary_symbol: str = "BTCUSDT"
    trending_adx_threshold: float = 20.0
    ranging_adx_threshold: float = 20.0
    ranging_choppiness_threshold: float = 50.0
    # Issue #6 tiling fix (2026-05-27): explicit clean-trend choppiness
    # ceiling (was the hardcoded literal 45 in regime.py). Trending requires
    # choppiness below this; coins above it are tiled by their dominant
    # metric with a computed confidence, never a fabricated RANGING/0.40.
    trending_choppiness_max: float = 45.0
    volatile_atr_percentile: float = 70.0
    # Per-coin-authority Phase 0a (2026-05-29): the VOLATILE volume trigger was
    # the hardcoded literal ``2.0`` in regime.py. Exposed as config so it is
    # tunable without redeploy. A coin is VOLATILE-by-volume when its
    # volume_sma_ratio exceeds this (evaluated AFTER trend/range/dead structure
    # now, per the 0a re-ordering so a choppy coin is RANGING, not VOLATILE).
    volatile_volume_ratio: float = 2.0
    dead_adx_threshold: float = 12.0
    dead_volume_ratio: float = 0.5
    hysteresis_count: int = 2
    # Per-coin-authority Phase 5 (2026-05-29): the breadth RISK/SIZING brake —
    # the ONLY sanctioned survivor of the "global" concept. Derived from the
    # per-coin regime distribution (NOT a single coin). It shrinks position size
    # when the whole universe is directionally lopsided (high correlation ->
    # systemic risk). It NEVER sets direction or selects a strategy roster.
    # Graduated: size_mult = 1.0 while the dominant-direction share <= start,
    # then linearly down to floor as that share -> 1.0. Operator decision:
    # graduated, live.
    breadth_brake_enabled: bool = True
    breadth_brake_start: float = 0.60   # Issue 2.4: engage sooner (was 0.65)
    breadth_brake_floor: float = 0.40   # Issue 2.4: cut harder at full one-sidedness (was 0.50)
    breadth_brake_min_coins: int = 10   # need >= this many classified coins to judge breadth

    def __post_init__(self) -> None:
        if self.hysteresis_count < 1:
            raise ValueError(
                f"regime.hysteresis_count must be >= 1, got {self.hysteresis_count}"
            )


@dataclass
class StrategyEngineSettings:
    """4-layer strategy engine configuration.

    Definitive-fix Phase 12 (2026-04-28) — added ensemble-voter
    diagnostics (``vote_trace_enabled``) and a togglable single-strategy
    cap (``single_strategy_max_share``) so a dominant strategy cannot
    force a STRONG consensus on its own. Live observation of
    STRAT_VOTE_TRACE drives whether the cap should bind.
    """
    scan_interval_seconds: int = 60
    min_score_threshold: float = 70.0
    min_ensemble_agreement: float = 2.5
    max_ensemble_opposition: float = 2.5
    # Issue #18 + E15 fix (2026-05-28) — the GOOD floor above and the STRONG
    # floor below now form a CORRECT ladder: STRONG requires MORE agreement
    # (4.0 > GOOD 2.5) and LESS opposition (1.5 < GOOD 2.5) than GOOD. Before
    # this fix the code defaults were INVERTED (GOOD floor 5.0/1.0 was set
    # STRICTER than STRONG 4.0/1.5), so a vote of agreeing=4.0/opposing=1.5
    # classified STRONG even though it failed the GOOD floor. Live config.toml
    # [strategy_engine] already rescued this at runtime (GOOD 2.5/2.5), so
    # correcting the code defaults to match leaves runtime BYTE-IDENTICAL while
    # making a config-less deploy or a config regression safe. The loader
    # fallbacks (_build_strategy_engine) and the EnsembleStateCache defaults
    # carry the same corrected values, and the EnsembleVoter boot self-check
    # now AUTO-CORRECTS (clamps STRONG to be at least as strict as GOOD and logs
    # BOOT_ENSEMBLE_THRESHOLDS_AUTOCORRECTED) instead of a silent warning, so a
    # future re-inversion can never silently mislabel a consensus tier.
    min_ensemble_agreement_strong: float = 4.0
    max_ensemble_opposition_strong: float = 1.5
    # Layer 1 Defect 1 (2026-05-21) — controls whether
    # StrategyRegistry.get_active_for_regime honors its regime
    # argument. Default True per operator decision: the function
    # filters strategies by REGIME_ACTIVE_CATEGORIES so momentum
    # strategies stop voting in ranging/dead regimes, contrarian
    # strategies stop voting in trending regimes, etc. Set False
    # to restore the pre-Defect-1 uniform-strategy behavior (every
    # enabled strategy votes regardless of regime) as an emergency
    # rollback. Each call emits a REGISTRY_REGIME_FILTER log so the
    # contract is auditable per regime.
    strategy_regime_filter_enabled: bool = True
    # Layer 3 (2026-05-22) — regime-conditional, data-derived per-strategy
    # weighting. Operator approved at design gate:
    #   regime_weighting_enabled — the live flag. Default False = SHADOW
    #     mode: the deriver computes per-(strategy, regime) factors and
    #     ensemble.vote logs both the live (equal-weight) consensus and
    #     the shadow (regime-weighted) consensus per cycle, but the LIVE
    #     consensus drives trading unchanged. Flip True only after the
    #     shadow trial validates the change. Instant rollback by flipping
    #     False with no code change.
    #   regime_weighting_cold_start_n — minimum supporting trades per
    #     (strategy, regime) cell before deriving a data-driven factor.
    #     Below this, the cell stays at factor=1.0 (equal-weight) — Rule
    #     5: "diverge from equal only as evidence accumulates". At ship
    #     time ALL cells are below threshold so behaviour is byte-equal
    #     to today; maturation is organic as Layer 2 data flows in.
    #   regime_weighting_floor / _ceil — the factor is multiplicatively
    #     bounded so no strategy is ever silenced (floor > 0) and no
    #     single strategy dominates (ceil < infinity). The factor is
    #     applied as: weight_used = perf.ensemble_weight * factor.
    #   regime_weighting_sensitivity — controls how steeply
    #     supporting_avg_pnl_pct maps into a factor:
    #     factor = clamp(1.0 + sensitivity * avg_pnl, floor, ceil).
    #     0.3 means a +3% avg PnL strategy gets factor ~1.9 in that
    #     regime; a -3% strategy gets factor 0.1 → clamped to floor.
    #   regime_weighting_ema_alpha — smoothing across recomputes so a
    #     single bad cycle doesn't whipsaw weights:
    #     new = alpha * computed + (1-alpha) * previous.
    regime_weighting_enabled: bool = False
    regime_weighting_cold_start_n: int = 20
    regime_weighting_floor: float = 0.3
    regime_weighting_ceil: float = 3.0
    regime_weighting_sensitivity: float = 0.3
    regime_weighting_ema_alpha: float = 0.3
    # Layer 4 (2026-05-22) — Consensus-Truth Fix. Surfaces a brief
    # "Consensus Context" note in the CALL_A prompt so the brain sees
    # the verified historical reality that broad strategy agreement
    # (5+) has tended to mark crowded/late entries with lower per-
    # trade edge than narrower (3-4) agreement. The brain remains the
    # sizer; this only informs its perception of consensus. Default
    # True ships the truthful framing; flip False as instant rollback
    # if undesired sizing effects appear post-restart. See
    # IMPLEMENT_LAYER4_CONSENSUS_TRUTH.md A.4: this is a truth-fix,
    # not a blocking fix — no code path uses this flag to mutate size.
    brain_prompt_l4_consensus_context_enabled: bool = True
    max_setups_to_brain: int = 3
    max_brain_calls_per_hour: int = 12
    # Candidate-Block Data Integrity Fix — Issue 2 (2026-06-09) — Layer 1
    # input-gate thresholds, centralized (were hardcoded inline in the strategy
    # worker's TA loop). kline_max_age_seconds: a symbol whose newest kline is
    # older than this is skipped before TA (stale candles vote with the past).
    # min_kline_count: a symbol with fewer candles than this is dropped from the
    # Layer 1 candle map (insufficient history for the indicators). Both keep
    # their prior hardcoded defaults; centralizing them lets operators tune the
    # stale/short-history tolerance without a code change and makes the silent
    # too-few-candles drop observable (STRAT_SKIP_KLINE_COUNT).
    kline_max_age_seconds: float = 300.0
    min_kline_count: int = 50
    # Candidate-Block Data Integrity Fix — Issue 5 (2026-06-09) — TradeScorer
    # grade composition, centralized (the cutoffs were hardcoded in scorer.py).
    # The grade is a threshold on total = base + confluence + context + quality;
    # a low quality sub-score can still reach a top grade when the other three
    # are near-maxed (e.g. BSB total=84 grade=A+ on quality=7/20).
    #   grade_threshold_*: the total cutoffs for A+/A/B/C (same defaults as the
    #     prior hardcoded 80/68/56/45).
    #   grade_quality_floor: quality sub-score (out of 20) below which the setup
    #     is treated as low-quality — drives the always-on candidate-block
    #     annotation and, when the cap is enabled, the grade cap. Set to 0 to
    #     disable both the annotation and the cap.
    #   grade_quality_cap_enabled: when True, a setup with quality below the
    #     floor cannot CARRY a grade above grade_quality_cap_max_grade — the
    #     canonical grade is lowered so neither the brain nor any grade-driven
    #     sizing is misled. Default False (behaviour-changing lever; the
    #     annotation surfaces the weakness regardless).
    #   grade_quality_cap_max_grade: the ceiling grade applied when capping.
    grade_threshold_a_plus: int = 80
    grade_threshold_a: int = 68
    grade_threshold_b: int = 56
    grade_threshold_c: int = 45
    grade_quality_floor: float = 10.0
    grade_quality_cap_enabled: bool = False
    grade_quality_cap_max_grade: str = "B"
    # Definitive-fix Phase 12 — when True, EnsembleVoter emits a
    # per-coin STRAT_VOTE_TRACE log enumerating each contributing
    # strategy's (name, vote, confidence, weight) for STRONG-classified
    # coins. Cheap (one log line per STRONG coin per cycle) and
    # togglable in case operators want to silence it.
    vote_trace_enabled: bool = True
    # Definitive-fix Phase 12 — fraction of ``agreeing`` votes any
    # single strategy is allowed to contribute. Above this, the
    # offending strategy's contribution is capped so consensus
    # promotion to STRONG depends on multiple independent strategies.
    # Default 1.0 = no cap (preserves current behaviour); set to e.g.
    # 0.4 to require at least three strategies for STRONG.
    single_strategy_max_share: float = 1.0

    # P2 entry-direction fix (2026-06-04) — two-sided ensemble vote. The
    # legacy ensemble polls every strategy with only the originator's
    # direction; the per-strategy vote() methods confirm the asked
    # direction or return NEUTRAL but never oppose, so the opposing side
    # tallies zero even when strong opposing signals exist. When True,
    # EnsembleVoter.vote runs a SECOND poll with the opposite direction
    # and records the honest opposing weighted sum on
    # EnsembleResult.opposing_votes, surfaced to the brain so it weighs a
    # real Buy-vs-Sell contest. Does NOT change the consensus
    # classification, size, or the per-symbol vote cache (the watchdog
    # reads the legacy tally unchanged). False = byte-identical legacy.
    ensemble_two_sided_vote: bool = False


@dataclass
class PnLTargetSettings:
    """Daily PnL target and risk scaling."""
    daily_target_pct: float = 5.0
    protect_threshold_pct: float = 3.0
    caution_threshold_pct: float = -1.0
    survival_threshold_pct: float = -3.0
    halt_threshold_pct: float = -5.0


@dataclass
class LeverageSettings:
    """Smart leverage configuration."""
    max_leverage: int = 5
    tier_1_max: int = 5
    tier_2_max: int = 4
    tier_3_max: int = 3
    volatile_max: int = 3
    dead_max: int = 2
    min_confidence_for_5x: float = 0.85
    min_confidence_for_4x: float = 0.75


@dataclass
class OptimizerSettings:
    """Weekly adaptive optimizer configuration."""
    enabled: bool = True
    run_day: str = "sunday"
    run_hour_utc: int = 0
    weight_adjustment_pct: float = 10.0
    max_param_change_pct: float = 20.0
    min_trades_for_optimization: int = 20
    underperform_threshold_pct: float = 10.0
    disable_after_weeks: int = 3


@dataclass
class FactorySettings:
    """Strategy Factory configuration."""
    enabled: bool = True
    discovery_schedule_hour_utc: int = 2
    discovery_lookback_days: int = 30
    min_pattern_occurrences: int = 20
    min_win_rate: float = 0.55
    min_profit_factor: float = 1.2
    min_statistical_significance: float = 0.05
    max_strategies_per_batch: int = 5
    max_generation_retries: int = 3
    generation_cost_limit_usd: float = 0.20
    live_monitor_interval_seconds: int = 300
    hot_pattern_threshold_win_rate: float = 0.70
    hot_pattern_threshold_occurrences: int = 5
    emergency_generation_enabled: bool = True


@dataclass
class BacktestSettings:
    """Backtesting engine configuration."""
    initial_capital: float = 10000.0
    default_leverage: int = 3
    commission_pct: float = 0.06
    slippage_pct: float = 0.02
    funding_rate_pct: float = 0.01
    walk_forward_enabled: bool = True
    train_pct: float = 0.70
    monte_carlo_runs: int = 1000
    min_trades_to_pass: int = 30
    min_win_rate: float = 0.52
    min_profit_factor: float = 1.3
    max_drawdown_pct: float = 15.0
    min_sharpe: float = 0.5
    min_walk_forward_efficiency: float = 0.5
    max_ruin_probability: float = 0.05


@dataclass
class TrialSettings:
    """Paper trading trial configuration."""
    trial_duration_days: int = 14
    max_extensions: int = 1
    extension_duration_days: int = 7
    trial_position_size_pct: float = 25.0
    min_trades_for_evaluation: int = 10
    promotion_min_win_rate: float = 0.50
    promotion_min_pnl: float = 0.0
    promotion_max_drawdown: float = 10.0
    max_active_strategies: int = 60
    demotion_underperform_weeks: int = 2
    demotion_win_rate_drop_pct: float = 15.0
    quarterly_revival_enabled: bool = True


@dataclass
class PortfolioSettings:
    """Portfolio optimizer configuration."""
    enabled: bool = True
    optimization_day: str = "sunday"
    optimization_hour_utc: int = 0
    kelly_fraction: float = 0.25
    min_trades_for_kelly: int = 20
    max_strategy_allocation_pct: float = 10.0
    min_strategy_allocation_pct: float = 1.0
    proven_strategies_budget_pct: float = 55.0
    ai_strategies_budget_pct: float = 30.0
    trial_strategies_budget_pct: float = 10.0
    cash_reserve_pct: float = 5.0
    correlation_lookback_days: int = 30
    high_correlation_threshold: float = 0.7
    daily_risk_budget_pct: float = 5.0
    drawdown_reduction_threshold_1: float = 5.0
    drawdown_reduction_factor_1: float = 0.7
    drawdown_reduction_threshold_2: float = 10.0
    drawdown_reduction_factor_2: float = 0.4
    kelly_weight: float = 0.30
    mean_variance_weight: float = 0.40
    risk_parity_weight: float = 0.30
    min_rebalance_change_pct: float = 2.0
    stress_test_enabled: bool = True


@dataclass
class TelegramInteractiveSettings:
    """Interactive Telegram bot configuration."""
    enabled: bool = True
    ai_responses_enabled: bool = True
    max_ai_calls_per_hour: int = 20
    trade_confirmation_required: bool = True
    morning_briefing_enabled: bool = True
    morning_briefing_hour_utc: int = 5
    price_alert_check_interval: int = 10


@dataclass
class MCPSettings:
    """MCP server transport configuration."""
    transport: str = "stdio"
    sse_host: str = "0.0.0.0"
    sse_port: int = 8080
    sse_auth_required: bool = True
    server_name: str = "trading-intelligence"
    server_version: str = "0.1.0"
    auth_token: str = ""


@dataclass
class EnforcerSettings:
    """Enforcer v2 — PnL-Based Intelligent Throttling."""
    enabled: bool = True
    check_interval_seconds: int = 300

    # PnL-based thresholds (daily PnL %)
    # Phase 4 of dir-block-fix (2026-05-05): raised thresholds (was
    # -2.0 / -5.0) so the enforcer no longer treats normal daily noise
    # as a defensive event. Aligned with the operator's
    # aggressive-exploitation philosophy.
    pnl_caution_pct: float = -3.0       # Below this → el=1 (capital preservation)
    # CALL_B Framing Fix Phase 2A (2026-05-06): operator decided to raise
    # SURVIVAL trigger from -7.0 to -12.0 so RR=2.5 trades stop being
    # blocked by the survival_block gate during normal recovery drawdown.
    # HALTED at -15.0 preserves a real emergency stop above SURVIVAL.
    pnl_survival_pct: float = -12.0     # Below this → el=2 (survival)
    pnl_halted_pct: float = -15.0       # Below this → el=3 (halted — no new trades)

    # Size reduction for mild negative PnL
    size_reduction_enabled: bool = True   # Toggle size reduction on/off
    size_reduction_at_pnl_pct: float = 0.0  # Start reducing below this PnL %
    size_reduction_factor: float = 0.75  # Multiplier when PnL is between 0% and caution

    # Streak as secondary signal (only when PnL is negative)
    # Phase 4 of dir-block-fix (2026-05-05): raised -5 → -8 so the
    # streak path no longer elevates level on a 5-loss-streak alone.
    # The streak path also now requires pnl < streak_boost_pnl_floor_pct
    # (default -1.0): two losses on a +0.10 % day no longer trigger el=1.
    streak_boost_threshold: int = -8     # 8-loss streak + meaningfully negative PnL → el=1
    streak_boost_pnl_floor_pct: float = -1.0   # streak path inactive when pnl >= this

    # Auto-recovery
    max_enforcement_minutes: int = 45    # Auto-recover after stuck at el>=1 for this long
    grace_period_minutes: int = 30       # Manual reset grace period (full skip)

    # Per-level restrictions (configurable)
    level_1_max_positions: int = 3
    level_1_max_leverage: int = 3
    level_1_min_score: int = 75
    level_2_max_positions: int = 2
    level_2_max_leverage: int = 3
    level_2_min_score: int = 80
    level_2_min_confluence: int = 7
    level_2_min_rr: float = 3.0

    # Legacy fields (kept for backward compatibility with config.toml)
    decay_minutes: int = 60
    min_trades_per_hour: int = 50
    min_profit_per_hour_pct: float = 10.0
    min_win_rate: float = 0.55
    min_signals_per_hour: int = 100
    min_setups_to_brain_per_hour: int = 20
    max_seconds_between_trades: int = 180
    max_escalation_level: int = 5
    force_trade_on_gap: bool = True
    rewards_enabled: bool = True
    hourly_report_enabled: bool = True


# CALL_B Framing Fix Phase 5B (2026-05-06) — sentiment consumption gate.
# Per operator decision: disable consumption (do not remove the code).
# Reddit is config-disabled, Finnhub free tier has no altcoin coverage,
# so the sentiment subsystem is load-bearing only ~3% of the time. The
# flag turns SignalGenerator's sentiment weighted-sum branch into a
# no-op and silences the per-coin SENT_DEGRADED_MODE log spam. The
# code stays in tree so the operator can re-enable when the data flow
# is restored in a future session.
@dataclass
class SentimentSettings:
    """Sentiment subsystem consumption gate."""
    # When False (default), SignalGenerator skips the sentiment branch
    # in its multi-source classifier, the SentimentAggregator suppresses
    # its per-coin SENT_DEGRADED_MODE log, and a boot-time
    # SENT_CONSUMPTION_DISABLED event fires once. The strategy scorers
    # continue to read sentiment_data when callers pass it; missing
    # data is already handled gracefully (`.get(..., 0)` defaults).
    consumption_enabled: bool = False


@dataclass
class Mode4Settings:
    """Mode 4 ProfitSniper — institutional-grade profit protection (Phase 1-10)."""

    enabled: bool = True
    check_interval_seconds: int = 5

    # Ring Buffer (Phase 1)
    buffer_max_size: int = 720          # 60 minutes at 5s intervals
    buffer_min_ready: int = 100         # Minimum points for model validity (8+ min)

    # Trailing System (Phase 8)
    base_atr_multiplier: float = 2.5    # Chandelier Exit base width in ATR units
    trail_min_change_pct: float = 0.1   # Min SL change % to avoid Shadow flooding

    # Regime trail factors (Phase 8) — must match REGIME_TRAIL_FACTORS constant
    regime_factor_trending: float = 1.3
    regime_factor_ranging: float = 0.7
    regime_factor_volatile: float = 1.0
    regime_factor_dead: float = 0.6

    # Anti-Greed (Phase 9) — pullback backstop
    anti_greed_enabled: bool = True
    anti_greed_pullback_40_min_peak: float = 2.0   # Min peak % for 40% pullback → tighten
    anti_greed_pullback_60_min_peak: float = 3.0   # Min peak % for 60% pullback → partial
    anti_greed_pullback_75_min_peak: float = 5.0   # Min peak % for 75% pullback → full close

    # Action cooldowns (Phase 9)
    # Phase 2 of dir-block-fix (2026-05-05): lowered 30 → 15 so M4 can
    # react to large pullbacks inside the same anti-greed window.
    tighten_cooldown_seconds: int = 15
    partial_close_cooldown_seconds: int = 120
    partial_close_pct: int = 50         # % of position to close on partial action

    # Sniper partial-close disable (IMPLEMENT_PNL_TRUTH_AND_DISABLE_OVERTIGHTENING,
    # 2026-05-26). Operator decision: stop the profit sniper from partial-
    # closing positions. When False (operator default), the sniper never
    # reduces a position — score / greed / stall-escape "partial_close"
    # actions are downgraded to a trailing-stop tighten (winner protection
    # preserved; no reduce-only fill, no fee, no winner-clip). The winner
    # trail, full_close, deadline/time-decay, sentinel advisor, hard stop,
    # native exchange stop, and SL gateway are all untouched. Set True to
    # restore the legacy partial-close behaviour.
    sniper_partial_close_enabled: bool = False

    # Phase 4 (Sniper-loop fix) — type-agnostic per-position cooldowns.
    # The legacy ``partial_close_cooldown_seconds`` only blocked the
    # NEXT partial when the IMMEDIATELY-prior action was also a partial
    # — alternating tighten ↔ partial defeated it (INJUSDT 21:48 bug).
    # ``min_seconds_between_actions`` enforces a per-position cooldown
    # that ANY M4 action of any type starts; ``min_seconds_before_close``
    # gives the position time to recover before the score branch can
    # full_close (anti-greed backstop bypasses by design).
    min_seconds_between_actions: int = 60
    min_seconds_before_close: int = 180

    # Phase 4 (Sniper-loop fix) — PROFIT GATE on partials. The legacy
    # ``min_profit_for_close`` only gates full_close. Without a partial
    # gate, the score branch could fire partial_close on a position
    # that had just gone red, locking in losses. Default 0.0 (require
    # break-even before any partial fires); raise to e.g. 0.5 to
    # require 0.5% profit before partials are allowed.
    min_profit_for_partial_pct: float = 0.0

    # Phase 9 (P1-8 Sniper Stall Escape) — escalate when actionable=True
    # but action="hold" persists for many ticks. Resets on any non-stall
    # tick. 0 disables the escalation (kill switch). Brief defaults.
    # Layer 4 Realignment Phase 1B (2026-05-06): recalibrated
    # 20→120 (partial) and 40→180 (full) to match the operator's
    # 10-30 minute hold strategy. Old values killed positions in
    # the first 1:40-3:20 of life — well below the 10-min strategy
    # minimum. New values preserve a 60-tick (5-min) grace gap
    # between partial-close emission and forced full-close.
    stall_escape_partial_after_ticks: int = 120
    stall_escape_full_after_ticks: int = 180

    # Phase 4A session-stability fix — de-escalate after stall emission.
    # Without these, ``_stall_escape_action`` returned "partial_close" on
    # every tick for as long as the stall persisted, so the
    # ``PARTIAL_CLOSE_UNSUPPORTED`` warning fired ~20 times per position
    # (observed on MOVRUSDT, 2026-04-24 session).
    #
    # Once an escape action (partial_close / full_close) is emitted, the
    # stall method will not emit another for ``stall_escape_cooldown_seconds``.
    # After ``stall_tighten_max_applications`` downgraded tighten_agg
    # applications (Shadow has no partial-close today) without PnL
    # recovery of at least ``stall_recovery_threshold_pct`` from the
    # worst-observed PnL, the method escalates straight to full_close.
    stall_escape_cooldown_seconds: int = 30
    stall_tighten_max_applications: int = 3
    stall_recovery_threshold_pct: float = 0.15

    # Definitive-fix Phase 10 (2026-04-28) — lifetime cap on partial
    # emissions per position. Forensic S6 captured 5 ladder steps in
    # 1:49-1:54 because cooldown elapsed and partial fired again,
    # salami-slicing the position to nothing. Once the per-position
    # budget is spent the next stall escape becomes ``full_close``
    # instead of another partial. Reset on the next ``_on_position_opened``
    # (one position lifetime = one budget).
    # Layer 4 Realignment Phase 1D (2026-05-06): default raised 1 → 3
    # to give positions room to recover via multiple partial closes
    # before forced full close. With Phase 1B's 60-tick (5-min) grace
    # gap between partial and full, three partials provide ~15 minutes
    # of recovery opportunity before the position is forcibly exited.
    max_partials_per_position: int = 3

    # Sniper-Latency-Size Fix Phase 1 (2026-05-07) — enforced grace gap
    # between stall-escape emissions. The 30-second blanket cooldown
    # (``stall_escape_cooldown_seconds``) was the only inter-escape gate
    # before this fix, producing 5-6 tick (25-30 sec) gaps between
    # ladder steps despite the documented "60-tick (5-min) grace gap"
    # design intent. These tick-count gates restore the recovery window:
    # after a partial emission the position cannot be partial-closed
    # again or full-closed (from the partial-cap path) until the gap
    # elapses. The forced-full path (``ticks > stall_escape_full_after_ticks``)
    # is the mature-stall safety valve and bypasses these gates.
    partial_to_full_grace_ticks: int = 60
    partial_to_partial_grace_ticks: int = 60

    # Logging / DB write throttle (Phase 10)
    log_every_n_ticks: int = 6          # M4_EVAL log every 30s (6 × 5s)
    log_always_above_score: int = 50    # Always log if composite score >= this
    sniper_log_write_every_n_ticks: int = 6  # DB write every 30s minimum

    # Legacy classification thresholds — used by _classify_score() for M7 labels
    score_watch: int = 30
    score_consult_claude: int = 50
    score_auto_partial: int = 70
    score_auto_full: int = 85

    # Legacy profit/immunity filters — used by _classify_score()
    min_profit_pct: float = 0.8
    min_profit_for_action: float = 0.10  # Min PnL% before Mode4 Phase 9 takes any action
    min_profit_for_close: float = 0.50  # Min PnL% before P9 can full_close (prevents killing tiny winners)
    profit_immunity_seconds: int = 60
    loss_immunity_seconds: int = 30
    full_rules_after_seconds: int = 300

    # Legacy cooldowns — used by is_in_cooldown() / _is_safe_to_execute()
    cooldown_extreme_seconds: int = 300
    cooldown_strong_seconds: int = 180
    cooldown_medium_seconds: int = 120

    # Legacy Claude settings — kept for _consult_claude() method
    claude_timeout_seconds: int = 15
    max_claude_queries_per_hour: int = 10
    claude_hold_recheck_seconds: int = 30

    # Legacy model weights — used for z_pts/vel_pts in last_score snapshot (M7)
    weight_zscore: int = 25
    weight_velocity: int = 25
    weight_volume: int = 20
    weight_bollinger: int = 15
    weight_momentum: int = 15

    # Legacy flash crash protection
    flash_crash_auto_score: int = 70

    # TRADE LIBERATION: Trail distance floors + activation threshold
    min_trail_atr_multiplier: float = 1.5   # Min trail = this × ATR (noise floor)
    min_trail_pct: float = 0.30             # Min trail as % of entry price (absolute floor)
    # Phase 2 of dir-block-fix (2026-05-05): raised 0.30 → 0.50 so the
    # trail does not begin tightening until the position is meaningfully
    # in profit, addressing the 65 % peak-give-back baseline.
    # DEPRECATED (PF/LC Top-15 Problem 2.4) — the Profit-Fetching trail now reads
    # profit_fetching.min_profit_for_trail_pct (aligned to the ladder arm). Kept
    # so any legacy reader does not NameError; no longer consulted by the sniper.
    min_profit_for_trail_pct: float = 0.50  # Min peak PnL% before trail activates
    min_profit_decay: float = 0.50          # Floor for profit_decay factor

    # T1-3 (2026-05-12) — Trail floor from CURRENT price (mean-reversion guard).
    # The min_trail_atr_multiplier / min_trail_pct above bound only the
    # from-PEAK distance. As current price oscillates BELOW peak (mean
    # reversion), sl_dist_to_cur shrinks toward zero independently —
    # gateway R2 is then the only safety net, and on low-vol coins R2's
    # effective floor collapses to 0.05 % leaving the trail vulnerable
    # to noise stop-outs.
    # Empirical bug (2026-05-12 logs): ARBUSDT and SKRUSDT trail
    # ratcheted to sl_dist ≈ 0.15 %; mean-reversion 0.13 % noise
    # stopped both out (-$2.35 each, -$4.70 total in 70 s).
    # Formula in profit_sniper._apply_trail_stop:
    #     floor_pct = clamp(max(min_pct, atr_5m_pct * atr_mult),
    #                       upper=max_pct)
    # Behaviour: CLAMP outward (preserve trail intent) — never reject.
    # Subcase: clamp would loosen prior cur_sl → reject explicitly with
    # action=reject_would_loosen so Bug-2 / R1 tighten-only contract is
    # preserved.
    # Defaults: atr_mult=0.75 (above noise band ~0.13 %, well below
    # entry SL ~0.9 %), min_pct=0.20 (smallest floor that would have
    # prevented every documented bug trip; deliberately below the
    # peak-distance floor 0.30 so the two floors compose), max_pct=1.50
    # (preserves trail's room to walk on extreme-vol coins).
    trail_floor_from_price_atr_multiplier: float = 0.75
    trail_floor_from_price_min_pct: float = 0.20
    trail_floor_from_price_max_pct: float = 1.50


@dataclass
class Layer4SniperSettings:
    """Layer 4 Realignment (2026-05-06) — Profit Sniper protection knobs.

    Knobs added by the Layer 4 Comprehensive Realignment fix to align the
    Profit Sniper with the operator's aggressive-exploitation philosophy.
    These do NOT replace Mode4Settings — they ADD a thin layer of
    protection in front of the existing stall-escape decision so fresh
    trades, profitable trades, and developing trades are not killed
    before they have a chance to resolve.

    Phase 1A (this dataclass field) — minimum-age guardrail. Mirrors
    ``watchdog.strategic_action_min_hold_seconds`` (300 s) and
    ``time_decay.min_age_seconds`` (300 s) so the sniper is held to the
    same settling contract as the other Layer 4 paths. The sniper's
    stall counter does not advance on positions younger than
    ``min_age_seconds`` — those positions cannot be force-closed by the
    sniper.

    Phase 1C (added in a later sub-phase commit) — PnL-aware stall
    escape. ``profit_protection_threshold`` and
    ``development_window_lower`` block the stall counter from emitting a
    partial / full close when the position is profitable or in the
    normal-development loss window. Defaults: profit > 0.0 or loss in
    the (-0.3 %, 0.0 %) window blocks the sniper.

    All values are configured under ``[layer4.sniper]`` in
    ``config.toml`` and consumed by ``ProfitSniper._stall_escape_action``.
    """

    # Phase 1A — minimum-age guardrail.
    min_age_seconds: float = 300.0

    # Phase 1C — PnL-aware stall escape (default values applied when the
    # later sub-phase ships; harmless defaults during 1A).
    profit_protection_threshold: float = 0.0   # Block stall escape when pnl > this
    development_window_lower: float = -0.3     # Block stall escape when pnl > this (still developing)

    # Issue C fix Phase 3b (2026-05-08) — peak-protected stall extension.
    # Positions that briefly touched a meaningful peak deserve more time
    # under the mature-stall valve before forced full close. The
    # 13:00–16:00 UTC 2026-05-08 audit showed 4 stall-valve closures of
    # which 2 (INJUSDT peak +0.30 %, ARBUSDT peak +0.13 %) had clearly
    # demonstrated edge before reverting and would have been spared by
    # this extension. SANDUSDT (peak +0.06 %) and HYPERUSDT (peak 0 %)
    # are below the default threshold and continue to be killed by the
    # base ``Mode4Settings.stall_escape_full_after_ticks`` (default 40)
    # — preserving runaway-loss protection. Setting
    # ``peak_protection_threshold_pct <= 0`` disables the extension
    # (kill-switch). Setting it > 100 disables effectively (no position
    # ever qualifies). Reading site:
    # ``ProfitSniper._stall_escape_action`` mature-stall branch.
    peak_protection_threshold_pct: float = 0.10
    peak_protected_full_after_ticks: int = 80

    # Issue C fix Phase 3c (2026-05-08) — recovering-PnL gate. The
    # mature-stall valve fires only when the position is NOT recovering
    # from its worst observed PnL. ``recovering_threshold_pct`` is the
    # minimum delta (current_pnl - worst_pnl) required to count as
    # "recovering". A position that has rebounded by at least this much
    # from the worst point is given another tick — the valve will
    # re-evaluate next tick. Set <= 0 to disable the recovery check
    # entirely (kill-switch).
    recovering_threshold_pct: float = 0.10


@dataclass
class FundManagerSettings:
    """Intelligent Fund Manager configuration."""
    enabled: bool = True
    check_interval_seconds: int = 60
    starting_unlock_pct: float = 20.0
    active_pool_pct: float = 70.0
    aplus_reserve_pct: float = 20.0
    emergency_reserve_pct: float = 10.0
    profit_lock_pct: float = 50.0
    trade_profit_lock_pct: float = 25.0
    max_correlation_bucket_pct: float = 30.0
    min_profitable_trade_fee_pct: float = 0.12

    # Phase 5 (post-Layer-1 fix). FundReconciler worker reconciles local
    # fund_manager state against Bybit's authoritative wallet view every
    # ``reconcile_interval_seconds`` seconds. Drift greater than the
    # alert threshold emits FUND_RECONCILE_DRIFT (WARNING) + Telegram
    # alert. ``reconcile_auto_correct`` is OFF by default — auto-applying
    # exchange state to local would need explicit operator opt-in to be
    # auditable.
    reconcile_enabled: bool = True
    reconcile_interval_seconds: int = 60
    reconcile_drift_alert_threshold_pct: float = 5.0
    reconcile_auto_correct: bool = False


@dataclass
class TIASSettings:
    """TIAS Phase 2 — DeepSeek post-trade analysis via OpenRouter."""
    enabled: bool = False
    api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    primary_model: str = "deepseek/deepseek-chat-v3-0324"
    fallback_model: str = "deepseek/deepseek-chat"
    temperature: float = 0.3
    max_tokens: int = 1500
    timeout_seconds: int = 45
    max_retries: int = 1
    http_referer: str = "https://github.com/trading-intelligence-mcp"
    x_title: str = "TIAS-TradeAnalysis"
    analysis_version: int = 1
    api_key: str = ""


@dataclass
class APEXSettings:
    """APEX — DeepSeek-based post-decision trade optimization via OpenRouter."""
    enabled: bool = False
    api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    model: str = "deepseek/deepseek-v3.2"
    fallback_model: str = "deepseek/deepseek-v3.2"
    timeout_seconds: int = 60
    max_tokens: int = 800
    temperature: float = 0.2
    max_position_size_usd: float = 1200.0
    max_leverage: int = 5
    min_tias_trades_for_optimization: int = 3
    # Brain-Authoritative Sizing (2026-05-31). When True the downstream HONORS
    # the brain's size_usd: APEX never shrinks below the brain's directive, and
    # the gate's CHECK 4 becomes a HARD available-capital ceiling (a fixed
    # generous fraction of REAL available) instead of a conviction shrink.
    # Dataclass default False = legacy-safe (test fixtures / partial config);
    # config.toml sets it True per operator intent. Instant revert = set False.
    brain_authoritative_sizing_enabled: bool = False
    # Per-trade hard ceiling as a fraction of REAL available capital, used by
    # gate CHECK 4 under brain-authoritative sizing. 0.40 of ~$7.6k ≈ $3,054.
    brain_auth_per_trade_pct_of_available: float = 0.40
    min_regime_trades_for_fallback: int = 10  # Regime-wide fallback threshold
    http_referer: str = "https://github.com/trading-intelligence-mcp"
    x_title: str = "APEX-TradeOptimizer"
    api_key: str = ""

    # Issue B fix Phase 3b (2026-05-08) — bounded retry knobs. The retry
    # loop in ``TradeOptimizer.optimize`` reads these via ``getattr`` so
    # legacy tests / configs continue to work; defaults match the values
    # documented in the issueB Phase 2 report (1 retry, 0.7 s backoff).
    # Set ``apex_max_attempts = 1`` to disable retries entirely (legacy
    # behaviour pre-fix). Per-call latency cost when retry fires:
    # ``apex_retry_backoff_seconds`` + (one retry HTTP RTT). Per-day
    # cost increase at the audit-window rate: negligible.
    apex_max_attempts: int = 2
    apex_retry_backoff_seconds: float = 0.7

    # Guardrails (Phase A3)
    min_tp_pct: float = 0.3
    gate_tp_floor_enabled: bool = True
    gate_trail_activation_floor_pct_of_tp: float = 15.0
    gate_trail_distance_floor_pct: float = 40.0
    gate_mode_override_enabled: bool = True
    gate_confidence_floor: float = 0.50
    # Hard ceiling on APEX/conviction size inflation vs. Claude's pre-APEX
    # directive. If APEX proposes size > (claude_original × mult), the gate
    # clamps to the ceiling and logs CONVICTION_SIZE_CAP. 1.5× leaves room
    # for DeepSeek to size up proven winners without runaway doubling.
    gate_apex_size_cap_mult: float = 1.5

    # J5 (2026-05-14) — dynamic per-trade size cap and conviction-aware
    # scaling.
    #
    # Pre-J5 the cap at src/apex/optimizer.py:828-830 was the static
    # ``max_position_size_usd`` constant (1200.0). Audit OBS-15 showed
    # 15 of 18 trades (83%) clamping to exactly $1200 regardless of
    # signal strength, regime, or volatility — every brain proposal in
    # the $12k-$18k range collapsed to identical output.
    #
    # Two knobs make the cap and the in-cap allocation respond to
    # context:
    #
    # 1. apex_size_cap_pct_of_equity (default 0.0 = disabled / legacy):
    #    When > 0, the effective cap is
    #        max(max_position_size_usd, account_equity * pct / 100).
    #    The ``max(...)`` floor preserves a sane minimum for small
    #    accounts so a freshly-funded $1k account does not trade $50
    #    positions. The operator wires this on by setting the value
    #    in config.toml [apex] section; until set, behaviour is
    #    byte-equivalent to pre-J5.
    #
    # 2. apex_size_conviction_floor (default 0.5 = 50%):
    #    Within the cap, the size is scaled by
    #        max(conviction_floor, trade.confidence).
    #    With floor=0.5, a 0.65 conviction trade gets 65% of the cap;
    #    a 0.85 conviction trade gets 85% of the cap. Low-conviction
    #    trades shrink relative to high-conviction ones, which is the
    #    differentiation the audit demanded.
    #
    # Both knobs preserve the master prompt's hard constraint "Risk
    # control preserved" — the cap is never exceeded; conviction can
    # only shrink within the cap, never inflate above it. The
    # aggressive-exploitation philosophy is honoured because strong
    # setups can now legitimately use a larger fraction of the cap.
    apex_size_cap_pct_of_equity: float = 0.0
    apex_size_conviction_floor: float = 0.5

    # Issue 3 (2026-05-18) — 5-min per-(symbol, direction) reentry
    # cooldown. After ANY close (win or loss / any reason / any
    # trigger) the gate blocks new entries on the same
    # (symbol, direction) for this many seconds. Opposite-direction
    # entries on the same symbol remain eligible. After the window
    # expires the (symbol, direction) is allowed again — no
    # condition matching, no DB lookup, just a clock. The
    # WorkerManager passes this value to
    # TradeCoordinator.set_reentry_cooldown_seconds() at wiring time;
    # the coordinator clamps non-positive values to its 300-second
    # default. Replaces the prior J6/H4 reentry_learning_gate and
    # T2-1 loss_cooldown surfaces removed in issue3/p3-3.
    reentry_cooldown_seconds: int = 300
    # F9 (2026-06-09) — loss-only re-entry cooldown + selection exclusion. When
    # True: (1) the per-(symbol,direction) re-entry cooldown is set ONLY on a
    # real loss (COORD_AUTH net dollar < 0), not on every close; and (2) a symbol
    # in an active loss cooldown is EXCLUDED from the Call-A candidate list (the
    # scanner skips it so a fresh coin takes the slot) until the cooldown expires,
    # then it reappears. Targets the DOGE-type re-churn (a loser re-bought every
    # 5 min into the same grind). Default False reproduces the prior every-close
    # cooldown with no selection exclusion (byte-identical). Duration reuses
    # reentry_cooldown_seconds.
    loss_cooldown_enabled: bool = False

    # Conviction Allocator (Phase B)
    conviction_enabled: bool = True
    conviction_min_trades: int = 3

    # T2-2 / F14 zero-conviction reject (six-tier-fixes 2026-05-11).
    # Apex gate hard-rejects a trade when ALL THREE conviction signals
    # fall at-or-below their thresholds (i.e. the all-zero case the
    # report cited: SOLUSDT xray_conf=0.00 setup_score=0.0
    # expected_rr=0.00 was placed at $180 lev=2 with no structural
    # backing). Defaults are 0.0 / 0.0 / 0.0 so only the all-zero case
    # rejects — operator's aggressive-exploitation philosophy is
    # preserved unless they explicitly tighten any threshold > 0.
    min_xray_conf_for_trade: float = 0.0
    min_setup_score_for_trade: float = 0.0
    min_expected_rr_for_trade: float = 0.0

    # Entry-quality filters (win-rate enhancement, 2026-07-07). Three
    # per-leg minimums from ENTRIES_QUALITY_DIAGNOSIS.md — each leg flipped
    # the audited 395-trade window positive (signal_conf >= 0.50: +$5.10;
    # xray_conf >= 0.40: 53% win +$2.90; ADX >= 25: +$3.40) but all three
    # are ONE-WINDOW hypotheses, so every reject AND accept is logged with
    # the three values (GATE_REJECT reason=entry_quality_* /
    # GATE_ENTRY_QUALITY_PASS) for multi-window validation. A leg whose
    # value is UNKNOWN at gate time (stamped -1.0, e.g. package missing)
    # fails OPEN — a degraded cache must never silently block all trading.
    # Defaults ship inert (enabled=False, mins 0.0) so an absent config
    # section preserves today's behaviour; config.toml activates.
    entry_quality_filters_enabled: bool = False
    entry_quality_signal_conf_min: float = 0.0
    entry_quality_xray_conf_min: float = 0.0
    entry_quality_adx_min: float = 0.0

    # Issue E18 (2026-05-27): A+ size-boost confidence floor. The gate boosts
    # size by gate_a_plus_size_mult when setup score >= gate_a_plus_score_threshold;
    # gate_a_plus_conf_floor withholds that boost when X-RAY structural
    # confidence is below the floor, so a high score with no structural
    # confidence is never upsized — risk concentrates on validated setups.
    # conf_floor 0.0 = current behaviour (boost always applies); config.toml
    # sets 0.70 to enable. NEVER blocks a trade — only withholds the multiplier.
    gate_a_plus_score_threshold: float = 80.0
    gate_a_plus_size_mult: float = 1.20
    gate_a_plus_conf_floor: float = 0.0

    # Issue E17 (2026-05-27): structureless-but-scored reject. Fires only when
    # X-RAY structural confidence <= gate_structureless_conf_floor AND
    # setup_score >= gate_structureless_score_min (the score/confidence
    # contradiction). Belt-and-suspenders behind #7's producer cap; never
    # touches a trade with genuine confidence, so it cannot cull legitimate
    # aggressive entries. Safe defaults disable it (conf_floor 0.0 = exactly-
    # zero confidence, score_min 999 = never). config.toml sets the live values
    # (conf_floor 0.05, score_min 65 = just above #7's matched-weak B-cap of 64).
    gate_structureless_conf_floor: float = 0.0
    gate_structureless_score_min: float = 999.0

    # Issue 7 (2026-06-08) — portfolio directional-drawdown breaker. Every
    # per-trade cap is per-trade only; there is no portfolio-level breaker, so a
    # one-directional book can bleed together (a -$204 correlated cluster of
    # same-side caps in one session) while each trade stays within its cap. When
    # ENABLED, the entry gate HALTS NEW SAME-DIRECTION entries once the open book
    # is over-concentrated in that direction AND the aggregate open (unrealized)
    # loss across that direction's positions exceeds a fraction of equity. It
    # NEVER closes open positions (runners are the edge) and only halts the
    # over-concentrated direction (the opposite direction stays open, which
    # rebalances the book) — a directional-risk circuit breaker, NOT a
    # coin-selection gate or broad suppression. Default OFF: one session is
    # insufficient to size the threshold, so the operator reviews more live
    # correlated-drawdown data and flips it on. min_positions requires a real
    # book before it can fire; concentration is the long/short skew that counts
    # as one-directional; open_loss_pct is the aggregate same-direction open loss
    # as a percent of equity that trips the halt.
    portfolio_dd_breaker_enabled: bool = False
    portfolio_dd_breaker_min_positions: int = 3
    portfolio_dd_breaker_concentration: float = 0.80
    portfolio_dd_breaker_open_loss_pct: float = 1.5

    # Per-class TP cap multiplier (× recommended_tp_pct from volatility profiler).
    # Applied after DeepSeek returns — optimizer.optimize() line ~180-200.
    # Phase 5 of dir-block-fix (2026-05-05): raised every class so Qwen
    # can recommend larger TPs when supported by structure. The hard
    # 5 % ceiling enforced inside optimize() prevents wild outliers
    # regardless of class multiplier. Missing class → falls back to
    # 1.6 (Phase 5 medium-class default).
    tp_cap_multiplier_by_class: dict = field(default_factory=lambda: {
        "dead": 1.4, "low": 1.5, "medium": 1.6, "high": 1.8, "extreme": 2.0,
    })
    # Hard upper bound on the TP cap regardless of class multiplier.
    # Even if recTP × mult would land above this value, the cap clamps
    # to apex_tp_cap_hard_ceiling_pct so genuine outliers don't reach
    # production.
    apex_tp_cap_hard_ceiling_pct: float = 5.0

    # Definitive-fix Phase 9 (2026-04-28) — flip discipline. The legacy
    # ``_check_direction_lock`` locked direction in trending and
    # volatile-without-evidence regimes but allowed unconstrained flips
    # in ranging / dead / unknown. Forensic S3 captured a bearish-thesis
    # APEX flip Sell→Buy that produced simultaneous long-BTC + short-ETH.
    # Two new gates close the loophole:
    #   apex_min_flip_confidence: when DeepSeek flips direction in a
    #     ranging/dead/unknown regime, its emitted ``confidence`` MUST
    #     clear this bar. Below it, the flip is reverted by the existing
    #     APEX_DIR_LOCK_OVERRIDE machinery.
    #   apex_block_flip_resize: when True, a flip cannot UPSIZE the
    #     position above Claude's original. Smaller Qwen sizing on a
    #     flip is ALLOWED (de-risks lower-conviction flips). Setting
    #     to False disables both the upsize cap and the downsize
    #     allowance — full Qwen size wins. Semantic narrowed in
    #     Post-Execution Closure Fix Phase 2 (2026-05-05); the prior
    #     "one change per directive" rule forced flipped trades to
    #     full-conviction sizing despite lower-conviction direction.
    # Phase 3 of dir-block-fix (2026-05-05): lowered 0.90 → 0.70 so a
    # moderately confident flip backed by structural evidence can pass.
    # Pre-fix all four 24-h APEX_FLIP_BLOCKED events (HYPERUSDT in
    # regime=ranging) had Qwen confidence 0.85, just below the prior
    # 0.90 floor — pure threshold churn. Combined with the new
    # apex_flip_rr_boost_*, a "moderately confident, strong-RR" flip
    # now passes; a "weak-RR" flip still blocks.
    apex_min_flip_confidence: float = 0.70
    apex_block_flip_resize: bool = True
    # APEX Direction-Flip Switch (IMPLEMENT_APEX_FLIP_SWITCH, 2026-05-25).
    # Master on/off gate for APEX's ability to REVERSE the brain's trade
    # direction in TradeOptimizer.optimize. When False (operator default,
    # 2026-05-25, symmetric with the X-RAY switch), any model-proposed flip
    # is reverted to the brain's direction and APEX's trade-optimization
    # (stop, target, size, leverage, analysis) still applies to that
    # direction — identical in shape to the existing flip-revert gates
    # (direction-lock / counter-trade / insufficient-data / confidence).
    # When True, behavior is exactly as before this switch existed.
    # Reversible at runtime via [apex] apex_dir_flip_enabled in config.toml.
    apex_dir_flip_enabled: bool = False
    # Issue 2.3 (2026-06-07): APEX leverage-override kill-switch, symmetric with
    # apex_dir_flip_enabled. The optimizer LLM can output its own leverage which
    # may EXCEED the brain's directed leverage (live: brain lev3 executed lev5,
    # amplifying a loss). When False (default) APEX may NOT raise leverage above
    # the brain's directive — the brain's leverage stands; APEX's SL/TP/size
    # optimization is untouched. When True, behavior is exactly as before.
    apex_leverage_override_enabled: bool = False
    # Five-Fix Follow-Up — Fix 5 (2026-06-10): APEX size-override kill-switch,
    # symmetric with apex_leverage_override_enabled above. The optimizer LLM
    # proposes its OWN position size; the J5 dynamic-sizing block adopted it
    # (capped, conviction-scaled, floored at the brain's size under
    # brain-authoritative mode) — so the EXECUTED size could land up to
    # gate_apex_size_cap_mult times the brain's deliberate size_usd (proven
    # live 2026-06-10: brain $700, executed $1050). When False (default,
    # operator decision 2026-06-10) the brain's parsed size flows UNMODIFIED
    # to the order — no adoption, no raise, no conviction shrink — and the
    # gate's A+ ceiling boost is inert too; every safety validation (gate
    # CHECK 0/1/2/4/5, breadth-brake, enforcer throttle, exchange minimum)
    # remains in force as ceilings. When True, the pre-fix sizing behaviour
    # applies byte-identically. Boot sentinel: BOOT_APEX_SIZE_OVERRIDE_{ON|OFF}.
    apex_size_override_enabled: bool = False
    # Phase 3 of dir-block-fix (2026-05-05): RR-weighted confidence
    # boost applied inside _enforce_flip_confidence. When the flipped
    # direction's structural R:R is at least
    # apex_flip_rr_boost_threshold times the chosen direction's R:R,
    # the effective confidence checked against
    # apex_min_flip_confidence is raw_confidence + apex_flip_rr_boost_amount.
    # The boost is gate-local; it does not propagate downstream.
    apex_flip_rr_boost_threshold: float = 3.0
    apex_flip_rr_boost_amount: float = 0.15

    # PRIMARY Sell-Bias Fix (2026-05-11) — asymmetric flip thresholds.
    # The 2026-05-11 investigation surfaced that Buy→Sell flips destroy
    # win rate (-16.1 pp in shadow data, -6.8 pp in bybit_demo) while
    # Sell→Buy flips help mildly (+10.4 pp in shadow). Replacing the
    # single symmetric ``apex_min_flip_confidence`` with two
    # direction-pair-specific floors lets the operator tighten the
    # harmful side without losing the helpful side.
    # Resolution order in ``_enforce_flip_confidence``:
    #   1. If brain=Buy and qwen=Sell, use apex_min_flip_confidence_buy_to_sell.
    #   2. If brain=Sell and qwen=Buy, use apex_min_flip_confidence_sell_to_buy.
    #   3. Else fall back to the legacy symmetric apex_min_flip_confidence.
    # Defaults reflect the operator-chosen HEAVY tune: Buy→Sell needs
    # near-certainty (0.95), Sell→Buy keeps the established 0.70 floor.
    # The RR-boost (apex_flip_rr_boost_amount) still applies on top —
    # the threshold is what raw_conf + boost must clear.
    # See dev_notes/sell_bias_fixes/p_phase1_flip_performance.md.
    apex_min_flip_confidence_buy_to_sell: float = 0.95
    apex_min_flip_confidence_sell_to_buy: float = 0.70

    # PRIMARY Sell-Bias Fix (2026-05-11) — insufficient-data flip gate.
    # Code-enforced rule: block a flip when the flipped direction has
    # fewer than this many trades in the current regime for this symbol —
    # closes the feedback loop where prior Sell-biased flips inflate Sell
    # history and license further Sell flips. Set to 0 to disable.
    # This is the AUTHORITATIVE, binding gate: it reverts any flip below
    # the threshold regardless of what DeepSeek proposes.
    # E27 (2026-05-28): raised 5 -> 8 to require a more durable sample.
    # The DeepSeek system-prompt advisory (src/apex/prompts.py) still
    # reads "fewer than 5" (a softer hint); the code gate is stricter and
    # wins, so the live prompt wording was intentionally left unchanged.
    # Latent today — the gate is only reached under was_flipped and
    # direction flips are disabled (apex_dir_flip_enabled=false).
    # See dev_notes/sell_bias_fixes/p_phase1_deepseek_responses.md
    # (EGLDUSDT example).
    apex_min_trades_for_flip: int = 8

    # PRIMARY Sell-Bias Fix (2026-05-11) — counter-trade respect.
    # The scanner explicitly labels coins with COUNTER_TRADE_LONG /
    # COUNTER_TRADE_SHORT secondary labels (91 such labels in the
    # 2026-05-11 9-hour log window). Brain renders these as
    # "(COUNTER-TRADE — opposite to structural bias)" in its prompt and
    # may take the contrarian Buy. Pre-fix, APEX (via DeepSeek) and
    # XRAY (via structural placement) would then silently flip the
    # trade back to the structural direction, destroying the operator-
    # designed contrarian alpha. When True, APEX may not flip direction
    # on trades whose ``package.structural_data.setup_type`` contains
    # "counter" (case-insensitive). XRAY runs in strategy_worker.py and
    # is unaffected by this flag — XRAY flips on a counter-trade still
    # fire today.
    # See dev_notes/sell_bias_fixes/p_phase1_xray_root_cause.md.
    apex_respect_counter_trade: bool = True

    # ===================================================================
    # R2 direction-fix (2026-05-17) — scenario-driven composite lock.
    # ===================================================================
    # Replaces the prior regime-only ``_check_direction_lock`` decision
    # tree (which locked any trending regime to its natural direction
    # regardless of evidence and vetoed all 11 DeepSeek flip attempts on
    # the 2026-05-16 session, including the 7.3x-favoring-Long BSBUSDT
    # case that cost -$70.08). The new lock asks the same direction-
    # agnostic question for both Buy and Sell: "given current evidence
    # (regime, structural R:R, counter-trade direction, recent
    # per-direction WR, symbol-specific flip evidence), is the brain's
    # direction supported?" The asymmetry between Buy and Sell EMERGES
    # from the WR signal automatically — Buys-win-more produces a higher
    # WR contribution for Buy and a lower one for Sell at decision time.
    # No hard-coded "if direction == X then Y" branches.
    #
    # Composite score = sum of (signal * weight) across 5 signals. Lock
    # fires when the score is below ``apex_lock_score_threshold``. Each
    # signal is normalized roughly to [-1, +1] except structural which
    # uses log-scale (naturally bounded by max realistic R:R ratios).
    # Default weights are NEUTRAL (1.0 each); the operator may tune any
    # weight up or down based on observed post-fix behavior.
    apex_lock_score_threshold: float = 0.0
    apex_lock_regime_weight: float = 1.0
    apex_lock_structural_weight: float = 1.0
    apex_lock_trade_dir_weight: float = 1.0
    apex_lock_wr_weight: float = 1.0
    apex_lock_symbol_evidence_weight: float = 1.0
    # Symbol-evidence threshold (the existing _check_flip_evidence floor).
    # If the opposite direction's WR for this symbol's regime-filtered
    # history is >= this percentage, the symbol-evidence signal is -1
    # (evidence against the brain's direction).
    apex_lock_symbol_evidence_wr_floor_pct: float = 70.0

@dataclass
class SentinelSettings:
    """SENTINEL — Exit Firewall + Deadline Engine + Portfolio Advisor."""
    enabled: bool = True

    # Part 1: Exit Firewall — blocks strategic review from closing positions
    firewall_enabled: bool = True

    # Part 2: Deadline Engine — tiered expiry logic based on PnL
    deadline_profit_pct: float = 0.5
    deadline_breakeven_lower_pct: float = -0.3
    deadline_small_loss_pct: float = -1.5
    deadline_grace_minutes: float = 5.0
    deadline_small_loss_sl_pct: float = 0.5

    # Part 3: Portfolio Advisor — DeepSeek V3 risk assessment
    advisor_enabled: bool = False
    advisor_interval_seconds: int = 300
    advisor_model: str = "deepseek/deepseek-chat-v3-0324"
    advisor_temperature: float = 0.2
    advisor_max_tokens: int = 800
    advisor_timeout_seconds: int = 30
    advisor_api_key: str = ""

    # TRADE LIBERATION: Min profit before allowing stop tightening
    advisor_min_profit_for_tighten_pct: float = 0.50


@dataclass
class StructureSettings:
    """X-RAY Structural Intelligence configuration."""
    enabled: bool = True
    worker_interval_seconds: int = 60
    cache_ttl_seconds: int = 300
    min_candles: int = 50
    swing_lookbacks: list[int] = field(default_factory=lambda: [3, 5, 10])
    cluster_pct: float = 0.3
    min_touches: int = 2
    # Issue 1 of 2026-05-19 direction-bias fix Phase C — symmetric
    # min_touches filter for resistance levels. Legacy behavior at
    # src/analysis/structure/support_resistance.py:126 hardcoded a
    # `>= 1` filter for resistance while support used config-driven
    # `min_touches >= 2`. In sustained downtrending markets, this
    # asymmetry filtered out single-touch swing lows but kept single-
    # touch swing highs, producing `sup=0 res=5` in 80.7% of audited
    # XRAY_ANALYZE rows — which collapsed `rr_long` toward 0 and
    # triggered cascading Buy → Sell flips in strategy_worker. Default
    # 2 symmetrizes with support. Operator may lower to 1 in markets
    # where resistance levels are clearly genuine on single touch.
    min_touches_resistance: int = 2
    # Issue 1 of 2026-05-19 direction-bias fix Phase C — minimum-edge
    # floor on structural_tp (consumed by _calc_long at
    # structural_levels.py:101 and _calc_short at :176). When the
    # nearest resistance/support is closer to current_price than this
    # percent, the structural_tp is clamped to be at least this far
    # away. Prevents `rr_long` (or `rr_short`) from collapsing toward
    # zero when price is at or near a level. Default 0.5 (i.e. 0.5%
    # minimum reward distance — well below the typical SL distance of
    # 1-2%, so legitimate tight setups still register but pathological
    # collapses are clamped).
    tp_min_distance_pct: float = 0.5
    max_levels_per_side: int = 5
    ms_swing_lookback: int = 5
    ms_min_swing_points: int = 3
    sl_buffer_pct: float = 0.15
    tp_buffer_pct: float = 0.10
    min_rr_ratio: float = 2.0
    sl_fallback_pct: float = 2.0
    tp_fallback_pct: float = 4.0
    # RR/direction-conflict fix Phase 2 (2026-05-31, flag-gated, default OFF).
    # In a trend, the setup direction is the trend side (uptrend->long,
    # downtrend->short). When that side's reward-to-risk is materially worse than
    # the opposite VALID side, re-point the setup to the better-RR side so
    # setup_type / trade_direction / label / placement all describe the tradeable
    # geometry instead of a setup the brain is merely told to skip. Addresses the
    # observed pattern where most candidates were labelled on the worse-RR (often
    # *_invalid) side. ``rr_aware_direction_ratio`` is the opposite/chosen RR
    # multiple that must be exceeded (matches the brain prompt's ">= 2x" rule).
    # Enable for a live trial (mirrors the xray_dir_flip_enabled rollout).
    rr_aware_direction_enabled: bool = False
    rr_aware_direction_ratio: float = 2.0
    # Direction-reconcile fix (2026-06-04, Problem 8). The NEUTRAL counterpart
    # to ``rr_aware_direction_enabled``: instead of RE-POINTING a trend-side
    # setup whose reward-to-risk is materially worse than the opposite VALID
    # side to that opposite side (which, in a downtrend tape, flips nearly every
    # range-floor coin to long and re-imposes a long-bias), X-RAY ABSTAINS — it
    # emits no directional commitment (trade_direction="") and caps the grade,
    # so the candidate carries no A+ score-100 short into the prompt. The brain
    # then decides direction from the coin's own evidence and the downstream
    # regime/win-rate/APEX backstops (P6/P7) judge it. Symmetric: abstains on an
    # invalid-RR long at tops exactly as on an invalid-RR short at bottoms. The
    # two flags are mutually exclusive by construction (rr_aware re-points first,
    # so the chosen side is already the valid better-RR side and abstain never
    # fires); keep rr_aware OFF when abstain is ON. ``xray_abstain_rr_ratio`` is
    # the opposite/chosen RR multiple that triggers abstain (matches the prompt's
    # ">= 2x" rule and rr_aware_direction_ratio).
    xray_abstain_on_invalid_rr_enabled: bool = False
    xray_abstain_rr_ratio: float = 2.0
    # F33 (2026-06-05) — confluence-paradox PRESENTATION (not a logic change).
    # When the X-RAY structurally-implied side is the WORSE reward-to-risk side
    # AND is roomless (its own RR below ``confluence_veto_rr_floor`` — i.e. reward
    # < risk) while the better side leads by >= ``confluence_veto_ratio``, the
    # candidate block adds an explicit NOTE that the confluent direction is vetoed
    # for insufficient room, so the brain understands a strong-looking structural
    # read (regime + X-RAY + ensemble agreeing) is correctly unavailable and does
    # not waste reasoning re-deriving it. The RR veto decision itself is unchanged
    # — this only surfaces it. rr_floor=1.0 means reward below risk (definitionally
    # roomless); ratio=2.0 matches the prompt's ">= 2x" better-side rule.
    confluence_veto_note_enabled: bool = True
    confluence_veto_rr_floor: float = 1.0
    confluence_veto_ratio: float = 2.0
    # Fix2 (2026-06-05) — with-trend continuation TP. In a confirmed trend, when
    # the WITH-TREND structural TP (support for a short, resistance for a long) is
    # valid (not min-edge clamped) but its reward sits closer than
    # continuation_tp_min_atr_mult x H1-ATR, the trade reads as 'no reward room'
    # (RR<1) and the brain skips the profitable with-trend continuation setup —
    # the artifact that suppressed with-trend short volume. The TP is then
    # re-anchored to an ATR-projected continuation target (continuation_tp_atr_mult
    # x ATR) further in the trend direction, taking only the FURTHER price so the
    # TP is never made closer. Pure ATR projection (no level-walking), bounded;
    # flip_tp_capper still caps over-distant TPs downstream. Trend-side only;
    # ranging/counter-trend/ATR<=0/already-clamped are untouched. Default ON;
    # xray_continuation_tp_enabled=false reverts.
    xray_continuation_tp_enabled: bool = True
    continuation_tp_min_atr_mult: float = 1.5
    continuation_tp_atr_mult: float = 2.5
    # Issue 3 (CALL_A exploit/fetch, 2026-06-05) — directional-RR setup scoring.
    # _compute_setup_score graded reward-to-risk off structural_placement.rr_ratio,
    # which structure_engine overwrites to rr_best = max(long_rr, short_rr). In a
    # downtrend a spent short (rr_short ~0.1) scored A+/100 because rr_long ~20 fed
    # the +20 RR modifier and bypassed the <0.5 -> SKIP hard cap. The scorer now
    # reads the CHOSEN direction's RR (rr_short for a short, rr_long for a long);
    # the rr_ratio = rr_best field is left intact for every other consumer (prompt
    # 'RR by direction' display, APEX gate/assembler, scanner ranking). That
    # directional correction is a bug fix and is always on. The range-position
    # no-room penalty below is an additive, symmetric defense: a short sitting at
    # the range floor (no room below) or a long at the range ceiling (no room
    # above) has spent geometry and is penalised even before the RR cap. These two
    # values are tuning starting points; set range_no_room_penalty = 0 to disable
    # only the range component (the directional-RR correction has no off knob —
    # it is simply correct).
    range_floor_threshold: float = 0.05
    range_no_room_penalty: int = 25
    # Four-Element Prompt Recalibration, Element 3 (2026-06-11) — range-truth
    # RENDER gate. The structure engine now captures the pre-clamp range
    # truth (range_breakout '' | 'below' | 'above' and range_overshoot_pct,
    # percent of the broken boundary's price) alongside the CLAMPED
    # position_in_range, which is unchanged for every bounds-assuming
    # consumer. This flag gates only the brain-facing markers (the
    # Structure line "range_pos=0.00 (BELOW RANGE by 2.3% — breakdown,
    # not a floor)" and the compact X-RAY pos= sites). June-11 evidence:
    # DYDX read range_pos=0.00 on all 24 submissions while price fell
    # THROUGH the range — the clamp disguised a breakdown as a floor and
    # the brain bought it 24 times. Computation/storage of the new fields
    # is unconditional (pure derivation); flipping this false restores
    # the prior prompt bytes exactly.
    range_truth_enabled: bool = True
    # Issue 3 (structure confluence, 2026-06-06): graded SMC confluence. The legacy
    # _compute_smc_confluence summed FLAT lumps on a binary present/absent test
    # (FVG 25, OB 30, liquidity 15, sweep 30), so ~81% of coins landed on the same
    # 70 (FVG+OB+liquidity present, no sweep) — the "structure confluence constant".
    # The graded version scales each component's contribution within [0, its weight]
    # by that coin's own zone quality (proximity to price plus the zone's
    # strength / freshness), so smc_confluence spreads per coin and differentiates
    # setups. The four weights are the per-component MAXIMA a perfect zone earns;
    # they keep the same 0-100 ceiling and the same per-component caps so the
    # downstream >=70 / >=40 / >=20 SMC bonus thresholds stay calibrated. The
    # proximity windows (percent of price) mirror the legacy binary thresholds so a
    # zone outside the window still earns nothing; only the grading inside is new.
    # Sweep recency (candles) is the window over which a sweep's contribution
    # decays to zero. All are tuning starting points.
    smc_weight_fvg: float = 25.0
    smc_weight_ob: float = 30.0
    smc_weight_liq: float = 15.0
    smc_weight_sweep: float = 30.0
    smc_fvg_proximity_pct: float = 2.0
    smc_ob_proximity_pct: float = 3.0
    smc_sweep_recency_candles: int = 20
    # Issue 2 (X-RAY de-saturation, 2026-06-06): the setup_score is base 50 plus
    # additive modifiers (entry position, trend alignment, reward-to-risk, SMC,
    # multi-timeframe, etc.) that sum to well over the 0-100 space for a good setup,
    # so they overflowed and the clamp pinned the majority of coins at exactly 100
    # (grade A+ only, the middle of the range empty). modifier_scale compresses the
    # NET modifier around the neutral base 50 by a single factor — relative ordering
    # is preserved EXACTLY (every modifier scales the same) but the score gains
    # headroom, so a maxed setup lands near the top, a typical one mid-range, a weak
    # one low, and the grades spread across A+/A/B/C. 1.0 reproduces the pre-fix
    # (overflow) behaviour; the active tuned value is in config.toml. The grade
    # thresholds are centralized here too (unchanged values, now operator-tunable).
    # The directional-RR hard caps and SMC+MTF floor cap run AFTER the scale and are
    # NOT scaled, so the spent-short de-grading is untouched.
    setup_score_modifier_scale: float = 1.0
    setup_grade_a_plus_min: int = 80
    setup_grade_a_min: int = 65
    setup_grade_b_min: int = 50
    setup_grade_c_min: int = 35
    # Phase 2: Smart Money Concepts
    fvg_min_gap_pct: float = 0.1
    fvg_max_age_candles: int = 50
    ob_displacement_min: float = 0.6
    ob_max_age_candles: int = 50
    liq_equal_tolerance_pct: float = 0.05
    liq_min_equal_count: int = 2
    liq_round_number_step: float = 100.0
    sweep_max_age_candles: int = 10
    sweep_min_wick_pct: float = 0.3
    # Phase 1c — XRAY confidence reachability fix. The legacy
    # ``LiquidityMapper._check_swept`` marked a zone as swept on ANY
    # historical wick through the level over the full candle window. Over
    # ~200 bars virtually every level had been wicked at some point, so
    # almost every zone entered analysis pre-marked swept. This collapsed
    # both the +15 unswept-liquidity component and the +30 active-sweep
    # component of the SMC confluence formula to 0 universe-wide,
    # capping setup_type_confidence at 0.55 (FVG=25 + OB=30 = 55/100).
    #
    # The fix bounds ``_check_swept`` to a recency window AND requires a
    # canonical SMC sweep+reclaim pattern (violation followed by close
    # back through the level on a later bar) before marking a zone
    # swept. Stale historical wicks no longer pollute the swept flag.
    #
    # ``sweep_recency_bars``: how far back (in candles) ``_check_swept``
    # examines for the violation+reclaim pattern. Default 30 — three
    # times ``sweep_max_age_candles=10`` so genuinely stale activity
    # (older than 30 bars) is ignored while ``detect_sweeps`` continues
    # to handle truly fresh single-candle sweep events within the
    # narrower 10-bar window.
    #
    # ``sweep_require_reclaim``: when True, ``_check_swept`` only marks
    # a zone swept when the violation has a corresponding reclaim bar
    # later in the window — matches canonical SMC. When False, falls
    # back to wick-only detection within the recency window (still an
    # improvement over the legacy unbounded scan, but less rigorous).
    # Same-candle reclaims are intentionally NOT caught here — those
    # are detect_sweeps' single-candle pattern and producing a
    # LiquiditySweep event for them is the right behavior.
    sweep_recency_bars: int = 30
    sweep_require_reclaim: bool = True
    # Phase 4: Intelligence
    setup_scanner_mode: str = "supplement"  # "supplement" or "replace"
    # Layer 1 universe alignment: structure_worker reads scanner's
    # active_universe (~30 coins) directly. Batched at batch_size per tick.
    # (Removed in Phase 6: ``scan_full_market`` flag — CoinDiscovery is gone.
    # Removed in Phase 6: ``coin_refresh_interval`` — was CoinDiscovery's
    # cache TTL, no longer relevant.)
    batch_size: int = 25
    shadow_db_path: str = "../shadow/data/shadow.db"
    # Issue #5 (2026-05-31): wire higher timeframes (H4 + Daily) into the X-RAY
    # MTF confluence scorer. Default OFF -> the scorer behaves byte-for-byte as
    # today's H1-only logic (regression-safe). When enabled, the structure
    # worker also analyses H4 + D1 structure (daily klines are already fetched
    # hourly by kline_worker but were consumed by nothing) and blends a bounded
    # cross-timeframe agreement signal into the existing 0-10 MTF score. This
    # changes X-RAY scores that gate setup classification + the brain prompt, so
    # it ships gated, awaiting an operator restart + trial.
    mtf_multi_timeframe_enabled: bool = False
    mtf_timeframes: list[str] = field(default_factory=lambda: ["240", "D"])
    mtf_h4_cache_ttl_seconds: int = 300       # aligns to kline H4 fetch cooldown
    mtf_d1_cache_ttl_seconds: int = 3600      # aligns to kline D1 fetch cooldown
    mtf_htf_weight: float = 0.25              # alpha — bounds HTF influence to +/-25%
    mtf_htf_limit: int = 120                  # candles fetched per higher timeframe
    # Layer 1 restructure Phase 2 — categorical setup-type thresholds.
    # See SetupTypesSettings docstring + StructureEngine.classify_setup.
    setup_types: "SetupTypesSettings" = field(
        default_factory=lambda: SetupTypesSettings()
    )

    def __post_init__(self) -> None:
        # Issue #5 validation — fail fast on a mis-typed config rather than
        # silently mis-scoring the whole universe.
        if not 0.0 <= self.mtf_htf_weight <= 1.0:
            raise ValueError(
                f"structure.mtf_htf_weight must be in [0,1], got {self.mtf_htf_weight}"
            )
        if self.mtf_h4_cache_ttl_seconds <= 0 or self.mtf_d1_cache_ttl_seconds <= 0:
            raise ValueError("structure.mtf_*_cache_ttl_seconds must be > 0")
        if self.mtf_htf_limit < self.min_candles:
            raise ValueError(
                f"structure.mtf_htf_limit ({self.mtf_htf_limit}) must be >= "
                f"min_candles ({self.min_candles}) for higher-TF structure detection"
            )
        # Only H4 ("240") and D1 ("D") are supported higher TFs; H1 ("60") is the
        # base and must not be listed here.
        _allowed = {"240", "D"}
        if not set(self.mtf_timeframes).issubset(_allowed):
            raise ValueError(
                f"structure.mtf_timeframes must be a subset of {_allowed}, "
                f"got {self.mtf_timeframes}"
            )


@dataclass
class SetupTypesSettings:
    """Phase 2 of Layer 1 restructure — categorical X-RAY setup classification thresholds.

    Tuned conservatively at first; Phase 9 observation drives any
    relaxation. Every threshold is exposed so operators can tighten or
    loosen behavior without code changes.

    Attributes:
        fvg_ob_min_confluence: Minimum normalized MTF confluence
            (0.0-1.0) to qualify a bullish/bearish FVG+OB combo.
        structural_break_require_retest: When True, BOS classification
            requires the BOS to be marked ``major`` (proxy for retest
            confirmation in absence of per-bar retest detection).
        sweep_min_displacement_pct: Minimum sweep depth (% of price) to
            qualify a liquidity sweep as a tradeable reclaim.
        range_breakout_min_compression_bars: Compression-bar floor for
            range breakout/breakdown classification.
        mtf_alignment_required: When True, FVG+OB classification
            additionally requires MTF alignment in the trade direction.
        ranging_market_mtf_threshold: Definitive-fix Phase 3 — minimum
            normalized MTF confluence (0.0-1.0) to allow a directional
            FVG+OB classification when the per-coin structure is
            ``ranging``. Set above ``fvg_ob_min_confluence`` if you
            want ranging setups to require stricter HTF backing than
            trending setups; set equal to relax uniformly. Defaults to
            0.55 (slightly above the 0.50 fvg_ob_min so trending markets
            keep their lower bar but ranging markets need a touch more
            HTF support to compensate for the absent trend bias).
    """
    fvg_ob_min_confluence: float = 0.7
    structural_break_require_retest: bool = True
    sweep_min_displacement_pct: float = 0.5
    range_breakout_min_compression_bars: int = 20
    mtf_alignment_required: bool = True
    ranging_market_mtf_threshold: float = 0.55
    # Issue 6 (2026-06-08) — FVG-OB-in-ranging down-weight. The fair-value-gap
    # order-block setup in a ranging regime at low confidence was the single
    # largest loss driver (all 7 win-prob cuts, ~ -$240, both long and short).
    # The exit's win-prob cut was correctly catching a losing ENTRY archetype, so
    # the fix is upstream: multiply the FVG-OB confidence by this discount when
    # the regime is ranging, so the setup scores LOWER (the rank-only funnel
    # selects it less) and sizes SMALLER (size_mult scales with confidence, then
    # floored at 0.5) — taken less and smaller on its genuine low quality. This
    # is a confidence-value calibration, NOT a coin-selection gate or a hard
    # exclusion (confidence is a multiplier with no pass/fail threshold; the
    # ensemble floor keeps a legitimate setup from being zeroed). Must be in
    # (0, 1]; 1.0 is the clean off-switch (no discount).
    fvg_ob_ranging_confidence_discount: float = 0.75

    # XRAY counter-setup Phase 2 — ATR-scaled distance windows for the
    # _find_nearest_fvg/ob proximity check. Replaces the fixed 2%/3% that
    # was too loose for low-vol coins (BTC at 0.42% ATR got a 4.8 ATR
    # window) and too tight for high-vol coins (DYDX at 1.30% ATR got
    # 1.5 ATR). Window = max(min_distance_pct, atr_multiplier * atr_pct_h1).
    # The floor protects very-low-vol coins from getting near-zero windows.
    fvg_atr_multiplier: float = 3.0
    ob_atr_multiplier: float = 4.0
    fvg_min_distance_pct: float = 2.0
    ob_min_distance_pct: float = 3.0

    # XRAY counter-setup Phase 4 — counter-setup classification knobs.
    # Counter setups (BULLISH_FVG_OB_COUNTER, BEARISH_FVG_OB_COUNTER) fire
    # when the suggested direction's in-direction structure is missing
    # but the OPPOSITE direction has tradeable FVG+OB structure near
    # price. They carry reduced confidence so downstream ranking honors
    # the lower conviction. Set counter_setup_enabled=false to disable
    # the entire counter-setup branch family for rollback.
    counter_setup_enabled: bool = True
    counter_confidence_multiplier: float = 0.7
    counter_mtf_threshold: float = 0.40
    # When True, counter setups only fire if structure is OPPOSITE to the
    # counter trade direction or ranging (e.g. BULLISH_*_COUNTER requires
    # downtrend or ranging structure). When False, also accepts volatile —
    # the more permissive default that maximizes characterization.
    counter_alignment_strict: bool = False

    # XRAY counter-setup Phase 6 — minor BoS confidence multiplier.
    # When ``structural_break_require_retest`` is False (Phase 6 default
    # change), BULLISH/BEARISH_STRUCTURAL_BREAK can fire on minor (non-
    # major) BoS events. Those carry less conviction than major BoS, so
    # confidence is reduced by this multiplier to reflect that. Major
    # BoS is unaffected. 0.8 default is intentionally above the 0.7
    # counter multiplier — minor BoS is "in-direction structure exists,
    # just less confirmed" which is more conviction than "no in-direction
    # structure but counter zones present."
    structural_break_minor_confidence_multiplier: float = 0.8

    def __post_init__(self) -> None:
        if not 0.0 <= self.fvg_ob_min_confluence <= 1.0:
            raise ValueError(
                f"setup_types.fvg_ob_min_confluence must be in [0,1], "
                f"got {self.fvg_ob_min_confluence}"
            )
        if self.sweep_min_displacement_pct <= 0:
            raise ValueError(
                f"setup_types.sweep_min_displacement_pct must be >0, "
                f"got {self.sweep_min_displacement_pct}"
            )
        if self.range_breakout_min_compression_bars < 1:
            raise ValueError(
                f"setup_types.range_breakout_min_compression_bars must be >=1, "
                f"got {self.range_breakout_min_compression_bars}"
            )
        if not 0.0 <= self.ranging_market_mtf_threshold <= 1.0:
            raise ValueError(
                f"setup_types.ranging_market_mtf_threshold must be in [0,1], "
                f"got {self.ranging_market_mtf_threshold}"
            )
        # Issue 6 (2026-06-08) — validate the FVG-OB-in-ranging discount in the
        # same style as the other multipliers (the classify_setup reader also
        # clamps defensively; this fails fast on a mistuned config). (0, 1].
        if not 0.0 < self.fvg_ob_ranging_confidence_discount <= 1.0:
            raise ValueError(
                f"setup_types.fvg_ob_ranging_confidence_discount must be in "
                f"(0, 1], got {self.fvg_ob_ranging_confidence_discount}"
            )
        if self.fvg_atr_multiplier <= 0:
            raise ValueError(
                f"setup_types.fvg_atr_multiplier must be >0, "
                f"got {self.fvg_atr_multiplier}"
            )
        if self.ob_atr_multiplier <= 0:
            raise ValueError(
                f"setup_types.ob_atr_multiplier must be >0, "
                f"got {self.ob_atr_multiplier}"
            )
        if self.fvg_min_distance_pct <= 0:
            raise ValueError(
                f"setup_types.fvg_min_distance_pct must be >0, "
                f"got {self.fvg_min_distance_pct}"
            )
        if self.ob_min_distance_pct <= 0:
            raise ValueError(
                f"setup_types.ob_min_distance_pct must be >0, "
                f"got {self.ob_min_distance_pct}"
            )
        if not 0.0 < self.counter_confidence_multiplier <= 1.0:
            raise ValueError(
                f"setup_types.counter_confidence_multiplier must be in (0,1], "
                f"got {self.counter_confidence_multiplier}"
            )
        if not 0.0 <= self.counter_mtf_threshold <= 1.0:
            raise ValueError(
                f"setup_types.counter_mtf_threshold must be in [0,1], "
                f"got {self.counter_mtf_threshold}"
            )
        if not 0.0 < self.structural_break_minor_confidence_multiplier <= 1.0:
            raise ValueError(
                f"setup_types.structural_break_minor_confidence_multiplier "
                f"must be in (0,1], got "
                f"{self.structural_break_minor_confidence_multiplier}"
            )


@dataclass
class VolatilityProfileSettings:
    """Per-coin volatility profiling — adaptive TP/SL/hold per coin's ATR."""
    enabled: bool = True
    # Phase 5 (P0-4): bumped from 60.0 to 120.0 so the per-symbol jitter
    # ([-jitter_range, +jitter_range], default 30 s) spreads expirations
    # across a 60 s window rather than clustering 30 coins into a single
    # 30 s thundering-herd recompute. Lower -> more recomputes/min, higher
    # -> staler profiles. 120 s matches the kline cadence.
    cache_ttl_seconds: float = 120.0
    # Phase 5 (P0-4): widened jitter range so the cache miss storm is
    # spread across the full TTL window, not just half of it.
    jitter_range_seconds: int = 30
    # Volatility class boundaries (ATR% on 5-min candles)
    dead_threshold: float = 0.05
    low_threshold: float = 0.15
    medium_threshold: float = 0.40
    high_threshold: float = 1.00
    # TP/SL floors and caps
    min_tp_pct: float = 0.30
    min_sl_pct: float = 0.20
    max_tp_pct: float = 8.0
    max_sl_pct: float = 5.0


@dataclass
class TimeDecaySettings:
    """Loser-lane Time-Decay SL — 5-model institutional exit intelligence.

    Runs inside PositionWatchdog only when pnl_pct < 0. Combined formula:
        allowed = atr_room × time_factor × recovery_mult × momentum_mult × prob_mult
        allowed = max(allowed, min_allowed_loss_pct)   # 0.15% floor
        allowed = min(allowed, original_sl_pct)        # never widen SL
    Force-closes when p_win < p_win_force_close. Propagates tighter-only SL
    via PositionWatchdog._push_sl_to_shadow (source="time_decay").
    """
    enabled: bool = True

    # Model 1 — convex time decay
    time_decay_exponent: float = 1.5

    # Model 2 — ATR-scaled room
    # atr_room_multiplier is the flat fallback; atr_room_multiplier_by_class
    # is the per-volatility-class override (dead=1.0, extreme=3.0, etc.).
    atr_room_multiplier: float = 2.0
    atr_room_multiplier_by_class: dict = field(default_factory=lambda: {
        "dead": 1.0, "low": 1.2, "medium": 2.0, "high": 2.5, "extreme": 3.0,
    })

    # Model 3 — MAE recovery multiplier
    mae_recovery_threshold: float = 0.5                # recovery > 0.5 → bonus
    mae_stagnation_threshold: float = 0.2              # recovery < 0.2 → penalty
    mae_bonus: float = 1.2
    mae_penalty: float = 0.8
    # Issue 3 (2026-06-08) — recovery-responsive tightening. The MAE monotonic
    # hold keeps mae_pct at the worst excursion; the recovery_multiplier (1.2
    # bonus) WIDENS the computed budget on recovery, so the tighter-only guard
    # pins the stop at the wide level set during the worst dip — the loss budget
    # stays widened through a genuine recovery. When the trade has recovered past
    # mae_tightening_recovery_threshold of its worst excursion, instead TIGHTEN
    # the stop toward the recovered level (a tight bounce-capture near the least
    # loss, blueprint Part 5.3), leaving recovery_tightening_buffer_pct below
    # current price so a still-running recovery is not cut. Fires only at a HIGH
    # recovery ratio so a moderate recovery still gets the room the 1.2 bonus
    # grants (no strangle). enabled=False is the clean off-switch.
    mae_recovery_tighten_enabled: bool = True
    mae_tightening_recovery_threshold: float = 0.75
    recovery_tightening_buffer_pct: float = 0.3

    # Model 4 — velocity/acceleration 4-case switch
    momentum_danger: float = 0.7                       # vel<0 & accel<0
    momentum_favorable: float = 1.3                    # vel>0 & accel>0
    momentum_slow_fall: float = 0.9                    # vel<0 & accel>0
    momentum_slow_rise: float = 1.1                    # vel>0 & accel<0

    # Model 5 — Bayesian p_win
    # Bug 3 fix (2026-04-23): prior base raised 0.40→0.55 so the starting
    # p_win at regime_conf=1.0 is 0.80 (was 0.65). With force-close
    # threshold dropped from 0.25 to 0.15 and regime penalty unchanged at
    # 0.60, a single bad tick no longer collapses p_win below the
    # force-close line. See FOUR_PRICE_AND_PARAMETER_BUGS_FIX.md, Bug 3.
    p_win_prior_base: float = 0.55
    p_win_prior_regime_weight: float = 0.25
    p_win_force_close: float = 0.15
    # H1 (2026-05-30) — near-certain-loser carve-out threshold. When p_win is
    # at/below this, the structural-invalidation guard yields so a clear bleeder
    # is cut instead of held. Must be <= p_win_force_close to take effect.
    near_certain_loser_p_win: float = 0.10
    # PF/LC Top-15 Problem 2.3 — age-aware near-certain-loser band. When
    # winprob_age_aware_band_enabled is true the carve-out yields at a higher
    # p_win once the trade is older than age_threshold_to_raise_p_win_seconds,
    # so an aged near-certain loser in the (young, old] band is cut instead of
    # held to its stop, while a young ambiguous trade keeps the lower threshold.
    # Both bounds must stay <= p_win_force_close (0.15). Default off (the single
    # near_certain_loser_p_win above is used), and the change can cut trades, so
    # offline-validate before enabling (Rule 9).
    winprob_age_aware_band_enabled: bool = False
    near_certain_loser_p_win_young: float = 0.10
    near_certain_loser_p_win_old: float = 0.13
    age_threshold_to_raise_p_win_seconds: float = 600.0
    p_win_tight: float = 0.40
    p_win_loose: float = 0.60
    p_win_tight_mult: float = 0.7
    p_win_loose_mult: float = 1.2

    # p_win update factors (each tick)
    p_win_atr1_penalty: float = 0.85                   # 1 ATR deeper this tick
    p_win_atr2_penalty: float = 0.70                   # 2 ATR deeper this tick
    p_win_recovery_bonus: float = 1.15                 # recovered 50%+ of MAE
    p_win_regime_bonus: float = 1.05
    p_win_regime_penalty: float = 0.60
    p_win_min: float = 0.05
    p_win_max: float = 0.95
    # PF/LC Top-15 Problem 3.1 — win-probability over-cut smoothing (the biggest
    # controllable exit lever). smooth_p_win_enabled is the master switch
    # (default off → behaviour identical to today). When on: the regime penalty
    # is edge-triggered (applied only after p_win_regime_penalty_sustained_ticks
    # consecutive mismatch ticks, so a single flicker no longer halves p_win),
    # and a recovery guard holds the force-close while the trade is within
    # p_win_recovery_guard_be_band_pct of breakeven AND making a new local high
    # over p_win_recovery_guard_n_ticks ticks (unless there is real structural
    # invalidation). Do NOT remove the exit — this only smooths it. Offline-
    # validate before enabling (Rule 9).
    smooth_p_win_enabled: bool = False
    p_win_regime_edge_trigger_enabled: bool = True
    p_win_regime_penalty_sustained_ticks: int = 3
    p_win_recovery_guard_enabled: bool = True
    p_win_recovery_guard_be_band_pct: float = 0.5
    p_win_recovery_guard_n_ticks: int = 3

    # Safety
    # grace_seconds is the flat fallback. grace_seconds_by_class overrides
    # per volatility class — slow coins (dead/low) act sooner (30-45 s), fast
    # coins (high/extreme) get more settling room (180-240 s).
    grace_seconds: int = 120
    grace_seconds_by_class: dict = field(default_factory=lambda: {
        "dead": 30, "low": 45, "medium": 120, "high": 180, "extreme": 240,
    })
    min_allowed_loss_pct: float = 0.15

    # Phase 1 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
    # minimum-age guardrail for force-close. Independent of and stricter
    # than `grace_seconds`. When position age is below this value, the
    # calculator returns None (no force-close, no SL push). Mirrors
    # `WatchdogSettings.strategic_action_min_hold_seconds=300` so the
    # time-decay path (which bypasses _execute_strategic_actions and calls
    # position_service.close_position() directly) is held to the same
    # 5-minute settling contract as CALL_B closes. Zero disables.
    min_age_seconds: float = 300.0

    # Phase 2 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
    # MAE-relative-to-SL gate. Force-close suppressed when the position's
    # worst drawdown (state.mae_pct) is less than this fraction of the
    # original SL distance. At 0.5 (default), a position must have drawn
    # down at least half of its original SL before time-decay can fire.
    # Below that level the trade is still in normal-development territory
    # — killing it captures noise rather than failure. Symmetric block:
    # both force-close and SL-tighten suppressed. Zero disables.
    mae_to_sl_ratio_threshold: float = 0.5

    # Phase 3 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
    # structural-invalidation gate. Force-close fires only when there's
    # real evidence of structural failure: XRAY confidence drop >=
    # `xray_drop_threshold` from entry, OR setup-type drift, OR regime
    # inverted at >= `regime_inversion_confidence_threshold`. The
    # watchdog computes the boolean and passes it into calculate(); this
    # flag controls whether the calculator HONORS the result. Set False
    # to disable the gate (preserves pre-fix p_win-only behaviour for
    # back-compat / debugging).
    structural_invalidation_required: bool = True
    # Issue 2.6 (2026-06-07): slow-bleed cumulative-drawdown force-close. When a
    # trade is below p_win_force_close AND has bled past this cumulative loss,
    # the structural guard yields so the force-close fires (catches the slow
    # structureless grind). Default OFF pending the offline check.
    slow_bleed_cumulative_force_close_enabled: bool = False
    slow_bleed_cumulative_loss_pct: float = 2.5
    # F4/F4b/F7 (2026-06-09): standalone monotonic-grind force-close (the
    # DOGE-grind lever). A p_win-INDEPENDENT cut for a trade that has stalled at
    # its worst excursion for a sustained run with no bounce (near_trough_streak)
    # and crossed a real loss floor — the dying slow grind the MAE-to-SL gate
    # (0.50) never reaches on a low-volatility coin. Fires after the min-age guard
    # and BEFORE the MAE gate. Does NOT read p_win (decoupled from the deferred
    # p_win unfreeze). Default OFF; offline-validated net-positive and
    # ZERO-strangle on the 2026-06-08 DOGE-vs-BLUR tape before enable. The
    # discriminator is a sustained-near-trough STALL streak, deliberately
    # conservative (pinned within 0.05% of the trough for ~4 minutes) so NO
    # recovering dip is strangled.
    monotonic_grind_cut_enabled: bool = False
    monotonic_grind_near_trough_band_pct: float = 0.05
    monotonic_grind_sustained_ticks: int = 24
    monotonic_grind_max_recovery_ratio: float = 0.20
    monotonic_grind_min_loss_pct: float = 0.30
    xray_drop_threshold: float = 0.40
    regime_inversion_confidence_threshold: float = 0.60

    # Absolute-PnL-depth penalty (Bayesian p_win update). Fires inside
    # _update_p_win when deeper_this_tick AND |pnl| exceeds the threshold.
    # Catches slow bleeders that never trip the ATR-relative penalty.
    p_win_abs_depth_threshold_pct: float = 1.5
    p_win_abs_depth_strong_pct: float = 3.0
    p_win_abs_depth_penalty: float = 0.90
    p_win_abs_depth_strong_penalty: float = 0.70

    # Observability
    # Phase 10 (logging overhaul): dropped from 10 to 1 so every TIME_DECAY_CALC
    # is visible during investigation. Revert to 10 once velocity metrics are
    # stable and the system has produced enough calculations to aggregate.
    log_every_n_ticks: int = 1


@dataclass
class MCPPoolSettings:
    """Phase 23 (Y-22) — MCP client pool config.

    Mirrors ``src.mcp.client_pool.MCPPoolSettings`` so the toml-driven
    Settings can populate the pool without circular imports. Defaults
    keep the pool DISABLED so existing consumers (one-shot stdio per
    call) keep working unchanged. Each consumer migrates by:

      1. Setting ``[mcp_pool] enabled = true`` in config.toml.
      2. Running ``python server.py --transport sse`` in the background.
      3. Updating consumer code to ``acquire`` from the pool, falling
         back to the legacy stdio path when the pool is disabled.
    """
    enabled: bool = False
    sse_url: str = "http://127.0.0.1:8080"
    min_warm: int = 1
    max_warm: int = 2
    health_check_interval_seconds: int = 60
    acquire_timeout_seconds: float = 2.0


@dataclass
class PriceFreshnessSettings:
    """Local price freshness + divergence-observation tuning.

    Controls how the transformer observes local-vs-Shadow price drift
    in shadow paper-trading mode. After the price-source-divergence fix
    (2026-05-03) the transformer's enrichment is observation-only —
    Shadow's authoritative ``mark_price`` and ``unrealized_pnl_usd``
    pass through to the dashboard unchanged. The divergence math is
    still run because the strategist's PROMPT_DEFERRED gate at
    ``src/brain/strategist.py:280-298, 500-523`` consumes
    ``Transformer._last_enrichment_max_divergence_pct``.

    - ``local_max_age_seconds``: above this age, the local
      ``ticker_cache`` row is treated as stale and the divergence
      calculation is skipped for that position (a ``PRICE_STALE``
      WARNING is logged).
    - ``divergence_override_pct``: above this absolute divergence (in
      percent), the observation emits a ``PRICE_DIVERGENCE_OBS``
      WARNING + ``price_divergence_obs`` event-buffer event so
      operators / Claude see the drift. The name is retained for
      backward compatibility with config.toml; semantics changed from
      "above-threshold = keep Shadow / below-threshold = override to
      local" (pre-fix) to "above-threshold = log + event-buffer /
      below-threshold = silent observation" (post-fix). No mutation
      happens in either branch.
    - ``divergence_block_prompt_pct``: above this divergence on any
      open position, the strategist defers Claude's B-cycle prompt
      until the next tick (so Claude never reasons over wrong prices).
      This contract is preserved byte-for-byte by Phase 2 of the fix.

    Set ``local_max_age_seconds=999999`` to disable the staleness gate
    (kill switch). Set ``divergence_override_pct=999`` to silence the
    observation log/event noise without affecting the strategist gate.
    Set ``divergence_block_prompt_pct=999`` to disable the prompt-defer
    guard.
    """
    local_max_age_seconds: float = 10.0
    divergence_override_pct: float = 0.5
    divergence_block_prompt_pct: float = 1.0
    # Issue 2.10 (2026-06-07): preventive anomalous-tick rejection at ingestion.
    # A single WS tick whose price jumps from the last accepted price by more
    # than this fraction (0.15 = 15%) is treated as an outlier and held (not
    # written), so a bad print cannot corrupt a PnL/stop decision. Consecutive
    # ticks at a new level past the escape count are accepted as the new
    # baseline, so a genuine sustained move is never stuck-rejected. 0 disables.
    spike_reject_pct: float = 0.15


@dataclass
class ObservabilitySettings:
    """Phase 1 of Layer 1 restructure — log tag + cycle tracker knobs.

    Attributes:
        cycle_tracker_history: How many cycles to retain in memory for
            ``/health`` (default 100 ≈ 8h at 5-min cadence).
        cycle_metrics_flush_seconds: How often to flush hourly aggregates
            to ``cycle_metrics`` table (default 3600 = 1h).
        log_tick_done_at_info: When True, ``LAYER1A_TICK_DONE`` and the
            other per-tick markers log at INFO. Set False to demote to
            DEBUG if log volume becomes a problem (rare).
    """
    cycle_tracker_history: int = 100
    cycle_metrics_flush_seconds: int = 3600
    log_tick_done_at_info: bool = True
    # System 1 (observability) — complete Call-A/Call-B brain prompt+response
    # capture. capture_brain_calls is the centralized boot-time gate (the
    # on-disk data/stage2_dumps/.enabled sentinel stays a live override). Each
    # call writes one JSON record to capture_dir; the hourly cleanup worker
    # prunes capture_dir/*.json older than capture_retention_days and caps the
    # directory at capture_max_files. Dedicated files only — never a trading or
    # protected table.
    capture_brain_calls: bool = True
    capture_dir: str = "data/stage2_dumps"
    capture_retention_days: int = 7
    capture_max_files: int = 5000
    # System 2 (observability) — per-second open-trade price path logger. A
    # standalone background task samples each open trade's WebSocket price (a
    # pure in-memory read, zero exchange API calls) about every
    # price_path_resolution_seconds, buffers in memory, and batch-flushes every
    # price_path_flush_seconds to the dedicated rotated price_path_filename. A
    # WS quote older than price_path_ws_max_age_seconds is treated as a gap, not
    # a point. price_path_rotation/retention are exposed for a future explicit
    # sink override (the routed sink inherits the global 10 MB / 7 days).
    price_path_logging_enabled: bool = True
    price_path_resolution_seconds: float = 1.0
    price_path_flush_seconds: int = 30
    price_path_ws_max_age_seconds: float = 5.0
    price_path_rotation: str = "10 MB"
    price_path_retention: str = "7 days"
    price_path_filename: str = "price_path.log"
    # System 3 (observability) — per-attempt stop-loss placement forensics. The
    # SL gateway emits one PLACEMENT_FORENSIC line per profit-lock placement
    # attempt to the dedicated rotated placement_forensic.log, recording the
    # caller snapshot vs the live mark (only when the fresh-mark-degrade already
    # fetched it — a pure piggyback, zero added API calls), the effective
    # min-distance, the placed/degraded/no-op outcome, and the foregone
    # tightening. Behaviour-neutral: it only reads already-computed values and
    # writes a log line (fire-and-forget). Default-on; set false to silence.
    placement_forensic_enabled: bool = True

    def __post_init__(self) -> None:
        if self.cycle_tracker_history < 1:
            raise ValueError(
                f"observability.cycle_tracker_history must be >= 1, "
                f"got {self.cycle_tracker_history}"
            )
        if self.cycle_metrics_flush_seconds < 60:
            raise ValueError(
                f"observability.cycle_metrics_flush_seconds must be >= 60, "
                f"got {self.cycle_metrics_flush_seconds}"
            )
        if self.capture_retention_days < 1:
            raise ValueError(
                f"observability.capture_retention_days must be >= 1, "
                f"got {self.capture_retention_days}"
            )
        if self.capture_max_files < 1:
            raise ValueError(
                f"observability.capture_max_files must be >= 1, "
                f"got {self.capture_max_files}"
            )
        if self.price_path_resolution_seconds <= 0:
            raise ValueError(
                f"observability.price_path_resolution_seconds must be > 0, "
                f"got {self.price_path_resolution_seconds}"
            )
        if self.price_path_flush_seconds < 1:
            raise ValueError(
                f"observability.price_path_flush_seconds must be >= 1, "
                f"got {self.price_path_flush_seconds}"
            )
        if self.price_path_ws_max_age_seconds <= 0:
            raise ValueError(
                f"observability.price_path_ws_max_age_seconds must be > 0, "
                f"got {self.price_path_ws_max_age_seconds}"
            )


@dataclass
class LayerManagerSettings:
    """Phase 2 (post-Layer-1 fix) — LayerManager safety knobs.

    Attributes:
        lm_attach_deadline_sec: Hard deadline (seconds since OrderService
            init) before the gate flips to fail-close for ALL purposes
            when ``layer_manager`` is still ``None``. Layer 4 close/SL
            normally bypass during the bootstrap window so a watchdog
            close can still execute, but exceeding the deadline implies
            attachment failure (LayerManager never constructed) — at
            that point even Layer 4 cannot be allowed to fly without an
            authoritative gate. Default 60 s comfortably covers the
            observed boot ordering window (≤ 5 s in production).
        state_sync_interval_sec: Disk/memory layer state sync heartbeat
            cadence. Every interval the LayerManager reads
            ``data/layer_state.json`` and compares to ``layer_active``
            in memory; a mismatch triggers a recovery action (see
            ``on_drift_action``). Default 60 s — fine for catching drift
            within one Strategy/Scanner cycle.
        on_drift_action: Phase 11 (dead-workers fix). What the heartbeat
            does when disk and memory disagree.

            ``"rewrite_disk"`` (default, post-fix): MEMORY is the live
                source of truth. On drift, re-persist memory to disk
                and emit ``LAYER_STATE_DRIFT_RECOVERED |
                direction=memory_to_disk``. Correct because: persist
                failures should be RECOVERED by re-attempting the
                persist, not by undoing the in-memory state. The
                pre-fix behaviour silently dropped operator toggles
                because a stale on-disk snapshot would overwrite the
                just-toggled memory state within ~30 s.

            ``"reload_memory"`` (legacy, pre-fix): DISK is the source
                of truth. On drift, overwrite memory from disk. Kept
                only as an emergency rollback in case the rewrite-disk
                semantics surface a pathology in the field. Operators
                should NOT use this in normal operation — it's the
                exact behaviour that produced the Layer 3 toggle
                revert regression observed live on 2026-04-27.

            Validated against the two allowed values in
            ``__post_init__``; any other string raises at config-load.
    """
    lm_attach_deadline_sec: float = 60.0
    state_sync_interval_sec: float = 60.0
    on_drift_action: str = "rewrite_disk"

    def __post_init__(self) -> None:
        if self.lm_attach_deadline_sec < 5.0:
            raise ValueError(
                f"layer_manager.lm_attach_deadline_sec must be >= 5.0, "
                f"got {self.lm_attach_deadline_sec}"
            )
        if self.state_sync_interval_sec < 10.0:
            raise ValueError(
                f"layer_manager.state_sync_interval_sec must be >= 10.0, "
                f"got {self.state_sync_interval_sec}"
            )
        if self.on_drift_action not in ("rewrite_disk", "reload_memory"):
            raise ValueError(
                f"layer_manager.on_drift_action must be 'rewrite_disk' or "
                f"'reload_memory', got {self.on_drift_action!r}"
            )


@dataclass
class WorkerLivenessSettings:
    """Phase 11 (dead-workers fix) — WorkerLivenessWatchdog tunables.

    Attributes:
        watchdog_interval_sec: Watchdog probe cadence in seconds.
            Default 30 — must be < ``first_tick_grace_sec`` so a
            never-ticked worker is detected within
            ``first_tick_grace_sec + watchdog_interval_sec``.
            Validated >= 10 (anything faster spams workers.log
            without operational benefit).
        first_tick_grace_sec: Grace window from ``WM_START`` before
            ``WORKER_NEVER_TICKED`` fires. Default 90 — covers four
            of the five 1B/1C/1D workers' first-tick latencies in
            the 06:18 reference run; scanner_worker (352 s in that
            run) will emit one alarm at boot before its first
            sweet-spot. Operators can raise this for environments
            where scanner reliably needs > 90 s. Validated >= 30.
        overdue_multiplier: Multiplier on ``expected_interval_s``;
            when ``last_tick_age_s`` exceeds
            ``expected_interval_s × overdue_multiplier``, the
            worker is ``WORKER_TICK_OVERDUE``. Default 2.0.
            Validated >= 1.5 (a multiplier below 1.5 false-alarms
            on normal late ticks under event-loop contention).
        alert_rate_limit_sec: Minimum seconds between Telegram alerts
            for the same worker name. Default 3600 (1 hour). Stops
            a stuck worker from flooding the operator. Validated
            >= 60.
    """
    watchdog_interval_sec: float = 30.0
    first_tick_grace_sec: float = 90.0
    overdue_multiplier: float = 2.0
    alert_rate_limit_sec: float = 3600.0

    def __post_init__(self) -> None:
        if self.watchdog_interval_sec < 10.0:
            raise ValueError(
                f"worker_liveness.watchdog_interval_sec must be >= 10, "
                f"got {self.watchdog_interval_sec}"
            )
        if self.first_tick_grace_sec < 30.0:
            raise ValueError(
                f"worker_liveness.first_tick_grace_sec must be >= 30, "
                f"got {self.first_tick_grace_sec}"
            )
        if self.overdue_multiplier < 1.5:
            raise ValueError(
                f"worker_liveness.overdue_multiplier must be >= 1.5, "
                f"got {self.overdue_multiplier}"
            )
        if self.alert_rate_limit_sec < 60.0:
            raise ValueError(
                f"worker_liveness.alert_rate_limit_sec must be >= 60, "
                f"got {self.alert_rate_limit_sec}"
            )


@dataclass
class SignalGeneratorMultiSourceSettings:
    """Phase 1 (output-quality) — multi-source signal classification.

    Pre-fix, ``SignalGenerator._evaluate_signal()`` used sentiment as a
    HARD gate: every BUY/SELL rule required ``abs(sentiment) > 0.2``.
    With sentiment=0.0 in 97.9% of coins (Reddit disabled + Finnhub no
    altcoin coverage), all signals fell through to NEUTRAL by design.

    The post-fix evaluator computes a weighted direction_score across
    four components (sentiment, F&G contrarian, funding rate, OI change)
    where each is "active" only if abs(score) >= its min threshold.
    A component with no data (sentiment=0.0) is INACTIVE — does NOT
    pull toward NEUTRAL — so other strong signals can derive direction
    independently.

    Attributes:
        sentiment_min_active: Minimum abs(sentiment) for sentiment to
            participate. Default 0.05 — sentiment near 0 means "no data"
            or "perfectly mixed", neither contributes meaningful direction.
        fg_min_active: Minimum abs(fg_score) for F&G to participate.
            Default 0.10 — corresponds to F&G < 47 OR > 53.
        funding_min_active: Minimum abs(funding_score) for funding to
            participate. Default 0.20 — needs meaningful skew.
        oi_min_active: Minimum abs(oi_score) for OI change to
            participate. Default 0.20.
        sentiment_weight: Weight in the rebalanced weighted sum.
            Default 0.40.
        fg_weight: Default 0.25 (contrarian; F&G extreme low → +1).
        funding_weight: Default 0.20 (positive funding → bearish).
        oi_weight: Default 0.15.
        strong_threshold: abs(direction_score) >= this → STRONG_BUY/STRONG_SELL.
            Default 0.55.
        buy_threshold: abs(direction_score) >= this → BUY/SELL.
            Default 0.25.
        fg_normalize_range: F&G normaliser. score = (50 - fg) / range.
            Default 30 — F&G=20 → +1.0, F&G=80 → -1.0.
        funding_normalize: Funding normaliser. score = -funding / norm
            (high positive funding = bearish). Default 0.005 (matches
            FUNDING_RATE_THRESHOLDS["high_positive"]).
        oi_normalize_pct: OI normaliser. score = oi_change / norm.
            Default 5.0 (matches OI_CHANGE_THRESHOLDS["moderate_increase"]).

    Validated in __post_init__: all weights in (0, 1], thresholds
    ordered (buy < strong), normalisers > 0.
    """
    sentiment_min_active: float = 0.05
    fg_min_active: float = 0.10
    # Definitive-fix Phase 5 (2026-04-28) — funding/oi min-active lowered
    # 0.20 → 0.10 so funding rates around |0.05%| and OI changes around
    # ±5% participate in direction_score (was ~|0.1%| / ±10%, which
    # rejected most coins). buy_threshold 0.25 → 0.18 to match the
    # typical BUY-leaning direction_score observed in forensic data.
    funding_min_active: float = 0.10
    oi_min_active: float = 0.10
    sentiment_weight: float = 0.40
    fg_weight: float = 0.25
    funding_weight: float = 0.20
    oi_weight: float = 0.15
    strong_threshold: float = 0.55
    buy_threshold: float = 0.18
    fg_normalize_range: float = 30.0
    funding_normalize: float = 0.005
    oi_normalize_pct: float = 5.0
    # Issue 1 (2026-06-08) — Fear-and-Greed direction neutrality. The
    # classifier produced ~100% buy because the contrarian F&G term
    # (s_fg = (50 - fg) / fg_normalize_range, large positive on extreme fear)
    # dominated the weighted direction sum while sentiment was off and
    # funding/OI were sub-threshold. That contradicts the prompt-layer
    # F&G-neutral work and was the structural root of the one-directional
    # contrarian-long book. When True, F&G contributes NOTHING to DIRECTION
    # (it is excluded from the active set of the direction sum) so direction
    # comes from the coin's own funding/OI/sentiment; F&G is still computed and
    # still informs the confidence magnitude. This is NEUTRALITY, not a flip
    # (the (50 - fg) mapping is untouched; only its directional participation is
    # removed). Offline replay over the forensic window: 100% buy -> 32% buy /
    # 51% sell / 17% neutral (a per-coin mix, not an inversion). False restores
    # the prior contrarian-buy behaviour (the clean off-switch). Implemented as
    # a flag, not fg_weight=0, because __post_init__ requires weights in (0, 1].
    fg_direction_neutral: bool = True
    # Fix 1 (price-conditioned OI, 2026-06-10) — the OI direction score is
    # conditioned on the same-window price move: rising OI on a FALLING price
    # reads bearish (shorts piling in), not bullish. oi_price_window_hours is the
    # price window matched to the OI change window (both ~24h today, so the two
    # halves of the condition describe the same move); oi_price_dead_band_pct is
    # the minimum absolute price move (percent) for the conditioning to apply —
    # 0.0 = invert on any opposite-sign move (truest futures semantics).
    oi_price_window_hours: float = 24.0
    oi_price_dead_band_pct: float = 0.0
    # Fix 2 (fresh signal inputs, 2026-06-10) — blend a SHORT OI window (fresh,
    # moves intra-session) with the 24h confirmation so the signal stops
    # freezing for many cycles on the slow 24h delta. The blend is applied to the
    # per-window PRICE-CONDITIONED, NORMALIZED OI scores (not raw percents), so
    # the often-saturated 24h delta does not drown the fresh short window.
    # oi_short_window_hours: the short window (the repo already computes
    #   change_1h_pct; this also sets the matching price-change window from klines).
    # oi_blend_weight_short / oi_blend_weight_long: weights on the short vs 24h
    #   conditioned scores. Set short=0.0 / long=1.0 to replay the prior
    #   24h-only behaviour (the revert / A-B baseline lever).
    # funding_use_instantaneous: keep the current funding rate as the fresh
    #   per-cycle input (funding is an 8h-settlement rate; a "delta" would need
    #   hourly history and would be less honest than the live rate).
    oi_short_window_hours: float = 1.0
    oi_blend_weight_short: float = 0.6
    oi_blend_weight_long: float = 0.0
    funding_use_instantaneous: bool = True
    # Five-Fix Follow-Up — Fix 2 (fresh OI windows, 2026-06-10) — the
    # 15-minute window joins the 1h window as the DIRECTIONAL DRIVERS; the 24h
    # window becomes CONTEXT-ONLY (oi_blend_weight_long default 0.0 — still
    # computed, rendered to the brain and logged, just not steering direction).
    # Operator-approved defaults: 15m=0.4, 1h=0.6, 24h=0.0. Each window is
    # price-conditioned against its OWN matching kline window before the blend
    # (the Fix-1 pairing measures both halves of the condition over the same
    # move). Cold-start ladder: if neither short window has usable data the
    # blend falls back to the 24h conditioned score at full strength — never
    # blind. Revert lever: oi_blend_weight_15m=0.0, short=0.7, long=0.3
    # restores the previous 1h+24h blend; 15m=0.0, short=0.0, long=1.0
    # restores the original 24h-only read.
    oi_15m_window_hours: float = 0.25
    oi_blend_weight_15m: float = 0.4

    def __post_init__(self) -> None:
        for name, val in (
            ("sentiment_weight", self.sentiment_weight),
            ("fg_weight", self.fg_weight),
            ("funding_weight", self.funding_weight),
            ("oi_weight", self.oi_weight),
        ):
            if not 0.0 < val <= 1.0:
                raise ValueError(
                    f"signal_generator.multi_source.{name} must be in (0, 1], "
                    f"got {val}"
                )
        if not 0.0 < self.buy_threshold < self.strong_threshold <= 1.0:
            raise ValueError(
                f"signal_generator.multi_source: must have "
                f"0 < buy_threshold < strong_threshold <= 1, got "
                f"buy={self.buy_threshold} strong={self.strong_threshold}"
            )
        for name, val in (
            ("fg_normalize_range", self.fg_normalize_range),
            ("funding_normalize", self.funding_normalize),
            ("oi_normalize_pct", self.oi_normalize_pct),
        ):
            if val <= 0.0:
                raise ValueError(
                    f"signal_generator.multi_source.{name} must be > 0, got {val}"
                )
        # Fix 1 (price-conditioned OI) bounds.
        if self.oi_price_window_hours <= 0.0:
            raise ValueError(
                f"signal_generator.multi_source.oi_price_window_hours must be "
                f"> 0, got {self.oi_price_window_hours}"
            )
        if self.oi_price_dead_band_pct < 0.0:
            raise ValueError(
                f"signal_generator.multi_source.oi_price_dead_band_pct must be "
                f">= 0, got {self.oi_price_dead_band_pct}"
            )
        # Fix 2 (fresh OI windows) bounds.
        if self.oi_short_window_hours <= 0.0:
            raise ValueError(
                f"signal_generator.multi_source.oi_short_window_hours must be "
                f"> 0, got {self.oi_short_window_hours}"
            )
        if (
            self.oi_blend_weight_short < 0.0
            or self.oi_blend_weight_long < 0.0
            or self.oi_blend_weight_15m < 0.0
        ):
            raise ValueError(
                f"signal_generator.multi_source: OI blend weights must be >= 0, "
                f"got 15m={self.oi_blend_weight_15m} "
                f"short={self.oi_blend_weight_short} long={self.oi_blend_weight_long}"
            )
        if self.oi_15m_window_hours <= 0.0:
            raise ValueError(
                f"signal_generator.multi_source.oi_15m_window_hours must be "
                f"> 0, got {self.oi_15m_window_hours}"
            )
        if (
            self.oi_blend_weight_15m
            + self.oi_blend_weight_short
            + self.oi_blend_weight_long
        ) <= 0.0:
            raise ValueError(
                "signal_generator.multi_source: at least one OI blend weight "
                "must be > 0"
            )


@dataclass
class CoinPackageValidatorSettings:
    """Phase 5 (output-quality) — CoinPackage validator thresholds.

    Attributes:
        fail_below: Verdict "fail" when completeness < this value.
            Failing packages are quarantined (not included in
            _coin_packages). Default 0.50.
        warn_below: Verdict "warn" when completeness < this value
            (and >= fail_below). Default 0.85.
        staleness_fail_seconds: built_at older than this counts as
            missing/stale. Default 300 (5 min) covers a normal cycle
            cadence with margin.
    """
    fail_below: float = 0.50
    warn_below: float = 0.85
    staleness_fail_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not 0.0 < self.fail_below < self.warn_below <= 1.0:
            raise ValueError(
                f"coin_package_validator: must have "
                f"0 < fail_below < warn_below <= 1, got "
                f"fail={self.fail_below} warn={self.warn_below}"
            )
        if self.staleness_fail_seconds <= 0:
            raise ValueError(
                f"coin_package_validator.staleness_fail_seconds must be > 0, "
                f"got {self.staleness_fail_seconds}"
            )


@dataclass
class SignalGeneratorSettings:
    """Phase 1 (output-quality) — SignalGenerator tunables.

    Wraps the multi-source classification settings so future signal-generation
    knobs can be added under the same ``[signal_generator]`` config section
    without growing the Settings class.
    """
    multi_source: SignalGeneratorMultiSourceSettings = field(
        default_factory=SignalGeneratorMultiSourceSettings,
    )


@dataclass
class AdaptiveExitSettings:
    """Dynamic Adaptive Exit System — centralized, tuning-ready coefficients.

    Converts the fixed exit thresholds into bounded multiples of R (the coin's
    ATR-as-percent, the movement unit), every PROFIT threshold floored at the
    round-trip fee so a locked win is net-positive. The pure geometry functions
    live in src/analysis/vol_scale.py and read these coefficients; nothing is
    hardcoded inline. The owner hierarchy is unchanged — this only sets the
    VALUES it writes. See ADAPTIVE_EXIT_PHASE3_DESIGN.md.

    EVERY value here is a STARTING POINT, tuned on the replay against the real
    logged trades (simulate_adaptive_exit_replay.py) and re-tunable without code.

    ``enabled=False`` lands the layer dormant: the geometry functions and config
    load, but no consumer reads them until the operator flips this on after the
    per-commit verification. The legacy fixed behaviour is unchanged while off.
    """

    # Master switch — dormant on landing; flipped on after verification.
    enabled: bool = False

    # ── The fee floor: the spine. Every profit threshold floors at this. ──
    round_trip_fee_pct: float = 0.11   # canonical round-trip taker fee for exit geometry
    fee_floor_buffer: float = 1.0      # fee floor = round_trip_fee_pct * buffer

    # R smoothing — EMA on the per-position movement unit so the geometry
    # breathes with the coin without vibrating with tick noise (the profiler's
    # 60-120s cache already makes R step-wise stable; this smooths the steps).
    # alpha in (0,1]; 1.0 = no smoothing. Applied at the fetch boundary, never
    # inside the pure geometry functions (which stay replayable).
    r_smoothing_alpha: float = 0.3

    # ── The arm: max(arm_r*R, fee_floor), bounded. ──
    arm_r: float = 0.5
    arm_min_pct: float = 0.0
    arm_max_pct: float = 1.0

    # ── The ladder rungs (in R) and staged capture. ──
    rung_r: list = field(default_factory=lambda: [1.5, 3.0, 5.0])
    secure_at_3r_r: float = 1.5        # secured profit (in R) once the middle rung is crossed
    lock_max_pct: float = 0.0          # 0 = naturally bounded by the peak

    # ── The trail fraction of R behind the peak (consumed by profit_lock_pct). ──
    trail_r: float = 0.5               # replay-tuned (peaks are ~1R, so a tight trail captures more)

    # ── Profit-scaled trail tightening (2026-06-26 give-back fix). ──
    # The effective trail coefficient starts at trail_r and decays toward
    # trail_r_floor as the running peak grows in R units, so a larger green trade
    # locks progressively nearer its peak (vol_scale.effective_trail_r). SHIPPED
    # INERT: trail_r_floor == trail_r makes the term a constant trail_r, identical
    # to the pre-fix half-R trail; activation is a single flip of trail_r_floor
    # below trail_r after the operator gate. Bounds enforced in validators.py.
    trail_r_floor: float = 0.5         # = trail_r → inert; (0, trail_r] when active
    trail_tighten_knee_r: float = 1.0  # no tightening until the peak clears this many R
    trail_tighten_scale_r: float = 1.0 # R above the knee over which the coefficient decays

    # ── The hard stop: a wide R-multiple backstop below the sacred cap. ──
    # The hard stop is a wider BACKSTOP than the sacred cap: floored at/above the
    # young cap (2.5%) so it can never preempt the cap (the operative catastrophic
    # floor, which fires first), and capped at a sane outer bound for volatile
    # coins (the old flat -3% was far too loose for a 0.05%-ATR coin, too tight
    # for a 2%-ATR coin). 9R: R=0.3% -> 2.7%, R=0.6% -> 5.4%, R>=1.1% -> 10% cap.
    hard_stop_r: float = 9.0
    hard_stop_min_pct: float = 2.5    # >= the young cap so the cap stays operative
    hard_stop_max_pct: float = 10.0   # outer sanity bound for high-vol coins

    # ── The dead-drifter scratch (conservative discriminator, operator choice). ──
    dead_drifter_enabled: bool = False
    dead_drifter_age_fraction: float = 0.70   # release only past ~70% of the deadline
    dead_drifter_min_move_r: float = 1.0      # lifetime peak must reach 1R or it is dead


@dataclass
class ProfitFetchingSettings:
    """Profit-Fetching Exit System — centralized, tuning-ready parameters.

    The time-driven whole-position exit engine (stepped break-even ladder +
    ATR/Chandelier trail + time-decay master dial + safety stop) integrated
    into the ProfitSniper. See PROFIT_FETCHING_SYSTEM_MASTER_BLUEPRINT.md.

    EVERY value here is a STARTING POINT for tuning, not a final number —
    honest tuning waits for truthful, after-cost PnL (blueprint Part 8). The
    time dial (src/core/time_dial.py) slides each ``*_young`` anchor toward its
    ``*_old`` anchor as the trade ages from 0 to its per-trade deadline.

    ``enabled=True`` (operator gate, 2026-05-29) makes the engine active on the
    next restart. Set False to keep the legacy trail/deadline/profit-take
    behaviour unchanged.
    """

    # Master switch — operator chose ON by default at the Phase 1 gate.
    enabled: bool = True

    # ── Techniques 2 + 4: ATR / Chandelier trail distance multiple ──
    # Wide when young (let the move breathe), tight when old (protect).
    atr_multiple_young: float = 3.0
    atr_multiple_old: float = 1.0
    # PF/LC Top-15 Problems 3.2 + 3.5 — feed the per-tick Chandelier trail the
    # real M5 Wilder ATR (via the warm-seeded _get_current_atr the loss path
    # already uses) instead of the cold ring-buffer atr_current / price-range/4
    # proxy. The existing live -> entry-ATR -> percent-floor fallback chain is
    # preserved for the cold-start window, so the trail is never naked. When on,
    # the warm cache is consulted every tick, so the SNIPER_ATR_FALLBACK log
    # (3.5) becomes rare and is demoted to DEBUG. Default off → the prior source
    # and INFO log (so landing the code changes nothing until the operator
    # enables and measures it).
    trail_live_m5_atr_enabled: bool = False

    # ── Technique 1: stepped break-even ladder ──
    # Step spacing — how far profit must climb to trigger the next lock.
    # Wider young (give the climb room), tighter old (grab in small steps).
    ladder_step_pct_young: float = 0.6
    ladder_step_pct_old: float = 0.4
    # Lock offset behind each crossed level. Looser young, tighter old.
    # At step 0.5 / offset 0.3 a +0.5% crossing locks +0.2% (blueprint 2.1).
    lock_offset_pct_young: float = 0.3
    lock_offset_pct_old: float = 0.2
    # First ladder level — profit must reach this before the ladder arms.
    # Item 2.1 / O1 (2026-06-03) — default lowered 0.5 -> 0.2 to match the
    # deployed config.toml value and keep the schema default in sync (the live
    # study: 108 of 162 round-trip losers peaked in [0.05%, 0.5%) and never
    # reached the old 0.5% arm, so the zero-crossing breakeven floor below was
    # unreachable; guardrail: zero winners peaked below 0.2%, so it cannot
    # strangle a slow-starter). Off-switch: set ladder_breakeven_lock_pct <= 0
    # to disable the floor, or raise this back to 0.5 to revert the arm.
    min_profit_to_arm_ladder_pct: float = 0.2
    # PF/LC Top-15 Problem 2.4 — the Chandelier trail's activation threshold,
    # centralized here next to the ladder arm and aligned to it (0.2%) so the
    # ladder and the trail coexist as candidates across the full graduated band
    # (blueprint Profit 3.1). Supersedes the stale Mode4Settings.min_profit_for_
    # trail_pct (0.5), which is now deprecated. Raise to revert the alignment.
    min_profit_for_trail_pct: float = 0.2
    # Finding 6 (2026-06-02) — zero-crossing breakeven floor. The arm threshold
    # (0.5%) sits BELOW the first step rung (step_young 0.6%), so a modest peak
    # in [arm, first_step) armed the ladder but the step-based level was 0 and
    # the lock came out negative, locking nothing — a +0.59% peak rode back to a
    # small loss (HBARUSDT). Once the high-water profit reaches the arm
    # threshold this guarantees AT LEAST this much locked profit (a breakeven-
    # plus-sliver floor) while price is still elevated, so the stop ratchets to
    # at least breakeven. The normal step-based lock takes over the moment a
    # real rung is crossed (it is larger and wins under highest-stop-wins). The
    # gateway min-distance rule is NOT loosened — breakeven is reached by
    # locking earlier, not by placing a stop on noise. PROVISIONAL pending
    # truthful PnL: 0.05 is intentionally conservative (a breakeven sliver)
    # versus blueprint 2.1's +0.2% first-lock, to avoid over-tightening a modest
    # winner before real after-cost data justifies a larger floor. A value <= 0
    # is the clean off-switch: it disables the floor and restores the old
    # behaviour (a modest in-the-gap peak locks nothing).
    ladder_breakeven_lock_pct: float = 0.05
    # Fix 3 (2026-06-05) — dead-band give-back trail. The ladder arms at
    # min_profit_to_arm_ladder_pct (0.2%) but the first step rung is at
    # ladder_step_pct (0.6% young), so a peak in [0.2%, 0.6%) had level=0 and
    # locked only the breakeven sliver (ladder_breakeven_lock_pct) — giving back
    # the WHOLE modest peak (live: IMXUSDT +$21 peak booked -$5; ENAUSDT +$11 ->
    # -$0.44). When > 0, a peak inside that dead band trails a floor this far
    # below the high-water peak: lock = max(be_lock, peak - giveback). So a
    # +0.22% peak with giveback 0.10 banks +0.12% instead of breakeven.
    # Monotonic (peak is high-water), tighten-only, bounded below by the
    # breakeven sliver; the normal step lock takes over the moment a real rung is
    # crossed. Gateway R1 tighten-only + R2 min-distance still apply (cannot sit
    # on noise). <= 0 restores the old breakeven-only behaviour. PROVISIONAL —
    # 0.10 is a starting point; widen if it exits modest winners on noise.
    ladder_deadband_giveback_pct: float = 0.10
    # Finding A (2026-06-08) — fee-aware breakeven/first-lock. The breakeven and
    # dead-band floor offsets were expressed in GROSS price and did not clear the
    # round-trip taker fee (~0.11% of notional), so a gross-positive "breakeven"
    # lock still booked a small NET loss after fees — the dominant small-loss
    # mechanism in the live record (e.g. NEAR locked +0.035% -> net -$4.57; ATOM
    # locked +0.186% -> +$1.77 win; the fee hurdle is the dividing line). When a
    # sub-fee floor would lock AND the trade's peak has cleared this hurdle, the
    # floor is raised to this fee-clearing level so a "breakeven" lock is
    # net-breakeven, not a fee-loss. Must be >= the round-trip taker fee
    # (_BYBIT_TAKER_FEE_PER_SIDE 0.055% x 2 = 0.11%); 0.13 = the fee plus a small
    # margin. Never lifts a lock above the peak the trade reached (no unreachable
    # stop), and the step locks (level - offset, >= ~0.3%) already clear the fee
    # so they are untouched. <= 0 is the clean off-switch (restores gross locks).
    # PROVISIONAL pending truthful PnL; a tuning starting point, not gospel.
    ladder_lock_fee_clearance_pct: float = 0.13
    # Issue 1 (CALL_A exploit/fetch, 2026-06-05) — decoupled micro-floor arm.
    # The ladder/breakeven floor armed at min_profit_to_arm_ladder_pct (0.2%),
    # but the SAME 0.2% also drives the one-way GRADUATION_LATCH that hands
    # authority from the loss-cutting system to the profit system, so it could
    # not simply be lowered without stripping the loss-side spike/cap/stall
    # protection on a trade that only briefly poked green. The evidence: most
    # losers peak under +0.2% (median ~+0.07%) and round-trip because the floor
    # never arms. This SEPARATE, lower arm engages ONLY the breakeven/dead-band
    # floor branch (capturing the small green where the gateway min-distance
    # physically allows, at-least-breakeven elsewhere via the R2 carve-out)
    # while GRADUATION_LATCH keeps reading min_profit_to_arm_ladder_pct (0.2%),
    # so loss-cutting authority is retained until genuine +0.2%. The micro arm
    # is bounded to never exceed the graduation arm. Set micro_floor_arm_pct
    # equal to min_profit_to_arm_ladder_pct to restore the old single-arm
    # behaviour (its own tuning off-switch). Tuning starting point.
    micro_floor_arm_pct: float = 0.10
    # F2 (fee-scratch churn, 2026-06-09) — fee-aware micro-floor arm. The micro
    # arm above (0.10%) AND the breakeven lock (ladder_breakeven_lock_pct, 0.05%)
    # both sit BELOW the round-trip taker fee (~0.11%), and the fee-aware LIFT
    # (ladder_lock_fee_clearance_pct, 0.13%) only fires when peak > 0.13%. So a
    # trade peaking in [micro_floor_arm_pct, ladder_lock_fee_clearance_pct) arms a
    # breakeven stop that is never lifted, locks sub-fee, and books a guaranteed
    # NET FEE LOSS when a tiny pullback taps it (the proven fee-scratch mechanism:
    # tape stop-distance collapses to a ~0.09% median at close, close_pnl ==
    # close_dist). When this flag is True, the effective arm becomes
    # max(micro_floor_arm_pct, ladder_lock_fee_clearance_pct) (still clamped to the
    # graduation arm min_profit_to_arm_ladder_pct), so the floor does NOT arm — and
    # pull the stop to a sub-fee breakeven — until the peak has cleared the SAME
    # fee hurdle the lift uses; below that the ladder no-ops and the trade keeps
    # its original wider stop (room to breathe), with loss-cutting authority
    # retained. Reuses the existing fee-clearance value (no inline fee constant),
    # consistent with the Finding-A exit lift. Default OFF (behaviour-preserving):
    # F2 must be measured against truthful PnL, available only after the pending
    # F5/F1 live restart. Also off via ladder_lock_fee_clearance_pct <= 0.
    micro_floor_arm_fee_aware_enabled: bool = False
    # Item 2.2 / O6 / F12 (2026-06-03) — jump the breakeven floor on the arming
    # tick. The ladder source already bypasses the gateway R3 max-step, but the
    # sniper still skips a write while inside the gateway R4 rate-limit window
    # (30s), so the FIRST breakeven lock can be deferred up to 30s — a modest
    # peak that fades inside that catch-up window round-trips despite having
    # armed the ladder (Finding 12). When true, the single arming tick (the
    # not-armed -> armed transition of the zero-crossing floor) also bypasses the
    # rate-limit so the floor lands immediately, in one move, as tight as the
    # gateway min-distance allows. R1 tighten-only and R2 min-distance still
    # apply, so it can never loosen or sit on noise; the bypass is one-shot per
    # position (subsequent ratchets use the normal cadence). Gated additionally
    # by ladder_breakeven_lock_pct > 0 (no floor, nothing to jump). Set false to
    # revert to the rate-limited first lock while keeping the floor. PROVISIONAL.
    ladder_floor_jump_on_arm: bool = True
    # F6 (2026-06-09) — first step-rung lock jump. ladder_floor_jump_on_arm above
    # covers ONLY the zero-crossing breakeven floor. On a FAST young pop the price
    # clears the first real step rung before the floor ever gets its turn, so the
    # FIRST step-rung lock (a real guaranteed-profit lock, breakeven_floor False)
    # is the one delayed up to the 30 s rate-limit window — the choppy-capture
    # collapse. When true, that first step-rung lock also joins the urgent lane
    # and lands in one move. One-shot per position via its own flag, distinct from
    # the breakeven-floor jump (the two are mutually exclusive on any tick). R1
    # tighten-only and R2 min-distance still apply, so it only removes the
    # rate-limit wait on the first real lock — it can never loosen or sit on
    # noise. Default OFF; ships inert until enabled. Observable via the
    # LADDER_FIRST_LOCK_JUMP sentinel. PROVISIONAL pending live observation.
    ladder_first_lock_jump_enabled: bool = False

    # ── Technique 3: time-decay master dial shape ──
    # The fade transition centres near this age (data: ~22-min peak). Used for
    # documentation now; a curve bend may be added later only if real data
    # justifies it (blueprint 4.4 — simple linear glide first).
    peak_minutes: float = 22.0
    # Fallback deadline (minutes) for the time dial when a position has no
    # registered TradePlan (e.g. externally-opened). The analyzed working
    # maximum hold was ~50 minutes (blueprint 4.5).
    default_deadline_minutes: float = 50.0
    # PF/LC Top-15 Problem 2.5 — when the watchdog extends a near-flat loser's
    # deadline, the time dial's age fraction (age / deadline) drops, so every
    # dialed value slides back toward its looser young anchor. On the stop paths
    # R1 tighten-only blocks any real harm, but non-stop force-close thresholds
    # (the dialed stall_min_age_fraction, the cap percent, the structure buffer)
    # DO re-loosen, making the stall-exit more patient on an already-stalling
    # loser — a divergence from the blueprint's "tighten to maximum at the
    # deadline, never re-widen". The audit measured the NET dollar effect as a
    # near-wash, so this is shipped OFF for the operator to decide. When true,
    # the dial is frozen on the ORIGINAL (pre-extension) deadline, so the
    # extension grants grace on the close-timer without re-loosening protection.
    dial_freeze_on_original_deadline_enabled: bool = False

    # ── Safety stop (loss cap for non-climbers / naked-position sweeper) ──
    # Constant (NOT time-dialed). Sits inside the watchdog -3% hard stop so it
    # acts first. Used by the Phase 6 sweeper when a position has no stop.
    safety_stop_pct: float = 2.5

    # ── ATR-zero hardening fallback (consumed in Phase 2) ──
    # When live ATR reads zero AND entry-ATR is also unavailable, the trail
    # falls back to this percent-of-price distance so it never disappears.
    atr_zero_fallback_pct: float = 0.5

    # ── Phase 5: Full reconciliation of the watchdog winner-cutters ──
    # Each is additionally gated by `enabled` and independently revertible
    # (flip false to restore that legacy watchdog path).
    # Ride a still-profitable trade past its deadline on the sniper's tight
    # trail instead of the SENTINEL hard-close (blueprint 4.5).
    ride_winner_past_deadline: bool = True
    # Skip the watchdog +1.5%-past-half-time profit-take close so the spine's
    # trailing SL captures winners instead of a hard profit cap.
    subordinate_profit_take: bool = True
    # Fully disable the watchdog percentage trail (activation, SL pushes, and
    # the trail-exit close) so the sniper spine is the SOLE trailing-SL writer.
    subordinate_watchdog_trail_exit: bool = True

    # ── Phase 6: safety stop / naked-position sweeper ──
    # The safety stop (safety_stop_pct off entry) is always a candidate in the
    # spine. A naked position (no exchange stop) is ALWAYS given one when the
    # system is enabled (fills the confirmed no-naked gap). When
    # safety_floor_reassert is true, the floor is also re-asserted on a
    # position whose existing stop is LOOSER than the floor (tighten-only) —
    # set false to only fill fully-naked positions and never override a
    # brain-set wider stop.
    safety_floor_reassert: bool = True


@dataclass
class LossCuttingSettings:
    """Loss-Cutting System — centralized, tuning-ready protective-exit parameters.

    The protective half of the position-management engine (companion to
    ProfitFetchingSettings). Integrated into the SAME ProfitSniper spine and
    SLGateway. See LOSS_CUTTING_SYSTEM_MASTER_BLUEPRINT.md +
    IMPLEMENT_LOSS_CUTTING_SYSTEM.md.

    Two dials drive every value: trade age (the shared TimeDial slides each
    ``*_young`` anchor toward its ``*_old`` anchor as the trade ages 0 -> its
    per-trade deadline) AND coin volatility (ATR-scaled — the dialed multiples
    are multiplied by the effective ATR in the sniper, exactly as the profit
    side does). EVERY value is a STARTING POINT for tuning, not a final number —
    honest loss-reduction tuning waits for truthful after-cost PnL (blueprint
    Part 8). Behavioral correctness is what this build verifies.

    Authority split (operator directive): when PnL >= 0 the profit-fetching
    system manages the position; when PnL < 0 this system has authority. The
    boundary is latched on ``peak_pnl_pct`` at the profit ladder's arm threshold
    to avoid flapping around zero.
    """

    # Master switch — operator chose ON by default (mirrors profit-fetching).
    enabled: bool = True

    # ── Per-technique enable flags (each independently revertible) ──
    enable_atr_initial_stop: bool = True
    enable_hard_cap: bool = True
    enable_stall_exit: bool = True
    enable_structure_stop: bool = True
    enable_winprob_observe: bool = True
    enable_spike_stop: bool = True
    enable_history_recovery: bool = True
    # Issue 2.5 (2026-06-07): one-way graduation latch re-arm. Once a trade
    # graduates to the profit system (peak >= arm) the loss-cutting block is
    # gated off for the trade's life; if it then craters the tightening cap never
    # returns. When enabled, re-arm the loss-cutting block ONCE per position when
    # a graduated trade has dropped to a real loss (current pnl_pct <=
    # -graduation_crater_loss_pct). One-shot (cannot whipsaw), and a trade that
    # keeps climbing never craters, so a genuine winner is never cut. Default OFF
    # pending live observation.
    graduation_crater_rearm_enabled: bool = False
    graduation_crater_loss_pct: float = 0.5
    # Phase 3 — volatility-based entry sizing. NOT IMPLEMENTED (operator decision
    # 2026-05-31: keep brain-authoritative sizing intact and bound loss via the
    # percent-of-notional cap instead of shrinking positions). Reserved flag; no
    # code reads it. Leave False.
    volatility_entry_sizing_enabled: bool = False

    # ── The sacred hard cap: min(dollar_ceiling, pct_of_notional) ──
    # Operator decision 2026-05-31: the PERCENT-of-notional is the normal binding
    # constraint (a sane, non-strangling stop that scales with size and keeps the
    # brain's sizing intact); the dollar ceiling only bounds the CATASTROPHIC
    # worst case on a very large position. The percent tightens inward with age;
    # the ceiling is fixed and never loosens. With ceiling $75 + percent 2.5%->1%
    # a $1000 position caps at $25/$10, a $3000 at $75/$30, and only >$3000 is
    # bounded by the ceiling. (A literal $5 ceiling strangles a $1000-3000
    # position at 0.17-0.5% and was rejected — it would have required shrinking
    # positions, undoing brain-authoritative sizing.)
    cap_dollar_ceiling: float = 75.0
    cap_pct_of_notional_young: float = 2.5
    cap_pct_of_notional_old: float = 1.0
    # Finding N (2026-06-08) — net-aware cap. The cap distance bounds the GROSS
    # price loss; the round-trip taker fee pushes the realized NET past the
    # ceiling (live: NEAR gross ws_net -74.69 ~= the $75 cap, but realized NET
    # -81.24, ~8% over). Subtracting this round-trip fee percent from the gross
    # cap budget (applied to BOTH the force-close threshold and the placed cap
    # SL) makes the realized net land at or under the ceiling. TIGHTENS gross so
    # net is bounded; it NEVER loosens the cap guarantee. Must equal the
    # round-trip taker fee (_BYBIT_TAKER_FEE_PER_SIDE 0.055% x 2 = 0.11%); 0 is
    # the clean off-switch (restores the gross cap). PROVISIONAL — a starting point.
    cap_round_trip_fee_pct: float = 0.11
    # When the cap distance falls inside the gateway min-distance (un-placeable
    # as an SL on high/extreme coins), enforce the cap by a coordinator force-
    # close when realized PnL reaches the cap distance, never a clamped SL.
    force_close_when_cap_unplaceable: bool = True
    # Finding 5 (2026-06-02) — cap slippage buffer. The exchange stop is a
    # market-trigger stop (slTriggerBy LastPrice); on a fast adverse move it
    # fills PAST its trigger, so a stop placed exactly at the cap distance can
    # realize a loss slightly over the ceiling (one breach in 314: BCHUSDT
    # closed ~7 over the $75 ceiling). This pulls the cap-DERIVED stop trigger
    # this percent of the cap distance INSIDE the ceiling so expected slippage
    # still lands within it. It is applied ONLY to stop PLACEMENT (the initial
    # ATR stop's cap clamp and the cap SL candidate); the sacred force-close
    # still fires at the true ceiling. PROVISIONAL pending truthful PnL: 0.5 is
    # a conservative starting point — a fast-gap fill can still overshoot (no
    # buffer fully eliminates market-stop slippage), which is why the
    # CAP_SLIPPAGE_OBSERVED sentinel and the force-close remain the backstops.
    # Raise it if the observed overshoot trend warrants; 0 disables the buffer.
    cap_slippage_buffer_pct: float = 0.5

    # ── Technique 1: ATR-based initial stop (placed the second a trade opens) ──
    # Loss-owned mirror of the profit ATR multiple so the two tune independently.
    atr_initial_multiple_young: float = 3.0
    atr_initial_multiple_old: float = 1.0

    # ── Technique 2: time-based stall-exit with the signs-of-life veto ──
    # The stall only fires past this fraction of the deadline. Young anchor > 1
    # means a young trade is NEVER stall-cut; old anchor ~0.55 fires past ~55%.
    stall_min_age_fraction_young: float = 1.1
    stall_min_age_fraction_old: float = 0.55
    # Signs of life that VETO the cut (spare a slightly-building late-bloomer):
    # a recent peak_pnl_pct rise of at least this many percent within the
    # lookback, OR a ticks-in-profit ratio at least this high, OR pnl improving.
    #
    # Finding 2 (2026-06-02) — THE VETO-AND-RECOVERY INTERACTION LEVER. Live
    # monitoring showed this veto is generous (it spared far more cuts than it
    # made), so some flat-faders rode to a deadline close at a moderate loss
    # (ENAUSDT -41) while the SAME lenient path let the final-phase recovery
    # trail catch others small (ATOMUSDT -6). The real lever is the INTERACTION
    # of this veto leniency with the loss-side recovery-trail tightness
    # (recovery_bounce_trail_atr_loss_side, below) — tightening both, in the
    # same direction, turns more deadline bleeds into small recovery catches.
    # stall_signs_of_life_profit_ratio is nudged 0.20 -> 0.25 (a trade must show
    # a touch more time-in-profit to be spared). The three-condition OR is kept
    # intact (Rule 9 — the late-bloomer protection is sacred). All values here
    # are PROVISIONAL: how much to tighten can only be set honestly against
    # truthful after-cost PnL. Watch LOSS_STALL_VETO (now with veto_count),
    # LOSS_STALL_VETO_BUDGET, and LOSS_RECOVERY to tune the interaction.
    stall_signs_of_life_peak_improve_pct: float = 0.15
    stall_signs_of_life_profit_ratio: float = 0.25
    stall_signs_of_life_lookback_ticks: int = 24
    # PF/LC Top-15 Problem 2.1 — the stall signs-of-life "building" veto used the
    # CUMULATIVE lifetime in-profit ratio (PositionProfitState.profit_ratio), so
    # a trade that looked healthy early was spared long after it turned bad
    # (stale evidence keeping dying trades alive). When this is true, the veto
    # instead uses a WINDOWED in-profit ratio over the last
    # stall_signs_of_life_lookback_ticks ticks (same 0.25 threshold). Default
    # off: this can cut a late-bloomer, so it must be offline-validated against
    # the historical record before enabling (Rule 9). The cumulative ratio is
    # still used by the recovery-trail width selection (a correct, separate use).
    stall_veto_windowed_profit_ratio_enabled: bool = False
    # PF/LC Top-15 Problem 2.2 — the "improving" reprieve fired on a SINGLE tick
    # of upward movement versus the immediately-prior tick, so ordinary market
    # noise repeatedly rescued a dying trade. When enabled, the reprieve instead
    # requires SUSTAINED improvement: the current PnL must sit at least
    # stall_signs_of_life_improving_floor_bps (in basis points of PnL) above the
    # LOWEST of the last stall_signs_of_life_improving_lookback_ticks ticks — a
    # genuine recovery from a recent low, not a one-tick blip. Default off
    # (tightening the reprieve cuts more, so offline-validate the late-bloomer
    # pool first, Rule 9). Reuses the windowed PnL history from Problem 2.1.
    stall_signs_of_life_sustained_improving_enabled: bool = False
    stall_signs_of_life_improving_lookback_ticks: int = 3
    stall_signs_of_life_improving_floor_bps: float = 2.0
    # Finding 2 — observability only. When a single position has been spared by
    # the veto this many times (each ~one minute apart), emit a one-shot
    # LOSS_STALL_VETO_BUDGET so a notably-lenient sparing is visible live. It
    # does NOT force a cut (the late-bloomer protection is preserved); it is a
    # watch flag for tuning the leniency. ENAUSDT hit ~8 before its deadline
    # bleed. 0 disables the flag.
    stall_veto_budget_warn: int = 8
    # Past this fraction of the deadline the stall-exit yields to the watchdog's
    # 95%-time loser timeout (so the two never race for the very-late cut).
    stall_tail_yield_fraction: float = 0.95

    # ── Technique 3: structure-based stop (just beyond X-RAY invalidation) ──
    # Buffer beyond the invalidation level, in ATR units; shrinks with age.
    structure_buffer_atr_young: float = 0.50
    structure_buffer_atr_old: float = 0.10

    # ── Technique 4: win-probability exit (COORDINATED, not duplicated) ──
    # The actual p_win force-close lives in the watchdog time-decay path (the
    # single owner; it logs its own cut). This is its tuning home: when loss-
    # cutting is enabled, the watchdog sources near_certain_loser_p_win from
    # winprob_cut_threshold_young (0.10 by default — identical to the existing
    # value, so a no-op until tuned) and logs LOSS_WINPROB_COORD at boot. Only
    # the _young anchor is wired today, so the cut threshold is a single static
    # value; the _old anchor is reserved for a future age-rising variant where
    # the time-decay calculator would consult the loss dial per tick. The sniper
    # does NOT add a second cutter. Widening is a truthful-PnL tuning decision.
    winprob_cut_threshold_young: float = 0.10
    winprob_cut_threshold_old: float = 0.20

    # ── Technique 5: volatility-spike-down catastrophe stop (time-INDEPENDENT) ──
    # NOT time-dialed (blueprint Rule 8): a crash is dangerous at any age. On a
    # violent adverse move over the window (>= this many ATR units) the spike
    # force-CLOSES the position (the fastest catastrophe exit); it does not place
    # an SL.
    spike_atr_move_mult: float = 2.5
    spike_window_seconds: float = 30.0
    # PF/LC Top-15 Problem 3.4 — opening-seconds carve-out so the now-always-on
    # spike (Problem 1.2) does not over-fire on a very young low-volatility
    # trade, where a few opening ticks make a modest settling wiggle look like a
    # crash in ATR units. For the first ``spike_young_opening_seconds`` the spike
    # requires the wider ``spike_atr_move_mult_opening``; after that it reverts to
    # ``spike_atr_move_mult``. The opening multiple is still a genuine-crash
    # threshold (3.8 ATR is a real catastrophe), so crash protection is intact.
    spike_young_opening_seconds: float = 12.0
    spike_atr_move_mult_opening: float = 3.8

    # ── Final-phase history-aware recovery (tight bounce-capture) ──
    # The final phase begins past this fraction of the deadline.
    recovery_final_fraction: float = 0.80
    # ticks_in_profit/ticks_total at/above this = mostly-profit-side (more room).
    recovery_profit_side_ratio: float = 0.50
    # Bounce trail distance in ATR units: wider for a mostly-profit-side trade,
    # tight for a mostly-loss-side trade (captures near least-loss).
    # Finding 2 (2026-06-02) — the loss-side half of the veto-and-recovery
    # interaction lever (see stall_signs_of_life_profit_ratio above). Nudged
    # 0.5 -> 0.40 so a vetoed bleeder that reaches the recovery window is caught
    # tighter (more ATOM-like small catches, fewer ENA-like deadline bleeds).
    # PROVISIONAL pending truthful PnL.
    recovery_bounce_trail_atr_profit_side: float = 1.5
    recovery_bounce_trail_atr_loss_side: float = 0.40


@dataclass
class Settings:
    """Top-level settings container holding all sub-configurations."""
    general: GeneralSettings = field(default_factory=GeneralSettings)
    bybit: BybitSettings = field(default_factory=BybitSettings)
    # Bybit demo (paper-money) execution adapter — additive 3rd mode
    # alongside Shadow and live Bybit. See BybitDemoSettings docstring.
    bybit_demo: BybitDemoSettings = field(default_factory=BybitDemoSettings)
    finnhub: FinnhubSettings = field(default_factory=FinnhubSettings)
    reddit: RedditSettings = field(default_factory=RedditSettings)
    altdata: AltDataSettings = field(default_factory=AltDataSettings)
    # XRAY phase-4 fix — TA engine settings (currently just confidence
    # smoothing alpha). See TASettings docstring.
    ta: TASettings = field(default_factory=TASettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    workers: WorkerSettings = field(default_factory=WorkerSettings)
    brain: BrainSettings = field(default_factory=BrainSettings)
    stage2: Stage2Settings = field(default_factory=Stage2Settings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    alerts: AlertSettings = field(default_factory=AlertSettings)
    watchdog: WatchdogSettings = field(default_factory=WatchdogSettings)
    sl_gateway: SLGatewaySettings = field(default_factory=SLGatewaySettings)
    scanner: ScannerSettings = field(default_factory=ScannerSettings)
    universe: UniverseSettings = field(default_factory=UniverseSettings)
    regime: RegimeSettings = field(default_factory=RegimeSettings)
    strategy_engine: StrategyEngineSettings = field(default_factory=StrategyEngineSettings)
    pnl_targets: PnLTargetSettings = field(default_factory=PnLTargetSettings)
    leverage: LeverageSettings = field(default_factory=LeverageSettings)
    optimizer: OptimizerSettings = field(default_factory=OptimizerSettings)
    factory: FactorySettings = field(default_factory=FactorySettings)
    backtesting: BacktestSettings = field(default_factory=BacktestSettings)
    trial: TrialSettings = field(default_factory=TrialSettings)
    portfolio: PortfolioSettings = field(default_factory=PortfolioSettings)
    telegram_interactive: TelegramInteractiveSettings = field(default_factory=TelegramInteractiveSettings)
    enforcer: EnforcerSettings = field(default_factory=EnforcerSettings)
    # CALL_B Framing Fix Phase 5B (2026-05-06) — sentiment consumption gate.
    sentiment: SentimentSettings = field(default_factory=SentimentSettings)
    mode4: Mode4Settings = field(default_factory=Mode4Settings)
    # Profit-Fetching Exit System (2026-05-29) — centralized time-dial /
    # ladder / safety-stop tunables. See ProfitFetchingSettings docstring.
    profit_fetching: ProfitFetchingSettings = field(
        default_factory=ProfitFetchingSettings,
    )
    # Loss-Cutting System (2026-05-31) — the protective half of the engine;
    # centralized time+volatility-dialed cap / stall / structure / spike /
    # recovery tunables. See LossCuttingSettings docstring.
    loss_cutting: LossCuttingSettings = field(
        default_factory=LossCuttingSettings,
    )
    # Dynamic Adaptive Exit System (2026-06-15) — R-and-fee-derived exit
    # geometry coefficients. Dormant until enabled. See AdaptiveExitSettings.
    adaptive_exit: AdaptiveExitSettings = field(
        default_factory=AdaptiveExitSettings,
    )
    # Layer 4 Realignment (2026-05-06) — Profit Sniper protection knobs
    # (min-age guardrail in Phase 1A; PnL-aware guards added in Phase 1C).
    layer4_sniper: Layer4SniperSettings = field(default_factory=Layer4SniperSettings)
    fund_manager: FundManagerSettings = field(default_factory=FundManagerSettings)
    mcp: MCPSettings = field(default_factory=MCPSettings)
    # Phase 3 (P0-2 Price Divergence) — see PriceFreshnessSettings docstring.
    price: PriceFreshnessSettings = field(default_factory=PriceFreshnessSettings)
    # Phase 23 (Y-22) — MCP client pool. Disabled by default; turn on
    # per consumer to migrate off the one-shot stdio storm.
    mcp_pool: "MCPPoolSettings" = field(
        default_factory=lambda: MCPPoolSettings()
    )
    tias: TIASSettings = field(default_factory=TIASSettings)
    apex: APEXSettings = field(default_factory=APEXSettings)
    sentinel: SentinelSettings = field(default_factory=SentinelSettings)
    structure: StructureSettings = field(default_factory=StructureSettings)
    volatility_profile: VolatilityProfileSettings = field(default_factory=VolatilityProfileSettings)
    time_decay: TimeDecaySettings = field(default_factory=TimeDecaySettings)
    # Phase 1 of Layer 1 restructure — log tag + cycle tracker knobs.
    observability: ObservabilitySettings = field(default_factory=ObservabilitySettings)
    # Phase 2 (post-Layer-1 fix) — LayerManager boot deadline + state sync.
    layer_manager: LayerManagerSettings = field(default_factory=LayerManagerSettings)
    # Phase 11 (dead-workers fix) — WorkerLivenessWatchdog tunables.
    worker_liveness: WorkerLivenessSettings = field(default_factory=WorkerLivenessSettings)
    # Phase 1 (output-quality) — SignalGenerator multi-source classification.
    signal_generator: SignalGeneratorSettings = field(default_factory=SignalGeneratorSettings)
    # Phase 5 (output-quality) — CoinPackage validator thresholds.
    coin_package_validator: CoinPackageValidatorSettings = field(
        default_factory=CoinPackageValidatorSettings,
    )

    _instance: "Settings | None" = field(default=None, init=False, repr=False)

    @classmethod
    def load(
        cls,
        config_path: str = "config.toml",
        env_path: str = ".env",
    ) -> "Settings":
        """Load configuration from config.toml and .env, returning a singleton.

        Args:
            config_path: Path to TOML config file.
            env_path: Path to .env file.

        Returns:
            Fully populated Settings instance.

        Raises:
            ConfigError: If config.toml cannot be read or parsed.
        """
        if cls._instance is not None:
            return cls._instance

        instance = cls._load_fresh(config_path, env_path)
        cls._instance = instance
        return instance

    @classmethod
    def _load_fresh(
        cls,
        config_path: str = "config.toml",
        env_path: str = ".env",
    ) -> "Settings":
        """Load config without caching (useful for testing).

        Args:
            config_path: Path to TOML config file.
            env_path: Path to .env file.

        Returns:
            New Settings instance.
        """
        # Load .env first so env vars are available
        load_dotenv(env_path, override=True)

        # Load TOML config
        toml_data: dict[str, Any] = {}
        config_file = Path(config_path)
        if config_file.exists():
            try:
                with open(config_file, "rb") as f:
                    toml_data = tomllib.load(f)
            except Exception as e:
                raise ConfigError(
                    f"Failed to parse {config_path}: {e}",
                    details={"path": config_path},
                )
        else:
            # No config file — use all defaults
            pass

        # Build each settings section from TOML + env overrides
        general = _build_general(toml_data.get("general", {}))
        bybit = _build_bybit(toml_data.get("bybit", {}))
        bybit_demo = _build_bybit_demo(toml_data.get("bybit_demo", {}))
        finnhub = _build_finnhub(toml_data.get("finnhub", {}))
        reddit = _build_reddit(toml_data.get("reddit", {}))
        altdata = _build_altdata(toml_data.get("altdata", {}))
        ta_cfg = _build_ta(toml_data.get("ta", {}))
        database = _build_database(toml_data.get("database", {}))
        workers = _build_workers(toml_data.get("workers", {}))
        brain = _build_brain(toml_data.get("brain", {}))
        stage2 = _build_stage2(toml_data.get("stage2", {}))
        risk = _build_risk(toml_data.get("risk", {}))
        alerts = _build_alerts(toml_data.get("alerts", {}))
        watchdog = _build_watchdog(toml_data.get("watchdog", {}))
        sl_gateway_cfg = _build_sl_gateway(toml_data.get("sl_gateway", {}))
        scanner = _build_scanner(toml_data.get("scanner", {}))
        universe = _build_universe(toml_data.get("universe", {}))
        regime = _build_regime(toml_data.get("regime", {}))
        strategy_engine = _build_strategy_engine(toml_data.get("strategy_engine", {}))
        pnl_targets = _build_pnl_targets(toml_data.get("pnl_targets", {}))
        leverage_cfg = _build_leverage(toml_data.get("leverage", {}))
        optimizer = _build_optimizer(toml_data.get("optimizer", {}))
        factory = _build_factory(toml_data.get("factory", {}))
        backtesting = _build_backtesting(toml_data.get("backtesting", {}))
        trial_cfg = _build_trial(toml_data.get("trial", {}))
        portfolio = _build_portfolio(toml_data.get("portfolio", {}))
        telegram_interactive = _build_telegram_interactive(toml_data.get("telegram_interactive", {}))
        enforcer_cfg = _build_enforcer(toml_data.get("enforcer", {}))
        sentiment_cfg = _build_sentiment(toml_data.get("sentiment", {}))
        mode4_cfg = _build_mode4(toml_data.get("mode4", {}))
        # Profit-Fetching Exit System (2026-05-29) — centralized tunables.
        profit_fetching_cfg = _build_profit_fetching(
            toml_data.get("profit_fetching", {}),
        )
        # Loss-Cutting System (2026-05-31) — centralized protective-exit tunables.
        loss_cutting_cfg = _build_loss_cutting(
            toml_data.get("loss_cutting", {}),
        )
        # Dynamic Adaptive Exit System (2026-06-15) — R-and-fee-derived geometry.
        adaptive_exit_cfg = _build_adaptive_exit(
            toml_data.get("adaptive_exit", {}),
        )
        # Layer 4 Realignment (2026-05-06) — Profit Sniper protection knobs.
        # Lives under ``[layer4.sniper]`` in config.toml so future Layer 4
        # protection settings can fan out under the same ``[layer4.*]``
        # namespace without colliding with the legacy ``[mode4]`` section.
        layer4_sniper_cfg = _build_layer4_sniper(
            toml_data.get("layer4", {}).get("sniper", {}),
        )
        fund_manager_cfg = _build_fund_manager(toml_data.get("fund_manager", {}))
        mcp = _build_mcp(toml_data.get("mcp", {}))
        tias_cfg = _build_tias(toml_data.get("tias", {}))
        apex_cfg = _build_apex(toml_data.get("apex", {}))
        sentinel_cfg = _build_sentinel(toml_data.get("sentinel", {}))
        structure_cfg = _build_structure(toml_data.get("analysis", {}).get("structure", {}))
        volatility_profile_cfg = _build_volatility_profile(
            toml_data.get("analysis", {}).get("volatility_profile", {})
        )
        time_decay_cfg = _build_time_decay(toml_data.get("time_decay", {}))
        # Phase 3 (P0-2 Price Divergence) — load freshness/override knobs.
        price_cfg = _build_price(toml_data.get("price", {}))
        # Phase 23 (Y-22) — MCP client pool config.
        mcp_pool_cfg = _build_mcp_pool(toml_data.get("mcp_pool", {}))
        # Phase 1 of Layer 1 restructure — observability knobs.
        observability_cfg = _build_observability(toml_data.get("observability", {}))
        # Phase 2 (post-Layer-1 fix) — LayerManager boot deadline + state sync.
        layer_manager_cfg = _build_layer_manager(toml_data.get("layer_manager", {}))
        # Phase 11 (dead-workers fix) — WorkerLivenessWatchdog tunables.
        worker_liveness_cfg = _build_worker_liveness(
            toml_data.get("worker_liveness", {}),
        )
        # Phase 1 (output-quality) — SignalGenerator multi-source classification.
        signal_generator_cfg = _build_signal_generator(
            toml_data.get("signal_generator", {}),
        )
        # Phase 5 (output-quality) — CoinPackage validator thresholds.
        coin_package_validator_cfg = _build_coin_package_validator(
            toml_data.get("coin_package_validator", {}),
        )

        return cls(
            general=general,
            bybit=bybit,
            bybit_demo=bybit_demo,
            finnhub=finnhub,
            reddit=reddit,
            altdata=altdata,
            ta=ta_cfg,
            database=database,
            workers=workers,
            brain=brain,
            stage2=stage2,
            risk=risk,
            alerts=alerts,
            watchdog=watchdog,
            sl_gateway=sl_gateway_cfg,
            scanner=scanner,
            universe=universe,
            regime=regime,
            strategy_engine=strategy_engine,
            pnl_targets=pnl_targets,
            leverage=leverage_cfg,
            optimizer=optimizer,
            factory=factory,
            backtesting=backtesting,
            trial=trial_cfg,
            portfolio=portfolio,
            telegram_interactive=telegram_interactive,
            enforcer=enforcer_cfg,
            sentiment=sentiment_cfg,
            mode4=mode4_cfg,
            profit_fetching=profit_fetching_cfg,
            loss_cutting=loss_cutting_cfg,
            adaptive_exit=adaptive_exit_cfg,
            layer4_sniper=layer4_sniper_cfg,
            fund_manager=fund_manager_cfg,
            mcp=mcp,
            tias=tias_cfg,
            apex=apex_cfg,
            sentinel=sentinel_cfg,
            structure=structure_cfg,
            volatility_profile=volatility_profile_cfg,
            time_decay=time_decay_cfg,
            price=price_cfg,
            mcp_pool=mcp_pool_cfg,
            observability=observability_cfg,
            layer_manager=layer_manager_cfg,
            worker_liveness=worker_liveness_cfg,
            signal_generator=signal_generator_cfg,
            coin_package_validator=coin_package_validator_cfg,
        )

    @classmethod
    def reset(cls) -> None:
        """Clear the singleton instance (for testing)."""
        cls._instance = None


# =============================================================================
# Section builders: TOML dict → dataclass, with env var overrides
# =============================================================================

def _build_general(data: dict[str, Any]) -> GeneralSettings:
    return GeneralSettings(
        mode=_env("TRADING_MODE", data.get("mode", "paper")),
        shadow_api_url=data.get("shadow_api_url", "http://127.0.0.1:9090"),
        timezone=data.get("timezone", "UTC"),
        log_level=_env("LOG_LEVEL", data.get("log_level", "INFO")),
        log_dir=data.get("log_dir", "data/logs"),
    )


def _build_bybit(data: dict[str, Any]) -> BybitSettings:
    return BybitSettings(
        testnet=data.get("testnet", True),
        default_symbols=data.get(
            "default_symbols",
            ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"],
        ),
        rate_limit_per_second=data.get("rate_limit_per_second", 10),
        ws_ping_interval=data.get("ws_ping_interval", 20),
        ws_reconnect_delay=data.get("ws_reconnect_delay", 5),
        recv_window=data.get("recv_window", 5000),
        # BYBIT_Mainnet_API_KEY1/_SECRET1 (2026-07-06) is the operator's
        # confirmed READ-ONLY mainnet key (verified via /v5/user/query-api:
        # readOnly=1). Deliberately does NOT fall back to
        # BYBIT_Mainnet_API_KEY/_SECRET (a full trade+wallet-transfer key
        # confirmed readOnly=0) — falling back to that would silently
        # reintroduce a write-capable key into the market-data path if the
        # read-only one is ever unset. Falls back only to the generic
        # BYBIT_API_KEY/_SECRET names (unset today).
        api_key=_env(
            "BYBIT_Mainnet_API_KEY1",
            _env("BYBIT_API_KEY", data.get("api_key", "")),
        ),
        api_secret=_env(
            "BYBIT_Mainnet_API_SECRET1",
            _env("BYBIT_API_SECRET", data.get("api_secret", "")),
        ),
    )


def _build_bybit_demo(data: dict[str, Any]) -> BybitDemoSettings:
    """Build BybitDemoSettings from the optional ``[bybit_demo]`` config section.

    Credentials read from env (``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET``)
    so they never land in config.toml. Default ``enabled=False`` — operator
    must explicitly opt in via config or env override.
    """
    # PnL-truth provenance: validate the close-pnl source so a typo cannot
    # silently fall back to the stale-row path.
    _close_src = str(data.get("close_pnl_source", "ws_exec")).strip().lower()
    if _close_src not in ("ws_exec", "gated", "legacy"):
        raise ConfigError(
            "[bybit_demo].close_pnl_source must be one of "
            f"'ws_exec', 'gated', 'legacy' (got {_close_src!r})"
        )
    return BybitDemoSettings(
        enabled=data.get("enabled", False),
        base_url=data.get("base_url", "https://api-demo.bybit.com"),
        recv_window=int(data.get("recv_window", 5000)),
        timeout_seconds=float(data.get("timeout_seconds", 10.0)),
        retry_attempts=int(data.get("retry_attempts", 5)),
        retry_base_delay_seconds=float(data.get("retry_base_delay_seconds", 0.2)),
        api_key=_env("BYBIT_DEMO_API_KEY", data.get("api_key", "")),
        api_secret=_env("BYBIT_DEMO_API_SECRET", data.get("api_secret", "")),
        close_pnl_source=_close_src,
        close_pnl_provisional=bool(data.get("close_pnl_provisional", True)),
        close_pnl_reconcile=bool(data.get("close_pnl_reconcile", True)),
        close_pnl_reconcile_max_attempts=int(
            data.get("close_pnl_reconcile_max_attempts", 10)
        ),
        close_pnl_reconcile_interval_s=float(
            data.get("close_pnl_reconcile_interval_s", 1.0)
        ),
        close_pnl_reconcile_total_budget_s=float(
            data.get("close_pnl_reconcile_total_budget_s", 30.0)
        ),
        close_pnl_reconcile_max_exit_divergence_pct=float(
            data.get("close_pnl_reconcile_max_exit_divergence_pct", 3.0)
        ),
    )


def _build_finnhub(data: dict[str, Any]) -> FinnhubSettings:
    return FinnhubSettings(
        enabled=data.get("enabled", True),
        rate_limit_per_minute=data.get("rate_limit_per_minute", 60),
        news_categories=data.get("news_categories", ["crypto", "general"]),
        max_articles_per_fetch=data.get("max_articles_per_fetch", 50),
        api_key=_env("FINNHUB_API_KEY", data.get("api_key", "")),
    )


def _build_reddit(data: dict[str, Any]) -> RedditSettings:
    return RedditSettings(
        enabled=data.get("enabled", True),
        subreddits=data.get("subreddits", ["cryptocurrency", "bitcoin", "ethtrader"]),
        max_posts_per_sub=data.get("max_posts_per_sub", 25),
        min_score=data.get("min_score", 10),
        rate_limit_per_minute=data.get("rate_limit_per_minute", 60),
        client_id=_env("REDDIT_CLIENT_ID", data.get("client_id", "")),
        client_secret=_env("REDDIT_CLIENT_SECRET", data.get("client_secret", "")),
        username=_env("REDDIT_USERNAME", data.get("username", "")),
        password=_env("REDDIT_PASSWORD", data.get("password", "")),
    )


def _build_ta(data: dict[str, Any]) -> TASettings:
    """Build TASettings from the optional ``[ta]`` config section."""
    alpha = float(data.get("confidence_ema_alpha", 0.4))
    if not 0.0 < alpha <= 1.0:
        raise ConfigError(
            f"[ta] confidence_ema_alpha must be in (0, 1]; got {alpha}",
            details={"value": alpha},
        )
    return TASettings(
        confidence_ema_alpha=alpha,
        volume_ratio_use_closed_candle=bool(
            data.get("volume_ratio_use_closed_candle", False)
        ),
    )


def _build_altdata(data: dict[str, Any]) -> AltDataSettings:
    return AltDataSettings(
        enabled=data.get("enabled", True),
        fear_greed_interval=data.get("fear_greed_interval", 3600),
        funding_rate_interval=data.get("funding_rate_interval", 300),
        open_interest_interval=data.get("open_interest_interval", 600),
        coingecko_rate_limit_per_minute=data.get("coingecko_rate_limit_per_minute", 10),
    )


def _build_database(data: dict[str, Any]) -> DatabaseSettings:
    return DatabaseSettings(
        path=_env("DATABASE_PATH", data.get("path", "data/trading.db")),
        wal_mode=data.get("wal_mode", True),
        pool_size=data.get("pool_size", 5),
        query_timeout=data.get("query_timeout", 30),
        vacuum_interval=data.get("vacuum_interval", 24),
        kline_save_chunk_size=int(data.get("kline_save_chunk_size", 500)),
        wal_checkpoint_every_n_kline_ticks=int(
            data.get("wal_checkpoint_every_n_kline_ticks", 50)
        ),
        wal_checkpoint_truncate_after_busy_count=int(
            data.get("wal_checkpoint_truncate_after_busy_count", 3)
        ),
        db_lock_wait_threshold_ms=int(data.get("db_lock_wait_threshold_ms", 1000)),
        # Phase conn-pool/p3-9: env override DATABASE_CONCURRENCY_MODEL is
        # retained as a forward-looking knob (e.g. for future per-domain
        # engine selection). Default is "reader_pool"; "single_lock" is
        # rejected by the validator.
        concurrency_model=str(
            _env("DATABASE_CONCURRENCY_MODEL", data.get("concurrency_model", "reader_pool"))
        ),
        reader_pool_size=int(data.get("reader_pool_size", 4)),
    )


def _build_altdata_sweet_spots(data: dict[str, Any]) -> AltDataSweetSpotsSettings:
    """Build AltDataSweetSpotsSettings from [workers.sweet_spots.altdata] TOML.

    Defaults match the corrected Layer 1 chain (funding at 1:45 between
    regime and scanner; OI every 5 min; F&G hourly).
    """
    return AltDataSweetSpotsSettings(
        funding_rates=data.get("funding_rates", "1:45"),
        open_interest_minutes=int(data.get("open_interest_minutes", 5)),
        fear_greed_minutes=int(data.get("fear_greed_minutes", 60)),
        open_interest_interval=str(
            data.get("open_interest_interval", "5min")
        ),
    )


def _build_sweet_spots(data: dict[str, Any]) -> SweetSpotsSettings:
    """Build SweetSpotsSettings from [workers.sweet_spots] TOML section.

    Validation (chain ordering, MM:SS bounds) runs in ``__post_init__``.
    Empty ``data`` yields the corrected-Layer-1 chain defaults so existing
    deployments without the new config section get sensible behavior on
    upgrade — paired with the config.toml addition that ships in the same
    Phase 1 commit.
    """
    return SweetSpotsSettings(
        kline_worker=data.get("kline_worker", "0:30"),
        structure_worker=data.get("structure_worker", "0:45"),
        signal_worker=data.get("signal_worker", "1:00"),
        regime_worker=data.get("regime_worker", "1:15"),
        strategy_worker=data.get("strategy_worker", "1:30"),
        scanner_worker=data.get("scanner_worker", "4:00"),
        window_minutes=int(data.get("window_minutes", 5)),
        altdata=_build_altdata_sweet_spots(data.get("altdata", {})),
    )


def _build_workers(data: dict[str, Any]) -> WorkerSettings:
    return WorkerSettings(
        enabled=data.get("enabled", True),
        market_data_interval=data.get("market_data_interval", 60),
        news_interval=data.get("news_interval", 300),
        reddit_interval=data.get("reddit_interval", 600),
        altdata_interval=data.get("altdata_interval", 300),
        health_check_interval=data.get("health_check_interval", 60),
        max_consecutive_failures=data.get("max_consecutive_failures", 5),
        restart_delay=data.get("restart_delay", 10),
        sweet_spots=_build_sweet_spots(data.get("sweet_spots", {})),
    )


def _build_brain(data: dict[str, Any]) -> BrainSettings:
    return BrainSettings(
        enabled=data.get("enabled", False),
        analysis_interval=data.get("analysis_interval", 1800),
        signal_triggered=data.get("signal_triggered", True),
        min_signal_confidence=data.get("min_signal_confidence", 0.7),
        max_calls_per_hour=data.get("max_calls_per_hour", 10),
        model=data.get("model", "claude-sonnet-4-20250514"),
        max_tokens=data.get("max_tokens", 4096),
        temperature=data.get("temperature", 0.3),
        api_key=_env("ANTHROPIC_API_KEY", data.get("api_key", "")),
        provider=str(data.get("provider", "claude_code")),
        glm_model=str(data.get("glm_model", "@cf/zai-org/glm-5.2")),
        glm_account_id=_env("CLOUDFLARE_ACCOUNT_ID", data.get("glm_account_id", "")),
        glm_api_key=_env("CLOUDFLARE_API_KEY", data.get("glm_api_key", "")),
        glm_timeout_seconds=float(data.get("glm_timeout_seconds", 180.0)),
        glm_max_tokens=int(data.get("glm_max_tokens", 8000)),
        glm_temperature=float(data.get("glm_temperature", 0.3)),
        glm_max_retries=int(data.get("glm_max_retries", 2)),
        strategic_interval=data.get("strategic_interval", 300),
        watchdog_interval=data.get("watchdog_interval", 30),
        claude_cli_timeout_seconds=data.get("claude_cli_timeout_seconds", 300),
        claude_cli_max_retries=data.get("claude_cli_max_retries", 2),
        claude_cli_min_interval=data.get("claude_cli_min_interval", 2.0),
        claude_cli_model=str(data.get("claude_cli_model", "claude-opus-4-7")),
        entry_magnitude_advisory_enabled=data.get(
            "entry_magnitude_advisory_enabled", False
        ),
        # P2-1 (2026-05-13): first-byte deadline. See BrainSettings docstring.
        claude_cli_first_byte_timeout_seconds=int(
            data.get("claude_cli_first_byte_timeout_seconds", 90)
        ),
        # P2-1 (2026-05-13): prewarm pool tuning. See BrainSettings docstring.
        claude_cli_prewarm_max_age_seconds=int(
            data.get("claude_cli_prewarm_max_age_seconds", 900)
        ),
        claude_cli_prewarm_canary_ttl_seconds=int(
            data.get("claude_cli_prewarm_canary_ttl_seconds", 600)
        ),
        claude_cli_prewarm_stats_interval_seconds=int(
            data.get("claude_cli_prewarm_stats_interval_seconds", 300)
        ),
        # Issue 1 (latency, 2026-06-06): CLI flags that cut CALL_A thinking
        # overhead. Defaults off so an absent config reverts to current behaviour.
        claude_cli_effort=str(data.get("claude_cli_effort", "")),
        claude_cli_bare=bool(data.get("claude_cli_bare", False)),
        claude_cli_exclude_dynamic_system_prompt=bool(
            data.get("claude_cli_exclude_dynamic_system_prompt", False)
        ),
        credential_refresh_margin_seconds=int(
            data.get("credential_refresh_margin_seconds", 600)
        ),
        credential_refresh_max_attempts=int(
            data.get("credential_refresh_max_attempts", 3)
        ),
        claude_cli_retry_timeout_backoff_base_seconds=data.get(
            "claude_cli_retry_timeout_backoff_base_seconds", 10
        ),
        prompt_event_buffer_max_events=data.get(
            "prompt_event_buffer_max_events", 20
        ),
        use_packages=bool(data.get("use_packages", True)),
        surface_briefing_fields=bool(data.get("surface_briefing_fields", False)),
        surface_top_n_voters=int(data.get("surface_top_n_voters", 10)),
        consensus_freshness_seconds=int(data.get("consensus_freshness_seconds", 360)),
        brain_target_play_count=int(data.get("brain_target_play_count", 3)),
        brain_preferred_hold_minutes_max=int(data.get("brain_preferred_hold_minutes_max", 25)),
        emit_vote_opposition=bool(data.get("emit_vote_opposition", True)),
        emit_category_split=bool(data.get("emit_category_split", True)),
        emit_direction_disagreement_notes=bool(
            data.get("emit_direction_disagreement_notes", True)
        ),
        fear_greed_components_demote_enabled=bool(
            data.get("fear_greed_components_demote_enabled", True)
        ),
        components_diagnostics_excluded=bool(
            data.get("components_diagnostics_excluded", True)
        ),
        xray_authority_conditional_enabled=bool(
            data.get("xray_authority_conditional_enabled", True)
        ),
        xray_authority_min_score=float(
            data.get("xray_authority_min_score", 45.0)
        ),
        book_tilt_enabled=bool(data.get("book_tilt_enabled", True)),
        book_tilt_small_count=int(data.get("book_tilt_small_count", 2)),
        book_tilt_one_sided_ratio=float(
            data.get("book_tilt_one_sided_ratio", 3.0)
        ),
        quality_skip_thin_vol_ratio=float(
            data.get("quality_skip_thin_vol_ratio", 0.25)
        ),
        quality_skip_heavy_attempts=int(
            data.get("quality_skip_heavy_attempts", 6)
        ),
        session_attempts_enabled=bool(
            data.get("session_attempts_enabled", True)
        ),
        session_liveness_enabled=bool(
            data.get("session_liveness_enabled", True)
        ),
        session_liveness_thin_vol_ratio=float(
            data.get("session_liveness_thin_vol_ratio", 0.25)
        ),
        session_liveness_live_max_thin_share=float(
            data.get("session_liveness_live_max_thin_share", 0.20)
        ),
        session_liveness_thin_min_thin_share=float(
            data.get("session_liveness_thin_min_thin_share", 0.60)
        ),
        emit_direction_perf_in_callb=bool(
            data.get("emit_direction_perf_in_callb", True)
        ),
        emit_recent_loss_context=bool(
            data.get("emit_recent_loss_context", True)
        ),
        recent_loss_lookback_hours=int(
            data.get("recent_loss_lookback_hours", 336)
        ),
        recent_loss_max_lessons=int(data.get("recent_loss_max_lessons", 2)),
        tias_cause_max_chars=int(data.get("tias_cause_max_chars", 120)),
        cold_start_protection=_build_brain_cold_start_protection(
            data.get("cold_start_protection", {})
        ),
    )


def _build_brain_cold_start_protection(
    data: dict[str, Any],
) -> BrainColdStartProtection:
    """Build BrainColdStartProtection from [brain.cold_start_protection] TOML.

    Definitive-fix Phase 6 (2026-04-28). Missing block falls back to
    defaults so existing deployments without the new section keep
    cold-start protection active out of the box.
    """
    if not data:
        return BrainColdStartProtection()
    # Fallbacks mirror the dataclass field defaults (the source of truth) so a
    # deployment that omits a key cannot silently revert to a stricter gate.
    # Issue E12 (2026-05-27) relaxed the two averages to 0.70 / 0.80; the
    # qualified-count was relaxed to 1 in the Phase 7 1D-briefing rollout.
    return BrainColdStartProtection(
        enabled=bool(data.get("enabled", True)),
        min_avg_completeness=float(data.get("min_avg_completeness", 0.70)),
        min_per_package_completeness=float(
            data.get("min_per_package_completeness", 0.75)
        ),
        min_qualified_packages=int(data.get("min_qualified_packages", 1)),
        boot_grace_period_sec=int(data.get("boot_grace_period_sec", 600)),
        boot_grace_completeness=float(data.get("boot_grace_completeness", 0.80)),
    )


def _build_stage2(data: dict[str, Any]) -> Stage2Settings:
    """Build Stage2Settings from [stage2] TOML.

    Stage 2 prompt-richness phased-rollout knobs. Missing block falls
    back to defaults (top_n_to_brain=10, all enable_* flags False) so
    a fresh deployment without [stage2] inherits the wider-candidate-
    set + cap-only behavior.
    """
    if not data:
        return Stage2Settings()
    return Stage2Settings(
        top_n_to_brain=int(data.get("top_n_to_brain", 10)),
        enable_full_layer_block=bool(data.get("enable_full_layer_block", False)),
        enable_zero_two_contract=bool(data.get("enable_zero_two_contract", False)),
        per_coin_direction_enabled=bool(data.get("per_coin_direction_enabled", True)),
        enable_priority_trim=bool(data.get("enable_priority_trim", False)),
        component_precision_decimals=int(
            data.get("component_precision_decimals", 4)
        ),
    )


def _build_flip_tp(data: dict[str, Any]) -> FlipTPSettings:
    """Build FlipTPSettings from a `[risk.flip_tp]` sub-table.

    Mirror of `_build_watchdog`'s nested-block pattern. Missing block →
    dataclass defaults. Used by `_build_risk` to populate
    `RiskSettings.flip_tp`.
    """
    return FlipTPSettings(
        enabled=bool(data.get("enabled", True)),
        hard_ceiling_pct=float(data.get("hard_ceiling_pct", 5.0)),
        fallback_tp_distance_pct=float(
            data.get("fallback_tp_distance_pct", 2.0),
        ),
        structural_buffer_multiplier=float(
            data.get("structural_buffer_multiplier", 1.0),
        ),
    )


def _build_volatility_stop_scaling(
    data: dict[str, Any],
) -> VolatilityStopScalingSettings:
    """Build VolatilityStopScalingSettings from `[risk.volatility_stop_scaling]`.

    Fix 7 (2026-06-10). Missing block -> dataclass defaults (enabled=False), so
    existing deployments are byte-identical until the operator opts in.
    """
    return VolatilityStopScalingSettings(
        enabled=bool(data.get("enabled", False)),
        reference_stop_pct=float(data.get("reference_stop_pct", 1.5)),
        max_cap_pct=float(data.get("max_cap_pct", 5.0)),
        use_profiler_recommended_sl=bool(
            data.get("use_profiler_recommended_sl", True),
        ),
        recommended_sl_scalar=float(data.get("recommended_sl_scalar", 1.0)),
    )


def _build_risk(data: dict[str, Any]) -> RiskSettings:
    # TP-Volume-Closure fix Phase 1B (2026-05-07) — pull the nested
    # [risk.flip_tp] sub-table; missing block falls back to dataclass
    # defaults so existing deployments keep functioning unchanged.
    flip_tp_data = data.get("flip_tp") or {}
    return RiskSettings(
        max_leverage=data.get("max_leverage", 3),
        mandatory_stop_loss=data.get("mandatory_stop_loss", True),
        default_stop_loss_pct=data.get("default_stop_loss_pct", 2.0),
        default_take_profit_pct=data.get("default_take_profit_pct", 4.0),
        min_sl_distance_pct=data.get("min_sl_distance_pct", 1.5),
        max_position_size_pct=data.get("max_position_size_pct", 10.0),
        max_open_positions=data.get("max_open_positions", 5),
        daily_loss_limit_pct=data.get("daily_loss_limit_pct", 5.0),
        max_total_exposure_pct=data.get("max_total_exposure_pct", 50.0),
        max_drawdown_pct=data.get("max_drawdown_pct", 15.0),
        min_order_value_usdt=data.get("min_order_value_usdt", 10.0),
        loss_cooldown_seconds=data.get("loss_cooldown_seconds", 300),
        # Maker-entry experiment (Phase D, 2026-07-07) — see RiskSettings.
        entry_order_type=str(data.get("entry_order_type", "market")),
        entry_limit_timeout_seconds=float(
            data.get("entry_limit_timeout_seconds", 20.0)
        ),
        entry_limit_offset_bps=float(
            data.get("entry_limit_offset_bps", 0.0)
        ),
        xray_dir_flip_threshold_ratio=data.get(
            "xray_dir_flip_threshold_ratio", 3.0,
        ),
        # X-RAY Direction-Flip Switch (IMPLEMENT_XRAY_FLIP_SWITCH,
        # 2026-05-25). Default False per operator decision; the live value
        # comes from [risk] xray_dir_flip_enabled in config.toml.
        xray_dir_flip_enabled=data.get(
            "xray_dir_flip_enabled", False,
        ),
        xray_lock_override_ratio_threshold=data.get(
            "xray_lock_override_ratio_threshold", 10.0,
        ),
        # R3 direction-fix (2026-05-17) — WR-aware override threshold.
        # Operator can tune base / floor / ceiling / window via
        # [risk] section in config.toml. Defaults match the dataclass
        # so existing deployments without these keys are byte-equivalent.
        xray_lock_override_wr_base=data.get(
            "xray_lock_override_wr_base", 10.0,
        ),
        xray_lock_override_wr_floor=data.get(
            "xray_lock_override_wr_floor", 2.0,
        ),
        xray_lock_override_wr_ceiling=data.get(
            "xray_lock_override_wr_ceiling", 15.0,
        ),
        xray_lock_override_wr_window_trades=data.get(
            "xray_lock_override_wr_window_trades", 200,
        ),
        xray_lock_override_wr_window_min=data.get(
            "xray_lock_override_wr_window_min", 30,
        ),
        # P0-2 fix (2026-05-22) — high-conviction protection toggle.
        xray_high_conviction_protection_enabled=data.get(
            "xray_high_conviction_protection_enabled", True,
        ),
        # X-RAY Trade-Suppression Switch (IMPLEMENT_XRAY_SUPPRESS_SWITCH,
        # 2026-05-25). Default False per operator decision; the live value
        # comes from [risk] xray_trade_suppression_enabled in config.toml.
        # False = X-RAY booklogs would-be blocks but does not skip trades.
        xray_trade_suppression_enabled=data.get(
            "xray_trade_suppression_enabled", False,
        ),
        flip_tp=_build_flip_tp(flip_tp_data),
        # Fix 7 (volatility-scaled entry stop, 2026-06-10) — nested sub-table.
        volatility_stop_scaling=_build_volatility_stop_scaling(
            data.get("volatility_stop_scaling") or {},
        ),
    )


def _build_alerts(data: dict[str, Any]) -> AlertSettings:
    return AlertSettings(
        telegram_enabled=data.get("telegram_enabled", False),
        alert_levels=data.get("alert_levels", ["WARNING", "CRITICAL"]),
        daily_summary=data.get("daily_summary", True),
        daily_summary_time=data.get("daily_summary_time", "00:00"),
        max_alerts_per_minute=data.get("max_alerts_per_minute", 10),
        trade_alerts=data.get("trade_alerts", True),
        signal_alerts=data.get("signal_alerts", True),
        error_alerts=data.get("error_alerts", True),
        # Project-scoped names only — no fallback to the generic
        # TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID, which already belong to an
        # unrelated bot (EcomListing Pro) in this operator's shell. Falling
        # back to those would risk silently routing trading alerts through
        # the wrong bot.
        bot_token=_env("Trading_ByBit_Token", data.get("bot_token", "")),
        chat_id=_env("Trading_ByBit_ID", data.get("chat_id", "")),
    )


def _build_watchdog(data: dict[str, Any]) -> WatchdogSettings:
    # Layer 4 Realignment Phase 3.2 (2026-05-06) — pull the nested
    # [watchdog.emergency] sub-table. Missing block falls back to the
    # WatchdogEmergencySettings dataclass defaults so existing
    # deployments without the new section keep functioning.
    emergency_data = data.get("emergency") or {}
    emergency_cfg = WatchdogEmergencySettings(
        session_pnl_threshold_pct=float(
            emergency_data.get("session_pnl_threshold_pct", -5.0),
        ),
        hard_stops_per_hour_threshold=int(
            emergency_data.get("hard_stops_per_hour_threshold", 5),
        ),
    )
    return WatchdogSettings(
        enabled=data.get("enabled", True),
        check_interval_seconds=float(data.get("check_interval_seconds", 10.0)),
        loss_warning_pct=data.get("loss_warning_pct", 1.0),
        trailing_loss_pct=data.get("trailing_loss_pct", 0.5),
        sl_proximity_pct=data.get("sl_proximity_pct", 30.0),
        rapid_move_pct=data.get("rapid_move_pct", 0.5),
        brain_trigger_loss_pct=data.get("brain_trigger_loss_pct", 1.5),
        brain_cooldown_seconds=data.get("brain_cooldown_seconds", 120),
        partial_close_pct=data.get("partial_close_pct", 50.0),
        max_brain_calls_per_hour=data.get("max_brain_calls_per_hour", 10),
        timeout_threshold_pct=float(data.get("timeout_threshold_pct", 95.0)),
        early_exit_enabled=bool(data.get("early_exit_enabled", False)),
        fast_reconcile_seconds=float(data.get("fast_reconcile_seconds", 30.0)),
        strategic_action_min_hold_seconds=float(
            data.get("strategic_action_min_hold_seconds", 300.0),
        ),
        strategic_action_allowed_early_close_reasons=list(
            data.get(
                "strategic_action_allowed_early_close_reasons",
                [
                    "stop loss hit", "sl hit",
                    "take profit hit", "tp hit",
                    "structure invalidated", "setup broken",
                    "regime change", "regime shift",
                    "manual operator close", "manual close",
                    # Mid-Hold Trade Management Fix Audit A6 — see
                    # WatchdogSettings dataclass for rationale.
                    "thesis_invalidation", "thesis invalidated",
                    "THESIS_INVALIDATION", "INVALIDATED",
                ],
            ),
        ),
        emergency=emergency_cfg,
        # Mid-Hold Trade Management Fix Phase 3.4 (2026-05-19) tunables.
        ensemble_flip_detection_enabled=bool(
            data.get("ensemble_flip_detection_enabled", True),
        ),
        ensemble_flip_strong_threshold=float(
            data.get("ensemble_flip_strong_threshold", 4.0),
        ),
        ensemble_flip_dedupe_window_seconds=float(
            data.get("ensemble_flip_dedupe_window_seconds", 300.0),
        ),
        # Mid-Hold Trade Management Fix Phase 3.5 (2026-05-19) tunables.
        thesis_invalidation_detection_enabled=bool(
            data.get("thesis_invalidation_detection_enabled", True),
        ),
        thesis_invalidation_close_buffer_pct=float(
            data.get("thesis_invalidation_close_buffer_pct", 0.5),
        ),
        thesis_invalidation_wick_buffer_pct=float(
            data.get("thesis_invalidation_wick_buffer_pct", 0.1),
        ),
        # Issue 1 (2026-05-18) — brain-close multi-factor scoring.
        wd_brain_scoring_enabled=bool(
            data.get("wd_brain_scoring_enabled", True),
        ),
        wd_brain_scoring_enforce=bool(
            data.get("wd_brain_scoring_enforce", False),
        ),
        wd_brain_scoring_threshold=float(
            data.get("wd_brain_scoring_threshold", 6.0),
        ),
        # P0-3 fix (2026-05-22) — hard_risk_floor SL consumption.
        wd_hard_risk_floor_sl_pct=float(
            data.get("wd_hard_risk_floor_sl_pct", 85.0),
        ),
    )


def _build_sl_gateway(data: dict[str, Any]) -> SLGatewaySettings:
    """Pass config.toml [sl_gateway] through to ``SLGatewaySettings``.

    Cross-check fix on top of Phase 2 of dir-block-fix (2026-05-05):
    pre-fix the builder used an explicit-arg pattern that omitted three
    fields the dataclass declares — ``min_distance_atr_multiplier``,
    ``min_distance_abs_floor_pct``, and ``min_distance_class_ceiling``.
    Operator tuning of those keys in ``config.toml`` was silently ignored.

    Owner-switch audit fix (2026-06-15): the prior ``hasattr(SLGatewaySettings,
    k)`` filter still silently dropped every field declared with
    ``field(default_factory=...)`` — because a factory-default installs NO
    class-level attribute, so ``hasattr`` is False for it. That dropped the
    owner-switch bucket lists (head/green/red/advisory/always) AND the
    pre-existing ``min_distance_class_ceiling`` sub-table, making those
    config.toml keys dead documentation (the dataclass defaults always won).
    Filtering on the dataclass FIELD NAMES instead makes every declared field —
    scalar and factory-default alike — load from config, so the buckets are
    genuinely tuning-ready (Rule 9). Behavior is unchanged today because the
    config lists/ceilings were byte-identical to the defaults; this makes future
    operator edits actually take effect.
    """
    if not data:
        return SLGatewaySettings()
    _field_names = {f.name for f in fields(SLGatewaySettings)}
    return SLGatewaySettings(**{
        k: data[k] for k in data if k in _field_names
    })


def _build_scanner_scoring_weights(data: dict[str, Any]) -> ScannerScoringWeights:
    """Build ScannerScoringWeights from [scanner.scoring_weights] TOML.

    Definitive-fix Phase 4 (2026-04-28): added ``rr`` weight (default 0.10).
    Defaults reshuffled so the no-config sum stays 1.0 — see
    ``ScannerScoringWeights`` docstring.
    """
    return ScannerScoringWeights(
        structure=float(data.get("structure", 0.27)),
        strategy=float(data.get("strategy", 0.27)),
        signal=float(data.get("signal", 0.13)),
        regime=float(data.get("regime", 0.13)),
        funding=float(data.get("funding", 0.10)),
        rr=float(data.get("rr", 0.10)),
    )


def _build_scanner_labeller(data: dict[str, Any]) -> LabellerSettings:
    """Issue 3 of 2026-05-19 direction-bias fix — load
    [scanner.labeller] TOML section into LabellerSettings.
    """
    return LabellerSettings(
        counter_regime_confidence_haircut=float(
            data.get("counter_regime_confidence_haircut", 0.5)
        ),
        extreme_sentiment_conviction_floor=float(
            data.get("extreme_sentiment_conviction_floor", 0.35)
        ),
        extreme_sentiment_offtrend_haircut=bool(
            data.get("extreme_sentiment_offtrend_haircut", True)
        ),
        range_fade_breakout_guard_enabled=bool(
            data.get("range_fade_breakout_guard_enabled", True)
        ),
    )


def _build_scanner(data: dict[str, Any]) -> ScannerSettings:
    return ScannerSettings(
        enabled=data.get("enabled", True),
        scan_interval_seconds=data.get("scan_interval_seconds", 300),
        min_volume_24h=data.get("min_volume_24h", 50_000_000),
        max_coins=data.get("max_coins", 15),
        max_spread_pct=data.get("max_spread_pct", 0.1),
        scoring_weights=_build_scanner_scoring_weights(
            data.get("scoring_weights", {})
        ),
        hysteresis=_build_scanner_hysteresis(data.get("hysteresis", {})),
        reentry_cooldown_seconds=int(data.get("reentry_cooldown_seconds", 600)),
        qualitative=_build_scanner_qualitative(data.get("qualitative", {})),
        briefing=_build_scanner_briefing(data.get("briefing", {})),
        labeller=_build_scanner_labeller(data.get("labeller", {})),
        mode=str(data.get("mode", "exclusion")),
        ab_mode=str(data.get("ab_mode", "off")),
    )


def _build_scanner_briefing_weights(
    data: dict[str, Any],
) -> ScannerBriefingInterestingnessWeights:
    """Build interestingness weights from [scanner.briefing.interestingness_weights] TOML."""
    return ScannerBriefingInterestingnessWeights(
        cleanness=float(data.get("cleanness", 0.20)),
        confluence=float(data.get("confluence", 0.20)),
        extremity=float(data.get("extremity", 0.15)),
        label_strength=float(data.get("label_strength", 0.20)),
        structural_quality=float(data.get("structural_quality", 0.15)),
        mtf_alignment=float(data.get("mtf_alignment", 0.07)),
        open_position_floor=float(data.get("open_position_floor", 0.03)),
    )


def _build_scanner_briefing(data: dict[str, Any]) -> ScannerBriefingSettings:
    """Phase 4 of the 1D briefing rewrite — load [scanner.briefing] TOML."""
    return ScannerBriefingSettings(
        top_n_packages=int(data.get("top_n_packages", 15)),
        min_briefing_packages=int(data.get("min_briefing_packages", 12)),
        qualified_threshold=float(data.get("qualified_threshold", 0.30)),
        prompt_floor_interestingness=float(
            data.get("prompt_floor_interestingness", 0.20)
        ),
        interestingness_weights=_build_scanner_briefing_weights(
            data.get("interestingness_weights", {})
        ),
    )


def _build_scanner_qualitative(data: dict[str, Any]) -> ScannerQualitativeSettings:
    """Build ScannerQualitativeSettings from [scanner.qualitative] TOML.

    Definitive-fix Phase 4 (2026-04-28): default fallback for
    ``min_rr_ratio`` aligned with the new dataclass default of 1.3
    (was 2.0 — that was the value the forensic data captured as the
    terminal pipeline gate). Production loads pick up the value from
    config.toml; the fallback only applies when the section is missing
    entirely.
    """
    return ScannerQualitativeSettings(
        min_rr_ratio=float(data.get("min_rr_ratio", 1.3)),
        min_consensus=str(data.get("min_consensus", "GOOD")),
        require_regime_alignment=bool(data.get("require_regime_alignment", True)),
        funding_blocker_threshold_pct=float(
            data.get("funding_blocker_threshold_pct", 0.001)
        ),
        recent_failure_blocker_hours=int(
            data.get("recent_failure_blocker_hours", 1)
        ),
        max_selection=int(data.get("max_selection", 15)),
        min_selection=int(data.get("min_selection", 0)),
    )


def _build_scanner_hysteresis(data: dict[str, Any]) -> ScannerHysteresisSettings:
    """Build ScannerHysteresisSettings from [scanner.hysteresis] TOML."""
    return ScannerHysteresisSettings(
        enabled=bool(data.get("enabled", True)),
        entry_consecutive_scans=int(data.get("entry_consecutive_scans", 2)),
        exit_consecutive_scans=int(data.get("exit_consecutive_scans", 3)),
        entry_threshold_above_min=int(data.get("entry_threshold_above_min", 5)),
        exit_threshold_below_min=int(data.get("exit_threshold_below_min", -5)),
    )


def _build_universe(data: dict[str, Any]) -> UniverseSettings:
    """Build UniverseSettings from [universe] TOML section.

    Validation runs in ``UniverseSettings.__post_init__`` and raises
    ConfigError on bad input — workers fail-fast with a clear message.

    Reads the optional ``[universe.coin_aliases]`` sub-table (mapping each
    watch_list symbol to a list of name aliases) and forwards it to the
    dataclass; absence -> empty dict (auto-derived tickers only).
    """
    coin_aliases_raw = data.get("coin_aliases", {})
    coin_aliases = {
        str(sym): list(aliases) if isinstance(aliases, (list, tuple)) else aliases
        for sym, aliases in coin_aliases_raw.items()
    }
    refresh = _build_universe_refresh(data.get("refresh", {}))
    if "watch_list" in data:
        return UniverseSettings(
            watch_list=list(data["watch_list"]),
            coin_aliases=coin_aliases,
            refresh=refresh,
        )
    return UniverseSettings(coin_aliases=coin_aliases, refresh=refresh)


def _build_universe_refresh(data: dict[str, Any]) -> UniverseRefreshSettings:
    """Build UniverseRefreshSettings from the [universe.refresh] sub-table.

    Absent section -> all defaults (feature disabled). Validation runs in
    ``UniverseRefreshSettings.__post_init__`` and raises ConfigError on
    bad input so workers fail-fast.
    """
    _default = UniverseRefreshSettings()
    return UniverseRefreshSettings(
        enabled=bool(data.get("enabled", _default.enabled)),
        schedule_hours_utc=[int(h) for h in data.get("schedule_hours_utc", _default.schedule_hours_utc)],
        warmup_max_minutes=int(data.get("warmup_max_minutes", _default.warmup_max_minutes)),
        warmup_poll_seconds=int(data.get("warmup_poll_seconds", _default.warmup_poll_seconds)),
        target_universe_size=int(data.get("target_universe_size", _default.target_universe_size)),
        shortlist_size=int(data.get("shortlist_size", _default.shortlist_size)),
        stable_core_size=int(data.get("stable_core_size", _default.stable_core_size)),
        liquidity_floor_usd=float(data.get("liquidity_floor_usd", _default.liquidity_floor_usd)),
        max_spread_pct=float(data.get("max_spread_pct", _default.max_spread_pct)),
        min_price=float(data.get("min_price", _default.min_price)),
        volatility_lookback_days=int(data.get("volatility_lookback_days", _default.volatility_lookback_days)),
        volatility_weight=float(data.get("volatility_weight", _default.volatility_weight)),
        volume_surge_weight=float(data.get("volume_surge_weight", _default.volume_surge_weight)),
        oi_weight=float(data.get("oi_weight", _default.oi_weight)),
        oi_enabled=bool(data.get("oi_enabled", _default.oi_enabled)),
        whipsaw_min_directionality=float(data.get("whipsaw_min_directionality", _default.whipsaw_min_directionality)),
        min_universe_size=int(data.get("min_universe_size", _default.min_universe_size)),
        softened_min_directionality=float(data.get("softened_min_directionality", _default.softened_min_directionality)),
        volatility_ceiling_pct=float(data.get("volatility_ceiling_pct", _default.volatility_ceiling_pct)),
        exclude_symbols=[str(s) for s in data.get("exclude_symbols", _default.exclude_symbols)],
        volatility_saturation_pct=float(data.get("volatility_saturation_pct", _default.volatility_saturation_pct)),
        volume_surge_saturation=float(data.get("volume_surge_saturation", _default.volume_surge_saturation)),
        oi_expansion_saturation_pct=float(data.get("oi_expansion_saturation_pct", _default.oi_expansion_saturation_pct)),
    )


def _build_regime(data: dict[str, Any]) -> RegimeSettings:
    return RegimeSettings(
        detection_interval_seconds=data.get("detection_interval_seconds", 300),
        primary_symbol=data.get("primary_symbol", "BTCUSDT"),
        trending_adx_threshold=data.get("trending_adx_threshold", 20.0),
        ranging_adx_threshold=data.get("ranging_adx_threshold", 20.0),
        ranging_choppiness_threshold=data.get("ranging_choppiness_threshold", 50.0),
        trending_choppiness_max=data.get("trending_choppiness_max", 45.0),
        volatile_atr_percentile=data.get("volatile_atr_percentile", 70.0),
        volatile_volume_ratio=data.get("volatile_volume_ratio", 2.0),
        dead_adx_threshold=data.get("dead_adx_threshold", 12.0),
        dead_volume_ratio=data.get("dead_volume_ratio", 0.5),
        # Phase 3 (output-quality) — config-exposed hysteresis count.
        hysteresis_count=int(data.get("hysteresis_count", 2)),
        # Per-coin-authority Phase 5 — breadth RISK/SIZING brake.
        breadth_brake_enabled=bool(data.get("breadth_brake_enabled", True)),
        # Issue 2.4 (2026-06-07): loader fallbacks aligned to the calibrated
        # dataclass defaults so a dropped config key cannot silently revert the
        # throttle to the pre-2.4 looser 0.65/0.50 curve.
        breadth_brake_start=float(data.get("breadth_brake_start", 0.60)),
        breadth_brake_floor=float(data.get("breadth_brake_floor", 0.40)),
        breadth_brake_min_coins=int(data.get("breadth_brake_min_coins", 10)),
    )


def _build_strategy_engine(data: dict[str, Any]) -> StrategyEngineSettings:
    return StrategyEngineSettings(
        scan_interval_seconds=data.get("scan_interval_seconds", 60),
        min_score_threshold=data.get("min_score_threshold", 70.0),
        min_ensemble_agreement=data.get("min_ensemble_agreement", 2.5),
        max_ensemble_opposition=data.get("max_ensemble_opposition", 2.5),
        min_ensemble_agreement_strong=data.get(
            "min_ensemble_agreement_strong", 4.0,
        ),
        max_ensemble_opposition_strong=data.get(
            "max_ensemble_opposition_strong", 1.5,
        ),
        strategy_regime_filter_enabled=bool(
            data.get("strategy_regime_filter_enabled", True),
        ),
        regime_weighting_enabled=bool(
            data.get("regime_weighting_enabled", False),
        ),
        regime_weighting_cold_start_n=int(
            data.get("regime_weighting_cold_start_n", 20),
        ),
        regime_weighting_floor=float(
            data.get("regime_weighting_floor", 0.3),
        ),
        regime_weighting_ceil=float(
            data.get("regime_weighting_ceil", 3.0),
        ),
        regime_weighting_sensitivity=float(
            data.get("regime_weighting_sensitivity", 0.3),
        ),
        regime_weighting_ema_alpha=float(
            data.get("regime_weighting_ema_alpha", 0.3),
        ),
        brain_prompt_l4_consensus_context_enabled=bool(
            data.get("brain_prompt_l4_consensus_context_enabled", True),
        ),
        max_setups_to_brain=data.get("max_setups_to_brain", 3),
        max_brain_calls_per_hour=data.get("max_brain_calls_per_hour", 12),
        kline_max_age_seconds=float(
            data.get("kline_max_age_seconds", 300.0)
        ),
        min_kline_count=int(data.get("min_kline_count", 50)),
        grade_threshold_a_plus=int(data.get("grade_threshold_a_plus", 80)),
        grade_threshold_a=int(data.get("grade_threshold_a", 68)),
        grade_threshold_b=int(data.get("grade_threshold_b", 56)),
        grade_threshold_c=int(data.get("grade_threshold_c", 45)),
        grade_quality_floor=float(data.get("grade_quality_floor", 10.0)),
        grade_quality_cap_enabled=bool(
            data.get("grade_quality_cap_enabled", False)
        ),
        grade_quality_cap_max_grade=str(
            data.get("grade_quality_cap_max_grade", "B")
        ),
        vote_trace_enabled=bool(data.get("vote_trace_enabled", True)),
        single_strategy_max_share=float(
            data.get("single_strategy_max_share", 1.0)
        ),
        ensemble_two_sided_vote=bool(
            data.get("ensemble_two_sided_vote", False)
        ),
    )


def _build_pnl_targets(data: dict[str, Any]) -> PnLTargetSettings:
    return PnLTargetSettings(
        daily_target_pct=data.get("daily_target_pct", 5.0),
        protect_threshold_pct=data.get("protect_threshold_pct", 3.0),
        caution_threshold_pct=data.get("caution_threshold_pct", -1.0),
        survival_threshold_pct=data.get("survival_threshold_pct", -3.0),
        halt_threshold_pct=data.get("halt_threshold_pct", -5.0),
    )


def _build_leverage(data: dict[str, Any]) -> LeverageSettings:
    return LeverageSettings(
        max_leverage=data.get("max_leverage", 5),
        tier_1_max=data.get("tier_1_max", 5),
        tier_2_max=data.get("tier_2_max", 4),
        tier_3_max=data.get("tier_3_max", 3),
        volatile_max=data.get("volatile_max", 3),
        dead_max=data.get("dead_max", 2),
        min_confidence_for_5x=data.get("min_confidence_for_5x", 0.85),
        min_confidence_for_4x=data.get("min_confidence_for_4x", 0.75),
    )


def _build_optimizer(data: dict[str, Any]) -> OptimizerSettings:
    return OptimizerSettings(
        enabled=data.get("enabled", True),
        run_day=data.get("run_day", "sunday"),
        run_hour_utc=data.get("run_hour_utc", 0),
        weight_adjustment_pct=data.get("weight_adjustment_pct", 10.0),
        max_param_change_pct=data.get("max_param_change_pct", 20.0),
        min_trades_for_optimization=data.get("min_trades_for_optimization", 20),
        underperform_threshold_pct=data.get("underperform_threshold_pct", 10.0),
        disable_after_weeks=data.get("disable_after_weeks", 3),
    )


def _build_factory(data: dict[str, Any]) -> FactorySettings:
    return FactorySettings(
        enabled=data.get("enabled", True),
        discovery_schedule_hour_utc=data.get("discovery_schedule_hour_utc", 2),
        discovery_lookback_days=data.get("discovery_lookback_days", 30),
        min_pattern_occurrences=data.get("min_pattern_occurrences", 20),
        min_win_rate=data.get("min_win_rate", 0.55),
        min_profit_factor=data.get("min_profit_factor", 1.2),
        min_statistical_significance=data.get("min_statistical_significance", 0.05),
        max_strategies_per_batch=data.get("max_strategies_per_batch", 5),
        max_generation_retries=data.get("max_generation_retries", 3),
        generation_cost_limit_usd=data.get("generation_cost_limit_usd", 0.20),
        live_monitor_interval_seconds=data.get("live_monitor_interval_seconds", 300),
        hot_pattern_threshold_win_rate=data.get("hot_pattern_threshold_win_rate", 0.70),
        hot_pattern_threshold_occurrences=data.get("hot_pattern_threshold_occurrences", 5),
        emergency_generation_enabled=data.get("emergency_generation_enabled", True),
    )


def _build_backtesting(data: dict[str, Any]) -> BacktestSettings:
    return BacktestSettings(
        initial_capital=data.get("initial_capital", 10000.0),
        default_leverage=data.get("default_leverage", 3),
        commission_pct=data.get("commission_pct", 0.06),
        slippage_pct=data.get("slippage_pct", 0.02),
        funding_rate_pct=data.get("funding_rate_pct", 0.01),
        walk_forward_enabled=data.get("walk_forward_enabled", True),
        train_pct=data.get("train_pct", 0.70),
        monte_carlo_runs=data.get("monte_carlo_runs", 1000),
        min_trades_to_pass=data.get("min_trades_to_pass", 30),
        min_win_rate=data.get("min_win_rate", 0.52),
        min_profit_factor=data.get("min_profit_factor", 1.3),
        max_drawdown_pct=data.get("max_drawdown_pct", 15.0),
        min_sharpe=data.get("min_sharpe", 0.5),
        min_walk_forward_efficiency=data.get("min_walk_forward_efficiency", 0.5),
        max_ruin_probability=data.get("max_ruin_probability", 0.05),
    )


def _build_trial(data: dict[str, Any]) -> TrialSettings:
    return TrialSettings(
        trial_duration_days=data.get("trial_duration_days", 14),
        max_extensions=data.get("max_extensions", 1),
        extension_duration_days=data.get("extension_duration_days", 7),
        trial_position_size_pct=data.get("trial_position_size_pct", 25.0),
        min_trades_for_evaluation=data.get("min_trades_for_evaluation", 10),
        promotion_min_win_rate=data.get("promotion_min_win_rate", 0.50),
        promotion_min_pnl=data.get("promotion_min_pnl", 0.0),
        promotion_max_drawdown=data.get("promotion_max_drawdown", 10.0),
        max_active_strategies=data.get("max_active_strategies", 60),
        demotion_underperform_weeks=data.get("demotion_underperform_weeks", 2),
        demotion_win_rate_drop_pct=data.get("demotion_win_rate_drop_pct", 15.0),
        quarterly_revival_enabled=data.get("quarterly_revival_enabled", True),
    )


def _build_portfolio(data: dict[str, Any]) -> PortfolioSettings:
    return PortfolioSettings(**{k: data[k] for k in data if hasattr(PortfolioSettings, k)}) if data else PortfolioSettings()


def _build_telegram_interactive(data: dict[str, Any]) -> TelegramInteractiveSettings:
    return TelegramInteractiveSettings(**{k: data[k] for k in data if hasattr(TelegramInteractiveSettings, k)}) if data else TelegramInteractiveSettings()


def _build_enforcer(data: dict[str, Any]) -> EnforcerSettings:
    return EnforcerSettings(**{k: data[k] for k in data if hasattr(EnforcerSettings, k)}) if data else EnforcerSettings()


def _build_sentiment(data: dict[str, Any]) -> SentimentSettings:
    """CALL_B Framing Fix Phase 5B (2026-05-06)."""
    return SentimentSettings(**{k: data[k] for k in data if hasattr(SentimentSettings, k)}) if data else SentimentSettings()


def _build_mode4(data: dict[str, Any]) -> Mode4Settings:
    return Mode4Settings(**{k: data[k] for k in data if hasattr(Mode4Settings, k)}) if data else Mode4Settings()


def _build_adaptive_exit(data: dict[str, Any]) -> AdaptiveExitSettings:
    """Build AdaptiveExitSettings from the ``[adaptive_exit]`` TOML section.

    Uses the field-name filter (fields()) rather than hasattr, so the list field
    ``rung_r`` is loaded from config instead of being silently dropped (the
    2026-06-15 owner-switch loader fix). Unknown keys are ignored; defaults live
    on the dataclass.
    """
    if not data:
        return AdaptiveExitSettings()
    _field_names = {f.name for f in fields(AdaptiveExitSettings)}
    return AdaptiveExitSettings(
        **{k: data[k] for k in data if k in _field_names}
    )


def _build_profit_fetching(data: dict[str, Any]) -> ProfitFetchingSettings:
    """Build ProfitFetchingSettings from the ``[profit_fetching]`` TOML section.

    Filter pattern mirrors ``_build_mode4`` — unknown keys in TOML are silently
    ignored so the config can carry forward-looking knobs without breaking the
    loader. Defaults are defined on the dataclass.
    """
    return (
        ProfitFetchingSettings(
            **{k: data[k] for k in data if hasattr(ProfitFetchingSettings, k)}
        )
        if data
        else ProfitFetchingSettings()
    )


def _build_loss_cutting(data: dict[str, Any]) -> LossCuttingSettings:
    """Build LossCuttingSettings from the ``[loss_cutting]`` TOML section.

    Filter pattern mirrors ``_build_profit_fetching`` — unknown keys in TOML are
    silently ignored so the config can carry forward-looking knobs (and so a
    stray key can never break the loader). Defaults are defined on the dataclass.
    """
    return (
        LossCuttingSettings(
            **{k: data[k] for k in data if hasattr(LossCuttingSettings, k)}
        )
        if data
        else LossCuttingSettings()
    )


def _build_layer4_sniper(data: dict[str, Any]) -> Layer4SniperSettings:
    """Build Layer4SniperSettings from ``[layer4.sniper]`` TOML section.

    Layer 4 Realignment (2026-05-06). Filter pattern mirrors
    ``_build_mode4`` so unknown keys in TOML do not blow up — they are
    silently ignored. Defaults defined on the dataclass.
    """
    return (
        Layer4SniperSettings(
            **{k: data[k] for k in data if hasattr(Layer4SniperSettings, k)}
        )
        if data
        else Layer4SniperSettings()
    )


def _build_fund_manager(data: dict[str, Any]) -> FundManagerSettings:
    return FundManagerSettings(**{k: data[k] for k in data if hasattr(FundManagerSettings, k)}) if data else FundManagerSettings()


def _build_mcp(data: dict[str, Any]) -> MCPSettings:
    return MCPSettings(
        transport=data.get("transport", "stdio"),
        sse_host=data.get("sse_host", "0.0.0.0"),
        sse_port=data.get("sse_port", 8080),
        sse_auth_required=data.get("sse_auth_required", True),
        server_name=data.get("server_name", "trading-intelligence"),
        server_version=data.get("server_version", "0.1.0"),
        auth_token=_env("MCP_AUTH_TOKEN", data.get("auth_token", "")),
    )


def _build_tias(data: dict[str, Any]) -> TIASSettings:
    base = TIASSettings(**{k: data[k] for k in data if hasattr(TIASSettings, k)}) if data else TIASSettings()
    env_key = _env("OPENROUTER_API_KEY")
    if env_key:
        base.api_key = env_key
    return base


def _build_apex(data: dict[str, Any]) -> APEXSettings:
    base = APEXSettings(**{k: data[k] for k in data if hasattr(APEXSettings, k)}) if data else APEXSettings()
    # APEX_API_KEY takes precedence over shared OPENROUTER_API_KEY
    apex_key = _env("APEX_API_KEY")
    if apex_key:
        base.api_key = apex_key
    else:
        env_key = _env("OPENROUTER_API_KEY")
        if env_key:
            base.api_key = env_key
    return base


def _build_sentinel(data: dict[str, Any]) -> SentinelSettings:
    base = SentinelSettings(**{k: data[k] for k in data if hasattr(SentinelSettings, k)}) if data else SentinelSettings()
    # SENTINEL_API_KEY takes precedence, then shared OPENROUTER_API_KEY
    sentinel_key = _env("SENTINEL_API_KEY")
    if sentinel_key:
        base.advisor_api_key = sentinel_key
    else:
        env_key = _env("OPENROUTER_API_KEY")
        if env_key:
            base.advisor_api_key = env_key
    return base


def _build_structure(data: dict[str, Any]) -> StructureSettings:
    """Build X-RAY StructureSettings from [analysis.structure] TOML section.

    Layer 1 restructure Phase 2 — pulls the nested
    ``[analysis.structure.setup_types]`` block into a SetupTypesSettings.
    Missing block falls back to defaults.
    """
    if not data:
        return StructureSettings()
    setup_types_data = data.get("setup_types") or {}
    setup_types = SetupTypesSettings(**{
        k: v for k, v in setup_types_data.items()
        if hasattr(SetupTypesSettings, k)
    }) if setup_types_data else SetupTypesSettings()
    # Filter top-level fields, excluding the nested setup_types block. Use the
    # dataclass FIELD NAMES (not hasattr): fields declared with field(
    # default_factory=...) — e.g. swing_lookbacks, mtf_timeframes — set NO class
    # attribute, so the old hasattr() check silently DROPPED them and an operator
    # editing those list knobs in config.toml had no effect. Field-name
    # membership loads every declared field, including factory ones.
    _field_names = {f.name for f in fields(StructureSettings)}
    filtered = {
        k: data[k] for k in data
        if k in _field_names and k != "setup_types"
    }
    return StructureSettings(**filtered, setup_types=setup_types)


def _build_volatility_profile(data: dict[str, Any]) -> VolatilityProfileSettings:
    """Build VolatilityProfileSettings from [analysis.volatility_profile] TOML section."""
    if not data:
        return VolatilityProfileSettings()
    filtered = {k: data[k] for k in data if hasattr(VolatilityProfileSettings, k)}
    return VolatilityProfileSettings(**filtered)


def _build_time_decay(data: dict[str, Any]) -> TimeDecaySettings:
    """Build TimeDecaySettings from [time_decay] TOML section."""
    if not data:
        return TimeDecaySettings()
    filtered = {k: data[k] for k in data if hasattr(TimeDecaySettings, k)}
    return TimeDecaySettings(**filtered)


def _build_mcp_pool(data: dict[str, Any]) -> MCPPoolSettings:
    """Build MCPPoolSettings from [mcp_pool] TOML section."""
    return MCPPoolSettings(
        enabled=bool(data.get("enabled", False)),
        sse_url=str(data.get("sse_url", "http://127.0.0.1:8080")),
        min_warm=int(data.get("min_warm", 1)),
        max_warm=int(data.get("max_warm", 2)),
        health_check_interval_seconds=int(data.get("health_check_interval_seconds", 60)),
        acquire_timeout_seconds=float(data.get("acquire_timeout_seconds", 2.0)),
    )


def _build_price(data: dict[str, Any]) -> PriceFreshnessSettings:
    """Build PriceFreshnessSettings from [price] TOML section.

    Phase 3 (P0-2). Defaults match the brief's Fix A/B/D thresholds:
    10s freshness window, 0.5% override threshold, 1% prompt-defer
    threshold. Missing keys fall back to dataclass defaults.
    """
    return PriceFreshnessSettings(
        local_max_age_seconds=float(data.get("local_max_age_seconds", 10.0)),
        divergence_override_pct=float(data.get("divergence_override_pct", 0.5)),
        divergence_block_prompt_pct=float(data.get("divergence_block_prompt_pct", 1.0)),
        spike_reject_pct=float(data.get("spike_reject_pct", 0.15)),
    )


def _build_observability(data: dict[str, Any]) -> ObservabilitySettings:
    """Build ObservabilitySettings from [observability] TOML section.

    Phase 1 of the Layer 1 restructure. Defaults: 100-cycle history,
    hourly flush to ``cycle_metrics``, INFO-level tick markers. Missing
    keys fall back to dataclass defaults.
    """
    return ObservabilitySettings(
        cycle_tracker_history=int(data.get("cycle_tracker_history", 100)),
        cycle_metrics_flush_seconds=int(data.get("cycle_metrics_flush_seconds", 3600)),
        log_tick_done_at_info=bool(data.get("log_tick_done_at_info", True)),
        capture_brain_calls=bool(data.get("capture_brain_calls", True)),
        capture_dir=str(data.get("capture_dir", "data/stage2_dumps")),
        capture_retention_days=int(data.get("capture_retention_days", 7)),
        capture_max_files=int(data.get("capture_max_files", 5000)),
        price_path_logging_enabled=bool(data.get("price_path_logging_enabled", True)),
        price_path_resolution_seconds=float(data.get("price_path_resolution_seconds", 1.0)),
        price_path_flush_seconds=int(data.get("price_path_flush_seconds", 30)),
        price_path_ws_max_age_seconds=float(data.get("price_path_ws_max_age_seconds", 5.0)),
        price_path_rotation=str(data.get("price_path_rotation", "10 MB")),
        price_path_retention=str(data.get("price_path_retention", "7 days")),
        price_path_filename=str(data.get("price_path_filename", "price_path.log")),
        placement_forensic_enabled=bool(data.get("placement_forensic_enabled", True)),
    )


def _build_coin_package_validator(
    data: dict[str, Any],
) -> CoinPackageValidatorSettings:
    """Build CoinPackageValidatorSettings from TOML section.

    Phase 5 (output-quality). Defaults match the dataclass; missing
    keys fall back to defaults.
    """
    return CoinPackageValidatorSettings(
        fail_below=float(data.get("fail_below", 0.50)),
        warn_below=float(data.get("warn_below", 0.85)),
        staleness_fail_seconds=float(data.get("staleness_fail_seconds", 300.0)),
    )


def _build_signal_generator(data: dict[str, Any]) -> SignalGeneratorSettings:
    """Build SignalGeneratorSettings from [signal_generator] TOML section.

    Phase 1 (output-quality). Reads optional [signal_generator.multi_source]
    nested block; falls back to dataclass defaults for missing keys.
    """
    ms_data = data.get("multi_source", {}) if isinstance(data, dict) else {}
    if not isinstance(ms_data, dict):
        ms_data = {}
    multi_source = SignalGeneratorMultiSourceSettings(
        sentiment_min_active=float(ms_data.get("sentiment_min_active", 0.05)),
        fg_min_active=float(ms_data.get("fg_min_active", 0.10)),
        # Definitive-fix Phase 5 (2026-04-28) — fallback aligned with
        # the new 0.10 dataclass default. config.toml drives production.
        funding_min_active=float(ms_data.get("funding_min_active", 0.10)),
        oi_min_active=float(ms_data.get("oi_min_active", 0.10)),
        sentiment_weight=float(ms_data.get("sentiment_weight", 0.40)),
        fg_weight=float(ms_data.get("fg_weight", 0.25)),
        funding_weight=float(ms_data.get("funding_weight", 0.20)),
        oi_weight=float(ms_data.get("oi_weight", 0.15)),
        strong_threshold=float(ms_data.get("strong_threshold", 0.55)),
        # Definitive-fix Phase 5 (2026-04-28) — calibration default
        # 0.25 → 0.18; config.toml drives production.
        buy_threshold=float(ms_data.get("buy_threshold", 0.18)),
        fg_normalize_range=float(ms_data.get("fg_normalize_range", 30.0)),
        funding_normalize=float(ms_data.get("funding_normalize", 0.005)),
        oi_normalize_pct=float(ms_data.get("oi_normalize_pct", 5.0)),
        # Issue 1 (2026-06-08) — this explicit-kwarg builder must load the
        # Fear-and-Greed direction-neutrality flag from config, or the operator's
        # off-switch ([signal_generator.multi_source].fg_direction_neutral=false)
        # is silently ignored (the dataclass default would always stand). Default
        # True matches the dataclass default (neutrality on).
        fg_direction_neutral=bool(ms_data.get("fg_direction_neutral", True)),
        # Fix 1 (price-conditioned OI, 2026-06-10) — the price window matched to
        # the OI change window and the dead-band for the opposite-sign inversion.
        oi_price_window_hours=float(ms_data.get("oi_price_window_hours", 24.0)),
        oi_price_dead_band_pct=float(ms_data.get("oi_price_dead_band_pct", 0.0)),
        # Fix 2 (fresh OI windows, 2026-06-10) — short-window blend. Five-Fix
        # Follow-Up: 15m joins as a driver; defaults 15m=0.4 / 1h=0.6 / 24h=0.0
        # (24h is context-only).
        oi_short_window_hours=float(ms_data.get("oi_short_window_hours", 1.0)),
        oi_blend_weight_short=float(ms_data.get("oi_blend_weight_short", 0.6)),
        oi_blend_weight_long=float(ms_data.get("oi_blend_weight_long", 0.0)),
        funding_use_instantaneous=bool(ms_data.get("funding_use_instantaneous", True)),
        oi_15m_window_hours=float(ms_data.get("oi_15m_window_hours", 0.25)),
        oi_blend_weight_15m=float(ms_data.get("oi_blend_weight_15m", 0.4)),
    )
    return SignalGeneratorSettings(multi_source=multi_source)


def _build_worker_liveness(data: dict[str, Any]) -> WorkerLivenessSettings:
    """Build WorkerLivenessSettings from [worker_liveness] TOML section.

    Phase 11 (dead-workers fix). Defaults: 30 s probe cadence, 90 s
    grace, 2.0 overdue multiplier, 1 hour alert rate-limit. Missing
    keys fall back to dataclass defaults.
    """
    return WorkerLivenessSettings(
        watchdog_interval_sec=float(data.get("watchdog_interval_sec", 30.0)),
        first_tick_grace_sec=float(data.get("first_tick_grace_sec", 90.0)),
        overdue_multiplier=float(data.get("overdue_multiplier", 2.0)),
        alert_rate_limit_sec=float(data.get("alert_rate_limit_sec", 3600.0)),
    )


def _build_layer_manager(data: dict[str, Any]) -> LayerManagerSettings:
    """Build LayerManagerSettings from [layer_manager] TOML section.

    Phase 2 (post-Layer-1 fix). Defaults: 60 s boot deadline, 60 s state
    sync heartbeat. Phase 11 (dead-workers fix) added ``on_drift_action``
    nested in ``[layer_manager.state_sync]``; it falls back to the
    flat-key form on the same section for ergonomic operator overrides.
    Missing keys fall back to dataclass defaults.
    """
    state_sync_section = data.get("state_sync", {})
    drift_action = (
        state_sync_section.get("on_drift_action")
        if isinstance(state_sync_section, dict)
        else None
    )
    if drift_action is None:
        drift_action = data.get("on_drift_action", "rewrite_disk")
    return LayerManagerSettings(
        lm_attach_deadline_sec=float(data.get("lm_attach_deadline_sec", 60.0)),
        state_sync_interval_sec=float(data.get("state_sync_interval_sec", 60.0)),
        on_drift_action=str(drift_action),
    )
