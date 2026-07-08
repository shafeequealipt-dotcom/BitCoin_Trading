# E1 — Scanner Worker Forensic Data

**Capture timestamp (UTC):** 2026-04-27 23:03:16
**Source code file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/scanner_worker.py` (1090 lines)
**Primary live log:** `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
**Rotated log used for ranking range / 7-cycle window:** `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.2026-04-27_01-31-00_169356.log`
**DB snapshot:** `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db`
**Config:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml`

---

## E.1.1 — File overview (every method, one-liner)

| Line | Symbol | Purpose |
|------|--------|---------|
| L40 | `class ScannerWorker(SweetSpotWorker)` | Layer 1D cycle trigger — qualifies + ranks + builds packages. |
| L57 | class attr `worker_tier = WorkerTier.LAYER1D` | Sub-layer assignment used by LayerManager. |
| L59 | class attr `cycle_gated = True` | Skip tick when `LayerManager.is_cycle_active()` is False. |
| L61–L76 | `__init__(settings, db, scanner, services=None)` | Stores DI handles; sweet-spot from `settings.workers.sweet_spots.scanner_worker` ("4:00"). |
| L80–L95 | `_get_setup_score(coin)` | Defensive accessor on `services["structure_worker"].get_setup_score` (0–100). |
| L97–L108 | `_get_strategy_score(coin)` | Defensive accessor on `services["strategy_worker"].get_score` (~0–100). |
| L110–L124 | `_get_signal_confidence(coin)` | Defensive accessor on `services["signal_worker"].get_signal(coin).confidence`. |
| L126–L152 | `_get_regime_alignment(coin)` | Returns -1..+1: trending_up/trending_down→+1, volatile→+0.5, ranging→0, dead/unknown/None→-1. |
| L154–L170 | `_get_funding_strength(coin)` | Returns `abs(funding_rate)` from altdata_worker. |
| L174–L215 | `_compute_opportunity_score(coin)` | Composite weighted score (returns `(score, breakdown)`). |
| L217–L234 | `_open_position_symbols()` async | Returns set of symbols with currently open positions (HR-3 force-include). |
| L238–L252 | `_regime_aligns(regime, direction)` static | True if direction long matches `trending_up`/`ranging`, short matches `trending_down`/`ranging`. |
| L254–L312 | `_check_blockers(symbol, structure, consensus, *, recent_loss_set=None)` | Returns list of blocker labels (funding-against-direction, manipulation_likely_session, recent_loss). |
| L316–L499 | `_build_package(symbol, score, record, forced)` | Builds CoinPackage from caches: structure, strategies, signals, alt_data, price, position. |
| L501–L605 | `_qualifies(symbol, *, recent_loss_set=None)` | The 5-criterion gate. Returns `(bool, record)`. |
| L609–L1090 | `tick()` async | Main scanner cycle: prefetch losers, qualify all 50, score survivors + forced, rank, take top max_selection, BTC/ETH force-include, build packages, validate, write `_coin_packages` cache + active_universe DB rows + `MarketScanner._active_universe`. |

### Qualification + ranking + package-build flow (line ranges)

| Phase | Lines | Description |
|------|------|------|
| Tick start / cycle_id allocation | L634–L641 | `cycle_tracker.start_cycle("layer1d")`. |
| Watch list read | L644–L649 | `self.settings.universe.watch_list` (50 coins; warn if empty). |
| Force-include open positions | L651 | `protected = await self._open_position_symbols()`. |
| Recent loser prefetch | L658–L667 | `recent_loss_symbols(self.db, hours=cfg_q.recent_failure_blocker_hours)`. |
| Qualification loop | L693–L740 | Runs `_qualifies` per coin; tallies `agg` buckets; appends to `qualified_records`. |
| `SCANNER_FILTER_AGGREGATE` log | L746–L758 | One INFO line per cycle with all bucket totals. |
| Off-watch-list open-position injection | L762–L773 | Score force-includes for any open-pos symbol outside watch_list. |
| Ranking | L776 | `qualified_records.sort(key=lambda r: r[1], reverse=True)`. |
| Selection | L778–L787 | Take top `max_selection` (15); if fewer than `min_selection` (0), output what we have. |
| BTC/ETH reference-pair inject | L805–L819 | Append BTCUSDT, ETHUSDT with score 0.0 if missing (HR-2). |
| Package build loop | L825–L883 | `_build_package()` per coin → `validate_package()` → drop on FAIL, keep on OK/WARN. |
| Cache write | L884–L897 | `lm._coin_packages = packages`; `record_write("packages")`. |
| Validation summary log | L905–L909 | `PACKAGE_VALIDATE_SUMMARY` rollup. |
| `CYCLE_FRESHNESS` log | L916–L952 | p50/p95 ages for klines, xray, packages. |
| Active-universe DB write | L992–L1017 | `DELETE`; `INSERT OR REPLACE` executemany. |
| In-memory broadcast | L1024–L1037 | `scanner.set_active_universe(new_symbols)` + subscriber callbacks. |
| Per-coin DEBUG | L1041–L1049 | `SCANNER_SELECTED` (DEBUG, currently disabled — 0 entries in logs). |
| `SCANNER_SELECT` summary | L1060–L1064 | `qualified=N selected=M forced=K watch_list=L`. |
| `SCANNER_TICK_SUMMARY` | L1070–L1076 | Legacy summary (`mean_score`, `top=`, drift). |
| `cycle_tracker.end_cycle` | L1079–L1090 | Records qualified/selected/packages and ends the cycle. |

---

## E.1.2 — `_qualifies()` — FULL METHOD VERBATIM

Source: `src/workers/scanner_worker.py` lines 501–605.

```python
    def _qualifies(
        self,
        symbol: str,
        *,
        recent_loss_set: set[str] | None = None,
    ) -> tuple[bool, dict]:
        """Layer 1 restructure Phase 5 — apply 5-criterion qualitative checklist.

        Order matters — the implementation short-circuits at the first
        failed check so that ``record["reasons_failed"]`` shows the
        first failing criterion (the most useful debugging hint).

        Args:
            symbol: Watch_list coin to evaluate.
            recent_loss_set: Optional pre-computed set of symbols that
                closed at a loss within ``cfg.recent_failure_blocker_hours``.
                Forwarded to ``_check_blockers`` so the recent-loss
                blocker is one set membership lookup, not a per-coin DB
                query. ``tick`` populates this once per cycle.

        Returns:
            ``(qualified, record)`` where record has keys
            ``reasons_passed``, ``reasons_failed``, and ``blockers``.
        """
        cfg = self.settings.scanner.qualitative
        record: dict = {
            "reasons_passed": [],
            "reasons_failed": [],
            "blockers": [],
        }

        # Criterion 1 — XRAY setup type identified.
        sw = self.services.get("structure_worker")
        structure = None
        try:
            cache = getattr(sw, "_cache", None) if sw else None
            structure = cache.get(symbol) if cache and hasattr(cache, "get") else None
        except Exception:
            structure = None
        if structure is None:
            record["reasons_failed"].append("no_xray_analysis")
            return False, record
        setup_type = getattr(structure, "setup_type", None)
        if setup_type is None or getattr(setup_type, "value", "none") == "none":
            record["reasons_failed"].append("no_xray_setup_type")
            return False, record
        record["reasons_passed"].append(f"xray_setup={setup_type.value}")

        # Criterion 2 — ensemble consensus.
        lm = self.services.get("layer_manager")
        consensus = None
        if lm and hasattr(lm, "get_strategy_consensus"):
            consensus = lm.get_strategy_consensus(symbol)
        accept = {"STRONG"} if cfg.min_consensus == "STRONG" else {"STRONG", "GOOD"}
        if consensus is None or consensus.get("consensus") not in accept:
            label = consensus.get("consensus") if consensus else "NONE"
            record["reasons_failed"].append(f"consensus={label}")
            return False, record
        record["reasons_passed"].append(f"consensus={consensus['consensus']}")

        # Criterion 3 — regime alignment.
        if cfg.require_regime_alignment:
            rw = self.services.get("regime_worker")
            regime_label = ""
            if rw and hasattr(rw, "get_regime"):
                try:
                    state = rw.get_regime(symbol)
                    if state is not None:
                        regime_label = (
                            state.regime.value
                            if hasattr(state.regime, "value")
                            else str(state.regime)
                        )
                except Exception:
                    regime_label = ""
            direction = consensus.get("direction", "")
            if not self._regime_aligns(regime_label, direction):
                record["reasons_failed"].append(
                    f"regime={regime_label or 'unknown'}_vs_{direction}"
                )
                return False, record
            record["reasons_passed"].append(f"regime={regime_label}_aligns_{direction}")

        # Criterion 4 — RR ratio.
        rr = 0.0
        try:
            sp = getattr(structure, "structural_placement", None)
            rr = float(getattr(sp, "rr_ratio", 0.0) or 0.0) if sp else 0.0
        except Exception:
            rr = 0.0
        if rr < cfg.min_rr_ratio:
            record["reasons_failed"].append(f"rr={rr:.2f}_below_{cfg.min_rr_ratio}")
            return False, record
        record["reasons_passed"].append(f"rr={rr:.2f}")

        # Criterion 5 — no blockers.
        blockers = self._check_blockers(
            symbol, structure, consensus, recent_loss_set=recent_loss_set,
        )
        if blockers:
            record["blockers"] = blockers
            record["reasons_failed"].append(f"blockers={','.join(blockers)}")
            return False, record

        return True, record
```

### Per-criterion documentation

#### Criterion 1 — XRAY setup type identified  (L532–L547)

- **Source cache:** `services["structure_worker"]._cache` (a `StructureCache` instance from `src/analysis/structure/structure_cache.py`).
- **Lookup:** `cache.get(symbol)` returns `StructuralAnalysis | None`.
- **Pass condition (must satisfy BOTH):**
  1. `structure is not None` (cache hit AND not expired by TTL).
  2. `structure.setup_type is not None AND structure.setup_type.value != "none"`.
- **What counts as "has setup type":** `setup_type` is the `SetupType` enum (`src/analysis/structure/models/structure_types.py`). Live observed values from `XRAY_CLASSIFY` log entries: `bearish_fvg_ob`. Any `SetupType.value` other than `"none"` qualifies. `XRAY_NONE_REASON` log lines (e.g., `2026-04-27 22:20:45.024 sym=CRVUSDT closest_type=BEARISH_FVG_OB missed_by='no_short_downtrend_align(...)'`) indicate the analyzer wrote `setup_type.value == "none"` to the cache.
- **First-failure short-circuit reasons:**
  - `"no_xray_analysis"` — cache.get returned None (entry missing or expired). Live counter: `fail_no_xray=25` per cycle (see E.1.6).
  - `"no_xray_setup_type"` — entry exists but value == "none". Live counter: `fail_setup_none=6..10` per cycle.

#### Criterion 2 — Strategy ensemble consensus  (L550–L559)

- **Source cache:** `services["layer_manager"].get_strategy_consensus(symbol)` (defined `src/core/layer_manager.py:1388`), which reads `self._strategy_consensus` (dict written by StrategyWorker L3).
- **Returned dict keys:** `{"consensus", "consensus_score", "vote_count", "direction", "last_updated"}`.
- **Acceptance set:** `accept = {"STRONG"} if cfg.min_consensus == "STRONG" else {"STRONG", "GOOD"}`.
  - With config `min_consensus = "GOOD"` (config.toml L401): `accept = {"STRONG", "GOOD"}`.
  - `WEAK`, `LEAN`, `CONFLICT`, `NONE`, missing all FAIL.
- **Pass condition:** `consensus is not None AND consensus.get("consensus") in accept`.
- **Live distribution (cycle c-2026-04-27-22:20, STRAT_L3_DONE @ 22:21:37.784):**
  `consensus_dist=[STRONG:4, WEAK:4, GOOD:3, LEAN:1]` — total 12; only STRONG(4) + GOOD(3) = **7 coins** could pass criterion 2 ; the other ~38 of the watch list either had no consensus entry OR had `WEAK`/`LEAN`/`NONE`.

#### Criterion 3 — Regime alignment  (L562–L582)

- Gated by `cfg.require_regime_alignment` (config.toml L402: `true`).
- **Source:** `services["regime_worker"].get_regime(symbol)` returns `RegimeState | None`; `state.regime.value` is the label.
- **Direction:** `consensus.get("direction", "")` from criterion 2's consensus dict.
- **`_regime_aligns(regime, direction)` (L238–L252) match table:**

| Regime label substring | direction="long" | direction="short" |
|---|---|---|
| `"trending_up"` | True | False |
| `"trending_down"` | False | True |
| `"ranging"` / `"rang"` | True | True |
| `"volatile"` | False | False |
| anything else (incl. `"dead"`, `""`) | False | False |

- Match is by substring (`in regime_name`).  Empty `direction` (consensus dict missing `"direction"`) → both branches return False.
- **Live distribution (STRAT_REGIME_DIST @ 22:21:30.014):** `up=0 down=20 ranging=23 volatile=6 dead=0 other=0 total=49 global=ranging`.

#### Criterion 4 — RR ratio  (L584–L594)

- **Source:** `structure.structural_placement.rr_ratio` (already loaded in criterion 1).
- **Threshold:** `cfg.min_rr_ratio` from config.toml L400: `min_rr_ratio = 2.0`.
- **Pass condition:** `rr >= 2.0`. Reads via `getattr(sp, "rr_ratio", 0.0) or 0.0` so missing/None → 0.0 → fail.
- **Source of threshold:** `[scanner.qualitative] min_rr_ratio = 2.0` (config.toml L400). Mirrored at config.toml L907 (`min_rr_ratio = 2.0` in another section).

#### Criterion 5 — Blockers  (L596–L603, calls `_check_blockers` L254–L312)

Blockers evaluated (in order, all collected — list returned, non-empty fails):

1. **`funding_against_long_rate=...`** / **`funding_against_short_rate=...`** (L279–L294) — set when `|rate|` exceeds `cfg.funding_blocker_threshold_pct = 0.001` (config.toml L403, 0.1%) AND the rate sign opposes `consensus["direction"]`.
2. **`manipulation_likely_session`** (L297–L302) — set when `structure.session_context.manipulation_likely == True`.
3. **`recent_loss_within_{N}h`** (L307–L310) — set when `symbol in recent_loss_set` (pre-fetched once per tick at `tick` L658–L667 via `src.core.trade_recorder.recent_loss_symbols`). Lookback `cfg.recent_failure_blocker_hours = 1` (config.toml L404).

Live observed counter: `fail_blockers=0` for every recent cycle (no blocker has fired in the captured window).

---

## E.1.3 — XRAY freshness gate

There is **no explicit freshness gate inside `_qualifies` or `_build_package`.** Freshness is enforced implicitly by the underlying cache:

- **File:** `src/analysis/structure/structure_cache.py`
- **Class:** `StructureCache`
- **Default TTL:** `DEFAULT_TTL = 300.0` (5 min) — `structure_cache.py:15`.
- **Read method (L31–L44):**
  ```python
  def get(self, symbol: str) -> StructuralAnalysis | None:
      cached = self._cache.get(symbol)
      if cached:
          cache_time, result = cached
          if time.monotonic() - cache_time < self._ttl:
              self._hits += 1
              return result
      self._misses += 1
      return None
  ```
  → entries older than 300 s are silently treated as missing (returned as None, miss counted).

### Why this rejects 25 of 50 per cycle

`StructureWorker` runs with `batch_size = 25` (config.toml L925, file `src/workers/structure_worker.py:82`). Sweep order (L341): `batch = self._full_universe[self._batch_start:self._batch_start + self._batch_size]` then advance with wrap-around. With 50 watch-list coins this gives a 2-tick rolling sweep — every 5-min interval refreshes 25 coins; the other 25 sit untouched.

- TTL = 300 s = 5 min.
- Sweet-spot interval = 5 min.
- The *previous* batch was written ~5 min ago, so by the time the scanner ticks at sweet-spot 4:00, those entries are at age ≈ 300 s and just past the TTL line.

Live evidence (workers.log):
- `2026-04-27 22:20:45.581 XRAY_TICK_SUMMARY | universe=50 batch=0/2 symbols=25 analyzed=25 cached=50 setups=12 skips=18`
- `2026-04-27 22:20:45.581 XRAY_CACHE_HEALTH | size=50 oldest_age_s=301 hits=139 misses=199 hit_rate=0.41`
- `2026-04-27 22:25:47.330 XRAY_CACHE_HEALTH | size=50 oldest_age_s=302 hits=165 misses=234 hit_rate=0.41`
- `CYCLE_FRESHNESS | cycle_id=c-2026-04-27-22:20 ... xray_age_p50_ms=194995 xray_age_p95_ms=494936 ... xray_keys=50` — p95 (495 s) is past TTL.
- All five recent SCANNER_FILTER_AGGREGATE entries report `fail_no_xray=25` (cycles 22:00, 22:10, 22:15, 22:20, 22:24… see E.1.5 table).

Cache field `size=50` because `set()` does not auto-evict; entries linger but `get()` returns None when stale, so 25 are functionally absent each cycle.

---

## E.1.4 — Composite scoring

### Formula (scanner_worker.py L182–L208)

```
struct_norm  = clip( (StructureWorker.get_setup_score(coin) or 0.0) / 100.0, 0..1 )
strat_norm   = clip( (StrategyWorker.get_score(coin) or 0.0) / 100.0,       0..1 )
sig_norm     = clip( SignalWorker.get_signal(coin).confidence or 0.0,       0..1 )
regime_align = _get_regime_alignment(coin)        # -1..+1
regime_norm  = (regime_align + 1.0) / 2.0          # 0..1
funding_norm = clip( |altdata_worker.get_funding(coin) or 0.0| / 0.001, 0..1 )

score = w_structure * struct_norm
      + w_strategy  * strat_norm
      + w_signal    * sig_norm
      + w_regime    * regime_norm
      + w_funding   * funding_norm
```

Missing components (None) contribute 0.

### Weights — `[scanner.scoring_weights]`, config.toml L388–L393

| Component | Weight | Source |
|---|---|---|
| structure | 0.30 | config.toml:389 |
| strategy  | 0.30 | config.toml:390 |
| signal    | 0.15 | config.toml:391 |
| regime    | 0.15 | config.toml:392 |
| funding   | 0.10 | config.toml:393 |
| **Sum**   | **1.00** | |

Default values match in `src/config/settings.py:551–555`.

### Live distribution (last 50 SCANNER_TICK_SUMMARY entries available)

`SCANNER_SELECTED` per-coin DEBUG breakdown is not present in any captured log
(`grep -c SCANNER_SELECTED` → 0 in both `workers.log` and the rotated
`workers.2026-04-27_01-31-00_169356.log`). DEBUG output is disabled at the
sink — only INFO `SCANNER_TICK_SUMMARY` aggregate is available, with
per-cycle `mean_score` and `top=COIN(score)`.

50 most-recent `SCANNER_TICK_SUMMARY` `mean_score` and `top` entries (chronological,
from `workers.2026-04-27_01-31-00_169356.log` and `workers.log`):

| Cycle (HH:MM) | mean_score | top coin | top score |
|---|---|---|---|
| 03:54 | 0.581 | ETHUSDT | 0.754 |
| 03:59 | 0.586 | CRVUSDT | 0.751 |
| 04:04 | 0.601 | LINKUSDT | 0.756 |
| 04:09 | 0.576 | CRVUSDT | 0.757 |
| 04:14 | 0.584 | ETHUSDT | 0.747 |
| 04:19 | 0.570 | CRVUSDT | 0.751 |
| 04:24 | 0.580 | ETHUSDT | 0.747 |
| 04:29 | 0.568 | CRVUSDT | 0.757 |
| 04:34 | 0.583 | ETHUSDT | 0.768 |
| 04:39 | 0.563 | CRVUSDT | 0.751 |
| 04:44 | 0.577 | ETHUSDT | 0.747 |
| 04:49 | 0.567 | CRVUSDT | 0.751 |
| 04:54 | 0.577 | RENDERUSDT | 0.763 |
| 04:59 | 0.561 | CRVUSDT | 0.751 |
| 05:04 | 0.561 | RENDERUSDT | 0.748 |
| 05:09 | 0.541 | AAVEUSDT | 0.750 |
| 05:14 | 0.545 | ETHUSDT | 0.694 |
| 05:19 | 0.537 | MONUSDT | 0.725 |
| 05:24 | 0.547 | ETHUSDT | 0.691 |
| 05:29 | 0.520 | MONUSDT | 0.695 |
| 05:34 | 0.553 | ETHUSDT | 0.703 |
| 05:39 | 0.519 | MONUSDT | 0.689 |
| 05:44 | 0.539 | ONDOUSDT | 0.693 |
| 05:49 | 0.513 | MONUSDT | 0.689 |
| 05:54 | 0.536 | ONDOUSDT | 0.679 |
| 05:59 | 0.508 | MONUSDT | 0.683 |
| 06:04 | 0.482 | RUNEUSDT | 0.648 |
| 06:09 | 0.509 | DYDXUSDT | 0.681 |
| 06:14 | 0.482 | RUNEUSDT | 0.642 |
| 06:24 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 06:29 | 0.214 | DYDXUSDT | 0.642 |
| 06:34 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 06:39 | 0.202 | DYDXUSDT | 0.606 |
| 06:44 | 0.212 | RUNEUSDT | 0.636 |
| 06:49 | 0.308 | BLURUSDT | 0.627 |
| 21:54 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 21:59 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 22:04 | 0.027 | BTCUSDT  | 0.055 |
| 22:09 | 0.123 | BTCUSDT  | 0.247 |
| 22:14 | 0.196 | ETHUSDT  | 0.392 |
| 22:19 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 22:24 | 0.000 | BTCUSDT  | 0.000 (forced) |

**Regime change in the data:** Pre-cycle ~06:24 the scanner ran the legacy code path (file shows `tick:329` line numbers) — Phase-5 qualitative gate was off, all 50 scored, top scores 0.7+. From 06:24 onward it ran the new gate (line numbers `tick:746`/`tick:1060`/`tick:1070`); top scores collapsed to 0.0 (BTC/ETH forced) or ≤0.4 when 1–2 coins qualified.

---

## E.1.5 — Selection logic

### Thresholds — `[scanner.qualitative]` config.toml L399–L406

| Setting | Value |
|---|---|
| `max_selection` | 15 |
| `min_selection` | 0 |
| `min_consensus` | "GOOD" |
| `min_rr_ratio` | 2.0 |
| `require_regime_alignment` | true |
| `funding_blocker_threshold_pct` | 0.001 |
| `recent_failure_blocker_hours` | 1 |

### Selection logic (scanner_worker.py L778–L819)

```
if len(qualified_records) >= n_max:           # 15
    selected = qualified_records[:n_max]
elif len(qualified_records) >= n_min:         # 0
    selected = qualified_records
else:
    selected = qualified_records              # min_selection often 0 today
# BTC/ETH always appended if absent (HR-2 reference pair)
```

`min_selection = 0` means the scanner never falls back; with zero qualifiers `selected` is empty and only BTC/ETH (force=True, score=0.0) end up in `final`.

### Force-include logic (HR-2 / HR-3)

1. **Open positions** (L651, L725): `_open_position_symbols()` returns set, then `forced = (coin in protected) and not qualified` — open-position symbols on watch_list bypass the gate; off-watch-list open-position symbols are scored separately L762–L773.
2. **BTC/ETH reference pairs** (L805–L819): always appended at the end with score 0.0 if missing from `new_symbols`.

### Last 7 cycle outputs (qualified, selected, forced)

Source: `workers.log` SCANNER_FILTER_AGGREGATE + SCANNER_SELECT pairs (L746, L1060). Newest first.

| Cycle id | qualified | selected | forced | watch_list | top coin | mean_score |
|---|---|---|---|---|---|---|
| c-2026-04-27-22:20 | 0 | 2 | 2 | 50 | BTCUSDT(0.000) | 0.000 |
| c-2026-04-27-22:15 | 0 | 2 | 2 | 50 | BTCUSDT(0.000) | 0.000 |
| c-2026-04-27-22:10 | 0 | 2 | 2 | 50 | ETHUSDT(0.392) | 0.196 |
| c-2026-04-27-22:05 | 0 | 2 | 2 | 50 | BTCUSDT(0.247) | 0.123 |
| c-2026-04-27-22:00 | 0 | 2 | 2 | 50 | BTCUSDT(0.055) | 0.027 |
| c-2026-04-27-21:55 | 0 | 2 | 2 | 50 | BTCUSDT(0.000) | 0.000 |
| c-2026-04-27-21:50 | 0 | 2 | 2 | 50 | BTCUSDT(0.000) | 0.000 |

Per-cycle filter aggregate (same 7 cycles):

| Cycle | total | qualified | fail_no_xray | fail_setup_none | fail_consensus | fail_regime | fail_rr | fail_blockers | pass_xray | pass_consensus_strong | pass_consensus_good |
|---|---|---|---|---|---|---|---|---|---|---|---|
| c-2026-04-27-22:20 | 50 | 0 | 25 | 10 | 12 | 2 | 1 | 0 | 15 | 3 | 0 |
| c-2026-04-27-22:15 | 50 | 0 | 25 | 6 | 14 | 2 | 3 | 0 | 19 | 4 | 1 |
| c-2026-04-27-22:10 | 50 | 0 | 25 | 10 | 12 | 2 | 1 | 0 | 15 | 3 | 0 |
| c-2026-04-27-22:05 | 50 | 0 | 25 | 6 | 15 | 1 | 3 | 0 | 19 | 4 | 0 |
| c-2026-04-27-22:00 | 50 | 0 | 25 | 10 | 13 | 0 | 2 | 0 | 15 | 2 | 0 |
| c-2026-04-27-21:55 | 50 | 0 | 25 | 5 | 19 | 0 | 1 | 0 | 20 | 0 | 1 |
| c-2026-04-27-21:50 | 50 | 0 | 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

`c-2026-04-27-21:50` is a cold-start: structure cache was empty for ALL 50 coins.

---

## E.1.6 — 7 consecutive `qualified=0` cycles — full forensic trace

**Selected cycle for 50×5 trace:** `c-2026-04-27-22:20` (`SCANNER_FILTER_AGGREGATE` emitted at `2026-04-27 22:24:00.014`).

Workflow timing in this cycle:
- StructureWorker tick @ 22:20:45 — `XRAY_TICK_SUMMARY universe=50 batch=0/2 symbols=25 analyzed=25 errors=0 cached=50 session=late_ny(mid) setups=12 skips=18 el=579ms` ; `XRAY_CACHE_HEALTH size=50 oldest_age_s=301`.
- StrategyWorker tick @ 22:21:37 — `STRAT_L3_DONE consensus=12 consensus_dist=[STRONG:4, WEAK:4, GOOD:3, LEAN:1]`; `STRAT_L4_HANDOFF score_cache_size=19 consensus_size=19 consensus_summary_size=6 hints_top20_size=7`.
- AltDataWorker tick @ 22:21:52 — `ALTDATA fg=None funding=50 oi=0 el=7273ms`.
- ScannerWorker tick @ 22:24:00.

### Bucket totals (verbatim from log)

```
SCANNER_FILTER_AGGREGATE | cycle_id=c-2026-04-27-22:20 total=50 qualified=0
fail_no_xray=25 fail_setup_none=10 fail_consensus=12 fail_regime=2 fail_rr=1
fail_blockers=0 pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0
```

Sum: 25 + 10 + 12 + 2 + 1 + 0 = 50 ✓

### Coin-by-coin bucket assignment

The scanner reads:
- StructureCache via `services["structure_worker"]._cache.get(symbol)` — **only the 25 coins analyzed at 22:20:45 (batch 0/2) have fresh entries.** The other 25 (batch 1/2 — last analyzed at 22:15:45) are at TTL=300 s+ and silently return None.
- `services["layer_manager"].get_strategy_consensus(symbol)` — 12 entries fresh from the 22:21:37 L3 cycle.
- `services["regime_worker"].get_regime(symbol)` — `per_coin_size=49`.

**Batch 1/2 — all 25 fail at criterion 1 with `no_xray_analysis`:** BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT, ARBUSDT, NEARUSDT, ATOMUSDT, INJUSDT, RENDERUSDT, ONDOUSDT, ENAUSDT, PYTHUSDT, SEIUSDT, AEROUSDT, RUNEUSDT, GALAUSDT, MANAUSDT, SANDUSDT, AXSUSDT, LDOUSDT.
*Inferred from `XRAY_(CLASSIFY|NONE_REASON)` lines at 22:20:45 — these 25 coins are NOT logged at 22:20:45 (their last analysis was 22:15:45, batch 1/2; logged at 22:25:47.329 next cycle as the rolling sweep returns to them).*

**Batch 0/2 — 25 coins analyzed at 22:20:45**, partitioned by setup_type from `XRAY_(CLASSIFY|NONE_REASON)` lines:

`pass_xray=15` (XRAY_CLASSIFY, setup_type != "none"):
DYDXUSDT (bearish_fvg_ob conf=0.55 score=100), ICPUSDT (bearish_fvg_ob conf=0.70 score=100), IMXUSDT (conf=0.55 score=49), HBARUSDT (conf=0.55 score=98), GMTUSDT (conf=0.55 score=49), FILUSDT (conf=0.55 score=100), MNTUSDT (conf=0.55 score=83), MONUSDT (conf=0.55 score=93), PLUMEUSDT (conf=0.55 score=100), BLURUSDT (conf=0.55 score=100), OPUSDT (conf=0.55 score=49), APTUSDT (conf=0.55 score=30), LTCUSDT (conf=0.55 score=30), BCHUSDT (conf=0.55 score=64), ALICEUSDT (conf=0.55 score=100).

`fail_setup_none=10` (XRAY_NONE_REASON, setup_type=="none"):
CRVUSDT, AAVEUSDT, HYPEUSDT, SKRUSDT, EGLDUSDT, ALGOUSDT, BSBUSDT, KATUSDT, HYPERUSDT, ORCAUSDT.

### Sample fail-bucket coins with EXACT input values

#### Bucket: `fail_no_xray=25` (sample 3 — all return None from `cache.get`)

For all 25, `services["structure_worker"]._cache.get(symbol) → None` because their entry was last set at `2026-04-27 22:15:45` (5 min before scanner tick at 22:24:00), age ≈ 495 s ≥ TTL 300 s.

| Symbol | Last `XRAY_CLASSIFY/NONE_REASON` event | Cache age at scanner read | Returned |
|---|---|---|---|
| BTCUSDT | 22:15:45.x batch (next batch, not in 22:20:45 logs) | ≈ 495 s | None |
| LINKUSDT | 22:15:45.x batch | ≈ 495 s | None |
| ATOMUSDT | 22:15:45.x batch | ≈ 495 s | None |

Verification: `CYCLE_FRESHNESS | cycle_id=c-2026-04-27-22:20 ... xray_age_p50_ms=194995 xray_age_p95_ms=494936 xray_keys=50` (workers.log @ 22:24:00.020) — half the keys are at p95=495 s (past TTL).

#### Bucket: `fail_setup_none=10` (sample 3)

These coins have a fresh entry but `setup_type.value == "none"`. Verbatim `XRAY_NONE_REASON` lines from workers.log @ 22:20:45:

```
sym=CRVUSDT closest_type=BEARISH_FVG_OB
  missed_by='no_short_downtrend_align(dir=short,struct=ranging)'
  weakest_input=direction_alignment mtf=0.70 smc=0.70 direction=short structure=ranging

sym=AAVEUSDT closest_type=BULLISH_FVG_OB
  missed_by='no_fresh_bullish_ob;no_long_uptrend_align(dir=long,struct=ranging);
             mtf_score=0.40<fvg_ob_min=0.70'
  weakest_input=direction_alignment mtf=0.40 smc=0.25 direction=long structure=ranging

sym=ALGOUSDT closest_type=BULLISH_FVG_OB
  missed_by='no_long_uptrend_align(dir=long,struct=ranging);mtf_score=0.60<fvg_ob_min=0.70'
  weakest_input=direction_alignment mtf=0.60 smc=0.70 direction=long structure=ranging
```

In the scanner: `setup_type.value == "none"` → criterion 1 fails with `no_xray_setup_type`.

#### Bucket: `fail_consensus=12` (sample 3)

These coins passed criterion 1 (have `setup_type != "none"`) but consensus is missing or not in `{STRONG, GOOD}`. The 15 pass_xray coins: DYDX, ICP, IMX, HBAR, GMT, FIL, MNT, MON, PLUME, BLUR, OP, APT, LTC, BCH, ALICE. From `STRAT_CONSENSUS_SUMMARY total=10 GOOD=3 LEAN=1 STRONG=3 WEAK=3` (writer's filtered-down set was 10 in this cycle; STRAT_L4_HANDOFF reports cache `consensus_size=19` carrying older entries) and only 3 of the 15 pass_xray coins matched STRONG (per `pass_consensus_strong=3`).

12 of the 15 fail at criterion 2. Sample (consensus values pulled from the most recent STRAT_CONSENSUS_CHANGE entries within the 5-min window):

| Symbol | Consensus value seen | Required | Reason |
|---|---|---|---|
| DYDXUSDT | `WEAK` (changed `NONE→WEAK` @ 22:21:37.785, score=0.30) | STRONG/GOOD | `consensus=WEAK` — fail criterion 2 |
| HYPERUSDT | `LEAN` (changed `WEAK→LEAN` @ 22:21:37.785, score=0.50) — but HYPERUSDT also has setup_type=none (in fail_setup_none bucket) | STRONG/GOOD | (HYPER actually short-circuits at criterion 1) |
| ALICEUSDT | no STRAT_CONSENSUS_CHANGE in last 10 min → consensus.get("consensus") in `{WEAK, NONE}` or entry absent | STRONG/GOOD | `consensus=WEAK` or `consensus=NONE` — fail criterion 2 |
| LTCUSDT | no recent CHANGE (last logged near 22:11–22:21 cycles) — likely `WEAK` or absent | STRONG/GOOD | fail criterion 2 |

`pass_consensus_strong=3` and `pass_consensus_good=0` confirms only 3 STRONG (no GOOD) made it past criteria 1 & 2 in this cycle.

#### Bucket: `fail_regime=2` (sample 2)

3 coins pass criterion 1 + 2 (the `pass_consensus_strong=3` set). 2 of them fail criterion 3. From `STRAT_REGIME_DIST | up=0 down=20 ranging=23 volatile=6 dead=0` (22:21:30) — `up=0`. Any STRONG-consensus coin with `direction="long"` automatically fails criterion 3 unless the per-coin regime is `ranging` (no `trending_up` exists this cycle). Volatile (6 coins) always fail. Without per-coin regime entries logged at INFO, exact identities can't be paired here, but the structural cause is: 6/49 coins are in `volatile`, which `_regime_aligns` rejects for both directions. Live the failure pattern is `regime=volatile_vs_long` or `regime=volatile_vs_short`.

#### Bucket: `fail_rr=1` (sample 1)

After the 2 regime failures, 1 coin remains. It fails RR at `cfg.min_rr_ratio = 2.0`. Looking at the 15 pass_xray coins, the candidates whose XRAY_ANALYZE shows `rr<2.0` and `direction=short`: DYDXUSDT had rr_s=7.8 (would pass), ICPUSDT rr_s=2.1 (would pass), IMXUSDT rr_s=0.4 / rr=1.0(long) (fail), GMTUSDT rr_s=0.9 (fail), MNTUSDT rr_s=0.2 / rr=1.5(long) (fail). The exact identity of the `fail_rr=1` coin can't be pinned without DEBUG SCANNER_FILTER_RESULT lines (currently disabled), but the threshold is 2.0 and the input field is `structure.structural_placement.rr_ratio`.

#### Bucket: `fail_blockers=0`

No blocker fired this cycle (funding magnitudes all `< 0.001` against direction; no `manipulation_likely` session flag; no recent loss in last hour).

### Summary of root causes for `qualified=0`

1. **Half the universe is silently invalidated by TTL** — `StructureCache.TTL=300 s` matches the structure-worker sweep interval (5 min × 2 batches = 10 min full sweep) so 25 coins are always at age ~5 min and 25 coins are always at age ~10 min. The latter 25 fail `cache.get()` (returns None) → `fail_no_xray`.
2. **Of the 25 fresh structure entries, ~10 have `setup_type=none`** (analyzer rejects them on `direction_alignment`/`mtf_score`/`fresh_ob` rules) → `fail_setup_none`.
3. **Of the remaining ~15, only 3–4 have a STRONG/GOOD consensus** in `_strategy_consensus` — StrategyWorker's L3 produces 10–12 consensus dist with WEAK/LEAN dominating → `fail_consensus=12`.
4. **Regime alignment trims another 1–2** because `STRAT_REGIME_DIST` shows `up=0` and 6/49 coins are `volatile` (always fails).
5. **RR threshold trims any with `rr_ratio < 2.0`** — ~1/cycle.
6. **No blocker fires** in the captured window.

Net: 0 qualifiers.  BTC/ETH force-include yields `selected=2 forced=2`.

---

## Gaps / NOT FOUND

- **NOT FOUND — searched** workers.log + workers.2026-04-27_01-31-00_169356.log for `SCANNER_SELECTED` (per-coin DEBUG breakdown) — 0 hits in either file. Distribution of component breakdowns (struct/strat/sig/regime/funding) per selected coin cannot be measured from logs because DEBUG-level logging is disabled at the sink. Only INFO-level `SCANNER_TICK_SUMMARY mean_score` + `top=COIN(score)` is available.
- **NOT FOUND — searched** workers.log for `SCANNER_FILTER_RESULT` (per-coin DEBUG fail reason) — 0 hits. Cannot enumerate the exact failing coin per RR / regime bucket from logs alone; identification done by intersecting structure analyzer logs (`XRAY_CLASSIFY`/`XRAY_NONE_REASON`) with consensus summary and regime distribution.
- **No log of the per-coin regime label at INFO** — `STRAT_REGIME_DIST` gives the global histogram but the per-coin label is only available via the regime_worker's in-memory accessor; no INFO-level `REGIME_STATE_BY_COIN` line.
