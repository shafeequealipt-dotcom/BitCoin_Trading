"""Backfill 1h/4h/1d klines, funding-rate history, and open-interest history
for the research universe from Bybit's PUBLIC market-data API (no key needed,
read-only, cannot place orders or touch the live account).

Own database (research/data/market_history.db) -- never touches shadow.db or
trading.db. Resumable: re-running skips ranges already covered. Paced well
under Bybit's public rate limit with exponential backoff on 403/429.
"""
import json, sqlite3, time, sys, requests
from datetime import datetime, timezone

DB = "research/data/market_history.db"
UNIVERSE = json.load(open("research/data/universe.json"))
BASE = "https://api.bybit.com/v5/market"
PACE_S = 0.25  # ~4 req/s, well under Bybit's public per-IP limit
NOW_MS = int(time.time() * 1000)
TWO_YEARS_MS = 2 * 365 * 24 * 3600 * 1000
START_MS = NOW_MS - TWO_YEARS_MS

INTERVAL_MAP = {"1h": "60", "4h": "240", "1d": "D"}
INTERVAL_MS = {"1h": 3600_000, "4h": 4 * 3600_000, "1d": 24 * 3600_000}

conn = sqlite3.connect(DB)
conn.executescript("""
CREATE TABLE IF NOT EXISTS klines (
    symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, turnover REAL,
    PRIMARY KEY (symbol, interval, ts)
);
CREATE TABLE IF NOT EXISTS funding (
    symbol TEXT NOT NULL, ts INTEGER NOT NULL, rate REAL,
    PRIMARY KEY (symbol, ts)
);
CREATE TABLE IF NOT EXISTS open_interest (
    symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL, oi REAL,
    PRIMARY KEY (symbol, interval, ts)
);
CREATE TABLE IF NOT EXISTS backfill_progress (
    symbol TEXT NOT NULL, kind TEXT NOT NULL, status TEXT, rows INTEGER, updated_at TEXT,
    PRIMARY KEY (symbol, kind)
);
""")
conn.commit()


def get(path, params, tries=6):
    for attempt in range(tries):
        try:
            r = requests.get(f"{BASE}/{path}", params=params, timeout=20)
            if r.status_code == 200:
                d = r.json()
                if d.get("retCode") == 0:
                    return d["result"]
                if d.get("retCode") in (10006, 10018):  # rate limit codes
                    time.sleep(2 ** attempt)
                    continue
                return d["result"]  # symbol not found / no data -- return empty-ish
            time.sleep(2 ** attempt)
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return {"list": []}


def mark(symbol, kind, status, rows):
    conn.execute(
        "INSERT INTO backfill_progress(symbol, kind, status, rows, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(symbol, kind) DO UPDATE SET status=excluded.status, rows=excluded.rows, updated_at=excluded.updated_at",
        (symbol, kind, status, rows, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def backfill_klines(symbol, tf):
    api_iv = INTERVAL_MAP[tf]
    step_ms = INTERVAL_MS[tf]
    have = conn.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM klines WHERE symbol=? AND interval=?", (symbol, tf)).fetchone()
    end = NOW_MS
    total_rows = have[2] or 0
    # Walk backwards from `end`, 1000 candles per call, until we reach START_MS
    # or hit empty results (symbol has no more history / wasn't listed yet).
    if have[0] and have[0] <= START_MS + step_ms:
        mark(symbol, f"klines_{tf}", "complete", total_rows)
        return total_rows
    cursor_end = have[0] - step_ms if have[0] else end
    empty_streak = 0
    while cursor_end > START_MS and empty_streak < 3:
        res = get("kline", {"category": "linear", "symbol": symbol, "interval": api_iv,
                             "end": cursor_end, "limit": 1000})
        rows = res.get("list", [])
        if not rows:
            empty_streak += 1
            cursor_end -= step_ms * 1000
            time.sleep(PACE_S)
            continue
        empty_streak = 0
        batch = [(symbol, tf, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                  float(r[5]), float(r[6])) for r in rows]
        conn.executemany(
            "INSERT OR IGNORE INTO klines(symbol,interval,ts,open,high,low,close,volume,turnover) VALUES (?,?,?,?,?,?,?,?,?)",
            batch)
        conn.commit()
        total_rows += len(batch)
        oldest = min(int(r[0]) for r in rows)
        cursor_end = oldest - step_ms
        time.sleep(PACE_S)
    mark(symbol, f"klines_{tf}", "complete", total_rows)
    return total_rows


def backfill_funding(symbol):
    have = conn.execute("SELECT MIN(ts), COUNT(*) FROM funding WHERE symbol=?", (symbol,)).fetchone()
    total = have[1] or 0
    if have[0] and have[0] <= START_MS + 8 * 3600_000:
        mark(symbol, "funding", "complete", total)
        return total
    cursor_end = (have[0] - 1) if have[0] else NOW_MS
    empty_streak = 0
    while cursor_end > START_MS and empty_streak < 3:
        res = get("funding/history", {"category": "linear", "symbol": symbol, "endTime": cursor_end, "limit": 200})
        rows = res.get("list", [])
        if not rows:
            empty_streak += 1
            cursor_end -= 30 * 24 * 3600_000
            time.sleep(PACE_S)
            continue
        empty_streak = 0
        batch = [(symbol, int(r["fundingRateTimestamp"]), float(r["fundingRate"])) for r in rows]
        conn.executemany("INSERT OR IGNORE INTO funding(symbol,ts,rate) VALUES (?,?,?)", batch)
        conn.commit()
        total += len(batch)
        cursor_end = min(int(r["fundingRateTimestamp"]) for r in rows) - 1
        time.sleep(PACE_S)
    mark(symbol, "funding", "complete", total)
    return total


def backfill_oi(symbol, tf="1h"):
    have = conn.execute("SELECT MIN(ts), COUNT(*) FROM open_interest WHERE symbol=? AND interval=?", (symbol, tf)).fetchone()
    total = have[1] or 0
    if have[0] and have[0] <= START_MS + 3600_000:
        mark(symbol, f"oi_{tf}", "complete", total)
        return total
    cursor_end = (have[0] - 1) if have[0] else NOW_MS
    empty_streak = 0
    calls = 0
    while cursor_end > START_MS and empty_streak < 3 and calls < 500:
        res = get("open-interest", {"category": "linear", "symbol": symbol, "intervalTime": tf,
                                     "endTime": cursor_end, "limit": 200})
        calls += 1
        rows = res.get("list", [])
        if not rows:
            empty_streak += 1
            cursor_end -= 7 * 24 * 3600_000
            time.sleep(PACE_S)
            continue
        empty_streak = 0
        batch = [(symbol, tf, int(r["timestamp"]), float(r["openInterest"])) for r in rows]
        conn.executemany("INSERT OR IGNORE INTO open_interest(symbol,interval,ts,oi) VALUES (?,?,?,?)", batch)
        conn.commit()
        total += len(batch)
        cursor_end = min(int(r["timestamp"]) for r in rows) - 1
        time.sleep(PACE_S)
    # OI history on Bybit's public API is known to have a shallower retention
    # window than klines/funding -- "complete" here means "as far back as the
    # API will give us", documented via the `rows`/oldest-ts columns, not
    # necessarily the full 2 years.
    mark(symbol, f"oi_{tf}", "complete", total)
    return total


def main():
    t0 = time.time()
    for i, symbol in enumerate(UNIVERSE):
        for tf in ("1h", "4h", "1d"):
            n = backfill_klines(symbol, tf)
        nf = backfill_funding(symbol)
        noi = backfill_oi(symbol, "1h")
        elapsed = time.time() - t0
        print(f"[{i+1}/{len(UNIVERSE)}] {symbol:<14} funding={nf:<6} oi_1h={noi:<6} elapsed={elapsed:.0f}s", flush=True)
    print(f"DONE in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
