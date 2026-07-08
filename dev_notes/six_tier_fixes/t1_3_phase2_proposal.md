# T1-3 Phase 2 — F9 TIAS learning loop dark proposal

## 1. Confirmed diagnosis

- F9a (generation gap): no code path generates a non-empty `lesson` string. `_thesis_close_callback` calls `close_thesis(...)` without `lesson=`. `close_thesis` defaults to `""`. The only auto-fill case is `close_reason=="transformer_switch"`.
- F9b (injection design): the 2026-05-05 fix REMOVED CALL_B's lesson section because uncritical per-trade lesson injection created a closed-loop failure ("RENDERUSDT Sell just lost on time_decay" → next cycle closes a 3-min-old RENDERUSDT Sell).
- TIAS DeepSeek already produces rich lesson-like content in `ds_what_should_done` and `ds_how_to_exploit` on the `trade_intelligence` table — but it is not bridged to `trade_thesis.lesson`.
- CALL_A still reads `get_recent_lessons` and renders the `## LESSONS FROM RECENT TRADES` header + per-trade summary lines. The lesson body is gated by `if l.get("lesson"):` — always False today.

## 2. Three solution options

### Option A — Bridge TIAS DeepSeek output → `trade_thesis.lesson` with anti-closed-loop guards (recommended)

After TIAS Phase 2 completes its DeepSeek analysis, a new lightweight callback writes a concise lesson summary to `trade_thesis.lesson`. CALL_A's existing injection (strategist.py:1415) becomes active; CALL_B remains disabled per the 2026-05-05 fix.

Anti-closed-loop guards in `get_recent_lessons`:
- **Age gate**: only return lessons where `closed_at < now - 5 min`. Prevents recency-bias re-entry while still surfacing same-session learnings within 30 min.
- **Same-symbol exclusion** (caller-side, in strategist `_build_context_prompt`): pass current open-position symbols to `get_recent_lessons(exclude_symbols=...)` so a lesson for SYM X is not shown when X is in the prompt's coin list. The brain still sees aggregate patterns; it does not see "X just lost".

Lesson content: a 100-180 char concise template, e.g.
`"5m hold, time_decay close, p_win 0.10 — TP unreachable past resistance. Lesson: avoid Sell in ranging regime when TP > resistance level."`

The template is built from TIAS's `ds_what_should_done` (already concise per the prompt) plus close_reason + hold time. Fail-loud if TIAS phase 2 did not run.

- Edits: new helper in `thesis_manager` (or a small bridge module) that maps TIAS output → lesson; new close-callback in `manager.py`; `get_recent_lessons` accepts `min_age_seconds` and `exclude_symbols` parameters; strategist's CALL_A caller passes the open-symbols set.
- LOC: ~80 added (small bridge, callback registration, two extra params, smoke tests).
- Pros:
  - Re-opens the learning loop using the system's BEST analytic output (DeepSeek). 
  - Two-layer closed-loop guard (age + symbol scope).
  - TIAS's natural latency (DeepSeek roundtrip ~30 s to 3 min) is a free THIRD layer.
  - No DeepSeek cost added — uses existing TIAS Phase 2 output.
- Cons:
  - Three new parameters to wire (age, exclude_symbols, on_tias_done callback).
  - Per-trade narrative is inherently closed-loop-prone; the guards reduce but do not eliminate risk.
  - Lesson body comes from TIAS, which may be 30s-3min late vs close — some closes get a lesson, some never if TIAS is throttled.

### Option B — Aggregated stats channel (lower risk; less rich signal)

Replace per-trade lessons with an aggregated stats summary computed from the last N closes (e.g. last 50 closes or last 24 hours). Render as a small block in both CALL_A and CALL_B prompts. Closed-loop-immune by construction because the block does not mention specific symbols.

Example block:

```
## RECENT PERFORMANCE (last 50 closes)
WR: 56% (28W / 22L)  |  avg hold 12.4 min  |  net PnL +$48.72
By close reason:  trailing_sl 62% (W 80%)  |  time_decay 18% (W 20%)  |  bybit_sl_hit 12% (W 30%)
By regime: ranging 50% (W 55%)  |  trending_down 30% (W 70%)
Lesson candidates: time_decay closes have low WR — consider tightening time-decay min_age or p_win threshold.
```

- Edits: new module (or method on thesis_manager) that computes stats; new prompt section in CALL_A and CALL_B prompt builders; smoke tests.
- LOC: ~120 added.
- Pros:
  - Zero closed-loop risk.
  - Operator-friendly summary (the same kind of data the operator looks at in dashboards).
  - Works regardless of TIAS Phase 2 completion latency.
- Cons:
  - Less rich than per-trade narrative. The brain doesn't see "RENDERUSDT lost on Sell" — only "Sell-side WR in ranging is 45%".
  - "Lesson candidates" suggestion needs careful templating to avoid sounding like a directive.
  - More code than Option A.

### Option C — Just fix the log line; defer the learning loop

Acknowledge that `lesson=''` is currently a safe coincidence and defer the actual learning-loop re-enable.

Specifically: remove the `lesson` field from the `THESIS_CLOSE` log line (it is misleading); mark `trade_thesis.lesson` column as deprecated in a comment; close T1-3 with a "design pending" note and defer the actual fix.

- Edits: 1 log line change, 1 comment, 1 dev_note.
- LOC: ~5.
- Pros: zero behavioural change. Cheapest closure.
- Cons: F9 is the actual learning-loop dark issue — closing without fixing leaves the system unable to learn.

### Option D — Defer F9 entirely

Skip T1-3 for this engagement. The operator decides later. The 2026-05-05 closed-loop incident was severe; a careful design pass deserves its own engagement.

- Edits: none.
- Cons: deviates from the prompt's tier sequence; T1-4 cannot continue Tier 1 without T1-3 resolution per the prompt's hard rules.

## 3. Recommendation

**Option A** — bridge TIAS → lesson with age + symbol-scope guards.

Reasons:

1. Re-opens the learning loop using the system's strongest analytic output (DeepSeek post-trade analysis).
2. Three-layer closed-loop defence: TIAS latency (natural) + age gate (5 min) + same-symbol exclusion. Each layer independently disables the failure pattern.
3. Uses existing TIAS infrastructure — no new external API costs.
4. The 2026-05-05 fix is preserved (CALL_B still has no lesson injection); only CALL_A is re-enabled, and it had been left intact even by the prior fix.
5. Aggressive-exploitation philosophy preserved: lessons are advisory; they do not gate actions.

Note: Option B (aggregated stats) is the safest choice and is a worthy follow-up. If the operator prefers the safest path, B is the right call. A and B are NOT mutually exclusive — both can ship later. T1-3 ships A first because A directly addresses F9's "learning loop dark" framing.

## 4. Aim preservation

Both A and B preserve aggressive-exploitation:
- Lessons are advisory inputs to the prompt; the brain may still choose to ignore them.
- No new gate or veto fires from lessons. The closed-loop concern is about Claude's REACTION to lessons, not enforcement.
- Both A and B reduce the closed-loop risk Compared to pre-2026-05-05 by adding explicit safeguards.

## 5. Observability additions

- `TIAS_LESSON_BRIDGED sym=X order_id=Y lesson_chars=N` — INFO, on each successful bridge.
- `TIAS_LESSON_BRIDGE_SKIP sym=X reason=<no_tias_run|empty_ds_what>` — DEBUG when bridge skipped.
- `STRAT_CALL_A_LESSONS_FILTERED count=N excluded_for_age=A excluded_for_symbol=S` — INFO from strategist when lessons are filtered out by guards.

## 6. Test plan (smoke, ≤10 min)

`tests/test_t1_3_lesson_bridge.py` — 4 tests:

1. Bridge produces non-empty lesson from a TradeIntelligence row with `ds_what_should_done`.
2. Bridge skips and returns None when `ds_what_should_done` is empty.
3. `get_recent_lessons` excludes lessons whose `closed_at` is younger than `min_age_seconds`.
4. `get_recent_lessons` excludes lessons whose symbol is in the `exclude_symbols` set.

Each under 30 lines; `timeout 20 python3 -m pytest tests/test_t1_3_lesson_bridge.py` wrap.

## 7. Operator decision required

Please choose:

- **A (recommended)**: bridge TIAS → lesson with age + symbol-scope guards. Reopens learning loop via DeepSeek.
- **B**: aggregated stats instead of per-trade lessons. Safest closed-loop posture but loses narrative richness.
- **A + B**: ship A now, plan B as follow-up. Most thorough but doubles the work for T1-3.
- **C**: defer the learning loop; just fix the misleading log line.
- **D**: defer F9 entirely; T1-3 closed with explanation.

Then state any non-default thresholds (e.g. `min_age_seconds` if not 300; lesson length cap if not 180 chars).

When you reply, I proceed to Phase 3 implementation.
