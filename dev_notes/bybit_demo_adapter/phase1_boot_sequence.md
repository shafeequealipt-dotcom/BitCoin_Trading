# Phase 1.5 — Boot Sequence

Investigation content lives in **[`phase1_synthesis.md`](phase1_synthesis.md) Section 5**.

## What's covered there

- 22-step boot order in `src/workers/manager.py:initialize`
- Step where Transformer instantiates (line 88-92)
- Step where Bybit live trading services create (lines 256-280)
- Step where Shadow adapters create (lines 282-298)
- New step where Bybit demo adapters create (post-Phase 3.C, lines 300-354)
- Step where `transformer.set_services(...)` wires all 9 kwargs (lines 359-369)
- Re-`initialize()` to apply DB-persisted mode (line 371)
- Phase 4.D addition: `verify_post_switch` call near end of init (line 811)

See `src/workers/manager.py:initialize`.
