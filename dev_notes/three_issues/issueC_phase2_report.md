# Issue C Phase 2 — Operator Report: Profit Sniper Killing Marginal-Loss Trades

> Operator decision document. Plain prose, h1/h2/h3 structure. Phase 1 evidence in `issueC_phase1_synthesis.md`.

## Root cause

The four sniper-driven full closures in the 13:00–16:00 UTC 2026-05-08 window (`SANDUSDT`, `INJUSDT`, `ARBUSDT`, `HYPERUSDT`) all came via the **mature-stall valve** at `src/workers/profit_sniper.py:2481–2494` (`MODE4_STALL_ESCALATE`), not via the score-based `mode4_p9` threshold path. The score path cannot fire on losing positions because of the profit gate at `_determine_action:1590`, and none of the 4 closures had positive PnL.

`mode4_p9` is a fixed-string label hardcoded at `_execute_action:1931`, attached to every full closure regardless of trigger path. It is NOT a phase number, NOT a threshold name. The audit's "32 mode4_p9 events" framing conflated the substring's occurrences in tick-evaluation logs with actual closure events. Only 4 actual full closures fired.

The 4 victims share a common pattern: brief or no profit (peak ≤ +0.30 %), drifted into mild-to-moderate loss, sat in `is_actionable=True` + `action="hold"` state for 80–181 ticks (7–15 minutes), then the mature-stall valve forced the close once `ticks > stall_escape_full_after_ticks=40`. The PnL guard `SNIPER_DEVELOPMENT_GUARD` (default floor −0.3 %) blocks stall escape when `_last_pnl > -0.3 %`, but two of the four (INJUSDT −0.30 %, ARBUSDT −0.31 %) were within 0.01 percentage points of the floor — too tight a window for the operator's aggressive-exploitation philosophy.

## Severity reframing

| Audit claim | Reality |
|---|---|
| "32 mode4_p9 (full close) + 17 SNIPER_STALL_ESCAPE + 4 M4_ACT_CLOSE = 53/64 sniper-driven = 83%" | 4 actual sniper full closures + 13 partial closures + 4 stall-valve full closures = the 4 M4_ACT_CLOSE events. Sniper share of full closures: 4/~21 ≈ **19 %**, not 83 %. |
| "Trades aren't surviving long enough for strategy edge to manifest" | The 4 victims survived 7–15 minutes each. The reasonable subset of this complaint: trades that briefly reached profit shouldn't be killed at the −0.3 % development floor. |
| "Sniper produces 91% of all trade closures" | Sniper produces 19 % of full closures. CALL_B / watchdog strategic-review closures (9 events) outnumber sniper closures 2.25× in the window. |

The underlying concern is real but narrower: **the mature-stall valve fires too eagerly on positions that briefly touched profit and sit in marginal loss territory.** The fix scope is the valve and its surrounding guards, not a sweeping recalibration.

## The 4 victims (table)

| Time | Sym | ticks | Score | PnL | Peak | Why it died |
|---|---|---|---|---|---|---|
| 14:54:19 | SANDUSDT | 181 | 37 | −1.25 % | +0.06 % | mature-stall valve (ticks > 40); pnl below −0.3 % floor |
| 15:24:32 | INJUSDT | ≥80 | 34 | −0.30 % | +0.30 % | mature-stall valve; pnl exactly at −0.3 % floor (`>` not `>=`) |
| 15:33:34 | ARBUSDT | ≥80 | 17 | −0.31 % | +0.13 % | mature-stall valve; pnl 0.01 pp below floor |
| 15:47:56 | HYPERUSDT | 134 | 31 | −0.37 % | +0.00 % | mature-stall valve; never reached profit |

Score at close was 17–37, well below regime full thresholds (65–85). The score path was NOT the trigger.

## Hard constraints (per the prompt)

- The sniper must continue protecting against runaway losses (HYPERUSDT case — never profitable, deteriorating — should still be cut).
- The aggressive-exploitation aim must be preserved.
- The fix must not entirely disable the sniper.
- The fix must not break Phase 1 grace (commit `00f8eb1`, 241 SNIPER_GRACE_BLOCKED in window, working).
- Positions must still close eventually (no infinite holds).
- Real losing trades should still get cut.

## Solution options

Each option was derived from the investigation, not pre-committed in the plan. Each preserves the runaway-loss protection that the valve provides for HYPERUSDT-style cases.

### Option 1 — Lower the development floor (smallest change)

Lower `settings.layer4_sniper.development_window_lower` from −0.3 % to **−0.5 %** (or another operator-chosen value). This widens the SNIPER_DEVELOPMENT_GUARD's protection band so positions in mild loss between −0.5 % and 0 % are spared.

Spared: INJUSDT (−0.30 %), ARBUSDT (−0.31 %). Still killed: HYPERUSDT (−0.37 % — wait, this is still above −0.5 % — would actually be SPARED). SANDUSDT (−1.25 %, still killed correctly).

Tradeoff: simple one-line config change. Spares HYPERUSDT too — which the operator might consider correct (it was a small loss that still had room to recover) or incorrect (it never reached profit, no edge demonstrated). HYPERUSDT's score was 31, still well below tighten threshold; it was genuinely stuck in low-conviction territory. With floor at −0.5 %, only SANDUSDT-type cases (deeper losses) would have been killed. That may be too lenient.

### Option 2 — Peak-protected stall extension (recommended)

Extend the mature-stall valve threshold for positions that touched a meaningful peak. Two new settings:

- `peak_protection_threshold_pct` (default 0.10 %): minimum peak PnL to qualify for extension.
- `peak_protected_full_after_ticks` (default 80, vs base 40): the valve's tick threshold when peak qualifies.

When `state.peak_pnl_pct >= peak_protection_threshold_pct`, the mature-stall valve uses the larger threshold. Effect on the 4 victims:

- SANDUSDT peak +0.06 % < 0.10 → no extension, killed at ticks=181 (would have been killed anyway, valve fires at ticks>40 base or ticks>80 extended)
- INJUSDT peak +0.30 % ≥ 0.10 → extended to 80, position would have had more time. INJUSDT closed at "≥80 ticks" so this might still kill it depending on exact tick count.
- ARBUSDT peak +0.13 % ≥ 0.10 → extended to 80.
- HYPERUSDT peak +0.00 % < 0.10 → no extension, killed at ticks=134 (still killed correctly — never reached profit).

Tradeoff: requires code change at `_stall_escape_action:2356`. Two new settings exposed to operator. Direct alignment with operator's aggressive-exploitation aim: trades that demonstrated edge get extra time; trades that never worked are still cut.

### Option 3 — Require deteriorating PnL for forced full close

Add a "PnL is getting worse" predicate to the mature-stall valve. The valve fires only when `current_pnl < worst_pnl_pct + recovery_threshold`. If a position is slowly recovering from its worst, it gets spared.

Effect: positions in stable-but-bad state still get killed (they're not recovering). Positions with even slow recovery momentum are spared. Combined with Option 2, gives a strong recovery-respecting valve.

Tradeoff: requires the position's PnL trend to be tracked. The infrastructure is partially there (`_stall_worst_pnl_pct` is already tracked at line 2467). Code change is small.

### Option 4 — Increase `stall_escape_full_after_ticks` globally

Raise from 40 to 80 (or higher). Doubles the time before mature-stall fires.

Tradeoff: blunt instrument. Slows valve for HYPERUSDT-style runaway-loss cases too — those trades will sit at deteriorating PnL longer before being cut. Conflicts with the runaway-loss protection requirement.

### Option 5 — Disambiguate `closed_by` labels

Replace the hardcoded `"mode4_p9"` at line 1931 with a context-aware label: `mode4_score_full`, `mode4_stall_valve`, `mode4_anti_greed_full`, `mode4_partial_cap_full`. Pure observability — no behavioural change.

Tradeoff: future incidents are diagnosable from log labels alone. Recommend pairing with any of Options 1–3.

### Option 6 — Hybrid (Options 2 + 3 + 5) — recommended

- Peak-protected extension on the mature-stall valve.
- Deteriorating-PnL gate (only fire if not recovering from worst).
- Path-disambiguating `closed_by` labels.

Three atomic commits, each independently revertable. Together they directly address the operator's "trades that almost worked are getting killed" concern while preserving runaway-loss protection.

## My recommendation

**Option 6 (hybrid).** Reasoning:

1. Option 2 (peak protection) directly addresses the philosophical mismatch: trades that briefly worked deserve more time. INJUSDT and ARBUSDT specifically would have been spared.
2. Option 3 (deteriorating-PnL gate) is the natural safety complement: even with peak protection extended to 80 ticks, a position that's clearly deteriorating still gets cut. Prevents marginal-loss positions from sitting indefinitely.
3. Option 5 (label disambiguation) is pure observability with no behavioural impact. Eliminates the source of confusion that drove the audit's misclassification of "32 mode4_p9 events".
4. Together they preserve both runaway-loss protection (HYPERUSDT-style cases, deteriorating positions) and aggressive-exploitation (trades that almost worked).
5. None of the 4 audit-window cases would have closed worse under this combination — SANDUSDT and HYPERUSDT (the genuinely-stuck cases) still close; INJUSDT and ARBUSDT (the marginal cases that almost worked) get more time.

If you want minimal change: Option 1 alone (config-only floor adjustment) handles INJUSDT/ARBUSDT but doesn't structurally improve the valve.

If you want maximum surgical control: Option 6 gives you per-position behavioural change keyed off the position's actual peak trajectory.

## Implementation plan if Option 6 is approved

Three atomic commits:

### Phase 3a — Path-disambiguating `closed_by` labels

- Add a new `_resolve_close_label()` helper at module top of `profit_sniper.py` mapping the action's source/path to one of the four labels: `mode4_score_full` (score-path), `mode4_stall_valve` (mature-stall valve fired), `mode4_anti_greed_full` (anti-greed >75% pullback), `mode4_partial_cap_full` (partial cap exhausted, escalated to full).
- Replace the hardcoded `closed_by="mode4_p9"` at `_execute_action:1931` with the resolved label based on `action.source` / `action.greed_rule_triggered` / the upstream stall-escape signal.
- Update `MODE4_STALL_ESCALATE` log to also pass through the resolved label so SNIPER_STALL_ESCAPE → close traces are labeled consistently.
- Atomic commit: `fix(issueC/3a): disambiguate mode4 close labels by trigger path`.
- Tests: each path emits its expected label.

### Phase 3b — Peak-protected stall extension

- Add `peak_protection_threshold_pct: float = 0.10` and `peak_protected_full_after_ticks: int = 80` settings to `Layer4SniperSettings` (alongside existing `min_age_seconds`, `profit_protection_threshold`, `development_window_lower`).
- Plumb the position's `state.peak_pnl_pct` into `_stall_escape_action()` (currently the method has access to `tracked` but not `state`; need to pass `peak_pnl` through). One option: the caller (`tick()`) already has `state`; pass `state.peak_pnl_pct` as a new param.
- At `_stall_escape_action`, compute `effective_full_after = peak_protected_full_after_ticks if peak_pnl_pct >= peak_protection_threshold_pct else stall_escape_full_after_ticks`.
- Use `effective_full_after` instead of `full_after` in the mature-stall valve predicate at line 2481.
- Emit a `MODE4_PEAK_PROTECTED` log when the extension applies (peak qualifies), so operators can see the gate working.
- Atomic commit: `fix(issueC/3b): extend mature-stall threshold for positions that touched peak profit`.
- Tests: peak ≥ threshold extends to 80; peak < threshold uses 40; HYPERUSDT-shape (peak=0, deeply losing) still kills at 40.

### Phase 3c — Deteriorating-PnL gate on mature-stall valve

- Read `_last_pnl` and `worst_pnl_pct` (already tracked at line 2467) in the mature-stall branch.
- Add a `recovering_threshold_pct: float = 0.10` setting. Position counts as "recovering" when `_last_pnl - worst_pnl_pct >= recovering_threshold_pct`.
- The valve at line 2481 fires only when NOT recovering.
- A position recovering from worst gets a free pass on the mature-stall valve for as long as the recovery trajectory holds. If the recovery stalls (PnL flatlines or worsens), the valve fires next tick.
- Emit `MODE4_VALVE_BLOCKED_RECOVERING` when the recovery gate spares a position.
- Atomic commit: `fix(issueC/3c): require non-recovering PnL for mature-stall full-close`.
- Tests: stuck-flat → kills; recovering by ≥0.10% → spared; no recovery + ticks > extended threshold → kills.

## Verification (Phase 4)

- Run 4–24 hours post-deploy. Sniper kill rate is sparse (4 events in 3 hours in audit window), so a 24-hour window gives stronger signal.
- Compare against the 13:00–16:00 baseline:
  - `M4_ACT_CLOSE` rate by `closed_by` label: should drop or shift composition.
  - `MODE4_PEAK_PROTECTED` count: should be non-zero whenever a previously-profitable position avoids kill.
  - `MODE4_VALVE_BLOCKED_RECOVERING` count: should fire on positions with recovery momentum.
  - `TRAIL HIT` count: should rise (more positions reach natural close as they get more time).
  - Trade execution rate: must stay 100 %.
- Per-position deep dive: pick 2–3 closures from the new window and trace the M4 lifecycle (M4_DECISION → SNIPER_STALL_ESCAPE → MODE4_STALL_ESCALATE → close) to confirm the new gates worked as intended.
- Aim check: no decrease in trade frequency (placement rate); no increase in catastrophic losing-trade hold time. SANDUSDT-shape closures should still occur; INJUSDT/ARBUSDT-shape should NOT.

## Discrepancies surfaced (added to discrepancies.md)

| # | Topic | Audit/memory | Reality |
|---|---|---|---|
| C-1 | Sniper has phases M1–M9 | Audit / memory | Regime-aware score thresholds; `mode4_p9` is a hardcoded label string |
| C-2 | "32 mode4_p9 events (full close)" | Audit | 32 substring occurrences in tick logs; only 4 actual `COORD_CLOSE_END | by=mode4_p9` events |
| C-3 | "53/64 sniper-driven (83%)" | Audit | 4/21 = 19% sniper share of full closures |
| C-4 | "Mode4_p9 events kill trades" | Audit | None of the 4 closures triggered by score path; profit gate blocks score path on losing positions |
| C-5 | Bybit demo affects sniper math | Audit speculation | Sniper consumes Shadow's authoritative `pos.net_pnl_usd`; same Bybit ticker source as before |

## Operator's decision needed

Choose:
- Option 1 — Lower development floor (config only).
- Option 2 — Peak-protected stall extension.
- Option 3 — Deteriorating-PnL gate.
- Option 4 — Global tick-threshold raise (NOT recommended; conflicts with runaway-loss protection).
- Option 5 — Path-disambiguating labels (observability only).
- Option 6 — Hybrid (2 + 3 + 5) (recommended).
- A modified version of any of the above.
