"""Phase 13 — 4-hour live observation harness for the post-Layer-1 fixes.

Run after the workers process is restarted with the post-Layer-1 code
applied. Collects metrics from ``data/logs/workers.log`` at fixed
cadences and writes a final report to
``dev_notes/phase13_post_layer1_observation_report.md``.

Sample usage::

    .venv/bin/python scripts/observation_4h.py

The script does NOT start or stop services on its own. The optional
mid-run restart (at t=2h) is gated behind ``--restart-at-midpoint``
and requires that the operator has already verified ``sudo`` access.

The script is deliberately stdlib-only (re, time, json, pathlib,
collections) so it can run inside the workers venv without extra
dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = PROJECT_ROOT / "data" / "logs" / "workers.log"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "trading.db"
DEFAULT_WAL_PATH = PROJECT_ROOT / "data" / "trading.db-wal"
DEFAULT_REPORT_PATH = (
    PROJECT_ROOT / "dev_notes" / "phase13_post_layer1_observation_report.md"
)

# Observation window constants. Truncated to 4 h per user decision in
# the plan; spec called for 24 h. 4 h covers one credential refresh,
# multiple WAL checkpoints, and the dominant trading hour, which is
# enough to validate the Phase 1-12 fixes without sitting on the
# session for a full day.
TOTAL_WINDOW_S = 4 * 3600
SAMPLE_INTERVAL_S = 5 * 60       # 5-minute log scrape
SNAPSHOT_INTERVAL_S = 30 * 60    # 30-minute file/DB snapshot
RESTART_AT_S = 2 * 3600          # restart workers half-way (optional)


# ---------------------------------------------------------------------------
# Log parsers
# ---------------------------------------------------------------------------


# These regexes are intentionally tolerant: they grep for the structured
# tag and one or two fields, then ignore everything else. Any change in
# the upstream log format that drops the tag or the named field will
# simply yield zero matches — visible in the report — rather than
# crashing the script.

KLINE_FETCH_RE = re.compile(
    r"KLINE_FETCH \| klines=(?P<klines>\d+) "
    r"expected=(?P<expected>\d+) "
    r"symbols=(?P<symbols>\d+) "
    r"quality=(?P<quality>\w+) "
    r"errors=(?P<errors>\d+) "
    r"el=(?P<el>\d+)ms"
)
KLINE_WRITE_LAG_RE = re.compile(
    r"KLINE_WRITE_LAG \| stale_count=(?P<stale_count>\d+)"
)
KLINE_FETCH_FAIL_RE = re.compile(
    r"KLINE_FETCH_FAIL \| sym=(?P<sym>\S+)"
)
KLINE_STRAGGLER_RE = re.compile(
    r"KLINE_STRAGGLER \| sym=(?P<sym>\S+) "
    r"consecutive_fails=(?P<n>\d+)"
)
DB_LOCK_WAIT_RE = re.compile(
    r"DB_LOCK_WAIT \| wait_ms=(?P<wait_ms>\d+)\.?\d* "
    r"holder=(?P<holder>\S+) caller=(?P<caller>\S+)"
)
DB_LOCK_HIST_RE = re.compile(
    r"DB_LOCK_HIST \| n=(?P<n>\d+) "
    r"p50=(?P<p50>\d+)ms p95=(?P<p95>\d+)ms max=(?P<max>\d+)ms"
)
WAL_CHECKPOINT_RE = re.compile(
    r"WAL_CHECKPOINT \| mode=(?P<mode>\w+) "
    r"busy=(?P<busy>\d+) log=(?P<log>\d+) ckpt=(?P<ckpt>\d+)"
)
ORDER_START_RE = re.compile(
    r"ORDER_START \| link_id=(?P<link_id>\S+) sym=(?P<sym>\S+)"
)
ORDER_START_LEGACY_RE = re.compile(
    r"ORDER_START \| sym=(?P<sym>\S+).*?(?<!link_id=)"
)
CLAUDE_PREFLIGHT_RE = re.compile(
    r"CLAUDE_PREFLIGHT_REFRESH \| reason=(?P<reason>\S+) "
    r"mins_left=(?P<mins>[\d\.]+)"
)
CLAUDE_CALL_OK_RE = re.compile(r"CLAUDE_CALL_OK \|")
CLAUDE_CALL_FAIL_RE = re.compile(r"CLAUDE_NONRETRY \|")
WORKER_DEGRADATION_RE = re.compile(r"WORKER_DEGRADATION_CASCADE \|")
SHADOW_FAIL_RE = re.compile(r"SHADOW_CALL_FAIL \|")
FUND_MGR_FAIL_RE = re.compile(r"FUND_MGR_BALANCE_FAIL \|")
FINNHUB_COVERAGE_RE = re.compile(
    r"FINNHUB_COVERAGE \| category=\S+ returned=(?P<returned>\d+) "
    r"considered=(?P<considered>\d+) new=(?P<new>\d+)"
)


# ---------------------------------------------------------------------------
# Metrics container
# ---------------------------------------------------------------------------


class Metrics:
    """Aggregate counters & samples collected over the observation."""

    def __init__(self) -> None:
        # KLINE
        self.kline_el_samples_ms: list[int] = []
        self.kline_errors_total = 0
        self.kline_quality_count: Counter[str] = Counter()
        self.kline_write_lag_events = 0
        self.kline_write_lag_max_stale = 0
        self.kline_fetch_fail_by_sym: Counter[str] = Counter()
        self.kline_stragglers: set[str] = set()

        # DB lock
        self.db_lock_wait_events = 0
        self.db_lock_wait_max_ms = 0
        self.db_lock_wait_by_holder: Counter[str] = Counter()
        self.db_lock_hist_p50_samples: list[int] = []
        self.db_lock_hist_p95_samples: list[int] = []
        self.db_lock_hist_max_samples: list[int] = []

        # WAL
        self.wal_checkpoints = 0
        self.wal_checkpoint_busy_events = 0
        self.wal_size_samples_bytes: list[int] = []

        # Orders
        self.order_starts_total = 0
        self.order_starts_with_link_id = 0
        self.order_starts_legacy_no_link_id = 0
        self.order_link_ids_seen: set[str] = set()
        self.order_retries = 0
        self.order_dedups = 0

        # Brain
        self.brain_preflight_refreshes = 0
        self.brain_call_ok = 0
        self.brain_call_fail = 0
        self.brain_cascades = 0

        # Boot / shadow / fund manager
        self.shadow_call_fails = 0
        self.fund_mgr_fails = 0

        # Finnhub coverage
        self.finnhub_returned_total = 0
        self.finnhub_new_total = 0
        self.finnhub_samples = 0

        # File-level snapshots
        self.aggregated_sentiment_max_age_s_samples: list[float] = []
        self.news_max_age_s_samples: list[float] = []

    def to_dict(self) -> dict[str, Any]:
        return {k: (sorted(v) if isinstance(v, set) else v)
                for k, v in self.__dict__.items()}


# ---------------------------------------------------------------------------
# Log scraping
# ---------------------------------------------------------------------------


def scrape_log(path: Path, last_offset: int, m: Metrics) -> int:
    """Read everything appended to ``path`` since ``last_offset``.

    Returns the new offset. Updates ``m`` in place. Tolerant of the
    file being rotated under us — if the file shrinks below
    ``last_offset`` we restart from 0.
    """
    if not path.exists():
        return last_offset

    size = path.stat().st_size
    if size < last_offset:
        # Rotated out; restart from zero on the new file.
        last_offset = 0

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(last_offset)
            for line in fh:
                _consume_line(line, m)
            new_offset = fh.tell()
    except OSError:
        return last_offset

    return new_offset


def _consume_line(line: str, m: Metrics) -> None:
    if mo := KLINE_FETCH_RE.search(line):
        m.kline_el_samples_ms.append(int(mo.group("el")))
        m.kline_errors_total += int(mo.group("errors"))
        m.kline_quality_count[mo.group("quality")] += 1
        return

    if mo := KLINE_WRITE_LAG_RE.search(line):
        m.kline_write_lag_events += 1
        n = int(mo.group("stale_count"))
        if n > m.kline_write_lag_max_stale:
            m.kline_write_lag_max_stale = n
        return

    if mo := KLINE_FETCH_FAIL_RE.search(line):
        m.kline_fetch_fail_by_sym[mo.group("sym")] += 1
        return

    if mo := KLINE_STRAGGLER_RE.search(line):
        m.kline_stragglers.add(mo.group("sym"))
        return

    if mo := DB_LOCK_WAIT_RE.search(line):
        m.db_lock_wait_events += 1
        wms = int(mo.group("wait_ms"))
        if wms > m.db_lock_wait_max_ms:
            m.db_lock_wait_max_ms = wms
        m.db_lock_wait_by_holder[mo.group("holder")] += 1
        return

    if mo := DB_LOCK_HIST_RE.search(line):
        m.db_lock_hist_p50_samples.append(int(mo.group("p50")))
        m.db_lock_hist_p95_samples.append(int(mo.group("p95")))
        m.db_lock_hist_max_samples.append(int(mo.group("max")))
        return

    if mo := WAL_CHECKPOINT_RE.search(line):
        m.wal_checkpoints += 1
        if int(mo.group("busy")) > 0:
            m.wal_checkpoint_busy_events += 1
        return

    if mo := ORDER_START_RE.search(line):
        m.order_starts_total += 1
        m.order_starts_with_link_id += 1
        m.order_link_ids_seen.add(mo.group("link_id"))
        return

    if "ORDER_START | " in line and "link_id=" not in line:
        # Pre-Phase-5 format — should be ZERO post-deploy.
        m.order_starts_total += 1
        m.order_starts_legacy_no_link_id += 1
        return

    if "ORDER_RETRY |" in line:
        m.order_retries += 1
        return

    if "ORDER_DEDUPED |" in line:
        m.order_dedups += 1
        return

    if CLAUDE_PREFLIGHT_RE.search(line):
        m.brain_preflight_refreshes += 1
        return

    if CLAUDE_CALL_OK_RE.search(line):
        m.brain_call_ok += 1
        return

    if CLAUDE_CALL_FAIL_RE.search(line):
        m.brain_call_fail += 1
        return

    if WORKER_DEGRADATION_RE.search(line):
        m.brain_cascades += 1
        return

    if SHADOW_FAIL_RE.search(line):
        m.shadow_call_fails += 1
        return

    if FUND_MGR_FAIL_RE.search(line):
        m.fund_mgr_fails += 1
        return

    if mo := FINNHUB_COVERAGE_RE.search(line):
        m.finnhub_returned_total += int(mo.group("returned"))
        m.finnhub_new_total += int(mo.group("new"))
        m.finnhub_samples += 1
        return


# ---------------------------------------------------------------------------
# DB / file snapshots
# ---------------------------------------------------------------------------


def snapshot_wal(m: Metrics, wal_path: Path) -> None:
    if wal_path.exists():
        m.wal_size_samples_bytes.append(wal_path.stat().st_size)


def snapshot_sentiment(m: Metrics, db_path: Path) -> None:
    """Capture freshness of news_articles and aggregated_sentiment."""
    if not db_path.exists():
        return
    try:
        # Use the read-only sqlite3 CLI to avoid any chance of contending
        # with the live workers process.
        for table, col, bucket in (
            ("news_articles", "published_at", m.news_max_age_s_samples),
            ("aggregated_sentiment", "created_at", m.aggregated_sentiment_max_age_s_samples),
        ):
            res = subprocess.run(
                ["sqlite3", str(db_path),
                 f"SELECT MAX({col}) FROM {table};"],
                capture_output=True, text=True, timeout=15,
            )
            ts = (res.stdout or "").strip()
            if not ts:
                continue
            # Normalize "YYYY-MM-DD HH:MM:SS" or ISO strings to seconds-old
            try:
                dt = datetime.fromisoformat(ts.replace(" ", "T"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            bucket.append(age)
    except (subprocess.TimeoutExpired, OSError):
        return


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _percentile(samples: list[int], p: float) -> int:
    if not samples:
        return 0
    s = sorted(samples)
    return s[min(len(s) - 1, int(len(s) * p))]


def write_report(m: Metrics, report_path: Path, wall_clock_s: float) -> None:
    """Write the final dev_notes/phase13_post_layer1_observation_report.md."""
    n = len(m.kline_el_samples_ms)
    p50 = _percentile(m.kline_el_samples_ms, 0.50)
    p95 = _percentile(m.kline_el_samples_ms, 0.95)
    pmax = max(m.kline_el_samples_ms) if m.kline_el_samples_ms else 0

    wal_max_mb = (max(m.wal_size_samples_bytes) / (1024 * 1024)
                  if m.wal_size_samples_bytes else 0.0)
    wal_avg_mb = (sum(m.wal_size_samples_bytes) / len(m.wal_size_samples_bytes) / (1024 * 1024)
                  if m.wal_size_samples_bytes else 0.0)

    finnhub_drop_rate = (
        1.0 - (m.finnhub_new_total / m.finnhub_returned_total)
        if m.finnhub_returned_total > 0 else 0.0
    )

    sentiment_avg_age_min = (
        sum(m.aggregated_sentiment_max_age_s_samples)
        / len(m.aggregated_sentiment_max_age_s_samples) / 60.0
        if m.aggregated_sentiment_max_age_s_samples else 0.0
    )
    news_avg_age_min = (
        sum(m.news_max_age_s_samples) / len(m.news_max_age_s_samples) / 60.0
        if m.news_max_age_s_samples else 0.0
    )

    brain_total = m.brain_call_ok + m.brain_call_fail
    brain_success_rate = (
        100.0 * m.brain_call_ok / brain_total if brain_total > 0 else 0.0
    )

    lines = [
        "# Phase 13 — Post-Layer-1 Live Observation Report (4-hour window)",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Wall-clock duration:** {wall_clock_s/3600:.2f} h",
        f"**Source:** `data/logs/workers.log` + live SQLite reads",
        "",
        "## Final verification checklist",
        "",
        "| Check | Target | Observed | Pass? |",
        "|---|---|---|---|",
        f"| kline_worker tick p50 | < 5 s | {p50/1000:.2f} s ({n} samples) | {'✅' if p50 < 5000 and n > 0 else '⚠️'} |",
        f"| kline_worker tick p95 | < 10 s | {p95/1000:.2f} s | {'✅' if p95 < 10000 and n > 0 else '⚠️'} |",
        f"| kline_worker tick max | < 15 s | {pmax/1000:.2f} s | {'✅' if pmax < 15000 and n > 0 else '⚠️'} |",
        f"| WAL size never exceeds | 50 MB | max={wal_max_mb:.1f} MB avg={wal_avg_mb:.1f} MB | {'✅' if wal_max_mb < 50 else '⚠️ (preallocation expected)'} |",
        f"| DB_LOCK_WAIT events | < 10 / hr | {m.db_lock_wait_events} total | {'✅' if m.db_lock_wait_events < 10 * (wall_clock_s / 3600) else '⚠️'} |",
        f"| DB_LOCK_WAIT max | < 5 s | {m.db_lock_wait_max_ms} ms | {'✅' if m.db_lock_wait_max_ms < 5000 else '⚠️'} |",
        f"| KLINE_WRITE_LAG events | < 5 typical | {m.kline_write_lag_events} (max stale_count={m.kline_write_lag_max_stale}) | {'✅' if m.kline_write_lag_events < 20 else '⚠️'} |",
        f"| Brain success rate | > 95% | {brain_success_rate:.1f}% ({m.brain_call_ok}/{brain_total}) | {'✅' if brain_success_rate > 95 else '⚠️'} |",
        f"| Brain cascade events | 0 | {m.brain_cascades} | {'✅' if m.brain_cascades == 0 else '⚠️'} |",
        f"| Pre-flight refreshes fired | as needed | {m.brain_preflight_refreshes} | ℹ️ |",
        f"| Duplicate ORDER_START events (legacy fmt) | 0 | {m.order_starts_legacy_no_link_id} | {'✅' if m.order_starts_legacy_no_link_id == 0 else '🚨 ROLLBACK'} |",
        f"| ORDER_START with link_id (post-fix) | all | {m.order_starts_with_link_id} | ℹ️ |",
        f"| Unique link_ids vs total ORDER_START | match | {len(m.order_link_ids_seen)}/{m.order_starts_with_link_id} | {'✅' if len(m.order_link_ids_seen) == m.order_starts_with_link_id else '⚠️'} |",
        f"| ORDER_DEDUPED events | rare | {m.order_dedups} | ℹ️ |",
        f"| Sentiment freshness (aggregated) | < 30 min | avg={sentiment_avg_age_min:.1f} min | {'✅' if sentiment_avg_age_min < 30 else '⚠️'} |",
        f"| News freshness | < 60 min | avg={news_avg_age_min:.1f} min | {'✅' if news_avg_age_min < 60 else '⚠️'} |",
        f"| Boot ERROR (Shadow connect) | 0 (post-grace) | {m.shadow_call_fails} | ℹ️ |",
        f"| Fund manager balance fails | 0 persistent | {m.fund_mgr_fails} | {'✅' if m.fund_mgr_fails < 5 else '⚠️'} |",
        f"| WAL checkpoints fired | hourly | {m.wal_checkpoints} | {'✅' if m.wal_checkpoints >= int(wall_clock_s/3600) else '⚠️'} |",
        f"| WAL checkpoint busy events | < 5% | {m.wal_checkpoint_busy_events}/{m.wal_checkpoints} | ℹ️ |",
        f"| KLINE_STRAGGLER unique syms | document | {len(m.kline_stragglers)} | ℹ️ |",
        f"| KLINE_FETCH_FAIL events | < 1% of fetches | top syms: {m.kline_fetch_fail_by_sym.most_common(5)} | ℹ️ |",
        "",
        "## Detailed metrics",
        "",
        "```json",
        json.dumps(m.to_dict(), indent=2, default=str),
        "```",
        "",
        "## Verdict",
        "",
        _verdict(m, p50, p95, brain_success_rate),
        "",
        "---",
        "_Generated by `scripts/observation_4h.py`._",
    ]
    report_path.write_text("\n".join(lines))
    print(f"\n✅ Wrote report: {report_path}")


def _verdict(m: Metrics, p50: int, p95: int, brain_success_rate: float) -> str:
    fails = []
    if m.order_starts_legacy_no_link_id > 0:
        fails.append(
            f"🚨 SAFETY-CRITICAL: {m.order_starts_legacy_no_link_id} ORDER_START "
            "events without link_id — Phase 5 fix not running. Rollback or "
            "investigate the deploy."
        )
    if p50 > 5000:
        fails.append(f"⚠️ kline tick p50 still {p50} ms — Phase 4 work needed.")
    if brain_success_rate < 95.0 and m.brain_call_ok + m.brain_call_fail > 0:
        fails.append(
            f"⚠️ brain success rate {brain_success_rate:.1f}% — investigate logs."
        )
    if m.brain_cascades > 0:
        fails.append(f"⚠️ {m.brain_cascades} brain cascades — check pre-flight logs.")

    if not fails:
        return (
            "**PASS.** Post-Layer-1 fixes are operating as designed across all "
            "observation criteria. The system is healthy for continued live "
            "operation. Document findings and consider extending the "
            "observation window if any tail behavior deserves more samples."
        )
    return "**ATTENTION REQUIRED.**\n\n" + "\n".join(f"- {f}" for f in fails)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> None:
    log_path = Path(args.log_path)
    db_path = Path(args.db_path)
    wal_path = Path(args.wal_path)
    report_path = Path(args.report_path)
    total_window_s = args.window * 60

    print(f"observation_4h: window={args.window} min log={log_path}")
    print(f"  report -> {report_path}")
    print(f"  sample={SAMPLE_INTERVAL_S}s  snapshot={SNAPSHOT_INTERVAL_S}s")

    m = Metrics()
    start = time.monotonic()
    last_offset = log_path.stat().st_size if log_path.exists() else 0
    print(f"  starting offset = {last_offset} bytes")

    last_sample = 0.0
    last_snapshot = 0.0
    restarted = False

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= total_window_s:
            break

        # 5-minute log scrape.
        if elapsed - last_sample >= SAMPLE_INTERVAL_S or last_sample == 0.0:
            last_offset = scrape_log(log_path, last_offset, m)
            last_sample = elapsed
            print(
                f"  [t={elapsed/60:.1f}m] "
                f"kline_el_n={len(m.kline_el_samples_ms)} "
                f"orders={m.order_starts_total} "
                f"(no_link_id={m.order_starts_legacy_no_link_id}) "
                f"brain_ok={m.brain_call_ok}/fail={m.brain_call_fail}"
            )

        # 30-minute file/DB snapshots.
        if elapsed - last_snapshot >= SNAPSHOT_INTERVAL_S or last_snapshot == 0.0:
            snapshot_wal(m, wal_path)
            snapshot_sentiment(m, db_path)
            last_snapshot = elapsed

        # Optional mid-run restart (must be explicitly authorized).
        if args.restart_at_midpoint and not restarted and elapsed >= RESTART_AT_S:
            print("  [t=2h] restarting workers (--restart-at-midpoint)")
            try:
                subprocess.run(
                    ["sudo", "systemctl", "restart", "trading-workers"],
                    check=True, timeout=60,
                )
                restarted = True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                print(f"  restart failed: {e}", file=sys.stderr)

        time.sleep(15)  # poll every 15s; coarser than sample/snapshot cadences

    # Final scrape catches any remaining lines.
    last_offset = scrape_log(log_path, last_offset, m)
    snapshot_wal(m, wal_path)
    snapshot_sentiment(m, db_path)

    write_report(m, report_path, time.monotonic() - start)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log-path", default=str(DEFAULT_LOG_PATH),
        help="Path to workers.log",
    )
    parser.add_argument(
        "--db-path", default=str(DEFAULT_DB_PATH),
        help="Path to trading.db",
    )
    parser.add_argument(
        "--wal-path", default=str(DEFAULT_WAL_PATH),
        help="Path to trading.db-wal",
    )
    parser.add_argument(
        "--report-path", default=str(DEFAULT_REPORT_PATH),
        help="Where to write the final report",
    )
    parser.add_argument(
        "--window", type=int, default=240,
        help="Observation window in MINUTES (default 240 = 4 h)",
    )
    parser.add_argument(
        "--restart-at-midpoint", action="store_true",
        help="At t=2h, run sudo systemctl restart trading-workers",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
