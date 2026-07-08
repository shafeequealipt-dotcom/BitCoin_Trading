#!/usr/bin/env python3
"""verify_watch_list — pre-deploy validation of [universe] watch_list.

Reads the watch_list from config.toml and confirms every symbol is
currently a tradeable USDT linear perpetual on Bybit. Exits 0 if all
valid; 1 if any are invalid (delisted, malformed, or unknown to Bybit).

Usage:
    .venv/bin/python scripts/verify_watch_list.py
    .venv/bin/python scripts/verify_watch_list.py --quiet  # only print failures

Exit codes:
    0 = all symbols valid and tradeable
    1 = one or more symbols invalid
    2 = config load or Bybit API failure
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only print invalid symbols and the final verdict",
    )
    args = parser.parse_args()

    # Late imports so --help works without a full project bootstrap.
    try:
        from src.config.settings import Settings
    except Exception as e:  # pragma: no cover — bootstrap failure
        print(f"FAIL: unable to import Settings: {e}", file=sys.stderr)
        return 2

    # Load config from project root (covers running from anywhere).
    Settings.reset()
    try:
        settings = Settings._load_fresh(
            str(PROJECT_DIR / "config.toml"),
            str(PROJECT_DIR / ".env"),
        )
    except Exception as e:
        print(f"FAIL: config load: {e}", file=sys.stderr)
        return 2

    watch_list = settings.universe.watch_list
    if not watch_list:
        print("FAIL: [universe] watch_list is empty", file=sys.stderr)
        return 1

    print(f"Verifying {len(watch_list)} symbols in [universe] watch_list against Bybit...")
    if not args.quiet:
        print()

    # Use pybit's public HTTP endpoint — no API keys required for instruments-info.
    try:
        from pybit.unified_trading import HTTP
    except Exception as e:  # pragma: no cover
        print(f"FAIL: pybit unavailable: {e}", file=sys.stderr)
        return 2

    client = HTTP(testnet=settings.bybit.testnet)

    # Fetch ALL linear instruments in one call (cheaper than per-symbol).
    try:
        resp = client.get_instruments_info(category="linear")
    except Exception as e:
        print(f"FAIL: Bybit get_instruments_info: {e}", file=sys.stderr)
        return 2
    if resp.get("retCode") != 0:
        print(f"FAIL: Bybit retCode={resp.get('retCode')} retMsg={resp.get('retMsg')}", file=sys.stderr)
        return 2

    items = resp.get("result", {}).get("list", [])
    tradeable: set[str] = {
        item["symbol"]
        for item in items
        if item.get("status") == "Trading"
        and item.get("quoteCoin") == "USDT"
        and item.get("contractType") == "LinearPerpetual"
    }

    print(f"Bybit returned {len(items)} instruments; {len(tradeable)} are tradeable USDT linear perps.")
    if not args.quiet:
        print()

    invalid: list[str] = []
    for sym in watch_list:
        if sym in tradeable:
            if not args.quiet:
                print(f"  ✓ {sym}")
        else:
            print(f"  ✗ {sym} — NOT a tradeable USDT linear perpetual on Bybit")
            invalid.append(sym)

    print()
    if invalid:
        print(
            f"FAIL: {len(invalid)} of {len(watch_list)} symbol(s) invalid: "
            f"{', '.join(invalid)}",
            file=sys.stderr,
        )
        return 1

    print(f"PASS: all {len(watch_list)} watch_list symbols are tradeable on Bybit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
