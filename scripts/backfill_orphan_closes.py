#!/usr/bin/env python3
"""One-shot operator backfill for ORPHAN CLOSES (lost-PnL heal, 2026-06-17).

Companion to ``scripts/backfill_orphan_positions.py``. That script clears stale
rows from the ``positions`` table; THIS one heals the *accounting* gap left by a
pre-durable-open orphan: a position that was live on the exchange with NO local
record (the order was placed but its thesis save was lost — e.g. a restart raced
the save), so when it closed green the realized PnL never reached ``trade_history``
or ``daily_pnl``. This is the exact failure that lost a UNIUSDT green close.

The source path is fixed by the thesis-before-order change (a trade can no longer
go live without a durable ``reserving`` thesis). Per CLAUDE.md Rule 3, an
automatic cron sweep would be a band-aid that hides the source path, so this is
**operator-supervised, one-shot**, and DRY-RUN by default.

What it does:
  * Queries the exchange closed-pnl ledger (the authority for realized PnL).
  * For each closed trade, derives trade_id = "bd-" + orderId and checks whether
    ``trade_history`` already has it. Rows that exist are already booked — skipped.
  * Rows with NO ``trade_history`` match are ORPHAN CLOSES whose PnL was lost.
  * --apply backfills each idempotently: INSERT OR REPLACE into ``trade_history``
    (keyed by trade_id), closes any matching open/reserving thesis, and adjusts
    that day's ``daily_pnl`` aggregate.

Safety:
  * DRY-RUN by default — prints the orphan closes; writes nothing.
  * --apply REFUSES to run while the trading workers are active, because the live
    DailyPnLManager persists ``daily_pnl`` via INSERT OR REPLACE and would
    overwrite our adjustment. Stop the workers, run --apply, then start them
    (boot restores the corrected daily_pnl). Override only with --force-running.
  * Idempotent: re-running after --apply finds nothing (trade_id already present).

Usage:
    .venv/bin/python scripts/backfill_orphan_closes.py                 # report
    sudo systemctl stop trading-workers
    .venv/bin/python scripts/backfill_orphan_closes.py --apply --yes   # heal
    sudo systemctl start trading-workers

Output markers:
    CLOSE_ORPHAN_SCAN_START / CLOSE_ORPHAN_FOUND / CLOSE_ORPHAN_BACKFILLED /
    CLOSE_ORPHAN_SCAN_DONE
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

_CLOSED_PNL_PATH = "/v5/position/closed-pnl"
_PER_SYMBOL_LIMIT = 50          # recent closes to pull per symbol
_TIME_MATCH_WINDOW_S = 120.0    # exit-time window for matching a close to trade_history
_LOOKBACK_DAYS = 3              # candidate symbols = those traded in this window

# Dedup is by (symbol, exit_time window), NOT by order id or pnl:
#  * a closed-pnl row's `orderId` is the CLOSING order id, which never equals
#    trade_history.trade_id (built from the OPENING order id);
#  * trade_history.pnl is the system's WS-derived net, which differs from the
#    exchange closedPnl (verified: ETH close exchange -0.2005 vs recorded
#    -0.1449, 7s apart) — so a pnl match yields false orphans.
# The close TIMESTAMPS line up within seconds, so the exit-time window is the
# reliable identity for "is this close already recorded".


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _workers_active() -> bool:
    """True if the trading-workers systemd unit is active (best-effort)."""
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "trading-workers.service"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() == "active"
    except Exception:
        return False


async def _candidate_symbols(db, extra: list[str] | None = None) -> list[str]:
    """Symbols this account actually traded recently + any currently open.

    The settleCoin-wide closed-pnl scan returns an unreliable window on the demo
    venue, so we scan per symbol over the set the system actually touched. That
    set is the distinct symbols from trade_thesis within the lookback plus the
    live positions table — the symbols whose close could plausibly have been
    lost AND were re-traded or are still open.

    LIMITATION: a true orphan whose thesis save was lost AND whose symbol was not
    re-traded within the lookback (and is no longer open) would not appear here.
    Use --symbols to force such a symbol into the scan when you know it.
    """
    syms: set[str] = set()
    try:
        async with db.execute(
            "SELECT DISTINCT symbol FROM trade_thesis "
            "WHERE opened_at >= datetime('now', ?)",
            (f"-{_LOOKBACK_DAYS} days",),
        ) as cur:
            for r in await cur.fetchall():
                syms.add(r["symbol"])
    except Exception as e:
        print(f"WARN: trade_thesis candidate query failed: {str(e)[:120]}")
    try:
        async with db.execute("SELECT DISTINCT symbol FROM positions") as cur:
            for r in await cur.fetchall():
                syms.add(r["symbol"])
    except Exception:
        pass
    for s in (extra or []):
        s = str(s).strip().upper()
        if s:
            syms.add(s)
    return sorted(s for s in syms if s)


async def _fetch_closed_pnl(symbols: list[str]):
    """Return closed-pnl rows for each symbol (most-recent _PER_SYMBOL_LIMIT)."""
    import aiohttp
    from src.config.settings import Settings
    from src.bybit_demo.bybit_demo_client import BybitDemoClient

    settings = Settings.load()
    bd = getattr(settings, "bybit_demo", None)
    if bd is None or not getattr(bd, "api_key", "") or not getattr(bd, "api_secret", ""):
        print("FATAL: bybit_demo credentials missing (BYBIT_DEMO_API_KEY / "
              "BYBIT_DEMO_API_SECRET). Cannot query the exchange ledger.")
        return None

    rows: list[dict] = []
    async with aiohttp.ClientSession() as session:
        client = BybitDemoClient(
            session=session,
            base_url=bd.base_url,
            api_key=bd.api_key,
            api_secret=bd.api_secret,
            recv_window=bd.recv_window,
            timeout_seconds=bd.timeout_seconds,
            retry_attempts=bd.retry_attempts,
            retry_base_delay_seconds=bd.retry_base_delay_seconds,
        )
        for sym in symbols:
            try:
                envelope = await client.get(
                    _CLOSED_PNL_PATH,
                    {"category": "linear", "symbol": sym, "limit": _PER_SYMBOL_LIMIT},
                    op="orphan_heal_scan",
                )
                rows.extend((envelope.get("result") or {}).get("list") or [])
            except Exception as e:
                print(f"  WARN: closed-pnl query failed for {sym}: {str(e)[:100]}")
    return rows


async def _already_booked(db, symbol: str, updated_ms: float) -> bool:
    """True if trade_history already has this close, matched by (symbol, exit_time).

    IMPORTANT: dedup is by CLOSE TIMESTAMP, not pnl. Verified against live data,
    the system stores its own WS-derived net in trade_history.pnl which differs
    from the exchange closedPnl (e.g. ETH close: exchange -0.2005 vs recorded
    -0.1449, 7s apart), so a pnl match produces false orphans. The close
    timestamps, however, line up within seconds, so the exit-time window is the
    reliable identity. A genuine orphan has NO trade_history row for the symbol
    anywhere near its close time.
    """
    if updated_ms <= 0:
        # No exchange timestamp to disambiguate — cannot safely call it an
        # orphan; treat as booked to avoid a false backfill.
        return True
    async with db.execute(
        "SELECT exit_time FROM trade_history WHERE symbol = ?", (symbol,)
    ) as cur:
        rows = await cur.fetchall()
    for r in rows:
        try:
            ex_dt = datetime.fromisoformat(r["exit_time"])
            if ex_dt.tzinfo is None:
                ex_dt = ex_dt.replace(tzinfo=timezone.utc)
            if abs(ex_dt.timestamp() * 1000.0 - updated_ms) <= _TIME_MATCH_WINDOW_S * 1000.0:
                return True
        except Exception:
            continue
    return False


async def _backfill_one(db, o: dict) -> None:
    """Book ONE orphan close: trade_history (idempotent) + daily_pnl adjust.

    MUST be called with the workers stopped (the daily_pnl read-modify-write
    would otherwise race the live DailyPnLManager). The caller commits.
    Factored out so the Pass-4 simulation can exercise the apply path directly.
    """
    # 1) permanent ledger (idempotent by trade_id)
    await db.execute(
        """INSERT OR REPLACE INTO trade_history
           (trade_id, symbol, side, entry_price, exit_price, qty, pnl,
            pnl_pct, strategy, signal_confidence, notes, entry_time,
            exit_time, exchange_mode)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'orphan_close_backfill', 0,
                   'healed by backfill_orphan_closes.py', ?, ?, 'bybit_demo')""",
        (o["trade_id"], o["symbol"], o["open_side"], o["entry"], o["exit"],
         o["qty"], o["pnl"], o["pnl_pct"], o["entry_iso"], o["exit_iso"]),
    )
    # (No thesis-close step: a genuine orphan close has no local thesis, and the
    # closed-pnl orderId is the CLOSE order id which never matches
    # trade_thesis.order_id. Any lingering reserving/open thesis is resolved by
    # sweep_reserving_theses, not here.)
    # 2) adjust that day's daily_pnl aggregate (safe: workers stopped).
    if o["exit_date"]:
        # Match the live DailyPnLManager.add_trade convention exactly: a 0.0 PnL
        # is a WIN (pnl >= 0), so the heal reconciles to the system of record.
        win = 1 if o["pnl"] >= 0 else 0
        loss = 1 if o["pnl"] < 0 else 0
        async with db.execute(
            "SELECT 1 FROM daily_pnl WHERE date=?", (o["exit_date"],)
        ) as cur:
            exists = await cur.fetchone() is not None
        if exists:
            await db.execute(
                "UPDATE daily_pnl SET realized_pnl=realized_pnl+?, "
                "total_trades=total_trades+1, wins=wins+?, losses=losses+? "
                "WHERE date=?",
                (o["pnl"], win, loss, o["exit_date"]),
            )
        else:
            # Carry starting_equity forward from the most recent prior day so a
            # brand-new row doesn't seed 0 equity (which the next boot's
            # _restore_today_from_db would load if the date is today).
            async with db.execute(
                "SELECT ending_equity FROM daily_pnl "
                "WHERE date < ? ORDER BY date DESC LIMIT 1",
                (o["exit_date"],),
            ) as cur:
                _prev = await cur.fetchone()
            _start_eq = float(_prev["ending_equity"]) if _prev and _prev["ending_equity"] else 0.0
            await db.execute(
                "INSERT INTO daily_pnl (date, starting_equity, "
                "ending_equity, realized_pnl, total_trades, wins, losses) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (o["exit_date"], _start_eq, _start_eq, o["pnl"], win, loss),
            )


async def _run(apply: bool, auto_yes: bool, force_running: bool,
               since_hours: float = 0.0,
               extra_symbols: list[str] | None = None) -> int:
    import aiosqlite

    db_path = _PROJECT_ROOT / "data" / "trading.db"
    if not db_path.exists():
        print(f"FATAL: trading.db not found at {db_path}")
        return 1

    if apply and _workers_active() and not force_running:
        print("REFUSING --apply: trading-workers is ACTIVE. The live "
              "DailyPnLManager would overwrite the daily_pnl adjustment.\n"
              "Stop it first:  sudo systemctl stop trading-workers\n"
              "(or pass --force-running to override — NOT recommended).")
        return 2

    orphans = []
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row

        # Authoritative window: trade_history only began recording bybit_demo
        # closes on a certain date (pre-wiring the table was simply empty — NOT
        # retention pruning, which is 60 days). Exchange closes OLDER than the
        # first recorded trade are not comparable: "missing from trade_history"
        # there means "before recording existed", not "orphan". A --since-hours
        # override can tighten the window further.
        async with db.execute("SELECT MIN(exit_time) AS m FROM trade_history") as cur:
            _r = await cur.fetchone()
        floor_ms = 0.0
        if _r and _r["m"]:
            try:
                _fd = datetime.fromisoformat(_r["m"])
                if _fd.tzinfo is None:
                    _fd = _fd.replace(tzinfo=timezone.utc)
                floor_ms = _fd.timestamp() * 1000.0
            except Exception:
                floor_ms = 0.0
        if since_hours and since_hours > 0:
            import time as _time
            since_floor = (_time.time() - since_hours * 3600.0) * 1000.0
            floor_ms = max(floor_ms, since_floor)
        floor_iso = (datetime.fromtimestamp(floor_ms / 1000.0, tz=timezone.utc).isoformat()
                     if floor_ms else "(none)")

        symbols = await _candidate_symbols(db, extra_symbols)
        print(f"\nCLOSE_ORPHAN_SCAN_START candidate_symbols={len(symbols)} "
              f"authoritative_floor={floor_iso} apply={apply}")
        rows = await _fetch_closed_pnl(symbols)
        if rows is None:
            return 1
        print(f"exchange_closes_scanned={len(rows)}")
        seen_close_oids: set[str] = set()
        skipped_pre_window = 0
        for row in rows:
            close_oid = str(row.get("orderId") or "")
            if not close_oid or close_oid in seen_close_oids:
                continue
            seen_close_oids.add(close_oid)
            symbol = str(row.get("symbol") or "")
            closed_pnl = _safe_float(row.get("closedPnl"))
            updated_ms = _safe_float(row.get("updatedTime"))
            # Skip closes from before trade_history started recording — not
            # comparable, would be false orphans.
            if floor_ms and 0 < updated_ms < floor_ms:
                skipped_pre_window += 1
                continue
            # Dedup by (symbol, exit_time). NOTE: a closed-pnl row's orderId is
            # the CLOSING order id, which never equals trade_history.trade_id
            # (built from the OPENING order id), and trade_history.pnl is a
            # WS-derived net that differs from closedPnl — so we dedup on the
            # close timestamp only (see _already_booked).
            if await _already_booked(db, symbol, updated_ms):
                continue
            # Genuine orphan close — never recorded locally.
            close_side = str(row.get("side") or "Buy")
            # closed-pnl `side` is the CLOSING order side; the OPEN side is the
            # opposite (a Sell closes a long, a Buy closes a short).
            open_side = "Buy" if close_side == "Sell" else "Sell"
            avg_entry = _safe_float(row.get("avgEntryPrice"))
            avg_exit = _safe_float(row.get("avgExitPrice"))
            qty = _safe_float(row.get("qty"))
            notional = avg_entry * qty
            pnl_pct = (closed_pnl / notional * 100.0) if notional > 0 else 0.0
            created_ms = _safe_float(row.get("createdTime"))
            entry_iso = (datetime.fromtimestamp(created_ms / 1000.0, tz=timezone.utc).isoformat()
                         if created_ms > 0 else "")
            exit_iso = (datetime.fromtimestamp(updated_ms / 1000.0, tz=timezone.utc).isoformat()
                        if updated_ms > 0 else "")
            exit_date = exit_iso[:10] if exit_iso else ""
            # Idempotent ledger key from the unique CLOSE order id (re-runs match
            # the inserted row via _already_booked and skip).
            trade_id = f"bd-orphan-{close_oid}"
            o = dict(trade_id=trade_id, order_id=close_oid, symbol=symbol,
                     open_side=open_side, entry=avg_entry, exit=avg_exit, qty=qty,
                     pnl=closed_pnl, pnl_pct=pnl_pct, entry_iso=entry_iso,
                     exit_iso=exit_iso, exit_date=exit_date)
            orphans.append(o)
            print(f"  CLOSE_ORPHAN_FOUND | sym={symbol} close_oid={close_oid[:12]} "
                  f"side={open_side} entry={avg_entry} exit={avg_exit} qty={qty} "
                  f"pnl={closed_pnl:+.4f} pnl_pct={pnl_pct:+.3f}% closed={exit_iso}")

        if not orphans:
            print(f"\nNo orphan closes in the authoritative window "
                  f"(skipped_pre_window={skipped_pre_window}). Nothing to heal.")
            print("CLOSE_ORPHAN_SCAN_DONE backfilled=0")
            return 0

        print(f"\nFound {len(orphans)} orphan close(s) "
              f"totalling {sum(o['pnl'] for o in orphans):+.4f} USDT "
              f"(skipped {skipped_pre_window} pre-recording-era closes).")

        if not apply:
            print("\nDRY-RUN (default): no writes. Re-run with --apply (workers "
                  "stopped) to book these into trade_history + daily_pnl.")
            print("CLOSE_ORPHAN_SCAN_DONE backfilled=0 dry_run=Y")
            return 0

        if not auto_yes:
            print(f"\nBackfill {len(orphans)} orphan close(s) into trade_history "
                  f"+ daily_pnl? [y/N]: ", end="", flush=True)
            if input().strip().lower() not in ("y", "yes"):
                print("Aborted; no writes performed.")
                return 0

        backfilled = 0
        for o in orphans:
            try:
                await _backfill_one(db, o)
                backfilled += 1
                print(f"  CLOSE_ORPHAN_BACKFILLED | sym={o['symbol']} "
                      f"oid={o['order_id'][:12]} pnl={o['pnl']:+.4f} "
                      f"date={o['exit_date']}")
            except Exception as e:
                print(f"  CLOSE_ORPHAN_BACKFILL_FAIL | sym={o['symbol']} "
                      f"oid={o['order_id'][:12]} err={str(e)[:120]}")

        await db.commit()
        print(f"\nCLOSE_ORPHAN_SCAN_DONE backfilled={backfilled} "
              f"orphans_found={len(orphans)}")
        print("Start the workers now: boot restores the corrected daily_pnl.")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--apply", action="store_true",
                   help="Book the orphan closes (default is dry-run report only).")
    p.add_argument("--yes", action="store_true",
                   help="Skip interactive confirmation (unattended operator run).")
    p.add_argument("--force-running", action="store_true",
                   help="Allow --apply while workers are active (NOT recommended).")
    p.add_argument("--since-hours", type=float, default=0.0,
                   help="Only consider closes within the last N hours (tightens "
                        "the auto authoritative window; 0 = use the window since "
                        "trade_history began recording).")
    p.add_argument("--symbols", type=str, default="",
                   help="Comma-separated symbols to FORCE into the scan, in "
                        "addition to recently-traded/open ones (use when you know "
                        "a lost symbol that wasn't re-traded within the lookback).")
    args = p.parse_args()
    _extra = [s for s in args.symbols.split(",") if s.strip()]
    return asyncio.run(_run(apply=args.apply, auto_yes=args.yes,
                            force_running=args.force_running,
                            since_hours=args.since_hours,
                            extra_symbols=_extra))


if __name__ == "__main__":
    raise SystemExit(main())
