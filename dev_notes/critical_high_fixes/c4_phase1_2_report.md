# CRITICAL-4 Phase 1+2 — Investigation and Operator Discussion

## Audit reference

CRITICAL-4 — Telegram alert spam: 143 critical + 175 warning alerts in 2.85h (50 critical/h).

## Phase 0 baseline + post-fix projection

| Source | Audit count (3h) | After CRITICAL-1+5 ship | After CRITICAL-4 ship |
|---|---|---|---|
| DL_TRADE_SUSPECT | 49 | ~0 (CRITICAL-1 fix) | ~0 |
| BYBIT_DEMO_SET_SL_FAIL | 8 (KATUSDT 5x + RENDERUSDT 2x + ICPUSDT 1x) | ~0 (CRITICAL-5 fix) | ~0 |
| BYBIT_DEMO_TIMESTAMP_FAIL | 0 in this window | unchanged | unchanged |
| Other CRITICAL | ~86 (unknown breakdown) | ~86 | depends on dedup quality |

CRITICAL-1 + CRITICAL-5 already remove the 57 audit-named alerts. The remaining ~86 critical events (in the 2.85h window) need structural defenses against future regressions.

## Investigation — dedup behaviour

`src/alerts/throttle.py:82-85`:

```python
@staticmethod
def content_hash(text: str) -> str:
    """SHA256 hash of message content for dedup."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]
```

The hash is computed over the FULL message text. Sample KATUSDT retries:

```
BYBIT_DEMO_SET_SL_FAIL | sym=KATUSDT sl=0.01015569 err=...StopLoss:1015000 set for Sell position should greater base_price:1017000??LastPr | tid=t-KATUSDT-sniper
BYBIT_DEMO_SET_SL_FAIL | sym=KATUSDT sl=0.01015569 err=...StopLoss:1015000 set for Sell position should greater base_price:1017100??LastPr | tid=t-KATUSDT-sniper
BYBIT_DEMO_SET_SL_FAIL | sym=KATUSDT sl=0.01015569 err=...StopLoss:1015000 set for Sell position should greater base_price:1017250??LastPr | tid=t-KATUSDT-sniper
```

The base_price differs each tick because Bybit's mark price moves. SHA256 of these messages produces different 16-char hashes → dedup misses → all 5 fire.

`src/alerts/throttle.py:37`: `if priority == AlertLevel.CRITICAL: return True` — CRITICAL bypasses the rate gate too. So CRITICAL-tagged spam has NO upper bound.

## Investigation — what would dedup correctly

If the hash were over the NORMALIZED message (numeric values replaced with placeholders), the three KATUSDT retries above would produce the same hash and dedup would catch them within the 5-min TTL window. Same applies to any other retry scenario that differs only in numeric details (timestamps, prices, IDs).

Tag-prefix-only dedup (audit's per-symbol cooldown suggestion) is more aggressive but risks hiding genuinely-different alerts that share the same prefix. Numeric-normalization dedup is more conservative and addresses the specific audit failure mode.

## Three options considered

### Option A — Numeric normalization in content_hash (recommended)

Add `normalized_content_hash` in throttle.py: replaces digit-runs and floats in the message with `#NUM` before hashing. Switch `_send` in alert_manager.py to use the normalized hash.

Pros:
- Catches ALL retry storms that differ only in numeric details
- Preserves dedup for genuinely-different messages
- Single throttle change + single alert_manager change
- Conservative — doesn't drop alerts that look different in non-numeric ways

Cons:
- Slightly more compute per alert (regex sub vs raw hash) — negligible at 50/h
- Would dedup a sequence of unique-but-numerically-different alerts (e.g., 3 different ORDER_REJECT codes) — but each represents the same underlying alert family within the 5-min window, which is correct

### Option B — Per-tag cooldown system

Add a tag-prefix cooldown table (e.g., SET_SL_FAIL cools 300s after one fire). Skip alerts whose tag is in cooldown.

Pros:
- Bounds spam regardless of message content

Cons:
- Risks hiding distinct symbols' failures within the cooldown window (e.g., SET_SL_FAIL for KATUSDT then for ETHUSDT 60s later — second one suppressed)
- Per-symbol cooldown (audit's actual suggestion) needs structured tag parsing — bigger surface area

### Option C — Both A and B

Pros: defense-in-depth.
Cons: doubles the change surface; over-engineering when CRITICAL-1+5 already remove the audit's named spam sources.

## Recommendation

**Option A.** Address the structural dedup defect with the minimum-blast-radius change. The audit's named spam sources auto-resolve via CRITICAL-1+5; numeric normalization protects against any future regression that produces retry-style alerts. Operator can revisit Option B (per-tag cooldown) if Phase 4 verification reveals residual spam patterns.

## Implementation plan

Single atomic commit. Files modified:

1. `src/alerts/throttle.py` — add `normalized_content_hash(text: str) -> str` static method. Uses `re.sub(r'[\d]+(?:\.[\d]+)?', '#NUM', text)` to replace integers and floats. Existing `content_hash` kept for back-compat (and tests).

2. `src/alerts/alert_manager.py:190` — switch `AlertThrottle.content_hash(message)` to `AlertThrottle.normalized_content_hash(message)`.

3. `tests/test_critical4_alert_dedup.py` — 5 tests:
   - Same alert text → same normalized hash (sanity)
   - Different numeric values → same normalized hash (the fix)
   - Different non-numeric content → different normalized hash (no over-dedup)
   - Hex address normalization works
   - Known KATUSDT retry pair → identical hash

## Open questions

None blocking. The 175 warning alerts (also reported in audit) flow through the throttle which DOES rate-limit non-CRITICAL — they will benefit from numeric normalization too.
