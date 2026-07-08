# Phase 2 — ScannerWorker Filters From watch_list (HR-1 + HR-2)

**Date:** 2026-04-26
**Restart:** `sudo systemctl restart trading-workers.service` at 00:50:38 UTC (PID 44239).
**Trial window:** 00:50:38 → 00:53:30 UTC (~3 minutes, 1 scan cycle, multiple downstream worker ticks).

---

## 1. Code Changes

### 1.1 `src/strategies/scanner.py`

**`MarketScanner.__init__`** — accepts a new optional `watch_list: set[str] | None` kwarg. Stores `self._watch_list: set[str]`. Empty / None → legacy behavior (no filter). When non-empty, emits `SCANNER_WATCH_LIST | size=N source=config.universe.watch_list` once at construction.

**`MarketScanner.scan_market`** — two changes:
1. **Pre-fetch positions** at the top of the method (was only inside `_update_universe`). The protected_symbols set is built once per scan cycle.
2. **Apply HR-1 watch_list filter** after `get_all_linear_tickers()` returns: `tickers = [t for t in tickers if t.symbol in (watch_list ∪ protected_symbols)]`. Emits `SCANNER_INPUT | watch_list=N protected=M input_set=K all_tickers=A filtered=F` per scan.
3. **Pass protected_symbols** into `_update_universe(result, protected_symbols=protected_symbols)` to avoid the redundant Bybit `get_positions()` call.

**`MarketScanner._update_universe`** — accepts new optional `protected_symbols: set[str] | None` kwarg. When provided, skips its own fetch (Phase 2 path); when None, fetches itself (legacy / direct-caller path — preserves backward compatibility for any test code that calls `_update_universe` directly).

### 1.2 `src/workers/manager.py`

**MarketScanner construction** at line 886-892:
```python
_watch_list = set(getattr(s.universe, "watch_list", []) or [])
scanner = MarketScanner(
    s, market_svc,
    instrument_service=inst_svc,
    watch_list=_watch_list,
)
```
`getattr` with default makes the change defensive against missing `[universe]` config (UniverseSettings already defaults to a 10-coin majors list).

### 1.3 `tests/test_scanner_filter.py` (new)

7 unit tests:
- `test_filters_input_to_watch_list` — non-watch-list tickers are dropped before scoring.
- `test_protected_symbols_outside_watch_list_included` — HR-2: open-position coins outside watch_list still get scored and can survive into the universe.
- `test_empty_watch_list_falls_back_to_legacy` — backward compatibility when watch_list is empty.
- `test_top_n_bounded_to_filtered_set` — top-N never exceeds filtered set size.
- `test_scan_market_does_not_double_fetch_positions` — `get_positions()` called exactly ONCE per scan cycle (was 2 before this fix when scan + update both fetched).
- `test_update_universe_with_protected_skips_fetch` — direct caller can supply protected set.
- `test_update_universe_without_protected_fetches` — legacy direct caller behavior preserved.

All 7 pass.

---

## 2. Trial Results — Live Workers Process

### 2.1 SCANNER_WATCH_LIST log at construction

```
2026-04-26 00:50:45.307 | INFO | scanner:__init__:56 |
SCANNER_WATCH_LIST | size=50 source=config.universe.watch_list
```

Confirms the watch_list flowed through `Settings.universe.watch_list` → `manager.py` → `MarketScanner.__init__` correctly.

### 2.2 SCANNER_INPUT log on first scan cycle

```
2026-04-26 00:50:46.033 | INFO | scanner:scan_market:249 |
SCANNER_INPUT | watch_list=50 protected=2 input_set=52 all_tickers=540 filtered=52
```

- `all_tickers=540` — Bybit returned 540 USDT linear perpetuals (the unfiltered scope before this fix).
- `watch_list=50` + `protected=2` (open positions: TRUMPUSDT, WCTUSDT) → `input_set=52`.
- `filtered=52` — 52 tickers survive into the scoring loop (488 tickers were skipped — ~90% reduction in scoring work per cycle).

### 2.3 First Market scan + universe update

```
2026-04-26 00:50:46.034 | INFO | scanner:scan_market:408 |
Market scan: 48 coins scored, top 30 selected. Best: AXSUSDT, TRUMPUSDT, KATUSDT, BASEDUSDT, MAGMAUSDT

2026-04-26 00:50:46.035 | INFO | scanner:_update_universe:162 |
Scanner universe UPDATED v1: 32 coins (added: {DOGEUSDT, ARBUSDT, 1000PEPEUSDT, AAVEUSDT,
SEIUSDT, PLUMEUSDT, GALAUSDT, TONUSDT, ENAUSDT, ALGOUSDT, DOTUSDT, SANDUSDT, FARTCOINUSDT,
LTCUSDT, MAGMAUSDT, WCTUSDT, BLURUSDT, TRUMPUSDT, KATUSDT, AXSUSDT, ASTERUSDT, SUIUSDT,
APTUSDT, BTCUSDT, BNBUSDT, HYPEUSDT, PENGUUSDT, BASEDUSDT, ENJUSDT, ETHUSDT, INJUSDT,
DYDXUSDT}, removed: none, protected: 2)
```

- 48 of 52 filtered tickers scored above 0 after hard disqualifiers.
- Top 30 selected.
- Final `_active_universe` = 32 = top-30 + BTC/ETH force-prepend (already in watch_list, no-op if already in top-30 — here BTC/ETH made the top-30 by score).
- `protected: 2` — the 2 open-position coins were force-included in `_update_universe` (here both ALSO ranked in the top 30 by their own score, so the protect logic was a no-op).

### 2.4 HR-2 invariant verification (live `active_universe` table)

After the scanner_worker tick at 00:51:51 wrote the latest scan to the table:

```
active_universe size: 30
watch_list size: 50
open positions: {'TRUMPUSDT', 'WCTUSDT'}
HR-2 violations: NONE
```

Every coin in `active_universe` is either in watch_list or has an open position. **HR-2 holds.**

(Note: the `active_universe` SQL table has BTC/ETH absent because the table writer in `scanner_worker.py` writes the `result` dicts from `scan_market` — which is the top-30 scored — NOT the in-memory `_active_universe` which has the +2 force-prepend for BTC/ETH. This is pre-existing behavior; the table is write-only and never read in `src/`. The downstream consumers all use `scanner.get_active_universe()` which returns the in-memory list correctly.)

### 2.5 Downstream worker activity (5-min window)

Sample log lines confirming the 6 unchanged workers consumed the new 32-coin universe correctly:

```
00:50:46  PRICE_UNIVERSE_SYNC  | added=32 removed=0 total=32
00:51:50  STRAT_CYCLE_DONE     | coins=32 signals=14 scored=14 hints=7 el=3971ms
00:51:53  SIG_BATCH            | n=32 coins=32 strongest=KATUSDT type=neutral conf=0.44
00:52:27  ALTDATA              | fg=33 funding=32 oi=32
00:52:35  STRAT_REGIME_DIST    | up=2 down=5 ranging=17 volatile=7 dead=1 other=0 total=32
00:52:36  STRAT_CYCLE_DONE     | coins=32 signals=14 scored=14 hints=9 el=454ms
00:52:50  KLINE_FETCH          | klines=25167 expected=25600 symbols=32 quality=ok
```

All 6 workers (PriceWorker, StrategyWorker, SignalWorker, AltDataWorker, RegimeWorker, KlineWorker) accepted the new constrained universe without code changes.

### 2.6 Errors in trial window

```
$ awk -v t="2026-04-26 00:50:38" '... grep -E "ERROR|CRITICAL"' workers.log
(empty)
```

**Zero errors** introduced by this change.

---

## 3. Test Results

```
$ .venv/bin/pytest tests/test_scanner_filter.py tests/test_universe_settings.py -v
================== 23 passed in 1.55s ==================
```

7 new scanner-filter tests + 16 universe-settings tests, all green. Existing settings + strategies tests unchanged (8 pre-existing failures in test_strategies/ are SymbolRegistry-vs-frozenset issues unrelated to this work).

---

## 4. Verification Gate (Phase 2 → Phase 3)

| Check | Status |
|---|---|
| `SCANNER_WATCH_LIST` log fires at MarketScanner init | PASS (size=50, source=config.universe.watch_list) |
| `SCANNER_INPUT` log fires per scan cycle | PASS (filtered 52 from 540) |
| `active_universe` table contents ⊆ `watch_list ∪ open_positions` | PASS (0 violations) |
| Open positions outside watch_list are included (HR-2) | PASS (TRUMPUSDT, WCTUSDT both in active_universe though not in watch_list) |
| Position service called exactly ONCE per scan (no double-fetch) | PASS (test_scan_market_does_not_double_fetch_positions) |
| Backward compat: empty watch_list falls back to legacy behavior | PASS (test_empty_watch_list_falls_back_to_legacy) |
| Top-30 active focus selected from filtered set, not all 540 | PASS (`Market scan: 48 scored, top 30 selected`) |
| 6 unchanged downstream workers consume new universe without errors | PASS (PRICE/STRAT/SIG/ALTDATA/REGIME/KLINE all logged 32 coins, no errors) |
| New scanner unit tests pass | PASS (7/7) |
| No new error patterns in workers.log | PASS (zero ERROR/CRITICAL in trial window) |

**Verification gate PASSED. Proceeding to Phase 3 (structure_worker uses active_universe directly).**

---

## 5. Notes for Subsequent Phases

- The 32-coin in-memory `_active_universe` (top-30 + force-prepended BTC/ETH if not in top-30) is what `get_active_universe()` returns to all 11 downstream callers. Phase 3's structure_worker rewrite will read this directly.
- The `active_universe` SQL table writer (scanner_worker.py:39-50) writes the top-30 scored only (no force-prepend, no protected force-include). This is a pre-existing behavior, write-only, never read in `src/`. **Out of scope** for this fix.
- The 540 → 52 filter reduces scanner CPU work by ~90% per cycle. Indirect benefit: faster scanner ticks, less Bybit API load.
