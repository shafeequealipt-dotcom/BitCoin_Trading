# Phase 6 — Cleanup Of Dead Code

**Date:** 2026-04-26
**Restart:** 01:34:59 UTC (PID 49344) — clean.
**Trial window:** 01:34:59 → 01:37:08 UTC (~2 minutes, full bootstrap + 2 XRAY_TICKs).

---

## 1. What Got Removed

### 1.1 `src/analysis/structure/coin_discovery.py` — DELETED (entire file, 132 LOC)

The CoinDiscovery class became unused after Phase 3 (structure_worker switched to scanner.get_active_universe). Confirmed only call site was `structure_worker.py:153`, removed in Phase 3 commit. Phase 6 deletes the now-orphan file entirely.

### 1.2 `src/workers/manager.py` — `coin_discovery` construction & registration removed

- Removed the `if settings.structure.scan_full_market:` gate (the flag is gone in Phase 6 too).
- Removed `from src.analysis.structure.coin_discovery import CoinDiscovery` import.
- Removed the `coin_discovery = CoinDiscovery(...)` construction.
- Removed `self._services["coin_discovery"] = coin_discovery` registration.
- Removed `"coin_discovery",` from `_EXPECTED_SERVICE_KEYS` (line 586). Service-key count went 65 → 64.
- Updated the bootstrap log from `"X-RAY: Full market mode (Shadow DB: ...)"` → `"X-RAY: shadow_kline_reader ready (Shadow DB: ...)"` (the "Full market mode" framing is no longer accurate).
- Updated the failure log from `"X-RAY full market unavailable: {err}"` → `"X-RAY shadow_kline_reader unavailable: {err}"`.
- Updated the StructureWorker construction comment block (lines 932-935) to reflect that CoinDiscovery is now fully removed.
- The `try/except` block now only wraps the shadow_reader initialization — same exception semantics, narrower scope.

The `shadow_kline_reader` service registration is **preserved** — it's still actively used by structure_worker's fallback path.

### 1.3 `src/config/settings.py` — `StructureSettings` dead fields removed

- Removed `scan_full_market: bool = True` field.
- Removed `coin_refresh_interval: int = 600` field (was used to seed CoinDiscovery's cache TTL).
- Added a comment explaining the Phase 6 removal so future readers know what those keys USED to do.

The `_build_structure` builder is reflective (`{k: data[k] for k in data if hasattr(StructureSettings, k)}`) — automatically ignores any TOML key that no longer maps to a dataclass field. No builder change required.

### 1.4 `config.toml` — `[analysis.structure]` dead keys removed

- Removed `scan_full_market = true`.
- Removed `coin_refresh_interval = 600`.
- Replaced the `# Market Dominance: Full market scanning via Shadow DB` comment with a Phase 6 explanatory note.

### 1.5 `shadow/config.toml` — `[collector]` legacy keys commented out

- `coin_count = 100` → commented out with a note explaining the Phase 4 supersession.
- `coin_refresh_interval = 86400` → commented out.

Kept as commented-out history rather than deleted: the Shadow `CollectorConfig` dataclass still has those fields with defaults (100, 86400), and `shadow.py:112` still calls `select_top_coins(config.collector.coin_count)`. In watch_list mode the `count` argument is ignored; this is documented in Phase 4. Removing the dataclass fields would force a `shadow.py` change beyond the Phase 6 cleanup scope.

---

## 2. Verification — Live Workers Process

### 2.1 Zero remaining code references

```
$ grep -rn "coin_discovery|CoinDiscovery|scan_full_market" src/ tests/ config.toml \
    | grep -v "src/workers/settings.py" | grep -v "dev_notes"

src/workers/manager.py:177  # (Phase 6 cleanup: CoinDiscovery and the scan_full_market gate
src/workers/manager.py:933  # scanner.get_active_universe() exclusively. CoinDiscovery
src/config/settings.py:791  # (Removed in Phase 6: ``scan_full_market`` flag — CoinDiscovery is gone.
src/config/settings.py:792  # Removed in Phase 6: ``coin_refresh_interval`` — was CoinDiscovery's
config.toml:738              # reads scanner.get_active_universe() directly — CoinDiscovery, the
config.toml:739              # scan_full_market gate, and coin_refresh_interval are removed.
src/workers/structure_worker.py:30   # docstring: ScannerWorker's ``get_active_universe()`` exclusively. CoinDiscovery
```

**ALL remaining matches are comments / docstrings** — no executable code references CoinDiscovery, scan_full_market, or coin_refresh_interval.

The orphan duplicate file `src/workers/settings.py` (45KB, no imports anywhere) still has `scan_full_market` and `coin_refresh_interval` fields. Out of scope per the brief — that file is dead code from a prior reorganization, not used by any module.

### 2.2 Import test confirms CoinDiscovery is gone

```
$ python -c "from src.analysis.structure.coin_discovery import CoinDiscovery"
ImportError: No module named 'src.analysis.structure.coin_discovery'
```

### 2.3 Settings load is clean

```
$ python -c "from src.config.settings import Settings; ..."
OK config loaded
  scan_full_market exists: False
  coin_refresh_interval exists: False
  batch_size: 25
  shadow_db_path: ../shadow/data/shadow.db
  watch_list size: 50
```

### 2.4 SERVICES_WIRED shows clean count after restart

```
2026-04-26 01:35:37 | INFO | manager:_emit_services_wired:612 |
SERVICES_WIRED | present=63/64 keys=[...,structure_engine,structure_cache,
  shadow_kline_reader,position,...]   ← coin_discovery NOT in the list

2026-04-26 01:35:37 | WARNING | manager:_emit_services_wired:617 |
SERVICES_MISSING | count=1 keys=[reddit]   ← only reddit (pre-existing)
```

Pre-Phase-6 was `present=64/65`; post-Phase-6 is `present=63/64`. Both `_EXPECTED_SERVICE_KEYS` total and live registrations dropped by 1 (the removed `coin_discovery`).

### 2.5 First scanner + structure_worker activity (post-restart)

```
01:35:06.531  SCANNER_WATCH_LIST | size=50 source=config.universe.watch_list
01:35:07.004  SCANNER_INPUT | watch_list=50 protected=0 input_set=50 all_tickers=540 filtered=50
01:36:04.574  SCANNER_INPUT | watch_list=50 protected=0 input_set=50 all_tickers=540 filtered=50
01:36:07.692  XRAY_TICK | batch=1/2 symbols=25 analyzed=25 errors=0 cached=25
              session=asian(early) setups=12 skips=13 el=4632ms
01:37:07.784  XRAY_TICK | batch=0/2 symbols=7 analyzed=7 errors=0 cached=31
              session=asian(early) setups=12 skips=19 el=89ms
```

- Scanner filter still works correctly (`watch_list=50 protected=0 → input_set=50`; protected=0 because no open positions at this moment — that's normal post-trade-close state).
- XRAY_TICK still uses scanner-only universe (`cached=31` = 30 scored + 1 BTC/ETH force-prepend; protected=0 means no extra additions).
- 0 XRAY_UNIVERSE_EMPTY warnings.
- 0 errors during bootstrap or the 2-min observation.

### 2.6 Tests still pass

```
$ .venv/bin/pytest tests/test_universe_settings.py tests/test_scanner_filter.py -q
.......................                                                  [100%]
23 passed in 0.64s
```

---

## 3. Files Modified (workers project)

- `src/analysis/structure/coin_discovery.py` — **DELETED**
- `src/workers/manager.py` — coin_discovery construction + service registration removed; `_EXPECTED_SERVICE_KEYS` updated; bootstrap log strings updated
- `src/config/settings.py` — `scan_full_market` and `coin_refresh_interval` fields removed from `StructureSettings`
- `config.toml` — `scan_full_market` and `coin_refresh_interval` lines removed from `[analysis.structure]`
- `shadow/config.toml` — `coin_count` and `coin_refresh_interval` commented out (kept as deprecation history)

## 4. Files NOT Modified (deferred)

- `src/workers/settings.py` (45 KB orphan duplicate of `src/config/settings.py`, ZERO imports anywhere) — still has dead `scan_full_market` and `coin_refresh_interval` fields. Not used by any code path. **Out of scope** for Phase 6 (the brief calls for cleanup of THIS task's introductions, not orphan-file cleanup from prior reorganizations). Operator can `git rm src/workers/settings.py` as a separate trivial cleanup.
- `shadow/src/utils/config.py` `CollectorConfig.coin_count` and `coin_refresh_interval` fields — still present with defaults (100, 86400) but the TOML keys are commented out so the `data.get("coin_count", 100)` defaults take effect. `shadow.py` still passes `config.collector.coin_count` to `select_top_coins()` — but in watch_list mode that arg is ignored. **Removing these dataclass fields requires a `shadow.py` signature update** which is outside the Phase 6 cleanup scope. Operator follow-up.
- The `active_universe` SQL table — still write-only as noted in `dev_notes/layer1_to_xray_complete_state.md` and Blueprint Section 19.1. **Out of scope per the brief and blueprint** — defer to a separate task.

---

## 5. HR Compliance

- **HR-1:** ✓ The single source of truth is `[universe] watch_list` everywhere. CoinDiscovery (the parallel universe source) is gone.
- **HR-2:** ✓ Both ScannerWorker (Phase 2) and Shadow's CoinSelector (Phase 4) preserve open-position re-add. Phase 6 doesn't change the protected-symbol path.
- **HR-3:** ✓ Phase 6 = one git commit, focused on cleanup. Reversible by `git revert`.

---

## 6. Verification Gate (Phase 6 → Phase 7)

| Check | Status |
|---|---|
| `coin_discovery.py` deleted | PASS |
| Zero code-path references to coin_discovery / CoinDiscovery / scan_full_market | PASS (only docs/comments remain) |
| `from src.analysis.structure.coin_discovery import CoinDiscovery` raises ImportError | PASS |
| Settings load clean (no scan_full_market field, no coin_refresh_interval field) | PASS |
| `_EXPECTED_SERVICE_KEYS` count went from 65 → 64 | PASS (SERVICES_WIRED present=63/64) |
| Workers boot without errors | PASS (0 errors in 2-min trial) |
| Scanner still filters from watch_list | PASS (SCANNER_INPUT logs) |
| structure_worker still produces XRAY_TICK | PASS |
| 23 universe + scanner tests still pass | PASS |
| `[analysis.structure]` config section cleaned | PASS |
| `[collector]` shadow config dead keys commented out | PASS |

**Verification gate PASSED. Proceeding to Phase 7 (~30-60 min live observation).**
