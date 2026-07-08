# Tier 6 — Configuration drift and cosmetic (combined investigation + proposals)

Eight issues. Five ship code changes (T6-1, T6-2, T6-4, T6-7, T6-8). Three are documented as investigation-only or "documented as expected".

## T6-1 (Phase5 F-2 — config.toml mode mismatch) — SHIPPED

config.toml `[general] mode` updated from `"shadow"` to `"bybit_demo"` to match the persisted runtime state in `transformer_state` (mode=bybit_demo since 2026-05-08). The previous stale value did not affect runtime (DB-persisted mode wins) but was a recurring operator-confusion source on cold-start audit reads.

## T6-2 (F6 — WORKER_NEVER_TICKED WARN→INFO during cold-start) — SHIPPED

`worker_liveness_watchdog.py:_emit_never_ticked` now emits at INFO when `cycle_gated AND NOT cycle_active` (cold-start expected behaviour per `project_cold_start_resume_fix.md`). Genuine never-ticked failures (cycle_active=True but tick_count=0) remain at WARN. Operator dashboards no longer alarm on the boot window.

## T6-3 (F12 — STRAT_AGGRESSIVE_FRAMING auto-engages after winning streak) — DOCUMENTED AS EXPECTED

The framing tier shift after a winning streak IS the operator's intended aggressive-exploitation philosophy. The report cited concern because the same CALL_A that engaged aggressive framing also produced the SKR -0.32% loss in 96 seconds (which combined with `aggressive_exploit` leverage promotion). But the connection between framing tier and outcome is correlation not causation — the SKR loss was driven by a tight 0.30% SL crossed by normal market noise, not by framing.

**Conclusion**: aggressive framing is intended. Operator can disable by setting `[strategist] aggressive_framing_enabled = false` in config if desired (knob already exists per memory `project_callb_framing_fix_status.md`). No code change.

## T6-4 (F7 — ALERT_FAIL singleton with no error context) — SHIPPED

`alerts/alert_manager.py:209` ALERT_FAIL now captures `send_message` exceptions and reports an `err='...'` field plus message length. Pre-fix singleton failures gave operators no idea WHY the send failed.

## T6-5 (F13 — packages 195s old when consumed by Strategist) — DOCUMENTED

The 195s package age is structural: scanner emits on M5 boundaries; strategist consumes at the next available cycle. Combined with the 60-180 s Claude latency, total staleness reaches 4-6 min at trade time.

Fix would require either:
- Tightening scanner cadence (touches Layer 1D scheduler — out of scope per plan).
- Re-fetching ticker at trade time (already done — APEX_PRICE_SOURCE source=ws).

The existing APEX re-fetch mitigates the most-critical staleness vector (entry price). Structural setup staleness is what remains. Operator may revisit during a Layer 1D refresh-cadence engagement. No code change.

## T6-6 (F10 — trail stops locking in profit too tight) — DOCUMENTED, RE-MEASURE AFTER T1-2

T1-2's step clamp + trail coalesce reshape the trail behaviour materially. The report's F10 finding (15-minute hold for +0.79% suggests peak was higher) was measured pre-T1-2. Re-measure required after T1-2 + T1-4 deploy.

Operator can also tune `trail_distance_pct` in `config.toml` if the post-T1-2 behaviour locks too aggressively in early profit. No code change at this time.

## T6-7 (F22 — APEX DeepSeek latency spikes) — SHIPPED

`apex/optimizer.py:471-ish` now emits `APEX_DEEPSEEK_SLOW` at WARN when `deepseek_ms > 5000`. Operators can grep for these to correlate against OpenRouter status without parsing the full APEX_TIMING stream. The existing `apex_max_attempts` + `apex_retry_backoff_seconds` settings already provide retry on transient failures.

## T6-8 (Phase5 F-21 — closed_by truncated narrative) — SHIPPED

`core/layer_manager.py:1173` now records the stable enum `"strategic_review"` instead of `f"strategic_review: {action.reason[:100]}"`. The narrative remains in the existing `STRAT_POS_ACT` log line so audit-trail correlation is preserved via decision_id.

Cardinality of `closed_by` values is now bounded; CSV exporters and trade_history grouping work correctly.
