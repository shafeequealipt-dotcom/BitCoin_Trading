# I1 Phase 2 — Operator Report: F-26 TIMESTAMP_FAIL Root-Cause Fix

**Status:** awaiting operator decision on fix option.
**Full investigation:** see `i1_phase1_investigation.md` (this report is a 1-page summary for screen-reader review).

---

## What the audit said

`recv_window=5000ms` is too tight. When Bybit returns `code=10002` for
positions or balance queries, the system writes phantom close events
for live positions (AEROUSDT, ORCAUSDT, ALGOUSDT observed in the
2026-05-13 window).

## What the current code shows

The audit is **directionally correct but the root cause is deeper than
recv_window alone.** Phase 1 traced the full call chain:

1. **`bybit_demo_client.py:344-351`** — the gap between signing
   (`timestamp_ms = int(time.time() * 1000)`) and HTTP send
   (`async with self._session.request(...)`) can exceed 5000ms under
   VM load. This produces `retCode=10002`.

2. **`bybit_demo_client.py:430-431`** — the retry loop catches
   `(aiohttp.ClientError, OSError, asyncio.TimeoutError)` but **NOT
   `BybitAPIError`**. So a 10002 hit is NOT retried. The exception
   propagates to the adapter immediately.

3. **`bybit_demo_adapter.py:181-182`** — `except TradingMCPError:
   return []`. **The adapter collapses error and success into the same
   response shape — both look like "Bybit confirms zero positions."**

4. **`position_watchdog.py:503-506`** — when `get_positions()` returns
   `[]`, the watchdog runs `_detect_and_record_closes(set())`. The
   in-function comment says "empty Shadow set is the strongest possible
   signal that everything we still track is a ghost." **That assumption
   is wrong when the empty set comes from an API error.** Every tracked
   symbol becomes a phantom close.

## Proximate cause

`recv_window=5000ms` exceeded by request-send latency under load.

## Architectural ROOT cause

The system has no semantic distinction between three states at the
adapter boundary:

| State | Adapter returns | Downstream consequence |
|-------|-----------------|-------------------------|
| Truly 0 positions on Bybit | `[]` | "close everything" — correct |
| HTTP 401 (auth fail) | `[]` | "close everything" — WRONG |
| 10002 TIMESTAMP_FAIL | `[]` | "close everything" — WRONG |

For a trading system, **"unknown state" must be DISTINCT from "empty
state"**. The safe response to unknown is "preserve last known"; the
safe response to empty is "close everything." These are opposite
actions.

The same `error → empty` pattern affects `get_wallet_balance` (returns
zero-equity AccountInfo) — visible as 2 of the 6 audit-window
TIMESTAMP_FAIL events were `op=balance`.

## Connection to other issues

- **I3 (PNL_MISMATCH):** Both verified PNL_MISMATCH events (ORCAUSDT
  22:37, AEROUSDT 23:06) involve positions phantom-closed during the
  22:40 TIMESTAMP_FAIL cluster. Fixing I1 eliminates the primary
  source of corrupted reconstruct inputs to I3.
- **I4 (DB cascade):** TIMESTAMP_FAIL is correlated with VM load.
  Reducing DB cascade (I4) reduces TIMESTAMP_FAIL frequency.
- **I5 (SEGV):** the 4 TIMESTAMP_FAIL events at 22:40 cluster 80
  seconds before the 22:42:34 SEGV. TIMESTAMP_FAIL is a leading
  indicator of broader pressure events.

---

## Fix options

### Option A — Bump recv_window (band-aid, prompt Rule 3 forbids this alone)

Change `bybit_demo_client.py:222` `recv_window: int = 5000` → `15000`.

- Cost: 1 hour. One-line change.
- Addresses proximate cause; **does NOT address root cause**.
- Doesn't help when retry window itself is exceeded.
- Doesn't cover other endpoints with the same `return []` pattern.

### Option B — Retry on 10002 with fresh timestamp

Add `BybitAPIError` (when `ret_code == 10002`) to the retry-exception
list at `bybit_demo_client.py:430`. The loop already re-signs each
iteration so a retry naturally gets a fresh timestamp. Emit
`BYBIT_DEMO_TIMESTAMP_RETRY` per attempt (Rule 6).

- Cost: 2 hours code + 2 tests.
- Addresses the symptom for transient hits.
- **Still falls through to `return []` when retries exhausted.**
- Doesn't address root cause.

### Option C — Architectural fix: distinguish "unknown" from "empty"

Change adapter contract: `get_positions()` returns a discriminated
result (e.g., `PositionsQueryResult(KNOWN: bool, positions: list)`).
Watchdog checks the `KNOWN` flag at `position_watchdog.py:503` BEFORE
running `_detect_and_record_closes(set())`. When `KNOWN=False`, log
`WD_GROUND_TRUTH_UNKNOWN` and skip the close pass. Emit
`BYBIT_DEMO_TIMESTAMP_UNKNOWN_STATE` (Rule 6) at the adapter.

- Cost: 1 day code + 4 tests + watchdog test coverage.
- **Addresses the architectural ROOT.**
- Scales to all error codes (10002, auth, network) and all endpoints
  (positions, wallet, etc.).
- **Aggressive-exploitation philosophy aligned** — positions never
  silently disappear.

### Option D — Combination A + B + C (defense-in-depth, RECOMMENDED)

1. **A:** Bump `recv_window` 5000 → 10000 ms (modest, still tight).
2. **B:** Retry-on-10002 with re-sign (bounded; existing 5-attempt loop).
3. **C:** Adapter returns discriminated result when all retries
   exhausted; watchdog preserves prior state.

Three new emissions per Rule 6:
- `BYBIT_DEMO_TIMESTAMP_RETRY` (B-side, per attempt)
- `BYBIT_DEMO_TIMESTAMP_UNKNOWN_STATE` (C-side, after retries)
- `WD_GROUND_TRUTH_UNKNOWN` (watchdog-side state preservation)

- Cost: 1.5-2 days code + 6-8 tests + 6h soak verify.
- Eliminates phantom closes at every layer (network jitter, transient
  error, exhausted retry).
- Operator-visible at all three layers.

---

## Recommendation

**Option D.** Rationale:

1. The prompt's Rule 3 forbids band-aid fixes. Option A alone is a
   band-aid.
2. The prompt's Rule 6 explicitly requires both
   `BYBIT_DEMO_TIMESTAMP_RETRY` AND
   `BYBIT_DEMO_TIMESTAMP_UNKNOWN_STATE` emissions — only Option D
   covers both.
3. The architectural ROOT cause (semantic loss at adapter) is only
   addressed by C, but C alone leaves recv_window=5000 too tight,
   meaning UNKNOWN_STATE fires too often. D mitigates both.
4. Wallet-balance endpoint has the same vulnerability; D's C
   component extends naturally to it.
5. Operator's "aggressive opportunity exploitation" philosophy
   demands positions never silently disappear. C is the only option
   that GUARANTEES this; A and B reduce frequency but don't eliminate.

If the operator prefers smaller scope:
- Option D minus C (only A + B): cost 0.5 days; addresses 95% of
  expected cases. Leaves vulnerability for cascade-driven exhausted
  retries.
- Option D minus B (only A + C): cost 1 day; addresses root cause
  but doesn't retry transient hits. Each TIMESTAMP_FAIL still costs
  one full adapter cycle.

---

## Operator decision needed

Please pick:

- [ ] **A** — recv_window bump only (band-aid; declined by Rule 3 unless explicitly approved)
- [ ] **B** — retry-on-10002 only (targeted)
- [ ] **C** — architectural fix only (root cause)
- [ ] **D** — combination A+B+C (RECOMMENDED, defense-in-depth)
- [ ] **D minus B** — A + C only
- [ ] **D minus C** — A + B only
- [ ] **Other** — operator-specified scope

Implementation will not begin until this report is signed off. Branch
name will be `fix/i1-timestamp-fail-recv-window` per the plan's Rule 7.

After approval, Phase 3 ships the fix and Phase 4 runs the 6+ hour
soak verification.

## Cluster + Shadow notes

- Shadow's analogous path (`shadow_adapter.py:163`) has the same
  `if data is None: return []` semantic-loss pattern. Operator should
  decide whether the fix's architectural changes (Option C / D) apply
  symmetrically to Shadow. **Recommendation: yes** — the watchdog's
  "empty result = all closed" interpretation is the real shared
  architectural gap, regardless of the underlying transport.
- `get_wallet_balance` shares the pattern. If Option C / D ships,
  it should cover both endpoints in one branch (still one issue: I1).
