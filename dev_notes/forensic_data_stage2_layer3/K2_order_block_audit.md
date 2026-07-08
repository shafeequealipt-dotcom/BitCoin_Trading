# K2 — Order Block Audit Trail

Collection timestamp: 2026-05-02 ~11:45 UTC
Logs searched: workers.2026-05-02_04-31-00_392071.log (active log, 4.5 MB), workers.log (current symlink, 320 KB)
Search query: `grep -h "ORDER_BLOCKED\|ORDER_GATE_LM_DEADLINE_EXCEEDED\|ORDER_REJECT_\|ORDER_GATE_NO_LM" workers.2026-05-02_04-31-00_392071.log workers.log`

---

## 1. Event-by-event (last 24h)

The 24h window contains exactly **4 `ORDER_BLOCKED` events**. All four are paired with `ORDER_ATTEMPT` (preceded ≤ 4 ms before) and `ORDER_GATE_LM_DEADLINE_EXCEEDED` (same millisecond). Each entry below shows the full triple.

### Event 1 — INJUSDT (05:10:34 UTC)

```
ORDER_ATTEMPT       2026-05-02 05:10:34.126 | link_id=ti-fa1828f0cd5c41f2b479eac8 sym=INJUSDT side=Buy purpose=mcp_tool qty=133 force=False
ORDER_GATE_LM_DEADLINE_EXCEEDED  2026-05-02 05:10:34.129 | link_id=ti-fa1828f0cd5c41f2b479eac8 sym=INJUSDT purpose=mcp_tool elapsed_s=9848.2 deadline_s=60.0 action=block
ORDER_BLOCKED       2026-05-02 05:10:34.130 | link_id=ti-fa1828f0cd5c41f2b479eac8 sym=INJUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=9848.2
```
- Timestamp: 2026-05-02 05:10:34 UTC
- Symbol/side/qty: INJUSDT / Buy / 133
- Block reason: `lm_deadline_exceeded` (LayerManager not attached after 60 s deadline; elapsed=9848.2 s)
- Caller (purpose): `mcp_tool`
- Actor: `system_auto`
- did=: NOT FOUND in event — `no_ctx` (the placement was made via mcp_tool path, no decision_id was attached to the log_context). The link_id is `ti-fa1828f0cd5c41f2b479eac8`.

### Event 2 — ONDOUSDT (05:10:35 UTC)

```
ORDER_ATTEMPT       2026-05-02 05:10:35.180 | link_id=ti-9e1c48df37024462a1d09bfb sym=ONDOUSDT side=Buy purpose=mcp_tool qty=1852 force=False
ORDER_GATE_LM_DEADLINE_EXCEEDED  2026-05-02 05:10:35.180 | link_id=ti-9e1c48df37024462a1d09bfb sym=ONDOUSDT purpose=mcp_tool elapsed_s=9849.3 deadline_s=60.0 action=block
ORDER_BLOCKED       2026-05-02 05:10:35.181 | link_id=ti-9e1c48df37024462a1d09bfb sym=ONDOUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=9849.3
```
- Timestamp: 2026-05-02 05:10:35 UTC
- Symbol/side/qty: ONDOUSDT / Buy / 1852
- Block reason: `lm_deadline_exceeded` (elapsed=9849.3 s)
- Caller: `mcp_tool`
- Actor: `system_auto`
- did=: `no_ctx` (link_id `ti-9e1c48df37024462a1d09bfb`)

### Event 3 — AXSUSDT (06:01:57 UTC)

```
ORDER_ATTEMPT       2026-05-02 06:01:57.117 | link_id=ti-cb5a2864c10c489fb7328344 sym=AXSUSDT side=Buy purpose=mcp_tool qty=362 force=False
ORDER_GATE_LM_DEADLINE_EXCEEDED  2026-05-02 06:01:57.118 | link_id=ti-cb5a2864c10c489fb7328344 sym=AXSUSDT purpose=mcp_tool elapsed_s=12931.2 deadline_s=60.0 action=block
ORDER_BLOCKED       2026-05-02 06:01:57.118 | link_id=ti-cb5a2864c10c489fb7328344 sym=AXSUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=12931.2
```
- Timestamp: 2026-05-02 06:01:57 UTC
- Symbol/side/qty: AXSUSDT / Buy / 362
- Block reason: `lm_deadline_exceeded` (elapsed=12931.2 s — over 3 h past deadline)
- Caller: `mcp_tool`
- Actor: `system_auto`
- did=: `no_ctx` (link_id `ti-cb5a2864c10c489fb7328344`)

### Event 4 — MANAUSDT (06:01:57 UTC)

```
ORDER_ATTEMPT       2026-05-02 06:01:57.933 | link_id=ti-294adae3e33c42eabe9432bf sym=MANAUSDT side=Buy purpose=mcp_tool qty=5556 force=False
ORDER_GATE_LM_DEADLINE_EXCEEDED  2026-05-02 06:01:57.933 | link_id=ti-294adae3e33c42eabe9432bf sym=MANAUSDT purpose=mcp_tool elapsed_s=12932.0 deadline_s=60.0 action=block
ORDER_BLOCKED       2026-05-02 06:01:57.933 | link_id=ti-294adae3e33c42eabe9432bf sym=MANAUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=12932.0
```
- Timestamp: 2026-05-02 06:01:57 UTC
- Symbol/side/qty: MANAUSDT / Buy / 5556
- Block reason: `lm_deadline_exceeded` (elapsed=12932.0 s)
- Caller: `mcp_tool`
- Actor: `system_auto`
- did=: `no_ctx` (link_id `ti-294adae3e33c42eabe9432bf`)

### Notional

NOT FOUND — `notional` field — searched: ORDER_BLOCKED format (order_service.py:192-197) and ORDER_ATTEMPT format (order_service.py:488-492). Neither event includes a notional or a price. The events carry only `qty`. To compute notional one would need to cross-reference the price at the timestamp.

---

## 2. Aggregate

### Top block reasons by count

| Rank | Reason | Count | % |
|---|---|---|---|
| 1 | `lm_deadline_exceeded` | **4** | 100% |
| – | `layer3_off` | 0 | 0% |
| – | `layer3_race` | 0 | 0% |
| – | `lm_boot_not_ready` | 0 | 0% |

(Only one reason occurred in the window. The other three closed-set reasons defined at order_service.py:186-191 are absent.)

### Distribution by caller (purpose)

| Purpose | Count | % |
|---|---|---|
| `mcp_tool` | **4** | 100% |
| `layer3_entry` | 0 | 0% |
| `telegram_manual` | 0 | 0% |
| `layer4_close` | 0 | 0% |
| `layer4_sl` | 0 | 0% |

### Distribution by actor (derived field, order_service.py:186-191)

| Actor | Count |
|---|---|
| `system_auto` | 4 |
| `layer3_auto` | 0 |
| `gate` | 0 |

### Distribution by hour (UTC)

| Hour bucket | Count |
|---|---|
| 04:00–05:00 | 0 |
| 05:00–06:00 | 2 (INJUSDT, ONDOUSDT — 05:10) |
| 06:00–07:00 | 2 (AXSUSDT, MANAUSDT — 06:01) |
| 07:00–11:48 | 0 |

### Side distribution

| Side | Count |
|---|---|
| Buy | 4 |
| Sell | 0 |

### Symbols

| Symbol | Count |
|---|---|
| INJUSDT | 1 |
| ONDOUSDT | 1 |
| AXSUSDT | 1 |
| MANAUSDT | 1 |

(Each symbol unique — no repeated rejections of the same coin.)

### Force flag

All 4 events: `force=False`. No `force=True` overrides were attempted.

---

## 3. Provenance / interpretation

All 4 events come from `_emit_order_blocked` at order_service.py:192. The companion `ORDER_GATE_LM_DEADLINE_EXCEEDED` at order_service.py:251 is emitted from `_enforce_layer3_gate` immediately before. The reject path is order_service.py:250-278 (Path 4a — deadline exceeded → fail-close ALL purposes).

`elapsed_s` values (9848.2, 9849.3, 12931.2, 12932.0) are far past the 60 s `lm_attach_deadline_sec`. This implies the OrderService instance handling the mcp_tool calls was **not** the same OrderService that received the LayerManager attachment (or LayerManager failed to attach to it at all). The `_init_monotonic` clock in those calls had been ticking for ~2.7–3.6 hours.

OrderService construction site (workers/manager.py during boot) is the same instance that `attach_layer_manager()` is called against — but the four `mcp_tool` placements were initiated from the MCP server (separate process) which constructs its own OrderService. The MCP-side OrderService never had its LayerManager attached → after the 60 s boot deadline, every placement attempt fails-close with `lm_deadline_exceeded`.

---

## 4. Other gate events in 24h (for context)

Outside the `ORDER_BLOCKED` cohort, the 24h window contains:
- **0** `ORDER_GATE_NO_LM` (Layer-4 fail-open warns — would have been logged for any layer4_close/layer4_sl pre-attach call). Implies LM attached cleanly on the worker-process OrderService before any L4 call.
- **0** `ORDER_REJECT_LAYER3_OFF`, `ORDER_REJECT_LAYER3_RACE`, `ORDER_REJECT_LM_BOOT`, `ORDER_LAYER3_OFF_FORCED`.
- **0** `ORDER_OK` events — no successful placements in the 24h window via OrderService. (Trade activity in the period was via Shadow paths, not Bybit live OrderService.)
- **10+** `GATE_ADJUST` events from the upstream APEX TradeGate (apex/gate.py:334) — these adjust trades but do not block. Not included in the order-block audit.

---

## 5. Gaps

- did= field: NOT FOUND on any of the 4 ORDER_BLOCKED rows — all show `no_ctx` because the calls came from `mcp_tool` purpose, which constructs no `did=` log_context. The `link_id` field is the per-call audit key.
- notional / price: NOT FOUND — emit format does not include them.
- Cross-day visibility: this run started 04:31 UTC on 2026-05-02. Earlier rotated logs (workers.2026-05-01_*.log) were not searched per the brief's "last 24h" window scope; if needed, expand to those files for a strict 24-hour rolling window from 11:45 UTC on 2026-05-01.
