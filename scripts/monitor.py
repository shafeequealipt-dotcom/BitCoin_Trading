#!/usr/bin/env python3
"""
Live monitoring dashboard for Trading Intelligence MCP.

Refreshes every 5 seconds. Press Ctrl+C to exit.
Reads all data directly from SQLite database.

Usage:
    python scripts/monitor.py
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path("/home/inshadaliqbal786/trading-intelligence-mcp")
DB_PATH = PROJECT_DIR / "data" / "trading.db"
LOG_DIR = PROJECT_DIR / "data" / "logs"

REFRESH_INTERVAL = 5

SERVICES = [
    ("trading-workers", "Workers"),
    ("trading-mcp-sse", "MCP SSE"),
    ("trading-brain", "Brain"),
]

TRACKED_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]


def get_terminal_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def clear_screen():
    os.system("clear" if os.name != "nt" else "cls")


def get_service_info(svc_name: str) -> dict:
    """Get systemd service status, PID, and memory."""
    info = {"status": "unknown", "pid": "-", "mem": "-"}
    try:
        result = subprocess.run(
            ["systemctl", "is-active", f"{svc_name}.service"],
            capture_output=True, text=True, timeout=3,
        )
        info["status"] = result.stdout.strip()

        if info["status"] == "active":
            pid_result = subprocess.run(
                ["systemctl", "show", "-p", "MainPID", f"{svc_name}.service"],
                capture_output=True, text=True, timeout=3,
            )
            pid = pid_result.stdout.strip().split("=")[-1]
            info["pid"] = pid
            if pid != "0":
                try:
                    with open(f"/proc/{pid}/status") as f:
                        for line in f:
                            if line.startswith("VmRSS:"):
                                kb = int(line.split()[1])
                                info["mem"] = f"{kb // 1024}MB"
                                break
                except (FileNotFoundError, ValueError, PermissionError):
                    pass
    except Exception:
        pass
    return info


def get_db_connection():
    """Open a read-only database connection."""
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def fetch_prices(conn) -> list[dict]:
    """Get latest ticker prices."""
    try:
        placeholders = ",".join("?" for _ in TRACKED_SYMBOLS)
        rows = conn.execute(
            f"SELECT symbol, last_price, change_24h_pct, volume_24h, updated_at "
            f"FROM ticker_cache WHERE symbol IN ({placeholders}) "
            f"ORDER BY symbol",
            TRACKED_SYMBOLS,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def fetch_signals(conn) -> list[dict]:
    """Get latest signals."""
    try:
        rows = conn.execute(
            "SELECT symbol, signal_type, confidence, source, created_at "
            "FROM signals ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def fetch_fear_greed(conn) -> dict | None:
    """Get latest Fear & Greed value."""
    try:
        row = conn.execute(
            "SELECT value, classification, timestamp "
            "FROM fear_greed_index ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def fetch_pnl_today(conn) -> dict:
    """Get today's realized PnL."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) as total_pnl, COUNT(*) as trades "
            "FROM trade_history WHERE exit_time >= ?",
            (today,),
        ).fetchone()
        return {"total_pnl": row[0], "trades": row[1]} if row else {"total_pnl": 0, "trades": 0}
    except Exception:
        return {"total_pnl": 0, "trades": 0}


def fetch_positions(conn) -> list[dict]:
    """Get open positions."""
    try:
        rows = conn.execute(
            "SELECT symbol, side, size, entry_price, mark_price, unrealized_pnl, leverage "
            "FROM positions WHERE size > 0 ORDER BY symbol"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def fetch_brain_last(conn) -> dict | None:
    """Get the last brain decision."""
    try:
        row = conn.execute(
            "SELECT action_taken, trigger, tokens_used, cost_usd, created_at "
            "FROM brain_decisions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def get_log_errors_last_hour() -> dict[str, int]:
    """Count ERROR/CRITICAL in each log file from the last hour."""
    counts = {}
    cutoff = time.time() - 3600
    for log_name in ["workers.log", "brain.log", "mcp.log"]:
        log_path = LOG_DIR / log_name
        count = 0
        if log_path.exists():
            try:
                with open(log_path, "rb") as f:
                    # Read last 500KB to save time on large files
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 512 * 1024))
                    for line in f:
                        try:
                            text = line.decode("utf-8", errors="replace")
                            if "ERROR" in text or "CRITICAL" in text:
                                count += 1
                        except Exception:
                            pass
            except Exception:
                pass
        counts[log_name] = count
    return counts


def get_system_resources() -> dict:
    """Get RAM and disk usage."""
    info = {"ram_used": "-", "ram_total": "-", "disk_used": "-", "disk_total": "-", "disk_pct": "-"}
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
        total = mem.get("MemTotal", 0) / 1024
        avail = mem.get("MemAvailable", 0) / 1024
        used = total - avail
        info["ram_used"] = f"{used:.0f}MB"
        info["ram_total"] = f"{total:.0f}MB"
    except Exception:
        pass

    try:
        usage = shutil.disk_usage("/")
        info["disk_used"] = f"{usage.used / (1024**3):.1f}GB"
        info["disk_total"] = f"{usage.total / (1024**3):.1f}GB"
        info["disk_pct"] = f"{(usage.used / usage.total) * 100:.0f}%"
    except Exception:
        pass

    return info


def render_dashboard():
    """Render one frame of the dashboard."""
    width = get_terminal_width()
    line = "=" * min(width, 60)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print(line)
    print(f" TRADING INTELLIGENCE MCP — LIVE MONITOR")
    print(f" {now}")
    print(line)
    print()

    # ---- Services ----
    print("SERVICES")
    for svc_name, label in SERVICES:
        info = get_service_info(svc_name)
        status = info["status"].upper()
        if status == "ACTIVE":
            marker = "[OK]"
        elif status == "INACTIVE":
            marker = "[--]"
        else:
            marker = "[!!]"
        print(f"  {marker} {label:<12}  PID: {info['pid']:<8}  Mem: {info['mem']}")
    print()

    # ---- Database content ----
    conn = get_db_connection()
    if conn is None:
        print("DATABASE: Not accessible")
        print()
    else:
        try:
            # Prices
            prices = fetch_prices(conn)
            if prices:
                print("PRICES")
                for p in prices:
                    sym = p["symbol"].replace("USDT", "")
                    price = p["last_price"]
                    change = p.get("change_24h_pct", 0) or 0
                    arrow = "+" if change >= 0 else ""
                    vol = p.get("volume_24h", 0) or 0
                    if vol >= 1_000_000_000:
                        vol_str = f"{vol / 1_000_000_000:.1f}B"
                    elif vol >= 1_000_000:
                        vol_str = f"{vol / 1_000_000:.1f}M"
                    else:
                        vol_str = f"{vol:,.0f}"
                    print(f"  {sym:<6}  ${price:>12,.2f}  {arrow}{change:>6.2f}%  Vol: {vol_str}")
                print()

            # Fear & Greed
            fg = fetch_fear_greed(conn)
            if fg:
                print(f"FEAR & GREED: {fg['value']} ({fg['classification']})")
                print()

            # Signals
            signals = fetch_signals(conn)
            if signals:
                print("LATEST SIGNALS")
                for s in signals:
                    conf = s.get("confidence", 0) or 0
                    bar = "#" * int(conf * 10)
                    sym = s["symbol"].replace("USDT", "")
                    print(f"  {sym:<6}  {s['signal_type']:<12}  [{bar:<10}] {conf:.0%}  ({s.get('source', '')})")
                print()

            # PnL
            pnl = fetch_pnl_today(conn)
            pnl_val = pnl["total_pnl"]
            pnl_sign = "+" if pnl_val >= 0 else ""
            print(f"TODAY'S PnL: {pnl_sign}${pnl_val:.2f} ({pnl['trades']} trades)")
            print()

            # Positions
            positions = fetch_positions(conn)
            if positions:
                print("OPEN POSITIONS")
                for pos in positions:
                    sym = pos["symbol"].replace("USDT", "")
                    upnl = pos.get("unrealized_pnl", 0) or 0
                    upnl_sign = "+" if upnl >= 0 else ""
                    print(
                        f"  {sym:<6}  {pos['side']:<5}  "
                        f"Size: {pos['size']:.4f}  "
                        f"Entry: ${pos['entry_price']:,.2f}  "
                        f"uPnL: {upnl_sign}${upnl:.2f}  "
                        f"{pos.get('leverage', 1)}x"
                    )
                print()
            else:
                print("OPEN POSITIONS: None")
                print()

            # Brain
            brain = fetch_brain_last(conn)
            if brain:
                print("BRAIN (last decision)")
                print(f"  Action:  {brain.get('action_taken', '-')}")
                print(f"  Trigger: {brain.get('trigger', '-')}")
                print(f"  Tokens:  {brain.get('tokens_used', 0)}  Cost: ${brain.get('cost_usd', 0):.4f}")
                print(f"  Time:    {brain.get('created_at', '-')}")
                print()

        except Exception as e:
            print(f"DB Error: {e}")
            print()
        finally:
            conn.close()

    # ---- Logs ----
    errors = get_log_errors_last_hour()
    total_errors = sum(errors.values())
    print(f"LOG ERRORS (last hour): {total_errors}")
    for name, count in errors.items():
        marker = "!!" if count > 0 else "ok"
        print(f"  [{marker}] {name}: {count}")
    print()

    # ---- System Resources ----
    res = get_system_resources()
    print(f"SYSTEM")
    print(f"  RAM:  {res['ram_used']} / {res['ram_total']}")
    print(f"  Disk: {res['disk_used']} / {res['disk_total']} ({res['disk_pct']})")
    print()

    # ---- Uptime ----
    try:
        with open("/proc/uptime") as f:
            uptime_sec = float(f.read().split()[0])
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        mins = int((uptime_sec % 3600) // 60)
        print(f"UPTIME: {days}d {hours}h {mins}m")
    except Exception:
        pass

    print()
    print(f"Refreshing every {REFRESH_INTERVAL}s. Press Ctrl+C to exit.")


def main():
    try:
        while True:
            clear_screen()
            render_dashboard()
            time.sleep(REFRESH_INTERVAL)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
