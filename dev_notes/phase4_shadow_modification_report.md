# Phase 4 — Shadow CoinSelector Reads watch_list (HR-1 Cross-Process)

**Date:** 2026-04-26
**Restart:** `sudo systemctl restart shadow.service` at 01:14:23 UTC (PID 47369).
**Trial window:** 01:14:23 → 01:17:30 UTC (~3 minutes).

---

## 1. Code Changes (in the Shadow project, `/home/inshadaliqbal786/shadow/`)

### 1.1 `shadow/src/utils/config.py`

Added `workers_config_path: str = ""` to `CollectorConfig` dataclass + threaded through `_build_collector` builder. Empty default → backward-compatible legacy behavior.

### 1.2 `shadow/src/collector/coin_selector.py`

**Major refactor (additive):**
- Added `_select_via_watch_list(workers_config_path)` — synchronous helper that reads `[universe] watch_list` from the workers' config.toml (Python's `tomllib`), validates each symbol against the same regex (`^[A-Z0-9]+USDT$`) the workers' UniverseSettings uses, then validates against Bybit's `instruments-info`.
- Added `_fetch_bybit_tradeable_symbols()` — paginated fetch of all tradeable USDT linear perpetuals (Bybit caps responses at 500/page; we pass `limit=1000` and follow `nextPageCursor` for safety).
- Modified `select_top_coins(count)` — when `workers_config_path` is set and produces a non-empty validated list, returns that list; falls back to legacy top-N otherwise.
- New log tags:
  - `SHADOW_WATCH_LIST | size=N source=workers_config path=... top5=...` (success — written before save_to_db)
  - `SHADOW_WATCH_LIST_NOT_FOUND | path=...` (config file missing)
  - `SHADOW_WATCH_LIST_BAD_TYPE | expected list got T` (TOML structural error)
  - `SHADOW_WATCH_LIST_BAD_ENTRY | entry=...` (malformed symbol — skipped)
  - `SHADOW_WATCH_LIST_EMPTY | no valid entries` (after validation)
  - `SHADOW_WATCH_LIST_INVALID | n=N symbols=...` (Bybit-untradeable — skipped)
  - `SHADOW_WATCH_LIST_BYBIT_UNREACHABLE` (Bybit API outage — uses candidates as-is)
  - `SHADOW_WATCH_LIST_LOAD_FAIL | path=... err=...` (hard failure → falls back to legacy top-N)
- Preserved `_save_to_db()` and `_get_cached_coins()` unchanged — both modes feed the same `tracked_coins` table; the cached fallback survives any hard failure.

**Critical fix mid-trial:** initial implementation called `get_instruments_info(category="linear")` once and got 500 items. With 538+ tradeable symbols, that paginated short — flagged 4 majors (XRPUSDT, SUIUSDT, WIFUSDT, TONUSDT) as "not tradeable" incorrectly. Fixed by passing `limit=1000` AND iterating `nextPageCursor` (defensive cap of 10 pages). Re-verified: all 50 watch_list symbols now validate.

### 1.3 `shadow/config.toml`

Added under `[collector]`:
```toml
workers_config_path = "/home/inshadaliqbal786/trading-intelligence-mcp/config.toml"
```
Plus a comment block explaining the Phase 4 source-of-truth pattern.

### 1.4 `shadow/shadow.py`

- Captured `watch_count = len(symbols)` immediately after `select_top_coins()` returns, before the orphan re-add loop.
- Added a final `SHADOW_SUBS_FINAL | watch=N orphans=M total=K` log line after the orphan logic completes — single line giving the operator the answer to "what is Shadow streaming right now?".
- Updated the orphan-re-add comment to reflect post-Phase-4 semantics (now covers operator removing a coin from watch_list while a position remains open).

**No structural changes** to shadow.py beyond log additions — the orphan re-add logic is preserved exactly per HR-2 + the brief Section 11.7.

---

## 2. Verification — Live Shadow Process

### 2.1 SHADOW_WATCH_LIST log at startup

```
2026-04-26 01:14:25.858 | INFO | collector.coins:select_top_coins:94 |
SHADOW_WATCH_LIST | size=50 source=workers_config
path=/home/inshadaliqbal786/trading-intelligence-mcp/config.toml
top5=BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT
```

Confirms Shadow's CoinSelector successfully read the workers' `[universe] watch_list` and validated all 50 entries against Bybit instruments-info. No `_INVALID` or `_BAD_ENTRY` warnings.

### 2.2 SHADOW_SUBS_FINAL log after orphan re-add

```
2026-04-26 01:14:25.865 | INFO | shadow:main:151 |
Expanded symbol list by 1 orphan(s). Total: 51

2026-04-26 01:14:25.866 | INFO | shadow:main:157 |
SHADOW_SUBS_FINAL | watch=50 orphans=1 total=51
```

50 watch_list + 1 orphan (an open-position coin not in watch_list) = 51 final subscriptions. **HR-2 enforced** — the orphan re-add path correctly handles position-coins outside the watch_list.

### 2.3 WebSocket subscription count

```
2026-04-26 01:14:28.887 | INFO | collector.ws:run:146 |
Starting WebSocket streams: 51 ticker + 51 kline topics
```

Matches `SHADOW_SUBS_FINAL` total. The WebSocket manager is subscribing to exactly 51 symbols (down from 102 pre-fix).

### 2.4 HR-2 invariant verification on fresh klines (live, ~3 min after restart)

```
recent (last 3 min) symbols streaming: 51
watch_list: 50
open positions: {'ALGOUSDT', 'TRUMPUSDT'}
recent ∩ watch:    50
recent ∩ positions: 2
HR-2 violations on fresh klines: NONE
subscribed but NOT receiving fresh klines: []
```

Every coin Shadow is currently streaming klines for is in `watch_list ∪ open_positions`. Both open-position coins (ALGOUSDT — also in watch_list, TRUMPUSDT — also in watch_list) are streaming. **HR-2 holds across the full subscription set.**

(Open-position state has changed since Phase 2/3 trials — WCTUSDT closed, ALGOUSDT opened. Normal trading activity. The orphan re-add captured TRUMPUSDT as the orphan because watch_list contains TRUMPUSDT directly — wait, looking at watch_list again, TRUMPUSDT is NOT in our 50; it's the orphan. The Phase 4 trial confirms the HR-2 mechanism works for an actual outside-watch-list coin.)

### 2.5 Workers process health (Shadow restart did not perturb workers)

```
Workers status:    Active: active (running) since 01:05:14 UTC; 12 min uptime
Memory: 538.6M / 600.0M (61.3M headroom)
Tasks: 35

Recent XRAY_TICK + STRAT_CYCLE_DONE (last 3 min):
  01:14:55  STRAT_CYCLE_DONE | coins=32 signals=11 scored=11 hints=5 el=463ms
  01:15:25  XRAY_TICK | batch=0/2 symbols=7 analyzed=7 errors=0 cached=32 el=88ms
  01:16:26  XRAY_TICK | batch=1/2 symbols=25 analyzed=25 errors=0 cached=32 el=432ms
  01:16:29  STRAT_CYCLE_DONE | coins=32 signals=11 scored=11 hints=4 el=1891ms
  01:17:15  STRAT_CYCLE_DONE | coins=32 signals=10 scored=10 hints=4 el=615ms
  01:17:26  XRAY_TICK | batch=0/2 symbols=7 analyzed=7 errors=0 cached=34 el=106ms

structure_worker errors since Shadow restart: 0
```

Workers stayed up across the Shadow restart. ShadowKlineReader's persistent connection (sister fix) handled the brief Shadow-down window via WAL semantics + busy_timeout. No errors. Tick times consistent with Phase 3.

---

## 3. HR Compliance

- **HR-1:** ✓ Shadow now reads watch_list from the workers' single source of truth (`/home/.../trading-intelligence-mcp/config.toml`). No Shadow-side duplicate.
- **HR-2:** ✓ Orphan re-add (open positions outside watch_list) preserved. Verified live: TRUMPUSDT orphan added → 51 total subscriptions. Both current open-position coins receiving fresh klines.
- **HR-3:** PARTIAL — Shadow is NOT a git repository. The Phase 4 changes are documented file-by-file in this report and are reversible via file edit. The workers-project commit (this report + plan refs) covers the workers-side artifacts.

---

## 4. Files Modified

**Shadow project (`/home/inshadaliqbal786/shadow/`):**
- `src/utils/config.py` — `CollectorConfig.workers_config_path` field + `_build_collector` wiring
- `src/collector/coin_selector.py` — major refactor (Phase 4 watch_list mode + Bybit pagination fix)
- `config.toml` — `[collector] workers_config_path = "..."` line + comment
- `shadow.py` — `SHADOW_SUBS_FINAL` log line + capture `watch_count` for the log

**Workers project (`/home/inshadaliqbal786/trading-intelligence-mcp/`):**
- `dev_notes/phase4_shadow_modification_report.md` — this file (the workers-side commit)

---

## 5. Discovered

- **D-1 (still relevant):** Shadow's daily coin refresh task is still missing from `asyncio.gather()` (shadow.py:254-263). Post-Phase-4, this is intentional — watch_list is static, no refresh needed. Phase 6 will comment out the dead `coin_refresh_interval` config key.
- **No new issues** discovered during Phase 4. Bybit pagination was a real bug in my initial implementation; caught + fixed before commit. Lesson re-confirmed: validate against the actual API behavior, not the apparent contract.

---

## 6. Verification Gate (Phase 4 → Phase 5)

| Check | Status |
|---|---|
| `SHADOW_WATCH_LIST | size=50 source=workers_config path=...` log at startup | PASS |
| `SHADOW_SUBS_FINAL | watch=50 orphans=1 total=51` log after orphan re-add | PASS |
| WebSocket subscription count = 50-55 | PASS (51 ticker + 51 kline) |
| Fresh klines (last 3 min) all in `watch_list ∪ open_positions` | PASS (HR-2 violations: NONE) |
| Open-position orphan (TRUMPUSDT outside watch_list) included | PASS |
| All subscribed coins receiving fresh klines | PASS (no missing) |
| Workers process unaffected by Shadow restart | PASS (ShadowKlineReader persistent connection handled it) |
| structure_worker errors since Shadow restart | 0 |
| Bybit pagination handles 538+ symbols correctly | PASS (after pagination fix) |
| All 50 watch_list symbols validated tradeable | PASS |

**Verification gate PASSED. Proceeding to Phase 5 (verify the 6 unchanged workers fall into place).**
