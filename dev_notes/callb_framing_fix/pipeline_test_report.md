# Real-project Pipeline Test Report

**Date:** 2026-05-07
**Method:** Real-config + real-DB-clone integration test of the full DI chain.
**Verdict:** **PASS** — all 8 phases verified end-to-end against the actual `config.toml`, an exact mirror of the live `data/trading.db` (1,434 rows, schema_version=27 → 28), and the production-style service constructors.

---

## 1. Live system snapshot

| Item | Value |
|---|---|
| Live `trading-workers` PID | 399 |
| Live process started | 2026-05-06 17:07:52 UTC |
| Live DB schema version | 27 (pre-v28; will upgrade to 28 on next restart) |
| Live trade_thesis row count | 1,434 (all closed) |
| Latest open trade | 2026-05-06 18:42:17 |
| Code on disk (HEAD) | `ce29517 docs(callb-framing-fix): cross-check audit report` |
| `STRAT_CALL_B_REFRAMED` count in current workers.log | **0** (correct — pre-fix code is running) |
| `SENT_CONSUMPTION_DISABLED` count in current workers.log | **0** (correct — pre-fix code is running) |
| All 3 systemd services | active |

The code on disk includes all 13 commits since `2ac091d`. The running process predates them. Restart loads the new code.

---

## 2. Pipeline test — real config, real DB clone

A 144MB exact mirror of `data/trading.db` was created at `data/trading.db.tmp_audit_clone` and operated on by the production-style DI chain. After the test, the clone was cleaned up. **The live DB was never modified.**

### 2.1 — Settings._load_fresh from real `config.toml`

```
Settings.enforcer.pnl_caution_pct = -3.0
Settings.enforcer.pnl_survival_pct = -12.0    [Phase 2A]
Settings.enforcer.pnl_halted_pct = -15.0      [Phase 2A]
Settings.enforcer.level_2_min_rr = 3.0
Settings.sentiment.consumption_enabled = False [Phase 5B]
```

Real `config.toml` parses cleanly with all Phase 2A + Phase 5B values. ✓

### 2.2 — Migration on the live-DB clone

```
pre-migration: schema_version=27
MIGRATIONS_SUMMARY | total=232 skipped_existing=94
post-migration: schema_version=28
v28 cols missing: set()
thesis row count: 1434  (unchanged)
sample legacy row: id=1764 LINKUSDT Sell xray_flip_source='' xray_flip_ratio=0.0
```

All 232 migration statements ran; 94 pre-existing columns correctly skipped via the PRAGMA-based check; 4 new v28 columns added; 1,434 production rows preserved with default values on the new columns. ✓

### 2.3 — SentimentAggregator real-DI gate

```
aggregator._consumption_enabled = False        [Phase 5B]
aggregator._reddit_intentionally_disabled = True [Phase 10 / dir-block-fix]
```

Both flags wire through `Settings.sentiment` and `Settings.reddit`. The aggregator suppresses per-coin `SENT_DEGRADED_MODE` log spam in production. ✓

### 2.4 — SignalGenerator real-DI gate

```
sg._sentiment_consumption_enabled = False      [Phase 5B]
```

Wired from `Settings.sentiment.consumption_enabled` through `_evaluate_signal`'s `active['sentiment']` flag. ✓

### 2.5 — PerformanceEnforcer real-DI thresholds

```
pe._pnl_caution_pct  = -3.0
pe._pnl_survival_pct = -12.0   [Phase 2A]
pe._pnl_halted_pct   = -15.0   [Phase 2A]
```

`_collect_stats` against the live-DB clone (1,434 rows) returns 0 today — no closed trades on the trial date — which is correct. The stats query (`SELECT symbol, direction, actual_pnl_pct, close_reason, exchange_mode FROM trade_thesis WHERE status='closed' AND DATE(closed_at) = ?`) is unchanged by the v28 column addition. ✓

### 2.6 — ThesisManager v28 round-trip on the live-DB clone

```
INSERT thesis #1765:
  symbol=PIPELINE_TEST_XRAY direction=Sell
  xray_flip_source='xray' xray_flip_ratio=4.5
  xray_flip_rr_long=0.7 xray_flip_rr_short=3.15
SELECT via get_open_theses:
  source='xray' ratio=4.5 rr_long=0.7 rr_short=3.15
THESIS_FLIP_PERSISTED log fired with full context
```

Schema v28 round-trips cleanly with concrete RR justification persisting end-to-end. ✓

### 2.7 — ClaudeStrategist full chain, real DI

The real `Settings`, the real `ThesisManager` (instantiated against the clone), real services dict — all wired through the real `ClaudeStrategist.__init__`. Output verified:

| Phase | Verification |
|---|---|
| 1B | `POSITION_SYSTEM_PROMPT_VERSION == 2`; "thesis is broken" absent; "Aggressive opportunity exploitation" present |
| 1C | per-position "  Thesis:" line absent |
| 1D | "## CONTRACT — POSITION MANAGEMENT" section present; "trust the current state shown above" literal phrase present |
| 1E | "FLIPPED via XRAY from Buy to Sell: RR_chosen=3.15 vs RR_rejected=0.70 (4.5x better)" rendered |

All 4 sub-phases of Phase 1 verified end-to-end against the real DI chain on the real-data clone. ✓

### 2.8 — Phase 2B runtime trace

```
level=2 pnl=-13.5%  rr=2.5
  -> new_tp=103.0 reason='rr_scaled_to_floor' old_rr=2.5 new_rr=3.0
```

`try_adjust_for_survival_rr` scales TP from structural target 102.5 to 103.0 to achieve the floor RR=3.0. The structural ceiling at `struct_tp + (struct_tp - ref_price) * 0.5 = 103.75` accommodates. ✓

### 2.9 — Phase 2A HALTED at all 3 enforcer touch-points

```
level=3 (HALTED):
  qualify_survival_trade  -> (False, "halted")
  try_adjust_for_survival_rr -> (None, "halted")
  clamp_leverage(5)         -> (1, "HALTED_CLAMP: 5->1 (PnL=-13.50%)")
```

Triple defense-in-depth: the qualify gate blocks, the adjust helper refuses, and the clamp drops leverage to 1 if a future call site forgets either gate. ✓

### 2.10 — Phase 4 non-destructive SIG_DOWNGRADE

```
input: STRONG_BUY at confidence=0.30
output:
  signal.signal_type = neutral
  signal.components.original_signal_type = strong_buy
  signal.components.confidence_floor_failed = True
  signal.components.confidence_below_strong = True
  signal.components.confidence_below_buy = True
```

Original classification preserved end-to-end through `Signal.components` JSON-serialization. ✓

### 2.11 — Phase 5B sentiment branch force-deactivation

```
input: sentiment=0.8 (would normally be ACTIVE >= sentiment_min_active=0.05)
        fear_greed=50 funding=0.0 oi=0.0 (other components inactive)
expected (gate ON): NEUTRAL ("no active components")
actual: signal_type=neutral
        reason: "Multi-source: no active components (s=+0.80, fg=+0.00, fund=-0.00, oi=+0.00)"
```

The Phase 5B gate forcibly deactivates the sentiment branch in `_evaluate_signal` even with a strong sentiment score. The score is still computed (visible as `s=+0.80` in the reason string) but the participation flag is gated. ✓

---

## 3. Wiring chain verification

### 3.1 — Settings → DI → service

```
Settings (config.toml: 1,400+ lines)
  ├── enforcer: EnforcerSettings  (pnl_halted_pct=-15.0, pnl_survival_pct=-12.0, level_2_min_rr=3.0, ...)
  │
  └── sentiment: SentimentSettings  (consumption_enabled=False)
          │
          ├── workers/manager.py:164 → SentimentAggregator(db, scorer, settings)
          ├── mcp/server.py:149      → SentimentAggregator(db, scorer, s)        [Phase 5B follow-up]
          ├── brain/__init__.py:84   → SentimentAggregator(self.db, scorer, self.settings)  [Phase 5B follow-up #2]
          ├── workers/manager.py:168 → SignalGenerator(aggregator, db, settings=settings)
          └── mcp/server.py:154      → SignalGenerator(self._services["aggregator"], db, settings=s)
```

Every production + deprecated DI site receives `settings` (verified by enumeration). ✓

### 3.2 — Strategy worker flip-meta round-trip

```
src/workers/strategy_worker.py:1720 (XRAY flip site)
    trade["_flip_source"] = "xray"
    trade["_xray_flip_ratio"] = round(_ratio, 2)
    trade["_xray_flip_rr_long"]  = round(float(_sp.rr_long), 2)
    trade["_xray_flip_rr_short"] = round(float(_sp.rr_short), 2)
        ↓
src/workers/strategy_worker.py:2187-2225 (save_thesis call site)
    _xray_flip_source     = str(trade.get("_flip_source", "") or "")
    _xray_flip_ratio      = float(trade.get("_xray_flip_ratio", 0) or 0)
    _xray_flip_rr_long    = float(trade.get("_xray_flip_rr_long", 0) or 0)
    _xray_flip_rr_short   = float(trade.get("_xray_flip_rr_short", 0) or 0)
    await thesis_mgr.save_thesis(
        ...,
        xray_flip_source=_xray_flip_source,
        xray_flip_ratio=_xray_flip_ratio,
        xray_flip_rr_long=_xray_flip_rr_long,
        xray_flip_rr_short=_xray_flip_rr_short,
    )
        ↓
src/core/thesis_manager.py:71-77 (INSERT)
    INSERT INTO trade_thesis (..., xray_flip_source, xray_flip_ratio,
                              xray_flip_rr_long, xray_flip_rr_short) VALUES (...)
        ↓
src/core/thesis_manager.py:97-104 (THESIS_FLIP_PERSISTED log if non-empty)
        ↓
src/core/thesis_manager.py:122-131 (SELECT in get_open_theses)
    SELECT ..., xray_flip_source, xray_flip_ratio,
                xray_flip_rr_long, xray_flip_rr_short FROM trade_thesis WHERE status='open'
        ↓
src/brain/strategist.py:3251-3287 (CALL_B prompt render)
    if xray_flip_source == "xray":
        sections.append(f"  FLIPPED via XRAY from {orig} to {side}: RR_chosen=...")
```

**Every keyword name is consistent across the 6-step chain.** No aliases, no rename, no orphans. The chain has been live-tested end-to-end with thesis #1765 on the clone DB. ✓

### 3.3 — Single-site DI for trading-critical classes

| Class | Production sites | Audit |
|---|---|---|
| `ThesisManager` | 1 (`workers/manager.py:472`) | ✓ |
| `PerformanceEnforcer` | 1 (`workers/manager.py:1363`) | ✓ |
| `ClaudeStrategist` | 1 (`workers/manager.py:567`) | ✓ |

No risk of a forgotten construction site silently bypassing Phase 2A/2B.

---

## 4. Code-on-disk vs running-process delta

| Item | On-disk (HEAD `ce29517`) | Running (PID 399) | After restart |
|---|---|---|---|
| `POSITION_SYSTEM_PROMPT_VERSION` | 2 | (constant didn't exist) | will fire `STRAT_CALL_B_REFRAMED \| system_prompt_version=2` |
| Schema version | code=28 | DB=27 | DB will upgrade to 28 |
| `EnforcerSettings.pnl_halted_pct` | -15.0 | (didn't exist) | will be loaded |
| `[sentiment]` section | present | (didn't exist) | will be loaded → SENT_CONSUMPTION_DISABLED + SENTIMENT_DEGRADED_MODE\|reason=consumption_disabled |
| `try_adjust_for_survival_rr` method | exists | doesn't exist | will exercise on first SURVIVAL-band trade with rr<3.0 |
| 4 v28 trade_thesis columns | code present | absent | will be added by migration |

After `sudo systemctl restart trading-workers trading-mcp-sse`, all 8 phases activate simultaneously.

---

## 5. Naming consistency final check

The Phase 1E v28 column names appear in **4 src files**, **54 occurrences**, all consistently spelled:

```
src/database/migrations.py        — DDL ALTER TABLE statements
src/core/thesis_manager.py        — kwargs + INSERT + SELECT + log
src/workers/strategy_worker.py    — flip-site dict keys + save_thesis kwargs
src/brain/strategist.py           — CALL_B render
```

Naming patterns parallel existing project conventions:

| New naming | Mirrors existing |
|---|---|
| `xray_flip_source` | `apex_flipped` (v23 boolean), `apex_original_direction` (v23) |
| `xray_flip_ratio` | `apex_reason` (v23 free-text) |
| `xray_flip_rr_long` / `_rr_short` | `entry_xray_confidence` (v27) |
| `pnl_halted_pct` | `pnl_caution_pct`, `pnl_survival_pct` |
| `HALTED_CLAMP` | `PRESERVATION_CLAMP`, `SURVIVAL_CLAMP` |
| `pnl_below_halted` | `pnl_below_caution`, `pnl_below_survival` |
| `SENT_CONSUMPTION_DISABLED` | `SENT_DEGRADED_MODE`, `SENTIMENT_DEGRADED_MODE` |
| `STRAT_CALL_B_REFRAMED` | `STRAT_CALL_B_CTX`, `STRAT_CALL_B_PARSED` |
| `STRAT_CALL_B_FLIP_NOTICE` | `STRAT_CALL_B_URGENT` |
| `THESIS_FLIP_PERSISTED` | `THESIS_OPEN`, `THESIS_CLOSE` |
| `ENFORCER_HALTED` | `ENFORCER_LEVEL`, `ENFORCER_STATE`, `ENFORCER_LEV_CLAMP` |
| `ENFORCER_RR_ADJUSTED` / `_FAIL` | `ENFORCER_LEV_CLAMP` |
| `try_adjust_for_survival_rr` | `qualify_survival_trade` |
| `_scale_tp_for_rr` | private helper |
| `consumption_enabled` flag | (new pattern, but `enabled` matches `[reddit].enabled` and `[finnhub].enabled`) |

No naming drift. ✓

---

## 6. What restart will do (predicted boot sequence)

After `sudo systemctl restart trading-workers trading-mcp-sse`:

1. **DatabaseManager.connect** — fires `DB_CONN | path=data/trading.db wal=Y` + `DB_PRAGMAS | journal_mode=WAL ...`.
2. **run_migrations** — fires `Schema upgrade: 27 -> 28` and `Migrations complete. Schema version: 28`. Adds 4 v28 columns to trade_thesis.
3. **SentimentAggregator init** (3 sites) — fires `SENTIMENT_DEGRADED_MODE | reason=no_reddit ...` + `SENTIMENT_DEGRADED_MODE | reason=consumption_disabled ...`.
4. **SignalGenerator init** (2 sites) — fires `SENT_CONSUMPTION_DISABLED | reason=operator_decision_2026-05-06 ...`.
5. **PerformanceEnforcer init** — uses `pnl_survival=-12.0`, `pnl_halted=-15.0`. First `ENFORCER_STATE` log shows the new mapping.
6. **ClaudeStrategist init** — fires `STRAT_CALL_B_REFRAMED | system_prompt_version=2 close_rules_removed=2 contract=aggressive_management`.
7. **First trade with XRAY flip** — fires `XRAY_DIR_FLIP` (existing) + `THESIS_FLIP_PERSISTED | source=xray ratio=...` (NEW Phase 1E).
8. **First CALL_B cycle for that trade** — fires `STRAT_CALL_B_FLIP_NOTICE | source=xray ratio=...` (NEW). Prompt carries the unified `FLIPPED via XRAY ... Nx better` line.
9. **First SURVIVAL-band trade with structural RR<3.0** — fires `ENFORCER_RR_ADJUSTED | requested_rr=2.5 floor=3.0 final_rr=3.0 ...` (NEW Phase 2B), trade flows through.
10. **Per-coin sentiment log** — count drops to 0 (Phase 5B suppression).

A single `tail -f data/logs/workers.log | grep -E '(STRAT_CALL_B_REFRAMED|SENT_CONSUMPTION_DISABLED|SENTIMENT_DEGRADED_MODE|THESIS_FLIP_PERSISTED|STRAT_CALL_B_FLIP_NOTICE|ENFORCER_HALTED|ENFORCER_RR_ADJUSTED|Schema upgrade)'` will show every new event.

---

## 7. Final verdict

| Check | Verdict |
|---|---|
| Real `config.toml` parses with all Phase 2A + 5B keys | ✓ |
| Real-DB-clone migration (27 → 28) preserves 1,434 production rows | ✓ |
| All 5 trading-critical class construction sites wire `settings` correctly | ✓ |
| Phase 1B `POSITION_SYSTEM_PROMPT_VERSION` boot sentinel reaches CALL_B prompt | ✓ |
| Phase 1C original-thesis line absent from rendered prompt | ✓ |
| Phase 1D contract section + literal phrase present in rendered prompt | ✓ |
| Phase 1E concrete RR notice rendered with correct format under real DI | ✓ |
| Phase 2A HALTED enforced at qualify, adjust, AND clamp_leverage touch-points | ✓ |
| Phase 2B `try_adjust_for_survival_rr` scales TP within structural buffer | ✓ |
| Phase 4 non-destructive downgrade preserves original_signal_type + flags | ✓ |
| Phase 5B sentiment branch force-deactivated under real Settings | ✓ |
| Strategy_worker → thesis_manager → DB → CALL_B prompt: 6-step round-trip | ✓ |
| All naming consistent across files (54 v28 occurrences, no aliases) | ✓ |
| No `try/except: pass` band-aids; no TODO/FIXME markers | ✓ |
| Live process pre-fix; restart pending | (operator action) |

**Outcome:** the fix is fully integrated into the project, all DI is wired correctly through real `Settings` and real DB schema, and the boot sequence is predictable. After operator restart, all 8 phases activate simultaneously and the trial monitors per `phase6_trial.md` apply.

The pipeline is verified.
