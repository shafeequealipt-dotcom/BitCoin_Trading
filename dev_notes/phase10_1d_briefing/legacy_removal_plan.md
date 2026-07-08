# Phase 10 of the 1D Briefing Rewrite — Legacy Removal Plan

**Status:** **PREPARATION ONLY — DO NOT EXECUTE UNTIL PHASE 11 SIGN-OFF.**

This document is the audit + deletion list that Phase 10 will execute after the Phase 11 observation window closes with all 15 items signed (`dev_notes/phase11_1d_briefing/observation_signoff.md`).

---

## Entry gate (must hold before Phase 10 begins)

- [ ] Phase 11 observation window CLOSED with all 15 checklist items signed.
- [ ] No incidents recorded in `dev_notes/phase11_1d_briefing/incident_*.md`.
- [ ] Operator approves Phase 10 entry per the rollout plan's Section E touchpoints.
- [ ] Final audit grep run + checked in to this directory (see Section 1 below).
- [ ] No commits since the Phase 9 cutover that touched the legacy files in unexpected ways (`git log --since=<phase9-deploy> -- <file>` reviewed).

---

## 1. Final audit grep (run before deletion)

```bash
# Anything still referencing the legacy 5-gate path:
grep -rn "_qualifies\b" /home/inshadaliqbal786/trading-intelligence-mcp/src \
                          /home/inshadaliqbal786/trading-intelligence-mcp/tests \
                          /home/inshadaliqbal786/trading-intelligence-mcp/scripts

# Anything still using the mode flag (should ONLY be in tests after deletion):
grep -rn "scanner\.mode\b\|\[scanner\]\.mode\|settings.scanner.mode" \
        /home/inshadaliqbal786/trading-intelligence-mcp/src

# Anything still referencing the legacy MARKET DATA section in strategist:
grep -rn "MARKET DATA\b" /home/inshadaliqbal786/trading-intelligence-mcp/src

# Anything still using use_packages flag (Phase 7 brain restructure relic):
grep -rn "use_packages\b" /home/inshadaliqbal786/trading-intelligence-mcp/src

# Anything referencing ab_mode (Phase 8 harness):
grep -rn "ab_mode\b" /home/inshadaliqbal786/trading-intelligence-mcp/src

# Anything still using ScannerQualitativeSettings:
grep -rn "ScannerQualitativeSettings\b" \
        /home/inshadaliqbal786/trading-intelligence-mcp/src
```

Each match is classified as: **(D)** delete, **(K)** keep-as-comment, **(U)** update. Result table goes here at execution time.

---

## 2. Files to modify

### `src/workers/scanner_worker.py`
- **DELETE** `_qualifies()` method (lines 891-1011 in the post-Phase-9 file).
- **DELETE** `_check_blockers()` method (lines 357-415).
- **DELETE** `_regime_aligns()` static method (lines 342-355).
- **DELETE** `_compute_opportunity_score()` method **only if** the briefing path no longer needs it for legacy `breakdown` log compat. If still needed for SCANNER_SELECTED log keys, **KEEP**.
- **DELETE** the legacy exclusion-mode body in `tick()` (lines after the new `if mode == "briefing": return await self._tick_briefing_mode()` branch).
- **DELETE** the mode branch line itself (`tick()` body becomes the briefing flow inlined).
- **DELETE** the `_tick_briefing_mode()` wrapper — its body becomes `tick()` directly.
- **DELETE** the `ab_mode` check + `_derive_ab_mode*` methods.
- Audit: `agg = {fail_no_xray: 0, ...}` and the mode-translated SCANNER_FILTER_AGGREGATE — **DELETE** the legacy buckets; rename to a clean briefing-mode aggregate.

### `src/brain/strategist.py`
- **DELETE** the legacy `MARKET DATA` section (~lines 594-720 — confirmed by audit grep).
- **DELETE** the `use_packages` flag handling (the `if cfg_brain.use_packages:` branches).
- **DELETE** the `surface_briefing_fields` flag handling — body becomes the always-on briefing renderer.
- **DELETE** legacy `_format_packages_for_prompt` skip rule's `else` branch (the `if not pkg.qualified...` legacy filter).
- **KEEP** `_format_briefing_extras` and `_format_action_hint` (they ARE the briefing logic).
- **KEEP** `BRIEFING_SYSTEM_PROMPT_SUFFIX` — but its content becomes part of the main `TRADE_SYSTEM_PROMPT`.
- **DELETE** `STRATEGIST_SYSTEM_PROMPT = TRADE_SYSTEM_PROMPT` alias if no consumer remains.

### `src/config/settings.py`
- **DELETE** `ScannerQualitativeSettings` dataclass.
- **DELETE** `ScannerSettings.qualitative` field + `__post_init__` checks.
- **DELETE** `ScannerSettings.mode` field + the `valid_modes` check.
- **DELETE** `ScannerSettings.ab_mode` field + the `valid_ab_modes` check.
- **DELETE** `BrainSettings.use_packages` field.
- **DELETE** `BrainSettings.surface_briefing_fields` field.
- **DELETE** `_build_scanner_qualitative()` builder.
- **DELETE** the `mode=` and `ab_mode=` lines in `_build_scanner()`.
- **DELETE** the `use_packages=` and `surface_briefing_fields=` lines in `_build_brain()`.

### `config.toml`
- **DELETE** `[scanner.qualitative]` section.
- **DELETE** `[scanner].mode` and `[scanner].ab_mode` keys.
- **DELETE** `[brain].use_packages` and `[brain].surface_briefing_fields` keys.

### Tests to delete (case-by-case audit)
- `tests/test_scanner_filter.py` — exercises only legacy `_qualifies()`. **DELETE.**
- `tests/test_scanner_filter_aggregate.py` — exercises legacy 9-bucket aggregate. **EVALUATE** — if the briefing-mode aggregate test (Phase 5+) covers the new shape, **DELETE**; otherwise rewrite.
- `tests/test_scanner_rr_direction.py` — RR is a structural input, still relevant. **KEEP**, possibly update for briefing-mode score path.
- `tests/test_scanner_opportunity_score_confidence.py` — `_compute_opportunity_score` is gone or repurposed. **EVALUATE.**
- `tests/test_phase5_1d_briefing/test_mode_flag_default_off.py` — mode flag is gone. **DELETE.**
- `tests/test_phase8_1d_briefing/test_ab_alternation_deterministic.py` — ab_mode is gone. **DELETE.**
- `tests/test_phase9_1d_briefing/test_default_mode_briefing.py` — mode/surface flags are gone. **DELETE.**
- `tests/test_corrected_layer1_pipeline_e2e.py` — three Phase-9 mode-isolated tests need their `real_settings.scanner.mode = "exclusion"` lines removed and assertions rewritten for briefing-mode contract.

---

## 3. Files to delete entirely (none expected)

No source file is purely legacy after Phase 9. Every file in `src/workers/scanner/` is the briefing pipeline.

---

## 4. Verification gate (after deletion)

```
pytest tests/ -q                       # all green
git diff --stat HEAD~1                 # net deletion (likely 500+ lines removed)
grep -rn "_qualifies" src/ tests/      # zero matches expected
grep -rn "MARKET DATA" src/            # zero matches expected
grep -rn "use_packages\|surface_briefing\|ab_mode\b" src/   # zero matches expected
```

After 3 consecutive cycles of healthy production behavior post-deletion, Phase 10 closes.

---

## 5. Rollback procedure

`git revert phase10-1d-briefing-shipped`. This is the FIRST phase whose rollback is non-trivial because it's a large delete revert. The Phase 11 observation window's 1-2 week duration is precisely the buffer that makes this revert safe — by the time Phase 10 ships, the briefing pipeline has weeks of healthy production data behind it.

---

## 6. Estimated effort

~20 min mechanical work after the Section 1 audit completes. The audit itself is ~10 min of grep + classification. Total ~30 min.

---

## 7. Operator sign-off (at execution time)

```
Operator name:   ______________________________
Phase 11 sign-off ref:   dev_notes/phase11_1d_briefing/observation_signoff.md
Phase 10 commit hash:   <fill after commit>
Lines deleted (net):   __________
Lines added (net):    __________
Tests dropped:        __________
Verification gate:    □ all green   □ no greps remain   □ 3 cycles healthy
```
