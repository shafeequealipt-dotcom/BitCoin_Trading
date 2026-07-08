# Issue 1 — ShadowOrderService.place_order signature drift

**Status:** PRESENT — actively crashing brain trades.
**Tier:** 1 (production blocker).
**Source observation:** `dev_notes/layer1_live_monitor_2026-04-27.md` lines 103–142.

## A. Mechanism

After Layer 1 restructure, `OrderService.place_order` accepts three keyword-only arguments — `purpose`, `layer_snapshot`, `force` — added by the Phase 2 Layer 3 enforcement work (`src/trading/services/order_service.py:231-245`). The corresponding Shadow paper-mode adapter at `src/shadow/shadow_adapter.py:393-403` was never updated. Callers pass `purpose='layer3_entry'` (`src/workers/strategy_worker.py:1232-1242`) and `purpose='mcp_tool'` (`src/mcp/tools/trading_tools.py:170-176`) regardless of which adapter is wired. Live mode succeeds; Shadow rejects with:

```
ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
```

Five crashes in the 29-min observation window (06:34:50 DYDXUSDT, 06:41:50 DYDXUSDT, 06:48:20 RUNEUSDT, 06:48:20 ETHUSDT). Zero brain-driven paper trades have completed since cold-start. The TypeError is caught by the `_execute_new_trades` outer try/except so the worker keeps running — bug is silent at the worker-error level but visible at the trade-completion level.

## B. Dependencies

- **Callers:** `_execute_new_trades` in core/layer_manager.py, MCP tool path, strategy_worker entry path.
- **Live OrderService:** `src/trading/services/order_service.py` (231-245 signature, 318-326 gate dispatch, _enforce_layer3_gate at 155-228).
- **Router:** `src/core/transformer.py:_TransparentService` (delegates `place_order(*args, **kwargs)` to active service — passes whatever it gets).
- **Test fixtures:** `grep` finds no test that pins the Shadow signature.
- **Observation contract:** `BRAIN_DO_TRADE` log expects `rsn=ok` or named filter rejections. TypeError surfaces as `rsn=exception` which masks legitimate skip diagnostics.

## C. Constraints

- Shadow simulation must remain side-effect free for the new kwargs (no real exchange contract for them).
- Adding `**kwargs` would lose type safety and re-mask future drift — forbidden.
- Public method `Order` return type unchanged; we only widen the input parameter list.
- The new kwargs are already part of OrderService's documented contract (docstring at 277-300).
- Shadow already implements `modify_order` / `cancel_order` / `get_open_orders` with compatible-or-narrower signatures (verified vs lines 465-484 of order_service.py).

## D. Fix candidates

1. **Mirror the signature exactly (chosen).** Add `*, purpose: str = "other", layer_snapshot: "LayerSnapshot | None" = None, force: bool = False` to ShadowOrderService.place_order. Log the kwargs at INFO so paper trades carry the audit context. Defer behavioral simulation of `force` and race-detection logic (Shadow doesn't have a Layer 3 gate).
2. `**kwargs` swallowing. Rejected — loses type safety, hides future drift.
3. Caller-side signature inspection / kwarg stripping. Rejected — caller becomes the type firewall, fragile and one-sided.
4. Try/except TypeError in `_execute_new_trades`. Rejected — masks the symptom (forbidden by Hard Rule 1).

Option 1 also lets Shadow eventually consume the kwargs (e.g., reject `purpose='layer3_entry'` if a paper Layer 3 toggle is added) without another contract break.

## E. Observability gap

- No event today says "Shadow received an order with purpose=X". Paper-trade audit can't be reconstructed from logs.
- Add `SHADOW_ORDER_RECEIVED | sym={s} side={d} qty={q} purpose={p} layer_snapshot_keys={k} force={f}` at INFO inside Shadow's place_order before validation/simulation.

## F. Verification approach

- `pytest tests/test_shadow_signature_parity.py` (new, Phase 1) — uses `inspect.signature` to assert `OrderService` ↔ `ShadowOrderService` parity for every public method. Pass = no drift.
- Live trial: 30 min with Layer 2 ON, Layer 3 OFF (paper). Operator counts `BRAIN_DO_TRADE rsn=ok` events. Target: ≥5 successful trades, zero `got an unexpected keyword argument` lines in workers.log.
- Edge case: brain decision with all three kwargs (`force=True`) succeeds — proves we accept all.

## G. Rollback path

Single-file revert of `src/shadow/shadow_adapter.py` plus deletion of `tests/test_shadow_signature_parity.py`. No DB migration. No state mutation. Rollback time: < 1 minute (`git revert <phase1_commit>`).
