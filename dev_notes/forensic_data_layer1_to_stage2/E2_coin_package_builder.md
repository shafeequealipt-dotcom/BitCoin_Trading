# E2 — Coin Package Builder Forensic Data

**Capture timestamp (UTC):** 2026-04-27 23:03:16
**Source files:**
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/core/coin_package.py` (133 lines)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/core/coin_package_validator.py` (193 lines)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/scanner_worker.py` (`_build_package` L316–L499)
**Live log:** `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
**Config:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml` `[coin_package_validator]` (L1072–L1075)

---

## E.2.1 — `CoinPackage` schema (verbatim)

`src/core/coin_package.py` L26–L133. All fields (type + default value):

### `StructuralLevels` (L26–L33)
| Field | Type | Default |
|---|---|---|
| `current_price` | `float` | `0.0` |
| `suggested_sl` | `float` | `0.0` |
| `suggested_tp` | `float` | `0.0` |
| `rr_ratio` | `float` | `0.0` |

### `XrayBlock` (L35–L45)
| Field | Type | Default |
|---|---|---|
| `setup_type` | `str` | `"none"` |
| `setup_score` | `float` | `0.0` |
| `setup_type_confidence` | `float` | `0.0` |
| `structural_levels` | `StructuralLevels` | `StructuralLevels()` (default factory) |
| `mtf_confluence` | `str` | `""` |
| `session` | `str` | `""` |
| `session_phase` | `str` | `""` |
| `key_features` | `list[str]` | `[]` (default factory) |

### `StrategiesBlock` (L48–L55)
| Field | Type | Default |
|---|---|---|
| `fired_count` | `int` | `0` |
| `fired_strategies` | `list[str]` | `[]` (default factory) |
| `ensemble_consensus` | `str` | `"NONE"` |
| `consensus_score` | `float` | `0.0` |
| `total_score` | `float` | `0.0` |

### `SignalsBlock` (L58–L64)
| Field | Type | Default |
|---|---|---|
| `confidence` | `float` | `0.0` |
| `direction` | `str` | `"neutral"` |
| `sentiment_score` | `float` | `0.0` |
| `sentiment_articles_count` | `int` | `0` |

### `AltDataBlock` (L67–L73)
| Field | Type | Default |
|---|---|---|
| `funding_rate` | `float` | `0.0` |
| `funding_signal` | `str` | `"neutral"` |
| `oi_change_4h_pct` | `float` | `0.0` |
| `fear_greed` | `int` | `0` |

### `PriceDataBlock` (L76–L82)
| Field | Type | Default |
|---|---|---|
| `current` | `float` | `0.0` |
| `change_24h_pct` | `float` | `0.0` |
| `volume_24h_usd` | `float` | `0.0` |
| `regime` | `str` | `""` |

### `CoinPackage` (L85–L133)
| Field | Type | Default |
|---|---|---|
| `symbol` | `str` | (required, no default) |
| `qualified` | `bool` | (required, no default) |
| `opportunity_score` | `float` | (required, no default) |
| `qualification_reasons` | `list[str]` | `[]` (default factory) |
| `price_data` | `PriceDataBlock` | `PriceDataBlock()` |
| `xray` | `XrayBlock` | `XrayBlock()` |
| `strategies` | `StrategiesBlock` | `StrategiesBlock()` |
| `signals` | `SignalsBlock` | `SignalsBlock()` |
| `alt_data` | `AltDataBlock` | `AltDataBlock()` |
| `open_position` | `dict | None` | `None` |
| `blockers_observed` | `list[str]` | `[]` (default factory) |
| `built_at` | `float` | `time.time()` (default factory) |

Methods (L127–L133):
- `to_dict()` → `dataclasses.asdict(self)` (full nested dict).
- `size_bytes()` → `len(json.dumps(self.to_dict(), default=str))`.

---

## E.2.2 — `_build_package()` — VERBATIM and per-field source

Source: `src/workers/scanner_worker.py` L316–L499.

```python
    def _build_package(
        self,
        symbol: str,
        score: float,
        record: dict,
        forced: bool,
    ) -> CoinPackage:
        """Layer 1 restructure Phase 6 — build self-contained CoinPackage.

        Reads existing caches with defensive ``getattr/get`` patterns so a
        missing service degrades to sensible defaults rather than crashing
        the cycle. Missing fields contribute a note to ``blockers_observed``
        rather than failing the whole package.

        Returns:
            Fully-populated CoinPackage matching blueprint Section 11.2.
        """
        blockers_observed: list[str] = list(record.get("blockers", []))

        # ── XRAY block ────────────────────────────────────────────────
        sw = self.services.get("structure_worker")
        structure = None
        try:
            cache = getattr(sw, "_cache", None) if sw else None
            structure = cache.get(symbol) if cache and hasattr(cache, "get") else None
        except Exception:
            structure = None

        levels = StructuralLevels()
        xray = XrayBlock(setup_type="none")
        if structure is not None:
            try:
                levels.current_price = float(getattr(structure, "current_price", 0.0) or 0.0)
                sp = getattr(structure, "structural_placement", None)
                if sp is not None:
                    direction = getattr(sp, "direction", "") or ""
                    if direction == "long":
                        levels.suggested_sl = float(getattr(sp, "long_sl_price", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "long_tp_price", 0.0) or 0.0)
                    elif direction == "short":
                        levels.suggested_sl = float(getattr(sp, "short_sl_price", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "short_tp_price", 0.0) or 0.0)
                    else:
                        levels.suggested_sl = float(getattr(sp, "structural_sl", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "structural_tp", 0.0) or 0.0)
                    levels.rr_ratio = float(getattr(sp, "rr_ratio", 0.0) or 0.0)

                setup_type_obj = getattr(structure, "setup_type", None)
                setup_type_value = (
                    setup_type_obj.value if setup_type_obj is not None else "none"
                )
                session = getattr(structure, "session_context", None)
                xray = XrayBlock(
                    setup_type=setup_type_value,
                    setup_score=float(getattr(structure, "setup_score", 0) or 0),
                    setup_type_confidence=float(
                        getattr(structure, "setup_type_confidence", 0.0) or 0.0
                    ),
                    structural_levels=levels,
                    mtf_confluence=str(getattr(structure, "confluence_quality", "")),
                    session=str(getattr(session, "current_session", "")) if session else "",
                    session_phase=str(getattr(session, "session_phase", "")) if session else "",
                    key_features=[],
                )
            except Exception:
                blockers_observed.append("xray_extract_failed")
        else:
            blockers_observed.append("xray_missing")

        # ── Strategies block ──────────────────────────────────────────
        lm = self.services.get("layer_manager")
        consensus = None
        if lm and hasattr(lm, "get_strategy_consensus"):
            try:
                consensus = lm.get_strategy_consensus(symbol)
            except Exception:
                consensus = None
        score_total = 0.0
        try:
            stw = self.services.get("strategy_worker")
            if stw and hasattr(stw, "get_score"):
                score_total = float(stw.get_score(symbol) or 0.0)
        except Exception:
            pass
        strategies = StrategiesBlock(
            fired_count=int((consensus or {}).get("vote_count", 0)),
            fired_strategies=[],  # detail kept in _strategy_hints, not packaged here
            ensemble_consensus=(consensus or {}).get("consensus", "NONE"),
            consensus_score=float((consensus or {}).get("consensus_score", 0.0)),
            total_score=score_total,
        )

        # ── Signals block ────────────────────────────────────────────
        sigw = self.services.get("signal_worker")
        signals = SignalsBlock(
            direction=(consensus or {}).get("direction", "neutral"),
        )
        try:
            if sigw and hasattr(sigw, "get_signal"):
                sig = sigw.get_signal(symbol)
                if sig is not None:
                    signals.confidence = float(getattr(sig, "confidence", 0.0) or 0.0)
                    if getattr(sig, "direction", None):
                        signals.direction = str(getattr(sig, "direction"))
        except Exception:
            blockers_observed.append("signal_missing")

        # ── Alt data block ───────────────────────────────────────────
        adw = self.services.get("altdata_worker")
        alt = AltDataBlock()
        try:
            if adw and hasattr(adw, "get_funding"):
                rate = adw.get_funding(symbol)
                if rate is not None:
                    alt.funding_rate = float(rate)
                    alt.funding_signal = (
                        "longs_paying" if rate > 0
                        else "shorts_paying" if rate < 0
                        else "neutral"
                    )
        except Exception:
            blockers_observed.append("funding_missing")
        try:
            fg = self.services.get("fear_greed")
            if fg and hasattr(fg, "get_latest"):
                latest = fg.get_latest()
                alt.fear_greed = int(getattr(latest, "value", 0) or 0)
        except Exception:
            pass

        # ── Price data block ─────────────────────────────────────────
        market = self.services.get("market") or self.services.get("market_service")
        price = PriceDataBlock()
        try:
            if market and hasattr(market, "get_ticker_cached"):
                t = market.get_ticker_cached(symbol)
                if t is not None:
                    price.current = float(getattr(t, "last_price", 0.0) or 0.0)
                    price.change_24h_pct = float(getattr(t, "change_24h_pct", 0.0) or 0.0)
                    price.volume_24h_usd = float(getattr(t, "volume_24h_usd", 0.0) or 0.0)
        except Exception:
            blockers_observed.append("ticker_missing")
        if structure is not None and price.current == 0.0:
            price.current = levels.current_price
        regime_worker = self.services.get("regime_worker")
        try:
            if regime_worker and hasattr(regime_worker, "get_regime"):
                state = regime_worker.get_regime(symbol)
                if state is not None:
                    price.regime = (
                        state.regime.value
                        if hasattr(state.regime, "value")
                        else str(state.regime)
                    )
        except Exception:
            pass

        # ── Open position (force-included only) ──────────────────────
        open_pos: dict | None = None
        if forced:
            try:
                pos_svc = self.services.get("position") or self.services.get("position_service")
                if pos_svc and hasattr(pos_svc, "get_position"):
                    p = pos_svc.get_position(symbol)
                    if p is not None:
                        open_pos = (
                            p.to_dict() if hasattr(p, "to_dict") else dict(getattr(p, "__dict__", {}))
                        )
            except Exception:
                blockers_observed.append("position_lookup_failed")

        return CoinPackage(
            symbol=symbol,
            qualified=not forced,
            opportunity_score=float(score),
            qualification_reasons=list(record.get("reasons_passed", [])),
            price_data=price,
            xray=xray,
            strategies=strategies,
            signals=signals,
            alt_data=alt,
            open_position=open_pos,
            blockers_observed=blockers_observed,
        )
```

### Per-field source mapping

| CoinPackage field | Source service / cache | Read method | Default if source missing | Lines |
|---|---|---|---|---|
| `symbol` | argument from caller | passed in | required | L487 |
| `qualified` | derived (`not forced`) | local | required | L489 |
| `opportunity_score` | argument from caller | passed in (computed by `_compute_opportunity_score`) | `0.0` for forced BTC/ETH | L490 |
| `qualification_reasons` | argument `record["reasons_passed"]` | local | `[]` | L491 |
| `xray.setup_type` | `services["structure_worker"]._cache.get(symbol).setup_type.value` | StructureCache.get (TTL 300 s) | **`"none"`** (XrayBlock default) | L362–L367 |
| `xray.setup_score` | `structure.setup_score` | direct attr | `0.0` | L370 |
| `xray.setup_type_confidence` | `structure.setup_type_confidence` | direct attr | `0.0` | L371–L373 |
| `xray.structural_levels.current_price` | `structure.current_price` | direct attr | `0.0` | L348 |
| `xray.structural_levels.suggested_sl` | `structure.structural_placement.{long,short,structural}_sl_price` (per direction) | direct attr | `0.0` | L353/356/359 |
| `xray.structural_levels.suggested_tp` | `structure.structural_placement.{long,short,structural}_tp_price` (per direction) | direct attr | `0.0` | L354/357/360 |
| `xray.structural_levels.rr_ratio` | `structure.structural_placement.rr_ratio` | direct attr | `0.0` | L361 |
| `xray.mtf_confluence` | `structure.confluence_quality` (str) | direct attr | `""` | L375 |
| `xray.session` | `structure.session_context.current_session` | direct attr | `""` | L376 |
| `xray.session_phase` | `structure.session_context.session_phase` | direct attr | `""` | L377 |
| `xray.key_features` | (always `[]` — not populated) | hardcoded | `[]` | L378 |
| `strategies.fired_count` | `consensus["vote_count"]` from `services["layer_manager"].get_strategy_consensus(symbol)` | dict.get | `0` | L401 |
| `strategies.fired_strategies` | hardcoded `[]` (kept in `_strategy_hints`) | hardcoded | `[]` | L402 |
| `strategies.ensemble_consensus` | `consensus["consensus"]` | dict.get | `"NONE"` | L403 |
| `strategies.consensus_score` | `consensus["consensus_score"]` | dict.get | `0.0` | L404 |
| `strategies.total_score` | `services["strategy_worker"].get_score(symbol)` | accessor | `0.0` | L394–L399 |
| `signals.confidence` | `services["signal_worker"].get_signal(symbol).confidence` | accessor | `0.0` | L417 |
| `signals.direction` | `consensus["direction"]` (then overridden by `sig.direction` if non-empty) | dict.get / accessor | `"neutral"` | L411, L418–L419 |
| `signals.sentiment_score` | NOT WRITTEN — defaults to `0.0` | n/a | `0.0` | (default) |
| `signals.sentiment_articles_count` | NOT WRITTEN — defaults to `0` | n/a | `0` | (default) |
| `alt_data.funding_rate` | `services["altdata_worker"].get_funding(symbol)` | accessor | `0.0` | L428–L430 |
| `alt_data.funding_signal` | derived from sign of `funding_rate` | local | `"neutral"` | L431–L435 |
| `alt_data.oi_change_4h_pct` | NOT WRITTEN — defaults to `0.0` | n/a | `0.0` | (default) |
| `alt_data.fear_greed` | `services["fear_greed"].get_latest().value` | accessor | `0` | L439–L443 |
| `price_data.current` | `services["market"].get_ticker_cached(symbol).last_price` (with `levels.current_price` as fallback if structure exists) | accessor + fallback | `0.0` | L453, L458–L459 |
| `price_data.change_24h_pct` | `ticker.change_24h_pct` | accessor | `0.0` | L454 |
| `price_data.volume_24h_usd` | `ticker.volume_24h_usd` | accessor | `0.0` | L455 |
| `price_data.regime` | `services["regime_worker"].get_regime(symbol).regime.value` | accessor | `""` | L463–L469 |
| `open_position` | `services["position"].get_position(symbol).to_dict()` (only when `forced=True`) | accessor | `None` | L474–L485 |
| `blockers_observed` | accumulated locally + `record["blockers"]` | local | `[]` | L333, L381, L421, L437, L457, L485 |
| `built_at` | `time.time()` at construction (CoinPackage default factory) | local | (always set) | (dataclass default) |

### Fields that default to zero/None when source is missing (silent zeros)

- `xray.setup_type = "none"` and all `structural_levels.*` zero when `cache.get(symbol) is None` (TTL miss).
- `strategies.ensemble_consensus = "NONE"`, `vote_count → fired_count = 0` when `get_strategy_consensus(symbol) is None`.
- `signals.confidence = 0.0` when `signal_worker.get_signal(symbol)` is None or accessor missing.
- `signals.sentiment_score = 0.0`, `signals.sentiment_articles_count = 0` — never populated (always zero).
- `alt_data.funding_rate = 0.0` when accessor returns None.
- `alt_data.fear_greed = 0` when fear_greed service returns None / missing — and the `int(getattr(latest, "value", 0) or 0)` collapses any falsy `value` to 0. Live: AltDataWorker logs `fg=None` on its own ticks (see workers.log @ 22:21:52, 22:26:55, 22:31:50, 22:36:54, 22:41:50 all `fg=None`); the SentimentAggregator uses a separate FearGreedService that returns `fg=47` (visible in workers.log @ 22:26:03). Whether the scanner's `services["fear_greed"]` resolves to the working FearGreedService or the broken AltDataWorker accessor is determined by service registration in WorkerManager (out of scope of this file).
- `alt_data.oi_change_4h_pct = 0.0` — never populated (always zero).
- `price_data.current = 0.0` when ticker cache miss AND no structure (otherwise falls back to `levels.current_price`).
- `price_data.regime = ""` when regime_worker returns None.

---

## E.2.3 — Package validator

Source: `src/core/coin_package_validator.py` (193 lines).

### Validation logic

Pure function `validate_package(pkg, *, fail_below, warn_below, staleness_fail_seconds, now_unix=None) → ValidationResult` (L72–L193).

**Required rules** (each contributes 1.0 to `req_score`, count `req_count`):
| Field name (label) | Pass condition | Lines |
|---|---|---|
| `symbol` | `bool(pkg.symbol) and isinstance(pkg.symbol, str)` | L121 |
| `qualified` | `isinstance(pkg.qualified, bool)` | L122 |
| `opportunity_score` | finite `float` in `[0, 1]` | L123–L128 |
| `price_data.current` | `pkg.price_data.current > 0` | L129–L134 |
| `built_at` | `now - pkg.built_at < staleness_fail_seconds` (if not, also added to `stale_fields`) | L135–L139 |

Total: 5 required fields.

**Optional rules** (each contributes 1.0 to `opt_score`, weighted at 0.5×):
| Field name (label) | Pass condition | Lines |
|---|---|---|
| `xray.setup_type` | `setup_type != "none"` | L142–L144 |
| `xray.structural_levels.suggested_sl` | only when `setup_type != "none"`, must be `> 0` | L147, L150 |
| `xray.structural_levels.suggested_tp` | only when `setup_type != "none"`, must be `> 0` | L148, L151 |
| `xray.structural_levels.rr_ratio` | only when `setup_type != "none"`, must be `> 0` | L149, L152 |
| `strategies.fired_count` | `int >= 0` | L154–L159 |
| `signals.confidence` | finite `float` in `[0, 1]` | L160–L166 |
| `price_data.regime` | non-empty | L167–L170 |
| `alt_data.fear_greed` | `int > 0` | L171–L174 |

Total: 8 optional fields, but the 3 SL/TP/RR optionals are conditional on `setup_type != "none"` (so when setup_type is "none", `opt_count = 5`; when setup_type is set, `opt_count = 8`).

### Completeness scoring formula (L177–L179)

```
denom = req_count + 0.5 * opt_count
completeness = (req_score + 0.5 * opt_score) / denom if denom > 0 else 0.0
completeness = max(0.0, min(1.0, completeness))
```

### Verdict thresholds (L181–L186)

```
if completeness < fail_below:  verdict = "fail"   (default fail_below = 0.50)
elif completeness < warn_below:  verdict = "warn"  (default warn_below = 0.85)
else:                           verdict = "ok"
```

Config (`config.toml` L1072–L1075):
```
[coin_package_validator]
fail_below = 0.50
warn_below = 0.85
staleness_fail_seconds = 300.0
```

### Quarantine action

`scanner_worker.py:866–873` — packages with verdict == `"fail"` are dropped (`continue`) and never inserted into the `_coin_packages` cache. Stage 2 never sees them.

---

## E.2.4 — Cold-start package — exact field-by-field analysis

### Cycle selected

The user-described "first package after restart" pattern is reproduced live in **cycle `c-2026-04-27-22:20`** (PACKAGE_VALIDATE @ `2026-04-27 22:24:00.017`):

```
PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=BTCUSDT
  completeness=0.67 verdict=warn
  missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']
  stale=[]
PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=ETHUSDT
  completeness=0.73 verdict=warn
  missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']
  stale=[]
PACKAGE_VALIDATE_SUMMARY | cycle_id=c-2026-04-27-22:20
  packages_built=2 ok=0 warn=2 fail_quarantined=0
```

These match the spec's expected 4 missing fields verbatim for BTCUSDT.

### Why each of the 4 fields is missing (BTCUSDT)

#### 1. `price_data.current` missing

- Path: `_build_package` L450–L455 reads `services["market"].get_ticker_cached(symbol).last_price`. If the market service or the ticker is None, `price.current` stays `0.0`.
- Fallback at L458–L459: `if structure is not None and price.current == 0.0: price.current = levels.current_price`.
- **In this cycle, BTCUSDT is in batch 1/2 of the structure worker** (analyzed at 22:15:45, age ≈ 495 s when the scanner ticks at 22:24:00). So `cache.get("BTCUSDT") → None` (TTL=300 s expired). With `structure is None`, the fallback does NOT fire. And ticker_cache for BTCUSDT may also be missing under the resolved service registration — combined effect: `price.current = 0.0`.
- Validator label: `price_data.current` (required) → fails when `<= 0`.

ETHUSDT differs: `missing` for ETH does NOT include `price_data.regime` ⇒ ETHUSDT had a regime label populated; but `price_data.current` IS in ETH's missing list, so its ticker is also unavailable AND its structure cache entry was also expired (same batch 1/2).

#### 2. `xray.setup_type` missing (counted as "missing" by the validator label `xray.setup_type` because `setup_type == "none"`)

- Path: `_build_package` L336–L383. With `cache.get("BTCUSDT") → None` (cache miss for batch 1/2), the `else` branch at L382 sets `xray = XrayBlock(setup_type="none")` (default) and appends `xray_missing` to `blockers_observed`.
- Validator: `setup_type != "none"` is the optional rule label. With value `"none"`, the optional rule fails — counted in `missing_fields` AND the conditional SL/TP/RR optional rules are skipped (so `opt_count = 5` instead of 8 for this package).
- **Confirmed cause:** structure cache TTL miss because BTCUSDT is in the *other* structure batch (last analyzed 22:15:45, age 495 s ≥ TTL 300 s at scanner read time 22:24:00).

#### 3. `price_data.regime` missing (BTCUSDT only)

- Path: `_build_package` L460–L471 reads `services["regime_worker"].get_regime(symbol).regime.value`.
- Live: `REGIME_TICK_SUMMARY universe=50 global=ranging per_coin_size=49` (workers.log @ 22:21:21.657) — regime_worker only has 49 entries. `STRAT_REGIME_DIST | up=0 down=20 ranging=23 volatile=6 dead=0 other=0 total=49` (22:21:30) — confirms 49.
- BTCUSDT is the regime worker's `primary_symbol` (config.toml L491: `primary_symbol = "BTCUSDT"`) and is excluded from `_per_coin_regimes` (it's tracked separately as the global regime). So `regime_worker.get_regime("BTCUSDT")` returns None → `price.regime = ""` (default) → validator counts this optional rule as missing.
- ETHUSDT is in the per-coin regime dict (49 includes ETH), so its `price_data.regime` IS populated and is NOT in ETH's missing list.

#### 4. `alt_data.fear_greed` missing

- Path: `_build_package` L438–L444: `services["fear_greed"].get_latest().value`.
- Live: AltDataWorker's tick reports `fg=None` consistently:
  - `2026-04-27 22:21:52.289 ALTDATA | fg=None funding=50 oi=0 el=7273ms`
  - `2026-04-27 22:26:55.156 ALTDATA | fg=None funding=50 oi=50 el=10137ms`
  - `2026-04-27 22:31:50.192 ALTDATA | fg=None funding=50 oi=0 el=5190ms`
  - `2026-04-27 22:36:54.159 ALTDATA | fg=None funding=50 oi=50 el=9156ms`
- A SentimentAggregator path does see `fg=47` (workers.log @ 22:26:03) via a separate FearGreedService. So the live FearGreed value (47) exists somewhere in the system, but the service registered as `services["fear_greed"]` (or whichever the scanner reads) returns None → `int(getattr(None, "value", 0) or 0) = 0`.
- Validator rule: `int(pkg.alt_data.fear_greed) > 0`. With value 0, optional rule fails → label `alt_data.fear_greed` added to `missing_fields`.

### Completeness math for BTCUSDT cold-start package

With `setup_type == "none"`, optional rules used (L142–L174) are **5** (the 3 SL/TP/RR optional rules are skipped):
- `xray.setup_type` (FAIL — value "none")
- `strategies.fired_count` (PASS — defaults to 0, which is `>= 0`)
- `signals.confidence` (PASS — defaults to 0.0 which is in [0,1])
- `price_data.regime` (FAIL — empty string)
- `alt_data.fear_greed` (FAIL — value 0)

Required (5): `symbol` PASS, `qualified` PASS, `opportunity_score` PASS, `price_data.current` FAIL, `built_at` PASS → req_score = 4.

Optional (5): `xray.setup_type` FAIL, `strategies.fired_count` PASS, `signals.confidence` PASS, `price_data.regime` FAIL, `alt_data.fear_greed` FAIL → opt_score = 2.

```
denom = 5 + 0.5 * 5 = 7.5
completeness = (4 + 0.5 * 2) / 7.5 = 5.0 / 7.5 = 0.6666... → rounded 0.67 ✓
```

This matches the live log (`completeness=0.67 verdict=warn`).

For ETHUSDT (no `price_data.regime` missing): req_score = 4, opt_score = 3 (regime passes).
```
completeness = (4 + 0.5 * 3) / 7.5 = 5.5 / 7.5 = 0.7333... → rounded 0.73 ✓
```

Matches live log.

### What would need to be true for `completeness = 1.0` (BTCUSDT)?

All 5 required + all relevant optional rules must pass.

1. **`price_data.current > 0`** → `services["market"].get_ticker_cached("BTCUSDT")` returns a non-None Ticker with `last_price > 0` AT scanner read time. Today this is failing because the resolved market service is not exposing fresh ticker data for BTCUSDT under the service-key "market" / "market_service" used by the scanner.
2. **`xray.setup_type != "none"`** → `services["structure_worker"]._cache.get("BTCUSDT")` must return a fresh `StructuralAnalysis` (within 300 s) AND its `setup_type.value` must be a non-"none" value (e.g. `bullish_fvg_ob`). Today this fails because BTCUSDT is in batch 1/2 — last analyzed at 22:15:45, age 495 s at scanner read at 22:24:00 ≥ TTL 300 s. Either (a) `StructureCache.TTL` must exceed 300 s by enough to cover the 5-min worst-case stride, or (b) StructureWorker must analyze the full 50 every cycle, or (c) ScannerWorker must run after both batches in the cycle. Additionally the analyzer must classify BTCUSDT to a non-"none" setup at that read.
3. **Following from (2), the 3 conditional optional rules** (`suggested_sl > 0`, `suggested_tp > 0`, `rr_ratio > 0`) become applicable; all three must be populated by `structural_placement` for the active direction.
4. **`price_data.regime` non-empty** → `services["regime_worker"].get_regime("BTCUSDT")` must return a `RegimeState` with `regime.value` non-empty. Today `regime_worker` excludes BTCUSDT from `_per_coin_regimes` because it is `primary_symbol` (config L491). Either BTCUSDT is added to per-coin coverage, or `_build_package` falls back to the global regime for BTCUSDT.
5. **`alt_data.fear_greed > 0`** → `services["fear_greed"].get_latest()` must return a record with `.value > 0`. Today AltDataWorker logs `fg=None` repeatedly; only SentimentAggregator's path reads `fg=47`. Either AltDataWorker's fear-greed fetch must succeed, or `services["fear_greed"]` must point to the working FearGreedService used by the SentimentAggregator.

When all five hold, BTCUSDT's completeness is `(5 + 0.5 * 8) / (5 + 0.5 * 8) = 9.0 / 9.0 = 1.0` (note opt_count rises to 8 once `setup_type != "none"` enables the SL/TP/RR rules).

---

## Notes / observations from the build path (NOT recommendations, just data captured during read)

- `_build_package` consistently writes to `pkg.price_data` and `pkg.alt_data` (correct field names per dataclass). However, the per-active-universe enrichment helper `_enrich_for(coin)` in `tick` (`scanner_worker.py:973–990`) reads `pkg.price.volume_24h_usd`, `pkg.price.change_24h_pct`, `pkg.alt.funding_rate`. The CoinPackage dataclass exposes those nested blocks under attribute names `price_data` and `alt_data` (see `coin_package.py:118, 122`), not `price` / `alt`. The `try/except` at L984–L989 silently returns `(0.0, 0.0, 0.0, 0.0)` on AttributeError. Live evidence: the `active_universe` table from the snapshot DB shows `volume_24h, change_24h_pct, funding_rate, spread_pct = 0.0, 0.0, 0.0, 0.0` for both BTCUSDT and ETHUSDT in cycle 22:24:
  ```
  BTCUSDT|0.0|0.0|0.0|0.0|0.0|1|2026-04-27 22:24:00
  ETHUSDT|0.0|0.0|0.0|0.0|0.0|1|2026-04-27 22:24:00
  ```
  (Documented as observation only — no fix proposed in this file.)

## Gaps / NOT FOUND

- **NOT FOUND — searched** workers.log for `PACKAGE_VALIDATE` lines for **non-forced** packages (real qualifiers) — none in the 21:50–22:24 window because every cycle had `qualified=0`. Only BTC/ETH force-included entries were validated, so the cold-start failure pattern dominates the available sample. The two warn-verdict packages in cycle 22:20 are the closest analog to "first package after restart" since the BTC/ETH forced path is functionally indistinguishable from a cold-start (no XRAY, no per-coin regime for BTC).
- **NOT FOUND — searched** `_build_package` and `coin_package.py` for any code that populates `signals.sentiment_score`, `signals.sentiment_articles_count`, or `alt_data.oi_change_4h_pct` — not written anywhere in the current builder. These fields will always be the dataclass defaults (0.0 / 0 / 0.0).
- **NOT FOUND — searched** `services["fear_greed"]` registration in WorkerManager — out of scope of this file (Module C/F territory). The exact identity of `services["fear_greed"]` (FearGreedService vs altdata_worker fallback) is not determined from logs alone.
