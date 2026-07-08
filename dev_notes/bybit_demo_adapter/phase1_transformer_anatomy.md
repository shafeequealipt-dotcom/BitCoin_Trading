# Phase 1.1 — Transformer Anatomy

Investigation content lives in **[`phase1_synthesis.md`](phase1_synthesis.md) Section 1**.

The synthesis was kept as a single comprehensive reference (rather than 9 separate per-topic files) because:
- All 9 anatomy investigations share file:line cross-references
- Maintaining one document is auditable; nine separate docs drift
- Phase 2-5 implementations cite "phase1_synthesis.md Section N" uniformly

This stub exists so the file structure listed in `IMPLEMENT_BYBIT_DEMO_ADAPTER_INDEPTH.md:481` resolves without duplication.

## What's covered in Section 1 of the synthesis

- `class Transformer:` definition + constructor + internal state fields
- `set_services()` adapter registration (3-slot additive pattern)
- `initialize()` boot-time DB read + crash recovery
- `_apply_mode()` 3-way dispatch + new `_services_for_mode()` helper
- `switch_to(target_mode, reason, confirmed)` hot-swap path (preserved)
- `record_switch()` / `set_switching_state()` public surfaces (post-encapsulation)
- DB schema for `transformer_state` and `switch_history`

See `src/core/transformer.py` and `src/database/migrations.py:995-1021`.
