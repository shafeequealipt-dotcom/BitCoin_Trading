# Phase 1.9 — Git History

Investigation content lives in **[`phase1_synthesis.md`](phase1_synthesis.md) Section 10**.

## What's covered there

Recent commits since 2026-04-01 affecting the audit-relevant files:
- `36e51aa docs(strategist/phase-2): correct enable_prompt_compression docstring`
- `b94d0bd fix(logging/phase-3D): route sizing component to workers.log`
- `d4c33d0 feat(sizing/phase-3D): unified SIZE_DERIVATION observability event`
- `960f8cf feat(fund_manager/phase-3C): capital tier hysteresis`
- `69ccc0c feat(sizing/phase-3AB): wire xray_confidence and expected_rr into APEX conviction weight`

**Conclusion:** No commits since 2026-04-01 modify `Transformer.switch_to` body, Shadow adapter contract, or trading service signatures. The audit (2026-05-08) is current with respect to the files that the bybit_demo project must integrate against.

See `git log --since='2026-04-01' -- src/core/transformer.py src/shadow/ src/trading/services/`.
