# Issue B Phase 2 — Operator Report: APEX OpenRouter Failures

> Operator decision document. Plain prose, h1/h2/h3 structure. Phase 1 evidence in `issueB_phase1_synthesis.md`.

## Root cause

APEX OpenRouter calls intermittently receive HTTP 200 responses with an empty `choices` array (or empty `content` string within `choices[0].message`). The QwenClient at `src/apex/qwen_client.py:159–163` raises `APEXOptimizationError("OpenRouter response has no choices")` and the optimizer's broad `except Exception` at `src/apex/optimizer.py:396–426` immediately falls through to `_fallback`, which preserves Claude's exact pre-APEX directive (direction, SL price, TP price, size, leverage) and sets `is_fallback=True`. There is no retry by deliberate architectural choice (`qwen_client.py:7–9` comment: "NO RETRY").

The single material difference between APEX (8 unique fails over 4 days) and TIAS (0 "no choices" fails over the same period) is: TIAS includes `response_format: {"type": "json_object"}` in its OpenRouter payload (`src/tias/deepseek_client.py:129`) and APEX does not. Forcing JSON mode is the most-evidence-backed mitigation.

## Severity

Across all available logs (last 4 days):
- 8 unique `APEX_FAIL_UNEXPECTED` events.
- Two failure modes, both empty-content variants: 4× "no choices" (2026-05-08), 4× "invalid/empty content" (2026-05-05/07).
- 4 of 8 events clustered in a 16-minute window on 2026-05-08 (15:33 + 15:49 trio within 4 seconds) — strong transient-upstream-incident signature.
- Failure rate in the 13:00–16:00 window on 2026-05-08: 4 fail / 30 calls ≈ 13%. Across 4 days: sub-1% baseline.
- Coin distribution is broad: EGLDUSDT, ORCAUSDT, LDOUSDT, ONDOUSDT, BCHUSDT, OPUSDT, RENDERUSDT — not symbol-specific.

## Audit reframing (operator should know)

The audit said: "Trades placed without proper APEX optimization use whatever default direction and sizing the system has, not what XRAY's analysis would have produced."

Reality: when `is_fallback=True`, layer_manager (`src/core/layer_manager.py:1457`) returns Claude's **exact pre-APEX directive** unchanged. That directive carries XRAY's grounded direction and Claude's chosen SL price, TP price, position size, and leverage. The trade is not placed with random defaults; it is placed with Claude's analysis untouched, just without APEX's optional flip-validation, sizing tweak, TP-cap reduction, or leverage adjustment on top. The placeholder `sl_pct=2.0` and `tp_pct=1.5` in `_fallback` are never read.

Loss when APEX fails: APEX flip on weak Claude direction (rare, gated by 0.70 confidence floor); APEX TP-cap reduction (most relevant in high-volatility regimes); APEX leverage-class clamp; APEX size adjustment (already clamped by `gate_apex_size_cap_mult=1.5×`). All real, but not "trade with no analysis".

## Hard constraints (per the prompt)

- APEX must continue providing optimization when up.
- The fallback must be safer than current defaults if optimization fails.
- No LLM provider switch.
- No significant API cost increase.
- No significant latency increase.
- "Using defaults" should be rare, not common.

## Solution options

Each option was derived from the investigation, not pre-committed in the plan.

### Option 1 — Add `response_format: json_object` (minimal change, evidence-backed)

The single biggest difference between APEX and TIAS. Add it to the qwen_client payload and audit log content keys.

Changes:
- Add `"response_format": {"type": "json_object"}` to the request payload in `src/apex/qwen_client.py:131`.
- Add a Phase-3 verification step: confirm the model's response now satisfies JSON mode without triggering [vllm bug #41132](https://github.com/vllm-project/vllm/issues/41132) (DeepSeek V3.2 with reasoning enabled emits structured payload inside `reasoning` field). If the bug fires, add `"reasoning": {"enabled": false}` (one-line fix).

Pro: smallest possible change. TIAS's empirical evidence (0 "no choices" failures) supports this strongly. One commit.
Con: doesn't help with transient incidents that hit BEFORE the model emits any choices (gateway-side wobble). Doesn't add diagnostics.

### Option 2 — Add capped retry on transient failure modes

Wrap the QwenClient call with a 1-attempt retry (total 2 attempts) for "no choices" and JSON-parse failures. Skip retry for HTTP non-200 (likely persistent: auth, model-not-found, etc.) and for the architectural "DeepSeek connection error" path.

Changes:
- Tag `APEXOptimizationError` instances raised in qwen_client at lines 161, 166, 225 (the empty-choices, empty-content, and invalid-JSON sites) with a `retryable=True` flag — mirrors the TIAS pattern at `src/tias/deepseek_client.py:148/153`.
- In `optimizer.py:233`, wrap the `optimize()` call with a small retry helper (1 retry, 0.7 s sleep) that respects the `retryable` flag.
- Update the architectural comment at `qwen_client.py:7–9` to reflect "1 capped retry on transient empty-content modes; no retry on HTTP errors".

Pro: addresses the 15:33–15:49 cluster pattern directly. Bounded latency cost (max +0.7 s + 1 attempt latency, ~+700–1500 ms total worst case for one retried failure). Bounded API cost (1 extra call per retried attempt). Maintains the "APEX never blocks" guarantee — fallback still fires after retry exhaustion.
Con: more code surface than Option 1. Test surface widens.

### Option 3 — Capture raw response body on failure (pure observability)

Stash `raw_body[:1000]` on the `APEXOptimizationError` before raising, and emit it in `APEX_FAIL_UNEXPECTED`. No semantic change; just better logs.

Changes:
- Add a `raw_body` attribute to `APEXOptimizationError` (qwen_client.py:36–44).
- At every raise site (lines 147, 155, 161, 166, 225), pass `raw_body=raw_body[:1000]`.
- In `optimizer.py:422`, include `raw_body=...` in the log line — truncated, no API key (the body has no key, only Authorization header) but be defensive.

Pro: zero behavioural change. Next incident is diagnosable from a single log line. Smallest implementation surface in terms of risk.
Con: by itself, doesn't reduce the failure rate. Pairs naturally with Options 1 and 2.

### Option 4 — Smarter fallback (refuse direction flips, conservative size)

When `_fallback` fires, mark the trade with a "no-APEX-optimization" tag that downstream consumers can use to:
- Refuse any APEX-driven flip (already true by definition: `was_flipped=False`).
- Cap leverage at 3 (the already-default).
- Cap size at the lesser of (Claude's size, settings.fallback_size_cap_usd default 600).

Changes:
- Add `apex_fallback_size_cap_usd` setting (default 600 — same as current placeholder).
- In `_fallback` (optimizer.py:622–646), apply the cap to `position_size_usd`.

Pro: hardens the fallback against any "Claude got the size wrong AND APEX couldn't catch it" scenario.
Con: this changes operator-visible behaviour even when fallback fires for benign reasons (e.g., TIAS data tier 3 → use Claude's size). Operator's aggressive-exploitation philosophy disfavours unprompted size reductions. Recommend NOT taking this option without explicit operator preference.

### Option 5 — Fallback model

Primary model fails → secondary model attempt. Many implementations exist; OpenRouter natively supports `models: [primary, fallback]` for routing.

Changes:
- Wire `APEXSettings.fallback_model` (currently identical to primary at `settings.py:1783`) to a different reliable model, e.g. `anthropic/claude-haiku-4.5`. Update qwen_client to send `models: [primary, fallback]` instead of `model: primary`.

Pro: highest resilience. Even if DeepSeek V3.2 has a 100% outage, APEX still works.
Con: cost differs across models (Claude Haiku 4.5 is cheaper input, more expensive output than DeepSeek; behaviour differs — APEX prompts were tuned for DeepSeek; flip thresholds and TP recommendations may shift). Adds model-output validation surface.

### Option 6 — Hybrid (Options 1 + 2 + 3)

Add JSON mode (root-cause fix) + capped retry (transient incident smoothing) + raw-body capture (next-incident diagnostics). Three commits, none individually large.

Pro: addresses all three of the ranked root causes simultaneously. Each piece is independently revertable.
Con: more code surface than any single option. Highest test surface.

## My recommendation

**Option 6 (hybrid: JSON mode + capped retry + raw-body capture).** Reasoning:

1. JSON mode is the strongest evidence-backed mitigation (TIAS's 0 vs APEX's 8). Cheap one-line fix; biggest expected reduction in baseline rate.
2. Capped retry addresses the cluster pattern (4 events in 16 minutes) which JSON mode alone won't smooth over — those failures may have been gateway-side, not model-side.
3. Raw-body capture is a permanent insurance policy: the next incident is diagnosable without speculation, at zero behavioural cost.
4. All three preserve the architecture's "APEX never blocks a trade" invariant — `is_fallback=True` still fires when retries exhaust.
5. Combined cost increase: ~+5% input tokens at steady state (retries are rare), +0.7 s worst-case latency on retried events. Well within the constraints.

If you want to be more conservative, Option 1 alone would likely cut "no choices" rate substantially and is the smallest change. Option 3 alone would buy diagnostics for whatever the residual rate ends up being.

## Implementation plan if Option 6 is approved

Three atomic commits, independently revertable:

1. **Phase 3a — JSON mode.**
   - Edit `src/apex/qwen_client.py:131` payload to include `"response_format": {"type": "json_object"}`.
   - Update module docstring at lines 1–14 to reflect JSON-mode contract.
   - Atomic commit: `fix(issueB/3a): request response_format json_object from OpenRouter (matches TIAS)`.
   - Tests added: payload-shape unit test asserts `response_format` key with correct value; mocked-response test covers existing parse paths still work with JSON mode payload.

2. **Phase 3b — Capped retry on transient empty-content modes.**
   - Add `retryable: bool = False` to `APEXOptimizationError.__init__` (qwen_client.py:36–44), defaulting False (no behavioural change for non-tagged sites).
   - At qwen_client.py:161, 166, 225 raise with `retryable=True`. At 147, 155, 196, 200, 202: keep `retryable=False`.
   - Wrap the `optimize()` call in `optimizer.py:233` with a small inline retry loop (max 2 attempts, fixed 0.7 s sleep). Respects `retryable`. Emits `APEX_RETRY_ATTEMPT | sym=… attempt=1 backoff_ms=700 err='…'` on each retry.
   - Update qwen_client.py:7–9 docstring comment.
   - Atomic commit: `fix(issueB/3b): retry once on transient empty-content modes`.
   - Tests added: retryable=True path retries and succeeds on second attempt; retryable=False path falls through immediately; retryable=True path falls back after exhaustion; APEX_RETRY_ATTEMPT log emitted exactly once per retry.

3. **Phase 3c — Raw-body capture on failure.**
   - Add `raw_body: str | None = None` attribute to `APEXOptimizationError`.
   - At each raise site in qwen_client.py (lines 147, 155, 161, 166, 196, 200, 202, 225), pass `raw_body=raw_body[:1000]` where `raw_body` is in scope.
   - In `optimizer.py:422–425`, include `body_preview=...` (first 200 chars, redacted of any "Authorization"-like tokens with a simple regex) in `APEX_FAIL_UNEXPECTED`.
   - Atomic commit: `fix(issueB/3c): capture raw body on APEX failure for diagnostics`.
   - Tests added: failed-call captures raw_body on the exception; log line includes body_preview field; redaction strips any Bearer/Authorization-like substrings.

Total: 3 atomic commits.

## Verification (Phase 4)

- Run 24 hours post-deploy (failure rate is sparse; need a longer window than Issue A's 4-6 hours).
- Compare against the 4-day baseline:
  - `APEX_FAIL_UNEXPECTED` rate per 1000 calls: target ↓.
  - `APEX_RETRY_ATTEMPT` count: should be non-zero whenever a transient blip occurs (without retry success, would-have-failed).
  - `APEX_OK` rate: should rise (retries that succeed).
  - `using_defaults=Y` rate: should drop.
  - Distribution by coin: should remain broad (no symbol-correlation).
  - `body_preview` field present on every fail event.
- Specifically watch the previously-affected coins (EGLDUSDT, ORCAUSDT, LDOUSDT, ONDOUSDT, BCHUSDT, OPUSDT, RENDERUSDT).
- Edge cases: what happens during a real OpenRouter outage (sustained 5xx)? Retries should fail fast with non-retryable HTTP errors, fallback fires; no retry storm. Verify with a synthetic test pointing the client at an invalid host.

## Discrepancies surfaced (added to discrepancies.md)

| # | Topic | Audit/memory | Reality |
|---|---|---|---|
| B-1 | APEX model | `deepseek/deepseek-chat-v3-0324` | `deepseek/deepseek-v3.2` (`settings.py:1782`) |
| B-2 | Affected coins on 2026-05-08 | EGLDUSDT, ORCAUSDT, LDOUSDT (×2) | EGLDUSDT, ORCAUSDT, LDOUSDT, **ONDOUSDT** (each ×1 in window) |
| B-3 | "Using defaults" framing | implies random defaults | reality preserves Claude's exact pre-APEX directive; the placeholder sl_pct/tp_pct in `_fallback` are dead code |
| B-4 | Reliability gap with TIAS | not flagged | TIAS uses `response_format: json_object`, has 0 "no choices" failures over same period; APEX has 8. Single material payload difference. |
| B-5 | Retry mechanism | "is the project's retry logic insufficient?" | NO retry by architectural choice (`qwen_client.py:7–9`). |

## Operator's decision needed

Choose:
- Option 1 — JSON mode only (smallest change).
- Option 2 — Capped retry only.
- Option 3 — Raw-body capture only (pure observability).
- Option 4 — Smarter fallback (note: I do NOT recommend this; conflicts with aggressive-exploitation aim).
- Option 5 — Fallback model (highest resilience, behavioural risk).
- Option 6 — Hybrid 1+2+3 (recommended).
- A modified version of any of the above.
