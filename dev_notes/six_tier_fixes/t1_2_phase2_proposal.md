# T1-2 Phase 2 — F8 Trail SL never advances proposal

## 1. Confirmed diagnosis

- `_push_sl_to_shadow` (`src/workers/position_watchdog.py:772-889`) is the single point of truth for watchdog SL submissions to the gateway.
- It is called by 7 callsites in position_watchdog.py with sources `trail_activation`, `trail_update`, `sentinel_deadline`, `sentinel_advisor`, and `STRAT_ACTION_SL` strategic-action paths.
- It does NOT clamp step_pct before submitting; the gateway's R3 cap (`max_step_pct=0.25`) rejects every legitimate trail step that exceeds the cap.
- profit_sniper.py already has the right pattern: SNIPER_CAP at lines 1469-1524 reads `settings.sl_gateway.max_step_pct`, clamps `new_sl_candidate` when the requested step exceeds the cap, and logs `SNIPER_CAP` with rich context.
- The fix is a pure port-forward of SNIPER_CAP into `_push_sl_to_shadow`, applied to ALL sources so gateway R3 becomes a safety net for genuine bugs rather than a production blocker.

## 2. Three solution options

### Option A — Watchdog clamp inside `_push_sl_to_shadow` (recommended)

Port profit_sniper's SNIPER_CAP into `_push_sl_to_shadow` so every watchdog source submits a step at most `max_step_pct` wide. Emit `WD_TRAIL_STEP_CLAMPED` at INFO with source + raw vs capped values. Multiple ticks gradually catch up to the new trail level over time.

- Edits: 1 block (~15 lines) in `_push_sl_to_shadow`, 1 smoke test.
- LOC: ~20 added.
- Pros:
  - Pure root-cause fix that preserves the gateway's R3 cap intent.
  - Single location covers ALL watchdog sources (trail_activation, trail_update, sentinel_*, STRAT_ACTION_SL).
  - Sibling to existing SNIPER_CAP — same pattern, same threshold source, audit-friendly.
  - Zero contract change at the gateway (R3 unchanged).
  - Trail can still keep up: a 1.2% price move yields 5 trail submissions of 0.25% each over the next 30s+ window (subject to R4 rate-limit).
- Cons:
  - Trail SL lags during fast price moves (steady catch-up over multiple ticks vs single jump).
  - Increases submission rate for trail sources; may exacerbate F5 thrash unless coalesced. Mitigation: optional 10s coalesce window for trail sources (see Option A2 below).

### Option A2 — A + trail-source coalescing

A plus a 10s consumer-side coalesce window for `trail_activation` and `trail_update` mirroring the existing time-decay coalesce at `_push_sl_to_shadow:845-859`. This keeps trail catch-up active without F5 thrash.

- Edits: A's edits plus a small coalesce block (~10 lines).
- LOC: ~30 added.
- Pros: A's benefits plus mitigation of F5 thrash for trail. Reduces total submission count while still letting trail advance.
- Cons: Adds 10s lag at fastest tick cadence; benign in practice because trail movements happen on M5 candles, not 1s scale.

### Option B — Raise gateway max_step_pct cap

Increase `max_step_pct` in config.toml from 0.25 to e.g. 1.5 so legitimate trail steps fit under the cap.

- Edits: 1 line in config.toml.
- LOC: 1.
- Pros: Minimal change.
- Cons:
  - Forbidden by the prompt's "Lowering the gateway's max_step_pct to fit the watchdog's computation" forbidden-list item. Raising is the inverse but goes against the gateway's stated intent (the original docstring cites the RIVERUSDT 2.5% strangulation as the case the cap exists to prevent — raising the cap re-enables that failure mode).
  - The rogue-jump safety net is weakened for ALL sources, including buggy ones.
  - Does not address the underlying asymmetry between sniper (clamps) and watchdog (does not).

### Option C — Full single-writer-of-record + coalescing (Architectural Theme 1)

Designate the watchdog as the SL writer-of-record per symbol. profit_sniper and sentinel_advisor publish proposed SL values via a `coordinator.propose_sl(symbol, source, value)` queue; the watchdog drains proposals per tick, picks the strictest (most-tightening), clamps to max_step_pct, and submits one consolidated change. Refactor profit_sniper.py and sentinel_advisor to publish instead of submit directly.

- Edits: large refactor across profit_sniper.py, position_watchdog.py, sentinel/advisor.py, trade_coordinator.py.
- LOC: ~300+ added/changed.
- Pros: Solves F8 (step) AND F5 (rate-limit thrash) AND Architectural Theme 1 in one shot.
- Cons:
  - Multi-day effort.
  - High blast radius across 4 modules.
  - The prompt explicitly notes T5-3 is "a continuation of T1-2's coalescing work — may end up bundled" — meaning the operator anticipated Option C would be done later, not now.
  - Risk of latent bugs in the new propose-consolidate path.

## 3. Recommendation

**Option A2** — clamp at `_push_sl_to_shadow` plus 10s trail-source coalescing.

Reasons:

1. Root-cause fix that preserves the gateway's R3 cap intent.
2. Smallest blast radius (1 helper, 1 test).
3. Sibling pattern to existing SNIPER_CAP — operators reading code will recognize the symmetry.
4. The 10s coalesce prevents F5 thrash for trail sources without needing the full Architectural Theme 1 rewrite.
5. T5-3 will handle the deeper multi-writer race separately as the plan anticipates.
6. Trail SL begins advancing immediately after deploy; F8 step_exceeded count drops to ~0.

The trade-off vs Option A (no coalesce): A2 adds 10s of lag on the fastest trail tick. The watchdog tick cadence is 5s and trail updates are based on M5 plan state — a 10s coalesce window is in the same noise range and acceptable.

## 4. Aim preservation

Both A and A2 preserve the operator's aggressive-exploitation philosophy:

- Trail SL advances faster than today (today it does not advance at all). Net effect on profit capture: significantly improved.
- Multiple smaller steps replace the single rejected large step; the eventual SL position is the same — just reached via N ticks instead of 1.
- No trade-frequency change; no entry/exit decision change.

## 5. Hard constraints satisfied

- Trail SL must advance on profitable runs — yes, multiple 0.25% steps replace the rejected single step.
- TRAIL_HIT events must fire on real reversals — unchanged.
- Gateway cap still prevents oscillation/thrash — yes, R3 still enforces at 0.25%.
- Multiple writers must not race — F5 mitigated by trail-source coalescing; deeper architecture in T5-3.

## 6. Observability additions

New log tags:

- `WD_TRAIL_STEP_CLAMPED sym=X src=<source> requested_pct=N capped_pct=M raw_new_sl=A capped_new_sl=B cur_sl=C dir=<dir>` — INFO, fires when clamp engages. Mirror of SNIPER_CAP semantics.
- `WD_TRAIL_COALESCE sym=X src=<source> last=N.Ns_ago new=Y cur=Z` — DEBUG, fires when 10s window suppresses a submission (only for A2).

Existing SL_GATEWAY_REJECT step_exceeded counts should drop to near-zero after deploy (gateway becomes a safety net rather than a production blocker).

## 7. Test plan (smoke, ≤10 min)

`tests/test_t1_2_trail_step_clamp.py` — 3 tests:

1. Direct unit on the clamp helper: a Buy/Long input with raw_step_pct=2.0% gets clamped to 0.25% exactly.
2. Direct unit on the clamp helper: a Sell/Short input with raw_step_pct=1.8% gets clamped to 0.25% exactly.
3. Edge: when current_sl is None or 0, no clamp engages.

Each test under 30 lines. `timeout 20 python3 -m pytest tests/test_t1_2_trail_step_clamp.py` wrap.

## 8. Operator decision required

Please choose:

- **A2 (recommended)**: clamp + trail coalesce. Best balance of F8 fix + F5 partial mitigation.
- **A**: clamp only, no coalesce. Simplest fix; may worsen F5 short-term (resolved later in T5-3).
- **B**: raise gateway cap. Forbidden by the prompt's spirit; presenting for completeness.
- **C**: full Architectural Theme 1 now. Defers T5-3 into T1-2; weeks of work.

Then state any non-default thresholds (e.g. coalesce window if not 10s; clamp percentile if not max_step_pct).

When you reply, I proceed to Phase 3 implementation.
