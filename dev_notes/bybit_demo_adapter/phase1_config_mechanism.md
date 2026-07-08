# Phase 1.4 — Configuration Mechanism

Investigation content lives in **[`phase1_synthesis.md`](phase1_synthesis.md) Section 4**.

## What's covered there

- `GeneralSettings.mode` default + load path
- `config.toml [general] mode` literal source-of-truth
- Validator location at `src/config/validators.py:_validate_mode`
- Runtime override mechanism: DB-driven via `transformer_state.current_mode`, NOT config-edit
- Phase 3.A additions: `BybitDemoSettings`, `_build_bybit_demo`, `[bybit_demo]` TOML section
- Phase 3.A.refactor: mode constants moved to `src/core/modes.py` (single source of truth post-cross-check)

See `src/config/settings.py`, `src/config/validators.py`, `src/core/modes.py`, and `config.toml`.
