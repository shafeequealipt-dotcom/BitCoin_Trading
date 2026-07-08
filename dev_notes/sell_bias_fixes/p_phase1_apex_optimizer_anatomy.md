# PRIMARY Issue — Phase 1 Step P.1.1: APEX Optimizer Anatomy

Source: `src/apex/optimizer.py` (1033 lines, current revision)
Scope: every code path that can produce or block a direction flip.
Status: read end-to-end. Investigation only — no code changes.

## 1. Top-Level Flow `TradeOptimizer.optimize()` (lines 94-494)

The `optimize()` method runs the following sequence for every directive:

| Step | Lines | Action |
|------|-------|--------|
| 1    | 122-124 | Check `_settings.enabled` — bail to fallback if disabled |
| 2    | 126-147 | Translate directive keys (stop_loss_price → sl) and enrich `plan_view`, `signal_score` |
| 3    | 148-152 | `assembler.assemble(translated)` — build the IntelligencePackage |
| 3.5  | 153-159 | Price validation — bail to fallback if `current_price <= 0` (emits `APEX_SKIP_NO_PRICE`) |
| 4    | 161-216 | Three-tier data threshold check — emits `APEX_TIER` (tier=1/2/3); tier 3 returns fallback |
| LOCK | 218-243 | **`_check_direction_lock()`** — see §3. Emits `APEX_DIR_LOCK` (INFO) and stores `_apex_lock_state` tuple |
| TPCAP | 245-271 | Volatility-aware TP cap — `_tp_cap` computed per volatility class |
| 5    | 273-274 | `build_apex_user_prompt(package)` — assembles the prompt sent to DeepSeek |
| 6    | 276-321 | `self._client.optimize(...)` — calls DeepSeek via OpenRouter. Bounded retry on retryable errors (default `apex_max_attempts=2`, backoff `apex_retry_backoff_seconds=0.7`) |
| 7    | 323-326 | `_parse_response(result, directive)` → `OptimizedTrade` (sets `was_flipped` if Qwen direction differs from Claude) |
| ENFORCE-LOCK | 328-341 | If pre-call lock was set and Qwen flipped anyway, **override back to Claude**, set `was_flipped=False`, emit `APEX_DIR_LOCK_OVERRIDE` (WARNING) |
| RR-BOOST | 343-387 | Compute `_effective_conf = raw_conf + _rr_boost` when flipped direction's structural R:R ≥ `apex_flip_rr_boost_threshold`; boost amount `apex_flip_rr_boost_amount` (0.15 default). RR boost is local — does **not** mutate `optimized.confidence` |
| FLIP-CONF | 389-409 | **`_enforce_flip_confidence(...)`** — see §4. If `_flip_revert=True`, revert direction to Claude, set `was_flipped=False`, emit `APEX_FLIP_BLOCKED` (WARNING) |
| RESIZE | 410-418 | If `optimized.was_flipped` AND `apex_block_flip_resize=True` (default), call **`_apply_flip_resize_policy(...)`** — see §5 |
| 8    | 420-427 | `_apply_constraints(optimized, coin_data)` — hard clamps on size, leverage, SL%, TP%, mode, confidence |
| TPCAP-ENF | 429-452 | Enforce volatility TP cap; emit `APEX_TP_CAP` (WARNING if reduced, DEBUG if no-op) |
| 9    | 454-471 | Track stats; call `_log_optimization(...)` → emits **`APEX_FLIP` (WARNING)** when `was_flipped=True` or **`APEX_OK` (INFO)** otherwise. Emits `APEX_TIMING` (INFO) and `APEX_DEEPSEEK_SLOW` (WARNING) if DeepSeek > 5 s |
| LOCK-STAMP | 486-491 | Stamp `_apex_lock_state` onto `optimized.is_locked` / `optimized.lock_reason` (Issue 1 fix 2026-05-11) |
| 10   | 494     | Return `OptimizedTrade` |

Exception path (lines 496-548): logs `APEX_TIMING outcome=fail`, decides whether to use regime fallback (`APEX_TIMEOUT_REGIME`) or full fallback (`APEX_FAIL_UNEXPECTED`); fallback preserves lock state.

## 2. Where The Flip Originates

The flip is set on line 631 of `_parse_response()`:

```python
was_flipped=(qwen_dir != original_dir),
```

`qwen_dir` is the `direction` field from DeepSeek's JSON response (line 572). If DeepSeek's JSON doesn't include direction or includes something other than "Buy"/"Sell", line 575-576 forces `qwen_dir = original_dir` (no flip).

There is no place in optimizer.py that *initiates* a flip — the flip is purely a function of DeepSeek's returned `direction` field. The optimizer's role is to **gate** that flip with the chain below.

## 3. `_check_direction_lock(package, claude_direction, regime)` — lines 885-931

Pre-call (before DeepSeek is even asked). Returns `(locked: bool, reason: str)`.

| Regime | Lock decision |
|--------|---------------|
| `trending_down` | ALWAYS locked. If claude=Sell → "trending_down aligns with Sell". If claude=Buy → "Claude chose Buy against trending_down (per-coin override)". |
| `trending_up`   | ALWAYS locked. Symmetric. |
| `volatile`      | Locked **unless** `_check_flip_evidence(trades, claude_direction)` returns True, i.e. opposite direction has ≥70% WR with ≥8 trades. |
| `ranging`       | NOT locked — flips allowed; confidence gate runs post-parse. |
| `dead`          | NOT locked — flips allowed; confidence gate runs post-parse. |
| any other       | NOT locked — confidence gate runs post-parse. |

`_check_flip_evidence(trades, claude_direction)` lines 870-883: opposite-direction wins ≥70% with ≥8 trades in `package.symbol_history.trades`.

Effect of the lock when active:
1. Reasoning injected into directive (lines 232-236) — DeepSeek is told "DO NOT change direction".
2. Tuple stored as `_apex_lock_state = (True, lock_reason)` (line 243).
3. After parse, if Qwen flipped anyway, override back (lines 330-341) and emit `APEX_DIR_LOCK_OVERRIDE`.

## 4. `_enforce_flip_confidence(...)` — lines 933-977

Post-parse, after DeepSeek has returned. Returns `(reverted: bool, reason: str)`.

Decision tree:
1. **Trending/volatile regime** → return `(False, "")` — already governed by pre-call lock.
2. **Direction unchanged** → return `(False, "")` — no flip to police.
3. Read `threshold = apex_min_flip_confidence` (default 0.70).
4. Use `effective_confidence` if provided (callers pass `raw_conf + rr_boost`), else raw `optimized.confidence`.
5. If `conf < threshold` → return `(True, "flip {claude}→{apex} in regime={r} blocked: conf={x}<{t}")`.
6. Else → return `(False, "")` — flip stands.

When reverted, the caller (lines 393-409):
- Sets `optimized.direction = claude_direction`
- Sets `optimized.was_flipped = False`
- Prepends "[FLIP BLOCKED conf<min] " to reasoning
- Increments `_lock_override_count`
- Emits `APEX_FLIP_BLOCKED` (WARNING) with fields `raw_conf`, `eff_conf`, `rr_boost`, `rr_chosen`, `rr_flipped`, `regime`

**Live measurement (today, ~9 h of logs): 5 APEX_FLIP_BLOCKED vs 23 APEX_FLIP. So the confidence gate fires on 18% of flip attempts; 82% pass. All 23 that passed were in `regime=ranging`.**

## 5. `_apply_flip_resize_policy(optimized, *, claude_direction, regime, symbol)` — lines 979-1032

Runs only when (a) `optimized.was_flipped=True` AND (b) `apex_block_flip_resize=True` (default). Policy:

| Qwen size vs original | Action | Tag |
|-----------------------|--------|-----|
| Within ±$0.01 of original | no-op (no log) | — |
| Qwen sized **up** (qwen > original + 0.01) | CAP back to original; mutate `position_size_usd = orig_size` | `APEX_FLIP_RESIZE_CAPPED` (WARNING) |
| Qwen sized **down** (qwen < original − 0.01) | ACCEPT (no mutation) | `APEX_FLIP_RESIZE_ACCEPTED` (INFO) |

Live evidence: 23 APEX_FLIP_RESIZE_ACCEPTED today (same count as APEX_FLIP), 0 CAPPED — DeepSeek consistently sizes flipped trades DOWN. The spec's CRVUSDT example sized $18000 → $1200 = 6.7% of original.

## 6. RR-Boost Path (the hidden lowering of the bar) — lines 343-387

When **all** conditions hold:
- Direction was flipped (qwen ≠ claude)
- Regime is not in {trending_up, trending_down, volatile}
- `package.structure_data` exists
- Both `rr_chosen` and `rr_flipped` > 0
- `_ratio = _rr_flipped / _rr_chosen >= apex_flip_rr_boost_threshold` (default 3.0)

Then `_rr_boost = apex_flip_rr_boost_amount` (default 0.15), and `_effective_conf = min(raw_conf + 0.15, 1.0)`.

Implication: if DeepSeek returns confidence 0.55 but X-RAY's structural R:R for the flipped direction is 3x the chosen direction's R:R, effective confidence becomes 0.70 — exactly the threshold. The boost can carry a flip past the gate that would otherwise have been blocked.

The boost is local — `optimized.confidence` retains the raw 0.55 for downstream consumers (gate.py, telemetry, thesis records). The `APEX_FLIP_BLOCKED` log includes both `raw_conf` and `eff_conf` so the operator can see whether the boost was the deciding factor.

## 7. Config Values (verified in `config.toml`)

| Key | Value | Notes |
|-----|-------|-------|
| `apex_min_flip_confidence` | 0.70 | Was 0.90; lowered to 0.70 in Phase 3 of dir-block-fix (2026-05-05) |
| `apex_block_flip_resize` | true | Enables `_apply_flip_resize_policy` chain |
| `apex_flip_rr_boost_threshold` | 3.0 | Structural R:R ratio that triggers the boost |
| `apex_flip_rr_boost_amount` | 0.15 | Confidence added when above threshold |
| `apex.model` | `deepseek/deepseek-v3.2` | OpenRouter routes this to `deepseek-v3.2-20251201` |
| `apex.temperature` | 0.2 (default in qwen_client.py) | Low for parameter determinism |
| `apex_max_attempts` | 2 (default) | Bounded retry on retryable errors |
| `apex_retry_backoff_seconds` | 0.7 (default) | Single-attempt backoff |

## 8. Log Emissions Catalog (file:line)

| Tag | File:line | Level | Fires when |
|-----|-----------|-------|------------|
| `APEX_TIER` | optimizer.py:180/187/207 | INFO | Per-call tier-1/2/3 classification |
| `APEX_REGIME` | 191 | INFO | Tier 2 fallback into regime data |
| `APEX_DEFAULT` | 211 | INFO | Tier 3 no-data; using Claude defaults |
| `APEX_DIR_LOCK` | 227 | INFO | Pre-call lock asserted (regime + reason) |
| `APEX_DIR_LOCK_OVERRIDE` | 331 | WARNING | Qwen tried to flip a locked trade — overridden back |
| `APEX_FLIP_BLOCKED` | 394 | WARNING | Post-parse confidence gate rejected the flip; reverted |
| `APEX_FLIP` | 802 | WARNING | Flip stands; logs claude→apex direction change, conf, regime |
| `APEX_OK` | 823 | INFO | No flip; logs current params, conf, regime |
| `APEX_SIZING` | 836 | INFO | Size changed; before/after USD |
| `APEX_LEVERAGE` | 843 | INFO | Per-call leverage decision |
| `APEX_FLIP_RESIZE_ACCEPTED` | 1024 | INFO | Flip + Qwen sized down; accepted |
| `APEX_FLIP_RESIZE_CAPPED` | 1014 | WARNING | Flip + Qwen sized up; capped to original |
| `APEX_TP_CAP` | 435/445 | WARNING/DEBUG | Volatility TP cap enforced (or no-op) |
| `APEX_SKIP_NO_PRICE` | 155 | WARNING | Current price ≤ 0; fallback |
| `APEX_SKIP` | 752 | WARNING | Generic fallback; reason included |
| `APEX_QWEN_OK` | qwen_client.py:262 | INFO | Per-call DeepSeek HTTP success |
| `APEX_RETRY_ATTEMPT` | optimizer.py:312 | WARNING | Bounded retry firing |
| `APEX_TIMING` | 467/501 | INFO | Per-call timing breakdown (assemble/deepseek/parse/constraints) |
| `APEX_DEEPSEEK_SLOW` | 480 | WARNING | DeepSeek HTTP > 5 s |
| `APEX_TIMEOUT_REGIME` | 513 | WARNING | Timeout + regime fallback path |
| `APEX_FAIL_UNEXPECTED` | 538 | ERROR | Final fallback after retry exhaustion or non-retryable error |
| `APEX_DEEPSEEK_SESSION_CLOSED` | qwen_client.py:363 | INFO | Session lifecycle close |

## 9. The Three Layers of Flip Gating — Summary

The system has three independent gates a flip must traverse:

```
DeepSeek returns direction
    ↓
Layer 1: Pre-call lock (_check_direction_lock)
    ↳ trending/volatile → blocks before call (lock_reason injected in prompt)
    ↳ ranging/dead → passes
    ↓ (if locked, override via APEX_DIR_LOCK_OVERRIDE)
Layer 2: Confidence gate (_enforce_flip_confidence)
    ↳ effective_conf < 0.70 → revert (APEX_FLIP_BLOCKED)
    ↳ effective_conf ≥ 0.70 → passes
    ↓
Layer 3: Resize policy (_apply_flip_resize_policy)
    ↳ Qwen sized up → cap to original (APEX_FLIP_RESIZE_CAPPED)
    ↳ Qwen sized down → accept (APEX_FLIP_RESIZE_ACCEPTED)
    ↓
Flip stands → APEX_FLIP logged → OptimizedTrade returned
```

## 10. Findings That Drive PRIMARY Phase 2

1. **The flip is sourced exclusively from DeepSeek's response.** Optimizer never initiates direction changes. The bias must originate either in (a) the prompt framing, (b) DeepSeek's model behavior, (c) section-4 TIAS data, or (d) the RR-boost path.

2. **Ranging/dead regimes have NO pre-call lock by design.** They are intentionally left to the confidence gate. Today, 80% of regime observations are ranging/dead — meaning 80% of trades pass through the unprotected pre-call path.

3. **The 0.70 threshold is empirically permissive.** 23 of 28 flip attempts today (82%) cleared it. DeepSeek tends to return high-confidence flips. The 5 blocks logged today are clearly real but minority.

4. **The RR boost lowers the effective bar** when X-RAY's structural R:R favors the flipped direction by ≥3x. This means a flip can pass the gate at raw confidence as low as 0.55. To verify how often this kicks in, P.1.9 (DeepSeek response inspection) should pair with grep of `APEX_FLIP_BLOCKED` lines for `rr_boost` ≠ 0.

5. **Resize policy is operating correctly** — 23/23 flips today were accepted because Qwen sized DOWN. The spec's CRVUSDT $18000 → $1200 sizing is the policy working as designed for flips. But this means flipped positions are systematically tiny relative to brain's intended size — possibly the explanation for why flipped trades have wildly different per-trade economics in the baseline data.

6. **APEX_DIR_LOCK_OVERRIDE never fires in the spec's window** — DeepSeek apparently respects the prompt-injected lock instruction in trending/volatile regimes. If it stops respecting it, the override path catches it.

7. **The `is_locked` plumbing is recent** (Issue 1 fix 2026-05-11, this branch). It surfaces the lock decision to layer_manager → strategy_worker so XRAY downstream can be suppressed when APEX has explicitly locked.

## 11. Open Questions for P.1.2 / P.1.3

- The `package.structure_data` referenced in RR-boost — where is it populated? (P.1.2 will verify assembler's data flow.)
- Section 3 / Section 4 of the prompt expose direction breakdown to DeepSeek — does the **content** of these sections systematically push toward Sell in ranging? (P.1.3 — examine sample TIAS data passed in.)
- How does APEX recover from "no choices" / empty content failures? Already mapped in qwen_client.py:198-239 — retryable flag + bounded retry — confirmed.

## 12. Out-of-scope confirmation

- No change to brain's Stage 2 prompt construction.
- No change to Claude CLI subprocess.
- This investigation read code only; no file was modified.
