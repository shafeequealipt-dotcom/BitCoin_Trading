# Phase 1A — CALL_B Investigation Document

**Date:** 2026-05-06
**No code changes.** Output is the input to sub-phases 1B-1E.

## 1. POSITION_SYSTEM_PROMPT (verbatim)

`src/brain/strategist.py:144-162`:

```
POSITION_SYSTEM_PROMPT = """You are managing open crypto futures positions. Review each position and decide what to do.

RULES:
1. Output ONLY valid JSON: {"position_actions": {"SYMBOL": {"action": "hold|close|tighten_stop|set_exit", "new_sl": price_or_null, "exit_price": price_or_null, "reasoning": "..."}}}
2. Review EVERY open position — do not skip any.
3. Actions:
   - hold: Position thesis intact, regime supports direction — let it run.
   - tighten_stop: Move SL closer to lock profits or reduce risk. Provide new_sl price.
   - set_exit: Set specific exit price target. Provide exit_price.
   - close: Position clearly failing — close immediately.
4. Decision framework:
   - If regime supports direction and thesis intact and SL not close to being hit: HOLD.
   - If profitable (PnL > +1.5%) and regime weakening: TIGHTEN_STOP to lock gains.
   - If PnL > +3% and position aging: TIGHTEN_STOP aggressively.
   - If regime reversed against position direction and SL > 70% consumed: CLOSE.
   - If thesis is broken (the reason for entry no longer holds): CLOSE.
5. Consider: PnL %, position age, SL consumed %, per-coin regime alignment, thesis validity, current TA / regime alignment.
6. Do NOT suggest new trades — only manage existing positions.
7. When tightening stops, set new_sl at a logical level (e.g., breakeven, recent swing, or halfway to entry)."""
```

**Framing-problematic content identified for Sub-phase 1B:**

- Rule 3 (line 150) — `hold: Position thesis intact, regime supports direction — let it run.` — the "thesis intact" + "regime supports" framing implies the inverse triggers close.
- Rule 3 (line 153) — `close: Position clearly failing — close immediately.` — needs reframing. "Clearly failing" must be defined by structural invalidation, not regime alignment.
- Rule 4 line 155 — `If regime supports direction and thesis intact and SL not close to being hit: HOLD.` — the implied negation is "if NOT regime-supportive OR thesis broken, then NOT hold (i.e., close or tighten)". This is the root of the regime-mismatch closure pattern.
- **Rule 4 line 158 — `If regime reversed against position direction and SL > 70% consumed: CLOSE.`** — explicit instruction to close on regime mismatch. Drop in 1B.
- **Rule 4 line 159 — `If thesis is broken (the reason for entry no longer holds): CLOSE.`** — explicit instruction to close on thesis-broken. Drop in 1B.
- Rule 5 line 160 — `Consider: ... per-coin regime alignment, thesis validity, current TA / regime alignment.` — promotes regime-alignment as a primary consideration. Reframe in 1B.

## 2. `_build_position_prompt` Section Map

`src/brain/strategist.py:3071-3240`. Sections in order:

| # | Lines | Section | Sample / Format | Classification |
|---|---|---|---|---|
| 1 | 3081 | MARKET REGIME | `## MARKET REGIME: trending_up (75%)` | OPERATIONAL — kept |
| 2 | 3084 | SENTIMENT | `## SENTIMENT: Fear & Greed = 45` | OPERATIONAL — kept |
| 3 | 3087-3092 | TODAY PnL | `## TODAY: PnL=-5.91%` | OPERATIONAL — kept |
| 4a | 3095 | Open positions header | `## YOUR OPEN POSITIONS — Review each and decide: hold, close, tighten_stop, set_exit` | ESSENTIAL — kept (header only; contract section to be added BELOW it in 1D) |
| 4b | 3120-3174 | Per-position block | symbol, side, entry/mark/PnL, SL/TP/lev, age/remaining/regime, sl_consumed, **thesis text** | ESSENTIAL except thesis line — see classification below |
| 4c | 3176-3181 | APEX-FLIPPED notice (conditional) | `APEX-FLIPPED: Buy->Sell: <reason>` | ESSENTIAL — to be EXTENDED in 1E with concrete RR for XRAY flips |
| 5 | 3186-3199 | RECENT LESSONS (sentinel-only, removed in `f718686`) | (none — removed Phase 1A post-execution-closure-fix) | Already removed; regression test guards |
| 6 | 3202-3211 | Recently closed cooldowns (conditional) | `RECENTLY CLOSED: SYM cooldown (Ns remaining)` | OPERATIONAL — kept |
| 7 | 3213-3222 | Urgent-queue concerns (conditional) | watchdog concerns formatted by `urgent_queue.format_for_prompt` | OPERATIONAL — kept |

**Per-position block detail (lines 3167-3181):**

```python
sections.append(
    f"\n### {symbol} [{side_val}]\n"
    f"  Entry: ${pos.entry_price:.2f} | Now: ${pos.mark_price:.2f} | PnL: {pnl_pct:+.2f}%\n"
    f"  SL: ${sl_price:.2f} | TP: ${thesis_data.get('take_profit_price', 0):.2f} | "
    f"Lev: {thesis_data.get('leverage', '?')}x\n"
    f"  Age: {age:.0f}min | Remaining: {remaining:.0f}min | Regime: {rgm_str}\n"
    f"  SL consumed: {sl_consumed:.0f}%\n"
    f"  Thesis: {str(thesis_text)[:200]}"  # ← line 3174, drop in 1C
)
if thesis_data.get("apex_flipped"):  # ← lines 3176-3181, replace in 1E
    sections.append(
        f"  APEX-FLIPPED: {thesis_data.get('apex_original_direction', '?')}"
        f"->{thesis_data.get('direction', '?')}: "
        f"{str(thesis_data.get('apex_reason', ''))[:100]}"
    )
```

## 3. Per-section Classification (ESSENTIAL / OPERATIONAL / FRAMING-PROBLEMATIC)

| Section | Class | Action |
|---|---|---|
| MARKET REGIME | OPERATIONAL | Keep — Claude needs context. Removing it would also break CALL_B's ability to make legitimate structure-based decisions. The framing fix at the system-prompt + contract level is sufficient — the regime data itself is benign; how the prompt teaches Claude to reason about it is what matters. |
| SENTIMENT | OPERATIONAL | Keep — used to inform tightening decisions. After Phase 5 (sentiment disable), F&G will still be displayed but Claude won't be told to over-weight it. |
| TODAY PnL | OPERATIONAL | Keep — Claude uses this for size/risk awareness. |
| Open positions header | ESSENTIAL | Keep but the contract directly below (Sub-phase 1D) reframes the decision aim. |
| Per-position block (entry/PnL/SL/TP/regime/sl_consumed) | ESSENTIAL | Keep all data fields — they're the source of truth Claude needs. |
| **Per-position thesis line** (line 3174) | FRAMING-PROBLEMATIC | **Drop in 1C** — the thesis text was written before any APEX/XRAY flip and now describes a different direction than the position holds. This contradicts the current state shown in the same block. Operator decided "remove from CALL_B". |
| APEX-FLIPPED notice (lines 3176-3181) | ESSENTIAL but incomplete | Keep + EXTEND in 1E to include source (xray vs apex) and concrete RR justification (RR_chosen vs RR_rejected, ratio). |
| RECENT LESSONS | (already removed) | No action — sentinel `_tias_lessons_removed=True` at line 3199; regression-guard test exists. |
| Cooldowns | OPERATIONAL | Keep. |
| Urgent queue | OPERATIONAL | Keep. |

## 4. CALL_A vs CALL_B Framing Comparison

CALL_A (`TRADE_SYSTEM_PROMPT` at strategist.py:65-141, post-fix) leads with:

```
Your aim is to exploit the current market situation and aggressively fetch the maximum profitable trade from these candidates.

Markets always present opportunities. Overbought conditions are fade setups. Extended moves are exhaustion plays. ...

Aggressive exploitation. Maximum profit. Find the play.
```

CALL_B (`POSITION_SYSTEM_PROMPT` at strategist.py:144-162, current) leads with:

```
You are managing open crypto futures positions. Review each position and decide what to do.

RULES:
1. Output ONLY valid JSON: ...
2. Review EVERY open position — do not skip any.
3. Actions: hold/tighten_stop/set_exit/close.
4. Decision framework:
   - If regime supports direction and thesis intact and SL not close to being hit: HOLD.
   ...
   - If regime reversed against position direction and SL > 70% consumed: CLOSE.
   - If thesis is broken (the reason for entry no longer holds): CLOSE.
```

**Gaps to mirror in 1B + 1D:**
1. CALL_A leads with the aim ("aggressively fetch maximum profitable"); CALL_B leads with mechanical rules. Sub-phase 1D adds an aim section.
2. CALL_A reframes setups (overbought ⇒ fade, extended ⇒ exhaustion). CALL_B has no equivalent vocabulary; close-decision relies on regime-alignment heuristics. Sub-phase 1D introduces "structural invalidation" as the close trigger language.
3. CALL_A's `## DIRECTION BY REGIME` (lines 80-86) explicitly notes per-coin regime overrides global. CALL_B's rules treat regime as a uniform close-trigger. Sub-phase 1B drops the trigger; per-coin regime data still appears in the position block but is no longer mechanically tied to close.

## 5. Thesis Text Trace

How the original thesis text reaches CALL_B's prompt:

1. Trade opens → `strategy_worker.py:2130` calls `thesis_mgr.save_thesis(...)` with `thesis=reasoning` (Claude's CALL_A reasoning text).
2. Row inserted into `trade_thesis` table with `status='open'`.
3. CALL_B cycle → `strategist.py:3115` calls `thesis_mgr.get_open_theses()`.
4. `thesis_manager.py:90-106` SELECTs (among other cols) `thesis` and returns dicts.
5. `strategist.py:3143-3147` reads `thesis_data["thesis"]` into `thesis_text`.
6. `strategist.py:3174` formats the line: `f"  Thesis: {str(thesis_text)[:200]}"`.

**Variable scope of `thesis_text`:** defined at line 3144, used only at line 3174. No other references. Dropping the line at 3174 orphans the lookup; the lookup itself can be removed in the same diff.

**`thesis_data` scope:** defined at line 3143, used at lines 3155 (`stop_loss_price`), 3170 (`take_profit_price`), 3171 (`leverage`), 3176 (`apex_flipped`), 3178 (`apex_original_direction`), 3179 (`direction`), 3180 (`apex_reason`). Stays — Sub-phase 1E adds new fields (xray_flip_*).

## 6. XRAY Flip Metadata Trace

Where it's set:

`src/workers/strategy_worker.py:1650-1696` — when XRAY direction-flip fires:

```python
trade["direction"] = _flipped_dir
trade["stop_loss_price"] = _new_sl
trade["take_profit_price"] = _new_tp
if not trade.get("_apex_original_direction"):
    trade["_apex_original_direction"] = _orig_dir
trade["_apex_was_flipped"] = True
trade["_flip_source"] = "xray"
trade["_xray_flip_ratio"] = round(_ratio, 2)
```

The `_orig_rr` (original-direction RR) and `_new_rr` (chosen-direction RR) are computed on lines 1664-1672 from `_sp.rr_long` / `_sp.rr_short`. Currently logged in `XRAY_DIR_FLIP` event but **NOT stored on the trade dict**.

Where flip metadata is persisted (currently):

`src/workers/strategy_worker.py:2130-2153` — `thesis_mgr.save_thesis(...)` is called with:
- `apex_flipped=bool(_apex_was_flipped)` ✓ persisted
- `apex_original_direction=_apex_original_dir` ✓ persisted
- `apex_reason=_apex_reasoning[:200]` ✓ persisted
- **`_flip_source` — NOT passed** ✗ lost
- **`_xray_flip_ratio` — NOT passed** ✗ lost
- **`_orig_rr` / `_new_rr` — NOT computed at save site** ✗ lost

Where flip metadata is read (currently):

`src/core/thesis_manager.py:90-106` — `get_open_theses()` SELECTs `apex_flipped, apex_original_direction, apex_reason` only. `xray_flip_*` columns don't exist yet.

`src/brain/strategist.py:3176-3181` — CALL_B reads `apex_flipped`, `apex_original_direction`, `apex_reason`. Cannot distinguish XRAY-driven flips from APEX-driven flips. Cannot quote concrete RR justification.

**Operator decision (recorded in plan): YES — schema v28 adds 4 columns:**
- `xray_flip_source` TEXT NOT NULL DEFAULT '' (values: 'xray' / 'apex' / '')
- `xray_flip_ratio` REAL NOT NULL DEFAULT 0.0
- `xray_flip_rr_long` REAL NOT NULL DEFAULT 0.0
- `xray_flip_rr_short` REAL NOT NULL DEFAULT 0.0

`thesis_manager.save_thesis()` extends with corresponding kwargs. `get_open_theses()` extends SELECT list. `strategy_worker.py:2130` extends call site with new kwargs. `strategist.py:3176-3181` extends APEX-FLIPPED notice with concrete RR for XRAY flips.

**Note:** for the at-flip RR values to land in the persisted thesis, the strategy_worker flip site (lines 1650-1696) must also store `_xray_flip_rr_long` / `_xray_flip_rr_short` on the trade dict so the downstream save_thesis call site can read them.

## 7. TIAS Lessons — Already Removed (regression-guarded)

`src/brain/strategist.py:3186-3199` — sentinel-only block (no code emits the section). The variable `_tias_lessons_removed = True` flows into the `STRAT_CALL_B_CTX` log (line 3229) for live regression detection.

Existing tests at `tests/test_strategist_callb_prompt.py:115-140`:
- `test_system_prompt_drops_lessons_from_similar_trades_guidance()` — asserts `"lessons from similar trades"` not in `POSITION_SYSTEM_PROMPT`.
- `test_callb_prompt_has_no_recent_lessons_header()` — asserts `"## RECENT LESSONS"` not in rendered prompt.
- `test_callb_prompt_has_no_lesson_keyword()` — asserts `"Lesson:"` not in rendered prompt.
- `test_callb_prompt_keeps_market_regime_and_sentiment_headers()` — asserts surrounding sections still present.

**No action in 1B-1E touches the lessons block.** Existing tests must continue to pass through Phase 1.

## 8. POSITION_SYSTEM_PROMPT call sites

- `src/brain/strategist.py:787` — `raw_response = await self.claude.send_message(prompt, POSITION_SYSTEM_PROMPT)` (sole runtime caller, in `create_position_plan`).
- `src/brain/strategist.py:144` — definition.
- `src/brain/strategist.py:37` — docstring reference (no functional dependency).
- `src/brain/strategist.py:3618` — docstring reference in `_parse_position_plan` (no functional dependency).
- `tests/test_strategist_callb_prompt.py:25, 115, 118` — test imports + assertions.

`_build_position_prompt` callers:
- `src/brain/strategist.py:784` — sole runtime caller.
- `tests/test_strategist_callb_prompt.py:128, 139, 151` — test invocations (rely on the existing fixture).
- `tests/test_stage2_phase4/test_priority_classifier.py:61, 74` — comments referencing the function (no test dependency).

## 9. Response Parser

`_parse_position_plan` at `src/brain/strategist.py:3615-3699`. Tolerates:
- Null fields (per `price_or_null` contract).
- Unknown action strings (downgrades to "hold" with `STRAT_CALL_B_DOWNGRADE` log).
- Missing fields (defaults to "hold").
- Invalid `new_sl`/`exit_price` on tighten/set_exit (downgrades to "hold").

**No parser changes needed for 1B-1E.** The action-type set (`hold | close | tighten_stop | set_exit | take_profit`) is unchanged.

## 10. Schema Migrations Pattern

`src/database/migrations.py`:
- `SCHEMA_VERSION = 27` (top of file).
- `MIGRATIONS = [...]` — flat list of SQL statements applied in order. New statements append at the bottom; the version bumps when a new logical group is added.
- Pre-existing `trade_thesis` columns from v23: `apex_flipped`, `apex_original_direction`, `apex_reason`.
- Pre-existing `trade_thesis` columns from v27: `entry_xray_confidence`, `entry_setup_type`, `entry_regime_at_open`, `entry_regime_confidence`.
- Pre-flight column-exists check (lines 1356-1379) means re-applying ADD COLUMN is idempotent — Phase 1E's v28 statements are safe even if run multiple times.
- v28 will add the 4 new `xray_flip_*` columns and bump `SCHEMA_VERSION` to 28.

## 11. Observability Already in Place

`STRAT_CALL_B_*` events already present (no new events needed beyond:):
- `STRAT_CALL_B_CTX` (line 3226-3231)
- `STRAT_CALL_B_URGENT` (line 3221)
- `STRAT_CALL_B_PARSED`, `STRAT_CALL_B_DOWNGRADE`, `STRAT_CALL_B_BAD_*` (parser)
- `STRAT_ACTION_CLOSE`, `STRAT_ACTION_CLOSE_BLOCKED` (watchdog, line 2627)
- `XRAY_DIR_FLIP` (strategy_worker line 1687)

New events to be added in 1B-1E:
- `STRAT_CALL_B_REFRAMED` (one-shot at boot, in 1B) — sentinel that the new system prompt is loaded.
- `THESIS_FLIP_PERSISTED` (at thesis_mgr.save_thesis when xray flip data non-empty, in 1E).
- `STRAT_CALL_B_FLIP_NOTICE` (in `_build_position_prompt` when notice rendered, in 1E).

## 12. Verification Gate

| Item | Status |
|---|---|
| POSITION_SYSTEM_PROMPT verbatim quoted | PASS |
| All sections classified | PASS |
| CALL_A vs CALL_B comparison documented | PASS |
| Thesis-text trace complete | PASS |
| XRAY flip metadata trace complete + persistence gap identified | PASS |
| TIAS lessons regression guard verified | PASS |
| Response parser change scope = none | PASS |
| Migrations pattern + v28 plan documented | PASS |
| All callers of `_build_position_prompt` and `POSITION_SYSTEM_PROMPT` mapped | PASS |

Gate: **PASS**. Proceed to Sub-phase 1B.
