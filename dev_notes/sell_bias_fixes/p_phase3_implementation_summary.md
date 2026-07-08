# PRIMARY Issue — Phase 3 Implementation Summary

Date: 2026-05-11
Branch: `fix/sell-bias-fixes-2026-05-11`
Operator tune chosen: HEAVY (asymmetric + insufficient-data + counter-trade respect + typo fix).
Status: code complete, tests pass, awaiting operator restart for Phase 4 live verification.

# Section 1 — What Shipped

Four commits on the feature branch, in landing order:

| Commit | Subject |
|--------|---------|
| `11ee05b` | docs(p): Sell-bias investigation reports (Phase 0 + Phase 1 + Phase 2) |
| `81552f9` | fix(p): repair structural_data attribute typo on optimizer flip-confidence gate |
| `b14cbd9` | feat(p): asymmetric Buy→Sell vs Sell→Buy flip-confidence thresholds |
| `2c82657` | feat(p): counter-trade + insufficient-data flip gates + APEX_FLIP_DECISION log |

Files touched:

- `src/apex/optimizer.py` — new helpers `_resolve_flip_threshold`, `_check_insufficient_data_for_flip`, `_is_counter_trade_setup`; typo fix; counter-trade + insufficient-data gates inline; APEX_FLIP_DECISION unified log.
- `src/apex/models.py` — added `StructuralData.setup_type: str` field.
- `src/apex/assembler.py` — populate `setup_type` from `analysis.setup_type` in `_gather_structural_data_from_cache`.
- `src/config/settings.py` — added four new APEXSettings fields with defaults matching the HEAVY tune.
- `config.toml` — added entries for the four new fields with operator-facing comments.
- `tests/test_apex_qwen_client.py` — fixed mock attribute name (`structure_data` → `structural_data`).
- `tests/test_apex_flip_rr_boost.py` — new regression test locking the canonical attribute name.
- `tests/test_apex_flip_discipline.py` — updated `_make_optimizer` to set all three threshold fields uniformly; added 4 new tests covering asymmetric behaviour.
- `tests/test_apex_sell_bias_gates.py` — NEW file with 13 unit tests for the two new gates.

Total: 4 commits, 8 production files touched, 4 test files touched (1 new), ~300 LOC of production code and 200 LOC of tests added.

# Section 2 — New Configuration

`config.toml [apex]` section additions:

| Key | Default | Effect |
|-----|---------|--------|
| `apex_min_flip_confidence_buy_to_sell` | 0.95 | Buy → Sell flips require effective confidence ≥ 0.95 (with RR boost) |
| `apex_min_flip_confidence_sell_to_buy` | 0.70 | Sell → Buy flips require ≥ 0.70 (unchanged from prior symmetric default) |
| `apex_min_trades_for_flip` | 5 | Target direction must have ≥ 5 trades in current regime; set 0 to disable |
| `apex_respect_counter_trade` | true | APEX may not flip on COUNTER_TRADE setups (any setup_type containing "counter") |

Legacy `apex_min_flip_confidence` (0.70) preserved as the fallback for unknown direction pairs.

# Section 3 — New Log Tags For Operators

| Tag | Level | Meaning |
|-----|-------|---------|
| `APEX_FLIP_COUNTER_PROTECTED` | WARNING | Counter-trade gate reverted a flip |
| `APEX_FLIP_INSUFFICIENT_DATA` | WARNING | <5 trades in target direction; flip reverted |
| `APEX_FLIP_DECISION` | INFO | Unified per-call decision log — single greppable line per optimize() |

`APEX_FLIP_DECISION` fields:

```
APEX_FLIP_DECISION | sym=X brain_dir={Buy|Sell} apex_dir={Buy|Sell}
  flip_attempted={Y|N} flip_accepted={Y|N}
  decision_reason={lock_override|counter_protected|insufficient_data|conf_below_threshold|flip_accepted|no_flip_attempt}
  regime={r} raw_conf={x.xx} eff_conf={x.xx}
  rr_boost={x.xx} rr_chosen={x.xx} rr_flipped={x.xx}
  dir_locked={Y|N} lock_reason='{r}'
  flip_dir_trades={n} qwen_initial_dir={Buy|Sell}
```

Operators can grep `APEX_FLIP_DECISION decision_reason=` and pipe to `sort | uniq -c` to see the daily distribution of decisions. This is the primary observability metric for Phase 4 verification.

# Section 4 — Behavioural Effect

Replay analysis of the 2026-05-11 9-hour log window predicts the following changes:

| Outcome (today) | Pre-Fix | Post-Fix Predicted |
|-----------------|--------:|--------------------:|
| APEX flips (Buy→Sell) | 16 | ~3-5 (only ones with conf ≥ 0.95 or RR-boost confirmation) |
| APEX flips (Sell→Buy) | 7 | 7 (unchanged threshold) |
| APEX flips total | 23 | ~10-12 |
| Final final_dir=Buy | 3 | Estimated 15-25 (counter-trade Buys survive APEX) |
| Final final_dir=Sell | 62 | Estimated 40-50 |

These numbers are estimates. XRAY runs downstream of APEX and may still flip Buy → Sell on trades that APEX no longer flips, so the final-Sell rate cannot be predicted precisely without live data. **Phase 4 verification will measure the actual shift.**

# Section 5 — Test Results

| Test file | Passing | Notes |
|-----------|--------:|-------|
| test_apex_flip_rr_boost.py | 4/4 | Includes new typo regression guard |
| test_apex_flip_discipline.py | 12/12 | 4 new asymmetric-threshold tests |
| test_apex_sell_bias_gates.py | 13/13 | NEW file, counter-trade + insufficient-data |
| test_apex_qwen_client.py | 21/21 | Mock attribute name corrected |
| test_apex_lock_propagation.py | 8/8 | No regressions |
| test_apex_pipeline_integration.py | 6/6 | No regressions |
| test_apex_tp_cap.py | 17/17 | No regressions |
| test_apex_direction_lock.py | 28/29 | One pre-existing failure unrelated (strategist prompt test) |
| test_shadow_adapter_boot_grace.py + test_shadow_signature_parity.py | 16/16 | Shadow path unaffected |

Total APEX + shadow tests: **125/126 passing**; the single failure is pre-existing and unrelated (`test_system_prompt_still_has_rsi_caution` in `test_apex_direction_lock.py` references a brain prompt out of this fix's scope).

# Section 6 — Out-of-Scope Confirmation

This fix does NOT touch:
- Brain (Stage 2 prompt construction, Claude CLI subprocess internals).
- Transformer architecture.
- Shadow adapter — verified unchanged (16/16 shadow tests pass).
- Existing strategies — analyzed only, not modified.
- Layer 1 scanner pipeline — not modified.
- Bybit demo HTTP/auth/signing/WS-parse layer.
- XRAY's flip code in `src/workers/strategy_worker.py:1604-1779` — unchanged. P.1.5 + p_phase1_xray_root_cause.md established that XRAY is structurally correct; the fix happens at the APEX layer.

# Section 7 — Restart + Verification Checklist (Phase 4)

When ready, restart both services:

```
sudo systemctl start trading-workers.service
sudo systemctl start trading-mcp-sse.service
```

Then monitor over a 24-48 hour window. Key Phase 4 metrics:

1. **Direction distribution shift**: `grep "DIRECTION_DECISION" data/logs/workers.log | grep -oE "final_dir=[A-Za-z]+" | sort | uniq -c`. Baseline today is 62 Sell / 3 Buy (95% Sell). After fix, expect a meaningful shift toward final_dir=Buy.
2. **APEX_FLIP_DECISION distribution**: `grep "APEX_FLIP_DECISION" data/logs/workers.log | grep -oE "decision_reason=[a-z_]+" | sort | uniq -c`. Look for non-zero counts in counter_protected and insufficient_data — confirms gates fire.
3. **Counter-trade preservation**: pair every `APEX_FLIP_COUNTER_PROTECTED` log line with the eventual outcome — confirms the operator's contrarian alpha is preserved through APEX.
4. **No regressions on Sell→Buy flips**: confirm APEX still allows Sell→Buy flips when confidence ≥ 0.70 (Sell-bias was Buy→Sell direction; Sell→Buy was helpful per data).
5. **Trade frequency**: should NOT collapse. Per the spec's "aggressive opportunity exploitation" aim, the fix must not silently slow the system. If trade-frequency drops more than ~30%, escalate.

Output: `dev_notes/sell_bias_fixes/p_phase4_verification.md` (after 24-48 h live data).

# Section 8 — Rollback Plan

If verification surfaces an issue:

- Single config rollback (most flexible): set in `config.toml`:
  - `apex_min_flip_confidence_buy_to_sell = 0.70` (back to symmetric)
  - `apex_min_trades_for_flip = 0` (disable gate)
  - `apex_respect_counter_trade = false`
  - Restart workers. No code redeploy needed. This nullifies the behavioural change without removing the code.
- Full revert: `git revert 2c82657 b14cbd9 81552f9` on the feature branch. This restores pre-fix behaviour exactly. The typo bug returns (which is what existed before).

The typo fix (`81552f9`) and the docs (`11ee05b`) are safe to keep regardless — only the asymmetric tuning + new gates are revertible if their effect is undesired.

# Section 9 — Next Steps After Phase 4

If verification passes:

1. Operator signs off on Phase 4 verification report.
2. PRIMARY issue is closed.
3. The fix series moves to Issue 2 (SL Gateway systematic rejects) — separate investigation phase.

If verification reveals issues:

1. Investigate the specific deviation.
2. Discuss with operator.
3. Either tune the new config values OR revert per Section 8.
