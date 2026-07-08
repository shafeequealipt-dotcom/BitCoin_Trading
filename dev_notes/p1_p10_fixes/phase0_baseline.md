# Phase 0 Baseline — P1 through P10 Bybit Demo Wiring Fixes

Captured: 2026-05-09 06:00 UTC
Branch: `feature/bybit-demo-adapter` @ `6014f0e` (29+ commits ahead of `main`)
Audit reference: `/home/inshadaliqbal786/AUDIT_BYBIT_DEMO_WIRING_GAPS_FINDINGS.md` (HEAD `9ac9b54`, 2026-05-08)

This file establishes the pre-fix state. Every priority's Phase 4 (Verification) compares its measured outcome against the relevant numbers here.

## 1. Audit Reference Verification

13 file:line references spot-checked against current code. All pass.

| Audit ref | Current line | Match | Excerpt |
|-----------|--------------|-------|---------|
| `src/trading/websocket.py:18` | 18 | YES | `class BybitWebSocket:` |
| `src/workers/position_watchdog.py:2925` | 2925 | YES | `async def _detect_and_record_closes(self, open_symbols: set[str]) -> None:` |
| `src/workers/position_watchdog.py:2936` | 2936 | YES | `if self.coordinator and self.coordinator.is_symbol_cooled_down(symbol):` |
| `src/workers/position_watchdog.py:2953` | 2953 | YES | `shadow_close = await self.position_service.get_last_close(symbol)` |
| `src/workers/position_watchdog.py:2963` | 2963 | YES | `if age_s is not None and age_s <= 120:` (120s freshness gate) |
| `src/workers/position_watchdog.py:3057` | 3057 | YES | `close_reason = self.coordinator.pop_close_reason(symbol) if self.coordinator else "shadow_sl_tp"` |
| `src/workers/position_watchdog.py:3114` | 3114 | YES | `f"Closed by: Shadow SL/TP"` (Telegram literal) |
| `src/core/trade_coordinator.py:166` | 166-167 | YES | `pop_close_reason` returns `"shadow_sl_tp"` fallback |
| `src/core/trade_coordinator.py:576` | 576 | YES | `return shadow_pnl_usd, shadow_pnl_pct, "shadow_authoritative", shadow_exit` |
| `src/bybit_demo/bybit_demo_adapter.py:117` | 117 | YES | `async def get_last_close(self, symbol: str) -> dict[str, Any] | None:` |
| `src/bybit_demo/bybit_demo_adapter.py:186` | 186 | YES | `async def close_position(self, symbol: str, *, purpose: str = "layer4_close") -> Order:` |
| `src/bybit_demo/bybit_demo_adapter.py:430` | 430 | YES | `async def place_order(...)` (BybitDemoOrderService) |
| `src/core/transformer.py:711` | 711 | YES | `def current_mode(self) -> str:` (property) |
| `src/brain/strategist.py:533` | **637** | SHIFT +104 | `source=shadow_live` log site moved (verified by Explore agent) |

Single shift caught: `strategist.py:533 → 637`. P2's Phase 1 will use the current line.

## 2. Runtime State

| Item | Value | Source |
|------|-------|--------|
| Current mode | `bybit_demo` | `transformer_state.current_mode` |
| Mode last switched | 2026-05-08T11:19:26Z | `transformer_state.last_switched_at` |
| Is switching now | 0 (false) | `transformer_state.is_switching` |
| Bybit demo enable timestamp | 2026-05-08 11:27:17 | first `trade_thesis.opened_at` with `exchange_mode='bybit_demo'` |
| Most recent BYBIT_DEMO_BOOT_VALIDATED | 2026-05-09 02:53:11 | `data/logs/workers.log` |
| Equity at last boot | $182,526.45 | BYBIT_DEMO_BOOT_VALIDATED log line |
| Services active | `trading-workers`, `trading-mcp-sse`, `shadow.service` all `active` | `systemctl is-active` |
| `trading.db` journal mode | `wal` | `PRAGMA journal_mode` (P9 prereq satisfied) |

The `bybit_demo` enable timestamp `2026-05-08 11:27:17` is the **canonical backfill cut-over** for P8 and P4: any pre-this-time row is genuinely shadow; any post-this-time row tagged `'shadow'` in `trade_log` is actually `bybit_demo`.

## 3. Database Baseline (trading.db)

| Table | COUNT | Audit-relevant |
|-------|-------|----------------|
| `trade_history` | **0** | P7 — completely empty in bybit_demo mode |
| `orders` | **0** | P7 — completely empty in bybit_demo mode |
| `positions` | 0 | Live-only persistence; demo never writes |
| `trade_thesis` (total) | 1,643 | working |
| `trade_thesis` close_reason='zombie_reconciler' | **36** | P5 — historical zombie rows with pnl=0 |
| `trade_log` (total) | 1,577 | working |
| `trade_log` exchange_mode='shadow' | 1,577 | P8 — every row tagged shadow |
| `trade_log` exchange_mode='bybit_demo' | **0** | P8 — never tagged correctly |
| `trade_log` shadow-tag AFTER bybit_demo enable | **116** | P8 — mistagged demo trades (audit said 73; grew from 73 → 116) |
| `trade_log` shadow-tag BEFORE bybit_demo enable | 1,461 | legitimately shadow |
| `trade_intelligence` | 1,186 | works in both modes |
| `switch_history` | 5 | switching infra used |

`trade_log` schema confirmed (PRAGMA table_info): 18 columns; column 17 is `exchange_mode TEXT NOT NULL DEFAULT 'shadow'` — the default is the root cause of P8.

## 4. Log Tag Baseline (last 48h, 4 most recent workers logs)

Files scanned: `workers.log`, `workers.2026-05-09_04-25-29_960203.log`, `workers.2026-05-08_16-45-45_222886.log`, `workers.2026-05-08_14-36-05_367431.log`. Approximately 24-36 hours of activity in current mode (`bybit_demo`).

### 4.1 Adapter health

| Tag | Count | Note |
|-----|-------|------|
| `BYBIT_DEMO_ORDER_RECEIVED` | 93 | trades placed |
| `BYBIT_DEMO_ORD_SEND` | 93 | sent to Bybit |
| `BYBIT_DEMO_ORD_RESP` | 92 | response received (1 missing — needs P10 follow-up?) |
| `BYBIT_DEMO_POSITION_CLOSE` | 52 | system-initiated closes |
| `BYBIT_DEMO_HTTP_FAIL` | 0 | |
| `BYBIT_DEMO_AUTH_FAIL` | 0 | |
| `BYBIT_DEMO_TIMESTAMP_FAIL` | 0 | |
| `BYBIT_DEMO_RATE_LIMIT_HIT` | 0 | |
| `BYBIT_DEMO_ORDER_REJECT` | 1 | P10 — currently silent to Telegram |
| `BYBIT_DEMO_LEVERAGE_FAIL` | 0 | |
| `BYBIT_DEMO_CLOSE_NO_POSITION` | 0 | |
| `BYBIT_DEMO_CLOSE_REJECT` | 0 | |
| `BYBIT_DEMO_PARTIAL_FILL` | 0 | |
| `BYBIT_DEMO_WALLET_FAIL` | 0 | |
| `BYBIT_DEMO_SET_SL_FAIL` | 1 | P10 — currently silent to Telegram |
| `BYBIT_DEMO_SET_TP_FAIL` | 0 | |
| `BYBIT_DEMO_INSUFFICIENT_BALANCE` | 0 | |
| `BYBIT_DEMO_CALL_FAIL` | 0 | not seen — P10 Phase 1 must verify if it's even an emit site |
| `REDUCE_FALLBACK` | 4 | P10 — un-prefixed tag, currently silent |

### 4.2 Close detection + accuracy

| Tag | Count | Audit-comparable rate |
|-----|-------|------------------------|
| `WD_TICK` | 5,063 | watchdog tick frequency healthy |
| `WD_CLOSE` | 71 | exchange-initiated closes detected via poll |
| `WD_CLOSE_PRICE_FALLBACK` | 15 | P1 — **fallback rate = 15/71 = 21.1%** (audit's 3hr window: 35%; multi-day average lower but still significant) |
| `WD_LAST_CLOSE_FALLBACK` | 6 | P3 — closed-pnl indexer races |
| `WD_LAST_CLOSE_AUTH` | 24 | P3 — closed-pnl returns authoritative data |
| `WD_SHADOW_CLOSE_LOOKUP_FAIL` | 0 | exception path clean |
| `WD_SKIP_CLOSE` | 30 | P5 — cooldown-skip count for U-3 baseline |
| `WD_PNL_MISMATCH` | 0 | data-integrity sentinel clean |
| `WD_ZERO_EXIT` | 0 | exit-price sentinel clean |
| `GHOST_RECONCILED` | 26 | normal flow |

P1 success criterion: `WD_CLOSE_PRICE_FALLBACK / WD_CLOSE` rate drops from **21.1%** to **<5%** post-fix.
P3 success criterion: `WD_LAST_CLOSE_FALLBACK` should drop to near-zero post-fix; `WD_LAST_CLOSE_AUTH` count should increase commensurately.

### 4.3 Coordinator + thesis

| Tag | Count | Note |
|-----|-------|------|
| `COORD_CLOSE_START` | 86 | coordinator close fan-out begins |
| `COORD_CLOSE_END` | 86 | coordinator close fan-out completes (cbs_fired=14) |
| `COORD_CB_FAIL` | 0 | no per-callback failures observed (good for P5 U-6) |
| `THESIS_OPEN` | 92 | new theses (BYBIT_DEMO_ORDER_RECEIVED minus 1 = 92, matches) |
| `THESIS_CLOSE` | 88 | thesis closes — **2 more than COORD_CLOSE_END (88-86=2)** |
| `ZOMBIE_RECONCILE` | 2 | matches THESIS_CLOSE - COORD_CLOSE_END delta |
| `ZOMBIE_CLEANUP` | 2 | cleanup runs (audit's 3hr window: 4) |

P5 success criterion: `THESIS_CLOSE` count - `COORD_CLOSE_END` count delta should drop to 0 (or all delta rows match the legit-orphan signature). `THESIS_CLOSE` rows with `pnl=0%` and `pnl$=0` and `rsn=zombie_reconciler` count should drop to 0 once the watchdog UPDATE successfully overwrites the zombie row.

### 4.4 Persistence + analytics

| Tag | Count | Note |
|-----|-------|------|
| `DL_TRADE` | 87 | data lake writes |
| `DL_TRADE_SUSPECT` | 1 | data integrity flag (audit U-4 case) |
| `TIAS_SAVE` | 86 | TIAS records saved |
| `TIAS_ANALYZED` | 86 | TIAS phase-2 analysis complete |
| `EXCHANGE_SWITCH_REQUESTED` | 0 | no operator switches in window |
| `EXCHANGE_SWITCH_CONFIRMED` | 0 | same |

P7 success criterion: `SELECT COUNT(*) FROM trade_history` and `FROM orders` increase by `BYBIT_DEMO_POSITION_CLOSE + WD_CLOSE` (52 + 71 = 123 over the same window) once persistence wires.

P8 success criterion: subsequent `DL_TRADE` writes carry `exchange_mode='bybit_demo'` rather than the default `'shadow'`. Backfill reduces `trade_log` shadow-tag-after-demo-enable count from 116 to 0.

## 5. Per-Priority Pre-Fix Defect Snapshot

| Priority | Pre-fix metric | Post-fix target |
|----------|----------------|------------------|
| P1 | `WD_CLOSE_PRICE_FALLBACK / WD_CLOSE` = 21.1% (15/71) | <5% |
| P2 | Telegram alerts in bybit_demo say "Closed by: Shadow SL/TP" — 100% mode-incorrect | 0% mode-incorrect |
| P3 | `WD_LAST_CLOSE_FALLBACK` count = 6 (over ~24h) | 0 or near-zero |
| P3 | `close_position` returns `mark_price` as exit-price — 100% (~52 system-initiated closes) | 0% mark-price-as-fill |
| P8 | `trade_log` exchange_mode='bybit_demo' = 0 of 116 demo-era rows; default-tagged 'shadow' | 116/116 correctly tagged after backfill; new writes correct |
| P4 | `_collect_stats` SQL filters by date but not mode | Filters by `(date, mode)` |
| P4 | `trade_intelligence.exchange_mode` column does not exist | Column added with backfill |
| P5 | 36 historical `trade_thesis` rows with `close_reason='zombie_reconciler'` and `pnl=0` | New zombie rows get final pnl via watchdog second pass; historical rows retain zero (immutable) |
| P6 | `BYBIT_DEMO_ORDER_RECEIVED` = 93; gates run = 0 | Gates run = 93/93 in demo mode |
| P7 | `SELECT COUNT(*) FROM trade_history` = 0; `FROM orders` = 0 | Both tables grow with each demo trade |
| P9 | MCP `get_account_info`/`get_positions` calls return live Bybit, not `bybit_demo` | Returns mode-routed data within 5s of mode change |
| P10 | 9 missing tags from `BybitDemoAlertRelay._TRIGGERS`; observed silent emits: 1× ORDER_REJECT, 1× SET_SL_FAIL, 4× REDUCE_FALLBACK | All 9 surfaced to Telegram with appropriate severity + dedup |

## 6. Working Tree State

```
M .gitignore                                  ← Phase 0 addition (this commit)
M data/layer_state.json                       ← runtime state (operator's)
M data/logs/layer1c_full.jsonl                ← runtime state (operator's)
?? data/trading.db.bak-*                      ← legacy DB backups (operator's)
?? dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db
?? dev_notes/three_issues/                    ← prior investigation artefacts
?? dev_notes/p1_p10_fixes/                    ← THIS investigation directory (will commit per priority)
```

`data/stage2_dumps/*.json` (18 files) now suppressed by `.gitignore` addition. Modified-tracked runtime files (`layer_state.json`, `layer1c_full.jsonl`) remain visible in `git status` because they were already tracked; suppression of future modifications would require `git rm --cached`, out of scope.

All P1-P10 commits will use specific file paths in `git add` — never `git add .` or `-A`.

## 7. Verification Gate

- All 13 audit references verified accurate.
- One known shift documented (`strategist.py:533 → 637`).
- Baseline metrics captured for every priority's success criterion.
- Bybit demo enable timestamp identified for P8/P4 backfill criteria.
- Working tree status documented; `.gitignore` updated to suppress runtime-state pollution.
- Services confirmed active.
- WAL mode confirmed (P9 prereq).

Phase 0 complete. Ready for P1 Phase 1.
