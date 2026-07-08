"""DeepSeek client for APEX — calls OpenRouter for trade parameter optimization.

Design mirrors src/tias/deepseek_client.py with APEX-specific differences:

- FASTER: 30s timeout (not 45s) — the trade execution pipeline is waiting
- SMALLER: 800 max_tokens (not 1500) — just the JSON parameter output
- BOUNDED RETRY: APEXOptimizationError carries a ``retryable`` flag —
  empty-choices, empty-content, and invalid-content-JSON modes are
  retryable; HTTP non-200, non-JSON body, timeout, and connection errors
  are NOT retryable. Caller (TradeOptimizer.optimize) wraps the call in
  a single retry attempt for retryable errors. After retry exhaustion
  (or on a non-retryable error) the caller falls back to Claude's
  original parameters; APEX failure NEVER blocks a trade.
- MORE DETERMINISTIC: temperature 0.2 (not 0.3) — parameter optimization
  should be consistent, not creative
- JSON MODE: matches TIAS — ``response_format: {"type": "json_object"}``
  is sent on every request (Issue B fix 2026-05-08). Forces the upstream
  model to emit a parseable JSON object; significantly reduces the
  "no choices" / "empty content" failure modes seen pre-fix (TIAS sees
  0 such failures over 4 days; APEX saw 8 unique). Caveat: DeepSeek
  V3.2 with reasoning enabled can leak the JSON payload into a
  ``reasoning`` field (vllm bug #41132). If observed in production,
  add ``"reasoning": {"enabled": false}`` to the payload.

Like DeepSeekClient: lazy persistent aiohttp.ClientSession, re.sub code-fence
stripping, time.monotonic() timing, and cost tracking. Raw response body
captured on the exception for diagnostics (Issue B fix 2026-05-08).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Optional

import aiohttp

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("apex")

# DeepSeek V3.2 pricing via OpenRouter (per-million tokens)
_DS_COST_PER_M_INPUT = 0.30
_DS_COST_PER_M_OUTPUT = 0.88


class APEXOptimizationError(Exception):
    """Raised when DeepSeek returns an error or the response cannot be parsed.

    Issue B fix (2026-05-08) — added ``retryable`` flag and ``raw_body``
    attribute. Pre-fix this class had no retryable concept and the
    raw response body was not captured anywhere; every transient
    upstream wobble (e.g. the 4-events-in-16-minutes cluster on
    2026-05-08 15:33–15:49) immediately became ``using_defaults=Y``
    and the operator had only ``str(e)[:120]`` to diagnose with.

    The caller (``TradeOptimizer.optimize``) wraps the call in a single
    retry attempt for ``retryable=True`` errors. Non-retryable errors
    (HTTP non-200, non-JSON body, timeout, connection error) fall
    through to Claude's original parameters immediately. APEX failure
    NEVER blocks a trade — retry is bounded and the fallback path is
    unchanged after exhaustion.

    Args:
        message: Human-readable error description.
        retryable: True for empirically-transient failure modes
            (empty choices, empty content, invalid content JSON, non-
            JSON body). False for likely-persistent failures (HTTP
            error, timeout, connection error).
        raw_body: First 1000 characters of the raw response body when
            available, for diagnostic logging by the caller. None when
            the failure occurred before the body was readable
            (timeout, connection error).
    """

    def __init__(
        self,
        message: str,
        retryable: bool = False,
        raw_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.raw_body = raw_body


class QwenClient:
    """Async OpenRouter client for APEX DeepSeek trade optimization.

    Uses a lazy persistent aiohttp.ClientSession — created on first call and
    reused thereafter. Checks the `closed` flag before each request so the
    session is transparently re-created after a service restart or explicit close.

    Args:
        api_key: OpenRouter API key (Bearer token).
        api_url: Full OpenRouter chat completions URL.
        http_referer: HTTP-Referer header value (for OpenRouter attribution).
        x_title: X-Title header value (for OpenRouter attribution).
    """

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://openrouter.ai/api/v1/chat/completions",
        http_referer: str = "https://github.com/trading-intelligence-mcp",
        x_title: str = "APEX-TradeOptimizer",
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._http_referer = http_referer
        self._x_title = x_title
        self._session: Optional[aiohttp.ClientSession] = None

        # Cumulative stats for health reporting
        self._total_calls: int = 0
        self._total_cost: float = 0.0

    def _get_session(self) -> aiohttp.ClientSession:
        """Return existing session or create a new one if closed/absent."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": self._http_referer,
                    "X-Title": self._x_title,
                }
            )
        return self._session

    async def optimize(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 800,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """POST to OpenRouter DeepSeek and return the parsed optimization result.

        Args:
            system_prompt: APEX system prompt defining DeepSeek's optimizer role.
            user_prompt: Per-trade intelligence package formatted for DeepSeek.
            model: OpenRouter model ID (e.g. "deepseek/deepseek-v3.2").
            temperature: Sampling temperature — keep low (0.2) for deterministic params.
            max_tokens: Maximum output tokens — 800 is sufficient for JSON params.
            timeout_seconds: Request timeout — 30s keeps the trade pipeline moving.

        Returns:
            Dict with keys:
                content (dict): Parsed JSON from DeepSeek (OptimizedTrade fields).
                response_time_ms (int): HTTP round-trip time.
                input_tokens (int): Prompt token count.
                output_tokens (int): Completion token count.
                cost_usd (float): Estimated cost for this call.
                model_used (str): Model ID as reported by the API.

        Raises:
            APEXOptimizationError: On any failure — HTTP error, timeout,
                parse failure, or empty response. No retries.
        """
        # Issue B fix (2026-05-08) — request JSON mode to match TIAS's
        # payload shape. TIAS's deepseek_client.py:129 has carried this
        # field since inception and has logged 0 ``no choices`` failures
        # across the same 4-day window in which APEX saw 8 unique. JSON
        # mode forces the upstream model into a parseable-JSON contract
        # and the OpenRouter gateway is more strict about empty payloads
        # under that contract. See ``issueB_phase1_synthesis.md`` for
        # the full reliability gap analysis.
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        session = self._get_session()
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        t_start = time.monotonic()

        try:
            async with session.post(
                self._api_url,
                json=payload,
                timeout=timeout,
            ) as resp:
                response_time_ms = int((time.monotonic() - t_start) * 1000)
                raw_body = await resp.text()

                if resp.status != 200:
                    # HTTP non-200 — likely persistent (auth, model-
                    # not-found, model deprecated). Not retryable.
                    raise APEXOptimizationError(
                        f"OpenRouter HTTP {resp.status} after {response_time_ms}ms: "
                        f"{raw_body[:300]}",
                        retryable=False,
                        raw_body=raw_body[:1000],
                    )

                try:
                    body = json.loads(raw_body)
                except json.JSONDecodeError as e:
                    # Non-JSON 200 body — empirically transient, likely
                    # gateway-side (similar to the 15:33–15:49 cluster).
                    # Retryable.
                    raise APEXOptimizationError(
                        f"OpenRouter returned non-JSON body: {str(e)[:100]}",
                        retryable=True,
                        raw_body=raw_body[:1000],
                    )

                choices = body.get("choices") or []
                if not choices:
                    # Empty choices on HTTP 200 — primary observed
                    # failure mode for the 2026-05-08 incident.
                    # Retryable; raw body captured for diagnosis.
                    raise APEXOptimizationError(
                        "OpenRouter response has no choices",
                        retryable=True,
                        raw_body=raw_body[:1000],
                    )
                raw_content = (choices[0].get("message") or {}).get("content", "")
                if not raw_content:
                    # ``message.content`` empty — same upstream root
                    # cause as ``no choices`` at a different pipeline
                    # stage. Retryable.
                    raise APEXOptimizationError(
                        "OpenRouter response message content is empty",
                        retryable=True,
                        raw_body=raw_body[:1000],
                    )

                content_dict = self._parse_json(
                    raw_content, model, _raw_body=raw_body[:1000],
                )

                usage = body.get("usage") or {}
                returned_model = body.get("model", model)
                in_tok = int(usage.get("prompt_tokens") or 0)
                out_tok = int(usage.get("completion_tokens") or 0)
                cost = (
                    (in_tok * _DS_COST_PER_M_INPUT / 1_000_000)
                    + (out_tok * _DS_COST_PER_M_OUTPUT / 1_000_000)
                )

                self._total_calls += 1
                self._total_cost += cost

                # Phase 12.3 (lifecycle-logging-audit Gap 3.3-G1): per-call
                # APEX_QWEN_OK at INFO with latency / token / cost fields.
                # Forensic value for OpenRouter latency attribution
                # separate from optimizer's APEX_OK rollup.
                log.info(
                    f"APEX_QWEN_OK | model={returned_model} "
                    f"latency_ms={response_time_ms} tokens_in={in_tok} "
                    f"tokens_out={out_tok} cost_usd={cost:.6f} | {ctx()}"
                )

                return {
                    "content": content_dict,
                    "response_time_ms": response_time_ms,
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "cost_usd": round(cost, 8),
                    "model_used": returned_model,
                }

        except APEXOptimizationError:
            raise
        except (aiohttp.ServerTimeoutError, asyncio.TimeoutError):
            # Timeouts are NOT retryable: optimizer.optimize already
            # special-cases timeout via its regime-fallback path
            # (optimizer.py:408–418). Body unrecoverable here.
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            raise APEXOptimizationError(
                f"DeepSeek request timed out after {elapsed_ms}ms "
                f"(limit={timeout_seconds}s)",
                retryable=False,
                raw_body=None,
            )
        except aiohttp.ClientError as e:
            # Connection errors (DNS, TLS, refused) — likely persistent
            # in the immediate window. Not retryable. Body unavailable.
            raise APEXOptimizationError(
                f"DeepSeek connection error: {str(e)[:200]}",
                retryable=False,
                raw_body=None,
            )

    def _parse_json(
        self,
        raw: str,
        model: str,
        _raw_body: str | None = None,
    ) -> dict[str, Any]:
        """Parse DeepSeek content string, stripping markdown fences if present.

        Args:
            raw: Raw content string from the API response.
            model: Model ID (used in error messages only).
            _raw_body: Outer HTTP body (already truncated to 1000 chars
                by the caller) — propagated onto the raised
                ``APEXOptimizationError`` so the operator sees the
                full diagnostic context, not just the inner content
                fragment. Defaults to None for the rare external
                callers that bypass the HTTP path.

        Returns:
            Parsed dict from the content.

        Raises:
            APEXOptimizationError: If content cannot be parsed as JSON or is not a dict.
        """
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as e:
            # Invalid content JSON — observed 3 times in the 4-day
            # audit window (BCHUSDT/OPUSDT/RENDERUSDT, content ``\`\```
            # / ``\``` / ``{``). Same upstream root cause family as
            # ``no choices``; retryable. Body propagated for diagnosis.
            raise APEXOptimizationError(
                f"DeepSeek ({model}) returned invalid JSON: {str(e)[:100]} | "
                f"content[:200]={raw[:200]}",
                retryable=True,
                raw_body=_raw_body,
            )

        if not isinstance(result, dict):
            # Non-dict valid JSON (e.g. a bare list or string) — model
            # ignored its instructions. Persistent enough that retry
            # is unlikely to help; not retryable.
            raise APEXOptimizationError(
                f"DeepSeek ({model}) returned non-dict JSON: {type(result).__name__}",
                retryable=False,
                raw_body=_raw_body,
            )
        return result

    def get_stats(self) -> dict[str, Any]:
        """Return cumulative call count and cost for health/monitoring."""
        return {
            "calls": self._total_calls,
            "cost": round(self._total_cost, 6),
        }

    async def close(self) -> None:
        """Close the underlying aiohttp session. Safe to call multiple times."""
        if self._session and not self._session.closed:
            await self._session.close()
            # Phase 12.3 (lifecycle-logging-audit Gap 3.3-G2): promoted
            # from DEBUG to INFO. Session lifecycle is noteworthy.
            log.info(f"APEX_DEEPSEEK_SESSION_CLOSED | {ctx()}")
        self._session = None
