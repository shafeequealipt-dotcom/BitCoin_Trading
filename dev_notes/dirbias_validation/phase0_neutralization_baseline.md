# Phase 0 — Neutralization Baseline (R4 cap + APEX flip thresholds)

Captured: 2026-05-19 13:30 UTC, prior to Phase 1A/1B edits.

## Service state

- `trading-workers`: active (PID 58466, up since 10:55:55 UTC)
- `trading-mcp-sse`: active (PID 58465, up since 10:55:55 UTC)
- Restart at 10:55:55 still current. No re-restart since.

## Boot sentinels (post-10:55 restart)

| Sentinel | Time | Status |
|---|---|---|
| `XRAY_FLIP_CONFIG` | 10:55:58.643 | fired (rotated workers log) |
| `STRAT_CALL_B_REFRAMED` | 10:56:00.897 | fired |
| `STRAT_REGIME_INSTR_REFRAMED` | 10:56:00.897 | fired |
| `STATE_LABELLER_REGIME_HAIRCUT_INIT` | 10:56:01.210 | fired |

All four 2026-05-19 fix sentinels confirmed firing.

## R4 portfolio cap baseline (14-day window)

| Metric | Count |
|---|---|
| Total `PORTFOLIO_CAP_HIT` events | 61 |
| `new_dir=Buy` blocked | 13 (21%) |
| `new_dir=Sell` blocked | 48 (79%) |
| `verdict=blocked_aim_conditional` (actual blocks) | 26 (43%) |
| `verdict=permitted_mono_trending` (fires but permits) | 35 (57%) |
| Most recent fire | 2026-05-19 13:04:52 (HYPEUSDT Buy blocked during monitoring) |

Direction skew (3.7x more Sell-blocks than Buy-blocks) reflects the pre-fix Sell-heavy portfolio state. Post-Phase-1A expectation: zero new `PORTFOLIO_CAP_HIT` events.

## APEX flip baseline (current workers rotation, ~2h)

| Metric | Count |
|---|---|
| `APEX_FLIP_DECISION` + `XRAY_DIR_FLIP` events | 27 |

Post-Phase-1B expectation: `APEX_FLIP_DECISION` log lines emit `floor_used=0.70` for BOTH `buy_to_sell` and `sell_to_buy` flips (was 0.95 vs 0.70 pre-fix).

## Current config.toml state (lines that will change)

```toml
# config.toml:1435 onwards (existing [apex] section)
[apex]
enabled = true
model = "deepseek/deepseek-v3.2"
fallback_model = "deepseek/deepseek-chat"
timeout_seconds = 60
max_tokens = 800
temperature = 0.2
max_position_size_usd = 1200
max_leverage = 5
min_tias_trades_for_optimization = 3
# ... continues ...

# config.toml:1552-1553 (asymmetric flip thresholds — current)
apex_min_flip_confidence_buy_to_sell = 0.95
apex_min_flip_confidence_sell_to_buy = 0.70
```

## Working tree state

Git HEAD: `2b0fa06 polish(dirbias/issue3): split STATE_LABELLER sentinel f-string for ruff E501`

Uncommitted at Phase 0 capture time:
- `data/layer_state.json` (runtime state — expected)
- `data/logs/layer1c_full.jsonl` (runtime log — expected)
- ~10 untracked `dev_notes/*.md` (this report's siblings — expected)

No source code or config.toml uncommitted. Phase 1 edits will land on a clean source/config tree.

## Reversibility plan

If Phase 1A or 1B causes a regression, revert via:
```bash
git checkout config.toml
sudo systemctl restart trading-workers trading-mcp-sse
```

Both phases are single-line additions / edits to `config.toml` only. No code change in Phase 1.

## Phase 1 gating criteria

Phase 1A (R4 cap) ready when: Phase 0 captured above (DONE).
Phase 1B (flip thresholds) ready when: Phase 1A complete + services restarted with new config.

Phase 2 (code removal) gated on 48-72h trial passing all M1-M10 metrics per plan.
