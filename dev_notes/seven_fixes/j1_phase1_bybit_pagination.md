# J1 Phase 1 Step 1.1.8 — Bybit V5 Pagination Investigation (H3)

Captured 2026-05-14 23:00 UTC. Read-only.

## Question

Does the bybit_demo adapter implement pagination for `/v5/position/list`? If not, can the operator's account size ever cause silent tail-loss?

## Findings

### Adapter request shape

`src/bybit_demo/bybit_demo_adapter.py:207-218`:

```python
params: dict[str, Any] = {"category": _CATEGORY}
if symbol is not None:
    params["symbol"] = symbol
else:
    params["settleCoin"] = "USDT"

try:
    envelope = await self._client.get(
        "/v5/position/list", params, op="positions"
    )
```

No `limit` parameter. No `cursor` parameter.

### Response parsing

`src/bybit_demo/bybit_demo_adapter.py:246-247`:

```python
rows = (envelope.get("result") or {}).get("list") or []
```

Reads only the `list` array. No reference to `nextPageCursor`. No loop.

### Client layer

`src/bybit_demo/bybit_demo_client.py:519-538` — `get()` is a single-request wrapper around `_request_with_retry`. No pagination logic.

### Bybit V5 documented behaviour

The Bybit V5 documentation for `/v5/position/list`:
- Optional `limit` parameter, default 20, max 200.
- Response envelope includes `result.nextPageCursor` for pagination.
- When more positions exist than the limit, the cursor is non-empty.

The adapter never sets `limit`, so it defaults to 20 from Bybit's side. Today the operator's account has under 14 positions, well under 20. **No tail-loss is currently happening.**

But the limit is exposed and changes are operator-driven. If the operator scales the strategy and ever exceeds 20 simultaneously-open positions, positions 21+ would be silently invisible.

### Audit-window evidence

In `/home/inshadaliqbal786/SESSION_LOGS_2026-05-14_20-35_to_21-46.log`, the peak observed n in `WD_TICK` was 10. The local `positions` table held at most 13 (audit's claim) or 7-13 (cross-referenced). Both are under the default limit.

The audit's "13 vs 10" gap is NOT pagination. It is the cache-stale residue described in H1.

## Decision

H3 (pagination loop) is real but **not the proximate cause of today's symptoms**. The fix is small but worth doing as defence-in-depth:

```python
# Inside get_positions_with_confirmation, after the initial envelope:
all_rows: list[dict] = list(envelope.get("result", {}).get("list") or [])
cursor = (envelope.get("result") or {}).get("nextPageCursor", "")
_pages = 1
while cursor and _pages < _MAX_POSITIONS_PAGES:
    params["cursor"] = cursor
    envelope = await self._client.get("/v5/position/list", params, op="positions_pg")
    rows = (envelope.get("result") or {}).get("list") or []
    if not rows:
        break
    all_rows.extend(rows)
    cursor = (envelope.get("result") or {}).get("nextPageCursor", "")
    _pages += 1

if _pages >= _MAX_POSITIONS_PAGES and cursor:
    self._log.warning(
        f"BYBIT_DEMO_POSITIONS_PAGINATION_CAP | pages={_pages} "
        f"max={_MAX_POSITIONS_PAGES} cursor_still_present=true | {ctx()}"
    )
```

Where `_MAX_POSITIONS_PAGES = 5` (cap at 100 positions assuming default limit=20, or 1000 with limit=200 if we explicitly pass it). Emit `BYBIT_DEMO_POSITIONS_PAGINATION_CAP` if the cap is ever hit; that should never happen in production unless strategy explodes.

Add a unit test that mocks the client to return a two-page response with `nextPageCursor` non-empty on page 1 and empty on page 2. Verify both pages are read.

## Recommendation

Include H3's pagination loop as **Option E** in the fix-option report. Low priority. Can ship in the same J1 branch but as a separate atomic commit. If the operator wants to defer it (and instead add an alert on `BYBIT_DEMO_POSITIONS_PAGINATION_DETECTED` when the response carries a non-empty cursor under the current single-page path), that is also defensible.

My current recommendation: **ship Option E with the J1 series** as a small hardening commit. It is mechanical, well-bounded, and removes a latent failure mode that future strategy scale-up could trigger.

## Compliance With Master Prompt Rules

- **Rule 3**: Adding pagination is structural, not a band-aid sweeper.
- **Rule 5 (no assumptions)**: The Bybit V5 documented limit default is verified to be 20 (per agent reference; the operator can re-verify against the live API by deliberately opening 21+ positions in a test scenario).
- **Rule 7 (atomic commits)**: Pagination commit is independent of the cleanup/reconciler commits; can land separately.
