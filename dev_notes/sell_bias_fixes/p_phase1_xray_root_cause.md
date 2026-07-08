# PRIMARY Issue — XRAY Root Cause Deep-Dive

Trigger: operator request after P.1.5 — "analyse and find the root cause xray why doing that".
Status: investigation only. No code changes.

## 1. Top-Level Verdict

**The XRAY flip code itself is correct.** It computes structural R:R per direction and flips when one direction has materially better R:R than the other. The asymmetry (18 of 19 flips Buy→Sell today) is a **downstream consequence**, not a code issue.

The actual root cause sits one layer up: **brain is being handed explicit COUNTER_TRADE signals from the scanner, and the flip mechanism silently negates them**. The chain that produces the Sell-bias has six layers:

1. **Scanner** labels coins with `COUNTER_TRADE_LONG` / `COUNTER_TRADE_SHORT` as secondary labels (91 such labels in today's 9-hour log window).
2. **Brain (strategist)** sees the counter-trade label in its prompt at `src/brain/strategist.py:1854-1859` (rendered as " (COUNTER-TRADE — trade direction is OPPOSITE to market structure bias; lower conviction)") and at `src/brain/strategist.py:2150-2151`.
3. **Brain** emits a Buy directive based on the counter-trade signal (knowingly against structural bias).
4. **APEX** receives the Buy directive. The TIAS Section 3 history for the coin is contaminated by prior Sell trades (feedback loop). DeepSeek often flips to Sell.
5. **XRAY** runs the structural R:R check. Because the counter-trade signal is by definition "opposite to structural bias", `rr_short >> rr_long` (or vice versa). XRAY flips back to the "natural" direction.
6. **The counter-trade alpha is destroyed.** The trade now goes in the structural direction at full size — exactly what the counter-trade strategy was designed to avoid.

The XRAY flip is operating exactly as written. The problem is that the flip mechanism is **structurally hostile to counter-trade strategies**, and the scanner is generating counter-trade signals.

## 2. Live Evidence — The Counter-Trade Signals

From today's `SCANNER_LABELED` events:

```
SCANNER_LABELED | rank=1  coin=NEARUSDT primary=OPEN_POSITION_HOLD_REVIEW
                  secondary=KILL_ZONE_OPPORTUNITY,RANGE_FADE_LONG,COUNTER_TRADE_LONG,RECENT_LOSER_COOLDOWN
SCANNER_LABELED | rank=2  coin=CRVUSDT  primary=OPEN_POSITION_HOLD_REVIEW
                  secondary=KILL_ZONE_OPPORTUNITY,RANGE_FADE_LONG,COUNTER_TRADE_LONG,RECENT_LOSER_COOLDOWN
SCANNER_LABELED | rank=11 coin=MONUSDT  primary=KILL_ZONE_OPPORTUNITY
                  secondary=RANGE_FADE_LONG,COUNTER_TRADE_LONG,RECENT_LOSER_COOLDOWN
```

`COUNTER_TRADE_LONG` count today: **91 SCANNER_LABELED events**. NEARUSDT and CRVUSDT are exactly the coins seen in the XRAY_DIR_FLIP sample table — flipped from Buy to Sell with ratios 26× and 17× respectively. These are explicit counter-trade signals being reversed.

## 3. The Structural Asymmetry Verified

`StructureEngine.analyze` at `src/analysis/structure/structure_engine.py:280-339` calls `_sl_engine.calculate(direction="long" | "short")` twice and stores both `rr_long` and `rr_short` on the placement. The code is symmetric.

The asymmetry in today's data is empirical:

| Symbol | rr_long | rr_short | Implication |
|--------|--------:|---------:|-------------|
| ARBUSDT  | 0.1 | 2.9 | Near resistance |
| ADAUSDT  | 0.1 | 4.1 | Near resistance |
| CRVUSDT  | 0.2 | 3.5 | Near resistance |
| NEARUSDT | 0.2 | 4.2 | Near resistance |
| BLURUSDT | 0.1 | 8.2 | Near resistance (extreme) |
| GALAUSDT | 4.9 | 0.1 | Near support (Sell→Buy flip) |
| HBARUSDT | 0.0 | 5.6 | At/past resistance |
| MANAUSDT | 0.1 | 7.4 | Near resistance |
| NEARUSDT | 0.0 | 6.7 | At/past resistance |

GALAUSDT is the only Sell→Buy flip — its `rr_long=4.9, rr_short=0.1` shows price near support. The 18 other coins were all in "price near resistance" state.

These are exactly the locations where counter-trade-long strategies place trades — "fade the local top" or "buy the support" — the structural bias is against the direction by design. The R:R ratio code is doing what it's designed to do: it sees the bias against Buy and recommends Sell. But the brain's Buy signal was deliberately counter to that bias.

## 4. The XRAY Quality Gates That ARE Working

Not every trade reaches the flip block. XRAY first applies hard quality gates:

| Gate | File:line | Action |
|------|-----------|--------|
| XRAY_BLOCK | strategy_worker.py:1559 | `setup_quality == "SKIP" AND rr_ratio < 0.5` → skip trade |
| XRAY_CONFLICT | strategy_worker.py:1577 | Direction conflicts with strong structural trend + SKIP/C quality → skip |
| XRAY_DIR_BLOCK | strategy_worker.py:1669-1691 | No dual structural levels → skip trade |
| XRAY_DIR_FLIP_BLOCKED | strategy_worker.py:1704-1722 | Post-flip would conflict with strong trend on weak setup → skip |
| XRAY_DIR_FLIP | strategy_worker.py:1768 | Otherwise flip if ratio > threshold |

So the flipped trades have:
- Setup quality at least B (not SKIP, not C-with-conflict)
- Both directions have populated structural levels
- The flipped direction doesn't itself create a conflict

These flips are **structurally valid** in the immediate price location. The question is whether the immediate-price-location-based flip should override the counter-trade strategic intent.

## 5. Why This Maps To "Sell-Bias Today"

The market state today (across the coins the scanner triggered on) has prices systematically NEAR RESISTANCE. Most counter-trade-long signals (fade the local top) end up with `rr_long ≈ 0` and `rr_short` large. XRAY flips them.

If market shifted (prices hovering near support), the asymmetry would invert — `rr_long` large, `rr_short` small — and most flips would become Sell→Buy. The Sell-bias is a current-day artifact, not a permanent feature. But for the operator's question, it is the current operating reality, has been steady for 3 days, and is producing net losses.

## 6. The Real Question The Operator Should Decide

This is not a "fix XRAY" decision. XRAY is correct. The decisions are:

### A. Should the flip mechanism respect COUNTER_TRADE intent?

Currently it does not. Brain receives a counter-trade label, takes the trade, and the flip silently negates the intent. Options:

1. Skip the flip when `setup_type` contains "counter" (operator endorses counter-trades).
2. Keep flipping and treat counter-trade signals as "discarded automatically" (operator agrees the structural direction is always better).
3. Stop the scanner from emitting counter-trade labels (out of scope — Layer 1 is excluded).

### B. Should APEX's feedback loop be broken?

Even without counter-trade signals, APEX flips trades based on per-coin TIAS data that compounds prior decisions. Independent of XRAY, this contributes to Sell-bias. Options 1-4 from the P.1.10 synthesis address this layer.

### C. Should XRAY have a minimum absolute R:R floor?

Today's data has flips like `rr_long=0.0` → flip to short. If `rr_long=0.0`, the chosen-direction trade ALREADY has bad R:R — maybe the trade should be SKIPPED rather than flipped. Today's code only skips when `rr_long > 0 AND rr_short / rr_long > threshold`. When `rr_long == 0` exactly, ratio defaults to 0, no flip, trade proceeds at Buy with terrible R:R.

Sub-options:
- Add a "minimum chosen-direction R:R" gate at e.g. 0.5 — skip the trade if BOTH directions are unviable.
- Require flipped direction's R:R > 1.0 absolutely (not just relatively > 3× chosen) before flipping.

## 7. Conclusion — What "Tune APEX" Should Look Like

Given the root cause is upstream (counter-trade signals + feedback loop), the most surgical APEX-side tuning is:

1. **Fix the typo bug** (`structure_data` → `structural_data`) so the RR-boost engages. Restores the designed safety valve.
2. **Raise `apex_min_flip_confidence`** to 0.85 so flips require either:
   - Raw confidence ≥ 0.85 (DeepSeek very sure), OR
   - Raw confidence ≥ 0.70 + RR-boost engaged (structural confirmation).
3. **Add asymmetric thresholds** based on the data: Buy→Sell needs higher conviction (0.95) than Sell→Buy (0.70). The data shows Buy→Sell flips destroy WR; Sell→Buy flips help.
4. **Add code-level "<5 trades in regime" gate** for the target direction. Breaks the feedback loop by enforcing the prompt rule that DeepSeek misreads.
5. **Add `APEX_FLIP_DECISION` structured log** at every direction-modification point (spec Rule 6).

What this does NOT do:
- Doesn't touch XRAY's flip logic — XRAY is correctly identifying structural mismatches.
- Doesn't touch the scanner's counter-trade labels (out of scope).
- Doesn't touch brain's prompt (out of scope).

What this does NOT solve completely:
- XRAY will still flip APEX-flipped Sell→Buy trades back to Sell when structural asymmetry is extreme. APEX-side tuning reduces the volume of those cases (fewer APEX flips overall) but does not eliminate XRAY's reversal mechanism.
- True counter-trade respect requires a `respect_counter_trade` flag check in XRAY's flip block — a small follow-up if the operator endorses counter-trade preservation.

## 8. Out-of-Scope Confirmation

- Read brain code only to confirm counter-trade rendering — no edits to brain.
- Read structure engine to confirm symmetric placement code — no edits there either.
- Scanner counter-trade label generation is Layer 1, out of scope.
