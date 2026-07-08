#!/usr/bin/env python3
"""
Health check script for Trading Intelligence MCP.

Verifies every critical component and returns structured results.

Usage:
    python scripts/health_check.py          # Human-readable output
    python scripts/health_check.py --json   # JSON output for monitoring

Exit codes:
    0 = healthy (all checks pass)
    1 = unhealthy (critical issue)
    2 = degraded (non-critical issue)
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path("/home/inshadaliqbal786/trading-intelligence-mcp")
DB_PATH = PROJECT_DIR / "data" / "trading.db"
LOG_DIR = PROJECT_DIR / "data" / "logs"

SERVICES = [
    "trading-workers",
    "trading-mcp-sse",
    "trading-brain",
]

# Thresholds
TICKER_STALE_SECONDS = 300       # 5 minutes
HEARTBEAT_STALE_SECONDS = 600    # 10 minutes
MIN_DISK_FREE_GB = 1.0
MAX_RAM_USAGE_MB = 900
MAX_DB_SIZE_MB = 500
BYBIT_STATUS_URL = "https://api.bybit.com/v5/market/time"
BYBIT_TIMEOUT_SEC = 10


class HealthCheck:
    """Run all health checks and collect results."""

    def __init__(self):
        self.checks: list[dict] = []
        self.critical_fail = False
        self.degraded = False

    def _add(self, name: str, status: str, message: str, critical: bool = True):
        self.checks.append({
            "name": name,
            "status": status,
            "message": message,
            "critical": critical,
        })
        if status == "FAIL":
            if critical:
                self.critical_fail = True
            else:
                self.degraded = True

    def check_services(self):
        """Verify all 3 systemd services are active."""
        for svc in SERVICES:
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", f"{svc}.service"],
                    capture_output=True, text=True, timeout=5,
                )
                is_active = result.stdout.strip() == "active"
                if is_active:
                    self._add(f"service:{svc}", "PASS", "running")
                else:
                    status = result.stdout.strip() or "unknown"
                    self._add(f"service:{svc}", "FAIL", f"status={status}")
            except Exception as e:
                self._add(f"service:{svc}", "FAIL", str(e))

    def check_database_accessible(self):
        """Verify database file exists and is readable."""
        if not DB_PATH.exists():
            self._add("db:accessible", "FAIL", f"not found: {DB_PATH}")
            return False
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.execute("SELECT 1")
            conn.close()
            self._add("db:accessible", "PASS", "connection OK")
            return True
        except Exception as e:
            self._add("db:accessible", "FAIL", str(e))
            return False

    def check_database_wal(self):
        """Verify WAL mode is enabled."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            conn.close()
            if mode == "wal":
                self._add("db:wal_mode", "PASS", "WAL enabled")
            else:
                self._add("db:wal_mode", "FAIL", f"mode={mode}, expected WAL", critical=False)
        except Exception as e:
            self._add("db:wal_mode", "FAIL", str(e), critical=False)

    def check_ticker_freshness(self):
        """Verify ticker_cache has been updated recently (workers alive)."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            row = conn.execute(
                "SELECT MAX(updated_at) as latest FROM ticker_cache"
            ).fetchone()
            conn.close()

            if row is None or row[0] is None:
                self._add("db:ticker_fresh", "FAIL", "no ticker data", critical=False)
                return

            latest = datetime.fromisoformat(str(row[0]))
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - latest).total_seconds()

            if age < TICKER_STALE_SECONDS:
                self._add("db:ticker_fresh", "PASS", f"updated {int(age)}s ago")
            else:
                self._add("db:ticker_fresh", "FAIL", f"stale: {int(age)}s old (limit: {TICKER_STALE_SECONDS}s)")
        except Exception as e:
            self._add("db:ticker_fresh", "FAIL", str(e), critical=False)

    def check_worker_heartbeat(self):
        """Check workers.log for recent activity."""
        log_file = LOG_DIR / "workers.log"
        if not log_file.exists():
            self._add("log:heartbeat", "FAIL", "workers.log not found", critical=False)
            return

        try:
            stat = log_file.stat()
            age = (datetime.now(timezone.utc) - datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)).total_seconds()
            if age < HEARTBEAT_STALE_SECONDS:
                self._add("log:heartbeat", "PASS", f"log modified {int(age)}s ago")
            else:
                self._add("log:heartbeat", "FAIL", f"log stale: {int(age)}s since last write", critical=False)
        except Exception as e:
            self._add("log:heartbeat", "FAIL", str(e), critical=False)

    def check_disk_space(self):
        """Verify at least 1GB free disk space."""
        try:
            usage = shutil.disk_usage("/")
            free_gb = usage.free / (1024 ** 3)
            if free_gb >= MIN_DISK_FREE_GB:
                self._add("system:disk", "PASS", f"{free_gb:.1f}GB free")
            else:
                self._add("system:disk", "FAIL", f"only {free_gb:.1f}GB free (need {MIN_DISK_FREE_GB}GB)")
        except Exception as e:
            self._add("system:disk", "FAIL", str(e))

    def check_ram_usage(self):
        """Verify RAM usage is below threshold."""
        try:
            with open("/proc/meminfo") as f:
                mem_info = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        mem_info[key] = int(parts[1])

            total_mb = mem_info.get("MemTotal", 0) / 1024
            available_mb = mem_info.get("MemAvailable", 0) / 1024
            used_mb = total_mb - available_mb

            if used_mb < MAX_RAM_USAGE_MB:
                self._add("system:ram", "PASS", f"{used_mb:.0f}MB / {total_mb:.0f}MB used")
            else:
                self._add("system:ram", "FAIL", f"{used_mb:.0f}MB / {total_mb:.0f}MB used (limit: {MAX_RAM_USAGE_MB}MB)")
        except Exception as e:
            self._add("system:ram", "FAIL", str(e))

    def check_bybit_reachable(self):
        """Quick connectivity check to Bybit mainnet."""
        try:
            req = urllib.request.Request(BYBIT_STATUS_URL, method="GET")
            req.add_header("User-Agent", "trading-mcp-healthcheck/1.0")
            with urllib.request.urlopen(req, timeout=BYBIT_TIMEOUT_SEC) as resp:
                if resp.status == 200:
                    self._add("api:bybit", "PASS", "reachable (mainnet)")
                else:
                    self._add("api:bybit", "FAIL", f"HTTP {resp.status}", critical=False)
        except Exception as e:
            self._add("api:bybit", "FAIL", f"unreachable: {e}", critical=False)

    def check_database_size(self):
        """Verify database is not unreasonably large."""
        if not DB_PATH.exists():
            return
        try:
            size_mb = DB_PATH.stat().st_size / (1024 * 1024)
            if size_mb < MAX_DB_SIZE_MB:
                self._add("db:size", "PASS", f"{size_mb:.1f}MB (limit: {MAX_DB_SIZE_MB}MB)")
            else:
                self._add("db:size", "FAIL", f"{size_mb:.1f}MB exceeds {MAX_DB_SIZE_MB}MB limit", critical=False)
        except Exception as e:
            self._add("db:size", "FAIL", str(e), critical=False)

    def run_all(self):
        """Execute all health checks."""
        self.check_services()
        db_ok = self.check_database_accessible()
        if db_ok:
            self.check_database_wal()
            self.check_ticker_freshness()
            self.check_database_size()
        self.check_worker_heartbeat()
        self.check_disk_space()
        self.check_ram_usage()
        self.check_bybit_reachable()

    def to_json(self) -> dict:
        """Return results as a JSON-serializable dict."""
        if self.critical_fail:
            overall = "unhealthy"
        elif self.degraded:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "status": overall,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": self.checks,
            "summary": {
                "total": len(self.checks),
                "passed": sum(1 for c in self.checks if c["status"] == "PASS"),
                "failed": sum(1 for c in self.checks if c["status"] == "FAIL"),
            },
        }

    def print_human(self):
        """Print human-readable health report."""
        print("==========================================")
        print(" Trading Intelligence MCP — Health Check")
        print(f" {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print("==========================================")
        print()

        for check in self.checks:
            icon = "PASS" if check["status"] == "PASS" else "FAIL"
            crit = " [CRITICAL]" if check["status"] == "FAIL" and check["critical"] else ""
            print(f"  [{icon}] {check['name']}: {check['message']}{crit}")

        print()
        report = self.to_json()
        summary = report["summary"]
        print(f"  Result: {report['status'].upper()} ({summary['passed']}/{summary['total']} passed)")
        print()


def main():
    json_mode = "--json" in sys.argv

    hc = HealthCheck()
    hc.run_all()

    if json_mode:
        print(json.dumps(hc.to_json(), indent=2))
    else:
        hc.print_human()

    if hc.critical_fail:
        sys.exit(1)
    elif hc.degraded:
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
