# Phase 0 — Pre-Flight Verification Baseline

**Date:** 2026-05-09
**Branch:** `feature/bybit-demo-adapter`
**HEAD:** `0c17edd test(p1-p10): end-to-end pipeline verification (42/42 PASS)`
**Working tree:** runtime artifacts only (`data/layer_state.json`, `data/logs/layer1c_full.jsonl`, 5 `.bak` DBs, 2 untracked forensic folders) — no uncommitted code changes.
**Goal of Phase 0:** capture the as-is logging architecture and runtime baseline so subsequent investigation phases can compare against ground truth without re-deriving the basics. No code changes in Phase 0.

---

## 1. Pre-condition Checks

| Check | Status | Notes |
|---|---|---|
| Audit reference exists | PASS | `/home/inshadaliqbal786/AUDIT_BYBIT_DEMO_WIRING_GAPS_FINDINGS.md` (53 KB, 37 gaps catalogued) |
| Pipeline flowchart exists | PASS | `/home/inshadaliqbal786/PIPELINE_FLOWCHART (1).html` (747 lines, 4-phase by-direction view; lifecycle Part C in audit prompt is the 10-phase authority) |
| Recent logs available | PASS | 24-48h coverage in `data/logs/`, 43 files, 2,830,631 total lines across rotated files |
| System state | LIVE | `data/layer_state.json` and `data/logs/layer1c_full.jsonl` modified continuously by runtime; this is normal |
| Working tree | ACCEPTABLE | Modified files are runtime artifacts only; no in-progress code changes (per CLAUDE.md, only blocks code-edits, not runtime state) |
| `dev_notes/lifecycle_logging_audit/` | CREATED | Phase 0 deliverable folder created this session |

---

## 2. Central Logging Architecture (`src/core/logging.py`, 174 lines)

### LOG_FORMAT (lines 12-15)

```
{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} | {message}
```

### Rotation / Retention (lines 18-19)

- `LOG_ROTATION = "10 MB"`
- `LOG_RETENTION = "7 days"`
- Loguru handler config (lines 134-144, 147-157): `enqueue=True` (thread-safe), `backtrace=True`, `diagnose=False` (no var leakage in production), `format=LOG_FORMAT`.

### `setup_logging()` lifecycle (lines 113-157)

1. **Removes ALL default Loguru handlers** (line 123) — protects MCP stdio protocol.
2. Ensures `data/logs/` exists.
3. Groups components by target file → one sink per unique file (lines 129-144).
4. Adds catch-all sink for unrouted (lines 147-157) → `general.log` via `_default_filter` (line 107).

### `get_logger(component)` (lines 160-173)

Returns `logger.bind(component=component)`. Async-safe — Loguru's `enqueue=True` queue is the thread-safety boundary.

### COMPONENT_ROUTING (lines 26-93) — 41 components, 4 target files

**Routed to `mcp.log`** (1 component):
- `mcp`

**Routed to `brain.log`** (3 components):
- `brain`, `claude_code`, `strategist`

**Routed to `workers.log`** (32 components):
- `worker`, `rule_engine`, `trading`, `sl_tp_validator`, `sl_gateway`, `coordinator`, `data_lake`, `thesis_manager`, `enforcer`, `strategies`, `intelligence`, `analysis`, `fund_manager`, `tiered_capital`, `risk`, `time_decay_sl`, `layer4_protection`, `volatility_profile`, `factory`, `portfolio`, `trade_recorder`, `trading_mode`, `shadow`, `bybit_demo`, `strategy`, `event_buffer`, `urgent_queue`, `layer_manager`, `core`, `tias`, `apex`, `sentinel`, `xray`, `sizing`, `cycle_tracker`, `worker_liveness` (36 entries, but a few overlap)

**Routed to `general.log`** (5 components, by design):
- `database`, `alerts`, `telegram`, `control_handler`, `dashboard`

**Catch-all fallback:** `general.log` via `_default_filter()` (line 107). The CI test (`tests/test_logging_routing.py`) prevents new components from leaking here unintentionally.

---

## 3. AlertManager (`src/alerts/alert_manager.py`, 305 lines)

### Lifecycle

- Constructor (line 28-35): wires `TelegramBot`, `AlertThrottle(max_per_hour=settings.alerts.max_alerts_per_minute * 60)`, `AlertTemplates`. Notice: alerts/min × 60 = per-hour cap.
- `initialize()` (line 37-45): connects bot, sets `enabled` flag.
- `shutdown()` (line 291-296): cancels daily-summary task, flushes queue, disconnects bot.

### 15 public send_*/send_custom methods

| # | Method | Line | AlertLevel | Throttled | Notes |
|---|---|---|---|---|---|
| 1 | `send_trade_alert(order, account_balance)` | 47 | INFO | yes | Gated by `settings.alerts.trade_alerts` |
| 2 | `send_position_closed_alert(symbol, side, entry, exit, pnl, pnl_pct)` | 54 | INFO | yes | Gated by `settings.alerts.trade_alerts` |
| 3 | `send_signal_alert(signal)` | 61 | INFO | yes | Skipped if confidence < 0.7 |
| 4 | `send_brain_decision_alert(decision, trigger, cost_usd)` | 70 | INFO | yes | All Brain decisions including holds |
| 5 | `send_error_alert(component, error_message, severity)` | 77 | WARNING or CRITICAL | partial | CRITICAL bypasses throttle |
| 6 | `send_worker_crash_alert(name, error, restart_count, max_restarts)` | 85 | CRITICAL when ≥max | partial | |
| 7 | `send_risk_warning(warning_type, details)` | 93 | always CRITICAL | bypasses | |
| 8 | `send_watchdog_alert(position, current, pnl_pct, warnings, severity)` | 100 | passed by caller | yes/bypass | Severity decided by caller |
| 9 | `send_watchdog_decision(position, decision, cost_usd)` | 114 | CRITICAL on close, else WARNING | partial | |
| 10 | `send_price_alert(symbol, price, change_pct, tf_minutes)` | 127 | WARNING | yes | |
| 11 | `send_system_startup(mode, symbols, workers)` | 134 | bot.send_message direct | bypass | No throttle path |
| 12 | `send_system_shutdown(reason)` | 141 | direct | bypass | No throttle path |
| 13 | `send_daily_summary()` | 148 | direct | bypass | |
| 14 | `send_test_message()` | 156 | direct | bypass | |
| 15 | `send_custom(message, priority)` | 161 | passed by caller | yes/bypass | For custom-formatted messages from external callers (watchdog, layer_manager, strategy_worker) |

### `_send()` (lines 171-210, the core path)

1. Mode indicator prefix from `_transformer.mode_label` if present, else `_trading_mode.mode.{indicator,label}` (handles the "Shadow SL/TP" mislabel scenario per audit).
2. Computes content hash via `AlertThrottle.content_hash(message)` (SHA256-16-char).
3. Dedup check: `throttle.is_duplicate(h)` → debug log `ALERT_THROTTLE | type=dedup` and return False.
4. Rate limit check: `throttle.can_send(priority)` → debug log `ALERT_THROTTLE | type=rate_limit level=...` + queue → return False.
5. Telegram silent flag: `silent = (priority == AlertLevel.INFO)`.
6. `bot.send_message(message, silent=silent)`.
7. On success: INFO log `ALERT_SENT | level={priority} len=... | {ctx()}` + `record_send` + `record_content`.
8. On failure: ERROR log `ALERT_FAIL | level={priority} | {ctx()}` + reposition dashboard.

**Gap candidate (Phase 9/Recording):** `_send()` ALERT_THROTTLE event is at DEBUG which is filtered out at the file sink (workers.log shows 0 DEBUG entries). Throttling and dedup events are effectively invisible. Investigation phases will cite this.

### AlertLevel enum (`src/core/types.py:123-127`)

```python
class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
```

---

## 4. AlertThrottle (`src/alerts/throttle.py`, 96 lines)

- `max_per_hour` defaults to 30. Effective cap = `settings.alerts.max_alerts_per_minute × 60`.
- `dedup_window = 300` seconds (5-minute SHA256 dedup).
- `content_hash(text)` = `hashlib.sha256(text.encode()).hexdigest()[:16]`.
- `can_send(priority)`:
  - CRITICAL → always True.
  - else: clean timestamps older than 1h, return `len(timestamps) < max_per_hour`.
- `record_send()` appends `time.time()`.
- `is_duplicate(content_hash)` checks `dedup_cache` after pruning entries older than 5 min.
- `queue_alert(alert)` stacks into `queued` list for `flush_queue()`.

---

## 5. Context binding (`src/core/log_context.py`, 146 lines)

### Truncation caps (lines 33-35)

- `MAX_ERR_LEN_SHORT = 80` — per-symbol per-tick logs (kline, regime).
- `MAX_ERR_LEN = 120` — default warning/error lines.
- `MAX_ERR_LEN_LONG = 200` — subprocess stderr tail, prompt rejects.

### 4 ContextVars (lines 39-42)

| ID | ContextVar | Generator | When set | Lives until |
|---|---|---|---|---|
| `did` | `_decision_id` | `new_decision_id()` → `f"d-{ms}"` | Start of each Claude strategic review cycle | Cycle ends |
| `tid` | `_trade_id` | `new_trade_id(symbol)` → `f"t-{symbol}-{ms}"` | When rule engine approves a trade | Trade close |
| `wid` | `_watchdog_id` | `new_watchdog_id()` → `f"w-{ms}"` | Start of each watchdog tick | Tick ends |
| `sid` | `_strategy_id` | `new_strategy_id()` → `f"s-{ms}"` | Start of each strategy_worker cycle | Cycle ends |

Setters (`set_did`/`set_tid`/`set_wid`/`set_sid`) propagate values across coroutines.

### `ctx()` helper (lines 121-145)

Returns `"did=... tid=... wid=... sid=..."` with only non-empty IDs. Returns `"no_ctx"` if all empty.

---

## 6. CI test (`tests/test_logging_routing.py`, 79 lines)

3 assertions:

1. `test_every_get_logger_component_is_routed()` — scans `src/**/*.py` for `get_logger("name")`, asserts every captured name is a key in `COMPONENT_ROUTING`. **This is the gate that catches new components leaking to general.log.**
2. `test_component_routing_targets_are_valid()` — every routing target ends with `.log`, no slashes/backslashes.
3. `test_scan_finds_known_components()` — sanity: scanner finds at least `worker`, `brain`, `mcp`, `strategist`.

**Implication for Phase 12:** any new `get_logger("X")` call introduced during Phase 12 implementation MUST be paired with adding `"X"` to `COMPONENT_ROUTING` in the same commit, or CI fails.

---

## 7. Baseline metrics — current rotation files

### Line counts (current rotation, not historical)

| File | Lines |
|---|---|
| `data/logs/workers.log` | 12,942 |
| `data/logs/brain.log` | 27,375 |
| `data/logs/mcp.log` | 9,542 |
| `data/logs/general.log` | 1 (just rotated) |
| Across all rotated `workers.*.log` | ~280k cumulative |
| Total all logs aggregate | 2,830,631 lines |

### Severity distribution (current rotation)

| File | INFO | WARNING | ERROR | DEBUG | CRITICAL |
|---|---|---|---|---|---|
| `workers.log` | 12,191 | 751 | 0 | 0 | 0 |
| `brain.log` | 24,998 | 1,919 | 381 | 0 | 0 |
| `mcp.log` | 9,299 | 12 | 196 | 0 | 0 |
| `general.log` | 0 | 1 | 0 | 0 | 0 |

**Observations (not gaps yet — flag for investigation):**
- DEBUG is invisible across all four files. `setup_logging()` defaults `log_level="INFO"`. Any DEBUG-level log statement (such as `ALERT_THROTTLE` at line 192/195 of alert_manager.py) is silently dropped at the sink. **Investigation phases must check whether any operational events sit at DEBUG.**
- `workers.log` ERROR count is 0 in the current rotation — possibly genuine (system stable), possibly because errors are at WARNING level, possibly because errors occurred in earlier rotations. Will sanity-check across the 7-day window in Phase 1.
- CRITICAL count is 0 across all files. Confirms that CRITICAL is rare and reserved for genuine emergencies (matches AlertManager's intent — CRITICAL bypasses throttle).

### Context binding occurrence (current rotation)

| ID prefix | workers.log | brain.log |
|---|---|---|
| `did=` | 106 | 21,068 |
| `tid=` | 3,569 | 831 |
| `wid=` | 2,113 | 765 |
| `sid=` | 236 | 0 |
| `no_ctx` | 3,630 | 2,300 |

**Observation:** `no_ctx` accounts for 28% of `workers.log` and 8% of `brain.log`. Some of these are legitimate (out-of-cycle ticks like `LAYER1A_TICK_DONE`, `PRICE_WS_HEALTH`, `LAYER_STATE_SYNC`, `ENFORCER_STATE`). Investigation phases must confirm per-step whether `no_ctx` is justified or whether context propagation was missed.

### Top tags by frequency (existing observability, current rotation)

**`workers.log` top tags** (descending):

```
12190 INFO              (severity, not a tag)
 1883 M4_TRAIL_FLOOR    (sniper trail update — chatty)
 1312 M4_DECISION       (sniper decision per tick)
  795 WD_TICK
  728 M4_GATED          (sniper gated by cooldown/grace/etc.)
  445 TIME_DECAY_MAE_GUARD
  271 WORKER_LIVENESS_HEARTBEAT
  262 LAYER1A_TICK_DONE
  248 WD_TICK_DONE
  212 SNIPER_AGE_GUARD
  190 SWEET_SPOT_FIRED
  181 PRICE_WS_HEALTH
  174 SNIPER_DEVELOPMENT_GUARD
  153 REGIME
  150 SIG_GEN_INPUT  /  SIG_GEN  /  SIG_CLASSIFY  (each)
  149 FUND_POOLS
  147 VOL_PROFILE  /  SNIPER_SPIKE  (each)
  145 XRAY_ANALYZE
  139 CAPITAL_TIER
  135 SYSTEM_HEALTH  /  LAYER_STATE_SYNC  /  FUND_RECONCILE  /  ENFORCER_BEAT  (each)
  133 XRAY_CONFIDENCE_DETAIL  /  XRAY_CLASSIFY  (each)
  123 ENFORCER_STATE
  120 BASE_WORKER_TICK_SLOW
  111 LOSS
  106 SENTINEL_DEADLINE
   98 SIG_DOWNGRADE
   89 STRONG_BUY
   70 SNIPER_CAP
   51 ENSEMBLE_VOTE_WEIGHTED
   49 XRAY_SCORE
   47 SL_GATEWAY_REJECT
   45 SCANNER_SELECTED  /  SCANNER_LABELED  /  PACKAGE_VALIDATE  (each)
   41 TREND_PULLBACK_LONG
```

**`brain.log` top tags** (descending):

```
24998 INFO              (severity, not a tag)
 2394 STRAT_POS_ACT
 2344 STRAT_DIRECTIVE
 1974 CLAUDE_CALL_START
 1732 CLAUDE_CALL_OK
 1192 CALL_A
 1049 CLAUDE_PROC_SPAWNED
  783 PROMPT_BUILD_DONE
  728 STRAT_CALL_A_START
  691 STRAT_CALL_A_CTX  /  STRAT_CALL_A  (each)
  683 CLAUDE_PROC_STALL_60S
  680 STRAT_CALL_A_END
  643 STRAT_PROMPT_BUILD
  631 STRAT_CALL_A_PLAN
  598 STRAT_CYCLE_START
  587 STRAT_CTX
  585 STRAT_PROMPT
  577 STRAT_PROMPT_SIZE
  522 STRAT_CYCLE_END
  479 POSITION_INVALIDATED
  476 CLAUDE_RETRY
  420 STRAT_PLAN
  417 STRAT_CALL_B_FLIP_NOTICE
  411 STRATEGIST_PACKAGES_READ
  362 STRAT_CALL_B_START  /  STRAT_CALL_B_CTX  /  STRAT_CALL_B  (each)
  359 STRAT_CALL_B_END
  353 STRAT_CALL_B_PLAN  /  STRAT_CALL_B_PARSED  (each)
  235 CLAUDE_PROMPT_TRIMMED
  216 CLAUDE_PROC_STALL_120S
  203 STRAT_PROMPT_REFRESH
  179 STRAT_TOP_N_APPLIED
```

**`mcp.log` top tags** (descending):

```
9299 INFO              (severity, not a tag)
1776 MCP_PROXY_PIPE_END
 923 MCP_PROXY_CONNECT
 889 MCP_PROXY_DISCONNECT
 549 MCP_PROXY_FORCE_EXIT
  64 MCP_INIT
   8 MCP_PROXY_UPSTREAM_FAIL
   5 MCP_PROXY_MSG_ERR
```

**`general.log`** (current rotation): 1 line (alerts log just rotated — historical rotations show ALERT_SENT counts of 3,879+ in `general.2026-05-04_07-22-03_204582.log`).

### Alert event counts (across all rotated `general.*.log` files)

- `ALERT_SENT` total: 6,142 across rotations (highest single rotation: `general.2026-05-04_07-22-03_204582.log` with 3,879).
- `ALERT_FAIL` total: 31 scattered across 9 rotations.
- `ALERT_THROTTLE`: 0 in any file (because emitted at DEBUG; sink filter is INFO).

---

## 8. Sample log lines (context binding inspection)

10 random samples from `workers.log`:

```
INFO M4_GATED              | tid=t-AXSUSDT-sniper           (no did/wid)
INFO LAYER1A_TICK_DONE     | no_ctx                          (cycle outside trade — OK)
INFO WD_LAST_CLOSE_AUTH    | tid=... wid=...                 (good — trade + watchdog)
INFO M4_TRAIL_FLOOR        | (no ctx suffix at all)          (gap candidate — sniper tick)
INFO PRICE_WS_HEALTH       | no_ctx                          (worker health — OK)
INFO ENFORCER_STATE        | no_ctx                          (gap candidate — should be sid?)
INFO M4_DECISION           | tid=t-AXSUSDT-sniper            (good)
INFO LAYER_STATE_SYNC      | no_ctx                          (config sync — OK)
INFO Capital pools updated | (NO TAG, prose-only)            (gap candidate — fund_manager.capital_reserves:50)
```

10 random samples from `brain.log`:

```
INFO CLAUDE_CALL_OK            | did=...                  (good)
INFO CLAUDE_CALL_START         | did=...                  (good)
INFO STRAT_CALL_A_END          | did=...                  (good)
INFO STRAT_CALL_B_FLIP_NOTICE  | did=...                  (good)
INFO STRAT_CALL_B_START        | did=... did=...          (DUPLICATE did= — gap candidate?)
INFO STRAT_CYCLE_END           | did=...                  (good)
INFO PROMPT_BUILD_DONE         | did=...                  (good)
INFO STRAT_POS_ACT             | did=...                  (good)
INFO STRAT_PLAN                | did=...                  (good)
INFO CLAUDE_PROC_STALL_60S     | no_ctx                   (gap candidate — should be did)
```

**Initial observations to flag for investigation phases:**
- `M4_TRAIL_FLOOR` at sniper:1236 — no `| {ctx()}` suffix at all. Compare with `M4_GATED`/`M4_DECISION` which have tid. Phase 6 audit.
- `Capital pools updated:` (capital_reserves:50) — prose, no structured tag. Phase 9 audit.
- `STRAT_CALL_B_START` line shows `did=... did=...` doubled — likely f-string + `{ctx()}` both adding it. Phase 2 audit.
- `CLAUDE_PROC_STALL_60S` shows `no_ctx` despite happening inside a strategist cycle. Phase 2 audit.
- `ENFORCER_STATE` shows `no_ctx` — performance enforcer runs out-of-cycle but is invoked from strategy_worker, so `sid=` SHOULD propagate. Phase 9 audit.

---

## 9. System State

### Git state

```
Branch: feature/bybit-demo-adapter
HEAD: 0c17edd test(p1-p10): end-to-end pipeline verification (42/42 PASS)

Recent commits (last 5):
0c17edd test(p1-p10): end-to-end pipeline verification (42/42 PASS)
89075ef docs(p1-p10/deep-audit): per-file verification report + 1 style cleanup
b0032c6 docs(p1-p10/cross-check): final test posture (2498 pass, 1 pre-existing fail)
00c9534 fix(p1-p10/cross-check): post-implementation cleanup + 2 test regression fixes
16de649 fix(p10): surface 9 silent BYBIT_DEMO_* tags through AlertManager (L11-G3 + L11-G4)
```

### Working tree (acceptable for Phase 0 — runtime artifacts only)

```
Modified:
  M data/layer_state.json           (continuously updated by layer_manager)
  M data/logs/layer1c_full.jsonl    (continuously appended by Layer 1C)

Untracked:
  ?? data/trading.db.bak-pre-dead-workers-fix-20260427-165401
  ?? data/trading.db.bak-pre-output-quality-fix-20260427-185043
  ?? data/trading.db.pre-layer1-restructure.20260427.bak
  ?? data/trading.db.pre-post-layer1-fixes.20260427.bak
  ?? dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db
  ?? dev_notes/three_issues/
```

None of these are code changes. Phase 0 proceeds.

---

## 10. Verification Gate

| Gate | Status |
|---|---|
| Audit reference confirmed | PASS |
| Pipeline flowchart confirmed | PASS |
| Logging architecture documented | PASS |
| AlertManager methods inventoried | PASS (15 public send_*/send_custom) |
| Context binding semantics documented | PASS (4 IDs, ctx() helper, truncation caps) |
| CI test understood | PASS (3 assertions, blocks unrouted components) |
| Top-tag baselines captured per file | PASS |
| Severity distribution captured | PASS |
| Context-binding presence sampled | PASS (10 lines per file inspected) |
| Alert event counts captured | PASS (6,142 ALERT_SENT, 31 ALERT_FAIL across rotations) |
| `dev_notes/lifecycle_logging_audit/` exists | PASS (created this session) |
| System state recorded | PASS |

**Phase 0 verification gate:** PASS. Proceeding to Phase 1.

---

## 11. Notes carried forward to investigation phases

- The DEBUG sink is silently dropped (default `log_level=INFO` in `setup_logging`). Any operational event currently logged at DEBUG is invisible. Phases 1-10 will count these.
- `no_ctx` rate is 28% of `workers.log` and 8% of `brain.log`. Phases 1-10 will distinguish "legitimately out-of-cycle" from "context propagation missed".
- `M4_TRAIL_FLOOR` and `Capital pools updated:` are early gap candidates already visible in baseline samples — to be confirmed in Phase 6 and Phase 9 audits respectively.
- The CI test means new component names cost a `COMPONENT_ROUTING` entry; investigation phases will note when a fix would require this.
- Rotated `workers.*.log` files (46k-48k lines each) are available if a 24-48h window proves insufficient for any phase's tag verification.
