# Phase 0 — Issue 3: Layer 3 OFF Enforcement Investigation

**Date:** 2026-04-27
**Brief reference:** `IMPLEMENT_FIVE_CRITICAL_FIXES_PROFESSIONAL.md` § Issue 3, Phase 2

## A — The mechanism

State source of truth: `src/core/layer_manager.py:25-40`. `_layer_active: dict[int, bool]` is initialised all-False; `_persist_state()` (`:69-80`) writes `data/layer_state.json`; `_load_persisted_state()` (`:82-91`) reads it on init.

Live state at investigation time:
```
{"layer_active": {"1": true, "2": false, "3": false}, "user_stopped": true}
```

The `is_layer_active(layer)` check is consulted in three places **inside** the LayerManager itself:
- `:317-357` — CALL_A execution gate (blocks new trade dispatch when L3 off)
- `:351-357` — CALL_A urgent watchdog concerns (blocks if L3 off)
- `:410-419` — CALL_B position-management actions (blocks if L3 off)

These gates short-circuit Claude directives upstream of OrderService. They do NOT block OrderService directly.

Order placement code paths (verified by grep at `src/`):

| # | Caller | File:line | Purpose category |
|---|---|---|---|
| 1 | TelegramBot manual order | `src/telegram/bot.py:689` | telegram_manual |
| 2 | Trading handler | `src/telegram/handlers/trading.py:88` | telegram_manual |
| 3 | Brain v2 directive execution | `src/brain/brain_v2.py:487` | layer3_entry |
| 4 | Strategy worker direct order | `src/workers/strategy_worker.py:1094` | layer3_entry |
| 5 | MCP tool `place_order` wrapper | `src/mcp/tools/trading_tools.py:166-197` | mcp_tool |
| 6 | TransformerProxy (active during transformer switch) | `src/core/transformer.py:945-958` | proxy passthrough |

`OrderService.place_order` (`src/trading/services/order_service.py:86-297`) currently has **zero `is_layer_active(3)` calls**. It validates symbol / SL / leverage / instrument / position size, generates an idempotent `orderLinkId`, logs `ORDER_START`, then issues the Bybit RPC. No gate at this level today.

`PositionService.close_position` (`src/trading/services/position_service.py:130-160`) and SL adjustments (`:259-300`, `:356`, `:383`, `:405`) call `self._client.call("place_order", ...)` **directly** — they do NOT go through OrderService. They emit `POS_CLOSE_START` (line 145), not `ORDER_START`.

`PositionWatchdog` (`src/workers/position_watchdog.py`) issues 12 separate `position_service.close_position(pos.symbol)` calls (lines 474, 505, 959, 1075, 1107, 1166, 1253, 1284, 1354, 1383, 1991, 2227). `ProfitSniper` calls `position_service.close_position(symbol)` at `profit_sniper.py:2274`. Both intentionally bypass OrderService for position management — this matches the architectural rationale that Layer 4 actions are independent of Layer 3.

## A.1 — 18:03:21 ETHUSDT/BTCUSDT incident analysis

The observation logs show `ORDER_START` followed by `ORDER_RETRY_EXHAUSTED` for ETHUSDT and BTCUSDT during an interval where `layer_state.json` reported `layer_active.3 = false`. Because the tags are `ORDER_*` (not `POS_CLOSE_*`), these came from `OrderService.place_order`, NOT from `PositionService.close_position`.

`OrderService.place_order` callers that could fire here:
- `brain/brain_v2.py:487` (Claude directive execution) — would be a `layer3_entry` that should have been blocked at LayerManager but reached OrderService anyway.
- `workers/strategy_worker.py:1094` (direct strategy order placement) — same purpose.
- Telegram or MCP — possible but unlikely without operator interaction.

The most likely conclusion: the LayerManager gate caught most of the directive flow, but a code path bypassed it (e.g., a sub-step inside the strategy_worker flow, or a directive returned by an earlier strategy that was already in-flight when L3 toggled off, or a transformer-switch race). Whatever the exact source, the **root architectural defect is that OrderService has no gate of its own.** That is what Phase 2 fixes.

## B — The dependencies

- LayerManager singleton is registered in the worker manager service container at `src/workers/manager.py:506-507`:
  ```python
  from src.core.layer_manager import LayerManager
  layer_manager = LayerManager(settings, self._services)
  ```
  ServiceContainer key: `"layer_manager"`. Consumed by Telegram handlers via `_svc(context, "layer_manager")`.
- **`src/workers/layer_manager.py` (33,668 bytes) is orphan code.** Zero imports anywhere in the codebase reference it (`grep -rn "workers.layer_manager"` returns nothing). The `src/core/layer_manager.py` (42,677 bytes) is the only one in use. Phase 2a deletes the orphan.
- Race conditions: `_layer_active` mutates synchronously; persistence is via plain `pathlib.Path.write_text(...)` (not atomic). A toggle mid-call could yield inconsistent reads in OrderService if the gate is naive. Mitigation = capture-and-pass `LayerSnapshot` (Approach C, user-selected).
- Telegram dashboard handlers (`src/telegram/handlers/dashboard_handler.py`, `control_handler.py`) read `is_layer_active` for display only — no enforcement implications.

## C — The constraints

- Layer 4 (sniper close, watchdog SL) **must continue to bypass** the L3 gate, by design. Closing existing positions while entries are paused is the architectural intent.
- `OrderService.place_order` signature change (adding `purpose=`) is a breaking change to internal callers; every caller must be updated in the same PR-equivalent (atomic-by-feature).
- `data/layer_state.json` schema cannot change (telegram dashboard reads the existing keys).
- The TransformerProxy (`src/core/transformer.py:945-958`) wraps OrderService when transformer is active; the proxy must thread the `purpose=` kwarg through.

## D — The fix candidates (per brief)

User selected: **A + B + C + LAYER_TOGGLE.**

Mapped to commits in Phase 2:
1. **B** — Add required `purpose: Literal[...]` to `OrderService.place_order`. Update six callers (and the TransformerProxy). Add `purpose=` to `ORDER_START`, `ORDER_RETRY_EXHAUSTED`, and `ORDER_REJECT_*` logs.
2. **A** — At OrderService entry, if `purpose == "layer3_entry"` and `not layer_manager.is_layer_active(3)`: emit `ORDER_REJECT_LAYER3_OFF` and raise `Layer3DisabledError` (new in `src/core/exceptions.py`). Layer 4 purposes (`layer4_close`, `layer4_sl`) bypass by design with an inline comment.
3. **C** — `LayerManager.snapshot_layer_state() -> LayerSnapshot` (frozen dataclass). Capture at top of brain_v2 / strategy_worker entry chains. Pass through to `OrderService`. If snapshot disagrees with current state at OrderService entry AND purpose == `layer3_entry`: abort with `ORDER_REJECT_LAYER3_RACE` and raise `Layer3RaceError`.
4. **LAYER_TOGGLE** — Every method that mutates `_layer_active` emits `LAYER_TOGGLE | layer=N from=... to=... reason=... actor=...` BEFORE persistence. Telegram + CLI paths supply `reason` and `actor`.

For symmetry of audit trail, also add `purpose=layer4_close|layer4_sl` to `POS_CLOSE_START` so the operator's grep can attribute every order placement (Layer 3 vs Layer 4) without ambiguity. This is a small additive change in PositionService.

## E — The observability gap

Today:
- `ORDER_START | link_id=... sym=... side=... type=... qty=...` — no purpose
- `ORDER_RETRY_EXHAUSTED` — no purpose
- `POS_CLOSE_START | link_id=... sym=... side=... qty=...` — no purpose
- No `LAYER_TOGGLE` event log
- No `ORDER_REJECT_LAYER3_OFF` log
- No `ORDER_REJECT_LAYER3_RACE` log

After Phase 2:
- All four ORDER_* tags carry `purpose=`.
- POS_CLOSE_START carries `purpose=layer4_close|layer4_sl`.
- Every layer toggle emits a `LAYER_TOGGLE` event.
- Synthetic and natural rejections are explicitly logged.

## F — The verification approach

| Trial | Procedure | Pass criterion |
|---|---|---|
| 2.1 | L3 off, force a Claude directive (manual trigger) | NO `ORDER_START purpose=layer3_entry`; `ORDER_REJECT_LAYER3_OFF` present |
| 2.2 | L3 off, open position present, watchdog tightens SL | `ORDER_START` does not fire; `POS_CLOSE_START purpose=layer4_sl` fires |
| 2.3 | Toggle L3 on/off via Telegram | `LAYER_TOGGLE` log fires each time with reason+actor |
| 2.4 | Synthetic race: flip L3 between snapshot and place_order | `ORDER_REJECT_LAYER3_RACE` fires |
| 2.5 | Re-grep the 18:03 logs against the new schema (post-deploy reproduction in a controlled run) | Each historical ORDER_RETRY_EXHAUSTED gets a definitive purpose attribution |

Edge cases:
- Telegram manual order while L3 off: honors gate by default; admin override possible via explicit `force=True` (out of scope unless requested).
- MCP tool while L3 off: same.

## G — The rollback path

Each commit reverts independently. Order:

- Commit 1 (purpose= field) is the most cross-cutting; reverting it requires reverting commits 2-4 first because they depend on the parameter.
- Commit 4 (LAYER_TOGGLE log) is purely additive; revert is trivial.
- Commit 3 (snapshot pass-through) is additive on top of commit 1 + 2; revert removes the race check.
- Commit 2 (gate at OrderService) reverts cleanly to "no gate" — re-introduces the leak risk but no other regression.

Phase 2a (orphan-file deletion) reverts by `git revert` of the deletion commit; risk-free because the file was unused.
