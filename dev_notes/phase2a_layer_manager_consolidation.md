# Phase 2a — Consolidate duplicate `layer_manager.py`

**Date:** 2026-04-27
**Brief reference:** plan-mode-first-compeltely-recursive-parasol.md § Phase 2a
**Status:** Complete (single atomic deletion commit).

## Why this stands alone

The user flagged the duplication as "very sensitive — it connects almost all the files and data" and asked for the "best suitable fix" with extreme care. Phase 2 modifies LayerManager behaviour (Layer 3 enforcement); doing that on two divergent copies would propagate any mistake. Resolve the duplication FIRST, then build Phase 2 on a single canonical file.

## Verification

### Step 1 — Import map

Searched every imports across `src/`, `tests/`, `workers.py`, `brain.py`, `server.py`, `mcp_stdio_proxy.py`:

```bash
grep -rn "from src.workers.layer_manager\|from src\\.workers\\.layer_manager|workers\\.layer_manager|workers/layer_manager" \\
  src/ tests/ workers.py brain.py server.py mcp_stdio_proxy.py --include="*.py"
```

Result: **zero matches.** `src/workers/layer_manager.py` is orphan dead code.

The canonical `LayerManager` is registered in the ServiceContainer at `src/workers/manager.py:506-507`:

```python
from src.core.layer_manager import LayerManager
layer_manager = LayerManager(settings, self._services)
```

ServiceContainer key: `"layer_manager"`. Consumed by Telegram handlers via `_svc(context, "layer_manager")`.

### Step 2 — Behavioural diff

```
core/    : 42677 bytes
workers/ : 33668 bytes  (orphan)
```

Line-level diff (`diff src/core/layer_manager.py src/workers/layer_manager.py`):
- 190 lines unique to `core/` — these are forward additions: BRAIN_HEALTH cycle-time tracking, `BRAIN_CYCLE_A_FAIL`/`_DONE` symmetry, `_cycle_times` rolling buffer, expanded observability comments, more granular exception handling.
- 7 lines unique to `workers/` — these are stale OLDER versions of the same code that has been improved in `core/`:
  - Shorter older docstring on `_run_one_cycle` (core has the elaborated observability-contract docstring)
  - Untrapped `await strategist.create_trade_plan()` (core wraps this in try/except to emit `BRAIN_CYCLE_A_FAIL`)
  - Untrapped `await strategist.create_position_plan()` (same pattern)
  - Older comment phrasing on the enforcer halt check
  - Older direct call to `strategy_worker._execute_claude_trade(...)` (core's call site is identical functionally; the line difference is whitespace/wrapping)
  - Older terse `log.info("Claude new trades: ...")` (core uses the same intent with structured tag)

**No unique behaviour exists in `workers/`.** Every line in `workers/` either exists verbatim in `core/` or has been improved in `core/`. Deletion is strictly subtractive — no functional regression possible.

### Step 3 — Decision

- **Canonical:** `src/core/layer_manager.py` (per architecture: `core/` hosts cross-cutting state managers; the ServiceContainer already registers from there).
- **Action:** Delete `src/workers/layer_manager.py` in a single atomic commit.
- **Risk:** Zero functional risk because the orphan is unreferenced. Future-risk eliminated: divergence cannot grow when one source is gone.

### Step 4 — Test gate

`pytest tests/` after deletion. Tests that exercise the active LayerManager (telegram handlers, Phase tests) must pass. Pre-existing unrelated failures (signal_generator sentiment, bybit client error mapping — both fail on `e955218` already) are documented and accepted.

## Rollback

`git revert <hash>` of the deletion commit restores the orphan file. Because no code references it, the revert is risk-free; it merely re-introduces dead code.

## Outcome

- Single canonical `LayerManager` going into Phase 2.
- 33 KB of stale code removed.
- Future Phase 2 / Phase 3 changes to layer enforcement land on one file, not two.
