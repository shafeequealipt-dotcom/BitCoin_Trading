# IMPLEMENT — Price-Source Divergence Fix (Professional / Enterprise-Grade)

**Date drafted:** 2026-05-03
**Author of prompt:** Claude Code CLI (post-investigation handoff response)
**Target implementer:** Claude Code CLI on the project VM
**Project:** Trading Intelligence MCP (`/home/inshadaliqbal786/trading-intelligence-mcp`)
**Co-project:** Shadow (`/home/inshadaliqbal786/shadow`) — **read-only** for this fix
**Investigation source:** `dev_notes/price_source_divergence/FULL_BUNDLE.md` (and per-module files in that directory) plus `HANDOFF_PRICE_SOURCE_ISSUE.md`
**Verification basis:** every file:line citation in this document was re-verified against current code on 2026-05-03 before this prompt was written. Re-verify again at the start of each phase before editing.

---

## 0. Operator-Facing Preamble (Read First)

This prompt fixes the price-source divergence bug surfaced by the operator: Telegram dashboard shows different P&L numbers than Shadow shows for the same trade, on both unrealized (open) and realized (closed) P&L. Forensic collection in `dev_notes/price_source_divergence/` traced the root causes to three bugs and one architectural anti-pattern. This document is the implementation plan that fixes all three bugs as a hot-fix, followed by a one-shot data-recovery script for already-corrupted historical rows.

The implementer (you, Claude Code CLI) MUST read this entire document before making any code changes. The plan is split into atomic phases with a single git commit per phase. Each phase has its own pre-conditions, files to modify, exact change specification, test plan, and verification steps. Skipping ahead is forbidden by the project's hard rules (see Section 2).

The fix is intentionally surgical. It is NOT the full architectural rebuild; it is the minimum change set that makes operator-visible numbers correct and matches Shadow's authoritative records. The longer-term architectural cleanup (deletion of the enrichment helpers entirely, rerouting of the sentiment aggregator, etc.) is scheduled as a separate follow-up prompt — see Section 11.

---

## 1. Problem Summary (Verified On Current Code)

Three bugs and one design anti-pattern. Every file:line citation below was confirmed against the working tree on 2026-05-03.

### 1.1 Bug 1 — `PriceWorker` WS write silently fails

**File:** `src/workers/price_worker.py`
**Lines:** 215–220

```python
import asyncio
try:
    loop = asyncio.get_running_loop()
    loop.create_task(self.market_repo.save_ticker(ticker))
except RuntimeError:
    pass
```

**Why it fails:** `_handle_ticker_update` is invoked by `pybit.unified_trading.WebSocket` on a thread-pool thread. That thread has no asyncio event loop attached. `asyncio.get_running_loop()` always raises `RuntimeError` here. The bare `except RuntimeError: pass` swallows the exception with no log line and no metric. The `ticker_cache` SQLite table therefore never receives WS-driven writes; it only gets rows from the REST path inside `MarketService._fetch_ticker` for symbols touched by an order placement. Forensic capture: at 2026-05-02 11:30:27 UTC, `ticker_cache` held 8 rows, all 5+ hours stale (`dev_notes/price_source_divergence/W2_anomalies.md` anomaly A1).

**Downstream consumers of `ticker_cache`:**
- `src/core/transformer.py:_get_local_price` — reads `last_price` for enrichment (Bug 2 below).
- `src/intelligence/sentiment/aggregator.py:169-175` — reads `change_24h_pct`. Inherits the same staleness.
- (Anything else discovered by Phase 0 grep — see Section 4.0.)

### 1.2 Bug 2 — `Transformer` enrichment overwrites Shadow's authoritative data

**File:** `src/core/transformer.py`
**Lines:** `_get_local_price` 654–714, `_enrich_positions_with_local_prices` 716–841, `_enrich_balance_with_local_prices` 843–908.
**Wired through:** `_PositionProxy.get_positions` 982–986, `_PositionProxy.get_position` 988–992, `_AccountProxy.get_wallet_balance` 1039–1044.

When any caller in shadow mode does `position_service.get_positions()`, the proxy fetches from Shadow's HTTP API (correct, authoritative numbers), then `_enrich_positions_with_local_prices` runs:

- Reads `local_price` from the (broken) `ticker_cache` table.
- If `local_price` is non-`None` AND `|local − shadow| / shadow ≤ override_threshold` (default 0.5%, configurable via `[price].divergence_override_pct`): **mutates** `pos.mark_price = local_price` and **recomputes** `pos.unrealized_pnl` from the local price.
- If divergence > threshold: emits `PRICE_OVERRIDE` log and the `price_override` event-buffer event, leaves Shadow's value in place.

Net effect with current Bug 1 state: when `ticker_cache` happens to be fresh for one symbol (because a recent order REST-fetched it), the override fires for that one symbol on the next `/positions` call. Operator sees one number, then another, with no visible cause — **bursty divergence**.

The `_enrich_balance_with_local_prices` helper at `:843-908` does the same mutation on `AccountInfo.unrealized_pnl` and `AccountInfo.total_equity` for the balance path.

### 1.3 Bug 3 — Self-initiated close paths persist locally-computed `pnl_usd`

**Files and lines:**
- `src/workers/position_watchdog.py:996-1002` — `time_decay_p_win_low` close path. Calls `coordinator.on_trade_closed(pnl_usd=pos.unrealized_pnl, ...)` using the Transformer-overwritten value.
- `src/workers/profit_sniper.py:2410, 2493, 2664` — `mode4_p9` close paths. Same pattern with locally-computed `pnl_usd`.
- (Phase 1 must verify there are no additional self-close sites by grepping for `on_trade_closed(.*pnl_usd=`.)

**Existing partial fix (do NOT remove):** `src/workers/position_watchdog.py:2569-2578`. The watchdog's external-detection path (where Shadow has already closed the position via SL/TP and the watchdog notices on its next tick) prefers Shadow's `net_pnl_pct` / `net_pnl_usd` from `get_last_close`. This works correctly. The bug is that **self-initiated** closes (where main project's logic decides to close, calls `position_service.close_position`, then immediately notifies the coordinator) do not go through this same authoritative-reconciliation step.

**Forensic confirmation:** `dev_notes/price_source_divergence/T1_closed_trade_forensics.md` shows 5 of 8 most recent closes (rows 1, 2, 3, 5, 6) with `Δpnl_usd` in the $0.16–$0.24 range, all closed via `time_decay_p_win_low` or `mode4_p9` triggers. The 3 trades that match exactly (rows 4, 7, 8) closed via `manual` / `strategic_review` paths that do not recompute.

### 1.4 Anti-pattern — Two parallel WebSockets, an "enrichment" layer reconciling them

`PriceWorker` runs `pybit.unified_trading.WebSocket` in main project (PID `workers.py`). Shadow runs raw `websockets.client` in PID `shadow.py`. Two TCP connections to Bybit's WSS, two caches, ~200ms relative jitter. The Transformer's enrichment was an attempt to "use main's faster local data when in agreement" — but combined with Bug 1, it ends up producing the divergence symptom rather than smoothing it.

**This prompt does not delete the parallel-WebSocket architecture.** That is a longer architectural cleanup. This prompt makes the enrichment layer **observation-only** so it stops corrupting numbers, while preserving the divergence telemetry that the strategist's `PROMPT_DEFERRED` gate depends on (see Section 4.2.4).

### 1.5 By-design divergence (do NOT "fix")

The universal ±0.03% entry-price gap between `trade_intelligence.entry_price` (main) and `virtual_positions.entry_price` (Shadow) is exactly Shadow's configured `[exchange].slippage_pct`. Shadow stores the post-slippage `fill_price`; main records the pre-slippage `last_price`. This is not a bug. It does mean **joins on entry_price always fail**; quantity is the safe join key (matches exactly per T1 Pattern D). The Phase 5 backfill script must use quantity, not entry_price.

---

## 2. Hard Rules (From `CLAUDE.md` And Operator Memory)

These apply to every phase. Violations of any of them are reason to stop and re-plan.

1. **Root cause, not symptom.** No `try/except: pass` wrappers. No "log and ignore" without a clear reason that fits the failure mode. Fix the underlying cause.
2. **Investigation before implementation.** Read every file end-to-end before modifying it. Map all callers of every function you touch. The CLAUDE.md `thesis_mgr_early` precedent: a single deletion 60 lines from its only consumer broke a feature in production. Grep before cut.
3. **No assumptions.** Verify every belief by reading current code. Memory is point-in-time and may be stale. File:line citations in this document were correct on 2026-05-03; re-verify before each phase.
4. **Production-quality code.** Type hints on new public methods. Docstrings explaining *why*, not *what*. Structured logging via `get_logger()` and `ctx()`. Exception handling that fails LOUDLY when failure is unexpected; only swallows when there is a documented reason that fits the failure mode.
5. **Per-phase atomic commits.** Each phase below is one git commit. Commit message format: `fix(price-source/phase-N): <subject>` followed by body explaining what changed and why. No combining phases. No skipping commits.
6. **Measurement-driven verification.** Each phase has explicit before/after numbers to capture. "Looks better" is not verification.
7. **No band-aid fixes.** If a phase blocks because a deeper issue surfaces, stop and surface it; do not patch around it.
8. **Test velocity guardrail (per `feedback_test_velocity.md`):** ≤10 minutes on tests per phase, focused on production code/structure, not exhaustive coverage. Each phase has the specific test files to add/update.
9. **Execution pace guardrail (per `feedback_execution_pace.md`):** ≤10 minutes per phase/command-cluster. No retry loops. No 60KB exploratory outputs. Plan once, execute fast.
10. **Ask when unsure.** If during implementation a fact contradicts this prompt, stop and surface it before continuing.

---

## 3. Phase Plan At A Glance

| # | Phase | Files Modified | Atomic Commit Subject |
|---|---|---|---|
| 0 | Pre-conditions, baseline measurement | (read-only) | (no commit) |
| 1 | Fix Bug 3 — route self-initiated closes through `get_last_close` | `position_watchdog.py`, `profit_sniper.py`, possibly `trade_coordinator.py` | `fix(price-source/phase-1): use Shadow's net_pnl_usd for time_decay and mode4 closes` |
| 2 | Fix Bug 2 — make Transformer enrichment observation-only | `transformer.py` | `fix(price-source/phase-2): demote price enrichment to observation-only; preserve divergence gate` |
| 3 | Fix Bug 1 — remove the broken `loop.create_task` write | `price_worker.py` | `fix(price-source/phase-3): remove broken WS->ticker_cache write path in PriceWorker` |
| 4 | Migrate sentiment aggregator off `ticker_cache` (optional, can defer) | `aggregator.py`, possibly cross-DB attach setup | `fix(price-source/phase-4): read 24h change from Shadow ticker_snapshots` |
| 5 | Backfill `trade_intelligence` from Shadow `virtual_positions` (one-shot) | `scripts/backfill_trade_intelligence_from_shadow.py` (new), `dev_notes/price_source_divergence/backfill_report.md` (new) | `fix(price-source/phase-5): backfill trade_intelligence pnl from Shadow authoritative records` |
| 6 | Post-fix verification, sign-off note | `dev_notes/price_source_divergence/postfix_verification.md` (new) | `docs(price-source): post-fix verification report` |

Phases 1–3 are the critical path. Phases 4–5 can ship after a soak window if the operator wants. Phase 6 is the closure document.

---

## 4. Phase Specifications

Each phase has the following structure: pre-conditions → exact change spec → test plan → verification → rollback. Do not deviate.

---

### Phase 0 — Pre-conditions And Baseline Measurement

**Goal:** capture the current "wrong" numbers so the post-fix verification has something to compare against. No code changes in this phase.

#### 4.0.1 Pre-conditions checklist

Run each as a separate verification step. All must pass.

1. Working tree is clean (`git status` shows no uncommitted changes).
2. Current branch is `main` or a freshly-cut topic branch from `main`.
3. The trading-workers, trading-mcp-sse, and shadow systemd services are all running (`systemctl is-active trading-workers trading-mcp-sse shadow` all report `active`).
4. `trading.db` and `shadow.db` are both readable (smoke test: `sqlite3 data/trading.db 'SELECT count(*) FROM trade_intelligence'`).
5. The forensic bundle exists (`dev_notes/price_source_divergence/FULL_BUNDLE.md`).
6. The strategist's PROMPT_DEFERRED gate config exists (`grep divergence_block_prompt_pct config.toml`).

Record each check's output in a temporary scratch file `dev_notes/price_source_divergence/precondition_check_2026-05-03.md`. Do NOT commit this file as part of any later phase — it is for your own working notes during implementation.

#### 4.0.2 Grep for additional consumers (DO NOT skip — CLAUDE.md rule)

Before any phase modifies code, identify all consumers of the affected surfaces:

```bash
# Consumers of ticker_cache (Bug 1 downstream).
grep -rn "ticker_cache" src/ tests/ --include="*.py"

# Consumers of _enrich_*_with_local_prices (Bug 2).
grep -rn "_enrich_positions_with_local_prices\|_enrich_balance_with_local_prices" src/ tests/

# Consumers of _last_enrichment_max_divergence_pct (the gate that must be preserved).
grep -rn "_last_enrichment_max_divergence_pct" src/ tests/

# Self-initiated close sites (Bug 3 candidates).
grep -rn "on_trade_closed.*pnl_usd" src/workers/ src/core/

# Anything that imports from the Transformer enrichment path.
grep -rn "from src.core.transformer\|from src\.core\.transformer" src/ tests/

# Anything that reads ticker_cache directly (rather than via _get_local_price).
grep -rn "FROM ticker_cache\|UPDATE ticker_cache\|INSERT INTO ticker_cache" src/ tests/
```

Capture all hits. Cross-reference against the per-phase change specs below. If a consumer surfaces that this prompt does not anticipate, **stop and surface it before proceeding** — that is grounds to revise this prompt rather than push through.

#### 4.0.3 Capture baseline measurements

Capture these numbers and append them to `dev_notes/price_source_divergence/precondition_check_2026-05-03.md`. They are the "before" half of the before/after comparison required by Hard Rule 6.

**Measurement A — close-trade P&L divergence (Bug 3 fingerprint).**

```sql
-- Run against trading.db.
SELECT symbol, closed_at, pnl_usd AS main_pnl, position_size_usd
FROM trade_intelligence
ORDER BY closed_at DESC LIMIT 20;
```

```sql
-- Run against shadow/data/shadow.db.
SELECT symbol, closed_at, net_pnl_usd AS shadow_net_pnl, notional_value, close_trigger
FROM virtual_positions
WHERE status = 'closed'
ORDER BY closed_at DESC LIMIT 20;
```

Join on `(symbol, qty, closed_at within ±90s)` per T1 Pattern D. Compute `Δ = main_pnl − shadow_net_pnl` per row. Record the 20-row table; record sum of |Δ|; record count of rows where |Δ| > $0.05.

**Measurement B — `ticker_cache` staleness (Bug 1 fingerprint).**

```sql
SELECT symbol, last_price,
       (julianday('now') - julianday(updated_at)) * 86400 AS age_seconds
FROM ticker_cache ORDER BY age_seconds DESC LIMIT 20;
```

Record the row count, the median age, and the count of rows newer than 60s.

**Measurement C — `PRICE_OVERRIDE` log emission rate (Bug 2 fingerprint).**

```bash
# Tail the last 1 hour of workers.log; grep for PRICE_OVERRIDE; count by symbol.
journalctl -u trading-workers --since "1 hour ago" 2>/dev/null \
  || tail -n 100000 data/logs/workers.log | grep PRICE_OVERRIDE
```

(Use whichever log surface is populated on this host.) Record total count and per-symbol count.

**Measurement D — strategist `PROMPT_DEFERRED` rate (gate health).**

```bash
grep PROMPT_DEFERRED data/logs/brain.log | tail -50
```

Record the count over the last 24 hours and whether `rsn=price_divergence` is the dominant defer reason. This number must NOT meaningfully increase after Phase 2 (it should slightly decrease, because the override no longer mutates the divergence calculation; the divergence math itself is preserved).

#### 4.0.4 Phase 0 exit criteria

- All pre-conditions pass.
- Baseline measurements recorded.
- No surprises from the consumer grep that contradict this prompt.

If any of the above fails: stop, surface to operator, do not start Phase 1.

---

### Phase 1 — Fix Bug 3 (Close-Path P&L Divergence)

**Goal:** every self-initiated close path persists Shadow's authoritative `net_pnl_usd` and `net_pnl_pct`, not a locally-recomputed value. The watchdog's external-detection path already does this — extend the same pattern to time_decay and mode4_p9.

#### 4.1.1 Files to modify

- `src/workers/position_watchdog.py` (primary)
- `src/workers/profit_sniper.py` (primary)
- `src/core/trade_coordinator.py` (read-only confirmation; no edits expected)

Read each file end-to-end before editing. Especially read:

- `position_watchdog.py:960-1023` (the `time_decay` self-close block)
- `position_watchdog.py:2480-2620` (the existing external-detection-path fix — your model for the pattern)
- `profit_sniper.py:2390-2520` and `:2640-2700` (the mode4 self-close blocks)
- `core/trade_coordinator.py:on_trade_closed` (confirm it accepts and persists `pnl_usd` verbatim, doesn't re-mutate)

#### 4.1.2 Change specification

Introduce a single helper, ideally on `position_watchdog` and reused or duplicated in `profit_sniper` (decide based on import topology — do NOT introduce a new shared module unless one is genuinely justified). The helper takes `(symbol, position_service, fallback_pnl_usd, fallback_pnl_pct)` and returns `(pnl_usd, pnl_pct, price_source)`. Behavior:

1. After the `await position_service.close_position(symbol)` call returns, call `await position_service.get_last_close(symbol)`.
2. If the result is a dict with non-`None` `net_pnl_usd` and `net_pnl_pct`: return `(float(net_pnl_usd), float(net_pnl_pct), "shadow_authoritative")`.
3. Otherwise: return `(fallback_pnl_usd, fallback_pnl_pct, "fallback_local")` and emit a structured warning `WD_LAST_CLOSE_FALLBACK | sym=... reason=<no_data|missing_field|exception>` so the fallback is observable.

Then, at each self-close site (`position_watchdog.py:996-1002`, `profit_sniper.py:2410, 2493, 2664`):

1. Record `_local_pnl_usd = pos.unrealized_pnl` and `_local_pnl_pct = pnl_pct` from the existing local computation. These are the fallbacks.
2. Call the helper to resolve the authoritative numbers.
3. Pass the helper's `pnl_usd` and `pnl_pct` to `coordinator.on_trade_closed`. Pass `price_source` through if `on_trade_closed` accepts it (it does, per `position_watchdog.py:2609`).
4. Add a `WD_CLOSE` / `SNIPER_CLOSE` log line that records `local_pnl=...$ shadow_pnl=...$ price_src=shadow_authoritative|fallback_local`. Match the format already used at `position_watchdog.py:2587-2592`.

**Do NOT** silently bypass the existing local computation — keep it as the fallback. **Do NOT** call `get_last_close` unconditionally in any other code path; it is a Shadow-only method (per `_PositionProxy.get_last_close` at `transformer.py:1020-1030`, returns `None` for Bybit). Ensure the helper handles the `None` return from non-shadow modes correctly by falling back to the local value.

**Mode awareness:** `get_last_close` is only meaningful in shadow mode. In Bybit mode, `_PositionProxy.get_last_close` returns `None` and the helper falls back to the local computation. This is already correct — the live Bybit path produces accurate `pnl_usd` from the authoritative Bybit fill response (no slippage simulation gap). Document this in the helper's docstring so a future reader does not "fix" it.

#### 4.1.3 Test plan (≤10 minutes)

Add or extend tests in `tests/test_watchdog/` and `tests/test_profit_sniper_*.py` (use the existing structure). Three test cases per close site:

1. **Happy path:** `position_service.get_last_close` returns a dict with `net_pnl_usd=-0.5232, net_pnl_pct=-0.18`. Helper returns those values with `price_source="shadow_authoritative"`. Coordinator is called with the Shadow values, NOT the locally-computed values.
2. **Fallback path — empty dict:** `get_last_close` returns `None`. Helper returns the local fallback with `price_source="fallback_local"`. `WD_LAST_CLOSE_FALLBACK` warning is emitted.
3. **Fallback path — exception:** `get_last_close` raises. Helper logs and falls back. No exception propagates to caller.

Use `unittest.mock.AsyncMock` for the position_service.

#### 4.1.4 Verification

After deploying Phase 1 to live workers (operator's call when):

- For the next close that triggers `time_decay_p_win_low` or `mode4_p9`, log line shows `price_src=shadow_authoritative` and `pnl_usd` matches Shadow's `virtual_positions.net_pnl_usd` exactly (no $0.16–$0.24 gap).
- The next 5 such closes consecutively show the same. Record in `postfix_verification.md` (Phase 6).
- `WD_LAST_CLOSE_FALLBACK` count over a 24h window is 0 (or, if non-zero, every occurrence has a clear reason logged).

#### 4.1.5 Rollback

`git revert` the Phase 1 commit. The fallback path inside the helper means even with the helper present, behavior degrades gracefully if Shadow is unreachable — but the cleanest rollback is the revert. No DB schema changes in this phase, so revert is safe.

#### 4.1.6 Phase 1 commit message template

```
fix(price-source/phase-1): use Shadow's net_pnl_usd for time_decay and mode4 closes

Self-initiated close paths in position_watchdog (time_decay_p_win_low) and
profit_sniper (mode4_p9) were persisting locally-computed pnl_usd from the
Transformer-overwritten pos.unrealized_pnl, producing $0.16–$0.24 gaps vs
Shadow's virtual_positions.net_pnl_usd on every such close (T1 forensics
rows 1, 2, 3, 5, 6).

This commit introduces a small helper that, after close_position succeeds,
calls position_service.get_last_close(symbol) and persists Shadow's
authoritative net_pnl_usd / net_pnl_pct. Local computation is retained as
the fallback when Shadow returns None (e.g. Bybit mode, where get_last_close
is a no-op). Existing external-detection-path fix at position_watchdog.py:
2569-2578 is left intact.

No DB schema changes. Tests added in tests/test_watchdog/ and
tests/test_profit_sniper_*.py.
```

---

### Phase 2 — Fix Bug 2 (Demote Transformer Enrichment To Observation-Only)

**Goal:** stop mutating `pos.mark_price`, `pos.unrealized_pnl`, `balance.unrealized_pnl`, and `balance.total_equity`. Preserve all the divergence calculation, the `PRICE_OVERRIDE` log line, the event-buffer write, and the `_last_enrichment_max_divergence_pct` field. The strategist's PROMPT_DEFERRED gate at `src/brain/strategist.py:280-298, 500-523` consumes that field; it must keep working.

#### 4.2.1 Files to modify

- `src/core/transformer.py` (only)

Read end-to-end before editing. Specifically:

- `Transformer.__init__` lines 35–56 (the field declarations including `_last_enrichment_max_divergence_pct`).
- `_get_local_price` lines 654–714 (do not change behavior; this is now a pure observation primitive).
- `_enrich_positions_with_local_prices` lines 716–841 (the main mutation site).
- `_enrich_balance_with_local_prices` lines 843–908 (the balance mutation site).
- `_PositionProxy.get_positions` and `get_position` lines 982–992 (callers of the position helper).
- `_AccountProxy.get_wallet_balance` lines 1039–1044 (caller of the balance helper).

#### 4.2.2 Change specification — `_enrich_positions_with_local_prices`

Rename the method to `_observe_positions_local_divergence` (semantic rename — the name should match what it now does). Update both `_PositionProxy.get_positions` and `_PositionProxy.get_position` to call the renamed method.

Inside the method, **delete** the four lines that mutate position state:

- `pos.mark_price = local_price` (current line ~797)
- The `pnl_pct` calculation block at current lines ~800–814
- `pos.unrealized_pnl = pnl_pct / 100 * notional` (current line ~816)

Keep:

- The full divergence calculation (`shadow_price`, `diff_pct`, `abs_div`).
- The `_last_enrichment_max_divergence_pct` running max update (current lines ~762–764). **This field is consumed by the strategist's `_has_blocking_price_divergence` gate at `strategist.py:280-298`. It MUST keep updating identically to before.**
- The threshold check, the `PRICE_OVERRIDE` log line, and the event-buffer write (current lines ~771–790). **Rename** the log tag to `PRICE_DIVERGENCE_OBS` to make it clear in logs that this is observation-only and no override is being applied. Update the event-buffer event name from `price_override` to `price_divergence_obs` for the same reason.
- The summary `Position enrichment: ... shadow_override` log at the bottom — rename to `Position enrichment OBS: ... above_threshold` to reflect the new semantics. Counts should now be `total / observed_in_tolerance / above_threshold / fallback`.

The renamed method should return `None` (it never returned anything anyway). The proxy methods continue to return Shadow's positions verbatim.

Add a one-paragraph docstring to the renamed method explaining that this is observation-only post the 2026-05-03 fix, that the divergence calculation is retained because the strategist's PROMPT_DEFERRED gate depends on `_last_enrichment_max_divergence_pct`, and that any future cleanup that removes this method must also rewire that gate.

#### 4.2.3 Change specification — `_enrich_balance_with_local_prices`

Rename to `_observe_balance_local_divergence`. Update `_AccountProxy.get_wallet_balance` to call the renamed method. **Delete** the four lines that mutate balance state:

- `balance.unrealized_pnl = local_unrealized` (current line ~885)
- `balance.total_equity = balance.total_equity - old_unrealized + local_unrealized` (current lines ~886–888)
- `balance.available_balance = balance.total_equity - balance.used_margin` (current lines ~889–891)

Keep the `local_unrealized` calculation and emit a log line `BALANCE_DIVERGENCE_OBS | shadow_unrealized=$.. local_unrealized=$.. diff=$..` when the absolute diff exceeds `0.01`. Do NOT mutate `balance`.

Return `balance` unchanged (the method already returns balance; preserve that signature so the proxy call site does not need to change).

#### 4.2.4 Strategist gate verification (CRITICAL — do not skip)

The strategist's PROMPT_DEFERRED gate at `src/brain/strategist.py:507-523` reads `tf._last_enrichment_max_divergence_pct` and compares it to `settings.price.divergence_block_prompt_pct` (default 1.0%). After Phase 2:

- The field MUST still update on every `get_positions` call.
- The field MUST be reset to 0.0 at the top of every observation pass (already done at current line 746).
- The numerical value MUST be identical to pre-fix for the same set of positions and the same `ticker_cache` state.

Add a unit test in `tests/test_transformer_enrichment_observation.py` that constructs a `Transformer`, mocks the position service to return positions with various Shadow vs local price gaps, calls the renamed observation method, and asserts:

1. `_last_enrichment_max_divergence_pct` is set correctly.
2. `pos.mark_price` is NOT mutated.
3. `pos.unrealized_pnl` is NOT mutated.
4. `PRICE_DIVERGENCE_OBS` log line is emitted at the right threshold.
5. The event-buffer is called with the renamed event name.

#### 4.2.5 Test plan (≤10 minutes)

- New: `tests/test_transformer_enrichment_observation.py` with the assertions above (4 cases: in-tolerance, above-threshold, no local price, balance path).
- Update: any existing test file that asserts `pos.mark_price` was mutated (run `grep -rn "mark_price.*=.*local_price\|local_price.*mark_price" tests/` to find them) — they must be updated to assert the new observation-only behavior.

#### 4.2.6 Verification

- Unit tests pass.
- Restart workers; trigger a `/positions` call from Telegram; verify the rendered numbers exactly match Shadow's `/api/positions` response (use `curl http://127.0.0.1:9090/api/positions | jq` to compare).
- `journalctl -u trading-workers --since "10 minutes ago" | grep PRICE_DIVERGENCE_OBS` shows the renamed log lines with correct shape.
- The `PROMPT_DEFERRED` count over 24h after deploy is in the same ballpark as the Phase 0 baseline (Measurement D). It should not spike.

#### 4.2.7 Rollback

`git revert` the Phase 2 commit. No schema changes. Risk: if a downstream consumer was secretly depending on the mutation (e.g., reading `pos.mark_price` after `get_positions` and expecting the local price), they will now see Shadow's price. This is the *correct* behavior, but if Phase 0's grep missed a consumer, expect a discovered-after-the-fact bug. The grep in Phase 0 should have caught all of them; if one slipped through, the fix is to update that consumer to read from the divergence telemetry, not to revert this phase.

#### 4.2.8 Phase 2 commit message template

```
fix(price-source/phase-2): demote price enrichment to observation-only; preserve divergence gate

Per the price-source-divergence forensic bundle (W2 anomaly A1+A3): the
Transformer's _enrich_positions_with_local_prices and _enrich_balance_with
_local_prices were mutating Shadow's authoritative mark_price and
unrealized_pnl whenever local price agreed with Shadow within
divergence_override_pct (default 0.5%). With ticker_cache silently 5h+
stale (Bug 1), the override produced bursty per-symbol divergence in
the operator dashboard.

This commit renames both helpers to _observe_*_local_divergence, deletes
the four mutation lines, and preserves all divergence telemetry —
including _last_enrichment_max_divergence_pct, the field consumed by
the strategist's PROMPT_DEFERRED gate at strategist.py:507-523. Log tags
renamed PRICE_DIVERGENCE_OBS (was PRICE_OVERRIDE) and event-buffer event
renamed price_divergence_obs (was price_override) to reflect the new
semantics in observability.

No DB schema changes. New unit test in tests/test_transformer_enrichment
_observation.py covers the four observation cases.
```

---

### Phase 3 — Fix Bug 1 (Remove The Broken `loop.create_task` Write)

**Goal:** the WS callback's structurally-impossible asyncio call is removed. The in-memory `_ws_quotes` cache continues to update correctly. `ticker_cache` becomes a REST-only table for the symbols that REST has touched. (Phase 4 then migrates the only remaining `ticker_cache` consumer that needs cross-symbol freshness — the sentiment aggregator — onto Shadow's `ticker_snapshots`, after which `ticker_cache` can be deprecated entirely in a follow-up.)

#### 4.3.1 Files to modify

- `src/workers/price_worker.py` (only)

Read end-to-end before editing. Specifically:

- `_handle_ticker_update` lines 161–237 (the WS callback).
- `__init__` lines 43–73 (verify no event-loop-reference field exists; if it does, that may be a previous attempted fix to revisit).

#### 4.3.2 Change specification

**Delete** lines 215–220 — the entire `import asyncio` / `try` / `except RuntimeError: pass` block.

Add a one-line code comment immediately after the dropped block:

```python
# Phase X (price-source fix 2026-05-03): the previous loop.create_task
# write to ticker_cache always raised RuntimeError on pybit's thread-pool
# callback (no asyncio loop attached) and was silently swallowed. The
# in-memory _ws_quotes cache above is the authoritative WS source for
# decision-time prices (APEX, scanner). DB persistence of WS ticks is no
# longer attempted from this callback. ticker_cache continues to be
# populated by MarketService._fetch_ticker on the REST path for symbols
# that workers explicitly query.
```

(Replace `Phase X` with the actual phase number when you commit. Use `Phase 33` if your project numbering convention requires a global phase number — check git log for recent phase numbers.)

The `_dropped_count` / `PRICE_WS_TICK_FAIL` exception handler at lines 223–237 stays as-is. It is unrelated to the deleted block and catches a different class of error (parse errors on the WS payload itself).

#### 4.3.3 What this phase does NOT do

This phase deliberately does NOT:

- Replace the deleted block with a `run_coroutine_threadsafe` write. Phase 4 migrates the remaining `ticker_cache` consumer (the sentiment aggregator) onto Shadow's `ticker_snapshots` instead, which is a more reliable feed and avoids needing main project's WS-to-DB bridge at all.
- Drop the `ticker_cache` table. Other code may still write to it on the REST path; deprecating the table is a separate cleanup.
- Touch the in-memory `_ws_quotes` cache. That works correctly and is the WS-driven source of truth for decision-time prices via `get_ws_quote` at lines 239–257.

#### 4.3.4 Test plan (≤5 minutes)

A small unit test that asserts the callback no longer attempts an asyncio call. Specifically: invoke `_handle_ticker_update` from a thread that has no event loop (use `threading.Thread`), verify it does not raise and that `_ws_quotes[symbol]` is updated. Add to `tests/test_price_worker_ws_callback.py` (new file) — keep it under 50 lines.

#### 4.3.5 Verification

After deploying:

- `data/logs/workers.log` shows no `RuntimeError` traces from PriceWorker (there shouldn't have been any before either since they were swallowed, but absence here is structural now).
- `_ws_quotes` continues to populate. APEX assembler at `src/apex/assembler.py:147-148` continues to read fresh WS quotes via `get_ws_quote(sym, max_age_s=5.0)`. The `PRICE_WS_HEALTH` heartbeat at `price_worker.py:149-157` continues to report `quotes_cached=N` with N close to subscribed-symbol count.
- `ticker_cache` row count over the next hour does NOT decrease (it just stops getting WS-driven updates; REST writes continue). Confirm via Measurement B repeated after deploy.

#### 4.3.6 Rollback

`git revert` the Phase 3 commit. The deleted block was a no-op in practice (every call raised and was swallowed), so rollback restores no functionality and breaks no functionality.

#### 4.3.7 Phase 3 commit message template

```
fix(price-source/phase-3): remove broken WS->ticker_cache write path in PriceWorker

The loop.create_task(market_repo.save_ticker(...)) at price_worker.py:
215-220 ran inside pybit's WebSocket callback thread, which has no
asyncio event loop attached. asyncio.get_running_loop() always raised
RuntimeError, which was silently swallowed by `except RuntimeError:
pass`. The DB write therefore never happened. ticker_cache was 5h+
stale at forensic capture (W2 anomaly A1).

This commit deletes the unreachable block. The in-memory _ws_quotes
cache (the actual WS-driven source of truth for decision-time prices
in APEX / scanner) continues to populate on every tick. ticker_cache
remains populated by MarketService._fetch_ticker on the REST path for
symbols that workers explicitly query — Phase 4 will migrate the
remaining cross-symbol consumer (sentiment aggregator) off ticker_cache
and onto Shadow's ticker_snapshots.

No DB schema changes. New test tests/test_price_worker_ws_callback.py
asserts the callback updates _ws_quotes from a non-asyncio thread.
```

---

### Phase 4 — Migrate Sentiment Aggregator Off `ticker_cache` (Optional / Schedulable)

**Goal:** the only remaining cross-symbol consumer of `ticker_cache.change_24h_pct` is moved to Shadow's `ticker_snapshots` (which is actually fresh, written every 60s by Shadow's `TickerCollector`). After this phase, `ticker_cache` is unused for live decisions and can be retired in a separate follow-up.

This phase is schedulable: it can ship 24–72 hours after Phase 3 with a soak window in between. If the operator opts to defer, document the deferral in `postfix_verification.md` (Phase 6) so it is not forgotten.

#### 4.4.1 Files to modify

- `src/intelligence/sentiment/aggregator.py` (lines 169–175 per V1 matrix; verify exact lines).
- `src/database/connection.py` or wherever cross-DB attach is configured (verify whether shadow.db is attached today; if not, set it up).

Read end-to-end before editing.

#### 4.4.2 Change specification

Two viable approaches; pick based on what the codebase already does:

**Approach A — direct query of `shadow.db`:** the aggregator opens a separate read-only connection to `/home/inshadaliqbal786/shadow/data/shadow.db` (the path is fixed; document this assumption) and queries `SELECT symbol, price_change_24h_pct FROM ticker_snapshots WHERE timestamp > ? ORDER BY symbol, timestamp DESC` for the most recent snapshot per symbol. The query selects `price_change_24h_pct` (Shadow column name) instead of `change_24h_pct` (main column name).

**Approach B — cross-DB attach:** main project's `DatabaseManager` attaches `shadow.db` as a second schema (`ATTACH DATABASE '/home/inshadaliqbal786/shadow/data/shadow.db' AS shadow`). Aggregator queries `shadow.ticker_snapshots`. Centralized, but requires attach-on-connect plumbing.

Recommended: **Approach A**. It is fully encapsulated in the aggregator, requires no DatabaseManager changes, and the read-only connection is short-lived per call (open, query, close). The performance cost is one extra SQLite open per aggregator tick; acceptable.

Update the aggregator method that reads `change_24h_pct` to use the new Shadow source. Add a fallback to the original `ticker_cache` query if Shadow's DB is unreachable, with a `SENTIMENT_PRICE_FALLBACK` warning log so the fallback is observable. (This is one of the few places where a fallback is justified — Shadow's DB may be temporarily locked during its own writes; the existing `ticker_cache` is a graceful degradation.)

#### 4.4.3 Test plan (≤10 minutes)

- Unit test against an in-memory or temp-file SQLite that mimics `shadow.db.ticker_snapshots` structure.
- Test the fallback path when the Shadow DB path doesn't exist.

#### 4.4.4 Verification

- Sentiment aggregator's per-tick log line shows it read from `shadow.ticker_snapshots` not `ticker_cache`.
- The `change_24h_pct` values are bounded and match Shadow's own per-symbol values within sub-second precision.

#### 4.4.5 Rollback

`git revert`. No schema changes.

#### 4.4.6 Phase 4 commit message template

```
fix(price-source/phase-4): read 24h change from Shadow ticker_snapshots

After phases 1-3 the only remaining consumer of ticker_cache for live
decisions was the sentiment aggregator's change_24h_pct read at
aggregator.py:169-175. ticker_cache is now REST-only and stale for the
50-coin universe; Shadow's ticker_snapshots is written every 60s by
Shadow's TickerCollector and is fresh for all subscribed symbols.

This commit migrates the aggregator to read from
/home/inshadaliqbal786/shadow/data/shadow.db.ticker_snapshots via a
short-lived read-only connection. ticker_cache fallback retained for
the case where Shadow's DB is unreachable; SENTIMENT_PRICE_FALLBACK
warning is emitted so the fallback is observable.
```

---

### Phase 5 — Backfill `trade_intelligence` From Shadow Authoritative Records

**Goal:** rebuild `trade_intelligence.pnl_usd` and `pnl_pct` for already-closed trades using Shadow's `virtual_positions.net_pnl_usd` and `net_pnl_pct`. After this phase, TIAS, `/history`, and the daily P&L aggregations are consistent with Shadow's lifetime ledger.

#### 4.5.1 Pre-conditions

- Phase 1 has been live for at least 24 hours and the verification report shows new closes are persisting Shadow's number correctly. (We don't want to backfill while the bug is still creating new corrupt rows.)
- A backup of `trading.db` has been taken: `cp data/trading.db data/trading.db.pre-phase5.bak`. Document the backup path in the commit body.

#### 4.5.2 Implementation

A new file: `scripts/backfill_trade_intelligence_from_shadow.py`. Standalone script (not a worker). Reads both DBs, joins on `(symbol, qty)` per T1 Pattern D (NOT entry_price — that has the by-design ±0.03% slippage gap), updates `trade_intelligence.pnl_usd` and `pnl_pct`. Idempotent — re-runs are safe and a second run finds zero rows to update.

Required behaviors:

1. **Dry-run by default.** First execution prints the proposed diff per row (symbol, closed_at, old_pnl_usd, new_pnl_usd, Δ) and writes a report file `dev_notes/price_source_divergence/backfill_report.md`. Operator confirms before applying.
2. **Apply mode** (`--apply` flag). Wraps the UPDATEs in a single transaction. On error, rolls back. Re-runs the same diff after applying and asserts zero remaining diffs.
3. **Provenance:** add a column to `trade_intelligence` if not already present: `pnl_source TEXT DEFAULT 'main_local'`. After backfill, set `pnl_source = 'shadow_authoritative_backfill_2026-05-03'` for updated rows. Migration is required — add it as the first SQL statement in the script and gate the rest on its success.
4. **Excluded rows:** rows where `Δ < $0.05` are left alone (those are the manual / strategic_review closes that already match Shadow). Log them in the report but do not update.
5. **Unmatched rows:** rows in `trade_intelligence` with no Shadow counterpart on `(symbol, qty)` get logged as `unmatched`. These need operator attention — likely orphan or pre-Shadow rows. Do not touch them.

#### 4.5.3 Test plan (≤10 minutes)

- Manual dry-run on the live DB pair (read-only).
- Spot-check 5 rows from the dry-run report by hand against the corresponding `virtual_positions` rows.
- Apply on a copied DB pair (`trading.db.test = trading.db`, `shadow.db.test = shadow.db`); confirm second dry-run shows zero diffs.

#### 4.5.4 Verification

- Re-run Measurement A from Phase 0. Sum of |Δ| should be near zero (only rows with Δ < $0.05 remain).
- Sum of `trade_intelligence.pnl_usd` over the lifetime now matches Shadow's `virtual_wallet.total_realized_pnl − total_fees_paid` within a small reconciliation epsilon. Document the epsilon in the report.
- TIAS lifetime aggregations re-run and produce updated lessons-learned. Compare top-10 strategies before/after; document any rank changes.

#### 4.5.5 Rollback

Restore `trading.db.pre-phase5.bak`. The backup MUST exist before applying. The script's safety net is not the only safety net.

#### 4.5.6 Phase 5 commit message template

```
fix(price-source/phase-5): backfill trade_intelligence pnl from Shadow authoritative records

Closed trades persisted before phase 1 shipped have main-side pnl_usd
that diverges from Shadow's virtual_positions.net_pnl_usd by $0.16-$0.24
per trade for the time_decay_p_win_low / mode4_p9 close paths (T1
Pattern B). TIAS, /history, and lifetime P&L aggregations have inherited
this bias.

This commit adds scripts/backfill_trade_intelligence_from_shadow.py — a
dry-run-by-default, idempotent, transactional script that rebuilds
trade_intelligence.pnl_usd and pnl_pct from virtual_positions.net_pnl
_usd / net_pnl_pct using (symbol, qty) as the join key (entry_price is
not safe to join on per T1 Pattern A). Adds pnl_source column to
trade_intelligence (migration in the script) so backfilled rows are
distinguishable from natively-recorded ones.

Backup taken at data/trading.db.pre-phase5.bak before apply. Backfill
report at dev_notes/price_source_divergence/backfill_report.md.
```

---

### Phase 6 — Post-Fix Verification And Sign-Off

**Goal:** a single document captures before/after numbers and confirms operator-visible numbers are now correct. This is the closure deliverable.

#### 4.6.1 File to create

`dev_notes/price_source_divergence/postfix_verification.md`. Sections:

1. Phase-by-phase commit hashes.
2. Phase 0 baseline measurements (copied from precondition_check_2026-05-03.md).
3. Post-phase-3 measurements (Measurement A, B, C, D repeated).
4. Post-phase-5 measurements (Measurement A again — should now show zero divergence on closed trades).
5. Live cross-source comparison: side-by-side `/positions` Telegram render and `curl shadow:9090/api/positions`. Numbers must match to 2 decimal places.
6. Operator sign-off line.

#### 4.6.2 Phase 6 commit message template

```
docs(price-source): post-fix verification report

Captures before/after measurements per Phase 0's measurement plan.
Documents commit hashes for phases 1-5 and confirms operator-visible
numbers now match Shadow's authoritative records to 2 decimal places
on /positions, /performance, and /history.
```

---

## 5. Specific Things The Implementer MUST Re-Verify Before Phase 1

The investigation that produced this prompt was done on 2026-05-03. Code may shift between then and the time of implementation. Re-confirm each:

1. `src/workers/price_worker.py:215-220` still contains the `try / loop.create_task / except RuntimeError: pass` block.
2. `src/core/transformer.py:716-841` still contains `_enrich_positions_with_local_prices` with the mutation lines `pos.mark_price = local_price` and the `pnl_pct` recompute.
3. `src/core/transformer.py:843-908` still contains `_enrich_balance_with_local_prices` with the `balance.unrealized_pnl = local_unrealized` mutation.
4. `src/workers/position_watchdog.py:996-1002` still calls `coordinator.on_trade_closed(pnl_usd=pos.unrealized_pnl, closed_by="time_decay_p_win_low")`.
5. `src/workers/profit_sniper.py` self-close sites at `:2410, 2493, 2664` still pass `pos.unrealized_pnl` (or equivalent local computation) to `on_trade_closed`.
6. `src/brain/strategist.py:280-298, 500-523` still consumes `tf._last_enrichment_max_divergence_pct` for the PROMPT_DEFERRED gate.
7. `src/shadow/shadow_adapter.py:192-225` still exposes `ShadowPositionService.get_last_close` and the `_PositionProxy.get_last_close` at `transformer.py:1020-1030` still forwards it.
8. `config.toml:[price]` block still contains `divergence_block_prompt_pct = 1.0` and `divergence_override_pct = 0.5`.

If any one of these has shifted: stop, re-read the new state of the file, and update this prompt before proceeding.

---

## 6. Out-Of-Scope For This Prompt (Explicit)

These are intentionally NOT in this fix. They will be separate prompts.

1. **Deleting the parallel WebSocket architecture.** The two-WS topology stays. Only the destructive enrichment layer is neutered.
2. **Migrating APEX assembler off main's WS.** APEX continues to use `_ws_quotes` for decision-time prices via `get_ws_quote`. This works correctly.
3. **Deprecating the `ticker_cache` table.** After Phase 4 it has no live consumers, but the table itself stays (its REST-path writes are harmless). A separate cleanup can drop the table after a soak window.
4. **The strategist's PROMPT_DEFERRED gate semantics.** The gate stays as-is. After Phase 2, `_last_enrichment_max_divergence_pct` continues to update. If the operator wants to tune `divergence_block_prompt_pct` based on post-fix telemetry, that is a config-only change handled elsewhere.
5. **Bybit graduation readiness.** A separate investigation analogous to this one is needed before going live with real money. See HANDOFF section 4 in the prior conversation. Do not conflate that with this fix.
6. **MCP server changes.** The MCP server (port 8080) has no awareness of Shadow or the Transformer. It is not in any of the affected paths and requires no changes for this fix.

---

## 7. Risk Register

For each risk, the mitigation is documented.

| # | Risk | Mitigation |
|---|---|---|
| R1 | Phase 2 silently breaks a downstream consumer that secretly depended on the post-enrichment `pos.mark_price` value | Phase 0 grep for all callers of `_PositionProxy.get_positions` and any code that reads `pos.mark_price` after a `get_positions` call. Cross-reference against the change spec. |
| R2 | Phase 2 breaks the strategist's PROMPT_DEFERRED gate | The change spec preserves `_last_enrichment_max_divergence_pct` byte-for-byte. The Phase 2 unit test asserts the field updates. Phase 0 captures the baseline `PROMPT_DEFERRED` rate; Phase 6 verifies it has not regressed. |
| R3 | Phase 1 helper `get_last_close` race — Shadow has not yet persisted the close | The helper has the local fallback; if `get_last_close` returns a stale or empty result, the local value is used and a warning is logged. The watchdog's existing fix at `position_watchdog.py:2569-2578` has been operating with this race for a while; we are extending the same pattern, not introducing a new one. |
| R4 | Phase 3 breaks an unknown consumer that relied on `ticker_cache` being WS-fresh | Phase 0 grep for all `FROM ticker_cache` should have caught them. The known consumer (sentiment aggregator) is migrated in Phase 4. Other consumers, if any, surface as Phase 0 grep hits and need a per-consumer decision. |
| R5 | Phase 5 backfill misjoins a row | `(symbol, qty)` join is per T1 Pattern D (verified exact match across 8 sample trades). Dry-run report is operator-reviewed before apply. Backup taken before apply. |
| R6 | A consumer reads `tf._last_enrichment_max_divergence_pct` and expects the previous "override-applied" semantics | The semantics of the field are unchanged: it is the max |divergence| seen during the last observation pass. The only thing that changed is whether the override was applied. Update the field's docstring to clarify post-fix semantics. |
| R7 | The renamed log tag `PRICE_DIVERGENCE_OBS` (was `PRICE_OVERRIDE`) breaks an external monitoring rule or dashboard | If any Grafana / Loki / log-monitor rule grepps for `PRICE_OVERRIDE`, the rename will silently break it. Phase 0 should grep for `PRICE_OVERRIDE` outside the codebase too (in dashboards / alerts repo). If that exists, update those rules in lockstep. |

---

## 8. What Success Looks Like (Operator-Facing)

After all six phases ship and Phase 6 is signed off:

1. Telegram `/positions` numbers match Shadow's `/api/positions` response to 2 decimal places. No bursty divergence.
2. Telegram `/performance` and `/history` agree with each other on lifetime realized P&L.
3. New time_decay / mode4 closes log `price_src=shadow_authoritative` and persist Shadow's `net_pnl_usd` exactly.
4. `journalctl -u trading-workers | grep PRICE_DIVERGENCE_OBS` continues to surface divergence telemetry — observability is unchanged.
5. The strategist's PROMPT_DEFERRED rate is in the same ballpark as pre-fix (the gate continues to function on real divergence; the fix doesn't blind it).
6. `data/trading.db trade_intelligence` rows have been backfilled where applicable; new rows are tagged `pnl_source = 'main_authoritative'` going forward.

---

## 9. What Success Does NOT Mean

For honesty: the items below are NOT addressed by this fix and will surface as separate work.

1. The two-WebSocket architecture still exists. Future architectural cleanup may consolidate it.
2. Bybit graduation readiness has not been audited. Do not flip the Transformer to `bybit` mode based on the success of this fix alone.
3. The `entry_price` ±0.03% slippage gap between main and Shadow is by design and will continue to exist. Reporting that aggregates by `entry_price` will continue to misjoin; use `quantity` as the join key.
4. Shadow's `OrderEngine.get_positions` `else row["entry_price"]` fallback at `shadow/src/exchange/order_engine.py:670` (W2 anomaly A4) — Shadow shows P&L = 0 instead of "no live data" during WS outages. Separate fix in Shadow's repo.
5. `trade_intelligence.position_size_usd` dual semantics (margin vs notional, T1 Pattern C) are not addressed. Cosmetic, low priority.

---

## 10. Estimated Effort

Phase-by-phase, assuming the implementer follows hard rule 9 (≤10 minutes per phase/command-cluster):

- Phase 0 (pre-conditions, baseline): ~30 minutes (read-heavy).
- Phase 1 (close-path fix): ~60 minutes (3 sites, 1 helper, 9 unit tests).
- Phase 2 (Transformer demote): ~45 minutes (1 file, 4 unit tests).
- Phase 3 (PriceWorker delete): ~15 minutes (1 file, 1 unit test).
- Phase 4 (sentiment migrate): ~45 minutes (1 file + cross-DB plumbing).
- Phase 5 (backfill script): ~90 minutes (new script, dry-run, apply, verification).
- Phase 6 (verification report): ~30 minutes (write-only).

Total: ~5–6 hours of focused implementation, plus the soak windows the operator chooses between phases.

Phases 1–3 are the critical path and can ship as a bundle in a single day. Phases 4–6 follow over the subsequent week.

---

## 11. Follow-Up Prompts (Not In This Document)

Suggested separate prompts after this fix is fully shipped and stable:

1. **Architectural cleanup — delete enrichment helpers entirely.** Once Phase 4 has migrated sentiment off `ticker_cache` and Phase 2's observation-only enrichment has soaked for a month with no surprises, the observation helpers can be deleted entirely. The strategist's PROMPT_DEFERRED gate must be rewired to consume divergence telemetry from a different source — likely a small standalone monitor that reads both feeds directly.
2. **Bybit-readiness audit.** Analogous in scope to the price-source forensic bundle. Maps every assumption that Shadow's permissive simulation hides: order rejection paths, partial fills, idempotency keys, rate limits, private WS streams, liquidation, funding payments, gradual capital ramp, kill-switch, per-symbol allowlists.
3. **Drop `ticker_cache` table.** After Phase 4, the table has no live consumers. After a 30-day soak, drop the table in a migration. Update any historical scripts that referenced it.

---

## 12. End Of Prompt — Implementation May Begin From Section 4.0

The implementer should begin with Phase 0 (Section 4.0) and proceed sequentially. Do not skip phases. Do not combine phases into a single commit. If any pre-condition or verification fails, stop and surface to the operator.

Good luck. The bug is real, the fix is surgical, and after this ships the operator will trust the dashboard again.
