# Phase 2 — Layer 3 OFF Enforcement Report

**Date:** 2026-04-27
**Status:** Implementation complete (2 commits + Phase 2a consolidation).

## Commits

| Commit | Subject | Hash |
|---|---|---|
| 2a | Consolidate orphan src/workers/layer_manager.py | `abbd500` |
| 1/2 | Gate at OrderService + purpose= field + LayerSnapshot | `028a6d5` |
| 2/2 | LAYER_TOGGLE event log + POS_CLOSE_START purpose= | `386e1b3` |

## Root cause and fix

**Diagnosis** (Phase 0): `OrderService.place_order` had ZERO `is_layer_active(3)` check. The LayerManager-level gates at `layer_manager.py:317/351/410` short-circuit Claude directives upstream but do not gate any code path that reaches OrderService directly. Six callers (telegram bot/handler, brain_v2, strategy_worker, mcp tools, transformer proxy) were all ungated. The 18:03:21 ETHUSDT/BTCUSDT `ORDER_RETRY_EXHAUSTED` while `layer_active.3=false` confirmed leakage.

Layer 4 destructive actions (sniper close, watchdog SL) intentionally bypass the gate by design — they call `PositionService.close_position` → `BybitClient.call("place_order")` directly, NOT through OrderService, and emit `POS_CLOSE_START` not `ORDER_START`.

**Fix** (user chose A + B + C + LAYER_TOGGLE):

- **B — `purpose=` field**: every `OrderService.place_order` callsite now passes a closed-set string (`layer3_entry|layer4_close|layer4_sl|telegram_manual|mcp_tool|test|other`). Threaded through ORDER_START / ORDER_OK / ORDER_FAIL / ORDER_RETRY / ORDER_RETRY_EXHAUSTED / ORDER_DEDUPED / ORDER_RETRY_OK.
- **A — gate**: `_enforce_layer3_gate` rejects `layer3_entry` (and `telegram_manual` / `mcp_tool` without `force=True`) when L3 is OFF, raising `Layer3DisabledError`. `force=True` does NOT apply to `layer3_entry`.
- **C — `LayerSnapshot`**: capture-and-pass dataclass. If supplied AND its view of L3 differs from live state for `layer3_entry`, raises `Layer3RaceError`.
- **LAYER_TOGGLE**: every `start_layer` / `stop_layer` / `emergency_close_all` mutation emits an attributable warning with `reason=` and `actor=`. Cascading stops emit one event per affected layer.
- **POS_CLOSE_START purpose= symmetry**: `PositionService.close_position` carries `purpose=` (default `layer4_close`) so operators can reconstruct entries (ORDER_*) vs closes (POS_CLOSE_*) without ambiguity.

## Files modified

- `src/core/exceptions.py` — Layer3DisabledError, Layer3RaceError
- `src/core/layer_manager.py` — LayerSnapshot dataclass, snapshot_layer_state(), reason+actor params, LAYER_TOGGLE emits
- `src/trading/services/order_service.py` — `purpose`, `layer_snapshot`, `force`, `_enforce_layer3_gate`, `attach_layer_manager`, threaded purpose into all log lines
- `src/trading/services/position_service.py` — `purpose=` kwarg, threaded into POS_CLOSE_START
- `src/telegram/bot.py`, `src/telegram/handlers/{trading,dashboard_handler,control_handler}.py` — pass `purpose="telegram_manual"` and `reason=`/`actor=` on toggles
- `src/brain/brain_v2.py` — pass `purpose="layer3_entry"`
- `src/workers/strategy_worker.py` — pass `purpose="layer3_entry"`
- `src/mcp/tools/trading_tools.py` — pass `purpose="mcp_tool"`
- `src/workers/manager.py` — `order_svc.attach_layer_manager(layer_manager)` after construction
- `src/workers/layer_manager.py` — DELETED (orphan, Phase 2a)

## Tests

- `tests/test_order_service/test_layer3_gate.py` — 9 tests: layer3_entry blocked/allowed, telegram_manual blocked/forced, layer3_entry force-does-not-apply, race detection, no-LM fails-open, closed-set validation.

## Operator verification runbook

| Trial | Procedure | Pass criterion |
|---|---|---|
| 2.1 | `layer_active.3 = false`; force a Claude directive | NO `ORDER_START purpose=layer3_entry`; `ORDER_REJECT_LAYER3_OFF` present |
| 2.2 | L3 off + open position; watchdog tightens SL | `POS_CLOSE_START purpose=layer4_*` fires; ORDER_* does not |
| 2.3 | Toggle L3 on/off via Telegram | `LAYER_TOGGLE` log with reason=`telegram_*` and actor=`telegram_user:<id>` |
| 2.4 | Synthetic race (flip L3 mid-call) | `ORDER_REJECT_LAYER3_RACE` fires |

## Rollback

Each of the three commits reverts independently. Phase 2a (orphan deletion) is risk-free to revert (re-introduces dead code only). Commit 1 is the safety-critical change; reverting restores prior leak-prone behaviour. Commit 2 is observability-only; reverting only loses LAYER_TOGGLE / POS_CLOSE_START purpose=.
