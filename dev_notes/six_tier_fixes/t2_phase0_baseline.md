# Tier 2 Phase 0 — Tier-specific pre-flight

## 1. Purpose

Refresh references for Tier 2's three issues (T2-1 F20, T2-2 F14, T2-3 F11) immediately before Tier 2 work begins.

## 2. References reconfirmed

| Issue | File:line | Status |
|-------|-----------|--------|
| T2-1 F20 cooldown emit | `src/core/trade_coordinator.py:893-901` (`on_trade_closed`) | Confirmed. 3 cooldown durations: win=180s, hard_stop/mode4_crash=900s, normal loss=600s. ALL closes get a cooldown today (including wins). |
| T2-1 F20 cooldown read | `src/core/trade_coordinator.py:1108-1116` (`is_symbol_cooled_down`) | Confirmed. Returns bool + clears expired entries lazily. |
| T2-1 F20 gate enforcement | `src/apex/gate.py:232-245` (CHECK 6) | Confirmed. **Halves size** when symbol is cooled — does NOT reject. modification tag `size_halved_cooldown_{N}s`. |
| T2-2 F14 zero-conviction | `src/apex/gate.py:147-179` (CHECK 4 CONVICTION_WEIGHT) | Confirmed. Weights size by xray_conf tier (>=0.85, >=0.70, >0, ==0). xray_conf==0 leaves size NEUTRAL — does NOT reject. |
| T2-3 F11 brain vs analysis | `src/brain/strategist.py` (CALL_A direction emission) + `src/analysis/engine.py` (analysis verdict) + `src/apex/gate.py` (`xray_flip` mechanism) | Mechanism exists; needs design Phase 1 on enforcement layer choice. |
| Gate return contract | `src/apex/gate.py:48-421` (`validate`) returns `dict` only — no reject path | T2-1 and T2-2 fixes need a new `_gate_rejected` flag + layer_manager skip at `layer_manager.py:1404`. |

## 3. New finding

The Gate has no reject mechanism today — `validate()` always returns a (possibly modified) trade dict. layer_manager:1404 unconditionally executes the validated trade. Adding T2-1 (hard cooldown reject) and T2-2 (zero-conviction reject) requires introducing a reject mechanism. Simplest: `trade["_gate_rejected"] = "<reason>"` flag; layer_manager checks after validate and skips.

## 4. Tier 2 plan

- T2-1 first (concrete: hard cooldown reject on same-direction).
- T2-2 second (similar pattern: gate reject on zero-conviction floor).
- T2-3 last (design pass: brain vs XRAY enforcement layer choice).

Each follows the same investigation + proposal + implementation cycle as Tier 1.
