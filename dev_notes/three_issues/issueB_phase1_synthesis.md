# Issue B Phase 1 — Synthesis (Investigation Findings)

> Consolidated investigation. Replaces the 8 separate deliverables (optimizer anatomy / OpenRouter integration / failure response sample / provider docs / default behaviour / retry logic / failure pattern / synthesis) — same content, single document for review efficiency. All claims have file:line evidence.

## Root cause (single sentence)

The APEX OpenRouter calls intermittently receive HTTP 200 responses with an empty `choices` array; the QwenClient at `src/apex/qwen_client.py:159–163` raises `APEXOptimizationError("OpenRouter response has no choices")`, the optimizer catches it at `src/apex/optimizer.py:396–426` (broad `except Exception`), logs `APEX_FAIL_UNEXPECTED`, and returns Claude's original directive unchanged via `_fallback`. There is **no retry by architectural choice** (qwen_client.py:7–9 comment: "NO RETRY"), so any transient upstream wobble immediately becomes `using_defaults=Y`. The closest analogue (TIAS using the same OpenRouter endpoint and DeepSeek model) does **not** see "no choices" failures because it requests `response_format: {"type": "json_object"}` (`src/tias/deepseek_client.py:129`) — APEX does not.

## Evidence chain

### B.1.1 — APEX Optimizer Anatomy

- File: `src/apex/optimizer.py` (875 lines).
- Class: `TradeOptimizer` at line 36.
- Entry: `optimize(directive, plan)` at line 61.
- 10-step flow documented at lines 7–17: enabled check → translate keys → assemble package → price-validate → tier-check → direction-lock → TP-cap → build prompts → DeepSeek call (line 233) → parse → constraints → log → return.
- DeepSeek call site: `src/apex/optimizer.py:233–240`. No try/except wrapping just the call — covered by the outer broad `except Exception` at line 396.
- Outer exception handler: lines 396–426. Special-cases `is_timeout` for regime fallback when `regime_trades >= min_regime`; everything else (including "no choices") falls straight through to `_fallback`.
- Fallback: `_fallback(directive, reason)` at lines 598–646 — sets `is_fallback=True` on the returned `OptimizedTrade`.
- All callers: `src/core/layer_manager.py:1372` (the only call site in production).

### B.1.2 — OpenRouter Integration

- File: `src/apex/qwen_client.py` (248 lines).
- Class: `QwenClient` at line 47.
- HTTP wrapper uses `aiohttp.ClientSession` (lazy, persistent) — created in `_get_session` at line 78 with the four required headers: Authorization, Content-Type, HTTP-Referer (`https://github.com/trading-intelligence-mcp`), X-Title (`APEX-TradeOptimizer`).
- Endpoint: `https://openrouter.ai/api/v1/chat/completions` (line 64; matches `APEXSettings.api_url` at `src/config/settings.py:1781`).
- Request payload (lines 123–131):
  ```python
  {
    "model": model,                     # "deepseek/deepseek-v3.2"
    "messages": [
      {"role": "system", "content": system_prompt},
      {"role": "user",   "content": user_prompt},
    ],
    "temperature": temperature,         # 0.2
    "max_tokens": max_tokens,           # 800
  }
  ```
  **No `response_format`** field. Compare to TIAS's identical wrapper at `src/tias/deepseek_client.py:120–132` which adds `"response_format": {"type": "json_object"}` (line 129).
- Status check: line 146 raises `APEXOptimizationError` for non-200.
- JSON body parse: lines 152–157 raises on `JSONDecodeError`.
- **"no choices" raise**: lines 159–163, the audit-confirmed error site.
- Empty content raise: lines 164–168 (`message.content == ""`).
- Content JSON parse: line 170 → `_parse_json` (lines 206–234) which raises on JSONDecodeError or non-dict.
- Cost tracking: lines 174–182 (per-million-token rates at lines 32–33).

### B.1.3 — Failure Response Sample

The current code captures **only `str(e)[:120]`** of the exception message in `APEX_FAIL_UNEXPECTED` (optimizer.py:422–425). The raw response body is not retained anywhere on the failure path. We can confirm three observable forms across logs:

1. **Empty choices** — `err='OpenRouter response has no choices'` (qwen_client.py:161). 4 unique events on 2026-05-08.
2. **Invalid JSON content** — `err='DeepSeek (deepseek/deepseek-v3.2) returned invalid JSON: Expecting value: line N column M (char K) | content[:200]=...'` (qwen_client.py:225–227). 3 unique events on 2026-05-05/07. Content samples:
   - BCHUSDT 2026-05-05 21:40:25 → `content[:200]=\`\`` (literal markdown fence, no body).
   - OPUSDT 2026-05-07 08:08:38 → `content[:200]=\`` (single fence, no body).
   - RENDERUSDT 2026-05-07 10:41:30 → `content[:200]={` (one open brace, body truncated).

The "no choices" form has zero diagnostic body in logs — the actual JSON OpenRouter returned (status, error fields, usage block) is unrecoverable. **Phase 3 must add raw-body capture on failure** so the next incident is diagnosable without speculation.

### B.1.4 — Provider Docs (OpenRouter + DeepSeek-V3.2)

Web search highlights:

- **Empty `choices` is a documented OpenRouter scenario** in streaming responses (final chunk often has empty choices, per [OpenRouter Streaming docs](https://openrouter.ai/docs/api/reference/streaming)). APEX does NOT use streaming — but OpenRouter's [Errors and Debugging guide](https://openrouter.ai/docs/api/reference/errors-and-debugging) does not promise non-streaming responses always have choices populated, and HTTP 200 with empty body is a known transient mode.
- **JSON mode is supported** via `response_format: {type: 'json_object'}` per [OpenRouter Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs). Forcing JSON mode has historically cut empty-content rates upstream.
- **DeepSeek V3.2 known bug**: per [vllm-project/vllm#41132](https://github.com/vllm-project/vllm/issues/41132), DeepSeek V3.2 with structured output combined with reasoning/thinking mode emits the structured payload INSIDE a `reasoning` field instead of `content`, manifesting as empty `content`. Recommendation in the bug thread: disable reasoning when requesting JSON mode (set `reasoning.enabled = false`).
- **Response Healing plugin** ([OpenRouter docs](https://openrouter.ai/docs/guides/features/plugins/response-healing)) auto-recovers malformed JSON. Optional, increases provider dependency.

### B.1.5 — Default Behaviour (`is_fallback=True` Path)

`_fallback` (`src/apex/optimizer.py:598–646`) returns:

```python
OptimizedTrade(
    symbol=symbol,
    direction=original_dir,                 # Claude's direction
    sl_pct=2.0,                             # placeholder — IGNORED
    tp_pct=1.5,                             # placeholder — IGNORED
    position_size_usd=float(directive.get("size_usd", 600)),
    leverage=int(directive.get("leverage", 3)),
    ...
    original_direction=original_dir,
    original_sl=float(directive.get("stop_loss_price") or directive.get("sl") or 0),
    original_tp=float(directive.get("take_profit_price") or directive.get("tp") or 0),
    original_size=float(directive.get("size_usd", 600)),
    is_fallback=True,
)
```

Consumer: `src/core/layer_manager.py:1457`:
```python
if getattr(optimized, "is_fallback", False):
    return original   # ← Claude's exact directive, no APEX changes
```

**This means `using_defaults=Y` does NOT mean "default direction/size/leverage from a config" — it means "Claude's exact pre-APEX directive, untouched."** The placeholder `sl_pct=2.0` and `tp_pct=1.5` in `_fallback` are never read by anyone, because `is_fallback=True` short-circuits in layer_manager before any pct→price conversion.

The audit/prompt's framing — "trades placed without APEX optimization use whatever default direction and sizing the system has, not what XRAY's analysis would have produced" — is misleading. The trade IS placed with XRAY/Claude's direction and Claude's chosen SL/TP/size/leverage, just without APEX's optional flip-validation, sizing tweak, TP-cap reduction, or leverage adjustment. That's a quality-degradation, not a "random defaults" failure.

### B.1.6 — Retry Logic

**No retry, by deliberate architectural choice.** Three load-bearing pieces of evidence:

1. `src/apex/qwen_client.py:7–9` (module docstring): *"NO RETRY: APEXOptimizationError has no retryable flag; callers fall back to Claude's original parameters immediately on any failure. APEX failure NEVER blocks a trade."*
2. `APEXOptimizationError` (qwen_client.py:36–44): no `retryable` attribute. Compare to TIAS's `TIASAnalysisError` which has `retryable=True/False` per HTTP code.
3. `optimizer.py:396–426` outer handler: catches all exceptions, special-cases `is_timeout` only for regime fallback (a different kind of fallback, not a retry), then logs and falls back.

The `src/core/decorators.py` `async_wrapper` retry decorator (used elsewhere for Finnhub etc.) is NOT applied to APEX. Finnhub uses `max_attempts=3, delay=2, backoff=2.0` per memory.

### B.1.7 — Failure Pattern (last 4 days available)

Total `APEX_FAIL_UNEXPECTED` events across all logs (workers/brain/combined): **8 unique** events, observed twice each because logs route to both workers.log and brain.log.

| Date | Sym | Time | Error class | Notes |
|---|---|---|---|---|
| 2026-05-05 | BCHUSDT | 21:40:25 | invalid JSON (`\`\``) | Single event, no cluster |
| 2026-05-07 | OPUSDT | 08:08:38 | invalid JSON (`\``) | Isolated |
| 2026-05-07 | RENDERUSDT | 10:41:30 | invalid JSON (`{`) | Isolated |
| 2026-05-08 | EGLDUSDT | 15:33:35 | no choices | Cluster start |
| 2026-05-08 | ORCAUSDT | 15:49:23 | no choices | Cluster |
| 2026-05-08 | LDOUSDT | 15:49:25 | no choices | Cluster (+2s) |
| 2026-05-08 | ONDOUSDT | 15:49:26 | no choices | Cluster (+1s) |

**Two distinct failure modes, both empty-content variants:**

- **Empty choices array** (4 events on 2026-05-08): the JSON body's `choices` field is `[]` or absent. All 4 events on 2026-05-08, three within 4 seconds at 15:49 — strong upstream-incident signature.
- **Empty/malformed content** (3 events on 2026-05-05/07): `body.choices[0].message.content` was `\`\``, `\``, or `{` — the model returned markdown fence opens without bodies, or a single-character "{" with no rest. Likely the same underlying model degradation as "no choices", just a different stage of OpenRouter's response pipeline.

**Coin distribution:** EGLDUSDT, ORCAUSDT, LDOUSDT, ONDOUSDT, BCHUSDT, OPUSDT, RENDERUSDT — broad, not concentrated. Nothing about specific symbols correlates with failure.

**Total APEX call rate (window context):** 26 successful APEX_OK + 4 fail = 30 in the 13:00–16:00 window; 13.3% failure rate that hour. Across 4 days of available logs: 8 unique fails over likely hundreds of calls — sub-1% baseline rate, with bursty clustering.

**TIAS comparison:** TIAS uses the same OpenRouter endpoint, the same DeepSeek-V3.2 model, the same headers, and runs at similar volume. TIAS_FAIL_UNEXPECTED count over the same 4-day log window: **1 event** (RUNEUSDT, empty `err`). TIAS "no choices" specifically: **0 events**. The single material difference between the two clients is the `response_format: {"type": "json_object"}` field in TIAS's payload (line 129 of `deepseek_client.py`).

### B.1.8 — Synthesis: ranked root causes

#### Root cause #1 (PRIMARY) — Missing `response_format` JSON mode

The strongest evidence-backed difference between APEX (8 unique fails / 4 days) and TIAS (0 "no choices" fails) is APEX's absent `response_format` field. JSON mode forces the model to emit a parseable JSON structure; without it, the upstream pipeline can return empty content (DeepSeek V3.2 has a documented quirk where reasoning text leaks into the `content` field when reasoning is enabled, manifesting as empty content from the consumer's side; see vllm bug #41132). With JSON mode, the upstream contract is stricter and OpenRouter's gateway is more likely to reject empty payloads upstream rather than ship them through.

Caveat: the [DeepSeek-V3.2 + JSON mode + reasoning bug](https://github.com/vllm-project/vllm/issues/41132) means JSON mode alone may not be sufficient; combining with `reasoning.enabled = false` is the safer recipe. This will be tested in Phase 3.

#### Root cause #2 (SECONDARY) — No retry buffer for transient upstream wobble

The 15:33–15:49 cluster (4 events in 16 minutes, 3 within 4 seconds) is a textbook transient incident pattern: the same upstream backend has a brief outage and every concurrent request fails. With even a single 1-second-backoff retry, all 4 events would have likely succeeded on the second attempt. The architectural "NO RETRY" decision in qwen_client.py:7–9 was made when APEX was new and operators feared retry storms. Today, with TIAS's 1-retry behaviour for HTTP 429/503 (deepseek_client.py:148/153) showing no storm, APEX can safely add bounded retry for the failure modes that empirically transient: empty choices and empty content.

#### Root cause #3 (CONTRIBUTING) — Zero diagnostic on failure

`str(e)[:120]` of the exception is all we capture. The actual JSON body — which would tell us whether OpenRouter's gateway returned a useful `error` field, what `usage` looked like, whether `model` echoed our model id correctly — is unrecoverable. Every incident must be re-diagnosed from scratch. Phase 3 must add raw-body capture on failure so future incidents are root-causable from logs alone.

#### Root cause #4 (LATENT) — Fallback semantics are documented but easily mis-described

`is_fallback=True` preserves Claude's directive unchanged; not "defaults". Multiple downstream conversations (audit, plan, this investigation) have at one point or another mis-stated this. The fallback code is correct; the operator-facing surface (log line `using_defaults=Y`) implies a worse degradation than reality. Cosmetic but worth fixing in the same observability pass: log the actual preserved direction/size/SL/TP next to `using_defaults=Y`.

## Discrepancies surfaced

| # | Topic | Audit/memory | Reality | Material? |
|---|---|---|---|---|
| 1 | APEX model name | `deepseek/deepseek-chat-v3-0324` | `deepseek/deepseek-v3.2` (`settings.py:1782`) | Cosmetic |
| 2 | Failure-coin list | EGLDUSDT, ORCAUSDT, LDOUSDT (×2) | EGLDUSDT, ORCAUSDT, LDOUSDT, **ONDOUSDT** (each ×1 in window) | Cosmetic |
| 3 | "Trades placed without APEX use whatever defaults the system has, not XRAY's analysis" | implies random defaults | Reality: `is_fallback=True` returns Claude's exact pre-APEX directive (XRAY-grounded direction, Claude's SL/TP/size/leverage). | Material reframe — degradation is real but smaller than implied |
| 4 | "Sizing is default, not optimized" | implies APEX produces sizing | Reality: APEX rarely changes sizing meaningfully (clamped by `gate_apex_size_cap_mult=1.5×`, `max_position_size_usd=1200`). Loss of APEX size adjustment is a small effect for most trades. | Material — fix urgency reduced |
| 5 | Failure cause | unspecified | TIAS uses JSON mode and has 0 "no choices"; APEX doesn't and has 8. Missing `response_format` is the strongest evidence-backed driver. | Material — fix direction |
| 6 | Retry mechanism | "is the project's retry logic insufficient?" | No retry at all by architectural choice (qwen_client.py:7–9). | Material |

## Why current approach is wrong

The architectural "NO RETRY" comment was written assuming all failure modes are persistent (e.g., authentication, prompt format) and retries would be wasted compute. Real-world failure mode: bursty 4-events-in-16-minutes clusters on 2026-05-08 indicate transient gateway/backend wobbles that retry would smooth over. Combined with no JSON mode (silently elevating empty-response rate vs TIAS) and no diagnostic capture, the system is engineered to maximise the observability cost of every failure.

## What the fix must achieve

1. Reduce `APEX_FAIL_UNEXPECTED` rate (especially "no choices" mode).
2. Reduce `using_defaults=Y` rate.
3. When fallback does fire, the operator must have enough log content to diagnose the next occurrence without me being there.
4. Preserve "APEX failure NEVER blocks a trade" guarantee — every fix must keep `is_fallback=True` as the terminal state when retries are exhausted.
5. Cost increase ≤ +20% per APEX call at steady state (retries cost extra, but most calls succeed first try).
6. Latency increase ≤ +1 s for the slowest case (one retry with 0.5–1s backoff).

Solution options enumerated in `issueB_phase2_report.md`.

## Sources

- [OpenRouter API Reference](https://openrouter.ai/docs/api/reference/overview)
- [OpenRouter Structured Outputs guide](https://openrouter.ai/docs/guides/features/structured-outputs)
- [OpenRouter Streaming docs (notes empty-choices in final chunk)](https://openrouter.ai/docs/api/reference/streaming)
- [OpenRouter Errors and Debugging](https://openrouter.ai/docs/api/reference/errors-and-debugging)
- [vllm-project/vllm#41132 — DeepSeek V3.2 structured output + reasoning bug](https://github.com/vllm-project/vllm/issues/41132)
- [DeepSeek V3.2 OpenRouter pricing/details](https://openrouter.ai/deepseek/deepseek-v3.2)
- [OpenRouter Response Healing plugin](https://openrouter.ai/docs/guides/features/plugins/response-healing)
