# PRIMARY Issue — Phase 1 Step P.1.2: APEX Gate + Assembler

Sources:
- `src/apex/gate.py` (~600 lines)
- `src/apex/assembler.py` (~830 lines)

Status: read end-to-end. Investigation only — no code changes.

## 1. Does Gate Mutate Direction? — NO (confirmed)

`TradeGate.validate(trade)` (gate.py:48-496) runs 14 checks plus the APEX guardrail block (checks 8-12 inside the `if trade.get("_apex_optimized")` branch). Direction is **read** at three sites:

| File:line | Use |
|-----------|-----|
| gate.py:301 | reads `trade["direction"]` for loss-cooldown same-direction reject check |
| gate.py:336 | reads `direction` for the APEX guardrail Buy-vs-Sell branching (TP/SL bounds) |
| gate.py:461 | reads `_dir` for TP/SL sanity nudge (1.02× or 0.98× when identical) |

Direction is **never written** in gate.py. `grep -n 'trade\["direction"\] =' src/apex/gate.py` returns no matches. Confirmed by reading checks 0-14 end-to-end.

## 2. What Gate Can Do To A Trade

| Check | Lines | Effect | Tag |
|-------|-------|--------|-----|
| 0 — Claude size cap | 65-92 | Cap `size_usd` ≤ `claude_original × 1.5` | `CONVICTION_SIZE_CAP` |
| 1 — Max position size | 99-104 | Cap `size_usd` ≤ `max_position_size_usd` | (mod string only) |
| 2 — Max leverage | 106-111 | Cap leverage | (mod string only) |
| 3 — Max concurrent positions | 113-129 | If ≥5 open, reduce size to 30% | `GATE_POS_CHECK` on err |
| 4 — Capital availability + **zero-conviction REJECT** | 131-182 | If xray≤min AND setup≤min AND rr≤min → `_gate_rejected="zero_conviction"` | `GATE_REJECT reason=zero_conviction` |
| 4 (cont) — conviction weight | 184-260 | Scale size by per-coin profit-factor weight | (mod string) |
| 5 — Duplicate position | 263-274 | Halve size if already have a position on this symbol | `GATE_DUP_CHECK` on err |
| 6 — Cooldown + **loss-direction REJECT** | 276-320 | If same direction as prior loss in cooldown → `_gate_rejected="loss_cooldown_same_direction_*"`; else halve size | `GATE_REJECT reason=loss_cooldown_same_direction` |
| 7 — Min size floor | 322-327 | Floor at $50 | (mod string) |
| 8 — TP Floor (Buy: APEX TP ≥ Claude TP; Sell: APEX TP ≤ Claude TP) | 355-376 | Restore TP to original if APEX shrunk it | (mod string) |
| 9-12 — APEX guardrails | continues | trail floors, mode override, confidence scaling | various |
| 13 — RR low | 444-454 | Halve size if expected RR below threshold | `GATE_RR_CHECK` on err |
| 14 — TP/SL identical | 456-471 | Nudge TP by 1.02× (Buy) or 0.98× (Sell) | (mod string) |

Final emissions:
- `GATE_ADJUST` (INFO) with comma-joined modification strings — every time any check fires.
- `GATE_PASS` (DEBUG) when zero modifications.
- `GATE_TIMING` (INFO) per call; `GATE_TIMING_SLOW` (WARNING) at >500 ms.

Gate **can reject** but cannot **flip**. Rejected trades are forwarded with `_gate_rejected` set; layer_manager skips them.

## 3. Assembler Data Flow Into The DeepSeek Prompt

`IntelligenceAssembler.assemble(directive) → IntelligencePackage` (lines 55-119):

| Section | Source | Field on package |
|---------|--------|------------------|
| 1 Directive | `_build_directive_context(directive)` | `directive` |
| 2 Coin data | `_gather_coin_data(symbol)` — TA → WS quote → REST ticker → Mode4 → orderbook → volatility profiler | `coin_data` |
| Market conds | `_get_market_conditions(symbol)` — regime_str + fear_greed | (consumed by 3 & 4) |
| 3 Symbol history | `_gather_symbol_history(symbol, regime=regime_str)` — regime-filtered TIAS trades | `symbol_history` |
| 4 Situation data | `_gather_situation_data(regime_str, fg_value)` — TIAS cross-coin stats for this regime | `situation_data` |
| 5 X-RAY structural | `_gather_structural_data(symbol)` → `_gather_structural_data_from_cache(services, symbol)` | `structural_data` |

`APEX_ASSEMBLE_DONE` (INFO) emitted at lines 108-111 with `populated=[ta,m4,ob,vol,xray,tias_sym,tias_sit]` showing which sub-sections were filled. Operators can grep this to spot degraded contexts.

### Critical: `structural_data` carries `rr_long` and `rr_short`

`_gather_structural_data_from_cache` (lines 718-815) reads from `services["structure_cache"]`. When `analysis.structural_placement` exists, it copies:

```python
sd.rr_long       = sp.rr_long        # lines 756
sd.rr_short      = sp.rr_short       # lines 757
sd.rr_ratio      = sp.rr_ratio
sd.rr_quality    = sp.rr_quality
sd.rr_best_direction = sp.rr_best_direction
```

These are the values that the RR-boost path in `optimizer.py:367-387` is supposed to read.

## 4. CRITICAL FINDING — Latent Bug: `structure_data` vs `structural_data`

**The IntelligencePackage dataclass declares `structural_data: Optional[StructuralData]`** at `src/apex/models.py:387`:

```python
@dataclass
class IntelligencePackage:
    directive: DirectiveContext
    coin_data: CoinData
    symbol_history: TIASSymbolHistory
    situation_data: TIASSituationData
    structural_data: Optional[StructuralData] = None  # Section 5: X-RAY structural
```

**The assembler correctly populates this field** at `src/apex/assembler.py:118`:

```python
return IntelligencePackage(
    directive=section1,
    coin_data=section2,
    symbol_history=section3,
    situation_data=section4,
    structural_data=section5,
)
```

**But the optimizer reads the WRONG attribute** at `src/apex/optimizer.py:367`:

```python
_sd = getattr(package, "structure_data", None)   # ← typo: missing "al"
```

The dataclass has no attribute `structure_data`, so `getattr` returns the default `None`. Consequently:

```python
if _sd is not None:           # always False
    if claude_direction == "Buy":
        _rr_chosen = float(getattr(_sd, "rr_long", 0.0) or 0.0)
        ...
```

The entire RR-weighted boost block (lines 359-387) is **dead code**. `_rr_chosen`, `_rr_flipped`, and `_rr_boost` remain 0; `_effective_conf = _raw_conf + 0 = _raw_conf`.

### Impact

1. The Phase 3 dir-block-fix (2026-05-05) shipped the RR-weighted boost as a safety valve that would lower the effective confidence threshold by 0.15 when X-RAY structure favors the flipped direction. **This safety valve has never engaged**. Per `project_dir_block_fix_status` memory, the fix was reported "shipped 22/22 tests passed" — the unit tests must have been local-scope and missed this integration typo.

2. Implication for the Sell-bias investigation: today's 23 APEX_FLIPs all cleared the **raw** 0.70 confidence threshold without any RR boost. DeepSeek is reporting raw confidence ≥ 0.70 on the vast majority of its flip recommendations.

3. The `APEX_FLIP_BLOCKED` log claims to print `rr_boost`, `rr_chosen`, `rr_flipped` fields — but these will always be 0.00 because of the bug. The empirically-observed 5 APEX_FLIP_BLOCKED events today should show `rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00`. Worth verifying directly in P.1.9 by grepping a sample.

4. **For Phase 2 (operator decision), the operator should be informed that:**
   - The RR-weighted boost is currently inert — it would not have rescued any flips in the current implementation.
   - Fixing the typo (one-char change: `structure_data` → `structural_data`) would activate the boost, which would *increase* flip-rate by allowing some sub-0.70 raw-confidence flips through. This is the opposite of what the Sell-bias fix needs.
   - The operator likely wants the typo NOT fixed in isolation; instead it should be addressed as part of whichever solution option is chosen (e.g., Option 3 may want the boost active but at a higher threshold).

This finding is informational for Phase 2; it does not require any code change in this stretch.

### Verification (further reading)

- `grep -n "package.structure_data\|package.structural_data" src/` confirms only one site of each spelling — the typo at optimizer.py:367 and the correct name everywhere else.
- `git log -p src/apex/optimizer.py | grep -E "structure_data|structural_data"` would reveal when the typo was introduced. (Not run in this stretch — read-only investigation only.)

## 5. Findings Map

| Question | Answer |
|----------|--------|
| Does gate.validate() mutate direction? | No — direction is read-only in gate.py (3 sites: 301, 336, 461). |
| Can gate REJECT a trade? | Yes — `_gate_rejected` set on zero-conviction (162-182) or loss-cooldown-same-direction (302-313). |
| What data reaches the Qwen prompt? | 5 sections: directive, coin_data, symbol_history, situation_data, structural_data. Each section may be partially populated; the `APEX_ASSEMBLE_DONE populated=[...]` log shows which. |
| Where does `rr_long`/`rr_short` come from? | `structure_cache` service → `analysis.structural_placement` → `_gather_structural_data_from_cache` (assembler.py:718-815). |
| Is RR-boost actually engaged at runtime? | **NO** — the optimizer reads `package.structure_data` (typo) instead of `package.structural_data`. Dead code. |

## 6. Open Questions Pushed Forward

- P.1.3 must paste the actual Section 3 + Section 4 content sent to DeepSeek (TIAS direction breakdown, situation buy/sell WR, direction_bias). If the **content** of these sections systematically favors Sell, that's the root prompt-side bias regardless of any prompt-engineering knob.
- P.1.9 must verify `APEX_FLIP_BLOCKED` log lines show `rr_boost=0.00` to corroborate the bug finding.

## 7. Out-of-scope Confirmation

- No code changed.
- No interaction with brain, Transformer, Shadow adapter, Layer 1, or Bybit execution.
