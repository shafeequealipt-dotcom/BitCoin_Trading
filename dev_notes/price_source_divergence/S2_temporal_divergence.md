# S2 — Temporal Divergence (repeated capture)

## Pre-condition status

Pre-condition NOT MET — no open positions at capture (see S1).

## Capture script (to run on next live position)

Save as `dev_notes/price_source_divergence/_s2_capture.sh`, run with `bash _s2_capture.sh > s2_run_$(date +%s).txt` for 10 minutes. The script captures every source at 30 s intervals.

```bash
#!/usr/bin/env bash
# Captures the 4 live price feeds + Shadow PnL + ticker_cache state every 30s.
# Run from /home/inshadaliqbal786 .
SYM="${1:?usage: ./_s2_capture.sh SYMBOL}"
DURATION=600  # 10 min
INTERVAL=30
END=$(( $(date +%s) + DURATION ))

while [ $(date +%s) -lt $END ]; do
  T=$(date -u +%FT%TZ)
  echo "=== $T ==="
  echo "-- Shadow API --"
  curl -s http://127.0.0.1:9090/api/positions | python3 -c "
import sys,json
data=json.load(sys.stdin)
for p in data.get('positions',[]):
    if p['symbol']=='$SYM':
        print(f\"shadow current=${{p['current_price']:.6f}} entry={p['entry_price']:.6f} pnl_usd={p['unrealized_pnl_usd']:+.4f}\")
"
  echo "-- Shadow ticker --"
  curl -s "http://127.0.0.1:9090/api/ticker/$SYM"
  echo
  echo "-- main ticker_cache --"
  sqlite3 trading-intelligence-mcp/data/trading.db \
    "SELECT last_price, updated_at FROM ticker_cache WHERE symbol='$SYM';"
  sleep $INTERVAL
done
```

## Expected analysis (predicted from architecture)

After collecting 20 captures over 10 min, classify:

- **Persistent divergence (constant offset):** points to a fixed transformation difference (e.g. slippage applied one side only, or a constant fee component included on one side).
- **Fluctuating divergence (changing each capture):** points to one or both feeds having staleness — the Δ varies with how fast the market moved between the two cache writes.
- **Step-function divergence (jumps at fixed cadence):** points to one feed being on a polled/snapshotted cadence (e.g. 60 s `ticker_collector` snapshot) while the other is push-driven.

Mapping to known mechanisms:

| Pattern | Likely cause | File:line |
|---|---|---|
| Constant Δ ≈ slippage_pct × notional | main records pre-slippage entry | `order_engine.py:191-194` (Shadow), `order_service.py` (main) |
| Fluctuating Δ correlated with price move | one feed stale | `transformer.py:701-706` PRICE_STALE gate |
| Step every ~60 s on `ticker_cache` row | only REST hits update `ticker_cache`, with WS write silently failing | `price_worker.py:215-220` (the `except RuntimeError: pass`) |
| Δ jumps to 0 every time `/positions` is called | Transformer overwrite when divergence ≤ 0.5 % aligns prices, then drifts again | `transformer.py:797` |

## Without live data: closed-trade temporal proxy

The 8 closed trades in `T1_closed_trade_forensics.md` cover the same symbols traded across 1 hour (05:32 → 06:29). The main DB / Shadow DB `entry_price` deltas are constant and equal to the per-trade slippage. The realized `pnl_usd` deltas are **not** constant — they reflect divergent notional handling. This is consistent with the "fluctuating divergence" prediction.
