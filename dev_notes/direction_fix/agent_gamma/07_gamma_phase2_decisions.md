# GAMMA Phase 2 — Operator Decision Record

## Decisions

- Design: **Design C — Aim-bias-conditional cap**
- Cap value: **70%** (with warn at 60%, min positions = 3)
- Location: **Layer 4 Gate, new CHECK 15 in `src/apex/gate.py`**
- Sequencing: **R1 (ALPHA) → R2+R3 (BETA) → R4 (GAMMA)** (R4 ships LAST as aim-aware back-stop)

## Architectural reconciliation

GAMMA Phase 1 flagged Design C as violating spec A.5 cross-layer doctrine because L4 Gate would need to consult L1B XRAY data. However, ALPHA Option E's plumbing already carries `trade_direction` from XRAY → assembler → APEX. By the time the package reaches Layer 4 Gate, `trade_direction` is in `StructuralData` (a shared in-package field). Gate reads what's already there; no new cross-layer dependency is introduced.

## Design C specification (with operator-chosen 70%)

CHECK 15 in `src/apex/gate.py`, after CHECK 14, before final return:

```
counts = coordinator.get_direction_counts()
total = counts['total']
if total < portfolio_direction_cap_min_positions:  # default 3
    emit PORTFOLIO_CONCENTRATION_CHECK verdict=skip
    return  # cap does not engage

new_dir = proposed_direction
post_count_new_dir = counts[new_dir] + 1
post_total = total + 1
post_pct_new_dir = post_count_new_dir / post_total

if post_pct_new_dir < portfolio_direction_cap_warn_pct:  # default 0.60
    emit PORTFOLIO_DIRECTION_PERMITTED
    return  # safely below warn

if post_pct_new_dir < portfolio_direction_cap_pct:  # default 0.70
    emit PORTFOLIO_CAP_WARN
    return  # in warn band, permit

# At or above cap. Apply aim-bias-conditional gate (Design C).
trade_direction = package.structural_data.trade_direction  # ALPHA plumbed
rr_long = package.structural_data.rr_long
rr_short = package.structural_data.rr_short

# Define "opposite direction viable" — the aim-bias signal that conditions the cap:
opposite_dir = "long" if new_dir == "Sell" else "short"
opposite_viable = False

if trade_direction and trade_direction != "":
    # Strongest signal: XRAY's setup-payoff direction differs from proposed direction
    if (new_dir == "Sell" and trade_direction == "long") or \
       (new_dir == "Buy" and trade_direction == "short"):
        opposite_viable = True

if not opposite_viable and rr_long is not None and rr_short is not None:
    # Fallback: structural ratio supports opposite
    if new_dir == "Sell" and rr_short > 0 and (rr_long / max(rr_short, 0.01)) >= 2.0:
        opposite_viable = True
    elif new_dir == "Buy" and rr_long > 0 and (rr_short / max(rr_long, 0.01)) >= 2.0:
        opposite_viable = True

if opposite_viable:
    # Aim-bias-conditional cap FIRES
    _gate_rejected = f"portfolio_direction_cap_{new_dir}_{int(post_pct_new_dir*100)}pct_aim_conditional"
    emit PORTFOLIO_CAP_HIT  with verdict="blocked_aim_conditional", trade_direction, rr_long, rr_short
else:
    # Market is genuinely mono-trending; cap does NOT fire even at high concentration
    emit PORTFOLIO_CAP_HIT  with verdict="permitted_mono_trending"
```

## Why this is "Design C"

The cap fires CONDITIONALLY on aim-bias signal:

- When portfolio is ≥70% Sell AND XRAY shows a viable Buy opportunity (`trade_direction=long` from counter setup, OR `rr_long >> rr_short`) → cap FIRES (block further Sell concentration; the operator's aim is to balance when opportunity exists)
- When portfolio is ≥70% Sell AND XRAY confirms the same direction (`trade_direction=short`, `rr_short >> rr_long`) → cap DOES NOT FIRE (market is genuinely mono-bearish; let the system exploit it)
- This preserves aggressive opportunity exploitation in mono-trending markets while preventing pile-on when balance is possible

## Cross-agent dependencies

- ALPHA Option E: plumbs `trade_direction` into `StructuralData` — GAMMA reads this field
- BETA Option B: lock consults the same `structural_data` for R:R ratio — GAMMA's fallback ratio check uses the same data shape
- GAMMA must ship LAST. R1+R2+R3 must be verified before GAMMA's Phase 3 begins

## Helper to add

`TradeCoordinator.get_direction_counts() -> dict` returning `{"Buy": int, "Sell": int, "total": int}`. Lives in `src/core/trade_coordinator.py` near `cleanup_stale()`. Reads from `_trades` dict; uses `TradeState.side` field.

## Settings

New `APEXSettings` fields:

- `portfolio_direction_cap_enabled` (default True)
- `portfolio_direction_cap_pct` (default 0.70)
- `portfolio_direction_cap_warn_pct` (default 0.60)
- `portfolio_direction_cap_min_positions` (default 3)
- `portfolio_direction_cap_opposite_ratio_threshold` (default 2.0)

## New observability events

- `PORTFOLIO_CONCENTRATION_CHECK` (INFO) — fires every gate run; shows counts and verdict
- `PORTFOLIO_CAP_HIT` (WARNING) — fires when cap blocks an entry
- `PORTFOLIO_CAP_WARN` (INFO) — fires in 60-69% band (post_pct_new_dir before block)
- `PORTFOLIO_DIRECTION_PERMITTED` (INFO) — fires when cap permits below warn band

## Branch

`fix/r4-portfolio-direction-cap` (created off HEAD 7320266 at Phase 0).

## Reference deliverables

- `01_existing_constraints_inventory.md` — confirms absent path
- `02_cascade_reconstruction.md` — 14:45 evidence
- `03_cap_design_options.md` — full Design C specification
- `04_architecture_location.md` — Gate CHECK 15 reasoning
- `05_gamma_synthesis.md` — trial behavior + verification queries
- `06_gamma_phase2_report.md` — operator-facing presentation
