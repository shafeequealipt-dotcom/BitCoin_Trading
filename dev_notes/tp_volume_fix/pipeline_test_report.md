# Real-Runtime Pipeline Test Report — TP-Volume-Closure Fix

**Date:** 2026-05-07
**Method:** Real `config.toml` + real component wiring + live `data/logs/workers.log` evidence + real `StrategyWorker._execute_claude_trade` flow with capture-bound loguru sink.
**Verdict:** **PASS** — all 8 pipelines green, 27 individual checks all green.

Run via `python3 tests/tp_volume_fix_pipeline_test.py`. Exit 0 = green.

---

## 1. Live system snapshot

| Item | Value |
|---|---|
| Branch | main |
| HEAD | `24236d8 test(flip-tp/phase-1f): edge-case coverage + lint cleanup + XRAY_SLTP deferral note` |
| Six-commit fix range | `54c4e86..24236d8` (parent `7406dbf`) |
| Live `workers.log` size | ~6 MB |
| `XRAY_DIR_FLIP` events in current log | 14 |
| `TRADE_SKIP rsn=sltp_skip` events in current log | 10 |
| `XRAY_FLIP_TP_DERIVATION` events in current log | **0** (correct — pre-restart, fix not yet boot-loaded) |
| `XRAY_FLIP_TP_DERIVATION_DEGRADED` events | 0 |
| Both systemd services | active (per Phase 0 baseline) |

The code on disk includes all six commits since `7406dbf`. The running process predates them. Restart loads the new code.

---

## 2. Pipelines

Each pipeline runs against the real production code path; the only mocks are at the I/O leaves (Bybit `market_service`, `order_service`) where running them for real would generate orders against a paper account. Settings, helpers, structure cache contracts, and the cap-emit log shape are all exercised against the live source tree.

### Pipeline 1 — Settings load from REAL `config.toml`

```
PASS  settings.risk.flip_tp.enabled = True            actual=True
PASS  settings.risk.flip_tp.hard_ceiling_pct = 5.0    actual=5.0
PASS  settings.risk.flip_tp.fallback_tp_distance_pct = 2.0    actual=2.0
PASS  settings.risk.flip_tp.structural_buffer_multiplier = 1.0    actual=1.0
PASS  type(s.risk.flip_tp) is FlipTPSettings    type=FlipTPSettings
```

`Settings._load_fresh("config.toml", ".env")` reads the real TOML, dispatches `_build_risk({…})` → `_build_flip_tp({…})`, populates the nested dataclass. The four knobs land at exactly the documented defaults.

### Pipeline 2 — RiskSettings nested-dataclass DI

```
PASS  RiskSettings() default has flip_tp populated   type=FlipTPSettings
PASS  _build_risk({}) → flip_tp is dataclass defaults
PASS  _build_risk(explicit) propagates all 4 fields
PASS  _build_flip_tp({partial}) fills missing fields with defaults
```

Backward-compatibility verified: deployments without `[risk.flip_tp]` in their `config.toml` continue working. Explicit overrides flow through. Partial blocks fill from dataclass defaults.

### Pipeline 3 — SLTPValidator backstop unchanged

```
PASS  SLTPValidator default max_distance_pct = 0.10 (10%)
PASS  validate_tp at 15% distance still returns SKIP=nonsensical
        action=SKIP reason='TP $85.0000 is 15.0% from price — nonsensical'
```

The validator that was rejecting flipped trades still rejects 15% TPs as nonsensical. The cap is upstream of this — its job is to avoid generating bad inputs in the first place, not silence the safety net.

### Pipeline 4 — Pure cap helper, all 5 method branches

```
PASS  branch=structural_kept (struct 1.5% < vol 3.9%)        method=structural_kept
PASS  branch=volatility_capped (struct 15.7% > vol 3.9%)     method=volatility_capped
       tp=0.142228 expected=0.142228
PASS  branch=hard_ceiling (vol*mult 9.0% > ceiling 5%)       method=hard_ceiling
PASS  branch=fallback (vol_profile=None)                     method=fallback
PASS  branch=disabled (settings.enabled=False)               method=disabled
```

All 5 method branches drive cleanly. The `volatility_capped` case uses the live GALAUSDT-shaped failure (15.7% structural target → capped to 3.9% vol-aware for `class=high`).

### Pipeline 5 — Live workers.log evidence (pre-fix bug confirmed in running process)

```
PASS  sltp_skip events present in log (=10)
PASS  XRAY_DIR_FLIP events present in log (=14)
PASS  XRAY_FLIP_TP_DERIVATION events NOT in current log (=0; restart pending)
PASS  XRAY_FLIP_TP_DERIVATION_DEGRADED count consistent (=0)
```

The bug still fires in the running process. After the operator runs `sudo systemctl restart trading-workers trading-mcp-sse`, `XRAY_FLIP_TP_DERIVATION` will start appearing alongside `XRAY_DIR_FLIP` and `sltp_skip` count will drop sharply. This pipeline acts as a deploy-readiness signal.

### Pipeline 6 — Cold imports + circular-import sanity

```
PASS  cold subprocess import of all fix modules succeeds
        rc=0 stdout='OK\n' stderr=''
```

A subprocess Python boots fresh, imports `Settings`, `FlipTPSettings`, `_build_flip_tp`, `_build_risk`, `compute_capped_flip_tp`, all 5 method constants, `StrategyWorker`, `CoinVolatilityProfile`, `VolatilityProfiler`, `SLTPValidator`. No circular import, no missing dep, no syntax error.

### Pipeline 7 — Real `StrategyWorker._execute_claude_trade` end-to-end

A real `StrategyWorker.__new__` instance is wired with an OPUSDT-shaped fake structure cache (rr_short/rr_long ratio 57x → flip), a fake market service returning the live OPUSDT price (0.148), a fake order service returning REJECTED, and a fake volatility profiler returning the live `class=high` profile (recommended_tp_pct=3.90%). Loguru output is captured into an in-memory sink.

```
PASS  function reached order_reject (cap fired downstream of cap site)
        ok=False reason='order_reject'
PASS  trade flipped Buy → Sell                          dir=Sell flip_source=xray
PASS  cap method = volatility_capped                    method=volatility_capped
PASS  trade['take_profit_price'] is the capped value    got=0.142228 expected=0.142228
PASS  XRAY_FLIP_TP_DERIVATION emitted with structured fields
```

The captured event verbatim:

```
XRAY_FLIP_TP_DERIVATION | sym=OPUSDT dir=Sell
  orig_tp=0.127280 capped_tp=0.142228
  structural_dist_pct=14.00 vol_aware_pct=3.90 vol_aware_capped_pct=3.90
  hard_ceiling_pct=5.00 chosen_cap_pct=3.90 chosen_dist_pct=3.90
  method=volatility_capped vol_profile_present=True degraded=False
  | no_ctx
```

This is the exact event operators will see in `data/logs/workers.log` after restart. The 14% structural target (which would have been rejected by the validator) is bounded to 3.9% — a realistic 10-30 minute scalp distance — and the trade flows through to order placement.

### Pipeline 8 — StructuralPlacement schema sanity

```
PASS  StructuralPlacement exposes long_*/short_* SL/TP + rr_* fields
        all 6 fields present
```

Catches future renames: `long_sl_price`, `long_tp_price`, `short_sl_price`, `short_tp_price`, `rr_long`, `rr_short` are the contract the flip path consumes. If any get renamed, this pipeline goes red before the cap logic does.

---

## 3. Cross-cutting verification

| Concern | Verified |
|---|---|
| Real `config.toml` parses cleanly with `[risk.flip_tp]` block | ✓ Pipeline 1 |
| Settings DI wiring (`_build_risk` → `_build_flip_tp`) | ✓ Pipeline 1, 2 |
| Backwards compatibility (missing block → defaults) | ✓ Pipeline 2 |
| Validator-as-backstop preserved (still rejects 15%) | ✓ Pipeline 3 |
| All 5 cap method branches reachable | ✓ Pipeline 4 |
| Live runtime still has the bug (pre-restart) | ✓ Pipeline 5 |
| Cold imports work, no circular deps | ✓ Pipeline 6 |
| Real worker invokes cap, mutates trade dict, emits log | ✓ Pipeline 7 |
| Structural placement contract intact | ✓ Pipeline 8 |

---

## 4. Side-effect-free guarantee

The pipeline test:

* Does NOT modify `data/trading.db` (no DB connection opened).
* Does NOT touch `data/logs/workers.log` (only reads it for evidence).
* Does NOT call the real `market_service` (uses a `_MS` stub).
* Does NOT call the real `order_service` (uses a `_OS` stub returning REJECTED — the expected bail point).
* Does NOT trigger any persistence (the order is rejected before save_thesis).
* Does NOT spawn long-running tasks.

It is safe to run repeatedly during the 24-72h post-deploy soak.

---

## 5. Verdict

**6 atomic commits on main + 27 pipeline checks across 8 real-runtime pipelines all green + 14 fix-specific pytest tests all green + 570/570 focused regression + 2285/2286 full regression (1 pre-existing unrelated failure documented).**

The fix is fully integrated, properly named, correctly wired, and ready for `sudo systemctl restart trading-workers trading-mcp-sse`. The first `XRAY_FLIP_TP_DERIVATION` event will appear within ~1h post-restart on the first qualifying flip; `TRADE_SKIP rsn=sltp_skip` should drop by ≥90% in the first 24h.
